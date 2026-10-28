"""The times of each output file the viewer shows, read from its time axis alone.

As output_years reads them: h5py reads only the time variable of a netCDF-4
file (netCDF4 reads every variable's metadata as it opens one), and netCDF4
reads netCDF-3 files. The times are decoded with cftime, so any calendar
works, and kept as ISO strings, which sort as the times do.

A simulation can hold thousands of files, so the viewer reads their times in
the background, in a process of its own (see Reading) that runs this file as a
script and can be stopped when the files are no longer wanted. It imports
nothing of OceanVal, as importing the package takes seconds, and so its pool of
workers can be started with spawn. It reads a request as JSON on stdin and
answers with a RESULT_MARKER line on stdout for each file as it is read.

What was read is kept in a cache of its own, ~/.oceanvaltimes (or the file
$OCEANVALTIMES names), by each file's path, size and modification time, so a
simulation looked at again reads only its new or changed files. It is not
.oceanvalcache, which would grow by the times of every file ever looked at.
"""

import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading

import cftime
import numpy as np

try:
    import h5py
except ImportError:  # pragma: no cover
    h5py = None

RESULT_MARKER = "@@viewer_times@@"
CACHE_VERSION = 1
# files read at once are shared out to worker processes from this many
PARALLEL_FROM = 40
# netCDF's default fill value for doubles, and larger: never a time
_NC_FILL = 9.0e36

# the attributes of a variable that say how to read its times
_ATTRIBUTES = (
    "units", "calendar", "axis", "standard_name", "CLASS",
    "_FillValue", "missing_value", "scale_factor", "add_offset",
)


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, np.ndarray) and value.size == 1:
        return _text(value.item())
    return str(value)


def natural_key(path):
    """path in the order of the numbers in it, so 10 follows 9."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", path)]


def default_cores():
    """The processes the times are read by in the background: half the cores
    this process may use (and at least 1), so the window keeps the rest."""
    try:
        cores = len(os.sched_getaffinity(0))
    except AttributeError:
        # macOS and Windows
        cores = os.cpu_count() or 1
    return max(1, cores // 2)


def _score(name, attrs, dimensions):
    """How likely a 1-D variable with units "... since ..." is the file's time
    axis: a coordinate variable first, then axis T, then standard_name time."""
    return (
        2 * (name in dimensions)
        + (_text(attrs.get("axis", "")) == "T")
        + (_text(attrs.get("standard_name", "")) == "time")
    )


def _decode(name, values, attrs):
    """{"name", "calendar", "times", "index"}: the times in values, as ISO
    strings, decoded with attrs' units and calendar, and the index in the file
    of each. Missing values are left out."""
    values = np.asarray(values, dtype="float64").ravel()
    keep = np.isfinite(values) & (np.abs(values) < _NC_FILL)
    for key in ("_FillValue", "missing_value"):
        if key in attrs:
            keep &= values != float(np.asarray(attrs[key]).ravel()[0])
    if "scale_factor" in attrs:
        values = values * float(np.asarray(attrs["scale_factor"]).ravel()[0])
    if "add_offset" in attrs:
        values = values + float(np.asarray(attrs["add_offset"]).ravel()[0])
    calendar = _text(attrs.get("calendar", "standard")) or "standard"
    index = np.flatnonzero(keep)
    dates = cftime.num2date(values[keep], _text(attrs["units"]), calendar) if index.size else []
    return {
        "name": name,
        "calendar": calendar,
        "times": [date.isoformat(sep=" ") for date in dates],
        "index": [int(i) for i in index],
    }


def _read_h5py(path):
    with h5py.File(path, "r") as handle:
        best = None
        for name, item in handle.items():
            if not isinstance(item, h5py.Dataset) or item.ndim != 1:
                continue
            # only these: a coordinate's REFERENCE_LIST, say, need not be read
            attrs = {key: item.attrs[key] for key in _ATTRIBUTES if key in item.attrs}
            if " since " not in _text(attrs.get("units", "")):
                continue
            # a netCDF coordinate variable is a dimension scale
            dimensions = {name} if _text(attrs.get("CLASS", "")) == "DIMENSION_SCALE" else set()
            score = _score(name, attrs, dimensions)
            if best is None or score > best[0]:
                best = (score, name, item, attrs)
        if best is None:
            return {"name": None, "calendar": None, "times": [], "index": []}
        return _decode(best[1], best[2][()], best[3])


def _read_netcdf4(path):
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        best = None
        for name, variable in handle.variables.items():
            if variable.ndim != 1 or " since " not in str(getattr(variable, "units", "")):
                continue
            score = _score(name, variable.__dict__, variable.dimensions)
            if best is None or score > best[0]:
                best = (score, name, variable)
        if best is None:
            return {"name": None, "calendar": None, "times": [], "index": []}
        variable = best[2]
        variable.set_auto_maskandscale(False)
        return _decode(best[1], variable[:], variable.__dict__)


def file_times(path):
    """The times of a file's time axis (see _decode), with an "error" if it
    could not be read. Never raises. In the window's own process the caller
    holds oceanval.netcdf_lock.LOCK."""
    try:
        if h5py is not None:
            try:
                return _read_h5py(path)
            except OSError:
                # not HDF5
                pass
        return _read_netcdf4(path)
    except Exception as error:  # noqa: BLE001 - a file that cannot be read is reported
        return {"name": None, "calendar": None, "times": [], "index": [], "error": str(error)}


# ---- the cache ----

def _stat(path):
    try:
        info = os.stat(path)
    except OSError:
        return None
    return [info.st_size, info.st_mtime_ns]


def cache_path():
    """Where what was read is kept: $OCEANVALTIMES, else ~/.oceanvaltimes."""
    return os.environ.get("OCEANVALTIMES") or os.path.join(os.path.expanduser("~"), ".oceanvaltimes")


def _load():
    with open(cache_path(), encoding="utf-8") as handle:
        kept = json.load(handle)
    if not isinstance(kept, dict) or kept.get("version") != CACHE_VERSION or not isinstance(kept.get("files"), dict):
        return None
    return kept


def load_cache(paths):
    """{path: times} kept for those of paths whose size and modification time
    are unchanged since. Never raises."""
    try:
        files = _load()["files"]
    except Exception:  # noqa: BLE001 - a cache must not stop anything
        return {}
    found = {}
    for path in paths:
        entry = files.get(path)
        if isinstance(entry, dict) and entry.get("stat") == _stat(path):
            found[path] = entry["times"]
    return found


def save_cache(read):
    """Add read ({path: times}) to the cache, keeping what else is in it.
    Written whole to a file beside it first, so a reader never sees half of
    it. A cache of a newer version, or one that cannot be read, is left as it
    is. Never raises."""
    read = {path: times for path, times in read.items() if not times.get("error")}
    if not read:
        return
    path = cache_path()
    try:
        kept = _load()
        if kept is None:
            # another version's: left alone
            return
    except FileNotFoundError:
        kept = {"version": CACHE_VERSION, "files": {}}
    except Exception:  # noqa: BLE001 - unreadable: left alone
        return
    for name, times in read.items():
        stat = _stat(name)
        if stat is not None:
            kept["files"][name] = {"stat": stat, "times": times}
    with contextlib.suppress(Exception):
        folder = os.path.dirname(path) or "."
        handle, partial = tempfile.mkstemp(dir=folder, prefix=".oceanvaltimes.")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(kept, out)
            os.replace(partial, path)
        finally:
            with contextlib.suppress(OSError):
                os.remove(partial)


# ---- reading in a process of its own ----

def _read_all(paths, workers):
    """(path, times) of each of paths as it is read: in a pool of workers
    processes when there are many and this file is run as a script (a worker
    of this module imported as oceanval.viewer_times would import all of
    OceanVal)."""
    if __name__ == "__main__" and workers > 1 and len(paths) >= PARALLEL_FROM:
        import concurrent.futures
        import multiprocessing

        try:
            pool = concurrent.futures.ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn"))
        except Exception:  # noqa: BLE001 - read here instead
            pool = None
        if pool is not None:
            with pool:
                futures = {pool.submit(file_times, path): path for path in paths}
                for future in concurrent.futures.as_completed(futures):
                    yield futures[future], future.result()
            return
    for path in paths:
        yield path, file_times(path)


class Reading:
    """Read the times of files in a process of their own, which cancel stops
    (as output_years.Reading). run(paths, found) calls found(path, times) for
    each file as it is read, in the thread that runs it, and returns whether
    every file was read."""

    def __init__(self, workers=None):
        self.workers = workers or default_cores()
        self.process = None
        self.cancelled = False
        self._lock = threading.Lock()

    def run(self, paths, found):
        with self._lock:
            if self.cancelled:
                return False
            self.process = subprocess.Popen(
                [sys.executable, "-I", os.path.abspath(__file__)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
        process = self.process
        left = set(paths)
        try:
            with contextlib.suppress(BrokenPipeError, OSError):
                process.stdin.write(json.dumps({"paths": list(paths), "workers": self.workers}))
                process.stdin.close()
            for line in process.stdout:
                if self.cancelled:
                    break
                if not line.startswith(RESULT_MARKER):
                    continue
                answer = json.loads(line[len(RESULT_MARKER):])
                left.discard(answer["path"])
                found(answer["path"], answer["times"])
        finally:
            if left:
                # stopped part way, or by an error here: the process goes too
                self.cancel()
            with contextlib.suppress(Exception):
                process.stdout.close()
            process.wait()
        return not left and not self.cancelled

    def cancel(self):
        with self._lock:
            self.cancelled = True
            process = self.process
        if process is not None and process.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)


def main():
    try:
        request = json.load(sys.stdin)
        paths = [str(path) for path in request["paths"]]
        workers = int(request.get("workers") or 1)
    except Exception:  # noqa: BLE001 - nothing to read
        return
    for path, times in _read_all(paths, workers):
        sys.stdout.write(RESULT_MARKER + json.dumps({"path": path, "times": times}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
