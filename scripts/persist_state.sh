#!/usr/bin/env bash
set -euo pipefail
git config user.name 'github-actions[bot]'
git config user.email 'github-actions[bot]@users.noreply.github.com'
for state_file in watchlist.json alert_state.json news_state.json scan_snapshot.json watchlist_history.json earnings_cache.json asset_profiles.json forward_observations.json strategy_report.json; do
  if [[ -f "$state_file" ]]; then git add -- "$state_file"; fi
done
if git diff --cached --quiet; then
  echo 'No state changes to persist.'
  exit 0
fi
git commit -m 'Update automation state'
git pull --rebase origin main
git push origin HEAD:main
