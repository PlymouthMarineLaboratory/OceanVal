"""The data store: built-in observational data kept for later matchups.

The servers are stood in for by local files: nctoolkit's open_thredds is
replaced by one opening the file a url stands for, as the network is not to
be relied on here.
"""

import json
import os
import re
import shutil
import threading

import nctoolkit as nc
import numpy as np
import pandas as pd
import pytest
import xarray as xr

import oceanval
from oceanval import data_store, ices, user_settings
from oceanval.parsers import find_recipe
from oceanval.session import session_info

WOA_URLS = find_recipe({"nitrate": "woa23"})["obs_path"]
COBE2_URL = find_recipe({"temperature": "cobe2"})["obs_path"]
COBE2_DOWNLOAD = data_store.DOWNLOADS["cobe2"]["file"]
# the example model's area, about 3.1-3.4E, 54.0-54.5N
MODEL_BOX = (3.11, 3.44, 54.0, 54.47)


def boom(*args, **kwargs):
    raise ConnectionError("Connection timed out: www.ncei.noaa.gov")


def _write(path, data, lon, lat, times, depth=None, name="n_an"):
    dims = ("time",) + (("depth",) if depth is not None else ()) + ("lat", "lon")
    coords = {"time": times, "lat": lat, "lon": lon}
    if depth is not None:
        coords["depth"] = depth
    ds = xr.Dataset({name: (dims, data.astype("float32"))}, coords=coords)
    ds.lon.attrs = {"units": "degrees_east", "standard_name": "longitude"}
    ds.lat.attrs = {"units": "degrees_north", "standard_name": "latitude"}
    if depth is not None:
        ds.depth.attrs = {"units": "m", "positive": "down", "axis": "Z"}
    ds.to_netcdf(path)


@pytest.fixture(scope="module")
def woa_files(tmp_path_factory):
    """A WOA23-like nitrate file per month: 0.1 degree, five levels, over
    more of the North Sea than the example model has."""
    folder = tmp_path_factory.mktemp("woa")
    rng = np.random.default_rng(1)
    lon = np.round(np.arange(-4.95, 12, 0.1), 2)
    lat = np.round(np.arange(48.05, 62, 0.1), 2)
    files = {}
    for month in range(1, 13):
        data = rng.random((1, 5, len(lat), len(lon))) * 10 + month
        path = folder / f"n{month:02d}.nc"
        _write(path, data, lon, lat, [np.datetime64(f"2000-{month:02d}-15")], depth=np.array([0.0, 5, 10, 20, 30]))
        files[month] = str(path)
    return files


@pytest.fixture(scope="module")
def cobe_file(tmp_path_factory):
    """A COBE2-like file: 1 degree, global, 0 to 360, latitudes falling,
    monthly from 2000 to the middle of 2002."""
    path = tmp_path_factory.mktemp("cobe") / "sst.mon.mean.nc"
    lon = np.arange(0.5, 360, 1.0)
    lat = np.arange(89.5, -90, -1.0)
    times = pd.date_range("2000-01-01", "2002-06-01", freq="MS").values
    data = np.random.default_rng(2).random((len(times), len(lat), len(lon))) * 20
    _write(path, data, lon, lat, times, name="sst")
    return str(path)


@pytest.fixture(scope="module")
def fine_cobe_file(tmp_path_factory):
    """Like cobe_file, but around the example model, in cells smaller than it is."""
    path = tmp_path_factory.mktemp("fine_cobe") / "sst.mon.mean.nc"
    lon = np.arange(0.025, 10, 0.05)
    lat = np.arange(59.975, 50, -0.05)
    times = pd.date_range("2000-01-01", "2002-06-01", freq="MS").values
    data = np.random.default_rng(3).random((len(times), len(lat), len(lon))) * 20
    _write(path, data, lon, lat, times, name="sst")
    return str(path)


class FakeServer:
    """nctoolkit's open_thredds, opening the local file a url stands for, and
    counting the urls opened."""

    def __init__(self, woa_files=None, cobe_file=None):
        self.woa_files = woa_files or {}
        self.cobe_file = cobe_file
        self.opened = []
        self.down = False
        # whether the server turns requests away (HTTP 429), and what was downloaded in its place
        self.limited = False
        self.downloaded = []
        self.download_down = False

    def local(self, url):
        found = re.search(r"_n(\d\d)_01\.nc$", url)
        if found:
            return self.woa_files[int(found.group(1))]
        if url == COBE2_URL:
            return self.cobe_file
        raise ValueError(f"no stand-in for {url}")

    def open_thredds(self, x, checks=False):
        urls = [x] if isinstance(x, str) else list(x)
        # matchup opens its first file again, by its path
        if all(os.path.exists(u) for u in urls):
            return nc.open_data(urls, checks=False)
        self.opened += urls
        if self.down:
            raise ConnectionError("Connection timed out")
        return nc.open_data([self.local(u) for u in urls], checks=False)


    def open_url(self, x, **kwargs):
        self.downloaded.append(x)
        if self.download_down or x != COBE2_DOWNLOAD:
            raise ConnectionError("Connection timed out")
        return nc.open_data(self.cobe_file, checks=False)


@pytest.fixture
def server(monkeypatch, woa_files, cobe_file):
    fake = FakeServer(woa_files, cobe_file)
    monkeypatch.setattr(nc, "open_thredds", fake.open_thredds)
    monkeypatch.setattr(nc, "open_url", fake.open_url)
    # no test asks a real server
    monkeypatch.setattr(data_store, "_server_limits", lambda url: fake.limited)
    return fake


@pytest.fixture
def store(tmp_path):
    """Start a run with a data store in tmp_path, used as asked."""
    started = []

    def use(mode="always", folder=None):
        folder = str(folder or tmp_path)
        data_store.start({"folder": folder, "root": data_store.root(folder), "use": mode})
        started.append(folder)
        return data_store.root(folder)

    yield use
    data_store.start(None)


def entries(root):
    with open(os.path.join(root, data_store.INDEX)) as handle:
        return json.load(handle)["entries"]


def retired(root):
    with open(os.path.join(root, data_store.INDEX)) as handle:
        return json.load(handle)["retired"]


def only_the_marker(root):
    """Whether the store holds nothing but the marker saying it is OceanVal's."""
    return [x.name for x in root.iterdir()] == [data_store.MARKER]


def woa_comparison():
    return {"builtin": "woa23", "obs_variable": "n_an", "recipe": True}


def values(ds):
    ds = ds.copy()
    ds.run()
    with xr.open_dataset(ds[0]) as opened:
        return opened.load()


# --------------------------------------------------------------------------
# boxes


class TestBoxes:
    def test_a_box_is_rounded_out_to_whole_degrees(self):
        assert data_store.snap([-12.34, 9.1], [48.2, 61.7]) == [-13, 10, 48, 62]
        assert data_store.snap([3, 4], [54, 55]) == [3, 4, 54, 55]

    def test_a_box_outside_the_globe_is_not_kept(self):
        assert data_store.snap([-200, 0], [0, 10]) is None
        assert data_store.snap([0, 360], [0, 10]) is None
        assert data_store.snap([10, 0], [0, 10]) is None
        assert data_store.snap(None, None) is None

    def test_a_box_with_most_of_the_globe_is_the_whole_globe(self):
        # a global model that stops short of Antarctica's coast
        assert data_store.snap([-180, 180], [-77.01, 89.9]) == data_store.GLOBAL
        assert data_store.snap([-179.5, 179.5], [-80, 85]) == data_store.GLOBAL

    def test_a_box_with_less_than_ninety_percent_of_the_globe_is_kept_as_it_is(self):
        # 60S to 60N is 87% of the globe's area
        assert data_store.snap([-180, 180], [-60, 60]) == [-180, 180, -60, 60]
        assert data_store.area_fraction([-180, 180, -60, 60]) == pytest.approx(np.sin(np.radians(60)))

    def test_contains_and_union(self):
        assert data_store.contains([-13, 10, 48, 62], [-12.3, 9.1, 48.2, 61.7])
        assert not data_store.contains([-13, 10, 48, 62], [-14, 9, 49, 61])
        assert data_store.union([0, 1, 2, 3], [-1, 0, 1, 5]) == [-1, 1, 1, 5]

    def test_years_are_put_in_runs(self):
        assert data_store._runs([2009, 2001, 2002, 2003, 2006, 2008]) == [
            (2001, 2003), (2006, 2006), (2008, 2009)]


# --------------------------------------------------------------------------
# keeping to the store's folder


class TestKeepingToTheStore:
    def test_paths_in_the_store_are_allowed(self, tmp_path):
        root = data_store.root(tmp_path)
        assert data_store._guard(f"{root}/woa23/a.nc", root) == f"{root}/woa23/a.nc"

    @pytest.mark.parametrize(
        "path",
        [
            "{tmp}/a.nc",
            "{root}/../a.nc",
            "{root}/woa23/../../a.nc",
            "/etc/passwd",
            "{root}",
            "",
        ],
    )
    def test_paths_outside_the_store_are_refused(self, tmp_path, path):
        root = data_store.root(tmp_path)
        with pytest.raises(data_store.OutsideTheStore):
            data_store._guard(path.format(tmp=tmp_path, root=root), root)

    def test_a_folder_not_named_for_the_store_is_refused(self, tmp_path):
        with pytest.raises(data_store.OutsideTheStore):
            data_store._guard(f"{tmp_path}/x/a.nc", str(tmp_path / "x"))

    def test_a_link_out_of_the_store_is_refused(self, tmp_path):
        root = data_store.prepare(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(outside, os.path.join(root, "woa23"))
        with pytest.raises(data_store.OutsideTheStore):
            data_store._guard(os.path.join(root, "woa23", "a.nc"), root)

    def test_the_store_is_made_only_in_a_folder_that_exists(self, tmp_path):
        with pytest.raises(ValueError, match="does not exist"):
            data_store.prepare(tmp_path / "new")
        assert not (tmp_path / "new").exists()
        assert data_store.prepare(tmp_path) == str(tmp_path / "oceanval_datastore")

    def test_a_file_named_in_the_index_outside_the_store_is_never_removed(self, tmp_path, store):
        root = store()
        mine = tmp_path / "mine.nc"
        mine.write_text("mine")
        os.makedirs(os.path.join(root, "woa23"))
        index = {"version": 1, "entries": [
            {"kind": "gridded", "dataset": "woa23", "url": "u", "variable": "n_an", "year": None,
             "levels": "all", "box": [0, 1, 0, 1], "file": "../mine.nc"},
            {"kind": "gridded", "dataset": "woa23", "url": "u", "variable": "n_an", "year": None,
             "levels": "all", "box": [0, 1, 0, 1], "file": str(mine)},
        ]}
        with open(os.path.join(root, data_store.INDEX), "w") as handle:
            json.dump(index, handle)
        # neither can be used, and a piece replacing them leaves the file alone
        assert data_store._entries(root) == []
        source = tmp_path / "new.nc"
        source.write_text("new")
        kept = data_store._save(
            root, dict(kind="gridded", dataset="woa23", url="u", variable="n_an", year=None,
                       levels="all", box=[0, 2, 0, 2]),
            lambda path: shutil.copyfile(source, path), ".nc",
            lambda old: data_store.contains([0, 2, 0, 2], old["box"]),
        )
        assert kept is not None
        assert mine.read_text() == "mine"
        assert [x["file"] for x in entries(root)] == [kept["file"]]


# --------------------------------------------------------------------------
# the index


class TestIndex:
    def test_an_index_that_cannot_be_read_is_not_written_over(self, tmp_path, store, capsys):
        root = store()
        where = os.path.join(root, data_store.INDEX)
        with open(where, "w") as handle:
            handle.write("{ not json")
        assert data_store._entries(root) == []
        kept = data_store._save(root, dict(kind="ices", dataset="ICE", year=2000, box=[0, 1, 0, 1], rows=0),
                                None, ".csv.gz", lambda old: False)
        assert kept is None
        assert open(where).read() == "{ not json"
        assert "COULD NOT ADD TO YOUR DATA STORE" in capsys.readouterr().out

    def test_an_index_from_a_newer_oceanval_is_not_used(self, tmp_path, store):
        root = store()
        with open(os.path.join(root, data_store.INDEX), "w") as handle:
            json.dump({"version": 99, "entries": []}, handle)
        assert data_store._load(root)[1] == "it is from a newer OceanVal (version 99)"

    def test_a_store_that_cannot_be_written_to_does_not_stop_the_run(self, tmp_path, store, server):
        root = store()
        os.chmod(root, 0o500)
        try:
            ds = data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top")
        finally:
            os.chmod(root, 0o700)
        assert len(ds) == 12
        assert len(values(ds).n_an.lat) > 0

    def test_a_store_that_has_gone_is_not_used(self, tmp_path, capsys):
        data_store.start({"folder": str(tmp_path / "gone"), "root": data_store.root(tmp_path / "gone"),
                          "use": "always"})
        try:
            assert data_store.active() is None
            assert "YOUR DATA STORE CANNOT BE USED" in capsys.readouterr().out
            assert not (tmp_path / "gone").exists()
        finally:
            data_store.start(None)


# --------------------------------------------------------------------------
# gridded data


class TestGridded:
    def test_without_a_store_nothing_is_done(self, server):
        data_store.start(None)
        assert data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top") is None
        assert server.opened == []

    def test_data_not_from_oceanvals_own_recipes_is_not_kept(self, store, server):
        store()
        for comparison in [{"builtin": None, "obs_variable": "n_an"}, {"obs_variable": "n_an"}]:
            assert data_store.gridded(comparison, WOA_URLS, True, MODEL_BOX, "top") is None
        assert server.opened == []

    def test_a_download_is_kept_cut_to_the_model_area(self, store, server):
        root = store()
        ds = data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top")
        assert len(server.opened) == 12
        kept = entries(root)
        assert len(kept) == 12
        assert {tuple(x["box"]) for x in kept} == {(3, 4, 54, 55)}
        assert {x["levels"] for x in kept} == {"top"}
        assert all(x["file"].startswith("woa23/") for x in kept)
        got = values(ds)
        assert float(got.lon.min()) >= 3 and float(got.lon.max()) <= 4
        assert len(got.depth) == 1

    def test_what_is_kept_is_used_without_the_server(self, store, server):
        store()
        first = values(data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top"))
        server.opened.clear()
        server.down = True
        store()
        second = values(data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top"))
        assert server.opened == []
        xr.testing.assert_equal(first.n_an, second.n_an)

    def test_a_smaller_area_is_taken_from_a_larger_one(self, store, server):
        store()
        data_store.gridded(woa_comparison(), WOA_URLS, True, (0.2, 8.7, 50.1, 58.9), "top")
        server.opened.clear()
        store()
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top")
        assert server.opened == []

    def test_all_the_levels_serve_the_top(self, store, server):
        store()
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "all")
        server.opened.clear()
        store()
        top = values(data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top"))
        assert server.opened == []
        # matchup takes the top level itself
        assert len(top.depth) == 5

    def test_the_top_level_does_not_serve_all_of_them(self, store, server):
        root = store()
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top")
        server.opened.clear()
        store()
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "all")
        assert len(server.opened) == 12
        # all the levels replace the top one
        assert {x["levels"] for x in entries(root)} == {"all"}
        assert len(entries(root)) == 12

    def test_a_wider_area_replaces_the_piece_with_one_covering_both(self, store, server):
        root = store()
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top")
        first_files = {x["file"] for x in entries(root)}
        store()
        data_store.gridded(woa_comparison(), WOA_URLS, True, (-2.5, -1.2, 55.1, 56.3), "top")
        kept = entries(root)
        assert len(kept) == 12
        assert {tuple(x["box"]) for x in kept} == {(-3, 4, 54, 57)}
        # the pieces replaced are kept a day, as another run may be reading them
        for name in first_files:
            assert os.path.exists(os.path.join(root, name))
        assert {x["file"] for x in retired(root)} == first_files
        server.opened.clear()
        store()
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top")
        assert server.opened == []

    def test_fallback_downloads_every_time(self, store, server):
        store("fallback")
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top")
        server.opened.clear()
        root = store("fallback")
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top")
        assert len(server.opened) == 12
        # replacing what was kept, not adding to it
        assert len(entries(root)) == 12

    def test_fallback_does_not_download_again_in_the_same_run(self, store, server):
        store("fallback")
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top")
        server.opened.clear()
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top")
        assert server.opened == []

    def test_fallback_uses_the_store_when_a_download_fails(self, store, server, capsys):
        store("fallback")
        first = values(data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top"))
        server.down = True
        session_info["end_messages"] = []
        store("fallback")
        capsys.readouterr()
        second = values(data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top"))
        xr.testing.assert_equal(first.n_an, second.n_an)
        out = capsys.readouterr().out
        assert "COULD NOT DOWNLOAD WOA23 N_AN" in out
        assert "is used instead" in out
        assert any("could not be downloaded" in x for x in session_info["end_messages"])

    @pytest.mark.parametrize("mode", ["always", "fallback"])
    def test_a_failed_download_not_in_the_store_still_fails(self, store, server, mode):
        store(mode)
        server.down = True
        with pytest.raises(ConnectionError):
            data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top")

    def test_a_year_at_a_time_and_not_one_still_being_added_to(self, store, server):
        root = store()
        comparison = {"builtin": "cobe2", "obs_variable": "sst", "recipe": True}
        ds = data_store.gridded(comparison, COBE2_URL, True, (-10.3, 5.2, 45.1, 60.2), "all",
                                years=[2000, 2001, 2002])
        assert len(ds) == 1
        got = values(ds)
        assert len(got.time) == 30
        kept = entries(root)
        # 2002 has only six months, so it is not kept
        assert sorted(x["year"] for x in kept) == [2000, 2001]
        assert {tuple(x["box"]) for x in kept} == {(-11, 6, 45, 61)}

        server.opened.clear()
        server.down = True
        store()
        again = values(data_store.gridded(comparison, COBE2_URL, True, (-10.3, 5.2, 45.1, 60.2), "all",
                                          years=[2000, 2001]))
        assert server.opened == []
        assert len(again.time) == 24
        np.testing.assert_array_equal(again.sst.values, got.sst.values[:24])


COBE2_BOX = (-10.3, 5.2, 45.1, 60.2)


class TestDownloadingInsteadOfThredds:
    """PSL's thredds server turns requests away (429) if it is asked for too many,
    as reading COBE2 over it does: the file is downloaded instead."""

    @staticmethod
    def comparison():
        return {"builtin": "cobe2", "obs_variable": "sst", "recipe": True}

    def years(self, years=(2000, 2001)):
        return data_store.gridded(self.comparison(), COBE2_URL, True, COBE2_BOX, "all", years=list(years))

    def test_the_recipe_is_read_over_thredds(self):
        recipe = find_recipe({"temperature": "cobe2"})
        assert recipe["thredds"] is True
        assert recipe["obs_path"] == data_store.DOWNLOADS["cobe2"]["thredds"]

    def test_a_server_that_answers_is_read_over_thredds(self, store, server):
        store()
        self.years()
        assert server.opened and server.downloaded == []

    def test_a_server_turning_requests_away_is_not_asked_and_the_file_is_downloaded(self, store, server, capsys):
        root = store()
        server.limited = True
        got = values(self.years([2000, 2001, 2002]))
        assert server.opened == []
        assert server.downloaded == [COBE2_DOWNLOAD]
        assert len(got.time) == 30
        assert "downloaded instead" in capsys.readouterr().out
        # kept under the thredds address, a year at a time and not the unfinished one
        kept = entries(root)
        assert sorted(x["year"] for x in kept) == [2000, 2001]
        assert {x["url"] for x in kept} == {COBE2_URL}
        assert {tuple(x["box"]) for x in kept} == {(-11, 6, 45, 61)}

    def test_what_was_downloaded_is_found_without_either_server(self, store, server):
        store()
        server.limited = True
        first = values(self.years())
        server.opened.clear()
        server.downloaded.clear()
        server.down = server.download_down = True
        store()
        again = values(self.years())
        assert server.opened == [] and server.downloaded == []
        np.testing.assert_array_equal(again.sst.values, first.sst.values)

    def test_a_server_that_fails_is_replaced_by_the_download(self, store, server):
        root = store()
        server.down = True
        got = values(self.years())
        assert server.opened and server.downloaded == [COBE2_DOWNLOAD]
        assert len(got.time) == 24
        assert sorted(x["year"] for x in entries(root)) == [2000, 2001]

    def test_both_failing_is_the_downloads_failure(self, store, server):
        store()
        server.down = server.download_down = True
        with pytest.raises(ConnectionError):
            self.years()

    def test_the_store_is_used_if_both_fail_in_fallback_mode(self, store, server):
        store("fallback")
        first = values(self.years())
        server.down = server.download_down = True
        store("fallback")
        with pytest.warns(UserWarning, match="COBE2|could not download"):
            again = values(self.years())
        np.testing.assert_array_equal(again.sst.values, first.sst.values)

    def test_a_file_that_is_not_the_built_in_one_is_never_swapped(self, store, server):
        store()
        server.limited = True
        other = "https://example.org/thredds/dodsC/mine/sst.mon.mean.nc"
        assert data_store.download_instead("cobe2", other) is None
        assert data_store.download_instead("woa23", COBE2_URL) is None
        assert data_store.download_instead("cobe2", COBE2_URL) == COBE2_DOWNLOAD


class TestServerLimits:
    @pytest.fixture
    def web(self):
        import http.server

        replies = {"code": 200}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(replies["code"])
                self.end_headers()

            def log_message(self, *args):
                pass

        httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield replies, f"http://127.0.0.1:{httpd.server_port}/file.nc"
        httpd.shutdown()
        httpd.server_close()

    @pytest.mark.parametrize("code,limits", [(200, False), (404, False), (429, True), (503, True)])
    def test_only_a_server_turning_requests_away_limits(self, web, code, limits):
        replies, url = web
        replies["code"] = code
        assert data_store._server_limits(url) is limits

    def test_a_server_that_cannot_be_reached_is_not_decided_on(self):
        assert data_store._server_limits("http://127.0.0.1:1/file.nc") is False


# --------------------------------------------------------------------------
# a matchup


def _matchup(vertical=False, **kwargs):
    oceanval.reset()
    session_info["failed_gridded"] = []
    session_info["end_messages"] = []
    oceanval.add_gridded_comparison(
        name="nitrate", model_variable="N3_n", recipe={"nitrate": "woa23"}, file_check=False,
        vertical=vertical,
    )
    if vertical:
        kwargs["thickness"] = "data/example/e3t.nc"
    oceanval.matchup(sim_dir="data/example", start=2000, end=2000, ask=False, cores=1, **kwargs)
    layer = "vertical" if vertical else "surface"
    with xr.open_dataset(f"oceanval_matchups/gridded/nitrate/WOA23_nitrate_{layer}.nc") as ds:
        found = ds.load()
    # a matchup with something in it, or the same would mean nothing
    assert int(found.observation.notnull().sum()) > 0
    assert int(found.model.notnull().sum()) > 0
    return found


@pytest.fixture
def clean_matchups():
    shutil.rmtree("oceanval_matchups", ignore_errors=True)
    yield
    shutil.rmtree("oceanval_matchups", ignore_errors=True)
    oceanval.reset()
    data_store.start(None)


class TestMatchup:
    def test_the_matchup_is_the_same_from_the_store(self, server, tmp_path, clean_matchups, capsys):
        without = _matchup(data_store=False)
        assert len(server.opened) > 0
        assert not (tmp_path / "oceanval_datastore").exists()

        user_settings.save({"data_store": str(tmp_path), "data_store_use": "always"})
        first = _matchup()
        assert len(entries(data_store.root(tmp_path))) == 12
        xr.testing.assert_equal(without, first)

        # the server is down, but nothing is asked of it
        server.opened.clear()
        server.down = True
        capsys.readouterr()
        second = _matchup()
        assert server.opened == []
        assert session_info["failed_gridded"] == []
        assert "WOA23 nitrate: taken from your data store" in capsys.readouterr().out
        xr.testing.assert_equal(without, second)

    def test_the_water_column_is_the_same_from_the_store(self, server, tmp_path, clean_matchups):
        without = _matchup(vertical=True, data_store=False)
        user_settings.save({"data_store": str(tmp_path)})
        first = _matchup(vertical=True)
        root = data_store.root(tmp_path)
        assert {x["levels"] for x in entries(root)} == {"all"}
        xr.testing.assert_equal(without, first)
        server.opened.clear()
        server.down = True
        second = _matchup(vertical=True)
        assert server.opened == []
        xr.testing.assert_equal(without, second)

    def test_a_limited_thredds_server_gives_the_same_matchup_by_download(
        self, server, fine_cobe_file, tmp_path, clean_matchups
    ):
        server.limited = True
        # the example model is smaller than a cell of the one degree file
        server.cobe_file = fine_cobe_file
        for data_store_arg in (False, None):
            oceanval.reset()
            session_info["failed_gridded"] = []
            session_info["end_messages"] = []
            oceanval.add_gridded_comparison(
                name="temperature", model_variable="votemper", recipe={"temperature": "cobe2"},
                file_check=False,
            )
            if data_store_arg is None:
                user_settings.save({"data_store": str(tmp_path)})
            oceanval.matchup(sim_dir="data/example", start=2000, end=2001, ask=False, cores=1,
                             data_store=data_store_arg)
            with xr.open_dataset("oceanval_matchups/gridded/temperature/COBE2_temperature_surface.nc") as ds:
                found = ds.load()
            assert int(found.observation.notnull().sum()) > 0
            assert session_info["failed_gridded"] == []
        assert server.opened == []
        assert server.downloaded == [COBE2_DOWNLOAD] * 2
        # and kept, under the thredds address
        kept = entries(data_store.root(tmp_path))
        assert sorted(x["year"] for x in kept) == [2000, 2001]
        assert {x["url"] for x in kept} == {COBE2_URL}

    def test_data_store_false_leaves_the_store_alone(self, server, tmp_path, clean_matchups):
        user_settings.save({"data_store": str(tmp_path)})
        _matchup(data_store=False)
        assert only_the_marker(tmp_path / "oceanval_datastore")

    def test_data_store_must_be_none_or_false(self, clean_matchups):
        with pytest.raises(ValueError, match="data_store must be None"):
            oceanval.matchup(sim_dir="data/example", start=2000, end=2000, ask=False, cores=1,
                             data_store="/data")

    def test_your_own_data_at_the_same_address_is_not_kept(self, server, tmp_path, clean_matchups):
        user_settings.save({"data_store": str(tmp_path)})
        oceanval.reset()
        oceanval.add_gridded_comparison(
            name="nitrate", model_variable="N3_n", obs_path=WOA_URLS, obs_variable="n_an",
            source="mine", thredds=True, climatology=True, file_check=False,
        )
        oceanval.matchup(sim_dir="data/example", start=2000, end=2000, ask=False, cores=1)
        assert os.path.exists("oceanval_matchups/gridded/nitrate/mine_nitrate_surface.nc")
        assert only_the_marker(tmp_path / "oceanval_datastore")

    def test_a_recipe_given_other_data_is_not_kept(self):
        oceanval.reset()
        oceanval.add_gridded_comparison(
            name="nitrate", model_variable="N3_n", recipe={"nitrate": "woa23"},
            obs_path="data/evaldata/gridded/nws/temperature", file_check=False,
        )
        from oceanval.parsers import definitions

        assert definitions["nitrate"].gridded_comparisons["WOA23"]["builtin"] is None
        oceanval.reset()
        oceanval.add_gridded_comparison(name="nitrate", model_variable="N3_n",
                                        recipe={"nitrate": "woa23"}, file_check=False)
        assert definitions["nitrate"].gridded_comparisons["WOA23"]["builtin"] == "woa23"
        oceanval.reset()


# --------------------------------------------------------------------------
# ICES data

TEMP = "Temperature (TEMPPR01_UPAA) [degC]"
NTRA = "Nitrate (NTRAZZXX_UPOX) [umol/l]"
DEPTH = "Depth (ADEPZZ01_ULAA) [m]"
HEADER = ["Cruise", "Station", "Type", ices.TIME_COLUMN, ices.LON_COLUMN, ices.LAT_COLUMN,
          DEPTH, f"QV:ODV:{DEPTH}", TEMP, f"QV:ODV:{TEMP}", NTRA, f"QV:ODV:{NTRA}"]


def _station(time, lon, lat, temp, ntra):
    return ["26DA", "0003", "B", time, lon, lat, 5.0, 0, temp, 0, ntra, 0]


# what ICES has: stations over the years, some on the edges of boxes
ICES_ROWS = [
    _station("1999-06-01T10:00Z", 3.5, 54.5, 9.0, 4.0),
    _station("2000-03-04T13:38Z", 3.6853, 54.8707, 8.5, 5.5),
    _station("2000-07-01T12:00Z", 3.2, 54.1, 15.1, 1.0),
    _station("2001-02-02T08:00Z", 3.0, 54.0, 6.2, 9.0),
    _station("2001-12-31T24:00Z", 3.4, 54.4, 7.7, 8.8),
    _station("2002-05-05T05:05Z", 9.5, 58.5, 10.0, 3.3),
    _station("2003-08-08T08:08Z", 3.3, 54.3, 17.0, 0.4),
]


class FakeIcesApi:
    """The ICES API: exports cut by ICES to the years and box asked for."""

    def __init__(self, rows):
        self.rows = rows
        self.asked = []
        self.down = False

    def start_job(self, session, criteria):
        if self.down:
            raise ConnectionError("ocean.ices.dk could not be reached")
        self.asked.append((criteria["dataset"], criteria["dateFrom"][:4], criteria["dateTo"][:4],
                           criteria["longitudeWest"], criteria["longitudeEast"],
                           criteria["latitudeSouth"], criteria["latitudeNorth"]))
        return json.dumps(criteria)

    def wait_for_job(self, session, job_id):
        return True

    def export(self, criteria):
        import io
        import zipfile

        rows = [
            x for x in self.rows
            if criteria["dateFrom"][:4] <= x[3][:4] <= criteria["dateTo"][:4]
            and criteria["longitudeWest"] <= x[4] <= criteria["longitudeEast"]
            and criteria["latitudeSouth"] <= x[5] <= criteria["latitudeNorth"]
        ]
        lines = ["//comment", ",".join(HEADER)] + [",".join(str(y) for y in x) for x in rows]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("data.csv", "\n".join(lines))
        return buffer.getvalue()

    def session(self):
        fake = self

        class Response:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                pass

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, timeout):
                return Response(fake.export(json.loads(url.rsplit("/", 1)[1])))

        return Session()


@pytest.fixture
def ices_api(monkeypatch):
    fake = FakeIcesApi(ICES_ROWS)
    monkeypatch.setattr(ices, "_start_job", fake.start_job)
    monkeypatch.setattr(ices, "_wait_for_job", fake.wait_for_job)
    monkeypatch.setattr(ices.requests, "Session", fake.session)
    return fake


NITRATE = {"parameter": "NTRAZZXX", "dataset": "ICE"}
BOX = ([3.0, 3.7], [54.0, 54.9])


class TestIces:
    def test_without_a_store_the_download_is_as_before(self, ices_api):
        data_store.start(None)
        df = ices.download_point_data(NITRATE, 2000, 2001, *BOX)
        assert ices_api.asked == [("ICE", "2000", "2001", 3.0, 3.7, 54.0, 54.9)]
        assert sorted(df.observation) == [1.0, 5.5, 8.8, 9.0]

    def test_the_store_gives_what_the_download_gives(self, ices_api, store):
        data_store.start(None)
        direct = ices.download_point_data(NITRATE, 2000, 2001, *BOX)
        ices_api.asked.clear()
        root = store()
        stored = ices.download_point_data(NITRATE, 2000, 2001, *BOX)
        # asked for a box rounded out to whole degrees
        assert ices_api.asked == [("ICE", "2000", "2001", 3.0, 4.0, 54.0, 55.0)]
        pd.testing.assert_frame_equal(direct, stored)
        assert sorted(x["year"] for x in entries(root)) == [2000, 2001]
        assert all(x["file"].startswith("ices/") for x in entries(root))

        ices_api.asked.clear()
        store()
        again = ices.download_point_data(NITRATE, 2000, 2001, *BOX)
        assert ices_api.asked == []
        pd.testing.assert_frame_equal(direct, again)

    def test_one_export_serves_every_variable(self, ices_api, store):
        store()
        ices.download_point_data(NITRATE, 2000, 2001, *BOX)
        ices_api.asked.clear()
        temperature = ices.download_point_data({"parameter": "TEMPPR01", "dataset": "ICE"}, 2000, 2001, *BOX)
        assert ices_api.asked == []
        assert sorted(temperature.observation) == [6.2, 7.7, 8.5, 15.1]

    def test_a_smaller_box_and_fewer_years_are_taken_from_the_store(self, ices_api, store):
        store()
        ices.download_point_data(NITRATE, 2000, 2001, *BOX)
        ices_api.asked.clear()
        store()
        df = ices.download_point_data(NITRATE, 2001, 2001, [3.1, 3.5], [54.2, 54.5])
        assert ices_api.asked == []
        assert sorted(df.observation) == [8.8]

    def test_only_the_years_missing_are_downloaded(self, ices_api, store):
        store()
        ices.download_point_data(NITRATE, 2000, 2001, *BOX)
        ices_api.asked.clear()
        store()
        df = ices.download_point_data(NITRATE, 1999, 2003, *BOX)
        assert [x[:3] for x in ices_api.asked] == [("ICE", "1999", "1999"), ("ICE", "2002", "2003")]
        assert sorted(df.observation) == [0.4, 1.0, 4.0, 5.5, 8.8, 9.0]

    def test_a_year_without_data_is_kept_as_one(self, ices_api, store):
        root = store()
        ices.download_point_data(NITRATE, 2002, 2002, *BOX)
        assert [(x["year"], x["rows"], x["file"]) for x in entries(root)] == [(2002, 0, None)]
        ices_api.asked.clear()
        store()
        assert len(ices.download_point_data(NITRATE, 2002, 2002, *BOX)) == 0
        assert ices_api.asked == []

    def test_a_larger_box_is_downloaded_again(self, ices_api, store):
        root = store()
        ices.download_point_data(NITRATE, 2002, 2002, *BOX)
        ices_api.asked.clear()
        store()
        df = ices.download_point_data(NITRATE, 2002, 2002, [3.0, 9.6], [54.0, 58.6])
        assert len(ices_api.asked) == 1
        assert sorted(df.observation) == [3.3]
        # the larger box has the smaller one in it, so it replaces it
        assert [x["box"] for x in entries(root)] == [[3, 10, 54, 59]]

    def test_fallback_downloads_every_time_and_falls_back(self, ices_api, store, capsys):
        store("fallback")
        first = ices.download_point_data(NITRATE, 2000, 2001, *BOX)
        ices_api.asked.clear()
        store("fallback")
        ices.download_point_data(NITRATE, 2000, 2001, *BOX)
        assert len(ices_api.asked) == 1

        ices_api.down = True
        store("fallback")
        capsys.readouterr()
        again = ices.download_point_data(NITRATE, 2000, 2001, *BOX)
        pd.testing.assert_frame_equal(first, again)
        assert "COULD NOT DOWNLOAD ICES ICE DATA FOR 2000-2001" in capsys.readouterr().out

    def test_a_failed_download_not_in_the_store_still_fails(self, ices_api, store):
        store()
        ices_api.down = True
        with pytest.raises(ConnectionError):
            ices.download_point_data(NITRATE, 2000, 2001, *BOX)

    def test_ctd_and_bottle_data_are_kept_apart(self, ices_api, store):
        root = store()
        ices.download_point_data({"parameter": "TEMPPR01", "dataset": ["CTD", "ICE"]}, 2000, 2000, *BOX)
        assert sorted(x["dataset"] for x in entries(root)) == ["CTD", "ICE"]


# --------------------------------------------------------------------------
# safety: nothing of yours is ever removed


def record_changes(monkeypatch):
    """Record every file removed, moved or copied to, from anywhere, while
    the test runs."""
    changed = []

    def wrap(module, name, which):
        real = getattr(module, name)

        def recording(*args, **kwargs):
            changed.append((name, os.fspath(args[which])))
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, recording)

    for name in ("remove", "unlink", "rmdir"):
        wrap(os, name, 0)
    wrap(os, "replace", 1)
    wrap(os, "rename", 1)
    wrap(shutil, "rmtree", 0)
    wrap(shutil, "copyfile", 1)
    return changed


def inside(path, root):
    real, base = os.path.realpath(path), os.path.realpath(root)
    return os.path.commonpath([real, base]) == base


def nctoolkit_temp(path):
    return "nctoolkit" in os.path.basename(path) and path.startswith(("/tmp/", "/var/tmp/", "/usr/tmp/"))


def snapshot(paths):
    return {str(x): (os.lstat(x).st_mtime_ns, x.read_bytes() if not x.is_symlink() else os.readlink(x)) for x in paths}


class TestNothingOfYoursIsRemoved:
    def test_a_store_among_your_files_with_a_damaged_index(self, tmp_path, server, monkeypatch, store):
        parent = tmp_path / "work"
        parent.mkdir()
        root = data_store.prepare(parent)
        hex1, hex2 = "a" * 32, "b" * 32
        # your files: beside the store, in a folder beside it, and in the store
        (parent / "user.nc").write_text("mine")
        (parent / "notes.txt").write_text("mine too")
        (parent / "other").mkdir()
        (parent / "other" / f"{hex2}.nc").write_text("named like a piece, outside")
        os.makedirs(os.path.join(root, "woa23"))
        mine = tmp_path / "work" / "oceanval_datastore" / "woa23" / "mine.nc"
        mine.write_text("kept in the store by hand")
        # links in the store, named like pieces, out to your files
        os.symlink(parent / "user.nc", os.path.join(root, "woa23", f"{hex1}.nc"))
        os.symlink(parent / "other", os.path.join(root, "occci"))
        yours = [parent / "user.nc", parent / "notes.txt", parent / "other" / f"{hex2}.nc", mine,
                 tmp_path / "work" / "oceanval_datastore" / "woa23" / f"{hex1}.nc",
                 tmp_path / "work" / "oceanval_datastore" / "occci"]
        before = snapshot(yours)

        names = ["../user.nc", str(parent / "user.nc"), "woa23/../../user.nc", "woa23/mine.nc",
                 f"woa23/{hex1}.nc", f"occci/{hex2}.nc", "", None, 3, "woa23/"]
        damaged = [
            {"kind": "gridded", "dataset": "woa23", "url": url, "variable": "n_an", "year": None,
             "levels": "top", "box": [3, 4, 54, 55], "file": name}
            for url in WOA_URLS for name in names
        ]
        with open(os.path.join(root, data_store.INDEX), "w") as handle:
            json.dump({"version": 1, "entries": damaged,
                       "retired": [{"file": x, "retired": 0} for x in names]}, handle)

        changed = record_changes(monkeypatch)
        # every way the store changes: runs that tidy it, add to it, replace
        # what it has, fall back on it, keep ICES data, and tidy it a day later
        store("always", folder=parent)
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top")
        store("fallback", folder=parent)
        data_store.gridded(woa_comparison(), WOA_URLS, True, (-2.5, -1.2, 55.1, 56.3), "all")
        server.down = True
        store("fallback", folder=parent)
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "all")
        rows = pd.DataFrame({ices.TIME_COLUMN: ["2000-01-01"], ices.LON_COLUMN: ["3.5"], ices.LAT_COLUMN: ["54.5"]})
        data_store.ices(["ICE"], 2000, 2000, [3.1, 3.4], [54.1, 54.4], lambda wanted: [{2000: rows}])
        monkeypatch.setattr(data_store, "_now", lambda: __import__("time").time() + 3 * 86400)
        store("always", folder=parent)

        assert snapshot(yours) == before
        # the pieces replaced were removed, a day on, while this was watched
        assert any(
            name == "remove" and data_store.PIECE_NAME.match(os.path.relpath(path, root))
            for name, path in changed
        )
        assert retired(root) == []
        for name, path in changed:
            assert inside(path, root) or nctoolkit_temp(path), (name, path)
            if name in ("remove", "unlink"):
                assert data_store.PIECE_NAME.match(os.path.relpath(path, root)) or any(
                    x.match(os.path.relpath(path, root)) for x in data_store.LEFTOVER_NAMES
                ) or nctoolkit_temp(path), path
            assert name not in ("rmdir", "rmtree"), path

    def test_a_file_in_the_store_not_named_by_oceanval_is_neither_used_nor_removed(self, store, server, monkeypatch):
        root = store()
        os.makedirs(os.path.join(root, "woa23"))
        mine = os.path.join(root, "woa23", "mine.nc")
        shutil.copyfile(server.woa_files[1], mine)
        with open(os.path.join(root, data_store.INDEX), "w") as handle:
            json.dump({"version": 1, "entries": [
                {"kind": "gridded", "dataset": "woa23", "url": WOA_URLS[0], "variable": "n_an", "year": None,
                 "levels": "all", "box": [-180, 180, -90, 90], "file": "woa23/mine.nc"}]}, handle)
        assert data_store._entries(root) == []
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "all")
        assert WOA_URLS[0] in server.opened
        monkeypatch.setattr(data_store, "_now", lambda: __import__("time").time() + 3 * 86400)
        store()
        assert os.path.exists(mine)


class TestTheStoresFolder:
    def test_a_folder_of_that_name_with_your_files_in_it_is_not_used(self, tmp_path, capsys):
        (tmp_path / "oceanval_datastore").mkdir()
        (tmp_path / "oceanval_datastore" / "mine.txt").write_text("mine")
        with pytest.raises(ValueError, match="OceanVal did not put there"):
            data_store.prepare(tmp_path)
        assert [x.name for x in (tmp_path / "oceanval_datastore").iterdir()] == ["mine.txt"]
        data_store.start({"folder": str(tmp_path), "root": data_store.root(tmp_path), "use": "always"})
        try:
            assert data_store.active() is None
            assert "YOUR DATA STORE CANNOT BE USED" in capsys.readouterr().out
        finally:
            data_store.start(None)
        assert [x.name for x in (tmp_path / "oceanval_datastore").iterdir()] == ["mine.txt"]

    def test_an_empty_folder_of_that_name_is_taken_and_marked(self, tmp_path):
        (tmp_path / "oceanval_datastore").mkdir()
        data_store.prepare(tmp_path)
        assert only_the_marker(tmp_path / "oceanval_datastore")
        assert "OceanVal's data store" in (tmp_path / "oceanval_datastore" / data_store.MARKER).read_text()

    def test_a_store_with_an_index_is_oceanvals(self, tmp_path):
        root = tmp_path / "oceanval_datastore"
        root.mkdir()
        (root / data_store.INDEX).write_text(json.dumps({"version": 1, "entries": []}))
        data_store.prepare(tmp_path)
        assert (root / data_store.MARKER).exists()

    def test_the_marker_is_never_written_over(self, tmp_path):
        root = tmp_path / "oceanval_datastore"
        root.mkdir()
        (root / data_store.MARKER).write_text("as it was")
        data_store.prepare(tmp_path)
        assert (root / data_store.MARKER).read_text() == "as it was"

    @pytest.mark.parametrize("kind", ["file", "link"])
    def test_something_else_of_that_name_is_not_used(self, tmp_path, kind):
        target = tmp_path / "oceanval_datastore"
        if kind == "file":
            target.write_text("mine")
        else:
            (tmp_path / "elsewhere").mkdir()
            os.symlink(tmp_path / "elsewhere", target)
        with pytest.raises(ValueError, match="not a folder"):
            data_store.prepare(tmp_path)
        if kind == "link":
            assert list((tmp_path / "elsewhere").iterdir()) == []

    @pytest.mark.parametrize("cleared", data_store.CLEARED_FOLDERS)
    def test_never_in_a_folder_oceanval_empties(self, tmp_path, oceanvalrc, cleared):
        folder = tmp_path / "run" / cleared / "deeper"
        folder.mkdir(parents=True)
        with pytest.raises(ValueError, match="empties"):
            data_store.prepare(folder)
        with pytest.raises(ValueError, match="empties"):
            user_settings.save({"data_store": str(folder)})
        assert list(folder.iterdir()) == []
        # nor by way of a link
        os.symlink(folder, tmp_path / "link")
        with pytest.raises(ValueError, match="empties"):
            data_store.prepare(tmp_path / "link")


class TestTidying:
    def test_a_write_that_fails_leaves_nothing_behind(self, store, capsys):
        root = store()

        def half_written(path):
            with open(path, "w") as handle:
                handle.write("half")
            raise OSError("No space left on device")

        assert data_store._save(root, dict(kind="ices", dataset="ICE", year=2000, box=[0, 1, 0, 1], rows=1),
                                half_written, ".csv.gz", lambda old: False) is None
        assert os.listdir(os.path.join(root, ".partial")) == []
        assert "No space left on device" in capsys.readouterr().out

    def test_what_was_left_as_it_was_written_goes_once_a_day_old(self, store, monkeypatch):
        root = store()
        os.makedirs(os.path.join(root, ".partial"))
        old = 0
        made = {
            ".partial/" + "c" * 32 + ".nc": True,
            ".partial/" + "d" * 32 + ".csv.gz": True,
            data_store.INDEX + ".abc_12.tmp": True,
            ".write_check.xyz": True,
            ".partial/notes.txt": False,
            "notes.txt": False,
        }
        for name in made:
            path = os.path.join(root, name)
            with open(path, "w") as handle:
                handle.write("x")
            os.utime(path, (old, old))
        fresh = os.path.join(root, ".partial", "e" * 32 + ".nc")
        with open(fresh, "w") as handle:
            handle.write("being written")
        store()
        for name, goes in made.items():
            assert os.path.exists(os.path.join(root, name)) is not goes, name
        assert os.path.exists(fresh)

    def test_a_replaced_piece_goes_a_day_later(self, store, server, monkeypatch):
        root = store()
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top")
        old = {x["file"] for x in entries(root)}
        reading = nc.open_data(os.path.join(root, sorted(old)[0]), checks=False)
        store()
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "all")
        # another run, still reading what was replaced, can carry on
        reading.subset(variables="n_an")
        reading.run()
        assert {x["file"] for x in retired(root)} == old
        assert all(os.path.exists(os.path.join(root, x)) for x in old)
        # a run an hour on leaves it
        now = __import__("time").time()
        monkeypatch.setattr(data_store, "_now", lambda: now + 3600)
        store()
        assert all(os.path.exists(os.path.join(root, x)) for x in old)
        # a run a day on removes it
        monkeypatch.setattr(data_store, "_now", lambda: now + 86400 + 60)
        store()
        assert not any(os.path.exists(os.path.join(root, x)) for x in old)
        assert retired(root) == []
        assert len(entries(root)) == 12
        assert all(os.path.exists(os.path.join(root, x["file"])) for x in entries(root))

    def test_a_retired_file_still_in_use_is_kept(self, store, server, monkeypatch):
        root = store()
        data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top")
        name = entries(root)[0]["file"]
        with open(os.path.join(root, data_store.INDEX)) as handle:
            index = json.load(handle)
        index["retired"] = [{"file": name, "retired": 0}]
        with open(os.path.join(root, data_store.INDEX), "w") as handle:
            json.dump(index, handle)
        store()
        assert os.path.exists(os.path.join(root, name))
