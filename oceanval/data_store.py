"""The data store: the built-in observational data OceanVal downloads, kept in
a folder of your choosing for later matchups.

The folder is set in the oceanval window's Preferences (the ``data_store`` and
``data_store_use`` settings, see oceanval.user_settings), and must exist.
OceanVal keeps everything in an ``oceanval_datastore`` folder inside it. As a
mistake here could cost you files of your own, it keeps to four rules:

- nothing is written, removed or made outside that folder: every path is
  checked to be in it first, as written and with links followed (_guard);
- no file is used or removed unless OceanVal named it, as it names its
  pieces (PIECE_NAME), whatever the index says, and links never are;
- an ``oceanval_datastore`` folder already there with anything in it is only
  used if OceanVal made it (it has the MARKER file, or an index);
- the store is never kept in a folder OceanVal empties (CLEARED_FOLDERS).

What is replaced is not removed for a day, as another run may be reading it.

Only OceanVal's own datasets are kept (WOA23, NSBC, OCCCI, COBE2, GLODAP and
ICES), never data of yours. The store is used in one of two ways:

- ``"always"``: what is in the store is used, and only what is missing is
  downloaded;
- ``"fallback"``: everything is downloaded, as without a store, and what is
  in the store is used only for what cannot be.

Either way, whatever is downloaded is saved in it, replacing what it holds of
the same data. Something saved earlier in the same run is not downloaded
again, so the ICES bottle data every ICES nutrient comes from is downloaded
once a run, not once a nutrient.

What the store holds is in pieces, listed in ``oceanval_data_store.json``:

- gridded data: one of a server's files (a year of it, for COBE2) for one
  variable, cut to a lon/lat box. The servers send whole global fields
  whatever is asked for, and matchup crops them to the model's area, so the
  piece kept is that area rounded out to whole degrees, or the whole globe if
  that area has 90% of it. A wider area later on is downloaded again and
  replaces the piece with one covering both. GLODAP is downloaded as a whole
  file, which is kept whole.
- ICES data: one ICES dataset for one year in a box, with every parameter in
  it, as ICES exports them. The box is cut by ICES, so it is not widened.

Anything asked for inside a piece is taken from it: cropping a crop gives the
same cells as cropping once, so the matchups are the same either way.

The store must never stop a run. A store that cannot be read or written is
said so, and the run carries on as if there were none.
"""

import contextlib
import datetime
import io
import json
import math
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from oceanval.quiet_nc import nc
import pandas as pd

from oceanval import user_settings
from oceanval.session import session_info
from oceanval.utils import loud_warning

try:
    import fcntl
except ImportError:  # pragma: no cover - not on Windows
    fcntl = None

# the folder, inside the one chosen, that everything is kept in
ROOT_NAME = "oceanval_datastore"
# the file that says a folder of that name is OceanVal's: one without it (or
# an index) that has anything in it is someone else's, and is not used
MARKER = ".oceanval_datastore"
INDEX = "oceanval_data_store.json"
VERSION = 1
GLOBAL = [-180, 180, -90, 90]
# the gridded datasets the store keeps, by their recipe key
GRIDDED = ("woa23", "nsbc", "occci", "cobe2", "glodap")
# a gridded dataset kept a year at a time, as its server has every year in one file
BY_YEAR = ("cobe2",)
# a dataset's server address, over thredds, and where the same file can be
# downloaded whole, for when the thredds server will not answer: PSL's limits
# the requests an address can make (HTTP 429), and reading COBE2 over thredds
# takes one for every month of it
DOWNLOADS = {
    "cobe2": {
        "thredds": "https://psl.noaa.gov/thredds/dodsC/Datasets/COBE2/sst.mon.mean.nc",
        "file": "https://downloads.psl.noaa.gov/Datasets/COBE2/sst.mon.mean.nc",
    },
}
# where OceanVal's own datasets are downloaded from: an entry for any other
# address is not built-in data (checked against the recipes by the tests)
BUILTIN_HOSTS = (
    "www.ncei.noaa.gov",
    "psl.noaa.gov",
    "downloads.psl.noaa.gov",
    "www.oceancolour.org",
    "icdc.cen.uni-hamburg.de",
)
# the names OceanVal gives the pieces' files: no other file is ever used or
# removed, whatever the index says
PIECE_NAME = re.compile(r"^(?:" + "|".join(GRIDDED + ("ices",)) + r")/[0-9a-f]{32}\.(?:nc|csv\.gz)$")
# what OceanVal leaves in the store as it writes, removed once old enough
# that no run can still be writing it
LEFTOVER_NAMES = (
    re.compile(r"^\.partial/[0-9a-f]{32}\.(?:nc|csv\.gz)$"),
    re.compile(r"^" + re.escape(INDEX) + r"\.[A-Za-z0-9_]+\.tmp$"),
    re.compile(r"^\.write_check\.[A-Za-z0-9_]+$"),
)
# how long a replaced piece, or something left as it was written, is kept,
# as another run may still be reading or writing it
KEEP_FOR = 24 * 60 * 60
# the folders OceanVal empties or removes as a whole, which the store must
# never be in: validate's oceanval_report and oceanval_results, the interim
# report's oceanval_interim_report (oceanval.live.FOLDER), compare's
# oceanval_comparison, the matchups' oceanval_matchups, and analyze()'s
# oceanval_analysis (oceanval.analysis.FOLDER), and compare_simulations()'s
# oceanval_simulation_comparison (oceanval.sim_compare.FOLDER)
CLEARED_FOLDERS = (
    "oceanval_report",
    "oceanval_results",
    "oceanval_interim_report",
    "oceanval_comparison",
    "oceanval_matchups",
    "oceanval_analysis",
    "oceanval_simulation_comparison",
)

# the ids of the pieces saved during this run, which are not downloaded again
_fresh = set()
# downloads in use that could not be saved: nctoolkit deletes a download once
# no DataSet holds it, so they are held until the next variable's are fetched
_held = []
# what has been said once is not said again
_warned = set()
_size_cache = {}
_free_cache = {}
_lock = threading.Lock()


class OutsideTheStore(ValueError):
    """A path OceanVal was about to write, remove or make is not in the store."""


# --------------------------------------------------------------------------
# where the store is, and keeping to it


def root(folder):
    """The folder everything is kept in, inside the folder chosen."""
    return os.path.join(os.path.abspath(os.path.expanduser(folder)), ROOT_NAME)


def _guard(path, root_dir, allow_root=False):
    """path, if it is inside the store's folder root_dir, both as written and
    once links are followed. Raises OutsideTheStore if not.

    Everything written, removed or made in the store is checked by this
    first, so that nothing outside it is ever touched: not by a mistake here,
    nor by a file name in an index that was edited or damaged.
    """
    if not isinstance(path, str) or not isinstance(root_dir, str) or not path or not root_dir:
        raise OutsideTheStore(f"{path!r} is not a path in the data store")
    for given, base in (
        (os.path.abspath(path), os.path.abspath(root_dir)),
        (os.path.realpath(path), os.path.realpath(root_dir)),
    ):
        if os.path.basename(base) != ROOT_NAME:
            raise OutsideTheStore(f"{base} is not an {ROOT_NAME} folder")
        if ROOT_NAME not in given.split(os.sep):
            raise OutsideTheStore(f"{given} is not in an {ROOT_NAME} folder")
        if os.path.commonpath([given, base]) != base:
            raise OutsideTheStore(f"{given} is not in the data store, {base}")
        if given == base and not allow_root:
            raise OutsideTheStore(f"{given} is the data store itself")
    return path


def _mkdir(path, root_dir, allow_root=False):
    """Make one folder in the store, if it is not there. Its parent must be:
    folders are only ever made one at a time, never a chain of them."""
    _guard(path, root_dir, allow_root=allow_root)
    if not os.path.isdir(path):
        os.mkdir(path)


def _remove(path, root_dir):
    """Remove a file of the store's."""
    _guard(path, root_dir)
    if os.path.isfile(path) and not os.path.islink(path):
        os.remove(path)


def _is_ours(root_dir):
    """Whether an existing oceanval_datastore folder is OceanVal's: it has the
    marker, or an index, or nothing at all in it."""
    if os.path.isfile(os.path.join(root_dir, MARKER)) or os.path.isfile(_index(root_dir)):
        return True
    return len(os.listdir(root_dir)) == 0


def prepare(folder):
    """Make the store's folder in the folder chosen, which must exist, and
    check it can be written to. Returns the store's folder. Raises ValueError
    saying what is wrong otherwise.

    The folder chosen is never made. Nor is the store kept in a folder
    OceanVal empties (see CLEARED_FOLDERS), or in an oceanval_datastore folder
    that is there already with files OceanVal did not put in it.
    """
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        raise ValueError(f"{folder} does not exist. Make it first, or choose a folder that does.")
    for path in (folder, os.path.realpath(folder)):
        cleared = [x for x in path.split(os.sep) if x in CLEARED_FOLDERS]
        if cleared:
            raise ValueError(
                f"The data store cannot be kept in {folder}, as it is in {cleared[0]}, a folder "
                "OceanVal empties each time it makes it. Choose a folder outside it."
            )
    root_dir = root(folder)
    try:
        if os.path.lexists(root_dir):
            if os.path.islink(root_dir) or not os.path.isdir(root_dir):
                raise ValueError(f"{root_dir} is there already, and is not a folder, so it is not used.")
            _guard(root_dir, root_dir, allow_root=True)
            if not _is_ours(root_dir):
                raise ValueError(
                    f"{root_dir} is there already, with files in it OceanVal did not put there, so it "
                    "is not used. Choose another folder, or move what is in that one."
                )
        else:
            _mkdir(root_dir, root_dir, allow_root=True)
        marker = _guard(os.path.join(root_dir, MARKER), root_dir)
        if not os.path.lexists(marker):
            # "x": made new, so nothing is ever written over
            with open(marker, "x", encoding="utf-8") as handle:
                handle.write(
                    "This folder is OceanVal's data store: the built-in observational data it\n"
                    "downloads, kept for later matchups. OceanVal adds to it and removes what it\n"
                    "added. Do not keep files of your own in it.\n"
                )
        handle, probe = tempfile.mkstemp(prefix=".write_check.", dir=root_dir)
        os.close(handle)
        _remove(probe, root_dir)
    except OutsideTheStore as error:
        raise ValueError(f"The data store cannot be kept there: {error}") from error
    except OSError as error:
        raise ValueError(f"OceanVal cannot write to {root_dir}: {error.strerror or error}") from error
    return root_dir


# --------------------------------------------------------------------------
# whether there is a store, for this run


def config(data_store=None):
    """The data store a matchup uses, as {"folder", "root", "use"}, or None
    if none.

    data_store is matchup's argument of the same name: None for the one in
    your settings, False for none.
    """
    if data_store is False:
        return None
    if data_store is not None:
        raise ValueError("data_store must be None, to use the data store in your settings, or False")
    settings = user_settings.load()
    folder = settings.get("data_store") or ""
    if not folder:
        return None
    return {"folder": folder, "root": root(folder), "use": settings.get("data_store_use", "always")}


def start(store):
    """Use store (from config) for the run starting, if it can be."""
    _fresh.clear()
    del _held[:]
    if store is not None:
        try:
            prepare(store["folder"])
            _tidy(store["root"])
        except Exception as error:
            _say(
                "YOUR DATA STORE CANNOT BE USED",
                [f"{error}", "", "The run carries on without it, downloading everything."],
                f"the data store cannot be used: {error}",
            )
            store = None
    session_info["data_store"] = store
    if store is not None:
        print(
            f"Built-in observational data is kept in your data store, {store['root']}: "
            f"{user_settings.DATA_STORE_USES[store['use']].lower()}"
        )


def active():
    """The data store of the run, or None."""
    store = session_info.get("data_store")
    if isinstance(store, dict) and store.get("root"):
        return store
    return None


def keeps(comparison):
    """Whether the store keeps a gridded comparison's data: only that of
    OceanVal's own recipes."""
    return comparison.get("builtin") in GRIDDED


# --------------------------------------------------------------------------
# boxes, as [west, east, south, north]


def snap(lon, lat):
    """The box lon x lat rounded out to whole degrees, or the whole globe if
    it has most of it (see _widen). None if it is not one the store can keep
    (outside -180 to 180, say)."""
    try:
        west, east, south, north = (float(x) for x in (lon[0], lon[1], lat[0], lat[1]))
    except (TypeError, ValueError, IndexError):
        return None
    if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
        return None
    return _widen([
        max(-180, math.floor(west)),
        min(180, math.ceil(east)),
        max(-90, math.floor(south)),
        min(90, math.ceil(north)),
    ])


# a box with at least this much of the globe's area in it is kept as the
# whole globe, as the rest is little more to keep and serves any area later
WHOLE_GLOBE = 0.9


def area_fraction(box):
    """How much of the globe's surface box has in it, from 0 to 1."""
    west, east, south, north = box
    return (east - west) / 360 * (math.sin(math.radians(north)) - math.sin(math.radians(south))) / 2


def _widen(box):
    """box, or the whole globe if box has most of it."""
    return list(GLOBAL) if area_fraction(box) >= WHOLE_GLOBE else box


def contains(outer, inner):
    """Whether box outer has all of box inner in it."""
    return (
        outer[0] <= inner[0] and outer[1] >= inner[1]
        and outer[2] <= inner[2] and outer[3] >= inner[3]
    )


def union(*boxes):
    """The smallest box with all of boxes in it."""
    return [
        min(x[0] for x in boxes),
        max(x[1] for x in boxes),
        min(x[2] for x in boxes),
        max(x[3] for x in boxes),
    ]


def _box(entry):
    """An entry's box, or None if it does not have one that can be used."""
    box = entry.get("box") if isinstance(entry, dict) else None
    if isinstance(box, list) and len(box) == 4 and all(
        isinstance(x, (int, float)) and not isinstance(x, bool) for x in box
    ):
        return box
    return None


def _levels_cover(have, want):
    return have == "all" or have == want


# --------------------------------------------------------------------------
# the index


def _index(root_dir):
    return os.path.join(root_dir, INDEX)


def _say(headline, lines, warning):
    if (headline, warning) in _warned:
        return
    _warned.add((headline, warning))
    with contextlib.suppress(Exception):
        loud_warning(headline, lines, warning=warning)


def _load(root_dir):
    """The index, as it is, and why it cannot be used (None if it can). A
    missing index is an empty one."""
    where = _index(root_dir)
    if not os.path.exists(where):
        return {"version": VERSION, "entries": [], "retired": []}, None
    try:
        with open(where, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("it should hold a JSON object")
        for name in ("entries", "retired"):
            if not isinstance(data.get(name, []), list):
                raise ValueError(f'"{name}" should be a list')
        version = data.get("version", VERSION)
        if not isinstance(version, int) or version > VERSION:
            raise ValueError(f"it is from a newer OceanVal (version {version})")
    except (OSError, ValueError) as error:
        return None, f"{error}"
    data.setdefault("version", VERSION)
    data.setdefault("entries", [])
    data.setdefault("retired", [])
    return data, None


def _file(entry, root_dir):
    """The path of an entry's file, if it is one OceanVal made in the store,
    else None: its name must be one OceanVal gives (PIECE_NAME), so a file of
    anyone else's is never used or removed, whatever the index says."""
    name = entry.get("file") if isinstance(entry, dict) else None
    if not isinstance(name, str) or not PIECE_NAME.match(name):
        return None
    path = os.path.join(root_dir, name)
    try:
        return _guard(path, root_dir)
    except OutsideTheStore:
        return None


def _usable(entry, root_dir):
    """Whether an entry of the index can be used: what it says makes sense,
    and its file, if it has data, is there in the store."""
    if _box(entry) is None:
        return False
    if entry.get("file") is None:
        return entry.get("rows") == 0
    path = _file(entry, root_dir)
    return path is not None and os.path.isfile(path)


def _entries(root_dir):
    """The pieces in the store that can be used. A store that cannot be read
    is said so, and taken as empty."""
    data, problem = _load(root_dir)
    if problem:
        _say(
            f"PROBLEM IN YOUR DATA STORE, {_index(root_dir)}",
            [f"It could not be read, so it was not used: {problem}", "",
             "It was left as it is. Fix or remove it to use your data store again."],
            f"{_index(root_dir)}: {problem}",
        )
        return []
    return [dict(x) for x in data["entries"] if _usable(x, root_dir)]


@contextlib.contextmanager
def _locked(root_dir):
    """Hold the store's lock, so that two runs adding to it at once both do."""
    if fcntl is None:
        yield
        return
    with open(_guard(os.path.join(root_dir, ".lock"), root_dir), "a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write(root_dir, data):
    where = _guard(_index(root_dir), root_dir)
    handle, temporary = tempfile.mkstemp(prefix=INDEX + ".", suffix=".tmp", dir=root_dir)
    try:
        _guard(temporary, root_dir)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=1, ensure_ascii=False)
            out.write("\n")
        os.replace(temporary, where)
    except BaseException:
        with contextlib.suppress(Exception):
            _remove(temporary, root_dir)
        raise


def _version():
    with contextlib.suppress(Exception):
        from importlib.metadata import version

        return version("oceanval")
    return None


def _save(root_dir, entry, write, extension, replaces):
    """Put a piece in the store. write(path) writes its file, or is None for a
    piece without data, and the pieces replaces(old) is true of are taken out.
    Returns the entry saved, or None if it could not be."""
    partial = None
    stored = None
    try:
        # made by prepare as the run started: one that has gone is not made again here
        if not os.path.isdir(root_dir):
            raise ValueError(f"{root_dir} is not there any more")
        entry = dict(entry, id=uuid.uuid4().hex)
        if write is not None:
            folder = "ices" if entry["kind"] == "ices" else entry["dataset"]
            name = f"{folder}/{entry['id']}{extension}"
            if not PIECE_NAME.match(name):
                raise ValueError(f"{name} is not a name OceanVal gives a piece")
            _mkdir(os.path.join(root_dir, ".partial"), root_dir)
            _mkdir(os.path.join(root_dir, folder), root_dir)
            # written aside first, so a download cut short is never a piece
            partial = _guard(os.path.join(root_dir, ".partial", os.path.basename(name)), root_dir)
            write(partial)
            target = _guard(os.path.join(root_dir, name), root_dir)
            os.replace(partial, target)
            partial = None
            stored = target
            entry["file"] = name
            entry["bytes"] = os.path.getsize(stored)
        else:
            entry["file"] = None
            entry["bytes"] = 0
        entry["downloaded"] = datetime.datetime.now().isoformat(timespec="seconds")
        entry["oceanval"] = _version()

        def replaced(old):
            try:
                return _box(old) is not None and bool(replaces(old))
            except Exception:
                return False

        with _locked(root_dir):
            data, problem = _load(root_dir)
            if problem:
                raise ValueError(f"{_index(root_dir)} could not be read, so nothing was added to it: {problem}")
            # what is replaced is not removed yet, as another run may be
            # reading it: _tidy removes it once it is a day old
            now = _now()
            data["retired"] += [
                {"file": x["file"], "bytes": x.get("bytes", 0), "retired": now}
                for x in data["entries"]
                if replaced(x) and isinstance(x.get("file"), str)
            ]
            data["entries"] = [x for x in data["entries"] if not replaced(x)] + [entry]
            _write(root_dir, data)
        _fresh.add(entry["id"])
        return entry
    except Exception as error:
        for made in (partial, stored):
            if made is not None:
                with contextlib.suppress(Exception):
                    _remove(made, root_dir)
        _say(
            "OCEANVAL COULD NOT ADD TO YOUR DATA STORE",
            [f"{error}", "", "What was downloaded is used for this run all the same."],
            f"could not add to the data store in {root_dir}: {error}",
        )
        return None


def _now():
    return time.time()


def _old(seconds):
    return isinstance(seconds, (int, float)) and _now() - seconds > KEEP_FOR


def _tidy(root_dir):
    """Remove what no run can still need, once it is a day old: the pieces
    replaced (listed in the index as "retired"), and what OceanVal leaves as
    it writes (LEFTOVER_NAMES). Only files OceanVal named are removed, and
    only through _remove. Never raises."""
    try:
        if os.path.isfile(_index(root_dir)):
            with _locked(root_dir):
                data, problem = _load(root_dir)
                if problem is None and data["retired"]:
                    in_use = {x.get("file") for x in data["entries"] if isinstance(x, dict)}
                    kept = []
                    for old in data["retired"]:
                        if not isinstance(old, dict) or not _old(old.get("retired")):
                            kept.append(old)
                            continue
                        path = _file(old, root_dir)
                        if path is not None and old["file"] not in in_use:
                            with contextlib.suppress(OSError):
                                _remove(path, root_dir)
                    if len(kept) != len(data["retired"]):
                        data["retired"] = kept
                        _write(root_dir, data)
        for folder in (root_dir, os.path.join(root_dir, ".partial")):
            if not os.path.isdir(folder) or os.path.islink(folder):
                continue
            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                relative = os.path.relpath(path, root_dir)
                if not any(x.match(relative) for x in LEFTOVER_NAMES):
                    continue
                with contextlib.suppress(OSError):
                    if _old(os.lstat(path).st_mtime):
                        _remove(path, root_dir)
    except Exception as error:
        _say(
            "OCEANVAL COULD NOT TIDY YOUR DATA STORE",
            [f"{error}", "", "Your run is not affected."],
            f"could not tidy the data store in {root_dir}: {error}",
        )


def _is_fresh(entry):
    return entry.get("id") in _fresh


def _when(entries):
    """When pieces were downloaded, to say so."""
    days = sorted({str(x.get("downloaded", ""))[:10] for x in entries if x.get("downloaded")})
    if not days:
        return "at a time not known"
    if len(days) == 1:
        return f"on {days[0]}"
    return f"between {days[0]} and {days[-1]}"


def _fallen_back(label, error, entries):
    when = _when(entries)
    with contextlib.suppress(Exception):
        loud_warning(
            f"COULD NOT DOWNLOAD {label.upper()}",
            [f"{type(error).__name__}: {error}", "",
             f"The copy in your data store, downloaded {when}, is used instead."],
            warning=f"Could not download {label}, so the copy in the data store was used: {error}",
        )
    session_info.setdefault("end_messages", []).append(
        f"{label} could not be downloaded, so the copy in your data store (downloaded {when}) was used."
    )


# --------------------------------------------------------------------------
# gridded data


def _fetch_gridded(url, thredds, variable, box, levels, years=None, downloaded=None):
    """Download one of a server's files: over thredds, the variable in box (the
    top level only, or some years, if asked); a plain file whole, or cut to
    years and box if years are asked for. downloaded is the DataSet of the
    plain file, if it has been downloaded already. A DataSet of the result, in
    a temporary file."""
    if thredds:
        ds = nc.open_thredds(url, checks=False)
    else:
        downloaded = downloaded if downloaded is not None else nc.open_url(url)
        ds = nc.open_data(downloaded[0], checks=False) if years else downloaded
    if thredds or years:
        ds.subset(variables=variable)
        if levels == "top":
            ds.top()
        if years:
            ds.cdo_command(f"-selyear,{','.join(str(x) for x in years)}")
        ds.crop(lon=box[:2], lat=box[2:])
        ds.run()
    if variable not in ds.variables:
        raise ValueError(f"{url} did not give {variable}")
    return ds


def _unit(dataset, url, variable, year=None):
    """Whether an entry is a piece of this dataset's file and variable (and year)."""
    def matches(entry):
        return (
            entry.get("kind") == "gridded" and entry.get("dataset") == dataset
            and entry.get("url") == url and entry.get("variable") == variable
            and entry.get("year") == year
        )
    return matches


def _covering(entries, matches, box, levels=None):
    """The latest piece matches is true of that has box in it (and levels)."""
    found = [
        x for x in entries
        if matches(x) and contains(x["box"], box)
        and (levels is None or _levels_cover(x.get("levels"), levels))
    ]
    found.sort(key=lambda x: str(x.get("downloaded", "")), reverse=True)
    return found[0] if found else None


def gridded(comparison, files, thredds, model_box, levels, years=None, label=None):
    """A built-in gridded dataset's files, by way of the data store, as a
    DataSet of local files, or None when the store is not used for them (there
    is none, or they are not OceanVal's own) and matchup opens them itself.

    files is the url, or list of them, matchup would open; model_box is the
    model's (lon_min, lon_max, lat_min, lat_max); levels is "top" if only the
    top level is used, else "all"; years is the years wanted, for a dataset
    kept a year at a time (COBE2). Raises what a failed download raises,
    unless the store has the data and is to be used when downloads fail.
    """
    store = active()
    if store is None or not keeps(comparison):
        return None
    box = snap(model_box[:2], model_box[2:])
    if box is None:
        return None
    del _held[:]
    dataset = comparison["builtin"]
    variable = comparison["obs_variable"]
    urls = [files] if isinstance(files, str) else list(files)
    label = label or f"{dataset.upper()} {variable}"
    if dataset in BY_YEAR and len(urls) == 1 and years:
        # kept a year at a time, cut to the box, whether it is downloaded or not
        return _gridded_by_year(store, dataset, urls[0], variable, box, years, label, thredds)
    if not thredds:
        # a plain file is downloaded whole, so it is kept whole
        box = list(GLOBAL)
        levels = "all"

    root_dir = store["root"]
    entries = _entries(root_dir)
    pieces, used, fallen = [], [], []
    saved = 0
    for url in urls:
        matches = _unit(dataset, url, variable)
        stored = _covering(entries, matches, box, levels)
        if stored is not None and (store["use"] == "always" or _is_fresh(stored)):
            pieces.append((_file(stored, root_dir), stored["box"]))
            used.append(stored)
            continue
        # what it replaces is widened to, so the piece covers both areas
        replaced = [x for x in entries if matches(x) and _levels_cover(levels, x.get("levels"))]
        fetch_box = _widen(union(box, *[x["box"] for x in replaced])) if thredds else list(GLOBAL)
        try:
            ds = _fetch_gridded(url, thredds, variable, fetch_box, levels)
        except Exception as error:
            if stored is None:
                raise
            fallen.append((error, stored))
            pieces.append((_file(stored, root_dir), stored["box"]))
            continue
        entry = dict(kind="gridded", dataset=dataset, url=url, variable=variable, year=None,
                     levels=levels, box=fetch_box)
        kept = _save(
            root_dir, entry, lambda path, ds=ds: shutil.copyfile(ds[0], path), ".nc",
            lambda old, matches=matches, fetch_box=fetch_box: (
                matches(old) and contains(fetch_box, old["box"])
                and _levels_cover(levels, old.get("levels"))
            ),
        )
        if kept is None:
            _held.append(ds)
            pieces.append((ds[0], fetch_box))
        else:
            saved += 1
            pieces.append((_file(kept, root_dir), fetch_box))

    if fallen:
        _fallen_back(label, fallen[0][0], [x for _, x in fallen])
    _tell(label, len(urls), used, saved, len(urls) - len(used) - len(fallen))
    return _open(pieces, box)


def _open(pieces, box):
    """A DataSet of pieces, as (path, box). Pieces cut to different boxes are
    first cut to box, so that they are on one grid, as the server's files are."""
    ds = nc.open_data([x for x, _ in pieces], checks=False)
    if len({tuple(x) for _, x in pieces}) > 1:
        ds.crop(lon=box[:2], lat=box[2:])
        ds.run()
    return ds


def _tell(label, n, used, saved, downloaded):
    if used and not downloaded:
        print(f"{label}: taken from your data store (downloaded {_when(used)})")
    elif used:
        print(f"{label}: {len(used)} of {n} taken from your data store "
              f"(downloaded {_when(used)}), and {downloaded} downloaded")
    elif downloaded and saved == downloaded:
        print(f"{label}: downloaded, and saved in your data store")


def _server_limits(url):
    """Whether the thredds server at url is turning requests away: it answered
    with 429 (too many requests) or a server error. Nothing else is decided
    here (a timeout, no network), as that is for the download to say."""
    request = urllib.request.Request(url + ".dds", headers={"User-Agent": "oceanval"})
    try:
        with urllib.request.urlopen(request, timeout=15):
            return False
    except urllib.error.HTTPError as error:
        return error.code == 429 or error.code >= 500
    except Exception:
        return False


def _download_of(dataset, url):
    """Where the file of url, a built-in dataset's thredds address, can be
    downloaded, or None."""
    found = DOWNLOADS.get(dataset)
    return found["file"] if found is not None and url == found["thredds"] else None


def download_instead(dataset, url):
    """Where to download a built-in dataset's file in place of reading url over
    thredds, if its server is turning requests away, else None. CDO and netCDF
    retry every request that is refused, so thousands of them would be waited
    for if the server were asked."""
    download = _download_of(dataset, url)
    return download if download is not None and _server_limits(url) else None


def told_to_download(label, why):
    message = f"{label}: {why}, so the file was downloaded instead"
    print(message)
    session_info.setdefault("end_messages", []).append(message)


def _gridded_by_year(store, dataset, url, variable, box, years, label, thredds=True):
    """COBE2's years, by way of the store. Its file has every year in it, so it
    is kept a year at a time, cut to box. A year without all 12 months (the
    one still being added to) is used, but not kept. thredds is whether the
    file is read over a thredds server, rather than downloaded. If its server
    will not answer, it is downloaded instead (see DOWNLOADS)."""
    root_dir = store["root"]
    entries = _entries(root_dir)
    years = sorted({int(x) for x in years})
    stored = {
        year: _covering(entries, _unit(dataset, url, variable, year), box, "all")
        for year in years
    }
    if store["use"] == "always":
        wanted = [x for x in years if stored[x] is None]
    else:
        wanted = [x for x in years if stored[x] is None or not _is_fresh(stored[x])]

    pieces = []
    fetched = []
    saved = 0
    if wanted:
        try:
            # whole is the file, if downloaded (once, for the years and the cut)
            whole = None
            download = download_instead(dataset, url) if thredds else None
            if download is not None:
                told_to_download(label, "the thredds server is turning requests away (HTTP 429)")
                thredds, whole = False, nc.open_url(download)
                available = whole.years
            elif thredds:
                try:
                    available = nc.open_thredds(url, checks=False).years
                    if len(available) == 0:
                        raise ValueError(f"the years in {url} could not be read")
                except Exception as error:
                    download = _download_of(dataset, url)
                    if download is None:
                        raise
                    told_to_download(label, f"the thredds server could not be read ({type(error).__name__})")
                    thredds, whole = False, nc.open_url(download)
                    available = whole.years
            else:
                whole = nc.open_url(url)
                available = whole.years
            if len(available) == 0:
                raise ValueError(f"the years in {url} could not be read")
            wanted = [x for x in wanted if x in available]
            if wanted:
                replaced = [
                    x for x in entries
                    if any(_unit(dataset, url, variable, year)(x) for year in wanted)
                ]
                fetch_box = _widen(union(box, *[x["box"] for x in replaced]))
                try:
                    ds = _fetch_gridded(url, thredds, variable, fetch_box, "all", wanted, whole)
                except Exception as error:
                    download = _download_of(dataset, url)
                    if not thredds or download is None:
                        raise
                    # the server answered, then stopped
                    told_to_download(label, f"the thredds server failed ({type(error).__name__})")
                    ds = _fetch_gridded(url, False, variable, fetch_box, "all", wanted,
                                        nc.open_url(download))
                _held.append(ds)
                pieces.append((ds[0], fetch_box))
                fetched = wanted
                times = pd.to_datetime(pd.Series([str(x)[:10] for x in ds.times]), errors="coerce")
                months = times.dt.month.groupby(times.dt.year).nunique()
                for year in wanted:
                    if months.get(year, 0) < 12:
                        continue

                    def write(path, year=year):
                        one = nc.open_data(ds[0], checks=False)
                        one.cdo_command(f"-selyear,{year}")
                        one.to_nc(path, zip=True)

                    matches = _unit(dataset, url, variable, year)
                    entry = dict(kind="gridded", dataset=dataset, url=url, variable=variable,
                                 year=year, levels="all", box=fetch_box)
                    if _save(root_dir, entry, write, ".nc",
                             lambda old, matches=matches: matches(old) and contains(fetch_box, old["box"])):
                        saved += 1
        except Exception as error:
            if any(stored.get(x) is None for x in wanted):
                raise
            _fallen_back(label, error, [stored[x] for x in wanted])
            pieces = []
            fetched = []

    used = [stored[x] for x in years if stored[x] is not None and x not in fetched]
    pieces += [(_file(x, root_dir), x["box"]) for x in used]
    if not pieces:
        return None
    _tell(label, len(years), used, saved, len(fetched))
    ds = _open(pieces, box)
    if len(ds) > 1:
        ds.merge("time")
        ds.run()
    return ds


# --------------------------------------------------------------------------
# ICES data


def _runs(years):
    """Years in runs of consecutive ones, as (first, last)."""
    runs = []
    for year in sorted(years):
        if runs and year == runs[-1][1] + 1:
            runs[-1][1] = year
        else:
            runs.append([year, year])
    return [tuple(x) for x in runs]


def _ices_unit(dataset, year):
    """Whether an entry is a piece of this ICES dataset's year."""
    def matches(entry):
        return entry.get("kind") == "ices" and entry.get("dataset") == dataset and entry.get("year") == year
    return matches


def _read_rows(path):
    """A piece's rows, read as an ICES export is."""
    return pd.read_csv(path, low_memory=False)


def ices(datasets, start_year, end_year, lon_lim, lat_lim, download):
    """ICES exports by way of the data store, or None when the store is not
    used (there is none, or the box is not one it can keep).

    Returns {dataset: [rows of a year, ...]} for the years start_year to
    end_year, read as ICES export files are: rows of a box with lon_lim x
    lat_lim in it, still to be cut to it.

    download is given [(dataset, first_year, last_year, box)] and returns, in
    order, the rows of each by year ({year: rows read as text}), or the
    exception that stopped it.
    """
    store = active()
    if store is None:
        return None
    box = snap(lon_lim, lat_lim)
    if box is None:
        return None
    want = [float(lon_lim[0]), float(lon_lim[1]), float(lat_lim[0]), float(lat_lim[1])]
    root_dir = store["root"]
    entries = _entries(root_dir)
    years = list(range(int(start_year), int(end_year) + 1))
    stored = {
        (dataset, year): _covering(entries, _ices_unit(dataset, year), want)
        for dataset in datasets for year in years
    }

    def wanted(dataset, year):
        piece = stored[(dataset, year)]
        return piece is None or (store["use"] == "fallback" and not _is_fresh(piece))

    requests = [
        (dataset, first, last, box)
        for dataset in datasets
        for first, last in _runs([x for x in years if wanted(dataset, x)])
    ]
    results = download(requests) if requests else []

    found = {dataset: {} for dataset in datasets}
    for (dataset, first, last, _), result in zip(requests, results):
        run = range(first, last + 1)
        if isinstance(result, BaseException):
            if any(stored[(dataset, x)] is None for x in run):
                raise result
            years_said = f"{first}" if first == last else f"{first}-{last}"
            _fallen_back(f"ICES {dataset} data for {years_said}", result, [stored[(dataset, x)] for x in run])
            continue
        for year in run:
            rows = result.get(year)
            if rows is not None and len(rows) == 0:
                rows = None
            entry = dict(kind="ices", dataset=dataset, year=year, box=box,
                         rows=0 if rows is None else int(len(rows)))
            kept = _save(
                root_dir, entry,
                None if rows is None else (lambda path, rows=rows: rows.to_csv(path, index=False, compression="gzip")),
                ".csv.gz",
                lambda old, matches=_ices_unit(dataset, year): matches(old) and contains(box, old["box"]),
            )
            if rows is None:
                found[dataset][year] = None
            elif kept is not None:
                found[dataset][year] = _read_rows(_file(kept, root_dir))
            else:
                found[dataset][year] = pd.read_csv(io.StringIO(rows.to_csv(index=False)), low_memory=False)

    for dataset in datasets:
        used = []
        for year in years:
            piece = stored[(dataset, year)]
            if year in found[dataset] or piece is None:
                continue
            used.append(piece)
            found[dataset][year] = None if piece.get("file") is None else _read_rows(_file(piece, root_dir))
        if used:
            print(f"ICES {dataset}: {len(used)} of {len(years)} years taken from your data store "
                  f"(downloaded {_when(used)})")
    return {
        dataset: [found[dataset][x] for x in years if found[dataset].get(x) is not None]
        for dataset in datasets
    }


# --------------------------------------------------------------------------
# for the Preferences panel

# what each way of using the store means, under its name
USE_NOTES = {
    "always": "Only what is not in it is downloaded, and kept in it.",
    "fallback": "Everything is downloaded each time, and kept in it. What is in it is used only for what cannot be downloaded.",
}


def panel(settings):
    """What the Preferences panel shows of the data store in settings: the
    ways it can be used and, if one is set, where it is kept, the free space
    on its disk and how much it holds (None where that cannot be told)."""
    info = {
        "uses": [[key, label, USE_NOTES.get(key)] for key, label in user_settings.DATA_STORE_USES.items()],
        "root": None,
        "free": None,
        "size": None,
    }
    folder = settings.get("data_store") or ""
    if not folder:
        return info
    info["root"] = root(folder)
    info["free"] = _free(folder)
    with contextlib.suppress(Exception):
        info["size"] = size(info["root"])
    return info


def _free(folder):
    """The free space on folder's disk, looked at no more than every few
    seconds, as the window asks for it with every change to the page."""
    now = time.monotonic()
    with _lock:
        if folder in _free_cache and now - _free_cache[folder][0] < 10:
            return _free_cache[folder][1]
    free = None
    with contextlib.suppress(Exception):
        free = shutil.disk_usage(folder).free
    with _lock:
        if len(_free_cache) > 16:
            _free_cache.clear()
        _free_cache[folder] = (now, free)
    return free


def size(root_dir):
    """How many bytes the pieces in the store take."""
    where = _index(root_dir)
    try:
        stat = os.stat(where)
    except OSError:
        return 0
    signature = (where, stat.st_mtime_ns, stat.st_size)
    with _lock:
        if signature in _size_cache:
            return _size_cache[signature]
    data, problem = _load(root_dir)
    total = 0
    if not problem:
        total = sum(
            int(x.get("bytes") or 0) for x in data["entries"] + data["retired"]
            if isinstance(x, dict) and isinstance(x.get("bytes"), (int, float))
        )
    with _lock:
        if len(_size_cache) > 16:
            _size_cache.clear()
        _size_cache[signature] = total
    return total


# --------------------------------------------------------------------------
# checking the store

# a file younger than this may be one a run is writing as it is looked at
CHECK_YOUNG = 10 * 60
# how many of what is found each group of the check lists
CHECK_LISTED = 20
# what a check can find, in the order it says it, with whether it is a problem
CHECK_GROUPS = (
    ("index", "Problems with the index", "problem"),
    ("missing", "Files that are missing, or not the size they were", "problem"),
    ("named", "Entries naming a file OceanVal does not use", "problem"),
    ("duplicate", "Data held twice", "problem"),
    ("shared", "Files or ids used by more than one entry", "problem"),
    ("not_ours", "In the data store, and not OceanVal's", "problem"),
    ("not_builtin", "Entries that are not built-in data", "problem"),
    ("overlap", "Pieces that overlap", "note"),
    ("orphan", "Piece files that are not in the index", "note"),
    ("leftover", "Left behind, to be tidied away", "note"),
    ("marker", "The marker file", "note"),
)


def _describe(entry):
    """What an entry is, for saying so."""
    if not isinstance(entry, dict):
        return f"An entry ({entry!r})"
    if entry.get("kind") == "ices":
        return f"ICES {entry.get('dataset')} for {entry.get('year')}"
    last = str(entry.get("url")).rsplit("/", 1)[-1]
    year = f" {entry['year']}" if entry.get("year") is not None else ""
    return f"{str(entry.get('dataset')).upper()} {entry.get('variable')}{year} ({last})"


def _bytes_text(n):
    for power, unit in enumerate(("bytes", "KB", "MB", "GB")):
        if n < 1024 ** (power + 1) or unit == "GB":
            return f"{n} bytes" if power == 0 else f"{n / 1024 ** power:.1f} {unit}"


def _overlap(a, b):
    """Whether two boxes have any area in common."""
    return min(a[1], b[1]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[2], b[2])


def _walk(root_dir):
    """What is in the store's folder, without following a link or changing
    anything: (relative path, lstat) of the files, and the paths of what
    OceanVal would not put there (a link, a folder it does not make)."""
    files, strays = [], []
    piece_folders = GRIDDED + ("ices",)

    def look(folder, relative):
        try:
            names = sorted(os.listdir(folder))
        except OSError as error:
            strays.append(f"{relative or './'} (cannot be read: {error.strerror or error})")
            return
        for name in names:
            path = os.path.join(folder, name)
            where = f"{relative}{name}"
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode):
                strays.append(f"{where} (a link)")
            elif stat.S_ISDIR(info.st_mode):
                if not relative and (name in piece_folders or name == ".partial"):
                    look(path, f"{where}/")
                else:
                    strays.append(f"{where}/ (a folder)")
            else:
                files.append((where, info))

    look(root_dir, "")
    return files, strays


def check(root_dir):
    """Look at the data store in root_dir for what is wrong with it, and say
    what is found. It only looks: nothing is written, removed or made, and
    nothing is locked, so it can be run while a matchup uses the store.

    Returns {"checked", "pieces", "bytes", "problems", "notes", "groups"}: the
    groups (see CHECK_GROUPS) that have something in them, each as {"key",
    "title", "level", "count", "items"} with the first CHECK_LISTED items said.
    Raises ValueError if there is no store folder.
    """
    if not os.path.isdir(root_dir) or os.path.islink(root_dir):
        raise ValueError(f"{root_dir} is not there, so there is nothing to check.")
    found = {key: [] for key, _, _ in CHECK_GROUPS}

    # the folder is looked at before the index is read, so that a piece a run
    # saves in between is in the index by the time it is looked for there
    files, strays = _walk(root_dir)
    data, problem = _load(root_dir)
    on_disk = {where: info for where, info in files}
    has_index = INDEX in on_disk

    found["not_ours"] += strays
    pieces_on_disk = []
    for where, info in files:
        if where in (INDEX, MARKER, ".lock"):
            continue
        young = time.time() - info.st_mtime < CHECK_YOUNG
        if PIECE_NAME.match(where):
            pieces_on_disk.append(where)
        elif any(x.match(where) for x in LEFTOVER_NAMES):
            if not young:
                found["leftover"].append(f"{where} ({_bytes_text(info.st_size)}), left by a download that did not finish")
        else:
            found["not_ours"].append(f"{where} ({_bytes_text(info.st_size)})")
    if MARKER not in on_disk:
        found["marker"].append(
            f"{MARKER} is not there. It says the folder is OceanVal's, and is put back the next "
            "time a matchup uses the data store."
        )

    entries, retired = [], []
    if problem:
        found["index"].append(f"{INDEX} could not be read, so what it holds was not checked: {problem}")
    else:
        entries, retired = data["entries"], data["retired"]
        if not has_index and pieces_on_disk:
            found["index"].append(f"There are {len(pieces_on_disk)} piece files, and no {INDEX} to say what they are.")

    live = []
    for number, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            found["index"].append(f"Entry {number} of the index is not an entry ({entry!r}).")
        elif _box(entry) is None:
            found["index"].append(f"{_describe(entry)} has no area that can be used.")
        else:
            live.append(entry)
    records = [x for x in entries if isinstance(x, dict)]

    total = 0
    for entry in records:
        name = entry.get("file")
        who = _describe(entry)
        if name is None:
            if entry.get("rows") != 0:
                found["missing"].append(f"{who} has no file, and is not one that was found to have no data.")
            continue
        folder = "ices" if entry.get("kind") == "ices" else entry.get("dataset")
        if not isinstance(name, str) or not PIECE_NAME.match(name) or name.split("/")[0] != folder:
            found["named"].append(
                f"{who} names {name!r}, which is not a name OceanVal gives a piece. It is never used or removed."
            )
            continue
        try:
            info = os.lstat(os.path.join(root_dir, name))
        except OSError:
            found["missing"].append(f"{who}: {name} is not in the data store.")
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            found["named"].append(f"{who}: {name} is a link, or not a file. It is never used or removed.")
            continue
        total += info.st_size
        wanted = entry.get("bytes")
        if isinstance(wanted, int) and not isinstance(wanted, bool) and wanted != info.st_size:
            found["missing"].append(
                f"{who}: {name} is {info.st_size} bytes, and was {wanted} when it was saved. "
                "It may be damaged or have been changed."
            )

    # data held more than once
    for label, values in (("id", [x.get("id") for x in records]), ("file", [x.get("file") for x in records])):
        for value in sorted({str(v) for v in values if v is not None}):
            users = [x for x in records if str(x.get("id" if label == "id" else "file")) == value]
            if len(users) > 1:
                found["shared"].append(
                    f"The {label} {value} is used by {len(users)} entries: " + "; ".join(_describe(x) for x in users) + "."
                )
    live_files = {x.get("file") for x in records if x.get("file")}
    for old in retired:
        if isinstance(old, dict) and old.get("file") in live_files:
            found["shared"].append(
                f"{old.get('file')} is in the data store as a piece, and as a replaced piece "
                "that is waiting to be removed."
            )

    units = {}
    for entry in live:
        key = (entry.get("kind"), entry.get("dataset"), entry.get("url"), entry.get("variable"), entry.get("year"))
        units.setdefault(key, []).append(entry)
    for same in units.values():
        for i, a in enumerate(same):
            for b in same[i + 1:]:
                box_a, box_b = _box(a), _box(b)
                if contains(box_a, box_b) and _levels_cover(a.get("levels"), b.get("levels")):
                    inner, outer = b, a
                elif contains(box_b, box_a) and _levels_cover(b.get("levels"), a.get("levels")):
                    inner, outer = a, b
                else:
                    inner = None
                if inner is not None:
                    found["duplicate"].append(
                        f"{_describe(inner)} for the area {_box(inner)} is held twice: it is inside "
                        f"the piece for {_box(outer)} ({inner.get('file')} and {outer.get('file')})."
                    )
                elif _overlap(box_a, box_b):
                    found["overlap"].append(
                        f"{_describe(a)} is held for the areas {box_a} and {box_b}, which overlap "
                        f"({a.get('file')} and {b.get('file')})."
                    )

    # data that should not be there
    for entry in records:
        kind, dataset = entry.get("kind"), entry.get("dataset")
        if kind == "ices":
            continue
        why = None
        if kind != "gridded" or dataset not in GRIDDED:
            why = f"it is for {dataset!r} ({kind!r}), which is not one of OceanVal's built-in datasets"
        else:
            url = entry.get("url")
            parsed = urllib.parse.urlparse(url) if isinstance(url, str) else None
            if parsed is None or parsed.scheme not in ("http", "https") or parsed.hostname not in BUILTIN_HOSTS:
                why = f"its address, {url!r}, is not one of the built-in sources"
        if why:
            found["not_builtin"].append(f"{_describe(entry)}: {why}.")

    named = live_files | {x.get("file") for x in retired if isinstance(x, dict)}
    if not problem:
        for where in pieces_on_disk:
            if where not in named and time.time() - on_disk[where].st_mtime >= CHECK_YOUNG:
                found["orphan"].append(f"{where} ({_bytes_text(on_disk[where].st_size)}) is not named in the index.")
    for old in retired:
        if isinstance(old, dict) and old.get("file") not in live_files and old.get("file") in on_disk:
            found["leftover"].append(f"{old['file']} was replaced, and is removed a day after that.")

    groups = []
    for key, title, level in CHECK_GROUPS:
        items = found[key]
        if not items:
            continue
        shown = items[:CHECK_LISTED]
        if len(items) > CHECK_LISTED:
            shown.append(f"and {len(items) - CHECK_LISTED} more")
        groups.append({"key": key, "title": title, "level": level, "count": len(items), "items": shown})
    return {
        "checked": datetime.datetime.now().isoformat(timespec="seconds"),
        "pieces": len([x for x in records if x.get("file")]),
        "bytes": total,
        "problems": sum(x["count"] for x in groups if x["level"] == "problem"),
        "notes": sum(x["count"] for x in groups if x["level"] == "note"),
        "groups": groups,
    }


def check_reply(settings):
    """The Preferences panel's check on the data store the settings have set,
    as (HTTP status, reply). The folder is the one saved, never one the page
    names."""
    folder = (settings or {}).get("data_store") or ""
    if not folder:
        return 400, {"ok": False, "error": "There is no data store to check: set a folder first."}
    try:
        return 200, {"ok": True, "check": check(root(folder))}
    except Exception as error:
        return 400, {"ok": False, "error": f"The data store could not be checked: {error}"}
