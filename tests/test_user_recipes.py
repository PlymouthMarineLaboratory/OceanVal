"""Data recipes of the user's own, kept in .oceanvalrc files."""

import json
import os
import re

import numpy as np
import pytest
import xarray as xr

import oceanval
from oceanval import recipes_gui, units, user_recipes
from oceanval.create_recipes import (
    POINT_RECIPE_CATALOGUE,
    RECIPE_CATALOGUE,
    RECIPE_VARIABLES,
    USER_REGION,
    build_recipe_script,
    default_selection,
    generate_recipe_mapping,
    gridded_catalogue,
    point_catalogue,
    recipe_variables,
)
from oceanval.parsers import Validator, find_recipe

NITRATE = os.path.abspath("data/evaldata/gridded/nws/nitrate/model_2000.nc")
POINTS = os.path.abspath("data/chl/obs_csv")


@pytest.fixture
def work(tmp_path):
    """A directory to work in, with no .oceanvalrc in it, and no global one
    (see the oceanvalrc fixture in conftest.py)."""
    directory = tmp_path / "work"
    directory.mkdir()
    return str(directory)


@pytest.fixture
def validator():
    v = Validator()
    v.keys = []
    return v


def gridded(**changes):
    recipe = dict(
        variable="nitrate",
        kind="gridded",
        source="MySat",
        source_info="My nitrate product",
        location="disk",
        obs_path=NITRATE,
        obs_variable="N3_n",
        climatology=True,
        units="mmol N/m^3",
    )
    recipe.update(changes)
    return recipe


def point(**changes):
    recipe = dict(
        variable="poc",
        kind="point",
        source="PocPts",
        source_info="DIC samples",
        obs_path=POINTS,
        units="umol/kg",
        long_name="particulate organic carbon",
        short_name="POC",
        short_title="POC",
    )
    recipe.update(changes)
    return recipe


class TestPaths:
    def test_the_directory_worked_in(self, work):
        assert user_recipes.local_path(work) == os.path.join(work, ".oceanvalrc")

    def test_the_global_file_is_in_the_home_directory(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OCEANVALRC")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert user_recipes.global_path() == str(tmp_path / ".oceanvalrc")

    def test_the_global_file_can_be_named(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OCEANVALRC", str(tmp_path / "elsewhere.json"))
        assert user_recipes.global_path() == str(tmp_path / "elsewhere.json")

    def test_working_in_the_home_directory_is_one_file(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OCEANVALRC")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert user_recipes.same_file(str(tmp_path))


class TestFiles:
    def test_nothing_without_files(self, work):
        assert user_recipes.load(work) == []
        assert user_recipes.variables(work) == []

    def test_a_saved_recipe_is_read_back(self, work):
        path = user_recipes.save(gridded(), "local", work)
        assert path == os.path.join(work, ".oceanvalrc")
        recipe = user_recipes.get("nitrate", "mysat", work)
        assert recipe["source"] == "MySat"
        assert recipe["where"] == "local"
        assert recipe["path"] == path
        data = json.load(open(path))
        assert data["version"] == 1
        assert "added" in data["recipes"]["nitrate"]["mysat"]

    def test_the_directorys_recipe_is_used_in_place_of_the_global_one(self, work):
        user_recipes.save(gridded(source_info="global one"), "global", work)
        user_recipes.save(gridded(source_info="local one"), "local", work)
        assert user_recipes.get("nitrate", "mysat", work)["source_info"] == "local one"
        rows = user_recipes.listing(work)
        assert [(r["where"], r["shadowed"]) for r in rows] == [
            ("local", False),
            ("global", True),
        ]
        assert len(user_recipes.load(work)) == 1
        # elsewhere, the global one is the one
        elsewhere = os.path.join(os.path.dirname(work), "elsewhere")
        os.makedirs(elsewhere)
        assert user_recipes.get("nitrate", "mysat", elsewhere)["source_info"] == "global one"

    def test_recipes_are_removed(self, work):
        user_recipes.save(gridded(), "local", work)
        user_recipes.save(gridded(variable="oxygen"), "local", work)
        assert user_recipes.remove("nitrate", "mysat", "local", work)
        assert not user_recipes.remove("nitrate", "mysat", "local", work)
        assert user_recipes.variables(work) == ["oxygen"]
        user_recipes.remove("oxygen", "mysat", "local", work)
        assert json.load(open(user_recipes.local_path(work)))["recipes"] == {}

    def test_what_is_not_used_is_kept_when_the_file_is_written(self, work):
        path = user_recipes.local_path(work)
        with open(path, "w") as handle:
            json.dump({"version": 1, "note": "mine", "recipes": {}}, handle)
        user_recipes.save(gridded(), "local", work)
        assert json.load(open(path))["note"] == "mine"

    def test_saving_leaves_no_temporary_files(self, work):
        user_recipes.save(gridded(), "local", work)
        assert os.listdir(work) == [".oceanvalrc"]

    def test_a_file_that_cannot_be_read_is_ignored_and_not_written_over(self, work):
        path = user_recipes.local_path(work)
        with open(path, "w") as handle:
            handle.write("{not json")
        with pytest.warns(UserWarning, match="could not be read"):
            assert user_recipes.load(work) == []
        assert "local" in user_recipes.problem_in_files(work)
        with pytest.raises(ValueError, match="could not be read"):
            user_recipes.save(gridded(), "local", work)
        assert open(path).read() == "{not json"

    def test_a_recipe_that_cannot_be_used_is_skipped(self, work):
        path = user_recipes.local_path(work)
        good = {k: v for k, v in gridded().items() if k != "variable"}
        with open(path, "w") as handle:
            json.dump(
                {
                    "version": 1,
                    "recipes": {
                        "nitrate": {"mysat": good, "woa23": dict(good, source="WOA23")},
                        "oxygen": {"bad": dict(good, kind="neither", source="Bad")},
                    },
                },
                handle,
            )
        with pytest.warns(UserWarning):
            found = user_recipes.load(work)
        assert [(r["variable"], r["key"]) for r in found] == [("nitrate", "mysat")]


class TestProblems:
    def test_a_sound_recipe(self, work):
        assert user_recipes.problems(gridded(), "local", work) == ({}, [])

    @pytest.mark.parametrize(
        "changes, box",
        [
            ({"variable": ""}, "variable"),
            ({"variable": "ni trate"}, "variable"),
            ({"variable": "keys"}, "variable"),
            ({"variable": "reset"}, "variable"),
            ({"source": ""}, "source"),
            ({"source": "My_Sat"}, "source"),
            ({"source": "My Sat"}, "source"),
            ({"source": "WOA23"}, "source"),
            ({"source": "Occci"}, "source"),
            ({"source": "ICES"}, "source"),
        ],
    )
    def test_names_that_cannot_be_used(self, work, changes, box):
        errors, _ = user_recipes.problems(gridded(**changes), "local", work)
        assert list(errors) == [box]

    def test_somewhere_has_to_be_chosen(self, work):
        errors, _ = user_recipes.problems(gridded(), "", work)
        assert list(errors) == ["where"]

    def test_the_same_recipe_twice_in_one_file(self, work):
        user_recipes.save(gridded(), "local", work)
        errors, _ = user_recipes.problems(gridded(), "local", work)
        assert "already a nitrate recipe" in errors["source"]
        # a source name is not case sensitive
        errors, _ = user_recipes.problems(gridded(source="mysat"), "local", work)
        assert "source" in errors

    def test_the_same_recipe_in_the_other_file_is_a_warning(self, work):
        user_recipes.save(gridded(), "global", work)
        errors, warnings = user_recipes.problems(gridded(), "local", work)
        assert errors == {}
        assert "used in its place" in warnings[0]
        errors, warnings = user_recipes.problems(gridded(), "global", work)
        assert "source" in errors
        # and the other way round: one in this directory is used in place of a
        # global one
        user_recipes.save(gridded(source="Other"), "local", work)
        errors, warnings = user_recipes.problems(gridded(source="Other"), "global", work)
        assert errors == {}
        assert "used in place of this" in warnings[0]

    def test_the_other_file_cannot_have_it_as_another_kind(self, work):
        user_recipes.save(gridded(variable="poc", source="PocPts"), "global", work)
        errors, _ = user_recipes.problems(point(), "local", work)
        assert "already a gridded poc recipe" in errors["source"]

    def test_a_variables_names_are_those_it_has(self, work):
        errors, _ = user_recipes.problems(
            gridded(short_name="a", long_name="b", short_title="c"), "local", work
        )
        assert "already has its own names" in errors[""]
        user_recipes.save(point(), "local", work)
        errors, _ = user_recipes.problems(
            point(source="Other", short_title="Different"), "local", work
        )
        assert "already has its own names" in errors[""]

    def test_the_reserved_variable_names_are_a_validators(self):
        own = {name for name in dir(Validator) if not name.startswith("_")}
        assert set(user_recipes.RESERVED_VARIABLES) == own

    def test_the_built_in_sources_are_the_catalogues(self):
        # BUILTIN_SOURCES may also reserve names for forthcoming implementations
        catalogue_sources = {e["recipe"] for e in RECIPE_CATALOGUE + POINT_RECIPE_CATALOGUE}
        assert catalogue_sources <= set(user_recipes.BUILTIN_SOURCES)

    def test_a_new_variables_names_come_with_the_recipe(self, work):
        user_recipes.save(point(), "local", work)
        assert user_recipes.labels("poc", work) == ("POC", "particulate organic carbon", "POC")
        assert user_recipes.labels("nitrate", work)[2] == "Nitrate"
        assert user_recipes.labels("nothing", work) is None


class TestFindRecipe:
    def test_built_in_recipes_are_as_they_were(self, work):
        found = find_recipe({"nitrate": "woa23"}, cwd=work)
        assert found["source"] == "WOA23" and found["thredds"] is True
        assert find_recipe({"temperature": "ices"}, cwd=work)["point"] is True

    def test_a_gridded_recipe_on_disk(self, work):
        user_recipes.save(gridded(), "local", work)
        found = find_recipe({"nitrate": "mysat"}, cwd=work)
        assert found["point"] is False
        assert found["thredds"] is False
        assert found["obs_path"] == NITRATE
        assert found["obs_variable"] == "N3_n"
        assert found["climatology"] is True
        assert found["source"] == "MySat"
        assert found["short_title"] == "Nitrate"
        assert "file_check" not in found
        # the variable and the key are not case sensitive
        assert find_recipe({"Nitrate": "MySat"}, cwd=work)["source"] == "MySat"

    def test_a_recipe_on_a_thredds_server(self, work):
        urls = ["https://example.org/thredds/dodsC/a.nc", "https://example.org/thredds/dodsC/b.nc"]
        user_recipes.save(gridded(location="thredds", obs_path=urls), "local", work)
        found = find_recipe({"nitrate": "mysat"}, cwd=work)
        assert found["thredds"] is True
        assert found["obs_path"] == urls

    def test_a_recipe_for_a_download_is_not_checked(self, work):
        user_recipes.save(gridded(location="url", obs_path="https://example.org/a.nc"), "local", work)
        assert find_recipe({"nitrate": "mysat"}, cwd=work)["file_check"] is False

    def test_depth_resolved_data_can_be_validated_through_the_water_column(self, work):
        user_recipes.save(gridded(depth_resolved=True), "local", work)
        assert find_recipe({"nitrate": "mysat"}, cwd=work)["vertical"] is None
        user_recipes.save(gridded(source="Other"), "local", work)
        assert find_recipe({"nitrate": "other"}, cwd=work)["vertical"] is False

    def test_a_point_recipe(self, work):
        user_recipes.save(point(), "global", work)
        found = find_recipe({"poc": "pocpts"}, cwd=work)
        assert found["point"] is True and found["obs_path"] == POINTS
        assert found["short_title"] == "POC"

    def test_a_recipe_that_is_nowhere_says_where_it_looked(self, work):
        with pytest.raises(ValueError, match="not valid") as error:
            find_recipe({"nitrate": "nothing"}, cwd=work)
        assert user_recipes.local_path(work) in str(error.value)
        assert user_recipes.global_path() in str(error.value)

    def test_a_recipe_is_looked_for_in_the_directory_worked_in(self, work, monkeypatch):
        user_recipes.save(gridded(), "local", work)
        with pytest.raises(ValueError):
            find_recipe({"nitrate": "mysat"})
        monkeypatch.chdir(work)
        assert find_recipe({"nitrate": "mysat"})["source"] == "MySat"


class TestRegistering:
    def test_a_gridded_recipe_is_registered(self, work, validator, monkeypatch):
        user_recipes.save(gridded(), "local", work)
        monkeypatch.chdir(work)
        validator.add_gridded_comparison(model_variable="N", recipe={"nitrate": "mysat"})
        comparison = validator["nitrate"].gridded_comparisons["MySat"]
        assert comparison["obs_path"] == NITRATE
        assert comparison["obs_variable"] == "N3_n"
        assert comparison["climatology"] is True
        assert comparison["thredds"] is False
        assert validator["nitrate"].sources["MySat"] == "My nitrate product"
        assert validator["nitrate"].short_title == "Nitrate"

    def test_a_recipe_can_be_a_pattern(self, work, validator, monkeypatch):
        pattern = os.path.abspath("data/evaldata/gridded/nws/*/model_2000.nc")
        user_recipes.save(gridded(obs_path=pattern), "local", work)
        monkeypatch.chdir(work)
        validator.add_gridded_comparison(model_variable="N", recipe={"nitrate": "mysat"})
        assert validator["nitrate"].gridded_comparisons["MySat"]["obs_path"] == pattern

    def test_a_pattern_is_checked_for_the_variable(self, work, validator, monkeypatch):
        pattern = os.path.abspath("data/evaldata/gridded/nws/*/model_2000.nc")
        user_recipes.save(gridded(obs_path=pattern, obs_variable="nothing"), "local", work)
        monkeypatch.chdir(work)
        with pytest.raises(ValueError, match="not found"):
            validator.add_gridded_comparison(model_variable="N", recipe={"nitrate": "mysat"})

    def test_a_download_is_not_opened_when_registered(self, work, validator, monkeypatch):
        user_recipes.save(gridded(location="url", obs_path="https://example.invalid/a.nc"), "local", work)
        monkeypatch.chdir(work)
        validator.add_gridded_comparison(model_variable="N", recipe={"nitrate": "mysat"})
        assert validator["nitrate"].gridded_comparisons["MySat"]["thredds"] is False

    def test_a_thredds_recipe_is_registered_without_the_server(self, work, validator, monkeypatch):
        urls = ["https://example.invalid/dodsC/a.nc"]
        user_recipes.save(gridded(location="thredds", obs_path=urls), "local", work)
        monkeypatch.chdir(work)
        validator.add_gridded_comparison(
            model_variable="N", recipe={"nitrate": "mysat"}, file_check=False
        )
        comparison = validator["nitrate"].gridded_comparisons["MySat"]
        assert comparison["thredds"] is True and comparison["obs_path"] == urls

    def test_a_point_recipe_is_registered_as_csv_files(self, work, validator, monkeypatch):
        user_recipes.save(point(), "local", work)
        monkeypatch.chdir(work)
        validator.add_point_comparison(model_variable="POC", recipe={"poc": "pocpts"})
        comparison = validator["poc"].point_comparisons["PocPts"]
        assert comparison["obs_path"] == POINTS
        # read as csv files, not downloaded like ICES
        assert comparison["recipe"] is None
        assert validator["poc"].short_title == "POC"

    def test_a_point_recipes_files_are_checked(self, work, validator, monkeypatch, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        user_recipes.save(point(obs_path=str(empty)), "local", work)
        monkeypatch.chdir(work)
        with pytest.raises(ValueError, match="No csv files"):
            validator.add_point_comparison(model_variable="POC", recipe={"poc": "pocpts"})

    def test_a_point_recipe_brings_its_own_data(self, work, validator, monkeypatch):
        user_recipes.save(point(), "local", work)
        monkeypatch.chdir(work)
        with pytest.raises(ValueError, match="supplies its own data"):
            validator.add_point_comparison(
                model_variable="POC", recipe={"poc": "pocpts"}, obs_path=POINTS
            )

    def test_a_recipe_is_for_one_kind(self, work, validator, monkeypatch):
        user_recipes.save(point(), "local", work)
        user_recipes.save(gridded(), "local", work)
        monkeypatch.chdir(work)
        with pytest.raises(ValueError, match="use add_gridded_comparison"):
            validator.add_point_comparison(model_variable="POC", recipe={"nitrate": "mysat"})
        with pytest.raises(ValueError, match="use add_point_comparison"):
            validator.add_gridded_comparison(model_variable="POC", recipe={"poc": "pocpts"})


@pytest.fixture
def registered(work):
    user_recipes.save(gridded(), "local", work)
    user_recipes.save(gridded(variable="poc", source="PocSat", depth_resolved=True,
                              long_name="particulate organic carbon", short_name="POC",
                              short_title="POC", obs_path=NITRATE), "local", work)
    user_recipes.save(point(), "global", work)
    return work


class TestCatalogues:
    def test_the_built_in_catalogues_are_not_changed(self, registered):
        gridded_entries = gridded_catalogue(registered)
        assert gridded_entries[: len(RECIPE_CATALOGUE)] == RECIPE_CATALOGUE
        assert point_catalogue(registered)[: len(POINT_RECIPE_CATALOGUE)] == POINT_RECIPE_CATALOGUE
        assert all(not e.get("user") for e in RECIPE_CATALOGUE + POINT_RECIPE_CATALOGUE)

    def test_the_users_recipes_come_after(self, registered):
        mine = [e for e in gridded_catalogue(registered) if e.get("user")]
        assert [(e["variable"], e["recipe"]) for e in mine] == [
            ("nitrate", "mysat"),
            ("poc", "pocsat"),
        ]
        assert all(e["region"] == USER_REGION for e in mine)
        nitrate, poc = mine
        assert nitrate["label"] == "MySat" and nitrate["units"] == "mmol N/m^3"
        assert "your recipe" in nitrate["notes"][0]
        # only a recipe through the water column offers it
        assert nitrate["arguments"] == ()
        assert poc["arguments"][0].startswith("vertical=")
        assert [e["recipe"] for e in point_catalogue(registered) if e.get("user")] == ["pocpts"]

    def test_there_are_variables_of_the_users_own(self, registered):
        assert recipe_variables(registered)[-1] == "poc"
        assert recipe_variables(registered)[:-1] == RECIPE_VARIABLES

    def test_nothing_is_added_without_recipes(self, work):
        assert gridded_catalogue(work) == RECIPE_CATALOGUE
        assert recipe_variables(work) == RECIPE_VARIABLES


def model_file(path, **variables):
    """A model file with 2D variables, each given as (long_name)."""
    data = {
        name: (("time", "y", "x"), np.ones((2, 2, 2), dtype="float32"),
               {"long_name": long_name, "units": "1"})
        for name, long_name in variables.items()
    }
    xr.Dataset(data, coords={"time": [0, 1], "y": [0, 1], "x": [0, 1]}).to_netcdf(path)
    return str(path)


class TestIdentification:
    def test_a_variable_with_a_long_name_is_found(self, registered, tmp_path):
        path = model_file(tmp_path / "m.nc", DIC="particulate organic carbon", thetao="sea temperature")
        mapping = generate_recipe_mapping(path, cwd=registered)
        assert mapping["poc"] == "DIC"

    def test_a_variable_named_for_it_is_found(self, registered, tmp_path):
        path = model_file(tmp_path / "m.nc", poc="carbon of some kind")
        assert generate_recipe_mapping(path, cwd=registered)["poc"] == "poc"

    def test_an_ambiguous_variable_is_left_blank(self, registered, tmp_path):
        path = model_file(
            tmp_path / "m.nc", A="particulate organic carbon", B="particulate organic carbon flux"
        )
        assert generate_recipe_mapping(path, cwd=registered)["poc"] is None

    def test_a_variable_that_is_not_there_is_left_blank(self, registered, tmp_path):
        path = model_file(tmp_path / "m.nc", thetao="sea temperature")
        assert generate_recipe_mapping(path, cwd=registered)["poc"] is None

    def test_benthic_variables_are_not_taken(self, registered, tmp_path):
        path = model_file(tmp_path / "m.nc", B="benthic particulate organic carbon")
        assert generate_recipe_mapping(path, cwd=registered)["poc"] is None

    def test_the_built_in_variables_are_found_as_before(self, registered, tmp_path):
        path = model_file(tmp_path / "m.nc", N="nitrate concentration")
        assert generate_recipe_mapping(path, cwd=registered)["nitrate"] == "N"


class TestSelection:
    def test_the_users_recipes_start_ticked_where_there_is_a_model_variable(self, registered):
        mapping = {"nitrate": "N", "poc": "POC"}
        selection = default_selection(mapping, "global", registered)
        assert {("nitrate", "mysat"), ("nitrate", "woa23"), ("poc", "pocsat"), ("poc", "pocpts")} <= selection
        assert default_selection({"nitrate": "N", "poc": None}, "global", registered) >= {("nitrate", "mysat")}
        assert not {pair for pair in default_selection({"nitrate": "N"}, "global", registered) if pair[0] == "poc"}

    def test_without_the_recipes_the_selection_is_as_it_was(self, work):
        mapping = {"nitrate": "N"}
        assert default_selection(mapping, "global", work) == default_selection(mapping, "global")

    def test_the_window_has_a_row_for_a_new_variable(self, registered):
        rows = recipes_gui.recipe_rows({"nitrate": "N", "poc": None}, "global", registered)
        by_variable = {row["variable"]: row for row in rows}
        poc = by_variable["poc"]
        assert poc["title"] == "POC"
        # not identified, so blank, with nothing ticked
        assert poc["model_variable"] == ""
        assert [d["ticked"] for d in poc["gridded"] + poc["point"]] == [False, False]
        assert [(d["label"], d["region"], d["user"]) for d in poc["gridded"]] == [("PocSat", "Yours", True)]
        assert poc["gridded"][0]["vertical_option"] is True
        # ticked once there is a model variable for it
        again = recipes_gui.recipe_rows({"poc": "POC"}, "global", registered)
        assert all(d["ticked"] for d in {r["variable"]: r for r in again}["poc"]["gridded"])
        nitrate = by_variable["nitrate"]
        assert [d["label"] for d in nitrate["gridded"]][-1] == "MySat"
        assert nitrate["gridded"][-1]["ticked"] is True
        assert nitrate["gridded"][0]["user"] is False

    def test_the_rows_are_the_same_without_recipes(self, work):
        rows = recipes_gui.recipe_rows({"nitrate": "N"}, "global", work)
        assert rows == recipes_gui.recipe_rows({"nitrate": "N"}, "global")


class TestScript:
    def test_the_recipes_are_written_into_the_script(self, registered):
        script = build_recipe_script(
            "/sim", 1, {"nitrate": "N", "poc": "POC"}, (2000, 2001), "global", cwd=registered
        )
        compile(script, "script", "exec")
        assert "Your recipes, from .oceanvalrc" in script
        assert 'recipe={"nitrate": "mysat"},' in script
        assert 'recipe={"poc": "pocsat"},' in script
        assert 'recipe={"poc": "pocpts"},' in script
        assert "# recipe: 'mysat'" in script
        assert "your recipe, in" in script

    def test_a_variable_with_no_model_variable_is_commented_out(self, registered):
        script = build_recipe_script("/sim", 1, {"nitrate": "N"}, (2000, 2001), "global", cwd=registered)
        compile(script, "script", "exec")
        live = [line for line in script.splitlines() if line.startswith("oceanval.add_")]
        assert not any("poc" in line for line in live)
        assert "# oceanval.add_gridded_comparison(" in script
        assert "No poc variable was found" in script

    def test_unticked_recipes_are_commented_out(self, registered):
        script = build_recipe_script(
            "/sim", 1, {"nitrate": "N"}, (2000, 2001), "global",
            selection={("nitrate", "woa23")}, cwd=registered,
        )
        assert '    recipe={"nitrate": "mysat"},' not in script.replace("#     recipe", "")
        assert "Not selected when this script was generated" in script

    def test_there_is_no_section_without_recipes(self, work):
        script = build_recipe_script("/sim", 1, {"nitrate": "N"}, (2000, 2001), "global", cwd=work)
        assert "Your recipes" not in script

    def test_the_script_registers_what_it_names(self, registered, validator, monkeypatch):
        script = build_recipe_script(
            "/sim", 1, {"nitrate": "N"}, (2000, 2001), "global",
            selection={("nitrate", "mysat")}, cwd=registered,
        )
        calls = [
            block for block in re.findall(r"^oceanval\.add_gridded_comparison\(.*?^\)", script, re.M | re.S)
            if "mysat" in block
        ]
        assert len(calls) == 1
        monkeypatch.chdir(registered)
        oceanval.reset()
        try:
            exec(calls[0], {"oceanval": oceanval})
            assert "MySat" in oceanval.definitions["nitrate"].gridded_comparisons
        finally:
            oceanval.reset()


class TestUnits:
    def test_the_units_of_a_recipe_are_the_ones_it_was_given(self, registered):
        rows = units.matchups(
            {"nitrate": "N", "poc": "POC"},
            {("nitrate", "mysat"), ("poc", "pocpts")},
            {}, {}, {"point": [], "gridded": []},
            {"N": "mmol N/m^3", "POC": "umol/kg"},
            registered,
        )
        by_title = {row["title"]: row for row in rows}
        gridded_row = by_title["nitrate (mysat)"]
        assert gridded_row["kind"] == "gridded"
        assert gridded_row["obs_units"] == "mmol N/m^3"
        assert gridded_row["obs_variable"] == "N3_n"
        assert gridded_row["check"]["status"] == "same"
        point_row = by_title["poc (pocpts, point)"]
        assert point_row["kind"] == "point"
        assert point_row["obs_units"] == "umol/kg"
        assert point_row["obs_variable"] == "observation"

    def test_their_model_variables_are_read_for_the_units(self, registered):
        names = units.model_variables(
            {"nitrate": "N", "poc": "DIC+B"}, {("nitrate", "mysat"), ("poc", "pocsat")},
            {"point": [], "gridded": []}, registered,
        )
        assert set(names) == {"N", "DIC", "B"}
        # without the recipes, there is nothing to read for them
        assert units.model_variables({"poc": "POC"}, {("poc", "pocsat")}, {"point": [], "gridded": []}) == []


class TestMatchup:
    def test_a_recipe_is_matched_up_like_any_other(self, work, monkeypatch):
        """A recipe of the user's own, in a .oceanvalrc, takes the place of the
        arguments of add_gridded_comparison all the way through matchup."""
        user_recipes.save(
            gridded(
                variable="temperature",
                source="foo",
                obs_path=os.path.abspath("data/evaldata/gridded/nws/temperature"),
                obs_variable="votemper",
                units="degC",
            ),
            "local",
            work,
        )
        sim = os.path.abspath("data/example")
        monkeypatch.chdir(work)
        oceanval.reset()
        try:
            oceanval.add_gridded_comparison(
                model_variable="votemper", recipe={"temperature": "foo"}, start=2000, end=2010
            )
            oceanval.matchup(
                sim_dir=sim, start=2000, end=2000, ask=False, cores=1, out_dir=work
            )
            matched = os.path.join(work, "oceanval_matchups", "gridded", "temperature")
            assert os.path.exists(os.path.join(matched, "foo_temperature_surface.nc"))
        finally:
            oceanval.reset()
