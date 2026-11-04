"""The oceanval window's landing page, and its Analyze steps (see
oceanval.analysis_app): the simulation, the variables, their files and the
gaps in them, the climatology and phenology pop-outs, and the run."""

import ast
import os
import shlex
import threading

import numpy as np
import pytest
import xarray as xr

from oceanval import analysis, analysis_examples, analysis_forms, model_check, transects
from oceanval.app import App
from simulations import write_analysis_simulation
from test_app import get, get_json, leftover_dir, post, wait_for, write_mixed_matchups  # noqa: F401 (leftover_dir is autouse)


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
def sim(tmp_path):
    write_analysis_simulation(str(tmp_path / "sim"))
    return "sim"


def setup_form(app, simdir="sim", **changes):
    return dict(app.analyze_setup_form, simdir=simdir, ndown="1", **changes)


def to_variables(app, simdir="sim"):
    assert post(app, "/api/choose", {"action": "analyze"})[0] == 200
    assert post(app, "/api/setup", {"form": setup_form(app, simdir)}) == (200, {"ok": True})
    wait_for(lambda: app.analyze_found["status"] != "reading")
    return get_json(app, "/api/state")[1]["analyze"]


ROWS = [
    {"name": "Chlorophyll", "model_variable": "P1_Chl+P2_Chl"},
    {"name": "Temperature", "model_variable": "thetao"},
]


def to_files(app, rows=ROWS):
    to_variables(app)
    assert post(app, "/api/analyze_variables", {"rows": rows}) == (200, {"ok": True})
    wait_for(lambda: app.analyze_check is not None and app.analyze_check.status != "running")
    return get_json(app, "/api/state")[1]["analyze"]


def to_options(app):
    to_files(app)
    assert post(app, "/api/analyze_files") == (200, {"ok": True})
    return get_json(app, "/api/state")[1]["analyze"]


CLIMATOLOGY = {
    "rows": {
        "Chlorophyll": {"use": True, "start": "2013", "end": "2014", "surface": True},
        "Temperature": {"use": True, "start": "2014", "end": "2014", "surface": False, "water_column": True, "how": "integral"},
    },
    "thickness": "e3t",
}


class TestLanding:
    def test_the_window_opens_on_analyze_or_validate(self, app):
        state = get_json(app, "/api/state")[1]
        assert state["view"] == "home"
        page = get(app, "/")[1]
        assert 'data-action="analyze"' in page and 'data-action="validate_menu"' in page

    def test_validate_opens_the_validation_page_and_back_returns(self, app):
        # the validation page's choices are not taken from the landing page
        assert post(app, "/api/choose", {"action": "matchup"})[0] == 409
        assert post(app, "/api/choose", {"action": "validate_menu"})[0] == 200
        assert app.view == "start"
        assert post(app, "/api/choose", {"action": "matchup"})[0] == 200
        assert app.view == "setup"
        assert post(app, "/api/back")[0] == 200
        assert app.view == "start"
        assert post(app, "/api/back")[0] == 200
        assert app.view == "home"

    def test_start_again_returns_to_the_landing_page(self, app):
        app.view = "finished"
        app.action = "analyze"
        assert post(app, "/api/restart_all")[0] == 200
        assert (app.view, app.action) == ("home", None)


class TestRestart:
    """Restart, beside Info: from any step back to the first page, with a clean slate."""

    def test_it_forgets_an_analysis_from_the_options_step(self, app, sim):
        from oceanval import user_cache

        user_cache.record_sim_dir(os.path.join(app.cwd, sim))
        before = app.version
        to_options(app)
        assert app.analyze_placed and app.analyze_setup
        settings, asked = dict(app.settings), app.leftovers_asked
        assert post(app, "/api/restart_all") == (200, {"ok": True})
        assert app.view == "home" and app.action is None
        assert app.version > before
        assert app.analyze_setup is None and app.analyze_placed is None and app.analyze_found is None
        assert app.analyze_check is None and app.analyze_rows is None
        assert app.analyze_setup_form == analysis_forms.default_setup_form(app.cwd)
        # what is the user's, beyond this session, is kept
        assert app.settings == settings and app.leftovers_asked == asked
        assert user_cache.recent_sim_dirs() == [os.path.join(app.cwd, sim)]
        # and the analysis can be started again, from nothing
        state = to_variables(app)
        assert state["rows"] is None or all(not row.get("model_variable") for row in state["rows"])

    def test_it_works_from_the_validation_steps_and_forgets_their_boxes(self, app):
        assert post(app, "/api/choose", {"action": "validate_menu"})[0] == 200
        assert post(app, "/api/choose", {"action": "matchup"})[0] == 200
        app.setup_form = dict(app.setup_form, simdir="somewhere")
        app.own_data["point"].append({"name": "mine"})
        assert post(app, "/api/restart_all")[0] == 200
        assert app.view == "home" and app.action is None
        assert app.setup_form["simdir"] != "somewhere"
        assert app.own_data == {"point": [], "gridded": []}

    def test_it_works_from_compare_simulations(self, app):
        assert post(app, "/api/choose", {"action": "simcompare"})[0] == 200
        assert app.view == "simcompare_sims"
        assert post(app, "/api/restart_all")[0] == 200
        assert app.view == "home" and app.action is None
        assert app.sc_sims is None

    def test_a_run_in_progress_is_stopped(self, app):
        class Run:
            args = ["analyze", "x"]
            stopped_by_reset = False

            def stop(self):
                self.stopped_by_reset = True

        run = Run()
        app.run, app.view, app.status, app.action = run, "running", "running", "analyze"
        assert post(app, "/api/restart_all")[0] == 200
        assert run.stopped_by_reset
        assert app.run is None and app.status is None and app.view == "home"
        # its end, when it comes, is not noticed
        app._run_finished(run)
        assert app.view == "home"

    def test_the_recipes_window_is_cancelled_and_its_thread_leaves_the_view_alone(self, app):
        class Page:
            cancelled = finished = False

            def cancel(self):
                self.cancelled = True

            def finish(self):
                self.finished = True

        page = Page()
        app.recipes_page, app.view = page, "recipes"
        # a thread of the recipes step, begun before the restart
        number = app._reset_count
        assert post(app, "/api/restart_all")[0] == 200
        assert page.cancelled and page.finished and app.recipes_page is None
        app._went_wrong(ValueError("late"), number)
        assert app.view == "home" and app.setup_error is None

    def test_stale_reads_of_the_simulation_are_dropped(self, app):
        before = app._analyze_reading
        assert post(app, "/api/restart_all")[0] == 200
        assert app._analyze_reading > before


class TestSimulation:
    def test_an_analysis_needs_no_domain_or_years(self, app, sim):
        state = to_variables(app)
        assert app.view == "analyze_variables"
        assert app.analyze_setup["simdir"].endswith("sim")
        assert state["found"]["status"] == "done"
        assert state["found"]["streams"] == 2
        # nothing is chosen for the user: the rows start empty
        assert state["rows"] == []
        assert state["picker"]["available"] == ["P1_Chl", "P2_Chl", "e3t", "thetao"]
        # the names the drop-down of "Environmental variable" offers, with the model variable of each
        presets = {preset["name"]: preset["model_variable"] for preset in state["presets"]}
        assert presets["Temperature"] == "thetao" and presets["Chlorophyll"] == "P1_Chl+P2_Chl"
        assert presets["Salinity"] is None

    def test_the_setup_page_is_its_own(self, app, sim):
        assert post(app, "/api/choose", {"action": "analyze"})[0] == 200
        state = get_json(app, "/api/state")[1]
        assert state["setup"]["form"]["out"] == "analysis.py"
        assert state["setup"]["form"]["domain"] == ""
        status, reply = post(app, "/api/setup", {"form": setup_form(app, "nowhere")})
        assert status == 400 and set(reply["errors"]) == {"simdir"}
        # the output is asked last, so an analysis already there is not a matter of this step
        os.makedirs(os.path.join(app.cwd, analysis.FOLDER))
        assert get_json(app, "/api/probe", simdir="sim", ndown="1", out="", out_dir="")[1]["out_dir_has_analysis"]
        assert post(app, "/api/setup", {"form": setup_form(app, out_dir="")}) == (200, {"ok": True})
        wait_for(lambda: app.analyze_found["status"] != "reading")

    def test_matchups_in_the_output_directory_are_not_asked_about(self, app, sim, tmp_path):
        # the output directory is the directory worked in, which the page does not ask
        # about the matchups of for an analysis
        write_mixed_matchups(tmp_path)
        to_variables(app)
        assert app.view == "analyze_variables"
        assert not {"overwrite", "previous"} & set(app.analyze_setup)

    def test_a_simulation_that_cannot_be_analysed(self, app, tmp_path):
        os.makedirs(tmp_path / "empty" / "2013")
        state = to_variables(app, "empty")
        assert state["found"]["status"] == "failed"
        assert "No netCDF files" in state["found"]["error"]
        assert post(app, "/api/analyze_variables", {"rows": ROWS})[0] == 409


class TestVariables:
    def test_the_rows_are_checked(self, app, sim):
        to_variables(app)
        status, reply = post(app, "/api/analyze_variables", {"rows": ROWS + [{"name": "Odd", "model_variable": "nope"}]})
        assert status == 400
        assert reply["errors"] == {"2": {"model_variable": "nope is not in the simulation's output."}}
        assert app.view == "analyze_variables"
        # what was sent is kept, to be shown again
        assert app.analyze_rows[2]["name"] == "Odd"

    def test_the_files_and_their_dates(self, app, sim):
        state = to_files(app)
        assert app.view == "analyze_files"
        assert state["time_shift"] == {"status": "none", "rows": [], "choice": None}
        assert [row["pattern"] for row in state["placed"]] == ["sim_1d_**_**_ptrc_T.nc", "sim_1d_**_**_grid_T.nc"]
        assert [row["time_res"] for row in state["placed"]] == ["1d", "1d"]
        assert [row["years"] for row in state["placed"]] == [[2013, 2014], [2013, 2014]]
        check = state["check"]
        assert check["status"] == "done"
        dates = check["rows"][0]["groups"][0]["dates"]
        assert dates["gaps"] is False and dates["first"] == "2013-01-01"
        status, reply = get_json(app, "/api/analyze_check_files", variable="Chlorophyll")
        assert reply["files"] == ["2013/sim_1d_20130101_20131231_ptrc_T.nc", "2014/sim_1d_20140101_20141231_ptrc_T.nc"]

    def test_a_change_of_simulation_keeps_your_rows(self, app, sim, tmp_path):
        to_variables(app)
        rows = [dict(row, filled=False) for row in ROWS[:1]] + [{"name": "Mine", "model_variable": "e3t"}]
        assert post(app, "/api/back", {"rows": rows})[0] == 200
        assert app.view == "setup"
        write_analysis_simulation(str(tmp_path / "other"), years=(2015,))
        assert post(app, "/api/setup", {"form": setup_form(app, "other")})[0] == 200
        wait_for(lambda: app.analyze_found["status"] != "reading")
        state = get_json(app, "/api/state")[1]["analyze"]
        assert [row["name"] for row in state["rows"]] == ["Chlorophyll", "Mine"]
        assert state["refilled"] is False

    def test_the_dates_are_checked_again_once_a_run_has_dropped_the_check(self, app, sim):
        """A run drops the check of the dates; starting again with the same
        variables must make a new one, not wait for one for ever."""
        to_files(app)
        with app._lock:
            app._drop_analyze_check()
        assert app.analyze_check is None
        assert post(app, "/api/back", {})[0] == 200
        assert post(app, "/api/back", {"rows": ROWS})[0] == 200
        assert post(app, "/api/setup", {"form": setup_form(app, "sim")})[0] == 200
        assert post(app, "/api/analyze_variables", {"rows": ROWS})[0] == 200
        wait_for(lambda: app.analyze_check is not None and app.analyze_check.status != "running")
        assert post(app, "/api/analyze_files")[0] == 200

    def test_a_change_of_simulation_checks_the_new_files_and_dates(self, app, sim, tmp_path):
        state = to_files(app)
        assert [row["years"] for row in state["placed"]] == [[2013, 2014], [2013, 2014]]
        first_check = app.analyze_check
        assert post(app, "/api/back", {})[0] == 200
        assert app.view == "analyze_variables"
        assert post(app, "/api/back", {"rows": ROWS})[0] == 200
        assert app.view == "setup"
        # the same simulation again keeps its check
        assert post(app, "/api/setup", {"form": setup_form(app, "sim")})[0] == 200
        assert post(app, "/api/analyze_variables", {"rows": ROWS})[0] == 200
        assert app.analyze_check is first_check
        assert post(app, "/api/back", {})[0] == 200
        assert post(app, "/api/back", {"rows": ROWS})[0] == 200
        # another, with the same variables and patterns
        write_analysis_simulation(str(tmp_path / "other"), years=(2015,))
        assert post(app, "/api/setup", {"form": setup_form(app, "other")})[0] == 200
        wait_for(lambda: app.analyze_found["status"] != "reading")
        assert post(app, "/api/analyze_variables", {"rows": ROWS})[0] == 200
        wait_for(lambda: app.analyze_check is not None and app.analyze_check.status != "running")
        state = get_json(app, "/api/state")[1]["analyze"]
        assert app.analyze_check is not first_check
        assert [row["years"] for row in state["placed"]] == [[2015, 2015], [2015, 2015]]
        assert state["check"]["rows"][0]["groups"][0]["dates"]["first"] == "2015-01-01"
        status, reply = get_json(app, "/api/analyze_check_files", variable="Chlorophyll")
        assert reply["files"] == ["2015/sim_1d_20150101_20151231_ptrc_T.nc"]


class TestTimeShift:
    def test_times_at_the_end_of_each_mean_are_asked_about_first(self, app, tmp_path):
        for year in (2013, 2014):
            os.makedirs(tmp_path / "getm" / str(year))
            times = [np.datetime64(f"{year + month // 12}-{month % 12 + 1:02d}-01") for month in range(1, 13)]
            xr.Dataset(
                {"temp": (("time", "lat", "lon"), np.ones((12, 2, 2)), {"units": "degC", "cell_methods": "time: mean"})},
                coords={"time": times, "lat": [50.0, 51.0], "lon": [0.0, 1.0]},
            ).to_netcdf(tmp_path / "getm" / str(year) / f"getm_{year}.nc")
        to_variables(app, "getm")
        assert post(app, "/api/analyze_variables", {"rows": [{"name": "Temperature", "model_variable": "temp"}]})[0] == 200
        wait_for(lambda: app.analyze_shift["status"] != "checking")
        assert app.analyze_shift["status"] == "asked"
        # the dates are only read once it is answered, as they are shifted
        assert app.analyze_check is None
        assert post(app, "/api/analyze_files")[0] == 409
        assert post(app, "/api/analyze_time_shift", {"choice": "maybe"})[0] == 409
        assert post(app, "/api/analyze_time_shift", {"choice": "shift"})[0] == 200
        wait_for(lambda: app.analyze_check.status != "running")
        dates = app.analyze_check.state()["rows"][0]["groups"][0]["dates"]
        assert (dates["first"], dates["last"]) == ("2013-01-31", "2014-12-31")
        assert post(app, "/api/analyze_files")[0] == 200
        assert app._shift_days() == -1


class TestOptions:
    def test_the_pop_outs_start_with_nothing_ticked(self, app, sim):
        state = to_options(app)
        assert app.view == "analyze_options"
        for kind in ("climatology", "long_term", "seasonal"):
            assert state["forms"][kind]["rows"]["Temperature"] == {
                "use": False, "start": "", "end": "", "surface": False, "bottom": False, "water_column": False, "how": "",
                "window": "1", "show": "absolute",
            }
        assert state["forms"]["phenology"]["rows"]["Chlorophyll"] == {"use": False, "start": "", "end": ""}
        assert state["forms"]["climatology"]["thickness"] == ""
        assert state["cores"] == "" and state["missing"] == {"from": "", "to": ""}
        # nor whether the report has code to reproduce its figures
        assert state["figure_code"] == "no"
        assert state["nemo_ersem"] is False
        assert state["chosen"] == {
            "climatology": None, "phenology": None, "long_term": None, "seasonal": None, "map_changes": None,
            "transects": None,
        }
        assert state["thickness_suggested"] == "e3t"
        assert state["placed"][0]["default_years"] == [2013, 2014]

    def test_an_ersem_simulation_is_assumed_to_have_zeros_as_missing(self, app, sim, monkeypatch):
        from oceanval import recipes_gui

        monkeypatch.setattr(recipes_gui, "is_likely_nemo_ersem", lambda variables: True)
        state = to_options(app)
        assert state["nemo_ersem"] is True
        assert state["missing"] == {"from": "0", "to": "0"}
        # what the user sets is kept, and a change of simulation does not undo it
        assert post(app, "/api/analyze_option", {"kind": "missing", "form": {"from": "", "to": ""}})[0] == 200
        assert app.analyze_missing == {"from": "", "to": ""}
        app._read_simulation()
        wait_for(lambda: app.analyze_found["status"] != "reading")
        assert app.analyze_missing == {"from": "", "to": ""}

    def test_the_missing_value_found_in_the_data_is_filled_in_to_be_checked(self, app, sim, monkeypatch):
        from oceanval import missing_probe

        looked = []
        monkeypatch.setattr(missing_probe, "probe", lambda files, name: looked.append((files, name)) or "1e+20")
        to_options(app)
        wait_for(lambda: app.analyze_missing_probed)
        state = get_json(app, "/api/state")[1]["analyze"]
        assert state["missing"] == {"from": "1e+20", "to": "1e+20"} and state["missing_probed"] is True
        # the first variable chosen, its first part
        assert len(looked) == 1 and looked[0][1] == "P1_Chl" and len(looked[0][0]) == 2
        # what it found is checked as anything typed would be, and typing over it makes it the user's
        assert analysis_forms.check_missing("1e+20", "1e+20")[1] == {}
        assert post(app, "/api/analyze_option", {"kind": "missing", "form": {"from": "0", "to": "0"}})[0] == 200
        state = get_json(app, "/api/state")[1]["analyze"]
        assert state["missing"] == {"from": "0", "to": "0"} and state["missing_probed"] is False

    def test_a_value_typed_before_the_data_is_looked_at_is_not_overwritten(self, app, sim, monkeypatch):
        import threading
        import time

        from oceanval import missing_probe

        release = threading.Event()
        monkeypatch.setattr(missing_probe, "probe", lambda files, name: release.wait(10) and "1e+20")
        to_options(app)
        assert post(app, "/api/analyze_option", {"kind": "missing", "form": {"from": "-9", "to": "-9"}})[0] == 200
        release.set()
        time.sleep(0.5)
        assert app.analyze_missing == {"from": "-9", "to": "-9"} and app.analyze_missing_probed is False

    def test_no_value_found_leaves_the_boxes_as_they_were(self, app, sim, monkeypatch):
        from oceanval import missing_probe

        monkeypatch.setattr(missing_probe, "probe", lambda files, name: None)
        state = to_options(app)
        assert state["missing"] == {"from": "", "to": ""} and state["missing_probed"] is False

    def test_the_long_term_series_have_a_rolling_mean_and_a_way_to_show_it(self, app, sim):
        to_options(app)
        rows = {"Chlorophyll": {"use": True, "start": "2013", "end": "2014", "surface": True}}
        # one year and no change is as it was: neither is given to analyze()
        form = {"rows": rows, "thickness": ""}
        assert post(app, "/api/analyze_option", {"kind": "long_term", "form": form}) == (200, {"ok": True})
        assert app.analyze_chosen["long_term"]["rows"]["Chlorophyll"] == {
            "start": 2013, "end": 2014, "surface": True, "bottom": False, "water_column": None,
        }
        rows["Chlorophyll"].update(window="1", show="absolute_change")
        assert post(app, "/api/analyze_option", {"kind": "long_term", "form": form}) == (200, {"ok": True})
        chosen = app.analyze_chosen["long_term"]["rows"]["Chlorophyll"]
        assert chosen["show"] == "absolute_change" and "window" not in chosen
        assert app.analyze_forms["long_term"]["rows"]["Chlorophyll"]["show"] == "absolute_change"
        rows["Chlorophyll"].update(window="2", show="absolute")
        assert post(app, "/api/analyze_option", {"kind": "long_term", "form": form}) == (200, {"ok": True})
        chosen = app.analyze_chosen["long_term"]["rows"]["Chlorophyll"]
        assert chosen["window"] == 2 and "show" not in chosen
        # they are the long-term series' alone
        assert post(app, "/api/analyze_option", {"kind": "seasonal", "form": form}) == (200, {"ok": True})
        assert "window" not in app.analyze_chosen["seasonal"]["rows"]["Chlorophyll"]
        # what cannot be used is said by the variable
        for window, show, box in [("0", "absolute", "window"), ("x", "absolute", "window"), ("3", "absolute", "window"),
                                  ("2", "percentage_change", "show")]:
            rows["Chlorophyll"].update(window=window, show=show)
            status, reply = post(app, "/api/analyze_option", {"kind": "long_term", "form": form})
            assert status == 400 and set(reply["errors"]["Chlorophyll"]) == {box}, (window, show)

    def test_the_time_series_hold_the_regions_and_area(self, app, sim):
        to_options(app)
        area = {"subregions": "global", "lon_min": "-5", "lon_max": "0", "lat_min": "49", "lat_max": "55"}
        form = dict(CLIMATOLOGY, area=area, stations=[["L4", "-4.2", "50.2"], ["", "", ""]])
        assert post(app, "/api/analyze_option", {"kind": "long_term", "form": form}) == (200, {"ok": True})
        assert app.analyze_area == dict(area, subregions_file="", stations=[["L4", "-4.2", "50.2"]])
        assert get_json(app, "/api/state")[1]["analyze"]["area"]["subregions"] == "global"
        assert app.analyze_chosen["long_term"]["rows"]["Chlorophyll"]["start"] == 2013
        # a bad box or file is said, beside the area, and nothing is kept
        bad = dict(form, area=dict(area, lon_max="", subregions="file", subregions_file="nowhere.nc"))
        status, reply = post(app, "/api/analyze_option", {"kind": "seasonal", "form": bad})
        assert status == 400
        assert set(reply["errors"]["area"]) == {"lon_max", "subregions_file"}
        assert app.analyze_chosen["seasonal"] is None and app.analyze_area["subregions"] == "global"
        # the climatology has no area
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": dict(CLIMATOLOGY, area=bad["area"])})[0] == 200
        assert app.analyze_area["subregions"] == "global"
        # and it is run with the time series
        assert post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "1"}})[0] == 200

    def test_confirm_checks_and_keeps(self, app, sim):
        to_options(app)
        bad = dict(CLIMATOLOGY, thickness="")
        status, reply = post(app, "/api/analyze_option", {"kind": "climatology", "form": bad})
        assert status == 400 and set(reply["errors"]) == {"thickness"}
        assert app.analyze_chosen["climatology"] is None
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY})[0] == 200
        assert app.analyze_chosen["climatology"] == {
            "rows": {
                "Chlorophyll": {"start": 2013, "end": 2014, "surface": True, "bottom": False, "water_column": None},
                "Temperature": {"start": 2014, "end": 2014, "surface": False, "bottom": False, "water_column": "integral"},
            },
            "thickness": "e3t",
        }
        # nothing ticked cannot be confirmed, whatever was chosen before
        none = {"rows": {name: {"use": False} for name in ("Chlorophyll", "Temperature")}}
        status, reply = post(app, "/api/analyze_option", {"kind": "phenology", "form": none})
        assert status == 400 and "rows" in reply["errors"]
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": dict(CLIMATOLOGY, rows=none["rows"])})[0] == 400
        assert app.analyze_chosen["climatology"] is not None

    def test_a_chosen_analysis_is_removed_with_its_boxes_kept(self, app, sim):
        to_options(app)
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY})[0] == 200
        assert app.analyze_chosen["climatology"] is not None
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": {"remove": True}}) == (200, {"ok": True})
        assert app.analyze_chosen["climatology"] is None
        assert app.analyze_forms["climatology"]["rows"]["Chlorophyll"]["use"] is True

    def test_back_keeps_every_step(self, app, sim):
        to_options(app)
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY})[0] == 200
        assert post(app, "/api/analyze_option", {"kind": "figure_code", "form": {"choice": "no"}})[0] == 200
        check = app.analyze_check
        for view in ("analyze_files", "analyze_variables", "setup"):
            assert post(app, "/api/back", {"rows": ROWS} if view == "setup" else {})[0] == 200
            assert app.view == view
        assert post(app, "/api/setup", {"form": setup_form(app)})[0] == 200
        # the same simulation is not read again
        assert app.analyze_found["status"] == "done"
        assert [row["name"] for row in get_json(app, "/api/state")[1]["analyze"]["rows"]] == ["Chlorophyll", "Temperature"]
        assert post(app, "/api/analyze_variables", {"rows": ROWS})[0] == 200
        # nor are the dates of the same files
        assert app.analyze_check is check
        assert post(app, "/api/analyze_files")[0] == 200
        assert app.analyze_forms["climatology"]["rows"]["Temperature"]["how"] == "integral"
        assert app.analyze_chosen["climatology"]["rows"]["Temperature"]["water_column"] == "integral"
        assert get_json(app, "/api/state")[1]["analyze"]["figure_code"] == "no"

    def test_a_variable_taken_away_is_left_out(self, app, sim):
        to_options(app)
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY})[0] == 200
        assert post(app, "/api/back")[0] == 200
        assert post(app, "/api/back")[0] == 200
        assert post(app, "/api/analyze_variables", {"rows": ROWS[:1]})[0] == 200
        wait_for(lambda: app.analyze_check is not None and app.analyze_check.status != "running")
        assert post(app, "/api/analyze_files")[0] == 200
        assert list(app.analyze_chosen["climatology"]["rows"]) == ["Chlorophyll"]

    def test_an_analysis_in_the_output_directory_has_to_be_ticked_to_be_replaced(self, app, sim, runs):
        to_options(app)
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY})[0] == 200
        assert post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "2"}})[0] == 200
        assert post(app, "/api/analyze_option", {"kind": "missing", "form": {"from": "0", "to": "0"}})[0] == 200
        assert post(app, "/api/analyze_option", {"kind": "figure_code", "form": {"choice": "no"}})[0] == 200
        assert get_json(app, "/api/state")[1]["analyze"]["output"] == {
            "out_dir": app.cwd, "out": "analysis.py", "out_typed": False, "replace_analysis": False}
        os.makedirs(os.path.join(app.cwd, analysis.FOLDER))
        # which the user must tick to replace, having named the directory
        status, reply = post(app, "/api/analyze_run", {"form": {"out_dir": app.cwd, "out": "analysis.py"}})
        assert status == 400 and reply["errors"] == {"replace_analysis": ""} and runs == []
        status, reply = post(app, "/api/analyze_run", {"form": {"out_dir": "", "out": "analysis.py", "replace_analysis": True}})
        assert status == 400 and reply["errors"] == {"out_dir": analysis_forms.NAME_THE_DIRECTORY}
        # nor is a script name left empty, or a directory
        status, reply = post(app, "/api/analyze_run", {"form": {"out_dir": app.cwd, "out": "", "replace_analysis": True}})
        assert status == 400 and set(reply["errors"]) == {"out"}
        assert post(app, "/api/analyze_save_script", {"form": {"out_dir": app.cwd, "out": "mine.py", "replace_analysis": True}}) == (200, {"ok": True})
        assert os.path.exists(os.path.join(app.cwd, "mine.py"))

    def test_the_run_writes_the_script_and_runs_it(self, app, sim, runs):
        to_options(app)
        assert post(app, "/api/analyze_run")[0] == 400
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY})[0] == 200
        # there is no default number of cores
        status, reply = post(app, "/api/analyze_run")
        assert status == 400 and reply["errors"] == {"cores": "Say how many cores OceanVal may use."}
        assert post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "0"}})[0] == 400
        assert post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "2"}}) == (200, {"ok": True})
        # and no more than the machine has
        too_many = str(os.cpu_count() + 1)
        status, reply = post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": too_many}})
        assert status == 400 and f"This machine has {os.cpu_count()} cores" in reply["errors"]["cores"]
        assert post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "2"}}) == (200, {"ok": True})
        assert post(app, "/api/analyze_option", {"kind": "missing", "form": {"from": "x", "to": ""}})[0] == 400
        assert post(app, "/api/analyze_run")[0] == 400
        assert post(app, "/api/analyze_option", {"kind": "missing", "form": {"from": "0", "to": "0"}})[0] == 200
        # whether the report has code to reproduce its figures is not required: it starts at "no"
        assert get_json(app, "/api/state")[1]["analyze"]["figure_code"] == "no"
        assert post(app, "/api/analyze_option", {"kind": "figure_code", "form": {"choice": "maybe"}})[0] == 400
        assert post(app, "/api/analyze_option", {"kind": "figure_code", "form": {"choice": "yes"}}) == (200, {"ok": True})
        assert get_json(app, "/api/state")[1]["analyze"]["figure_code"] == "yes"
        assert post(app, "/api/analyze_run") == (200, {"ok": True})
        script = os.path.join(app.cwd, "analysis.py")
        assert runs == [(["analyze", script], "python analysis.py")]
        assert app.results_dir == app.cwd
        tree = ast.parse(open(script).read())
        call = tree.body[-1].value
        assert ast.unparse(call.func) == "oceanval.analyze"
        given = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
        assert given["variables"] == {"Chlorophyll": "P1_Chl+P2_Chl", "Temperature": "thetao"}
        assert given["climatology"]["Temperature"]["water_column"] == "integral"
        assert given["phenology"] is None
        assert given["thickness"] == "e3t"
        assert given["n_dirs_down"] == 1
        assert given["cores"] == 2 and given["as_missing"] == [0.0, 0.0]
        assert given["figure_code"] is True

    def test_the_times_the_check_read_are_kept_for_the_run(self, app, tmp_path):
        index = tmp_path / "times.pkl"
        index.write_bytes(b"x")

        class Check:
            finished = True

        Check.index = str(index)
        app.analyze_check = Check()
        kept = app._analyze_keep_times()
        assert kept != str(index) and open(kept, "rb").read() == b"x"
        # a check not finished, or with nothing saved, keeps nothing
        Check.finished = False
        assert app._analyze_keep_times() is None and not os.path.exists(kept)
        app.analyze_check = None
        assert app._analyze_keep_times() is None
        assert "times_index" not in analysis_forms.arguments(
            {"simdir": "d", "ndown": 1, "out_dir": ".", "out": "a.py"}, [], None, None, None, None
        )

    def test_the_script_can_be_saved_to_run_later(self, app, sim, runs):
        to_options(app)
        # the same things are needed as to run it
        assert post(app, "/api/analyze_save_script")[0] == 400
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY})[0] == 200
        status, reply = post(app, "/api/analyze_save_script")
        assert status == 400 and reply["errors"] == {"cores": "Say how many cores OceanVal may use."}
        assert post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "2"}})[0] == 200
        assert post(app, "/api/analyze_option", {"kind": "missing", "form": {"from": "0", "to": "0"}})[0] == 200
        # not answering whether the report has figure code is "no"
        assert app.view == "analyze_options" and not os.path.exists(os.path.join(app.cwd, "analysis.py"))
        assert post(app, "/api/analyze_save_script") == (200, {"ok": True})
        # the script is written, and nothing is run
        script = os.path.join(app.cwd, "analysis.py")
        assert runs == []
        assert app.view == "script_saved"
        run = get_json(app, "/api/state")[1]["run"]
        assert run["script_saved"] is True and run["script"] == script
        assert run["script_command"] == f"python {shlex.quote(script)}"
        call = ast.parse(open(script).read()).body[-1].value
        assert ast.unparse(call.func) == "oceanval.analyze"
        given = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
        assert given["cores"] == 2 and given["figure_code"] is False
        # it is over
        assert post(app, "/api/analyze_save_script")[0] == 409
        assert post(app, "/api/analyze_run")[0] == 409
        # and Start again goes back to the first page
        assert post(app, "/api/restart_all")[0] == 200
        assert app.view == "home"
        assert get_json(app, "/api/state")[1]["run"]["script_saved"] is False


# the map changes pop-out's boxes: the historical year against the one future year the simulation has
CHANGES = {
    "rows": {
        "Temperature": {
            "use": True, "historical_start": "2013", "historical_end": "2013", "future1_start": "2014", "future1_end": "2014",
            "future2_start": "", "future2_end": "", "plot": "absolute_changes", "change": "percentage",
            "surface": True, "bottom": False, "water_column": True, "how": "mean",
        },
    },
    "thickness": "e3t",
}


def changes_form(**boxes):
    return {"rows": {"Temperature": dict(CHANGES["rows"]["Temperature"], **boxes)}, "thickness": "e3t"}


class TestMapChanges:
    def test_the_pop_out_starts_with_every_variable_ticked_and_nothing_else(self, app, sim):
        state = to_options(app)
        assert state["forms"]["map_changes"]["rows"]["Temperature"] == {
            "use": False, "plot": "", "change": "", "surface": False, "bottom": False, "water_column": False, "how": "",
            **{f"{period}_{end}": "" for period in ("historical", "future1", "future2") for end in ("start", "end")},
        }
        assert state["forms"]["map_changes"]["thickness"] == ""
        assert state["chosen"]["map_changes"] is None
        # the tile is served just left of Transects
        text = get(app, "/")[1]
        assert text.index('id="ao-map_changes"') < text.index('id="ao-transects"')
        assert 'id="chg-dialog"' in text

    def test_confirm_checks_and_keeps(self, app, sim):
        to_options(app)
        status, reply = post(app, "/api/analyze_option", {"kind": "map_changes", "form": CHANGES})
        assert (status, reply) == (200, {"ok": True})
        assert app.analyze_chosen["map_changes"] == {
            "rows": {
                "Temperature": {
                    "historical": {"start": 2013, "end": 2013},
                    "future": [{"start": 2014, "end": 2014}],
                    "plot": "absolute_changes",
                    "change": "percentage",
                    "surface": True,
                    "bottom": False,
                    "water_column": "mean",
                }
            },
            "thickness": "e3t",
        }
        assert app.analyze_forms["map_changes"]["rows"]["Temperature"]["plot"] == "absolute_changes"

    def test_what_is_wrong_is_said_by_box(self, app, sim):
        to_options(app)

        def errors_of(**boxes):
            status, reply = post(app, "/api/analyze_option", {"kind": "map_changes", "form": changes_form(**boxes)})
            assert status == 400
            return reply["errors"]

        # the historical and first future periods are required, with both their years
        found = errors_of(historical_start="", future1_end="x")["Temperature"]
        assert set(found) == {"historical_start", "future1_end"}
        assert found["historical_start"].startswith("Historical: ")
        assert found["future1_end"].startswith("Future 1: ")
        # a second future period is both years or neither
        assert set(errors_of(future2_start="2014")["Temperature"]) == {"future2_end"}
        # years the output does not have
        assert errors_of(future1_start="2020", future1_end="2021")["Temperature"]["future1_start"] == (
            "Future 1: The output is from 2013 to 2014."
        )
        assert errors_of(future1_end="2015")["Temperature"]["future1_end"] == "Future 1: The output ends in 2014."
        # a future period that is the historical one is no change
        assert "same years" in errors_of(future1_start="2013", future1_end="2013")["Temperature"]["future1_start"]
        # what to plot and the kind of change are required
        assert set(errors_of(plot="", change="")["Temperature"]) == {"plot", "change"}
        assert set(errors_of(plot="everything")["Temperature"]) == {"plot"}
        # a layer, and the thickness for the water column
        assert set(errors_of(surface=False, water_column=False)["Temperature"]) == {"surface"}
        assert set(errors_of(how="")["Temperature"]) == {"water_column"}
        status, reply = post(app, "/api/analyze_option", {"kind": "map_changes", "form": dict(changes_form(), thickness="")})
        assert status == 400 and set(reply["errors"]) == {"thickness"}
        # nothing ticked
        none = {"rows": {"Temperature": dict(CHANGES["rows"]["Temperature"], use=False)}, "thickness": ""}
        status, reply = post(app, "/api/analyze_option", {"kind": "map_changes", "form": none})
        assert status == 400 and "rows" in reply["errors"]
        assert app.analyze_chosen["map_changes"] is None

    def test_a_second_future_period_and_removal(self, app, sim):
        to_options(app)
        # the output has two years, so a second future period has to be the historical year or the first future one:
        # the same as another is refused, and one that is not is a period of its own
        form = changes_form(future2_start="2013", future2_end="2014", plot="changes", change="absolute", water_column=False)
        status, reply = post(app, "/api/analyze_option", {"kind": "map_changes", "form": form})
        assert (status, reply) == (200, {"ok": True})
        assert [x["start"] for x in app.analyze_chosen["map_changes"]["rows"]["Temperature"]["future"]] == [2014, 2013]
        assert app.analyze_chosen["map_changes"]["thickness"] is None
        assert post(app, "/api/analyze_option", {"kind": "map_changes", "form": {"remove": True}}) == (200, {"ok": True})
        assert app.analyze_chosen["map_changes"] is None
        assert app.analyze_forms["map_changes"]["rows"]["Temperature"]["future2_start"] == "2013"

    def test_the_run_writes_map_changes_into_the_script(self, app, sim, runs):
        to_options(app)
        assert post(app, "/api/analyze_option", {"kind": "map_changes", "form": CHANGES})[0] == 200
        for kind, form in (("cores", {"cores": "1"}), ("figure_code", {"choice": "no"})):
            assert post(app, "/api/analyze_option", {"kind": kind, "form": form})[0] == 200
        assert post(app, "/api/analyze_run") == (200, {"ok": True})
        tree = ast.parse(open(os.path.join(app.cwd, "analysis.py")).read())
        given = {keyword.arg: ast.literal_eval(keyword.value) for keyword in tree.body[-1].value.keywords}
        assert given["map_changes"] == app.analyze_chosen["map_changes"]["rows"]
        assert given["variables"] == {"Temperature": "thetao"}
        assert given["thickness"] == "e3t"
        assert given["climatology"] is None

    def test_the_thickness_is_the_same_in_all_the_analyses(self, app, sim, runs):
        to_options(app)
        assert post(app, "/api/analyze_option", {"kind": "map_changes", "form": CHANGES})[0] == 200
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": dict(CLIMATOLOGY, thickness="z_level")})[0] == 200
        for kind, form in (("cores", {"cores": "1"}), ("figure_code", {"choice": "no"})):
            assert post(app, "/api/analyze_option", {"kind": kind, "form": form})[0] == 200
        status, reply = post(app, "/api/analyze_run")
        assert status == 400 and "cell thickness is not the same" in reply["error"]


needs_to_transect = pytest.mark.skipif(not transects.available(), reason="nctoolkit cannot extract transects")

# a transect's boxes: its ends and its surface climatology
TRANSECT = {
    "start_lon": "-2", "start_lat": "50", "end_lon": "-2", "end_lat": "53",
    "climatology": True, "climatology_start": "2013", "climatology_end": "2014",
    "vertical": False, "vertical_start": "", "vertical_end": "", "trends": "", "trends_start": "", "trends_end": "",
    "trends_window": "1",
}
EAST_WEST = dict(
    TRANSECT, start_lon="-4", start_lat="51", end_lon="0", end_lat="51", climatology=False, climatology_start="", climatology_end="",
    vertical=True, vertical_start="2013", vertical_end="2014", trends="absolute_change", trends_start="2013", trends_end="2014",
)


def transects_form(**variables):
    """The pop-out's boxes: the transects of each variable named, which ticks it."""
    return {"rows": {name: {"use": True, "transects": boxes} for name, boxes in variables.items()}}


class TestTransects:
    def test_the_pop_out_starts_with_every_variable_ticked_and_nothing_else(self, app, sim):
        state = to_options(app)
        blank = {
            "start_lon": "", "start_lat": "", "end_lon": "", "end_lat": "",
            "climatology": False, "climatology_start": "", "climatology_end": "",
            "vertical": False, "vertical_start": "", "vertical_end": "",
            "trends": "", "trends_start": "", "trends_end": "", "trends_window": "1",
            "vertical_trends": "", "vertical_trends_start": "", "vertical_trends_end": "", "vertical_trends_window": "1",
        }
        # each variable has transects of its own
        assert state["forms"]["transects"] == {
            "rows": {name: {"use": False, "transects": [blank]} for name in ("Chlorophyll", "Temperature")},
            "thickness": "",
        }
        assert state["chosen"]["transects"] is None
        assert 'id="ao-transects"' in get(app, "/")[1] and 'id="trans-dialog"' in get(app, "/")[1]

    @needs_to_transect
    def test_confirm_checks_and_keeps(self, app, sim):
        to_options(app)
        # nothing ticked, or no transect for what is, cannot be confirmed
        status, reply = post(app, "/api/analyze_option", {"kind": "transects", "form": {"rows": {}}})
        assert status == 400 and "Tick at least one variable" in reply["errors"]["rows"]
        status, reply = post(app, "/api/analyze_option", {"kind": "transects", "form": transects_form(Temperature=[{}])})
        assert status == 400 and "Add a transect for this variable" in reply["errors"]["Temperature"]["use"]
        # a transect has to follow the rule and say what to work out, and is said by its number for its variable
        diagonal = dict(TRANSECT, end_lon="3", climatology=False)
        status, reply = post(app, "/api/analyze_option", {"kind": "transects", "form": transects_form(Temperature=[TRANSECT, diagonal])})
        assert status == 400 and set(reply["errors"]) == {"Temperature"}
        found = reply["errors"]["Temperature"]["transects"]
        assert set(found) == {"1"} and {"start_lon", "end_lat", "options"} <= set(found["1"])
        assert app.analyze_chosen["transects"] is None
        # as many as are wanted, and different for each variable
        form = transects_form(Temperature=[TRANSECT, EAST_WEST], Chlorophyll=[dict(TRANSECT, end_lat="52")])
        # a vertical section needs to know whether the simulation is z-level, or where its thickness is
        status, reply = post(app, "/api/analyze_option", {"kind": "transects", "form": form})
        assert status == 400 and set(reply["errors"]) == {"thickness"}
        assert post(app, "/api/analyze_option", {"kind": "transects", "form": dict(form, thickness="e3t")}) == (200, {"ok": True})
        assert app.analyze_chosen["transects"]["thickness"] == "e3t"
        chosen = app.analyze_chosen["transects"]["rows"]
        assert [line["end"] for line in chosen["Temperature"]] == [[-2.0, 53.0], [0.0, 51.0]]
        assert chosen["Chlorophyll"][0]["end"] == [-2.0, 52.0]
        assert chosen["Temperature"][1] == {
            "start": [-4.0, 51.0], "end": [0.0, 51.0], "climatology": None, "vertical": {"start": 2013, "end": 2014},
            "trends": {"start": 2013, "end": 2014, "how": "absolute_change"}, "vertical_trends": None,
        }
        state = get_json(app, "/api/state")[1]["analyze"]
        assert state["forms"]["transects"]["rows"]["Temperature"]["transects"][1]["end_lon"] == "0"
        assert state["forms"]["transects"]["thickness"] == "e3t"
        # it is removed with its boxes kept
        assert post(app, "/api/analyze_option", {"kind": "transects", "form": {"remove": True}}) == (200, {"ok": True})
        assert app.analyze_chosen["transects"] is None
        assert len(app.analyze_forms["transects"]["rows"]["Temperature"]["transects"]) == 2

    @needs_to_transect
    def test_a_vertical_transect_needs_a_variable_with_levels(self, app, sim):
        to_options(app)
        form = dict(transects_form(Chlorophyll=[EAST_WEST]), thickness="e3t")
        status, reply = post(app, "/api/analyze_option", {"kind": "transects", "form": form})
        assert status == 400
        assert "vertical" in reply["errors"]["Chlorophyll"]["transects"]["0"]

    @needs_to_transect
    def test_back_keeps_them_and_a_variable_taken_away_is_left_out(self, app, sim):
        to_options(app)
        form = transects_form(Temperature=[TRANSECT], Chlorophyll=[TRANSECT])
        assert post(app, "/api/analyze_option", {"kind": "transects", "form": form})[0] == 200
        assert sorted(app.analyze_chosen["transects"]["rows"]) == ["Chlorophyll", "Temperature"]
        assert post(app, "/api/back")[0] == 200 and post(app, "/api/back")[0] == 200
        assert post(app, "/api/analyze_variables", {"rows": ROWS[1:]})[0] == 200
        wait_for(lambda: app.analyze_check is not None and app.analyze_check.status != "running")
        assert post(app, "/api/analyze_files")[0] == 200
        assert list(app.analyze_chosen["transects"]["rows"]) == ["Temperature"]
        assert app.analyze_forms["transects"]["rows"]["Temperature"]["transects"][0]["end_lat"] == "53"

    @needs_to_transect
    def test_the_run_writes_the_transects_into_the_script(self, app, sim, runs):
        to_options(app)
        form = dict(transects_form(Temperature=[TRANSECT, EAST_WEST]), thickness="z_level")
        assert post(app, "/api/analyze_option", {"kind": "transects", "form": form})[0] == 200
        for kind, answer in (("cores", {"cores": "1"}), ("figure_code", {"choice": "no"})):
            assert post(app, "/api/analyze_option", {"kind": kind, "form": answer})[0] == 200
        assert post(app, "/api/analyze_run") == (200, {"ok": True})
        text = open(os.path.join(app.cwd, "analysis.py")).read()
        call = ast.parse(text).body[-1].value
        given = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
        assert given["variables"] == {"Temperature": "thetao"}
        assert given["climatology"] is None and given["phenology"] is None and given["thickness"] == "z_level"
        assert given["transects"] == {
            "Temperature": [
                {"start": [-2.0, 50.0], "end": [-2.0, 53.0], "climatology": {"start": 2013, "end": 2014}},
                {
                    "start": [-4.0, 51.0], "end": [0.0, 51.0], "vertical": {"start": 2013, "end": 2014},
                    "trends": {"start": 2013, "end": 2014, "how": "absolute_change"},
                },
            ]
        }


def test_the_analysis_report_is_served(app, tmp_path):
    pages = tmp_path / analysis.FOLDER / analysis.REPORT / "_build" / "html" / "notebooks"
    os.makedirs(pages)
    (pages / "000_overview.html").write_text("<p>overview</p>")
    os.symlink(os.path.join(analysis.REPORT, "_build", "html", "notebooks", "000_overview.html"),
               tmp_path / analysis.FOLDER / analysis.REPORT_LINK)
    app.action = "analyze"
    app.results_dir = str(tmp_path)
    run = get_json(app, "/api/state")[1]["run"]
    assert run["report_exists"]
    assert run["report_href"].startswith(f"analysis/{app.token}/")
    status, body = get(app, "/" + run["report_href"], token=None)
    assert (status, body) == (200, "<p>overview</p>")
    assert get(app, f"/analysis/wrong/notebooks/000_overview.html")[0] == 403


class FakeRun:
    """A run that is started and runs until the test is over."""

    def __init__(self, args, *_, **__):
        self.args = args
        self.returncode = None

    def start(self):
        pass

    def running(self):
        return True

    def stop(self):
        pass

    def join(self, timeout=None):
        pass


@pytest.mark.parametrize("kind, folder, which, what", [
    ("analyze", analysis.FOLDER, "analysis", "analysis"),
    ("simcompare", "oceanval_simulation_comparison", "simcompare", "comparison"),
])
def test_the_report_built_a_variable_at_a_time_is_linked_as_it_goes(app, tmp_path, monkeypatch, kind, folder, which, what):
    """The page follows the report the run builds a variable at a time (see
    oceanval.analysis_report), as it does validate's interim report."""
    from oceanval import analysis_report, app as app_module

    monkeypatch.setattr(app_module, "Run", FakeRun)
    status = tmp_path / folder / analysis_report.STATUS
    pages = tmp_path / folder / analysis.REPORT / "_build" / "html" / "notebooks"
    pages.mkdir(parents=True)
    # an earlier run's
    status.write_text('{"state": "complete", "pages": 3, "expected": 3}')
    app.action = kind
    app.results_dir = str(tmp_path)
    app.start_run([kind, "script.py"], "python script.py")
    assert not status.exists()
    assert get_json(app, "/api/state")[1]["interim"] == {
        "state": "waiting", "pages": 0, "expected": None, "what": what, "href": None,
        # only a validation's matchups are in its grid
        "included": None, "building_key": None, "added": {}, "failed": [],
    }
    (pages / "000_overview.html").write_text("<p>overview</p>")
    status.write_text(
        '{"state": "building", "pages": 1, "expected": 2, "landing": "%s"}' % (pages / "000_overview.html")
    )
    wait_for(lambda: get_json(app, "/api/state")[1]["interim"]["pages"] == 1, timeout=10)
    interim = get_json(app, "/api/state")[1]["interim"]
    assert interim == {
        "state": "building", "pages": 1, "expected": 2, "what": what,
        "href": f"{which}/{app.token}/notebooks/000_overview.html",
        "included": None, "building_key": None, "added": {}, "failed": [],
    }
    assert get(app, "/" + interim["href"], token=None) == (200, "<p>overview</p>")


def test_the_dates_check_works_out_the_resolution(tmp_path):
    write_analysis_simulation(str(tmp_path / "sim"), daily=False)
    files = sorted(str(path) for path in (tmp_path / "sim").glob("*/*ptrc_T.nc"))
    events = []
    model_check.check(
        {
            "sim_dir": str(tmp_path / "sim"),
            "years": [2013, 2014],
            "rows": [{"variable": "chl", "pattern": "p", "time_res": None}],
            "files": {"p": files},
            "out_dir": str(tmp_path),
        },
        events.append,
    )
    found = next(event for event in events if event["event"] == "files")
    assert found["time_res"] == "monthly"


def test_analyze_in_a_browser(browser, tmp_path):
    """The landing page, the variables OceanVal fills in in bold,
    and a pop-out whose Cancel forgets what was changed and whose Confirm
    keeps it."""
    write_analysis_simulation(str(tmp_path / "sim"))
    app = App(cwd=str(tmp_path))
    url = app.start()
    errors = []
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-analyze")
        page.wait_for_selector("#view-setup:not([hidden])")
        # an analysis has no domain to choose
        assert page.is_hidden("#setup-domain")
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "1")
        page.wait_for_function("() => !document.querySelector('#continue').disabled")
        page.click("#continue")
        page.wait_for_selector("#av-main:not([hidden])", timeout=60000)
        assert page.inner_text("#av-lede") == "Choose the variables to analyze" and page.is_hidden("#title")
        # nothing is filled in for the user: there is one empty row
        assert page.locator("#av-rows > tr").count() == 1
        # a lone blank row has nothing to remove (grey ×), but its + adds one; with something in it, × blanks the table
        assert page.locator("#av-rows > tr").first.locator(".analyze-remove").is_disabled()
        assert page.locator("#av-rows > tr").first.locator(".analyze-add").is_enabled()
        page.locator("#av-rows > tr").first.locator("input").first.fill("X")
        assert page.locator("#av-rows > tr").first.locator(".analyze-remove").is_enabled()
        page.locator("#av-rows > tr").first.locator(".analyze-remove").click()
        assert page.locator("#av-rows > tr").count() == 1
        assert page.locator("#av-rows > tr").first.locator("input").first.input_value() == ""
        assert page.locator("#av-rows > tr").first.locator(".analyze-remove").is_disabled()
        first = page.locator("#av-rows > tr").first.locator("td").first.locator("input")
        model = page.locator("#av-rows > tr").first.locator("td").nth(1).locator("input").first
        assert first.input_value() == "" and model.input_value() == ""
        first.fill("Temperature")
        model.fill("thetao")
        page.click("#av-continue")
        page.wait_for_selector("#view-analyze-files:not([hidden])")
        page.wait_for_function("() => !document.querySelector('#af-continue').disabled", timeout=60000)
        page.click("#af-continue")
        page.wait_for_selector("#view-analyze-options:not([hidden])")
        # the tiles are the first page, with nothing to continue with until one is chosen
        assert page.is_disabled("#ao-continue") and page.is_hidden("#ao-page-2") and page.is_visible("#ao-climatology")
        # nothing is chosen, not even the number of cores, or whether the report has figure code
        assert page.input_value("#ao-cores") == "" and page.locator("#ao-cores option").all_text_contents() == [
            "Choose…", *[str(n) for n in range(1, os.cpu_count() + 1)]]
        assert page.input_value("#ao-missing_from") == "" and page.input_value("#ao-missing_from") == ""
        assert page.is_checked("#ao-code-no") and not page.is_checked("#ao-code-yes")
        # the regional subdomains are in the time series' pop-outs, not on this page
        assert page.locator("#area-subregions").count() == 1 and page.is_hidden("#area-subregions")

        # the first tick of a row has the variable in the analysis or not, and then come its layers
        use = page.locator("#clim-body > tr").first.locator("input[type=checkbox]").first
        surface = page.locator("#clim-body > tr").first.locator("input[type=checkbox]").nth(1)
        page.click("#ao-climatology")
        page.wait_for_selector("#clim-dialog:not([hidden])")
        # nothing to remove yet
        assert page.is_hidden("#clim-remove")
        assert page.locator("#clim-dialog .picker__foot button:visible").all_inner_texts() == ["Cancel", "Confirm"]
        # nothing is ticked to start with; one variable has no All variables row
        assert not use.is_checked() and not surface.is_checked()
        assert page.locator("#clim-all > tr").count() == 0
        assert "required" in page.inner_text("#clim-dialog thead").lower()
        assert page.locator("#clim-body input.is-year").first.get_attribute("placeholder") == "2013"
        # with the variable unticked there is nothing to confirm, and a layer ticked ticks it
        assert page.is_disabled("#clim-confirm") and "Tick at least one variable" in page.inner_text("#clim-msg")
        surface.check()
        assert use.is_checked() and page.is_enabled("#clim-confirm")
        surface.uncheck()
        # the bottom is a layer of its own, which needs no thickness
        bottom = page.locator("#clim-body > tr").first.locator("input[type=checkbox]").nth(2)
        assert not bottom.is_checked()
        assert "bottom" in page.inner_text("#clim-dialog thead").lower()
        bottom.check()
        assert page.is_enabled("#clim-confirm") and page.is_hidden("#clim-thickness-row")
        bottom.uncheck()
        surface.check()
        page.click("#clim-cancel")
        page.wait_for_selector("#clim-dialog", state="hidden")
        assert app.analyze_chosen["climatology"] is None
        page.click("#ao-climatology")
        assert not surface.is_checked()
        surface.check()
        page.locator("#clim-body input.is-year").nth(0).fill("2013")
        page.locator("#clim-body input.is-year").nth(1).fill("2014")
        page.click("#clim-confirm")
        page.wait_for_selector("#clim-dialog", state="hidden")
        wait_for(lambda: app.analyze_chosen["climatology"] is not None)
        assert list(app.analyze_chosen["climatology"]["rows"]) == ["Temperature"]
        page.wait_for_selector("#ao-climatology.is-chosen")
        # the cores and the rest are on a second page
        assert page.is_enabled("#ao-continue")
        page.click("#ao-continue")
        page.wait_for_selector("#ao-page-2:not([hidden])")
        assert page.is_hidden("#ao-page-1") and page.is_hidden("#ao-continue")
        assert page.is_hidden("#title")
        # the last page is not reached until the cores are given
        assert page.is_disabled("#ao-next") and page.locator("#ao-run").count() == 0
        # the cores are marked as required until they are given, the figure code is not
        assert "required" in page.inner_text("#ao-cores-group legend").lower()
        page.select_option("#ao-cores", "2")
        # an unanswered figure code question is no, so the cores are all that is needed
        assert page.is_enabled("#ao-next")
        page.check("#ao-code-yes")
        wait_for(lambda: app.analyze_figure_code == "yes")
        assert "required" not in page.inner_text("#ao-code-group legend").lower()
        # where the output is saved is the last page, which has the buttons that run the analysis
        assert page.is_enabled("#ao-next") and page.locator("#ao-run").count() == 0
        page.click("#ao-next")
        page.wait_for_selector("#ao-page-3:not([hidden])")
        assert page.is_hidden("#ao-page-2")
        assert page.is_hidden("#title")
        assert page.inner_text("#ao-save-lede") == "Choose where to save the analysis"
        assert page.input_value("#ao-out_dir") == app.cwd and page.input_value("#ao-out") == "analysis.py"
        assert page.is_enabled("#ao-run") and page.is_enabled("#ao-save")
        # the script can be saved to run later, from a button to the left of the one that runs it, which is on the right
        assert page.inner_text("#ao-save") == "Save Python script to run later"
        assert page.evaluate("() => document.querySelector('#ao-run').getBoundingClientRect().left"
                             " > document.querySelector('#ao-save').getBoundingClientRect().right")
        # a script that has no name cannot be written
        page.fill("#ao-out", "")
        assert page.is_disabled("#ao-run") and page.is_disabled("#ao-save")
        page.fill("#ao-out", "mine.py")
        assert page.is_enabled("#ao-run")
        # Back keeps what was typed
        page.click("#actions button:has-text('Back')")
        page.wait_for_selector("#ao-page-2:not([hidden])")
        page.click("#ao-next")
        assert page.input_value("#ao-out") == "mine.py"
        # a file that exists is warned of only when its name is typed
        assert page.locator("#ao-m-out .group__line.is-warn").count() == 0
        open(os.path.join(app.cwd, "taken.py"), "w").close()
        page.fill("#ao-out", "taken.py")
        page.wait_for_selector("#ao-m-out .group__line.is-warn")
        assert "already exists" in page.inner_text("#ao-m-out")
        page.fill("#ao-out", "mine.py")
        page.wait_for_function("document.querySelector('#ao-m-out .group__line.is-warn') === null")
        # Back goes to the tiles, which keep what was chosen
        page.click("#actions button:has-text('Back')")
        page.click("#actions button:has-text('Back')")
        page.wait_for_selector("#ao-page-1:not([hidden])")
        assert page.is_hidden("#ao-page-2") and page.locator("#ao-run").count() == 0
        assert page.is_enabled("#ao-continue") and page.locator("#ao-climatology.is-chosen").count() == 1
        page.click("#ao-climatology")
        assert surface.is_checked()
        page.keyboard.press("Escape")
        page.wait_for_selector("#clim-dialog", state="hidden")
        # the analysis can be removed again; the answers on the other page are kept
        assert page.is_checked("#ao-code-yes") and not page.is_checked("#ao-code-no")
        assert page.locator("#ao-need").count() == 0
        page.click("#ao-climatology")
        page.click("#clim-remove")
        page.wait_for_selector("#clim-dialog", state="hidden")
        wait_for(lambda: app.analyze_chosen["climatology"] is None)
        page.wait_for_selector("#ao-climatology:not(.is-chosen)")
        assert page.is_disabled("#ao-continue")
        assert errors == []
    finally:
        app.close()


def test_replacing_an_analysis_must_be_ticked_in_a_browser(browser, tmp_path):
    """An analysis in the output directory is replaced by the new one: the last page
    waits for a tick, which is forgotten when the directory is changed, and the
    directory must be named rather than left empty."""
    write_analysis_simulation(str(tmp_path / "sim"))
    os.makedirs(tmp_path / "old" / analysis.FOLDER)
    app = App(cwd=str(tmp_path))
    url = app.start()
    errors = []
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-analyze")
        page.wait_for_selector("#view-setup:not([hidden])")
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "1")
        page.wait_for_function("() => !document.querySelector('#continue').disabled")
        # where the output is saved is not asked here
        assert page.is_hidden("#setup-output")
        page.click("#continue")
        page.wait_for_selector("#av-main:not([hidden])", timeout=60000)
        first = page.locator("#av-rows > tr").first.locator("td").first.locator("input")
        first.fill("Temperature")
        page.locator("#av-rows > tr").first.locator("td").nth(1).locator("input").first.fill("thetao")
        page.click("#av-continue")
        page.wait_for_selector("#view-analyze-files:not([hidden])")
        page.wait_for_function("() => !document.querySelector('#af-continue').disabled", timeout=60000)
        page.click("#af-continue")
        page.wait_for_selector("#view-analyze-options:not([hidden])")
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

        page.fill("#ao-out_dir", "old")
        page.wait_for_selector("#ao-row-replace:not([hidden])")
        assert page.text_content("#ao-m-out_dir .group__line.is-warn") == (
            "There is an analysis here already, from an earlier run. It will be replaced: "
            "choose another directory to keep it, or tick below to replace it.")
        assert page.is_disabled("#ao-run") and page.is_disabled("#ao-save")
        page.check("#ao-replace_analysis")
        assert page.is_enabled("#ao-run")
        # the tick was for that directory
        page.fill("#ao-out_dir", "old/")
        assert not page.is_checked("#ao-replace_analysis")
        assert page.is_disabled("#ao-run")
        page.check("#ao-replace_analysis")
        # a directory with no analysis needs no tick
        page.fill("#ao-out_dir", "new")
        page.wait_for_selector("#ao-row-replace", state="hidden")
        assert page.is_enabled("#ao-run")
        page.fill("#ao-out_dir", "old")
        page.wait_for_selector("#ao-row-replace:not([hidden])")
        assert not page.is_checked("#ao-replace_analysis")

        # an empty box is the directory worked in
        os.makedirs(tmp_path / analysis.FOLDER)
        page.fill("#ao-out_dir", "")
        page.wait_for_function("() => document.querySelector('#ao-m-out_dir').textContent.includes('name the directory')")
        assert page.is_disabled("#ao-run")
        page.check("#ao-replace_analysis")
        assert page.is_disabled("#ao-run")
        page.fill("#ao-out_dir", "old")
        page.check("#ao-replace_analysis")
        assert page.is_enabled("#ao-save")
        assert not errors
    finally:
        app.close()


def test_map_changes_in_a_browser(browser, tmp_path):
    """The map temporal changes pop-out: its tile is left of Transects, what has to be given is
    red, and Confirm waits for a tick and keeps what was chosen."""
    write_analysis_simulation(str(tmp_path / "sim"))
    app = App(cwd=str(tmp_path))
    url = app.start()
    errors = []
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-analyze")
        page.wait_for_selector("#view-setup:not([hidden])")
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "1")
        page.wait_for_function("() => !document.querySelector('#continue').disabled")
        page.click("#continue")
        page.wait_for_selector("#av-main:not([hidden])", timeout=60000)
        page.locator("#av-rows > tr").first.locator("td").first.locator("input").fill("Temperature")
        page.locator("#av-rows > tr").first.locator("td").nth(1).locator("input").first.fill("thetao")
        page.click("#av-continue")
        page.wait_for_selector("#view-analyze-files:not([hidden])")
        page.wait_for_function("() => !document.querySelector('#af-continue').disabled", timeout=60000)
        page.click("#af-continue")
        page.wait_for_selector("#view-analyze-options:not([hidden])")
        # the tile is just left of Transects, on the same row
        changes, transects_tile = page.locator("#ao-map_changes").bounding_box(), page.locator("#ao-transects").bounding_box()
        assert changes["y"] == transects_tile["y"] and changes["x"] < transects_tile["x"]
        assert page.inner_text("#ao-map_changes-text") == ""

        page.click("#ao-map_changes")
        page.wait_for_selector("#chg-dialog:not([hidden])")
        # the variable is not ticked to start with
        assert page.is_hidden("#chg-remove") and page.is_disabled("#chg-confirm")
        assert not page.locator("#chg-body > tr.chg-main").first.locator("input[type=checkbox]").first.is_checked()
        page.locator("#chg-body > tr.chg-main").first.locator("input[type=checkbox]").first.check()
        main = page.locator("#chg-body > tr.chg-main").first
        years = main.locator("input.is-year")
        assert years.count() == 6
        # nothing is chosen for the user, and what to plot and the change are asked for
        assert years.first.get_attribute("placeholder") == "2013"
        selects = page.locator("#chg-body > tr.chg-options select")
        assert selects.count() == 2
        assert selects.nth(0).input_value() == "" and selects.nth(1).input_value() == ""
        assert [x.strip() for x in selects.nth(0).locator("option").all_inner_texts()] == [
            "Choose…", "Historical + Future (absolute values)",
            "Historical + Future (absolute values) + Changes", "Historical + Changes",
        ]
        assert selects.nth(1).locator("option").all_inner_texts() == ["Choose…", "Absolute change", "Percentage change"]
        surface = main.locator("input[type=checkbox]").nth(1)
        surface.check()
        assert page.is_enabled("#chg-confirm")
        # nothing yet: the second future period's boxes are not marked, and are once one has a year
        def marked(index):
            return "is-need" in (years.nth(index).get_attribute("class") or "")
        assert not marked(4) and not marked(5)
        years.nth(4).fill("2013")
        assert not marked(4) and marked(5)
        years.nth(4).fill("")
        assert not marked(5)
        # what is wrong is said by the variable, and nothing is kept
        page.click("#chg-confirm")
        page.wait_for_function("() => document.querySelector('#chg-body .analyze-msg').textContent.includes('Historical')")
        message = page.locator("#chg-body > tr.chg-options .analyze-msg").first.inner_text()
        assert "Future 1: Start must be a year" in message and "Choose what to plot" in message
        assert app.analyze_chosen["map_changes"] is None and page.is_visible("#chg-dialog")
        for index, year in enumerate(["2013", "2013", "2014", "2014"]):
            years.nth(index).fill(year)
        selects.nth(0).select_option("changes")
        selects.nth(1).select_option("absolute")
        page.click("#chg-confirm")
        page.wait_for_selector("#chg-dialog", state="hidden")
        wait_for(lambda: app.analyze_chosen["map_changes"] is not None)
        chosen = app.analyze_chosen["map_changes"]["rows"]["Temperature"]
        assert chosen["historical"] == {"start": 2013, "end": 2013}
        assert chosen["future"] == [{"start": 2014, "end": 2014}]
        assert (chosen["plot"], chosen["change"]) == ("changes", "absolute")
        page.wait_for_selector("#ao-map_changes.is-chosen")
        assert "Temperature, 2013 to 2014" in page.inner_text("#ao-map_changes-text")
        # what was chosen is there when it is opened again, and it can be removed
        page.click("#ao-map_changes")
        assert page.locator("#chg-body > tr.chg-main").first.locator("input.is-year").nth(2).input_value() == "2014"
        assert page.locator("#chg-body > tr.chg-options select").nth(0).input_value() == "changes"
        page.click("#chg-cancel")
        page.wait_for_selector("#chg-dialog", state="hidden")
        # a tile that is not chosen has no x; a chosen one has, and it removes the analysis without opening the pop-out
        assert page.is_visible("#ao-map_changes .choice__remove")
        assert page.is_hidden("#ao-transects .choice__remove")
        page.click("#ao-map_changes .choice__remove")
        wait_for(lambda: app.analyze_chosen["map_changes"] is None)
        page.wait_for_selector("#ao-map_changes:not(.is-chosen)")
        assert page.is_hidden("#chg-dialog")
        assert page.inner_text("#ao-map_changes-text") == ""
        assert errors == []
    finally:
        app.close()


def open_analyze_option(browser, tmp_path, years, option, dialog, rows=(("Temperature", "thetao"),), daily=True):
    """The pop-out of an Analyze option (its button `ao-<option>`, its
    dialog id) of a simulation of years, for the variables rows (name, model
    variable): (the page, the app, the errors). With daily=False the output is monthly."""
    write_analysis_simulation(str(tmp_path / "sim"), years=years, daily=daily)
    app = App(cwd=str(tmp_path))
    url = app.start()
    errors = []
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.wait_for_selector("#view-home:not([hidden])")
    page.click("#home-analyze")
    page.wait_for_selector("#view-setup:not([hidden])")
    page.fill("#f-simdir", "sim")
    page.fill("#f-ndown", "1")
    page.wait_for_function("() => !document.querySelector('#continue').disabled")
    page.click("#continue")
    page.wait_for_selector("#av-main:not([hidden])", timeout=60000)
    for number, (name, model) in enumerate(rows):
        if number:
            page.locator("#av-rows > tr").last.locator(".analyze-add").click()
        page.locator("#av-rows > tr").nth(number).locator("td").first.locator("input").fill(name)
        page.locator("#av-rows > tr").nth(number).locator("td").nth(1).locator("input").first.fill(model)
    page.click("#av-continue")
    page.wait_for_selector("#view-analyze-files:not([hidden])")
    page.wait_for_function("() => !document.querySelector('#af-continue').disabled", timeout=60000)
    page.click("#af-continue")
    page.wait_for_selector("#view-analyze-options:not([hidden])")
    page.click(f"#ao-{option}")
    page.wait_for_selector(f"#{dialog}:not([hidden])")
    return page, app, errors


def picker_of(app):
    """The model's variables the variables step offers, as the app sends them (the
    page's own `state` is not reachable from the console)."""
    return get_json(app, "/api/state")[1]["analyze"]["picker"]


def open_analyze_variables_in_browser(browser, tmp_path):
    """The Analyze variables step of the analysis simulation, opened in a browser:
    (the page, the app)."""
    write_analysis_simulation(str(tmp_path / "sim"), years=(2011, 2012))
    app = App(cwd=str(tmp_path))
    url = app.start()
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    page.goto(url)
    page.wait_for_selector("#view-home:not([hidden])")
    page.click("#home-analyze")
    page.wait_for_selector("#view-setup:not([hidden])")
    page.fill("#f-simdir", "sim")
    page.fill("#f-ndown", "1")
    page.wait_for_function("() => !document.querySelector('#continue').disabled")
    page.click("#continue")
    page.wait_for_selector("#av-main:not([hidden])", timeout=60000)
    return page, app


def test_the_variable_searches_take_wildcards_in_a_browser(browser, tmp_path):
    """P*_Chl finds P1_Chl and P2_Chl as a row's box is typed in and in Add multiple variables."""
    page, app = open_analyze_variables_in_browser(browser, tmp_path)
    try:
        box = page.locator("#av-rows > tr").first.locator("td").nth(1).locator("input")
        assert page.locator("details.vars").count() == 0
        shown = lambda: page.evaluate("() => [...document.querySelectorAll('.vars__popover:not([hidden]) tbody tr:not([hidden])')].map((row) => row.cells[1].textContent)")
        box.fill("P*_Chl")
        assert shown() == ["P1_Chl", "P2_Chl"]
        box.fill("e3?")
        assert shown() == ["e3t"]
        box.fill("P*_Chl e3t")
        assert shown() == []
        # after a + every row is shown, with P1_Chl ticked, and the next part is searched
        box.fill("P1_Chl+")
        assert len(shown()) == 4
        assert page.locator('.vars__popover:not([hidden]) input[aria-label="Use P1_Chl"]').is_checked()
        box.press_sequentially("P2")
        assert shown() == ["P2_Chl"]
        page.locator(".vars__popover:not([hidden]) tbody tr:not([hidden])").first.click()
        assert box.input_value() == "P1_Chl+P2_Chl"

        page.click("#av-add-multiple")
        page.wait_for_selector("#multi-dialog:not([hidden])")
        cells = lambda: page.locator("#multi-grid .multi-cell:not([hidden])").evaluate_all("nodes => nodes.map(n => n.dataset.variable)")
        assert len(cells()) == 4
        page.fill("#multi-search", "P*_Chl")
        assert cells() == ["P1_Chl", "P2_Chl"]
        page.fill("#multi-search", "p?_chl")
        assert cells() == ["P1_Chl", "P2_Chl"]
        page.fill("#multi-search", "thetao")
        assert cells() == ["thetao"]
        page.fill("#multi-search", "P(*")
        assert cells() == []
    finally:
        page.close()
        app.close()


def variable_rows_of(page):
    rows = page.locator("#av-rows > tr")
    return [rows.nth(i).locator("td").nth(1).locator("input").first.input_value() for i in range(rows.count())]


def add_every_variable(page):
    page.click("#av-add-multiple")
    page.wait_for_selector("#multi-dialog:not([hidden])")
    cells = page.locator("#multi-grid .multi-cell:not([disabled])")
    for i in range(cells.count()):
        cells.nth(i).click()
    page.click("#multi-confirm")


def test_remove_all_variables_leaves_one_blank_row(browser, tmp_path):
    page, app = open_analyze_variables_in_browser(browser, tmp_path)
    # nothing to remove yet
    assert page.is_disabled("#av-remove-all")
    add_every_variable(page)
    available = picker_of(app)["available"]
    assert sorted(variable_rows_of(page)) == sorted(available)
    assert not page.is_disabled("#av-remove-all")
    assert not page.is_disabled("#av-continue")
    page.click("#av-remove-all")
    assert variable_rows_of(page) == [""]
    assert page.is_disabled("#av-continue")
    assert page.is_disabled("#av-remove-all")
    # and the variables can be added again
    add_every_variable(page)
    assert sorted(variable_rows_of(page)) == sorted(available)
    page.close()


def test_the_setup_page_says_it_is_finding_files_until_the_answer_is_in(browser, tmp_path):
    """While the files are being looked for the box beside the depth shows
    "Finding files" and a spinner, not the previous count."""
    write_analysis_simulation(str(tmp_path / "sim"), years=(2011, 2012))
    app = App(cwd=str(tmp_path))
    url = app.start()
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    try:
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-analyze")
        page.wait_for_selector("#view-setup:not([hidden])")
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "1")
        page.wait_for_function("() => document.querySelector('#ndown-found').textContent.includes('netCDF files found')")
        held = []
        page.route("**/api/probe*", lambda route: held.append(route))
        page.fill("#f-ndown", "0")
        # at once, before the request is even made, and with no count left over
        assert page.text_content("#ndown-found") == "Finding files"
        assert page.is_visible("#ndown-found .identify__spinner")
        for _ in range(50):
            if held:
                break
            page.wait_for_timeout(100)
        assert held
        assert page.text_content("#ndown-found") == "Finding files"
        for route in held:
            route.continue_()
        page.unroute("**/api/probe*")
        page.wait_for_function("() => !document.querySelector('#ndown-found').textContent.includes('Finding files')")
        assert page.locator("#ndown-found .identify__spinner").count() == 0
    finally:
        page.close()
        app.close()


def open_transects_in_browser(browser, tmp_path, years):
    """The transects pop-out of a simulation of years, with Temperature's
    transect card: (the page, the app, the card's locator, the errors)."""
    page, app, errors = open_analyze_option(browser, tmp_path, years, "transects", "trans-dialog")
    return page, app, page.locator("#trans-body .transect-card").first, errors


def test_the_year_boxes_list_the_years_of_the_output(browser, tmp_path):
    """Each start/end year box has an arrow listing every year of the output;
    a year picked fills the box, and Escape closes the list before the
    pop-out."""
    page, app, errors = open_analyze_option(browser, tmp_path, (2011, 2012, 2013, 2014), "climatology", "clim-dialog")
    try:
        start = page.locator("#clim-body input.is-year").nth(0)
        end = page.locator("#clim-body input.is-year").nth(1)
        assert page.is_hidden("body > .year-list")
        # the arrow is the right-hand end of the box
        box = start.bounding_box()
        page.mouse.click(box["x"] + box["width"] - 8, box["y"] + box["height"] / 2)
        assert page.locator("body > .year-list .combo__option").all_inner_texts() == ["2011", "2012", "2013", "2014"]
        # a year picked by the mouse
        page.locator("body > .year-list .combo__option", has_text="2012").click()
        assert start.input_value() == "2012" and page.is_hidden("body > .year-list")
        # the keyboard: the arrow down opens, down moves, Enter picks
        end.focus()
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert end.input_value() == "2012"
        end.focus()
        # it opens on the year in the box, 2012
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert end.input_value() == "2013"
        # Escape closes the list, not the pop-out; the next one the pop-out
        page.keyboard.press("ArrowDown")
        assert page.is_visible("body > .year-list")
        page.keyboard.press("Escape")
        assert page.is_hidden("body > .year-list") and page.is_visible("#clim-dialog")
        page.keyboard.press("Escape")
        page.wait_for_selector("#clim-dialog", state="hidden")
        # typing still works, and closes the list
        page.click("#ao-climatology")
        end = page.locator("#clim-body input.is-year").nth(1)
        end.focus()
        page.keyboard.press("ArrowDown")
        page.keyboard.type("2")
        assert page.is_hidden("body > .year-list")
        assert errors == []
    finally:
        app.close()


def test_the_long_term_series_have_a_rolling_mean_and_a_way_to_show_them(browser, tmp_path):
    """After Water column, the long-term pop-out has a "Use a rolling mean (years)" column,
    1 to start with, and Show as; the climatology's has neither."""
    page, app, errors = open_analyze_option(browser, tmp_path, (2011, 2012, 2013, 2014), "long_term", "clim-dialog")
    try:
        heads = page.locator("#clim-dialog thead tr").nth(1).locator("th:visible").all_inner_texts()
        assert [h.lower().replace("required", "").strip() for h in heads] == [
            "variable", "start", "end", "surface", "bottom", "water column", "use a rolling mean (years)", "show as"]
        row = page.locator("#clim-body > tr").first
        window = row.locator("input.is-window")
        show = row.locator("select.is-show")
        assert window.input_value() == "1"
        assert show.locator("option").all_inner_texts() == ["Absolute value", "Absolute change", "Percentage change"]
        assert show.input_value() == "absolute"
        row.locator("input[type=checkbox]").nth(1).check()
        page.locator("#clim-body input.is-year").nth(0).fill("2011")
        page.locator("#clim-body input.is-year").nth(1).fill("2013")
        show.select_option("absolute_change")
        window.fill("4")
        page.click("#clim-confirm")
        # four years do not fit in three, which is said by the variable
        page.wait_for_function("() => document.querySelector('#clim-body .analyze-msg').textContent !== ''")
        assert "more than the 3 years" in row.locator(".analyze-msg").inner_text()
        window.fill("2")
        page.click("#clim-confirm")
        page.wait_for_selector("#clim-dialog", state="hidden")
        chosen = app.analyze_chosen["long_term"]["rows"]["Temperature"]
        assert chosen["window"] == 2 and chosen["show"] == "absolute_change"
        # what was chosen is there when it is opened again, and the climatology has neither column
        page.click("#ao-long_term")
        assert page.locator("#clim-body input.is-window").input_value() == "2"
        page.click("#clim-cancel")
        page.click("#ao-climatology")
        assert page.locator("#clim-body input.is-window").count() == 0
        assert page.locator("#clim-dialog th.is-series:visible").count() == 0
        assert errors == []
    finally:
        app.close()


@needs_to_transect
def test_a_disabled_year_box_has_no_list(browser, tmp_path):
    """The transect options' year boxes are shut until the option is chosen,
    and then list the output's years: one year here."""
    page, app, card, errors = open_transects_in_browser(browser, tmp_path, (2013,))
    try:
        years = card.locator("table.transect-options input.is-year")
        box = years.nth(0).bounding_box()
        page.mouse.click(box["x"] + box["width"] - 8, box["y"] + box["height"] / 2)
        assert page.is_hidden("body > .year-list")
        card.locator("input[type=checkbox]").first.check()
        years.nth(0).focus()
        page.keyboard.press("ArrowDown")
        assert page.locator("body > .year-list .combo__option").all_inner_texts() == ["2013"]
        assert errors == []
    finally:
        app.close()


@needs_to_transect
def test_trends_are_not_offered_for_one_year_of_output(browser, tmp_path):
    page, app, card, errors = open_transects_in_browser(browser, tmp_path, (2013,))
    try:
        # neither the surface changes nor the vertical changes are there to choose
        assert all(card.locator("select").nth(i).is_disabled() for i in (0, 1)) and "Only one year of output" in card.inner_text()
        assert all(card.locator("table.transect-options input.is-year").nth(i).is_disabled() for i in (2, 3, 6, 7))
        # the other options are there
        card.locator("input[type=checkbox]").first.check()
        assert card.locator("table.transect-options input.is-year").nth(0).is_enabled()
        assert errors == []
    finally:
        app.close()


@needs_to_transect
def test_two_years_are_enough_for_every_way_of_showing_the_changes(browser, tmp_path):
    page, app, card, errors = open_transects_in_browser(browser, tmp_path, (2013, 2014))
    try:
        assert "Only one year" not in card.inner_text()
        for number in (0, 1):
            trends = card.locator("select").nth(number)
            assert trends.is_enabled()
            disabled = {o.get_attribute("value"): o.is_disabled() for o in trends.locator("option").all() if o.get_attribute("value")}
            assert disabled == {"absolute": False, "absolute_change": False, "percentage_change": False}
        assert errors == []
    finally:
        app.close()


@needs_to_transect
def test_transects_in_a_browser(browser, tmp_path):
    """The transects pop-out: a section for each variable, with a card for each
    of its transects: the ends in one line, and a Surface and a Vertical part,
    each with a climatology and its changes, each with its own years."""
    write_analysis_simulation(str(tmp_path / "sim"))
    app = App(cwd=str(tmp_path))
    url = app.start()
    errors = []
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-analyze")
        page.wait_for_selector("#view-setup:not([hidden])")
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "1")
        page.wait_for_function("() => !document.querySelector('#continue').disabled")
        page.click("#continue")
        page.wait_for_selector("#av-main:not([hidden])", timeout=60000)
        rows = [("Temperature", "thetao"), ("Chlorophyll", "P1_Chl+P2_Chl")]
        for number, (name, model) in enumerate(rows):
            if number:
                page.locator("#av-rows > tr").last.locator(".analyze-add").click()
            page.locator("#av-rows > tr").nth(number).locator("td").first.locator("input").fill(name)
            page.locator("#av-rows > tr").nth(number).locator("td").nth(1).locator("input").first.fill(model)
        page.click("#av-continue")
        page.wait_for_selector("#view-analyze-files:not([hidden])")
        page.wait_for_function("() => !document.querySelector('#af-continue').disabled", timeout=60000)
        page.click("#af-continue")
        page.wait_for_selector("#view-analyze-options:not([hidden])")
        assert page.inner_text("#ao-transects-text") == ""
        page.click("#ao-transects")
        page.wait_for_selector("#trans-dialog:not([hidden])")
        assert page.is_hidden("#trans-remove")
        assert page.locator("#trans-dialog .picker__foot button:visible").all_inner_texts() == ["Cancel", "Confirm"]
        # a section for each variable, each unticked to start with, with one transect with nothing in it
        sections = page.locator("#trans-body .transect-var")
        assert sections.count() == 2 and page.is_disabled("#trans-confirm")
        assert "north–south" in page.inner_text("#trans-dialog .transect-rule")
        temperature = sections.nth(0)
        assert not temperature.locator(".transect-var__head input[type=checkbox]").is_checked()
        for section in (temperature, sections.nth(1)):
            section.locator(".transect-var__head input[type=checkbox]").check()
        # the transects of chlorophyll are left out
        sections.nth(1).locator(".transect-var__head input[type=checkbox]").uncheck()
        cards = temperature.locator(".transect-card")
        assert cards.count() == 1 and "Transect 1" in cards.first.inner_text()
        # the ends are in one line, with the start's longitude and latitude and then the end's, above the options
        end_boxes = cards.first.locator(".transect-ends input")
        assert [end_boxes.nth(i).get_attribute("aria-label") for i in range(4)] == [
            "Longitude of the start", "Latitude of the start", "Longitude of the end", "Latitude of the end",
        ]
        lines = {round(end_boxes.nth(i).bounding_box()["y"]) for i in range(4)}
        assert len(lines) == 1
        ends = cards.first.locator(".transect-ends").bounding_box()
        options = cards.first.locator("table.transect-options").bounding_box()
        assert options["y"] >= ends["y"] + ends["height"] - 1
        # a Surface and a Vertical part
        assert [x.strip() for x in cards.first.locator("tr.transect-part").all_inner_texts()] == ["Surface", "Vertical"]
        # the years of an option are shut until it is chosen: a climatology and changes, in each part
        years = cards.first.locator("table.transect-options input.is-year")
        assert years.count() == 8 and all(years.nth(i).is_disabled() for i in range(8))
        for number in (0, 1):
            assert [x.strip() for x in cards.first.locator("select").nth(number).locator("option").all_inner_texts()] == [
                "Trends: choose…", "Absolute", "Absolute change", "Percentage change",
            ]
        # the rolling mean window starts at 1, is only open once changes are chosen, and is right of their years
        window = cards.first.locator("input.is-window").first
        assert window.input_value() == "1" and window.is_disabled()
        assert window.bounding_box()["x"] > years.nth(3).bounding_box()["x"] + years.nth(3).bounding_box()["width"] - 1
        # chlorophyll has no depth levels
        assert "No depth levels" in sections.nth(1).inner_text()
        temperature.locator(".transect-var__head input[type=checkbox]").check()
        assert page.is_enabled("#trans-confirm")
        for box, value in zip(end_boxes.all(), ["-2", "50", "-2", "53"]):
            box.fill(value)
        cards.first.locator("input[type=checkbox]").first.check()
        assert years.nth(0).is_enabled() and years.nth(4).is_disabled()
        years.nth(0).fill("2013")
        years.nth(1).fill("2014")
        # a vertical section asks whether the simulation is z-level, or where its thickness is
        assert page.is_hidden("#trans-thickness-row")
        vertical = cards.first.locator("input[type=checkbox]").nth(1)
        vertical.check()
        assert page.is_visible("#trans-thickness-row") and years.nth(4).is_enabled()
        page.fill("#trans-thickness", "e3t")
        vertical.uncheck()
        assert page.is_hidden("#trans-thickness-row")
        # the vertical changes need it too, and open their own years and rolling mean
        vertical_changes = cards.first.locator("select").nth(1)
        vertical_changes.select_option("absolute_change")
        assert page.is_visible("#trans-thickness-row") and years.nth(6).is_enabled() and years.nth(7).is_enabled()
        assert cards.first.locator("input.is-window").nth(1).is_enabled() and cards.first.locator("input.is-window").first.is_disabled()
        vertical_changes.select_option("")
        assert page.is_hidden("#trans-thickness-row") and years.nth(6).is_disabled()
        # a second transect, with the changes, which does not follow the rule, is said by its number
        temperature.get_by_role("button", name="Add a transect").click()
        assert cards.count() == 2 and "Transect 2" in cards.nth(1).inner_text()
        for box, value in zip(cards.nth(1).locator(".transect-ends input").all(), ["0", "0", "5", "5"]):
            box.fill(value)
        cards.nth(1).locator("select").first.select_option("absolute_change")
        assert cards.nth(1).locator("input.is-window").first.is_enabled()
        cards.nth(1).locator("table.transect-options input.is-year").nth(2).fill("2013")
        cards.nth(1).locator("table.transect-options input.is-year").nth(3).fill("2014")
        page.click("#trans-confirm")
        page.wait_for_function(
            "() => document.querySelectorAll('#trans-body .transect-var')[0].querySelectorAll('.transect-card')[1].querySelector('.analyze-msg').textContent.length > 0"
        )
        assert "north–south or east–west" in cards.nth(1).locator(".analyze-msg").inner_text()
        assert cards.first.locator(".analyze-msg").inner_text() == ""
        assert page.is_visible("#trans-dialog") and app.analyze_chosen["transects"] is None
        # a transect is taken away, and the rest are numbered again
        cards.first.locator("button[aria-label='Remove this transect']").click()
        assert cards.count() == 1 and "Transect 1" in cards.first.inner_text()
        for box, value in zip(cards.first.locator(".transect-ends input").all(), ["-4", "51", "0", "51"]):
            box.fill(value)
        # a rolling mean that leaves too few years is said
        cards.first.locator("input.is-window").first.fill("5")
        page.click("#trans-confirm")
        page.wait_for_function(
            "() => document.querySelector('#trans-body .transect-card .analyze-msg').textContent.includes('rolling mean of 5')"
        )
        assert page.is_visible("#trans-dialog")
        cards.first.locator("input.is-window").first.fill("1")
        page.click("#trans-confirm")
        page.wait_for_selector("#trans-dialog", state="hidden")
        wait_for(lambda: app.analyze_chosen["transects"] is not None)
        assert app.analyze_chosen["transects"]["rows"] == {
            "Temperature": [
                {"start": [-4.0, 51.0], "end": [0.0, 51.0], "climatology": None, "vertical": None,
                 "trends": {"start": 2013, "end": 2014, "how": "absolute_change"}, "vertical_trends": None}
            ]
        }
        page.wait_for_selector("#ao-transects.is-chosen")
        assert "Temperature, 1 transect" in page.inner_text("#ao-transects-text")
        assert page.is_enabled("#ao-continue")
        # what was chosen is there when it is opened again, and can be removed
        page.click("#ao-transects")
        assert page.locator("#trans-body .transect-var").nth(0).locator(".transect-var__head input[type=checkbox]").is_checked()
        assert page.locator("#trans-body .transect-card select").first.input_value() == "absolute_change"
        assert page.locator("#trans-body .transect-card select").nth(1).input_value() == ""
        assert page.is_visible("#trans-remove")
        page.click("#trans-remove")
        page.wait_for_selector("#trans-dialog", state="hidden")
        wait_for(lambda: app.analyze_chosen["transects"] is None)
        assert errors == []
    finally:
        app.close()


# Temperature has depth levels, Chlorophyll is at the surface only (see write_analysis_simulation)
TWO_VARIABLES = (("Temperature", "thetao"), ("Chlorophyll", "P1_Chl+P2_Chl"))


def test_the_all_variables_row_sets_every_variable_in_a_browser(browser, tmp_path):
    """With more than one variable, a pop-out's table has an All variables row at the top: what is set
    there is set for every variable it applies to, a variable set otherwise has a Reset back to it, and
    each variable's tick, unticked to start with, leaves it out."""
    page, app, errors = open_analyze_option(browser, tmp_path, (2011, 2012, 2013, 2014), "climatology", "clim-dialog",
                                            rows=TWO_VARIABLES)
    try:
        all_row = page.locator("#clim-all > tr")
        assert all_row.count() == 1 and "All variables" in all_row.inner_text()
        rows = page.locator("#clim-body > tr")
        temperature, chlorophyll = rows.nth(0), rows.nth(1)
        ticks = lambda row: row.locator("input[type=checkbox]")
        # no variable is ticked to start with, nor is the All variables row's tick; ticking it ticks every variable
        assert not ticks(temperature).first.is_checked() and not ticks(chlorophyll).first.is_checked()
        assert not ticks(all_row).first.is_checked()
        ticks(all_row).first.check()
        # the years and the surface, set once
        all_row.locator("input.is-year").nth(0).fill("2011")
        all_row.locator("input.is-year").nth(1).fill("2013")
        ticks(all_row).nth(1).check()
        for row in (temperature, chlorophyll):
            assert row.locator("input.is-year").nth(0).input_value() == "2011"
            assert row.locator("input.is-year").nth(1).input_value() == "2013"
            assert ticks(row).nth(1).is_checked()
        # the bottom is set only for a variable with depth levels: one without has none to tick
        assert ticks(chlorophyll).count() == 2 and chlorophyll.inner_text().count("No depth levels") == 2
        ticks(all_row).nth(2).check()
        assert ticks(temperature).nth(2).is_checked() and ticks(chlorophyll).count() == 2
        ticks(all_row).nth(2).uncheck()
        assert not ticks(temperature).nth(2).is_checked()
        # a variable set otherwise has a Reset, which puts it back
        reset = chlorophyll.locator("button.analyze-reset")
        assert reset.is_hidden()
        chlorophyll.locator("input.is-year").nth(1).fill("2012")
        assert reset.is_visible() and temperature.locator("button.analyze-reset").is_hidden()
        assert all_row.locator("input.is-year").nth(1).input_value() == "2013"
        reset.click()
        assert chlorophyll.locator("input.is-year").nth(1).input_value() == "2013" and reset.is_hidden()
        # a variable unticked is left out
        ticks(chlorophyll).first.uncheck()
        page.click("#clim-confirm")
        page.wait_for_selector("#clim-dialog", state="hidden")
        wait_for(lambda: app.analyze_chosen["climatology"] is not None)
        assert app.analyze_chosen["climatology"]["rows"] == {
            "Temperature": {"start": 2011, "end": 2013, "surface": True, "bottom": False, "water_column": None},
        }
        # the variables differ, so the All variables row's tick starts unticked; ticking it ticks every
        # variable, and unticking it unticks them all, so there is nothing to confirm
        page.click("#ao-climatology")
        page.wait_for_selector("#clim-dialog:not([hidden])")
        assert not ticks(all_row).first.is_checked()
        ticks(all_row).first.check()
        assert ticks(temperature).first.is_checked() and ticks(chlorophyll).first.is_checked()
        ticks(all_row).first.uncheck()
        assert not ticks(temperature).first.is_checked() and not ticks(chlorophyll).first.is_checked()
        assert page.is_disabled("#clim-confirm")
        assert errors == []
    finally:
        app.close()


def test_a_variable_ticked_with_no_layer_is_said_in_a_browser(browser, tmp_path):
    """A variable left ticked with no layer is said by its row, which asks for a layer or the tick off."""
    page, app, errors = open_analyze_option(browser, tmp_path, (2013, 2014), "climatology", "clim-dialog",
                                            rows=TWO_VARIABLES)
    try:
        temperature = page.locator("#clim-body > tr").nth(0)
        temperature.locator("input[type=checkbox]").nth(1).check()
        temperature.locator("input.is-year").nth(0).fill("2013")
        temperature.locator("input.is-year").nth(1).fill("2014")
        # the second variable is ticked, with its years, but no layer
        chlorophyll = page.locator("#clim-body > tr").nth(1)
        chlorophyll.locator("input[type=checkbox]").nth(0).check()
        chlorophyll.locator("input.is-year").nth(0).fill("2013")
        chlorophyll.locator("input.is-year").nth(1).fill("2014")
        page.click("#clim-confirm")
        page.wait_for_function("() => document.querySelectorAll('#clim-body .analyze-msg')[1].textContent !== ''")
        assert "untick the variable" in page.locator("#clim-body > tr").nth(1).locator(".analyze-msg").inner_text()
        assert app.analyze_chosen["climatology"] is None
        assert errors == []
    finally:
        app.close()


def test_the_errors_of_the_pop_outs_are_red_and_bold_in_a_browser(browser, tmp_path):
    """A row's error (an end year before the start) is red and bold, as the page's --red token."""
    page, app, errors = open_analyze_option(browser, tmp_path, (2013, 2014), "climatology", "clim-dialog")
    try:
        row = page.locator("#clim-body > tr").first
        row.locator("input[type=checkbox]").nth(1).check()
        row.locator("input.is-year").nth(0).fill("2014")
        row.locator("input.is-year").nth(1).fill("2013")
        page.click("#clim-confirm")
        page.wait_for_function("() => document.querySelector('#clim-body .analyze-msg').textContent !== ''")
        message = row.locator(".analyze-msg")
        assert "End must not be before start" in message.inner_text()
        red = page.evaluate("() => { const probe = document.createElement('i'); probe.style.color = 'var(--red)';"
                            " document.body.append(probe); const colour = getComputedStyle(probe).color; probe.remove(); return colour; }")
        style = message.evaluate("(node) => { const css = getComputedStyle(node); return [css.color, +css.fontWeight]; }")
        assert style[0] == red
        assert style[1] >= 700
        assert errors == []
    finally:
        app.close()


def test_the_all_variables_row_of_the_long_term_series_and_the_map_changes_in_a_browser(browser, tmp_path):
    """The long-term series' rolling mean and way to show them, and the map changes' periods, plot and
    change, are set for every variable from the All variables row; one variable has no such row."""
    page, app, errors = open_analyze_option(browser, tmp_path, (2011, 2012, 2013, 2014), "long_term", "clim-dialog",
                                            rows=TWO_VARIABLES)
    try:
        all_row = page.locator("#clim-all > tr")
        all_row.locator("input.is-window").fill("2")
        all_row.locator("select.s-select").last.select_option("percentage_change")
        for number in range(2):
            row = page.locator("#clim-body > tr").nth(number)
            assert row.locator("input.is-window").input_value() == "2"
            assert row.locator("select.is-show").input_value() == "percentage_change"
        page.click("#clim-cancel")
        page.wait_for_selector("#clim-dialog", state="hidden")

        page.click("#ao-map_changes")
        page.wait_for_selector("#chg-dialog:not([hidden])")
        all_main = page.locator("#chg-all > tr.chg-main")
        for index, year in enumerate(["2011", "2012", "2013", "2014"]):
            all_main.locator("input.is-year").nth(index).fill(year)
        all_options = page.locator("#chg-all > tr.chg-options select")
        all_options.nth(0).select_option("changes")
        all_options.nth(1).select_option("percentage")
        all_main.locator("input[type=checkbox]").nth(1).check()
        page.click("#chg-confirm")
        page.wait_for_selector("#chg-dialog", state="hidden")
        wait_for(lambda: app.analyze_chosen["map_changes"] is not None)
        chosen = app.analyze_chosen["map_changes"]["rows"]
        assert set(chosen) == {"Temperature", "Chlorophyll"}
        for entry in chosen.values():
            assert entry["historical"] == {"start": 2011, "end": 2012} and entry["future"] == [{"start": 2013, "end": 2014}]
            assert (entry["plot"], entry["change"]) == ("changes", "percentage")
        assert errors == []
    finally:
        app.close()
    page, app, errors = open_analyze_option(browser, tmp_path / "one", (2013, 2014), "map_changes", "chg-dialog")
    try:
        assert page.locator("#chg-all > tr").count() == 0
        assert page.locator("#chg-body button.analyze-reset").is_hidden()
        assert errors == []
    finally:
        app.close()


def test_the_transects_of_a_variable_are_copied_to_every_variable_in_a_browser(browser, tmp_path):
    """A variable's transects are copied to every other variable, which a variable with no depth levels
    has without its Vertical part."""
    page, app, errors = open_analyze_option(browser, tmp_path, (2013, 2014), "transects", "trans-dialog",
                                            rows=TWO_VARIABLES)
    try:
        sections = page.locator("#trans-body .transect-var")
        temperature, chlorophyll = sections.nth(0), sections.nth(1)
        card = temperature.locator(".transect-card").first
        for box, value in zip(card.locator(".transect-ends input").all(), ["-4", "51", "0", "51"]):
            box.fill(value)
        card.locator("input[type=checkbox]").nth(0).check()
        card.locator("input[type=checkbox]").nth(1).check()
        card.locator("table.transect-options input.is-year").nth(0).fill("2013")
        card.locator("table.transect-options input.is-year").nth(1).fill("2014")
        temperature.get_by_role("button", name="Copy these transects to every variable").click()
        assert "Copied to 1 other variable" in temperature.locator(".analyze-msg").first.inner_text()
        copied = chlorophyll.locator(".transect-card")
        assert copied.count() == 1
        assert [box.input_value() for box in copied.first.locator(".transect-ends input").all()] == ["-4", "51", "0", "51"]
        assert copied.first.locator("input[type=checkbox]").nth(0).is_checked()
        # chlorophyll has no depth levels, so no Vertical climatology
        assert not copied.first.locator("input[type=checkbox]").nth(1).is_checked()
        assert copied.first.locator("input[type=checkbox]").nth(1).is_disabled()
        assert errors == []
    finally:
        app.close()


def test_the_names_drop_down_the_larger_pop_outs_and_the_missing_value_in_a_browser(browser, tmp_path, monkeypatch):
    """The arrow of "Environmental variable" lists the preset names, and choosing one fills
    in the Variable name in the simulation in bold; the pop-outs are 1300px wide; and the
    missing value found in the data is amber, until it is typed over."""
    from oceanval import missing_probe

    monkeypatch.setattr(missing_probe, "probe", lambda files, name: "1e+20")
    write_analysis_simulation(str(tmp_path / "sim"))
    app = App(cwd=str(tmp_path))
    url = app.start()
    errors = []
    try:
        page = browser.new_page(viewport={"width": 1500, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-analyze")
        page.wait_for_selector("#view-setup:not([hidden])")
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "1")
        page.wait_for_function("() => !document.querySelector('#continue').disabled")
        page.click("#continue")
        page.wait_for_selector("#av-main:not([hidden])", timeout=60000)
        row = page.locator("#av-rows > tr").first
        name = row.locator("td").first.locator("input")
        model = row.locator("td").nth(1).locator("input").first
        # the arrow is the right-hand end of the box
        box = name.bounding_box()
        page.mouse.click(box["x"] + box["width"] - 10, box["y"] + box["height"] / 2)
        page.wait_for_selector(".year-list:not([hidden])")
        options = page.locator(".year-list li").all_inner_texts()
        assert any(text.startswith("Temperature") for text in options) and any(text.startswith("Salinity") for text in options)
        # a name the model has no variable for says so
        assert "not found in this model" in [t for t in options if t.startswith("Salinity")][0]
        page.locator(".year-list li", has_text="Temperature").click()
        assert name.input_value() == "Temperature" and model.input_value() == "thetao"
        assert "is-oceanval" in name.get_attribute("class") and "is-oceanval" in model.get_attribute("class")
        # choosing another name replaces what the drop-down filled in, and one with no model variable empties it
        box = name.bounding_box()
        page.mouse.click(box["x"] + box["width"] - 10, box["y"] + box["height"] / 2)
        page.locator(".year-list li", has_text="Chlorophyll").click()
        assert name.input_value() == "Chlorophyll" and model.input_value() == "P1_Chl+P2_Chl"
        page.mouse.click(box["x"] + box["width"] - 10, box["y"] + box["height"] / 2)
        page.locator(".year-list li", has_text="Salinity").click()
        assert name.input_value() == "Salinity" and model.input_value() == ""
        assert "is-oceanval" not in model.get_attribute("class")
        page.mouse.click(box["x"] + box["width"] - 10, box["y"] + box["height"] / 2)
        page.locator(".year-list li", has_text="Temperature").click()
        assert name.input_value() == "Temperature" and model.input_value() == "thetao"
        # typing over it makes it the user's
        model.fill("e3t")
        assert "is-oceanval" not in model.get_attribute("class")
        # a model box the user typed in is not overwritten
        page.mouse.click(box["x"] + box["width"] - 10, box["y"] + box["height"] / 2)
        page.locator(".year-list li", has_text="Chlorophyll").click()
        assert name.input_value() == "Chlorophyll" and model.input_value() == "e3t"
        model.fill("thetao")
        name.fill("Temperature")
        page.click("#av-continue")
        page.wait_for_selector("#view-analyze-files:not([hidden])")
        page.wait_for_function("() => !document.querySelector('#af-continue').disabled", timeout=60000)
        page.click("#af-continue")
        page.wait_for_selector("#view-analyze-options:not([hidden])")
        # the missing value is on the second page, which needs an analysis chosen
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": {
            "rows": {"Temperature": {"use": True, "start": "2013", "end": "2014", "surface": True}}}})[0] == 200
        page.wait_for_function("() => !document.querySelector('#ao-continue').disabled")
        page.click("#ao-continue")
        page.wait_for_selector("#ao-page-2:not([hidden])")
        # the missing value found in the data is amber, with a note
        page.wait_for_function("() => document.querySelector('#ao-missing_from').value === '1e+20'")
        assert "is-found" in page.get_attribute("#ao-missing_from", "class")
        assert page.is_visible("#ao-missing-found") and page.is_hidden("#ao-missing-ersem")
        page.fill("#ao-missing_from", "0")
        assert "is-found" not in page.get_attribute("#ao-missing_from", "class")
        assert page.is_hidden("#ao-missing-found")
        page.click("#actions button:has-text('Back')")
        page.wait_for_selector("#ao-page-1:not([hidden])")
        # the pop-outs are 30% larger than the 1000px they were
        for option, dialog in (("climatology", "clim-dialog"), ("transects", "trans-dialog")):
            page.click(f"#ao-{option}")
            page.wait_for_selector(f"#{dialog}:not([hidden])")
            assert page.locator(f"#{dialog} .picker__card").bounding_box()["width"] == pytest.approx(1300, abs=1)
            page.click(f"#{dialog} .picker__foot button:has-text('Cancel')")
        assert errors == []
    finally:
        app.close()


# ---- the info buttons, which say in words what an option plots ----


def test_only_the_options_step_is_given_what_the_buttons_say(app, sim):
    assert to_variables(app)["examples"] == {}


def test_the_options_step_is_given_what_the_buttons_say(app, sim):
    assert to_options(app)["examples"] == analysis_examples.describe()


INFO_ROWS = (("Chlorophyll", "P1_Chl+P2_Chl"), ("Temperature", "thetao"))


def test_the_info_buttons_of_the_climatology_pop_out(browser, tmp_path):
    page, app, errors = open_analyze_option(browser, tmp_path, (2011, 2012), "climatology", "clim-dialog", rows=INFO_ROWS)
    try:
        everyone, rows = page.locator("#clim-all > tr"), page.locator("#clim-body > tr")
        chlorophyll, temperature = rows.nth(0), rows.nth(1)
        # one beside each layer a variable has, and the All variables row's beside each
        assert everyone.locator(".info-btn").count() == 3
        assert chlorophyll.locator(".info-btn").count() == 1 and temperature.locator(".info-btn").count() == 3
        assert temperature.locator(".info-btn").evaluate_all("buttons => buttons.map((button) => button.getAttribute('aria-label'))") == [
            "About the surface option of Temperature", "About the bottom option of Temperature",
            "About the water column option of Temperature"]
        # beside the label, not in it, so that it does not tick the box
        assert page.locator("#clim-dialog label .info-btn").count() == 0
        surface = temperature.locator(".info-btn").nth(0)
        tick = temperature.locator("input[type=checkbox]").nth(1)
        surface.click()
        page.wait_for_selector("#example-dialog:not([hidden])")
        assert page.inner_text("#example-title") == "Surface" and page.inner_text("#example-where") == "Map climatologies"
        assert "sea surface" in page.inner_text("#example-text")
        # words only: nothing is drawn
        assert page.locator("#example-dialog img, #example-dialog canvas, #example-dialog svg").count() == 0
        # the pop-out it is over cannot be reached, and the box is as it was
        assert page.evaluate("() => document.getElementById('clim-dialog').inert")
        assert not tick.is_checked()
        # Escape closes only this one, and the i is where the focus goes back to
        page.keyboard.press("Escape")
        assert page.is_hidden("#example-dialog") and page.is_visible("#clim-dialog")
        assert not page.evaluate("() => document.getElementById('clim-dialog').inert")
        assert surface.evaluate("button => button === document.activeElement")
        # the Close button, and a click beside the card, close it too
        temperature.locator(".info-btn").nth(1).click()
        assert page.inner_text("#example-title") == "Bottom"
        page.click("#example-close")
        assert page.is_hidden("#example-dialog") and page.is_visible("#clim-dialog")
        everyone.locator(".info-btn").nth(2).click()
        assert page.inner_text("#example-title") == "Water column" and "Depth integral" in page.inner_text("#example-text")
        page.mouse.click(5, 5)
        assert page.is_hidden("#example-dialog") and page.is_visible("#clim-dialog")
        assert errors == []
    finally:
        page.close()
        app.close()


@pytest.mark.parametrize(
    "option, dialog, title, rows",
    [
        ("long_term", "clim-dialog", "Long-term time series", "#clim-body > tr"),
        ("seasonal", "clim-dialog", "Seasonal cycles", "#clim-body > tr"),
        ("map_changes", "chg-dialog", "Map temporal changes", "#chg-body > tr.chg-main"),
    ],
)
def test_the_other_pop_outs_with_layers_have_the_info_buttons_too(browser, tmp_path, option, dialog, title, rows):
    page, app, errors = open_analyze_option(browser, tmp_path, (2011, 2012), option, dialog, rows=INFO_ROWS)
    try:
        row = page.locator(rows)
        assert row.nth(0).locator(".info-btn").count() == 1 and row.nth(1).locator(".info-btn").count() == 3
        assert page.locator(f"#{dialog} .info-btn").count() == 3 + 1 + 3
        for number, name in enumerate(["Surface", "Bottom", "Water column"]):
            row.nth(1).locator(".info-btn").nth(number).click()
            page.wait_for_selector("#example-dialog:not([hidden])")
            assert page.inner_text("#example-title") == name and page.inner_text("#example-where") == title
            page.keyboard.press("Escape")
            assert page.is_hidden("#example-dialog") and page.is_visible(f"#{dialog}")
        assert errors == []
    finally:
        page.close()
        app.close()


def test_phenology_without_daily_output_says_so_and_closes_to_the_options(browser, tmp_path):
    page, app, errors = open_analyze_option(browser, tmp_path, (2013, 2014), "climatology", "clim-dialog", daily=False)
    try:
        page.click("#clim-cancel")
        page.click("#ao-phenology")
        page.wait_for_selector("#no-daily:not([hidden])")
        assert "daily output" in page.inner_text("#no-daily-text")
        assert "Temperature" in page.inner_text("#no-daily-text")
        assert page.is_hidden("#phen-dialog")
        page.click("#no-daily-close")
        assert page.is_hidden("#no-daily") and page.is_hidden("#phen-dialog")
        assert page.is_visible("#view-analyze-options")
        assert not page.evaluate("() => document.querySelector('main').inert")
        # Escape closes it too
        page.click("#ao-phenology")
        page.wait_for_selector("#no-daily:not([hidden])")
        page.keyboard.press("Escape")
        assert page.is_hidden("#no-daily")
        assert errors == []
    finally:
        page.close()
        app.close()


def test_the_phenology_pop_out_has_an_i_beside_its_title(browser, tmp_path):
    page, app, errors = open_analyze_option(browser, tmp_path, (2013, 2014), "phenology", "phen-dialog")
    try:
        button = page.locator("#phen-info .info-btn")
        assert button.count() == 1 and page.locator("#phen-dialog .info-btn").count() == 1
        assert page.evaluate("() => document.querySelector('#phen-info').previousElementSibling.id") == "phen-title"
        button.click()
        page.wait_for_selector("#example-dialog:not([hidden])")
        assert page.inner_text("#example-title") == "Peak day" and page.inner_text("#example-where") == "Map phenologies"
        assert "day of the year with the highest value" in page.inner_text("#example-text")
        page.keyboard.press("Escape")
        assert page.is_hidden("#example-dialog") and page.is_visible("#phen-dialog")
        assert errors == []
    finally:
        page.close()
        app.close()


def test_each_transect_has_an_i_beside_its_four_options(browser, tmp_path):
    page, app, errors = open_analyze_option(browser, tmp_path, (2013, 2014), "transects", "trans-dialog", rows=INFO_ROWS)
    try:
        section = page.locator("#trans-body .transect-var").nth(1)
        card = section.locator(".transect-card").first
        buttons = card.locator(".info-btn")
        assert buttons.count() == 4 and card.locator("label .info-btn").count() == 0
        section.get_by_role("button", name="Add a transect").click()
        assert section.locator(".info-btn").count() == 8
        for number, title in enumerate(["Surface climatology", "Surface changes", "Vertical climatology", "Vertical changes"]):
            buttons.nth(number).click()
            page.wait_for_selector("#example-dialog:not([hidden])")
            assert page.inner_text("#example-title") == title and page.inner_text("#example-where") == "Analyse transects"
            page.keyboard.press("Escape")
            assert page.is_hidden("#example-dialog") and page.is_visible("#trans-dialog")
        # the i beside a drop-down does not change it
        assert card.locator("select").first.input_value() == "" and not card.locator("input[type=checkbox]").nth(0).is_checked()
        assert errors == []
    finally:
        page.close()
        app.close()


class TestClosingTheBrowser:
    def test_it_quits_at_the_options_step(self, app, sim):
        app.page_timeout = 2
        to_options(app)
        assert get_json(app, "/api/state", page="a")[0] == 200
        assert app.closed.wait(15)
        assert app.closed_reason == "The browser window was closed."
        assert app.analyze_check is None

    def test_it_quits_while_the_variables_are_read(self, app, sim, monkeypatch):
        app.page_timeout = 2
        release = threading.Event()
        monkeypatch.setattr(app, "_read_streams", lambda *args, **kwargs: release.wait(30))
        assert post(app, "/api/choose", {"action": "analyze"})[0] == 200
        assert post(app, "/api/setup", {"form": setup_form(app)}) == (200, {"ok": True})
        assert get_json(app, "/api/state", page="a")[0] == 200
        try:
            assert app.closed.wait(15)
            assert app.closed_reason == "The browser window was closed."
        finally:
            release.set()

    def test_a_run_started_is_left_to_go_on(self, app, sim, runs, capsys):
        app.page_timeout = 1
        to_options(app)
        assert post(app, "/api/analyze_option", {"kind": "climatology", "form": CLIMATOLOGY})[0] == 200
        assert post(app, "/api/analyze_option", {"kind": "cores", "form": {"cores": "2"}})[0] == 200
        assert post(app, "/api/analyze_option", {"kind": "missing", "form": {"from": "0", "to": "0"}})[0] == 200
        assert post(app, "/api/analyze_option", {"kind": "figure_code", "form": {"choice": "no"}})[0] == 200
        assert get_json(app, "/api/state", page="a")[0] == 200
        assert post(app, "/api/analyze_run") == (200, {"ok": True})
        # starting, the run itself recorded and not started: never quit
        assert app.status == "starting"
        assert not app.closed.wait(3)
        assert capsys.readouterr().out.count("The OceanVal window has gone quiet") == 1


def test_the_required_tags_of_the_climatology_go_when_the_rows_are_filled(browser, tmp_path):
    """The column tags over the climatology's rows stay until a ticked
    variable has its years and a layer, and the red round a filled year box goes."""
    page, app, errors = open_analyze_option(browser, tmp_path, (2011, 2012, 2013, 2014), "climatology", "clim-dialog")
    try:
        years = page.locator("#clim-dialog thead tr:last-child .own-req")
        tick = page.locator("#clim-dialog thead .is-need .own-req")
        row = page.locator("#clim-body > tr").first
        assert years.nth(0).is_visible() and years.nth(1).is_visible() and tick.is_visible()
        use = row.locator("input[type=checkbox]").first
        if not use.is_checked():
            use.check()
        row.locator("input[type=checkbox]").nth(1).check()
        page.wait_for_selector("#clim-dialog thead .is-need .own-req", state="hidden")
        # a layer is not the years
        assert years.nth(0).is_visible() and years.nth(1).is_visible()
        start = page.locator("#clim-body input.is-year").nth(0)
        end = page.locator("#clim-body input.is-year").nth(1)
        red = "rgb(226, 165, 157)"
        assert start.evaluate("node => getComputedStyle(node).borderTopColor") == red
        start.fill("2012")
        end.fill("2013")
        page.wait_for_selector("#clim-dialog thead tr:last-child .own-req >> nth=1", state="hidden")
        assert years.nth(0).is_hidden() and years.nth(1).is_hidden()
        # the border changes colour over a transition
        page.wait_for_function(
            "([node, red]) => getComputedStyle(node).borderTopColor !== red", arg=[start.element_handle(), red]
        )
        end.fill("")
        page.wait_for_selector("#clim-dialog thead tr:last-child .own-req >> nth=1", state="visible")
        assert years.nth(0).is_hidden() and years.nth(1).is_visible()
        assert not errors
    finally:
        app.close()
