"""Masking the matchups to part of the domain, by a netCDF file the user gives:
one variable at one time step, 1 in the cells wanted and 0 or missing in the
others.

matchup(domain_mask=path) checks the file (inspect) before anything is done,
and once the matchups are confirmed writes it, cleaned, into its
oceanval_matchups folder (keep): the one variable, 1 where wanted and missing
elsewhere, with an explicit _FillValue. It is then applied as the unified grid
is (see oceanval.unified_grid), always by nearest neighbour: gridded matchups
put the model's monthly means on the file's grid and multiply them by it, and
point matchups keep only the locations it has a 1 at.
"""

import os

import numpy as np
import xarray as xr

from oceanval import extent

# what the mask is called in the oceanval_matchups folder
FILE = "domain_mask.nc"
# the only regrid method: a mask of 1s and 0s has no values in between
METHOD = "nn"
# the missing value of the mask kept
FILL = -9999.0


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
    session_info keeps: {"path": the file, "method", "multiply"} (see
    oceanval.unified_grid.mask_gridded), or None for no domain_mask."""
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
    return {"path": kept, "method": METHOD, "multiply": True}


def cells(mask):
    """How many cells of mask (as inspect gives it) are wanted, of how many."""
    return int(mask.notnull().sum()), int(mask.size)
