"""The compatibility check of one model file (oceanval.compat_check)

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
