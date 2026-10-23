"""The years a simulation's output covers, read from its files' times (oceanval.output_years)."""

import os
import random
import subprocess
import sys
import threading

import netCDF4
import numpy as np
import pytest

from oceanval import output_years, user_cache


def write_times(path, values, units="days since 2000-01-01", calendar=None, name="time_counter",
                fmt="NETCDF4", **attrs):
    """A file whose time axis, name, holds values, with a second variable
    measured in time as NEMO's time_centered is, and bounds."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with netCDF4.Dataset(path, "w", format=fmt) as handle:
        handle.createDimension(name, None)
        handle.createDimension("axis_nbounds", 2)
        handle.createDimension("x", 2)
        time = handle.createVariable(name, "f8", (name,), fill_value=attrs.pop("_FillValue", None))
        time.units = units
        if calendar:
            time.calendar = calendar
        for key, value in attrs.items():
            setattr(time, key, value)
        time[:] = np.asarray(values, dtype="f8")
        # not the time axis: its values are far from the axis's
        centred = handle.createVariable("time_centered", "f8", (name,))
        centred.units = "days since 1800-01-01"
        centred[:] = np.zeros(len(values))
        bounds = handle.createVariable(f"{name}_bounds", "f8", (name, "axis_nbounds"))
        bounds.units = units
        bounds[:] = np.zeros((len(values), 2))
        handle.createVariable("thetao", "f4", (name, "x"))[:] = np.ones((len(values), 2))
    return str(path)


def write_simulation(root, years, streams=("grid_T", "ptrc_T"), down=("{year}", "{month:02d}")):
    """A file a month of each stream, root/<year>/<month>/ by default, or
    every file in root with down=()."""
    paths = []
    for year in years:
        for month in (1, 6):
            folder = os.path.join(str(root), *[part.format(year=year, month=month) for part in down])
            for stream in streams:
                days = (year - 2000) * 365 + (month - 1) * 30
                paths.append(write_times(
                    os.path.join(folder, f"nemo_1m_{year}{month:02d}01_{stream}.nc"),
                    [days, days + 15], calendar="noleap",
                ))
    return paths


class TestTheTimesOfAFile:
    def test_nemo_output(self, tmp_path):
        # the coordinate variable, not time_centered or the bounds
        path = write_times(tmp_path / "a.nc", [0, 400], units="seconds since 2001-12-31", calendar="gregorian")
        assert output_years.file_years(path) == [2001, 2001]
        path = write_times(tmp_path / "b.nc", [0, 400], units="days since 2001-12-31", calendar="gregorian")
        assert output_years.file_years(path) == [2001, 2003]

    def test_a_360_day_calendar(self, tmp_path):
        path = write_times(tmp_path / "a.nc", [0, 359, 360, 720], calendar="360_day")
        assert output_years.file_years(path) == [2000, 2002]

    def test_netcdf3_is_read_with_netcdf4(self, tmp_path):
        path = write_times(tmp_path / "a.nc", [0, 800], fmt="NETCDF3_CLASSIC", name="time")
        assert output_years.file_years(path) == [2000, 2002]

    def test_without_h5py(self, tmp_path, monkeypatch):
        monkeypatch.setattr(output_years, "h5py", None)
        path = write_times(tmp_path / "a.nc", [0, 800])
        assert output_years.file_years(path) == [2000, 2002]

    def test_missing_times_are_left_out(self, tmp_path):
        path = write_times(tmp_path / "a.nc", [-1.0, 10.0, 800.0], _FillValue=-1.0)
        assert output_years.file_years(path) == [2000, 2002]

    def test_a_time_axis_found_by_its_attributes(self, tmp_path):
        path = str(tmp_path / "a.nc")
        with netCDF4.Dataset(path, "w") as handle:
            handle.createDimension("t", 2)
            # not a coordinate variable, so known by axis and standard_name
            time = handle.createVariable("date", "f8", ("t",))
            time.units, time.axis, time.standard_name = "days since 1990-01-01", "T", "time"
            time[:] = [0.0, 400.0]
            other = handle.createVariable("age", "f8", ("t",))
            other.units = "days since 1700-01-01"
            other[:] = [0.0, 1.0]
        assert output_years.file_years(path) == [1990, 1991]

    def test_no_time_axis(self, tmp_path):
        path = str(tmp_path / "grid.nc")
        with netCDF4.Dataset(path, "w") as handle:
            handle.createDimension("x", 2)
            handle.createVariable("e3t", "f4", ("x",))[:] = [1.0, 2.0]
        assert output_years.file_years(path) is None
        assert output_years.file_years(str(tmp_path / "nowhere.nc")) is None
        (tmp_path / "broken.nc").write_bytes(b"not netCDF")
        assert output_years.file_years(str(tmp_path / "broken.nc")) is None


class TestTheYearsOfASimulation:
    def years_of(self, streams, **kwargs):
        return output_years.years_of_streams(streams, rng=random.Random(1), **kwargs)

    def test_the_output_in_directories(self, tmp_path):
        paths = write_simulation(tmp_path, range(2001, 2009))
        found = self.years_of(output_years.group(paths))
        assert found["years"] == [2001, 2008]
        assert len(found["streams"]) == 2
        assert all(span == [2001, 2008] for span in found["streams"].values())
        assert found["dropped"] == [] and found["full"] == []

    def test_every_file_in_one_directory(self, tmp_path):
        paths = write_simulation(tmp_path, range(2001, 2009), down=())
        assert len(os.listdir(tmp_path)) == len(paths)
        assert self.years_of(output_years.group(paths))["years"] == [2001, 2008]

    def test_only_some_files_are_read(self):
        streams = {"a": [f"a{n}" for n in range(500)], "b": [f"b{n}" for n in range(500)]}
        read = []

        def years(path):
            read.append(path)
            return [2000, 2000]

        found = self.years_of(streams, read=years)
        assert found["years"] == [2000, 2000]
        # the first and last of each, and the check
        assert len(read) == found["read"] == 4 + output_years.SPOT_CHECKS

    def test_numbers_are_in_order(self):
        # by name, a10 comes before a9, but it is the later file
        streams = {"a": [f"a{n}" for n in range(1, 11)]}
        years = {f"a{n}": [2000 + n, 2000 + n] for n in range(1, 11)}
        found = self.years_of(streams, read=years.get, checks=0)
        assert found["years"] == [2001, 2010]

    def test_the_modification_times_are_used_too(self):
        # the names say nothing of the order, which the files were written in
        names = [f"run_{letter}.nc" for letter in "qwertyuiopasdfgh"]
        years = {name: [1990 + number, 1990 + number] for number, name in enumerate(names)}
        mtimes = {name: number for number, name in enumerate(names)}
        found = self.years_of({"run": names}, mtimes=mtimes, read=years.get, checks=0)
        assert found["years"] == [1990, 2005]

    def test_a_stream_out_of_order_is_read_in_full(self):
        # neither the names nor the modification times follow the times
        names = [f"f{n:03d}" for n in range(100)]
        order = list(range(100))
        random.Random(3).shuffle(order)
        years = {name: [1900 + year, 1900 + year] for name, year in zip(names, order)}
        found = self.years_of({"f": names}, read=years.get)
        assert found["full"] == ["f"]
        assert found["years"] == [1900, 1999]
        assert found["read"] == 100

    def test_files_beside_the_output_are_left_out_unread(self):
        # NEMO_ERSEM_MIZER_archer_W80_F08: a month a file from 1981 to 2017, beside an
        # offline series running to 2022, the grids two validations saved, and
        # series whose time axis is broken
        months = [(year, month) for year in range(1981, 2018) for month in range(1, 13)]
        streams = {
            f"amm7_1d_{stream}": [f"{year}/{month:02d}/amm7_1d_{year}{month:02d}_{stream}.nc" for year, month in months]
            for stream in ("grid_T", "ptrc_T")
        }
        streams.update({
            "offline_MIZER_timeseries.nc": ["post/offline_MIZER_timeseries.nc"],
            "e3t.nc": ["ecoval/e3t.nc", "old_ecoval/e3t.nc"],
            "fluxes.nc": ["post/fluxes.nc"],
        })
        years = {path: [int(path[:4]), int(path[:4])] for paths in streams.values() for path in paths if path[0].isdigit()}
        years.update({
            "post/offline_MIZER_timeseries.nc": [1985, 2022],
            "ecoval/e3t.nc": [1981, 1981], "old_ecoval/e3t.nc": [1981, 1981], "post/fluxes.nc": [1900, 1900],
        })
        read = []
        found = self.years_of(streams, read=lambda path: read.append(path) or years[path])
        assert found["years"] == [1981, 2017]
        assert found["left_out"] == {"offline_MIZER_timeseries.nc": 1, "e3t.nc": 2, "fluxes.nc": 1}
        assert set(found["streams"]) == {"amm7_1d_grid_T", "amm7_1d_ptrc_T"}
        assert not [path for path in read if not path[0].isdigit()]

    def test_streams_with_a_tenth_of_the_files_count(self):
        # daily output for its last years only, in a file a month, and monthly
        # output for all of them, in a file a year
        daily = [f"d{n}" for n in range(60)]
        monthly = [f"m{n}" for n in range(6)]
        years = {path: [2015 + int(path[1:]) // 12] * 2 for path in daily}
        years.update({path: [2014 + int(path[1:])] * 2 for path in monthly})
        found = self.years_of({"d": daily, "m": monthly}, read=years.get)
        assert found["years"] == [2014, 2019]
        assert found["left_out"] == {}
        # with fewer, a stream is not the simulation's output
        found = self.years_of({"d": daily, "m": monthly[:5]}, read=years.get)
        assert found["years"] == [2015, 2019]
        assert found["left_out"] == {"m": 5}

    def test_decades_of_output_beside_a_one_off_file(self):
        streams = {"a": ["a1", "a2", "a3", "a4"], "b": ["b1", "b2", "b3", "b4"], "post.nc": ["post.nc"]}
        years = {f"{stream}{n}": [1940 + 10 * n, 1949 + 10 * n] for stream in "ab" for n in range(1, 5)}
        years["post.nc"] = [1950, 2100]
        found = self.years_of(streams, read=years.get)
        assert found["years"] == [1950, 1989]
        assert found["left_out"] == {"post.nc": 1}

    def test_a_stream_whose_years_miss_the_output_is_dropped(self):
        # enough files to count, but the times of another century
        streams = {"out": [f"out_{n}" for n in range(100)], "fluxes": [f"fluxes_{n}" for n in range(20)]}
        years = {f"out_{n}": [2000 + n // 10] * 2 for n in range(100)}
        years.update({f"fluxes_{n}": [1900, 1900] for n in range(20)})
        found = self.years_of(streams, read=years.get)
        assert found["years"] == [2000, 2009]
        assert found["dropped"] == ["fluxes"]

    def test_output_in_one_file_per_stream(self):
        streams = {"a.nc": ["a.nc"], "b.nc": ["b.nc"]}
        years = {"a.nc": [1950, 2000], "b.nc": [1960, 2010]}
        found = self.years_of(streams, read=years.get)
        assert found["years"] == [1950, 2010]
        assert found["left_out"] == {}

    def test_files_without_times(self):
        streams = {"mesh_#.nc": ["mesh_1.nc", "mesh_2.nc"], "out": ["o1", "o2"]}
        years = {"o1": [2010, 2010], "o2": [2011, 2011]}
        found = self.years_of(streams, read=years.get)
        assert found["years"] == [2010, 2011]
        assert set(found["streams"]) == {"out"}
        assert self.years_of({"mesh.nc": ["mesh.nc"]}, read=years.get)["years"] is None

    def test_names_that_do_not_group(self):
        names = [f"file_{chr(97 + n % 26)}{chr(97 + n // 26)}.nc" for n in range(300)]
        streams = {name: [name] for name in names}
        years = {name: [2000 + number % 7, 2000 + number % 7] for number, name in enumerate(names)}
        found = self.years_of(streams, read=years.get)
        # one stream, of every file
        assert list(found["streams"]) == [""]
        assert found["years"] == [2000, 2006]

    def test_no_workers_in_oceanvals_own_process(self, tmp_path):
        # each would import OceanVal: only the script the window runs has them
        paths = write_simulation(tmp_path, range(2001, 2016))
        found = output_years.years_of_streams({"all": paths}, rng=random.Random(1), workers=2)
        assert found["years"] == [2001, 2015]
        assert not found["shared"]


class TestReading:
    def wait(self, key, paths, **kwargs):
        done = threading.Event()
        box = {}
        reading = output_years.Reading(key, paths, lambda found: (box.update(found), done.set()), **kwargs)
        reading.start()
        assert done.wait(60)
        return box

    def test_in_a_process_of_its_own_and_kept(self, tmp_path, oceanvalcache, monkeypatch):
        paths = write_simulation(tmp_path / "sim", range(2003, 2006))
        key = (str(tmp_path / "sim"), 2, (), ())
        assert self.wait(key, paths)["years"] == [2003, 2005]
        assert oceanvalcache.exists()

        # the same files are not read again
        def no(*args, **kwargs):
            raise AssertionError("read again")

        monkeypatch.setattr(output_years.Reading, "_read", no)
        assert self.wait(key, paths)["years"] == [2003, 2005]
        # but are once they change
        monkeypatch.undo()
        paths += write_simulation(tmp_path / "sim", [2006])
        assert self.wait(key, paths)["years"] == [2003, 2006]

    def test_what_an_earlier_rule_kept_is_read_again(self, tmp_path, oceanvalcache):
        paths = write_simulation(tmp_path / "sim", range(2003, 2006))
        key = (str(tmp_path / "sim"), 2, (), ())
        stamp, _ = output_years.signature(paths)
        # as the first rule kept it: no rule in the stamp, and a series beside the output counted
        user_cache.record_output_years(key, stamp[1:], {"years": [2003, 2022]})
        assert self.wait(key, paths)["years"] == [2003, 2005]

    def test_files_are_read_in_workers(self, tmp_path):
        paths = write_simulation(tmp_path, range(2001, 2016))
        found = self.wait((str(tmp_path), 2, (), ()), paths, workers=2)
        assert found["years"] == [2001, 2015]
        # the files read at random as a check
        assert found["shared"]

    def test_it_runs_without_oceanval(self, tmp_path):
        # as the window runs it: importing the package would take seconds
        paths = write_simulation(tmp_path, [2001])
        request = '{"streams": {"a": %s}}' % str(paths).replace("'", '"')
        code = "import sys; import runpy; runpy.run_path(sys.argv[1], run_name='__main__'); " \
               "assert 'oceanval' not in sys.modules"
        output = subprocess.run(
            [sys.executable, "-I", "-c", code, output_years.__file__],
            input=request, capture_output=True, text=True, check=True,
        ).stdout
        assert output.startswith(output_years.RESULT_MARKER)
        assert '"years": [2001, 2001]' in output

    def test_cancelled(self, tmp_path):
        paths = write_simulation(tmp_path, [2001])
        called = []
        reading = output_years.Reading(("x", 0, (), ()), paths, called.append)
        reading.cancel()
        reading._run()
        assert called == []


class TestTheCache:
    def test_kept_while_the_files_are_unchanged(self, oceanvalcache):
        key = ("/sim", 2, (), ("grid",))
        found = {"years": [2000, 2010], "streams": {}}
        assert user_cache.output_years(key, [3, 10, 5]) is None
        assert user_cache.record_output_years(key, [3, 10, 5], found)
        assert user_cache.output_years(key, [3, 10, 5]) == found
        assert user_cache.output_years(key, [4, 10, 5]) is None
        assert user_cache.output_years(("/sim", 1, (), ("grid",)), [3, 10, 5]) is None

    def test_other_entries_are_kept(self, oceanvalcache, tmp_path):
        user_cache.record_sim_dir(str(tmp_path))
        user_cache.record_output_years(("/sim", 2, (), ()), [1, 1, 1], {"years": [1, 2]})
        assert user_cache.recent_sim_dirs() == [str(tmp_path)]
        user_cache.record_sim_dir(str(tmp_path / "other"))
        assert user_cache.output_years(("/sim", 2, (), ()), [1, 1, 1]) == {"years": [1, 2]}

    def test_only_the_latest_are_kept(self, oceanvalcache):
        for number in range(user_cache.MAX_OUTPUT_YEARS + 5):
            user_cache.record_output_years((f"/sim{number}", 2, (), ()), [1, 1, 1], {"years": [1, 2]})
        assert user_cache.output_years(("/sim0", 2, (), ()), [1, 1, 1]) is None
        last = user_cache.MAX_OUTPUT_YEARS + 4
        assert user_cache.output_years((f"/sim{last}", 2, (), ()), [1, 1, 1]) == {"years": [1, 2]}

    def test_an_unreadable_file_is_left_alone(self, oceanvalcache):
        oceanvalcache.write_text("not json")
        assert user_cache.output_years(("/sim", 2, (), ()), [1, 1, 1]) is None
        assert not user_cache.record_output_years(("/sim", 2, (), ()), [1, 1, 1], {"years": [1, 2]})
        assert oceanvalcache.read_text() == "not json"
