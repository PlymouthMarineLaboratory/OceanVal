"""The GLODAPv3 bottle data: point observations of the interior ocean.

GLODAPv3 (Lange et al., 2026; https://glodap.info) is one file of every
bottle sample from 1181 cruises, 1972 to 2023, with the systematic
differences between cruises adjusted away. Its server cannot cut it to an
area (ERDDAP's copy can, but takes minutes a variable as it reads all of it),
so the whole file is downloaded, zipped (88 MB), and each variable read from
it: the samples with a good WOCE flag (2), and a value that can be real (see
VARIABLES). Their rows are in OceanVal's point format, ices.POINT_COLUMNS.

With a data store (see oceanval.data_store), every variable is kept in it as
the file is first downloaded, so it is downloaded once, and the 945 MB it
unzips to is not kept. Without one, it is downloaded once per process.

Should both the file's servers fail, the variable is asked of ERDDAP for the
years and area being matched, which is slow but needs nothing else.
"""

import contextlib
import io
import os
import shutil
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import numpy as np
import pandas as pd

from oceanval import data_store

POINT_COLUMNS = ["lon", "lat", "year", "month", "day", "depth", "observation"]

# where the file is, in the order it is downloaded from: GEOMAR's mirror has it
# zipped, NCEI's archive (accession 0315582) has it as it is, 945 MB
ZIP_URL = "https://glodap.info/glodap_files/v3/GLODAPv3_Merged_Master_File.nc.zip"
NC_URL = "https://www.ncei.noaa.gov/data/oceans/ncei/ocads/data/0315582/GLODAPv3_Merged_Master_File.nc"
ERDDAP_URL = "https://data.pmel.noaa.gov/pmel/erddap/tabledap/glodapV3"
# the address said to be failing when a download does, for the window
API_BASE = ZIP_URL
TIMEOUT = 600
# a WOCE flag of 2 is a good value (0 is one GLODAP worked out, 9 none)
GOOD_FLAG = 2

# each variable's column in the file, and the range a value must be in to be
# kept (None for no bound). The flags pass some that cannot be real: -900 and
# values of 113 to 16383 ug/kg for chlorophyll (cruises 459 and 263), 181
# phosphates of 5 to 2374 umol/kg from five cruises, oxygen over 600 umol/kg
VARIABLES = {
    "alkalinity": ("talk", None, None),
    "chlorophyll": ("chla", 0, 100),
    "dic": ("tco2", None, None),
    "doc": ("doc", 0, None),
    "nitrate": ("nitrate", None, None),
    "oxygen": ("oxygen", None, 600),
    "ph": ("phtsinsitutp", 6.5, 9),
    "phosphate": ("phosphate", None, 5),
    "silicate": ("silicate", None, None),
}
# the units of each, as the file gives them (doc's attribute is misspelt
# "unts", and chlorophyll's reads "ug kg-1 g")
UNITS = {
    "alkalinity": "µmol/kg",
    "chlorophyll": "µg/kg",
    "dic": "µmol/kg",
    "doc": "µmol/l",
    "nitrate": "µmol/kg",
    "oxygen": "µmol/kg",
    "ph": "total scale",
    "phosphate": "µmol/kg",
    "silicate": "µmol/kg",
}

# what has been read without a store, by process, so a run downloads the file once
_read = {}
_lock = threading.Lock()


def _fetch(url, path):
    """Download url to path."""
    request = urllib.request.Request(url, headers={"User-Agent": "oceanval"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(path, "wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)


def _download(folder):
    """Download the file into folder, unzipped. Returns (its path, the url it came from)."""
    errors = []
    zipped = os.path.join(folder, "GLODAPv3.nc.zip")
    try:
        _fetch(ZIP_URL, zipped)
        with zipfile.ZipFile(zipped) as archive:
            names = [x for x in archive.namelist() if x.endswith(".nc")]
            if len(names) != 1:
                raise ValueError(f"{ZIP_URL} does not hold one netCDF file")
            path = os.path.join(folder, "GLODAPv3.nc")
            with archive.open(names[0]) as source, open(path, "wb") as out:
                shutil.copyfileobj(source, out, 1 << 20)
        os.remove(zipped)
        return path, ZIP_URL
    except Exception as error:
        errors.append(error)
    path = os.path.join(folder, "GLODAPv3.nc")
    try:
        _fetch(NC_URL, path)
        return path, NC_URL
    except Exception as error:
        errors.append(error)
    raise errors[-1] from errors[0]


def _column(f, name):
    """A column of the open file, as floats with NaN for missing."""
    return np.ma.filled(f.variables[name][:].astype(float), np.nan)


def extract(path, variables=None):
    """Each of variables (all of VARIABLES by default) in the GLODAPv3 netCDF
    file at path, as {variable: DataFrame of POINT_COLUMNS}: the samples with
    a good flag and a value in its range, longitudes -180 to 180."""
    import netCDF4

    from oceanval.netcdf_lock import LOCK

    variables = list(VARIABLES) if variables is None else list(variables)
    found = {}
    with LOCK, netCDF4.Dataset(path) as f:
        where = {
            "lon": _column(f, "longitude"),
            "lat": _column(f, "latitude"),
            "year": _column(f, "year"),
            "month": _column(f, "month"),
            "day": _column(f, "day"),
            "depth": _column(f, "depth"),
        }
        placed = np.logical_and.reduce([~np.isnan(x) for x in where.values()])
        for variable in variables:
            column, low, high = VARIABLES[variable]
            values = _column(f, column)
            flags = _column(f, column + "f")
            keep = placed & (flags == GOOD_FLAG) & ~np.isnan(values)
            if low is not None:
                # chlorophyll can be 0; a DOC of 0 is none at all
                keep &= values > low if variable == "doc" else values >= low
            if high is not None:
                keep &= values <= high
            found[variable] = _frame(where, values, keep)
    return found


def _frame(where, values, keep):
    df = pd.DataFrame({name: where[name][keep] for name in POINT_COLUMNS[:-1]})
    df["observation"] = values[keep]
    for name in ("year", "month", "day"):
        df[name] = df[name].astype(int)
    df["lon"] = df["lon"].where(df["lon"] <= 180, df["lon"] - 360)
    return df.reset_index(drop=True)


def download_all(variables=None):
    """Download the file and read variables from it (all of VARIABLES by
    default), the file removed after. Returns ({variable: DataFrame}, url).
    The download callback of data_store.glodapv3."""
    folder = tempfile.mkdtemp(prefix="oceanval_glodapv3_")
    try:
        print("Downloading the GLODAPv3 bottle data (88 MB)")
        path, url = _download(folder)
        return extract(path, variables), url
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _cut(df, start_year, end_year, lon_lim, lat_lim):
    """The rows of df in the years and the box (None for no limit)."""
    keep = (df["year"] >= start_year) & (df["year"] <= end_year)
    if lon_lim is not None:
        keep &= (df["lon"] >= lon_lim[0]) & (df["lon"] <= lon_lim[1])
    if lat_lim is not None:
        keep &= (df["lat"] >= lat_lim[0]) & (df["lat"] <= lat_lim[1])
    return df[keep].reset_index(drop=True)


def _erddap_query(variable, start_year, end_year, lon_lim, lat_lim):
    """The ERDDAP tabledap query for one variable's good samples in the years and box."""
    column = VARIABLES[variable][0]
    constraints = [
        f"{column}f={GOOD_FLAG}",
        f"year>={int(start_year)}",
        f"year<={int(end_year)}",
    ]
    if lon_lim is not None:
        constraints += [f"longitude>={lon_lim[0]}", f"longitude<={lon_lim[1]}"]
    if lat_lim is not None:
        constraints += [f"latitude>={lat_lim[0]}", f"latitude<={lat_lim[1]}"]
    query = ",".join(["longitude", "latitude", "year", "month", "day", "depth", column])
    query += "".join("&" + urllib.parse.quote(x, safe="=") for x in constraints)
    return f"{ERDDAP_URL}.csv?{query}"


def from_erddap(variable, start_year, end_year, lon_lim, lat_lim):
    """One variable's good samples in the years and box, asked of ERDDAP."""
    url = _erddap_query(variable, start_year, end_year, lon_lim, lat_lim)
    print(f"Asking ERDDAP for the GLODAPv3 {variable} data, which can take some minutes")
    request = urllib.request.Request(url, headers={"User-Agent": "oceanval"})
    try:
        with urllib.request.urlopen(request, timeout=3600) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        # ERDDAP says 404 for a query that finds no rows
        if error.code == 404:
            return pd.DataFrame(columns=POINT_COLUMNS)
        raise
    raw = pd.read_csv(io.StringIO(text), skiprows=[1])
    raw.columns = POINT_COLUMNS
    raw = raw.dropna()
    column, low, high = VARIABLES[variable]
    values = raw["observation"].to_numpy(float)
    keep = np.ones(len(raw), dtype=bool)
    if low is not None:
        keep &= values > low if variable == "doc" else values >= low
    if high is not None:
        keep &= values <= high
    where = {name: raw[name].to_numpy(float) for name in POINT_COLUMNS[:-1]}
    return _frame(where, values, keep)


def _without_store(variable):
    """The variable's rows, downloaded once per process when there is no store."""
    with _lock:
        if variable not in _read:
            frames, _ = download_all()
            _read.update(frames)
        return _read[variable]


def download_point_data(recipe, start_year, end_year, lon_lim, lat_lim):
    """A glodapv3 recipe's observations for the years and box, as ices.download_point_data
    gives ICES's. recipe is the dict add_point_comparison keeps, with its "variable"."""
    variable = recipe["variable"]
    if start_year > end_year:
        return pd.DataFrame(columns=POINT_COLUMNS)
    try:
        stored = data_store.glodapv3([variable], download_all)
        df = stored[variable] if stored is not None else _without_store(variable)
    except Exception as error:
        print(f"The GLODAPv3 file could not be downloaded ({type(error).__name__}: {error})")
        with contextlib.suppress(Exception):
            from oceanval.utils import loud_warning

            loud_warning(
                "GLODAPV3 COULD NOT BE DOWNLOADED",
                [f"{type(error).__name__}: {error}", "",
                 "Its data are asked of ERDDAP instead, for this matchup's years and area."],
                warning=f"GLODAPv3 could not be downloaded, so ERDDAP was asked: {error}",
            )
        df = from_erddap(variable, start_year, end_year, lon_lim, lat_lim)
    df = _cut(df, start_year, end_year, lon_lim, lat_lim)
    print(f"{len(df)} GLODAPv3 {variable} observations passed quality control")
    return df


# --------------------------------------------------------------------------
# the GLODAPv2.2016b mapped climatologies (recipe "glodap")


def mapped_depths(path):
    """The depths, in metres, of the levels of a GLODAPv2.2016b mapped file:
    its Depth variable. Their vertical axis is a numbered one (depth_surface),
    which CDO reads as generic levels, not depths."""
    import netCDF4

    from oceanval.netcdf_lock import LOCK

    with LOCK, netCDF4.Dataset(path) as f:
        return [float(x) for x in f.variables["Depth"][:]]


def fix_depths(ds):
    """Give the DataSet ds of a GLODAPv2.2016b mapped file (or a piece of one
    from the data store) its depths, so that it can be matched up through the
    water column as WOA23 is: CDO's setzaxis, from its Depth variable, on
    every variable with that many levels. Run at once, as the zaxis file it
    is given is removed after."""
    depths = mapped_depths(ds[0])
    handle, zaxis = tempfile.mkstemp(prefix="oceanval_glodap_zaxis_", suffix=".txt")
    try:
        with os.fdopen(handle, "w") as out:
            out.write("zaxistype = depth_below_sea\n")
            out.write(f"size = {len(depths)}\n")
            out.write("levels = " + " ".join(f"{x:g}" for x in depths) + "\n")
        ds.cdo_command(f"setzaxis,{zaxis}")
        ds.run()
    finally:
        with contextlib.suppress(OSError):
            os.remove(zaxis)
    return ds
