"""Daily US-equity data with explicit freshness and exchange-session checks."""
from functools import lru_cache
import time

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import yfinance as yf


class DataQualityError(ValueError):
    pass


def utc_now(now=None):
    value = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    return value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC")


@lru_cache(maxsize=128)
def market_schedule(start, end):
    return mcal.get_calendar("NYSE").schedule(start_date=start, end_date=end)


def last_completed_session(now=None):
    now = utc_now(now)
    schedule = market_schedule(str((now - pd.Timedelta(days=14)).date()), str(now.date()))
    # Allow a short delay for the daily feed to settle after the exchange closes.
    complete = schedule[schedule.market_close + pd.Timedelta(minutes=15) <= now]
    if complete.empty:
        raise DataQualityError("No completed US market session available")
    return pd.Timestamp(complete.index[-1]).tz_localize(None).normalize()


def normalize_prices(df):
    if df is None or df.empty:
        raise DataQualityError("Empty price history")
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    columns = ["Open", "High", "Low", "Close", "Volume"]
    if not set(columns).issubset(df.columns):
        raise DataQualityError("Missing OHLCV columns")
    df = df[columns].apply(pd.to_numeric, errors="coerce")
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    valid = (df[["Open", "High", "Low", "Close"]] > 0).all(axis=1)
    valid &= (df.Volume >= 0) & (df.High >= df[["Open", "Close", "Low"]].max(axis=1))
    valid &= df.Low <= df[["Open", "Close"]].min(axis=1)
    return df.loc[valid]


def download_prices(ticker, period="max", now=None, attempts=3):
    cutoff = last_completed_session(now)
    error = None
    for attempt in range(attempts):
        try:
            df = normalize_prices(yf.download(
                ticker, period=period, interval="1d", progress=False,
                auto_adjust=True, multi_level_index=False, threads=False, timeout=20,
            ))
            df = df.loc[df.index <= cutoff]
            if df.empty or df.index[-1] != cutoff:
                latest = "none" if df.empty else str(df.index[-1].date())
                raise DataQualityError(f"{ticker}: latest valid close {latest}; expected {cutoff.date()}")
            return df
        except Exception as exc:
            # Do not log request URLs, cookies, or credentials from provider exceptions.
            error = exc if isinstance(exc, DataQualityError) else DataQualityError(f"{ticker}: download failed ({type(exc).__name__})")
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise error


def closed_daily_data(df, timeframe="Daily", now=None):
    """Remove current sessions and unfinished weekly/monthly periods before aggregation.

    Callers must pass DAILY bars, including for weekly and monthly indicators.
    Exchange holidays and early closes are supplied by the NYSE calendar.
    """
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()
    df = df.copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df.sort_index().loc[lambda x: x.index <= last_completed_session(now)]
    if df.empty or timeframe == "Daily":
        return df
    frequency = {"Weekly": "W-FRI", "Monthly": "M"}[timeframe]
    period = df.index[-1].to_period(frequency)
    schedule = market_schedule(str(period.start_time.date()), str(period.end_time.date()))
    if schedule.empty:
        return df.loc[df.index < period.start_time]
    final_session = pd.Timestamp(schedule.index[-1]).tz_localize(None).normalize()
    final_close = schedule.market_close.iloc[-1] + pd.Timedelta(minutes=15)
    if df.index[-1] < final_session or utc_now(now) < final_close:
        df = df.loc[df.index < period.start_time]
    return df


def resample_prices(df, timeframe, now=None):
    df = closed_daily_data(df, timeframe, now)
    if df.empty or timeframe == "Daily":
        return df
    rule = "W-FRI" if timeframe == "Weekly" else "ME"
    aggregations = {k: v for k, v in {
        "Open": "first", "High": "max", "Low": "min", "Close": "last",
        "Volume": "sum", "MA200_DAILY": "last", "MA50_DAILY": "last",
        "MA200_RISING": "last", "VSTOP_ATR": "last", "VSTOP_ADX": "last",
        "VSTOP_LINE": "last", "VSTOP_TREND": "last", "VSTOP_MULTIPLIER": "last",
        "VSTOP_BREACH_SESSIONS": "last", "VSTOP_CONFIRM_SESSIONS": "last",
        "VSTOP_SHIELD_ELIGIBLE": "last", "VSTOP_SHIELD_SUPPRESSED": "last",
    }.items() if k in df.columns}
    result = df.resample(rule).agg(aggregations).dropna(subset=["Close"])
    # Date the signal on the last actual trading session, including holiday weeks.
    dates = pd.Series(df.index, index=df.index).resample(rule).last()
    result.index = pd.DatetimeIndex(dates.reindex(result.index).values, name=df.index.name)
    return result
