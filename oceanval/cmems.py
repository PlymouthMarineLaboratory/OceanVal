"""Gridded observations from Copernicus Marine, cut to an area on the server.

One of OceanVal's gridded datasets is read this way:

* **OSTIA** (recipe ``temperature``, source "ostia"): the Met Office's OSTIA
  reprocessed foundation sea surface temperature (Copernicus Marine product
  SST_GLO_SST_L4_REP_OBSERVATIONS_010_011), daily on a 0.05 degree grid from
  October 1981. Copernicus Marine has no OPeNDAP: its own toolbox,
  ``copernicusmarine``, is asked for the model's area a month at a time, and
  each month's days are averaged into a monthly mean (in degC) at once, so
  only the monthly means are kept and matched, as for the other SST datasets.

The toolbox is not one of OceanVal's requirements: it is needed only for
these datasets, with a free Copernicus Marine account. Its own login is used
(``copernicusmarine login``, which saves it in ~/.copernicusmarine, or the
COPERNICUSMARINE_SERVICE_USERNAME and COPERNICUSMARINE_SERVICE_PASSWORD
environment variables): OceanVal never asks for or keeps a password.

Nothing is thinned: what is asked for comes at the dataset's own resolution,
so it is meant for regional models. A global model is better validated
against a coarser dataset (OISST, COBE2).

A dataset is known by the address of its product page (DATASETS), which is
what its recipe gives as obs_path.
"""

import calendar
import contextlib
import datetime
import logging
import os
import shutil
import tempfile
import time

# how long the last time a dataset has is remembered for, in seconds
LAST_KEPT = 3600

DATASETS = {
    "ostia": {
        "url": "https://data.marine.copernicus.eu/product/SST_GLO_SST_L4_REP_OBSERVATIONS_010_011",
        "dataset_id": "METOFFICE-GLO-SST-L4-REP-OBS-SST",
        "variable": "analysed_sst",
        # the centres of the grid's outermost cells: anything asked for
        # beyond them is cut to them
        "lon": (-179.975, 179.975),
        "lat": (-89.975, 89.975),
        "res": 0.05,
        # it starts in October 1981: 1982 is its first whole year
        "first_year": 1982,
        # its values are in kelvin, and are given in degC
        "adder": -273.15,
        "units": "degC",
    },
}

INSTALL = (
    "OSTIA comes from Copernicus Marine, which needs its copernicusmarine toolbox: "
    "install it with `conda install -c conda-forge copernicusmarine` or `pip install copernicusmarine`."
)
LOGIN = (
    "OSTIA comes from Copernicus Marine, which needs a free account "
    "(https://data.marine.copernicus.eu/register): run `copernicusmarine login` once, or set "
    "COPERNICUSMARINE_SERVICE_USERNAME and COPERNICUSMARINE_SERVICE_PASSWORD."
)

# {dataset: (when it was asked, (year, month) of its last whole month)}
_last = {}
# True once the login has been found to work, in this process
_logged_in = []


def dataset_of(url):
    """The key of the dataset whose product page is url, or None."""
    for key, info in DATASETS.items():
        if url == info["url"]:
            return key
    return None


def _toolbox():
    """The copernicusmarine module, or a ValueError saying how to install it."""
    try:
        import copernicusmarine
    except ImportError as error:
        raise ValueError(INSTALL) from error
    return copernicusmarine


@contextlib.contextmanager
def _quiet():
    """The toolbox's INFO lines (its dataset version, the size of the
    download) not shown."""
    logger = logging.getLogger("copernicusmarine")
    level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.setLevel(level)


def check_login():
    """Raise a ValueError, saying what to do, if the toolbox is missing or
    has no working Copernicus Marine login. Asked of the server once a
    process."""
    toolbox = _toolbox()
    if _logged_in:
        return
    try:
        with _quiet():
            ok = toolbox.login(check_credentials_valid=True)
    except Exception as error:
        raise ValueError(f"{LOGIN} ({type(error).__name__}: {error})") from error
    if not ok:
        raise ValueError(LOGIN)
    _logged_in.append(True)


def _clamp(low, high, limits):
    """low to high cut to limits, or all of limits if nothing of it is left."""
    low, high = max(low, limits[0]), min(high, limits[1])
    if low > high:
        return limits
    return low, high


def _whole_month(when):
    """(year, month) of the last whole month there is on or before the day when."""
    if when.day == calendar.monthrange(when.year, when.month)[1]:
        return when.year, when.month
    if when.month == 1:
        return when.year - 1, 12
    return when.year, when.month - 1


def last_month(dataset):
    """(year, month) of the last whole month dataset has, asked of Copernicus
    Marine's catalogue at most once an hour."""
    found = _last.get(dataset)
    if found is not None and time.time() - found[0] < LAST_KEPT:
        return found[1]
    info = DATASETS[dataset]
    toolbox = _toolbox()
    with _quiet():
        catalogue = toolbox.describe(dataset_id=info["dataset_id"], disable_progress_bar=True)
    latest = None
    for product in catalogue.products:
        for each in product.datasets:
            if each.dataset_id != info["dataset_id"]:
                continue
            for version in each.versions:
                for part in version.parts:
                    for service in part.services:
                        for variable in service.variables:
                            if variable.short_name != info["variable"]:
                                continue
                            for coordinate in variable.coordinates:
                                if coordinate.coordinate_id == "time" and coordinate.maximum_value is not None:
                                    latest = max(latest or 0, coordinate.maximum_value)
    if latest is None:
        raise ValueError(f"Copernicus Marine did not say what times {info['dataset_id']} has")
    # milliseconds since 1970
    when = datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=latest)
    last = _whole_month(when)
    _last[dataset] = (time.time(), last)
    return last


def years(dataset):
    """The years dataset has, from its first to the year of its last whole month."""
    return list(range(DATASETS[dataset]["first_year"], last_month(dataset)[0] + 1))


def download(dataset, year, month, box, folder):
    """Download dataset's days of month in year, in box (west, east, south,
    north), to a netCDF file in folder, and return its path. An error the
    toolbox gives is raised as a ValueError, with its reason."""
    info = DATASETS[dataset]
    west, east = _clamp(float(box[0]), float(box[1]), info["lon"])
    south, north = _clamp(float(box[2]), float(box[3]), info["lat"])
    days = calendar.monthrange(year, month)[1]
    name = f"{dataset}_{year}_{month:02d}.nc"
    toolbox = _toolbox()
    try:
        with _quiet():
            toolbox.subset(
                dataset_id=info["dataset_id"],
                variables=[info["variable"]],
                minimum_longitude=west,
                maximum_longitude=east,
                minimum_latitude=south,
                maximum_latitude=north,
                start_datetime=f"{year}-{month:02d}-01T00:00:00",
                end_datetime=f"{year}-{month:02d}-{days:02d}T00:00:00",
                # every cell that touches the box, as the other datasets give
                coordinates_selection_method="outside",
                output_directory=folder,
                output_filename=name,
                overwrite=True,
                disable_progress_bar=True,
                # netCDF3 has no string attributes, which CDO 2.6 misreads (it
                # took the calendar to be "p", and the year to be 30)
                netcdf3_compatible=True,
            )
    except Exception as error:
        raise ValueError(
            f"Copernicus Marine could not give {info['dataset_id']} for {year}-{month:02d}: "
            f"{type(error).__name__}: {error}"
        ) from error
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        raise ValueError(f"Copernicus Marine gave no file of {info['dataset_id']} for {year}-{month:02d}")
    return path


def monthly_mean(dataset, path, year, month):
    """A DataSet of the mean of the days of month in year in the file at
    path, in DATASETS' units, in a temporary file nctoolkit looks after."""
    from oceanval.quiet_nc import nc

    info = DATASETS[dataset]
    one = nc.open_data(path, checks=False)
    # its "coordinates" attribute names lon and lat, which it does not have
    # (CDO warns of each)
    one.nco_command(f"ncatted -a coordinates,{info['variable']},d,,")
    # the server gives the nearest day it has to each end, which can be in
    # the next month
    one.cdo_command(f"-selmon,{month} -selyear,{year}")
    one.tmean(["year", "month"])
    if info.get("adder"):
        one.add(info["adder"])
    if info.get("units"):
        one.set_units({info["variable"]: info["units"]})
    one.run()
    return one


def fetch(dataset, wanted, box):
    """A DataSet of the monthly means of dataset's variable for the years
    wanted that it has, in box, merged in time, in a temporary file nctoolkit
    looks after. Raises ValueError if it has none of them, or if the toolbox
    or its login is missing."""
    from oceanval.quiet_nc import nc

    check_login()
    last = last_month(dataset)
    have = set(years(dataset))
    wanted = sorted({int(x) for x in wanted} & have)
    if not wanted:
        raise ValueError(
            f"{dataset} has {min(have)} to {max(have)}, none of the years asked for"
        )
    folder = tempfile.mkdtemp(prefix="oceanval_cmems_")
    try:
        months = []
        for year in wanted:
            for month in range(1, 13):
                if (year, month) > last:
                    break
                path = download(dataset, year, month, box, folder)
                months.append(monthly_mean(dataset, path, year, month))
                # the days are not needed once averaged: only a month of
                # them is ever on disk
                os.remove(path)
        if len(months) == 1:
            return months[0]
        ds = nc.open_data([x[0] for x in months], checks=False)
        ds.merge("time")
        ds.run()
        return ds
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(folder)
