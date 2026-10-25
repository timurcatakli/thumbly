import pytest

from thumbly.domain.errors import ThumblyError
from thumbly.domain.profiles import AppProfile, ProfileStore


def test_shipped_profiles_load_and_reference_only_known_links():
    profiles = ProfileStore().load()
    assert {"com.burbn.instagram", "net.whatsapp.WhatsApp", "com.apple.mobilesafari"} <= set(profiles)
    for profile in profiles.values():
        for flow in profile.workflows.values():
            for step in flow.steps:
                assert step.kind in {
                    "open",
                    "tap",
                    "long_press",
                    "swipe",
                    "send_text",
                    "type_text",
                    "key",
                    "button",
                    "wait",
                    "check",
                }
        described = profile.describe()
        for flow in described["workflows"].values():
            assert all({"kind", "value", "expect"} <= set(step) for step in flow["steps"])


def test_link_rendering_requires_exact_params():
    instagram = ProfileStore().get("com.burbn.instagram")
    assert instagram.link("profile", {"username": "eonminion"}) == "instagram://user?username=eonminion"
    with pytest.raises(ThumblyError, match="missing"):
        instagram.link("profile", {})
    with pytest.raises(ThumblyError, match="unexpected"):
        instagram.link("profile", {"username": "x", "extra": "y"})
    assert instagram.link("profile", {"username": "a b"}) == "instagram://user?username=a%20b"
    whatsapp = ProfileStore().get("net.whatsapp.WhatsApp")
    assert (
        whatsapp.link("chat_with_text", {"phone": "15555550123", "text": "hi there & bye"})
        == "whatsapp://send?phone=15555550123&text=hi%20there%20%26%20bye"
    )
    safari = ProfileStore().get("com.apple.mobilesafari")
    assert safari.link("page", {"url": "https://example.org/a?b=c"}) == "https://example.org/a?b=c"
    with pytest.raises(ThumblyError, match="whitespace"):
        safari.link("page", {"url": "https://example.org/a b"})
    with pytest.raises(ThumblyError, match="control"):
        whatsapp.link("chat_with_text", {"phone": "1", "text": "a\nb"})
    with pytest.raises(ThumblyError, match="no deep link"):
        instagram.link("nope", {})


def test_profile_validation_rejects_bad_steps_and_unknown_links():
    base = {"bundle_id": "com.example.app", "name": "Example", "verified": {"date": "2026-09-29"}}
    with pytest.raises(ValueError, match="exactly one"):
        AppProfile.model_validate(
            {**base, "workflows": {"w": {"goal": "g", "steps": [{"tap": "a", "wait": 1}]}}}
        )
    with pytest.raises(ValueError, match="unknown deep link"):
        AppProfile.model_validate({**base, "workflows": {"w": {"goal": "g", "steps": [{"open": "missing"}]}}})
    with pytest.raises(ValueError, match="placeholders"):
        AppProfile.model_validate({**base, "deep_links": {"d": {"url": "x://{a}", "params": ["b"]}}})


def test_override_dir_wins_and_bad_files_report_their_name(tmp_path):
    (tmp_path / "com.burbn.instagram.toml").write_text(
        'bundle_id = "com.burbn.instagram"\nname = "IG override"\n[verified]\ndate = "2026-09-29"\n'
    )
    (tmp_path / "._com.burbn.instagram.toml").write_bytes(b"\x00\x05\x16\x07\xa3")  # macOS resource fork
    store = ProfileStore(tmp_path)
    assert store.get("com.burbn.instagram").name == "IG override"
    (tmp_path / "broken.toml").write_text(
        'bundle_id = "com.x.y"\nname = "n"\n[verified]\ndate = "2026-09-29"\n'
    )
    with pytest.raises(ThumblyError, match="broken.toml"):
        ProfileStore(tmp_path).load()
