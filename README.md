# i-stock-dashboard

US-equity screening with Telegram alerts, an additive watchlist, and a morning
news email. `app.py` is the primary Streamlit interface; the workflows are the
main unattended entry points.

## Automation

| Workflow | Schedule (UTC) | Result |
| --- | --- | --- |
| Automated Daily Watchlist Scanner | 13:18 and 21:18, Monday–Friday | Adds momentum candidates, scans completed periods, sends new alerts and a coverage report |
| Weekly Macro Sector Scan | Monday 13:49 | Ranks 11 sectors, adds up to five leaders from each of the top five sectors, sends the report |
| Daily Morning News Agent | Daily 06:30 | Sends the news briefing by email |
| Manual Stock Scan | Manual only | Runs the same stock workflow; no duplicate daily schedule |
| Validate and refresh automations | Relevant code changes to main; manual | Tests first, then sector scan, stock scan, and email |

GitHub schedules are UTC and can be delayed by the service. UK local time shifts
between GMT and BST. Sector reports show the actual generation time.

Pull requests run regression tests without sending messages. A code update to
`main` runs the live checks only after tests pass. Data-only commits do not trigger
this pipeline. Stock and sector jobs serialize writes to `watchlist.json` and
`alert_state.json`; confirmed deliveries are persisted even if later steps fail.

## Required repository secrets

- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`: stock and sector reports.
- `USER_EMAIL` and `APP_PASSWORD`: Gmail sender/recipient and app password.
- `GEMINI_API_KEY`: AI summaries. A temporary model failure is retried; if it
  remains unavailable, email contains a clearly labelled source-headline digest.

Missing delivery credentials and failed Telegram/SMTP delivery fail the job.
SMTP acceptance is logged; inbox arrival is not independently verified.

## Signal and data rules

- All technical calculations start from the same adjusted daily OHLCV history.
- The NYSE calendar handles US market holidays, early closes and daylight saving.
  A session becomes usable 15 minutes after its close; incomplete weeks and
  months are excluded. Signals are dated by their actual last trading session.
- The entry guardrail requires price above the selected timeframe's 50-bar MA
  and the **200-day** MA. The latter remains daily on weekly/monthly views.
- Indicators need 30 daily, 20 weekly or 14 monthly bars. Additional indicator
  warm-up requirements still apply; missing values cannot qualify an entry.
- Missing benchmarks are shown as unavailable, never as zero relative strength.
- Stale/invalid symbols are reported and make the run incomplete. Valid signals
  can still be delivered. Stocks are never silently deleted from the watchlist.
- Sector candidates enrich missing metadata and preserve existing groups and
  custom fields. The candidate universes are explicit lists in the two scanners;
  this is not an exhaustive market-wide search.
- Alerts are deduplicated by symbol, timeframe, completed-period date and signal
  direction. Sector rank in a stock alert means **scanned watchlist peers**.
  State is saved after each successful Telegram message; a crash between remote
  acceptance and state persistence can still cause a repeat on the next run.
- Backtests execute close-derived signals at the next available session open.
  The portfolio simulator uses fixed 4,000 allocations and excludes costs;
  the VectorBT tools separately apply their configured fees and stops.

## Run locally

Use Python 3.11 and install `requirements.txt` for the full dashboard, or
`requirements-automation.txt` for the unattended scripts. Dependencies have
bounded compatibility ranges; CI checks the resolved set on each code update.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
streamlit run app.py
```

The test suite mocks external downloads and deliveries. Calendar tests use the
installed exchange-calendar package. `run_test.py` is a manual live backtest,
separate from the offline regression suite.

Manual production runs, with the required environment variables set:

```bash
python sector_notifier.py
python heatmap_skimmer.py
python notifier.py
python news_agent.py
```

Dashboard edits affect its local watchlist file. Scheduled automation uses the
repository copy; these are not automatically synchronized by Streamlit hosting.

