import json
import os
import yfinance as yf
import pandas as pd

# Force clean absolute pathing relative to where the script actually sits
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")

from market_data import download_prices
from storage import load_watchlist, merge_candidates, write_json


def fetch_dynamic_growth_watchlist():
    momentum_universe = [
        "AAPL", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "NFLX", "AMD",
        "SMCI", "ARM", "PLTR", "PANW", "CRWD", "COIN", "MARA", "RIOT", "HOOD", "SOFI",
        "AFRM", "UPST", "AI", "PATH", "CELH", "WING", "DUOL", "CARD", "APP", "MELI",
        "SHOP", "TSM", "GEV", "NET", "OKTA", "DDOG", "SNOW", "ZS", "MDB", "TEAM",
        "SPOT", "TTD", "RBLX", "U", "AAL", "CCL", "NCLH", "RCL", "DKNG", "BABA"
    ]

    performance, failures = {}, []
    for ticker in momentum_universe:
        try:
            df = download_prices(ticker, period="1mo")
            if len(df) < 6:
                raise ValueError("Need six closes for a five-session return")
            performance[ticker] = (df.Close.iloc[-1] / df.Close.iloc[-6] - 1) * 100
        except Exception as exc:
            failures.append(f"{ticker}: {exc}")
    if not performance:
        raise RuntimeError("Momentum scan returned no valid stocks")
    top = sorted(performance, key=performance.get, reverse=True)[:25]
    watchlist = load_watchlist(WATCHLIST_FILE)
    added = merge_candidates(watchlist, {ticker: {"group": "Small/Mid Growth", "source": "momentum_scan"} for ticker in top})
    write_json(WATCHLIST_FILE, watchlist)
    print(f"Momentum scan: {len(performance)}/{len(momentum_universe)} valid, {len(added)} additions; {len(watchlist)} tracked.")
    if failures:
        for error in failures:
            print(f"Data failure: {error}")
        raise RuntimeError(f"Momentum scan incomplete: {len(failures)} data failures")
    return added


if __name__ == "__main__":
    fetch_dynamic_growth_watchlist()
