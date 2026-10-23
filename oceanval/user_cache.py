"""What OceanVal remembers of your earlier work, kept in a ``.oceanvalcache`` file.

For now that is the simulation directories you have matched up, which the
oceanval window offers again when you set up a run, and the years the output
of the simulations set up covers (see oceanval.output_years), which are read
again only when their files change.

The file is in your home directory (or is the file the ``OCEANVALCACHE``
environment variable names, if it is set). OceanVal never ships it and does not
create it until there is something to remember, so installing or upgrading
OceanVal leaves an existing file as it is. When something is added the whole
file is read and written again, so what is already in it is kept; a file that
cannot be read is left alone, and nothing is added to it or removed from it.

A cache must never stop a run, so nothing here raises.
"""

import contextlib
import datetime
import json
import os
import tempfile
import threading

from oceanval.utils import loud_warning

FILE_NAME = ".oceanvalcache"
VERSION = 1
MAX_SIM_DIRS = 20
MAX_OUTPUT_YEARS = 50

# a file that is unreadable is said so once, not on every look
_warned = set()
_cache = {}
_lock = threading.Lock()


def path():
    """The cache file: ``$OCEANVALCACHE`` if that is set, otherwise the one in
    your home directory."""
    custom = os.environ.get("OCEANVALCACHE", "").strip()
    if custom:
        return os.path.abspath(os.path.expanduser(custom))
    return os.path.join(os.path.expanduser("~"), FILE_NAME)


def _say(where, stamp, text):
    if (where, stamp, text) in _warned:
        return
    _warned.add((where, stamp, text))
    loud_warning(
        f"PROBLEM IN {where}",
        [text, "", "It was left as it is. Fix or remove it to have OceanVal remember your simulations."],
        warning=f"{where}: {text}",
    )


def _load(where):
    """The whole file, as it is, and why it cannot be used (None if it can).
    A missing file is an empty one."""
    if not os.path.exists(where):
        return {"version": VERSION, "sim_dirs": []}, None
    try:
        with open(where, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("it should hold a JSON object")
        if not isinstance(data.get("sim_dirs", []), list):
            raise ValueError('"sim_dirs" should be a list')
        version = data.get("version", VERSION)
        if not isinstance(version, int) or version > VERSION:
            raise ValueError(f"it is from a newer OceanVal (version {version})")
    except (OSError, ValueError) as error:
        return None, f"{error}"
    data.setdefault("version", VERSION)
    data.setdefault("sim_dirs", [])
    return data, None


def _paths(entries):
    found = []
    for entry in entries:
        value = entry.get("path") if isinstance(entry, dict) else entry
        if isinstance(value, str) and value and value not in found:
            found.append(value)
    return found


def recent_sim_dirs():
    """The simulation directories matched up before, the latest first."""
    where = path()
    try:
        stat = os.stat(where)
    except OSError:
        return []
    signature = (where, stat.st_mtime_ns, stat.st_size)
    with _lock:
        if signature in _cache:
            return list(_cache[signature])
    data, problem = _load(where)
    if problem:
        _say(where, stat.st_mtime_ns, f"the file could not be read, so it was ignored: {problem}")
        found = []
    else:
        found = _paths(data["sim_dirs"])
    with _lock:
        if len(_cache) > 16:
            _cache.clear()
        _cache[signature] = found
    return list(found)


def record_sim_dir(sim_dir):
    """Remember a simulation directory as the latest used. Returns whether it
    was written."""
    try:
        where = path()
        sim_dir = os.path.abspath(os.path.expanduser(sim_dir))
        data, problem = _load(where)
        if problem:
            _say(where, 0, f"the file could not be used, so nothing was added to it: {problem}")
            return False
        now = datetime.datetime.now().isoformat(timespec="seconds")
        entries = [
            entry for entry in data["sim_dirs"]
            if (entry.get("path") if isinstance(entry, dict) else entry) != sim_dir
        ]
        data["sim_dirs"] = ([{"path": sim_dir, "last_used": now}] + entries)[:MAX_SIM_DIRS]
        _write(where, data)
        return True
    except Exception as error:
        with contextlib.suppress(Exception):
            loud_warning(
                "OCEANVAL COULD NOT UPDATE ITS CACHE",
                [f"{error}", "", "Your run is not affected."],
                warning=f"could not update {path()}: {error}",
            )
        return False


def forget_sim_dir(sim_dir):
    """Take a simulation directory out of the file, leaving all else in it.
    Returns whether it is now not in it (also when it never was)."""
    try:
        where = path()
        data, problem = _load(where)
        if problem:
            _say(where, 0, f"the file could not be used, so nothing was removed from it: {problem}")
            return False
        gone = {sim_dir, os.path.abspath(os.path.expanduser(sim_dir))}
        entries = [
            entry for entry in data["sim_dirs"]
            if (entry.get("path") if isinstance(entry, dict) else entry) not in gone
        ]
        if len(entries) == len(data["sim_dirs"]):
            return True
        data["sim_dirs"] = entries
        _write(where, data)
        return True
    except Exception as error:
        with contextlib.suppress(Exception):
            loud_warning(
                "OCEANVAL COULD NOT UPDATE ITS CACHE",
                [f"{error}", "", "Your run is not affected."],
                warning=f"could not update {path()}: {error}",
            )
        return False


def _key(key):
    return json.dumps(list(key), ensure_ascii=False)


def output_years(key, stamp):
    """The years of a simulation's output as record_output_years kept them
    for key (its directory, depth and file filters), if its files are as
    they were then (stamp, see output_years.signature); otherwise None."""
    try:
        where = path()
        if not os.path.exists(where):
            return None
        data, problem = _load(where)
        if problem:
            return None
        entry = (data.get("output_years") or {}).get(_key(key))
        if isinstance(entry, dict) and entry.get("stamp") == list(stamp):
            found = entry.get("found")
            return found if isinstance(found, dict) else None
    except Exception:
        pass
    return None


def record_output_years(key, stamp, found):
    """Keep the years of a simulation's output, found, for key while its
    files are as stamp says. Returns whether it was written."""
    try:
        where = path()
        data, problem = _load(where)
        if problem:
            _say(where, 0, f"the file could not be used, so nothing was added to it: {problem}")
            return False
        kept = data.get("output_years", {})
        if not isinstance(kept, dict):
            return False
        kept.pop(_key(key), None)
        kept[_key(key)] = {"stamp": list(stamp), "found": found}
        # the latest last, and only so many
        data["output_years"] = dict(list(kept.items())[-MAX_OUTPUT_YEARS:])
        _write(where, data)
        return True
    except Exception as error:
        with contextlib.suppress(Exception):
            loud_warning(
                "OCEANVAL COULD NOT UPDATE ITS CACHE",
                [f"{error}", "", "Your run is not affected."],
                warning=f"could not update {path()}: {error}",
            )
        return False


def _write(where, data):
    directory = os.path.dirname(where) or "."
    os.makedirs(directory, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=FILE_NAME + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=2, ensure_ascii=False)
            out.write("\n")
        os.replace(temporary, where)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(temporary)
        raise
