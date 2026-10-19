"""analyze()'s transects (see oceanval.analysis_transects): the arguments, what
each option works out along a line, and the forms of the window's pop-out
(see oceanval.analysis_forms)."""

import ast
import glob
import os

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import oceanval
from oceanval import analysis, analysis_forms, analysis_transects, transects
from simulations import ANALYSIS_LEVEL_VALUES, ANALYSIS_LEVELS, write_analysis_simulation

needs_to_transect = pytest.mark.skipif(not transects.available(), reason="nctoolkit cannot extract transects")

YEARS = (2010, 2011, 2012, 2013, 2014, 2015)
NORTH_SOUTH = {"start": [-2.0, 50.0], "end": [-2.0, 53.0]}
EAST_WEST = {"start": [-4.0, 51.0], "end": [0.0, 51.0]}
ALL_YEARS = {"start": 2010, "end": 2015}


@pytest.fixture(scope="module")
def monthly(tmp_path_factory):
    """Six years of monthly output that warms by a degree a year, and by half
    a degree for each column east, so that every answer is known."""
    root = tmp_path_factory.mktemp("transects") / "sim"
    write_analysis_simulation(str(root), years=YEARS, daily=False, trend=1.0, east_west=0.5)
    return str(root)


@pytest.fixture
def no_report(monkeypatch):
    monkeypatch.setattr(analysis, "build_report", lambda folder, info: os.path.join(folder, "landing.html"))


def results(out_dir):
    return os.path.join(out_dir, analysis.FOLDER, analysis.RESULTS)


def line(ends, **options):
    return {**ends, **options}


def run(sim, lines, tmp_path, variables=None, **arguments):
    """analyze() of Temperature's transects, and what it saved of each: (the
    entry of the variable's transects, the data frame of each option of each
    transect)."""
    out = str(tmp_path)
    oceanval.analyze(
        sim, variables or {"Temperature": "thetao"}, transects={"Temperature": lines},
        n_dirs_down=1, out_dir=out, cores=1, view=False, **arguments,
    )
    saved = analysis.load(results(out))["variables"][0]["transects"]
    frames = [
        {name: pd.read_csv(os.path.join(results(out), option["csv"])) for name, option in item["options"].items()}
        for item in saved["lines"]
    ]
    return saved, frames


class TestArguments:
    def check(self, lines):
        return analysis_transects.check_lines(lines, "Temperature")

    @needs_to_transect
    def test_a_transect_is_checked(self):
        assert self.check([line(NORTH_SOUTH, climatology=ALL_YEARS)]) == [
            {"start": [-2.0, 50.0], "end": [-2.0, 53.0], "climatology": ALL_YEARS, "vertical": None, "trends": None}
        ]
        checked = self.check([line(EAST_WEST, vertical=ALL_YEARS, trends=dict(ALL_YEARS, how="percentage_change"))])
        assert checked[0]["vertical"] == ALL_YEARS and checked[0]["trends"]["how"] == "percentage_change"
        # an option left out may be None
        assert self.check([line(NORTH_SOUTH, climatology=ALL_YEARS, vertical=None)])[0]["vertical"] is None

    @pytest.mark.parametrize(
        "lines, message",
        [
            (None, "must be a list of transects"),
            ([], "must be a list of transects"),
            (["north"], "must be a dict"),
            ([{"start": [0, 0]}], "has no 'end'"),
            ([line(NORTH_SOUTH)], "needs at least one of"),
            ([line(NORTH_SOUTH, climatology=ALL_YEARS, colour="red")], "'colour', which it cannot have"),
            ([{"start": [0, 0], "end": [5, 5], "climatology": ALL_YEARS}], "north-south"),
            ([{"start": [0, 0], "end": [0, 0], "climatology": ALL_YEARS}], "same point"),
            ([line(NORTH_SOUTH, climatology={"start": 2010})], "has no end year"),
            ([line(NORTH_SOUTH, vertical={"start": 2015, "end": 2010})], "is before its start"),
            ([line(NORTH_SOUTH, vertical="all")], "must be a dict"),
            ([line(NORTH_SOUTH, climatology=dict(ALL_YEARS, how="absolute"))], "only start and end years"),
            ([line(NORTH_SOUTH, trends=ALL_YEARS)], "need \"how\""),
            ([line(NORTH_SOUTH, trends=dict(ALL_YEARS, how="slope"))], "need \"how\""),
            ([line(NORTH_SOUTH, trends=dict(ALL_YEARS, how="absolute", n=2))], "have only start and end years, and how"),
        ],
    )
    def test_what_is_wrong_is_said(self, lines, message):
        with pytest.raises(ValueError, match=message):
            self.check(lines)

    @needs_to_transect
    def test_the_trends_need_enough_years(self):
        with pytest.raises(ValueError, match="at least 3 years"):
            self.check([line(NORTH_SOUTH, trends={"start": 2010, "end": 2011, "how": "absolute"})])
        with pytest.raises(ValueError, match="at least 2 years"):
            self.check([line(NORTH_SOUTH, trends={"start": 2010, "end": 2010, "how": "absolute_change"})])
        assert self.check([line(NORTH_SOUTH, trends={"start": 2010, "end": 2011, "how": "absolute_change"})])

    def test_there_must_be_something_to_analyze(self):
        with pytest.raises(ValueError, match="nothing to analyze"):
            analysis.check_arguments({"Temperature": "thetao"})

    @needs_to_transect
    def test_transects_count_as_something_to_analyze_and_give_the_years_to_read(self):
        rows = analysis.check_arguments(
            {"Temperature": "thetao"},
            transects={"Temperature": [line(NORTH_SOUTH, climatology={"start": 2011, "end": 2012}, vertical={"start": 2013, "end": 2015})]},
            thickness="z_level",
        )
        assert rows[0]["climatology"] is None
        assert (rows[0]["transects"]["start"], rows[0]["transects"]["end"]) == (2011, 2015)

    def test_a_variable_must_be_one_of_the_variables(self):
        with pytest.raises(ValueError, match="'Salinity'"):
            analysis.check_arguments(
                {"Temperature": "thetao"}, transects={"Salinity": [line(NORTH_SOUTH, climatology=ALL_YEARS)]}
            )


class TestWhatIsWorkedOut:
    """The simulation's thetao at level k, month m, year y, column c (the
    longitudes -4 to 0 are -2 to 2) is LEVEL[k] + m + (y - 2010) + 0.5 c."""

    @needs_to_transect
    def test_the_surface_climatology_is_the_monthly_mean_over_its_years(self, monthly, tmp_path, no_report):
        saved, (frames,) = run(monthly, [line(NORTH_SOUTH, climatology={"start": 2010, "end": 2011})], tmp_path)
        item = saved["lines"][0]
        option = item["options"]["climatology"]
        assert item["direction"] == "north-south" and not item["empty"] and option["title"] == "Surface climatology"
        assert item["description"] == "north–south along 2°W, from 50°N to 53°N" and option["units"] == "degC"
        assert (option["start"], option["end"]) == (2010, 2011) and option["months"] == 12
        frame = frames["climatology"]
        assert set(frame.columns) == {"position", "month", "value"}
        assert sorted(frame.position.unique()) == [50.0, 51.0, 52.0, 53.0] and item["steps"] == 4
        # the surface is the top level, the middle column is 0, and the mean of 2010 and 2011 is 0.5 warmer
        for month, value in zip(frame.month, frame.value):
            assert value == pytest.approx(ANALYSIS_LEVEL_VALUES[0] + month + 0.5)

    @needs_to_transect
    def test_the_vertical_section_is_the_annual_mean(self, monthly, tmp_path, no_report):
        saved, (frames,) = run(monthly, [line(EAST_WEST, vertical=ALL_YEARS)], tmp_path, thickness="z_level")
        assert list(saved["lines"][0]["options"]) == ["vertical"]
        frame = frames["vertical"]
        assert set(frame.columns) == {"position", "depth", "value"}
        assert sorted(frame.depth.unique()) == ANALYSIS_LEVELS
        for _, row in frame.iterrows():
            level = ANALYSIS_LEVEL_VALUES[ANALYSIS_LEVELS.index(row.depth)]
            # 6.5 is the mean month and 2.5 the mean year; the longitudes are 1 degree a column
            assert row.value == pytest.approx(level + 6.5 + 2.5 + 0.5 * (row.position + 2))

    @needs_to_transect
    def test_an_absolute_trend_is_the_slope_of_the_annual_means(self, monthly, tmp_path, no_report):
        saved, (frames,) = run(monthly, [line(EAST_WEST, trends=dict(ALL_YEARS, how="absolute"))], tmp_path)
        option = saved["lines"][0]["options"]["trends"]
        assert option["units"] == "degC per year" and option["title"] == "Trends: absolute"
        assert frames["trends"].value.to_numpy() == pytest.approx(1.0) and (frames["trends"].years == 6).all()
        # over fewer years
        _, (frames,) = run(
            monthly, [line(EAST_WEST, trends={"start": 2012, "end": 2015, "how": "absolute"})], tmp_path / "again"
        )
        assert (frames["trends"].years == 4).all() and frames["trends"].value.to_numpy() == pytest.approx(1.0)

    @needs_to_transect
    def test_a_change_is_the_last_year_minus_the_first(self, monthly, tmp_path, no_report):
        saved, (absolute, percent) = run(
            monthly,
            [
                line(NORTH_SOUTH, trends=dict(ALL_YEARS, how="absolute_change")),
                line(NORTH_SOUTH, trends=dict(ALL_YEARS, how="percentage_change")),
            ],
            tmp_path,
        )
        assert saved["lines"][1]["options"]["trends"]["units"] == "%"
        # 2015 is 5 degrees warmer than 2010; the surface of 2010 has a mean of 10 + 6.5
        assert absolute["trends"].value.to_numpy() == pytest.approx(5.0)
        assert percent["trends"].value.to_numpy() == pytest.approx(100 * 5.0 / 16.5)

    @needs_to_transect
    def test_each_option_has_its_own_years_and_a_line_may_have_all_three(self, monthly, tmp_path, no_report):
        saved, (frames,) = run(
            monthly,
            [
                line(
                    NORTH_SOUTH,
                    climatology={"start": 2010, "end": 2011},
                    vertical={"start": 2014, "end": 2015},
                    trends={"start": 2011, "end": 2013, "how": "absolute_change"},
                )
            ],
            tmp_path,
            thickness="z_level",
        )
        options = saved["lines"][0]["options"]
        assert list(options) == ["climatology", "vertical", "trends"]
        assert [(o["start"], o["end"]) for o in options.values()] == [(2010, 2011), (2014, 2015), (2011, 2013)]
        assert (saved["start"], saved["end"]) == (2010, 2015)
        assert frames["trends"].value.to_numpy() == pytest.approx(2.0)
        # 2014 and 2015 are 4.5 warmer than 2010 on average, and the first row is the shallowest depth
        assert frames["vertical"].value.iloc[0] == pytest.approx(ANALYSIS_LEVEL_VALUES[0] + 6.5 + 4.5)

    @needs_to_transect
    def test_every_variable_has_transects_of_its_own(self, monthly, tmp_path, no_report):
        out = str(tmp_path)
        oceanval.analyze(
            monthly, {"Temperature": "thetao", "Chlorophyll": "P1_Chl+P2_Chl"},
            transects={
                "Temperature": [line(NORTH_SOUTH, vertical=ALL_YEARS), line(EAST_WEST, climatology=ALL_YEARS)],
                "Chlorophyll": [line(EAST_WEST, trends=dict(ALL_YEARS, how="absolute"))],
            },
            n_dirs_down=1, out_dir=out, cores=1, view=False, thickness="z_level",
        )
        entries = {x["name"]: x["transects"] for x in analysis.load(results(out))["variables"]}
        assert [list(x["options"]) for x in entries["Temperature"]["lines"]] == [["vertical"], ["climatology"]]
        assert [list(x["options"]) for x in entries["Chlorophyll"]["lines"]] == [["trends"]]

    @needs_to_transect
    def test_years_without_all_their_months_are_left_out(self, tmp_path, no_report):
        root = tmp_path / "sim"
        write_analysis_simulation(str(root), years=YEARS, daily=False, trend=1.0)
        path = glob.glob(os.path.join(str(root), "2012", "*grid_T.nc"))[0]
        with xr.open_dataset(path) as ds:
            kept = ds.isel(time=list(range(11))).load()
        kept.to_netcdf(path)
        saved, _ = run(
            str(root),
            [
                line(NORTH_SOUTH, trends=dict(ALL_YEARS, how="absolute")),
                line(NORTH_SOUTH, trends={"start": 2012, "end": 2015, "how": "absolute_change"}),
            ],
            tmp_path / "out",
        )
        first, second = (x["options"]["trends"] for x in saved["lines"])
        assert first["notes"] == ["2012 was left out, as the output does not have all 12 months in it."]
        assert second["notes"] == ["2012 does not have all 12 months of output, so there is no change."] and second["empty"]

    @needs_to_transect
    def test_a_line_with_no_values_is_said_to_be_empty(self, monthly, tmp_path, no_report):
        saved, (frames,) = run(
            monthly, [line({"start": [30.0, 20.0], "end": [30.0, 25.0]}, climatology=ALL_YEARS)], tmp_path
        )
        assert saved["lines"][0]["empty"] is True and frames["climatology"].value.isna().all()

    @needs_to_transect
    def test_a_vertical_transect_needs_levels(self, monthly, tmp_path, no_report):
        with pytest.raises(ValueError, match="only one vertical level"):
            oceanval.analyze(
                monthly, {"Chlorophyll": "P1_Chl+P2_Chl"},
                transects={"Chlorophyll": [line(NORTH_SOUTH, vertical=ALL_YEARS)]},
                n_dirs_down=1, out_dir=str(tmp_path), cores=1, view=False, thickness="z_level",
            )

    @needs_to_transect
    def test_a_vertical_transect_needs_the_thickness(self, monthly, tmp_path, no_report):
        with pytest.raises(ValueError, match="needs the cell thickness"):
            oceanval.analyze(
                monthly, {"Temperature": "thetao"}, transects={"Temperature": [line(NORTH_SOUTH, vertical=ALL_YEARS)]},
                n_dirs_down=1, out_dir=str(tmp_path), cores=1, view=False,
            )
        # nor does a surface transect
        run(monthly, [line(NORTH_SOUTH, climatology=ALL_YEARS)], tmp_path)

    @needs_to_transect
    def test_with_a_thickness_each_cell_is_put_at_its_depth(self, monthly, tmp_path, no_report):
        saved, (frames,) = run(monthly, [line(NORTH_SOUTH, vertical=ALL_YEARS)], tmp_path, thickness="e3t")
        option = saved["lines"][0]["options"]["vertical"]
        assert option["depths"] == "thickness"
        frame = frames["vertical"]
        # the cells are 1, 2 and 7 m thick, so their middles are at 0.5, 2 and 6.5 m: the
        # same as the model's levels, which are put on evenly spaced depths between them
        depths = sorted(frame.depth.unique())
        assert len(depths) == analysis_transects.N_DEPTHS and depths[0] == pytest.approx(0.5) and depths[-1] == pytest.approx(6.5)
        top, bottom = frame[frame.depth == depths[0]].value, frame[frame.depth == depths[-1]].value
        assert top.to_numpy() == pytest.approx(ANALYSIS_LEVEL_VALUES[0] + 9.0) and bottom.to_numpy() == pytest.approx(ANALYSIS_LEVEL_VALUES[-1] + 9.0)
        # in between, it is the line between the cells' values
        middle = frame[(frame.depth > 0.5) & (frame.depth < 2.0)].value
        assert ((middle < ANALYSIS_LEVEL_VALUES[0] + 9.0) & (middle > ANALYSIS_LEVEL_VALUES[1] + 9.0)).all()
        # a z-level model's levels are used as they are
        saved, (frames,) = run(monthly, [line(NORTH_SOUTH, vertical=ALL_YEARS)], tmp_path / "z", thickness="z_level")
        assert saved["lines"][0]["options"]["vertical"]["depths"] == "levels"
        assert sorted(frames["vertical"].depth.unique()) == ANALYSIS_LEVELS

    @needs_to_transect
    def test_trends_need_years_of_output(self, tmp_path, no_report):
        root = tmp_path / "sim"
        write_analysis_simulation(str(root), years=(2013,), daily=False)
        with pytest.raises(ValueError, match="need output in at least 2 years of 2013-2014, and it has 1"):
            run(str(root), [line(NORTH_SOUTH, trends={"start": 2013, "end": 2014, "how": "absolute_change"})], tmp_path / "a")
        # the surface climatology of the same year is fine
        saved, _ = run(str(root), [line(NORTH_SOUTH, climatology={"start": 2013, "end": 2013})], tmp_path / "b")
        assert list(saved["lines"][0]["options"]) == ["climatology"]

    @needs_to_transect
    def test_transects_are_in_what_the_analysis_saves(self, monthly, tmp_path, no_report):
        saved, _ = run(
            monthly,
            [line(NORTH_SOUTH, climatology=ALL_YEARS), line(EAST_WEST, trends=dict(ALL_YEARS, how="absolute"))],
            tmp_path,
        )
        assert [x["index"] for x in saved["lines"]] == [1, 2]
        assert saved["files"] == 6
        assert saved["lines"][0]["options"]["climatology"]["csv"] == "temperature_transect_1_climatology.csv"
        assert saved["lines"][1]["options"]["trends"]["csv"] == "temperature_transect_2_trends.csv"


class TestTheSums:
    def test_the_trend_is_a_least_squares_slope(self):
        years = [2000, 2001, 2002, 2003]
        annual = np.array([[1.0, 5.0], [3.0, np.nan], [5.0, 5.0], [7.0, 5.0]])
        trend, count = analysis_transects.slope(annual, years)
        assert trend == pytest.approx([2.0, 0.0]) and list(count) == [4, 3]
        # fewer than three years have no trend
        trend, count = analysis_transects.slope(annual[:2], years[:2])
        assert np.isnan(trend).all() and list(count) == [2, 1]

    def test_annual_output_has_a_mean_for_each_year(self):
        values = np.arange(5, dtype=float).reshape(5, 1)
        times = [(2000 + i, 7) for i in range(5)]
        years, annual = analysis_transects._annual(values, times, 2000, 2004, 1)
        assert years == list(range(2000, 2005)) and annual[:, 0].tolist() == [0, 1, 2, 3, 4]
        # a month of twelve is not a year
        years, annual = analysis_transects._annual(values, times, 2000, 2004, 12)
        assert np.isnan(annual).all()

    def test_annual_output_has_no_monthly_climatology(self):
        positions = np.array([50.0, 51.0])
        values = np.array([[1.0, 2.0], [3.0, 4.0]])
        frame, notes = analysis_transects.derive(
            "climatology", positions, None, values, [(2000, 7), (2001, 7)], {"start": 2000, "end": 2001}, 1
        )
        # one value for each position, the mean of the years
        assert list(frame.columns) == ["position", "value"]
        assert frame.value.tolist() == [2.0, 3.0] and notes == []

    def test_a_percentage_change_is_of_the_size_of_the_first_year(self):
        positions = np.array([50.0, 51.0, 52.0])
        values = np.array([[-10.0, 0.0, 10.0], [-5.0, 5.0, 20.0]])
        times = [(2000, 7), (2001, 7)]
        spec = {"start": 2000, "end": 2001, "how": "percentage_change"}
        frame, _ = analysis_transects.derive("trends", positions, None, values, times, spec, 1)
        value = frame.value.to_numpy()
        # warming of a negative mean is an increase; no change from zero can be a percentage
        assert value[0] == pytest.approx(50.0) and np.isnan(value[1]) and value[2] == pytest.approx(100.0)


    def test_cells_are_at_the_middle_of_their_thickness(self):
        thickness = np.array([[1.0, 2.0], [2.0, 2.0], [7.0, np.nan]])
        depths = analysis_transects.cell_depths(thickness)
        assert depths[:, 0].tolist() == [0.5, 2.0, 6.5]
        # where there is no water, there is no depth
        assert depths[:, 1].tolist()[:2] == [1.0, 3.0] and np.isnan(depths[2, 1])

    def test_each_position_is_put_on_depths_they_share(self):
        depths = np.array([[1.0, 2.0], [3.0, 6.0]])
        values = np.array([[10.0, 20.0], [30.0, 60.0]])
        common, out = analysis_transects.on_common_depths(depths, values)
        assert common[0] == 1.0 and common[-1] == 6.0 and len(common) == analysis_transects.N_DEPTHS
        # the first position has cells from 1 to 3 m, and the second from 2 to 6 m: outside them there are no values
        assert out[0, 0] == 10.0 and np.isnan(out[0, 1]) and np.isnan(out[-1, 0]) and out[-1, 1] == 60.0
        assert 29.0 < np.nanmax(out[:, 0]) <= 30.0


class TestForms:
    def placed(self, sim):
        found = analysis.streams(sim, 1)
        rows = analysis_forms.clean_rows(
            [{"name": "Chlorophyll", "model_variable": "P1_Chl+P2_Chl"}, {"name": "Temperature", "model_variable": "thetao"}]
        )
        placed, errors = analysis_forms.check_rows(rows, found)
        assert errors == {}
        return placed

    def box(self, **changes):
        box = dict(
            analysis_forms.blank_transect(), start_lon="-2", start_lat="50", end_lon="-2", end_lat="53",
            climatology=True, climatology_start="2010", climatology_end="2015",
        )
        box.update(changes)
        return box

    def form(self, placed, thickness="z_level", **variables):
        """The pop-out's boxes, with the variables' transects given (a list of
        boxes for each, which ticks it)."""
        form = analysis_forms.default_transects(placed)
        for name, boxes in variables.items():
            form["rows"][name] = {"use": True, "transects": boxes}
        form["thickness"] = thickness
        return analysis_forms.clean_transects(form, placed)

    def check(self, placed, form):
        return analysis_forms.check_transects(form, placed, lambda name: (2010, 2015), {"e3t", "thetao"}, ".")

    def test_the_form_starts_with_nothing_chosen(self, monthly):
        placed = self.placed(monthly)
        form = analysis_forms.default_transects(placed)
        # each variable has transects of its own, one with nothing in it, which is not one
        assert form["rows"]["Temperature"] == {"use": False, "transects": [analysis_forms.blank_transect()]}
        assert analysis_forms.blank_transect() == {
            "start_lon": "", "start_lat": "", "end_lon": "", "end_lat": "",
            "climatology": False, "climatology_start": "", "climatology_end": "",
            "vertical": False, "vertical_start": "", "vertical_end": "",
            "trends": "", "trends_start": "", "trends_end": "",
        }
        chosen, errors = self.check(placed, analysis_forms.clean_transects(form, placed))
        assert chosen is None and errors == {"rows": "Tick at least one variable to work out transects of."}

    @needs_to_transect
    def test_transects_are_chosen_for_each_variable_on_its_own(self, monthly):
        placed = self.placed(monthly)
        east_west = self.box(
            start_lon="-4", start_lat="51", end_lon="0", end_lat="51", climatology=False, climatology_start="", climatology_end="",
            vertical=True, vertical_start="2012", vertical_end="2014", trends="absolute_change", trends_start="2011", trends_end="2015",
        )
        chosen, errors = self.check(
            placed,
            self.form(placed, Temperature=[self.box(), analysis_forms.blank_transect(), east_west], Chlorophyll=[self.box(end_lat="52")]),
        )
        assert errors == {}
        assert chosen["thickness"] == "z_level"
        assert chosen == {
            "thickness": "z_level",
            "rows": {
                "Temperature": [
                    {"start": [-2.0, 50.0], "end": [-2.0, 53.0], "climatology": ALL_YEARS, "vertical": None, "trends": None},
                    {
                        "start": [-4.0, 51.0], "end": [0.0, 51.0], "climatology": None, "vertical": {"start": 2012, "end": 2014},
                        "trends": {"start": 2011, "end": 2015, "how": "absolute_change"},
                    },
                ],
                "Chlorophyll": [
                    {"start": [-2.0, 50.0], "end": [-2.0, 52.0], "climatology": ALL_YEARS, "vertical": None, "trends": None},
                ],
            }
        }
        # a variable that is not ticked has nothing, whatever its boxes hold, and a surface transect no thickness
        form = self.form(placed, thickness="", Temperature=[self.box()])
        form["rows"]["Chlorophyll"]["transects"] = [self.box(end_lon="9")]
        chosen = self.check(placed, form)[0]
        assert list(chosen["rows"]) == ["Temperature"] and chosen["thickness"] is None

    @needs_to_transect
    def test_what_is_wrong_with_a_transect_is_said_by_its_number(self, monthly):
        placed = self.placed(monthly)
        diagonal = self.box(start_lon="0", start_lat="0", end_lon="5", end_lat="5")
        nothing = self.box(climatology=False, climatology_start="", climatology_end="")
        missing = self.box(start_lon="x", vertical=True, vertical_start="2010")
        short = self.box(climatology=False, trends="absolute", trends_start="2010", trends_end="2011")
        chosen, errors = self.check(placed, self.form(placed, Temperature=[self.box(), diagonal, nothing, missing, short]))
        assert chosen is None and set(errors) == {"Temperature"}
        found = errors["Temperature"]["transects"]
        # the first is fine
        assert set(found) == {"1", "2", "3", "4"}
        assert set(found["1"]) == {"start_lon", "start_lat", "end_lon", "end_lat"} and "north–south or east–west" in found["1"]["start_lon"]
        assert set(found["2"]) == {"options"} and "Tick Surface climatology" in found["2"]["options"]
        assert found["3"]["start_lon"] == "Coordinates must be numbers." and found["3"]["vertical_end"] == "End must be a year, e.g. 2011."
        assert found["4"] == {"trends_end": "Absolute needs at least 3 years, and this is 2."}

    @needs_to_transect
    def test_the_years_are_checked_against_the_variables_output(self, monthly):
        placed = self.placed(monthly)
        chosen, errors = analysis_forms.check_transects(
            self.form(placed, Temperature=[self.box(climatology_start="1990", climatology_end="1991")]),
            placed, lambda name: (2013, 2014),
        )
        assert errors["Temperature"]["transects"]["0"] == {"climatology_start": "The output is from 2013 to 2014."}

    @needs_to_transect
    def test_trends_need_enough_years_of_output(self, monthly):
        placed = self.placed(monthly)
        trends = self.box(climatology=False, trends="absolute_change", trends_start="2013", trends_end="2013")
        check = lambda box, years: analysis_forms.check_transects(self.form(placed, Temperature=[box]), placed, lambda name: years, {"e3t"}, ".")
        # one year of output has no trend of any kind
        _, errors = check(trends, (2013, 2013))
        assert errors["Temperature"]["transects"]["0"]["trends"] == "Absolute change needs at least 2 years of output: Temperature's is only 2013."
        # two years is enough for a change, not for the slope
        assert check(dict(trends, trends_start="2013", trends_end="2014"), (2013, 2014))[1] == {}
        _, errors = check(dict(trends, trends="absolute", trends_start="2013", trends_end="2014"), (2013, 2014))
        assert "needs at least 3 years of output: Temperature's is from 2013 to 2014" in errors["Temperature"]["transects"]["0"]["trends"]
        # what is not a trend is not affected
        assert check(self.box(), (2013, 2013))[1] == {}

    @needs_to_transect
    def test_vertical_needs_the_thickness(self, monthly):
        placed = self.placed(monthly)
        vertical = self.box(vertical=True, vertical_start="2010", vertical_end="2015")
        for thickness, message in (("", "needs the cell thickness"), ("nothing", "not a variable in the output")):
            chosen, errors = self.check(placed, self.form(placed, thickness=thickness, Temperature=[vertical]))
            assert chosen is None and message in errors["thickness"]
        assert self.check(placed, self.form(placed, thickness="e3t", Temperature=[vertical]))[0]["thickness"] == "e3t"

    @needs_to_transect
    def test_vertical_needs_a_variable_with_levels(self, monthly):
        placed = self.placed(monthly)
        vertical = self.box(vertical=True, vertical_start="2010", vertical_end="2015")
        chosen, errors = self.check(placed, self.form(placed, Chlorophyll=[vertical], Temperature=[vertical]))
        assert chosen is None and set(errors) == {"Chlorophyll"}
        assert errors["Chlorophyll"]["transects"] == {
            "0": {"vertical": "It has only one vertical level, so it has no section through the water column."}
        }

    @needs_to_transect
    def test_a_ticked_variable_needs_a_transect(self, monthly):
        placed = self.placed(monthly)
        chosen, errors = self.check(placed, self.form(placed, Temperature=[analysis_forms.blank_transect()]))
        assert chosen is None and "Add a transect for this variable" in errors["Temperature"]["use"]

    @needs_to_transect
    def test_the_script_has_a_line_for_each_transect(self, monthly):
        placed = self.placed(monthly)
        chosen, _ = self.check(
            placed,
            self.form(placed, Temperature=[self.box(trends="absolute", trends_start="2010", trends_end="2015")], Chlorophyll=[self.box()]),
        )
        arguments = analysis_forms.arguments(
            {"simdir": monthly, "ndown": 1, "out_dir": "."}, placed, None, None, None, None, cores=1, transects=chosen
        )
        assert arguments["variables"] == {"Chlorophyll": "P1_Chl+P2_Chl", "Temperature": "thetao"}
        # only the options asked for are written
        assert arguments["transects"]["Temperature"] == [
            {"start": [-2.0, 50.0], "end": [-2.0, 53.0], "climatology": ALL_YEARS, "trends": dict(ALL_YEARS, how="absolute")}
        ]
        text = analysis_forms.script_text(arguments)
        call = ast.parse(text).body[-1].value
        given = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}
        assert given["transects"] == arguments["transects"]
        # nothing is written of transects that were not asked for
        arguments = analysis_forms.arguments(
            {"simdir": monthly, "ndown": 1, "out_dir": "."}, placed, {"Temperature": {}}, None, None, None, cores=1
        )
        assert "transects" not in arguments and "transects=" not in analysis_forms.script_text(arguments)
