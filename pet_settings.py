"""
Pet settings shared between the Telegram bot and the hanging pet.

The bot writes pet_settings.json (and the picture, pet_custom.png) when you
change the pet from Telegram; hanging_pet.py watches the file and redraws
itself within a second or so. Anything not set falls back to the defaults in
hanging_pet.py.
"""

import json
import os

from PIL import ImageColor

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "pet_settings.json")
CUSTOM_IMAGE = os.path.join(HERE, "pet_custom.png")

ROPES = ("thread", "chain")


def load():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(d):
    # Write-then-rename, so the pet never reads a half-written file.
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, SETTINGS_FILE)


def update(**changes):
    """Merge changes into the saved settings; a value of None removes a key."""
    d = load()
    for k, v in changes.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    save(d)
    return d


def parse_color(text):
    """'red', 'hot pink' or '#ff8800' -> (r, g, b). Raises ValueError."""
    text = text.strip().lower().replace(" ", "")
    if not text:
        raise ValueError("no colour given")
    if all(c in "0123456789abcdef" for c in text) and len(text) in (3, 6):
        text = "#" + text                      # "ff8800" without the hash
    return ImageColor.getrgb(text)[:3]


def mtime():
    try:
        return os.path.getmtime(SETTINGS_FILE)
    except OSError:
        return None
