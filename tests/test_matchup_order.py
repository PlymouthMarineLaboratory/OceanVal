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
    assert told[1:] == [
        *[{"kind": "matchup", "key": key, "state": state} for key in keys for state in ("matching", "made")],
        {"kind": "matchups_finished"},
    ]
