"""The order matchup makes its matchups in: quick, non-vertical ones first."""

import os
import shutil

import pytest

import oceanval
import oceanval.matchall as matchall
from oceanval import live, prompts
from oceanval.session import session_info

POINT_SURFACE = "oceanval_matchups/point/surface/temperature/bar/bar_surface_temperature.csv"
POINT_ALL = "oceanval_matchups/point/all/temperature/foo/foo_all_temperature.csv"


@pytest.fixture(autouse=True)
def clean_matchups():
    oceanval.reset()
    session_info["failed_gridded"] = []
    shutil.rmtree("oceanval_matchups", ignore_errors=True)
    yield
    shutil.rmtree("oceanval_matchups", ignore_errors=True)
    oceanval.reset()
    session_info["failed_gridded"] = []


def register(gridded_vertical, point_vertical):
    """One gridded and one point source of temperature per vertical setting."""
    for source, vertical in gridded_vertical:
        oceanval.add_gridded_comparison(
            name="temperature",
            obs_path="data/evaldata/gridded/nws/temperature",
            source=source,
            model_variable="votemper",
            obs_variable="votemper",
            climatology=True,
            start=2000,
            end=2010,
            obs_adder=273.15,
            vertical=vertical,
        )
    for source, vertical in point_vertical:
        oceanval.add_point_comparison(
            name="temperature",
            obs_path="data/evaldata/point/nws/all/temperature",
            source=source,
            model_variable="votemper",
            vertical=vertical,
            obs_adder=273.15,
        )


def test_non_vertical_first_gridded_before_point(monkeypatch):
    register([("foo", True), ("bar", False)], [("foo", True), ("bar", False)])

    calls = []
    real = matchall.gridded_matchup

    def recorded(var_choice=None, **kwargs):
        # what point matchups are made by the time each gridded run starts
        calls.append(
            (
                sorted(var_choice),
                os.path.exists(POINT_SURFACE),
                os.path.exists(POINT_ALL),
            )
        )
        return real(var_choice=var_choice, **kwargs)

    monkeypatch.setattr(matchall, "gridded_matchup", recorded)

    oceanval.matchup(
        sim_dir="data/example",
        start=2000,
        end=2000,
        ask=False,
        thickness="data/example/e3t.nc",
        cores=1,
    )

    # gridded non-vertical, then point non-vertical, then gridded vertical,
    # then point vertical
    assert calls == [
        ([("temperature", "bar")], False, False),
        ([("temperature", "foo")], True, False),
    ]
    assert os.path.exists(POINT_ALL)
    assert os.path.exists("oceanval_matchups/gridded/temperature/foo_temperature_vertical.nc")


def test_failures_of_the_first_gridded_run_are_kept(monkeypatch):
    register([("foo", True), ("bar", False)], [("bar", False)])

    failures = []

    def retried(ask=True, **kwargs):
        failures.extend(x["source"] for x in session_info["failed_gridded"])

    def run(var_choice=None, reset_failures=True, **kwargs):
        if reset_failures:
            session_info["failed_gridded"] = []
        for variable, source in var_choice:
            session_info["failed_gridded"].append(
                {"variable": variable, "source": source, "error": "x"}
            )

    monkeypatch.setattr(matchall, "gridded_matchup", run)
    monkeypatch.setattr(matchall, "retry_failed_gridded", retried)

    oceanval.matchup(
        sim_dir="data/example",
        start=2000,
        end=2000,
        ask=False,
        thickness="data/example/e3t.nc",
        cores=1,
    )

    # the non-vertical run failed too, and its failure survives the vertical run
    assert failures == ["bar", "foo"]


def test_the_window_is_told_of_each_matchup_in_the_order_made():
    register([("foo", True), ("bar", False)], [("foo", True), ("bar", False)])
    told = []

    with prompts.told_by(told.append):
        oceanval.matchup(
            sim_dir="data/example",
            start=2000,
            end=2000,
            ask=False,
            thickness="data/example/e3t.nc",
            cores=1,
        )

    keys = [
        live.matchup_key("gridded", "temperature", "bar"),
        live.matchup_key("point", "temperature", "bar", "surface"),
        live.matchup_key("gridded", "temperature", "foo"),
        live.matchup_key("point", "temperature", "foo", "all"),
    ]
    # first the matchups to make, for the oceanval window's grid
    assert told[0] == {
        "kind": "matchups",
        "items": [
            {"key": key, "variable": "temperature", "title": title, "source": source, "report": False}
            for key, source, title in zip(
                keys,
                ["BAR", "BAR point data (surface)", "FOO", "FOO point data (all depths)"],
                [session_info["short_title"]["temperature"]] * 4,
            )
        ],
    }
    # then each as it is started and made, in turn
    assert [news for news in told[1:] if news["kind"] not in ("extraction", "observations")] == [
        *[{"kind": "matchup", "key": key, "state": state} for key in keys for state in ("matching", "made")],
        {"kind": "matchups_finished"},
    ]
    # and how many of its model files have been read, while it is made
    for key in keys:
        made = told.index({"kind": "matchup", "key": key, "state": "made"})
        counts = [news for news in told[: made] if news["kind"] == "extraction" and news["key"] == key]
        started = told.index({"kind": "matchup", "key": key, "state": "matching"})
        assert told.index(counts[0]) > started
        total = counts[0]["total"]
        assert total > 0 and all(news["total"] == total for news in counts)
        # from none, or not known for a gridded matchup, whose files nctoolkit reads,
        # to all of them
        assert counts[0]["done"] == (None if key.startswith("gridded") else 0)
        assert counts[-1] == {"kind": "extraction", "key": key, "total": total, "done": total, "finished": True}
        done = [news["done"] for news in counts if news["done"] is not None]
        assert done == sorted(done)
        # and that its observations are being read, then have been, while it is made
        observations = [news for news in told if news["kind"] == "observations" and news["key"] == key]
        assert [news["state"] for news in observations] == ["reading", "read"]
        reading, read = (told.index(news) for news in observations)
        assert started < reading < read < made
        if key.startswith("gridded"):
            # after its model files
            assert reading > told.index(counts[-1])
        else:
            # before them
            assert read < told.index(counts[0])


def test_the_observations_read_are_told_in_a_window_only():
    told = matchall._Told(None)
    heard = []
    with prompts.told_by(heard.append):
        told.observing("point", "nitrate", "ices", "surface")
        told.observed("gridded", "temperature", "nsbc")
    assert heard == [
        {"kind": "observations", "key": live.matchup_key("point", "nitrate", "ices", "surface"), "state": "reading"},
        {"kind": "observations", "key": live.matchup_key("gridded", "temperature", "nsbc"), "state": "read"},
    ]
    # at a terminal, nothing
    told.observing("point", "nitrate", "ices", "surface")
    assert len(heard) == 2


def test_the_files_read_are_counted_in_a_window_only(monkeypatch):
    """At a terminal no watcher is started (see oceanval.cdo_watch)."""
    register([("bar", False)], [])
    started = []
    monkeypatch.setattr(matchall.cdo_watch, "_start", lambda *args: started.append(args))

    oceanval.matchup(
        sim_dir="data/example",
        start=2000,
        end=2000,
        ask=False,
        thickness="data/example/e3t.nc",
        cores=1,
    )
    assert started == []

    oceanval.reset()
    shutil.rmtree("oceanval_matchups", ignore_errors=True)
    register([("bar", False)], [])
    with prompts.told_by(lambda news: None, "marker "):
        oceanval.matchup(
            sim_dir="data/example",
            start=2000,
            end=2000,
            ask=False,
            thickness="data/example/e3t.nc",
            cores=1,
        )
    # in a window, one for the gridded matchup's files
    assert [(marker, key) for marker, key, paths in started] == [
        ("marker ", live.matchup_key("gridded", "temperature", "bar"))
    ]
    assert len(started[0][2]) > 0 and all(os.path.exists(path) for path in started[0][2])
