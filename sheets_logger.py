"""
Google Sheets naplózó — Trading Bot
Két lap:
  1. "Kereskedések" — minden nyitott/zárt pozíció adata
  2. "Statisztika"  — automatikus összesítők, napi bontás

Beállítás:
  - Google Cloud Console-ban létre kell hozni egy Service Account-ot
  - A service account JSON kulcsfájlt le kell tölteni
  - A kulcsfájl elérési útját a .env fájlban kell megadni: SHEETS_CREDENTIALS_FILE
  - A Sheets ID-t a .env fájlban: SHEETS_ID (vagy automatikusan létrehozza)
  - A megosztandó email: SHEETS_SHARE_EMAIL

Telepítés:
  pip install gspread google-auth
"""

import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Lazy import — csak ha be van kapcsolva ────────────────────────────────────
_gc          = None   # gspread kliens
_spreadsheet = None   # a megnyitott spreadsheet (saját)
_sh_kereskedes = None  # "Kereskedések" munkalap
_sh_statisztika = None  # "Statisztika" munkalap
_initialized = False
_init_failed = False

# ── Közös összesített sheet ───────────────────────────────────────────────────
_kozos_spreadsheet  = None
_kozos_sh_kereskedes = None
_kozos_initialized  = False
_kozos_init_failed  = False

KERESKEDES_FEJLEC = [
    "Dátum", "Idő", "Bot", "Irány", "Belépési ár", "SL", "TP szint",
    "TP ár", "Lot", "Magic", "Pozíció label", "Signal ID",
    "Ticket",
    "Zárási ár", "Eredmény (USD)", "Időtartam (perc)", "Státusz",
    "Megjegyzés"
]


def _init_sheets():
    """Inicializálja a Google Sheets kapcsolatot."""
    global _gc, _spreadsheet, _sh_kereskedes, _sh_statisztika, _initialized, _init_failed

    if _initialized or _init_failed:
        return _initialized

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        logger.warning("gspread / google-auth nincs telepítve — Sheets naplózás kikapcsolva.")
        logger.warning("Telepítés: pip install gspread google-auth")
        _init_failed = True
        return False

    try:
        import config

        creds_file   = getattr(config, 'SHEETS_CREDENTIALS_FILE', None)
        sheets_id    = getattr(config, 'SHEETS_ID', None)
        share_email  = getattr(config, 'SHEETS_SHARE_EMAIL', 'matyastamas88@gmail.com')
        bot_nev      = getattr(config, 'BOT_NEV', 'TradingBot')

        if not creds_file or not os.path.exists(creds_file):
            logger.warning(f"Sheets credentials fájl nem található: {creds_file} — naplózás kikapcsolva.")
            _init_failed = True
            return False

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
        _gc = gspread.authorize(creds)

        # Spreadsheet megnyitása vagy létrehozása
        if sheets_id:
            try:
                _spreadsheet = _gc.open_by_key(sheets_id)
                logger.info(f"Sheets megnyitva: {_spreadsheet.title}")
            except Exception:
                logger.warning(f"Sheets ID ({sheets_id}) nem található — új spreadsheet létrehozása.")
                _spreadsheet = None

        if _spreadsheet is None:
            # Mappa ID — ide kerül a Sheets fájl a Drive-ban
            drive_folder_id = getattr(config, 'SHEETS_FOLDER_ID', None)

            if drive_folder_id:
                # Létrehozás adott mappában a Drive API-val
                drive = _gc.auth.authorized_session if hasattr(_gc, 'auth') else None
                file_metadata = {
                    "name": f"{bot_nev} — Kereskedési napló",
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                    "parents": [drive_folder_id],
                }
                from googleapiclient.discovery import build
                from google.oauth2.service_account import Credentials as _Creds
                _creds2 = _Creds.from_service_account_file(creds_file, scopes=[
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ])
                drive_service = build("drive", "v3", credentials=_creds2)
                created = drive_service.files().create(
                    body=file_metadata,
                    fields="id"
                ).execute()
                new_id = created.get("id")
                _spreadsheet = _gc.open_by_key(new_id)
                logger.info(f"Új Sheets létrehozva mappában: {_spreadsheet.title} | ID: {new_id}")
            else:
                # Létrehozás gyökér mappában
                _spreadsheet = _gc.create(f"{bot_nev} — Kereskedési napló")
                new_id = _spreadsheet.id
                logger.info(f"Új Sheets létrehozva: {_spreadsheet.title} | ID: {new_id}")

            logger.info(f"Írd be a .env fájlba: SHEETS_ID={new_id}")

        # Megosztás a személyes email-lel
        try:
            _spreadsheet.share(share_email, perm_type='user', role='writer', notify=False)
            logger.info(f"Sheets megosztva: {share_email}")
        except Exception as e:
            logger.warning(f"Megosztás sikertelen ({share_email}): {e}")

        # Munkalapok létrehozása / megnyitása
        _sh_kereskedes  = _get_or_create_sheet("Kereskedések", KERESKEDES_FEJLEC)
        _sh_statisztika = _get_or_create_sheet("Statisztika", [])
        _init_statisztika_lap()

        _initialized = True
        logger.info("Google Sheets naplózás aktív.")
        return True

    except Exception as e:
        logger.error(f"Sheets inicializálás sikertelen: {e}")
        _init_failed = True
        return False



def _init_kozos_sheets():
    """Inicializálja a közös összesített Google Sheets kapcsolatot."""
    global _kozos_spreadsheet, _kozos_sh_kereskedes, _kozos_initialized, _kozos_init_failed

    if _kozos_initialized or _kozos_init_failed:
        return _kozos_initialized

    try:
        import gspread
        from google.oauth2.service_account import Credentials
        import config

        kozos_id   = getattr(config, 'SHEETS_KOZOS_ID', None)
        creds_file = getattr(config, 'SHEETS_CREDENTIALS_FILE', None)

        if not kozos_id or not creds_file or not os.path.exists(creds_file):
            _kozos_init_failed = True
            return False

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
        gc = gspread.authorize(creds)
        _kozos_spreadsheet = gc.open_by_key(kozos_id)
        _kozos_sh_kereskedes = _get_or_create_sheet_in(
            _kozos_spreadsheet, "Kereskedések", KERESKEDES_FEJLEC
        )
        _kozos_initialized = True
        logger.info(f"✅ Közös Sheets aktív: {_kozos_spreadsheet.title}")
        return True

    except Exception as e:
        logger.warning(f"Közös Sheets init sikertelen: {e}")
        _kozos_init_failed = True
        return False


def _get_or_create_sheet_in(spreadsheet, name: str, fejlec: list):
    """Adott spreadsheet-ben keres vagy létrehoz munkalapot."""
    try:
        return spreadsheet.worksheet(name)
    except Exception:
        sh = spreadsheet.add_worksheet(title=name, rows=5000, cols=30)
        if fejlec:
            sh.append_row(fejlec, value_input_option='USER_ENTERED')
            try:
                _format_header(sh, len(fejlec))
            except Exception:
                pass
        return sh


def _get_or_create_sheet(name: str, fejlec: list):
    """Visszaadja a munkalapot, ha nem létezik létrehozza."""
    try:
        sh = _spreadsheet.worksheet(name)
        return sh
    except Exception:
        sh = _spreadsheet.add_worksheet(title=name, rows=2000, cols=30)
        if fejlec:
            sh.append_row(fejlec, value_input_option='USER_ENTERED')
            # Fejléc formázása (félkövér, háttérszín)
            try:
                _format_header(sh, len(fejlec))
            except Exception:
                pass
        return sh


def _format_header(sh, col_count: int):
    """Formázza a fejléc sort."""
    sh.format(f"A1:{_col_letter(col_count)}1", {
        "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.6},
        "textFormat": {
            "bold": True,
            "foregroundColor": {"red": 1, "green": 1, "blue": 1}
        },
        "horizontalAlignment": "CENTER"
    })


def _col_letter(n: int) -> str:
    """Számot oszlop betűvé alakít (1=A, 26=Z, 27=AA)."""
    result = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _init_statisztika_lap():
    """Inicializálja a Statisztika lapot képletekkel."""
    try:
        sh = _sh_statisztika
        sh.clear()

        # Fejléc és statisztikai blokk
        adatok = [
            ["📊 KERESKEDÉSI STATISZTIKA", "", "", "Frissítve:", "=NOW()"],
            [""],
            ["── ÖSSZESÍTŐ ──────────────────────────────"],
            ["Összes kereskedés",       "=COUNTA(Kereskedések!A2:A)-COUNTBLANK(Kereskedések!A2:A)"],
            ["Lezárt kereskedések",     "=COUNTIF(Kereskedések!P2:P,\"TP\")+COUNTIF(Kereskedések!P2:P,\"SL\")+COUNTIF(Kereskedések!P2:P,\"Manuális\")"],
            ["Nyertes kereskedések",    "=COUNTIF(Kereskedések!N2:N,\">0\")"],
            ["Vesztes kereskedések",    "=COUNTIF(Kereskedések!N2:N,\"<0\")"],
            ["Nyerési arány %",         "=IFERROR(ROUND(B6/B5*100,1)&\"%\",\"–\")"],
            [""],
            ["── PROFIT/LOSS ────────────────────────────"],
            ["Összes P&L (USD)",        "=IFERROR(ROUND(SUM(Kereskedések!N2:N),2),0)"],
            ["Átlag nyereség (USD)",     "=IFERROR(ROUND(AVERAGEIF(Kereskedések!N2:N,\">0\"),2),\"–\")"],
            ["Átlag veszteség (USD)",    "=IFERROR(ROUND(AVERAGEIF(Kereskedések!N2:N,\"<0\"),2),\"–\")"],
            ["Legnagyobb nyereség",      "=IFERROR(ROUND(MAX(Kereskedések!N2:N),2),\"–\")"],
            ["Legnagyobb veszteség",     "=IFERROR(ROUND(MIN(Kereskedések!N2:N),2),\"–\")"],
            [""],
            ["── BONTÁS IRÁNY SZERINT ───────────────────"],
            ["BUY kereskedések",         "=COUNTIF(Kereskedések!D2:D,\"BUY\")"],
            ["SELL kereskedések",        "=COUNTIF(Kereskedések!D2:D,\"SELL\")"],
            ["BUY P&L (USD)",            "=IFERROR(ROUND(SUMIF(Kereskedések!D2:D,\"BUY\",Kereskedések!N2:N),2),0)"],
            ["SELL P&L (USD)",           "=IFERROR(ROUND(SUMIF(Kereskedések!D2:D,\"SELL\",Kereskedések!N2:N),2),0)"],
            [""],
            ["── NAPI BONTÁS ─────────────────────────────"],
            ["Dátum", "Kereskedések", "P&L (USD)", "Nyertes", "Vesztes"],
        ]

        sh.update('A1', adatok, value_input_option='USER_ENTERED')

        # Fejléc formázás
        sh.format("A1:E1", {
            "backgroundColor": {"red": 0.1, "green": 0.4, "blue": 0.1},
            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
        })
        # Összesítő fejlécek
        for sor in ["A3", "A10", "A17", "A23"]:
            sh.format(sor, {"textFormat": {"bold": True, "foregroundColor": {"red": 0.2, "green": 0.2, "blue": 0.6}}})

        # Összes P&L kiemelés
        sh.format("B11", {"textFormat": {"bold": True, "fontSize": 12}})

        logger.info("Statisztika lap inicializálva.")
    except Exception as e:
        logger.error(f"Statisztika lap inicializálás hiba: {e}")


def _frissit_napi_bontas():
    """Frissíti a napi bontás táblázatot a Statisztika lapon."""
    try:
        kereskedes_sh = _sh_kereskedes
        stat_sh       = _sh_statisztika

        # Összes sor beolvasása
        sorok = kereskedes_sh.get_all_values()
        if len(sorok) < 2:
            return

        # Napi összesítés
        napi = {}
        for sor in sorok[1:]:
            if not sor or not sor[0]:
                continue
            datum = sor[0]  # Dátum oszlop
            try:
                pl = float(sor[13]) if sor[13] else 0  # Eredmény (USD) oszlop
            except (ValueError, IndexError):
                pl = 0
            if datum not in napi:
                napi[datum] = {"db": 0, "pl": 0, "nyert": 0, "veszett": 0}
            napi[datum]["db"] += 1
            napi[datum]["pl"] += pl
            if pl > 0:
                napi[datum]["nyert"] += 1
            elif pl < 0:
                napi[datum]["veszett"] += 1

        # Napi bontás beírása (25. sortól)
        napi_sorok = []
        for datum in sorted(napi.keys(), reverse=True):
            d = napi[datum]
            napi_sorok.append([
                datum,
                d["db"],
                round(d["pl"], 2),
                d["nyert"],
                d["veszett"],
            ])

        if napi_sorok:
            # Töröljük a régi napi adatokat
            stat_sh.batch_clear(["A25:E200"])
            stat_sh.update('A25', napi_sorok, value_input_option='USER_ENTERED')

    except Exception as e:
        logger.error(f"Napi bontás frissítés hiba: {e}")


# ── Publikus függvények ───────────────────────────────────────────────────────

def init_on_startup():
    """
    Bot indulaskor hivodik — inicializalja a Sheets kapcsolatot
    es kiirja a logba a Sheets ID-t hogy be lehessen masolni a .env-be.
    """
    logger.info("Google Sheets inicializalas...")
    if _init_sheets():
        logger.info(f"✅ Google Sheets aktiv!")
        logger.info(f"📊 Sheets neve: {_spreadsheet.title}")
        logger.info(f"🔑 Sheets ID: {_spreadsheet.id}")
        logger.info(f"🔗 Sheets URL: https://docs.google.com/spreadsheets/d/{_spreadsheet.id}")
        logger.info(f"   >>> Masold be a .env fajlba: SHEETS_ID={_spreadsheet.id} <<<")
    else:
        logger.warning("⚠️ Google Sheets NEM aktiv — ellenorizd a credentials fajlt es a .env beallitasokat.")

    # Közös sheet ellenőrzése
    import config
    kozos_id = getattr(config, 'SHEETS_KOZOS_ID', None)
    if kozos_id:
        _init_kozos_sheets()
    else:
        logger.info("ℹ️ Közös összesített Sheets nincs beállítva (SHEETS_KOZOS_ID hiányzik a .env-ből)")


def log_trade(deal: dict):
    """
    Pozíció nyitásakor hívódik — beírja az adatokat a Kereskedések lapra.
    A Zárási ár, Eredmény, Időtartam, Státusz később kerül kitöltésre.
    """
    if not _init_sheets():
        return

    try:
        import config
        bot_nev = getattr(config, 'BOT_NEV', 'Bot')

        most = datetime.now()
        datum = most.strftime("%Y-%m-%d")
        ido   = most.strftime("%H:%M:%S")

        tp_index = deal.get("tp_index", 0)
        tp_label = f"TP{tp_index + 1}"
        tp_ar    = deal.get("tp", "")

        ticket = deal.get("ticket", "")
        sor = [
            datum,                          # Dátum
            ido,                            # Idő
            bot_nev,                        # Bot
            deal.get("action", ""),         # Irány
            deal.get("entry_price", ""),    # Belépési ár
            deal.get("sl", ""),             # SL
            tp_label,                       # TP szint
            tp_ar,                          # TP ár
            deal.get("lot", ""),            # Lot
            deal.get("magic", ""),          # Magic
            deal.get("label", ""),          # Pozíció label
            deal.get("signal_id", ""),      # Signal ID
            str(ticket),                    # Ticket (azonosításhoz)
            "",                             # Zárási ár (majd záráskor)
            "",                             # Eredmény USD (majd záráskor)
            "",                             # Időtartam (majd záráskor)
            "Pending" if deal.get("is_pending") else "Nyitott",  # Státusz
            "",                             # Megjegyzés
        ]

        _sh_kereskedes.append_row(sor, value_input_option='USER_ENTERED')
        logger.info(f"Sheets: kereskedés naplózva (ticket={deal.get('ticket')})")

        # Közös összesített sheet-be is írunk ha be van állítva
        if _init_kozos_sheets():
            try:
                _kozos_sh_kereskedes.append_row(sor, value_input_option='USER_ENTERED')
                logger.info("Sheets: közös sheet-be is naplózva")
            except Exception as ke:
                logger.warning(f"Közös Sheets naplózás sikertelen: {ke}")

    except Exception as e:
        logger.error(f"Sheets log_trade hiba: {e}")


def log_trade_closed(ticket: int, zaras_ar: float, eredmeny_usd: float,
                     idotartam_perc: float, status: str = "TP"):
    """
    Pozíció záráskor hívódik — ticket alapján megkeresi és frissíti a sort.
    status: "TP", "SL", "Manuális", "Timeout"
    """
    if not _init_sheets():
        return

    try:
        sorok = _sh_kereskedes.get_all_values()
        ticket_str = str(ticket)
        talalt = False

        # Ticket oszlop index = 12 (0-tól számozva)
        TICKET_COL = 12
        ZARAS_COL  = 13  # N oszlop
        EREDM_COL  = 14  # O oszlop
        IDO_COL    = 15  # P oszlop
        STAT_COL   = 16  # Q oszlop

        for idx, sor in enumerate(sorok[1:], start=2):
            if len(sor) > TICKET_COL and str(sor[TICKET_COL]) == ticket_str:
                # Megtaláltuk — frissítjük a zárási adatokat
                nyitas_ido_str = f"{sor[0]} {sor[1]}" if len(sor) > 1 else ""

                _sh_kereskedes.update(
                    f"N{idx}:Q{idx}",
                    [[
                        zaras_ar,
                        round(eredmeny_usd, 2),
                        round(idotartam_perc, 1),
                        status,
                    ]],
                    value_input_option="USER_ENTERED"
                )
                talalt = True
                logger.info(f"Sheets: zárás frissítve (ticket={ticket}, eredmény={eredmeny_usd:.2f} USD, státusz={status})")

                # Közös sheet frissítése
                if _init_kozos_sheets():
                    try:
                        k_sorok = _kozos_sh_kereskedes.get_all_values()
                        for k_idx, k_sor in enumerate(k_sorok[1:], start=2):
                            if len(k_sor) > TICKET_COL and str(k_sor[TICKET_COL]) == ticket_str:
                                _kozos_sh_kereskedes.update(
                                    f"N{k_idx}:Q{k_idx}",
                                    [[zaras_ar, round(eredmeny_usd, 2), round(idotartam_perc, 1), status]],
                                    value_input_option="USER_ENTERED"
                                )
                                break
                    except Exception as ke:
                        logger.warning(f"Közös Sheets zárás frissítés sikertelen: {ke}")
                break

        if not talalt:
            logger.warning(f"Sheets: ticket #{ticket} nem található a táblázatban")

        _frissit_napi_bontas()

    except Exception as e:
        logger.error(f"Sheets log_trade_closed hiba: {e}")


def log_pending_result(ticket: int, status: str, megjegyzes: str = ""):
    """
    Pending megbízás eredményének naplózása.
    status: "Teljesült", "Timeout", "Törölt"
    """
    if not _init_sheets():
        return
    try:
        sorok = _sh_kereskedes.get_all_values()
        ticket_str = str(ticket)
        for idx, sor in enumerate(sorok[1:], start=2):
            if len(sor) > 12 and str(sor[12]) == ticket_str:
                _sh_kereskedes.update(
                    f"Q{idx}:R{idx}",
                    [[status, megjegyzes]],
                    value_input_option="USER_ENTERED"
                )
                logger.info(f"Sheets: pending frissítve #{ticket} → {status}")
                break
    except Exception as e:
        logger.error(f"Sheets log_pending_result hiba: {e}")


def log_skipped_signal(signal, reason: str):
    """
    Kihagyott jelzés naplózása — beírja a Kereskedések lapra státusz=Kihagyva.
    """
    if not _init_sheets():
        return

    try:
        import config
        bot_nev = getattr(config, 'BOT_NEV', 'Bot')

        most  = datetime.now()
        datum = most.strftime("%Y-%m-%d")
        ido   = most.strftime("%H:%M:%S")

        sor = [
            datum,
            ido,
            bot_nev,
            getattr(signal, 'action', ''),
            getattr(signal, 'entry_mid', ''),
            getattr(signal, 'sl', ''),
            "",   # TP szint
            "",   # TP ár
            "",   # Lot
            "",   # Magic
            "",   # Label
            "",   # Signal ID
            "",   # Zárási ár
            "",   # Eredmény
            "",   # Időtartam
            "Kihagyva",
            str(reason)[:200],  # Megjegyzés — ok
        ]

        _sh_kereskedes.append_row(sor, value_input_option='USER_ENTERED')
        logger.info(f"Sheets: kihagyott jelzés naplózva ({reason[:50]})")

        # Közös sheet
        if _init_kozos_sheets():
            try:
                _kozos_sh_kereskedes.append_row(sor, value_input_option='USER_ENTERED')
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Sheets log_skipped_signal hiba: {e}")


def frissit_statisztika():
    """
    Manuálisan frissíti a Statisztika lapot.
    A heartbeat hívhatja este 20:00-kor.
    """
    if not _init_sheets():
        return
    try:
        _frissit_napi_bontas()
        logger.info("Statisztika lap frissítve.")
    except Exception as e:
        logger.error(f"Statisztika frissítés hiba: {e}")
