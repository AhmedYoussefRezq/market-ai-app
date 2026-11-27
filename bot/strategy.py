# -*- coding: utf-8 -*-
"""Scalp strategy: EMA trend + RSI pullback + ATR stop/target.

Pure Python, no MT5 dependency. Works on any OHLC list:
    [{'t': ts, 'o': open, 'h': high, 'l': low, 'c': close}, ...]
"""
DEFAULTS = {
    "fast": 20,        # trend EMA
    "slow": 50,        # trend EMA
    "pull": 9,         # pullback EMA
    "rsi": 14,
    "atr": 14,
    "sl_atr": 1.2,
    "tp_atr": 2.5,
    "max_hold_bars": 10,
}


def ema(vals, period):
    if not vals:
        return []
    k = 2.0 / (period + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(vals, period=14):
    out = [None] * len(vals)
    if len(vals) <= period:
        return out
    g = l = 0.0
    for i in range(1, period + 1):
        d = vals[i] - vals[i - 1]
        g += max(d, 0.0)
        l += max(-d, 0.0)
    g /= period
    l /= period
    out[period] = 100.0 if l == 0 else 100.0 - 100.0 / (1.0 + g / l)
    for i in range(period + 1, len(vals)):
        d = vals[i] - vals[i - 1]
        g = (g * (period - 1) + max(d, 0.0)) / period
        l = (l * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100.0 if l == 0 else 100.0 - 100.0 / (1.0 + g / l)
    return out


def atr(candles, period=14):
    out = [None] * len(candles)
    if len(candles) <= period:
        return out
    tr = [None]
    for i in range(1, len(candles)):
        prev = candles[i - 1]["c"]
        tr.append(max(candles[i]["h"] - candles[i]["l"],
                      abs(candles[i]["h"] - prev),
                      abs(candles[i]["l"] - prev)))
    value = sum(tr[1:period + 1]) / period
    out[period] = value
    for i in range(period + 1, len(candles)):
        value = (value * (period - 1) + tr[i]) / period
        out[i] = value
    return out


def _series(candles, cfg):
    closes = [c["c"] for c in candles]
    return {
        "fast": ema(closes, cfg["fast"]),
        "slow": ema(closes, cfg["slow"]),
        "pull": ema(closes, cfg["pull"]),
        "rsi": rsi(closes, cfg["rsi"]),
        "atr": atr(candles, cfg["atr"]),
        "closes": closes,
    }


def signal(candles, cfg=None, spread_cost=0.0):
    """Signal on the LAST CLOSED candle. Entry is estimated at the next open."""
    cfg = dict(DEFAULTS, **(cfg or {}))
    if len(candles) < cfg["slow"] + 5:
        return None
    s = _series(candles, cfg)
    i = len(candles) - 1
    r, a = s["rsi"][i], s["atr"][i]
    if r is None or not a:
        return None
    prev_r = s["rsi"][i - 1]
    if prev_r is None:
        return None
    long_cross = prev_r <= 45 < r
    short_cross = prev_r >= 55 > r
    up = s["fast"][i] > s["slow"][i]
    dn = s["fast"][i] < s["slow"][i]
    long_ok = long_cross and s["closes"][i] <= s["pull"][i] * 1.002 and up
    short_ok = short_cross and s["closes"][i] >= s["pull"][i] * 0.998 and dn
    if not long_ok and not short_ok:
        return None
    side = "BUY" if long_ok else "SELL"
    price = candles[-1]["c"] + (spread_cost if long_ok else -spread_cost)
    risk = cfg["sl_atr"] * a
    sl = price - risk if long_ok else price + risk
    tp = price + cfg["tp_atr"] * a if long_ok else price - cfg["tp_atr"] * a
    return {
        "side": side, "price": round(price, 6),
        "sl": round(sl, 6), "tp": round(tp, 6), "risk": round(risk, 6),
        "rsi": round(r, 1), "atr": round(a, 6), "spread_cost": spread_cost,
        "reasons": [
            "RSI14 %.1f %s 45/55" % (r, "crossed above" if long_ok else "crossed below"),
            "EMA%d %s EMA%d" % (cfg["fast"], ">" if long_ok else "<", cfg["slow"]),
            "price near EMA%d pullback" % cfg["pull"],
        ],
    }


def backtest(candles, cfg=None, spread_cost=0.0):
    """Trade on next bar open. Stop is checked before target on a two-sided bar."""
    cfg = dict(DEFAULTS, **(cfg or {}))
    if len(candles) < cfg["slow"] + 5:
        return []
    s = _series(candles, cfg)
    trades = []
    pos = None
    for i in range(cfg["slow"] + 2, len(candles) - 1):
        bar = candles[i]
        if pos and i >= pos["i"]:
            long = pos["side"] == "BUY"
            hit_sl = bar["l"] <= pos["sl"] if long else bar["h"] >= pos["sl"]
            hit_tp = bar["h"] >= pos["tp"] if long else bar["l"] <= pos["tp"]
            if hit_sl or hit_tp:
                exit_price = pos["sl"] if hit_sl else pos["tp"]
                r = ((exit_price - pos["e"]) / pos["risk"]) if long else ((pos["e"] - exit_price) / pos["risk"])
                trades.append({"r": round(r, 3), "reason": "SL" if hit_sl else "TP",
                               "bars": i - pos["i"], "t": bar["t"]})
                pos = None
            elif i - pos["i"] >= cfg["max_hold_bars"]:
                exit_price = bar["c"]
                r = ((exit_price - pos["e"]) / pos["risk"]) if long else ((pos["e"] - exit_price) / pos["risk"])
                trades.append({"r": round(r, 3), "reason": "TIME",
                               "bars": i - pos["i"], "t": bar["t"]})
                pos = None
        if not pos and s["rsi"][i] is not None and s["rsi"][i - 1] is not None and s["atr"][i]:
            prev_r, r, a = s["rsi"][i - 1], s["rsi"][i], s["atr"][i]
            next_bar = candles[i + 1]
            long_ok = prev_r <= 45 < r and s["closes"][i] <= s["pull"][i] * 1.002 and s["fast"][i] > s["slow"][i]
            short_ok = prev_r >= 55 > r and s["closes"][i] >= s["pull"][i] * 0.998 and s["fast"][i] < s["slow"][i]
            if long_ok or short_ok:
                side = "BUY" if long_ok else "SELL"
                entry = next_bar["o"] + (spread_cost if long_ok else -spread_cost)
                risk = cfg["sl_atr"] * a
                pos = {
                    "side": side, "i": i + 1, "e": entry, "risk": risk,
                    "sl": entry - risk if long_ok else entry + risk,
                    "tp": entry + cfg["tp_atr"] * a if long_ok else entry - cfg["tp_atr"] * a,
                }
    if pos:
        exit_price = candles[-1]["c"]
        long = pos["side"] == "BUY"
        r = ((exit_price - pos["e"]) / pos["risk"]) if long else ((pos["e"] - exit_price) / pos["risk"])
        trades.append({"r": round(r, 3), "reason": "END",
                       "bars": len(candles) - 1 - pos["i"], "t": candles[-1]["t"]})
    return trades


def stats(trades):
    n = len(trades)
    if not n:
        return {"trades": 0, "win_rate": 0, "profit_factor": 0, "total_r": 0, "max_dd": 0, "avg_bars": 0}
    wins = [t for t in trades if t["r"] > 0]
    gross_win = sum(t["r"] for t in wins)
    gross_loss = abs(sum(t["r"] for t in trades if t["r"] <= 0))
    cum = peak = dd = 0.0
    for t in trades:
        cum += t["r"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return {
        "trades": n,
        "win_rate": round(100.0 * len(wins) / n, 1),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else (99.0 if gross_win else 0.0),
        "total_r": round(cum, 2),
        "max_dd": round(dd, 2),
        "avg_bars": round(sum(t["bars"] for t in trades) / n, 1),
    }


def edge_ok(st, min_pf=1.15, min_trades=100, max_dd=10.0):
    return (st["trades"] >= min_trades and st["profit_factor"] >= min_pf and st["max_dd"] <= max_dd)
