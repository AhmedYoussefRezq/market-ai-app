# -*- coding: utf-8 -*-
"""Run every enabled AI API key at once. Keys never leave the local machine in responses."""
import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(HERE, "keys.local.json")
EXAMPLE_FILE = os.path.join(HERE, "keys.example.json")

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{12,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{12,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{16,}", re.I),
]


def _data():
    path = KEYS_FILE if os.path.exists(KEYS_FILE) else EXAMPLE_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"providers": []}


def _key_of(p):
    name = str(p.get("name", "")).upper()
    return p.get("api_key") or os.environ.get(f"AI_KEY_{name}", "")


def load_providers():
    out = []
    for p in _data().get("providers", []):
        key = _key_of(p)
        if not key:
            continue
        out.append({
            "name": str(p.get("name", "provider")),
            "base_url": str(p.get("base_url", "")).rstrip("/"),
            "model": str(p.get("model", "")),
            "api_key": key,
            "enabled": bool(p.get("enabled", True)),
        })
    return [p for p in out if p["enabled"] and p["base_url"] and p["model"]]


def public_providers():
    out = []
    for p in _data().get("providers", []):
        out.append({
            "name": p.get("name", "provider"),
            "base_url": p.get("base_url", ""),
            "model": p.get("model", ""),
            "enabled": bool(p.get("enabled", True)),
            "has_key": bool(_key_of(p)),
        })
    return out


def _clean(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _redact(message, key):
    text = str(message)
    if key:
        text = text.replace(key, "[redacted]")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:500]


def _post(url, headers, payload, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def call_provider(provider, messages, timeout=75, temperature=0.15):
    started = time.time()
    out = {"name": provider["name"], "model": provider["model"], "ok": False,
           "text": "", "error": None, "latency_ms": 0}
    headers = {"Authorization": "Bearer " + provider["api_key"],
               "Content-Type": "application/json"}
    payload = {"model": provider["model"], "messages": messages,
               "temperature": temperature, "response_format": {"type": "json_object"}}
    url = provider["base_url"] + "/chat/completions"
    try:
        try:
            data = _post(url, headers, payload, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in (400, 422):
                raise
            payload.pop("response_format", None)
            data = _post(url, headers, payload, timeout)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("empty choices")
        out["text"] = _clean(choices[0].get("message", {}).get("content", ""))
        out["ok"] = bool(out["text"])
    except Exception as exc:
        out["error"] = _redact(f"{type(exc).__name__}: {exc}", provider.get("api_key"))
    finally:
        out["latency_ms"] = int((time.time() - started) * 1000)
    return out


def analyze_all(messages, mode="ensemble", timeout=75, temperature=0.15,
                max_providers=25):
    providers = load_providers()[:max(1, int(max_providers))]
    if not providers:
        return []
    if mode == "failover":
        results = []
        for p in providers:
            r = call_provider(p, messages, timeout, temperature)
            results.append(r)
            if r["ok"]:
                break
        return results
    results = []
    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        futures = {pool.submit(call_provider, p, messages, timeout, temperature): p
                   for p in providers}
        for f in as_completed(futures):
            results.append(f.result())
    order = {p["name"]: i for i, p in enumerate(providers)}
    results.sort(key=lambda r: order.get(r["name"], 999))
    return results
