"""A small model for the stratification tests, written with xarray: its
temperature and salinity are linear in depth, so the model at any depth between
its cell centres is known exactly, and casts sampled from it give the model
back. On s-levels (with a sixth level under the sea bed that is 0, as COMFORT's
AMM7 has) or on z-levels (with the cells under the sea bed missing).
"""

import numpy as np
import pandas as pd
import xarray as xr

from oceanval import ices, stratification

LONS = [-5.0, -4.9, -4.8, -4.7]
LATS = [50.0, 50.1, 50.2, 50.3]
# the water depth of each cell (lat, lon); NaN is land
DEPTHS = np.array([
    [30.0, 45.0, 60.0, np.nan],
    [40.0, 55.0, 70.0, 80.0],
    [50.0, 65.0, 75.0, 90.0],
    [35.0, 60.0, 85.0, 100.0],
])
WET_LEVELS = 5
DAYS = 31


def temperature(depth, day):
    return 18.0 + 0.01 * day - 0.1 * depth


def salinity(depth):
    return 34.0 + 0.01 * depth


def _s_levels(sim_dir):
    """A model on s-levels: five wet levels of equal thickness, and a sixth under
    the sea bed whose temperature and salinity are 0 (not missing) but whose
    thickness is not, as NEMO's last level in COMFORT's AMM7. Daily, July 2012."""
    thickness = np.repeat((DEPTHS / WET_LEVELS)[None], WET_LEVELS, axis=0)
    thickness = np.concatenate([thickness, np.where(np.isnan(DEPTHS), np.nan, 10.0)[None]])
    centres = np.cumsum(thickness, axis=0) - thickness / 2
    day = np.arange(1, DAYS + 1)[:, None, None, None]
    t = np.where(np.arange(6)[None, :, None, None] < WET_LEVELS, temperature(centres[None], day), 0.0)
    s = np.where(np.arange(6)[None, :, None, None] < WET_LEVELS, salinity(centres[None]) + 0 * day, 0.0)
    land = np.isnan(DEPTHS)[None, None]
    t = np.where(land, np.nan, t)
    s = np.where(land, np.nan, s)
    e3t = np.repeat(thickness[None], DAYS, axis=0)
    _write(sim_dir, t, s, np.arange(1.0, 7.0), e3t=e3t)
    return centres


def _z_levels(sim_dir):
    """A model on z-levels at 2.5, 7.5, ... m (cells 5 m thick), with the cells
    under the sea bed missing: each cell's deepest wet level is the one whose
    cell holds its water depth (DEPTHS)."""
    levels = np.arange(2.5, 100.0, 5.0)
    day = np.arange(1, DAYS + 1)[:, None, None, None]
    wet = (levels[:, None, None] - 2.5) < np.nan_to_num(DEPTHS, nan=-1)[None]
    z = levels[:, None, None] + 0 * DEPTHS[None]
    t = np.where(wet[None], temperature(z[None], day), np.nan)
    s = np.where(wet[None], salinity(z[None]) + 0 * day, np.nan)
    _write(sim_dir, t, s, levels)
    return levels, wet


def _write(sim_dir, t, s, levels, e3t=None):
    times = pd.date_range("2012-07-01 12:00", periods=DAYS, freq="D")
    dims = ("time_counter", "deptht", "lat", "lon")
    variables = {
        "votemper": (dims, t.astype("float32"), {"units": "degC", "long_name": "temperature"}),
        "vosaline": (dims, s.astype("float32"), {"units": "1e-3", "long_name": "salinity"}),
    }
    if e3t is not None:
        variables["e3t"] = (dims, e3t.astype("float32"), {"units": "m", "standard_name": "cell_thickness"})
    ds = xr.Dataset(
        variables,
        coords={
            "time_counter": times, "deptht": ("deptht", levels, {"units": "m", "positive": "down"}),
            "lat": ("lat", LATS, {"units": "degrees_north"}), "lon": ("lon", LONS, {"units": "degrees_east"}),
        },
    )
    encoding = {name: {"_FillValue": 1e20} for name in variables}
    folder = sim_dir / "2012" / "07"
    folder.mkdir(parents=True)
    ds.to_netcdf(folder / "amm7_1d_20120701_20120731_grid_T.nc", encoding=encoding)


# the casts: (lon index, lat index, day), each sampled at 8 depths from the top cell's centre to the bottom one's
CASTS = [(0, 0, 5), (1, 0, 5), (2, 1, 12), (3, 3, 20), (1, 2, 20)]


def _casts(top, bottom):
    rows = []
    for i, j, day in CASTS:
        for depth in np.linspace(top(i, j), bottom(i, j), 8):
            rows.append((LONS[i], LATS[j], 2012, 7, day, float(depth), temperature(depth, day), salinity(depth)))
    return pd.DataFrame(rows, columns=ices.CAST_COLUMNS)


def _expected(top, bottom, water):
    rows = []
    for i, j, day in CASTS:
        depths = np.linspace(top(i, j), bottom(i, j), 8)
        found = stratification.profile_measures(
            depths, temperature(depths, day), salinity(depths), top(i, j), bottom(i, j), water(i, j), LONS[i], LATS[j]
        )
        rows += [(LONS[i], LATS[j], day, measure, value) for measure, value in found.items()]
    return pd.DataFrame(rows, columns=["lon", "lat", "day", "measure", "expected"]).dropna()
