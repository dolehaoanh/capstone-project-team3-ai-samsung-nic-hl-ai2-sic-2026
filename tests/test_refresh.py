"""Background snapshot refresh (lane App, Issue #1).

No test here touches the network: `fetch_vietnam_history` is replaced for every test,
and the default replacement fails loudly if anything calls it unexpectedly.
"""

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from vietnam_stock_forecasting import refresh
from vietnam_stock_forecasting.api import REFRESHER, _cached_forecasts, app
from vietnam_stock_forecasting.data import MarketSnapshot, load_csv_snapshot
from vietnam_stock_forecasting.refresh import (
    VN_TIMEZONE,
    latest_closed_session,
    refresh_target,
    trim_to_session,
    write_snapshot_atomically,
)

client = TestClient(app)


def _frame(symbols=("AAA", "BBB"), periods: int = 80, start: str = "2024-01-01") -> pd.DataFrame:
    """Prices that rise and fall, so the model trains on both outcome classes."""
    dates = pd.date_range(start, periods=periods)
    frames = []
    for offset, symbol in enumerate(symbols):
        close = [100 + offset * 50 + abs((i % 40) - 20) * 2 + (i % 5) for i in range(periods)]
        frames.append(
            pd.DataFrame(
                {
                    "date": dates,
                    "symbol": symbol,
                    "open": close,
                    "high": [c + 1 for c in close],
                    "low": [c - 1 for c in close],
                    "close": close,
                    "volume": [1_000 + i * 10 for i in range(periods)],
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _write(path: Path, frame: pd.DataFrame, *, source: str, retrieved_at: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    dates = pd.to_datetime(frame["date"])
    path.with_suffix(".metadata.json").write_text(
        json.dumps(
            {
                "source": source,
                "retrieved_at": retrieved_at,
                "start_date": dates.min().strftime("%Y-%m-%d"),
                "end_date": dates.max().strftime("%Y-%m-%d"),
                "symbols": sorted(frame["symbol"].unique()),
            }
        ),
        encoding="utf-8",
    )
    return path


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class FakeFetch:
    """Stands in for lane Data's fetch_vietnam_history and records every call."""

    def __init__(
        self,
        *,
        error: Exception | None = None,
        gate: threading.Event | None = None,
        periods: int = 120,
    ):
        self.calls: list[dict] = []
        self.error = error
        self.gate = gate
        self.periods = periods

    def __call__(self, symbols, *, start, end, source):
        self.calls.append({"symbols": list(symbols), "start": start, "end": end, "source": source})
        if self.gate is not None:
            self.gate.wait(timeout=10)
        if self.error is not None:
            raise self.error
        return MarketSnapshot(
            frame=_frame(symbols=tuple(symbols), periods=self.periods),  # 120 -> ends 2024-04-29
            source=f"vnstock:{source}",
            retrieved_at=pd.Timestamp.now(tz="UTC"),
        )


def _no_network(*args, **kwargs):
    raise AssertionError("a test reached fetch_vietnam_history without replacing it")


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("ALLOW_ONLINE_REFRESH", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("SNAPSHOT_STALE_DAYS", raising=False)
    monkeypatch.delenv("REFRESH_HISTORY_DAYS", raising=False)
    monkeypatch.delenv("REFRESH_RETRY_MINUTES", raising=False)
    monkeypatch.setenv("LIVE_SNAPSHOT_PATH", str(tmp_path / "raw" / "vn30_live.csv"))
    monkeypatch.setattr(refresh, "fetch_vietnam_history", _no_network)
    REFRESHER.reset()
    _cached_forecasts.cache_clear()
    yield
    REFRESHER.reset()
    _cached_forecasts.cache_clear()


@pytest.fixture
def base(monkeypatch, tmp_path):
    """Write the configured (git-tracked style) snapshot and point the API at it."""

    def _use(*, retrieved_at: str = "2024-03-20T09:00:00+00:00", **frame_kwargs) -> Path:
        path = _write(
            tmp_path / "sample" / "vn30_snapshot.csv",
            _frame(**frame_kwargs),
            source="vnstock:VCI",
            retrieved_at=retrieved_at,
        )
        monkeypatch.setenv("SNAPSHOT_PATH", str(path))
        return path

    return _use


@pytest.fixture
def live_path(tmp_path) -> Path:
    return tmp_path / "raw" / "vn30_live.csv"


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ALLOW_ONLINE_REFRESH", "true")


def _install(monkeypatch, fake: FakeFetch) -> FakeFetch:
    monkeypatch.setattr(refresh, "fetch_vietnam_history", fake)
    return fake


# --- When is a refresh due? -------------------------------------------------------------

VN = VN_TIMEZONE


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 10, 16, 16, 0, tzinfo=VN), date(2026, 10, 16)),  # Friday after close
        (datetime(2026, 10, 16, 15, 0, tzinfo=VN), date(2026, 10, 16)),  # exactly at close
        (datetime(2026, 10, 16, 14, 2, tzinfo=VN), date(2026, 10, 15)),  # Friday intraday
        (datetime(2026, 10, 17, 10, 0, tzinfo=VN), date(2026, 10, 16)),  # Saturday
        (datetime(2026, 10, 18, 23, 0, tzinfo=VN), date(2026, 10, 16)),  # Sunday
        (datetime(2026, 10, 19, 9, 0, tzinfo=VN), date(2026, 10, 16)),  # Monday morning
        # 01:00 UTC Monday is 08:00 Monday in Vietnam: still before Monday's close.
        (datetime(2026, 10, 19, 1, 0, tzinfo=timezone.utc), date(2026, 10, 16)),
    ],
)
def test_latest_closed_session_skips_weekends_and_open_sessions(now, expected):
    assert latest_closed_session(now) == expected


def test_bundled_snapshot_taken_mid_session_is_due_for_its_own_day():
    # The committed snapshot was fetched at 14:02 VN on Friday 16/10, before the close,
    # so its 16/10 row is an intraday price and the refresh must fetch 16/10 again.
    retrieved = datetime(2026, 10, 16, 7, 2, 47, tzinfo=timezone.utc)

    assert refresh_target(retrieved, datetime(2026, 10, 17, 10, 0, tzinfo=VN)) == date(2026, 10, 16)


def test_snapshot_retrieved_after_the_close_is_not_refetched_over_the_weekend():
    retrieved = datetime(2026, 10, 16, 16, 30, tzinfo=VN)

    for hour in (0, 12, 23):
        assert refresh_target(retrieved, datetime(2026, 10, 17, hour, 0, tzinfo=VN)) is None
        assert refresh_target(retrieved, datetime(2026, 10, 18, hour, 0, tzinfo=VN)) is None
    assert refresh_target(retrieved, datetime(2026, 10, 19, 14, 59, tzinfo=VN)) is None
    assert refresh_target(retrieved, datetime(2026, 10, 19, 15, 0, tzinfo=VN)) == date(2026, 10, 19)


def test_rows_after_the_target_session_are_dropped():
    frame = _frame(periods=10)  # 2024-01-01 .. 2024-01-10

    trimmed = trim_to_session(frame, date(2024, 1, 8))

    assert pd.to_datetime(trimmed["date"]).max().date() == date(2024, 1, 8)
    assert len(trimmed) == 16
    assert list(trimmed.index) == list(range(16))


def test_fetch_source_follows_the_snapshot_provider():
    assert refresh.fetch_source("vnstock:TCBS") == "TCBS"
    assert refresh.fetch_source("vnstock:") == "VCI"
    assert refresh.fetch_source("D:/some/file.csv") == "VCI"
    assert refresh.fetch_source(None) == "VCI"


# --- Writing the live file ----------------------------------------------------------------


def test_atomic_write_produces_a_readable_pair_and_leaves_no_staging(live_path):
    snapshot = MarketSnapshot(frame=_frame(), source="vnstock:VCI", retrieved_at=pd.Timestamp.now(tz="UTC"))

    write_snapshot_atomically(snapshot, live_path)

    loaded = load_csv_snapshot(live_path)
    assert loaded.source == "vnstock:VCI"
    assert loaded.end_date == "2024-03-20"
    assert sorted(p.name for p in live_path.parent.iterdir()) == [
        "vn30_live.csv",
        "vn30_live.metadata.json",
    ]


def test_failed_write_keeps_the_previous_live_file(live_path):
    good = MarketSnapshot(frame=_frame(), source="vnstock:VCI", retrieved_at=pd.Timestamp.now(tz="UTC"))
    write_snapshot_atomically(good, live_path)
    before = live_path.read_bytes(), live_path.with_suffix(".metadata.json").read_bytes()

    broken = _frame()
    broken.loc[0, "high"] = 0  # high < low fails validate_market_frame
    with pytest.raises(ValueError):
        write_snapshot_atomically(
            MarketSnapshot(frame=broken, source="x", retrieved_at=pd.Timestamp.now(tz="UTC")),
            live_path,
        )

    assert (live_path.read_bytes(), live_path.with_suffix(".metadata.json").read_bytes()) == before
    assert not [p for p in live_path.parent.iterdir() if p.name.startswith(".refresh-")]


def test_a_fresh_lock_blocks_and_a_stale_lock_is_cleared(tmp_path):
    lock = tmp_path / "vn30_live.lock"
    lock.write_text("12345", encoding="ascii")

    assert refresh._acquire_file_lock(lock) is False

    old = time.time() - refresh.LOCK_STALE_AFTER_SECONDS - 60
    os.utime(lock, (old, old))
    assert refresh._acquire_file_lock(lock) is True
    assert lock.read_text(encoding="ascii") == str(os.getpid())


# --- Through the API ----------------------------------------------------------------------


def test_refresh_is_off_by_default_and_ignores_an_existing_live_file(monkeypatch, base, live_path):
    fake = _install(monkeypatch, FakeFetch())
    base(retrieved_at="2024-03-20T09:00:00+00:00")  # long overdue for a refresh
    _write(live_path, _frame(periods=120), source="vnstock:LIVE", retrieved_at=_now_utc())

    payload = client.get("/forecasts").json()

    assert payload["source"] == "vnstock:VCI", "with refresh off the live file must not be read"
    assert fake.calls == []
    assert client.get("/health").json()["snapshot_refresh"]["enabled"] is False


def test_due_refresh_runs_in_the_background_and_the_next_request_uses_it(
    monkeypatch, enabled, base, live_path
):
    fake = _install(monkeypatch, FakeFetch())
    base()

    first = client.get("/forecasts")
    assert first.status_code == 200
    assert first.json()["source"] == "vnstock:VCI", "the first answer comes from disk at once"
    assert REFRESHER.wait(timeout=10)

    assert len(fake.calls) == 1
    call = fake.calls[0]
    target = latest_closed_session(datetime.now(VN))
    assert call["end"] == target.isoformat()
    assert call["start"] == (target - timedelta(days=365)).isoformat()
    assert call["symbols"] == ["AAA", "BBB"]
    assert call["source"] == "VCI"
    assert live_path.is_file() and live_path.with_suffix(".metadata.json").is_file()
    assert not live_path.with_suffix(".lock").exists(), "the lock must be released"

    second = client.get("/forecasts").json()
    assert second["source"] == "vnstock:VCI"
    assert second["data_end"] == "2024-04-29", "the fresher live snapshot is now in use"
    assert not any("Đang cập nhật" in text for text in second["warnings"])

    status = client.get("/health").json()["snapshot_refresh"]
    assert status["last_outcome"] == "ok"
    assert status["last_rows"] == 240
    assert status["running"] is False

    client.get("/forecasts")
    REFRESHER.wait(timeout=10)
    assert len(fake.calls) == 1, "a snapshot retrieved after the close is not refetched"


def test_successful_fetch_older_than_the_snapshot_is_not_repeated(monkeypatch, enabled, base):
    # The provider can lag behind a snapshot someone committed by hand. The configured
    # snapshot then stays in use and still looks due, so without a memory of the
    # finished attempt every request would start another three-minute fetch.
    fake = _install(monkeypatch, FakeFetch(periods=60))  # ends 2024-02-29
    base()  # ends 2024-03-20

    client.get("/forecasts")
    assert REFRESHER.wait(timeout=10)
    for _ in range(3):
        payload = client.get("/forecasts").json()
        REFRESHER.wait(timeout=10)

    assert payload["data_end"] == "2024-03-20"
    assert len(fake.calls) == 1


def test_concurrent_requests_start_a_single_fetch(monkeypatch, enabled, base):
    gate = threading.Event()
    fake = _install(monkeypatch, FakeFetch(gate=gate))
    base()

    try:
        payloads = [client.get("/forecasts").json() for _ in range(5)]
        assert len(fake.calls) == 1
        assert all(any("Đang cập nhật" in text for text in p["warnings"]) for p in payloads)
        assert client.get("/health").json()["snapshot_refresh"]["running"] is True
    finally:
        gate.set()
    assert REFRESHER.wait(timeout=10)


def test_failed_refresh_keeps_serving_and_waits_before_retrying(monkeypatch, enabled, base, live_path):
    fake = _install(monkeypatch, FakeFetch(error=RuntimeError("429 rate limited")))
    base()

    client.get("/forecasts")
    assert REFRESHER.wait(timeout=10)
    response = client.get("/forecasts")

    assert response.status_code == 200
    assert response.json()["source"] == "vnstock:VCI"
    assert any("không thành công" in text for text in response.json()["warnings"])
    assert not live_path.exists()
    status = client.get("/health").json()["snapshot_refresh"]
    assert status["last_outcome"] == "error"
    assert "429" in status["last_error"]
    assert len(fake.calls) == 1, "no retry inside the cooldown"


def test_failure_notice_disappears_once_a_fresh_snapshot_arrives(monkeypatch, enabled, base):
    _install(monkeypatch, FakeFetch(error=RuntimeError("network down")))
    path = base()
    client.get("/forecasts")
    assert REFRESHER.wait(timeout=10)
    assert any("không thành công" in t for t in client.get("/forecasts").json()["warnings"])

    # Someone commits a snapshot retrieved after the close: nothing is due any more, so
    # the earlier failure no longer describes the data on screen.
    base(retrieved_at=_now_utc())
    sidecar = path.with_suffix(".metadata.json")
    stat = sidecar.stat()
    os.utime(sidecar, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert not any("không thành công" in t for t in client.get("/forecasts").json()["warnings"])


def test_replace_retries_while_the_target_is_briefly_locked(monkeypatch, tmp_path):
    source, target = tmp_path / "new.csv", tmp_path / "live.csv"
    source.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    real_replace, failures = os.replace, []

    def flaky(src, dst):
        if len(failures) < 2:
            failures.append(dst)
            raise PermissionError("in use")
        real_replace(src, dst)

    monkeypatch.setattr(refresh.os, "replace", flaky)
    monkeypatch.setattr(refresh._time, "sleep", lambda seconds: None)
    refresh._replace(source, target)

    assert target.read_text(encoding="utf-8") == "new"
    assert len(failures) == 2


def test_unreadable_live_file_falls_back_to_the_configured_snapshot(monkeypatch, enabled, base, live_path):
    _install(monkeypatch, FakeFetch())
    base(retrieved_at=_now_utc())  # up to date, so no refresh starts
    live_path.parent.mkdir(parents=True, exist_ok=True)
    live_path.write_text("date,symbol\nnot,a snapshot\n", encoding="utf-8")

    response = client.get("/forecasts")

    assert response.status_code == 200
    assert response.json()["data_end"] == "2024-03-20"


def test_configured_snapshot_wins_when_it_is_newer_than_the_live_file(monkeypatch, enabled, base, live_path):
    _install(monkeypatch, FakeFetch())
    base(retrieved_at=_now_utc())  # ends 2024-03-20
    _write(live_path, _frame(periods=60), source="vnstock:LIVE", retrieved_at=_now_utc())  # ends 2024-02-29

    assert client.get("/forecasts").json()["data_end"] == "2024-03-20"


def test_configured_snapshot_wins_an_exact_tie(monkeypatch, enabled, base, live_path):
    # Same last session and same retrieval time: nothing is gained by switching, and the
    # configured file is the one the team reviewed.
    _install(monkeypatch, FakeFetch())
    stamp = _now_utc()
    base(retrieved_at=stamp)
    _write(live_path, _frame(), source="vnstock:LIVE", retrieved_at=stamp)

    assert client.get("/forecasts").json()["source"] == "vnstock:VCI"


def test_live_file_is_used_when_refresh_is_on_and_it_is_fresher(monkeypatch, enabled, base, live_path):
    _install(monkeypatch, FakeFetch())
    base(retrieved_at=_now_utc())
    _write(live_path, _frame(periods=120), source="vnstock:LIVE", retrieved_at=_now_utc())

    payload = client.get("/forecasts").json()

    assert payload["source"] == "vnstock:LIVE"
    assert payload["data_end"] == "2024-04-29"


def test_recommendations_use_the_same_snapshot_as_forecasts(monkeypatch, enabled, base, live_path):
    _install(monkeypatch, FakeFetch())
    base(retrieved_at=_now_utc(), symbols=("AAA", "BBB"))
    _write(live_path, _frame(symbols=("CCC", "DDD"), periods=120), source="vnstock:LIVE", retrieved_at=_now_utc())

    payload = client.post(
        "/recommendations",
        json={"confirmed": True, "risk_profile": "balanced", "symbols": ["AAA", "CCC", "DDD"]},
    ).json()

    assert set(payload["symbols"]) == {"CCC", "DDD"}
    assert "AAA" in payload["message"], "symbols only in the older snapshot are reported as skipped"


# --- What the user is told ----------------------------------------------------------------


def test_last_session_taken_before_the_close_is_flagged(base):
    base(retrieved_at="2024-03-20T07:02:00+00:00")  # 14:02 VN on the last session

    warnings = client.get("/forecasts").json()["warnings"]

    assert any("trước giờ đóng cửa" in text and "14:02" in text for text in warnings)


def test_last_session_taken_after_the_close_is_not_flagged(base):
    base(retrieved_at="2024-03-20T08:30:00+00:00")  # 15:30 VN

    warnings = client.get("/forecasts").json()["warnings"]

    assert not any("trước giờ đóng cửa" in text for text in warnings)


def test_rewriting_only_the_sidecar_invalidates_the_cache(base):
    path = base()
    assert client.get("/forecasts").json()["source"] == "vnstock:VCI"

    sidecar = path.with_suffix(".metadata.json")
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    meta["source"] = "vnstock:TCBS"
    sidecar.write_text(json.dumps(meta), encoding="utf-8")
    stat = sidecar.stat()
    os.utime(sidecar, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert client.get("/forecasts").json()["source"] == "vnstock:TCBS"


def test_dashboard_always_shows_the_data_age():
    page = client.get("/").text

    assert "dữ liệu cách đây" in page
    assert "dữ liệu hôm nay" in page
