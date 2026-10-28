"""Small simulations written for the viewer's tests (oceanval.viewer_*,
from ncviewer's): NEMO-like output, a file per stream per month, on a
curvilinear grid whose land cells have 0 longitude and latitude, a regular
lon-lat one, and FVCOM output on a small mesh."""

import os

import netCDF4
import numpy as np
import xarray as xr


def _times(year, month, per_month, calendar):
    days = 30 if calendar == "360_day" else [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    start = (year - 2000) * (360 if calendar == "360_day" else 365) + (
        (month - 1) * 30 if calendar == "360_day"
        else sum([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][: month - 1])
    )
    if per_month == 1:
        return np.array([start + days / 2])
    return start + np.arange(per_month) + 0.5


def nemo_month(path_t, path_p, year, month, per_month=1, calendar="noleap", ny=12, nx=10, nz=4):
    time = _times(year, month, per_month, calendar)
    y, x = np.meshgrid(np.arange(ny), np.arange(nx), indexing="ij")
    lon = -10 + x * 0.5 + y * 0.05
    lat = 48 + y * 0.4 + x * 0.02
    land = (x < 2) & (y < 3)
    lon = np.where(land, 0.0, lon).astype("float32")
    lat = np.where(land, 0.0, lat).astype("float32")
    depth = np.array([1.0, 5.0, 20.0, 50.0][:nz])
    base = 10 + month + (year - 2000) * 0.1
    temp = base + 0.1 * y[None, None] - 0.05 * depth[None, :, None, None] + time[:, None, None, None] * 0
    temp = np.where(land[None, None], np.nan, temp).astype("float32")
    time_attrs = {"units": "days since 2000-01-01 00:00:00", "calendar": calendar, "standard_name": "time", "axis": "T"}
    coords = {
        "nav_lon": (("y", "x"), lon, {"standard_name": "longitude", "units": "degrees_east"}),
        "nav_lat": (("y", "x"), lat, {"standard_name": "latitude", "units": "degrees_north"}),
        "deptht": ("deptht", depth, {"units": "m", "positive": "down", "long_name": "Vertical T levels"}),
        "time_counter": ("time_counter", time, time_attrs),
    }
    grid_t = xr.Dataset(
        {
            "votemper": (("time_counter", "deptht", "y", "x"), temp,
                         {"long_name": "Temperature", "units": "degC", "coordinates": "nav_lon nav_lat"}),
            "sossheig": (("time_counter", "y", "x"), temp[:, 0] * 0.01,
                         {"long_name": "sea surface height", "units": "m"}),
            "time_counter_bnds": (("time_counter", "nb"), np.stack([time - 0.5, time + 0.5], axis=1)),
        },
        coords=coords,
    )
    grid_t["time_counter"].attrs["bounds"] = "time_counter_bnds"
    ptrc = xr.Dataset(
        {
            "chl": (("time_counter", "deptht", "y", "x"), temp / 10,
                    {"long_name": "Chlorophyll", "units": "mg m-3"}),
            "votemper": (("time_counter", "deptht", "y", "x"), temp,
                         {"long_name": "Temperature", "units": "degC"}),
        },
        coords=coords,
    )
    encoding = {"time_counter": {"dtype": "float64"}}
    grid_t.to_netcdf(path_t, encoding=encoding)
    ptrc.to_netcdf(path_p, encoding=encoding)


def nemo_simulation(root, years=(2000, 2001), layout="YYYY/MM", per_month=1, calendar="noleap"):
    """A NEMO-like simulation under root, its files laid out as layout says:
    "YYYY/MM" (2 down), "MM" (1 down) or "flat" (0 down). Returns root."""
    for year in years:
        for month in range(1, 13):
            if layout == "YYYY/MM":
                folder = os.path.join(root, str(year), f"{month:02d}")
            elif layout == "MM":
                folder = os.path.join(root, f"{month:02d}")
            else:
                folder = root
            os.makedirs(folder, exist_ok=True)
            stamp = f"{year}{month:02d}01_{year}{month:02d}28"
            nemo_month(
                os.path.join(folder, f"amm7_1m_{stamp}_grid_T.nc"),
                os.path.join(folder, f"amm7_1m_{stamp}_ptrc_T.nc"),
                year, month, per_month=per_month, calendar=calendar,
            )
    return root


def regular_file(path, nt=3):
    lon = np.linspace(-20, 10, 31)
    lat = np.linspace(40, 65, 26)
    time = np.arange(nt) * 30.0 + 15
    values = np.sin(lon[None, None] / 10) + np.cos(lat[None, :, None] / 10) + time[:, None, None] / 100
    ds = xr.Dataset(
        {"sst": (("time", "lat", "lon"), values.astype("float32"), {"long_name": "Sea surface temperature", "units": "K"}),
         "station_temp": (("time",), values[:, 5, 5], {"units": "K"})},
        coords={
            "lon": ("lon", lon, {"units": "degrees_east"}),
            "lat": ("lat", lat, {"units": "degrees_north"}),
            "time": ("time", time, {"units": "days since 1990-01-01", "calendar": "standard"}),
        },
    )
    ds.to_netcdf(path)
    return path


# 2012-01-01 in FVCOM's modified julian days
MJD_2012 = 55927


def fvcom_mesh(cartesian=False):
    """The mesh of tests/test_fvcom.py: 0.1 degree squares over
    5-4W, 50-51N, each two triangles, with the north-east corner cut out.
    Returns the nodes' lon, lat and nv (three, nele), 1-based."""
    lons = np.round(np.arange(-5.0, -3.95, 0.1), 6)
    lats = np.round(np.arange(50.0, 51.05, 0.1), 6)
    glon, glat = np.meshgrid(lons, lats)
    keep = ~((glon > -4.45) & (glat > 50.55))
    index = np.full(glon.shape, -1)
    index[keep] = np.arange(keep.sum())
    triangles = []
    for j in range(len(lats) - 1):
        for i in range(len(lons) - 1):
            a, b = index[j, i], index[j, i + 1]
            c, d = index[j + 1, i], index[j + 1, i + 1]
            if min(a, b, c, d) >= 0:
                triangles += [[a, b, d], [a, d, c]]
    return glon[keep], glat[keep], np.array(triangles).T + 1


def fvcom_file(path, first_day=0, n_times=3, cartesian=False):
    """An FVCOM file of the mesh above, written as FVCOM writes them (with
    netCDF4, as xarray will not write a 2-D variable named after one of its
    dimensions): temp on the nodes (5 sigma layers) equal to 10 + lon + the
    layer + the day, zeta on the nodes, u on the elements equal to the
    element's number, h, and FVCOM's times. cartesian: lon and lat are 0,
    and the positions are x and y in metres."""
    lon, lat, nv = fvcom_mesh()
    n_node, n_nele = len(lon), nv.shape[1]
    days = first_day + np.arange(n_times)
    siglay = np.repeat((-(np.arange(5) + 0.5) / 5)[:, None], n_node, axis=1)
    siglev = np.repeat((-np.arange(6) / 5)[:, None], n_node, axis=1)
    temp = 10 + lon[None, None, :] + np.arange(5)[None, :, None] + days[:, None, None]
    lonc, latc = lon[nv - 1].mean(axis=0), lat[nv - 1].mean(axis=0)
    with netCDF4.Dataset(path, "w") as ds:
        for name, size in (("node", n_node), ("nele", n_nele), ("three", 3), ("siglay", 5), ("siglev", 6), ("time", None)):
            ds.createDimension(name, size)

        def add(name, dims, values, dtype="f4", **attrs):
            variable = ds.createVariable(name, dtype, dims)
            variable[:] = values
            variable.setncatts(attrs)

        add("x", ("node",), (lon + 5) * 70000, units="meters", long_name="nodal x-coordinate")
        add("y", ("node",), (lat - 50) * 111000, units="meters", long_name="nodal y-coordinate")
        add("lon", ("node",), lon * (0 if cartesian else 1), units="degrees_east", long_name="nodal longitude")
        add("lat", ("node",), lat * (0 if cartesian else 1), units="degrees_north", long_name="nodal latitude")
        add("lonc", ("nele",), lonc * (0 if cartesian else 1), units="degrees_east", long_name="zonal longitude")
        add("latc", ("nele",), latc * (0 if cartesian else 1), units="degrees_north", long_name="zonal latitude")
        add("nv", ("three", "nele"), nv, dtype="i4", long_name="nodes surrounding element")
        add("siglay", ("siglay", "node"), siglay, long_name="Sigma Layers", positive="up")
        add("siglev", ("siglev", "node"), siglev, long_name="Sigma Levels", positive="up")
        add("h", ("node",), 20 + 200 * (lon + 5), units="m", long_name="Bathymetry")
        add("time", ("time",), MJD_2012 + days, units="days since 1858-11-17 00:00:00", format="modified julian day (MJD)")
        add("Itime", ("time",), MJD_2012 + days, dtype="i4", units="days since 1858-11-17 00:00:00")
        # FVCOM's integer time has units xarray cannot decode
        add("Itime2", ("time",), np.zeros(n_times), dtype="i4", units="msec since 00:00:00")
        add("zeta", ("time", "node"), np.zeros((n_times, n_node)) + 0.5, units="meters", long_name="Water Surface Elevation")
        add("temp", ("time", "siglay", "node"), temp, units="degrees_C", long_name="temperature")
        add("u", ("time", "siglay", "nele"), np.broadcast_to(np.arange(n_nele, dtype="f4"), (n_times, 5, n_nele)),
            units="meters s-1", long_name="Eastward Water Velocity")
    return path


def fvcom_simulation(root, files=4, cartesian=False):
    """FVCOM output under root: files files of three days each."""
    os.makedirs(root, exist_ok=True)
    for number in range(files):
        fvcom_file(os.path.join(root, f"estuary_{number + 1:04d}.nc"), first_day=3 * number, cartesian=cartesian)
    return root
