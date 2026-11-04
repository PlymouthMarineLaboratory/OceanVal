"""Transects of raw FVCOM output, for analyze() (see oceanval.analysis_transects).

nctoolkit's to_transect regrids a dataset bilinearly onto the points of the
line, which CDO can only do from a grid with rows and columns, and CDO cannot
read FVCOM's mesh at all. So the monthly means are worked out on the mesh, as
for the climatology and the series (see oceanval.fvcom_climatology), of only
the cells the lines need, and each line's are put on a strip of a lon-lat grid
along it, which to_transect then extracts the line from as it does any other
output's:

- each point of the line has two rows (or columns, along an east-west line)
  of the strip a little either side of it, and the strip is two columns wide,
  a little either side of the line, so that each point is inside a cell of the
  strip whose four corners all hold the point's value: bilinear remapping
  gives the value back as it is, and a point with no value spreads to none of
  its neighbours;
- a point's value is interpolated on the mesh as its maps draw it (see
  oceanval.mesh_maps): linearly within the triangle it is in, from the values
  at its three nodes, or the value of the triangle, for a variable on the
  elements. A point outside the mesh, or in a triangle with a value missing,
  has none.

A section through the water column is of the sigma layers, each cell of a
position put at its depth from the thickness of the layers there: the layer's
share of the sigma levels (siglev) of the mean depth of the water, h + zeta,
over the months worked out (the thickness is worked out at each time step
for the water column, but a section has one depth for each cell). That
thickness is put on the strip and extracted as the values are, so it goes on
to the depths as any other model's thickness does (see
analysis_transects.cell_depths).
"""

import os

import cftime
import numpy as np
import xarray as xr

from oceanval import fvcom_climatology, transects
from oceanval.analysis_transects import STEPS_UNKNOWN, line_length, steps_of

# how far either side of a point the rows and columns of its strip are, as a share
# of the distance between the points
NEAR = 1e-3
# the points a line is sampled at to find the size of the mesh's triangles along it
SAMPLES = 200
# what the strip's files call the thickness of the layers
THICKNESS = "thickness"


def _wrap(lon):
    return (np.asarray(lon, dtype="float64") + 180) % 360 - 180


def line_points(line, steps):
    """The longitudes and latitudes of the points to_transect extracts the
    line at, as analysis_transects.work_out asks for them."""
    start, end = transects.line_ends(line)
    return np.linspace(start[0], end[0], steps), np.linspace(start[1], end[1], steps)


def strip(line, steps):
    """The strip of a lon-lat grid the line is extracted from (see the
    module's notes): (lons, lats, along), the strip's longitudes and
    latitudes, in order, and the points of the line in the order of the
    strip's rows (north-south) or columns (east-west), each in two of them, as
    (lons, lats)."""
    lons, lats = line_points(line, steps)
    north_south = transects.direction(line) == "north-south"
    along = lats if north_south else lons
    order = np.argsort(along, kind="stable")
    along = along[order]
    gap = np.min(np.diff(along)) if steps > 1 else 1.0
    near = gap * NEAR if gap > 0 else NEAR
    pairs = np.ravel(np.column_stack([along - near, along + near]))
    across = lons[0] if north_south else lats[0]
    sides = np.array([across - near, across + near])
    if north_south:
        return sides, pairs, (lons[order], lats[order])
    return pairs, sides, (lons[order], lats[order])


def weights(mesh, where, lons, lats):
    """How a value at each of the points (lons, lats) is interpolated from
    the cells of the mesh (an oceanval.viewer_mesh.Mesh) a variable is on
    (where: "node" or "nele"): (cells, weights), each (points, 3) for the
    nodes, linear within the triangle a point is in, or (points, 1) for the
    elements, the triangle's own value. The weights are NaN at a point
    outside the mesh."""
    from matplotlib.tri import Triangulation

    x, y = _wrap(lons), np.asarray(lats, dtype="float64")
    triangles = mesh.triangles
    found = Triangulation(mesh.x, mesh.y, triangles).get_trifinder()(x, y)
    inside = found >= 0
    if where == fvcom_climatology.NELE:
        cells = np.where(inside, found, 0)[:, None]
        return cells, np.where(inside, 1.0, np.nan)[:, None]
    cells = triangles[np.where(inside, found, 0)]
    cx, cy = mesh.x[cells], mesh.y[cells]
    # the barycentric coordinates of each point in its triangle
    det = (cy[:, 1] - cy[:, 2]) * (cx[:, 0] - cx[:, 2]) + (cx[:, 2] - cx[:, 1]) * (cy[:, 0] - cy[:, 2])
    with np.errstate(invalid="ignore", divide="ignore"):
        first = ((cy[:, 1] - cy[:, 2]) * (x - cx[:, 2]) + (cx[:, 2] - cx[:, 1]) * (y - cy[:, 2])) / det
        second = ((cy[:, 2] - cy[:, 0]) * (x - cx[:, 2]) + (cx[:, 0] - cx[:, 2]) * (y - cy[:, 2])) / det
    out = np.column_stack([first, second, 1 - first - second])
    out[~inside] = np.nan
    return cells, out


def interpolate(field, cells, weights):
    """The values at the points of field (..., cells), interpolated with the
    cells and weights of weights(): (..., points). A point is missing where a
    cell it is interpolated from is."""
    return (field[..., cells] * weights).sum(axis=-1)


def steps(mesh, line):
    """How many points to sample a transect of raw FVCOM output at: about one
    for each triangle of the mesh it crosses, from the mean length of the sides
    of those triangles (see oceanval.mesh_means), as finer steps add nothing
    the mesh does not have."""
    from oceanval.mesh_means import _side_lengths
    from matplotlib.tri import Triangulation

    lons, lats = line_points(line, SAMPLES)
    found = Triangulation(mesh.x, mesh.y, mesh.triangles).get_trifinder()(_wrap(lons), lats)
    found = np.unique(found[found >= 0])
    if found.size == 0:
        return STEPS_UNKNOWN
    sides = _side_lengths(mesh.x, mesh.y, mesh.triangles[found], mesh.cartesian)
    return steps_of(line_length(line), float(np.median(sides)))


def _on_strip(values, north_south):
    """values at the points of a line (..., points), as the strip holds
    them: (..., lat, lon), each point in two rows (or columns) and two columns
    (or rows)."""
    doubled = np.repeat(values, 2, axis=-1)
    if north_south:
        return np.repeat(doubled[..., :, None], 2, axis=-1)
    return np.repeat(doubled[..., None, :], 2, axis=-2)


def _save(path, name, values, lons, lats, times=None, calendar="standard", units="", long_name=""):
    """Save values (time, [level], lat, lon), or with times None (level,
    lat, lon), on the strip (lons, lats) at path, for CDO to read: a time for
    each (year, month) of times, on its first day, as fvcom_climatology.save
    dates them, and missing values marked with a fill value."""
    dims = (("time",) if times is not None else ()) + (("level",) if values.ndim - (times is not None) == 3 else ())
    dims = dims + ("lat", "lon")
    coords = {
        "lat": ("lat", np.asarray(lats, dtype="float64"), {"units": "degrees_north", "standard_name": "latitude"}),
        "lon": ("lon", np.asarray(lons, dtype="float64"), {"units": "degrees_east", "standard_name": "longitude"}),
    }
    if "level" in dims:
        coords["level"] = (
            "level",
            np.arange(1, values.shape[dims.index("level")] + 1, dtype="float64"),
            {"long_name": "sigma layer", "axis": "Z", "positive": "down"},
        )
    if times is not None:
        dates = [cftime.datetime(year, month, 1, calendar=calendar) for year, month in times]
        coords["time"] = (
            "time",
            np.asarray(cftime.date2num(dates, fvcom_climatology.TIME_UNITS, calendar), dtype="float64"),
            {"units": fvcom_climatology.TIME_UNITS, "calendar": calendar},
        )
    ds = xr.Dataset(
        {name: (dims, np.asarray(values, dtype="float32"), {"units": units, "long_name": long_name})}, coords=coords
    )
    ds[name].encoding["_FillValue"] = fvcom_climatology.FILL
    ds.to_netcdf(path)
    return path


def _calendar(path):
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        return getattr(handle.variables["time"], "calendar", "standard") if "time" in handle.variables else "standard"


def banded(row, files, lines, years, time_shift, folder, file_years=None):
    """The strips of the transects of row's variable, raw FVCOM output (see
    the module's notes), saved in folder. lines are [(number, line,
    {vertical: whether it needs the layer})], years {vertical: [first, last]}
    for the surface (False) and the water column (True), files the files to
    read. Returns ({number: steps}, {(number, vertical): (path of the strip,
    its times as (year, month), path of the thickness of the layers on it or
    None)})."""
    from oceanval.viewer_mesh import Mesh

    parts = row["parts"]
    where = fvcom_climatology.location(files[0], parts)
    mesh = Mesh.read(files[0])
    if mesh.cartesian:
        raise ValueError(
            f"The FVCOM mesh of {row['name']} is in metres, with no longitudes and latitudes, so it has no transects."
        )
    calendar = _calendar(files[0])
    found_steps, strips = {}, {}
    for number, line, _ in lines:
        found_steps[number] = steps(mesh, line)
        lons, lats, (point_lons, point_lats) = strip(line, found_steps[number])
        cells, weight = weights(mesh, where, point_lons, point_lats)
        strips[number] = {"lons": lons, "lats": lats, "cells": cells, "weights": weight,
                          "north_south": transects.direction(line) == "north-south"}
    # only the cells some line needs are read, and at least one, so the times are known
    used = np.concatenate([s["cells"][np.isfinite(s["weights"])] for s in strips.values()])
    pick = np.unique(used) if used.size else np.zeros(1, dtype="int64")
    for s in strips.values():
        s["cells"] = np.searchsorted(pick, s["cells"]).clip(0, pick.size - 1)
    out = {}
    for vertical, span in years.items():
        wanted = [number for number, _, needs in lines if needs.get(vertical)]
        if not wanted:
            continue
        layer = fvcom_climatology.LEVELS if vertical else fvcom_climatology.SURFACE
        fields, times = {number: [] for number in wanted}, []
        for (year, month), mean in fvcom_climatology.monthly_means(
            files, parts, list(span), time_shift, row.get("as_missing"), row.get("cores", 1), layer, file_years, pick
        ):
            times.append((year, month))
            if vertical:
                mean = mean.reshape(-1, pick.size)
            for number in wanted:
                fields[number].append(interpolate(mean, strips[number]["cells"], strips[number]["weights"]))
        thickness = None
        if vertical:
            thickness = _thickness(row, files, span, time_shift, where, pick, file_years)
        for number in wanted:
            s = strips[number]
            path = os.path.join(folder, f"{row['key']}_transect_{number}_{'levels' if vertical else 'surface'}.nc")
            values = np.array(fields[number]) if times else np.zeros((0,) + ((len(thickness),) if vertical else ()) + (s["weights"].shape[0],))
            _save(path, row["key"], _on_strip(values, s["north_south"]), s["lons"], s["lats"], times, calendar,
                  row.get("units", ""), row["name"])
            thick_path = None
            if vertical:
                thick_path = os.path.join(folder, f"{row['key']}_transect_{number}_thickness.nc")
                _save(thick_path, THICKNESS, _on_strip(interpolate(thickness, s["cells"], s["weights"]), s["north_south"]),
                      s["lons"], s["lats"], units="m", long_name="cell thickness")
            out[(number, vertical)] = (path, times, thick_path)
    return found_steps, out


def _thickness(row, files, span, time_shift, where, pick, file_years):
    """The thickness of each sigma layer of the cells pick, (layers, cells):
    its share of the sigma levels of the mean depth of the water over span
    (see the module's notes)."""
    import netCDF4

    total, seen = np.zeros(pick.size), np.zeros(pick.size)
    for _, depth in fvcom_climatology.monthly_means(
        files, row["parts"], list(span), time_shift, None, row.get("cores", 1), fvcom_climatology.DEPTH, file_years,
        pick,
    ):
        ok = np.isfinite(depth)
        total[ok] += depth[ok]
        seen += ok
    with np.errstate(invalid="ignore", divide="ignore"):
        depth = np.where(seen > 0, total / np.maximum(seen, 1), np.nan)
    with netCDF4.Dataset(files[0]) as handle:
        shares = fvcom_climatology._shares(handle, where, files[0])
    if shares.shape[1] > 1:
        shares = shares[:, pick]
    return shares * depth[None, :]
