"""
Calculation engine for the Portfolio Diversification Dashboard.

Everything here mirrors the IAPM Excel workings, extended from 3 stocks to N:
  Daily return      = (P_t - P_{t-1}) / P_{t-1}                (Excel: =(B5-B4)/B4)
  Average return    = arithmetic mean of daily returns          (Excel: AVERAGE)
  Standard dev.     = population SD of daily returns            (Excel: STDEV.P)
  Correlation       = Pearson correlation of daily returns      (Excel: CORREL)
  Portfolio E(R)    = sum(w_i * E(R_i))                         (Excel: SUMPRODUCT)
  Portfolio SD      = sqrt( sum_i sum_j w_i w_j s_i s_j rho_ij ) (Excel: the long SQRT formula)
  Annualised return = daily mean x 252
  Annualised SD     = daily SD x sqrt(252)
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize

TRADING_DAYS = 252          # trading days per year, used for annualising
LOOKBACK_YEARS = 5          # history required for every stock
START_TOLERANCE_DAYS = 7    # allowance for holidays around the 5-year start date
STALE_DAYS = 10             # latest price older than this -> suspended / delisted
JUMP_THRESHOLD = 0.40       # |daily return| above this is flagged as a likely split/bonus
MAX_STOCKS = 15


class DataError(Exception):
    """Raised when a stock cannot be added (bad symbol, < 5 years of data, ...)."""


@dataclass
class StockData:
    symbol: str
    prices: pd.Series                       # date-indexed closing prices, 5-year window
    source: str = "Yahoo Finance (NSE)"
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# Data loading and validation
# --------------------------------------------------------------------------------------
def normalise_symbol(raw: str) -> str:
    s = (raw or "").strip().upper().replace(" ", "")
    if s.startswith("NSE:"):
        s = s[4:]
    if s.endswith(".NS"):
        s = s[:-3]
    if s.endswith(".BO") or s.startswith("BOM:"):
        raise DataError("Only NSE symbols are supported (e.g. RELIANCE, not RELIANCE.BO).")
    if not s:
        raise DataError("Enter an NSE symbol, e.g. RELIANCE, TCS, M&M.")
    return s


def _clean_series(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    idx = pd.DatetimeIndex(s.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)          # keep the Indian trading date, drop the timezone
    s.index = idx.normalize()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s[s > 0].dropna()


def validate_history(prices: pd.Series, symbol: str, today: pd.Timestamp | None = None
                     ) -> tuple[pd.Series, list[str]]:
    """Check the 5-year rule and trim to exactly the last 5 years.

    Returns the trimmed series and a list of warnings (possible split/bonus jumps).
    Raises DataError if the stock fails a check.
    """
    today = pd.Timestamp(today if today is not None else pd.Timestamp.today()).normalize()
    s = _clean_series(prices)
    if len(s) < 2:
        raise DataError(f"No usable price data found for {symbol}.")

    last = s.index[-1]
    if (today - last).days > STALE_DAYS:
        raise DataError(
            f"{symbol}: latest available price is from {last:%d %b %Y}. "
            "The stock may be suspended or delisted, so it can't be used."
        )

    cutoff = last - pd.DateOffset(years=LOOKBACK_YEARS)
    first = s.index[0]
    if first > cutoff + pd.Timedelta(days=START_TOLERANCE_DAYS):
        years = (last - first).days / 365.25
        raise DataError(
            f"{symbol} has only {years:.1f} years of NSE price history "
            f"(from {first:%d %b %Y}). 5 years are required."
        )

    on_or_before = s.index[s.index <= cutoff]
    base = on_or_before.max() if len(on_or_before) else first
    s = s[s.index >= base]

    warnings = []
    r = daily_returns(s.to_frame(symbol))[symbol]
    for d, v in r[r.abs() > JUMP_THRESHOLD].items():
        warnings.append(
            f"{symbol}: {v:+.1%} move on {d:%d %b %Y}. This is probably a split or bonus "
            "that the data source hasn't adjusted, and it will distort the results."
        )
    return s, warnings


def fetch_nse_prices(symbol: str, price_basis: str = "Close",
                     today: pd.Timestamp | None = None) -> StockData:
    """Download daily prices for an NSE stock from Yahoo Finance (<SYMBOL>.NS).

    price_basis:
      "Close"     -> close adjusted for splits/bonuses (price return, like the Excel)
      "Adj Close" -> also adjusted for dividends (total return)
    """
    import yfinance as yf  # imported here so the maths can be used without it

    symbol = normalise_symbol(symbol)
    today = pd.Timestamp(today if today is not None else pd.Timestamp.today()).normalize()
    start = today - pd.DateOffset(years=LOOKBACK_YEARS) - pd.Timedelta(days=30)
    try:
        hist = yf.Ticker(f"{symbol}.NS").history(
            start=start.strftime("%Y-%m-%d"),
            end=(today + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            interval="1d",
            auto_adjust=False,
            actions=False,
        )
    except Exception as e:  # network errors, rate limits, Yahoo changes
        raise DataError(f"Couldn't download {symbol} from Yahoo Finance: {e}") from e

    if hist is None or hist.empty:
        raise DataError(
            f"No data found for '{symbol}' on NSE. Check the symbol as listed on NSE "
            "(e.g. RELIANCE, TCS, M&M, HDFCBANK)."
        )
    col = price_basis if price_basis in hist.columns else "Close"
    prices, warnings = validate_history(hist[col], symbol, today)
    label = "Adj Close (splits, bonuses & dividends)" if col == "Adj Close" else "Close (split/bonus adjusted)"
    return StockData(symbol, prices, f"Yahoo Finance {symbol}.NS — {label}", warnings)


def parse_price_csv(file, symbol: str, today: pd.Timestamp | None = None) -> StockData:
    """Fallback: read a CSV with a Date column and a Close column.

    Works with NSE's historical-data download (columns like 'Date ', 'close ', values
    with commas) and Yahoo-style CSVs.
    """
    symbol = normalise_symbol(symbol)
    try:
        df = pd.read_csv(file)
    except Exception as e:
        raise DataError(f"Couldn't read the CSV: {e}") from e
    cols = {c.strip().lower(): c for c in df.columns}
    date_col = next((cols[k] for k in ("date", "timestamp", "trade date") if k in cols), None)
    close_col = next((cols[k] for k in ("adj close", "close", "close price", "close (unit curr)", "closing price")
                      if k in cols), None)
    if date_col is None or close_col is None:
        raise DataError("The CSV needs a 'Date' column and a 'Close' column.")
    dates = pd.to_datetime(df[date_col].astype(str).str.strip(), dayfirst=True, errors="coerce", format="mixed")
    vals = pd.to_numeric(df[close_col].astype(str).str.replace(",", "").str.strip(), errors="coerce")
    s = pd.Series(vals.values, index=dates).dropna()
    s = s[s.index.notna()]
    prices, warnings = validate_history(s, symbol, today)
    return StockData(symbol, prices, f"Uploaded CSV ({close_col.strip()})", warnings)


# --------------------------------------------------------------------------------------
# Calculations
# --------------------------------------------------------------------------------------
def align_prices(stocks: list[StockData]) -> pd.DataFrame:
    """Keep only dates every stock traded on, then the common last-5-years window."""
    prices = pd.concat({s.symbol: s.prices for s in stocks}, axis=1, join="inner").sort_index()
    if len(prices) < 3:
        raise DataError("The selected stocks have too few common trading dates.")
    end = prices.index[-1]
    cutoff = end - pd.DateOffset(years=LOOKBACK_YEARS)
    on_or_before = prices.index[prices.index <= cutoff]
    base = on_or_before.max() if len(on_or_before) else prices.index[0]
    return prices[prices.index >= base]


def daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """(P_t - P_{t-1}) / P_{t-1}; the first date has no return and is dropped."""
    return (prices / prices.shift(1) - 1).iloc[1:]


def stock_stats(returns: pd.DataFrame) -> pd.DataFrame:
    mean = returns.mean()
    sd = returns.std(ddof=0)                      # population SD, same as STDEV.P
    return pd.DataFrame({
        "Avg daily return": mean,
        "Daily SD": sd,
        "Annual return": mean * TRADING_DAYS,
        "Annual SD": sd * np.sqrt(TRADING_DAYS),
    })


def correlation_matrix(returns: pd.DataFrame) -> pd.DataFrame:
    return returns.corr()                         # Pearson, same as CORREL


def covariance_matrix(returns: pd.DataFrame) -> pd.DataFrame:
    return returns.cov(ddof=0)                    # = rho_ij * s_i * s_j with STDEV.P


def portfolio_stats(weights, mean: pd.Series, cov: pd.DataFrame) -> dict:
    w = np.asarray(weights, dtype=float)
    mu = mean.to_numpy()
    C = cov.to_numpy()
    ret_d = float(w @ mu)
    sd_d = float(np.sqrt(max(w @ C @ w, 0.0)))
    sd_i = np.sqrt(np.diag(C))
    wavg_sd = float(w @ sd_i)                     # what the risk would be if every rho were 1
    return {
        "ret_d": ret_d,
        "sd_d": sd_d,
        "ret_a": ret_d * TRADING_DAYS,
        "sd_a": float(sd_d * np.sqrt(TRADING_DAYS)),
        "wavg_sd_d": wavg_sd,
        "wavg_sd_a": float(wavg_sd * np.sqrt(TRADING_DAYS)),
        "risk_reduction": (1 - sd_d / wavg_sd) if wavg_sd > 0 else 0.0,
    }


def _solve_min_var(C: np.ndarray, mu: np.ndarray | None = None, target: float | None = None,
                   x0: np.ndarray | None = None) -> np.ndarray | None:
    n = C.shape[0]
    scale = float(np.mean(np.diag(C))) or 1.0
    Cs = C / scale                                # rescale so the optimiser sees O(1) numbers
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0, "jac": lambda w: np.ones(n)}]
    if target is not None:
        mscale = float(np.max(np.abs(mu))) or 1.0
        ms, ts = mu / mscale, target / mscale
        cons.append({"type": "eq", "fun": lambda w: w @ ms - ts, "jac": lambda w: ms})
    res = minimize(
        lambda w: w @ Cs @ w,
        x0 if x0 is not None else np.full(n, 1.0 / n),
        jac=lambda w: 2 * Cs @ w,
        bounds=[(0.0, 1.0)] * n,
        constraints=cons,
        method="SLSQP",
        options={"ftol": 1e-14, "maxiter": 2000},
    )
    if not res.success:
        return None
    w = np.clip(res.x, 0.0, None)
    return w / w.sum()


def min_variance_weights(cov: pd.DataFrame) -> pd.Series:
    """Long-only minimum-variance weights (what Excel Solver finds with w >= 0, sum = 1)."""
    C = cov.to_numpy()
    w = _solve_min_var(C)
    if w is None:
        raise DataError("The minimum-variance optimiser did not converge.")
    return pd.Series(w, index=cov.index)


def efficient_frontier(mean: pd.Series, cov: pd.DataFrame, points: int = 40) -> pd.DataFrame:
    """Long-only efficient frontier: least-risk portfolio for each target return
    between the minimum-variance portfolio and the highest-return stock."""
    C = cov.to_numpy()
    mu = mean.to_numpy()
    w0 = _solve_min_var(C)
    if w0 is None:
        return pd.DataFrame(columns=["ret_d", "sd_d"])
    lo, hi = float(w0 @ mu), float(mu.max())
    rows = [(lo, float(np.sqrt(w0 @ C @ w0)))]
    x = w0
    for t in np.linspace(lo, hi, points)[1:]:
        w = _solve_min_var(C, mu, t, x)
        if w is None:
            continue
        x = w
        rows.append((float(w @ mu), float(np.sqrt(w @ C @ w))))
    return pd.DataFrame(rows, columns=["ret_d", "sd_d"])


# --------------------------------------------------------------------------------------
# Excel export
# --------------------------------------------------------------------------------------
def build_excel(prices: pd.DataFrame, returns: pd.DataFrame, stats: pd.DataFrame,
                corr: pd.DataFrame, weights: pd.Series, port: dict, method: str) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        summary = stats.copy()
        summary.insert(0, "Weight", weights)
        summary.to_excel(xw, sheet_name="Summary", startrow=0)
        r0 = len(summary) + 3
        pd.DataFrame({
            "Value": [method, port["ret_d"], port["sd_d"], port["ret_a"], port["sd_a"],
                      port["wavg_sd_d"], port["risk_reduction"]],
        }, index=["Weighting method", "Portfolio E(R) daily", "Portfolio SD daily",
                  "Portfolio E(R) annual", "Portfolio SD annual",
                  "Weighted avg of stock SDs (daily)", "Risk reduction from diversification"]
        ).to_excel(xw, sheet_name="Summary", startrow=r0)
        corr.to_excel(xw, sheet_name="Correlation")
        prices.rename_axis("Date").to_excel(xw, sheet_name="Prices")
        returns.rename_axis("Date").to_excel(xw, sheet_name="Daily Returns")
        for ws in xw.book.worksheets:
            ws.column_dimensions["A"].width = 34 if ws.title == "Summary" else 14
    return buf.getvalue()
