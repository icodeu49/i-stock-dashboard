"""Completed-session stock alerts with visible coverage and delivery failures."""
import hashlib
from collections import defaultdict
import pandas as pd
from alpha_engine import check_market_regime, check_overextension, check_vcp_contraction
from helpers import calculate_technicals
from market_data import download_prices, last_completed_session
from notifications import require_telegram_config, send_records
from storage import load_watchlist


def percentage(value):
    return "N/A" if pd.isna(value) else f"{value:+.2f}%"


def run_automated_scanner():
    require_telegram_config()
    watchlist = load_watchlist()
    session = last_completed_session().date().isoformat()
    spy = download_prices("SPY")
    regime = check_market_regime(spy)
    sectors, failures, results, limited = {}, [], {}, []
    for etf in sorted({item.get("sector_etf") for item in watchlist.values()} - {None, ""}):
        try:
            sectors[etf] = download_prices(etf)
        except Exception as exc:
            failures.append(f"{etf}: {exc}")
    for ticker, metadata in watchlist.items():
        try:
            raw = download_prices(ticker)
            for timeframe in ["Daily", "Weekly", "Monthly"]:
                frame = calculate_technicals(raw, timeframe, spy_df=spy,
                    sector_df=sectors.get(metadata.get("sector_etf")))
                if frame.empty:
                    limited.append(f"{ticker}/{timeframe}")
                    continue
                results[(ticker, timeframe)] = frame
        except Exception as exc:
            failures.append(f"{ticker}: {exc}")
            print(f"Scan error: {ticker}: {exc}")
    groups = defaultdict(list)
    for (ticker, timeframe), frame in results.items():
        sector = watchlist[ticker].get("sector_etf")
        score = frame.RS_SCORE.iloc[-1]
        if sector and pd.notna(score):
            groups[(sector, timeframe)].append((ticker, score))
    rankings = {}
    for (_, timeframe), peers in groups.items():
        peers.sort(key=lambda pair: pair[1], reverse=True)
        for rank, (ticker, _) in enumerate(peers, 1):
            rankings[(ticker, timeframe)] = f"{rank}/{len(peers)}"
    records = []
    for timeframe in ["Monthly", "Weekly", "Daily"]:
        for ticker in watchlist:
            frame = results.get((ticker, timeframe))
            if frame is None:
                continue
            row = frame.iloc[-1]
            buy = bool(row.BREAKOUT_TRIGGERED)
            sell = timeframe in ["Weekly", "Monthly"] and bool(row.VSTOP_SELL_SIGNAL)
            if not buy and not sell:
                continue
            date = frame.index[-1].date().isoformat()
            kind = "sell" if sell else "buy"
            key = f"signal:{ticker}:{timeframe}:{date}:{kind}"
            label = "🔴 VSTOP breakdown" if sell else "🟢 Breakout"
            text = f"{label} — {ticker} | {timeframe} close {date}\n"
            text += f"Close USD {row.Close:.2f} | Market regime: {regime}\n"
            text += f"RS vs SPY: {percentage(row.RS_SCORE)}"
            sector = watchlist[ticker].get("sector_etf")
            if sector:
                text += f" | vs {sector}: {percentage(row.RS_SECTOR_SCORE)}"
            text += f"\nWatchlist sector rank: {rankings.get((ticker, timeframe), 'N/A')}"
            text += f"\nADX {row.ADX:.1f} | EMA10>30: {'yes' if row.EMA_SPEED_ALIGNED else 'no'}"
            text += f" | Above SAR: {'yes' if row.SAR_ALIGNED else 'no'}"
            text += f"\nPocket pivot: {'yes' if row.POCKET_PIVOT else 'no'}"
            text += f" | Accumulation: {'yes' if row.ACCUMULATION_DAY else 'no'}"
            text += f"\nVSTOP USD {row.VSTOP_LINE:.2f} | 200-day MA USD {row.MA200_DAILY:.2f}"
            if timeframe == "Daily" and buy:
                vcp, extension = check_vcp_contraction(frame), check_overextension(frame)
                if vcp['has_vcp']:
                    text += f"\nVCP contraction: {vcp['tight_range']:.2f}% range"
                if extension['is_extended']:
                    text += f"\n⚠️ Extended {extension['dist_50']:.2f}% above the 50-day MA"
            records.append((key, text))
    scanned = len({ticker for ticker, _ in results})
    if not scanned:
        failures.append("No stocks have sufficient valid history for analysis")
    digest = hashlib.sha256("|".join(failures + limited).encode()).hexdigest()[:12]
    status = f"{'⚠️ Partial' if failures else '✅ Completed'} stock scan — US close {session}\n"
    status += f"Analysed {scanned}/{len(watchlist)} stocks. Market regime: {regime}.\n"
    status += f"{len(records)} latest-period signals; previously delivered signals are suppressed."
    if limited:
        status += f"\nInsufficient history in {len(limited)} stock/timeframe combinations (see workflow log)."
        print("Insufficient history:", ", ".join(limited))
    if failures:
        status += "\nData failures: " + "; ".join(failures)[:1500]
    records.append((f"scan-status:{session}:{digest}", status))
    send_records(records)
    if failures:
        raise RuntimeError(f"Scan incomplete: {len(failures)} data failures; valid alerts were delivered")
    if not scanned:
        raise RuntimeError("No stock could be analysed")


if __name__ == "__main__":
    run_automated_scanner()
