"""``thumbly-sdk``: approve once, then call any Thumbly tool from a shell or another language.

    thumbly-sdk login
    thumbly-sdk status
    thumbly-sdk screenshot screen.jpg
    thumbly-sdk call thumbly_button button=home observe=false
    thumbly-sdk status https://<mac>.ts.net         # remote access

``url`` is optional everywhere and defaults to Thumbly on this Mac (http://127.0.0.1:8771).

``call`` prints the tool's structured result as JSON; ``key=value`` values are parsed as JSON
when they can be (numbers, true/false, objects) and passed as strings otherwise.
"""

import argparse
import json
import sys
from uuid import uuid4

from thumbly_sdk.errors import ThumblyError
from thumbly_sdk.phone import DEFAULT_URL, Phone


def _value(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="thumbly-sdk", description=__doc__.splitlines()[0])
    parser.add_argument("--name", help="client name shown in the Thumbly app")
    commands = parser.add_subparsers(dest="command", required=True)
    url_help = f"Thumbly origin (default {DEFAULT_URL})"
    for command in ("login", "status"):
        commands.add_parser(command).add_argument("url", nargs="?", help=url_help)
    shot = commands.add_parser("screenshot")
    shot.add_argument("url", nargs="?", help=url_help)
    shot.add_argument("path")
    call = commands.add_parser("call")
    call.add_argument("url", nargs="?", help=url_help)
    call.add_argument("tool")
    call.add_argument("arguments", nargs="*", metavar="key=value")
    args = parser.parse_args(argv)
    if args.command == "call" and args.url and "://" not in args.url:
        # No url given: argparse filled it with the tool name.
        args.arguments, args.tool, args.url = [args.tool, *args.arguments], args.url, None

    phone = Phone(args.url, name=args.name, allow_approval=args.command == "login" or None)
    try:
        if args.command == "login":
            phone.connect()
            print(f"Approved for {phone.url}.", file=sys.stderr)
        elif args.command == "status":
            print(json.dumps(phone.status(), indent=2))
        elif args.command == "screenshot":
            frame = phone.screenshot(format="png" if args.path.lower().endswith(".png") else "jpeg")
            frame.save(args.path)
            print(
                json.dumps(
                    {"path": args.path, "frame_id": frame.id, "width": frame.width, "height": frame.height}
                )
            )
        else:
            arguments = dict(pair.split("=", 1) for pair in args.arguments if "=" in pair)
            values = {key: _value(value) for key, value in arguments.items()}
            schema = phone.mcp.tools().get(args.tool, {})
            if "operation_id" in schema.get("properties", {}):
                values.setdefault("operation_id", str(uuid4()))
            result = phone.mcp.call(args.tool, values)
            print(json.dumps(result.data, indent=2))
            return 1 if result.is_error else 0
    except ThumblyError as exc:
        print(f"thumbly-sdk: {exc}", file=sys.stderr)
        return 2
    finally:
        phone.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
