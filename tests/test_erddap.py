"""Gridded observations from an ERDDAP server (CoralTemp), cut to an area there.

The server is stood in for: its answers are small local netCDF files, as the
network is not to be relied on. One test asks the real server, and is
skipped when it cannot be reached.
"""

import io
import os
import re
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanval import erddap, server_trouble
from oceanval.parsers import find_recipe

URL = erddap.DATASETS["coraltemp"]["url"]
BOX = (-6.3, 2.1, 48.0, 52.2)


def query(url):
    """The constraints of a query_url, unquoted."""
    return urllib.parse.unquote(url.split(".nc?", 1)[1])


class TestTheRecipe:
    def test_it_is_read_from_erddap_not_thredds(self):
        recipe = find_recipe({"temperature": "coraltemp"})
        assert recipe["obs_path"] == URL
        assert recipe["thredds"] is False
        assert recipe["obs_variable"] == "sea_surface_temperature"
        assert recipe["builtin"] == "coraltemp"
        assert recipe["climatology"] is False

    def test_its_address_is_known(self):
        assert erddap.dataset_of(URL) == "coraltemp"
        assert erddap.dataset_of(URL + ".nc") is None
        assert erddap.dataset_of(find_recipe({"temperature": "oisst"})["obs_path"]) is None


class TestQueryUrl:
    def test_a_year_in_a_box_north_to_south(self):
        got = query(erddap.query_url("coraltemp", 1990, BOX))
        assert got == (
            "sea_surface_temperature[(1990-01-01T00:00:00Z):1:(1990-12-31T23:59:59Z)]"
            "[(52.2):1:(48)][(-6.3):1:(2.1)]"
        )

    def test_the_brackets_are_quoted(self):
        url = erddap.query_url("coraltemp", 1990, BOX)
        assert url.startswith(URL + ".nc?sea_surface_temperature%5B(")
        assert "[" not in url and "]" not in url

    def test_the_last_year_stops_at_the_last_time(self):
        got = query(erddap.query_url("coraltemp", 2026, BOX, last=(2026, 10)))
        assert "[(2026-01-01T00:00:00Z):1:(last)]" in got
        # another year is not cut
        got = query(erddap.query_url("coraltemp", 2025, BOX, last=(2026, 10)))
        assert "(2025-12-31T23:59:59Z)" in got

    def test_a_box_beyond_the_grid_is_cut_to_it(self):
        got = query(erddap.query_url("coraltemp", 2000, (-180, 180, -90, 90)))
        assert got.endswith("[(89.975):1:(-89.975)][(-179.975):1:(179.975)]")

    def test_a_box_with_nothing_of_the_grid_asks_for_all_of_it(self):
        got = query(erddap.query_url("coraltemp", 2000, (179.99, 180, 50, 51)))
        assert got.endswith("[(51):1:(50)][(-179.975):1:(179.975)]")


def _year_file(path, year, months=12, box=BOX):
    """A CoralTemp-like file of months from January of year, as ERDDAP sends
    it: latitudes falling, each month's values its year's last two digits."""
    lat = np.arange(box[3], box[2], -0.05)
    lon = np.arange(box[0], box[1], 0.05)
    # the middle of each month, as ERDDAP dates them
    times = pd.date_range(f"{year}-01-01", periods=months, freq="MS").values + np.timedelta64(15, "D")
    data = np.stack([
        np.full((len(lat), len(lon)), float(pd.Timestamp(t).year % 100), dtype="float32") for t in times
    ])
    ds = xr.Dataset(
        {"sea_surface_temperature": (("time", "latitude", "longitude"), data, {"units": "degree_C"})},
        coords={"time": times, "latitude": lat, "longitude": lon},
    )
    ds.latitude.attrs = {"units": "degrees_north", "standard_name": "latitude"}
    ds.longitude.attrs = {"units": "degrees_east", "standard_name": "longitude"}
    ds.to_netcdf(path)
    with open(path, "rb") as handle:
        return handle.read()


class FakeErddap:
    """urlopen, answering CoralTemp's queries with small files, and its last time."""

    def __init__(self, folder, last=(2026, 10)):
        self.folder = folder
        self.last = last
        self.asked = []
        # the status to answer with, as many times as it is listed
        self.errors = []

    def urlopen(self, request, timeout=None):
        url = request.full_url
        self.asked.append(url)
        if self.errors:
            code = self.errors.pop(0)
            body = io.BytesIO(
                b'Error {\n    code=%d;\n    message="Service Unavailable: There was a (temporary?) problem.";\n}\n' % code
            )
            raise urllib.error.HTTPError(url, code, "error", {}, body)
        if ".csv?" in url:
            return io.BytesIO(f"time\nUTC\n{self.last[0]}-{self.last[1]:02d}-16T00:00:00Z\n".encode())
        year = int(re.search(r"\((\d{4})-01-01", urllib.parse.unquote(url)).group(1))
        # the times nearest those asked for: 31 December is nearer the next
        # 16 January than 16 December, so a whole year comes with 13 months
        months = self.last[1] if year == self.last[0] else 13
        return io.BytesIO(_year_file(os.path.join(self.folder, f"answer_{len(self.asked)}.nc"), year, months))


@pytest.fixture
def server(monkeypatch, tmp_path):
    fake = FakeErddap(str(tmp_path))
    monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)
    monkeypatch.setattr(erddap.time, "sleep", lambda seconds: None)
    erddap._last.clear()
    yield fake
    erddap._last.clear()


class TestFetch:
    def test_the_years_asked_for_that_it_has_merged_in_time(self, server):
        ds = erddap.fetch("coraltemp", [1984, 2025, 2026, 2030], BOX)
        assert ds.years == [2025, 2026]
        assert len(ds.times) == 22
        # one question for the last time, and one a year
        assert len(server.asked) == 3
        assert "(last)" in urllib.parse.unquote(server.asked[-1])

    def test_one_year(self, server):
        ds = erddap.fetch("coraltemp", [2000], BOX)
        assert ds.years == [2000]
        assert len(ds.times) == 12

    def test_the_next_years_january_it_sends_is_left_out(self, server):
        ds = erddap.fetch("coraltemp", [2000, 2001], BOX)
        with xr.open_dataset(ds[0]) as opened:
            times = pd.to_datetime(opened.time.values)
            assert len(times) == 24 and times.is_unique
            years = opened.sea_surface_temperature.isel(latitude=0, longitude=0).values
        np.testing.assert_array_equal(years, [0.0] * 12 + [1.0] * 12)

    def test_none_of_the_years_is_an_error(self, server):
        with pytest.raises(ValueError, match="1985 to 2026"):
            erddap.fetch("coraltemp", [1970, 1971], BOX)

    def test_the_last_time_is_asked_once(self, server):
        erddap.fetch("coraltemp", [2000], BOX)
        erddap.fetch("coraltemp", [2001], BOX)
        assert sum(".csv?" in x for x in server.asked) == 1

    def test_a_busy_server_is_asked_again(self, server):
        server.errors = [503, 503]
        ds = erddap.fetch("coraltemp", [2000], BOX)
        assert ds.years == [2000]

    def test_a_server_that_stays_busy_is_its_trouble(self, server):
        erddap.last_month("coraltemp")
        server.errors = [503] * len(erddap.RETRY_WAITS + (None,))
        with pytest.raises(ValueError, match="HTTP 503 for 2000: Service Unavailable") as raised:
            erddap.fetch("coraltemp", [2000], BOX)
        assert server_trouble.answer_of(raised.value) == {"status": 503, "reason": "Service Unavailable"}

    def test_a_refusal_is_not_asked_again(self, server):
        erddap.last_month("coraltemp")
        server.errors = [404]
        with pytest.raises(ValueError, match="HTTP 404"):
            erddap.fetch("coraltemp", [2000], BOX)
        assert len(server.asked) == 2


def _reachable():
    try:
        with urllib.request.urlopen(urllib.request.Request(URL + ".das", headers={"User-Agent": "oceanval"}), timeout=20):
            return True
    except Exception:
        return False


@pytest.mark.timeout(300)
def test_the_real_server_cuts_out_the_area():
    if not _reachable():
        pytest.skip("CoastWatch's ERDDAP server cannot be reached")
    erddap._last.clear()
    ds = erddap.fetch("coraltemp", [2000], (-4.5, -3.5, 50, 50.5))
    assert ds.years == [2000]
    assert len(ds.times) == 12
    with xr.open_dataset(ds[0]) as opened:
        assert opened.latitude.size <= 12 and opened.longitude.size <= 22
        sst = opened.sea_surface_temperature.values
    # the western Channel: 8 to 20 degrees, with land missing
    assert 8 < np.nanmin(sst) and np.nanmax(sst) < 20
