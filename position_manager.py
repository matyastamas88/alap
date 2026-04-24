"""
Pozíció figyelő — v4 (dinamikus POS támogatás)
- Kettős trigger: testvér pozíció zárulása + ár alapú figyelés
- Önállóan is működik (pl. csak egy magic fut)
- JSON mentés adatvesztés ellen
- Tetszőleges számú pozíciót kezel (POS1..POSN), nem csak 3-at

Mozgó SL logika — dinamikusan generálódik a config alapján:
  SL_MOZGAS_ELSO_TP = 3 esetén:
    TP3 elérve → SL = Entry
    TP4 elérve → SL = TP1
    TP5 elérve → SL = TP2
    TP6 elérve → SL = TP3
    TP7 elérve → SL = TP4
  Tehát: TP_k elérve → SL = TP_(k - SL_MOZGAS_ELSO_TP), Entry ha k = SL_MOZGAS_ELSO_TP
"""

import asyncio
import logging
import json
import os
import MetaTrader5 as mt5
from datetime import datetime, timedelta
import config
from mt5_trader import modify_position, is_position_open, cancel_pending_order, is_pending_open
from notifier import send_notification
from sheets_logger import log_trade_closed, log_pending_result

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 5

_active_deals:  dict[int, dict] = {}
_pending_deals: dict[int, dict] = {}

POSITIONS_FILE = getattr(config, 'POSITIONS_FILE', 'positions.json')
_signal_groups: dict[str, list[int]] = {}


# ── JSON mentés / betöltés ────────────────────────────────────────────────────

def _save_positions():
    data = {
        "active":        {str(k): v for k, v in _active_deals.items()},
        "pending":       {str(k): v for k, v in _pending_deals.items()},
        "signal_groups": _signal_groups,
    }
    try:
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Pozíció mentés sikertelen: {e}")


def _load_positions():
    global _signal_groups
    if not os.path.exists(POSITIONS_FILE):
        return
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.get("active", {}).items():
            ticket = int(k)
            if is_position_open(ticket):
                _active_deals[ticket] = v
                logger.info(f"♻️ Pozíció visszatöltve: #{ticket} | Magic: {v.get('magic')}")
        for k, v in data.get("pending", {}).items():
            ticket = int(k)
            if is_pending_open(ticket):
                _pending_deals[ticket] = v
                logger.info(f"♻️ Pending visszatöltve: #{ticket}")
        _signal_groups = data.get("signal_groups", {})
        if _active_deals or _pending_deals:
            logger.info(f"Visszatöltve: {len(_active_deals)} aktív, {len(_pending_deals)} pending")
    except Exception as e:
        logger.error(f"Pozíció betöltés sikertelen: {e}")


# ── Dinamikus POS felismerés ──────────────────────────────────────────────────

def _get_all_pos_configs() -> list[dict]:
    """
    Visszaadja az ÖSSZES aktív POS pozíció konfigurációját a config alapján.
    Visszatér: [{magic, tp_index, label, pos_num}, ...]
    tp_index: 1-tól indexelve (a configban így van tárolva)
    """
    poziciok = []
    pozicio_szam = getattr(config, 'POZICIO_SZAM', 3)
    for i in range(1, pozicio_szam + 1):
        if not getattr(config, f'POS{i}_ENABLED', False):
            continue
        magic = getattr(config, f'POS{i}_MAGIC', None)
        if magic is None:
            continue
        tp_idx = getattr(config, f'POS{i}_TP_INDEX', i)  # 1-indexelt
        label  = getattr(config, f'POS{i}_LABEL', f'TP{tp_idx}-fix')
        poziciok.append({
            "pos_num":  i,
            "magic":    magic,
            "tp_index": tp_idx,
            "label":    label,
        })
    return poziciok


def _get_pos_by_magic(magic: int) -> dict | None:
    """Visszaadja a POS config-ot magic szerint, vagy None-t."""
    for p in _get_all_pos_configs():
        if p["magic"] == magic:
            return p
    return None


# ── Mozgó SL szabályok — DINAMIKUS ────────────────────────────────────────────

def get_sl_rules(magic: int) -> dict:
    """
    Visszaadja a mozgó SL szabályokat az adott magic numberhez.
    DINAMIKUSAN generálódik a POS tp_index és a SL_MOZGAS_ELSO_TP alapján.

    Logika: TP_k elérésekor (ahol k >= elso_tp) az SL oda ugrik, hogy
    a pozíció már nyereségben legyen védve.

      elso_tp = 3 esetén:
        TP3 elér → SL = Entry        (k - elso_tp = 0 → "entry")
        TP4 elér → SL = TP1          (k - elso_tp = 1 → tp_levels[0])
        TP5 elér → SL = TP2          (k - elso_tp = 2 → tp_levels[1])
        TP6 elér → SL = TP3          (k - elso_tp = 3 → tp_levels[2])
        TP7 elér → SL = TP4          (k - elso_tp = 4 → tp_levels[3])

    A szabály csak addig a TP-ig él, amit még NEM a pozíció saját TP-je
    (azt már a bróker automatikusan zárja).

    Kulcs a dict-ben: tp_index (0-indexelt), pl. TP3 = 2
    Érték: {"sl_source": "entry" | int, "next_watch": int}
    """
    pos = _get_pos_by_magic(magic)
    if pos is None:
        return {}

    # A pozíció saját TP-je (1-indexelt)
    pos_tp = pos["tp_index"]

    # Mikor kezdje emelni (1-indexelt, configból)
    elso_tp_1idx = getattr(config, 'SL_MOZGAS_ELSO_TP', 3)

    # Ha a pozíció TP-je kisebb mint az első mozgatandó TP → nincs mozgó SL
    # (pl. POS1 TP3-ra nyit, elso_tp=3 → TP3 már a pozíció saját cél, nincs szabály)
    if pos_tp <= elso_tp_1idx:
        return {}

    rules = {}
    # k = az a TP, aminek elérésekor trigger
    # k végigfut elso_tp_1idx-től pos_tp-1-ig (a saját TP-t nem tesszük bele)
    for k in range(elso_tp_1idx, pos_tp):
        trigger_idx_0 = k - 1                    # 0-indexelt key a dict-hez
        offset        = k - elso_tp_1idx         # 0 = Entry, 1 = TP1, stb.
        if offset == 0:
            sl_source = "entry"
        else:
            sl_source = offset - 1               # tp_levels[0] = TP1

        next_watch_0 = k                         # 0-indexelt (k+1 szint, k 1-idx → index k a 0-idx-ben)
        rules[trigger_idx_0] = {
            "sl_source":   sl_source,
            "next_watch":  next_watch_0,
        }

    return rules


def _get_new_sl(deal: dict, sl_source) -> float:
    if sl_source == "entry":
        return deal["entry_price"]
    idx = int(sl_source)
    if idx < len(deal["tp_levels"]):
        return deal["tp_levels"][idx]
    return deal["entry_price"]


# ── Magic → label ─────────────────────────────────────────────────────────────

def _magic_label(magic: int) -> str:
    csoport = getattr(config, 'BOT_NEV', '1.csoport')
    pos = _get_pos_by_magic(magic)
    if pos:
        return f"{csoport} {pos['label']}"
    return f"magic={magic}"


# ── Regisztráció ──────────────────────────────────────────────────────────────

def register_deal(deal: dict):
    ticket    = deal["ticket"]
    signal_id = deal.get("signal_id", "")

    if deal.get("is_pending"):
        _pending_deals[ticket] = deal
        logger.info(f"⏳ Pending figyelés: #{ticket} | {_magic_label(deal['magic'])}")
    else:
        _active_deals[ticket] = deal
        logger.info(f"Pozíció figyelés: #{ticket} | {_magic_label(deal['magic'])}")

    if signal_id:
        if signal_id not in _signal_groups:
            _signal_groups[signal_id] = []
        if ticket not in _signal_groups[signal_id]:
            _signal_groups[signal_id].append(ticket)

    _save_positions()


# ── History alapú detektálás ──────────────────────────────────────────────────

def _was_closed_at_tp(ticket: int) -> bool:
    """Ellenőrzi hogy a pozíció TP-n zárt-e az MT5 history alapján."""
    try:
        deals = mt5.history_deals_get(position=ticket)
        if not deals:
            return False
        close_deal = sorted(deals, key=lambda d: d.time)[-1]
        return close_deal.reason == mt5.DEAL_REASON_TP
    except Exception:
        return False


def _price_ever_reached_tp(deal: dict, tp_index: int) -> bool:
    """
    Ellenőrzi hogy az ár valaha elérte-e a TP szintet
    az MT5 tick history alapján — visszamenőleg is detektálható.
    Ez a backup trigger ha nincs testvér pozíció.
    """
    if tp_index >= len(deal["tp_levels"]):
        return False

    tp_price = deal["tp_levels"][tp_index]
    action   = deal["action"]

    ticks = mt5.copy_ticks_from(
        config.SYMBOL,
        int(datetime.fromisoformat(deal["time"]).timestamp()),
        50000,
        mt5.COPY_TICKS_ALL
    )
    if ticks is None or len(ticks) == 0:
        tick = mt5.symbol_info_tick(config.SYMBOL)
        if tick is None:
            return False
        current = tick.bid if action == "SELL" else tick.ask
        return (action == "SELL" and current <= tp_price) or \
               (action == "BUY"  and current >= tp_price)

    for tick in ticks:
        price = tick[2]
        if action == "SELL" and price <= tp_price:
            return True
        if action == "BUY" and price >= tp_price:
            return True
    return False


def _get_sister_deals(ticket: int) -> list[dict]:
    """Visszaadja az összetartozó pozíciókat."""
    for signal_id, tickets in _signal_groups.items():
        if ticket in tickets:
            return [_active_deals[t] for t in tickets if t in _active_deals and t != ticket]
    return []


# ── SL mozgatás végrehajtása ──────────────────────────────────────────────────

async def _apply_sl_move(deal: dict, triggered_tp_index: int):
    """
    SL mozgatást hajt végre a triggered_tp_index alapján.
    triggered_tp_index: melyik TP szint lett elérve (0-indexelt, pl. 2=TP3, 3=TP4)
    """
    ticket = deal["ticket"]
    label  = _magic_label(deal["magic"])

    if not is_position_open(ticket):
        return

    rules = get_sl_rules(deal["magic"])
    if triggered_tp_index not in rules:
        logger.debug(f"Nincs SL szabály TP{triggered_tp_index+1}-re (magic={deal['magic']})")
        return

    rule       = rules[triggered_tp_index]
    new_sl     = _get_new_sl(deal, rule["sl_source"])
    next_watch = rule["next_watch"]
    sl_source  = rule["sl_source"]
    sl_label   = "Entry" if sl_source == "entry" else f"TP{int(sl_source)+1}"
    tp_name    = f"TP{triggered_tp_index+1}"

    new_tp = deal["tp"]

    ok = modify_position(ticket, new_sl, new_tp, config.SYMBOL)
    if ok:
        deal["sl"]                = new_sl
        deal["next_watch_tp"]     = next_watch
        deal["mozgo_sl_active"]   = True
        deal["last_triggered_tp"] = triggered_tp_index
        _active_deals[ticket]     = deal
        _save_positions()
        logger.info(f"✅ SL mozgatva #{ticket}: {sl_label} ({new_sl}) | Következő figyelés: TP{next_watch+1}")
        await send_notification(
            f"📍 <b>{tp_name} elérve!</b>\n"
            f"Forrás: <b>{label}</b>\n"
            f"Ticket: #{ticket}\n"
            f"SL átmozgatva: <b>{new_sl}</b> ({sl_label})\n"
            f"Pozíció TP célja: <b>{new_tp}</b>"
        )
    else:
        logger.error(f"SL mozgatás sikertelen #{ticket}")


# ── Pending ellenőrzés ────────────────────────────────────────────────────────

async def _check_pending(ticket: int, deal: dict):
    label = _magic_label(deal["magic"])

    if not is_pending_open(ticket):
        if is_position_open(ticket):
            logger.info(f"✅ Pending teljesült: #{ticket} ({label})")
            deal["is_pending"] = False
            _active_deals[ticket] = deal
            try:
                log_pending_result(ticket, "Teljesült")
            except Exception:
                pass
            await send_notification(
                f"✅ <b>Limit megbízás teljesült!</b>\n"
                f"Forrás: <b>{label}</b>\n"
                f"Ticket: #{ticket} | Magic: {deal.get('magic', '?')}\n"
                f"Ár: <b>{deal['price']}</b>\n"
                f"SL: {deal['sl']} | TP: {deal['tp']}"
            )
        else:
            await send_notification(
                f"ℹ️ <b>Pending megszűnt</b>\n"
                f"Forrás: <b>{label}</b>\n"
                f"Ticket: #{ticket}"
            )
        del _pending_deals[ticket]
        _save_positions()
        return

    if config.PENDING_TIMEOUT_MINUTES <= 0:
        return

    created_at = datetime.fromisoformat(deal["time"])
    if datetime.now() - created_at >= timedelta(minutes=config.PENDING_TIMEOUT_MINUTES):
        cancelled = cancel_pending_order(ticket)
        del _pending_deals[ticket]
        _save_positions()
        if cancelled:
            try:
                log_pending_result(ticket, "Timeout", f"{config.PENDING_TIMEOUT_MINUTES} perc eltelt")
            except Exception:
                pass
            await send_notification(
                f"⏰ <b>Pending törölve — időtúllépés</b>\n"
                f"Forrás: <b>{label}</b>\n"
                f"Ticket: #{ticket} | {config.PENDING_TIMEOUT_MINUTES} perc eltelt."
            )


# ── Aktív pozíció ellenőrzés ──────────────────────────────────────────────────

async def _check_deal(ticket: int, deal: dict):
    label = _magic_label(deal["magic"])
    magic = deal["magic"]

    # ── Pozíció lezárult? ────────────────────────────────────────────────────
    if not is_position_open(ticket):
        logger.info(f"Pozíció zárul: #{ticket} ({label})")

        # ── Zárási adatok lekérése MT5 history-ból ───────────────────────────
        zaras_ar = 0.0
        eredmeny_usd = 0.0
        zaras_status = "Manuális"
        try:
            deals_h = mt5.history_deals_get(position=ticket)
            if deals_h:
                close_deal = sorted(deals_h, key=lambda d: d.time)[-1]
                zaras_ar = close_deal.price
                eredmeny_usd = close_deal.profit
                if close_deal.reason == mt5.DEAL_REASON_TP:
                    zaras_status = "TP"
                elif close_deal.reason == mt5.DEAL_REASON_SL:
                    zaras_status = "SL"
                else:
                    zaras_status = "Manuális"
        except Exception as e:
            logger.warning(f"Zárási adatok lekérése sikertelen: {e}")

        # Időtartam számítása
        try:
            nyitas = datetime.fromisoformat(deal.get("time", datetime.now().isoformat()))
            idotartam = (datetime.now() - nyitas).total_seconds() / 60
        except Exception:
            idotartam = 0.0

        # ── DINAMIKUS TESTVÉR SL TRIGGER ─────────────────────────────────────
        # Ha ez a pozíció TP-n zárt, az azt jelenti: elérte a saját tp_index-ét.
        # Minden testvér pozíció, aminek a tp_index-e >= ennél, SL mozgatást kaphat.
        if config.MOZGO_SL_ENABLED and zaras_status == "TP":
            closed_pos_cfg = _get_pos_by_magic(magic)
            if closed_pos_cfg:
                closed_tp_1idx = closed_pos_cfg["tp_index"]          # 1-indexelt
                closed_tp_0idx = closed_tp_1idx - 1                   # 0-indexelt trigger
                elso_tp_1idx   = getattr(config, 'SL_MOZGAS_ELSO_TP', 3)

                # Csak akkor trigger, ha a zárt TP szint >= az első mozgatandó TP
                if closed_tp_1idx >= elso_tp_1idx:
                    logger.info(
                        f"📍 {label} TP{closed_tp_1idx}-n zárt → testvér SL mozgatás "
                        f"(trigger: TP{closed_tp_1idx})"
                    )
                    for sister in _get_sister_deals(ticket):
                        last = sister.get("last_triggered_tp", -1)
                        # Csak akkor mozgassunk, ha ez újabb trigger
                        if last < closed_tp_0idx:
                            # Ellenőrizzük: van-e a testvérnek szabálya erre a TP-re
                            sister_rules = get_sl_rules(sister["magic"])
                            if closed_tp_0idx in sister_rules:
                                await _apply_sl_move(sister, triggered_tp_index=closed_tp_0idx)
            else:
                logger.warning(f"Ismeretlen magic a zárt pozíciónál: {magic}")
        elif config.MOZGO_SL_ENABLED:
            logger.info(f"{label} SL-en/manuálisan zárt — mozgó SL nem triggerel")

        # ── Sheets naplózás ───────────────────────────────────────────────────
        try:
            log_trade_closed(ticket, zaras_ar, eredmeny_usd, idotartam, zaras_status)
        except Exception as e:
            logger.warning(f"Sheets zárás naplózás hiba: {e}")

        eredmeny_jel = "+" if eredmeny_usd >= 0 else ""
        await send_notification(
            f"🏁 <b>Pozíció lezárult — {zaras_status}</b>\n"
            f"Forrás: <b>{label}</b>\n"
            f"Ticket: #{ticket} | Magic: <code>{magic}</code>\n"
            f"Zárási ár: {zaras_ar} | Eredmény: {eredmeny_jel}{eredmeny_usd:.2f} USD\n"
            f"Időtartam: {idotartam:.0f} perc"
        )
        del _active_deals[ticket]
        _save_positions()
        return

    # Fix SL verzió — nincs teendő
    if not config.MOZGO_SL_ENABLED:
        return

    # ── Ár alapú figyelés (backup + önálló működés) ──────────────────────────
    rules = get_sl_rules(magic)
    if not rules:
        return  # Ennek a pozíciónak nincs mozgó SL szabálya (pl. POS1 TP3-ra)

    # Meghatározzuk melyik TP szintet kell most figyelni
    last_triggered = deal.get("last_triggered_tp", -1)

    # A legkisebb trigger index a szabályokból, ami még nem volt triggerelve
    pending_triggers = sorted([k for k in rules.keys() if k > last_triggered])
    if not pending_triggers:
        return  # Már minden szabály triggerelve

    next_watch = pending_triggers[0]  # a legközelebbi, még nem triggerelt TP

    # Ellenőrizzük az árat
    tick = mt5.symbol_info_tick(config.SYMBOL)
    if tick is None:
        return

    current_price = tick.bid if deal["action"] == "SELL" else tick.ask

    if next_watch >= len(deal["tp_levels"]):
        return

    watch_tp_price = deal["tp_levels"][next_watch]

    tp_reached = (
        (deal["action"] == "SELL" and current_price <= watch_tp_price) or
        (deal["action"] == "BUY"  and current_price >= watch_tp_price)
    )

    if tp_reached:
        logger.info(f"📍 TP{next_watch+1} ár alapú trigger: #{ticket} (magic={magic})")
        await _apply_sl_move(deal, triggered_tp_index=next_watch)


# ── Fő loop ───────────────────────────────────────────────────────────────────

async def run_monitor():
    mozgo = "MOZGÓ SL" if config.MOZGO_SL_ENABLED else "FIX SL"
    logger.info(f"Pozíció figyelő indult | Verzió: {mozgo} | Pending timeout: {config.PENDING_TIMEOUT_MINUTES} perc")

    # Indításkor debug: kiírjuk az összes POS szabályát
    if config.MOZGO_SL_ENABLED:
        for p in _get_all_pos_configs():
            rules = get_sl_rules(p["magic"])
            if rules:
                parts = []
                for k, r in sorted(rules.items()):
                    if r["sl_source"] == "entry":
                        sl_txt = "Entry"
                    else:
                        sl_txt = f"TP{int(r['sl_source']) + 1}"
                    parts.append(f"TP{k + 1}→{sl_txt}")
                rule_desc = ", ".join(parts)
                logger.info(f"   SL szabály {p['label']} (magic={p['magic']}): {rule_desc}")
            else:
                logger.info(f"   SL szabály {p['label']} (magic={p['magic']}): nincs (saját TP zárja)")

    _load_positions()

    while True:
        for ticket, deal in list(_pending_deals.items()):
            try:
                await _check_pending(ticket, deal)
            except Exception as e:
                logger.error(f"Pending hiba #{ticket}: {e}")

        for ticket, deal in list(_active_deals.items()):
            try:
                await _check_deal(ticket, deal)
            except Exception as e:
                logger.error(f"Pozíció hiba #{ticket}: {e}")

        await asyncio.sleep(CHECK_INTERVAL)
