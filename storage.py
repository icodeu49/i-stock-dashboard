"""Validated, atomic local state; Actions commits these files back to the repo."""
import json
import os
from pathlib import Path
import tempfile

BASE_DIR = Path(__file__).resolve().parent
WATCHLIST_FILE = BASE_DIR / "watchlist.json"
ALERT_STATE_FILE = BASE_DIR / "alert_state.json"


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            name = handle.name
            json.dump(value, handle, indent=4, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def load_watchlist(path=WATCHLIST_FILE):
    data = read_json(path)
    if isinstance(data, list):
        data = {ticker: {"group": "Small/Mid Growth"} for ticker in data}
    if not isinstance(data, dict) or not data:
        raise ValueError("Watchlist must be a non-empty dictionary")
    for ticker, metadata in data.items():
        if not isinstance(ticker, str) or not isinstance(metadata, dict):
            raise ValueError("Invalid watchlist entry")
    return data


def merge_candidates(watchlist, candidates):
    """Keep manual entries and enrich missing metadata without overwriting it."""
    added = []
    for ticker, metadata in candidates.items():
        if ticker not in watchlist:
            watchlist[ticker] = dict(metadata)
            added.append(ticker)
        else:
            for key, value in metadata.items():
                if not watchlist[ticker].get(key):
                    watchlist[ticker][key] = value
    return added
