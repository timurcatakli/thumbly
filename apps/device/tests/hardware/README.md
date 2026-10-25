Hardware checks are opt-in because they send input to an owner-bound phone. Use
`scripts/endurance.py` (bounded taps and captures through the SDK, latency percentiles,
metadata-only evidence) and `scripts/bench.py` for step-by-step checks; both need the script
approved once in the Thumbly app. Store real screenshots, identities, and raw evidence only under
ignored `.tmp/validation`. Do not turn simulator results into hardware passes. Run the matrix in
both transports: USB and Wi-Fi are separate acceptance rows.
Smoke test: approve a script, open an app, tap and swipe, press a hardware button, inspect
SUCCEEDED plus the post-action image, list apps, Pause from the Thumbly app, and confirm the next
action reports PAUSED; then remove the script under Access and confirm the next call fails.
