"""compare_simulations(): the arguments, each simulation analysed into a folder of its own,
and, when the grids are unified, the same cells in all."""

import glob
import inspect
import json
import os

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import oceanval
from oceanval import sim_compare
from oceanval import analysis
from conftest import fvcom_arguments, quietly, same_as_analyze
from viewer_simulations import fvcom_months, fvcom_simulation
from simulations import (
    ANALYSIS_LEVEL_VALUES,
    ANALYSIS_THICKNESS,
    B_LATS,
    B_LONS,
    COMPARE_CLIMATOLOGY as CLIMATOLOGY,
    COMPARE_LONG_TERM,
    COMPARE_YEARS as YEARS,
    TREND_A,
    TREND_B,
    analysis_peak_day,
    every_option,
    write_analysis_simulation,
)

# surface temperature is 10 plus the month, plus the trend for each year after the first


@pytest.fixture(scope="module")
def sims(compared_monthly):
    """Two simulations: A on the usual grid and B on a finer one, which rises faster."""
    return compared_monthly


def transects_available():
    from oceanval import transects

    return transects.available()


def given(sims, **changes):
    a, b = sims
    arguments = dict(
        simulations=[
            {"name": "Base run", "sim_dir": a, "n_dirs_down": 1},
            {"name": "Warm run", "sim_dir": b, "n_dirs_down": 1},
        ],
        variables={"Temperature": "thetao"},
        climatology=CLIMATOLOGY,
        view=False,
        cores=1,
    )
    arguments.update(changes)
    return arguments


def results(out):
    return os.path.join(str(out), sim_compare.FOLDER, sim_compare.RESULTS)


def field(out, sim_key, name):
    with xr.open_dataset(os.path.join(results(out), sim_key, name)) as ds:
        return ds["temperature"].load()


def values(path, variable):
    """A variable's values in path, one row for each time."""
    with xr.open_dataset(path) as ds:
        data = ds[variable].values
    return data.reshape(data.shape[0], -1) if data.ndim > 2 else data.reshape(1, -1)


class TestTimesAreReadOnce:
    """The times of the files are read once: what the window's check of the dates read is handed to the run."""

    @staticmethod
    def rows(shift=None):
        from oceanval import analysis

        rows = analysis.check_arguments({"Temperature": "thetao"}, CLIMATOLOGY, None, None, shift, None, None, None, None)
        for row in rows:
            row["as_missing"] = None
        return rows

    @pytest.fixture
    def reads(self, monkeypatch):
        from oceanval import matchall

        read = []
        original = matchall._read_times

        def spy(path, *arguments):
            read.append(path)
            return original(path, *arguments)

        monkeypatch.setattr(matchall, "_read_times", spy)
        return read

    def test_nothing_is_read_again_if_the_index_is_there(self, sims, tmp_path, reads):
        from oceanval import analysis, model_check

        first = self.rows()
        analysis.locate_rows(first, sims[0], 1, [], [], None)
        assert len(reads) == len(first[0]["files"]) > 0
        index = str(tmp_path / "times.pkl")
        model_check.save_times_index(index, first[0]["times"])
        reads.clear()
        again = self.rows()
        analysis.locate_rows(again, sims[0], 1, [], [], None, index)
        assert reads == []
        assert again[0]["time_res"] == first[0]["time_res"]
        assert again[0]["climatology"]["files"] == first[0]["climatology"]["files"]
        assert set(again[0]["times"]) == set(first[0]["times"])

    def test_only_a_file_that_has_changed_is_read_again(self, sims, tmp_path, reads):
        from oceanval import analysis, model_check

        first = self.rows()
        analysis.locate_rows(first, sims[0], 1, [], [], None)
        index = str(tmp_path / "times.pkl")
        model_check.save_times_index(index, first[0]["times"])
        changed = first[0]["files"][0]
        stat = os.stat(changed)
        os.utime(changed, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
        try:
            reads.clear()
            analysis.locate_rows(self.rows(), sims[0], 1, [], [], None, index)
            assert reads == [changed]
        finally:
            os.utime(changed, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    @pytest.mark.parametrize("garbage", ["missing", "unreadable"])
    def test_an_index_that_is_not_usable_means_the_files_are_read(self, sims, tmp_path, reads, garbage):
        from oceanval import analysis

        index = str(tmp_path / "times.pkl")
        if garbage == "unreadable":
            with open(index, "w") as file:
                file.write("not a pickle")
        rows = self.rows()
        analysis.locate_rows(rows, sims[0], 1, [], [], None, index)
        assert len(reads) == len(rows[0]["files"]) > 0

    def test_the_times_are_shifted_as_they_are_when_read(self, sims, tmp_path, reads):
        from oceanval import analysis, model_check

        first = self.rows(shift=1)
        analysis.locate_rows(first, sims[0], 1, [], [], 1)
        # the index holds the times as read, before the shift
        plain = self.rows()
        analysis.locate_rows(plain, sims[0], 1, [], [], None)
        index = str(tmp_path / "times.pkl")
        model_check.save_times_index(index, plain[0]["times"])
        reads.clear()
        again = self.rows(shift=1)
        analysis.locate_rows(again, sims[0], 1, [], [], 1, index)
        assert reads == []
        for path in first[0]["times"]:
            pd.testing.assert_frame_equal(again[0]["times"][path], first[0]["times"][path])

    def test_the_resolution_of_a_stream_is_not_read_when_there_is_nothing_to_choose(self, sims, monkeypatch):
        from oceanval import analysis

        asked = []
        monkeypatch.setattr(analysis, "stream_time_res", lambda files, fvcom=None: asked.append(files))
        rows = self.rows()
        analysis.locate_rows(rows, sims[0], 1, [], [], None)
        assert asked == [] and rows[0]["time_res"]

    def test_a_calendar_is_read_once_for_the_files_of_a_pattern(self, sims, monkeypatch):
        from oceanval import analysis, time_stamps

        asked = []
        original = time_stamps.calendar
        monkeypatch.setattr(time_stamps, "calendar", lambda path: asked.append(path) or original(path))
        rows = self.rows(shift=1) * 2
        analysis.locate_rows(rows, sims[0], 1, [], [], 1)
        assert len(asked) == 1

    def test_each_simulation_is_given_its_own_index(self, sims, tmp_path, no_report, monkeypatch):
        given_to = {}
        original = sim_compare.locate_rows

        def spy(rows, sim_dir, *arguments, **options):
            given_to[sim_dir] = arguments[-1]
            return original(rows, sim_dir, *arguments, **options)

        monkeypatch.setattr(sim_compare, "locate_rows", spy)
        arguments = given(sims, out_dir=str(tmp_path))
        arguments["simulations"][0]["times_index"] = str(tmp_path / "a.pkl")
        oceanval.compare_simulations(**arguments)
        assert given_to == {sims[0]: str(tmp_path / "a.pkl"), sims[1]: None}

    def test_an_index_must_be_a_path(self, sims, tmp_path):
        arguments = given(sims, out_dir=str(tmp_path))
        arguments["simulations"][0]["times_index"] = 3
        with pytest.raises(ValueError, match="times_index"):
            oceanval.compare_simulations(**arguments)


class TestArguments:
    def test_cores_have_no_default(self):
        assert inspect.signature(oceanval.compare_simulations).parameters["cores"].default is None

    @pytest.mark.parametrize("cores", [None, 0, 1.5, "2", True])
    def test_the_cores_must_be_given(self, sims, tmp_path, cores):
        with pytest.raises(ValueError, match="Give cores"):
            oceanval.compare_simulations(**given(sims, cores=cores, out_dir=str(tmp_path)))

    def test_simulations_are_needed(self, sims, tmp_path):
        with pytest.raises(ValueError, match="Give simulations"):
            oceanval.compare_simulations(**given(sims, simulations=None, out_dir=str(tmp_path)))

    def test_one_simulation_is_not_a_comparison(self, sims):
        with pytest.raises(ValueError, match="at least two"):
            sim_compare.check_simulations([{"name": "A", "sim_dir": sims[0]}])

    def test_at_most_five(self, sims):
        many = [{"name": f"S{n}", "sim_dir": sims[0]} for n in range(6)]
        with pytest.raises(ValueError, match="At most 5"):
            sim_compare.check_simulations(many)
        assert len(sim_compare.check_simulations(many[:5])) == 5

    @pytest.mark.parametrize(
        "change, message",
        [
            ({"name": ""}, "needs a name"),
            ({"name": "!!"}, "letter or a digit"),
            ({"sim_dir": "/no/such/dir"}, "There is no directory"),
            ({"sim_dir": None}, "Give the sim_dir"),
            ({"n_dirs_down": -1}, "whole number"),
            ({"n_dirs_down": "x"}, "whole number"),
            ({"time_shift": 1.5}, "time_shift"),
            ({"as_missing": "0"}, "as_missing"),
            ({"colour": "red"}, "colour"),
        ],
    )
    def test_a_simulation_that_is_wrong(self, sims, change, message):
        one = {"name": "A", "sim_dir": sims[0], **change}
        with pytest.raises(ValueError, match=message):
            sim_compare.check_simulations([one, {"name": "B", "sim_dir": sims[1]}])

    def test_names_that_would_name_files_the_same(self, sims):
        with pytest.raises(ValueError, match="same"):
            sim_compare.check_simulations(
                [{"name": "Run 1", "sim_dir": sims[0]}, {"name": "run-1", "sim_dir": sims[1]}]
            )

    def test_a_dict_of_simulations_by_name_and_the_defaults(self, sims):
        checked = sim_compare.check_simulations(
            {"A": sims[0], "B": {"sim_dir": sims[1], "thickness": "e3t_b"}}, thickness="e3t", time_shift=-1, as_missing=0
        )
        assert [x["name"] for x in checked] == ["A", "B"]
        assert [x["thickness"] for x in checked] == ["e3t", "e3t_b"]
        assert all(x["time_shift"] == -1 and x["as_missing"] == 0.0 and x["n_dirs_down"] == 2 for x in checked)

    def test_every_simulation_needs_every_variable(self):
        with pytest.raises(ValueError, match="needs a model variable in 'B'"):
            sim_compare.check_variables({"Temperature": {"A": "thetao"}}, ["A", "B"])
        with pytest.raises(ValueError, match="not one of the simulations"):
            sim_compare.check_variables({"Temperature": {"A": "t", "B": "t", "C": "t"}}, ["A", "B"])
        with pytest.raises(ValueError, match="variables must be a dict"):
            sim_compare.check_variables({}, ["A", "B"])

    def test_a_model_variable_alone_is_the_same_in_each(self):
        assert sim_compare.check_variables({"T": "thetao", "C": {"A": "a+b", "B": "c"}}, ["A", "B"]) == {
            "A": {"T": "thetao", "C": "a+b"},
            "B": {"T": "thetao", "C": "c"},
        }

    def test_a_bad_unify_is_refused_before_anything_is_done(self, sims, tmp_path):
        with pytest.raises(ValueError, match="baseline"):
            oceanval.compare_simulations(**given(sims, unify={"baseline": "Nobody"}, out_dir=str(tmp_path)))
        assert not os.path.exists(os.path.join(str(tmp_path), sim_compare.FOLDER))

    def test_the_water_column_needs_a_thickness_in_every_simulation(self, sims, tmp_path):
        columns = {"Temperature": {"start": 2012, "end": 2014, "water_column": "mean"}}
        with pytest.raises(ValueError, match="thickness"):
            oceanval.compare_simulations(**given(sims, climatology=columns, out_dir=str(tmp_path)))

    def test_the_variables_must_be_in_the_output_of_each_simulation(self, sims, tmp_path):
        with pytest.raises(ValueError, match="Warm run: .*votemper"):
            oceanval.compare_simulations(
                **given(sims, variables={"Temperature": {"Base run": "thetao", "Warm run": "votemper"}}, out_dir=str(tmp_path))
            )

    def test_the_years_must_be_in_every_simulation(self, sims, tmp_path):
        late = {"Temperature": {"start": 2013, "end": 2016, "surface": True}}
        with pytest.raises(ValueError, match="2012 to 2014"):
            oceanval.compare_simulations(**given(sims, climatology=late, out_dir=str(tmp_path)))

    def test_the_units_must_agree(self, sims, tmp_path):
        other = tmp_path / "other"
        write_analysis_simulation(str(other), years=YEARS, daily=False)
        for path in glob.glob(str(other / "*" / "*grid_T.nc")):
            with xr.open_dataset(path) as ds:
                ds = ds.load()
            ds["thetao"].attrs["units"] = "K"
            ds.to_netcdf(path)
        with pytest.raises(ValueError, match=r"not in the same units.*degC.*K.*give unit_conversions"):
            oceanval.compare_simulations(
                **given(sims, simulations=[
                    {"name": "Base run", "sim_dir": sims[0], "n_dirs_down": 1},
                    {"name": "Kelvin run", "sim_dir": str(other), "n_dirs_down": 1},
                ], out_dir=str(tmp_path / "out"))
            )

    @pytest.mark.parametrize(
        "conversions, message",
        [
            ("K", "must be a dict"),
            ({"Salinity": {"Warm run": {"adder": 1}}}, "'Salinity', which is not one of the variables"),
            ({"Temperature": {"Cold run": {"adder": 1}}}, "'Cold run', which is not one of the simulations"),
            ({"Temperature": {"Base run": {"adder": 1}}}, "the first simulation"),
            ({"Temperature": {"Warm run": 2}}, "a multiplier and an adder"),
            ({"Temperature": {"Warm run": {"times": 2}}}, "a multiplier and an adder"),
            ({"Temperature": {"Warm run": {"multiplier": "2"}}}, "must be a number"),
            ({"Temperature": {"Warm run": {"adder": float("nan")}}}, "must be a number"),
            ({"Temperature": {"Warm run": {"multiplier": 0}}}, "more than 0"),
            ({"Temperature": {"Warm run": {"multiplier": -1}}}, "more than 0"),
        ],
    )
    def test_a_bad_unit_conversion_is_refused_before_anything_is_done(self, sims, tmp_path, conversions, message):
        with pytest.raises(ValueError, match=message):
            oceanval.compare_simulations(**given(sims, unit_conversions=conversions, out_dir=str(tmp_path)))
        assert not os.path.exists(os.path.join(str(tmp_path), sim_compare.FOLDER))

    def test_unit_conversions_are_checked_to_numbers(self):
        names = ["Base run", "Warm run"]
        checked = sim_compare.check_unit_conversions(
            {"Temperature": {"Warm run": {"adder": -273.15}}, "Chlorophyll": {"Warm run": {"multiplier": 1, "adder": 0}}},
            {"Temperature": "thetao", "Chlorophyll": "chl"},
            names,
        )
        # 1 and 0 too: it says units written otherwise are the first simulation's
        assert checked == {"Temperature": {"Warm run": (1.0, -273.15)}, "Chlorophyll": {"Warm run": (1.0, 0.0)}}
        assert sim_compare.check_unit_conversions(None, {"Temperature": "thetao"}, names) == {}

    @pytest.mark.parametrize(
        "units, multiplier, adder, into, text",
        [
            ("K", 1, -273.15, "degC", "from K into degC (273.15 taken away)"),
            ("mol m-3", 1000, 0, "mmol m-3", "from mol m-3 into mmol m-3 (multiplied by 1000)"),
            ("a", 0.5, 2, "b", "from a into b (multiplied by 0.5 and 2 added)"),
            ("degree_Celsius", 1, 0, "degC", "from degree_Celsius into degC (the same units, written otherwise)"),
            ("", 1, 0, "degC", "from no units into degC (the same units, written otherwise)"),
        ],
    )
    def test_a_conversion_is_said_in_words(self, units, multiplier, adder, into, text):
        assert sim_compare.describe_conversion(units, multiplier, adder, into) == text


class TestUnitConversions:
    """A simulation in other units than the first's is converted into them as it is read, so every analysis of the
    same output, given in K and in degC (and its chlorophyll in ug and in mg), gives the same answers."""

    OPTIONS = dict(
        variables={"Temperature": "thetao", "Chlorophyll": "P1_Chl+P2_Chl"},
        climatology={"Temperature": {"start": 2012, "end": 2014, "surface": True, "bottom": True, "water_column": "mean"}},
        phenology={"Chlorophyll": {"start": 2012, "end": 2014}},
        long_term={"Temperature": {"start": 2012, "end": 2014, "surface": True, "water_column": "integral"}},
        seasonal={"Temperature": {"start": 2012, "end": 2014, "surface": True}},
        map_changes={
            "Temperature": {
                "historical": {"start": 2012, "end": 2012},
                "future": [{"start": 2014, "end": 2014}],
                "plot": "absolute_changes",
                "change": "percentage",
                "surface": True,
            }
        },
        unit_conversions={
            "Temperature": {"Kelvin run": {"adder": -273.15}},
            "Chlorophyll": {"Kelvin run": {"multiplier": 0.001}},
        },
        thickness="e3t",
        stations={"Station": [-2.0, 51.5]},
    )
    LINE = {
        "start": [-2.0, 50.0],
        "end": [-2.0, 53.0],
        "climatology": {"start": 2012, "end": 2014},
        "vertical": {"start": 2012, "end": 2014},
        "trends": {"start": 2012, "end": 2014, "how": "percentage_change"},
    }

    @pytest.fixture(scope="class")
    @classmethod
    def compared(cls, tmp_path_factory):
        base = tmp_path_factory.mktemp("units")
        a, k = base / "a", base / "k"
        write_analysis_simulation(str(a), years=YEARS, daily=True, trend=TREND_A)
        write_analysis_simulation(str(k), years=YEARS, daily=True, trend=TREND_A)
        for path in glob.glob(str(k / "*" / "*.nc")):
            with xr.open_dataset(path) as ds:
                ds = ds.load()
            if "thetao" in ds:
                ds["thetao"] = (ds["thetao"] + 273.15).assign_attrs(ds["thetao"].attrs, units="K")
            for name in ("P1_Chl", "P2_Chl"):
                if name in ds:
                    ds[name] = (ds[name] * 1000).assign_attrs(ds[name].attrs, units="ug Chl m-3")
            ds.to_netcdf(path)
        options = dict(cls.OPTIONS)
        if transects_available():
            options["transects"] = {"Temperature": [cls.LINE]}
        arguments = given(
            (str(a), str(k)),
            out_dir=str(base / "out"),
            simulations=[
                {"name": "Base run", "sim_dir": str(a), "n_dirs_down": 1},
                {"name": "Kelvin run", "sim_dir": str(k), "n_dirs_down": 1},
            ],
            **options,
        )
        return base / "out", compare_quietly(arguments)

    def test_without_them_the_units_are_refused(self, compared, tmp_path):
        out, info = compared
        sims = [sim["sim_dir"] for sim in info["simulations"]]
        options = dict(self.OPTIONS, unit_conversions=None)
        with pytest.raises(ValueError, match="Temperature is not in the same units"):
            oceanval.compare_simulations(**given(sims, out_dir=str(tmp_path), simulations=[
                {"name": "Base run", "sim_dir": sims[0], "n_dirs_down": 1},
                {"name": "Kelvin run", "sim_dir": sims[1], "n_dirs_down": 1},
            ], **options))

    def test_every_netcdf_result_is_the_same(self, compared):
        out, _ = compared
        names = sorted(name for name in os.listdir(os.path.join(results(out), "base_run")) if name.endswith(".nc"))
        assert {"temperature_surface_monthly.nc", "temperature_mean_annual.nc", "temperature_bottom_annual.nc",
                "chlorophyll_phenology.nc", "temperature_surface_change1.nc"} <= set(names)
        for name in names:
            with xr.open_dataset(os.path.join(results(out), "base_run", name)) as a, xr.open_dataset(
                os.path.join(results(out), "kelvin_run", name)
            ) as k:
                for variable in a.data_vars:
                    assert k[variable].values == pytest.approx(a[variable].values, abs=1e-3, nan_ok=True), (name, variable)
                    assert k[variable].attrs.get("units") == a[variable].attrs.get("units"), (name, variable)

    def test_every_series_is_the_same(self, compared):
        out, _ = compared
        names = sorted(name for name in os.listdir(os.path.join(results(out), "base_run")) if name.endswith(".csv"))
        assert {"temperature_surface_long_term.csv", "temperature_integral_long_term.csv",
                "temperature_surface_seasonal.csv"} <= set(names)
        if transects_available():
            assert {"temperature_transect_1_climatology.csv", "temperature_transect_1_vertical.csv",
                    "temperature_transect_1_trends.csv"} <= set(names)
        for name in names:
            a = pd.read_csv(os.path.join(results(out), "base_run", name))
            k = pd.read_csv(os.path.join(results(out), "kelvin_run", name))
            pd.testing.assert_frame_equal(a, k, check_exact=False, atol=1e-2)

    def test_the_units_are_the_first_simulations(self, compared):
        _, info = compared
        temperature, chlorophyll = info["variables"]
        assert temperature["sims"]["Kelvin run"]["units"] == temperature["sims"]["Base run"]["units"] == "degC"
        assert chlorophyll["sims"]["Kelvin run"]["units"] == "mg Chl m-3"

    def test_the_info_says_what_was_converted(self, compared):
        _, info = compared
        temperature, chlorophyll = info["variables"]
        assert temperature["conversions"] == {
            "Kelvin run": {"from": "K", "multiplier": 1.0, "adder": -273.15, "text": "from K into degC (273.15 taken away)"}
        }
        assert chlorophyll["conversions"]["Kelvin run"]["text"] == "from ug Chl m-3 into mg Chl m-3 (multiplied by 0.001)"
        assert info["unit_conversions"] == {
            "Temperature": {"Kelvin run": {"multiplier": 1.0, "adder": -273.15}},
            "Chlorophyll": {"Kelvin run": {"multiplier": 0.001, "adder": 0.0}},
        }

    def test_the_report_says_so(self, compared):
        from oceanval import sim_report

        _, info = compared
        said = sim_report.conversions_text(info["variables"][0], "Base run")
        assert said == " It was converted into the units of Base run as it was read: Kelvin run, from K into degC (273.15 taken away)."
        assert sim_report.conversions_text({"conversions": {}}, "Base run") == ""
        units = next(text for text in sim_report.methods(info) if text.startswith("**Units.**"))
        assert "Temperature in Kelvin run, from K into degC" in units and "Chlorophyll in Kelvin run" in units


class TestSameGridIsLookedAtLightly:
    """A model file can be many GB: only its longitudes and latitudes are read to tell the grids apart."""

    @staticmethod
    def read_names(monkeypatch):
        read = []
        original = xr.DataArray.values.fget

        def values(self):
            read.append(self.name)
            return original(self)

        monkeypatch.setattr(xr.DataArray, "values", property(values))
        original_load = xr.Dataset.load

        def load(self, **kwargs):
            # the small file CDO writes of one level and time step is loaded; the model's own file never is
            if str(self.encoding.get("source", "")).endswith("_grid_T.nc"):
                pytest.fail("a whole file was loaded")
            return original_load(self, **kwargs)

        monkeypatch.setattr(xr.Dataset, "load", load)
        return read

    def rows(self, sims):
        from oceanval import analysis

        out = {}
        for name, folder in zip(("Base run", "Warm run"), sims):
            files = sorted(glob.glob(os.path.join(folder, "**", "*_grid_T.nc"), recursive=True))
            out[name] = [{"files": files}]
        return out, [{"name": name} for name in out]

    def test_only_the_coordinates_are_read(self, sims, monkeypatch):
        rows, names = self.rows(sims)
        read = self.read_names(monkeypatch)
        assert sim_compare._same_grid(rows, names) is False
        assert read and all(name in ("nav_lon", "nav_lat", "lon", "lat", "longitude", "latitude") for name in read), read

    def test_the_same_grid_is_the_same(self, sims, monkeypatch):
        rows, names = self.rows((sims[0], sims[0]))
        self.read_names(monkeypatch)
        assert sim_compare._same_grid(rows, names) is True


class TestCores:
    """The cores the user gives are the cores nctoolkit uses, and are put back afterwards."""

    def test_nctoolkit_uses_the_cores_given(self, sims, no_report, monkeypatch, tmp_path):
        import nctoolkit as nc

        class Stop(BaseException):
            """Ends the run once the cores are seen: with several cores nctoolkit runs NCO in pools it
            never closes, which hang when they are collected (nctoolkit 1.3.6 and before), so the
            analysis itself is not run."""

        seen = []
        for name in ("_work_out", "make_common_grid"):
            original = getattr(sim_compare, name)

            def wrapped(*args, _original=original, _name=name, **kwargs):
                seen.append(nc.session_info["cores"])
                if _name == "_work_out":
                    raise Stop
                return _original(*args, **kwargs)

            monkeypatch.setattr(sim_compare, name, wrapped)
        before = nc.session_info["cores"]
        arguments = given(sims, cores=3, out_dir=str(tmp_path), unify={"baseline": "Base run"})
        with pytest.raises(Stop):
            compare_quietly(arguments)
        assert len(seen) == 2 and set(seen) == {3}
        assert nc.session_info["cores"] == before

    def test_the_cores_are_put_back_when_it_fails(self, sims, monkeypatch, tmp_path):
        import nctoolkit as nc

        before = nc.session_info["cores"]
        monkeypatch.setattr(sim_compare, "_work_out", lambda *args, **kwargs: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            compare_quietly(given(sims, cores=2 if before != 2 else 3, out_dir=str(tmp_path)))
        assert nc.session_info["cores"] == before


def compare_quietly(arguments):
    """compare_simulations(**arguments) with no report built: its info."""
    return quietly(oceanval.compare_simulations, **arguments)


class TestTheSimulationsOfAComparison:
    """analyze() of one of the simulations compared below,
    with the comparison's options (the analysed_* fixtures of conftest.py,
    run once a session): what it gives, which the comparison must give that
    simulation too. thetao is its level's value plus the month, plus the
    simulation's trend for each year after 2012."""

    def test_the_monthly_climatology_and_long_term_series_of_b(self, analysed_monthly_b):
        found, info = analysed_monthly_b
        assert [v["key"] for v in info["variables"]] == ["temperature"]
        annual = values(os.path.join(found, "temperature_surface_annual.nc"), "temperature")
        # 10 + the mean of the months (6.5) + the trend over 2012-2014 (mean of 0, 1, 2 years)
        assert annual.shape[-1] == len(B_LATS) * len(B_LONS)
        assert np.allclose(annual, 10 + 6.5 + TREND_B, atol=1e-4)
        frame = pd.read_csv(os.path.join(found, "temperature_surface_long_term.csv"))
        domain = frame[frame.name == "Full Domain"].groupby("year").value.mean()
        assert domain.loc[2012] == pytest.approx(10 + 6.5, abs=1e-4)
        assert domain.loc[2014] == pytest.approx(10 + 6.5 + 2 * TREND_B, abs=1e-4)

    def test_every_option_of_a(self, analysed_daily_a):
        found, info = analysed_daily_a
        assert [v["key"] for v in info["variables"]] == ["temperature", "chlorophyll"]
        assert {
            "temperature_surface_monthly.nc",
            "chlorophyll_surface_daily.nc",
            "temperature_surface_change1.nc",
            "temperature_surface_seasonal.csv",
        } <= set(os.listdir(found))

        def annual(layer):
            return values(os.path.join(found, f"temperature_{layer}_annual.nc"), "temperature")

        # each month's days are its number, and the mean year is a year after the first
        assert np.allclose(annual("surface"), ANALYSIS_LEVEL_VALUES[0] + 6.5 + TREND_A, atol=1e-4)
        assert np.allclose(annual("bottom"), ANALYSIS_LEVEL_VALUES[-1] + 6.5 + TREND_A, atol=1e-4)
        column = np.average(ANALYSIS_LEVEL_VALUES, weights=ANALYSIS_THICKNESS)
        assert np.allclose(annual("mean"), column + 6.5 + TREND_A, atol=1e-4)
        change = values(os.path.join(found, "temperature_surface_change1.nc"), "temperature")
        assert np.allclose(change, 2 * TREND_A, atol=1e-4)
        with xr.open_dataset(os.path.join(found, "chlorophyll_phenology.nc")) as ds:
            days = ds[analysis.PEAK_DAY].values
        # a day out in places for the leap year
        assert days[:, 0] == pytest.approx([analysis_peak_day(row) for row in range(days.shape[0])], abs=1.5)
        stations = pd.read_csv(os.path.join(found, "temperature_surface_long_term.csv"))
        assert "Station" in set(stations.name)

    def test_a_transect_of_every_kind_of_a(self, analysed_daily_a):
        if not transects_available():
            pytest.skip("needs nctoolkit 1.3.6 or later")
        found, info = analysed_daily_a
        options = info["variables"][0]["transects"]["lines"][0]["options"]
        assert set(options) == {"climatology", "vertical", "trends"}
        frames = {name: pd.read_csv(os.path.join(found, option["csv"])) for name, option in options.items()}
        for month, value in zip(frames["climatology"].month, frames["climatology"].value):
            assert value == pytest.approx(ANALYSIS_LEVEL_VALUES[0] + month + TREND_A, abs=1e-4)
        for _, part in frames["trends"].groupby("position"):
            assert part.sort_values("year").value.diff().dropna().to_numpy() == pytest.approx(TREND_A)


@pytest.fixture(scope="module")
def out(sims, tmp_path_factory):
    out = tmp_path_factory.mktemp("own")
    return out, compare_quietly(given(sims, long_term=COMPARE_LONG_TERM, out_dir=str(out)))


class TestEachSimulationOnItsOwn:

    def test_warm_run_is_what_analyze_gives(self, out, analysed_monthly_b):
        """With nothing unified, masked or converted, a simulation's results are
        those of analyze() of it alone (tests/test_analysis.py's)."""
        folder, _ = out
        same_as_analyze(os.path.join(results(folder), "warm_run"), analysed_monthly_b[0])

    def test_there_is_a_folder_for_each(self, out):
        folder, _ = out
        assert sorted(os.listdir(results(folder))) == ["base_run", "comparison.json", "warm_run"]
        for key in ("base_run", "warm_run"):
            assert "temperature_surface_monthly.nc" in os.listdir(os.path.join(results(folder), key))

    def test_each_has_its_own_answers_on_its_own_grid(self, out):
        """Warm run's values are analyze()'s (test_warm_run_is_what_analyze_gives), which
        TestTheSimulationsOfAComparison checks: here Base run's, and that each keeps its grid."""
        folder, _ = out
        a = field(folder, "base_run", "temperature_surface_annual.nc")
        b = field(folder, "warm_run", "temperature_surface_annual.nc")
        # 10 + the mean of the months (6.5) + the trend over 2012-2014 (mean of 0, 1, 2 years)
        assert float(a.mean()) == pytest.approx(10 + 6.5 + TREND_A * 1, abs=1e-4)
        assert a.shape[-2:] == (4, 5)
        assert b.shape[-2:] == (len(B_LATS), len(B_LONS))

    def test_the_series_are_per_simulation(self, out):
        folder, _ = out
        frame = pd.read_csv(os.path.join(results(folder), "base_run", "temperature_surface_long_term.csv"))
        annual = frame[frame.name == "Full Domain"].groupby("year").value.mean()
        assert annual.loc[2012] == pytest.approx(10 + 6.5, abs=1e-4)
        assert annual.loc[2014] == pytest.approx(10 + 6.5 + 2 * TREND_A, abs=1e-4)

    def test_the_info_says_what_was_compared(self, out):
        folder, built = out
        saved = sim_compare.load(results(folder))
        assert saved == json.loads(json.dumps(built))
        assert [sim["name"] for sim in saved["simulations"]] == ["Base run", "Warm run"]
        assert len(set(saved["colours"].values())) == 2
        assert saved["unify"] is None
        assert saved["same_grid"] is False
        (variable,) = saved["variables"]
        assert variable["units"] == "degC"
        assert variable["kinds"] == ["climatology", "long_term"]
        assert variable["model_variables"] == {"Base run": "thetao", "Warm run": "thetao"}
        assert set(variable["sims"]) == {"Base run", "Warm run"}
        assert saved["unify_text"].startswith("The simulations were not put on a common grid")

    def test_a_comparison_is_replaced_whole(self, sims, tmp_path, no_report):
        oceanval.compare_simulations(**given(sims, out_dir=str(tmp_path)))
        stale = os.path.join(results(tmp_path), "stale.txt")
        open(stale, "w").close()
        oceanval.compare_simulations(**given(sims, out_dir=str(tmp_path)))
        assert not os.path.exists(stale)

    def test_the_window_is_told_of_each_analysis_in_each_simulation_and_the_directories_are_remembered(
        self, sims, tmp_path, no_report
    ):
        from oceanval import prompts, user_cache

        told = []
        with prompts.told_by(told.append):
            oceanval.compare_simulations(**given(sims, out_dir=str(tmp_path)))
        assert set(sims) <= set(user_cache.recent_sim_dirs())
        assert told[0] == {
            "kind": "analyses",
            "simulations": 2,
            "items": [{"key": "temperature", "title": "Temperature", "kinds": ["climatology"]}],
        }
        said = [(news["kind"], news.get("analysis"), news["state"]) for news in told[1:]]
        # the variable is started once, and each analysis is worked out in each simulation
        assert said == [
            ("analysis_variable", None, "working"),
            ("analysis", "climatology", "working"),
            ("analysis", "climatology", "done"),
            ("analysis", "climatology", "working"),
            ("analysis", "climatology", "done"),
            ("analysis_variable", None, "done"),
        ]


class TestSameThicknessNames:
    def test_each_simulation_uses_its_own_thickness(self, tmp_path, no_report):
        """Both have a grid_T file of the same name; the cache of thicknesses must not mix them up."""
        a, b = tmp_path / "a", tmp_path / "b"
        for root in (a, b):
            write_analysis_simulation(str(root), years=(2013,), daily=False)
        for path in glob.glob(str(b / "*" / "*grid_T.nc")):
            with xr.open_dataset(path) as ds:
                ds = ds.load()
            ds["e3t"].values[:] = np.array([7.0, 2.0, 1.0])[None, :, None, None]
            ds.to_netcdf(path)
        oceanval.compare_simulations(
            simulations=[
                {"name": "A", "sim_dir": str(a), "n_dirs_down": 1},
                {"name": "B", "sim_dir": str(b), "n_dirs_down": 1},
            ],
            variables={"Temperature": "thetao"},
            climatology={"Temperature": {"start": 2013, "end": 2013, "water_column": "mean"}},
            thickness="e3t",
            out_dir=str(tmp_path / "out"),
            view=False,
            cores=1,
        )
        a_mean = field(tmp_path / "out", "a", "temperature_mean_annual.nc")
        b_mean = field(tmp_path / "out", "b", "temperature_mean_annual.nc")
        # levels are 10, 8, 4 (plus the mean month, 6.5), weighted by 1, 2, 7 in A and 7, 2, 1 in B
        assert float(a_mean.mean()) == pytest.approx((10 * 1 + 8 * 2 + 4 * 7) / 10 + 6.5, abs=1e-4)
        assert float(b_mean.mean()) == pytest.approx((10 * 7 + 8 * 2 + 4 * 1) / 10 + 6.5, abs=1e-4)


class TestUnified:
    @pytest.fixture(scope="class")
    @classmethod
    def holes(cls, tmp_path_factory):
        """The simulations of `sims`, with a land cell in each of a different place."""
        base = tmp_path_factory.mktemp("holes")
        a, b = base / "a", base / "b"
        write_analysis_simulation(str(a), years=YEARS, daily=False, trend=TREND_A)
        write_analysis_simulation(str(b), years=YEARS, daily=False, trend=TREND_B, lats=B_LATS, lons=B_LONS)

        def rewrite(root, change):
            for path in glob.glob(str(root / "*" / "*grid_T.nc")):
                with xr.open_dataset(path) as ds:
                    ds = ds.load()
                change(ds)
                ds.to_netcdf(path, encoding={"thetao": {"_FillValue": np.float32(-1e20)}})

        def a_land(ds):
            ds["thetao"][:, :, 3, 4] = np.nan

        def b_land(ds):
            j, i = B_LATS.index(51.0), B_LONS.index(-2.0)
            ds["thetao"][:, :, j - 1 : j + 2, i - 1 : i + 2] = np.nan

        rewrite(a, a_land)
        rewrite(b, b_land)
        return str(a), str(b)

    @staticmethod
    def run(holes, out, unify, **options):
        return compare_quietly(
            given(
                holes,
                unify=unify,
                long_term={"Temperature": {"start": 2012, "end": 2014, "surface": True}},
                seasonal={"Temperature": {"start": 2012, "end": 2014, "surface": True}},
                out_dir=str(out),
                **options,
            )
        )

    @pytest.fixture(scope="class")
    @classmethod
    def on_baseline(cls, holes, tmp_path_factory):
        """The comparison on Base run's grid: (its folder, its info)."""
        out = tmp_path_factory.mktemp("holes_on_baseline")
        return out, cls.run(holes, out, {"baseline": "Base run"})

    def test_a_baseline_gives_every_simulation_its_grid_and_cells(self, on_baseline):
        out, info = on_baseline
        a = field(out, "base_run", "temperature_surface_monthly.nc")
        b = field(out, "warm_run", "temperature_surface_monthly.nc")
        assert a.shape == b.shape
        assert (np.isnan(a.values) == np.isnan(b.values)).all()
        # A's own land, and B's, are missing in both; the sea is not
        assert np.isnan(a.values[:, 3, 4]).all()
        assert np.isnan(b.values[:, 1, 2]).all()
        assert np.isfinite(b.values[:, 2, 1]).all()
        assert info["same_grid"] is True
        assert info["unify"] == {"method": "baseline", "baseline": "Base run"}
        assert "missing in all" in info["unify_text"]
        shared, cells = info["shared_cells"]["temperature"]["surface"]
        assert cells == 4 * 5 and 0 < shared < cells

    def test_a_box_gives_every_simulation_the_box(self, holes, tmp_path):
        box = {"lon": [-4, 0], "lat": [50, 53], "res": 1}
        self.run(holes, tmp_path, box)
        for key in ("base_run", "warm_run"):
            data = field(tmp_path, key, "temperature_surface_monthly.nc")
            assert list(data.lon.values) == [-4, -3, -2, -1, 0]
            assert list(data.lat.values) == [50, 51, 52, 53]
        a = field(tmp_path, "base_run", "temperature_surface_monthly.nc")
        b = field(tmp_path, "warm_run", "temperature_surface_monthly.nc")
        assert (np.isnan(a.values) == np.isnan(b.values)).all()

    def test_the_series_are_of_the_same_cells(self, on_baseline):
        """The means are over the same cells, so the same trend in A and B differs only by what they rise by."""
        out, _ = on_baseline
        means = {}
        for key in ("base_run", "warm_run"):
            frame = pd.read_csv(os.path.join(results(out), key, "temperature_surface_long_term.csv"))
            means[key] = frame[frame.name == "Full Domain"].groupby("year").value.mean()
        # the same cells, and the temperature is the same in every cell but for the trend
        assert means["warm_run"].loc[2012] == pytest.approx(means["base_run"].loc[2012], abs=1e-3)
        assert means["warm_run"].loc[2014] - means["base_run"].loc[2014] == pytest.approx(2 * (TREND_B - TREND_A), abs=1e-3)

    def test_the_way_round_does_not_matter_for_the_cells(self, holes, tmp_path):
        self.run(holes, tmp_path, {"baseline": "Warm run"})
        a = field(tmp_path, "base_run", "temperature_surface_monthly.nc")
        b = field(tmp_path, "warm_run", "temperature_surface_monthly.nc")
        assert a.shape == b.shape == (12, len(B_LATS), len(B_LONS))
        assert (np.isnan(a.values) == np.isnan(b.values)).all()


class TestTheMaskIsFromTheSecondTimeStep:
    """A cell missing in any simulation is missing in all, judged from the second time step: the first can be empty
    (a spin-up step of zeros, which as_missing makes missing), which would make every cell missing."""

    @staticmethod
    def emptied(base, name, everything=False, value=0.0):
        """A copy of the simulation of `sims` in which the first time of its first file (or every time) is value."""
        root = base / name
        write_analysis_simulation(str(root), years=YEARS, daily=False, trend=TREND_A)
        files = sorted(glob.glob(str(root / "*" / "*grid_T.nc")))
        for path in files if everything else files[:1]:
            with xr.open_dataset(path) as ds:
                ds = ds.load()
            if everything:
                ds["thetao"][:] = value
            else:
                ds["thetao"][0] = value
            ds.to_netcdf(path, encoding={"thetao": {"_FillValue": np.float32(-1e20)}})
        return str(root)

    @staticmethod
    def run(directories, out, **options):
        return compare_quietly(
            given(
                directories,
                unify={"baseline": "Base run"},
                as_missing=[0, 0],
                out_dir=str(out),
                **options,
            )
        )

    @pytest.fixture(scope="class")
    @classmethod
    def empty_first(cls, tmp_path_factory):
        """B's first time step is empty: (the comparison's folder, its info, the CDO commands it ran)."""
        import nctoolkit as nc

        base = tmp_path_factory.mktemp("empty_first")
        a = base / "a"
        write_analysis_simulation(str(a), years=YEARS, daily=False, trend=TREND_A)
        directories = (str(a), cls.emptied(base, "b"))
        from oceanval import surface

        seen, checking = [], []
        original = nc.DataSet.cdo_command

        def recorded(self, command, *a, **k):
            if not checking:
                seen.append(command)
            return original(self, command, *a, **k)

        # whether the surface is the first level is told from a file's first time step, once for each file
        # (surface.first_level_is_surface keeps the answer), which is not the mask's
        check = surface._first_level_is_surface

        def surface_check(*a, **k):
            checking.append(True)
            try:
                return check(*a, **k)
            finally:
                checking.pop()

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(nc.DataSet, "cdo_command", recorded)
            patch.setattr(surface, "_first_level_is_surface", surface_check)
            info = cls.run(directories, base / "out")
        return base / "out", info, seen

    def test_an_empty_first_time_step_does_not_make_everything_missing(self, empty_first):
        out, info, _ = empty_first
        shared, cells = info["shared_cells"]["temperature"]["surface"]
        assert shared == cells > 0
        for key in ("base_run", "warm_run"):
            data = field(out, key, "temperature_surface_monthly.nc")
            assert np.isfinite(data.values).all()

    def test_the_masks_time_step_is_asked_of_cdo(self, empty_first):
        _, _, seen = empty_first
        # the mask is made first, of one time step, the second; the analyses' own reads choose no time step
        steps = [command for command in seen if "timestep=" in command]
        assert steps and all(command.endswith("timestep=2") for command in steps), steps

    def test_nothing_shared_is_said(self, tmp_path):
        a = self.emptied(tmp_path, "a", everything=True)
        b = self.emptied(tmp_path, "b")
        with pytest.raises(ValueError, match=r"No cell has a value in every simulation for temperature \(surface\).*Base run: 0 of"):
            self.run((a, b), tmp_path / "out")

    @pytest.mark.parametrize(
        "files, steps, expected",
        [
            (["a", "b"], 12, ("a", 1)),
            (["a", "b"], 2, ("a", 1)),
            (["a", "b"], 1, ("b", 0)),
            (["a"], 1, ("a", 0)),
        ],
    )
    def test_the_second_step_is_in_the_first_file_or_is_the_second_files_first(self, files, steps, expected):
        from oceanval import analysis

        assert analysis.second_step(files, steps) == expected

    def test_the_run_finds_it_from_the_times_it_has_read(self):
        from oceanval import analysis

        row = {"files": ["a", "b"], "times": {"a": [1], "b": [2, 3]}}
        assert analysis.second_step_of(row) == ("b", 0)
        row["times"]["a"] = [1, 2]
        assert analysis.second_step_of(row) == ("a", 1)
        # not known: the first
        assert analysis.second_step_of({"files": ["a", "b"], "times": {}}) == ("a", 0)


class TestEveryOption:
    """Everything analyze() does, with a transect of every kind, for two daily simulations on different grids:
    as they are (Base run's results are then analyze()'s, analysed_daily_a), and on Base run's grid."""

    @pytest.fixture(scope="class")
    @classmethod
    def as_they_are(cls, compared_daily, tmp_path_factory):
        """The comparison with every option and nothing unified: (its folder, its info)."""
        out = tmp_path_factory.mktemp("every_option")
        return out, compare_quietly(given(compared_daily, out_dir=str(out), **every_option()))

    @pytest.fixture(scope="class")
    @classmethod
    def on_baseline(cls, compared_daily, tmp_path_factory):
        """The comparison with every option on Base run's grid: (its folder, its info). A box goes the same way
        once its grid is made (TestUnified.test_a_box_gives_every_simulation_the_box)."""
        out = tmp_path_factory.mktemp("every_option_on_baseline")
        return out, compare_quietly(given(compared_daily, unify={"baseline": "Base run"}, out_dir=str(out), **every_option()))

    def test_base_run_is_what_analyze_gives(self, as_they_are, analysed_daily_a):
        """With nothing unified, masked or converted, a simulation's results are
        those of analyze() of it alone (TestTheSimulationsOfAComparison checks them)."""
        out, _ = as_they_are
        same_as_analyze(os.path.join(results(out), "base_run"), analysed_daily_a[0])

    @pytest.mark.parametrize("compared", ["as_they_are", "on_baseline"])
    def test_each_simulation_has_each_result(self, compared, request):
        out, info = request.getfixturevalue(compared)
        assert [v["key"] for v in info["variables"]] == ["temperature", "chlorophyll"]
        # Base run's, as they are, are analyze()'s (test_base_run_is_what_analyze_gives)
        for key in ("warm_run",) if compared == "as_they_are" else ("base_run", "warm_run"):
            files = set(os.listdir(os.path.join(results(out), key)))
            assert {
                "temperature_surface_monthly.nc",
                "temperature_bottom_annual.nc",
                "temperature_mean_annual.nc",
                "chlorophyll_phenology.nc",
                "temperature_surface_historical.nc",
                "temperature_surface_future1.nc",
                "temperature_surface_change1.nc",
                "temperature_surface_long_term.csv",
                "temperature_surface_seasonal.csv",
            } <= files
            if transects_available():
                assert any("transect_1_vertical" in name for name in files)
                assert any("transect_1_trends" in name for name in files)

    def test_the_phenology_is_of_the_same_cells(self, on_baseline):
        out, _ = on_baseline
        with xr.open_dataset(os.path.join(results(out), "base_run", "chlorophyll_phenology.nc")) as a, xr.open_dataset(
            os.path.join(results(out), "warm_run", "chlorophyll_phenology.nc")
        ) as b:
            assert a["peak_day"].shape == b["peak_day"].shape
            assert (np.isnan(a["peak_day"].values) == np.isnan(b["peak_day"].values)).all()
            # A peaks about 60 + 30 for each row north (a day out for the leap year); B has its own rows
            assert a["peak_day"].values[:, 0] == pytest.approx([60, 90, 120, 150], abs=1.5)

    def test_transects_of_both_on_the_common_grid(self, as_they_are, on_baseline):
        if not transects_available():
            pytest.skip("needs nctoolkit 1.3.6 or later")

        def steps(info):
            return {sim: own["transects"]["lines"][0]["steps"] for sim, own in info["variables"][0]["sims"].items()}

        # each is sampled as its own grid has it
        alone = steps(as_they_are[1])
        assert alone["Base run"] != alone["Warm run"]
        # on one grid, every simulation has the same points
        assert len(set(steps(on_baseline[1]).values())) == 1


class TestDomainMask:
    """compare_simulations(domain_mask=): every simulation is put on the mask's grid and masked by it, as matchup's is."""

    @staticmethod
    def write_mask(path, west=-2.0, name="wanted"):
        """A mask on the usual grid: 1 from longitude west eastwards, 0 to the west of it."""
        from simulations import ANALYSIS_LATS, ANALYSIS_LONS

        lon, lat = np.array(ANALYSIS_LONS), np.array(ANALYSIS_LATS)
        values = np.where(lon[None, :] >= west, 1.0, 0.0) * np.ones((lat.size, 1))
        coords = {
            "lat": ("lat", lat, {"units": "degrees_north", "standard_name": "latitude"}),
            "lon": ("lon", lon, {"units": "degrees_east", "standard_name": "longitude"}),
        }
        xr.DataArray(values.astype("f4"), coords=coords, dims=("lat", "lon"), name=name).to_dataset().to_netcdf(path)
        return str(path)

    @pytest.mark.parametrize("unify", [None, {"baseline": "Warm run"}])
    def test_every_simulation_is_on_the_masks_grid_and_masked(self, sims, tmp_path, unify):
        from simulations import ANALYSIS_LATS, ANALYSIS_LONS

        mask = self.write_mask(tmp_path / "mask.nc")
        info = compare_quietly(given(sims, domain_mask=mask, unify=unify, out_dir=str(tmp_path)))
        wanted = np.array(ANALYSIS_LONS) >= -2.0
        for key in ("base_run", "warm_run"):
            values = field(tmp_path, key, "temperature_surface_annual.nc").values
            # B is on a finer grid, but ends on A's
            assert values.shape[-2:] == (len(ANALYSIS_LATS), len(ANALYSIS_LONS))
            assert (np.isfinite(values[0]) == wanted[None, :]).all()
        assert os.path.exists(os.path.join(results(tmp_path), "domain_mask.nc"))
        assert info["domain_mask"] == "domain_mask.nc"
        assert "nearest neighbour" in info["domain_mask_text"]
        assert info["same_grid"] is True

    def test_without_one_nothing_is_masked(self, out):
        folder, info = out
        assert info["domain_mask"] is None and info["domain_mask_text"] is None
        assert not os.path.exists(os.path.join(results(folder), "domain_mask.nc"))
        assert np.isfinite(field(folder, "base_run", "temperature_surface_annual.nc").values).all()

    def test_a_file_that_is_not_a_mask_is_refused_before_any_work(self, sims, tmp_path):
        with pytest.raises(ValueError, match="domain_mask"):
            compare_quietly(given(sims, domain_mask=str(tmp_path / "missing.nc"), out_dir=str(tmp_path)))
        assert not os.path.exists(os.path.join(str(tmp_path), sim_compare.FOLDER))

    def test_the_analysis_of_one_simulation_is_not_masked(self, sims):
        from oceanval import analysis

        files = glob.glob(os.path.join(sims[0], "*", "*grid_T.nc"))
        row = {"key": "temp", "name": "Temperature", "parts": ["thetao"]}
        ds = analysis.monthly_means_of(files, row, "surface", YEARS[:1] * 2, None, 3)
        ds.run()
        assert np.isfinite(ds.to_xarray()["temp"].values).all()

    WEST = -3.0

    @pytest.fixture(scope="class")
    @classmethod
    def every_analysis(cls, compared_daily, tmp_path_factory):
        """The daily simulations compared with every option and a transect, masked west of WEST: (its folder, its info)."""
        out = tmp_path_factory.mktemp("masked_every_option")
        mask = cls.write_mask(out / "mask.nc", west=cls.WEST)
        return out, compare_quietly(given(compared_daily, domain_mask=mask, out_dir=str(out), **every_option()))

    def test_transects_are_taken_on_the_masks_grid(self, every_analysis):
        if not transects_available():
            pytest.skip("needs nctoolkit 1.3.6 or later")
        out, info = every_analysis
        steps = {sim: own["transects"]["lines"][0]["steps"] for sim, own in info["variables"][0]["sims"].items()}
        # both are sampled as the mask's grid has it
        assert len(set(steps.values())) == 1
        for key in ("base_run", "warm_run"):
            assert any("transect_1_vertical" in name for name in os.listdir(os.path.join(results(out), key)))

    def test_every_analysis_is_of_the_masked_cells(self, every_analysis):
        out, info = every_analysis
        wanted = np.array([-4.0, -3.0, -2.0, -1.0, 0.0]) >= self.WEST
        for key in ("base_run", "warm_run"):
            folder = os.path.join(results(out), key)
            with xr.open_dataset(os.path.join(folder, "chlorophyll_phenology.nc")) as ds:
                assert (np.isfinite(ds["peak_day"].values) == wanted[None, :]).all()
            for name in ("temperature_surface_annual.nc", "temperature_bottom_annual.nc", "temperature_mean_annual.nc"):
                with xr.open_dataset(os.path.join(folder, name)) as ds:
                    assert (np.isfinite(ds["temperature"].values[0]) == wanted[None, :]).all()
        assert set(info["variables"][0]["kinds"]) >= {"climatology", "long_term", "seasonal", "map_changes"}


class TestFVCOM:
    """Raw FVCOM output (oceanval.fvcom), compared on its own mesh: two
    simulations on one mesh, whose results must be what analyze() gives each alone
    (Base run's are checked; Warm run's are worked out by the same code)."""

    given_fvcom = staticmethod(fvcom_arguments)

    @pytest.fixture(scope="class")
    @classmethod
    def compared(cls, fvcom_comparison):
        """The comparison with every option (conftest.fvcom_comparison, which test_sim_plots.py draws too): (its folder, its info)."""
        return fvcom_comparison

    def test_base_run_is_what_analyze_gives(self, compared, analysed_fvcom_a):
        out, _ = compared
        same_as_analyze(os.path.join(results(out), "base_run"), analysed_fvcom_a[0])

    def test_the_comparison_says_it_is_fvcom_on_one_mesh(self, compared):
        out, info = compared
        assert info["fvcom"] is True and info["same_grid"] is True and info["unify"] is None
        assert "FVCOM" in info["unify_text"] and "mesh" in info["unify_text"]
        saved = sim_compare.load(results(out))
        assert saved["fvcom"] is True
        for key in ("base_run", "warm_run"):
            with open(os.path.join(results(out), key, "analysis.json")) as file:
                assert json.load(file)["fvcom"] is True

    def test_b_rises_faster_than_a_on_the_same_cells(self, compared):
        out, _ = compared
        with xr.open_dataset(os.path.join(results(out), "base_run", "temperature_surface_annual.nc")) as a, xr.open_dataset(
            os.path.join(results(out), "warm_run", "temperature_surface_annual.nc")
        ) as b:
            assert a["temperature"].shape == b["temperature"].shape
            # only January and February have a second year, a trend higher, of the four months of the climatology
            # (the mean of their two years is half of it)
            assert np.allclose(b["temperature"].values - a["temperature"].values, (TREND_B - TREND_A) / 2 * 2 / 4, atol=1e-4)

    def test_no_thickness_is_asked_for(self, compared):
        _, info = compared
        assert info["variables"][0]["sims"]["Base run"]["climatology"]["layers"]["integral"]["mesh"]

    def test_the_simulations_must_share_a_mesh(self, tmp_path):
        import netCDF4

        degrees = fvcom_months(str(tmp_path / "degrees"), months=((2012, 1),))
        moved = fvcom_months(str(tmp_path / "moved"), months=((2012, 1),))
        # the same triangles, a little to the east
        for root, _, names in os.walk(moved):
            for name in names:
                with netCDF4.Dataset(os.path.join(root, name), "a") as ds:
                    ds.variables["lon"][:] = ds.variables["lon"][:] + 0.01
        with pytest.raises(ValueError, match="is not that of Base run"):
            oceanval.compare_simulations(
                simulations=[
                    {"name": "Base run", "sim_dir": degrees, "n_dirs_down": 1},
                    {"name": "Warm run", "sim_dir": moved, "n_dirs_down": 1},
                ],
                variables={"Temperature": "temp"},
                climatology={"Temperature": {"start": 2012, "end": 2012, "surface": True}},
                cores=1, view=False, out_dir=str(tmp_path / "out"),
            )

    @pytest.mark.parametrize(
        "extra, message",
        [
            ({"unify": {"baseline": "Base run"}}, "can only be put on a lon-lat grid"),
            ({"unit_conversions": {"Temperature": {"Warm run": {"multiplier": 1, "adder": 0}}}}, "unit_conversions do not work with FVCOM output"),
        ],
    )
    def test_what_cannot_be_done_to_a_mesh_is_refused(self, compared_fvcom, tmp_path, extra, message):
        arguments = self.given_fvcom(compared_fvcom, out_dir=str(tmp_path), **extra)
        with pytest.raises(ValueError, match=message):
            oceanval.compare_simulations(**arguments)

    BOX = {"lon": [-6, -3], "lat": [49, 52], "res": 0.1}

    @pytest.fixture(scope="class")
    @classmethod
    def on_a_box(cls, compared_fvcom, tmp_path_factory):
        """The two simulations put on a lon-lat box: (its folder, its info)."""
        out = tmp_path_factory.mktemp("compare_fvcom_box")
        a, b = compared_fvcom
        arguments = dict(
            simulations=[
                {"name": "Base run", "sim_dir": a, "n_dirs_down": 1},
                {"name": "Warm run", "sim_dir": b, "n_dirs_down": 1},
            ],
            variables={"Temperature": {"Base run": "temp", "Warm run": "temp"}},
            climatology={"Temperature": {"start": 2012, "end": 2013, "surface": True, "bottom": True, "water_column": "mean"}},
            long_term={"Temperature": {"start": 2012, "end": 2013, "surface": True}},
            unify=cls.BOX,
            view=False,
            cores=1,
            out_dir=str(out),
        )
        return out, compare_quietly(arguments)

    def test_a_box_regrids_the_mesh_to_a_lon_lat_grid(self, on_a_box):
        out, info = on_a_box
        assert info["fvcom"] is False and info["fvcom_regridded"] == ["Base run", "Warm run"]
        assert info["same_grid"] is True and info["unify"]["method"] == "box"
        assert "mesh" in info["unify_text"] and "regridded" in info["unify_text"] and "lon-lat" in info["unify_text"]
        saved = sim_compare.load(results(out))
        assert {sim["name"]: sim["thickness"] for sim in saved["simulations"]} == {"Base run": "z_level", "Warm run": "z_level"}
        for key in ("base_run", "warm_run"):
            with open(os.path.join(results(out), key, "analysis.json")) as file:
                assert json.load(file)["fvcom"] is False

    def test_the_box_results_are_on_the_boxs_grid_in_the_same_cells(self, on_a_box):
        out, _ = on_a_box
        a = field(out, "base_run", "temperature_surface_annual.nc")
        b = field(out, "warm_run", "temperature_surface_annual.nc")
        assert a.shape == b.shape
        shown = np.isfinite(a.values)
        assert shown.any() and (shown == np.isfinite(b.values)).all()
        # only the cells inside the mesh, 5-4 W, have values, and the second simulation is warmer
        lons = np.broadcast_to(a["lon"].values[None, None, :], a.shape)[shown]
        assert lons.min() >= -5.15 and lons.max() <= -3.85
        assert (b.values[shown] > a.values[shown]).all()

    def test_the_box_has_the_bottom_and_the_water_column(self, on_a_box):
        out, _ = on_a_box
        surface = field(out, "base_run", "temperature_surface_annual.nc")
        assert np.isfinite(field(out, "base_run", "temperature_bottom_annual.nc").values).any()
        assert np.isfinite(field(out, "base_run", "temperature_mean_annual.nc").values).any()
        assert np.isfinite(surface.values).any()

    def test_a_domain_mask_is_refused_with_a_box_too(self, compared_fvcom, tmp_path):
        mask = str(tmp_path / "mask.nc")
        TestDomainMask.write_mask(mask)
        arguments = self.given_fvcom(compared_fvcom, out_dir=str(tmp_path / "out"), domain_mask=mask, unify=self.BOX)
        with pytest.raises(ValueError, match="domain_mask does not work with FVCOM output"):
            oceanval.compare_simulations(**arguments)

    def test_a_domain_mask_is_refused(self, compared_fvcom, tmp_path):
        mask = str(tmp_path / "mask.nc")
        TestDomainMask.write_mask(mask)
        arguments = self.given_fvcom(compared_fvcom, out_dir=str(tmp_path / "out"), domain_mask=mask)
        with pytest.raises(ValueError, match="domain_mask does not work with FVCOM output"):
            oceanval.compare_simulations(**arguments)


class TestFVCOMWithOtherOutput:
    """Raw FVCOM output compared with output on a grid (oceanval.sim_fvcom): regridded onto the
    grid's, cropped to the mesh, and then ordinary output on a grid."""

    @staticmethod
    def given_mixed(mixed, **changes):
        fvcom_dir, fvcom_down, grid_dir, grid_down = mixed
        arguments = dict(
            simulations=[
                {"name": "Mesh", "sim_dir": fvcom_dir, "n_dirs_down": fvcom_down},
                {"name": "Grid", "sim_dir": grid_dir, "n_dirs_down": grid_down},
            ],
            variables={"Temperature": {"Mesh": "temp", "Grid": "thetao"}},
            climatology={"Temperature": {"start": 2012, "end": 2013, "surface": True, "bottom": True, "water_column": "mean"}},
            long_term={"Temperature": {"start": 2012, "end": 2013, "surface": True}},
            thickness="e3t",
            unit_conversions={"Temperature": {"Grid": {"multiplier": 1, "adder": 0}}},
            view=False,
            cores=1,
        )
        arguments.update(changes)
        return arguments

    @pytest.fixture(scope="class")
    @classmethod
    def compared(cls, mixed_simulations, tmp_path_factory):
        out = tmp_path_factory.mktemp("compare_mixed")
        return out, compare_quietly(cls.given_mixed(mixed_simulations, out_dir=str(out)))

    def test_the_comparison_is_on_the_grid_cropped_to_the_mesh(self, compared):
        out, info = compared
        assert info["fvcom"] is False and info["fvcom_regridded"] == ["Mesh"]
        assert info["unify"]["baseline"] == "Grid" and info["same_grid"] is True
        # the mesh covers 1 degree of the grid's 1.5 each way, and the crop is a degree wider than the mesh
        lon, lat = info["unify"]["crop"]
        assert lon[0] == pytest.approx(-6.0) and lon[1] == pytest.approx(-3.0)
        assert "mesh" in info["unify_text"] and "regridded" in info["unify_text"]
        saved = sim_compare.load(results(out))
        assert saved["fvcom_regridded"] == ["Mesh"]
        with open(os.path.join(results(out), "mesh", "analysis.json")) as file:
            assert json.load(file)["fvcom"] is False
        assert {sim["name"]: sim["thickness"] for sim in saved["simulations"]} == {"Mesh": "z_level", "Grid": "e3t"}

    def test_the_surface_is_the_meshs_at_the_nearest_node_in_the_cells_of_both(self, compared):
        out, _ = compared
        mesh = field(out, "mesh", "temperature_surface_annual.nc")
        grid = field(out, "grid", "temperature_surface_annual.nc")
        assert mesh.shape == grid.shape
        wanted = np.isfinite(mesh.values) & np.isfinite(grid.values)
        assert (np.isfinite(mesh.values) == np.isfinite(grid.values)).all() and wanted.any()
        # 10 + lon + the mean of the four months' mean days (15, 45, 381, 410.5) and the 2013 trend of 2 each
        expected = 10 + 213.875 + mesh["lon"].values[None, None, :] + 0 * mesh.values
        assert np.allclose(mesh.values[wanted], expected[wanted], atol=1e-3)
        # nothing beyond the mesh, which is 5-4 W
        lons = np.broadcast_to(mesh["lon"].values[None, None, :], mesh.shape)[wanted]
        assert lons.min() >= -5.05 and lons.max() <= -3.95

    def test_the_bottom_and_the_water_column_are_there(self, compared):
        out, _ = compared
        surface = field(out, "mesh", "temperature_surface_annual.nc")
        bottom = field(out, "mesh", "temperature_bottom_annual.nc")
        assert np.isfinite(bottom.values).sum() == np.isfinite(surface.values).sum()
        # the grid's levels are 0.5, 2 and 6.5 m: only where the water is shallow is the deepest below the top layer
        shown = np.isfinite(bottom.values)
        assert (bottom.values[shown] >= surface.values[shown] - 1e-4).all()
        assert (bottom.values[shown] > surface.values[shown] + 0.5).any()
        assert np.isfinite(field(out, "mesh", "temperature_mean_annual.nc").values).any()

    def test_the_regridded_files_are_kept_in_the_results(self, compared):
        out, _ = compared
        folder = os.path.join(results(out), sim_compare.FVCOM_REGRIDDED)
        # each FVCOM file regridded, beside the grid it was regridded onto
        regridded = glob.glob(os.path.join(folder, "**", "*.nc"), recursive=True)
        assert [path for path in regridded if not path.endswith("_grid.nc")]

    def test_each_variable_has_regridded_files_of_its_own(self, mixed_simulations, tmp_path):
        # each regridded file holds only its variable's parts (nctoolkit's open_fvcom), so the second variable's
        # regridding must not replace the first's
        arguments = self.given_mixed(
            mixed_simulations,
            out_dir=str(tmp_path),
            variables={
                "Temperature": {"Mesh": "temp", "Grid": "thetao"},
                "Warmth": {"Mesh": "temp", "Grid": "thetao"},
            },
            climatology={
                name: {"start": 2012, "end": 2013, "surface": True, "bottom": False, "water_column": None}
                for name in ("Temperature", "Warmth")
            },
            long_term=None,
            unit_conversions={name: {"Grid": {"multiplier": 1, "adder": 0}} for name in ("Temperature", "Warmth")},
        )
        compare_quietly(arguments)
        folder = os.path.join(results(tmp_path), sim_compare.FVCOM_REGRIDDED, "mesh")
        assert sorted(os.listdir(folder)) == ["temperature", "warmth"]
        for name in ("temperature", "warmth"):
            assert glob.glob(os.path.join(folder, name, "*.nc"))
        with xr.open_dataset(os.path.join(results(tmp_path), "mesh", "warmth_surface_annual.nc")) as ds:
            assert np.isfinite(ds["warmth"].values).any()

    @pytest.mark.parametrize(
        "extra, message",
        [
            ({"unify": {"baseline": "Mesh"}}, "is FVCOM output, which is regridded"),
        ],
    )
    def test_what_cannot_be_done_with_a_mesh_and_a_grid_is_refused(self, mixed_simulations, tmp_path, extra, message):
        with pytest.raises(ValueError, match=message):
            oceanval.compare_simulations(**self.given_mixed(mixed_simulations, out_dir=str(tmp_path), **extra))

    @pytest.fixture(scope="class")
    @classmethod
    def on_a_box(cls, mixed_simulations, tmp_path_factory):
        out = tmp_path_factory.mktemp("compare_mixed_box")
        box = {"lon": [-6, -3], "lat": [49, 52], "res": 0.25}
        return out, compare_quietly(cls.given_mixed(mixed_simulations, out_dir=str(out), unify=box))

    def test_a_box_is_allowed(self, on_a_box):
        out, info = on_a_box
        assert info["fvcom"] is False and info["fvcom_regridded"] == ["Mesh"]
        assert info["unify"]["method"] == "box" and info["same_grid"] is True
        assert "lon-lat" in info["unify_text"] and "first simulation that is not FVCOM" in info["unify_text"]
        mesh = field(out, "mesh", "temperature_surface_annual.nc")
        grid = field(out, "grid", "temperature_surface_annual.nc")
        assert mesh.shape == grid.shape
        shown = np.isfinite(mesh.values)
        assert shown.any() and (shown == np.isfinite(grid.values)).all()

    def test_a_domain_mask_is_refused(self, mixed_simulations, tmp_path):
        mask = str(tmp_path / "mask.nc")
        TestDomainMask.write_mask(mask)
        arguments = self.given_mixed(mixed_simulations, out_dir=str(tmp_path / "out"), domain_mask=mask)
        with pytest.raises(ValueError, match="domain_mask does not work when FVCOM output"):
            oceanval.compare_simulations(**arguments)
