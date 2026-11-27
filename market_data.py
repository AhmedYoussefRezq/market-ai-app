# -*- coding: utf-8 -*-
"""Free market data: prices, indicators, macro context and news. No API keys needed."""
import datetime
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

SYMBOLS = {
    "XAUUSD": {"yahoo": "GC=F", "label": "Gold / الذهب", "query": "gold price XAUUSD", "kind": "metal"},
    "XAGUSD": {"yahoo": "SI=F", "label": "Silver / الفضة", "query": "silver price XAGUSD", "kind": "metal"},
    "EURUSD": {"yahoo": "EURUSD=X", "label": "EUR/USD", "query": "EUR USD forex", "kind": "forex"},
    "GBPUSD": {"yahoo": "GBPUSD=X", "label": "GBP/USD", "query": "GBP USD forex", "kind": "forex"},
    "USDJPY": {"yahoo": "USDJPY=X", "label": "USD/JPY", "query": "USD JPY forex", "kind": "forex"},
    "BTCUSD": {"yahoo": "BTC-USD", "label": "Bitcoin", "query": "Bitcoin BTC price", "kind": "crypto"},
    "ETHUSD": {"yahoo": "ETH-USD", "label": "Ethereum", "query": "Ethereum ETH price", "kind": "crypto"},
    "US500": {"yahoo": "^GSPC", "label": "S&P 500", "query": "S&P 500 stock market", "kind": "index"},
    "US30": {"yahoo": "^DJI", "label": "Dow Jones", "query": "Dow Jones stock market", "kind": "index"},
    "NAS100": {"yahoo": "^IXIC", "label": "Nasdaq", "query": "Nasdaq stock market", "kind": "index"},
    "VIX": {"yahoo": "^VIX", "label": "Volatility VIX", "query": "VIX volatility market", "kind": "index"},
    "DXY": {"yahoo": "DX-Y.NYB", "label": "US Dollar Index", "query": "US dollar index DXY", "kind": "macro"},
    "US10Y": {"yahoo": "^TNX", "label": "US 10Y yield", "query": "US treasury yields", "kind": "macro"},
    "WTI": {"yahoo": "CL=F", "label": "WTI Oil", "query": "crude oil price", "kind": "energy"},
}

MACRO_SYMBOLS = ["DXY", "US10Y", "VIX", "US500", "BTCUSD", "WTI"]

INTERVALS = {
    "M15": ("15m", "1mo", 1),
    "M30": ("30m", "1mo", 1),
    "H1": ("60m", "3mo", 1),
    "H4": ("60m", "3mo", 4),
    "D1": ("1d", "1y", 1),
}

_CACHE = {}


def _cached(key, ttl, producer):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = producer()
    if value:
        _CACHE[key] = (now, value)
    return value


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json,text/xml,text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _resample(candles, factor):
    if factor <= 1 or not candles:
        return candles
    out = []
    for i in range(0, len(candles), factor):
        group = candles[i:i + factor]
        if not group:
            continue
        out.append({
            "t": group[0]["t"],
            "o": group[0]["o"],
            "h": max(c["h"] for c in group),
            "l": min(c["l"] for c in group),
            "c": group[-1]["c"],
            "v": sum(c["v"] for c in group),
        })
    return out


def fetch_candles(symbol, interval="H1", cache_minutes=15):
    """Return OHLC candles from Yahoo Finance, with H4 resampled from 1h."""
    info = SYMBOLS.get(symbol)
    if not info:
        raise ValueError("الأداة غير معروفة")
    yahoo_int, rng, factor = INTERVALS.get(interval, INTERVALS["H1"])
    key = ("candles", symbol, interval)
    ttl = max(1, int(cache_minutes)) * 60

    def producer():
        q = urllib.parse.quote(info["yahoo"], safe="")
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{q}"
               f"?range={rng}&interval={yahoo_int}&includePrePost=false")
        data = json.loads(_get(url))
        result = ((data.get("chart") or {}).get("result") or [None])[0]
        if not result:
            return []
        stamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        vols = quote.get("volume") or []
        out = []
        for i, ts in enumerate(stamps):
            try:
                c = float(closes[i])
                o = float(opens[i] if opens[i] is not None else c)
                h = float(highs[i] if highs[i] is not None else max(o, c))
                l = float(lows[i] if lows[i] is not None else min(o, c))
            except (TypeError, ValueError, IndexError):
                continue
            out.append({"t": int(ts), "o": o, "h": h, "l": l, "c": c,
                        "v": float(vols[i]) if i < len(vols) and vols[i] is not None else 0.0})
        return _resample(out, factor)

    return _cached(key, ttl, producer)


def fetch_spot_fallback(symbol):
    """Last-resort spot price when Yahoo is rate-limited."""
    try:
        if symbol == "XAUUSD":
            d = json.loads(_get("https://api.gold-api.com/price/XAU"))
            return float(d["price"])
        if symbol == "XAGUSD":
            d = json.loads(_get("https://api.gold-api.com/price/XAG"))
            return float(d["price"])
    except Exception:
        return None
    return None


def _ema(values, period):
    if not values:
        return None
    k = 2.0 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def _sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _rsi(values, period=14):
    if len(values) <= period:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        diff = values[i] - values[i - 1]
        gains += max(diff, 0)
        losses += max(-diff, 0)
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - (100 / (1 + rs))


def _macd(values):
    if len(values) < 35:
        return None, None, None
    line = _ema(values, 12) - _ema(values, 26)
    # signal from the whole series for stability
    series = []
    for i in range(26, len(values) + 1):
        sub = values[:i]
        series.append(_ema(sub, 12) - _ema(sub, 26))
    signal = _ema(series, 9)
    return line, signal, line - signal


def _atr(candles, period=14):
    if len(candles) <= period:
        return None
    trs = []
    for i in range(-period, 0):
        c = candles[i]
        prev = candles[i - 1]["c"]
        trs.append(max(c["h"] - c["l"], abs(c["h"] - prev), abs(c["l"] - prev)))
    return sum(trs) / period


def _stoch(candles, period=14):
    if len(candles) < period:
        return None
    window = candles[-period:]
    hi = max(c["h"] for c in window)
    lo = min(c["l"] for c in window)
    if hi == lo:
        return 50.0
    return 100 * (window[-1]["c"] - lo) / (hi - lo)


def indicators(candles):
    closes = [c["c"] for c in candles]
    if not closes:
        return {}
    last, prev = closes[-1], closes[-2] if len(closes) > 1 else closes[-1]
    macd, signal, hist = _macd(closes)
    atr = _atr(candles)
    sma20 = _sma(closes, 20)
    sd = 0.0
    if len(closes) >= 20:
        mean = sum(closes[-20:]) / 20
        sd = (sum((x - mean) ** 2 for x in closes[-20:]) / 20) ** 0.5
    bb_up, bb_lo = (sma20 + 2 * sd, sma20 - 2 * sd) if sma20 else (None, None)
    vols = [c["v"] for c in candles[-20:]]
    avg_vol = sum(vols) / len(vols) if vols else 0
    trend = "unknown"
    if _sma(closes, 50) and _ema(closes, 20):
        if _ema(closes, 20) > _sma(closes, 50) and last > _ema(closes, 20):
            trend = "bullish"
        elif _ema(closes, 20) < _sma(closes, 50) and last < _ema(closes, 20):
            trend = "bearish"
        else:
            trend = "mixed"
    return {
        "last": round(last, 4),
        "previous": round(prev, 4),
        "change_pct": round((last - prev) / prev * 100, 3) if prev else 0,
        "ema20": round(_ema(closes, 20), 4) if _ema(closes, 20) else None,
        "ema50": round(_ema(closes, 50), 4) if _ema(closes, 50) else None,
        "sma200": round(_sma(closes, 200), 4) if _sma(closes, 200) else None,
        "rsi14": round(_rsi(closes), 1) if _rsi(closes) is not None else None,
        "macd": round(macd, 4) if macd is not None else None,
        "macd_signal": round(signal, 4) if signal is not None else None,
        "macd_hist": round(hist, 4) if hist is not None else None,
        "atr14": round(atr, 4) if atr is not None else None,
        "atr_pct": round(atr / last * 100, 3) if atr and last else None,
        "bb_upper": round(bb_up, 4) if bb_up else None,
        "bb_lower": round(bb_lo, 4) if bb_lo else None,
        "stoch_k": round(_stoch(candles), 1) if _stoch(candles) is not None else None,
        "high20": round(max(c["h"] for c in candles[-20:]), 4),
        "low20": round(min(c["l"] for c in candles[-20:]), 4),
        "volume_ratio": round(candles[-1]["v"] / avg_vol, 2) if avg_vol else None,
        "trend": trend,
        "bars": len(candles),
    }


def fetch_news(query, limit=8, cache_minutes=15):
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    key = ("news", query, limit)
    ttl = max(1, int(cache_minutes)) * 60

    def producer():
        try:
            root = ET.fromstring(_get(url, timeout=12))
        except Exception:
            return []
        out = []
        for item in root.findall(".//item")[:max(10, int(limit) * 3)]:
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            out.append({
                "title": title[:220],
                "link": (item.findtext("link") or "").strip(),
                "published": (item.findtext("pubDate") or "").strip(),
                "source": ((item.find("source").text if item.find("source") is not None else "") or "").strip(),
            })
            if len(out) >= limit:
                break
        return out

    return _cached(key, ttl, producer)


def fetch_macro(cache_minutes=15):
    out = []
    for sym in MACRO_SYMBOLS:
        try:
            candles = fetch_candles(sym, "D1", cache_minutes)
            if not candles:
                continue
            ind = indicators(candles)
            out.append({"symbol": sym, "label": SYMBOLS[sym]["label"],
                        "price": ind.get("last"), "change_pct": ind.get("change_pct"),
                        "trend": ind.get("trend")})
        except Exception:
            continue
    return out


def market_snapshot(symbol="XAUUSD", interval="H1", cfg=None):
    """Everything the AI needs in one compact packet."""
    cfg = cfg or {}
    symbol = symbol if symbol in SYMBOLS else cfg.get("default_symbol", "XAUUSD")
    interval = interval if interval in INTERVALS else cfg.get("default_interval", "H1")
    cache_minutes = int(cfg.get("cache_minutes", 15))
    news_limit = max(3, min(20, int(cfg.get("news_limit", 12))))

    warnings = []
    try:
        candles = fetch_candles(symbol, interval, cache_minutes)
    except Exception as exc:
        candles = []
        warnings.append(f"price source error: {type(exc).__name__}")
    if not candles:
        spot = fetch_spot_fallback(symbol)
        if spot:
            candles = [{"t": int(time.time()), "o": spot, "h": spot, "l": spot,
                        "c": spot, "v": 0}]
            warnings.append("using spot fallback; indicators unavailable")
        else:
            warnings.append("no price data available")

    ind = indicators(candles)
    recent = [{"t": datetime.datetime.utcfromtimestamp(c["t"]).strftime("%Y-%m-%d %H:%M"),
               "o": round(c["o"], 4), "h": round(c["h"], 4),
               "l": round(c["l"], 4), "c": round(c["c"], 4)} for c in candles[-16:]]

    news = fetch_news(SYMBOLS[symbol]["query"], news_limit, cache_minutes)
    general = fetch_news("financial markets economy central banks", news_limit, cache_minutes)
    seen = set()
    merged = []
    for n in news + general:
        title = n["title"]
        if title in seen:
            continue
        seen.add(title)
        merged.append(n)
        if len(merged) >= news_limit:
            break

    return {
        "symbol": symbol,
        "label": SYMBOLS[symbol]["label"],
        "kind": SYMBOLS[symbol]["kind"],
        "interval": interval,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "indicators": ind,
        "recent_candles": recent,
        "macro": fetch_macro(cache_minutes),
        "news": merged,
        "warnings": warnings,
        "sources": ["Yahoo Finance", "Google News RSS", "gold-api.com (fallback)"],
    }
