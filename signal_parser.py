import re
import logging

logger = logging.getLogger(__name__)

class TradeSignal:
    def __init__(self, action, entry_low, entry_high, tp_levels, sl, raw_text):
        self.action     = action
        self.entry_low  = entry_low
        self.entry_high = entry_high
        self.tp_levels  = tp_levels
        self.sl         = sl
        self.raw_text   = raw_text

    @property
    def entry_mid(self):
        return round((self.entry_low + self.entry_high) / 2, 2)

    @property
    def tp1(self):
        return self.tp_levels[0] if self.tp_levels else None

    def __str__(self):
        tps = " | ".join([f"TP{i+1}: {v}" for i, v in enumerate(self.tp_levels)])
        return (f"📊 {self.action} XAUUSD\n"
                f"Entry: {self.entry_low}-{self.entry_high}\n"
                f"{tps}\nSL: {self.sl}")

def parse_signal(text: str) -> TradeSignal | None:
    if not text:
        return None
    text_upper = text.upper()

    if "BUY" in text_upper:
        action = "BUY"
    elif "SELL" in text_upper:
        action = "SELL"
    else:
        return None

    entry_low = entry_high = None

    # --- 1) Entry tartomány keresése több formátumban ---
    # Formátumok, amiket kezelünk:
    #   "Entry: 4840/4835"
    #   "Entry Price\n4840/4835"
    #   "Entry: 4840-4835"
    #   "SELL XAUUSD @ 4823-4830"
    #   "BUY XAUUSD @ 4823/4830"
    #   "SELL @ 4823-4830"
    #   "SELL XAUUSD 4823-4830"   (se entry, se @)
    range_patterns = [
        # "entry" kulcsszó után (kettőspont, pont, kötőjel is megengedett közte)
        r'entry[\w\s:\.\-]{0,20}?\n?\s*(\d{3,5}(?:\.\d+)?)\s*[\/\-]\s*(\d{3,5}(?:\.\d+)?)',
        # "@" jel után (pl. SELL XAUUSD @ 4823-4830)
        r'@\s*(\d{3,5}(?:\.\d+)?)\s*[\/\-]\s*(\d{3,5}(?:\.\d+)?)',
        # BUY/SELL (+ opcionális XAUUSD/GOLD) után közvetlenül
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
        # --- 2) Egyetlen entry ár keresése több formátumban ---
        single_patterns = [
            r'entry[\w\s:\.\-]{0,20}?\n?\s*(\d{3,5}(?:\.\d+)?)',
            r'@\s*(\d{3,5}(?:\.\d+)?)',
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

    # --- 3) TP szintek ---
    tp_levels = []
    # Kezeli: "TP1: 4817", "• TP1: 4817", "TP 4817", "Take Profit 1: 4817"
    tp_matches = re.findall(r'TP\s*\d*[:\s\-•]*\s*(\d{3,5}(?:\.\d+)?)', text, re.IGNORECASE)
    for v in tp_matches:
        tp_levels.append(float(v))

    if not tp_levels:
        logger.warning("TP szintek nem találhatók a signálban.")
        return None

    # --- 4) Stop Loss ---
    sl = None
    # Kezeli: "SL: 4840", "Stop Loss: 4840", "🛑 Stop Loss: 4840", "SL 4840"
    sl_match = re.search(
        r'(?:stop\s*loss|\bsl\b)\s*[:\-•]?\s*(\d{3,5}(?:\.\d+)?)',
        text, re.IGNORECASE
    )
    if sl_match:
        sl = float(sl_match.group(1))

    if sl is None:
        logger.warning("Stop Loss nem található a signálban.")
        return None

    return TradeSignal(action, entry_low, entry_high, tp_levels, sl, text)
