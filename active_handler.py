"""
Active Trigger Handler — "Active ✅✅" típusú üzenetek kezelése

Logika:
  - A signal csoportban előfordul, hogy egy korábbi jelzéshez később jön egy
    "Active" üzenet (pl. "Active ✅✅"), ami azt jelzi: be kell lépni piaci áron,
    nem kell várni az eredeti entry zóna elérését.
  - Ha van nyitott (pending) limit megbízás, azt töröljük és market order-t nyitunk
    helyette ugyanazokkal a paraméterekkel (SL, TP, lot, magic).
  - Csúszás ellenőrzés: az aktuális ár nem lehet túl messze az eredeti entry-től,
    különben skip (a config-ban állítható: ACTIVE_TRIGGER_MAX_SLIPPAGE_USD).

Config kapcsolók (config.py):
  ACTIVE_TRIGGER_ENABLED          = True / False     (alap: False)
  ACTIVE_TRIGGER_MAX_SLIPPAGE_USD = 5.0              (alap: 5.0 USD)
  ACTIVE_KEYWORDS                 = ["active", "aktív", "running", "live"]
"""

import re
import logging
import MetaTrader5 as mt5

logger = logging.getLogger(__name__)


# ── Active üzenet felismerés ──────────────────────────────────────────────────

DEFAULT_ACTIVE_KEYWORDS = ["active", "aktív", "aktiv", "running", "live", "now active"]

# Ezek a szavak/tartalmak NEM számítanak active-nek (kizárás), hogy ne
# triggereljen pl. egy "BUY ACTIVE ZONE" jelzés vagy egy hosszabb signal.
EXCLUDE_PATTERNS = [
    r'\b(BUY|SELL)\b',           # ha van benne BUY/SELL → ez egy normál signal
    r'\bTP\d*\b',                # TP1, TP2, stb.
    r'\bSL\b',                   # SL
    r'\bTAKE\s*PROFIT\b',
    r'\bSTOP\s*LOSS\b',
    r'\bENTRY\b',
]


def is_active_message(text: str, keywords: list[str] | None = None) -> bool:
    """
    Eldönti, hogy az üzenet egy "active" típusú megerősítés-e.

    Egy üzenet akkor active, ha:
      1. Tartalmazza valamelyik kulcsszót (kis-nagybetű érzéketlen)
      2. NEM tartalmaz signal-specifikus elemeket (BUY/SELL/TP/SL/Entry)
      3. Rövid (< 100 karakter) — egy active üzenet általában rövid
    """
    if not text:
        return False

    text_clean = text.strip()
    if not text_clean:
        return False

    # Túl hosszú → valószínűleg nem egy egyszerű active megerősítés
    if len(text_clean) > 100:
        return False

    text_upper = text_clean.upper()

    # Kizáró minták ellenőrzése — ha ezek bármelyike van benne, nem active
    for pattern in EXCLUDE_PATTERNS:
        if re.search(pattern, text_upper):
            return False

    # Kulcsszó keresés
    kw_list = keywords if keywords else DEFAULT_ACTIVE_KEYWORDS
    text_lower = text_clean.lower()
    for kw in kw_list:
        # Szó-határ figyelembevétele, hogy ne "deactivate" stb. is matcheljen
        if re.search(r'\b' + re.escape(kw.lower()) + r'\b', text_lower):
            return True

    return False


# ── Csúszás ellenőrzés ────────────────────────────────────────────────────────

def check_slippage(deal: dict, current_price: float, max_slippage_usd: float) -> tuple[bool, str]:
    """
    Ellenőrzi, hogy az aktuális ár mennyivel ment el az eredeti entry zónától.

    A "rossz" oldali csúszást nézi:
      - BUY esetén: ha az ár az entry felett van, és magasabb mint entry_high + slippage → skip
      - SELL esetén: ha az ár az entry alatt van, és alacsonyabb mint entry_low - slippage → skip

    Args:
        deal: a tárolt pending deal (entry_low, entry_high, action, entry_price)
        current_price: az aktuális piaci ár
        max_slippage_usd: maximum megengedett csúszás USD-ben

    Returns:
        (ok, üzenet) — ok=True ha a csúszás elfogadható
    """
    action = deal.get("action")

    # Az eredeti entry zóna határai — a deal-ben tárolt entry_price az entry_mid,
    # a tényleges low/high értékek a signal-ból kellenek. Ha nincsenek, fallback
    # az entry_price-ra (csak egy pontot tudunk vizsgálni).
    entry_low  = deal.get("entry_low",  deal.get("entry_price"))
    entry_high = deal.get("entry_high", deal.get("entry_price"))

    if entry_low is None or entry_high is None:
        return True, "Csúszás nem ellenőrizhető (nincs entry zóna adat) — engedélyezve"

    if action == "BUY":
        # BUY-nál a "rossz" oldal a magasabb ár (drágábban veszünk be)
        if current_price > entry_high:
            slip = current_price - entry_high
            if slip > max_slippage_usd:
                return False, (
                    f"Csúszás túl nagy: {slip:.2f} USD a felső entry ({entry_high}) felett "
                    f"(limit: {max_slippage_usd} USD)"
                )
            return True, f"Csúszás OK: +{slip:.2f} USD a felső entry felett"
        # Az ár a zónában vagy alatta — kedvezőbb belépés mint az eredeti
        return True, f"Ár ({current_price}) a zónában vagy alatta — kedvezőbb mint az eredeti BUY zóna"

    elif action == "SELL":
        # SELL-nél a "rossz" oldal az alacsonyabb ár (olcsóbban adunk el)
        if current_price < entry_low:
            slip = entry_low - current_price
            if slip > max_slippage_usd:
                return False, (
                    f"Csúszás túl nagy: {slip:.2f} USD az alsó entry ({entry_low}) alatt "
                    f"(limit: {max_slippage_usd} USD)"
                )
            return True, f"Csúszás OK: -{slip:.2f} USD az alsó entry alatt"
        # Az ár a zónában vagy felette — kedvezőbb belépés mint az eredeti
        return True, f"Ár ({current_price}) a zónában vagy felette — kedvezőbb mint az eredeti SELL zóna"

    return False, f"Ismeretlen irány: {action}"


# ── Pending → Market konverzió ────────────────────────────────────────────────

def convert_pending_to_market(deal: dict, cfg, current_price: float) -> tuple[dict | None, str]:
    """
    Egy pending megbízást piaci megbízássá alakít:
      1. Törli a pending order-t
      2. Nyit egy market order-t ugyanazokkal a paraméterekkel
      3. Visszaadja az új deal-t

    Args:
        deal: a tárolt pending deal (ticket, action, sl, tp, magic, lot, ...)
        cfg: config modul
        current_price: aktuális piaci ár (SL/TP validációhoz, és a request price-hoz)

    Returns:
        (új_deal, hiba_üzenet) — ha sikertelen, az új_deal None
    """
    from mt5_trader import cancel_pending_order, format_mt5_error
    from datetime import datetime

    old_ticket = deal["ticket"]
    action     = deal["action"]
    lot        = deal["lot"]
    sl         = deal["sl"]
    tp         = deal["tp"]
    magic      = deal["magic"]

    # ── 1. Pending törlése ────────────────────────────────────────────────────
    if not cancel_pending_order(old_ticket):
        return None, f"Nem sikerült törölni a pending megbízást (#{old_ticket})"

    logger.info(f"🗑️ Pending törölve (#{old_ticket}) — market megbízás következik")

    # ── 2. SL/TP validáció a piaci árhoz képest ──────────────────────────────
    # Egy XAUUSD-nél a brokerek megkövetelik, hogy az SL/TP legalább
    # stops_level pontnyira legyen az árfolyamtól. Ha az "Active" jelzés
    # nagyon közel jött az SL-hez/TP-hez, lehet hogy nem fogadja el a megbízást.
    if action == "BUY":
        if sl >= current_price:
            return None, f"Érvénytelen SL BUY-nál: {sl} >= ár {current_price}"
        if tp <= current_price:
            return None, f"Érvénytelen TP BUY-nál: {tp} <= ár {current_price}"
    elif action == "SELL":
        if sl <= current_price:
            return None, f"Érvénytelen SL SELL-nál: {sl} <= ár {current_price}"
        if tp >= current_price:
            return None, f"Érvénytelen TP SELL-nál: {tp} >= ár {current_price}"

    # ── 3. Market order küldése ──────────────────────────────────────────────
    order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       cfg.SYMBOL,
        "volume":       lot,
        "type":         order_type,
        "price":        current_price,
        "sl":           sl,
        "tp":           tp,
        "deviation":    cfg.SLIPPAGE,
        "magic":        magic,
        "comment":      f"Bot_{action}_ACTIVE_m{magic}",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err = format_mt5_error(result)
        logger.error(f"Market megbízás sikertelen ACTIVE triggerre: {err}")
        return None, f"Market megbízás sikertelen: {err}"

    # ── 4. Új deal összeállítása (a pending alapján, frissített adatokkal) ───
    # A tényleges nyitási árat lekérjük az MT5-ből
    import time as _time
    _time.sleep(0.2)
    pos = mt5.positions_get(ticket=result.order)
    if pos and pos[0].price_open > 0:
        exec_price = pos[0].price_open
    elif result.price > 0:
        exec_price = result.price
    else:
        tick = mt5.symbol_info_tick(cfg.SYMBOL)
        exec_price = tick.ask if action == "BUY" else tick.bid if tick else current_price

    # signal_id átvétele a régi deal-ből (testvér pozíció kapcsolat megmarad)
    signal_id = deal.get("signal_id", "")

    new_deal = {
        "ticket":          result.order,
        "action":          action,
        "symbol":          cfg.SYMBOL,
        "lot":             lot,
        "price":           exec_price,
        "entry_price":     exec_price,
        "sl":              sl,
        "tp":              tp,
        "tp_levels":       deal.get("tp_levels", []),
        "tp_index":        deal.get("tp_index", 0),
        "start_tp_index":  deal.get("start_tp_index", deal.get("tp_index", 0)),
        "mozgo_sl_active": False,
        "magic":           magic,
        "signal_id":       signal_id,
        "is_pending":      False,
        "is_market":       True,
        "time":            datetime.now().isoformat(),
        # Eredeti entry zóna megőrzése későbbi referenciaként
        "entry_low":       deal.get("entry_low"),
        "entry_high":      deal.get("entry_high"),
        # Megjelölés, hogy ez ACTIVE triggerre nyílt
        "via_active":      True,
        "old_pending_ticket": old_ticket,
    }

    logger.info(
        f"✅ ACTIVE → Market megbízás | Ticket: {result.order} | "
        f"Magic: {magic} | Ár: {exec_price} | SL: {sl} | TP: {tp}"
    )
    return new_deal, ""
