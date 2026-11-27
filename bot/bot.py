# -*- coding: utf-8 -*-
"""Demo-only MT5 scalping bot. Run --check first; live requires --live-demo.

Never put broker passwords or API keys in config.json. The terminal must already
be logged in. By default this script ONLY prints signals (dry run).
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
import time

import MetaTrader5 as mt5

import strategy

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
LOG_PATH = os.path.join(ROOT, "trades.csv")
SUMMARY_PATH = os.path.join(ROOT, "daily_summary.csv")
TF = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
      "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1}
TF_SEC = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}


def load_cfg():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def log_line(row):
    new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "event", "symbol", "side", "volume", "price",
                        "sl", "tp", "reason", "result"])
        w.writerow(row)


def out(msg):
    print("[" + dt.datetime.now().strftime("%H:%M:%S") + "] " + str(msg), flush=True)


def connect(cfg, require_demo=True):
    if not mt5.initialize():
        raise RuntimeError("MT5 initialize failed: " + str(mt5.last_error()))
    acc = mt5.account_info()
    term = mt5.terminal_info()
    if acc is None or term is None:
        raise RuntimeError("Cannot read account/terminal info")
    if require_demo and acc.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        raise RuntimeError("REFUSED: account is NOT demo (trade_mode=%s)" % acc.trade_mode)
    if not term.trade_allowed:
        raise RuntimeError("Trading is disabled in the terminal")
    symbol = cfg["symbol"]
    si = mt5.symbol_info(symbol)
    if si is None:
        raise RuntimeError("Symbol not found: " + symbol)
    if not si.visible and not mt5.symbol_select(symbol, True):
        raise RuntimeError("Cannot select symbol: " + symbol)
    out("CONNECTED demo=%s login=%s server=%s balance=%.2f %s" %
        (acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO, acc.login, acc.server, acc.balance, acc.currency))
    return acc, si


def candles(symbol, tf_name, count):
    rates = mt5.copy_rates_from_pos(symbol, TF[tf_name], 0, count)
    if rates is None or len(rates) < 60:
        raise RuntimeError("Not enough candles from MT5")
    now = int(time.time())
    out_list = []
    for r in rates:
        t = int(r["time"])
        if t + TF_SEC[tf_name] > now:   # skip the still-forming bar
            continue
        out_list.append({"t": t, "o": float(r["open"]), "h": float(r["high"]),
                         "l": float(r["low"]), "c": float(r["close"])})
    if len(out_list) < 60:
        raise RuntimeError("Not enough closed candles from MT5")
    return out_list


def check_edge(cfg, cs, si):
    st = strategy.stats(strategy.backtest(cs, cfg["strategy"], si.spread * si.point))
    out("BACKTEST %s %s n=%d win=%.1f%% PF=%.2f total=%.1fR DD=%.1fR avg_bars=%.1f" %
        (cfg["symbol"], cfg["timeframe"], st["trades"], st["win_rate"],
         st["profit_factor"], st["total_r"], st["max_dd"], st["avg_bars"]))
    if cfg.get("check_edge", True):
        ok = strategy.edge_ok(st, cfg.get("min_pf", 1.15),
                              cfg.get("min_trades", 100), cfg.get("max_dd", 10.0))
        out("EDGE GATE: " + ("PASS" if ok else "FAIL — bot will NOT trade"))
        return ok
    return True


def volume_for_risk(si, price, sl, balance, risk_pct):
    dist = abs(price - sl)
    if dist <= 0:
        return None
    risk_money = balance * risk_pct / 100.0
    contract = si.trade_contract_size or 100.0
    raw = risk_money / (dist * contract)
    step = si.volume_step or 0.01
    vmin = si.volume_min or step
    vmax = si.volume_max or 100.0
    vol = max(vmin, round(raw / step) * step)
    vol = min(vol, vmax)
    vol = round(vol, 4)
    return vol, round(dist * contract * vol, 2), round(risk_money, 2)


def _filling(si):
    mode = getattr(si, "filling_mode", 0)
    if mode == 1:
        return mt5.ORDER_FILLING_IOC
    if mode == 2:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def open_position(cfg, si, sig, dry):
    side = sig["side"]
    price = mt5.symbol_info_tick(si.name).ask if side == "BUY" else mt5.symbol_info_tick(si.name).bid
    acc = mt5.account_info()
    sized = volume_for_risk(si, price, sig["sl"], acc.balance, cfg["risk_pct"])
    if not sized:
        return False
    vol, actual_risk, wanted = sized
    out("SIGNAL %s price=%.3f sl=%.3f tp=%.3f volume=%.2f risk=$%.2f (target $%.2f)" %
        (side, price, sig["sl"], sig["tp"], vol, actual_risk, wanted))
    if actual_risk > wanted * 1.5:
        out("WARN: minimum lot makes real risk much higher than target — consider a bigger balance.")
    if dry:
        out("DRY RUN — no order sent")
        return False
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": si.name, "volume": vol,
        "type": mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": price, "sl": sig["sl"], "tp": sig["tp"], "deviation": 60,
        "magic": cfg["magic"], "comment": "scalp_m15", "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling(si),
    }
    result = mt5.order_send(req)
    out("ORDER result=%s retcode=%s" % (result.comment if result else "no result",
                                        result.retcode if result else "-"))
    log_line([dt.datetime.now().isoformat(timespec="seconds"), "open", si.name,
              side, vol, price, sig["sl"], sig["tp"], sig["reasons"][0],
              (result.comment if result else "no result")])
    return bool(result and result.retcode == mt5.TRADE_RETCODE_DONE)


def manage_positions(cfg, si, cs, dry):
    tf_name = cfg["timeframe"]
    secs = TF_SEC[tf_name]
    last_t = cs[-1]["t"]
    positions = mt5.positions_get(symbol=si.name) or []
    for p in positions:
        if p.magic != cfg["magic"]:
            continue
        held = (last_t - p.time) / float(secs)
        tick = mt5.symbol_info_tick(si.name)
        price = tick.bid if p.type == mt5.ORDER_TYPE_BUY else tick.ask
        if not dry and held >= cfg["strategy"]["max_hold_bars"]:
            close_type = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": si.name, "volume": p.volume,
                   "type": close_type, "position": p.ticket, "price": price,
                   "deviation": 60, "magic": cfg["magic"], "comment": "time_exit",
                   "type_filling": _filling(si)}
            r = mt5.order_send(req)
            out("TIME EXIT ticket=%s bars=%.1f result=%s" %
                (p.ticket, held, r.comment if r else "-"))
            log_line([dt.datetime.now().isoformat(timespec="seconds"), "time_exit",
                      si.name, "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                      p.volume, price, "", "", "held %.1f bars" % held,
                      (r.comment if r else "-")])
        elif not dry and cfg.get("break_even_at_r"):
            entry, sl, tp = p.price_open, p.sl, p.tp
            risk = abs(entry - sl)
            target = entry + cfg["break_even_at_r"] * risk if p.type == mt5.ORDER_TYPE_BUY else entry - cfg["break_even_at_r"] * risk
            reached = price >= target if p.type == mt5.ORDER_TYPE_BUY else price <= target
            new_sl = entry + si.spread * si.point if p.type == mt5.ORDER_TYPE_BUY else entry - si.spread * si.point
            if reached and abs((sl or 0) - new_sl) > si.point:
                req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": si.name,
                       "position": p.ticket, "sl": round(new_sl, si.digits),
                       "tp": tp, "magic": cfg["magic"]}
                r = mt5.order_send(req)
                out("BREAK EVEN ticket=%s result=%s" % (p.ticket, r.comment if r else "-"))


def day_state(cfg, si, day=None):
    """Trades, PnL and consecutive losing exits for a given UTC day."""
    day = day or dt.date.today()
    start = dt.datetime.combine(day, dt.time.min)
    end = start + dt.timedelta(days=1)
    deals = mt5.history_deals_get(start, end) or []
    mine = [d for d in deals if d.symbol == si.name and d.magic == cfg["magic"]]
    entries = [d for d in mine if d.entry == mt5.DEAL_ENTRY_IN]
    exits = sorted([d for d in mine if d.entry == mt5.DEAL_ENTRY_OUT],
                   key=lambda d: d.time, reverse=True)
    pnl = sum(d.profit + d.swap + d.commission for d in mine)
    consec = 0
    for d in exits:
        if (d.profit + d.swap + d.commission) < 0:
            consec += 1
        else:
            break
    return {"date": str(day), "trades": len(entries), "pnl": round(pnl, 2),
            "consec_losses": consec, "wins": sum(1 for d in exits if d.profit > 0),
            "losses": sum(1 for d in exits if d.profit <= 0)}


def daily_limits(cfg, si):
    acc = mt5.account_info()
    st = day_state(cfg, si)
    risk_money = acc.balance * cfg["risk_pct"] / 100.0
    limited = (st["trades"] >= cfg["max_trades_per_day"] or
               st["pnl"] <= -cfg["daily_loss_limit_r"] * risk_money or
               st["consec_losses"] >= cfg.get("max_consecutive_losses", 3))
    if limited:
        out("DAILY LIMIT: trades=%d pnl=%.2f consec_loss=%d — no new entries today" %
            (st["trades"], st["pnl"], st["consec_losses"]))
    return limited


def write_daily_summary(cfg, si):
    if not cfg.get("daily_summary", True):
        return
    st = day_state(cfg, si, dt.date.today() - dt.timedelta(days=1))
    if not st["trades"]:
        return
    new = not os.path.exists(SUMMARY_PATH)
    with open(SUMMARY_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "trades", "wins", "losses", "pnl_usd", "consec_losses"])
        w.writerow([st["date"], st["trades"], st["wins"], st["losses"],
                    st["pnl"], st["consec_losses"]])
    out("DAILY SUMMARY written: " + json.dumps(st, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="connect, backtest, edge gate; no orders")
    ap.add_argument("--once", action="store_true", help="check current signal once; no orders")
    ap.add_argument("--status", action="store_true", help="read-only status: day, positions, signal, edge")
    ap.add_argument("--live-demo", action="store_true", help="PLACE DEMO ORDERS (demo account only)")
    args = ap.parse_args()
    cfg = load_cfg()
    # Plain runs and --check/--once NEVER send orders. Only --live-demo does.
    dry = not args.live_demo
    acc, si = connect(cfg)
    cs = candles(cfg["symbol"], cfg["timeframe"], int(cfg["bars_to_fetch"]))
    out("DATA %d bars, spread=%d points (%.3f)" % (len(cs), si.spread, si.spread * si.point))
    if si.spread > cfg["max_spread_points"]:
        out("WARN: spread above limit — entries blocked by filter")
        spread_ok = False
    else:
        spread_ok = True
    if args.check:
        check_edge(cfg, cs, si)
        mt5.shutdown()
        return
    if not check_edge(cfg, cs, si):
        out("No trading: edge gate failed. Change parameters only after new backtest evidence.")
        mt5.shutdown()
        return
    if args.once or args.status:
        sig = strategy.signal(cs, cfg["strategy"], si.spread * si.point)
        st = day_state(cfg, si)
        positions = mt5.positions_get(symbol=si.name) or []
        mine = [p for p in positions if p.magic == cfg["magic"]]
        out("STATUS balance=%.2f spread=%d day=%s signal=%s positions=%d" %
            (mt5.account_info().balance, si.spread,
             json.dumps(st, ensure_ascii=False),
             (sig["side"] if sig else "WAIT"), len(mine)))
        if args.once:
            out("SIGNAL NOW: " + (json.dumps(sig, ensure_ascii=False) if sig else "WAIT / no setup"))
        mt5.shutdown()
        return
    if not dry:
        out("LIVE DEMO MODE — orders will be placed on the demo account")
    last_bar = cs[-1]["t"]
    edge_good = True
    last_edge_check = time.time()
    last_day = dt.date.today()
    while True:
        try:
            if dt.date.today() != last_day:
                write_daily_summary(cfg, si)
                last_day = dt.date.today()
            si = mt5.symbol_info(cfg["symbol"]) or si
            spread_ok = si.spread <= cfg["max_spread_points"]
            cs = candles(cfg["symbol"], cfg["timeframe"], int(cfg["bars_to_fetch"]))
            atrs = [x for x in strategy.atr(cs) if x]
            avg_atr = sum(atrs) / len(atrs) if atrs else 0.0
            ratio_ok = avg_atr > 0 and (si.spread * si.point) / avg_atr <= cfg.get("max_spread_atr_ratio", 0.05)
            if time.time() - last_edge_check >= 6 * 3600:
                edge_good = check_edge(cfg, cs, si)
                last_edge_check = time.time()
                if not edge_good:
                    out("TRADING PAUSED — edge gate failed on fresh data; existing positions still managed.")
            manage_positions(cfg, si, cs, dry)
            if cs[-1]["t"] != last_bar:
                last_bar = cs[-1]["t"]
                sig = strategy.signal(cs, cfg["strategy"], si.spread * si.point)
                if sig and spread_ok and ratio_ok and edge_good and not daily_limits(cfg, si):
                    open_position(cfg, si, sig, dry)
                elif sig:
                    out("SIGNAL skipped (filter/edge/limit): " + sig["side"])
        except Exception as exc:
            out("LOOP ERROR: %s — reconnecting in 10s" % exc)
            try:
                mt5.shutdown()
            except Exception:
                pass
            time.sleep(10)
            try:
                acc, si = connect(cfg)
                out("RECONNECTED")
            except Exception as exc2:
                out("RECONNECT FAILED: %s" % exc2)
        time.sleep(int(cfg["poll_seconds"]))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        out("stopped")
    finally:
        mt5.shutdown()
