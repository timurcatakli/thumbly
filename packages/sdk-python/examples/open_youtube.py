#!/usr/bin/env python3
"""Open YouTube on the iPhone connected to Thumbly and save what the screen shows.

    python examples/open_youtube.py                            # Thumbly on this Mac
    python examples/open_youtube.py https://<mac>.ts.net       # remote access

Connecting a script to Thumbly for the first time (one time per machine):

    $ python examples/open_youtube.py
    Connecting to http://127.0.0.1:8771 ...
    Thumbly: approve 'open_youtube.py' in the Thumbly dashboard (http://127.0.0.1:8770, Access tab). Code GZMG-QDXT.
      http://127.0.0.1:8770/access?r=...
      For unattended scripts choose 'Until I remove it'.

    Open the Thumbly dashboard (http://127.0.0.1:8770), find 'open_youtube.py' on the Access
    tab, check that the code matches, pick how long it may stay connected, and click Approve.
    The script continues by itself:

    Thumbly: approved.
    Connected.

The grant is saved in ~/.config/thumbly/sdk/ and refreshed by itself, so later runs connect
without asking. Remove the script on the Access tab to cut it off; delete its file in
~/.config/thumbly/sdk/ to connect it again from scratch.
"""

import sys

from thumbly_sdk import ApprovalRequired, Phone, ThumblyError

YOUTUBE = "com.google.ios.youtube"


def main(url: str | None = None) -> int:
    with Phone(url, name="open_youtube.py") as phone:
        # 1. Connect. With no saved grant this asks the owner to approve in the dashboard and
        #    waits (up to 10 minutes); with one, it only refreshes the token. Without connect()
        #    the first phone call below would do the same.
        print(f"{'Reconnecting' if phone.approved else 'Connecting'} to {phone.url} ...")
        try:
            phone.connect()
        except ApprovalRequired:
            # No terminal (cron, CI): the SDK never waits for approval there. Run once by hand.
            print("Not approved yet: run this once in a terminal and approve it in the Thumbly dashboard.")
            return 2
        print("Connected.")

        # 2. Find the app: launching takes the exact bundle id.
        apps = phone.apps("youtube")
        if not any(app["bundle_id"] == YOUTUBE for app in apps):
            found = ", ".join(f"{app['name']} ({app['bundle_id']})" for app in apps) or "nothing"
            print(f"YouTube is not installed; a search for 'youtube' found {found}.", file=sys.stderr)
            return 1

        # 3. Open it. Thumbly waits for it to draw and returns the new screen.
        phone.open_app(YOUTUBE)
        frame = phone.frame or phone.screenshot()
        path = frame.save("youtube.jpg")
        print(f"YouTube is open ({frame.width}x{frame.height}); screen saved to {path}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2:
        sys.exit(__doc__)
    try:
        sys.exit(main(sys.argv[1] if len(sys.argv) == 2 else None))
    except ThumblyError as exc:
        sys.exit(f"Thumbly: {exc}")
