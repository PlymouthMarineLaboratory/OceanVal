"""The check of the dates of the model output the oceanval window's matchups
page makes (see oceanval.model_check): the times of the files, what is
missing from them, and the process that reads them."""

import glob
import os
import pickle
import shutil
import subprocess
import time

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from simulations import write_fvcom

from oceanval import matchall, model_check
from oceanval.model_check import (
    ModelCheck, date_report, files_in_years, load_times_index, save_times_index, year_groups,
)

EXAMPLE = os.path.abspath("data/example_temperature")
PATTERN = "**/**/amm7_1d_**_**_grid_T.nc"


def example_files(*months):
    """The example output's files for months given as "YYYY/MM"."""
    return [glob.glob(os.path.join(EXAMPLE, month, "*.nc"))[0] for month in months]


def times_of(*dates):
    """A file's times, from (year, month, day)s."""
    return pd.DataFrame(
        {"year": [d[0] for d in dates], "month": [d[1] for d in dates], "day": [d[2] for d in dates]}
    )


def monthly(year, months=range(1, 13), day=16):
    return times_of(*[(year, month, day) for month in months])


def daily(year, month, days):
    return times_of(*[(year, month, day) for day in days])


class TestIndexTimes:
    def test_each_file_has_its_times(self):
        files = example_files("2003/01", "2003/02")
        found = []

        times = matchall.index_times(files, progress=lambda done, total: found.append((done, total)))

        assert list(times) == files
        for path in files:
            # as matchup has read them all along
            with xr.open_dataset(path) as ds:
                expected = pd.DataFrame(
                    {
                        "year": [int(x) for x in ds.time_counter.dt.year],
                        "month": [int(x) for x in ds.time_counter.dt.month],
                        "day": [int(x) for x in ds.time_counter.dt.day],
                    }
                )
            pd.testing.assert_frame_equal(times[path], expected)
        assert len(times[files[1]]) == 28
        assert found == [(1, 2), (2, 2)]

    def test_files_known_and_restart_files_are_not_read(self, monkeypatch):
        files = example_files("2003/01", "2003/02")
        known = {files[0]: monthly(2003, [1])}
        opened = []
        open_dataset = xr.open_dataset
        monkeypatch.setattr(
            matchall.xr, "open_dataset", lambda path, **kwargs: opened.append(path) or open_dataset(path, **kwargs)
        )

        times = matchall.index_times(files + ["/sim/restart_2003.nc"], times=known)

        assert times is known
        assert list(times) == files
        assert times[files[0]] is not None and len(times[files[0]]) == 1
        assert set(opened) == {files[1]}
        # and nothing at all to read is not an error
        assert matchall.index_times([]) == {}

    def test_fvcom_output_is_read_as_fvcom(self, monkeypatch):
        monkeypatch.setattr(matchall, "fvcom_times", lambda path: monthly(2010, [3]))

        times = matchall.index_times(["/sim/a.nc"], fvcom=True)

        pd.testing.assert_frame_equal(times["/sim/a.nc"], monthly(2010, [3]))


def old_times(path, time_name="time_counter"):
    """What index_times read from a file before it was vectorised: the
    year, month and day of each time, one time at a time."""
    with xr.open_dataset(path) as ds:
        return pd.DataFrame(
            {
                "year": [int(x.dt.year) for x in ds[time_name]],
                "month": [int(x.dt.month) for x in ds[time_name]],
                "day": [int(x.dt.day) for x in ds[time_name]],
            }
        )


class TestTimesAreReadAsBefore:
    def test_the_example_files(self):
        files = sorted(glob.glob(os.path.join(EXAMPLE, "*", "*", "*.nc")))
        times = matchall.index_times(files, workers=0)
        assert list(times) == files
        for path in files:
            pd.testing.assert_frame_equal(times[path], old_times(path))

    @pytest.mark.parametrize("calendar", ["noleap", "360_day", "standard"])
    def test_calendars_that_are_not_numpy_dates(self, tmp_path, calendar):
        path = str(tmp_path / "out.nc")
        time = xr.date_range("2004-02-26", periods=8, freq="D", calendar=calendar, use_cftime=True)
        xr.Dataset({"x": (("time_counter",), np.arange(8.0))}, coords={"time_counter": time}).to_netcdf(path)

        times = matchall.index_times([path], workers=0)

        pd.testing.assert_frame_equal(times[path], old_times(path))
        assert len(times[path]) == 8

    def test_times_xarray_cannot_decode_are_read_by_nctoolkit(self, tmp_path, monkeypatch):
        import datetime

        path = str(tmp_path / "out.nc")
        # a time dimension without times in it: nothing for xarray to decode
        xr.Dataset({"x": (("time_counter",), np.arange(3.0))}).to_netcdf(path)
        opened = []

        class Opened:
            times = [datetime.datetime(1999, 12, 30), datetime.datetime(1999, 12, 31), datetime.datetime(2000, 1, 1)]

        monkeypatch.setattr(
            matchall.nc, "open_data", lambda ff, checks=True: opened.append(ff) or Opened()
        )

        times = matchall.index_times([path], workers=0)

        assert opened == [path]
        assert list(times[path].day) == [30, 31, 1]
        assert list(times[path].month) == [12, 12, 1]
        assert list(times[path].year) == [1999, 1999, 2000]


class TestIndexTimesInWorkers:
    @pytest.fixture
    def shared_out(self, monkeypatch):
        """Share anything over a file or so out to workers, and record the
        workers started and the calls to share out."""
        monkeypatch.setattr(matchall, "_INDEX_HEAD", (1, 0.0))
        monkeypatch.setattr(matchall, "_PARALLEL_FROM", 0.0)
        record = {"shared": [], "processes": []}
        shared = matchall._read_times_in_workers
        popen = subprocess.Popen

        def spy(paths, fvcom, time_name, count, done):
            record["shared"].append((list(paths), count))
            return shared(paths, fvcom, time_name, count, done)

        def started(*args, **kwargs):
            record["processes"].append(popen(*args, **kwargs))
            return record["processes"][-1]

        monkeypatch.setattr(matchall, "_read_times_in_workers", spy)
        monkeypatch.setattr(matchall.subprocess, "Popen", started)
        return record

    def test_the_times_are_the_same_as_when_read_here(self, shared_out):
        files = sorted(glob.glob(os.path.join(EXAMPLE, "*", "*", "*.nc")))
        found = []

        times = matchall.index_times(files, workers=3, progress=lambda done, total: found.append((done, total)))

        # all but the first file were shared out, to three workers
        [(shared, count)] = shared_out["shared"]
        assert (shared, count) == (files[1:], 3)
        assert len(shared_out["processes"]) == 3
        assert list(times) == files
        for path in files:
            pd.testing.assert_frame_equal(times[path], old_times(path))
        assert [done for done, _ in found] == list(range(1, len(files) + 1))
        assert {total for _, total in found} == {len(files)}
        # and they are gone
        assert all(process.poll() is not None for process in shared_out["processes"])

    def test_few_files_are_read_here(self, monkeypatch):
        started = []
        monkeypatch.setattr(matchall, "_read_times_in_workers", lambda *args: started.append(args))
        files = example_files("2003/01", "2003/02", "2003/03")

        matchall.index_times(files, workers=4)

        assert started == []

    @pytest.mark.parametrize("workers", [0, 1])
    def test_one_worker_or_none_reads_them_here(self, shared_out, workers):
        files = example_files("2003/01", "2003/02", "2003/03")
        matchall.index_times(files, workers=workers)
        assert shared_out["shared"] == []

    def test_by_default_there_are_as_many_workers_as_cores(self, monkeypatch):
        monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {0, 1, 2, 3, 4})
        assert matchall._index_workers() == 5
        assert matchall._index_workers(2) == 2
        assert matchall._index_workers(0) == 0

    def test_files_known_are_not_shared_out_again(self, shared_out):
        files = example_files("2003/01", "2003/02", "2003/03", "2003/04")
        known = {files[2]: monthly(2003, [3])}

        times = matchall.index_times(files + ["/sim/restart_2003.nc"], times=known, workers=2)

        assert shared_out["shared"] == [([files[1], files[3]], 2)]
        assert times is known and list(times) == [files[2], files[0], files[1], files[3]]

    def test_files_a_worker_cannot_read_are_read_here(self, shared_out, monkeypatch):
        # a worker that answers every file with an error
        monkeypatch.setattr(
            matchall,
            "_TIMES_WORKER",
            "import sys\nfor line in sys.stdin:\n    print('@@{\"error\": \"no\"}', flush=True)\n",
        )
        files = example_files("2003/01", "2003/02", "2003/03", "2003/04")

        times = matchall.index_times(files, workers=2)

        assert len(shared_out["shared"]) == 1
        for path in files:
            pd.testing.assert_frame_equal(times[path], old_times(path))

    def test_workers_that_stop_leave_their_files_to_be_read_here(self, shared_out, monkeypatch):
        monkeypatch.setattr(matchall, "_TIMES_WORKER", "import sys; sys.exit(3)")
        files = example_files("2003/01", "2003/02", "2003/03", "2003/04")

        times = matchall.index_times(files, workers=2)

        assert len(shared_out["processes"]) == 2
        assert all(process.poll() is not None for process in shared_out["processes"])
        for path in files:
            pd.testing.assert_frame_equal(times[path], old_times(path))

    def test_noise_from_a_worker_is_ignored(self, shared_out, monkeypatch):
        monkeypatch.setattr(matchall, "_TIMES_WORKER", "print('some library said this')\n" + matchall._TIMES_WORKER)
        files = example_files("2003/01", "2003/02", "2003/03")

        times = matchall.index_times(files, workers=2)

        for path in files:
            pd.testing.assert_frame_equal(times[path], old_times(path))

    def test_a_file_that_is_not_there_is_an_error_as_before(self, shared_out):
        files = example_files("2003/01", "2003/02") + ["/nowhere/at_all.nc"]
        with pytest.raises(Exception):
            matchall.index_times(files, workers=2)
        # and no worker is left behind
        assert all(process.poll() is not None for process in shared_out["processes"])

    def test_fvcom_files(self, shared_out, tmp_path):
        write_fvcom(str(tmp_path))
        first = tmp_path / "2012" / "01" / "run_avg_0001.nc"
        files = [str(first)]
        for number in (2, 3, 4):
            copy = tmp_path / "2012" / "01" / f"run_avg_000{number}.nc"
            shutil.copy(first, copy)
            files.append(str(copy))

        times = matchall.index_times(files, fvcom=True, workers=2)

        assert shared_out["shared"] == [(files[1:], 2)]
        for path in files:
            pd.testing.assert_frame_equal(times[path], matchall.fvcom_times(path))
            assert (times[path].year[0], times[path].month[0]) == (2012, 1)

    def test_the_check_shares_all_the_patterns_files_out_together(self, shared_out, tmp_path):
        files = example_files("2003/01", "2003/02", "2003/03", "2003/04")
        emitted = []
        request = {
            "sim_dir": EXAMPLE,
            "years": [2003, 2003],
            "out_dir": str(tmp_path),
            "fvcom": False,
            "rows": [],
            "files": {"a": files[:3], "b": files[1:]},
        }

        model_check.check(request, emitted.append)

        # one call, for each file once
        [(shared, _)] = shared_out["shared"]
        assert sorted(shared + [files[0]]) == sorted(files)
        progress = [event["done"] for event in emitted if event["event"] == "progress"]
        assert progress == sorted(progress) and progress[-1] == 4


class TestFilesInYears:
    def test_only_files_with_times_in_the_years(self):
        times = {"b": monthly(2001), "a": monthly(2000), "c": monthly(2003)}

        assert files_in_years(times, 2000, 2001) == ["a", "b"]

    def test_a_file_with_any_time_in_the_years(self):
        times = {"x": times_of((1999, 12, 1), (2000, 2, 1)), "y": times_of((2000, 1, 5))}

        assert files_in_years(times, 2000, 2000) == ["x", "y"]
        assert files_in_years(times, 2005, 2006) == []


class TestDateReport:
    def test_complete_monthly_output(self):
        report = date_report({"a": monthly(2000), "b": monthly(2001)}, 2000, 2001, "monthly")

        assert (report["ok"], report["gaps"]) == (True, False)
        assert report["issues"] == []
        assert report["summary"] == "All 24 months from 2000 to 2001 are there"
        assert [year["year"] for year in report["years"]] == [2000, 2001]
        assert {month["status"] for year in report["years"] for month in year["months"]} == {"complete"}
        assert (report["first"], report["last"], report["steps"]) == ("2000-01-16", "2001-12-16", 24)

    def test_missing_months_and_years(self):
        times = {"a": monthly(2000, [1, 2, 4]), "b": monthly(2002)}

        report = date_report(times, 1999, 2003, "monthly", gridded=True)

        assert (report["ok"], report["gaps"]) == (False, True)
        # the years shown run from the first with output to the last
        assert [year["year"] for year in report["years"]] == [2000, 2001, 2002]
        march = report["years"][0]["months"][2]
        assert (march["month"], march["status"], march["count"]) == (3, "missing", 0)
        assert report["issues"] == [
            "No output in 1999, 2001, 2003, which are in the years matched up (1999-2003).",
            "21 months with no output: Mar 2000, May 2000, Jun 2000, Jul 2000, Aug 2000, "
            "Sep 2000, Oct 2000, Nov 2000, Dec 2000, Jan 2001, Feb 2001, Mar 2001, Apr 2001, "
            "May 2001, Jun 2001, Jul 2001, Aug 2001, Sep 2001, Oct 2001, Nov 2001, Dec 2001.",
            "Gridded matchups leave out 2000, as it has fewer than 12 months and other years have all 12.",
        ]
        assert report["summary"] == "21 of 36 months have gaps"

    def test_missing_days_of_daily_output(self):
        times = {
            "jan": daily(2000, 1, range(1, 32)),
            "feb": daily(2000, 2, [day for day in range(1, 30) if day not in (3, 4, 5, 20)]),
            "jan1": daily(2001, 1, range(1, 32)),
            "feb1": daily(2001, 2, range(1, 29)),
        }

        report = date_report(times, 2000, 2001, "1d")

        february = report["years"][0]["months"][1]
        assert february["status"] == "partial"
        assert report["gaps"] is True
        assert february["missing_days"] == [3, 4, 5, 20]
        assert "1 month with times missing: Feb 2000 (days 3-5, 20)." in report["issues"]
        # 28 days in a February of 2001 is all there are
        assert report["years"][1]["months"][1]["status"] == "complete"

    def test_a_missing_leap_day(self):
        times = {f"{year}": daily(year, 2, range(1, 29)) for year in (2001, 2002, 2003, 2004)}
        times["2005"] = daily(2005, 2, range(1, 29))
        times["2008"] = daily(2008, 2, range(1, 30))

        report = date_report(times, 2001, 2008, "1d")

        february = {year["year"]: year["months"][1] for year in report["years"]}
        assert february[2004]["missing_days"] == [29]
        assert february[2008]["status"] == "complete"
        assert february[2005]["status"] == "complete"

    def test_calendars_without_leap_days_or_with_30_day_months_look_complete(self):
        noleap = {f"{year}": daily(year, 2, range(1, 29)) for year in (2000, 2001, 2004)}
        days_360 = {f"{year}/{month}": daily(year, month, range(1, 31)) for year in (2000, 2001) for month in range(1, 13)}

        assert date_report(noleap, 2000, 2000, "1d")["years"][0]["months"][1]["status"] == "complete"
        assert date_report(days_360, 2000, 2001, "1d")["ok"] is True
        assert date_report(days_360, 2000, 2001, "1d")["gaps"] is False

    def test_output_every_few_days(self):
        times = {f"{month}": daily(2000, month, [1, 6, 11, 16, 21, 26]) for month in range(1, 13)}
        times["13"] = daily(2001, 1, [1, 6, 11, 16, 21, 26])
        times["14"] = daily(2001, 2, [1, 6, 11])

        report = date_report(times, 2000, 2001, "5d")

        assert report["years"][1]["months"][1]["status"] == "partial"
        assert "Feb 2001 (3 of 6 times)" in report["issues"][-1]

    def test_dates_in_more_than_one_file(self):
        times = {"a": monthly(2000), "b": monthly(2000, [1])}

        report = date_report(times, 2000, 2000, "monthly")

        assert report["issues"] == ["1 date is in more than one file: 2000-01-16."]
        assert report["summary"] == "See the notes below"
        # something to say, but no dates are missing
        assert (report["ok"], report["gaps"]) == (False, False)

    def test_output_more_often_than_daily_is_not_a_repeat(self):
        hourly = times_of(*[(2000, 1, 1)] * 24 + [(2000, 1, 2)] * 24)
        other = times_of(*[(2000, 1, 2)] * 2)

        report = date_report({"a": hourly, "b": other}, 2000, 2000, "1d")

        assert not any("more than one file" in issue for issue in report["issues"])

    def test_no_output_in_the_years(self):
        report = date_report({"a": monthly(1990)}, 2000, 2001, "monthly")

        assert (report["ok"], report["gaps"]) == (False, True)
        assert report["years"] == []
        assert report["issues"] == ["There is no output in the years 2000-2001."]

    def test_years_asked_for_after_the_output_ends_are_gaps(self):
        # every month shown is there, but 2001 was asked for
        report = date_report({"a": monthly(2000)}, 2000, 2001, "monthly")

        assert [year["year"] for year in report["years"]] == [2000]
        assert {month["status"] for month in report["years"][0]["months"]} == {"complete"}
        assert report["gaps"] is True
        assert report["issues"][0].startswith("No output in 2001")


class TestTimesIndex:
    def test_only_unchanged_files_are_used(self, tmp_path):
        files = []
        for name in ("a.nc", "b.nc"):
            (tmp_path / name).write_bytes(b"x")
            files.append(str(tmp_path / name))
        index = str(tmp_path / "times.pkl")
        save_times_index(index, {files[0]: monthly(2000), files[1]: monthly(2001)})
        (tmp_path / "b.nc").write_bytes(b"changed")

        found = load_times_index(index, files + [str(tmp_path / "c.nc")])

        assert list(found) == [files[0]]
        pd.testing.assert_frame_equal(found[files[0]], monthly(2000))

    def test_a_relative_path_finds_its_file(self, tmp_path, monkeypatch):
        (tmp_path / "a.nc").write_bytes(b"x")
        index = str(tmp_path / "times.pkl")
        save_times_index(index, {str(tmp_path / "a.nc"): monthly(2000)})
        monkeypatch.chdir(tmp_path)

        assert list(load_times_index(index, ["a.nc"])) == ["a.nc"]

    def test_an_unreadable_index_is_nothing(self, tmp_path):
        (tmp_path / "bad.pkl").write_bytes(b"not a pickle")
        with open(tmp_path / "odd.pkl", "wb") as file:
            pickle.dump(["not", "an", "index"], file)

        assert load_times_index(str(tmp_path / "missing.pkl"), ["a"]) == {}
        assert load_times_index(str(tmp_path / "bad.pkl"), ["a"]) == {}
        assert load_times_index(str(tmp_path / "odd.pkl"), ["a"]) == {}


class TestYearGroups:
    def test_datasets_matched_up_for_the_same_years_are_one_group(self):
        datasets = [
            {"source": "NSBC", "gridded": True, "years": [2001, 2003]},
            {"source": "ICES (point)", "gridded": False, "years": [2002, 2002]},
            {"source": "WOA23", "gridded": True, "years": [2002, 2002]},
            {"source": "Mine (point)", "gridded": False, "years": [2001, 2003]},
        ]

        assert year_groups(datasets, [2001, 2003]) == [
            {"years": [2001, 2003], "sources": ["NSBC", "Mine (point)"], "gridded": True},
            {"years": [2002, 2002], "sources": ["ICES (point)", "WOA23"], "gridded": True},
        ]

    def test_point_datasets_alone_are_not_gridded(self):
        datasets = [{"source": "ICES (point)", "gridded": False, "years": [2001, 2003]}]

        assert year_groups(datasets, [2001, 2003])[0]["gridded"] is False

    def test_a_variable_without_datasets_has_the_runs_years(self):
        assert year_groups([], [2001, 2003]) == [{"years": [2001, 2003], "sources": [], "gridded": False}]


def test_the_check_says_what_it_finds_as_it_goes(tmp_path):
    """Its events: how many files have been read, where their times are
    saved, and then the files and dates of each variable, for each of the
    years its datasets are matched up for."""
    files = example_files("2002/01", "2002/02", "2003/01")
    events = []
    request = {
        "sim_dir": EXAMPLE,
        "years": [2002, 2003],
        "fvcom": False,
        "rows": [{"variable": "temperature", "pattern": PATTERN, "time_res": "1d", "groups": [
            {"years": [2002, 2002], "sources": ["NSBC"], "gridded": True},
            {"years": [2003, 2003], "sources": ["ICES (point)"], "gridded": False},
        ]}],
        "files": {PATTERN: files},
        "out_dir": str(tmp_path),
    }

    model_check.check(request, events.append)

    kinds = [event["event"] for event in events]
    assert kinds[0] == "progress" and kinds[-3:] == ["indexed", "files", "done"]
    assert events[kinds.index("indexed") - 1] == {"event": "progress", "done": 3, "total": 3}
    index = events[kinds.index("indexed")]["index"]
    assert set(load_times_index(index, files)) == set(files)
    found = events[kinds.index("files")]
    assert found["total"] == 3
    [nsbc, ices] = found["groups"]
    # each group has only the files with output in its years
    assert (nsbc["years"], nsbc["sources"]) == ([2002, 2002], ["NSBC"])
    assert nsbc["files"] == [os.path.relpath(path, EXAMPLE) for path in files[:2]]
    assert nsbc["dates"]["summary"] == "10 of 12 months have gaps"
    assert nsbc["dates"]["first"] == "2002-01-01"
    # a gridded dataset's years with fewer than 12 months are not left out
    # when no year has all 12, so nothing is said of them
    assert not any("Gridded" in issue for issue in nsbc["dates"]["issues"])
    assert (ices["years"], ices["sources"]) == ([2003, 2003], ["ICES (point)"])
    assert ices["files"] == [os.path.relpath(files[2], EXAMPLE)]
    assert ices["dates"]["first"] == "2003-01-01"
    assert ices["dates"]["summary"] == "11 of 12 months have gaps"
    assert os.listdir(tmp_path) == ["times.pkl"]


def test_years_that_are_no_limit_are_the_outputs(tmp_path):
    """A dataset given no start or end (see oceanval.parsers) is matched up
    for the years of the output, so the years before and after are not
    said to be missing."""
    files = example_files("2002/01", "2002/02")
    events = []
    request = {
        "sim_dir": EXAMPLE,
        "years": [-1000, 10000],
        "fvcom": False,
        "rows": [{"variable": "temperature", "pattern": PATTERN, "time_res": "1d", "groups": [
            {"years": [-1000, 3000], "sources": ["NSBC"], "gridded": True},
        ]}],
        "files": {PATTERN: files},
        "out_dir": str(tmp_path),
    }

    model_check.check(request, events.append)

    [group] = [event for event in events if event["event"] == "files"][0]["groups"]
    assert (group["years"], len(group["files"])) == ([2002, 2002], 2)
    assert not any(issue.startswith("No output in") for issue in group["dates"]["issues"])


def wait_until(condition, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


class TestModelCheck:
    """The check in a process of its own, as the window runs it."""

    def request(self, files, **changes):
        request = {
            "sim_dir": EXAMPLE,
            "years": [2002, 2002],
            "fvcom": False,
            "rows": [{"variable": "temperature", "pattern": PATTERN, "time_res": "1d", "groups": [
                {"years": [2002, 2002], "sources": ["NSBC"], "gridded": True},
            ]}],
            "files": {PATTERN: files},
        }
        request.update(changes)
        return request

    def test_it_runs_and_is_removed(self):
        files = example_files("2002/01", "2002/02")
        changed = []
        check = ModelCheck(7, self.request(files), lambda: changed.append(1))
        # its groups are known before their files and dates are
        assert check.state()["rows"] == [{"variable": "temperature", "total": None, "time_res": "1d", "groups": [
            {"years": [2002, 2002], "sources": ["NSBC"], "count": None, "dates": None},
        ]}]
        try:
            check.start()
            wait_until(lambda: check.status != "running")

            assert (check.status, check.finished, check.question) == ("done", True, 7)
            assert check.index == os.path.join(check.folder, "times.pkl")
            assert changed
            assert check.files_of("temperature", 0) == [os.path.relpath(path, EXAMPLE) for path in files]
            assert check.files_of("temperature", 1) is None
            assert check.files_of("salinity", 0) is None
            state = check.state()
            assert state["status"] == "done" and state["error"] is None
            assert state["progress"] == {"done": 2, "total": 2}
            [row] = state["rows"]
            assert (row["variable"], row["total"]) == ("temperature", 2)
            [group] = row["groups"]
            assert (group["years"], group["sources"], group["count"]) == ([2002, 2002], ["NSBC"], 2)
            assert group["dates"]["summary"] == "10 of 12 months have gaps"
        finally:
            check.remove()
        assert not os.path.exists(check.folder)

    def test_a_check_that_fails_says_why(self):
        check = ModelCheck(1, self.request(["/no/such/file.nc"]), lambda: None)
        try:
            check.start()
            wait_until(lambda: check.status != "running")
            assert check.status == "failed"
            assert check.error
        finally:
            check.remove()

    def test_a_check_can_be_stopped(self):
        files = example_files(*[f"200{year}/{month:02d}" for year in range(5) for month in range(1, 13)])
        check = ModelCheck(1, self.request(files), lambda: None)
        try:
            check.start()
            check.stop()
            assert check.status == "stopped"
            check.process.wait(timeout=10)
            # what it says after it is stopped is not taken
            assert check.status == "stopped" and not check.finished
        finally:
            check.remove()


def _end_stamped_monthly(directory):
    """A year of monthly means, a file a month, each dated at the end of its
    month as GETM dates them: December's at 00:00 on 1 January 2001."""
    paths = []
    for month in range(1, 13):
        ends = np.datetime64(f"{2000 + month // 12}-{month % 12 + 1:02}-01")
        ds = xr.Dataset(
            {"temp": (("time", "lat", "lon"), np.ones((1, 2, 2)))},
            coords={"time": [ends], "lat": [50.0, 51.0], "lon": [-4.0, -3.0]},
        )
        ds.temp.attrs["cell_methods"] = "time: mean"
        path = directory / f"{month:02}" / f"sea_3dm_2000{month:02}.nc"
        os.makedirs(path.parent)
        ds.to_netcdf(path)
        paths.append(str(path))
    return paths


@pytest.mark.parametrize("time_shift, files, first", [(0, 11, "2000-02-01"), (-1, 12, "2000-01-31")])
def test_the_dates_are_said_as_matchup_shifts_them(tmp_path, time_shift, files, first):
    """matchup asks whether to shift the model's times before the matchups
    are shown (see oceanval.time_stamps), so that they show the files and
    dates matched up; the times saved are as read, as matchup shifts them"""
    paths = _end_stamped_monthly(tmp_path / "sim")
    events = []
    request = {
        "sim_dir": str(tmp_path / "sim"),
        "years": [2000, 2000],
        "fvcom": False,
        "time_shift": time_shift,
        "rows": [{"variable": "temperature", "pattern": "**/sea_3dm_**.nc", "time_res": "monthly",
                  "groups": [{"years": [2000, 2000], "sources": ["NSBC"], "gridded": True}]}],
        "files": {"**/sea_3dm_**.nc": paths},
        "out_dir": str(tmp_path / "out"),
    }
    os.makedirs(tmp_path / "out")

    model_check.check(request, events.append)

    [found] = [event for event in events if event["event"] == "files"]
    [group] = found["groups"]
    assert len(group["files"]) == files
    assert group["dates"]["first"] == first
    assert group["dates"]["gaps"] is (time_shift == 0)
    index = [event for event in events if event["event"] == "indexed"][0]["index"]
    assert load_times_index(index, paths)[paths[-1]].year.tolist() == [2001]
