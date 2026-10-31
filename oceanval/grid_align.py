"""Putting simulations on one grid, which the oceanval window's Compare
simulations grids step uses to show the unified grid (see
oceanval.sim_compare_app).

Each simulation is given by one of its files and a variable in it: the variable's
surface (the level the viewer starts at) at the file's first time, with the
user's missing values made missing, is its field. unified() puts every field on
the grid of one of them, the baseline, as oceanval.sim_unify does for
compare_simulations(), and gives the cells that have a value in every
simulation as 1 and the rest as missing.

The files it writes are in a folder of its own, removed once the answer is
read.
"""

import tempfile
import warnings

from oceanval.quiet_nc import nc
import numpy as np
import xarray as xr

from oceanval import netcdf_lock, sim_unify, viewer_catalogue

# nctoolkit's regrid methods the window offers, and their names
REGRID_METHODS = sim_unify.METHODS
# what unified() calls the field it makes
NAME = "grid"


def field_for_cdo(path, variable, level=0, missing=(), step=0):
    """The nctoolkit dataset of variable at level (counted from 0, as the
    viewer counts) of time step step (from 0) of the file at path, with each
    (low, high) range of missing made missing. A model file can be many GB,
    so CDO's select reads only what it chooses (see sim_compare.grid_of).
    Raises ValueError, in words for the user, if it cannot be read."""
    tried = (
        f"-select,name={variable},levidx={int(level) + 1},timestep={int(step) + 1}",
        # a variable with no levels, or no times
        f"-select,name={variable},timestep={int(step) + 1}",
        f"-select,name={variable}",
    )
    for choice in tried:
        try:
            with warnings.catch_warnings(record=True):
                ds = nc.open_data(path, checks=False)
                ds.cdo_command(choice)
                ds.run()
            break
        except Exception:
            continue
    else:
        raise ValueError(f"{variable} could not be read from {path} by CDO.")
    with warnings.catch_warnings(record=True):
        for low, high in missing:
            ds.as_missing([low, high])
        ds.run()
    return ds


def unified(fields, baseline, method, box=None, progress=None):
    """The cells that have a value in every simulation, on the grid of
    baseline (or, if box is given, {"lon": [west, east], "lat": [south,
    north], "res": [lon, lat]} as sim_unify.check_unify makes it, on a lon-lat
    grid of that, and baseline is not used): fields is {name: dataset} (see
    field_for_cdo), each on its simulation's own grid, and method one of
    REGRID_METHODS. Returns (a loaded DataArray, 1 where every
    simulation has a value and NaN where any has not, with its longitudes and
    latitudes as coords, how many cells are 1, how many there are). Raises
    ValueError, in words for the user, if they cannot be put on one grid. progress, if given, is called as
    progress(stage, name) as it goes (see sim_unify.prepare)."""
    if box is None and baseline not in fields:
        raise ValueError("Choose the baseline from the simulations drawn.")
    if method not in REGRID_METHODS:
        raise ValueError("Choose a regridding method.")
    unify = {"method": "baseline", "baseline": baseline, "regrid": method} if box is None else {**box, "regrid": method}
    with tempfile.TemporaryDirectory(prefix="oceanval-align-") as folder:
        try:
            # nctoolkit's to_xarray opens files in this process (see oceanval.netcdf_lock)
            with netcdf_lock.LOCK:
                shared, total = sim_unify.prepare(folder, unify, NAME, "surface", fields, progress=progress)
                with xr.open_dataset(sim_unify.mask_path(folder, NAME, "surface")) as data:
                    mask = data[NAME].load()
        except ValueError:
            raise
        except Exception as error:  # noqa: BLE001 - CDO's errors, said to the user
            raise ValueError(
                f"The grids could not be aligned with {REGRID_METHODS[method].lower()} regridding: {error}"
            ) from None
    # the mask is 0 where every simulation has a value
    return xr.where(np.isfinite(mask), 1.0, np.nan).assign_attrs(units=""), shared, total


def about(data):
    """How the viewer draws data, a DataArray unified() gives (see
    viewer_plots.map_plot): its horizontal dims and longitude/latitude."""
    found = viewer_catalogue.describe_variables(data.to_dataset(name=NAME), None)
    return found[NAME]
