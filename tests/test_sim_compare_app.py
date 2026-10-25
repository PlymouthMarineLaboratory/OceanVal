"""The oceanval window's Compare simulations steps (see oceanval.sim_compare_app):
the simulations, the variables in each, their files, the grids, and the options
step Analyze shares, which ends in the run."""

import ast
import os

import numpy as np
import pytest

from oceanval import analysis, sim_compare, sim_compare_forms
from oceanval.app import App
from simulations import write_analysis_simulation
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
        assert 'class="choices is-three"' in page
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
        assert post(app, "/api/restart")[0] == 200
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

    def test_the_units_must_agree(self, app, sims, tmp_path):
        import glob

        import xarray as xr

        for path in glob.glob(str(tmp_path / "fine" / "*" / "*grid_T.nc")):
            with xr.open_dataset(path) as ds:
                ds = ds.load()
            ds["thetao"].attrs["units"] = "K"
            ds.to_netcdf(path)
        to_variables(app)
        status, reply = post(app, "/api/simcompare_variables", {"rows": ROWS[1:]})
        assert status == 400
        assert "in K here, and in degC in Base run" in reply["errors"]["0"]["models"]["Fine run"]

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

    def test_back_keeps_the_boxes(self, app, sims):
        to_grids(app)
        assert post(app, "/api/back", {"form": {"need": "yes", "method": "box", "res": "0.25"}})[0] == 200
        assert app.view == "simcompare_files"
        assert state(app)["simcompare"]["unify_form"]["res"] == "0.25"


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
            "out_dir": app.cwd, "out": "simulation_comparison.py", "replace_analysis": False}
        status, reply = post(app, "/api/analyze_run", {"form": {"out_dir": "", "out": ""}})
        assert status == 400 and set(reply["errors"]) == {"out"} and runs == []
        os.makedirs(os.path.join(app.cwd, "somewhere"))
        status, reply = post(app, "/api/analyze_run", {"form": {"out_dir": "", "out": "somewhere"}})
        assert status == 400 and set(reply["errors"]) == {"out"}
        # what was sent is kept for the page, and the script goes where it says
        assert state(app)["analyze"]["output"] == {"out_dir": "", "out": "somewhere", "replace_analysis": False}
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
        assert page.inner_text("#title") == "How should the comparison be run?"
        assert page.is_disabled("#ao-next")
        page.select_option("#ao-cores", "1")
        page.check("#ao-code-no")
        page.click("#ao-next")
        page.wait_for_selector("#ao-page-3:not([hidden])")
        # where the comparison is saved is asked last, for it, and has no tick to replace anything
        assert page.inner_text("#title") == "Where do you want the comparison to be saved?"
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
        assert page.locator("#view-home .choice").count() == 3
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
        assert page.inner_text("#title") == "What do you want to compare?"
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
