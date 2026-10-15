"""Walk the strategy over historical candles. Same code path as live decisions.

    OANDA_TOKEN=... OANDA_ACCOUNT_ID=... python bot/backtest.py EUR_USD H1 2000
"""

from __future__ import annotations

import os
import sys

import pandas as pd

from oanda import Oanda
from strategy import Params, indicators


def backtest(df: pd.DataFrame, p: Params, allow_short: bool = True) -> dict:
    ind = indicators(df, p).dropna().reset_index(drop=True)
    side, entry, stop = "flat", 0.0, 0.0
    trades: list[float] = []

    def close_at(price: float) -> None:
        nonlocal side
        pnl = (price - entry) if side == "long" else (entry - price)
        trades.append(pnl)
        side = "flat"

    for i in range(1, len(ind)):
        row, prev = ind.iloc[i], ind.iloc[i - 1]
        if side == "long":
            stop = max(stop, row["close"] - row["atr"])
            if row["low"] <= stop:
                close_at(stop)
        elif side == "short":
            stop = min(stop, row["close"] + row["atr"])
            if row["high"] >= stop:
                close_at(stop)

        above_now = row["ema_fast"] > row["ema_slow"]
        above_prev = prev["ema_fast"] > prev["ema_slow"]
        if above_now == above_prev:
            continue
        want = "long" if above_now else "short"
        if side != "flat" and side != want:
            close_at(row["close"])
        if want == "short" and not allow_short:
            continue
        if side == "flat":
            side, entry = want, row["close"]
            stop = entry - row["atr"] if want == "long" else entry + row["atr"]

    if side != "flat":
        close_at(float(ind.iloc[-1]["close"]))

    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    return {
        "bars": len(ind),
        "trades": len(trades),
        "win_rate": (len(wins) / len(trades)) if trades else 0.0,
        "net_price_move": sum(trades),
        "profit_factor": (gross_win / gross_loss) if gross_loss else float("inf"),
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (gross_loss / len(losses)) if losses else 0.0,
    }


def main() -> None:
    instrument = sys.argv[1] if len(sys.argv) > 1 else "EUR_USD"
    granularity = sys.argv[2] if len(sys.argv) > 2 else "H1"
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 2000
    api = Oanda(os.environ["OANDA_TOKEN"], os.environ["OANDA_ACCOUNT_ID"])
    candles = [c for c in api.candles(instrument, granularity, min(count, 5000)) if c.complete]
    df = pd.DataFrame(
        {
            "time": [c.time for c in candles],
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
        }
    )
    p = Params(
        ema_fast=int(os.environ.get("EMA_FAST", 20)),
        ema_slow=int(os.environ.get("EMA_SLOW", 50)),
        atr_period=int(os.environ.get("ATR_PERIOD", 14)),
        atr_mult=float(os.environ.get("ATR_MULT", 2.0)),
    )
    result = backtest(df, p)
    print(f"{instrument} {granularity} x{len(df)} bars, {p}")
    for k, v in result.items():
        print(f"  {k:16} {v:.5f}" if isinstance(v, float) else f"  {k:16} {v}")


if __name__ == "__main__":
    main()
