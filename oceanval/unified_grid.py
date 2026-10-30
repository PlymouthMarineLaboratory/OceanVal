"""Masking the matchups by a unified grid: the cells that have a value in this
simulation and in others it is to be compared with (see oceanval.grid_align,
which the oceanval window's step for aligning the grid uses to make one).

matchup(unified_grid={"path": mask file, "method": regrid method}) keeps the
file in its oceanval_matchups folder once the matchups are confirmed, and says
so in session_info["unified_grid"]. The file is a mask as
oceanval.sim_unify.prepare writes it: 0 where every simulation has a value,
missing elsewhere, with an explicit _FillValue, so adding it to a field only
makes cells missing.

- Gridded matchups: the model's monthly means are put on the mask's grid with
  the method (unless they are on it already) and the mask is added to them.
- Point matchups: the mask is put on the matchups' locations by nearest
  neighbour, and only the locations with a value are kept.
"""

import contextlib
import os
import shutil
import warnings

import nctoolkit as nc
import xarray as xr

from oceanval import extent, sim_unify

# what the mask is called in the oceanval_matchups folder
FILE = "unified_grid.nc"
# nctoolkit's regrid methods, and their names
METHODS = sim_unify.METHODS
# a set of points has no cell bounds, and bilinear would lose a point if any of
# the four cells around it is missing
POINT_METHOD = "nn"
# the matchups' locations are merged with the mask's by these many decimals of
# lon and lat: nctoolkit writes the points to a text grid for CDO
DECIMALS = 5


def check(unified_grid):
    """matchup's unified_grid, checked: None, or {"path", "method"} with the
    path a file that exists and the method one of METHODS."""
    if unified_grid is None:
        return None
    if not isinstance(unified_grid, dict) or set(unified_grid) != {"path", "method"}:
        raise TypeError('unified_grid must be None or {"path": mask file, "method": regrid method}')
    path, method = unified_grid["path"], unified_grid["method"]
    if not isinstance(path, str) or not os.path.isfile(path):
        raise ValueError(f"unified_grid's path, {path!r}, is not a file")
    if method not in METHODS:
        raise ValueError(f"unified_grid's method must be one of {', '.join(METHODS)}, not {method!r}")
    return {"path": os.path.abspath(path), "method": method}


def keep(unified_grid, folder):
    """Copy the mask of unified_grid (as check gives it) into folder as FILE,
    unless it is that file already. Returns what session_info keeps:
    {"path": the copy, "method"}, or None for no unified_grid."""
    if unified_grid is None:
        return None
    kept = os.path.join(folder, FILE)
    if not (os.path.exists(kept) and os.path.samefile(unified_grid["path"], kept)):
        shutil.copyfile(unified_grid["path"], kept)
    return {"path": kept, "method": unified_grid["method"]}


def _lonlat_of(path):
    """The longitudes and latitudes of a netCDF file (see sim_unify._lonlat):
    only the coordinates are read."""
    with xr.open_dataset(path, decode_times=False) as data:
        return sim_unify._lonlat(data)


def mask_gridded(ds, info, fvcom=False):
    """Mask ds, an nctoolkit dataset of the model's monthly means, by the
    unified grid info (session_info["unified_grid"]): ds is put on the mask's
    grid by info's method, unless it is on it already (regridding would only
    spread its missing cells), and the mask is added. Before it is regridded
    it is cropped to the mask's extent (see sim_unify.padded_box), unless it is
    FVCOM output (fvcom), which is left as it is. Changes ds."""
    if info is None:
        return ds
    with warnings.catch_warnings(record=True):
        ds.run()
        if not sim_unify.same_lonlat(_lonlat_of(ds[0]), _lonlat_of(info["path"])):
            # the extent of the mask's cells with values (0), not of its whole grid
            with xr.open_dataset(info["path"], decode_times=False) as data:
                target = extent.valid_lonlat(data)
            if not fvcom and target is not None:
                sim_unify.crop_to(ds, sim_unify.padded_box(*target))
            ds.regrid(info["path"], method=info["method"])
        ds + nc.open_data(info["path"], checks=False)
        ds.run()
    return ds


@contextlib.contextmanager
def _no_extrapolation():
    """CDO does not give a point beyond the mask's grid the nearest cell's
    value while this lasts."""
    before = os.environ.get("REMAP_EXTRAPOLATE")
    os.environ["REMAP_EXTRAPOLATE"] = "off"
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("REMAP_EXTRAPOLATE", None)
        else:
            os.environ["REMAP_EXTRAPOLATE"] = before


KEYS = ["_unified_lon", "_unified_lat"]


def _keyed(df):
    return df.assign(**{KEYS[0]: df.lon.round(DECIMALS), KEYS[1]: df.lat.round(DECIMALS)})


def mask_points(df, info):
    """The rows of df, point matchups with lon and lat columns, whose location
    has a value in the unified grid info (session_info["unified_grid"]): the
    mask is put on the locations by nearest neighbour, and those it leaves
    missing, or that are beyond its grid, are dropped."""
    if info is None or len(df) == 0:
        return df
    places = df[["lon", "lat"]].drop_duplicates().reset_index(drop=True)
    with warnings.catch_warnings(record=True), _no_extrapolation():
        ds_mask = nc.open_data(info["path"], checks=False)
        ds_mask.regrid(places, method=POINT_METHOD)
        ds_mask.run()
        found = ds_mask.to_dataframe().reset_index()
    name = ds_mask.variables[0]
    valid = _keyed(found.dropna(subset=[name]))[KEYS].drop_duplicates()
    return _keyed(df).merge(valid, on=KEYS).drop(columns=KEYS)[list(df.columns)]
