"""The validation report's Report view and Gallery view (oceanval.report_gallery):
a tile for each figure and table, opened with its caption in a pop-out."""

import os

import pytest

from oceanval import report_gallery

# a page as nbconvert writes it: a heading, a figure of two images and its caption, a table and its
# caption, and an image with no caption
PAGE = """<!doctype html><html><head><title>t</title>{head}</head>
<body class="jp-Notebook"><aside class="oceanval-sidebar"><div class="oceanval-nav-sections"></div></aside><main>
<div class="jp-Cell"><div class="jp-OutputArea-child"><h1>Nitrate validation</h1></div></div>
<div class="jp-Cell"><div class="jp-OutputArea-child"><div class="jp-RenderedMarkdown"><h2>Maps¶</h2></div></div>
<div class="jp-OutputArea-child"><img src="data:image/png;base64,{pixel}"></div>
<div class="jp-OutputArea-child"><img src="data:image/png;base64,{pixel}"></div></div>
<div class="jp-Cell"><div class="jp-OutputArea-child"><div class="jp-RenderedMarkdown"><p><strong>Figure 1</strong>: The maps.</p></div></div></div>
<div class="jp-Cell"><div class="jp-OutputArea-child"><div class="jp-RenderedMarkdown"><h2>Statistics</h2></div></div>
<div class="jp-OutputArea-child"><table><tr><td>0.5</td></tr></table></div>
<div class="jp-OutputArea-child"><div class="jp-RenderedMarkdown"><p><strong>Table 1</strong>: The numbers.</p></div></div></div>
<div class="jp-Cell"><div class="jp-OutputArea-child"><img src="data:image/png;base64,{pixel}"></div></div>
</main></body></html>"""
PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


@pytest.fixture
def pages(tmp_path):
    for name in ("one", "two"):
        (tmp_path / f"{name}.html").write_text(
            PAGE.format(head=report_gallery.style() + report_gallery.script(), pixel=PIXEL)
        )
    return tmp_path


def test_the_two_views_in_a_browser(browser, pages):
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(f"file://{pages / 'one.html'}")
        switch = page.locator(".oceanval-sidebar .ov-view-switch button")
        assert switch.all_text_contents() == ["Report view", "Gallery view"]
        # Report view first: the page as it is
        assert switch.nth(0).get_attribute("aria-pressed") == "true"
        assert page.is_hidden("#ov-gallery") and page.locator("main > .jp-Cell img").first.is_visible()
        switch.nth(1).click()
        # a tile for each figure and table with a caption, under the title
        assert page.is_visible("#ov-gallery") and page.is_visible("h1")
        tiles = page.locator(".ov-tile")
        assert tiles.locator(".ov-tile__label").all_text_contents() == ["Figure 1", "Table 1"]
        assert tiles.locator(".ov-tile__title").all_text_contents() == ["Maps", "Statistics"]
        assert tiles.nth(0).locator("img").count() == 1 and tiles.nth(1).locator("svg").count() == 1
        assert not page.locator("main > .jp-Cell img").first.is_visible()
        # the pop-out: both images of the figure, and its caption
        tiles.nth(0).click()
        assert page.text_content("#ov-dialog-title") == "Figure 1: Maps"
        assert page.locator(".ov-dialog__body img").count() == 2
        assert "The maps." in page.text_content(".ov-dialog__body")
        page.keyboard.press("Escape")
        assert page.locator(".ov-dialog").is_hidden()
        tiles.nth(1).click()
        assert page.text_content(".ov-dialog__body td") == "0.5"
        page.click(".ov-dialog__close")
        # the view chosen holds on the next page
        page.goto(f"file://{pages / 'two.html'}")
        assert page.is_visible("#ov-gallery")
        page.locator(".ov-view-switch button").nth(0).click()
        assert page.is_hidden("#ov-gallery")
        assert errors == []
    finally:
        page.close()


def test_only_a_validation_report_has_the_gallery(tmp_path):
    import nbformat

    import oceanval

    nbformat.write(nbformat.v4.new_notebook(cells=[nbformat.v4.new_markdown_cell("# A page")]), str(tmp_path / "page.ipynb"))
    pages = tmp_path / "notebooks"
    pages.mkdir()
    (tmp_path / "oceanval_wordmark.svg").write_text("<svg/>")
    for gallery in (True, False):
        (pages / "page.html").write_text("<html><head></head><body><main></main></body></html>")
        oceanval._write_offline_report_pages(str(tmp_path), [str(tmp_path / "page.ipynb")], gallery=gallery)
        assert ("ov-view-switch" in (pages / "page.html").read_text()) is gallery
