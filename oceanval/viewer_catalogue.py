"""What a simulation's output holds, for the viewer (the window's View
simulation output): its streams (file types), the variables in each, and the
times of every file. Also the reading of the slices the viewer plots, which is
all that is ever read of the data, as the files can be many GB.

The variables are read from the first file of each stream (every file of a
stream holds the same ones), as analysis.streams does; the times from every
file (see oceanval.viewer_times). Reading every file's times is the long part
for output of thousands of files, so scan(wait=False) returns a Catalogue that
has only the first file of each stream's times (and those kept in the cache),
and Catalogue.read_times, run in the background, reads the rest in a process
of its own and then gives each stream all its times.

Every read of a file in the window's process holds oceanval.netcdf_lock.LOCK,
which is not re-entrant: nothing here takes it while holding it.
"""

import os
import threading
from collections import OrderedDict

import numpy as np
import xarray as xr

from oceanval import viewer_mesh as fvcom
from oceanval import viewer_times as file_times
from oceanval.create_recipes import _file_pattern, simulation_paths
from oceanval.netcdf_lock import LOCK
from oceanval.viewer_times import default_cores, natural_key

# read in a process of their own once there are this many files to read
PARALLEL_FROM = file_times.PARALLEL_FROM

# the names of vertical dimensions, as OceanVal tells them
_VERTICAL = ("depth", "lev", "sig", "s_rho", "z", "height", "nz", "deptht", "depthu", "depthv", "depthw")


class Stopped(Exception):
    """A scan that was stopped as it is no longer wanted."""


# ---- the variables of a file ----

def _attr(variable, name):
    value = variable.attrs.get(name, "")
    return str(value) if value is not None else ""


def _is_vertical(ds, dim):
    name = dim.lower()
    if dim in ds.variables:
        attrs = ds[dim].attrs
        if str(attrs.get("axis", "")).upper() == "Z" or attrs.get("positive") in ("up", "down"):
            return True
        if str(attrs.get("standard_name", "")) in ("depth", "height", "altitude"):
            return True
    return any(name == word or name.startswith(word) for word in _VERTICAL)


def _bounds_names(ds):
    names = set()
    for variable in ds.variables.values():
        for key in ("bounds", "climatology", "edges"):
            if key in variable.attrs:
                names.add(str(variable.attrs[key]))
    return names


def _lonlat(ds, name):
    """The names of the longitude and latitude of variable name, or Nones:
    its coordinates with a standard_name, units or name that says so (the
    rules of ncplot's get_dims, without metpy)."""
    variable = ds[name]
    candidates = list(variable.coords)
    for word in _attr(variable, "coordinates").split():
        if word in ds.variables and word not in candidates:
            candidates.append(word)
    candidates += [dim for dim in variable.dims if dim in ds.variables and dim not in candidates]

    def find(standard, units, words):
        for key in candidates:
            attrs = ds[key].attrs
            if str(attrs.get("standard_name", "")) == standard:
                return key
        for key in candidates:
            if str(ds[key].attrs.get("units", "")).lower() in units:
                return key
        for key in candidates:
            if any(key.lower().startswith(word) or key.lower().endswith(word) for word in words):
                return key
        return None

    lon = find("longitude", ("degrees_east", "degree_east", "degrees_e", "degreee"), ("lon", "nav_lon", "glamt"))
    lat = find("latitude", ("degrees_north", "degree_north", "degrees_n", "degreen"), ("lat", "nav_lat", "gphit"))
    # coordinates that do not span the variable's own dimensions cannot place it
    for key in (lon, lat):
        if key is not None and not set(ds[key].dims) <= set(variable.dims):
            return None, None
    return lon, lat


def describe_variables(ds, time_dim):
    """{name: about} for each data variable of ds (an open xarray Dataset):
    its long_name, units, dims, sizes, which dim is time, which is vertical,
    which are the horizontal ones, and its longitude/latitude names. For
    FVCOM output (see describe_fvcom), "mesh" says what of the mesh a
    variable is on."""
    if fvcom.looks_like_fvcom(ds):
        return describe_fvcom(ds, time_dim)
    skip = _bounds_names(ds)
    coords = set(ds.coords)
    found = {}
    for name, variable in ds.data_vars.items():
        if name in skip or name in coords or variable.ndim == 0:
            continue
        if name.endswith(("_bnds", "_bounds")):
            continue
        dims = list(variable.dims)
        time = time_dim if time_dim in dims else None
        rest = [dim for dim in dims if dim != time]
        vertical = next((dim for dim in rest if _is_vertical(ds, dim)), None)
        # the last two of the others are the horizontal ones; any before them are taken at their first index
        horizontal = [dim for dim in rest if dim != vertical][-2:]
        lon, lat = _lonlat(ds, name)
        levels = int(variable.sizes[vertical]) if vertical else 1
        found[name] = {
            "long_name": _attr(variable, "long_name") or _attr(variable, "standard_name"),
            "units": _attr(variable, "units"),
            "dims": dims,
            "shape": [int(variable.sizes[dim]) for dim in dims],
            "time_dim": time,
            "vertical": vertical,
            "levels": levels,
            "level_values": _level_values(ds, vertical) if vertical else None,
            "level_units": _attr(ds[vertical], "units") if vertical and vertical in ds.variables else "",
            "horizontal": horizontal,
            "lon": lon,
            "lat": lat,
        }
    return found


def describe_fvcom(ds, time_dim):
    """describe_variables for FVCOM output: a variable on the mesh's nodes or
    elements has that as its one horizontal dimension ("mesh": "node" or
    "nele"), and its sigma layers (siglay, siglev) as its vertical, named
    by number from the surface, as a layer is at no one depth. The mesh's
    own variables, and those of whole numbers, are left out."""
    found = {}
    for name, variable in ds.data_vars.items():
        dims = list(variable.dims)
        mesh = fvcom.mesh_location(dims)
        if name in fvcom.MESH_VARIABLES or variable.dtype.kind not in "fc" or mesh is None:
            continue
        time = time_dim if time_dim in dims else None
        vertical = next((dim for dim in dims if dim in ("siglay", "siglev")), None)
        levels = int(variable.sizes[vertical]) if vertical else 1
        found[name] = {
            "long_name": _attr(variable, "long_name") or _attr(variable, "standard_name"),
            "units": _attr(variable, "units"),
            "dims": dims,
            "shape": [int(variable.sizes[dim]) for dim in dims],
            "time_dim": time,
            "vertical": vertical,
            "levels": levels,
            "level_values": list(range(1, levels + 1)) if vertical else None,
            "level_labels": [
                f"Layer {number}" + (" (surface)" if number == 1 else " (bottom)" if number == levels else "")
                for number in range(1, levels + 1)
            ] if vertical else None,
            "level_units": "",
            "horizontal": [mesh],
            "mesh": mesh,
            "lon": None,
            "lat": None,
        }
    return found


def _level_values(ds, dim):
    if dim in ds.variables and ds[dim].ndim == 1:
        try:
            return [float(value) for value in np.asarray(ds[dim].values, dtype="float64")]
        except (TypeError, ValueError):
            pass
    return list(range(int(ds.sizes[dim])))


def open_dataset(path):
    """An xarray Dataset of path, with times left as numbers (they are read
    by oceanval.viewer_times, for any calendar). FVCOM's siglay and siglev,
    2-D variables named after one of their dimensions, which xarray refuses,
    are left out (as OceanVal opens FVCOM output). The caller holds LOCK."""
    try:
        return xr.open_dataset(path, decode_times=False, cache=False)
    except ValueError:
        return xr.open_dataset(path, decode_times=False, cache=False, drop_variables=["siglay", "siglev"])


# ---- the scan ----

def _read_times(paths, report, stopped, cores=None, read=None, reading=None):
    """{path: times} of every one of paths: those in read (by default those
    kept in the cache from before) and the rest, read in a process of their
    own (a viewer_times.Reading, reading, which can be cancelled from another
    thread) when there are many, else here, under LOCK, one file at a time, as
    other threads are reading files. report(done) is told how many of paths
    are in, and stopped() is asked whether to give up (raising Stopped). What
    is read is kept in the cache even if this is stopped, as it is not read
    again."""
    read = dict(file_times.load_cache(paths) if read is None else read)
    todo = [path for path in paths if path not in read]
    report(sum(path in read for path in paths))
    fresh = {}

    def found(path, times):
        fresh[path] = times
        report(len(paths) - len(todo) + len(fresh))

    def found_there(path, times):
        found(path, times)
        if stopped():
            reading.cancel()

    try:
        if len(todo) >= PARALLEL_FROM:
            reading = reading or file_times.Reading(cores)
            reading.run(todo, found_there)
            if stopped():
                raise Stopped()
        for path in todo:
            if path in fresh:
                continue
            if stopped():
                raise Stopped()
            with LOCK:
                times = file_times.file_times(path)
            found(path, times)
    finally:
        file_times.save_cache(fresh)
    read.update(fresh)
    return read


def resolution(times):
    """How often the output is, from its times (ISO strings): "hourly",
    "3-hourly", "daily", "5-daily", "monthly", "annual", or "" if there is
    one time or none. Only the dates are compared, so any calendar works."""
    if len(times) < 2:
        return ""
    stamps = sorted(set(times))
    days = [(int(t[0:4].lstrip("-") or 0), int(t[5:7]), int(t[8:10])) for t in stamps]
    per_day = len(stamps) / max(1, len(set(days)))
    if per_day > 1.5:
        hours = max(1, round(24 / per_day))
        return "hourly" if hours == 1 else f"{hours}-hourly"
    months = {day[:2] for day in days}
    if len(months) == len(days):
        years = {day[0] for day in days}
        return "annual" if len(years) == len(days) and len(days) > 1 else "monthly"
    gaps = sorted(later[2] - earlier[2] for earlier, later in zip(days, days[1:]) if earlier[:2] == later[:2])
    if not gaps:
        return "daily"
    step = max(1, gaps[len(gaps) // 2])
    return "daily" if step == 1 else f"{step}-daily"


def scan(simdir, ndown, exclude="", require="", progress=lambda **_: None, stopped=lambda: False, wait=True):
    """The Catalogue of the output files ndown directories below simdir that
    pass the file filters. progress(phase=, done=, total=) is told how far it
    has got, and stopped() is asked whether to give up (raising Stopped).
    With wait, the times of every file are read first. Without, only those of
    the first file of each file type are (and any the cache has), so that
    the Catalogue is there at once: each stream says how many of its files
    are "pending", and Catalogue.read_times reads the rest."""
    simdir = os.path.abspath(os.path.expanduser(simdir.strip()))
    paths = simulation_paths(simdir, int(ndown), exclude.split() or None, require.split() or None)
    if not paths:
        raise ValueError("No netCDF files were found.")
    groups = OrderedDict()
    for path in sorted(paths, key=natural_key):
        groups.setdefault(_file_pattern(path), []).append(path)

    if wait:
        progress(phase="times", done=0, total=len(paths))
        read = _read_times(
            paths, lambda done: progress(phase="times", done=done, total=len(paths)), stopped
        )
    else:
        read = file_times.load_cache(paths)

    streams = []
    progress(phase="variables", done=0, total=len(groups))
    for number, (pattern, members) in enumerate(groups.items()):
        if stopped():
            raise Stopped()
        if members[0] not in read:
            # the first file's times give the time variable, which the variables are told by
            with LOCK:
                read[members[0]] = file_times.file_times(members[0])
        streams.append(_stream(number, pattern, members, read))
        progress(phase="variables", done=number + 1, total=len(groups))
    return Catalogue(simdir, int(ndown), streams, read)


def _stream(number, pattern, members, read):
    first = members[0]
    time_name = read[first].get("name")
    variables, time_dim, error, is_fvcom = {}, None, None, False
    try:
        with LOCK:
            with open_dataset(first) as ds:
                if time_name and time_name in ds.variables and ds[time_name].ndim == 1:
                    time_dim = ds[time_name].dims[0]
                variables = describe_variables(ds, time_dim)
                is_fvcom = fvcom.looks_like_fvcom(ds)
    except Exception as problem:  # noqa: BLE001 - a stream that cannot be read is reported
        error = f"{os.path.basename(first)} could not be read: {problem}"
    return {
        "id": number,
        "pattern": pattern,
        "files": members,
        "variables": variables,
        "time_name": time_name,
        "time_dim": time_dim,
        "error": error,
        "fvcom": is_fvcom,
        **_steps_of(members, read),
    }


def _steps_of(members, read):
    """The times of a stream's files that are in read ({path: times}): every
    time of the stream in order with the file and index it is at ("steps"),
    their names ("times"), whether they are dates, how often, the calendar,
    how many files could not be read, and how many are not in read yet
    ("pending", which are left out: they are not files with no times)."""
    steps = []
    calendar = None
    unreadable = pending = 0
    for index, path in enumerate(members):
        if path not in read:
            pending += 1
            continue
        times = read[path] or {}
        if times.get("error"):
            unreadable += 1
        calendar = calendar or times.get("calendar")
        if times.get("times"):
            steps += [(stamp, index, at) for stamp, at in zip(times["times"], times["index"])]
        else:
            # no time axis: the file is one step, named by the file
            steps.append((None, index, 0))
    if all(stamp is not None for stamp, _, _ in steps):
        steps.sort(key=lambda step: (step[0], step[1]))
    stamps = [stamp for stamp, _, _ in steps]
    return {
        "calendar": calendar,
        "steps": steps,
        "times": [stamp if stamp is not None else os.path.basename(members[index]) for stamp, index, _ in steps],
        "has_times": all(stamp is not None for stamp in stamps) and bool(stamps),
        "resolution": resolution([stamp for stamp in stamps if stamp is not None]),
        "unreadable": unreadable,
        "pending": pending,
    }


class Catalogue:
    """The streams of a simulation's output (see scan), and the reading of
    the slices of its variables that the viewer plots."""

    def __init__(self, simdir, ndown, streams, read=None):
        self.simdir = simdir
        self.ndown = ndown
        self.streams = streams
        # {path: times} of the files read so far (see read_times)
        self._read = read if read is not None else {}
        self.times_complete = all(stream["pending"] == 0 for stream in streams)
        self._stop = threading.Event()
        # the process reading the times, while it runs (see stop_times)
        self._reading = None
        self._open = OrderedDict()
        # stream -> its FVCOM mesh (see mesh)
        self._meshes = {}

    # ---- the times of the files not read yet ----

    def read_times(self, progress=lambda done, total: None, stopped=lambda: False, cores=None):
        """Read the times of the files scan(wait=False) did not (in a process
        of their own, with a pool of cores workers, by default half the
        machine's, when there are many) and give each stream all its times: its steps, times, resolution and
        the rest are made again and put in place at the end, all of them at
        once (streams is a new list, so what reads one stream sees it whole,
        as it was or as it is). progress(done, total) is told how many of the
        files are in, and stopped() is asked whether to give up (raising
        Stopped, as stop_times and close do)."""
        if self.times_complete:
            return
        paths = [path for stream in self.streams for path in stream["files"]]
        self._reading = file_times.Reading(cores or default_cores())
        if self._stop.is_set():
            self._reading.cancel()
        try:
            read = _read_times(
                paths, lambda done: progress(done, len(paths)), lambda: stopped() or self._stop.is_set(),
                read=self._read, reading=self._reading,
            )
        finally:
            self._reading = None
        streams = []
        for stream in self.streams:
            made = dict(stream)
            made.update(_steps_of(stream["files"], read))
            streams.append(made)
        self._read = read
        self.streams = streams
        self.times_complete = True

    # ---- what the viewer is told ----

    def summary(self):
        """What the viewer page shows: each stream and each variable name
        that can be mapped, with the streams it can be mapped in (as
        analysis_app's picker merges them)."""
        streams = []
        variables = {}
        for stream in self.streams:
            times = stream["times"]
            streams.append({
                "id": stream["id"],
                "pattern": stream["pattern"],
                "files": len(stream["files"]),
                "first_file": os.path.relpath(stream["files"][0], self.simdir),
                "steps": len(times),
                "first": times[0] if times else None,
                "last": times[-1] if times else None,
                "times": times,
                "has_times": stream["has_times"],
                "calendar": stream["calendar"],
                "resolution": stream["resolution"],
                "unreadable": stream["unreadable"],
                "pending": stream["pending"],
                # the file (its number in the stream) each step is in, to say how many files some of the steps are in
                "step_files": [index for _, index, _ in stream["steps"]],
                "error": stream["error"],
                "fvcom": stream.get("fvcom", False),
            })
            for name, about in stream["variables"].items():
                if not mappable(about):
                    continue
                entry = variables.setdefault(name, {"long_name": "", "units": "", "streams": []})
                entry["long_name"] = entry["long_name"] or about["long_name"]
                entry["units"] = entry["units"] or about["units"]
                entry["streams"].append({
                    "stream": stream["id"],
                    "dims": about["dims"],
                    "shape": about["shape"],
                    "time": about["time_dim"] is not None,
                    "vertical": about["vertical"],
                    "levels": about["levels"],
                    "level_values": about["level_values"],
                    "level_units": about["level_units"],
                    "horizontal": about["horizontal"],
                    "mesh": about.get("mesh"),
                    "level_labels": about.get("level_labels"),
                })
        return {
            "simdir": self.simdir,
            "ndown": self.ndown,
            "files": sum(len(stream["files"]) for stream in self.streams),
            "times_complete": self.times_complete,
            "streams": streams,
            "variables": dict(sorted(variables.items(), key=lambda item: item[0].lower())),
        }

    def mesh(self, stream):
        """The FVCOM mesh of the stream (read from its first file the first
        time it is asked for, and kept, with the raster it is drawn on)."""
        stream = int(stream)
        if stream not in self._meshes:
            with LOCK:
                self._meshes[stream] = fvcom.Mesh.read(self.streams[stream]["files"][0])
        return self._meshes[stream]

    def about(self, stream, name):
        try:
            return self.streams[int(stream)]["variables"][name]
        except (IndexError, KeyError, ValueError):
            raise KeyError(f"{name} is not in that file type") from None

    # ---- reading ----

    def _dataset(self, path):
        """An open Dataset of path, a few kept open. The caller holds LOCK."""
        if path in self._open:
            self._open.move_to_end(path)
            return self._open[path]
        ds = open_dataset(path)
        self._open[path] = ds
        while len(self._open) > 8:
            _, old = self._open.popitem(last=False)
            old.close()
        return ds

    def stop_times(self):
        """Stop the times being read (read_times raises Stopped), at once,
        though the process reading them is waiting on a file."""
        self._stop.set()
        reading = self._reading
        if reading is not None:
            reading.cancel()

    def close(self):
        # stops the times being read, too
        self.stop_times()
        with LOCK:
            for ds in self._open.values():
                ds.close()
            self._open.clear()

    def _step(self, stream, step):
        stream = self.streams[int(stream)]
        steps = stream["steps"]
        step = min(max(int(step), 0), len(steps) - 1)
        _, index, at = steps[step]
        return stream, stream["files"][index], at, stream["times"][step]

    def combined(self, stream, names):
        """How to draw the sum of the variables names (one name is just that
        variable): a copy of the first one's about, with the names joined
        by " + " as its long_name (and name), and "units_differ" the units,
        if they are not all the same. Raises ValueError, in words for the
        user, if they cannot be added: they have to be in the file type, and
        have the same dimensions and sizes."""
        names = [names] if isinstance(names, str) else list(names)
        if not names:
            raise ValueError("Choose a variable.")
        if len(set(names)) != len(names):
            raise ValueError("A variable can only be added once.")
        try:
            abouts = [self.about(stream, name) for name in names]
        except KeyError as error:
            raise ValueError(str(error.args[0])) from None
        first = abouts[0]
        for name, about in zip(names[1:], abouts[1:]):
            if about["dims"] != first["dims"] or about["shape"] != first["shape"]:
                raise ValueError(
                    f"{names[0]} and {name} have different dimensions, so they cannot be added."
                )
        found = dict(first)
        units = [about["units"] for about in abouts]
        if len(names) > 1:
            found["name"] = " + ".join(names)
            found["long_name"] = " + ".join(about["long_name"] or name for name, about in zip(names, abouts))
            found["units"] = units[0] if len(set(units)) == 1 else ""
            found["units_differ"] = sorted(set(units)) if len(set(units)) > 1 else None
        else:
            found["name"] = names[0]
            found["units_differ"] = None
        return found

    def field(self, stream, names, step=0, level=0, missing=()):
        """The 2-D slice at time step and level of the variable names, or of
        the sum of them (see combined), as a loaded DataArray with its
        longitude/latitude as coords, and the time it is at. The sum is made
        here in xarray: missing is a list of (low, high) ranges (see
        missing_mask) that are set to NaN in each variable before it is
        added, and a cell that is missing in any of them is missing in the
        sum. How many cells were set missing is in attrs["n_missing"]."""
        names = [names] if isinstance(names, str) else list(names)
        about = self.combined(stream, names)
        record, path, at, when = self._step(stream, step)
        select = {}
        if about["time_dim"]:
            select[about["time_dim"]] = at
        if about["vertical"]:
            select[about["vertical"]] = min(max(int(level), 0), about["levels"] - 1)
        total = None
        n_missing = 0
        for name in names:
            with LOCK:
                ds = self._dataset(path)
                data = ds[name].isel(select)
                # anything else that is not horizontal is taken at its first index
                extra = {dim: 0 for dim in data.dims if dim not in about["horizontal"]}
                data = data.isel(extra).load()
                for key in (about["lon"], about["lat"]):
                    if key is not None and key not in data.coords and key in ds.variables:
                        data = data.assign_coords({key: ds[key].load()})
            mask = missing_mask(data, missing)
            data = data.where(~mask)
            n_missing += int(mask.sum())
            total = data if total is None else total + data
        total.attrs = {"n_missing": n_missing}
        return total, (when if about["time_dim"] else None)

    def limits(self, stream, names, level=0, missing=(), samples=12, first=0, last=None):
        """The range of the variable names (or their sum) over the run (or
        the steps first to last of it: the years chosen) at level, from up to
        samples time steps spread evenly through it (one
        slice is read for each, so a long run costs a few reads, not one for
        every step): (low, high, symmetric), symmetric being whether the
        values span 0 (as plotting.colour_limits says). None if every
        sampled slice is missing."""
        from oceanval.viewer_plots import colour_limits

        count = len(self.streams[int(stream)]["steps"])
        last = count - 1 if last is None else min(max(int(last), 0), count - 1)
        first = min(max(int(first), 0), last)
        count = last - first + 1
        if count <= samples:
            steps = list(range(first, last + 1))
        else:
            steps = sorted({first + round(i * (count - 1) / (samples - 1)) for i in range(samples)})
        low = high = None
        for step in steps:
            data, _ = self.field(stream, names, step, level, missing)
            values = data.values[np.isfinite(data.values)]
            if values.size:
                low = float(values.min()) if low is None else min(low, float(values.min()))
                high = float(values.max()) if high is None else max(high, float(values.max()))
        if low is None:
            return None
        (low, high), symmetric = colour_limits([low, high])
        return low, high, symmetric


def _bound(value, dtype):
    """value as the data's own type, so that 1e20 matches a float32 1e20
    (which is not the float64 1e20), and a value too big for the type is
    infinite instead of an error."""
    if np.issubdtype(dtype, np.floating):
        limit = float(np.finfo(dtype).max)
        if abs(value) > limit:
            return np.inf if value > 0 else -np.inf
        return dtype.type(value)
    return value


def missing_mask(data, ranges):
    """A boolean DataArray, True where data (an xarray DataArray) has a value
    the user says is missing: ranges is a list of (low, high), a single
    number being (x, x), and both ends are included. NaN never matches."""
    mask = xr.zeros_like(data, dtype=bool)
    for low, high in ranges:
        if low > high:
            low, high = high, low
        low, high = _bound(low, data.dtype), _bound(high, data.dtype)
        mask = mask | ((data >= low) & (data <= high))
    return mask


def mappable(about):
    """Whether a variable can be drawn as a map: it has two horizontal
    dimensions, or is on an FVCOM mesh."""
    return len(about["horizontal"]) >= 2 or bool(about.get("mesh"))
