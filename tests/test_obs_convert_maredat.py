"""The MAREDAT builder (oceanval.obs_convert.maredat): a group's zip, made into
a CF netCDF file CDO and the gridded matchup can use, from a small file laid
out as PANGAEA's are."""

import os
import shutil
import zipfile

import netCDF4
import numpy as np
import pytest

from oceanval import obs_convert, parsers

DEPTHS = [0.0, 10.0, 20.0, 30.0, 50.0]


def write_maredat(path):
    """A MAREDAT-like file: 12 months, 5 depths, a 1 degree global grid, the
    BIOMASS of three cells, 1e35 elsewhere, and the files' own oddities (the
    time axis in seconds since year 0, the last latitude at 89.62)."""
    with netCDF4.Dataset(path, "w", format="NETCDF3_CLASSIC") as ds:
        for name, size in (("LONGITUDE", 360), ("LATITUDE", 180), ("DEPTH", len(DEPTHS)), ("TIME", 12)):
            ds.createDimension(name, size)
        lon = ds.createVariable("LONGITUDE", "f4", ("LONGITUDE",))
        lon[:] = np.arange(-179.5, 180)
        lat = ds.createVariable("LATITUDE", "f4", ("LATITUDE",))
        values = np.arange(-89.5, 90)
        values[-1] = 89.62
        lat[:] = values
        depth = ds.createVariable("DEPTH", "f4", ("DEPTH",))
        depth.units = "meters"
        depth[:] = DEPTHS
        time = ds.createVariable("TIME", "f4", ("TIME",))
        time.units = "seconds since 0-01-01"
        time[:] = 1314000.0 + 2628000.0 * np.arange(12)
        biomass = ds.createVariable("BIOMASS", "f4", ("TIME", "DEPTH", "LATITUDE", "LONGITUDE"), fill_value=1e35)
        biomass.units = "ug/L"
        data = np.full((12, len(DEPTHS), 180, 360), 1e35, dtype="f4")
        data[0, 0, 140, 180] = 12.5  # January, 0 m, 50.5N 0.5E
        data[5, 3, 140, 180] = 3.0  # June, 30 m, the same cell
        data[6, 4, 100, 10] = 0.25  # July, 50 m only: no surface sample
        biomass[:] = data


@pytest.fixture
def zipped(tmp_path):
    """A zip like the diatoms one: its netCDF file in a folder of its own."""
    raw = tmp_path / "MarEDat20120716Diatoms.nc"
    write_maredat(str(raw))
    path = tmp_path / "PANGAEA.777384_(PDI-1347).zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.write(raw, "PANGAEA.777384_(PDI-1347)/MarEDat20120716Diatoms.nc")
        archive.writestr("PANGAEA.777384_(PDI-1347)/diatoms.xlsx", b"not read")
    return str(path)


@pytest.fixture
def built(zipped, tmp_path, monkeypatch):
    monkeypatch.setattr(obs_convert, "_fetch", lambda url, path: shutil.copyfile(zipped, path))
    folder = tmp_path / "built"
    folder.mkdir()
    url = obs_convert.MAREDAT_URLS["diatoms"]
    return obs_convert.build(url, str(folder)), folder


def test_every_group_is_a_built_product_with_a_recipe():
    for variable, url in obs_convert.MAREDAT_URLS.items():
        assert obs_convert.is_built(url)
        recipe = parsers.find_recipe({variable: "maredat"})
        assert recipe["obs_path"] == url
        assert recipe["obs_variable"] == "biomass"
        assert recipe["climatology"] is True
        assert recipe["thredds"] is False
        assert recipe["source"] == "MAREDAT"
        # each group's own paper
        assert "Earth Syst. Sci. Data" in recipe["source_info"] or "Global Biogeochem" in recipe["source_info"]
    assert len(set(obs_convert.MAREDAT_URLS.values())) == len(obs_convert.MAREDAT_URLS)


def test_the_file_is_cf_with_the_months_in_2000_and_empty_cells_missing(built):
    import xarray as xr

    path, folder = built
    # only the file made is left: the zip and the extracted file are removed
    assert os.listdir(folder) == [os.path.basename(path)]
    with xr.open_dataset(path) as ds:
        b = ds["biomass"]
        assert b.dims == ("time", "depth", "lat", "lon")
        assert b.attrs["units"] == "mg C m-3"
        assert [t.month for t in ds.indexes["time"]] == list(range(1, 13))
        assert {t.year for t in ds.indexes["time"]} == {obs_convert.CLIMATOLOGY_YEAR}
        assert ds["depth"].values.tolist() == DEPTHS
        assert ds["depth"].attrs["positive"] == "down"
        assert float(ds["lat"].max()) == 89.5
        assert int(b.count()) == 3
        assert float(b.sel(lat=50.5, lon=0.5).isel(time=0, depth=0)) == pytest.approx(12.5)


@pytest.mark.skipif(shutil.which("cdo") is None, reason="needs CDO")
def test_cdo_reads_its_levels_and_months_and_its_top_is_0_m(built):
    from oceanval.quiet_nc import nc

    path, _ = built
    ds = nc.open_data(path, checks=False)
    assert ds.levels == DEPTHS
    assert len(ds.times) == 12
    # the gridded matchup takes MAREDAT's first level (0 m) as its surface, so a
    # cell sampled only at 50 m has no surface value
    ds.top()
    ds.run()
    top = ds.to_xarray()["biomass"].squeeze(drop=True)
    assert int(top.count()) == 1
    assert float(top.isel(time=0).sel(lat=50.5, lon=0.5)) == pytest.approx(12.5)
