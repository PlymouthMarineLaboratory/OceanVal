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

import os
import tempfile
import warnings

from oceanval.quiet_nc import nc
import numpy as np
import pandas as pd
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


# FVCOM's nodes are points of no grid CDO knows, so they can only be put on a grid by taking the nearest
# node, as the matchups and compare_simulations() regrid them (see oceanval.fvcom, oceanval.sim_fvcom)
FVCOM_METHOD = "nn"


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


def _fvcom_on_a_grid(field, mesh, regrid, extra=()):
    """An FVCOM field (see fvcom_field) put on a lon-lat grid by regrid(ds),
    which changes the dataset it is given, with the cells outside the mesh
    missing, since the nearest node would otherwise fill the whole grid. extra
    are the names of the dimensions of one that the other simulations' fields
    have before their latitude and longitude (a time and a depth of one, as CDO
    leaves them), so that the shapes agree."""
    ds = field.copy()
    with warnings.catch_warnings(record=True):
        regrid(ds)
        ds.run()
        data = ds.to_xarray()
    found = sim_unify._lonlat(data)
    if found is None:
        raise ValueError("The grid has no longitudes and latitudes to compare the FVCOM mesh with.")
    lon, lat = found if found[0].ndim > 1 else np.meshgrid(*found)
    inside = _inside_mesh(mesh, lon, lat)
    template = sim_unify.field_variable(data, NAME)
    values = template.values
    if values.shape[-inside.ndim:] != inside.shape:
        raise ValueError("The FVCOM field could not be put on the grid.")
    # in the dataset's own dimensions (it may have a time, or levels, of one), so the shapes agree
    values = np.where(inside, values.reshape(inside.shape), np.nan).reshape(template.shape)
    out = data.drop_vars(list(data.data_vars))
    values = template.copy(data=values)
    for dim in reversed([dim for dim in extra if dim not in values.dims]):
        values = values.expand_dims(dim)
    out[NAME] = values
    return nc.from_xarray(out)


def _mesh_box(meshes):
    """The box the baseline is cropped to when FVCOM output is put on its grid: the
    meshes' extent, a little wider (sim_unify.padded_box), or None."""
    lon = np.concatenate([np.where(mesh.x > 180, mesh.x - 360, mesh.x) for mesh in meshes.values()])
    lat = np.concatenate([mesh.y for mesh in meshes.values()])
    return sim_unify.padded_box(lon, lat)


def unified(fields, baseline, method, box=None, progress=None, meshes=None):
    """The cells that have a value in every simulation, on the grid of
    baseline (or, if box is given, {"lon": [west, east], "lat": [south,
    north], "res": [lon, lat]} as sim_unify.check_unify makes it, on a lon-lat
    grid of that, and baseline is not used): fields is {name: dataset} (see
    field_for_cdo), each on its simulation's own grid, and method one of
    REGRID_METHODS. Returns (a loaded DataArray, 1 where every
    simulation has a value and NaN where any has not, with its longitudes and
    latitudes as coords, how many cells are 1, how many there are). Raises
    ValueError, in words for the user, if they cannot be put on one grid. progress, if given, is called as
    progress(stage, name) as it goes (see sim_unify.prepare). meshes, if given, is
    {name: the viewer_mesh.Mesh} of the fields that are FVCOM output (see
    fvcom_field), which are put on the grid first by nearest neighbour, as the
    run does: onto the baseline's grid cropped to the meshes' extent, or the box's
    lon-lat grid, with the cells outside the mesh missing. method is then how
    the others are regridded. An FVCOM simulation cannot be the baseline."""
    if box is None and baseline not in fields:
        raise ValueError("Choose the baseline from the simulations drawn.")
    if method not in REGRID_METHODS:
        raise ValueError("Choose a regridding method.")
    meshes = meshes or {}
    netcdf_lock.silence_hdf5()
    if box is None and baseline in meshes:
        raise ValueError("An FVCOM simulation cannot be the baseline grid. Choose another simulation's.")
    unify = {"method": "baseline", "baseline": baseline, "regrid": method} if box is None else {**box, "regrid": method}
    with tempfile.TemporaryDirectory(prefix="oceanval-align-") as folder:
        try:
            # nctoolkit's to_xarray opens files in this process (see oceanval.netcdf_lock)
            with netcdf_lock.LOCK:
                if meshes:
                    fields = dict(fields)
                    if box is None:
                        # the baseline's grid, cropped to where the meshes are, as the run's is
                        crop = _mesh_box(meshes)
                        grid = fields[baseline].copy()
                        with warnings.catch_warnings(record=True):
                            sim_unify.crop_to(grid, crop)
                            grid_nc = os.path.join(folder, "baseline.nc")
                            grid.to_nc(grid_nc, zip=False, overwrite=True)
                        if crop is not None:
                            unify["crop"] = [list(crop[0]), list(crop[1])]
                        regrid = lambda ds: ds.regrid(grid_nc, method=FVCOM_METHOD)  # noqa: E731
                    else:
                        regrid = lambda ds: ds.to_latlon(  # noqa: E731
                            lon=box["lon"], lat=box["lat"], res=box["res"], method=FVCOM_METHOD
                        )
                    # the dimensions the others' fields have before their latitude and longitude
                    extra = ()
                    for name, field in fields.items():
                        if name not in meshes:
                            extra = sim_unify.field_variable(field.to_xarray(), NAME).dims[:-2]
                            break
                    for name, mesh in meshes.items():
                        if progress is not None:
                            progress("regridding", name)
                        fields[name] = _fvcom_on_a_grid(fields[name], mesh, regrid, extra)
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
