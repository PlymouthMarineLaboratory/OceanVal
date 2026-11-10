"""View simulation output in the oceanval window (oceanval.viewer_app, ported
from ncviewer's tests): the landing page's tile, the setup page it shares with
Validate and Analyze (the same directories-down logic), the viewer's maps and
colour scales over HTTP, the times read in the background, Back and Restart."""

import glob
import json
import os
import threading
import time
import urllib.request

import pytest
from viewer_simulations import nemo_simulation, regular_file

from oceanval import user_cache, viewer_app
from oceanval.app import App
from test_app import get, get_json, leftover_dir, post  # noqa: F401 (leftover_dir is autouse)


@pytest.fixture
def simulation(viewer_simulation):
    return viewer_simulation


@pytest.fixture
def app(tmp_path):
    app = App(cwd=str(tmp_path))
    app.leftovers_asked = True
    app.start()
    yield app
    app.close()


def _form(simulation, **changes):
    return dict({"simdir": simulation, "ndown": "2", "exclude": "restart", "require": "", "missing": ""}, **changes)


def _wait_for(app, view, seconds=60):
    deadline = time.monotonic() + seconds
    while app.view != view and time.monotonic() < deadline:
        time.sleep(0.05)
    assert app.view == view


def _wait_for_times(app, seconds=60):
    """The times of every file are read in the background once the viewer has opened."""
    deadline = time.monotonic() + seconds
    while (app.viewer_times is not None or not app.viewer_catalogue.times_complete) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert app.viewer_catalogue.times_complete and app.viewer_times is None


def _guessed(app, seconds=60, **asked):
    """The answer to /api/viewer/missing, which is looked for in a thread of
    its own: the page asks again while the status is "reading"."""
    deadline = time.monotonic() + seconds
    while True:
        answer = get_json(app, "/api/viewer/missing", **asked)[1]
        if answer["status"] != "reading" or time.monotonic() > deadline:
            return answer
        time.sleep(0.02)


def _view(app, simulation, **changes):
    assert post(app, "/api/choose", {"action": "viewer"}) == (200, {"ok": True})
    assert post(app, "/api/setup", {"form": _form(simulation, **changes)}) == (200, {"ok": True})
    _wait_for(app, "viewer")


def _plot(app, **params):
    return get_json(app, "/api/viewer/plot", **params)[1]


def _limits(app, **params):
    return get_json(app, "/api/viewer/limits", **params)[1]


# ---- the landing page and the setup page ----

def test_the_tile_is_first_on_the_landing_page(app):
    page = get(app, "/")[1]
    assert 'class="choices is-four"' in page
    assert page.index('data-action="viewer"') < page.index('data-action="analyze_menu"') < page.index('data-action="validate_menu"') < page.index('data-action="help"')
    assert "View simulation output" in page and "Interactively view netCDF files" in page


def test_the_tile_goes_to_the_setup_page_and_back(app, simulation):
    assert post(app, "/api/choose", {"action": "viewer"}) == (200, {"ok": True})
    shown = get_json(app, "/api/state")[1]
    assert shown["view"] == "setup" and shown["action"] == "viewer"
    # the setup page's boxes, with the files skipped left to the user
    assert shown["setup"]["form"]["exclude"] == "" and shown["setup"]["form"]["missing"] == ""
    # Back keeps what was typed, for when the tile is chosen again
    form = _form(simulation, missing='[["-999", ""]]')
    assert post(app, "/api/back", {"form": form}) == (200, {"ok": True})
    assert app.view == "home"
    assert post(app, "/api/choose", {"action": "viewer"})[0] == 200
    kept = get_json(app, "/api/state")[1]["setup"]["form"]
    assert kept["simdir"] == simulation and kept["missing"] == '[["-999", ""]]'
    # the other tiles are not changed by it
    post(app, "/api/back", {"form": form})
    assert post(app, "/api/choose", {"action": "validate_menu"})[0] == 200
    assert post(app, "/api/back")[0] == 200 and app.view == "home"


def test_the_directories_down_are_found_as_oceanval_finds_them(app, simulation, tmp_path):
    """The setup page's probe, which Validate and Analyze use too: the layout
    of the folders tells the depth, whatever depth is typed."""
    post(app, "/api/choose", {"action": "viewer"})
    found = get_json(app, "/api/probe", simdir=simulation, ndown="", out="", exclude="restart")[1]
    assert found["layout"] == {"pattern": "YYYY/MM/**.nc", "ndown": 2} and found["files"] is None
    found = get_json(app, "/api/probe", simdir=simulation, ndown="2", out="", exclude="restart")[1]
    assert found["files"] == 48
    # the years of the output are the recipes window's, not read for viewing
    assert "output_key" not in found and app._output_years is None
    months = nemo_simulation(str(tmp_path / "months"), years=(2000,), layout="MM")
    assert get_json(app, "/api/probe", simdir=months, ndown="", out="")[1]["layout"]["ndown"] == 1
    flat = nemo_simulation(str(tmp_path / "flat"), years=(2000,), layout="flat")
    assert get_json(app, "/api/probe", simdir=flat, ndown="", out="")[1]["layout"]["ndown"] == 0
    assert get_json(app, "/api/probe", simdir=str(tmp_path / "nowhere"), ndown="", out="")[1]["directory"] == "missing"


def test_what_the_setup_page_refuses(app, simulation):
    post(app, "/api/choose", {"action": "viewer"})
    status, reply = post(app, "/api/setup", {"form": _form(simulation, ndown="5")})
    assert status == 400 and reply["errors"] == {"ndown": "There are no netCDF files there to look at."}
    # a missing value that is not a number is said so, and nothing is read
    status, reply = post(app, "/api/setup", {"form": _form(simulation, missing='[["abc",""]]')})
    assert status == 400 and reply["errors"] == {"missing": "Missing value 1: 'abc' is not a number."}
    status, reply = post(app, "/api/setup", {"form": _form(simulation, simdir="")})
    assert status == 400 and "simdir" in reply["errors"]
    assert app.view == "setup" and app.viewer_catalogue is None


def test_restart_files_are_left_out_only_by_the_skip_box(app, tmp_path):
    root = nemo_simulation(str(tmp_path / "sim"), years=(2000,), layout="flat")
    regular_file(os.path.join(root, "model_restart.nc"))
    post(app, "/api/choose", {"action": "viewer"})
    assert get_json(app, "/api/probe", simdir=root, ndown="0", out="", exclude="restart")[1]["files"] == 24
    assert get_json(app, "/api/probe", simdir=root, ndown="0", out="", exclude="")[1]["files"] == 25
    assert post(app, "/api/setup", {"form": _form(root, ndown="0", exclude="")})[0] == 200
    _wait_for(app, "viewer")
    assert "sst" in get_json(app, "/api/viewer/catalogue")[1]["variables"]


def test_parse_missing():
    assert viewer_app.parse_missing("") == []
    assert viewer_app.parse_missing('[["-999",""],["0","5"],["",""]]') == [(-999.0, -999.0), (0.0, 5.0)]
    assert viewer_app.parse_missing('[[" 1e20 ", ""]]') == [(1e20, 1e20)]
    for bad in ('[["abc",""]]', '[["1","x"]]', '[["","3"]]', '[["nan",""]]', '[["inf",""]]', "not json", '{"a": 1}'):
        with pytest.raises(ValueError):
            viewer_app.parse_missing(bad)


def test_parse_names():
    assert viewer_app.parse_names("") == []
    assert viewer_app.parse_names('["a", "b"]') == ["a", "b"]
    for bad in ("x", '{"a": 1}', "[1]"):
        with pytest.raises(ValueError):
            viewer_app.parse_names(bad)


# ---- the viewer ----

def test_the_token_is_needed(app):
    for route in ("/api/viewer/catalogue", "/api/viewer/plot", "/api/viewer/limits", "/api/viewer/missing", "/api/pattern_checks/plot", "/bokeh.min.js"):
        assert get(app, route, token="wrong")[0] == 403


def test_the_pattern_map_is_answered_as_json(app):
    # the output step's card asks for its map as JSON: a route left off the list is a plain 404
    status, body = get_json(app, "/api/pattern_checks/plot", number="0")
    assert status == 200 and body == {"ok": False, "error": "There is no map."}


def test_the_missing_value_guess(app, tmp_path):
    from viewer_simulations import land_simulation

    root = land_simulation(str(tmp_path / "sim"), land=-999.0)
    asked = dict(simdir=root, ndown="0", exclude="", require="")
    # the first ask starts the search and comes back at once, holding nothing up
    assert get_json(app, "/api/viewer/missing", **asked)[1] == {
        "status": "reading", "value": None, "ersem": False,
    }
    assert _guessed(app, **asked) == {"status": "done", "value": "-999", "ersem": False}
    # the answer is kept: the same question is not looked for again
    from oceanval import viewer_missing

    original = viewer_missing.guess
    viewer_missing.guess = lambda *args, **kwargs: pytest.fail("asked again")
    try:
        assert get_json(app, "/api/viewer/missing", **asked)[1]["value"] == "-999"
    finally:
        viewer_missing.guess = original
    # a depth that is not a number, or no directory, is nothing to guess at
    for odd in (dict(asked, ndown="x"), dict(asked, simdir="")):
        assert get_json(app, "/api/viewer/missing", **odd)[1] == {
            "status": "done", "value": None, "ersem": False,
        }


def test_a_missing_value_search_moved_on_from_gives_up(app, tmp_path, monkeypatch):
    """The search opens a file of each file type under the netCDF lock, which
    the viewer's own reading of the output needs: one the user has moved on
    from (Continue, Back or Restart, all of which drop the viewer) must stop
    rather than run to the end."""
    from oceanval import viewer_missing
    from viewer_simulations import land_simulation

    root = land_simulation(str(tmp_path / "sim"), land=-999.0)
    asking = threading.Event()
    gave_up = []

    def slow(*args, stopped=lambda: False, **kwargs):
        asking.set()
        deadline = time.monotonic() + 30
        while not stopped() and time.monotonic() < deadline:
            time.sleep(0.01)
        gave_up.append(stopped())
        return {"value": None, "ersem": False}

    monkeypatch.setattr(viewer_missing, "guess", slow)
    asked = dict(simdir=root, ndown="0", exclude="", require="")
    assert get_json(app, "/api/viewer/missing", **asked)[1]["status"] == "reading"
    assert asking.wait(10)
    with app._lock:
        app._viewer_drop()
    deadline = time.monotonic() + 10
    while not gave_up and time.monotonic() < deadline:
        time.sleep(0.01)
    assert gave_up == [True]
    # nothing of the search that gave up is kept, so asking again looks afresh
    assert app._viewer_guess is None


class TestContinueDoesNotFindTheFilesAgain:
    """The setup page has found the files (the live check) and read them (the
    missing value search) by the time Continue is pressed, so Continue must
    carry on with both rather than walk the directories and read the output
    again, behind a page saying the files are being found."""

    @staticmethod
    def _ask(app, simulation, **changes):
        form = _form(simulation, **changes)
        return dict(simdir=form["simdir"], ndown=form["ndown"], exclude=form["exclude"], require=form["require"])

    def test_the_directories_are_walked_once(self, app, simulation, monkeypatch):
        import importlib

        from oceanval import app as app_module
        from oceanval import viewer_catalogue

        # oceanval.create_recipes is the function, so the module is asked for by name
        create_recipes = importlib.import_module("oceanval.create_recipes")
        walks = []
        original = create_recipes.simulation_paths

        def counted(*args, **kwargs):
            walks.append(args[:2])
            return original(*args, **kwargs)

        # every module that walks imported the function by name
        monkeypatch.setattr(app_module, "simulation_paths", counted)
        monkeypatch.setattr(viewer_catalogue, "simulation_paths", counted)
        monkeypatch.setattr(create_recipes, "simulation_paths", counted)
        assert post(app, "/api/choose", {"action": "viewer"}) == (200, {"ok": True})
        asked = self._ask(app, simulation)
        found = get_json(app, "/api/probe", out="", **asked)[1]
        assert found["files"] == 48 and len(walks) == 1
        # however long ago it was found: the user has agreed to that count
        clock = [1000.0]
        monkeypatch.setattr(app, "_clock", lambda: clock[0])
        clock[0] += 3600
        assert post(app, "/api/setup", {"form": _form(simulation)}) == (200, {"ok": True})
        _wait_for(app, "viewer")
        assert len(walks) == 1, f"the directories were walked again after Continue: {walks}"

    def test_the_output_is_read_once_when_the_missing_value_search_has_read_it(self, app, simulation, monkeypatch):
        from oceanval import viewer_catalogue

        reads = []
        original = viewer_catalogue.scan

        def counted(*args, **kwargs):
            reads.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(viewer_catalogue, "scan", counted)
        assert post(app, "/api/choose", {"action": "viewer"}) == (200, {"ok": True})
        asked = self._ask(app, simulation)
        # the page asks with the words as typed; Continue has them split
        assert _guessed(app, **dict(asked, exclude=" restart "))["status"] == "done"
        assert len(reads) == 1
        assert post(app, "/api/setup", {"form": _form(simulation)}) == (200, {"ok": True})
        _wait_for(app, "viewer")
        assert len(reads) == 1, "the output was read again after the missing value search read it"
        # it is the reading the viewer works with
        assert app.viewer_catalogue is not None and app.viewer_catalogue.streams
        assert app._viewer_scanned is None
        _wait_for_times(app)

    def test_a_search_of_other_files_is_not_taken_over(self, app, simulation):
        assert post(app, "/api/choose", {"action": "viewer"}) == (200, {"ok": True})
        # asked about without the filter, Continue is pressed with it
        assert _guessed(app, **self._ask(app, simulation, exclude=""))["status"] == "done"
        kept = app._viewer_scanned
        assert kept is not None
        assert post(app, "/api/setup", {"form": _form(simulation)}) == (200, {"ok": True})
        _wait_for(app, "viewer")
        assert app.viewer_catalogue is not kept[1]
        assert app._viewer_scanned is None
        # what was not taken is let go of
        assert kept[1]._stop.is_set()

    def test_nothing_is_kept_when_the_user_goes_back(self, app, simulation):
        assert post(app, "/api/choose", {"action": "viewer"}) == (200, {"ok": True})
        assert _guessed(app, **self._ask(app, simulation))["status"] == "done"
        kept = app._viewer_scanned[1]
        with app._lock:
            app._viewer_drop()
        assert app._viewer_scanned is None and kept._stop.is_set()

    def test_the_page_is_told_only_of_the_variables_being_read(self, app, simulation, monkeypatch):
        """The files are known, so the first news is how many kinds of file
        there are to read: "Finding the files" has nothing left to describe."""
        from oceanval import viewer_catalogue

        release = threading.Event()
        seen = []
        original = viewer_catalogue.scan

        def held(*args, **kwargs):
            with app._lock:
                seen.append(dict(app.viewer_scan))
            release.wait(30)
            return original(*args, **kwargs)

        monkeypatch.setattr(viewer_catalogue, "scan", held)
        assert post(app, "/api/choose", {"action": "viewer"}) == (200, {"ok": True})
        try:
            assert post(app, "/api/setup", {"form": _form(simulation)}) == (200, {"ok": True})
            deadline = time.monotonic() + 10
            while not seen and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            release.set()
        assert seen and seen[0]["phase"] == "variables" and seen[0]["total"] == 2
        _wait_for(app, "viewer")


def test_bokeh_is_served(app):
    status, body = get(app, "/bokeh.min.js")
    assert status == 200 and len(body) > 100_000
    # only once the viewer is opened is it loaded by the page
    assert "bokeh.min.js" not in get(app, "/")[1].split("<body")[0]


def test_probe_then_view(app, simulation):
    _view(app, simulation, missing='[["-999",""]]')
    assert app.viewer_missing == [(-999.0, -999.0)]
    _wait_for_times(app)
    summary = get_json(app, "/api/viewer/catalogue")[1]
    assert summary["files"] == 48
    reply = _plot(app, stream=0, vars='["votemper"]', step=3, level=1)
    assert reply["ok"] and reply["file"].startswith("2000/04/")
    assert reply["when"] == "2000-04-16 00:00:00"
    assert reply["clim"][0] < reply["clim"][1]
    assert reply["n_missing"] == 0
    # the warmest and coldest cells set missing go, and the scale narrows
    lo, hi = reply["clim"]
    app.viewer_missing = [(lo, lo + 0.01), (hi - 0.01, hi)]
    narrower = _plot(app, stream=0, vars='["votemper"]', step=3, level=1)
    assert narrower["n_missing"] > 0
    assert lo < narrower["clim"][0] and narrower["clim"][1] < hi
    app.viewer_missing = [(-1e9, 1e9)]
    everything = _plot(app, stream=0, vars='["votemper"]', step=3, level=1)
    assert everything["ok"] and any("Every cell is missing" in note for note in everything["notes"])
    app.viewer_missing = []
    reply = _plot(app, stream=0, vars='["votemper"]', vmin=0, vmax=30)
    assert reply["clim"] == [0, 30]
    assert _plot(app, stream=0, vars='["nothing"]')["ok"] is False
    # the sum of two variables of a file type: one map, and the units said to differ
    summed = _plot(app, stream=1, vars='["chl", "votemper"]', step=3, level=1)
    assert summed["ok"] and any("units differ (degC, mg m-3)" in note for note in summed["notes"])
    single = _plot(app, stream=1, vars='["chl"]', step=3, level=1)
    assert summed["clim"][0] > single["clim"][1] and "units differ" not in " ".join(single["notes"])
    # the figure names the sum by its hover, not a title
    assert "chl + votemper" in str(summed["item"])
    # what cannot be added is said in words, not as an exception
    refused = _plot(app, stream=0, vars='["votemper", "sossheig"]')
    assert refused == {"ok": False, "error": "votemper and sossheig have different dimensions, so they cannot be added."}
    assert _plot(app, stream=0, vars='["votemper", "votemper"]')["ok"] is False
    assert _plot(app, stream=0, vars="[]") == {"ok": False, "error": "Choose a variable."}
    assert _plot(app, stream=0, vars="nonsense")["error"] == "The variables could not be read."
    # the range over the whole run, for an animation's one colour scale
    limits = _limits(app, stream=1, vars='["chl"]', level=1)
    assert limits["ok"] and limits["vmin"] < limits["vmax"] and limits["symmetric"] is False
    app.viewer_missing = [(-1e9, 1e9)]
    assert "Every cell is missing" in _limits(app, stream=1, vars='["chl"]')["error"]
    app.viewer_missing = []
    assert _limits(app, stream=0, vars='["votemper", "sossheig"]')["error"].endswith("cannot be added.")
    assert _limits(app, stream=0, vars="[]")["error"] == "Choose a variable."
    # Back goes to the setup page, with the boxes as they were
    assert post(app, "/api/back")[0] == 200
    shown = get_json(app, "/api/state")[1]
    assert shown["view"] == "setup" and shown["setup"]["form"]["missing"] == '[["-999",""]]'
    assert app.viewer_catalogue is None
    assert _plot(app, stream=0, vars='["votemper"]')["error"] == "Nothing has been read yet."


def test_a_simulation_viewed_is_used_before(app, simulation):
    """It is listed in the simulation box's arrow, as one analysed or matched up is."""
    assert get_json(app, "/api/state")[1]["setup"]["recent"] == []
    _view(app, simulation)
    assert user_cache.recent_sim_dirs()[0] == simulation
    assert get_json(app, "/api/state")[1]["setup"]["recent"][0] == simulation


def test_state_is_long_polled(app):
    first = get_json(app, "/api/state")[1]
    started = time.monotonic()
    threading.Timer(0.3, app._notify).start()
    later = get_json(app, "/api/state", version=first["version"])[1]
    assert later["version"] == first["version"] + 1
    assert time.monotonic() - started < 5


@pytest.fixture
def slow_times(monkeypatch):
    """The background reading of the times of the files (those after the first of each file type, which are
    read before the viewer opens) waits for the gate to be opened."""
    from oceanval import viewer_catalogue

    gate = threading.Event()
    real = viewer_catalogue._read_times

    def gated(*args, **kwargs):
        gate.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(viewer_catalogue, "_read_times", gated)
    yield gate
    gate.set()


def test_the_viewer_opens_while_the_times_are_read(app, simulation, slow_times):
    _view(app, simulation)
    shown = get_json(app, "/api/state")[1]
    # the viewer is there, and says how many of the files' times are in
    assert shown["view"] == "viewer" and shown["viewer"]["times"]["total"] == 48 and shown["viewer"]["times_number"] == 0
    summary = get_json(app, "/api/viewer/catalogue")[1]
    assert summary["times_complete"] is False
    assert [(stream["steps"], stream["pending"]) for stream in summary["streams"]] == [(1, 23), (1, 23)]
    # the first map can be drawn meanwhile
    reply = _plot(app, stream=1, vars='["chl"]', step=0, level=1)
    assert reply["ok"] and reply["when"] == "2000-01-16 12:00:00"
    # the times come in, and the page is told
    slow_times.set()
    deadline = time.monotonic() + 30
    while app.viewer_times is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    shown = get_json(app, "/api/state")[1]
    assert shown["viewer"]["times"] is None and shown["viewer"]["times_number"] == 1
    summary = get_json(app, "/api/viewer/catalogue")[1]
    assert summary["times_complete"] is True
    assert [(stream["steps"], stream["pending"]) for stream in summary["streams"]] == [(24, 0), (24, 0)]
    assert summary["streams"][0]["step_files"] == list(range(24))
    # Play's colour scale for some of the steps: those of 2001 (steps 12 to 23) differ from those of 2000
    year_2000 = _limits(app, stream=1, vars='["chl"]', level=1, first=0, last=11)
    year_2001 = _limits(app, stream=1, vars='["chl"]', level=1, first=12, last=23)
    assert year_2000["ok"] and year_2001["ok"] and year_2001["vmin"] > year_2000["vmin"]


def test_going_back_stops_the_times_being_read(app, simulation, slow_times):
    _view(app, simulation)
    assert app.viewer_times is not None
    assert post(app, "/api/back")[0] == 200
    assert app.view == "setup" and app.viewer_times is None
    # what was waiting is let go, and the reading stops: it does not say it is done
    slow_times.set()
    time.sleep(0.5)
    assert app.viewer_times is None and app.viewer_times_number == 0
    assert get_json(app, "/api/state")[1]["viewer"]["times"] is None


def test_a_new_reading_replaces_the_one_going(app, simulation, slow_times):
    _view(app, simulation)
    first = app.viewer_catalogue
    assert post(app, "/api/back")[0] == 200
    assert post(app, "/api/setup", {"form": _form(simulation)}) == (200, {"ok": True})
    deadline = time.monotonic() + 30
    while app.viewer_catalogue in (first, None) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert app.viewer_catalogue is not first
    slow_times.set()
    deadline = time.monotonic() + 30
    while app.viewer_times is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    # one set of times came in, not two
    assert app.viewer_times_number == 1 and app.viewer_catalogue.times_complete


def test_restart_from_the_viewer(app, simulation, slow_times):
    _view(app, simulation, missing='[["0",""]]')
    assert post(app, "/api/restart_all")[0] == 200
    shown = get_json(app, "/api/state")[1]
    assert shown["view"] == "home" and shown["action"] is None and shown["viewer"]["catalogue_number"] is None
    slow_times.set()
    time.sleep(0.5)
    assert app.viewer_times is None and app.viewer_times_number == 0 and app.viewer_missing == []
    # the boxes start afresh
    post(app, "/api/choose", {"action": "viewer"})
    assert get_json(app, "/api/state")[1]["setup"]["form"]["missing"] == ""


def test_the_page_says_how_to_open_it(app):
    """The state the page starts with, inlined, has the viewer's part."""
    body = urllib.request.urlopen(app.url).read().decode()
    assert '"viewer":' in body and "renderViewer" in body
    assert json.loads(get(app, "/api/state")[1])["viewer"]["coast"] in (True, False)


class TestOneFile:
    """The setup page takes the path of one file as well as a directory: used as it is, whatever its name,
    with no directories down and no file name filters."""

    @pytest.fixture
    def one_file(self, tmp_path):
        from viewer_simulations import land_simulation

        import xarray as xr

        root = land_simulation(str(tmp_path / "land"), land=-999.0)
        # the three files' times in one file, named as a file that the filters would leave out
        parts = [xr.open_dataset(name) for name in sorted(glob.glob(os.path.join(root, "land_*.nc")))]
        try:
            ds = xr.concat(parts, dim="time")
            path = str(tmp_path / "restart_all.nc")
            ds.load().to_netcdf(path, encoding={"thetao": {"_FillValue": None}})
        finally:
            for part in parts:
                part.close()
        return path

    def test_check_setup_keeps_the_file_and_asks_no_depth(self, one_file, tmp_path):
        arguments, errors = viewer_app.check_setup(
            {"simdir": one_file, "ndown": "", "exclude": "restart", "require": "nothing", "missing": ""}, str(tmp_path)
        )
        assert errors == {}
        assert arguments["simdir"] == one_file

    def test_a_directory_still_needs_its_depth(self, one_file, tmp_path):
        _, errors = viewer_app.check_setup(
            {"simdir": os.path.dirname(one_file), "ndown": "", "exclude": "", "require": "", "missing": ""}, str(tmp_path)
        )
        assert "ndown" in errors

    def test_scan_gives_the_one_file(self, one_file):
        from oceanval import viewer_catalogue

        found = viewer_catalogue.scan(one_file, 0, exclude="restart", require="nothing", wait=False)
        try:
            assert [stream["files"] for stream in found.streams] == [[one_file]]
            assert found.simdir == os.path.dirname(one_file)
        finally:
            found.close()

    def test_probe_calls_it_found_for_viewing_only(self, one_file, app):
        app.action = viewer_app.VIEWER
        found = app.probe(one_file, "", "")
        assert (found["directory"], found["file"], found["files"], found["all_files"]) == ("found", True, 1, 1)
        app.action = "matchup_validate"
        assert app.probe(one_file, "0", "")["directory"] == "missing"

    def test_the_missing_value_is_looked_for_without_a_depth(self, one_file, app):
        asked = dict(simdir=one_file, ndown="", exclude="restart", require="")
        assert app.viewer_missing_guess(dict(asked))["status"] == "reading"
        assert _guessed(app, **asked) == {"status": "done", "value": "-999", "ersem": False}


# ---- closing the browser ----

def test_closing_the_browser_quits_while_viewing(app, simulation):
    """The viewer is open and its times are being read: the page going quiet
    quits the command and lets go of what it was reading."""
    app.page_timeout = 2
    _view(app, simulation)
    catalogue = app.viewer_catalogue
    assert get_json(app, "/api/state", page="a")[0] == 200
    assert app.closed.wait(15)
    assert app.closed_reason == "The browser window was closed."
    assert app.viewer_catalogue is None and catalogue._stop.is_set()


def test_closing_the_browser_quits_while_the_files_are_read(app, simulation, monkeypatch):
    """A file read that never ends, as on a stalled network drive, does not
    hold the quitting up."""
    app.page_timeout = 2
    release = threading.Event()
    monkeypatch.setattr(viewer_app.viewer_catalogue, "scan", lambda *args, **kwargs: release.wait(30) and None)
    assert post(app, "/api/choose", {"action": "viewer"}) == (200, {"ok": True})
    assert post(app, "/api/setup", {"form": _form(simulation)}) == (200, {"ok": True})
    assert get_json(app, "/api/state", page="a")[0] == 200
    try:
        assert app.closed.wait(15)
        assert app.closed_reason == "The browser window was closed."
    finally:
        release.set()
