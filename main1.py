"""
Trading Bot — Főprogram (dinamikus pozíció támogatás)
Tetszőleges számú pozíció párhuzamos nyitása (setup.py-ban állítható).
Mozgó SL: config.py-ban MOZGO_SL_ENABLED = True, SL_MOZGAS_ELSO_TP = 3

Indítás: python main1.py
"""

import asyncio
import logging
import sys
import os
import subprocess
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient, events

import config
from signal_parser import parse_signal
from mt5_trader import connect as mt5_connect, disconnect as mt5_disconnect
from mt5_trader import place_order, set_notifier as mt5_set_notifier, close_all_positions
from position_manager import register_deal, run_monitor
from notifier import notify_trade_opened, notify_trade_failed, notify_pending_opened, send_notification
from sheets_logger import log_trade, log_skipped_signal

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main_c1")

LABEL = config.BOT_NEV  # .env fájlban állítható: BOT_NEV=SuperXAUUSD

# ── Jelzés kor ellenőrzés ─────────────────────────────────────────────────────
MAX_JELZES_KOR_PERC = 10  # Ennél régebbi jelzést nem dolgoz fel a bot

# ── Napi kereskedés számláló ──────────────────────────────────────────────────
_napi_kereskedes_szam  = 0
_napi_kereskedes_datum = None

# ── Duplikáció szűrő ──────────────────────────────────────────────────────────
_utolso_jelzes_kulcs = None
_utolso_jelzes_ido   = None
_trading_paused      = False


# ── Dinamikus pozíció lista ───────────────────────────────────────────────────

def _get_poziciok() -> list[tuple]:
    """
    Visszaadja az aktív pozíciók listáját a config alapján.
    Visszatér: [(lot, magic, tp_index, label), ...]
    tp_index: 0-tól indexelve (pl. TP3 = index 2)
    """
    poziciok = []
    pozicio_szam = getattr(config, 'POZICIO_SZAM', 3)
    for i in range(1, pozicio_szam + 1):
        enabled = getattr(config, f'POS{i}_ENABLED', False)
        if not enabled:
            continue
        lot    = getattr(config, f'POS{i}_LOT', 0.01)
        magic  = getattr(config, f'POS{i}_MAGIC', 10 + i)
        tp_idx = getattr(config, f'POS{i}_TP_INDEX', i) - 1  # 0-tól indexelve
        label  = getattr(config, f'POS{i}_LABEL', f'TP{tp_idx+1}-fix')
        poziciok.append((lot, magic, tp_idx, label))
    return poziciok

def _get_first_magic() -> int | None:
    """Az első aktív pozíció magic numbere (napi számláló növeléshez)."""
    pozicio_szam = getattr(config, 'POZICIO_SZAM', 3)
    for i in range(1, pozicio_szam + 1):
        if getattr(config, f'POS{i}_ENABLED', False):
            return getattr(config, f'POS{i}_MAGIC', None)
    return None

def _get_aktiv_poz_lista() -> list[str]:
    """Státusz üzenethez formázott pozíció lista."""
    sorok = []
    pozicio_szam = getattr(config, 'POZICIO_SZAM', 3)
    for i in range(1, pozicio_szam + 1):
        if getattr(config, f'POS{i}_ENABLED', False):
            lot   = getattr(config, f'POS{i}_LOT', '?')
            magic = getattr(config, f'POS{i}_MAGIC', '?')
            label = getattr(config, f'POS{i}_LABEL', f'POS{i}')
            sorok.append(f"{label}({lot}lot, magic={magic})")
    return sorok


def check_and_update():
    """Indításkor ellenőrzi van-e újabb verzió GitHubon. Ha igen, frissít és újraindul."""
    try:
        result = subprocess.run(
            ["git", "fetch", "origin"],
            capture_output=True, text=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        if result.returncode != 0:
            print("Git fetch sikertelen — offline mód, frissítés kihagyva.")
            return

        result = subprocess.run(
            ["git", "rev-list", "HEAD..origin/main", "--count"],
            capture_output=True, text=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        count = result.stdout.strip()
        if count and int(count) > 0:
            print(f"🔄 {count} új commit elérhető — frissítés...")
            subprocess.run(
                ["git", "pull", "origin", "main"],
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            print("✅ Frissítés kész! Bot újraindítása...")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            print("✅ Bot naprakész, nincs frissítés.")
    except Exception as e:
        print(f"Frissítés ellenőrzés sikertelen: {e} — folytatás frissítés nélkül.")


async def do_update(client, notify_chat_id):
    """GitHub frissítés végrehajtása és bot újraindítása."""
    import subprocess, sys, os
    await send_notification(
        f"🔄 <b>Frissítés elindítva...</b>\n"
        f"GitHub ellenőrzése folyamatban."
    )
    try:
        result = subprocess.run(
            ["git", "pull", "origin", "main"],
            capture_output=True, text=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        if "Already up to date" in result.stdout:
            await send_notification("✅ <b>Már a legfrissebb verzió fut.</b>\nNincs új frissítés.")
            return
        await send_notification(
            f"✅ <b>Frissítés sikeres!</b>\n"
            f"Bot újraindítása folyamatban...\n\n"
            f"{result.stdout[:200]}"
        )
        await asyncio.sleep(2)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        await send_notification(f"❌ <b>Frissítés sikertelen!</b>\nHiba: {e}")


def check_mt5_health() -> dict:
    """MT5 önellenőrzés."""
    import MetaTrader5 as mt5
    import urllib.request
    eredmeny = {
        "mt5_fut":       False,
        "bejelentkezve": False,
        "szimbolum_ok":  False,
        "algo_trading":  False,
        "internet_ok":   False,
        "egyenleg":      None,
        "szerver":       None,
        "szimbolum":     config.SYMBOL,
        "terminal_path": config.MT5_TERMINAL_PATH,
    }

    try:
        urllib.request.urlopen("https://www.google.com", timeout=5)
        eredmeny["internet_ok"] = True
    except Exception:
        eredmeny["internet_ok"] = False

    try:
        if not mt5.initialize():
            return eredmeny
        eredmeny["mt5_fut"] = True

        info = mt5.account_info()
        if info is None:
            mt5.shutdown()
            return eredmeny
        eredmeny["bejelentkezve"] = True
        eredmeny["egyenleg"]      = info.balance
        eredmeny["szerver"]       = info.server

        terminal_info = mt5.terminal_info()
        if terminal_info is not None:
            eredmeny["algo_trading"] = terminal_info.trade_allowed

        sym = mt5.symbol_info(config.SYMBOL)
        if sym is not None and sym.visible:
            eredmeny["szimbolum_ok"] = True
        elif sym is not None:
            mt5.symbol_select(config.SYMBOL, True)
            sym2 = mt5.symbol_info(config.SYMBOL)
            eredmeny["szimbolum_ok"] = sym2 is not None

        mt5.shutdown()
    except Exception as e:
        logger.error(f"MT5 önellenőrzés hiba: {e}")

    return eredmeny


def format_mt5_health(check: dict) -> str:
    """Formázza az MT5 ellenőrzés eredményét Telegram üzenethez."""
    import os

    internet_sor = "✅ Internet: OK" if check["internet_ok"] else "❌ Internet: NINCS KAPCSOLAT!"
    mt5_sor      = "✅ MT5 fut" if check["mt5_fut"] else "❌ MT5 NEM fut!"
    bej_sor      = "✅ Bejelentkezve" if check["bejelentkezve"] else "❌ NEM bejelentkezve!"
    algo_sor     = "✅ Algo Trading: BE" if check["algo_trading"] else "❌ Algo Trading: KI! (pozíció nem nyílhat)"
    sym_sor      = f"✅ {check['szimbolum']} elérhető" if check["szimbolum_ok"] else f"❌ {check['szimbolum']} NEM elérhető!"

    path     = check.get("terminal_path") or "nincs beállítva"
    path_ok  = os.path.exists(path) if path and path != "nincs beállítva" else False
    path_sor = "✅ Terminal megtalálható" if path_ok else f"❌ Terminal NEM található!\n   ({path})"

    egyenleg_sor = f"💰 Egyenleg: {check['egyenleg']} USD" if check["egyenleg"] is not None else ""
    szerver_sor  = f"🖥️ Szerver: {check['szerver']}" if check["szerver"] else ""

    sorok = [internet_sor, mt5_sor, bej_sor, algo_sor, path_sor, sym_sor]
    if egyenleg_sor: sorok.append(egyenleg_sor)
    if szerver_sor:  sorok.append(szerver_sor)

    minden_ok = (check["internet_ok"] and check["mt5_fut"] and check["bejelentkezve"]
                 and check["algo_trading"] and check["szimbolum_ok"] and path_ok)
    fejlec = "🟢 <b>MT5 állapot: OK</b>" if minden_ok else "🔴 <b>MT5 állapot: PROBLÉMA!</b>"

    return fejlec + "\n" + "\n".join(sorok)


# ── Heartbeat ─────────────────────────────────────────────────────────────────

async def run_heartbeat():
    last_sent_day = None
    while True:
        now = datetime.now()
        if (now.hour   == config.HEARTBEAT_HOUR and
            now.minute == config.HEARTBEAT_MINUTE and
            now.day    != last_sent_day):

            mozgo = "MOZGÓ SL" if config.MOZGO_SL_ENABLED else "FIX SL"
            aktiv = _get_aktiv_poz_lista()

            # Napi számláló nullázása és pause visszaállítása
            global _napi_kereskedes_szam, _napi_kereskedes_datum, _trading_paused
            _napi_kereskedes_szam  = 0
            _napi_kereskedes_datum = now.date()
            if _trading_paused:
                _trading_paused = False
                logger.info("Pause automatikusan visszaállítva (napi nullázás).")

            mt5_check = check_mt5_health()
            mt5_info  = format_mt5_health(mt5_check)

            max_napi = getattr(config, 'MAX_NAPI_KERESKEDES', 0)
            limit_info = (
                f"Napi kereskedések: korlátlan"
                if max_napi == 0
                else f"Napi kereskedések: {_napi_kereskedes_szam}/{max_napi}"
            )

            await send_notification(
                f"✅ <b>{LABEL} bot él</b>\n"
                f"Idő: {now.strftime('%Y-%m-%d %H:%M')}\n"
                f"Verzió: {mozgo}\n"
                f"Aktív pozíciók: {', '.join(aktiv) if aktiv else 'egyik sem'}\n"
                f"{limit_info}\n\n"
                f"{mt5_info}"
            )
            last_sent_day = now.day
            logger.info("Heartbeat elküldve.")
        await asyncio.sleep(30)


# ── Jelzés feldolgozás ────────────────────────────────────────────────────────

async def process_signal(signal):
    global _napi_kereskedes_szam, _napi_kereskedes_datum, _trading_paused
    global _utolso_jelzes_kulcs, _utolso_jelzes_ido

    logger.info(f"[{LABEL}] Jelzés: {signal.action} @ {signal.entry_mid}")

    # ── Duplikáció szűrő ──────────────────────────────────────────────────────
    jelzes_kulcs = f"{signal.action}_{signal.entry_mid}"
    most = datetime.now()
    if (_utolso_jelzes_kulcs == jelzes_kulcs and
            _utolso_jelzes_ido is not None and
            (most - _utolso_jelzes_ido).total_seconds() < 10):
        logger.warning(
            f"[{LABEL}] Duplikált jelzés kiszűrve: {jelzes_kulcs} "
            f"(már feldolgozva {(most - _utolso_jelzes_ido).total_seconds():.1f} másodperce)"
        )
        return
    _utolso_jelzes_kulcs = jelzes_kulcs
    _utolso_jelzes_ido   = most

    # ── Pause ellenőrzés ──────────────────────────────────────────────────────
    if _trading_paused:
        logger.info(f"[{LABEL}] Kereskedés szüneteltetve (/pause) — jelzés kihagyva.")
        await send_notification(
            f"⏸️ <b>Jelzés érkezett — kereskedés szünetel</b>\n"
            f"A /resume paranccsal lehet visszakapcsolni."
        )
        return

    # ── Napi limit ellenőrzés ─────────────────────────────────────────────────
    max_napi = getattr(config, 'MAX_NAPI_KERESKEDES', 0)
    if max_napi > 0:
        if _napi_kereskedes_szam >= max_napi:
            logger.info(f"[{LABEL}] Napi limit elérve ({_napi_kereskedes_szam}/{max_napi}) — jelzés kihagyva.")
            await send_notification(
                f"🚫 <b>Napi kereskedési limit elérve!</b>\n"
                f"Ma már {_napi_kereskedes_szam} kereskedés volt (max: {max_napi}).\n"
                f"Este 20:00-kor automatikusan visszaáll."
            )
            return

    mozgo = "MOZGÓ SL" if config.MOZGO_SL_ENABLED else "FIX SL"
    logger.info(f"[{LABEL}] Verzió: {mozgo}")

    poziciok = _get_poziciok()
    if not poziciok:
        logger.warning("Minden pozíció ki van kapcsolva a config.py-ban!")
        return

    first_magic = _get_first_magic()
    for lot, magic, tp_index, pos_label in poziciok:
        deal, error = place_order(
            signal, config,
            lot_size=lot,
            magic=magic,
            tp_index=tp_index,
        )
        full_label = f"{LABEL} {pos_label}"
        if deal:
            register_deal(deal)
            if deal.get("is_pending"):
                await notify_pending_opened(deal, label=full_label)
            else:
                await notify_trade_opened(deal, label=full_label)
            log_trade(deal)
            # Csak az első pozíció nyitásakor növeljük a számlálót
            if magic == first_magic:
                _napi_kereskedes_szam += 1
                logger.info(f"Napi kereskedés számláló: {_napi_kereskedes_szam}")
        else:
            await notify_trade_failed(error, label=full_label)
            log_skipped_signal(signal, error)


# ── Jelzés feldolgozó helper ──────────────────────────────────────────────────

async def handle_message_text(text: str, source: str = "új"):
    """Közös logika új és szerkesztett üzenetekhez."""
    logger.info(f"[{LABEL}] {source.capitalize()} üzenet ({len(text)} karakter)")

    if text.strip().lower() in ["/update", "!update"]:
        logger.info(f"[{LABEL}] UPDATE parancs érkezett!")
        return "update"

    if "close" in text.lower():
        logger.info(f"[{LABEL}] CLOSE parancs érkezett!")
        return "close"

    signal = parse_signal(text)
    if signal:
        asyncio.create_task(process_signal(signal))
    else:
        logger.info(f"[{LABEL}] Nem felismert formátum — kihagyva. Szöveg: {text[:80]!r}")

    return None


# ── Főprogram ─────────────────────────────────────────────────────────────────

async def run_bot():
    logger.info("=" * 60)
    logger.info(f"{config.BOT_NEV} Trading Bot indítása...")
    mozgo = "MOZGÓ SL" if config.MOZGO_SL_ENABLED else "FIX SL"
    logger.info(f"Verzió: {mozgo}")
    aktiv = _get_aktiv_poz_lista()
    logger.info(f"Aktív pozíciók: {', '.join(aktiv)}")
    logger.info("=" * 60)

    mt5_set_notifier(send_notification)

    if not mt5_connect(config):
        logger.critical("MT5 csatlakozás sikertelen. Bot leáll.")
        return

    client = TelegramClient(
        config.SESSION_NEV,
        config.TELEGRAM_API_ID,
        config.TELEGRAM_API_HASH,
        catch_up=True,
    )

    # ── Új üzenetek figyelése ─────────────────────────────────────────────────
    @client.on(events.NewMessage(chats=config.SIGNAL_CHANNEL))
    async def on_message(event):
        text = event.message.text or ""

        uzenet_ideje = event.message.date
        most_utc = datetime.now(timezone.utc)
        kor_perc = (most_utc - uzenet_ideje).total_seconds() / 60
        if kor_perc > MAX_JELZES_KOR_PERC:
            signal_check = parse_signal(text)
            if signal_check:
                logger.warning(
                    f"[{LABEL}] ⚠️ Lejárt jelzés ({kor_perc:.1f} perc régi) — kihagyva. "
                    f"({signal_check.action} @ {signal_check.entry_mid})"
                )
                # Csak logba írjuk, NEM küldünk Telegram értesítőt
            return

        cmd = await handle_message_text(text, source="új")

        if cmd == "update":
            sender = await event.get_sender()
            if sender and sender.id == config.NOTIFY_CHAT_ID:
                asyncio.create_task(do_update(client, config.NOTIFY_CHAT_ID))
            else:
                logger.warning("UPDATE parancs ismeretlen feladótól — kihagyva.")

        elif cmd == "close":
            sikeres, sikertelen = close_all_positions(config, label=LABEL)
            if sikeres > 0 or sikertelen > 0:
                await send_notification(
                    f"🔴 <b>Close parancs végrehajtva!</b>\n"
                    f"Forrás: <b>{LABEL}</b>\n"
                    f"✅ Sikeresen lezárva: {sikeres} pozíció\n"
                    f"❌ Sikertelen: {sikertelen} pozíció"
                )
            else:
                await send_notification(
                    f"ℹ️ <b>Close parancs érkezett</b>\n"
                    f"Forrás: <b>{LABEL}</b>\n"
                    f"Nincs nyitott bot pozíció."
                )

    # ── Szerkesztett üzenetek figyelése ──────────────────────────────────────
    @client.on(events.MessageEdited(chats=config.SIGNAL_CHANNEL))
    async def on_edited(event):
        text = event.message.text or ""
        logger.info(f"[{LABEL}] Szerkesztett üzenet érkezett ({len(text)} karakter)")

        uzenet_ideje = event.message.date
        most_utc = datetime.now(timezone.utc)
        kor_perc = (most_utc - uzenet_ideje).total_seconds() / 60
        if kor_perc > MAX_JELZES_KOR_PERC:
            signal_check = parse_signal(text)
            if signal_check:
                logger.warning(
                    f"[{LABEL}] ⚠️ Lejárt szerkesztett jelzés ({kor_perc:.1f} perc régi) — kihagyva. "
                    f"({signal_check.action} @ {signal_check.entry_mid})"
                )
                # Csak logba írjuk, NEM küldünk Telegram értesítőt
            return

        signal = parse_signal(text)
        if signal:
            logger.info(f"[{LABEL}] Szerkesztett jelzés feldolgozva: {signal.action} @ {signal.entry_mid}")
            asyncio.create_task(process_signal(signal))
        else:
            logger.info(f"[{LABEL}] Szerkesztett üzenet — nem jelzés formátum, kihagyva.")

    # ── Parancs csoport figyelő ───────────────────────────────────────────────
    command_channel = getattr(config, 'COMMAND_CHANNEL', None)
    if command_channel:
        @client.on(events.NewMessage(chats=command_channel))
        async def on_command(event):
            global _trading_paused

            # Parancs kor ellenőrzés — régi parancsokat kihagyja újraindításkor
            uzenet_ideje = event.message.date
            most_utc = datetime.now(timezone.utc)
            kor_perc = (most_utc - uzenet_ideje).total_seconds() / 60
            if kor_perc > 5:
                logger.info(f"[{LABEL}] Régi parancs kihagyva ({kor_perc:.1f} perc régi)")
                return

            text = (event.message.text or "").strip().lower()
            sender = await event.get_sender()
            sender_id = sender.id if sender else None

            if sender_id != config.NOTIFY_CHAT_ID:
                logger.warning(f"Parancs ismeretlen feladótól ({sender_id}) — kihagyva.")
                return

            logger.info(f"[{LABEL}] Parancs érkezett: {text}")

            if text in ["/update", "!update"]:
                asyncio.create_task(do_update(client, config.NOTIFY_CHAT_ID))

            elif text in ["/close", "!close"]:
                sikeres, sikertelen = close_all_positions(config, label=LABEL)
                if sikeres > 0 or sikertelen > 0:
                    await send_notification(
                        f"🔴 <b>Close parancs végrehajtva!</b>\n"
                        f"Forrás: <b>{LABEL}</b>\n"
                        f"✅ Lezárva: {sikeres} | ❌ Sikertelen: {sikertelen}"
                    )
                else:
                    await send_notification(f"ℹ️ <b>Nincs nyitott bot pozíció.</b>")

            elif text in ["/status", "!status"]:
                now = datetime.now()
                mozgo = "MOZGÓ SL" if config.MOZGO_SL_ENABLED else "FIX SL"
                elso_tp = getattr(config, 'SL_MOZGAS_ELSO_TP', 3)
                mozgo_info = f"{mozgo} (TP{elso_tp}-től)" if config.MOZGO_SL_ENABLED else mozgo
                aktiv_poz = _get_aktiv_poz_lista()
                mt5_check = check_mt5_health()
                mt5_info  = format_mt5_health(mt5_check)
                max_napi  = getattr(config, 'MAX_NAPI_KERESKEDES', 0)
                limit_info = (
                    f"Napi kereskedések: korlátlan"
                    if max_napi == 0
                    else f"Napi kereskedések: {_napi_kereskedes_szam}/{max_napi}"
                )
                pause_sor = "⏸️ Kereskedés SZÜNETEL\n" if _trading_paused else ""

                await send_notification(
                    f"📊 <b>{LABEL} bot státusz</b>\n"
                    f"Idő: {now.strftime('%Y-%m-%d %H:%M')}\n"
                    f"Verzió: {mozgo_info}\n"
                    f"Aktív pozíciók: {', '.join(aktiv_poz) if aktiv_poz else 'egyik sem'}\n"
                    f"{limit_info}\n"
                    f"{pause_sor}"
                    f"\n{mt5_info}"
                )

            elif text in ["/pause", "!pause"]:
                _trading_paused = True
                await send_notification(
                    f"⏸️ <b>Kereskedés szüneteltetve!</b>\n"
                    f"Forrás: <b>{LABEL}</b>\n"
                    f"Új jelzésekre nem reagál.\n"
                    f"Meglévő pozíciók futnak tovább (TP/SL szerint zárnak).\n"
                    f"Visszakapcsolás: /resume\n"
                    f"Automatikus visszaállítás: este 20:00"
                )

            elif text in ["/stop", "!stop"]:
                _trading_paused = True
                sikeres, sikertelen = close_all_positions(config, label=LABEL)
                await send_notification(
                    f"🛑 <b>STOP — Minden pozíció lezárva!</b>\n"
                    f"Forrás: <b>{LABEL}</b>\n"
                    f"✅ Lezárva: {sikeres} pozíció\n"
                    f"❌ Sikertelen: {sikertelen} pozíció\n\n"
                    f"Új kereskedések szünetelnek.\n"
                    f"Visszakapcsolás: /resume\n"
                    f"Automatikus visszaállítás: este 20:00"
                )

            elif text in ["/resume", "!resume"]:
                _trading_paused = False
                await send_notification(
                    f"▶️ <b>Kereskedés visszakapcsolva!</b>\n"
                    f"Forrás: <b>{LABEL}</b>\n"
                    f"A bot ismét reagál az új jelzésekre."
                )

            elif text in ["/help", "!help"]:
                max_napi = getattr(config, 'MAX_NAPI_KERESKEDES', 0)
                await send_notification(
                    f"📋 <b>Elérhető parancsok:</b>\n\n"
                    f"/update — Bot frissítése GitHubról\n"
                    f"/close — Összes pozíció lezárása (bot fut tovább)\n"
                    f"/pause — Nem nyit újat, meglévők futnak tovább\n"
                    f"/stop — Azonnal lezár mindent és nem nyit újat\n"
                    f"/resume — Kereskedés visszakapcsolása\n"
                    f"/status — Bot státusz\n"
                    f"/help — Parancsok listája\n\n"
                    f"Napi limit: {'korlátlan' if max_napi == 0 else str(max_napi)}"
                )

        logger.info(f"[{LABEL}] Parancs csatorna figyelés aktív: {command_channel}")

    await client.start(phone=config.TELEGRAM_PHONE)
    logger.info(f"[{LABEL}] Telegram figyelés aktív: {config.SIGNAL_CHANNEL}")

    # ── Indulási értesítő ────────────────────────────────────────────────────
    mozgo = "MOZGÓ SL" if config.MOZGO_SL_ENABLED else "FIX SL"
    elso_tp = getattr(config, 'SL_MOZGAS_ELSO_TP', 3)
    mozgo_info = f"{mozgo} (TP{elso_tp}-től)" if config.MOZGO_SL_ENABLED else mozgo

    settings_list = []
    pozicio_szam = getattr(config, 'POZICIO_SZAM', 3)
    for i in range(1, pozicio_szam + 1):
        if getattr(config, f'POS{i}_ENABLED', False):
            lot   = getattr(config, f'POS{i}_LOT', '?')
            magic = getattr(config, f'POS{i}_MAGIC', '?')
            tp_idx = getattr(config, f'POS{i}_TP_INDEX', i)
            settings_list.append(f"💰 POS{i} Lot: <b>{lot}</b> (TP{tp_idx}) | Magic: <code>{magic}</code>")

    sl_status = "<b>BE</b>" if config.MOZGO_SL_ENABLED else "<b>KI</b>"
    settings_list.append(f"🛡️ Mozgó SL: {sl_status}")
    if config.MOZGO_SL_ENABLED:
        settings_list.append(f"📍 SL mozog: TP{elso_tp}-től")

    settings_info = "\n" + "\n".join(settings_list) if settings_list else "\n<i>Nincs aktív beállítás.</i>"

    await send_notification(
        f"🟢 <b>{LABEL} bot elindult!</b>\n"
        f"Verzió: <b>{mozgo_info}</b>\n"
        f"----------------------------"
        f"{settings_info}"
    )

    monitor_task   = asyncio.create_task(run_monitor())
    heartbeat_task = asyncio.create_task(run_heartbeat())

    try:
        await client.run_until_disconnected()
    finally:
        monitor_task.cancel()
        heartbeat_task.cancel()
        mt5_disconnect()
        await send_notification(f"🔴 <b>{LABEL} bot leállt.</b>")


if __name__ == "__main__":
    check_and_update()
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot manuálisan leállítva.")
