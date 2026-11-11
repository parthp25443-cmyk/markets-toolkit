# Markets Toolkit (NSE)

One Streamlit app with three pages, listed in the sidebar:

| Page | What it does |
|---|---|
| 📈 **Portfolio Dashboard** | Diversification analysis for up to 15 NSE stocks over the last 5 years |
| 🔍 **Arbitrage Scanner** | Runs no-arbitrage checks on NSE's Nifty option-chain CSV, net of Indian trading costs |
| 🧮 **Black-Scholes Calculator** | Option prices, d₁/d₂, N(d₁)/N(d₂), Greeks and an implied-volatility solver |

The pages share data:
- **Scanner → Calculator:** pick a strike and click *Open in calculator*. It opens with the implied
  forward, expiry, rate, that strike's volatility and its mid prices already loaded into the IV solver.
- **Dashboard → Calculator:** any stock you've added on the dashboard can be used in the calculator
  with one click: its last close becomes S and its 5-year annual SD becomes σ. If you then enter
  a market premium, the calculator compares implied volatility with historical volatility.

## Setup (one time)

You need Python 3.10 or later.

```bash
cd portfolio_app
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python -m streamlit run app.py
```

The app opens in your browser at http://localhost:8501.

## Files

```
portfolio_app/
├── app.py               navigation entry point (run this)
├── portfolio_core.py    dashboard maths and data loading (unchanged)
├── options_core.py      scanner checks, costs, Black-Scholes, Greeks, IV solver
├── views/
│   ├── portfolio.py     page 1: Portfolio Dashboard (your original app.py)
│   ├── scanner.py       page 2: Arbitrage Scanner
│   └── calculator.py    page 3: Black-Scholes Calculator
└── requirements.txt
```

---

## 1 · Portfolio Dashboard

Add up to 15 NSE stocks. For each one, the app pulls the last 5 years of daily prices and shows:

1. Each stock's average daily return and SD, daily and annualised
2. The correlation matrix
3. Portfolio weights: equal, manual, or minimum-variance (long-only)
4. Portfolio expected return and SD, and how much risk diversification removed
5. The efficient frontier, showing your portfolio and the minimum-variance portfolio
6. An Excel download of all the workings

### How the numbers are calculated (same as the IAPM Excel)

| Item | Formula | Excel equivalent |
|---|---|---|
| Daily return | (Pₜ − Pₜ₋₁) / Pₜ₋₁ | `=(B5-B4)/B4` |
| Average return | mean of daily returns | `AVERAGE` |
| Standard deviation | population SD | `STDEV.P` |
| Correlation | Pearson | `CORREL` |
| Portfolio E(R) | Σ wᵢ·E(Rᵢ) | `SUMPRODUCT` |
| Portfolio SD | √(ΣᵢΣⱼ wᵢwⱼσᵢσⱼρᵢⱼ) | the long `SQRT(...)` formula |
| Annualised | return × 252, SD × √252 | n/a |
| Min-variance weights | lowest SD with weights ≥ 0 that sum to 1 | Solver |

These were checked against `IAPM_Grp3_2501443.xlsx`. Using Reliance, M&M and Lupin over the same
dates, every average, SD, correlation and portfolio figure matches the Excel. The minimum-variance
weights also match the Solver result (46.7% / 20.6% / 32.7%).

### Rules when adding a stock

- **Symbol:** use the NSE symbol, e.g. `RELIANCE`, `M&M`, `HDFCBANK`. `NSE:TCS` and `TCS.NS` also work.
- **5-year rule:** if the stock's price history starts less than 5 years before its latest price
  (allowing a few days for holidays), it is rejected with an error.
- **Stale data:** if the latest price is more than 10 days old (suspended or delisted), it is rejected.
- **Split/bonus check:** a single-day move larger than ±40% shows a warning.
- **Common dates:** returns are calculated only on dates on which every selected stock traded.

### Data source

Prices come from Yahoo Finance (`SYMBOL.NS`) through `yfinance`. **Close** is adjusted for splits
and bonuses (price returns, like the Excel). **Adj Close** is also adjusted for dividends (total
returns). If Yahoo fails, try again after a minute and run `pip install -U yfinance`, or use
**"Upload a CSV instead"** in the sidebar.

---

## 2 · Arbitrage Scanner

1. On nseindia.com open **Option Chain**, choose NIFTY and the expiry, and click **Download (.csv)**.
2. Upload the file. The expiry date is read from the filename. Set the time you downloaded it.
3. Set your funding rate r (what your capital earns elsewhere). For a **monthly** expiry you can
   also enter the futures bid/ask.

Every check uses the **ask** for anything you buy and the **bid** for anything you sell, never LTP:

| Check | Rule it tests | Needs |
|---|---|---|
| Box spread | 4 options paying exactly K₂ − K₁ must cost (K₂ − K₁)·e^(−rT) | r only |
| Conversion / reversal | C − P = (F − K)·e^(−rT) | same-expiry futures |
| Vertical bounds | price ordering across strikes; a spread ≤ its strike gap | nothing |
| Butterfly | a 1 : −2 : 1 butterfly can't cost less than zero | nothing |

**Net = edge − costs.** Costs use the post-Budget 2026 STT rates (effective 1 April 2026):
0.15% on option sales, 0.15% on exercise of ITM options, 0.05% on futures sales. Exchange charges,
stamp duty, SEBI fees, GST and ₹20 brokerage per order are also included, and every rate can be
edited. The default lot size is 65.

**Load demo data** creates a synthetic chain with one put deliberately priced ₹14 too high, so you
can see what a hit looks like.

**Not included:** bid/ask quantity, slippage between legs, margin blocked until expiry, and the delay
between NSE's snapshot and your order. A hit is a lead to check live, not a trade.

---

## 3 · Black-Scholes Calculator

**Inputs:** S (spot, or futures price in Black-76 mode), K, σ, r, dividend yield q (spot mode only),
and time to expiry as either an expiry date and time or a number of days.

**Outputs:** call and put prices (split into intrinsic and time value), a put-call parity check,
d₁, d₂, N(±d₁), N(±d₂), n(d₁), and the Greeks:

| Greek | Shown as |
|---|---|
| Delta | premium change per 1-point move |
| Gamma | delta change per 1-point move |
| Theta | ₹ per calendar day (÷365) or trading day (÷252) |
| Vega | ₹ per 1 point change in IV |
| Rho | ₹ per 1% change in r |

**IV solver:** enter a call and/or put market premium to get the implied volatility. *Use as σ*
copies it into the inputs.

All Greeks were checked against numerical derivatives, and the IV solver recovers the input
volatility exactly.
