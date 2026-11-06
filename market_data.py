"""Daily US-equity data with explicit freshness and exchange-session checks."""
from functools import lru_cache
import json
import os
import time
from pathlib import Path

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


_STALE_WAIT_BUDGET = {"left": 240.0}  # total seconds one process may spend waiting for lagging bars


def _fetch_raw(ticker, period, use_history):
    if use_history:
        return yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
    return yf.download(
        ticker, period=period, interval="1d", progress=False,
        auto_adjust=True, multi_level_index=False, threads=False, timeout=20,
    )


_MASSIVE_GROUPED = {"date": None, "bars": None, "throttled": False, "failed_date": None}
_MASSIVE_PACE = {"last_call": 0.0}
_MASSIVE_MIN_GAP = 13.0  # free plan: 5 requests/minute across all endpoints
_YAHOO_LAG = {"tickers": set(), "tripped": False}
_YAHOO_BREAKER_TRIP_AT = 3  # distinct lagging tickers before Yahoo is treated as run-wide broken


def _bar_cache_file(cutoff):
    return Path(os.environ.get("BAR_CACHE_FILE", f".bar_cache_{cutoff.date()}.json"))


def _bar_cache_read(cutoff):
    """Rescued current-session bars shared by every scanner process in one job run.

    Written by whichever process rescues a bar first (heatmap refresh or notifier); keyed
    by ticker with the exact session date and source recorded. A cache entry is used only
    when its date is exactly the cutoff - a stale or foreign entry is ignored, never served.
    """
    try:
        return json.loads(_bar_cache_file(cutoff).read_text())
    except Exception:
        return {}


def _bar_cache_add(cutoff, ticker, bar, source):
    cache = _bar_cache_read(cutoff)
    cache[ticker] = {"date": str(cutoff.date()), "o": bar["o"], "h": bar["h"],
                     "l": bar["l"], "c": bar["c"], "v": bar["v"], "source": source}
    try:
        _bar_cache_file(cutoff).write_text(json.dumps(cache))
    except Exception:
        pass


def _cached_bar(ticker, cutoff):
    entry = _bar_cache_read(cutoff).get(ticker)
    if entry is None or entry.get("date") != str(cutoff.date()):
        return None
    print(f"::notice::{ticker}: latest bar {cutoff.date()} reused from this run's rescued-bar cache "
          f"(source={entry.get('source', 'unknown')})")
    return pd.DataFrame(
        {"Open": [entry["o"]], "High": [entry["h"]], "Low": [entry["l"]],
         "Close": [entry["c"]], "Volume": [entry["v"]]}, index=[cutoff])




def _massive_get(path, params):
    """One Massive request: paced to the free-plan rate, errors logged without credentials.

    Returns the decoded JSON on HTTP 200, else None. A 429 disables the fallback for the
    rest of the run; other failures log the status and a sanitised body excerpt so the
    cause (entitlement, quota, request shape) is visible in the workflow log.
    """
    import os
    import requests
    key = os.environ.get("MASSIVE_API_KEY")
    if not key or _MASSIVE_GROUPED["throttled"]:
        return None
    gap = time.time() - _MASSIVE_PACE["last_call"]
    if gap < _MASSIVE_MIN_GAP:
        time.sleep(_MASSIVE_MIN_GAP - gap)
    try:
        resp = requests.get(f"https://api.massive.com{path}",
                            params={**params, "apiKey": key}, timeout=30)
    except Exception as exc:
        print(f"::warning::Massive request failed ({type(exc).__name__})")
        return None
    finally:
        _MASSIVE_PACE["last_call"] = time.time()
    if resp.status_code == 429:
        _MASSIVE_GROUPED["throttled"] = True
        print("::warning::Massive rate limit hit; fallback disabled for the rest of this run")
        return None
    if resp.status_code != 200:
        body = resp.text[:200].replace(key, "***")
        print(f"::warning::Massive fallback unavailable (HTTP {resp.status_code}): {body}")
        return None
    return resp.json()


def _bar_frame(bar, cutoff):
    return pd.DataFrame(
        {"Open": [bar["o"]], "High": [bar["h"]], "Low": [bar["l"]],
         "Close": [bar["c"]], "Volume": [bar["v"]]}, index=[cutoff])


def _massive_grouped_bar(ticker, cutoff):
    """Previous-day bar for one ticker from Massive's grouped daily endpoint.

    One grouped call returns every US stock's bar for the cutoff date, so any number of
    lagging tickers can be rescued with a single free-tier request (limit: 5/minute).
    The result is cached per run; a 429 disables the fallback for the rest of the run.
    Verified live on the free plan (5 Nov 2026 run): grouped and prev-bar data were
    available the same evening after the US close; treat that as observed, not guaranteed -
    if the cutoff date has no bar the lookup declines and the caller still fails loudly.
    """
    if _MASSIVE_GROUPED["failed_date"] == cutoff:
        return None  # grouped already failed this run (403/429); go straight to per-ticker prev
    if _MASSIVE_GROUPED["date"] != cutoff or _MASSIVE_GROUPED["bars"] is None:
        data = _massive_get(f"/v2/aggs/grouped/locale/us/market/stocks/{cutoff.date()}",
                            {"adjusted": "true"})
        if data is None:
            _MASSIVE_GROUPED["failed_date"] = cutoff
            return None
        results = data.get("results") or []
        _MASSIVE_GROUPED["date"] = cutoff
        _MASSIVE_GROUPED["bars"] = {item["T"]: item for item in results if "T" in item}
    bar = _MASSIVE_GROUPED["bars"].get(ticker)
    if bar is None:
        print(f"::warning::{ticker}: no Massive bar for {cutoff.date()} in grouped results")
        return None
    _bar_cache_add(cutoff, ticker, bar, "massive_grouped")
    return _bar_frame(bar, cutoff)


def _massive_prev_bar(ticker, cutoff):
    """Latest completed daily bar for one ticker from Massive's prev endpoint (free plan).

    Used when the grouped call is unavailable (for example an HTTP 403). The bar is used
    only when its date is exactly the cutoff - an older bar is never substituted silently;
    the caller then still fails loudly.
    """
    data = _massive_get(f"/v2/aggs/ticker/{ticker}/prev", {"adjusted": "true"})
    if data is None:
        return None
    results = data.get("results") or []
    if not results:
        print(f"::warning::{ticker}: Massive prev returned no bar")
        return None
    bar = results[0]
    day = pd.Timestamp(bar["t"], unit="ms").normalize()
    if day != cutoff:
        print(f"::warning::{ticker}: Massive prev bar is {day.date()}, expected {cutoff.date()}")
        return None
    _bar_cache_add(cutoff, ticker, bar, "massive_prev")
    return _bar_frame(bar, cutoff)


def _stale_diagnostic(raw, cutoff):
    """Say why the newest bar is missing, without request URLs or credentials."""
    if raw is None or len(raw) == 0:
        return "raw=empty"
    idx = pd.to_datetime(raw.index).tz_localize(None).normalize() if getattr(raw.index, "tz", None) is not None \
        else pd.to_datetime(raw.index).normalize()
    last = idx[-1]
    note = f"raw_last={last.date()}"
    if last == cutoff:
        note += " (row present but removed by validation/NaN)"
    return note


def download_prices(ticker, period="max", now=None, attempts=3, stale_retry_delay=45):
    """Return validated daily bars ending on the last completed session.

    Still raises DataQualityError when that session's bar is missing: stale data is never returned.
    Extra handling: a bar that is exactly one session behind (Yahoo lag after the close) is retried
    with spaced waits, via the Ticker.history endpoint, and finally via a Massive grouped
    previous-day bar when MASSIVE_API_KEY is set, before failing.
    """
    cutoff = last_completed_session(now)
    error = None
    use_history = False
    if _YAHOO_LAG["tripped"]:
        attempts = 1  # provider already broken run-wide: one probe, then rescue paths
    for attempt in range(attempts):
        wait = 2 ** attempt
        try:
            raw = _fetch_raw(ticker, period, use_history)
            df = normalize_prices(raw)
            df = df.loc[df.index <= cutoff]
            if df.empty or df.index[-1] != cutoff:
                latest = "none" if df.empty else str(df.index[-1].date())
                lag = (not df.empty) and df.index[-1] < cutoff and \
                    len(market_schedule(str(df.index[-1].date()), str(cutoff.date()))) == 2
                if lag:
                    print(f"::warning::{ticker}: bar for {cutoff.date()} not available yet "
                          f"(attempt {attempt + 1}/{attempts}, source={'history' if use_history else 'download'}, "
                          f"{_stale_diagnostic(raw, cutoff)})")
                    _YAHOO_LAG["tickers"].add(ticker)
                    if not _YAHOO_LAG["tripped"] and len(_YAHOO_LAG["tickers"]) >= _YAHOO_BREAKER_TRIP_AT:
                        _YAHOO_LAG["tripped"] = True
                        print("::warning::Yahoo is failing broadly this run; later tickers get one "
                              "probe before the rescued-bar cache and Massive prev fallback")
                    use_history = not use_history  # alternate endpoints between attempts
                    wait = min(stale_retry_delay * (attempt + 1), _STALE_WAIT_BUDGET["left"])
                    _STALE_WAIT_BUDGET["left"] -= wait
                if lag and attempt + 1 == attempts:
                    bar = _cached_bar(ticker, cutoff)
                    if bar is None:
                        bar = _massive_grouped_bar(ticker, cutoff)
                    if bar is None:
                        bar = _massive_prev_bar(ticker, cutoff)
                    if bar is not None:
                        df = normalize_prices(pd.concat([df, bar]))
                        df = df.loc[df.index <= cutoff]
                        if not df.empty and df.index[-1] == cutoff:
                            print(f"::notice::{ticker}: latest bar {cutoff.date()} supplied by Massive fallback")
                            return df
                raise DataQualityError(f"{ticker}: latest valid close {latest}; expected {cutoff.date()}")
            return df
        except Exception as exc:
            # Do not log request URLs, cookies, or credentials from provider exceptions.
            error = exc if isinstance(exc, DataQualityError) else DataQualityError(f"{ticker}: download failed ({type(exc).__name__})")
            if attempt + 1 < attempts:
                time.sleep(max(wait, 0))
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
