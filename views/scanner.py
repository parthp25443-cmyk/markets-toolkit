"""Nifty Option-Chain Arbitrage Scanner: page 2 of the Markets Toolkit."""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, time, timedelta, timezone

import pandas as pd
import streamlit as st

import options_core as oc

IST = timezone(timedelta(hours=5, minutes=30))
ss = st.session_state


def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None, second=0, microsecond=0)


def years_between(d1, t1, d2, t2) -> float:
    return (datetime.combine(d1, t1) - datetime.combine(d2, t2)).total_seconds() / (365 * 86400)


def rs(x: float) -> str:
    return "–" if not math.isfinite(x) else f"{'−' if x < 0 else ''}₹{abs(x):,.0f}"


# ---- state ---------------------------------------------------------------------------
_now = now_ist()
for k, v in {
    "sc_chain": None, "sc_name": "", "sc_demo": False, "sc_err": "",
    "sc_exp_date": None, "sc_exp_time": time(15, 30), "sc_val_date": _now.date(), "sc_val_time": _now.time(),
    "sc_r": 6.0, "sc_lot": oc.LOT_SIZE_NIFTY, "sc_fut_bid": None, "sc_fut_ask": None, "sc_hold": True,
    "c_brok": 20.0, "c_stt_opt": 0.15, "c_stt_ex": 0.15, "c_stt_fut": 0.05, "c_exch_opt": 0.03503,
    "c_exch_fut": 0.00173, "c_sebi": 10.0, "c_stamp_opt": 0.003, "c_stamp_fut": 0.002, "c_gst": 18.0,
}.items():
    ss.setdefault(k, v)


def on_upload():
    f = ss.get("sc_file")
    if f is None:
        return
    try:
        chain = oc.parse_nse_csv(f.getvalue().decode("utf-8", errors="replace"))
    except oc.ChainError as e:
        ss.sc_err, ss.sc_chain = str(e), None
        return
    if ss.sc_demo:                                   # don't carry the demo's futures prices over
        ss.sc_fut_bid = ss.sc_fut_ask = None
    ss.sc_chain, ss.sc_name, ss.sc_demo, ss.sc_err = chain, f.name, False, ""
    exp = oc.expiry_from_filename(f.name)
    if exp:
        ss.sc_exp_date = exp


def load_demo():
    n = now_ist()
    ss.sc_exp_date, ss.sc_exp_time = (n + timedelta(days=7)).date(), time(15, 30)
    ss.sc_val_date, ss.sc_val_time = n.date(), n.time()
    ss.sc_fut_bid, ss.sc_fut_ask = 23998.0, 24002.0
    T = years_between(ss.sc_exp_date, ss.sc_exp_time, ss.sc_val_date, ss.sc_val_time)
    ss.sc_chain = oc.demo_chain(T, ss.sc_r / 100)
    ss.sc_name, ss.sc_demo, ss.sc_err = "demo data", True, ""


# ---- header and inputs ---------------------------------------------------------------
st.title("Nifty Option-Chain Arbitrage Scanner")
st.caption("Upload the CSV from NSE's option chain page. The scanner runs the model-free arbitrage checks "
           "(box spreads, put-call parity against futures, vertical-spread bounds and butterflies) on "
           "executable bid/ask prices, then subtracts STT, exchange charges, stamp duty, GST and brokerage. "
           "No Black-Scholes or IV is used here.")

left, right = st.columns([1, 1.3], gap="large")
with left:
    st.subheader("1 · Load option chain")
    st.file_uploader("NSE option-chain CSV", type=["csv"], key="sc_file", on_change=on_upload,
                     help="nseindia.com → Option Chain → choose NIFTY and the expiry → Download (.csv)")
    st.button("Load demo data", on_click=load_demo,
              help="Synthetic chain with one deliberately mispriced put, to see what a hit looks like")
    if ss.sc_err:
        st.error(ss.sc_err)
    elif ss.sc_chain:
        two = sum(1 for s in ss.sc_chain if s.c.ok and s.p.ok)
        st.success(f"Loaded {len(ss.sc_chain)} strikes ({two} with two-sided call and put quotes) from {ss.sc_name}."
                   + (" The 24,300 put is quoted ₹14 too high." if ss.sc_demo else ""))

with right:
    st.subheader("2 · Inputs")
    a, b, c = st.columns(3)
    a.date_input("Expiry date", key="sc_exp_date", format="DD/MM/YYYY",
                 help="Read from the NSE filename when possible. Holiday weeks expire a day early.")
    b.time_input("Expiry time (IST)", key="sc_exp_time", step=60)
    c.number_input("Your funding rate r (%)", key="sc_r", step=0.1, format="%.2f",
                   help="What your capital earns elsewhere. Only used for discounting.")
    a, b, c = st.columns(3)
    a.date_input("Prices as of (date)", key="sc_val_date", format="DD/MM/YYYY")
    b.time_input("Prices as of (IST)", key="sc_val_time", step=60, help="When you downloaded the CSV")
    c.number_input("Lot size", key="sc_lot", min_value=1, step=1)
    a, b, c = st.columns(3)
    a.number_input("Futures bid", key="sc_fut_bid", step=0.05, format="%.2f", placeholder="optional")
    b.number_input("Futures ask", key="sc_fut_ask", step=0.05, format="%.2f", placeholder="optional")
    c.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    c.caption("Same-expiry futures only (monthly expiry)")
    st.checkbox("Hold to expiry (charge STT on exercise of long ITM options at the implied forward)", key="sc_hold")
    with st.expander("Transaction cost settings"):
        a, b, c = st.columns(3)
        a.number_input("Brokerage ₹ / order", key="c_brok", step=1.0)
        b.number_input("STT option sell (% of premium)", key="c_stt_opt", step=0.01, format="%.3f")
        c.number_input("STT on exercise (% of intrinsic)", key="c_stt_ex", step=0.01, format="%.3f")
        a.number_input("STT futures sell (%)", key="c_stt_fut", step=0.01, format="%.3f")
        b.number_input("Exchange charge, options (% of premium)", key="c_exch_opt", step=0.0001, format="%.5f")
        c.number_input("Exchange charge, futures (%)", key="c_exch_fut", step=0.0001, format="%.5f")
        a.number_input("SEBI fee (₹ per crore)", key="c_sebi", step=1.0)
        b.number_input("Stamp duty, options buy (%)", key="c_stamp_opt", step=0.001, format="%.4f")
        c.number_input("Stamp duty, futures buy (%)", key="c_stamp_fut", step=0.001, format="%.4f")
        a.number_input("GST (%)", key="c_gst", step=1.0)
        st.caption("STT rates are the post-Budget 2026 rates (effective 1 April 2026). Exchange charges vary, "
                   "so check your broker's contract note.")

chain = ss.sc_chain
if not chain:
    st.info("Upload an NSE option-chain CSV or load the demo data to run the scan.")
    st.stop()
if ss.sc_exp_date is None:
    st.warning("Set the expiry date to run the scan.")
    st.stop()
T = years_between(ss.sc_exp_date, ss.sc_exp_time, ss.sc_val_date, ss.sc_val_time)
if T <= 0:
    st.error('"Prices as of" is after expiry. Check the expiry date and time.')
    st.stop()

costs = oc.Costs(
    brokerage=ss.c_brok, stt_opt_sell=ss.c_stt_opt / 100, stt_exercise=ss.c_stt_ex / 100,
    stt_fut_sell=ss.c_stt_fut / 100, exch_opt=ss.c_exch_opt / 100, exch_fut=ss.c_exch_fut / 100,
    sebi=ss.c_sebi / 1e7, stamp_opt_buy=ss.c_stamp_opt / 100, stamp_fut_buy=ss.c_stamp_fut / 100, gst=ss.c_gst / 100,
)
r = ss.sc_r / 100
R = oc.analyze(chain, T, r, int(ss.sc_lot), costs, ss.sc_hold, ss.sc_fut_bid, ss.sc_fut_ask)
all_opps = R["box"] + R["parity"] + R["vertical"] + R["fly"]
hits = sorted([o for o in all_opps if o.net > 0], key=lambda o: o.net, reverse=True)
gross_hits = [o for o in all_opps if o.gross > 0]

# ---- summary ---------------------------------------------------------------------------
st.divider()
m = st.columns(5)
m[0].metric("Time to expiry", f"{T * 365:.2f} days")
m[1].metric("Implied forward (ATM parity)", f"{R['f_star']:,.1f}")
m[2].metric("Discount factor e^(−rT)", f"{R['df']:.5f}")
m[3].metric("Strikes with full quotes", R["two_sided"])
m[4].metric("Net-positive opportunities", len(hits))

if hits:
    freq = Counter(k for o in hits for k in {l.K for l in o.legs if l.K is not None})
    k, cnt = freq.most_common(1)[0]
    same = (f" {cnt} of them involve the {k:,.0f} strike, so it's most likely one bad quote showing up "
            f"in many combinations.") if len(hits) > 1 and cnt / len(hits) > 0.6 else ""
    best = hits[0]
    msg = (f"**{len(hits)} combination{'s' if len(hits) > 1 else ''} show a positive net edge after costs.**{same} "
           f"Best: {best.type} {best.label}, {rs(best.net)} per set. Before acting, confirm the quotes are "
           f"still live and that the bid/ask quantity covers your size.")
    (st.warning if ss.sc_demo else st.success)(msg + (" (This is the planted mispricing in the demo data.)" if ss.sc_demo else ""))
elif gross_hits:
    st.info(f"**No arbitrage after costs.** {len(gross_hits)} combination{'s have' if len(gross_hits) > 1 else ' has'} "
            "a small positive edge before costs, but STT, exchange charges and brokerage wipe it out. "
            "That's what an efficient market looks like.")
else:
    st.info("**No arbitrage, even before costs.** On executable bid/ask prices, every check holds. "
            "The bid-ask spread alone closes any gap you'd see using LTPs.")


def opp_table(lst: list[oc.Opp], empty: str, rate: bool = False, top: int = 10):
    if not lst:
        st.caption(empty)
        return
    rows = []
    for o in lst[:top]:
        bd = o.breakdown
        row = {"Trade": o.type, "Strikes": o.label, "Legs (bid if selling, ask if buying)": " · ".join(l.text() for l in o.legs),
               "Edge / unit": o.edge_per_unit}
        if rate:
            row["Implied rate"] = ("below 0%" if o.rate < 0 else f"{o.rate * 100:.1f}%") + f" ({o.rate_label})"
        row.update({"Edge / set (₹)": o.gross, "Costs (₹)": o.costs, "Net (₹)": o.net,
                    "Cost breakdown": f"STT {bd['stt']:,.0f} · exercise STT {bd['stt_exercise']:,.0f} · exchange "
                                      f"{bd['exchange']:,.0f} · brokerage {bd['brokerage']:,.0f} · GST {bd['gst']:,.0f} · "
                                      f"stamp+SEBI {bd['stamp'] + bd['sebi']:,.0f}"})
        rows.append(row)
    df = pd.DataFrame(rows)
    sty = (df.style.format({"Edge / unit": "{:,.2f}", "Edge / set (₹)": "{:,.0f}", "Costs (₹)": "{:,.0f}", "Net (₹)": "{:,.0f}"})
           .map(lambda v: "background-color:#e6f3eb;color:#1d7a46;font-weight:600" if v > 0 else "color:#b03a2e",
                subset=["Net (₹)"])
           .map(lambda v: "color:#1d7a46" if v > 0 else "color:#b03a2e", subset=["Edge / unit"]))
    st.dataframe(sty, hide_index=True, use_container_width=True)
    if len(lst) > top:
        st.caption(f"Showing the {top} closest of {len(lst):,} combinations checked.")


st.subheader("Box spreads")
st.caption("Four options that pay exactly the strike gap at expiry, so this is really a fixed-income trade. "
           "The implied rate shows what the market is lending or borrowing at. It's the cleanest check for "
           "weekly expiries because it needs no futures, spot or dividends.")
opp_table(R["box"], "No box spreads could be formed.", rate=True)

st.subheader("Put-call parity vs futures")
st.caption("Conversion: sell call, buy put, buy futures. Reversal: the opposite. Both lock in K − F at expiry. "
           "STT on the futures sale (0.05% of ~₹15 lakh notional) dominates the cost of a reversal.")
opp_table(R["parity"], "Enter same-expiry futures bid/ask to run this check (monthly expiry only; "
          "weekly options have no matching future).")

st.subheader("Vertical-spread bounds")
st.caption("Checks every strike pair for price ordering and maximum spread value. Only pairs within ₹5/unit "
           "of a violation are listed.")
opp_table(R["vertical"], "No strike pair is anywhere near violating the bounds.")

st.subheader("Butterflies (1 : −2 : 1)")
st.caption("Equally spaced neighbouring strikes. A butterfly's payoff is never negative, so receiving a credit "
           "to open one is free money. A positive edge usually means one strike's quote is out of line.")
opp_table(R["fly"], "Not enough equally spaced strikes.")

# ---- chain table -------------------------------------------------------------------------
st.subheader("Chain with implied forwards")
st.caption("Each strike's mid prices imply a forward F = K + (C − P)·e^(rT). In a consistent chain these all "
           "agree. Strikes off by more than ₹3 from the median are highlighted.")
atm_k = min(chain, key=lambda s: abs(s.K - R["f_star"]) if math.isfinite(R["f_star"]) else 0).K
cdf = pd.DataFrame([{
    "Call bid": s.c.bid, "Call ask": s.c.ask, "Call IV": s.c.iv, "Strike": s.K,
    "Put bid": s.p.bid, "Put ask": s.p.ask, "Put IV": s.p.iv, "Implied fwd": s.fwd, "vs median": s.fwd_dev,
} for s in chain])
csty = (cdf.style.format({c: "{:,.2f}" for c in ["Call bid", "Call ask", "Put bid", "Put ask"]} |
                         {"Call IV": "{:.1f}", "Put IV": "{:.1f}", "Strike": "{:,.0f}", "Implied fwd": "{:,.1f}",
                          "vs median": "{:+,.1f}"}, na_rep="–")
        .map(lambda v: "background-color:#f8e9e7;color:#b03a2e" if isinstance(v, float) and math.isfinite(v) and abs(v) > 3 else "",
             subset=["vs median"])
        .apply(lambda row: ["background-color:#eaf1f7" if row["Strike"] == atm_k else "" for _ in row], axis=1))
st.dataframe(csty, hide_index=True, use_container_width=True, height=420)

# ---- link to calculator ------------------------------------------------------------------
st.subheader("Open a strike in the Black-Scholes Calculator")
strikes = [s.K for s in chain]
a, b = st.columns([1, 2])
k_sel = a.selectbox("Strike", strikes, index=strikes.index(atm_k), format_func=lambda k: f"{k:,.0f}")
b.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
if b.button("Open in calculator →", type="primary"):
    s = next(x for x in chain if x.K == k_sel)
    F = R["f_star"]
    otm_call = s.K >= F
    iv = s.c.iv if otm_call else s.p.iv
    src = f"NSE's {'call' if otm_call else 'put'} IV"
    if not (iv > 0):
        mid = s.c.mid if otm_call else s.p.mid
        iv_dec, _ = oc.implied_vol(mid, otm_call, "fut", F, s.K, T, r) if math.isfinite(mid) else (math.nan, "")
        iv, src = iv_dec * 100, f"IV solved from the {'call' if otm_call else 'put'} mid price"
    ss.calc_prefill = {
        "bs_mode": "Futures price (Black-76)", "bs_S": round(F, 2), "bs_K": float(s.K),
        "bs_sig": round(iv, 2) if math.isfinite(iv) else None, "bs_r": ss.sc_r, "bs_tmode": "Expiry date & time",
        "bs_exp_date": ss.sc_exp_date, "bs_exp_time": ss.sc_exp_time, "bs_val_date": ss.sc_val_date,
        "bs_val_time": ss.sc_val_time,
        "bs_mC": round(s.c.mid, 2) if math.isfinite(s.c.mid) else None,
        "bs_mP": round(s.p.mid, 2) if math.isfinite(s.p.mid) else None,
        "bs_lot": int(ss.sc_lot),
        "note": (f"Pre-filled from the scanner: strike {s.K:,.0f}, futures mode with F = {F:,.1f} (the forward "
                 f"implied by ATM put-call parity), σ from {src} (the out-of-the-money side, which traders use as "
                 f"the strike's volatility). The call and put mid prices are loaded into the IV solver."),
    }
    st.switch_page("views/calculator.py")

with st.expander("How to read the results"):
    st.markdown(
        "- **Edge** is the locked-in profit in today's rupees per 1 lot-set, using the bid for anything you sell and the "
        "ask for anything you buy. Butterflies are 1 : 2 : 1 lots. **Net** = edge − all costs.\n"
        "- **Box spread**: buy call K₁, sell call K₂, buy put K₂, sell put K₁ pays exactly K₂ − K₁ at expiry. A *long* box "
        "earning above your rate, or a *short* box borrowing below it, is the arbitrage.\n"
        "- **Conversion / reversal** checks C − P = (F − K)·e^(−rT) against same-expiry futures. Futures mark-to-market "
        "and margin funding are ignored.\n"
        "- **Vertical bounds**: a higher-strike call can't be worth more than a lower-strike call, and a call spread can't "
        "cost more than the discounted strike gap (same for puts). **Butterfly**: a 1 : −2 : 1 butterfly can't be worth "
        "less than zero.\n"
        "- **Not included**: bid/ask *quantity* (top of book may be 1 lot), slippage between legs, margin blocked until "
        "expiry, and the delay between NSE's snapshot and your order. A hit is a lead to check live, not a trade."
    )
