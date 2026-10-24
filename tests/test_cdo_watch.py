"""Counting the model files CDO has read, while nctoolkit reads them (see
oceanval.cdo_watch), and the counts matchup tells the window."""

import json
import os
import subprocess
import sys
import time

import psutil
import pytest

import oceanval.matchall as matchall
from oceanval import cdo_watch, prompts

MARKER = "\x1eoceanval-progress "
SCRIPT = os.path.abspath(cdo_watch.__file__)


def reader(path, seconds):
    """A stand-in for CDO reading path: a process with it in its arguments."""
    return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})", str(path)])


def counts(output):
    """The counts the watcher told, from its output."""
    text = output.decode() if isinstance(output, bytes) else output
    # not splitlines, which splits at the marker's \x1e too
    return [json.loads(line[len(MARKER):]) for line in text.split("\n") if line.startswith(MARKER)]


def watcher(paths, key="gridded|temperature|nsbc|"):
    process = subprocess.Popen(
        [sys.executable, "-I", SCRIPT], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    request = {"parent": os.getpid(), "marker": MARKER, "key": key, "paths": [str(x) for x in paths]}
    with process.stdin:
        process.stdin.write(json.dumps(request).encode())
    return process


def stopped(process):
    """What the watcher wrote, once stopped as matchup stops it."""
    process.terminate()
    output = process.stdout.read()
    process.wait(timeout=10)
    return output


def test_the_files_read_are_counted(tmp_path):
    paths = [tmp_path / f"model_{year}.nc" for year in (2000, 2001, 2002)]
    process = watcher(paths)
    try:
        # one file read, then another, while something else runs that reads none of them
        other = reader(tmp_path / "other.nc", 3)
        reader(paths[0], 1.5).wait()
        reader(paths[1], 1.5).wait()
        time.sleep(2)
        other.wait()
    finally:
        output = stopped(process)
    told = counts(output)
    assert told[0] == cdo_watch.news("gridded|temperature|nsbc|", 3, 0)
    # never more than were read, never fewer than before, and at least a second apart
    assert [news["done"] for news in told] == [0, 1, 2]
    assert {news["total"] for news in told} == {3}
    assert not any(news["finished"] for news in told)


def test_a_file_named_another_way_is_the_same_file(tmp_path):
    (tmp_path / "sub").mkdir()
    path = tmp_path / "model.nc"
    process = watcher([str(tmp_path / "sub" / ".." / "model.nc")])
    try:
        reader(path, 1.5).wait()
        time.sleep(1.5)
    finally:
        output = stopped(process)
    assert [news["done"] for news in counts(output)] == [0, 1]


def gone(pid):
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def test_it_stops_once_the_matchup_has_gone(tmp_path):
    # a stand-in for the matchup, which starts the watcher and is gone without stopping it
    started = tmp_path / "pid"
    matchup = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, os, subprocess, sys\n"
            f"process = subprocess.Popen([sys.executable, '-I', {SCRIPT!r}], stdin=subprocess.PIPE,"
            " stdout=subprocess.DEVNULL)\n"
            "request = {'parent': os.getpid(), 'marker': 'x', 'key': 'k', 'paths': ['a.nc']}\n"
            "process.stdin.write(json.dumps(request).encode())\n"
            "process.stdin.close()\n"
            f"open({str(started)!r}, 'w').write(str(process.pid))\n",
        ],
        timeout=30,
    )
    assert matchup.returncode == 0
    pid = int(started.read_text())
    deadline = time.time() + 10
    while not gone(pid):
        if time.time() > deadline:
            os.kill(pid, 9)
            pytest.fail("the watcher outlived the matchup")
        time.sleep(0.1)


def test_without_psutil_nothing_is_counted(tmp_path):
    # as if psutil could not be imported
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy, sys\n"
            "sys.modules['psutil'] = None\n"
            f"runpy.run_path({SCRIPT!r}, run_name='__main__')\n",
        ],
        input=json.dumps(
            {"parent": os.getpid(), "marker": MARKER, "key": "k", "paths": [str(tmp_path / "a.nc")]}
        ).encode(),
        capture_output=True,
        timeout=30,
    )
    assert run.returncode == 0
    assert run.stdout == b""


def test_it_is_watched_in_a_window_alone(tmp_path, monkeypatch, capfd):
    path = tmp_path / "model.nc"
    started = []
    real = cdo_watch._start
    monkeypatch.setattr(cdo_watch, "_start", lambda *args: started.append(args) or real(*args))
    # at a terminal, or told otherwise than by lines on stdout, nothing is started
    with cdo_watch.watching([str(path)], "k"):
        pass
    with prompts.told_by(lambda news: None):
        with cdo_watch.watching([str(path)], "k"):
            pass
    assert started == []
    with prompts.told_by(lambda news: None, MARKER):
        # nor for no files
        with cdo_watch.watching([], "k"):
            pass
        assert started == []
        with cdo_watch.watching([str(path)], "k"):
            reader(path, 1.5).wait()
            time.sleep(1.5)
    assert len(started) == 1
    # its counts are lines on this process's stdout, and it is stopped by the end of the block
    assert [news["done"] for news in counts(capfd.readouterr().out)] == [0, 1]


class TestToldCounts:
    """matchall._Told's counts of the model files read."""

    def told(self, monkeypatch):
        told = matchall._Told(None)
        clock = [100.0]
        monkeypatch.setattr(told, "_clock", lambda: clock[0])
        return told, clock

    def test_counts_are_spaced_but_the_first_and_last_always_told(self, monkeypatch):
        told, clock = self.told(monkeypatch)
        key = "point|nitrate|ices|surface"
        heard = []
        with prompts.told_by(heard.append):
            told.files("point", "nitrate", "ices", 10, 0, "surface")
            clock[0] += 0.3
            told.files("point", "nitrate", "ices", 10, 1, "surface")
            clock[0] += 0.8
            told.files("point", "nitrate", "ices", 10, 2, "surface")
            clock[0] += 0.1
            told.files("point", "nitrate", "ices", 10, 3, "surface")
            told.files("point", "nitrate", "ices", 10, 10, "surface", finished=True)
            # tried again, from none
            told.files("point", "nitrate", "ices", 10, 0, "surface")
        assert heard == [
            cdo_watch.news(key, 10, 0),
            cdo_watch.news(key, 10, 2),
            cdo_watch.news(key, 10, 10, finished=True),
            cdo_watch.news(key, 10, 0),
        ]

    def test_a_matchup_with_no_files_has_no_count(self, monkeypatch):
        told, _ = self.told(monkeypatch)
        heard = []
        with prompts.told_by(heard.append):
            told.files("point", "nitrate", "ices", 0, 0, "surface")
            told.files("point", "nitrate", "ices", 0, 0, "surface", finished=True)
        assert heard == []

    def test_files_read_by_nctoolkit_are_not_counted_until_watched(self, monkeypatch):
        told, _ = self.told(monkeypatch)
        key = "gridded|temperature|nsbc|"
        heard = []
        with prompts.told_by(heard.append):
            with told.reading("gridded", "temperature", "nsbc", ["a.nc", "b.nc"]):
                assert heard == [cdo_watch.news(key, 2, None)]
            # all have been read once the block is over
            assert heard[-1] == cdo_watch.news(key, 2, 2, finished=True)
            # but not if it fails
            with pytest.raises(ValueError):
                with told.reading("gridded", "temperature", "nsbc", ["a.nc", "b.nc"]):
                    raise ValueError("no")
        assert heard[2:] == [cdo_watch.news(key, 2, None)]
