"""
Markets Toolkit: one Streamlit app with three pages
  1. Portfolio Diversification Dashboard (NSE stocks)
  2. Nifty Option-Chain Arbitrage Scanner
  3. Black-Scholes Calculator (prices, Greeks, IV solver)

Run:  python -m streamlit run app.py
"""

import streamlit as st

st.set_page_config(page_title="Markets Toolkit", page_icon="📈", layout="wide")

pages = {
    "Portfolio": [
        st.Page("views/portfolio.py", title="Portfolio Dashboard", icon="📈", default=True),
    ],
    "Nifty options": [
        st.Page("views/scanner.py", title="Arbitrage Scanner", icon="🔍"),
        st.Page("views/calculator.py", title="Black-Scholes Calculator", icon="🧮"),
    ],
}

st.navigation(pages).run()
