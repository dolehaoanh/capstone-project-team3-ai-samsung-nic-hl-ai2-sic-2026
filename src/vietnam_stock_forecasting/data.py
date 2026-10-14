from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

REQUIRED_COLUMNS = {
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
}


@dataclass(frozen=True)
class MarketSnapshot:
    frame: pd.DataFrame
    source: str
    retrieved_at: pd.Timestamp


def validate_market_frame(frame: pd.DataFrame) -> None:
    missing = REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"Market data is missing required columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("Market data must contain at least one row")
    if frame[["open", "high", "low", "close", "volume"]].isna().any().any():
        raise ValueError("Market data contains missing numeric values")
    if (frame[["open", "high", "low", "close", "volume"]] < 0).any().any():
        raise ValueError("Market prices and volume cannot be negative")


def load_csv_snapshot(path: str | Path) -> MarketSnapshot:
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"Market snapshot not found: {csv_path}")
    frame = pd.read_csv(csv_path, parse_dates=["date"])
    validate_market_frame(frame)
    frame = frame.sort_values(["symbol", "date"]).reset_index(drop=True)
    retrieved_at = pd.Timestamp.utcnow()
    return MarketSnapshot(frame=frame, source=str(csv_path), retrieved_at=retrieved_at)


def write_csv_snapshot(snapshot: MarketSnapshot, path: str | Path) -> Path:
    """Persist a validated, sorted snapshot for reproducible offline inference."""
    validate_market_frame(snapshot.frame)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    snapshot.frame.sort_values(["symbol", "date"]).to_csv(output, index=False)
    return output


def fetch_vietnam_history(
    symbols: Iterable[str],
    *,
    start: str,
    end: str,
    source: str = "VCI",
) -> MarketSnapshot:
    """Fetch daily history through optional vnstock; network use is explicit."""
    try:
        from vnstock import Vnstock
    except ImportError as exc:
        raise RuntimeError(
            "Online data refresh requires the optional dependency. "
            "Install it with: python -m pip install -e '.[data]'"
        ) from exc

    frames: list[pd.DataFrame] = []
    for symbol in symbols:
        if not symbol or symbol.strip() != symbol:
            raise ValueError(f"Invalid symbol: {symbol!r}")
        history = Vnstock().stock(symbol=symbol, source=source).quote.history(
            start=start,
            end=end,
        )
        if history is None or history.empty:
            raise ValueError(f"No market history returned for symbol {symbol}")
        normalized = history.rename(
            columns={
                "time": "date",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            }
        )
        normalized["symbol"] = symbol
        frames.append(normalized[["date", "symbol", "open", "high", "low", "close", "volume"]])

    if not frames:
        raise ValueError("At least one symbol is required")
    frame = pd.concat(frames, ignore_index=True)
    frame["date"] = pd.to_datetime(frame["date"])
    validate_market_frame(frame)
    return MarketSnapshot(
        frame=frame.sort_values(["symbol", "date"]).reset_index(drop=True),
        source=f"vnstock:{source}",
        retrieved_at=pd.Timestamp.utcnow(),
    )
