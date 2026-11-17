"""The validation report's Report view and Gallery view (oceanval.report_gallery):
a tile for each figure and table, opened with its caption in a pop-out."""

import os

import pytest

from oceanval import report_gallery

# a page as nbconvert writes it: a section of two figures (one of two images, one captioned without a
# gallery title), a section of one table, and an image with no caption
PAGE = """<!doctype html><html><head><title>t</title>{head}</head>
<body class="jp-Notebook"><aside class="oceanval-sidebar"><div class="oceanval-nav-sections"></div></aside><main>
<div class="jp-Cell"><div class="jp-OutputArea-child"><h1>Nitrate validation</h1></div></div>
<div class="jp-Cell"><div class="jp-OutputArea-child"><div class="jp-RenderedMarkdown"><h2>Maps¶</h2></div></div>
<div class="jp-OutputArea-child"><img src="data:image/png;base64,{pixel}"></div>
<div class="jp-OutputArea-child"><img src="data:image/png;base64,{pixel}"></div></div>
<div class="jp-Cell"><div class="jp-OutputArea-child"><div class="jp-RenderedMarkdown"><p><strong>Figure 1</strong>: The maps.<span class="ov-gallery-title" data-title="Annual maps"></span></p></div></div></div>
<div class="jp-Cell"><div class="jp-OutputArea-child"><img src="data:image/png;base64,{pixel}"></div></div>
<div class="jp-Cell"><div class="jp-OutputArea-child"><div class="jp-RenderedMarkdown"><p><strong>Figure 2</strong>: Bias of the model. A positive bias is too high.</p></div></div></div>
<div class="jp-Cell"><div class="jp-OutputArea-child"><div class="jp-RenderedMarkdown"><h2>Statistics</h2></div></div>
<div class="jp-OutputArea-child"><table><tr><td>0.5</td></tr></table></div>
<div class="jp-OutputArea-child"><div class="jp-RenderedMarkdown"><p><strong>Table 1</strong>: The numbers.<span class="ov-gallery-title" data-title="Statistics &amp; more"></span></p></div></div></div>
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
        # a tile for each figure and table with a caption, under the title, titled by its gallery title
        # or else its caption's first sentence
        assert page.is_visible("#ov-gallery") and page.is_visible("h1")
        tiles = page.locator(".ov-tile")
        assert tiles.locator(".ov-tile__label").all_text_contents() == ["Figure 1", "Figure 2", "Table 1"]
        assert tiles.locator(".ov-tile__title").all_text_contents() == ["Annual maps", "Bias of the model", "Statistics & more"]
        assert tiles.nth(0).locator("img").count() == 1 and tiles.nth(2).locator("svg").count() == 1
        assert not page.locator("main > .jp-Cell img").first.is_visible()
        # the section of two is a coloured group, its question once before its tiles; the lone table is not
        groups = page.locator(".ov-group")
        assert groups.count() == 1
        assert groups.locator(".ov-group__title").text_content() == "Maps"
        assert groups.locator(".ov-tile__label").all_text_contents() == ["Figure 1", "Figure 2"]
        assert page.locator("#ov-gallery > .ov-tile .ov-tile__label").all_text_contents() == ["Table 1"]
        # no row leaves a gap: in the gallery and in each group, every row's last tile reaches the right edge
        for width in (1400, 1000, 760):
            page.set_viewport_size({"width": width, "height": 900})
            short_rows = page.evaluate("""() => {
              const short = [];
              for (const box of document.querySelectorAll('#ov-gallery, .ov-group__tiles')) {
                const right = box.getBoundingClientRect().right
                  - parseFloat(getComputedStyle(box).paddingRight) - parseFloat(getComputedStyle(box).borderRightWidth);
                const rows = {};
                for (const child of box.children) {
                  const r = child.getBoundingClientRect();
                  rows[Math.round(r.top)] = Math.max(rows[Math.round(r.top)] || 0, r.right);
                }
                for (const end of Object.values(rows)) if (Math.abs(end - right) > 1) short.push([box.id || box.className, end, right]);
              }
              return short;
            }""")
            assert short_rows == [], (width, short_rows)
        colour = tiles.nth(0).evaluate("t => getComputedStyle(t).borderTopColor")
        assert colour == "rgb(15, 124, 124)"
        assert tiles.nth(2).evaluate("t => getComputedStyle(t).borderTopColor") != colour
        # the pop-out: both images of the figure, its section, and its caption
        tiles.nth(0).click()
        assert page.text_content("#ov-dialog-title") == "Figure 1: Annual maps"
        assert page.text_content(".ov-dialog__section") == "Maps"
        assert page.locator(".ov-dialog__body img").count() == 2
        assert "The maps." in page.text_content(".ov-dialog__body")
        page.keyboard.press("Escape")
        assert page.locator(".ov-dialog").is_hidden()
        tiles.nth(2).click()
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


def test_a_tall_figure_scrolls_in_the_pop_out(browser, tmp_path):
    # a figure four times as tall as the window: the pop-out stays within the window, and its body scrolls to the caption
    import base64

    tall = base64.b64encode(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="4000"><rect width="1000" height="4000" fill="teal"/></svg>'
    ).decode()
    page_html = PAGE.format(head=report_gallery.style() + report_gallery.script(), pixel=PIXEL).replace(
        f"data:image/png;base64,{PIXEL}", f"data:image/svg+xml;base64,{tall}", 1
    )
    (tmp_path / "tall.html").write_text(page_html)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        page.goto(f"file://{tmp_path / 'tall.html'}")
        page.locator(".ov-view-switch button").nth(1).click()
        page.locator(".ov-tile").nth(0).click()
        card = page.locator(".ov-dialog__card").bounding_box()
        assert card["y"] >= 0 and card["y"] + card["height"] <= 800
        assert page.is_visible(".ov-dialog__close")
        body = page.locator(".ov-dialog__body")
        assert body.evaluate("b => b.scrollHeight > b.clientHeight + 1000")
        # the page behind is still while it is open
        assert page.evaluate("getComputedStyle(document.body).overflow") == "hidden"
        caption = page.locator(".ov-dialog__caption")
        assert not caption.evaluate(
            "c => c.getBoundingClientRect().top < document.querySelector('.ov-dialog__body').getBoundingClientRect().bottom"
        )
        body.hover()
        for _ in range(30):
            page.mouse.wheel(0, 400)
        page.wait_for_function("(() => { const b = document.querySelector('.ov-dialog__body');"
                               " return b.scrollTop + b.clientHeight >= b.scrollHeight - 2; })()")
        assert caption.evaluate(
            "c => c.getBoundingClientRect().top < document.querySelector('.ov-dialog__body').getBoundingClientRect().bottom"
        )
        page.keyboard.press("Escape")
        assert page.evaluate("getComputedStyle(document.body).overflow") != "hidden"
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


def test_a_caption_carries_its_gallery_title_as_an_empty_marker():
    from oceanval.tidiers import md

    plain = md("**Figure 1**: Bias of CO2 in the model").data
    marked = md("**Figure 1**: Bias of CO2 in the model", gallery='Bias: "model" & obs').data
    assert marked == plain + '<span class="ov-gallery-title" data-title="Bias: &quot;model&quot; &amp; obs"></span>'
    assert md("Some text.").data == "Some text."


def test_the_gridded_report_captions_have_gallery_titles():
    # every figure and table caption of a validation report's gridded page gives its tile a short title
    import json
    import re
    from importlib.resources import files

    data = files("oceanval").joinpath("data")
    chunks = re.findall(r'"(chunk_[a-z]+)', data.joinpath("gridded_template.ipynb").read_text())
    sources = ["".join(cell["source"]) for cell in json.loads(data.joinpath("gridded_template.ipynb").read_text())["cells"]]
    sources += [data.joinpath(f"{chunk}.pytemplate").read_text() for chunk in set(chunks)]
    missing = []
    for source in sources:
        for line in source.split("\n"):
            if re.search(r"md\(f?\"\*\*(Figure|Table) \{", line) and "gallery=" not in line:
                missing.append(line.strip()[:90])
    assert missing == []
    assert any("md(regional_summary, gallery=" in source for source in sources)
