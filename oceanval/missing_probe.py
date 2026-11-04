"""Find the value a model uses for missing data, for the Analyze window.

Something that should be a missing value (land, below the sea bed) is the same
in every file: the first time step of the surface of one variable, read from a
number of files, is identical in a good many cells, which real data are not. The
most common such value is offered to the user, who must check it.
"""

import numpy as np
import xarray as xr

from oceanval import netcdf_lock

# the most files looked at
MOST_FILES = 10
# the share of the cells that must hold the same value in every file for it to
# be offered
LEAST_SHARE = 0.01
# the vertical dimensions are found by their names, as analysis._variables_of
_VERTICAL = ("depth", "lev", "sig", "s_rho", "z")
# the horizontal dimension of an unstructured grid: FVCOM's nodes and elements
_UNSTRUCTURED = ("node", "nele")


def _spread(items, most):
    """At most `most` of items, evenly spread, in order."""
    if len(items) <= most:
        return list(items)
    return [items[int(i)] for i in np.linspace(0, len(items) - 1, most).round()]


def _is_time(dim, ds):
    if "time" in dim.lower():
        return True
    return dim in ds.variables and "since" in str(ds[dim].attrs.get("units", ""))


def _surface(variable, ds, step=0):
    """The first time step, at the surface, of variable in the open dataset ds,
    as a 2D array (or a 1D one for an unstructured grid), or None if that
    cannot be told. The surface is the level nearest to 0 of the vertical
    coordinate, or the first level if there is none."""
    index = {}
    for dim in variable.dims:
        if _is_time(dim, ds):
            index[dim] = step
    others = [dim for dim in variable.dims if dim not in index]
    # the last two (one, for an unstructured grid, as FVCOM's node or nele) dimensions are horizontal
    horizontal = 1 if others and others[-1] in _UNSTRUCTURED else 2
    vertical = [dim for dim in others[:-horizontal] if any(word in dim.lower() for word in _VERTICAL)]
    vertical += [dim for dim in others[:-horizontal] if dim not in vertical]
    for dim in vertical:
        if dim in ds.variables and np.issubdtype(ds[dim].dtype, np.number) and ds[dim].ndim == 1:
            index[dim] = int(np.nanargmin(np.abs(ds[dim].values)))
        else:
            index[dim] = 0
    values = variable.isel(index).values
    return values if values.ndim >= 1 else None


def _open(path):
    """The file at path, opened by xarray. FVCOM's siglay and siglev are 2-D
    but named after their dimension, which xarray will not open, so raw FVCOM
    output is opened without them (its first sigma layer is the surface)."""
    try:
        return xr.open_dataset(path, decode_times=False)
    except ValueError:
        return xr.open_dataset(path, decode_times=False, drop_variables=["siglay", "siglev"])


def _slices(files, name, steps):
    """The surface of variable name at the first time step of each of files (or
    at `steps` of the first, if it is the only one) as a list of arrays."""
    out = []
    for path in files:
        # the window probes in a thread of its own, beside others that read files
        with netcdf_lock.LOCK, _open(path) as ds:
            if name not in ds.variables:
                continue
            variable = ds[name]
            time_dims = [dim for dim in variable.dims if _is_time(dim, ds)]
            if len(files) == 1 and time_dims:
                for step in _spread(list(range(variable.sizes[time_dims[0]])), steps):
                    out.append(_surface(variable, ds, step))
            else:
                out.append(_surface(variable, ds))
    return [array for array in out if array is not None]


def probe(files, name, most_files=MOST_FILES, least_share=LEAST_SHARE):
    """The value that is the same, in at least least_share of the cells of the
    surface of variable name, at the first time step of each of up to
    most_files of files (or at as many steps of the one file); the one in most
    cells if several are. As a string, as a box holds it, or None if there is
    none, or it cannot be told. It never raises."""
    try:
        slices = _slices(_spread(sorted(files), most_files), name, most_files)
        if len(slices) < 2:
            return None
        shape = slices[0].shape
        slices = [array for array in slices if array.shape == shape]
        if len(slices) < 2:
            return None
        stack = np.stack(slices).astype("float64" if slices[0].dtype.itemsize > 4 else "float32")
        low, high = np.nanmin(stack, axis=0), np.nanmax(stack, axis=0)
        # cells that are never missing already, and the same everywhere
        same = np.isfinite(stack).all(axis=0) & (low == high)
        if not same.any():
            return None
        values, counts = np.unique(low[same], return_counts=True)
        best = int(np.argmax(counts))
        # in enough of the cells; the one value of nearly all the cells that do not change, not
        # one of many (a field that is periodic, or the same everywhere, has no missing value to tell)
        if counts[best] < least_share * stack[0].size or counts[best] < 0.5 * same.sum():
            return None
        if counts[best] >= stack[0].size:
            return None
        value = values[best]
        return np.format_float_scientific(value, unique=True, trim="-") if abs(value) >= 1e5 or (
            0 < abs(value) < 1e-3
        ) else np.format_float_positional(value, unique=True, trim="-")
    except Exception:
        return None
