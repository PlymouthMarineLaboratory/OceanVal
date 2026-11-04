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


def quietly(run, **arguments):
    """run(**arguments), analyze() or compare_simulations(), with no report
    and no check of the files by `cdo sinfo`: the info the run saved."""
    from oceanval import analysis_report, cdo_check

    built = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(analysis_report, "InterimBook", no_book(built))
        patch.setattr(cdo_check, "ENABLED", False)
        run(**arguments)
    return built[0]


# The simulations compare_simulations() is tested with, and analyze() of one
# of them alone with the same options as a comparison of them: each is run
# once in a session, by the test of analyze() (tests/test_analysis.py) that
# checks it, and tests/test_sim_compare.py then checks that the simulation's
# results in the comparison are the same (same_as_analyze).


def _compared(tmp_path_factory, name, daily):
    from simulations import B_LATS, B_LONS, COMPARE_YEARS, TREND_A, TREND_B, write_analysis_simulation

    base = tmp_path_factory.mktemp(name)
    a, b = base / "a", base / "b"
    write_analysis_simulation(str(a), years=COMPARE_YEARS, daily=daily, trend=TREND_A)
    write_analysis_simulation(str(b), years=COMPARE_YEARS, daily=daily, trend=TREND_B, lats=B_LATS, lons=B_LONS)
    return str(a), str(b)


@pytest.fixture(scope="session")
def compared_monthly(tmp_path_factory):
    """Two monthly simulations: A on the analysis grid and B on a finer one, which rises faster."""
    return _compared(tmp_path_factory, "compared_monthly", daily=False)


@pytest.fixture(scope="session")
def compared_daily(tmp_path_factory):
    """compared_monthly's simulations, with daily output."""
    return _compared(tmp_path_factory, "compared_daily", daily=True)


def _analysed(tmp_path_factory, name, sim_dir, **arguments):
    """analyze() of sim_dir alone: (its results folder, the info it saved)."""
    import oceanval
    from oceanval import analysis

    out = tmp_path_factory.mktemp(name)
    info = quietly(oceanval.analyze, sim_dir=sim_dir, n_dirs_down=1, out_dir=str(out), view=False, cores=1, **arguments)
    return os.path.join(str(out), analysis.FOLDER, analysis.RESULTS), info


@pytest.fixture(scope="session")
def analysed_monthly_b(compared_monthly, tmp_path_factory):
    """The monthly B alone, with the climatology and long-term series of
    test_sim_compare.py's comparison of the monthly simulations."""
    from simulations import COMPARE_CLIMATOLOGY, COMPARE_LONG_TERM

    return _analysed(
        tmp_path_factory,
        "analysed_monthly_b",
        compared_monthly[1],
        variables={"Temperature": "thetao"},
        climatology=COMPARE_CLIMATOLOGY,
        long_term=COMPARE_LONG_TERM,
    )


@pytest.fixture(scope="session")
def analysed_daily_a(compared_daily, tmp_path_factory):
    """The daily A alone, with every option (EVERY_OPTION)."""
    from simulations import EVERY_OPTION

    return _analysed(tmp_path_factory, "analysed_daily_a", compared_daily[0], **EVERY_OPTION)


@pytest.fixture(scope="session")
def analysed_transects_b(compared_daily, tmp_path_factory):
    """The daily B alone, with a transect of every kind (COMPARE_TRANSECT)."""
    from oceanval import transects
    from simulations import COMPARE_TRANSECT

    if not transects.available():
        pytest.skip("needs nctoolkit 1.3.6 or later")
    return _analysed(
        tmp_path_factory,
        "analysed_transects_b",
        compared_daily[1],
        variables={"Temperature": "thetao"},
        transects={"Temperature": [COMPARE_TRANSECT]},
        thickness="e3t",
    )


@pytest.fixture(scope="session")
def compared_fvcom(tmp_path_factory):
    """Two raw FVCOM simulations on one mesh: A, and B, which rises faster."""
    from simulations import TREND_A, TREND_B
    from viewer_simulations import fvcom_months

    base = tmp_path_factory.mktemp("compared_fvcom")
    return (
        fvcom_months(str(base / "a"), trend=TREND_A),
        fvcom_months(str(base / "b"), trend=TREND_B),
    )


@pytest.fixture(scope="session")
def mixed_simulations(tmp_path_factory):
    """FVCOM output (the mesh of viewer_simulations.fvcom_mesh, a file a month) and monthly
    output on a grid of 0.1 degrees over it, whose nodes are the grid's cells: (the FVCOM
    directory, its n_dirs_down, the grid's directory, its n_dirs_down)."""
    import numpy as np
    from simulations import write_analysis_simulation
    from viewer_simulations import fvcom_months

    base = tmp_path_factory.mktemp("mixed_simulations")
    months = ((2012, 1), (2012, 2), (2013, 1), (2013, 2))
    fvcom_dir = fvcom_months(str(base / "mesh"), months=months, trend=2.0)
    write_analysis_simulation(
        str(base / "grid"),
        years=(2012, 2013),
        daily=False,
        lats=[float(x) for x in np.round(np.arange(49.8, 51.25, 0.1), 6)],
        lons=[float(x) for x in np.round(np.arange(-5.2, -3.75, 0.1), 6)],
    )
    return fvcom_dir, 1, str(base / "grid"), 1


def _analysed_fvcom(tmp_path_factory, name, sim_dir):
    from simulations import FVCOM_COMPARE_OPTIONS

    return _analysed(tmp_path_factory, name, sim_dir, **FVCOM_COMPARE_OPTIONS)


@pytest.fixture(scope="session")
def analysed_fvcom_a(compared_fvcom, tmp_path_factory):
    """The FVCOM A alone, with every option (FVCOM_COMPARE_OPTIONS)."""
    return _analysed_fvcom(tmp_path_factory, "analysed_fvcom_a", compared_fvcom[0])


@pytest.fixture(scope="session")
def analysed_fvcom_b(compared_fvcom, tmp_path_factory):
    """The FVCOM B alone, with every option (FVCOM_COMPARE_OPTIONS)."""
    return _analysed_fvcom(tmp_path_factory, "analysed_fvcom_b", compared_fvcom[1])


def same_as_analyze(compared, analysed):
    """The results of a simulation in a comparison (compared, its folder)
    are those of analyze() of it alone (analysed, its results folder): the
    same files, each the same."""
    import pandas as pd
    import xarray as xr
    from oceanval import analysis

    names = sorted(os.listdir(analysed))
    assert sorted(os.listdir(compared)) == names
    # the info last, so that a difference in what was worked out is said first
    for name in sorted(names, key=lambda name: name == analysis.INFO_FILE):
        ours, theirs = os.path.join(compared, name), os.path.join(analysed, name)
        if name.endswith(".nc"):
            with xr.open_dataset(ours) as a, xr.open_dataset(theirs) as b:
                a, b = a.load(), b.load()
            # CDO's and nctoolkit's records of what was run name the folders
            for ds in (a, b):
                for key in [key for key in ds.attrs if key.lower().startswith("history")]:
                    del ds.attrs[key]
            xr.testing.assert_identical(a, b)
        elif name.endswith(".csv"):
            pd.testing.assert_frame_equal(pd.read_csv(ours), pd.read_csv(theirs), check_exact=True)
        elif name == analysis.INFO_FILE:
            with open(ours) as a, open(theirs) as b:
                a, b = json.load(a), json.load(b)
            # when each was run
            a.pop("created"), b.pop("created")
            assert a == b, name
        else:
            with open(ours, "rb") as a, open(theirs, "rb") as b:
                assert a.read() == b.read(), name


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
