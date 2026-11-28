"""Load a local panel or retrieve consistent daily Yahoo Finance ETF histories."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd

from .model import SYMBOLS


PRICE_START = "2006-01-03"
PRICE_END = "2026-09-15"
FIELDS = ["date", "symbol", "open", "close", "adjusted_close"]
YAHOO_HISTORY_URLS = {symbol: f"https://finance.yahoo.com/quote/{symbol}/history/" for symbol in SYMBOLS}


@dataclass(frozen=True)
class MarketData:
    opens: pd.DataFrame
    closes: pd.DataFrame
    provenance: dict


def provenance_path(path):
    return path.with_suffix(path.suffix + ".json")


def load_prices(path):
    """Read normalized daily bars and require complete XNYS session coverage."""
    import exchange_calendars as xcals

    path = Path(path)
    raw = path.read_bytes()
    digest = sha256(raw).hexdigest()
    frame = pd.read_csv(BytesIO(raw), dtype={"date": str, "symbol": str})
    if list(frame.columns) != FIELDS or frame.empty:
        raise ValueError(f"Price CSV must contain columns {', '.join(FIELDS)} in order")
    if set(frame.symbol) != set(SYMBOLS) or frame.duplicated(["date", "symbol"]).any():
        raise ValueError("Price CSV has unexpected assets or duplicate dates for an asset")
    if not frame.date.str.fullmatch(r"\d{4}-\d{2}-\d{2}").fillna(False).all():
        raise ValueError("Price CSV dates must be ISO calendar dates")
    frame["date"] = pd.to_datetime(frame.date, format="%Y-%m-%d", errors="raise")
    quotes = frame[["open", "close", "adjusted_close"]].to_numpy(float)
    if not np.isfinite(quotes).all() or (quotes <= 0).any():
        raise ValueError("Prices must be finite and strictly positive")

    calendar = xcals.get_calendar("XNYS", start=PRICE_START, end=PRICE_END)
    dates = calendar.sessions_in_range(PRICE_START, PRICE_END)
    raw_open = frame.pivot(index="date", columns="symbol", values="open").loc[:, list(SYMBOLS)]
    raw_close = frame.pivot(index="date", columns="symbol", values="close").loc[:, list(SYMBOLS)]
    adjusted_close = frame.pivot(index="date", columns="symbol", values="adjusted_close").loc[:, list(SYMBOLS)]
    if not raw_open.index.equals(dates) or len(frame) != len(dates) * len(SYMBOLS):
        raise ValueError("Prices must match every expected exchange session for every asset")
    adjusted_open = raw_open * (adjusted_close / raw_close)
    if not np.isfinite(adjusted_open.to_numpy()).all() or (adjusted_open <= 0).any().any():
        raise ValueError("Adjusted open calculation produced invalid prices")

    metadata_file = provenance_path(path)
    provenance = {
        "normalized_csv_sha256": digest,
        "exchange_calendar": "XNYS",
        "expected_sessions": len(dates),
        "session_first": PRICE_START,
        "session_last": PRICE_END,
        "observations_per_asset": {symbol: len(dates) for symbol in SYMBOLS},
        "basis": "Provider adjusted close and open multiplied by adjusted close / close",
    }
    if metadata_file.exists():
        metadata = json.loads(metadata_file.read_text())
        if metadata["normalized_csv_sha256"] != digest:
            raise ValueError("Price CSV hash differs from its provider download receipt")
        provenance["provider"] = metadata["provider"]
        provenance["retrieved_at_utc"] = metadata["retrieved_at_utc"]
        provenance["client"] = metadata["client"]
        provenance["history_urls"] = metadata["history_urls"]
    return MarketData(adjusted_open, adjusted_close, provenance)


def fetch_yahoo(path):
    """Write a local reproducibility input and downloader receipt."""
    import yfinance as yf

    path = Path(path)
    exclusive_end = (pd.Timestamp(PRICE_END) + timedelta(days=1)).date().isoformat()
    yf.config.debug.hide_exceptions = False
    histories = []
    for symbol in SYMBOLS:
        history = yf.Ticker(symbol).history(
            start=PRICE_START, end=exclusive_end, interval="1d", actions=False,
            auto_adjust=False, repair=False, rounding=False,
        )
        if history.empty or not {"Open", "Close", "Adj Close"}.issubset(history.columns):
            raise ValueError(f"Missing Yahoo daily history for {symbol}")
        histories.append(pd.DataFrame({
            "date": history.index.date, "symbol": symbol, "open": history.Open.to_numpy(),
            "close": history.Close.to_numpy(), "adjusted_close": history["Adj Close"].to_numpy(),
        }))
    panel = pd.concat(histories).sort_values(["date", "symbol"])
    path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(path, index=False, lineterminator="\n")
    receipt = {
        "provider": "Yahoo Finance",
        "normalized_csv_sha256": sha256(path.read_bytes()).hexdigest(),
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "client": f"yfinance {yf.__version__}",
        "history_urls": YAHOO_HISTORY_URLS,
    }
    provenance_path(path).write_text(json.dumps(receipt, indent=2) + "\n")
    return load_prices(path)


def audit_archived_spy(price_csv, manifest_path):
    """Check split-adjusted SPY opens and closes against a verified local Yahoo archive."""
    manifest = json.loads(Path(manifest_path).read_text())
    entry = manifest["items"]["SPY:1d"]
    if entry["symbol"] != "SPY" or entry["provider"] != "Yahoo" or entry["interval"] != "1d":
        raise ValueError("Archive entry must contain Yahoo SPY daily prices")
    parquet = Path(entry["file"]).read_bytes()
    archive_digest = sha256(parquet).hexdigest()
    if archive_digest != entry["sha256"]:
        raise ValueError("Archive parquet hash differs from its manifest")
    archived = pd.read_parquet(BytesIO(parquet), columns=["datetime_utc", "open", "close"])
    archived["date"] = archived.datetime_utc.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    archived = archived.set_index("date").loc[:, ["open", "close"]]
    current = pd.read_csv(price_csv).query('symbol == "SPY"').set_index("date").loc[:, ["open", "close"]]
    common = current.join(archived, lsuffix="_current", rsuffix="_archive", how="inner", validate="one_to_one")
    if common.empty:
        raise ValueError("The SPY archive has no overlapping dates")
    return {
        "source": "Local Yahoo Finance archive",
        "symbol": "SPY",
        "archived_at_utc": entry["received_at"],
        "parquet_sha256": archive_digest,
        "sessions_compared": len(common),
        "first_session": str(common.index.min()),
        "last_session": str(common.index.max()),
        "maximum_absolute_relative_difference": {
            field: float((common[f"{field}_current"] / common[f"{field}_archive"] - 1).abs().max())
            for field in ("open", "close")
        },
    }
