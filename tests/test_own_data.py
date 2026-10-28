import inspect

import numpy as np
import pytest
import xarray as xr

import oceanval
from oceanval import own_data
from oceanval.create_recipes import build_recipe_script
from oceanval.parsers import Validator
from oceanval.session import session_info


@pytest.fixture
def points(tmp_path):
    directory = tmp_path / "points"
    directory.mkdir()
    (directory / "obs.csv").write_text("lon,lat,observation\n1,2,3\n")
    return directory


@pytest.fixture
def grid(tmp_path):
    path = tmp_path / "obs.nc"
    xr.Dataset(
        {"chl": (("time", "lat", "lon"), np.ones((2, 2, 2)))},
        coords={"time": [0, 1], "lat": [0.0, 1.0], "lon": [0.0, 1.0]},
    ).to_netcdf(path)
    return path


POINT = {"name": "chl", "source": "cruise", "model_variable": "thetao"}


def required(kind):
    return {field["name"] for field in own_data.FIELDS[kind] if field["required"]}


class TestFields:
    @pytest.mark.parametrize(
        "kind, function",
        [
            ("point", Validator.add_point_comparison),
            ("gridded", Validator.add_gridded_comparison),
        ],
    )
    def test_every_argument_but_the_recipe_is_offered(self, kind, function):
        arguments = set(inspect.signature(function).parameters) - {"self", "recipe"}
        offered = [field["name"] for field in own_data.FIELDS[kind]]

        assert set(offered) == arguments
        assert len(offered) == len(set(offered))

    def test_what_has_to_be_given(self):
        assert required("point") == {"name", "source", "model_variable", "obs_path"}
        assert required("gridded") == {
            "name",
            "source",
            "model_variable",
            "obs_path",
            "obs_variable",
            "climatology",
        }

    def test_the_defaults_are_the_calls(self):
        for kind, function in (
            ("point", Validator.add_point_comparison),
            ("gridded", Validator.add_gridded_comparison),
        ):
            parameters = inspect.signature(function).parameters
            for field in own_data.FIELDS[kind]:
                if field["default"] is not None:
                    assert field["default"] == parameters[field["name"]].default


class TestPoint:
    def test_an_entry(self, points, tmp_path):
        arguments, errors = own_data.check_entry(
            "point",
            dict(POINT, obs_path="points", start="2000", vertical=False, binning="1, 2"),
            cwd=str(tmp_path),
        )

        assert errors == {}
        # relative to the directory worked in, and only what is not a default
        assert arguments == dict(
            POINT, obs_path=str(points), start=2000, binning=[1, 2]
        )

    def test_everything_needed_is_reported(self):
        _, errors = own_data.check_entry("point", {})

        assert set(errors) == required("point")

    def test_boxes_that_cannot_be_used(self, points):
        _, errors = own_data.check_entry(
            "point",
            dict(
                name="a_b",
                source="c_d",
                model_variable="x",
                obs_path=str(points),
                start="soon",
                obs_adder="lots",
                binning="1",
                point_time_res=["week"],
            ),
        )

        assert set(errors) == {
            "name",
            "source",
            "start",
            "obs_adder",
            "binning",
            "point_time_res",
        }

    def test_the_directory_must_have_csv_files(self, tmp_path):
        (tmp_path / "empty").mkdir()

        for path in ("nowhere", "empty"):
            _, errors = own_data.check_entry(
                "point", dict(POINT, obs_path=path), cwd=str(tmp_path)
            )
            assert set(errors) == {"obs_path"}

    def test_a_csv_file_can_be_the_path(self, points):
        csv = sorted(points.glob("*.csv"))[0]

        arguments, errors = own_data.check_entry(
            "point", dict(POINT, obs_path=str(csv))
        )
        assert errors == {}
        assert arguments["obs_path"] == str(csv)

    def test_the_calls_own_checks_are_shown_with_the_box(self, points):
        (points / "bad.csv").write_text("lon,lat,observation,colour\n1,2,3,red\n")

        _, errors = own_data.check_entry(
            "point", dict(POINT, obs_path=str(points))
        )
        assert "colour" in errors["obs_path"]

        (points / "bad.csv").unlink()
        _, errors = own_data.check_entry(
            "point", dict(POINT, obs_path=str(points), vertical=True)
        )
        assert set(errors) == {"vertical"}

    def test_a_clash_with_an_earlier_entry(self, points):
        first, _ = own_data.check_entry("point", dict(POINT, obs_path=str(points)))
        existing = [("point", first)]

        _, errors = own_data.check_entry(
            "point",
            dict(POINT, source="other", model_variable="so", obs_path=str(points)),
            existing,
        )
        assert set(errors) == {"model_variable"}

        # a new source for the same variable is fine
        _, errors = own_data.check_entry(
            "point", dict(POINT, source="other", obs_path=str(points)), existing
        )
        assert errors == {}

    def test_oceanvals_own_definitions_are_left_alone(self, points):
        before = (list(oceanval.definitions.keys), dict(session_info["short_title"]))

        own_data.check_entry(
            "point", dict(POINT, short_title="Chl", obs_path=str(points))
        )

        assert (
            list(oceanval.definitions.keys),
            dict(session_info["short_title"]),
        ) == before

    def test_the_same_entry_can_be_tried_again(self, points):
        # a short title is remembered by the calls, but not between checks
        for title in ("One", "Two"):
            _, errors = own_data.check_entry(
                "point", dict(POINT, short_title=title, obs_path=str(points))
            )
            assert errors == {}


class TestGridded:
    GRIDDED = dict(
        name="chl",
        source="cruise",
        model_variable="thetao",
        obs_variable="chl",
        climatology="no",
    )

    def test_an_entry(self, grid):
        arguments, errors = own_data.check_entry(
            "gridded", dict(self.GRIDDED, obs_path=str(grid), file_check=True)
        )

        assert errors == {}
        assert arguments == dict(
            self.GRIDDED, obs_path=str(grid), climatology=False
        )

    def test_everything_needed_is_reported(self):
        _, errors = own_data.check_entry("gridded", {})

        assert set(errors) == required("gridded")

    def test_climatology_must_be_chosen(self, grid):
        form = dict(self.GRIDDED, obs_path=str(grid), climatology="")

        assert set(own_data.check_entry("gridded", form)[1]) == {"climatology"}

    def test_the_variable_must_be_in_the_file(self, grid):
        _, errors = own_data.check_entry(
            "gridded", dict(self.GRIDDED, obs_path=str(grid), obs_variable="sst")
        )

        assert set(errors) == {"obs_variable"}

    def test_unticking_the_file_check_is_kept(self, grid):
        arguments, errors = own_data.check_entry(
            "gridded",
            dict(
                self.GRIDDED, obs_path=str(grid), obs_variable="sst", file_check=False
            ),
        )

        assert errors == {}
        assert arguments["file_check"] is False

    def test_a_url_is_not_looked_for(self):
        arguments, errors = own_data.check_entry(
            "gridded",
            dict(
                self.GRIDDED,
                obs_path="https://example.org/data.nc",
                thredds=True,
                file_check=False,
            ),
        )

        assert errors == {}
        assert arguments["obs_path"] == "https://example.org/data.nc"

    def test_no_file_matches(self, tmp_path):
        _, errors = own_data.check_entry(
            "gridded",
            dict(self.GRIDDED, obs_path="none/*.nc"),
            cwd=str(tmp_path),
        )

        assert set(errors) == {"obs_path"}


class TestScript:
    def test_the_calls_are_written_before_the_matchup(self):
        entries = {
            "point": [dict(POINT, obs_path="/data/points", obs_multiplier=2)],
            "gridded": [
                dict(
                    name="chl",
                    source="grid",
                    model_variable="thetao",
                    obs_path="/data/obs.nc",
                    obs_variable="chl",
                    climatology=True,
                )
            ],
        }

        script = build_recipe_script(
            "/sim", 2, {}, (2000, 2001), "global", own_data=entries
        )

        assert (
            'oceanval.add_point_comparison(\n    name="chl",\n    source="cruise",\n'
            '    model_variable="thetao",\n    obs_path="/data/points",\n'
            "    obs_multiplier=2,\n)\n"
        ) in script
        assert "    climatology=True,\n)" in script
        assert script.index("add_point_comparison(") < script.index(
            "add_gridded_comparison(\n    name=\"chl\""
        )
        assert script.index("add_gridded_comparison(\n    name=\"chl\"") < script.index(
            "oceanval.matchup("
        )
        # and it is Python that registers them
        compile(script, "matchup.py", "exec")

    def test_without_them_nothing_is_added(self):
        script = build_recipe_script("/sim", 2, {}, (2000, 2001), "global")

        assert "Your own observations" not in script
