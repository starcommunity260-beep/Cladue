"""EMA cross entry, ATR stop. Pure functions over closed candles; no I/O."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Params:
    ema_fast: int = 20
    ema_slow: int = 50
    atr_period: int = 14
    atr_mult: float = 2.0


@dataclass(frozen=True)
class Signal:
    bar_time: str
    close: float
    atr: float
    ema_fast: float
    ema_slow: float
    direction: str  # "long" | "short" | "flat"
    crossed: bool  # True only on the bar where fast crossed slow


def indicators(df: pd.DataFrame, p: Params) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = out["close"].ewm(span=p.ema_fast, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=p.ema_slow, adjust=False).mean()
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.ewm(alpha=1.0 / p.atr_period, adjust=False).mean() * p.atr_mult
    return out


def latest_signal(df: pd.DataFrame, p: Params) -> Signal:
    """df must contain only completed candles, oldest first."""
    need = max(p.ema_slow, p.atr_period) * 3
    if len(df) < need:
        raise ValueError(f"need at least {need} closed candles, got {len(df)}")
    ind = indicators(df, p)
    last, prev = ind.iloc[-1], ind.iloc[-2]
    above_now = last["ema_fast"] > last["ema_slow"]
    above_prev = prev["ema_fast"] > prev["ema_slow"]
    direction = "long" if above_now else "short"
    return Signal(
        bar_time=str(last["time"]),
        close=float(last["close"]),
        atr=float(last["atr"]),
        ema_fast=float(last["ema_fast"]),
        ema_slow=float(last["ema_slow"]),
        direction=direction,
        crossed=above_now != above_prev,
    )


def position_units(
    risk_amount_home: float,
    stop_distance_price: float,
    quote_to_home: float,
    max_units: int,
) -> int:
    """Units such that a stop-out loses ~risk_amount_home in account currency."""
    if stop_distance_price <= 0 or quote_to_home <= 0:
        return 0
    loss_per_unit_home = stop_distance_price * quote_to_home
    units = int(risk_amount_home / loss_per_unit_home)
    return max(0, min(units, max_units))
