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


def test_the_bank_in_a_browser(browser, tmp_path, oceanvalcache):
    write_analysis_simulation(str(tmp_path / "base"), years=(2013, 2014), daily=False)
    write_analysis_simulation(str(tmp_path / "fine"), years=(2013, 2014), daily=False)
    app = App(cwd=str(tmp_path))
    url = app.start()
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    try:
        page.goto(url)
        page.wait_for_selector("#view-home:not([hidden])")
        # the bank is not on the landing page: it is Help | Examples' second tile
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
