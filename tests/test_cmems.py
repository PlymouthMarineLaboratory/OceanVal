"""Gridded observations from Copernicus Marine (OSTIA), cut to an area there and
made monthly means of their days.

The copernicusmarine toolbox is stood in for: its subset writes small local
netCDF files of daily values in kelvin, as the toolbox does, so nothing is
downloaded and the toolbox need not be installed. One test asks the real
server, and is skipped without the toolbox or a Copernicus Marine login.
"""

import datetime
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanval import cmems, data_store, gridded, server_trouble
from oceanval.parsers import find_recipe

URL = cmems.DATASETS["ostia"]["url"]
BOX = (3, 4, 54, 55)


def _value(day):
    """The fake's value, in degC, of a day: its month plus a tenth of its day of the month."""
    return day.month + day.day / 10


def _month_file(path, start, end, box):
    """The days from start to end in box, as the toolbox gives them: in kelvin
    as packed shorts, a netCDF3 file with a "coordinates" attribute naming
    variables it does not have, and the next month's first day too (the
    nearest day to the end asked for, with "outside")."""
    days = pd.date_range(start, end, freq="D").append(pd.DatetimeIndex([end + datetime.timedelta(days=1)]))
    # a few cells of the box, whatever its size (a global box at 0.05 degrees
    # would be gigabytes)
    lat = np.linspace(box[2] + 0.025, box[3] - 0.025, 4)
    lon = np.linspace(box[0] + 0.025, box[1] - 0.025, 4)
    data = np.stack([
        np.full((len(lat), len(lon)), 273.15 + _value(day) + (100 if day > end else 0), dtype="float64")
        for day in days
    ])
    ds = xr.Dataset(
        {"analysed_sst": (("time", "latitude", "longitude"), data,
                          {"units": "kelvin", "coordinates": "lon lat"})},
        coords={"time": days.values, "latitude": lat, "longitude": lon},
    )
    ds.latitude.attrs = {"units": "degrees_north", "standard_name": "latitude"}
    ds.longitude.attrs = {"units": "degrees_east", "standard_name": "longitude"}
    ds.analysed_sst.encoding = {"dtype": "int16", "scale_factor": 0.01, "add_offset": 273.15, "_FillValue": -32768}
    ds.time.encoding = {"units": "seconds since 1981-01-01", "calendar": "proleptic_gregorian", "dtype": "int32"}
    ds.to_netcdf(path, format="NETCDF3_CLASSIC")


class FakeToolbox(types.ModuleType):
    """The copernicusmarine module: subset, describe and login, and what was asked."""

    def __init__(self, last=datetime.datetime(2026, 6, 30)):
        super().__init__("copernicusmarine")
        self.last = last
        self.subsets = []
        self.described = 0
        self.logins = 0
        self.valid = True
        self.fail = None

    def login(self, check_credentials_valid=False, **kwargs):
        assert check_credentials_valid
        self.logins += 1
        return self.valid

    def describe(self, dataset_id=None, disable_progress_bar=False, **kwargs):
        self.described += 1
        ms = (self.last - datetime.datetime(1970, 1, 1)).total_seconds() * 1000
        time = types.SimpleNamespace(coordinate_id="time", minimum_value=370742400000.0, maximum_value=ms)
        lat = types.SimpleNamespace(coordinate_id="latitude", minimum_value=-89.975, maximum_value=89.975)
        variable = types.SimpleNamespace(short_name="analysed_sst", coordinates=[time, lat])
        service = types.SimpleNamespace(service_name="arco-geo-series", variables=[variable])
        part = types.SimpleNamespace(services=[service])
        version = types.SimpleNamespace(parts=[part])
        dataset = types.SimpleNamespace(dataset_id=dataset_id, versions=[version])
        return types.SimpleNamespace(products=[types.SimpleNamespace(datasets=[dataset])])

    def subset(self, **kwargs):
        self.subsets.append(kwargs)
        if self.fail is not None:
            raise self.fail
        start = pd.Timestamp(kwargs["start_datetime"])
        end = pd.Timestamp(kwargs["end_datetime"])
        box = (kwargs["minimum_longitude"], kwargs["maximum_longitude"],
               kwargs["minimum_latitude"], kwargs["maximum_latitude"])
        _month_file(os.path.join(kwargs["output_directory"], kwargs["output_filename"]), start, end, box)


@pytest.fixture
def toolbox(monkeypatch):
    fake = FakeToolbox()
    monkeypatch.setitem(sys.modules, "copernicusmarine", fake)
    cmems._last.clear()
    cmems._logged_in.clear()
    yield fake
    cmems._last.clear()
    cmems._logged_in.clear()


class TestTheRecipe:
    def test_it_is_cut_by_copernicus_marine_not_read_over_thredds(self):
        recipe = find_recipe({"temperature": "ostia"})
        assert recipe["obs_path"] == URL
        assert recipe["thredds"] is False
        assert recipe["file_check"] is False
        assert recipe["obs_variable"] == "analysed_sst"
        assert recipe["builtin"] == "ostia"
        assert recipe["source"] == "OSTIA"
        assert recipe["climatology"] is False
        assert recipe["vertical"] is False

    def test_its_address_is_known(self):
        assert cmems.dataset_of(URL) == "ostia"
        assert cmems.dataset_of(URL + "/") is None
        assert cmems.dataset_of(find_recipe({"temperature": "coraltemp"})["obs_path"]) is None

    def test_the_matchup_and_the_store_send_it_to_copernicus_marine(self):
        assert gridded._server_cut(URL) == (cmems, "ostia")
        coraltemp = find_recipe({"temperature": "coraltemp"})["obs_path"]
        assert gridded._server_cut(coraltemp)[1] == "coraltemp"
        assert gridded._server_cut(find_recipe({"temperature": "oisst"})["obs_path"]) is None
        assert data_store.server_cut("ostia") is cmems
        assert data_store.server_cut("oisst") is None

    def test_its_server_is_named(self):
        assert server_trouble.server_of(server_trouble.host_of(URL)) == "Copernicus Marine"


class TestLastMonth:
    @pytest.mark.parametrize(
        "when, last",
        [
            (datetime.datetime(2026, 6, 30), (2026, 6)),
            (datetime.datetime(2026, 7, 15), (2026, 6)),
            (datetime.datetime(2026, 1, 10), (2025, 12)),
            (datetime.datetime(2024, 2, 29), (2024, 2)),
        ],
    )
    def test_only_a_whole_month_is_its_last(self, when, last):
        assert cmems._whole_month(when) == last

    def test_it_is_read_from_the_catalogue_once(self, toolbox):
        toolbox.last = datetime.datetime(2026, 7, 12)
        assert cmems.last_month("ostia") == (2026, 6)
        assert cmems.last_month("ostia") == (2026, 6)
        assert toolbox.described == 1
        assert cmems.years("ostia") == list(range(1982, 2027))


class TestFetch:
    def test_a_month_at_a_time_in_the_box(self, toolbox):
        cmems.fetch("ostia", [2000], BOX)
        assert len(toolbox.subsets) == 12
        first, feb = toolbox.subsets[0], toolbox.subsets[1]
        assert first["dataset_id"] == "METOFFICE-GLO-SST-L4-REP-OBS-SST"
        assert first["variables"] == ["analysed_sst"]
        assert (first["minimum_longitude"], first["maximum_longitude"]) == (3, 4)
        assert (first["minimum_latitude"], first["maximum_latitude"]) == (54, 55)
        assert first["coordinates_selection_method"] == "outside"
        # CDO 2.6 misreads netCDF4's string attributes
        assert first["netcdf3_compatible"] is True
        assert (first["start_datetime"], first["end_datetime"]) == ("2000-01-01T00:00:00", "2000-01-31T00:00:00")
        # a leap year's February
        assert feb["end_datetime"] == "2000-02-29T00:00:00"

    def test_a_box_beyond_the_grid_is_cut_to_it(self, toolbox):
        cmems.fetch("ostia", [2000], (-180, 180, -90, 90))
        asked = toolbox.subsets[0]
        assert (asked["minimum_longitude"], asked["maximum_longitude"]) == (-179.975, 179.975)
        assert (asked["minimum_latitude"], asked["maximum_latitude"]) == (-89.975, 89.975)

    def test_monthly_means_of_the_days_in_degc(self, toolbox):
        ds = cmems.fetch("ostia", [2001], BOX)
        assert ds.years == [2001]
        with xr.open_dataset(ds[0]) as opened:
            assert opened.analysed_sst.attrs["units"] == "degC"
            got = opened.analysed_sst.isel(latitude=0, longitude=0).values
            months = pd.to_datetime(opened.time.values).month
        # the mean of each month's own days (the next month's first day,
        # which the server also gives, left out)
        days = pd.date_range("2001-01-01", "2001-12-31", freq="D")
        expected = pd.Series([_value(d) for d in days], index=days).groupby(days.month).mean()
        np.testing.assert_array_equal(months, range(1, 13))
        np.testing.assert_allclose(got, expected.values, atol=0.01)

    def test_the_years_it_has_merged_in_time_to_its_last_month(self, toolbox):
        toolbox.last = datetime.datetime(2026, 3, 31)
        ds = cmems.fetch("ostia", [1981, 2025, 2026, 2030], BOX)
        assert ds.years == [2025, 2026]
        assert len(ds.times) == 15
        assert len(toolbox.subsets) == 15
        assert toolbox.subsets[-1]["start_datetime"] == "2026-03-01T00:00:00"

    def test_the_days_are_not_left_on_disk(self, toolbox):
        cmems.fetch("ostia", [2000], BOX)
        assert not os.path.exists(toolbox.subsets[0]["output_directory"])

    def test_none_of_the_years_is_an_error(self, toolbox):
        with pytest.raises(ValueError, match="1982 to 2026"):
            cmems.fetch("ostia", [1970, 1971], BOX)
        assert toolbox.subsets == []

    def test_an_error_of_the_toolbox_says_the_month(self, toolbox):
        toolbox.fail = RuntimeError("the service is unavailable")
        with pytest.raises(ValueError, match="2000-01: RuntimeError: the service is unavailable"):
            cmems.fetch("ostia", [2000], BOX)


class TestLogin:
    def test_without_the_toolbox_it_says_how_to_install_it(self, monkeypatch):
        # None in sys.modules makes the import fail
        monkeypatch.setitem(sys.modules, "copernicusmarine", None)
        cmems._logged_in.clear()
        with pytest.raises(ValueError, match="pip install copernicusmarine"):
            cmems.fetch("ostia", [2000], BOX)

    def test_without_a_login_it_says_how_to_log_in(self, toolbox):
        toolbox.valid = False
        with pytest.raises(ValueError, match="copernicusmarine login"):
            cmems.fetch("ostia", [2000], BOX)
        assert toolbox.subsets == []

    def test_the_login_is_checked_once(self, toolbox):
        cmems.fetch("ostia", [2000], BOX)
        cmems.fetch("ostia", [2001], BOX)
        assert toolbox.logins == 1


def _logged_in():
    try:
        import copernicusmarine
    except ImportError:
        return False
    try:
        return bool(copernicusmarine.login(check_credentials_valid=True))
    except Exception:
        return False


@pytest.mark.timeout(600)
def test_the_real_server_cuts_out_the_area():
    if not _logged_in():
        pytest.skip("the copernicusmarine toolbox, or a Copernicus Marine login, is missing")
    cmems._last.clear()
    ds = cmems.fetch("ostia", [2010], (-4.5, -3.5, 50, 50.5))
    assert ds.years == [2010]
    assert len(ds.times) == 12
    with xr.open_dataset(ds[0]) as opened:
        assert opened.latitude.size <= 12 and opened.longitude.size <= 22
        sst = opened.analysed_sst.values
    # the western Channel: 8 to 20 degrees, with land missing
    assert 7 < np.nanmin(sst) and np.nanmax(sst) < 20
