# ============================================================
#  FELHASZNÁLÓI BEÁLLÍTÁSOK (setup.py által generált)
#  Ha létezik user_settings.json, felülírja a fenti értékeket
# ============================================================

import json as _json

_settings_file = os.path.join(os.path.dirname(__file__), "user_settings.json")
if os.path.exists(_settings_file):
    try:
        with open(_settings_file, "r", encoding="utf-8") as _f:
            _s = _json.load(_f)

        # ── ÚJ: dinamikus pozíció lista ──────────────────────────────────────
        # Ha a setup.py az új formátumot mentette (POZICIOK lista),
        # akkor ebből építjük fel a POS1/2/3... változókat dinamikusan.
        if "POZICIOK" in _s:
            _poziciok = _s["POZICIOK"]
            POZICIO_SZAM = len(_poziciok)

            # Minden pozícióhoz létrehozzuk a POS{N}_* változókat
            for _i, _p in enumerate(_poziciok, 1):
                globals()[f"POS{_i}_ENABLED"]  = True
                globals()[f"POS{_i}_TP_INDEX"] = _p["tp_index"]
                globals()[f"POS{_i}_MAGIC"]    = _p["magic"]
                globals()[f"POS{_i}_LABEL"]    = _p.get("label", f"TP{_p['tp_index']}-fix")
                globals()[f"POS{_i}_LOT"]      = _p.get("lot") or globals().get(f"POS{_i}_LOT", 0.01)
                globals()[f"POS{_i}_RISK_PCT"] = _p.get("risk_pct") or 1.0

            # Ha kevesebb pozíció van mint az alapértelmezett 3,
            # a maradék POS változókat kikapcsoljuk
            for _j in range(len(_poziciok) + 1, 6):
                globals()[f"POS{_j}_ENABLED"] = False

        else:
            # ── RÉGI formátum visszafelé kompatibilitás ───────────────────────
            if "POS1_ENABLED" in _s: POS1_ENABLED = _s["POS1_ENABLED"]
            if "POS2_ENABLED" in _s: POS2_ENABLED = _s["POS2_ENABLED"]
            if "POS3_ENABLED" in _s: POS3_ENABLED = _s["POS3_ENABLED"]

            if _s.get("POS1_LOT") is not None: POS1_LOT = _s["POS1_LOT"]
            if _s.get("POS2_LOT") is not None: POS2_LOT = _s["POS2_LOT"]
            if _s.get("POS3_LOT") is not None: POS3_LOT = _s["POS3_LOT"]

            POZICIO_SZAM = sum([
                1 if _s.get("POS1_ENABLED", POS1_ENABLED) else 0,
                1 if _s.get("POS2_ENABLED", POS2_ENABLED) else 0,
                1 if _s.get("POS3_ENABLED", POS3_ENABLED) else 0,
            ])

        # ── Közös beállítások (mindkét formátumnál) ───────────────────────────
        AUTO_LOT             = _s.get("AUTO_LOT", False)
        MOZGO_SL_ENABLED     = _s.get("MOZGO_SL_ENABLED", MOZGO_SL_ENABLED)
        SL_MOZGAS_ELSO_TP    = _s.get("SL_MOZGAS_ELSO_TP", 3)
        MAX_NAPI_KERESKEDES  = _s.get("MAX_NAPI_KERESKEDES", 0)
        DAILY_LOSS_LIMIT_PCT = _s.get("DAILY_LOSS_LIMIT_PCT", 0.0)
        TRADE_HOURS_ENABLED  = _s.get("TRADE_HOURS_ENABLED", False)
        TRADE_HOUR_START     = _s.get("TRADE_HOUR_START", 0)
        TRADE_HOUR_END       = _s.get("TRADE_HOUR_END", 24)
        AKTIV_SZUROK         = _s.get("AKTIV_SZUROK", [])
        SZURO_CONFIG         = _s.get("SZURO_CONFIG", {})

    except Exception as _e:
        print(f"⚠️ user_settings.json betöltési hiba: {_e} — alapértelmezett értékek használva")
        AUTO_LOT             = False
        DAILY_LOSS_LIMIT_PCT = 0.0
        TRADE_HOURS_ENABLED  = False
        TRADE_HOUR_START     = 0
        TRADE_HOUR_END       = 24
        MAX_NAPI_KERESKEDES  = 0
        POZICIO_SZAM         = 3
else:
    # Ha nincs user_settings.json, alapértelmezett értékek
    AUTO_LOT             = False
    DAILY_LOSS_LIMIT_PCT = 0.0
    TRADE_HOURS_ENABLED  = False
    TRADE_HOUR_START     = 0
    TRADE_HOUR_END       = 24
    MAX_NAPI_KERESKEDES  = 0
    SL_MOZGAS_ELSO_TP    = 3
    POZICIO_SZAM         = 3
