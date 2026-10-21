"""UI tests for the dashboard served at GET / (lane App, Issue #1).

The page's inline script runs in Node against a minimal fake DOM
(`tests/dashboard_harness.cjs`), with `fetch` and the clock replaced. The happy-path
tests feed it real responses from this API, so a field renamed on either side fails
here instead of leaving a blank table on demo day.

Node is preinstalled on GitHub's ubuntu-latest runners. On a developer machine without
it the Node tests are skipped; on CI (CI=true) they fail instead, so the suite cannot
turn green without running them. The static checks at the top always run.
"""

from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from vietnam_stock_forecasting.api import DISCLAIMER, _cached_forecasts, app


client = TestClient(app)
REPO = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).with_name("dashboard_harness.cjs")
BUNDLED_SNAPSHOT = REPO / "data" / "sample" / "vn30_snapshot.csv"
NODE = shutil.which("node")
ON_CI = os.environ.get("CI") == "true"
needs_node = pytest.mark.skipif(NODE is None and not ON_CI, reason="Node.js is not installed")

# 10:00 in Vietnam on the planned demo day.
DEMO_NOW = "2026-10-23T03:00:00Z"
DEMO_DAY_VN = date(2026, 10, 23)


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLOW_ONLINE_REFRESH", "false")
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("SNAPSHOT_STALE_DAYS", raising=False)
    monkeypatch.setenv("LIVE_SNAPSHOT_PATH", str(tmp_path / "raw" / "vn30_live.csv"))
    monkeypatch.setenv("SNAPSHOT_PATH", str(BUNDLED_SNAPSHOT))


def _page() -> str:
    response = client.get("/")
    assert response.status_code == 200
    return response.text


def _script(page: str) -> str:
    scripts = re.findall(r"<script>(.*?)</script>", page, flags=re.S)
    assert len(scripts) == 1, "the dashboard is expected to have exactly one inline script"
    return scripts[0]


def _elements(page: str) -> dict[str, dict[str, object]]:
    """Every element with an id, with the initial state the browser would give it."""
    elements: dict[str, dict[str, object]] = {}
    for tag, before, element_id, after in re.findall(r"<(\w+)\b([^>]*?)\sid=\"([\w-]+)\"([^>]*)>", page):
        attributes = f"{before} {after}"
        props: dict[str, object] = {"disabled": " disabled" in attributes, "checked": " checked" in attributes}
        if tag == "select":
            body = page.split(f'id="{element_id}"', 1)[1].split("</select>", 1)[0]
            props["value"] = re.search(r'<option value="(\w+)"[^>]*\sselected', body).group(1)
        elements[element_id] = props
    return elements


def _node() -> str:
    assert NODE, "Node.js is required to run the dashboard tests on CI"
    return NODE


def _run(tmp_path: Path, *, responses=(), steps=(), now: str = DEMO_NOW) -> dict:
    page = _page()
    payload = {
        "script": _script(page),
        "elements": _elements(page),
        "responses": list(responses),
        "steps": list(steps),
        "now": now,
    }
    spec = tmp_path / "harness-input.json"
    spec.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run(
        [_node(), str(HARNESS), str(spec)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _ok(body: dict) -> dict:
    return {"status": 200, "body": body}


def _forecast_payload(**overrides) -> dict:
    """A small /forecasts body shaped like ForecastResponse."""
    body = {
        "generated_at": "2026-10-23T02:59:00Z",
        "mode": "snapshot",
        "source": "vnstock:VCI",
        "data_start": "2025-10-16",
        "data_end": "2026-10-16",
        "warnings": [],
        "disclaimer": DISCLAIMER,
        "forecasts": [
            {
                "symbol": "AAA",
                "as_of": "2026-10-16",
                "horizon_days": 20,
                "positive_return_probability": 0.6,
                "expected_return": 0.01,
                "expected_volatility": 0.02,
                "confidence": 0.2,
                "model_version": "test-model",
                "evidence": ["Động lượng 20 phiên: +1,0%"],
            }
        ],
    }
    body.update(overrides)
    return body


def _vi_pct(value: float, digits: int) -> str:
    return f"{value * 100:.{digits}f}".replace(".", ",") + "%"


# --- static checks: no Node needed -------------------------------------------------


def test_every_element_the_script_looks_up_exists_in_the_page():
    page = _page()
    used = set(re.findall(r"querySelector\(\"#([\w-]+)\"\)", _script(page)))
    assert used, "expected the script to look elements up by id"
    assert used <= set(_elements(page)), f"missing ids: {sorted(used - set(_elements(page)))}"


def test_page_starts_with_the_shortlist_locked():
    elements = _elements(_page())
    assert elements["advise"]["disabled"] is True
    assert elements["confirm"]["checked"] is False
    assert elements["profile"]["value"] == "balanced"


@needs_node
def test_dashboard_script_is_valid_javascript(tmp_path):
    script = tmp_path / "dashboard.js"
    script.write_text(_script(_page()), encoding="utf-8")
    result = subprocess.run([_node(), "--check", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# --- behaviour in the page ---------------------------------------------------------


@needs_node
def test_page_asks_to_load_forecasts_first(tmp_path):
    initial = _run(tmp_path)["initial"]
    assert initial["advise"]["disabled"] is True
    assert initial["advise-hint"]["textContent"] == "Cần tải dự báo trước."
    assert initial["rows"]["innerHTML"] == ""


@needs_node
def test_real_forecast_response_renders_every_row_in_api_order(tmp_path):
    _cached_forecasts.cache_clear()
    response = client.get("/forecasts")
    assert response.status_code == 200
    data = response.json()

    run = _run(tmp_path, responses=[_ok(data)], steps=["click:load"])
    after = run["states"][0]
    rows = re.findall(r"<tr>(.*?)</tr>", after["rows"]["innerHTML"], flags=re.S)

    assert rows and len(rows) == len(data["forecasts"])
    for row, item in zip(rows, data["forecasts"]):
        assert f"<td>{item['symbol']}</td>" in row
        assert _vi_pct(item["positive_return_probability"], 1) in row
        assert _vi_pct(item["confidence"], 1) in row
    assert after["messages"]["innerHTML"].count('class="warn"') == len(data["warnings"])

    status = after["status"]["textContent"]
    assert f"Chế độ: {data['mode']}" in status
    assert f"Nguồn: {data['source']}" in status
    data_end = date.fromisoformat(data["data_end"])
    assert data_end.strftime("%d/%m/") in status
    assert f"(dữ liệu cách đây {(DEMO_DAY_VN - data_end).days} ngày)" in status

    footer = after["footer"]["textContent"]
    assert data["forecasts"][0]["model_version"] in footer
    assert "Kỳ hạn: 20 phiên" in footer
    assert DISCLAIMER in footer


@needs_node
def test_load_button_is_busy_while_the_request_is_in_flight(tmp_path):
    run = _run(tmp_path, responses=[_ok(_forecast_payload())], steps=["click:load"])
    during = run["calls"][0]["during"]
    assert run["calls"][0]["url"] == "/forecasts"
    assert during["load"]["disabled"] is True
    assert during["load"]["attributes"].get("aria-busy") == "true"
    assert during["status"]["textContent"] == "Đang tải..."

    after = run["states"][0]
    assert after["load"]["disabled"] is False
    assert "aria-busy" not in after["load"]["attributes"]


@pytest.mark.parametrize(
    ("now", "data_end", "expected"),
    [
        ("2026-10-16T09:00:00Z", "2026-10-16", "(dữ liệu hôm nay)"),
        ("2026-10-23T03:00:00Z", "2026-10-16", "(dữ liệu cách đây 7 ngày)"),
        # 00:30 on 17/10 in Vietnam is still 16/10 in UTC: the age must follow Vietnam.
        ("2026-10-16T17:30:00Z", "2026-10-16", "(dữ liệu cách đây 1 ngày)"),
        ("2026-10-15T03:00:00Z", "2026-10-16", "(ngày trong tương lai)"),
    ],
)
@needs_node
def test_data_age_is_counted_in_vietnamese_calendar_days(tmp_path, now, data_end, expected):
    run = _run(tmp_path, responses=[_ok(_forecast_payload(data_end=data_end))], steps=["click:load"], now=now)
    assert expected in run["states"][0]["status"]["textContent"]


@needs_node
def test_server_text_is_escaped_not_injected(tmp_path):
    hostile = "<img src=x onerror=alert(1)>"
    payload = _forecast_payload(warnings=[f"Cảnh báo {hostile}"])
    payload["forecasts"][0]["symbol"] = hostile
    payload["forecasts"][0]["evidence"] = [hostile]

    after = _run(tmp_path, responses=[_ok(payload)], steps=["click:load"])["states"][0]
    for html in (after["rows"]["innerHTML"], after["messages"]["innerHTML"]):
        assert "<img" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"status": 503, "body": {"detail": "Không đọc được snapshot dữ liệu. Thử lại."}},
         "Không đọc được snapshot dữ liệu. Thử lại."),
        # FastAPI's own validation errors carry a list, not a string: use the fallback.
        ({"status": 422, "body": {"detail": [{"loc": ["query", "mode"], "msg": "bad"}]}},
         "Snapshot không đủ lịch sử để tạo dự báo."),
        ({"status": 501, "body": {}}, "Chế độ làm mới trực tuyến chưa được bật trong MVP."),
        ({"status": 500, "body": {}}, "Có lỗi xảy ra."),
    ],
)
@needs_node
def test_forecast_errors_show_a_vietnamese_message(tmp_path, response, expected):
    after = _run(tmp_path, responses=[response], steps=["click:load"])["states"][0]
    assert after["status"]["textContent"] == "Không tải được dự báo."
    assert after["messages"]["innerHTML"] == f'<div class="err">{expected}</div>'
    assert after["rows"]["innerHTML"] == ""


@needs_node
def test_unreachable_server_is_reported(tmp_path):
    after = _run(tmp_path, responses=[{"throw": "Failed to fetch"}], steps=["click:load"])["states"][0]
    assert after["status"]["textContent"] == "Không kết nối được tới máy chủ."
    assert "Failed to fetch" in after["messages"]["innerHTML"]
    assert after["load"]["disabled"] is False


@needs_node
def test_shortlist_unlocks_only_with_forecasts_and_confirmation(tmp_path):
    run = _run(
        tmp_path,
        responses=[_ok(_forecast_payload())],
        steps=["click:load", "check:confirm", "uncheck:confirm"],
    )
    loaded, confirmed, unconfirmed = run["states"]
    assert loaded["advise"]["disabled"] is True
    assert loaded["advise-hint"]["textContent"] == "Cần đánh dấu xác nhận trước."
    assert confirmed["advise"]["disabled"] is False
    assert confirmed["advise-hint"]["textContent"] == ""
    assert unconfirmed["advise"]["disabled"] is True


@needs_node
def test_failed_reload_clears_old_symbols_and_locks_the_shortlist(tmp_path):
    run = _run(
        tmp_path,
        responses=[_ok(_forecast_payload()), {"status": 503, "body": {"detail": "Hỏng."}}],
        steps=["click:load", "check:confirm", "click:load"],
    )
    after = run["states"][-1]
    assert after["rows"]["innerHTML"] == ""
    assert after["footer"]["textContent"] == ""
    assert after["advise"]["disabled"] is True
    assert after["advise-hint"]["textContent"] == "Cần tải dự báo trước."


@needs_node
def test_real_shortlist_round_trip_posts_the_loaded_symbols(tmp_path):
    _cached_forecasts.cache_clear()
    forecasts = client.get("/forecasts").json()
    symbols = [item["symbol"] for item in forecasts["forecasts"]]
    expected_body = {"confirmed": True, "risk_profile": "growth", "symbols": symbols}
    recommendation = client.post("/recommendations", json=expected_body)
    assert recommendation.status_code == 200
    advice = recommendation.json()

    run = _run(
        tmp_path,
        responses=[_ok(forecasts), _ok(advice)],
        steps=["click:load", "check:confirm", "select:profile=growth", "click:advise"],
    )
    post = run["calls"][1]
    assert post["url"] == "/recommendations"
    assert post["method"] == "POST"
    assert post["body"] == expected_body
    assert post["during"]["advise"]["disabled"] is True

    after = run["states"][-1]["advice"]["innerHTML"]
    assert re.findall(r"<li>(\w+)</li>", after) == advice["symbols"]
    assert advice["symbols"], "expected a non-empty growth shortlist from the bundled snapshot"
    assert run["states"][-1]["advise"]["disabled"] is False


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_ok({"symbols": [], "message": "Không có mã."}), "<p>Không có mã nào phù hợp.</p>"),
        ({"status": 400, "body": {"detail": "Cần xác nhận."}}, '<div class="err">Cần xác nhận.</div>'),
        ({"status": 400, "body": {}}, '<div class="err">Cần xác nhận trước khi tạo danh sách.</div>'),
    ],
)
@needs_node
def test_shortlist_empty_and_error_states(tmp_path, response, expected):
    run = _run(
        tmp_path,
        responses=[_ok(_forecast_payload()), response],
        steps=["click:load", "check:confirm", "click:advise"],
    )
    assert expected in run["states"][-1]["advice"]["innerHTML"]
