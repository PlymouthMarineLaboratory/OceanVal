"""The stratification recipes ({"stratification": "ices"} and {"stratification":
"en4"}) and EN4's temperature and salinity: what they stand for, how they are
registered (always through the water column, always matched by year, month and
day), the window's row, and the script's block."""

import importlib

import pytest

import oceanval
from oceanval.parsers import find_recipe

create_recipes = importlib.import_module("oceanval.create_recipes")
DAY = ["year", "month", "day"]


@pytest.fixture
def fresh():
    oceanval.reset()
    yield
    oceanval.reset()


@pytest.mark.parametrize("source, label", [("ices", "ICES"), ("en4", "EN4")])
def test_the_recipes_are_points_through_the_water_column_by_day(source, label):
    found = find_recipe({"stratification": source}, cwd="/nonexistent")
    assert found["point"] is True and found["vertical"] is True and found["stratification"] is True
    assert found["source"] == label and found["point_time_res"] == DAY


def test_stratification_has_only_its_two_datasets():
    with pytest.raises(ValueError, match="not valid for stratification"):
        find_recipe({"stratification": "woa23"}, cwd="/nonexistent")


@pytest.mark.parametrize("variable", ["temperature", "salinity"])
def test_en4_is_a_temperature_and_salinity_recipe(variable):
    found = find_recipe({variable: "en4"}, cwd="/nonexistent")
    assert found["point"] is True and found["en4_variable"] == variable
    with pytest.raises(ValueError, match="only for"):
        find_recipe({"nitrate": "en4"}, cwd="/nonexistent")


def test_registered_through_the_water_column_by_day_with_its_salinity(fresh):
    oceanval.add_point_comparison(name="stratification", model_variable="votemper",
                                  salinity_variable="vosaline", recipe={"stratification": "ices"})
    variable = oceanval.definitions["stratification"]
    comparison = variable.point_comparisons["ICES"]
    assert variable.model_variable == "votemper" and variable.salinity_variable == "vosaline"
    assert comparison["recipe"] == {"kind": "ices", "parameter": "TEMPPR01", "dataset": ["CTD", "ICE"], "casts": True}
    assert comparison["vertical"] is True and comparison["point_time_res"] == DAY
    oceanval.add_point_comparison(name="stratification", model_variable="votemper",
                                  salinity_variable="vosaline", recipe={"stratification": "en4"})
    assert variable.point_comparisons["EN4"]["recipe"] == {"kind": "en4", "variable": "stratification"}
    assert variable.point_comparisons["EN4"]["point_time_res"] == DAY


@pytest.mark.parametrize("point_time_res", [["year", "month"], "month", ["month", "day"], "year"])
def test_it_cannot_be_matched_by_anything_coarser_than_the_day(fresh, point_time_res):
    with pytest.raises(ValueError, match="always matched by year, month and day"):
        oceanval.add_point_comparison(name="stratification", model_variable="votemper",
                                      recipe={"stratification": "en4"}, point_time_res=point_time_res)


def test_year_month_and_day_may_be_given(fresh):
    oceanval.add_point_comparison(name="stratification", model_variable="votemper",
                                  recipe={"stratification": "en4"}, point_time_res=["day", "month", "year"])
    assert oceanval.definitions["stratification"].point_comparisons["EN4"]["point_time_res"] == DAY


def test_it_needs_its_recipe_and_salinity_is_only_for_it(fresh):
    with pytest.raises(ValueError, match="casts of temperature and salinity"):
        oceanval.add_point_comparison(name="stratification", source="mine", model_variable="votemper",
                                      obs_path="/nonexistent.csv")
    with pytest.raises(ValueError, match="only for stratification"):
        oceanval.add_point_comparison(name="temperature", model_variable="votemper", salinity_variable="vosaline",
                                      recipe={"temperature": "en4"})


def test_the_window_row_is_first_matched_by_day_with_its_years():
    from oceanval import recipes_gui

    mapping = create_recipes.with_derived({"temperature": "votemper", "salinity": "vosaline", "nitrate": "N3_n"})
    rows = recipes_gui.recipe_rows(mapping, "nwes")
    assert rows[0]["variable"] == "stratification"
    assert rows[0]["model_variable"] == "votemper"
    datasets = {d["recipe"]: d for d in rows[0]["point"]}
    assert set(datasets) == {"ices", "en4"}
    for dataset in datasets.values():
        assert dataset["fixed_time_res"] == "Year, month, day"
        assert "day it was made" in dataset["fixed_time_res_why"]
        assert dataset["vertical_option"] is False
    # its years still count: EN4 is limited to what it covers
    assert datasets["en4"]["year_range"][0] == 1900
    # ICES is ticked on the shelf by default; EN4, at 0.5 to 0.9 GB a year, never is
    assert datasets["ices"]["ticked"] is True and datasets["en4"]["ticked"] is False


def test_stratification_is_never_looked_for_in_the_model():
    assert create_recipes.with_derived({"temperature": "thetao"})["stratification"] == "thetao"
    # nothing found stays nothing found
    assert create_recipes.with_derived({}) == {}


def test_the_script_block_uses_the_temperature_and_salinity():
    entry = [e for e in create_recipes.POINT_RECIPE_CATALOGUE if (e["variable"], e["recipe"]) == ("stratification", "en4")][0]
    lines = create_recipes._point_recipe_block(entry, "votemper", True, None, False, "vosaline")
    assert '    model_variable="votemper",' in lines and '    salinity_variable="vosaline",' in lines
    assert '    recipe={"stratification": "en4"},' in lines
    # how it is matched is the recipe's: nothing to write
    assert not any("point_time_res" in line for line in lines)


# stratification is always through the water column, so matchup needs the model's cell thickness

STRATIFICATION_ROW = [{
    "variable": "stratification",
    "model_variable": "votemper",
    "selected": ["ices"],
    "point": {"ices": {"start": "2012", "end": "2012", "point_time_res": ""}},
}]


def _app_page():
    from oceanval import recipes_gui

    written = []
    context = {"simdir": "/sim", "ndown": 2, "domain": "nwes", "region": "Northwest European Shelf",
               "regions": ["Northwest European Shelf", "European"], "start": 2012, "end": 2012,
               "fvcom": False, "out": "/out/matchup.py", "app": {"action": "matchup"}}
    mapping = create_recipes.with_derived({"temperature": "votemper", "salinity": "vosaline"})
    page = recipes_gui.RecipePage(recipes_gui.recipe_rows(mapping, "nwes"), {"votemper", "vosaline", "e3t"},
                                  context, lambda *chosen: written.append(chosen) or "/out/matchup.py")
    page.written = written
    return page


def test_the_window_sees_stratification_as_through_the_water_column():
    from oceanval import recipes_gui

    rows = recipes_gui.recipe_rows(create_recipes.with_derived({"temperature": "votemper"}), "nwes")
    assert all(dataset["always_vertical"] for dataset in rows[0]["point"])
    temperature = next(row for row in rows if row["variable"] == "temperature")
    assert not any(dataset["always_vertical"] for dataset in temperature["point"])


def test_in_the_app_stratification_needs_a_thickness():
    from oceanval import recipes_gui

    page = _app_page()
    status, reply = page.submit({"rows": STRATIFICATION_ROW})
    assert status == 400
    assert reply["setting_errors"] == {"thickness": recipes_gui.THICKNESS_NEEDED}
    assert page.written == []
    # with NEMO's e3t, it is written with it
    status, _ = page.submit({"rows": STRATIFICATION_ROW, "settings": dict(page.form, thickness="e3t")})
    assert status == 200
    assert page.written[0][2]["thickness"] == "e3t"


def _script(**kwargs):
    mapping = create_recipes.with_derived({"temperature": "votemper", "salinity": "vosaline"})
    return create_recipes.build_recipe_script(
        "/sim", 2, mapping, (2012, 2012), "nwes", selection={("stratification", "ices")}, **kwargs)


LIVE = '\noceanval.add_point_comparison(\n    name="stratification",'


def test_a_script_without_a_thickness_has_stratification_commented_out_saying_why():
    script = _script()
    assert LIVE not in script
    assert "# thickness: give matchup thickness= (e.g. \"e3t\"), then uncomment this block." in script


@pytest.mark.parametrize("kwargs", [{"settings": {"thickness": "e3t"}}, {"fvcom": True}])
def test_with_a_thickness_or_fvcom_stratification_is_live(kwargs):
    assert LIVE in _script(**kwargs)
