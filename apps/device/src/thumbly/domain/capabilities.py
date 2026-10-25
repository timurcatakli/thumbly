from thumbly.domain.models import BUTTONS

SHORTCUTS = {
    "home": ("h", ["cmd"]),
    "spotlight": ("space", ["cmd"]),
    "select_all": ("a", ["cmd"]),
    "copy": ("c", ["cmd"]),
    "paste": ("v", ["cmd"]),
    "undo": ("z", ["cmd"]),
}

# HID usage page 0x0C (Consumer) codes sent through the CoreDevice Indigo button service,
# with the hold between the down and up events. Lock and Siri need a real hold.
BUTTON_EVENTS = {
    "home": (0x0C, 0x40, 0.05),
    "lock": (0x0C, 0x30, 0.5),
    "volume_up": (0x0C, 0xE9, 0.05),
    "volume_down": (0x0C, 0xEA, 0.05),
    "mute": (0x0C, 0xE2, 0.05),
    "siri": (0x0C, 0xCF, 1.0),
}
assert set(BUTTON_EVENTS) == set(BUTTONS)


def capabilities(validated: list[str]):
    return {
        "actions": [
            "tap",
            "long_press",
            "swipe",
            "type_text",
            "key",
            "button",
            "launch_app",
            "send_text",
            "open_url",
        ],
        "buttons": list(BUTTONS),
        "shortcuts": [s for s in SHORTCUTS if s in validated],
        "unavailable": ["multitouch", "pointer_move", "landscape"],
        "text": "type_text: up to 200 printable U.S. ASCII keystrokes; send_text: up to 10000 Unicode characters "
        "(16 KB of UTF-8) through the clipboard and Cmd+V, replacing the phone clipboard",
        "coordinates": "Normalized [0,1] within the returned portrait phone image",
        "launch_app": "Foreground an installed app by bundle identifier (see apps); a running app keeps its state",
        "open_url": "http(s) opens in Safari without restarting it; other schemes cold-launch the given bundle_id "
        "with the URL, so that app loses its state",
        "visual_confirmation_required": True,
    }
