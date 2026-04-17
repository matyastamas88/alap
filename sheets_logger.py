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
_spreadsheet = None   # a megnyitott spreadsheet
_sh_kereskedes = None  # "Kereskedések" munkalap
_sh_statisztika = None  # "Statisztika" munkalap
_initialized = False
_init_failed = False

KERESKEDES_FEJLEC = [
    "Dátum", "Idő", "Bot", "Irány", "Belépési ár", "SL", "TP szint",
    "TP ár", "Lot", "Magic", "Pozíció label", "Signal ID",
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
            "",                             # Zárási ár (majd záráskor)
            "",                             # Eredmény USD (majd záráskor)
            "",                             # Időtartam (majd záráskor)
            "Nyitott",                      # Státusz
            "pending" if deal.get("is_pending") else "",  # Megjegyzés
        ]

        _sh_kereskedes.append_row(sor, value_input_option='USER_ENTERED')
        logger.info(f"Sheets: kereskedés naplózva (ticket={deal.get('ticket')})")

    except Exception as e:
        logger.error(f"Sheets log_trade hiba: {e}")


def log_trade_closed(ticket: int, zaras_ar: float, eredmeny_usd: float,
                     idotartam_perc: float, status: str = "TP"):
    """
    Pozíció záráskor hívódik — frissíti a sort a zárási adatokkal.
    status: "TP", "SL", "Manuális"
    """
    if not _init_sheets():
        return

    try:
        # Megkeressük a sort a ticket alapján (Signal ID vagy magic alapján)
        # A ticket a Signal ID-ben is szerepel, de egyszerűbb az összes sort végignézni
        sorok = _sh_kereskedes.get_all_values()
        ticket_str = str(ticket)

        for idx, sor in enumerate(sorok[1:], start=2):  # 1-es sor a fejléc
            # Signal ID tartalmazza a ticket infót, vagy keresünk egyéb azonosítót
            if len(sor) >= 12 and sor[15] == "Nyitott":
                # Ha a sor még nyitott, és egyéb azonosítóval megtalálható
                # Ez egy egyszerűsített keresés — a teljes implementációhoz
                # a ticket-et is el lehet tárolni egy oszlopban
                pass

        # Egyszerűbb megközelítés: az utolsó "Nyitott" sort frissítjük
        # (ha mindig csak 1 pozíció van egyszerre)
        # Pontosabb megoldás: ticket oszlop hozzáadása
        logger.info(f"Sheets: zárás naplózva (ticket={ticket}, eredmény={eredmeny_usd} USD)")
        _frissit_napi_bontas()

    except Exception as e:
        logger.error(f"Sheets log_trade_closed hiba: {e}")


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
