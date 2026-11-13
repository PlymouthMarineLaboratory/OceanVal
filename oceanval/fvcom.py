"""Raw FVCOM output: telling it apart, opening it, and keeping results on its mesh.

FVCOM writes its output on an unstructured mesh of triangles that CDO cannot
read. nctoolkit's open_fvcom (nctoolkit 1.4.0 and later) opens it as a dataset
that CDO, and so the rest of nctoolkit, can work with: each node or element of
the mesh is a cell with its true area, the sigma layers are levels numbered
from 1 at the surface, and the thickness of each layer can be added
(THICKNESS), which nctoolkit's vertical methods, regrid, to_latlon, to_transect
and match_points use. So once a file is found to be FVCOM output (is_fvcom),
OceanVal opens it with open_model and works with it as with any other output.

What is left here is what open_fvcom is not for: reading what a file holds
from its header (its variables, times and extent), as the window's pages need
it fast and open_fvcom converts every value it opens; and keeping results on
the mesh (on_mesh), so that their maps are drawn on its triangles
(oceanval.mesh_maps).
"""

import os
import warnings

import numpy as np
import pandas as pd
import xarray as xr

from oceanval.quiet_nc import nc


# ---------------------------------------------------------------------------
# telling FVCOM output apart, and opening it
# ---------------------------------------------------------------------------

# the variable nctoolkit's open_fvcom adds: the thickness of each sigma layer, in m
THICKNESS = "layer_thickness"


def has_mesh(dims, variables):
    """Whether a file with these dimensions and variables is raw FVCOM output:
    its unstructured mesh (node, nele and the triangles, nv), which output that
    has been regridded no longer has. The "source" attribute is not used, as
    regridded FVCOM output still carries it."""
    return {"node", "nele"} <= set(dims) and "nv" in variables


def is_fvcom(path):
    """Whether the netCDF file at path is raw FVCOM output (see has_mesh), which
    CDO cannot read and nctoolkit opens with open_fvcom. Reads only the header.
    It does not take oceanval.netcdf_lock.LOCK: a caller in the window's
    threads holds it."""
    import netCDF4

    try:
        with netCDF4.Dataset(path) as handle:
            return has_mesh(handle.dimensions, handle.variables)
    except Exception:
        return False


def open_model(paths, variables, fvcom=None, thickness=False):
    """The model output in paths as an nctoolkit dataset: opened by
    nctoolkit's open_fvcom if it is raw FVCOM output (fvcom, or found by
    is_fvcom of the first file if None), with only variables (the names of a
    model variable, see oceanval.model_expr), as open_fvcom converts every
    value of each variable it opens, and, if thickness, the thickness of the
    sigma layers, THICKNESS, which only depths and the water column need;
    otherwise by open_data, with every variable."""
    if isinstance(paths, str):
        paths = [paths]
    if fvcom is None:
        fvcom = is_fvcom(paths[0])
    if not fvcom:
        return nc.open_data(paths if len(paths) > 1 else paths[0], checks=False)
    with warnings.catch_warnings(record=True):
        return nc.open_fvcom(
            paths if len(paths) > 1 else paths[0], variables=list(dict.fromkeys(variables)), thickness=thickness
        )


def thickness_dataset(path, variable):
    """The thickness of the sigma layers of the FVCOM file at path, at its first
    time, as a dataset of THICKNESS, for matchups through the water column, which
    use it as the cell thickness of other models (variable is one of the file's
    variables on the sigma layers, which open_fvcom needs)."""
    ds = open_model(path, [variable], fvcom=True, thickness=True)
    if ds.fvcom_thickness is None:
        raise ValueError(
            f"The depths of the sigma layers of {variable} in {path} cannot be worked out: "
            "the file needs its sigma levels (siglev or siglay) and bathymetry (h)"
        )
    ds.subset(time=0, variables=THICKNESS)
    ds.run()
    # from here it is a variable like any other cell thickness
    ds.fvcom_thickness = None
    return ds


# ---------------------------------------------------------------------------
# what is in a file, read from its header: nctoolkit's open_fvcom converts every
# value it opens, far too slow for the window's pages, which only need to know
# what is there
# ---------------------------------------------------------------------------

# the location of a variable on the mesh: its nodes or its elements
NODE = "node"
NELE = "nele"
# the vertical dimensions: the sigma layers, whose first is the surface, and the
# sigma levels, the layers' tops and bottoms
SIGLAY = "siglay"
SIGLEV = "siglev"
VERTICAL = (SIGLAY, SIGLEV)
# the mesh, as the results files keep it, so a map can be drawn of them alone
# (see oceanval.mesh_maps and on_mesh)
MESH = ("lon", "lat", "lonc", "latc", "x", "y", "nv")


def names_of(parts):
    """The variables parts (a list of names or a model variable, see
    oceanval.model_expr) needs."""
    from oceanval import model_expr

    return model_expr.names(parts) if isinstance(parts, str) else list(parts)


def variables(path):
    """The variables of the FVCOM file at path that can be mapped (those on
    its nodes or elements, other than the mesh's own), as analysis' streams
    gives them: {name: {"long_name", "units", "levels"}}, levels the sigma
    layers or levels, or 1."""
    import netCDF4

    from oceanval.viewer_mesh import MESH_VARIABLES

    found = {}
    with netCDF4.Dataset(path) as handle:
        for name, variable in handle.variables.items():
            dims = variable.dimensions
            if name in MESH_VARIABLES or not ({NODE, NELE} & set(dims)) or "time" not in dims:
                continue
            levels = [len(handle.dimensions[dim]) for dim in dims if dim in VERTICAL]
            found[str(name)] = {
                "long_name": str(getattr(variable, "long_name", "")),
                "units": str(getattr(variable, "units", "")),
                "levels": int(levels[0]) if levels else 1,
            }
    return found


def location(path, parts):
    """NODE or NELE: where every one of the variables in parts is on the mesh
    in the file at path. Raises ValueError if they differ, or are on neither:
    nctoolkit's open_fvcom opens the variables of one of them at a time."""
    import netCDF4

    parts = names_of(parts)
    found = {}
    with netCDF4.Dataset(path) as handle:
        for name in parts:
            dims = handle.variables[name].dimensions
            found[name] = NODE if NODE in dims else NELE if NELE in dims else None
    if any(where is None for where in found.values()):
        missing = [name for name, where in found.items() if where is None]
        raise ValueError(
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} on neither the nodes nor the "
            "elements of the FVCOM mesh, so cannot be mapped."
        )
    if len(set(found.values())) > 1:
        raise ValueError(
            " and ".join(parts) + " are not all on the same part of the FVCOM mesh (some are on its nodes, "
            "some on its elements), so cannot be used together."
        )
    return next(iter(found.values()))


def vertical(path, parts):
    """The vertical dimension of each of the variables in parts in the FVCOM
    file at path: {name: SIGLAY, SIGLEV or None, for one with none}."""
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        return {
            name: next((dim for dim in handle.variables[name].dimensions if dim in VERTICAL), None)
            for name in names_of(parts)
        }


def has_lon_lat(path):
    """Whether the FVCOM file at path has the longitudes and latitudes of its
    mesh (a mesh in metres has them all 0, or none), which nctoolkit's
    open_fvcom needs."""
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        if "lon" not in handle.variables or "lat" not in handle.variables:
            return False
        lon = np.ma.filled(np.ma.asarray(handle.variables["lon"][:]).astype("float64"), np.nan)
        lat = np.ma.filled(np.ma.asarray(handle.variables["lat"][:]).astype("float64"), np.nan)
    return bool(np.nanmax(np.abs(lon)) > 0 or np.nanmax(np.abs(lat)) > 0)


def not_layered(path, parts):
    """Those of parts that are not on the sigma layers (siglay) of the FVCOM
    file at path, and so have no water column to take: those on the sigma
    levels (siglev), the tops and bottoms of the layers, and those with no
    vertical dimension."""
    return [name for name, dim in vertical(path, parts).items() if dim != SIGLAY]


def mesh_arrays(path):
    """The mesh of the FVCOM file at path as numpy arrays, {name: values} for
    the MESH variables it has: only these are read, never a variable of the
    model's values. The caller holds oceanval.netcdf_lock.LOCK in a thread of
    the window."""
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        return {name: np.asarray(handle.variables[name][:]) for name in MESH if name in handle.variables}


def same_mesh(first, second):
    """Whether two meshes (from mesh_arrays) are the same: the same cells (nv)
    in the same places."""
    if set(first) != set(second):
        return False
    for name in first:
        a, b = first[name], second[name]
        if a.shape != b.shape:
            return False
        if name == "nv":
            if not np.array_equal(a, b):
                return False
        elif not np.allclose(a, b, rtol=0, atol=1e-6, equal_nan=True):
            return False
    return True


# ---------------------------------------------------------------------------
# results on the mesh
# ---------------------------------------------------------------------------

# the attributes nctoolkit's open_fvcom gives its files, which CDO keeps: the part
# of the mesh the values are on, and the FVCOM file they came from
LOCATION_ATTRIBUTE = "nctoolkit_fvcom"
# the area of each cell of a results file on the mesh (see on_mesh)
AREA = "cell_area"
SOURCE_ATTRIBUTE = "fvcom_source"


def from_fvcom(path):
    """The part of the FVCOM mesh (NODE or NELE) the values in the netCDF file
    at path are on, if they were worked out from output nctoolkit's open_fvcom
    opened (see on_mesh), else None."""
    import netCDF4

    try:
        with netCDF4.Dataset(path) as handle:
            return getattr(handle, LOCATION_ATTRIBUTE, None)
    except OSError:
        return None


def on_mesh(path, source=None):
    """Rewrite the results file at path, worked out by nctoolkit from FVCOM
    output that open_fvcom opened (each value on a node or an element of the
    mesh, as a cell of an unstructured grid), as OceanVal keeps results on the
    mesh: each variable on (time, NODE or NELE), with the mesh of the FVCOM
    file it came from (MESH) and the "mesh" attribute saying which part of it
    the values are on, so that a map can be drawn of the file alone (see
    oceanval.mesh_maps), and with "month", the month of each time. source is
    that FVCOM file, if the results file does not name it. Does nothing to a
    file that is on the mesh already, or was not worked out from FVCOM output.
    Returns the part of the mesh, or None."""
    import cftime
    import netCDF4

    with xr.open_dataset(path, decode_times=False) as ds:
        if ds.attrs.get("mesh"):
            return ds.attrs["mesh"]
        where = ds.attrs.get(LOCATION_ATTRIBUTE)
        if where is None:
            return None
        source = source or ds.attrs.get(SOURCE_ATTRIBUTE)
        ds = ds.load()
    # the area of each cell, as CDO works it out from the cells' bounds, which the
    # file on the mesh does not keep: means of it are weighted by these (see AREA)
    with warnings.catch_warnings(record=True):
        ds_area = nc.open_data(path, checks=False)
        ds_area.cdo_command("-gridarea")
        ds_area.run()
    with xr.open_dataset(ds_area[0], decode_times=False) as found:
        areas = np.asarray(found[list(found.data_vars)[-1]].values, dtype="float64").ravel()
    mesh = {}
    with netCDF4.Dataset(source) as handle:
        for name in MESH:
            if name in handle.variables:
                variable = handle.variables[name]
                attrs = {key: variable.getncattr(key) for key in variable.ncattrs() if key != "_FillValue"}
                mesh[name] = xr.Variable(variable.dimensions, np.asarray(variable[:]), attrs)
    data = {}
    time_name = next((name for name in ds.dims if "time" in name.lower()), None)
    for name, variable in ds.data_vars.items():
        if where not in variable.dims or name in ("lon", "lat", "lon_bnds", "lat_bnds", AREA):
            continue
        # any vertical dimension left has one level: the layer has been taken
        extra = [dim for dim in variable.dims if dim not in (where, time_name)]
        values = variable.squeeze(extra, drop=True)
        attrs = {key: value for key, value in variable.attrs.items() if key in ("units", "long_name", "standard_name")}
        data[name] = xr.Variable(values.dims, values.values.astype("float32"), attrs)
        data[name].encoding["_FillValue"] = np.float32(-1e20)
    coords = {}
    if time_name is not None:
        time = ds[time_name]
        attrs = {key: time.attrs[key] for key in ("units", "calendar") if key in time.attrs}
        coords[time_name] = (time_name, time.values.astype("float64"), attrs)
        dates = cftime.num2date(time.values, attrs["units"], attrs.get("calendar", "standard"))
        data["month"] = xr.Variable(
            (time_name,), np.asarray([int(date.month) for date in np.atleast_1d(dates)], dtype="int32"),
            {"long_name": "month of the year"},
        )
    data[AREA] = xr.Variable((where,), areas, {"long_name": "area of the cell", "units": "m2"})
    out = xr.Dataset({**data, **mesh}, coords=coords, attrs={"source": "oceanval", "mesh": where})
    temporary = path + ".mesh.nc"
    out.to_netcdf(temporary)
    os.replace(temporary, path)
    return where


def domain_mean(path, key):
    """The mean over the mesh of key at each time in path, a results file on
    the mesh (see on_mesh), weighted by the areas of its cells (AREA), as
    analysis_plots.domain_mean gives it: (times, values, note), note "" as the
    areas are the cells' own."""
    import cftime
    import netCDF4

    with netCDF4.Dataset(path) as handle:
        variable = handle.variables[key]
        variable.set_auto_maskandscale(True)
        values = np.ma.filled(np.ma.asarray(variable[:]).astype("float64"), np.nan)
        areas = np.asarray(handle.variables[AREA][:], dtype="float64")
        time = handle.variables["time"]
        times = cftime.num2date(time[:], time.units, getattr(time, "calendar", "standard"))
    values = values.reshape(-1, values.shape[-1])
    weights = np.where(np.isfinite(values), areas[None, :], 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        means = np.nansum(np.nan_to_num(values) * weights, axis=1) / weights.sum(axis=1)
    return list(np.atleast_1d(times)), np.where(weights.sum(axis=1) > 0, means, np.nan), ""


def fvcom_extent(ff):
    """
    [lon_min, lon_max], [lat_min, lat_max] of the nodes in an FVCOM file
    """
    import netCDF4 as nc4

    with nc4.Dataset(ff) as ds_nc:
        lon = np.ma.filled(ds_nc.variables["lon"][:].astype("float64"), np.nan)
        lat = np.ma.filled(ds_nc.variables["lat"][:].astype("float64"), np.nan)
    lon = np.where(lon > 180, lon - 360, lon)
    return [float(np.nanmin(lon)), float(np.nanmax(lon))], [
        float(np.nanmin(lat)),
        float(np.nanmax(lat)),
    ]


# CDO cannot read FVCOM's unstructured variables, so nctoolkit's contents and times
# come back empty for raw FVCOM files. These read the same information with xarray.


def fvcom_contents(ff):
    """
    Variables in an FVCOM file, with the columns of nctoolkit's contents used by oceanval
    """
    rows = []
    with xr.open_dataset(ff, drop_variables=["siglay", "siglev"], decode_times=False) as ds:
        for vv in ds.data_vars:
            da = ds[vv]
            nlevels = 1
            for dim in ["siglay", "siglev"]:
                if dim in da.dims:
                    nlevels = da.sizes[dim]
            rows.append(
                {
                    "variable": vv,
                    "nlevels": nlevels,
                    "long_name": da.attrs.get("long_name"),
                    "unit": da.attrs.get("units"),
                }
            )
    return pd.DataFrame(rows)


def fvcom_times(ff):
    """
    Year, month and day of each time step in an FVCOM file
    """
    # other time variables (e.g. Itime2) have units xarray cannot decode
    with xr.open_dataset(ff, drop_variables=["siglay", "siglev"], decode_times=False) as ds:
        times = xr.decode_cf(ds[["time"]]).time
        return pd.DataFrame(
            {
                "year": [int(x) for x in times.dt.year.values],
                "month": [int(x) for x in times.dt.month.values],
                "day": [int(x) for x in times.dt.day.values],
            }
        )
