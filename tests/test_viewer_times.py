"""The times of each file the viewer shows (oceanval.viewer_times): read from
the time axis alone, in any calendar, in a process of its own that runs the
module as a script and imports nothing of OceanVal, and kept in a cache of
their own (~/.oceanvaltimes), which never stops anything."""

import ast
import json
import os
import subprocess
import sys

import pytest
from viewer_simulations import fvcom_file, nemo_simulation, regular_file

from oceanval import viewer_times as times


def test_the_times_of_a_file(tmp_path):
    path = regular_file(str(tmp_path / "r.nc"))
    read = times.file_times(path)
    assert read["name"] == "time" and read["calendar"] == "standard"
    assert read["times"] == ["1990-01-16 00:00:00", "1990-02-15 00:00:00", "1990-03-17 00:00:00"]
    assert read["index"] == [0, 1, 2]


def test_a_360_day_calendar_and_fvcom_times(tmp_path):
    root = nemo_simulation(str(tmp_path / "sim"), years=(2000,), layout="flat", per_month=30, calendar="360_day")
    read = times.file_times(os.path.join(root, "amm7_1m_20000201_20000228_grid_T.nc"))
    assert read["calendar"] == "360_day" and read["times"][-1] == "2000-02-30 12:00:00"
    # FVCOM's modified julian days, beside its Itime2, which is no time axis
    fvcom = times.file_times(fvcom_file(str(tmp_path / "f.nc")))
    assert fvcom["times"][0] == "2012-01-01 00:00:00"


def test_a_file_that_cannot_be_read(tmp_path):
    path = tmp_path / "broken.nc"
    path.write_text("not netCDF")
    read = times.file_times(str(path))
    assert read["times"] == [] and read["error"]


def test_the_script_imports_nothing_of_oceanval():
    """It is run as a script by path, and its pool's workers are spawned:
    importing OceanVal would take seconds in each."""
    tree = ast.parse(open(times.__file__).read())
    imported = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    imported += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [name for name in imported if name.split(".")[0] == "oceanval"]


def test_the_script_reads_each_file(tmp_path):
    paths = [regular_file(str(tmp_path / f"r{number}.nc")) for number in range(3)]
    done = subprocess.run(
        [sys.executable, "-I", times.__file__], input=json.dumps({"paths": paths, "workers": 1}),
        capture_output=True, text=True, timeout=120,
    )
    answers = [json.loads(line[len(times.RESULT_MARKER):]) for line in done.stdout.splitlines()
               if line.startswith(times.RESULT_MARKER)]
    assert sorted(answer["path"] for answer in answers) == sorted(paths)
    assert all(answer["times"]["times"][0] == "1990-01-16 00:00:00" for answer in answers)


def test_reading_in_a_pool_of_workers(tmp_path, monkeypatch):
    root = nemo_simulation(str(tmp_path / "sim"), years=(2000, 2001), layout="flat")
    paths = sorted(os.path.join(root, name) for name in os.listdir(root))
    found = {}
    assert times.Reading(workers=2).run(paths, found.__setitem__)
    assert sorted(found) == paths and all(read["times"] for read in found.values())


def test_a_cancelled_reading_reads_nothing(tmp_path):
    reading = times.Reading(workers=1)
    reading.cancel()
    assert reading.run([regular_file(str(tmp_path / "r.nc"))], lambda path, read: pytest.fail("cancelled")) is False


def test_the_cache(tmp_path, oceanvaltimes):
    path = regular_file(str(tmp_path / "r.nc"))
    assert times.load_cache([path]) == {}
    read = times.file_times(path)
    times.save_cache({path: read, str(tmp_path / "gone.nc"): read, "bad": {"error": "no"}})
    assert times.load_cache([path]) == {path: read}
    assert set(json.loads(oceanvaltimes.read_text())["files"]) == {path}
    # a changed file is read again
    os.utime(path, ns=(1, 1))
    assert times.load_cache([path]) == {}


def test_a_cache_of_another_version_or_unreadable_is_left_alone(tmp_path, oceanvaltimes):
    path = regular_file(str(tmp_path / "r.nc"))
    read = times.file_times(path)
    for kept in ('{"version": 99, "files": {}}', "not json"):
        oceanvaltimes.write_text(kept)
        times.save_cache({path: read})
        assert oceanvaltimes.read_text() == kept
        assert times.load_cache([path]) == {}


def test_half_the_cores(monkeypatch):
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: set(range(8)), raising=False)
    assert times.default_cores() == 4
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {0}, raising=False)
    assert times.default_cores() == 1
