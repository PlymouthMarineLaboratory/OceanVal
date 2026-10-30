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
from oceanval import analysis_report, sim_compare
from conftest import no_book
from simulations import write_analysis_simulation

YEARS = (2012, 2013, 2014)
# surface temperature is 10 plus the month, plus the trend for each year after the first
TREND_A, TREND_B = 1.0, 3.0
B_LATS = list(np.arange(49.5, 53.6, 0.5))
B_LONS = list(np.arange(-4.5, 0.6, 0.5))
CLIMATOLOGY = {"Temperature": {"start": 2012, "end": 2014, "surface": True}}


@pytest.fixture(scope="module")
def sims(tmp_path_factory):
    """Two simulations: A on the usual grid and B on a finer one, which rises faster."""
    base = tmp_path_factory.mktemp("compare")
    a, b = base / "a", base / "b"
    write_analysis_simulation(str(a), years=YEARS, daily=False, trend=TREND_A)
    write_analysis_simulation(str(b), years=YEARS, daily=False, trend=TREND_B, lats=B_LATS, lons=B_LONS)
    return str(a), str(b)


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
        monkeypatch.setattr(analysis, "stream_time_res", lambda files: asked.append(files))
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

        def spy(rows, sim_dir, *arguments):
            given_to[sim_dir] = arguments[-1]
            return original(rows, sim_dir, *arguments)

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
        with pytest.raises(ValueError, match=r"not in the same units.*degC.*K"):
            oceanval.compare_simulations(
                **given(sims, simulations=[
                    {"name": "Base run", "sim_dir": sims[0], "n_dirs_down": 1},
                    {"name": "Kelvin run", "sim_dir": str(other), "n_dirs_down": 1},
                ], out_dir=str(tmp_path / "out"))
            )


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
    built = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(analysis_report, "InterimBook", no_book(built))
        oceanval.compare_simulations(**arguments)
    return built[0]


@pytest.fixture(scope="module")
def out(sims, tmp_path_factory):
    out = tmp_path_factory.mktemp("own")
    long_term = {"Temperature": {"start": 2012, "end": 2014, "surface": True}}
    return out, compare_quietly(given(sims, long_term=long_term, out_dir=str(out)))


class TestEachSimulationOnItsOwn:

    def test_there_is_a_folder_for_each(self, out):
        folder, _ = out
        assert sorted(os.listdir(results(folder))) == ["base_run", "comparison.json", "warm_run"]
        for key in ("base_run", "warm_run"):
            assert "temperature_surface_monthly.nc" in os.listdir(os.path.join(results(folder), key))

    def test_each_has_its_own_answers_on_its_own_grid(self, out):
        folder, _ = out
        a = field(folder, "base_run", "temperature_surface_annual.nc")
        b = field(folder, "warm_run", "temperature_surface_annual.nc")
        # 10 + the mean of the months (6.5) + the trend over 2012-2014 (mean of 0, 1, 2 years)
        assert float(a.mean()) == pytest.approx(10 + 6.5 + TREND_A * 1, abs=1e-4)
        assert float(b.mean()) == pytest.approx(10 + 6.5 + TREND_B * 1, abs=1e-4)
        assert a.shape[-2:] == (4, 5)
        assert b.shape[-2:] == (len(B_LATS), len(B_LONS))

    def test_the_series_are_per_simulation(self, out):
        folder, _ = out
        for key, trend in (("base_run", TREND_A), ("warm_run", TREND_B)):
            frame = pd.read_csv(os.path.join(results(folder), key, "temperature_surface_long_term.csv"))
            domain = frame[frame.name == "Full Domain"]
            annual = domain.groupby("year").value.mean()
            assert annual.loc[2012] == pytest.approx(10 + 6.5, abs=1e-4)
            assert annual.loc[2014] == pytest.approx(10 + 6.5 + 2 * trend, abs=1e-4)

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

    def test_the_directories_are_remembered(self, sims, tmp_path, no_report):
        from oceanval import user_cache

        oceanval.compare_simulations(**given(sims, out_dir=str(tmp_path)))
        assert set(sims) <= set(user_cache.recent_sim_dirs())

    def test_a_comparison_is_replaced_whole(self, sims, tmp_path, no_report):
        oceanval.compare_simulations(**given(sims, out_dir=str(tmp_path)))
        stale = os.path.join(results(tmp_path), "stale.txt")
        open(stale, "w").close()
        oceanval.compare_simulations(**given(sims, out_dir=str(tmp_path)))
        assert not os.path.exists(stale)

    def test_the_window_is_told_of_each_analysis_in_each_simulation(self, sims, tmp_path, no_report):
        from oceanval import prompts

        told = []
        with prompts.told_by(told.append):
            oceanval.compare_simulations(**given(sims, out_dir=str(tmp_path)))
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

    def run(self, holes, out, unify, **options):
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

    def test_a_baseline_gives_every_simulation_its_grid_and_cells(self, holes, tmp_path):
        info = self.run(holes, tmp_path, {"baseline": "Base run"})
        a = field(tmp_path, "base_run", "temperature_surface_monthly.nc")
        b = field(tmp_path, "warm_run", "temperature_surface_monthly.nc")
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

    def test_the_series_are_of_the_same_cells(self, holes, tmp_path):
        """The means are over the same cells, so the same trend in A and B differs only by what they rise by."""
        self.run(holes, tmp_path, {"baseline": "Base run"})
        means = {}
        for key in ("base_run", "warm_run"):
            frame = pd.read_csv(os.path.join(results(tmp_path), key, "temperature_surface_long_term.csv"))
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


class TestEveryOption:
    """Everything analyze() does, for two daily simulations on different grids."""

    @pytest.fixture(scope="class")
    @classmethod
    def daily(cls, tmp_path_factory):
        base = tmp_path_factory.mktemp("daily")
        a, b = base / "a", base / "b"
        write_analysis_simulation(str(a), years=YEARS, daily=True, trend=TREND_A)
        write_analysis_simulation(str(b), years=YEARS, daily=True, trend=TREND_B, lats=B_LATS, lons=B_LONS)
        return str(a), str(b)

    OPTIONS = dict(
        variables={"Temperature": "thetao", "Chlorophyll": "P1_Chl+P2_Chl"},
        climatology={"Temperature": {"start": 2012, "end": 2014, "surface": True, "bottom": True, "water_column": "mean"}},
        phenology={"Chlorophyll": {"start": 2012, "end": 2014}},
        long_term={"Temperature": {"start": 2012, "end": 2014, "surface": True}},
        seasonal={"Temperature": {"start": 2012, "end": 2014, "surface": True}},
        map_changes={
            "Temperature": {
                "historical": {"start": 2012, "end": 2012},
                "future": [{"start": 2014, "end": 2014}],
                "plot": "absolute_changes",
                "change": "absolute",
                "surface": True,
            }
        },
        thickness="e3t",
        stations={"Station": [-2.0, 51.5]},
    )

    @pytest.mark.parametrize("unify", [None, {"baseline": "Base run"}, {"lon": [-4, 0], "lat": [50, 53], "res": 1}])
    def test_each_simulation_has_each_result(self, daily, tmp_path, unify):
        info = compare_quietly(given(daily, unify=unify, out_dir=str(tmp_path), **self.OPTIONS))
        assert [v["key"] for v in info["variables"]] == ["temperature", "chlorophyll"]
        for key in ("base_run", "warm_run"):
            files = set(os.listdir(os.path.join(results(tmp_path), key)))
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

    def test_the_phenology_is_of_the_same_cells(self, daily, tmp_path):
        compare_quietly(given(daily, unify={"baseline": "Base run"}, out_dir=str(tmp_path), **self.OPTIONS))
        with xr.open_dataset(os.path.join(results(tmp_path), "base_run", "chlorophyll_phenology.nc")) as a, xr.open_dataset(
            os.path.join(results(tmp_path), "warm_run", "chlorophyll_phenology.nc")
        ) as b:
            assert a["peak_day"].shape == b["peak_day"].shape
            assert (np.isnan(a["peak_day"].values) == np.isnan(b["peak_day"].values)).all()
            # A peaks about 60 + 30 for each row north (a day out for the leap year); B has its own rows
            assert a["peak_day"].values[:, 0] == pytest.approx([60, 90, 120, 150], abs=1.5)

    def test_transects_of_both_on_the_common_grid(self, daily, tmp_path):
        from oceanval import transects

        if not transects.available():
            pytest.skip("needs nctoolkit 1.3.6 or later")
        line = {
            "start": [-2.0, 50.0],
            "end": [-2.0, 53.0],
            "climatology": {"start": 2012, "end": 2014},
            "vertical": {"start": 2012, "end": 2014},
            "trends": {"start": 2012, "end": 2014, "how": "absolute"},
        }
        for unify in (None, {"baseline": "Base run"}):
            out = tmp_path / ("none" if unify is None else "baseline")
            info = compare_quietly(
                given(
                    daily,
                    unify=unify,
                    variables={"Temperature": "thetao"},
                    climatology=None,
                    transects={"Temperature": [line]},
                    thickness="e3t",
                    out_dir=str(out),
                )
            )
            for key in ("base_run", "warm_run"):
                files = os.listdir(os.path.join(results(out), key))
                assert any("transect_1_vertical" in name for name in files)
                assert any("transect_1_trends" in name for name in files)
            assert info["variables"][0]["kinds"] == ["transects"]
            steps = {
                sim: own["transects"]["lines"][0]["steps"] for sim, own in info["variables"][0]["sims"].items()
            }
            if unify is None:
                # each is sampled as its own grid has it
                assert steps["Base run"] != steps["Warm run"]
            else:
                # on one grid, every simulation has the same points
                assert len(set(steps.values())) == 1
