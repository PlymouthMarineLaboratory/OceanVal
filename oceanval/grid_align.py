"""Aligning a simulation's grid with other simulations', which the oceanval
window's validation can be asked to look at (see oceanval.grid_align_app).

A simulation validated now may be compared with others later, on other grids.
Each simulation is given by one of its files and a variable in it: the variable's
surface (the level the viewer starts at) at the file's first time, with the
user's missing values made missing, is its field. unified() puts every field on
the grid of one of them, the baseline, as oceanval.sim_unify does for
compare_simulations(), and gives the cells that have a value in every
simulation as 1 and the rest as missing.

The files it writes are in a folder of its own, removed once the answer is
read, but for the mask, which it copies where it is asked to: matchup masks
the matchups by it (see oceanval.unified_grid).
"""

import os
import shutil
import tempfile
import warnings

import nctoolkit as nc
import numpy as np
import pandas as pd
import xarray as xr

from oceanval import netcdf_lock, sim_unify, viewer_catalogue
from oceanval.unified_grid import METHODS

# nctoolkit's regrid methods the window offers, and their names
REGRID_METHODS = METHODS
# what unified() calls the field it makes
NAME = "grid"
# FVCOM's nodes are points of no grid CDO knows, so they can only be put on another grid by taking
# the nearest node (as fvcom.fvcom_regrid does for the matchups)
FVCOM_METHOD = "nn"
NN_ONLY_NOTE = "FVCOM output can only be put on another grid by nearest neighbour, so that is the method used."


def field_for_cdo(path, variable, level=0, missing=()):
    """The nctoolkit dataset of variable at level (counted from 0, as the
    viewer counts) of the first time step of the file at path, with each
    (low, high) range of missing made missing. A model file can be many GB,
    so CDO's select reads only what it chooses (see sim_compare.grid_of).
    Raises ValueError, in words for the user, if it cannot be read."""
    tried = (
        f"-select,name={variable},levidx={int(level) + 1},timestep=1",
        # a variable with no levels, or no times
        f"-select,name={variable},timestep=1",
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


def fvcom_field(mesh, data):
    """The nctoolkit dataset of an FVCOM field: data is the DataArray
    viewer_catalogue.Catalogue.field gives (a value for each node, or each
    element), and mesh its viewer_mesh.Mesh. It is on a grid of points (the
    nodes, or the centres of the elements), as fvcom.fvcom_regrid has it, which
    CDO can regrid by nearest neighbour. Raises ValueError, in words for the
    user, if the mesh has no longitudes and latitudes."""
    if mesh.cartesian:
        raise ValueError("The FVCOM mesh has no longitudes and latitudes, so it cannot be put on a longitude-latitude grid.")
    location = "node" if data.size == mesh.nodes else "nele"
    if location == "node":
        lon, lat = mesh.x, mesh.y
    else:
        lon, lat = mesh.x[mesh.triangles].mean(axis=1), mesh.y[mesh.triangles].mean(axis=1)
    values = np.asarray(data.values, dtype="float32").reshape(-1)
    array = xr.DataArray(
        values, dims=(location,), name=NAME,
        coords={
            "lon": (location, lon, {"standard_name": "longitude", "units": "degrees_east"}),
            "lat": (location, lat, {"standard_name": "latitude", "units": "degrees_north"}),
        },
    )
    ds = nc.from_xarray(array)
    points = nc.generate_grid.generate_grid(pd.DataFrame({"lon": lon, "lat": lat}))
    nc.session.append_safe(points)
    ds.cdo_command(f"setgrid,{points}")
    ds.run()
    return ds


def _inside_mesh(mesh, lon, lat):
    """Whether each of the longitudes and latitudes (arrays of one shape) is
    inside the triangles of the FVCOM mesh."""
    from matplotlib.tri import Triangulation

    lon = np.where(lon > 180, lon - 360, lon)
    return Triangulation(mesh.x, mesh.y, mesh.triangles).get_trifinder()(lon, lat) != -1


def _fvcom_regridded(field, mesh, grid_nc, reference):
    """An FVCOM field (see fvcom_field) on the grid of the file grid_nc by
    nearest neighbour, with the cells outside the mesh missing, since the
    nearest node would otherwise fill the whole grid. reference is the xarray
    dataset of the baseline, which says where the cells are."""
    ds = field.copy()
    with warnings.catch_warnings(record=True):
        ds.regrid(grid_nc, method=FVCOM_METHOD)
        ds.run()
        data = ds.to_xarray()
    found = sim_unify._lonlat(reference)
    if found is None:
        raise ValueError("The baseline grid has no longitudes and latitudes to compare the FVCOM mesh with.")
    lon, lat = found if found[0].ndim > 1 else np.meshgrid(*found)
    inside = _inside_mesh(mesh, lon, lat)
    name = NAME
    values = sim_unify.field_variable(data, NAME).values
    if values.shape[-inside.ndim:] != inside.shape:
        raise ValueError("The FVCOM field could not be put on the baseline's grid.")
    # in the baseline's own dimensions (it may have a time, or levels, of one), so the shapes agree
    template = sim_unify.field_variable(reference, NAME)
    values = np.where(inside, values.reshape(inside.shape), np.nan).reshape(template.shape)
    out = reference.drop_vars(list(reference.data_vars))
    out[name] = template.copy(data=values)
    return nc.from_xarray(out)


def unified(fields, baseline, method, fvcom=None, keep=None, box=None, progress=None):
    """The cells that have a value in every simulation, on the grid of
    baseline (or, if box is given, {"lon": [west, east], "lat": [south,
    north], "res": [lon, lat]} as sim_unify.check_unify makes it, on a lon-lat
    grid of that, and baseline is not used): fields is {name: dataset} (see
    field_for_cdo, or fvcom_field for those of FVCOM simulations), each on its
    simulation's own grid, and method one of REGRID_METHODS. fvcom is
    {name: the viewer_mesh.Mesh} of the FVCOM ones in fields, which can only be regridded by nearest neighbour, and
    cannot be the baseline. keep, if given, is where the mask is copied to,
    as sim_unify.prepare writes it (0 where every simulation has a value,
    missing elsewhere), for matchup. Returns (a loaded DataArray, 1 where every
    simulation has a value and NaN where any has not, with its longitudes and
    latitudes as coords, how many cells are 1, how many there are). Raises
    ValueError, in words for the user, if they cannot be put on one grid. progress, if given, is called as
    progress(stage, name) as it goes (see sim_unify.prepare)."""
    fvcom = fvcom or {}
    if box is None and baseline not in fields:
        raise ValueError("Choose the baseline from the simulations drawn.")
    if method not in REGRID_METHODS:
        raise ValueError("Choose a regridding method.")
    if box is None and baseline in fvcom:
        raise ValueError("An FVCOM simulation cannot be the baseline grid. Choose another simulation's.")
    if fvcom and method != FVCOM_METHOD:
        raise ValueError(f"FVCOM output can only be put on another grid by nearest neighbour, not {REGRID_METHODS[method].lower()} regridding.")
    unify = {"method": "baseline", "baseline": baseline, "regrid": method} if box is None else {**box, "regrid": method}
    with tempfile.TemporaryDirectory(prefix="oceanval-align-") as folder:
        try:
            # nctoolkit's to_xarray opens files in this process (see oceanval.netcdf_lock)
            with netcdf_lock.LOCK:
                if fvcom:
                    # on the baseline's grid before prepare(), which then leaves them as they are
                    grid_nc = os.path.join(folder, "baseline.nc")
                    with warnings.catch_warnings(record=True):
                        fields[baseline].copy().to_nc(grid_nc, zip=False, overwrite=True)
                        reference = fields[baseline].to_xarray()
                    def regridded(name, field):
                        if name not in fvcom:
                            return field
                        if progress is not None:
                            progress("regridding", name)
                        return _fvcom_regridded(field, fvcom[name], grid_nc, reference)

                    fields = {name: regridded(name, field) for name, field in fields.items()}
                shared, total = sim_unify.prepare(folder, unify, NAME, "surface", fields, progress=progress)
                with xr.open_dataset(sim_unify.mask_path(folder, NAME, "surface")) as data:
                    mask = data[NAME].load()
            if keep is not None:
                shutil.copyfile(sim_unify.mask_path(folder, NAME, "surface"), keep)
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
