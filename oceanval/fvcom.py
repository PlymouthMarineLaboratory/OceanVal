import os
import hashlib
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from oceanval.quiet_nc import nc
import warnings
import xarray as xr
import numpy as np
import subprocess
import pandas as pd
from tqdm import tqdm
from oceanval.session import session_info


def bin_value(x, bin_res):
    return np.floor((x + bin_res / 2) / bin_res + 0.5) * bin_res - bin_res / 2


# stands in for missing values below the seabed during regridding
z_fill = 1e20

# WOA23 standard depth levels (m)
woa_levels = np.concatenate(
    [
        np.arange(0, 100, 5),
        np.arange(100, 500, 25),
        np.arange(500, 2000, 50),
        np.arange(2000, 5501, 100),
    ]
).astype("float64")


def default_levels(max_depth):
    """
    WOA23 standard depths, down to the first level at or below max_depth
    """
    n = int(np.searchsorted(woa_levels, max_depth)) + 1
    return woa_levels[:n].tolist()


def _sigma_to_z(values, siglay, h, zeta, levels):
    """
    Linearly interpolate FVCOM sigma layer values onto fixed depth levels.

    Parameters
    ----------
    values : np.ndarray
        Values on sigma layers, shape (time, siglay, node).
    siglay : np.ndarray
        Sigma layer centres, shape (siglay, node). 0 at the surface, -1 at the seabed.
    h : np.ndarray
        Bathymetry, shape (node,). Positive down.
    zeta : np.ndarray
        Free surface elevation, shape (time, node).
    levels : list
        Depths below the free surface to interpolate to. Positive down.

    Returns
    -------
    np.ndarray
        Values on depth levels, shape (time, depth, node).
        Levels above the top layer centre take the top layer value, levels between
        the bottom layer centre and the seabed take the bottom layer value, and
        levels below the seabed are NaN.
    """
    levels = np.asarray(levels, dtype="float64")
    siglay = np.asarray(siglay, dtype="float64")
    h = np.asarray(h, dtype="float64")
    zeta = np.asarray(zeta, dtype="float64")
    n_time, n_layer, n_node = values.shape
    out = np.full((n_time, len(levels), n_node), np.nan, dtype="float32")

    for tt in range(n_time):
        total_depth = h + zeta[tt]
        # layer centre depths below the free surface, increasing with layer index
        depths = -siglay * total_depth
        vals = np.asarray(values[tt], dtype="float64")

        # layers either side of each level, clamped to the top and bottom layers
        k = (depths[None, :, :] < levels[:, None, None]).sum(axis=1)
        upper = np.clip(k - 1, 0, n_layer - 1)
        lower = np.clip(k, 0, n_layer - 1)

        d_upper = np.take_along_axis(depths, upper, axis=0)
        d_lower = np.take_along_axis(depths, lower, axis=0)
        v_upper = np.take_along_axis(vals, upper, axis=0)
        v_lower = np.take_along_axis(vals, lower, axis=0)

        gap = d_lower - d_upper
        weight = np.zeros_like(gap)
        np.divide(
            levels[:, None] - d_upper, gap, out=weight, where=(upper != lower) & (gap > 0)
        )
        result = v_upper + weight * (v_lower - v_upper)

        # nothing below the seabed
        result[levels[:, None] > total_depth[None, :]] = np.nan
        out[tt] = result

    return out


def _z_level_array(ds_xr, ff, vv, levels):
    """
    Build a (time, depth, node) DataArray of variable vv on fixed depth levels
    """
    import netCDF4 as nc4

    with nc4.Dataset(ff) as ds_nc:
        siglay = ds_nc.variables["siglay"][:]
        h = ds_nc.variables["h"][:]
    siglay = np.ma.filled(siglay.astype("float64"), np.nan)
    h = np.ma.filled(h.astype("float64"), np.nan)

    da = ds_xr[vv]
    if siglay.ndim == 1:
        siglay = np.repeat(siglay[:, None], da.sizes["node"], axis=1)
    values = da.transpose("time", "siglay", "node").values
    zeta = ds_xr["zeta"].transpose("time", "node").values

    z_values = _sigma_to_z(values, siglay, h, zeta, levels)

    depth = xr.DataArray(
        np.asarray(levels, dtype="float64"),
        dims="depth",
        attrs={
            "long_name": "depth",
            "standard_name": "depth",
            "units": "m",
            "positive": "down",
            "axis": "Z",
        },
    )
    return xr.DataArray(
        z_values,
        dims=("time", "depth", "node"),
        coords={
            "time": da["time"],
            "depth": depth,
            "lon": da["lon"],
            "lat": da["lat"],
        },
        attrs=da.attrs,
        name=vv,
    )


def _mesh_mask(ff, ds):
    """
    Dataset on the grid of ds that is 0 inside the FVCOM mesh and missing outside it
    """
    import netCDF4 as nc4
    from matplotlib.tri import Triangulation

    with nc4.Dataset(ff) as ds_nc:
        lon = np.ma.filled(ds_nc.variables["lon"][:].astype("float64"), np.nan)
        lat = np.ma.filled(ds_nc.variables["lat"][:].astype("float64"), np.nan)
        nv = np.asarray(ds_nc.variables["nv"][:]).astype("int64")
    lon = np.where(lon > 180, lon - 360, lon)
    trifinder = Triangulation(lon, lat, nv.T - 1).get_trifinder()

    ds_xr = ds.to_xarray()
    glon, glat = np.meshgrid(ds_xr["lon"].values, ds_xr["lat"].values)
    glon = np.where(glon > 180, glon - 360, glon)
    inside = trifinder(glon, glat) != -1

    mask = xr.DataArray(
        np.where(inside, 0, np.nan).astype("float32"),
        dims=("lat", "lon"),
        coords={"lat": ds_xr["lat"], "lon": ds_xr["lon"]},
        name="mask",
    )
    return nc.from_xarray(mask.to_dataset())


def fvcom_regrid(ff=None, new_grid=None, vv=None, lons=None, lats=None, res=None, model_res = None, missing = None, levels = None):
    with warnings.catch_warnings(record=True) as w:
        drop_variables = ["siglay", "siglev"]
        ds_xr = xr.open_dataset(ff, drop_variables=drop_variables, decode_times=False)
        long_name = ds_xr[vv].attrs["long_name"]
        z_level = levels is not None and "siglay" in ds_xr[vv].dims
        if z_level:
            # CDO's nearest neighbour regridding would fill missing values below the
            # seabed from the nearest wet node, so use a fill value until afterwards
            da_z = _z_level_array(ds_xr, ff, vv, levels).fillna(z_fill)
            ds1 = nc.from_xarray(da_z)
        else:
            ds1 = nc.from_xarray(ds_xr[vv])
        lon = ds1.to_xarray().lon.values
        lat = ds1.to_xarray().lat.values

        lon_min = float(lon.min())
        lon_max = float(lon.max())
        lat_min = float(lat.min())
        lat_max = float(lat.max())
        # handle longitudes over 180 appropriately
        if lon_min > 180:
            lon_min = lon_min - 360
        if lon_max > 180:
            lon_max = lon_max - 360
        extent = [lon_min, lon_max, lat_min, lat_max]
        session_info["extent"] = extent

        ds1.run()
        if not z_level:
            ds1.nco_command("ncks -d siglay,0,0")
            ds_xr = ds1.to_xarray()
            try:
                ds_xr = ds_xr.squeeze("siglay")
            except:
                pass
            ds1 = nc.from_xarray(ds_xr)
        ds1.subset(variable=vv)
        grid = pd.DataFrame({"lon": lon, "lat": lat})
        lon_max = grid["lon"].max()
        lon_min = grid["lon"].min()
        lat_max = grid["lat"].max()
        lat_min = grid["lat"].min()
        ds1.run()
        out_grid = nc.generate_grid.generate_grid(grid)
        nc.session.append_safe(out_grid)

        ds2 = ds1.copy()
        ds2.run()
        ds2.cdo_command(f"setgrid,{out_grid}")
        if lons is not None:
            ds2.to_latlon(lon=lons, lat=lats, res=res, method="nn")
        else:
            ds2.regrid(new_grid, method="nn")
        if z_level:
            ds2.as_missing([z_fill / 2, z_fill * 2])

        if missing is not None:
            ds2.as_missing(missing)
        ds2.run()
        if model_res is None:
            ds_mask = _mesh_mask(ff, ds2)
        else:
            df_mask = grid.assign(value=1)
            df_mask["lon"] = bin_value(df_mask["lon"], model_res)
            df_mask["lat"] = bin_value(df_mask["lat"], model_res)
            df_mask = df_mask.groupby(["lon", "lat"]).sum().reset_index()
            df_mask = df_mask.set_index(["lat", "lon"])
            ds_mask = nc.from_xarray(df_mask.to_xarray())
            # unique grid description files, so parallel runs do not clash
            fd, griddes_in = tempfile.mkstemp(suffix=".txt")
            os.close(fd)
            fd, griddes_out = tempfile.mkstemp(suffix=".txt")
            os.close(fd)
            os.system(f"cdo griddes {ds_mask[0]} > {griddes_in}")
            # open the text file text.txt and replace the string "generic" with "lonlat"
            with open(griddes_in, "r") as f:
                lines = f.readlines()

            # write line by line to the new grid description
            with open(griddes_out, "w") as f:
                for ll in lines:
                    f.write(ll.replace("generic", "lonlat"))

            ds_mask.cdo_command(f"setgrid,{griddes_out}")
            # ds_mask.to_nc("/tmp/mask.nc")
            ds_mask.regrid(ds2, method="bil")
            # ds_mask.to_nc("/tmp/mask1.nc")
            ds_mask > 0
            ds_mask.as_missing(0)
            ds_mask.set_fill(-9999)
            ds_mask - 1
            # ds_mask.to_nc("/tmp/mask2.nc")
            os.remove(griddes_in)
            os.remove(griddes_out)
            ds_mask.set_fill(-9999)
        ds2 + ds_mask
        ds2.set_longnames({ds2.variables[0]: long_name})

        if lon_min > 180:
            lon_min = lon_min - 360
        if lon_max > 180:
            lon_max = lon_max - 360

        ds2.subset(lon=[lon_min, lon_max], lat=[lat_min, lat_max])

        # if multiple is False:
        return ds2


def fvcom_preprocess(
    variables=None, paths=None, lon_lim=None, lat_lim=None, res=0.05, out_dir=None,
    model_res = None, missing = None, z_level = False, levels = None,
):
    """
    Preprocess FVCOM data for gridding and regridding.

    Parameters
    ----------
    variables : list
        List of variable names to process. This must be the names in the netCDF files.
    paths : list
        List of file paths to the FVCOM data files.
    lon_lim : list
        Minimum and maximum longitudes for regridding.
    lat_lim : list
        Minimum and maximum latitudes for regridding.
    res : list or float
        Resolution for regridding. This defaults to 0.05 degrees, which should be fine for point matchups.
    out_dir : str
        Output directory where processed data will be saved. If None, an error is raised.
    model_res : float
        Approximate native resolution of the FVCOM mesh in degrees, used to build the
        land/sea mask by binning node positions. If None (default), the mask is built from
        the mesh triangles instead: a grid cell is sea if its centre lies inside the mesh.
    missing : float or list
        Value(s) to treat as missing in the FVCOM output.
    z_level : bool
        If False (default), only the surface sigma layer is regridded.
        If True, variables on sigma layers are interpolated onto fixed depth levels
        below the free surface before regridding. Depths above the top layer centre take
        the top layer value, depths between the bottom layer centre and the seabed take
        the bottom layer value, and depths below the seabed are missing.
        Variables without sigma layers are regridded as normal.
        Use ``thickness="z_level"`` when running ``matchup`` on the output.
    levels : list
        Depth levels (m, positive down) to interpolate to. Supplying levels implies z_level=True.
        Defaults to the WOA23 standard depths, down to the maximum model depth.

    """
    # check if out_dir is None
    if out_dir is None:
        raise ValueError("out_dir must be specified")
    # check if out_dir exists
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    # make sure variables is a list
    if not isinstance(variables, list):
        variables = [variables]

    if isinstance(paths, str):
        paths = [paths]

    if not isinstance(z_level, bool):
        raise TypeError("z_level must be a boolean")
    if levels is not None:
        z_level = True
    if z_level:
        if levels is None:
            import netCDF4 as nc4

            with nc4.Dataset(paths[0]) as ds_nc:
                max_depth = float(np.nanmax(ds_nc.variables["h"][:]))
            levels = default_levels(max_depth)
        try:
            levels = np.asarray(levels, dtype="float64").ravel()
        except (TypeError, ValueError):
            raise TypeError("levels must be a list of numbers")
        if len(levels) == 0 or not np.all(np.isfinite(levels)):
            raise ValueError("levels must be a non-empty list of finite numbers")
        if np.any(levels < 0):
            raise ValueError("levels must be depths, i.e. positive down")
        if np.any(np.diff(levels) <= 0):
            raise ValueError("levels must be strictly increasing")
        levels = levels.tolist()
        print(f"Interpolating to depth levels: {levels}")

    with warnings.catch_warnings(record=True) as w:
        ds_all = nc.open_data()
        for vv in variables:
            print(f"Processing variable: {vv}")
            ds_vv = nc.open_data()
            for ff in tqdm(paths):
                ds = xr.open_dataset(
                    ff, drop_variables=["siglay", "siglev"], decode_times=False
                )
                ds_variables = ds.data_vars
                if vv not in ds_variables:
                    continue
                ds2 = fvcom_regrid(
                    ff=ff, new_grid=None, vv=vv, lons=lon_lim, lats=lat_lim, res=res, model_res = model_res, missing = missing,
                    levels = levels,
                )
                ds_vv.append(ds2)
            ds_vv.merge("time")
            ds_all.append(ds_vv)
        ff_out = out_dir + "/" + f"fvcom_values.nc"
        if os.path.exists(ff_out):
            os.remove(ff_out)
        ds_all.merge("variables")
        # ds_all.tmean(["year", "month", "day"])
        ds_all.to_nc(ff_out, zip=True)


# the lon-lat resolution (degrees) of the grid point matchups use, and the depths
# (m) of vertical matchups: from min_depth to max_depth every dz. matchup takes
# each of them as an argument, with these as the defaults
default_res = 0.05
default_min_depth = 0
default_max_depth = 150
default_dz = 5


def _setting(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise TypeError(f"{name} must be a number")
    value = float(value)
    if not np.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if positive and value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return value


def matchup_depths(
    min_depth=default_min_depth, max_depth=default_max_depth, dz=default_dz
):
    """
    Depths of vertical matchups: min_depth to max_depth every dz, as a list.
    The last is the last one at or above max_depth, if dz does not divide the range.
    """
    min_depth = _setting(min_depth, "fvcom_min_depth")
    max_depth = _setting(max_depth, "fvcom_max_depth")
    dz = _setting(dz, "fvcom_dz", positive=True)
    if min_depth < 0:
        raise ValueError("fvcom_min_depth must be a depth, i.e. positive down")
    if max_depth <= min_depth:
        raise ValueError("fvcom_max_depth must be greater than fvcom_min_depth")
    levels = min_depth + dz * np.arange(int(np.floor((max_depth - min_depth) / dz + 1e-9)) + 1)
    return [float(x) for x in np.round(levels, 6)]


def check_matchup_settings(res, min_depth, max_depth, dz):
    """
    The settings of matchup's fvcom_ arguments, checked: (res, depths)
    """
    return _setting(res, "fvcom_res", positive=True), matchup_depths(min_depth, max_depth, dz)


# depth levels used for vertical matchups by default
matchup_levels = matchup_depths()


def _matchup_file(ff, variables, out_dir, vertical, res, lon_lim, lat_lim, levels=None):
    fvcom_preprocess(
        variables=variables,
        paths=[ff],
        lon_lim=lon_lim,
        lat_lim=lat_lim,
        res=res,
        out_dir=out_dir,
        levels=(matchup_levels if levels is None else levels) if vertical else None,
    )


def _run_matchup_file(args):
    """
    Run _matchup_file in a new python process, returning the regridded file.

    Forked worker processes can deadlock once the parent has read netCDF files, and
    spawned ones re-run the user's script, so each file gets a fresh interpreter.
    """
    code = (
        "import json, sys\n"
        "from oceanval.fvcom import _matchup_file\n"
        "_matchup_file(*json.loads(sys.argv[1]))\n"
    )
    # make sure the new process uses this copy of oceanval
    env = dict(os.environ)
    package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = os.pathsep.join(
        [package_dir] + [x for x in [env.get("PYTHONPATH")] if x]
    )
    result = subprocess.run(
        [sys.executable, "-c", code, json.dumps(args)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Unable to regrid {args[0]}:\n{result.stderr[-3000:]}")
    return os.path.join(args[2], "fvcom_values.nc")


def fvcom_matchup_files(
    paths, variables, out_dir, vertical, res, lon_lim, lat_lim, cores=1, levels=None
):
    """
    Regrid FVCOM files for matchup, one output file per input file.

    Surface values are used if vertical is False, otherwise values are interpolated
    to levels, which are 0-150 m every 5 m unless given. Files already done for the
    same settings in this matchup are reused.

    Returns
    -------
    dict
        Mapping of each FVCOM file to its regridded file.
    """
    if not isinstance(res, list):
        res = [res, res]
    res = [float(x) for x in res]
    layer = "z_level" if vertical else "surface"
    variant = f"{layer}_{'_'.join(variables)}_{res[0]:g}x{res[1]:g}"
    if levels is None:
        levels = matchup_levels
    if vertical:
        variant += "_" + hashlib.md5(json.dumps(levels).encode()).hexdigest()[:8]

    registry = session_info.setdefault("fvcom_files", dict())
    todo = [ff for ff in paths if (variant, ff) not in registry]

    if len(todo) > 0:
        description = f"{levels[0]:g}-{levels[-1]:g} m" if vertical else "surface"
        print(
            f"Regridding {len(todo)} FVCOM files ({description}, {res[0]:g} x {res[1]:g} degrees)"
        )
        jobs = dict()
        for ff in todo:
            stem = os.path.splitext(os.path.basename(ff))[0]
            key = hashlib.md5(ff.encode()).hexdigest()[:8]
            jobs[ff] = os.path.join(out_dir, variant, f"{stem}_{key}")

        pbar = tqdm(total=len(todo), position=0, leave=True)
        with ThreadPoolExecutor(max_workers=max(cores, 1)) as executor:
            results = dict()
            for ff in todo:
                results[ff] = executor.submit(
                    _run_matchup_file,
                    [ff, variables, jobs[ff], vertical, res, lon_lim, lat_lim, levels],
                )
            for ff, result in results.items():
                registry[(variant, ff)] = result.result()
                pbar.update(1)
        pbar.close()

    return {ff: registry[(variant, ff)] for ff in paths}


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
