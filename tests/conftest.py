"""What the test modules share."""

import pytest


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
