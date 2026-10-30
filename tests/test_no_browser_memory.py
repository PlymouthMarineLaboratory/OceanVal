"""The browser never offers what was typed in OceanVal's windows before: every
box has autocomplete="off", in the markup and when the pages make it later.
The simulation directories are remembered by OceanVal itself (.oceanvalcache)."""

import pathlib
import re

from oceanval.app import App
from oceanval.recipes_gui import RecipePage, recipe_rows

DATA = pathlib.Path(__file__).resolve().parent.parent / "oceanval" / "data"

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

# the typed boxes on the page whose autocomplete is not "off"
REMEMBERING = """() => [...document.querySelectorAll("input, textarea")]
    .filter((box) => box.tagName === "TEXTAREA"
        || !["hidden", "checkbox", "radio", "range", "button", "submit", "reset", "file", "image", "color"].includes(box.type))
    .filter((box) => box.getAttribute("autocomplete") !== "off")
    .map((box) => box.id || box.outerHTML.slice(0, 80))"""

# a box made by the page after it has loaded
MADE_LATER = """async () => {
    const box = document.createElement("input");
    box.id = "made-later";
    document.body.append(box);
    await new Promise((done) => setTimeout(done, 0));
    return box.getAttribute("autocomplete");
}"""


def test_chrome_off_is_never_used():
    """Chrome takes no notice of autocomplete="chrome-off"."""
    for path in DATA.iterdir():
        if path.suffix in {".html", ".js"}:
            assert not re.search(r"""autocomplete\W+chrome-off""", path.read_text()), path.name


def test_every_box_in_the_markup_says_off():
    for name in ["oceanval_app.html", "create_recipes_gui.html"]:
        text = (DATA / name).read_text()
        markup = text[: text.index('<script type="application/json" id="state">')]
        for tag in re.findall(r"<(?:input|textarea)\b[^>]*>", markup):
            kind = re.search(r'\btype="([^"]+)"', tag)
            if kind and kind.group(1) in {"hidden", "checkbox", "radio", "range"}:
                continue
            assert 'autocomplete="off"' in tag, (name, tag)


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
        assert page.evaluate(MADE_LATER) == "off"
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
        assert page.evaluate(MADE_LATER) == "off"
    finally:
        recipe_page.close()
