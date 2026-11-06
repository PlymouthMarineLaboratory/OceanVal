"""Whether a simulation is of the northwest European Shelf, worked out from
its domain rather than asked.

The shelf is the "Full Domain" variable (``FullDomain``) of
``data/nwes_subdomains.nc``. A simulation is of the shelf when more than
``SHARE`` of its cells that have values lie inside it; otherwise the domain
is global. As the domain mask does (see oceanval.domain_mask), the work is
nctoolkit's: the shelf is put on the model's own grid (nearest neighbour, no
extrapolation beyond the shelf's grid) and multiplied with one level of one
time step of the model, so the model is never read whole. Raw FVCOM output,
which CDO cannot read, is looked up node by node instead.
"""

import importlib.resources
import warnings

import numpy as np
import xarray as xr

from oceanval import extent, netcdf_lock
from oceanval.quiet_nc import nc

SHARE = 0.8
VARIABLE = "FullDomain"
METHOD = "nn"


def shelf_file():
    """The path of the file of the shelf's sub-domains."""
    return str(importlib.resources.files("oceanval").joinpath("data/nwes_subdomains.nc"))


def _count(path, name=None):
    """How many cells of the field in the netCDF file path have a value."""
    with netcdf_lock.LOCK, xr.open_dataset(path, decode_times=False) as data:
        variable = extent.field_of(data, name)
        if variable is None:
            return 0
        return int(np.isfinite(np.asarray(variable.values, dtype="float64")).sum())


def share_of_grid(path, name=None):
    """The share (0 to 1) of the cells with values of variable name (the
    biggest field if None) of the model file path that lie inside the shelf,
    or None if it can not be told. One level of one time step is used."""
    from oceanval import domain_mask

    chosen = f"name={name}," if name else ""
    for choice in (f"-select,{chosen}levidx=1,timestep=1", f"-select,{chosen}levidx=1"):
        try:
            with warnings.catch_warnings(record=True):
                ds_model = nc.open_data(path, checks=False)
                ds_model.cdo_command(choice)
                ds_model.run()
                model = ds_model.current[0]
                total = _count(model, name)
                if total == 0:
                    continue
                ds_shelf = nc.open_data(shelf_file(), checks=False)
                ds_shelf.subset(variable=VARIABLE)
                with domain_mask._no_extrapolation():
                    ds_shelf.regrid(model, method=METHOD)
                    ds_shelf.run()
                ds_in = nc.open_data(model, checks=False)
                ds_in * ds_shelf
                ds_in.run()
                inside = _count(ds_in.current[0], name)
            return inside / total
        except Exception:
            continue
    return None


def share_of_mesh(path):
    """The share (0 to 1) of the nodes of the raw FVCOM file path that lie
    inside the shelf, or None if it can not be told."""
    try:
        with netcdf_lock.LOCK:
            with xr.open_dataset(path, decode_times=False, drop_variables=["siglay", "siglev"]) as data:
                lon = np.asarray(data["lon"].values, dtype="float64")
                lat = np.asarray(data["lat"].values, dtype="float64")
            with xr.open_dataset(shelf_file(), decode_times=False) as shelf:
                grid_lon = np.asarray(shelf["lon"].values, dtype="float64")
                grid_lat = np.asarray(shelf["lat"].values, dtype="float64")
                wanted = np.asarray(shelf[VARIABLE].values, dtype="float64")
        lon = np.where(lon > 180, lon - 360, lon)
        valid = np.isfinite(lon) & np.isfinite(lat)
        lon, lat = lon[valid], lat[valid]
        if lon.size == 0:
            return None
        # the nearest cell of the shelf's regular grid; beyond its edge is outside
        step_lon, step_lat = np.abs(np.diff(grid_lon)).mean(), np.abs(np.diff(grid_lat)).mean()
        i = np.rint((lat - grid_lat[0]) / (grid_lat[1] - grid_lat[0])).astype(int)
        j = np.rint((lon - grid_lon[0]) / (grid_lon[1] - grid_lon[0])).astype(int)
        within = (
            (i >= 0) & (i < grid_lat.size) & (j >= 0) & (j < grid_lon.size)
            & (np.abs(lat - grid_lat[np.clip(i, 0, grid_lat.size - 1)]) <= step_lat)
            & (np.abs(lon - grid_lon[np.clip(j, 0, grid_lon.size - 1)]) <= step_lon)
        )
        inside = np.zeros(lon.size, dtype=bool)
        cells = wanted[i[within], j[within]]
        inside[within] = np.isfinite(cells) & (cells > 0)
        return float(inside.mean())
    except Exception:
        return None


def detect(files, variable=None, fvcom=False):
    """"nwes" if more than SHARE of the cells with values of the simulation
    (the first of files, and variable, a model variable name, if given) lie
    inside the northwest European Shelf, else "global". Never raises: output
    that can not be read is global."""
    try:
        if not files:
            return "global"
        path = files[0]
        if fvcom:
            share = share_of_mesh(path)
        else:
            # a sum of variables is read by its first
            name = variable.split("+")[0].strip() if variable else None
            share = share_of_grid(path, name)
            if share is None and name:
                share = share_of_grid(path)
        return "nwes" if share is not None and share > SHARE else "global"
    except Exception:
        return "global"
