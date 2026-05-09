"""
Beallitasi Varazslo - Ablakos verzio (tkinter)

A regi parancssoros setup.py helyett egy modern, ful-alapu GUI a bot
beallitasaihoz. Ugyanaz a user_settings.json az output, csak kenyelmesebb
a kitoltes.

Hasznalat: dupla klikk a setup.bat fajlra a bot mappajaban (vagy közvetlenül
            python setup.py paranccsal).

Funkciok:
  - Megnyitaskor betolti a meglevo user_settings.json-t (ha letezik)
  - Pozíciók: 1-5 dinamikus pozicio, ha-keret, lot/kockazat
  - Kockazat: mozgo SL, napi limit, max napi kereskedes
  - Entry: zona bovites + ACTIVE trigger
  - Szurok: 8 technikai szuro pipakkal, mindegyik egyedi parameter
  - Mentes: user_settings.json + .env frissites az ENTRY/ACTIVE valtozokhoz
"""

import os
import json
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext


# ── Konstansok ────────────────────────────────────────────────────────────────

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "user_settings.json")
ENV_FILE      = os.path.join(SCRIPT_DIR, ".env")
MAX_POZICIO   = 5

TP_OPCIOK = ["TP1", "TP2", "TP3", "TP4", "TP5", "TP6", "TP7"]
TIMEFRAME_OPCIOK = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]

# Szuro definiciok: kulcs, megjelenitendo nev, default timeframe, parameterek
# Parameter formatum: (kulcs, label, default, tipus, tip)
SZUROK = {
    "ema_sma": {
        "nev": "EMA/SMA Keresztezes",
        "leiras": "Trend irany meghatarozasa: BUY-nal EMA > SMA, SELL-nel EMA < SMA.",
        "default_tf": "H1",
        "parameterek": [
            ("ema_period", "EMA periodus", 7, "int", "Gyorsabb mozgoatlag periodusa."),
            ("sma_period", "SMA periodus", 10, "int", "Lassabb mozgoatlag periodusa."),
        ],
    },
    "macd": {
        "nev": "MACD + Signal",
        "leiras": "Trend + momentum megerositese. BUY-hoz MACD a Signal felett kell legyen.",
        "default_tf": "H1",
        "parameterek": [
            ("macd_fast", "Gyors EMA periodus", 12, "int", "MACD gyors periodus."),
            ("macd_slow", "Lassu EMA periodus", 26, "int", "MACD lassu periodus."),
            ("macd_signal", "Signal vonal periodus", 9, "int", "Signal vonal periodus."),
        ],
    },
    "rsi": {
        "nev": "RSI Szuro",
        "leiras": "Tulvett/tuladott zonak szurese. BUY csak ha RSI < BUY limit, SELL csak ha RSI > SELL limit.",
        "default_tf": "H1",
        "parameterek": [
            ("rsi_period", "RSI periodus", 14, "int", "Standard: 14."),
            ("rsi_buy_limit", "BUY limit (max RSI)", 65, "int", "BUY-hoz az RSI ennel kisebb kell legyen."),
            ("rsi_sell_limit", "SELL limit (min RSI)", 35, "int", "SELL-hez az RSI ennel nagyobb kell legyen."),
        ],
    },
    "bollinger": {
        "nev": "Bollinger Band",
        "leiras": "Volatilitas alapu szuro. BUY az also savhoz kozel, SELL a felsohoz.",
        "default_tf": "H1",
        "parameterek": [
            ("bb_period", "BB periodus", 20, "int", "Standard: 20."),
            ("bb_std", "Szoras szorzo", 2.0, "float", "Standard: 2.0."),
        ],
    },
    "atr_sl": {
        "nev": "ATR Dinamikus SL",
        "leiras": "Okosabb stop loss: az ATR (volatilitas) alapjan szamol SL-t. NEM szuro - csak SL beallitas.",
        "default_tf": "H1",
        "parameterek": [
            ("atr_period", "ATR periodus", 14, "int", "Standard: 14."),
            ("atr_multiplier", "ATR szorzo", 1.5, "float", "Pl. 1.5x ATR tavolsag az entry-tol."),
        ],
    },
    "candle": {
        "nev": "Gyertya Minta",
        "leiras": "Price action megerositese: pinbar, engulfing es hasonlo mintak felismerese.",
        "default_tf": "M15",
        "parameterek": [],
    },
    "adx": {
        "nev": "ADX Trend Ero",
        "leiras": "Csak eros trendben nyit poziciot. ADX alacsony = oldalazo piac, kihagyja.",
        "default_tf": "H1",
        "parameterek": [
            ("adx_period", "ADX periodus", 14, "int", "Standard: 14."),
            ("adx_min", "Minimum ADX ertek", 25, "int", "ADX>25: trendelo piac, ADX<20: oldalazas."),
        ],
    },
    "volume": {
        "nev": "Volume Szuro",
        "leiras": "Forgalom megerositese. Csak akkor lep be, ha az aktualis volume magas.",
        "default_tf": "H1",
        "parameterek": [
            ("volume_period", "Volume atlag periodus", 20, "int", "Hany gyertya atlagahoz hasonlit."),
            ("volume_multiplier", "Minimum szorzo", 1.3, "float", "Pl. 1.3x = 30%-kal magasabb az atlagnal."),
        ],
    },
}


# ── Meglevo settings betoltes ─────────────────────────────────────────────────

def betolt_settings():
    """Beolvassa a meglevo user_settings.json-t (ha letezik), vagy ures dict-tel ter vissza."""
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ── Fo ablak osztaly ──────────────────────────────────────────────────────────

class SetupApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Trading Bot - Beallitasi Varazslo")
        self.root.geometry("820x780")
        self.root.minsize(750, 600)

        # Meglevo beallitasok betoltese
        self.settings = betolt_settings()

        # Stilus
        try:
            ttk.Style().theme_use('vista')
        except:
            pass

        # ── Header ────────────────────────────────────────────────────────────
        header = tk.Frame(self.root, bg="#2c3e50", height=55)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="Bot Beallitasok",
            bg="#2c3e50", fg="white",
            font=("Segoe UI", 13, "bold")
        ).pack(pady=(7, 0))
        tk.Label(
            header, text="Pipald be amit hasznalsz, a kikapcsolt mezok inaktivak",
            bg="#2c3e50", fg="#bdc3c7",
            font=("Segoe UI", 9)
        ).pack()

        # ── Notebook (fulek) ──────────────────────────────────────────────────
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        self.fl_poziciok    = self._epit_fult("Poziciok")
        self.fl_kockazat    = self._epit_fult("Kockazat & SL")
        self.fl_entry       = self._epit_fult("Entry & ACTIVE")
        self.fl_szurok      = self._epit_fult("Szurok")

        # Tartalom feltoltese
        self._epit_poziciok_fult()
        self._epit_kockazat_fult()
        self._epit_entry_fult()
        self._epit_szurok_fult()

        # ── Lent gombok ───────────────────────────────────────────────────────
        gomb_frame = tk.Frame(self.root, bg="#ecf0f1", height=55)
        gomb_frame.pack(fill="x", side="bottom")
        gomb_frame.pack_propagate(False)

        # Bal oldalt: status
        if os.path.exists(SETTINGS_FILE):
            status = tk.Label(
                gomb_frame,
                text=f"Meglevo beallitasok betoltve: {os.path.basename(SETTINGS_FILE)}",
                bg="#ecf0f1", fg="#27ae60",
                font=("Segoe UI", 9)
            )
            status.pack(side="left", padx=15)
        else:
            status = tk.Label(
                gomb_frame,
                text="Nincs meg user_settings.json - elso beallitas",
                bg="#ecf0f1", fg="#7f8c8d",
                font=("Segoe UI", 9)
            )
            status.pack(side="left", padx=15)

        # Jobb oldalt: gombok
        tk.Button(
            gomb_frame, text="Megse", width=12,
            command=self.root.destroy,
            font=("Segoe UI", 10)
        ).pack(side="right", padx=(5, 15), pady=12)

        tk.Button(
            gomb_frame, text="Beallitasok mentese", width=22,
            command=self.mentes,
            bg="#27ae60", fg="white",
            font=("Segoe UI", 10, "bold"),
            activebackground="#229954", activeforeground="white"
        ).pack(side="right", pady=12)

    # ── Ful epitese (gorgetheto) ──────────────────────────────────────────────

    def _epit_fult(self, cim):
        """Letrehoz egy gorgetheto fulet a notebookban."""
        kulso = tk.Frame(self.notebook)
        self.notebook.add(kulso, text=cim)

        canvas = tk.Canvas(kulso, highlightthickness=0)
        scrollbar = ttk.Scrollbar(kulso, orient="vertical", command=canvas.yview)
        belso = tk.Frame(canvas, bg="white")

        belso.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=belso, anchor="nw", width=780)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Egergorgo
        def on_mousewheel(event):
            # Csak akkor gorgessuk, ha ez a ful aktiv
            if self.notebook.index(self.notebook.select()) == self.notebook.index(kulso):
                canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", on_mousewheel, add="+")

        return belso

    # ── 1. POZICIOK FUL ────────────────────────────────────────────────────────

    def _epit_poziciok_fult(self):
        f = self.fl_poziciok

        self._szakasz_cim(f, "Poziciok beallitasa",
                          "Hany kulonbozo TP-celhoz nyisson a bot kulonallo pozicikat? Maximum 5.")

        # Pozicio szam valaszto
        szam_keret = tk.Frame(f, bg="white")
        szam_keret.pack(fill="x", padx=20, pady=10)

        tk.Label(szam_keret, text="Aktiv poziciok szama:", bg="white",
                 font=("Segoe UI", 10, "bold")).pack(side="left")

        self.poz_szam_var = tk.IntVar(value=self.settings.get("POZICIO_SZAM", 1))

        for i in range(1, MAX_POZICIO + 1):
            tk.Radiobutton(
                szam_keret, text=str(i), variable=self.poz_szam_var,
                value=i, bg="white", command=self._frissit_pozicio_lathato
            ).pack(side="left", padx=8)

        # Lot mod kapcsolo (auto / manualis)
        lot_keret = tk.Frame(f, bg="white")
        lot_keret.pack(fill="x", padx=20, pady=(15, 5))

        self.auto_lot_var = tk.BooleanVar(value=self.settings.get("AUTO_LOT", False))
        tk.Checkbutton(
            lot_keret, text="Automata lot meretezes (kockazat % alapjan)",
            variable=self.auto_lot_var, bg="white",
            command=self._frissit_lot_oszlop,
            font=("Segoe UI", 10, "bold")
        ).pack(side="left")

        tk.Label(
            f, text="  Ha be van pipalva: a bot a szamlaegyenleg %-aban szamolja a lot meretet.\n"
            "  Ha nincs: te adod meg a fix lot meretet minden pozicioban.",
            bg="white", fg="#7f8c8d",
            font=("Segoe UI", 8), anchor="w", wraplength=720, justify="left"
        ).pack(fill="x", padx=20)

        # Pozicio sorok kontener
        self.poz_kontener = tk.Frame(f, bg="white")
        self.poz_kontener.pack(fill="x", padx=20, pady=(15, 20))

        # Tablazat fejlec
        fejlec = tk.Frame(self.poz_kontener, bg="#34495e")
        fejlec.pack(fill="x")
        tk.Label(fejlec, text="#",   bg="#34495e", fg="white", font=("Segoe UI", 9, "bold"),
                 width=4).grid(row=0, column=0, padx=2, pady=4)
        tk.Label(fejlec, text="TP cel", bg="#34495e", fg="white", font=("Segoe UI", 9, "bold"),
                 width=10).grid(row=0, column=1, padx=2, pady=4)
        tk.Label(fejlec, text="Magic szam", bg="#34495e", fg="white", font=("Segoe UI", 9, "bold"),
                 width=15).grid(row=0, column=2, padx=2, pady=4)
        self.lot_oszlop_label = tk.Label(fejlec, text="Lot meret", bg="#34495e", fg="white",
                                          font=("Segoe UI", 9, "bold"), width=15)
        self.lot_oszlop_label.grid(row=0, column=3, padx=2, pady=4)
        tk.Label(fejlec, text="", bg="#34495e", width=20).grid(row=0, column=4, padx=2, pady=4)

        # Pozicio sorok (mind az 5, kezdetben rejtett)
        self.poz_sorok = []
        self.poz_vars  = []

        # Eredeti ertekek a settings-bol
        eredeti_poziciok = self.settings.get("POZICIOK", [])
        defaults = [
            {"tp_index": 3, "magic": 11},
            {"tp_index": 5, "magic": 12},
            {"tp_index": 6, "magic": 13},
            {"tp_index": 7, "magic": 14},
            {"tp_index": 4, "magic": 15},
        ]

        for i in range(MAX_POZICIO):
            sor = tk.Frame(self.poz_kontener, bg="white")
            # Adatok eredetibol vagy default-bol
            if i < len(eredeti_poziciok):
                src = eredeti_poziciok[i]
            else:
                src = defaults[i]

            tk.Label(sor, text=f"{i+1}.", bg="white", font=("Segoe UI", 10),
                     width=4).grid(row=0, column=0, padx=2, pady=3)

            # TP cel combobox
            tp_var = tk.StringVar(value=f"TP{src.get('tp_index', defaults[i]['tp_index'])}")
            tp_combo = ttk.Combobox(sor, textvariable=tp_var, values=TP_OPCIOK,
                                    width=8, state="readonly", font=("Segoe UI", 10))
            tp_combo.grid(row=0, column=1, padx=2, pady=3)

            # Magic szam
            magic_var = tk.StringVar(value=str(src.get("magic", defaults[i]["magic"])))
            magic_entry = ttk.Entry(sor, textvariable=magic_var, width=13, font=("Segoe UI", 10))
            magic_entry.grid(row=0, column=2, padx=2, pady=3)

            # Lot / Kockazat (csere AUTO_LOT-tol fuggoen)
            lot_var = tk.StringVar(value=str(src.get("lot") if src.get("lot") else "0.01"))
            risk_var = tk.StringVar(value=str(src.get("risk_pct") if src.get("risk_pct") else "1.0"))

            ertek_var = tk.StringVar()
            # Kezdeti ertek a moduszhoz
            if self.settings.get("AUTO_LOT", False):
                ertek_var.set(risk_var.get())
            else:
                ertek_var.set(lot_var.get())

            ertek_entry = ttk.Entry(sor, textvariable=ertek_var, width=13, font=("Segoe UI", 10))
            ertek_entry.grid(row=0, column=3, padx=2, pady=3)

            self.poz_sorok.append(sor)
            self.poz_vars.append({
                "tp": tp_var,
                "magic": magic_var,
                "ertek": ertek_var,  # ez vagy lot vagy risk_pct, a moduszra figyelve
            })

        # Frissitsuk a megjeleneset
        self._frissit_pozicio_lathato()
        self._frissit_lot_oszlop()

    def _frissit_pozicio_lathato(self):
        """A poz_szam_var alapjan elrejti / megmutatja a sorokat."""
        szam = self.poz_szam_var.get()
        for i, sor in enumerate(self.poz_sorok):
            if i < szam:
                sor.pack(fill="x")
            else:
                sor.pack_forget()

    def _frissit_lot_oszlop(self):
        """A lot oszlop fejléc cseréje attol fuggoen, hogy AUTO_LOT vagy fix lot."""
        if self.auto_lot_var.get():
            self.lot_oszlop_label.config(text="Kockazat % (pl. 1.0)")
        else:
            self.lot_oszlop_label.config(text="Lot meret (pl. 0.01)")

    # ── 2. KOCKAZAT FUL ────────────────────────────────────────────────────────

    def _epit_kockazat_fult(self):
        f = self.fl_kockazat

        # Mozgo SL
        self._szakasz_cim(f, "Mozgo Stop Loss",
                          "Az SL automatikusan lep fel ahogy az ar eleri a TP szinteket.")

        self.mozgo_sl_var = tk.BooleanVar(value=self.settings.get("MOZGO_SL_ENABLED", False))
        self.sl_elso_tp_var = tk.StringVar(value=str(self.settings.get("SL_MOZGAS_ELSO_TP", 3)))

        sor = tk.Frame(f, bg="white")
        sor.pack(fill="x", padx=20, pady=10)

        cb = tk.Checkbutton(
            sor, text="Mozgo SL bekapcsolva",
            variable=self.mozgo_sl_var, bg="white",
            font=("Segoe UI", 10, "bold"),
            command=self._frissit_mozgo_sl_aktiv
        )
        cb.pack(side="left")

        tk.Label(sor, text="    SL emeles kezdo TP szint:", bg="white",
                 font=("Segoe UI", 9)).pack(side="left", padx=(20, 5))

        self.sl_elso_tp_entry = ttk.Entry(sor, textvariable=self.sl_elso_tp_var, width=6,
                                            font=("Segoe UI", 10))
        self.sl_elso_tp_entry.pack(side="left")

        tk.Label(
            f,
            text=("  Pl. ha 3-at adsz meg, TP3 elerese utan SL → entry, TP4-nel SL → TP1, TP5-nel SL → TP2.\n"
                  "  Figyelem: ha a csatornad max TP3-ig megy, jobb a fix SL!"),
            bg="white", fg="#7f8c8d",
            font=("Segoe UI", 8), anchor="w", wraplength=720, justify="left"
        ).pack(fill="x", padx=20)

        # Napi limit
        self._szakasz_cim(f, "Napi veszteseg limit",
                          "Ha a nap folyaman a bot ennyit veszit, leall es masnap reggel ujraindul.")

        self.napi_limit_var = tk.BooleanVar(value=self.settings.get("DAILY_LOSS_LIMIT_PCT", 0) > 0)
        self.napi_limit_pct_var = tk.StringVar(value=str(self.settings.get("DAILY_LOSS_LIMIT_PCT", 5.0) or 5.0))

        sor2 = tk.Frame(f, bg="white")
        sor2.pack(fill="x", padx=20, pady=10)

        cb2 = tk.Checkbutton(
            sor2, text="Napi veszteseg limit bekapcsolva",
            variable=self.napi_limit_var, bg="white",
            font=("Segoe UI", 10, "bold"),
            command=self._frissit_napi_limit_aktiv
        )
        cb2.pack(side="left")

        tk.Label(sor2, text="    Max veszteseg %:", bg="white",
                 font=("Segoe UI", 9)).pack(side="left", padx=(20, 5))

        self.napi_limit_entry = ttk.Entry(sor2, textvariable=self.napi_limit_pct_var,
                                            width=8, font=("Segoe UI", 10))
        self.napi_limit_entry.pack(side="left")

        tk.Label(
            f, text="  Pl. 5% = ha a napi veszteseg eleri az egyenleg 5%-at, leall a kereskedes.",
            bg="white", fg="#7f8c8d",
            font=("Segoe UI", 8), anchor="w", wraplength=720, justify="left"
        ).pack(fill="x", padx=20)

        # Max napi kereskedes
        self._szakasz_cim(f, "Napi maximum kereskedesek szama",
                          "Hany jelzes utan ne nyisson tobb poziciot a napon belul.")

        sor3 = tk.Frame(f, bg="white")
        sor3.pack(fill="x", padx=20, pady=10)

        self.max_napi_var = tk.StringVar(value=str(self.settings.get("MAX_NAPI_KERESKEDES", 0)))
        tk.Label(sor3, text="Max napi kereskedes:", bg="white",
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Entry(sor3, textvariable=self.max_napi_var, width=8,
                  font=("Segoe UI", 10)).pack(side="left", padx=10)
        tk.Label(sor3, text="(0 = korlatlan)", bg="white", fg="#7f8c8d",
                 font=("Segoe UI", 9)).pack(side="left")

        # Irany szuro
        self._szakasz_cim(f, "Azonos iranyu jelzes szuro",
                          "Egymas utani azonos iranyu jelzesek szurese az ujra-belepes elkerulesere.")

        sor4 = tk.Frame(f, bg="white")
        sor4.pack(fill="x", padx=20, pady=10)

        irany_default = self.settings.get("IRANY_SZURO_PERC", 10)
        self.irany_szuro_var = tk.BooleanVar(value=irany_default > 0)
        self.irany_szuro_perc_var = tk.StringVar(value=str(irany_default if irany_default > 0 else 10))

        cb4 = tk.Checkbutton(
            sor4, text="Irany szuro bekapcsolva",
            variable=self.irany_szuro_var, bg="white",
            font=("Segoe UI", 10, "bold"),
            command=self._frissit_irany_szuro_aktiv
        )
        cb4.pack(side="left")

        tk.Label(sor4, text="    Idoszak (perc):", bg="white",
                 font=("Segoe UI", 9)).pack(side="left", padx=(20, 5))

        self.irany_szuro_entry = ttk.Entry(sor4, textvariable=self.irany_szuro_perc_var,
                                            width=8, font=("Segoe UI", 10))
        self.irany_szuro_entry.pack(side="left")

        tk.Label(
            f, text="  Ha ennyi percen belul jott egy azonos iranyu jelzes, a kovetkezoket kihagyja.",
            bg="white", fg="#7f8c8d",
            font=("Segoe UI", 8), anchor="w", wraplength=720, justify="left"
        ).pack(fill="x", padx=20)

        # Frissitsuk az aktiv allapotokat
        self._frissit_mozgo_sl_aktiv()
        self._frissit_napi_limit_aktiv()
        self._frissit_irany_szuro_aktiv()

    def _frissit_mozgo_sl_aktiv(self):
        allapot = "normal" if self.mozgo_sl_var.get() else "disabled"
        self.sl_elso_tp_entry.config(state=allapot)

    def _frissit_napi_limit_aktiv(self):
        allapot = "normal" if self.napi_limit_var.get() else "disabled"
        self.napi_limit_entry.config(state=allapot)

    def _frissit_irany_szuro_aktiv(self):
        allapot = "normal" if self.irany_szuro_var.get() else "disabled"
        self.irany_szuro_entry.config(state=allapot)

    # ── 3. ENTRY & ACTIVE FUL ──────────────────────────────────────────────────

    def _epit_entry_fult(self):
        f = self.fl_entry

        # Entry zona bovites
        self._szakasz_cim(f, "Entry zona bovites",
                          "Ha mire a jel megerkezik az ar mar kicsuszott a belepesi zonabol, "
                          "ennyi USD-vel arrebb is lephessen be a bot. Aszimmetrikusan a kereskedes "
                          "iranyaba bovit (BUY-nal felfele, SELL-nel lefele).")

        sor = tk.Frame(f, bg="white")
        sor.pack(fill="x", padx=20, pady=10)

        self.entry_bov_var = tk.BooleanVar(
            value=self.settings.get("ENTRY_ZONA_BOVITES_ENABLED", False))
        self.entry_bov_usd_var = tk.StringVar(
            value=str(self.settings.get("ENTRY_ZONA_BOVITES_USD", 3.0) or 3.0))

        cb = tk.Checkbutton(
            sor, text="Entry zona bovites bekapcsolva",
            variable=self.entry_bov_var, bg="white",
            font=("Segoe UI", 10, "bold"),
            command=self._frissit_entry_bov_aktiv
        )
        cb.pack(side="left")

        tk.Label(sor, text="    Bovites:", bg="white",
                 font=("Segoe UI", 9)).pack(side="left", padx=(20, 5))

        self.entry_bov_entry = ttk.Entry(sor, textvariable=self.entry_bov_usd_var,
                                          width=8, font=("Segoe UI", 10))
        self.entry_bov_entry.pack(side="left")

        tk.Label(sor, text="USD", bg="white", fg="#7f8c8d",
                 font=("Segoe UI", 9)).pack(side="left", padx=(5, 0))

        # Tortenet perc
        sor2 = tk.Frame(f, bg="white")
        sor2.pack(fill="x", padx=20, pady=(0, 5))

        tk.Label(sor2, text="    Visszanezendo ido:", bg="white",
                 font=("Segoe UI", 9)).pack(side="left")

        self.entry_tortenet_var = tk.StringVar(
            value=str(self.settings.get("ENTRY_ZONA_TORTENET_PERC", 5)))
        self.entry_tortenet_entry = ttk.Entry(sor2, textvariable=self.entry_tortenet_var,
                                                width=8, font=("Segoe UI", 10))
        self.entry_tortenet_entry.pack(side="left", padx=(28, 5))

        tk.Label(sor2, text="perc", bg="white", fg="#7f8c8d",
                 font=("Segoe UI", 9)).pack(side="left")

        tk.Label(
            f, text=("  A bovites csak akkor ervenyes, ha az ar az utolso X percben jart az eredeti zonaban.\n"
                     "  Igy kiszurodnek azok a jelek, ahol az ar sosem volt ott (pl. nagy hir miatt elment)."),
            bg="white", fg="#7f8c8d",
            font=("Segoe UI", 8), anchor="w", wraplength=720, justify="left"
        ).pack(fill="x", padx=20)

        # ACTIVE trigger
        self._szakasz_cim(f, "ACTIVE trigger",
                          "Ha a csoportban kesobb erkezik egy 'Active ✅✅' uzenet, a meg nyitott "
                          "pending megbizasokat azonnal piaci aron belepteti. A maximum csuszas "
                          "megakadalyozza, hogy tul rossz aron lepjen be ha az ar mar elment.")

        sor3 = tk.Frame(f, bg="white")
        sor3.pack(fill="x", padx=20, pady=10)

        self.active_var = tk.BooleanVar(value=self.settings.get("ACTIVE_TRIGGER_ENABLED", True))
        self.active_slip_var = tk.StringVar(
            value=str(self.settings.get("ACTIVE_TRIGGER_MAX_SLIPPAGE_USD", 5.0)))

        cb3 = tk.Checkbutton(
            sor3, text="ACTIVE trigger bekapcsolva",
            variable=self.active_var, bg="white",
            font=("Segoe UI", 10, "bold"),
            command=self._frissit_active_aktiv
        )
        cb3.pack(side="left")

        tk.Label(sor3, text="    Max csuszas:", bg="white",
                 font=("Segoe UI", 9)).pack(side="left", padx=(20, 5))

        self.active_slip_entry = ttk.Entry(sor3, textvariable=self.active_slip_var,
                                            width=8, font=("Segoe UI", 10))
        self.active_slip_entry.pack(side="left")

        tk.Label(sor3, text="USD", bg="white", fg="#7f8c8d",
                 font=("Segoe UI", 9)).pack(side="left", padx=(5, 0))

        tk.Label(
            f, text=("  Ha az aktualis ar ennyi USD-nel tovabb csuszott a 'rossz' iranyba, a bot kihagyja\n"
                     "  a beleptetest. BUY-nal: ar > entry tetejenel + slippage. SELL-nel: ar < entry alja - slippage."),
            bg="white", fg="#7f8c8d",
            font=("Segoe UI", 8), anchor="w", wraplength=720, justify="left"
        ).pack(fill="x", padx=20)

        # Frissites
        self._frissit_entry_bov_aktiv()
        self._frissit_active_aktiv()

    def _frissit_entry_bov_aktiv(self):
        allapot = "normal" if self.entry_bov_var.get() else "disabled"
        self.entry_bov_entry.config(state=allapot)
        self.entry_tortenet_entry.config(state=allapot)

    def _frissit_active_aktiv(self):
        allapot = "normal" if self.active_var.get() else "disabled"
        self.active_slip_entry.config(state=allapot)

    # ── 4. SZUROK FUL ──────────────────────────────────────────────────────────

    def _epit_szurok_fult(self):
        f = self.fl_szurok

        self._szakasz_cim(f, "Technikai szurok",
                          "A szurok a Telegram jelzest indikatorokkal erositik meg. "
                          "Ha a szuro nem teljesul, a bot NEM nyit poziciot.")

        # Entry tolerancia (szurok hasznalatakor)
        tol_keret = tk.Frame(f, bg="#fffbe5", relief="solid", bd=1)
        tol_keret.pack(fill="x", padx=20, pady=(5, 15))

        tk.Label(
            tol_keret, text="Entry zona tolerancia (csak ha legalabb 1 szuro aktiv)",
            bg="#fffbe5", font=("Segoe UI", 9, "bold"), anchor="w"
        ).pack(fill="x", padx=10, pady=(8, 2))

        tol_sor = tk.Frame(tol_keret, bg="#fffbe5")
        tol_sor.pack(fill="x", padx=10, pady=(0, 8))

        tk.Label(tol_sor, text="Tolerancia:", bg="#fffbe5",
                 font=("Segoe UI", 9)).pack(side="left")

        # Az aktualis szuro_config-bol vagy default
        szuro_cfg = self.settings.get("SZURO_CONFIG", {}) or {}
        self.entry_tol_var = tk.StringVar(value=str(szuro_cfg.get("entry_tolerancia_usd", 0.0)))

        ttk.Entry(tol_sor, textvariable=self.entry_tol_var, width=8,
                  font=("Segoe UI", 10)).pack(side="left", padx=(8, 5))
        tk.Label(tol_sor, text="USD (0 = csak pontos zonaban)", bg="#fffbe5",
                 fg="#7f8c8d", font=("Segoe UI", 8)).pack(side="left")

        # Szurok listaja
        self.szuro_vars = {}  # {key: {"aktiv": BooleanVar, "tf": StringVar, "params": {param_key: StringVar}}}

        aktiv_szurok = self.settings.get("AKTIV_SZUROK", []) or []

        for kulcs, info in SZUROK.items():
            keret = tk.Frame(f, bg="white", relief="solid", bd=1)
            keret.pack(fill="x", padx=20, pady=4)

            # Fejlec sor
            fej_sor = tk.Frame(keret, bg="white")
            fej_sor.pack(fill="x", padx=8, pady=(8, 2))

            aktiv_var = tk.BooleanVar(value=(kulcs in aktiv_szurok))
            tf_var = tk.StringVar(value=szuro_cfg.get(f"{kulcs}_tf", info["default_tf"]))

            cb = tk.Checkbutton(
                fej_sor, variable=aktiv_var, bg="white",
                font=("Segoe UI", 10, "bold")
            )
            cb.pack(side="left")

            tk.Label(fej_sor, text=info["nev"], bg="white",
                     font=("Segoe UI", 10, "bold")).pack(side="left", padx=(2, 20))

            tk.Label(fej_sor, text="Idokeret:", bg="white",
                     font=("Segoe UI", 9)).pack(side="left", padx=(0, 5))

            tf_combo = ttk.Combobox(fej_sor, textvariable=tf_var, values=TIMEFRAME_OPCIOK,
                                     width=6, state="readonly", font=("Segoe UI", 9))
            tf_combo.pack(side="left")

            # Leiras
            tk.Label(
                keret, text="  " + info["leiras"], bg="white", fg="#7f8c8d",
                font=("Segoe UI", 8), anchor="w", wraplength=720, justify="left"
            ).pack(fill="x", padx=8)

            # Parameter sorok
            param_vars = {}
            param_widgets = []

            if info["parameterek"]:
                param_keret = tk.Frame(keret, bg="white")
                param_keret.pack(fill="x", padx=20, pady=(4, 8))

                for p_kulcs, p_label, p_default, p_tipus, p_tip in info["parameterek"]:
                    p_sor = tk.Frame(param_keret, bg="white")
                    p_sor.pack(fill="x", pady=2)

                    p_label_w = tk.Label(p_sor, text=p_label + ":", bg="white",
                                          font=("Segoe UI", 9), width=22, anchor="w")
                    p_label_w.pack(side="left")

                    aktualis_ertek = szuro_cfg.get(p_kulcs, p_default)
                    p_var = tk.StringVar(value=str(aktualis_ertek))
                    p_entry = ttk.Entry(p_sor, textvariable=p_var, width=10,
                                         font=("Segoe UI", 10))
                    p_entry.pack(side="left", padx=(0, 8))

                    p_tip_w = tk.Label(p_sor, text=p_tip, bg="white", fg="#95a5a6",
                                        font=("Segoe UI", 8), anchor="w")
                    p_tip_w.pack(side="left", fill="x", expand=True)

                    param_vars[p_kulcs] = p_var
                    param_widgets.extend([p_label_w, p_entry, p_tip_w, tf_combo])
            else:
                # Padding ha nincs parameter
                tk.Frame(keret, bg="white", height=8).pack()

            # Aktiv allapot frissites callback
            def make_callback(av, widgets, tfc):
                def cb_func(*args):
                    allapot = "normal" if av.get() else "disabled"
                    for w in widgets:
                        if isinstance(w, ttk.Entry):
                            w.config(state=allapot)
                        elif isinstance(w, ttk.Combobox):
                            w.config(state="readonly" if av.get() else "disabled")
                        elif isinstance(w, tk.Label):
                            if "fg" in w.config():
                                w.config(fg="#7f8c8d" if av.get() else "#bdc3c7")
                    # tf is csere
                    tfc.config(state="readonly" if av.get() else "disabled")
                return cb_func

            cb_func = make_callback(aktiv_var, param_widgets, tf_combo)
            aktiv_var.trace_add("write", cb_func)
            cb_func()  # Kezdeti allapot

            self.szuro_vars[kulcs] = {
                "aktiv": aktiv_var,
                "tf": tf_var,
                "params": param_vars,
            }

    # ── Cim helper ─────────────────────────────────────────────────────────────

    def _szakasz_cim(self, parent, cim, leiras):
        """Szakaszcim leirassal - sotet hatter."""
        keret = tk.Frame(parent, bg="#34495e")
        keret.pack(fill="x", pady=(15, 5))
        tk.Label(
            keret, text=cim, bg="#34495e", fg="white",
            font=("Segoe UI", 10, "bold"), anchor="w"
        ).pack(fill="x", padx=15, pady=(8, 2))
        tk.Label(
            keret, text=leiras, bg="#34495e", fg="#bdc3c7",
            font=("Segoe UI", 8), anchor="w", wraplength=750, justify="left"
        ).pack(fill="x", padx=15, pady=(0, 8))

    # ── Mentes ─────────────────────────────────────────────────────────────────

    def mentes(self):
        """Validal es elment a beallitasokat user_settings.json-ba."""
        try:
            settings = self._osszeallit_settings()
        except ValueError as e:
            messagebox.showerror("Hibas adat", str(e))
            return

        # Ellenorizzuk a duplikalt magic szamokat
        magic_lista = [p["magic"] for p in settings["POZICIOK"]]
        if len(magic_lista) != len(set(magic_lista)):
            messagebox.showerror(
                "Duplikalt magic szam",
                "Tobb pozicioban ugyanaz a magic szam szerepel!\n\n"
                "Minden pozicionak EGYEDI magic numbernek kell lennie."
            )
            return

        # Mentes JSON-ba
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("Mentes hiba", f"Nem sikerult menteni:\n{e}")
            return

        # .env frissites az ENTRY/ACTIVE/IRANY ertekekhez (ha letezik a .env)
        self._env_frissites(settings)

        messagebox.showinfo(
            "Sikeres mentes",
            f"Beallitasok elmentve!\n\n"
            f"Fajl: {SETTINGS_FILE}\n\n"
            f"Most mar inditsd ujra a botot, hogy az uj beallitasokkal indulhasson el."
        )
        self.root.destroy()

    def _osszeallit_settings(self):
        """Felepiti a settings dict-et a form mezokbol. ValueError-t dob hiba eseten."""
        s = {}

        # Poziciok
        szam = self.poz_szam_var.get()
        s["POZICIO_SZAM"] = szam
        s["AUTO_LOT"] = self.auto_lot_var.get()

        poziciok = []
        for i in range(szam):
            v = self.poz_vars[i]
            tp_str = v["tp"].get()
            tp_idx = int(tp_str.replace("TP", ""))

            try:
                magic = int(v["magic"].get())
            except ValueError:
                raise ValueError(f"{i+1}. pozicio: a magic szamnak egesz szamnak kell lennie.")

            try:
                ertek = float(v["ertek"].get())
            except ValueError:
                cim = "kockazat %" if s["AUTO_LOT"] else "lot meret"
                raise ValueError(f"{i+1}. pozicio: a {cim} szamnak kell lennie.")

            p = {
                "tp_index": tp_idx,
                "magic": magic,
                "label": f"TP{tp_idx}-fix",
            }
            if s["AUTO_LOT"]:
                p["risk_pct"] = ertek
                p["lot"] = None
            else:
                p["lot"] = ertek
                p["risk_pct"] = None
            poziciok.append(p)

        s["POZICIOK"] = poziciok

        # Mozgo SL
        s["MOZGO_SL_ENABLED"] = self.mozgo_sl_var.get()
        try:
            s["SL_MOZGAS_ELSO_TP"] = int(self.sl_elso_tp_var.get())
        except ValueError:
            s["SL_MOZGAS_ELSO_TP"] = 3

        # Napi limit
        if self.napi_limit_var.get():
            try:
                s["DAILY_LOSS_LIMIT_PCT"] = float(self.napi_limit_pct_var.get())
            except ValueError:
                raise ValueError("Napi limit %: szamnak kell lennie.")
        else:
            s["DAILY_LOSS_LIMIT_PCT"] = 0.0

        # Max napi
        try:
            s["MAX_NAPI_KERESKEDES"] = int(self.max_napi_var.get())
        except ValueError:
            s["MAX_NAPI_KERESKEDES"] = 0

        # Trade hours - alapertek
        s["TRADE_HOURS_ENABLED"] = self.settings.get("TRADE_HOURS_ENABLED", False)
        s["TRADE_HOUR_START"]    = self.settings.get("TRADE_HOUR_START", 0)
        s["TRADE_HOUR_END"]      = self.settings.get("TRADE_HOUR_END", 24)

        # Irany szuro
        if self.irany_szuro_var.get():
            try:
                s["IRANY_SZURO_PERC"] = int(self.irany_szuro_perc_var.get())
            except ValueError:
                s["IRANY_SZURO_PERC"] = 0
        else:
            s["IRANY_SZURO_PERC"] = 0

        # Entry zona
        s["ENTRY_ZONA_BOVITES_ENABLED"] = self.entry_bov_var.get()
        if self.entry_bov_var.get():
            try:
                s["ENTRY_ZONA_BOVITES_USD"] = float(self.entry_bov_usd_var.get())
            except ValueError:
                raise ValueError("Entry zona bovites USD: szamnak kell lennie.")
            try:
                s["ENTRY_ZONA_TORTENET_PERC"] = int(self.entry_tortenet_var.get())
            except ValueError:
                s["ENTRY_ZONA_TORTENET_PERC"] = 5
        else:
            s["ENTRY_ZONA_BOVITES_USD"] = 0.0
            s["ENTRY_ZONA_TORTENET_PERC"] = 5

        # ACTIVE
        s["ACTIVE_TRIGGER_ENABLED"] = self.active_var.get()
        try:
            s["ACTIVE_TRIGGER_MAX_SLIPPAGE_USD"] = float(self.active_slip_var.get())
        except ValueError:
            s["ACTIVE_TRIGGER_MAX_SLIPPAGE_USD"] = 5.0

        # Szurok
        aktiv_szurok = []
        szuro_cfg = {}

        for kulcs, vars_dict in self.szuro_vars.items():
            if vars_dict["aktiv"].get():
                aktiv_szurok.append(kulcs)
                szuro_cfg[f"{kulcs}_tf"] = vars_dict["tf"].get()

                # Parameterek
                for p_kulcs, p_var in vars_dict["params"].items():
                    info = SZUROK[kulcs]
                    p_def = next((p for p in info["parameterek"] if p[0] == p_kulcs), None)
                    if p_def:
                        p_tipus = p_def[3]
                        try:
                            if p_tipus == "int":
                                szuro_cfg[p_kulcs] = int(p_var.get())
                            elif p_tipus == "float":
                                szuro_cfg[p_kulcs] = float(p_var.get())
                            else:
                                szuro_cfg[p_kulcs] = p_var.get()
                        except ValueError:
                            raise ValueError(f"Szuro {info['nev']} - {p_def[1]}: hibas szam.")

        # Entry tolerancia (csak ha van szuro aktiv)
        if aktiv_szurok:
            try:
                szuro_cfg["entry_tolerancia_usd"] = float(self.entry_tol_var.get())
            except ValueError:
                szuro_cfg["entry_tolerancia_usd"] = 0.0

        s["AKTIV_SZUROK"] = aktiv_szurok
        s["SZURO_CONFIG"] = szuro_cfg

        return s

    def _env_frissites(self, settings):
        """Frissiti a .env fajlt a kulcsfontossagu valtozokkal (ENTRY, ACTIVE, IRANY)."""
        if not os.path.exists(ENV_FILE):
            return  # Nincs .env, nem teszunk semmit

        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                env_lines = f.readlines()

            # Felulirando ertekek
            updates = {
                "ENTRY_ZONA_BOVITES_ENABLED": str(settings["ENTRY_ZONA_BOVITES_ENABLED"]),
                "ENTRY_ZONA_BOVITES_USD":     str(settings["ENTRY_ZONA_BOVITES_USD"]),
                "ENTRY_ZONA_TORTENET_PERC":   str(settings["ENTRY_ZONA_TORTENET_PERC"]),
                "ACTIVE_TRIGGER_ENABLED":     str(settings["ACTIVE_TRIGGER_ENABLED"]),
                "ACTIVE_TRIGGER_MAX_SLIPPAGE_USD": str(settings["ACTIVE_TRIGGER_MAX_SLIPPAGE_USD"]),
                "IRANY_SZURO_PERC":           str(settings["IRANY_SZURO_PERC"]),
            }

            updated_keys = set()
            new_lines = []
            for line in env_lines:
                # Komment vagy ures sor
                if not line.strip() or line.strip().startswith("#"):
                    new_lines.append(line)
                    continue
                if "=" not in line:
                    new_lines.append(line)
                    continue
                kulcs = line.split("=", 1)[0].strip()
                if kulcs in updates:
                    new_lines.append(f"{kulcs}={updates[kulcs]}\n")
                    updated_keys.add(kulcs)
                else:
                    new_lines.append(line)

            # Hozzafuzzuk a hianyzo kulcsokat
            for kulcs, ertek in updates.items():
                if kulcs not in updated_keys:
                    new_lines.append(f"{kulcs}={ertek}\n")

            with open(ENV_FILE, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

        except Exception as e:
            # Ha nem sikerult a .env frissites, nem hibazunk - csak figyelmeztetunk
            print(f"Figyelmeztetes: a .env frissitese sikertelen: {e}")


# ── Program inditas ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    app = SetupApp(root)
    root.mainloop()
