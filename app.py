# -*- coding: utf-8 -*-
"""Market AI Trade App — phone PWA, all AI keys at once. ANALYSIS ONLY."""
import json
import os
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import ai_providers
import market_ai
import market_data

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
CONFIG_PATH = os.path.join(HERE, "config.json")
KEYS_PATH = os.path.join(HERE, "keys.local.json")
TOKEN_PATH = os.path.join(HERE, "web_token.txt")
MARKET_PATH = os.path.join(HERE, "market.json")
PORT = 8002


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


CFG = load_json(CONFIG_PATH, {})
try:
    TOKEN = open(TOKEN_PATH, "r", encoding="utf-8").read().strip()
except OSError:
    TOKEN = secrets.token_urlsafe(14)
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(TOKEN)

INTERVAL = max(1, int(CFG.get("auto_analysis_minutes", 30))) * 60
state = {
    "busy": False, "result": None, "error": None, "started": None,
    "finished": None, "reason": None,
    "auto": {"enabled": bool(CFG.get("auto_analysis_enabled", False)),
             "interval_minutes": INTERVAL // 60, "last_run": None,
             "next_run": time.time() + 20, "runs": 0},
}

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".webmanifest": "application/manifest+json", ".png": "image/png"}


def public_config():
    providers = ai_providers.public_providers()
    return {
        "mode": CFG.get("mode", "ensemble"),
        "timeout_seconds": int(CFG.get("timeout_seconds", 75)),
        "default_symbol": CFG.get("default_symbol", "XAUUSD"),
        "default_interval": CFG.get("default_interval", "H1"),
        "symbols": [{"id": k, "label": v["label"], "kind": v["kind"]}
                    for k, v in market_data.SYMBOLS.items()],
        "intervals": list(market_data.INTERVALS.keys()),
        "providers": providers,
        "providers_with_keys": sum(1 for p in providers if p["has_key"]),
        "auto": dict(state["auto"]),
    }


def status_payload():
    auto = dict(state["auto"])
    auto["next_run_in"] = max(0, int(auto["next_run"] - time.time()))
    r = state["result"] or {}
    return {
        "busy": state["busy"], "error": state["error"],
        "has_result": state["result"] is not None,
        "result_summary": {"bias": r.get("bias"), "confidence": r.get("confidence"),
                           "symbol": r.get("symbol"), "consensus": r.get("consensus"),
                           "generated_at": r.get("generated_at")} if r else {},
        "providers_with_keys": sum(1 for p in ai_providers.public_providers() if p["has_key"]),
        "auto": auto,
    }


def save_providers(providers):
    with open(KEYS_PATH, "w", encoding="utf-8") as f:
        json.dump({"providers": providers}, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(KEYS_PATH, 0o600)
    except OSError:
        pass


def provider_file_data():
    path = KEYS_PATH if os.path.exists(KEYS_PATH) else os.path.join(HERE, "keys.example.json")
    return load_json(path, {"providers": []})


def mutate_provider(body):
    data = provider_file_data()
    providers = data.setdefault("providers", [])
    action = str(body.get("action", "")).lower()
    name = str(body.get("name", "")).strip()
    if not name:
        raise ValueError("لازم تكتب اسم المزود")
    idx = next((i for i, p in enumerate(providers)
                if str(p.get("name", "")).lower() == name.lower()), None)
    if action == "remove":
        if idx is not None:
            providers.pop(idx)
        save_providers(providers)
        return
    if action == "toggle":
        if idx is None:
            raise ValueError("المزود مش موجود")
        providers[idx]["enabled"] = not bool(providers[idx].get("enabled", True))
        save_providers(providers)
        return
    if action in ("add", "update"):
        item = providers[idx] if idx is not None else {}
        item["name"] = name
        item["base_url"] = str(body.get("base_url") or item.get("base_url", "")).strip().rstrip("/")
        item["model"] = str(body.get("model") or item.get("model", "")).strip()
        key = str(body.get("api_key") or "").strip()
        if key:
            item["api_key"] = key
        item.setdefault("api_key", "")
        item["enabled"] = bool(body.get("enabled", True))
        if not item["base_url"] or not item["model"]:
            raise ValueError("لازم Base URL والموديل")
        if idx is None:
            providers.append(item)
        else:
            providers[idx] = item
        save_providers(providers)
        return
    raise ValueError("إجراء غير معروف")


def run_analysis(payload):
    try:
        state["result"] = market_ai.analyze(
            payload.get("symbol") or CFG.get("default_symbol", "XAUUSD"),
            payload.get("interval") or CFG.get("default_interval", "H1"), CFG)
        state["error"] = None
    except Exception as exc:
        state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        state["busy"] = False
        state["finished"] = time.time()
        state["auto"]["next_run"] = time.time() + INTERVAL


def start_analysis(payload, reason="manual"):
    if state["busy"]:
        return False
    state["busy"] = True
    state["started"] = time.time()
    state["reason"] = reason
    state["error"] = None
    threading.Thread(target=run_analysis, args=(payload,), daemon=True).start()
    return True


def scheduler_loop():
    while True:
        now = time.time()
        saved = load_json(MARKET_PATH, None)
        if (state["auto"]["enabled"] and not state["busy"] and saved
                and now >= state["auto"]["next_run"]):
            if start_analysis(saved, "auto"):
                state["auto"]["last_run"] = now
                state["auto"]["runs"] += 1
        time.sleep(3)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def _json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return {}

    def _authorized(self):
        return secrets.compare_digest(self.headers.get("X-Token") or "", TOKEN)

    def _static(self, path):
        rel = path.lstrip("/") or "index.html"
        full = os.path.abspath(os.path.join(STATIC, rel))
        root = os.path.abspath(STATIC)
        if not full.startswith(root + os.sep) and full != os.path.join(root, "index.html"):
            return self._json({"error": "not found"}, 404)
        if not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        with open(full, "rb") as f:
            body = f.read()
        ext = os.path.splitext(full)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        if ext in (".webmanifest", ".png"):
            self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        if ext == ".html":
            body = body.replace(b"__TOKEN__", TOKEN.encode("utf-8"))
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html", "/style.css", "/manifest.webmanifest",
                    "/sw.js", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"):
            return self._static(path)
        if not self._authorized():
            return self._json({"error": "unauthorized"}, 401)
        if path == "/api/status":
            return self._json(status_payload())
        if path == "/api/config":
            return self._json(public_config())
        if path == "/api/result":
            return self._json(state["result"] or {})
        if path == "/api/snapshot":
            q = parse_qs(parsed.query)
            try:
                snap = market_data.market_snapshot(
                    (q.get("symbol") or [CFG.get("default_symbol", "XAUUSD")])[0],
                    (q.get("interval") or [CFG.get("default_interval", "H1")])[0], CFG)
                return self._json(snap)
            except Exception as exc:
                return self._json({"error": str(exc)}, 400)
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._authorized():
            return self._json({"error": "unauthorized"}, 401)
        path = urlparse(self.path).path
        body = self._body()
        try:
            if path == "/api/analyze":
                if not start_analysis(body, "manual"):
                    return self._json({"error": "التحليل شغال بالفعل"})
                return self._json({"ok": True})
            if path == "/api/providers":
                mutate_provider(body)
                return self._json({"ok": True, "providers": ai_providers.public_providers()})
            if path == "/api/auto":
                enabled = bool(body.get("enabled"))
                state["auto"]["enabled"] = enabled
                if enabled:
                    state["auto"]["next_run"] = time.time() + 5
                return self._json({"ok": True, "enabled": enabled})
            if path == "/api/market":
                with open(MARKET_PATH, "w", encoding="utf-8") as f:
                    json.dump({"symbol": body.get("symbol") or CFG.get("default_symbol", "XAUUSD"),
                               "interval": body.get("interval") or CFG.get("default_interval", "H1")}, f)
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=scheduler_loop, daemon=True).start()
    print(f"MARKET AI APP: http://{local_ip()}:{PORT}", flush=True)
    print("Analysis only. No order-placing endpoint.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
