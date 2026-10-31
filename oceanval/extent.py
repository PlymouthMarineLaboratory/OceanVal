"""The extent of a model's field: the longitudes and latitudes of the cells
that have values, never those of the whole grid.

A model's grid can have cells far from its domain. NEMO puts the cells of the
land processors it leaves out at lon -1, lat -1 (36 thousand of AMM7's 111
thousand), so the minimum and maximum of a whole grid of the north-west
European shelf run from latitude -1 to 65. The cells of those processors have
no values, so the extent of the cells with values is the model's domain.
"""

import warnings

from oceanval.quiet_nc import nc
import numpy as np
import xarray as xr

from oceanval import netcdf_lock


def _lonlat_variables(data):
    """The longitude and latitude DataArrays of an xarray dataset, or None if
    they can not be told."""
    found = {}
    for name in list(data.coords) + list(data.data_vars):
        variable = data[name]
        text = " ".join(str(variable.attrs.get(a, "")) for a in ("standard_name", "units", "axis")).lower()
        low = name.lower()
        if "lon" not in found and ("degrees_east" in text or "longitude" in text or low in ("lon", "nav_lon")):
            found["lon"] = variable
        elif "lat" not in found and ("degrees_north" in text or "latitude" in text or low in ("lat", "nav_lat")):
            found["lat"] = variable
    return (found["lon"], found["lat"]) if len(found) == 2 else None


def lonlat(data):
    """The longitudes and latitudes of an xarray dataset's grid, as arrays, or
    None if they can not be told."""
    found = _lonlat_variables(data)
    if found is None:
        return None
    return tuple(np.asarray(variable.values, dtype="float64") for variable in found)


def field_of(data, name=None):
    """The DataArray of the field in an xarray dataset: name if it has it, else
    the data variable that is not a bounds variable and has the most cells, or
    None if there is none."""
    if name is not None and name in data.data_vars:
        return data[name]
    candidates = [
        variable
        for key, variable in data.data_vars.items()
        if variable.ndim >= 1
        and not any(word in key.lower() for word in ("bnds", "bounds"))
        and not any(word in dim.lower() for dim in variable.dims for word in ("bnds", "bounds"))
    ]
    return max(candidates, key=lambda variable: variable.size) if candidates else None


def valid_lonlat(data, name=None):
    """The longitudes and latitudes (flat arrays) of the cells of an xarray
    dataset's field (name, or see field_of) that have a value at its first time
    step and top level: no cell deeper or later has a value the surface lacks,
    and only that slice is read. None if they can not be told: no coordinates,
    no field, coordinates that do not fit the field, or no cell with a value."""
    found = _lonlat_variables(data)
    variable = field_of(data, name)
    if found is None or variable is None:
        return None
    lon, lat = found
    horizontal = set(lon.dims) | set(lat.dims)
    if not horizontal or not horizontal <= set(variable.dims):
        return None
    # the first of every other dimension: the first time, the top level
    first = variable.isel({dim: 0 for dim in variable.dims if dim not in horizontal})
    try:
        lon = lon.broadcast_like(first).transpose(*first.dims)
        lat = lat.broadcast_like(first).transpose(*first.dims)
    except ValueError:
        return None
    values = np.asarray(first.values, dtype="float64")
    lon, lat = np.asarray(lon.values, dtype="float64"), np.asarray(lat.values, dtype="float64")
    if not values.shape == lon.shape == lat.shape:
        return None
    valid = np.isfinite(values) & np.isfinite(lon) & np.isfinite(lat)
    if not valid.any():
        return None
    return lon[valid], lat[valid]


def first_slice(path, name=None, as_missing=None):
    """One level of one time step of variable name (every variable, if None)
    of the model file path, loaded, with as_missing (as matchup takes it) made
    missing, or None if it can not be read. A model file can be many GB, so CDO
    writes that slice to a small file first, and only that is opened: select
    reads only what it chooses."""
    chosen = f"name={name}," if name else ""
    # a variable with no times has no first time step to choose
    for choice in (f"-select,{chosen}levidx=1,timestep=1", f"-select,{chosen}levidx=1"):
        try:
            with warnings.catch_warnings(record=True):
                ds = nc.open_data(path, checks=False)
                ds.cdo_command(choice)
                if as_missing is not None:
                    ds.as_missing(as_missing)
                ds.run()
            # the window reads files in threads of its own
            with netcdf_lock.LOCK, xr.open_dataset(ds.current[0], decode_times=False) as data:
                return data.load()
        except Exception:
            continue
    return None


def of_file(path, name=None, as_missing=None):
    """valid_lonlat of variable name in the model file path (see first_slice),
    or None."""
    data = first_slice(path, name, as_missing)
    return None if data is None else valid_lonlat(data, name)
