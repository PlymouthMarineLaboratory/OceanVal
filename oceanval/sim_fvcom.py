"""Raw FVCOM output compared with output on other grids, in compare_simulations().

Raw FVCOM output is on a mesh of triangles, which nctoolkit's open_fvcom opens
(see oceanval.fvcom), so it is compared on its own mesh with other FVCOM output
unless the simulations are put on one grid with unify. Then, and whenever some
simulations are FVCOM and some are not, the FVCOM output is regridded (nearest
neighbour, as matchup regrids it) and is then ordinary output on a grid:
- unify={"baseline": name} (or, for FVCOM with other output, no unify): onto the
  grid of the first simulation that is not FVCOM (or the one named), cropped to
  the extent of the mesh;
- unify={"lon", "lat", "res"} (the only unify for FVCOM output alone): onto that
  lon-lat grid, and then the others are put on it as for any output.

A variable that needs more than the surface is interpolated from the sigma
layers to the depths of the baseline's levels (for a box, those of the first
simulation that is not FVCOM, else DEPTHS: 0-150 m every 5 m), so that every
simulation has the same levels, and its water column is worked out as
thickness="z_level" is.
"""

import os

import numpy as np

from oceanval import fvcom, sim_unify, surface
from oceanval.analysis import KINDS, SURFACE, Z_LEVEL, _say, first_step_of, second_step_of
from oceanval.quiet_nc import nc

# the depths FVCOM output alone is interpolated to on a lon-lat box, as no simulation has levels to go to
DEPTHS = [float(depth) for depth in range(0, 155, 5)]

_LAYERS = (
    "with the top layer's value above the top layer, the bottom layer's between it and the seabed and nothing "
    "below the seabed, so the bottom is the deepest of those levels that is above the seabed."
)
TEXT = (
    "The output of {names} is on a mesh of triangles, so it was regridded (nearest neighbour) onto the grid of "
    "{baseline}, cropped to the extent of the mesh, where only the cells inside the mesh have values. Variables "
    "below the surface were interpolated from the model's sigma layers to the depths of the levels of {baseline}, "
    + _LAYERS
)
BOX_TEXT = (
    "The output of {names} is on a mesh of triangles, so it was regridded (nearest neighbour) onto the lon-lat "
    "grid of the box, where only the cells inside the mesh have values. Variables below the surface were "
    "interpolated from the model's sigma layers to {depths}, " + _LAYERS
)


def on_mesh(rows):
    """Whether any of a simulation's variables is raw FVCOM output."""
    return any(row.get("fvcom") for row in rows)


def names_on_mesh(rows_by_sim, names):
    """The names of the simulations whose output is raw FVCOM output."""
    return [name for name in names if on_mesh(rows_by_sim[name])]


def is_mixed(rows_by_sim, names):
    """Whether some of the simulations are raw FVCOM output, and some are not."""
    return 0 < len(names_on_mesh(rows_by_sim, names)) < len(names)


def first_on_a_grid(rows_by_sim, names):
    """The first simulation that is not raw FVCOM output."""
    return next(name for name in names if not on_mesh(rows_by_sim[name]))


def crop_box(rows_by_sim, names):
    """The box the baseline is cropped to, as sim_unify.padded_box gives it: the
    extent of the meshes, a little wider. None if it can not be cropped to."""
    lon, lat = [], []
    for name in names_on_mesh(rows_by_sim, names):
        extent_lon, extent_lat = fvcom.fvcom_extent(rows_by_sim[name][0]["files"][0])
        lon += extent_lon
        lat += extent_lat
    box = sim_unify.padded_box(np.array(lon), np.array(lat))
    return None if box is None else [list(box[0]), list(box[1])]


def unify_of(unify, rows_by_sim, names):
    """unify (as check_unify makes it, or None) for a comparison of FVCOM output with
    output on grids: the grid of the first simulation that is not FVCOM, unless unify
    says another, with the box it is cropped to as "crop"."""
    if unify is None:
        unify = sim_unify.check_unify({"baseline": first_on_a_grid(rows_by_sim, names)}, names)
    if unify["method"] == "box":
        # the FVCOM output is regridded to the box itself: nothing is cropped
        return dict(unify)
    return {**unify, "crop": crop_box(rows_by_sim, names)}


def problems_of(rows_by_sim, names, unify, domain_mask):
    """What stops FVCOM output being compared on a grid, as a list of sentences:
    with other output (unify a box or the baseline, the first that is not FVCOM if
    not given) or, if all the simulations are FVCOM, on a lon-lat box."""
    problems = []
    every = len(names_on_mesh(rows_by_sim, names)) == len(names)
    for name in names_on_mesh(rows_by_sim, names):
        if not all(row.get("fvcom") for row in rows_by_sim[name]):
            problems.append(
                f"{name} has some variables that are raw FVCOM output and some that are not: compare them in "
                "separate comparisons."
            )
        elif not fvcom.has_lon_lat(rows_by_sim[name][0]["files"][0]):
            problems.append(
                f"{name}: the FVCOM mesh is in metres, with no longitudes and latitudes, so it can not be "
                "regridded to the grid of another simulation."
            )
    if unify is not None:
        if every and unify.get("method") != "box":
            problems.append(
                "FVCOM output on its own can only be put on a lon-lat grid, "
                'unify={"lon": [..], "lat": [..], "res": ..}: there is no simulation with a grid to be the baseline.'
            )
        elif unify.get("method") == "baseline" and unify["baseline"] in names_on_mesh(rows_by_sim, names):
            problems.append(
                f"unify's baseline, {unify['baseline']!r}, is FVCOM output, which is regridded to the baseline's "
                "grid: choose a simulation that is not."
            )
    if domain_mask is not None:
        problems.append("domain_mask does not work when FVCOM output is put on a grid.")
    return problems


def _depths_of(row):
    """The depths of the levels of row's variable."""
    ds = nc.open_data(row["files"][0], checks=False)
    ds.subset(variables=row["parts"][0])
    return [float(depth) for depth in ds.levels]


def _needed(row):
    """The files of row that any of its analyses reads, in the order of row["files"]."""
    needed = set(row.get("series_files") or [])
    for kind in KINDS:
        entry = row[kind]
        if entry is None:
            continue
        needed.update(entry.get("files") or [])
        for period in entry.get("periods") or []:
            needed.update(period.get("files") or [])
    return [path for path in row["files"] if path in needed] or list(row["files"])


def _swap_files(row, mapping):
    """Make row read the regridded files, as ordinary output."""
    row["files"] = [mapping[path] for path in row["files"]]
    if row.get("series_files"):
        row["series_files"] = [mapping[path] for path in row["series_files"]]
    for kind in KINDS:
        entry = row[kind]
        if entry is None:
            continue
        if entry.get("files"):
            entry["files"] = [mapping[path] for path in entry["files"]]
        for period in entry.get("periods") or []:
            if period.get("files"):
                period["files"] = [mapping[path] for path in period["files"]]
    row["times"] = {mapping[path]: times for path, times in row["times"].items() if path in mapping}


def _regridded(row, files, folder, depths, grid, box):
    """Regrid each of row's FVCOM files, opened by nctoolkit's open_fvcom with only
    row's parts, into folder (nearest neighbour, so only the cells inside the mesh
    have values): onto the grid of the netCDF file grid, or, if it is None, onto
    the lon-lat box (lon, lat, res). Below the surface, with depths, the sigma
    layers are interpolated to them first (by the thickness of the layers, as
    nctoolkit does); otherwise only the surface is kept. Returns {file: its
    regridded file}."""
    os.makedirs(folder, exist_ok=True)
    mapping = {}
    for number, path in enumerate(files):
        ds = fvcom.open_model(path, row["parts"], fvcom=True, thickness=depths is not None)
        if depths is not None:
            ds.vertical_interp(depths)
        elif row["levels"] > 1:
            surface.finite_missing(ds)
            ds.cdo_command("topvalue")
        if grid is not None:
            ds.regrid(grid, method="nn")
        else:
            lon, lat, res = box
            ds.to_latlon(lon=lon, lat=lat, res=res, method="nn")
        out = os.path.join(folder, f"{number:05d}_{os.path.basename(path)}")
        ds.to_nc(out, zip=True, overwrite=True)
        mapping[path] = out
    return mapping


def regrid_rows(rows_by_sim, sims, unify, folder, layers_of, cores):
    """Regrid the raw FVCOM output of rows_by_sim onto the grid of unify (a baseline
    simulation's, or a lon-lat box), in folder, and make those rows ordinary output
    (row["fvcom"] False, row["fvcom_regridded"] True) of the regridded files, the
    simulation's thickness "z_level". layers_of is sim_compare.layers_of. Regridded
    files are made for the files the analyses read."""
    names = [sim["name"] for sim in sims]
    meshes = names_on_mesh(rows_by_sim, names)
    is_box = unify["method"] == "box"
    box = unify.get("crop")
    for number, first_row in enumerate(rows_by_sim[names[0]]):
        grid = lon_lim = lat_lim = res = None
        if is_box:
            lon_lim, lat_lim, res = unify["lon"], unify["lat"], unify["res"]
            on_grids = [name for name in names if name not in meshes]
            # the levels of the first simulation that is not FVCOM, else matchup's
            reference = rows_by_sim[on_grids[0]][number] if on_grids else first_row
            vertical = reference["levels"] > 1 and any(layer != SURFACE for layer in layers_of(reference))
            if not vertical:
                depths = None
            elif on_grids:
                depths = _depths_of(reference)
            else:
                depths = DEPTHS
        else:
            base_row = rows_by_sim[unify["baseline"]][number]
            vertical = base_row["levels"] > 1 and any(layer != SURFACE for layer in layers_of(base_row))
            _say(f"Making the grid of {base_row['name']} that the FVCOM output is regridded to")
            field = first_step_of(
                base_row["files"], base_row, SURFACE, base_row["levels"], None, at=second_step_of(base_row)
            )
            if box:
                sim_unify.crop_to(field, (box[0], box[1]))
            os.makedirs(folder, exist_ok=True)
            grid = os.path.join(folder, f"{base_row['key']}_grid.nc")
            field.to_nc(grid, zip=False, overwrite=True)
            depths = _depths_of(base_row) if vertical else None
        for name in meshes:
            row = rows_by_sim[name][number]
            target = "the lon-lat grid" if is_box else f"the grid of {unify['baseline']}"
            _say(f"Regridding {row['name']} in the simulation {name} onto {target}")
            files = _needed(row)
            mapping = _regridded(
                row,
                files,
                os.path.join(folder, next(sim["key"] for sim in sims if sim["name"] == name)),
                depths if vertical else None,
                grid,
                (lon_lim, lat_lim, res),
            )
            row["files"] = files
            _swap_files(row, mapping)
            row["fvcom"] = False
            row["fvcom_regridded"] = True
            row["levels"] = len(depths) if vertical else 1
            # CDO reads the first level of the regridded files' vertical axis as any other output's
            row["surface_fast"] = row["surface_small_cache"] = False
    for sim in sims:
        if sim["name"] in meshes:
            sim["thickness"] = Z_LEVEL


def describe(names, unify, on_grids):
    """What was done to the FVCOM output of simulations names, for the report. unify
    is the unify the simulations were put on (a baseline's grid or a box), and
    on_grids whether some simulations are not FVCOM output."""
    if unify["method"] == "box":
        depths = (
            "the depths of the levels of the first simulation that is not FVCOM output"
            if on_grids
            else f"{DEPTHS[0]:g} to {DEPTHS[-1]:g} m every {DEPTHS[1] - DEPTHS[0]:g} m"
        )
        return BOX_TEXT.format(names=", ".join(names), depths=depths)
    return TEXT.format(names=", ".join(names), baseline=unify["baseline"])
