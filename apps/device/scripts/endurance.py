#!/usr/bin/env python3
"""Bounded tap-and-capture endurance run through the script SDK, with latency percentiles.

Opens a blank Safari page (about:blank) so the taps land on nothing, then taps one point
repeatedly, capturing after every Nth tap, until the tap count or the time limit is reached.
Evidence is metadata only (timings, states, error codes); images are discarded.

    uv run scripts/endurance.py --taps 200 --minutes 10
"""

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import httpx
from thumbly_sdk import Phone, ThumblyError

SAFARI = "com.apple.mobilesafari"
MAX_ERRORS = 20  # Stop early, keeping the evidence, after this many failed steps.


def percentiles(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)

    def at(q: float) -> float:
        return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 3)

    return {
        "n": len(values),
        "p50": at(0.5),
        "p95": at(0.95),
        "max": round(ordered[-1], 3),
        "mean": round(statistics.fmean(values), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--url", default=os.environ.get("THUMBLY_URL"), help="Thumbly MCP origin (default: this Mac)"
    )
    parser.add_argument("--taps", type=int, default=200)
    parser.add_argument("--minutes", type=float, default=10)
    parser.add_argument("--capture-every", type=int, default=1, help="capture after every N taps (1-25)")
    parser.add_argument("--point", default="0.5,0.45", help="tap point as fractions x,y on the blank page")
    parser.add_argument("--out", type=Path, default=Path(".tmp/validation/endurance"))
    args = parser.parse_args()
    if not 1 <= args.taps <= 1000 or not 0 < args.minutes <= 90:
        parser.error("Bound taps to 1..1000 and duration to 90 minutes")
    if not 1 <= args.capture_every <= 25:
        parser.error("Bound capture cadence to 1..25 taps")
    x, y = (float(v) for v in args.point.split(","))
    args.out.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.out, 0o700)

    taps: list[float] = []
    captures: list[float] = []
    errors: Counter[str] = Counter()
    report: dict = {"started": time.time(), "taps_requested": args.taps, "minutes": args.minutes}
    deadline = time.monotonic() + args.minutes * 60
    last_error: list[str] = [""]

    def attempt(work):
        """Run one step; a failure becomes a counted error instead of ending the run."""
        try:
            return work()
        except ThumblyError as exc:
            last_error[0] = exc.code
        except httpx.HTTPError as exc:
            last_error[0] = type(exc).__name__
        errors[last_error[0]] += 1
        return None

    try:
        with Phone(args.url, name="endurance.py", observe=False) as phone:
            status = attempt(phone.status)
            report["status_before"] = status.get("connection") if status else None
            attempt(lambda: phone.open_url("about:blank", SAFARI).sleep(1.5).screenshot())
            done = 0
            while done < args.taps and time.monotonic() < deadline and sum(errors.values()) <= MAX_ERRORS:
                started = time.monotonic()
                if attempt(lambda: phone.tap(x, y)) is None:
                    if last_error[0] == "FRAME_EXPIRED":
                        attempt(phone.screenshot)  # A stale frame refuses the tap; look again.
                    continue
                taps.append(
                    (phone.last.get("timing") or {}).get("action_seconds", time.monotonic() - started)
                )
                done += 1
                if done % args.capture_every == 0:
                    started = time.monotonic()
                    if attempt(phone.screenshot) is not None:
                        captures.append(time.monotonic() - started)
                if done % 50 == 0:
                    print(f"{done} taps, p95 {percentiles(taps).get('p95')} s", flush=True)
            status = attempt(phone.status)
            report["status_after"] = status.get("connection") if status else None
    except (ThumblyError, httpx.HTTPError) as exc:
        # Approval, pinning or connection failed outright; still keep what was measured.
        errors[getattr(exc, "code", type(exc).__name__)] += 1
    finally:
        # Written on every exit, Ctrl-C included, with whatever was measured so far.
        report |= {
            "finished": time.time(),
            "taps": percentiles(taps),
            "captures": percentiles(captures),
            "errors": dict(errors),
            "passed": bool(taps) and not errors,
        }
        path = args.out / f"endurance-{int(report['started'])}.json"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(report, handle, indent=2)
        print(json.dumps({k: report[k] for k in ("taps", "captures", "errors", "passed")}, indent=2))
        print(f"Evidence: {path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
