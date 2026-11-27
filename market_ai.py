# -*- coding: utf-8 -*-
"""Turn the whole market snapshot + every AI opinion into one studied trade plan."""
import datetime
import json
import statistics

import ai_providers
import market_data

SYSTEM_PROMPT = """You are a disciplined institutional multi-asset trade analyst.
You receive REAL market data: price candles, technical indicators, macro instruments
(dollar index, yields, VIX, S&P 500, Bitcoin, oil) and current news headlines.

Rules:
- Use ONLY the supplied data. Never invent prices, news or facts.
- Study trend, momentum, volatility, support/resistance, macro risk and news together.
- If signals conflict or news risk is high, choose WAIT. Never claim certainty.
- Entry must be a realistic zone near the current price, not a fantasy level.
- Stop loss must be on the opposite side of the entry with a sensible risk.
- Take profit levels must be consistent with volatility and risk/reward.
- Give short, concrete Arabic or English text. Return ONLY one valid JSON object:

{
  "bias": "BUY" | "SELL" | "WAIT",
  "confidence": 0-100,
  "thesis": "2-3 short sentences explaining the trade",
  "key_factors": ["factor 1", "factor 2", "factor 3"],
  "news_impact": "short news/risk assessment",
  "entry_zone": [low, high],
  "stop_loss": number or null,
  "take_profit": [tp1, tp2],
  "risk_reward": number or null,
  "invalidation": "condition that cancels the idea",
  "wait_conditions": "what must happen before entering (or empty string)",
  "time_horizon": "short label",
  "risk_note": "short warning"
}"""


def build_messages(snapshot):
    user = {
        "instrument": snapshot.get("symbol"),
        "label": snapshot.get("label"),
        "timeframe": snapshot.get("interval"),
        "generated_at": snapshot.get("generated_at"),
        "indicators": snapshot.get("indicators", {}),
        "recent_candles": snapshot.get("recent_candles", []),
        "macro_market": snapshot.get("macro", []),
        "news": snapshot.get("news", []),
        "data_warnings": snapshot.get("warnings", []),
        "mandate": "Produce one trade plan (BUY/SELL/WAIT) using all of this evidence.",
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def parse_json(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            pass
    return {}


def _median(values):
    nums = []
    for v in values:
        try:
            n = float(v)
            if n == n and abs(n) != float("inf"):
                nums.append(n)
        except (TypeError, ValueError):
            continue
    return round(statistics.median(nums), 4) if nums else None


def _nth(seq, idx):
    try:
        return seq[idx]
    except (TypeError, IndexError, KeyError):
        return None


def _text(parsed, key, default=""):
    items = parsed if isinstance(parsed, list) else [parsed]
    for item in items:
        if isinstance(item, dict) and item.get(key):
            return str(item[key])
    return default


def _public_market(snapshot):
    return {
        "symbol": snapshot.get("symbol"),
        "label": snapshot.get("label"),
        "interval": snapshot.get("interval"),
        "generated_at": snapshot.get("generated_at"),
        "indicators": snapshot.get("indicators", {}),
        "macro": snapshot.get("macro", []),
        "news": [{"title": n.get("title"), "source": n.get("source"),
                  "published": n.get("published"), "link": n.get("link")}
                 for n in snapshot.get("news", [])],
        "warnings": snapshot.get("warnings", []),
    }


def aggregate(results, snapshot):
    parsed = []
    for r in results:
        if not r.get("ok"):
            continue
        obj = parse_json(r.get("text"))
        if not obj or not isinstance(obj, dict):
            continue
        obj["_meta"] = {"name": r["name"], "model": r["model"],
                        "latency_ms": r.get("latency_ms", 0)}
        parsed.append(obj)

    market = _public_market(snapshot)
    ok = len(parsed)
    provider_rows = []
    for obj in parsed:
        meta = obj["_meta"]
        provider_rows.append({
            "name": meta["name"], "model": meta["model"],
            "bias": str(obj.get("bias", "WAIT")).upper(),
            "confidence": obj.get("confidence"),
            "latency_ms": meta["latency_ms"], "ok": True, "error": None,
        })
    for r in results:
        if not r.get("ok"):
            provider_rows.append({
                "name": r["name"], "model": r["model"], "bias": "-",
                "confidence": None, "latency_ms": r.get("latency_ms", 0),
                "ok": False, "error": r.get("error"),
            })

    if not ok:
        return {
            "error": "كل مفاتيح الذكاء الاصطناعي فشلت. راجع المفاتيح والموديلات والـ Base URL.",
            "market": market, "providers": provider_rows,
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "execution": "ANALYSIS ONLY - no order was placed",
        }

    votes = {}
    for obj in parsed:
        bias = str(obj.get("bias", "WAIT")).upper()
        votes[bias] = votes.get(bias, 0) + 1
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
    winner = ranked[0][0] if ranked else "WAIT"
    if winner not in ("BUY", "SELL", "WAIT"):
        winner = "WAIT"
    if winner in ("BUY", "SELL") and len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        winner = "WAIT"  # no clear majority

    pool = [o for o in parsed if str(o.get("bias", "")).upper() == winner] or parsed
    conf = _median([o.get("confidence") for o in pool])
    conf = max(0, min(100, int(round(conf)))) if conf is not None else 0

    zone = [_median([_nth(o.get("entry_zone") or [], i) for o in pool]) for i in (0, 1)]
    sl = _median([o.get("stop_loss") for o in pool])
    tps = []
    for i in range(3):
        v = _median([_nth(o.get("take_profit") or [], i) for o in pool])
        if v is not None:
            tps.append(v)
    rr = _median([o.get("risk_reward") for o in pool])
    if rr is None and sl is not None and zone[0] is not None and tps:
        risk = abs(zone[0] - sl)
        reward = abs(tps[0] - zone[0])
        rr = round(reward / risk, 2) if risk else None

    factors = []
    for obj in parsed:
        for f in obj.get("key_factors") or []:
            t = str(f).strip()
            if t and t not in factors:
                factors.append(t)

    best = max(parsed, key=lambda o: float(o.get("confidence") or 0))
    return {
        "symbol": snapshot.get("symbol"),
        "label": snapshot.get("label"),
        "interval": snapshot.get("interval"),
        "bias": winner,
        "confidence": conf,
        "thesis": _text(pool, "thesis"),
        "key_factors": factors[:8],
        "news_impact": _text(best, "news_impact"),
        "entry_zone": zone,
        "stop_loss": sl,
        "take_profit": tps,
        "risk_reward": rr,
        "invalidation": _text(best, "invalidation"),
        "wait_conditions": _text(best, "wait_conditions"),
        "time_horizon": _text(best, "time_horizon"),
        "risk_note": _text(best, "risk_note"),
        "market": market,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "engine": "all enabled AI models in parallel — consensus",
        "consensus": {
            "providers_ok": ok,
            "providers_total": len(results),
            "agreement": round(ok and len(pool) / ok or 0, 2),
            "votes": votes,
        },
        "providers": provider_rows,
        "execution": "ANALYSIS ONLY - no order was placed",
    }


def analyze(symbol, interval, cfg):
    snapshot = market_data.market_snapshot(symbol, interval, cfg)
    messages = build_messages(snapshot)
    results = ai_providers.analyze_all(
        messages,
        mode=cfg.get("mode", "ensemble"),
        timeout=int(cfg.get("timeout_seconds", 75)),
        temperature=float(cfg.get("temperature", 0.15)),
        max_providers=int(cfg.get("max_providers", 25)),
    )
    return aggregate(results, snapshot)
