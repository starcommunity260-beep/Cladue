"""Thin OANDA v20 REST client. Practice and live differ only by host."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import requests

PRACTICE_HOST = "https://api-fxpractice.oanda.com"
LIVE_HOST = "https://api-fxtrade.oanda.com"


class OandaError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candle:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    complete: bool


@dataclass(frozen=True)
class OpenTrade:
    id: str
    instrument: str
    units: float  # positive long, negative short
    price: float
    stop_loss: float | None


@dataclass(frozen=True)
class AccountSnapshot:
    id: str
    currency: str
    nav: float
    balance: float
    margin_available: float


def fmt(x: float | Decimal, places: int) -> str:
    """Fixed-point string OANDA accepts: no exponent, no trailing junk."""
    q = Decimal(1).scaleb(-places)
    return str(Decimal(str(x)).quantize(q))


class Oanda:
    def __init__(self, token: str, account_id: str, live: bool = False, timeout: int = 20):
        self.account_id = account_id
        self.host = LIVE_HOST if live else PRACTICE_HOST
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept-Datetime-Format": "RFC3339",
            }
        )

    # ---- plumbing -------------------------------------------------------

    def _request(self, method: str, path: str, *, params=None, json=None) -> dict:
        r = self.session.request(
            method, f"{self.host}{path}", params=params, json=json, timeout=self.timeout
        )
        try:
            body = r.json()
        except ValueError:
            body = {"errorMessage": r.text}
        if r.status_code >= 400:
            msg = body.get("errorMessage") or body.get("rejectReason") or body
            raise OandaError(f"{method} {path} -> {r.status_code}: {msg}")
        return body

    # ---- account --------------------------------------------------------

    def account(self) -> AccountSnapshot:
        a = self._request("GET", f"/v3/accounts/{self.account_id}/summary")["account"]
        return AccountSnapshot(
            id=a["id"],
            currency=a["currency"],
            nav=float(a["NAV"]),
            balance=float(a["balance"]),
            margin_available=float(a["marginAvailable"]),
        )

    def instrument_info(self, instrument: str) -> dict:
        body = self._request(
            "GET",
            f"/v3/accounts/{self.account_id}/instruments",
            params={"instruments": instrument},
        )
        items = body.get("instruments", [])
        if not items:
            raise OandaError(f"instrument {instrument} not available on this account")
        return items[0]

    # ---- market data ----------------------------------------------------

    def candles(self, instrument: str, granularity: str, count: int) -> list[Candle]:
        body = self._request(
            "GET",
            f"/v3/instruments/{instrument}/candles",
            params={"granularity": granularity, "count": count, "price": "M"},
        )
        out = []
        for c in body["candles"]:
            m = c["mid"]
            out.append(
                Candle(
                    time=c["time"],
                    open=float(m["o"]),
                    high=float(m["h"]),
                    low=float(m["l"]),
                    close=float(m["c"]),
                    volume=int(c["volume"]),
                    complete=bool(c["complete"]),
                )
            )
        return out

    def pricing(self, instrument: str) -> dict:
        """Current bid/ask plus quote->home conversion factors."""
        body = self._request(
            "GET",
            f"/v3/accounts/{self.account_id}/pricing",
            params={"instruments": instrument},
        )
        p = body["prices"][0]
        factors = p.get("quoteHomeConversionFactors", {})
        return {
            "bid": float(p["bids"][0]["price"]),
            "ask": float(p["asks"][0]["price"]),
            "tradeable": bool(p.get("tradeable", True)),
            "home_factor_positive": float(factors.get("positiveUnits", 1.0)),
            "home_factor_negative": float(factors.get("negativeUnits", 1.0)),
        }

    # ---- positions ------------------------------------------------------

    def open_trades(self, instrument: str) -> list[OpenTrade]:
        body = self._request(
            "GET",
            f"/v3/accounts/{self.account_id}/openTrades",
        )
        out = []
        for t in body.get("trades", []):
            if t["instrument"] != instrument:
                continue
            sl = t.get("stopLossOrder")
            out.append(
                OpenTrade(
                    id=t["id"],
                    instrument=t["instrument"],
                    units=float(t["currentUnits"]),
                    price=float(t["price"]),
                    stop_loss=float(sl["price"]) if sl else None,
                )
            )
        return out

    def market_order(
        self,
        instrument: str,
        units: int,
        stop_loss_price: str,
        trailing_distance: str | None,
        tag: str,
    ) -> dict:
        order: dict = {
            "type": "MARKET",
            "instrument": instrument,
            "units": str(units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": stop_loss_price, "timeInForce": "GTC"},
            "clientExtensions": {"tag": tag, "comment": "ema-atr bot"},
        }
        if trailing_distance:
            order["trailingStopLossOnFill"] = {
                "distance": trailing_distance,
                "timeInForce": "GTC",
            }
        body = self._request(
            "POST", f"/v3/accounts/{self.account_id}/orders", json={"order": order}
        )
        if "orderCancelTransaction" in body:
            reason = body["orderCancelTransaction"].get("reason")
            raise OandaError(f"order cancelled: {reason}")
        return body

    def close_position(self, instrument: str, side: str) -> dict:
        key = "longUnits" if side == "long" else "shortUnits"
        return self._request(
            "PUT",
            f"/v3/accounts/{self.account_id}/positions/{instrument}/close",
            json={key: "ALL"},
        )
