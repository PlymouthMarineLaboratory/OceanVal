"""oceanval.surface: the surface read from the first level, with a small chunk cache, and used by matchup too."""

import glob
import os

import nctoolkit as nc
import numpy as np
import pandas as pd
import pytest
import xarray as xr

from oceanval import gridded, matchall, surface
from oceanval.session import session_info
from simulations import ANALYSIS_LATS, ANALYSIS_LONS, write_analysis_simulation, write_month_files

DAYS = [(2013, 1, 5), (2013, 3, 20), (2013, 7, 11)]


def write_pair(path, positive="down", compress=False, hole=False, days=6):
    """A daily file of two 3D variables a and b (a is 1 plus the level, b 10 times that), on a small lon-lat grid."""
    levels = [5.0, 10.0, 20.0]
    values = np.arange(1, len(levels) + 1, dtype="f4")[None, :, None, None] * np.ones((days, 1, 2, 4), dtype="f4")
    a, b = values.copy(), values.copy() * 10
    if hole:
        a[:, 0, 0, 0] = np.nan
        a[:, 1, 0, 0] = 7
    attrs = {"units": "m", "axis": "Z"}
    if positive:
        attrs["positive"] = positive
    xr.Dataset(
        {"a": (("time", "deptht", "lat", "lon"), a), "b": (("time", "deptht", "lat", "lon"), b)},
        coords={
            "time": ("time", np.arange(days, dtype="f8"), {"units": "days since 2013-01-01", "calendar": "standard"}),
            "deptht": ("deptht", levels, attrs),
            "lat": ("lat", [50.0, 51.0], {"units": "degrees_north", "standard_name": "latitude"}),
            "lon": ("lon", [-3.0, -2.0, -1.0, 0.0], {"units": "degrees_east", "standard_name": "longitude"}),
        },
    ).to_netcdf(
        path,
        encoding={
            name: {"_FillValue": np.float32(-9999), "zlib": compress, "chunksizes": (1, 3, 2, 4)} for name in ("a", "b")
        },
    )
    return path


class TestChoose:
    @staticmethod
    def history(monkeypatch, path, names, fast, timesteps):
        monkeypatch.setattr(nc.DataSet, "run", lambda self: None)
        ds = nc.open_data(path, checks=False)
        surface.choose(ds, names, fast, timesteps)
        return ds.history[-1]

    def test_the_surface_and_the_days_are_one_select_then_a_surface_axis(self, tmp_path, monkeypatch):
        command = self.history(monkeypatch, write_pair(str(tmp_path / "f.nc")), ["a", "b"], True, [0, 2, 4])
        assert "-select,name=a,b,levidx=1,timestep=1,3,5" in command
        assert command.index("-setzaxis") < command.index("-select")
        assert "selname" not in command and "seltimestep" not in command and "topvalue" not in command

    def test_without_the_surface_it_is_the_days_and_the_variables(self, tmp_path, monkeypatch):
        command = self.history(monkeypatch, write_pair(str(tmp_path / "f.nc")), ["a"], False, [1])
        assert "-select,name=a,timestep=2" in command and "levidx" not in command and "setzaxis" not in command

    def test_with_neither_it_is_the_variables(self, tmp_path, monkeypatch):
        command = self.history(monkeypatch, write_pair(str(tmp_path / "f.nc")), ["a"], False, None)
        assert "-selname,a" in command

    def test_very_many_days_are_chosen_after_the_select(self, tmp_path, monkeypatch):
        steps = list(range(surface.MOST_TIMESTEPS + 1))
        command = self.history(monkeypatch, write_pair(str(tmp_path / "f.nc")), ["a"], True, steps)
        assert "timestep=" not in command.split("-seltimestep")[-1] and "-seltimestep" in command and "levidx=1" in command

    @pytest.mark.parametrize("name", surface.CACHE_VARIABLES)
    def test_the_chunk_cache_is_set_and_restored(self, monkeypatch, name):
        """Under both names: CDI_CHUNK_CACHE before CDO 2.6, CDI_CACHESIZE_IN from 2.6 on."""
        other = [each for each in surface.CACHE_VARIABLES if each != name]
        for each in other:
            monkeypatch.delenv(each, raising=False)
        monkeypatch.setenv(name, "7")
        with surface.chunk_cache(True):
            assert all(os.environ[each] == str(surface.CHUNK_CACHE) for each in surface.CACHE_VARIABLES)
        assert os.environ[name] == "7" and not any(each in os.environ for each in other)
        with surface.chunk_cache(False):
            assert os.environ[name] == "7" and not any(each in os.environ for each in other)
        monkeypatch.delenv(name)
        with surface.chunk_cache(True):
            assert all(each in os.environ for each in surface.CACHE_VARIABLES)
        assert not any(each in os.environ for each in surface.CACHE_VARIABLES)

    def test_cdo_is_asked_about_the_cache_under_both_names(self, tmp_path, monkeypatch):
        """CDO 2.6 renamed CDI_CHUNK_CACHE(_INFO) to CDI_CACHESIZE_IN and CDI_CACHE_INFO: the check gives both."""
        import subprocess

        given = {}

        def run(command, env, **kwargs):
            given.update(env)
            return subprocess.CompletedProcess(command, 0, "", f"cdi  cdf_set_var_chunk_cache: a: chunkCacheSize={env['CDI_CACHESIZE_IN']}")

        monkeypatch.setattr(surface.subprocess, "run", run)
        assert surface.cdo_takes_a_chunk_cache(write_pair(str(tmp_path / "f.nc")))
        assert all(given[name] == str(surface.CHUNK_CACHE) for name in surface.CACHE_VARIABLES)
        assert all(given[name] == "1" for name in surface.CACHE_INFO)


class TestFirstLevelIsSurface:
    NLEVELS = {"a": 3, "b": 3}

    def test_it_is_used_for_an_uncompressed_axis_positive_down(self, tmp_path):
        found = surface.first_level_is_surface(write_pair(str(tmp_path / "f.nc")), ["a", "b"], self.NLEVELS)
        assert found == (True, "")

    @pytest.mark.parametrize(
        "options, why",
        [
            ({"positive": "up"}, "not at the surface"),
            ({"positive": None}, "cannot be told"),
            ({"hole": True}, "missing at the first level"),
        ],
    )
    def test_it_is_not_otherwise(self, tmp_path, options, why):
        found, reason = surface.first_level_is_surface(write_pair(str(tmp_path / "f.nc"), **options), ["a"], self.NLEVELS)
        assert found is False and why in reason

    def test_the_small_cache_is_given_for_an_uncompressed_file(self, tmp_path):
        assert surface.small_cache_helps(write_pair(str(tmp_path / "f.nc")), ["a", "b"], self.NLEVELS) is True

    def test_a_compressed_file_is_read_from_its_first_level_with_hdf5s_own_cache(self, tmp_path):
        """A compressed chunk is read whole: a small cache would decompress it again for every level."""
        path = write_pair(str(tmp_path / "f.nc"), compress=True)
        assert surface.first_level_is_surface(path, ["a"], self.NLEVELS) == (True, "")
        assert surface.small_cache_helps(path, ["a"], self.NLEVELS) is False
        assert surface.compressed(path, ["a"], self.NLEVELS).startswith("a (zlib")

    def test_a_variable_with_one_level_is_not_looked_at_for_compression(self, tmp_path):
        path = write_pair(str(tmp_path / "f.nc"), compress=True)
        assert surface.compressed(path, ["a"], {"a": 1}) is None

    def test_a_cdo_without_a_chunk_cache_reads_the_first_level_with_hdf5s_own_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(surface, "cdo_takes_a_chunk_cache", lambda path: False)
        path = write_pair(str(tmp_path / "f.nc"))
        assert surface.first_level_is_surface(path, ["a"], self.NLEVELS) == (True, "")
        assert surface.small_cache_helps(path, ["a"], self.NLEVELS) is False

    def test_what_is_said(self):
        assert surface.how_read(True, "", True) == "the surface is read from the first level"
        assert "HDF5's own chunk cache" in surface.how_read(True, "", False)
        assert surface.how_read(False, "it is positive up", False).endswith("as it is positive up")

    def test_it_is_worked_out_once_for_a_file(self, tmp_path, monkeypatch):
        path = write_pair(str(tmp_path / "f.nc"))
        asked = []
        original = surface._first_level_is_surface
        monkeypatch.setattr(surface, "_first_level_is_surface", lambda *a: asked.append(a) or original(*a))
        for _ in range(3):
            assert surface.first_level_is_surface(path, ["a"], self.NLEVELS) == (True, "")
        assert len(asked) == 1


@pytest.fixture
def point_session(monkeypatch):
    for key, value in {"cache": False, "time_shift": 0, "invert": False, "as_missing": None}.items():
        monkeypatch.setitem(session_info, key, value)


@pytest.fixture(scope="module")
def daily_simulation(tmp_path_factory):
    root = str(tmp_path_factory.mktemp("surface") / "sim")
    write_analysis_simulation(root, years=(2013,), daily=True)
    return glob.glob(os.path.join(root, "2013", "*grid_T.nc"))[0]


def observations(path):
    """Three points on three days."""
    doy = [pd.Timestamp(*day).dayofyear - 1 for day in DAYS]
    df_times = pd.DataFrame(
        {
            "path": path,
            "time_index": doy,
            "year": [d[0] for d in DAYS],
            "month": [d[1] for d in DAYS],
            "day": [d[2] for d in DAYS],
        }
    )
    df = pd.DataFrame(
        {
            "lon": [ANALYSIS_LONS[1], ANALYSIS_LONS[2], ANALYSIS_LONS[3]],
            "lat": [ANALYSIS_LATS[1], ANALYSIS_LATS[0], ANALYSIS_LATS[2]],
            "year": [d[0] for d in DAYS],
            "month": [d[1] for d in DAYS],
            "day": [d[2] for d in DAYS],
            "observation": [1.0, 2.0, 3.0],
        }
    )
    return df, df_times


def same(left, right):
    """Two matchups equal, whatever order the points came back in."""
    keys = ["year", "month", "day", "lon", "lat"]
    pd.testing.assert_frame_equal(
        left.sort_values(keys).reset_index(drop=True), right.sort_values(keys).reset_index(drop=True)
    )


class TestPointMatchups:
    @staticmethod
    def match(path, variable="thetao", layer="surface", fast=False, small_cache=None):
        df, df_times = observations(path)
        out = []
        small_cache = fast if small_cache is None else small_cache
        matchall.mm_match(path, variable, df, df_times, None, "temperature", out, layer, fast, small_cache)
        return out[0] if out else None

    def test_the_surface_is_the_same_read_from_the_first_level(self, daily_simulation, point_session):
        slow = self.match(daily_simulation)
        fast = self.match(daily_simulation, fast=True)
        same(slow, fast)
        # the surface of thetao is 10 plus the month
        assert sorted(slow.thetao) == [11.0, 13.0, 17.0]

    def test_the_commands_asked_for(self, daily_simulation, point_session, monkeypatch):
        asked = []
        original = nc.DataSet.cdo_command

        def spy(self, command, *a, **k):
            asked.append(command)
            return original(self, command, *a, **k)

        monkeypatch.setattr(nc.DataSet, "cdo_command", spy)
        self.match(daily_simulation, fast=True)
        steps = ",".join(str(pd.Timestamp(*day).dayofyear) for day in DAYS)
        assert asked[0] == f"-select,name=thetao,levidx=1,timestep={steps}"
        assert "topvalue" not in asked
        asked.clear()
        self.match(daily_simulation, fast=False)
        assert asked[0] == f"-select,name=thetao,timestep={steps}" and "topvalue" in asked

    def test_the_cache_is_small_only_while_the_surface_is_read_from_the_first_level(self, daily_simulation, point_session, monkeypatch):
        seen = []
        original = nc.DataSet.run

        def spy(self, *a, **k):
            seen.extend(os.environ.get(name) for name in surface.CACHE_VARIABLES)
            return original(self, *a, **k)

        monkeypatch.setattr(nc.DataSet, "run", spy)
        self.match(daily_simulation, fast=True)
        assert str(surface.CHUNK_CACHE) in seen
        seen.clear()
        self.match(daily_simulation, fast=False)
        assert str(surface.CHUNK_CACHE) not in seen
        # a compressed file's: read from the first level with HDF5's own cache
        self.match(daily_simulation, fast=True, small_cache=False)
        assert str(surface.CHUNK_CACHE) not in seen
        assert not any(name in os.environ for name in surface.CACHE_VARIABLES)

    def test_a_compressed_file_is_the_same_read_from_the_first_level(self, tmp_path, point_session, monkeypatch):
        path = write_pair(str(tmp_path / "pair.nc"), days=365, compress=True)
        fast, small_cache = matchall._surface_is_first_level(path, "a")
        assert (fast, small_cache) == (True, False)
        df, df_times = observations(path)
        df = df.assign(lon=[-3.0, -2.0, -1.0], lat=[50.0, 51.0, 50.0])
        found = []
        for fast in (False, True):
            out = []
            matchall.mm_match(path, "a", df, df_times, None, "x", out, "surface", fast, small_cache)
            found.append(out[0])
        same(found[0], found[1])
        assert list(found[0].iloc[:, -1]) == [1.0, 1.0, 1.0]

    def test_the_profile_layer_reads_only_the_days_asked_for_and_all_its_levels(self, daily_simulation, point_session, monkeypatch):
        asked = []
        original = nc.DataSet.cdo_command

        def spy(self, command, *a, **k):
            asked.append(command)
            return original(self, command, *a, **k)

        monkeypatch.setattr(nc.DataSet, "cdo_command", spy)
        # no depths to match to, so this gives each level
        profile = self.match(daily_simulation, layer="all", fast=True)
        steps = ",".join(str(pd.Timestamp(*day).dayofyear) for day in DAYS)
        assert asked[0] == f"-select,name=thetao,timestep={steps}"
        assert profile is not None

    def test_a_sum_of_variables_with_levels(self, tmp_path, point_session):
        path = write_pair(str(tmp_path / "pair.nc"), days=365)
        df, df_times = observations(path)
        df = df.assign(lon=[-3.0, -2.0, -1.0], lat=[50.0, 51.0, 50.0])
        found = []
        for fast in (False, True):
            out = []
            matchall.mm_match(path, "a+b", df, df_times, None, "x", out, "surface", fast)
            found.append(out[0])
        same(found[0], found[1])
        # the surface of a is 1 and of b 10
        assert list(found[0].iloc[:, -1]) == [11.0, 11.0, 11.0]

    def test_with_a_shift_and_a_missing_value(self, tmp_path, point_session, monkeypatch):
        monkeypatch.setitem(session_info, "time_shift", 1)
        monkeypatch.setitem(session_info, "as_missing", [-5, -5])
        path = write_pair(str(tmp_path / "pair.nc"), days=365)
        df, df_times = observations(path)
        df = df.assign(lon=[-3.0, -2.0, -1.0], lat=[50.0, 51.0, 50.0], day=[6, 21, 12])
        found = []
        for fast in (False, True):
            out = []
            matchall.mm_match(path, "a", df, df_times, None, "x", out, "surface", fast)
            found.append(out[0] if out else None)
        assert (found[0] is None) == (found[1] is None)
        if found[0] is not None:
            same(found[0], found[1])

    def test_it_is_decided_for_the_variable_from_its_first_file(self, daily_simulation, capsys):
        assert matchall._surface_is_first_level(daily_simulation, "thetao") == (True, True)
        assert "read from the first level" in capsys.readouterr().out
        # a variable with one level has no surface to find
        other = os.path.join(os.path.dirname(daily_simulation), os.path.basename(daily_simulation).replace("grid_T", "ptrc_T"))
        assert matchall._surface_is_first_level(other, "P1_Chl+P2_Chl") == (False, False)


class TestGriddedMatchups:
    @staticmethod
    def read(path, selection, years, shift=None, surface_only=True, as_missing=None):
        ds, fast, small_cache = gridded.model_dataset([path], selection, years, shift, as_missing, surface_only)
        with surface.chunk_cache(small_cache):
            ds.merge("time")
        ds.tmean(["year", "month"], align="left")
        ds.run()
        with xr.open_dataset(ds[0]) as out:
            return fast, out.load()

    @staticmethod
    def old(path, selection, years, shift=None, as_missing=None):
        """The chain as it was: selname, topvalue, then the shift."""
        ds = nc.open_data([path], checks=False)
        if shift:
            ds.shift(days=shift)
            ds.cdo_command(f"-selyear,{','.join(str(x) for x in years)}")
        else:
            ds.subset(years=years)
        ds.subset(variables=selection)
        ds.cdo_command("topvalue")
        if as_missing is not None:
            ds.as_missing(as_missing)
        ds.merge("time")
        ds.tmean(["year", "month"], align="left")
        ds.run()
        with xr.open_dataset(ds[0]) as out:
            return out.load()

    @pytest.mark.parametrize("shift", [None, 1])
    def test_the_surface_is_the_same_as_it_was(self, daily_simulation, shift):
        fast, new = self.read(daily_simulation, ["thetao"], [2013], shift)
        assert fast is True
        old = self.old(daily_simulation, ["thetao"], [2013], shift)
        assert new["thetao"].dims == old["thetao"].dims
        np.testing.assert_array_equal(new["thetao"].values, old["thetao"].values)

    def test_a_sum_and_a_missing_value(self, tmp_path):
        path = write_pair(str(tmp_path / "pair.nc"), days=365)
        fast, new = self.read(path, ["a", "b"], [2013], as_missing=[0, 0])
        assert fast is True
        old = self.old(path, ["a", "b"], [2013], as_missing=[0, 0])
        for name in ("a", "b"):
            np.testing.assert_array_equal(new[name].values, old[name].values)

    def test_a_compressed_file_is_read_from_its_first_level_with_hdf5s_own_cache(self, tmp_path, capsys, monkeypatch):
        path = write_pair(str(tmp_path / "pair.nc"), days=365, compress=True)
        seen = []
        original = nc.DataSet.run

        def spy(self, *a, **k):
            seen.extend(os.environ.get(name) for name in surface.CACHE_VARIABLES)
            return original(self, *a, **k)

        monkeypatch.setattr(nc.DataSet, "run", spy)
        fast, new = self.read(path, ["a"], [2013])
        assert fast is True
        assert "HDF5's own chunk cache" in capsys.readouterr().out
        assert str(surface.CHUNK_CACHE) not in seen
        np.testing.assert_array_equal(new["a"].values, self.old(path, ["a"], [2013])["a"].values)

    def test_all_the_levels_are_kept_for_a_vertical_comparison(self, tmp_path):
        path = write_pair(str(tmp_path / "pair.nc"), days=365)
        fast, new = self.read(path, ["a"], [2013], surface_only=False)
        assert fast is False and new["a"].sizes["deptht"] == 3

    @pytest.mark.parametrize("shift", [None, -1])
    def test_a_file_a_month_has_its_mean_taken_as_it_is_read(self, daily_simulation, tmp_path, shift):
        """With month_per_file, the chain ends with each file's mean, and the mean of each month is not taken
        again after the merge. It gives what that gives."""
        whole, split = write_month_files(
            daily_simulation, str(tmp_path / "whole"), str(tmp_path / "sim"), end_stamped=shift is not None
        )
        ds, fast, small_cache = gridded.model_dataset(split, ["thetao"], [2013], shift, [0, 0], True, month_per_file=True)
        assert "-timmean" in ds.history[-1]
        with surface.chunk_cache(small_cache):
            ds.merge("time")
        ds.run()
        with xr.open_dataset(ds[0]) as out:
            new = out.load()
        _, old = self.read(whole, ["thetao"], [2013], shift, as_missing=[0, 0])
        assert new.sizes["time"] == 12
        np.testing.assert_allclose(new["thetao"].values, old["thetao"].values, rtol=1e-6)
        np.testing.assert_array_equal(new["time"].values, old["time"].values)

    def test_the_variables_and_the_surface_are_chosen_before_the_shift(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nc.DataSet, "run", lambda self: None)
        path = write_pair(str(tmp_path / "pair.nc"), days=365)
        ds, fast, _ = gridded.model_dataset([path], ["a"], [2013], 1, [0, 0], True)
        command = ds.history[-1]
        assert fast is True
        places = [command.index(x) for x in ("-select,name=a,levidx=1", "-shifttime", "-selyear", "-setrtomiss")]
        assert "topvalue" not in command and "selname" not in command
        # CDO applies the rightmost first: the select, then the shift, the years, the missing value
        assert places == sorted(places, reverse=True), command
