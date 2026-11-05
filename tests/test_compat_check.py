"""Tools & checks: is my data compatible with OceanVal? (oceanval.compat_check)

The checks of one model file: CDO can open it, it can be regridded, a spatial
average can be worked out, its times are understood and it can be plotted.
"""

import time

import netCDF4
import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanval import compat_check
from oceanval.app import App
from oceanval.compat_check import FAILED, OK, SKIPPED, WARNING

TIMES = pd.date_range("2000-01-16", periods=3, freq="MS")


def regular(path, time_axis=True):
    lon, lat = np.arange(-10, 10) + 0.5, np.arange(40, 60) + 0.5
    dims = ("time", "lat", "lon") if time_axis else ("lat", "lon")
    shape = (3, 20, 20) if time_axis else (20, 20)
    coords = {
        "lon": ("lon", lon, {"units": "degrees_east", "standard_name": "longitude"}),
        "lat": ("lat", lat, {"units": "degrees_north", "standard_name": "latitude"}),
    }
    if time_axis:
        coords["time"] = TIMES
    xr.Dataset({"tos": (dims, np.random.rand(*shape), {"units": "degC"})}, coords=coords).to_netcdf(path)
    return str(path)


def generic(path):
    xr.Dataset({"tos": (("time", "y", "x"), np.random.rand(3, 20, 20))}, coords={"time": TIMES}).to_netcdf(path)
    return str(path)


def curvilinear(path):
    y, x = np.meshgrid(np.arange(20), np.arange(25), indexing="ij")
    xr.Dataset(
        {"tos": (("time", "y", "x"), np.random.rand(3, 20, 25), {"coordinates": "nav_lon nav_lat"})},
        coords={
            "time": TIMES,
            "nav_lon": (("y", "x"), -5 + x * 0.2 + 0.05 * y, {"units": "degrees_east", "standard_name": "longitude"}),
            "nav_lat": (("y", "x"), 50 + y * 0.15, {"units": "degrees_north", "standard_name": "latitude"}),
        },
    ).to_netcdf(path)
    return str(path)


def fvcom(path):
    n = 5
    xs, ys = np.meshgrid(np.arange(n) * 0.1 - 4, 50 + np.arange(n) * 0.1)
    triangles = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            triangles += [[a, a + 1, a + n], [a + 1, a + n + 1, a + n]]
    with netCDF4.Dataset(path, "w") as out:
        out.createDimension("node", n * n)
        out.createDimension("nele", len(triangles))
        out.createDimension("three", 3)
        out.createDimension("siglay", 2)
        out.createDimension("time", None)
        for name, values in (("lon", xs.ravel()), ("lat", ys.ravel()), ("x", xs.ravel() * 1e4), ("y", ys.ravel() * 1e4)):
            out.createVariable(name, "f8", ("node",))[:] = values
        out.createVariable("nv", "i4", ("three", "nele"))[:] = (np.array(triangles) + 1).T
        axis = out.createVariable("time", "f8", ("time",))
        axis.units = "days since 2000-01-01 00:00:00"
        axis[:] = [0, 1, 2]
        out.createVariable("temp", "f4", ("time", "siglay", "node"))[:] = np.random.rand(3, 2, n * n)
    return str(path)


def checked(path):
    """Every event run() tells for path, and the status of each row at the end."""
    events = []
    compat_check.run(str(path), events.append)
    rows = {}
    for event in events:
        if event["event"] == "row":
            rows[event["key"]] = (event["status"], event["message"])
    return events, rows


def checked_with(path, **options):
    events = []
    compat_check.run(str(path), events.append, **options)
    return events, {e["key"]: (e["status"], e["message"]) for e in events if e["event"] == "row"}


def statuses(rows):
    return {key: status for key, (status, _) in rows.items()}


class TestPieces:
    @pytest.mark.parametrize(
        "error, grids, expected",
        [
            ("Dataset contains generic grids. Please fix to lonlat or curvilinear", (), compat_check.GENERIC),
            ("cdo remapbil: whatever", ["generic"], compat_check.GENERIC),
            ("Unsupported grid type: unstructured", ["unstructured"], "does not support this type of grid"),
            ("Grid cell corner missing", [], "no cell bounds"),
            ("Cannot allocate memory", [], "ran out of memory"),
            ("line one\nthe last line\n", [], "the last line"),
        ],
    )
    def test_explain(self, error, grids, expected):
        assert expected in compat_check.explain(error, grids)

    def test_sinfo_is_read(self):
        output = (
            "   File format : NetCDF4\n"
            "    -1 : Institut Source   T Steptype Levels Num    Points Num Dtype : Parameter ID\n"
            "     1 : unknown  unknown  v instant       1   1       400   1  F64  : -1            \n"
            "     2 : unknown  unknown  v instant      10   2       400   1  F64  : -2            \n"
            "   Grid coordinates :\n"
            "     1 : lonlat                   : points=400 (20x20)\n"
            "                              lon : -9.5 to 9.5 by 1 [degrees_east]\n"
            "   Vertical coordinates :\n"
            "     1 : surface                  : levels=1\n"
            "   Time coordinate :\n"
            "                             time : 3 steps\n"
        )
        assert compat_check.read_sinfo(output) == {"variables": 2, "grids": [("lonlat", 400, "20x20")], "steps": 3}
        assert compat_check.read_sinfo("File format: NetCDF\n") == {"variables": 0, "grids": [], "steps": None}


def two_variables(path):
    data = xr.open_dataset(regular(path.with_name("one.nc")))
    data["chl"] = data["tos"] * 2
    data["chl"].attrs.update(long_name="chlorophyll", units="mg m-3")
    data.to_netcdf(path)
    return str(path)


SIX = [key for key, _ in compat_check.ROWS]


class TestListing:
    @pytest.fixture
    def folder(self, tmp_path):
        folder = tmp_path / "output"
        (folder / "sub").mkdir(parents=True)
        (folder / ".hidden").mkdir()
        for name in ("run_10.nc", "run_2.nc", "Run_3.nc", "notes.txt", "data.csv"):
            (folder / name).write_text("x")
        (folder / "link.nc").symlink_to(folder / "run_2.nc")
        (folder / "broken.nc").symlink_to(folder / "nothing.nc")
        return folder

    def names(self, found):
        return [(entry["name"], entry["type"]) for entry in found["entries"]]

    def test_nothing_typed_lists_nothing(self, tmp_path):
        assert compat_check.listing("", str(tmp_path))["kind"] == "empty"
        assert compat_check.listing("   ", str(tmp_path))["entries"] == []

    def test_a_directory_lists_its_folders_then_its_netcdf_files_in_natural_order(self, folder):
        for typed in (f"{folder}/", str(folder)):
            found = compat_check.listing(typed, "/")
            assert found["kind"] == "directory" and found["directory"] == str(folder)
            # hidden folders, other files and broken links are left out, links followed, 10 after 2
            assert self.names(found) == [
                ("sub", "folder"), ("link.nc", "file"), ("run_2.nc", "file"), ("Run_3.nc", "file"), ("run_10.nc", "file"),
            ]
            assert (found["folders"], found["nc_files"], found["more"], found["error"]) == (1, 4, 0, None)
        # what is chosen goes after the "/", added if it was not typed
        assert compat_check.listing(f"{folder}/", "/")["base"] == f"{folder}/"
        assert compat_check.listing(str(folder), "/")["base"] == f"{folder}/"

    def test_the_start_of_a_name_narrows_the_list_whatever_its_case(self, folder):
        found = compat_check.listing(f"{folder}/RUN_", "/")
        assert found["kind"] == "partial" and found["base"] == f"{folder}/"
        assert self.names(found) == [("run_2.nc", "file"), ("Run_3.nc", "file"), ("run_10.nc", "file")]
        # all of the directory is counted, whatever is typed
        assert (found["folders"], found["nc_files"]) == (1, 4)
        assert self.names(compat_check.listing(f"{folder}/s", "/")) == [("sub", "folder")]

    def test_a_file_is_a_file_with_its_size(self, folder):
        found = compat_check.listing(f"{folder}/run_2.nc", "/")
        assert found["kind"] == "file" and found["size"] == 1 and found["entries"] == []

    def test_what_is_not_there_is_missing(self, folder):
        for typed in (f"{folder}/zzz", f"{folder}/nodir/x", "/nowhere/at/all"):
            assert compat_check.listing(typed, "/")["kind"] == "missing"

    def test_a_relative_path_and_a_home_path_are_as_elsewhere_in_the_window(self, folder, monkeypatch):
        found = compat_check.listing("output/", str(folder.parent))
        assert found["kind"] == "directory" and found["base"] == "output/" and found["nc_files"] == 4
        monkeypatch.setenv("HOME", str(folder))
        found = compat_check.listing("~/su", "/")
        assert found["kind"] == "partial" and self.names(found) == [("sub", "folder")] and found["base"] == "~/"

    def test_a_long_list_is_cut_and_the_rest_counted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(compat_check, "MOST_ENTRIES", 5)
        for number in range(12):
            (tmp_path / f"f_{number}.nc").write_text("x")
        found = compat_check.listing(f"{tmp_path}/", "/")
        assert [entry["name"] for entry in found["entries"]] == [f"f_{n}.nc" for n in range(5)]
        assert found["more"] == 7 and found["nc_files"] == 12

    def test_a_directory_that_cannot_be_read_says_so(self, tmp_path, monkeypatch):
        def refuse(path):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(compat_check.os, "scandir", refuse)
        found = compat_check.listing(f"{tmp_path}/", "/")
        assert found["kind"] == "directory" and "do not have permission" in found["error"]


class TestRun:
    def test_a_regular_grid_passes_everything(self, tmp_path):
        events, rows = checked(regular(tmp_path / "regular.nc"))
        assert statuses(rows) == {key: OK for key in SIX}
        assert "3 time steps, 2000-02-01 to 2000-04-01, monthly" in rows["time"][1]
        assert "360x180" in rows["regrid"][1]
        # the interactive map is a Bokeh json_item, the climatology map a PNG, and the last event is done
        item = [event for event in events if event["event"] == "item"]
        assert len(item) == 1 and item[0]["item"]["doc"]
        image = [event for event in events if event["event"] == "image"]
        assert len(image) == 1 and image[0]["data"].startswith("data:image/png;base64,")
        assert events[-1] == {"event": "done"}
        # the only variable is used without asking
        assert {"event": "variable", "name": "tos"} in events
        assert [event["ask"] for event in events if event["event"] == "variables"] == [False]

    def test_every_row_is_told_working_before_it_is_answered(self, tmp_path):
        events, _ = checked(regular(tmp_path / "regular.nc"))
        told = [(event["key"], event["status"]) for event in events if event["event"] == "row"]
        for key in SIX:
            assert told.index((key, compat_check.WORKING)) < [i for i, (k, s) in enumerate(told) if k == key and s != "working"][0]

    def test_a_file_with_several_variables_asks_which(self, tmp_path):
        path = two_variables(tmp_path / "two.nc")
        asked = []

        def choose(variables):
            asked.append(variables)
            return "chl"

        events = []
        compat_check.run(path, events.append, choose=choose)
        assert len(asked) == 1 and list(asked[0]) == ["tos", "chl"]
        assert asked[0]["chl"] == {"long_name": "chlorophyll", "units": "mg m-3", "levels": 1}
        assert {"event": "variable", "name": "chl"} in events
        assert "chl regridded" in [e["message"] for e in events if e["event"] == "row" and e["key"] == "regrid" and e["status"] == OK][0]
        assert all(e["status"] == OK for e in events if e["event"] == "row" and e["status"] != "working")

    def test_a_variable_given_is_not_asked_about(self, tmp_path):
        path = two_variables(tmp_path / "two.nc")
        events = []
        compat_check.run(path, events.append, variable="tos", choose=lambda variables: pytest.fail("asked"))
        assert {"event": "variable", "name": "tos"} in events
        assert [event["ask"] for event in events if event["event"] == "variables"] == [False]

    def test_a_variable_the_file_does_not_have_is_refused(self, tmp_path):
        _, rows = checked_with(two_variables(tmp_path / "two.nc"), variable="nothing")
        assert rows["cdo"] == (FAILED, "The file has no variable nothing.")

    def test_a_generic_grid_cannot_be_regridded_and_says_so(self, tmp_path):
        events, rows = checked(generic(tmp_path / "generic.nc"))
        assert statuses(rows) == {
            "cdo": OK, "regrid": FAILED, "mean": FAILED, "time": OK, "interactive": WARNING, "climatology": FAILED,
        }
        assert "generic grid" in rows["regrid"][1] and "cannot be regridded" in rows["regrid"][1]
        assert "no cell areas" in rows["mean"][1]
        # the interactive map is drawn by grid index, and says so; Analyze's cannot be drawn
        assert "grid index" in rows["interactive"][1]
        assert "no longitudes and latitudes" in rows["climatology"][1]
        assert not [event for event in events if event["event"] == "image"]

    def test_a_curvilinear_grid_without_bounds_is_a_warning_for_the_mean(self, tmp_path):
        _, rows = checked(curvilinear(tmp_path / "curvilinear.nc"))
        status = statuses(rows)
        assert status["cdo"] == status["regrid"] == status["time"] == status["interactive"] == OK
        assert status["mean"] == WARNING and status["climatology"] in (OK, WARNING)
        assert "regrid" in rows["mean"][1] and "no cell" in rows["mean"][1]

    def test_a_file_with_no_time_axis_fails_the_time_check_only(self, tmp_path):
        _, rows = checked(regular(tmp_path / "static.nc", time_axis=False))
        assert rows["time"][0] == FAILED and "time axis" in rows["time"][1]
        assert {rows[key][0] for key in ("cdo", "regrid", "mean")} == {OK}

    def test_end_stamped_times_are_a_warning(self, tmp_path):
        path = tmp_path / "ends.nc"
        # a daily mean for January, dated at 00:00 on 1 February: a month of means with its last at the 1st of the next
        times = pd.date_range("2000-01-02", "2000-02-01", freq="D")
        data = xr.Dataset(
            {"tos": (("time", "lat", "lon"), np.random.rand(len(times), 4, 4), {"cell_methods": "time: mean"})},
            coords={
                "time": times,
                "lon": ("lon", np.arange(4.0), {"units": "degrees_east", "standard_name": "longitude"}),
                "lat": ("lat", np.arange(4.0) + 50, {"units": "degrees_north", "standard_name": "latitude"}),
            },
        )
        data.to_netcdf(path)
        _, rows = checked(path)
        assert rows["time"][0] == WARNING and "time_shift" in rows["time"][1]

    def test_a_missing_file_stops_at_the_first_row(self, tmp_path):
        _, rows = checked(tmp_path / "nothing.nc")
        assert rows["cdo"] == (FAILED, "There is no file at that path.")
        assert {status for key, status in statuses(rows).items() if key != "cdo"} == {SKIPPED}

    def test_a_file_that_is_not_netcdf_is_refused_by_cdo(self, tmp_path):
        path = tmp_path / "text.nc"
        path.write_text("not a netCDF file")
        _, rows = checked(path)
        assert rows["cdo"][0] == FAILED and "CDO could not read the file" in rows["cdo"][1]
        assert {status for key, status in statuses(rows).items() if key != "cdo"} == {SKIPPED}

    def test_fvcom_is_told_and_only_the_time_check_and_interactive_plot_are_made(self, tmp_path):
        events, rows = checked(fvcom(tmp_path / "fvcom.nc"))
        notice = [event for event in events if event["event"] == "fvcom"]
        assert len(notice) == 1 and "FVCOM" in notice[0]["notice"] and "should be compatible" in notice[0]["notice"]
        assert statuses(rows) == {
            "cdo": SKIPPED, "regrid": SKIPPED, "mean": SKIPPED, "time": OK, "interactive": OK, "climatology": SKIPPED,
        }
        assert "3 time steps" in rows["time"][1]
        assert "does not work with FVCOM" in rows["climatology"][1]
        assert any(event["event"] == "item" for event in events)

    @pytest.mark.cdo_check
    def test_a_segfault_of_cdo_is_said_and_stops_the_rest(self, tmp_path, fake_cdo):
        path = tmp_path / "bad.nc"
        path.write_text("x")
        _, rows = checked(path)
        assert rows["cdo"][0] == FAILED
        assert "segfaulted" in rows["cdo"][1] and "CF-compliant" in rows["cdo"][1]
        assert "https://anaconda.org/conda-forge/cdo" in rows["cdo"][1]
        assert {status for key, status in statuses(rows).items() if key != "cdo"} == {SKIPPED}


def finish(check, seconds=120):
    deadline = time.monotonic() + seconds
    while check.state()["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.1)
    return check.state()


class TestCompat:
    def test_the_state_follows_the_check(self, tmp_path):
        changes = []
        check = compat_check.Compat(regular(tmp_path / "regular.nc"), lambda: changes.append(1))
        start = check.state()
        assert start["status"] == "running" and [row["status"] for row in start["rows"]] == ["waiting"] * 6
        check.start()
        state = finish(check)
        assert state["status"] == "done" and state["path"].endswith("regular.nc")
        assert [row["key"] for row in state["rows"]] == [key for key, _ in compat_check.ROWS]
        assert [row["question"] for row in state["rows"]][0] == "Can CDO open my data?"
        assert {row["status"] for row in state["rows"]} == {OK}
        assert state["image"].startswith("data:image/png") and not state["fvcom"]
        assert state["map"] and check.item()["doc"] and state["variable"] == "tos"
        assert len(changes) >= 12

    def test_a_file_with_several_variables_waits_for_the_choice(self, tmp_path):
        check = compat_check.Compat(two_variables(tmp_path / "two.nc"), step_timeout=10).start()
        deadline = time.monotonic() + 60
        while check.state()["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.1)
        state = check.state()
        assert state["status"] == "choose" and list(state["variables"]) == ["tos", "chl"]
        assert state["variables"]["tos"]["units"] == "degC"
        assert {row["status"] for row in state["rows"]} == {"waiting"}
        # nothing times out while the user chooses, and only the file's variables are taken
        time.sleep(12)
        assert check.state()["status"] == "choose"
        assert not check.choose("nothing")
        assert check.choose("chl")
        state = finish(check)
        assert state["status"] == "done" and state["variable"] == "chl" and state["variables"] == {}
        assert {row["status"] for row in state["rows"]} == {OK}
        # it cannot be chosen again
        assert not check.choose("tos")

    def test_fvcom_state(self, tmp_path):
        state = finish(compat_check.Compat(fvcom(tmp_path / "fvcom.nc")).start())
        assert state["fvcom"] and "FVCOM" in state["notice"]

    def test_a_step_that_hangs_is_stopped(self, tmp_path, fake_cdo):
        path = tmp_path / "slow.nc"
        path.write_text("x")
        state = finish(compat_check.Compat(str(path), step_timeout=2).start(), 60)
        assert state["status"] == "done"
        assert state["rows"][0]["status"] == FAILED and "took more than 2 seconds" in state["rows"][0]["message"]
        assert {row["status"] for row in state["rows"][1:]} == {SKIPPED}

    def test_cancel_kills_the_process(self, tmp_path, fake_cdo):
        path = tmp_path / "slow.nc"
        path.write_text("x")
        check = compat_check.Compat(str(path)).start()
        deadline = time.monotonic() + 30
        while (check.process is None or check.state()["rows"][0]["status"] != "working") and time.monotonic() < deadline:
            time.sleep(0.1)
        process = check.process
        check.cancel()
        assert process.wait(10) is not None
        time.sleep(0.5)
        # nothing is told once it is cancelled
        assert check.state()["status"] == "running"


class TestApp:
    @pytest.fixture
    def app(self, tmp_path):
        app = App(cwd=str(tmp_path))
        app.leftovers_asked = True
        yield app
        app.close()

    def in_help(self, app):
        assert app.choose("help") and app.choose("help_compatible")
        assert app.view == "help_compatible"

    def test_there_is_nothing_to_check_until_a_file_is_given(self, app, tmp_path):
        self.in_help(app)
        assert app.state()["compat"] is None
        assert app.compat_start("")[1]["error"].startswith("Enter the path")
        assert app.compat_start(str(tmp_path))[1]["error"].startswith("That is a directory")
        assert app.compat_start("nothing.nc")[1]["error"] == "There is no file at that path."
        assert app.state()["compat"] is None

    def test_a_relative_path_is_from_the_directory_worked_in(self, app, tmp_path):
        regular(tmp_path / "here.nc")
        self.in_help(app)
        assert app.compat_start("here.nc") == (200, {"ok": True})
        state = app.state()["compat"]
        assert state["path"] == str(tmp_path / "here.nc")
        assert finish(app._compat)["status"] == "done"
        assert {row["status"] for row in app.state()["compat"]["rows"]} == {OK}

    def test_the_variable_is_chosen_through_the_app(self, app, tmp_path):
        two_variables(tmp_path / "two.nc")
        self.in_help(app)
        assert app.compat_variable("tos")[0] == 409
        assert app.compat_plot() == {"ok": False, "error": "There is no map."}
        app.compat_start("two.nc")
        deadline = time.monotonic() + 60
        while app.state()["compat"]["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.1)
        found = app.state()["compat"]
        assert found["status"] == "choose" and set(found["variables"]) == {"tos", "chl"}
        assert app.compat_variable("nothing")[1]["ok"] is False
        assert app.compat_variable("tos") == (200, {"ok": True})
        assert finish(app._compat)["status"] == "done"
        # the map has its own request, as it is large
        reply = app.compat_plot()
        assert reply["ok"] and reply["item"]["doc"]
        assert "item" not in app.state()["compat"]

    def test_the_path_box_is_listed_through_the_app(self, app, tmp_path):
        regular(tmp_path / "here.nc")
        (tmp_path / "sub").mkdir()
        found = app.compat_listing("")
        assert found["kind"] == "empty"
        found = app.compat_listing("h")
        assert found["kind"] == "partial" and [e["name"] for e in found["entries"]] == ["here.nc"]
        assert app.compat_listing("./")["kind"] == "directory"

    def test_the_check_is_for_the_help_page_only(self, app, tmp_path):
        regular(tmp_path / "here.nc")
        assert app.compat_start("here.nc")[0] == 409
        assert app._compat is None

    @pytest.mark.parametrize("how, view", [("clear", "help_compatible"), ("back", "help"), ("restart", "home")])
    def test_clear_back_and_restart_stop_the_check(self, app, tmp_path, fake_cdo, how, view):
        slow = tmp_path / "slow.nc"
        slow.write_text("x")
        self.in_help(app)
        app.compat_start(str(slow))
        check = app._compat
        assert check is not None
        {"clear": app.compat_clear, "back": lambda: app.back({}), "restart": app.reset}[how]()
        assert app._compat is None and check.cancelled
        assert app.state()["compat"] is None
        assert app.view == view

    def test_a_new_check_replaces_the_old(self, app, tmp_path, fake_cdo):
        slow = tmp_path / "slow.nc"
        slow.write_text("x")
        self.in_help(app)
        app.compat_start(str(slow))
        first = app._compat
        app.compat_start(str(slow))
        assert first.cancelled and app._compat is not first


# ---- the page ----


@pytest.fixture
def page_of(browser, tmp_path):
    app = App(cwd=str(tmp_path))
    app.leftovers_asked = True
    url = app.start()
    errors = []
    tab = browser.new_page(viewport={"width": 1100, "height": 900})
    tab.on("pageerror", lambda error: errors.append(str(error)))
    tab.goto(url)
    tab.wait_for_selector("#view-home:not([hidden])")
    tab.click("#home-help")
    tab.wait_for_selector("#view-help:not([hidden])")
    tab.click("#help-compatible")
    tab.wait_for_selector("#view-help-compatible:not([hidden])")
    try:
        yield tab, app
    finally:
        app.close()
    assert not errors


def marks(tab):
    return tab.eval_on_selector_all("#compat-rows tr", "rows => rows.map(row => row.dataset.status)")


def test_the_page_asks_the_questions_and_marks_each(page_of, tmp_path):
    tab, app = page_of
    path = curvilinear(tmp_path / "curvilinear.nc")
    assert tab.is_hidden("#compat-grid") and tab.is_visible("#compat-path")
    tab.fill("#compat-path", path)
    tab.click("#compat-start")
    tab.wait_for_selector("#compat-grid:not([hidden])")
    tab.wait_for_selector("#compat-summary:not([hidden])", timeout=120000)
    questions = tab.eval_on_selector_all("#compat-rows .matchup-grid__title", "items => items.map(i => i.textContent)")
    assert questions == [
        "Can CDO open my data?",
        "Can my data be regridded?",
        "Can a spatial average be calculated from my data?",
        "Is the time axis understood?",
        "Can my data be plotted interactively, as in View simulation output?",
        "Can my data be plotted as a climatology, as in Analyze simulations?",
    ]
    found = marks(tab)
    assert found[:4] == ["ok", "ok", "warning", "ok"] and found[4] == "ok" and found[5] in ("ok", "warning")
    # a tick, a warning, and a message under the warning
    assert tab.locator("#compat-rows tr:nth-child(1) td.is-state[data-state='done'] svg").count() == 1
    assert tab.locator("#compat-rows tr:nth-child(3) td.is-state[data-state='warning'] svg").count() == 1
    assert "no cell" in tab.inner_text("#compat-rows tr:nth-child(3) .compat-message")
    assert "with the warnings" in tab.inner_text("#compat-summary")
    assert "Checking the variable tos" in tab.inner_text("#compat-using")
    # the interactive map (Bokeh draws a canvas) and the climatology map
    tab.wait_for_selector("#compat-map-figure:not([hidden]) #compat-map canvas", timeout=60000)
    tab.wait_for_selector("#compat-figure:not([hidden])")
    assert tab.evaluate("document.getElementById('compat-image').naturalWidth") > 50
    # the button is back
    assert tab.inner_text("#compat-start") == "Check" and tab.is_enabled("#compat-start")


def test_a_file_with_several_variables_asks_which_on_the_page(page_of, tmp_path):
    tab, app = page_of
    tab.fill("#compat-path", two_variables(tmp_path / "two.nc"))
    tab.click("#compat-start")
    tab.wait_for_selector("#compat-choose:not([hidden])", timeout=60000)
    assert marks(tab) == ["waiting"] * 6 and tab.is_disabled("#compat-continue")
    # a variable is picked from the pop-out of the file's variables
    tab.click("#compat-variable")
    tab.wait_for_selector(".vars__popover:not([hidden])")
    assert "chlorophyll" in tab.inner_text(".vars__popover")
    tab.click(".vars__popover >> text=chl")
    assert tab.input_value("#compat-variable") == "chl" and tab.is_enabled("#compat-continue")
    tab.click("#compat-continue")
    tab.wait_for_selector("#compat-summary:not([hidden])", timeout=120000)
    assert tab.is_hidden("#compat-choose")
    assert marks(tab) == ["ok"] * 6
    assert "chl" in tab.inner_text("#compat-using")


def test_a_variable_that_is_not_the_files_cannot_be_continued_with(page_of, tmp_path):
    tab, app = page_of
    tab.fill("#compat-path", two_variables(tmp_path / "two.nc"))
    tab.click("#compat-start")
    tab.wait_for_selector("#compat-choose:not([hidden])", timeout=60000)
    tab.fill("#compat-variable", "nothing")
    assert tab.is_disabled("#compat-continue")


def test_the_circle_turns_while_a_question_is_being_answered(page_of, tmp_path, fake_cdo):
    tab, app = page_of
    slow = tmp_path / "slow.nc"
    slow.write_text("x")
    tab.fill("#compat-path", str(slow))
    tab.click("#compat-start")
    tab.wait_for_selector("#compat-rows tr[data-status='working'] .identify__spinner")
    assert marks(tab)[1:] == ["waiting"] * 5
    assert tab.locator("#compat-rows tr[data-status='waiting'] .matchup-grid__todo").count() == 5
    assert tab.inner_text("#compat-start") == "Checking…" and tab.is_disabled("#compat-start")


def test_a_generic_grid_is_said_to_be_unregriddable_on_the_page(page_of, tmp_path):
    tab, app = page_of
    tab.fill("#compat-path", generic(tmp_path / "generic.nc"))
    tab.press("#compat-path", "Enter")
    tab.wait_for_selector("#compat-summary:not([hidden])", timeout=120000)
    assert marks(tab) == ["ok", "failed", "failed", "ok", "warning", "failed"]
    assert "generic grid" in tab.inner_text("#compat-rows tr:nth-child(2) .compat-message")
    assert "cannot be regridded" in tab.inner_text("#compat-rows tr:nth-child(2) .compat-message")
    assert "failed" in tab.get_attribute("#compat-summary", "class")
    assert tab.is_hidden("#compat-figure")
    # the interactive map is still drawn, by grid index
    tab.wait_for_selector("#compat-map-figure:not([hidden]) #compat-map canvas", timeout=60000)


def test_fvcom_is_told_on_the_page(page_of, tmp_path):
    tab, app = page_of
    tab.fill("#compat-path", fvcom(tmp_path / "fvcom.nc"))
    tab.click("#compat-start")
    tab.wait_for_selector("#compat-summary:not([hidden])", timeout=120000)
    assert "FVCOM" in tab.inner_text("#compat-fvcom") and "should be compatible" in tab.inner_text("#compat-fvcom")
    assert marks(tab) == ["skipped", "skipped", "skipped", "ok", "ok", "skipped"]


def test_a_missing_file_is_said_under_the_box_and_editing_clears_a_result(page_of, tmp_path):
    tab, app = page_of
    tab.fill("#compat-path", str(tmp_path / "nothing.nc"))
    tab.click("#compat-start")
    tab.wait_for_selector("#compat-error:not([hidden])")
    assert tab.inner_text("#compat-error") == "There is no file at that path."
    assert tab.is_hidden("#compat-grid")
    tab.fill("#compat-path", regular(tmp_path / "regular.nc"))
    assert tab.is_hidden("#compat-error")
    tab.click("#compat-start")
    tab.wait_for_selector("#compat-summary:not([hidden])", timeout=120000)
    assert "Every check passed" in tab.inner_text("#compat-summary")
    # a new path is a new question
    tab.press("#compat-path", "End")
    tab.type("#compat-path", "x")
    tab.wait_for_selector("#compat-grid", state="hidden")
    assert app._compat is None


def test_back_leaves_the_check(page_of, tmp_path):
    tab, app = page_of
    tab.fill("#compat-path", regular(tmp_path / "regular.nc"))
    tab.click("#compat-start")
    tab.wait_for_selector("#compat-summary:not([hidden])", timeout=120000)
    tab.get_by_role("button", name="Back").click()
    tab.wait_for_selector("#view-help:not([hidden])")
    assert app._compat is None


def test_the_path_box_lists_what_can_be_chosen(page_of, tmp_path):
    tab, app = page_of
    folder = tmp_path / "output"
    (folder / "sub").mkdir(parents=True)
    for name in ("run_10.nc", "run_2.nc", "other.nc"):
        regular(folder / name)
    (folder / "notes.txt").write_text("x")
    assert tab.is_hidden("#compat-options")
    # a directory: its folders and files, and a note of how many
    tab.fill("#compat-path", f"{folder}/")
    tab.wait_for_selector("#compat-options:not([hidden])")
    shown = tab.eval_on_selector_all("#compat-options .combo__option", "items => items.map(i => i.textContent)")
    assert shown == ["sub/", "other.nc", "run_2.nc", "run_10.nc"]
    assert "3 netCDF files and 1 folder in here" in tab.inner_text("#compat-path-note")
    # nothing is checked until there is a file
    assert tab.is_disabled("#compat-start")
    tab.press("#compat-path", "Enter")
    assert app._compat is None
    # typing narrows it
    tab.type("#compat-path", "run")
    tab.wait_for_function("document.querySelectorAll('#compat-options .combo__option').length === 2")
    assert "2 names start like that" in tab.inner_text("#compat-path-note")
    # a folder is gone into
    tab.fill("#compat-path", f"{folder}/s")
    tab.wait_for_function("document.querySelectorAll('#compat-options .combo__option').length === 1")
    tab.click("#compat-options .combo__option")
    assert tab.input_value("#compat-path") == f"{folder}/sub/"
    tab.wait_for_function("document.getElementById('compat-path-note').textContent.includes('There are no netCDF files here')")
    # a file is what is checked
    tab.fill("#compat-path", f"{folder}/run_")
    tab.wait_for_function("document.querySelectorAll('#compat-options .combo__option').length === 2")
    tab.click("#compat-options .combo__option >> text=run_2.nc")
    assert tab.input_value("#compat-path") == f"{folder}/run_2.nc"
    tab.wait_for_selector("#compat-options", state="hidden")
    tab.wait_for_function("document.getElementById('compat-path-note').textContent.startsWith('A netCDF file')")
    assert tab.is_enabled("#compat-start")


def test_the_path_box_can_be_browsed(page_of, tmp_path):
    tab, app = page_of
    folder = tmp_path / "output"
    (folder / "sub").mkdir(parents=True)
    regular(folder / "run_2.nc")
    tab.fill("#compat-path", f"{folder}/")
    tab.click("[data-browse=compat-path]")
    tab.wait_for_selector("#picker:not([hidden])")
    # a file is chosen by clicking it; there is no choosing of a directory
    assert tab.is_hidden("#picker-choose")
    tab.click("#picker-list .picker__row >> text=sub")
    tab.wait_for_selector("#picker-list .picker__empty")
    tab.click("#picker-crumbs .crumb >> text=output")
    tab.click("#picker-list .picker__row >> text=run_2.nc")
    tab.wait_for_selector("#picker", state="hidden")
    assert tab.input_value("#compat-path").endswith("run_2.nc")
    tab.wait_for_function("document.getElementById('compat-path-note').textContent.startsWith('A netCDF file')")
    assert tab.is_enabled("#compat-start")


def test_the_path_box_is_used_from_the_keyboard(page_of, tmp_path):
    tab, app = page_of
    folder = tmp_path / "output"
    (folder / "sub").mkdir(parents=True)
    regular(folder / "a.nc")
    tab.fill("#compat-path", f"{folder}/")
    tab.wait_for_selector("#compat-options:not([hidden])")
    # Down twice: sub/, then a.nc; Enter takes it and does not start the check
    tab.press("#compat-path", "ArrowDown")
    tab.press("#compat-path", "ArrowDown")
    tab.press("#compat-path", "Enter")
    assert tab.input_value("#compat-path") == f"{folder}/a.nc"
    tab.wait_for_function("document.getElementById('compat-path-note').textContent.startsWith('A netCDF file')")
    assert app._compat is None
    # Enter with nothing marked checks the file, and Escape shuts the list
    tab.press("#compat-path", "Escape")
    tab.press("#compat-path", "Enter")
    tab.wait_for_selector("#compat-grid:not([hidden])")


def test_a_missing_path_is_said_and_cannot_be_checked(page_of, tmp_path):
    tab, app = page_of
    tab.fill("#compat-path", str(tmp_path / "nothing.nc"))
    tab.wait_for_function("document.getElementById('compat-path-note').textContent.includes('no file or directory')")
    assert "is-error" in tab.get_attribute("#compat-path-note", "class")
    assert tab.is_disabled("#compat-start") and tab.is_hidden("#compat-options")
