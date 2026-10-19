"""Small simulations written to disk, for the tests that read model output."""

import datetime
import os

import numpy as np
import xarray as xr


def write_simulation(root, years=(2011, 2012), tracers=False):
    """A small NEMO-like simulation, filed as root/<year>/01/<file>: a grid_T
    stream of temperature, and with tracers, a ptrc_T stream of nitrate."""
    streams = [("grid_T", "thetao", "sea water potential temperature", "degC")]
    if tracers:
        streams.append(("ptrc_T", "N3_n", "nitrate nitrogen", "mmol N m-3"))
    for year in years:
        folder = os.path.join(root, str(year), "01")
        os.makedirs(folder, exist_ok=True)
        dims = ("time", "deptht", "y", "x")
        for stream, variable, long_name, units in streams:
            dataset = xr.Dataset(
                {
                    variable: (
                        dims,
                        np.random.rand(1, 3, 2, 2).astype("f4"),
                        {"long_name": long_name, "units": units},
                    )
                }
            )
            dataset["time"] = ("time", [0.0], {"units": f"days since {year}-01-01"})
            dataset.to_netcdf(
                os.path.join(folder, f"nemo_1m_{year}0101_{year}0131_{stream}.nc")
            )


# the analysis simulation's grid, levels and their thicknesses
ANALYSIS_LATS = [50.0, 51.0, 52.0, 53.0]
ANALYSIS_LONS = [-4.0, -3.0, -2.0, -1.0, 0.0]
ANALYSIS_LEVELS = [0.5, 2.0, 6.5]
ANALYSIS_THICKNESS = [1.0, 2.0, 7.0]
# thetao at each level, before its month is added
ANALYSIS_LEVEL_VALUES = [10.0, 8.0, 4.0]


def analysis_peak_day(row):
    """The day of the year chl peaks on in a row of the analysis grid."""
    return 60 + 30 * row


def write_analysis_simulation(root, years=(2013, 2014), daily=True, calendar="standard", trend=0.0, east_west=0.0):
    """A small simulation on a regular lon-lat grid with known answers,
    filed as root/<year>/<file>, a file a year for each of two streams:

    - grid_T: temperature (thetao) through ANALYSIS_LEVELS, of
      ANALYSIS_LEVEL_VALUES at each level plus the month (1-12), and the cell
      thickness e3t (ANALYSIS_THICKNESS), and
    - ptrc_T: surface chlorophyll (P1_Chl and P2_Chl, which sum to
      2 + cos(2 pi (d - p) / n)), where d is the day of the year as the
      calendar counts it, n its days, and p analysis_peak_day of the row).

    The output is daily, or monthly (one time mid-month) with daily=False.

    trend adds trend degrees to thetao for each year after the first, and
    east_west adds east_west degrees for each column east of the third (so
    the west two columns, which ANALYSIS_REGIONS calls "West", are
    -2 and -1 times it, and the east two, "East", 1 and 2 times it).
    """
    import cftime

    n_days = {"360_day": 360, "noleap": 365}.get(calendar, 365)
    for year in years:
        folder = os.path.join(root, str(year))
        os.makedirs(folder, exist_ok=True)
        if daily:
            start = cftime.datetime(year, 1, 1, calendar=calendar)
            dates = []
            date = start
            while date.year == year:
                dates.append(date)
                date = date + datetime.timedelta(days=1)
        else:
            dates = [cftime.datetime(year, month, 15, calendar=calendar) for month in range(1, 13)]
        units = f"days since {year}-01-01"
        offsets = cftime.date2num(dates, units, calendar=calendar)
        months = np.array([date.month for date in dates])
        day_of_year = np.array([date.dayofyr for date in dates])
        coords = {
            "time": ("time", offsets, {"units": units, "calendar": calendar, "standard_name": "time"}),
            "deptht": ("deptht", ANALYSIS_LEVELS, {"units": "m", "positive": "down", "axis": "Z"}),
            "lat": ("lat", ANALYSIS_LATS, {"units": "degrees_north", "standard_name": "latitude"}),
            "lon": ("lon", ANALYSIS_LONS, {"units": "degrees_east", "standard_name": "longitude"}),
        }
        shape = (len(dates), len(ANALYSIS_LEVELS), len(ANALYSIS_LATS), len(ANALYSIS_LONS))
        columns = np.arange(len(ANALYSIS_LONS)) - len(ANALYSIS_LONS) // 2
        thetao = (
            np.array(ANALYSIS_LEVEL_VALUES)[None, :, None, None]
            + months[:, None, None, None]
            + trend * (year - years[0])
            + east_west * columns[None, None, None, :]
        ) * np.ones(shape)
        e3t = np.array(ANALYSIS_THICKNESS)[None, :, None, None] * np.ones(shape)
        xr.Dataset(
            {
                "thetao": (("time", "deptht", "lat", "lon"), thetao.astype("f4"),
                           {"long_name": "sea water potential temperature", "units": "degC"}),
                "e3t": (("time", "deptht", "lat", "lon"), e3t.astype("f4"),
                        {"long_name": "T-cell thickness", "units": "m"}),
            },
            coords=coords,
        ).to_netcdf(os.path.join(folder, f"sim_{'1d' if daily else '1m'}_{year}0101_{year}1231_grid_T.nc"))
        peaks = np.array([analysis_peak_day(row) for row in range(len(ANALYSIS_LATS))])
        chl = 2 + np.cos(2 * np.pi * (day_of_year[:, None] - peaks[None, :]) / n_days)
        chl = chl[:, :, None] * np.ones((1, 1, len(ANALYSIS_LONS)))
        surface = {name: coords[name] for name in ("time", "lat", "lon")}
        xr.Dataset(
            {
                "P1_Chl": (("time", "lat", "lon"), (chl / 2).astype("f4"),
                           {"long_name": "diatom chlorophyll", "units": "mg Chl m-3"}),
                "P2_Chl": (("time", "lat", "lon"), (chl / 2).astype("f4"),
                           {"long_name": "nanophytoplankton chlorophyll", "units": "mg Chl m-3"}),
            },
            coords=surface,
        ).to_netcdf(os.path.join(folder, f"sim_{'1d' if daily else '1m'}_{year}0101_{year}1231_ptrc_T.nc"))


def write_curvilinear_simulation(root, years=(2013,)):
    """A monthly simulation of one surface variable, sst, on a curvilinear
    grid, with its longitudes and latitudes in 2D and no cell bounds, so that
    the cell areas a spatial mean is weighted by cannot be worked out. The grid
    is tilted, of cells about 0.3 degrees of longitude and 0.25 of latitude, and
    sst is 10 plus the month everywhere."""
    ny, nx = 20, 30
    y, x = np.meshgrid(np.arange(ny), np.arange(nx), indexing="ij")
    lon = -10 + 0.3 * x + 0.05 * y
    lat = 45 + 0.25 * y - 0.03 * x
    for year in years:
        folder = os.path.join(root, str(year))
        os.makedirs(folder, exist_ok=True)
        months = np.arange(1, 13)
        sst = (10.0 + months)[:, None, None] * np.ones((12, ny, nx))
        xr.Dataset(
            {"sst": (("time", "y", "x"), sst.astype("f4"),
                     {"long_name": "sea surface temperature", "units": "degC", "coordinates": "nav_lon nav_lat"})},
            coords={
                "nav_lon": (("y", "x"), lon, {"units": "degrees_east", "standard_name": "longitude"}),
                "nav_lat": (("y", "x"), lat, {"units": "degrees_north", "standard_name": "latitude"}),
                "time": ("time", (months - 1) * 30.0 + 15.0,
                         {"units": f"days since {year}-01-01", "calendar": "360_day", "standard_name": "time"}),
            },
        ).to_netcdf(os.path.join(folder, f"curv_1m_{year}0101_{year}1230_grid_T.nc"))


def write_analysis_regions(path):
    """A regions file for the analysis simulation: "West", its first two
    columns, and "East", its last two, with the middle one in neither."""
    lat, lon = np.array(ANALYSIS_LATS), np.array(ANALYSIS_LONS)
    columns = np.arange(len(lon)) - len(lon) // 2
    ones = np.ones((len(lat), 1))
    xr.Dataset(
        {
            "west": (("lat", "lon"), np.where(columns[None, :] < 0, 1.0, np.nan) * ones, {"long_name": "West"}),
            "east": (("lat", "lon"), np.where(columns[None, :] > 0, 1.0, np.nan) * ones, {"long_name": "East"}),
        },
        coords={
            "lat": ("lat", lat, {"units": "degrees_north", "standard_name": "latitude"}),
            "lon": ("lon", lon, {"units": "degrees_east", "standard_name": "longitude"}),
        },
    ).to_netcdf(path)


def write_fvcom(root):
    """A month of raw FVCOM output, filed as root/2012/01/<file>."""
    folder = os.path.join(root, "2012", "01")
    os.makedirs(folder)
    dataset = xr.Dataset(
        {
            "nv": (("three", "nele"), np.array([[1, 1], [2, 4], [4, 3]], dtype="i4")),
            "lon": ("node", np.array([-5.0, -4.9, -5.0, -4.9], dtype="f4")),
            "lat": ("node", np.array([50.0, 50.0, 50.1, 50.1], dtype="f4")),
            "h": ("node", np.full(4, 20.0, dtype="f4")),
            "temp": (
                ("time", "siglay", "node"),
                np.random.rand(1, 5, 4).astype("f4"),
                {"long_name": "temperature", "units": "degree_C"},
            ),
        }
    )
    dataset["time"] = (
        "time",
        np.array([55927.0]),
        {"units": "days since 1858-11-17 00:00:00"},
    )
    dataset.to_netcdf(os.path.join(folder, "run_avg_0001.nc"))
