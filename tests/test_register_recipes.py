"""Registering recipes of your own in the oceanval window: the pages' requests,
and the pages in a browser."""

import functools
import http.server
import json
import os
import threading

import pytest

from oceanval import recipe_forms, user_recipes
from simulations import write_simulation
from test_app import (  # noqa: F401 - the fixtures
    SETUP_FORM,
    app,
    get,
    get_json,
    leftover_dir,
    page_state,
    post,
    recipe_settings,
    runs,
    units_match,
    wait_for,
)

NITRATE_DIR = os.path.abspath("data/evaldata/gridded/nws/nitrate")
NITRATE = os.path.join(NITRATE_DIR, "model_2000.nc")
POINTS = os.path.abspath("data/chl/obs_csv")


def gridded_form(**changes):
    form = recipe_forms.default_form("gridded")
    form.update(
        variable="nitrate", source="MySat", source_info="My nitrate", units="mmol N/m^3",
        where="local", obs_path=NITRATE, obs_variable="N3_n", climatology="yes",
    )
    form.update(changes)
    return form


def point_form(**changes):
    form = recipe_forms.default_form("point")
    form.update(
        variable="dic", source="DicPts", source_info="DIC samples", units="umol/kg",
        where="global", obs_path=POINTS, short_title="DIC",
    )
    form.update(changes)
    return form


def register(app, kind="gridded"):
    assert post(app, "/api/choose", {"action": "register"})[0] == 200
    assert post(app, "/api/register_kind", {"kind": kind})[0] == 200


@pytest.fixture
def served():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=NITRATE_DIR)
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/model_2000.nc"
    server.shutdown()


class TestSteps:
    def test_registering_is_a_choice_on_the_first_page(self, app):
        body = get(app, "/")[1]
        assert 'data-action="register"' in body
        assert "Add your own validation data for future use" in body

    def test_the_way_through_and_back(self, app):
        assert post(app, "/api/choose", {"action": "register"})[0] == 200
        assert (app.view, app.action) == ("register_kind", "register")
        assert post(app, "/api/register_kind", {"kind": "nonsense"})[0] == 409
        assert post(app, "/api/register_kind", {"kind": "point"})[0] == 200
        assert app.view == "register_point"
        # Back keeps what was entered
        assert post(app, "/api/back", {"form": point_form()})[0] == 200
        assert app.view == "register_kind"
        state = app.state(console=False)["register"]
        assert state["kind"] == "point"
        assert state["forms"]["point"]["source"] == "DicPts"
        assert post(app, "/api/register_kind", {"kind": "gridded"})[0] == 200
        assert post(app, "/api/back")[0] == 200
        assert post(app, "/api/back")[0] == 200
        assert app.view == "start"

    def test_nothing_happens_out_of_turn(self, app):
        assert post(app, "/api/register_kind", {"kind": "point"})[0] == 409
        assert post(app, "/api/recipe_save", {"kind": "gridded", "form": gridded_form()})[0] == 409
        assert post(app, "/api/recipe_remove", {"variable": "a", "key": "b", "where": "local"})[0] == 409
        assert post(app, "/api/register_done")[0] == 409

    def test_a_kind_that_is_not_one(self, app):
        register(app)
        assert post(app, "/api/recipe_names", {"kind": "other", "form": {}})[0] == 400
        assert post(app, "/api/recipe_data", {"kind": "other", "form": {}})[0] == 400
        assert post(app, "/api/recipe_save", {"kind": "other", "form": {}})[0] == 400

    def test_finishing_goes_back_to_the_start(self, app):
        register(app)
        assert post(app, "/api/register_done")[0] == 200
        assert (app.view, app.action) == ("start", None)


class TestChecks:
    def test_the_names(self, app):
        register(app)
        status, reply = post(app, "/api/recipe_names", {"kind": "gridded", "form": gridded_form(source="WOA23")})
        assert status == 200
        assert "own recipes" in reply["errors"]["source"]
        status, reply = post(app, "/api/recipe_names", {"kind": "gridded", "form": gridded_form()})
        assert reply["errors"] == {}
        assert reply["info"]["recipe"] == 'recipe={"nitrate": "mysat"}'
        assert reply["info"]["labels"] == ["nitrate concentration", "nitrate concentration", "Nitrate"]

    def test_the_data_on_this_machine(self, app):
        register(app)
        status, reply = post(app, "/api/recipe_data", {"kind": "gridded", "form": gridded_form()})
        assert status == 200 and reply["ok"] and reply["running"] is False
        assert reply["found"]["variables"][0]["name"] == "N3_n"
        status, reply = post(app, "/api/recipe_data", {"kind": "gridded", "form": gridded_form(obs_path="/nowhere.nc")})
        assert status == 200 and not reply["ok"]
        assert "no file or directory" in reply["error"]

    def test_data_that_is_relative_is_from_the_directory_worked_in(self, app, tmp_path):
        register(app)
        os.symlink(NITRATE, tmp_path / "obs.nc")
        reply = post(app, "/api/recipe_data", {"kind": "gridded", "form": gridded_form(obs_path="obs.nc")})[1]
        assert reply["ok"] and reply["found"]["obs_path"] == str(tmp_path / "obs.nc")

    def test_point_data(self, app):
        register(app, "point")
        reply = post(app, "/api/recipe_data", {"kind": "point", "form": point_form()})[1]
        assert reply["ok"] and reply["found"]["files"] == 1

    def test_data_on_a_server_is_a_job(self, app, served):
        register(app)
        form = gridded_form(location="url", obs_path=served)
        status, reply = post(app, "/api/recipe_data", {"kind": "gridded", "form": form})
        assert (status, reply["ok"], reply["running"]) == (200, True, True)
        wait_for(lambda: (app.state(console=False)["register"]["job"] or {}).get("status") in ("ok", "failed"))
        job = app.state(console=False)["register"]["job"]
        assert job["status"] == "ok", job
        assert job["found"]["variables"][0]["name"] == "N3_n"
        assert job["signature"] == ["url", [served]]

    def test_a_server_that_cannot_be_reached(self, app):
        register(app)
        form = gridded_form(location="thredds", obs_path="http://127.0.0.1:1/x.nc")
        post(app, "/api/recipe_data", {"kind": "gridded", "form": form})
        wait_for(lambda: (app.state(console=False)["register"]["job"] or {}).get("status") == "failed")
        job = app.state(console=False)["register"]["job"]
        assert "connection was refused" in job["error"]
        status, reply = post(app, "/api/recipe_save", {"kind": "gridded", "form": form})
        assert status == 400 and "connection was refused" in reply["errors"]["obs_path"]
        assert not os.path.exists(user_recipes.local_path(app.cwd))

    def test_an_address_that_cannot_be_used_is_refused_at_once(self, app):
        register(app)
        form = gridded_form(location="thredds", obs_path="https://x.org/thredds/fileServer/a.nc")
        reply = post(app, "/api/recipe_data", {"kind": "gridded", "form": form})[1]
        assert not reply["ok"] and "download link" in reply["error"]
        assert app.state(console=False)["register"]["job"] is None

    def test_leaving_stops_the_check(self, app):
        register(app)
        form = gridded_form(location="thredds", obs_path="http://127.0.0.1:1/x.nc")
        post(app, "/api/recipe_data", {"kind": "gridded", "form": form})
        post(app, "/api/back")
        assert app.state(console=False)["register"]["job"] is None


class TestSaving:
    def test_a_gridded_recipe_is_saved_to_this_directory(self, app, tmp_path):
        register(app)
        status, reply = post(app, "/api/recipe_save", {"kind": "gridded", "form": gridded_form()})
        assert status == 200, reply
        assert reply["saved"]["recipe"] == 'recipe={"nitrate": "mysat"}'
        data = json.load(open(tmp_path / ".oceanvalrc"))
        assert data["recipes"]["nitrate"]["mysat"]["obs_variable"] == "N3_n"
        assert not os.path.exists(user_recipes.global_path())
        state = app.state(console=False)["register"]
        assert [r["key"] for r in state["recipes"]] == ["mysat"]
        assert [s["recipe"] for s in state["saved"]] == ['recipe={"nitrate": "mysat"}']
        # the next recipe starts afresh, in the same place
        assert state["forms"]["gridded"]["source"] == ""
        assert state["forms"]["gridded"]["where"] == "local"

    def test_a_point_recipe_is_saved_everywhere(self, app, tmp_path):
        register(app, "point")
        status, reply = post(app, "/api/recipe_save", {"kind": "point", "form": point_form()})
        assert status == 200, reply
        data = json.load(open(user_recipes.global_path()))
        assert data["recipes"]["dic"]["dicpts"]["short_title"] == "DIC"
        assert not os.path.exists(tmp_path / ".oceanvalrc")

    def test_what_cannot_be_saved_is_sent_back(self, app, tmp_path):
        register(app)
        status, reply = post(
            app, "/api/recipe_save",
            {"kind": "gridded", "form": gridded_form(source="", units="", obs_variable="x", where="")},
        )
        assert status == 400
        assert {"source", "units", "where"} <= set(reply["errors"])
        assert not os.path.exists(tmp_path / ".oceanvalrc")

    def test_a_recipe_of_the_same_name_is_refused_and_the_other_files_is_warned_of(self, app):
        register(app)
        assert post(app, "/api/recipe_save", {"kind": "gridded", "form": gridded_form(where="global")})[0] == 200
        status, reply = post(app, "/api/recipe_save", {"kind": "gridded", "form": gridded_form(where="global")})
        assert status == 400 and "already" in reply["errors"]["source"]
        names = post(app, "/api/recipe_names", {"kind": "gridded", "form": gridded_form()})[1]
        assert names["errors"] == {} and "used in its place" in names["warnings"][0]
        status, reply = post(app, "/api/recipe_save", {"kind": "gridded", "form": gridded_form()})
        assert status == 200 and "used in its place" in reply["warnings"][0]

    def test_a_download_is_saved_once_it_has_been_checked(self, app, served):
        register(app)
        form = gridded_form(location="url", obs_path=served)
        assert "Check the data first" in post(app, "/api/recipe_save", {"kind": "gridded", "form": form})[1]["errors"]["obs_path"]
        post(app, "/api/recipe_data", {"kind": "gridded", "form": form})
        wait_for(lambda: (app.state(console=False)["register"]["job"] or {}).get("status") == "ok")
        status, reply = post(app, "/api/recipe_save", {"kind": "gridded", "form": form})
        assert status == 200, reply
        saved = user_recipes.get("nitrate", "mysat", app.cwd)
        assert (saved["location"], saved["obs_path"]) == ("url", served)

    def test_recipes_are_removed(self, app):
        register(app)
        post(app, "/api/recipe_save", {"kind": "gridded", "form": gridded_form()})
        body = {"variable": "nitrate", "key": "mysat", "where": "local"}
        assert post(app, "/api/recipe_remove", dict(body, where="nowhere"))[0] == 400
        assert post(app, "/api/recipe_remove", body)[0] == 200
        assert post(app, "/api/recipe_remove", body)[0] == 409
        assert app.state(console=False)["register"]["recipes"] == []

    def test_a_file_that_cannot_be_read_is_not_written_over(self, app, tmp_path):
        register(app)
        (tmp_path / ".oceanvalrc").write_text("{broken")
        with pytest.warns(UserWarning):
            status, reply = post(app, "/api/recipe_save", {"kind": "gridded", "form": gridded_form()})
        assert status == 400 and "could not be read" in reply["errors"][""]
        assert (tmp_path / ".oceanvalrc").read_text() == "{broken"
        assert "local" in app.state(console=False)["register"]["problems"]


class TestUsingThem:
    def test_a_registered_recipe_is_offered_ticked_and_written_into_the_script(self, app, tmp_path, runs):
        register(app)
        assert post(app, "/api/recipe_save", {"kind": "gridded", "form": gridded_form()})[0] == 200
        post(app, "/api/register_done")
        write_simulation(tmp_path / "sim", tracers=True)
        post(app, "/api/choose", {"action": "matchup"})
        assert post(app, "/api/setup", {"form": SETUP_FORM})[0] == 200
        assert post(app, "/api/own_data", {"answer": False})[0] == 200
        wait_for(lambda: app.view == "recipes")

        rows = {row["variable"]: row for row in page_state(get(app, "/recipes/")[1])["rows"]}
        mine = [d for d in rows["nitrate"]["gridded"] if d["user"]]
        assert [(d["recipe"], d["label"], d["region"], d["ticked"]) for d in mine] == [
            ("mysat", "MySat", "Yours", True)
        ]
        post_rows = [{"variable": "nitrate", "model_variable": "N3_n", "selected": ["mysat"]}]
        status, reply = post(app, "/recipes/write", {"rows": post_rows, "settings": recipe_settings(app)})
        assert status == 200, reply
        wait_for(lambda: app.view == "units_table" and app.units_rows is not None)
        row = [r for r in app.units_rows if r["title"] == "nitrate (mysat)"][0]
        assert (row["obs_units"], row["obs_variable"]) == ("mmol N/m^3", "N3_n")
        units_match(app)
        wait_for(lambda: runs)
        script = (tmp_path / "matchup.py").read_text()
        assert 'recipe={"nitrate": "mysat"},' in script
        assert "Your recipes, from .oceanvalrc" in script
        compile(script, "matchup.py", "exec")

    def test_a_variable_that_is_not_identified_is_left_blank(self, app, tmp_path):
        register(app, "point")
        assert post(app, "/api/recipe_save", {"kind": "point", "form": point_form(where="local")})[0] == 200
        post(app, "/api/register_done")
        write_simulation(tmp_path / "sim", tracers=True)
        post(app, "/api/choose", {"action": "matchup"})
        post(app, "/api/setup", {"form": SETUP_FORM})
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "recipes")
        rows = {row["variable"]: row for row in page_state(get(app, "/recipes/")[1])["rows"]}
        dic = rows["dic"]
        assert dic["model_variable"] == ""
        assert [d["ticked"] for d in dic["point"]] == [False]
        assert dic["point"][0]["label"] == "DicPts"


def own_gridded(**changes):
    """The own data form of a gridded dataset, as the page sends it."""
    form = {
        "name": "nitrate", "source": "MySat", "model_variable": "N3_n",
        "obs_path": NITRATE, "obs_variable": "N3_n", "climatology": "yes",
    }
    form.update(changes)
    return form


def own_point(**changes):
    form = {"name": "dic", "source": "DicPts", "model_variable": "thetao", "obs_path": POINTS}
    form.update(changes)
    return form


def own_step(app, tmp_path, kind="gridded"):
    """Go to the own data step for kind, in a matchup run."""
    write_simulation(tmp_path / "sim", tracers=True)
    assert post(app, "/api/choose", {"action": "matchup"})[0] == 200
    assert post(app, "/api/setup", {"form": SETUP_FORM})[0] == 200
    assert post(app, "/api/own_data", {"answer": True})[0] == 200
    if kind == "gridded":
        assert post(app, "/api/own_next")[0] == 200
    assert app.view == f"{kind}_data"


def future_state(app):
    return app.state(console=False)["own"]["future"]


class TestFromOwnData:
    """Adding the user's own data for use now and in future: saved as a
    recipe from the own data step, as well as added to the run."""

    def test_gridded_data_is_saved_everywhere_with_what_is_assumed(self, app, tmp_path):
        own_step(app, tmp_path)
        status, reply = post(app, "/api/own_future", {"kind": "gridded", "form": own_gridded()})
        assert status == 200, reply
        assert app.view == "own_future"
        # nothing is added or saved until the step is done
        assert app.own_data["gridded"] == []
        future = future_state(app)
        assert future["recipe"] == 'recipe={"nitrate": "mysat"}'
        # OceanVal names nitrate, so its names are not asked for
        assert future["labels"] == ["nitrate concentration", "nitrate concentration", "Nitrate"]
        assert [(box["name"], box["assumed"]) for box in future["boxes"]] == [
            ("units", "mmol N/m^3"), ("source_info", "Source for MySat"), ("where", "global"),
        ]
        assert future["data"] == {"status": "ok", "error": None}
        assert future["where"]["global"] == {
            "path": user_recipes.global_path(), "errors": [], "warnings": [],
        }

        status, reply = post(app, "/api/own_future_save", {"boxes": {}})
        assert status == 200, reply
        assert (reply["saved"]["recipe"], reply["saved"]["where"]) == ('recipe={"nitrate": "mysat"}', "global")
        saved = user_recipes.get("nitrate", "mysat", app.cwd)
        assert saved["where"] == "global"
        assert (saved["units"], saved["source_info"], saved["obs_variable"]) == ("mmol N/m^3", "Source for MySat", "N3_n")
        assert (saved["location"], saved["climatology"], saved["depth_resolved"]) == ("disk", True, False)
        assert not os.path.exists(tmp_path / ".oceanvalrc")
        # and added to the run, as for use now only
        assert app.view == "gridded_data"
        assert app.own_data["gridded"] == [
            {"name": "nitrate", "source": "MySat", "model_variable": "N3_n",
             "obs_path": NITRATE, "obs_variable": "N3_n", "climatology": True},
        ]
        state = app.state(console=False)["own"]
        assert state["saved"]["gridded"][0]["recipe"] == 'recipe={"nitrate": "mysat"}'
        assert state["future"] is None

    def test_point_data_needs_its_units_and_uses_what_is_typed_now_too(self, app, tmp_path):
        own_step(app, tmp_path, "point")
        assert post(app, "/api/own_future", {"kind": "point", "form": own_point()})[0] == 200
        future = future_state(app)
        # csv files do not say what their units are, and dic is a variable of the user's own
        assert {box["name"]: box["assumed"] for box in future["boxes"]} == {
            "units": None, "source_info": "Source for DicPts",
            "long_name": "dic", "short_name": "dic", "short_title": "Dic", "where": "global",
        }
        assert future["labels"] is None

        status, reply = post(app, "/api/own_future_save", {"boxes": {"where": "local"}})
        assert status == 400 and set(reply["errors"]) == {"units"}
        assert not os.path.exists(tmp_path / ".oceanvalrc")
        assert app.own_data["point"] == []

        typed = {"units": "umol/kg", "source_info": "DIC samples", "short_title": "DIC", "where": "local"}
        status, reply = post(app, "/api/own_future_save", {"boxes": typed})
        assert status == 200, reply
        saved = json.load(open(tmp_path / ".oceanvalrc"))["recipes"]["dic"]["dicpts"]
        assert (saved["units"], saved["source_info"]) == ("umol/kg", "DIC samples")
        assert (saved["long_name"], saved["short_name"], saved["short_title"]) == ("dic", "dic", "DIC")
        # what was typed for the report is used now as well, and what was
        # assumed is left to the call, which assumes the same
        entry = app.own_data["point"][0]
        assert (entry["source_info"], entry["short_title"]) == ("DIC samples", "DIC")
        assert "long_name" not in entry
        # a dataset added for now only is not marked as saved
        assert post(app, "/api/own_add", {"kind": "point", "form": own_point(source="Other")})[0] == 200
        saved = app.state(console=False)["own"]["saved"]["point"]
        assert [item and item["where"] for item in saved] == ["local", None]
        # taking it out of the run leaves the recipe saved
        assert post(app, "/api/own_remove", {"kind": "point", "index": 0})[0] == 200
        assert app.state(console=False)["own"]["saved"]["point"] == [None]
        assert user_recipes.get("dic", "dicpts", app.cwd) is not None

    def test_what_a_recipe_cannot_be_is_said_on_the_form(self, app, tmp_path):
        own_step(app, tmp_path)
        status, reply = post(app, "/api/own_future", {"kind": "gridded", "form": own_gridded(source="My Sat")})
        assert status == 400
        assert reply["errors"]["source"] == (
            "To save this for future use, use letters and numbers only: no spaces or underscores."
        )
        reply = post(app, "/api/own_future", {"kind": "gridded", "form": own_gridded(source="WOA23")})[1]
        assert "own recipes" in reply["errors"]["source"]
        reply = post(app, "/api/own_future", {"kind": "gridded", "form": own_gridded(obs_path="auto", file_check=False)})[1]
        assert reply["errors"] == {"obs_path": "To save this for future use, give the observations' path, not auto."}
        # the form's own checks come first
        reply = post(app, "/api/own_future", {"kind": "gridded", "form": own_gridded(model_variable="")})[1]
        assert set(reply["errors"]) == {"model_variable"}
        assert app.view == "gridded_data"
        # each can still be used now
        assert post(app, "/api/own_add", {"kind": "gridded", "form": own_gridded(source="My Sat")})[0] == 200

    def test_nothing_happens_out_of_turn(self, app, tmp_path):
        assert post(app, "/api/own_future", {"kind": "gridded", "form": own_gridded()})[0] == 409
        assert post(app, "/api/own_future_save", {"boxes": {}})[0] == 409
        assert post(app, "/api/own_future_check")[0] == 409
        own_step(app, tmp_path, "point")
        # the step's kind only
        assert post(app, "/api/own_future", {"kind": "gridded", "form": own_gridded()})[0] == 409
        assert post(app, "/api/own_future", {"kind": "point", "form": own_point()})[0] == 200
        # data on this machine has nothing to check again
        assert post(app, "/api/own_future_check")[0] == 409

    def test_back_keeps_what_was_typed_for_the_same_data(self, app, tmp_path):
        own_step(app, tmp_path)
        post(app, "/api/own_future", {"kind": "gridded", "form": own_gridded()})
        assert post(app, "/api/back", {"boxes": {"units": "mmol/m3", "nonsense": "x"}})[0] == 200
        assert app.view == "gridded_data"
        assert app.own_data["gridded"] == []
        assert app.own_pending["typed"] == {"units": "mmol/m3"}
        post(app, "/api/own_future", {"kind": "gridded", "form": own_gridded()})
        values = {box["name"]: box["value"] for box in future_state(app)["boxes"]}
        assert values == {"units": "mmol/m3", "source_info": "", "where": ""}
        # other data starts afresh
        post(app, "/api/back", {"boxes": {"units": "mmol/m3"}})
        post(app, "/api/own_future", {"kind": "gridded", "form": own_gridded(source="Other")})
        assert {box["name"]: box["value"] for box in future_state(app)["boxes"]}["units"] == ""
        # and all of it is forgotten as another run starts
        for view in ("gridded_data", "point_data", "own_data", "setup", "start"):
            assert post(app, "/api/back")[0] == 200
            assert app.view == view
        assert post(app, "/api/choose", {"action": "matchup"})[0] == 200
        assert app.own_pending is None

    def test_a_recipe_of_the_same_name_in_the_file_chosen(self, app, tmp_path):
        recipe = recipe_forms.check_save("gridded", gridded_form(where="global"), str(tmp_path))[0]
        user_recipes.save(recipe, "global", str(tmp_path))
        own_step(app, tmp_path)
        assert post(app, "/api/own_future", {"kind": "gridded", "form": own_gridded()})[0] == 200
        where = future_state(app)["where"]
        assert "already a nitrate recipe" in where["global"]["errors"][0]
        assert where["local"]["errors"] == [] and "used in its place" in where["local"]["warnings"][0]
        status, reply = post(app, "/api/own_future_save", {"boxes": {}})
        assert status == 400 and "already a nitrate recipe" in reply["errors"]["where"]
        assert app.own_data["gridded"] == []
        status, reply = post(app, "/api/own_future_save", {"boxes": {"where": "local"}})
        assert status == 200, reply
        assert "used in its place" in reply["warnings"][0]
        assert user_recipes.where_of("nitrate", "mysat", app.cwd) == "local"

    def test_data_at_a_web_address_is_checked_before_it_is_saved(self, app, tmp_path, served):
        own_step(app, tmp_path)
        form = own_gridded(obs_path=served, file_check=False)
        status, reply = post(app, "/api/own_future", {"kind": "gridded", "form": form})
        assert status == 200, reply
        wait_for(lambda: future_state(app)["data"]["status"] != "checking")
        future = future_state(app)
        assert future["data"]["status"] == "ok", future
        # the units come from the data once it has been checked
        assert future["boxes"][0] == {"name": "units", "assumed": "mmol N/m^3", "value": ""}
        status, reply = post(app, "/api/own_future_save", {"boxes": {}})
        assert status == 200, reply
        saved = user_recipes.get("nitrate", "mysat", app.cwd)
        assert (saved["location"], saved["obs_path"], saved["units"]) == ("url", served, "mmol N/m^3")
        assert app.own_data["gridded"][0]["file_check"] is False

    def test_a_server_that_cannot_be_reached_is_not_saved(self, app, tmp_path):
        own_step(app, tmp_path)
        form = own_gridded(obs_path="http://127.0.0.1:1/thredds/dodsC/x.nc", thredds=True, file_check=False)
        assert post(app, "/api/own_future", {"kind": "gridded", "form": form})[0] == 200
        wait_for(lambda: future_state(app)["data"]["status"] == "failed")
        assert "connection was refused" in future_state(app)["data"]["error"]
        # nothing can be assumed of its units
        assert future_state(app)["boxes"][0]["assumed"] is None
        status, reply = post(app, "/api/own_future_save", {"boxes": {"units": "mg m-3"}})
        assert status == 400 and "connection was refused" in reply["errors"][""]
        assert app.own_data["gridded"] == [] and user_recipes.listing(app.cwd) == []
        # it can be checked again, and leaving stops it
        assert post(app, "/api/own_future_check")[0] == 200
        assert post(app, "/api/back")[0] == 200
        assert app.register_job is None

    def test_an_address_that_cannot_be_used_is_said_on_the_form(self, app, tmp_path):
        own_step(app, tmp_path)
        form = own_gridded(obs_path="https://x.org/thredds/catalog/a.nc", thredds=True, file_check=False)
        status, reply = post(app, "/api/own_future", {"kind": "gridded", "form": form})
        assert status == 400 and "catalogue page" in reply["errors"]["obs_path"]
        assert app.register_job is None

    def test_the_recipe_is_left_out_of_the_runs_recipes_window(self, app, tmp_path, runs):
        own_step(app, tmp_path)
        post(app, "/api/own_future", {"kind": "gridded", "form": own_gridded()})
        assert post(app, "/api/own_future_save", {"boxes": {"where": "local"}})[0] == 200
        assert post(app, "/api/own_next")[0] == 200
        wait_for(lambda: app.view == "recipes")

        # the own data registers it in this run, so it is not offered as well
        rows = {row["variable"]: row for row in page_state(get(app, "/recipes/")[1])["rows"]}
        assert rows["nitrate"]["model_variable"] == "N3_n"
        assert [d["recipe"] for d in rows["nitrate"]["gridded"] if d["user"]] == []
        post_rows = [{"variable": "nitrate", "model_variable": "N3_n", "selected": []}]
        status, reply = post(app, "/recipes/write", {"rows": post_rows, "settings": recipe_settings(app)})
        assert status == 200, reply
        units_match(app)
        wait_for(lambda: runs)
        script = (tmp_path / "matchup.py").read_text()
        assert '\noceanval.add_gridded_comparison(\n    name="nitrate",\n    source="MySat",\n' in script
        assert "Your recipes, from .oceanvalrc" in script
        mine = [line for line in script.splitlines() if 'recipe={"nitrate": "mysat"}' in line]
        assert mine and all(line.startswith("#") for line in mine)
        compile(script, "matchup.py", "exec")


def test_the_pages_in_a_browser(browser, tmp_path):
    from oceanval.app import App

    app = App(cwd=str(tmp_path))
    app.leftovers_asked = True
    url = app.start()
    try:
        page = browser.new_page()
        failures = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.goto(url)
        page.wait_for_selector("#view-start:not([hidden])")
        assert page.locator("#view-start .choice:not(.is-demo):not(.is-register)").count() == 4
        page.click('button.choice[data-action="register"]')
        page.wait_for_selector("#view-register-kind:not([hidden])")
        assert page.text_content("#title") == "Which kind of data is your recipe for?"
        assert page.locator("#steps").count() == 0
        page.click("#register-gridded")
        page.wait_for_selector("#view-register-gridded:not([hidden])")
        assert page.is_disabled("#register-save")

        page.fill("#r-gridded-variable", "nitrate")
        page.fill("#r-gridded-source", "MySat")
        page.fill("#r-gridded-source_info", "My nitrate")
        page.fill("#r-gridded-obs_path", NITRATE)
        page.wait_for_function("document.querySelector('#r-gridded-obs_variable').value === 'N3_n'")
        # what OceanVal filled in is red and bold
        red = "rgb(192, 57, 43)"
        for selector in ("#r-gridded-obs_variable", "#r-gridded-units"):
            assert page.locator(selector).evaluate("node => getComputedStyle(node).color") == red
            assert page.locator(selector).evaluate("node => getComputedStyle(node).fontWeight") == "700"
        assert page.input_value("#r-gridded-units") == "mmol N/m^3"
        assert "recipe={\"nitrate\": \"mysat\"}" in page.text_content("#r-gridded-preview")
        page.select_option("#r-gridded-climatology", "yes")
        page.check("#r-gridded-where-local")
        page.wait_for_function("!document.querySelector('#register-save').disabled")

        # Back keeps what was entered
        page.click("text=Back")
        page.wait_for_selector("#view-register-kind:not([hidden])")
        page.click("#register-gridded")
        page.wait_for_selector("#view-register-gridded:not([hidden])")
        assert page.input_value("#r-gridded-source") == "MySat"
        page.wait_for_function("!document.querySelector('#register-save').disabled")

        # a name that cannot be used
        page.fill("#r-gridded-source", "WOA23")
        page.wait_for_function("document.querySelector('#r-gridded-source-err').textContent.includes('own recipes')")
        assert page.is_disabled("#register-save")
        page.fill("#r-gridded-source", "MySat")
        page.wait_for_function("!document.querySelector('#register-save').disabled")

        page.click("#register-save")
        page.wait_for_selector("#register-list-gridded:not([hidden])")
        assert json.load(open(tmp_path / ".oceanvalrc"))["recipes"]["nitrate"]["mysat"]["units"] == "mmol N/m^3"
        assert page.input_value("#r-gridded-source") == ""
        assert page.is_checked("#r-gridded-where-local")

        # removing takes two clicks
        page.click("#register-list-gridded >> text=Remove")
        assert user_recipes.get("nitrate", "mysat", str(tmp_path)) is not None
        page.click("#register-list-gridded >> text=Click again to remove")
        page.wait_for_selector("#register-list-gridded", state="hidden")
        assert user_recipes.get("nitrate", "mysat", str(tmp_path)) is None

        # point data is on this machine only
        page.click("text=Back")
        page.click("#register-point")
        page.wait_for_selector("#view-register-point:not([hidden])")
        assert page.locator("#r-point-location").count() == 0
        page.fill("#r-point-obs_path", "/nowhere")
        page.wait_for_function("document.querySelector('#r-point-data-msg').innerText.includes('no directory')")
        assert failures == []
    finally:
        app.close()


def test_saving_your_own_data_for_future_use_in_a_browser(browser, tmp_path):
    """The own data form's two buttons, and the step that saves the data as a
    recipe as well: what OceanVal assumes is amber until it is typed over,
    what has to be given is red, and Back keeps what was typed."""
    from oceanval.app import App

    write_simulation(tmp_path / "sim", tracers=True)
    app = App(cwd=str(tmp_path))
    app.leftovers_asked = True
    url = app.start()
    amber, red = "rgb(168, 106, 0)", "rgb(192, 57, 43)"
    try:
        page = browser.new_page()
        failures = []
        page.on("pageerror", lambda error: failures.append(str(error)))

        def colour(selector):
            return page.locator(selector).evaluate("node => getComputedStyle(node).color")

        page.goto(url)
        page.click('button.choice[data-action="matchup"]')
        page.fill("#f-simdir", "sim")
        page.wait_for_function("document.querySelector('#f-end').value === '2012'")
        page.fill("#f-ndown", "2")
        page.click("#continue")
        page.click("#own-yes")
        page.wait_for_selector("#own-form-point")
        assert page.text_content("#own-add-point") == "Add this data for use now only"
        assert page.text_content("#own-future-point") == "Add this data for use now and in future"

        page.fill("#o-point-name", "dic")
        page.fill("#o-point-source", "DicPts")
        page.fill("#o-point-model_variable", "thetao")
        page.fill("#o-point-obs_path", POINTS)
        page.click("#own-future-point")
        page.wait_for_selector("#view-own-future:not([hidden])")
        assert page.text_content("#title") == "Save your point data for future use"
        assert 'recipe={"dic": "dicpts"}' in page.text_content(".future-summary")
        assert "Model variable (thetao)" in page.text_content("#fu-run")

        # a csv file says nothing of its units, so they have to be given
        assert page.input_value("#fu-units") == ""
        assert colour("label[for=fu-units]") == red
        assert page.is_visible("#fu-legend-required")
        assert page.is_disabled("#future-save")
        # what OceanVal assumes is amber
        assert page.input_value("#fu-source_info") == "Source for DicPts"
        assert page.input_value("#fu-short_title") == "Dic"
        for selector in ("#fu-source_info", "#fu-short_title", "#fu-where-global-title"):
            assert colour(selector) == amber
        assert page.is_checked("#fu-where-global")

        page.fill("#fu-units", "umol/kg")
        page.fill("#fu-short_title", "DIC")
        assert colour("#fu-short_title") != amber
        page.wait_for_function("!document.querySelector('#future-save').disabled")

        # Back keeps the form, and what was typed here
        page.click("#actions >> text=Back")
        page.wait_for_selector("#own-form-point")
        assert page.input_value("#o-point-name") == "dic"
        page.click("#own-future-point")
        page.wait_for_selector("#view-own-future:not([hidden])")
        assert page.input_value("#fu-units") == "umol/kg"
        assert page.input_value("#fu-short_title") == "DIC"
        assert colour("#fu-source_info") == amber

        page.click("#future-save")
        page.wait_for_function("document.querySelector('#toast').textContent.includes('saved for future use')")
        page.wait_for_selector("#view-point:not([hidden]) #own-list-point:not([hidden])")
        assert 'Also saved for future use, as recipe={"dic": "dicpts"}' in page.text_content("#own-list-point")
        assert page.input_value("#o-point-name") == ""
        saved = json.load(open(user_recipes.global_path()))["recipes"]["dic"]["dicpts"]
        assert (saved["units"], saved["short_title"]) == ("umol/kg", "DIC")
        assert app.own_data["point"][0]["short_title"] == "DIC"

        # gridded data's units are read from it, and are amber too
        page.click("#own-next")
        page.wait_for_selector("#own-form-gridded")
        page.fill("#o-gridded-name", "nitrate")
        page.fill("#o-gridded-source", "MySat")
        page.fill("#o-gridded-model_variable", "N3_n")
        page.fill("#o-gridded-obs_path", NITRATE)
        page.fill("#o-gridded-obs_variable", "N3_n")
        page.select_option("#o-gridded-climatology", "yes")
        page.click("#own-future-gridded")
        page.wait_for_selector("#view-own-future:not([hidden]) #fu-labels-note")
        assert page.input_value("#fu-units") == "mmol N/m^3"
        assert colour("#fu-units") == amber
        assert not page.is_visible("#fu-legend-required")
        # nitrate's names are OceanVal's, so are not asked for
        assert page.locator("#fu-long_name").count() == 0
        page.wait_for_function("!document.querySelector('#future-save').disabled")
        assert failures == []
    finally:
        app.close()
