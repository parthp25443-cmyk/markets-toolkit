"""Black-Scholes Calculator (prices, d1/d2, N(d), Greeks, IV solver): page 3 of the Markets Toolkit."""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone

import pandas as pd
import streamlit as st

import options_core as oc

IST = timezone(timedelta(hours=5, minutes=30))
ss = st.session_state
MODES = ["Spot + dividend yield", "Futures price (Black-76)"]
TMODES = ["Expiry date & time", "Days to expiry"]
BASES = ["Calendar day (÷365)", "Trading day (÷252)"]

# ---- state -------------------------------------------------------------------------------
_now = datetime.now(IST).replace(tzinfo=None, second=0, microsecond=0)
for k, v in {
    "bs_mode": MODES[0], "bs_S": None, "bs_K": None, "bs_sig": None, "bs_r": None, "bs_q": None,
    "bs_tmode": TMODES[0], "bs_exp_date": None, "bs_exp_time": time(15, 30), "bs_val_date": _now.date(),
    "bs_val_time": _now.time(), "bs_days": None, "bs_basis": BASES[0], "bs_mC": None, "bs_mP": None,
    "bs_lot": oc.LOT_SIZE_NIFTY, "bs_note": None, "bs_hv": None,
}.items():
    ss.setdefault(k, v)

prefill = ss.pop("calc_prefill", None)          # sent by the scanner's "Open in calculator" button
if prefill:
    ss.bs_note = prefill.pop("note")
    ss.bs_hv = None
    for k, v in prefill.items():
        ss[k] = v


def use_stock(sym: str):
    h = ss.hist_vol[sym]
    ss.bs_mode, ss.bs_S, ss.bs_sig = MODES[0], round(h["last_price"], 2), round(h["annual_sd"] * 100, 2)
    ss.bs_K = ss.bs_mC = ss.bs_mP = None             # clear any strike/premiums left from another underlying
    ss.bs_hv = {"sym": sym, "sd": h["annual_sd"], "window": h["window"]}
    ss.bs_note = (f"Using {sym}: S = last close ₹{h['last_price']:,.2f} ({h['last_date']}), σ = 5-year historical "
                  f"volatility {h['annual_sd'] * 100:.2f}% from the Portfolio Dashboard. Enter the strike, rate, "
                  f"expiry and the stock's dividend yield.")


def set_sigma(v: float):
    ss.bs_sig = round(v, 2)


def dismiss_note():
    ss.bs_note = None


# ---- header ------------------------------------------------------------------------------
st.title("Black-Scholes Calculator")
st.caption("Prices a European option (Nifty index options are European) and gives d₁, d₂, N(d₁), N(d₂) and the "
           "Greeks. Use spot with a dividend yield, or the futures price (Black-76), which already includes "
           "interest and dividends.")

if ss.bs_note:
    c1, c2 = st.columns([12, 1])
    c1.info(ss.bs_note)
    c2.button("✕", on_click=dismiss_note, help="Dismiss")

hv = ss.get("hist_vol") or {}
with st.expander("Use a stock from your Portfolio Dashboard (historical volatility as σ)", expanded=False):
    if hv:
        a, b = st.columns([2, 3])
        sym = a.selectbox("Stock", list(hv), key="bs_hv_pick")
        h = hv[sym]
        b.markdown(f"**{sym}**: last close ₹{h['last_price']:,.2f} on {h['last_date']} · 5-year annual SD "
                   f"**{h['annual_sd'] * 100:.2f}%** ({h['window']})")
        b.button(f"Use {sym} as S and σ", on_click=use_stock, args=(sym,))
        st.caption("Historical volatility measures how much the stock *did* move. Implied volatility is what the "
                   "market expects it to move. Comparing the two is how traders judge whether options are cheap or "
                   "expensive.")
    else:
        st.caption("Add stocks on the Portfolio Dashboard page first. Their 5-year annual SD and last price will "
                   "show up here.")

# ---- inputs ------------------------------------------------------------------------------
left, right = st.columns([1.15, 1], gap="large")
with left:
    st.subheader("Inputs")
    st.radio("Underlying", MODES, key="bs_mode", horizontal=True)
    fut = ss.bs_mode == MODES[1]
    a, b = st.columns(2)
    a.number_input("Futures price F" if fut else "Spot price S", key="bs_S", min_value=0.0, step=0.05,
                   format="%.2f", placeholder="e.g. 24000")
    b.number_input("Strike price K", key="bs_K", min_value=0.0, step=50.0, format="%.2f", placeholder="e.g. 24000")
    a.number_input("Volatility σ (%)", key="bs_sig", min_value=0.0, step=0.1, format="%.2f", placeholder="e.g. 13")
    b.number_input("Risk-free rate r (%)", key="bs_r", step=0.1, format="%.2f", placeholder="e.g. 6")
    if not fut:
        a.number_input("Dividend yield q (%)", key="bs_q", step=0.01, format="%.2f", placeholder="0 if blank")

    st.radio("Time to expiry", TMODES, key="bs_tmode", horizontal=True)
    if ss.bs_tmode == TMODES[0]:
        a, b = st.columns(2)
        a.date_input("Expiry date", key="bs_exp_date", format="DD/MM/YYYY")
        b.time_input("Expiry time (IST)", key="bs_exp_time", step=60)
        a.date_input("Value as of (date)", key="bs_val_date", format="DD/MM/YYYY")
        b.time_input("Value as of (IST)", key="bs_val_time", step=60)
        T = ((datetime.combine(ss.bs_exp_date, ss.bs_exp_time) - datetime.combine(ss.bs_val_date, ss.bs_val_time))
             .total_seconds() / (365 * 86400)) if ss.bs_exp_date else math.nan
    else:
        st.number_input("Days to expiry (calendar days, decimals allowed)", key="bs_days", min_value=0.0,
                        step=1.0, format="%.2f", placeholder="e.g. 7")
        T = ss.bs_days / 365 if ss.bs_days else math.nan
    if math.isfinite(T):
        (st.caption if T > 0 else st.error)(f"T = {T * 365:.3f} days = {T:.6f} years" if T > 0
                                            else 'Expiry is not after the "value as of" time.')
    a, b = st.columns([2, 1])
    a.radio("Theta per", BASES, key="bs_basis", horizontal=True)
    b.number_input("Lot size (per-lot figures)", key="bs_lot", min_value=1, step=1)

S, K, r = ss.bs_S, ss.bs_K, (ss.bs_r / 100 if ss.bs_r is not None else None)
q = 0.0 if fut else (ss.bs_q or 0.0) / 100
sigma = ss.bs_sig / 100 if ss.bs_sig else None
mode = "fut" if fut else "spot"

# ---- IV solver ---------------------------------------------------------------------------
with right:
    st.subheader("IV solver")
    st.caption("Enter a market premium to find the volatility at which Black-Scholes gives exactly that price. "
               "It uses S, K, r, q and time from the inputs and ignores σ.")
    ready = bool(S and K and r is not None and math.isfinite(T) and T > 0)
    a, b = st.columns(2)
    ivs = {}
    for col, key, is_call, name in ((a, "bs_mC", True, "Call"), (b, "bs_mP", False, "Put")):
        col.number_input(f"{name} market premium (₹)", key=key, min_value=0.0, step=0.05, format="%.2f",
                         placeholder="e.g. 187.60" if is_call else "e.g. 157.70")
        prem = ss[key]
        if prem is None:
            continue
        if not ready:
            col.caption("Fill S, K, r and time to expiry first.")
            continue
        iv, msg = oc.implied_vol(prem, is_call, mode, S, K, T, r, q)
        if math.isfinite(iv):
            ivs[name] = iv
            col.metric(f"{name} implied volatility", f"{iv * 100:.2f}%")
            col.button("Use as σ", key=f"use_{key}", on_click=set_sigma, args=(iv * 100,))
        else:
            col.error(msg)
    st.caption("Call and put IVs on the same strike should be close. A gap usually means the underlying price or "
               "rate doesn't match what the market is using. Futures mode with the implied forward makes them "
               "line up.")
    if ss.bs_hv and ivs:
        hvv = ss.bs_hv["sd"]
        for name, iv in ivs.items():
            diff = iv - hvv
            label, why = (("expensive", "the market expects bigger moves than the stock's history shows") if diff > 0.01
                          else ("cheap", "the market expects smaller moves than the stock's history shows") if diff < -0.01
                          else ("fairly priced", "IV is within 1 point of the stock's historical volatility"))
            st.markdown(f"**Volatility check ({name.lower()}):** IV {iv * 100:.2f}% vs {ss.bs_hv['sym']} historical "
                        f"{hvv * 100:.2f}%. The option looks **{label}**: {why}.")

# ---- results -----------------------------------------------------------------------------
st.divider()
missing = [n for n, ok in (("futures price" if fut else "spot price", bool(S)), ("strike", bool(K)),
                           ("volatility", bool(sigma)), ("risk-free rate", r is not None),
                           ("time to expiry", math.isfinite(T) and T > 0)) if not ok]
if missing:
    st.info(f"Enter {', '.join(missing)} to see prices and Greeks.")
    st.stop()

o = oc.bs(mode, S, K, T, r, q, sigma)
B = 365 if ss.bs_basis == BASES[0] else 252
lot = int(ss.bs_lot)
intr_c, intr_p = max(S - K, 0), max(K - S, 0)
gap = o["parity_lhs"] - o["parity_rhs"]
mny = S / K

m = st.columns(4)
m[0].metric("Call price", f"₹{o['call']:,.2f}", help=f"Intrinsic ₹{intr_c:,.2f} + time value ₹{o['call'] - intr_c:,.2f}")
m[1].metric("Put price", f"₹{o['put']:,.2f}", help=f"Intrinsic ₹{intr_p:,.2f} + time value ₹{o['put'] - intr_p:,.2f}")
m[2].metric("Put-call parity check", "Holds" if abs(gap) < 1e-6 else f"{gap:.4f}",
            help=f"C − P = {o['parity_lhs']:,.2f} = S·e^(−qT) − K·e^(−rT)")
m[3].metric("Moneyness (S / K)", f"{mny:.4f}",
            help="At the money" if abs(mny - 1) < 0.0025 else ("Call ITM, put OTM" if mny > 1 else "Call OTM, put ITM"))
st.caption(f"Call: intrinsic ₹{intr_c:,.2f} + time value ₹{o['call'] - intr_c:,.2f} · "
           f"Put: intrinsic ₹{intr_p:,.2f} + time value ₹{o['put'] - intr_p:,.2f}")

a, b = st.columns([1, 1.6], gap="large")
with a:
    st.subheader("Intermediate values")
    iv_rows = [("d₁", o["d1"]), ("d₂ = d₁ − σ√T", o["d2"]), ("N(d₁)", o["Nd1"]), ("N(d₂)", o["Nd2"]),
               ("N(−d₁)", o["Nmd1"]), ("N(−d₂)", o["Nmd2"]), ("n(d₁), normal density", o["nd1"]),
               ("σ√T", sigma * math.sqrt(T)), ("e^(−rT)", o["dfr"]),
               ("e^(−qT)" + (" (= e^(−rT) in futures mode)" if fut else ""), o["dfq"])]
    st.dataframe(pd.DataFrame([{"Item": k, "Value": f"{v:.6f}"} for k, v in iv_rows]),
                 hide_index=True, use_container_width=True)
    st.caption(f"N(d₂) ≈ the risk-neutral probability that the call expires in the money ({o['Nd2'] * 100:.1f}%). "
               f"For the put it's N(−d₂) = {o['Nmd2'] * 100:.1f}%.")
with b:
    st.subheader("Greeks")
    day = "calendar" if B == 365 else "trading"
    g = pd.DataFrame([
        {"Greek": "Delta", "Call": f"{o['delta_c']:.4f}", "Put": f"{o['delta_p']:.4f}",
         "What it means (per unit)": f"Premium change per 1-point move in {'the futures price' if fut else 'the underlying'}"},
        {"Greek": "Gamma", "Call": f"{o['gamma']:.6f}", "Put": f"{o['gamma']:.6f}",
         "What it means (per unit)": f"Change in delta per 1-point move. A 100-point move shifts delta by {o['gamma'] * 100:.3f}"},
        {"Greek": "Theta", "Call": f"{o['theta_c'] / B:,.2f}", "Put": f"{o['theta_p'] / B:,.2f}",
         "What it means (per unit)": f"₹ lost per {day} day from time decay"},
        {"Greek": "Vega", "Call": f"{o['vega']:,.2f}", "Put": f"{o['vega']:,.2f}",
         "What it means (per unit)": "₹ change per 1 point change in IV (e.g. 13% → 14%)"},
        {"Greek": "Rho", "Call": f"{o['rho_c']:,.3f}", "Put": f"{o['rho_p']:,.3f}",
         "What it means (per unit)": "₹ change per 1% change in r" + (" (futures price held fixed)" if fut else "")},
    ])
    st.dataframe(g, hide_index=True, use_container_width=True)
    st.caption(f"Per lot of {lot}: call delta {o['delta_c']:.3f} is {o['delta_c'] * lot:,.1f} units of exposure, "
               f"theta costs ₹{abs(o['theta_c'] / B) * lot:,.0f} a day, and a 1-point rise in IV adds "
               f"₹{o['vega'] * lot:,.0f}.")

with st.expander("Formulas used"):
    st.latex(r"d_1=\frac{\ln(S/K)+(r-q+\tfrac{1}{2}\sigma^2)T}{\sigma\sqrt{T}},\qquad d_2=d_1-\sigma\sqrt{T}")
    st.latex(r"C=S e^{-qT}N(d_1)-K e^{-rT}N(d_2),\qquad P=K e^{-rT}N(-d_2)-S e^{-qT}N(-d_1)")
    st.latex(r"\Delta_C=e^{-qT}N(d_1),\quad \Delta_P=e^{-qT}\big(N(d_1)-1\big),\quad "
             r"\Gamma=\frac{e^{-qT}n(d_1)}{S\sigma\sqrt{T}},\quad \nu=\frac{S e^{-qT}n(d_1)\sqrt{T}}{100}")
    st.latex(r"\Theta_C=-\frac{S e^{-qT}n(d_1)\sigma}{2\sqrt{T}}-rKe^{-rT}N(d_2)+qSe^{-qT}N(d_1)")
    st.latex(r"\Theta_P=-\frac{S e^{-qT}n(d_1)\sigma}{2\sqrt{T}}+rKe^{-rT}N(-d_2)-qSe^{-qT}N(-d_1)")
    st.latex(r"\rho_C=\frac{KTe^{-rT}N(d_2)}{100},\qquad \rho_P=-\frac{KTe^{-rT}N(-d_2)}{100}")
    st.caption("Theta is divided by 365 (or 252) to give a per-day figure. Futures mode (Black-76) sets S = F and "
               "q = r, so d₁ = [ln(F/K) + σ²T/2]/(σ√T). Delta and gamma are then with respect to the futures price, "
               "and rho = −T × option price ÷ 100 (futures price held fixed). n(x) is the standard normal density "
               "and N(x) the cumulative probability.")
