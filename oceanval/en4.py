"""EN4 profiles: quality controlled temperature and salinity profiles of the
global ocean, from the Met Office Hadley Centre.

EN.4.2.2 (Good, Martin and Rayner, 2013; https://www.metoffice.gov.uk/hadobs/en4/)
gathers the profiles of the World Ocean Database, GTSPP, Argo and ASBO, from
1900 to the present, quality controlled. Its profiles come as one zip a year,
for the whole ocean, of twelve monthly netCDF files: the server cannot cut a
region out, so a year is downloaded whole, 0.5 to 0.9 GB a year since 2012
(MB_PER_YEAR). The profiles with Gouretski and Reseghetti (2010) XBT and
Gouretski and Cheng (2020) MBT corrections (g10) are used.

A level is kept when the profile's position and its temperature profile are
accepted (QC '1', not '4' reject), and the level's depth and temperature are
too; its salinity is kept when the salinity profile and level are accepted,
and is missing otherwise. A profile that EN4 flags as a duplicate of a better
one nearby (bit 2 of QC_FLAGS_PROFILES) is left out. The temperature is
potential temperature (POTM_CORRECTED), the salinity practical
(PSAL_CORRECTED), the depth in metres (DEPH_CORRECTED).

Without a data store each year is downloaded once per process, and only the
profiles in the box asked for are kept from it.
"""

import atexit
import contextlib
import os
import shutil
import tempfile
import threading
import zipfile

import numpy as np
import pandas as pd

VERSION = "EN.4.2.2"
CORRECTIONS = "g10"
SITE = "https://www.metoffice.gov.uk/hadobs/en4/"
# the address said to be failing when a download does, for the window
API_BASE = SITE
TIMEOUT = 600
FIRST_YEAR = 1900

# the columns of a year's profiles: the point format, ices.POINT_COLUMNS, with
# the temperature as the observation, and the salinity beside it
POINT_COLUMNS = ["lon", "lat", "year", "month", "day", "depth", "observation"]
COLUMNS = POINT_COLUMNS + ["salinity"]

# the size of each year's zip, in MB, as the server gave it on 2026-11-17 (the
# current year's grows each month); a year not here is taken to be as big as
# the last one that is
MB_PER_YEAR = {
    1900: 0, 1901: 0, 1902: 0, 1903: 0, 1904: 0, 1905: 0, 1906: 0, 1907: 0, 1908: 0, 1909: 0,
    1910: 0, 1911: 1, 1912: 0, 1913: 0, 1914: 0, 1915: 0, 1916: 0, 1917: 0, 1918: 0, 1919: 0,
    1920: 0, 1921: 0, 1922: 0, 1923: 0, 1924: 1, 1925: 1, 1926: 1, 1927: 1, 1928: 1, 1929: 1,
    1930: 1, 1931: 1, 1932: 1, 1933: 1, 1934: 1, 1935: 2, 1936: 2, 1937: 2, 1938: 2, 1939: 2,
    1940: 1, 1941: 2, 1942: 2, 1943: 3, 1944: 6, 1945: 7, 1946: 5, 1947: 8, 1948: 10, 1949: 12,
    1950: 18, 1951: 18, 1952: 24, 1953: 21, 1954: 19, 1955: 16, 1956: 18, 1957: 21, 1958: 24, 1959: 23,
    1960: 26, 1961: 28, 1962: 31, 1963: 36, 1964: 35, 1965: 37, 1966: 40, 1967: 43, 1968: 43, 1969: 49,
    1970: 42, 1971: 48, 1972: 52, 1973: 51, 1974: 59, 1975: 46, 1976: 44, 1977: 50, 1978: 62, 1979: 57,
    1980: 56, 1981: 62, 1982: 55, 1983: 68, 1984: 71, 1985: 79, 1986: 87, 1987: 100, 1988: 92, 1989: 87,
    1990: 129, 1991: 148, 1992: 164, 1993: 221, 1994: 225, 1995: 245, 1996: 205, 1997: 211, 1998: 221, 1999: 207,
    2000: 195, 2001: 194, 2002: 166, 2003: 185, 2004: 260, 2005: 308, 2006: 288, 2007: 346, 2008: 378, 2009: 381,
    2010: 396, 2011: 388, 2012: 528, 2013: 692, 2014: 780, 2015: 727, 2016: 869, 2017: 838, 2018: 925, 2019: 894,
    2020: 699, 2021: 667, 2022: 681, 2023: 683, 2024: 690, 2025: 720, 2026: 571,
}

# bit 2 of QC_FLAGS_PROFILES: another profile within 0.2 degrees and 1 hour looks better
DUPLICATE_BIT = 1 << 2
ACCEPT = b"1"


def url(year):
    """The address of a year's zip of profiles: those to 2020 are in a folder
    of their own, the later ones beside it."""
    name = f"{VERSION}.profiles.{CORRECTIONS}.{int(year)}.zip"
    if int(year) <= 2020:
        return f"{SITE}data/en4-2-1/{VERSION}/{name}"
    return f"{SITE}data/en4-2-1/{name}"


def year_mb(year):
    """The size of a year's zip, in MB (see MB_PER_YEAR)."""
    year = int(year)
    if year in MB_PER_YEAR:
        return MB_PER_YEAR[year]
    known = [x for x in MB_PER_YEAR if x <= year]
    return MB_PER_YEAR[max(known)] if known else 0


def total_gb(start_year, end_year):
    """Roughly how many GB downloading the years start_year to end_year takes."""
    if start_year is None or end_year is None or int(start_year) > int(end_year):
        return 0.0
    return sum(year_mb(x) for x in range(int(start_year), int(end_year) + 1)) / 1000


def sizes():
    """MB_PER_YEAR as a dict of str years, for the window."""
    return {str(year): mb for year, mb in MB_PER_YEAR.items()}


def _accepted(flags):
    """Whether each of an array of QC characters is accepted."""
    return np.asarray(flags) == ACCEPT


def _filled(variable, index):
    """A 2D variable's rows index, as floats with NaN for missing."""
    return np.ma.filled(np.ma.asarray(variable[index, :]).astype(float), np.nan)


def read_month(source, lon_lim=None, lat_lim=None):
    """The levels of one monthly profile file that pass quality control, in
    the box lon_lim, lat_lim (None for all), as a dataframe of COLUMNS. source
    is the file's path or its bytes."""
    import netCDF4

    from oceanval.netcdf_lock import LOCK

    in_memory = isinstance(source, (bytes, bytearray))
    with LOCK, (netCDF4.Dataset("en4", memory=source) if in_memory else netCDF4.Dataset(source)) as f:
        f.set_auto_mask(True)
        lon = np.asarray(f["LONGITUDE"][:], dtype=float)
        lon = np.where(lon > 180, lon - 360, lon)
        lat = np.asarray(f["LATITUDE"][:], dtype=float)
        keep = _accepted(f["POSITION_QC"][:]) & _accepted(f["PROFILE_POTM_QC"][:])
        keep &= (np.asarray(f["QC_FLAGS_PROFILES"][:]) & DUPLICATE_BIT) == 0
        keep &= np.asarray(f["PROFILE_DEPH_QC"][:]) != b"4"
        keep &= np.isfinite(lon) & np.isfinite(lat)
        if lon_lim is not None:
            keep &= (lon >= lon_lim[0]) & (lon <= lon_lim[1])
        if lat_lim is not None:
            keep &= (lat >= lat_lim[0]) & (lat <= lat_lim[1])
        index = np.flatnonzero(keep)
        if len(index) == 0:
            return pd.DataFrame(columns=COLUMNS)
        juld = np.asarray(f["JULD"][index], dtype=float)
        depth = _filled(f["DEPH_CORRECTED"], index)
        temperature = _filled(f["POTM_CORRECTED"], index)
        salinity = _filled(f["PSAL_CORRECTED"], index)
        good_t = _accepted(f["POTM_CORRECTED_QC"][index, :]) & (np.asarray(f["DEPH_CORRECTED_QC"][index, :]) != b"4")
        good_s = _accepted(f["PSAL_CORRECTED_QC"][index, :]) & _accepted(f["PROFILE_PSAL_QC"][index])[:, None]
    good_t &= np.isfinite(depth) & np.isfinite(temperature) & (depth >= 0)
    salinity = np.where(good_s & np.isfinite(salinity), salinity, np.nan)
    rows, levels = np.nonzero(good_t)
    if len(rows) == 0:
        return pd.DataFrame(columns=COLUMNS)
    when = np.datetime64("1950-01-01T00:00") + np.round(juld * 1440).astype("timedelta64[m]")
    when = pd.DatetimeIndex(when[rows])
    return pd.DataFrame(
        {
            "lon": lon[index][rows],
            "lat": lat[index][rows],
            "year": when.year.astype(int),
            "month": when.month.astype(int),
            "day": when.day.astype(int),
            "depth": depth[rows, levels],
            "observation": temperature[rows, levels],
            "salinity": salinity[rows, levels],
        }
    )


def read_zip(path, lon_lim=None, lat_lim=None, on_month=None):
    """The levels of a year's zip that pass quality control in the box, as
    one dataframe of COLUMNS. on_month(done, total) is told after each month."""
    frames = []
    with zipfile.ZipFile(path) as archive:
        names = sorted(x for x in archive.namelist() if x.endswith(".nc"))
        for i, name in enumerate(names):
            frames.append(read_month(archive.read(name), lon_lim, lat_lim))
            if on_month is not None:
                on_month(i + 1, len(names))
    frames = [x for x in frames if len(x) > 0]
    if len(frames) == 0:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat(frames, ignore_index=True)


def fetch(year, path):
    """Download a year's zip to path, showing the MB downloaded."""
    import requests
    from tqdm import tqdm

    with requests.get(url(year), stream=True, timeout=TIMEOUT, headers={"User-Agent": "oceanval"}) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0)) or None
        with open(path, "wb") as out, tqdm(
            total=total, unit="B", unit_scale=True, desc=f"EN4 {int(year)}", leave=False
        ) as bar:
            for chunk in response.iter_content(chunk_size=1 << 20):
                out.write(chunk)
                bar.update(len(chunk))


# the profiles read in this run without a data store, as {(year, box): their rows}
_kept = {}
_lock = threading.Lock()
_folder = []


def clear_kept():
    """Forget the years read (a new run, or a test)."""
    _kept.clear()
    for folder in _folder:
        shutil.rmtree(folder, ignore_errors=True)
    del _folder[:]


atexit.register(clear_kept)


def _year_rows(year, lon_lim, lat_lim):
    """A year's levels in the box, downloaded once per process for each box."""
    box = tuple(float(x) for x in (*lon_lim, *lat_lim))
    with _lock:
        if (year, box) in _kept:
            return _kept[(year, box)]
        if not _folder:
            _folder.append(tempfile.mkdtemp(prefix="oceanval_en4_"))
        path = os.path.join(_folder[0], f"en4_{year}.zip")
        print(f"Downloading EN4 profiles for {year} (about {year_mb(year)} MB, the whole ocean)")
        fetch(year, path)
        try:
            rows = read_zip(path, lon_lim, lat_lim, on_month=lambda done, total: print(f"EN4 {year}: {done}/{total} months read"))
        finally:
            with contextlib.suppress(OSError):
                os.remove(path)
        _kept[(year, box)] = rows
        return rows


def download_point_data(recipe, start_year, end_year, lon_lim, lat_lim):
    """The EN4 levels in the years and box, as ices.download_point_data gives
    ICES's. For stratification (recipe["variable"]), the temperature as the
    observation with the salinity beside it (COLUMNS); for temperature or
    salinity, that alone as the observation (POINT_COLUMNS)."""
    variable = recipe.get("variable", "stratification")
    df = _levels(start_year, end_year, lon_lim, lat_lim)
    if variable == "temperature":
        df = df.loc[:, POINT_COLUMNS]
    elif variable == "salinity":
        df = (
            df.drop(columns="observation")
            .rename(columns={"salinity": "observation"})
            .dropna(subset=["observation"])
            .loc[:, POINT_COLUMNS]
            .reset_index(drop=True)
        )
    print(f"{len(df)} EN4 {variable} observations passed quality control")
    return df


def _levels(start_year, end_year, lon_lim, lat_lim):
    """The EN4 levels in the years and box, with the temperature and the salinity (COLUMNS)."""
    if start_year > end_year:
        return pd.DataFrame(columns=COLUMNS)
    start_year = max(int(start_year), FIRST_YEAR)
    years = range(start_year, int(end_year) + 1)
    print(
        f"EN4 is downloaded a year at a time, for the whole ocean: about {total_gb(start_year, end_year):.1f} GB "
        f"for {start_year}-{int(end_year)}"
    )
    frames = [_year_rows(year, lon_lim, lat_lim) for year in years]
    frames = [x for x in frames if len(x) > 0]
    if len(frames) == 0:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat(frames, ignore_index=True)
