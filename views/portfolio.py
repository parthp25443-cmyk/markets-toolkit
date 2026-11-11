"""
Portfolio Diversification Dashboard (NSE): page 1 of the Markets Toolkit.
Run the whole app with:  streamlit run app.py
"""

from __future__ import annotations

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

import portfolio_core as pc

# Chart colours (blue/red diverging around a grey midpoint; fixed categorical order)
BLUE, ORANGE, AQUA, RED = "#2a78d6", "#eb6834", "#1baf7a", "#e34948"
GREY_MID, INK, INK_2, MUTED, SURFACE = "#f0efec", "#0b0b0b", "#52514e", "#898781", "#fcfcfb"

# (page config is set once in app.py, the navigation entry point)


# --------------------------------------------------------------------------------------
# State and data loading
# --------------------------------------------------------------------------------------
if "symbols" not in st.session_state:
    st.session_state.symbols = []          # stocks fetched from Yahoo Finance
if "csv_stocks" not in st.session_state:
    st.session_state.csv_stocks = {}       # symbol -> StockData from uploaded CSVs


@st.cache_data(ttl=3600, show_spinner=False)
def load_stock(symbol: str, basis: str, day: str) -> pc.StockData:
    # `day` is part of the cache key so data refreshes once per day
    return pc.fetch_nse_prices(symbol, basis, pd.Timestamp(day))


def all_symbols() -> list[str]:
    return st.session_state.symbols + list(st.session_state.csv_stocks)


def pct(x: float, d: int = 3) -> str:
    return f"{x * 100:.{d}f}%"


TODAY = pd.Timestamp.today().normalize().strftime("%Y-%m-%d")

# --------------------------------------------------------------------------------------
# Sidebar: add / remove stocks
# --------------------------------------------------------------------------------------
with st.sidebar:
    st.header(f"Stocks ({len(all_symbols())}/{pc.MAX_STOCKS})")

    basis_label = st.radio(
        "Price series",
        ["Close (adjusted for splits & bonuses)", "Adj Close (also adjusted for dividends)"],
        help="Close gives price returns, like the class Excel. Adj Close gives total returns, "
             "including dividends.",
    )
    basis = "Adj Close" if basis_label.startswith("Adj") else "Close"

    with st.form("add_stock", clear_on_submit=True):
        raw = st.text_input("NSE symbol", placeholder="e.g. RELIANCE, TCS, M&M, HDFCBANK")
        submitted = st.form_submit_button("Add stock", type="primary")
    if submitted:
        try:
            sym = pc.normalise_symbol(raw)
            if sym in all_symbols():
                st.warning(f"{sym} is already in the portfolio.")
            elif len(all_symbols()) >= pc.MAX_STOCKS:
                st.error(f"You can add up to {pc.MAX_STOCKS} stocks. Remove one first.")
            else:
                with st.spinner(f"Fetching 5 years of {sym} prices…"):
                    sd = load_stock(sym, basis, TODAY)
                st.session_state.symbols.append(sym)
                st.success(f"Added {sym}: {len(sd.prices) - 1:,} daily returns "
                           f"from {sd.prices.index[0]:%d %b %Y}.")
        except pc.DataError as e:
            st.error(str(e))

    for sym in list(all_symbols()):
        c1, c2 = st.columns([4, 1])
        tag = " (CSV)" if sym in st.session_state.csv_stocks else ""
        c1.markdown(f"**{sym}**{tag}")
        if c2.button("✕", key=f"rm_{sym}", help=f"Remove {sym}"):
            if sym in st.session_state.csv_stocks:
                del st.session_state.csv_stocks[sym]
            else:
                st.session_state.symbols.remove(sym)
            st.rerun()
    if all_symbols() and st.button("Clear all"):
        st.session_state.symbols, st.session_state.csv_stocks = [], {}
        st.rerun()

    with st.expander("Yahoo not working? Upload a CSV instead"):
        st.caption("Download 5 years of daily data from nseindia.com (Get Quote → Historical "
                   "Data) or anywhere else. The file needs a Date column and a Close column.")
        up_sym = st.text_input("Symbol for this file", key="csv_sym")
        up = st.file_uploader("Price CSV", type=["csv"], key="csv_file")
        if st.button("Add from CSV"):
            try:
                if up is None:
                    raise pc.DataError("Choose a CSV file first.")
                sym = pc.normalise_symbol(up_sym)
                if sym in all_symbols():
                    st.warning(f"{sym} is already in the portfolio.")
                elif len(all_symbols()) >= pc.MAX_STOCKS:
                    st.error(f"You can add up to {pc.MAX_STOCKS} stocks.")
                else:
                    sd = pc.parse_price_csv(up, sym, pd.Timestamp(TODAY))
                    st.session_state.csv_stocks[sym] = sd
                    st.success(f"Added {sym} from CSV.")
                    st.rerun()
            except pc.DataError as e:
                st.error(str(e))

# --------------------------------------------------------------------------------------
# Main page
# --------------------------------------------------------------------------------------
st.title("Portfolio Diversification Dashboard")
st.caption("NSE stocks · last 5 years of daily prices · daily return = (Pₜ − Pₜ₋₁) / Pₜ₋₁ · "
           "SD = population SD (STDEV.P) · annualised with 252 trading days")

if not all_symbols():
    st.session_state.hist_vol = {}
    st.info("Add NSE stocks from the sidebar to begin. Up to 15 stocks; each needs at least "
            "5 years of price history. Try RELIANCE, M&M and LUPIN to reproduce the class Excel.")
    st.stop()

stocks: list[pc.StockData] = []
for sym in st.session_state.symbols:
    try:
        stocks.append(load_stock(sym, basis, TODAY))
    except pc.DataError as e:
        st.error(str(e))
stocks += list(st.session_state.csv_stocks.values())
for s in stocks:
    for w in s.warnings:
        st.warning(w)
if not stocks:
    st.stop()

try:
    prices = pc.align_prices(stocks)
except pc.DataError as e:
    st.error(str(e))
    st.stop()
returns = pc.daily_returns(prices)
stats = pc.stock_stats(returns)
names = list(prices.columns)
n = len(names)

# Share each stock's historical volatility and last price with the Black-Scholes Calculator page
st.session_state.hist_vol = {
    sym: {"annual_sd": float(stats.loc[sym, "Annual SD"]), "last_price": float(prices[sym].iloc[-1]),
          "last_date": prices.index[-1].strftime("%d %b %Y"),
          "window": f"{prices.index[0]:%d %b %Y} → {prices.index[-1]:%d %b %Y}"}
    for sym in names
}

st.markdown(
    f"**Data window:** {prices.index[0]:%d %b %Y} → {prices.index[-1]:%d %b %Y} · "
    f"{len(returns):,} daily returns per stock (only dates on which every selected stock traded)."
)
with st.expander("Data sources"):
    st.dataframe(pd.DataFrame({"Stock": [s.symbol for s in stocks],
                               "Source": [s.source for s in stocks],
                               "First price": [f"{s.prices.index[0]:%d %b %Y}" for s in stocks],
                               "Last price": [f"{s.prices.index[-1]:%d %b %Y}" for s in stocks]}),
                 hide_index=True)

# ---- 1. Each stock ------------------------------------------------------------------
st.subheader("1 · Return and risk of each stock")
pct_cfg = {c: st.column_config.NumberColumn(c, format="%.3f%%") for c in stats.columns}
st.dataframe((stats * 100).rename_axis("Stock"), column_config=pct_cfg)
st.caption("Each stock's Annual SD is its historical volatility. You can use it as σ in the "
           "Black-Scholes Calculator page.")

if n < 2:
    st.info("Add at least one more stock to see correlations and build a portfolio.")
    st.stop()

# ---- 2. Correlation -----------------------------------------------------------------
st.subheader("2 · Correlation between stocks")
corr = pc.correlation_matrix(returns)
cov = pc.covariance_matrix(returns)

cm = corr.rename_axis("Stock A").reset_index().melt(id_vars="Stock A", var_name="Stock B",
                                                     value_name="Correlation")
cm["ink"] = np.where(cm["Correlation"].abs() > 0.6, "#ffffff", INK)
base = alt.Chart(cm).encode(
    x=alt.X("Stock B:N", sort=names, title=None, axis=alt.Axis(labelAngle=-45, domain=False, ticks=False)),
    y=alt.Y("Stock A:N", sort=names, title=None, axis=alt.Axis(domain=False, ticks=False)),
)
heat = base.mark_rect(stroke=SURFACE, strokeWidth=2, cornerRadius=2).encode(
    color=alt.Color("Correlation:Q",
                    scale=alt.Scale(domain=[-1, 0, 1], range=[BLUE, GREY_MID, RED]),
                    legend=alt.Legend(title="Correlation", gradientLength=160)),
    tooltip=["Stock A:N", "Stock B:N", alt.Tooltip("Correlation:Q", format=".3f")],
)
labels = base.mark_text(fontSize=11 if n <= 10 else 9).encode(
    text=alt.Text("Correlation:Q", format=".2f"), color=alt.Color("ink:N", scale=None))
c_left, c_right = st.columns([3, 2])
c_left.altair_chart((heat + labels).properties(width="container", height=max(240, 38 * n)))

off = corr.where(~np.eye(n, dtype=bool)).stack().dropna()
lo_pair, hi_pair = off.idxmin(), off.idxmax()
with c_right:
    st.metric("Average correlation between pairs", f"{off.mean():.2f}")
    st.markdown(f"- **Least correlated:** {lo_pair[0]} & {lo_pair[1]} ({off.min():.2f})\n"
                f"- **Most correlated:** {hi_pair[0]} & {hi_pair[1]} ({off.max():.2f})")
    st.caption("The lower the correlation, the more one stock's falls are offset by another's, "
               "so portfolio risk ends up below the weighted average of the stocks' own risk.")
    with st.expander("Correlation table"):
        st.dataframe(corr.round(4))

# ---- 3. Weights and portfolio -------------------------------------------------------
st.subheader("3 · Portfolio weights")
method = st.radio("Weighting method", ["Equal weights", "Manual weights", "Minimum variance (long-only)"],
                  horizontal=True)

weights: pd.Series | None = None
if method == "Equal weights":
    weights = pd.Series(1 / n, index=names)
elif method == "Manual weights":
    editor_key = "w_" + "_".join(names)
    default_w = np.round(np.full(n, 100 / n), 2)
    default_w[-1] = round(100 - default_w[:-1].sum(), 2)     # so the defaults total exactly 100
    w_in = st.data_editor(
        pd.DataFrame({"Stock": names, "Weight (%)": default_w}),
        hide_index=True, disabled=["Stock"], key=editor_key,
        column_config={"Weight (%)": st.column_config.NumberColumn(min_value=0.0, max_value=100.0,
                                                                    step=0.01, format="%.2f")},
    )
    w_pct = w_in.set_index("Stock")["Weight (%)"].fillna(0.0).astype(float)
    total = float(w_pct.sum())
    if abs(total - 100) > 0.01:
        st.error(f"Weights add up to {total:.2f}%. They must total exactly 100%.")
    else:
        weights = w_pct / total
else:
    try:
        weights = pc.min_variance_weights(cov)
        st.caption("Weights that give the lowest possible portfolio SD with no short selling "
                   "(the same thing Excel Solver finds with weights ≥ 0 and summing to 1).")
    except pc.DataError as e:
        st.error(str(e))

if weights is None:
    st.stop()

port = pc.portfolio_stats(weights.values, stats["Avg daily return"], cov)

if method != "Manual weights":
    wt = pd.DataFrame({"Weight": weights * 100}).rename_axis("Stock").T
    st.dataframe(wt, column_config={c: st.column_config.NumberColumn(c, format="%.2f%%") for c in names})

st.subheader("4 · Portfolio result")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Expected return (daily)", pct(port["ret_d"], 4))
m2.metric("Risk – SD (daily)", pct(port["sd_d"], 3))
m3.metric("Expected return (annual)", pct(port["ret_a"], 2))
m4.metric("Risk – SD (annual)", pct(port["sd_a"], 2))

st.markdown("**What diversification did**")
d1, d2, d3 = st.columns(3)
d1.metric("Weighted avg of stocks' SD (annual)", pct(port["wavg_sd_a"], 2),
          help="What the portfolio's risk would be if every pair of stocks had correlation 1.")
d2.metric("Actual portfolio SD (annual)", pct(port["sd_a"], 2))
d3.metric("Risk removed by diversification", pct(port["risk_reduction"], 1))
st.caption(
    f"Expected return is the weighted average of the stocks' returns ({pct(port['ret_a'], 2)} a year), "
    f"so diversification doesn't change it. Because the stocks aren't perfectly correlated, the "
    f"portfolio's SD is {pct(port['sd_a'], 2)} instead of {pct(port['wavg_sd_a'], 2)}."
)
with st.expander("Formula used"):
    st.latex(r"E(R_p)=\sum_i w_i\,E(R_i)\qquad"
             r"\sigma_p=\sqrt{\sum_i\sum_j w_i w_j\,\sigma_i\sigma_j\,\rho_{ij}}")
    st.caption("For 3 stocks this expands to the Excel formula: (w₁σ₁)² + (w₂σ₂)² + (w₃σ₃)² "
               "+ 2ρ₁₂w₁w₂σ₁σ₂ + 2ρ₁₃w₁w₃σ₁σ₃ + 2ρ₂₃w₂w₃σ₂σ₃, then the square root.")

# ---- 5. Efficient frontier ----------------------------------------------------------
st.subheader("5 · Efficient frontier")
fr = pc.efficient_frontier(stats["Avg daily return"], cov)
ann_r, ann_s = pc.TRADING_DAYS * 100, np.sqrt(pc.TRADING_DAYS) * 100
mv_w = pc.min_variance_weights(cov)
mv = pc.portfolio_stats(mv_w.values, stats["Avg daily return"], cov)

fr_df = pd.DataFrame({"Risk": fr["sd_d"] * ann_s, "Return": fr["ret_d"] * ann_r})
pts = pd.concat([
    pd.DataFrame({"Label": names, "Type": "Individual stock",
                  "Risk": stats["Daily SD"].values * ann_s, "Return": stats["Avg daily return"].values * ann_r}),
    pd.DataFrame({"Label": ["Your portfolio"], "Type": ["Your portfolio"],
                  "Risk": [port["sd_a"] * 100], "Return": [port["ret_a"] * 100]}),
    pd.DataFrame({"Label": ["Min-variance"], "Type": ["Minimum-variance portfolio"],
                  "Risk": [mv["sd_a"] * 100], "Return": [mv["ret_a"] * 100]}),
], ignore_index=True)

x = alt.X("Risk:Q", title="Risk – annual SD (%)", scale=alt.Scale(zero=False),
          axis=alt.Axis(gridColor="#e1e0d9", domainColor="#c3c2b7", tickColor="#c3c2b7"))
y = alt.Y("Return:Q", title="Expected return – annual (%)", scale=alt.Scale(zero=False),
          axis=alt.Axis(gridColor="#e1e0d9", domainColor="#c3c2b7", tickColor="#c3c2b7"))
types = ["Individual stock", "Your portfolio", "Minimum-variance portfolio"]
line = alt.Chart(fr_df).mark_line(color=BLUE, strokeWidth=2).encode(x=x, y=y)
line_lbl = alt.Chart(fr_df.tail(1)).mark_text(align="left", dx=8, dy=-8, color=BLUE, fontSize=11).encode(
    x=x, y=y, text=alt.value("Efficient frontier"))
dots = alt.Chart(pts).mark_point(filled=True, size=110, opacity=1, stroke=SURFACE, strokeWidth=2).encode(
    x=x, y=y,
    color=alt.Color("Type:N", scale=alt.Scale(domain=types, range=[MUTED, ORANGE, AQUA]),
                    legend=alt.Legend(title=None, orient="bottom")),
    shape=alt.Shape("Type:N", scale=alt.Scale(domain=types, range=["circle", "diamond", "square"])),
    tooltip=["Label:N", alt.Tooltip("Return:Q", title="Return %", format=".2f"),
             alt.Tooltip("Risk:Q", title="Risk %", format=".2f")],
)
dot_lbl = alt.Chart(pts[pts["Type"] == "Individual stock"]).mark_text(
    align="left", dx=8, fontSize=11, color=INK_2).encode(x=x, y=y, text="Label:N")
st.altair_chart((line + line_lbl + dots + dot_lbl).properties(width="container", height=420)
                .configure_view(stroke=None))
st.caption("The blue curve shows the lowest risk you can get for each level of expected return "
           "using these stocks with no short selling. Portfolios on the curve are efficient. "
           "If your portfolio sits to the right of it, the same return is available with less risk.")

# ---- 6. Download --------------------------------------------------------------------
st.subheader("6 · Download")
xlsx = pc.build_excel(prices, returns, stats, corr, weights, port, method)
st.download_button("Download workings as Excel", xlsx, file_name="portfolio_dashboard.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
