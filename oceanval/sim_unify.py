"""Putting the simulations of compare_simulations() on one grid.

The simulations of a comparison can be on different grids. unify says what to
do about it, and is one of

- None: nothing; each simulation is analysed on its own grid,
- {"lon": [west, east], "lat": [south, north], "res": degrees}: every simulation
  is put on a lon-lat grid of that box and resolution, with nctoolkit's
  to_latlon, or
- {"baseline": name}: the grid of simulation name, which every other
  simulation is regridded to, with nctoolkit's regrid.

Whichever it is, a cell with no value in one simulation is made missing in all of
them, so that every simulation is compared over exactly the same cells. That
mask is worked out from the first time step of each simulation's field (once
it is on the common grid), for each variable and each layer (surface, bottom,
water column, or every level), in prepare(), and kept in a folder as netCDF.

The analysis reads it through row["unify"] (see row_unify), which
oceanval.analysis.monthly_means_of and daily_climatology_of hand to apply().
With no row["unify"], they are as analyze() has them.
"""

import json
import os
import warnings

import nctoolkit as nc
import numpy as np
import xarray as xr

# what a missing cell is written as in the masks
FILL = -9999.0


def check_unify(unify, names):
    """unify (see the module), as the dict it is kept as, or None, or raise a
    ValueError saying what is wrong. names are the names of the simulations."""
    if unify in (None, False):
        return None
    if not isinstance(unify, dict):
        raise ValueError('unify must be None, {"lon": [..], "lat": [..], "res": ..} or {"baseline": name}.')
    if "baseline" in unify:
        extra = set(unify) - {"baseline"}
        if extra:
            raise ValueError(f"unify has a baseline, so it can not also have {sorted(extra)}.")
        if unify["baseline"] not in names:
            raise ValueError(
                f"unify's baseline, {unify['baseline']!r}, is not one of the simulations: {', '.join(names)}."
            )
        return {"method": "baseline", "baseline": unify["baseline"]}
    missing = [key for key in ("lon", "lat", "res") if key not in unify]
    extra = set(unify) - {"lon", "lat", "res"}
    if missing or extra:
        raise ValueError('unify needs either "baseline", or "lon", "lat" and "res" (and nothing else).')
    box = {}
    for key in ("lon", "lat"):
        try:
            low, high = (float(value) for value in unify[key])
        except (TypeError, ValueError):
            raise ValueError(f"unify's {key} must be two numbers: [minimum, maximum].") from None
        if not low < high:
            raise ValueError(f"unify's {key} must have its minimum below its maximum.")
        if key == "lat" and (low < -90 or high > 90):
            raise ValueError("unify's lat must be within -90 and 90.")
        box[key] = [low, high]
    res = unify["res"]
    if isinstance(res, (int, float)) and not isinstance(res, bool):
        res = [float(res), float(res)]
    try:
        res = [float(value) for value in res]
    except (TypeError, ValueError):
        res = []
    if len(res) != 2 or min(res) <= 0:
        raise ValueError("unify's res must be a number above 0 (degrees), or [lon resolution, lat resolution].")
    if res[0] > box["lon"][1] - box["lon"][0] or res[1] > box["lat"][1] - box["lat"][0]:
        raise ValueError("unify's res is larger than its box.")
    return {"method": "box", "res": res, **box}


def describe(unify):
    """What unify (as check_unify makes it) does, as a sentence for the report."""
    if unify is None:
        return "The simulations were not put on a common grid; each was analysed on its own."
    if unify["method"] == "baseline":
        how = (
            f"The simulations were put on the grid of {unify['baseline']}: each of the others was regridded "
            "to it (bilinear interpolation, nctoolkit's regrid)."
        )
    else:
        how = (
            f"The simulations were put on a lon-lat grid from {unify['lon'][0]:g} to {unify['lon'][1]:g}°E "
            f"and {unify['lat'][0]:g} to {unify['lat'][1]:g}°N, at {unify['res'][0]:g}° by {unify['res'][1]:g}° "
            "(bilinear interpolation, nctoolkit's to_latlon)."
        )
    return (
        how + " Where any simulation has no value in a cell (judged from its first time step), "
        "that cell is missing in all of them, so every simulation is compared over exactly the same cells."
    )


def mask_path(folder, key, layer):
    return os.path.join(folder, f"{key}_{layer}_mask.nc")


def grid_path(folder, key, layer):
    return os.path.join(folder, f"{key}_{layer}_grid.nc")


def row_unify(unify, folder, sim):
    """What goes in row["unify"] of simulation sim's variables: unify (as
    check_unify makes it), where prepare() keeps its masks, and whether sim is
    the baseline (which is not regridded). None for no unify."""
    if unify is None:
        return None
    return {
        **unify,
        "folder": folder,
        "sim": sim,
        "is_baseline": unify["method"] == "baseline" and unify["baseline"] == sim,
    }


def same_path(folder, key, layer):
    return os.path.join(folder, f"{key}_{layer}_same.json")


def _lonlat(data):
    """The longitudes and latitudes of an xarray dataset, as arrays, or None
    if they can not be told."""
    found = {}
    for name in list(data.coords) + list(data.data_vars):
        variable = data[name]
        text = " ".join(str(variable.attrs.get(a, "")) for a in ("standard_name", "units", "axis")).lower()
        low = name.lower()
        if "lon" not in found and ("degrees_east" in text or "longitude" in text or low in ("lon", "nav_lon")):
            found["lon"] = np.asarray(variable.values, dtype="float64")
        elif "lat" not in found and ("degrees_north" in text or "latitude" in text or low in ("lat", "nav_lat")):
            found["lat"] = np.asarray(variable.values, dtype="float64")
    return (found["lon"], found["lat"]) if len(found) == 2 else None


def same_lonlat(one, other):
    """Whether two (longitudes, latitudes) pairs, as _lonlat gives them, are the
    same horizontal grid."""
    if one is None or other is None:
        return False
    return all(a.shape == b.shape and np.allclose(a, b, atol=1e-6) for a, b in zip(one, other))


def same_grid(one, other):
    """Whether two xarray datasets are on the same horizontal grid."""
    return same_lonlat(_lonlat(one), _lonlat(other))


def _regrid(ds, unify, key, layer):
    if unify["method"] == "box":
        ds.to_latlon(lon=unify["lon"], lat=unify["lat"], res=unify["res"], method="bil")
    elif not unify["is_baseline"] and not unify.get("same_grid"):
        ds.regrid(grid_path(unify["folder"], key, layer), method="bil")
    ds.run()


def apply(ds, unify, key, layer, masked=True):
    """ds, a dataset of the layer of variable key, on the common grid and, once
    prepare() has made its mask, with the cells missing in any simulation
    missing. It changes ds, and returns it."""
    if unify is None:
        return ds
    with warnings.catch_warnings(record=True):
        if masked and "same_grid" not in unify and os.path.exists(same_path(unify["folder"], key, layer)):
            with open(same_path(unify["folder"], key, layer)) as f:
                unify = {**unify, "same_grid": json.load(f).get(unify.get("sim"), False)}
        _regrid(ds, unify, key, layer)
        mask = mask_path(unify["folder"], key, layer)
        if masked and os.path.exists(mask):
            ds + nc.open_data(mask, checks=False)
            ds.run()
    return ds


def prepare(folder, unify, key, layer, fields):
    """Make what the simulations share for one layer of variable key: for a
    baseline, its grid, and for all, the mask. fields is {simulation: dataset}
    of the layer at one time, on each simulation's own grid (they are not
    changed). A mask that is already there is replaced."""
    os.makedirs(folder, exist_ok=True)
    for path in (mask_path(folder, key, layer), grid_path(folder, key, layer), same_path(folder, key, layer)):
        if os.path.exists(path):
            os.remove(path)
    if unify["method"] == "baseline":
        base = fields[unify["baseline"]].copy()
        with warnings.catch_warnings(record=True):
            base.to_nc(grid_path(folder, key, layer), zip=False, overwrite=True)
    valid = None
    reference = None
    same = {}
    for sim, field in fields.items():
        ds = field.copy()
        here_unify = row_unify(unify, folder, sim)
        if unify["method"] == "baseline" and sim != unify["baseline"]:
            # on the baseline's grid already: regridding would only spread its missing cells
            with warnings.catch_warnings(record=True):
                here_unify["same_grid"] = same_grid(field.to_xarray(), fields[unify["baseline"]].to_xarray())
            same[sim] = here_unify["same_grid"]
        apply(ds, here_unify, key, layer, masked=False)
        with warnings.catch_warnings(record=True):
            data = ds.to_xarray()
        variable = data[key] if key in data else data[list(data.data_vars)[0]]
        here = np.isfinite(variable.values)
        if valid is None:
            valid, reference = here, variable
        elif valid.shape != here.shape:
            raise ValueError(
                f"The simulations' {key} ({layer}) do not have the same number of cells once on one grid "
                f"({valid.shape} against {here.shape} in {sim}); do they have different numbers of levels?"
            )
        else:
            valid = valid & here
    # 0 where every simulation has a value, missing where any has not, so that
    # adding it to a field only makes cells missing
    with open(same_path(folder, key, layer), "w") as f:
        json.dump(same, f)
    mask = xr.DataArray(
        np.where(valid, 0.0, np.nan).astype("f4"), coords=reference.coords, dims=reference.dims, name=key
    )
    mask.to_dataset().to_netcdf(mask_path(folder, key, layer), encoding={key: {"_FillValue": np.float32(FILL)}})
    return int(valid.sum()), int(valid.size)
