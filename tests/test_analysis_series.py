"""analyze()'s long-term and seasonal time series: of the model's domain, of
its regions and at stations (see oceanval.analysis_series), and the forms of
the window's regions and area (see oceanval.analysis_forms)."""

import ast
import glob
import json
import os

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import oceanval
from oceanval import analysis, analysis_forms
from simulations import (
    ANALYSIS_LEVEL_VALUES,
    ANALYSIS_THICKNESS,
    write_analysis_regions,
    write_analysis_simulation,
    write_curvilinear_simulation,
)

LEVELS = np.array(ANALYSIS_LEVEL_VALUES)
THICKNESS = np.array(ANALYSIS_THICKNESS)
# the output is monthly, of 2012-2014, which rises by TREND a year and by EAST_WEST for each column east of the middle one
TREND = 1.0
EAST_WEST = 0.5
YEARS = (2012, 2013, 2014)
SURFACE = LEVELS[0]
MEAN = (LEVELS * THICKNESS).sum() / THICKNESS.sum()


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    root = tmp_path_factory.mktemp("series") / "sim"
    write_analysis_simulation(str(root), years=YEARS, daily=False, trend=TREND, east_west=EAST_WEST)
    return str(root)


@pytest.fixture(scope="module")
def regions(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("regions") / "regions.nc")
    write_analysis_regions(path)
    return path


@pytest.fixture(scope="module")
def gappy(tmp_path_factory):
    """The same simulation with March 2014 missing from its temperature."""
    root = tmp_path_factory.mktemp("gappy") / "sim"
    write_analysis_simulation(str(root), years=YEARS, daily=False, trend=TREND, east_west=EAST_WEST)
    path = glob.glob(os.path.join(str(root), "2014", "*grid_T.nc"))[0]
    with xr.open_dataset(path) as ds:
        kept = ds.isel(time=[month for month in range(12) if month != 2]).load()
    kept.to_netcdf(path)
    return str(root)


@pytest.fixture(scope="module")
def shallow(tmp_path_factory):
    """The same simulation with no water in the deepest level of its east two columns."""
    root = tmp_path_factory.mktemp("shallow") / "sim"
    write_analysis_simulation(str(root), years=YEARS, daily=False, trend=TREND, east_west=EAST_WEST)
    for path in glob.glob(os.path.join(str(root), "*", "*grid_T.nc")):
        with xr.open_dataset(path) as ds:
            ds = ds.load()
        ds["thetao"][:, -1, :, -2:] = np.nan
        ds.to_netcdf(path, encoding={"thetao": {"_FillValue": np.float32(-1e20)}})
    return str(root)


def series(out, name):
    return pd.read_csv(os.path.join(out, analysis.FOLDER, analysis.RESULTS, name))


def base(year, month):
    """The surface temperature in the middle column."""
    return SURFACE + month + TREND * (year - YEARS[0])


def run(sim, out, **options):
    arguments = dict(
        sim_dir=sim,
        variables={"Temperature": "thetao"},
        long_term={"Temperature": {"start": 2012, "end": 2014, "surface": True}},
        seasonal={"Temperature": {"start": 2012, "end": 2014, "surface": True}},
        n_dirs_down=1,
        out_dir=out,
        view=False,
        cores=1,
    )
    arguments.update(options)
    return oceanval.analyze(**arguments)


@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    root = tmp_path_factory.mktemp("plain") / "sim"
    write_analysis_simulation(str(root), years=(2013, 2014), daily=False)
    return str(root)


class TestCoresAndMissing:
    @pytest.mark.parametrize("cores", [None, 0, -1, 1.5, "2", True])
    def test_the_cores_must_be_given(self, plain, tmp_path, cores):
        with pytest.raises(ValueError, match="Give cores"):
            oceanval.analyze(
                plain, {"Temperature": "thetao"},
                climatology={"Temperature": {"start": 2013, "end": 2014, "surface": True}},
                n_dirs_down=1, out_dir=str(tmp_path), view=False, cores=cores,
            )

    def test_cores_have_no_default(self):
        import inspect

        assert inspect.signature(oceanval.analyze).parameters["cores"].default is None

    @pytest.mark.parametrize(
        "given, expected",
        [(None, None), (0, 0.0), ([0, 0], [0.0, 0.0]), ([-1e20, 1e20], [-1e20, 1e20]), (2, 2.0)],
    )
    def test_what_is_treated_as_missing(self, given, expected):
        assert analysis.check_as_missing(given) == expected

    @pytest.mark.parametrize("given", ["0", [1, 2, 3], [], [5, 1], [True], [None]])
    def test_what_cannot_be_treated_as_missing(self, given):
        with pytest.raises(ValueError, match="as_missing"):
            analysis.check_as_missing(given)

    def test_values_treated_as_missing_are_missing(self, plain, tmp_path, no_report):
        out = str(tmp_path)
        # the surface is 10 plus the month: January and February are 11 and 12
        oceanval.analyze(
            plain, {"Temperature": "thetao"},
            climatology={"Temperature": {"start": 2013, "end": 2014, "surface": True}},
            long_term={"Temperature": {"start": 2013, "end": 2014, "surface": True}},
            n_dirs_down=1, out_dir=out, view=False, cores=1, as_missing=[-1000, 12.0],
        )
        monthly = xr.open_dataset(os.path.join(out, analysis.FOLDER, analysis.RESULTS, "temperature_surface_monthly.nc"))
        values = monthly["temperature"].values.reshape(12, -1)
        assert np.isnan(values[:2]).all()
        assert np.allclose(values[2:], 10 + np.arange(3, 13)[:, None])
        assert no_report[0]["as_missing"] == [-1000.0, 12.0]
        # the year is the mean of the 10 months that are there
        annual = series(out, "temperature_surface_long_term.csv")
        assert annual.months.tolist() == [10, 10]
        assert annual.value.iloc[0] == pytest.approx(np.mean(10 + np.arange(3, 13)))


class TestArguments:
    def test_the_series_count_as_something_to_analyze(self):
        rows = analysis.check_arguments(
            {"T": "thetao"}, long_term={"T": {"start": 2000, "end": 2001, "surface": True}}
        )
        assert rows[0]["long_term"] == {
            "start": 2000, "end": 2001, "layers": ["surface"], "window": 1, "show": "absolute",
        }
        assert rows[0]["seasonal"] is None
        with pytest.raises(ValueError, match="nothing to analyze"):
            analysis.check_arguments({"T": "thetao"})

    def test_the_water_column_needs_a_thickness(self):
        with pytest.raises(ValueError, match="needs the cell thickness"):
            analysis.check_arguments(
                {"T": "thetao"}, seasonal={"T": {"start": 2000, "end": 2001, "water_column": "mean"}}
            )

    @pytest.mark.parametrize(
        "area, message",
        [
            ({"lon_lim": [-5, 0]}, "lon_lim and lat_lim together"),
            ({"lon_lim": [0, -5], "lat_lim": [0, 1]}, "more than its minimum"),
            ({"lon_lim": [-5, 0], "lat_lim": [0, 100]}, "between -90 and 90"),
            ({"subregions": "arctic"}, "subregions must be"),
            ({"subregions": "/not/there.nc"}, "does not exist"),
            ({"stations": {"A": [-4, 50], " A ": [-3, 50]}}, "two stations called"),
            ({"stations": {"A": [-4]}}, r"must be \[lon, lat\]"),
            ({"stations": {"A": [-4, 95]}}, "between -90 and 90"),
            ({"stations": {"": [-4, 50]}}, "needs a name"),
        ],
    )
    def test_what_is_wrong_with_the_area_is_said(self, area, message):
        rows = analysis.check_arguments(
            {"T": "thetao"}, long_term={"T": {"start": 2000, "end": 2001, "surface": True}}
        )
        with pytest.raises(ValueError, match=message):
            analysis.check_area(rows, **area)

    def test_the_area_is_for_the_series(self):
        rows = analysis.check_arguments(
            {"T": "thetao"}, climatology={"T": {"start": 2000, "end": 2001, "surface": True}}
        )
        with pytest.raises(ValueError, match="long_term and seasonal"):
            analysis.check_area(rows, stations={"A": [-4, 50]})
        assert analysis.check_area(rows)["stations"] == {}

    def test_longitudes_are_wrapped(self):
        rows = analysis.check_arguments(
            {"T": "thetao"}, long_term={"T": {"start": 2000, "end": 2001, "surface": True}}
        )
        assert analysis.check_area(rows, stations={"A": [356, 50]})["stations"] == {"A": [-4.0, 50.0]}


class TestSeries:
    def test_the_domain_and_the_regions(self, sim, regions, tmp_path, no_report):
        out = str(tmp_path)
        run(sim, out, subregions=regions)
        annual = series(out, "temperature_surface_long_term.csv")
        assert list(annual.columns) == ["kind", "name", "year", "value", "months", "complete"]
        for year in YEARS:
            for name, offset in (("Full Domain", 0), ("West", -1.5), ("East", 1.5)):
                row = annual[(annual.name == name) & (annual.year == year)].iloc[0]
                assert row.value == pytest.approx(base(year, 6.5) + offset * EAST_WEST, abs=1e-4)
                assert row.months == 12 and row.complete
        assert set(annual.kind) == {"domain", "region"}
        monthly = series(out, "temperature_surface_seasonal.csv")
        assert len(monthly) == 3 * 36
        row = monthly[(monthly.name == "East") & (monthly.year == 2013) & (monthly.month == 4)].iloc[0]
        assert row.value == pytest.approx(base(2013, 4) + 1.5 * EAST_WEST, abs=1e-4)
        info = no_report[0]
        assert info["subregions"] == regions and info["variables"][0]["long_term"]["regions"] == ["West", "East"]
        assert info["variables"][0]["seasonal"]["layers"]["surface"]["csv"] == "temperature_surface_seasonal.csv"

    def test_a_full_domain_region_of_the_file_is_not_a_second_one(self, sim, tmp_path, no_report):
        # the file's full domain is a part of the model, so the series of that name is the model's own
        path = str(tmp_path / "full.nc")
        write_analysis_regions(path)
        with xr.open_dataset(path) as ds:
            ds = ds.load()
        ds["fulldomain"] = ds["west"].copy()
        ds["fulldomain"].attrs["long_name"] = "Full Domain"
        ds.to_netcdf(path.replace("full.nc", "full2.nc"))
        out = str(tmp_path / "out")
        os.makedirs(out)
        run(sim, out, subregions=path.replace("full.nc", "full2.nc"))
        annual = series(out, "temperature_surface_long_term.csv")
        assert set(annual.name) == {"Full Domain", "West", "East"}
        assert set(annual[annual.name == "Full Domain"].kind) == {"domain"}
        row = annual[(annual.name == "Full Domain") & (annual.year == 2012)].iloc[0]
        assert row.value == pytest.approx(base(2012, 6.5), abs=1e-4)
        assert no_report[0]["variables"][0]["long_term"]["regions"] == ["West", "East"]

    def test_the_water_column(self, sim, tmp_path, no_report):
        out = str(tmp_path)
        run(
            sim,
            out,
            variables={"Mean": "thetao", "Heat": "thetao"},
            long_term={
                "Mean": {"start": 2012, "end": 2013, "water_column": "mean"},
                "Heat": {"start": 2012, "end": 2013, "water_column": "integral"},
            },
            seasonal=None,
            thickness="e3t",
        )
        mean = series(out, "mean_mean_long_term.csv")
        assert mean.value.tolist() == pytest.approx([MEAN + 6.5, MEAN + 6.5 + TREND], abs=1e-4)
        heat = series(out, "heat_integral_long_term.csv")
        assert heat.value.iloc[0] == pytest.approx((LEVELS * THICKNESS).sum() + 6.5 * THICKNESS.sum(), abs=1e-3)
        assert no_report[0]["variables"][1]["long_term"]["layers"]["integral"]["units"] == "degC m"

    def test_the_bottom_is_the_deepest_value_with_water(self, shallow, regions, tmp_path, no_report):
        out = str(tmp_path)
        run(
            shallow, out, variables={"Bottom": "thetao"},
            long_term={"Bottom": {"start": 2012, "end": 2013, "bottom": True}},
            seasonal={"Bottom": {"start": 2012, "end": 2012, "bottom": True}},
            subregions=regions,
        )
        # the middle column is 4 deep; of the two west and the two east columns, the east ones
        # have no water in the deepest level, so their bottom is the one above
        april = series(out, "bottom_bottom_seasonal.csv")
        april = april[april.month == 4].set_index("name").value
        assert april["East"] == pytest.approx(LEVELS[1] + 4 + 1.5 * EAST_WEST, abs=1e-4)
        assert april["West"] == pytest.approx(LEVELS[2] + 4 - 1.5 * EAST_WEST, abs=1e-4)
        info = no_report[0]["variables"][0]["long_term"]
        assert list(info["layers"]) == ["bottom"] and info["layers"]["bottom"]["title"] == "Bottom"

    def test_a_box_of_the_domain(self, sim, regions, tmp_path, no_report):
        out = str(tmp_path)
        # the west two columns and the middle one
        run(sim, out, subregions=regions, lon_lim=[-4.5, -1.5], lat_lim=[49.0, 54.0])
        annual = series(out, "temperature_surface_long_term.csv")
        domain = annual[(annual.name == "Full Domain") & (annual.year == 2012)].value.iloc[0]
        assert domain == pytest.approx(base(2012, 6.5) + EAST_WEST * (-2 - 1 + 0) / 3, abs=1e-4)
        assert set(annual.name) == {"Full Domain", "West"}
        assert no_report[0]["variables"][0]["long_term"]["notes"] == [
            "East has no cells in the model's domain and box, so it is left out."
        ]
        assert no_report[0]["lon_lim"] == [-4.5, -1.5]

    def test_stations_are_the_nearest_cell(self, sim, tmp_path, no_report):
        out = str(tmp_path)
        # nearest to the cell at -3 and 51, in the west
        run(sim, out, stations={"Near": [-3.1, 51.2], "East": [-0.1, 52.9]})
        monthly = series(out, "temperature_surface_seasonal.csv")
        stations = monthly[monthly.kind == "station"]
        assert set(stations.name) == {"Near", "East"}
        row = stations[(stations.name == "Near") & (stations.year == 2013) & (stations.month == 1)].iloc[0]
        assert row.value == pytest.approx(base(2013, 1) - EAST_WEST, abs=1e-4)
        row = stations[(stations.name == "East") & (stations.year == 2014) & (stations.month == 12)].iloc[0]
        assert row.value == pytest.approx(base(2014, 12) + 2 * EAST_WEST, abs=1e-4)
        found = no_report[0]["variables"][0]["seasonal"]["stations"]
        assert (found["Near"]["lon"], found["Near"]["lat"]) == (-3.0, 51.0)
        assert found["Near"]["distance"] == pytest.approx(22.5, abs=2)
        assert no_report[0]["stations"]["Near"] == {"lon": -3.1, "lat": 51.2}
        # an annual mean of a station is the mean of its months
        annual = series(out, "temperature_surface_long_term.csv")
        assert annual[(annual.name == "Near") & (annual.year == 2012)].value.iloc[0] == pytest.approx(
            base(2012, 6.5) - EAST_WEST, abs=1e-4
        )

    def test_a_station_outside_the_model_is_refused(self, sim, tmp_path, no_report):
        with pytest.raises(ValueError, match="Lost.*on land, or outside the model"):
            run(sim, str(tmp_path), stations={"Lost": [-30.0, 51.0]})

    def test_gaps_in_the_output_are_gaps_in_the_series(self, gappy, tmp_path, no_report):
        out = str(tmp_path)
        run(gappy, out)
        monthly = series(out, "temperature_surface_seasonal.csv")
        missing = monthly[monthly.value.isna()]
        assert [(int(y), int(m)) for y, m in zip(missing.year, missing.month)] == [(2014, 3)]
        annual = series(out, "temperature_surface_long_term.csv")
        year = annual[annual.year == 2014].iloc[0]
        assert year.months == 11 and not year.complete
        # the mean of the 11 months there are
        months = [m for m in range(1, 13) if m != 3]
        assert year.value == pytest.approx(np.mean([base(2014, m) for m in months]), abs=1e-4)
        assert no_report[0]["variables"][0]["long_term"]["layers"]["surface"]["incomplete"] == [2014]
        assert no_report[0]["variables"][0]["long_term"]["dates"]["issues"] == ["1 month with no output: Mar 2014."]

    def test_each_series_has_its_own_years(self, sim, tmp_path, no_report):
        out = str(tmp_path)
        run(
            sim,
            out,
            long_term={"Temperature": {"start": 2013, "end": 2014, "surface": True}},
            seasonal={"Temperature": {"start": 2012, "end": 2012, "surface": True}},
        )
        assert series(out, "temperature_surface_long_term.csv").year.tolist() == [2013, 2014]
        monthly = series(out, "temperature_surface_seasonal.csv")
        assert set(monthly.year) == {2012} and len(monthly) == 12

    def test_the_years_need_output(self, sim, tmp_path, no_report):
        with pytest.raises(ValueError, match="no output in 2020-2021"):
            run(sim, str(tmp_path), long_term={"Temperature": {"start": 2020, "end": 2021, "surface": True}})


def test_the_report(sim, regions, tmp_path):
    out = str(tmp_path)
    landing = run(
        sim,
        out,
        variables={"Temperature": "thetao"},
        long_term={"Temperature": {"start": 2012, "end": 2014, "surface": True, "water_column": "mean"}},
        thickness="e3t",
        subregions=regions,
        stations={"Near": [-3.1, 51.2]},
    )
    pages = os.path.dirname(landing)
    page = open(os.path.join(pages, "001_temperature.html")).read()
    assert "Long-term time series" in page and "Seasonal time series" in page
    assert "Water column: vertical mean" in page
    assert "Near" in page and "Model cell" in page
    # figures and tables are numbered from 1 on the page
    assert "Figure 7" in page and "Table 3" in page and "Figure 8" not in page
    overview = open(landing).read()
    assert "Long-term series" in overview and "Regions." in overview and "Stations." in overview
    assert "Traceback" not in page + overview


class TestForms:
    def test_the_stations_are_checked(self):
        errors = {}
        rows = [["A", "-4.2", "50.2"], ["", "", ""], ["A", "-3", "x"], ["", "5", "50"], ["B", "400", "95"]]
        assert analysis_forms.check_stations(rows, errors) is None
        assert errors["stations"] == {
            "2": {"name": "Another station has this name.", "lat": "This must be a number."},
            "3": {"name": "Name the station."},
            "4": {"lon": "This must be between -180 and 360.", "lat": "This must be between -90 and 90."},
        }
        assert analysis_forms.check_stations([["A", "-4.2", "50.2"], ["", "", ""]]) == {"A": [-4.2, 50.2]}

    def test_the_area_is_checked(self, regions, tmp_path):
        form = analysis_forms.clean_area(
            {"subregions": "file", "subregions_file": regions, "lon_min": "-5", "lon_max": "0",
             "lat_min": "49", "lat_max": "55", "stations": [["A", "-4", "50"]]}
        )
        area, errors = analysis_forms.check_area(form, str(tmp_path))
        assert errors == {}
        assert area == {"subregions": regions, "lon_lim": [-5.0, 0.0], "lat_lim": [49.0, 55.0],
                        "stations": {"A": [-4.0, 50.0]}}
        assert analysis_forms.check_area(analysis_forms.default_area(), str(tmp_path)) == (
            {"subregions": None, "lon_lim": None, "lat_lim": None, "stations": {}}, {}
        )
        bad = analysis_forms.clean_area({"subregions": "file", "subregions_file": "nowhere.nc", "lon_min": "-5"})
        _, errors = analysis_forms.check_area(bad, str(tmp_path))
        assert errors["subregions_file"] == "There is no file at this path."
        assert errors["lon_max"] == "Fill in all four limits, or leave them all empty."

    def test_the_script_has_the_series(self, monkeypatch):
        setup = {"simdir": "/sim", "ndown": 2, "exclude": None, "require": None, "out_dir": "/out"}
        placed = [{"name": "Temperature", "model_variable": "thetao"}]
        series_form = {"Temperature": {"start": 2000, "end": 2009, "surface": True, "water_column": None}}
        area = {"subregions": "nwes", "lon_lim": [-10.0, 5.0], "lat_lim": [48.0, 60.0],
                "stations": {"L4": [-4.217, 50.25]}}
        arguments = analysis_forms.arguments(setup, placed, None, None, None, None, series_form, None, area)
        assert arguments["long_term"] == series_form and "seasonal" not in arguments
        assert arguments["stations"] == {"L4": [-4.217, 50.25]} and arguments["subregions"] == "nwes"
        text = analysis_forms.script_text(arguments)
        ast.parse(text)
        called = []
        monkeypatch.setattr(oceanval, "analyze", lambda **given: called.append(given))
        exec(compile(text, "analysis.py", "exec"), {})
        assert called == [arguments]
        # a script without a series has none of its arguments
        plain = analysis_forms.arguments(setup, placed, series_form, None, None, None, None, None, area)
        assert not {"long_term", "seasonal", "stations", "subregions", "lon_lim", "lat_lim"} & set(plain)


class TestRollingMeanAndShow:
    """The long-term series' "window" (a rolling mean of the annual means) and
    "show" (the annual means, or their change from the first year)."""

    def annual(self, sim, tmp_path, no_report, **entry):
        out = str(tmp_path)
        run(sim, out, seasonal=None, long_term={"Temperature": dict({"start": 2012, "end": 2014, "surface": True}, **entry)})
        frame = series(out, "temperature_surface_long_term.csv")
        return frame, no_report[0]["variables"][0]["long_term"]

    def test_the_annual_means_are_the_default(self, sim, tmp_path, no_report):
        frame, info = self.annual(sim, tmp_path, no_report)
        expected = [np.mean([base(year, month) for month in range(1, 13)]) for year in YEARS]
        assert frame.value.tolist() == pytest.approx(expected)
        assert info["window"] == 1 and info["show"] == "absolute" and info["layers"]["surface"]["show"] == "absolute"

    def test_a_rolling_mean_is_of_that_year_and_those_before(self, sim, tmp_path, no_report):
        frame, info = self.annual(sim, tmp_path, no_report, window=2)
        first, second, third = [np.mean([base(year, month) for month in range(1, 13)]) for year in YEARS]
        assert np.isnan(frame.value.iloc[0])
        assert frame.value.iloc[1:].tolist() == pytest.approx([(first + second) / 2, (second + third) / 2])
        assert info["window"] == 2
        # the years with no value because of the window are not years the output lacks
        assert info["layers"]["surface"]["missing"] == [] and info["layers"]["surface"]["incomplete"] == []

    def test_the_change_is_from_the_first_year(self, sim, tmp_path, no_report):
        frame, info = self.annual(sim, tmp_path, no_report, show="absolute_change")
        assert frame.value.tolist() == pytest.approx([0.0, TREND, 2 * TREND])
        assert info["layers"]["surface"]["units"] == "degC"

    def test_the_percentage_change_is_in_per_cent_of_the_first_year(self, sim, tmp_path, no_report):
        frame, info = self.annual(sim, tmp_path, no_report, show="percentage_change")
        first = np.mean([base(2012, month) for month in range(1, 13)])
        assert frame.value.tolist() == pytest.approx([0.0, 100 * TREND / first, 200 * TREND / first])
        assert info["layers"]["surface"]["units"] == "%"

    def test_the_change_is_from_the_first_year_of_the_rolling_mean(self, sim, tmp_path, no_report):
        frame, _ = self.annual(sim, tmp_path, no_report, window=2, show="absolute_change")
        assert np.isnan(frame.value.iloc[0])
        assert frame.value.iloc[1:].tolist() == pytest.approx([0.0, TREND])

    def test_a_window_or_way_to_show_that_cannot_be_used(self, sim, tmp_path):
        entry = {"Temperature": {"start": 2012, "end": 2014, "surface": True}}
        for given, message in [
            ({"window": 0}, "whole number of years"),
            ({"window": 1.5}, "whole number of years"),
            ({"window": True}, "whole number of years"),
            ({"window": 4}, "more than the 3 years"),
            ({"show": "relative"}, "must be one of"),
            ({"window": 2, "show": "absolute_change", "end": 2013}, "needs at least 3 years"),
            ({"show": "percentage_change", "end": 2012}, "needs at least 2 years"),
        ]:
            with pytest.raises(ValueError, match=message):
                run(sim, str(tmp_path), seasonal=None, long_term={"Temperature": dict(entry["Temperature"], **given)})
        # they are for the long-term series only
        with pytest.raises(ValueError, match="long_term only"):
            run(sim, str(tmp_path), long_term=None, seasonal={"Temperature": dict(entry["Temperature"], window=2)})

    def test_the_figure_and_report_say_what_it_shows(self, sim, tmp_path, no_report):
        from oceanval import analysis_plots as ap

        out = str(tmp_path)
        run(sim, out, seasonal=None, long_term={"Temperature": {"start": 2012, "end": 2014, "surface": True,
                                                                  "window": 2, "show": "percentage_change"}})
        results = os.path.join(out, analysis.FOLDER, analysis.RESULTS)
        info = ap.load(results)
        variable = ap.entry(info, "temperature")
        assert ap.series_legend(variable, variable["long_term"]["layers"]["surface"]) == "Temperature change (%)"
        drawn = ap.long_term_plot(results, "temperature", "surface")
        assert "Temperature change (%)" in drawn.code


class TestSpatialMeans:
    """A spatial mean must not rest on a grid whose cell areas cannot be
    worked out: it is worked out again on a regular grid the model's is
    regridded to, by nearest neighbour and by bilinear interpolation."""

    @pytest.fixture(scope="class")
    def curvilinear(self, tmp_path_factory):
        root = tmp_path_factory.mktemp("curvilinear") / "sim"
        write_curvilinear_simulation(str(root))
        return str(root)

    def test_a_grid_with_cell_bounds_is_left_alone(self, sim, tmp_path, no_report):
        run(sim, str(tmp_path))
        assert no_report[0]["variables"][0]["long_term"]["notes"] == []

    def test_the_grid_is_estimated(self, curvilinear):
        from oceanval import analysis_series

        path = glob.glob(os.path.join(curvilinear, "2013", "*.nc"))[0]
        grid = analysis_series.estimate_grid(path, "sst")
        lon_res, lat_res = grid["res"]
        # the tilt shifts the longitudes a little along the other direction
        assert lon_res == pytest.approx(0.3, abs=0.06) and lat_res == pytest.approx(0.25, abs=0.06)
        # the extent of the cells, with two cells of extra space
        assert grid["lon"][0] == pytest.approx(-10 - 2 * lon_res, abs=1e-6)
        assert grid["lon"][1] == pytest.approx(-10 + 0.3 * 29 + 0.05 * 19 + 2 * lon_res, abs=1e-6)
        assert grid["lat"][0] == pytest.approx(45 - 0.03 * 29 - 2 * lat_res, abs=1e-6)
        assert grid["lat"][1] == pytest.approx(45 + 0.25 * 19 + 2 * lat_res, abs=1e-6)

    def test_a_regular_grid_is_estimated(self, sim):
        from oceanval import analysis_series

        path = glob.glob(os.path.join(sim, "2012", "*grid_T.nc"))[0]
        grid = analysis_series.estimate_grid(path, "thetao")
        assert grid["res"] == [1.0, 1.0]
        assert grid["lon"] == [-6.0, 2.0] and grid["lat"] == [48.0, 55.0]

    def test_a_grid_without_cell_bounds_is_regridded_and_said(self, curvilinear, tmp_path, no_report, monkeypatch):
        from oceanval import analysis_series

        methods = []
        real = analysis_series._mean_once

        def watching(ds, key, n_times):
            methods.append(None)
            return real(ds, key, n_times)

        monkeypatch.setattr(analysis_series, "_mean_once", watching)
        out = str(tmp_path)
        oceanval.analyze(
            curvilinear, {"SST": "sst"},
            long_term={"SST": {"start": 2013, "end": 2013, "surface": True}},
            seasonal={"SST": {"start": 2013, "end": 2013, "surface": True}},
            n_dirs_down=1, out_dir=out, view=False, cores=1,
        )
        # once as it is, and once for each of nearest neighbour and bilinear
        assert len(methods) == 3
        notes = no_report[0]["variables"][0]["long_term"]["notes"]
        assert len(notes) == 1
        assert notes[0].startswith("The raw grid of the model did not have sufficient information to calculate grid cell areas")
        assert "bilinear" in notes[0] and "nearest neighbour" in notes[0]
        # the sst is the same everywhere, so any mean of it is that
        monthly = series(out, "sst_surface_seasonal.csv")
        assert monthly.value.tolist() == pytest.approx(list(10 + np.arange(1, 13)), abs=1e-3)

    def test_regions_are_taken_from_the_raw_model_regridded_to_their_own_lon_lat_grid(
        self, curvilinear, tmp_path, no_report, monkeypatch
    ):
        from oceanval import analysis_series

        # regions on a regular grid that is not the model's: one inside its domain, and one around it
        lat, lon = np.arange(40.0, 56.0, 0.5), np.arange(-14.0, 4.0, 0.5)
        inner = np.where(((lon >= -8) & (lon <= -3))[None, :] & ((lat >= 46) & (lat <= 49))[:, None], 1.0, np.nan)
        regions = str(tmp_path / "regions.nc")
        xr.Dataset(
            {"inner": (("lat", "lon"), inner, {"long_name": "Inner"}),
             "outer": (("lat", "lon"), np.ones((len(lat), len(lon))), {"long_name": "Outer"})},
            coords={"lat": ("lat", lat, {"units": "degrees_north", "standard_name": "latitude"}),
                    "lon": ("lon", lon, {"units": "degrees_east", "standard_name": "longitude"})},
        ).to_netcdf(regions)

        calls = []
        real = analysis_series._mean_once
        monkeypatch.setattr(analysis_series, "_mean_once", lambda *a: calls.append(None) or real(*a))
        out = str(tmp_path / "out")
        oceanval.analyze(
            curvilinear, {"SST": "sst"}, subregions=regions,
            seasonal={"SST": {"start": 2013, "end": 2013, "surface": True}},
            n_dirs_down=1, out_dir=out, view=False, cores=1,
        )
        monthly = series(out, "sst_surface_seasonal.csv")
        assert set(monthly.name) == {"Full Domain", "Inner", "Outer"}
        # the sst is the same everywhere, so the mean of any region is that
        for name in ("Inner", "Outer"):
            assert monthly[monthly.name == name].value.tolist() == pytest.approx(list(10 + np.arange(1, 13)), abs=1e-3)
        # the domain's mean is as it was (as it is, and by nn and bil, as the grid has no cell bounds); each
        # region's is worked out once, on the lon-lat grid of the regions, which needs no regridding of its own
        assert len(calls) == 3 + 2

    def test_a_grid_without_cell_bounds_does_not_taint_the_means_after_it(self, curvilinear, sim, tmp_path, no_report):
        """nctoolkit gives a warning again after every run once it has found it, so the
        means of one grid must not be judged by the warnings of another."""
        oceanval.analyze(
            curvilinear, {"SST": "sst"}, long_term={"SST": {"start": 2013, "end": 2013, "surface": True}},
            n_dirs_down=1, out_dir=str(tmp_path / "curvilinear"), view=False, cores=1,
        )
        assert len(no_report[0]["variables"][0]["long_term"]["notes"]) == 1
        run(sim, str(tmp_path / "regular"))
        assert no_report[1]["variables"][0]["long_term"]["notes"] == []
        out = series(str(tmp_path / "regular"), "temperature_surface_long_term.csv")
        # the weighted mean of the grid, not one regridded
        assert out.value.iloc[0] == pytest.approx(base(2012, 6.5), abs=1e-4)

    def test_the_note_is_in_the_report(self, curvilinear, tmp_path):
        landing = oceanval.analyze(
            curvilinear, {"SST": "sst"},
            climatology={"SST": {"start": 2013, "end": 2013, "surface": True}},
            long_term={"SST": {"start": 2013, "end": 2013, "surface": True}},
            n_dirs_down=1, out_dir=str(tmp_path), view=False, cores=1,
        )
        page = open(os.path.join(os.path.dirname(landing), "001_sst.html")).read()
        # in the time series' text (the climatology has no domain mean)
        assert page.count("did not have sufficient information to calculate grid cell areas") == 1
        assert "Traceback" not in page

    @pytest.mark.parametrize(
        "message, problem",
        [
            ("Grid cell bounds are not available. Constant grid cell weights are used for spatial mean!", True),
            ("CDO warning: Grid cell bounds not available, using constant grid cell area weights for operation", True),
            ("CDO warning: cdo    fldmean (warning): computation of grid cell area weights failed, grid cell corner coordinates missing!", True),
            ("The following year was missing in the dataset: 2014", False),
            ("Layer bounds not available in netCDF file, using constant vertical weights for vertical mean", False),
        ],
    )
    def test_the_warnings_that_make_a_mean_unreliable(self, message, problem):
        import warnings

        from oceanval import analysis_series

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            warnings.warn(message)
        assert analysis_series._grid_problem(caught) is problem
