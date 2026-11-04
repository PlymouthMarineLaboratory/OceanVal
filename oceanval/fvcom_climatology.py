"""The climatology of raw FVCOM output, on its own mesh, for analyze(): at
the surface, at the bottom, and the vertical mean or depth integral of the
water column.

CDO cannot read FVCOM's unstructured variables (see oceanval.fvcom), so the
nctoolkit chain the rest of Analyze uses cannot work them out. Here they are
read with netCDF4, a block of times at a time, so a file is never read whole,
and each time step is taken to its layer as nctoolkit would take it:

- the surface is the top sigma layer (siglay 0, FVCOM's first layer being the
  one at the surface);
- the bottom is the deepest layer with a value, as nctoolkit's
  bottom(choice="value") (CDO's bottomvalue) takes it;
- the water column is weighted by the thickness of each layer, as nctoolkit's
  vertical_mean and vertical_integration weight it: the integral is the sum
  of value x thickness, and the mean that divided by the sum of the
  thicknesses of the layers with a value. A layer's thickness is its share of
  the sigma levels (siglev, 0 at the surface to -1 at the seabed) of the depth
  of the water, h + zeta, as oceanval.fvcom takes the depths of the layers,
  at each time step. On the elements, the depth of the water is the mean of
  their three nodes' (siglev_center gives their levels, if the file has it).

Then for each month of each year, the sum of each node's (or element's)
values and how many there are. The monthly mean of each year is taken first,
then the climatology is the mean of each month of the year over the years, and
the annual mean the mean of those, as analysis.climatology_of does. The years
are read one at a time (monthly_means), so no more than a year of months is
held at once. The monthly means are also those the long-term and seasonal
series are of (see oceanval.analysis_series and oceanval.mesh_means), and the
daily climatology of the phenology is worked out in the same way, by day
(daily_climatology). The transects (see oceanval.fvcom_transects) take the
monthly means of only the cells along their lines (pick), at the surface or
of every sigma layer (LEVELS), and the mean depth of the water (DEPTH).

The files can be shared out to `cores` processes, each running this file as a
script by its path (python -I), as it imports nothing of OceanVal: a spawned
worker of a module of the package would import the package, and the user's
script with it, which would then run again.
"""

import json
import os
import subprocess
import sys
import tempfile

import cftime
import numpy as np

# the location of a variable on the mesh: its nodes or its elements
NODE = "node"
NELE = "nele"
# the vertical dimensions: the sigma layers, whose first is the surface, and the
# sigma levels, the layers' tops and bottoms
SIGLAY = "siglay"
SIGLEV = "siglev"
VERTICAL = (SIGLAY, SIGLEV)
# the layers a climatology can be worked out at, as analysis.LAYER_TITLES names them
SURFACE = "surface"
BOTTOM = "bottom"
MEAN = "mean"
INTEGRAL = "integral"
LAYERS = (SURFACE, BOTTOM, MEAN, INTEGRAL)
# what a vertical transect needs (see oceanval.fvcom_transects): every sigma layer
# as it is, and the depth of the water, h + zeta
LEVELS = "levels"
DEPTH = "depth"
READ_LAYERS = LAYERS + (LEVELS, DEPTH)
# the mesh, as the results files keep it, so a map can be drawn of them alone
MESH = ("lon", "lat", "lonc", "latc", "x", "y", "nv")
# the most values read at once, from one variable
BLOCK_VALUES = 20_000_000
# the most values of a daily climatology worked out at once (days x cells): a mesh with more
# cells is worked out a band of its cells at a time
DAILY_VALUES = 40_000_000
# what the sums are taken over: each month of each year, or each day
MONTH = "month"
DAY = "day"


def variables(path):
    """The variables of the FVCOM file at path that can be mapped (those on
    its nodes or elements, other than the mesh's own), as analysis' streams
    gives them: {name: {"long_name", "units", "levels"}}, levels the sigma
    layers or levels, or 1."""
    import netCDF4

    from oceanval.viewer_mesh import MESH_VARIABLES

    found = {}
    with netCDF4.Dataset(path) as handle:
        for name, variable in handle.variables.items():
            dims = variable.dimensions
            if name in MESH_VARIABLES or not ({NODE, NELE} & set(dims)) or "time" not in dims:
                continue
            levels = [len(handle.dimensions[dim]) for dim in dims if dim in VERTICAL]
            found[str(name)] = {
                "long_name": str(getattr(variable, "long_name", "")),
                "units": str(getattr(variable, "units", "")),
                "levels": int(levels[0]) if levels else 1,
            }
    return found


def location(path, parts):
    """NODE or NELE: where every one of the variables in parts is on the mesh
    in the file at path. Raises ValueError if they differ, or are on neither."""
    import netCDF4

    found = {}
    with netCDF4.Dataset(path) as handle:
        for name in parts:
            dims = handle.variables[name].dimensions
            found[name] = NODE if NODE in dims else NELE if NELE in dims else None
    if any(where is None for where in found.values()):
        missing = [name for name, where in found.items() if where is None]
        raise ValueError(
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} on neither the nodes nor the "
            "elements of the FVCOM mesh, so cannot be mapped."
        )
    if len(set(found.values())) > 1:
        raise ValueError(
            " and ".join(parts) + " are not all on the same part of the FVCOM mesh (some are on its nodes, "
            "some on its elements), so cannot be summed."
        )
    return next(iter(found.values()))


def vertical(path, parts):
    """The vertical dimension of each of the variables in parts in the FVCOM
    file at path: {name: SIGLAY, SIGLEV or None, for one with none}."""
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        return {
            name: next((dim for dim in handle.variables[name].dimensions if dim in VERTICAL), None) for name in parts
        }


def has_lon_lat(path):
    """Whether the FVCOM file at path has the longitudes and latitudes of its
    mesh (a mesh in metres has them all 0, or none)."""
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        if "lon" not in handle.variables or "lat" not in handle.variables:
            return False
        lon = np.ma.filled(np.ma.asarray(handle.variables["lon"][:]).astype("float64"), np.nan)
        lat = np.ma.filled(np.ma.asarray(handle.variables["lat"][:]).astype("float64"), np.nan)
    return bool(np.nanmax(np.abs(lon)) > 0 or np.nanmax(np.abs(lat)) > 0)


def not_layered(path, parts):
    """Those of parts that are not on the sigma layers (siglay) of the FVCOM
    file at path, and so have no water column to take: those on the sigma
    levels (siglev), the tops and bottoms of the layers, and those with no
    vertical dimension."""
    return [name for name, dim in vertical(path, parts).items() if dim != SIGLAY]


def _times(handle, time_shift):
    """The (year, month, day) of each time in an open FVCOM file, moved by
    time_shift days."""
    time = handle.variables["time"]
    calendar = getattr(time, "calendar", "standard")
    dates = cftime.num2date(np.asarray(time[:], dtype="float64"), time.units, calendar)
    dates = np.atleast_1d(dates)
    if time_shift:
        import datetime

        dates = [date + datetime.timedelta(days=time_shift) for date in dates]
    return [(int(date.year), int(date.month), int(date.day)) for date in dates]


def _missing(values, as_missing):
    """values with those as_missing says (a value, or [low, high]) made NaN."""
    if as_missing is None:
        return values
    if isinstance(as_missing, (list, tuple)):
        low, high = as_missing if len(as_missing) == 2 else (as_missing[0], as_missing[0])
    else:
        low = high = as_missing
    values[(values >= low) & (values <= high)] = np.nan
    return values


def _read(variable, start, stop, level=None, cells=None):
    """The values of variable at times start to stop as a float64 array with
    NaN where they are missing: at its level-th sigma layer (or level), as
    (time, cells), or with level None at all of them, as (time, levels,
    cells). A variable with no vertical dimension is (time, cells). cells, a
    slice, reads only those of its nodes or elements."""
    dims = variable.dimensions
    index, kept = [], []
    for dim in dims:
        if dim == "time":
            index.append(slice(start, stop))
            kept.append(dim)
        elif dim in (NODE, NELE):
            index.append(slice(None) if cells is None else cells)
            kept.append(dim)
        elif dim in VERTICAL and level is None:
            index.append(slice(None))
            kept.append(dim)
        else:
            # the level asked for (any other dimension: its first)
            index.append(level if dim in VERTICAL else 0)
    variable.set_auto_maskandscale(True)
    values = np.ma.filled(np.ma.asarray(variable[tuple(index)]).astype("float64"), np.nan)
    # in the order time, levels, cells, whatever the file's
    values = np.transpose(values, [kept.index(dim) for dim in ("time",) + VERTICAL + (NODE, NELE) if dim in kept])
    return values.reshape((stop - start,) + values.shape[1:-1] + (-1,))


def _summed(handle, parts, start, stop, as_missing, level=None, cells=None):
    """The sum of parts (see _read), with as_missing made missing in each. A
    value missing in any part is missing in the sum, as CDO sums."""
    values = None
    for name in parts:
        part = _missing(_read(handle.variables[name], start, stop, level, cells), as_missing)
        values = part if values is None else values + part
    return values


def _bottom(handle, parts, start, stop, as_missing):
    """The sum of the deepest value of each of parts that is not missing, at
    times start to stop, as (time, cells): the last layer, and the layers
    above only where it is missing, as nctoolkit's bottom(choice="value")
    takes it (a part with no vertical dimension is as it is)."""
    values = None
    for name in parts:
        part = _deepest(handle, name, start, stop, as_missing)
        values = part if values is None else values + part
    return values


def _deepest(handle, name, start, stop, as_missing):
    """The deepest value of the variable name that is not missing (see _bottom)."""
    dim = next((dim for dim in handle.variables[name].dimensions if dim in VERTICAL), None)
    if dim is None:
        return _summed(handle, [name], start, stop, as_missing, 0)
    levels = len(handle.dimensions[dim])
    values = _summed(handle, [name], start, stop, as_missing, levels - 1)
    for level in range(levels - 2, -1, -1):
        gap = ~np.isfinite(values)
        if not gap.any():
            break
        above = _summed(handle, [name], start, stop, as_missing, level)
        values[gap] = above[gap]
    return values


def _needed(handle, name, path):
    if name not in handle.variables:
        raise ValueError(
            f"{path} has no {name}, which the water column of FVCOM output needs: the thickness of its "
            "layers is worked out from the sigma levels (siglev), the bathymetry (h) and the surface "
            "elevation (zeta, taken as 0 if it is not there)."
        )
    variable = handle.variables[name]
    variable.set_auto_maskandscale(True)
    return np.ma.filled(np.ma.asarray(variable[:]).astype("float64"), np.nan)


def _nv(handle):
    """The nodes of each element, from 0, as (3, elements)."""
    nv = np.asarray(handle.variables["nv"][:]).astype("int64")
    return nv - 1


def _shares(handle, where, path):
    """The share of the depth of the water in each sigma layer, as (layers,
    cells), or (layers, 1) if the sigma levels are the same everywhere."""
    if where == NELE and "siglev_center" in handle.variables:
        siglev = _needed(handle, "siglev_center", path)
        on_nodes = False
    else:
        siglev = _needed(handle, SIGLEV, path)
        on_nodes = siglev.ndim > 1
    if siglev.ndim == 1:
        siglev = siglev[:, None]
    # the sigma levels run from 0 at the surface to -1 at the seabed
    shares = np.abs(siglev[:-1] - siglev[1:])
    if where == NELE and on_nodes:
        shares = shares[:, _nv(handle)].mean(axis=1)
    return shares


def _depth_of_water(handle, where, start, stop, h):
    """The depth of the water, h + zeta, at times start to stop, as (time,
    cells): on the elements the mean of their three nodes'. Missing where it
    is not above 0 (a dry cell)."""
    if "zeta" in handle.variables:
        zeta = _read(handle.variables["zeta"], start, stop)
    else:
        zeta = np.zeros((stop - start, h.size))
    depth = h[None, :] + zeta
    if where == NELE:
        depth = depth[:, _nv(handle)].mean(axis=1)
    depth[~(depth > 0)] = np.nan
    return depth


def _column(handle, parts, start, stop, as_missing, layer, where, shares, h):
    """The vertical mean (layer MEAN) or depth integral (INTEGRAL) of the sum
    of parts at times start to stop, as (time, cells), each layer weighted by
    its thickness, as nctoolkit's vertical_mean and vertical_integration
    weight them. A layer with no value is left out; a column with none, or no
    depth of water, is missing."""
    values = _summed(handle, parts, start, stop, as_missing)
    thickness = shares[None, :, :] * _depth_of_water(handle, where, start, stop, h)[:, None, :]
    ok = np.isfinite(values) & np.isfinite(thickness)
    total = np.where(ok, values * thickness, 0.0).sum(axis=1)
    if layer == INTEGRAL:
        return np.where(ok.any(axis=1), total, np.nan)
    depth = np.where(ok, thickness, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(depth > 0, total / np.where(depth > 0, depth, 1.0), np.nan)


def file_sums(path, parts, years, time_shift=None, as_missing=None, layer=SURFACE, by=MONTH, cells=None, pick=None):
    """The sums of the values of the sum of parts in layer (one of
    READ_LAYERS) in one FVCOM file, and how many there are, in each month of
    years ([first, last]): {(year, month): (sums, counts)}, each an array with
    a value for each cell. A cell missing in any part is missing in the sum, as
    CDO sums. by DAY takes them over each day instead, {(year, month, day):
    ...}. cells ([first, last + 1], the surface only) reads only those
    cells, and pick (the numbers of the cells, in order) only those cells.
    LEVELS has a value for each sigma layer of each cell, layer by layer
    (layers x cells, flattened), and DEPTH is the depth of the water, h +
    zeta (parts are not read)."""
    import netCDF4

    if layer not in READ_LAYERS:
        raise ValueError(f"layer must be one of {', '.join(READ_LAYERS)}, not {layer}.")
    if by not in (MONTH, DAY):
        raise ValueError(f"by must be {MONTH} or {DAY}, not {by}.")
    if cells is not None and layer != SURFACE:
        raise ValueError("Only the surface can be read a band of cells at a time.")
    if cells is not None and pick is not None:
        raise ValueError("Give cells or pick, not both.")
    band = None if cells is None else slice(int(cells[0]), int(cells[1]))
    chosen = None
    if pick is not None:
        pick = np.asarray(pick, dtype="int64")
        if pick.size == 0 or np.any(np.diff(pick) <= 0):
            raise ValueError("pick must be the numbers of one or more cells, in order.")
        # the cells from the first picked to the last are read, and the picked kept
        band = slice(int(pick[0]), int(pick[-1]) + 1)
        chosen = pick - pick[0]
    found = {}
    with netCDF4.Dataset(path) as handle:
        dates = _times(handle, time_shift)
        if not dates:
            return found
        months = [date[:2] if by == MONTH else date for date in dates]
        first = handle.variables[parts[0]]
        where = NODE if NODE in first.dimensions else NELE
        cells = int(np.prod([len(handle.dimensions[dim]) for dim in first.dimensions if dim in (NODE, NELE)]))
        if layer == DEPTH:
            h = _needed(handle, "h", path)
            # zeta is read on every node
            cells, read = h.size, 2
        else:
            if band is not None:
                cells = len(range(cells)[band])
            read = 1
        if layer in (MEAN, INTEGRAL, LEVELS):
            bad = [name for name in parts if SIGLAY not in handle.variables[name].dimensions]
            if bad:
                raise ValueError(
                    f"{', '.join(bad)} {'is' if len(bad) == 1 else 'are'} not on the sigma layers (siglay) of the "
                    "FVCOM mesh, so there is no "
                    + ("water column to take." if layer != LEVELS else "section through the water column to take.")
                )
            read = len(handle.dimensions[SIGLAY])
        if layer in (MEAN, INTEGRAL):
            shares = _shares(handle, where, path)
            h = _needed(handle, "h", path)
            # the layers of a block at once, and their thicknesses
            read = 2 * len(handle.dimensions[SIGLAY])
        block = max(1, BLOCK_VALUES // max(cells * read, 1))
        for start in range(0, len(months), block):
            stop = min(start + block, len(months))
            wanted = [i for i in range(start, stop) if years[0] <= months[i][0] <= years[1]]
            if not wanted:
                continue
            if layer == SURFACE:
                values = _summed(handle, parts, start, stop, as_missing, 0, band)
            elif layer == LEVELS:
                values = _summed(handle, parts, start, stop, as_missing, None, band)
            elif layer == DEPTH:
                values = _depth_of_water(handle, where, start, stop, h)
                if band is not None:
                    values = values[..., band]
            elif layer == BOTTOM:
                values = _bottom(handle, parts, start, stop, as_missing)
            else:
                values = _column(handle, parts, start, stop, as_missing, layer, where, shares, h)
            if chosen is not None:
                values = values[..., chosen]
            for i in wanted:
                row = values[i - start].reshape(-1)
                sums, counts = found.setdefault(months[i], (np.zeros(row.size), np.zeros(row.size, dtype="int64")))
                ok = np.isfinite(row)
                sums[ok] += row[ok]
                counts += ok
    return found


def _add(total, found):
    for month, (sums, counts) in found.items():
        if month in total:
            total[month][0][:] += sums
            total[month][1][:] += counts
        else:
            total[month] = (sums.copy(), counts.copy())


def _run_jobs(jobs, out_dir):
    """Work out the file_sums of each of jobs ([path, parts, years,
    time_shift, as_missing, layer, by, cells, pick]), saving each as
    out_dir/<n>.npz: the script each worker process runs."""
    for number, (path, parts, years, time_shift, as_missing, layer, by, cells, pick) in jobs:
        found = file_sums(path, parts, years, time_shift, as_missing, layer, by, cells, pick)
        arrays = {}
        for key, (sums, counts) in found.items():
            name = "_".join(str(x) for x in key)
            arrays[f"sums_{name}"] = sums
            arrays[f"counts_{name}"] = counts
        np.savez(os.path.join(out_dir, f"{number}.npz"), **arrays)


def _read_saved(path):
    found = {}
    with np.load(path) as saved:
        for name in saved.files:
            if name.startswith("sums_"):
                key = name[len("sums_"):]
                found[tuple(int(x) for x in key.split("_"))] = (saved[name], saved[f"counts_{key}"])
    return found


def monthly_sums(files, parts, years, time_shift=None, as_missing=None, cores=1, layer=SURFACE, by=MONTH,
                 cells=None, pick=None):
    """The file_sums of every one of files in layer, added: {(year, month):
    (sums, counts)} (or by day, see file_sums). With cores > 1 the files are
    shared out to that many processes running this file as a script."""
    total = {}
    workers = max(1, min(int(cores), len(files)))
    if workers == 1:
        for path in files:
            _add(total, file_sums(path, parts, years, time_shift, as_missing, layer, by, cells, pick))
        return total
    cells = None if cells is None else [int(cells[0]), int(cells[1])]
    pick = None if pick is None else [int(cell) for cell in pick]
    jobs = [
        [number, [path, list(parts), list(years), time_shift, as_missing, layer, by, cells, pick]]
        for number, path in enumerate(files)
    ]
    with tempfile.TemporaryDirectory(prefix="oceanval_fvcom_") as out_dir:
        running = []
        for worker in range(workers):
            share = jobs[worker::workers]
            running.append(
                subprocess.Popen(
                    [sys.executable, "-I", os.path.abspath(__file__), out_dir],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
            running[-1].stdin.write(json.dumps(share))
            running[-1].stdin.close()
        errors = []
        for process in running:
            error = process.stderr.read()
            process.wait()
            if process.returncode != 0:
                errors.append(error[-3000:])
        if errors:
            raise RuntimeError("The FVCOM output could not be read:\n" + errors[0])
        for number, _ in jobs:
            _add(total, _read_saved(os.path.join(out_dir, f"{number}.npz")))
    return total


def _mean(sums, counts):
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)


def climatology(total):
    """The months of the year in total (see monthly_sums), and the
    climatology of each: the mean over the years of each year's monthly mean,
    as ([month, ...], array (months, cells)). A cell with no values is NaN."""
    climate = _Climate()
    for (year, month), (sums, counts) in sorted(total.items()):
        climate.add(year, month, _mean(sums, counts))
    months, fields, _ = climate.fields()
    return months, fields


class _Climate:
    """The mean over the years of each period of the year (a month, or a
    (month, day)), added a year's mean at a time: a cell with no values is
    NaN."""

    def __init__(self):
        self.sums, self.seen, self.last = {}, {}, {}

    def add(self, year, period, mean):
        if period not in self.sums:
            self.sums[period] = np.zeros(mean.shape)
            self.seen[period] = np.zeros(mean.shape, dtype="int64")
        ok = np.isfinite(mean)
        self.sums[period][ok] += mean[ok]
        self.seen[period] += ok
        self.last[period] = max(year, self.last.get(period, year))

    def fields(self):
        """(periods, array (periods, cells), the last year of each period)."""
        periods = sorted(self.sums)
        if not periods:
            return [], np.zeros((0, 0)), []
        fields = np.vstack([_mean(self.sums[period], self.seen[period]) for period in periods])
        return periods, fields, [self.last[period] for period in periods]


def years_of(file_years, years):
    """The years ([first, last]) to read one at a time, each with the files
    holding times in it: [(year, files)]. file_years is {path: the years of its
    (shifted) times}; files whose times are not known are read for every year."""
    return [
        (year, [path for path, held in file_years.items() if held is None or year in held])
        for year in range(int(years[0]), int(years[1]) + 1)
    ]


def monthly_means(files, parts, years, time_shift=None, as_missing=None, cores=1, layer=SURFACE, file_years=None,
                  pick=None):
    """The mean of each month of each year in layer of the sum of parts in the
    FVCOM files, over years ([first, last]), in order: yields ((year, month),
    array of a value for each cell, NaN where there is none). With file_years
    ({path: the years of its times}, as analyze() reads them) a year is read at
    a time, from only its files, so no more than a year of months is held.
    pick: only those cells (see file_sums)."""
    if file_years is None:
        total = monthly_sums(files, parts, years, time_shift, as_missing, cores, layer, pick=pick)
        for key in sorted(total):
            yield key, _mean(*total[key])
        return
    file_years = {path: file_years.get(path) for path in files}
    for year, chosen in years_of(file_years, years):
        if not chosen:
            continue
        total = monthly_sums(chosen, parts, [year, year], time_shift, as_missing, cores, layer, pick=pick)
        for key in sorted(total):
            yield key, _mean(*total[key])


def daily_climatology(files, parts, years, time_shift=None, as_missing=None, cores=1, file_years=None):
    """The daily climatology of the sum of parts at the surface of the FVCOM
    files over years ([first, last]): each year's mean of each day, then the
    mean over the years of each day of the year, as CDO's ydaymean takes it
    of daily output. Returns ([(year, month, day)], array (days, cells)), the
    days in order through the year, each in the last year that has it, as
    CDO dates the days of a climatology. A mesh with many cells is worked out
    a band of them at a time (DAILY_VALUES)."""
    import netCDF4

    with netCDF4.Dataset(files[0]) as handle:
        dims = handle.variables[parts[0]].dimensions
        cells = int(np.prod([len(handle.dimensions[dim]) for dim in dims if dim in (NODE, NELE)]))
    file_years = {path: (file_years or {}).get(path) for path in files}
    step = max(1, DAILY_VALUES // 366)
    days, pieces = None, []
    for first in range(0, cells, step):
        band = [first, min(first + step, cells)]
        climate = _Climate()
        for year, chosen in years_of(file_years, years):
            if not chosen:
                continue
            total = monthly_sums(chosen, parts, [year, year], time_shift, as_missing, cores, SURFACE, DAY, band)
            for (y, month, day) in sorted(total):
                climate.add(y, (month, day), _mean(*total[(y, month, day)]))
        periods, fields, last = climate.fields()
        if not periods:
            return [], np.zeros((0, 0))
        days = [(year, month, day) for year, (month, day) in zip(last, periods)]
        pieces.append(fields.astype("float32"))
    return days, np.hstack(pieces)


def mesh_arrays(path):
    """The mesh of the FVCOM file at path as numpy arrays, {name: values} for
    the MESH variables it has: only these are read, never a variable of the
    model's values. The caller holds oceanval.netcdf_lock.LOCK in a thread of
    the window."""
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        return {name: np.asarray(handle.variables[name][:]) for name in MESH if name in handle.variables}


def same_mesh(first, second):
    """Whether two meshes (from mesh_arrays) are the same: the same cells (nv)
    in the same places."""
    if set(first) != set(second):
        return False
    for name in first:
        a, b = first[name], second[name]
        if a.shape != b.shape:
            return False
        if name == "nv":
            if not np.array_equal(a, b):
                return False
        elif not np.allclose(a, b, rtol=0, atol=1e-6, equal_nan=True):
            return False
    return True


def _mesh(path):
    """The variables of the mesh of the FVCOM file at path, as xarray
    variables, and the calendar of its times."""
    import netCDF4
    import xarray as xr

    mesh = {}
    with netCDF4.Dataset(path) as handle:
        for name in MESH:
            if name in handle.variables:
                variable = handle.variables[name]
                attrs = {key: variable.getncattr(key) for key in variable.ncattrs() if key != "_FillValue"}
                mesh[name] = xr.Variable(variable.dimensions, np.asarray(variable[:]), attrs)
        calendar = getattr(handle.variables["time"], "calendar", "standard") if "time" in handle.variables else "standard"
    return mesh, calendar


FILL = np.float32(-1e20)
TIME_UNITS = "days since 1900-01-01 00:00:00"


def save(path, key, where, months, fields, year, mesh_from, units, long_name, days=None):
    """Save fields (a row for each of months) as variable key on the mesh
    (where: NODE or NELE) of the FVCOM file mesh_from, at path: a time for
    each month, on its first day in year, as CDO dates a climatology, and the
    month of each in "month". days ([(year, month, day)]) gives the date of
    each row instead (a daily climatology's)."""
    import xarray as xr

    mesh, calendar = _mesh(mesh_from)
    if days is None:
        dates = [cftime.datetime(year, month, 1, calendar=calendar) for month in months]
    else:
        dates = [cftime.datetime(y, m, d, calendar=calendar) for y, m, d in days]
        months = [m for _, m, _ in days]
    time = cftime.date2num(dates, TIME_UNITS, calendar)
    ds = xr.Dataset(
        {
            key: (("time", where), np.asarray(fields, dtype="float32"), {"units": units, "long_name": long_name}),
            "month": (("time",), np.asarray(months, dtype="int32"), {"long_name": "month of the year"}),
            **mesh,
        },
        coords={"time": ("time", np.asarray(time, dtype="float64"), {"units": TIME_UNITS, "calendar": calendar})},
        attrs={"source": "oceanval", "mesh": where},
    )
    ds[key].encoding["_FillValue"] = FILL
    if os.path.exists(path):
        os.remove(path)
    ds.to_netcdf(path)
    return path


def work_out(files, parts, years, key, monthly, annual, units, long_name, time_shift=None, as_missing=None, cores=1,
             layer=SURFACE, file_years=None):
    """The climatology in layer (one of LAYERS) of the sum of parts in the
    FVCOM files, over years ([first, last]): its monthly climatology saved
    at monthly (unless it is None), and its annual mean, the mean of the
    months, at annual. file_years: see monthly_means. Returns (where, months):
    the part of the mesh it is on, and how many months the climatology has.
    Raises ValueError if there is no output in years."""
    where = location(files[0], parts)
    climate = _Climate()
    for (year, month), mean in monthly_means(files, parts, years, time_shift, as_missing, cores, layer, file_years):
        climate.add(year, month, mean)
    months, fields, last = climate.fields()
    if not months:
        raise ValueError(f"There is no output of {'+'.join(parts)} in {years[0]}-{years[1]}.")
    last = max(last)
    if monthly is not None:
        save(monthly, key, where, months, fields, last, files[0], units, long_name)
    save(annual, key, where, [months[0]], _mean(np.nansum(fields, axis=0), np.isfinite(fields).sum(axis=0))[None, :],
         last, files[0], units, long_name)
    return where, len(months)


def daily_work_out(files, parts, years, key, out, units, long_name, time_shift=None, as_missing=None, cores=1,
                   file_years=None):
    """The daily climatology at the surface of the sum of parts in the FVCOM
    files over years (see daily_climatology), saved at out on the mesh.
    Returns the part of the mesh it is on. Raises ValueError if there is no
    output in years."""
    where = location(files[0], parts)
    days, fields = daily_climatology(files, parts, years, time_shift, as_missing, cores, file_years)
    if not days:
        raise ValueError(f"There is no output of {'+'.join(parts)} in {years[0]}-{years[1]}.")
    save(out, key, where, None, fields, None, files[0], units, long_name, days=days)
    return where


if __name__ == "__main__":
    _run_jobs(json.loads(sys.stdin.read()), sys.argv[1])
