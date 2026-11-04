"""Raw FVCOM output compared with output on other grids, in compare_simulations().

Raw FVCOM output is on a mesh of triangles, which CDO cannot read, so it is only
ever compared on its own mesh with other FVCOM output (see oceanval.fvcom_climatology).
When some simulations are FVCOM and some are not, the FVCOM output is regridded
(nearest neighbour, as matchup regrids it) onto the grid of the first simulation
that is not, cropped to the extent of the mesh, and then it is ordinary output on
a grid: the simulations are put on that grid as unify={"baseline": name} does.

A variable that needs more than the surface is interpolated from the sigma
layers to the depths of the baseline's levels, so that every simulation has the
same levels, and its water column is worked out as thickness="z_level" is.
"""

import os

import numpy as np

from oceanval import fvcom, fvcom_climatology, sim_unify
from oceanval.analysis import KINDS, SURFACE, Z_LEVEL, _say, first_step_of, second_step_of
from oceanval.quiet_nc import nc

TEXT = (
    "The output of {names} is on a mesh of triangles, so it was regridded (nearest neighbour) onto the grid of "
    "{baseline}, cropped to the extent of the mesh, where only the cells inside the mesh have values. Variables "
    "below the surface were interpolated from the model's sigma layers to the depths of the levels of {baseline}, "
    "with the top layer's value above the top layer, the bottom layer's between it and the seabed and nothing "
    "below the seabed, so the bottom is the deepest of those levels that is above the seabed."
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
    return {**unify, "crop": crop_box(rows_by_sim, names)}


def problems_of(rows_by_sim, names, unify, domain_mask):
    """What stops FVCOM output being compared with other output, as a list of sentences."""
    problems = []
    for name in names_on_mesh(rows_by_sim, names):
        if not all(row.get("fvcom") for row in rows_by_sim[name]):
            problems.append(
                f"{name} has some variables that are raw FVCOM output and some that are not: compare them in "
                "separate comparisons."
            )
        elif not fvcom_climatology.has_lon_lat(rows_by_sim[name][0]["files"][0]):
            problems.append(
                f"{name}: the FVCOM mesh is in metres, with no longitudes and latitudes, so it can not be "
                "regridded to the grid of another simulation."
            )
    if unify is not None:
        if unify.get("method") != "baseline":
            problems.append(
                "FVCOM output is compared with other output on the grid of one of the simulations that is not "
                'FVCOM: unify can only be {"baseline": name}, or left out.'
            )
        elif unify["baseline"] in names_on_mesh(rows_by_sim, names):
            problems.append(
                f"unify's baseline, {unify['baseline']!r}, is FVCOM output, which is regridded to the baseline's "
                "grid: choose a simulation that is not."
            )
    if domain_mask is not None:
        problems.append("domain_mask does not work when FVCOM output is compared with other output.")
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


def regrid_rows(rows_by_sim, sims, unify, folder, layers_of, cores):
    """Regrid the raw FVCOM output of rows_by_sim onto the grid of the baseline, in
    folder, and make those rows ordinary output (row["fvcom"] False,
    row["fvcom_regridded"] True) of the regridded files, the simulation's thickness
    "z_level". layers_of is sim_compare.layers_of. Regridded files are made for the
    files the analyses read."""
    names = [sim["name"] for sim in sims]
    baseline = unify["baseline"]
    box = unify.get("crop")
    meshes = names_on_mesh(rows_by_sim, names)
    for number, base_row in enumerate(rows_by_sim[baseline]):
        layers = layers_of(base_row)
        vertical = base_row["levels"] > 1 and any(layer != SURFACE for layer in layers)
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
            _say(f"Regridding {row['name']} in the simulation {name} onto the grid of {baseline}")
            files = _needed(row)
            mapping = fvcom.fvcom_matchup_files(
                files,
                list(row["parts"]),
                os.path.join(folder, next(sim["key"] for sim in sims if sim["name"] == name)),
                vertical=vertical,
                res=fvcom.default_res,
                lon_lim=None,
                lat_lim=None,
                cores=cores,
                levels=depths,
                grid=grid,
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


def describe(names, baseline):
    """What was done to the FVCOM output of simulations names, for the report."""
    return TEXT.format(names=", ".join(names), baseline=baseline)
