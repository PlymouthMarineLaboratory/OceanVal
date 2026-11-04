"""analyze(): a simulation's climatologies and phenology on their own, with
no observations (see oceanval.analysis), and the forms of the oceanval
window's Analyze steps (see oceanval.analysis_forms)."""

import ast
import datetime
import glob
import json
import os
import re
import shutil

import cftime
import numpy as np
import pandas as pd
import pytest
import xarray as xr

import oceanval
from oceanval import analysis, analysis_forms, surface
from simulations import (
    ANALYSIS_LEVEL_VALUES,
    ANALYSIS_THICKNESS,
    B_LATS,
    B_LONS,
    TREND_A,
    TREND_B,
    analysis_peak_day,
    write_analysis_simulation,
    write_month_files,
)

LEVELS = np.array(ANALYSIS_LEVEL_VALUES)
THICKNESS = np.array(ANALYSIS_THICKNESS)


@pytest.fixture(scope="module")
def daily(tmp_path_factory):
    """A daily simulation of 2013-2014, written once for the module."""
    root = tmp_path_factory.mktemp("daily") / "sim"
    write_analysis_simulation(str(root))
    return str(root)


@pytest.fixture(scope="module")
def monthly(tmp_path_factory):
    """A monthly simulation of 2013-2014 with March 2014 missing from its
    chlorophyll."""
    root = tmp_path_factory.mktemp("monthly") / "sim"
    write_analysis_simulation(str(root), daily=False)
    path = glob.glob(os.path.join(str(root), "2014", "*ptrc_T.nc"))[0]
    with xr.open_dataset(path) as ds:
        kept = ds.isel(time=[month for month in range(12) if month != 2]).load()
    kept.to_netcdf(path)
    return str(root)


def results(out_dir):
    return os.path.join(out_dir, analysis.FOLDER, analysis.RESULTS)


def values(path, variable):
    """A variable's values in path, one row for each time."""
    with xr.open_dataset(path) as ds:
        data = ds[variable].values
    return data.reshape(data.shape[0], -1) if data.ndim > 2 else data.reshape(1, -1)


class TestOrderOfTheChain:
    """nctoolkit chains what is asked for into one CDO call per file, the last asked for applied last. Model
    files can be many GB with a great many variables, so what cuts them down comes first: the variables, then the
    surface (as matchup takes it, before the missing value is set), the missing value, the bottom (which needs it),
    and the renaming last."""

    @staticmethod
    def chain(monkeypatch, files, parts, years, layer, shift=None, missing=None, convert=None):
        import nctoolkit as nc

        monkeypatch.setattr(nc.DataSet, "run", lambda self: None)
        row = {"key": "chl", "name": "chl", "parts": parts, "as_missing": missing}
        if convert is not None:
            row["convert"] = convert
        ds = analysis._model_data(files, row, years, shift, layer, 3)
        return ds.history[-1]

    @staticmethod
    def first_applied(command, *operators):
        """Whether the operators are applied in the order given: CDO applies the rightmost first."""
        places = [command.index(operator) for operator in operators]
        return places == sorted(places, reverse=True)

    @pytest.mark.parametrize(
        "layer, order",
        [
            ("surface", ("-selname", "-topvalue", "-setrtomiss", "-chname")),
            ("bottom", ("-selname", "-setrtomiss", "-bottomvalue", "-chname")),
        ],
    )
    def test_the_variable_is_chosen_then_the_level_then_the_name(self, daily, monkeypatch, layer, order):
        files = sorted(glob.glob(os.path.join(daily, "*", "*grid_T.nc")))
        command = self.chain(monkeypatch, files, ["thetao"], (2013, 2014), layer, missing=[0, 0])
        assert self.first_applied(command, *order), command

    def test_a_sum_is_made_of_the_levels_chosen(self, daily, monkeypatch):
        files = sorted(glob.glob(os.path.join(daily, "*", "*ptrc_T.nc")))
        command = self.chain(monkeypatch, files, ["P1_Chl", "P2_Chl"], (2013, 2014), "surface", missing=[0, 0])
        assert self.first_applied(command, "-selname", "-topvalue", "-setrtomiss", "-expr"), command

    @pytest.mark.parametrize("parts, last", [(["thetao"], "-chname"), (["P1_Chl", "P2_Chl"], "-expr")])
    def test_a_comparisons_conversion_of_units_is_last(self, daily, monkeypatch, parts, last):
        """After the missing value, which is in the simulation's own units, and after a sum, so it is made once."""
        stream = "grid_T" if parts == ["thetao"] else "ptrc_T"
        files = sorted(glob.glob(os.path.join(daily, "*", f"*{stream}.nc")))
        command = self.chain(monkeypatch, files, parts, (2013, 2014), "surface", missing=[0, 0], convert=(0.001, -273.15))
        assert self.first_applied(command, "-setrtomiss", last, "-mulc,0.001", "-addc,-273.15"), command

    @pytest.mark.parametrize("convert, says", [(None, ()), ((1, 0), ()), ((1000, 0), ("-mulc,1000",)), ((1, 2.5), ("-addc,2.5",))])
    def test_only_what_converts_is_asked_for(self, daily, monkeypatch, convert, says):
        files = sorted(glob.glob(os.path.join(daily, "*", "*grid_T.nc")))
        command = self.chain(monkeypatch, files, ["thetao"], (2013, 2014), "surface", convert=convert)
        assert [word for word in ("-mulc", "-addc") if word in command] == [said.split(",")[0] for said in says]
        assert all(said in command for said in says)

    def test_a_chosen_time_step_and_file_are_the_ones_read(self, daily, monkeypatch):
        import nctoolkit as nc

        files = sorted(glob.glob(os.path.join(daily, "*", "*grid_T.nc")))
        monkeypatch.setattr(nc.DataSet, "run", lambda self: None)
        row = {"key": "chl", "name": "chl", "parts": ["thetao"], "as_missing": None}
        ds = analysis._model_data(files, row, None, None, "surface", 3, at=(files[1], 1))
        assert "timestep=2" in ds.history[-1] and ds.start == [files[1]]
        # without it, the first of the first file, as before
        ds = analysis._model_data(files, row, None, None, "surface", 3)
        assert "timestep=1" in ds.history[-1] and ds.start == [files[0]]

    def test_the_variable_and_the_first_time_step_are_chosen_together_first(self, daily, monkeypatch):
        files = sorted(glob.glob(os.path.join(daily, "*", "*grid_T.nc")))
        command = self.chain(monkeypatch, files, ["thetao"], None, "surface")
        assert self.first_applied(command, "-select,name=thetao,timestep=1", "-topvalue", "-chname"), command
        assert "selname" not in command and "seltimestep" not in command

    def test_the_variable_is_chosen_before_the_times_are_shifted(self, daily, monkeypatch):
        files = sorted(glob.glob(os.path.join(daily, "*", "*grid_T.nc")))
        command = self.chain(monkeypatch, files, ["thetao"], (2013, 2014), "surface", shift=1.0)
        assert self.first_applied(command, "-selname", "-topvalue", "-shifttime", "-selyear", "-chname"), command

    def test_the_sum_of_the_parts_surface_values(self, tmp_path):
        """Each part's surface is the first value from the top that is not missing, and the parts are then added."""
        path = str(tmp_path / "sum.nc")
        a = np.full((1, 3, 2, 2), 1.0, dtype="f4")
        b = np.full((1, 3, 2, 2), 10.0, dtype="f4")
        a[0, 0, 0, 0] = np.nan
        a[0, 1, 0, 0] = 2.0
        xr.Dataset(
            {"a": (("time", "deptht", "y", "x"), a), "b": (("time", "deptht", "y", "x"), b)},
            coords={
                "time": ("time", [0.0], {"units": "days since 2013-01-01"}),
                "deptht": ("deptht", [5.0, 10.0, 20.0], {"units": "m", "positive": "down", "axis": "Z"}),
            },
        ).to_netcdf(path, encoding={"a": {"_FillValue": np.float32(-9999)}, "b": {"_FillValue": np.float32(-9999)}})
        for layer, expected in (("surface", 12.0), ("bottom", 11.0)):
            ds = analysis._model_data(path, {"key": "chl", "name": "chl", "parts": ["a", "b"], "as_missing": None}, None, None, layer, 3)
            ds.run()
            with xr.open_dataset(ds[0]) as out:
                assert float(out["chl"].values[0, 0, 0]) == expected
                assert float(out["chl"].values[0, 1, 1]) == 11.0

    def test_the_surface_is_taken_before_the_missing_value_is_set(self, tmp_path):
        """As matchup does: a surface of 0 that is the missing value is missing, not the value below it."""
        path = str(tmp_path / "zero.nc")
        a = np.full((1, 3, 1, 2), 5.0, dtype="f4")
        a[0, 0, 0, 0] = 0.0
        xr.Dataset(
            {"a": (("time", "deptht", "y", "x"), a)},
            coords={
                "time": ("time", [0.0], {"units": "days since 2013-01-01"}),
                "deptht": ("deptht", [5.0, 10.0, 20.0], {"units": "m", "positive": "down", "axis": "Z"}),
            },
        ).to_netcdf(path)
        row = {"key": "chl", "name": "chl", "parts": ["a"], "as_missing": [0, 0]}
        for layer, first in (("surface", np.nan), ("bottom", 5.0)):
            ds = analysis._model_data(path, row, None, None, layer, 3)
            ds.run()
            with xr.open_dataset(ds[0]) as out:
                assert np.isnan(first) == np.isnan(float(out["chl"].values[0, 0, 0]))
                assert float(out["chl"].values[0, 0, 1]) == 5.0
                if layer == "bottom":
                    assert float(out["chl"].values[0, 0, 0]) == 5.0


class TestTheSurfaceFromTheFirstLevel:
    """CDO's select reads only the first level; selname, seltimestep and topvalue read them all. It is used only
    when the first level is the surface, and gives what topvalue gives."""

    @staticmethod
    def write(path, positive="down", compress=False, hole=False, days=(0.0,)):
        values = np.arange(1, 25, dtype="f4").reshape(1, 3, 2, 4) * np.arange(1, len(days) + 1, dtype="f4").reshape(-1, 1, 1, 1)
        if hole:
            values[0, 0, 0, 0] = np.nan
        attrs = {"units": "m", "axis": "Z"}
        if positive:
            attrs["positive"] = positive
        xr.Dataset(
            {
                "a": (("time", "deptht", "y", "x"), values),
                "w": (("time", "y", "x"), values[:, 0] * 10),
            },
            coords={
                "time": ("time", list(days), {"units": "days since 2013-01-01"}),
                "deptht": ("deptht", [5.0, 10.0, 20.0], attrs),
            },
        ).to_netcdf(
            path,
            encoding={
                "a": {"_FillValue": np.float32(-9999), "zlib": compress, "chunksizes": (1, 3, 2, 4)},
                "w": {"_FillValue": np.float32(-9999), "zlib": compress, "chunksizes": (1, 2, 4)},
            },
        )
        return path

    @staticmethod
    def stream(path):
        return {"files": [path], "variables": {"a": {"levels": 3}, "w": {"levels": 1}}}

    def test_it_is_used_for_an_uncompressed_axis_positive_down_with_the_surface_at_the_first_level(self, tmp_path):
        row = {"model_variable": "a+w"}
        assert analysis._first_level_is_surface(row, self.stream(self.write(str(tmp_path / "f.nc")))) == (True, "", True)

    def test_it_is_used_without_the_small_cache_if_cdo_does_not_take_one(self, tmp_path, monkeypatch):
        from oceanval import surface

        monkeypatch.setattr(surface, "cdo_takes_a_chunk_cache", lambda path: False)
        found = analysis._first_level_is_surface({"model_variable": "a"}, self.stream(self.write(str(tmp_path / "f.nc"))))
        assert found == (True, "", False)

    def test_it_is_used_without_the_small_cache_for_a_compressed_file(self, tmp_path):
        """A compressed chunk is read whole: a small cache would decompress it again for every level."""
        row = {"model_variable": "a+w"}
        found = analysis._first_level_is_surface(row, self.stream(self.write(str(tmp_path / "f.nc"), compress=True)))
        assert found == (True, "", False)

    @pytest.mark.parametrize(
        "options, why",
        [
            ({"positive": "up"}, "not at the surface"),
            ({"positive": None}, "cannot be told"),
            ({"hole": True}, "missing at the first level"),
        ],
    )
    def test_it_is_not_used_otherwise(self, tmp_path, options, why):
        row = {"model_variable": "a"}
        found, reason, small_cache = analysis._first_level_is_surface(
            row, self.stream(self.write(str(tmp_path / "f.nc"), **options))
        )
        assert found is False and small_cache is False
        assert why in reason

    @pytest.mark.parametrize("compress", [False, True])
    @pytest.mark.parametrize("first_step", [False, True])
    @pytest.mark.parametrize("parts", [["a"], ["a", "w"]])
    def test_it_gives_what_topvalue_gives(self, tmp_path, parts, first_step, compress):
        path = self.write(str(tmp_path / "f.nc"), compress=compress)
        found = []
        for fast in (False, True):
            row = {"key": "chl", "name": "chl", "parts": parts, "as_missing": [0, 0], "surface_fast": fast}
            years = None if first_step else (2013, 2013)
            ds = analysis._model_data(path, row, years, None, "surface", 3)
            ds.run()
            with xr.open_dataset(ds[0]) as out:
                found.append((out["chl"].dims, np.asarray(out["chl"].values), out["chl"].attrs.get("units")))
        assert found[0][0] == found[1][0]
        assert np.array_equal(found[0][1], found[1][1], equal_nan=True)

    @pytest.mark.parametrize("shift", [None, 1.0])
    def test_it_gives_what_topvalue_gives_for_a_file_that_runs_over_the_years_asked(self, tmp_path, shift):
        """Two of the file's four days are in the second year: the years are chosen (and the times shifted) as before."""
        path = self.write(str(tmp_path / "f.nc"), days=(10.0, 20.0, 400.0, 410.0))
        found = []
        for fast in (False, True):
            row = {"key": "chl", "name": "chl", "parts": ["a", "w"], "as_missing": [0, 0], "surface_fast": fast}
            ds = analysis.monthly_means_of([path], row, "surface", (2013, 2013), shift, 3)
            with xr.open_dataset(ds[0]) as out:
                found.append((out["chl"].dims, np.asarray(out["chl"].values)))
        assert found[0][1].shape[0] == 1
        assert found[0][0] == found[1][0]
        assert np.array_equal(found[0][1], found[1][1], equal_nan=True)

    def test_the_chain_selects_the_variables_and_the_level_in_one_step_first(self, tmp_path, monkeypatch):
        import nctoolkit as nc

        monkeypatch.setattr(nc.DataSet, "run", lambda self: None)
        row = {"key": "chl", "name": "chl", "parts": ["a", "w"], "as_missing": [0, 0], "surface_fast": True}
        command = analysis._model_data(self.write(str(tmp_path / "f.nc")), row, (2013, 2013), None, "surface", 3).history[-1]
        assert "selname" not in command and "topvalue" not in command and "sellevidx" not in command
        assert TestOrderOfTheChain.first_applied(
            command, "-select,name=a,w,levidx=1", "-setzaxis", "-setrtomiss", "-expr"
        ), command

    def test_the_bottom_does_not_use_it(self, tmp_path, monkeypatch):
        import nctoolkit as nc

        monkeypatch.setattr(nc.DataSet, "run", lambda self: None)
        row = {"key": "chl", "name": "chl", "parts": ["a"], "as_missing": None, "surface_fast": True}
        command = analysis._model_data(self.write(str(tmp_path / "f.nc")), row, (2013, 2013), None, "bottom", 3).history[-1]
        assert "-selname" in command and "-bottomvalue" in command and "-select," not in command

    def test_the_chunk_cache_is_small_while_it_is_read_and_restored_after(self, monkeypatch):
        names = surface.CACHE_VARIABLES
        monkeypatch.setenv("CDI_CHUNK_CACHE", "7")
        monkeypatch.setenv("CDI_CACHESIZE_IN", "8")
        small = {"surface_fast": True, "surface_small_cache": True}
        with analysis._chunk_cache(small, "surface"):
            assert all(os.environ[name] == str(analysis.CHUNK_CACHE) for name in names)
        assert os.environ["CDI_CHUNK_CACHE"] == "7" and os.environ["CDI_CACHESIZE_IN"] == "8"
        for name in names:
            monkeypatch.delenv(name)
        with analysis._chunk_cache(small, "surface"):
            assert all(name in os.environ for name in names)
        assert not any(name in os.environ for name in names)
        for row, layer in (
            ({"surface_fast": False, "surface_small_cache": False}, "surface"),
            (small, "bottom"),
            # a compressed file's first level, read with HDF5's own cache
            ({"surface_fast": True, "surface_small_cache": False}, "surface"),
        ):
            with analysis._chunk_cache(row, layer):
                assert not any(name in os.environ for name in names)

    def test_it_is_set_while_cdo_runs(self, tmp_path):
        """CDO itself gets the small cache: it says so when asked (CDI_CHUNK_CACHE_INFO, CDI_CACHE_INFO from 2.6 on)."""
        import subprocess

        path = self.write(str(tmp_path / "f.nc"))
        row = {"key": "chl", "name": "chl", "parts": ["a"], "as_missing": None, "surface_fast": True, "surface_small_cache": True}
        with analysis._chunk_cache(row, "surface"):
            environment = dict(os.environ, **{name: "1" for name in surface.CACHE_INFO})
            out = subprocess.run(["cdo", "-s", "info", path], env=environment, capture_output=True, text=True)
        assert f"chunkCacheSize={analysis.CHUNK_CACHE}" in out.stderr + out.stdout


class TestAMonthAFile:
    """When each file is a month of output, and no month is in two of them (time_res.one_month_per_file), the
    mean of each month is taken as its file is read, so what is merged is a time a file, not all its days. It is
    what the mean of each month of the merged files is."""

    @pytest.fixture(scope="class")
    def months(self, daily, tmp_path_factory):
        """The daily simulation's 2013, as a whole year in a file, and as a simulation of a file a month."""
        root = tmp_path_factory.mktemp("months")
        written = {}
        for path in sorted(glob.glob(os.path.join(daily, "2013", "*.nc"))):
            stream = "grid_T" if "grid_T" in path else "ptrc_T"
            written[stream] = write_month_files(path, str(root / "whole"), str(root / "sim"))
        return str(root / "sim"), written

    @staticmethod
    def means(files, layer, fast=False, month_per_file=False, time_shift=None, levels=len(LEVELS)):
        row = {
            "key": "t", "name": "t", "parts": ["thetao"], "as_missing": None,
            "surface_fast": fast, "month_per_file": month_per_file,
        }
        thickness = analysis.Z_LEVEL if layer in analysis.WATER_COLUMN else None
        ds = analysis.monthly_means_of(files, row, layer, (2013, 2013), time_shift, levels, thickness)
        with xr.open_dataset(ds[0]) as out:
            return out.load()

    @staticmethod
    def same(new, old):
        np.testing.assert_allclose(new["t"].values, old["t"].values, rtol=1e-6)
        np.testing.assert_array_equal(new["time"].values, old["time"].values)
        assert new["t"].dims == old["t"].dims
        bounds = [name for name in old.variables if "bnds" in name or "bounds" in name]
        for name in bounds:
            np.testing.assert_array_equal(new[name].values, old[name].values)

    def test_it_is_found_when_the_files_are_located(self, daily, months):
        found = {}
        for name, sim in (("months", months[0]), ("years", daily)):
            rows = analysis.check_arguments(
                {"Temperature": "thetao"}, {"Temperature": {"start": 2013, "end": 2013, "surface": True}},
                None, None, None, None, None, None, None,
            )
            rows[0]["as_missing"] = None
            analysis.locate_rows(rows, sim, 1, [], [], None)
            found[name] = rows[0]["month_per_file"]
        assert found == {"months": True, "years": False}

    @pytest.mark.parametrize(
        "layer, fast", [("surface", False), ("surface", True), ("bottom", False), ("mean", False)]
    )
    def test_the_means_of_the_months_are_the_same(self, months, layer, fast):
        whole, split = months[1]["grid_T"]
        new = self.means(split, layer, fast, month_per_file=True)
        old = self.means([whole], layer, fast)
        assert new.sizes["time"] == 12
        self.same(new, old)

    def test_with_the_times_shifted(self, daily, tmp_path):
        """Output dated at the end of each day: on disk its files are not a month each, shifted they are."""
        path = sorted(glob.glob(os.path.join(daily, "2013", "*grid_T.nc")))[0]
        whole, split = write_month_files(path, str(tmp_path / "whole"), str(tmp_path / "sim"), end_stamped=True)
        new = self.means(split, "surface", month_per_file=True, time_shift=-1)
        old = self.means([whole], "surface", time_shift=-1)
        assert new.sizes["time"] == 12
        self.same(new, old)

    def test_the_mean_of_each_file_is_taken_before_they_are_merged(self, months, monkeypatch):
        import nctoolkit as nc

        monkeypatch.setattr(nc.DataSet, "run", lambda self: None)
        whole, split = months[1]["grid_T"]
        row = {"key": "t", "name": "t", "parts": ["thetao"], "as_missing": None, "month_per_file": True}
        command = " ".join(analysis.monthly_means_of(split, row, "surface", (2013, 2013), None, 3).history)
        assert TestOrderOfTheChain.first_applied(command, "-topvalue", "-chname", "-timmean", "-mergetime"), command
        assert "-monmean" not in command
        row["month_per_file"] = False
        command = " ".join(analysis.monthly_means_of(split, row, "surface", (2013, 2013), None, 3).history)
        assert "-monmean" in command and "-timmean" not in command


class TestArguments:
    @pytest.mark.parametrize(
        "name, key",
        [("Chlorophyll", "chlorophyll"), ("Chlorophyll a", "chlorophyll_a"), ("2m temp!", "v_2m_temp"), ("!!", "")],
    )
    def test_names_become_file_names(self, name, key):
        assert analysis.variable_key(name) == key

    @pytest.mark.parametrize(
        "arguments, message",
        [
            ({"variables": {}}, "variables must be a dict"),
            ({"climatology": {"Other": {"start": 2000, "end": 2001, "surface": True}}}, "not in variables"),
            ({"climatology": None}, "nothing to analyze"),
            ({"climatology": {"Chl": {"start": 2001, "end": 2000, "surface": True}}}, "before its start"),
            ({"climatology": {"Chl": {"start": "x", "end": 2000, "surface": True}}}, "must be a year"),
            ({"climatology": {"Chl": {"start": 2000, "end": 2001}}}, "needs surface=True"),
            ({"climatology": {"Chl": {"start": 2000, "end": 2001, "water_column": "sum"}}}, '"mean"'),
            ({"climatology": {"Chl": {"start": 2000, "end": 2001, "water_column": "mean"}}}, "cell thickness"),
            ({"variables": {"Chl": "a", "chl": "b"}}, "would name their files the same"),
            ({"variables": {"Chl": ""}}, "Give the model variable"),
            ({"time_shift": 0.5}, "time_shift"),
        ],
    )
    def test_what_is_wrong_is_said(self, arguments, message):
        given = dict(
            {
                "variables": {"Chl": "P1_Chl"},
                "climatology": {"Chl": {"start": 2000, "end": 2001, "surface": True}},
            },
            **arguments,
        )
        with pytest.raises(ValueError, match=message):
            analysis.check_arguments(**given)

    def test_what_is_checked(self):
        rows = analysis.check_arguments(
            {"Chlorophyll": "P1_Chl + P2_Chl", "Temperature": "thetao", "Unused": "x"},
            climatology={"Temperature": {"start": "2000", "end": 2009, "surface": True, "water_column": "integral"}},
            phenology={"Chlorophyll": {"start": 2001, "end": 2002}},
            thickness="e3t",
        )
        # a variable nothing is worked out for is left out
        assert [row["name"] for row in rows] == ["Chlorophyll", "Temperature"]
        assert rows[0]["model_variable"] == "P1_Chl+P2_Chl"
        assert rows[0]["parts"] == ["P1_Chl", "P2_Chl"]
        assert rows[0]["phenology"] == {"start": 2001, "end": 2002}
        assert rows[1]["climatology"] == {"start": 2000, "end": 2009, "layers": ["surface", "integral"]}

    @pytest.mark.parametrize(
        "units, integral",
        [("mmol m-3", "mmol m-2"), ("mg Chl m-3", "mg Chl m-2"), ("mg/m3", "mg/m2"), ("degC", "degC m"), ("", "m")],
    )
    def test_the_units_of_a_depth_integral(self, units, integral):
        assert analysis.integral_units(units) == integral


class TestFiles:
    def test_the_streams_and_their_variables(self, daily):
        found = analysis.streams(daily, 1)
        assert [stream["pattern"] for stream in found] == ["sim_1d_**_**_grid_T.nc", "sim_1d_**_**_ptrc_T.nc"]
        assert all(len(stream["files"]) == 2 for stream in found)
        grid = found[0]["variables"]
        assert grid["thetao"] == {"long_name": "sea water potential temperature", "units": "degC", "levels": 3}
        assert found[1]["variables"]["P1_Chl"]["levels"] == 1

    def test_restart_files_are_left_to_the_file_filters(self, tmp_path):
        root = tmp_path / "sim"
        write_analysis_simulation(str(root), daily=False)
        first = sorted(root.glob("*/*grid_T.nc"))[0]
        shutil.copy(first, first.with_name("sim_restart_2013.nc"))

        # nothing is left out unasked, so it is a stream of its own, to be skipped or not
        found = analysis.streams(str(root), 1)
        assert [stream["pattern"] for stream in found if "restart" in stream["pattern"]] == ["sim_restart_**.nc"]
        found = analysis.streams(str(root), 1, exclude=["restart"])
        assert not [stream for stream in found if "restart" in stream["pattern"]]

    def test_a_variable_is_read_from_the_stream_holding_it(self, daily):
        found = analysis.streams(daily, 1)
        assert [stream["pattern"] for stream in analysis.place("P1_Chl+P2_Chl", found)] == ["sim_1d_**_**_ptrc_T.nc"]
        with pytest.raises(ValueError, match="nope is not in the simulation's output"):
            analysis.place("nope", found)
        with pytest.raises(ValueError, match="not in the same files"):
            analysis.place("P1_Chl+thetao", found)

    def test_the_finest_output_is_chosen(self):
        streams = [
            {"pattern": "monthly_**.nc", "files": ["a", "b"]},
            {"pattern": "daily_**.nc", "files": ["c"]},
            {"pattern": "five_**.nc", "files": ["d"]},
        ]
        res = {"monthly_**.nc": "monthly", "daily_**.nc": "1d", "five_**.nc": "5d"}
        stream, found = analysis.choose_stream(streams, lambda files: res[{"a": "monthly_**.nc", "c": "daily_**.nc", "d": "five_**.nc"}[files[0]]])
        assert (stream["pattern"], found) == ("daily_**.nc", "1d")


class TestClimatology:
    def test_the_surface_and_the_water_column(self, daily, tmp_path, no_report):
        out = str(tmp_path)
        oceanval.analyze(
            daily,
            {"Temperature": "thetao", "Mean temperature": "thetao", "Heat": "thetao"},
            climatology={
                "Temperature": {"start": 2013, "end": 2014, "surface": True},
                "Mean temperature": {"start": 2013, "end": 2014, "water_column": "mean"},
                "Heat": {"start": 2013, "end": 2014, "water_column": "integral"},
            },
            n_dirs_down=1,
            thickness="e3t",
            out_dir=out,
            cores=1, view=False,
        )
        months = np.arange(1, 13)[:, None]
        found = results(out)
        surface = values(os.path.join(found, "temperature_surface_monthly.nc"), "temperature")
        assert np.allclose(surface, LEVELS[0] + months)
        annual = values(os.path.join(found, "temperature_surface_annual.nc"), "temperature")
        assert np.allclose(annual, LEVELS[0] + 6.5)
        mean = values(os.path.join(found, "mean_temperature_mean_monthly.nc"), "mean_temperature")
        assert np.allclose(mean, (LEVELS * THICKNESS).sum() / THICKNESS.sum() + months, atol=1e-5)
        heat = values(os.path.join(found, "heat_integral_monthly.nc"), "heat")
        assert np.allclose(heat, (LEVELS * THICKNESS).sum() + months * THICKNESS.sum(), atol=1e-4)
        with xr.open_dataset(os.path.join(found, "heat_integral_monthly.nc")) as ds:
            assert ds["heat"].attrs["units"] == "degC m"
            assert ds["heat"].attrs["long_name"] == "Heat, water column: depth integral"

        info = json.load(open(os.path.join(found, analysis.INFO_FILE)))
        assert info == no_report[0]
        heat_info = info["variables"][2]
        assert heat_info["pattern"] == "sim_1d_**_**_grid_T.nc"
        assert heat_info["time_res"] == "1d"
        assert heat_info["climatology"]["layers"]["integral"]["units"] == "degC m"
        assert heat_info["climatology"]["dates"]["gaps"] is False

    def test_the_bottom_is_the_deepest_value_with_water(self, daily, tmp_path, no_report, monkeypatch):
        import nctoolkit as nc

        # no water in the deepest level of the east two columns, as at a sea bed
        shallow = os.path.join(str(tmp_path), "sim")
        write_analysis_simulation(shallow)
        for path in glob.glob(os.path.join(shallow, "*", "*grid_T.nc")):
            with xr.open_dataset(path) as ds:
                ds = ds.load()
            ds["thetao"][:, -1, :, -2:] = np.nan
            ds.to_netcdf(path, encoding={"thetao": {"_FillValue": np.float32(-1e20)}})
        chosen = []
        real = nc.DataSet.bottom
        monkeypatch.setattr(nc.DataSet, "bottom", lambda self, choice="level": (chosen.append(choice), real(self, choice))[1])
        out = os.path.join(str(tmp_path), "out")
        oceanval.analyze(
            shallow, {"Temperature": "thetao"},
            climatology={"Temperature": {"start": 2013, "end": 2014, "surface": True, "bottom": True}},
            n_dirs_down=1, out_dir=out, cores=1, view=False,
        )
        # nctoolkit's bottom method, taking the value, not the level
        assert chosen and set(chosen) == {"value"}
        found = results(out)
        with xr.open_dataset(os.path.join(found, "temperature_bottom_monthly.nc")) as ds:
            bottom = ds["temperature"].values
            assert ds["temperature"].attrs["long_name"] == "Temperature, bottom"
        months = np.arange(1, 13)[:, None]
        assert np.allclose(bottom[:, :, :3], (LEVELS[2] + months)[:, :, None])
        # where the deepest level has no water, the deepest level with it
        assert np.allclose(bottom[:, :, 3:], (LEVELS[1] + months)[:, :, None])
        annual = values(os.path.join(found, "temperature_bottom_annual.nc"), "temperature")
        assert np.allclose(annual.reshape(-1, 5)[:, :3], LEVELS[2] + 6.5)
        # the surface is as it was
        assert np.allclose(values(os.path.join(found, "temperature_surface_monthly.nc"), "temperature"), LEVELS[0] + months)
        info = no_report[0]["variables"][0]["climatology"]["layers"]
        assert list(info) == ["surface", "bottom"] and info["bottom"]["title"] == "Bottom"
        assert info["bottom"]["units"] == "degC"

    def test_the_bottom_needs_levels(self, daily, tmp_path, no_report):
        with pytest.raises(ValueError, match="no bottom to take"):
            oceanval.analyze(
                daily, {"Chlorophyll": "P1_Chl"},
                climatology={"Chlorophyll": {"start": 2013, "end": 2014, "bottom": True}},
                n_dirs_down=1, out_dir=str(tmp_path), cores=1, view=False,
            )

    def test_a_layer_must_be_asked_for(self):
        with pytest.raises(ValueError, match="surface=True, bottom=True, or a water_column"):
            analysis.check_arguments(
                {"T": "thetao"}, {"T": {"start": 2013, "end": 2014}}, None, None, None
            )

    def test_a_sum_of_model_variables(self, daily, tmp_path, no_report):
        oceanval.analyze(
            daily,
            {"Chlorophyll": "P1_Chl+P2_Chl"},
            climatology={"Chlorophyll": {"start": 2013, "end": 2013, "surface": True}},
            n_dirs_down=1,
            out_dir=str(tmp_path),
            cores=1, view=False,
        )
        monthly = values(os.path.join(results(str(tmp_path)), "chlorophyll_surface_monthly.nc"), "chlorophyll")
        # the sum of the two halves, whose year averages to 2
        assert monthly.shape[0] == 12
        assert np.allclose(monthly.mean(axis=0), 2, atol=0.01)


def test_the_window_is_told_of_each_variable_and_analysis(daily, tmp_path, no_report):
    """For the oceanval window's grid of them (see oceanval.app._analysis_news)."""
    from oceanval import prompts

    told = []
    with prompts.told_by(told.append):
        oceanval.analyze(
            daily, {"Chlorophyll": "P1_Chl+P2_Chl", "Temperature": "thetao"},
            climatology={"Chlorophyll": {"start": 2013, "end": 2013, "surface": True}},
            phenology={"Chlorophyll": {"start": 2013, "end": 2013}},
            seasonal={"Temperature": {"start": 2013, "end": 2013, "surface": True}},
            n_dirs_down=1, out_dir=str(tmp_path), cores=1, view=False,
        )
    assert told[0] == {
        "kind": "analyses",
        "simulations": 1,
        "items": [
            {"key": "chlorophyll", "title": "Chlorophyll", "kinds": ["climatology", "phenology"]},
            {"key": "temperature", "title": "Temperature", "kinds": ["seasonal"]},
        ],
    }
    said = [
        (news["key"], news.get("analysis"), news["state"]) for news in told[1:]
    ]
    assert said == [
        ("chlorophyll", None, "working"),
        ("chlorophyll", "climatology", "working"),
        ("chlorophyll", "climatology", "done"),
        ("chlorophyll", "phenology", "working"),
        ("chlorophyll", "phenology", "done"),
        ("chlorophyll", None, "done"),
        ("temperature", None, "working"),
        ("temperature", "seasonal", "working"),
        ("temperature", "seasonal", "done"),
        ("temperature", None, "done"),
    ]
    assert {news["kind"] for news in told[1:]} == {"analysis_variable", "analysis"}

    def test_the_gaps_in_the_output_are_kept(self, monthly, tmp_path, no_report):
        oceanval.analyze(
            monthly,
            {"Chlorophyll": "P1_Chl+P2_Chl"},
            climatology={"Chlorophyll": {"start": 2013, "end": 2014, "surface": True}},
            n_dirs_down=1,
            out_dir=str(tmp_path),
            cores=1, view=False,
        )
        dates = no_report[0]["variables"][0]["climatology"]["dates"]
        assert dates["gaps"] is True
        assert dates["issues"] == ["1 month with no output: Mar 2014."]
        assert no_report[0]["variables"][0]["time_res"] == "monthly"

    def test_times_at_the_end_of_each_mean_are_shifted(self, tmp_path, no_report):
        sim = tmp_path / "sim"
        for year in (2013, 2014):
            os.makedirs(sim / str(year))
            # each month's mean dated 00:00 on the 1st of the next, as GETM dates them
            times = [np.datetime64(f"{year + month // 12}-{month % 12 + 1:02d}-01") for month in range(1, 13)]
            xr.Dataset(
                {"temp": (("time", "lat", "lon"), np.arange(1.0, 13.0)[:, None, None] * np.ones((12, 2, 2)),
                          {"units": "degC", "cell_methods": "time: mean"})},
                coords={"time": times, "lat": ("lat", [50.0, 51.0], {"units": "degrees_north"}),
                        "lon": ("lon", [0.0, 1.0], {"units": "degrees_east"})},
            ).to_netcdf(sim / str(year) / f"getm_{year}.nc")
        out = tmp_path / "out"
        oceanval.analyze(
            str(sim), {"Temperature": "temp"},
            climatology={"Temperature": {"start": 2013, "end": 2014, "surface": True}},
            n_dirs_down=1, time_shift=-1, out_dir=str(out), cores=1, view=False,
        )
        monthly = values(os.path.join(results(str(out)), "temperature_surface_monthly.nc"), "temperature")
        # January's mean, dated 1 February, is January's again
        assert np.allclose(monthly[:, 0], np.arange(1.0, 13.0))
        assert no_report[0]["time_shift"] == -1

    def test_the_water_column_needs_levels(self, daily, tmp_path, no_report):
        with pytest.raises(ValueError, match="only one vertical level"):
            oceanval.analyze(
                daily, {"Chlorophyll": "P1_Chl"},
                climatology={"Chlorophyll": {"start": 2013, "end": 2014, "water_column": "mean"}},
                n_dirs_down=1, thickness="e3t", out_dir=str(tmp_path), cores=1, view=False,
            )
        assert not os.path.exists(os.path.join(str(tmp_path), analysis.FOLDER))

    @pytest.mark.parametrize("start, end", [(2012, 2014), (2013, 2015), (2012, 2015)])
    def test_years_that_stick_out_of_the_output_are_refused(self, daily, tmp_path, no_report, start, end):
        with pytest.raises(ValueError, match="output is from 2013 to 2014, so the years of its climatology"):
            oceanval.analyze(
                daily, {"Temperature": "thetao"},
                climatology={"Temperature": {"start": start, "end": end, "surface": True}},
                n_dirs_down=1, out_dir=str(tmp_path), cores=1, view=False,
            )
        assert not os.path.exists(os.path.join(str(tmp_path), analysis.FOLDER))

    def test_years_without_output_are_refused(self, daily, tmp_path, no_report):
        with pytest.raises(ValueError, match="no output in 1990-1991"):
            oceanval.analyze(
                daily, {"Temperature": "thetao"},
                climatology={"Temperature": {"start": 1990, "end": 1991, "surface": True}},
                n_dirs_down=1, out_dir=str(tmp_path), cores=1, view=False,
            )

    def test_an_analysis_replaces_the_last_and_nothing_else(self, daily, tmp_path, no_report):
        out = tmp_path / "out"
        os.makedirs(out / analysis.FOLDER / "old")
        (out / "keep.txt").write_text("mine")
        os.makedirs(out / "oceanval_matchups")
        oceanval.analyze(
            daily, {"Temperature": "thetao"},
            climatology={"Temperature": {"start": 2013, "end": 2013, "surface": True}},
            n_dirs_down=1, out_dir=str(out), cores=1, view=False,
        )
        assert not os.path.exists(out / analysis.FOLDER / "old")
        assert (out / "keep.txt").read_text() == "mine"
        assert os.path.isdir(out / "oceanval_matchups")


class TestPhenology:
    def test_the_day_of_the_peak(self, daily, tmp_path, no_report):
        oceanval.analyze(
            daily, {"Chlorophyll": "P1_Chl+P2_Chl"},
            phenology={"Chlorophyll": {"start": 2013, "end": 2014}},
            n_dirs_down=1, out_dir=str(tmp_path), cores=1, view=False,
        )
        found = results(str(tmp_path))
        with xr.open_dataset(os.path.join(found, "chlorophyll_phenology.nc")) as ds:
            days = ds[analysis.PEAK_DAY].values
            peak = ds[analysis.PEAK_VALUE].values
            assert int(ds.attrs["days"]) == 365
            assert list(ds.attrs["month_starts"]) == [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335]
        assert [set(row) for row in days] == [{analysis_peak_day(row)} for row in range(days.shape[0])]
        assert np.allclose(peak, 3)
        assert no_report[0]["variables"][0]["phenology"]["days"] == 365
        assert os.path.exists(os.path.join(found, "chlorophyll_surface_daily.nc"))

    def test_a_360_day_calendar(self, tmp_path, no_report):
        sim = str(tmp_path / "sim")
        write_analysis_simulation(sim, years=(2013,), calendar="360_day")
        oceanval.analyze(
            sim, {"Chlorophyll": "P1_Chl"},
            phenology={"Chlorophyll": {"start": 2013, "end": 2013}},
            n_dirs_down=1, out_dir=str(tmp_path / "out"), cores=1, view=False,
        )
        with xr.open_dataset(os.path.join(results(str(tmp_path / "out")), "chlorophyll_phenology.nc")) as ds:
            assert int(ds.attrs["days"]) == 360
            # 30 days in every month
            assert list(ds.attrs["month_starts"]) == [1 + 30 * month for month in range(12)]
            days = ds[analysis.PEAK_DAY].values
        assert [set(row) for row in days] == [{analysis_peak_day(row)} for row in range(days.shape[0])]

    def test_29_february_is_in_its_place(self, tmp_path):
        # CDO dates each day of a daily climatology in the last year that has
        # it, so 29 February comes first, from an earlier leap year
        days = [cftime.datetime(2000, 2, 29)] + [
            cftime.datetime(2003, 1, 1) + datetime.timedelta(days=day) for day in range(365)
        ]
        units = "days since 2000-01-01"
        peak_at = 59  # 28 February, day 59 of a leap year
        data = np.zeros((366, 1, 2))
        data[1 + peak_at - 1] = 5
        path = str(tmp_path / "daily.nc")
        xr.Dataset(
            {"chl": (("time", "lat", "lon"), data)},
            coords={"time": ("time", cftime.date2num(days, units, "standard"), {"units": units, "calendar": "standard"}),
                    "lat": [50.0], "lon": [0.0, 1.0]},
        ).to_netcdf(path)
        order, month_days = analysis.day_order(path, "chl")
        assert month_days[58:61] == [(2, 28), (2, 29), (3, 1)]
        assert order[59] == 0
        analysis.peak(path, "chl", str(tmp_path / "peak.nc"))
        with xr.open_dataset(str(tmp_path / "peak.nc")) as ds:
            assert ds[analysis.PEAK_DAY].values.ravel().tolist() == [59, 59]
            assert list(ds.attrs["month_starts"])[2] == 61

    def test_a_cell_with_no_values_has_no_peak(self, tmp_path):
        units = "days since 2003-01-01"
        data = np.random.rand(365, 1, 2)
        data[:, 0, 1] = np.nan
        path = str(tmp_path / "daily.nc")
        xr.Dataset(
            {"chl": (("time", "lat", "lon"), data)},
            coords={"time": ("time", np.arange(365.0), {"units": units, "calendar": "standard"}),
                    "lat": [50.0], "lon": [0.0, 1.0]},
        ).to_netcdf(path)
        analysis.peak(path, "chl", str(tmp_path / "peak.nc"))
        with xr.open_dataset(str(tmp_path / "peak.nc")) as ds:
            found = ds[analysis.PEAK_DAY].values.ravel()
        assert found[0] == np.argmax(data[:, 0, 0]) + 1
        assert np.isnan(found[1])

    def test_it_needs_daily_output(self, monthly, tmp_path, no_report):
        with pytest.raises(ValueError, match="needs daily output.*is monthly"):
            oceanval.analyze(
                monthly, {"Chlorophyll": "P1_Chl"},
                phenology={"Chlorophyll": {"start": 2013, "end": 2014}},
                n_dirs_down=1, out_dir=str(tmp_path), cores=1, view=False,
            )


def test_the_report(daily, tmp_path):
    """The report, built as validate() builds its own, with a page for each
    variable and the overview first."""
    out = str(tmp_path)
    landing = oceanval.analyze(
        daily, {"Chlorophyll": "P1_Chl+P2_Chl", "Temperature": "thetao"},
        climatology={"Temperature": {"start": 2013, "end": 2014, "surface": True, "water_column": "mean"}},
        phenology={"Chlorophyll": {"start": 2013, "end": 2014}},
        n_dirs_down=1, thickness="e3t", out_dir=out, cores=1, view=False,
    )
    folder = os.path.join(out, analysis.FOLDER)
    pages = os.path.join(folder, analysis.REPORT, "_build", "html", "notebooks")
    assert landing == os.path.join(pages, "000_overview.html")
    assert os.path.realpath(os.path.join(folder, analysis.REPORT_LINK)) == os.path.realpath(landing)
    assert sorted(os.path.basename(x) for x in glob.glob(os.path.join(pages, "*.html"))) == [
        "000_overview.html", "001_chlorophyll.html", "002_temperature.html",
    ]
    overview = open(landing).read()
    assert "Gaps in the output" in overview and "Methods" in overview
    chlorophyll = open(os.path.join(pages, "001_chlorophyll.html")).read()
    # only what was chosen: phenology, but no climatology
    assert "Phenology" in chlorophyll and "Climatology" not in chlorophyll
    assert "Day of the year of the seasonal peak" in chlorophyll and "Figure 3" in chlorophyll
    temperature = open(os.path.join(pages, "002_temperature.html")).read()
    assert "Water column: vertical mean" in temperature and "Phenology" not in temperature
    # figures are numbered from 1 on each page, as on validate's
    assert "Figure 1" in temperature and "Figure 4" in temperature and "Figure 5" not in temperature
    assert "Traceback" not in overview + chlorophyll + temperature
    # no code to reproduce the figures unless it is asked for
    assert "oceanval-copy-code" not in chlorophyll + temperature
    assert json.load(open(os.path.join(folder, analysis.RESULTS, analysis.INFO_FILE)))["figure_code"] is False


def test_the_report_with_code_to_reproduce_its_figures(daily, tmp_path):
    """Each figure has a button to copy the code that draws it, from the
    results where they are."""
    out = str(tmp_path)
    landing = oceanval.analyze(
        daily, {"Chlorophyll": "P1_Chl+P2_Chl"},
        climatology={"Chlorophyll": {"start": 2013, "end": 2014, "surface": True}},
        phenology={"Chlorophyll": {"start": 2013, "end": 2014}},
        n_dirs_down=1, out_dir=out, cores=1, view=False, figure_code=True,
    )
    page = open(os.path.join(os.path.dirname(landing), "001_chlorophyll.html")).read()
    assert "Traceback" not in page
    figures = sorted(set(int(n) for n in re.findall(r"Figure (\d+)</strong>", page)))
    # the surface's annual and monthly maps, and the phenology's three
    assert figures == list(range(1, 6))
    assert page.count('class="oceanval-copy-code"') == 5
    results = os.path.join(out, analysis.FOLDER, analysis.RESULTS)
    assert f"chlorophyll_surface_annual.nc" in page and results in page
    assert "# Figure 1 on the Chlorophyll page of the OceanVal analysis of" in page
    # the overview has no figures
    assert "oceanval-copy-code" not in open(landing).read()


@pytest.mark.skipif(not oceanval.transects.available(), reason="nctoolkit cannot extract transects")
def test_the_report_with_transects(tmp_path):
    """A heading for each transect on the variable's page, each with a map of
    it and a figure of each thing worked out along it."""
    root = str(tmp_path / "sim")
    write_analysis_simulation(root, years=(2010, 2011, 2012, 2013), daily=False, trend=1.0)
    years = {"start": 2010, "end": 2013}
    lines = [
        {"start": [-2.0, 50.0], "end": [-2.0, 53.0], "climatology": years, "trends": dict(years, how="absolute")},
        {"start": [-4.0, 51.0], "end": [0.0, 51.0], "vertical": years, "vertical_trends": dict(years, how="absolute_change")},
    ]
    landing = oceanval.analyze(
        root, {"Temperature": "thetao"}, transects={"Temperature": lines}, thickness="e3t",
        n_dirs_down=1, out_dir=str(tmp_path / "out"), cores=1, view=False,
    )
    overview = open(landing).read()
    page = open(os.path.join(os.path.dirname(landing), "001_temperature.html")).read()
    assert "Traceback" not in overview + page
    assert "Transect 1: north–south along 2°W, from 50°N to 53°N" in page
    assert "Transect 2: east–west along 51°N, from 4°W to 0°" in page
    assert "Surface climatology" in page and "Surface changes: absolute" in page and "Vertical climatology" in page
    assert "Vertical changes: absolute change" in page
    # a map for each, and a figure for each option of each
    assert sorted(set(int(n) for n in re.findall(r"Figure (\d+)</strong>", page))) == [1, 2, 3, 4, 5, 6]
    assert "Transects" in overview and "to_transect" in overview
    assert "oceanval-copy-code" not in page


class TestTheSimulationsOfAComparison:
    """analyze() of one of the simulations tests/test_sim_compare.py compares,
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

    def test_a_transect_of_every_kind_of_b(self, analysed_transects_b):
        found, info = analysed_transects_b
        options = info["variables"][0]["transects"]["lines"][0]["options"]
        assert set(options) == {"climatology", "vertical", "trends"}
        frames = {name: pd.read_csv(os.path.join(found, option["csv"])) for name, option in options.items()}
        for month, value in zip(frames["climatology"].month, frames["climatology"].value):
            assert value == pytest.approx(ANALYSIS_LEVEL_VALUES[0] + month + TREND_B, abs=1e-4)
        for _, part in frames["trends"].groupby("position"):
            assert part.sort_values("year").value.diff().dropna().to_numpy() == pytest.approx(TREND_B)


class TestForms:
    def test_the_simulation_needs_no_domain_or_years(self, daily, tmp_path):
        form = dict(analysis_forms.default_setup_form(str(tmp_path)), simdir=daily, ndown="1")
        arguments, errors = analysis_forms.check_setup(form, str(tmp_path))
        assert errors == {}
        assert arguments["simdir"] == daily
        # where the output is saved is asked last (see check_output)
        assert not {"domain", "start", "end", "overwrite", "previous", "out_dir", "out"} & set(arguments)
        paths, errors = analysis_forms.check_output(form, str(tmp_path))
        assert errors == {}
        assert paths == {"out_dir": str(tmp_path), "out": os.path.join(str(tmp_path), "analysis.py")}

    def test_what_oceanval_could_fill_in(self, daily):
        rows = analysis_forms.prefill(analysis.streams(daily, 1))
        assert {"name": "Temperature", "model_variable": "thetao", "filled": True} in rows
        assert all(row["filled"] for row in rows)

    def test_the_names_the_drop_down_offers(self, daily):
        presets = analysis_forms.presets(analysis.streams(daily, 1))
        by_name = {preset["name"]: preset["model_variable"] for preset in presets}
        # every variable the recipes know, found as the matchups find it, or None
        assert by_name["Temperature"] == "thetao"
        assert by_name["Chlorophyll"] == "P1_Chl+P2_Chl"
        assert by_name["Salinity"] is None
        assert [preset["name"] for preset in presets] == sorted(by_name, key=str.lower)
        assert {"pH", "Nitrate", "Oxygen"} <= set(by_name)

    def test_what_the_page_lists(self, daily):
        picker = analysis_forms.picker_state(analysis.streams(daily, 1))
        assert picker["available"] == ["P1_Chl", "P2_Chl", "e3t", "thetao"]
        assert picker["levels"]["thetao"] == 3 and picker["levels"]["P1_Chl"] == 1

    def test_the_variables_are_checked(self, daily):
        found = analysis.streams(daily, 1)
        rows = analysis_forms.clean_rows([
            {"name": "Chlorophyll", "model_variable": "P1_Chl + P2_Chl"},
            {"name": "", "model_variable": ""},
            {"name": "chlorophyll", "model_variable": "P1_Chl"},
            {"name": "Odd", "model_variable": "P1_Chl+thetao"},
            {"name": "", "model_variable": "thetao"},
        ])
        placed, errors = analysis_forms.check_rows(rows, found)
        assert [row["name"] for row in placed] == ["Chlorophyll"]
        assert placed[0]["pattern"] == "sim_1d_**_**_ptrc_T.nc"
        assert placed[0]["model_variable"] == "P1_Chl+P2_Chl"
        assert set(errors) == {"2", "3", "4"}
        assert "same name" in errors["2"]["name"]
        assert "not in the same files" in errors["3"]["model_variable"]
        assert errors["4"] == {"name": "Give this variable a name."}
        assert analysis_forms.check_rows([], found) == ([], {"rows": "Add a variable to analyse."})

    def placed(self, daily):
        found = analysis.streams(daily, 1)
        rows = analysis_forms.clean_rows([
            {"name": "Chlorophyll", "model_variable": "P1_Chl+P2_Chl"},
            {"name": "Temperature", "model_variable": "thetao"},
        ])
        return analysis_forms.check_rows(rows, found)[0]

    def test_the_climatology_is_checked(self, daily, tmp_path):
        placed = self.placed(daily)
        years = lambda name: (2013, 2014)
        form = analysis_forms.clean_climatology({
            "rows": {
                "Chlorophyll": {"use": True, "start": "2013", "end": "2014", "surface": True, "water_column": True},
                "Temperature": {"use": True, "start": "2014", "end": "2013", "surface": True},
            },
        }, placed)
        chosen, thickness, errors = analysis_forms.check_climatology(form, placed, years, {"e3t"}, str(tmp_path))
        assert errors == {
            "Chlorophyll": {"water_column": "It has only one vertical level."},
            "Temperature": {"end": "End must not be before start."},
        }
        # nor has it a bottom
        form["rows"]["Chlorophyll"].update(water_column=False, bottom=True)
        form["rows"]["Temperature"].update(end="2014")
        _, _, errors = analysis_forms.check_climatology(form, placed, years, {"e3t"}, str(tmp_path))
        assert errors == {"Chlorophyll": {"bottom": "It has only one vertical level."}}
        # a layer must be ticked
        form["rows"]["Chlorophyll"].update(surface=False, bottom=False)
        _, _, errors = analysis_forms.check_climatology(form, placed, years, {"e3t"}, str(tmp_path))
        assert "Tick the surface, the bottom, the water column" in errors["Chlorophyll"]["surface"]
        form["rows"]["Chlorophyll"]["use"] = False
        form["rows"]["Temperature"].update(bottom=True)
        chosen, _, errors = analysis_forms.check_climatology(form, placed, years, {"e3t"}, str(tmp_path))
        assert errors == {} and chosen == {
            "Temperature": {"start": 2014, "end": 2014, "surface": True, "bottom": True, "water_column": None}
        }
        form = analysis_forms.clean_climatology({
            "rows": {"Temperature": {"use": True, "start": "2013", "end": "2014", "water_column": True, "how": "integral"}},
            "thickness": "",
        }, placed)
        assert "thickness" in analysis_forms.check_climatology(form, placed, years, {"e3t"}, str(tmp_path))[2]
        form["thickness"] = "e3t"
        chosen, thickness, errors = analysis_forms.check_climatology(form, placed, years, {"e3t"}, str(tmp_path))
        assert errors == {}
        assert chosen == {
            "Temperature": {"start": 2013, "end": 2014, "surface": False, "bottom": False, "water_column": "integral"}
        }
        assert thickness == "e3t"
        form["thickness"] = "nothing"
        assert analysis_forms.check_climatology(form, placed, years, {"e3t"}, str(tmp_path))[2] == {
            "thickness": "This is not a variable in the output, nor a file."
        }
        form["rows"]["Temperature"].update(start="1990", end="1991")
        form["thickness"] = analysis.Z_LEVEL
        assert analysis_forms.check_climatology(form, placed, years, set(), str(tmp_path))[2] == {
            "Temperature": {"start": "The output is from 2013 to 2014."}
        }
        # years the output starts after or ends before are refused, each in its own box
        for start, end, expected in [
            ("2012", "2014", {"start": "The output starts in 2013."}),
            ("2013", "2015", {"end": "The output ends in 2014."}),
            ("2012", "2015", {"start": "The output starts in 2013.", "end": "The output ends in 2014."}),
        ]:
            form["rows"]["Temperature"].update(start=start, end=end)
            assert analysis_forms.check_climatology(form, placed, years, set(), str(tmp_path))[2] == {
                "Temperature": expected
            }
        # the output's own years are fine
        form["rows"]["Temperature"].update(start="2013", end="2014")
        assert analysis_forms.check_climatology(form, placed, years, set(), str(tmp_path))[2] == {}

    def test_nothing_ticked_is_an_error(self, daily, tmp_path):
        placed = self.placed(daily)
        rows = {"Chlorophyll": {"use": False, "start": "2013", "end": "2014"}}
        form = analysis_forms.clean_climatology({"rows": rows}, placed)
        errors = analysis_forms.check_climatology(form, placed, lambda name: (2013, 2014), set(), str(tmp_path))[2]
        assert "Surface, Bottom, Water column, or more than one" in errors["rows"]
        form = analysis_forms.clean_phenology({"rows": rows}, placed)
        errors = analysis_forms.check_phenology(form, placed, lambda name: (2013, 2014), {}.get)[1]
        assert "at least one variable" in errors["rows"]

    def test_the_phenology_needs_daily_output(self, daily):
        placed = self.placed(daily)
        form = analysis_forms.clean_phenology({
            "rows": {"Chlorophyll": {"use": True, "start": "2013", "end": "2014"}, "Temperature": {"use": True, "start": "2013", "end": "2014"}},
        }, placed)
        res = {"Chlorophyll": "1d", "Temperature": "monthly"}
        chosen, errors = analysis_forms.check_phenology(form, placed, lambda name: (2013, 2014), res.get)
        assert chosen == {"Chlorophyll": {"start": 2013, "end": 2014}}
        assert errors == {"Temperature": {"use": "Its output is not daily."}}

    def test_the_forms_start_with_nothing_ticked(self, daily):
        placed = self.placed(daily)
        climatology = analysis_forms.default_climatology(placed, lambda name: (2013, 2014))
        assert climatology["rows"]["Temperature"] == {
            "use": False, "start": "", "end": "", "surface": False, "bottom": False, "water_column": False, "how": "",
            "window": "1", "show": "absolute",
        }
        res = {"Chlorophyll": "1d", "Temperature": "monthly"}
        phenology = analysis_forms.default_phenology(placed, lambda name: (2013, 2014), res.get)
        # only a variable with daily output can have its phenology
        assert {name: row["use"] for name, row in phenology["rows"].items()} == {"Chlorophyll": False, "Temperature": False}
        assert all(row["start"] == "" and row["end"] == "" for row in phenology["rows"].values())

    def test_the_water_column_needs_a_way_to_take_it(self, daily):
        placed = self.placed(daily)
        form = analysis_forms.clean_climatology(
            {"rows": {"Temperature": {"use": True, "start": "2013", "end": "2014", "water_column": True}},
             "thickness": "e3t"},
            placed,
        )
        _, _, errors = analysis_forms.check_climatology(form, placed, lambda name: (2013, 2014), {"e3t"}, "/")
        assert errors == {"Temperature": {"water_column": "Choose the vertical mean or the depth integral."}}

    def test_cores_and_missing_values_have_no_default(self, monkeypatch):
        # the same machine wherever the test runs (a macos-latest runner has 3 cores)
        monkeypatch.setattr(analysis_forms.os, "cpu_count", lambda: 8)
        assert analysis_forms.check_cores("") == (None, "Say how many cores OceanVal may use.")
        assert analysis_forms.check_cores("0")[1] and analysis_forms.check_cores("two")[1]
        assert analysis_forms.check_cores(" 4 ") == (4, None)
        assert analysis_forms.check_cores("9") == (None, "This machine has 8 cores. Choose 8 or fewer.")
        assert analysis_forms.check_missing("", "") == (None, {})
        assert analysis_forms.check_missing("0", "") == (0.0, {})
        assert analysis_forms.check_missing("0", "0") == ([0.0, 0.0], {})
        assert analysis_forms.check_missing("", "1") == (None, {"missing_from": "Fill in the value, or the start of a range."})
        assert analysis_forms.check_missing("5", "1")[1] == {
            "missing_to": "The end of the range must not be less than its start."
        }

    def test_the_figure_code_is_yes_or_no_and_not_answering_is_no(self):
        assert analysis_forms.check_figure_code("yes") == (True, None)
        assert analysis_forms.check_figure_code("No") == (False, None)
        for choice in ("", None):
            assert analysis_forms.check_figure_code(choice) == (False, None)
        assert analysis_forms.check_figure_code("maybe") == (
            None, "Say whether the report should have code to reproduce its figures."
        )

    def test_the_script_calls_analyze(self, monkeypatch):
        arguments = analysis_forms.arguments(
            {"simdir": "/sim", "ndown": 2, "exclude": ["restart"], "require": None, "out_dir": "/out"},
            [{"name": "Chlorophyll", "model_variable": "P1_Chl+P2_Chl"}, {"name": "Unused", "model_variable": "x"}],
            {"Chlorophyll": {"start": 2000, "end": 2009, "surface": True, "water_column": None}},
            {"Chlorophyll": {"start": 2000, "end": 2009}},
            None,
            -1,
            figure_code=True,
        )
        text = analysis_forms.script_text(arguments)
        ast.parse(text)
        called = []
        monkeypatch.setattr(oceanval, "analyze", lambda **given: called.append(given))
        exec(compile(text, "analysis.py", "exec"), {})
        assert called == [arguments]
        assert called[0]["variables"] == {"Chlorophyll": "P1_Chl+P2_Chl"}
        assert called[0]["time_shift"] == -1
        assert called[0]["figure_code"] is True

    def test_figure_code_is_true_or_false(self, tmp_path):
        with pytest.raises(ValueError, match="figure_code must be True or False"):
            oceanval.analyze(str(tmp_path), {"Temperature": "thetao"}, cores=1, figure_code="yes")
