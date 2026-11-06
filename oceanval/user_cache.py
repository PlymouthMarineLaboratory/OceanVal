"""What OceanVal remembers of your earlier work, kept in a ``.oceanvalcache`` file.

For now that is the simulation directories you have matched up, which the
oceanval window offers again when you set up a run, and the years the output
of the simulations set up covers (see oceanval.output_years), which are read
again only when their files change, and which first-time messages the window
has shown (``seen``, e.g. the note on the data store when Validate is first opened),
and the simulation bank: the names and directories of simulations the user has chosen to
keep (``bank``), which the window offers when a simulation is set up, each with the info
(files, years, size) the user has had extracted for it, if any, and the history of
the work the user asked to have remembered (``history``): the name and the report of
an analysis, a comparison of simulations, a validation or a comparison of validations,
which the window's View previous work pages list and open.

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
import hashlib
import json
import os
import tempfile
import threading

from oceanval.utils import loud_warning

FILE_NAME = ".oceanvalcache"
VERSION = 1
MAX_SIM_DIRS = 20
MAX_OUTPUT_YEARS = 50
# the kinds of work that can be in the history, and the longest name one can have
HISTORY_KINDS = ("analysis", "simcompare", "validation", "comparison")
MAX_HISTORY_NAME = 80

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
        return {"version": VERSION, "sim_dirs": [], "bank": [], "history": []}, None
    try:
        with open(where, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("it should hold a JSON object")
        if not isinstance(data.get("sim_dirs", []), list):
            raise ValueError('"sim_dirs" should be a list')
        if not isinstance(data.get("bank", []), list):
            raise ValueError('"bank" should be a list')
        if not isinstance(data.get("history", []), list):
            raise ValueError('"history" should be a list')
        version = data.get("version", VERSION)
        if not isinstance(version, int) or version > VERSION:
            raise ValueError(f"it is from a newer OceanVal (version {version})")
    except (OSError, ValueError) as error:
        return None, f"{error}"
    data.setdefault("version", VERSION)
    data.setdefault("sim_dirs", [])
    data.setdefault("bank", [])
    data.setdefault("history", [])
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
        if not str(sim_dir).lower().startswith(("http://", "https://")):
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


# ---- the simulation bank: names and directories kept on purpose ----


def _bank_path(sim_dir):
    sim_dir = str(sim_dir).strip()
    if sim_dir.lower().startswith(("http://", "https://")):
        return sim_dir
    return os.path.abspath(os.path.expanduser(sim_dir))


def _bank_entries(entries):
    found = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name, where = entry.get("name"), entry.get("path")
        if isinstance(name, str) and name.strip() and isinstance(where, str) and where:
            kept = {"name": name.strip(), "path": where}
            info = _bank_info(entry.get("info"))
            if info is not None:
                kept["info"] = info
            found.append(kept)
    return found


def _bank_info(info):
    """What extracting a simulation's info kept, if it is well formed, else None:
    {"ndown", "exclude", "require", "files", "variables", "years", "size", "extracted"}.
    variables (how many) is None when it could not be found or the info is from before
    it was kept; that alone does not make the info badly formed."""
    if not isinstance(info, dict):
        return None
    ndown, files, size = info.get("ndown"), info.get("files"), info.get("size")
    for number in (ndown, files, size):
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            return None
    variables = info.get("variables")
    if isinstance(variables, bool) or not isinstance(variables, int) or variables < 0:
        variables = None
    years = info.get("years")
    if years is not None:
        if (
            not isinstance(years, (list, tuple))
            or len(years) != 2
            or not all(isinstance(year, int) and not isinstance(year, bool) for year in years)
        ):
            return None
        years = [years[0], years[1]]
    words = {}
    for key in ("exclude", "require"):
        value = info.get(key) or []
        if not isinstance(value, list) or not all(isinstance(word, str) for word in value):
            return None
        words[key] = list(value)
    return {
        "ndown": ndown,
        "exclude": words["exclude"],
        "require": words["require"],
        "files": files,
        "variables": variables,
        "years": years,
        "size": size,
        "extracted": str(info.get("extracted") or ""),
    }


def bank():
    """The simulations in the bank, as [{"name", "path"}], in the order they were added."""
    where = path()
    try:
        data, problem = _load(where)
    except Exception:
        return []
    if problem:
        with contextlib.suppress(Exception):
            _say(where, os.stat(where).st_mtime_ns, f"the file could not be read, so it was ignored: {problem}")
        return []
    return _bank_entries(data["bank"])


def add_to_bank(name, sim_dir):
    """Keep a simulation's name and directory in the bank. Returns (True, "") or
    (False, why): it is refused when the name is already there (for this directory
    or another) or the directory is already there under another name, so nothing
    in the bank is ever replaced."""
    try:
        name = str(name or "").strip()
        if not name or not str(sim_dir or "").strip():
            return False, "Give the simulation a name and a directory first."
        sim_dir = _bank_path(sim_dir)
        where = path()
        data, problem = _load(where)
        if problem:
            _say(where, 0, f"the file could not be used, so nothing was added to it: {problem}")
            return False, "The OceanVal cache file could not be used, so nothing was added to the bank."
        for entry in _bank_entries(data["bank"]):
            same_name = entry["name"].casefold() == name.casefold()
            same_dir = entry["path"] == sim_dir
            if same_name and same_dir:
                return False, "Already in the bank."
            if same_name:
                return False, f"The name '{entry['name']}' is already in the bank for {entry['path']}."
            if same_dir:
                return False, f"{sim_dir} is already in the bank as '{entry['name']}'."
        now = datetime.datetime.now().isoformat(timespec="seconds")
        data["bank"] = list(data["bank"]) + [{"name": name, "path": sim_dir, "added": now}]
        _write(where, data)
        return True, ""
    except Exception as error:
        with contextlib.suppress(Exception):
            loud_warning(
                "OCEANVAL COULD NOT UPDATE ITS CACHE",
                [f"{error}", "", "Your run is not affected."],
                warning=f"could not update {path()}: {error}",
            )
        return False, "The simulation could not be added to the bank."


def set_bank_info(name, info):
    """Keep what extracting a bank simulation's info found (see _bank_info for the
    keys; the time is added here) with its entry, leaving all else in the file.
    Returns (True, "") or (False, why): it is refused when the simulation is not in
    the bank (it may have been removed meanwhile) or the info is not well formed."""
    try:
        info = _bank_info(dict(info, extracted=datetime.datetime.now().isoformat(timespec="seconds")))
        if info is None:
            return False, "The simulation's info was not in the form expected."
        where = path()
        data, problem = _load(where)
        if problem:
            _say(where, 0, f"the file could not be used, so nothing was added to it: {problem}")
            return False, "The OceanVal cache file could not be used, so the info was not kept."
        wanted = str(name or "").strip().casefold()
        found = False
        for entry in data["bank"]:
            if isinstance(entry, dict) and str(entry.get("name", "")).strip().casefold() == wanted:
                entry["info"] = info
                found = True
        if not found:
            return False, "That simulation is no longer in the bank."
        _write(where, data)
        return True, ""
    except Exception as error:
        with contextlib.suppress(Exception):
            loud_warning(
                "OCEANVAL COULD NOT UPDATE ITS CACHE",
                [f"{error}", "", "Your run is not affected."],
                warning=f"could not update {path()}: {error}",
            )
        return False, "The simulation's info could not be kept."


def remove_from_bank(name):
    """Take a simulation out of the bank by name, leaving all else in the file.
    Returns whether it is now not in it (also when it never was)."""
    try:
        where = path()
        data, problem = _load(where)
        if problem:
            _say(where, 0, f"the file could not be used, so nothing was removed from it: {problem}")
            return False
        name = str(name or "").strip().casefold()
        entries = [
            entry for entry in data["bank"]
            if not (isinstance(entry, dict) and str(entry.get("name", "")).strip().casefold() == name)
        ]
        if len(entries) == len(data["bank"]):
            return True
        data["bank"] = entries
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


def has_seen(name):
    """Whether record_seen noted name before. A file that cannot be read counts
    as seen, as a note could not be kept in it and the user would be told
    every time."""
    try:
        where = path()
        if not os.path.exists(where):
            return False
        data, problem = _load(where)
        if problem:
            return True
        seen = data.get("seen")
        return isinstance(seen, dict) and name in seen
    except Exception:
        return True


def record_seen(name):
    """Note that the user has been shown or has done name (e.g. "validate").
    Returns whether it was written."""
    try:
        where = path()
        data, problem = _load(where)
        if problem:
            _say(where, 0, f"the file could not be used, so nothing was added to it: {problem}")
            return False
        seen = data.get("seen", {})
        if not isinstance(seen, dict):
            return False
        if name in seen:
            return True
        seen[name] = datetime.datetime.now().isoformat(timespec="seconds")
        data["seen"] = seen
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


# ---- the history of work the user asked to keep ----


def history_id(kind, report):
    """A short, stable id for a piece of work: its kind and the real path of its report."""
    where = os.path.realpath(os.path.expanduser(str(report)))
    return hashlib.sha1(f"{kind}\0{where}".encode("utf-8")).hexdigest()[:12]


def clean_history_name(name):
    """The name as it is kept: trimmed, on one line."""
    return " ".join(str(name or "").split())


def check_history_name(name):
    """Why a name cannot be used, or "" if it can (not whether it is taken)."""
    name = clean_history_name(name)
    if not name:
        return "Give it a name."
    if len(name) > MAX_HISTORY_NAME:
        return f"Use a name of at most {MAX_HISTORY_NAME} characters."
    return ""


def _history_entries(entries):
    """The well formed entries of a history, as they are kept; anything else is ignored."""
    found = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kind, name, report = entry.get("kind"), entry.get("name"), entry.get("report")
        if kind not in HISTORY_KINDS or not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(report, str) or not report:
            continue
        landing = entry.get("landing")
        folders = entry.get("folders")
        folders = [folder for folder in folders if isinstance(folder, str) and folder] if isinstance(folders, list) else []
        found.append(
            {
                "id": history_id(kind, report),
                "kind": kind,
                "name": name.strip(),
                "report": report,
                "landing": landing if isinstance(landing, str) and landing else report,
                "folders": folders,
                "added": str(entry.get("added") or ""),
            }
        )
    return found


def _kept_history(raw, keep):
    """The raw entries of a history that stay: those OceanVal cannot make sense of are kept
    exactly as they are (they may be from a newer OceanVal), the others if keep(entry)."""
    kept = []
    for entry in raw:
        parsed = _history_entries([entry])
        if not parsed or keep(parsed[0]):
            kept.append(entry)
    return kept


def history(kind=None):
    """The work in the history whose report is still there, newest first, as
    [{"id", "kind", "name", "report", "landing", "folders", "added"}], those of one kind if
    it is given. Work whose report has been deleted is left out (it stays in the file, in
    case the folder is only unavailable for now)."""
    where = path()
    try:
        data, problem = _load(where)
    except Exception:
        return []
    if problem:
        with contextlib.suppress(Exception):
            _say(where, os.stat(where).st_mtime_ns, f"the file could not be read, so it was ignored: {problem}")
        return []
    found = []
    for entry in _history_entries(data["history"]):
        if kind is not None and entry["kind"] != kind:
            continue
        if os.path.exists(entry["report"]):
            found.append(entry)
    return found


def history_problem(kind, name, report=None):
    """Why a piece of work of this kind cannot be given this name, or "" if it can. A name
    is taken by other work of the same kind, whatever its capitals, whose report is still
    there; the same report again (a run repeated) may keep its name or change it."""
    why = check_history_name(name)
    if why:
        return why
    if kind not in HISTORY_KINDS:
        return "That kind of work is not kept in the history."
    wanted = clean_history_name(name).casefold()
    own = history_id(kind, report) if report else None
    for entry in history(kind):
        if entry["name"].casefold() == wanted and entry["id"] != own:
            return f"The name '{entry['name']}' is already used in your history. Choose another."
    return ""


def record_history(kind, name, report, landing=None, folders=None):
    """Keep a piece of work in the history, at the top. Returns (True, "") or (False, why).
    The same report again replaces its earlier entry (and may change its name); a name
    used by other work of the same kind is refused. Entries whose reports are gone and
    which have this name are replaced."""
    try:
        why = history_problem(kind, name, report)
        if why:
            return False, why
        where = path()
        data, problem = _load(where)
        if problem:
            _say(where, 0, f"the file could not be used, so nothing was added to it: {problem}")
            return False, "The OceanVal cache file could not be used, so the work was not added to your history."
        name = clean_history_name(name)
        report = os.path.abspath(os.path.expanduser(str(report)))
        own = history_id(kind, report)
        kept = _kept_history(
            data["history"],
            lambda old: old["id"] != own
            and not (old["kind"] == kind and old["name"].casefold() == name.casefold()),
        )
        entry = {
            "kind": kind,
            "name": name,
            "report": report,
            "landing": os.path.realpath(str(landing or report)),
            "folders": [os.path.realpath(str(folder)) for folder in (folders or history_folders(landing or report))],
            "added": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        data["history"] = [entry] + kept
        _write(where, data)
        return True, ""
    except Exception as error:
        with contextlib.suppress(Exception):
            loud_warning(
                "OCEANVAL COULD NOT UPDATE ITS CACHE",
                [f"{error}", "", "Your run is not affected."],
                warning=f"could not update {path()}: {error}",
            )
        return False, "The work could not be added to your history."


def remove_history(kind, entry_id):
    """Take a piece of work out of the history. Only the entry goes, never any file.
    Returns whether the file is as it should be afterwards (True if it was not there)."""
    try:
        where = path()
        data, problem = _load(where)
        if problem:
            _say(where, 0, f"the file could not be used, so nothing was removed from it: {problem}")
            return False
        kept = _kept_history(data["history"], lambda old: not (old["kind"] == kind and old["id"] == entry_id))
        if len(kept) == len(data["history"]):
            return True
        data["history"] = kept
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


def require_free_history_name(kind, name, report):
    """For the start of a run asked to be kept in the history: raise ValueError, before any
    work, if name (None = not to be kept) cannot be used for this kind of work whose report
    will be at report."""
    if name is None:
        return
    if not isinstance(name, str):
        raise ValueError("name must be text, the name to give this work in your OceanVal history, or left out.")
    why = history_problem(kind, name, report)
    if why:
        raise ValueError(f"name: {why}")


def keep_work(kind, name, report, landing=None, folders=None):
    """For the end of a run asked to be kept in the history (name is not None): keep it, and
    say so. Never raises, as a run that has worked must not fail at the end."""
    if name is None:
        return
    ok, why = record_history(kind, name, report, landing, folders)
    if ok:
        print(f"OceanVal: added to your history as '{clean_history_name(name)}'", flush=True)
    else:
        print(f"OceanVal: this was not added to your history: {why}", flush=True)


def history_folders(landing):
    """The folder a report's pages are served from, [the ``_build/html`` folder above
    landing], or [] if it is not in one."""
    folder = os.path.dirname(os.path.realpath(str(landing)))
    while folder and folder != os.path.dirname(folder):
        if os.path.basename(folder) == "html" and os.path.basename(os.path.dirname(folder)) == "_build":
            return [folder]
        folder = os.path.dirname(folder)
    return []


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
