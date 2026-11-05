"""The simulation bank: names and directories kept on purpose in .oceanvalcache
(see oceanval.user_cache), added to from Analyze's and Compare's setup, listed by
the Name boxes, and managed on the Simulation bank page."""

import json

import pytest

from oceanval import user_cache
from oceanval.app import App
from simulations import write_analysis_simulation
from test_app import get_json, leftover_dir, post  # noqa: F401 (leftover_dir is autouse)


class TestTheBankInTheCache:
    def test_it_starts_empty_and_creates_nothing(self, oceanvalcache):
        assert user_cache.bank() == []
        assert not oceanvalcache.exists()

    def test_a_simulation_is_kept_in_the_order_added(self, oceanvalcache, tmp_path):
        assert user_cache.add_to_bank("Control", str(tmp_path / "a")) == (True, "")
        assert user_cache.add_to_bank("New mixing", str(tmp_path / "b")) == (True, "")
        assert user_cache.bank() == [
            {"name": "Control", "path": str(tmp_path / "a")},
            {"name": "New mixing", "path": str(tmp_path / "b")},
        ]

    def test_a_relative_directory_is_kept_in_full(self, oceanvalcache, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        user_cache.add_to_bank("Control", "sim")
        assert user_cache.bank()[0]["path"] == str(tmp_path / "sim")

    def test_the_same_pair_is_not_added_twice(self, oceanvalcache, tmp_path):
        user_cache.add_to_bank("Control", str(tmp_path / "a"))
        added, why = user_cache.add_to_bank("control", str(tmp_path / "a"))
        assert not added and "Already in the bank" in why
        assert len(user_cache.bank()) == 1

    def test_a_name_already_used_for_another_directory_is_refused(self, oceanvalcache, tmp_path):
        user_cache.add_to_bank("Control", str(tmp_path / "a"))
        added, why = user_cache.add_to_bank("Control", str(tmp_path / "b"))
        assert not added
        assert "'Control'" in why and str(tmp_path / "a") in why
        assert user_cache.bank() == [{"name": "Control", "path": str(tmp_path / "a")}]

    def test_a_directory_already_kept_under_another_name_is_refused(self, oceanvalcache, tmp_path):
        user_cache.add_to_bank("Control", str(tmp_path / "a"))
        added, why = user_cache.add_to_bank("Other", str(tmp_path / "a"))
        assert not added and "'Control'" in why
        assert len(user_cache.bank()) == 1

    def test_a_blank_name_or_directory_is_refused(self, oceanvalcache):
        assert not user_cache.add_to_bank("", "/x")[0]
        assert not user_cache.add_to_bank("Name", "  ")[0]
        assert not oceanvalcache.exists()

    def test_a_web_address_is_kept_as_it_is(self, oceanvalcache):
        user_cache.add_to_bank("Remote", "https://example.org/data.nc")
        assert user_cache.bank()[0]["path"] == "https://example.org/data.nc"

    def test_removing_leaves_everything_else(self, oceanvalcache, tmp_path):
        user_cache.record_sim_dir(str(tmp_path / "used"))
        user_cache.record_seen("validate")
        user_cache.add_to_bank("A", str(tmp_path / "a"))
        user_cache.add_to_bank("B", str(tmp_path / "b"))
        assert user_cache.remove_from_bank("a")
        assert user_cache.bank() == [{"name": "B", "path": str(tmp_path / "b")}]
        data = json.loads(oceanvalcache.read_text())
        assert data["sim_dirs"][0]["path"] == str(tmp_path / "used")
        assert user_cache.has_seen("validate")
        # a name that is not there is already gone
        assert user_cache.remove_from_bank("nothing")

    def test_adding_leaves_the_directories_used_before(self, oceanvalcache, tmp_path):
        user_cache.record_sim_dir(str(tmp_path / "used"))
        user_cache.add_to_bank("A", str(tmp_path / "a"))
        assert user_cache.recent_sim_dirs() == [str(tmp_path / "used")]

    def test_a_file_that_cannot_be_used_is_left_alone(self, oceanvalcache, tmp_path):
        oceanvalcache.write_text('{"version": 1, "bank": "oops"}')
        assert user_cache.bank() == []
        assert not user_cache.add_to_bank("A", str(tmp_path))[0]
        assert not user_cache.remove_from_bank("A")
        assert oceanvalcache.read_text() == '{"version": 1, "bank": "oops"}'

    def test_a_newer_file_is_left_alone(self, oceanvalcache, tmp_path):
        oceanvalcache.write_text('{"version": 99, "bank": []}')
        assert not user_cache.add_to_bank("A", str(tmp_path))[0]
        assert oceanvalcache.read_text() == '{"version": 99, "bank": []}'


class TestTheInfoOfASimulation:
    INFO = {"ndown": 1, "exclude": ["restart"], "require": [], "files": 4, "variables": 7, "years": [2013, 2014], "size": 1234}

    def test_an_entry_has_no_info_until_it_is_extracted(self, oceanvalcache, tmp_path):
        user_cache.add_to_bank("Control", str(tmp_path))
        assert "info" not in user_cache.bank()[0]

    def test_info_is_kept_with_its_entry(self, oceanvalcache, tmp_path):
        user_cache.add_to_bank("Control", str(tmp_path / "a"))
        user_cache.add_to_bank("Other", str(tmp_path / "b"))
        assert user_cache.set_bank_info("control", self.INFO) == (True, "")
        first, second = user_cache.bank()
        assert first["info"]["files"] == 4 and first["info"]["years"] == [2013, 2014]
        assert first["info"]["exclude"] == ["restart"] and first["info"]["extracted"]
        assert "info" not in second

    def test_the_number_of_variables_is_kept(self, oceanvalcache, tmp_path):
        user_cache.add_to_bank("Control", str(tmp_path))
        user_cache.set_bank_info("Control", self.INFO)
        assert user_cache.bank()[0]["info"]["variables"] == 7

    @pytest.mark.parametrize("variables", [None, -1, "many", True, 2.5])
    def test_variables_that_are_missing_or_wrong_do_not_spoil_the_info(self, oceanvalcache, tmp_path, variables):
        user_cache.add_to_bank("Control", str(tmp_path))
        info = {k: v for k, v in self.INFO.items() if k != "variables"} if variables is None else dict(self.INFO, variables=variables)
        assert user_cache.set_bank_info("Control", info)[0]
        kept = user_cache.bank()[0]["info"]
        assert kept["variables"] is None and kept["files"] == 4

    def test_info_kept_before_the_variables_were_has_none(self, oceanvalcache, tmp_path):
        user_cache.add_to_bank("Control", str(tmp_path))
        data = json.loads(oceanvalcache.read_text())
        data["bank"][0]["info"] = {k: v for k, v in self.INFO.items() if k != "variables"}
        oceanvalcache.write_text(json.dumps(data))
        assert user_cache.bank()[0]["info"]["variables"] is None

    def test_info_without_years_is_kept(self, oceanvalcache, tmp_path):
        user_cache.add_to_bank("Control", str(tmp_path))
        assert user_cache.set_bank_info("Control", dict(self.INFO, years=None))[0]
        assert user_cache.bank()[0]["info"]["years"] is None

    def test_info_for_a_simulation_not_in_the_bank_is_refused(self, oceanvalcache, tmp_path):
        user_cache.add_to_bank("Control", str(tmp_path))
        ok, why = user_cache.set_bank_info("Gone", self.INFO)
        assert not ok and "no longer in the bank" in why
        assert "info" not in user_cache.bank()[0]

    @pytest.mark.parametrize("bad", [
        dict(INFO, files=-1), dict(INFO, size="big"), dict(INFO, years=[2013]), dict(INFO, exclude="restart"),
        dict(INFO, ndown=True),
    ])
    def test_info_that_is_not_well_formed_is_refused(self, oceanvalcache, tmp_path, bad):
        user_cache.add_to_bank("Control", str(tmp_path))
        assert not user_cache.set_bank_info("Control", bad)[0]
        assert "info" not in user_cache.bank()[0]

    def test_a_badly_formed_info_in_the_file_is_ignored(self, oceanvalcache, tmp_path):
        user_cache.add_to_bank("Control", str(tmp_path))
        data = json.loads(oceanvalcache.read_text())
        data["bank"][0]["info"] = {"files": "many"}
        oceanvalcache.write_text(json.dumps(data))
        assert user_cache.bank() == [{"name": "Control", "path": str(tmp_path)}]

    def test_the_rest_of_the_file_is_kept(self, oceanvalcache, tmp_path):
        user_cache.record_sim_dir(str(tmp_path / "used"))
        user_cache.add_to_bank("Control", str(tmp_path))
        user_cache.set_bank_info("Control", self.INFO)
        assert user_cache.recent_sim_dirs() == [str(tmp_path / "used")]

    def test_a_file_that_cannot_be_used_is_left_alone(self, oceanvalcache, tmp_path):
        oceanvalcache.write_text('{"version": 99, "bank": []}')
        assert not user_cache.set_bank_info("Control", self.INFO)[0]
        assert oceanvalcache.read_text() == '{"version": 99, "bank": []}'

    def test_removing_the_simulation_removes_its_info(self, oceanvalcache, tmp_path):
        user_cache.add_to_bank("Control", str(tmp_path))
        user_cache.set_bank_info("Control", self.INFO)
        user_cache.remove_from_bank("Control")
        assert "info" not in oceanvalcache.read_text()


@pytest.fixture
def app(tmp_path):
    app = App(cwd=str(tmp_path))
    app.start()
    yield app
    app.close()


class TestTheWindow:
    def test_the_page_starts_with_an_empty_bank(self, app):
        assert get_json(app, "/api/state")[1]["bank"] == []

    def test_adding_and_removing(self, app, tmp_path):
        (tmp_path / "sim").mkdir()
        status, reply = post(app, "/api/bank_add", {"name": "Control", "path": "sim"})
        assert reply["ok"]
        # a directory is relative to the one OceanVal works in
        assert reply["bank"] == [{"name": "Control", "path": str(tmp_path / "sim"), "exists": True}]
        status, reply = post(app, "/api/bank_add", {"name": "Control", "path": "sim"})
        assert not reply["ok"] and "Already" in reply["error"]
        status, reply = post(app, "/api/bank_add", {"name": "Control", "path": "elsewhere"})
        assert not reply["ok"] and "already in the bank" in reply["error"]
        assert len(get_json(app, "/api/state")[1]["bank"]) == 1
        assert post(app, "/api/bank_remove", {"name": "Control"})[1]["bank"] == []
        assert get_json(app, "/api/state")[1]["bank"] == []

    def test_a_directory_that_has_gone_is_said_to(self, app, tmp_path):
        post(app, "/api/bank_add", {"name": "Gone", "path": "nowhere"})
        assert get_json(app, "/api/state")[1]["bank"][0]["exists"] is False

    def test_the_page_is_reached_from_help_and_back(self, app):
        app.choose("help")
        assert app.choose("bank")
        assert app.view == "bank"
        assert app.back()
        assert app.view == "help"

    def test_it_is_reached_only_from_help(self, app):
        assert not app.choose("bank")
        assert app.view == "home"

    def test_the_name_of_an_analysis_is_not_an_argument(self, tmp_path):
        from oceanval import analysis_forms

        write_analysis_simulation(str(tmp_path / "base"), years=(2013, 2014), daily=False)
        form = dict(analysis_forms.default_setup_form(str(tmp_path)), name="Control", simdir="base", ndown="1")
        arguments, errors = analysis_forms.check_setup(form, str(tmp_path))
        assert not errors and "name" not in arguments


def _wait_for_extraction(app, name, seconds=60):
    import time

    stop = time.monotonic() + seconds
    while time.monotonic() < stop:
        said = get_json(app, "/api/state")[1]["bank_extract"]
        if said and said["name"] == name and said["status"] != "working":
            return said
        time.sleep(0.1)
    raise AssertionError("the info was not extracted")


class TestExtractingTheInfo:
    @pytest.fixture
    def banked(self, app, tmp_path):
        write_analysis_simulation(str(tmp_path / "base"), years=(2013, 2014), daily=False)
        post(app, "/api/bank_add", {"name": "Control", "path": "base"})
        return app

    def test_nothing_is_extracted_to_start_with(self, banked):
        state = get_json(banked, "/api/state")[1]
        assert state["bank_extract"] is None
        assert "info" not in state["bank"][0]

    def test_the_files_years_and_size_are_kept(self, banked, tmp_path):
        import os

        assert post(banked, "/api/bank_extract", {"name": "control", "ndown": "1"})[1]["ok"]
        assert _wait_for_extraction(banked, "Control")["status"] == "done"
        info = get_json(banked, "/api/state")[1]["bank"][0]["info"]
        files = sorted((tmp_path / "base").glob("*/*.nc"))
        assert info["files"] == len(files) == 4
        assert info["years"] == [2013, 2014]
        # thetao and e3t in grid_T, P1_Chl and P2_Chl in ptrc_T
        assert info["variables"] == 4
        assert info["size"] == sum(os.path.getsize(f) for f in files)
        assert (info["ndown"], info["exclude"], info["require"]) == (1, [], [])
        assert user_cache.bank()[0]["info"]["files"] == 4

    def test_the_file_filters_decide_what_is_counted(self, banked, tmp_path):
        post(banked, "/api/bank_extract", {"name": "Control", "ndown": "1", "require": "grid_T", "exclude": "x_y"})
        assert _wait_for_extraction(banked, "Control")["status"] == "done"
        info = user_cache.bank()[0]["info"]
        assert info["files"] == 2 and info["require"] == ["grid_T"] and info["exclude"] == ["x_y"]
        # only that stream's variables
        assert info["variables"] == 2

    def test_files_that_cannot_be_read_leave_the_variables_unknown(self, app, tmp_path):
        (tmp_path / "bad").mkdir()
        (tmp_path / "bad" / "a_2013.nc").write_text("not a netCDF file")
        post(app, "/api/bank_add", {"name": "Bad", "path": "bad"})
        post(app, "/api/bank_extract", {"name": "Bad", "ndown": "0"})
        assert _wait_for_extraction(app, "Bad")["status"] == "done"
        info = user_cache.bank()[0]["info"]
        assert info["files"] == 1 and info["variables"] is None

    def test_the_page_is_told_it_is_over_and_can_clear_it(self, banked):
        post(banked, "/api/bank_extract", {"name": "Control", "ndown": "1"})
        _wait_for_extraction(banked, "Control")
        post(banked, "/api/bank_extract_clear")
        assert get_json(banked, "/api/state")[1]["bank_extract"] is None

    def test_no_files_at_that_depth_fails_and_keeps_nothing(self, banked):
        post(banked, "/api/bank_extract", {"name": "Control", "ndown": "0"})
        said = _wait_for_extraction(banked, "Control")
        assert said["status"] == "failed" and "no netCDF files" in said["error"]
        assert "info" not in user_cache.bank()[0]

    @pytest.mark.parametrize("ndown", ["", "-1", "two", "1.5"])
    def test_a_depth_that_is_not_a_whole_number_is_refused(self, banked, ndown):
        reply = post(banked, "/api/bank_extract", {"name": "Control", "ndown": ndown})[1]
        assert not reply["ok"] and "whole number" in reply["error"]

    def test_a_simulation_not_in_the_bank_is_refused(self, banked):
        reply = post(banked, "/api/bank_extract", {"name": "Nope", "ndown": "1"})[1]
        assert not reply["ok"] and "not in the bank" in reply["error"]

    def test_a_missing_directory_is_refused(self, app):
        post(app, "/api/bank_add", {"name": "Gone", "path": "nowhere"})
        assert "no directory" in post(app, "/api/bank_extract", {"name": "Gone", "ndown": "0"})[1]["error"]

    def test_a_web_address_is_refused(self, app):
        post(app, "/api/bank_add", {"name": "Remote", "path": "https://example.org/data.nc"})
        assert "web address" in post(app, "/api/bank_extract", {"name": "Remote", "ndown": "0"})[1]["error"]

    def test_restarting_forgets_it(self, banked):
        post(banked, "/api/bank_extract", {"name": "Control", "ndown": "1"})
        banked.reset()
        assert get_json(banked, "/api/state")[1]["bank_extract"] is None


def test_the_bank_in_a_browser(browser, tmp_path, oceanvalcache):
    write_analysis_simulation(str(tmp_path / "base"), years=(2013, 2014), daily=False)
    write_analysis_simulation(str(tmp_path / "fine"), years=(2013, 2014), daily=False)
    app = App(cwd=str(tmp_path))
    url = app.start()
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    try:
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        # the bank is not on the landing page: it is Tools & checks' second tile
        assert page.locator("#view-home .choice").count() == 4
        assert page.locator("#home-bank").count() == 0

        # Compare: Add to bank needs both boxes, then says it is in
        page.click("#home-analyze")
        page.click("#menu-simcompare")
        page.wait_for_selector("#view-simcompare-sims:not([hidden])")
        assert page.is_disabled("#sc-bank_1")
        assert not page.is_visible("#sc-name_1-bank-toggle")
        page.fill("#sc-name_1", "Base run")
        page.fill("#sc-dir_1", "base")
        page.click("#sc-bank_1")
        page.wait_for_function("() => document.querySelector('#sc-bank_1').textContent === 'In bank'")
        assert page.is_disabled("#sc-bank_1")
        assert [entry["name"] for entry in user_cache.bank()] == ["Base run"]

        # a clashing name is refused, with the reason
        page.fill("#sc-name_2", "Base run")
        page.fill("#sc-dir_2", "fine")
        page.click("#sc-bank_2")
        page.wait_for_function("() => document.querySelector('#toast').textContent.includes('already in the bank')")
        assert len(user_cache.bank()) == 1

        # the name arrow lists the bank, and a pick fills the name and directory
        page.fill("#sc-name_2", "")
        page.fill("#sc-dir_2", "")
        page.click("#sc-name_2-bank-toggle")
        page.click("#sc-name_2-bank .combo__option")
        assert page.input_value("#sc-name_2") == "Base run"
        assert page.input_value("#sc-dir_2") == str(tmp_path / "base")

        # the page lists it, and removing it empties the cache's bank
        page.click("button:has-text('Back')")
        page.click("button:has-text('Back')")
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-help")
        page.wait_for_selector("#view-help:not([hidden])")
        assert page.locator("#view-help .choice").count() == 2
        page.click("#help-bank")
        page.wait_for_selector("#view-bank:not([hidden])")
        assert page.locator("#bank-rows tr").count() == 1
        assert "Base run" in page.inner_text("#bank-rows")
        page.click("#bank-rows .analyze-remove")
        page.wait_for_selector("#bank-empty:not([hidden])")
        assert user_cache.bank() == []

        # Analyze: the name box is there for an analysis only
        page.click("button:has-text('Back')")
        page.wait_for_selector("#view-help:not([hidden])")
        assert page.is_visible("#help-bank")
        page.click("button:has-text('Back')")
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-analyze")
        page.click("#menu-analyze")
        page.wait_for_selector("#view-setup:not([hidden])")
        assert page.is_visible("#f-name")
        page.fill("#f-simdir", "base")
        assert page.is_disabled("#f-bank")
        page.fill("#f-name", "Analysed")
        page.click("#f-bank")
        page.wait_for_function("() => document.querySelector('#f-bank').textContent === 'In bank'")
        assert user_cache.bank() == [{"name": "Analysed", "path": str(tmp_path / "base")}]
    finally:
        page.close()
        app.close()


def test_adding_simulation_info_in_a_browser(browser, tmp_path, oceanvalcache):
    write_analysis_simulation(str(tmp_path / "base"), years=(2013, 2014), daily=False)
    user_cache.add_to_bank("Control", str(tmp_path / "base"))
    user_cache.add_to_bank("Remote", "https://example.org/data.nc")
    app = App(cwd=str(tmp_path))
    url = app.start()
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    try:
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-help")
        page.click("#help-bank")
        page.wait_for_selector("#view-bank:not([hidden])")
        heads = [text.title() if text else text for text in page.locator("#view-bank thead th").all_inner_texts()]
        assert heads[:2] == ["Name", "Directory"]
        assert heads[2:6] == ["Number Of Files", "Number Of Variables", "Years", "Size Of Simulation"]
        assert heads[6:] == ["", ""]
        # missing to start with, and a button to add them (not for a web address)
        row = page.locator("#bank-rows tr").first
        assert row.locator("td.bank__info").count() == 0
        assert row.locator("button:has-text('Add simulation info')").count() == 1
        assert page.locator("#bank-rows tr").nth(1).locator("button:has-text('Add simulation info')").count() == 0

        row.locator("button:has-text('Add simulation info')").click()
        page.wait_for_selector("#bank-info:not([hidden])")
        # nothing is counted until the depth is given; this layout is not one OceanVal fills in
        assert page.input_value("#bank-ndown") == "" and page.is_disabled("#bank-info-extract")
        assert page.is_hidden("#bank-info-view-row")
        page.fill("#bank-ndown", "1")
        page.wait_for_function("() => document.querySelector('#bank-info-count').textContent.includes('4 netCDF files found')")
        assert page.is_enabled("#bank-info-extract")
        page.fill("#bank-require", "grid_T")
        page.wait_for_function("() => document.querySelector('#bank-info-count').textContent.includes('2 of the 4')")
        page.fill("#bank-require", "")
        page.wait_for_function("() => document.querySelector('#bank-info-count').textContent.includes('4 netCDF files found')")

        # a look at a random subdirectory, above the pop-out, and back
        page.click("#bank-info-view")
        page.wait_for_selector("#sample:not([hidden])")
        page.wait_for_function("() => document.querySelector('#sample-list').textContent.includes('grid_T.nc')")
        page.click("#sample-close")
        assert page.is_hidden("#sample") and page.is_visible("#bank-info")
        assert page.evaluate("document.getElementById('bank-info').inert") is False
        # still behind the pop-out
        assert page.evaluate("document.querySelector('main').inert") is True

        page.click("#bank-info-extract")
        page.wait_for_selector("#bank-info", state="hidden")
        row = page.locator("#bank-rows tr").first
        cells = row.locator("td.bank__info").all_inner_texts()
        assert cells[0] == "4" and cells[1] == "4" and cells[2] == "2013–2014"
        assert cells[3].startswith(("1", "2", "3", "4", "5", "6", "7", "8", "9")) and "B" in cells[3]
        assert row.locator("button:has-text('Add simulation info')").count() == 0
        assert user_cache.bank()[0]["info"]["files"] == 4

        # Regenerate, the last button on the right but for the ×, opens it again with what was used
        assert row.locator("td").nth(6).locator("button:has-text('Regenerate')").count() == 1
        row.locator("button:has-text('Regenerate')").click()
        page.wait_for_selector("#bank-info:not([hidden])")
        assert page.input_value("#bank-ndown") == "1"
        page.fill("#bank-require", "grid_T")
        page.wait_for_function("() => document.querySelector('#bank-info-count').textContent.includes('2 of the 4')")
        page.click("#bank-info-extract")
        page.wait_for_selector("#bank-info", state="hidden")
        assert page.locator("#bank-rows tr").first.locator("td.bank__info").first.inner_text() == "2"
        # grid_T only: thetao and e3t
        assert page.locator("#bank-rows tr").first.locator("td.bank__info").nth(1).inner_text() == "2"
        assert user_cache.bank()[0]["info"]["require"] == ["grid_T"]
        assert page.evaluate("document.querySelector('main').inert") is False
    finally:
        page.close()
        app.close()


def test_adding_to_the_bank_from_the_bank_page(browser, tmp_path, oceanvalcache):
    write_analysis_simulation(str(tmp_path / "base"), years=(2013, 2014), daily=False)
    write_analysis_simulation(str(tmp_path / "fine"), years=(2013, 2014), daily=False)
    app = App(cwd=str(tmp_path))
    url = app.start()
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    try:
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-help")
        page.wait_for_selector("#view-help:not([hidden])")
        # only the bank page shows it, under the simulations at the left (not in the bar), also with the bank empty
        assert page.is_hidden("#bank-add-open")
        page.click("#help-bank")
        page.wait_for_selector("#view-bank:not([hidden])")
        assert page.locator("#view-bank #bank-add-open").count() == 1
        assert page.locator("#bar #bank-add-open").count() == 0
        assert page.inner_text("#bank-add-open") == "Add to the bank"
        assert page.is_visible("#bank-empty")

        def under_the_table_at_the_left():
            card = page.locator("#view-bank .card.bank").bounding_box()
            button = page.locator("#bank-add-open").bounding_box()
            assert button["y"] >= card["y"] + card["height"]
            assert abs(button["x"] - card["x"]) <= 2

        under_the_table_at_the_left()

        page.click("#bank-add-open")
        page.wait_for_selector("#bank-add:not([hidden])")
        assert page.evaluate("document.querySelector('main').inert") is True
        assert page.is_disabled("#bank-add-confirm")
        page.fill("#bank-add-name", "Control")
        assert page.is_disabled("#bank-add-confirm")

        # a directory that is not there is caught as it is typed
        page.fill("#bank-add-dir", "nowhere")
        page.wait_for_function("() => document.querySelector('#bank-add-note').textContent.includes('no directory')")
        assert page.is_disabled("#bank-add-confirm")

        # Browse: the folder browser is above the pop-out, which waits behind it, then is as it was
        page.click("[data-browse='bank-add-dir']")
        page.wait_for_selector("#picker:not([hidden])")
        assert page.evaluate("document.getElementById('bank-add').inert") is True
        page.click("#picker-cancel")
        page.wait_for_selector("#picker", state="hidden")
        assert page.evaluate("document.getElementById('bank-add').inert") is False
        assert page.evaluate("document.querySelector('main').inert") is True

        # a real directory (relative to where OceanVal works) turns the check off and allows it
        page.fill("#bank-add-dir", "base")
        page.wait_for_function("() => !document.querySelector('#bank-add-confirm').disabled")
        assert page.inner_text("#bank-add-note") == ""
        page.click("#bank-add-confirm")
        page.wait_for_selector("#bank-add", state="hidden")
        assert page.evaluate("document.querySelector('main').inert") is False
        assert user_cache.bank() == [{"name": "Control", "path": str(tmp_path / "base")}]
        row = page.locator("#bank-rows tr")
        assert row.count() == 1 and "Control" in row.first.inner_text()
        assert row.first.locator("button:has-text('Add simulation info')").count() == 1
        under_the_table_at_the_left()

        # a refusal is said in the pop-out, which stays open; a new name and directory then add
        page.click("#bank-add-open")
        page.fill("#bank-add-name", "control")
        page.fill("#bank-add-dir", "fine")
        page.wait_for_function("() => !document.querySelector('#bank-add-confirm').disabled")
        page.click("#bank-add-confirm")
        page.wait_for_function("() => document.querySelector('#bank-add-note').textContent.includes('already in the bank')")
        assert page.is_visible("#bank-add") and len(user_cache.bank()) == 1
        page.fill("#bank-add-name", "Fine")
        assert page.inner_text("#bank-add-note") == ""
        page.click("#bank-add-confirm")
        page.wait_for_selector("#bank-add", state="hidden")
        assert [entry["name"] for entry in user_cache.bank()] == ["Control", "Fine"]
        assert page.locator("#bank-rows tr").count() == 2
        # still under the list, which has grown
        under_the_table_at_the_left()

        # a web address needs no directory; Escape closes without adding
        page.click("#bank-add-open")
        page.fill("#bank-add-name", "Remote")
        page.fill("#bank-add-dir", "https://example.org/data.nc")
        page.wait_for_function("() => !document.querySelector('#bank-add-confirm').disabled")
        page.keyboard.press("Escape")
        page.wait_for_selector("#bank-add", state="hidden")
        assert len(user_cache.bank()) == 2
        assert page.evaluate("document.querySelector('main').inert") is False
        # the pop-out starts empty each time
        page.click("#bank-add-open")
        assert page.input_value("#bank-add-name") == "" and page.input_value("#bank-add-dir") == ""
    finally:
        page.close()
        app.close()


def test_the_simulation_directory_boxes_have_one_drop_down_each(browser, tmp_path, oceanvalcache):
    """The bank's arrow is the only list: the browser's own list of folders is not on any of them."""
    user_cache.add_to_bank("Control", str(tmp_path))
    app = App(cwd=str(tmp_path))
    url = app.start()
    page = browser.new_page(viewport={"width": 1280, "height": 1000})

    def one_drop_down(box):
        assert page.evaluate(f"document.getElementById('{box}').list") is None
        assert page.locator(f"#{box}-combo .combo__toggle").count() == 1
        assert page.is_visible(f"#{box}-recent-toggle")

    try:
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        page.click("#home-analyze")
        page.click("#menu-simcompare")
        page.wait_for_selector("#view-simcompare-sims:not([hidden])")
        for row in range(1, 6):
            assert page.evaluate(f"document.getElementById('sc-dir_{row}').list") is None
            assert page.locator(f"#sc-dir_{row}-folders").count() == 0
        one_drop_down("sc-dir_1")
        one_drop_down("sc-dir_2")
        # Analyze, Validate and Viewing share the setup page
        for tile, choice in (("#home-analyze", "#menu-analyze"), ("#home-validate", 'button.choice[data-action="matchup"]'),
                             ("#home-viewer", None)):
            page.click("button:has-text('Back')")
            if page.is_visible("button:has-text('Back')"):
                page.click("button:has-text('Back')")
            page.wait_for_selector("#view-home:not([hidden])")
            page.click(tile)
            if choice:
                page.click(choice)
            page.wait_for_selector("#view-setup:not([hidden])")
            one_drop_down("f-simdir")
            assert page.locator("#f-simdir-folders").count() == 0
    finally:
        page.close()
        app.close()
