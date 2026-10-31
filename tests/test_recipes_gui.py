"""The create_recipes window: its rows, the server behind it and, where a
browser can be run, the page itself."""

import copy
import json
import queue
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pytest
import xarray as xr

import oceanval
import oceanval.recipes_gui as recipes_gui
from oceanval import user_recipes, user_settings
from oceanval.create_recipes import RECIPE_VARIABLES, default_selection, replaced_recipes
from oceanval.recipes_gui import (
    RecipePage,
    check_gridded_options,
    check_point_options,
    check_settings,
    choose_recipes,
    default_form,
    default_settings,
    check_own_options,
    own_options,
    recipe_rows,
)

CONTEXT = {
    "simdir": "/sim",
    "ndown": 2,
    "domain": "nwes",
    "region": "Northwest European Shelf",
    "start": 2011,
    "end": 2012,
    "fvcom": False,
    "out": "/out/matchup.py",
}


def ticks(rows, variable, kind="gridded"):
    row = next(row for row in rows if row["variable"] == variable)
    return {dataset["recipe"]: dataset["ticked"] for dataset in row[kind]}


class TestRows:
    def test_there_is_one_row_per_variable_alphabetically(self):
        rows = recipe_rows({}, "global")
        assert [row["variable"] for row in rows] == sorted(RECIPE_VARIABLES)

    def test_titles_are_the_ones_the_notes_use(self):
        titles = {row["variable"]: row["title"] for row in recipe_rows({}, "global")}
        assert titles["ph"] == "pH"
        assert titles["kd490"] == "KD490"
        assert titles["ammonium"] == "Ammonium"

    def test_the_tables_show_descriptive_names_and_the_report_keeps_the_titles(self):
        rows = {row["variable"]: row for row in recipe_rows({}, "global")}
        assert rows["nitrate"]["display"] == "Nitrate concentration in sea water"
        assert rows["nitrate"]["title"] == "Nitrate"
        assert {variable: row["display"] for variable, row in rows.items()} == {
            variable: user_recipes.DISPLAY_NAMES[variable] for variable in rows
        }

    @pytest.mark.parametrize("domain", ["global", "nwes"])
    def test_the_ticks_are_what_the_script_would_leave_live(self, domain):
        # so writing the script straight away gives the one gui=False would
        mapping = {variable: variable for variable in RECIPE_VARIABLES}
        ticked = {
            (row["variable"], dataset["recipe"])
            for row in recipe_rows(mapping, domain)
            for dataset in row["gridded"] + row["point"]
            if dataset["ticked"]
        }
        assert ticked == default_selection(mapping, domain)

    def test_recipes_the_users_own_data_takes_the_place_of_are_left_out(self, tmp_path):
        cwd = str(tmp_path)
        user_recipes.save(
            {"variable": "nitrate", "kind": "gridded", "source": "MySat", "obs_path": "/obs.nc",
             "location": "disk", "obs_variable": "N3_n", "climatology": True},
            "local", cwd,
        )
        user_recipes.save(
            {"variable": "dic", "kind": "point", "source": "DicPts", "obs_path": "/points",
             "short_title": "DIC"},
            "local", cwd,
        )
        own = {
            "gridded": [{"name": "Nitrate", "source": "MYSAT"}, {"name": "chlorophyll", "source": "OCCCI"}],
            "point": [{"name": "dic", "source": "dicpts"}],
        }
        # the user's recipes of the same variable and source, whatever their
        # case, and never one OceanVal comes with
        replaced = replaced_recipes(own, cwd)
        assert replaced == {("nitrate", "mysat"), ("dic", "dicpts")}

        def datasets(rows, variable):
            row = next((row for row in rows if row["variable"] == variable), None)
            return None if row is None else [d["recipe"] for d in row["gridded"] + row["point"]]

        rows = recipe_rows({}, "global", cwd)
        assert datasets(rows, "nitrate") == ["woa23", "nsbc", "mysat", "ices"]
        assert datasets(rows, "dic") == ["dicpts"]
        rows = recipe_rows({"nitrate": "N3_n"}, "global", cwd, replaced)
        assert datasets(rows, "nitrate") == ["woa23", "nsbc", "ices"]
        assert "occci" in datasets(rows, "chlorophyll")
        # a variable only those recipes were for has no row left
        assert datasets(rows, "dic") is None

    def test_the_users_own_data_is_shown_in_its_variables_row(self, tmp_path):
        cwd = str(tmp_path)
        user_recipes.save(
            {"variable": "temperature", "kind": "point", "source": "pygetm",
             "obs_path": "/points", "depth_resolved": True},
            "local", cwd,
        )
        own = {
            "point": [{"name": "Temperature", "source": "pygetm", "model_variable": "temp",
                       "obs_path": "/points", "vertical": True}],
            "gridded": [
                {"name": "dic", "source": "MyDic", "model_variable": "O3_c",
                 "obs_path": "/dic.nc", "short_title": "DIC"},
                {"name": "zooc", "source": "Zoo", "model_variable": "Z4_c", "obs_path": "/zoo.nc"},
            ],
        }
        replaced = replaced_recipes(own, cwd)
        assert replaced == {("temperature", "pygetm")}

        rows = recipe_rows({"temperature": "temp"}, "global", cwd, replaced, own)
        found = {row["variable"]: row for row in rows}
        assert [row["variable"] for row in rows] == sorted(found)
        # json, for the page
        json.dumps(rows)

        # beside the datasets, in place of the recipe it was saved as
        temperature = found["temperature"]
        assert [d["recipe"] for d in temperature["point"]] == ["ices"]
        assert temperature["own"] == [{
            "kind": "point", "index": 0, "label": "pygetm", "model_variable": "temp",
            "path": "/points", "saved": True,
            # its options, as the window's boxes show them
            "opts": {"start": "", "end": "", "vertical": True, "point_time_res": ""},
            # only gridded data is given the years for climatologies
            "climatology": False,
        }]
        # a variable only the user's own data is for has a row with it alone,
        # named as add_gridded_comparison names it
        assert (found["dic"]["title"], found["zooc"]["title"]) == ("DIC", "Zooc")
        for index, (variable, source) in enumerate((("dic", "MyDic"), ("zooc", "Zoo"))):
            assert found[variable]["gridded"] == found[variable]["point"] == []
            assert [(item["label"], item["index"], item["saved"]) for item in found[variable]["own"]] == [
                (source, index, False)
            ]
        assert found["dic"]["own"][0]["opts"] == {"start": "", "end": "", "vertical": False}
        assert all(row["own"] == [] for row in rows if row["variable"] not in ("temperature", "dic", "zooc"))

        # without it, every row's is empty
        assert all(row["own"] == [] for row in recipe_rows({}, "global", cwd))


class TestErsemDetection:
    def test_more_than_twenty_signature_variables_are_required(self):
        variables = sorted(recipes_gui.ERSEM_VARIABLES)

        assert not recipes_gui.is_likely_nemo_ersem(variables[:20])
        assert recipes_gui.is_likely_nemo_ersem(variables[:21])

    @pytest.mark.parametrize("has_e3t", [True, False])
    def test_hosted_page_suggests_the_ersem_thickness_in_amber(self, browser, has_e3t):
        available = set(recipes_gui.ERSEM_VARIABLES)
        if has_e3t:
            available.add("e3t")
        else:
            available.discard("e3t")
        recipe_page = RecipePage(
            recipe_rows({}, "global"),
            available,
            dict(CONTEXT, app={"action": "matchup", "own_vertical": False}),
            lambda *chosen: "/out/matchup.py",
        )
        url = recipe_page.start()
        try:
            browser_page = browser.new_page()
            browser_page.goto(url)

            assert browser_page.input_value("#s-thickness") == ("e3t" if has_e3t else "")
            if has_e3t:
                assert "is-ersem" in browser_page.locator("#s-thickness").get_attribute("class")
            # the missing values are asked with the cores, once the matchups
            # are confirmed, where 0 to 0 is assumed (see test_app)
            assert browser_page.locator("#s-missing_from").is_hidden()
            assert browser_page.input_value("#s-missing_from") == ""
        finally:
            recipe_page.close()

    def test_the_other_regions_datasets_start_unticked(self):
        rows = recipe_rows({"temperature": "thetao"}, "nwes")
        assert ticks(rows, "temperature") == {"cobe2": False, "woa23": False, "nsbc": True}
        assert ticks(rows, "temperature", "point") == {"ices": True}

        rows = recipe_rows({"temperature": "thetao"}, "global")
        # and within a region only the recipe the script leaves live is ticked
        assert ticks(rows, "temperature") == {"cobe2": True, "woa23": False, "nsbc": False}
        assert ticks(rows, "temperature", "point") == {"ices": False}

    def test_a_dataset_outside_the_domain_is_ticked_if_it_is_the_only_one(self):
        assert ticks(recipe_rows({"ammonium": "N4_n"}, "global"), "ammonium") == {
            "nsbc": True
        }
        assert ticks(recipe_rows({"alkalinity": "O3_TA"}, "nwes"), "alkalinity") == {
            "glodap": True
        }

    def test_an_unidentified_variable_is_blank_with_nothing_ticked(self):
        row = next(
            row for row in recipe_rows({}, "nwes") if row["variable"] == "salinity"
        )
        datasets = row["gridded"] + row["point"]

        assert row["model_variable"] == ""
        assert not any(dataset["ticked"] for dataset in datasets)
        # what the page ticks once a model variable is filled in
        assert {dataset["recipe"]: dataset["default"] for dataset in datasets} == {
            "woa23": False,
            "nsbc": True,
            "ices": True,
        }

    def test_kd490_has_no_point_dataset(self):
        row = next(
            row for row in recipe_rows({}, "global") if row["variable"] == "kd490"
        )
        assert row["point"] == []
        assert [dataset["label"] for dataset in row["gridded"]] == ["OC-CCI"]

    def test_vertical_is_only_offered_where_the_observations_have_depths(self):
        rows = recipe_rows({}, "global")
        offered = {
            (row["variable"], dataset["recipe"])
            for row in rows
            for dataset in row["gridded"] + row["point"]
            if dataset["vertical_option"]
        }

        assert {recipe for _, recipe in offered} == {"woa23", "nsbc", "ices"}
        # surface-only: satellite, sea surface temperature and GLODAP's surface
        assert not offered & {
            ("chlorophyll", "occci"), ("kd490", "occci"),
            ("temperature", "cobe2"), ("ph", "glodap"), ("alkalinity", "glodap"),
        }

    def test_only_woa23_temperature_and_salinity_are_per_decade(self):
        decadal = {
            (row["variable"], dataset["recipe"])
            for row in recipe_rows({}, "global")
            for dataset in row["gridded"]
            if dataset["decadal"]
        }
        assert decadal == {("temperature", "woa23"), ("salinity", "woa23")}

    def test_tooltips_leave_out_what_only_makes_sense_in_the_script(self):
        for row in recipe_rows({}, "global"):
            for dataset in row["gridded"] + row["point"]:
                assert "below" not in dataset["details"]
                assert "recipe:" not in dataset["details"]


def _panels_work(page, oceanvalrc):
    """The cog and the i beside the wordmark open their panels on this page,
    and Apply saves the settings."""
    assert page.locator(".masthead .brand #settings-open").count() == 1
    assert page.locator(".masthead .brand #info-open").count() == 1
    assert page.is_hidden("#preferences")
    page.click("#settings-open")
    assert page.is_visible("#preferences")
    # the data store is set here too
    assert page.is_visible("#settings-data-store")
    assert page.locator('input[name="settings-data-store-use"]').count() == 2
    assert "reasonable amount of free space" in page.text_content("#settings-data-store-note")
    assert page.text_content("#settings-data-store-check") == "Run a check on the data store"
    assert page.locator("#settings-themes").count() == 0
    page.uncheck("#settings-show-demo")
    page.click("#settings-apply")
    page.wait_for_selector("#preferences", state="hidden")
    assert json.loads(oceanvalrc.read_text())["settings"]["show_demo"] is False
    assert page.evaluate("document.activeElement.id") == "settings-open"

    page.click("#info-open")
    assert page.is_visible("#info")
    assert page.text_content("#info-version") == f"Version {oceanval.__version__}"
    assert page.locator("#info-gridded tbody tr").count() > 0
    page.keyboard.press("Escape")
    assert page.is_hidden("#info")
    assert page.evaluate("document.activeElement.id") == "info-open"
    # Restart is only for the pages the oceanval window hosts, which have a first page to go back to
    assert page.locator("#restart-open").count() == 0


def test_the_pages_carry_the_logos_inline():
    html = recipes_gui.render_page("create_recipes_gui.html", {})
    assert "data:image/jpeg;base64," in html
    assert "__OCEANVAL_PML_LOGO__" not in html


def test_the_info_panel_credits_pml_and_gives_the_licence(browser, oceanvalrc):
    recipe_page = RecipePage(
        recipe_rows({"temperature": "thetao"}, "nwes"),
        {"thetao"},
        CONTEXT,
        lambda *chosen: "/out/matchup.py",
    )
    url = recipe_page.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click("#info-open")
        credit = page.locator("#info .info__credit")
        assert "Environmental Intelligence group at Plymouth Marine Laboratory" in credit.text_content()
        assert "GNU GPLv3 license" in credit.text_content()
        assert credit.locator("a[href='https://pml.ac.uk/']").count() == 1
        assert all(page.evaluate("img => img.naturalWidth", img.element_handle()) > 0
                   for img in credit.locator("img").all())
        # at the bottom, under the tables
        assert credit.bounding_box()["y"] > page.locator("#info-point").bounding_box()["y"]
    finally:
        recipe_page.close()


def test_the_preferences_and_info_are_in_the_window_too(browser, oceanvalrc):
    recipe_page = RecipePage(
        recipe_rows({"temperature": "thetao"}, "nwes"),
        {"thetao"},
        CONTEXT,
        lambda *chosen: "/out/matchup.py",
    )
    url = recipe_page.start()
    try:
        page = browser.new_page()
        page.goto(url)
        _panels_work(page, oceanvalrc)
    finally:
        recipe_page.close()


def _get(page, query=""):
    url = page.url.split("?")[0] + query
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def _post(url, path, body, token=None):
    """POST to the server behind url, as the page does."""
    parts = urllib.parse.urlsplit(url)
    if token is None:
        token = urllib.parse.parse_qs(parts.query)["token"][0]
    request = urllib.request.Request(
        f"{parts.scheme}://{parts.netloc}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-OceanVal-Token": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def page():
    written = []

    def write(*chosen):
        written.append(chosen)
        return "/out/matchup.py"

    page = RecipePage(
        recipe_rows({"temperature": "thetao"}, "nwes"),
        {"thetao", "so", "P1_Chl", "P2_Chl"},
        CONTEXT,
        write,
    )
    page.written = written
    page.start()
    yield page
    page.close()


class TestServer:
    def test_the_page_needs_the_token(self, page):
        assert _get(page)[0] == 403
        assert _get(page, "?token=wrong")[0] == 403

    def test_the_page_holds_the_rows(self, page):
        status, body = _get(page, f"?token={page.token}")
        state = re.search(r'<script type="application/json" id="state">(.*?)</script>', body, re.S)
        state = json.loads(state.group(1))

        assert status == 200
        assert [row["variable"] for row in state["rows"]] == sorted(RECIPE_VARIABLES)
        assert state["context"] == CONTEXT

    def test_a_variable_name_cannot_break_out_of_the_page(self, page):
        page.available.add("</script><script>alert(1)</script>")
        _, body = _get(page, f"?token={page.token}")

        assert "<script>alert(1)" not in body

    def test_posting_needs_the_token(self, page):
        status, _ = _post(page.url, "/cancel", {}, token="wrong")

        assert status == 403
        assert not page._done.is_set()

    def test_a_model_variable_not_in_the_output_is_sent_back(self, page):
        status, reply = _post(
            page.url,
            "/write",
            {"rows": [{"variable": "temperature", "model_variable": "thetaoo", "selected": ["nsbc"]}]},
        )

        assert status == 400
        assert reply["errors"] == {"temperature": "thetaoo is not in the simulation output."}
        assert page.written == []
        assert not page._done.is_set()

    def test_the_script_is_written_with_what_was_chosen(self, page):
        rows = [
            {"variable": "temperature", "model_variable": "thetao", "selected": ["cobe2", "nsbc", "ices"]},
            {"variable": "chlorophyll", "model_variable": " P1_Chl + P2_Chl ", "selected": ["nsbc"]},
            {"variable": "salinity", "model_variable": "", "selected": []},
        ]
        status, reply = _post(page.url, "/write", {"rows": rows})
        # a request without settings keeps the defaults
        chosen = (
            {"temperature": "thetao", "chlorophyll": "P1_Chl+P2_Chl"},
            {
                ("temperature", "cobe2"),
                ("temperature", "nsbc"),
                ("temperature", "ices"),
                ("chlorophyll", "nsbc"),
            },
            default_settings(2011, 2012),
            {},
            {},
        )

        assert (status, reply) == (200, {"ok": True, "out": "/out/matchup.py"})
        assert page.written == [chosen]
        assert page.wait() == chosen
        # and a second click does not write it again
        assert _post(page.url, "/write", {"rows": rows})[0] == 409
        assert len(page.written) == 1

    def test_a_dataset_the_variable_does_not_have_is_refused(self, page):
        status, _ = _post(
            page.url,
            "/write",
            {"rows": [{"variable": "kd490", "model_variable": "thetao", "selected": ["ices"]}]},
        )

        assert status == 400
        assert page.written == []

    def test_a_failed_write_is_reported_and_can_be_tried_again(self, page):
        def write(*chosen):
            raise PermissionError("read-only file system")

        page.write = write
        status, reply = _post(page.url, "/write", {"rows": []})

        assert status == 500
        assert "read-only file system" in reply["error"]
        assert not page._done.is_set()

    def test_cancelling(self, page):
        assert _post(page.url, "/cancel", {})[0] == 200
        assert page.wait() is None

    def test_what_the_page_held_when_it_was_cancelled_is_kept(self, page):
        """The oceanval app shows the window again with it."""
        sent = {
            "rows": [{"variable": "temperature", "model_variable": "so", "selected": []}],
            "settings": {"start": "2011"},
        }

        assert _post(page.url, "/cancel", sent)[0] == 200
        assert page.wait() is None
        assert page.sent == sent

    def test_a_window_reopened_can_write_the_script_again(self, page):
        rows = [{"variable": "temperature", "model_variable": "thetao", "selected": ["cobe2"]}]
        assert _post(page.url, "/write", {"rows": rows})[0] == 200
        assert _post(page.url, "/write", {"rows": rows})[0] == 409

        page.reopen()

        assert page.result is None and not page._done.is_set()
        assert _post(page.url, "/write", {"rows": []})[0] == 200
        assert page.wait()[0] == {}
        assert page.sent == {"rows": []}
        assert len(page.written) == 2

    def test_settings_and_point_options_are_written(self, page):
        # GitHub's runners have 2 cores, and no more can be chosen
        form = dict(default_form(2011, 2012), cores="1", lon_min="-20", lon_max="10",
                    lat_min="40", lat_max="65", pdf=True)
        rows = [{
            "variable": "temperature",
            "model_variable": "thetao",
            "selected": ["nsbc", "ices"],
            "point": {"ices": {"point_time_res": "month", "start": "2011", "end": ""}},
        }]
        status, _ = _post(page.url, "/write", {"rows": rows, "settings": form})
        mapping, selection, settings, point_options, gridded_options = page.written[0]

        assert status == 200
        assert (settings["cores"], settings["pdf"]) == (1, True)
        assert (settings["lon_lim"], settings["lat_lim"]) == ([-20, 10], [40, 65])
        assert point_options == {
            ("temperature", "ices"): {
                "start": 2011, "end": None, "point_time_res": ["month"], "vertical": None
            }
        }
        assert gridded_options == {}

    def test_gridded_options_are_written(self, page):
        rows = [{
            "variable": "temperature",
            "model_variable": "thetao",
            "selected": ["cobe2", "nsbc"],
            "gridded": {
                "cobe2": {"start": "2012", "end": "", "vertical": False},
                "nsbc": {"start": "", "end": "", "vertical": True},
            },
        }]
        status, _ = _post(page.url, "/write", {"rows": rows})

        assert status == 200
        assert page.written[0][4] == {
            ("temperature", "cobe2"): {"start": 2012, "end": None, "vertical": None},
            ("temperature", "nsbc"): {"start": None, "end": None, "vertical": True},
        }

    def test_gridded_options_that_cannot_be_used_are_sent_back(self, page):
        rows = [{
            "variable": "temperature",
            "model_variable": "thetao",
            "selected": ["woa23"],
            "gridded": {"woa23": {"start": "2011", "end": "2016"}},
        }]
        status, reply = _post(page.url, "/write", {"rows": rows})

        assert status == 400
        assert reply["gridded_errors"] == {
            "temperature": {
                "woa23": {"end": "These years must sit inside one WOA23 decade, e.g. 2005–2014."}
            }
        }
        assert page.written == []

    def test_climatologies_are_given_the_years_for_climatologies(self, page):
        rows = [{
            "variable": "temperature",
            "model_variable": "thetao",
            "selected": ["cobe2", "nsbc"],
            "gridded": {"cobe2": {}, "nsbc": {}},
        }]
        form = dict(default_form(1990, 2020), clim_start="2000", clim_end="2010")
        status, _ = _post(page.url, "/write", {"rows": rows, "settings": form})

        assert status == 200
        # not COBE2, which is a time series
        assert page.written[0][4] == {
            ("temperature", "nsbc"): {"start": 2000, "end": 2010, "vertical": None},
        }

    def test_woa23_temperature_is_given_years_when_none_sit_inside_a_decade(self, page):
        rows = [{
            "variable": "temperature",
            "model_variable": "thetao",
            "selected": ["woa23"],
            "gridded": {"woa23": {}},
        }]
        form = dict(default_form(1990, 2020), clim_start="2000", clim_end="2010")
        status, reply = _post(page.url, "/write", {"rows": rows, "settings": form})

        assert status == 400
        assert set(reply["gridded_errors"]["temperature"]["woa23"]) == {"start", "end"}
        assert page.written == []

        form.update(woa23_start="2005", woa23_end="2014")
        status, _ = _post(page.url, "/write", {"rows": rows, "settings": form})

        assert status == 200
        assert page.written[0][4] == {
            ("temperature", "woa23"): {"start": 2005, "end": 2014, "vertical": None},
        }

    def test_settings_that_cannot_be_used_are_sent_back(self, page):
        form = dict(default_form(2011, 2012), cores="0", lon_min="-20")
        status, reply = _post(page.url, "/write", {"rows": [], "settings": form})

        assert status == 400
        assert set(reply["setting_errors"]) == {"cores", "lon_max", "lat_min", "lat_max"}
        assert page.written == []

    def test_the_data_store_check_is_answered_here_too(self, page, tmp_path):
        status, reply = _post(page.url, "/data_store_check", {})
        assert status == 400 and "no data store" in reply["error"]
        folder = tmp_path / "store"
        folder.mkdir()
        user_settings.save({"data_store": str(folder)})
        (folder / "oceanval_datastore" / "mine.txt").write_text("mine")
        status, reply = _post(page.url, "/data_store_check", {"data_store": "/"})
        assert status == 200
        assert reply["check"]["problems"] == 1
        assert (folder / "oceanval_datastore" / "mine.txt").read_text() == "mine"

    def test_point_options_that_cannot_be_used_are_sent_back(self, page):
        rows = [{
            "variable": "temperature",
            "model_variable": "thetao",
            "selected": ["ices"],
            "point": {"ices": {"start": "2020"}},
        }]
        status, reply = _post(page.url, "/write", {"rows": rows})

        assert status == 400
        assert reply["point_errors"] == {
            "temperature": {"ices": {"start": "These years miss the 2011–2012 being matched up."}}
        }

    def test_an_unticked_datasets_options_are_ignored(self, page):
        # the page only shows them for ticked datasets
        rows = [{
            "variable": "temperature",
            "model_variable": "thetao",
            "selected": ["nsbc"],
            "point": {"ices": {"start": "nonsense"}},
        }]
        status, _ = _post(page.url, "/write", {"rows": rows})

        assert status == 200
        assert page.written[0][3] == {}

    def test_an_unticked_gridded_datasets_options_are_ignored(self, page):
        rows = [{
            "variable": "temperature",
            "model_variable": "thetao",
            "selected": ["nsbc"],
            "gridded": {"cobe2": {"start": "nonsense"}},
        }]
        status, _ = _post(page.url, "/write", {"rows": rows})

        assert status == 200
        assert page.written[0][4] == {}


class TestSettingsChecks:
    AVAILABLE = {"thetao", "e3t"}

    def check(self, fvcom=False, **changes):
        return check_settings(dict(default_form(2011, 2012), **changes), self.AVAILABLE, fvcom)

    def test_the_boxes_start_at_the_defaults(self):
        settings, errors = self.check()

        assert errors == {}
        assert settings == default_settings(2011, 2012)

    def test_no_years_is_fine_when_nothing_needs_them(self):
        settings, errors = self.check(start="", end="", clim_start="1990", clim_end="2000")

        assert errors == {}
        assert settings["start"] is None and settings["end"] is None
        assert settings["clim_years"] == (1990, 2000)

    def test_one_year_alone_is_still_an_error(self):
        _, errors = self.check(start="2011", end="")

        assert "end" in errors

    def test_core_count_cannot_exceed_system_cores(self):
        cpu_count = recipes_gui.os.cpu_count()
        if not cpu_count:
            pytest.skip("system core count is unavailable")
        _, errors = self.check(cores=str(cpu_count + 1))

        assert errors["cores"] == f"This machine has {cpu_count} cores. Choose {cpu_count} or fewer."

    @pytest.mark.parametrize(
        "changes, errors",
        [
            ({"start": "2011.5"}, {"start"}),
            ({"end": "2010"}, {"end"}),
            ({"lon_min": "-20"}, {"lon_max", "lat_min", "lat_max"}),
            ({"lon_min": "x", "lon_max": "1", "lat_min": "2", "lat_max": "3"}, {"lon_min"}),
            ({"lon_min": "5", "lon_max": "1", "lat_min": "2", "lat_max": "3"}, {"lon_max"}),
            ({"lon_min": "0", "lon_max": "1", "lat_min": "-95", "lat_max": "3"}, {"lat_min"}),
            ({"missing_to": "5"}, {"missing_from"}),
            ({"missing_from": "5", "missing_to": "1"}, {"missing_to"}),
            ({"missing_from": "inf"}, {"missing_from"}),
            ({"cores": "0"}, {"cores"}),
            ({"thickness": "not_there"}, {"thickness"}),
            ({"point_time_res": "week"}, {"point_time_res"}),
            ({"subregions": "arctic"}, {"subregions"}),
        ],
    )
    def test_boxes_that_cannot_be_used(self, changes, errors):
        assert set(self.check(**changes)[1]) == errors

    def test_the_report_is_concise_unless_unticked(self):
        assert self.check()[0]["concise"] is True
        assert self.check(concise=False)[0]["concise"] is False
        assert self.check(concise="true")[0]["concise"] is True
        assert self.check(concise="false")[0]["concise"] is False

    def test_the_matchups_to_overwrite_can_be_a_list(self):
        # as the oceanval window's simulation step chooses them, which the hidden tick leaves alone
        assert self.check(overwrite=["temperature/COBE2"])[0]["overwrite"] == ["temperature/COBE2"]
        assert self.check(overwrite=[])[0]["overwrite"] is False
        assert self.check(overwrite=False)[0]["overwrite"] is False
        assert self.check(overwrite=True)[0]["overwrite"] is True

    def test_the_detail_choice_must_be_known(self):
        assert self.check(concise="verbose")[1] == {"concise": "Choose concise or detailed."}

    def test_the_file_filters_create_recipes_was_given(self):
        form = default_form(2011, 2012, ["ptrc", "5d"], ["grid_T"])
        settings, errors = check_settings(form, self.AVAILABLE)

        assert (form["exclude"], form["require"]) == ("ptrc 5d", "grid_T")
        assert errors == {}
        assert (settings["exclude"], settings["require"]) == (["ptrc", "5d"], ["grid_T"])

    def test_the_subset_is_all_four_limits(self):
        settings, _ = self.check(lon_min="-20", lon_max="10.5", lat_min="40", lat_max="65")
        assert (settings["lon_lim"], settings["lat_lim"]) == ([-20, 10.5], [40, 65])

    @pytest.mark.parametrize(
        "low, high, value", [("0", "", 0), ("0", "1e20", [0, 1e20])]
    )
    def test_as_missing_is_a_value_or_a_range(self, low, high, value):
        assert self.check(missing_from=low, missing_to=high)[0]["as_missing"] == value

    @pytest.mark.parametrize("typed", ["z_level", "z-level", "z level"])
    def test_z_level_however_it_is_written(self, typed):
        assert self.check(thickness=typed)[0]["thickness"] == "z_level"

    def test_thickness_can_be_a_variable_or_a_file(self, tmp_path):
        assert self.check(thickness="e3t")[0]["thickness"] == "e3t"
        path = tmp_path / "thickness.nc"
        path.write_text("")
        assert self.check(thickness=str(path))[0]["thickness"] == str(path)

    def test_fvcom_needs_no_thickness(self):
        settings, errors = self.check(fvcom=True, thickness="anything")
        assert (settings["thickness"], errors) == (None, {})

    def test_fvcom_settings_start_at_matchups_defaults(self):
        form = default_form(2011, 2012)
        assert [form[x] for x in ("fvcom_res", "fvcom_min_depth", "fvcom_max_depth", "fvcom_dz")] == [
            "0.05", "0", "150", "5",
        ]
        settings, errors = self.check(fvcom=True)
        assert errors == {}
        assert settings == default_settings(2011, 2012)

    def test_fvcom_settings_are_numbers_kept_whole_when_they_are(self):
        settings, errors = self.check(
            fvcom=True, fvcom_res="0.1", fvcom_min_depth="10", fvcom_max_depth="200.0", fvcom_dz="2.5"
        )
        assert errors == {}
        assert [settings[x] for x in ("fvcom_res", "fvcom_min_depth", "fvcom_max_depth", "fvcom_dz")] == [
            0.1, 10, 200, 2.5,
        ]
        assert isinstance(settings["fvcom_max_depth"], int)

    @pytest.mark.parametrize(
        "changes, box",
        [
            (dict(fvcom_res=""), "fvcom_res"),
            (dict(fvcom_res="0"), "fvcom_res"),
            (dict(fvcom_res="fine"), "fvcom_res"),
            (dict(fvcom_min_depth="-1"), "fvcom_min_depth"),
            (dict(fvcom_max_depth="0"), "fvcom_max_depth"),
            (dict(fvcom_min_depth="150"), "fvcom_max_depth"),
            (dict(fvcom_dz="-5"), "fvcom_dz"),
            (dict(fvcom_dz="nan"), "fvcom_dz"),
        ],
    )
    def test_fvcom_settings_that_cannot_be_used(self, changes, box):
        settings, errors = self.check(fvcom=True, **changes)
        assert set(errors) == {box}

    def test_other_output_is_not_asked_about_fvcom(self):
        settings, errors = self.check(fvcom_res="nonsense", fvcom_dz="0")
        assert errors == {}
        assert settings == default_settings(2011, 2012)

    def test_out_dir_must_not_be_a_file(self, tmp_path):
        path = tmp_path / "a_file"
        path.write_text("")
        assert set(self.check(out_dir=str(path))[1]) == {"out_dir"}
        assert self.check(out_dir=str(tmp_path / "new"))[0]["out_dir"] == str(tmp_path / "new")

    def test_regional_summaries_can_come_from_a_file(self, tmp_path):
        path = tmp_path / "regions.nc"
        path.write_text("")
        assert self.check(subregions="file", subregions_file=str(path))[0]["subregions"] == str(path)
        assert set(self.check(subregions="file")[1]) == {"subregions_file"}
        assert set(self.check(subregions="file", subregions_file="regions.txt")[1]) == {"subregions_file"}
        assert set(self.check(subregions="file", subregions_file=str(tmp_path / "no.nc"))[1]) == {"subregions_file"}

    def test_file_filters_are_split_on_spaces(self):
        settings, _ = self.check(exclude="restart 5d", require="")
        assert (settings["exclude"], settings["require"]) == (["restart", "5d"], None)


class TestPointOptions:
    def test_empty_boxes_leave_everything_to_the_global_settings(self):
        assert check_point_options({}, (2011, 2012)) == (
            {"start": None, "end": None, "point_time_res": None, "vertical": None},
            {},
        )

    def test_the_options_are_read(self):
        options, errors = check_point_options(
            {"point_time_res": "month,day", "start": "2011", "end": "2012"}, (2011, 2012)
        )
        assert errors == {}
        assert options == {
            "start": 2011, "end": 2012, "point_time_res": ["month", "day"], "vertical": None
        }

    def test_vertical_is_surface_only_until_ticked(self):
        assert check_point_options({}, None)[0]["vertical"] is None
        assert check_point_options({"vertical": True}, None)[0]["vertical"] is True

    @pytest.mark.parametrize(
        "form, error",
        [
            ({"start": "x"}, "start"),
            ({"start": "2012", "end": "2011"}, "end"),
            ({"start": "2015"}, "start"),
            ({"end": "2000"}, "end"),
            ({"point_time_res": "week"}, "point_time_res"),
        ],
    )
    def test_options_that_cannot_be_used(self, form, error):
        assert list(check_point_options(form, (2011, 2012))[1]) == [error]


class TestGriddedOptions:
    def test_empty_boxes_leave_everything_to_the_global_settings(self):
        assert check_gridded_options({}, (2011, 2012), True) == (
            {"start": None, "end": None, "vertical": None},
            {},
        )

    def test_the_years_are_read(self):
        options, errors = check_gridded_options({"start": "2011", "end": "2012"}, (2011, 2012))
        assert errors == {}
        assert options == {"start": 2011, "end": 2012, "vertical": None}

    def test_vertical_is_only_read_where_it_is_offered(self):
        assert check_gridded_options({"vertical": True}, None, True)[0]["vertical"] is True
        assert check_gridded_options({"vertical": True}, None, False)[0]["vertical"] is None

    @pytest.mark.parametrize(
        "form, error",
        [
            ({"start": "x"}, "start"),
            ({"start": "2012", "end": "2011"}, "end"),
            ({"start": "2015"}, "start"),
            ({"end": "2000"}, "end"),
        ],
    )
    def test_years_that_cannot_be_used(self, form, error):
        assert list(check_gridded_options(form, (2011, 2012))[1]) == [error]

    @pytest.mark.parametrize(
        "form, errors",
        [
            ({}, []),
            ({"start": "2011", "end": "2012"}, []),
            ({"start": "2015", "end": "2022"}, []),
            ({"start": "2011"}, ["end"]),
            ({"end": "2012"}, ["start"]),
            ({"start": "2011", "end": "2016"}, ["end"]),
        ],
    )
    def test_woa23_years_must_sit_inside_one_decade(self, form, errors):
        # were they not per decade, all of these could be used
        assert list(check_gridded_options(form, None, True, decadal=True)[1]) == errors
        assert check_gridded_options(form, None, True)[1] == {}

    def test_climatology_years_without_a_woa23_decade_require_woa23_years(self):
        options, errors = check_gridded_options(
            {}, (1990, 2020), True, decadal=True, climatology=True
        )

        assert errors == {
            "start": "WOA23 publishes this per decade, so give both years.",
            "end": "WOA23 publishes this per decade, so give both years.",
        }
        # the years asked for in the window are used
        options, errors = check_gridded_options(
            {}, (1990, 2020), True, decadal=True, climatology=True, woa_years=(2005, 2014)
        )
        assert errors == {}
        assert (options["start"], options["end"]) == (2005, 2014)
        # the dataset's own are used before them
        options, errors = check_gridded_options(
            {"start": "1995", "end": "2004"}, (1990, 2020), True, decadal=True,
            climatology=True, woa_years=(2005, 2014),
        )
        assert errors == {}
        assert (options["start"], options["end"]) == (1995, 2004)

    def test_the_years_for_climatologies_are_given_to_climatologies_only(self):
        options, errors = check_gridded_options(
            {}, (1990, 2020), climatology=True, clim_years=(2000, 2010)
        )
        assert errors == {}
        assert (options["start"], options["end"]) == (2000, 2010)
        # not to a time series
        options, _ = check_gridded_options({}, (1990, 2020), clim_years=(2000, 2010))
        assert (options["start"], options["end"]) == (None, None)
        # nor over a dataset's own
        options, _ = check_gridded_options(
            {"start": "2001"}, (1990, 2020), climatology=True, clim_years=(2000, 2010)
        )
        assert (options["start"], options["end"]) == (2001, None)

    def test_the_years_for_climatologies_decide_whether_woa23_needs_a_decade(self):
        # the years being validated sit in no decade, but those for climatologies do
        options, errors = check_gridded_options(
            {}, (1990, 2020), True, decadal=True, climatology=True, clim_years=(2006, 2012)
        )
        assert errors == {}
        assert (options["start"], options["end"]) == (2006, 2012)
        # the years being validated sit in one, those for climatologies do not
        _, errors = check_gridded_options(
            {}, (2006, 2012), True, decadal=True, climatology=True, clim_years=(1990, 2020)
        )
        assert set(errors) == {"start", "end"}
        # and with neither given, WOA23 takes the decade of the years being validated
        options, errors = check_gridded_options(
            {}, (2006, 2012), True, decadal=True, climatology=True
        )
        assert errors == {} and (options["start"], options["end"]) == (None, None)


class TestClimatologyYearsSetting:
    @staticmethod
    def check(**boxes):
        form = dict(default_form(1990, 2020), **boxes)
        settings, errors = check_settings(form, {"thetao"})
        return settings, {name: errors[name] for name in errors if "clim" in name or "woa" in name}

    def test_they_are_empty_to_start_with_and_then_left_out(self):
        settings, errors = self.check()
        assert errors == {}
        assert "clim_years" not in settings and "woa_years" not in settings

    def test_they_are_given_with_the_settings(self):
        settings, errors = self.check(clim_start="2000", clim_end="2010")
        assert errors == {}
        assert settings["clim_years"] == (2000, 2010)

    @pytest.mark.parametrize(
        "boxes, names",
        [
            ({"clim_start": "2000"}, ["clim_end"]),
            ({"clim_end": "2000"}, ["clim_start"]),
            ({"clim_start": "20x0", "clim_end": "2010"}, ["clim_start"]),
            ({"clim_start": "2010", "clim_end": "2000"}, ["clim_end"]),
            # matchup's years are worked out from every dataset's, so they need
            # not cover these
            ({"clim_start": "2030", "clim_end": "2040"}, []),
            ({"woa23_start": "2005", "woa23_end": "2020"}, ["woa23_end"]),
            ({"woa23_start": "2005"}, ["woa23_end"]),
        ],
    )
    def test_those_that_cannot_be_used_are_refused(self, boxes, names):
        assert list(self.check(**boxes)[1]) == names

class TestHosting:
    def test_the_oceanval_app_shows_the_window_instead(self, monkeypatch):
        monkeypatch.setattr(recipes_gui, "_open_browser", lambda url: pytest.fail("opened"))
        shown = []

        class Host:
            def show_recipes(self, page):
                shown.append(page)
                return "chosen"

        with recipes_gui.hosted_by(Host()):
            result = choose_recipes(
                {"temperature": "thetao"}, "global", {"thetao"}, CONTEXT, lambda *args: None
            )

        assert result == "chosen"
        [page] = shown
        # it has no server of its own
        assert page._server is None
        assert page.context == CONTEXT
        assert recipes_gui._host is None

    VERTICAL = [{
        "variable": "temperature",
        "model_variable": "thetao",
        "selected": ["nsbc"],
        "gridded": {"nsbc": {"start": "", "end": "", "vertical": True}},
    }]

    def app_page(self, own_vertical=False, fvcom=False):
        """The window as the oceanval app shows it."""
        written = []
        page = RecipePage(
            recipe_rows({"temperature": "thetao"}, "nwes"),
            {"thetao"},
            dict(CONTEXT, fvcom=fvcom, app={"action": "matchup", "own_vertical": own_vertical}),
            lambda *chosen: written.append(chosen) or "/out/matchup.py",
        )
        page.written = written
        return page

    def test_the_app_always_asks(self):
        page = self.app_page()
        status, _ = page.submit({"rows": [], "settings": dict(page.form, ask=False)})

        assert status == 200
        assert page.written[0][2]["ask"] is True

    def test_in_the_app_vertical_needs_a_thickness(self):
        page = self.app_page()
        status, reply = page.submit({"rows": self.VERTICAL})

        assert status == 400
        assert reply["setting_errors"] == {"thickness": recipes_gui.THICKNESS_NEEDED}
        assert page.written == []
        settings = dict(page.form, thickness="z_level")
        assert page.submit({"rows": self.VERTICAL, "settings": settings})[0] == 200

    def own_page(self, own, app=None):
        """The window with the user's own data, whose script is written
        with what it holds then."""
        written = []
        page = RecipePage(
            recipe_rows({"temperature": "thetao"}, "global", own_data=own),
            {"thetao"},
            dict(CONTEXT, own_data=own, **({"app": app} if app else {})),
            lambda *chosen: written.append(copy.deepcopy(own)) or "/out/matchup.py",
        )
        page.written = written
        return page

    def test_your_own_datas_options_are_written_into_it(self, tmp_path):
        (tmp_path / "obs.csv").write_text("observation,lon,lat,depth,year,month,day\n1,3,56,1,2011,1,2\n")
        own = {
            "point": [{"name": "temperature", "source": "pygetm", "model_variable": "thetao",
                       "obs_path": str(tmp_path), "start": 2000, "binning": [0.5, 0.5]}],
            "gridded": [],
        }
        page = self.own_page(own)
        sent = {"point": {"0": {"point_time_res": "year,month", "start": "", "end": "2012", "vertical": True}}}
        status, _ = page.submit({"rows": [], "own": sent})

        assert status == 200
        # into the same lists, which the script is written from; what the
        # window has no box for is kept, and an emptied box goes
        assert page.written[0]["point"] == own["point"] == [
            {"name": "temperature", "source": "pygetm", "model_variable": "thetao",
             "obs_path": str(tmp_path), "end": 2012, "binning": [0.5, 0.5],
             "point_time_res": ["year", "month"], "vertical": True}
        ]
        # shown again with them
        (row,) = [row for row in page._rows_now() if row["own"]]
        assert row["own"][0]["opts"] == {"start": "", "end": "2012", "vertical": True,
                                         "point_time_res": "year,month"}

    def test_your_own_datas_options_are_checked(self, tmp_path):
        (tmp_path / "obs.csv").write_text("observation,lon,lat,year,month,day\n1,3,56,2011,1,2\n")
        given = {"name": "temperature", "source": "pygetm", "model_variable": "thetao",
                 "obs_path": str(tmp_path)}
        own = {"point": [dict(given)], "gridded": []}
        page = self.own_page(own)

        # as a recipe's: these years miss 2011-2012
        status, reply = page.submit({"rows": [], "own": {"point": {"0": {"start": "1990", "end": "1995"}}}})
        assert status == 400
        assert set(reply["own_errors"]["point"]["0"]) == {"end"}
        # and as the own data step's: these csv files have no depth column
        status, reply = page.submit({"rows": [], "own": {"point": {"0": {"vertical": True}}}})
        assert status == 400
        assert "no depth column" in reply["own_errors"]["point"]["0"]["vertical"]
        assert own["point"] == [given]
        assert page.written == []

    def test_a_time_resolution_the_window_does_not_offer_is_kept_while_unchanged(self):
        own = {"point": [{"name": "temperature", "source": "pygetm", "model_variable": "thetao",
                          "obs_path": "/points", "point_time_res": ["year"]}], "gridded": []}
        page = self.own_page(own)
        (row,) = [row for row in page._rows_now() if row["own"]]
        assert row["own"][0]["opts"]["point_time_res"] == "year"
        # unchanged, nothing is checked again, as it was when it was added
        status, _ = page.submit({"rows": [], "own": {"point": {"0": own_options("point", own["point"][0])}}})
        assert status == 200
        assert own["point"][0]["point_time_res"] == ["year"]
        assert check_own_options("point", own["point"][0], {"point_time_res": "day"}, None)[1] == {
            "point_time_res": "Choose one of the options."
        }

    def test_in_the_app_back_keeps_your_own_datas_options(self, tmp_path):
        (tmp_path / "obs.csv").write_text("observation,lon,lat,depth\n1,3,56,1\n")
        own = {"point": [{"name": "temperature", "source": "pygetm", "model_variable": "thetao",
                          "obs_path": str(tmp_path)}], "gridded": []}
        page = self.own_page(own, app={"action": "matchup"})
        page.cancel({"rows": [], "own": {"point": {"0": {"start": "2011", "end": "", "vertical": True}}}})

        assert own["point"][0]["start"] == 2011
        assert own["point"][0]["vertical"] is True

    def test_vertical_in_your_own_data_needs_a_thickness_in_the_app(self, tmp_path):
        (tmp_path / "obs.csv").write_text("observation,lon,lat,depth\n1,3,56,1\n")
        own = {"point": [{"name": "temperature", "source": "pygetm", "model_variable": "thetao",
                          "obs_path": str(tmp_path), "vertical": True}], "gridded": []}
        page = self.own_page(own, app={"action": "matchup", "own_vertical": True})
        assert "thickness" in page.submit({"rows": []})[1]["setting_errors"]
        # unticked in the window, it needs none
        status, _ = page.submit({"rows": [], "own": {"point": {"0": {"vertical": False}}}})
        assert status == 200
        assert "vertical" not in own["point"][0]

    def test_a_row_of_only_your_own_data_is_not_matched_up_by_the_window(self):
        own = {"gridded": [{"name": "zooc", "source": "Zoo", "model_variable": "Z4_c",
                            "obs_path": "/zoo.nc"}]}
        written = []
        page = RecipePage(
            recipe_rows({"temperature": "thetao"}, "global", own_data=own),
            {"thetao", "Z4_c"},
            dict(CONTEXT),
            lambda *chosen: written.append(chosen) or "/out/matchup.py",
        )
        # the script registers it, with its own model variable
        status, _ = page.submit({"rows": [{"variable": "zooc", "model_variable": "Z4_c", "selected": []}]})

        assert status == 200
        assert "zooc" not in written[0][0]

    def test_so_does_your_own_data_through_the_water_column(self):
        assert "thickness" in self.app_page(own_vertical=True).submit({"rows": []})[1]["setting_errors"]
        # FVCOM output is regridded onto z-levels
        assert self.app_page(own_vertical=True, fvcom=True).submit({"rows": []})[0] == 200

    # that the report options are asked for elsewhere in the app, and
    # matchup's own subset stays, is checked in test_app.py's
    # test_the_window_in_a_browser


# the pop-out of the model's variables under the box that has the focus (OceanValGUI.variableBox)
SHOWN = """() => {
    const popover = document.querySelector(".vars__popover:not([hidden])");
    if (!popover) return null;
    return [...popover.querySelectorAll("tbody tr:not([hidden])")].map((row) => [...row.cells].slice(1).map((cell) => cell.textContent));
}"""
TICKED = """() => [...document.querySelectorAll(".vars__popover:not([hidden]) tbody input:checked")]
    .map((box) => box.closest("tr").cells[1].textContent)"""
SELECTED = """() => [...document.querySelectorAll(".vars__popover:not([hidden]) .vars__picked:not([hidden]) li")]
    .map((item) => item.firstChild.textContent)"""


def shown(page):
    """The names of the rows shown in the open pop-out, or None if none is open."""
    rows = page.evaluate(SHOWN)
    return None if rows is None else [name for name, _long_name in rows]


def row(page, name):
    return page.locator(".vars__popover:not([hidden]) tbody tr", has=page.locator(f'input[aria-label="Use {name}"]'))


class TestModelVariablesBox:
    """Each simulation variable box opens the old pop-out of the model's
    variables, which it is the search for: the part being typed, after the
    last + before the caret."""

    def page(self, long_names, available=("thetao", "so", "time")):
        return RecipePage(
            recipe_rows({"temperature": "thetao"}, "global"),
            set(available),
            dict(CONTEXT, long_names=long_names),
            lambda *chosen: "/out/matchup.py",
        )

    def test_the_box_opens_a_table_of_names_and_long_names(self, browser):
        recipe_page = self.page({"thetao": "Sea water temperature", "so": "Sea water salinity"})
        url = recipe_page.start()
        try:
            page = browser.new_page()
            page.goto(url)
            # one box on each page the row is on, and no link to a pop-out beside it
            rows = recipe_rows({"temperature": "thetao"}, "global")
            assert page.locator("#rows-gridded .field__input").count() == sum(bool(r["gridded"]) for r in rows)
            assert page.locator("details.vars").count() == 0
            assert page.evaluate(SHOWN) is None
            box = page.locator("#rows-gridded .field__input").first
            box.fill("")
            box.click()
            assert page.evaluate(SHOWN) == [["so", "Sea water salinity"], ["thetao", "Sea water temperature"], ["time", ""]]
            assert page.locator(".vars__popover:not([hidden]) .vars__shown").text_content() == "3"
            # it sits under the box (or over it, where there is more room), never on it
            pop, under = page.locator(".vars__popover:not([hidden])").bounding_box(), box.bounding_box()
            assert pop["y"] >= under["y"] + under["height"] or pop["y"] + pop["height"] <= under["y"]
            # and closes when the box loses the focus, or on Escape
            page.locator("h1").first.click()
            assert page.evaluate(SHOWN) is None
            box.click()
            box.press("Escape")
            assert page.evaluate(SHOWN) is None
        finally:
            recipe_page.close()

    def test_a_row_with_a_dataset_ticked_is_tinted_teal(self, browser):
        recipe_page = self.page({"thetao": "Sea water temperature"})
        url = recipe_page.start()
        try:
            page = browser.new_page()
            page.goto(url)
            row = page.locator("#rows-gridded tbody tr").first
            box = row.locator(".chip input[type=checkbox]").first
            box.uncheck()
            assert "is-selected" not in row.get_attribute("class")
            plain = row.evaluate("tr => getComputedStyle(tr).backgroundColor")
            box.check()
            assert "is-selected" in row.get_attribute("class")
            assert row.evaluate("tr => getComputedStyle(tr).backgroundColor") != plain
            box.uncheck()
            assert "is-selected" not in row.get_attribute("class")
        finally:
            recipe_page.close()

    def test_what_is_typed_searches_the_names_and_long_names(self, browser):
        recipe_page = self.page({"thetao": "Sea water potential temperature", "so": "Sea water salinity"})
        url = recipe_page.start()
        try:
            page = browser.new_page()
            page.goto(url)
            box = page.locator("#rows-gridded .field__input").first
            box.fill("SALINITY")
            assert shown(page) == ["so"]
            assert page.locator(".vars__popover:not([hidden]) .vars__shown").text_content() == "1 of 3"
            # every word has to match, in either column
            box.fill("sea temp")
            assert shown(page) == ["thetao"]
            box.fill("nonsense")
            assert shown(page) == []
            assert page.locator(".vars__popover:not([hidden]) .vars__none").is_visible()
        finally:
            recipe_page.close()

    def test_the_search_takes_wildcards(self, browser):
        recipe_page = self.page(
            {"P1_Chl": "Diatom chlorophyll", "P2_Chl": "Flagellate chlorophyll", "Chl_total": "Total chlorophyll", "so": "Sea water salinity"},
            available=("P1_Chl", "P2_Chl", "Chl_total", "so"),
        )
        url = recipe_page.start()
        try:
            page = browser.new_page()
            page.goto(url)
            box = page.locator("#rows-gridded .field__input").first
            for text, names in [
                ("P*_Chl", ["P1_Chl", "P2_Chl"]),
                ("p?_chl", ["P1_Chl", "P2_Chl"]),
                ("chl_*", ["Chl_total"]),
                ("flag*phyll", ["P2_Chl"]),
                ("P*_Chl diatom", ["P1_Chl"]),
                # a wildcard does not run from a name into its long_name
                ("P1*Diatom", []),
                ("P(1*", []),
            ]:
                box.fill(text)
                assert shown(page) == names, text
        finally:
            recipe_page.close()

    def test_names_typed_are_ticked_and_a_plus_starts_a_new_search(self, browser):
        recipe_page = self.page(
            {"P1_Chl": "Diatom chlorophyll", "P2_Chl": "Flagellate chlorophyll", "so": "Sea water salinity"},
            available=("P1_Chl", "P2_Chl", "so"),
        )
        url = recipe_page.start()
        try:
            page = browser.new_page()
            page.goto(url)
            box = page.locator("#rows-gridded .field__input").first
            box.fill("")
            box.press_sequentially("P1_Chl+")
            # the + is typed as it is, P1_Chl is ticked, and every row is shown for the next
            assert box.input_value() == "P1_Chl+"
            assert page.evaluate(TICKED) == ["P1_Chl"]
            assert shown(page) == ["P1_Chl", "P2_Chl", "so"]
            assert page.evaluate(SELECTED) == ["P1_Chl Diatom chlorophyll"]
            box.press_sequentially("fla")
            assert shown(page) == ["P2_Chl"]
            # a click replaces the half-typed search, and every row is shown again
            row(page, "P2_Chl").locator("td").nth(2).click()
            assert box.input_value() == "P1_Chl+P2_Chl"
            assert page.evaluate(TICKED) == ["P1_Chl", "P2_Chl"]
            assert shown(page) == ["P1_Chl", "P2_Chl", "so"]
            assert page.locator(".vars__popover:not([hidden]) .vars__ticked").text_content() == "2 ticked, summed"
            # the focus stays in the box, so the pop-out stays open
            assert box.evaluate("node => node === document.activeElement")
        finally:
            recipe_page.close()

    def test_clicks_tick_and_untick(self, browser):
        recipe_page = self.page({"thetao": "Sea water potential temperature", "so": "Sea water salinity"})
        url = recipe_page.start()
        try:
            page = browser.new_page()
            page.goto(url)
            box = page.locator("#rows-gridded .field__input").first
            box.fill("")
            box.click()
            row(page, "so").locator("td").nth(1).click()
            row(page, "thetao").locator('input[type="checkbox"]').click()
            assert box.input_value() == "so+thetao"
            # a ticked row is unticked by a click, and taken out of the box
            row(page, "so").click()
            assert box.input_value() == "thetao"
            assert page.evaluate(TICKED) == ["thetao"]
            # and by its x in Selected
            page.locator(".vars__popover:not([hidden])").get_by_role("button", name="Remove thetao").click()
            assert box.input_value() == ""
            assert page.locator(".vars__popover:not([hidden]) .vars__picked").is_hidden()
        finally:
            recipe_page.close()

    def test_selected_lists_the_box_and_flags_names_not_in_the_output(self, browser):
        recipe_page = self.page({"thetao": "Sea water potential temperature", "so": "Sea water salinity"})
        url = recipe_page.start()
        try:
            page = browser.new_page()
            page.goto(url)
            box = page.locator("#rows-gridded .field__input").first
            box.fill("time+missing+")
            assert page.evaluate(SELECTED) == ["time", "missing not in the simulation output"]
            picked = page.locator(".vars__popover:not([hidden]) .vars__picked")
            assert picked.bounding_box()["y"] < page.locator(".vars__popover:not([hidden]) .vars__scroll").bounding_box()["y"]
        finally:
            recipe_page.close()

    def test_keys_move_through_the_rows(self, browser):
        recipe_page = self.page({"thetao": "Sea water temperature", "so": "Sea water salinity"})
        url = recipe_page.start()
        try:
            page = browser.new_page()
            page.goto(url)
            box = page.locator("#rows-gridded .field__input").first
            box.fill("")
            box.click()
            # nothing is highlighted to start, so Enter changes nothing
            box.press("Enter")
            assert box.input_value() == ""
            box.press("ArrowDown")
            box.press("ArrowDown")
            box.press("Enter")
            assert box.input_value() == "thetao"
        finally:
            recipe_page.close()


class TestThicknessBox:
    """The thickness box opens the same pop-out, with z_level first, and takes one only."""

    def page(self, fvcom=False):
        long_names = {"e3t": "Cell thickness", "thetao": "Sea water temperature", "so": "Sea water salinity"}
        return RecipePage(
            recipe_rows({"temperature": "thetao"}, "global"),
            {"e3t", "thetao", "so"},
            # a thickness is only asked for when a dataset needs one
            dict(CONTEXT, long_names=long_names, fvcom=fvcom, app={"action": "matchup", "own_vertical": True}),
            lambda *chosen: "/out/matchup.py",
        )

    def test_it_lists_z_level_and_the_variables_and_takes_one(self, browser):
        recipe_page = self.page()
        url = recipe_page.start()
        try:
            page = browser.new_page()
            page.goto(url)
            box = page.locator("#s-thickness")
            box.click()
            assert page.evaluate(SHOWN) == [
                ["z_level", ""], ["e3t", "Cell thickness"], ["so", "Sea water salinity"], ["thetao", "Sea water temperature"]]
            box.fill("thick")
            assert shown(page) == ["e3t"]
            row(page, "e3t").click()
            assert box.input_value() == "e3t"
            # one only: the pop-out closes on a pick
            assert page.evaluate(SHOWN) is None
            # opened again, every row is shown, with the box's ticked; another replaces it
            box.click()
            assert page.evaluate(TICKED) == ["e3t"]
            assert len(shown(page)) == 4
            row(page, "so").click()
            assert box.input_value() == "so"
            # a + is not a sum here
            box.fill("e3t+so")
            assert shown(page) == []
        finally:
            recipe_page.close()

    def test_the_fvcom_regridding_is_only_asked_of_fvcom_output(self, browser):
        for fvcom in (False, True):
            recipe_page = self.page(fvcom=fvcom)
            url = recipe_page.start()
            try:
                page = browser.new_page()
                page.goto(url)
                assert page.locator("#group-fvcom").is_visible() == fvcom
                if fvcom:
                    values = [page.locator(f"#s-{x}").input_value() for x in
                              ("fvcom_res", "fvcom_min_depth", "fvcom_max_depth", "fvcom_dz")]
                    assert values == ["0.05", "0", "150", "5"]
                    page.locator("#s-fvcom_dz").fill("0")
                    assert "more than 0" in page.locator("#g-fvcom").inner_text()
                    page.locator("#s-fvcom_dz").fill("10")
                    page.locator("#s-fvcom_max_depth").fill("300")
                    assert "more than 0" not in page.locator("#g-fvcom").inner_text()
            finally:
                recipe_page.close()

    def test_it_is_not_offered_for_fvcom(self, browser):
        recipe_page = self.page(fvcom=True)
        url = recipe_page.start()
        try:
            page = browser.new_page()
            page.goto(url)
            assert page.locator("#s-thickness").is_disabled()
        finally:
            recipe_page.close()


class TestChooseRecipes:
    def test_it_waits_for_the_page_to_be_written(self, monkeypatch, capsys):
        written = []

        def write(*chosen):
            written.append(chosen)
            return "/out/matchup.py"

        def open_browser(url):
            # the user ticks WOA23 and clicks Write script. Posted here rather
            # than from a thread of its own, whose failure would leave the
            # page waiting for ever
            rows = [{"variable": "temperature", "model_variable": "thetao", "selected": ["woa23"]}]
            assert _post(url, "/write", {"rows": rows})[0] == 200
            return True

        monkeypatch.setattr(recipes_gui, "_can_open_browser", lambda: True)
        monkeypatch.setattr(recipes_gui, "_open_browser", open_browser)
        result = choose_recipes({"temperature": "thetao"}, "global", {"thetao"}, CONTEXT, write)

        assert result == (
            {"temperature": "thetao"},
            {("temperature", "woa23")},
            default_settings(2011, 2012),
            {},
            {},
        )
        assert written == [result]
        assert "open in your web browser" in capsys.readouterr().out

    def test_the_link_is_printed_when_no_browser_can_be_opened(self, monkeypatch, capsys):
        wait = RecipePage.wait

        def cancel_then_wait(page):
            # the user follows the printed link, and cancels
            assert _post(page.url, "/cancel", {})[0] == 200
            return wait(page)

        monkeypatch.setattr(recipes_gui, "_can_open_browser", lambda: False)
        monkeypatch.setattr(RecipePage, "wait", cancel_then_wait)
        result = choose_recipes({}, "global", set(), CONTEXT, lambda *args: "/out")
        printed = capsys.readouterr().out

        assert result is None
        assert "Open this link in a web browser" in printed
        assert re.search(r"http://127\.0\.0\.1:(\d+)/\?token=\S+", printed)
        assert "forward port" in printed

    def test_no_browser_is_opened_without_a_display(self, monkeypatch):
        # webbrowser would fall back on a text browser in the terminal
        monkeypatch.setattr(sys, "platform", "linux")
        for name in (
            "DISPLAY", "WAYLAND_DISPLAY", "BROWSER", "OCEANVAL_BROWSER",
            "SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY",
        ):
            monkeypatch.delenv(name, raising=False)
        assert not recipes_gui._can_open_browser()

        monkeypatch.setenv("DISPLAY", ":1")
        assert recipes_gui._can_open_browser()

    def test_an_x11_forward_over_ssh_is_not_your_browser(self, monkeypatch):
        # MobaXterm sets DISPLAY, so a browser would open on the remote machine
        monkeypatch.setattr(sys, "platform", "linux")
        for name in ("BROWSER", "OCEANVAL_BROWSER"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("DISPLAY", "localhost:10.0")
        monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 51234 10.0.0.9 22")
        assert not recipes_gui._can_open_browser()

        monkeypatch.setenv("OCEANVAL_BROWSER", "1")
        assert recipes_gui._can_open_browser()

        monkeypatch.setenv("OCEANVAL_BROWSER", "0")
        monkeypatch.delenv("SSH_CONNECTION")
        assert not recipes_gui._can_open_browser()

    def test_a_browser_set_by_vs_code_is_used(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        for name in ("DISPLAY", "WAYLAND_DISPLAY"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("BROWSER", "/home/me/.vscode-server/bin/helpers/browser.sh")
        monkeypatch.setenv("SSH_CONNECTION", "10.0.0.2 51234 10.0.0.9 22")
        monkeypatch.delenv("OCEANVAL_BROWSER", raising=False)

        assert recipes_gui._can_open_browser()


def _open_window(browser, tmp_path, monkeypatch, own_data=None):
    """Run create_recipes(gui=True) on a small NEMO-like simulation, whose KD490
    is not identified, and open its window, with the user's own data if given.
    Returns the page, the path the script is written to, and a function
    waiting for create_recipes."""
    values = np.random.rand(1, 3, 2, 2).astype("f4")
    dims = ("time", "deptht", "y", "x")
    dataset = xr.Dataset(
        {
            "thetao": (dims, values, {"long_name": "sea water potential temperature"}),
            # nothing identifies a variable described like this as KD490
            "kd_misc": (dims, values, {"long_name": "light extinction"}),
        }
    )
    dataset["time"] = ("time", [0.0], {"units": "days since 2011-01-01"})
    (tmp_path / "sim").mkdir()
    dataset.to_netcdf(tmp_path / "sim" / "output.nc")

    links = queue.Queue()
    monkeypatch.setattr(recipes_gui, "_can_open_browser", lambda: True)
    monkeypatch.setattr(recipes_gui, "_open_browser", lambda url: links.put(url) or True)
    out = str(tmp_path / "matchup.py")
    returned = {}
    thread = threading.Thread(
        target=lambda: returned.update(
            out=oceanval.create_recipes(
                simdir=str(tmp_path / "sim"), ndown=0, out=out, domain="nwes",
                start=2011, end=2012, ask=False, gui=True, own_data=own_data,
            )
        ),
        daemon=True,
    )
    thread.start()

    page = browser.new_page()
    page.goto(links.get(timeout=120))

    def finished():
        thread.join(timeout=60)
        return returned["out"]

    return page, out, finished


def _continue(page):
    """On from the gridded datasets' page to the point datasets'."""
    assert page.text_content("#page-title") == "Gridded data selection"
    page.click("#write")
    assert page.text_content("#page-title") == "Point data selection"


def test_filling_in_a_variable_in_the_browser(browser, tmp_path, monkeypatch):
    """The page, driven as a user would: fill in the variable that was not
    identified, and write the script."""
    page, out, finished = _open_window(browser, tmp_path, monkeypatch)
    kd490 = page.locator('input[aria-label="Simulation variable for KD490"]')
    occci = page.locator('input[aria-label="OC-CCI, Global, for KD490"]')
    assert kd490.input_value() == ""
    assert occci.is_disabled()

    # a name that is not in the output holds up carrying on
    kd490.fill("kd_mis")
    assert page.is_disabled("#write")
    # a real one unlocks the datasets, ticking what nwes would use - the
    # global OC-CCI, as the shelf has no KD490 dataset of its own
    kd490.fill("kd_misc")
    assert occci.is_enabled()
    assert occci.is_checked()

    _continue(page)
    page.click("#write")
    page.wait_for_selector("#done:not([hidden])")

    assert finished() == out
    assert (
        '\noceanval.add_gridded_comparison(\n    name="kd490",\n'
        '    model_variable="kd_misc",\n'
    ) in open(out).read()


def test_the_two_pages_in_the_browser(browser, tmp_path, monkeypatch):
    """The gridded datasets come first, then the point datasets, with Back
    between them; a row of both is on both, with the same simulation
    variable, and the point page lists the gridded datasets chosen."""
    page, _, _ = _open_window(browser, tmp_path, monkeypatch)
    assert page.text_content("#page-title") == "Gridded data selection"
    assert page.title().startswith("Gridded data selection")
    assert page.locator("#page-point").is_hidden()
    # no simulation path, or anything else, above the pages
    assert page.locator("#meta").count() == 0
    assert page.text_content("#cancel") == "Cancel"
    assert page.text_content("#write") == "Continue"
    gridded = page.locator("#rows-gridded")
    assert page.locator("#page-gridded thead th").all_text_contents() == [
        "Environmental variable", "What is the simulation variable name?", "What gridded datasets do you want?Deselect all",
    ]
    assert gridded.locator('input[aria-label="ICES, Northwest European Shelf, for Temperature"]').count() == 0

    _continue(page)
    assert page.locator("#page-gridded").is_hidden()
    assert page.text_content("#cancel") == "Back"
    assert page.text_content("#write") == "Write script"
    assert page.locator("#page-point thead th").all_text_contents() == [
        "Environmental variable", "What is the simulation variable name?", "What point datasets do you want?Deselect all",
        "Gridded data selected",
    ]
    temperature = page.locator("#rows-point tr", has=page.locator('input[aria-label="Simulation variable for Temperature"]'))
    assert temperature.locator(".chosen").text_content() == "Yes: NSBC"
    # KD490 has no point dataset, so is not on this page
    assert page.locator('#rows-point input[aria-label="Simulation variable for KD490"]').count() == 0

    page.click("#cancel")
    assert page.text_content("#page-title") == "Gridded data selection"
    page.uncheck('input[aria-label="NSBC, Northwest European Shelf, for Temperature"]')
    page.check('input[aria-label="COBE2, Global, for Temperature"]')
    page.locator('#rows-gridded input[aria-label="Simulation variable for Temperature"]').fill("kd_misc")
    _continue(page)
    assert temperature.locator(".chosen").text_content() == "Yes: COBE2"
    assert page.locator('#rows-point input[aria-label="Simulation variable for Temperature"]').input_value() == "kd_misc"
    page.click("#cancel")
    page.click("#clear-gridded")
    _continue(page)
    assert temperature.locator(".chosen").text_content() == "None"


def test_the_info_beside_a_gridded_source_in_the_browser(browser, tmp_path, monkeypatch):
    """An i beside each built-in gridded source gives the years it covers and
    a link to its page."""
    page, _, _ = _open_window(browser, tmp_path, monkeypatch)
    nsbc = page.locator("#rows-gridded .dataset", has=page.locator(
        'input[aria-label="NSBC, Northwest European Shelf, for Temperature"]'))
    info = nsbc.locator(".dataset__info")
    assert info.locator(".opts__help-popover").is_hidden()
    info.locator("summary").click()
    assert info.locator(".opts__help-popover").is_visible()
    assert "Years covered: 1960–2014" in info.inner_text()
    link = info.locator("a")
    assert link.get_attribute("href") == "https://www.geo.uni-hamburg.de/en/service/icdc/data/ocean/nsbc.html"
    assert link.get_attribute("target") == "_blank"
    page.keyboard.press("Escape")
    assert info.locator(".opts__help-popover").is_hidden()
    # every built-in gridded source has one, and the point ones none
    assert page.locator("#rows-gridded .dataset__info").count() == page.locator("#rows-gridded .chip").count()
    assert page.locator("#rows-point .dataset__info").count() == 0


def test_settings_in_the_browser(browser, tmp_path, monkeypatch):
    """A partial subset holds up writing; the settings and a point dataset's
    own options reach the script."""
    page, out, finished = _open_window(browser, tmp_path, monkeypatch)

    # the gridded page asks the years for climatologies, not those to validate
    assert page.locator("#page-gridded #s-clim_start").is_visible()
    assert page.locator("#s-point_start").is_hidden()
    assert page.locator("#settings-title").count() == 0
    assert page.locator("#s-clim_start").locator("xpath=ancestor::fieldset").locator("legend").text_content() == (
        "What simulation years do you want to use to compare with climatologies?")
    # no dataset is set to Vertical yet, so no thickness is asked for
    assert page.locator("#group-thickness").is_hidden()
    # a gridded dataset's years and Vertical, which only depth-resolved ones offer:
    # the years create_recipes was given, in bold until typed over
    first_nsbc = page.locator('input[aria-label="First year of NSBC observations for Temperature"]')
    assert first_nsbc.input_value() == "2011"
    assert "is-oceanval" in first_nsbc.get_attribute("class")
    first_nsbc.fill("2012")
    assert "is-oceanval" not in first_nsbc.get_attribute("class")
    page.check('input[aria-label="Validate NSBC observations for Temperature through the full water column"]')
    assert page.locator("#page-gridded #group-thickness").is_visible()
    assert page.locator("#s-thickness").evaluate("node => node.previousElementSibling.textContent") == "Is the simulation z-level or is thickness supplied?"
    assert page.locator(
        'input[aria-label="Validate COBE2 observations for Temperature through the full water column"]'
    ).count() == 0
    _continue(page)

    assert page.locator("#s-point_start-label").text_content().startswith("First")
    assert page.locator("#s-point_end-label").text_content().startswith("Last")
    assert page.locator("#s-point_start").locator("xpath=ancestor::fieldset").locator("legend").text_content() == "Settings for all point matchups"
    # ICES is ticked, so they are needed
    page.fill("#s-point_start", "")
    page.fill("#s-point_end", "")
    assert "First must be a year, e.g. 2011." in page.text_content("#g-years")
    assert "Last must be a year, e.g. 2011." in page.text_content("#g-years")
    assert page.locator("#s-point_start-required").is_visible()
    page.fill("#s-point_start", "2011")
    page.fill("#s-point_end", "2012")
    assert page.locator("#s-missing_from").evaluate("node => node.closest('.g-row').querySelector('.g-label').textContent") == "What should be treated as missing values?"
    assert page.locator("#s-point_time_res").evaluate("node => node.closest('.g-row').querySelector('.g-label').textContent") == "Match point observations by"
    # a "?" beside it explains each option, and only opens when asked
    assert page.locator("#help-point_time_res").evaluate("node => node.open") is False
    assert not page.locator("#help-point_time_res .opts__help-popover").is_visible()
    page.click("#help-point_time_res > summary")
    assert page.locator("#help-point_time_res").evaluate("node => node.open") is True
    popover = page.locator("#help-point_time_res .opts__help-popover")
    assert popover.is_visible()
    for label in ("Year, month, day", "Year, month", "Month, day", "Month"):
        assert label in popover.locator("dt").all_text_contents()
    box = popover.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"]
    page.click("#help-point_time_res > summary")
    assert not popover.is_visible()

    # create_recipes on its own asks the rest on this page, under the datasets
    assert page.text_content("#settings-more-title") == "Other settings"
    assert page.locator("#settings-more .settings__grid > .settings__column").count() == 4
    assert page.locator("#group-subset legend").text_content() == "Do you want a spatial subset?"
    assert page.locator("#group-detail legend").count() == 0
    assert page.locator("#group-regional legend").count() == 0
    assert page.locator("#s-subregions").evaluate("node => node.closest('label').querySelector('.g-label').textContent") == "Which region do you want to use for subregion analysis?"
    page.fill("#s-lon_min", "-20")
    assert page.is_disabled("#write")
    page.fill("#s-lon_max", "10")
    page.fill("#s-lat_min", "40")
    page.fill("#s-lat_max", "65")
    cpu_count = page.evaluate("JSON.parse(document.getElementById('state').textContent).cpu_count")
    # a drop-down of 1 to the machine's cores, with the default chosen
    assert page.locator("#s-cores").evaluate("node => node.tagName") == "SELECT"
    assert page.locator("#s-cores option").all_text_contents() == [str(n) for n in range(1, cpu_count + 1)]
    assert page.input_value("#s-cores") == str(min(6, cpu_count))
    # valid on any machine, and not the default, so it is written
    valid_cores = 1
    page.select_option("#s-cores", str(valid_cores))
    assert page.locator(".cores-row .g-label").text_content() == "How many cores do you want to use?"
    assert page.text_content("#group-report legend") == "Which additional validation report formats do you want?"
    assert page.locator("#s-pdf").bounding_box()["y"] == page.locator("#s-word").bounding_box()["y"]
    assert page.locator("#s-concise").locator("option").all_text_contents() == ["Concise", "Detailed"]
    assert page.text_content("#group-detail .g-label") == "How detailed do you want the validation to be?"
    page.select_option("#s-concise", "false")
    assert page.is_enabled("#write")
    # how each point dataset is matched, filled in from the box above, with no global setting
    match_ices = 'select[aria-label="Match ICES observations for Temperature by"]'
    assert page.locator(f"{match_ices} option").all_text_contents()[0] != "Global setting"
    assert page.input_value(match_ices) == "year,month,day"
    page.select_option("#s-point_time_res", "year,month")
    assert page.input_value(match_ices) == "year,month"
    page.select_option(match_ices, "month")
    # the user's own choice stays when the box above changes again
    page.select_option("#s-point_time_res", "year,month,day")
    assert page.input_value(match_ices) == "month"
    page.click("#write")
    page.wait_for_selector("#done:not([hidden])")

    assert finished() == out
    script = open(out).read()
    assert f"    lon_lim=[-20, 10],\n    lat_lim=[40, 65],\n    cores={valid_cores},\n" in script
    assert "    concise=False,\n" in script
    # each dataset's years are given, as the page fills them in
    assert '    recipe={"temperature": "ices"},\n    start=2011,\n    end=2012,\n    point_time_res=["month"],\n' in script
    assert (
        '    recipe={"temperature": "nsbc"},\n'
        '    start=2012,\n'
        '    end=2012,\n'
        '    climatology=True,\n'
        '    vertical=True,'
    ) in script


def test_only_the_settings_the_datasets_need_are_shown_in_the_browser(browser, tmp_path, monkeypatch):
    """The thickness is asked for datasets validated through the water column:
    on the gridded datasets' page if one of them is, else on the point
    datasets'. The years to validate are needed only for point datasets."""
    page, _, _ = _open_window(browser, tmp_path, monkeypatch)
    nsbc_vertical = 'input[aria-label="Validate NSBC observations for Temperature through the full water column"]'
    ices_vertical = 'input[aria-label="Validate ICES observations for Temperature through the full water column"]'

    assert page.locator("#group-thickness").is_hidden()
    page.check(nsbc_vertical)
    assert page.locator("#page-gridded #group-thickness").is_visible()
    page.uncheck(nsbc_vertical)
    assert page.locator("#group-thickness").is_hidden()

    _continue(page)
    # nwes ticks ICES, a point dataset
    assert page.locator("#group-point").is_visible()
    assert page.locator("#s-point_start-required").is_visible()
    page.check(ices_vertical)
    assert page.locator("#page-point #group-thickness").is_visible()
    page.uncheck(ices_vertical)
    assert page.locator("#group-thickness").is_hidden()

    page.click("#clear-point")
    assert page.locator("#s-point_start-required").is_hidden()
    page.fill("#s-point_start", "")
    page.fill("#s-point_end", "")
    # nothing needs them, and the gridded datasets have years of their own
    assert page.is_enabled("#write")


def test_nothing_ticked_can_continue_but_is_told_and_kept_on_the_point_page_in_the_browser(browser, tmp_path, monkeypatch):
    """With no dataset ticked there are no years to validate to ask for, so
    blank years do not disable Continue; clicking it says nothing
    is ticked and keeps the user on the point datasets' page until Return."""
    page, out, finished = _open_window(browser, tmp_path, monkeypatch)
    _continue(page)
    page.fill("#s-point_start", "")
    page.fill("#s-point_end", "")

    assert page.is_enabled("#write")
    page.click("#write")
    assert page.locator("#nothing-ticked").is_visible()
    assert page.locator("#group-point").is_visible()
    page.click("#nothing-return")
    assert page.locator("#nothing-ticked").count() == 0
    assert page.locator("#group-point").is_visible()
    assert not out or not __import__("os").path.exists(out)


def test_the_gridded_years_are_required_in_the_browser(browser, tmp_path, monkeypatch):
    """Every gridded dataset ticked needs its years, typed or filled in from
    the years for climatologies, which are written into those that are
    climatologies and not typed in."""
    page, out, finished = _open_window(browser, tmp_path, monkeypatch)
    cobe2 = 'input[aria-label="COBE2, Global, for Temperature"]'
    first = lambda name: page.locator(f'input[aria-label="First year of {name} observations for Temperature"]')
    last = lambda name: page.locator(f'input[aria-label="Last year of {name} observations for Temperature"]')
    page.check(cobe2)
    first("COBE2").fill("")
    year_row = first("COBE2").locator("xpath=ancestor::div[contains(concat(' ', @class, ' '), ' opts__row ')]")
    assert "is-required" in year_row.get_attribute("class")
    assert page.is_disabled("#write")
    first("COBE2").fill("2011")
    assert "is-required" not in year_row.get_attribute("class")
    assert page.is_enabled("#write")

    # the climatologies take the years for them, COBE2, a time series, does not
    page.fill("#s-clim_start", "2000")
    page.fill("#s-clim_end", "2010")
    assert (first("NSBC").input_value(), last("NSBC").input_value()) == ("2000", "2010")
    assert first("COBE2").input_value() == "2011"
    assert last("COBE2").input_value() == "2012"
    # typed over, the dataset's own stay
    last("NSBC").fill("2009")
    page.fill("#s-clim_end", "2008")
    assert (first("NSBC").input_value(), last("NSBC").input_value()) == ("2000", "2009")

    _continue(page)
    page.click("#write")
    page.wait_for_selector("#done:not([hidden])")
    assert finished() == out
    script = open(out).read()
    block = script.split('recipe={"temperature": "nsbc"},\n')[1].split(")\n")[0]
    assert "    start=2000,\n    end=2009,\n" in block
    block = script.split('recipe={"temperature": "cobe2"},\n')[1].split(")\n")[0]
    assert "    start=2011,\n    end=2012,\n" in block
    # matchup is given every year a dataset is matched up for
    assert "start=2000" in script.split("oceanval.matchup(")[1] and "end=2012" in script.split("oceanval.matchup(")[1]


def test_your_own_data_is_shown_ticked_in_the_browser(browser, tmp_path, monkeypatch):
    """The user's own data is in its variable's row, ticked for good, with
    the options a recipe's dataset has, and a variable only it is for has a
    row of its own. The script registers it whatever is ticked, with the
    options chosen."""
    (tmp_path / "points").mkdir()
    (tmp_path / "points" / "obs.csv").write_text("observation,lon,lat,depth,year,month,day\n1,3,56,1,2011,1,2\n")
    own_data = {
        "point": [{"name": "temperature", "source": "pygetm", "model_variable": "thetao",
                   "obs_path": str(tmp_path / "points")}],
        "gridded": [{"name": "zooc", "source": "Zoo", "model_variable": "kd_misc",
                     "obs_path": str(tmp_path / "zoo.nc"), "obs_variable": "zoo", "climatology": False}],
    }
    page, out, finished = _open_window(browser, tmp_path, monkeypatch, own_data)
    # a variable only the user's gridded data is for, on the gridded page
    zooc = page.locator("tr.is-own")
    assert zooc.count() == 1
    assert zooc.locator(".obs__title").inner_text() == "Zooc"
    assert zooc.locator(".own__model").inner_text() == "kd_misc"
    assert zooc.locator("input.field__input").count() == 0
    assert "Your own data is always matched up, so is ticked in its row" in page.inner_text("#lede-gridded")
    assert page.locator('input[aria-label="Validate Zoo observations for Zooc through the full water column"]').count() == 1
    page.click("#clear-gridded")
    _continue(page)

    pygetm = page.locator('input[aria-label="pygetm, your data, for Temperature: always matched up"]')
    assert pygetm.is_checked()
    assert pygetm.is_disabled()
    assert pygetm.evaluate("node => node.closest('td').dataset.label") == "What point datasets do you want?"
    assert page.locator("label.chip.is-own", has=pygetm).inner_text().split() == ["pygetm", "Your", "data"]
    assert "Your own data is always matched up, so is ticked in its row" in page.inner_text("#lede-point")
    # Zooc's own row lists the user's gridded data beside it
    assert page.locator("#rows-point tr.is-own").count() == 0

    page.click("#clear-point")
    assert pygetm.is_checked()
    assert "Only your own data is ticked" in page.inner_text("#summary")

    # its options, as ICES has them, shown whether or not the row is ticked
    name = "pygetm observations for Temperature"
    assert page.locator(f'select[aria-label="Match {name} by"]').is_visible()
    assert page.locator(f'input[aria-label="First year of {name}"]').input_value() == "2011"
    page.check(f'input[aria-label="Validate {name} through the full water column"]')
    page.select_option(f'select[aria-label="Match {name} by"]', "year,month")
    # checked as a recipe's are
    page.fill(f'input[aria-label="Last year of {name}"]', "1990")
    assert page.is_disabled("#write")
    page.fill(f'input[aria-label="Last year of {name}"]', "2012")

    page.click("#write")
    page.wait_for_selector("#done:not([hidden])")
    assert finished() == out
    script = open(out).read()
    block = script.split('\noceanval.add_point_comparison(\n    name="temperature",\n    source="pygetm",\n')[1].split(")\n")[0]
    assert "    vertical=True,\n" in block
    assert "point_time_res=" in block and '"month"' in block
    assert '\noceanval.add_gridded_comparison(\n    name="zooc",\n' in script


def test_woa23_years_must_sit_in_one_decade_in_the_browser(browser, tmp_path, monkeypatch):
    """WOA23 temperature is published per decade: years for climatologies
    that sit in no decade are written into it, and marked to be changed."""
    page, out, finished = _open_window(browser, tmp_path, monkeypatch)
    page.fill("#s-clim_start", "1990")
    page.fill("#s-clim_end", "2020")
    page.check('input[aria-label="WOA23, Global, for Temperature"]')
    first = page.locator('input[aria-label="First year of WOA23 observations for Temperature"]')
    last = page.locator('input[aria-label="Last year of WOA23 observations for Temperature"]')
    assert (first.input_value(), last.input_value()) == ("1990", "2020")
    assert "is-invalid" in last.get_attribute("class")
    assert "published per decade" in page.inner_text("#g-climate")
    assert "1955–1964, 1965–1974" in page.inner_text("#g-climate")
    assert page.is_disabled("#write")

    first.fill("2005")
    last.fill("2014")
    assert page.is_enabled("#write")
    _continue(page)
    page.click("#write")
    page.wait_for_selector("#done:not([hidden])")
    assert page.locator("#woa-dialog").count() == 0
    assert finished() == out
    block = open(out).read().split('recipe={"temperature": "woa23"},\n')[1].split(")\n")[0]
    assert "    start=2005,\n    end=2014,\n" in block


def test_the_years_for_climatologies_are_used_when_they_fit_a_decade(browser, tmp_path, monkeypatch):
    page, out, finished = _open_window(browser, tmp_path, monkeypatch)
    page.fill("#s-clim_start", "2006")
    page.fill("#s-clim_end", "2012")
    page.check('input[aria-label="WOA23, Global, for Temperature"]')
    assert "published per decade" not in page.inner_text("#g-climate")

    _continue(page)
    page.click("#write")
    page.wait_for_selector("#done:not([hidden])")
    assert finished() == out
    script = open(out).read()
    block = script.split('recipe={"temperature": "woa23"},\n')[1].split(")\n")[0]
    assert "    start=2006,\n    end=2012,\n" in block
    # COBE2 is not ticked, so its block has no years
    block = script.split('recipe={"temperature": "cobe2"},\n')[1].split(")\n")[0]
    assert "start=" not in block
