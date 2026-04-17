"""
Pozíció figyelő — v4 (dinamikus pozíció támogatás)
- Dinamikus pozíció lista (POZICIO_SZAM alapján, nincs hardcode POS1/2/3)
- SL_MOZGAS_ELSO_TP: melyik TP elérésekor mozduljon először az SL
- Kettős trigger: testvér pozíció zárulása + ár alapú figyelés
- Önállóan is működik (pl. csak 1 pozíció fut)
- JSON mentés adatvesztés ellen

Mozgó SL logika (SL_MOZGAS_ELSO_TP alapján):
  Ha SL_MOZGAS_ELSO_TP = 3:
    TP3 elérve → SL = Entry
    TP4 elérve → SL = TP1
    TP5 elérve → SL = TP2
    stb.
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


# ── Dinamikus magic lista ─────────────────────────────────────────────────────

def _get_all_magics() -> list[int]:
    """Visszaadja az összes aktív pozíció magic numberét a config alapján."""
    magics = []
    pozicio_szam = getattr(config, 'POZICIO_SZAM', 3)
    for i in range(1, pozicio_szam + 1):
        magic = getattr(config, f'POS{i}_MAGIC', None)
        enabled = getattr(config, f'POS{i}_ENABLED', False)
        if magic is not None and enabled:
            magics.append(magic)
    return magics

def _get_pozicio_index(magic: int) -> int:
    """Visszaadja hogy az adott magic a hányadik pozíció (0-tól indexelve)."""
    pozicio_szam = getattr(config, 'POZICIO_SZAM', 3)
    for i in range(1, pozicio_szam + 1):
        if getattr(config, f'POS{i}_MAGIC', None) == magic:
            return i - 1  # 0-tól indexelve
    return -1

def _get_pozicio_tp_index(magic: int) -> int:
    """Visszaadja az adott magic pozíció TP indexét."""
    pozicio_szam = getattr(config, 'POZICIO_SZAM', 3)
    for i in range(1, pozicio_szam + 1):
        if getattr(config, f'POS{i}_MAGIC', None) == magic:
            return getattr(config, f'POS{i}_TP_INDEX', 1) - 1  # 0-tól indexelve
    return 0


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

def get_sl_rules(magic: int) -> dict:
    """
    Visszaadja a mozgó SL szabályokat az adott magic numberhez.
    Dinamikusan épül fel a SL_MOZGAS_ELSO_TP és a pozíció TP indexe alapján.

    SL_MOZGAS_ELSO_TP = 3 (alapértelmezett):
      TP3 elérve → SL = Entry
      TP4 elérve → SL = TP1
      TP5 elérve → SL = TP2
      stb.

    A szabályok csak addig mennek ameddig az adott pozíció TP-je engedi.
    """
    if not getattr(config, 'MOZGO_SL_ENABLED', False):
        return {}

    # Melyik TP-nél kezdődjön az SL mozgatás (1-től számozva, pl. 3 = TP3-nál)
    elso_tp = getattr(config, 'SL_MOZGAS_ELSO_TP', 3)
    elso_tp_idx = elso_tp - 1  # 0-tól indexelve

    # A pozíció saját TP indexe — csak addig érdemes szabályokat generálni
    pozicio_tp_idx = _get_pozicio_tp_index(magic)

    if pozicio_tp_idx < elso_tp_idx:
        # Ez a pozíció nem ér el az első SL mozgatási TP-ig → nincs szabály
        return {}

    rules = {}
    # Az első SL mozgatástól a pozíció TP-jéig generálunk szabályokat
    for tp_idx in range(elso_tp_idx, pozicio_tp_idx):
        # sl_source: hány TP-vel van az aktuális trigger előtt
        # Első trigger: "entry", utána TP1, TP2, stb.
        offset = tp_idx - elso_tp_idx
        if offset == 0:
            sl_source = "entry"
        else:
            sl_source = offset - 1  # TP(offset) index, 0-tól
        rules[tp_idx] = {
            "sl_source": sl_source,
            "next_watch": tp_idx + 1,
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
    pozicio_szam = getattr(config, 'POZICIO_SZAM', 3)
    for i in range(1, pozicio_szam + 1):
        if getattr(config, f'POS{i}_MAGIC', None) == magic:
            label = getattr(config, f'POS{i}_LABEL', f'POS{i}')
            return f"{csoport} {label}"
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
    triggered_tp_index: melyik TP szint lett elérve (0-tól indexelve, pl. 2=TP3)
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
    label  = _magic_label(deal["magic"])
    magic  = deal["magic"]
    magics = _get_all_magics()

    elso_tp     = getattr(config, 'SL_MOZGAS_ELSO_TP', 3)
    elso_tp_idx = elso_tp - 1  # 0-tól indexelve

    # ── Pozíció lezárult? ────────────────────────────────────────────────────
    if not is_position_open(ticket):
        logger.info(f"Pozíció zárul: #{ticket} ({label})")

        # Ha mozgó SL be van kapcsolva és a pozíció TP-n zárt →
        # triggereljük a testvér pozíciókat a megfelelő TP szinten
        if config.MOZGO_SL_ENABLED and _was_closed_at_tp(ticket):
            pozicio_tp_idx = _get_pozicio_tp_index(magic)
            logger.info(f"📍 {label} TP-n zárt (TP{pozicio_tp_idx+1}) → testvér SL mozgatás")
            for sister in _get_sister_deals(ticket):
                sister_rules = get_sl_rules(sister["magic"])
                last = sister.get("last_triggered_tp", -1)
                # Csak akkor triggereljük ha ez a TP szint még nem volt triggerlve
                # és a testvér pozíciónak van szabálya erre a szintre
                if pozicio_tp_idx in sister_rules and last < pozicio_tp_idx:
                    await _apply_sl_move(sister, triggered_tp_index=pozicio_tp_idx)
        elif config.MOZGO_SL_ENABLED:
            logger.info(f"{label} SL-en/manuálisan zárt — mozgó SL nem aktiválódik")

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

    # ── Ár alapú figyelés (backup + önálló működés) ──────────────────────────
    rules = get_sl_rules(magic)
    if not rules:
        return

    last_triggered = deal.get("last_triggered_tp", -1)
    next_watch     = deal.get("next_watch_tp", elso_tp_idx)

    if next_watch not in rules:
        return  # Nincs több SL mozgatás

    tick = mt5.symbol_info_tick(config.SYMBOL)
    if tick is None:
        return

    current_price  = tick.bid if deal["action"] == "SELL" else tick.ask
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
