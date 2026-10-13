"""The oceanval window's settings, kept in your home ``.oceanvalrc``.

They sit beside your recipes, under ``"settings"``::

    {"version": 1, "recipes": {...}, "settings": {"theme": "dark", "show_demo": false}}

The file is the one for everywhere (see oceanval.user_recipes.global_path), so
the settings follow you to whichever directory OceanVal runs in. Saving them
reads the whole file and writes it again, so the recipes in it are kept, as
saving a recipe keeps the settings.

Settings must never stop the window opening, so load does not raise: anything
missing or that cannot be used is left at its default.
"""

import contextlib
import json
import os

from oceanval import user_recipes
from oceanval.utils import loud_warning

# the window's colours, in the order the settings show them, with their names
THEMES = {
    "default": "Default",
    "dark": "Dark",
    "silver": "Silver",
    "blue": "Blue",
}

DEFAULTS = {"theme": "default", "show_demo": True}

# a file that is unreadable is said so once, not on every look
_warned = set()


def path():
    """The file the settings are kept in."""
    return user_recipes.global_path()


def _valid(name, value):
    if name == "theme":
        return value in THEMES
    if name == "show_demo":
        return isinstance(value, bool)
    return False


def load():
    """The settings, with the defaults for anything not set or that cannot be
    used."""
    settings = dict(DEFAULTS)
    try:
        where = path()
        if not os.path.exists(where):
            return settings
        with open(where, encoding="utf-8") as handle:
            stored = json.load(handle)
        stored = stored.get("settings") if isinstance(stored, dict) else None
        if not isinstance(stored, dict):
            return settings
        for name in DEFAULTS:
            if name in stored and _valid(name, stored[name]):
                settings[name] = stored[name]
    except Exception as error:
        if f"{error}" not in _warned:
            _warned.add(f"{error}")
            with contextlib.suppress(Exception):
                loud_warning(
                    f"PROBLEM IN {path()}",
                    [f"{error}", "", "The window's settings were left at their defaults."],
                    warning=f"{path()}: {error}",
                )
    return settings


def save(settings):
    """Save the settings given, keeping the rest of the file. Raises
    ValueError if one cannot be used, or if the file cannot be read, as
    writing it again would lose the recipes in it. Returns the settings now
    in force."""
    if not isinstance(settings, dict):
        raise ValueError("The settings could not be read.")
    unknown = sorted(set(settings) - set(DEFAULTS))
    if unknown:
        raise ValueError(f"There is no setting called {', '.join(unknown)}.")
    for name, value in settings.items():
        if not _valid(name, value):
            raise ValueError(f"{value!r} cannot be used for {name}.")
    where = path()
    try:
        data = user_recipes._read_raw(where)
    except (OSError, ValueError) as error:
        raise ValueError(
            f"{where} could not be read, so the settings were not saved, "
            f"as that would lose what is in it: {error}"
        ) from error
    stored = data.get("settings")
    stored = dict(stored) if isinstance(stored, dict) else {}
    stored.update(settings)
    data["settings"] = stored
    user_recipes._write(where, data)
    return load()
