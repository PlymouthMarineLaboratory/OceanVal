"""The time series of analyze(): long-term (annual means) and seasonal (monthly
means), of the model's domain and of the regions of a regional summary, and at
stations.

For each variable and layer (the surface, the bottom, or the water column's vertical
mean or depth integral) the model's mean of each month of each year is worked out first,
as the climatology does. The means over the domain, over each region and at
each station are then taken from it:

- the domain, and the regions, are area-weighted means, worked out as
  validate's regional summaries are: a region's map is put on the model's grid
  by nearest neighbour, the model's field is multiplied by it, and its spatial
  mean taken. A lon/lat box (lon_lim and lat_lim) is cut out of the model's
  domain first, which leaves the stations as they are.
- a station is the nearest cell of the model's grid that has a value.

The long-term series are the means of each year's monthly means, so that a year
with a gap is the mean of its months there are, which the report says.
"""

import os
import warnings

from oceanval.quiet_nc import nc
import numpy as np
import pandas as pd
import xarray as xr

from oceanval import extent
from oceanval.analysis import (
    BOTTOM,
    SHOW_ABSOLUTE,
    SHOW_PERCENTAGE_CHANGE,
    LAYER_TITLES,
    SURFACE,
    WATER_COLUMN,
    _labelled,
    _say,
    _time_dim,
    integral_units,
    monthly_means_of,
    thickness_data,
)

# the model's domain as a series is called this, whatever the regions are
DOMAIN = "Full Domain"
# what each series is of
SERIES_KINDS = ("domain", "region", "station")
# the columns of what is saved, and the files it is saved in
COLUMNS = ["kind", "name", "year", "month", "value"]
# a station further than this many grid cells from a cell with values is not in the model
STATION_CELLS = 2
EARTH_KM = 6371.0


def station_name_ok(name):
    return isinstance(name, str) and bool(name.strip())


def check_stations(stations):
    """stations, as {name: [lon, lat]}, with the longitudes in -180 to 180 and
    the values as floats. Raises ValueError saying what is wrong."""
    if stations is None:
        return {}
    if not isinstance(stations, dict):
        raise ValueError('stations must be a dict of the names of the stations and their [lon, lat], e.g. {"L4": [-4.217, 50.25]}.')
    checked = {}
    for name, position in stations.items():
        if not station_name_ok(name):
            raise ValueError("Each station needs a name.")
        name = name.strip()
        if name in checked:
            raise ValueError(f"There are two stations called {name!r}.")
        try:
            lon, lat = (float(x) for x in position)
            if len(position) != 2 or not (np.isfinite(lon) and np.isfinite(lat)):
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError(f"The position of station {name!r} must be [lon, lat], two numbers.")
        if not -180 <= lon <= 360:
            raise ValueError(f"The longitude of station {name!r} must be between -180 and 360.")
        if not -90 <= lat <= 90:
            raise ValueError(f"The latitude of station {name!r} must be between -90 and 90.")
        checked[name] = [wrap(lon), lat]
    return checked


def wrap(lon):
    """lon in -180 to 180."""
    if -180 <= lon <= 180:
        return float(lon)
    return round(float((lon + 180) % 360 - 180), 10)


def check_limits(lon_lim, lat_lim):
    """lon_lim and lat_lim as lists of two floats, or None. Raises ValueError
    saying what is wrong."""
    if (lon_lim is None) != (lat_lim is None):
        raise ValueError("Give lon_lim and lat_lim together, or neither of them.")
    if lon_lim is None:
        return None, None
    out = []
    for name, limits, low, high in (("lon_lim", lon_lim, -180, 360), ("lat_lim", lat_lim, -90, 90)):
        try:
            first, last = (float(x) for x in limits)
            if len(limits) != 2:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be two numbers, [minimum, maximum].")
        if first < low or last > high:
            raise ValueError(f"{name} must be between {low} and {high}.")
        if first >= last:
            raise ValueError(f"The maximum of {name} must be more than its minimum.")
        out.append([first, last])
    return out[0], out[1]


# ---- the model's grid ----


def _grid(ds_xr, variable):
    """The longitudes and latitudes of the cells of variable, as 2D arrays in
    the order of its (squeezed) spatial dimensions, with the longitudes in -180
    to 180."""
    names = list(ds_xr.coords) + list(ds_xr.variables)
    lon_name = next(name for name in names if "lon" in name.lower())
    lat_name = next(name for name in names if "lat" in name.lower())
    lon, lat = ds_xr[lon_name], ds_xr[lat_name]
    if lon.ndim == 1 and lat.ndim == 1:
        lat2, lon2 = np.meshgrid(lat.values, lon.values, indexing="ij")
        # the variable's own order of its dimensions
        if variable.dims[-1] == lat.dims[0] and variable.dims[-2] == lon.dims[0]:
            lon2, lat2 = lon2.T, lat2.T
    else:
        lon2, lat2 = lon.values, lat.values
    lon2 = (np.asarray(lon2, dtype="float64") + 180) % 360 - 180
    return lon2, np.asarray(lat2, dtype="float64")


def _distance(lon1, lat1, lon2, lat2):
    """Great-circle distance in km, from the position (lon1, lat1) to the
    arrays lon2, lat2, all in degrees."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlam / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _spacing(lon, lat):
    """The usual distance in km between neighbouring cells of a grid."""
    steps = []
    for axis in (0, 1):
        if lon.shape[axis] < 2:
            continue
        first = [slice(None)] * 2
        later = [slice(None)] * 2
        first[axis], later[axis] = slice(None, -1), slice(1, None)
        first, later = tuple(first), tuple(later)
        steps.append(np.nanmedian(_distance(lon[first], lat[first], lon[later], lat[later])))
    return float(max(steps)) if steps else float("nan")


def station_values(path, key, stations, times):
    """The values of key at each station, from the monthly means in path, as
    (rows, found): rows a list of (kind, name, year, month, value), and found
    {name: {"lon", "lat", "distance"}} of the cell each station is in, its
    longitude, latitude and its distance from the station in km. Raises
    ValueError for the stations that are not in the model's domain: further
    than STATION_CELLS cells from a cell with values."""
    rows, found, problems = [], {}, []
    with xr.open_dataset(path, decode_times=False) as ds_xr:
        variable = ds_xr[key]
        time_dim = _time_dim(variable, ds_xr)
        # the horizontal grid only: any level dimension has been taken, or has one level
        extra = [dim for dim in variable.dims if dim != time_dim and variable.sizes[dim] == 1]
        variable = variable.squeeze(extra)
        variable = variable.transpose(time_dim, *[dim for dim in variable.dims if dim != time_dim])
        lon, lat = _grid(ds_xr, variable)
        if variable.ndim != 3 or lon.shape != variable.shape[1:]:
            raise ValueError(f"The grid of {key} cannot be read, to find the stations on it.")
        # which cells have values at any time, a year of times at a time: the series can be too big to hold
        has_values = np.zeros(lon.shape, dtype=bool)
        for first in range(0, variable.shape[0], 12):
            has_values |= np.isfinite(variable.isel({time_dim: slice(first, first + 12)}).values).any(axis=0)
        spacing = _spacing(lon, lat)
        for name, (station_lon, station_lat) in stations.items():
            distance = _distance(station_lon, station_lat, lon, lat)
            distance = np.where(has_values, distance, np.inf)
            if not np.isfinite(distance).any():
                problems.append(f"{name} has no cell with values near it.")
                continue
            index = np.unravel_index(np.argmin(distance), distance.shape)
            km = float(distance[index])
            if np.isfinite(spacing) and km > STATION_CELLS * spacing:
                problems.append(
                    f"{name} is {km:.0f} km from the nearest cell with values, more than "
                    f"{STATION_CELLS} grid cells ({STATION_CELLS * spacing:.0f} km): it is on land, or outside the model."
                )
                continue
            found[name] = {"lon": float(lon[index]), "lat": float(lat[index]), "distance": round(km, 2)}
            # the one cell's series only
            series = np.asarray(variable.isel({variable.dims[1]: int(index[0]), variable.dims[2]: int(index[1])}).values, dtype="float64")
            for (year, month), value in zip(times, series):
                rows.append(("station", name, year, month, float(value) if np.isfinite(value) else np.nan))
    if problems:
        raise ValueError("; ".join(problems))
    return rows, found


# ---- the regions ----


# ---- spatial means, which must not rest on a grid whose cell areas cannot be worked out ----

# the most cells a grid regridded for a spatial mean may have
MOST_CELLS = 4_000_000


def _grid_problem(caught):
    """Whether any of the warnings in caught (from warnings.catch_warnings) say
    that the cell areas the spatial mean is weighted by could not be worked
    out, so constant weights were used. nctoolkit says "Grid cell bounds are
    not available. Constant grid cell weights are used for spatial mean!", and
    passes on CDO's own warnings of the same."""
    for warning in caught:
        text = str(warning.message).lower()
        if "grid cell" in text and ("bounds" in text or "weights" in text or "corner" in text):
            return True
    return False


def _forget_pending_warnings():
    """Empty nctoolkit's list of the warnings it has yet to give.

    nctoolkit keeps each warning it finds in session.session_warnings, gives it
    after every run, and is meant to take it out once it has: but it looks for
    it with `in`, which never finds it (a warning is not equal to the copy that
    comes back from the list's manager), so it stays, and is given again after
    every later run in the Python session. A warning that the cell bounds of one
    grid are missing would then be given for the cells of every grid after it."""
    try:
        from nctoolkit import session

        del session.session_warnings[:]
    except Exception:
        pass


def _mean_once(ds, key, n_times):
    """The spatial mean of key in ds at each time, as (values, problem). ds
    itself is left as it is: the mean is of a copy of it, so that it can be
    calculated again, and problem is whether the weights could not be
    worked out. Every warning is caught and looked at."""
    work = ds.copy()
    _forget_pending_warnings()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        work.spatial_mean()
        work.run()
    # so that this mean's warnings are not given again for the next
    _forget_pending_warnings()
    with xr.open_dataset(work[0], decode_times=False) as means:
        values = np.asarray(means[key].values, dtype="float64")
    return values.reshape(n_times, -1)[:, 0], _grid_problem(caught)


def grid_geometry(path, key):
    """The longitudes and latitudes of the cells of the grid key is on in
    path, wrapped to -180 to 180, as flat arrays, and the shape of the grid, or
    None for a grid of cells with no shape (an unstructured one)."""
    with xr.open_dataset(path, decode_times=False) as ds_xr:
        variable = ds_xr[key]
        time_dim = _time_dim(variable, ds_xr)
        extra = [dim for dim in variable.dims if dim != time_dim and variable.sizes[dim] == 1]
        variable = variable.squeeze(extra)
        names = list(ds_xr.coords) + list(ds_xr.variables)
        lon = ds_xr[next(name for name in names if "lon" in name.lower())]
        lat = ds_xr[next(name for name in names if "lat" in name.lower())]
        if lon.ndim == 1 and lat.ndim == 1 and lon.dims == lat.dims:
            lon2, lat2, shape = lon.values, lat.values, None
        else:
            lon2, lat2 = _grid(ds_xr, variable)
            shape = lon2.shape
    lon2 = (np.asarray(lon2, dtype="float64") + 180) % 360 - 180
    return lon2, np.asarray(lat2, dtype="float64"), shape


def estimate_grid(path, key):
    """The regular lon-lat grid to regrid the grid key is on in path to, so
    that a spatial mean can be worked out on it: {"lon": [min, max], "lat":
    [min, max], "res": [lon resolution, lat resolution]}, all in degrees.

    The resolution is the usual distance between neighbouring cells, in
    longitude and in latitude (the larger over the two directions of the grid,
    which is right for a regular grid and for a curvilinear one); the extent is
    that of the cells with values, with two cells of extra space around it. A grid across
    the whole of the globe, or over the dateline, is all of the longitudes."""
    lon, lat, shape = grid_geometry(path, key)
    finite = np.isfinite(lon) & np.isfinite(lat)
    # the extent of the cells with values, not of the whole grid (see oceanval.extent)
    with xr.open_dataset(path, decode_times=False) as ds_xr:
        found = extent.valid_lonlat(ds_xr, key)
    valid_lon, valid_lat = found if found is not None else (lon[finite], lat[finite])
    valid_lon = (np.asarray(valid_lon, dtype="float64") + 180) % 360 - 180
    lon_min, lon_max = float(np.nanmin(valid_lon)), float(np.nanmax(valid_lon))
    lat_min, lat_max = float(np.nanmin(valid_lat)), float(np.nanmax(valid_lat))
    res_lon = res_lat = 0.0
    if shape is not None:
        for axis in (0, 1):
            if shape[axis] < 2:
                continue
            first, later = [slice(None)] * 2, [slice(None)] * 2
            first[axis], later[axis] = slice(None, -1), slice(1, None)
            first, later = tuple(first), tuple(later)
            step_lon = np.abs((lon[later] - lon[first] + 180) % 360 - 180)
            step_lat = np.abs(lat[later] - lat[first])
            res_lon = max(res_lon, float(np.nanmedian(step_lon)))
            res_lat = max(res_lat, float(np.nanmedian(step_lat)))
    else:
        # cells with no neighbours: the side of the square each would have if they covered the extent evenly
        res_lon = res_lat = float(np.sqrt(max((lon_max - lon_min) * (lat_max - lat_min), 1e-6) / max(lon.size, 1)))
    res_lon = res_lon or res_lat or 1.0
    res_lat = res_lat or res_lon
    res_lon, res_lat = max(res_lon, 0.01), max(res_lat, 0.01)
    spans_globe = lon_max - lon_min > 300
    lon_lim = [-180.0, 180.0] if spans_globe else [lon_min - 2 * res_lon, lon_max + 2 * res_lon]
    lat_lim = [max(lat_min - 2 * res_lat, -90.0), min(lat_max + 2 * res_lat, 90.0)]
    lon_lim = [max(lon_lim[0], -180.0), min(lon_lim[1], 180.0)]
    # not so many cells that the regridding is slow
    cells = (lon_lim[1] - lon_lim[0]) / res_lon * (lat_lim[1] - lat_lim[0]) / res_lat
    if cells > MOST_CELLS:
        scale = float(np.sqrt(cells / MOST_CELLS))
        res_lon, res_lat = res_lon * scale, res_lat * scale
    return {"lon": lon_lim, "lat": lat_lim, "res": [res_lon, res_lat]}


def robust_spatial_mean(ds, key, n_times):
    """The mean of key over the domain of ds at each of its n_times, by
    nctoolkit's spatial_mean, as (values, note).

    The mean is of a copy of ds, with every warning caught. If nctoolkit
    warns that the grid has no cell bounds, so that its cells are weighted
    equally whatever their areas, the mean cannot be relied on. The grid is
    then regridded with to_latlon to a regular lon-lat grid, as fine as the
    grid itself and a little larger, and the mean worked out again, from a copy
    of ds each time, once regridded by nearest neighbour ("nn") and once by
    bilinear interpolation ("bil"). The values are the bilinear ones, and the
    nearest neighbour's are the check of them. note is what the report must say
    about that, "" if it was not done."""
    values, problem = _mean_once(ds, key, n_times)
    if not problem:
        return values, ""
    ds.run()
    grid = estimate_grid(ds[0], key)
    means, still = {}, False
    for method in ("nn", "bil"):
        regridded = ds.copy()
        try:
            regridded.to_latlon(lon=grid["lon"], lat=grid["lat"], res=grid["res"], method=method)
            regridded.run()
        except Exception as error:
            raise ValueError(
                "The grid of the model has no cell bounds, so its spatial mean cannot be weighted by cell "
                f"area, and regridding it to a regular lon-lat grid to work it out by {method} failed: {error}"
            )
        means[method], more = _mean_once(regridded, key, n_times)
        still = still or more
    nn, bil = means["nn"], means["bil"]
    scale = np.nanmean(np.abs(bil)) if np.isfinite(bil).any() else np.nan
    gap = np.nanmax(np.abs(nn - bil)) if np.isfinite(nn - bil).any() else np.nan
    percent = 100 * gap / scale if np.isfinite(gap) and scale and np.isfinite(scale) else np.nan
    note = (
        "The raw grid of the model did not have sufficient information to calculate grid cell areas, so its "
        f"spatial means were calculated after regridding it to a regular lon-lat grid of {grid['res'][0]:.3g} by "
        f"{grid['res'][1]:.3g} degrees, by bilinear interpolation"
    )
    if np.isfinite(percent):
        note += f"; regridding by nearest neighbour instead changed them by at most {percent:.2g}%"
    note += "."
    if still:
        note += " The regridded grid also lacked cell bounds, so these means should be treated with caution."
    return bil, note


def _spatial_means(ds, key, times):
    """The mean of key over the domain of ds at each of times, and what the
    report must say about how it was worked out (see robust_spatial_mean)."""
    return robust_spatial_mean(ds, key, len(times))


def _box(ds, lon_lim, lat_lim):
    """ds in the lon/lat box. A grid the box cannot be cut from is regridded to
    a regular lon-lat grid first."""
    from oceanval import analysis_plots

    out = ds.copy()
    try:
        out.subset(lon=lon_lim, lat=lat_lim)
        out.run()
        return out
    except Exception:
        out = analysis_plots.regridded(ds)
        out.subset(lon=lon_lim, lat=lat_lim)
        out.run()
        return out


def region_masks(region_file):
    """The regions in region_file as one dataset, with 1 where a region is and
    missing elsewhere, on the lon-lat grid of the file itself, and their names:
    (ds_regions, {variable: name}). As validate's regional summaries, but the
    regions stay as they are: it is the model that is regridded to them."""
    ds_regions = nc.open_data(region_file, checks=False)
    ds_regions.as_missing(0)
    ds_regions.set_fill(-9999)
    ds_regions.run()
    contents = ds_regions.contents
    # regions without a long_name are named by their variable
    names = {
        row.variable: (row.long_name if isinstance(row.long_name, str) and row.long_name else row.variable)
        for row in contents.itertuples()
    }
    return ds_regions, names


def region_values(ds, key, times, region_file, lon_lim, lat_lim, masks_for):
    """The mean of key over the domain, and over each region in region_file
    (if any), at each of times, in the lon/lat box if there is one: (rows,
    notes). rows are (kind, name, year, month, value), notes what was left out.
    The domain mean is of ds as it is. The regions are lon-lat grids already, so
    the raw ds is regridded (bilinear) to them once, and each region's mean is
    of that. masks_for keeps the regions of each region_file."""
    notes = []
    if lon_lim is not None:
        ds = _box(ds, lon_lim, lat_lim)
    times = [(time.year, time.month) for time in ds.times]
    values, grid_note = _spatial_means(ds, key, times)
    rows = [("domain", DOMAIN, y, m, v) for (y, m), v in zip(times, values)]
    if grid_note:
        notes.append(grid_note)
    if region_file is None:
        return rows, notes
    if region_file not in masks_for:
        masks_for[region_file] = region_masks(region_file)
    ds_regions, names = masks_for[region_file]
    ds_on_regions = ds.copy()
    ds_on_regions.regrid(region_file, method="bil")
    ds_on_regions.run()
    for variable, name in names.items():
        # the whole model is the Full Domain series: a region of the file of
        # that name would only be the part of the model inside the file
        if name.replace(" ", "").lower() == DOMAIN.replace(" ", "").lower():
            continue
        ds_region = ds_regions.copy()
        ds_region.subset(variables=variable)
        ds_region.run()
        ds_masked = ds_on_regions.copy()
        with warnings.catch_warnings(record=True):
            ds_masked.multiply(ds_region)
            ds_masked.run()
        values, grid_note = _spatial_means(ds_masked, key, times)
        if grid_note and grid_note not in notes:
            notes.append(grid_note)
        if not np.isfinite(values).any():
            notes.append(f"{name} has no cells in the model's domain{' and box' if lon_lim else ''}, so it is left out.")
            continue
        rows.extend(("region", name, y, m, v) for (y, m), v in zip(times, values))
    return rows, notes


# ---- the series ----


def _frame(rows):
    return pd.DataFrame(rows, columns=COLUMNS).astype({"year": "int64", "month": "int64", "value": "float64"})


def _complete_months(frame, start, end):
    """frame with a row for every month from the start to the end year of
    each series in it, with value missing where the output has none, so that
    a gap in the output is a gap in the series."""
    pieces = []
    full = pd.DataFrame(
        [(year, month) for year in range(start, end + 1) for month in range(1, 13)], columns=["year", "month"]
    )
    for (kind, name), part in frame.groupby(["kind", "name"], sort=False):
        part = full.merge(part.drop(columns=["kind", "name"]), how="left", on=["year", "month"])
        part.insert(0, "name", name)
        part.insert(0, "kind", kind)
        pieces.append(part)
    return pd.concat(pieces, ignore_index=True) if pieces else frame


def monthly_series(frame, start, end):
    """The seasonal series: every month of start to end of each series in
    frame, missing where the output has none."""
    return _complete_months(frame[(frame.year >= start) & (frame.year <= end)], start, end)


def annual_series(frame, start, end, months_per_year):
    """The long-term series: for each series in frame, the mean of each year's
    monthly means, and how many months it is the mean of. months_per_year is 12
    for monthly or finer output, and 1 for annual output."""
    frame = frame[(frame.year >= start) & (frame.year <= end)]
    out = []
    for (kind, name), part in frame.groupby(["kind", "name"], sort=False):
        by_year = part.groupby("year")["value"].agg(value="mean", months="count")
        by_year = by_year.reindex(range(start, end + 1))
        by_year["months"] = by_year["months"].fillna(0).astype(int)
        by_year = by_year.reset_index().rename(columns={"index": "year"})
        by_year.insert(0, "name", name)
        by_year.insert(0, "kind", kind)
        out.append(by_year)
    result = pd.concat(out, ignore_index=True)
    result["complete"] = result["months"] >= months_per_year
    return result


def smooth_and_change(annual, window, show):
    """annual (see annual_series) with the rolling mean of window years taken
    of the annual means (each year is the mean of its window, that year and the
    window - 1 before it, so the first years have no value, nor has one whose
    window has a year with none) and then shown as show (see
    analysis.SHOWS): the change from the first year with a value, in the units
    or in per cent of it. A year of the window that is the mean of fewer months
    makes the year one too. Returns (the frame, the names of the series that
    have no percentage change, as their first value is 0)."""
    zero = []
    pieces = []
    for (kind, name), part in annual.groupby(["kind", "name"], sort=False):
        part = part.sort_values("year").copy()
        value = part["value"]
        if window > 1:
            value = value.rolling(window, min_periods=window).mean()
            part["months"] = part["months"].rolling(window, min_periods=window).min().fillna(0).astype(int)
            part["complete"] = part["complete"].astype(int).rolling(window, min_periods=window).min().fillna(0).astype(bool)
        if show != SHOW_ABSOLUTE:
            valid = value.dropna()
            first = valid.iloc[0] if len(valid) else np.nan
            if show == SHOW_PERCENTAGE_CHANGE:
                if first == 0:
                    zero.append(name)
                    value = value * np.nan
                else:
                    value = 100 * (value - first) / abs(first)
            else:
                value = value - first
        part["value"] = value
        pieces.append(part)
    return pd.concat(pieces, ignore_index=True), zero


def months_per_year(times):
    """12 if the output has more than one month in any year, else 1 (annual
    output). times are the (year, month) of the output."""
    by_year = {}
    for year, month in times:
        by_year.setdefault(year, set()).add(month)
    return 12 if any(len(months) > 1 for months in by_year.values()) else 1


def mesh_series(row, files, layer, years, time_shift, area, masks_for):
    """The domain, region and station series of row's variable, raw FVCOM
    output, in layer, from the mean of each month of each year on its own
    mesh (see oceanval.fvcom_climatology), each mean weighted by the areas of
    the mesh's cells (see oceanval.mesh_means): (region rows, notes, station
    rows, found), as region_values and station_values give them."""
    from oceanval import fvcom_climatology, mesh_means
    from oceanval.analysis import file_years

    where = fvcom_climatology.location(files[0], row["parts"])
    series = mesh_means.Series(
        mesh_means.MeshCells(files[0], where), area["region_file"], area["lon_lim"], area["lat_lim"],
        area["stations"], masks_for,
    )
    for time, field in fvcom_climatology.monthly_means(
        files, row["parts"], list(years), time_shift, row.get("as_missing"), row.get("cores", 1), layer, file_years(row)
    ):
        series.add(time, field)
    region_rows, notes = series.region_rows()
    rows, found = series.station_rows() if area["stations"] else ([], {})
    return region_rows, notes, rows, found


def work_out(row, results_dir, thickness, thickness_for, time_shift, area, masks_for):
    """The long-term and seasonal series that row asks for (see
    check_arguments, with its files and times added by analyze), saved in
    results_dir. Returns {"long_term": ..., "seasonal": ...} for what
    INFO_FILE says of each, None for those not asked for. area is
    {"region_file", "lon_lim", "lat_lim", "stations"}."""
    kinds = [kind for kind in ("long_term", "seasonal") if row[kind]]
    start = min(row[kind]["start"] for kind in kinds)
    end = max(row[kind]["end"] for kind in kinds)
    layers = [layer for layer in (SURFACE, BOTTOM, *WATER_COLUMN) if any(layer in row[kind]["layers"] for kind in kinds)]
    files = row["series_files"]
    present = {(int(y), int(m)) for df in row["times"].values() for y, m in zip(df.year, df.month)}
    entries = {kind: {"layers": {}, "regions": [], "notes": [], "stations": {}} for kind in kinds}
    for kind in kinds:
        entries[kind].update(
            {
                "start": row[kind]["start"],
                "end": row[kind]["end"],
                "files": len(row[kind]["files"]),
                "dates": row[kind]["dates"],
            }
        )
        if kind == "long_term":
            entries[kind].update({"window": row[kind]["window"], "show": row[kind]["show"]}
        )
    for layer in layers:
        what = ", ".join(sorted({kind.replace("_", "-") for kind in kinds}))
        _say(f"Working out the {what} series of {row['name']}: {LAYER_TITLES[layer].lower()}")
        units = integral_units(row["units"]) if layer == "integral" else row["units"]
        if row.get("fvcom"):
            region_rows, notes, rows, found = mesh_series(row, files, layer, (start, end), time_shift, area, masks_for)
        else:
            ds_thickness = None
            if layer in WATER_COLUMN:
                if row["pattern"] not in thickness_for:
                    thickness_for[row["pattern"]] = thickness_data(thickness, row["files"])
                ds_thickness = thickness_for[row["pattern"]]
            ds = monthly_means_of(files, row, layer, (start, end), time_shift, row["levels"], ds_thickness)
            _labelled(ds, row, units, LAYER_TITLES[layer].lower())
            ds.run()
            times = [(time.year, time.month) for time in ds.times]
            rows, found = [], {}
            if area["stations"]:
                rows, found = station_values(ds[0], row["key"], area["stations"], times)
            region_rows, notes = region_values(
                ds, row["key"], times, area["region_file"], area["lon_lim"], area["lat_lim"], masks_for
            )
        frame = _frame(region_rows + rows)
        names = list(dict.fromkeys(name for kind, name, *_ in region_rows if kind == "region"))
        for kind in kinds:
            if layer not in row[kind]["layers"]:
                continue
            extra = []
            if kind == "seasonal":
                out = monthly_series(frame, row[kind]["start"], row[kind]["end"])
            else:
                out = annual_series(frame, row[kind]["start"], row[kind]["end"], months_per_year(present))
                # which years have no output, or fewer months, is of the annual means as they are
                domain = out[out.kind == "domain"]
                incomplete = sorted(int(year) for year in domain[~domain.complete & (domain.months > 0)].year)
                missing = sorted(int(year) for year in domain[domain.months == 0].year)
                out, zero = smooth_and_change(out, row[kind]["window"], row[kind]["show"])
                if zero:
                    extra = ["There is no percentage change of " + ", ".join(zero) + ", as its first value is 0."]
            csv = f"{row['key']}_{layer}_{kind}.csv"
            out.to_csv(os.path.join(results_dir, csv), index=False)
            saved = {"title": LAYER_TITLES[layer], "units": units, "csv": csv, "stations": found}
            if kind == "long_term":
                saved["incomplete"] = incomplete
                saved["missing"] = missing
                # what the csv's values are: the annual means, or their change from the first year
                saved["show"] = row[kind]["show"]
                if row[kind]["show"] == SHOW_PERCENTAGE_CHANGE:
                    saved["units"] = "%"
            entries[kind]["layers"][layer] = saved
            entries[kind]["regions"] = names
            entries[kind]["stations"].update(found)
            entries[kind]["notes"] = sorted(set(entries[kind]["notes"]) | set(notes) | set(extra))
    return {kind: entries.get(kind) for kind in ("long_term", "seasonal")}
