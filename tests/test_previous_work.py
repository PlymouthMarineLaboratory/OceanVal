"""View previous work: the history of reports the user chose to keep (oceanval.user_cache's
``history``), asked for at the end of Analyze, Compare simulations, Validate and Compare
validations (oceanval.history_forms), listed by Tools & info's View previous work pages and
opened in the window."""

import json
import os

import pytest

from oceanval import analysis_forms, history_forms, sim_compare_forms, user_cache
from oceanval.app import App, check_compare, check_report
from simulations import write_analysis_simulation
from test_app import get, get_json, leftover_dir, post  # noqa: F401 (leftover_dir is autouse)


def make_report(folder, kind="analysis", page="Overview"):
    """A report like the ones OceanVal builds: pages below report/_build/html and a link to the
    first, as analyze() makes. Returns (the link, the first page)."""
    html = os.path.join(str(folder), "oceanval_analysis", "report", "_build", "html", "notebooks")
    os.makedirs(html)
    landing = os.path.join(html, "overview.html")
    with open(landing, "w") as file:
        file.write(f"<html><body><h1>{page}</h1><a href='second.html'>next</a></body></html>")
    with open(os.path.join(html, "second.html"), "w") as file:
        file.write("<html><body>second</body></html>")
    link = os.path.join(str(folder), "oceanval_analysis", "report.html")
    os.symlink(os.path.relpath(landing, os.path.dirname(link)), link)
    return link, landing


def keep(tmp_path, name, kind="analysis", folder=None):
    folder = folder or tmp_path / name.replace(" ", "_")
    link, landing = make_report(folder)
    assert user_cache.record_history(kind, name, link, landing) == (True, "")
    return link, landing


class TestTheHistoryInTheCache:
    def test_it_starts_empty_and_creates_nothing(self, oceanvalcache):
        assert user_cache.history() == []
        assert not oceanvalcache.exists()

    def test_work_is_kept_newest_first_with_its_report(self, oceanvalcache, tmp_path):
        first = keep(tmp_path, "First")
        keep(tmp_path, "Second")
        found = user_cache.history()
        assert [entry["name"] for entry in found] == ["Second", "First"]
        assert found[1]["report"] == first[0]
        assert found[1]["landing"] == os.path.realpath(first[1])
        # the pages are served from the html folder above the first page
        assert found[1]["folders"] == [os.path.dirname(os.path.dirname(os.path.realpath(first[1])))]

    def test_a_name_is_unique_within_its_kind(self, oceanvalcache, tmp_path):
        keep(tmp_path, "Baseline", "analysis")
        link, landing = make_report(tmp_path / "other")
        added, why = user_cache.record_history("analysis", "baseline", link, landing)
        assert not added and "'Baseline' is already used" in why
        # another kind may use it
        assert user_cache.record_history("validation", "baseline", link, landing)[0]
        assert [entry["kind"] for entry in user_cache.history("validation")] == ["validation"]

    def test_running_the_same_work_again_keeps_one_entry(self, oceanvalcache, tmp_path):
        link, landing = keep(tmp_path, "Baseline")
        assert user_cache.history_problem("analysis", "Baseline", link) == ""
        assert user_cache.record_history("analysis", "Renamed", link, landing)[0]
        assert [entry["name"] for entry in user_cache.history()] == ["Renamed"]

    def test_a_name_whose_report_has_gone_is_free(self, oceanvalcache, tmp_path):
        link, landing = keep(tmp_path, "Baseline")
        os.remove(link)
        assert user_cache.history() == []
        link2, landing2 = make_report(tmp_path / "again")
        assert user_cache.record_history("analysis", "Baseline", link2, landing2)[0]
        # and the stale entry went with it
        data = json.loads(oceanvalcache.read_text())
        assert [entry["report"] for entry in data["history"]] == [link2]

    def test_a_deleted_report_is_hidden_but_not_forgotten(self, oceanvalcache, tmp_path):
        link, _ = keep(tmp_path, "Baseline")
        os.rename(link, link + ".away")
        assert user_cache.history() == []
        assert len(json.loads(oceanvalcache.read_text())["history"]) == 1
        os.rename(link + ".away", link)
        assert len(user_cache.history()) == 1

    def test_removing_work_removes_the_entry_and_no_file(self, oceanvalcache, tmp_path):
        link, landing = keep(tmp_path, "Baseline")
        keep(tmp_path, "Other")
        entry = user_cache.history("analysis")[1]
        assert user_cache.remove_history("analysis", entry["id"])
        assert [e["name"] for e in user_cache.history()] == ["Other"]
        assert os.path.exists(landing) and os.path.islink(link)
        # removing what is not there is not an error
        assert user_cache.remove_history("analysis", entry["id"])

    def test_names_are_checked(self, oceanvalcache):
        assert "Give it a name" in user_cache.history_problem("analysis", "   ")
        assert "at most" in user_cache.history_problem("analysis", "x" * 81)
        assert user_cache.history_problem("analysis", "x" * 80) == ""
        assert user_cache.clean_history_name("  a \n  b ") == "a b"
        assert user_cache.history_problem("nonsense", "a")

    def test_entries_of_a_newer_oceanval_survive(self, oceanvalcache, tmp_path):
        oceanvalcache.write_text(json.dumps({"version": 1, "history": [{"kind": "hologram", "name": "x", "report": "/y"}]}))
        keep(tmp_path, "Baseline")
        kept = json.loads(oceanvalcache.read_text())["history"]
        assert {"kind": "hologram", "name": "x", "report": "/y"} in kept
        assert len(kept) == 2

    def test_other_parts_of_the_file_are_kept(self, oceanvalcache, tmp_path):
        user_cache.add_to_bank("Control", str(tmp_path))
        keep(tmp_path, "Baseline")
        data = json.loads(oceanvalcache.read_text())
        assert data["bank"][0]["name"] == "Control" and len(data["history"]) == 1

    def test_a_file_that_cannot_be_used_is_left_alone(self, oceanvalcache, tmp_path):
        oceanvalcache.write_text(json.dumps({"version": 1, "history": "no"}))
        link, landing = make_report(tmp_path / "r")
        assert not user_cache.record_history("analysis", "Baseline", link, landing)[0]
        assert json.loads(oceanvalcache.read_text()) == {"version": 1, "history": "no"}
        oceanvalcache.write_text(json.dumps({"version": 99}))
        assert not user_cache.record_history("analysis", "Baseline", link, landing)[0]
        assert user_cache.history() == []

    def test_the_html_folder_is_found(self, tmp_path):
        link, landing = make_report(tmp_path)
        assert user_cache.history_folders(link) == [os.path.dirname(os.path.dirname(os.path.realpath(landing)))]
        assert user_cache.history_folders(str(tmp_path / "x.html")) == []

    def test_keep_work_says_so_and_never_raises(self, oceanvalcache, tmp_path, capsys):
        link, landing = make_report(tmp_path)
        user_cache.keep_work("analysis", None, link, landing)
        assert user_cache.history() == [] and capsys.readouterr().out == ""
        user_cache.keep_work("analysis", "Baseline", link, landing)
        assert "added to your history as 'Baseline'" in capsys.readouterr().out
        # a name that is taken is said, not raised
        user_cache.keep_work("analysis", "baseline", str(tmp_path / "x"), str(tmp_path / "x"))
        assert "was not added to your history" in capsys.readouterr().out

    def test_a_taken_name_is_refused_before_a_run(self, oceanvalcache, tmp_path):
        keep(tmp_path, "Baseline")
        with pytest.raises(ValueError, match="name: .*already used"):
            user_cache.require_free_history_name("analysis", "Baseline", str(tmp_path / "elsewhere.html"))
        user_cache.require_free_history_name("analysis", None, "x")
        with pytest.raises(ValueError, match="must be text"):
            user_cache.require_free_history_name("analysis", 3, "x")


class TestTheFunctionsTakeAName:
    def test_analyze_refuses_a_taken_name_before_any_work(self, oceanvalcache, tmp_path):
        import oceanval

        keep(tmp_path, "Baseline")
        write_analysis_simulation(str(tmp_path / "sim"), years=(2013, 2014), daily=False)
        with pytest.raises(ValueError, match="name: .*Baseline"):
            oceanval.analyze(
                sim_dir=str(tmp_path / "sim"), variables={"T": "thetao"}, n_dirs_down=1, cores=1,
                climatology={"T": {"start": 2013, "end": 2014, "surface": True}},
                out_dir=str(tmp_path / "out"), view=False, name="baseline",
            )
        assert not (tmp_path / "out").exists()

    def test_compare_refuses_a_taken_name_before_any_work(self, oceanvalcache, tmp_path):
        import oceanval

        keep(tmp_path, "Baseline", "comparison")
        for name in ("a", "b"):
            (tmp_path / name).mkdir()
        with pytest.raises(ValueError, match="name: .*Baseline"):
            oceanval.compare(
                {"a": str(tmp_path / "a"), "b": str(tmp_path / "b")}, view=False,
                out_dir=str(tmp_path / "out"), name="Baseline",
            )
        assert not (tmp_path / "out").exists()

    def test_every_function_has_the_argument_and_it_defaults_to_none(self):
        import inspect

        import oceanval

        for function in (oceanval.analyze, oceanval.compare_simulations, oceanval.validate, oceanval.compare):
            parameter = inspect.signature(function).parameters["name"]
            assert parameter.default is None


class TestTheQuestion:
    def test_nothing_is_chosen_to_start(self):
        assert history_forms.defaults() == {"add_to_history": "", "work_name": "", "overwrite_history": False}
        name, errors = history_forms.check({}, history_forms.ANALYSIS)
        assert name is None and errors == {"add_to_history": history_forms.ASK}

    def test_no_keeps_nothing_and_needs_no_name(self):
        assert history_forms.check({"add_to_history": "no", "work_name": "ignored"}, "analysis") == (None, {})

    def test_yes_needs_a_name(self, oceanvalcache):
        name, errors = history_forms.check({"add_to_history": "yes", "work_name": "  "}, "analysis")
        assert name is None and "work_name" in errors
        assert history_forms.check({"add_to_history": "yes", "work_name": " My  run "}, "analysis") == ("My run", {})

    def test_a_name_in_use_is_refused(self, oceanvalcache, tmp_path):
        keep(tmp_path, "Baseline", folder=tmp_path / "here")
        form = {"add_to_history": "yes", "work_name": "Baseline"}
        assert "work_name" in history_forms.check(form, "analysis", str(tmp_path / "elsewhere"))[1]
        # a validation may use the name
        assert history_forms.check(form, "validation", str(tmp_path / "elsewhere"))[1] == {}

    def test_work_in_the_history_is_not_replaced_unless_the_user_agrees(self, oceanvalcache, tmp_path):
        keep(tmp_path, "Baseline", folder=tmp_path / "here")
        form = {"add_to_history": "yes", "work_name": "Baseline"}
        # the analysis of this directory is that one: say so, with its name
        assert history_forms.check(form, "analysis", str(tmp_path / "here"))[1] == {"overwrite_history": "Baseline"}
        # even if this one is not to be kept
        assert history_forms.check({"add_to_history": "no"}, "analysis", str(tmp_path / "here"))[1] == {"overwrite_history": "Baseline"}
        # agreed: the name may be used again, or changed
        agreed = dict(form, overwrite_history=True)
        assert history_forms.check(agreed, "analysis", str(tmp_path / "here")) == ("Baseline", {})
        assert history_forms.check(dict(agreed, work_name="New"), "analysis", str(tmp_path / "here")) == ("New", {})
        # nothing has been removed by asking
        assert len(user_cache.history()) == 1
        # other directories and other kinds are not asked about
        assert history_forms.check(form, "validation", str(tmp_path / "here"))[1] == {}

    def test_forgetting_what_a_run_replaces_removes_only_that(self, oceanvalcache, tmp_path):
        link, landing = keep(tmp_path, "Baseline", folder=tmp_path / "here")
        keep(tmp_path, "Other")
        history_forms.forget_overwritten("analysis", str(tmp_path / "there"))
        assert len(user_cache.history()) == 2
        history_forms.forget_overwritten("analysis", str(tmp_path / "here"))
        assert [entry["name"] for entry in user_cache.history()] == ["Other"]
        assert os.path.exists(landing)

    def test_the_report_is_where_the_function_puts_it(self, tmp_path):
        for kind, tail in (
            ("analysis", "oceanval_analysis/report.html"),
            ("simcompare", "oceanval_simulation_comparison/report.html"),
            ("validation", "oceanval_report.html"),
            ("comparison", "oceanval_comparison/compare/_build/html/notebooks/comparison_seasonal.html"),
        ):
            assert history_forms.report_path(kind, str(tmp_path)) == os.path.join(str(tmp_path), *tail.split("/"))

    def test_analyze_and_compare_check_it_with_the_output(self, oceanvalcache, tmp_path):
        form = dict(analysis_forms.default_setup_form(str(tmp_path)), out_dir=str(tmp_path), out="a.py")
        arguments, errors = analysis_forms.check_output(form, str(tmp_path))
        assert errors == {"add_to_history": history_forms.ASK} and arguments["history_name"] is None
        arguments, errors = analysis_forms.check_output(dict(form, add_to_history="yes", work_name="Run"), str(tmp_path))
        assert errors == {} and arguments["history_name"] == "Run"
        arguments, errors = analysis_forms.check_output(dict(form, add_to_history="no"), str(tmp_path))
        assert errors == {} and arguments["history_name"] is None
        # a comparison's names are its own
        user_cache.record_history("simcompare", "Run", *make_report(tmp_path / "r"))
        sc = sim_compare_forms.clean_output_form(dict(form, add_to_history="yes", work_name="Run"), str(tmp_path))
        assert "work_name" in sim_compare_forms.check_output(sc, str(tmp_path))[1]
        assert "add_to_history" in sim_compare_forms.check_output(
            sim_compare_forms.default_output_form(str(tmp_path)), str(tmp_path))[1]

    def test_the_name_is_written_into_the_script_only_for_yes(self):
        setup = {"simdir": "/s", "ndown": 1, "out_dir": "/o"}
        placed = [{"name": "T", "model_variable": "t"}]
        common = dict(climatology={"T": {"start": 1, "end": 2, "surface": True}}, phenology=None, thickness=None, time_shift=None, cores=1)
        assert "name" not in analysis_forms.arguments(setup, placed, **common)
        assert analysis_forms.arguments(setup, placed, history_name="Run", **common)["name"] == "Run"
        assert "name='Run'" in analysis_forms.script_text(analysis_forms.arguments(setup, placed, history_name="Run", **common)).replace('"', "'")

    def test_validate_and_compare_validations_check_it(self, oceanvalcache, tmp_path):
        assert check_report({}, str(tmp_path))[1] == {"add_to_history": history_forms.ASK}
        arguments, errors = check_report({"add_to_history": "yes", "work_name": "V1"}, str(tmp_path), str(tmp_path))
        assert errors == {} and arguments["name"] == "V1"
        arguments, errors = check_report({"add_to_history": "no"}, str(tmp_path))
        assert errors == {} and "name" not in arguments
        assert "add_to_history" in check_compare({}, str(tmp_path))[1]

    def test_the_name_is_not_an_interim_option(self):
        from oceanval import live

        assert "name" in live.NOT_INTERIM


@pytest.fixture
def app(tmp_path):
    app = App(cwd=str(tmp_path))
    app.start()
    yield app
    app.close()


class TestTheWindow:
    def test_the_pages_are_reached_from_help_and_back(self, app, oceanvalcache, tmp_path):
        keep(tmp_path, "Baseline")
        app.choose("help")
        assert app.choose("previous_work")
        assert app.view == "previous_work"
        state = get_json(app, "/api/state")[1]["previous"]
        assert state["counts"] == {"analysis": 1, "simcompare": 0, "validation": 0, "comparison": 0}
        assert post(app, "/api/previous_open", {"kind": "analysis"})[1]["ok"]
        assert app.view == "previous_list"
        entries = get_json(app, "/api/state")[1]["previous"]["entries"]
        assert [entry["name"] for entry in entries] == ["Baseline"]
        assert post(app, "/api/previous_view", {"id": entries[0]["id"]})[1]["ok"]
        assert app.view == "previous_report"
        viewing = get_json(app, "/api/state")[1]["previous"]["viewing"]
        assert viewing["name"] == "Baseline" and viewing["href"].startswith(f"previous/{app.token}/{entries[0]['id']}/")
        assert app.back() and app.view == "previous_list"
        assert app.back() and app.view == "previous_work"
        assert app.back() and app.view == "help"

    def test_it_is_reached_only_from_help(self, app):
        assert not app.choose("previous_work")
        assert app.view == "home"
        assert post(app, "/api/previous_open", {"kind": "analysis"})[0] == 409
        assert post(app, "/api/previous_open", {"kind": "nonsense"})[0] == 400

    def test_the_report_is_served_inside_the_window(self, app, oceanvalcache, tmp_path):
        keep(tmp_path, "Baseline")
        app.choose("help")
        app.choose("previous_work")
        post(app, "/api/previous_open", {"kind": "analysis"})
        entry = get_json(app, "/api/state")[1]["previous"]["entries"][0]
        status, body = get(app, "/" + entry["href"])
        assert status == 200 and "<h1>Overview</h1>" in body
        # a page the report links to, by a relative path
        status, body = get(app, "/" + entry["href"].rsplit("/", 1)[0] + "/second.html")
        assert status == 200 and "second" in body

    def test_only_the_recorded_report_is_served(self, app, oceanvalcache, tmp_path):
        keep(tmp_path, "Baseline")
        (tmp_path / "secret.txt").write_text("secret")
        entry = user_cache.history()[0]
        base = f"/previous/{app.token}/{entry['id']}/"
        assert get(app, base + "notebooks/overview.html")[0] == 200
        assert get(app, base + "../../../../../../secret.txt")[0] == 404
        assert get(app, base + "%2e%2e/%2e%2e/%2e%2e/%2e%2e/%2e%2e/%2e%2e/secret.txt")[0] == 404
        assert get(app, f"/previous/{app.token}/nonsense/notebooks/overview.html")[0] == 404
        assert get(app, f"/previous/wrong/{entry['id']}/notebooks/overview.html")[0] == 403

    def test_a_link_out_of_the_report_is_refused(self, app, oceanvalcache, tmp_path):
        link, landing = keep(tmp_path, "Baseline")
        outside = tmp_path / "outside.html"
        outside.write_text("outside")
        os.symlink(str(outside), os.path.join(os.path.dirname(landing), "leak.html"))
        entry = user_cache.history()[0]
        assert get(app, f"/previous/{app.token}/{entry['id']}/notebooks/leak.html")[0] == 404

    def test_a_comparison_may_open_the_validations_it_links_to(self, app, oceanvalcache, tmp_path):
        folder = tmp_path / "c" / "oceanval_comparison" / "compare" / "_build" / "html" / "notebooks"
        folder.mkdir(parents=True)
        (folder / "comparison_seasonal.html").write_text("comparison")
        validation = tmp_path / "v" / "oceanval_report" / "_build" / "html" / "notebooks"
        validation.mkdir(parents=True)
        (validation / "summary.html").write_text("validation")
        page = str(folder / "comparison_seasonal.html")
        assert user_cache.record_history(
            "comparison", "Both", page, page,
            user_cache.history_folders(page) + [str(validation.parent)],
        )[0]
        entry = user_cache.history("comparison")[0]
        listed = app._previous_href(entry)
        assert listed.endswith("/c/oceanval_comparison/compare/_build/html/notebooks/comparison_seasonal.html") or "comparison_seasonal.html" in listed
        assert get(app, "/" + listed)[1] == "comparison"
        root = os.path.commonpath([os.path.realpath(str(tmp_path / "c")), os.path.realpath(str(tmp_path / "v"))])
        assert app.previous_target(entry["id"])[0] == root
        relative = os.path.relpath(str(validation / "summary.html"), root)
        assert get(app, f"/previous/{app.token}/{entry['id']}/{relative}")[1] == "validation"

    def test_removing_takes_only_the_entry(self, app, oceanvalcache, tmp_path):
        link, landing = keep(tmp_path, "Baseline")
        app.choose("help")
        app.choose("previous_work")
        post(app, "/api/previous_open", {"kind": "analysis"})
        entry = get_json(app, "/api/state")[1]["previous"]["entries"][0]
        assert post(app, "/api/previous_remove", {"kind": "analysis", "id": entry["id"]})[1]["ok"]
        assert get_json(app, "/api/state")[1]["previous"]["entries"] == []
        assert os.path.exists(landing)
        assert post(app, "/api/previous_remove", {"kind": "analysis"})[0] == 400

    def test_work_whose_files_are_deleted_is_not_listed(self, app, oceanvalcache, tmp_path):
        link, landing = keep(tmp_path, "Baseline")
        keep(tmp_path, "Kept")
        app.choose("help")
        app.choose("previous_work")
        os.remove(link)
        post(app, "/api/previous_open", {"kind": "analysis"})
        assert [e["name"] for e in get_json(app, "/api/state")[1]["previous"]["entries"]] == ["Kept"]

    def test_a_report_deleted_after_the_list_is_said_to_be_gone(self, app, oceanvalcache, tmp_path):
        link, landing = keep(tmp_path, "Baseline")
        app.choose("help")
        app.choose("previous_work")
        post(app, "/api/previous_open", {"kind": "analysis"})
        entry = get_json(app, "/api/state")[1]["previous"]["entries"][0]
        os.remove(link)
        reply = post(app, "/api/previous_view", {"id": entry["id"]})[1]
        assert not reply["ok"] and "no longer there" in reply["error"]
        assert app.view == "previous_list"

    def test_restarting_forgets_where_it_was(self, app, oceanvalcache, tmp_path):
        keep(tmp_path, "Baseline")
        app.choose("help")
        app.choose("previous_work")
        post(app, "/api/previous_open", {"kind": "analysis"})
        app.reset()
        assert app.view == "home" and get_json(app, "/api/state")[1]["previous"] is None


def test_view_previous_work_in_a_browser(browser, tmp_path, oceanvalcache):
    keep(tmp_path, "Baseline run")
    keep(tmp_path, "Another one")
    gone, _ = keep(tmp_path, "Deleted run")
    os.remove(gone)
    app = App(cwd=str(tmp_path))
    url = app.start()
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    try:
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-help")
        page.wait_for_selector("#view-help:not([hidden])")
        # three across, then two: About and this are the second row
        assert page.locator("#view-help .choice").count() == 5
        top = page.locator("#help-about").bounding_box()
        last = page.locator("#help-history").bounding_box()
        # (a tile under the pointer may be a fraction of a pixel up)
        assert abs(last["y"] - top["y"]) < 2 and last["x"] > top["x"]
        assert top["y"] > page.locator("#help-bank").bounding_box()["y"]
        page.click("#help-history")
        page.wait_for_selector("#view-previous-work:not([hidden])")
        assert page.locator("#view-previous-work .choice").count() == 4
        assert "2 reports" in page.inner_text("#history-count-analysis")
        assert page.inner_text("#history-count-validation") == "Nothing yet"
        # a kind with nothing in it says how to get something
        page.click("#history-validation")
        page.wait_for_selector("#view-previous-list:not([hidden])")
        assert page.is_visible("#history-empty")
        page.click("#back-open")
        page.wait_for_selector("#view-previous-work:not([hidden])")
        page.click("#history-analysis")
        page.wait_for_selector("#history-rows tr")
        assert page.locator("#history-rows tr").count() == 2
        assert "Deleted run" not in page.inner_text("#history-rows")
        # the report opens in the window, below the header, and Back and Restart are there
        page.click("text=Baseline run")
        page.wait_for_selector("#view-previous-report:not([hidden])")
        frame = page.frame_locator("#history-frame")
        frame.locator("h1").wait_for()
        assert frame.locator("h1").inner_text() == "Overview"
        header = page.locator(".masthead").bounding_box()
        shown = page.locator("#history-frame").bounding_box()
        assert shown["y"] >= header["y"] + header["height"] - 1
        assert page.is_enabled("#back-open") and page.is_visible("#restart-open")
        # a link inside the report stays in the frame
        frame.locator("a").click()
        frame.locator("text=second").wait_for()
        page.click("#back-open")
        page.wait_for_selector("#view-previous-list:not([hidden])")
        # the x removes the entry and no file
        page.click("#history-rows tr:has-text('Baseline run') .analyze-remove")
        page.wait_for_function("document.querySelectorAll('#history-rows tr').length === 1")
        assert os.path.exists(str(tmp_path / "Baseline_run" / "oceanval_analysis" / "report.html"))
        assert [entry["name"] for entry in user_cache.history()] == ["Another one"]
    finally:
        page.close()
        app.close()


def comparison_in_history(tmp_path, out):
    """A comparison in the history that was built in out, and two validations to compare."""
    for name in ("control", "mixing"):
        (tmp_path / name / "oceanval_results" / "annual_mean").mkdir(parents=True)
    page = os.path.join(out, "oceanval_comparison", "compare", "_build", "html", "notebooks", "comparison_seasonal.html")
    os.makedirs(os.path.dirname(page))
    with open(page, "w") as file:
        file.write("old")
    assert user_cache.record_history("comparison", "Old comparison", page, page)[0]
    return page


def test_a_comparison_that_replaces_work_in_the_history_must_be_agreed(oceanvalcache, tmp_path):
    out = str(tmp_path / "out")
    comparison_in_history(tmp_path, out)
    form = {"name_1": "control", "dir_1": "control", "name_2": "mixing", "dir_2": "mixing",
            "out_dir": out, "add_to_history": "no"}
    assert check_compare(form, str(tmp_path))[1] == {"overwrite_history": "Old comparison"}
    assert check_compare(dict(form, overwrite_history=True), str(tmp_path))[1] == {}


def test_the_popup_asks_before_replacing_work_in_the_history(browser, tmp_path, oceanvalcache, monkeypatch):
    out = str(tmp_path / "out")
    page_file = comparison_in_history(tmp_path, out)
    app = App(cwd=str(tmp_path))
    started = []
    monkeypatch.setattr(app, "start_run", lambda *args: started.append(args))
    url = app.start()
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    try:
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-validate")
        page.click("#view-start [data-action=compare]")
        page.wait_for_selector("#view-compare:not([hidden])")
        for number, name in ((1, "control"), (2, "mixing")):
            page.fill(f"#c-name_{number}", name)
            page.fill(f"#c-dir_{number}", name)
        page.fill("#c-out_dir", out)
        page.check("#c-history-no")
        page.click("#compare")
        # cancelling leaves the work in the history and starts nothing
        page.wait_for_selector("#history-overwrite:not([hidden])")
        assert "Old comparison" in page.inner_text("#history-overwrite-text")
        page.click("#history-overwrite-cancel")
        page.wait_for_selector("#history-overwrite", state="hidden")
        assert started == [] and len(user_cache.history()) == 1
        # agreeing takes it out of the history and starts the run
        page.click("#compare")
        page.wait_for_selector("#history-overwrite:not([hidden])")
        page.click("#history-overwrite-confirm")
        page.wait_for_function("document.querySelector('#view-compare').hidden")
        assert len(started) == 1 and user_cache.history() == []
        assert os.path.exists(page_file)
    finally:
        page.close()
        app.close()
