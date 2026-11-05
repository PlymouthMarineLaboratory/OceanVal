"""How many workers read a simulation's files, and the threaded stat."""

import os

import pytest

from oceanval import cdo_check, file_workers, output_years


def cores(monkeypatch, count):
    # raising=False: macOS has no sched_getaffinity
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: set(range(count)), raising=False)


@pytest.mark.parametrize("count, expected", [(1, 1), (2, 1), (4, 2), (8, 4), (12, 6), (16, 6), (64, 6)])
def test_half_the_cores_to_at_most_6(monkeypatch, count, expected):
    cores(monkeypatch, count)
    assert file_workers.workers() == expected


def test_without_sched_getaffinity_the_cpu_count_is_used(monkeypatch):
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 10)
    assert file_workers.workers() == 5
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert file_workers.workers() == 1


def test_every_reader_uses_the_one_rule(monkeypatch):
    cores(monkeypatch, 16)
    assert cdo_check._workers() == 6
    assert output_years.Reading("key", [], lambda result: None).workers == 6
    assert output_years.Reading("key", [], lambda result: None, workers=2).workers == 2


@pytest.fixture
def files(tmp_path):
    paths = []
    for number in range(40):
        path = tmp_path / f"file{number}.nc"
        path.write_bytes(b"x" * (number + 1))
        paths.append(str(path))
    return paths


@pytest.mark.parametrize("workers", [None, 1, 4])
def test_stat_all_is_as_a_loop_would_be(files, workers):
    found = file_workers.stat_all(files, workers)
    assert list(found) == files
    assert [stat.st_size for stat in found.values()] == [os.stat(path).st_size for path in files]


def test_a_file_that_is_not_there_is_left_out(files, tmp_path):
    missing = str(tmp_path / "missing.nc")
    found = file_workers.stat_all(files[:3] + [missing] + files[3:], 4)
    assert missing not in found
    assert list(found) == files


def test_repeats_are_looked_at_once_and_no_files_is_fine(files):
    assert list(file_workers.stat_all(files[:2] + files[:2])) == files[:2]
    assert file_workers.stat_all([]) == {}


def test_stopped_ends_the_search_early(files):
    assert file_workers.stat_all(files, 4, stopped=lambda: True) == {}


def test_the_signature_of_output_years_is_the_same_whatever_the_workers(files):
    one = output_years.signature(files, workers=1)
    many = output_years.signature(files, workers=4)
    assert one == many
    assert one[0][1:3] == [40, sum(range(1, 41))]
    assert set(one[1]) == set(files)
