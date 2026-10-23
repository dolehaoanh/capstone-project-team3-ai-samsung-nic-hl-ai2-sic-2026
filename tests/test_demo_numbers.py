"""Smoke tests for scripts/demo_numbers.py, so it still runs on demo week."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "demo_numbers.py"


@pytest.fixture(scope="module")
def demo_numbers():
    spec = spec_from_file_location("demo_numbers", SCRIPT)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("value", "digits", "signed", "expected"),
    [
        (0.736, 1, False, "73,6%"),
        (0.0337, 2, True, "+3,37%"),
        (-0.0108, 2, True, "−1,08%"),
        (4382.7054, 0, True, "+438.271%"),
        (0.0, 1, True, "0,0%"),
    ],
)
def test_numbers_are_written_the_way_the_doc_writes_them(demo_numbers, value, digits, signed, expected):
    assert demo_numbers.vi_pct(value, digits, signed=signed) == expected


def test_doc_lookup_accepts_the_doc_spellings(demo_numbers):
    report = demo_numbers.Report("AUC 0,534, accuracy\n   50,9%; hệ số (−0,23); nhóm có 50% tăng")
    assert report.found("accuracy 50,9%")
    assert report.found("-0,23")
    assert report.found("50,0%")
    assert not report.found("51,0%")


def test_script_runs_on_the_bundled_snapshot(demo_numbers, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ALLOW_ONLINE_REFRESH", "false")
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("SNAPSHOT_PATH", raising=False)
    monkeypatch.setenv("LIVE_SNAPSHOT_PATH", str(tmp_path / "vn30_live.csv"))

    assert demo_numbers.main(["--skip-eval"]) == 0
    output = capsys.readouterr().out
    for heading in ("## Bước 1", "## Bước 3", "## Bước 4", "## Mục 6"):
        assert heading in output
    assert "| Thận trọng |" in output
