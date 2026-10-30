"""Aligning the simulation's grid with other simulations': the step a matchup
run asks for last in the oceanval window (oceanval.grid_align_app), and how the
grid they share is worked out (oceanval.grid_align)."""

import json
import os
import shutil
import textwrap
import time

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanval import grid_align, sim_unify
from oceanval.app import App
from viewer_simulations import fvcom_file
from test_app import get_json, leftover_dir, output_continue, post, through_output, wait_for  # noqa: F401 (leftover_dir is autouse)


def write_grid(path, res, land_west_of, fill=-1e20, bounds=False):
    """A simulation's file on a lon-lat grid of res degrees, with two times
    and three depths: 5 everywhere, but fill west of land_west_of. bounds:
    with the time and depth bounds variables NEMO writes, which CDO keeps
    ahead of the variable."""
    lon = np.arange(-10 + res / 2, 10, res)
    lat = np.arange(40 + res / 2, 60, res)
    values = np.full((2, 3, lat.size, lon.size), 5.0)
    values[:, :, :, lon < land_west_of] = fill
    data = xr.Dataset(
        {"temp": (("time", "depth", "lat", "lon"), values, {"long_name": "Temperature", "units": "degC"})},
        coords={"time": pd.date_range("2000-01-01", periods=2), "depth": [0.0, 10.0, 20.0], "lat": lat, "lon": lon},
    )
    data.lon.attrs.update(units="degrees_east", standard_name="longitude")
    data.lat.attrs.update(units="degrees_north", standard_name="latitude")
    data.depth.attrs.update(units="m", positive="down")
    if bounds:
        data["time"].attrs["bounds"] = "time_bnds"
        data["depth"].attrs["bounds"] = "depth_bnds"
        data["time_bnds"] = (("time", "bnds"), np.zeros((2, 2)))
        data["depth_bnds"] = (("depth", "bnds"), np.zeros((3, 2)))
        data["time_bnds"].encoding["units"] = "days since 2000-01-01"
        data = data[["time_bnds", "depth_bnds", "temp"]]
    data.to_netcdf(path)
    return str(path)


@pytest.fixture
def grids(tmp_path):
    """Two simulations' files: a 1 degree grid with land west of 5W, and a
    half degree grid with land west of 0, whose land is 0."""
    return (
        write_grid(tmp_path / "a.nc", 1.0, -5),
        write_grid(tmp_path / "b.nc", 0.5, 0, fill=0.0),
    )


class TestUnified:
    def test_the_missing_values_are_made_missing(self, grids):
        field = grid_align.field_for_cdo(grids[0], "temp", 0, [(-1e20, -1e20)])
        values = field.to_xarray()["temp"].values.squeeze()
        assert values.shape == (20, 20)
        assert np.isnan(values[:, :5]).all() and (values[:, 5:] == 5).all()

    @pytest.mark.parametrize("method", list(grid_align.REGRID_METHODS))
    def test_the_cells_every_simulation_has_are_1(self, grids, method):
        fields = {
            "1": grid_align.field_for_cdo(grids[0], "temp", 0, [(-1e20, -1e20)]),
            "2": grid_align.field_for_cdo(grids[1], "temp", 0, [(0, 0)]),
        }
        mask, shared, total = grid_align.unified(fields, "1", method)
        values = mask.values.squeeze()
        # on the baseline's grid: the cells east of 0 have values in both
        assert values.shape == (20, 20) and (shared, total) == (200, 400)
        assert (values[:, 10:] == 1).all() and np.isnan(values[:, :10]).all()
        assert grid_align.about(mask)["lon"] == "lon"

    @pytest.mark.parametrize("method", ["bil", "nn"])
    def test_on_a_box_there_is_no_baseline(self, grids, method):
        fields = {
            "1": grid_align.field_for_cdo(grids[0], "temp", 0, [(-1e20, -1e20)]),
            "2": grid_align.field_for_cdo(grids[1], "temp", 0, [(0, 0)]),
        }
        box = sim_unify.check_unify({"lon": [-9, 9], "lat": [41, 59], "res": 1}, ["1", "2"])
        mask, shared, total = grid_align.unified(fields, None, method, box=box)
        values = mask.values.squeeze()
        # nctoolkit's to_latlon has cells on both edges of the box: 19 by 19
        assert values.shape == (19, 19) and total == 361
        # the second simulation has land west of 0, so only the cells east of it have a value in both
        lon = mask.lon.values.squeeze()
        assert (values[:, lon >= 1] == 1).all() and np.isnan(values[:, lon <= -1]).all()
        assert 0 < shared <= total

    @pytest.mark.parametrize("method", ["nn", "bil"])
    def test_the_variable_is_not_a_bounds_variable(self, tmp_path, method):
        # NEMO's files have time_counter_bnds and deptht_bnds, which come first once CDO has selected the variable
        with_bounds = write_grid(tmp_path / "nemo.nc", 1.0, -5, bounds=True)
        plain = write_grid(tmp_path / "plain.nc", 0.5, 0, fill=0.0)
        field = grid_align.field_for_cdo(with_bounds, "temp", 0, [(-1e20, -1e20)])
        assert "time_bnds" in field.to_xarray().data_vars
        fields = {"1": field, "2": grid_align.field_for_cdo(plain, "temp", 0, [(0, 0)])}
        mask, shared, total = grid_align.unified(fields, "1", method)
        assert mask.values.squeeze().shape == (20, 20) and (shared, total) == (200, 400)
        fields = {"1": grid_align.field_for_cdo(plain, "temp", 0, [(0, 0)]), "2": field}
        shared, total = grid_align.unified(fields, "1", method)[1:]
        # bilinear regridding spreads the missing cells at the edge of the land
        assert total == 1600 and (shared == 800 if method == "nn" else 0 < shared <= 800)

    def test_fvcom_on_a_grid_with_bounds_variables(self, tmp_path):
        from oceanval import viewer_catalogue

        found = viewer_catalogue.scan(fvcom_file(str(tmp_path / "f.nc")), 0, wait=False)
        try:
            data, _ = found.field(0, ["temp"], 0, 0, [])
            mesh = found.mesh(0)
        finally:
            found.close()
        nemo = write_grid(tmp_path / "nemo.nc", 0.5, -10, bounds=True)
        fields = {"1": grid_align.fvcom_field(mesh, data), "2": grid_align.field_for_cdo(nemo, "temp", 0, [])}
        mask, shared, total = grid_align.unified(fields, "2", "nn", fvcom={"1": mesh})
        assert total == 1600 and shared == 3

    def test_an_fvcom_field_is_not_cropped(self, tmp_path, monkeypatch):
        from oceanval import viewer_catalogue

        found = viewer_catalogue.scan(fvcom_file(str(tmp_path / "f.nc")), 0, wait=False)
        try:
            data, _ = found.field(0, ["temp"], 0, 0, [])
            mesh = found.mesh(0)
        finally:
            found.close()
        cropped = []
        real = sim_unify.crop_to
        monkeypatch.setattr(sim_unify, "crop_to", lambda ds, box: cropped.append(box) or real(ds, box))
        nemo = write_grid(tmp_path / "nemo.nc", 0.5, -10)
        fields = {"1": grid_align.fvcom_field(mesh, data), "2": grid_align.field_for_cdo(nemo, "temp", 0, [])}
        grid_align.unified(fields, "2", "nn", fvcom={"1": mesh})
        # put on the baseline's grid by nearest neighbour, as it was, and not cropped
        assert cropped == []

    def test_field_variable(self):
        data = xr.Dataset({
            "time_bnds": (("time", "bnds"), np.zeros((1, 2))),
            "temp": (("time", "y", "x"), np.zeros((1, 3, 4))),
            "mask": (("y", "x"), np.zeros((3, 4))),
        })
        assert sim_unify.field_variable(data, "temp").name == "temp"
        assert sim_unify.field_variable(data, "grid").name == "temp"
        with pytest.raises(ValueError, match="bounds"):
            sim_unify.field_variable(data[["time_bnds"]], "grid")

    def test_on_the_other_grid_as_the_baseline(self, grids):
        fields = {
            "1": grid_align.field_for_cdo(grids[0], "temp", 0, [(-1e20, -1e20)]),
            "2": grid_align.field_for_cdo(grids[1], "temp", 0, [(0, 0)]),
        }
        mask, shared, total = grid_align.unified(fields, "2", "nn")
        assert mask.values.squeeze().shape == (40, 40) and (shared, total) == (800, 1600)

    def test_fvcom_on_another_grid_by_nearest_neighbour(self, grids, tmp_path):
        from oceanval import viewer_catalogue

        path = fvcom_file(str(tmp_path / "f.nc"))
        found = viewer_catalogue.scan(path, 0, wait=False)
        try:
            data, _ = found.field(0, ["temp"], 0, 0, [])
            mesh = found.mesh(0)
        finally:
            found.close()
        fields = {
            "1": grid_align.field_for_cdo(grids[1], "temp", 0, []),
            "2": grid_align.fvcom_field(mesh, data),
        }
        mask, shared, total = grid_align.unified(fields, "1", "nn", fvcom={"2": mesh})
        values = mask.values.squeeze()
        assert values.shape == (40, 40) and total == 1600
        # only the half degree cells inside the mesh (5W-4W, 50-51N, with the north-east corner cut out) have values
        # in both: 2 by 2 cells, less the one in the corner
        assert shared == 3 and (values == 1).sum() == 3
        lon, lat = np.meshgrid(mask["lon"].values, mask["lat"].values)
        assert ((lon[values == 1] > -5) & (lon[values == 1] < -4) & (lat[values == 1] > 50) & (lat[values == 1] < 51)).all()
        # the cell in the cut-out corner is outside the mesh, though a node is its nearest
        assert np.isnan(values[(lon > -4.5) & (lon < -4) & (lat > 50.5) & (lat < 51)]).all()

    def test_fvcom_cannot_be_the_baseline_or_use_other_methods(self, grids, tmp_path):
        fields = {"1": grid_align.field_for_cdo(grids[0], "temp", 0, []), "2": grid_align.field_for_cdo(grids[1], "temp", 0, [])}
        with pytest.raises(ValueError, match="cannot be the baseline"):
            grid_align.unified(fields, "2", "nn", fvcom={"2": None})
        with pytest.raises(ValueError, match="only be put on another grid by nearest neighbour"):
            grid_align.unified(fields, "1", "bil", fvcom={"2": None})

    def test_the_baseline_and_method_must_be_chosen(self, grids):
        fields = {"1": grid_align.field_for_cdo(grids[0], "temp")}
        with pytest.raises(ValueError, match="baseline"):
            grid_align.unified(fields, "3", "bil")
        with pytest.raises(ValueError, match="regridding method"):
            grid_align.unified(fields, "1", "best")

    def test_compare_simulations_still_regrids_bilinearly(self):
        asked = []

        class Dataset:
            def regrid(self, grid, method):
                asked.append(method)

            def to_latlon(self, **options):
                asked.append(options["method"])

            def run(self):
                pass

        sim_unify._regrid(Dataset(), {"method": "baseline", "is_baseline": False, "folder": "f"}, "k", "surface")
        sim_unify._regrid(Dataset(), {"method": "box", "lon": [0, 1], "lat": [0, 1], "res": 1}, "k", "surface")
        sim_unify._regrid(Dataset(), {"method": "baseline", "is_baseline": False, "folder": "f", "regrid": "nn"}, "k", "surface")
        assert asked == ["bil", "bil", "nn"]


def matchups_script(tmp_path, first):
    """A stand-in for matchup, asking whether the matchups are right, of a
    simulation whose first file is first."""
    details = {
        "kind": "matchups",
        "sim_dir": str(tmp_path),
        "years": [2000, 2000],
        "rows": [{"variable": "temperature", "title": "Temperature", "model_variable": "temp",
                  "pattern": "*.nc", "observations": ["COBE2"], "files": 1}],
        "files": {"*.nc": [first]},
    }
    script = tmp_path / "run.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json
            from oceanval import prompts
            question = "Are you happy with these matchups? (y/n) "
            answer = prompts.ask(question, ("y", "n"), details={details!r})
            print("answer:", answer)
            print("settings:", json.dumps(getattr(answer, "settings", {{}})))
            print("matching up")
            """
        )
    )
    return script


def at_the_question(app, tmp_path, action):
    """Start a run of action, and carry on to the step that asks whether the
    grid is to be aligned (with the output step's answer for matchup alone
    left out)."""
    app.action = action
    app.start_run(["matchup", str(matchups_script(tmp_path, "a.nc"))], "python run.py")
    wait_for(lambda: app.question is not None)
    number = app.question.id
    assert post(app, "/api/answer", {"id": number, "answer": "y"})[0] == 200
    return number


@pytest.fixture
def app(tmp_path, grids, monkeypatch):
    app = App(cwd=str(tmp_path))
    app.start()
    # the validate run, which is not started
    started = app.start_run
    monkeypatch.setattr(app, "start_run", lambda args, label: None if args[0] == "validate" else started(args, label))
    yield app
    app.close()


REPORT = {"cores": "1", "missing_from": "-1e20"}


class TestTheStep:
    def test_the_report_options_ask_it(self, app, tmp_path):
        number = at_the_question(app, tmp_path, "matchup_validate")
        output_continue(app)
        assert app.view == "report_options"
        status, reply = post(app, "/api/report", {"form": REPORT})
        assert status == 400
        assert reply["errors"] == {"align_grids": "Say whether the grid needs aligning with other simulations."}
        # a script stored for later does not need it
        assert app.validate_form["align_grids"] == ""

        assert post(app, "/api/report", {"form": dict(REPORT, align_grids="yes")})[0] == 200
        _, state = get_json(app, "/api/state")
        assert state["view"] == "align_grids"
        # matchup still waits, and cannot be answered from here
        assert state["question"]["id"] == number
        assert post(app, "/api/answer", {"id": number, "answer": "y"})[0] == 409
        time.sleep(0.3)
        assert "answer:" not in app.console.since(0, None)["text"]
        assert state["align"]["first"] == {"file": "a.nc", "path": str(tmp_path / "a.nc"), "variable": "temp"}
        assert state["align"]["titles"][:2] == ["This simulation", "Simulation 2"]
        # the simulation's panel starts with the missing values given
        assert state["align"]["form"]["panels"][0]["missing_from"] == "-1e20"

        # Back keeps the boxes, both steps'
        form = state["align"]["form"]
        form["panels"][1]["path"] = "b.nc"
        assert post(app, "/api/back", {"form": form})[0] == 200
        assert app.view == "report_options"
        assert app.validate_form["align_grids"] == "yes"
        assert app.align_form["panels"][1]["path"] == "b.nc"
        # and no carries on as before
        assert post(app, "/api/report", {"form": dict(REPORT, align_grids="no")})[0] == 200
        assert app.view == "running"
        wait_for(lambda: "matching up" in app.console.since(0, None)["text"])

    def test_matchup_alone_asks_it_with_where_the_matchups_go(self, app, tmp_path):
        number = at_the_question(app, tmp_path, "matchup")
        wait_for(lambda: app.view == "output")
        status, reply = post(app, "/api/output", {"form": {"out": "matchup.py", "cores": "1"}})
        assert status == 400 and "align_grids" in reply["errors"]
        output_continue(app, align_grids="yes")
        assert (app.view, app.question.id, app.align_from) == ("align_grids", number, "output")
        assert post(app, "/api/back", {"form": {}})[0] == 200
        assert app.view == "output"

    def test_the_panels_and_the_unified_grid(self, app, tmp_path):
        at_the_question(app, tmp_path, "matchup_validate")
        output_continue(app)
        assert post(app, "/api/report", {"form": dict(REPORT, align_grids="yes")})[0] == 200

        # the simulation's own, from its first file and variable
        status, reply = post(app, "/api/align_panel", {"number": 1, "panel": {"missing_from": "-1e20"}})
        assert status == 200 and reply["ok"], reply
        assert reply["variables"] == {"temp": "Temperature"} and reply["item"]["aspect"]
        # another's: its variables, once the file is given, and its map once one is chosen
        status, reply = post(app, "/api/align_panel", {"number": 2, "panel": {"path": "b.nc"}})
        assert reply["ok"] and reply["variables"] == {"temp": "Temperature"} and reply["item"] is None
        reply = post(app, "/api/align_panel", {"number": 2, "panel": {"path": "b.nc", "variable": "temp", "missing_from": "0"}})[1]
        assert reply["ok"] and reply["item"]
        reply = post(app, "/api/align_panel", {"number": 2, "panel": {"path": "b.nc", "variable": "salt"}})[1]
        assert reply == {**reply, "ok": False, "error": "salt is not a variable in the file that can be mapped."}
        reply = post(app, "/api/align_panel", {"number": 3, "panel": {"path": "c.nc"}})[1]
        assert not reply["ok"] and reply["error"] == "There is no file or directory there."
        reply = post(app, "/api/align_panel", {"number": 2, "panel": {"path": "b.nc", "variable": "temp", "missing_from": "x"}})[1]
        assert not reply["ok"] and reply["error"] == "This must be a number."

        panels = [{"missing_from": "-1e20"}, {"path": "b.nc", "variable": "temp", "missing_from": "0"}]
        status, reply = post(app, "/api/align_generate", {"form": {"panels": panels}})
        assert status == 400
        assert reply["errors"] == {"baseline": "Choose the baseline grid.", "method": "Choose a regridding method."}
        status, reply = post(app, "/api/align_generate", {"form": {"panels": panels[:1], "baseline": "1", "method": "nn"}})
        assert reply["errors"] == {"panels": "Draw at least one other simulation's grid."}
        status, reply = post(app, "/api/align_generate", {"form": {"panels": panels, "baseline": "2", "method": "nn"}})
        assert status == 200, reply
        assert (reply["shared"], reply["total"]) == (800, 1600) and reply["item"]["aspect"]
        # the boxes are kept
        assert app.align_form["baseline"] == "2"

        # Restart forgets them
        assert post(app, "/api/restart_all")[0] == 200
        assert app.align_form["panels"][1]["path"] == "" and app.align_first is None

    def test_the_state_says_what_generating_is_doing(self, app, tmp_path, monkeypatch):
        import threading

        at_the_question(app, tmp_path, "matchup_validate")
        output_continue(app)
        assert post(app, "/api/report", {"form": dict(REPORT, align_grids="yes")})[0] == 200
        assert get_json(app, "/api/state")[1]["align"]["stage"] is None
        # the first simulation is 1 degree on 20 by 20, the second half a degree: 2 is regridded to 1
        entered, release = threading.Event(), threading.Event()
        real = sim_unify.crop_to

        def held(ds, box):
            entered.set()
            assert release.wait(60)
            return real(ds, box)

        monkeypatch.setattr(sim_unify, "crop_to", held)
        panels = [{"missing_from": "-1e20"}, {"path": "b.nc", "variable": "temp", "missing_from": "0"}]
        done = []
        thread = threading.Thread(target=lambda: done.append(
            post(app, "/api/align_generate", {"form": {"panels": panels, "baseline": "1", "method": "nn"}})))
        thread.start()
        try:
            assert entered.wait(60)
            assert get_json(app, "/api/state")[1]["align"]["stage"] == "Cropping Simulation 2…"
        finally:
            release.set()
            thread.join(120)
        assert done[0][0] == 200 and done[0][1]["ok"], done
        # over: nothing is being done
        assert get_json(app, "/api/state")[1]["align"]["stage"] is None

    def test_a_directory_and_how_far_down_its_files_are(self, app, tmp_path, grids):
        import shutil

        for stream, path in (("grid_T", grids[0]), ("ptrc_T", grids[1])):
            (tmp_path / "sims" / "2000" / "01").mkdir(parents=True, exist_ok=True)
            shutil.copy(path, tmp_path / "sims" / "2000" / "01" / f"{stream}.nc")
        (tmp_path / "one").mkdir()
        shutil.copy(grids[1], tmp_path / "one" / "only.nc")
        at_the_question(app, tmp_path, "matchup_validate")
        output_continue(app)
        assert post(app, "/api/report", {"form": dict(REPORT, align_grids="yes")})[0] == 200

        def ask(**panel):
            return post(app, "/api/align_panel", {"number": 2, "panel": panel})[1]

        # how far down must be said, and the files are listed, not drawn, until one is chosen
        assert "how many directories down" in ask(path="sims")["error"]
        assert "no netCDF files 1 directory down" in ask(path="sims", ndown="1")["error"]
        reply = ask(path="sims", ndown="2")
        assert reply["ok"] and reply["files"] == ["2000/01/grid_T.nc", "2000/01/ptrc_T.nc"]
        assert (reply["files_total"], reply["file"], reply["is_file"], reply["item"]) == (2, "", False, None)
        # one is chosen, whatever else is typed in the box; the variables are its
        reply = ask(path="sims", ndown="2", file="2000/01/ptrc_T.nc")
        assert reply["file"] == "2000/01/ptrc_T.nc" and reply["variables"] == {"temp": "Temperature"} and reply["item"] is None
        reply = ask(path="sims", ndown="2", file="2000/01/ptrc_T.nc", variable="temp", missing_from="0")
        assert reply["item"]
        assert ask(path="sims", ndown="2", file="elsewhere.nc")["file"] == ""
        # a directory with one file uses it, and a file is used as it is
        reply = ask(path="one", ndown="0", variable="temp")
        assert reply["file"] == "only.nc" and reply["item"]
        reply = ask(path="one/only.nc", ndown="7")
        assert reply["is_file"] and reply["files"] == [] and reply["file"] == "" and reply["variables"]

        panels = [
            {"missing_from": "-1e20"},
            {"path": "sims", "ndown": "2", "file": "2000/01/ptrc_T.nc", "variable": "temp", "missing_from": "0"},
        ]
        status, reply = post(app, "/api/align_generate", {"form": {"panels": panels, "baseline": "2", "method": "nn"}})
        assert status == 200, reply
        assert (reply["shared"], reply["total"]) == (800, 1600)
        # no file chosen: the panel is not drawn
        panels[1]["file"] = ""
        status, reply = post(app, "/api/align_generate", {"form": {"panels": panels, "baseline": "1", "method": "nn"}})
        assert status == 400 and reply["errors"]["panels"] == "Draw at least one other simulation's grid."

    def test_fvcom_panels(self, app, tmp_path):
        fvcom_file(str(tmp_path / "f.nc"))
        at_the_question(app, tmp_path, "matchup_validate")
        output_continue(app)
        assert post(app, "/api/report", {"form": dict(REPORT, align_grids="yes")})[0] == 200
        assert post(app, "/api/align_panel", {"number": 1, "panel": {"missing_from": "-1e20"}})[1]["fvcom"] is False
        reply = post(app, "/api/align_panel", {"number": 2, "panel": {"path": "f.nc", "variable": "temp"}})[1]
        assert reply["ok"] and reply["fvcom"] is True and reply["item"]
        assert "nearest neighbour" in get_json(app, "/api/state")[1]["align"]["fvcom_note"]

        panels = [{"missing_from": "-1e20"}, {"path": "f.nc", "variable": "temp"}]
        status, reply = post(app, "/api/align_generate", {"form": {"panels": panels, "baseline": "1", "method": "nn"}})
        assert status == 200, reply
        assert 0 < reply["shared"] < reply["total"] and reply["item"]["aspect"]
        status, reply = post(app, "/api/align_generate", {"form": {"panels": panels, "baseline": "1", "method": "bil"}})
        assert status == 400 and "nearest neighbour" in reply["errors"]["method"]
        status, reply = post(app, "/api/align_generate", {"form": {"panels": panels, "baseline": "2", "method": "nn"}})
        assert status == 400 and "cannot be the baseline" in reply["errors"]["baseline"]

    def test_nothing_is_drawn_once_the_step_is_over(self, app, tmp_path):
        at_the_question(app, tmp_path, "matchup_validate")
        output_continue(app)
        assert post(app, "/api/align_panel", {"number": 1, "panel": {}})[0] == 409
        assert post(app, "/api/align_generate", {"form": {}})[0] == 409
        assert post(app, "/api/align_continue", {"form": {}})[0] == 409

    @pytest.mark.parametrize("action", ["matchup_validate", "matchup"])
    def test_the_matchups_are_masked_by_the_grid_generated(self, app, tmp_path, action):
        at_the_question(app, tmp_path, action)
        # the script's settings, as the recipes window would have left them
        written = []
        app._script_writer = (lambda *choices, **report: written.append(choices[2]), ({}, [], {}, {}))
        if action == "matchup":
            output_continue(app, align_grids="yes", missing_from="-1e20")
        else:
            output_continue(app)
            assert post(app, "/api/report", {"form": dict(REPORT, align_grids="yes")})[0] == 200
        assert app.view == "align_grids"
        _, state = get_json(app, "/api/state")
        assert state["align"]["from"] == ("output" if action == "matchup" else "report_options")

        panels = [{"missing_from": "-1e20"}, {"path": "b.nc", "variable": "temp", "missing_from": "0"}]
        form = {"panels": panels, "baseline": "2", "method": "bil"}
        # only once it is generated
        status, reply = post(app, "/api/align_continue", {"form": form})
        assert status == 400 and reply["errors"] == {"panels": "Generate the unified grid with these choices first."}
        assert post(app, "/api/align_generate", {"form": form})[0] == 200
        # and with the boxes it was generated with
        status, reply = post(app, "/api/align_continue", {"form": dict(form, method="nn")})
        assert status == 400 and "panels" in reply["errors"]
        assert app.view == "align_grids"

        made = app.align_made["path"]
        # nothing is written in the output directory before the matchups are confirmed
        assert os.path.dirname(made) == os.path.dirname(app._draft_script())
        with xr.open_dataset(made, mask_and_scale=False) as data:
            # 0 where every simulation has a value, missing elsewhere, as sim_unify writes it
            assert data[grid_align.NAME].attrs["_FillValue"] == sim_unify.FILL
            assert set(np.unique(data[grid_align.NAME].values)) == {0.0, sim_unify.FILL}

        assert post(app, "/api/align_continue", {"form": form}) == (200, {"ok": True})
        assert app.view == "running"
        wait_for(lambda: "matching up" in app.console.since(0, None)["text"])
        text = app.console.since(0, None)["text"]
        settings = json.loads(text.split("settings: ", 1)[1].splitlines()[0])
        # sent along with yes, with the cores and missing values
        assert settings["unified_grid"] == {"path": made, "method": "bil"}
        assert settings["cores"] == 1
        if action == "matchup_validate":
            assert "live_validation" in settings
        # the script names the copy matchup keeps
        kept = os.path.join(str(tmp_path), "oceanval_matchups", "unified_grid.nc")
        assert written[-1]["unified_grid"] == {"path": kept, "method": "bil"}

    def test_no_after_back_sends_no_grid(self, app, tmp_path):
        at_the_question(app, tmp_path, "matchup_validate")
        output_continue(app)
        app._run_output["unified_grid"] = {"path": "old.nc", "method": "nn"}
        assert post(app, "/api/report", {"form": dict(REPORT, align_grids="no")})[0] == 200
        wait_for(lambda: "matching up" in app.console.since(0, None)["text"])
        text = app.console.since(0, None)["text"]
        assert "unified_grid" not in json.loads(text.split("settings: ", 1)[1].splitlines()[0])


def test_the_step_in_a_browser(browser, tmp_path, grids, monkeypatch):
    """Yes to aligning the grid shows the simulation's own panel, with the
    second simulation's and a plus for more, and Match up and validate, once
    the grid is generated."""
    app = App(cwd=str(tmp_path))
    # each stage of generating is held for a moment, as it may be over before the page is told of it
    said_progress = app._align_progress
    monkeypatch.setattr(app, "_align_progress", lambda *args: said_progress(*args) or time.sleep(0.8))
    started = app.start_run
    monkeypatch.setattr(app, "start_run", lambda args, label: None if args[0] == "validate" else started(args, label))
    app.action = "matchup_validate"
    app.start_run(["matchup", str(matchups_script(tmp_path, "a.nc"))], "python run.py")
    url = app.start()
    try:
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(url)
        page.wait_for_selector("#review:not([hidden])")
        page.click("#actions button:has-text('Yes')")
        page.wait_for_selector("#view-output:not([hidden])")
        # matchup alone's: to validate too, it is asked with the report options
        assert page.is_hidden("#row-f-align")
        through_output(page)
        page.wait_for_selector("#view-validate:not([hidden])")
        page.select_option("#v-cores", "1")
        page.fill("#v-missing_from", "-1e20")
        # required, with no default
        assert page.is_visible("#v-group-align") and page.is_disabled("#build")
        assert page.is_enabled("#save-script")
        page.check("#v-align-yes")
        assert page.text_content("#build") == "Continue"
        page.click("#build")
        page.wait_for_selector("#view-align-grids:not([hidden])")

        assert page.text_content("#title") == "Align the simulation grid with other simulations"
        # two simulations to start, a plus to the right of the second, then the unified grid
        assert page.locator("#ag-grid .ag-card").count() == 3
        assert page.locator("#ag-card-3, #ag-card-4, #ag-card-5").count() == 0
        assert page.text_content("#ag-add").strip() == "+Add simulation 3"
        assert page.locator("#actions button").all_text_contents() == ["Back", "Match up and validate"]
        assert page.is_disabled("#ag-continue")
        tops = [page.locator(selector).bounding_box() for selector in ("#ag-card-1", "#ag-card-2", "#ag-add-tile", "#ag-card-6")]
        lefts = [box["x"] for box in tops]
        assert tops[0]["y"] == tops[1]["y"] == tops[2]["y"] and tops[3]["y"] > tops[0]["y"] and lefts[0] < lefts[1] < lefts[2]
        page.wait_for_selector("#ag-map-1.is-drawn", timeout=60000)
        assert page.is_disabled("#ag-generate")

        # a directory, how far down its files are, and one of them
        (tmp_path / "sims" / "grid_T").mkdir(parents=True)
        shutil.copy(tmp_path / "b.nc", tmp_path / "sims" / "grid_T" / "b.nc")
        shutil.copy(tmp_path / "a.nc", tmp_path / "sims" / "grid_T" / "a.nc")
        assert page.locator("#ag-card-2 [data-browse='ag-2-path']").count() == 1
        page.fill("#ag-2-path", str(tmp_path / "sims"))
        page.fill("#ag-2-ndown", "1")
        page.wait_for_selector("#ag-2-files details.vars", timeout=60000)
        assert page.text_content("#ag-2-found") == "2 netCDF files 1 directory down."
        assert page.text_content("#ag-2-file") == "none chosen"
        assert "Choose the file" in page.text_content("#ag-map-2")
        page.click("#ag-2-files summary")
        page.fill("#ag-2-files .vars__search", "b.nc")
        page.click("#ag-2-files .vars__scroll >> text=grid_T/b.nc")
        page.wait_for_function("() => document.getElementById('ag-2-file').textContent === 'grid_T/b.nc'")
        page.wait_for_selector("#ag-2-pick details.vars", timeout=60000)
        page.fill("#ag-2-variable", "temp")
        page.fill("#ag-2-missing_from", "0")
        page.wait_for_selector("#ag-map-2.is-drawn", timeout=60000)
        page.select_option("#ag-baseline", "1")
        page.select_option("#ag-method", "bil")
        # the spinner beside the text while it is generated, which may be over in a moment, so it is watched for,
        # with each thing said to be done
        page.evaluate("""(id) => { window.sawSpinner = []; new MutationObserver(() => {
            const busy = document.querySelector(`#${id} .ag-map__empty.is-busy`);
            if (busy && busy.querySelector('.identify__spinner') && !window.sawSpinner.includes(busy.textContent)) window.sawSpinner.push(busy.textContent); })
            .observe(document.getElementById(id), { childList: true, subtree: true }); }""", "ag-map-6")
        page.click("#ag-generate")
        page.wait_for_selector("#ag-map-6.is-drawn", timeout=60000)
        assert page.text_content("#ag-6-msg") == "200 of 400 cells have a value in every simulation."
        said = page.evaluate("() => window.sawSpinner")
        assert said[0] == "Putting the grids on the baseline's grid…"
        assert said[1:] == ["Cropping Simulation 2…", "Regridding Simulation 2…", "Unifying the simulations…"]
        page.wait_for_function("() => !document.getElementById('ag-continue').disabled")
        # a change to the boxes needs Generate again
        page.select_option("#ag-method", "nn")
        assert page.is_disabled("#ag-continue")
        # back as it was generated
        page.select_option("#ag-method", "bil")
        assert page.is_enabled("#ag-continue")

        # an FVCOM simulation: only nearest neighbour, and it cannot be the baseline
        fvcom_file(str(tmp_path / "f.nc"))
        assert page.is_enabled("#ag-method")
        # the plus adds the next simulation, and the unified grid made is still the one shown
        page.click("#ag-add")
        assert page.locator("#ag-grid .ag-card").count() == 4
        assert page.is_enabled("#ag-continue")
        assert page.text_content("#ag-add").strip() == "+Add simulation 4"
        assert page.evaluate("() => document.activeElement.id") == "ag-3-path"
        page.fill("#ag-3-path", str(tmp_path / "f.nc"))
        page.wait_for_selector("#ag-3-pick details.vars", timeout=60000)
        page.fill("#ag-3-variable", "temp")
        page.wait_for_selector("#ag-map-3.is-drawn", timeout=60000)
        # the page sets the method just after the map is drawn
        page.wait_for_function("() => document.getElementById('ag-method').disabled")
        assert page.input_value("#ag-method") == "nn"
        assert "nearest neighbour" in page.text_content("#ag-msg")
        assert page.locator("#ag-baseline option").all_text_contents() == ["Choose the baseline", "This simulation", "Simulation 2"]
        page.click("#ag-generate")
        page.wait_for_selector("#ag-map-6.is-drawn", timeout=60000)
        assert "have a value in every simulation" in page.text_content("#ag-6-msg")
        page.fill("#ag-3-path", "")
        page.wait_for_function("() => !document.getElementById('ag-method').disabled")

        # all five, and the plus is gone
        page.click("#ag-add")
        page.click("#ag-add")
        assert page.locator("#ag-grid .ag-card").count() == 6 and page.locator("#ag-add").count() == 0
        page.fill("#ag-5-path", str(tmp_path / "sims"))

        page.click("#actions button:has-text('Back')")
        page.wait_for_selector("#view-validate:not([hidden])")
        assert page.is_checked("#v-align-yes")
        assert app.align_form["panels"][1]["path"] == str(tmp_path / "sims")
        assert app.align_form["panels"][1]["file"] == "grid_T/b.nc"
        # coming back shows every simulation with something in its boxes, the plus after them
        page.click("#build")
        page.wait_for_selector("#view-align-grids:not([hidden])")
        assert page.locator("#ag-grid .ag-card").count() == 6 and page.locator("#ag-add").count() == 0
        assert page.input_value("#ag-5-path") == str(tmp_path / "sims")
    finally:
        app.close()
