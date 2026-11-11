"""
Calculation engine for the Nifty options pages.

Two parts:
  1. Arbitrage scanner: reads NSE's option-chain CSV and runs model-free no-arbitrage checks
     (box spreads, put-call parity vs futures, vertical-spread bounds, butterflies) on
     executable bid/ask prices, then subtracts Indian transaction costs.
  2. Black-Scholes / Black-76: prices, d1/d2, N(d1)/N(d2), Greeks and an implied-volatility solver.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from datetime import date

from scipy.optimize import brentq
from scipy.stats import norm

NAN = float("nan")
LOT_SIZE_NIFTY = 65


# --------------------------------------------------------------------------------------
# NSE option-chain CSV
# --------------------------------------------------------------------------------------
@dataclass
class Quote:
    bid: float = NAN
    ask: float = NAN
    ltp: float = NAN
    iv: float = NAN
    oi: float = NAN
    vol: float = NAN

    @property
    def ok(self) -> bool:
        return self.bid > 0 and self.ask > 0 and self.ask >= self.bid

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2 if self.ok else NAN


@dataclass
class Strike:
    K: float
    c: Quote
    p: Quote
    fwd: float = NAN       # forward implied by this strike's mid prices
    fwd_dev: float = NAN   # difference from the median implied forward


class ChainError(Exception):
    pass


def _num(s) -> float:
    if s is None:
        return NAN
    s = str(s).replace(",", "").strip()
    if s in ("", "-", "--"):
        return NAN
    try:
        v = float(s)
    except ValueError:
        return NAN
    return v if math.isfinite(v) else NAN


def parse_nse_csv(text: str) -> list[Strike]:
    """Calls sit left of the STRIKE column and puts right of it (mirrored)."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    h = s_idx = -1
    for i, r in enumerate(rows[:15]):
        up = [x.strip().upper() for x in r]
        j = next((k for k, x in enumerate(up) if x.startswith("STRIKE")), -1)
        if j >= 0:
            h, s_idx = i, j
            break
    if h < 0:
        raise ChainError("Couldn't find a STRIKE column. Use the CSV from the Download button "
                         "on NSE's option chain page.")
    H = [x.strip().upper() for x in rows[h]]

    def find(pred, side):
        rng = range(s_idx - 1, -1, -1) if side < 0 else range(s_idx + 1, len(H))
        return next((j for j in rng if pred(H[j])), -1)

    preds = {
        "bid": lambda x: x.startswith("BID") and "QTY" not in x,
        "ask": lambda x: x.startswith("ASK") and "QTY" not in x,
        "ltp": lambda x: x == "LTP",
        "iv": lambda x: x == "IV",
        "oi": lambda x: x == "OI",
        "vol": lambda x: x.startswith("VOLUME"),
    }
    cols = {side: {k: find(p, -1 if side == "c" else 1) for k, p in preds.items()} for side in ("c", "p")}
    if min(cols["c"]["bid"], cols["c"]["ask"], cols["p"]["bid"], cols["p"]["ask"]) < 0:
        raise ChainError("Found the STRIKE column but not BID/ASK columns on both sides.")

    def get(r, j):
        return _num(r[j]) if 0 <= j < len(r) else NAN

    chain = []
    for r in rows[h + 1:]:
        K = get(r, s_idx)
        if not math.isfinite(K):
            continue
        chain.append(Strike(K, Quote(**{k: get(r, j) for k, j in cols["c"].items()}),
                            Quote(**{k: get(r, j) for k, j in cols["p"].items()})))
    if not chain:
        raise ChainError("No strike rows found under the header.")
    return sorted(chain, key=lambda s: s.K)


def expiry_from_filename(name: str | None) -> date | None:
    m = re.search(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", name or "")
    if not m:
        return None
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    mon = m.group(2).lower()
    if mon not in months:
        return None
    try:
        return date(int(m.group(3)), months.index(mon) + 1, int(m.group(1)))
    except ValueError:
        return None


# --------------------------------------------------------------------------------------
# Transaction costs
# --------------------------------------------------------------------------------------
@dataclass
class Costs:
    """Rates as fractions (0.0015 = 0.15%). STT rates are post-Budget 2026 (from 1 Apr 2026)."""
    brokerage: float = 20.0          # ₹ per order
    stt_opt_sell: float = 0.0015     # on premium, sell side
    stt_exercise: float = 0.0015     # on intrinsic value of long ITM options at expiry
    stt_fut_sell: float = 0.0005     # on futures notional, sell side
    exch_opt: float = 0.0003503      # on premium, both sides
    exch_fut: float = 0.0000173      # on notional, both sides
    sebi: float = 10 / 1e7           # ₹10 per crore of turnover
    stamp_opt_buy: float = 0.00003
    stamp_fut_buy: float = 0.00002
    gst: float = 0.18                # on brokerage + exchange + SEBI fees


@dataclass
class Leg:
    kind: str          # "C", "P" or "F"
    K: float | None
    side: int          # +1 buy, -1 sell
    px: float
    n: int = 1         # lots

    def text(self) -> str:
        what = "FUT" if self.kind == "F" else f"{self.K:,.0f} {'CE' if self.kind == 'C' else 'PE'}"
        return f"{'Buy' if self.side > 0 else 'Sell'} {str(self.n) + '× ' if self.n > 1 else ''}{what} @ {self.px:,.2f}"


@dataclass
class Opp:
    type: str
    label: str
    legs: list[Leg]
    edge_per_unit: float
    gross: float
    costs: float
    net: float
    breakdown: dict
    rate: float | None = None
    rate_label: str = ""


def leg_cost(leg: Leg, lot: int, c: Costs, hold_to_expiry: bool, f_star: float) -> dict:
    units = lot * leg.n
    v = leg.px * units
    out = dict(brokerage=c.brokerage, stt=0.0, exchange=0.0, sebi=v * c.sebi, stamp=0.0, stt_exercise=0.0)
    if leg.kind == "F":
        if leg.side < 0:
            out["stt"] = v * c.stt_fut_sell
        out["exchange"] = v * c.exch_fut
        if leg.side > 0:
            out["stamp"] = v * c.stamp_fut_buy
    else:
        if leg.side < 0:
            out["stt"] = v * c.stt_opt_sell
        out["exchange"] = v * c.exch_opt
        if leg.side > 0:
            out["stamp"] = v * c.stamp_opt_buy
            if hold_to_expiry and math.isfinite(f_star):
                intrinsic = max(f_star - leg.K, 0) if leg.kind == "C" else max(leg.K - f_star, 0)
                out["stt_exercise"] = intrinsic * units * c.stt_exercise
    out["gst"] = (out["brokerage"] + out["exchange"] + out["sebi"]) * c.gst
    out["total"] = sum(out.values())
    return out


def _opp(type_, label, legs, edge, lot, costs, hold, f_star, **extra) -> Opp:
    bd = {k: 0.0 for k in ("brokerage", "stt", "stt_exercise", "exchange", "stamp", "gst", "sebi")}
    total = 0.0
    for leg in legs:
        lc = leg_cost(leg, lot, costs, hold, f_star)
        total += lc["total"]
        for k in bd:
            bd[k] += lc[k]
    gross = edge * lot
    return Opp(type_, label, legs, edge, gross, total, gross - total, bd, **extra)


# --------------------------------------------------------------------------------------
# Arbitrage checks
# --------------------------------------------------------------------------------------
def analyze(chain: list[Strike], T: float, r: float, lot: int = LOT_SIZE_NIFTY,
            costs: Costs | None = None, hold_to_expiry: bool = True,
            fut_bid: float | None = None, fut_ask: float | None = None) -> dict:
    costs = costs or Costs()
    df = math.exp(-r * T)
    two = [s for s in chain if s.c.ok and s.p.ok]

    # forward implied by each strike's mid prices; F* = median of the 7 strikes nearest ATM
    for s in chain:
        s.fwd = s.K + (s.c.mid - s.p.mid) / df if (s.c.ok and s.p.ok) else NAN
    near = sorted(two, key=lambda s: abs(s.c.mid - s.p.mid))[:7]
    fw = sorted(s.fwd for s in near)
    f_star = fw[len(fw) // 2] if fw else NAN
    for s in chain:
        s.fwd_dev = s.fwd - f_star

    args = dict(lot=lot, costs=costs, hold=hold_to_expiry, f_star=f_star)
    box, parity, vertical, fly = [], [], [], []

    # 1) Box spreads
    for i, a in enumerate(two):
        for b in two[i + 1:]:
            W = b.K - a.K
            D = a.c.ask - b.c.bid + b.p.ask - a.p.bid
            if D > 0:
                box.append(_opp("Long box", f"{a.K:,.0f} / {b.K:,.0f}",
                                [Leg("C", a.K, 1, a.c.ask), Leg("C", b.K, -1, b.c.bid),
                                 Leg("P", b.K, 1, b.p.ask), Leg("P", a.K, -1, a.p.bid)],
                                W * df - D, **args, rate=math.log(W / D) / T, rate_label="you earn"))
            Cr = a.c.bid - b.c.ask + b.p.bid - a.p.ask
            if Cr > 0:
                box.append(_opp("Short box", f"{a.K:,.0f} / {b.K:,.0f}",
                                [Leg("C", a.K, -1, a.c.bid), Leg("C", b.K, 1, b.c.ask),
                                 Leg("P", b.K, -1, b.p.bid), Leg("P", a.K, 1, a.p.ask)],
                                Cr - W * df, **args, rate=math.log(W / Cr) / T, rate_label="you pay"))

    # 2) Put-call parity vs same-expiry futures
    if fut_bid and fut_ask and fut_bid > 0 and fut_ask >= fut_bid:
        for s in two:
            parity.append(_opp("Conversion", f"{s.K:,.0f}",
                               [Leg("C", s.K, -1, s.c.bid), Leg("P", s.K, 1, s.p.ask), Leg("F", None, 1, fut_ask)],
                               (s.c.bid - s.p.ask) + (s.K - fut_ask) * df, **args))
            parity.append(_opp("Reversal", f"{s.K:,.0f}",
                               [Leg("C", s.K, 1, s.c.ask), Leg("P", s.K, -1, s.p.bid), Leg("F", None, -1, fut_bid)],
                               (s.p.bid - s.c.ask) + (fut_bid - s.K) * df, **args))

    # 3) Vertical-spread bounds (listed only within ₹5/unit of a violation)
    cs = [s for s in chain if s.c.ok]
    ps = [s for s in chain if s.p.ok]
    for i, a in enumerate(cs):
        for b in cs[i + 1:]:
            W = b.K - a.K
            e1 = b.c.bid - a.c.ask
            if e1 > -5:
                vertical.append(_opp("Call monotonicity", f"{a.K:,.0f} / {b.K:,.0f}",
                                     [Leg("C", a.K, 1, a.c.ask), Leg("C", b.K, -1, b.c.bid)], e1, **args))
            e2 = a.c.bid - b.c.ask - W * df
            if e2 > -5:
                vertical.append(_opp("Call spread > width", f"{a.K:,.0f} / {b.K:,.0f}",
                                     [Leg("C", a.K, -1, a.c.bid), Leg("C", b.K, 1, b.c.ask)], e2, **args))
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            W = b.K - a.K
            e1 = a.p.bid - b.p.ask
            if e1 > -5:
                vertical.append(_opp("Put monotonicity", f"{a.K:,.0f} / {b.K:,.0f}",
                                     [Leg("P", b.K, 1, b.p.ask), Leg("P", a.K, -1, a.p.bid)], e1, **args))
            e2 = b.p.bid - a.p.ask - W * df
            if e2 > -5:
                vertical.append(_opp("Put spread > width", f"{a.K:,.0f} / {b.K:,.0f}",
                                     [Leg("P", b.K, -1, b.p.bid), Leg("P", a.K, 1, a.p.ask)], e2, **args))

    # 4) Butterflies (1 : -2 : 1) on consecutive, equally spaced strikes
    for kind, arr, key in (("C", cs, "c"), ("P", ps, "p")):
        for a, b, c in zip(arr, arr[1:], arr[2:]):
            if abs((b.K - a.K) - (c.K - b.K)) > 1e-9:
                continue
            qa, qb, qc = getattr(a, key), getattr(b, key), getattr(c, key)
            fly.append(_opp("Call butterfly" if kind == "C" else "Put butterfly",
                            f"{a.K:,.0f} / {b.K:,.0f} / {c.K:,.0f}",
                            [Leg(kind, a.K, 1, qa.ask), Leg(kind, b.K, -1, qb.bid, 2), Leg(kind, c.K, 1, qc.ask)],
                            2 * qb.bid - qa.ask - qc.ask, **args))

    for lst in (box, parity, vertical, fly):
        lst.sort(key=lambda o: o.net, reverse=True)
    return dict(df=df, f_star=f_star, two_sided=len(two), box=box, parity=parity, vertical=vertical, fly=fly)


def demo_chain(T: float, r: float) -> list[Strike]:
    """Synthetic chain around 24,000 with one deliberately overpriced put (24,300, +₹14)."""
    F, chain = 24000.0, []

    def tick(x):
        return max(0.05, round(x * 20) / 20)

    for K in range(22800, 25201, 50):
        m = math.log(K / F)
        iv = 0.125 - 0.35 * m + 1.8 * m * m

        def side(is_call):
            px = black76_price(F, K, T, r, iv, is_call)
            half = max(0.05, 0.004 * px + 0.15)
            bid = NAN if px - half < 0.05 else tick(px - half)
            return Quote(bid, tick(px + half), tick(px), round(iv * 100, 2))
        chain.append(Strike(float(K), side(True), side(False)))
    s = next(x for x in chain if x.K == 24300)
    s.p.bid, s.p.ask, s.p.ltp = tick(s.p.bid + 14), tick(s.p.ask + 14), tick(s.p.ltp + 14)
    return chain


# --------------------------------------------------------------------------------------
# Black-Scholes / Black-76
# --------------------------------------------------------------------------------------
def black76_price(F, K, T, r, sigma, is_call):
    o = bs(mode="fut", S=F, K=K, T=T, r=r, q=0.0, sigma=sigma)
    return o["call"] if is_call else o["put"]


def bs(mode: str, S: float, K: float, T: float, r: float, q: float, sigma: float) -> dict:
    """mode 'spot': S is spot, q is dividend yield.  mode 'fut': S is the futures price (Black-76).

    Theta is per year (divide by 365 or 252), vega per 1 vol point, rho per 1% rate.
    """
    qq = r if mode == "fut" else (q or 0.0)
    sqT = math.sqrt(T)
    vs = sigma * sqT
    d1 = (math.log(S / K) + (r - qq + sigma * sigma / 2) * T) / vs
    d2 = d1 - vs
    dfr, dfq = math.exp(-r * T), math.exp(-qq * T)
    Nd1, Nd2, Nmd1, Nmd2, nd1 = norm.cdf(d1), norm.cdf(d2), norm.cdf(-d1), norm.cdf(-d2), norm.pdf(d1)
    call = S * dfq * Nd1 - K * dfr * Nd2
    put = K * dfr * Nmd2 - S * dfq * Nmd1
    th = -S * dfq * nd1 * sigma / (2 * sqT)
    out = dict(
        d1=d1, d2=d2, Nd1=Nd1, Nd2=Nd2, Nmd1=Nmd1, Nmd2=Nmd2, nd1=nd1, dfr=dfr, dfq=dfq,
        call=call, put=put,
        delta_c=dfq * Nd1, delta_p=dfq * (Nd1 - 1),
        gamma=dfq * nd1 / (S * vs),
        vega=S * dfq * nd1 * sqT / 100,
        theta_c=th - r * K * dfr * Nd2 + qq * S * dfq * Nd1,
        theta_p=th + r * K * dfr * Nmd2 - qq * S * dfq * Nmd1,
        rho_c=K * T * dfr * Nd2 / 100, rho_p=-K * T * dfr * Nmd2 / 100,
        parity_lhs=call - put, parity_rhs=S * dfq - K * dfr,
    )
    if mode == "fut":                       # futures price held fixed when r moves
        out["rho_c"], out["rho_p"] = -T * call / 100, -T * put / 100
    return out


def implied_vol(target: float, is_call: bool, mode: str, S: float, K: float, T: float,
                r: float, q: float = 0.0) -> tuple[float, str]:
    """Volatility at which the model price equals the market premium. Returns (iv, message)."""
    if not (target and target > 0):
        return NAN, "Enter a positive premium."

    def price(s):
        o = bs(mode, S, K, T, r, q, s)
        return o["call"] if is_call else o["put"]
    lo, hi = 1e-4, 5.0
    p_lo, p_hi = price(lo), price(hi)
    if target < p_lo - 1e-9:
        return NAN, f"Below the option's minimum value (₹{p_lo:,.2f}), so no volatility fits."
    if target > p_hi:
        return NAN, "Premium too high: implied volatility above 500%."
    if abs(target - p_lo) <= 1e-9:
        return lo, ""
    return brentq(lambda s: price(s) - target, lo, hi, xtol=1e-12, maxiter=500), ""
