from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Iterable

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

COLUMN_ORDER = [
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
]

VN30_SYMBOLS: tuple[str, ...] = (
    "ACB",
    "BCM",
    "BID",
    "BVH",
    "CTG",
    "FPT",
    "GAS",
    "GVR",
    "HDB",
    "HPG",
    "MBB",
    "MSN",
    "MWG",
    "PLX",
    "PNJ",
    "POW",
    "SAB",
    "SHB",
    "SSB",
    "SSI",
    "STB",
    "TCB",
    "TPB",
    "VCB",
    "VHM",
    "VIB",
    "VIC",
    "VJC",
    "VNM",
    "VPB",
)


@dataclass(frozen=True)
class MarketSnapshot:
    frame: pd.DataFrame
    source: str
    retrieved_at: pd.Timestamp
    start_date: str | None = None
    end_date: str | None = None
    symbols: tuple[str, ...] | None = None
    frequency: str = "1D"
    adjusted: bool = True

    def __post_init__(self) -> None:
        if not self.frame.empty and "date" in self.frame.columns:
            parsed_dates = pd.to_datetime(self.frame["date"], errors="coerce").dropna()
            if self.start_date is None and not parsed_dates.empty:
                object.__setattr__(self, "start_date", str(parsed_dates.min().strftime("%Y-%m-%d")))
            if self.end_date is None and not parsed_dates.empty:
                object.__setattr__(self, "end_date", str(parsed_dates.max().strftime("%Y-%m-%d")))
        if not self.frame.empty and "symbol" in self.frame.columns and self.symbols is None:
            valid_symbols = tuple(sorted(self.frame["symbol"].dropna().astype(str).str.strip().unique()))
            object.__setattr__(self, "symbols", valid_symbols)

    def metadata_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "retrieved_at": self.retrieved_at.isoformat(),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "symbols": list(self.symbols or ()),
            "row_count": int(len(self.frame)),
            "frequency": self.frequency,
            "adjusted": self.adjusted,
        }


def validate_market_frame(frame: pd.DataFrame) -> None:
    missing = REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(f"Market data is missing required columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("Market data must contain at least one row")

    # Validate dates
    if frame["date"].isna().any():
        raise ValueError("Market data contains missing date values")
    parsed_dates = pd.to_datetime(frame["date"], errors="coerce")
    if parsed_dates.isna().any():
        raise ValueError("Market data contains invalid date values")
    today = pd.Timestamp.now(tz=None).date()
    if (parsed_dates.dt.date > today).any():
        raise ValueError("Market data contains future dates")

    # Validate symbols
    if frame["symbol"].isna().any() or frame["symbol"].astype(str).str.strip().eq("").any():
        raise ValueError("Market data contains missing or empty symbol values")

    # Validate numeric values
    numeric_cols = ["open", "high", "low", "close", "volume"]
    if frame[numeric_cols].isna().any().any():
        raise ValueError("Market data contains missing numeric values")
    if (frame[numeric_cols] < 0).any().any():
        raise ValueError("Market prices and volume cannot be negative")

    # Validate price relationships
    if (frame["high"] < frame["low"]).any():
        raise ValueError("Market data contains invalid price ranges: high cannot be less than low")

    # Validate duplicate symbol and date entries
    date_normalized = parsed_dates.dt.strftime("%Y-%m-%d")
    symbol_normalized = frame["symbol"].astype(str).str.strip().str.upper()
    duplicates = pd.DataFrame({"symbol": symbol_normalized, "date": date_normalized}).duplicated()
    if duplicates.any():
        raise ValueError("Market data contains duplicate symbol and date entries")


def load_csv_snapshot(path: str | Path) -> MarketSnapshot:
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"Market snapshot not found: {csv_path}")
    frame = pd.read_csv(csv_path, parse_dates=["date"])
    validate_market_frame(frame)
    frame = frame[COLUMN_ORDER].sort_values(["symbol", "date"]).reset_index(drop=True)

    # Attempt to load sidecar metadata if available
    meta_path = csv_path.with_suffix(".metadata.json")
    if not meta_path.is_file():
        meta_path = csv_path.with_suffix(".json")

    if meta_path.is_file():
        try:
            with meta_path.open("r", encoding="utf-8") as f:
                meta = json.load(f)
            source = str(meta.get("source", str(csv_path)))
            retrieved_at_val = meta.get("retrieved_at")
            retrieved_at = pd.Timestamp(retrieved_at_val) if retrieved_at_val else pd.Timestamp.utcnow()
            start_date = meta.get("start_date")
            end_date = meta.get("end_date")
            raw_symbols = meta.get("symbols")
            symbols = tuple(raw_symbols) if raw_symbols is not None else None
            frequency = str(meta.get("frequency", "1D"))
            adjusted = bool(meta.get("adjusted", True))
            return MarketSnapshot(
                frame=frame,
                source=source,
                retrieved_at=retrieved_at,
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
                frequency=frequency,
                adjusted=adjusted,
            )
        except Exception:
            pass

    retrieved_at = pd.Timestamp.utcnow()
    return MarketSnapshot(frame=frame, source=str(csv_path), retrieved_at=retrieved_at)


def write_csv_snapshot(
    snapshot: MarketSnapshot,
    path: str | Path,
    *,
    include_metadata: bool = True,
) -> Path:
    """Persist a validated, sorted snapshot and sidecar metadata for reproducible offline inference."""
    validate_market_frame(snapshot.frame)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame_to_save = snapshot.frame[COLUMN_ORDER].sort_values(["symbol", "date"]).reset_index(drop=True)
    frame_to_save.to_csv(output, index=False)

    if include_metadata:
        meta_path = output.with_suffix(".metadata.json")
        with meta_path.open("w", encoding="utf-8") as f:
            json.dump(snapshot.metadata_dict(), f, indent=2, ensure_ascii=False)
            f.write("\n")

    return output


def get_vn30_symbols(*, online: bool = False) -> list[str]:
    """Return VN30 stock symbols, optionally querying online provider if enabled."""
    if online:
        try:
            from vnstock import Listing

            listing = Listing()
            symbols_series = listing.symbols_by_group("VN30")
            if symbols_series is not None and not symbols_series.empty:
                return sorted(symbols_series.dropna().astype(str).str.strip().unique().tolist())
        except Exception:
            pass
    return list(VN30_SYMBOLS)


def fetch_vietnam_history(
    symbols: Iterable[str],
    *,
    start: str,
    end: str,
    source: str = "VCI",
) -> MarketSnapshot:
    """Fetch daily OHLCV history through optional vnstock; network use is explicit."""
    symbols_list = [s for s in symbols]
    if not symbols_list:
        raise ValueError("At least one symbol is required")
    normalized_symbols: list[str] = []
    for symbol in symbols_list:
        if not symbol or symbol.strip() != symbol:
            raise ValueError(f"Invalid symbol: {symbol!r}")
        normalized_symbols.append(symbol.strip().upper())

    quote_fn = None
    vnstock_v3_cls = None

    try:
        from vnstock import Quote

        quote_fn = Quote
    except ImportError:
        pass

    if quote_fn is None:
        try:
            from vnstock import Vnstock

            vnstock_v3_cls = Vnstock
        except ImportError:
            pass

    if quote_fn is None and vnstock_v3_cls is None:
        raise RuntimeError(
            "Online data refresh requires the optional dependency. "
            "Install it with: pip install --extra-index-url https://vnstocks.com/api/packages vnstock"
        )

    import time

    frames: list[pd.DataFrame] = []
    is_real_vnstock = (
        getattr(quote_fn, "__module__", "").startswith("vnstock")
        or getattr(vnstock_v3_cls, "__module__", "").startswith("vnstock")
    )
    for idx, clean_symbol in enumerate(normalized_symbols):
        if idx > 0 and is_real_vnstock:
            time.sleep(6.5)

        history = None
        for attempt in range(4):
            try:
                if quote_fn is not None:
                    history = quote_fn(symbol=clean_symbol, source=source).history(
                        start=start,
                        end=end,
                    )
                else:
                    history = vnstock_v3_cls().stock(symbol=clean_symbol, source=source).quote.history(
                        start=start,
                        end=end,
                    )
                break
            except Exception as exc:
                exc_msg = str(exc).lower()
                if attempt < 3 and any(k in exc_msg for k in ["hạn mức", "rate", "entitlement", "circuit", "429"]):
                    time.sleep(62)
                    continue
                raise

        if history is None or history.empty:
            raise ValueError(f"No market history returned for symbol {clean_symbol}")

        normalized = history.rename(
            columns={
                "time": "date",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            }
        ).copy()

        # Normalize date column
        date_series = pd.to_datetime(normalized["date"])
        if date_series.dt.tz is not None:
            date_series = date_series.dt.tz_convert("Asia/Ho_Chi_Minh").dt.tz_localize(None)
        normalized["date"] = pd.to_datetime(date_series.dt.strftime("%Y-%m-%d"))
        normalized["symbol"] = clean_symbol

        # Ensure correct numeric types
        for col in ["open", "high", "low", "close", "volume"]:
            normalized[col] = pd.to_numeric(normalized[col])

        frames.append(normalized[COLUMN_ORDER])

    frame = pd.concat(frames, ignore_index=True)
    frame = frame[COLUMN_ORDER].sort_values(["symbol", "date"]).reset_index(drop=True)
    validate_market_frame(frame)

    return MarketSnapshot(
        frame=frame,
        source=f"vnstock:{source}",
        retrieved_at=pd.Timestamp.utcnow(),
    )


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and snapshot Vietnamese equity market data")
    parser.add_argument(
        "--symbols",
        type=str,
        default="VN30",
        help="Comma-separated symbols or 'VN30' (default: VN30)",
    )
    parser.add_argument("--start", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--source", type=str, default="VCI", help="vnstock source provider (default: VCI)")
    parser.add_argument(
        "--output",
        type=str,
        default="data/raw/vn30_snapshot.csv",
        help="Output CSV path (default: data/raw/vn30_snapshot.csv)",
    )

    args = parser.parse_args(argv)

    if args.symbols.strip().upper() == "VN30":
        symbols = get_vn30_symbols(online=True)
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    print(f"Fetching {len(symbols)} symbols from {args.start} to {args.end} via {args.source}...")
    snapshot = fetch_vietnam_history(symbols, start=args.start, end=args.end, source=args.source)
    output_path = write_csv_snapshot(snapshot, args.output)
    print(f"Saved snapshot to {output_path} with {len(snapshot.frame)} rows.")
    print(f"Metadata saved to {output_path.with_suffix('.metadata.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
