from thumbly.domain.apps import match_apps

APPS = [
    {"bundle_id": "com.apple.mobilenotes", "name": "Notes"},
    {"bundle_id": "com.apple.mobilesafari", "name": "Safari"},
    {"bundle_id": "com.apple.Preferences", "name": "Settings", "hidden": True},
]


def test_match_by_name_or_bundle_and_hidden_filter():
    assert [a["name"] for a in match_apps(APPS, "SAF")] == ["Safari"]
    assert len(match_apps(APPS, "apple")) == 3
    assert [a["name"] for a in match_apps(APPS, " notes ")] == ["Notes"]
    assert all(not a.get("hidden") for a in match_apps(APPS, "", include_hidden=False))
    assert match_apps(APPS) == APPS
