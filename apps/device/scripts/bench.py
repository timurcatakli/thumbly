#!/usr/bin/env python3
"""Bench helper: run a list of steps against Thumbly through the script SDK.

    uv run scripts/bench.py "launch:com.apple.mobilenotes" shot:1 tap:0.5,0.5 button:home

The first run asks for approval in the dashboard (choose "Until I remove it" for a bench).
Steps:
  shot:<name>                 save a screenshot to <out>/shot_<name>.jpg
  tap:x,y  long_press:x,y     fractions of the last image (or ints for pixels)
  swipe:x,y,x2,y2[,seconds[,hold]]
  launch:<bundle_id>          url:<url>[|<bundle_id>]   link:<bundle>|<link>|k=v|...
  type:<ascii>  send:<text>   key:cmd+l   shortcut:<name>   button:home|lock|...
  clip  apps[:query]  profiles  profile:<bundle>  status  sleep:<seconds>  pause  release
Each step prints its outcome; a failing step prints the error code and the run continues.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from thumbly_sdk import Phone, ThumblyError


def number(text: str) -> int | float:
    return int(text) if text.lstrip("-").isdigit() else float(text)


def run_step(phone: Phone, step: str, out: Path) -> str:
    kind, _, arg = step.partition(":")
    started = time.monotonic()
    if kind == "shot":
        frame = phone.screenshot()
        path = frame.save(out / f"shot_{arg or 'x'}.jpg")
        return f"{frame.width}x{frame.height} -> {path}"
    if kind == "sleep":
        phone.sleep(float(arg))
        return "ok"
    if kind == "clip":
        return repr(phone.clipboard())
    if kind == "apps":
        apps = phone.apps(arg, include_hidden=True)
        names = ", ".join(f"{a.get('name')}={a.get('bundle_id')}" for a in apps[:10])
        return f"{len(apps)} apps in {time.monotonic() - started:.2f}s: {names}"
    if kind == "profiles":
        return json.dumps(phone._read("thumbly_app_profiles"))[:600]
    if kind == "profile":
        return json.dumps(phone._read("thumbly_app_profile", bundle_id=arg))[:600]
    if kind == "status":
        return json.dumps(phone.status())[:600]
    if kind == "pause":
        phone.pause()
    elif kind == "release":
        phone.release()
    elif kind == "launch":
        phone.open_app(arg)
    elif kind == "url":
        url, _, bundle = arg.partition("|")
        phone.open_url(url, bundle)
    elif kind == "link":
        bundle, link, *pairs = arg.split("|")
        phone.open_link(bundle, link, **dict(pair.split("=", 1) for pair in pairs))
    elif kind == "type":
        phone.type(arg)
    elif kind == "send":
        phone.paste(arg.replace("\\n", "\n"))
    elif kind == "key":
        *modifiers, key = arg.split("+")
        phone.key(key, *modifiers)
    elif kind == "shortcut":
        phone.shortcut(arg)
    elif kind == "button":
        phone.button(arg)  # type: ignore[arg-type]
    elif kind in ("tap", "long_press"):
        x, y = map(number, arg.split(","))
        (phone.tap if kind == "tap" else phone.long_press)(x, y)
    elif kind == "swipe":
        x, y, x2, y2, *rest = map(number, arg.split(","))
        phone.swipe(x, y, x2, y2, *(float(v) for v in rest))
    else:
        return "unknown step"
    operation = phone.last.get("operation") or {}
    seconds = (phone.last.get("timing") or {}).get("bridge_seconds")
    return f"{operation.get('state', 'ok')} {seconds if seconds is not None else ''}".strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--url", default=os.environ.get("THUMBLY_URL"), help="Thumbly MCP origin (default: this Mac)"
    )
    parser.add_argument("--out", type=Path, default=Path("/tmp"), help="where shot:<name> saves images")
    parser.add_argument("--no-observe", action="store_true", help="skip the image after each action")
    parser.add_argument("steps", nargs="+")
    args = parser.parse_args()
    with Phone(args.url, name="bench.py", observe=not args.no_observe) as phone:
        for step in args.steps:
            try:
                print(step, "->", run_step(phone, step, args.out), flush=True)
            except ThumblyError as exc:
                print(step, "->", exc.code, exc.message, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
