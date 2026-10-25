"""scripts/endurance.py keeps its evidence when steps fail."""

import importlib.util
import json
import sys
from pathlib import Path

from thumbly_sdk import FrameExpired, ThumblyError

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "endurance.py"


def load():
    spec = importlib.util.spec_from_file_location("endurance", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FlakyPhone:
    """Tap 2 hits a stale frame, the recapture after it fails, and the final status fails."""

    def __init__(self, *args, **kwargs):
        self.last: dict = {}
        self.taps = 0
        self.statuses = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def status(self):
        self.statuses += 1
        if self.statuses > 1:
            raise ThumblyError("API_UNREACHABLE")
        return {"connection": {"state": "connected"}}

    def open_url(self, *args):
        return self

    def sleep(self, seconds):
        return self

    def screenshot(self):
        if self.taps == 2:
            raise ThumblyError("CAPTURE_TIMEOUT")
        return self

    def tap(self, x, y):
        self.taps += 1
        if self.taps == 2:
            raise FrameExpired("FRAME_EXPIRED", detail={"dispatched": False})
        self.last = {"timing": {"action_seconds": 0.2}}
        return self


def test_failures_are_counted_and_evidence_is_always_written(tmp_path, monkeypatch):
    endurance = load()
    monkeypatch.setattr(endurance, "Phone", FlakyPhone)
    out = tmp_path / "evidence"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "endurance.py",
            "--url",
            "http://127.0.0.1:8771",
            "--taps",
            "4",
            "--capture-every",
            "25",
            "--out",
            str(out),
        ],
    )
    assert endurance.main() == 1  # Errors happened, so it did not pass.
    (evidence,) = out.glob("endurance-*.json")
    report = json.loads(evidence.read_text())
    assert report["taps"]["n"] == 4
    assert report["errors"] == {"FRAME_EXPIRED": 1, "CAPTURE_TIMEOUT": 1, "API_UNREACHABLE": 1}
    assert report["status_before"] == {"state": "connected"} and report["status_after"] is None
    assert report["passed"] is False
