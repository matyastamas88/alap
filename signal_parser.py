"""
Signal Parser — Telegram kereskedési jelzések értelmezése

Támogatott formátumok:
  1. "#Sell #XAUUSD at 4870/4873" + "Tp1 4867" + "Sl 4885"
  2. "BUY GOLD @4866" + "TP1 -- 4870" + "SL -- PREMIUM" (ha DEFAULT_SL_USD be van állítva)
  3. "GOLD BUY AT CMP :- 4792 - 4788" + "TP :- 4797" + "SL :- 4785"
  4. "GOLD BUY NEAR :- 4793 - 4790" + "SL :- 4788"
  5. "SELL XAUUSD\nEntry Price\n4840/4835" + "TP1: 4834" + "SL: 4848"

Szimbólum szűrés: csak GOLD / XAUUSD / XAU jelzéseket dolgoz fel.
Minden mást (EURUSD, BTCUSD stb.) figyelmen kívül hagy.

DEFAULT_SL_USD (config/env-ből):
  - Ha 0: a jelzésben muszáj lennie SL-nek, különben kihagyja
  - Ha > 0: ha nincs SL a jelzésben, entry ± DEFAULT_SL_USD lesz az SL
"""

import re
import logging

logger = logging.getLogger(__name__)

# Támogatott szimbólumok
VALID_SYMBOLS = ["GOLD", "XAUUSD", "XAU"]


class TradeSignal:
    def __init__(self, action, entry_low, entry_high, tp_levels, sl, raw_text, sl_was_auto=False):
        self.action       = action
        self.entry_low    = entry_low
        self.entry_high   = entry_high
        self.tp_levels    = tp_levels
        self.sl           = sl
        self.raw_text     = raw_text
        self.sl_was_auto  = sl_was_auto  # True ha DEFAULT_SL_USD által generált

    @property
    def entry_mid(self):
        return round((self.entry_low + self.entry_high) / 2, 2)

    @property
    def tp1(self):
        return self.tp_levels[0] if self.tp_levels else None

    def __str__(self):
        tps = " | ".join([f"TP{i+1}: {v}" for i, v in enumerate(self.tp_levels)])
        auto = " (AUTO)" if self.sl_was_auto else ""
        return (f"📊 {self.action} XAUUSD\n"
                f"Entry: {self.entry_low}-{self.entry_high}\n"
                f"{tps}\nSL: {self.sl}{auto}")


def parse_signal(text: str, default_sl_usd: float = 0) -> TradeSignal | None:
    """
    Jelzés értelmezése.

    Args:
        text: A Telegram üzenet szövege
        default_sl_usd: Ha > 0 és nincs SL a jelzésben, ez a távolság lesz az SL
                        (pl. 20 → entry ± 20 USD)

    Returns:
        TradeSignal objektum vagy None ha nem értelmezhető
    """
    if not text:
        return None
    text_upper = text.upper()

    # ── HIGH RISK szűrő — ha a jelzés tartalmazza, kihagyja ──────────────────
    if "HIGH RISK" in text_upper:
        logger.info("HIGH RISK jelzés — kihagyva (config: HIGH_RISK_SKIP=True)")
        return None

    # ── Irány ─────────────────────────────────────────────────────────────────
    if "BUY" in text_upper:
        action = "BUY"
    elif "SELL" in text_upper:
        action = "SELL"
    else:
        return None

    # ── Szimbólum szűrés — csak GOLD/XAUUSD/XAU ───────────────────────────────
    has_valid_symbol = any(sym in text_upper for sym in VALID_SYMBOLS)
    if not has_valid_symbol:
        logger.info("Jelzés nem GOLD/XAUUSD — kihagyva")
        return None

    # ── Entry tartomány ──────────────────────────────────────────────────────
    entry_low = entry_high = None

    range_patterns = [
        # "SELL ZONE : 4796 - 4799" vagy "BUY ZONE : 4793 - 4790"
        r'(?:BUY|SELL)\s+ZONE\s*[:\-]*\s*(\d{3,5}(?:\.\d+)?)\s*[-]\s*(\d{3,5}(?:\.\d+)?)',
        # "4793 - 4790 BUY ZONE" vagy "4797 - 4799 SELL ZONE" — ELŐRE kell!
        r'(\d{3,5}(?:\.\d+)?)\s*[-]\s*(\d{3,5}(?:\.\d+)?)\s+(?:BUY|SELL)\s+ZONE',
        # "AT CMP :- 4792 - 4788" (kettőspont-kötőjel)
        r'at\s+cmp\s*[:\-]*\s*(\d{3,5}(?:\.\d+)?)\s*[\/\-]+\s*(\d{3,5}(?:\.\d+)?)',
        # "NEAR :- 4793 - 4790"
        r'near\s*[:\-]+\s*(\d{3,5}(?:\.\d+)?)\s*[\/\-]+\s*(\d{3,5}(?:\.\d+)?)',
        # "at 4870/4873"
        r'\bat\s+(\d{3,5}(?:\.\d+)?)\s*[\/\-]\s*(\d{3,5}(?:\.\d+)?)',
        # "@4780-4775"
        r'@\s*(\d{3,5}(?:\.\d+)?)\s*[\/\-]\s*(\d{3,5}(?:\.\d+)?)',
        # "Entry: 4840/4835"
        r'entry[\w\s:\.\-]{0,20}?\n?\s*(\d{3,5}(?:\.\d+)?)\s*[\/\-]\s*(\d{3,5}(?:\.\d+)?)',
        # "BUY XAUUSD 4870/4873"
        r'(?:BUY|SELL)(?:\s+(?:XAUUSD|GOLD|XAU))?\s*[:@]?\s*(\d{3,5}(?:\.\d+)?)\s*[\/\-]\s*(\d{3,5}(?:\.\d+)?)',
    ]

    range_match = None
    for pattern in range_patterns:
        range_match = re.search(pattern, text, re.IGNORECASE)
        if range_match:
            break

    if range_match:
        a, b = float(range_match.group(1)), float(range_match.group(2))
        entry_low, entry_high = min(a, b), max(a, b)
    else:
        # Egyetlen entry ár
        single_patterns = [
            r'@\s*(\d{3,5}(?:\.\d+)?)',
            r'at\s+cmp\s*[:\-]*\s*(\d{3,5}(?:\.\d+)?)',
            r'near\s*[:\-]+\s*(\d{3,5}(?:\.\d+)?)',
            r'\bat\s+(\d{3,5}(?:\.\d+)?)',
            r'entry[\w\s:\.\-]{0,20}?\n?\s*(\d{3,5}(?:\.\d+)?)',
            r'(?:BUY|SELL)(?:\s+(?:XAUUSD|GOLD|XAU))?\s*[:@]?\s*(\d{3,5}(?:\.\d+)?)',
        ]
        for pattern in single_patterns:
            single_match = re.search(pattern, text, re.IGNORECASE)
            if single_match:
                entry_low = entry_high = float(single_match.group(1))
                break

    if entry_low is None:
        logger.warning("Entry ár nem található a signálban.")
        return None

    # ── TP szintek ───────────────────────────────────────────────────────────
    # "open" szöveget kihagyja
    tp_levels = []
    tp_matches = re.findall(r'TP\s*\d*[\s:\-•]*\s*(\d{3,5}(?:\.\d+)?|open)', text, re.IGNORECASE)
    for v in tp_matches:
        if v.lower() == "open":
            continue
        tp_levels.append(float(v))

    if not tp_levels:
        logger.warning("TP szintek nem találhatók a signálban.")
        return None

    # ── Stop Loss ────────────────────────────────────────────────────────────
    sl = None
    sl_was_auto = False

    sl_match = re.search(
        r'(?:stop\s*loss(?:\s*\([^)]*\))?|\bsl\b)\s*[:\-•]*\s*(\d{3,5}(?:\.\d+)?)',
        text, re.IGNORECASE
    )
    if sl_match:
        sl = float(sl_match.group(1))

    # Ha nincs SL és van DEFAULT_SL_USD, auto SL
    if sl is None:
        if default_sl_usd > 0:
            entry_mid = (entry_low + entry_high) / 2
            if action == "BUY":
                sl = round(entry_mid - default_sl_usd, 2)
            else:
                sl = round(entry_mid + default_sl_usd, 2)
            sl_was_auto = True
            logger.info(f"Stop Loss nem található — AUTO SL alkalmazva: {sl} (entry {entry_mid} ± {default_sl_usd} USD)")
        else:
            logger.warning("Stop Loss nem található a signálban és DEFAULT_SL_USD nincs beállítva.")
            return None

    return TradeSignal(action, entry_low, entry_high, tp_levels, sl, text, sl_was_auto)
