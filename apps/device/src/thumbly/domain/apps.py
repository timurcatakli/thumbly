"""Finding installed apps by name or bundle identifier, one lookup for every door.

MCP tools, language SDKs and the dashboard pass a query and Thumbly answers, so no client
reimplements matching. Launching then takes the exact bundle identifier found here.
"""


def _fold(value) -> str:
    return str(value or "").strip().casefold()


def match_apps(apps: list[dict], query: str = "", include_hidden: bool = True) -> list[dict]:
    """Apps whose name or bundle identifier contains ``query`` (case-insensitive)."""
    needle = _fold(query)
    return [
        app
        for app in apps
        if (include_hidden or not app.get("hidden"))
        and (not needle or needle in _fold(app.get("name")) or needle in _fold(app.get("bundle_id")))
    ]
