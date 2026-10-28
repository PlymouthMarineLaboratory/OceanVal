"""What the test modules share."""

import json
import os

import pytest


def no_book(built):
    """A stand-in for oceanval.analysis_report.InterimBook, which builds the
    report of analyze() and compare_simulations() and takes a minute: no
    page is built, and built is given the info the run saved (analysis.json,
    or comparison.json) once it finishes."""
    from oceanval import analysis, sim_compare

    class NoBook:
        def __init__(self, folder, expected, templates, what="analysis"):
            self.folder = folder
            self.what = what

        def start(self):
            pass

        def add(self, number, entry):
            pass

        def stop(self, now=False):
            pass

        def finish(self):
            name = analysis.INFO_FILE if self.what == "analysis" else sim_compare.INFO_FILE
            with open(os.path.join(self.folder, analysis.RESULTS, name)) as file:
                built.append(json.load(file))
            return os.path.join(self.folder, analysis.REPORT, "landing.html")

    return NoBook


@pytest.fixture
def no_report(monkeypatch):
    """analyze() and compare_simulations() without their report: the info
    each run saved, in the order they ran."""
    from oceanval import analysis_report

    built = []
    monkeypatch.setattr(analysis_report, "InterimBook", no_book(built))
    return built


@pytest.fixture(autouse=True)
def oceanvalrc(tmp_path_factory, monkeypatch):
    """Keep the user's own ~/.oceanvalrc out of the tests, and the tests out of
    it: the global file is one in a directory of the test's own, which the
    processes the oceanval window starts use too. (HOME is not changed, as
    that would hide the browsers and caches the tests use.)"""
    path = tmp_path_factory.mktemp("oceanvalrc") / ".oceanvalrc"
    monkeypatch.setenv("OCEANVALRC", str(path))
    return path


@pytest.fixture(autouse=True)
def oceanvalcache(tmp_path_factory, monkeypatch):
    """Keep the user's own ~/.oceanvalcache out of the tests, and the tests out
    of it, as oceanvalrc does for ~/.oceanvalrc."""
    path = tmp_path_factory.mktemp("oceanvalcache") / ".oceanvalcache"
    monkeypatch.setenv("OCEANVALCACHE", str(path))
    return path


@pytest.fixture(autouse=True)
def oceanvaltimes(tmp_path_factory, monkeypatch):
    """Keep the user's own ~/.oceanvaltimes (the times of the files the viewer
    has read, see oceanval.viewer_times) out of the tests: each test has a
    cache of its own."""
    path = tmp_path_factory.mktemp("oceanvaltimes") / ".oceanvaltimes"
    monkeypatch.setenv("OCEANVALTIMES", str(path))
    return path


@pytest.fixture(scope="session")
def viewer_simulation(tmp_path_factory):
    """A NEMO-like simulation laid out YYYY/MM/*.nc, two years of monthly
    files of two file types, for the viewer's tests (see viewer_simulations)."""
    from viewer_simulations import nemo_simulation

    return nemo_simulation(str(tmp_path_factory.mktemp("viewer_sim")))


@pytest.fixture(autouse=True)
def check_servers(monkeypatch):
    """Registering checks the THREDDS servers, as it does outside the oceanval
    window, whose matchups turn it off for the rest of their process (see
    oceanval.app_child), which in a test is the test run's."""
    from oceanval import parsers

    monkeypatch.setattr(parsers, "check_servers", True)


@pytest.fixture
def browser():
    """A Chromium browser, for the tests that drive a page, which are skipped
    where playwright or Chromium is not installed (as in CI)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        playwright = sync_api.sync_playwright().start()
    except Exception as error:
        pytest.skip(f"playwright could not start: {error}")
    try:
        chromium = playwright.chromium.launch()
    except Exception as error:
        playwright.stop()
        pytest.skip(f"Chromium is not available: {error}")
    yield chromium
    chromium.close()
    playwright.stop()

