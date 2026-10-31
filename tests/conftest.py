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


HOME_FILES = (".oceanvalcache", ".oceanvaltimes", ".oceanvalrc")


@pytest.fixture(scope="session", autouse=True)
def home_files_untouched(tmp_path_factory):
    """The per-test fixtures below only cover function-scoped setup: a
    module-scoped fixture that runs analyze() or matchup() is set up before
    them and used to write the user's own ~/.oceanvalcache (20 pytest paths
    pushed out the real simulations). These paths hold for the whole session,
    and a session that left its own temporary folder in the user's files
    fails."""
    folder = tmp_path_factory.mktemp("oceanval_home_files")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OCEANVALCACHE", str(folder / ".oceanvalcache"))
        patch.setenv("OCEANVALTIMES", str(folder / ".oceanvaltimes"))
        patch.setenv("OCEANVALRC", str(folder / ".oceanvalrc"))
        yield
    mine = str(tmp_path_factory.getbasetemp())
    left = []
    for name in HOME_FILES:
        try:
            with open(os.path.join(os.path.expanduser("~"), name)) as file:
                if mine in file.read():
                    left.append("~/" + name)
        except OSError:
            pass
    assert not left, f"the tests wrote to your own {', '.join(left)}"


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


@pytest.fixture(autouse=True)
def no_cdo_check(request, monkeypatch):
    """The check of every model file with `cdo sinfo` (see oceanval.cdo_check) is
    turned off, as most tests have no use for it: tests/test_cdo_check.py and the
    tests that mark themselves with cdo_check turn it on, with a cdo of their own."""
    from oceanval import cdo_check

    monkeypatch.setattr(cdo_check, "ENABLED", bool(request.node.get_closest_marker("cdo_check")))


@pytest.fixture
def fake_cdo(tmp_path, monkeypatch):
    """A `cdo` first on the PATH that crashes with a segmentation fault on files
    whose name contains "bad" and says it read any other (a file with "error" in
    its name ends with an error). Returns the list of the paths it was run on, a
    file in the folder the fake keeps (fake_cdo.calls())."""
    folder = tmp_path / "fake_cdo"
    folder.mkdir()
    log = folder / "calls.txt"
    script = folder / "cdo"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$2" >> {log}\n'
        'case "$2" in\n'
        "  *bad*) kill -SEGV $$ ;;\n"
        "  *error*) echo 'cdo: error' >&2; exit 1 ;;\n"
        "  *slow*) sleep 30 ;;\n"
        "esac\n"
        "echo 'File format: NetCDF'\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")

    class Fake:
        @staticmethod
        def calls():
            return sorted(log.read_text().split()) if log.exists() else []

    return Fake


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
    # the pop-out that says the directories down were filled in from the folders' layout would cover the page
    # for every test that points at a simulation, so each page dismisses it, unless the test sets
    # window.__keepFolderStructure (before it goes to the page) to look at it
    new_page = chromium.new_page

    def new_page_dismissing(*args, **kwargs):
        page = new_page(*args, **kwargs)
        page.context.add_init_script(DISMISS_FOLDER_STRUCTURE)
        return page

    chromium.new_page = new_page_dismissing
    yield chromium
    chromium.close()
    playwright.stop()


DISMISS_FOLDER_STRUCTURE = """
document.addEventListener("DOMContentLoaded", () => {
  new MutationObserver(() => {
    const dialog = document.getElementById("folder-structure");
    if (dialog && !dialog.hidden && !window.__keepFolderStructure) {
      document.getElementById("folder-structure-continue").click();
    }
  }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["hidden"] });
});
"""



def pytest_configure(config):
    config.addinivalue_line(
        "markers", "cdo_check: turns on the check of the model files with `cdo sinfo` (see oceanval.cdo_check)"
    )
