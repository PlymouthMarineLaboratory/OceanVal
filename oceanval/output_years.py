"""The years a simulation's output covers, read from the time axes of its files.

Nothing is taken from the names of the files or directories: names only group
the files into streams (see create_recipes._file_pattern), and every year comes
from a file's time axis, decoded with its units and calendar. A simulation can
hold thousands of files, each of many GB, and reading the time of each would
take minutes on a shared disk, so only the files most likely to hold a stream's
first and last times are read: its first and last in path order (numbers in
order, so 10 follows 9) and its oldest and newest by modification time. Some
other files are then read at random as a check, and a stream with one outside
the years found has every file read.

Only the simulation's main output counts: the streams with at least a tenth as
many files as the largest, and never fewer than two, unless every stream is a
single file. Files beside it, such as a post-processed series whose times run
past the simulation's or the grids an earlier validation saved, are left out
unread. A counted stream whose years do not overlap the largest stream's is
left out too.

The window runs this file as a script in a process of its own (see Reading),
which can be stopped when the simulation changes and needs no netCDF lock: it
imports nothing of OceanVal, as importing the package takes seconds, and reads
a request as JSON on stdin and answers with a RESULT_MARKER line on stdout.
"""

import contextlib
import json
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time

import cftime
import numpy as np

try:
    import h5py
except ImportError:
    h5py = None

# how many files are read at random to check the streams' years
SPOT_CHECKS = 30
# the share of the largest stream's files a stream needs to count as output
MAIN_SHARE = 0.1
# changed whenever what is found for the same files changes, so that years
# kept in .oceanvalcache by an earlier rule are not used (see signature)
RULE = 2
# more streams than this and names are not grouping the files (they hold
# no numbers, say), so all the files are taken as one stream
MAX_STREAMS = 200
# files read at once are shared out to worker processes from this many (see _Reader)
PARALLEL_FROM = 20
RESULT_MARKER = "@@output_years@@"
# netCDF's default fill value for doubles: never a time
_NC_FILL = 9.0e36


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, np.ndarray) and value.size == 1:
        return _text(value.item())
    return str(value)


def natural_key(path):
    """path in the order of the numbers in it, so 10 follows 9."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", path)]


def _score(name, attrs, dimensions):
    """How likely a 1-D variable with units "... since ..." is the file's time
    axis: a coordinate variable first, then axis T, then standard_name time."""
    return (
        2 * (name in dimensions)
        + (_text(attrs.get("axis", "")) == "T")
        + (_text(attrs.get("standard_name", "")) == "time")
    )


def _years(values, attrs):
    """The first and last year of the times in values, decoded with attrs'
    units and calendar, or None if there is no time in them."""
    values = np.asarray(values, dtype="float64").ravel()
    for name in ("_FillValue", "missing_value"):
        if name in attrs:
            values = values[values != float(np.asarray(attrs[name]).ravel()[0])]
    values = values[np.isfinite(values) & (np.abs(values) < _NC_FILL)]
    if "scale_factor" in attrs:
        values = values * float(np.asarray(attrs["scale_factor"]).ravel()[0])
    if "add_offset" in attrs:
        values = values + float(np.asarray(attrs["add_offset"]).ravel()[0])
    if values.size == 0:
        return None
    first, last = cftime.num2date(
        [values.min(), values.max()],
        _text(attrs["units"]),
        _text(attrs.get("calendar", "standard")) or "standard",
    )
    return [int(first.year), int(last.year)]


# the attributes of a variable that say how to read its times
_ATTRIBUTES = (
    "units", "calendar", "axis", "standard_name", "CLASS",
    "_FillValue", "missing_value", "scale_factor", "add_offset",
)


def _years_h5py(path):
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
                best = (score, item, attrs)
        if best is None:
            return None
        return _years(best[1][()], best[2])


def _years_netcdf4(path):
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        best = None
        for name, variable in handle.variables.items():
            if variable.ndim != 1 or " since " not in str(getattr(variable, "units", "")):
                continue
            score = _score(name, variable.__dict__, variable.dimensions)
            if best is None or score > best[0]:
                best = (score, variable)
        if best is None:
            return None
        variable = best[1]
        variable.set_auto_maskandscale(False)
        return _years(variable[:], variable.__dict__)


def file_years(path):
    """The first and last year of a file's time axis, as [first, last], or
    None if it has none or cannot be read. Only the time variable is read:
    h5py reads nothing else of a netCDF-4 file, where netCDF4 reads every
    variable's metadata as it opens one; netCDF4 reads the rest (netCDF-3)."""
    try:
        if h5py is not None:
            try:
                return _years_h5py(path)
            except OSError:
                # not HDF5
                pass
        return _years_netcdf4(path)
    except Exception:
        return None


class _Reader:
    """Reads files with read, in a pool of worker processes once there are
    PARALLEL_FROM to read at once: the files are on shared disks, where a few
    reads at a time take half as long as one at a time (40 files of a
    simulation, 2.1 s alone, 1.1 s in 4 workers). The pool is started once,
    when it is first needed, as starting it takes a few tenths of a second.
    Only this file run as a script (see Reading) has one: a worker of this
    module imported as oceanval.output_years would import all of OceanVal."""

    def __init__(self, read, workers):
        self.read = read
        self.workers = workers
        self.pool = None
        self.found = {}
        # whether any files were read in the pool
        self.shared = False

    def __call__(self, paths):
        todo = [path for path in dict.fromkeys(paths) if path not in self.found]
        if (
            self.read is file_years
            and __name__ == "__main__"
            and self.workers > 1
            and len(todo) >= PARALLEL_FROM
        ):
            try:
                if self.pool is None:
                    import concurrent.futures
                    import multiprocessing

                    self.pool = concurrent.futures.ProcessPoolExecutor(
                        max_workers=self.workers, mp_context=multiprocessing.get_context("spawn")
                    )
                chunk = max(1, len(todo) // (4 * self.workers))
                self.found.update(zip(todo, self.pool.map(file_years, todo, chunksize=chunk)))
                self.shared = True
                return
            except Exception:
                # read here instead
                self.close()
                self.workers = 1
        for path in todo:
            self.found[path] = self.read(path)

    def close(self):
        if self.pool is not None:
            with contextlib.suppress(Exception):
                self.pool.shutdown(cancel_futures=True)
            self.pool = None


def _widen(found, years):
    if years is None:
        return found
    if found is None:
        return list(years)
    return [min(found[0], years[0]), max(found[1], years[1])]


def years_of_streams(streams, mtimes=None, checks=SPOT_CHECKS, rng=None, workers=1, read=file_years):
    """The years the output in streams ({pattern: [paths]}) covers, read from
    the time axes of some of its files (see the module's description); mtimes
    is {path: modification time}. Returns

        {"years": [first, last] or None, "streams": {pattern: [first, last]}
         of those counted, "left_out": {pattern: files} of those too small to
         be output, "dropped": [patterns whose years miss the output's],
         "full": [patterns read in full], "read": files read,
         "shared": whether workers read any, "seconds": how long it took}
    """
    started = time.monotonic()
    rng = rng or random.Random()
    mtimes = mtimes or {}
    streams = {pattern: list(paths) for pattern, paths in streams.items() if paths}
    if len(streams) > MAX_STREAMS:
        streams = {"": [path for paths in streams.values() for path in paths]}
    left_out = {}
    if streams:
        largest = max(len(paths) for paths in streams.values())
        if largest > 1:
            # the main output: files beside it are not read
            least = max(2, largest * MAIN_SHARE)
            left_out = {pattern: len(paths) for pattern, paths in streams.items() if len(paths) < least}
            streams = {pattern: paths for pattern, paths in streams.items() if pattern not in left_out}
    reader = _Reader(read, workers)
    found = reader.found
    try:
        picks = {}
        for pattern, paths in streams.items():
            ordered = sorted(paths, key=natural_key)
            picks[pattern] = [ordered[0], ordered[-1]]
            if mtimes:
                by_time = sorted(paths, key=lambda path: mtimes.get(path, 0))
                picks[pattern] += [by_time[0], by_time[-1]]
        reader([path for paths in picks.values() for path in paths])
        ranges = {}
        for pattern, paths in picks.items():
            for path in paths:
                ranges[pattern] = _widen(ranges.get(pattern), found[path])

        # a stream whose order is not its times' has a file outside the years found
        pool = [
            (pattern, path)
            for pattern, paths in streams.items()
            if len(paths) > 4
            for path in paths
            if path not in found
        ]
        sample = rng.sample(pool, min(checks, len(pool)))
        reader([path for _, path in sample])
        full = set()
        for pattern, path in sample:
            here, known = found[path], ranges.get(pattern)
            if here is not None and (known is None or here[0] < known[0] or here[1] > known[1]):
                full.add(pattern)
        reader([path for pattern in sorted(full) for path in streams[pattern]])
        for pattern in full:
            for path in streams[pattern]:
                ranges[pattern] = _widen(ranges.get(pattern), found[path])
    finally:
        reader.close()

    ranges = {pattern: span for pattern, span in ranges.items() if span is not None}
    result = {
        "years": None, "streams": {}, "left_out": left_out, "dropped": [], "full": sorted(full),
        "read": len(found), "shared": reader.shared,
    }
    if ranges:
        most = max(len(streams[pattern]) for pattern in ranges)
        main = [span for pattern, span in ranges.items() if len(streams[pattern]) == most]
        first, last = min(span[0] for span in main), max(span[1] for span in main)
        kept = {pattern: span for pattern, span in ranges.items() if span[1] >= first and span[0] <= last}
        result["streams"] = kept
        result["dropped"] = sorted(pattern for pattern in ranges if pattern not in kept)
        result["years"] = [min(span[0] for span in kept.values()), max(span[1] for span in kept.values())]
    result["seconds"] = round(time.monotonic() - started, 2)
    return result


def group(paths):
    """paths as {stream pattern: [paths]}, the streams analysis.streams makes."""
    from oceanval.create_recipes import _file_pattern

    streams = {}
    for path in paths:
        streams.setdefault(_file_pattern(path), []).append(path)
    return streams


def signature(paths):
    """What changes when the files in paths, or the way their years are found
    (RULE), do: the rule, how many files there are, their total size and the
    latest modification time; with each one's modification time, as {path:
    seconds}."""
    mtimes = {}
    size = 0
    latest = 0
    for path in paths:
        with contextlib.suppress(OSError):
            stat = os.stat(path)
            mtimes[path] = stat.st_mtime
            size += stat.st_size
            latest = max(latest, stat.st_mtime_ns)
    return [RULE, len(paths), size, latest], mtimes


class Reading:
    """Find the years of a simulation's output (its files, paths) in a thread,
    reading the files in a process of their own, which cancel stops. The
    answer is kept in .oceanvalcache under key (see user_cache.output_years),
    so the same files are not read again. done(result) is called from the
    thread with years_of_streams' result, or {"years": None, "error": ...}."""

    def __init__(self, key, paths, done, workers=4):
        self.key = key
        self.paths = list(paths)
        self.done = done
        self.workers = workers
        self.process = None
        self.cancelled = False
        self.result = None

    def start(self):
        threading.Thread(target=self._run, name="oceanval-output-years", daemon=True).start()
        return self

    def _run(self):
        from oceanval import user_cache

        try:
            stamp, mtimes = signature(self.paths)
            result = user_cache.output_years(self.key, stamp)
            if result is None and not self.cancelled:
                result = self._read(mtimes)
                if "error" not in result and not self.cancelled:
                    user_cache.record_output_years(self.key, stamp, result)
        except Exception as error:
            result = {"years": None, "error": f"{type(error).__name__}: {error}"}
        if self.cancelled:
            return
        self.result = result
        self.done(result)

    def _read(self, mtimes):
        request = json.dumps({
            "streams": group(self.paths),
            "mtimes": mtimes,
            "workers": self.workers,
        })
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
        if self.cancelled:
            self._stop()
        output, _ = self.process.communicate(request)
        for line in reversed(output.splitlines()):
            if line.startswith(RESULT_MARKER):
                return json.loads(line[len(RESULT_MARKER):])
        return {"years": None, "error": "the files' times could not be read"}

    def _stop(self):
        process = self.process
        if process is not None and process.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)

    def cancel(self):
        self.cancelled = True
        self._stop()


def main():
    try:
        request = json.load(sys.stdin)
        result = years_of_streams(
            request["streams"], request.get("mtimes"), workers=int(request.get("workers") or 1)
        )
    except Exception as error:
        result = {"years": None, "error": f"{type(error).__name__}: {error}"}
    sys.stdout.write(RESULT_MARKER + json.dumps(result) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
