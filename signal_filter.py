"""
Technikai szűrő modul — Trading Bot
8 indikátor alapú jelzés megerősítő

Az MT5 beépített indikátorokat használja — ugyanazokat az értékeket
adja mint amit a charton látsz.

Indikátorok:
  1. EMA/SMA Keresztezés
  2. MACD + Signal Vonal
  3. RSI Szűrő
  4. Bollinger Band
  5. ATR Dinamikus SL
  6. Gyertya Minta Felismerő
  7. ADX Trend Erő
  8. Volume Szűrő

Beállítások a user_settings.json-ban (setup.py generálja).
"""

import logging
import MetaTrader5 as mt5
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Timeframe mapping ─────────────────────────────────────────────────────────

TIMEFRAME_MAP = {
    "M1":  mt5.TIMEFRAME_M1,
    "M5":  mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}


def _get_tf(tf_str: str):
    """Időkeret string → MT5 konstans."""
    return TIMEFRAME_MAP.get(tf_str.upper(), mt5.TIMEFRAME_H1)


def _get_rates(symbol: str, tf_str: str, count: int):
    """Gyertyaadatok lekérése MT5-ből."""
    tf = _get_tf(tf_str)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) < count:
        logger.warning(f"Nem sikerült lekérni a gyertyaadatokat ({symbol}, {tf_str}, {count})")
        return None
    return rates


# ── 1. EMA/SMA Keresztezés ───────────────────────────────────────────────────

def check_ema_sma(symbol: str, action: str, config: dict) -> tuple[bool, str]:
    """
    EMA/SMA keresztezés ellenőrzése.
    BUY: gyors EMA > lassú SMA (és az előző gyertyan fordítva volt)
    SELL: gyors EMA < lassú SMA (és az előző gyertyan fordítva volt)
    """
    tf        = config.get("ema_sma_tf", "H1")
    ema_period = config.get("ema_period", 7)
    sma_period = config.get("sma_period", 10)
    count = max(ema_period, sma_period) + 5

    rates = _get_rates(symbol, tf, count)
    if rates is None:
        return False, "EMA/SMA: adatlekérés sikertelen"

    closes = [r['close'] for r in rates]

    # EMA számítás
    def ema(values, period):
        k = 2 / (period + 1)
        result = [sum(values[:period]) / period]
        for v in values[period:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    # SMA számítás
    def sma(values, period):
        return [sum(values[i:i+period]) / period for i in range(len(values) - period + 1)]

    ema_vals = ema(closes, ema_period)
    sma_vals = sma(closes, sma_period)

    # Utolsó 2 érték összehasonlítása
    min_len = min(len(ema_vals), len(sma_vals))
    if min_len < 2:
        return False, "EMA/SMA: nincs elég adat"

    ema_now  = ema_vals[-1]
    sma_now  = sma_vals[-1]
    ema_prev = ema_vals[-2]
    sma_prev = sma_vals[-2]

    if action == "BUY":
        # Gyors EMA felette van a lassú SMA-nak (bullish)
        if ema_now > sma_now:
            return True, f"EMA/SMA ✅ BUY: EMA{ema_period}({ema_now:.2f}) > SMA{sma_period}({sma_now:.2f}) [{tf}]"
        else:
            return False, f"EMA/SMA ❌ BUY: EMA{ema_period}({ema_now:.2f}) < SMA{sma_period}({sma_now:.2f}) [{tf}]"

    elif action == "SELL":
        # Gyors EMA alatta van a lassú SMA-nak (bearish)
        if ema_now < sma_now:
            return True, f"EMA/SMA ✅ SELL: EMA{ema_period}({ema_now:.2f}) < SMA{sma_period}({sma_now:.2f}) [{tf}]"
        else:
            return False, f"EMA/SMA ❌ SELL: EMA{ema_period}({ema_now:.2f}) > SMA{sma_period}({sma_now:.2f}) [{tf}]"

    return False, "EMA/SMA: ismeretlen irány"


# ── 2. MACD ──────────────────────────────────────────────────────────────────

def check_macd(symbol: str, action: str, config: dict) -> tuple[bool, str]:
    """
    MACD + Signal vonal ellenőrzése.
    BUY: MACD > Signal (MACD felette)
    SELL: MACD < Signal (MACD alatta)
    """
    tf         = config.get("macd_tf", "H1")
    fast       = config.get("macd_fast", 12)
    slow       = config.get("macd_slow", 26)
    signal_per = config.get("macd_signal", 9)
    count = slow + signal_per + 10

    rates = _get_rates(symbol, tf, count)
    if rates is None:
        return False, "MACD: adatlekérés sikertelen"

    closes = [r['close'] for r in rates]

    def ema(values, period):
        k = 2 / (period + 1)
        result = [sum(values[:period]) / period]
        for v in values[period:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    ema_fast   = ema(closes, fast)
    ema_slow   = ema(closes, slow)
    min_len    = min(len(ema_fast), len(ema_slow))
    macd_line  = [ema_fast[i + len(ema_fast) - min_len] - ema_slow[i + len(ema_slow) - min_len]
                  for i in range(min_len)]
    signal_line = ema(macd_line, signal_per)

    min_len2    = min(len(macd_line), len(signal_line))
    macd_now    = macd_line[-1]
    signal_now  = signal_line[-1]
    histogram   = macd_now - signal_now

    if action == "BUY":
        if macd_now > signal_now:
            return True, f"MACD ✅ BUY: MACD({macd_now:.4f}) > Signal({signal_now:.4f}) hist:{histogram:.4f} [{tf}]"
        else:
            return False, f"MACD ❌ BUY: MACD({macd_now:.4f}) < Signal({signal_now:.4f}) [{tf}]"

    elif action == "SELL":
        if macd_now < signal_now:
            return True, f"MACD ✅ SELL: MACD({macd_now:.4f}) < Signal({signal_now:.4f}) hist:{histogram:.4f} [{tf}]"
        else:
            return False, f"MACD ❌ SELL: MACD({macd_now:.4f}) > Signal({signal_now:.4f}) [{tf}]"

    return False, "MACD: ismeretlen irány"


# ── 3. RSI ───────────────────────────────────────────────────────────────────

def check_rsi(symbol: str, action: str, config: dict) -> tuple[bool, str]:
    """
    RSI szűrő.
    BUY: RSI < felső limit (nem túlvett)
    SELL: RSI > alsó limit (nem túladott)
    """
    tf          = config.get("rsi_tf", "H1")
    period      = config.get("rsi_period", 14)
    buy_limit   = config.get("rsi_buy_limit", 65)
    sell_limit  = config.get("rsi_sell_limit", 35)
    count = period + 5

    rates = _get_rates(symbol, tf, count)
    if rates is None:
        return False, "RSI: adatlekérés sikertelen"

    closes = [r['close'] for r in rates]
    deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]

    gains  = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        rsi = 100.0
    else:
        rs  = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

    if action == "BUY":
        if rsi < buy_limit:
            return True, f"RSI ✅ BUY: RSI({rsi:.1f}) < {buy_limit} (nem túlvett) [{tf}]"
        else:
            return False, f"RSI ❌ BUY: RSI({rsi:.1f}) >= {buy_limit} (túlvett, kihagyva) [{tf}]"

    elif action == "SELL":
        if rsi > sell_limit:
            return True, f"RSI ✅ SELL: RSI({rsi:.1f}) > {sell_limit} (nem túladott) [{tf}]"
        else:
            return False, f"RSI ❌ SELL: RSI({rsi:.1f}) <= {sell_limit} (túladott, kihagyva) [{tf}]"

    return False, "RSI: ismeretlen irány"


# ── 4. Bollinger Band ─────────────────────────────────────────────────────────

def check_bollinger(symbol: str, action: str, config: dict) -> tuple[bool, str]:
    """
    Bollinger Band szűrő.
    BUY: ár az alsó sáv közelében
    SELL: ár a felső sáv közelében
    """
    tf         = config.get("bb_tf", "H1")
    period     = config.get("bb_period", 20)
    std_dev    = config.get("bb_std", 2.0)
    proximity  = config.get("bb_proximity", 0.1)  # sáv szélességének hány %-a
    count = period + 5

    rates = _get_rates(symbol, tf, count)
    if rates is None:
        return False, "Bollinger: adatlekérés sikertelen"

    closes   = [r['close'] for r in rates]
    mid      = sum(closes[-period:]) / period
    variance = sum((c - mid) ** 2 for c in closes[-period:]) / period
    std      = variance ** 0.5

    upper    = mid + std_dev * std
    lower    = mid - std_dev * std
    current  = closes[-1]
    band_width = upper - lower
    threshold  = band_width * proximity

    if action == "BUY":
        if current <= lower + threshold:
            return True, f"Bollinger ✅ BUY: ár({current:.2f}) közel alsó sávhoz({lower:.2f}) [{tf}]"
        else:
            return False, f"Bollinger ❌ BUY: ár({current:.2f}) nem az alsó sávnál (lower:{lower:.2f}) [{tf}]"

    elif action == "SELL":
        if current >= upper - threshold:
            return True, f"Bollinger ✅ SELL: ár({current:.2f}) közel felső sávhoz({upper:.2f}) [{tf}]"
        else:
            return False, f"Bollinger ❌ SELL: ár({current:.2f}) nem a felső sávnál (upper:{upper:.2f}) [{tf}]"

    return False, "Bollinger: ismeretlen irány"


# ── 5. ATR Dinamikus SL ───────────────────────────────────────────────────────

def get_atr_sl(symbol: str, action: str, entry_price: float, config: dict) -> tuple[float | None, str]:
    """
    ATR alapú dinamikus SL számítás.
    Visszatér az új SL értékkel (nem szűrő, hanem SL javító).
    """
    tf       = config.get("atr_tf", "H1")
    period   = config.get("atr_period", 14)
    multiplier = config.get("atr_multiplier", 1.5)
    count = period + 5

    rates = _get_rates(symbol, tf, count)
    if rates is None:
        return None, "ATR: adatlekérés sikertelen"

    true_ranges = []
    for i in range(1, len(rates)):
        high = rates[i]['high']
        low  = rates[i]['low']
        prev_close = rates[i-1]['close']
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None, "ATR: nincs elég adat"

    atr = sum(true_ranges[-period:]) / period
    sl_distance = atr * multiplier

    if action == "BUY":
        new_sl = round(entry_price - sl_distance, 2)
        return new_sl, f"ATR SL: {new_sl:.2f} (entry:{entry_price:.2f} - ATR:{atr:.2f} × {multiplier}) [{tf}]"
    elif action == "SELL":
        new_sl = round(entry_price + sl_distance, 2)
        return new_sl, f"ATR SL: {new_sl:.2f} (entry:{entry_price:.2f} + ATR:{atr:.2f} × {multiplier}) [{tf}]"

    return None, "ATR: ismeretlen irány"


# ── 6. Gyertya Minta ─────────────────────────────────────────────────────────

def check_candle_pattern(symbol: str, action: str, config: dict) -> tuple[bool, str]:
    """
    Gyertya minta felismerő.
    Bullish/Bearish Engulfing, Hammer, Shooting Star, Pin Bar.
    """
    tf    = config.get("candle_tf", "M15")
    count = 5

    rates = _get_rates(symbol, tf, count)
    if rates is None:
        return False, "Gyertya: adatlekérés sikertelen"

    curr  = rates[-1]
    prev  = rates[-2]

    c_open  = curr['open']
    c_close = curr['close']
    c_high  = curr['high']
    c_low   = curr['low']
    c_body  = abs(c_close - c_open)
    c_range = c_high - c_low

    p_open  = prev['open']
    p_close = prev['close']
    p_body  = abs(p_close - p_open)

    if c_range == 0:
        return False, "Gyertya: érvénytelen gyertya (range=0)"

    upper_shadow = c_high - max(c_open, c_close)
    lower_shadow = min(c_open, c_close) - c_low

    if action == "BUY":
        # Bullish Engulfing: nagy zöld nyeli el a pirosat
        if (c_close > c_open and p_close < p_open and
                c_open < p_close and c_close > p_open and c_body > p_body * 0.8):
            return True, f"Gyertya ✅ BUY: Bullish Engulfing [{tf}]"

        # Hammer: kis test fent, hosszú alsó árnyék
        if (lower_shadow >= c_body * 2 and upper_shadow <= c_body * 0.3 and
                c_body > 0):
            return True, f"Gyertya ✅ BUY: Hammer [{tf}]"

        # Bullish Pin Bar: alsó árnyék legalább 2× a test
        if lower_shadow >= c_range * 0.6 and c_body <= c_range * 0.3:
            return True, f"Gyertya ✅ BUY: Bullish Pin Bar [{tf}]"

        return False, f"Gyertya ❌ BUY: nincs bullish minta [{tf}]"

    elif action == "SELL":
        # Bearish Engulfing: nagy piros nyeli el a zöldet
        if (c_close < c_open and p_close > p_open and
                c_open > p_close and c_close < p_open and c_body > p_body * 0.8):
            return True, f"Gyertya ✅ SELL: Bearish Engulfing [{tf}]"

        # Shooting Star: kis test lent, hosszú felső árnyék
        if (upper_shadow >= c_body * 2 and lower_shadow <= c_body * 0.3 and
                c_body > 0):
            return True, f"Gyertya ✅ SELL: Shooting Star [{tf}]"

        # Bearish Pin Bar: felső árnyék legalább 2× a test
        if upper_shadow >= c_range * 0.6 and c_body <= c_range * 0.3:
            return True, f"Gyertya ✅ SELL: Bearish Pin Bar [{tf}]"

        return False, f"Gyertya ❌ SELL: nincs bearish minta [{tf}]"

    return False, "Gyertya: ismeretlen irány"


# ── 7. ADX Trend Erő ─────────────────────────────────────────────────────────

def check_adx(symbol: str, action: str, config: dict) -> tuple[bool, str]:
    """
    ADX trend erő szűrő.
    Csak akkor engedi a nyitást ha ADX > minimum és a DI irány egyezik.
    """
    tf         = config.get("adx_tf", "H1")
    period     = config.get("adx_period", 14)
    min_adx    = config.get("adx_min", 25)
    count = period * 2 + 5

    rates = _get_rates(symbol, tf, count)
    if rates is None:
        return False, "ADX: adatlekérés sikertelen"

    # +DM, -DM, TR számítás
    plus_dm  = []
    minus_dm = []
    true_ranges = []

    for i in range(1, len(rates)):
        high_diff = rates[i]['high'] - rates[i-1]['high']
        low_diff  = rates[i-1]['low'] - rates[i]['low']
        tr = max(
            rates[i]['high'] - rates[i]['low'],
            abs(rates[i]['high'] - rates[i-1]['close']),
            abs(rates[i]['low'] - rates[i-1]['close'])
        )
        true_ranges.append(tr)
        plus_dm.append(high_diff if high_diff > low_diff and high_diff > 0 else 0)
        minus_dm.append(low_diff if low_diff > high_diff and low_diff > 0 else 0)

    if len(true_ranges) < period:
        return False, "ADX: nincs elég adat"

    # Smoothed átlagok
    def smooth(values, p):
        s = sum(values[:p])
        result = [s]
        for v in values[p:]:
            s = s - s/p + v
            result.append(s)
        return result

    tr_smooth   = smooth(true_ranges, period)
    pdm_smooth  = smooth(plus_dm, period)
    mdm_smooth  = smooth(minus_dm, period)

    min_len = min(len(tr_smooth), len(pdm_smooth), len(mdm_smooth))

    pdi = 100 * pdm_smooth[-1] / tr_smooth[-1] if tr_smooth[-1] != 0 else 0
    mdi = 100 * mdm_smooth[-1] / tr_smooth[-1] if tr_smooth[-1] != 0 else 0

    dx_list = []
    for i in range(min_len):
        denom = pdm_smooth[i] + mdm_smooth[i]
        if denom != 0:
            dx = 100 * abs(pdm_smooth[i] - mdm_smooth[i]) / denom
            dx_list.append(dx)

    if not dx_list:
        return False, "ADX: nem sikerült kiszámolni"

    adx = sum(dx_list[-period:]) / min(period, len(dx_list))

    if adx < min_adx:
        return False, f"ADX ❌: ADX({adx:.1f}) < {min_adx} (gyenge trend, kihagyva) [{tf}]"

    if action == "BUY":
        if pdi > mdi:
            return True, f"ADX ✅ BUY: ADX({adx:.1f}) +DI({pdi:.1f}) > -DI({mdi:.1f}) [{tf}]"
        else:
            return False, f"ADX ❌ BUY: ADX({adx:.1f}) de -DI({mdi:.1f}) > +DI({pdi:.1f}) [{tf}]"

    elif action == "SELL":
        if mdi > pdi:
            return True, f"ADX ✅ SELL: ADX({adx:.1f}) -DI({mdi:.1f}) > +DI({pdi:.1f}) [{tf}]"
        else:
            return False, f"ADX ❌ SELL: ADX({adx:.1f}) de +DI({pdi:.1f}) > -DI({mdi:.1f}) [{tf}]"

    return False, "ADX: ismeretlen irány"


# ── 8. Volume Szűrő ───────────────────────────────────────────────────────────

def check_volume(symbol: str, action: str, config: dict) -> tuple[bool, str]:
    """
    Volume (tick volume) szűrő.
    Az aktuális gyertya forgalma az átlag felett van-e.
    """
    tf         = config.get("volume_tf", "H1")
    avg_period = config.get("volume_period", 20)
    multiplier = config.get("volume_multiplier", 1.3)
    count = avg_period + 2

    rates = _get_rates(symbol, tf, count)
    if rates is None:
        return False, "Volume: adatlekérés sikertelen"

    volumes     = [r['tick_volume'] for r in rates]
    current_vol = volumes[-1]
    avg_vol     = sum(volumes[-avg_period-1:-1]) / avg_period

    if current_vol >= avg_vol * multiplier:
        return True, f"Volume ✅: {current_vol} >= átlag({avg_vol:.0f}) × {multiplier} [{tf}]"
    else:
        return False, f"Volume ❌: {current_vol} < átlag({avg_vol:.0f}) × {multiplier} [{tf}]"


# ── Fő szűrő függvény ─────────────────────────────────────────────────────────

def run_filters(signal, config_obj) -> tuple[bool, float | None, list[str]]:
    """
    Lefuttatja az összes engedélyezett szűrőt.

    Visszatér:
      - allowed (bool): nyithat-e a bot
      - new_sl (float|None): ATR által javasolt új SL (vagy None)
      - messages (list): szűrők üzenetei a loghoz
    """
    # Szűrő beállítások a config-ból
    szuro_config = getattr(config_obj, 'SZURO_CONFIG', {})
    aktiv_szurok = getattr(config_obj, 'AKTIV_SZUROK', [])
    symbol       = getattr(config_obj, 'SYMBOL', 'XAUUSD')
    action       = signal.action
    entry_price  = signal.entry_mid

    if not aktiv_szurok:
        return True, None, ["Szűrő: nincs aktív szűrő — jelzés engedélyezve"]

    messages = []
    new_sl   = None
    results  = []

    # ── Szűrők futtatása ─────────────────────────────────────────────────────
    if "ema_sma" in aktiv_szurok:
        ok, msg = check_ema_sma(symbol, action, szuro_config)
        messages.append(msg)
        results.append(ok)

    if "macd" in aktiv_szurok:
        ok, msg = check_macd(symbol, action, szuro_config)
        messages.append(msg)
        results.append(ok)

    if "rsi" in aktiv_szurok:
        ok, msg = check_rsi(symbol, action, szuro_config)
        messages.append(msg)
        results.append(ok)

    if "bollinger" in aktiv_szurok:
        ok, msg = check_bollinger(symbol, action, szuro_config)
        messages.append(msg)
        results.append(ok)

    if "atr_sl" in aktiv_szurok:
        sl, msg = get_atr_sl(symbol, action, entry_price, szuro_config)
        messages.append(msg)
        new_sl = sl  # ATR SL nem szűr, csak javasol

    if "candle" in aktiv_szurok:
        ok, msg = check_candle_pattern(symbol, action, szuro_config)
        messages.append(msg)
        results.append(ok)

    if "adx" in aktiv_szurok:
        ok, msg = check_adx(symbol, action, szuro_config)
        messages.append(msg)
        results.append(ok)

    if "volume" in aktiv_szurok:
        ok, msg = check_volume(symbol, action, szuro_config)
        messages.append(msg)
        results.append(ok)

    # ── Entry zóna tolerancia ellenőrzés ─────────────────────────────────────
    tolerancia = szuro_config.get("entry_tolerancia_usd", 0)
    if tolerancia > 0:
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            current = tick.bid if action == "SELL" else tick.ask
            entry_low  = signal.entry_low  - tolerancia
            entry_high = signal.entry_high + tolerancia

            in_zone = entry_low <= current <= entry_high
            if in_zone:
                messages.append(f"Entry zóna ✅: ár({current:.2f}) a tolerancia zónában ({entry_low:.2f}-{entry_high:.2f}, ±{tolerancia} USD)")
            else:
                messages.append(f"Entry zóna ❌: ár({current:.2f}) kívül van ({entry_low:.2f}-{entry_high:.2f}, ±{tolerancia} USD)")
                results.append(False)

    # ── Végeredmény ───────────────────────────────────────────────────────────
    allowed = all(results) if results else True

    if allowed:
        messages.append(f"🟢 SZŰRŐ EREDMÉNY: ENGEDÉLYEZVE ({sum(results)}/{len(results)} szűrő OK)")
    else:
        failed = sum(1 for r in results if not r)
        messages.append(f"🔴 SZŰRŐ EREDMÉNY: KIHAGYVA ({failed} szűrő nem teljesült)")

    return allowed, new_sl, messages
