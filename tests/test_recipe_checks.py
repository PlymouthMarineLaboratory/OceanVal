"""Looking at the data of a recipe of the user's own, and the checks the
oceanval window makes of a form before it saves one."""

import functools
import http.server
import os
import socket
import threading

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanval import recipe_checks, recipe_forms
from oceanval.recipe_checks import CheckFailed, RemoteCheck, inspect_gridded, inspect_point

NITRATE_DIR = os.path.abspath("data/evaldata/gridded/nws/nitrate")
NITRATE = os.path.join(NITRATE_DIR, "model_2000.nc")
POINTS = os.path.abspath("data/chl/obs_csv")


def netcdf(path, years=(2000, 2001), levels=None, units="mg m-3"):
    time = pd.date_range(f"{years[0]}-01-15", f"{years[1]}-12-15", freq="MS") + pd.Timedelta(days=14)
    shape = (len(time), 2, 2) if levels is None else (len(time), levels, 2, 2)
    dims = ("time", "lat", "lon") if levels is None else ("time", "depth", "lat", "lon")
    coords = {"time": time, "lat": [50.0, 51.0], "lon": [1.0, 2.0]}
    if levels:
        coords["depth"] = np.arange(levels, dtype="float32")
    xr.Dataset(
        {"chl": (dims, np.ones(shape, dtype="float32"), {"units": units, "long_name": "chlorophyll"})},
        coords=coords,
    ).to_netcdf(path)
    return str(path)


class TestGridded:
    def test_a_file(self):
        found = inspect_gridded("disk", NITRATE)
        assert found["obs_path"] == NITRATE
        assert found["files"] == 1
        assert found["years"] == [2000, 2000]
        assert found["variables"] == [
            {"name": "N3_n", "long_name": "nitrate nitrogen", "units": "mmol N/m^3", "nlevels": 1}
        ]

    def test_a_directory_and_a_pattern(self):
        assert inspect_gridded("disk", NITRATE_DIR)["files"] == 1
        pattern = os.path.abspath("data/evaldata/gridded/nws/*/model_2000.nc")
        found = inspect_gridded("disk", pattern)
        assert found["files"] == 2 and found["obs_path"] == pattern

    def test_a_relative_path_is_from_the_directory_worked_in(self):
        found = inspect_gridded("disk", "nitrate/model_2000.nc", cwd=os.path.dirname(NITRATE_DIR))
        assert found["obs_path"] == NITRATE

    def test_the_years_are_those_of_the_first_and_last_files(self, tmp_path):
        netcdf(tmp_path / "a_2000.nc", (2000, 2000))
        netcdf(tmp_path / "b_2003.nc", (2003, 2003))
        found = inspect_gridded("disk", str(tmp_path / "*.nc"))
        assert found["years"] == [2000, 2003]

    def test_levels_and_units(self, tmp_path):
        found = inspect_gridded("disk", netcdf(tmp_path / "d.nc", levels=3, units="umol/kg"))
        assert found["variables"][0]["nlevels"] == 3
        assert found["variables"][0]["units"] == "umol/kg"

    @pytest.mark.parametrize(
        "path, message",
        [
            ("/nowhere.nc", "no file or directory"),
            ("/nowhere/*.nc", "No files match"),
            (os.path.join(POINTS, "chl_model_sum.csv"), "has to end in .nc"),
            (os.path.abspath("data/evaldata/gridded/nws/*/model_2000"), "has to end in .nc"),
            (os.path.abspath("data/evaldata/gridded/nws/*/nothing.nc"), "No files match"),
            (POINTS, "no netCDF files"),
        ],
    )
    def test_data_that_cannot_be_used(self, path, message):
        with pytest.raises(CheckFailed, match=message):
            inspect_gridded("disk", path)

    def test_a_file_that_is_not_netcdf(self, tmp_path):
        bad = tmp_path / "bad.nc"
        bad.write_text("not netcdf")
        with pytest.raises(CheckFailed, match="could not be read"):
            inspect_gridded("disk", str(bad))

    @pytest.mark.parametrize(
        "location, urls, message",
        [
            ("thredds", [], "web address of the data"),
            ("thredds", ["ftp.example.org/a.nc"], "not a web address"),
            ("thredds", ["https://x.org/thredds/catalog/a/catalog.html"], "catalogue page"),
            ("thredds", ["https://x.org/thredds/fileServer/a.nc"], "download link"),
            ("thredds", ["https://x.org/thredds/dodsC/a"], "has to end in .nc"),
            ("url", ["https://x.org/thredds/dodsC/a.nc"], "choose THREDDS"),
            ("url", ["https://x.org/a.nc", "https://x.org/b.nc"], "just one"),
        ],
    )
    def test_addresses_that_cannot_be_used(self, location, urls, message):
        with pytest.raises(CheckFailed, match=message):
            recipe_checks.check_addresses(location, urls)

    def test_addresses_are_split(self):
        assert recipe_checks.addresses("https://a/x.nc\n https://b/y.nc,https://c/z.nc ") == [
            "https://a/x.nc", "https://b/y.nc", "https://c/z.nc",
        ]


class TestPoint:
    def test_a_directory(self):
        found = inspect_point(POINTS)
        assert found["files"] == 1
        assert found["columns"] == ["lon", "lat", "observation", "year", "month", "day"]
        assert found["depth"] is False
        assert found["years"] == [2000, 2000]
        assert found["lon"][0] < found["lon"][1]

    def test_a_depth_column(self, tmp_path):
        pd.DataFrame({"lon": [1], "lat": [2], "observation": [3], "depth": [4]}).to_csv(
            tmp_path / "a.csv", index=False
        )
        assert inspect_point(str(tmp_path))["depth"] is True

    @pytest.mark.parametrize(
        "frame, message",
        [
            ({"lon": [1], "lat": [2]}, "no observation column"),
            ({"lon": [1], "lat": [2], "observation": [3], "extra": [4]}, "does not use: extra"),
            ({"observation": [3]}, "no lon, lat columns"),
        ],
    )
    def test_columns_that_cannot_be_used(self, tmp_path, frame, message):
        pd.DataFrame(frame).to_csv(tmp_path / "a.csv", index=False)
        with pytest.raises(CheckFailed, match=message):
            inspect_point(str(tmp_path))

    def test_places_with_no_csv_files(self, tmp_path):
        with pytest.raises(CheckFailed, match="no csv files"):
            inspect_point(str(tmp_path))
        with pytest.raises(CheckFailed, match="no file or directory"):
            inspect_point(str(tmp_path / "nothing"))

    def test_a_csv_file(self, tmp_path):
        pd.DataFrame({"lon": [1], "lat": [2], "observation": [3], "year": [2000]}).to_csv(
            tmp_path / "a.csv", index=False
        )
        pd.DataFrame({"lon": [5], "lat": [2], "observation": [3]}).to_csv(
            tmp_path / "b.csv", index=False
        )
        found = inspect_point(str(tmp_path / "a.csv"))
        assert found["files"] == 1
        assert found["obs_path"] == str(tmp_path / "a.csv")
        assert found["years"] == [2000, 2000]

    def test_longitudes_from_0_to_360_are_reported_as_matchup_reads_them(self, tmp_path):
        pd.DataFrame({"lon": [190, 350], "lat": [2, 3], "observation": [3, 4]}).to_csv(
            tmp_path / "a.csv", index=False
        )
        assert inspect_point(str(tmp_path))["lon"] == [-170, -10]


def wait_for(check):
    done = threading.Event()
    replies = []
    check.done = lambda reply: (replies.append(reply), done.set())
    check.start()
    assert done.wait(120), "the check never finished"
    return replies[0]


@pytest.fixture
def served():
    """The directory of the nitrate file, served over http, as a web address."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=NITRATE_DIR)
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/model_2000.nc"
    server.shutdown()


class TestOnAServer:
    def test_a_file_to_download(self, served):
        reply = wait_for(RemoteCheck("url", served, None))
        assert reply["ok"], reply
        assert reply["found"]["obs_path"] == served
        assert reply["found"]["variables"][0]["name"] == "N3_n"
        assert reply["found"]["years"] == [2000, 2000]

    def test_a_server_that_is_not_there(self):
        reply = wait_for(RemoteCheck("thredds", ["http://127.0.0.1:1/x.nc"], None))
        assert not reply["ok"]
        assert "could not be reached: the connection was refused" in reply["error"]

    def test_an_address_the_server_does_not_have(self, served):
        reply = wait_for(RemoteCheck("url", served.replace("model_2000", "nothing"), None))
        assert not reply["ok"]
        assert "404" in reply["error"]

    def test_a_server_that_does_not_answer_is_stopped(self):
        quiet = socket.socket()
        quiet.bind(("127.0.0.1", 0))
        quiet.listen(5)
        try:
            address = f"http://127.0.0.1:{quiet.getsockname()[1]}/x.nc"
            reply = wait_for(RemoteCheck("thredds", [address], None, timeout=4))
        finally:
            quiet.close()
        assert not reply["ok"]
        assert "did not answer" in reply["error"]

    def test_a_check_that_is_cancelled_says_nothing(self):
        quiet = socket.socket()
        quiet.bind(("127.0.0.1", 0))
        quiet.listen(5)
        replies = []
        try:
            address = f"http://127.0.0.1:{quiet.getsockname()[1]}/x.nc"
            check = RemoteCheck("thredds", [address], replies.append, timeout=60)
            check.start()
            for _ in range(100):
                if check.process is not None:
                    break
                threading.Event().wait(0.1)
            check.cancel()
            check.process.wait(timeout=30)
        finally:
            quiet.close()
        assert check.process.returncode is not None
        assert replies == []


def form(kind="gridded", **changes):
    boxes = recipe_forms.default_form(kind)
    boxes.update(
        variable="nitrate", source="MySat", source_info="My product", units="mmol N/m^3",
        where="local", obs_path=NITRATE,
    )
    if kind == "gridded":
        boxes.update(obs_variable="N3_n", climatology="yes")
    boxes.update(changes)
    return boxes


class TestForms:
    def test_the_boxes_as_they_start(self):
        assert recipe_forms.default_form("gridded")["location"] == "disk"
        assert "obs_variable" not in recipe_forms.default_form("point")

    def test_what_is_sent_is_limited_to_the_boxes(self):
        cleaned = recipe_forms.clean("point", {"variable": " dic ", "nonsense": 1, "units": None})
        assert cleaned["variable"] == "dic" and cleaned["units"] == "" and "nonsense" not in cleaned

    def test_names_are_checked_as_they_are_typed(self, tmp_path):
        errors, warnings, info = recipe_forms.check_names("gridded", form(variable="", source="x y"), str(tmp_path))
        assert list(errors) == ["source"]
        errors, _, info = recipe_forms.check_names("gridded", form(source="WOA23"), str(tmp_path))
        assert "own recipes" in errors["source"]
        errors, _, info = recipe_forms.check_names("gridded", form(), str(tmp_path))
        assert errors == {}
        assert info["recipe"] == 'recipe={"nitrate": "mysat"}'
        assert info["known"] is True and info["labels"][2] == "Nitrate"

    def test_where_is_only_asked_for_when_saving(self, tmp_path):
        errors, _, _ = recipe_forms.check_names("gridded", form(where=""), str(tmp_path))
        assert errors == {}
        errors, _, _ = recipe_forms.check_names("gridded", form(where=""), str(tmp_path), strict=True)
        assert errors == {"where": "Choose where to save it."}

    def test_a_sound_form_is_a_recipe(self, tmp_path):
        recipe, errors, warnings, data = recipe_forms.check_save("gridded", form(), str(tmp_path))
        assert errors == {}
        assert recipe == {
            "variable": "nitrate", "kind": "gridded", "source": "MySat",
            "source_info": "My product", "units": "mmol N/m^3", "obs_path": NITRATE,
            "location": "disk", "obs_variable": "N3_n", "climatology": True,
            "depth_resolved": False,
        }
        assert data["files"] == 1

    def test_a_new_variable_is_named(self, tmp_path):
        recipe, errors, _, _ = recipe_forms.check_save(
            "gridded", form(variable="dic", short_title="DIC"), str(tmp_path)
        )
        assert errors == {}
        assert (recipe["long_name"], recipe["short_name"], recipe["short_title"]) == ("dic", "dic", "DIC")

    def test_a_known_variable_keeps_its_names(self, tmp_path):
        recipe, _, _, _ = recipe_forms.check_save(
            "gridded", form(short_title="Mine"), str(tmp_path)
        )
        assert "short_title" not in recipe

    @pytest.mark.parametrize(
        "changes, box",
        [
            ({"source_info": ""}, "source_info"),
            ({"units": ""}, "units"),
            ({"where": ""}, "where"),
            ({"obs_path": ""}, "obs_path"),
            ({"obs_variable": ""}, "obs_variable"),
            ({"climatology": ""}, "climatology"),
            ({"obs_path": "/nowhere.nc"}, "obs_path"),
            ({"obs_variable": "nothing"}, "obs_variable"),
            ({"source": "ICES"}, "source"),
        ],
    )
    def test_a_form_that_cannot_be_saved(self, tmp_path, changes, box):
        recipe, errors, _, _ = recipe_forms.check_save("gridded", form(**changes), str(tmp_path))
        assert recipe is None
        assert box in errors

    def test_data_over_several_years_is_not_a_climatology(self, tmp_path):
        path = netcdf(tmp_path / "multi.nc", (2000, 2003))
        recipe, errors, _, _ = recipe_forms.check_save(
            "gridded", form(obs_path=path, obs_variable="chl", climatology="yes"), str(tmp_path)
        )
        assert recipe is None and "not a climatology" in errors["climatology"]
        recipe, errors, _, _ = recipe_forms.check_save(
            "gridded", form(obs_path=path, obs_variable="chl", climatology="no"), str(tmp_path)
        )
        assert errors == {}

    def test_data_through_the_water_column(self, tmp_path):
        path = netcdf(tmp_path / "deep.nc", levels=4)
        recipe, errors, _, _ = recipe_forms.check_save(
            "gridded", form(obs_path=path, obs_variable="chl", climatology="no"), str(tmp_path)
        )
        assert errors == {} and recipe["depth_resolved"] is True

    def test_a_point_form(self, tmp_path):
        recipe, errors, _, _ = recipe_forms.check_save(
            "point", form("point", variable="dic", short_title="DIC", obs_path=POINTS), str(tmp_path)
        )
        assert errors == {}
        assert recipe["obs_path"] == POINTS and recipe["depth_resolved"] is False
        assert recipe["kind"] == "point" and "location" not in recipe

    def test_a_remote_form_needs_its_data_checked_first(self, tmp_path):
        remote = form(location="thredds", obs_path="https://x.org/dodsC/a.nc")
        recipe, errors, _, _ = recipe_forms.check_save("gridded", remote, str(tmp_path))
        assert "Check the data first" in errors["obs_path"]
        signature = recipe_forms.signature("gridded", remote, str(tmp_path))
        found = inspect_gridded("disk", NITRATE)
        found["obs_path"] = ["https://x.org/dodsC/a.nc"]
        reply = {"signature": signature, "ok": True, "found": found}
        recipe, errors, _, _ = recipe_forms.check_save("gridded", remote, str(tmp_path), reply)
        assert errors == {}
        assert recipe["location"] == "thredds" and recipe["obs_path"] == ["https://x.org/dodsC/a.nc"]
        # a check of other addresses is not this one's
        other = dict(reply, signature=["thredds", ["https://x.org/dodsC/b.nc"]])
        assert "Check the data first" in recipe_forms.check_save("gridded", remote, str(tmp_path), other)[1]["obs_path"]
        failed = {"signature": signature, "ok": False, "error": "no server"}
        assert recipe_forms.check_save("gridded", remote, str(tmp_path), failed)[1]["obs_path"] == "no server"
