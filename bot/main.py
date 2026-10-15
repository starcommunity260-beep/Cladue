"""One decision cycle: fetch closed candles, read signal, sync position, notify.

Run it on a schedule (GitHub Actions cron, a VPS cron, Termux). It is
idempotent per candle: the last handled bar time lives in STATE_FILE.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import requests

from oanda import Oanda, OandaError, OpenTrade, fmt
from strategy import Params, Signal, latest_signal, position_units


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None or v == "":
        if default is None:
            sys.exit(f"missing env {name}")
        return default
    return v


def env_bool(name: str, default: bool) -> bool:
    return env(name, "true" if default else "false").strip().lower() in ("1", "true", "yes")


class Config:
    def __init__(self) -> None:
        self.token = env("OANDA_TOKEN")
        self.account_id = env("OANDA_ACCOUNT_ID")
        self.live = env_bool("OANDA_LIVE", False)
        self.dry_run = env_bool("DRY_RUN", True)
        self.instrument = env("INSTRUMENT", "EUR_USD")
        self.granularity = env("GRANULARITY", "H1")
        self.candle_count = int(env("CANDLE_COUNT", "300"))
        self.risk_pct = float(env("RISK_PCT", "1.0"))
        self.max_units = int(env("MAX_UNITS", "100000"))
        self.allow_short = env_bool("ALLOW_SHORT", True)
        self.trailing = env_bool("TRAILING_STOP", True)
        self.params = Params(
            ema_fast=int(env("EMA_FAST", "20")),
            ema_slow=int(env("EMA_SLOW", "50")),
            atr_period=int(env("ATR_PERIOD", "14")),
            atr_mult=float(env("ATR_MULT", "2.0")),
        )
        self.state_file = Path(env("STATE_FILE", "bot/state.json"))
        self.tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")


class Notifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token, self.chat_id = token, chat_id
        self.lines: list[str] = []

    def log(self, line: str) -> None:
        print(line, flush=True)
        self.lines.append(line)

    def flush(self, send: bool) -> None:
        if not send or not (self.token and self.chat_id) or not self.lines:
            return
        text = "\n".join(self.lines)[:4000]
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=15,
            )
        except requests.RequestException as e:
            print(f"telegram failed: {e}", file=sys.stderr)


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except ValueError:
            return {}
    return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def closed_candles_frame(api: Oanda, cfg: Config) -> pd.DataFrame:
    raw = api.candles(cfg.instrument, cfg.granularity, cfg.candle_count)
    rows = [c for c in raw if c.complete]
    return pd.DataFrame(
        {
            "time": [c.time for c in rows],
            "open": [c.open for c in rows],
            "high": [c.high for c in rows],
            "low": [c.low for c in rows],
            "close": [c.close for c in rows],
        }
    )


def net_side(trades: list[OpenTrade]) -> str:
    units = sum(t.units for t in trades)
    if units > 0:
        return "long"
    if units < 0:
        return "short"
    return "flat"


def run() -> int:
    cfg = Config()
    out = Notifier(cfg.tg_token, cfg.tg_chat)
    api = Oanda(cfg.token, cfg.account_id, live=cfg.live)
    state = load_state(cfg.state_file)
    acted = False

    mode = "DRY-RUN" if cfg.dry_run else ("LIVE" if cfg.live else "PRACTICE")
    out.log(f"[{mode}] {cfg.instrument} {cfg.granularity} ema{cfg.params.ema_fast}/{cfg.params.ema_slow} atr{cfg.params.atr_period}x{cfg.params.atr_mult}")

    df = closed_candles_frame(api, cfg)
    sig: Signal = latest_signal(df, cfg.params)
    out.log(
        f"bar {sig.bar_time} close={sig.close:.5f} fast={sig.ema_fast:.5f} "
        f"slow={sig.ema_slow:.5f} stop_dist={sig.atr:.5f} -> {sig.direction}"
        + (" (CROSS)" if sig.crossed else "")
    )

    if state.get("last_bar") == sig.bar_time:
        out.log("already handled this bar, nothing to do")
        return 0

    trades = api.open_trades(cfg.instrument)
    side = net_side(trades)
    out.log(f"position: {side} ({sum(t.units for t in trades):.0f} units)")

    want = sig.direction if sig.crossed else None
    if want == "short" and not cfg.allow_short:
        want = "flat"

    if want is not None and want != side:
        if side != "flat":
            out.log(f"close {side} on opposite cross")
            if not cfg.dry_run:
                api.close_position(cfg.instrument, side)
            acted = True

        if want != "flat":
            acct = api.account()
            info = api.instrument_info(cfg.instrument)
            px = api.pricing(cfg.instrument)
            places = int(info["displayPrecision"])
            min_dist = float(info.get("minimumTrailingStopDistance", "0"))
            dist = max(sig.atr, min_dist)
            factor = px["home_factor_positive"] if want == "long" else px["home_factor_negative"]
            risk_amt = acct.nav * cfg.risk_pct / 100.0
            units = position_units(risk_amt, dist, factor, cfg.max_units)
            if units == 0:
                out.log("units computed as 0, skip entry")
            else:
                entry = px["ask"] if want == "long" else px["bid"]
                stop = entry - dist if want == "long" else entry + dist
                signed = units if want == "long" else -units
                out.log(
                    f"open {want} {units} units @~{entry:.{places}f} stop={stop:.{places}f} "
                    f"risk={risk_amt:.2f} {acct.currency}"
                )
                if not cfg.dry_run:
                    try:
                        api.market_order(
                            cfg.instrument,
                            signed,
                            stop_loss_price=fmt(stop, places),
                            trailing_distance=fmt(dist, places) if cfg.trailing else None,
                            tag=f"ema-atr:{sig.bar_time}",
                        )
                    except OandaError as e:
                        out.log(f"order failed: {e}")
                        out.flush(send=True)
                        return 1
                acted = True
    else:
        out.log("no change")

    state["last_bar"] = sig.bar_time
    save_state(cfg.state_file, state)
    out.flush(send=acted)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except OandaError as e:
        print(f"oanda error: {e}", file=sys.stderr)
        sys.exit(2)
