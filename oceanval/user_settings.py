"""The oceanval window's settings, kept in your home ``.oceanvalrc``.

They sit beside your recipes, under ``"settings"``::

    {"version": 1, "recipes": {...},
     "settings": {"show_demo": false,
                  "data_store": "/data/me", "data_store_use": "always"}}

``data_store`` is the folder the data store is kept in (see
oceanval.data_store), "" for none, and ``data_store_use`` how matchup uses it.
Unlike the others, they are not only the window's: every matchup uses them.

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

# how matchup uses the data store, in the order the settings show them
DATA_STORE_USES = {
    "always": "Always use data from your data store, if available",
    "fallback": "Only use the data store when download fails",
}

DEFAULTS = {"show_demo": True, "data_store": "", "data_store_use": "always"}

# a file that is unreadable is said so once, not on every look
_warned = set()


def path():
    """The file the settings are kept in."""
    return user_recipes.global_path()


def _valid(name, value):
    if name == "show_demo":
        return isinstance(value, bool)
    if name == "data_store":
        # a folder for everywhere, so a full path
        return isinstance(value, str) and (value == "" or os.path.isabs(value))
    if name == "data_store_use":
        return value in DATA_STORE_USES
    return False


def _data_store(value, current):
    """The data store's folder as it is kept: the full path of a folder that
    exists, with the store's own folder made in it, or "" for none. Raises
    ValueError saying what is wrong otherwise. The folder already set
    (current) is not checked again, so that a disk that is away for now does
    not stop the other settings being saved."""
    if not isinstance(value, str):
        raise ValueError(f"{value!r} cannot be used for data_store.")
    value = value.strip()
    if value == "":
        return ""
    value = os.path.expanduser(value)
    if not os.path.isabs(value):
        raise ValueError(
            "Give the data store folder's full path, as it is used wherever OceanVal is run."
        )
    value = os.path.normpath(value)
    if value == current:
        return value
    if os.path.exists(value) and not os.path.isdir(value):
        raise ValueError(f"{value} is a file, not a folder.")
    # not imported at the top, as the data store reads the settings
    from oceanval import data_store

    data_store.prepare(value)
    return value


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
    settings = dict(settings)
    if "data_store" in settings:
        settings["data_store"] = _data_store(settings["data_store"], load()["data_store"])
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
