"""Masking the matchups to part of the domain, by a netCDF file the user gives:
one variable at one time step, 1 in the cells wanted and 0 or missing in the
others.

matchup(domain_mask=path) checks the file (inspect) before anything is done,
and once the matchups are confirmed writes it, cleaned, into its
oceanval_matchups folder (keep): the one variable, 1 where wanted and missing
elsewhere, with an explicit _FillValue. It is then applied always by nearest
neighbour: gridded matchups put the model's monthly means on the file's grid
(mask_gridded) and multiply them by it, and point matchups keep only the
locations it has a 1 at (mask_points).
"""

import contextlib
import os
import warnings

from oceanval.quiet_nc import nc
import numpy as np
import xarray as xr

from oceanval import extent, sim_unify

# what the mask is called in the oceanval_matchups folder
FILE = "domain_mask.nc"
# the only regrid method: a mask of 1s and 0s has no values in between
METHOD = "nn"
# the missing value of the mask kept
FILL = -9999.0
# the matchups' locations are merged with the mask's by these many decimals of
# lon and lat: nctoolkit writes the points to a text grid for CDO
DECIMALS = 5


def _field(data):
    """The one data variable of an xarray dataset that is neither a bounds
    variable nor a longitude or latitude, and its longitudes and latitudes:
    (variable, (lon, lat)), or an error in words for the user."""
    found = extent._lonlat_variables(data)
    if found is None:
        return "The file has no longitudes and latitudes."
    coordinates = {variable.name for variable in found}
    names = [
        name
        for name, variable in data.data_vars.items()
        if name not in coordinates
        and variable.ndim >= 1
        and not any(word in name.lower() for word in ("bnds", "bounds"))
        and not any(word in dim.lower() for dim in variable.dims for word in ("bnds", "bounds"))
    ]
    if len(names) != 1:
        listed = f": it has {', '.join(names)}" if names else ""
        return f"The file must have a single variable{listed}."
    return data[names[0]], found


def inspect(path):
    """The mask in the netCDF file path, as a 2-D DataArray of 1 (the cells
    wanted) and missing (the rest), with its longitudes and latitudes as
    coordinates: (mask, None), or (None, an error in words for the user) if the
    file is not such a mask: one variable, at most one time step and one level,
    values of 1, 0 or missing and at least one 1."""
    if not os.path.isfile(path):
        return None, "There is no file there."
    try:
        data = xr.open_dataset(path, decode_times=False)
    except Exception:  # noqa: BLE001 - any file xarray cannot read
        return None, "The file could not be read as netCDF."
    with data:
        found = _field(data)
        if isinstance(found, str):
            return None, found
        variable, (lon, lat) = found
        horizontal = set(lon.dims) | set(lat.dims)
        if not horizontal or not horizontal <= set(variable.dims):
            return None, f"{variable.name} is not on the file's longitudes and latitudes."
        others = {dim: variable.sizes[dim] for dim in variable.dims if dim not in horizontal}
        many = [dim for dim, size in others.items() if size > 1]
        if many:
            return None, f"{variable.name} must have a single time step and level: {many[0]} has {others[many[0]]}."
        values = variable.isel({dim: 0 for dim in others}).load()
        lon, lat = lon.load(), lat.load()
    numbers = np.asarray(values.values, dtype="float64")
    finite = numbers[np.isfinite(numbers)]
    if not np.isin(finite, (0, 1)).all():
        return None, f"{variable.name} must be 1 in the cells wanted and 0 or missing elsewhere: it has other values."
    if not (finite == 1).any():
        return None, f"{variable.name} has no cells of 1, so every cell would be masked out."
    mask = xr.where(values == 1, 1.0, np.nan).astype("float32")
    mask = mask.drop_vars([name for name in mask.coords if name not in mask.dims])
    mask = mask.assign_coords({lon.name: lon, lat.name: lat})
    mask.attrs = {"long_name": "Domain mask: 1 in the cells matched up", "units": ""}
    mask.name = variable.name
    return mask, None


def check(domain_mask):
    """matchup's domain_mask, checked: None, or the absolute path of a file
    that inspect finds a mask in."""
    if domain_mask is None:
        return None
    if not isinstance(domain_mask, str):
        raise TypeError("domain_mask must be None or the path of a netCDF file")
    _, error = inspect(domain_mask)
    if error is not None:
        raise ValueError(f"domain_mask, {domain_mask}: {error}")
    return os.path.abspath(domain_mask)


def keep(domain_mask, folder):
    """Write the mask of domain_mask (as check gives it) into folder as FILE:
    1 where wanted, missing elsewhere, with an explicit _FillValue, as CDO
    needs to carry missing values through arithmetic. Returns what
    session_info keeps: {"path": the file, "method"}, or None for no
    domain_mask."""
    if domain_mask is None:
        return None
    mask, error = inspect(domain_mask)
    if error is not None:
        raise ValueError(f"domain_mask, {domain_mask}: {error}")
    kept = os.path.join(folder, FILE)
    encoding = {mask.name: {"_FillValue": np.float32(FILL)}}
    encoding.update({name: {"_FillValue": None} for name in mask.coords})
    written = kept + ".part"
    mask.to_dataset().to_netcdf(written, encoding=encoding)
    os.replace(written, kept)
    return {"path": kept, "method": METHOD}


def cells(mask):
    """How many cells of mask (as inspect gives it) are wanted, of how many."""
    return int(mask.notnull().sum()), int(mask.size)


def _lonlat_of(path):
    """The longitudes and latitudes of a netCDF file (see sim_unify._lonlat):
    only the coordinates are read."""
    with xr.open_dataset(path, decode_times=False) as data:
        return sim_unify._lonlat(data)


def mask_gridded(ds, info, fvcom=False):
    """Multiply ds, an nctoolkit dataset of the model's monthly means, by the
    domain mask info (session_info["domain_mask"]): ds is put on the mask's
    grid by info's method, unless it is on it already (regridding would only
    spread its missing cells). Before it is regridded it is cropped to the
    mask's extent (see sim_unify.padded_box), unless it is FVCOM output
    (fvcom), which is left as it is. Changes ds."""
    if info is None:
        return ds
    with warnings.catch_warnings(record=True):
        ds.run()
        if not sim_unify.same_lonlat(_lonlat_of(ds[0]), _lonlat_of(info["path"])):
            # the extent of the mask's cells with values, not of its whole grid
            with xr.open_dataset(info["path"], decode_times=False) as data:
                target = extent.valid_lonlat(data)
            if not fvcom and target is not None:
                sim_unify.crop_to(ds, sim_unify.padded_box(*target))
            ds.regrid(info["path"], method=info["method"])
        ds * nc.open_data(info["path"], checks=False)
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


KEYS = ["_mask_lon", "_mask_lat"]


def _keyed(df):
    return df.assign(**{KEYS[0]: df.lon.round(DECIMALS), KEYS[1]: df.lat.round(DECIMALS)})


def mask_points(df, info):
    """The rows of df, point matchups with lon and lat columns, whose location
    has a value in the domain mask info (session_info["domain_mask"]): the
    mask is put on the locations by nearest neighbour (a set of points has no
    cell bounds, and bilinear would lose a point if any of the four cells
    around it is missing), and those it leaves missing, or that are beyond its
    grid, are dropped."""
    if info is None or len(df) == 0:
        return df
    places = df[["lon", "lat"]].drop_duplicates().reset_index(drop=True)
    with warnings.catch_warnings(record=True), _no_extrapolation():
        ds_mask = nc.open_data(info["path"], checks=False)
        ds_mask.regrid(places, method=METHOD)
        ds_mask.run()
        found = ds_mask.to_dataframe().reset_index()
    name = ds_mask.variables[0]
    valid = _keyed(found.dropna(subset=[name]))[KEYS].drop_duplicates()
    return _keyed(df).merge(valid, on=KEYS).drop(columns=KEYS)[list(df.columns)]
