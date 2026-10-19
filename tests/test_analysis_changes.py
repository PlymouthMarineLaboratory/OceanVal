"""Map temporal changes in analyze() (see oceanval.analysis_changes): the
arguments, the maps worked out, and the report's page."""

import json
import os
import re
import shutil

import numpy as np
import pytest
import xarray as xr

import oceanval
from oceanval import analysis, analysis_changes
from simulations import ANALYSIS_LEVEL_VALUES, ANALYSIS_THICKNESS, write_analysis_simulation

YEARS = (2010, 2011, 2012, 2013, 2014, 2015)
HISTORICAL = {"start": 2010, "end": 2011}
FUTURE = [{"start": 2014, "end": 2015}]
# thetao is its level's value plus the month, a degree for each year after the first, and half a degree for each
# column east of the third: the surface of a month is 10 + month, and the mean of the months 16.5
SURFACE_BASE = ANALYSIS_LEVEL_VALUES[0] + 6.5
BOTTOM_BASE = ANALYSIS_LEVEL_VALUES[-1] + 6.5
MEAN_BASE = float(np.average(ANALYSIS_LEVEL_VALUES, weights=ANALYSIS_THICKNESS)) + 6.5
COLUMNS = np.arange(5) - 2


def entry(**changes):
    return dict({"historical": HISTORICAL, "future": FUTURE, "plot": "absolute_changes", "change": "absolute", "surface": True}, **changes)


@pytest.fixture(scope="module")
def monthly(tmp_path_factory):
    """Six years of monthly output that warms by a degree a year, and by half a degree for each column east."""
    root = tmp_path_factory.mktemp("changes") / "sim"
    write_analysis_simulation(str(root), years=YEARS, daily=False, trend=1.0, east_west=0.5)
    return str(root)


@pytest.fixture
def no_report(monkeypatch):
    monkeypatch.setattr(analysis, "build_report", lambda folder, info: os.path.join(folder, "landing.html"))


def run(sim, tmp_path, variables=None, **arguments):
    out = str(tmp_path / "out")
    analysis_arguments = dict(thickness="e3t", n_dirs_down=1, out_dir=out, cores=1, view=False)
    analysis_arguments.update(arguments)
    oceanval.analyze(sim, variables or {"Temperature": "thetao"}, **analysis_arguments)
    return os.path.join(out, analysis.FOLDER, analysis.RESULTS)


def field(results, name):
    """The one map in a results file, as a 2D array of lat by lon."""
    with xr.open_dataset(os.path.join(results, name)) as ds:
        return ds["temperature"].values.reshape(4, 5)


class TestArguments:
    def check(self, given, thickness="e3t"):
        return analysis.check_arguments({"Temperature": "thetao"}, map_changes={"Temperature": given}, thickness=thickness)

    def test_what_is_asked_is_checked(self):
        rows = self.check(entry(future=FUTURE + [{"start": 2012, "end": 2013}], bottom=True, water_column="mean"))
        asked = rows[0]["map_changes"]
        assert [(x["name"], x["start"], x["end"]) for x in asked["periods"]] == [
            ("historical", 2010, 2011), ("future1", 2014, 2015), ("future2", 2012, 2013),
        ]
        assert asked["layers"] == ["surface", "bottom", "mean"]
        assert (asked["plot"], asked["change"]) == ("absolute_changes", "absolute")
        # the years the files are chosen from are those of all the periods
        assert (asked["start"], asked["end"]) == (2010, 2015)
        # a single future period can be given as it is, and years as text
        assert self.check(entry(future={"start": "2014", "end": 2015}))[0]["map_changes"]["periods"][1]["start"] == 2014

    @pytest.mark.parametrize(
        "given, message",
        [
            (entry(plot="everything"), "The plot of map_changes\\['Temperature'\\] must be one of"),
            (entry(plot=None), "The plot of"),
            (entry(change="relative"), "The change of map_changes\\['Temperature'\\] must be"),
            ({k: v for k, v in entry().items() if k != "historical"}, "has no historical period"),
            (entry(future=[]), "needs a future"),
            (entry(future=None), "needs a future"),
            (entry(future=FUTURE * 3), "at most 2 future periods, not 3"),
            (entry(historical={"start": 2010}), "the historical period of map_changes\\['Temperature'\\] has no end year"),
            (entry(future=[{"start": 2015, "end": 2014}]), "end of future period 1 of map_changes\\['Temperature'\\], 2014, is before"),
            (entry(future=[{"start": "x", "end": 2014}]), "must be a year"),
            (entry(future=FUTURE + FUTURE), "future 2 and future 1 periods"),
            (entry(future=[HISTORICAL]), "future 1 and historical periods of map_changes\\['Temperature'\\] are both 2010-2011"),
            (entry(surface=False), "needs surface=True, bottom=True"),
            (entry(surface=False, water_column="column"), "water_column of map_changes"),
            ("2010", "must be a dict"),
        ],
    )
    def test_what_is_wrong_is_said(self, given, message):
        with pytest.raises(ValueError, match=message):
            self.check(given)

    def test_the_water_column_needs_the_thickness(self):
        with pytest.raises(ValueError, match="needs the cell thickness"):
            self.check(entry(water_column="integral"), thickness=None)

    def test_only_variables_given_can_have_changes(self):
        with pytest.raises(ValueError, match="names 'Salinity', which is not in variables"):
            analysis.check_arguments({"Temperature": "thetao"}, map_changes={"Salinity": entry()})
        with pytest.raises(ValueError, match="there is nothing to analyze"):
            analysis.check_arguments({"Temperature": "thetao"})


class TestWhatIsWorkedOut:
    def test_the_absolute_changes(self, monthly, tmp_path, no_report):
        saved = run(monthly, tmp_path, map_changes={"Temperature": entry(future=FUTURE + [{"start": 2012, "end": 2013}])})
        base = SURFACE_BASE + 0.5 * COLUMNS[None, :] * np.ones((4, 1))
        # the mean of a period's years: 0.5 degrees after the first year for the historical one, 4.5 and 2.5 for the futures
        assert np.allclose(field(saved, "temperature_surface_historical.nc"), base + 0.5)
        assert np.allclose(field(saved, "temperature_surface_future1.nc"), base + 4.5)
        assert np.allclose(field(saved, "temperature_surface_future2.nc"), base + 2.5)
        assert np.allclose(field(saved, "temperature_surface_change1.nc"), 4.0)
        assert np.allclose(field(saved, "temperature_surface_change2.nc"), 2.0)
        with xr.open_dataset(os.path.join(saved, "temperature_surface_change1.nc")) as ds:
            assert ds["temperature"].attrs["units"] == "degC"
            assert "absolute change from 2010-2011 to 2014-2015" in ds["temperature"].attrs["long_name"]

    def test_the_percentage_changes(self, monthly, tmp_path, no_report):
        saved = run(monthly, tmp_path, map_changes={"Temperature": entry(change="percentage")})
        historical = SURFACE_BASE + 0.5 + 0.5 * COLUMNS[None, :] * np.ones((4, 1))
        assert np.allclose(field(saved, "temperature_surface_change1.nc"), 100 * 4.0 / historical, rtol=1e-5)
        with xr.open_dataset(os.path.join(saved, "temperature_surface_change1.nc")) as ds:
            assert ds["temperature"].attrs["units"] == "%"
        # the periods themselves stay in the variable's own units
        with xr.open_dataset(os.path.join(saved, "temperature_surface_future1.nc")) as ds:
            assert ds["temperature"].attrs["units"] == "degC"

    def test_every_layer(self, monthly, tmp_path, no_report):
        saved = run(
            monthly, tmp_path,
            map_changes={"Temperature": entry(surface=True, bottom=True, water_column="mean", change="percentage")},
        )
        info = analysis.load(saved)["variables"][0]["map_changes"]
        assert list(info["layers"]) == ["surface", "bottom", "mean"]
        columns = 0.5 * COLUMNS[None, :] * np.ones((4, 1))
        for layer, base in (("surface", SURFACE_BASE), ("bottom", BOTTOM_BASE), ("mean", MEAN_BASE)):
            assert np.allclose(field(saved, f"temperature_{layer}_change1.nc"), 100 * 4.0 / (base + 0.5 + columns), rtol=1e-5), layer
        assert info["layers"]["bottom"] == {
            "title": "Bottom", "units": "degC", "change_units": "%", "historical": "temperature_bottom_historical.nc",
            "futures": ["temperature_bottom_future1.nc"], "changes": ["temperature_bottom_change1.nc"],
        }

    def test_the_depth_integral_has_its_own_units(self, monthly, tmp_path, no_report):
        saved = run(monthly, tmp_path, map_changes={"Temperature": entry(surface=False, water_column="integral")})
        with xr.open_dataset(os.path.join(saved, "temperature_integral_change1.nc")) as ds:
            assert ds["temperature"].attrs["units"] == "degC m"
            # a degree a year over 10 m of water, and four years apart
            assert np.allclose(ds["temperature"].values, 4.0 * sum(ANALYSIS_THICKNESS))

    def test_what_analysis_json_says(self, monthly, tmp_path, no_report):
        saved = run(monthly, tmp_path, map_changes={"Temperature": entry(plot="changes", change="percentage")})
        info = json.load(open(os.path.join(saved, analysis.INFO_FILE)))["variables"][0]["map_changes"]
        assert (info["plot"], info["change"], info["start"], info["end"]) == ("changes", "percentage", 2010, 2015)
        assert [(x["name"], x["title"], x["start"], x["end"], x["files"]) for x in info["periods"]] == [
            ("historical", "Historical", 2010, 2011, 2), ("future1", "Future 1", 2014, 2015, 2),
        ]
        assert all(x["dates"]["issues"] == [] and not x["dates"]["gaps"] for x in info["periods"])
        assert info["layers"]["surface"]["historical"] == "temperature_surface_historical.nc"

    def test_the_years_between_the_periods_are_not_needed(self, monthly, tmp_path, no_report):
        sim = str(tmp_path / "gaps")
        shutil.copytree(monthly, sim)
        for year in (2012, 2013):
            shutil.rmtree(os.path.join(sim, str(year)))
        saved = run(sim, tmp_path, map_changes={"Temperature": entry()})
        assert np.allclose(field(saved, "temperature_surface_change1.nc"), 4.0)
        info = analysis.load(saved)["variables"][0]["map_changes"]
        assert all(not x["dates"]["gaps"] for x in info["periods"])

    def test_each_period_must_be_in_the_output(self, monthly, tmp_path, no_report):
        with pytest.raises(ValueError, match=r"output is from 2010 to 2015, so the years of the future 1 period of its map_changes, 2015-2016, are not all in it"):
            run(monthly, tmp_path, map_changes={"Temperature": entry(future=[{"start": 2015, "end": 2016}])})
        with pytest.raises(ValueError, match=r"has no output in 2020-2021, the years of the future 1 period"):
            run(monthly, tmp_path, map_changes={"Temperature": entry(future=[{"start": 2020, "end": 2021}])})
        with pytest.raises(ValueError, match=r"the historical period of its map_changes"):
            run(monthly, tmp_path, map_changes={"Temperature": entry(historical={"start": 2008, "end": 2011})})

    def test_a_variable_with_one_level_has_no_bottom_or_water_column(self, monthly, tmp_path, no_report):
        for layers in ({"bottom": True}, {"water_column": "mean"}):
            with pytest.raises(ValueError, match="has only one vertical level"):
                run(monthly, tmp_path, variables={"Chlorophyll": "P1_Chl+P2_Chl"}, map_changes={"Chlorophyll": entry(**layers)})

    def test_it_goes_with_the_other_analyses(self, monthly, tmp_path, no_report):
        saved = run(
            monthly, tmp_path,
            climatology={"Temperature": {"start": 2010, "end": 2011, "surface": True}},
            map_changes={"Temperature": entry()},
        )
        assert os.path.exists(os.path.join(saved, "temperature_surface_annual.nc"))
        assert os.path.exists(os.path.join(saved, "temperature_surface_change1.nc"))


class TestTheSums:
    def test_the_changes(self):
        historical, future = np.array([[10.0, -5.0, 0.0]]), np.array([[12.0, -10.0, 3.0]])
        assert np.array_equal(analysis_changes.change_between(historical, future, "absolute"), [[2.0, -5.0, 3.0]])
        percentage = analysis_changes.change_between(historical, future, "percentage")
        # of the size of the historical value, so that an increase is positive, and nothing where it is 0
        assert np.allclose(percentage[0, :2], [20.0, -100.0]) and np.isnan(percentage[0, 2])

    def test_the_units(self):
        assert analysis_changes.change_units("degC", "absolute") == "degC"
        assert analysis_changes.change_units("degC", "percentage") == "%"


def test_the_report(monthly, tmp_path):
    """The variable's page has a heading, and a figure of the periods and one of the changes for each layer, with
    code to copy for each, and the overview says what was done."""
    landing = oceanval.analyze(
        monthly, {"Temperature": "thetao"}, thickness="e3t", n_dirs_down=1, out_dir=str(tmp_path / "out"), cores=1,
        view=False, figure_code=True,
        map_changes={"Temperature": entry(future=FUTURE + [{"start": 2012, "end": 2013}], bottom=True, change="percentage")},
    )
    overview = open(landing).read()
    page = open(os.path.join(os.path.dirname(landing), "001_temperature.html")).read()
    assert "Traceback" not in overview + page
    assert "Map temporal changes" in page
    assert "the historical period is 2010-2011" in page and "the future 2 period is 2012-2013" in page
    assert "the percentage change" in page
    # one figure for each of the two layers
    assert sorted(set(int(n) for n in re.findall(r"Figure (\d+)</strong>", page))) == [1, 2]
    assert page.count("Copy code to reproduce figure") >= 2
    assert "in 2010-2011, 2014-2015 and 2012-2013, and the percentage change (%) from 2010-2011 to 2014-2015 and to 2012-2013" in page
    assert "Map changes" in overview and "Map temporal changes." in overview


@pytest.mark.parametrize("plot", ["absolute", "changes", "absolute_changes"])
def test_the_report_draws_what_was_asked_for(monthly, tmp_path, plot):
    landing = oceanval.analyze(
        monthly, {"Temperature": "thetao"}, n_dirs_down=1, out_dir=str(tmp_path / "out"), cores=1, view=False,
        map_changes={"Temperature": entry(plot=plot)},
    )
    page = open(os.path.join(os.path.dirname(landing), "001_temperature.html")).read()
    assert "Traceback" not in page
    # one figure, and one caption, whatever is drawn
    assert sorted(set(re.findall(r"Figure (\d+)</strong>", page))) == ["1"]
    assert page.count("Figure 1</strong>") == 1
    assert ("which red is an increase" in page) is (plot != "absolute")
    assert ("the change (degC) from 2010-2011 to 2014-2015" in page.replace("°C", "degC")) is (plot != "absolute")
