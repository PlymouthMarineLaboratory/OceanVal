"""The browser never offers what was typed in OceanVal's windows before: every
box has autocomplete="oceanval-none", in the markup and when the pages make it later.
The simulation directories are remembered by OceanVal itself (.oceanvalcache)."""

import json
import pathlib
import re

import os

from oceanval.app import App
from oceanval.recipes_gui import RecipePage, recipe_rows

DATA = pathlib.Path(__file__).resolve().parent.parent / "oceanval" / "data"

CONTEXT = {
    "simdir": "/sim",
    "ndown": 2,
    "domain": "nwes",
    "region": "Northwest European Shelf",
    "regions": ["Northwest European Shelf", "European"],
    "start": 2011,
    "end": 2012,
    "fvcom": False,
    "out": "/out/matchup.py",
}

# the typed boxes on the page whose autocomplete is not "oceanval-none"
REMEMBERING = """() => [...document.querySelectorAll("input, textarea")]
    .filter((box) => box.tagName === "TEXTAREA"
        || !["hidden", "checkbox", "radio", "range", "button", "submit", "reset", "file", "image", "color"].includes(box.type))
    .filter((box) => box.getAttribute("autocomplete") !== "oceanval-none")
    .map((box) => box.id || box.outerHTML.slice(0, 80))"""

# a box made by the page after it has loaded
MADE_LATER = """async () => {
    const box = document.createElement("input");
    box.id = "made-later";
    document.body.append(box);
    await new Promise((done) => setTimeout(done, 0));
    return box.getAttribute("autocomplete");
}"""


def test_off_is_never_used():
    """Chrome takes no notice of autocomplete="off" or "chrome-off" on boxes it takes for address
    fields (a "first" box offered the user's saved addresses); an unknown token fills nothing."""
    for path in DATA.iterdir():
        if path.suffix in {".html", ".js"}:
            assert not re.search(r"""autocomplete\W+(chrome-)?off\b""", path.read_text()), path.name


def test_every_box_in_the_markup_says_oceanval_none():
    for name in ["oceanval_app.html", "create_recipes_gui.html"]:
        text = (DATA / name).read_text()
        markup = text[: text.index('<script type="application/json" id="state">')]
        for tag in re.findall(r"<(?:input|textarea)\b[^>]*>", markup):
            kind = re.search(r'\btype="([^"]+)"', tag)
            if kind and kind.group(1) in {"hidden", "checkbox", "radio", "range"}:
                continue
            assert 'autocomplete="oceanval-none"' in tag, (name, tag)

SHOT = os.environ.get("VALIDATE_INTRO_SHOT")


def test_the_app_window_remembers_nothing(browser, tmp_path):
    app = App(cwd=str(tmp_path))
    app.leftovers_asked = True
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.click("#home-validate")
        page.click('button.choice[data-action="matchup_validate"]')
        page.wait_for_selector("#view-setup:not([hidden])")
        assert page.evaluate(REMEMBERING) == []
        assert page.evaluate(MADE_LATER) == "oceanval-none"
    finally:
        app.close()


def test_the_recipes_window_remembers_nothing(browser):
    recipe_page = RecipePage(
        recipe_rows({"temperature": "thetao"}, "nwes"),
        {"thetao", "so"},
        CONTEXT,
        lambda *chosen: "/out/matchup.py",
    )
    url = recipe_page.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#s-clim_start")
        assert page.evaluate(REMEMBERING) == []
        assert page.evaluate(MADE_LATER) == "oceanval-none"
    finally:
        recipe_page.close()


# the names of the typed boxes: Chrome's list of earlier entries is keyed on them
NAMES = """() => {
    const box = document.createElement("input");
    box.id = "made-later-name";
    document.body.append(box);
    return new Promise((done) => setTimeout(() => done({
        later: box.getAttribute("name"),
        boxes: Object.fromEntries([...document.querySelectorAll("input, textarea")]
            .filter((box) => !["hidden", "checkbox", "radio", "range", "button", "submit", "reset", "file", "image", "color"].includes(box.type))
            .filter((box) => box.id)
            .map((box) => [box.id, box.getAttribute("name")])),
    }), 0));
}"""


def test_typed_boxes_are_renamed_at_random_on_every_page_load(browser):
    """Chrome offers what was typed in a box before, matched on its name; a name that is new for each page
    load (and never the fixed one of the markup) has nothing remembered under it."""
    recipe_page = RecipePage(
        recipe_rows({"temperature": "thetao"}, "nwes"),
        {"thetao", "so"},
        CONTEXT,
        lambda *chosen: "/out/matchup.py",
    )
    url = recipe_page.start()
    try:
        loads = []
        for _ in range(2):
            page = browser.new_page()
            page.goto(url)
            page.wait_for_selector("#s-clim_start")
            loads.append(page.evaluate(NAMES))
            page.close()
        first, second = loads
        assert "climatology-start-year" not in first["boxes"].values()
        assert first["boxes"]["s-clim_start"].startswith("ov-")
        for loaded in loads:
            assert all(name and name.startswith("ov-") for name in loaded["boxes"].values()), loaded["boxes"]
            assert loaded["later"].startswith("ov-")
            assert len(set(loaded["boxes"].values())) == len(loaded["boxes"])
        assert first["boxes"]["s-clim_start"] != second["boxes"]["s-clim_start"]
    finally:
        recipe_page.close()


def test_the_first_time_validate_is_opened_the_data_store_is_mentioned(browser, tmp_path, oceanvalcache):
    app = App(cwd=str(tmp_path))
    app.leftovers_asked = True
    url = app.start()
    try:
        page = browser.new_page()
        page.add_init_script("window.__keepValidateIntro = true")
        page.goto(url)
        page.click("#home-validate")
        page.wait_for_selector("#validate-intro:not([hidden])")
        text = page.inner_text("#validate-intro-text")
        assert "downloads observational data" in text and "Preferences" in text
        # it points to Tools & info, as Preferences is no longer a button of the header
        assert "Tools & info" in text
        assert page.query_selector("#validate-intro-text svg") is None
        assert page.locator(".masthead .brand #settings-open").count() == 0
        page.screenshot(path=SHOT) if SHOT else None
        page.click("#validate-intro-continue")
        page.wait_for_selector("#validate-intro", state="hidden")
        for _ in range(50):
            if "validate" in oceanvalcache.read_text() if oceanvalcache.exists() else False:
                break
            page.wait_for_timeout(100)
        assert json.loads(oceanvalcache.read_text())["seen"].keys() == {"validate"}
        # a page opened later is not told again
        page2 = browser.new_page()
        page2.add_init_script("window.__keepValidateIntro = true")
        page2.goto(url)
        page2.wait_for_selector("#view-start:not([hidden])")
        page2.wait_for_timeout(600)
        assert page2.is_hidden("#validate-intro")
    finally:
        app.close()
