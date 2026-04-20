"""
MT5 kereskedési modul — általános verzió
Mindkét csoport botja használja, config paraméterekkel.
"""

import logging
import csv
import os
import subprocess
import time
from datetime import datetime
import MetaTrader5 as mt5

logger = logging.getLogger(__name__)

SPREAD_LOG_FILE   = "spread_log.csv"
MAX_TP_DISTANCE_USD = 50.0  # Maximum megengedett TP távolság USD-ben
MIN_TP_DISTANCE_USD = 0.05  # Minimum megengedett TP távolság USD-ben
MT5_TERMINAL_PATH = None  # config.py-ból töltődik be
MT5_RESTART_WAIT  = 15

_notifier_send = None


def set_notifier(send_fn):
    global _notifier_send
    _notifier_send = send_fn


def _notify_sync(msg: str):
    if _notifier_send:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_notifier_send(msg))
            else:
                loop.run_until_complete(_notifier_send(msg))
        except Exception as e:
            logger.error(f"Telegram értesítés sikertelen: {e}")


# ── MT5 hibakódok ─────────────────────────────────────────────────────────────

RETCODE_MESSAGES = {
    10004: "Újrakuotálás — az ár megváltozott",
    10006: "Kérés elutasítva a broker által",
    10013: "Érvénytelen kérés (hibás paraméterek)",
    10014: "Érvénytelen lot méret",
    10015: "Érvénytelen ár",
    10016: "Érvénytelen SL vagy TP szint",
    10017: "Kereskedés le van tiltva — engedélyezd az algo kereskedést!",
    10018: "Piac zárva",
    10019: "Nincs elegendő fedezet (margin)",
    10026: "Automatikus kereskedés tiltva a szerveren",
    10027: "Automatikus kereskedés tiltva a kliensen",
    10031: "Nincs kapcsolat a szerverrel",
    10039: "Már van nyitott pozíció erre a szimbólumra",
}


def get_retcode_description(retcode: int) -> str:
    return RETCODE_MESSAGES.get(retcode, f"Ismeretlen hibakód: {retcode}")


def format_mt5_error(result) -> str:
    if result is None:
        return f"MT5 nem válaszolt (last_error: {mt5.last_error()})"
    retcode  = result.retcode
    desc     = get_retcode_description(retcode)
    comment  = result.comment or ""
    msg      = f"[{retcode}] {desc}"
    if comment and comment.lower() not in desc.lower():
        msg += f" | Broker: {comment}"
    return msg


# ── MT5 újraindítás ───────────────────────────────────────────────────────────

def restart_mt5(cfg=None) -> bool:
    path = MT5_TERMINAL_PATH
    if cfg is not None:
        path = getattr(cfg, 'MT5_TERMINAL_PATH', MT5_TERMINAL_PATH)
    if path is None or not os.path.exists(path):
        logger.error(f"MT5 nem található: {MT5_TERMINAL_PATH}")
        _notify_sync("❌ <b>MT5 nem található!</b>\nEllenőrizd az elérési útvonalat.")
        return False

    logger.warning("MT5 újraindítása folyamatban...")
    _notify_sync("⚠️ <b>MT5 kapcsolat megszakadt!</b>\nAutomatikus újraindítás folyamatban...")

    try:
        subprocess.run(["taskkill", "/F", "/IM", "terminal64.exe"], capture_output=True)
        time.sleep(3)
        subprocess.Popen([path])
        logger.info(f"MT5 elindítva, várakozás {MT5_RESTART_WAIT}s...")
        time.sleep(MT5_RESTART_WAIT)
        return True
    except Exception as e:
        logger.error(f"MT5 újraindítás sikertelen: {e}")
        _notify_sync(f"❌ <b>MT5 újraindítás sikertelen!</b>\n{e}")
        return False



# ── Automata lot számítás ─────────────────────────────────────────────────────

def calculate_lot(cfg, risk_pct: float, sl_price: float, entry_price: float) -> float:
    """
    Kiszámolja az optimális lot méretet a kockázat % alapján.
    
    Képlet: lot = (egyenleg × kockázat%) / (SL távolság USD-ben × 100)
    Az XAUUSD-nél 1 lot = 100 oz, 1 pont = 1 USD mozgás 1 lotnál.
    """
    try:
        info = mt5.account_info()
        if info is None:
            logger.warning("Nem sikerült lekérni az egyenleget — alapértelmezett lot használva")
            return 0.01

        balance    = info.balance
        risk_usd   = balance * (risk_pct / 100.0)
        sl_dist    = abs(entry_price - sl_price)

        if sl_dist == 0:
            logger.warning("SL távolság 0 — alapértelmezett lot használva")
            return 0.01

        # XAUUSD: 1 lot mozgása 1 pontban = 1 USD
        # sl_dist pontban = sl_dist (mivel az ár USD/oz)
        lot = risk_usd / (sl_dist * 1.0)

        # Kerekítés 2 tizedesre, min 0.01
        sym_info = mt5.symbol_info(cfg.SYMBOL)
        if sym_info:
            step = sym_info.volume_step
            lot  = round(round(lot / step) * step, 2)

        lot = max(0.01, min(lot, 100.0))
        logger.info(f"Automata lot: {lot} | Egyenleg: {balance} | Kockázat: {risk_pct}% ({risk_usd:.2f} USD) | SL táv: {sl_dist}")
        return lot

    except Exception as e:
        logger.error(f"Lot számítás hiba: {e} — 0.01 lot használva")
        return 0.01


# ── Napi veszteség ellenőrzés ─────────────────────────────────────────────────

_daily_start_balance = None
_daily_start_date    = None

def check_daily_loss_limit(cfg) -> bool:
    """
    Ellenőrzi hogy elérte-e a napi veszteség limitet.
    True = limit elérve (bot leáll), False = minden rendben.
    """
    global _daily_start_balance, _daily_start_date

    limit_pct = getattr(cfg, 'DAILY_LOSS_LIMIT_PCT', 0.0)
    if not limit_pct or limit_pct <= 0:
        return False

    try:
        info = mt5.account_info()
        if info is None:
            return False

        today = __import__('datetime').date.today()

        # Nap elején menti az egyenleget
        if _daily_start_date != today:
            _daily_start_balance = info.balance
            _daily_start_date    = today
            logger.info(f"Napi egyenleg rögzítve: {_daily_start_balance} USD")
            return False

        # Veszteség számítás
        loss     = _daily_start_balance - info.balance
        loss_pct = (loss / _daily_start_balance) * 100 if _daily_start_balance > 0 else 0

        if loss_pct >= limit_pct:
            logger.warning(f"⛔ Napi limit elérve! Veszteség: {loss_pct:.1f}% (limit: {limit_pct}%)")
            return True

        return False

    except Exception as e:
        logger.error(f"Napi limit ellenőrzés hiba: {e}")
        return False

# ── Kapcsolat ─────────────────────────────────────────────────────────────────

def connect(cfg, after_restart: bool = False) -> bool:
    if not mt5.initialize():
        err = mt5.last_error()
        logger.error(f"MT5 inicializálás sikertelen: {err}")
        if not after_restart:
            if restart_mt5():
                result = connect(cfg, after_restart=True)
                _notify_sync("✅ <b>MT5 újraindult!</b>" if result else "❌ <b>MT5 újraindítás után sem csatlakozott!</b>")
                return result
        return False

    authorized = mt5.login(cfg.MT5_LOGIN, password=cfg.MT5_PASSWORD, server=cfg.MT5_SERVER)
    if not authorized:
        err = mt5.last_error()
        logger.error(f"MT5 bejelentkezés sikertelen: {err}")
        mt5.shutdown()
        if not after_restart:
            if restart_mt5():
                result = connect(cfg, after_restart=True)
                _notify_sync("✅ <b>MT5 újraindult!</b>" if result else "❌ <b>MT5 újraindítás után sem csatlakozott!</b>")
                return result
        return False

    info = mt5.account_info()
    logger.info(f"MT5 csatlakozva | Számla: {info.login} | Egyenleg: {info.balance} {info.currency}")
    return True



def close_all_positions(cfg, label: str = "") -> tuple[int, int]:
    """
    Lezárja az összes bot által nyitott pozíciót (magic number alapján).
    Visszatér: (sikeresen_zárt, sikertelen) tuple
    """
    magics = set()
    # config-ból összegyűjti a magic numbereket
    for attr in ['POS1_MAGIC', 'POS2_MAGIC', 'POS3_MAGIC', 'MAGIC', 'MAGIC_NUMBER']:
        val = getattr(cfg, attr, None)
        if val is not None:
            magics.add(val)

    positions = mt5.positions_get(symbol=cfg.SYMBOL)
    if not positions:
        logger.info(f"Close parancs: nincs nyitott pozíció ({cfg.SYMBOL})")
        return 0, 0

    # Csak a bot magic numberéhez tartozó pozíciókat zárja
    bot_positions = [p for p in positions if p.magic in magics]
    if not bot_positions:
        logger.info(f"Close parancs: nincs bot pozíció (magics: {magics})")
        return 0, 0

    sikeres = 0
    sikertelen = 0

    for pos in bot_positions:
        # Zárás piaci áron (ellentétes irányú megbízással)
        order_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(cfg.SYMBOL)
        if tick is None:
            logger.error(f"Nem sikerült lekérni az árat záráshoz: #{pos.ticket}")
            sikertelen += 1
            continue

        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       cfg.SYMBOL,
            "volume":       pos.volume,
            "type":         order_type,
            "position":     pos.ticket,
            "price":        price,
            "deviation":    cfg.SLIPPAGE,
            "magic":        pos.magic,
            "comment":      f"Close_{label}",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"✅ Pozíció lezárva: #{pos.ticket} | Magic: {pos.magic}")
            sikeres += 1
        else:
            err = format_mt5_error(result)
            logger.error(f"❌ Zárás sikertelen: #{pos.ticket} | {err}")
            sikertelen += 1

    return sikeres, sikertelen

def disconnect():
    mt5.shutdown()
    logger.info("MT5 kapcsolat lezárva.")


# ── Spread ────────────────────────────────────────────────────────────────────

def _log_spread_to_csv(spread_points, spread_usd, signal_action, eredmeny, max_spread, log_file):
    file_exists = os.path.isfile(log_file)
    try:
        with open(log_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["datum", "ido", "irany", "spread_pont", "spread_usd", "max_limit", "eredmeny"])
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d"),
                datetime.now().strftime("%H:%M:%S"),
                signal_action,
                spread_points,
                f"{spread_usd:.2f}",
                max_spread,
                eredmeny,
            ])
    except Exception as e:
        logger.error(f"Spread CSV naplózás sikertelen: {e}")


def check_spread(cfg, signal_action: str = "N/A") -> bool | str:
    info = mt5.symbol_info(cfg.SYMBOL)
    if info is None:
        logger.warning("symbol_info None — MT5 újracsatlakozás...")
        mt5.shutdown()
        if connect(cfg):
            info = mt5.symbol_info(cfg.SYMBOL)
        if info is None:
            _notify_sync("⚠️ <b>MT5 kapcsolat hiba!</b>\nNem sikerült lekérni a symbol infót.")
            return "Nem sikerült lekérni a symbol infót"

    spread_points = info.spread
    spread_usd    = spread_points * 0.01

    spread_log = getattr(cfg, 'SPREAD_LOG_FILE', 'spread_log.csv')

    if spread_points > cfg.MAX_SPREAD:
        logger.warning(f"Spread BLOKKOLVA | {spread_points} pont ({spread_usd:.2f} USD)")
        _log_spread_to_csv(spread_points, spread_usd, signal_action, "BLOKKOLVA", cfg.MAX_SPREAD, spread_log)
        return f"Spread túl magas: {spread_points} pont ({spread_usd:.2f} USD) — limit: {cfg.MAX_SPREAD} pont"
    else:
        logger.info(f"Spread OK | {spread_points} pont ({spread_usd:.2f} USD)")
        _log_spread_to_csv(spread_points, spread_usd, signal_action, "OK", cfg.MAX_SPREAD, spread_log)
        return True


# ── Ár lekérés ────────────────────────────────────────────────────────────────

def get_current_price(symbol: str, action: str) -> float | None:
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"Nem sikerült lekérni az árat: {symbol}")
        return None
    return tick.ask if action == "BUY" else tick.bid


def _is_in_entry_zone(current_price, signal) -> bool:
    return signal.entry_low <= current_price <= signal.entry_high


# ── Megbízás küldése ──────────────────────────────────────────────────────────

def place_order(signal, cfg, lot_size: float, magic: int, tp_index: int = 2) -> tuple[dict | None, str]:
    """
    Pozíció nyitása.
    tp_index: melyik TP szintre célozzunk (0=TP1, 2=TP3, stb.)
    Visszatér: (deal, hiba_szöveg)
    """
    spread_ok = check_spread(cfg, signal_action=signal.action)
    if spread_ok is not True:
        return None, spread_ok

    # Napi veszteség limit ellenőrzés
    if check_daily_loss_limit(cfg):
        return None, f"Napi veszteség limit elérve ({getattr(cfg, 'DAILY_LOSS_LIMIT_PCT', 0)}%) — kereskedés kihagyva"

    # Időablak ellenőrzés
    if getattr(cfg, 'TRADE_HOURS_ENABLED', False):
        now_hour = __import__('datetime').datetime.now().hour
        start    = getattr(cfg, 'TRADE_HOUR_START', 0)
        end      = getattr(cfg, 'TRADE_HOUR_END', 24)
        if not (start <= now_hour < end):
            return None, f"Kereskedési időablakon kívül ({now_hour}:00, ablak: {start}:00-{end}:00)"

    # Automata lot számítás ha be van kapcsolva
    if getattr(cfg, 'AUTO_LOT', False):
        risk_map = {
            getattr(cfg, 'POS1_MAGIC', None): getattr(cfg, 'POS1_RISK_PCT', 1.0),
            getattr(cfg, 'POS2_MAGIC', None): getattr(cfg, 'POS2_RISK_PCT', 1.0),
            getattr(cfg, 'POS3_MAGIC', None): getattr(cfg, 'POS3_RISK_PCT', 1.0),
        }
        risk_pct = risk_map.get(magic, 1.0)
        lot_size = calculate_lot(cfg, risk_pct, signal.sl, signal.entry_mid)
        logger.info(f"Automata lot: {lot_size} (magic={magic}, kockázat={risk_pct}%)")

    if len(signal.tp_levels) <= tp_index:
        err = f"Nincs elég TP szint (kell: {tp_index+1}, van: {len(signal.tp_levels)})"
        logger.error(err)
        return None, err

    current_price = get_current_price(cfg.SYMBOL, signal.action)
    if current_price is None:
        return None, f"Nem sikerült lekérni az aktuális árat ({cfg.SYMBOL})"

    in_zone   = _is_in_entry_zone(current_price, signal)
    entry_mid = signal.entry_mid

    # ── TP validáció: SELL→TP az ár alatt, BUY→TP az ár felett ──────────────
    ref_price = entry_mid  # limit megbízásnál az entry mid a belépési ár
    if in_zone:
        ref_price = current_price

    valid_tp_index = None
    for i in range(tp_index, len(signal.tp_levels)):
        tp_candidate = signal.tp_levels[i]
        if signal.action == "SELL" and tp_candidate < ref_price:
            valid_tp_index = i
            break
        elif signal.action == "BUY" and tp_candidate > ref_price:
            valid_tp_index = i
            break

    if valid_tp_index is None:
        err = f"Nem található érvényes TP szint (TP{tp_index+1}-től vizsgálva, {signal.action} @ {ref_price})"
        logger.error(err)
        return None, err

    if valid_tp_index != tp_index:
        logger.warning(
            f"TP{tp_index+1} ({signal.tp_levels[tp_index]}) érvénytelen {signal.action}-nál "
            f"→ következő érvényes: TP{valid_tp_index+1} ({signal.tp_levels[valid_tp_index]})"
        )
        tp_index = valid_tp_index

    tp_price = signal.tp_levels[tp_index]

    # ── TP távolság validáció ─────────────────────────────────────────────────
    tp_distance_usd = abs(tp_price - ref_price) * 0.01
    if tp_distance_usd > MAX_TP_DISTANCE_USD:
        err = (
            f"TP{tp_index+1} ({tp_price}) távolsága {tp_distance_usd:.1f} USD — "
            f"túl messze van a belépési ártól (max: {MAX_TP_DISTANCE_USD} USD). "
            f"Valószínű elírás a jelzésben, kereskedés kihagyva."
        )
        logger.error(err)
        return None, err
    if tp_distance_usd < MIN_TP_DISTANCE_USD:
        err = (
            f"TP{tp_index+1} ({tp_price}) távolsága {tp_distance_usd:.2f} USD — "
            f"túl közel van a belépési árhoz (min: {MIN_TP_DISTANCE_USD} USD). "
            f"Kereskedés kihagyva."
        )
        logger.error(err)
        return None, err

    if in_zone:
        order_type = mt5.ORDER_TYPE_BUY if signal.action == "BUY" else mt5.ORDER_TYPE_SELL
        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       cfg.SYMBOL,
            "volume":       lot_size,
            "type":         order_type,
            "price":        current_price,
            "sl":           signal.sl,
            "tp":           tp_price,
            "deviation":    cfg.SLIPPAGE,
            "magic":        magic,
            "comment":      f"Bot_{signal.action}_m{magic}",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
    else:
        order_type = mt5.ORDER_TYPE_BUY_LIMIT if signal.action == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
        request = {
            "action":       mt5.TRADE_ACTION_PENDING,
            "symbol":       cfg.SYMBOL,
            "volume":       lot_size,
            "type":         order_type,
            "price":        entry_mid,
            "sl":           signal.sl,
            "tp":           tp_price,
            "deviation":    cfg.SLIPPAGE,
            "magic":        magic,
            "comment":      f"Bot_{signal.action}_m{magic}",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err = format_mt5_error(result)
        logger.error(f"Megbízás sikertelen (magic={magic}): {err}")
        return None, err

    # Piaci megbízásnál a result.price néha 0.0 (broker specifikus hiba)
    # Ezért a tényleges nyitási árat az MT5 pozíció adataiból kérjük le
    if in_zone:
        import time as _time
        _time.sleep(0.2)  # kis várakozás hogy az MT5 regisztrálja
        pos = mt5.positions_get(ticket=result.order)
        if pos and pos[0].price_open > 0:
            exec_price = pos[0].price_open
        elif result.price > 0:
            exec_price = result.price
        else:
            # Fallback: aktuális ár lekérése
            tick = mt5.symbol_info_tick(cfg.SYMBOL)
            exec_price = tick.ask if signal.action == "BUY" else tick.bid if tick else entry_mid
    else:
        exec_price = entry_mid
    # signal_id: azonosítja az összetartozó pozíciókat (mozgó SL trigger)
    signal_id = f"{signal.action}_{signal.entry_mid}_{datetime.now().strftime('%Y%m%d_%H%M')}"

    deal = {
        "ticket":         result.order,
        "action":         signal.action,
        "symbol":         cfg.SYMBOL,
        "lot":            lot_size,
        "price":          exec_price,
        "entry_price":    exec_price,
        "sl":             signal.sl,
        "tp":             tp_price,
        "tp_levels":      signal.tp_levels,
        "tp_index":       tp_index,
        "start_tp_index": tp_index,   # az első érvényes TP index
        "mozgo_sl_active": False,     # True ha mozgó SL már aktiválódott
        "magic":          magic,
        "signal_id":      signal_id,  # összetartozó pozíciók azonosítója
        "is_pending":     not in_zone,
        "is_market":      in_zone,     # True = azonnali piaci belépés
        "time":           datetime.now().isoformat(),
    }

    logger.info(
        f"{'✅ Pozíció' if in_zone else '⏳ Limit'} | "
        f"Ticket: {result.order} | Magic: {magic} | "
        f"Ár: {exec_price} | SL: {signal.sl} | TP{tp_index+1}: {tp_price}"
    )
    return deal, ""


def modify_position(ticket: int, new_sl: float, new_tp: float, symbol: str) -> bool:
    request = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "symbol":   symbol,
        "position": ticket,
        "sl":       new_sl,
        "tp":       new_tp,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err = format_mt5_error(result)
        logger.error(f"SL/TP módosítás sikertelen (#{ticket}): {err}")
        return False
    logger.info(f"🔧 Módosítva | #{ticket} | SL: {new_sl} | TP: {new_tp}")
    return True


def is_position_open(ticket: int) -> bool:
    return bool(mt5.positions_get(ticket=ticket))


def cancel_pending_order(ticket: int) -> bool:
    result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        err = format_mt5_error(result)
        logger.error(f"Pending törlés sikertelen (#{ticket}): {err}")
        return False
    logger.info(f"🗑️ Pending törölve: #{ticket}")
    return True


def is_pending_open(ticket: int) -> bool:
    return bool(mt5.orders_get(ticket=ticket))
