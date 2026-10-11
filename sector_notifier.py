import os
import sys
import json
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo
from market_data import download_prices, last_completed_session
from notifications import require_telegram_config, send_records
from storage import WATCHLIST_FILE, load_watchlist, merge_candidates, write_json

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 11 Sector ETFs mapped to their top institutional stock components
SECTOR_MAP = {
    "XLK": {"name": "Technology", "stocks": ["MSFT", "AAPL", "NVDA", "AVGO", "ORCL", "CSCO", "AMD", "QCOM", "NOW", "INTU"]},
    "XLF": {"name": "Financials", "stocks": ["JPM", "BAC", "WFC", "MS", "GS", "BRK-B", "AXP", "V", "MA", "BLK"]},
    "XLY": {"name": "Consumer Discretionary", "stocks": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "CMG"]},
    "XLC": {"name": "Communications", "stocks": ["META", "GOOGL", "NFLX", "TMUS", "DIS", "CHTR", "CMCSA", "VZ", "T", "PINS"]},
    "XLI": {"name": "Industrials", "stocks": ["CAT", "GE", "UNP", "HON", "ETN", "URI", "WM", "UPS", "DE", "LMT"]},
    "XLP": {"name": "Consumer Staples", "stocks": ["PG", "COST", "KO", "PEP", "WMT", "PM", "MDLZ", "EL", "MO", "CL"]},
    "XLV": {"name": "Healthcare", "stocks": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ISRG", "PFE", "AMGN", "REGN"]},
    "XLE": {"name": "Energy", "stocks": ["XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "WMB", "HAL"]},
    "XLB": {"name": "Materials", "stocks": ["LIN", "APD", "SHW", "FCX", "NEM", "ECL", "CTVA", "DOW", "NUE", "ALB"]},
    "XLU": {"name": "Utilities", "stocks": ["NEE", "SO", "DUK", "CEG", "AEP", "SRE", "D", "FE", "EXC", "PCG"]},
    "XLRE": {"name": "Real Estate", "stocks": ["PLD", "AMT", "EQIX", "CCI", "WY", "PSA", "O", "IRM", "DLR", "AVB"]}
}

GICS_NAMES = {"XLK": "Information Technology", "XLF": "Financials",
    "XLY": "Consumer Discretionary", "XLC": "Communication Services",
    "XLI": "Industrials", "XLP": "Consumer Staples", "XLV": "Health Care",
    "XLE": "Energy", "XLB": "Materials", "XLU": "Utilities", "XLRE": "Real Estate"}


def calculate_momentum_score(ticker):
    df = download_prices(ticker, period="6mo")
    if len(df) < 64:
        raise ValueError(f"{ticker}: need 64 daily closes for sector momentum")
    close, volume = df.Close, df.Volume
    roc = lambda n: (close.iloc[-1] / close.iloc[-n-1] - 1) * 100
    volume_ratio = volume.tail(5).mean() / volume.tail(50).mean() if volume.tail(50).mean() > 0 else 1
    return float((0.4 * roc(21) + 0.4 * roc(5) + 0.2 * roc(63)) * np.clip(volume_ratio, 0.5, 2))


def rank_individual_stocks(stock_list, spy=None, failures=None):
    spy = download_prices("SPY", period="3mo") if spy is None else spy
    failures = [] if failures is None else failures
    stock_scores = []
    for ticker in stock_list:
        try:
            df = download_prices(ticker, period="3mo")
            merged = pd.concat([df.Close.rename("stock"), spy.Close.rename("spy")], axis=1).dropna()
            if len(merged) < 22:
                raise ValueError("need 22 matching daily closes")
            ratio = merged.stock / merged.spy
            stock_scores.append((ticker, (ratio.iloc[-1] / ratio.iloc[-22] - 1) * 100))
        except Exception as exc:
            failures.append(f"{ticker}: {exc}")
    stock_scores.sort(key=lambda pair: pair[1], reverse=True)
    return [ticker for ticker, _ in stock_scores[:5]]


def run_macro_rotation_pipeline():
    require_telegram_config()
    watchlist = load_watchlist()
    failures, rankings, candidates, records = [], [], {}, []
    session = last_completed_session().date().isoformat()
    for etf, metadata in SECTOR_MAP.items():
        try:
            rankings.append((etf, metadata['name'], calculate_momentum_score(etf)))
        except Exception as exc:
            failures.append(f"{etf}: {exc}")
    if len(rankings) < 5:
        raise RuntimeError("Fewer than five sectors have valid current data")
    rankings.sort(key=lambda row: row[2], reverse=True)
    spy = download_prices("SPY", period="3mo")
    for rank, (etf, name, score) in enumerate(rankings[:5], 1):
        leaders = rank_individual_stocks(SECTOR_MAP[etf]['stocks'], spy, failures)
        for ticker in leaders:
            candidates[ticker] = {"group": "Sector Leaders", "sector_etf": etf,
                "gics_sector": GICS_NAMES[etf], "source": "sector_scan"}
        text = f"Sector #{rank}: {name} ({etf}) | US close {session}\nMomentum {score:+.2f}\nLeaders: {', '.join(leaders) or 'unavailable'}"
        records.append((f"sector:{session}:{etf}:{','.join(leaders)}", text))
    added = merge_candidates(watchlist, candidates)
    write_json(WATCHLIST_FILE, watchlist)
    status = "⚠️ Partial" if failures else "✅ Completed"
    stamp = datetime.now(ZoneInfo("Europe/London")).strftime("%a %d %b %Y %H:%M %Z")
    message = f"{status} sector scan — {stamp}\nWatchlist: {len(watchlist)} stocks. Added {len(added)}: {', '.join(added) or 'none (leaders already tracked)'}."
    if failures:
        message += "\nData failures: " + "; ".join(failures)[:1000]
    records.append((f"sector-status:{session}:{','.join(added)}:{len(failures)}", message))
    send_records(records)
    print(message)
    if failures:
        raise RuntimeError(f"Sector scan incomplete: {len(failures)} data failures")


if __name__ == "__main__":
    run_macro_rotation_pipeline()
