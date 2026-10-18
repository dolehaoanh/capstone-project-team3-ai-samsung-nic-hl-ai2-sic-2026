"""Background refresh of the market snapshot (lane App, Issue #1).

The app always answers from a snapshot already on disk. When that snapshot is missing
the latest closed trading session, one background thread fetches it through lane Data's
`fetch_vietnam_history` and writes the result atomically to a separate *live* file, so
the snapshot tracked by git is never rewritten. See docs/CONTRACTS_VI.md section 4.5.

Everything here is off unless ALLOW_ONLINE_REFRESH is set; the API decides that and
passes the configuration in, so this module never reads the environment itself.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time as _time

import pandas as pd

from .data import MarketSnapshot, fetch_vietnam_history, load_csv_snapshot, write_csv_snapshot

logger = logging.getLogger(__name__)

# Vietnam has observed UTC+7 with no daylight saving since 1975, so a fixed offset is
# exact here and avoids depending on an IANA database being present on the host.
VN_TIMEZONE = timezone(timedelta(hours=7))
# HOSE's closing auction (ATC) ends at 14:45. 15:00 leaves a margin for the provider to
# publish the closing price; anything fetched earlier holds an intraday price.
SESSION_CLOSE_VN = time(15, 0)
# A lock file older than this is treated as left behind by a crashed process. A full
# VN30 fetch takes about 3-4 minutes, plus 62 s per rate-limit retry inside data.py.
LOCK_STALE_AFTER_SECONDS = 60 * 60
DEFAULT_SOURCE = "VCI"


def now_vn() -> datetime:
    return datetime.now(VN_TIMEZONE)


def to_vn(value: datetime | pd.Timestamp) -> datetime:
    """Convert to Vietnam time; a naive value is taken as UTC, as data.py writes it."""
    moment = value.to_pydatetime() if isinstance(value, pd.Timestamp) else value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(VN_TIMEZONE)


def session_close(day: date) -> datetime:
    return datetime.combine(day, SESSION_CLOSE_VN, tzinfo=VN_TIMEZONE)


def latest_closed_session(now: datetime) -> date:
    """Most recent weekday whose session has closed by `now` (Vietnam time).

    Public holidays are not known here. On a holiday this names a day with no session;
    `refresh_target` still behaves, because a fetch made after that day's close cannot
    find anything newer and is therefore not repeated.
    """
    current = to_vn(now)
    day = current.date()
    if day.weekday() < 5 and current >= session_close(day):
        return day
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def refresh_target(retrieved_at: datetime | pd.Timestamp, now: datetime) -> date | None:
    """Session a refresh should fetch up to, or None when the snapshot is up to date.

    The test is on `retrieved_at`, not on `end_date`: a snapshot retrieved after the
    close of the latest closed session already holds everything the provider can give,
    whether or not that day was a trading day. Comparing `end_date` with the calendar
    instead would call every weekend and holiday "stale" and refetch on every request.
    """
    target = latest_closed_session(now)
    return target if to_vn(retrieved_at) < session_close(target) else None


def last_session_is_intraday(data_end: date | None, retrieved_at: datetime | pd.Timestamp) -> bool:
    """True when the snapshot was taken before its own last session closed."""
    return data_end is not None and to_vn(retrieved_at) < session_close(data_end)


def trim_to_session(frame: pd.DataFrame, target: date) -> pd.DataFrame:
    """Drop rows after `target`: a session that has not closed has no closing price."""
    dates = pd.to_datetime(frame["date"]).dt.date
    return frame.loc[dates <= target].reset_index(drop=True)


def fetch_source(snapshot_source: str | None) -> str:
    """Provider to refetch from, taken from a source label such as `vnstock:VCI`."""
    if snapshot_source and snapshot_source.startswith("vnstock:"):
        provider = snapshot_source.split(":", 1)[1].strip()
        if provider:
            return provider
    return DEFAULT_SOURCE


def write_snapshot_atomically(snapshot: MarketSnapshot, path: Path) -> Path:
    """Write CSV and sidecar so a reader sees the old pair or the new pair, never half.

    Both files are staged in a directory next to the target (same filesystem, so
    `os.replace` is atomic) and read back before anything is replaced. The sidecar is
    swapped first: until the CSV follows, a reader gets old prices under new provenance,
    which is cached under the old CSV identity and dropped as soon as the CSV changes.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".refresh-", dir=path.parent))
    try:
        staged_csv = write_csv_snapshot(snapshot, staging / path.name)
        load_csv_snapshot(staged_csv)  # read-back check with the same loader the API uses
        _replace(staged_csv.with_suffix(".metadata.json"), path.with_suffix(".metadata.json"))
        _replace(staged_csv, path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return path


def _replace(source: Path, target: Path, attempts: int = 5) -> None:
    """os.replace, retried briefly: on Windows it fails while a request has the target open."""
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            _time.sleep(0.2 * (attempt + 1))


def _acquire_file_lock(lock_path: Path) -> bool:
    """Cross-process guard, so several uvicorn workers do not fetch at the same time."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = _time.time() - lock_path.stat().st_mtime
            except FileNotFoundError:
                continue  # released between the two calls; try again
            if age < LOCK_STALE_AFTER_SECONDS:
                return False
            logger.warning("Removing stale refresh lock %s (%.0f s old)", lock_path, age)
            lock_path.unlink(missing_ok=True)
            continue
        with os.fdopen(handle, "w", encoding="ascii") as stream:
            stream.write(str(os.getpid()))
        return True
    return False


@dataclass(frozen=True)
class RefreshState:
    running: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    outcome: str | None = None  # "ok" or "error"
    error: str | None = None
    target_session: date | None = None
    rows: int | None = None


@dataclass(frozen=True)
class RefreshConfig:
    live_path: Path
    history_days: int
    retry_after: timedelta


class SnapshotRefresher:
    """At most one background refresh at a time, per process and across processes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = RefreshState()
        self._thread: threading.Thread | None = None

    @property
    def state(self) -> RefreshState:
        with self._lock:
            return self._state

    def maybe_start(
        self, snapshot: MarketSnapshot, config: RefreshConfig, *, now: datetime | None = None
    ) -> bool:
        """Start a refresh in the background if one is due; never blocks on the fetch."""
        now = to_vn(now or now_vn())
        target = refresh_target(snapshot.retrieved_at, now)
        if target is None:
            return False
        with self._lock:
            state = self._state
            if state.running:
                return False
            if state.outcome == "ok" and state.target_session == target:
                # Already fetched this session; the provider simply has nothing newer
                # than the snapshot in use. Do not loop on every request.
                return False
            if (
                state.outcome == "error"
                and state.finished_at is not None
                and now - state.finished_at < config.retry_after
            ):
                return False
            lock_path = config.live_path.with_suffix(".lock")
            if not _acquire_file_lock(lock_path):
                return False
            symbols = list(snapshot.symbols or sorted(snapshot.frame["symbol"].unique()))
            self._state = RefreshState(running=True, started_at=now, target_session=target)
            self._thread = threading.Thread(
                target=self._run,
                args=(symbols, fetch_source(snapshot.source), target, config, lock_path),
                name="snapshot-refresh",
                daemon=True,
            )
            self._thread.start()
        logger.info("Snapshot refresh started for session %s (%d symbols)", target, len(symbols))
        return True

    def _run(
        self, symbols: list[str], source: str, target: date, config: RefreshConfig, lock_path: Path
    ) -> None:
        outcome, error, rows = "error", None, None
        try:
            fetched = fetch_vietnam_history(
                symbols,
                start=(target - timedelta(days=config.history_days)).isoformat(),
                end=target.isoformat(),
                source=source,
            )
            frame = trim_to_session(fetched.frame, target)
            if frame.empty:
                raise ValueError(f"No rows on or before {target} in the fetched history")
            fresh = MarketSnapshot(frame=frame, source=fetched.source, retrieved_at=fetched.retrieved_at)
            write_snapshot_atomically(fresh, config.live_path)
            outcome, rows = "ok", len(frame)
            logger.info("Snapshot refresh wrote %d rows to %s", rows, config.live_path)
        except Exception as exc:  # the worker must never die silently
            error = f"{type(exc).__name__}: {exc}"[:300]
            logger.exception("Snapshot refresh failed for session %s", target)
        finally:
            lock_path.unlink(missing_ok=True)
            with self._lock:
                self._state = replace(
                    self._state,
                    running=False,
                    finished_at=now_vn(),
                    outcome=outcome,
                    error=error,
                    rows=rows,
                )

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the current refresh ends; True when nothing is left running."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def reset(self) -> None:
        """Forget past attempts (tests, or after changing configuration)."""
        self.wait()
        with self._lock:
            self._state = RefreshState()
            self._thread = None

    def status(self, *, enabled: bool, config: RefreshConfig) -> dict[str, object]:
        state = self.state
        return {
            "enabled": enabled,
            "running": state.running,
            "live_path": str(config.live_path),
            "target_session": state.target_session.isoformat() if state.target_session else None,
            "last_started_at": state.started_at.isoformat() if state.started_at else None,
            "last_finished_at": state.finished_at.isoformat() if state.finished_at else None,
            "last_outcome": state.outcome,
            "last_error": state.error,
            "last_rows": state.rows,
        }
