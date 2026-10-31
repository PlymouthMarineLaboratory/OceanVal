"""The oceanval window's Compare simulations steps (see oceanval.sim_compare_app):
the simulations, the variables in each, their files, the grids, and the options
step Analyze shares, which ends in the run."""

import ast
import os
import threading
import time

import numpy as np
import pytest

from oceanval import analysis, sim_compare, sim_compare_forms, sim_unify
from oceanval.app import App
from simulations import write_analysis_simulation, write_folders
from test_app import get, get_json, leftover_dir, post, wait_for  # noqa: F401 (leftover_dir is autouse)

B_LATS = list(np.arange(49.5, 53.6, 0.5))
B_LONS = list(np.arange(-4.5, 0.6, 0.5))


@pytest.fixture
def app(tmp_path):
    app = App(cwd=str(tmp_path))
    app.start()
    yield app
    app.close()


@pytest.fixture
def runs(app, monkeypatch):
    """The runs the app starts, which are recorded rather than started."""
    started = []
    monkeypatch.setattr(app, "start_run", lambda args, label: started.append((args, label)))
    return started


@pytest.fixture
def sims(tmp_path):
    write_analysis_simulation(str(tmp_path / "base"), years=(2013, 2014), daily=False)
    write_analysis_simulation(
        str(tmp_path / "fine"), years=(2013, 2014), daily=False, trend=1.0, lats=B_LATS, lons=B_LONS
    )
    return "base", "fine"


def sims_form(**changes):
    rows = [
        {"name": "Base run", "simdir": "base", "ndown": "1", "exclude": "", "require": ""},
        {"name": "Fine run", "simdir": "fine", "ndown": "1", "exclude": "", "require": ""},
    ]
    form = {"rows": rows}
    form.update(changes)
    return form


# the last page's boxes, which the run takes
OUTPUT = {"form": {"out_dir": "", "out": "comparison.py"}}


def state(app):
    return get_json(app, "/api/state")[1]


def to_sims(app):
    assert post(app, "/api/choose", {"action": "simcompare"}) == (200, {"ok": True})
    return state(app)["simcompare"]


def to_variables(app):
    to_sims(app)
    assert post(app, "/api/simcompare_sims", {"form": sims_form()}) == (200, {"ok": True})
    wait_for(lambda: app._sc_all_read())
    return state(app)["simcompare"]


ROWS = [
    {"name": "Chlorophyll", "models": {"Base run": "P1_Chl+P2_Chl", "Fine run": "P1_Chl+P2_Chl"}},
    {"name": "Temperature", "models": {"Base run": "thetao", "Fine run": "thetao"}},
]


def to_files(app, rows=ROWS):
    to_variables(app)
    assert post(app, "/api/simcompare_variables", {"rows": rows}) == (200, {"ok": True})
    wait_for(lambda: app._sc_checks_done())
    return state(app)["simcompare"]


def to_grids(app):
    to_files(app)
    assert post(app, "/api/simcompare_files") == (200, {"ok": True})
    wait_for(lambda: (app.sc_grids or {}).get("status") == "done")
    return state(app)["simcompare"]


def to_options(app, unify=None):
    to_grids(app)
    form = {"need": "no"} if unify is None else unify
    assert post(app, "/api/simcompare_grids", {"form": form}) == (200, {"ok": True})
    return state(app)["analyze"]


class TestLanding:
    def test_there_is_a_third_tile(self, app):
        page = get(app, "/")[1]
        assert 'data-action="simcompare"' in page and 'data-action="analyze"' in page
        assert 'class="choices is-four"' in page
        assert state(app)["view"] == "home"

    def test_the_tile_goes_to_the_simulations_and_back(self, app):
        shown = to_sims(app)
        assert app.view == "simcompare_sims" and app.action == "simcompare"
        assert len(shown["form"]["rows"]) == 2
        assert shown["colours"] == sim_compare.COLOURS and shown["most"] == 5
        assert post(app, "/api/back", {"form": sims_form()})[0] == 200
        assert app.view == "home"
        # what was entered is shown again
        assert state(app)["simcompare"]["form"]["rows"][0]["name"] == "Base run"

    def test_the_other_tiles_start_afresh(self, app, sims):
        to_options(app)
        app.view, app.action = "finished", "simcompare"
        assert post(app, "/api/restart_all")[0] == 200
        assert post(app, "/api/choose", {"action": "analyze"})[0] == 200
        # the options of the comparison are not an analysis's
        assert app.analyze_placed is None and app.analyze_found is None


class TestSimulations:
    def test_each_row_is_checked(self, app, sims):
        to_sims(app)
        form = sims_form()
        form["rows"][0]["name"] = ""
        form["rows"][1]["simdir"] = "nowhere"
        status, reply = post(app, "/api/simcompare_sims", {"form": form})
        assert status == 400
        assert reply["errors"]["rows"]["0"] == {"name": "Give this simulation a name."}
        assert set(reply["errors"]["rows"]["1"]) == {"simdir"}

    def test_two_are_needed_and_names_must_differ(self, app, sims):
        to_sims(app)
        form = sims_form()
        form["rows"][1] = {"name": "", "simdir": "", "ndown": "", "exclude": "", "require": ""}
        status, reply = post(app, "/api/simcompare_sims", {"form": form})
        assert status == 400 and "at least two" in reply["errors"]["simulations"]
        form = sims_form()
        form["rows"][1]["name"] = "base-run"
        status, reply = post(app, "/api/simcompare_sims", {"form": form})
        assert status == 400 and "same name" in reply["errors"]["rows"]["1"]["name"]

    def test_the_files_skipped_start_empty_and_a_row_of_nothing_is_unused(self, app, sims, tmp_path):
        assert sim_compare_forms.blank_sim()["exclude"] == ""
        assert "default_exclude" not in state(app)
        # the rows as they start are no simulations, and nothing is wrong with them
        assert sim_compare_forms.check_sims(sim_compare_forms.default_sims_form(str(tmp_path)), str(tmp_path)) == (
            None, {"simulations": "Give at least two simulations to compare."})
        to_sims(app)
        # a third row left as it starts is not a simulation to name
        form = sims_form()
        form["rows"].append(sim_compare_forms.blank_sim())
        assert post(app, "/api/simcompare_sims", {"form": form}) == (200, {"ok": True})
        # but anything else in it is
        form["rows"][2]["name"] = "Third"
        wait_for(lambda: app._sc_all_read())
        sims_found, errors = sim_compare_forms.check_sims(sim_compare_forms.clean_sims_form(form, str(tmp_path)), str(tmp_path))
        assert errors["rows"]["2"]["simdir"]

    def test_the_simulations_are_read(self, app, sims):
        shown = to_variables(app)
        assert app.view == "simcompare_variables"
        assert [sim["name"] for sim in shown["sims"]] == ["Base run", "Fine run"]
        assert [sim["colour"] for sim in shown["sims"]] == sim_compare.COLOURS[:2]
        for sim in shown["sims"]:
            assert sim["status"] == "done" and sim["streams"] == 2
            assert sim["picker"]["available"] == ["P1_Chl", "P2_Chl", "e3t", "thetao"]
            presets = {preset["name"]: preset["model_variable"] for preset in sim["presets"]}
            assert presets["Temperature"] == "thetao"
        # nothing is filled in for the user
        assert shown["rows"] == []

    def test_the_files_are_read_one_thread_at_a_time(self, tmp_path, sims, monkeypatch):
        # netCDF4/HDF5 is not thread-safe, and each simulation is read in a thread of its own
        import threading
        import time

        from oceanval import analysis

        reading = []
        overlapped = []
        real = analysis._read_variables_of

        def watched(path):
            reading.append(path)
            if len(reading) > 1:
                overlapped.append(list(reading))
            time.sleep(0.05)
            try:
                return real(path)
            finally:
                reading.remove(path)

        monkeypatch.setattr(analysis, "_read_variables_of", watched)
        found = {}

        def read(name):
            found[name] = analysis.streams(str(tmp_path / name), 1)

        threads = [threading.Thread(target=read, args=(name,)) for name in sims]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert overlapped == []
        for name in sims:
            assert len(found[name]) == 2
            variables = {key: value["units"] for stream in found[name] for key, value in stream["variables"].items()}
            assert variables["P1_Chl"] == "mg Chl m-3" and variables["thetao"] == "degC"

    def test_the_fvcom_check_is_made_with_the_files_read_lock_held(self, app, tmp_path, sims, monkeypatch):
        # it opens a file with xarray, which must not overlap another thread's read
        from oceanval import analysis

        held = []

        def looks(path):
            held.append(analysis._FILE_READ_LOCK.locked())
            return False

        monkeypatch.setattr(analysis, "_looks_like_fvcom", looks)
        to_variables(app)
        assert len(held) == 2 and all(held)

    def test_a_simulation_that_cannot_be_read(self, app, tmp_path, sims):
        os.makedirs(tmp_path / "empty" / "2013")
        to_sims(app)
        form = sims_form()
        form["rows"][1]["simdir"] = "empty"
        assert post(app, "/api/simcompare_sims", {"form": form}) == (200, {"ok": True})
        wait_for(lambda: app.sc_found["Fine run"]["status"] != "reading")
        shown = state(app)["simcompare"]["sims"]
        assert shown[1]["status"] == "failed" and "No netCDF files" in shown[1]["error"]
        assert post(app, "/api/simcompare_variables", {"rows": ROWS})[0] == 409

    def test_the_directories_are_looked_at_as_they_are_typed(self, app, sims):
        found = get_json(app, "/api/probe", simdir="base", ndown="1", out="", out_dir="")[1]
        assert found["files"] == 4 and found["out_dir_has_comparison"] is False
        os.makedirs(os.path.join(app.cwd, sim_compare.FOLDER))
        assert get_json(app, "/api/probe", simdir="base", ndown="1", out="", out_dir="")[1]["out_dir_has_comparison"]


class TestVariables:
    def test_the_rows_are_checked_in_each_simulation(self, app, sims):
        to_variables(app)
        rows = [
            {"name": "Temperature", "models": {"Base run": "thetao", "Fine run": "votemper"}},
            {"name": "", "models": {"Base run": "thetao", "Fine run": ""}},
        ]
        status, reply = post(app, "/api/simcompare_variables", {"rows": rows})
        assert status == 400
        assert "votemper" in reply["errors"]["0"]["models"]["Fine run"]
        assert reply["errors"]["1"]["name"] == "Give this variable a name."
        assert "Fine run" in reply["errors"]["1"]["models"]
        assert post(app, "/api/simcompare_variables", {"rows": []})[1]["errors"] == {"rows": "Add a variable to compare."}

    def test_the_variables_are_placed_in_every_simulation(self, app, sims):
        shown = to_files(app)
        assert app.view == "simcompare_files"
        assert [row["name"] for row in shown["placed"]] == ["Chlorophyll", "Temperature"]
        assert shown["placed"][1]["per_sim"]["Fine run"]["model_variable"] == "thetao"
        assert shown["shift"]["status"] == "none"
        assert all(sim["check"]["status"] == "done" for sim in shown["sims"])
        # the combined variables are what the options step is for
        assert [row["name"] for row in app.analyze_placed] == ["Chlorophyll", "Temperature"]

    def test_back_keeps_the_rows(self, app, sims):
        to_variables(app)
        assert post(app, "/api/back", {"rows": ROWS})[0] == 200
        assert app.view == "simcompare_sims"
        assert state(app)["simcompare"]["rows"][0]["models"] == ROWS[0]["models"]


def in_kelvin(folder):
    """The temperature of the simulation in folder given in K, as it is in degC."""
    import glob

    import xarray as xr

    for path in glob.glob(os.path.join(folder, "*", "*grid_T.nc")):
        with xr.open_dataset(path) as ds:
            ds = ds.load()
        ds["thetao"] = (ds["thetao"] + 273.15).assign_attrs(ds["thetao"].attrs, units="K")
        # replaced, not written over: HDF5 refuses to open for writing a file this process still holds open
        # (the window's reads can leave one), which comes out as a PermissionError
        ds.to_netcdf(path + ".tmp")
        os.replace(path + ".tmp", path)


# the boxes of the units step for the one row of TestUnits: OceanVal's suggestion
SUGGESTED = {"1:1": {"multiplier": "", "adder": "-273.15"}}


class TestUnits:
    """A simulation in other units than the first's is converted into them, as validate's units step converts the
    observations into the model's: a page for each simulation that needs one, which must be confirmed."""

    @pytest.fixture
    def kelvin(self, app, sims, tmp_path):
        in_kelvin(str(tmp_path / "fine"))
        to_variables(app)
        assert post(app, "/api/simcompare_variables", {"rows": ROWS}) == (200, {"ok": True})
        return state(app)["simcompare"]

    def test_units_not_the_firsts_are_asked_about(self, app, kelvin):
        assert app.view == "simcompare_units"
        assert kelvin["units"]["boxes"] == {}
        # chlorophyll is in the same units in both: only temperature has a row
        [row] = kelvin["units"]["rows"]
        assert row["key"] == "1:1"
        assert row["simulation"] == "Fine run" and row["variable"] == "Temperature"
        assert row["model_variable"] == "thetao" and row["units"] == "K"
        assert row["baseline"] == {"simulation": "Base run", "model_variable": "thetao", "units": "degC"}
        assert row["check"]["status"] == "convert"
        assert (row["check"]["multiplier"], row["check"]["adder"]) == (1, -273.15)

    def test_the_units_must_be_confirmed(self, app, kelvin):
        status, reply = post(app, "/api/simcompare_units", {"conversions": SUGGESTED})
        assert status == 400 and reply["error"] == "Confirm the units before carrying on."
        assert app.view == "simcompare_units"

    @pytest.mark.parametrize(
        "boxes, said",
        [
            ({"multiplier": "abc", "adder": ""}, "Multiply by a number that is not 0"),
            ({"multiplier": "0", "adder": ""}, "Multiply by a number that is not 0"),
            ({"multiplier": "-1", "adder": ""}, "more than 0"),
        ],
    )
    def test_boxes_that_cannot_be_used_are_said(self, app, kelvin, boxes, said):
        status, reply = post(app, "/api/simcompare_units", {"conversions": {"1:1": boxes}, "confirmed": True})
        assert status == 400 and said in reply["errors"]["1:1"]
        # kept for the page as they were
        assert state(app)["simcompare"]["units"]["boxes"]["1:1"] == boxes

    def test_continue_goes_on_to_the_files_with_the_conversions(self, app, kelvin):
        assert post(app, "/api/simcompare_units", {"conversions": SUGGESTED, "confirmed": True}) == (200, {"ok": True})
        assert app.view == "simcompare_files"
        assert app.sc_conversions == {"Temperature": {"Fine run": {"multiplier": 1, "adder": -273.15}}}
        # the checks of the files went on while the units were asked about
        wait_for(lambda: app._sc_checks_done())

    def test_blank_boxes_say_the_units_are_the_firsts(self, app, kelvin):
        blank = {"1:1": {"multiplier": "", "adder": ""}}
        assert post(app, "/api/simcompare_units", {"conversions": blank, "confirmed": True}) == (200, {"ok": True})
        assert app.sc_conversions == {"Temperature": {"Fine run": {"multiplier": 1, "adder": 0}}}

    def test_back_keeps_the_boxes(self, app, kelvin):
        typed = {"1:1": {"multiplier": "1", "adder": "-273"}}
        assert post(app, "/api/simcompare_units", {"conversions": typed, "confirmed": True})[0] == 200
        # from the files to the units, and from the units to the variables
        assert post(app, "/api/back", {})[0] == 200
        assert app.view == "simcompare_units"
        assert state(app)["simcompare"]["units"]["boxes"] == typed
        assert post(app, "/api/back", {"conversions": SUGGESTED})[0] == 200
        assert app.view == "simcompare_variables"
        assert post(app, "/api/simcompare_variables", {"rows": ROWS}) == (200, {"ok": True})
        assert state(app)["simcompare"]["units"]["boxes"] == SUGGESTED

    def test_no_step_once_the_units_are_the_same(self, app, kelvin, tmp_path):
        assert post(app, "/api/back", {"conversions": {"1:1": {"multiplier": "", "adder": "5"}}})[0] == 200
        # both simulations in K, read afresh (as other simulations): nothing to convert
        in_kelvin(str(tmp_path / "base"))
        assert post(app, "/api/back", {})[0] == 200
        assert post(app, "/api/simcompare_sims", {"form": sims_form(rows=[
            {"name": "Base run", "simdir": "fine", "ndown": "1", "exclude": "", "require": ""},
            {"name": "Fine run", "simdir": "base", "ndown": "1", "exclude": "", "require": ""},
        ])}) == (200, {"ok": True})
        wait_for(lambda: app._sc_all_read())
        assert post(app, "/api/simcompare_variables", {"rows": ROWS}) == (200, {"ok": True})
        assert app.view == "simcompare_files" and app.sc_conversions == {}

    def test_the_script_converts_them(self, app, kelvin, runs):
        assert post(app, "/api/simcompare_units", {"conversions": SUGGESTED, "confirmed": True})[0] == 200
        wait_for(lambda: app._sc_checks_done())
        assert post(app, "/api/simcompare_files") == (200, {"ok": True})
        assert post(app, "/api/simcompare_grids", {"form": {"need": "no"}}) == (200, {"ok": True})
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY}) == (200, {"ok": True})
        assert post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "2"}})[0] == 200
        assert post(app, "/api/analyze_option", {"kind": "figure_code", "form": {"choice": "no"}})[0] == 200
        assert post(app, "/api/analyze_save_script", OUTPUT) == (200, {"ok": True})
        call = ast.parse(open(os.path.join(app.cwd, "comparison.py")).read()).body[-1].value
        given = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
        assert given["unit_conversions"] == {"Temperature": {"Fine run": {"multiplier": 1, "adder": -273.15}}}
        sim_compare.check_unit_conversions(given["unit_conversions"], given["variables"], ["Base run", "Fine run"])

    def test_the_same_units_have_no_step(self, app, sims, runs):
        to_files(app)
        assert app.view == "simcompare_files" and app.sc_unit_rows == [] and app.sc_conversions == {}
        assert post(app, "/api/back", {})[0] == 200
        assert app.view == "simcompare_variables"


class TestFiles:
    def test_the_dates_of_every_simulation_are_read(self, app, sims):
        shown = to_files(app)
        for sim in shown["sims"]:
            rows = {row["variable"]: row for row in sim["check"]["rows"]}
            assert rows["Temperature"]["groups"][0]["dates"]["first"].startswith("2013")
        status, reply = get_json(app, "/api/simcompare_check_files", simulation="Fine run", variable="Temperature")
        assert status == 200 and len(reply["files"]) == 2

    def test_the_years_asked_for_are_those_every_simulation_has(self, app, sims, tmp_path):
        import glob
        import shutil

        # the fine run has only 2014
        for path in glob.glob(str(tmp_path / "fine" / "2013")):
            shutil.rmtree(path)
        to_files(app)
        assert app._sc_check_rows()["Temperature"]["groups"][0]["dates"] == {"first": "2014-01-01", "last": "2014-12-31"}

    def test_no_years_in_common_stops_the_step(self, app, sims, tmp_path):
        import shutil

        shutil.rmtree(tmp_path / "fine" / "2013")
        shutil.rmtree(tmp_path / "base" / "2014")
        to_files(app)
        status, reply = post(app, "/api/simcompare_files")
        assert status == 400 and "no years in common" in reply["error"]

    def test_times_that_look_like_ends_are_asked_about(self, app, sims, monkeypatch):
        from oceanval import time_stamps

        row = {
            "key": "Temperature", "variable": "Temperature", "title": "Temperature", "model_variable": "thetao",
            "pattern": "x", "first": "2013-02-01", "last": "2014-01-01", "first_shifted": "2013-01-01",
            "last_shifted": "2013-12-01", "has_bounds": False, "reason": "dated at the end", "cell_methods": "time: mean",
        }

        def check(chosen, files, titles=None):
            return [dict(row, pattern=one.pattern) for one in chosen.itertuples() if one.variable == "Temperature"]

        monkeypatch.setattr(time_stamps, "check_rows", check)
        to_variables(app)
        assert post(app, "/api/simcompare_variables", {"rows": ROWS})[0] == 200
        wait_for(lambda: (app.sc_shift or {}).get("status") == "asked")
        shown = state(app)["simcompare"]
        assert {r["simulation"] for r in shown["shift"]["rows"]} == {"Base run", "Fine run"}
        # nothing is read until it is answered
        assert post(app, "/api/simcompare_files")[0] == 409
        assert post(app, "/api/simcompare_time_shift", {"choice": "maybe"})[0] == 409
        assert post(app, "/api/simcompare_time_shift", {"choice": "shift"})[0] == 200
        wait_for(lambda: app._sc_checks_done())
        assert app._sc_shifts() == {"Base run": time_stamps.SUGGESTED, "Fine run": time_stamps.SUGGESTED}
        assert post(app, "/api/simcompare_files") == (200, {"ok": True})


def opened(monkeypatch):
    """The files xarray opens from now on, with whether the netCDF lock was held."""
    import xarray as xr

    from oceanval import netcdf_lock

    seen = []
    real = xr.open_dataset

    def watched(path, *args, **kwargs):
        seen.append((str(path), netcdf_lock.LOCK.locked()))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(xr, "open_dataset", watched)
    return seen


class TestGrids:
    def test_the_simulations_grids_are_looked_at(self, app, sims):
        shown = to_grids(app)
        assert app.view == "simcompare_grids"
        assert shown["grids"]["same"] is False
        assert set(shown["grids"]["extents"]) == {"Base run", "Fine run"}
        assert shown["grids"]["extents"]["Fine run"]["lat"] == [49.5, 53.5]
        # nothing is chosen for the user
        assert shown["unify_form"]["need"] == "" and shown["unify_form"]["method"] == ""

    def test_one_simulation_twice_is_on_the_same_grid(self, app, sims):
        to_sims(app)
        form = sims_form()
        form["rows"][1]["simdir"] = "base"
        assert post(app, "/api/simcompare_sims", {"form": form}) == (200, {"ok": True})
        wait_for(lambda: app._sc_all_read())
        assert post(app, "/api/simcompare_variables", {"rows": ROWS}) == (200, {"ok": True})
        wait_for(lambda: app._sc_checks_done())
        assert post(app, "/api/simcompare_files") == (200, {"ok": True})
        wait_for(lambda: (app.sc_grids or {}).get("status") == "done")
        grids = state(app)["simcompare"]["grids"]
        assert grids["same"] is True
        assert grids["extents"]["Base run"] == grids["extents"]["Fine run"]

    def test_the_model_file_is_cut_by_cdo_before_xarray_opens_it(self, tmp_path, sims, monkeypatch):
        # a model file can be many GB: the window loaded all of one, and was killed
        import glob

        import xarray as xr

        from oceanval import sim_unify

        path = sorted(glob.glob(str(tmp_path / "fine" / "**" / "*_grid_T.nc"), recursive=True))[0]
        with xr.open_dataset(path, decode_times=False) as data:
            lon, lat = sim_unify._lonlat(data)
        seen = opened(monkeypatch)
        found = sim_compare.grid_of(path, "thetao")
        assert np.allclose(found[0], lon) and np.allclose(found[1], lat)
        # with no variable named, every variable's first level and step
        assert np.allclose(sim_compare.grid_of(path)[1], lat)
        assert seen and all(not opened_path.startswith(str(tmp_path)) for opened_path, _ in seen), seen
        assert sim_compare.grid_of(str(tmp_path / "nothing.nc"), "thetao") is None

    def test_the_window_reads_files_with_the_netcdf_lock_held(self, app, tmp_path, sims, monkeypatch):
        # netCDF-C/HDF5 is not thread-safe: two of the window's threads opening files at once
        # (the grids and the missing value's probe) crashed it with a segmentation fault
        from oceanval import analysis_app

        seen = opened(monkeypatch)
        probed = []
        real = analysis_app.missing_probe.probe
        monkeypatch.setattr(
            analysis_app.missing_probe, "probe", lambda *args, **kwargs: probed.append(real(*args, **kwargs))
        )
        to_options(app)
        wait_for(lambda: probed)
        assert any(opened_path.startswith(str(tmp_path)) for opened_path, _ in seen)
        assert all(held for _, held in seen), [opened_path for opened_path, held in seen if not held]

    @pytest.mark.parametrize(
        "form, boxes",
        [
            ({}, {"need"}),
            ({"need": "yes"}, {"method"}),
            ({"need": "yes", "method": "baseline"}, {"baseline"}),
            ({"need": "yes", "method": "baseline", "baseline": "Nobody"}, {"baseline"}),
            ({"need": "yes", "method": "box"}, {"lon_min", "lon_max", "lat_min", "lat_max", "res"}),
            ({"need": "yes", "method": "box", "lon_min": "x", "lon_max": "0", "lat_min": "50", "lat_max": "53", "res": "1"}, {"lon_min"}),
            ({"need": "yes", "method": "box", "lon_min": "0", "lon_max": "-4", "lat_min": "50", "lat_max": "53", "res": "1"}, {"lon_max"}),
        ],
    )
    def test_the_boxes_are_checked(self, app, sims, form, boxes):
        to_grids(app)
        status, reply = post(app, "/api/simcompare_grids", {"form": form})
        assert status == 400 and set(reply["errors"]) == boxes

    def test_no_is_an_answer(self, app, sims):
        to_options(app)
        assert app.view == "analyze_options" and app.sc_unify is None

    def test_a_baseline(self, app, sims):
        to_options(app, {"need": "yes", "method": "baseline", "baseline": "Fine run"})
        assert app.sc_unify == {"baseline": "Fine run"}

    def test_a_box(self, app, sims):
        box = {"need": "yes", "method": "box", "lon_min": "-4", "lon_max": "0", "lat_min": "50", "lat_max": "53", "res": "0.5"}
        to_options(app, box)
        assert app.sc_unify == {"lon": [-4.0, 0.0], "lat": [50.0, 53.0], "res": 0.5}

    def test_how_they_are_regridded(self, app, sims):
        to_options(app, {"need": "yes", "method": "baseline", "baseline": "Fine run", "regrid": "nn"})
        assert app.sc_unify == {"baseline": "Fine run", "regrid": "nn"}

    def test_a_box_regridded(self, app, sims):
        box = {"need": "yes", "method": "box", "lon_min": "-4", "lon_max": "0", "lat_min": "50", "lat_max": "53",
               "res": "0.5", "regrid": "dis"}
        to_options(app, box)
        assert app.sc_unify == {"lon": [-4.0, 0.0], "lat": [50.0, 53.0], "res": 0.5, "regrid": "dis"}

    def test_a_method_that_is_not_one_is_bilinear(self, app, sims):
        to_options(app, {"need": "yes", "method": "baseline", "baseline": "Fine run", "regrid": "best"})
        assert app.sc_unify == {"baseline": "Fine run"}

    def test_the_methods_and_what_is_drawn_of_each_simulation_are_sent(self, app, sims):
        shown = to_grids(app)
        assert list(shown["regrid_methods"]) == ["bil", "nn", "dis", "con"]
        # the first variable's first file, and the model variable read from it (the first of a sum)
        assert [field["variable"] for field in shown["grid_fields"]] == ["P1_Chl", "P1_Chl"]
        assert all(field["file"].endswith(".nc") for field in shown["grid_fields"])

    def test_each_simulation_is_drawn(self, app, sims):
        to_grids(app)
        status, reply = post(app, "/api/simcompare_panel", {"number": 2})
        assert status == 200 and reply["ok"], reply
        assert reply["variable"] == "P1_Chl" and reply["item"]
        assert post(app, "/api/simcompare_panel", {"number": 3})[0] == 400
        assert post(app, "/api/simcompare_panel", {"number": 0})[0] == 400

    def test_the_maps_are_of_the_second_time_step(self, app, sims, monkeypatch):
        """The second time step of each simulation, which the unified grid is made from too."""
        from oceanval import grid_align

        to_grids(app)
        files = app.sc_placed["Fine run"][0]["files"]
        reply = post(app, "/api/simcompare_panel", {"number": 2})[1]
        # a file a year of monthly output: the first file's second month
        assert reply["ok"] and (reply["file"], reply["step"]) == (os.path.basename(files[0]), 2)
        steps = []
        real = grid_align.field_for_cdo
        monkeypatch.setattr(grid_align, "field_for_cdo", lambda *args: steps.append(args[-1]) or real(*args))
        form = {"need": "yes", "method": "baseline", "baseline": "Fine run"}
        assert post(app, "/api/simcompare_generate", {"form": form})[1]["ok"]
        assert steps == [1, 1]

    def test_a_file_of_one_time_step_gives_the_next_files_first(self, app, tmp_path):
        """The simulation's second time step, wherever it is: a first file of one time is followed by the next's."""
        files = [str(tmp_path / "a.nc"), str(tmp_path / "b.nc")]
        counts = {files[0]: 1, files[1]: 1}

        class Times:
            def __init__(self, path, checks=False):
                self.times = [0] * counts[path]

        import oceanval.sim_compare_app as module

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(module.nc, "open_data", Times)
            assert app._sc_field_step(files) == (files[1], 0)
            # worked out once for each first file
            counts[files[0]] = 5
            assert app._sc_field_step(files) == (files[1], 0)
            # a simulation of one time step has only its first
            assert app._sc_field_step([files[1]]) == (files[1], 0)

    def test_the_unified_grid_is_a_preview_on_a_baseline(self, app, sims):
        to_grids(app)
        form = {"need": "yes", "method": "baseline", "baseline": "Fine run", "regrid": "nn"}
        status, reply = post(app, "/api/simcompare_generate", {"form": form})
        assert status == 200 and reply["ok"], reply
        assert 0 < reply["shared"] <= reply["total"] and reply["item"]
        # the grid of the baseline: the half degree one
        assert reply["total"] == len(B_LATS) * len(B_LONS)
        # a preview: nothing is chosen by it
        assert app.sc_unify is None and app.view == "simcompare_grids"

    def test_the_state_says_what_generating_is_doing(self, app, sims, monkeypatch):
        to_grids(app)
        assert state(app)["simcompare"]["stage"] is None
        entered, release = threading.Event(), threading.Event()
        real = sim_unify.crop_to
        said = []

        def held(ds, box):
            entered.set()
            assert release.wait(60)
            return real(ds, box)

        monkeypatch.setattr(sim_unify, "crop_to", held)
        progress = app._sc_progress
        monkeypatch.setattr(app, "_sc_progress", lambda *args: said.append(sim_unify.stage_text(*args)) or progress(*args))
        form = {"need": "yes", "method": "baseline", "baseline": "Fine run"}
        done = []
        thread = threading.Thread(target=lambda: done.append(post(app, "/api/simcompare_generate", {"form": form})))
        thread.start()
        try:
            assert entered.wait(60)
            assert state(app)["simcompare"]["stage"] == "Cropping Base run…"
        finally:
            release.set()
            thread.join(120)
        assert done[0][0] == 200 and done[0][1]["ok"], done
        # cropping, regridding and unifying, in that order, each said once
        assert said == ["Cropping Base run…", "Regridding Base run…", "Unifying the simulations…"]
        assert state(app)["simcompare"]["stage"] is None

    def test_the_unified_grid_is_a_preview_on_a_box(self, app, sims):
        to_grids(app)
        box = {"need": "yes", "method": "box", "lon_min": "-4", "lon_max": "0", "lat_min": "50", "lat_max": "53", "res": "0.5"}
        status, reply = post(app, "/api/simcompare_generate", {"form": box})
        assert status == 200 and reply["ok"], reply
        # nctoolkit's to_latlon has cells on both edges of the box
        assert reply["total"] == 9 * 7 and 0 < reply["shared"] <= reply["total"]

    @pytest.mark.parametrize(
        "form, boxes",
        [
            ({}, {"need"}),
            ({"need": "no"}, {"need"}),
            ({"need": "yes"}, {"method"}),
            ({"need": "yes", "method": "baseline"}, {"baseline"}),
            ({"need": "yes", "method": "box", "lon_min": "x"}, {"lon_min", "lon_max", "lat_min", "lat_max", "res"}),
        ],
    )
    def test_the_preview_is_checked_as_continue_is(self, app, sims, form, boxes):
        to_grids(app)
        status, reply = post(app, "/api/simcompare_generate", {"form": form})
        assert status == 400 and set(reply["errors"]) == boxes

    def test_the_preview_and_the_maps_are_for_the_grids_step_only(self, app, sims):
        to_options(app)
        assert post(app, "/api/simcompare_panel", {"number": 1})[0] == 409
        assert post(app, "/api/simcompare_generate", {"form": {"need": "yes", "method": "baseline", "baseline": "Fine run"}})[0] == 409

    def test_back_keeps_the_boxes(self, app, sims):
        to_grids(app)
        assert post(app, "/api/back", {"form": {"need": "yes", "method": "box", "res": "0.25"}})[0] == 200
        assert app.view == "simcompare_files"
        assert state(app)["simcompare"]["unify_form"]["res"] == "0.25"


def write_mask(path):
    """A mask on the usual grid, 1 from longitude -2 eastwards."""
    import xarray as xr

    from simulations import ANALYSIS_LATS, ANALYSIS_LONS

    lon, lat = np.array(ANALYSIS_LONS), np.array(ANALYSIS_LATS)
    values = np.where(lon[None, :] >= -2.0, 1.0, 0.0) * np.ones((lat.size, 1))
    coords = {
        "lat": ("lat", lat, {"units": "degrees_north", "standard_name": "latitude"}),
        "lon": ("lon", lon, {"units": "degrees_east", "standard_name": "longitude"}),
    }
    xr.DataArray(values.astype("f4"), coords=coords, dims=("lat", "lon"), name="wanted").to_dataset().to_netcdf(path)
    return str(path)


class TestDomainMask:
    """The grids step's question: No to start, a file for Yes, checked and drawn as validate's is, and written
    into the script."""

    def test_no_to_start(self, app, sims):
        shown = to_grids(app)
        assert shown["unify_form"]["domain_mask"] == "no" and shown["unify_form"]["domain_mask_path"] == ""
        assert post(app, "/api/simcompare_grids", {"form": {"need": "no"}}) == (200, {"ok": True})
        assert app.sc_domain_mask is None

    def test_the_file_is_checked_when_the_step_is_left(self, app, sims, tmp_path):
        mask = write_mask(tmp_path / "mask.nc")
        to_options(app, {"need": "no", "domain_mask": "yes", "domain_mask_path": mask})
        assert app.view == "analyze_options" and app.sc_domain_mask == mask

    def test_a_relative_path_is_from_the_directory_worked_in(self, app, sims, tmp_path):
        write_mask(tmp_path / "mask.nc")
        to_options(app, {"need": "no", "domain_mask": "yes", "domain_mask_path": "mask.nc"})
        assert app.sc_domain_mask == str(tmp_path / "mask.nc")

    @pytest.mark.parametrize("path, error", [("", "Give the path"), ("nothing.nc", "There is no file there.")])
    def test_yes_needs_a_file_that_is_a_mask(self, app, sims, path, error):
        to_grids(app)
        form = {"need": "no", "domain_mask": "yes", "domain_mask_path": path}
        status, reply = post(app, "/api/simcompare_grids", {"form": form})
        assert status == 400 and list(reply["errors"]) == ["domain_mask_path"]
        assert reply["errors"]["domain_mask_path"].startswith(error)
        assert app.view == "simcompare_grids"

    def test_a_file_typed_with_no_is_not_used(self, app, sims, tmp_path):
        to_options(app, {"need": "no", "domain_mask": "no", "domain_mask_path": write_mask(tmp_path / "mask.nc")})
        assert app.sc_domain_mask is None

    def test_it_goes_with_unify(self, app, sims, tmp_path):
        mask = write_mask(tmp_path / "mask.nc")
        to_options(app, {"need": "yes", "method": "baseline", "baseline": "Fine run", "domain_mask": "yes", "domain_mask_path": mask})
        assert app.sc_unify == {"baseline": "Fine run"} and app.sc_domain_mask == mask

    def test_the_map_is_drawn_on_the_grids_step_only(self, app, sims, tmp_path):
        mask = write_mask(tmp_path / "mask.nc")
        assert post(app, "/api/domain_mask", {"path": mask})[0] == 409
        to_grids(app)
        status, reply = post(app, "/api/domain_mask", {"path": mask})
        assert status == 200 and reply["ok"], reply
        assert (reply["kept"], reply["total"]) == (12, 20) and reply["item"]["doc"]
        assert post(app, "/api/domain_mask", {"path": "nothing.nc"}) == (
            200, {"ok": False, "error": "There is no file there."})
        assert post(app, "/api/simcompare_grids", {"form": {"need": "no"}}) == (200, {"ok": True})
        assert post(app, "/api/domain_mask", {"path": mask})[0] == 409

    def test_back_keeps_the_boxes(self, app, sims, tmp_path):
        mask = write_mask(tmp_path / "mask.nc")
        to_grids(app)
        form = {"need": "no", "domain_mask": "yes", "domain_mask_path": mask}
        assert post(app, "/api/back", {"form": form})[0] == 200
        shown = state(app)["simcompare"]["unify_form"]
        assert (shown["domain_mask"], shown["domain_mask_path"]) == ("yes", mask)

    def test_the_script_has_it_only_when_there_is_one(self, app, sims, runs, tmp_path):
        mask = write_mask(tmp_path / "mask.nc")
        to_options(app, {"need": "no", "domain_mask": "yes", "domain_mask_path": mask})
        post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY})
        post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "1"}})
        post(app, "/api/analyze_option", {"kind": "figure_code", "form": {"choice": "no"}})
        assert post(app, "/api/analyze_run", OUTPUT) == (200, {"ok": True})
        call = ast.parse(open(os.path.join(app.cwd, "comparison.py")).read()).body[-1].value
        given = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
        assert given["domain_mask"] == mask

    def test_the_form_is_cleaned(self):
        clean = sim_compare_forms.clean_unify({"domain_mask": "YES", "domain_mask_path": "  Mask.nc "})
        assert clean["domain_mask"] == "yes" and clean["domain_mask_path"] == "Mask.nc"
        assert sim_compare_forms.clean_unify({"domain_mask": "maybe"})["domain_mask"] == "no"


CLIMATOLOGY = {
    "rows": {
        "Chlorophyll": {"use": True, "start": "2013", "end": "2014", "surface": True},
        "Temperature": {"use": True, "start": "2013", "end": "2014", "surface": True, "water_column": True, "how": "mean"},
    },
    "thickness": "e3t",
}


class TestOptionsAndRun:
    def test_the_options_are_for_the_variables_as_they_are_in_all(self, app, sims):
        shown = to_options(app)
        assert [row["name"] for row in shown["placed"]] == ["Chlorophyll", "Temperature"]
        assert shown["placed"][1]["years"] == [2013, 2014]
        # the thickness is looked for among the model variables of all the simulations
        assert shown["picker"]["available"] == ["P1_Chl", "P2_Chl", "e3t", "thetao"]
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY}) == (200, {"ok": True})
        # years the simulations do not all have are refused
        late = {"rows": {"Temperature": {"use": True, "start": "2013", "end": "2016", "surface": True}}}
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": late})[0] == 400

    def test_the_script_can_be_saved_without_the_times_of_the_checks(self, app, sims, runs):
        to_options(app, {"need": "yes", "method": "baseline", "baseline": "Base run"})
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY}) == (200, {"ok": True})
        assert post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "2"}})[0] == 200
        assert post(app, "/api/analyze_option", {"kind": "figure_code", "form": {"choice": "no"}})[0] == 200
        assert post(app, "/api/analyze_save_script", OUTPUT) == (200, {"ok": True})
        script = os.path.join(app.cwd, "comparison.py")
        assert runs == [] and app.view == "script_saved"
        run = get_json(app, "/api/state")[1]["run"]
        assert run["script_saved"] is True and run["script"] == script
        call = ast.parse(open(script).read()).body[-1].value
        assert ast.unparse(call.func) == "oceanval.compare_simulations"
        given = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
        # the times the checks read are temporary, so they are not in a script kept for later
        assert all("times_index" not in sim for sim in given["simulations"])
        sim_compare.check_simulations(given["simulations"])

    def test_where_the_output_is_saved_is_checked_by_the_run(self, app, sims, runs):
        to_options(app, {"need": "yes", "method": "baseline", "baseline": "Base run"})
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY}) == (200, {"ok": True})
        assert post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "2"}})[0] == 200
        assert post(app, "/api/analyze_option", {"kind": "figure_code", "form": {"choice": "no"}})[0] == 200
        # the boxes of the last page are not the first page's: it starts with them
        assert state(app)["analyze"]["output"] == {
            "out_dir": app.cwd, "out": "simulation_comparison.py", "out_typed": False, "replace_analysis": False}
        status, reply = post(app, "/api/analyze_run", {"form": {"out_dir": "", "out": ""}})
        assert status == 400 and set(reply["errors"]) == {"out"} and runs == []
        os.makedirs(os.path.join(app.cwd, "somewhere"))
        status, reply = post(app, "/api/analyze_run", {"form": {"out_dir": "", "out": "somewhere"}})
        assert status == 400 and set(reply["errors"]) == {"out"}
        # what was sent is kept for the page, and the script goes where it says
        assert state(app)["analyze"]["output"] == {"out_dir": "", "out": "somewhere", "out_typed": False, "replace_analysis": False}
        assert post(app, "/api/analyze_save_script", {"form": {"out_dir": "", "out": "mine.py"}}) == (200, {"ok": True})
        assert os.path.exists(os.path.join(app.cwd, "mine.py"))

    def test_a_comparison_in_the_output_directory_has_to_be_ticked_to_be_replaced(self, app, sims, runs):
        to_options(app, {"need": "yes", "method": "baseline", "baseline": "Base run"})
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY}) == (200, {"ok": True})
        assert post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "2"}})[0] == 200
        assert post(app, "/api/analyze_option", {"kind": "figure_code", "form": {"choice": "no"}})[0] == 200
        # an analysis there is not this run's to replace: only the empty script name is said
        os.makedirs(os.path.join(app.cwd, analysis.FOLDER))
        status, reply = post(app, "/api/analyze_run", {"form": {"out_dir": app.cwd, "out": ""}})
        assert status == 400 and set(reply["errors"]) == {"out"}
        os.makedirs(os.path.join(app.cwd, sim_compare.FOLDER))
        # which the user must tick to replace, having named the directory
        status, reply = post(app, "/api/analyze_run", {"form": {"out_dir": app.cwd, "out": "comparison.py"}})
        assert status == 400 and reply["errors"] == {"replace_analysis": ""} and runs == []
        status, reply = post(
            app, "/api/analyze_run", {"form": {"out_dir": "", "out": "comparison.py", "replace_analysis": True}}
        )
        assert status == 400 and reply["errors"] == {"out_dir": sim_compare_forms.NAME_THE_DIRECTORY}
        assert "a comparison" in sim_compare_forms.NAME_THE_DIRECTORY
        assert post(
            app, "/api/analyze_save_script", {"form": {"out_dir": app.cwd, "out": "mine.py", "replace_analysis": True}}
        ) == (200, {"ok": True})
        assert os.path.exists(os.path.join(app.cwd, "mine.py"))

    def test_back_goes_to_the_grids(self, app, sims):
        to_options(app)
        assert post(app, "/api/back")[0] == 200
        assert app.view == "simcompare_grids"

    def test_the_run_writes_the_script_and_runs_it(self, app, sims, runs):
        to_options(app, {"need": "yes", "method": "baseline", "baseline": "Base run"})
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY}) == (200, {"ok": True})
        assert post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "2"}})[0] == 200
        assert post(app, "/api/analyze_option", {"kind": "figure_code", "form": {"choice": "no"}})[0] == 200
        assert post(app, "/api/analyze_run", OUTPUT) == (200, {"ok": True})
        script = os.path.join(app.cwd, "comparison.py")
        assert runs == [(["simcompare", script], "python comparison.py")]
        assert app.view == "running" and app.results_dir == app.cwd
        tree = ast.parse(open(script).read())
        call = tree.body[-1].value
        assert ast.unparse(call.func) == "oceanval.compare_simulations"
        given = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
        assert [sim["name"] for sim in given["simulations"]] == ["Base run", "Fine run"]
        assert given["simulations"][0]["sim_dir"].endswith("base") and given["simulations"][0]["n_dirs_down"] == 1
        assert given["variables"] == {
            "Chlorophyll": {"Base run": "P1_Chl+P2_Chl", "Fine run": "P1_Chl+P2_Chl"},
            "Temperature": {"Base run": "thetao", "Fine run": "thetao"},
        }
        assert given["unify"] == {"baseline": "Base run"}
        assert given["climatology"]["Temperature"]["water_column"] == "mean"
        assert given["thickness"] == "e3t" and given["cores"] == 2 and given["figure_code"] is False
        for left_out in ("sim_dir", "n_dirs_down", "time_shift"):
            assert left_out not in given
        # the script is a valid call of the function
        sim_compare.check_simulations(given["simulations"])
        sim_compare.check_variables(given["variables"], ["Base run", "Fine run"])
        # the times the check of the dates read are kept for the run, though the check's folders are removed
        indexes = [sim["times_index"] for sim in given["simulations"]]
        assert len(set(indexes)) == 2 and all(os.path.isfile(path) for path in indexes)
        assert all(not path.startswith(app.cwd) for path in indexes)
        from oceanval.model_check import load_times_index

        for sim, path in zip(given["simulations"], indexes):
            files = [os.path.join(root, name) for root, _, names in os.walk(sim["sim_dir"]) for name in names if name.endswith(".nc")]
            assert set(load_times_index(path, files)) == set(files)

    def test_the_kept_times_are_removed_when_the_simulations_change(self, app, sims, runs):
        to_options(app)
        post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY})
        post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "1"}})
        post(app, "/api/analyze_option", {"kind": "figure_code", "form": {"choice": "no"}})
        assert post(app, "/api/analyze_run", OUTPUT) == (200, {"ok": True})
        folder = app.sc_times_dir
        assert folder and os.path.isdir(folder)
        with app._lock:
            app._sc_forget_after_sims()
        assert not os.path.exists(folder) and app.sc_times_dir is None

    def test_without_unify_there_is_no_unify_in_the_script(self, app, sims, runs):
        to_options(app)
        post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY})
        post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "1"}})
        post(app, "/api/analyze_option", {"kind": "figure_code", "form": {"choice": "yes"}})
        assert post(app, "/api/analyze_run", OUTPUT) == (200, {"ok": True})
        assert "unify=" not in open(os.path.join(app.cwd, "comparison.py")).read()

    def test_the_report_is_served_from_its_own_folder(self, app, tmp_path):
        pages = tmp_path / sim_compare.FOLDER / sim_compare.REPORT / "_build" / "html" / "notebooks"
        os.makedirs(pages)
        (pages / "000_overview.html").write_text("<p>a comparison</p>")
        os.symlink(
            os.path.join(sim_compare.REPORT, "_build", "html", "notebooks", "000_overview.html"),
            tmp_path / sim_compare.FOLDER / sim_compare.REPORT_LINK,
        )
        app.action = "simcompare"
        app.results_dir = str(tmp_path)
        run = get_json(app, "/api/state")[1]["run"]
        assert run["report_exists"]
        assert run["report_href"].startswith(f"simcompare/{app.token}/")
        assert get(app, "/" + run["report_href"], token=None) == (200, "<p>a comparison</p>")
        assert get(app, "/simcompare/wrong/notebooks/000_overview.html")[0] == 403


class TestPhenology:
    """Phenology needs daily output in every simulation."""

    @staticmethod
    def write(tmp_path, base_daily, fine_daily):
        write_analysis_simulation(str(tmp_path / "base"), years=(2013, 2014), daily=base_daily)
        write_analysis_simulation(
            str(tmp_path / "fine"), years=(2013, 2014), daily=fine_daily, trend=1.0, lats=B_LATS, lons=B_LONS
        )

    def test_it_can_be_chosen_and_run_when_all_are_daily(self, app, tmp_path, runs):
        self.write(tmp_path, True, True)
        shown = to_options(app)
        assert [(row["time_res"], row["time_res_by_sim"]) for row in shown["placed"]] == [
            ("1d", {"Base run": "1d", "Fine run": "1d"})
        ] * 2
        phenology = {"rows": {"Chlorophyll": {"use": True, "start": "2013", "end": "2014"}}}
        assert post(app, "/api/analyze_option", {"kind": "phenology", "form": phenology}) == (200, {"ok": True})
        post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "1"}})
        post(app, "/api/analyze_option", {"kind": "figure_code", "form": {"choice": "no"}})
        assert post(app, "/api/analyze_run", OUTPUT) == (200, {"ok": True})
        call = ast.parse(open(os.path.join(app.cwd, "comparison.py")).read()).body[-1].value
        given = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
        assert given["phenology"] == {"Chlorophyll": {"start": 2013, "end": 2014}}

    def test_one_simulation_that_is_not_daily_is_named(self, app, tmp_path):
        self.write(tmp_path, True, False)
        shown = to_options(app)
        assert shown["placed"][0]["time_res"] == "monthly"
        assert shown["placed"][0]["time_res_by_sim"] == {"Base run": "1d", "Fine run": "monthly"}
        phenology = {"rows": {"Chlorophyll": {"use": True, "start": "2013", "end": "2014"}}}
        status, reply = post(app, "/api/analyze_option", {"kind": "phenology", "form": phenology})
        assert status == 400 and "not daily" in str(reply)


def test_phenology_is_ticked_in_a_browser(browser, tmp_path):
    for name, daily, kwargs in (("base", True, {}), ("fine", True, dict(trend=1.0, lats=B_LATS, lons=B_LONS))):
        write_analysis_simulation(str(tmp_path / name), years=(2013, 2014), daily=daily, **kwargs)
    app = App(cwd=str(tmp_path))
    url = app.start()
    errors = []
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.click("#home-simcompare")
        page.wait_for_selector("#view-simcompare-sims:not([hidden])")
        for number, (name, folder) in enumerate((("Base run", "base"), ("Fine run", "fine")), 1):
            page.fill(f"#sc-name_{number}", name)
            page.fill(f"#sc-dir_{number}", folder)
            page.fill(f"#sc-ndown_{number}", "1")
        page.wait_for_function("() => !document.querySelector('#sc-continue').disabled")
        page.click("#sc-continue")
        page.wait_for_selector("#scv-main:not([hidden])", timeout=60000)
        cells = page.locator("#scv-rows > tr").first.locator("input.s-input")
        for cell, text in zip((cells.nth(0), cells.nth(1), cells.nth(2)), ("Chlorophyll", "P1_Chl+P2_Chl", "P1_Chl+P2_Chl")):
            cell.fill(text)
        page.wait_for_function("() => !document.querySelector('#scv-continue').disabled")
        page.click("#scv-continue")
        page.wait_for_selector("#view-simcompare-files:not([hidden])")
        page.wait_for_function("() => !document.querySelector('#scf-continue').disabled", timeout=60000)
        page.click("#scf-continue")
        page.wait_for_selector("#view-simcompare-grids:not([hidden])")
        page.check("#scg-need-no")
        page.click("#scg-continue")
        page.wait_for_selector("#view-analyze-options:not([hidden])")
        page.click("#ao-phenology")
        page.wait_for_selector("#phen-dialog:not([hidden])")
        tick = page.locator("#phen-body input.tick").first
        assert tick.is_enabled()
        tick.check()
        # the years are asked for, not assumed
        years = page.locator("#phen-body input.is-year")
        years.nth(0).fill("2013")
        years.nth(1).fill("2014")
        page.wait_for_function("() => !document.querySelector('#phen-confirm').disabled")
        page.click("#phen-confirm")
        page.wait_for_selector("#phen-dialog", state="hidden")
        # the tile says what was chosen once the window has it
        page.wait_for_selector("#ao-phenology.is-chosen")
        assert "Chlorophyll" in page.inner_text("#ao-phenology-text")
        page.click("#ao-continue")
        page.wait_for_selector("#ao-page-2:not([hidden])")
        assert page.is_hidden("#title")
        assert page.is_disabled("#ao-next")
        page.select_option("#ao-cores", "1")
        page.check("#ao-code-no")
        page.click("#ao-next")
        page.wait_for_selector("#ao-page-3:not([hidden])")
        # where the comparison is saved is asked last, for it, and has no tick to replace anything
        assert page.is_hidden("#title")
        assert page.inner_text("#ao-save-lede") == "Choose where to save the comparison"
        assert page.input_value("#ao-out") == "simulation_comparison.py" and page.is_hidden("#ao-row-replace")
        assert page.inner_text("#ao-run") == "Run the comparison"
        assert page.inner_text("#ao-save") == "Save Python script to run later"
        assert errors == []
    finally:
        app.close()


def test_the_child_runs_a_comparison_script(tmp_path, monkeypatch):
    import sys

    from oceanval import app_child

    script = tmp_path / "script.py"
    script.write_text("print('ran the comparison')\n")
    monkeypatch.setattr(sys, "argv", ["x"])
    app_child.main(["simcompare", str(script)])


def test_a_simulation_row_says_it_is_finding_files_until_the_answer_is_in(browser, tmp_path):
    write_analysis_simulation(str(tmp_path / "base"), years=(2013, 2014), daily=False)
    app = App(cwd=str(tmp_path))
    url = app.start()
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    try:
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-simcompare")
        page.wait_for_selector("#view-simcompare-sims:not([hidden])")
        held = []
        page.route("**/api/probe*", lambda route: held.append(route))
        page.fill("#sc-dir_1", "base")
        page.fill("#sc-ndown_1", "1")
        assert "Finding files" in page.inner_text("#sc-m-1")
        assert page.is_visible("#sc-m-1 .identify__spinner")
        for _ in range(50):
            if held:
                break
            page.wait_for_timeout(100)
        assert held
        for route in held:
            route.continue_()
        page.unroute("**/api/probe*")
        page.wait_for_function("() => document.querySelector('#sc-m-1').innerText.includes('netCDF files')")
        assert "Finding files" not in page.inner_text("#sc-m-1")
        assert page.locator("#sc-m-1 .identify__spinner").count() == 0
    finally:
        page.close()
        app.close()


def test_a_simulation_row_fills_in_the_directories_down_from_the_layout(browser, tmp_path):
    write_folders(tmp_path / "months", "YYYY/MM")
    write_folders(tmp_path / "flat", "flat")
    write_analysis_simulation(str(tmp_path / "base"), years=(2013, 2014), daily=False)
    app = App(cwd=str(tmp_path))
    url = app.start()
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    try:
        page.add_init_script("window.__keepFolderStructure = true")
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-simcompare")
        page.wait_for_selector("#view-simcompare-sims:not([hidden])")
        page.fill("#sc-dir_1", "months")
        page.wait_for_function("document.querySelector('#sc-ndown_1').value === '2'")
        box = page.locator("#sc-ndown_1")
        assert box.evaluate("node => node.classList.contains('is-detected')")
        assert box.evaluate("node => getComputedStyle(node).fontWeight") == "700"
        popout = page.locator("#folder-structure")
        popout.wait_for(state="visible")
        assert "For Simulation 1, it looks like the folder structure is YYYY/MM/**.nc" in popout.inner_text()
        assert "directories down is assumed to be 2" in popout.inner_text()
        assert "Folder structure was identified" not in page.inner_text("#sc-m-1")
        page.click("#folder-structure-continue")
        assert popout.is_hidden()
        page.fill("#sc-dir_2", "flat")
        page.wait_for_function("document.querySelector('#sc-ndown_2').value === '0'")
        popout.wait_for(state="visible")
        assert "For Simulation 2, it looks like the folder structure is *.nc" in popout.inner_text()
        page.click("#folder-structure-continue")
        # typing over it makes it the user's, and the suggestion goes
        page.fill("#sc-ndown_1", "1")
        assert not page.locator("#sc-ndown_1").evaluate("node => node.classList.contains('is-detected')")
        # a directory without the layout empties what OceanVal filled in
        page.fill("#sc-dir_2", "base")
        page.wait_for_function("document.querySelector('#sc-ndown_2').value === ''")
    finally:
        app.close()


def test_a_simulation_row_starts_skipping_nothing(browser, tmp_path):
    write_analysis_simulation(str(tmp_path / "base"), years=(2013, 2014), daily=False)
    app = App(cwd=str(tmp_path))
    url = app.start()
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    amber = lambda n: page.locator(f"#sc-exclude_{n}").evaluate("node => node.classList.contains('is-detected')")
    try:
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-simcompare")
        page.wait_for_selector("#view-simcompare-sims:not([hidden])")
        # every row starts with nothing skipped, and an unused row is not a simulation to name
        assert [page.input_value(f"#sc-exclude_{n}") for n in (1, 2)] == ["", ""]
        assert not amber(1) and not amber(2)
        assert page.is_hidden("#sc-row-3")
        assert "skipped" not in page.inner_text("#sc-m-1")
        assert "Give this simulation a name" not in page.inner_text("#sc-m-1")
        page.fill("#sc-dir_1", "base")
        page.wait_for_function("document.querySelector('#sc-m-1').innerText.includes('Folder structure') || true")
        assert "skipped" not in page.inner_text("#sc-m-1")
        # a row added starts the same way
        page.click("#sc-add")
        assert page.input_value("#sc-exclude_3") == "" and not amber(3)
        # changing it
        page.click("#sc-row-1 summary")
        page.fill("#sc-exclude_1", "restart 5d")
        assert not amber(1)
        assert "skipped" not in page.inner_text("#sc-m-1")
        page.fill("#sc-exclude_1", "")
        assert not amber(1)
    finally:
        app.close()


def test_comparing_simulations_in_a_browser(browser, tmp_path):
    write_analysis_simulation(str(tmp_path / "base"), years=(2013, 2014), daily=False)
    write_analysis_simulation(str(tmp_path / "fine"), years=(2013, 2014), daily=False, trend=1.0, lats=B_LATS, lons=B_LONS)
    app = App(cwd=str(tmp_path))
    url = app.start()
    errors = []
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        assert page.locator("#view-home .choice").count() == 4
        page.click("#home-simcompare")
        page.wait_for_selector("#view-simcompare-sims:not([hidden])")
        # two simulations to start, a third can be added
        assert page.is_visible("#sc-row-2") and page.is_hidden("#sc-row-3")
        assert page.is_disabled("#sc-continue")
        page.fill("#sc-name_1", "Base run")
        page.fill("#sc-dir_1", "base")
        # the directories down can be picked from a list of 0 to 5, opened by the arrow in the box, or typed
        edge = page.locator("#sc-ndown_1").bounding_box()
        page.locator("#sc-ndown_1").click(position={"x": edge["width"] - 8, "y": edge["height"] / 2})
        options = page.locator("body > .year-list .combo__option")
        assert [text[0] for text in options.all_inner_texts()] == list("012345")
        options.nth(1).click()
        assert page.input_value("#sc-ndown_1") == "1"
        page.fill("#sc-name_2", "Fine run")
        page.fill("#sc-dir_2", "fine")
        page.fill("#sc-ndown_2", "1")
        page.wait_for_function("() => !document.querySelector('#sc-continue').disabled")
        page.wait_for_function("() => document.querySelector('#sc-m-1').innerText.includes('netCDF files')")
        page.click("#sc-add")
        assert page.is_visible("#sc-row-3")
        page.click("#sc-remove-3")
        assert page.is_hidden("#sc-row-3")
        page.click("#sc-continue")
        page.wait_for_selector("#scv-main:not([hidden])", timeout=60000)
        # one row, a model variable for each simulation
        assert page.locator("#scv-rows > tr").count() == 1
        cells = page.locator("#scv-rows > tr").first.locator("input.s-input")
        assert cells.count() == 3
        # each row has its + and × in one box: + adds a row directly below this one
        names = lambda: [page.locator("#scv-rows > tr").nth(n).locator("input.s-input").first.input_value()
                         for n in range(page.locator("#scv-rows > tr").count())]
        page.locator("#scv-rows > tr").first.locator("input.s-input").first.fill("A")
        page.locator("#scv-rows > tr").first.locator(".analyze-pill .analyze-add").click()
        assert names() == ["A", ""]
        page.locator("#scv-rows > tr").nth(1).locator("input.s-input").first.fill("B")
        page.locator("#scv-rows > tr").first.locator(".analyze-pill .analyze-add").click()
        assert names() == ["A", "", "B"]
        page.locator("#scv-rows > tr").nth(1).locator(".analyze-pill .analyze-remove").click()
        page.locator("#scv-rows > tr").last.locator(".analyze-pill .analyze-remove").click()
        assert names() == ["A"]
        # a lone row with something in it can be removed: a blank row takes its place, with a grey ×
        pill = page.locator("#scv-rows > tr").first.locator(".analyze-pill")
        assert pill.locator(".analyze-remove").is_enabled()
        pill.locator(".analyze-remove").click()
        assert names() == [""]
        assert pill.locator(".analyze-remove").is_disabled()
        assert pill.locator(".analyze-add").is_enabled()
        page.locator("#scv-rows > tr").first.locator("input.s-input").first.fill("C")
        assert page.locator("#scv-rows > tr").first.locator(".analyze-remove").is_enabled()
        pill.locator(".analyze-add").click()
        assert page.locator("#scv-rows > tr").first.locator(".analyze-remove").is_enabled()
        page.locator("#scv-rows > tr").first.locator(".analyze-remove").click()
        assert names() == [""]
        # a simulation's box lists its variables as it is typed in, with wildcards
        assert page.locator("details.vars").count() == 0
        cells.nth(1).fill("P*_Chl")
        assert page.evaluate("() => [...document.querySelectorAll('.vars__popover:not([hidden]) tbody tr:not([hidden])')].map((row) => row.cells[1].textContent)") == ["P1_Chl", "P2_Chl"]
        cells.nth(1).press("Escape")
        cells.nth(0).fill("Temperature")
        cells.nth(1).fill("thetao")
        cells.nth(2).fill("thetao")
        page.wait_for_function("() => !document.querySelector('#scv-continue').disabled")
        page.click("#scv-continue")
        page.wait_for_selector("#view-simcompare-files:not([hidden])")
        page.wait_for_function("() => !document.querySelector('#scf-continue').disabled", timeout=60000)
        assert page.locator("#scf-sims .review").count() == 2
        page.click("#scf-continue")
        page.wait_for_selector("#view-simcompare-grids:not([hidden])")
        # nothing is chosen; the question must be answered
        assert page.is_disabled("#scg-continue")
        assert not page.is_checked("#scg-need-yes") and not page.is_checked("#scg-need-no")
        assert page.is_hidden("#scg-how")
        # the question is inside its box, not across the box's top edge
        legend_inside = """(box) => {
            const fieldset = document.querySelector(box);
            const legend = fieldset.querySelector('legend').getBoundingClientRect();
            const edge = fieldset.getBoundingClientRect();
            return legend.top >= edge.top + parseFloat(getComputedStyle(fieldset).paddingTop) - 1 && legend.bottom <= edge.bottom;
        }"""
        assert page.evaluate(legend_inside, "#scg-need-group")
        page.check("#scg-need-yes")
        page.wait_for_selector("#scg-how:not([hidden])")
        assert page.evaluate(legend_inside, "#scg-how .sc-choice")
        # the missing-in-all rule is said, whichever way they are put on one grid
        assert "missing in all of them" in page.inner_text("#scg-note")
        assert page.is_disabled("#scg-continue")
        page.check("#scg-method-baseline")
        assert page.is_visible("#scg-baseline") and page.is_hidden("#scg-box")
        page.select_option("#scg-baseline-select", "Base run")
        assert page.is_enabled("#scg-continue")
        page.check("#scg-method-box")
        assert page.is_visible("#scg-box") and page.is_disabled("#scg-continue")
        for box, value in (("lon_min", "-4"), ("lon_max", "0"), ("lat_min", "50"), ("lat_max", "53"), ("res", "0.5")):
            page.fill(f"#scg-{box}", value)
        assert page.is_enabled("#scg-continue")
        page.click("#scg-continue")
        page.wait_for_selector("#view-analyze-options:not([hidden])")
        assert page.is_visible("#ao-compare-note")
        # the tiles are the first page; the run buttons are on the second, once something is chosen
        assert page.is_hidden("#title")
        assert page.is_disabled("#ao-continue")
        assert app.sc_unify == {"lon": [-4.0, 0.0], "lat": [50.0, 53.0], "res": 0.5}

        # a comparison already where it is to be saved must be ticked to be replaced
        os.makedirs(tmp_path / "old" / sim_compare.FOLDER)
        # an analysis there is not this run's to replace
        os.makedirs(tmp_path / "other" / analysis.FOLDER)
        for kind, form in (
            ("climatology", {"rows": {"Temperature": {"use": True, "start": "2013", "end": "2014", "surface": True}}}),
            ("cores", {"cores": "1"}), ("missing", {"from": "0", "to": "0"}), ("figure_code", {"choice": "no"}),
        ):
            assert post(app, "/api/analyze_option", {"kind": kind, "form": form})[0] == 200
        page.wait_for_function("() => !document.querySelector('#ao-continue').disabled")
        page.click("#ao-continue")
        page.wait_for_function("() => !document.querySelector('#ao-next').disabled")
        page.click("#ao-next")
        page.wait_for_selector("#ao-page-3:not([hidden])")
        assert page.is_hidden("#ao-row-replace")
        page.fill("#ao-out_dir", "other")
        page.wait_for_function("() => document.querySelector('#ao-m-out_dir').textContent.includes('inside this directory')")
        assert page.is_hidden("#ao-row-replace")
        page.fill("#ao-out_dir", "old")
        page.wait_for_selector("#ao-row-replace:not([hidden])")
        assert page.text_content("#ao-row-replace") == (
            "Tick to replace the comparison files in this directory (oceanval_simulation_comparison)")
        assert page.text_content("#ao-m-out_dir .group__line.is-warn") == (
            "There is a comparison here already, from an earlier run. It will be replaced: "
            "choose another directory to keep it, or tick below to replace it.")
        assert page.is_disabled("#ao-run") and page.is_disabled("#ao-save")
        page.check("#ao-replace_analysis")
        assert page.is_enabled("#ao-run") and page.is_enabled("#ao-save")
        # the tick was for that directory
        page.fill("#ao-out_dir", "old/")
        assert not page.is_checked("#ao-replace_analysis")
        assert page.is_disabled("#ao-run")

        # an empty box is the directory worked in, which must be named
        os.makedirs(tmp_path / sim_compare.FOLDER)
        page.fill("#ao-out_dir", "")
        page.wait_for_function("() => document.querySelector('#ao-m-out_dir').textContent.includes('a comparison in the directory')")
        assert page.is_disabled("#ao-run")
        assert errors == []
    finally:
        app.close()


class TestClosingTheBrowser:
    def test_it_quits_at_the_options_step(self, app, sims):
        app.page_timeout = 2
        to_options(app)
        assert get_json(app, "/api/state", page="a")[0] == 200
        assert app.closed.wait(15)
        assert app.closed_reason == "The browser window was closed."

    def test_it_quits_while_the_simulations_are_read(self, app, sims, monkeypatch):
        app.page_timeout = 2
        release = threading.Event()
        monkeypatch.setattr(app, "_sc_read_streams", lambda *args, **kwargs: release.wait(30))
        to_sims(app)
        assert post(app, "/api/simcompare_sims", {"form": sims_form()}) == (200, {"ok": True})
        assert get_json(app, "/api/state", page="a")[0] == 200
        try:
            assert app.closed.wait(15)
            assert app.closed_reason == "The browser window was closed."
        finally:
            release.set()


def test_the_grids_step_shows_each_simulation_and_a_preview_in_a_browser(browser, app, sims, monkeypatch):
    """Yes shows a map of each simulation and, after Generate unified grid, the
    cells they share on the grid chosen, none of which Continue waits for."""
    to_grids(app)
    # each stage of generating is held for a moment, as it may be over before the page is told of it
    said_progress = app._sc_progress
    monkeypatch.setattr(app, "_sc_progress", lambda *args: said_progress(*args) or time.sleep(0.8))
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(app.url)
        page.wait_for_selector("#view-simcompare-grids:not([hidden])")
        # nothing is drawn, or asked of the files, until Yes
        assert page.locator("#scg-grid .ag-card").count() == 0
        page.check("#scg-need-no")
        assert page.locator("#scg-grid .ag-card").count() == 0
        page.check("#scg-need-yes")
        page.wait_for_selector("#scg-map-1.is-drawn", timeout=60000)
        page.wait_for_selector("#scg-map-2.is-drawn", timeout=60000)
        # a card for each simulation, then the unified grid, and no plus: the simulations are given
        assert page.locator("#scg-grid .ag-card").count() == 3
        assert page.locator("#scg-add, #ag-add").count() == 0
        assert "Base run" in page.text_content("#scg-title-1") and "Fine run" in page.text_content("#scg-title-2")
        assert page.text_content("#scg-card-1 .ag-card__line:nth-of-type(2)").strip() == "Variable: P1_Chl"
        # the choices, then Generate
        assert page.is_hidden("#scg-controls") and page.is_disabled("#scg-continue")
        page.check("#scg-method-baseline")
        assert page.is_visible("#scg-controls") and page.is_disabled("#scg-generate")
        page.select_option("#scg-baseline-select", "Fine run")
        page.wait_for_function("() => !document.getElementById('scg-generate').disabled")
        assert page.locator("#scg-regrid-select option").all_text_contents() == [
            "Bilinear (default)", "Nearest neighbour", "Distance-weighted", "Conservative"]
        page.select_option("#scg-regrid-select", "nn")
        # the spinner beside the text while it is generated, which may be over in a moment, so it is watched for,
        # with each thing said to be done
        page.evaluate("""(id) => { window.sawSpinner = []; new MutationObserver(() => {
            const busy = document.querySelector(`#${id} .ag-map__empty.is-busy`);
            if (busy && busy.querySelector('.identify__spinner') && !window.sawSpinner.includes(busy.textContent)) window.sawSpinner.push(busy.textContent); })
            .observe(document.getElementById(id), { childList: true, subtree: true }); }""", "scg-map-unified")
        page.click("#scg-generate")
        page.wait_for_selector("#scg-map-unified.is-drawn", timeout=60000)
        said = page.evaluate("() => window.sawSpinner")
        assert said[0] == "Putting the simulations on one grid…"
        assert said[1:] == ["Cropping Base run…", "Regridding Base run…", "Unifying the simulations…"]
        assert "have a value in every simulation" in page.text_content("#scg-unified-msg")
        # a change makes it out of date, but does not stop Continue
        page.select_option("#scg-regrid-select", "")
        assert "Generate unified grid again" in page.text_content("#scg-map-unified")
        assert page.is_enabled("#scg-continue")
        # the box has the same maps and the same preview
        page.check("#scg-method-box")
        for box, text in (("lon_min", "-4"), ("lon_max", "0"), ("lat_min", "50"), ("lat_max", "53"), ("res", "0.5")):
            page.fill(f"#scg-{box}", text)
        page.click("#scg-generate")
        page.wait_for_selector("#scg-map-unified.is-drawn", timeout=60000)
        page.click("#scg-continue")
        page.wait_for_selector("#view-analyze-options:not([hidden])")
        assert app.sc_unify == {"lon": [-4.0, 0.0], "lat": [50.0, 53.0], "res": 0.5}
        assert errors == []
    finally:
        page.close()


def test_the_domain_mask_is_asked_in_a_browser(browser, tmp_path):
    write_analysis_simulation(str(tmp_path / "base"), years=(2013, 2014), daily=False)
    write_analysis_simulation(str(tmp_path / "fine"), years=(2013, 2014), daily=False, trend=1.0, lats=B_LATS, lons=B_LONS)
    mask = write_mask(tmp_path / "mask.nc")
    app = App(cwd=str(tmp_path))
    url = app.start()
    errors = []
    try:
        to_grids(app)
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.wait_for_selector("#view-simcompare-grids:not([hidden])")
        # No to start, with the file's box hidden
        assert page.is_checked("#scg-dm-no") and page.is_hidden("#scg-dm-detail")
        assert page.inner_text("#scg-dm-question") == "Do you want to mask part of the domain?"
        page.check("#scg-need-no")
        assert page.is_enabled("#scg-continue")
        # Yes needs a file that is a mask, which is drawn
        page.check("#scg-dm-yes")
        assert page.is_visible("#scg-dm-detail") and page.is_disabled("#scg-continue")
        assert "single variable and a single time step" in page.inner_text("#scg-dm-detail")
        page.fill("#scg-domain_mask_path", "nothing.nc")
        page.wait_for_function("() => document.querySelector('#scm-domain_mask_path').innerText.includes('There is no file there.')")
        assert page.is_disabled("#scg-continue")
        page.fill("#scg-domain_mask_path", mask)
        page.wait_for_function("() => document.querySelector('#scm-domain_mask_path').innerText.includes('12 of 20 cells will be matched up.')", timeout=60000)
        page.wait_for_selector("#dm-map-scg.is-drawn", timeout=60000)
        page.wait_for_function("() => !document.querySelector('#scg-continue').disabled", timeout=60000)
        page.click("#scg-continue")
        page.wait_for_selector("#view-analyze-options:not([hidden])")
        assert app.sc_domain_mask == mask
        assert not errors, errors
    finally:
        app.close()
