"""Observational products that are not a netCDF file OceanVal can open as they
are, made into one.

Four of OceanVal's gridded datasets are like that:

* **MPI-SOM-FFN** (recipes ``pco2`` and ``co2flux``, source "mpisom"): its
  monthly climatology is a netCDF file, but with its dimensions as (lon, lat,
  time), a time axis of 1 to 12 "months", and a fill value of 1e20 that is only
  in the data. It is put in CF form. Its air-sea CO2 flux is negative into the
  ocean; it is made positive into the ocean, as CMIP6's fgco2 and ERSEM's fair
  are.
* **SOCAT** (recipe ``pco2``, source "socat"): the monthly 1 degree product's
  netCDF file is 3.9 GB, but the same product is a 50 MB csv of the cells that
  have data, which is made into a netCDF file of the cruise-weighted mean fCO2
  of every month, with nothing where there is none.
* **COPEPOD** (recipe ``mesozooplankton``, source "copepod"): the zooplankton
  carbon biomass fields of the 12 months are csv files of the 0.25 degree cells
  that have data, in a zip, and are made into a climatology netCDF file.

Each is known by the address it comes from (BUILT), which is what OceanVal's
recipes give as their obs_path, and the data store keeps the file made, whole.
open_url is nctoolkit's open_url, but builds these.
"""

import contextlib
import io
import os
import shutil
import tempfile
import urllib.request
import zipfile

import numpy as np
import pandas as pd

_NCEI = "https://www.ncei.noaa.gov/data/oceans/ncei/ocads/data"
MPISOM_URL = f"{_NCEI}/0160558/MPI_SOM-FFN_v2025/SOM-FFN_v2025_CLIM_NCEI_OCADS.nc"
# the product's own netCDF file is 3.9 GB; its csv, made into a netCDF file, is
# what is read (the file name is the same)
SOCAT_URL = f"{_NCEI}/0315110/SOCATv2026_Gridded_Data/SOCATv2026_tracks_gridded_monthly.nc"
COPEPOD_URL = "https://www.st.nmfs.noaa.gov/copepod/biomass/copepod-2012__biomass-fields.zip"
TIMEOUT = 600
# the year a climatology's twelve months are dated in, as WOA23's are all in one
CLIMATOLOGY_YEAR = 2000
# what a fill value is in the MPI-SOM-FFN file
MPISOM_FILL = 1e19


def _builders():
    found = {MPISOM_URL: mpisom, SOCAT_URL: socat, COPEPOD_URL: copepod}
    return found


def is_built(path):
    """Whether path is the address of a product that is built into a file, not downloaded."""
    return isinstance(path, str) and path in _builders()


def _read(url):
    """The bytes at url."""
    request = urllib.request.Request(url, headers={"User-Agent": "oceanval"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def _write(ds, path):
    """ds as a compressed netCDF file, its floats missing as 1e20."""
    encoding = {
        name: {"zlib": True, "complevel": 4, "_FillValue": np.float32(1e20), "dtype": "float32"}
        for name in ds.data_vars
    }
    for name in ("lon", "lat", "time"):
        if name in ds.variables:
            encoding[name] = {"_FillValue": None}
    ds.to_netcdf(path, encoding=encoding)
    return path


def _months_of(year, n=12):
    """The middle of each of n months from January of year, as dates."""
    return pd.date_range(f"{year}-01-01", periods=n, freq="MS") + pd.Timedelta(days=14)


def _grid(step, lon_start=-180.0, lat_start=-90.0):
    """The centres of the cells of a global grid of step degrees."""
    half = step / 2
    lon = np.arange(lon_start + half, lon_start + 360, step)
    lat = np.arange(lat_start + half, lat_start + 180, step)
    return lon, lat


def mpisom(folder):
    """The MPI-SOM-FFN v2025 climatology made a CF file: path of a file with
    spco2_clim (uatm) and fgco2_clim (mol C m-2 yr-1, positive into the ocean)."""
    import xarray as xr

    raw = os.path.join(folder, "mpisom_raw.nc")
    with open(raw, "wb") as out:
        out.write(_read(MPISOM_URL))
    with xr.open_dataset(raw, decode_times=False) as source:
        data = {}
        for name in ("spco2_clim", "fgco2_clim"):
            a = source[name].transpose("time", "lat", "lon").load()
            a = a.where(a < MPISOM_FILL)
            data[name] = a
        lon, lat = source["lon"].values, source["lat"].values
    # the product's flux is negative where the ocean takes CO2 up
    data["fgco2_clim"] = -data["fgco2_clim"]
    out = xr.Dataset(
        {
            name: (("time", "lat", "lon"), a.values.astype("float32"))
            for name, a in data.items()
        },
        coords={"time": _months_of(CLIMATOLOGY_YEAR), "lat": lat, "lon": lon},
    )
    out["lat"].attrs.update(units="degrees_north", standard_name="latitude", axis="Y")
    out["lon"].attrs.update(units="degrees_east", standard_name="longitude", axis="X")
    out["spco2_clim"].attrs.update(
        units="uatm", long_name="surface ocean pCO2, monthly climatology (MPI-SOM-FFN v2025)")
    out["fgco2_clim"].attrs.update(
        units="mol m-2 yr-1",
        long_name="air-sea CO2 flux, monthly climatology (MPI-SOM-FFN v2025), positive into the ocean",
        comment="The product's own sign (negative into the ocean) is reversed by OceanVal.",
    )
    out.attrs.update(source=MPISOM_URL, climatology="monthly climatology; the months are dated in "
                     f"{CLIMATOLOGY_YEAR}")
    path = os.path.join(folder, "MPI-SOM-FFN_v2025_clim.nc")
    return _write(out, path)


def socat_cells(text):
    """The SOCAT monthly gridded csv (text, or a path) as a DataFrame of the
    month's first day, lon, lat and the cruise-weighted mean fCO2 of each cell
    that has data."""
    df = pd.read_csv(text, skipinitialspace=True, usecols=lambda c: c.strip() in (
        "valid_tax_date", "valid_lat_month", "valid_lon_month", "valid_fco2_ave_weighted_month"))
    df.columns = ["date", "lat", "lon", "fco2"]
    df["date"] = pd.to_datetime(df["date"])
    # cells with nothing in the month are -1e34 in the product
    df = df[df["fco2"] > -1e30]
    return df.reset_index(drop=True)


def socat_array(df, step=1.0):
    """A DataSet of fco2_ave_weighted (time, lat, lon) from socat_cells' rows:
    every month from the first to the last, mid-month, a cell with no data missing."""
    import xarray as xr

    months = pd.period_range(df["date"].min(), df["date"].max(), freq="M")
    lon, lat = _grid(step)
    values = np.full((len(months), len(lat), len(lon)), np.nan, dtype="float32")
    t = (df["date"].dt.year.to_numpy() - months[0].year) * 12 + df["date"].dt.month.to_numpy() - months[0].month
    j = np.floor((df["lat"].to_numpy() + 90) / step).astype(int)
    i = np.floor((df["lon"].to_numpy() + 180) / step).astype(int)
    values[t, j, i] = df["fco2"].to_numpy()
    times = months.to_timestamp() + pd.Timedelta(days=14)
    out = xr.Dataset(
        {"fco2_ave_weighted": (("time", "lat", "lon"), values)},
        coords={"time": times, "lat": lat, "lon": lon},
    )
    out["lat"].attrs.update(units="degrees_north", standard_name="latitude", axis="Y")
    out["lon"].attrs.update(units="degrees_east", standard_name="longitude", axis="X")
    out["fco2_ave_weighted"].attrs.update(
        units="uatm", long_name="fCO2 mean, per cruise weighted (SOCATv2026, 1 degree, monthly)")
    out.attrs.update(source=SOCAT_URL)
    return out


def socat(folder):
    """The SOCAT monthly 1 degree product made a netCDF file of
    fco2_ave_weighted (uatm): path of the file."""
    csv = os.path.join(folder, "socat_monthly.csv")
    with open(csv, "wb") as out:
        out.write(_read(SOCAT_URL[:-3] + ".csv"))
    path = os.path.join(folder, "SOCATv2026_tracks_gridded_monthly.nc")
    return _write(socat_array(socat_cells(csv)), path)


def copepod_months(archive):
    """The COPEPOD-2012 carbon mass csv files of the months in the opened
    zip archive, as {month: DataFrame of lon, lat, carbon mass (mg C m-3)}."""
    found = {}
    for month in range(1, 13):
        name = f"copepod-2012__biomass-fields/data/copepod-2012__cmass-m{month:02d}-qtr.csv"
        with archive.open(name) as f:
            df = pd.read_csv(f, skipinitialspace=True, usecols=[0, 1, 3], names=["lon", "lat", "cmass"], header=0)
        found[month] = df
    return found


def copepod_array(months, step=0.25):
    """A DataSet of the carbon mass (time, lat, lon) of the 12 months' cells, a
    cell with no samples missing, the months dated in CLIMATOLOGY_YEAR."""
    import xarray as xr

    lon, lat = _grid(step)
    values = np.full((12, len(lat), len(lon)), np.nan, dtype="float32")
    for month, df in months.items():
        j = np.floor((df["lat"].to_numpy() + 90) / step).astype(int)
        i = np.floor((df["lon"].to_numpy() + 180) / step).astype(int)
        values[month - 1, j, i] = df["cmass"].to_numpy()
    out = xr.Dataset(
        {"cmass": (("time", "lat", "lon"), values)},
        coords={"time": _months_of(CLIMATOLOGY_YEAR), "lat": lat, "lon": lon},
    )
    out["lat"].attrs.update(units="degrees_north", standard_name="latitude", axis="Y")
    out["lon"].attrs.update(units="degrees_east", standard_name="longitude", axis="X")
    out["cmass"].attrs.update(
        units="mg C m-3",
        long_name="zooplankton carbon mass, 0-200 m mean, 330 um nets (COPEPOD-2012)",
    )
    out.attrs.update(source=COPEPOD_URL, climatology="monthly climatology; the months are dated in "
                     f"{CLIMATOLOGY_YEAR}")
    return out


def copepod(folder):
    """The COPEPOD-2012 carbon mass fields made a netCDF file of cmass
    (mg C m-3): path of the file."""
    with zipfile.ZipFile(io.BytesIO(_read(COPEPOD_URL))) as archive:
        months = copepod_months(archive)
    path = os.path.join(folder, "COPEPOD-2012_carbon_mass.nc")
    return _write(copepod_array(months), path)


def _fetch(url, path):
    """Download url to path, a megabyte at a time."""
    request = urllib.request.Request(url, headers={"User-Agent": "oceanval"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(path, "wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)


def build(url, folder):
    """The product at url (one of BUILT's) made into a netCDF file in folder: its path."""
    return _builders()[url](folder)


def open_url(url):
    """nctoolkit's open_url of url, or, for a product that is built into a
    file (see is_built), a DataSet of it, in a temporary file nctoolkit looks
    after."""
    from oceanval.quiet_nc import nc

    if not is_built(url):
        return nc.open_url(url)
    folder = tempfile.mkdtemp(prefix="oceanval_built_")
    try:
        path = build(url, folder)
        ds = nc.open_data(path, checks=False)
        # a copy nctoolkit keeps, as it does a download, so the folder can go
        ds.cdo_command("copy")
        ds.run()
        return ds
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(folder)
