"""Putting the simulations of compare_simulations() on one grid.

The simulations of a comparison can be on different grids. unify says what to
do about it, and is one of

- None: nothing; each simulation is analysed on its own grid,
- {"lon": [west, east], "lat": [south, north], "res": degrees}: every simulation
  is put on a lon-lat grid of that box and resolution, with nctoolkit's
  to_latlon, or
- {"baseline": name}: the grid of simulation name, which every other
  simulation is regridded to, with nctoolkit's regrid.

Either can also say how the regridding is done, "regrid": one of METHODS
(bilinear, "bil", if not said).

Whichever it is, a cell with no value in one simulation is made missing in all of
them, so that every simulation is compared over exactly the same cells. That
mask is worked out from the second time step of each simulation's field (its first
if it has only one; the first can be empty, see oceanval.analysis.second_step), once
it is on the common grid, for each variable and each layer (surface, bottom,
water column, or every level), in prepare(), and kept in a folder as netCDF.

The analysis reads it through row["unify"] (see row_unify), which
oceanval.analysis.monthly_means_of and daily_climatology_of hand to apply().
With no row["unify"], they are as analyze() has them.
"""

import json
import os
import warnings

from oceanval.quiet_nc import nc
import numpy as np
import xarray as xr

from oceanval import extent

# what a missing cell is written as in the masks
FILL = -9999.0
# nctoolkit's regrid methods, and their names; the first is the default
METHODS = {
    "bil": "Bilinear",
    "nn": "Nearest neighbour",
    "dis": "Distance-weighted",
    "con": "Conservative",
}
DEFAULT_METHOD = "bil"
# what the window says is being done while the simulations are put on one grid (see prepare's progress)
STAGE_WORDS = {"cropping": "Cropping", "regridding": "Regridding", "unifying": "Unifying the simulations"}


def stage_text(stage, simulation=None):
    """The words for a stage of prepare()'s progress, e.g. "Cropping Simulation 2…"."""
    return f"{STAGE_WORDS[stage]}{' ' + str(simulation) if simulation else ''}…"


# how far beyond the target grid, in degrees, a grid is kept when it is cropped before regridding
PAD = 1.0


def check_unify(unify, names):
    """unify (see the module), as the dict it is kept as, or None, or raise a
    ValueError saying what is wrong. names are the names of the simulations."""
    if unify in (None, False):
        return None
    if not isinstance(unify, dict):
        raise ValueError('unify must be None, {"lon": [..], "lat": [..], "res": ..} or {"baseline": name}.')
    # how the simulations are regridded, if said: bilinear if not
    regrid = {}
    if unify.get("regrid") is not None:
        if unify["regrid"] not in METHODS:
            raise ValueError(f"unify's regrid must be one of {', '.join(METHODS)}, not {unify['regrid']!r}.")
        regrid = {"regrid": unify["regrid"]}
    unify = {key: value for key, value in unify.items() if key != "regrid"}
    if "baseline" in unify:
        extra = set(unify) - {"baseline"}
        if extra:
            raise ValueError(f"unify has a baseline, so it can not also have {sorted(extra)}.")
        if unify["baseline"] not in names:
            raise ValueError(
                f"unify's baseline, {unify['baseline']!r}, is not one of the simulations: {', '.join(names)}."
            )
        return {"method": "baseline", "baseline": unify["baseline"], **regrid}
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
    return {"method": "box", "res": res, **box, **regrid}


def describe(unify):
    """What unify (as check_unify makes it) does, as a sentence for the report."""
    if unify is None:
        return "The simulations were not put on a common grid; each was analysed on its own."
    # bilinear, as it was before the method could be chosen, unless said
    interpolation = {"bil": "bilinear interpolation"}.get(
        unify.get("regrid", DEFAULT_METHOD), f"{METHODS[unify.get('regrid', DEFAULT_METHOD)].lower()} regridding"
    )
    if unify["method"] == "baseline":
        how = (
            f"The simulations were put on the grid of {unify['baseline']}: each of the others was regridded "
            f"to it ({interpolation}, nctoolkit's regrid)."
        )
    else:
        how = (
            f"The simulations were put on a lon-lat grid from {unify['lon'][0]:g} to {unify['lon'][1]:g}°E "
            f"and {unify['lat'][0]:g} to {unify['lat'][1]:g}°N, at {unify['res'][0]:g}° by {unify['res'][1]:g}° "
            f"({interpolation}, nctoolkit's to_latlon)."
        )
    return (
        how + " Where any simulation has no value in a cell (judged from its second time step), "
        "that cell is missing in all of them, so every simulation is compared over exactly the same cells."
    )


def mask_path(folder, key, layer):
    return os.path.join(folder, f"{key}_{layer}_mask.nc")


def grid_path(folder, key, layer):
    return os.path.join(folder, f"{key}_{layer}_grid.nc")


def box_path(folder, key, layer):
    return os.path.join(folder, f"{key}_{layer}_box.json")


def padded_box(lon, lat, pad=PAD):
    """The lon-lat box a grid is cropped to before it is regridded to the
    target grid whose cells with values are at longitudes lon and latitudes
    lat (arrays of any shape, see extent.valid_lonlat): the target's extent, pad degrees wider each way, as ([west, east], [south,
    north]) in -180 to 180 and -90 to 90, as nctoolkit's crop takes it. None
    if no crop can be made: the box would go round the globe, or the target
    crosses the dateline."""
    lon = np.asarray(lon, dtype="float64")
    lat = np.asarray(lat, dtype="float64")
    lon, lat = lon[np.isfinite(lon)], lat[np.isfinite(lat)]
    if not lon.size or not lat.size:
        return None
    span = float(lon.max() - lon.min())
    if span + 2 * pad >= 360:
        return None
    lon = (lon + 180) % 360 - 180
    # a grid across the dateline spans most of the globe once in -180 to 180
    if float(lon.max() - lon.min()) > span + 1e-6:
        return None
    return (
        [max(-180.0, float(lon.min()) - pad), min(180.0, float(lon.max()) + pad)],
        [max(-90.0, float(lat.min()) - pad), min(90.0, float(lat.max()) + pad)],
    )


def crop_to(ds, box):
    """Crop ds to box (as padded_box gives it, or None for no crop), so that
    less of it is regridded. If CDO cannot crop it (the box misses the grid, or
    the grid is one CDO cannot box), ds is left as it was: cropping only saves
    time. Changes ds, and returns whether it was cropped."""
    if box is None:
        return False
    try:
        with warnings.catch_warnings(record=True):
            ds.run()
            trial = ds.copy()
            trial.crop(lon=list(box[0]), lat=list(box[1]))
            trial.run()
    except Exception:
        return False
    ds.current = trial.current
    return True


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


# the longitudes and latitudes of an xarray dataset, as arrays, or None if they can not be told
_lonlat = extent.lonlat


def same_lonlat(one, other):
    """Whether two (longitudes, latitudes) pairs, as _lonlat gives them, are the
    same horizontal grid."""
    if one is None or other is None:
        return False
    return all(a.shape == b.shape and np.allclose(a, b, atol=1e-6) for a, b in zip(one, other))


def same_grid(one, other):
    """Whether two xarray datasets are on the same horizontal grid."""
    return same_lonlat(_lonlat(one), _lonlat(other))


def field_variable(data, key):
    """The DataArray of the field in an xarray dataset of one variable's
    layer: key if the dataset has it, else the data variable that is not a
    bounds variable (CDO keeps a file's time_counter_bnds, deptht_bnds, which
    come first) and has the most cells."""
    if key in data:
        return data[key]
    candidates = [
        variable
        for name, variable in data.data_vars.items()
        if variable.ndim >= 2
        and not any(word in name.lower() for word in ("bnds", "bounds"))
        and not any(word in dim.lower() for dim in variable.dims for word in ("bnds", "bounds"))
    ]
    if not candidates:
        raise ValueError("The file's variable could not be told from its bounds variables.")
    return max(candidates, key=lambda variable: variable.size)


def _target_box(unify, key, layer):
    """The box a simulation is cropped to before it is put on the common grid
    (see padded_box): the box's own, or the baseline's, which prepare() keeps."""
    if unify["method"] == "box":
        return padded_box(np.array(unify["lon"]), np.array(unify["lat"]))
    path = box_path(unify["folder"], key, layer)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        box = json.load(f)
    return None if box is None else (box["lon"], box["lat"])


def _regrid(ds, unify, key, layer):
    # bilinear, unless unify says otherwise (Compare simulations' choice)
    method = unify.get("regrid", "bil")
    progress = unify.get("progress") or (lambda stage: None)
    if unify["method"] == "box" or (not unify["is_baseline"] and not unify.get("same_grid")):
        # only the part of the simulation near the target is regridded
        box = _target_box(unify, key, layer)
        if box is not None:
            progress("cropping")
        crop_to(ds, box)
        progress("regridding")
        if unify["method"] == "box":
            ds.to_latlon(lon=unify["lon"], lat=unify["lat"], res=unify["res"], method=method)
        else:
            ds.regrid(grid_path(unify["folder"], key, layer), method=method)
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


def prepare(folder, unify, key, layer, fields, progress=None):
    """Make what the simulations share for one layer of variable key: for a
    baseline, its grid, and for all, the mask. fields is {simulation: dataset}
    of the layer at one time, on each simulation's own grid (they are not
    changed). A mask that is already there is replaced. progress, if given, is
    called as progress(stage, simulation) as it goes: "cropping" and
    "regridding" for each simulation that is, and "unifying" (with no
    simulation) once they are on the grid and the shared cells are found."""
    os.makedirs(folder, exist_ok=True)
    for path in (mask_path(folder, key, layer), grid_path(folder, key, layer), same_path(folder, key, layer),
                 box_path(folder, key, layer)):
        if os.path.exists(path):
            os.remove(path)
    if unify["method"] == "baseline":
        base = fields[unify["baseline"]].copy()
        with warnings.catch_warnings(record=True):
            base.to_nc(grid_path(folder, key, layer), zip=False, overwrite=True)
            # the cells with values, not the whole grid (see oceanval.extent)
            found = extent.valid_lonlat(base.to_xarray(), key)
        # what the others are cropped to before they are regridded to it
        box = None if found is None else padded_box(*found)
        with open(box_path(folder, key, layer), "w") as f:
            json.dump(None if box is None else {"lon": box[0], "lat": box[1]}, f)
    valid = None
    reference = None
    same = {}
    counts = {}
    for sim, field in fields.items():
        ds = field.copy()
        here_unify = row_unify(unify, folder, sim)
        if progress is not None:
            here_unify["progress"] = lambda stage, sim=sim: progress(stage, sim)
        if unify["method"] == "baseline" and sim != unify["baseline"]:
            # on the baseline's grid already: regridding would only spread its missing cells
            with warnings.catch_warnings(record=True):
                here_unify["same_grid"] = same_grid(field.to_xarray(), fields[unify["baseline"]].to_xarray())
            same[sim] = here_unify["same_grid"]
        apply(ds, here_unify, key, layer, masked=False)
        with warnings.catch_warnings(record=True):
            data = ds.to_xarray()
        variable = field_variable(data, key)
        here = np.isfinite(variable.values)
        counts[sim] = int(here.sum())
        if valid is None:
            valid, reference = here, variable
        elif valid.shape != here.shape:
            raise ValueError(
                f"The simulations' {key} ({layer}) do not have the same number of cells once on one grid "
                f"({valid.shape} against {here.shape} in {sim}); do they have different numbers of levels?"
            )
        else:
            valid = valid & here
    if progress is not None:
        progress("unifying", None)
    if not valid.any():
        raise ValueError(
            f"No cell has a value in every simulation for {key} ({layer}), at the time step the cells are judged from "
            f"(the second, or the first if there is only one). Cells with a value: "
            + "; ".join(f"{sim}: {count} of {valid.size}" for sim, count in counts.items())
            + ". Check the missing values (as_missing), which are set before this, and that the output has values then."
        )
    # 0 where every simulation has a value, missing where any has not, so that
    # adding it to a field only makes cells missing
    with open(same_path(folder, key, layer), "w") as f:
        json.dump(same, f)
    mask = xr.DataArray(
        np.where(valid, 0.0, np.nan).astype("f4"), coords=reference.coords, dims=reference.dims, name=key
    )
    mask.to_dataset().to_netcdf(mask_path(folder, key, layer), encoding={key: {"_FillValue": np.float32(FILL)}})
    return int(valid.sum()), int(valid.size)
