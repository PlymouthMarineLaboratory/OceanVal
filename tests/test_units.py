"""The units of each gridded matchup, for the oceanval window's units step."""

import html
import os
import re

import numpy as np
import pytest
import xarray as xr

from oceanval import units
from oceanval.create_recipes import POINT_RECIPE_CATALOGUE, RECIPE_CATALOGUE

from simulations import write_fvcom, write_simulation

DOCS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs-site", "recipes.html")


class TestCatalogue:
    # that every recipe has units OceanVal can read is checked in
    # test_unit_conversion.py

    def test_the_website_lists_the_same_units(self):
        """https://pmlmodelling.github.io/OceanVal/recipes.html has a Units
        column for the gridded recipes, which must be the catalogue's."""
        if not os.path.exists(DOCS):
            pytest.skip("the docs site is not part of the installed package")
        page = open(DOCS).read()
        rows = re.findall(r'<tr class="recipe-row".*?</tr>', page, re.S)
        listed = {}
        for row in rows:
            cells = re.findall(r"<td>(.*?)</td>", row, re.S)
            variable = re.sub(r"<.*?>", "", cells[1]).lower()
            recipe = re.sub(r"<.*?>", "", cells[2])
            listed[variable, recipe] = html.unescape(re.sub(r"<.*?>", "", cells[-1]))

        assert listed == {
            (entry["variable"], entry["recipe"]): entry["units"]
            for entry in RECIPE_CATALOGUE
        }

    def test_the_website_lists_the_same_point_units(self):
        if not os.path.exists(DOCS):
            pytest.skip("the docs site is not part of the installed package")
        page = open(DOCS).read()
        table = page[page.index('id="point-recipes"') :]
        listed = {}
        for row in re.findall(r"<tr>\s*(<td>Northeast.*?)</tr>", table, re.S):
            cells = [
                html.unescape(re.sub(r"<.*?>", "", cell))
                for cell in re.findall(r"<td>(.*?)</td>", row, re.S)
            ]
            listed[cells[1]] = cells[4]

        titles = {"alkalinity": "Total Alkalinity", "ph": "pH"}
        assert listed == {
            titles.get(entry["variable"], entry["variable"].title()): entry["units"]
            for entry in POINT_RECIPE_CATALOGUE
        }


class TestModelUnits:
    def test_units_are_read_from_the_files(self, tmp_path):
        write_simulation(tmp_path, tracers=True)

        assert units.model_units(str(tmp_path), 2, ["thetao", "N3_n"]) == {
            "thetao": "degC",
            "N3_n": "mmol N m-3",
        }

    def test_the_file_filters_are_used(self, tmp_path):
        write_simulation(tmp_path, tracers=True)

        found = units.model_units(str(tmp_path), 2, ["thetao", "N3_n"], exclude=["ptrc"])

        assert found == {"thetao": "degC", "N3_n": None}

    def test_raw_fvcom_output(self, tmp_path):
        write_fvcom(str(tmp_path))

        assert units.model_units(str(tmp_path), 2, ["temp"]) == {"temp": "degree_C"}

    def test_nothing_is_read_when_no_variable_is_asked_for(self, tmp_path, monkeypatch):
        # as when no dataset is ticked in the recipes window
        write_simulation(tmp_path)
        monkeypatch.setattr(
            units.xr, "open_dataset", lambda *args, **kwargs: pytest.fail("a file was read")
        )

        assert units.model_units(str(tmp_path), 2, []) == {}

    def test_a_variable_without_units(self, tmp_path):
        folder = tmp_path / "2011" / "01"
        folder.mkdir(parents=True)
        xr.Dataset({"chl": (("y", "x"), np.ones((2, 2)))}).to_netcdf(folder / "a.nc")

        assert units.model_units(str(tmp_path), 2, ["chl"]) == {"chl": None}

    def test_descriptions_are_read_with_the_units(self, tmp_path):
        folder = tmp_path / "2011" / "01"
        folder.mkdir(parents=True)
        attributes = {
            "units": "1e-3",
            "long_name": "absolute salinity",
            "standard_name": "sea_water_absolute_salinity",
        }
        xr.Dataset({"so_abs": (("y", "x"), np.ones((2, 2)), attributes)}).to_netcdf(
            folder / "a.nc"
        )

        assert units.model_attributes(str(tmp_path), 2, ["so_abs", "nowhere"]) == {
            "so_abs": {
                "units": "1e-3",
                "description": "absolute salinity sea_water_absolute_salinity",
            },
            "nowhere": {"units": None, "description": ""},
        }


class TestOwnObsUnits:
    def test_read_from_the_file(self, tmp_path):
        xr.Dataset(
            {"chl": (("y", "x"), np.ones((2, 2)), {"units": "mg/m3"})}
        ).to_netcdf(tmp_path / "obs.nc")

        arguments = {"obs_path": str(tmp_path / "obs.nc"), "obs_variable": "chl"}
        assert units.own_obs_units(arguments) == "mg/m3"
        # a glob, and a path relative to where oceanval was started
        assert units.own_obs_units(dict(arguments, obs_path=str(tmp_path / "*.nc"))) == "mg/m3"
        assert units.own_obs_units(dict(arguments, obs_path="obs.nc"), str(tmp_path)) == "mg/m3"

    @pytest.mark.parametrize(
        "obs_path", ["https://example.org/obs.nc", "auto", "/nowhere/obs.nc", None]
    )
    def test_not_available_where_there_is_no_local_file(self, obs_path):
        arguments = {"obs_path": obs_path, "obs_variable": "chl"}

        assert units.own_obs_units(arguments) is None

    def test_a_variable_that_is_not_in_the_file(self, tmp_path):
        xr.Dataset({"a": (("x",), np.ones(2))}).to_netcdf(tmp_path / "obs.nc")

        arguments = {"obs_path": str(tmp_path / "obs.nc"), "obs_variable": "chl"}
        assert units.own_obs_units(arguments) is None

    def test_the_description_is_read_with_the_units(self, tmp_path):
        attributes = {"units": "g/kg", "long_name": "Absolute Salinity"}
        xr.Dataset({"sa": (("y", "x"), np.ones((2, 2)), attributes)}).to_netcdf(
            tmp_path / "obs.nc"
        )

        arguments = {"obs_path": str(tmp_path / "obs.nc"), "obs_variable": "sa"}
        assert units.own_obs_attributes(arguments) == ("g/kg", "Absolute Salinity")


class TestMatchups:
    LOOKUP = {"thetao": "degC", "N3_n": "mmol N m-3"}

    def rows(self, selection, own=None, options=None, mapping=None, own_point=None,
             point_options=None):
        mapping = mapping or {"temperature": "thetao", "nitrate": "N3_n", "salinity": "so"}
        return units.matchups(
            mapping,
            selection,
            options or {},
            point_options or {},
            {"gridded": own or [], "point": own_point or []},
            self.LOOKUP,
        )

    def test_one_row_for_each_gridded_recipe_in_the_catalogues_order(self):
        rows = self.rows({("temperature", "woa23"), ("nitrate", "woa23"), ("temperature", "cobe2")})

        assert [row["key"] for row in rows] == [
            "recipe:nitrate:woa23",
            "recipe:temperature:cobe2",
            "recipe:temperature:woa23",
        ]

    def test_woa23_temperature_and_salinity_name_their_variables(self):
        rows = self.rows({("temperature", "woa23"), ("salinity", "woa23")})

        assert {row["key"]: row["obs_variable"] for row in rows} == {
            "recipe:salinity:woa23": "s_an",
            "recipe:temperature:woa23": "t_an",
        }

    def test_variables_without_a_model_variable_are_left_out(self):
        rows = self.rows({("ph", "ices"), ("ph", "glodap"), ("nitrate", "woa23")})

        assert [row["key"] for row in rows] == ["recipe:nitrate:woa23"]

    def test_ices_recipes_follow_the_gridded_ones(self):
        rows = self.rows({("temperature", "ices"), ("nitrate", "ices"), ("temperature", "cobe2")})

        assert [row["key"] for row in rows] == [
            "recipe:temperature:cobe2",
            "point:temperature:ices",
            "point:nitrate:ices",
        ]
        temperature = rows[1]
        assert temperature["title"] == "temperature (ices, point)"
        assert temperature["model"]["parts"] == [{"name": "thetao", "units": "degC"}]
        assert (temperature["obs_variable"], temperature["obs_units"]) == (
            "TEMPPR01",
            "\u00b0C",
        )
        assert rows[2]["obs_units"] == "\u00b5mol/l"
        assert [row["kind"] for row in rows] == ["gridded", "point", "point"]

    def test_point_options_give_the_conversion_already_chosen(self):
        rows = self.rows(
            {("temperature", "ices")},
            point_options={("temperature", "ices"): {"start": None, "obs_adder": -1}},
        )

        assert (rows[0]["obs_multiplier"], rows[0]["obs_adder"]) == (1, -1)

    def test_own_point_data_has_no_units_and_says_so(self):
        own = [{"name": "cruise", "model_variable": "thetao", "obs_path": "x", "obs_adder": 2}]
        (row,) = self.rows(set(), own_point=own)

        assert (row["kind"], row["key"]) == ("point", "ownpoint:0")
        assert row["title"] == "cruise (your own point data)"
        assert (row["obs_variable"], row["obs_units"]) == ("observation", None)
        assert "no units" in row["obs_note"]
        assert row["model"]["parts"] == [{"name": "thetao", "units": "degC"}]
        assert (row["obs_multiplier"], row["obs_adder"]) == (1, 2)

    def test_a_sum_is_listed_by_its_parts(self):
        own = [{"name": "x", "model_variable": "thetao + N3_n", "obs_variable": "v"}]
        (row,) = self.rows(set(), own)

        assert row["model"]["parts"] == [
            {"name": "thetao", "units": "degC"},
            {"name": "N3_n", "units": "mmol N m-3"},
        ]
        assert (row["obs_multiplier"], row["obs_adder"]) == (1, 0)

    def test_each_row_says_what_oceanval_makes_of_its_units(self):
        rows = self.rows(
            {("temperature", "cobe2"), ("nitrate", "woa23"), ("nitrate", "ices")}
        )
        checks = {row["key"]: row["check"] for row in rows}

        assert checks["recipe:temperature:cobe2"]["status"] == "same"
        # WOA23 is per kilogram, and ICES per litre, where the model is per m3
        assert checks["recipe:nitrate:woa23"]["multiplier"] == 1.025
        assert checks["point:nitrate:ices"]["status"] == "same"

    def test_a_sum_is_checked_only_if_its_parts_are_in_the_same_units(self):
        def check(lookup):
            (row,) = units.matchups(
                {"nitrate": "N3_n+N4_n"},
                {("nitrate", "woa23")},
                {},
                {},
                {"gridded": [], "point": []},
                lookup,
            )
            return row["check"]

        assert check({"N3_n": "mmol N m-3", "N4_n": "mmol N/m^3"})["multiplier"] == 1.025
        assert "different units" in check({"N3_n": "mmol N m-3", "N4_n": "mg/m3"})["note"]
        assert "do not give" in check({"N3_n": "mmol N m-3", "N4_n": None})["note"]
        # units written the same way are the same, and ones OceanVal cannot
        # read are named rather than called different
        assert check({"N3_n": "mmol N/m**3", "N4_n": "mmol N/m**3"})["multiplier"] == 1.025
        unread = check({"N3_n": "mmol banana", "N4_n": "mmol banana"})["note"]
        assert "“banana”" in unread and "different units" not in unread

    def test_teos10_variables_are_warned_of(self):
        (row,) = units.matchups(
            {"salinity": "so_abs"},
            {("salinity", "woa23")},
            {},
            {},
            {"gridded": [], "point": []},
            {"so_abs": "1e-3"},
            model_descriptions={"so_abs": "absolute salinity sea_water_absolute_salinity"},
        )

        assert (row["check"]["status"], row["check"]["multiplier"]) == ("warn", 1)
        assert "Absolute Salinity" in row["check"]["note"]
        assert row["model"]["parts"] == [{"name": "so_abs", "units": "1e-3"}]

    def test_own_observations_of_absolute_salinity_are_not_warned_of(self, tmp_path):
        attributes = {"units": "g/kg", "standard_name": "sea_water_absolute_salinity"}
        xr.Dataset({"sa": (("y", "x"), np.ones((2, 2)), attributes)}).to_netcdf(
            tmp_path / "obs.nc"
        )
        own = {
            "name": "salinity",
            "model_variable": "so_abs",
            "obs_path": str(tmp_path / "obs.nc"),
            "obs_variable": "sa",
        }

        (row,) = units.matchups(
            {},
            set(),
            {},
            {},
            {"gridded": [own], "point": []},
            {"so_abs": "1e-3"},
            model_descriptions={"so_abs": "absolute salinity"},
        )

        assert row["check"]["status"] == "same"

    def test_the_model_variables_to_read(self):
        names = units.model_variables(
            {"temperature": "thetao", "nitrate": "N3_n+N4_n"},
            {("temperature", "cobe2"), ("nitrate", "woa23"), ("temperature", "ices")},
            {"gridded": [{"model_variable": "thetao+chl"}], "point": [{"model_variable": "zz"}]},
        )

        # the ICES recipe adds nothing new here, and own point data is included
        assert sorted(names) == ["N3_n", "N4_n", "chl", "thetao", "zz"]


class TestConversions:
    def test_blank_boxes_are_the_defaults(self):
        conversions, errors = units.check_conversions({"a": {"multiplier": " ", "adder": ""}})

        assert (conversions, errors) == ({"a": (1, 0)}, {})

    def test_numbers(self):
        conversions, errors = units.check_conversions(
            {"a": {"multiplier": "0.001", "adder": "-273.15"}, "b": {"multiplier": "2.0"}}
        )

        assert conversions == {"a": (0.001, -273.15), "b": (2, 0)}
        assert isinstance(conversions["b"][0], int)
        assert errors == {}

    @pytest.mark.parametrize(
        "boxes",
        [{"multiplier": "x"}, {"multiplier": "0"}, {"multiplier": "nan"}, {"adder": "inf"}],
    )
    def test_what_cannot_be_used(self, boxes):
        conversions, errors = units.check_conversions({"a": boxes})

        assert conversions == {}
        assert set(errors) == {"a"}

    def test_nothing_sent(self):
        assert units.check_conversions(None) == ({}, {})

    def test_conversions_go_to_the_options_and_entries_they_belong_to(self):
        options = {("nitrate", "woa23"): {"start": None, "vertical": True}}
        own = {"gridded": [{"name": "x", "obs_multiplier": 5}, {"name": "y"}]}

        units.apply_conversions(
            {
                "recipe:nitrate:woa23": (0.001, 0),
                "recipe:temperature:cobe2": (1, 2),
                "own:0": (1, 0),
                "own:1": (3, 4),
                "own:7": (9, 9),
                "nonsense": (9, 9),
            },
            options,
            own,
        )

        assert options == {
            ("nitrate", "woa23"): {"start": None, "vertical": True, "obs_multiplier": 0.001},
            ("temperature", "cobe2"): {"obs_adder": 2},
        }
        assert own == {
            "gridded": [{"name": "x"}, {"name": "y", "obs_multiplier": 3, "obs_adder": 4}]
        }

    def test_point_conversions_go_to_the_point_options_and_own_point_data(self):
        gridded, point = {}, {("temperature", "ices"): {"start": 2000}}
        own = {"point": [{"name": "x"}, {"name": "y", "obs_multiplier": 4}], "gridded": []}

        units.apply_conversions(
            {
                "point:temperature:ices": (1, -273.15),
                "point:nitrate:ices": (2, 0),
                "ownpoint:0": (3, 4),
                "ownpoint:1": (1, 0),
                "ownpoint:5": (9, 9),
            },
            gridded,
            own,
            point,
        )

        assert gridded == {}
        assert point == {
            ("temperature", "ices"): {"start": 2000, "obs_adder": -273.15},
            ("nitrate", "ices"): {"obs_multiplier": 2},
        }
        assert own["point"] == [{"name": "x", "obs_multiplier": 3, "obs_adder": 4}, {"name": "y"}]

    def test_a_conversion_undone_leaves_no_options(self):
        options = {("nitrate", "woa23"): {"obs_multiplier": 2}}

        units.apply_conversions({"recipe:nitrate:woa23": (1, 0)}, options, {})

        assert options == {}


class TestRecipeSource:
    """The source a recipe's matchups are named by, which the oceanval window's
    output step matches against those in the output directory already."""

    @pytest.mark.parametrize(
        "variable, recipe, source",
        [
            # WOA23 temperature and salinity need years for find_recipe, but not for their source
            ("salinity", "woa23", "WOA23"),
            ("temperature", "woa23", "WOA23"),
            ("nitrate", "woa23", "WOA23"),
            ("temperature", "cobe2", "COBE2"),
            ("temperature", "ices", "ICES"),
        ],
    )
    def test_the_source_of_a_built_in_recipe(self, variable, recipe, source):
        assert units.recipe_source(variable, recipe) == source

    def test_an_unknown_recipe_has_none(self):
        assert units.recipe_source("temperature", "nowhere") is None
