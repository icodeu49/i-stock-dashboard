# i-stock-dashboard

US-equity screening with Telegram alerts, an additive watchlist, and a morning
news email. `app.py` is the primary Streamlit interface; the workflows are the
main unattended entry points.

## Automation

| Workflow | Schedule (UTC) | Result |
| --- | --- | --- |
| Automated Daily Watchlist Scanner | 13:18 and 21:18, Monday–Friday | Adds momentum candidates, scans completed periods, sends new alerts and a coverage report |
| Weekly Macro Sector Scan | Monday 13:49 | Ranks 11 sectors, adds up to five leaders from each of the top five sectors, sends the report |
| Daily Morning News Agent | Daily 06:30 | Original five-topic news briefing followed by a separate watchlist and signals section |
| Compare entry timeframes | Manual; validated code updates | Four fixed strategies, historical holdout and downloadable metrics/trades/equity |
| Manual Stock Scan | Manual only | Runs the same stock workflow; no duplicate daily schedule |
| Validate and refresh automations | Relevant code changes to main; manual | Tests first, then sector scan, stock scan, comparison, and email |

GitHub schedules are UTC and can be delayed by the service. UK local time shifts
between GMT and BST. Sector reports show the actual generation time.

Pull requests run regression tests without sending messages. A code update to
`main` runs the live checks only after tests pass. Data-only commits do not trigger
this pipeline. Stock and sector jobs serialize writes to `watchlist.json` and
`alert_state.json`; confirmed deliveries are persisted even if later steps fail.

## Required repository secrets

- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`: stock and sector reports.
- `USER_EMAIL` and `APP_PASSWORD`: Gmail sender/recipient and app password.
  Both must be configured as repository secrets.
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
`requirements-automation.txt` for the unattended scripts. Direct dependency
versions are pinned to the set validated by CI; transitive dependencies are
resolved by pip. CI checks installations and imports on each code update.

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


## Briefing improvements

The original headings remain in their original order: **AI & Tech**, **Global
Markets**, **Macroeconomics**, **AI Power**, and **AI memory HBM companies**.
The concise HTML bullets and spacing remain. A separate **Your Watchlist &
Confirmed Signals** section follows; company news never replaces the original
five topics.

- Two fresh stories per original topic by default (up to ten overall). Set
  `NEWS_PER_TOPIC` from 1 to 5 to adjust. Each item includes its supplied publication
  timestamp and link-check status. First-run news uses a 48-hour window; subsequent
  runs use the last accepted briefing time, with catch-up bounded to 72 hours.
  Undated, future-dated and old search hits are excluded. An empty topic is stated
  explicitly rather than filled with old news.
- Exact and similar headlines are deduplicated within the briefing and against
  seven days of delivery history. Ranking prefers primary institutions and
  financially relevant headline terms within each original topic.
- All anchors are validated against the actual source map. Model-generated URLs,
  malformed extra anchors and active HTML are rejected. Link checks follow at most
  six redirects, reject private destinations and use bounded request timeouts.
  A reachable Google News page is **not** called a verified publisher destination.
  Blocked/paywalled checks remain labelled; confirmed 404/410 links become plain
  source text. A 200 publisher response establishes reachability, not free full-text access.
- AI output is structured and rendered by code. Summaries are based only on the
  supplied headlines and cannot introduce numerical claims. Failures fall back
  to escaped original headlines. Full-article research or inferred investment
  implications are not presented as verified facts.
- The supplement uses the latest completed-session scan snapshot. Stale snapshots
  are labelled rather than presented as new signals. It shows up to ten new dated
  individual-alert candidates, the daily setup digest, recent watchlist changes,
  and up to five company headlines. Company-news search covers eight selected
  watchlist stocks: recent signals plus a rotating sample. Coverage is disclosed;
  it is not a claim to have read news for every stock.
- Original-topic delivery continues if the optional supplement is unavailable.
  News history and emailed signal IDs advance only after SMTP acceptance. A process
  crash after SMTP acceptance but before state persistence can still cause a repeat.
- Each email has plain-text and HTML alternatives. The generated HTML preview is
  retained for 14 days in its Actions run, without recipient headers or credentials.

## Signal policy: weekly-led-v1

- Existing `BREAKOUT_TRIGGERED` calculations remain as a compatibility column for
  the dashboard's historical tools. They are now labelled **momentum setups** when
  triggered by a pocket pivot, accumulation or bullish VSTOP turn.
- **Confirmed price breakout** means the completed close first exceeds the highest
  high of the preceding **20 bars of that timeframe**, with the existing MA50 and
  200-day filters. The current bar is excluded from its own resistance level;
  consecutive closes above their thresholds do not create repeated breakout events.
  The 20-bar definition is a transparent starting rule, not an optimised parameter.
- Weekly signals lead the alert ordering. Monthly signals show their actual closed
  month and provide broader context. Daily signals receive individual alerts only
  when the last completed weekly **and** monthly trend states are bullish.
  Unknown/mixed/bearish higher-timeframe states route daily candidates into one
  compact digest; they do not qualify as bullish alignment. A fresh monthly
  breakout is not required—an established bullish monthly trend is sufficient.
- Bullish trend state requires price above that timeframe's MA50, above the daily
  MA200 and a bullish VSTOP state. Bearish means a bearish VSTOP or price below the
  daily MA200. Other fully warmed states are mixed; missing warm-up is unknown.
- Alerts show the actual trigger, period date, resistance/stop distances, latest
  daily extension, relative strength, and earnings-calendar availability.
  Earnings dates are provider-reported Yahoo dates, may change, and never block
  an otherwise valid price alert. Dates within seven days are flagged.
- Earlier completed-period signals discovered in a new watchlist entry are labelled
  as older setups. Signal IDs remain compatible with previously delivered alerts;
  routing changes do not resend the same individual signals. Digest acknowledgements
  are saved only after successful Telegram delivery.
- `watchlist_history.json` tracks first observation, actual new additions, selection
  reasons, current selection membership and trend changes. Existing stocks receive
  a first-observed timestamp, not a fabricated historical addition date. Failed or
  partial scans cannot establish loss of sector leadership. Manual watchlist fields
  are preserved and stocks are never automatically deleted.

## Measuring daily versus weekly/monthly entries

Run **Compare entry timeframes** in Actions, or `python strategy_comparison.py`.
The default run evaluates five years, with the latest 18 months separated as a
chronological historical holdout. No parameters are fitted or selected on either
window. Each window starts with fresh capital.

Four entry rules—Daily, Weekly, Monthly and Aligned daily—share these assumptions:
100,000 initial capital, fixed 10,000 allocations, at most ten positions, 0.10% fees
and 0.05% slippage on each side. Close-derived entries and weekly VSTOP exits occur
at the following session's open. All strategies also use an 8% fixed stop and
63-session maximum holding period. A gap through a stop fills at the open; positions
remaining at a reporting boundary are liquidated for comparable valuation. Fractional
shares are allowed. These are comparison assumptions, not broker execution guarantees.

Metrics, trades, equity curves, data fingerprints and limitations are uploaded as
an Actions artifact for 30 days; `strategy_report.json` retains the latest summary.
Reports include SPY, returns, drawdowns, trade counts, win rates and small-sample
flags. A current-watchlist backtest has selection/survivorship bias and does not
reconstruct historical additions. The historical holdout is not evidence from the
future and no strategy is automatically declared a winner.

Separately, `forward_observations.json` freezes signals as they are observed from
this deployment onward. Its 20-session cohort study uses the first market open
**after** the observation was recorded, the same fees/slippage, and an adjusted
SPY comparison. Overlapping cohorts are not a portfolio and do not use the historical
portfolio's stop/exit rules. Results remain pending until all 20 subsequent sessions
have completed. This provides genuinely prospective evidence without backdating.

## Deployment gates and state

Pull-request CI tests without live messages. The validated production sequence is:
sectors → stocks → comparison → email. Comparison or optional scan-context failure
cannot suppress the original email after the test gate succeeds. Scheduled workflows
remain independent; stock, sector, email and comparison writes share the same job
concurrency group and rebase state-only commits without force pushes.

The twice-daily stock schedule is retained: the post-close run processes new data;
the pre-open run can catch up after a failed delivery. Previously delivered signals
are suppressed. News is still delivered at 06:30 UTC. No additional credentials are
required for these upgrades.
