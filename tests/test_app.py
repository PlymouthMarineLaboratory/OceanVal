"""The oceanval command: the questions OceanVal asks, the process it runs
matchup and validate in, the window's server and, where a browser can be
run, the window itself."""

import ast
import datetime
import glob
import io
import json
import os
import pickle
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import numpy as np
import pytest
import xarray as xr

import oceanval
from oceanval import app as app_module
from oceanval import analysis_report, app_child, cdo_watch, leftovers, live, model_check, prompts, sim_compare
from oceanval.app import (
    App, Console, Question, Run, check_compare, check_output, check_report, check_setup, check_validate,
    default_validate_form, existing_matchups, matchups_asked_about,
)
from oceanval.app_child import ANSWER_MARKER, PROGRESS_MARKER, QUESTION_MARKER
from oceanval.gridded import _ask_minutes, _ask_yes_no
from oceanval.recipes_gui import recipe_rows
from simulations import write_folders, write_fvcom, write_simulation

SETUP_FORM = {
    "simdir": "sim",
    "ndown": "2",
    "domain": "global",
    "start": "2011",
    "end": "2012",
    "exclude": "",
    "require": "",
}

# the output step's boxes, the last before a matchup starts
OUTPUT_FORM = {
    "out_dir": "",
    "unused": [],
    # the choices in `unused` are the user's own, as when the grid has been used
    "existing_chosen": True,
    "existing_confirmed": False,
    "out": "matchup.py",
    "out_typed": False,
}

# a matchup as matchup lists it with its question of whether the matchups are right
COBE2_MATCHUP = {
    "kind": "gridded", "variable": "temperature", "source": "cobe2", "layer": None,
    "vertical": False, "title": "Temperature", "key": "temperature/cobe2",
}

# what matchup sends with its question of whether the matchups are right
MATCHUPS = {
    "kind": "matchups",
    "sim_dir": "/sim",
    "years": [2011, 2012],
    "rows": [
        {
            "variable": "temperature",
            "title": "Temperature",
            "model_variable": "thetao",
            "pattern": "x_**_grid_T.nc",
            "observations": ["COBE2", "ICES (point)"],
            "files": 2,
        }
    ],
    "files": {"x_**_grid_T.nc": ["2011/x_2011_grid_T.nc", "2012/x_2012_grid_T.nc"]},
}


def write_matchups(directory):
    """What validate looks for from a finished matchup."""
    matchups = os.path.join(directory, "oceanval_matchups")
    os.makedirs(os.path.join(matchups, "gridded", "temperature"))
    open(
        os.path.join(
            matchups, "gridded", "temperature", "cobe2_temperature_surface.nc"
        ),
        "w",
    ).close()
    for name in ("short_titles.pkl", "variables_matched.pkl"):
        with open(os.path.join(matchups, name), "wb") as saved:
            pickle.dump({}, saved)


def units_read(app):
    """Wait for the units step, after the recipes window's, to have read the
    units."""
    wait_for(lambda: app.view == "units_table" and app.units_rows is not None)


def units_match(app):
    """Carry on from the units step without changing any conversion."""
    units_read(app)
    assert post(app, "/api/units_continue", {"conversions": {}, "confirmed": True})[0] == 200


def chosen_output(app, tmp_path):
    """Write the script where the output step would, by default: matchup.py
    in the directory worked in, with the matchups beside it."""
    with app._lock:
        app._keep_in_script(out=str(tmp_path / "matchup.py"), out_dir="", overwrite=True)
        app.out_dir = app.results_dir = str(tmp_path)
        writer = app._script_writer
    app._write_script(writer)


def draft(app):
    """The draft of the script the run starts from, in a folder of OceanVal's
    own, until the output step writes it where it says."""
    return app._recipes.context["out"]


def output_continue(app, **output):
    """Carry on from the output step, once the matchups are confirmed, with
    its boxes as OUTPUT_FORM with output (and a core, which matchup alone
    asks for there)."""
    wait_for(lambda: app.view == "output")
    status, reply = post(app, "/api/output", {"form": {**OUTPUT_FORM, "cores": "1", **output}})
    assert status == 200, reply


def choose_domain(page, domain="global"):
    """Choose the spatial coverage on the setup page, which has no default."""
    page.check(f'#f-domain input[value="{domain}"]')


def fill_gridded_years(page):
    """Give each gridded dataset ticked in the recipes window its years, which
    the app has no default for: the climatologies are filled in from the years
    for climatologies, chosen with the arrow at the right of the box, which
    lists the years the output's times cover, and the rest are typed."""
    assert page.text_content("#page-title") == "Gridded data selection"
    for name in ("clim_start", "clim_end"):
        assert page.locator(f"#s-{name}").evaluate("node => node.yearChoices") == [2011, 2012]
    assert page.get_attribute("#s-clim_start", "placeholder") == "2011"
    assert page.get_attribute("#s-clim_end", "placeholder") == "2012"
    assert page.text_content("#g-output-years") == ""
    # the cores, missing values and subset are asked once the matchups are confirmed
    assert page.is_hidden("#s-cores")
    assert page.is_hidden("#s-missing_from")
    assert page.is_hidden("#s-lon_min")
    box = page.locator("#s-clim_start").bounding_box()
    page.mouse.click(box["x"] + box["width"] - 8, box["y"] + box["height"] / 2)
    page.click(".year-list .combo__option >> text=2011")
    assert page.input_value("#s-clim_start") == "2011"
    page.fill("#s-clim_end", "2012")
    # each dataset ticked needs its years: those of the others are typed
    for year in page.locator("#rows-gridded .opts:not([hidden]) input.is-num").all():
        if not year.input_value():
            year.fill("2011" if year.get_attribute("aria-label").startswith("First") else "2012")
    # years outside the output (2011-2012) are said so at the dataset: warned
    # about when partly outside, refused when none are in it
    block = page.locator("#rows-gridded .opts:not([hidden])").first
    first, last = block.locator("input.is-num").nth(0), block.locator("input.is-num").nth(1)
    message = block.locator(".opts__msg")
    first.fill("2009")
    assert "These go beyond the 2011–2012" in message.text_content()
    assert not page.is_disabled("#write")
    first.fill("2005")
    last.fill("2008")
    assert "not in the simulation's output" in message.text_content()
    assert page.is_disabled("#write")
    first.fill("2011")
    last.fill("2012")
    assert message.text_content() == ""


def fill_required_recipe_settings(page):
    """Give what the recipes window has no default for in the app: the years
    of the gridded datasets (see fill_gridded_years), and, on the point
    datasets' page, the years to validate, which can be typed or chosen from
    the years the output's times cover. The number of cores is asked once the
    matchups are confirmed, not here."""
    fill_gridded_years(page)
    page.click("#write")
    assert page.text_content("#page-title") == "Point data selection"
    red = "rgb(192, 57, 43)"
    for name in ("point_start", "point_end"):
        assert page.input_value(f"#s-{name}") == ""
        assert page.locator(f"#s-{name}").evaluate("node => node.yearChoices") == [2011, 2012]
        if page.is_visible(f"#s-{name}-required"):
            # a point dataset is ticked, so they are needed
            assert page.locator(f"#s-{name}").evaluate("node => node.required")
            label = page.locator(f"#s-{name}-label")
            assert label.evaluate("node => getComputedStyle(node).color") == red
            assert label.evaluate("node => getComputedStyle(node).fontWeight") == "700"
            assert page.is_disabled("#write")
    assert page.get_attribute("#s-point_start", "placeholder") == "2011"
    assert page.get_attribute("#s-point_end", "placeholder") == "2012"
    # the first year chosen with the arrow at the right of its box
    box = page.locator("#s-point_start").bounding_box()
    page.mouse.click(box["x"] + box["width"] - 8, box["y"] + box["height"] / 2)
    page.click(".year-list .combo__option >> text=2011")
    assert page.input_value("#s-point_start") == "2011"
    # the last typed: one beyond the years found is only warned about
    page.fill("#s-point_end", "2015")
    assert "These go beyond the 2011–2012 found in the simulation's files." in page.text_content("#g-years")
    page.fill("#s-point_end", "2012")
    assert "go beyond" not in page.text_content("#g-years")
    # none of the years in the output: refused
    page.fill("#s-point_start", "2000")
    page.fill("#s-point_end", "2005")
    assert page.is_disabled("#write")
    page.fill("#s-point_start", "2011")
    page.fill("#s-point_end", "2012")


def through_output(page):
    """Carry on from where the matchups are saved, once they are confirmed,
    as it starts (and with a core, for matchup alone, which asks for it there)."""
    page.wait_for_selector("#view-output:not([hidden])")
    if page.is_visible("#f-cores"):
        page.select_option("#f-cores", "1")
    page.wait_for_function("!document.getElementById('output-run').disabled")
    page.click("#output-run")


def pick_cores(page):
    """Choose a core on the report options, which matchup is given with them."""
    if page.is_visible("#v-cores"):
        page.select_option("#v-cores", "1")


def recipe_settings(app, **changes):
    settings = dict(app.recipes_page.form)
    settings.update(changes)
    if not settings.get("start"):
        settings["start"] = "2011"
    if not settings.get("end"):
        settings["end"] = "2012"
    # the app's window has no default
    if not settings.get("cores"):
        settings["cores"] = "1"
    return settings


def wait_for(condition, timeout=90):
    deadline = time.time() + timeout
    while not condition():
        if time.time() > deadline:
            raise AssertionError("timed out waiting")
        time.sleep(0.05)


def page_state(body):
    return json.loads(
        re.search(
            r'<script type="application/json" id="state">(.*?)</script>', body, re.S
        ).group(1)
    )


class TestQuestions:
    def test_a_terminal_is_asked_with_input(self, monkeypatch):
        monkeypatch.setattr(
            "builtins.input", lambda question: f"typed after {question}"
        )
        assert prompts.ask("Go? ", ("y", "n")) == "typed after Go? "

    def test_an_answerer_takes_the_questions(self):
        asked = []

        def answerer(question, choices, details):
            asked.append((question, choices, details))
            return "y"

        with prompts.answered_by(answerer):
            assert prompts.interactive()
            assert prompts.ask("Go? ", ["y", "n"]) == "y"
            assert prompts.ask("These? ", ["y", "n"], details={"kind": "x"}) == "y"
        assert asked == [
            ("Go? ", ("y", "n"), None),
            ("These? ", ("y", "n"), {"kind": "x"}),
        ]
        # and afterwards the terminal is asked again
        assert prompts._answerer is None

    def test_a_terminal_is_not_shown_the_details(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda question: question)
        assert prompts.ask("Go? ", ("y", "n"), details={"kind": "x"}) == "Go? "

    def test_whether_anyone_can_be_asked_follows_the_terminal(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
        assert not prompts.interactive()
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        assert prompts.interactive()

    def test_the_retry_questions_go_through_it(self):
        # asked when observations could not be downloaded
        answers = iter(["maybe", "yes", "soon", "-1", "15"])
        asked = []

        def answerer(question, choices, details):
            asked.append(choices)
            return next(answers)

        with prompts.answered_by(answerer):
            assert _ask_yes_no("Try again? (y/n) ") is True
            assert _ask_minutes("How long? ") == 15
        assert asked == [("y", "n"), ("y", "n"), None, None, None]


class TestConsole:
    def test_the_page_reads_what_is_new(self):
        changes = []
        console = Console(lambda: changes.append(1))
        console.write("one\n")
        console.write("")
        console.write("two\n")
        first = console.since(0, None)
        console.write("three\n")
        later = console.since(first["last"], first["epoch"])

        assert (first["text"], first["reset"]) == ("one\ntwo\n", True)
        assert (later["text"], later["reset"]) == ("three\n", False)
        assert len(changes) == 3

    def test_clearing_starts_the_page_afresh(self):
        console = Console(lambda: None)
        console.write("old\n")
        seen = console.since(0, None)
        console.clear()
        console.write("new\n")
        update = console.since(seen["last"], seen["epoch"])

        assert (update["text"], update["reset"]) == ("new\n", True)

    def test_only_the_latest_output_is_kept(self, monkeypatch):
        monkeypatch.setattr(Console, "LIMIT", 10)
        console = Console(lambda: None)
        for number in range(5):
            console.write(f"line {number}\n")
        update = console.since(1, console.epoch)

        # the page had missed some, so is sent what is left
        assert (update["text"], update["reset"], update["dropped"]) == (
            "line 4\n",
            True,
            True,
        )


def run_script(tmp_path, source, answers=()):
    """Run a script as the window does, answering its questions in turn.
    Returns what it printed, the questions it asked and the Run."""
    script = tmp_path / "script.py"
    script.write_text(textwrap.dedent(source))
    console = Console(lambda: None)
    questions = []
    answers = iter(answers)
    finished = threading.Event()

    def ask(question, choices, respond, details):
        questions.append((question, choices))
        respond(next(answers))

    run = Run(
        ["script", str(script)], str(tmp_path), console, ask, lambda run: finished.set()
    )
    run.start()
    assert finished.wait(90)
    return console.since(0, None)["text"], questions, run


class TestRun:
    def test_output_and_questions_reach_the_window(self, tmp_path):
        output, questions, run = run_script(
            tmp_path,
            """
            from oceanval import prompts
            print("before")
            answer = prompts.ask("Carry on? (y/n) ", ("y", "n"))
            print(f"answered {answer}")
            print("\\rprogress 50%", end="", flush=True)
            print("\\rprogress 100%")
            """,
            answers=["y"],
        )

        assert questions == [("Carry on? (y/n) ", ["y", "n"])]
        assert "before\n" in output
        assert "answered y\n" in output
        # redrawn lines as the terminal would get them
        assert "\rprogress 50%\rprogress 100%\n" in output
        assert (run.returncode, run.stopped) == (0, False)

    def test_an_error_is_shown_and_fails_the_run(self, tmp_path):
        output, _, run = run_script(tmp_path, "raise RuntimeError('broken')")

        assert "RuntimeError: broken" in output
        assert run.returncode != 0

    def test_it_runs_in_the_directory_worked_in_with_this_oceanval(self, tmp_path):
        output, _, _ = run_script(
            tmp_path,
            "import os, oceanval; print(os.getcwd()); print(oceanval.__file__)",
        )

        assert f"{tmp_path}\n" in output
        # not whichever oceanval happens to be installed
        assert f"{oceanval.__file__}\n" in output

    def test_stopping(self, tmp_path):
        script = tmp_path / "script.py"
        script.write_text(
            "import time\nprint('started', flush=True)\ntime.sleep(120)\n"
        )
        console = Console(lambda: None)
        finished = threading.Event()
        run = Run(
            ["script", str(script)],
            str(tmp_path),
            console,
            None,
            lambda run: finished.set(),
        )
        run.start()
        wait_for(lambda: "started" in console.since(0, None)["text"])
        run.stop()

        assert finished.wait(20)
        assert run.stopped
        assert run.returncode != 0

    def test_a_question_can_arrive_in_pieces(self):
        console = Console(lambda: None)
        asked = []
        run = Run(
            [],
            ".",
            console,
            lambda question, choices, respond, details: asked.append((question, details)),
            None,
        )
        question = (
            QUESTION_MARKER
            + json.dumps({"question": "Go? ", "choices": None, "details": {"kind": "x"}})
            + "\n"
        )
        left = ""
        for character in "before" + question + "after":
            left = run._take(left + character)

        assert asked == [("Go? ", {"kind": "x"})]
        assert console.since(0, None)["text"] + left == "beforeafter"

    def test_news_of_how_it_is_going_is_not_output(self):
        console = Console(lambda: None)
        told = []
        run = Run([], ".", console, None, None, told=told.append)
        news = PROGRESS_MARKER + json.dumps({"kind": "matchup", "key": "k", "state": "made"}) + "\n"
        left = ""
        # in pieces, as a question can be, and anything else is printed as it was
        for character in "before" + news + "after" + PROGRESS_MARKER + "[1]\n":
            left = run._take(left + character)

        assert told == [{"kind": "matchup", "key": "k", "state": "made"}]
        assert console.since(0, None)["text"] + left == "beforeafter" + PROGRESS_MARKER + "[1]\n"


class TestChildProcess:
    def test_questions_are_written_out_and_the_answer_read_back(
        self, monkeypatch, capfd
    ):
        monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
        answer = app_child._ask_the_window("Go? (y/n) ", ["y", "n"])

        assert answer == "n"
        assert capfd.readouterr().out == (
            QUESTION_MARKER
            + json.dumps({"question": "Go? (y/n) ", "choices": ["y", "n"]})
            + "\n"
        )
        # with nothing left to read, as input() does
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        with pytest.raises(EOFError):
            app_child._ask_the_window("Go? ", None)

    def test_news_is_written_out_for_the_window_alone(self, capfd):
        news = {"kind": "matchup", "key": "gridded|temperature|nsbc|", "state": "matching"}
        # at a terminal, nothing is said
        prompts.tell(news)
        assert capfd.readouterr().out == ""
        with prompts.told_by(app_child._tell_the_window):
            prompts.tell(news)
        assert capfd.readouterr().out == PROGRESS_MARKER + json.dumps(news) + "\n"

    def test_another_process_can_tell_the_window_only_by_lines_on_stdout(self):
        # at a terminal, or told otherwise, there is no line for it to write
        assert prompts.news_marker() is None
        with prompts.told_by(lambda news: None):
            assert prompts.news_marker() is None
        with prompts.told_by(app_child._tell_the_window, PROGRESS_MARKER):
            assert prompts.news_marker() == PROGRESS_MARKER
            with prompts.told_by(lambda news: None):
                assert prompts.news_marker() is None
            assert prompts.news_marker() == PROGRESS_MARKER
        assert prompts.news_marker() is None

    def test_details_go_with_the_question(self, monkeypatch, capfd):
        monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
        app_child._ask_the_window("These? ", ["y", "n"], {"kind": "matchups"})

        assert capfd.readouterr().out == (
            QUESTION_MARKER
            + json.dumps(
                {"question": "These? ", "choices": ["y", "n"], "details": {"kind": "matchups"}}
            )
            + "\n"
        )

    def test_an_answer_can_come_with_settings(self, monkeypatch):
        # yes to the matchups, with the report's options for the interim report
        settings = {"live_validation": {"concise": False}}
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(ANSWER_MARKER + json.dumps({"answer": "y", "settings": settings}) + "\n"),
        )
        answer = app_child._ask_the_window("Happy? (y/n) ", ["y", "n"])

        assert answer == "y"
        assert answer.settings == settings
        monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
        assert not hasattr(app_child._ask_the_window("Happy? ", None), "settings")

    def test_validate_is_given_its_arguments(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            oceanval, "validate", lambda **arguments: called.append(arguments)
        )
        # main replaces it where no browser can be opened
        monkeypatch.setattr(webbrowser, "open", webbrowser.open)
        app_child.main(["validate", json.dumps({"data_dir": "/matchups", "pdf": True})])

        assert called == [{"data_dir": "/matchups", "pdf": True}]

    def test_compare_is_given_its_arguments(self, monkeypatch):
        called = []
        monkeypatch.setattr(oceanval, "compare", lambda **arguments: called.append(arguments))
        monkeypatch.setattr(webbrowser, "open", webbrowser.open)
        arguments = {"model_dict": {"control": "/a", "mixing": "/b"}, "out_dir": "/c"}
        app_child.main(["compare", json.dumps(arguments)])

        assert called == [arguments]

    def test_a_matchup_script_leaves_validate_to_the_window(self, tmp_path, monkeypatch, capsys):
        script = tmp_path / "matchup.py"
        script.write_text(
            "import oceanval\nprint('matched')\noceanval.validate(data_dir='.')\n"
        )
        monkeypatch.setattr(oceanval, "validate", lambda **arguments: pytest.fail("validated"))
        monkeypatch.setattr(webbrowser, "open", webbrowser.open)
        app_child.main(["matchup", str(script)])

        assert capsys.readouterr().out == (
            "matched\nThe report is built next, with the options chosen in the OceanVal window.\n"
        )

    def test_a_script_registers_its_recipes_without_the_servers(self, tmp_path, monkeypatch):
        # the matchups are shown to check first, so are not held up by them
        script = tmp_path / "matchup.py"
        script.write_text(
            "import oceanval\n"
            "oceanval.add_gridded_comparison(name='nitrate', model_variable='N3_n',\n"
            "    recipe={'nitrate': 'woa23'}, climatology=True)\n"
        )
        monkeypatch.setattr(
            oceanval.parsers.nc, "open_thredds", lambda *a, **k: pytest.fail("contacted")
        )
        monkeypatch.setattr(webbrowser, "open", webbrowser.open)
        oceanval.reset()
        try:
            app_child.main(["script", str(script)])

            assert "WOA23" in oceanval.definitions.nitrate.gridded_comparisons
        finally:
            oceanval.reset()

    def test_without_a_display_the_report_is_not_opened(self, monkeypatch, capsys):
        # webbrowser would fall back on a text browser, which would wait for
        # keys the window never sends
        monkeypatch.setattr(sys, "platform", "linux")
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "BROWSER"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(webbrowser, "open", lambda url: pytest.fail("opened"))
        monkeypatch.setattr(
            oceanval,
            "validate",
            lambda **arguments: webbrowser.open("file:///run/report.html"),
        )
        app_child.main(["validate", "{}"])

        assert "file:///run/report.html" in capsys.readouterr().out


class TestRemoteAccess:
    SSH = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")

    def _remote(self, monkeypatch, over_ssh):
        for name in self.SSH:
            monkeypatch.delenv(name, raising=False)
        if over_ssh:
            monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 51234 10.0.0.9 22")

    def _run(self, monkeypatch, capsys, arguments):
        """Run oceanval's main, with no browser to open, and quit at once."""
        monkeypatch.setattr(app_module.recipes_gui, "_can_open_browser", lambda: False)
        monkeypatch.setattr(app_module.App, "_watch", lambda self: self.closed.set())
        app_module.main(arguments)
        return capsys.readouterr().out

    def test_the_ssh_command_is_printed_in_an_ssh_session(self, monkeypatch):
        self._remote(monkeypatch, True)
        monkeypatch.setattr(app_module.recipes_gui.socket, "gethostname", lambda: "server")
        monkeypatch.setattr(app_module.recipes_gui.getpass, "getuser", lambda: "me")

        assert "ssh -L 33007:127.0.0.1:33007 me@server" in app_module.recipes_gui.remote_hint(33007)

    def test_the_generic_hint_is_printed_otherwise(self, monkeypatch):
        self._remote(monkeypatch, False)
        hint = app_module.recipes_gui.remote_hint(33007)

        assert "forward port 33007" in hint
        assert "ssh -L" not in hint

    def test_over_ssh_the_usual_port_is_used_if_it_is_free(self, monkeypatch, capsys):
        self._remote(monkeypatch, True)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        free = sock.getsockname()[1]
        sock.close()
        monkeypatch.setattr(app_module.recipes_gui, "SSH_PORT", free)
        printed = self._run(monkeypatch, capsys, [])

        assert f"127.0.0.1:{free}/?token=" in printed
        assert f"ssh -L {free}:127.0.0.1:{free}" in printed

    def test_over_ssh_another_port_is_used_if_the_usual_one_is_taken(self, monkeypatch, capsys):
        self._remote(monkeypatch, True)
        taken = socket.socket()
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        busy = taken.getsockname()[1]
        monkeypatch.setattr(app_module.recipes_gui, "SSH_PORT", busy)
        try:
            printed = self._run(monkeypatch, capsys, [])
        finally:
            taken.close()

        assert f"127.0.0.1:{busy}/" not in printed
        assert re.search(r"ssh -L (\d+):127\.0\.0\.1:\1 ", printed)

    def test_over_ssh_with_a_browser_any_free_port_is_used(self, monkeypatch, capsys):
        self._remote(monkeypatch, True)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        free = sock.getsockname()[1]
        sock.close()
        monkeypatch.setattr(app_module.recipes_gui, "SSH_PORT", free)
        opened = []
        monkeypatch.setattr(app_module.recipes_gui, "_can_open_browser", lambda: True)
        monkeypatch.setattr(
            app_module.recipes_gui, "_open_browser", lambda url: opened.append(url) or True
        )
        monkeypatch.setattr(app_module.App, "_watch", lambda self: self.closed.set())
        app_module.main([])

        assert len(opened) == 1
        assert "/?token=" in opened[0]
        assert f"127.0.0.1:{free}/" not in opened[0]
        assert "ssh -L" not in capsys.readouterr().out

    def test_no_browser_stops_one_being_opened(self, monkeypatch, capsys):
        self._remote(monkeypatch, False)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("DISPLAY", ":1")
        monkeypatch.delenv("BROWSER", raising=False)
        monkeypatch.delenv("OCEANVAL_BROWSER", raising=False)
        opened = []
        monkeypatch.setattr(
            app_module.recipes_gui, "_open_browser", lambda url: opened.append(url) or True
        )
        monkeypatch.setattr(app_module.App, "_watch", lambda self: self.closed.set())
        app_module.main(["--no-browser"])
        monkeypatch.delenv("OCEANVAL_BROWSER")

        assert opened == []
        assert "Open this link in a web browser" in capsys.readouterr().out

    def test_a_port_that_was_asked_for_must_be_free(self, monkeypatch, capsys):
        self._remote(monkeypatch, True)
        taken = socket.socket()
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        try:
            with pytest.raises(SystemExit):
                self._run(monkeypatch, capsys, ["--port", str(taken.getsockname()[1])])
        finally:
            taken.close()


class TestSetupChecks:
    def test_the_arguments(self, tmp_path):
        (tmp_path / "sim").mkdir()
        arguments, errors = check_setup(dict(SETUP_FORM, domain="nwes"), str(tmp_path))

        assert errors == {}
        # where the output is saved is asked last (see TestOutputChecks)
        assert arguments == {
            "simdir": str(tmp_path / "sim"),
            "ndown": 2,
            "domain": "nwes",
            "start": 2011,
            "end": 2012,
            "exclude": None,
            "require": None,
        }

    def test_the_file_filters_are_words(self, tmp_path):
        (tmp_path / "sim").mkdir()
        form = dict(SETUP_FORM, exclude=" ptrc  5d ", require="grid_T")
        arguments = check_setup(form, str(tmp_path))[0]

        assert (arguments["exclude"], arguments["require"]) == (
            ["ptrc", "5d"],
            ["grid_T"],
        )

    @pytest.mark.parametrize(
        "changes, error",
        [
            ({"simdir": ""}, "simdir"),
            ({"simdir": "nowhere"}, "simdir"),
            ({"ndown": "-1"}, "ndown"),
            ({"ndown": "two"}, "ndown"),
            ({"domain": "arctic"}, "domain"),
            ({"domain": ""}, "domain"),
            ({"start": "x"}, "start"),
            ({"end": "2010"}, "end"),
        ],
    )
    def test_boxes_that_cannot_be_used(self, tmp_path, changes, error):
        (tmp_path / "sim").mkdir()
        assert list(check_setup(dict(SETUP_FORM, **changes), str(tmp_path))[1]) == [
            error
        ]


class TestOutputChecks:
    """The output step's boxes, the last before a matchup starts."""

    def test_the_arguments(self, tmp_path):
        arguments, errors = check_output(dict(OUTPUT_FORM, out="run/m.py"), str(tmp_path))

        assert errors == {}
        assert arguments == {
            "out_dir": str(tmp_path),
            "overwrite": True,
            "previous": True,
            "out": str(tmp_path / "run" / "m.py"),
        }

    def test_the_output_directory(self, tmp_path):
        (tmp_path / "notes.txt").write_text("")

        def check(out_dir):
            return check_output(dict(OUTPUT_FORM, out_dir=out_dir), str(tmp_path))

        # relative to the directory worked in, and it need not be there yet
        assert check("results")[0]["out_dir"] == str(tmp_path / "results")
        assert check(str(tmp_path / "elsewhere"))[0]["out_dir"] == str(tmp_path / "elsewhere")
        assert check("notes.txt")[1] == {"out_dir": "This is a file, not a directory."}

    def test_the_matchups_there_already_are_listed_by_variable_and_source(self, tmp_path):
        write_mixed_matchups(tmp_path / "old")
        assert [
            (row["key"], row["title"], row["kinds"]) for row in existing_matchups(str(tmp_path / "old"))
        ] == [
            ("temperature/COBE2", "Temperature", [{"kind": "gridded", "layer": None}]),
            ("temperature/WOA23", "Temperature", [{"kind": "gridded", "layer": None}]),
            ("nitrate/glodap", "Nitrate", [{"kind": "point", "layer": "all"}]),
            ("nitrate/ICES", "Nitrate", [{"kind": "point", "layer": "surface"}]),
        ]
        # a matchups folder with none in it, as one that did not get far leaves, has none to ask about
        (tmp_path / "empty" / "oceanval_matchups").mkdir(parents=True)
        assert existing_matchups(str(tmp_path / "empty")) == []
        assert existing_matchups(str(tmp_path / "nowhere")) == []

    def test_those_asked_about_depend_on_what_the_run_makes(self, tmp_path):
        write_mixed_matchups(tmp_path / "old")
        made = {"temperature/cobe2", "nitrate/ices"}

        def asked(report):
            return [
                (row["key"], row["made"])
                for row in matchups_asked_about(str(tmp_path / "old"), made, report)
            ]

        # one the run makes is used or replaced, one it does not is used in the report or ignored
        assert asked(True) == [
            ("temperature/COBE2", True), ("temperature/WOA23", False),
            ("nitrate/glodap", False), ("nitrate/ICES", True),
        ]
        # with no report to use them in, only those the run makes are asked about
        assert asked(False) == [("temperature/COBE2", True), ("nitrate/ICES", True)]
        # not knowing what the run makes, each is one it makes
        assert all(row["made"] for row in matchups_asked_about(str(tmp_path / "old")))

    def test_what_is_done_with_the_matchups_there_already_must_be_said(self, tmp_path):
        write_mixed_matchups(tmp_path / "old")
        (tmp_path / "empty" / "oceanval_matchups").mkdir(parents=True)
        # the run makes the gridded COBE2 and the surface ICES matchups again
        made = {"temperature/cobe2", "nitrate/ices"}

        def check(report=True, **changes):
            return check_output(
                dict(OUTPUT_FORM, out_dir="old", **changes), str(tmp_path), report=report, made=made
            )

        # until the user chooses, those the run makes are replaced and the others used,
        # which must be confirmed
        arguments, errors = check(existing_chosen=False)
        assert errors == {"existing_confirmed": ""}
        assert (arguments["overwrite"], arguments["previous"]) == (True, ["temperature/WOA23", "nitrate/glodap"])
        arguments, errors = check(existing_chosen=False, existing_confirmed=True, unused=MIXED_KEYS)
        assert not errors and (arguments["overwrite"], arguments["previous"]) == (True, ["temperature/WOA23", "nitrate/glodap"])
        # with no report, only those the run makes are asked about, and replaced
        arguments = check(report=False, existing_chosen=False, existing_confirmed=True)[0]
        assert (arguments["overwrite"], arguments["previous"]) == (True, True)
        # once chosen, none unused is each used as it is
        arguments, errors = check(existing_confirmed=True)
        assert not errors and (arguments["overwrite"], arguments["previous"]) == (False, True)
        # every one unused: those the run makes are replaced, the others ignored
        arguments, errors = check(existing_confirmed=True, unused=MIXED_KEYS)
        assert not errors and (arguments["overwrite"], arguments["previous"]) == (True, False)
        # one replaced and one ignored: only those used are in the report as they are
        arguments, errors = check(existing_confirmed=True, unused=["temperature/COBE2", "nitrate/glodap", "oxygen/ICES"])
        assert not errors and (arguments["overwrite"], arguments["previous"]) == (
            ["temperature/COBE2"], ["temperature/WOA23", "nitrate/ICES"],
        )
        # with no report, only those the run makes are asked about, to be used or replaced
        assert check(report=False, unused=["nitrate/ICES"])[1] == {"existing_confirmed": ""}
        arguments, errors = check(report=False, existing_confirmed=True, unused=["nitrate/ICES"])
        assert not errors and (arguments["overwrite"], arguments["previous"]) == (["nitrate/ICES"], True)
        arguments = check(report=False, existing_confirmed=True)[0]
        assert (arguments["overwrite"], arguments["previous"]) == (False, True)
        # and none, if the run makes none of them
        arguments, errors = check_output(dict(OUTPUT_FORM, out_dir="old"), str(tmp_path), report=False, made=set())
        assert not errors and (arguments["overwrite"], arguments["previous"]) == (True, True)
        # nor in the demo's own folder, where every model tried keeps its matchups:
        # this run's are made again, and no others are in the report
        arguments, errors = check_output(
            dict(OUTPUT_FORM, out_dir="old"), str(tmp_path), demo_dir=str(tmp_path / "old")
        )
        assert not errors and (arguments["overwrite"], arguments["previous"]) == (True, False)
        # nothing is asked without matchups there
        for out_dir in ("empty", "new"):
            arguments, errors = check_output(dict(OUTPUT_FORM, out_dir=out_dir), str(tmp_path))
            assert not errors and (arguments["overwrite"], arguments["previous"]) == (True, True)

    @pytest.mark.parametrize("changes", [{"out": ""}, {"out": "sim"}])
    def test_a_script_that_cannot_be_written(self, tmp_path, changes):
        (tmp_path / "sim").mkdir()
        assert list(check_output(dict(OUTPUT_FORM, **changes), str(tmp_path))[1]) == ["out"]


class TestBrowsing:
    """The folder browser, and the folders suggested as a path is typed."""

    def test_a_directory(self, tmp_path):
        for name in ("b", "A", "c", ".hidden", "oceanval_matchups"):
            (tmp_path / "run" / name).mkdir(parents=True)
        for name in ("x.nc", "a.nc", "notes.txt"):
            (tmp_path / "run" / name).write_text("")
        found = App(cwd=str(tmp_path)).browse("run")

        assert (found["path"], found["shown"], found["exact"]) == (
            str(tmp_path / "run"),
            "run",
            True,
        )
        assert found["parent"] == str(tmp_path)
        # hidden folders are left out
        assert [folder["name"] for folder in found["folders"]] == [
            "A",
            "b",
            "c",
            "oceanval_matchups",
        ]
        assert found["folders"][0]["path"] == str(tmp_path / "run" / "A")
        assert (found["nc_files"], found["example"]) == (2, "a.nc")
        assert found["has_matchups"]
        assert found["error"] is None

    def test_the_working_directory_to_start_with(self, tmp_path):
        found = App(cwd=str(tmp_path)).browse("")

        assert (found["path"], found["shown"], found["exact"]) == (
            str(tmp_path),
            ".",
            True,
        )
        assert found["places"][0] == {
            "label": "Working directory",
            "path": str(tmp_path),
        }

    def test_what_is_not_a_directory_opens_the_one_above_it(self, tmp_path):
        (tmp_path / "run").mkdir()
        (tmp_path / "run" / "x.nc").write_text("")
        app = App(cwd=str(tmp_path))

        # typed only in part
        assert app.browse("run/2011/0")["path"] == str(tmp_path / "run")
        assert not app.browse("run/2011/0")["exact"]
        assert app.browse("run/x.nc")["path"] == str(tmp_path / "run")

    def test_a_random_directory_of_files(self, app, tmp_path):
        for year in ("2001", "2002"):
            (tmp_path / "sim" / year).mkdir(parents=True)
            for name in ("a_grid_T.nc", "a_restart.nc", "notes.txt"):
                (tmp_path / "sim" / year / name).write_text("")

        found = app.sample_files("sim", "1")
        assert found["directory"] == "found"
        assert found["path"] in ("sim/2001", "sim/2002")
        assert found["directories"] == 2
        # every netCDF file, restart files too, and nothing else
        assert found["files"] == ["a_grid_T.nc", "a_restart.nc"]
        # asking again for another directory
        other = app.sample_files("sim", "1", found["path"])
        assert other["path"] != found["path"]

        assert app.sample_files("", "1")["directory"] == "empty"
        assert app.sample_files("nowhere", "1")["directory"] == "missing"
        assert app.sample_files("sim", "x")["ndown"] is None
        assert app.sample_files("sim", "0")["files"] == []
        assert get_json(app, "/api/files", simdir="sim", ndown="1")[1]["directories"] == 2

    def test_the_top_of_the_file_system(self, tmp_path):
        found = App(cwd=str(tmp_path)).browse("/")
        assert (found["path"], found["parent"]) == ("/", None)

    def test_elsewhere_is_shown_in_full(self, tmp_path):
        (tmp_path / "work").mkdir()
        (tmp_path / "data").mkdir()
        found = App(cwd=str(tmp_path / "work")).browse("../data")
        assert found["shown"] == str(tmp_path / "data")

    @pytest.mark.skipif(os.geteuid() == 0, reason="root can open anything")
    def test_a_directory_that_cannot_be_opened(self, tmp_path):
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0)
        try:
            found = App(cwd=str(tmp_path)).browse("locked")
        finally:
            locked.chmod(0o755)

        assert found["error"] == "You do not have permission to open this directory."
        # so the browser can still go back up
        assert found["parent"] == str(tmp_path)


class TestValidateChecks:
    def test_the_matchups_must_be_there(self, tmp_path):
        errors = check_validate({}, str(tmp_path))[1]
        assert list(errors) == ["data_dir"]
        assert "No matchups found" in errors["data_dir"]

    def test_the_arguments(self, tmp_path):
        write_matchups(tmp_path)
        form = {
            "out_dir": "report",
            "lon_min": "-20",
            "lon_max": "10",
            "lat_min": "40",
            "lat_max": "65",
            "subregions": "global",
            "pdf": True,
            "zip": True,
            "concise": False,
        }
        arguments, errors = check_validate(form, str(tmp_path))

        assert errors == {}
        assert arguments == {
            "data_dir": str(tmp_path),
            "out_dir": str(tmp_path / "report"),
            "lon_lim": [-20, 10],
            "lat_lim": [40, 65],
            "subregions": "global",
            "pdf": True,
            "zip": True,
            "concise": False,
        }

    def test_the_report_is_concise_unless_detailed_is_chosen(self, tmp_path):
        write_matchups(tmp_path)

        assert "concise" not in check_validate({}, str(tmp_path))[0]
        assert check_validate({"concise": True}, str(tmp_path))[0].get("concise") is None
        assert check_validate({"concise": False}, str(tmp_path))[0]["concise"] is False

    def test_a_regions_file_is_found_in_the_directory_worked_in(self, tmp_path):
        write_matchups(tmp_path)
        form = {"subregions": "file", "subregions_file": "regions.nc"}

        assert check_validate(form, str(tmp_path))[1] == {
            "subregions_file": "There is no file at this path."
        }

    def test_the_report_options_need_no_matchups(self, tmp_path):
        # asked for before anything is matched up, with no directories
        form = {"lon_min": "-20", "lon_max": "10", "lat_min": "40", "lat_max": "65",
                "data_dir": "nowhere", "word": True, "fixed_scale": True}
        arguments, errors = check_report(form, str(tmp_path))

        assert errors == {}
        assert arguments == {
            "lon_lim": [-20, 10],
            "lat_lim": [40, 65],
            "fixed_scale": True,
            "word": True,
        }
        assert check_report({"lon_min": "x"}, str(tmp_path))[1]["lon_min"] == "Limits must be numbers."


needs_to_transect = pytest.mark.skipif(
    not oceanval.transects.available(), reason="needs nctoolkit 1.3.6 or later"
)

TRANSECT_FORM = {
    "transect": True,
    "transect_start_lon": "-30",
    "transect_start_lat": "0",
    "transect_end_lon": "-30",
    "transect_end_lat": "65",
}


class TestTransectChecks:
    """The report options step's transect: the boxes of its two ends."""

    @needs_to_transect
    def test_a_north_south_transect(self, tmp_path):
        arguments, errors = check_report(TRANSECT_FORM, str(tmp_path))

        assert errors == {}
        assert arguments == {"transect": {"start": [-30, 0], "end": [-30, 65]}}

    @needs_to_transect
    def test_an_east_west_transect(self, tmp_path):
        form = dict(
            TRANSECT_FORM, transect_start_lon="-10", transect_start_lat="55",
            transect_end_lon="8", transect_end_lat="55",
        )

        assert check_report(form, str(tmp_path)) == (
            {"transect": {"start": [-10, 55], "end": [8, 55]}}, {},
        )

    def test_a_transect_that_is_not_asked_for_is_not_used(self, tmp_path):
        # even though its boxes are filled in, and even if they would not do
        form = dict(TRANSECT_FORM, transect=False, transect_end_lon="x")

        assert check_report(form, str(tmp_path)) == ({}, {})
        assert check_report({}, str(tmp_path)) == ({}, {})

    def test_the_boxes_must_all_be_filled_in(self, tmp_path):
        form = dict(TRANSECT_FORM, transect_end_lat="")
        arguments, errors = check_report(form, str(tmp_path))

        assert arguments == {}
        assert errors == {"transect_end_lat": "Fill in the longitude and latitude of both ends."}
        # all empty: every box is marked
        errors = check_report({"transect": True}, str(tmp_path))[1]
        assert sorted(errors) == sorted(
            ["transect_start_lon", "transect_start_lat", "transect_end_lon", "transect_end_lat"]
        )

    def test_the_boxes_must_be_numbers(self, tmp_path):
        form = dict(TRANSECT_FORM, transect_start_lat="north")

        assert check_report(form, str(tmp_path))[1] == {
            "transect_start_lat": "Coordinates must be numbers."
        }
        assert check_report(dict(TRANSECT_FORM, transect_start_lat="nan"), str(tmp_path))[1] == {
            "transect_start_lat": "Coordinates must be numbers."
        }

    @pytest.mark.parametrize(
        "box, value, message",
        [
            ("transect_start_lon", "-181", "Longitude must be between -180 and 360."),
            ("transect_end_lon", "361", "Longitude must be between -180 and 360."),
            ("transect_start_lat", "-91", "Latitude must be between -90 and 90."),
            ("transect_end_lat", "91", "Latitude must be between -90 and 90."),
        ],
    )
    def test_the_boxes_must_be_on_the_globe(self, tmp_path, box, value, message):
        form = dict(TRANSECT_FORM, **{box: value})
        # keep the line straight, so the range is what is wrong with it
        if box.endswith("_lon"):
            form["transect_start_lon"] = form["transect_end_lon"] = value
        else:
            form["transect_start_lat"] = form["transect_end_lat"] = value

        arguments, errors = check_report(form, str(tmp_path))
        assert arguments == {}
        assert set(errors.values()) == {message}

    def test_the_two_ends_cannot_be_the_same_point(self, tmp_path):
        form = dict(TRANSECT_FORM, transect_end_lat="0")

        assert check_report(form, str(tmp_path))[1] == {
            "transect_end_lon": "The two ends are the same point.",
            "transect_end_lat": "The two ends are the same point.",
        }

    @pytest.mark.parametrize(
        "end", [("-20", "65"), ("-29.99", "0.5"), ("0", "1")]
    )
    def test_a_diagonal_transect_is_refused(self, tmp_path, end):
        form = dict(TRANSECT_FORM, transect_end_lon=end[0], transect_end_lat=end[1])

        arguments, errors = check_report(form, str(tmp_path))
        assert arguments == {}
        # every box is marked, and the reason is the rule
        assert sorted(errors) == sorted(
            ["transect_start_lon", "transect_start_lat", "transect_end_lon", "transect_end_lat"]
        )
        assert set(errors.values()) == {
            "The transect must run north–south or east–west: give both ends the same "
            "longitude, or the same latitude."
        }

    def test_an_nctoolkit_that_cannot_extract_it_is_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(oceanval.transects, "available", lambda: False)

        arguments, errors = check_report(TRANSECT_FORM, str(tmp_path))
        assert arguments == {}
        assert "nctoolkit 1.3.6 or later" in errors["transect"]

    @needs_to_transect
    def test_validating_matchups_made_before_needs_gridded_ones(self, tmp_path):
        write_matchups(tmp_path)
        form = dict(TRANSECT_FORM)

        arguments, errors = check_validate(form, str(tmp_path))
        assert errors == {}
        assert arguments["transect"] == {"start": [-30, 0], "end": [-30, 65]}

        # point matchups only
        gridded = tmp_path / "oceanval_matchups" / "gridded"
        for found in gridded.rglob("*.nc"):
            found.unlink()
        point = tmp_path / "oceanval_matchups" / "point" / "surface" / "temperature" / "bar"
        point.mkdir(parents=True)
        (point / "bar_surface_temperature.csv").write_text("x\n")
        # the page does not ask for a transect then, so one sent is not used
        arguments, errors = check_validate(form, str(tmp_path))
        assert errors == {}
        assert "transect" not in arguments


def write_mixed_matchups(directory):
    """A finished matchup's files, as validate() reads their names: gridded
    temperature against two sources, and point nitrate at the surface and
    through the water column."""
    matchups = os.path.join(directory, "oceanval_matchups")
    for name in (
        "gridded/temperature/COBE2_temperature_surface.nc",
        "gridded/temperature/WOA23_temperature_surface.nc",
        "point/surface/nitrate/ICES/ICES_surface_nitrate.csv",
        "point/all/nitrate/glodap/glodap_all_nitrate.csv",
    ):
        path = os.path.join(matchups, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
    with open(os.path.join(matchups, "short_titles.pkl"), "wb") as saved:
        pickle.dump({"temperature": "Temperature", "nitrate": "Nitrate"}, saved)
    with open(os.path.join(matchups, "variables_matched.pkl"), "wb") as saved:
        pickle.dump(["nitrate", "temperature"], saved)


# the matchups write_mixed_matchups writes, by their keys
MIXED_KEYS = ["temperature/COBE2", "temperature/WOA23", "nitrate/glodap", "nitrate/ICES"]


class TestChoosingMatchups:
    """The report validates the matchups ticked, and asks only for the
    options of those: a transect, regional summaries and fixed colour scales
    for gridded ones, and depth bins for point ones through the water
    column."""

    def test_all_are_ticked_to_start_with(self, tmp_path):
        write_mixed_matchups(tmp_path)
        assert default_validate_form()["unticked"] == []
        arguments, errors = check_validate({}, str(tmp_path))
        assert errors == {}
        # validate's own default
        assert "variables" not in arguments

    def test_those_unticked_are_left_out(self, tmp_path):
        write_mixed_matchups(tmp_path)
        form = {"unticked": ["temperature/WOA23", "nitrate/ICES"]}
        arguments, errors = check_validate(form, str(tmp_path))
        assert errors == {}
        assert arguments["variables"] == ["temperature/COBE2", "nitrate/glodap"]
        assert app_module._validate_call(arguments, str(tmp_path)).endswith(
            'variables=["temperature/COBE2", "nitrate/glodap"])'
        )

    def test_one_must_be_ticked(self, tmp_path):
        write_mixed_matchups(tmp_path)
        errors = check_validate({"unticked": MIXED_KEYS}, str(tmp_path))[1]
        assert errors == {"variables": "Tick at least one dataset to validate."}

    def test_those_unticked_in_another_directory_are_not_a_choice_here(self, tmp_path):
        write_mixed_matchups(tmp_path)
        arguments, errors = check_validate({"unticked": ["oxygen/WOA23"]}, str(tmp_path))
        assert errors == {}
        assert "variables" not in arguments

    def test_the_options_for_gridded_matchups_need_one_ticked(self, tmp_path):
        write_mixed_matchups(tmp_path)
        form = {
            "subregions": "global",
            "fixed_scale": True,
            "transect": True,
            "transect_start_lon": "-30",
            "transect_start_lat": "0",
            "transect_end_lon": "-30",
            "transect_end_lat": "65",
        }
        no_gridded = dict(form, unticked=["temperature/COBE2", "temperature/WOA23"])
        arguments, errors = check_validate(no_gridded, str(tmp_path))
        assert errors == {}
        assert not {"subregions", "fixed_scale", "transect"} & set(arguments)

        one_gridded = dict(form, unticked=["temperature/COBE2"], transect=False)
        arguments, errors = check_validate(one_gridded, str(tmp_path))
        assert errors == {}
        assert (arguments["subregions"], arguments["fixed_scale"]) == ("global", True)

    def test_the_depth_bins_need_a_point_matchup_through_the_water_column(self, tmp_path):
        write_mixed_matchups(tmp_path)
        arguments = check_validate(DEPTH_FORM, str(tmp_path))[0]
        assert arguments["depth_bins"] == [[0, 20], [20, 200], [200, None]]
        # point nitrate at the surface only
        arguments = check_validate(dict(DEPTH_FORM, unticked=["nitrate/glodap"]), str(tmp_path))[0]
        assert "depth_bins" not in arguments

    def test_the_matchups_are_found_as_their_directory_is_typed_in(self, app, tmp_path):
        assert get_json(app, "/api/matchups", data_dir="nowhere")[1] == {
            "status": "missing", "error": "There is no directory at this path.", "matchups": [],
        }
        found = get_json(app, "/api/matchups", data_dir="")[1]
        assert found["status"] == "none"
        assert "No matchups found" in found["error"]

        os.makedirs(tmp_path / "unfinished" / "oceanval_matchups" / "gridded" / "temperature")
        (tmp_path / "unfinished" / "oceanval_matchups" / "gridded" / "temperature"
         / "COBE2_temperature_surface.nc").touch()
        found = get_json(app, "/api/matchups", data_dir="unfinished")[1]
        assert found["status"] == "unfinished"
        assert "did not finish" in found["error"]

        write_mixed_matchups(tmp_path / "done")
        found = get_json(app, "/api/matchups", data_dir=str(tmp_path / "done"))[1]
        assert (found["status"], found["error"]) == ("found", None)
        assert [(m["kind"], m["key"], m["title"]) for m in found["matchups"]] == [
            ("gridded", "temperature/COBE2", "Temperature"),
            ("gridded", "temperature/WOA23", "Temperature"),
            ("point", "nitrate/glodap", "Nitrate"),
            ("point", "nitrate/ICES", "Nitrate"),
        ]
        # with the token only, as for the rest of the window
        assert _request(app, "/api/matchups", token="wrong", data_dir="done")[0] == 403

    def test_a_matchup_and_validate_run_lists_those_it_is_to_make(
        self, app, tmp_path, monkeypatch
    ):
        """Unticked, they are still matched up, but left out of the interim
        report and the report."""
        validated = []
        start_run = app.start_run
        monkeypatch.setattr(
            app,
            "start_run",
            lambda args, label: validated.append(args) if args[0] == "validate" else start_run(args, label),
        )
        listed = [
            {"kind": "gridded", "variable": "temperature", "source": "COBE2", "layer": None,
             "vertical": False, "title": "Temperature", "key": "temperature/COBE2"},
            {"kind": "point", "variable": "temperature", "source": "ICES", "layer": "all",
             "vertical": True, "title": "Temperature", "key": "temperature/ICES"},
        ]
        details = dict(MATCHUPS, matchups=listed)
        script = tmp_path / "run.py"
        script.write_text(
            textwrap.dedent(
                f"""
                import json
                from oceanval import prompts
                question = "Are you happy with these matchups? (y/n) "
                answer = prompts.ask(question, ("y", "n"), details={details!r})
                print("settings:", json.dumps(answer.settings, sort_keys=True))
                """
            )
        )
        app.action = "matchup_validate"
        app.start_run(["matchup", str(script)], "python run.py")
        wait_for(lambda: app.question is not None)
        assert post(app, "/api/answer", {"id": app.question.id, "answer": "y"})[0] == 200
        output_continue(app)
        assert get_json(app, "/api/state")[1]["report"]["matchups"] == listed

        # none ticked
        form = {"unticked": ["temperature/COBE2", "temperature/ICES"], "cores": "1"}
        status, reply = post(app, "/api/report", {"form": form})
        assert (status, reply["errors"]) == (400, {"variables": "Tick at least one dataset to validate."})

        # the gridded one unticked, so its options are not used
        form = {"unticked": ["temperature/COBE2"], "subregions": "global", "fixed_scale": True, "cores": "1"}
        assert post(app, "/api/report", {"form": dict(form, **DEPTH_FORM)})[0] == 200
        assert app.report_arguments == {
            "data_dir": str(tmp_path),
            "out_dir": str(tmp_path),
            "depth_bins": [[0, 20], [20, 200], [200, None]],
            "variables": ["temperature/ICES"],
        }
        wait_for(lambda: validated)
        text = app.console.since(0, None)["text"]
        assert (
            '"live_validation": {"depth_bins": [[0, 20], [20, 200], [200, null]], '
            '"variables": ["temperature/ICES"]}'
        ) in text
        assert json.loads(validated[0][1])["variables"] == ["temperature/ICES"]

    @pytest.mark.parametrize(
        "previous, variables",
        [
            # the earlier matchups chosen are not listed, so stay in whatever is unticked
            (["nitrate/ICES"], ["temperature/ICES", "nitrate/ICES"]),
            (True, ["temperature/ICES", "temperature/WOA23", "nitrate/glodap", "nitrate/ICES"]),
            (False, ["temperature/ICES"]),
        ],
    )
    def test_the_earlier_matchups_chosen_are_in_the_report_but_not_the_interim_one(
        self, app, tmp_path, monkeypatch, previous, variables
    ):
        validated = []
        start_run = app.start_run
        monkeypatch.setattr(
            app,
            "start_run",
            lambda args, label: validated.append(args) if args[0] == "validate" else start_run(args, label),
        )
        # an earlier run's, of which this run makes temperature/COBE2 again
        write_mixed_matchups(tmp_path)
        listed = [
            {"kind": "gridded", "variable": "temperature", "source": "COBE2", "layer": None,
             "vertical": False, "title": "Temperature", "key": "temperature/COBE2"},
            {"kind": "point", "variable": "temperature", "source": "ICES", "layer": "surface",
             "vertical": False, "title": "Temperature", "key": "temperature/ICES"},
        ]
        script = tmp_path / "run.py"
        script.write_text(
            textwrap.dedent(
                f"""
                import json
                from oceanval import prompts
                answer = prompts.ask("Are you happy with these matchups? (y/n) ", ("y", "n"),
                                     details={dict(MATCHUPS, matchups=listed)!r})
                print("settings:", json.dumps(answer.settings, sort_keys=True))
                """
            )
        )
        app.action = "matchup_validate"
        app.start_run(["matchup", str(script)], "python run.py")
        wait_for(lambda: app.question is not None)
        assert post(app, "/api/answer", {"id": app.question.id, "answer": "y"})[0] == 200
        # those used, as the output step chooses them
        unused = ([] if previous is True else MIXED_KEYS if previous is False
                  else [key for key in MIXED_KEYS if key not in previous])
        output_continue(app, existing_confirmed=True, unused=unused)
        assert app.previous == previous
        assert post(app, "/api/report", {"form": {"unticked": ["temperature/COBE2"], "cores": "1"}})[0] == 200
        expected = {"data_dir": str(tmp_path), "out_dir": str(tmp_path), "variables": variables}
        if previous is not True:
            expected["previous"] = previous
        assert app.report_arguments == expected
        wait_for(lambda: validated)
        assert json.loads(validated[0][1]) == expected
        # the interim report only ever has this run's matchups
        text = app.console.since(0, None)["text"]
        settings = json.loads(re.search(r"settings: (.*)", text).group(1))
        assert settings["live_validation"] == {"variables": variables}


# the depth bins as the page sends them, other than OceanVal's own
DEPTH_FORM = {"depth_bins": [["0", "20"], ["20", "200"], ["200", ""]]}


class TestDepthBinChecks:
    """The report options step's depth bins: a [from, to] pair of boxes for
    each."""

    def test_the_default_bins_are_validates_own_and_not_sent(self, tmp_path):
        assert default_validate_form()["depth_bins"] == [
            ["0", "10"], ["10", "30"], ["30", "60"], ["60", "100"], ["100", "150"],
            ["150", "300"], ["300", "600"], ["600", "1000"], ["1000", ""],
        ]
        assert check_report({}, str(tmp_path)) == ({}, {})
        assert check_report(default_validate_form(), str(tmp_path)) == ({}, {})

    def test_bins_of_the_users_own(self, tmp_path):
        assert check_report(DEPTH_FORM, str(tmp_path)) == (
            {"depth_bins": [[0, 20], [20, 200], [200, None]]}, {},
        )

    def test_they_are_sorted_and_empty_rows_are_ignored(self, tmp_path):
        form = {"depth_bins": [["50", "100.5"], ["", ""], [" 0 ", "50"]]}

        assert check_report(form, str(tmp_path)) == (
            {"depth_bins": [[0, 50], [50, 100.5]]}, {},
        )

    @pytest.mark.parametrize(
        "rows, problem",
        [
            ([["", ""]], "Add at least one bin."),
            ([], "Add at least one bin."),
            ([["", "10"]], "Each bin needs a From depth."),
            ([["0", "ten"]], "Depths must be numbers."),
            ([["-5", "10"]], "Depths must be 0 or more."),
            ([["10", "5"]], "A bin's To depth must be deeper than its From depth."),
            ([["0", ""], ["10", "20"]], "Only the deepest bin can leave To empty, for everything below it."),
            ([["0", "20"], ["10", "30"]], "The bins 0-20m and 10-30m overlap."),
        ],
    )
    def test_bins_that_cannot_be_used_are_refused(self, tmp_path, rows, problem):
        assert check_report({"depth_bins": rows}, str(tmp_path)) == (
            {}, {"depth_bins": problem},
        )

    def test_anything_but_pairs_is_left_out(self, tmp_path):
        form = {"depth_bins": [["0", "20"], ["5"], "20-30", None, ["20", None]]}

        assert app_module._form(form, default_validate_form())["depth_bins"] == [
            ["0", "20"], ["20", ""],
        ]
        assert check_report({"depth_bins": "0-10"}, str(tmp_path))[1] == {
            "depth_bins": "Add at least one bin."
        }


def validations(directory, *names):
    """Directories as validate() leaves them, with the results compare reads."""
    for name in names:
        (directory / name / "oceanval_results" / "annual_mean").mkdir(parents=True)
        (directory / name / "oceanval_report" / "_build" / "html" / "notebooks").mkdir(parents=True)


class TestCompareChecks:
    """The compare step: a name and a validation directory for each
    simulation, and where to build the comparison."""

    def test_the_arguments(self, tmp_path):
        validations(tmp_path, "control", "mixing")
        form = {"name_1": "control", "dir_1": "control", "name_3": " mixing ", "dir_3": str(tmp_path / "mixing"),
                "out_dir": "comparisons"}

        arguments, errors = check_compare(form, str(tmp_path))

        assert errors == {}
        # in the rows' order, relative to the directory worked in, and empty rows left out
        assert list(arguments["model_dict"].items()) == [
            ("control", str(tmp_path / "control")),
            ("mixing", str(tmp_path / "mixing")),
        ]
        assert arguments["out_dir"] == str(tmp_path / "comparisons")
        # by default, the directory worked in
        form.pop("out_dir")
        assert check_compare(form, str(tmp_path))[0]["out_dir"] == str(tmp_path)

    def test_two_simulations_are_needed(self, tmp_path):
        validations(tmp_path, "control")

        errors = check_compare({"name_1": "control", "dir_1": "control"}, str(tmp_path))[1]
        assert errors == {"rows": "Fill in at least two simulations to compare."}
        assert check_compare({}, str(tmp_path))[1] == {
            "rows": "Fill in at least two simulations to compare."
        }

    def test_a_row_needs_a_name_and_a_directory(self, tmp_path):
        validations(tmp_path, "control", "mixing", "deep")
        form = {"name_1": "control", "dir_1": "control", "name_2": "mixing", "dir_2": "mixing",
                "name_3": "deep", "dir_4": "deep"}

        assert check_compare(form, str(tmp_path))[1] == {
            "dir_3": "Enter the directory of this simulation's validation.",
            "name_4": "Give the simulation a name.",
        }

    def test_names_and_directories_are_used_once(self, tmp_path):
        validations(tmp_path, "control", "mixing")
        form = {"name_1": "control", "dir_1": "control", "name_2": "control", "dir_2": "mixing",
                "name_3": "again", "dir_3": "./control/"}

        assert check_compare(form, str(tmp_path))[1] == {
            "name_2": "Each simulation needs a name of its own.",
            "dir_3": "This validation is in another row too.",
        }

    def test_the_directories_hold_validations(self, tmp_path):
        validations(tmp_path, "control")
        (tmp_path / "matchups_only" / "oceanval_matchups").mkdir(parents=True)
        form = {"name_1": "control", "dir_1": "control", "name_2": "mixing", "dir_2": "nowhere",
                "name_3": "matchups", "dir_3": "matchups_only"}

        errors = check_compare(form, str(tmp_path))[1]

        assert errors["dir_2"] == "There is no directory at this path."
        assert errors["dir_3"].startswith("There are no validation results here (no oceanval_results/annual_mean)")
        assert "rows" not in errors

    def test_the_comparison_is_built_in_a_directory(self, tmp_path):
        validations(tmp_path, "control", "mixing")
        (tmp_path / "a_file").write_text("")
        form = {"name_1": "control", "dir_1": "control", "name_2": "mixing", "dir_2": "mixing",
                "out_dir": "a_file"}

        assert check_compare(form, str(tmp_path))[1] == {"out_dir": "This is a file, not a directory."}


def _request(app, route, data=None, token=None, **params):
    token = app.token if token is None else token
    url = f"http://127.0.0.1:{app.port}{route}"
    headers = {}
    if data is None:
        url += "?" + urllib.parse.urlencode(dict(params, token=token))
    else:
        data = json.dumps(data).encode()
        headers = {"Content-Type": "application/json", "X-OceanVal-Token": token}
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def get(app, route, **params):
    return _request(app, route, **params)


def get_json(app, route, **params):
    status, body = _request(app, route, **params)
    return status, json.loads(body)


def post(app, route, body=None, token=None):
    status, reply = _request(app, route, data=body or {}, token=token)
    return status, json.loads(reply)


@pytest.fixture(autouse=True)
def leftover_dir(tmp_path_factory, monkeypatch):
    """The only directory the window looks in for files earlier sessions
    left behind, empty, so the real temp directory never shows a question."""
    directory = tmp_path_factory.mktemp("leftovers")
    monkeypatch.setattr(leftovers, "_directories", lambda: [str(directory)])
    return directory


def _finished_process():
    """The id of a process that has finished, so is not running."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


DEAD_PID = _finished_process()


def _leftover(directory, name="nctoolkit_me_abcdnctoolkit_oceanval_output_p999999_tmp1.nc", size=2048):
    # p999999 stands for a process that has finished
    path = directory / name.replace("p999999", f"p{DEAD_PID}")
    path.write_bytes(b"x" * size)
    return str(path)


@pytest.fixture
def app(tmp_path):
    """An app on the validation page, which its landing page's Validate tile leads to."""
    app = App(cwd=str(tmp_path))
    app.start()
    assert app.view == "home"
    assert app.choose("validate_menu")
    yield app
    app.close()


@pytest.fixture
def runs(app, monkeypatch):
    """The runs the app starts, which are recorded rather than started."""
    started = []
    monkeypatch.setattr(
        app, "start_run", lambda args, label: started.append((args, label))
    )
    return started


class TestServer:
    def test_everything_needs_the_token(self, app):
        assert get(app, "/", token="wrong")[0] == 403
        assert get(app, "/api/state", token="wrong")[0] == 403
        assert get(app, "/api/browse", token="wrong", path="/")[0] == 403
        assert post(app, "/api/quit", token="wrong")[0] == 403
        assert post(app, "/api/forget_sim_dir", {"path": "/x"}, token="wrong")[0] == 403
        assert not app.closed.is_set()

    def test_the_page(self, app):
        status, body = get(app, "/")
        state = page_state(body)

        assert status == 200
        assert (state["token"], state["state"]["view"]) == (app.token, "start")
        # the shared stylesheet and scripts are inlined
        assert "__OCEANVAL_" not in body

    def test_the_simulations_matched_up_before_are_sent_that_still_exist(self, app, tmp_path):
        from oceanval import user_cache

        assert page_state(get(app, "/")[1])["state"]["setup"]["recent"] == []
        (tmp_path / "here").mkdir()
        user_cache.record_sim_dir(str(tmp_path / "gone"))
        user_cache.record_sim_dir(str(tmp_path / "here"))
        state = page_state(get(app, "/")[1])["state"]
        assert state["setup"]["recent"] == [str(tmp_path / "here")]

    def test_a_simulation_matched_up_before_can_be_forgotten(self, app, tmp_path):
        from oceanval import user_cache

        for name in ("one", "two"):
            (tmp_path / name).mkdir()
            user_cache.record_sim_dir(str(tmp_path / name))
        version = app.version
        assert post(app, "/api/forget_sim_dir", {"path": str(tmp_path / "two")})[0] == 200
        assert app.version > version
        assert app.state()["setup"]["recent"] == [str(tmp_path / "one")]
        # what is not there is already forgotten, but something must be named
        assert post(app, "/api/forget_sim_dir", {"path": str(tmp_path / "two")})[0] == 200
        assert post(app, "/api/forget_sim_dir", {})[0] == 409
        assert post(app, "/api/forget_sim_dir", {"path": 3})[0] == 409
        assert app.state()["setup"]["recent"] == [str(tmp_path / "one")]

    def test_choosing(self, app):
        assert post(app, "/api/choose", {"action": "everything"})[0] == 409
        assert post(app, "/api/choose", {"action": "matchup"})[0] == 200
        assert app.view == "setup"
        # only at the start
        assert post(app, "/api/choose", {"action": "validate"})[0] == 409
        assert post(app, "/api/back")[0] == 200
        assert app.view == "start"

    def test_the_state_waits_for_a_change(self, app):
        _, state = get_json(app, "/api/state")
        threading.Timer(0.5, lambda: app.choose("validate")).start()
        started = time.time()
        _, later = get_json(app, "/api/state", version=state["version"])

        assert later["view"] == "validate"
        assert 0.3 < time.time() - started < 15

    def test_the_live_check(self, app, tmp_path):
        write_simulation(tmp_path / "sim")
        (tmp_path / "matchup.py").write_text("")

        _, found = get_json(
            app, "/api/probe", simdir="sim", ndown="2", out="matchup.py"
        )
        assert (found["directory"], found["ndown"], found["files"]) == ("found", 2, 2)
        assert found["years"] == [2011, 2012]
        assert found["out_exists"]
        # with no files where it was told to look, it says so and no more
        _, found = get_json(app, "/api/probe", simdir="sim", ndown="1", out="")
        assert found["files"] == 0
        assert "suggestion" not in found
        _, found = get_json(app, "/api/probe", simdir="nowhere", ndown="2", out="")
        assert found["directory"] == "missing"

    @pytest.mark.parametrize(
        "layout, expected",
        [
            ("YYYY/MM", {"pattern": "YYYY/MM/**.nc", "ndown": 2}),
            ("MM", {"pattern": "MM/*.nc", "ndown": 1}),
            ("flat", {"pattern": "*.nc", "ndown": 0}),
            ("YYYY/MM-short", None),
            ("YYYY/MM-names", None),
        ],
    )
    def test_the_live_check_finds_the_layout_of_the_folders(self, app, tmp_path, layout, expected):
        write_folders(tmp_path / "sim", layout)
        # whatever depth is typed, or none
        for ndown in ("", "0", "5"):
            _, found = get_json(app, "/api/probe", simdir="sim", ndown=ndown, out="")
            assert found["layout"] == expected

    def test_the_live_check_finds_no_layout_without_files_or_a_directory(self, app, tmp_path):
        os.makedirs(tmp_path / "empty")
        _, found = get_json(app, "/api/probe", simdir="empty", ndown="", out="")
        assert found["layout"] is None
        _, found = get_json(app, "/api/probe", simdir="nowhere", ndown="", out="")
        assert found["layout"] is None

    def test_the_live_check_of_the_output_directory(self, app, tmp_path):
        write_mixed_matchups(tmp_path / "old")
        # as a matchup that did not get far leaves it, with none in it
        (tmp_path / "started" / "oceanval_matchups").mkdir(parents=True)
        (tmp_path / "notes.txt").write_text("")

        def probe(out_dir):
            found = get_json(
                app, "/api/probe", simdir="", ndown="", out="", out_dir=out_dir
            )[1]
            return found["out_dir"], found["out_dir_has_matchups"]

        # empty is the directory worked in
        assert probe("") == ("found", False)
        assert probe("old") == ("found", True)
        assert probe("started") == ("found", False)
        assert probe("new") == ("missing", False)
        assert probe("notes.txt") == ("file", False)
        # each matchup there, for the page to ask what is done with it
        found = get_json(app, "/api/probe", simdir="", ndown="", out="", out_dir="old")[1]
        assert [row["key"] for row in found["out_dir_matchups"]] == MIXED_KEYS
        assert found["out_dir_is_demo"] is False

    def test_the_live_check_counts_what_passes_the_file_filters(self, app, tmp_path):
        write_simulation(tmp_path / "sim", tracers=True)

        def probe(**filters):
            return get_json(
                app, "/api/probe", simdir="sim", ndown="2", out="", **filters
            )[1]

        assert (probe()["files"], probe()["all_files"]) == (4, 4)
        found = probe(exclude="ptrc")
        assert (found["files"], found["all_files"]) == (2, 4)
        assert found["years"] == [2011, 2012]
        assert probe(require="ptrc grid")["files"] == 0
        # the depth is right, so it is the filters that need changing
        found = probe(require="nothing_like_this")
        assert (found["files"], found["all_files"]) == (0, 4)

    def test_the_years_of_the_output_are_read_from_its_times(self, app, tmp_path):
        # the names say 2011 and 2012, the times 2011 to 2013
        write_simulation(tmp_path / "sim")
        from netCDF4 import Dataset

        with Dataset(str(next((tmp_path / "sim" / "2012").rglob("*.nc"))), "a") as handle:
            handle["time"][:] = [400.0]
        # only for the recipes window, from the simulation step
        get_json(app, "/api/probe", simdir="sim", ndown="2", out="")
        assert app.state()["setup"]["output_years"] is None
        assert post(app, "/api/choose", {"action": "matchup"})[0] == 200

        _, found = get_json(app, "/api/probe", simdir="sim", ndown="2", out="")
        assert found["years"] == [2011, 2012]
        wait_for(lambda: app.state()["setup"]["output_years"]["status"] == "done")
        read = app.state()["setup"]["output_years"]
        assert read == {"key": found["output_key"], "status": "done", "years": [2011, 2013]}
        assert app.output_years_of(app._output_years["key"]) == [2011, 2013]
        # another simulation's are read instead
        write_simulation(tmp_path / "other", years=(1990,))
        _, other = get_json(app, "/api/probe", simdir="other", ndown="2", out="")
        assert other["output_key"] != found["output_key"]
        assert app.state()["setup"]["output_years"]["key"] == other["output_key"]
        wait_for(lambda: app.state()["setup"]["output_years"]["status"] == "done")
        assert app.state()["setup"]["output_years"]["years"] == [1990, 1990]

    def test_browsing(self, app, tmp_path):
        (tmp_path / "sim").mkdir()
        status, found = get_json(app, "/api/browse", path="")

        assert status == 200
        assert [folder["name"] for folder in found["folders"]] == ["sim"]

    def test_setup_problems_are_sent_back(self, app, runs):
        app.choose("matchup")
        status, reply = post(app, "/api/setup", {"form": {"simdir": "nowhere"}})

        assert status == 400
        assert set(reply["errors"]) == {"simdir", "ndown", "domain", "start", "end"}
        assert app.view == "setup"

    @pytest.mark.parametrize("action", ["matchup", "matchup_validate"])
    def test_matching_up(self, app, tmp_path, runs, action):
        """create_recipes reads the simulation, its window is the recipes
        step, and writing the script there starts it."""
        write_simulation(tmp_path / "sim")
        app.choose(action)
        assert post(app, "/api/setup", {"form": SETUP_FORM})[0] == 200
        # whether there is data of your own comes before the recipes
        assert app.view == "own_data"
        assert post(app, "/api/own_data", {"answer": False})[0] == 200
        wait_for(lambda: app.view == "recipes")

        status, body = get(app, "/recipes/")
        state = page_state(body)
        assert status == 200
        assert state["context"]["app"] == {"action": action, "own_vertical": False, "own_point": False}
        # its requests are answered by the app's own server
        assert state["token"] == app.token
        # the output directory was chosen with the simulation
        assert state["settings"]["out_dir"] == ""
        assert (state["settings"]["start"], state["settings"]["end"]) == ("", "")

        rows = [
            {
                "variable": "temperature",
                "model_variable": "thetao",
                "selected": ["cobe2"],
            }
        ]
        # no years is not an error: matchup's are left for the script to say
        assert post(app, "/recipes/write", {"rows": rows, "settings": recipe_settings(app)})[0] == 200
        units_match(app)
        wait_for(lambda: runs)
        # a draft in a folder of OceanVal's own, written where the user says once the
        # matchups are confirmed, with nothing in the directory worked in until then
        script = draft(app)
        assert not script.startswith(str(tmp_path))
        assert not (tmp_path / "matchup.py").exists()
        text = open(script).read()
        assert f'    out_dir="{os.path.dirname(script)}",\n' in text

        # to validate as well, the report is built after the script, with the
        # options chosen once the matchups are checked
        kind = "script" if action == "matchup" else "matchup"
        assert runs == [([kind, script], f"python {script}")]
        assert '\noceanval.add_gridded_comparison(\n    name="temperature",' in text
        if action == "matchup":
            # the report is left for later
            assert "\n# oceanval.validate(" in text
            assert "\noceanval.validate(" not in text
        else:
            # of the draft's directory, until the output step says where the matchups go
            assert f'\noceanval.validate(\n    data_dir="{os.path.dirname(script)}",' in text
        # chosen once the matchups are confirmed
        assert app.results_dir is None
        # the recipes step is over, so its link leads back to the app
        assert 'id="view-start"' in get(app, "/recipes/")[1]

    def test_the_report_options_are_written_into_the_script(
        self, app, tmp_path, runs, monkeypatch
    ):
        write_simulation(tmp_path / "sim")
        app.choose("matchup_validate")
        post(app, "/api/setup", {"form": SETUP_FORM})
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "recipes")
        rows = [{"variable": "temperature", "model_variable": "thetao", "selected": ["cobe2"]}]
        assert post(app, "/recipes/write", {"rows": rows, "settings": recipe_settings(app)})[0] == 200
        units_match(app)
        wait_for(lambda: runs)
        # as when the script has run without asking about the matchups, after the
        # output step said where it goes
        chosen_output(app, tmp_path)
        app.view = "report_options"
        # COBE-SST 2 is gridded, so the step can ask for a transect
        assert get_json(app, "/api/state")[1]["report"]["gridded"] is True
        form = {"lon_min": "-20", "lon_max": "10", "lat_min": "40", "lat_max": "65",
                "pdf": True, "concise": False}
        assert post(app, "/api/report", {"form": form})[0] == 200
        text = open(tmp_path / "matchup.py").read()

        # so it can be run again from a terminal, as the window ran it
        assert (
            "\noceanval.validate(\n    lon_lim=[-20, 10],\n    lat_lim=[40, 65],\n"
            "    pdf=True,\n    concise=False,\n)\n"
        ) in text
        # the subset is the report's, not matchup's, which the interim report
        # matchup builds as it goes has too
        call = text[text.index("oceanval.matchup(") : text.index("\noceanval.validate(")]
        assert "    lon_lim=" not in call
        assert (
            '    live_validation={"lon_lim": [-20, 10], "lat_lim": [40, 65], "concise": False},\n)'
        ) in call
        # matchup has run already, so builds none
        assert get_json(app, "/api/state")[1]["interim"] is None
        assert runs[-1] == (
            ["validate", json.dumps({"data_dir": str(tmp_path), "out_dir": str(tmp_path),
                                     "lon_lim": [-20.0, 10.0], "lat_lim": [40.0, 65.0],
                                     "pdf": True, "concise": False})],
            'oceanval.validate(data_dir=".", out_dir=".", lon_lim=[-20, 10], '
            'lat_lim=[40, 65], pdf=True, concise=False)',
        )

    def report_step(self, app, tmp_path, runs, selected):
        """Get to the report options step, after the recipes window chose
        these observations of temperature."""
        write_simulation(tmp_path / "sim")
        app.choose("matchup_validate")
        post(app, "/api/setup", {"form": SETUP_FORM})
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "recipes")
        rows = [{"variable": "temperature", "model_variable": "thetao", "selected": selected}]
        assert post(app, "/recipes/write", {"rows": rows, "settings": recipe_settings(app)})[0] == 200
        units_match(app)
        wait_for(lambda: runs)
        # as when the script has run without asking about the matchups, after the
        # output step said where it goes
        chosen_output(app, tmp_path)
        app.view = "report_options"

    def test_a_transect_that_cannot_be_used_is_refused(self, app, tmp_path, runs):
        self.report_step(app, tmp_path, runs, ["cobe2"])
        diagonal = dict(TRANSECT_FORM, transect_end_lon="-20")

        status, reply = post(app, "/api/report", {"form": diagonal})

        assert status == 400
        assert set(reply["errors"]) == {
            "transect_start_lon", "transect_start_lat", "transect_end_lon", "transect_end_lat",
        }
        # the step carries on being asked, and nothing was run or written with it
        assert app.view == "report_options"
        assert len(runs) == 1
        assert "transect=" not in open(tmp_path / "matchup.py").read()
        # the boxes are kept, to be put right
        assert get_json(app, "/api/state")[1]["validate"]["form"]["transect_end_lon"] == "-20"

    def test_a_transect_is_not_asked_for_without_gridded_matchups(
        self, app, tmp_path, runs
    ):
        self.report_step(app, tmp_path, runs, ["ices"])
        assert get_json(app, "/api/state")[1]["report"]["gridded"] is False

        # the page does not send one, but if it did, there is nothing to draw it for
        assert post(app, "/api/report", {"form": TRANSECT_FORM})[0] == 200
        assert "transect=" not in open(tmp_path / "matchup.py").read()
        assert '"transect"' not in runs[-1][0][1]

    def vertical_report_step(self, app, tmp_path, runs):
        """Get to the report options step, after the recipes window chose
        ICES's temperature through the water column."""
        write_simulation(tmp_path / "sim")
        app.choose("matchup_validate")
        post(app, "/api/setup", {"form": SETUP_FORM})
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "recipes")
        rows = [{
            "variable": "temperature", "model_variable": "thetao", "selected": ["ices"],
            "point": {"ices": {"vertical": True}},
        }]
        settings = recipe_settings(app, thickness="z_level")
        assert post(app, "/recipes/write", {"rows": rows, "settings": settings})[0] == 200
        units_match(app)
        wait_for(lambda: runs)
        chosen_output(app, tmp_path)
        app.view = "report_options"

    def test_depth_bins_are_written_into_the_script(
        self, app, tmp_path, runs, monkeypatch
    ):
        self.vertical_report_step(app, tmp_path, runs)
        assert get_json(app, "/api/state")[1]["report"]["vertical"] is True

        assert post(app, "/api/report", {"form": DEPTH_FORM})[0] == 200
        text = open(tmp_path / "matchup.py").read()

        # so it can be run again from a terminal, as the window ran it
        assert "\noceanval.validate(\n    depth_bins=[[0, 20], [20, 200], [200, None]],\n)\n" in text
        # and the interim report matchup builds as it goes has them too
        call = text[text.index("oceanval.matchup(") : text.index("\noceanval.validate(")]
        assert '    live_validation={"depth_bins": [[0, 20], [20, 200], [200, None]]},\n)' in call
        assert json.loads(runs[-1][0][1])["depth_bins"] == [[0, 20], [20, 200], [200, None]]

    def test_the_default_depth_bins_are_not_written(self, app, tmp_path, runs):
        self.vertical_report_step(app, tmp_path, runs)

        assert post(app, "/api/report", {"form": default_validate_form()})[0] == 200
        assert "depth_bins=" not in open(tmp_path / "matchup.py").read()

    def test_depth_bins_are_not_asked_for_without_vertical_point_matchups(
        self, app, tmp_path, runs
    ):
        self.report_step(app, tmp_path, runs, ["ices"])
        assert get_json(app, "/api/state")[1]["report"]["vertical"] is False

        # the page does not send them, but if it did, there is nothing to bin
        assert post(app, "/api/report", {"form": DEPTH_FORM})[0] == 200
        assert "depth_bins=" not in open(tmp_path / "matchup.py").read()
        assert '"depth_bins"' not in runs[-1][0][1]

    def test_the_users_own_point_data_through_the_water_column_is_vertical(self, app):
        assert app._report_vertical() is None
        app._script_writer = (None, ({}, [], {}, {}, {}))
        assert app._report_vertical() is False
        app.own_data["point"].append({"name": "mine", "vertical": False})
        assert app._report_vertical() is False
        app.own_data["point"].append({"name": "deep", "vertical": True})
        assert app._report_vertical() is True

    def test_the_users_own_gridded_data_is_gridded(self, app):
        assert app._report_gridded() is None
        app._script_writer = (None, ({}, [], {}, {}, {}))
        assert app._report_gridded() is False
        app.own_data["gridded"].append({"name": "mine"})
        assert app._report_gridded() is True

    def test_the_recipes_step_always_asks_and_needs_a_thickness(self, app, tmp_path, runs):
        write_simulation(tmp_path / "sim")
        app.choose("matchup")
        post(app, "/api/setup", {"form": SETUP_FORM})
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "recipes")
        rows = [
            {
                "variable": "temperature",
                "model_variable": "thetao",
                "selected": ["nsbc"],
                "gridded": {"nsbc": {"start": "", "end": "", "vertical": True}},
            }
        ]
        settings = recipe_settings(app, ask=False)

        status, reply = post(app, "/recipes/write", {"rows": rows, "settings": settings})
        assert status == 400
        assert "a thickness is needed" in reply["setting_errors"]["thickness"]
        assert not (tmp_path / "matchup.py").exists()

        settings["thickness"] = "z_level"
        assert post(app, "/recipes/write", {"rows": rows, "settings": settings})[0] == 200
        units_match(app)
        wait_for(lambda: runs)
        text = open(draft(app)).read()
        # asking is matchup's default, so it is not written
        assert "    ask=" not in text
        assert '    thickness="z_level",\n' in text

    def test_own_data_through_the_water_column_needs_a_thickness(self, app, tmp_path, runs):
        write_simulation(tmp_path / "sim")
        app.choose("matchup")
        post(app, "/api/setup", {"form": SETUP_FORM})
        app.own_data["point"].append(
            {"name": "cruise", "source": "mine", "model_variable": "thetao",
             "obs_path": "points", "vertical": True}
        )
        post(app, "/api/own_data", {"answer": True})
        post(app, "/api/own_next")
        post(app, "/api/own_next")
        wait_for(lambda: app.view == "recipes")

        assert app.recipes_page.context["app"]["own_vertical"] is True
        status, reply = post(app, "/recipes/write", {"rows": [], "settings": recipe_settings(app)})
        assert status == 400
        assert "thickness" in reply["setting_errors"]

    def choose_recipes(self, app, tmp_path, own_gridded=None, own_point=None, ices=False):
        """Get to the units step with temperature (COBE-SST 2) and nitrate
        (WOA23) chosen, with temperature from ICES as well if ices, and the
        user's own gridded and point data if given."""
        write_simulation(tmp_path / "sim", tracers=True)
        app.choose("matchup")
        post(app, "/api/setup", {"form": SETUP_FORM})
        if own_gridded:
            app.own_data["gridded"].append(own_gridded)
        if own_point:
            app.own_data["point"].append(own_point)
        own = bool(own_gridded or own_point)
        post(app, "/api/own_data", {"answer": own})
        if own:
            post(app, "/api/own_next")
            post(app, "/api/own_next")
        wait_for(lambda: app.view == "recipes")
        rows = [
            {
                "variable": "temperature",
                "model_variable": "thetao",
                "selected": ["cobe2", "ices"] if ices else ["cobe2"],
            },
            {"variable": "nitrate", "model_variable": "N3_n", "selected": ["woa23"]},
        ]
        assert post(app, "/recipes/write", {"rows": rows, "settings": recipe_settings(app)})[0] == 200
        units_read(app)

    def test_the_units_are_shown_after_the_recipes(self, app, tmp_path, runs):
        self.choose_recipes(app, tmp_path)
        _, state = get_json(app, "/api/state")

        assert state["view"] == "units_table"
        # the draft of the script is written, and nothing runs until it is carried on from
        assert os.path.exists(draft(app))
        assert not (tmp_path / "matchup.py").exists()
        assert runs == []

        # nothing is converted that the page does not send
        assert post(app, "/api/units_continue", {"conversions": {}, "confirmed": True})[0] == 200
        wait_for(lambda: runs)
        assert "obs_multiplier" not in open(draft(app)).read()
        assert app.view == "running"

    def test_nothing_carries_on_while_the_units_are_read(self, app):
        app.view = "units_table"

        status, reply = post(app, "/api/units_continue", {"conversions": {}, "confirmed": True})

        assert status == 409
        assert "still being read" in reply["error"]

    def test_the_units_always_have_to_be_confirmed(self, app, tmp_path, runs):
        """Not just by a button that is disabled: only a confirmation of true
        carries on, whatever conversions are sent."""
        self.choose_recipes(app, tmp_path)

        # each is refused, leaving the step as it was for the next
        for confirmed in (None, False, "true", 1):
            sent = {"conversions": {}}
            if confirmed is not None:
                sent["confirmed"] = confirmed

            status, reply = post(app, "/api/units_continue", sent)

            assert status == 400, confirmed
            assert "Confirm the units" in reply["error"]
            assert app.view == "units_table"
            assert runs == []
        # and it can still be confirmed
        assert post(app, "/api/units_continue", {"conversions": {}, "confirmed": True})[0] == 200
        wait_for(lambda: runs)

    def test_the_units_of_every_matchup_are_listed(self, app, tmp_path, runs):
        obs = tmp_path / "obs.nc"
        xr.Dataset(
            {"chl_obs": (("y", "x"), np.ones((2, 2)), {"units": "mg/m3"})}
        ).to_netcdf(obs)
        own_gridded = {
            "name": "chl",
            "source": "mine",
            "model_variable": "thetao+N3_n",
            "obs_path": str(obs),
            "obs_variable": "chl_obs",
            "obs_multiplier": 5,
        }
        own_point = {
            "name": "cruise",
            "source": "mine",
            "model_variable": "thetao",
            "obs_path": "points",
            "obs_adder": 3,
        }
        self.choose_recipes(app, tmp_path, own_gridded, own_point, ices=True)

        _, state = get_json(app, "/api/state")
        rows = {row["key"]: row for row in state["units"]["rows"]}

        assert state["view"] == "units_table"
        assert list(rows) == [
            "recipe:nitrate:woa23",
            "recipe:temperature:cobe2",
            "point:temperature:ices",
            "own:0",
            "ownpoint:0",
        ]
        # the page shows gridded datasets and point datasets in sections of their own
        assert {key: row["kind"] for key, row in rows.items()} == {
            "recipe:nitrate:woa23": "gridded",
            "recipe:temperature:cobe2": "gridded",
            "point:temperature:ices": "point",
            "own:0": "gridded",
            "ownpoint:0": "point",
        }
        nitrate = rows["recipe:nitrate:woa23"]
        temperature = rows["recipe:temperature:cobe2"]
        mine = rows["own:0"]
        assert nitrate["model"] == {
            "variable": "N3_n",
            "parts": [{"name": "N3_n", "units": "mmol N m-3"}],
        }
        assert (nitrate["obs_variable"], nitrate["obs_units"]) == (
            "n_an",
            "micromoles_per_kilogram",
        )
        assert temperature["model"]["parts"] == [{"name": "thetao", "units": "degC"}]
        assert (temperature["obs_variable"], temperature["obs_units"]) == ("sst", "degC")
        # a sum is listed by its parts, and the file's own units are read
        assert [part["name"] for part in mine["model"]["parts"]] == ["thetao", "N3_n"]
        assert (mine["obs_variable"], mine["obs_units"]) == ("chl_obs", "mg/m3")
        assert (mine["obs_multiplier"], mine["obs_adder"]) == (5, 0)
        # what OceanVal makes of each: WOA23 is per kilogram, the model per volume
        assert (nitrate["check"]["status"], nitrate["check"]["multiplier"]) == (
            "convert",
            1.025,
        )
        assert "1025 kg/m³" in nitrate["check"]["note"]
        assert temperature["check"]["status"] == "same"
        assert mine["check"]["status"] == "unknown"
        assert "different units" in mine["check"]["note"]
        # which the page fills in: the conversion already chosen is unchanged
        assert (nitrate["obs_multiplier"], nitrate["obs_adder"]) == (1, 0)
        ices = rows["point:temperature:ices"]
        assert ices["model"]["parts"] == [{"name": "thetao", "units": "degC"}]
        assert (ices["obs_variable"], ices["obs_units"]) == ("TEMPPR01", "\u00b0C")
        assert ices["check"]["status"] == "same"
        mine_point = rows["ownpoint:0"]
        assert mine_point["obs_units"] is None and "no units" in mine_point["obs_note"]
        assert (mine_point["obs_multiplier"], mine_point["obs_adder"]) == (1, 3)
        assert mine_point["check"]["status"] == "unknown"

    def test_conversions_are_written_into_the_script(self, app, tmp_path, runs):
        own_gridded = {
            "name": "chl",
            "source": "mine",
            "model_variable": "thetao",
            "obs_path": "obs.nc",
            "obs_variable": "chl_obs",
            "obs_multiplier": 5,
        }
        own_point = {
            "name": "cruise",
            "source": "mine",
            "model_variable": "thetao",
            "obs_path": "points",
        }
        self.choose_recipes(app, tmp_path, own_gridded, own_point, ices=True)
        conversions = {
            "recipe:nitrate:woa23": {"multiplier": "0.001", "adder": " "},
            "recipe:temperature:cobe2": {"multiplier": "", "adder": "-273.15"},
            "point:temperature:ices": {"multiplier": "", "adder": "2"},
            "ownpoint:0": {"multiplier": "10", "adder": ""},
            # blank puts your own data's back to the default, which is left out
            "own:0": {"multiplier": "", "adder": "2"},
        }

        assert post(app, "/api/units_continue", {"conversions": conversions, "confirmed": True})[0] == 200
        wait_for(lambda: runs)
        text = open(draft(app)).read()
        ast.parse(text)

        def call(start):
            # the live call, not one commented out for another dataset
            return text[text.index(start) :].split("\n)\n")[0]

        nitrate = call('add_gridded_comparison(\n    name="nitrate"')
        assert "    obs_multiplier=0.001,\n" in nitrate
        assert "obs_adder" not in nitrate
        assert "    obs_adder=-273.15,\n" in call('add_gridded_comparison(\n    name="temperature"')
        assert "    obs_adder=2,\n" in call('add_point_comparison(\n    name="temperature"')
        assert "    obs_multiplier=10," in call('name="cruise"')
        chl = call('name="chl"')
        assert "obs_multiplier" not in chl
        assert "    obs_adder=2," in chl

    def test_conversions_that_cannot_be_used_are_sent_back(self, app, tmp_path, runs):
        self.choose_recipes(app, tmp_path)

        for conversions in (
            {"recipe:nitrate:woa23": {"multiplier": "x", "adder": ""}},
            {"recipe:nitrate:woa23": {"multiplier": "0", "adder": ""}},
            {"recipe:nitrate:woa23": {"multiplier": "", "adder": "inf"}},
            {"recipe:salinity:woa23": {"multiplier": "2", "adder": ""}},
        ):
            status, reply = post(app, "/api/units_continue", {"conversions": conversions, "confirmed": True})
            assert status == 400
            assert set(reply["errors"]) == set(conversions)
        assert app.view == "units_table"
        assert runs == []

    def test_own_data_is_added_one_at_a_time(self, app, tmp_path, runs):
        """Yes leads to the point data step and then the gridded data step,
        each adding entries until it is moved on from, and the calls are
        written into the script."""
        write_simulation(tmp_path / "sim")
        points = tmp_path / "points"
        points.mkdir()
        (points / "obs.csv").write_text("lon,lat,observation\n1,2,3\n")
        app.choose("matchup")
        post(app, "/api/setup", {"form": SETUP_FORM})

        assert post(app, "/api/own_data", {"answer": True})[0] == 200
        assert app.view == "point_data"
        # the wrong kind for the step is not taken
        assert post(app, "/api/own_add", {"kind": "gridded", "form": {}})[0] == 409

        status, reply = post(app, "/api/own_add", {"kind": "point", "form": {"name": "chl"}})
        assert status == 400
        assert set(reply["errors"]) == {"source", "model_variable", "obs_path"}
        form = {
            "name": "chl",
            "source": "mycruise",
            "model_variable": "thetao",
            "obs_path": "points",
            "vertical": False,
            "obs_multiplier": "2",
        }
        assert post(app, "/api/own_add", {"kind": "point", "form": form})[0] == 200
        assert app.own_data["point"] == [
            {
                "name": "chl",
                "source": "mycruise",
                "model_variable": "thetao",
                "obs_path": str(points),
                "obs_multiplier": 2,
            }
        ]
        # a second one can be added and taken off again
        assert post(app, "/api/own_add", {"kind": "point", "form": dict(form, source="other")})[0] == 200
        assert post(app, "/api/own_remove", {"kind": "point", "index": 1})[0] == 200
        assert post(app, "/api/own_remove", {"kind": "point", "index": 5})[0] == 409
        assert len(app.own_data["point"]) == 1
        _, state = get_json(app, "/api/state")
        assert state["own"]["entries"]["point"][0]["name"] == "chl"

        # Back and forward keep what was added
        assert post(app, "/api/own_next")[0] == 200
        assert app.view == "gridded_data"
        assert post(app, "/api/back")[0] == 200
        assert app.view == "point_data"
        post(app, "/api/own_next")
        assert post(app, "/api/own_next")[0] == 200
        wait_for(lambda: app.view == "recipes")
        post(app, "/recipes/write", {"rows": [], "settings": recipe_settings(app)})
        units_match(app)
        wait_for(lambda: runs)

        text = open(draft(app)).read()
        assert (
            '\noceanval.add_point_comparison(\n    name="chl",\n    source="mycruise",\n'
            in text
        )
        assert text.index("add_point_comparison") < text.index("oceanval.matchup(")

    def test_own_data_picks_from_the_simulations_variables(self, app, tmp_path):
        """The simulation's variables are read once the setup is answered, for
        the own data steps' pickers, and a name that is not one is refused."""
        write_simulation(tmp_path / "sim")
        points = tmp_path / "points"
        points.mkdir()
        (points / "obs.csv").write_text("lon,lat,observation\n1,2,3\n")
        app.choose("matchup")
        post(app, "/api/setup", {"form": SETUP_FORM})
        post(app, "/api/own_data", {"answer": True})
        wait_for(lambda: get_json(app, "/api/state")[1]["own"]["simulation"]["status"] == "done")
        found = get_json(app, "/api/state")[1]["own"]["simulation"]
        assert "thetao" in found["available"]
        assert found["long_names"]["thetao"] == "sea water potential temperature"

        form = {"name": "chl", "source": "cruise", "model_variable": "nonsense", "obs_path": "points"}
        status, reply = post(app, "/api/own_add", {"kind": "point", "form": form})
        assert status == 400
        assert reply["errors"] == {"model_variable": "nonsense is not in the simulation output."}
        # every name of a sum is looked at
        reply = post(app, "/api/own_add", {"kind": "point", "form": dict(form, model_variable="thetao+a+b")})[1]
        assert reply["errors"] == {"model_variable": "a, b are not in the simulation output."}
        assert post(app, "/api/own_add", {"kind": "point", "form": dict(form, model_variable="thetao")})[0] == 200
        # the variables are only sent while the own data steps are shown
        post(app, "/api/own_next")
        post(app, "/api/own_next")
        wait_for(lambda: app.view == "recipes")
        assert get_json(app, "/api/state")[1]["own"]["simulation"]["available"] == []

    def test_own_data_does_not_refuse_while_the_simulation_is_unread(self, app, tmp_path):
        """A read that is slow or has failed never stops an entry being added."""
        write_simulation(tmp_path / "sim")
        points = tmp_path / "points"
        points.mkdir()
        (points / "obs.csv").write_text("lon,lat,observation\n1,2,3\n")
        app.choose("matchup")
        post(app, "/api/setup", {"form": SETUP_FORM})
        post(app, "/api/own_data", {"answer": True})
        wait_for(lambda: get_json(app, "/api/state")[1]["own"]["simulation"]["status"] != "reading")
        with app._lock:
            app._own_sim = dict(app._own_sim, status="failed", long_names={})
        form = {"name": "chl", "source": "cruise", "model_variable": "nonsense", "obs_path": "points"}
        assert post(app, "/api/own_add", {"kind": "point", "form": form})[0] == 200

    def test_the_variables_of_gridded_observations_are_looked_at(self, app, tmp_path):
        """The first file of a netCDF file, a directory or a pattern says
        what the Observation variable can be."""
        write_simulation(tmp_path / "sim")
        folder = tmp_path / "obs"
        folder.mkdir()
        for year in (2011, 2012):
            xr.Dataset(
                {"chl": (("lat", "lon"), np.random.rand(2, 2), {"long_name": "chlorophyll", "units": "mg m-3"})},
                coords={"lat": [50.0, 51.0], "lon": [0.0, 1.0]},
            ).to_netcdf(folder / f"chl_{year}.nc")
        app.choose("matchup")
        post(app, "/api/setup", {"form": SETUP_FORM})
        post(app, "/api/own_data", {"answer": True})
        # only on the gridded data step
        assert post(app, "/api/own_inspect", {"obs_path": "obs"})[0] == 409
        post(app, "/api/own_next")
        assert app.view == "gridded_data"

        for path in ("obs/chl_2011.nc", "obs", "obs/chl_*.nc"):
            status, reply = post(app, "/api/own_inspect", {"obs_path": path})
            assert status == 200 and reply["ok"], path
            by_name = {variable["name"]: variable for variable in reply["variables"]}
            assert by_name["chl"]["long_name"] == "chlorophyll"
            assert by_name["chl"]["units"] == "mg m-3"
            assert reply["sample"].endswith("chl_2011.nc")
        assert reply["files"] == 2

        reply = post(app, "/api/own_inspect", {"obs_path": "nothing/here"})[1]
        assert not reply["ok"] and reply["error"] == "There is no file or directory at this path."
        # nothing to say about a blank box or an address
        for path in ("", "  ", "https://example.org/data.nc", None):
            assert post(app, "/api/own_inspect", {"obs_path": path})[1] == {"ok": False, "error": ""}

    def test_back_through_the_own_data_steps(self, app, tmp_path, runs):
        write_simulation(tmp_path / "sim")
        app.choose("matchup")
        post(app, "/api/setup", {"form": SETUP_FORM})
        assert app.view == "own_data"
        assert post(app, "/api/own_next")[0] == 409
        post(app, "/api/own_data", {"answer": True})
        post(app, "/api/own_next")
        assert app.view == "gridded_data"
        for view in ("point_data", "own_data", "setup"):
            assert post(app, "/api/back")[0] == 200
            assert app.view == view

    def test_the_file_filters_reach_the_script(self, app, tmp_path, runs):
        write_simulation(tmp_path / "sim", tracers=True)
        app.choose("matchup")
        post(app, "/api/setup", {"form": dict(SETUP_FORM, exclude="ptrc")})
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "recipes")
        page = app.recipes_page

        # the recipes step starts with them, and hides them
        assert page.context["exclude"] == ["ptrc"]
        assert page.form["exclude"] == "ptrc"
        # nitrate is only in the files they leave out
        found = {row["variable"]: row["model_variable"] for row in page.rows}
        assert (found["nitrate"], found["temperature"]) == ("", "thetao")

        rows = [
            {
                "variable": "temperature",
                "model_variable": "thetao",
                "selected": ["cobe2"],
            }
        ]
        post(app, "/recipes/write", {"rows": rows, "settings": recipe_settings(app, **page.form)})
        units_match(app)
        wait_for(lambda: runs)
        script = open(draft(app)).read()
        # with the draft's cores, which the window always writes (the user's are written once chosen)
        call = script[script.index("oceanval.matchup(") :]
        call = call[: call.index("\n)")]
        for line in ('    n_dirs_down=2,', '    cores=1,', '    exclude=["ptrc"],'):
            assert line in call

    def test_back_from_the_recipes_step(self, app, tmp_path, runs):
        write_simulation(tmp_path / "sim")
        app.choose("matchup_validate")
        post(app, "/api/setup", {"form": SETUP_FORM})
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "recipes")

        assert post(app, "/recipes/cancel")[0] == 200
        # back to where own data was asked about
        wait_for(lambda: app.view == "own_data")
        assert app.setup_form == SETUP_FORM
        assert runs == []
        assert not (tmp_path / "matchup.py").exists()

    def test_what_create_recipes_finds_wrong_is_shown(self, app, tmp_path, runs):
        (tmp_path / "empty").mkdir()
        app.choose("matchup")
        post(app, "/api/setup", {"form": dict(SETUP_FORM, simdir="empty")})
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "setup" and app.setup_error)

        assert "Check the ndown argument" in app.setup_error

    def test_the_fvcom_question_is_asked_in_the_window(self, app, tmp_path, runs):
        write_fvcom(tmp_path / "fvcom")
        app.choose("matchup")
        post(
            app, "/api/setup", {"form": dict(SETUP_FORM, simdir="fvcom", start="2012")}
        )
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.question is not None)
        _, state = get_json(app, "/api/state")
        question = state["question"]

        assert question["choices"] == ["y", "n"]
        assert "looks like raw FVCOM output" in question["text"]
        assert question["text"].endswith("Is this FVCOM output? (y/n) ")
        assert post(app, "/api/answer", {"id": question["id"], "answer": "y"})[0] == 200
        wait_for(lambda: app.view == "recipes")
        assert app.recipes_page.context["fvcom"] is True
        # it can only be answered once
        assert post(app, "/api/answer", {"id": question["id"], "answer": "n"})[0] == 409

    def test_validating(self, app, tmp_path, runs):
        app.choose("validate")
        status, reply = post(app, "/api/validate", {"form": {}})
        assert status == 400
        assert "No matchups found" in reply["errors"]["data_dir"]

        write_matchups(tmp_path)
        assert (
            post(app, "/api/validate", {"form": {"out_dir": "report", "word": True}})[0]
            == 200
        )
        [(args, label)] = runs
        assert args[0] == "validate"
        assert json.loads(args[1]) == {
            "data_dir": str(tmp_path),
            "out_dir": str(tmp_path / "report"),
            "word": True,
        }
        assert label == 'oceanval.validate(data_dir=".", out_dir="report", word=True)'
        assert app.results_dir == str(tmp_path / "report")

    def test_validating_matchups_just_made(self, app, tmp_path):
        app.view, app.action, app.status = "finished", "matchup", "finished"
        post(
            app,
            "/api/choose",
            {"action": "validate", "data_dir": str(tmp_path / "run")},
        )

        assert app.view == "validate"
        assert app.validate_form["data_dir"] == str(tmp_path / "run")

    @pytest.mark.parametrize("chosen", [None, "matchups"])
    def test_validate_defaults_to_matchup_output_directory(self, app, tmp_path, chosen):
        if chosen:
            app.out_dir = str(tmp_path / chosen)

        assert post(app, "/api/choose", {"action": "validate"})[0] == 200
        expected = str(tmp_path / chosen) if chosen else app.cwd
        assert app.validate_form["data_dir"] == expected

    def test_a_data_directory_entered_is_kept_on_going_back_and_in_again(self, app, tmp_path):
        assert post(app, "/api/choose", {"action": "validate"})[0] == 200
        form = dict(app.validate_form, data_dir=str(tmp_path / "mine"))
        assert post(app, "/api/back", {"form": form})[0] == 200
        assert app.view == "start"
        app.out_dir = str(tmp_path / "other")
        assert post(app, "/api/choose", {"action": "validate"})[0] == 200
        assert app.validate_form["data_dir"] == str(tmp_path / "mine")
        # the matchups just made are what is validated, whatever was entered
        app.view = "finished"
        assert post(app, "/api/choose", {"action": "validate", "data_dir": str(tmp_path / "run")})[0] == 200
        assert app.validate_form["data_dir"] == str(tmp_path / "run")

    def test_a_data_directory_left_as_filled_in_follows_the_output_directory(self, app, tmp_path):
        app.out_dir = str(tmp_path / "first")
        assert post(app, "/api/choose", {"action": "validate"})[0] == 200
        assert post(app, "/api/back", {"form": dict(app.validate_form)})[0] == 200
        app.out_dir = str(tmp_path / "second")
        assert post(app, "/api/choose", {"action": "validate"})[0] == 200
        assert app.validate_form["data_dir"] == str(tmp_path / "second")

    def test_a_run_from_start_to_finish(self, app, tmp_path):
        script = tmp_path / "run.py"
        script.write_text(
            textwrap.dedent(
                """
                from oceanval import prompts
                print("matching")
                print("answer:", prompts.ask("Happy? (y/n) ", ("y", "n")))
                """
            )
        )
        app.action = "matchup"
        app.start_run(["script", str(script)], "python run.py")
        wait_for(lambda: app.question is not None)
        _, state = get_json(app, "/api/state")

        assert state["view"] == "running"
        # the command that runs is not echoed
        assert "python run.py" not in state["console"]["text"]
        assert state["console"]["text"].endswith("matching\nHappy? (y/n) ")
        assert (
            post(app, "/api/answer", {"id": state["question"]["id"], "answer": "n"})[0]
            == 200
        )
        wait_for(lambda: app.view == "finished")
        _, state = get_json(app, "/api/state")
        assert state["run"]["status"] == "finished"
        # the answer follows the question, as at a terminal
        assert state["console"]["text"].endswith("Happy? (y/n) n\nanswer: n\n")
        assert post(app, "/api/restart_all")[0] == 200
        assert app.view == "home"

    def test_the_matchups_are_followed_as_matchup_tells_of_them(self, app, tmp_path, monkeypatch):
        items = [
            {"key": "gridded|temperature|nsbc|", "variable": "temperature", "title": "Temperature",
             "source": "NSBC", "report": True},
            {"key": "point|nitrate|ices|surface", "variable": "nitrate", "title": "Nitrate",
             "source": "ICES point data (surface)", "report": False},
        ]
        go = tmp_path / "go"
        script = tmp_path / "run.py"
        script.write_text(
            textwrap.dedent(
                f"""
                import os, time
                from oceanval import prompts
                prompts.tell({{"kind": "matchups", "items": {items!r}}})
                prompts.tell({{"kind": "matchup", "key": "gridded|temperature|nsbc|", "state": "matching"}})
                print("matching up")
                while not os.path.exists({str(go)!r}):
                    time.sleep(0.05)
                prompts.tell({{"kind": "matchup", "key": "gridded|temperature|nsbc|", "state": "made"}})
                prompts.tell({{"kind": "matchup", "key": "point|nitrate|ices|surface", "state": "matching"}})
                prompts.tell({{"kind": "matchups_finished"}})
                """
            )
        )
        # the times of the grid, which the test moves on itself
        clock = [100.0]
        monkeypatch.setattr(app, "_clock", lambda: clock[0])
        app.action = "matchup_validate"
        app.start_run(["script", str(script)], "python run.py")
        wait_for(lambda: app.matchup_progress and app.matchup_progress["states"])
        clock[0] = 103.0
        progress = get_json(app, "/api/state")[1]["run"]["progress"]
        assert progress == {
            "items": [
                {**items[0], "state": "matching", "seconds": 3.0, "extraction": None, "observations": None},
                {**items[1], "state": "waiting", "seconds": None, "extraction": None, "observations": None},
            ],
            "finished": False,
            "elapsed": 3.0,
            "running": True,
        }
        clock[0] = 107.0
        go.write_text("")
        wait_for(lambda: app.view == "finished")
        _, state = get_json(app, "/api/state")
        # one not made by the end is not made
        assert [item["state"] for item in state["run"]["progress"]["items"]] == ["made", "not_made"]
        assert state["run"]["progress"]["finished"]
        # the first took as long as it was going, the other was left half way: no time
        assert [item["seconds"] for item in state["run"]["progress"]["items"]] == [7.0, None]
        # the clock stops with the run
        clock[0] = 150.0
        assert get_json(app, "/api/state")[1]["run"]["progress"]["elapsed"] == 7.0
        assert not get_json(app, "/api/state")[1]["run"]["progress"]["running"]
        # none of it is in the output
        assert "matching up" in state["console"]["text"]
        assert "oceanval-progress" not in state["console"]["text"]
        # the report run after it keeps the grid, and another run does not
        monkeypatch.setattr(app_module.Run, "start", lambda run: None)
        app.start_run(["validate", "{}"], "validate()")
        assert app.matchup_progress is not None
        # its clock goes on from where it was, without the time between the runs
        clock[0] = 155.0
        progress = get_json(app, "/api/state")[1]["run"]["progress"]
        assert progress["elapsed"] == 12.0
        assert progress["running"]
        app.start_run(["script", str(script)], "python run.py")
        assert app.matchup_progress is None

    def test_each_matchup_is_timed_from_when_it_is_started(self, app, monkeypatch):
        clock = [10.0]
        monkeypatch.setattr(app, "_clock", lambda: clock[0])
        run = object()
        app.run = run

        def tell(key=None, state=None):
            if key is None:
                items = [{"key": key, "variable": key} for key in "abc"]
                app._matchup_news(run, {"kind": "matchups", "items": items})
            else:
                app._matchup_news(run, {"kind": "matchup", "key": key, "state": state})

        def seconds():
            progress = app._matchup_progress_state()
            return {item["key"]: item["seconds"] for item in progress["items"]}

        try:
            tell()
            assert seconds() == {"a": None, "b": None, "c": None}
            tell("a", "matching")
            clock[0] = 14.5
            assert seconds()["a"] == 4.5
            tell("a", "made")
            clock[0] = 20.0
            # it took what it took, however long ago
            assert seconds()["a"] == 4.5
            # one made already is told as made without being started: no time
            tell("b", "made")
            assert seconds()["b"] is None
            # one that fails and is tried again is timed from its last try
            tell("c", "matching")
            clock[0] = 23.0
            tell("c", "not_made")
            assert seconds()["c"] == 3.0
            clock[0] = 30.0
            tell("c", "matching")
            assert seconds()["c"] == 0.0
            clock[0] = 31.0
            assert seconds()["c"] == 1.0
            tell("c", "made")
            assert seconds()["c"] == 1.0
            # all the while the run has been going from the first news
            assert app._matchup_progress_state()["elapsed"] == 21.0
        finally:
            app.run = None

    def test_how_many_model_files_have_been_read_is_followed(self, app):
        run = object()
        app.run = run

        def tell(news):
            app._matchup_news(run, news)

        def extraction():
            progress = app._matchup_progress_state()
            return {item["key"]: item["extraction"] for item in progress["items"]}

        try:
            tell({"kind": "matchups", "items": [{"key": key, "variable": key} for key in "abc"]})
            # none before they are read
            assert extraction() == {"a": None, "b": None, "c": None}
            tell({"kind": "matchup", "key": "a", "state": "matching"})
            # a gridded matchup's files are being read, and how many have been is not known yet
            tell(cdo_watch.news("a", 12, None))
            assert extraction()["a"] == {"total": 12, "done": None, "finished": False}
            tell(cdo_watch.news("a", 12, 5))
            assert extraction()["a"] == {"total": 12, "done": 5, "finished": False}
            # the watcher's counts and the matchup's own come in either order: a count never goes down
            tell(cdo_watch.news("a", 12, 3))
            tell(cdo_watch.news("a", 12, None))
            assert extraction()["a"]["done"] == 5
            tell(cdo_watch.news("a", 12, 12, finished=True))
            tell({"kind": "matchup", "key": "a", "state": "made"})
            assert extraction()["a"] == {"total": 12, "done": 12, "finished": True}
            # one tried again is counted again
            tell({"kind": "matchup", "key": "b", "state": "matching"})
            tell(cdo_watch.news("b", 3, 2))
            tell({"kind": "matchup", "key": "b", "state": "not_made"})
            assert extraction()["b"] == {"total": 3, "done": 2, "finished": False}
            tell({"kind": "matchup", "key": "b", "state": "matching"})
            assert extraction()["b"] is None
            # one made before is told as made with no files read
            tell({"kind": "matchup", "key": "c", "state": "made"})
            assert extraction()["c"] is None
            # news that is not a count is left out
            tell({"kind": "extraction", "key": "c", "total": "many", "done": 1})
            assert extraction()["c"] is None
        finally:
            app.run = None

    def test_whether_the_observations_have_been_read_is_followed(self, app):
        run = object()
        app.run = run

        def tell(news):
            app._matchup_news(run, news)

        def observations():
            progress = app._matchup_progress_state()
            return {item["key"]: item["observations"] for item in progress["items"]}

        try:
            tell({"kind": "matchups", "items": [{"key": key, "variable": key} for key in "abc"]})
            # none before they are read
            assert observations() == {"a": None, "b": None, "c": None}
            tell({"kind": "matchup", "key": "a", "state": "matching"})
            tell({"kind": "observations", "key": "a", "state": "reading"})
            assert observations()["a"] == "reading"
            tell({"kind": "observations", "key": "a", "state": "read"})
            tell({"kind": "matchup", "key": "a", "state": "made"})
            assert observations()["a"] == "read"
            # a point matchup reads them again for each pattern of its files: the last said is shown
            tell({"kind": "observations", "key": "a", "state": "reading"})
            assert observations()["a"] == "reading"
            # one tried again starts afresh
            tell({"kind": "matchup", "key": "b", "state": "matching"})
            tell({"kind": "observations", "key": "b", "state": "reading"})
            tell({"kind": "matchup", "key": "b", "state": "not_made"})
            assert observations()["b"] == "reading"
            tell({"kind": "matchup", "key": "b", "state": "matching"})
            assert observations()["b"] is None
            # one made before is told as made with none read
            tell({"kind": "matchup", "key": "c", "state": "made"})
            assert observations()["c"] is None
            # news that is not a state of them is left out
            tell({"kind": "observations", "key": "c", "state": "downloaded"})
            tell({"kind": "observations", "key": "c"})
            assert observations()["c"] is None
        finally:
            app.run = None

    def test_a_server_that_is_down_is_said_to_be(self, app, monkeypatch):
        monkeypatch.setattr(app, "_wall_clock", lambda: datetime.datetime(2026, 10, 25, 15, 45))
        monkeypatch.setattr(app_module.Run, "start", lambda run: None)
        # what the state reads of a run
        run = types.SimpleNamespace(args=["script"])
        app.run = run

        def tell(news):
            app._matchup_news(run, news)

        def down(key, title, host="www.ncei.noaa.gov", server="NOAA", status=503, reason="Service Unavailable"):
            tell({"kind": "server_trouble", "key": key, "title": title, "host": host,
                  "server": server, "status": status, "reason": reason})

        def trouble():
            return get_json(app, "/api/state")[1]["run"]["server_trouble"]

        try:
            assert trouble() == []
            items = [
                {"key": "gridded|nitrate|WOA23|", "title": "Nitrate (WOA23)"},
                {"key": "gridded|salinity|WOA23|", "title": "Salinity (WOA23)"},
                {"key": "gridded|chlorophyll|OCCCI|", "title": "Chlorophyll (OCCCI)"},
            ]
            tell({"kind": "matchups", "items": items})
            # told before any grid: the grid's names are used once there is one
            down("gridded|nitrate|WOA23|", "nitrate (WOA23)")
            assert trouble() == [{
                "host": "www.ncei.noaa.gov", "server": "NOAA", "status": 503,
                "reason": "Service Unavailable", "at": "15:45", "matchups": ["Nitrate (WOA23)"],
            }]
            # another from the same server, which answered otherwise this time
            down("gridded|salinity|WOA23|", "salinity (WOA23)", status=502, reason="Proxy Error")
            assert trouble()[0]["matchups"] == ["Nitrate (WOA23)", "Salinity (WOA23)"]
            assert (trouble()[0]["status"], trouble()[0]["reason"]) == (502, "Proxy Error")
            # and another server
            down("gridded|chlorophyll|OCCCI|", "chlorophyll (OCCCI)", host="www.oceancolour.org",
                 server="ESA's Ocean Colour CCI")
            assert [x["host"] for x in trouble()] == ["www.ncei.noaa.gov", "www.oceancolour.org"]
            # made when tried again: no longer said, nor the server once none is left
            tell({"kind": "matchup", "key": "gridded|chlorophyll|OCCCI|", "state": "made"})
            assert [x["host"] for x in trouble()] == ["www.ncei.noaa.gov"]
            tell({"kind": "matchup", "key": "gridded|nitrate|WOA23|", "state": "made"})
            assert trouble()[0]["matchups"] == ["Salinity (WOA23)"]
            # news from a run that has been left is not said
            app._matchup_news(object(), {"kind": "server_trouble", "key": "x", "host": "h"})
            assert len(trouble()) == 1
            # the report run after the matchups keeps it, another run does not
            app.start_run(["validate", "{}"], "validate()")
            assert len(trouble()) == 1
            app.start_run(["script", "run.py"], "python run.py")
            assert trouble() == []
        finally:
            app.run = None

    def test_the_analyses_are_followed_as_they_are_worked_out(self, app, monkeypatch):
        clock = [10.0]
        monkeypatch.setattr(app, "_clock", lambda: clock[0])
        run = object()
        app.run = run

        def tell(kind, **news):
            app._matchup_news(run, {"kind": kind, **news})

        def cells(key):
            progress = app._analysis_progress_state()
            item = next(item for item in progress["items"] if item["key"] == key)
            return {kind: cell["state"] for kind, cell in item["cells"].items()}

        try:
            # a comparison of two simulations: each analysis is told once for each
            tell("analyses", simulations=2, items=[
                # in any order, they are given in that of analysis.KINDS
                {"key": "chl", "title": "Chlorophyll", "kinds": ["seasonal", "climatology"]},
                {"key": "temp", "title": "Temperature", "kinds": ["transects"]},
            ])
            progress = app._analysis_progress_state()
            # only the analyses some variable has are columns
            assert progress["kinds"] == ["climatology", "seasonal", "transects"]
            assert progress["simulations"] == 2
            assert progress["items"][0]["kinds"] == ["climatology", "seasonal"]
            assert cells("chl") == {"climatology": "waiting", "seasonal": "waiting"}
            assert [item["state"] for item in progress["items"]] == ["waiting", "waiting"]
            tell("analysis_variable", key="chl", state="working")
            tell("analysis", key="chl", analysis="climatology", state="working")
            assert cells("chl") == {"climatology": "working", "seasonal": "waiting"}
            tell("analysis", key="chl", analysis="climatology", state="done")
            # in one of the two simulations
            assert cells("chl")["climatology"] == "partial"
            assert app._analysis_progress_state()["items"][0]["cells"]["climatology"]["done"] == 1
            tell("analysis", key="chl", analysis="climatology", state="working")
            assert cells("chl")["climatology"] == "working"
            tell("analysis", key="chl", analysis="climatology", state="done")
            assert cells("chl")["climatology"] == "done"
            clock[0] = 14.0
            assert app._analysis_progress_state()["items"][0]["seconds"] == 4.0
            tell("analysis_variable", key="chl", state="done")
            clock[0] = 20.0
            progress = app._analysis_progress_state()
            assert progress["items"][0]["state"] == "done"
            # it took what it took, however long ago
            assert progress["items"][0]["seconds"] == 4.0
            assert progress["items"][1]["seconds"] is None
            # news that is not a state of them is left out
            tell("analysis", key="temp", analysis="transects", state="started")
            tell("analysis_variable", key="temp")
            assert cells("temp") == {"transects": "waiting"}
            # the run ends half way through one
            tell("analysis_variable", key="temp", state="working")
            tell("analysis", key="temp", analysis="transects", state="working")
            clock[0] = 23.0
            app._analysis_clock_stop()
            clock[0] = 50.0
            progress = app._analysis_progress_state()
            assert cells("temp") == {"transects": "failed"}
            assert progress["items"][1]["seconds"] == 3.0
            assert progress["elapsed"] == 13.0
            assert not progress["running"]
        finally:
            app.run = None

    def test_the_analyses_are_followed_from_the_run(self, app, tmp_path, monkeypatch):
        script = tmp_path / "analysis.py"
        go = tmp_path / "go"
        script.write_text(
            textwrap.dedent(
                f"""
                import os, time
                from oceanval import prompts
                prompts.tell({{"kind": "analyses", "simulations": 1,
                               "items": [{{"key": "chl", "title": "Chlorophyll", "kinds": ["phenology"]}}]}})
                prompts.tell({{"kind": "analysis_variable", "key": "chl", "state": "working"}})
                prompts.tell({{"kind": "analysis", "key": "chl", "analysis": "phenology", "state": "working"}})
                print("OceanVal: Working out the phenology of Chlorophyll")
                while not os.path.exists({str(go)!r}):
                    time.sleep(0.05)
                """
            )
        )
        clock = [100.0]
        monkeypatch.setattr(app, "_clock", lambda: clock[0])
        app.action = "analyze"
        app.start_run(["analyze", str(script)], "python analysis.py")
        wait_for(lambda: app.analysis_progress and app.analysis_progress["working"])
        clock[0] = 102.0
        _, state = get_json(app, "/api/state")
        assert state["run"]["analysis"] == {
            "simulations": 1,
            "kinds": ["phenology"],
            "items": [{
                "key": "chl", "title": "Chlorophyll", "kinds": ["phenology"], "state": "working",
                "seconds": 2.0, "cells": {"phenology": {"state": "working", "done": 0}},
            }],
            "elapsed": 2.0,
            "running": True,
        }
        # the matchups' grid is not made of it
        assert state["run"]["progress"] is None
        go.write_text("")
        wait_for(lambda: app.view == "finished")
        _, state = get_json(app, "/api/state")
        assert "oceanval-progress" not in state["console"]["text"]
        assert state["run"]["analysis"]["items"][0]["cells"]["phenology"]["state"] == "failed"
        # another run starts afresh
        monkeypatch.setattr(app_module.Run, "start", lambda run: None)
        app.start_run(["analyze", str(script)], "python analysis.py")
        assert app.analysis_progress is None

    def test_the_files_read_are_counted_by_a_process_of_its_own(self, app, tmp_path):
        """The model files read in nctoolkit are counted by a process watching
        CDO's (see oceanval.cdo_watch), whose news shares the run's stdout."""
        key = "gridded|temperature|nsbc|"
        items = [{"key": key, "variable": "temperature", "title": "Temperature", "source": "NSBC", "report": False}]
        model = tmp_path / "model_2000.nc"
        script = tmp_path / "run.py"
        script.write_text(
            textwrap.dedent(
                f"""
                import subprocess, sys, time
                from oceanval import cdo_watch, prompts
                prompts.tell({{"kind": "matchups", "items": {items!r}}})
                prompts.tell({{"kind": "matchup", "key": {key!r}, "state": "matching"}})
                with cdo_watch.watching([{str(model)!r}], {key!r}):
                    # stands in for CDO reading the file
                    subprocess.run([sys.executable, "-c", "import time; time.sleep(1.5)", {str(model)!r}])
                    time.sleep(2)
                print("read")
                """
            )
        )
        app.action = "matchup_validate"
        app.start_run(["script", str(script)], "python run.py")
        wait_for(lambda: app.view == "finished", timeout=60)
        _, state = get_json(app, "/api/state")
        assert state["run"]["progress"]["items"][0]["extraction"] == {"total": 1, "done": 1, "finished": False}
        assert "read" in state["console"]["text"]
        assert "oceanval-progress" not in state["console"]["text"]

    def matchups_script(self, tmp_path):
        """A stand-in for matchup, asking whether the matchups are right with
        the details the window shows."""
        script = tmp_path / "run.py"
        script.write_text(
            textwrap.dedent(
                f"""
                from oceanval import prompts
                print("finding the files")
                question = "Are you happy with these matchups? (y/n) "
                print("answer:", prompts.ask(question, ("y", "n"), details={MATCHUPS!r}))
                print("matching up")
                """
            )
        )
        return script

    def test_the_matchups_are_asked_about_with_their_files(self, app, tmp_path):
        app.action = "matchup"
        app.start_run(["script", str(self.matchups_script(tmp_path))], "python run.py")
        # the page says the files are being found until it is asked
        assert get_json(app, "/api/state")[1]["run"]["identifying"]
        wait_for(lambda: app.question is not None)
        _, state = get_json(app, "/api/state")
        question = state["question"]

        assert not state["run"]["identifying"]
        assert question["details"]["rows"] == MATCHUPS["rows"]
        # the files can be many, so they are asked for a pattern at a time
        assert "files" not in question["details"]
        status, reply = get_json(
            app, "/api/question_files", id=question["id"], pattern="x_**_grid_T.nc"
        )
        assert (status, reply["files"]) == (200, MATCHUPS["files"]["x_**_grid_T.nc"])
        assert get_json(app, "/api/question_files", id=question["id"], pattern="y")[0] == 409

        assert post(app, "/api/answer", {"id": question["id"], "answer": "y"})[0] == 200
        # where the matchups go, and the cores, and then matchup carries on
        output_continue(app)
        wait_for(lambda: app.view == "finished")
        assert app.status == "finished"
        assert "answer: y\nmatching up\n" in app.console.since(0, None)["text"]
        # once it is answered, they are not listed
        assert (
            get_json(app, "/api/question_files", id=question["id"], pattern="x_**_grid_T.nc")[0]
            == 409
        )

    def validate_runs(self, app, monkeypatch):
        """The validate runs the app starts, which are recorded rather than
        started; the other runs are started."""
        started = []
        start_run = app.start_run

        def record(args, label):
            if args[0] == "validate":
                started.append((args, label))
            else:
                start_run(args, label)

        monkeypatch.setattr(app, "start_run", record)
        return started

    def test_the_report_options_come_before_anything_is_matched_up(
        self, app, tmp_path, monkeypatch
    ):
        validated = self.validate_runs(app, monkeypatch)
        app.action = "matchup_validate"
        app.start_run(["matchup", str(self.matchups_script(tmp_path))], "python run.py")
        wait_for(lambda: app.question is not None)
        number = app.question.id

        assert post(app, "/api/answer", {"id": number, "answer": "y"})[0] == 200
        # where the matchups go first
        assert app.view == "output"
        output_continue(app)
        _, state = get_json(app, "/api/state")
        assert state["view"] == "report_options"
        # matchup still waits for the answer, so nothing is matched up yet
        assert state["question"]["id"] == number
        # whether the matchups are gridded is only known from the recipes window's choices
        assert state["report"] == {
            "given": False, "dir": str(tmp_path), "gridded": None, "vertical": None, "matchups": None,
        }
        time.sleep(0.5)
        assert "answer:" not in app.console.since(0, None)["text"]
        assert post(app, "/api/answer", {"id": number, "answer": "y"})[0] == 409

        # Back shows where the matchups go, and then the matchups again, still asked about
        assert post(app, "/api/back")[0] == 200
        assert app.view == "output"
        assert post(app, "/api/back")[0] == 200
        assert (app.view, app.question.id) == ("running", number)
        assert post(app, "/api/answer", {"id": number, "answer": "y"})[0] == 200
        output_continue(app)
        assert app.view == "report_options"

        status, reply = post(app, "/api/report", {"form": {"lon_min": "x"}})
        assert status == 400
        assert reply["errors"]["lon_min"] == "Limits must be numbers."
        # the cores are asked for here too, with no default
        assert reply["errors"]["cores"] == "Choose the number of cores."
        assert app.view == "report_options"

        form = {"lon_min": "-20", "lon_max": "10", "lat_min": "40", "lat_max": "65",
                "pdf": True, "concise": False, "cores": "1"}
        assert post(app, "/api/report", {"form": form})[0] == 200
        assert get_json(app, "/api/state")[1]["report"]["given"] is True
        # then matchup carries on, and the report is built once it has run
        wait_for(lambda: validated)
        text = app.console.since(0, None)["text"]
        assert "Are you happy with these matchups? (y/n) y\nanswer: y\nmatching up\n" in text
        [(args, label)] = validated
        assert args[0] == "validate"
        # the subset was matchup's, given with yes, so the report has nothing outside it
        assert json.loads(args[1]) == {
            "data_dir": str(tmp_path),
            "out_dir": str(tmp_path),
            "pdf": True,
            "concise": False,
        }
        assert label == 'oceanval.validate(data_dir=".", out_dir=".", pdf=True, concise=False)'

    def test_the_report_options_step_can_store_the_script_instead(
        self, app, tmp_path, monkeypatch
    ):
        validated = self.validate_runs(app, monkeypatch)
        script = self.matchups_script(tmp_path)
        app.action = "matchup_validate"
        app.start_run(["matchup", str(script)], "python run.py")
        wait_for(lambda: app.question is not None)
        number = app.question.id
        assert post(app, "/api/answer", {"id": number, "answer": "y"})[0] == 200
        output_continue(app)
        assert app.view == "report_options"

        # the boxes are checked as for the report
        status, reply = post(app, "/api/save_script", {"form": {"lon_min": "x"}})
        assert status == 400
        assert reply["errors"]["lon_min"] == "Limits must be numbers."
        assert app.view == "report_options"

        assert post(app, "/api/save_script", {"form": {"pdf": True, "cores": "1"}})[0] == 200
        wait_for(lambda: app.view == "script_saved")
        _, state = get_json(app, "/api/state")
        assert state["run"]["status"] == "stopped"
        assert state["run"]["script_saved"] is True
        assert state["run"]["script_command"] == f"python {script}"
        # nothing was matched up, and no report is built
        assert "matching up" not in state["console"]["text"]
        assert "answer:" not in state["console"]["text"]
        assert validated == []
        # the step is over
        assert post(app, "/api/save_script", {"form": {}})[0] == 409

    def test_the_report_options_are_asked_for_if_matchup_did_not_ask(
        self, app, tmp_path, monkeypatch
    ):
        validated = self.validate_runs(app, monkeypatch)
        script = tmp_path / "run.py"
        script.write_text("print('matched up')\n")
        app.action = "matchup_validate"
        app.start_run(["matchup", str(script)], "python run.py")
        wait_for(lambda: app.view == "report_options")

        # the matchups are made, so there is nothing to go back to
        assert post(app, "/api/back")[0] == 409
        assert post(app, "/api/report", {"form": {}})[0] == 200
        assert validated == [
            (
                ["validate", json.dumps({"data_dir": str(tmp_path), "out_dir": str(tmp_path)})],
                'oceanval.validate(data_dir=".", out_dir=".")',
            )
        ]

    def test_the_report_options_go_with_yes_to_the_matchups(
        self, app, tmp_path, monkeypatch
    ):
        """matchup builds the interim report with them as it goes (see
        oceanval.live), so they are sent with yes, without the full
        report's other forms."""
        validated = self.validate_runs(app, monkeypatch)
        script = tmp_path / "run.py"
        script.write_text(
            textwrap.dedent(
                f"""
                import json
                from oceanval import prompts
                question = "Are you happy with these matchups? (y/n) "
                answer = prompts.ask(question, ("y", "n"), details={MATCHUPS!r})
                print("answer:", answer)
                print("settings:", json.dumps(answer.settings, sort_keys=True))
                """
            )
        )
        stale = tmp_path / "oceanval_interim_report" / "status.json"
        stale.parent.mkdir()
        stale.write_text(json.dumps({"state": "complete", "pages": 4}))
        app.action = "matchup_validate"
        app.start_run(["matchup", str(script)], "python run.py")
        wait_for(lambda: app.question is not None)
        assert post(app, "/api/answer", {"id": app.question.id, "answer": "y"})[0] == 200
        output_continue(app)

        form = {"lon_min": "-20", "lon_max": "10", "lat_min": "40", "lat_max": "65",
                "pdf": True, "zip": True, "concise": False, "cores": "1"}
        assert post(app, "/api/report", {"form": form})[0] == 200
        # until matchup's builder says how far it has got, the page says it is coming
        assert get_json(app, "/api/state")[1]["interim"] == {
            "state": "waiting", "pages": 0, "expected": None, "what": "validation", "href": None,
            "included": None, "building_key": None, "added": {}, "failed": [],
        }
        # what an earlier run left is not taken for this run's
        assert not stale.exists()
        wait_for(lambda: validated)
        text = app.console.since(0, None)["text"]
        # shown as typed at a terminal
        assert "Are you happy with these matchups? (y/n) y\nanswer: y\n" in text
        # the subset is matchup's, so is not the interim report's
        assert '"live_validation": {"concise": false}' in text
        # with where the matchups go, what is done with those there already, the
        # cores and the subset
        assert f'"cores": 1, ' in text and f'"out_dir": "{tmp_path}", "overwrite": true' in text
        assert '"lat_lim": [40.0, 65.0], "live_validation"' in text and '"lon_lim": [-20.0, 10.0]' in text
        # the full report still has them all
        assert json.loads(validated[0][0][1])["pdf"] is True

    def test_the_interim_reports_progress_is_followed(self, app, tmp_path):
        app.results_dir = str(tmp_path)
        folder = tmp_path / "oceanval_interim_report"
        pages = folder / "oceanval_report" / "_build" / "html" / "notebooks"
        pages.mkdir(parents=True)
        (pages / "summary.html").write_text("<p>So far</p>")
        status = folder / "status.json"
        interim = {"status_path": str(status), "status": None}
        app.interim = interim
        threading.Thread(target=app._follow_interim, args=(interim,), daemon=True).start()

        def interim_state():
            return get_json(app, "/api/state")[1]["interim"]

        status.write_text(json.dumps({"state": "starting", "pages": 0, "expected": 3, "landing": None}))
        wait_for(lambda: interim_state()["expected"] == 3, timeout=15)
        assert interim_state()["href"] is None
        status.write_text(
            json.dumps(
                {
                    "state": "building", "pages": 1, "expected": 3, "landing": str(pages / "summary.html"),
                    # for the grid of the matchups, each page by the matchup's key
                    "included": ["a", "b", "c"], "building_key": "b", "failed": ["c"],
                    "added": {"a": str(pages / "summary.html")},
                }
            )
        )
        wait_for(lambda: interim_state()["pages"] == 1, timeout=15)

        assert interim_state() == {
            "state": "building",
            "pages": 1,
            "expected": 3,
            "what": "validation",
            "href": f"interim/{app.token}/notebooks/summary.html",
            "included": ["a", "b", "c"],
            "building_key": "b",
            "added": {"a": f"interim/{app.token}/notebooks/summary.html"},
            "failed": ["c"],
        }
        # the link opens the report from the window
        with urllib.request.urlopen(
            f"http://127.0.0.1:{app.port}/{interim_state()['href']}", timeout=30
        ) as response:
            assert response.read() == b"<p>So far</p>"
        status.write_text(
            json.dumps(
                {"state": "complete", "pages": 3, "expected": 3, "landing": str(pages / "summary.html")}
            )
        )
        wait_for(lambda: interim_state()["state"] == "complete", timeout=15)

    def test_comparing(self, app, tmp_path, runs):
        validations(tmp_path, "control", "mixing")
        assert post(app, "/api/choose", {"action": "compare"})[0] == 200
        assert app.view == "compare"
        _, state = get_json(app, "/api/state")
        assert state["compare"]["form"]["name_1"] == ""
        assert post(app, "/api/back")[0] == 200
        assert app.view == "start"
        assert post(app, "/api/choose", {"action": "compare"})[0] == 200

        status, reply = post(app, "/api/compare", {"form": {"name_1": "control", "dir_1": "control"}})
        assert status == 400
        assert reply["errors"] == {"rows": "Fill in at least two simulations to compare."}
        assert app.view == "compare" and runs == []
        # the boxes are kept
        assert get_json(app, "/api/state")[1]["compare"]["form"]["dir_1"] == "control"

        form = {"name_1": "control", "dir_1": "control", "name_2": "mixing", "dir_2": "mixing"}
        assert post(app, "/api/compare", {"form": form})[0] == 200
        assert app.view == "running"
        arguments = {
            "model_dict": {"control": str(tmp_path / "control"), "mixing": str(tmp_path / "mixing")},
            "out_dir": str(tmp_path),
        }
        assert runs == [(
            ["compare", json.dumps(arguments)],
            'oceanval.compare(model_dict={"control": "control", "mixing": "mixing"}, out_dir=".")',
        )]
        # the step is over
        assert post(app, "/api/compare", {"form": form})[0] == 409

    def test_the_comparison_is_served_from_the_window(self, app, tmp_path):
        """With the validation reports it links to, which are elsewhere, and
        nothing else."""
        runs_dir = tmp_path / "runs"
        validations(runs_dir, "control")
        validations(tmp_path / "elsewhere", "mixing")
        out = tmp_path / "comparisons"
        pages = out / "oceanval_comparison" / "compare" / "_build" / "html" / "notebooks"
        pages.mkdir(parents=True)
        (pages / "comparison_seasonal.html").write_text("<p>comparison</p>")
        summary = runs_dir / "control" / "oceanval_report" / "_build" / "html" / "notebooks" / "003_summary.html"
        summary.write_text("<p>control</p>")
        (runs_dir / "control" / "oceanval_results" / "annual_mean" / "x.nc").write_text("results")
        (tmp_path / "secret.txt").write_text("secret")
        app.action = "compare"
        app.compare_arguments = {
            "model_dict": {"control": str(runs_dir / "control"), "mixing": str(tmp_path / "elsewhere" / "mixing")},
            "out_dir": str(out),
        }
        app.results_dir = str(out)

        def fetch(path):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{app.port}/{path}", timeout=30) as response:
                    return response.status, response.read().decode()
            except urllib.error.HTTPError as error:
                return error.code, None

        run = get_json(app, "/api/state")[1]["run"]
        assert run["report_exists"] is True
        assert run["report"] == str(pages / "comparison_seasonal.html")
        href = run["report_href"]
        assert href.startswith(f"comparison/{app.token}/") and href.endswith(
            "comparison_seasonal.html"
        )
        assert fetch(href) == (200, "<p>comparison</p>")
        # its link to a simulation's report is relative, as compare() writes it
        link = os.path.relpath(summary, pages)
        url = urllib.parse.urljoin(f"http://127.0.0.1:{app.port}/{href}", link)
        assert fetch(url.split(f"{app.port}/", 1)[1]) == (200, "<p>control</p>")
        # nothing but the reports' pages, and only with the token
        root = os.path.commonpath([str(pages), str(summary)])
        results = os.path.relpath(runs_dir / "control" / "oceanval_results" / "annual_mean" / "x.nc", root)
        assert fetch(f"comparison/{app.token}/{results}")[0] == 404
        assert fetch(f"comparison/{app.token}/{os.path.relpath(tmp_path / 'secret.txt', root)}")[0] == 404
        assert fetch(f"comparison/wrong/{os.path.relpath(summary, root)}")[0] == 403

    def test_the_reports_are_served_from_the_window(self, app, tmp_path):
        """A file:// link cannot be opened from the page, nor over a
        forwarded port, so the window serves the reports itself."""
        app.results_dir = str(tmp_path)
        interim = tmp_path / "oceanval_interim_report" / "oceanval_report" / "_build" / "html"
        report = tmp_path / "oceanval_report" / "_build" / "html"
        (interim / "notebooks").mkdir(parents=True)
        (report / "notebooks").mkdir(parents=True)
        (report / "_static").mkdir()
        (interim / "notebooks" / "summary.html").write_text("<p>interim</p>")
        (interim / "notebooks" / "oceanval_wordmark.svg").write_text("<svg/>")
        (report / "notebooks" / "004_summary.html").write_text("<p>full</p>")
        (report / "_static" / "custom.css").write_text("p {}")
        (tmp_path / "oceanval_interim_report" / "status.json").write_text("{}")
        (tmp_path / "secret.txt").write_text("secret")
        token = app.token

        def fetch(path):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{app.port}/{path}", timeout=30) as response:
                    return response.status, response.headers["Content-Type"], response.read().decode()
            except urllib.error.HTTPError as error:
                return error.code, None, None

        assert fetch(f"interim/{token}/notebooks/summary.html") == (
            200, "text/html; charset=utf-8", "<p>interim</p>",
        )
        assert fetch(f"interim/{token}/notebooks/oceanval_wordmark.svg")[:2] == (
            200, "image/svg+xml; charset=utf-8",
        )
        assert fetch(f"report/{token}/notebooks/004_summary.html")[2] == "<p>full</p>"
        # the full report's pages link to its stylesheets a directory up
        assert fetch(f"report/{token}/_static/custom.css")[:2] == (200, "text/css; charset=utf-8")
        # only with the token, and nothing but the reports' pages
        assert fetch("interim/wrong/notebooks/summary.html")[0] == 403
        assert fetch(f"interim/{token}/../../status.json")[0] == 404
        assert fetch(f"interim/{token}/..%2F..%2F..%2F..%2Fsecret.txt")[0] == 404
        assert fetch(f"report/{token}/notebooks/")[0] == 404
        assert fetch(f"report/{token}/notebooks/missing.html")[0] == 404
        assert fetch(f"elsewhere/{token}/secret.txt")[0] == 404

        # the finished page links to the full report, as validate leaves it
        app.action = "validate"
        assert get_json(app, "/api/state")[1]["run"]["report_href"] is None
        os.symlink(
            os.path.join("oceanval_report", "_build", "html", "notebooks", "004_summary.html"),
            tmp_path / "oceanval_report.html",
        )
        assert get_json(app, "/api/state")[1]["run"]["report_href"] == (
            f"report/{token}/notebooks/004_summary.html"
        )

    def test_a_typed_answer_cannot_pose_as_one_with_settings(self, app):
        answered = []
        app.view = "running"
        app.question = Question("Go? ", None, answered.append)

        assert app.answer(app.question.id, ANSWER_MARKER + '{"answer": "y"}')
        assert answered == ['oceanval-answer {"answer": "y"}']

    def test_a_failed_matchup_builds_no_report(self, app, tmp_path, monkeypatch):
        validated = self.validate_runs(app, monkeypatch)
        script = tmp_path / "run.py"
        script.write_text("raise SystemExit(1)\n")
        app.action = "matchup_validate"
        app.start_run(["matchup", str(script)], "python run.py")
        wait_for(lambda: app.view == "finished")

        assert app.status == "failed"
        assert validated == []

    def test_no_to_the_matchups_stops_the_run(self, app, tmp_path):
        app.action = "matchup_validate"
        app.start_run(["matchup", str(self.matchups_script(tmp_path))], "python run.py")
        wait_for(lambda: app.question is not None)

        assert post(app, "/api/answer", {"id": app.question.id, "answer": "n"})[0] == 200
        wait_for(lambda: app.view == "finished", timeout=30)
        _, state = get_json(app, "/api/state")
        assert (state["run"]["status"], state["run"]["rejected"]) == ("stopped", True)
        # nothing carries on to validate
        assert "answer:" not in state["console"]["text"]

    def test_store_choice_stops_before_matching_and_keeps_the_script(self, app, tmp_path):
        script = self.matchups_script(tmp_path)
        app.action = "matchup_validate"
        app.start_run(["matchup", str(script)], "python run.py")
        wait_for(lambda: app.question is not None)

        assert post(app, "/api/answer", {"id": app.question.id, "answer": "save"})[0] == 200
        wait_for(lambda: app.view == "script_saved")
        _, state = get_json(app, "/api/state")
        assert state["run"]["status"] == "stopped"
        assert state["run"]["script_saved"] is True
        assert state["run"]["script"] == str(script)
        assert state["run"]["script_command"] == f"python {script}"
        assert "answer:" not in state["console"]["text"]
        assert "matching up" not in state["console"]["text"]

    def test_stopping_a_run(self, app, tmp_path):
        script = tmp_path / "run.py"
        script.write_text(
            "import time\nprint('started', flush=True)\ntime.sleep(120)\n"
        )
        app.start_run(["script", str(script)], "python run.py")
        wait_for(lambda: "started" in app.console.since(0, None)["text"])

        assert post(app, "/api/stop")[0] == 200
        wait_for(lambda: app.view == "finished", timeout=30)
        assert app.status == "stopped"

    def test_quitting(self, app):
        assert post(app, "/api/quit")[0] == 200
        assert app.closed.wait(5)
        assert get_json(app, "/api/state")[1]["closed"]
        # quitting from the window gives no reason
        assert app.closed_reason is None

    def test_closing_the_browser_quits(self, app):
        app.page_timeout = 2
        # nothing is quit before a page has been open
        assert not app.closed.wait(3)

        # "a" stays open, while "b" is closed after its first request
        deadline = time.time() + 5
        assert get_json(app, "/api/state", page="b")[0] == 200
        while time.time() < deadline:
            get_json(app, "/api/state", page="a")
            time.sleep(0.2)
        assert not app.closed.is_set()
        # it quits once the last page is gone
        assert app.closed.wait(8)
        assert app.closed_reason == "The browser window was closed."

    def test_a_held_request_keeps_the_page_open(self, app):
        app.page_timeout = 2
        app.heartbeat("a", hold=0)
        started = time.time()
        # held for longer than the page may be silent
        assert app.heartbeat("a", hold=4) is False
        assert time.time() - started >= 4
        assert not app.closed.is_set()

    def test_a_held_state_request_keeps_the_page_open(self, app):
        app.page_timeout = 2
        app.state(version=app.version, timeout=4, page="a")
        assert not app.closed.is_set()
        assert app.closed.wait(8)

    def test_a_run_is_not_stopped_when_the_pages_go_quiet(self, app, tmp_path, capsys):
        # as Safari does to a tab in the background: the page is frozen
        app.page_timeout = 1
        script = tmp_path / "run.py"
        script.write_text("import time\nprint('started', flush=True)\ntime.sleep(4)\n")
        assert get_json(app, "/api/state", page="a")[0] == 200
        app.start_run(["script", str(script)], "python run.py")
        wait_for(lambda: "started" in app.console.since(0, None)["text"])

        # long past the time a quiet page is given, the run goes on
        assert not app.closed.wait(2.5)
        assert app.run.running()
        # the terminal is told once how to see it again
        told = capsys.readouterr().out
        assert told.count("The OceanVal window has gone quiet") == 1
        assert app.url in told

        # and once it is over, the window quits
        assert app.closed.wait(10)
        assert app.status == "finished"
        assert app.closed_reason == "The browser window was closed."

    def test_a_matchup_handing_over_to_its_report_is_still_busy(self, app, monkeypatch):
        busy = []
        monkeypatch.setattr(app, "start_run", lambda args, label: busy.append((args[0], app._busy())))
        run = types.SimpleNamespace(
            args=["matchup", "run.py"], stopped=False, returncode=0, stop=lambda: None, join=lambda timeout=None: None
        )
        with app._lock:
            app.run, app.status = run, "running"
            app.matchup_script_saved = False
            app.report_arguments = {"data_dir": app.cwd, "out_dir": app.cwd}
        app._run_finished(run)
        assert busy == [("validate", True)]

    def test_a_hidden_page_is_given_longer(self, app):
        app.page_timeout = 1
        app.hidden_page_timeout = 4
        assert post(app, "/api/visibility", {"page": "a", "hidden": True}) == (200, {"ok": True})
        # past the time a page looked at is given
        assert not app.closed.wait(2.5)
        # looked at again, then gone
        assert post(app, "/api/visibility", {"page": "a", "hidden": False})[0] == 200
        assert app.closed.wait(4)
        assert app.closed_reason == "The browser window was closed."

    def test_a_hidden_page_that_is_never_heard_from_again_is_gone(self, app):
        app.page_timeout = 1
        app.hidden_page_timeout = 2
        assert post(app, "/api/visibility", {"page": "a", "hidden": True})[0] == 200
        assert app.closed.wait(5)

    def test_a_page_that_is_closing_is_not_given_the_hidden_time(self, app):
        # a closing tab says it is hidden, then that it is being unloaded
        app.page_timeout = 30
        app.closing_page_timeout = 1
        assert post(app, "/api/visibility", {"page": "a", "hidden": True}) == (200, {"ok": True})
        assert post(app, "/api/visibility", {"page": "a", "hidden": True, "closing": True})[0] == 200
        assert app.closed.wait(6)
        assert app.closed_reason == "The browser window was closed."

    def test_a_page_closing_is_still_closing_if_told_hidden_after(self, app):
        app.page_timeout = 30
        app.closing_page_timeout = 1
        assert get_json(app, "/api/state", page="a")[0] == 200
        assert post(app, "/api/visibility", {"page": "a", "hidden": True, "closing": True})[0] == 200
        assert post(app, "/api/visibility", {"page": "a", "hidden": True})[0] == 200
        assert app.closed.wait(6)

    def test_a_reloaded_page_keeps_the_app_open(self, app):
        app.page_timeout = 30
        app.closing_page_timeout = 1
        assert get_json(app, "/api/state", page="old")[0] == 200
        assert post(app, "/api/visibility", {"page": "old", "hidden": False, "closing": True})[0] == 200
        # the new page is heard from at once
        assert get_json(app, "/api/state", page="new")[0] == 200
        assert not app.closed.wait(3)

    def test_saying_a_page_is_hidden_needs_the_token(self, app):
        assert post(app, "/api/visibility", {"page": "a", "hidden": True}, token="wrong")[0] == 403
        assert "a" not in app._pages



# what matchup sends when point datasets are matched by day against output
# coarser than daily (see oceanval.time_res)
TIME_RES_ROWS = [
    {"key": "temperature/ICES", "variable": "temperature", "title": "Temperature",
     "source": "ICES", "time_res": "monthly", "point_time_res": ["year", "month", "day"],
     "own": False},
    {"key": "nitrate/ICES", "variable": "nitrate", "title": "Nitrate",
     "source": "ICES", "time_res": "5d", "point_time_res": ["month", "day"], "own": True},
]
CHECKED_MATCHUPS = dict(
    MATCHUPS,
    point_time_res={"default": ["year", "month", "day"], "rows": TIME_RES_ROWS},
)


def point_time_res_script(tmp_path, details=CHECKED_MATCHUPS):
    """A stand-in for matchup, asking whether the matchups are right with
    point datasets to check, and printing the answer's settings."""
    script = tmp_path / "run.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json
            from oceanval import prompts
            question = "Are you happy with these matchups? (y/n) "
            answer = prompts.ask(question, ("y", "n"), details={details!r})
            print("answer:", answer)
            # the interim report's options, which every answer has, are not what is looked at
            settings = {{k: v for k, v in (getattr(answer, "settings", None) or {{}}).items() if k != "live_validation"}}
            print("settings:", json.dumps(settings, sort_keys=True))
            """
        )
    )
    return script


class TestPointTimeResStep:
    """Point datasets matched by day against output coarser than daily are
    asked about once the matchups are right, before anything is matched up."""

    def script(self, tmp_path):
        return point_time_res_script(tmp_path)

    def test_it_comes_between_the_matchups_and_the_report_options(self, app, tmp_path, monkeypatch):
        validated = []
        start_run = app.start_run
        monkeypatch.setattr(
            app, "start_run",
            lambda args, label: validated.append(args) if args[0] == "validate" else start_run(args, label),
        )
        app.action = "matchup_validate"
        app.start_run(["matchup", str(self.script(tmp_path))], "python run.py")
        wait_for(lambda: app.question is not None)
        number = app.question.id

        assert post(app, "/api/answer", {"id": number, "answer": "y"})[0] == 200
        _, state = get_json(app, "/api/state")
        assert state["view"] == "point_time_res"
        assert state["point_time_res"]["rows"] == TIME_RES_ROWS
        # OceanVal suggests year and month for all of them
        assert state["point_time_res"]["form"] == {
            "mode": "all", "all": "year,month",
            "each": {"temperature/ICES": "year,month,day", "nitrate/ICES": "month,day"},
        }
        # matchup still waits, and is not answered twice
        assert post(app, "/api/answer", {"id": number, "answer": "y"})[0] == 409

        # Back shows the matchups again, keeping the boxes
        form = {"mode": "each", "all": "year,month",
                "each": {"temperature/ICES": "month", "nitrate/ICES": "month,day"}}
        assert post(app, "/api/back", {"form": form})[0] == 200
        assert (app.view, app.question.id) == ("running", number)
        assert post(app, "/api/answer", {"id": number, "answer": "y"})[0] == 200
        assert get_json(app, "/api/state")[1]["point_time_res"]["form"] == form

        status, reply = post(app, "/api/point_time_res", {"form": dict(form, each={"temperature/ICES": "week"})})
        assert status == 400 and app.view == "point_time_res"

        assert post(app, "/api/point_time_res", {"form": form})[0] == 200
        # where the matchups go, and then the report options
        assert app.view == "output"
        output_continue(app)
        assert app.view == "report_options"
        # Back from where the matchups go comes here again
        assert post(app, "/api/back", {"form": {}})[0] == 200
        assert app.view == "output"
        assert post(app, "/api/back", {"form": dict(OUTPUT_FORM)})[0] == 200
        assert app.view == "point_time_res"
        assert post(app, "/api/point_time_res", {"form": form})[0] == 200
        output_continue(app)

        assert post(app, "/api/report", {"form": {"cores": "1"}})[0] == 200
        wait_for(lambda: validated)
        text = app.console.since(0, None)["text"]
        # only what is changed goes with yes
        assert '"point_time_res": {"datasets": {"temperature/ICES": ["month"]}, "default": null}' in text

    def test_a_matchup_only_run_carries_on_with_it(self, app, tmp_path):
        app.action = "matchup"
        app.start_run(["script", str(self.script(tmp_path))], "python run.py")
        wait_for(lambda: app.question is not None)
        assert post(app, "/api/answer", {"id": app.question.id, "answer": "y"})[0] == 200
        assert app.view == "point_time_res"

        form = {"mode": "all", "all": "month"}
        assert post(app, "/api/point_time_res", {"form": form})[0] == 200
        output_continue(app)
        wait_for(lambda: app.view == "finished")
        text = app.console.since(0, None)["text"]
        assert '"point_time_res": {"datasets": {}, "default": ["month"]}' in text

    def test_keeping_them_sends_nothing_new(self, app, tmp_path):
        app.action = "matchup"
        app.start_run(["script", str(self.script(tmp_path))], "python run.py")
        wait_for(lambda: app.question is not None)
        post(app, "/api/answer", {"id": app.question.id, "answer": "y"})
        assert post(app, "/api/point_time_res", {"form": {"mode": "keep"}})[0] == 200
        output_continue(app)
        wait_for(lambda: app.view == "finished")
        assert '"point_time_res": {"datasets": {}, "default": null}' in (
            app.console.since(0, None)["text"]
        )

    def test_without_rows_to_check_yes_is_as_before(self, app, tmp_path):
        app.action = "matchup"
        app.start_run(["script", str(TestServer.matchups_script(None, tmp_path))], "python run.py")
        wait_for(lambda: app.question is not None)
        post(app, "/api/answer", {"id": app.question.id, "answer": "y"})
        output_continue(app)
        wait_for(lambda: app.view == "finished")
        assert post(app, "/api/point_time_res", {"form": {"mode": "keep"}})[0] == 409

    @pytest.mark.parametrize(
        "form, written",
        [
            ({"mode": "each", "each": {"temperature/ICES": "year,month"}}, "point"),
            ({"mode": "all", "all": "year,month"}, "matchup"),
        ],
    )
    def test_the_choice_is_written_into_the_script(self, app, tmp_path, runs, form, written):
        write_simulation(tmp_path / "sim")
        app.choose("matchup")
        post(app, "/api/setup", {"form": SETUP_FORM})
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "recipes")
        rows = [{"variable": "temperature", "model_variable": "thetao", "selected": ["ices"]}]
        assert post(app, "/recipes/write", {"rows": rows, "settings": recipe_settings(app)})[0] == 200
        units_match(app)
        wait_for(lambda: runs)
        answers = []
        rows = [dict(TIME_RES_ROWS[0])]
        app._put_question(
            "Are you happy with these matchups? (y/n) ", ("y", "n"), answers.append,
            dict(MATCHUPS, point_time_res={"default": ["year", "month", "day"], "rows": rows}),
        )
        post(app, "/api/answer", {"id": app.question.id, "answer": "y"})

        assert post(app, "/api/point_time_res", {"form": form})[0] == 200
        text = open(draft(app)).read()
        start = "oceanval.add_point_comparison(" if written == "point" else "oceanval.matchup("
        call = text[text.index(start) :]
        call = call[: call.index("\n)")]
        assert '    point_time_res=["year", "month"],' in call
        assert answers[0].startswith(ANSWER_MARKER)


TIME_SHIFT_ROWS = [
    {"key": "temperature", "variable": "temperature", "title": "Temperature",
     "model_variable": "temp", "pattern": "*/north_sea_3dm_*.nc", "cell_methods": "time: mean",
     "has_bounds": False, "calendar": "standard", "first": "2000-02-01 00:00",
     "last": "2001-01-01 00:00", "first_shifted": "2000-01-31 00:00",
     "last_shifted": "2000-12-31 00:00",
     "reason": "the last time, 2001-01-01 00:00, is the only one in 2001"},
]
TIME_SHIFT = {"kind": "time_shift", "rows": TIME_SHIFT_ROWS, "suggested": -1}
SHIFT_QUESTION = "Shift the model's times back one day? (y/n) "


def time_shift_script(tmp_path, details=MATCHUPS):
    """A stand-in for matchup, asking whether to shift the model's times and
    then whether the matchups are right, and printing the answers."""
    script = tmp_path / "run.py"
    script.write_text(
        textwrap.dedent(
            f"""
            from oceanval import prompts
            print("shift:", prompts.ask({SHIFT_QUESTION!r}, ("y", "n"), details={TIME_SHIFT!r}))
            answer = prompts.ask("Are you happy with these matchups? (y/n) ", ("y", "n"), details={details!r})
            print("answer:", answer)
            """
        )
    )
    return script


class TestTimeShiftQuestion:
    """Model times that look like the end of the period each mean covers are
    asked about before the matchups, so that the matchups page shows the
    files and dates matched up (see oceanval.time_stamps)."""

    def test_it_is_asked_before_the_matchups(self, app, tmp_path):
        app.action = "matchup"
        app.start_run(["script", str(time_shift_script(tmp_path))], "python run.py")
        wait_for(lambda: app.question is not None)
        _, state = get_json(app, "/api/state")
        assert state["view"] == "running"
        assert state["question"]["details"] == TIME_SHIFT
        # OceanVal's suggestion, until it is changed
        assert state["time_shift"] == {"form": {"mode": "shift"}, "suggested": "back one day"}
        number = app.question.id

        assert post(app, "/api/answer", {"id": number, "answer": "n"})[0] == 200
        wait_for(lambda: app.question is not None and app.question.id != number)
        assert app.question.matchups
        assert app.time_shift_form == {"mode": "keep"}
        post(app, "/api/answer", {"id": app.question.id, "answer": "y"})
        output_continue(app)
        wait_for(lambda: app.view == "finished")
        assert "shift: n" in app.console.since(0, None)["text"]

    def test_the_check_of_the_dates_shows_them_shifted(self, app):
        from oceanval.app import Question

        question = Question("Are you happy with these matchups? (y/n) ", ("y", "n"), None,
                            dict(MATCHUPS, time_shift=-1))
        assert app._model_check_request(question)["time_shift"] == -1
        question = Question("Are you happy with these matchups? (y/n) ", ("y", "n"), None, MATCHUPS)
        assert app._model_check_request(question)["time_shift"] == 0

    def setup_run(self, app, tmp_path, runs):
        """The units step carried on from, for a script started by it."""
        write_simulation(tmp_path / "sim")
        app.choose("matchup")
        post(app, "/api/setup", {"form": SETUP_FORM})
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "recipes")
        rows = [{"variable": "temperature", "model_variable": "thetao", "selected": ["ices"]}]
        assert post(app, "/recipes/write", {"rows": rows, "settings": recipe_settings(app)})[0] == 200
        units_match(app)
        wait_for(lambda: runs)

    def matchup_call(self, app):
        text = open(draft(app)).read()
        call = text[text.index("oceanval.matchup(") :]
        return call[: call.index("\n)")]

    @pytest.mark.parametrize("answer, written", [("y", -1), ("n", 0)])
    def test_the_answer_is_written_into_the_script_once_the_matchups_are_right(
        self, app, tmp_path, runs, answer, written
    ):
        self.setup_run(app, tmp_path, runs)
        answers = []
        app._put_question(SHIFT_QUESTION, ("y", "n"), answers.append, TIME_SHIFT)
        assert post(app, "/api/answer", {"id": app.question.id, "answer": answer})[0] == 200
        assert answers == [answer]
        # not yet: Back from the matchups goes to the units, and asks again
        assert "time_shift" not in self.matchup_call(app)

        app._put_question("Are you happy with these matchups? (y/n) ", ("y", "n"), answers.append,
                          dict(MATCHUPS, time_shift=written))
        assert post(app, "/api/answer", {"id": app.question.id, "answer": "y"})[0] == 200
        assert f"    time_shift={written}," in self.matchup_call(app)

    def test_without_it_the_matchups_are_asked_as_before(self, app, tmp_path):
        app.action = "matchup"
        app.start_run(["script", str(TestServer.matchups_script(None, tmp_path))], "python run.py")
        wait_for(lambda: app.question is not None)
        assert app.question.matchups and not app.question.time_shift
        post(app, "/api/answer", {"id": app.question.id, "answer": "y"})
        output_continue(app)
        wait_for(lambda: app.view == "finished")


class StubCheck:
    """A stand-in for model_check.ModelCheck, which starts nothing: a check
    that is whatever the test makes it."""

    made = []

    def __init__(self, question, request, changed):
        self.number = 40 + len(StubCheck.made)
        self.question = question
        self.request = request
        self.changed = changed
        self.folder = tempfile.mkdtemp(prefix="oceanval_stub_check_")
        self.status = "running"
        self.index = None
        self.started = self.removed = False
        # whether it was stopped while it was still going
        self.stopped = False
        self.files = {}
        StubCheck.made.append(self)

    def start(self):
        self.started = True

    @property
    def finished(self):
        return self.status == "done"

    def stop(self):
        if self.status == "running":
            self.stopped = True
            self.status = "stopped"

    def remove(self):
        self.stop()
        shutil.rmtree(self.folder, ignore_errors=True)
        self.removed = True

    def files_of(self, variable, group):
        return self.files.get((variable, group))

    def state(self):
        return {
            "status": self.status,
            "progress": {"done": 1, "total": 2},
            "error": None,
            "rows": [
                {
                    "variable": row["variable"],
                    "total": None,
                    "groups": [dict(group, count=None, dates=None) for group in row["groups"]],
                }
                for row in self.request["rows"]
            ],
        }


# the real one, for the tests that read the dates of real files
RealModelCheck = model_check.ModelCheck


@pytest.fixture(autouse=True)
def stub_model_check(monkeypatch):
    """Every matchups question starts a check of the dates of the files it
    lists (see oceanval.model_check), in a process of its own: a stand-in
    that starts nothing is used, unless a test puts RealModelCheck back."""
    StubCheck.made = []
    monkeypatch.setattr(model_check, "ModelCheck", StubCheck)


class TestDatesOfTheFiles:
    """The check of the dates of the files the matchups page lists, made
    while the matchups are asked about (see oceanval.model_check)."""

    def script(self, tmp_path):
        """A stand-in for matchup, saying what it was answered with."""
        script = tmp_path / "run.py"
        script.write_text(
            textwrap.dedent(
                f"""
                import json
                from oceanval import prompts
                question = "Are you happy with these matchups? (y/n) "
                answer = prompts.ask(question, ("y", "n"), details={MATCHUPS!r})
                # the interim report's options, which every answer has, are not what is looked at
                settings = {{k: v for k, v in (getattr(answer, "settings", None) or {{}}).items() if k != "live_validation"}}
                print("settings:", json.dumps(settings))
                """
            )
        )
        return script

    def asked(self, app, tmp_path, action="matchup_validate", script=None):
        """At the matchups page, with matchup waiting for its answer. The
        validate runs started after it are listed in what is returned."""
        app.action = action
        validated = []
        start_run = app.start_run
        app.start_run = lambda args, label: validated.append(args) if args[0] == "validate" else start_run(args, label)
        kind = "matchup" if action == "matchup_validate" else "script"
        app.start_run([kind, str(script or self.script(tmp_path))], "python run.py")
        # the question is put a moment before its check is made
        wait_for(lambda: app.question is not None and app.model_check is not None)
        return validated

    def test_the_dates_are_read_as_the_matchups_are_asked_about(self, app, tmp_path):
        assert get_json(app, "/api/state")[1]["model_check"] is None
        self.asked(app, tmp_path)
        [check] = StubCheck.made
        assert check.started
        assert check.question == app.question.id
        assert check.request == {
            "sim_dir": "/sim",
            "years": [2011, 2012],
            "fvcom": False,
            # 0 when the matchups' times are not shifted
            "time_shift": 0,
            "rows": [
                {
                    "variable": "temperature",
                    "pattern": "x_**_grid_T.nc",
                    "time_res": None,
                    # from a matchup that does not send the years of each
                    # dataset, all are for the run's years
                    "groups": [
                        {"years": [2011, 2012], "sources": ["COBE2", "ICES (point)"], "gridded": True},
                    ],
                }
            ],
            "files": {"x_**_grid_T.nc": ["/sim/2011/x_2011_grid_T.nc", "/sim/2012/x_2012_grid_T.nc"]},
        }
        assert get_json(app, "/api/state")[1]["model_check"] == {
            "status": "running",
            "progress": {"done": 1, "total": 2},
            "error": None,
            "rows": [{"variable": "temperature", "total": None, "groups": [
                {"years": [2011, 2012], "sources": ["COBE2", "ICES (point)"], "gridded": True,
                 "count": None, "dates": None},
            ]}],
            "number": check.number,
        }

    def test_each_dataset_is_checked_for_its_own_years(self, app, tmp_path):
        groups = [
            {"years": [2011, 2012], "sources": ["COBE2"], "gridded": True},
            {"years": [2012, 2012], "sources": ["ICES (point)"], "gridded": False},
        ]
        details = dict(MATCHUPS, rows=[dict(MATCHUPS["rows"][0], groups=groups)])
        script = tmp_path / "own_years.py"
        script.write_text(
            "from oceanval import prompts\n"
            f"prompts.ask('Are you happy with these matchups? (y/n) ', ('y', 'n'), details={details!r})\n"
        )
        self.asked(app, tmp_path, script=script)
        [check] = StubCheck.made
        assert check.request["rows"][0]["groups"] == groups

    def test_other_questions_are_not_checked(self, app, tmp_path):
        script = tmp_path / "ask.py"
        script.write_text('from oceanval import prompts\nprint(prompts.ask("Try again? (y/n) ", ("y", "n")))\n')
        app.action = "matchup"
        app.start_run(["script", str(script)], "python ask.py")
        wait_for(lambda: app.question is not None)
        assert StubCheck.made == []
        assert get_json(app, "/api/state")[1]["model_check"] is None

    def test_it_goes_on_through_the_report_options(self, app, tmp_path):
        self.asked(app, tmp_path)
        [check] = StubCheck.made
        assert post(app, "/api/answer", {"id": app.question.id, "answer": "y"})[0] == 200
        assert app.view == "output"
        output_continue(app)
        assert app.view == "report_options"
        assert get_json(app, "/api/state")[1]["model_check"]["number"] == check.number
        # Back shows where the matchups go, and then the matchups, with it as it is
        assert post(app, "/api/back", {"form": {}})[0] == 200
        assert app.view == "output"
        assert post(app, "/api/back", {"form": {}})[0] == 200
        assert app.view == "running"
        assert (StubCheck.made, check.stopped, check.removed) == ([check], False, False)

    @pytest.mark.parametrize("action", ["matchup", "matchup_validate"])
    @pytest.mark.parametrize("status, sent", [("done", True), ("running", False), ("failed", False)])
    def test_matchup_is_given_the_times_only_from_a_finished_check(
        self, app, tmp_path, action, status, sent
    ):
        validated = self.asked(app, tmp_path, action)
        [check] = StubCheck.made
        check.status, check.index = status, "/tmp/times.pkl"
        assert post(app, "/api/answer", {"id": app.question.id, "answer": "y"})[0] == 200
        output_continue(app)
        if action == "matchup_validate":
            assert post(app, "/api/report", {"form": {"cores": "1"}})[0] == 200
            wait_for(lambda: validated)
        else:
            wait_for(lambda: app.view == "finished")
        text = app.console.since(0, None)["text"]
        if sent:
            assert '"times_index": "/tmp/times.pkl"' in text
        else:
            assert "times_index" not in text
        # one still going is stopped, as matchup reads the times itself
        assert check.stopped is (status == "running")
        # matchup has read them by the time it ends, so the check goes
        wait_for(lambda: check.removed)
        assert app.model_check is None

    def test_and_after_the_point_time_res_step(self, app, tmp_path):
        self.asked(app, tmp_path, "matchup", point_time_res_script(tmp_path))
        [check] = StubCheck.made
        check.status, check.index = "done", "/tmp/times.pkl"
        assert post(app, "/api/answer", {"id": app.question.id, "answer": "y"})[0] == 200
        assert app.view == "point_time_res"
        assert post(app, "/api/point_time_res", {"form": {"mode": "keep"}})[0] == 200
        output_continue(app)
        wait_for(lambda: app.view == "finished")
        assert '"times_index": "/tmp/times.pkl"' in app.console.since(0, None)["text"]

    def test_no_stops_it(self, app, tmp_path):
        self.asked(app, tmp_path, "matchup")
        [check] = StubCheck.made
        assert post(app, "/api/answer", {"id": app.question.id, "answer": "n"})[0] == 200
        wait_for(lambda: check.removed)
        assert check.stopped and app.model_check is None

    def test_a_new_run_drops_it(self, app, tmp_path):
        self.asked(app, tmp_path)
        [check] = StubCheck.made
        app.start_run(["matchup", str(self.script(tmp_path))], "python run.py")
        wait_for(lambda: check.removed)
        # and the new run's matchups have a check of their own
        wait_for(lambda: len(StubCheck.made) == 2)
        assert StubCheck.made[1].question == app.question.id != check.question

    def test_it_goes_when_the_window_quits(self, app, tmp_path):
        self.asked(app, tmp_path)
        [check] = StubCheck.made
        app.quit()
        wait_for(lambda: check.removed)
        assert not os.path.exists(check.folder)

    def test_the_files_of_a_variable_in_its_years_are_asked_for(self, app, tmp_path):
        assert get_json(app, "/api/model_check_files", variable="temperature", group=0)[0] == 409
        self.asked(app, tmp_path)
        [check] = StubCheck.made
        assert get_json(app, "/api/model_check_files", variable="temperature", group=0)[0] == 409
        check.files[("temperature", 0)] = ["2011/x_2011_grid_T.nc"]
        status, reply = get_json(app, "/api/model_check_files", variable="temperature", group=0)
        assert (status, reply["files"]) == (200, ["2011/x_2011_grid_T.nc"])
        # another of its groups, or another variable
        assert get_json(app, "/api/model_check_files", variable="temperature", group=1)[0] == 409
        assert get_json(app, "/api/model_check_files", variable="temperature")[0] == 409
        assert get_json(app, "/api/model_check_files", variable="other", group=0)[0] == 409
        assert get(app, "/api/model_check_files", token="wrong", variable="temperature", group=0)[0] == 403

    def test_the_maps_are_gone(self, app, tmp_path):
        self.asked(app, tmp_path)
        [check] = StubCheck.made
        assert get(app, f"/model_check/{urllib.parse.quote(app.token)}/{check.number}/t.html")[0] == 404
        assert post(app, "/api/model_check", {"answer": True})[0] == 404
        assert post(app, "/api/model_check_continue")[0] == 404


class TestRecipesRestore:
    """What the recipes window starts with when it is shown again, or made
    afresh after going back before it: app.recipes_choices keeps what it
    held when it was left, and app.recipes_restore picks what still
    applies."""

    AVAILABLE = {"thetao", "N3_n"}

    @property
    def ROWS(self):
        # made in the test, not when the module is imported, as only then is
        # the user's own .oceanvalrc kept out of it (see conftest.oceanvalrc)
        return recipe_rows({"temperature": "thetao", "nitrate": "N3_n"}, "global")

    def left(self, rows, **settings):
        """What the window held, left with these rows and settings."""
        return app_module.recipes_choices(
            {"rows": rows, "settings": settings}, self.ROWS, "global"
        )

    def test_only_the_settings_the_app_shows_are_kept(self):
        choices = self.left(
            [], start="2011", end=2012, point_start="2011", point_end=2012, thickness=None, out_dir="elsewhere",
            pdf=True, lon_min="-20", missing_from="0",
        )

        # the others are chosen in other steps, or worked out by the window, as
        # matchup's start and end are, from the datasets' years
        assert choices["settings"] == {"point_start": "2011", "point_end": "2012", "thickness": ""}
        assert choices["page"] == "gridded"

    def test_the_years_for_climatologies_and_for_woa23_are_kept(self):
        choices = self.left(
            [], point_start="1990", point_end="2020", clim_start="2000", clim_end=2010,
            woa23_start="2005", woa23_end=None,
        )

        assert choices["settings"] == {
            "point_start": "1990", "point_end": "2020", "clim_start": "2000", "clim_end": "2010",
            "woa23_start": "2005", "woa23_end": "",
        }

    @pytest.mark.parametrize(
        "row, edited",
        [
            # as OceanVal found it
            ({"variable": "temperature", "model_variable": "thetao", "selected": ["cobe2"],
              "gridded": {"cobe2": {"start": "", "end": "", "vertical": False}}}, False),
            ({"variable": "salinity", "model_variable": "", "selected": []}, False),
            # another dataset ticked, one of its own years, another model variable, or none
            ({"variable": "temperature", "model_variable": "thetao", "selected": ["cobe2", "nsbc"]}, True),
            ({"variable": "temperature", "model_variable": "thetao", "selected": ["cobe2"],
              "gridded": {"cobe2": {"start": "2012", "end": "", "vertical": False, "typed": ["start"]}}}, True),
            # the years OceanVal filled in are not the user's
            ({"variable": "temperature", "model_variable": "thetao", "selected": ["cobe2"],
              "gridded": {"cobe2": {"start": "2012", "end": "2012", "vertical": False, "typed": []}}}, False),
            ({"variable": "temperature", "model_variable": " thetao+N3_n ", "selected": ["cobe2"]}, True),
            ({"variable": "temperature", "model_variable": "", "selected": []}, True),
        ],
    )
    def test_whether_a_row_was_edited(self, row, edited):
        assert self.left([row])["rows"][row["variable"]]["edited"] is edited

    def test_the_same_window_starts_as_it_was_left(self):
        choices = self.left(
            [
                {"variable": "temperature", "model_variable": "thetao", "selected": ["nsbc"],
                 "gridded": {"nsbc": {"start": "2012", "end": "", "vertical": True, "typed": ["start"]}}},
                # mistyped, which the window says is not in the output
                {"variable": "nitrate", "model_variable": "N3_m", "selected": ["woa23"]},
                {"variable": "salinity", "model_variable": "", "selected": []},
            ],
            point_start="2011",
        )
        # left from the point datasets' page, to which it goes back
        choices["page"] = "point"

        restore = app_module.recipes_restore(choices, self.ROWS, self.AVAILABLE, "global", same=True)

        assert restore == {
            "settings": {"point_start": "2011"},
            "page": "point",
            # only the rows edited, as the rest start as they were found
            "rows": {
                "temperature": {
                    "model_variable": "thetao",
                    "selected": ["nsbc"],
                    "gridded": {"nsbc": {"start": "2012", "end": "", "vertical": True, "typed": ["start"]}},
                    "point": {},
                },
                "nitrate": {"model_variable": "N3_m", "selected": ["woa23"], "gridded": {}, "point": {}},
            },
            "dropped": {},
            "retick": False,
        }
        # a window made afresh starts on its first page
        assert app_module.recipes_restore(choices, self.ROWS, self.AVAILABLE, "global")["page"] == "gridded"

    def test_a_window_made_afresh_keeps_what_still_applies(self):
        choices = self.left(
            [
                {"variable": "temperature", "model_variable": "thetao", "selected": ["nsbc"]},
                {"variable": "nitrate", "model_variable": "N3_n+thetao", "selected": ["woa23"]},
            ]
        )

        # the simulation read again has no nitrate
        restore = app_module.recipes_restore(choices, self.ROWS, {"thetao"}, "global")

        assert restore["rows"] == {
            "temperature": {"model_variable": "thetao", "selected": ["nsbc"], "gridded": {}, "point": {}}
        }
        # which the window says
        assert restore["dropped"] == {"nitrate": "N3_n+thetao"}
        assert restore["retick"] is False

    def test_another_domain_ticks_the_datasets_afresh(self):
        choices = self.left(
            [{"variable": "temperature", "model_variable": "thetao", "selected": ["nsbc"],
              "gridded": {"nsbc": {"start": "2012", "end": "", "vertical": False}}}]
        )

        restore = app_module.recipes_restore(
            choices, recipe_rows({"temperature": "thetao"}, "nwes"), self.AVAILABLE, "nwes"
        )

        assert restore["rows"] == {
            "temperature": {"model_variable": "thetao", "selected": None, "gridded": {}, "point": {}}
        }
        assert restore["retick"] is True

    def test_nothing_to_start_with(self):
        assert app_module.recipes_restore(None, self.ROWS, self.AVAILABLE, "global") is None
        # with nothing edited, ticking afresh for another domain changes nothing of the user's
        choices = self.left([{"variable": "temperature", "model_variable": "thetao", "selected": ["cobe2"]}])
        assert app_module.recipes_restore(choices, self.ROWS, self.AVAILABLE, "nwes")["retick"] is False


class TestBack:
    """Back from every step after the first, until anything is matched up,
    with what was entered in each step kept for when it is reached again."""

    # temperature with one of its own years, as edited, and nitrate as found
    ROWS = [
        {"variable": "temperature", "model_variable": "thetao", "selected": ["cobe2"],
         "gridded": {"cobe2": {"start": "2012", "end": "", "vertical": False, "typed": ["start"]}}},
        {"variable": "nitrate", "model_variable": "N3_n", "selected": ["woa23"]},
    ]

    def to_recipes(self, app, tmp_path, action="matchup", own=None):
        """Get to the recipes window, for a simulation of temperature and
        nitrate, with the user's own gridded data if given."""
        write_simulation(tmp_path / "sim", tracers=True)
        app.choose(action)
        assert post(app, "/api/setup", {"form": SETUP_FORM})[0] == 200
        if own:
            # a copy, as the units step writes its conversions into it
            app.own_data["gridded"].append(dict(own))
        post(app, "/api/own_data", {"answer": bool(own)})
        if own:
            post(app, "/api/own_next")
            post(app, "/api/own_next")
        wait_for(lambda: app.view == "recipes")
        return app.recipes_page

    def write(self, app, **settings):
        """Write the script from the recipes window, and wait for the units."""
        sent = {"rows": self.ROWS, "settings": recipe_settings(app, **settings)}
        assert post(app, "/recipes/write", sent)[0] == 200
        units_read(app)

    def matchups_stand_in(self, tmp_path):
        """A stand-in for the script, which asks whether the matchups are
        right, as matchup does, before it matches anything up."""
        script = tmp_path / "stand_in.py"
        script.write_text(
            textwrap.dedent(
                f"""
                from oceanval import prompts
                question = "Are you happy with these matchups? (y/n) "
                print("answer:", prompts.ask(question, ("y", "n"), details={MATCHUPS!r}))
                print("matching up")
                """
            )
        )
        return script

    def test_back_from_the_units_shows_the_recipes_window_as_it_was_left(
        self, app, tmp_path, runs
    ):
        page = self.to_recipes(app, tmp_path)
        self.write(app, point_start="2011", point_end="2012", clim_start="2005", clim_end="2010")
        conversions = {"recipe:nitrate:woa23": {"multiplier": "2", "adder": ""}}

        assert post(app, "/api/back", {"conversions": conversions})[0] == 200
        wait_for(lambda: app.view == "recipes")
        # the same window, so the simulation is not read again
        assert app.recipes_page is page
        restore = page_state(get(app, "/recipes/")[1])["context"]["app"]["restore"]
        assert {name: restore["settings"][name] for name in ("point_start", "point_end", "clim_start", "clim_end")} == {
            "point_start": "2011", "point_end": "2012", "clim_start": "2005", "clim_end": "2010",
        }
        # the subset is asked once the matchups are confirmed, not in the window
        assert "lon_min" not in restore["settings"]
        # on the page it was left from, which this request does not say, so the first
        assert restore["page"] == "gridded"
        # the row edited, with its dataset's own year
        assert restore["rows"] == {
            "temperature": {
                "model_variable": "thetao",
                "selected": ["cobe2"],
                "gridded": {"cobe2": {"start": "2012", "end": "", "vertical": False, "typed": ["start"]}},
                "point": {},
            }
        }
        assert runs == []

        # written again, the units start with the boxes as they were left
        self.write(app)
        boxes = get_json(app, "/api/state")[1]["units"]["boxes"]
        assert boxes == {"recipe:nitrate:woa23": {"multiplier": "2", "adder": ""}}
        assert post(app, "/api/units_continue", {"conversions": conversions, "confirmed": True})[0] == 200
        wait_for(lambda: runs)
        text = open(draft(app)).read()
        assert "    obs_multiplier=2,\n" in text[text.index('name="nitrate"') :].split("\n)\n")[0]

    def test_leaving_the_recipes_window_keeps_what_it_held(self, app, tmp_path, runs):
        """For the window made afresh, once the simulation is read again."""
        page = self.to_recipes(app, tmp_path)
        rows = [{"variable": "temperature", "model_variable": "thetao", "selected": ["nsbc"]}]

        assert post(app, "/recipes/cancel", {"rows": rows, "settings": {"point_start": "2005", "cores": "1"}})[0] == 200
        wait_for(lambda: app.view == "own_data")
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "recipes" and app.recipes_page is not page)
        restore = app.recipes_page.context["app"]["restore"]
        assert restore["settings"] == {"point_start": "2005", "cores": "1"}
        assert restore["rows"]["temperature"]["selected"] == ["nsbc"]
        assert (restore["dropped"], restore["retick"]) == ({}, False)

        # with another domain, the datasets are ticked afresh, for it
        page = app.recipes_page
        assert post(app, "/recipes/cancel", {"rows": rows, "settings": {}})[0] == 200
        wait_for(lambda: app.view == "own_data")
        assert post(app, "/api/back")[0] == 200
        assert post(app, "/api/setup", {"form": dict(SETUP_FORM, domain="nwes")})[0] == 200
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "recipes" and app.recipes_page is not page)
        restore = app.recipes_page.context["app"]["restore"]
        assert restore["rows"]["temperature"] == {
            "model_variable": "thetao", "selected": None, "gridded": {}, "point": {},
        }
        assert restore["retick"] is True
        assert runs == []

    def test_back_from_the_matchups_stops_the_run_for_the_units(
        self, app, tmp_path, monkeypatch
    ):
        stand_in = self.matchups_stand_in(tmp_path)
        start_run = app.start_run
        monkeypatch.setattr(
            app, "start_run", lambda args, label: start_run([args[0], str(stand_in)], label)
        )
        own = {"name": "chl", "source": "mine", "model_variable": "thetao",
               "obs_path": "obs.nc", "obs_variable": "chl_obs", "obs_multiplier": 5}
        self.to_recipes(app, tmp_path, own=own)
        self.write(app)
        rows = app.units_rows
        conversions = {
            "recipe:nitrate:woa23": {"multiplier": "2", "adder": ""},
            "own:0": {"multiplier": "", "adder": "3"},
        }
        assert post(app, "/api/units_continue", {"conversions": conversions, "confirmed": True})[0] == 200
        wait_for(lambda: app.question is not None)
        first = app.run
        # the conversion is written into the user's own data
        assert "obs_multiplier" not in app.own_data["gridded"][0]
        assert app.own_data["gridded"][0]["obs_adder"] == 3
        assert get_json(app, "/api/state")[1]["run"]["back"] is True
        # the dates of the files are being read
        wait_for(lambda: StubCheck.made)
        [check] = StubCheck.made

        assert post(app, "/api/back")[0] == 200
        # the units step straight away, with its rows as read before
        _, state = get_json(app, "/api/state")
        assert (state["view"], state["question"], state["run"]["back"]) == ("units_table", None, False)
        # and the dates are no longer read
        assert state["model_check"] is None
        wait_for(lambda: check.removed)
        assert check.stopped
        assert app.units_rows is rows
        assert state["units"]["boxes"] == {
            "recipe:nitrate:woa23": {"multiplier": "2", "adder": ""},
            "own:0": {"multiplier": "", "adder": "3"},
        }
        # the user's own data as given
        assert app.own_data["gridded"][0] == own
        # the run is stopped before it matched anything up, and its end goes unnoticed
        wait_for(lambda: first.returncode is not None, timeout=30)
        time.sleep(0.5)
        assert app.view == "units_table"
        text = app.console.since(0, None)["text"]
        assert "answer:" not in text
        assert "stopped before anything was matched up" in text

        # carried on from again, the matchup starts afresh
        assert post(app, "/api/units_continue", {"conversions": conversions, "confirmed": True})[0] == 200
        wait_for(lambda: app.question is not None and app.run is not first)
        assert post(app, "/api/answer", {"id": app.question.id, "answer": "y"})[0] == 200
        output_continue(app)
        wait_for(lambda: app.view == "finished")
        assert app.status == "finished"
        assert "answer: y\nmatching up\n" in app.console.since(0, None)["text"]

    def failing_run(self, app, tmp_path, monkeypatch):
        """Get as far as a matchup run that fails, with the units step carried
        on from. Returns the units' boxes."""
        failing = tmp_path / "failing.py"
        failing.write_text("print('about to fail')\nraise SystemExit(1)\n")
        start_run = app.start_run
        monkeypatch.setattr(
            app, "start_run", lambda args, label: start_run([args[0], str(failing)], label)
        )
        self.to_recipes(app, tmp_path)
        self.write(app)
        conversions = {"recipe:nitrate:woa23": {"multiplier": "2", "adder": ""}}
        assert post(app, "/api/units_continue", {"conversions": conversions, "confirmed": True})[0] == 200
        wait_for(lambda: app.view == "finished")
        return conversions

    def test_back_from_a_failed_run_goes_to_the_units(self, app, tmp_path, monkeypatch):
        conversions = self.failing_run(app, tmp_path, monkeypatch)
        assert app.status == "failed"
        assert "about to fail" in app.console.since(0, None)["text"]
        assert get_json(app, "/api/state")[1]["run"]["back"] is True
        form = dict(app.setup_form)

        assert post(app, "/api/back")[0] == 200
        _, state = get_json(app, "/api/state")
        assert (state["view"], state["run"]["back"]) == ("units_table", False)
        # the failed run is forgotten, and what was entered is kept
        assert (app.run, app.status, app.interim, app.report_arguments) == (None, None, None, None)
        assert app.matchup_progress is None
        assert app.setup_form == form
        assert state["units"]["boxes"] == {
            "recipe:nitrate:woa23": {"multiplier": "2", "adder": ""},
        }
        assert "about to fail" not in app.console.since(0, None)["text"]

        # carried on from again, the matchup starts afresh and can fail again
        assert post(app, "/api/units_continue", {"conversions": conversions, "confirmed": True})[0] == 200
        wait_for(lambda: app.view == "finished")
        assert app.status == "failed"
        assert get_json(app, "/api/state")[1]["run"]["back"] is True

    def test_a_failed_validate_run_after_the_matchups_has_a_back(self, app, tmp_path, monkeypatch):
        self.failing_run(app, tmp_path, monkeypatch)
        # the matchup run was over, and the report's run is what failed
        app.run.args[:] = ["validate", "{}"]
        assert get_json(app, "/api/state")[1]["run"]["back"] is True
        assert post(app, "/api/back")[0] == 200
        assert app.view == "units_table"

    def test_there_is_no_back_unless_a_run_failed(self, app, tmp_path, monkeypatch):
        self.failing_run(app, tmp_path, monkeypatch)
        for status in ("finished", None):
            app.status = status
            assert get_json(app, "/api/state")[1]["run"]["back"] is False
        app.status = "failed"
        app.matchup_script_saved = True
        assert get_json(app, "/api/state")[1]["run"]["back"] is False
        app.matchup_script_saved = False
        app.action = "validate"
        assert get_json(app, "/api/state")[1]["run"]["back"] is False
        assert app.view == "finished"

    def settings_stand_in(self, tmp_path):
        """A stand-in for the script, which asks whether the matchups are
        right, as matchup does, and says what it was given along with yes."""
        script = tmp_path / "stand_in.py"
        script.write_text(
            textwrap.dedent(
                f"""
                import json
                from oceanval import prompts
                question = "Are you happy with these matchups? (y/n) "
                answer = prompts.ask(question, ("y", "n"), details={dict(MATCHUPS, matchups=[COBE2_MATCHUP])!r})
                print("answer:", answer)
                print("settings:", json.dumps(getattr(answer, "settings", {{}}), sort_keys=True))
                """
            )
        )
        return script

    def test_where_the_matchups_go_is_asked_once_they_are_confirmed(self, app, tmp_path, monkeypatch):
        """And then the report options, with the cores, all sent to matchup with
        yes; the script is written only where the output step says."""
        stand_in = self.settings_stand_in(tmp_path)
        start_run = app.start_run
        validated = []
        monkeypatch.setattr(
            app, "start_run", lambda args, label: validated.append(args) if args[0] == "validate"
            else start_run([args[0], str(stand_in)], label),
        )
        write_matchups(tmp_path / "results")
        self.to_recipes(app, tmp_path, action="matchup_validate")
        self.write(app)
        units_match(app)
        wait_for(lambda: app.question is not None)
        number = app.question.id

        assert post(app, "/api/answer", {"id": number, "answer": "y"})[0] == 200
        # the output step, with matchup still waiting for the answer
        assert (app.view, app.question.id) == ("output", number)
        # what the run makes, from the matchups it asks about
        assert app.output_made == ["temperature/cobe2"]
        # Back goes to the matchups, still asked about
        assert post(app, "/api/back", {"form": dict(OUTPUT_FORM, out="mine.py")})[0] == 200
        assert (app.view, app.question.id) == ("running", number)
        assert app.output_form["out"] == "mine.py"
        assert post(app, "/api/answer", {"id": number, "answer": "y"})[0] == 200

        # the matchups there already have to be said what is done with
        status, reply = post(app, "/api/output", {"form": dict(OUTPUT_FORM, out_dir="results", out="run.py")})
        assert (status, reply["errors"]) == (400, {"existing_confirmed": ""})
        output_continue(app, out_dir="results", out="run.py", existing_confirmed=True,
                        unused=["temperature/cobe2"])
        assert app.view == "report_options"
        results = str(tmp_path / "results")
        assert app.results_dir == results
        text = open(tmp_path / "run.py").read()
        assert f'    out_dir="{results}",\n' in text
        assert not (tmp_path / "matchup.py").exists()
        # Back goes to the output step
        assert post(app, "/api/back", {"form": {"pdf": True}})[0] == 200
        assert app.view == "output"
        output_continue(app, out_dir="results", out="run.py", existing_confirmed=True,
                        unused=["temperature/cobe2"])

        # the cores are asked for with the report options
        status, reply = post(app, "/api/report", {"form": {"pdf": True}})
        assert (status, reply["errors"]) == (400, {"cores": "Choose the number of cores."})
        assert post(app, "/api/report", {"form": {"pdf": True, "cores": "2"}})[0] == 200
        wait_for(lambda: validated)
        console = app.console.since(0, None)["text"]
        settings = json.loads(re.search(r"^settings: (\{.*\})$", console, re.M).group(1))
        # the matchup there already that this run makes is replaced
        assert {name: settings[name] for name in ("out_dir", "overwrite", "cores")} == {
            "out_dir": results, "overwrite": True, "cores": 2,
        }
        text = open(tmp_path / "run.py").read()
        assert "    cores=2," in text
        assert f'    data_dir="{results}",' in text and "    pdf=True," in text
        assert json.loads(validated[0][1])["data_dir"] == results

    def test_matchup_alone_asks_for_the_cores_with_where_the_matchups_go(self, app, tmp_path, monkeypatch):
        stand_in = self.settings_stand_in(tmp_path)
        start_run = app.start_run
        monkeypatch.setattr(app, "start_run", lambda args, label: start_run([args[0], str(stand_in)], label))
        self.to_recipes(app, tmp_path)
        self.write(app)
        units_match(app)
        wait_for(lambda: app.question is not None)
        assert post(app, "/api/answer", {"id": app.question.id, "answer": "y"})[0] == 200
        assert app.view == "output"

        status, reply = post(app, "/api/output", {"form": dict(OUTPUT_FORM, cores="")})
        assert (status, reply["errors"]) == (400, {"cores": "Choose the number of cores."})
        too_many = str((os.cpu_count() or 1) + 1)
        assert post(app, "/api/output", {"form": dict(OUTPUT_FORM, cores=too_many)})[0] == 400
        output_continue(app, cores="1")
        wait_for(lambda: app.view == "finished")
        console = app.console.since(0, None)["text"]
        assert '"cores": 1' in console and f'"out_dir": "{tmp_path}"' in console
        text = open(tmp_path / "matchup.py").read()
        assert "    cores=1," in text

    def test_the_report_options_give_matchup_its_subset_and_missing_values(self, app, tmp_path, monkeypatch):
        """Asked with the cores, they go to matchup with yes and into its call
        in the script: the subset is matchup's alone, not validate's too."""
        stand_in = self.settings_stand_in(tmp_path)
        start_run = app.start_run
        validated = []
        monkeypatch.setattr(
            app, "start_run", lambda args, label: validated.append(args) if args[0] == "validate"
            else start_run([args[0], str(stand_in)], label),
        )
        self.to_recipes(app, tmp_path, action="matchup_validate")
        self.write(app)
        units_match(app)
        wait_for(lambda: app.question is not None)
        assert post(app, "/api/answer", {"id": app.question.id, "answer": "y"})[0] == 200
        output_continue(app, out="run.py")
        assert app.view == "report_options"

        form = {"cores": "1", "lon_min": "-20", "lon_max": "10", "lat_min": "40", "lat_max": "65"}
        status, reply = post(app, "/api/report", {"form": dict(form, lon_max="", missing_to="0")})
        assert status == 400
        assert reply["errors"] == {
            "lon_max": "Fill in all four limits, or leave them all empty.",
            "missing_from": "Fill in the value, or the start of a range.",
        }
        assert post(app, "/api/report", {"form": dict(form, missing_from="0", missing_to="1e-10")})[0] == 200
        wait_for(lambda: validated)
        console = app.console.since(0, None)["text"]
        settings = json.loads(re.search(r"^settings: (\{.*\})$", console, re.M).group(1))
        assert {name: settings[name] for name in ("lon_lim", "lat_lim", "as_missing", "cores")} == {
            "lon_lim": [-20, 10], "lat_lim": [40, 65], "as_missing": [0, 1e-10], "cores": 1,
        }
        assert "lon_lim" not in settings["live_validation"]
        assert "lon_lim" not in json.loads(validated[0][1])
        text = open(tmp_path / "run.py").read()
        call = text.split("oceanval.matchup(")[1].split(")\n")[0]
        assert "    lon_lim=[-20, 10],\n    lat_lim=[40, 65],\n" in call
        assert "    as_missing=[0, 1e-10]," in call
        assert "lon_lim" not in text.split("oceanval.validate(")[1]

    def test_matchup_alone_asks_for_the_subset_and_missing_values_with_the_cores(self, app, tmp_path, monkeypatch):
        stand_in = self.settings_stand_in(tmp_path)
        start_run = app.start_run
        monkeypatch.setattr(app, "start_run", lambda args, label: start_run([args[0], str(stand_in)], label))
        self.to_recipes(app, tmp_path)
        self.write(app)
        units_match(app)
        wait_for(lambda: app.question is not None)
        assert post(app, "/api/answer", {"id": app.question.id, "answer": "y"})[0] == 200
        assert app.view == "output"

        status, reply = post(app, "/api/output", {"form": dict(OUTPUT_FORM, cores="1", lat_min="x", missing_from="nan?")})
        assert status == 400
        assert set(reply["errors"]) == {"lon_min", "lon_max", "lat_min", "lat_max", "missing_from"}
        output_continue(app, lon_min="-20", lon_max="10", lat_min="40", lat_max="65", missing_from="0")
        wait_for(lambda: app.view == "finished")
        console = app.console.since(0, None)["text"]
        settings = json.loads(re.search(r"^settings: (\{.*\})$", console, re.M).group(1))
        assert {name: settings[name] for name in ("lon_lim", "lat_lim", "as_missing")} == {
            "lon_lim": [-20, 10], "lat_lim": [40, 65], "as_missing": 0,
        }
        text = open(tmp_path / "matchup.py").read()
        assert "    lon_lim=[-20, 10],\n    lat_lim=[40, 65],\n" in text
        assert "    as_missing=0," in text

    def test_back_from_the_time_shift_question_keeps_the_choice(self, app, tmp_path, monkeypatch):
        """It is asked before the matchups, so nothing is matched up yet"""
        stand_in = time_shift_script(tmp_path)
        start_run = app.start_run
        monkeypatch.setattr(
            app, "start_run", lambda args, label: start_run([args[0], str(stand_in)], label)
        )
        self.to_recipes(app, tmp_path)
        self.write(app)
        units_match(app)
        wait_for(lambda: app.question is not None)
        first = app.run
        assert app.question.time_shift
        assert get_json(app, "/api/state")[1]["run"]["back"] is True

        assert post(app, "/api/back", {"form": {"mode": "keep"}})[0] == 200
        _, state = get_json(app, "/api/state")
        assert (state["view"], state["question"]) == ("units_table", None)
        wait_for(lambda: first.returncode is not None, timeout=30)
        assert "shift:" not in app.console.since(0, None)["text"]

        # carried on from again, it is asked again, as it was left
        units_match(app)
        wait_for(lambda: app.question is not None and app.run is not first)
        assert app.question.time_shift
        assert get_json(app, "/api/state")[1]["time_shift"]["form"] == {"mode": "keep"}
        post(app, "/api/answer", {"id": app.question.id, "answer": "n"})
        wait_for(lambda: app.question is not None and app.question.matchups)
        post(app, "/api/answer", {"id": app.question.id, "answer": "y"})
        output_continue(app)
        wait_for(lambda: app.view == "finished")
        assert "shift: n" in app.console.since(0, None)["text"]

    def test_other_file_filters_read_the_simulation_again(
        self, app, tmp_path, monkeypatch
    ):
        stand_in = self.matchups_stand_in(tmp_path)
        start_run = app.start_run
        monkeypatch.setattr(
            app, "start_run", lambda args, label: start_run([args[0], str(stand_in)], label)
        )
        own = {"name": "chl", "source": "mine", "model_variable": "thetao",
               "obs_path": "obs.nc", "obs_variable": "chl_obs"}
        first_page = self.to_recipes(app, tmp_path, own=own)
        self.write(app)
        conversions = {"own:0": {"multiplier": "", "adder": "3"}}
        assert post(app, "/api/refilter", {"exclude": "restart"})[0] == 409
        assert post(app, "/api/units_continue", {"conversions": conversions, "confirmed": True})[0] == 200
        wait_for(lambda: app.question is not None)
        first = app.run
        assert get_json(app, "/api/state")[1]["run"]["filters"] == {"exclude": "", "require": ""}

        assert post(app, "/api/refilter", {"exclude": " restart  5d", "require": ""})[0] == 200
        # the model variables can differ in the files that pass them, so
        # they are checked again in the recipes window
        wait_for(lambda: app.view == "recipes", timeout=60)
        wait_for(lambda: first.returncode is not None, timeout=30)
        page = app.recipes_page
        assert page is not first_page
        assert page.form["exclude"] == "restart 5d"
        assert app.setup_arguments["exclude"] == ["restart", "5d"]
        assert app.setup_form["exclude"] == "restart 5d"
        # the user's own data as given, without the conversion
        assert app.own_data["gridded"][0] == own

        # carried on from, the script is written with them
        self.write(app)
        assert post(app, "/api/units_continue", {"conversions": conversions, "confirmed": True})[0] == 200
        wait_for(lambda: app.question is not None and app.run is not first)
        script = open(page.context["out"]).read().replace(" ", "")
        assert "exclude=['restart','5d']" in script or 'exclude=["restart","5d"]' in script
        assert get_json(app, "/api/state")[1]["run"]["filters"] == {"exclude": "restart 5d", "require": ""}

    def test_back_from_the_report_options_keeps_them(self, app, tmp_path):
        app.action = "matchup_validate"
        app.start_run(["matchup", str(self.matchups_stand_in(tmp_path))], "python stand_in.py")
        wait_for(lambda: app.question is not None)
        number = app.question.id
        assert post(app, "/api/answer", {"id": number, "answer": "y"})[0] == 200
        output_continue(app)
        form = {"pdf": True, "subregions": "global", "lon_min": "-20", **DEPTH_FORM}

        # to where the matchups are saved, and then the matchups
        assert post(app, "/api/back", {"form": form})[0] == 200
        assert (app.view, app.question.id) == ("output", number)
        assert post(app, "/api/back")[0] == 200
        assert (app.view, app.question.id) == ("running", number)
        # this run was not started by the units step, so has none to go back to
        assert get_json(app, "/api/state")[1]["run"]["back"] is False
        assert post(app, "/api/answer", {"id": number, "answer": "y"})[0] == 200
        output_continue(app)
        kept = get_json(app, "/api/state")[1]["validate"]["form"]
        assert (kept["pdf"], kept["subregions"], kept["lon_min"], kept["word"]) == (
            True, "global", "-20", False,
        )
        assert kept["depth_bins"] == DEPTH_FORM["depth_bins"]

    def test_the_answer_about_own_data_is_kept(self, app, tmp_path, runs):
        write_simulation(tmp_path / "sim")
        app.choose("matchup")
        post(app, "/api/setup", {"form": SETUP_FORM})
        assert get_json(app, "/api/state")[1]["own"]["answer"] is None
        post(app, "/api/own_data", {"answer": True})
        post(app, "/api/back")
        assert get_json(app, "/api/state")[1]["own"]["answer"] is True
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "recipes")
        post(app, "/recipes/cancel")
        wait_for(lambda: app.view == "own_data")
        assert get_json(app, "/api/state")[1]["own"]["answer"] is False

        # another run starts afresh
        post(app, "/api/back")
        post(app, "/api/back")
        assert post(app, "/api/choose", {"action": "matchup"})[0] == 200
        assert app.own_answer is None

    def test_a_kept_conversion_is_only_shown_for_the_same_matchup(self, app):
        row = {
            "kind": "gridded", "key": "recipe:nitrate:woa23", "title": "nitrate (woa23)",
            "model": {"variable": "N3_n", "parts": [{"name": "N3_n", "units": "mmol N m-3"}]},
            "obs_variable": "n_an", "obs_units": "micromoles_per_kilogram",
            "obs_multiplier": 1, "obs_adder": 0, "check": {"status": "convert"},
        }
        app.view, app.units_rows = "units_table", [row]
        sent = {"recipe:nitrate:woa23": {"multiplier": "2", "adder": ""}}

        assert post(app, "/api/back", {"conversions": sent})[0] == 200
        assert get_json(app, "/api/state")[1]["units"]["boxes"] == sent
        # another model variable for it, in other units
        app.units_rows = [dict(row, model={"variable": "thetao", "parts": [{"name": "thetao", "units": "degC"}]})]
        assert get_json(app, "/api/state")[1]["units"]["boxes"] == {}

    def test_conversions_of_own_data_are_forgotten_as_it_changes(self, app):
        """Their keys are places in the lists, which change."""
        app.view = "point_data"
        app.own_data["point"].append({"name": "chl", "source": "mine", "model_variable": "thetao",
                                      "obs_path": "points"})
        kept = {"multiplier": "2", "adder": "", "row": {}}
        app.units_boxes = {"ownpoint:0": kept, "recipe:nitrate:woa23": kept}

        assert post(app, "/api/own_remove", {"kind": "point", "index": 0})[0] == 200
        assert list(app.units_boxes) == ["recipe:nitrate:woa23"]

    def test_the_fvcom_question_is_not_asked_again_for_the_same_files(
        self, app, tmp_path, runs
    ):
        write_fvcom(tmp_path / "fvcom")
        app.choose("matchup")
        post(app, "/api/setup", {"form": dict(SETUP_FORM, simdir="fvcom", start="2012")})
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.question is not None)
        post(app, "/api/answer", {"id": app.question.id, "answer": "y"})
        wait_for(lambda: app.view == "recipes")
        page = app.recipes_page

        post(app, "/recipes/cancel")
        wait_for(lambda: app.view == "own_data")
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.question is not None or (app.view == "recipes" and app.recipes_page is not page))
        assert app.question is None
        assert app.recipes_page.context["fvcom"] is True


# the variables of the demo's files, as CMIP6 names and describes them
DEMO_VARIABLES = {
    "tos": ("Sea Surface Temperature", "degC"),
    "sos": ("Sea Surface Salinity", "0.001"),
    "no3os": ("Surface Dissolved Nitrate Concentration", "mol m-3"),
}


def write_demo_file(path):
    """A small stand-in for one of the demo's model output files: a year of
    monthly values of the variable the file is named after, as CMIP6 names it."""
    variable = os.path.basename(path).split("_")[0]
    long_name, units = DEMO_VARIABLES[variable]
    dataset = xr.Dataset(
        {
            variable: (
                ("time", "j", "i"),
                np.random.rand(12, 2, 2).astype("f4"),
                {"long_name": long_name, "units": units},
            )
        }
    )
    dataset["time"] = (
        "time",
        np.arange(12) * 30.0 + 15,
        {"units": "days since 2010-01-01", "calendar": "noleap"},
    )
    dataset.to_netcdf(path)


@pytest.fixture
def fetched(monkeypatch):
    """The demo's downloads, which write a stand-in file rather than
    download one. Set fetched.error to make them fail."""

    class Fetched(list):
        error = None

    downloads = Fetched()

    def fetch(url, path, progress, cancelled):
        downloads.append(url)
        if downloads.error is not None:
            raise downloads.error
        write_demo_file(path)
        progress(10, 10)

    monkeypatch.setattr(app_module, "_fetch", fetch)
    return downloads


# a choice in the demo's grid: a model, and the variables ticked for it
NORESM = {"model": "NorESM2-LM", "variables": ["tos", "no3os", "sos"]}


def demo_files(model, variables):
    """The names and urls of the files the demo downloads for a choice."""
    files = app_module.DEMO_MODELS[model]["files"]
    return [files[variable]["name"] for variable in variables], [files[variable]["url"] for variable in variables]


def to_setup(app):
    """The demo, once downloaded, says what to do, and goes on from there."""
    wait_for(lambda: app.view == "demo_instructions")
    assert post(app, "/api/demo_continue")[0] == 200
    assert app.view == "setup"


def back_to_demo(app):
    """Back from the simulation step: the instructions, then the model and variables."""
    for _ in range(2):
        assert post(app, "/api/back")[0] == 200
    assert app.view == "demo"


class TestDemo:
    def test_the_models_are_from_three_servers(self):
        models = app_module.DEMO_MODELS
        assert list(models) == ["NorESM2-LM", "MPI-ESM1-2-LR", "UKESM1-0-LL"]
        assert len({model["server"] for model in models.values()}) == 3
        for label, model in models.items():
            assert list(model["files"]) == list(app_module.DEMO_VARIABLES) == ["tos", "no3os", "sos"]
            for variable, file in model["files"].items():
                assert file["name"].startswith(f"{variable}_Omon_{label}_historical_{model['member']}_gn_")
                assert file["url"].startswith(f"https://{model['server']}/")
                assert file["url"].endswith("/" + file["name"])

    def test_choosing_the_demo(self, app):
        assert post(app, "/api/choose", {"action": "demo"})[0] == 200
        _, state = get_json(app, "/api/state")

        assert (state["view"], state["action"]) == ("demo", "matchup_validate")
        demo = state["demo"]
        assert (demo["status"], demo["downloaded"], demo["year"]) == ("idle", False, 2010)
        # nothing is asked for until a model and its variables are ticked
        assert (demo["model"], demo["variables"], demo["files"]) == (None, [], [])
        assert [model["label"] for model in demo["models"]] == list(app_module.DEMO_MODELS)
        assert demo["prefilled"] == {"setup": [], "output": [], "report": []}
        assert demo["folder"] == os.path.join(app.cwd, "oceanval_demo")
        # nothing is downloaded until asked for
        assert not os.path.exists(demo["folder"])
        assert post(app, "/api/back")[0] == 200
        assert (app.view, app.demo) == ("start", None)

    def test_the_instructions_come_after_the_download(self, app, fetched):
        app.choose("demo")
        post(app, "/api/demo_download", NORESM)
        wait_for(lambda: app.view == "demo_instructions")
        # nothing is filled in until they have been read, and they can only be left once
        assert post(app, "/api/setup", {"form": app.setup_form})[0] == 409
        assert post(app, "/api/demo_continue")[0] == 200
        assert app.view == "setup"
        assert post(app, "/api/demo_continue")[0] == 409

    def test_the_matchups_of_a_model_tried_before_are_not_asked_about(self, app, fetched):
        # every model tried keeps its matchups in the demo's folder
        write_mixed_matchups(os.path.join(app.cwd, "oceanval_demo"))
        app.choose("demo")
        post(app, "/api/demo_download", NORESM)
        wait_for(lambda: app.view == "demo_instructions")
        assert post(app, "/api/demo_continue")[0] == 200
        found = get_json(app, "/api/probe", simdir="", ndown="", out="", out_dir=app.output_form["out_dir"])[1]
        assert (found["out_dir_has_matchups"], found["out_dir_is_demo"]) == (True, True)
        # this run's are made again, and no others are in the report
        with app._lock:
            arguments, errors = app._check_matchup_output(app.output_form)
        assert not errors and (arguments["overwrite"], arguments["previous"]) == (True, False)

    def test_the_report_is_concise_unless_detailed_is_chosen(self, app, fetched):
        app.choose("demo")
        assert app.demo["concise"] is True
        post(app, "/api/demo_download", NORESM)
        wait_for(lambda: app.view == "demo_instructions")
        assert get_json(app, "/api/state")[1]["demo"]["concise"] is True
        # only true or false, and only on the instructions
        assert post(app, "/api/demo_detail", {"concise": "no"})[0] == 409
        assert post(app, "/api/demo_detail", {})[0] == 409
        assert post(app, "/api/demo_detail", {"concise": False})[0] == 200
        assert get_json(app, "/api/state")[1]["demo"]["concise"] is False
        assert post(app, "/api/demo_continue")[0] == 200
        _, state = get_json(app, "/api/state")
        assert state["validate"]["form"]["concise"] is False
        assert state["validate"]["form"]["subregions"] == "global"
        # with PDF and Word versions too
        assert (state["validate"]["form"]["pdf"], state["validate"]["form"]["word"]) == (True, True)
        # filled in by OceanVal, so marked as its own
        assert state["demo"]["prefilled"]["report"] == app_module.DEMO_PREFILLED["report"]
        # and a transect down the middle of the Pacific, from south to north
        form = state["validate"]["form"]
        assert form["transect"] is True
        assert [form[name] for name in app_module.TRANSECT_BOXES] == ["180", "-70", "180", "65"]
        if oceanval.transects.available():
            assert app_module.check_transect_boxes(form, {}) == {"start": [180.0, -70.0], "end": [180.0, 65.0]}
        assert post(app, "/api/demo_detail", {"concise": True})[0] == 409

        # back to the instructions, the choice is still there, and can be changed
        assert post(app, "/api/back")[0] == 200
        assert get_json(app, "/api/state")[1]["demo"]["concise"] is False
        assert post(app, "/api/demo_detail", {"concise": True})[0] == 200
        assert post(app, "/api/demo_continue")[0] == 200
        assert app.validate_form["concise"] is True
        assert app.validate_form["subregions"] == ""
        assert (app.validate_form["pdf"], app.validate_form["word"]) == (False, False)
        assert app.validate_form["transect"] is False
        assert [app.validate_form[name] for name in app_module.TRANSECT_BOXES] == [""] * 4

    def test_a_report_detail_changed_by_hand_is_kept(self, app, fetched):
        app.choose("demo")
        post(app, "/api/demo_download", NORESM)
        wait_for(lambda: app.view == "demo_instructions")
        assert post(app, "/api/demo_continue")[0] == 200
        app.validate_form["concise"] = False
        assert "concise" not in get_json(app, "/api/state")[1]["demo"]["prefilled"]["report"]
        assert "pdf" in get_json(app, "/api/state")[1]["demo"]["prefilled"]["report"]
        assert post(app, "/api/back")[0] == 200
        assert post(app, "/api/demo_detail", {"concise": False})[0] == 200
        assert post(app, "/api/demo_continue")[0] == 200
        # the subregions follow the choice, and the box changed by hand is kept
        assert app.validate_form["subregions"] == "global"
        assert app.validate_form["concise"] is False
        assert (app.validate_form["pdf"], app.validate_form["word"]) == (True, True)
        app.validate_form["subregions"] = "nwes"
        assert post(app, "/api/back")[0] == 200
        assert post(app, "/api/demo_detail", {"concise": True})[0] == 200
        assert post(app, "/api/demo_continue")[0] == 200
        assert app.validate_form["subregions"] == "nwes"
        assert app.validate_form["concise"] is True
        assert (app.validate_form["pdf"], app.validate_form["word"]) == (False, False)

    def test_the_download_fills_in_the_simulation_step(self, app, fetched, tmp_path):
        app.choose("demo")
        assert post(app, "/api/demo_download", NORESM)[0] == 200
        to_setup(app)

        names, urls = demo_files(**NORESM)
        assert fetched == urls
        for name in names:
            assert (tmp_path / "oceanval_demo" / "simulation" / name).is_file()
        _, state = get_json(app, "/api/state")
        form = state["setup"]["form"]
        assert (form["simdir"], form["ndown"], form["domain"]) == (
            os.path.join("oceanval_demo", "simulation"), "0", "global")
        # only the first year in the file
        assert (form["start"], form["end"]) == ("2010", "2010")
        # where the output goes is asked once the matchups are confirmed, filled in too
        output = state["output"]["form"]
        assert (output["out_dir"], output["out"]) == (
            "oceanval_demo", os.path.join("oceanval_demo", "matchup.py"))
        # with the subset the models' grids are matched up in, which matchup alone asks there
        assert state["demo"]["prefilled"]["output"] == ["out_dir", "out", *app_module.DEMO_LIMITS]
        assert {name: state["validate"]["form"][name] for name in app_module.DEMO_LIMITS} == app_module.DEMO_LIMITS
        # concise and fast, with no subregions, unless detailed is chosen on the instructions
        assert state["validate"]["form"]["subregions"] == ""
        assert state["validate"]["form"]["concise"] is True
        # concise has nothing ticked and no subregions or transect, but they are OceanVal's choices
        assert state["demo"]["prefilled"]["report"] == app_module.DEMO_PREFILLED["report"]
        # only the chosen model's files are used: those with its name in
        assert form["require"] == "NorESM2-LM"
        assert state["demo"]["prefilled"]["setup"] == ["simdir", "ndown", "require"]
        # the filled-in boxes pass the simulation step's checks
        assert check_setup(form, app.cwd)[1] == {}
        # back to the instructions, and the demo's page, from which the file is not downloaded again
        assert post(app, "/api/back")[0] == 200
        assert app.view == "demo_instructions"
        assert post(app, "/api/back")[0] == 200
        assert app.view == "demo"
        # with what was ticked still there for the page to tick again
        assert get_json(app, "/api/state")[1]["demo"]["variables"] == ["tos", "no3os", "sos"]
        assert post(app, "/api/demo_download", NORESM)[0] == 200
        to_setup(app)
        assert len(fetched) == 3

    @pytest.mark.parametrize("body", [
        {},
        {"model": "NorESM2-LM"},
        {"model": "NorESM2-LM", "variables": []},
        {"model": "NorESM2-LM", "variables": ["thetao"]},
        {"model": "NorESM2-LM", "variables": "tos"},
        {"model": "GFDL-ESM4", "variables": ["tos"]},
    ])
    def test_a_choice_has_to_be_a_model_and_some_of_its_variables(self, app, fetched, body):
        app.choose("demo")
        assert post(app, "/api/demo_download", body)[0] == 400
        assert fetched == []
        assert app.demo["status"] == "idle"

    def test_only_the_ticked_variables_are_downloaded(self, app, fetched, tmp_path):
        app.choose("demo")
        # in any order, and once each
        assert post(app, "/api/demo_download", {"model": "MPI-ESM1-2-LR", "variables": ["sos", "tos", "sos"]})[0] == 200
        to_setup(app)

        names, urls = demo_files("MPI-ESM1-2-LR", ["tos", "sos"])
        assert fetched == urls
        folder = tmp_path / "oceanval_demo" / "simulation"
        assert sorted(path.name for path in folder.iterdir()) == sorted(names)
        state = get_json(app, "/api/state")[1]
        assert state["demo"]["files"] == names
        assert state["setup"]["form"]["require"] == "MPI-ESM1-2-LR"

    def test_another_model_uses_its_own_files(self, app, fetched, tmp_path):
        folder = tmp_path / "oceanval_demo" / "simulation"
        app.choose("demo")
        post(app, "/api/demo_download", NORESM)
        to_setup(app)
        back_to_demo(app)

        # the files of the model before stay, and are left out by the box
        post(app, "/api/demo_download", {"model": "UKESM1-0-LL", "variables": ["no3os"]})
        to_setup(app)
        assert app.setup_form["require"] == "UKESM1-0-LL"
        assert len(list(folder.iterdir())) == 4
        _, state = get_json(app, "/api/state")
        assert state["demo"]["prefilled"]["setup"] == ["simdir", "ndown", "require"]

        # a box that was changed by hand is not filled in again
        app.setup_form["require"] = "mine"
        back_to_demo(app)
        post(app, "/api/demo_download", {"model": "MPI-ESM1-2-LR", "variables": ["tos"]})
        to_setup(app)
        assert app.setup_form["require"] == "mine"
        assert "require" not in get_json(app, "/api/state")[1]["demo"]["prefilled"]["setup"]

    def test_variables_not_ticked_this_time_are_removed(self, app, fetched, tmp_path):
        folder = tmp_path / "oceanval_demo" / "simulation"
        app.choose("demo")
        post(app, "/api/demo_download", NORESM)
        to_setup(app)
        (folder / "mine.nc").write_text("not the demo's")
        back_to_demo(app)

        post(app, "/api/demo_download", {"model": "NorESM2-LM", "variables": ["sos"]})
        to_setup(app)
        # downloaded before, so not fetched again, and the others would be matched up too
        assert len(fetched) == 3
        assert sorted(path.name for path in folder.iterdir()) == sorted(
            [demo_files("NorESM2-LM", ["sos"])[0][0], "mine.nc"])

    def test_a_failed_download_can_be_tried_again(self, app, fetched, tmp_path):
        fetched.error = OSError("no network")
        app.choose("demo")
        post(app, "/api/demo_download", NORESM)
        wait_for(lambda: app.demo["status"] == "failed")
        _, state = get_json(app, "/api/state")

        assert state["view"] == "demo"
        assert "no network" in state["demo"]["error"]
        assert not (tmp_path / "oceanval_demo" / "simulation" / demo_files(**NORESM)[0][0]).exists()
        fetched.error = None
        assert post(app, "/api/demo_download", NORESM)[0] == 200
        to_setup(app)

    def test_a_retry_only_downloads_what_is_missing(self, app, fetched, tmp_path, monkeypatch):
        folder = tmp_path / "oceanval_demo" / "simulation"
        fetch, dropped = app_module._fetch, [True]

        def flaky(url, path, progress, cancelled):
            # the connection is lost during the third file
            if dropped and url == demo_files(**NORESM)[1][2]:
                raise OSError("dropped")
            fetch(url, path, progress, cancelled)

        monkeypatch.setattr(app_module, "_fetch", flaky)
        app.choose("demo")
        post(app, "/api/demo_download", NORESM)
        wait_for(lambda: app.demo["status"] == "failed")

        assert sorted(path.name for path in folder.iterdir()) == sorted(demo_files(**NORESM)[0][:2])
        assert app.demo["file"] == 3
        assert get_json(app, "/api/state")[1]["demo"]["downloaded"] is False

        dropped.clear()
        del fetched[:]
        assert post(app, "/api/demo_download", NORESM)[0] == 200
        to_setup(app)
        assert fetched == [demo_files(**NORESM)[1][2]]
        assert get_json(app, "/api/state")[1]["demo"]["downloaded"] is True

    def test_only_one_download_at_a_time(self, app, monkeypatch):
        release = threading.Event()
        monkeypatch.setattr(
            app_module, "_fetch", lambda url, path, progress, cancelled: release.wait(30)
        )
        app.choose("demo")
        assert post(app, "/api/demo_download", NORESM)[0] == 200
        assert post(app, "/api/demo_download", NORESM)[0] == 409
        # nor going back while it downloads
        assert post(app, "/api/back")[0] == 409
        release.set()

    def test_the_demo_is_only_offered_at_the_start(self, app):
        app.choose("validate")
        assert post(app, "/api/choose", {"action": "demo"})[0] == 409
        assert post(app, "/api/demo_download", NORESM)[0] == 409

    def test_what_was_changed_is_kept_and_not_marked_as_the_demos(self, app, fetched):
        app.choose("demo")
        post(app, "/api/demo_download", NORESM)
        to_setup(app)
        # the user changed a box, and went back
        with app._lock:
            app.setup_form = dict(app.setup_form, ndown="1")
            app.output_form = dict(app.output_form, out=os.path.join("oceanval_demo", "mine.py"))
        assert post(app, "/api/back")[0] == 200
        assert app.view == "demo_instructions"

        # the page only marks what still holds what the demo filled in
        prefilled = get_json(app, "/api/state")[1]["demo"]["prefilled"]
        assert (prefilled["setup"], prefilled["output"]) == (["simdir", "require"], ["out_dir", *app_module.DEMO_LIMITS])
        # and Continue on the demo's page, after Back, does not fill it in again
        post(app, "/api/back")
        assert app.view == "demo"
        assert post(app, "/api/demo_download", NORESM)[0] == 200
        to_setup(app)
        assert app.setup_form["ndown"] == "1"
        assert app.output_form["out"] == os.path.join("oceanval_demo", "mine.py")

    def test_starting_again_after_a_run_forgets_its_settings(self, app):
        with app._lock:
            app.view, app.action = "finished", "validate"
            app.setup_form["simdir"] = "/some/simulation"
            app.validate_form["subregions"] = "some regions"
            app.own_data["point"].append({"name": "mine"})
            app.matchup_progress = {"items": []}
            app.status, app.returncode = "finished", 0
        assert post(app, "/api/restart_all")[0] == 200
        assert (app.view, app.action) == ("home", None)
        assert app.setup_form["simdir"] == ""
        assert app.validate_form["subregions"] == ""
        assert app.own_data == {"point": [], "gridded": []}
        assert app.matchup_progress is None
        assert app.status is None

    def test_starting_again_forgets_the_demo(self, app, fetched):
        app.choose("demo")
        post(app, "/api/demo_download", NORESM)
        to_setup(app)
        with app._lock:
            app.view = "finished"

        assert post(app, "/api/restart_all")[0] == 200
        assert app.demo is None
        assert app.setup_form["simdir"] == ""
        assert app.output_form["out_dir"] == app.cwd
        assert app.validate_form["subregions"] == ""
        app.choose("matchup")
        assert get_json(app, "/api/state")[1]["demo"] is None

    def test_the_demo_picks_the_datasets_but_not_the_years(self, app, fetched, runs):
        app.choose("demo")
        post(app, "/api/demo_download", NORESM)
        to_setup(app)
        # only OceanVal's own datasets, so there is no step for the user's own
        assert post(app, "/api/setup", {"form": app.setup_form})[0] == 200
        wait_for(lambda: app.view == "recipes")
        page = app.recipes_page

        # the years are the user's to say
        assert [page.form[name] for name in ("point_start", "point_end", "clim_start", "clim_end")] == [""] * 4
        assert "prefilled" not in page.context["app"]
        assert "initial_years" not in page.context["app"]
        # tos is the sea surface temperature, validated against COBE-SST 2
        temperature = next(row for row in page.rows if row["variable"] == "temperature")
        assert temperature["model_variable"] == "tos"
        assert [dataset["recipe"] for dataset in temperature["gridded"] if dataset["ticked"]] == ["cobe2"]
        # sos and no3os are the surface salinity and nitrate, validated against WOA23
        for variable, model_variable in (("salinity", "sos"), ("nitrate", "no3os")):
            row = next(row for row in page.rows if row["variable"] == variable)
            assert row["model_variable"] == model_variable
            assert [dataset["recipe"] for dataset in row["gridded"] if dataset["ticked"]] == ["woa23"]
        status, body = get(app, "/recipes/")
        assert status == 200
        assert "prefilled" not in page_state(body)["context"]["app"]


def test_the_command_runs():
    root = os.path.dirname(os.path.dirname(os.path.abspath(oceanval.__file__)))
    result = subprocess.run(
        [sys.executable, "-m", "oceanval", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    assert "--port" in result.stdout


def test_the_files_skipped_start_empty(app, tmp_path):
    """Nothing is left out unasked: the window starts the files skipped
    empty, restart files included, and no default of OceanVal adds them."""
    import inspect

    from oceanval import analysis_forms, sim_compare_forms

    assert app_module.default_setup_form()["exclude"] == ""
    assert analysis_forms.default_setup_form(str(tmp_path))["exclude"] == ""
    assert sim_compare_forms.blank_sim()["exclude"] == ""
    assert "default_exclude" not in get_json(app, "/api/state")[1]
    assert not hasattr(oceanval.create_recipes, "DEFAULT_EXCLUDE")
    # nor do the Python functions start excluding anything
    assert not inspect.signature(oceanval.matchup).parameters["exclude"].default
    assert inspect.signature(oceanval.analyze).parameters["exclude"].default is None
    arguments, errors = check_setup(dict(SETUP_FORM, exclude="restart"), str(tmp_path))
    assert arguments["exclude"] == ["restart"]
    # removed, nothing is skipped
    assert check_setup(dict(SETUP_FORM, exclude=""), str(tmp_path))[0]["exclude"] is None


@pytest.mark.parametrize("tile", ["#home-viewer", "#home-analyze", "#home-validate"])
def test_the_files_skipped_start_empty_in_a_browser(browser, tmp_path, tile):
    """Validate, Analyze and View simulation output share the box: it starts
    empty, so nothing is skipped unless the user types it."""
    write_simulation(tmp_path / "sim", tracers=True)
    app = App(cwd=str(tmp_path))
    app.leftovers_asked = True
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click(tile)
        if tile == "#home-validate":
            page.click('button.choice[data-action="matchup_validate"]')
        amber = lambda: page.locator("#f-exclude").evaluate("node => node.classList.contains('is-detected')")
        page.wait_for_selector("#view-setup:not([hidden])")
        assert page.input_value("#f-exclude") == ""
        assert not amber()
        assert "Only file names are checked" not in page.inner_text("#m-simulation")
        page.fill("#f-exclude", "restart 5d")
        assert not amber()
        assert "skipped. Remove restart" not in page.inner_text("#m-simulation")
        assert "Only file names are checked" in page.inner_text("#m-simulation")
        page.fill("#f-exclude", "")
        assert not amber()
    finally:
        app.close()


def test_required_tags_show_only_while_empty(browser, tmp_path):
    """The red Required tag is there at the start and goes once the box is
    filled (typed or written by a script), and comes back when it is emptied."""
    write_simulation(tmp_path / "sim")
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click('button.choice[data-action="matchup"]')
        page.wait_for_selector("#view-setup:not([hidden])")
        tag = page.locator('label[for="f-ndown"] .own-req')
        simdir = page.locator('label[for="f-simdir"] .own-req')
        domain = page.locator("#setup-domain legend .own-req")
        assert tag.is_visible() and simdir.is_visible() and domain.is_visible()
        page.fill("#f-simdir", str(tmp_path / "sim"))
        page.wait_for_selector('label[for="f-simdir"] .own-req', state="hidden")
        assert tag.is_visible()
        # a value written by a script fires no event
        page.evaluate("document.getElementById('f-ndown').value = '0'")
        page.wait_for_selector('label[for="f-ndown"] .own-req', state="hidden")
        page.fill("#f-simdir", "")
        page.wait_for_selector('label[for="f-simdir"] .own-req', state="visible")
        # the tag stays in the page: only its display changes
        assert page.locator('label[for="f-ndown"]').text_content().strip().endswith("Required")
        # a group of choices is filled once one is chosen
        page.locator("#f-domain > *").first.click()
        page.wait_for_selector("#setup-domain legend .own-req", state="hidden")
    finally:
        app.close()


def test_the_window_in_a_browser(browser, tmp_path, monkeypatch):
    """The whole of matching up only, driven as a user would, with a stand-in
    for the script, which would otherwise download observations."""
    write_simulation(tmp_path / "sim")
    stand_in = tmp_path / "stand_in.py"
    stand_in.write_text(
        textwrap.dedent(
            f"""
            import time
            from oceanval import prompts
            # long enough for the page to say the files are being found
            time.sleep(2)
            question = "Are you happy with these matchups? (y/n) "
            print("answer:", prompts.ask(question, ("y", "n"), details={MATCHUPS!r}))
            print("again:", prompts.ask("Try again? (y/n) ", ("y", "n")))
            """
        )
    )
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    start_run = app.start_run
    monkeypatch.setattr(
        app,
        "start_run",
        lambda args, label: start_run(["script", str(stand_in)], label),
    )
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        assert "Choose what to do. Matchups, scripts and reports are written" not in page.text_content("#view-start")
        # the first page has no title, the cards speak for themselves, and there is no row of steps
        assert page.locator("#steps").count() == 0
        assert page.is_hidden("#title")
        page.click('button.choice[data-action="matchup"]')
        page.wait_for_selector("#view-setup:not([hidden])")
        # the setup page has no title: the boxes say what they ask
        assert page.is_hidden("#title")
        assert page.locator("#m-domain").count() == 0
        assert "OceanVal is running from" not in page.text_content("#meta")
        assert "Next, OceanVal reads the simulation, and shows you what it found." not in page.text_content("#bar")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.set_viewport_size({"width": 1280, "height": 720})
        assert page.locator(".ndown-row").evaluate(
            "node => getComputedStyle(node).display"
        ) == "grid"
        assert page.locator("#f-ndown").evaluate(
            "node => node.getBoundingClientRect().width"
        ) <= 100
        assert page.locator("#ndown-example").count() == 0
        ndown_label = page.locator('label[for="f-ndown"]')
        assert page.locator("#f-ndown").evaluate("node => node.required")
        assert ndown_label.evaluate("node => getComputedStyle(node).color") == "rgb(192, 57, 43)"
        assert ndown_label.evaluate("node => getComputedStyle(node).fontWeight") == "700"
        assert "Required" in ndown_label.text_content()
        assert page.is_hidden("#f-start")
        assert page.is_hidden("#f-end")
        assert "Years to validate" not in page.locator("#view-setup").text_content()
        simdir_label = page.locator('label[for="f-simdir"]')
        assert simdir_label.text_content().startswith("Which directory stores your simulation data?")
        assert simdir_label.locator(".own-req").text_content() == "Required"
        assert simdir_label.evaluate("node => getComputedStyle(node).color") == "rgb(192, 57, 43)"
        assert simdir_label.evaluate("node => getComputedStyle(node).fontWeight") == "700"
        # nothing is said in the bar but the link to how files are identified, beside Continue
        assert page.text_content("#summary").strip() == "How does OceanVal identify simulation files to matchup?"
        assert page.locator("#summary > *").count() == 1 and page.locator("#summary #how-files").count() == 1
        page.fill("#f-simdir", "sim")
        # the user must choose the directory depth; the years are inferred from the files there
        assert page.input_value("#f-ndown") == ""
        assert page.is_disabled("#continue")
        # the depth can be picked from a list of 0 to 5, opened by the arrow in the box, as well as typed
        ndown = page.locator("#f-ndown")
        edge = ndown.bounding_box()
        ndown.click(position={"x": edge["width"] - 8, "y": edge["height"] / 2})
        options = page.locator("body > .year-list .combo__option")
        assert options.all_inner_texts() == [
            "0in the directory itself", "1directory down", "2directories down",
            "3directories down", "4directories down", "5directories down",
        ]
        # nothing is chosen in an empty box
        assert page.locator("body > .year-list .is-active").count() == 0
        options.nth(2).click()
        assert page.input_value("#f-ndown") == "2"
        assert page.is_hidden("body > .year-list")
        page.wait_for_function("document.querySelector('#f-end').value === '2012'")
        assert page.input_value("#f-start") == "2011"
        # the years are read from the files' times, for the recipes window: none are shown here
        page.wait_for_function("document.querySelector('#ndown-found').textContent.includes('2 netCDF files found')")
        assert page.text_content("#ndown-found") == "2 netCDF files found"
        # there is no default domain: it has to be chosen too
        assert page.is_disabled("#continue")
        choose_domain(page)
        assert page.is_enabled("#continue")
        # where the output goes is asked last
        assert page.is_hidden("#f-out_dir")

        page.click("#continue")
        # the link to how files are identified is the setup page's only
        page.wait_for_function("document.querySelector('#summary #how-files') === null")
        assert page.text_content("#summary").strip() == ""
        # no data of our own
        page.click("#own-no")
        page.wait_for_url("**/recipes/**", timeout=90000)
        # the units and where the matchups are saved come next
        assert page.text_content("#page-title") == "Gridded data selection"
        assert page.text_content("#write") == "Continue"
        # the simulation is not shown at the top
        assert page.locator("#meta").count() == 0
        # chosen with the simulation, or once the matchups are checked, instead:
        # the file filters, the output, the report's options, the cores, the
        # missing values and the spatial subset
        assert page.is_hidden("#settings-more")
        assert page.is_hidden("#group-subset")
        # the app always asks
        assert page.is_hidden("#s-ask")
        fill_gridded_years(page)
        # nothing is Vertical, so no thickness is asked for
        assert page.is_hidden("#group-thickness")
        # a dataset through the water column cannot go ahead without a thickness
        nsbc = 'input[aria-label="NSBC, Northwest European Shelf, for Temperature"]'
        page.check(nsbc)
        page.check(
            'input[aria-label="Validate NSBC observations for Temperature through the full water column"]'
        )
        assert page.is_disabled("#write")
        assert page.is_visible("#page-gridded #group-thickness")
        assert "a thickness is needed" in page.text_content("#g-thickness")
        page.fill("#s-thickness", "z_level")
        assert page.is_enabled("#write")
        page.uncheck(nsbc)
        assert page.is_hidden("#group-thickness")
        # the point datasets' page, from which the units are next
        page.click("#write")
        assert page.text_content("#page-title") == "Point data selection"
        assert page.text_content("#cancel") == "Back"
        assert page.text_content("#write") == "Continue"
        if page.is_disabled("#write"):
            page.fill("#s-point_start", "2011")
            page.fill("#s-point_end", "2012")
        page.click("#write")
        # the units have to be confirmed, once they have been read
        page.wait_for_selector("#units-body-gridded tr[data-units-row]")
        page.check("#units-confirm")
        page.click("#units-continue")

        # until matchup asks, the page says it is finding the files
        page.wait_for_selector("#prepare:not([hidden])", timeout=90000)
        assert page.inner_text(".prepare__card") == "Identifying files that meet criteria. Please wait!"
        assert page.is_hidden("#title")
        # the output is not shown while the run goes well
        assert page.is_hidden("#run")
        # and then shows what it found as a table, in place of the question below the output
        page.wait_for_selector("#review:not([hidden])", timeout=90000)
        assert page.is_hidden("#prepare")
        assert page.is_hidden("#progress")
        assert page.is_hidden("#ask")
        assert page.locator("#review th").all_text_contents() == [
            "Variable", "Years", "Simulation variable", "Observation source", "File pattern to be used",
            "Time resolution", "Dates",
        ]
        # the answers are in the bar, Back first, and none are in the card
        assert page.locator("#review button:not(.btn--text):not(#review-refilter)").count() == 0
        assert [button.text_content() for button in page.locator("#actions button").all()] == [
            "Back", "Yes, continue",
        ]
        assert page.locator("text=No, stop the run").count() == 0
        # the mapping is introduced first, and the question is in the bar, just left of its buttons
        assert page.text_content("#review-title") == (
            "The following mapping will be assumed between variables and simulation files"
        )
        assert page.text_content("#review-question") == "Are you happy with these file selections?"
        table_bottom = page.locator("#review table").evaluate("node => node.getBoundingClientRect().bottom")
        question_box = page.locator("#review-question").bounding_box()
        assert question_box["y"] >= table_bottom
        assert page.locator("#bar #review-question").count() == 1
        yes_box = page.locator("#actions button:has-text('Yes, continue')").bounding_box()
        assert question_box["x"] + question_box["width"] <= yes_box["x"]
        assert abs(question_box["y"] - yes_box["y"]) < yes_box["height"]
        # each row has the files of its years, so there is no note that they are subset
        assert page.locator("#review-time-note").count() == 0
        row = page.locator('#review-body tr[data-variable="temperature"]')
        for text in ("Temperature", "thetao", "COBE2, ICES (point)", "x_**_grid_T.nc", "2 files"):
            assert text in row.text_content()

        # the file filters can be changed, to find the files again with
        assert page.is_visible("#review-filters")
        assert page.input_value("#review-exclude") == ""
        assert page.input_value("#review-require") == ""
        assert page.is_disabled("#review-refilter")
        assert page.text_content("#review-refilter") == "Use these filters"
        page.fill("#review-require", "nowhere")
        page.wait_for_function(
            "document.getElementById('review-filters-msg').textContent.startsWith('No files pass')"
        )
        assert page.is_disabled("#review-refilter")
        page.fill("#review-require", "2011")
        page.wait_for_function(
            "document.getElementById('review-filters-msg').textContent.includes('pass these filters')"
        )
        assert re.match(r"\d+ of the \d+ files found pass these filters\.$", page.text_content("#review-filters-msg"))
        assert page.is_enabled("#review-refilter")
        # as they were, there is nothing to find again
        page.fill("#review-require", "")
        assert page.is_disabled("#review-refilter")
        assert page.text_content("#review-filters-msg") == ""

        # until the dates of the files are read, every file of the pattern is
        # listed, saying so
        row.locator("button:has-text('List files')").click()
        page.wait_for_selector("#files-list .picker__file")
        assert page.text_content("#files-title") == "Temperature, 2011–2012: files"
        assert page.is_visible("#files-notice")
        assert page.text_content("#files-notice") == (
            "Their dates have not been read, so all of them are listed: "
            "only their times in 2011–2012 are matched up."
        )
        assert page.locator("#files-list .picker__file").all_text_contents() == [
            "2011/x_2011_grid_T.nc",
            "2012/x_2012_grid_T.nc",
        ]
        page.keyboard.press("Escape")
        assert page.is_hidden("#files")
        page.click("#actions button:has-text('Yes')")

        # then where the matchups are saved: where oceanval was started, unless changed,
        # and, with no report options to ask it with, the number of cores, which has no default
        page.wait_for_selector("#view-output:not([hidden])")
        assert page.text_content("#title") == "Where do you want the matchups to be saved?"
        assert page.input_value("#f-out_dir") == str(tmp_path)
        assert page.text_content("#f-out_dir-label") == "Where do you want matchups to be saved?"
        assert page.input_value("#f-out") == "matchup.py"
        assert page.is_hidden("#row-existing")
        assert page.is_visible("#f-cores")
        assert page.input_value("#f-cores") == ""
        assert page.locator("#f-cores option").first.text_content() == "Choose the number of cores"
        # matchup alone is asked its missing values and spatial subset here too, after the cores
        assert page.is_visible("#f-missing_from") and page.is_visible("#f-lon_min")
        cores_y = page.locator("#f-cores").bounding_box()["y"]
        assert cores_y < page.locator("#f-missing_from").bounding_box()["y"] < page.locator("#f-lon_min").bounding_box()["y"]
        assert "Empty: the whole domain." in page.text_content("#m-subset")
        assert [button.text_content() for button in page.locator("#actions button").all()] == ["Back", "Match up"]
        assert page.is_disabled("#output-run")
        page.select_option("#f-cores", "1")
        page.wait_for_function("!document.getElementById('output-run').disabled")
        page.click("#output-run")

        # any other question is asked under the output
        page.wait_for_selector("#ask:not([hidden])", timeout=60000)
        assert page.is_hidden("#review")
        # the buttons say what "(y/n)" would
        assert page.text_content("#ask-question") == "Try again?"
        page.click("#ask-answer button:has-text('Yes')")
        page.wait_for_selector("#result:not([hidden])", timeout=60000)

        assert "The matchups are ready" in page.text_content("#result")
        assert "answer: y" in page.text_content("#console-text")
    finally:
        app.close()


def test_the_files_are_found_in_a_box_with_no_title(browser, tmp_path):
    """While the files are found a matchup and validate run is a box in the
    middle of the page, and is titled for the matchups once they are asked
    about, not "Matching up and validating"."""
    go = tmp_path / "go"
    script = tmp_path / "matchup.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os, time
            from oceanval import prompts
            while not os.path.exists({str(go)!r}):
                time.sleep(0.1)
            prompts.ask("Are you happy with these matchups? (y/n) ", ("y", "n"), details={MATCHUPS!r})
            """
        )
    )
    app = App(cwd=str(tmp_path))
    app.action = "matchup_validate"
    app.start_run(["script", str(script)], f"python {script}")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#prepare:not([hidden])", timeout=60000)
        # the box in the middle of the page says it, with no title, text or code
        assert page.inner_text(".prepare__card") == "Identifying files that meet criteria. Please wait!"
        assert page.is_hidden("#title")
        assert page.is_hidden("#progress")
        assert page.evaluate(
            "getComputedStyle(document.querySelector('.prepare__spinner'), '::after').animationName"
        ) == "spin"
        # no output while the files are found, nor while they are checked
        assert page.is_hidden("#run")
        go.write_text("")
        page.wait_for_selector("#review:not([hidden])", timeout=60000)
        assert page.text_content("#title") == ""
        assert page.is_hidden("#run")
    finally:
        app.close()


def test_a_server_that_is_down_is_said_to_be_at_the_bottom_of_the_page(browser, tmp_path):
    go = tmp_path / "go"
    script = tmp_path / "matchup.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os, time
            from oceanval import prompts
            prompts.tell({{"kind": "matchups", "items": [{{"key": "gridded|nitrate|WOA23|", "title": "Nitrate (WOA23)"}}]}})
            prompts.tell({{"kind": "matchup", "key": "gridded|nitrate|WOA23|", "state": "matching"}})
            prompts.tell({{"kind": "server_trouble", "key": "gridded|nitrate|WOA23|", "title": "nitrate (WOA23)",
                           "host": "www.ncei.noaa.gov", "server": "NOAA", "status": 503,
                           "reason": "Service Unavailable"}})
            prompts.tell({{"kind": "matchup", "key": "gridded|nitrate|WOA23|", "state": "not_made"}})
            while not os.path.exists({str(go)!r}):
                time.sleep(0.05)
            prompts.tell({{"kind": "server_trouble", "key": "gridded|salinity|WOA23|", "title": "salinity (WOA23)",
                           "host": "www.ncei.noaa.gov", "server": "NOAA", "status": 503,
                           "reason": "Service Unavailable"}})
            while True:
                time.sleep(0.05)
            """
        )
    )
    app = App(cwd=str(tmp_path))
    app.action = "matchup"
    app.start_run(["script", str(script)], "python run.py")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#server-trouble:not([hidden])", timeout=60000)
        text = page.inner_text("#server-trouble")
        assert "NOAA's data server is not available right now" in text
        assert "www.ncei.noaa.gov answered “503 Service Unavailable”" in text
        assert "not with your simulation" in text
        assert "Could not match up: Nitrate (WOA23)." in text
        # at the bottom of the window, in the bar above its buttons
        assert page.locator("#bar #server-trouble").count() == 1
        bar = page.locator("#bar").bounding_box()
        assert abs(bar["y"] + bar["height"] - page.viewport_size["height"]) < 2
        # hidden with its ×, until there is more to say
        page.click(".server-trouble__close")
        page.wait_for_selector("#server-trouble", state="hidden")
        go.write_text("")
        page.wait_for_selector("#server-trouble:not([hidden])", timeout=60000)
        assert "Nitrate (WOA23), salinity (WOA23)" in page.inner_text("#server-trouble")
    finally:
        app.close()


def test_a_saved_matchup_script_opens_run_instructions(browser, tmp_path):
    script = tmp_path / "matchup.py"
    script.write_text(
        textwrap.dedent(
            f"""
            from oceanval import prompts
            question = "Are you happy with these matchups? (y/n) "
            prompts.ask(question, ("y", "n"), details={MATCHUPS!r})
            print("matching up")
            """
        )
    )
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    app.action = "matchup_validate"
    app.start_run(["script", str(script)], f"python {script}")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#review:not([hidden])")
        assert page.text_content("#title") == ""
        # the window does not offer to store the script, but an answer of "save" still does
        assert page.locator("#actions button:has-text('store')").count() == 0
        question = get_json(app, "/api/state")[1]["question"]
        assert post(app, "/api/answer", {"id": question["id"], "answer": "save"})[0] == 200
        page.wait_for_selector("#view-script-saved:not([hidden])")

        assert page.text_content("#title") == "Your matchup script is saved"
        assert "Nothing has been matched up" in page.text_content("#view-script-saved")
        assert page.text_content("#script-saved-command") == f"python {script}"
        assert page.text_content("#script-saved-path") == str(script)
        assert "matching up" not in page.text_content("#console-text")
        page.click('#actions button:has-text("Start again")')
        page.wait_for_selector("#view-home:not([hidden])")
    finally:
        app.close()


def test_the_point_time_res_step_in_a_browser(browser, tmp_path):
    """The files table shows each variable's time resolution, and yes to it
    asks about the point datasets matched by day, before matchup carries on."""
    details = dict(CHECKED_MATCHUPS, rows=[dict(MATCHUPS["rows"][0], time_res="monthly")])
    app = App(cwd=str(tmp_path))
    url = app.start()
    try:
        app.action = "matchup"
        app.start_run(["script", str(point_time_res_script(tmp_path, details))], "python run.py")
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#review:not([hidden])")
        assert page.locator("#review th").nth(5).text_content() == "Time resolution"
        assert page.locator("#review-body tr[data-variable] td").nth(5).text_content() == "monthly"

        page.click("text=Yes, continue")
        page.wait_for_selector("#view-point-time-res:not([hidden])")
        # OceanVal's suggestion for all of them, in bold
        assert page.is_checked("#ptr-mode-all")
        assert page.input_value("#ptr-all") == "year,month"
        assert "is-oceanval" in page.get_attribute("#ptr-all", "class")
        assert page.is_disabled('#ptr-body select[data-key="temperature/ICES"]')

        page.check("#ptr-mode-each")
        select = '#ptr-body select[data-key="temperature/ICES"]'
        assert page.is_enabled(select)
        page.select_option(select, "month")
        page.click("#ptr-continue")
        through_output(page)
        wait_for(lambda: app.view == "finished")
        assert (
            '"point_time_res": {"datasets": {"temperature/ICES": ["month"]}, "default": null}'
            in app.console.since(0, None)["text"]
        )
    finally:
        app.close()


def test_the_time_shift_question_in_a_browser(browser, tmp_path):
    """Whether to shift the model's times is asked before the matchups are
    shown, with its own answers."""
    app = App(cwd=str(tmp_path))
    url = app.start()
    try:
        app.action = "matchup"
        app.start_run(["script", str(time_shift_script(tmp_path))], "python run.py")
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#time-shift:not([hidden])")

        assert page.is_hidden("#review") and page.is_hidden("#ask")
        assert page.text_content("#title") == "Are the model's times at the end of each mean?"
        # OceanVal's suggestion, in bold
        assert page.is_checked("#ts-mode-shift")
        assert page.text_content("#ts-shift-label") == "Shift the simulation's times back one day"
        label = page.locator("#ts-shift-label")
        assert label.evaluate("node => getComputedStyle(node).fontWeight") == "700"
        assert label.evaluate("node => getComputedStyle(node).color") == page.locator(
            "#time-shift .lede .oceanval-change").evaluate("node => getComputedStyle(node).color")
        assert page.is_visible("#ts-bounds")
        cells = page.locator('#ts-body tr[data-key="temperature"] td')
        assert cells.nth(1).text_content() == "*/north_sea_3dm_*.nc"
        assert cells.nth(2).text_content() == "2000-02-01 00:00 to 2001-01-01 00:00"
        assert cells.nth(3).text_content() == "2000-01-31 00:00 to 2000-12-31 00:00"
        assert "is the only one in 2001" in cells.nth(4).text_content()

        page.click("#ts-continue")
        page.wait_for_selector("#review:not([hidden])")
        assert page.is_hidden("#time-shift")
        page.click("text=Yes, continue")
        through_output(page)
        wait_for(lambda: app.view == "finished")
        assert "shift: y" in app.console.since(0, None)["text"]
    finally:
        app.close()


def test_the_report_options_step_stores_the_script_in_a_browser(browser, tmp_path):
    """A matchup and validate run can be stopped before the matchups are
    made, keeping the script, and the page says how to run it."""
    script = tmp_path / "matchup.py"
    script.write_text(
        textwrap.dedent(
            f"""
            from oceanval import prompts
            question = "Are you happy with these matchups? (y/n) "
            print("answer:", prompts.ask(question, ("y", "n"), details={MATCHUPS!r}))
            print("matching up")
            """
        )
    )
    app = App(cwd=str(tmp_path))
    app.action = "matchup_validate"
    app.start_run(["matchup", str(script)], f"python {script}")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#review:not([hidden])")
        page.click("#actions button:has-text('Yes')")
        through_output(page)
        page.wait_for_selector("#view-validate:not([hidden])")
        pick_cores(page)

        assert page.text_content("#save-script") == "Stop and store Python script for later"
        page.click("#save-script")
        page.wait_for_selector("#view-script-saved:not([hidden])")
        assert page.text_content("#title") == "Your matchup script is saved"
        assert page.text_content("#script-saved-command") == f"python {script}"
        assert page.text_content("#script-saved-path") == str(script)
        assert "matching up" not in page.text_content("#console-text")
    finally:
        app.close()


def test_the_report_options_in_a_browser(browser, tmp_path, monkeypatch):
    """Once the matchups are checked, a matchup and validate run asks for the
    report's options, before anything is matched up."""
    script = tmp_path / "matchup.py"
    script.write_text(
        textwrap.dedent(
            f"""
            from oceanval import prompts
            question = "Are you happy with these matchups? (y/n) "
            print("answer:", prompts.ask(question, ("y", "n"), details={MATCHUPS!r}))
            print("matching up")
            """
        )
    )
    app = App(cwd=str(tmp_path))
    app.action = "matchup_validate"
    validated = []
    start_run = app.start_run
    monkeypatch.setattr(
        app,
        "start_run",
        lambda args, label: validated.append(args) if args[0] == "validate" else start_run(args, label),
    )
    app.start_run(["matchup", str(script)], f"python {script}")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#review:not([hidden])")
        page.click("#actions button:has-text('Yes')")
        through_output(page)
        page.wait_for_selector("#view-validate:not([hidden])")

        assert page.text_content("#title") == ""
        assert page.locator("#title").evaluate("node => getComputedStyle(node).whiteSpace") == "nowrap"
        # the matchups and the report go where the output step said
        assert page.is_hidden("#v-group-data_dir")
        assert page.is_hidden("#v-group-out_dir")
        assert page.text_content("#v-report-dir") == (
            f"Matchups will be stored here: {tmp_path}/oceanval_matchups"
            f"An interim validation report will be viewable here: {tmp_path}/oceanval_interim_report"
            "/oceanval_report/_build/html/notebooks/summary.html"
            f"The final validation report will be viewable here: {tmp_path}/oceanval_report.html"
        )
        assert page.evaluate("document.activeElement.id") == "v-lon_min"
        assert page.text_content("#build") == "Match up and validate"
        # matchup's cores, which have no default, are asked for here
        assert page.is_visible("#v-cores") and page.input_value("#v-cores") == ""
        assert page.is_disabled("#build")
        pick_cores(page)
        assert page.is_enabled("#build")
        assert "matching up" not in page.text_content("#console-text")

        # Back shows where the matchups go, and then the matchups again, to be answered afresh
        page.click("#actions button:has-text('Back')")
        page.wait_for_selector("#view-output:not([hidden])")
        page.click("#actions button:has-text('Back')")
        page.wait_for_selector("#review:not([hidden])")
        assert page.is_enabled("#actions button:has-text('Yes')")
        page.click("#actions button:has-text('Yes')")
        through_output(page)
        page.wait_for_selector("#view-validate:not([hidden])")
        pick_cores(page)

        page.check("#v-pdf")
        page.select_option("#v-concise", "false")
        page.click("#build")
        page.wait_for_function(
            "document.querySelector('#console-text').textContent.includes('matching up')"
        )
        wait_for(lambda: validated)
        assert json.loads(validated[0][1]) == {
            "data_dir": str(tmp_path),
            "out_dir": str(tmp_path),
            "pdf": True,
            "concise": False,
        }
    finally:
        app.close()


def test_the_dates_of_the_files_in_a_browser(browser, tmp_path, monkeypatch):
    """While the matchups are asked about, the dates of the files are read.
    The table has a row for each variable and each of the years its
    datasets are matched up for, with only the files with output in those
    years, and whether any dates are missing from them, with the months and
    what is missing in a row that opens under it, all without anything being
    fetched from the web. matchup is given the times read."""
    monkeypatch.setattr(model_check, "ModelCheck", RealModelCheck)
    simulation = os.path.abspath("data/example_temperature")
    pattern = "**/**/amm7_1d_**_**_grid_T.nc"
    # March 2002 is left out, so there is a month for the check to find missing
    every_file = sorted(
        os.path.relpath(path, simulation)
        for path in glob.glob(os.path.join(simulation, "*", "*", "*.nc"))
    )
    files = [path for path in every_file if "200203" not in os.path.basename(path)]
    details = {
        "kind": "matchups", "sim_dir": simulation, "years": [2001, 2003],
        "fvcom": False,
        # one to validate, for the report options
        "matchups": [{"kind": "point", "variable": "temperature", "source": "ICES", "layer": "surface",
                      "vertical": False, "title": "Temperature", "key": "temperature/ICES"}],
        "rows": [
            # ICES matched up for one of the run's years
            {"variable": "temperature", "title": "Temperature", "model_variable": "votemper",
             "pattern": pattern, "observations": ["NSBC", "ICES (point)"], "files": len(files),
             "time_res": "1d", "groups": [
                 {"years": [2001, 2003], "sources": ["NSBC"], "gridded": True},
                 {"years": [2002, 2002], "sources": ["ICES (point)"], "gridded": False},
             ]},
            # nothing left out of this one's files
            {"variable": "temperature_all", "title": "Temperature, all files", "model_variable": "votemper",
             "pattern": "all_files", "observations": ["ICES (point)"], "files": len(every_file),
             "time_res": "1d", "groups": [
                 {"years": [2001, 2003], "sources": ["ICES (point)"], "gridded": False},
             ]},
        ],
        "files": {pattern: files, "all_files": every_file},
    }
    script = tmp_path / "matchup.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json
            from oceanval import prompts
            question = "Are you happy with these matchups? (y/n) "
            answer = prompts.ask(question, ("y", "n"), details={details!r})
            # the interim report's options, which every answer has, are not what is looked at
            settings = {{k: v for k, v in (getattr(answer, "settings", None) or {{}}).items() if k != "live_validation"}}
            print("settings:", json.dumps(settings))
            """
        )
    )
    app = App(cwd=str(tmp_path))
    app.action = "matchup_validate"
    validated = []
    start_run = app.start_run
    monkeypatch.setattr(
        app, "start_run",
        lambda args, label: validated.append(args) if args[0] == "validate" else start_run(args, label),
    )
    app.start_run(["matchup", str(script)], f"python {script}")
    url = app.start()
    fetched = []

    def year(path):
        return int(os.path.basename(path).split("_")[2][:4])

    in_run = [path for path in files if 2001 <= year(path) <= 2003]
    in_2002 = [path for path in files if year(path) == 2002]
    assert 0 < len(in_2002) < len(in_run) < len(files)
    try:
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        # nothing but the window itself is reached
        page.route(
            "**/*",
            lambda route: route.continue_()
            if route.request.url.startswith("http://127.0.0.1")
            else (fetched.append(route.request.url), route.abort()),
        )
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.wait_for_selector("#review:not([hidden])")
        assert page.locator("#review thead th").all_text_contents() == [
            "Variable", "Years", "Simulation variable", "Observation source", "File pattern to be used",
            "Time resolution", "Dates",
        ]
        # the table says which files are used, so there is no note of it
        assert page.locator("#review-time-note").count() == 0
        assert "Temporal subsetting" not in page.text_content("#review")

        # a row for each of the years the variable's datasets are matched up for
        rows = page.locator("#review-body tr[data-variable]")
        assert rows.count() == 3
        nsbc = page.locator("#review-body tr[data-variable=temperature][data-group='0']")
        ices = page.locator("#review-body tr[data-variable=temperature][data-group='1']")
        other = page.locator("#review-body tr[data-variable=temperature_all]")
        for row in (nsbc, ices, other):
            row.locator(".review__verdict").wait_for(timeout=180000)
        page.wait_for_selector("#review-dates-progress[hidden]", state="attached")

        def cells(row):
            return [cell.strip() for cell in row.locator("td").all_text_contents()]

        assert cells(nsbc)[:4] == ["Temperature", "2001–2003", "votemper", "NSBC"]
        assert cells(ices)[:4] == ["Temperature", "2002", "votemper", "ICES (point)"]
        # only the files with output in its years, and their dates
        assert nsbc.locator(".review__found").text_content() == (
            f"{len(in_run)} filesList files"
            # every day of the three years, but March 2002
            f"2001-01-01 to 2003-12-31, {365 * 3 - 31:,} time steps"
        )
        assert ices.locator(".review__found").text_content() == (
            f"{len(in_2002)} filesList files2002-01-01 to 2002-12-31, {365 - 31} time steps"
        )
        for row in (nsbc, ices):
            verdict = row.locator(".review__verdict")
            assert verdict.text_content().strip() == "Some dates appear to be missing"
            assert "is-gaps" in verdict.get_attribute("class")

        # the months of each, under its row
        for index, row in enumerate((nsbc, ices)):
            fold = page.locator(f"#review-fold-{index}")
            assert fold.is_hidden()
            toggle = row.locator(".review__toggle")
            assert (toggle.text_content(), toggle.get_attribute("aria-expanded")) == ("Show the months ▾", "false")
            toggle.click()
            assert fold.is_visible()
            assert (toggle.text_content(), toggle.get_attribute("aria-expanded")) == ("Hide the months ▴", "true")
            assert row.bounding_box()["y"] < fold.bounding_box()["y"]
            assert fold.locator(".review__grid .is-missing").count() == 1
            assert fold.locator(".review__grid .is-missing").get_attribute("title") == "Mar 2002: no output"
            assert fold.locator(".review__legend li").count() == 3
            assert "1 month with no output: Mar 2002." in fold.locator(".review__issues").text_content()
        nsbc_fold, ices_fold = page.locator("#review-fold-0"), page.locator("#review-fold-1")
        assert nsbc_fold.locator(".review__year").count() == 3
        assert ices_fold.locator(".review__year").count() == 1
        # only the gridded dataset leaves out a year without all 12 months
        assert "Gridded matchups leave out 2002" in nsbc_fold.locator(".review__issues").text_content()
        assert "Gridded" not in ices_fold.locator(".review__issues").text_content()
        # and they are kept open as the page is drawn again
        time.sleep(1.5)
        assert nsbc_fold.is_visible() and ices_fold.is_visible()

        # when nothing is missing, that is all that is said
        assert cells(other)[1:4] == ["2001–2003", "votemper", "ICES (point)"]
        said = other.locator(".review__verdict")
        assert said.text_content().strip() == "All 36 months from 2001 to 2003 are complete"
        assert "is-ok" in said.get_attribute("class")
        assert other.locator(".review__toggle").count() == 0
        assert page.locator("#review-fold-2").is_hidden()
        assert other.locator(".review__found").text_content() == (
            f"36 filesList files2001-01-01 to 2003-12-31, {365 * 3:,} time steps"
        )

        # a row's files are those with output in its years
        ices.locator("button:has-text('List files')").click()
        page.wait_for_selector("#files:not([hidden]) .picker__file")
        assert page.text_content("#files-title") == "Temperature, 2002: files"
        assert page.locator("#files-list .picker__file").all_text_contents() == in_2002
        assert page.text_content("#files-intro") == (
            f"{len(in_2002)} of the {len(files)} files in {simulation} that match {pattern} have output in 2002."
        )
        assert page.is_hidden("#files-notice")
        page.click("#files-close")
        nsbc.locator("button:has-text('List files')").click()
        page.wait_for_function(f"document.querySelectorAll('#files-list .picker__file').length === {len(in_run)}")
        assert page.text_content("#files-title") == "Temperature, 2001–2003: files"
        page.click("#files-close")

        # the page does not scroll sideways on a phone
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.set_viewport_size({"width": 1400, "height": 1000})

        # Yes goes on, through where the matchups go, to the report options, and Back shows the dates again
        number = app.model_check.number
        page.click("#actions button:has-text('Yes')")
        through_output(page)
        page.wait_for_selector("#view-validate:not([hidden])")
        pick_cores(page)
        page.click("#actions button:has-text('Back')")
        page.wait_for_selector("#view-output:not([hidden])")
        page.click("#actions button:has-text('Back')")
        page.wait_for_selector("#review:not([hidden])")
        assert app.model_check.number == number
        assert rows.count() == 3
        assert nsbc.locator(".review__verdict").text_content().strip() == "Some dates appear to be missing"

        page.click("#actions button:has-text('Yes')")
        through_output(page)
        page.wait_for_selector("#view-validate:not([hidden])")
        pick_cores(page)
        index = app.model_check.index
        page.click("#build")
        wait_for(lambda: validated)
        # what the check read of the dates is given to matchup
        assert f'"times_index": "{index}"' in app.console.since(0, None)["text"]
        assert errors == []
        assert fetched == []
    finally:
        app.close()


def report_options_app(tmp_path, monkeypatch, gridded=None, vertical=False):
    """An app held at the report options step of a matchup and validate run,
    with the validate runs it starts recorded. gridded is what the recipes
    window chose, if it is to be known, and vertical whether a point recipe
    was chosen with Vertical, as is known then too."""
    script = tmp_path / "matchup.py"
    script.write_text(
        textwrap.dedent(
            f"""
            from oceanval import prompts
            question = "Are you happy with these matchups? (y/n) "
            print("answer:", prompts.ask(question, ("y", "n"), details={MATCHUPS!r}))
            print("matching up")
            """
        )
    )
    app = App(cwd=str(tmp_path))
    app.action = "matchup_validate"
    if gridded is not None:
        # the recipes window's choices: a gridded one, or none
        selection = [("temperature", "cobe2")] if gridded else []
        point_options = {}
        if vertical:
            selection.append(("temperature", "ices"))
            point_options[("temperature", "ices")] = {"vertical": True}
        app._script_writer = (
            lambda *choices, **options: None,
            ({"temperature": "thetao"}, selection, {}, point_options, {}),
        )
    validated = []
    start_run = app.start_run
    monkeypatch.setattr(
        app,
        "start_run",
        lambda args, label: validated.append(args) if args[0] == "validate" else start_run(args, label),
    )
    app.start_run(["matchup", str(script)], f"python {script}")
    return app, validated


def go_to_report_options(page, url):
    page.goto(url)
    page.wait_for_selector("#review:not([hidden])")
    page.click("#actions button:has-text('Yes')")
    through_output(page)
    page.wait_for_selector("#view-validate:not([hidden])")
    pick_cores(page)


def test_the_matchups_to_validate_in_a_browser(browser, tmp_path, monkeypatch):
    """Building a report from matchups made before: the matchups directory is
    looked at as soon as it is there, and the options are only shown once
    there are matchups in it, and only those for the matchups ticked, in a
    list that opens."""
    write_matchups(tmp_path / "gridded")
    write_mixed_matchups(tmp_path / "mixed")
    (tmp_path / "empty").mkdir()
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    validated = []
    monkeypatch.setattr(app, "start_run", lambda args, label: validated.append(args))
    app.choose("validate", data_dir=str(tmp_path / "empty"))
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#view-validate:not([hidden])")
        page.wait_for_function("document.querySelector('#vm-data_dir').textContent.includes('No matchups found')")
        # nothing to choose until there are matchups
        assert page.is_hidden("#v-options")
        assert page.is_disabled("#build")

        page.fill("#v-data_dir", str(tmp_path / "gridded"))
        page.wait_for_selector("#v-options:not([hidden])")
        assert page.text_content("#vm-data_dir") == "Found 1 gridded matchup."
        assert page.is_enabled("#build")
        # gridded only: no depth bins, and no point list
        assert page.is_hidden("#v-group-depth_bins")
        for shown in ("#v-group-subregions", "#v-group-transect", "#row-fixed_scale"):
            assert page.is_visible(shown)
        assert page.text_content("#v-datasets-count") == "All 1"
        # closed, until it is opened
        assert page.is_hidden("#v-datasets-gridded-list")

        page.fill("#v-data_dir", str(tmp_path / "mixed"))
        page.wait_for_function("document.querySelector('#vm-data_dir').textContent.includes('point')")
        assert page.text_content("#vm-data_dir") == "Found 2 gridded matchups and 2 point matchups."
        assert page.is_visible("#v-group-depth_bins")
        assert page.text_content("#v-datasets-count") == "All 4"
        page.click("#v-datasets summary")
        ticks = page.locator("#v-datasets input[data-key]")
        assert ticks.count() == 4
        assert all(ticks.nth(i).is_checked() for i in range(4))
        assert page.locator("#v-datasets-point-list .tick-label").first.text_content() == (
            "Nitrate (glodap, through the water column)"
        )

        # with no gridded matchup ticked, their options go
        page.uncheck("#v-datasets input[data-key='temperature/COBE2']")
        page.uncheck("#v-datasets input[data-key='temperature/WOA23']")
        assert page.text_content("#v-datasets-count") == "2 of 4"
        for hidden in ("#v-group-subregions", "#v-group-transect", "#row-fixed_scale"):
            assert page.is_hidden(hidden)
        # and with none ticked, there is nothing to build
        page.click("#v-untick-all")
        assert page.text_content("#v-datasets-count") == "0 of 4"
        assert page.text_content("#vm-variables") == "Tick at least one dataset to validate."
        assert page.is_disabled("#build")
        assert page.get_attribute("#build", "title") == "Tick at least one dataset to validate."
        page.click("#v-tick-all")
        assert page.text_content("#v-datasets-count") == "All 4"
        assert page.is_visible("#v-group-transect")

        page.uncheck("#v-datasets input[data-key='nitrate/ICES']")
        page.click("#build")
        wait_for(lambda: validated)
        arguments = json.loads(validated[0][1])
        assert arguments["data_dir"] == str(tmp_path / "mixed")
        assert arguments["variables"] == ["temperature/COBE2", "temperature/WOA23", "nitrate/glodap"]
    finally:
        app.close()


def test_the_matchups_to_be_made_in_a_browser(browser, tmp_path, monkeypatch):
    """A matchup and validate run lists the matchups it is to make, to choose
    those the report validates."""
    listed = [
        {"kind": "gridded", "variable": "temperature", "source": "COBE2", "layer": None,
         "vertical": False, "title": "Temperature", "key": "temperature/COBE2"},
        {"kind": "point", "variable": "temperature", "source": "ICES", "layer": "all",
         "vertical": True, "title": "Temperature", "key": "temperature/ICES"},
    ]
    script = tmp_path / "matchup.py"
    script.write_text(
        textwrap.dedent(
            f"""
            from oceanval import prompts
            question = "Are you happy with these matchups? (y/n) "
            print("answer:", prompts.ask(question, ("y", "n"), details={dict(MATCHUPS, matchups=listed)!r}))
            """
        )
    )
    app = App(cwd=str(tmp_path))
    app.action = "matchup_validate"
    app.start_run(["matchup", str(script)], f"python {script}")
    url = app.start()
    try:
        page = browser.new_page()
        go_to_report_options(page, url)
        assert page.is_visible("#v-options")
        assert page.text_content("#v-datasets-count") == "All 2"
        assert page.is_visible("#v-group-depth_bins")
        assert page.is_visible("#v-group-transect")
        page.click("#v-datasets summary")
        page.uncheck("#v-datasets input[data-key='temperature/ICES']")
        assert page.is_hidden("#v-group-depth_bins")
        assert page.is_visible("#v-group-transect")
        assert page.text_content("#build") == "Match up and validate"
        assert page.is_enabled("#build")
    finally:
        app.close()


@needs_to_transect
def test_a_transect_in_a_browser(browser, tmp_path, monkeypatch):
    """The report options ask whether to validate along a transect, which must
    run north-south or east-west, and the report cannot be asked for until it
    does."""
    red_bold = ["rgb(192, 57, 43)", "700"]
    app, validated = report_options_app(tmp_path, monkeypatch, gridded=True)
    url = app.start()
    try:
        page = browser.new_page()
        posts = []
        page.on("request", lambda request: posts.append(request.url) if request.method == "POST" else None)
        go_to_report_options(page, url)

        # no point matchups through the water column, so no depth bins
        assert page.is_hidden("#v-group-depth_bins")
        # asked in a group of its own, before the other options, and not ticked
        assert page.is_visible("#v-group-transect")
        assert page.text_content("#v-group-transect legend") == "Transect"
        assert page.locator("#v-transect").evaluate("node => node.closest('label').textContent") == (
            "Do you want to validate against gridded datasets along a transect?"
        )
        assert not page.is_checked("#v-transect")
        assert page.is_hidden("#row-transect")
        assert page.is_enabled("#build")

        # ticked, it says what a transect has to be, and asks for both ends
        page.check("#v-transect")
        assert page.is_visible("#row-transect")
        rule = page.text_content(".transect-rule")
        assert "north–south" in rule and "east–west" in rule
        assert "same longitude" in rule and "same latitude" in rule
        assert page.locator("#row-transect .span-row:not(.transect-heads) .span-row__label").all_text_contents() == [
            "Start", "End",
        ]
        # and says which box is which, as the boxes' hints go once they are filled in
        assert page.locator(".transect-head").all_text_contents() == ["Longitude (°E)", "Latitude (°N)"]
        # empty, so the report cannot be asked for yet
        assert page.is_disabled("#build")
        assert page.get_attribute("#build", "title") == "Fill in both ends of the transect, or untick it"

        # a diagonal line is refused, in bold, and every box is marked
        for name, value in [("start_lon", "-30"), ("start_lat", "0"), ("end_lon", "-20"), ("end_lat", "65")]:
            page.fill(f"#v-transect_{name}", value)
        problem = page.locator("#vm-transect .group__line")
        assert problem.text_content() == (
            "The transect must run north–south or east–west: give both ends the same "
            "longitude, or the same latitude."
        )
        assert problem.evaluate("node => [getComputedStyle(node).color, getComputedStyle(node).fontWeight]") == red_bold
        for name in ("start_lon", "start_lat", "end_lon", "end_lat"):
            assert "is-invalid" in page.get_attribute(f"#v-transect_{name}", "class")
        assert page.is_disabled("#build")
        assert page.get_attribute("#build", "title") == "Fix what is marked in red first"
        # Enter in a box does not get past the disabled button
        page.press("#v-transect_end_lat", "Enter")
        page.wait_for_timeout(300)
        assert not [url for url in posts if url.split("?")[0].endswith("api/report")]
        assert app.report_arguments is None

        # north-south: the same longitude at both ends
        page.fill("#v-transect_end_lon", "-30")
        assert page.text_content("#vm-transect") == "North–south along 30°W, from 0° to 65°N."
        assert "is-ok" in page.get_attribute("#vm-transect .group__line", "class")
        assert page.locator("#v-transect_end_lon").evaluate("node => node.classList.contains('is-invalid')") is False
        assert page.is_enabled("#build")
        assert page.get_attribute("#build", "title") in ("", None)

        # east-west: the same latitude at both ends
        for name, value in [("start_lon", "-10"), ("start_lat", "55"), ("end_lon", "8"), ("end_lat", "55")]:
            page.fill(f"#v-transect_{name}", value)
        assert page.text_content("#vm-transect") == "East–west along 55°N, from 10°W to 8°E."
        assert page.is_enabled("#build")

        # the other things that cannot be used
        page.fill("#v-transect_end_lat", "95")
        assert "Latitude must be between -90 and 90." in page.text_content("#vm-transect")
        assert page.is_disabled("#build")
        page.fill("#v-transect_end_lat", "north")
        assert "Coordinates must be numbers." in page.text_content("#vm-transect")
        page.fill("#v-transect_end_lat", "55")
        page.fill("#v-transect_end_lon", "-10")
        assert "The two ends are the same point." in page.text_content("#vm-transect")
        assert page.is_disabled("#build")
        page.fill("#v-transect_end_lon", "")
        assert "Fill in the longitude and latitude of both ends." in page.text_content("#vm-transect")
        assert page.is_disabled("#build")

        # unticked, whatever is in its boxes no longer matters
        page.uncheck("#v-transect")
        assert page.is_hidden("#row-transect")
        assert page.text_content("#vm-transect") == ""
        assert page.is_enabled("#build")

        # a transect that can be used is sent with the rest of the options
        page.check("#v-transect")
        page.fill("#v-transect_end_lon", "8")
        assert page.is_enabled("#build")
        page.click("#build")
        wait_for(lambda: validated)
        assert json.loads(validated[0][1]) == {
            "data_dir": str(tmp_path),
            "out_dir": str(tmp_path),
            "transect": {"start": [-10, 55], "end": [8, 55]},
        }
    finally:
        app.close()


def test_depth_bins_in_a_browser(browser, tmp_path, monkeypatch):
    """With a point recipe through the water column, the report options end
    with the depth bins: OceanVal's own to start with, each removed with its
    x, more added with +, and the report not asked for while they cannot be
    used."""
    red_bold = ["rgb(192, 57, 43)", "700"]
    app, validated = report_options_app(tmp_path, monkeypatch, gridded=False, vertical=True)
    url = app.start()
    try:
        page = browser.new_page()
        go_to_report_options(page, url)
        group = page.locator("#v-group-depth_bins")
        rows = page.locator("#depth-bins-rows .depth-bin")
        box = lambda part, row: page.locator(f"[aria-label='{part} depth of bin {row}, in metres']")
        values = lambda: [
            (box("From", row).input_value(), box("To", row).input_value())
            for row in range(1, rows.count() + 1)
        ]
        focused = lambda: page.evaluate("document.activeElement.getAttribute('aria-label')")

        # no gridded matchups, so no transect
        assert page.is_hidden("#v-group-transect")
        # below how detailed the report is, with OceanVal's bins
        assert group.is_visible()
        assert group.bounding_box()["y"] > page.locator("#v-concise").bounding_box()["y"]
        assert page.text_content("#v-group-depth_bins legend") == (
            "Which depth bins do you want for vertical validation?"
        )
        # the x column's heading is for screen readers only
        assert page.locator(".depth-bins__head [role=columnheader]").all_text_contents() == [
            "Bin", "From (metres)", "To (metres)", "Remove",
        ]
        assert values() == [
            ("0", "10"), ("10", "30"), ("30", "60"), ("60", "100"), ("100", "150"),
            ("150", "300"), ("300", "600"), ("600", "1000"), ("1000", ""),
        ]
        assert box("To", 9).get_attribute("placeholder") == "and deeper"
        assert page.text_content("#vm-depth_bins") == ""
        assert page.is_disabled("#depth-reset")
        assert page.is_enabled("#build")

        # x removes a bin, leaving a gap, which is said, and moves on to the next
        page.click("[aria-label='Remove bin 8']")
        page.wait_for_function("document.querySelectorAll('#depth-bins-rows .depth-bin').length === 8")
        assert values()[-2:] == [("300", "600"), ("1000", "")]
        assert page.text_content("#vm-depth_bins") == "Observations between 600 and 1000 m are left out."
        assert focused() == "Remove bin 8"
        assert page.is_enabled("#build")

        # back to OceanVal's own
        page.click("#depth-reset")
        assert rows.count() == 9
        assert page.is_disabled("#depth-reset")

        # + starts a bin where the deepest ends, ready for its To
        box("To", 9).fill("2000")
        assert page.text_content("#vm-depth_bins") == "Observations deeper than 2000 m are left out."
        page.click("#depth-add")
        assert rows.count() == 10
        assert box("From", 10).input_value() == "2000"
        assert focused() == "To depth of bin 10, in metres"
        assert values()[-2:] == [("1000", "2000"), ("2000", "")]
        assert page.text_content("#vm-depth_bins") == ""

        # or empty, if a bin starts there already, which is not used until filled in
        page.click("#depth-add")
        assert box("From", 11).input_value() == ""
        assert focused() == "From depth of bin 11, in metres"
        assert page.is_enabled("#build")
        page.keyboard.type("3000")
        problem = page.locator("#vm-depth_bins .group__line")
        assert problem.text_content() == "Only the deepest bin can leave To empty, for everything below it."
        assert problem.evaluate("node => [getComputedStyle(node).color, getComputedStyle(node).fontWeight]") == red_bold
        assert "is-invalid" in box("To", 10).get_attribute("class")
        assert page.is_disabled("#build")
        assert page.get_attribute("#build", "title") == "Fix what is marked in red first"
        page.click("[aria-label='Remove bin 11']")
        page.wait_for_function("document.querySelectorAll('#depth-bins-rows .depth-bin').length === 10")
        assert page.is_enabled("#build")

        # bins that overlap
        box("From", 2).fill("5")
        assert page.text_content("#vm-depth_bins") == "The bins 0-10m and 5-30m overlap."
        assert "is-invalid" in box("To", 1).get_attribute("class")
        assert "is-invalid" in box("From", 2).get_attribute("class")
        assert page.is_disabled("#build")
        box("From", 2).fill("10")
        assert page.is_enabled("#build")

        # Back keeps them
        page.click("#actions button:has-text('Back')")
        page.wait_for_selector("#view-output:not([hidden])")
        page.click("#actions button:has-text('Back')")
        page.wait_for_selector("#review:not([hidden])")
        page.click("#actions button:has-text('Yes')")
        through_output(page)
        page.wait_for_selector("#view-validate:not([hidden])")
        pick_cores(page)
        assert values()[-3:] == [("600", "1000"), ("1000", "2000"), ("2000", "")]

        # sent with the rest of the options
        page.click("#build")
        wait_for(lambda: validated)
        assert json.loads(validated[0][1])["depth_bins"] == [
            [0, 10], [10, 30], [30, 60], [60, 100], [100, 150], [150, 300],
            [300, 600], [600, 1000], [1000, 2000], [2000, None],
        ]
    finally:
        app.close()


def test_the_demo_in_a_browser(browser, tmp_path, fetched):
    """The demo is offered below the four, says in large bold type that it
    makes oceanval_demo, says what it is not, and fills in the simulation
    step in bold."""
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    app.leftovers_asked = True
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#view-start:not([hidden])")
        card = page.locator(".choice.is-demo")
        assert card.locator(".choice__title").text_content() == "Try a demo"
        # below the four, across both columns
        below = page.locator(".choice[data-action=compare]").bounding_box()
        box = card.bounding_box()
        assert box["y"] > below["y"] + below["height"]
        assert box["width"] > 1.5 * below["width"]
        card.click()
        page.wait_for_selector("#view-demo:not([hidden])")

        assert page.text_content("#title") == "Try OceanVal with a demo"
        warning = page.locator("#demo-folder")
        assert "oceanval_demo" in warning.text_content()
        assert "Remove it when you have finished" in warning.text_content()
        assert str(tmp_path / "oceanval_demo") in warning.text_content()
        assert warning.evaluate("node => getComputedStyle(node).fontWeight") == "700"
        assert float(warning.evaluate("node => getComputedStyle(node).fontSize")[:-2]) >= 20
        caveat = page.text_content(".demo-caveat")
        assert "not how to validate a climate model" in caveat
        assert "2010" in caveat
        assert "NorESM2-LM" in page.text_content("#demo-grid")

        page.locator('input[aria-label="Sea surface temperature from NorESM2-LM"]').check()
        page.click("#demo-start")
        # told what to do, then the steps
        page.wait_for_selector("#view-demo-instructions:not([hidden])")
        assert page.text_content("#title") == "Instructions"
        text = page.text_content("#view-demo-instructions")
        assert "Just press Continue on each page, and tick agreement when asked, to run the validation." in text
        assert "Feel free to customize the validation by tweaking the options." in text
        assert "You should start seeing validation results in a couple of minutes." in text
        assert "How do you want your validation sample?" in text
        # each sentence in a box of its own, and then the question
        boxes = page.locator("#view-demo-instructions .demo-about")
        assert boxes.count() == 4
        assert [boxes.nth(i).locator("p").count() for i in range(4)] == [1, 1, 1, 1]
        options = page.locator("#demo-detail label")
        assert options.all_text_contents() == ["Concise and fast", "Detailed, but slower"]
        assert page.is_checked('input[name="demo-detail"][value="true"]')
        # the wait follows the choice
        page.check('input[name="demo-detail"][value="false"]')
        page.wait_for_function("document.getElementById('demo-wait').textContent.includes('4 or 5 minutes')")
        assert page.text_content("#demo-wait") == "You should start seeing validation results in 4 or 5 minutes."
        page.check('input[name="demo-detail"][value="true"]')
        page.wait_for_function("document.getElementById('demo-wait').textContent.includes('a couple of minutes')")
        # the boxes together are in the middle of the space between the masthead and the bar, in type 1.5 times the demo's
        box = page.locator("#view-demo-instructions .demo").bounding_box()
        width = page.evaluate("innerWidth")
        assert abs((box["x"] + box["width"] / 2) - width / 2) <= 20
        masthead = page.locator(".masthead").bounding_box()
        bar = page.locator("#bar").bounding_box()
        free_top, free_bottom = masthead["y"] + masthead["height"], bar["y"]
        assert abs((box["y"] + box["height"] / 2) - (free_top + free_bottom) / 2) <= 40
        size = page.locator("#view-demo-instructions p").first.evaluate("node => parseFloat(getComputedStyle(node).fontSize)")
        assert size == 14.5 * 1.5
        page.click("#demo-continue")
        page.wait_for_selector("#view-setup:not([hidden])")
        red = "rgb(192, 57, 43)"
        simdir = page.locator("#f-simdir")
        assert simdir.input_value() == os.path.join("oceanval_demo", "simulation")
        assert simdir.evaluate("node => getComputedStyle(node).color") == red
        assert simdir.evaluate("node => getComputedStyle(node).fontWeight") == "700"
        assert page.locator("#f-ndown").evaluate("node => node.classList.contains('is-oceanval')")
        # the user's own once typed over
        simdir.fill("elsewhere")
        assert not simdir.evaluate("node => node.classList.contains('is-oceanval')")
        assert page.locator("#meta .is-flag").text_content() == "Demooceanval_demo"
    finally:
        app.close()


def test_comparing_validations_in_a_browser(browser, tmp_path, monkeypatch):
    """The first page offers to compare validations made before: five rows
    of a name and a validation directory, said what they must hold, and
    compare runs with them."""
    validations(tmp_path, "control", "mixing")
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    app.leftovers_asked = True
    compared = []
    monkeypatch.setattr(app, "start_run", lambda args, label: compared.append((args, label)))
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#view-start:not([hidden])")
        assert page.locator("#view-start .choice:not(.is-demo):not(.is-register)").count() == 4
        card = page.locator(".choice[data-action=compare]")
        assert card.locator(".choice__title").text_content() == "Compare existing validations"
        card.click()
        page.wait_for_selector("#view-compare:not([hidden])")

        assert page.text_content("#title") == "Which validations do you want to compare?"
        assert page.evaluate("document.activeElement.id") == "c-name_1"
        # what a validation directory holds
        info = page.text_content(".compare-info")
        for part in ("validate()", "oceanval_report", "oceanval_results", "annual_mean", "temporals", "regionals",
                     "at least two of the simulations"):
            assert part in info
        # five rows of two columns, with a folder browser for each directory
        assert page.locator(".compare-rows thead th").all_text_contents()[1:] == [
            "Simulation name", "Validation directory",
        ]
        assert page.locator(".compare-rows tbody tr").count() == 5
        assert page.locator(".compare-rows tbody [data-browse]").count() == 5
        assert page.get_attribute("#c-out_dir", "placeholder") == str(tmp_path)

        # two simulations are needed
        assert page.is_disabled("#compare")
        assert page.get_attribute("#compare", "title") == "Fill in at least two simulations"
        page.fill("#c-name_1", "control")
        page.fill("#c-dir_1", "control")
        page.fill("#c-name_2", "mixing")
        page.fill("#c-dir_2", "nowhere")
        assert page.is_enabled("#compare")

        # what cannot be used is said in bold, beside the row
        page.click("#compare")
        page.wait_for_selector("#cm-rows.is-error")
        problem = page.locator("#cm-rows .group__line.is-error")
        assert problem.text_content() == "Simulation 2: There is no directory at this path."
        assert problem.evaluate("node => [getComputedStyle(node).color, getComputedStyle(node).fontWeight]") == [
            "rgb(192, 57, 43)", "700",
        ]
        assert "is-invalid" in page.get_attribute("#c-dir_2", "class")
        assert compared == []

        page.fill("#c-dir_2", "mixing")
        assert "is-invalid" not in page.get_attribute("#c-dir_2", "class")
        page.click("#compare")
        wait_for(lambda: compared)
        [(args, label)] = compared
        assert args[0] == "compare"
        assert json.loads(args[1]) == {
            "model_dict": {"control": str(tmp_path / "control"), "mixing": str(tmp_path / "mixing")},
            "out_dir": str(tmp_path),
        }
    finally:
        app.close()


def test_the_output_is_only_shown_when_a_run_fails(browser, tmp_path):
    app = App(cwd=str(tmp_path))
    with app._lock:
        app.action, app.view, app.status = "matchup_validate", "running", "running"
    app.console.write("matching up\n")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#progress:not([hidden])")
        # not while the matchups are made
        assert page.is_hidden("#run")
        with app._lock:
            app.view, app.status, app.returncode = "finished", "failed", 1
            app._notify()
        # then it is, with its errors, and the panel no longer says what it is doing
        page.wait_for_selector("#run:not([hidden])")
        assert page.is_hidden("#progress")
        assert "matching up" in page.text_content("#console-text")
        # Quit and Start again are always there; Back only when the choices to go back to are kept
        page.wait_for_selector("#bar button:has-text('Start again')")
        assert page.locator("#bar button:has-text('Back')").count() == 0
    finally:
        app.close()


def test_the_interim_report_in_a_browser(browser, tmp_path):
    """While matchup builds the interim report, the page says so, then links
    to it once its first page is made; and once the full report is built,
    the page links to that too."""
    app = App(cwd=str(tmp_path))
    folder = tmp_path / "oceanval_interim_report"
    pages = folder / "oceanval_report" / "_build" / "html" / "notebooks"
    pages.mkdir(parents=True)
    (pages / "summary.html").write_text("<html><body><p>Summary so far</p></body></html>")
    status = folder / "status.json"
    interim = {"status_path": str(status), "status": None}
    with app._lock:
        app.action, app.view, app.status = "matchup_validate", "running", "running"
        app.results_dir = str(tmp_path)
        app.interim = interim
    threading.Thread(target=app._follow_interim, args=(interim,), daemon=True).start()
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#progress:not([hidden])")
        assert page.text_content("#progress-title") == "Matching the model to the observations"
        assert page.is_hidden("#interim")
        # the output is not shown while the run goes well
        assert page.is_hidden("#run")

        status.write_text(
            json.dumps(
                {"state": "building", "pages": 1, "expected": 3, "landing": str(pages / "summary.html")}
            )
        )
        page.wait_for_selector("#interim a")
        assert page.text_content("#interim h2") == "Interim validation report"
        assert page.text_content("#interim p").startswith("1 of 3 matchups is in it so far.")
        assert page.text_content("#progress-title") == "Matching up: 1 of 3 matchups made"
        assert page.get_attribute("#progress-bar", "aria-valuenow") == "1"
        link = page.locator("#interim a")
        assert link.text_content() == "Open the interim validation report"
        assert link.get_attribute("target") == "_blank"
        # it opens from the window, in a tab of its own
        with page.expect_popup() as opened:
            link.click()
        report = opened.value
        report.wait_for_load_state()
        assert "Summary so far" in report.text_content("body")
        report.close()

        # the full report is built, once every matchup is in the interim one
        full = tmp_path / "oceanval_report" / "_build" / "html" / "notebooks"
        full.mkdir(parents=True)
        (full / "004_summary.html").write_text("<html><body><p>The full report</p></body></html>")
        os.symlink(
            os.path.relpath(full / "004_summary.html", tmp_path), tmp_path / "oceanval_report.html"
        )
        status.write_text(
            json.dumps(
                {"state": "complete", "pages": 3, "expected": 3, "landing": str(pages / "summary.html")}
            )
        )
        wait_for(lambda: (app.interim["status"] or {}).get("state") == "complete")
        with app._lock:
            app.view, app.status, app.returncode = "finished", "finished", 0
            app._notify()
        page.wait_for_selector("#result:not([hidden]) a")
        assert page.text_content("#result h2") == "The validation report is ready"
        assert page.text_content("#result a") == "Open the validation report"
        assert page.text_content("#interim p") == (
            "All 3 matchups are in it. The full validation report above is built "
            "from the same matchups."
        )
        with page.expect_popup() as opened:
            page.click("#result a")
        report = opened.value
        report.wait_for_load_state()
        assert "The full report" in report.text_content("body")
    finally:
        app.close()


def test_the_matchups_grid_in_a_browser(browser, tmp_path):
    """While matchup and validate runs, a grid says which matchup is being
    made, and which have a page in the interim report: a tick when done,
    nothing before it starts, and a turning circle while it is going."""
    temperature, nitrate, chlorophyll = (
        "gridded|temperature|nsbc|", "point|nitrate|ices|surface", "gridded|chlorophyll|occci|",
    )
    items = [
        {"key": temperature, "variable": "temperature", "title": "Temperature", "source": "NSBC",
         "report": True},
        {"key": nitrate, "variable": "nitrate", "title": "Nitrate",
         "source": "ICES point data (surface)", "report": True},
        # left out of the report by its variables
        {"key": chlorophyll, "variable": "chlorophyll", "title": "Chlorophyll", "source": "OCCCI",
         "report": False},
    ]
    script = tmp_path / "matchup.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os, time
            from oceanval import prompts

            def wait(name):
                while not os.path.exists(os.path.join({str(tmp_path)!r}, name)):
                    time.sleep(0.05)

            def files(key, total, done, finished=False):
                prompts.tell({{"kind": "extraction", "key": key, "total": total, "done": done, "finished": finished}})

            def observations(key, state):
                prompts.tell({{"kind": "observations", "key": key, "state": state}})

            prompts.tell({{"kind": "matchups", "items": {items!r}}})
            wait("go1")
            prompts.tell({{"kind": "matchup", "key": {temperature!r}, "state": "matching"}})
            files({temperature!r}, 12, None)
            print("matching up temperature")
            wait("count")
            files({temperature!r}, 12, 5)
            wait("go2")
            # a gridded matchup reads its observations after its model files
            files({temperature!r}, 12, 12, True)
            observations({temperature!r}, "reading")
            wait("observe")
            observations({temperature!r}, "read")
            prompts.tell({{"kind": "matchup", "key": {temperature!r}, "state": "made"}})
            # a point matchup before them
            prompts.tell({{"kind": "matchup", "key": {nitrate!r}, "state": "matching"}})
            observations({nitrate!r}, "reading")
            observations({nitrate!r}, "read")
            files({nitrate!r}, 3, 1)
            wait("go3")
            prompts.tell({{"kind": "matchup", "key": {nitrate!r}, "state": "not_made"}})
            prompts.tell({{"kind": "matchup", "key": {chlorophyll!r}, "state": "matching"}})
            wait("go4")
            files({chlorophyll!r}, 20, 7)
            wait("go5")
            files({chlorophyll!r}, 20, 20, True)
            observations({chlorophyll!r}, "reading")
            wait("go6")
            prompts.tell({{"kind": "matchup", "key": {chlorophyll!r}, "state": "not_made"}})
            wait("never")
            """
        )
    )
    folder = tmp_path / "oceanval_interim_report"
    pages = folder / "oceanval_report" / "_build" / "html" / "notebooks"
    pages.mkdir(parents=True)
    (pages / "nsbc_temperature.html").write_text("<html><body><p>Temperature</p></body></html>")
    status = folder / "status.json"
    interim = {"status_path": str(status), "status": None}
    app = App(cwd=str(tmp_path))
    with app._lock:
        app.action = "matchup_validate"
        app.results_dir = str(tmp_path)
    app.start_run(["script", str(script)], f"python {script}")
    with app._lock:
        app.identifying = False
        app.interim = interim
    threading.Thread(target=app._follow_interim, args=(interim,), daemon=True).start()
    url = app.start()

    def cell(row, column):
        return page.locator(f'#progress-grid tbody tr:nth-child({row}) [data-column="{column}"]')

    def fits():
        """Whether the grid fits its box, all its columns in sight."""
        return page.evaluate("(() => { const grid = document.getElementById('progress-grid'); "
                             "return grid.scrollWidth <= grid.clientWidth; })()")

    total = '#progress-grid tfoot [data-column="total"]'
    stopwatch = re.compile(r"\d+:\d\d")

    def counts_on(selector):
        """Wait for a clock to change: the page counts on from the last state it was sent."""
        before = page.text_content(selector)
        page.wait_for_function(
            "([selector, text]) => document.querySelector(selector).textContent !== text",
            arg=[selector, before],
            timeout=5000,
        )

    def time_selector(row):
        return f'#progress-grid tbody tr:nth-child({row}) [data-column="time"]'

    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#progress-grid:not([hidden])", timeout=60000)
        assert page.locator("#progress-grid th").all_text_contents() == [
            "Variable", "Data source", "Model extraction", "Observation extraction", "Matchup", "Time",
            "Interim report", "Time elapsed",
        ]
        assert page.locator("#progress-grid tbody td:nth-child(-n+2)").all_text_contents() == [
            "Temperature", "NSBC", "Nitrate", "ICES point data (surface)", "Chlorophyll", "OCCCI",
        ]
        assert page.text_content("#progress-title") == "Getting ready to match up"
        # nothing done yet: empty
        assert cell(1, "matchup").get_attribute("data-state") == "waiting"
        assert cell(1, "matchup").text_content() == ""
        assert cell(1, "matchup").locator("[role=img]").get_attribute("aria-label") == "Not started"
        assert cell(1, "extraction").get_attribute("data-state") == "waiting"
        assert cell(1, "extraction").text_content() == ""
        assert cell(1, "observations").get_attribute("data-state") == "waiting"
        assert cell(1, "observations").text_content() == ""
        assert cell(1, "observations").locator("[role=img]").get_attribute("aria-label") == "Observations not read yet"
        assert fits()
        # a matchup the report leaves out has a dash in its column
        assert cell(3, "report").text_content() == "—"
        # no paragraph or output while it goes well, and never the command that runs
        assert page.is_hidden("#progress-text")
        assert page.locator("#progress-label").count() == 0
        assert page.is_hidden("#run")
        # no matchup has a time before it starts, and the time elapsed is already going
        assert [cell(row, "time").text_content() for row in (1, 2, 3)] == ["", "", ""]
        assert stopwatch.fullmatch(page.text_content(total))
        counts_on(total)

        (tmp_path / "go1").write_text("")
        page.wait_for_selector('#progress-grid tbody tr:nth-child(1) [data-state="working"] .identify__spinner')
        assert page.text_content("#progress-title") == "Matching up: 0 of 3 matchups made"
        assert page.locator("#progress-grid tbody tr.is-active").count() == 1
        spinner = cell(1, "matchup").locator(".identify__spinner")
        assert spinner.evaluate("node => getComputedStyle(node).animationName") == "spin"
        # beside the circle, its time counts up
        assert stopwatch.fullmatch(cell(1, "time").text_content())
        counts_on(time_selector(1))
        assert cell(2, "time").text_content() == ""
        # its model files are being read, how many not known yet: a circle
        page.wait_for_selector('#progress-grid tbody tr:nth-child(1) [data-column="extraction"] .identify__spinner')
        assert cell(1, "extraction").locator("[role=img]").get_attribute("aria-label") == "Reading 12 model files"

        (tmp_path / "count").write_text("")
        # then how many have been
        page.wait_for_selector('#progress-grid tbody tr:nth-child(1) [data-column="extraction"] .matchup-grid__count')
        assert cell(1, "extraction").text_content() == "5/12"
        assert cell(1, "extraction").locator("[role=img]").get_attribute("aria-label") == "Read 5 of 12 model files"
        assert cell(1, "matchup").get_attribute("data-state") == "working"

        (tmp_path / "go2").write_text("")
        # its model files are all read, and then its observations are
        page.wait_for_selector('#progress-grid tbody tr:nth-child(1) [data-column="observations"] .identify__spinner')
        assert cell(1, "observations").locator("[role=img]").get_attribute("aria-label") == "Reading the observations"
        assert cell(1, "extraction").get_attribute("data-state") == "done"
        assert cell(1, "matchup").get_attribute("data-state") == "working"

        (tmp_path / "observe").write_text("")
        status.write_text(json.dumps({
            "state": "building", "pages": 0, "expected": 2, "building_key": temperature,
            "added": {}, "failed": [], "included": [temperature, nitrate],
        }))
        page.wait_for_selector('#progress-grid tbody tr:nth-child(1) [data-column="report"][data-state="working"]')
        assert cell(1, "matchup").get_attribute("data-state") == "done"
        assert cell(1, "matchup").locator(".icon").count() == 1
        assert cell(1, "extraction").get_attribute("data-state") == "done"
        assert cell(1, "extraction").locator("[role=img]").get_attribute("aria-label") == "Read all 12 model files"
        assert cell(1, "observations").get_attribute("data-state") == "done"
        assert cell(1, "observations").locator("[role=img]").get_attribute("aria-label") == "Observations read"
        assert cell(2, "extraction").text_content() == "1/3"
        assert cell(2, "observations").get_attribute("data-state") == "done"
        assert cell(2, "matchup").get_attribute("data-state") == "working"
        assert page.text_content("#progress-title") == "Matching up: 1 of 3 matchups made"
        # the circle keeps turning: it is not drawn again as the state is
        cell(2, "matchup").locator(".identify__spinner").evaluate("node => { node.dataset.kept = 'yes'; }")
        # the first matchup's time stops at how long it took, the second's counts up
        took = cell(1, "time").text_content()
        assert stopwatch.fullmatch(took)
        assert cell(1, "time").get_attribute("data-live") == ""
        assert cell(2, "time").get_attribute("data-live") == "yes"
        counts_on(time_selector(2))
        assert cell(1, "time").text_content() == took

        status.write_text(json.dumps({
            "state": "building", "pages": 1, "expected": 2, "building_key": None,
            "added": {temperature: str(pages / "nsbc_temperature.html")}, "failed": [],
            "included": [temperature, nitrate],
            "landing": str(pages / "nsbc_temperature.html"),
        }))
        link = cell(1, "report").locator("a")
        link.wait_for()
        assert cell(1, "report").get_attribute("data-state") == "done"
        assert link.get_attribute("href").startswith("interim/")
        assert link.get_attribute("target") == "_blank"
        # which opens that matchup's page
        assert "Temperature" in link.evaluate("node => fetch(node.href).then((reply) => reply.text())")
        assert cell(2, "matchup").locator(".identify__spinner").get_attribute("data-kept") == "yes"

        (tmp_path / "go3").write_text("")
        page.wait_for_selector('#progress-grid tbody tr:nth-child(3) [data-column="matchup"][data-state="working"]')
        assert cell(2, "matchup").get_attribute("data-state") == "failed"
        assert cell(2, "matchup").locator("[role=img]").get_attribute("aria-label") == "Not made"
        # stopped part way through its files
        assert cell(2, "extraction").get_attribute("data-state") == "failed"
        assert cell(2, "extraction").locator("[role=img]").get_attribute("aria-label") == "Not all 3 model files read"
        # its observations had been read
        assert cell(2, "observations").get_attribute("data-state") == "done"
        # one started that has read none yet
        assert cell(3, "extraction").get_attribute("data-state") == "waiting"
        assert cell(3, "observations").get_attribute("data-state") == "waiting"
        # one not made keeps how long it was tried for, and the next one counts up
        assert stopwatch.fullmatch(cell(2, "time").text_content())
        assert cell(2, "time").get_attribute("data-live") == ""
        counts_on(time_selector(3))
        # the time elapsed is below the last row and goes on through it all
        assert page.locator(total).bounding_box()["y"] > cell(3, "time").bounding_box()["y"]
        assert page.locator(total).get_attribute("data-live") == "yes"
        # the interim report's card is at the top, above the grid
        assert page.is_visible("#interim a")
        assert page.locator("#interim").bounding_box()["y"] < page.locator("#progress").bounding_box()["y"]
        assert page.is_hidden("#run")

        (tmp_path / "go4").write_text("")
        page.wait_for_selector('#progress-grid tbody tr:nth-child(3) [data-column="extraction"] .matchup-grid__count')
        assert cell(3, "extraction").text_content() == "7/20"

        # in a narrower window it is smaller, and still fits, with all its columns
        page.set_viewport_size({"width": 700, "height": 800})
        page.wait_for_function("document.querySelector('#progress-grid table').getBoundingClientRect().width < 700")
        assert page.locator("#progress-grid th").count() == 8
        assert fits()

        # on a phone the grid scrolls in its box, never the page
        page.set_viewport_size({"width": 390, "height": 800})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        # and fits it, without the extraction columns: the count is under the circle
        page.wait_for_selector('#progress-grid tbody tr:nth-child(3) [data-column="matchup"] .matchup-grid__count')
        assert page.locator("#progress-grid th").all_text_contents() == [
            "Variable", "Data source", "Matchup", "Time", "Interim report", "Time elapsed",
        ]
        assert cell(3, "extraction").count() == 0
        assert cell(3, "observations").count() == 0
        assert cell(3, "matchup").locator(".identify__spinner").count() == 1
        assert cell(3, "matchup").locator(".matchup-grid__count").text_content() == "7/20"
        assert fits()
        # and back again
        page.set_viewport_size({"width": 1100, "height": 800})
        page.wait_for_selector('#progress-grid tbody tr:nth-child(3) [data-column="extraction"] .matchup-grid__count')
        assert cell(3, "matchup").locator(".matchup-grid__count").count() == 0

        (tmp_path / "go5").write_text("")
        page.wait_for_selector('#progress-grid tbody tr:nth-child(3) [data-column="observations"] .identify__spinner')
        assert cell(3, "extraction").get_attribute("data-state") == "done"
        # not made while its observations were being read
        (tmp_path / "go6").write_text("")
        page.wait_for_selector('#progress-grid tbody tr:nth-child(3) [data-column="observations"][data-state="failed"]')
        assert cell(3, "observations").locator("[role=img]").get_attribute("aria-label") == "Observations not read"
        assert cell(3, "matchup").get_attribute("data-state") == "failed"
    finally:
        app.close()


def test_the_analyses_grid_in_a_browser(browser, tmp_path):
    """While Compare simulations (or Analyze) runs, a grid of the variables
    and their analyses says what is being worked out: a hollow ring for what
    is to do, a turning circle while it is going, the count of simulations
    done in between, and a tick once done, with a column only for the
    analyses some variable has."""
    script = tmp_path / "simulation_comparison.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os, time
            from oceanval import prompts

            def wait(name):
                while not os.path.exists(os.path.join({str(tmp_path)!r}, name)):
                    time.sleep(0.05)

            def tell(kind, **news):
                prompts.tell({{"kind": kind, **news}})

            tell("analyses", simulations=2, items=[
                {{"key": "chl", "title": "Chlorophyll", "kinds": ["climatology", "long_term"]}},
                {{"key": "temp", "title": "Temperature", "kinds": ["transects"]}},
            ])
            wait("go1")
            tell("analysis_variable", key="chl", state="working")
            tell("analysis", key="chl", analysis="climatology", state="working")
            print("OceanVal: Working out the climatology of Chlorophyll: surface")
            wait("go2")
            tell("analysis", key="chl", analysis="climatology", state="done")
            wait("go3")
            tell("analysis", key="chl", analysis="long_term", state="working")
            tell("analysis", key="chl", analysis="long_term", state="done")
            tell("analysis", key="chl", analysis="climatology", state="working")
            tell("analysis", key="chl", analysis="climatology", state="done")
            tell("analysis", key="chl", analysis="long_term", state="working")
            tell("analysis", key="chl", analysis="long_term", state="done")
            tell("analysis_variable", key="chl", state="done")
            wait("never")
            """
        )
    )
    folder = tmp_path / sim_compare.FOLDER
    pages = folder / sim_compare.REPORT / "_build" / "html" / "notebooks"
    pages.mkdir(parents=True)
    (pages / "001_chl.html").write_text("<html><body><p>Chlorophyll</p></body></html>")
    app = App(cwd=str(tmp_path))
    with app._lock:
        app.action = "simcompare"
        app.results_dir = str(tmp_path)
    app.start_run(["simcompare", str(script)], f"python {script}")
    url = app.start()

    def cell(row, column):
        return page.locator(f'#progress-grid tbody tr:nth-child({row}) [data-column="{column}"]')

    def said(row, column):
        return cell(row, column).locator("[role=img]").get_attribute("aria-label")

    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#progress-grid:not([hidden])", timeout=60000)
        # a column for each analysis some variable has, and none for the others
        assert page.locator("#progress-grid th").all_text_contents() == [
            "Variable", "Climatology", "Long-term series", "Transects", "Time", "Report", "Time elapsed",
        ]
        assert page.locator("#progress-grid tbody .matchup-grid__title").all_text_contents() == [
            "Chlorophyll", "Temperature",
        ]
        assert page.text_content("#progress-title") == "Comparing: 0 of 2 variables worked out"
        # to do: a hollow ring
        assert cell(1, "climatology").get_attribute("data-state") == "todo"
        assert cell(1, "climatology").locator(".matchup-grid__todo").count() == 1
        assert said(1, "climatology") == "To do"
        assert cell(1, "report").get_attribute("data-state") == "todo"
        # an analysis a variable does not have: a dash
        assert cell(1, "transects").text_content() == "—"
        assert said(1, "transects") == "Not chosen for this variable"
        assert cell(2, "climatology").text_content() == "—"
        assert cell(2, "transects").get_attribute("data-state") == "todo"

        (tmp_path / "go1").write_text("")
        page.wait_for_selector('#progress-grid tbody tr:nth-child(1) [data-column="climatology"] .identify__spinner')
        assert page.locator("#progress-grid tbody tr.is-active").count() == 1
        assert said(1, "climatology") == "Being worked out (0 of 2 simulations done)"
        assert cell(1, "long_term").get_attribute("data-state") == "todo"
        assert re.fullmatch(r"\d+:\d\d", cell(1, "time").text_content())
        assert cell(2, "time").text_content() == ""
        # what is being worked out is said under the title
        page.wait_for_function(
            "document.getElementById('progress-text').textContent.includes('climatology of Chlorophyll')"
        )

        (tmp_path / "go2").write_text("")
        # done in one of the two simulations: the count
        page.wait_for_selector('#progress-grid tbody tr:nth-child(1) [data-column="climatology"] .matchup-grid__count')
        assert cell(1, "climatology").text_content() == "1/2"
        assert said(1, "climatology") == "Worked out in 1 of 2 simulations"

        (tmp_path / "go3").write_text("")
        page.wait_for_selector('#progress-grid tbody tr:nth-child(1) [data-column="long_term"][data-state="done"]')
        assert cell(1, "climatology").get_attribute("data-state") == "done"
        assert page.text_content("#progress-title") == "Comparing: 1 of 2 variables worked out"
        assert page.locator("#progress-grid tbody tr.is-active").count() == 0

        # its page in the report: a tick, a link to it
        (folder / analysis_report.STATUS).write_text(json.dumps({
            "state": "building", "pages": 1, "expected": 2, "built": ["Chlorophyll"],
            "added": {"chl": str(pages / "001_chl.html")}, "building_key": "temp", "failed": [],
            "landing": str(pages / "001_chl.html"),
        }))
        page.wait_for_selector('#progress-grid tbody tr:nth-child(1) [data-column="report"] a')
        assert cell(1, "report").get_attribute("data-state") == "done"
        assert cell(1, "report").locator("a").get_attribute("href").endswith("notebooks/001_chl.html")
        assert cell(2, "report").get_attribute("data-state") == "working"

        # it fits a phone's width, every analysis in sight, without the report's column
        page.set_viewport_size({"width": 390, "height": 800})
        page.wait_for_function("document.querySelectorAll('#progress-grid [data-column=\"report\"]').length === 0")
        assert page.locator("#progress-grid thead th").all_text_contents() == [
            "Variable", "Climatology", "Long-term series", "Transects", "Time",
        ]
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.evaluate("(() => { const grid = document.getElementById('progress-grid'); "
                             "return grid.scrollWidth <= grid.clientWidth; })()")
        # and back again
        page.set_viewport_size({"width": 1100, "height": 800})
        page.wait_for_selector('#progress-grid tbody tr:nth-child(1) [data-column="report"] a')
    finally:
        app.close()


def test_no_files_in_the_simulation_is_red_bold_and_large(browser, tmp_path):
    write_simulation(tmp_path / "sim")
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click('button.choice[data-action="matchup"]')
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "0")
        warning = page.locator("#m-simulation .group__line.is-nofiles")
        warning.wait_for()
        assert "No netCDF files in the directory itself" in warning.text_content()
        assert page.is_hidden("#ndown-found")
        body = warning.evaluate(
            "node => [getComputedStyle(node).color, getComputedStyle(node).fontWeight, getComputedStyle(node).fontSize]"
        )
        assert body == ["rgb(192, 57, 43)", "700", "25px"]
    finally:
        app.close()


@pytest.mark.parametrize(
    "layout, depth, text",
    [
        ("YYYY/MM", "2", "YYYY/MM/**.nc"),
        ("MM", "1", "MM/*.nc"),
        ("flat", "0", "*.nc"),
    ],
)
def test_the_directories_down_are_filled_in_from_the_layout(browser, tmp_path, layout, depth, text):
    """Told once in a pop-out, with the box in bold; the user's own value is never replaced by it."""
    write_folders(tmp_path / "sim", layout)
    write_simulation(tmp_path / "other")
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    try:
        page = browser.new_page()
        page.add_init_script("window.__keepFolderStructure = true")
        page.goto(url)
        page.click('button.choice[data-action="matchup"]')
        page.fill("#f-simdir", "sim")
        page.wait_for_function(f"document.querySelector('#f-ndown').value === '{depth}'")
        ndown = page.locator("#f-ndown")
        assert ndown.evaluate("node => node.classList.contains('is-detected')")
        assert not ndown.evaluate("node => node.classList.contains('is-oceanval')")
        # bold, no longer amber (the pop-out tells it instead)
        assert ndown.evaluate("node => getComputedStyle(node).fontWeight") == "700"
        assert ndown.evaluate("node => getComputedStyle(node).color") != "rgb(192, 57, 43)"
        assert page.locator("#ndown-layout").count() == 0
        popout = page.locator("#folder-structure")
        popout.wait_for(state="visible")
        assert popout.inner_text().count("It looks like the folder structure is " + text) == 1
        assert f"the number of directories down is assumed to be {depth}. Change it if this is not correct." in popout.inner_text()
        page.click("#folder-structure-continue")
        assert popout.is_hidden()
        # the files at that depth are counted
        page.wait_for_function("document.querySelector('#ndown-found').textContent.includes('netCDF file')")
        # a directory without the layout empties what OceanVal filled in
        page.fill("#f-simdir", "other")
        page.wait_for_function("document.querySelector('#f-ndown').value === ''")
        assert popout.is_hidden()
        # a value typed is the user's: it stays, with no suggestion shown
        page.fill("#f-ndown", "3")
        page.fill("#f-simdir", "sim")
        page.wait_for_timeout(1000)
        assert page.input_value("#f-ndown") == "3"
        assert not ndown.evaluate("node => node.classList.contains('is-detected')")
        assert popout.is_hidden()
    finally:
        app.close()


def test_the_domain_chosen_is_filled_and_bigger_than_the_other(browser, tmp_path):
    """A choice between boxes must be unmistakable: the chosen one is filled
    with the accent colour, bold, ticked and bigger."""
    write_simulation(tmp_path / "sim")
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click('button.choice[data-action="matchup_validate"]')
        page.fill("#f-simdir", "sim")
        look = """node => {
            const style = getComputedStyle(node);
            return [style.backgroundColor, style.color, style.fontWeight, node.getBoundingClientRect().width,
                    getComputedStyle(node, "::before").content];
        }"""
        chosen, other = "#f-domain input[value=global] + span", "#f-domain input[value=nwes] + span"
        before = page.locator(other).evaluate(look)
        page.check('#f-domain input[value="global"]')
        page.wait_for_function(
            "selector => getComputedStyle(document.querySelector(selector)).color === 'rgb(255, 255, 255)'",
            arg=chosen)
        page.wait_for_timeout(400)
        picked, left = page.locator(chosen).evaluate(look), page.locator(other).evaluate(look)
        assert left == before
        assert picked[0] != left[0] and picked[1] == "rgb(255, 255, 255)" and picked[2] == "700"
        assert picked[4] != left[4]
        # the transform makes it bigger, though not its layout box
        assert page.locator(chosen).evaluate("node => getComputedStyle(node).transform") != "none"
        assert page.locator(other).evaluate("node => getComputedStyle(node).transform") == "none"
    finally:
        app.close()


def test_the_preferences_and_info_are_on_the_fine_tune_page(browser, oceanvalrc, tmp_path):
    write_simulation(tmp_path / "sim")
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click('button.choice[data-action="matchup_validate"]')
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "2")
        page.wait_for_function("document.querySelector('#f-end').value === '2012'")
        choose_domain(page)
        page.click("#continue")
        page.click("#own-no")
        page.wait_for_url("**/recipes/**", timeout=90000)
        assert page.text_content("#page-title") == "Gridded data selection"
        assert page.locator(".masthead .brand #settings-open").count() == 1
        assert page.locator(".masthead .brand #info-open").count() == 1

        page.click("#settings-open")
        assert page.is_visible("#preferences")
        page.uncheck("#settings-show-demo")
        page.click("#settings-apply")
        page.wait_for_selector("#preferences", state="hidden")
        # saved through the app, which keeps it for its other pages
        assert app.state()["user_settings"]["values"]["show_demo"] is False
        assert json.loads(oceanvalrc.read_text())["settings"]["show_demo"] is False

        page.click("#info-open")
        assert page.is_visible("#info")
        assert page.text_content("#info-version") == f"Version {app.state()['oceanval_version']}"
        assert page.locator("#info-point tbody tr").count() == 10
        page.click("#info-close")
        assert page.is_hidden("#info")
    finally:
        app.close()


class _Reading:
    """A reading of the output's years that is never started."""

    def cancel(self):
        pass


def test_finding_key_information_in_a_browser(browser, tmp_path):
    """While create_recipes reads the simulation, a box in the middle of the
    page, with no title above it, says what is being found, and the years of
    the output once they are read from its files' times."""
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    app.leftovers_asked = True
    url = app.start()
    try:
        page = browser.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(url)
        page.wait_for_selector("#view-start:not([hidden])")
        reading = {"key": ("/data/sim", 2, (), ()), "result": None, "done": threading.Event(), "reading": _Reading()}
        with app._lock:
            app.action = "matchup"
            app.setup_form["simdir"] = "/data/sim"
            app._output_years = reading
            app.view = "preparing"
            app._notify()
        page.wait_for_selector("#prepare:not([hidden])")
        assert page.is_hidden("#title")
        assert page.is_hidden("#progress")
        assert page.inner_text(".prepare__card").split() == " ".join([
            "Finding key information about your simulation",
            "Finding the years of the output from the times in its files…",
        ]).split()
        assert page.text_content("#prepare-years") == "Finding the years of the output from the times in its files…"
        assert page.locator(".prepare__spinner").evaluate("node => getComputedStyle(node, '::after').animationName") == "spin"
        # in the middle of the page
        box = page.locator(".prepare__card").bounding_box()
        assert abs(box["x"] + box["width"] / 2 - 640) < 20
        assert box["y"] > 150

        with app._lock:
            reading["result"] = {"years": [1981, 2017]}
            app._notify()
        page.wait_for_function("document.querySelector('#prepare-years').textContent.includes('1981')")
        assert page.text_content("#prepare-years") == "Found output for the years 1981 to 2017"

        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        app.close()


@pytest.mark.parametrize("action", ["matchup", "matchup_validate"])
def test_what_is_done_with_existing_matchups_must_be_said(browser, tmp_path, monkeypatch, action):
    """In the output step, once the matchups are confirmed, a grid of the
    matchups there already: each to be replaced, if this run matches it up
    (to start with), used, or ignored, left out of the report, if it does not
    (asked about only if a report follows; used to start with), with a tick at
    the top of each column to choose it for every row that has it; what is
    chosen must be confirmed, as they may not notice what each starts on."""
    write_simulation(tmp_path / "sim")
    write_mixed_matchups(tmp_path / "old")
    # a stand-in for the script, which makes the gridded COBE2 temperature only
    made = dict(COBE2_MATCHUP, source="COBE2", key="temperature/COBE2")
    stand_in = tmp_path / "stand_in.py"
    stand_in.write_text(
        textwrap.dedent(
            f"""
            from oceanval import prompts
            question = "Are you happy with these matchups? (y/n) "
            print("answer:", prompts.ask(question, ("y", "n"), details={dict(MATCHUPS, matchups=[made])!r}))
            """
        )
    )
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    start_run = app.start_run
    monkeypatch.setattr(app, "start_run", lambda args, label: start_run([args[0], str(stand_in)], label))
    url = app.start()
    report = action == "matchup_validate"
    option = '#existing-body input[value="{}"][data-key="{}"]'
    made_keys = ["temperature/COBE2"]

    def choices():
        """What each row has ticked."""
        return page.locator("#existing-body tr").evaluate_all(
            "rows => rows.map((row) => [...row.querySelectorAll('input:checked')].map((box) => box.value))"
        )

    def offered():
        """The options each row has."""
        return page.locator("#existing-body tr").evaluate_all(
            "rows => rows.map((row) => [...row.querySelectorAll('input')].map((box) => box.value))"
        )

    def tops():
        """The options ticked at the top of their columns."""
        return [name for name in ("use", "replace", "ignore") if page.is_checked(f"#existing-all-{name}")]

    try:
        page = browser.new_page()
        page.goto(url)
        page.click(f'button.choice[data-action="{action}"]')
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "2")
        page.wait_for_function("document.querySelector('#f-end').value === '2012'")
        choose_domain(page)
        page.click("#continue")
        page.click("#own-no")
        page.wait_for_url("**/recipes/**", timeout=90000)
        fill_required_recipe_settings(page)
        page.click("#write")
        page.wait_for_selector("#units-body-gridded tr[data-units-row]")
        page.check("#units-confirm")
        page.click("#units-continue")
        page.wait_for_selector("#review:not([hidden])", timeout=90000)
        page.click("#actions button:has-text('Yes')")
        page.wait_for_selector("#view-output:not([hidden])")
        assert page.text_content("#actions button.btn--primary") == ("Continue" if report else "Match up")
        if not report:
            page.select_option("#f-cores", "1")
        page.wait_for_function("!document.getElementById('output-run').disabled")
        assert page.is_hidden("#row-existing")
        # with no report, those the run does not make are left as they are, unasked about
        rows = MIXED_KEYS if report else made_keys

        page.fill("#f-out_dir", "old")
        page.wait_for_selector("#row-existing:not([hidden])")
        # the grid says it; the directory box has no warning of its own
        assert page.locator("#m-out_dir .group__line.is-warn").count() == 0
        assert page.text_content("#existing-title") == "What do you want to do with existing matchups? Required"
        # confirming what is chosen is required, report or not
        assert page.is_visible("#existing-required")
        assert page.is_visible("#row-existing-confirm")
        # a column for each option some row has
        columns = ["use", "replace", "ignore"] if report else ["use", "replace"]
        assert [name for name in ("use", "replace", "ignore") if page.is_visible(f"#existing-col-{name}")] == columns
        assert [page.text_content(f"#existing-all-{name} >> xpath=..") for name in columns] == [
            name.title() for name in columns
        ]
        assert page.locator("#existing-body tr").evaluate_all(
            "rows => rows.map((row) => [...row.cells].slice(0, 2).map((cell) => cell.textContent))"
        ) == [[key.split("/")[0].title(), key.split("/")[1]] for key in rows]
        # one the run makes is used or replaced, one it does not is used or ignored
        assert offered() == [["use", "replace" if key in made_keys else "ignore"] for key in rows]
        intro = page.text_content("#existing-intro")
        assert ("Replace: it is one this run matches up" in intro) and (("Ignore:" in intro) == report)
        # those the run makes are replaced to start with, the others used
        assert choices() == [["replace" if key in made_keys else "use"] for key in rows]
        assert tops() == ["replace"]
        assert page.is_disabled("#output-run")

        # one option a row, and one at the top chooses it for every row that has it
        page.check("#existing-all-use")
        assert choices() == [["use"]] * len(rows)
        assert tops() == ["use"]
        page.check("#existing-all-replace")
        assert choices() == [["replace" if key in made_keys else "use"] for key in rows]
        assert "replace" in tops()
        if report:
            page.check("#existing-all-ignore")
            assert choices() == [["replace" if key in made_keys else "ignore"] for key in rows]
            assert tops() == ["replace", "ignore"]
        page.check("#existing-all-use")
        assert choices() == [["use"]] * len(rows)
        assert tops() == ["use"]
        page.check(option.format("replace", "temperature/COBE2"))
        if report:
            page.check(option.format("ignore", "nitrate/glodap"))
        # what is chosen must be confirmed
        assert page.is_disabled("#output-run")
        page.check("#f-existing_confirmed")
        assert page.is_enabled("#output-run")

        page.click("#output-run")
        # the one replaced is all those the run makes; only those used are in the report
        wait_for(lambda: app.view != "output")
        assert app.overwrite is True
        if report:
            assert app.view == "report_options"
            assert app.previous == ["temperature/WOA23", "nitrate/ICES"]
        else:
            assert app.previous is True
        assert app.results_dir == str(tmp_path / "old")
    finally:
        app.close()


def test_closing_the_browser_quits_the_app(browser, tmp_path):
    """The window stays open for as long as the page is, through a reload,
    and closing the page quits."""
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    app.page_timeout = 3
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector('button.choice[data-action="matchup"]')
        time.sleep(6)
        assert not app.closed.is_set()

        page.reload()
        page.wait_for_selector('button.choice[data-action="matchup"]')
        time.sleep(5)
        assert not app.closed.is_set()

        page.close()
        assert app.closed.wait(15)
        assert app.closed_reason == "The browser window was closed."
    finally:
        app.close()


def test_the_recipes_window_keeps_the_app_open(browser, tmp_path, monkeypatch):
    """The recipes window makes no state requests, so it says it is open with
    heartbeats, and closing it quits the app."""
    write_simulation(tmp_path / "sim", tracers=True)
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    beats = []
    heartbeat = app.heartbeat
    monkeypatch.setattr(
        app, "heartbeat", lambda page, **kwargs: beats.append(page) or heartbeat(page, **kwargs)
    )
    url = app.start()
    app.page_timeout = 3
    try:
        page = browser.new_page()
        page.goto(url)
        page.click('button.choice[data-action="matchup"]')
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "2")
        page.wait_for_function("document.querySelector('#f-end').value === '2012'")
        choose_domain(page)
        page.click("#continue")
        page.click("#own-no")
        page.wait_for_url("**/recipes/**", timeout=90000)
        wait_for(lambda: beats, timeout=15)
        time.sleep(6)
        assert not app.closed.is_set()

        page.close()
        assert app.closed.wait(15)
    finally:
        app.close()


def test_the_demo_grid_in_a_browser(browser, tmp_path, fetched):
    """One model at a time, of three, with one to three of its variables;
    what is ticked is downloaded, and the model's name fills the box that
    picks its files out."""
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click('button.choice[data-action="demo"]')
        page.wait_for_selector("#demo-grid")

        assert page.locator("#demo-grid thead th").all_text_contents() == [
            "Model", "Sea surface temperature", "Surface nitrate", "Sea surface salinity"]
        assert page.locator("#demo-grid-body tr").count() == 3
        assert page.locator("#demo-grid-body th .demo-grid__name").all_text_contents() == list(
            app_module.DEMO_MODELS)
        servers = page.locator("#demo-grid-body th .demo-grid__where").all_text_contents()
        assert [text.split(" · ")[0] for text in servers] == [
            model["server"] for model in app_module.DEMO_MODELS.values()]
        # nothing ticked, so nothing to download
        assert page.is_disabled("#demo-start")
        assert page.is_hidden("#demo-chosen")

        def box(model, title):
            return page.locator(f'input[aria-label="{title} from {model}"]')

        box("NorESM2-LM", "Sea surface salinity").check()
        box("NorESM2-LM", "Surface nitrate").check()
        assert page.is_enabled("#demo-start")
        assert page.is_visible("#demo-chosen")
        # in the order of the columns, whatever the order they were ticked in
        assert page.text_content("#demo-chosen-variables").startswith("Surface nitrate")
        assert "NorESM2-LM" in page.text_content("#demo-chosen-model")

        # ticking another model's clears the first's
        box("UKESM1-0-LL", "Sea surface temperature").check()
        assert page.locator("#demo-grid input:checked").count() == 1
        assert box("UKESM1-0-LL", "Sea surface temperature").is_checked()
        assert "MB" in page.text_content("#demo-chosen-files")
        # and unticking the last one leaves nothing chosen
        box("UKESM1-0-LL", "Sea surface temperature").uncheck()
        assert page.is_disabled("#demo-start")
        assert page.is_hidden("#demo-chosen")

        box("MPI-ESM1-2-LR", "Surface nitrate").check()
        box("MPI-ESM1-2-LR", "Sea surface temperature").check()
        page.click("#demo-start")
        page.click("#demo-continue")
        page.wait_for_selector("#f-simdir")
        assert page.input_value("#f-require") == "MPI-ESM1-2-LR"
        assert "is-oceanval" in page.get_attribute("#f-require", "class")
        names, urls = demo_files("MPI-ESM1-2-LR", ["tos", "no3os"])
        assert fetched == urls

        # back, the same boxes are ticked, and their files are there already
        page.click("#actions button:has-text('Back')")
        page.click("#actions button:has-text('Back')")
        page.wait_for_selector("#demo-grid")
        assert page.locator("#demo-grid input:checked").count() == 2
        assert page.text_content("#demo-start") == "Continue"
    finally:
        app.close()


def test_the_units_step_in_a_browser(browser, tmp_path, monkeypatch):
    """The table lists the units of each matchup with the conversion OceanVal
    fills in where they differ, in bold for as long as it is OceanVal's,
    the units have to be confirmed to carry on, a conversion that is not a
    number is marked, and those that are go into the script."""
    write_simulation(tmp_path / "sim", tracers=True)
    stand_in = tmp_path / "stand_in.py"
    stand_in.write_text("print('running')\n")
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    start_run = app.start_run
    monkeypatch.setattr(
        app,
        "start_run",
        lambda args, label: start_run(["script", str(stand_in)], label),
    )
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click('button.choice[data-action="matchup"]')
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "2")
        page.wait_for_function("document.querySelector('#f-end').value === '2012'")
        choose_domain(page)
        page.click("#continue")
        page.click("#own-no")
        page.wait_for_url("**/recipes/**", timeout=90000)
        fill_required_recipe_settings(page)
        page.click("#write")

        page.wait_for_selector("#units-body-gridded tr[data-units-row]")
        # the units start unconfirmed, and nothing carries on until they are
        assert "Tick if you are happy with the units" in page.text_content("#summary")
        assert page.is_visible("#units-confirm")
        assert not page.is_checked("#units-confirm")
        assert page.is_disabled("#units-continue")
        assert page.is_hidden("#units-reading")
        # two sections, and with no point datasets the second says so
        assert page.text_content("#units-title-gridded") == "Gridded datasets"
        assert page.text_content("#units-title-point") == "Point datasets"
        assert page.locator("#units-body-point tr").count() == 0
        assert page.is_visible("#units-empty-point")
        assert page.is_hidden("#units-empty-gridded")

        temperature = page.locator('tr[data-units-row="recipe:temperature:cobe2"]')
        for text in ("temperature (cobe2)", "thetao", "degC", "sst"):
            assert text in temperature.text_content()
        # what OceanVal made of the units is on the line under each row
        below = "xpath=following-sibling::tr[1]"
        assert "OceanVal thinks these are the same units" in temperature.locator(below).text_content()
        # which is a suggestion, so it is not ticked off
        assert temperature.locator(f"{below}//*[contains(@class, 'icon')]").count() == 0
        multiplier = temperature.locator('input[data-units-box="multiplier"]')
        assert multiplier.input_value() == ""
        # WOA23's nitrate is per kilogram, and the model's per volume
        nitrate = page.locator('tr[data-units-row="recipe:nitrate:woa23"]')
        assert nitrate.locator('input[data-units-box="multiplier"]').input_value() == "1.025"
        assert "1025 kg/m³" in nitrate.locator(below).text_content()

        # what OceanVal filled in is red and bold, and what it did not is not
        def look(box):
            return box.evaluate(
                "node => [getComputedStyle(node).color, getComputedStyle(node).fontWeight]"
            )

        red_bold = ["rgb(192, 57, 43)", "700"]
        oceanval = nitrate.locator('input[data-units-box="multiplier"]')
        oceanval_note = nitrate.locator(f"{below}//*[contains(@class, 'group__line')]")
        assert look(oceanval) == red_bold
        assert look(oceanval_note) == red_bold
        assert look(multiplier)[1] == "400"
        assert look(temperature.locator(f"{below}//*[contains(@class, 'group__line')]"))[1] == "400"
        # units OceanVal has concluded are the same are amber
        assert "is-same" in temperature.locator(f"{below}//*[contains(@class, 'group__line')]").get_attribute("class")
        assert look(temperature.locator(f"{below}//*[contains(@class, 'group__line')]"))[0] == "rgb(168, 106, 0)"
        # but not once it is changed, which the note says, and it is again if put back
        oceanval.fill("2")
        assert look(oceanval)[1] == "400"
        assert look(oceanval_note)[1] == "400"
        assert "You have changed the conversion OceanVal filled in" in nitrate.locator(below).text_content()
        oceanval.fill("1.025")
        assert look(oceanval) == red_bold
        assert look(oceanval_note) == red_bold

        # the units always have to be confirmed, under the tables, to carry on
        page.check("#units-confirm")
        assert page.is_enabled("#units-continue")
        page.uncheck("#units-confirm")
        assert page.is_disabled("#units-continue")

        # changing a conversion undoes the confirmation, as it is not what was confirmed
        page.check("#units-confirm")
        multiplier.fill("abc")
        assert not page.is_checked("#units-confirm")
        assert page.is_disabled("#units-continue")
        page.check("#units-confirm")
        page.click("#units-continue")
        page.wait_for_selector(".units-body .is-invalid")
        error = page.locator('[data-units-error="recipe:temperature:cobe2"]')
        assert error.text_content() != ""
        multiplier.fill("2")
        page.check("#units-confirm")
        page.click("#units-continue")
        page.wait_for_selector("#console-text:has-text('running')", state="attached", timeout=60000)

        text = open(draft(app)).read()
        assert "    obs_multiplier=1.025,\n" in text[text.index('name="nitrate"') :].split("\n)\n")[0]
        assert "    obs_multiplier=2,\n" in text
    finally:
        app.close()


def look(locator):
    """A box's colour and weight, which are red and bold for what OceanVal filled in."""
    return locator.evaluate("node => [getComputedStyle(node).color, getComputedStyle(node).fontWeight]")


def test_going_back_keeps_what_was_entered_in_a_browser(browser, tmp_path, monkeypatch):
    """Back from the report options all the way to the simulation, with what
    was entered in every step kept for when it is reached again. A stand-in
    for the script asks about the matchups, as matchup does."""
    write_simulation(tmp_path / "sim", tracers=True)
    stand_in = tmp_path / "stand_in.py"
    stand_in.write_text(
        textwrap.dedent(
            f"""
            from oceanval import prompts
            question = "Are you happy with these matchups? (y/n) "
            print("answer:", prompts.ask(question, ("y", "n"), details={MATCHUPS!r}))
            print("matching up")
            """
        )
    )
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    start_run = app.start_run
    validated = []
    monkeypatch.setattr(
        app,
        "start_run",
        lambda args, label: validated.append(args) if args[0] == "validate"
        else start_run([args[0], str(stand_in)], label),
    )
    url = app.start()
    red_bold = ["rgb(192, 57, 43)", "700"]
    nsbc = 'input[aria-label="NSBC, Northwest European Shelf, for Temperature"]'
    nsbc_start = 'input[aria-label="First year of NSBC observations for Temperature"]'
    try:
        page = browser.new_page()

        def bar(text):
            return page.locator(f"#actions button:has-text('{text}')")

        def to_units():
            page.wait_for_selector("#view-units-table:not([hidden]) #units-body-gridded tr[data-units-row]",
                                   timeout=90000)

        def to_output():
            page.wait_for_selector("#view-output:not([hidden])")
            page.wait_for_function("!document.getElementById('output-run').disabled")

        page.goto(url)
        page.click('button.choice[data-action="matchup_validate"]')
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "2")
        page.wait_for_function("document.querySelector('#f-end').value === '2012'")
        choose_domain(page)
        page.click("#continue")
        page.click("#own-no")
        page.wait_for_url("**/recipes/**", timeout=90000)
        fill_required_recipe_settings(page)
        # carrying on from the point datasets' page, and a model variable box that is text only
        assert page.text_content("#write") == "Continue"
        assert page.locator('input[aria-label^="Simulation variable for"]').first.get_attribute("list") is None
        assert page.locator("#model-variables").count() == 0
        # Back to the gridded datasets' page, which keeps what was entered
        page.click("#cancel")
        assert page.text_content("#page-title") == "Gridded data selection"
        assert page.input_value("#s-clim_start") == "2011"
        page.check(nsbc)
        page.fill(nsbc_start, "2012")
        page.click("#write")
        assert page.input_value("#s-point_start") == "2011"
        page.click("#write")

        # a conversion of the user's own, and OceanVal's for nitrate
        to_units()
        temperature = page.locator('tr[data-units-row="recipe:temperature:cobe2"] input[data-units-box="adder"]')
        nitrate = page.locator('tr[data-units-row="recipe:nitrate:woa23"] input[data-units-box="multiplier"]')
        assert nitrate.input_value() == "1.025"
        temperature.fill("0.5")
        page.check("#units-confirm")
        page.click("#units-continue")

        # Back from the matchups stops the run before anything is matched up
        page.wait_for_selector("#review:not([hidden])", timeout=90000)
        assert bar("Back").is_visible()
        assert bar("Yes, continue").is_visible()
        assert page.locator("#actions button:has-text('Stop')").count() == 0
        # beside the answers, without the page scrolling sideways on a phone
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.set_viewport_size({"width": 1280, "height": 720})
        bar("Back").click()
        to_units()
        assert temperature.input_value() == "0.5"
        assert look(temperature)[1] == "400"
        assert nitrate.input_value() == "1.025"
        assert look(nitrate) == red_bold
        # the units are confirmed afresh
        assert not page.is_checked("#units-confirm")
        assert page.is_disabled("#units-continue")
        assert "matching up" not in page.text_content("#console-text")

        # the recipes window, as it was left: on the point datasets' page
        bar("Back").click()
        page.wait_for_url("**/recipes/**", timeout=90000)
        assert page.text_content("#page-title") == "Point data selection"
        assert (page.input_value("#s-point_start"), page.input_value("#s-point_end")) == ("2011", "2012")
        page.click("#cancel")
        assert page.is_checked(nsbc)
        assert page.input_value(nsbc_start) == "2012"
        page.click("#write")
        page.click("#write")
        to_units()
        assert temperature.input_value() == "0.5"
        page.check("#units-confirm")
        page.click("#units-continue")

        # where the matchups are saved, once they are confirmed, and then the report options
        page.wait_for_selector("#review:not([hidden])", timeout=90000)
        page.click("#actions button:has-text('Yes')")
        to_output()
        assert page.text_content("#actions button.btn--primary") == "Continue"
        assert page.is_hidden("#f-cores")
        page.fill("#f-out", "mine.py")
        page.click("#output-run")
        page.wait_for_selector("#view-validate:not([hidden])")
        # the cores are asked for here, with no default
        assert page.is_visible("#v-cores")
        assert page.input_value("#v-cores") == ""
        assert page.is_disabled("#build")
        page.select_option("#v-cores", "1")
        assert page.is_enabled("#build")
        # then the missing values, matchup's too, and its spatial subset
        assert page.is_visible("#v-missing_from")
        assert page.locator("#v-cores").bounding_box()["y"] < page.locator("#v-missing_from").bounding_box()["y"]
        assert page.text_content("#v-subset-legend") == "Do you want a spatial subset?"
        assert "Empty: the whole domain." in page.text_content("#vm-subset")
        page.fill("#v-missing_from", "0")
        page.check("#v-pdf")
        page.select_option("#v-subregions", "global")
        # Back keeps them, and where the matchups are saved
        bar("Back").click()
        to_output()
        assert page.input_value("#f-out") == "mine.py"
        bar("Back").click()
        page.wait_for_selector("#review:not([hidden])")
        page.click("#actions button:has-text('Yes')")
        to_output()
        assert page.input_value("#f-out") == "mine.py"
        page.click("#output-run")
        page.wait_for_selector("#view-validate:not([hidden])")
        assert page.is_checked("#v-pdf")
        assert page.input_value("#v-subregions") == "global"
        assert page.input_value("#v-cores") == "1"
        assert page.input_value("#v-missing_from") == "0"

        # and back all the way to the simulation
        bar("Back").click()
        to_output()
        bar("Back").click()
        page.wait_for_selector("#review:not([hidden])")
        bar("Back").click()
        to_units()
        bar("Back").click()
        page.wait_for_url("**/recipes/**", timeout=90000)
        # Back to the gridded datasets' page, and Back from it
        page.click("#cancel")
        page.click("#cancel")
        page.wait_for_selector("#view-own:not([hidden])", timeout=60000)
        # the answer given before
        assert "is-chosen" in page.get_attribute("#own-no", "class")
        assert page.is_visible("#own-no .choice__tag")
        assert page.is_hidden("#own-yes .choice__tag")
        bar("Back").click()
        page.wait_for_selector("#view-setup:not([hidden])")
        assert (page.input_value("#f-simdir"), page.input_value("#f-ndown")) == ("sim", "2")
        assert validated == []
    finally:
        app.close()


def test_the_recipes_window_after_the_simulation_changes_in_a_browser(browser, tmp_path):
    """What OceanVal could not keep of what the window held, once the
    simulation is read again, is said in bold."""
    write_simulation(tmp_path / "sim", tracers=True)
    # temperature only
    write_simulation(tmp_path / "other")
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    red_bold = ["rgb(192, 57, 43)", "700"]
    try:
        app.choose("matchup")
        post(app, "/api/setup", {"form": SETUP_FORM})
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "recipes")
        # left with Back, with nitrate as a sum the other simulation cannot give
        rows = [
            {"variable": "nitrate", "model_variable": "N3_n+thetao", "selected": ["woa23"]},
            {"variable": "temperature", "model_variable": "thetao", "selected": ["nsbc"]},
        ]
        post(app, "/recipes/cancel", {"rows": rows, "settings": {"point_start": "2011", "point_end": "2012"}})
        wait_for(lambda: app.view == "own_data")
        post(app, "/api/back")
        post(app, "/api/setup", {"form": dict(SETUP_FORM, simdir="other", domain="nwes")})
        post(app, "/api/own_data", {"answer": False})
        wait_for(lambda: app.view == "recipes")

        page = browser.new_page()
        page.goto(url)
        page.wait_for_url("**/recipes/**", timeout=90000)
        lede = page.locator("#lede-gridded .oceanval-change")
        assert lede.text_content() == "As the domain has changed, OceanVal has ticked the datasets afresh for it."
        assert look(lede) == red_bold
        dropped = page.locator("#message-gridded-nitrate .oceanval-change")
        assert dropped.text_content() == "N3_n+thetao, chosen before, is not in this simulation's output."
        assert look(dropped) == red_bold
        # temperature is kept, with its datasets ticked as for the domain, and the years
        assert page.input_value('input[aria-label="Simulation variable for Temperature"]') == "thetao"
        assert page.is_checked('input[aria-label="NSBC, Northwest European Shelf, for Temperature"]')
        assert (page.input_value("#s-point_start"), page.input_value("#s-point_end")) == ("2011", "2012")
        # once nitrate is filled in, it is the user's
        page.fill('#rows-gridded input[aria-label="Simulation variable for Nitrate"]', "thetao")
        assert page.locator("#message-gridded-nitrate .oceanval-change").count() == 0
    finally:
        app.close()


def test_clearing_every_selection_in_a_browser(browser, tmp_path):
    """Deselect all unticks every dataset, and is then disabled until
    one is ticked again."""
    write_simulation(tmp_path / "sim")
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click('button.choice[data-action="matchup"]')
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "2")
        page.wait_for_function("document.querySelector('#f-end').value === '2012'")
        choose_domain(page)
        page.click("#continue")
        page.click("#own-no")
        page.wait_for_url("**/recipes/**", timeout=90000)

        ticked = "#rows-gridded .chip input:checked"
        assert page.locator(ticked).count() > 0
        assert page.is_enabled("#clear-gridded")
        page.click("#clear-gridded")

        assert page.locator(ticked).count() == 0
        # the counts of variables matched and comparisons selected are not shown
        assert "matched" not in page.text_content("#summary")
        assert "selected" not in page.text_content("#summary")
        assert page.is_disabled("#clear-gridded")
        # a dataset can be ticked again
        page.locator("#rows-gridded .chip input:not(:disabled)").first.check()
        assert page.is_enabled("#clear-gridded")
    finally:
        app.close()


def test_adding_your_own_data_in_a_browser(browser, tmp_path):
    """Yes leads to the point data step, where what has to be given is marked
    in red, an entry is added, and Skip becomes Continue."""
    write_simulation(tmp_path / "sim")
    (tmp_path / "points").mkdir()
    (tmp_path / "points" / "obs.csv").write_text("lon,lat,observation\n1,2,3\n")
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click('button.choice[data-action="matchup"]')
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "2")
        page.wait_for_function("document.querySelector('#f-end').value === '2012'")
        choose_domain(page)
        page.click("#continue")
        page.click("#own-yes")
        page.wait_for_selector("#own-form-point")

        assert page.text_content("#own-next") == "Skip"
        assert page.get_attribute("#o-point-name", "list") == "own-variable-options"
        suggestions = page.locator("#own-variable-options option").evaluate_all(
            "nodes => nodes.map(node => node.value)"
        )
        assert {"temperature", "nitrate"} <= set(suggestions)
        optional = page.locator("#own-optional-point")
        chevron = optional.locator(".own-optional__chevron")
        assert not optional.evaluate("node => node.open")
        closed_chevron = chevron.evaluate("node => getComputedStyle(node).transform")
        page.locator("#own-optional-point > summary").click()
        assert optional.evaluate("node => node.open")
        # it turns over a CSS transition, so is read once that has begun
        page.wait_for_function(
            "([node, before]) => getComputedStyle(node).transform !== before",
            arg=[chevron.element_handle(), closed_chevron],
        )
        red = "rgb(192, 57, 43)"

        def field_of(name):
            return page.locator(f"#o-point-{name}").locator(
                "xpath=ancestor::div[contains(concat(' ', @class, ' '), ' own-field ')]"
            )

        for name in ("name", "source", "model_variable", "obs_path"):
            field = field_of(name)
            assert "is-required" in field.get_attribute("class")
            assert field.locator(".g-label").evaluate("node => getComputedStyle(node).color") == red
        # optional ones are not
        assert field_of("long_name").get_attribute("class") == "own-field"

        page.click("#own-add-point")
        page.wait_for_selector("#o-point-name-err:not(:empty)")
        assert page.get_attribute("#o-point-name", "aria-invalid") == "true"

        page.fill("#o-point-name", "chl")
        page.fill("#o-point-source", "cruise")
        page.fill("#o-point-model_variable", "thetao")
        page.fill("#o-point-obs_path", "points")
        page.click("#own-add-point")
        page.wait_for_selector("#own-list-point:not([hidden])")
        page.wait_for_function("document.querySelector('#own-next').textContent === 'Continue'")

        page.click("#own-next")
        page.wait_for_selector("#own-form-gridded")
        assert page.get_attribute("#o-gridded-name", "list") == "own-variable-options"
        assert page.is_visible("#o-gridded-climatology")
        assert app.own_data["point"][0]["name"] == "chl"
    finally:
        app.close()


def test_picking_the_variables_of_your_own_data_in_a_browser(browser, tmp_path):
    """The simulation variable is picked from the simulation's variables, and
    the observation variable from those in the first file of the observations."""
    write_simulation(tmp_path / "sim")
    (tmp_path / "obs").mkdir()
    for year in (2011, 2012):
        xr.Dataset(
            {
                "chl": (("lat", "lon"), np.random.rand(2, 2), {"long_name": "chlorophyll", "units": "mg m-3"}),
                "kd490": (("lat", "lon"), np.random.rand(2, 2), {"long_name": "light attenuation"}),
            },
            coords={"lat": [50.0, 51.0], "lon": [0.0, 1.0]},
        ).to_netcdf(tmp_path / "obs" / f"obs_{year}.nc")
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click('button.choice[data-action="matchup"]')
        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "2")
        page.wait_for_function("document.querySelector('#f-end').value === '2012'")
        choose_domain(page)
        page.click("#continue")
        page.click("#own-yes")
        page.wait_for_selector("#own-form-point")

        # the simulation's variables, searched by name or long_name
        shown = lambda: page.evaluate("() => [...document.querySelectorAll('.vars__popover:not([hidden]) tbody tr:not([hidden])')].map((row) => row.cells[1].textContent)")
        page.wait_for_function("document.querySelector('#o-point-model_variable-pick').innerText.includes('Click in the box')")
        page.fill("#o-point-model_variable", "potential temp")
        assert shown() == ["thetao"]
        page.locator(".vars__popover:not([hidden]) tbody tr:not([hidden])").first.click()
        assert page.input_value("#o-point-model_variable") == "thetao"
        page.click("#own-next")
        page.wait_for_selector("#own-form-gridded")

        # the same on the gridded step, where several are summed with +
        page.wait_for_function("document.querySelector('#o-gridded-model_variable-pick').innerText.includes('Click in the box')")
        page.fill("#o-gridded-model_variable", "thetao+")
        assert "thetao" in shown()
        page.fill("#o-gridded-model_variable", "")

        # nothing to pick from until there are observations
        assert page.locator("#o-gridded-obs_variable-pick").inner_text() == ""
        page.fill("#o-gridded-obs_path", "obs")
        picker = page.locator("#o-gridded-obs_variable-pick")
        page.wait_for_function("document.querySelector('#o-gridded-obs_variable-pick').innerText.includes('click in the box')")
        assert "variables in obs_2011.nc, the first of 2 files" in picker.inner_text()
        page.fill("#o-gridded-obs_variable", "attenuation")
        assert shown() == ["kd490"]
        page.locator(".vars__popover:not([hidden]) tbody tr:not([hidden])").first.click()
        assert page.input_value("#o-gridded-obs_variable") == "kd490"
        # one is chosen, and the list closes
        assert shown() == []

        # a path with no data says so, and an address is not looked at
        page.fill("#o-gridded-obs_path", "nowhere")
        page.wait_for_function("document.querySelector('#o-gridded-obs_variable-pick').innerText.includes('no file or directory')")
        page.fill("#o-gridded-obs_path", "https://example.org/data.nc")
        page.wait_for_function("document.querySelector('#o-gridded-obs_variable-pick').innerText === ''")
    finally:
        app.close()


def test_choosing_directories_in_a_browser(browser, tmp_path):
    """The folder browser and the folders suggested as a path is typed, on the
    simulation step's directory and the validate step's matchups."""
    write_simulation(tmp_path / "sim")
    (tmp_path / "sim_old").mkdir()
    write_matchups(tmp_path / "run")
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click('button.choice[data-action="matchup"]')

        page.click('[data-browse="f-simdir"]')
        page.click('#picker-list .picker__row[title="sim"]')
        page.wait_for_function(
            "document.querySelector('#picker-crumbs .is-current').textContent === 'sim'"
        )
        page.click("#picker-choose")
        # as if it had been typed: the live check is asked (the years wait for the depth)
        page.wait_for_function("!document.querySelector('#ndown-found').classList.contains('is-finding')")
        assert page.input_value("#f-simdir") == "sim"
        assert page.input_value("#f-ndown") == ""

        page.fill("#f-simdir", "si")
        page.wait_for_function(
            "document.querySelectorAll('#f-simdir-folders option').length === 2"
        )
        assert page.eval_on_selector_all(
            "#f-simdir-folders option", "options => options.map(o => o.value)"
        ) == ["sim/", "sim_old/"]

        page.click("#actions >> text=Back")
        page.click('button.choice[data-action="validate"]')
        # concise or detailed is the last option, and concise to start with, with
        # only the depth bins after it, as the matchups may be through the water column
        assert page.evaluate(
            "[...document.querySelectorAll('#v-options > fieldset')].slice(-2)"
            ".map(group => group.id || group.querySelector('select').id)"
        ) == ["v-concise", "v-group-depth_bins"]
        assert page.input_value("#v-concise") == "true"
        assert page.locator("#v-concise option").all_text_contents() == ["Concise", "Detailed"]
        page.click('[data-browse="v-data_dir"]')
        page.click('#picker-list .picker__row[title="run"]')
        page.wait_for_function(
            "document.querySelector('#picker-crumbs .is-current').textContent === 'run'"
        )
        assert page.text_content("#picker-info") == "oceanval_matchups is here."
        page.click("#picker-choose")
        assert page.input_value("#v-data_dir") == "run"
        assert page.is_hidden("#picker")
    finally:
        app.close()


def test_viewing_files_in_a_random_directory_in_a_browser(browser, tmp_path):
    """The pop-up on the simulation step that lists the file names in one
    directory of the simulation, chosen at random."""
    for year in ("2001", "2002"):
        (tmp_path / "sim" / year).mkdir(parents=True)
        for name in ("a_grid_T.nc", "a_ptrc_T.nc"):
            (tmp_path / "sim" / year / name).write_text("")
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click('button.choice[data-action="matchup"]')

        # nothing to look in yet
        page.click("#view-files")
        # "Looking…" is said first, in the same style, until the answer comes
        page.wait_for_selector("#sample-list .picker__empty:has-text('Enter the directory')")
        page.keyboard.press("Escape")
        assert page.is_hidden("#sample")

        page.fill("#f-simdir", "sim")
        page.fill("#f-ndown", "1")
        page.click("#view-files")
        page.wait_for_selector("#sample-list .picker__file")
        assert page.eval_on_selector_all(
            "#sample-list .picker__file", "nodes => nodes.map(n => n.textContent)"
        ) == ["a_grid_T.nc", "a_ptrc_T.nc"]
        first = page.text_content("#sample-intro")
        assert first in ("sim/2001", "sim/2002")

        # another directory, never the same one twice running
        page.click("#sample-another")
        page.wait_for_function(
            "text => document.querySelector('#sample-intro').textContent !== text", arg=first
        )
        page.click("#sample-close")
        assert page.is_hidden("#sample")
    finally:
        app.close()


def test_how_files_are_identified_pop_up_in_a_browser(browser, tmp_path):
    """The pop-up on the simulation step that explains how OceanVal finds the
    simulation files, and why the skip and only boxes are there."""
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click('button.choice[data-action="matchup"]')
        assert page.is_hidden("#how")
        page.click("#how-files")
        assert page.is_visible("#how")
        text = page.text_content("#how")
        assert "highest temporal resolution" in text
        assert "Skip file names containing" in text
        assert "Only include file names containing" in text
        page.keyboard.press("Escape")
        assert page.is_hidden("#how")
        assert page.evaluate("document.activeElement.id") == "how-files"
        page.click("#how-files")
        page.click("#how-close")
        assert page.is_hidden("#how")
    finally:
        app.close()


class TestLeftovers:
    """The temporary files earlier sessions left behind, which the window
    offers to remove."""

    def test_found_files_are_in_the_state(self, leftover_dir, tmp_path):
        path = _leftover(leftover_dir)
        _leftover(leftover_dir, "unrelated.nc")
        _leftover(leftover_dir, "nctoolkit_me_abcdnctoolkit_other_tmp2.nc")
        state = App(cwd=str(tmp_path)).state(console=False)["leftovers"]

        assert state["asked"] is False
        assert state["count"] == 1
        assert state["bytes"] == 2048
        assert [item["path"] for item in state["files"]] == [path]

    def test_nothing_to_ask_when_there_are_none(self, tmp_path):
        state = App(cwd=str(tmp_path)).state(console=False)["leftovers"]

        assert state["count"] == 0
        assert state["files"] == []

    def test_this_sessions_files_are_not_offered(self, leftover_dir):
        stamp = leftovers.nc.session_info["stamp"]
        _leftover(leftover_dir, f"{stamp}tmpzz.nc")
        earlier = _leftover(leftover_dir, "nctoolkit_me_zzzznctoolkit_oceanval_output_p999999_tmpyy.nc")

        assert [item["path"] for item in leftovers.find_leftovers()] == [earlier]

    def test_files_from_before_the_rename_are_offered(self, leftover_dir):
        legacy = _leftover(leftover_dir, "nctoolkit_me_abcdnctoolkit_ecoval_output_tmp5.nc")

        assert [item["path"] for item in leftovers.find_leftovers()] == [legacy]

    def test_files_of_a_running_session_are_not_offered(self, leftover_dir):
        # another process that is running, as a session in another terminal is
        running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            _leftover(leftover_dir, f"nctoolkit_me_abcdnctoolkit_oceanval_output_p{running.pid}_tmp1.nc")
            finished = _leftover(leftover_dir, "nctoolkit_me_abcdnctoolkit_oceanval_output_p999999_tmp2.nc")

            assert [item["path"] for item in leftovers.find_leftovers()] == [finished]
            # nor can they be removed by asking
            names = [os.path.join(leftover_dir, name) for name in os.listdir(leftover_dir)]
            assert leftovers.remove_leftovers(names)["removed"] == 1
            assert len(os.listdir(leftover_dir)) == 1
        finally:
            running.kill()
            running.wait()

    def test_a_process_that_has_gone_is_not_alive(self):
        assert leftovers.process_alive(os.getpid())
        assert not leftovers.process_alive(DEAD_PID)

    def test_other_users_files_and_links_are_skipped(self, leftover_dir, monkeypatch, tmp_path):
        mine = _leftover(leftover_dir)
        theirs = _leftover(leftover_dir, "nctoolkit_you_abcdnctoolkit_oceanval_output_p999999_tmp9.nc")
        link = leftover_dir / "nctoolkit_me_linknctoolkit_oceanval_output_p999999_tmp8.nc"
        link.symlink_to(mine)
        real_lstat = os.lstat

        def lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if str(path) == theirs:
                return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink,
                                       os.getuid() + 1, info.st_gid, info.st_size,
                                       info.st_atime, info.st_mtime, info.st_ctime))
            return info

        monkeypatch.setattr(leftovers.os, "lstat", lstat)

        assert [item["path"] for item in leftovers.find_leftovers()] == [mine]

    def test_only_found_files_can_be_removed(self, leftover_dir, tmp_path):
        path = _leftover(leftover_dir)
        outside = tmp_path / "precious.nc"
        outside.write_text("keep")

        result = leftovers.remove_leftovers([path, str(outside)])

        assert result == {"removed": 1, "freed_bytes": 2048, "failed": []}
        assert not os.path.exists(path)
        assert outside.exists()

    def test_a_file_that_cannot_be_removed_is_reported(self, leftover_dir, monkeypatch):
        path = _leftover(leftover_dir)

        def refuse(target):
            raise PermissionError(target)

        monkeypatch.setattr(leftovers.os, "remove", refuse)

        result = leftovers.remove_leftovers([path])
        assert result == {"removed": 0, "freed_bytes": 0, "failed": [path]}

    def test_removing_through_the_window(self, leftover_dir, tmp_path):
        # the window scans when it is made, so make one with files in place
        path = _leftover(leftover_dir)
        other = App(cwd=str(tmp_path))
        other.start()
        try:
            status, reply = post(other, "/api/leftovers", {"action": "remove"})
            assert (status, reply["removed"], reply["freed_bytes"]) == (200, 1, 2048)
            assert not os.path.exists(path)
            assert other.state(console=False)["leftovers"]["asked"] is True

            # not asked, or removed, twice
            assert post(other, "/api/leftovers", {"action": "remove"})[0] == 409
        finally:
            other.close()

    def test_keeping_leaves_the_files(self, leftover_dir, tmp_path):
        path = _leftover(leftover_dir)
        other = App(cwd=str(tmp_path))
        other.start()
        try:
            status, reply = post(other, "/api/leftovers", {"action": "keep"})
            assert (status, reply["removed"]) == (200, 0)
            assert os.path.exists(path)
            assert other.state(console=False)["leftovers"]["asked"] is True
        finally:
            other.close()

    def test_the_answer_must_be_remove_or_keep(self, leftover_dir, tmp_path):
        path = _leftover(leftover_dir)
        other = App(cwd=str(tmp_path))
        other.start()
        try:
            assert post(other, "/api/leftovers", {"action": "delete"})[0] == 400
            assert os.path.exists(path)
            assert other.state(console=False)["leftovers"]["asked"] is False
        finally:
            other.close()

    def test_the_question_in_a_browser(self, browser, leftover_dir, tmp_path):
        first = _leftover(leftover_dir)
        second = _leftover(leftover_dir, "nctoolkit_me_wxyznctoolkit_oceanval_output_p999999_tmp2.nc")
        app = App(cwd=str(tmp_path))
        app.choose("validate_menu")
        url = app.start()
        try:
            page = browser.new_page()
            page.goto(url)
            page.wait_for_selector("#leftovers:not([hidden])")
            assert "nobody else is sharing this disk space" in page.text_content("#leftovers-notice")
            assert page.text_content("#leftovers-remove") == "Remove 2 files"

            # the files are listed in a pop-out of their own
            assert page.is_hidden("#leftovers-files")
            page.click("#leftovers-view")
            page.wait_for_selector("#leftovers-files:not([hidden])")
            assert sorted(
                page.eval_on_selector_all(
                    "#leftovers-list .leftover__path", "nodes => nodes.map(n => n.textContent)"
                )
            ) == sorted([first, second])
            # closing it returns to the question, which is still open
            page.keyboard.press("Escape")
            assert page.is_hidden("#leftovers-files")
            assert page.is_visible("#leftovers")
            assert os.path.exists(first)

            # a click outside the box answers nothing
            page.mouse.click(2, 2)
            assert page.is_visible("#leftovers")

            page.click("#leftovers-remove")
            page.wait_for_selector("#leftovers", state="hidden")
            assert not os.path.exists(first) and not os.path.exists(second)
            assert "Removed 2 files" in page.text_content("#toast")

            # reloading does not ask again
            page.reload()
            page.wait_for_selector('button.choice[data-action="matchup"]')
            assert page.is_hidden("#leftovers")
        finally:
            app.close()

    def test_keeping_in_a_browser(self, browser, leftover_dir, tmp_path):
        path = _leftover(leftover_dir)
        app = App(cwd=str(tmp_path))
        app.choose("validate_menu")
        url = app.start()
        try:
            page = browser.new_page()
            page.goto(url)
            page.wait_for_selector("#leftovers:not([hidden])")
            assert page.text_content("#leftovers-remove") == "Remove 1 file"
            page.keyboard.press("Escape")
            page.wait_for_selector("#leftovers", state="hidden")
            assert os.path.exists(path)
            page.click('button.choice[data-action="matchup"]')
        finally:
            app.close()

    def test_no_question_without_files_in_a_browser(self, browser, tmp_path):
        app = App(cwd=str(tmp_path))
        app.choose("validate_menu")
        url = app.start()
        try:
            page = browser.new_page()
            page.goto(url)
            page.wait_for_selector('button.choice[data-action="matchup"]')
            assert page.is_hidden("#leftovers")
        finally:
            app.close()


def test_choosing_a_simulation_matched_up_before_in_a_browser(browser, tmp_path):
    """The arrow inside the simulation directory box lists the simulations
    validated before, and picking one fills the box in. It is not there while
    nothing has been matched up."""
    from oceanval import user_cache

    write_simulation(tmp_path / "sim")
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click('button.choice[data-action="matchup"]')
        assert page.is_hidden("#f-simdir-recent-toggle")

        user_cache.record_sim_dir(str(tmp_path / "sim"))
        # the window is still on the setup step, as the server keeps the view
        page.goto(url)
        assert page.is_visible("#f-simdir-recent-toggle")
        # one box: the list is inside the directory's own
        assert page.locator("#view-setup select").count() == 0
        assert page.is_hidden("#f-simdir-recent")

        page.click("#f-simdir-recent-toggle")
        assert page.is_visible("#f-simdir-recent")
        page.keyboard.press("Escape")
        assert page.is_hidden("#f-simdir-recent")

        page.click("#f-simdir-recent-toggle")
        assert page.locator("#f-simdir-recent .combo__option").all_text_contents() == [
            str(tmp_path / "sim")
        ]
        page.click("#f-simdir-recent .combo__option")
        assert page.is_hidden("#f-simdir-recent")
        assert page.input_value("#f-simdir") == str(tmp_path / "sim")
        # as if it had been typed: the live check is asked (the years wait for the depth)
        page.wait_for_function("!document.querySelector('#ndown-found').classList.contains('is-finding')")

        # and from the keyboard
        page.fill("#f-simdir", "")
        page.keyboard.press("Alt+ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert page.input_value("#f-simdir") == str(tmp_path / "sim")
        assert page.is_hidden("#f-simdir-recent")
    finally:
        app.close()


def _recent_dirs(tmp_path, names):
    """Simulation directories that exist and have been matched up, the first
    named the earliest."""
    from oceanval import user_cache

    for name in names:
        (tmp_path / name).mkdir()
        user_cache.record_sim_dir(str(tmp_path / name))
    return [str(tmp_path / name) for name in reversed(names)]


def _forget_in_a_list(page, list_id, toggle_id, tmp_path):
    """Take the entries of a list of directories used before out with the x,
    then with the Delete key: the list stays open for the next."""
    from oceanval import user_cache

    page.click(toggle_id)
    options = page.locator(f"{list_id} .combo__option")
    # the x is not part of an entry's text
    assert options.all_text_contents() == [str(tmp_path / name) for name in ("c", "b", "a")]
    options.nth(1).locator(".combo__remove").click()
    page.wait_for_function(f"document.querySelectorAll('{list_id} .combo__option').length === 2")
    assert page.is_visible(list_id)
    assert options.all_text_contents() == [str(tmp_path / name) for name in ("c", "a")]
    assert user_cache.recent_sim_dirs() == [str(tmp_path / name) for name in ("c", "a")]

    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Delete")
    page.wait_for_function(f"document.querySelectorAll('{list_id} .combo__option').length === 1")
    assert user_cache.recent_sim_dirs() == [str(tmp_path / "c")]
    # the next entry is marked
    page.wait_for_selector(f"{list_id} .combo__option.is-active")
    assert page.locator(f"{list_id} .combo__option.is-active").all_text_contents() == [str(tmp_path / "c")]

    options.first.locator(".combo__remove").click()
    page.wait_for_function(f"document.querySelectorAll('{list_id} .combo__option').length === 0")
    assert user_cache.recent_sim_dirs() == []
    assert page.is_hidden(toggle_id)
    assert page.is_hidden(list_id)


def test_taking_a_simulation_out_of_the_validate_list_in_a_browser(browser, tmp_path):
    """The x beside a simulation validated before takes it out of the history,
    and does not choose it."""
    _recent_dirs(tmp_path, ["a", "b", "c"])
    app = App(cwd=str(tmp_path))
    app.choose("validate_menu")
    url = app.start()
    errors = []
    try:
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.click('button.choice[data-action="matchup"]')
        page.wait_for_selector("#view-setup:not([hidden])")
        _forget_in_a_list(page, "#f-simdir-recent", "#f-simdir-recent-toggle", tmp_path)
        # none of it chose a directory
        assert page.input_value("#f-simdir") == ""
        assert errors == []
    finally:
        app.close()


def test_taking_a_simulation_out_of_the_analyze_list_in_a_browser(browser, tmp_path):
    _recent_dirs(tmp_path, ["a", "b", "c"])
    app = App(cwd=str(tmp_path))
    url = app.start()
    errors = []
    try:
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.click("#home-analyze")
        page.wait_for_selector("#view-setup:not([hidden])")
        _forget_in_a_list(page, "#f-simdir-recent", "#f-simdir-recent-toggle", tmp_path)
        assert page.input_value("#f-simdir") == ""
        assert errors == []
    finally:
        app.close()


def test_taking_a_simulation_out_of_the_compare_simulations_lists_in_a_browser(browser, tmp_path):
    _recent_dirs(tmp_path, ["a", "b", "c"])
    app = App(cwd=str(tmp_path))
    url = app.start()
    errors = []
    try:
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        page.click("#home-simcompare")
        page.wait_for_selector("#view-simcompare-sims:not([hidden])")
        _forget_in_a_list(page, "#sc-dir_1-recent", "#sc-dir_1-recent-toggle", tmp_path)
        assert page.input_value("#sc-dir_1") == ""
        # and so are the other boxes' lists
        assert page.is_hidden("#sc-dir_2-recent-toggle")
        assert errors == []
    finally:
        app.close()


def test_a_simulation_taken_out_in_one_compare_simulations_box_goes_from_the_others(browser, tmp_path):
    _recent_dirs(tmp_path, ["a", "b"])
    app = App(cwd=str(tmp_path))
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click("#home-simcompare")
        page.wait_for_selector("#view-simcompare-sims:not([hidden])")
        page.click("#sc-dir_1-recent-toggle")
        page.locator("#sc-dir_1-recent .combo__option").first.locator(".combo__remove").click()
        page.wait_for_function(
            "document.querySelectorAll('#sc-dir_2-recent .combo__option').length === 1"
        )
        assert page.locator("#sc-dir_2-recent .combo__option").all_text_contents() == [str(tmp_path / "a")]
    finally:
        app.close()


# the data store settings, as they are without a data store
NO_STORE = {"data_store": "", "data_store_use": "always"}


class TestSettings:
    """The window's settings, saved in the home .oceanvalrc from its panel."""

    def test_the_settings_are_read_as_the_window_opens(self, oceanvalrc, tmp_path):
        oceanvalrc.write_text(json.dumps({"settings": {"show_demo": False}}))
        app = App(cwd=str(tmp_path))
        settings = app.state()["user_settings"]
        assert settings["values"] == {"show_demo": False, **NO_STORE}
        assert settings["path"] == str(oceanvalrc)
        assert "themes" not in settings

    def test_applying_saves_them(self, app, oceanvalrc):
        version = app.version
        status, reply = post(app, "/api/settings", {"settings": {"show_demo": False}})
        assert status == 200
        assert reply["ok"] is True
        assert reply["settings"] == {"show_demo": False, **NO_STORE}
        assert reply["data_store"]["root"] is None
        assert app.state()["user_settings"]["values"] == {"show_demo": False, **NO_STORE}
        assert app.version > version
        assert json.loads(oceanvalrc.read_text())["settings"] == {"show_demo": False}

    def test_settings_that_cannot_be_used_are_refused(self, app, oceanvalrc):
        status, reply = post(app, "/api/settings", {"settings": {"show_demo": "purple"}})
        assert status == 400
        assert not reply["ok"] and "purple" in reply["error"]
        assert app.state()["user_settings"]["values"] == {"show_demo": True, **NO_STORE}
        assert not oceanvalrc.exists()

    def test_applying_needs_the_token(self, app, oceanvalrc):
        assert post(app, "/api/settings", {"settings": {"show_demo": False}}, token="wrong")[0] == 403
        assert not oceanvalrc.exists()

    def test_the_settings_panel_in_a_browser(self, browser, oceanvalrc, tmp_path):
        app = App(cwd=str(tmp_path))
        app.choose("validate_menu")
        url = app.start()
        try:
            page = browser.new_page()
            page.goto(url)
            page.wait_for_selector("#view-start:not([hidden])")
            assert page.is_visible("#choice-demo")

            # Cancel leaves everything as it was
            page.click("#settings-open")
            assert page.is_visible("#preferences")
            assert page.is_checked("#settings-show-demo")
            page.uncheck("#settings-show-demo")
            page.click("#settings-cancel")
            assert page.is_hidden("#preferences")
            assert page.is_visible("#choice-demo")
            assert not oceanvalrc.exists()
            assert page.evaluate("document.activeElement.id") == "settings-open"

            # opened again, it shows what is saved, not what was cancelled
            page.click("#settings-open")
            assert page.is_checked("#settings-show-demo")
            page.keyboard.press("Escape")
            assert page.is_hidden("#preferences")

            page.click("#settings-open")
            page.uncheck("#settings-show-demo")
            page.click("#settings-apply")
            page.wait_for_selector("#preferences", state="hidden")
            assert page.is_hidden("#choice-demo")
            assert json.loads(oceanvalrc.read_text())["settings"] == {"show_demo": False}

            # and are there when the page is opened again
            page.reload()
            page.wait_for_selector("#view-start:not([hidden])")
            assert page.is_hidden("#choice-demo")

            # back to the default
            page.click("#settings-open")
            page.check("#settings-show-demo")
            page.click("#settings-apply")
            page.wait_for_selector("#preferences", state="hidden")
            assert page.is_visible("#choice-demo")
            assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(244, 250, 250)"
        finally:
            app.close()

    def test_a_data_store_is_kept_with_its_folder_made(self, app, oceanvalrc, tmp_path):
        folder = tmp_path / "store"
        folder.mkdir()
        status, reply = post(app, "/api/settings", {"settings": {
            "data_store": str(folder), "data_store_use": "fallback"}})
        assert status == 200
        assert reply["settings"]["data_store"] == str(folder)
        assert reply["settings"]["data_store_use"] == "fallback"
        assert reply["data_store"]["root"] == str(folder / "oceanval_datastore")
        assert reply["data_store"]["size"] == 0
        assert reply["data_store"]["free"] > 0
        assert (folder / "oceanval_datastore").is_dir()
        info = app.state()["user_settings"]["data_store"]
        assert info["root"] == str(folder / "oceanval_datastore")
        assert [x[:2] for x in info["uses"]] == [
            ["always", "Always use data from your data store, if available"],
            ["fallback", "Only use the data store when download fails"],
        ]

    @pytest.mark.parametrize("folder, error", [("data", "full path"), ("/no/such/folder", "does not exist")])
    def test_a_data_store_folder_that_cannot_be_used_is_refused(self, app, oceanvalrc, folder, error):
        status, reply = post(app, "/api/settings", {"settings": {"data_store": folder}})
        assert status == 400
        assert error in reply["error"]
        assert app.state()["user_settings"]["values"]["data_store"] == ""
        assert not oceanvalrc.exists()

    def test_the_data_store_in_the_settings_panel(self, browser, oceanvalrc, tmp_path):
        folder = tmp_path / "store"
        folder.mkdir()
        app = App(cwd=str(tmp_path))
        app.choose("validate_menu")
        url = app.start()
        try:
            page = browser.new_page()
            page.goto(url)
            page.wait_for_selector("#view-start:not([hidden])")
            page.click("#settings-open")
            assert page.input_value("#settings-data-store") == ""
            assert "No data store" in page.text_content("#settings-data-store-status")
            assert not page.is_visible("#settings-data-store-size")
            uses = page.locator('input[name="settings-data-store-use"]')
            assert uses.count() == 2
            # how it is used cannot be chosen without a folder
            assert all(uses.nth(i).is_disabled() for i in range(2))
            assert page.is_checked('input[name="settings-data-store-use"][value="always"]')
            labels = page.locator("#settings-data-store-uses .prefs__use").all_text_contents()
            assert labels[0].startswith("Always use data from your data store, if available")
            assert labels[1].startswith("Only use the data store when download fails")
            assert "reasonable amount of free space" in page.text_content("#settings-data-store-note")

            # a folder that is not a full path is refused, and said why
            page.fill("#settings-data-store", "store")
            assert not uses.nth(0).is_disabled()
            assert "store/oceanval_datastore" in page.text_content("#settings-data-store-status")
            # a folder not applied yet has no size to show
            assert not page.is_visible("#settings-data-store-size")
            page.click("#settings-apply")
            page.wait_for_selector("#settings-error:not([hidden])")
            assert "full path" in page.text_content("#settings-error")
            assert page.is_visible("#preferences")
            assert not oceanvalrc.exists()

            page.fill("#settings-data-store", str(folder))
            page.check('input[name="settings-data-store-use"][value="fallback"]')
            page.click("#settings-apply")
            page.wait_for_selector("#preferences", state="hidden")
            saved = json.loads(oceanvalrc.read_text())["settings"]
            assert saved["data_store"] == str(folder)
            assert saved["data_store_use"] == "fallback"
            assert (folder / "oceanval_datastore").is_dir()

            # opened again, it says where the store is and how much it holds
            page.click("#settings-open")
            assert page.input_value("#settings-data-store") == str(folder)
            assert page.is_checked('input[name="settings-data-store-use"][value="fallback"]')
            status = page.text_content("#settings-data-store-status")
            assert f"Kept in {folder}/oceanval_datastore." in status
            assert "free on that disk" in status
            # and its size, on a line of its own
            assert page.is_visible("#settings-data-store-size")
            assert page.text_content("#settings-data-store-size") == "Size of the data store: 0 bytes"

            # emptied, there is no data store
            page.fill("#settings-data-store", "")
            assert uses.nth(1).is_disabled()
            page.click("#settings-apply")
            page.wait_for_selector("#preferences", state="hidden")
            assert json.loads(oceanvalrc.read_text())["settings"]["data_store"] == ""
            # and what it held is left as it was
            assert (folder / "oceanval_datastore").is_dir()
        finally:
            app.close()

    def test_the_data_store_check_in_the_settings_panel(self, browser, oceanvalrc, tmp_path):
        folder = tmp_path / "store"
        folder.mkdir()
        app = App(cwd=str(tmp_path))
        app.choose("validate_menu")
        url = app.start()
        try:
            page = browser.new_page()
            page.goto(url)
            page.wait_for_selector("#view-start:not([hidden])")
            page.click("#settings-open")
            button = page.locator("#settings-data-store-check")
            assert button.text_content() == "Run a check on the data store"
            # nothing to check until a folder is saved
            assert button.is_disabled()
            page.fill("#settings-data-store", str(folder))
            assert button.is_disabled()
            assert "Apply a folder first" in page.text_content("#settings-data-store-check-hint")
            page.click("#settings-apply")
            page.wait_for_selector("#preferences", state="hidden")

            page.click("#settings-open")
            assert not button.is_disabled()
            assert "nothing is changed, removed or fixed" in page.text_content("#settings-data-store-check-hint")
            assert page.is_hidden("#settings-data-store-result")
            button.click()
            page.wait_for_selector("#settings-data-store-result .check__summary")
            assert "No problems found." in page.text_content("#settings-data-store-result")
            assert "Nothing was changed." in page.text_content("#settings-data-store-result")

            # something of yours in the store, and a piece that has gone, are found and left as they are
            store = folder / "oceanval_datastore"
            (store / "mine.txt").write_text("mine")
            (store / "woa23").mkdir()
            piece = "a" * 32 + ".nc"
            (store / "woa23" / piece).write_text("data")
            # not young enough to be one a run is writing
            os.utime(store / "woa23" / piece, (1_600_000_000, 1_600_000_000))
            (store / "oceanval_data_store.json").write_text(json.dumps({"version": 1, "entries": [
                {"id": "a" * 32, "kind": "gridded", "dataset": "woa23", "variable": "n_an",
                 "url": "https://www.ncei.noaa.gov/thredds-ocean/dodsC/woa23/DATA/nitrate/netcdf/all/1.00/woa23_all_n01_01.nc",
                 "year": None, "levels": "top", "box": [-180, 180, -90, 90], "file": f"woa23/{'b' * 32}.nc", "bytes": 4},
            ], "retired": []}))
            before = {x.name: x.read_bytes() for x in store.iterdir() if x.is_file()}
            button.click()
            page.wait_for_selector("#settings-data-store-result .check__group")
            found = page.text_content("#settings-data-store-result")
            assert "2 problems found" in found
            assert "mine.txt" in found
            assert f"woa23/{'b' * 32}.nc is not in the data store" in found
            assert "1 thing to know about" in found
            assert piece not in page.locator(".check__group--problem").all_text_contents()
            assert {x.name: x.read_bytes() for x in store.iterdir() if x.is_file()} == before
            assert (store / "woa23" / piece).read_text() == "data"

            # a folder typed in is no longer the one that was checked
            page.fill("#settings-data-store", str(tmp_path))
            assert button.is_disabled() and page.is_hidden("#settings-data-store-result")
        finally:
            app.close()

    def test_the_data_store_check_is_of_the_folder_saved(self, app, oceanvalrc, tmp_path):
        status, reply = post(app, "/api/data_store_check")
        assert status == 400 and "no data store" in reply["error"]
        folder = tmp_path / "store"
        folder.mkdir()
        post(app, "/api/settings", {"settings": {"data_store": str(folder)}})
        # a folder in the request is ignored
        status, reply = post(app, "/api/data_store_check", {"data_store": "/", "folder": "/"})
        assert status == 200
        assert reply["check"]["groups"] == [] and reply["check"]["pieces"] == 0

    def test_a_settings_problem_is_shown_in_the_panel(self, browser, oceanvalrc, tmp_path):
        app = App(cwd=str(tmp_path))
        app.choose("validate_menu")
        url = app.start()
        try:
            page = browser.new_page()
            page.goto(url)
            page.wait_for_selector("#view-start:not([hidden])")
            # a file that cannot be read is not written over
            oceanvalrc.write_text("{ not json")
            page.click("#settings-open")
            page.uncheck("#settings-show-demo")
            page.click("#settings-apply")
            page.wait_for_selector("#settings-error:not([hidden])")
            assert "could not be read" in page.text_content("#settings-error")
            assert page.is_visible("#preferences")
            assert oceanvalrc.read_text() == "{ not json"
        finally:
            app.close()


class TestInfo:
    """The info panel: the website, GitHub and the built-in datasets."""

    def test_the_state_lists_the_built_in_datasets(self, app):
        from oceanval.create_recipes import POINT_RECIPE_CATALOGUE, RECIPE_CATALOGUE

        rows = app.state()["datasets"]
        assert [row["point"] for row in rows].count(False) == len(RECIPE_CATALOGUE)
        assert [row["point"] for row in rows].count(True) == len(POINT_RECIPE_CATALOGUE)
        for row in rows:
            assert row["variable"] and row["recipe"] and row["dataset"] and row["units"]
        woa = next(
            r for r in rows if (r["variable"], r["recipe"]) == ("Nitrate concentration in sea water", "woa23")
        )
        assert woa["dataset"] == "World Ocean Atlas 2023" and woa["water_column"]
        assert woa["url"].startswith("https://")
        glodap = next(
            r for r in rows if r["recipe"] == "glodap" and r["variable"] == "Total alkalinity of sea water"
        )
        assert not glodap["water_column"] and glodap["region"] == "Global"
        nsbc = next(r for r in rows if r["recipe"] == "nsbc")
        assert nsbc["dataset"] == "North Sea Biogeochemical Climatology"
        assert nsbc["url"] == "https://www.cen.uni-hamburg.de/en/icdc/data/ocean/nsbc.html"
        ices = next(r for r in rows if r["recipe"] == "ices")
        assert ices["url"] == "https://www.ices.dk/data/data-portals/Pages/ocean.aspx"
        assert all(r["url"] for r in rows)
        assert {r["region"] for r in rows if r["point"]} == {"Northeast Atlantic"}

    def test_the_version_is_the_packages(self, app):
        import oceanval

        assert app.state()["oceanval_version"] == oceanval.__version__

    def test_the_users_own_recipes_are_left_out(self, tmp_path):
        from oceanval import user_recipes

        before = App(cwd=str(tmp_path)).state()["datasets"]
        user_recipes.save(
            dict(variable="nitrate", kind="gridded", source="MySat", source_info="My nitrate product",
                 location="disk", obs_path="/x/n.nc", obs_variable="N3_n", climatology=True,
                 units="mmol N/m^3"),
            "local", str(tmp_path),
        )
        app = App(cwd=str(tmp_path))
        assert [e for e in user_recipes.load(str(tmp_path))]
        assert app.state()["datasets"] == before
        assert "MySat" not in json.dumps(app.state()["datasets"])

    def test_the_restart_button_in_a_browser(self, browser, tmp_path):
        app = App(cwd=str(tmp_path))
        app.choose("validate_menu")
        url = app.start()
        try:
            page = browser.new_page()
            page.goto(url)
            page.wait_for_selector("#view-start:not([hidden])")
            # beside Info, in the header
            ids = page.evaluate("[...document.querySelector('.brand').querySelectorAll('button')].map(b => b.id)")
            assert ids[ids.index("info-open") + 1] == "restart-open"
            assert page.is_hidden("#restart-confirm")
            page.click("#restart-open")
            assert page.is_visible("#restart-confirm")
            assert page.text_content("#restart-title") == "Begin again with a clean slate"
            assert page.locator("#restart-confirm .restart__body *").count() == 1
            assert page.evaluate("document.activeElement.id") == "restart-cancel"
            # Cancel and Escape change nothing
            page.click("#restart-cancel")
            assert page.is_hidden("#restart-confirm")
            assert app.view == "start"
            page.click("#restart-open")
            page.keyboard.press("Escape")
            assert page.is_hidden("#restart-confirm")
            assert app.view == "start"
            # Restart goes back to the first page
            page.click("#restart-open")
            page.click("#restart-go")
            page.wait_for_selector("#view-home:not([hidden])")
            assert app.view == "home"
            assert page.is_hidden("#restart-confirm")
        finally:
            app.close()

    def test_the_info_panel_in_a_browser(self, browser, tmp_path):
        app = App(cwd=str(tmp_path))
        app.choose("validate_menu")
        url = app.start()
        try:
            page = browser.new_page()
            page.goto(url)
            page.wait_for_selector("#view-start:not([hidden])")
            assert page.is_hidden("#info")
            page.click("#info-open")
            assert page.is_visible("#info")
            assert page.text_content("#info-version") == f"Version {app.state()['oceanval_version']}"
            assert page.get_attribute("#info-website", "href") == "https://PlymouthMarineLaboratory.github.io/OceanVal/"
            assert page.get_attribute("#info-github", "href") == "https://github.com/PlymouthMarineLaboratory/oceanVal"
            assert page.locator("#info-gridded tbody tr").count() == 19
            assert page.locator("#info-point tbody tr").count() == 10
            assert page.locator("#info-gridded tbody tr", has_text="GLODAPv2.2016b").first.is_visible()
            page.keyboard.press("Escape")
            assert page.is_hidden("#info")
            assert page.evaluate("document.activeElement.id") == "info-open"
            page.click("#info-open")
            page.click("#info-close")
            assert page.is_hidden("#info")
        finally:
            app.close()
