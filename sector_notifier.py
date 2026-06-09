import os
import sys
import json
import requests
import yfinance as yf
import pandas as pd
import numpy as np

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
    "XLI_MAT": {"name": "Materials", "stocks": ["LIN", "APD", "SHW", "FCX", "NEM", "ECL", "CTVA", "DOW", "NUE", "ALB"]}, # Using alternative tracking key
    "XLU": {"name": "Utilities", "stocks": ["NEE", "SO", "DUK", "CEG", "AEP", "SRE", "D", "FE", "EXC", "PCG"]},
    "XLRE": {"name": "Real Estate", "stocks": ["PLD", "AMT", "EQIX", "CCI", "WY", "PSA", "O", "IRM", "DLR", "AVB"]}
}

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        res = requests.post(url, json=payload, timeout=15)
        if not res.ok: print(f"Telegram error: {res.text}")
    except Exception as e:
        print(f"Failed to transmit Telegram message: {e}")

def calculate_momentum_score(ticker):
    try:
        df = yf.download(ticker, period="6mo", interval="1d", progress=False, multi_level_index=False)
        if df.empty or len(df) < 65: return -999.0
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)

        close = df['Close']
        volume = df['Volume']
        
        # Calculate Multi-Timeframe Rate of Change (ROC)
        roc_3m = ((close.iloc[-1] - close.iloc[-63]) / close.iloc[-63]) * 100
        roc_1m = ((close.iloc[-1] - close.iloc[-21]) / close.iloc[-21]) * 100
        roc_1w = ((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]) * 100
        
        # Mathematical Scoring Engine Formula: Blended Weighted Matrix
        raw_score = (0.40 * roc_1m) + (0.40 * roc_1w) + (0.20 * roc_3m)
        
        # Volume Force Multiplier calculation
        vol_5d = volume.iloc[-5:].mean()
        vol_50d = volume.iloc[-50:].mean()
        vol_multiplier = vol_5d / vol_50d if vol_50d > 0 else 1.0
        
        # Cap multiplier bounds to prevent statistical anomalies from penny movements
        vol_multiplier = max(0.5, min(vol_multiplier, 2.0))
        
        return float(raw_score * vol_multiplier)
    except Exception:
        return -999.0

def rank_individual_stocks(stock_list):
    stock_scores = []
    spy_df = yf.download("SPY", period="3mo", interval="1d", progress=False, multi_level_index=False)
    if isinstance(spy_df.columns, pd.MultiIndex): spy_df.columns = spy_df.columns.get_level_values(0)
    spy_roc = ((spy_df['Close'].iloc[-1] - spy_df['Close'].iloc[-21]) / spy_df['Close'].iloc[-21]) * 100

    for ticker in stock_list:
        try:
            df = yf.download(ticker, period="3mo", interval="1d", progress=False, multi_level_index=False)
            if df.empty or len(df) < 22: continue
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            
            # Simple 1-Month Relative Strength Score calculations vs SPY benchmark
            stock_roc = ((df['Close'].iloc[-1] - df['Close'].iloc[-21]) / df['Close'].iloc[-21]) * 100
            rs_score = stock_roc - spy_roc
            stock_scores.append((ticker, rs_score))
        except Exception:
            continue
            
    # Return sorted structure ranked high to low by relative outperformance
    stock_scores.sort(key=lambda x: x[1], reverse=True)
    return [item[0] for item in stock_scores[:5]]

def run_macro_rotation_pipeline():
    print("🚀 Running Mathematical Sector Scoring Engine...")
    sector_rankings = []
    
    # Evaluate raw sector strengths
    for etf, metadata in SECTOR_MAP.items():
        score = calculate_momentum_score(etf)
        if score != -999.0:
            sector_rankings.append((etf, metadata['name'], score))
            
    # Rank top sectors down
    sector_rankings.sort(key=lambda x: x[2], reverse=True)
    top_5_sectors = sector_rankings[:5]
    
    # Build Telegram Output String Matrix
    msg = "📊 *WEEKLY MACRO SECTOR ROTATION MATRIX* 📊\n"
    msg += "🕒 _Generated Monday 2:49 PM UK Time_\n"
    msg += "====================================\n\n"
    
    for rank, (etf, name, score) in enumerate(top_5_sectors, 1):
        msg += f"🔥 *RANK #{rank}: {name} ({etf})*\n"
        msg += f"    └── 🧮 Momentum Score: `{score:+.2f}`\n"
        
        # Dig out underlying stock leaders within selected winner sector
        leader_stocks = rank_individual_stocks(SECTOR_MAP[etf]['stocks'])
        msg += f"    └── 🏆 Top 5 Liquid Leaders: `{', '.join(leader_stocks)}`\n\n"
        
    msg += "====================================\n"
    msg += "💡 *Strategy Note:* Consider focusing your daily breakout scans on these 25 leading institutional horses this week."
    
    print("📤 Sending macro matrix report to Telegram...")
    send_telegram_message(msg)
    print("✅ Completed Successfully.")

if __name__ == "__main__":
    run_macro_rotation_pipeline()
