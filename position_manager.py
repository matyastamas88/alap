"""
Pozíció figyelő — v3
- Kettős trigger: testvér pozíció zárulása + ár alapú figyelés
- Önállóan is működik (pl. csak magic=33 fut)
- JSON mentés adatvesztés ellen

Mozgó SL logika (magic=32 és 33):
  TP3 elérve → SL = Entry
  TP4 elérve → SL = TP1
  TP5 elérve → SL = TP2  (csak magic=33)
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


# ── Mozgó SL szabályok ────────────────────────────────────────────────────────

# tp_trigger_index → (sl_forrás, következő_figyelendő_tp_index)
# sl_forrás: "entry" vagy int (tp_levels index)
# Ez az általános táblázat — a konkrét pozíciónál a start_tp_index alapján épül fel

def get_sl_rules(magic: int) -> dict:
    """
    Visszaadja a mozgó SL szabályokat az adott magic numberhez.
    A szabályok: melyik TP elérése után mi legyen az SL.

    Magic=POS2 (TP5-re nyit):
      TP3 (index 2) → SL = Entry,  következő figyelés: TP4
      TP4 (index 3) → SL = TP1,    következő figyelés: TP5 (zárul)

    Magic=POS3 (TP6-ra nyit):
      TP3 (index 2) → SL = Entry,  következő figyelés: TP4
      TP4 (index 3) → SL = TP1,    következő figyelés: TP5
      TP5 (index 4) → SL = TP2,    következő figyelés: TP6 (zárul)
    """
    pos2_magic = getattr(config, 'POS2_MAGIC', None)
    pos3_magic = getattr(config, 'POS3_MAGIC', None)

    if magic == pos2_magic:
        return {
            2: {"sl_source": "entry", "next_watch": 3},  # TP3→SL=Entry
            3: {"sl_source": 0,       "next_watch": 4},  # TP4→SL=TP1
        }
    elif magic == pos3_magic:
        return {
            2: {"sl_source": "entry", "next_watch": 3},  # TP3→SL=Entry
            3: {"sl_source": 0,       "next_watch": 4},  # TP4→SL=TP1
            4: {"sl_source": 1,       "next_watch": 5},  # TP5→SL=TP2
        }
    return {}


def _get_new_sl(deal: dict, sl_source) -> float:
    if sl_source == "entry":
        return deal["entry_price"]
    idx = int(sl_source)
    if idx < len(deal["tp_levels"]):
        return deal["tp_levels"][idx]
    return deal["entry_price"]


# ── Magic → label ─────────────────────────────────────────────────────────────

def _magic_label(magic: int) -> str:
    # BOT_NEV a .env fájlból jön — dinamikusan azonosítja a botot
    csoport = getattr(config, 'BOT_NEV', '1.csoport')
    for attr, label_attr in [('POS1_MAGIC','POS1_LABEL'),('POS2_MAGIC','POS2_LABEL'),('POS3_MAGIC','POS3_LABEL')]:
        if getattr(config, attr, None) == magic:
            return f"{csoport} {getattr(config, label_attr, attr)}"
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

    # Lekérjük a legutóbbi tick-eket
    ticks = mt5.copy_ticks_from(
        config.SYMBOL,
        int(datetime.fromisoformat(deal["time"]).timestamp()),
        50000,
        mt5.COPY_TICKS_ALL
    )
    if ticks is None or len(ticks) == 0:
        # Ha nem érhető el history, ár alapú ellenőrzés
        tick = mt5.symbol_info_tick(config.SYMBOL)
        if tick is None:
            return False
        current = tick.bid if action == "SELL" else tick.ask
        return (action == "SELL" and current <= tp_price) or \
               (action == "BUY"  and current >= tp_price)

    # Végigmegyünk a tickeken
    for tick in ticks:
        price = tick[2]  # bid
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
    triggered_tp_index: melyik TP szint lett elérve (pl. 2=TP3, 3=TP4, 4=TP5)
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

    # TP marad az eredeti (a pozíció saját TP-je zárja)
    new_tp = deal["tp"]

    ok = modify_position(ticket, new_sl, new_tp, config.SYMBOL)
    if ok:
        deal["sl"]              = new_sl
        deal["next_watch_tp"]   = next_watch   # következő figyelendő TP szint
        deal["mozgo_sl_active"] = True
        deal["last_triggered_tp"] = triggered_tp_index
        _active_deals[ticket]   = deal
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
            await send_notification(
                f"⏰ <b>Pending törölve — időtúllépés</b>\n"
                f"Forrás: <b>{label}</b>\n"
                f"Ticket: #{ticket} | {config.PENDING_TIMEOUT_MINUTES} perc eltelt."
            )


# ── Aktív pozíció ellenőrzés ──────────────────────────────────────────────────

async def _check_deal(ticket: int, deal: dict):
    label     = _magic_label(deal["magic"])
    magic     = deal["magic"]
    pos1_magic = getattr(config, 'POS1_MAGIC', None)
    pos2_magic = getattr(config, 'POS2_MAGIC', None)
    pos3_magic = getattr(config, 'POS3_MAGIC', None)

    # ── Pozíció lezárult? ────────────────────────────────────────────────────
    if not is_position_open(ticket):
        logger.info(f"Pozíció zárul: #{ticket} ({label})")

        # Ha POS1 TP-n zárt → triggereljük a testvér pozíciók TP3 SL mozgatását
        if config.MOZGO_SL_ENABLED and magic == pos1_magic:
            if _was_closed_at_tp(ticket):
                logger.info(f"📍 POS1 (TP3) TP-n zárt → testvér SL mozgatás (TP3 trigger)")
                for sister in _get_sister_deals(ticket):
                    last = sister.get("last_triggered_tp", -1)
                    if last < 2:  # TP3 még nem volt triggerlve
                        await _apply_sl_move(sister, triggered_tp_index=2)
            else:
                logger.info(f"POS1 SL-en/manuálisan zárt — mozgó SL nem aktiválódik")

        # Ha POS2 TP-n zárt → triggereljük POS3 TP5 SL mozgatását
        if config.MOZGO_SL_ENABLED and magic == pos2_magic:
            if _was_closed_at_tp(ticket):
                logger.info(f"📍 POS2 (TP5) TP-n zárt → POS3 SL mozgatás (TP5 trigger)")
                for sister in _get_sister_deals(ticket):
                    if sister["magic"] == pos3_magic:
                        last = sister.get("last_triggered_tp", -1)
                        if last < 4:  # TP5 még nem volt triggerlve
                            await _apply_sl_move(sister, triggered_tp_index=4)

        await send_notification(
            f"🏁 <b>Pozíció lezárult</b>\n"
            f"Forrás: <b>{label}</b>\n"
            f"Ticket: #{ticket} | Cél: TP{deal['tp_index']+1}"
        )
        del _active_deals[ticket]
        _save_positions()
        return

    # Fix SL verzió — nincs teendő
    if not config.MOZGO_SL_ENABLED:
        return

    # POS1 esetén nincs ár figyelés — azt a zárás triggeri
    if magic == pos1_magic:
        return

    # ── POS2 és POS3: ár alapú figyelés (backup + önálló működés) ────────────
    rules = get_sl_rules(magic)
    if not rules:
        return

    # Meghatározzuk melyik TP szintet kell most figyelni
    last_triggered = deal.get("last_triggered_tp", -1)
    next_watch     = deal.get("next_watch_tp", 2)  # alapból TP3-tól indul

    if next_watch not in rules:
        return  # Nincs több SL mozgatás

    # Ellenőrizzük az árat
    tick = mt5.symbol_info_tick(config.SYMBOL)
    if tick is None:
        return

    current_price = tick.bid if deal["action"] == "SELL" else tick.ask
    watch_tp_price = deal["tp_levels"][next_watch] if next_watch < len(deal["tp_levels"]) else None

    if watch_tp_price is None:
        return

    tp_reached = (
        (deal["action"] == "SELL" and current_price <= watch_tp_price) or
        (deal["action"] == "BUY"  and current_price >= watch_tp_price)
    )

    if tp_reached and last_triggered < next_watch:
        logger.info(f"📍 TP{next_watch+1} ár alapú trigger: #{ticket} (magic={magic})")
        await _apply_sl_move(deal, triggered_tp_index=next_watch)


# ── Fő loop ───────────────────────────────────────────────────────────────────

async def run_monitor():
    mozgo = "MOZGÓ SL" if config.MOZGO_SL_ENABLED else "FIX SL"
    logger.info(f"Pozíció figyelő indult | Verzió: {mozgo} | Pending timeout: {config.PENDING_TIMEOUT_MINUTES} perc")
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
