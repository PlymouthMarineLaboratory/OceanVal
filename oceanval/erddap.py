"""Gridded observations from an ERDDAP server, cut to an area on the server.

One of OceanVal's gridded datasets is read this way:

* **CoralTemp** (recipe ``temperature``, source "coraltemp"): NOAA Coral Reef
  Watch's monthly mean sea surface temperature, v3.1, on a 0.05 degree grid,
  from 1985. A month of the whole globe is about 100 MB, so it is not opened
  over thredds and cropped, as the coarser datasets are (CDO reads every
  global field it is given over OPeNDAP): ERDDAP's griddap is asked for the
  model's area and the years wanted, a year at a time, as a netCDF file.

Nothing is thinned: what is asked for comes at the dataset's own resolution,
so it is meant for regional models. A global model is better validated
against a coarser dataset (OISST, COBE2).

A dataset is known by its griddap address (DATASETS), which is what its
recipe gives as obs_path.
"""

import contextlib
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

# how long a response is waited for: a year of the whole globe is about 1.2 GB
TIMEOUT = 600
# ERDDAP answers 503 ("a (temporary?) problem, wait a minute") when it is
# busy: a request is tried again after each of these waits, in seconds
RETRY_WAITS = (20, 60)
# how long the last time a server has is remembered for, in seconds
LAST_KEPT = 3600

DATASETS = {
    "coraltemp": {
        "url": "https://coastwatch.pfeg.noaa.gov/erddap/griddap/NOAA_DHW_monthly",
        "variable": "sea_surface_temperature",
        # the centres of the grid's outermost cells: anything asked for
        # beyond them is cut to them
        "lon": (-179.975, 179.975),
        "lat": (-89.975, 89.975),
        "res": 0.05,
        "first_year": 1985,
    },
}

# {dataset: (when it was asked, (year, month) of its last time)}
_last = {}


def dataset_of(url):
    """The key of the dataset whose griddap address is url, or None."""
    for key, info in DATASETS.items():
        if url == info["url"]:
            return key
    return None


def _clamp(low, high, limits):
    """low to high cut to limits, or all of limits if nothing of it is left."""
    low, high = max(low, limits[0]), min(high, limits[1])
    if low > high:
        return limits
    return low, high


def query_url(dataset, year, box, last=None):
    """The address of a netCDF file of dataset's variable for year, in box
    (west, east, south, north), from January to December, or to last, the
    (year, month) of the server's last time, if that is in year."""
    info = DATASETS[dataset]
    west, east = _clamp(float(box[0]), float(box[1]), info["lon"])
    south, north = _clamp(float(box[2]), float(box[3]), info["lat"])
    start = f"{year}-01-01T00:00:00Z"
    stop = f"{year}-12-31T23:59:59Z"
    if last is not None and last[0] == year:
        # a time beyond the last is refused (404), so the year stops there
        stop = "last"
    # latitudes are asked for north to south, the order the file has them in
    query = (
        f"{info['variable']}[({start}):1:({stop})]"
        f"[({north:g}):1:({south:g})][({west:g}):1:({east:g})]"
    )
    return f"{info['url']}.nc?{urllib.parse.quote(query, safe='():,')}"


def _request(url):
    return urllib.request.Request(url, headers={"User-Agent": "oceanval"})


def _message(error):
    """The reason ERDDAP gives in the body of an error it answers with."""
    try:
        body = error.read().decode("utf-8", "replace")
    except Exception:
        return ""
    found = body.split('message="', 1)
    text = found[1].rsplit('"', 1)[0] if len(found) > 1 else body
    return " ".join(text.split())[:500]


def _open(url):
    """urlopen of url, tried again while the server says it is busy (503)."""
    for wait in RETRY_WAITS + (None,):
        try:
            return urllib.request.urlopen(_request(url), timeout=TIMEOUT)
        except urllib.error.HTTPError as error:
            if error.code != 503 or wait is None:
                raise
        time.sleep(wait)


def last_month(dataset):
    """(year, month) of the last time dataset's server has, asked of the
    server at most once an hour."""
    found = _last.get(dataset)
    if found is not None and time.time() - found[0] < LAST_KEPT:
        return found[1]
    url = f"{DATASETS[dataset]['url']}.csv?time%5Blast%5D"
    with _open(url) as response:
        lines = response.read().decode("utf-8", "replace").split()
    # a header of the name and of the units, then the time, e.g. 2026-10-16T00:00:00Z
    when = lines[-1]
    last = (int(when[:4]), int(when[5:7]))
    _last[dataset] = (time.time(), last)
    return last


def years(dataset):
    """The years dataset has, from its first to its server's last."""
    return list(range(DATASETS[dataset]["first_year"], last_month(dataset)[0] + 1))


def download(dataset, year, box, path, last=None):
    """Download dataset's year in box to path. An error the server answers
    with is raised with its reason, from the HTTPError (see
    oceanval.server_trouble)."""
    url = query_url(dataset, year, box, last)
    try:
        response = _open(url)
    except urllib.error.HTTPError as error:
        reason = _message(error)
        raise ValueError(
            f"{DATASETS[dataset]['url']} answered HTTP {error.code} for {year}"
            + (f": {reason}" if reason else "")
        ) from error
    with response, open(path, "wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)
    return path


def fetch(dataset, wanted, box):
    """A DataSet of dataset's variable for the years wanted that it has, in
    box, merged in time, in a temporary file nctoolkit looks after. Raises
    ValueError if it has none of them."""
    from oceanval.quiet_nc import nc

    last = last_month(dataset)
    have = set(years(dataset))
    wanted = sorted({int(x) for x in wanted} & have)
    if not wanted:
        raise ValueError(
            f"{dataset} has {min(have)} to {max(have)}, none of the years asked for"
        )
    folder = tempfile.mkdtemp(prefix="oceanval_erddap_")
    try:
        years_kept = []
        for year in wanted:
            path = download(dataset, year, box, os.path.join(folder, f"{dataset}_{year}.nc"), last)
            # ERDDAP gives the times nearest those asked for, so the year's
            # file can have the next year's first month in it (31 December is
            # nearer 16 January than 16 December): only the year is kept, in
            # a copy nctoolkit keeps, so the folder can go
            one = nc.open_data(path, checks=False)
            one.cdo_command(f"-selyear,{year}")
            one.run()
            years_kept.append(one)
        ds = nc.open_data([x[0] for x in years_kept], checks=False)
        if len(years_kept) > 1:
            ds.merge("time")
            ds.run()
        else:
            ds = years_kept[0]
        return ds
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(folder)
