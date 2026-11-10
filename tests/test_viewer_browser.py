"""View simulation output in a browser (ported from ncviewer's tests): the
landing page's tile, the setup page filling in the directories down from the
folders' layout and taking the missing values, Continue reading the files,
and the viewer mapping a variable once its file type is chosen, summing,
playing, and cutting to the years chosen. Skipped where Chromium is not
installed."""

import os
import time
from urllib.parse import parse_qs, urlparse

import pytest


def set_words(page, store, text):
    """Sets a file filter's words as typing them into its list would: the
    store is a hidden input, so it is written and its input event fired."""
    page.evaluate(
        "([id, value]) => { const node = document.getElementById(id); node.value = value;"
        " node.dispatchEvent(new Event('input', { bubbles: true })); }",
        [store.lstrip("#"), text],
    )


from oceanval import viewer_app
from oceanval.app import App
from test_app import leftover_dir  # noqa: F401 (autouse)


@pytest.fixture
def simulation(viewer_simulation):
    return viewer_simulation


@pytest.fixture
def page(browser, tmp_path):
    app = App(cwd=str(tmp_path))
    app.leftovers_asked = True
    url = app.start()
    tab = browser.new_page(viewport={"width": 1300, "height": 900})
    errors = []
    tab.on("pageerror", lambda error: errors.append(str(error)))
    tab.goto(url)
    tab.click("#home-viewer")
    tab.wait_for_selector("#vw-setup-pop:not([hidden])")
    yield tab, app
    assert errors == []
    tab.close()
    app.close()


def _times_in(tab, app):
    """Waits for the times of every file, which are read in the background once the viewer has opened."""
    for _ in range(600):
        if app.viewers[1].catalogue is not None and app.viewers[1].catalogue.times_complete and app.viewers[1].times is None:
            break
        time.sleep(0.1)
    assert app.viewers[1].catalogue.times_complete
    tab.wait_for_function("() => !document.querySelector('[data-vw=first]').disabled", timeout=60000)


def test_files_page_finds_the_depth(page, simulation):
    tab, app = page
    tab.fill("#f-simdir", simulation)
    tab.wait_for_function("() => document.querySelector('#f-ndown').value === '2'")
    tab.wait_for_selector("#ndown-found:not(.is-finding):not([hidden])")
    assert tab.text_content("#ndown-found") == "48 netCDF files found"
    # the layout fills the box in bold
    assert tab.evaluate("() => getComputedStyle(document.querySelector('#f-ndown')).fontWeight") == "700"
    assert tab.locator("#ndown-layout").count() == 0
    assert "is-detected" in tab.get_attribute("#f-ndown", "class")
    assert tab.is_enabled("#vw-apply")
    # the files the filters leave out are not mentioned, only the number there are
    set_words(tab, "#f-exclude", "grid_T")
    tab.wait_for_function("() => document.querySelector('#ndown-found').textContent.startsWith('24 ')")
    assert tab.text_content("#ndown-found") == "24 netCDF files found"
    set_words(tab, "#f-exclude", "")
    # typing a depth makes it the user's own
    tab.fill("#f-ndown", "1")
    tab.wait_for_selector("#m-simulation .is-nofiles")
    assert "is-detected" not in tab.get_attribute("#f-ndown", "class")
    assert not tab.is_enabled("#vw-apply")


def test_missing_values_on_the_files_page(page, simulation):
    tab, app = page
    tab.fill("#f-simdir", simulation)
    tab.wait_for_function("() => !document.querySelector('#vw-apply').disabled")
    # the boxes are there from the start, empty, and an untouched row stops nothing
    assert tab.locator("#missing-rows .missing-rows__row").count() == 1
    assert tab.is_visible("#missing-from-0") and tab.is_visible("#missing-to-0")
    assert tab.input_value("#missing-from-0") == ""
    assert tab.is_enabled("#vw-apply")
    tab.fill("#missing-from-0", "abc")
    # a box that is not a number is marked, and Continue waits
    assert "is-invalid" in tab.get_attribute("#missing-from-0", "class")
    assert tab.is_disabled("#vw-apply")
    assert "not a number" in tab.text_content("#m-missing-rows")
    # there is one From and one To, with no way to add another
    tab.fill("#missing-from-0", "-999")
    assert tab.locator("#missing-add").count() == 0
    assert tab.locator(".missing-rows__add, .missing-rows__remove").count() == 0
    assert tab.locator("#missing-rows .missing-rows__row").count() == 1
    tab.fill("#missing-to-0", "-900")
    assert tab.is_enabled("#vw-apply")
    # clearing the From box leaves it to fill in again
    tab.fill("#missing-from-0", "")
    assert tab.input_value("#missing-from-0") == ""
    tab.fill("#missing-to-0", "")
    tab.fill("#missing-from-0", "10")
    tab.fill("#missing-to-0", "12.5")
    tab.click("#vw-apply")
    tab.wait_for_function("() => document.querySelector('[data-vw=body]:not([hidden])')", timeout=60000)
    assert app.viewers[1].missing == [(10.0, 12.5)]
    # going back and choosing the tile again shows it again, in the panel's box
    tab.click("#back-open")
    tab.click("#home-viewer")
    tab.wait_for_selector("#vw-setup-pop:not([hidden])")
    assert tab.input_value("#missing-from-0") == "10"
    assert tab.input_value("#missing-to-0") == "12.5"
    assert tab.locator("#missing-rows .missing-rows__row").count() == 1


def _land_files_page(tab, root):
    tab.fill("#f-simdir", root)
    tab.wait_for_function("() => document.querySelector('#f-ndown').value === '0'")
    tab.wait_for_selector("#ndown-found:not(.is-finding):not([hidden])")


def test_a_missing_value_found_in_the_files_is_filled_in_to_be_checked(page, tmp_path):
    from viewer_simulations import land_simulation

    tab, app = page
    _land_files_page(tab, land_simulation(str(tmp_path / "land"), land=-999.0))
    tab.wait_for_function("() => document.querySelector('#missing-from-0').value === '-999'")
    assert tab.input_value("#missing-to-0") == "-999"
    assert "is-found" in tab.get_attribute("#missing-from-0", "class")
    assert tab.is_visible("#missing-found") and tab.is_hidden("#missing-ersem")
    assert tab.is_enabled("#vw-apply")
    # typing over it makes it the user's: the amber goes, and nothing is put there again
    tab.fill("#missing-from-0", "-9")
    assert "is-found" not in tab.get_attribute("#missing-from-0", "class")
    assert tab.is_hidden("#missing-found")
    tab.fill("#f-require", "land_0")
    tab.wait_for_selector("#ndown-found:not(.is-finding):not([hidden])")
    time.sleep(0.5)
    assert tab.input_value("#missing-from-0") == "-9"
    # and what is in the rows is what Continue is given
    tab.click("#vw-apply")
    tab.wait_for_selector("[data-vw=body]:not([hidden])", timeout=60000)
    assert app.viewers[1].missing == [(-9.0, -999.0)]


def test_the_page_says_it_is_identifying_the_missing_value(page, tmp_path, monkeypatch):
    import threading

    from viewer_simulations import land_simulation

    from oceanval import viewer_missing

    tab, app = page
    release = threading.Event()
    original = viewer_missing.guess
    monkeypatch.setattr(viewer_missing, "guess", lambda *args: release.wait(30) and original(*args))
    _land_files_page(tab, land_simulation(str(tmp_path / "land"), land=-999.0))
    tab.wait_for_selector("#missing-identifying:not([hidden])")
    assert tab.text_content("#missing-identifying").strip() == "Attempting to identify missing value range"
    assert tab.is_visible("#missing-identifying .identify__spinner")
    release.set()
    tab.wait_for_selector("#missing-identifying", state="hidden")
    assert tab.input_value("#missing-from-0") == "-999"
    # typing while it is looked for ends it, and the answer does not come after
    release.clear()
    tab.fill("#f-require", "land_0")
    tab.wait_for_selector("#missing-identifying:not([hidden])")
    tab.fill("#missing-from-0", "-9")
    assert tab.is_hidden("#missing-identifying")
    release.set()
    time.sleep(0.5)
    assert tab.input_value("#missing-from-0") == "-9"


def test_the_rows_are_taken_back_when_the_simulation_changes(page, tmp_path):
    from viewer_simulations import land_simulation, nemo_simulation

    tab, app = page
    _land_files_page(tab, land_simulation(str(tmp_path / "land"), land=0.0))
    tab.wait_for_function("() => document.querySelector('#missing-from-0').value === '0'")
    # a simulation with nothing to find: the rows OceanVal filled are emptied
    nemo = nemo_simulation(str(tmp_path / "nemo"), years=(2000,))
    tab.fill("#f-simdir", nemo)
    tab.wait_for_function("() => document.querySelector('#f-ndown').value === '2'")
    tab.wait_for_selector("#ndown-found:not(.is-finding):not([hidden])")
    tab.wait_for_function("() => document.querySelector('#missing-from-0').value === ''")
    assert tab.is_hidden("#missing-found")


def test_a_new_directory_is_searched_afresh(page, simulation, tmp_path):
    from viewer_simulations import land_simulation

    tab, app = page
    tab.fill("#f-simdir", simulation)
    tab.wait_for_function("() => document.querySelector('#f-ndown').value === '2'")
    tab.wait_for_selector("#ndown-found:not(.is-finding):not([hidden])")
    # the depth and the missing value are the user's own
    tab.fill("#f-ndown", "2")
    tab.fill("#missing-from-0", "-9")
    assert "is-detected" not in tab.get_attribute("#f-ndown", "class")
    # another directory: both are looked for again
    tab.fill("#f-simdir", land_simulation(str(tmp_path / "land"), land=-999.0))
    tab.wait_for_function("() => document.querySelector('#f-ndown').value === '0'")
    tab.wait_for_selector("#ndown-found:not(.is-finding):not([hidden])")
    assert "is-detected" in tab.get_attribute("#f-ndown", "class")
    tab.wait_for_function("() => document.querySelector('#missing-from-0').value === '-999'")
    assert "is-found" in tab.get_attribute("#missing-from-0", "class")


def test_the_path_of_one_file_can_be_given(page, tmp_path):
    from viewer_simulations import land_simulation

    tab, app = page
    assert tab.text_content("#simdir-question") == "Which directory or file path stores your simulation data?"
    root = land_simulation(str(tmp_path / "land"), land=-999.0)
    tab.fill("#f-simdir", root)
    tab.wait_for_function("() => document.querySelector('#f-ndown').value === '0'")
    tab.fill("#f-simdir", os.path.join(root, "land_01.nc"))
    tab.wait_for_function("() => document.querySelector('#f-ndown').disabled")
    tab.wait_for_selector("#ndown-found:not(.is-finding):not([hidden])")
    assert tab.text_content("#ndown-found") == "1 netCDF file found"
    assert tab.is_enabled("#vw-apply")
    tab.click("#vw-apply")
    tab.wait_for_selector("[data-vw=body]:not([hidden])", timeout=60000)
    assert [len(stream["files"]) for stream in app.viewers[1].catalogue.streams] == [1]
    # back to a directory: the depth is asked and found again
    tab.click("#back-open")
    tab.wait_for_selector("#vw-setup-pop:not([hidden])")
    tab.fill("#f-simdir", root)
    tab.wait_for_function("() => !document.querySelector('#f-ndown').disabled && document.querySelector('#f-ndown').value === '0'")


def test_the_thredds_tick_is_asked_only_for_a_web_address(page, tmp_path):
    """Not for a path on disk, only for what looks like a web address (the user's rule, 2026-11-06)."""
    tab, app = page
    assert tab.is_hidden("#row-f-thredds")
    tab.fill("#f-simdir", str(tmp_path))
    assert tab.is_hidden("#row-f-thredds")
    # not ending in .nc, so nothing is read from the network
    tab.fill("#f-simdir", "https://example.invalid/thredds/model")
    tab.wait_for_selector("#row-f-thredds:not([hidden])")
    assert tab.is_checked("#f-thredds")
    tab.fill("#f-simdir", str(tmp_path))
    tab.wait_for_selector("#row-f-thredds", state="hidden")
    assert not tab.is_checked("#f-thredds")


def test_an_ersem_simulation_has_zeros_assumed_to_be_missing(page, simulation, monkeypatch):
    from oceanval import recipes_gui

    tab, app = page
    monkeypatch.setattr(recipes_gui, "is_likely_nemo_ersem", lambda names: True)
    tab.fill("#f-simdir", simulation)
    tab.wait_for_function("() => document.querySelector('#missing-from-0').value === '0'")
    assert tab.input_value("#missing-to-0") == "0"
    assert "is-ersem" in tab.get_attribute("#missing-from-0", "class")
    assert tab.is_visible("#missing-ersem") and tab.is_hidden("#missing-found")
    # a row emptied is the user's doing, and stays empty
    assert tab.locator("[aria-label='Remove missing value 1']").count() == 0
    tab.fill("#missing-from-0", "")
    tab.fill("#missing-to-0", "")
    assert tab.input_value("#missing-from-0") == ""
    assert "is-ersem" not in tab.get_attribute("#missing-from-0", "class")


def test_viewer(page, simulation):
    tab, app = page
    tab.fill("#f-simdir", simulation)
    tab.wait_for_function("() => !document.querySelector('#vw-apply').disabled")
    # the coldest cells of the first map, which the colour scale then leaves out
    tab.fill("#missing-from-0", "0")
    tab.fill("#missing-to-0", "11.3")
    tab.click("#vw-apply")
    tab.wait_for_selector("[data-vw=body]:not([hidden])", timeout=60000)
    _times_in(tab, app)
    tab.fill("[data-vw=search]", "chloro")
    assert tab.locator("[data-vw=list] li").count() == 1
    # * and ? stand for characters, and are matched within the name, the long name or the units
    tab.fill("[data-vw=search]", "ch*phyll")
    assert tab.locator("[data-vw=list] li").count() == 1
    tab.fill("[data-vw=search]", "*")
    assert tab.locator("[data-vw=list] li").count() > 1
    tab.fill("[data-vw=search]", "temp")
    tab.keyboard.press("Enter")
    # in two file types: nothing is plotted until one is chosen
    assert "Which do you want?" in tab.text_content("[data-vw=streams]")
    assert tab.locator("[data-vw=plot-wrap] [class^=bk]").count() == 0
    tab.click("[data-vw=streams] label.ptr-mode >> nth=1")
    tab.wait_for_selector("[data-vw=plot-wrap] [class^=bk]", timeout=30000)
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    # nothing is said at the bottom of the page (not the files, file types or directory)
    assert tab.text_content("#summary") == ""
    # the only thing said about the variable is its units: the name and description are in the list above,
    # and the dates, time resolution, calendar and files are not shown (the slider and the year boxes give the times)
    assert tab.locator("[data-vw=info] dt").all_text_contents() == ["Units"]
    # nothing is written under the map
    assert tab.locator("#plot-notes, .stage__notes").count() == 0
    # the map fills its box, not a strip of axes
    assert tab.locator("[data-vw=plot-wrap] [class^=bk]").first.bounding_box()["height"] > 300
    tab.click("[data-vw=next]")
    tab.wait_for_function("() => document.querySelector('[data-vw=when]').textContent.startsWith('2000-02')")
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    # a deeper level is drawn too
    tab.select_option("[data-vw=level]", "3")
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    assert tab.is_hidden("[data-vw=plot-error]")
    # nothing but the map: no other plots to choose
    assert tab.locator("[data-vw=kind], [data-vw=point], [data-vw=c-cell]").count() == 0
    tab.click("#back-open")
    tab.wait_for_selector("#vw-setup-pop:not([hidden])")


_FRAME = """() => {
  const index = Bokeh.index;
  const views = index.roots ? [...index.roots] : (index[Symbol.iterator] ? [...index] : Object.values(index));
  const shown = views.find((view) => view.el.isConnected && !view.el.closest(".is-back"));
  const box = document.querySelector('[data-vw=plot-wrap]').getBoundingClientRect();
  const outer = shown.el.getBoundingClientRect();
  return {frame: [shown.frame.bbox.width, shown.frame.bbox.height], outer: [outer.width, outer.height],
          box: [box.width, box.height], centre: [outer.left + outer.width / 2 - box.left - box.width / 2,
                                                   outer.top + outer.height / 2 - box.top - box.height / 2]};
}"""


def _fits(tab, aspect):
    """The map's frame is aspect high for its width, and the figure fills the
    width or the height of its box, centred in the other."""
    tab.wait_for_timeout(300)
    got = tab.evaluate(_FRAME)
    (width, height), (outer_w, outer_h), (box_w, box_h) = got["frame"], got["outer"], got["box"]
    assert height / width == pytest.approx(aspect, rel=0.03), got
    assert abs(outer_w - box_w) <= 3 or abs(outer_h - box_h) <= 3, got
    assert outer_w <= box_w + 1 and outer_h <= box_h + 1, got
    assert abs(got["centre"][0]) <= 2 and abs(got["centre"][1]) <= 2, got


def test_the_map_keeps_distances_on_one_scale(page, tmp_path):
    """A lon-lat map's frame is as tall for its width as the area is north to
    south for its width east to west, as large as fits, whatever the window."""
    import numpy as np
    from viewer_simulations import regular_file

    root = tmp_path / "regular"
    root.mkdir()
    regular_file(str(root / "r.nc"))
    tab, app = page
    tab.fill("#f-simdir", str(root))
    tab.wait_for_function("() => !document.querySelector('#vw-apply').disabled")
    tab.click("#vw-apply")
    tab.wait_for_selector("[data-vw=body]:not([hidden])", timeout=60000)
    _times_in(tab, app)
    tab.fill("[data-vw=search]", "sst")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("[data-vw=plot-wrap] [class^=bk]", timeout=30000)
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    # 40-65N by 20W-10E, cells of a degree, reaching half a cell past the outer points
    aspect = 26 / (31 * np.cos(np.radians(52.5)))
    _fits(tab, aspect)
    # the next time step, drawn behind and then shown, is fitted too
    tab.click("[data-vw=next]")
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    _fits(tab, aspect)
    # a narrower window: the map is fitted again
    tab.set_viewport_size({"width": 900, "height": 900})
    _fits(tab, aspect)
    tab.set_viewport_size({"width": 1600, "height": 700})
    _fits(tab, aspect)


def test_levels_written_bottom_first_start_at_the_surface(page, tmp_path):
    """Levels with no depths (GETM's) are named at the surface and the bottom,
    and the map starts at the surface, the last level here."""
    from viewer_simulations import levels_file

    root = tmp_path / "getm"
    root.mkdir()
    levels_file(str(root / "levels.nc"), height="up")
    tab, app = page
    tab.fill("#f-simdir", str(root))
    tab.wait_for_function("() => !document.querySelector('#vw-apply').disabled")
    tab.click("#vw-apply")
    tab.wait_for_selector("[data-vw=body]:not([hidden])", timeout=60000)
    _times_in(tab, app)
    tab.fill("[data-vw=search]", "temp")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("[data-vw=plot-wrap] [class^=bk]", timeout=30000)
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    assert tab.locator("[data-vw=level] option").all_text_contents() == ["0 (bottom)", "1", "2", "3", "4 (surface)"]
    assert tab.input_value("[data-vw=level]") == "4"
    assert tab.is_hidden("[data-vw=plot-error]")
    # a level chosen is kept
    tab.select_option("[data-vw=level]", "0")
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    assert tab.input_value("[data-vw=level]") == "0"


def test_another_simulation_is_drawn_after_back(page, simulation, tmp_path):
    """The maps of a simulation viewed before are not shown for another with
    the same variables: the page asks for the new simulation's map."""
    from viewer_simulations import nemo_simulation

    tab, app = page
    other = nemo_simulation(str(tmp_path / "other"), years=(2010,))
    asked = []
    tab.on("request", lambda request: asked.append(request.url) if "api/viewer/plot" in request.url else None)

    def view(directory):
        tab.fill("#f-simdir", directory)
        tab.wait_for_function("() => !document.querySelector('#vw-apply').disabled")
        tab.click("#vw-apply")
        tab.wait_for_selector("[data-vw=body]:not([hidden])", timeout=60000)
        tab.fill("[data-vw=search]", "sossheig")
        tab.keyboard.press("Enter")
        tab.wait_for_selector("[data-vw=plot-wrap] [class^=bk]", timeout=30000)
        tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")

    view(simulation)
    assert len(asked) == 1
    tab.click("#back-open")
    tab.click("#home-viewer")
    tab.wait_for_selector("#vw-setup-pop:not([hidden])")
    view(other)
    assert len(asked) == 2
    assert tab.text_content("[data-vw=when]").startswith("2010-01")


def test_sum_of_variables(page, simulation):
    tab, app = page
    tab.fill("#f-simdir", simulation)
    tab.wait_for_function("() => !document.querySelector('#vw-apply').disabled")
    tab.click("#vw-apply")
    tab.wait_for_selector("[data-vw=body]:not([hidden])", timeout=60000)
    row = lambda name: f"[data-vw=list] li[data-name='{name}']"  # noqa: E731
    # add chl with its + button: ptrc_T is its only file type, so it is chosen and drawn
    tab.click(f"{row('chl')} .vw-pm")
    tab.wait_for_selector("[data-vw=plot-wrap] [class^=bk]", timeout=30000)
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    # sossheig cannot be added to chl: not the same dimensions
    assert "is-incompatible" in tab.get_attribute(row("sossheig"), "class")
    assert tab.is_disabled(f"{row('sossheig')} .vw-pm")
    assert tab.text_content(f"{row('sossheig')} .vw-pm") == '+'
    # only the + is greyed out: the name and the row read as usual (not dimmed, so they look available)
    assert tab.evaluate(f"() => getComputedStyle(document.querySelector({row('sossheig')!r})).opacity") == "1"
    assert tab.evaluate(f"() => getComputedStyle(document.querySelector({row('sossheig')!r} + ' .vw-list__name')).opacity") == "1"
    assert tab.evaluate(f"() => getComputedStyle(document.querySelector({row('sossheig')!r} + ' .vw-pm')).opacity") == "0.5"
    # votemper can (it is in two file types, but only ptrc_T has chl)
    tab.click(f"{row('votemper')} .vw-pm")
    tab.wait_for_function("() => document.querySelector('[data-vw=info]').textContent.includes('Sum of')")
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    assert "chl (mg m-3) + votemper (degC)" in tab.text_content("[data-vw=info]")
    assert tab.is_hidden("[data-vw=plot-error]")
    # the file type is not asked: only one holds both
    assert tab.is_hidden("[data-vw=streams]")
    # Clear takes everything out
    tab.click("[data-vw=clear]")
    assert tab.locator("[data-vw=list] li.is-chosen").count() == 0
    assert tab.is_hidden("[data-vw=chosen]")
    # a click on a row chooses that variable alone: a second click replaces the first
    tab.click(f"{row('chl')} .vw-list__name")
    tab.wait_for_selector("[data-vw=plot-wrap] [class^=bk]", timeout=30000)
    tab.click(row("votemper"), position={"x": 200, "y": 10})
    assert tab.locator("[data-vw=list] li.is-chosen").count() == 1
    assert tab.text_content(f"{row('votemper')} .vw-pm") == '-'
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    assert "Sum of" not in tab.text_content("[data-vw=info]")
    # only the + button adds to the sum
    tab.click(f"{row('chl')} .vw-pm")
    assert tab.locator("[data-vw=list] li.is-chosen").count() == 2
    tab.wait_for_function("() => document.querySelector('[data-vw=info]').textContent.includes('Sum of')")
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    # an incompatible row's + is greyed out, but a click chooses it alone
    assert tab.is_disabled(f"{row('sossheig')} .vw-pm")
    tab.click(f"{row('sossheig')} .vw-list__name")
    assert tab.locator("[data-vw=list] li.is-chosen").count() == 1
    assert tab.text_content(f"{row('sossheig')} .vw-pm") == '-'
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    # the - button leaves nothing chosen
    tab.click(f"{row('sossheig')} .vw-pm")
    assert tab.locator("[data-vw=list] li.is-chosen").count() == 0
    tab.click("#back-open")
    tab.wait_for_selector("#vw-setup-pop:not([hidden])")


def _open_chl(tab, app, simulation):
    tab.fill("#f-simdir", simulation)
    tab.wait_for_function("() => !document.querySelector('#vw-apply').disabled")
    tab.click("#vw-apply")
    tab.wait_for_selector("[data-vw=body]:not([hidden])", timeout=60000)
    _times_in(tab, app)
    tab.fill("[data-vw=search]", "chl")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("[data-vw=plot-wrap] [class^=bk]", timeout=30000)
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")


def _watch_play(tab):
    """Records the dates shown, and any time the "reading the data" cover is up, from now on."""
    tab.evaluate("""() => {
        window.__seen = []; window.__covered = 0;
        const busy = document.querySelector('[data-vw=plot-busy]');
        new MutationObserver(() => { if (!busy.hidden) window.__covered += 1; }).observe(busy, {attributes: true});
        const when = document.querySelector('[data-vw=when]');
        new MutationObserver(() => window.__seen.push(when.textContent.slice(0, 10)))
            .observe(when, {childList: true, characterData: true, subtree: true});
    }""")


def test_play_is_an_animation(page, simulation):
    tab, app = page
    _open_chl(tab, app, simulation)
    plots = []
    tab.on("request", lambda request: plots.append(request.url) if "api/viewer/plot" in request.url else None)
    _watch_play(tab)
    tab.click("[data-vw=play]")
    # it moves through the times, and the map is never covered or empty
    tab.wait_for_function("() => window.__seen.length >= 4", timeout=30000)
    assert tab.evaluate("() => window.__covered") == 0
    assert tab.locator("[data-vw=plot-wrap] [class^=bk]:visible").count() > 0
    assert tab.get_attribute("[data-vw=play]", "aria-pressed") == "true"
    # each date is the next one in the run
    seen = tab.evaluate("() => window.__seen")
    assert seen[:2] == ["2000-02-15", "2000-03-16"]
    # one colour scale for every frame that was asked for
    scales = {tuple(url.split(name + "=")[1].split("&")[0] for name in ("vmin", "vmax")) for url in plots[1:]}
    assert len(scales) == 1 and "" not in next(iter(scales))
    # pausing stops it
    tab.click("[data-vw=play]")
    assert tab.get_attribute("[data-vw=play]", "aria-pressed") == "false"
    settled = tab.evaluate("() => window.__seen.length")
    tab.wait_for_timeout(800)
    assert tab.evaluate("() => window.__seen.length") <= settled + 1
    assert tab.is_hidden("[data-vw=plot-error]")


def test_play_speed(page, simulation):
    tab, app = page
    _open_chl(tab, app, simulation)
    assert tab.input_value("[data-vw=speed]") == "normal"
    rates = {}
    for speed in ("slow", "fast"):
        tab.select_option("[data-vw=speed]", speed)
        _watch_play(tab)
        tab.click("[data-vw=play]")
        tab.wait_for_timeout(3000)
        tab.click("[data-vw=play]")
        rates[speed] = tab.evaluate("() => window.__seen.length")
    assert rates["fast"] > rates["slow"] + 3


def _figure_ranges(tab, to=None):
    """The ranges of the figure on show (and sets them first, if to is given)."""
    return tab.evaluate("""(to) => {
        const views = Object.values(Bokeh.index).filter((v) => v.model && v.model.type === "Figure");
        const m = views[views.length - 1].model;
        if (to) { [m.x_range.start, m.x_range.end, m.y_range.start, m.y_range.end] = to; }
        return [m.x_range.start, m.x_range.end, m.y_range.start, m.y_range.end];
    }""", to)


def test_play_keeps_the_zoom_and_the_scale(page, simulation):
    tab, app = page
    _open_chl(tab, app, simulation)
    whole = _figure_ranges(tab)
    # zoom in, then play: every frame is zoomed the same
    x0, x1, y0, y1 = whole
    zoomed = [x0 + (x1 - x0) * 0.2, x0 + (x1 - x0) * 0.6, y0 + (y1 - y0) * 0.1, y0 + (y1 - y0) * 0.4]
    _figure_ranges(tab, zoomed)
    _watch_play(tab)
    tab.click("[data-vw=play]")
    tab.wait_for_function("() => window.__seen.length >= 3", timeout=30000)
    tab.click("[data-vw=play]")
    assert _figure_ranges(tab) == pytest.approx(zoomed)
    # only the figure on show is kept, not one for every frame
    assert tab.evaluate("() => Object.values(Bokeh.index).filter((v) => v.model && v.model.type === 'Figure').length") == 1
    # the scale of the animation is kept after it, so the map does not change under the user (it is not put
    # in the Minimum and Maximum boxes), until Automatic is chosen again
    assert tab.input_value("[data-vw=vmin]") == ""
    tab.click("[data-vw=scale-auto]")
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    assert tab.is_hidden("[data-vw=plot-error]")
    # choosing a different variable starts from a fresh view
    tab.fill("[data-vw=search]", "sossheig")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    assert _figure_ranges(tab) == pytest.approx(whole)


def _is_plot(request):
    return "api/viewer/plot" in request.url


def _asked(request):
    """What a plot request asks for, as the query's values by name (a blank one is [""])."""
    return {name: values for name, values in parse_qs(urlparse(request.url).query, keep_blank_values=True).items()}


def _before(tab, first, second):
    """Whether the element of the selector first comes before that of second in the page."""
    return tab.evaluate(
        "([a, b]) => !!(document.querySelector(a).compareDocumentPosition(document.querySelector(b)) & Node.DOCUMENT_POSITION_FOLLOWING)",
        [first, second],
    )


def test_the_side_panel_layout(page, simulation):
    tab, app = page
    _open_chl(tab, app, simulation)
    side = "[data-vw=side-body]"
    # the headings, in order (there is no About, Subset years or Colours; file type and depth come and go)
    heads = tab.locator(f"{side} .vw-head:visible").all_text_contents()
    ours = ["Variable selector", "Selection and animation", "Select years", "Map options"]
    assert [head for head in heads if head in ours] == ours
    assert not {"About", "Subset years", "Colours", "Time", "Variable"} & set(heads)
    # the units come first of what is said about the variable, above the time and the years
    assert _before(tab, "[data-vw=info]", "[data-vw=c-time]") and _before(tab, "[data-vw=c-time]", "[data-vw=c-years]")
    # a light teal box holds the time, the depth and the years, in that order, and another the map options
    boxes = tab.locator(f"{side} .vw-teal:visible")
    assert boxes.count() == 2
    for part in ("c-time", "c-level", "c-years"):
        assert boxes.nth(0).locator(f"[data-vw={part}]").count() == 1
    assert _before(tab, "[data-vw=c-time]", "[data-vw=c-level]") and _before(tab, "[data-vw=c-level]", "[data-vw=c-years]")
    assert boxes.nth(1).locator("[data-vw=c-colour]").count() == 1
    # Play is in the row of Speed, ahead of it, and not with the slider
    assert tab.evaluate("() => document.querySelector('.vw-play-row').firstElementChild.dataset.vw") == "play"
    assert tab.locator(".vw-time-row [data-vw=play]").count() == 0
    # the year boxes say what they are in themselves, with no labels over them
    assert tab.locator("[data-vw=c-years] .g-label").count() == 0
    assert tab.get_attribute("[data-vw=first]", "placeholder") == "first" and tab.get_attribute("[data-vw=last]", "placeholder") == "last"
    assert tab.get_attribute("[data-vw=first]", "aria-label") == "First year" and tab.get_attribute("[data-vw=last]", "aria-label") == "Last year"
    # what to do with the variables is said on hovering over their heading, at once (not the browser's slow title)
    tip = tab.locator(f"{side} .vw-tip")
    text = "Click a variable to map it. Use the + buttons to map the sum of several variables."
    assert tip.text_content() == "Variable selector" and tip.get_attribute("data-tip") == text and tip.get_attribute("title") is None
    assert text not in tab.text_content(side)
    shown = "() => getComputedStyle(document.querySelector('.vw-tip'), '::after').display"
    assert tab.evaluate(shown) == "none"
    tip.hover()
    assert tab.evaluate(shown) == "block"
    # the colour maps: Automatic first, and no turbo
    options = tab.locator("[data-vw=cmap] option").all_text_contents()
    assert options[0] == "Automatic" and "viridis" in options and "turbo" not in options
    # coastlines are ticked by default, and asked for
    if tab.is_enabled("[data-vw=coast]"):
        assert tab.is_checked("[data-vw=coast]")
    with tab.expect_request(_is_plot) as sent:
        tab.select_option("[data-vw=cmap]", "plasma")
    assert _asked(sent.value)["coast"] == ["1"]


def test_the_scale_is_automatic_or_fixed(page, simulation):
    tab, app = page
    _open_chl(tab, app, simulation)
    # Automatic is the default: the Minimum and Maximum boxes are not shown
    assert tab.get_attribute("[data-vw=scale-auto]", "aria-pressed") == "true"
    assert tab.get_attribute("[data-vw=scale-fixed]", "aria-pressed") == "false"
    assert tab.is_hidden("[data-vw=minmax]")
    # Fixed shows them, empty, with their names in them
    tab.click("[data-vw=scale-fixed]")
    assert tab.get_attribute("[data-vw=scale-fixed]", "aria-pressed") == "true" and tab.is_visible("[data-vw=minmax]")
    assert tab.get_attribute("[data-vw=vmin]", "placeholder") == "Minimum" and tab.get_attribute("[data-vw=vmax]", "placeholder") == "Maximum"
    assert tab.input_value("[data-vw=vmin]") == "" and tab.input_value("[data-vw=vmax]") == ""
    # the limits typed are used
    with tab.expect_request(lambda request: _is_plot(request) and "vmax=9" in request.url) as sent:
        tab.fill("[data-vw=vmin]", "5")
        tab.fill("[data-vw=vmax]", "9")
    assert _asked(sent.value)["vmin"] == ["5"]
    # Automatic hides the boxes (the map it goes back to was fetched before, so nothing is asked for)...
    tab.click("[data-vw=scale-auto]")
    assert tab.is_hidden("[data-vw=minmax]")
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    # ...and stops using the limits, but keeps them: the next time drawn is asked for without them
    with tab.expect_request(_is_plot) as sent:
        tab.click("[data-vw=next]")
    assert _asked(sent.value).get("vmin", [""]) == [""] and _asked(sent.value).get("vmax", [""]) == [""]
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    # Fixed brings them back, in the boxes and on the map at once (that time was not drawn with them yet)
    with tab.expect_request(_is_plot) as sent:
        tab.click("[data-vw=scale-fixed]")
    assert tab.input_value("[data-vw=vmin]") == "5" and tab.input_value("[data-vw=vmax]") == "9"
    assert _asked(sent.value)["vmin"] == ["5"] and _asked(sent.value)["vmax"] == ["9"]


def test_fvcom_output(page, tmp_path, monkeypatch):
    from viewer_simulations import fvcom_simulation

    tab, app = page
    # without geoviews, coastlines can still be drawn, with cartopy alone
    monkeypatch.setattr(viewer_app, "_installed", lambda *names: "geoviews" not in names)
    tab.reload()
    root = fvcom_simulation(str(tmp_path / "fvcom"))
    tab.fill("#f-simdir", root)
    tab.wait_for_function("() => !document.querySelector('#vw-apply').disabled")
    tab.click("#vw-apply")
    tab.wait_for_selector("[data-vw=body]:not([hidden])", timeout=60000)
    _times_in(tab, app)
    tab.fill("[data-vw=search]", "temperature")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("[data-vw=plot-wrap] [class^=bk]", timeout=30000)
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    assert tab.is_hidden("[data-vw=plot-error]")
    assert "values on the nodes" in tab.text_content("[data-vw=info]")
    # the sigma layers, by name
    assert tab.locator("[data-vw=level] option").all_text_contents() == ["Layer 1 (surface)", "Layer 2", "Layer 3", "Layer 4", "Layer 5 (bottom)"]
    tab.select_option("[data-vw=level]", "4")
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    assert tab.is_hidden("[data-vw=plot-error]")
    # coastlines are ticked by default (so the map above has one): off and on again redraws it each time
    assert tab.is_enabled("[data-vw=coast]") and tab.is_checked("[data-vw=coast]")
    tab.uncheck("[data-vw=coast]")
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    assert tab.is_hidden("[data-vw=plot-error]")
    tab.check("[data-vw=coast]")
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    assert tab.is_hidden("[data-vw=plot-error]")
    tab.screenshot(path=os.environ.get("OCEANVAL_VIEWER_SCREENSHOT", "/dev/null"), type="png")
    # it plays
    _watch_play(tab)
    tab.click("[data-vw=play]")
    tab.wait_for_function("() => window.__seen.length >= 3", timeout=30000)
    tab.click("[data-vw=play]")
    assert tab.is_hidden("[data-vw=plot-error]")
    # a variable on the elements
    tab.fill("[data-vw=search]", "velocity")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    assert "values on the elements" in tab.text_content("[data-vw=info]")
    assert tab.is_hidden("[data-vw=plot-error]")
    # not for other output
    tab.click("#back-open")
    tab.wait_for_selector("#vw-setup-pop:not([hidden])")


@pytest.fixture
def gated_times(monkeypatch):
    """The times of the files after the first of each file type are not read in the background until the gate is opened."""
    import threading

    from oceanval import viewer_catalogue as catalogue

    gate = threading.Event()
    real = catalogue._read_times

    def gated(*args, **kwargs):
        gate.wait(60)
        return real(*args, **kwargs)

    monkeypatch.setattr(catalogue, "_read_times", gated)
    yield gate
    gate.set()


def _open_viewer(tab, simulation):
    tab.fill("#f-simdir", simulation)
    tab.wait_for_function("() => !document.querySelector('#vw-apply').disabled")
    tab.click("#vw-apply")
    tab.wait_for_selector("[data-vw=body]:not([hidden])", timeout=60000)


def test_the_viewer_opens_while_the_times_are_read(page, simulation, gated_times):
    tab, app = page
    _open_viewer(tab, simulation)
    # the years wait for the times, with a count of the files' times read
    tab.wait_for_selector("[data-vw=c-years]:not([hidden])")
    assert tab.is_disabled("[data-vw=first]") and tab.is_disabled("[data-vw=last]")
    tab.wait_for_function("() => /Reading the times of the files: \\d+\\/48/.test(document.querySelector('[data-vw=times-text]').textContent)")
    assert tab.is_visible("[data-vw=times]") and tab.is_visible("[data-vw=times-meter]")
    # a variable is mapped meanwhile (its first time), and its time cannot be moved yet
    tab.fill("[data-vw=search]", "chl")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("[data-vw=plot-wrap] [class^=bk]", timeout=30000)
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    assert tab.is_hidden("[data-vw=plot-error]")
    assert tab.is_disabled("[data-vw=step]") and tab.is_disabled("[data-vw=play]") and tab.is_disabled("[data-vw=next]")
    assert "being read" in tab.text_content("[data-vw=info]")
    assert tab.is_enabled("[data-vw=level]") and tab.is_enabled("[data-vw=cmap]")
    # the times come in: the years and the time can be used, and the map is as it was
    gated_times.set()
    tab.wait_for_function("() => !document.querySelector('[data-vw=first]').disabled", timeout=60000)
    assert tab.is_hidden("[data-vw=times]") and tab.is_hidden("[data-vw=times-meter]")
    # (the year boxes' arrow lists the years of the output: see test_first_and_last_year)
    assert tab.is_enabled("[data-vw=step]") and tab.is_enabled("[data-vw=play]")
    assert tab.get_attribute("[data-vw=step]", "max") == "23"
    assert "being read" not in tab.text_content("[data-vw=info]")
    assert tab.is_hidden("[data-vw=plot-error]")
    assert "2000-01-16" in tab.text_content("[data-vw=when]")


def test_first_and_last_year(page, simulation):
    tab, app = page
    _open_viewer(tab, simulation)
    _times_in(tab, app)
    tab.fill("[data-vw=search]", "chl")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("[data-vw=plot-wrap] [class^=bk]", timeout=30000)
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    # the arrow lists the years of the output
    tab.click("[data-vw=first]", position={"x": tab.locator("[data-vw=first]").bounding_box()["width"] - 8, "y": 10})
    assert tab.locator(".year-list li").all_text_contents() == ["2000", "2001"]
    tab.click(".year-list li >> text=2001")
    assert tab.input_value("[data-vw=first]") == "2001"
    # the time is cut to 2001: the slider, the arrows and the dates
    tab.wait_for_function("() => document.querySelector('[data-vw=step]').min === '12'")
    assert tab.get_attribute("[data-vw=step]", "max") == "23"
    tab.wait_for_function("() => document.querySelector('[data-vw=when]').textContent.startsWith('2001-01')")
    tab.wait_for_selector("[data-vw=plot-busy][hidden]", state="attached")
    assert tab.is_disabled("[data-vw=prev]")
    for _ in range(11):
        tab.click("[data-vw=next]")
    assert tab.is_disabled("[data-vw=next]") and tab.get_attribute("[data-vw=step]", "max") == tab.input_value("[data-vw=step]")
    assert tab.text_content("[data-vw=when]").startswith("2001-12")
    # Play stays in the years
    tab.fill("[data-vw=last]", "2001")
    # (the years settle a moment after they are typed, and stop an animation that is going)
    tab.wait_for_timeout(800)
    _watch_play(tab)
    tab.click("[data-vw=play]")
    tab.wait_for_function("() => window.__seen.length >= 4", timeout=30000)
    tab.click("[data-vw=play]")
    assert all(date.startswith("2001-") for date in tab.evaluate("() => window.__seen"))
    assert tab.is_hidden("[data-vw=plot-error]")
    # the colour scale of the animation is for those years only
    assert tab.input_value("[data-vw=vmin]") == ""
    # one year: the slider's steps are the year's
    tab.fill("[data-vw=first]", "2000")
    tab.fill("[data-vw=last]", "2000")
    tab.wait_for_function("() => document.querySelector('[data-vw=step]').max === '11'")
    assert tab.get_attribute("[data-vw=step]", "min") == "0"
    # the time shown (December 2001) was outside, so the map is moved to the nearest time inside
    tab.wait_for_function("() => document.querySelector('[data-vw=when]').textContent.startsWith('2000-12')")
    # blank boxes are no limit
    tab.fill("[data-vw=first]", "")
    tab.fill("[data-vw=last]", "")
    tab.wait_for_function("() => document.querySelector('[data-vw=step]').max === '23'")
    assert tab.is_hidden("[data-vw=years-note]")


def test_years_that_cannot_be_used(page, simulation):
    tab, app = page
    _open_viewer(tab, simulation)
    _times_in(tab, app)
    tab.fill("[data-vw=search]", "chl")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("[data-vw=plot-wrap] [class^=bk]", timeout=30000)
    tab.fill("[data-vw=first]", "20x")
    tab.wait_for_selector("[data-vw=years-note]:not([hidden])")
    assert "First year is a whole number" in tab.text_content("[data-vw=years-note]")
    assert "is-invalid" in tab.get_attribute("[data-vw=first]", "class")
    tab.fill("[data-vw=first]", "2001")
    tab.fill("[data-vw=last]", "2000")
    tab.wait_for_function("() => document.querySelector('[data-vw=years-note]').textContent.includes('after the last')")
    assert "is-invalid" in tab.get_attribute("[data-vw=last]", "class")
    # nothing is cut until they are right
    assert tab.get_attribute("[data-vw=step]", "min") == "0" and tab.get_attribute("[data-vw=step]", "max") == "23"
    tab.fill("[data-vw=first]", "1990")
    tab.fill("[data-vw=last]", "1995")
    tab.wait_for_function("() => document.querySelector('[data-vw=years-note]').textContent.includes('no output between 1990 and 1995')")
    assert "from 2000 to 2001" in tab.text_content("[data-vw=years-note]")
    assert tab.get_attribute("[data-vw=step]", "max") == "23"
    # a year beyond the output is only mentioned: the rest of the range is used
    tab.fill("[data-vw=first]", "1990")
    tab.fill("[data-vw=last]", "2000")
    tab.wait_for_function("() => document.querySelector('[data-vw=step]').max === '11'")
    assert "is-invalid" not in tab.get_attribute("[data-vw=first]", "class")
    assert "starts in 2000" in tab.text_content("[data-vw=years-note]")
    assert tab.is_hidden("[data-vw=plot-error]")


def test_years_are_forgotten_with_new_files(page, simulation):
    tab, app = page
    _open_viewer(tab, simulation)
    _times_in(tab, app)
    tab.fill("[data-vw=first]", "2001")
    tab.click("#back-open")
    tab.wait_for_selector("#vw-setup-pop:not([hidden])")
    tab.click("#vw-apply")
    tab.wait_for_selector("[data-vw=body]:not([hidden])", timeout=60000)
    _times_in(tab, app)
    assert tab.input_value("[data-vw=first]") == "" and tab.input_value("[data-vw=last]") == ""


@pytest.mark.parametrize("width, rows", [(1300, 2), (900, 3)])
def test_the_landing_page_has_five_tiles_that_wrap(browser, tmp_path, width, rows):
    """Five tiles: three and two on a wide window, and two a row on a narrow one."""
    app = App(cwd=str(tmp_path))
    app.leftovers_asked = True
    url = app.start()
    try:
        tab = browser.new_page(viewport={"width": width, "height": 900})
        tab.goto(url)
        tab.wait_for_selector("#view-home:not([hidden])")
        names = ("help", "viewer", "analyze", "simcompare", "validate")
        boxes = [tab.locator(f"#home-{name}").bounding_box() for name in names]
        assert len({round(box["y"]) for box in boxes}) == rows
        # Tools & checks is first, at the top left
        assert boxes[0]["y"] == min(box["y"] for box in boxes) and boxes[0]["x"] == min(box["x"] for box in boxes)
        # nothing is wider than the window
        assert tab.evaluate("document.documentElement.scrollWidth") <= width
        tab.close()
    finally:
        app.close()


def test_the_simulation_box_lists_the_bank(page, simulation):
    """The arrow in the simulation box lists the simulations in the bank, as for Analyze and Validate."""
    from oceanval import user_cache

    tab, app = page
    user_cache.add_to_bank("Viewed", simulation)
    _open_viewer(tab, simulation)
    tab.click("#back-open")
    tab.wait_for_selector("#vw-setup-pop:not([hidden])")
    tab.fill("#f-simdir", "")
    tab.click("#f-simdir-recent-toggle")
    assert tab.locator("#f-simdir-recent .combo__head").text_content() == "In the simulation bank"
    tab.locator("#f-simdir-recent .combo__option", has_text=simulation).first.click()
    assert tab.input_value("#f-simdir") == simulation
    assert tab.input_value("#f-name") == "Viewed"



# ---- up to four simulations side by side ----

def _apply(tab, number, directory):
    """Gives a panel its simulation through its box, and waits for the panel to show it."""
    tab.fill("#f-simdir", directory)
    tab.wait_for_function("() => !document.querySelector('#vw-apply').disabled", timeout=60000)
    tab.click("#vw-apply")
    tab.wait_for_selector(f"[data-panel='{number}'] [data-vw=body]:not([hidden])", timeout=120000)


def test_the_first_panels_box_opens_by_itself_and_stays_until_a_simulation_is_given(page, simulation):
    tab, app = page
    # in the middle of the panel, with no button for it and no way to close it
    assert tab.is_hidden("[data-panel='1'] [data-vw=idle-btn]")
    assert tab.is_hidden("#vw-setup-close")
    box = tab.locator("#vw-setup-pop").bounding_box()
    panel = tab.locator("[data-panel='1']").bounding_box()
    assert abs((box["x"] + box["width"] / 2) - (panel["x"] + panel["width"] / 2)) < 4
    tab.keyboard.press("Escape")
    tab.mouse.click(5, 600)
    assert tab.is_visible("#vw-setup-pop")
    _apply(tab, 1, simulation)
    # applied: the box goes, and the panel has no "Provide simulation info" button
    assert tab.is_hidden("#vw-setup-pop")
    assert tab.locator("[data-vw=info-btn]").count() == 0
    assert tab.locator("[data-panel='1'] [data-vw=remove]").is_hidden()


def test_panels_are_added_and_removed(page, simulation, tmp_path):
    from viewer_simulations import nemo_simulation

    tab, app = page
    other = nemo_simulation(str(tmp_path / "other"), years=(2000,))
    _apply(tab, 1, simulation)
    # one panel: the + is beside it, and an added panel has a large button, which opens its own box
    assert tab.get_attribute("#vw-panels", "data-count") == "1"
    tab.click("#vw-add")
    tab.wait_for_selector("[data-panel='2'] [data-vw=idle-btn]")
    assert tab.get_attribute("#vw-panels", "data-count") == "2"
    big = tab.locator("[data-panel='2'] [data-vw=idle-btn]").bounding_box()
    assert big["width"] > 300 and big["width"] > big["height"]
    tab.click("[data-panel='2'] [data-vw=idle-btn]")
    tab.wait_for_selector("#vw-setup-pop:not([hidden])")
    # this box can be closed, and the button is back
    tab.keyboard.press("Escape")
    assert tab.is_hidden("#vw-setup-pop") and tab.is_visible("[data-panel='2'] [data-vw=idle-btn]")
    tab.click("[data-panel='2'] [data-vw=idle-btn]")
    _apply(tab, 2, other)
    # each panel keeps its own simulation, and plots on its own
    assert [app.viewers[n].catalogue is not None for n in (1, 2)] == [True, True]
    tab.fill("[data-panel='1'] [data-vw=search]", "sossheig")
    tab.keyboard.press("Enter")
    tab.fill("[data-panel='2'] [data-vw=search]", "chl")
    tab.keyboard.press("Enter")
    for number in (1, 2):
        tab.wait_for_selector(f"[data-panel='{number}'] [data-vw=plot-wrap] [class^=bk]", timeout=30000)
    # up to four, and the + goes at four
    tab.click("#vw-add")
    tab.click("#vw-add")
    tab.wait_for_selector("[data-panel='4']")
    assert tab.is_hidden("#vw-add")
    # the x closes a panel and lets go of its files
    gone = app.viewers[2].catalogue
    tab.click("[data-panel='2'] [data-vw=remove]")
    tab.wait_for_selector("[data-panel='2']", state="detached")
    assert gone._stop.is_set() and app.viewers[1].catalogue is not None
    assert tab.is_visible("#vw-add")


def test_the_depth_list_does_not_close_a_box(page, simulation):
    tab, app = page
    _apply(tab, 1, simulation)
    tab.click("#vw-add")
    tab.click("[data-panel='2'] [data-vw=idle-btn]")
    tab.fill("#f-simdir", simulation)
    tab.wait_for_function("() => document.querySelector('#f-ndown').value === '2'")
    box = tab.locator("#f-ndown").bounding_box()
    tab.mouse.click(box["x"] + box["width"] - 8, box["y"] + box["height"] / 2)
    tab.wait_for_selector(".year-list:not([hidden]) li")
    tab.locator(".year-list li").nth(1).click()
    assert tab.is_visible("#vw-setup-pop") and tab.input_value("#f-ndown") == "1"


def test_the_variable_list_is_resized_by_its_divider(page, simulation):
    tab, app = page
    _apply(tab, 1, simulation)
    tab.fill("[data-panel='1'] [data-vw=search]", "sossheig")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("[data-panel='1'] [data-vw=plot-wrap] [class^=bk]", timeout=30000)
    side = lambda: tab.evaluate("() => document.querySelector('[data-panel=\"1\"] .vw-side').getBoundingClientRect().width")
    plot = lambda: tab.evaluate("() => document.querySelector('[data-panel=\"1\"] [data-vw=plot-wrap]').getBoundingClientRect().width")
    start, plot_before = side(), plot()
    assert round(start) == 280
    divider = tab.locator("[data-panel='1'] [data-vw=split]").bounding_box()
    x, y = divider["x"] + divider["width"] / 2, divider["y"] + divider["height"] / 2
    tab.mouse.move(x, y)
    tab.mouse.down()
    tab.mouse.move(x - 100, y, steps=8)
    tab.mouse.up()
    tab.wait_for_function("() => document.querySelector('[data-panel=\"1\"] .vw-side').getBoundingClientRect().width < 200")
    assert round(side()) == 180 and plot() > plot_before + 90
    # as small as a thin strip, with the contents hidden and the divider still there to drag out
    tab.focus("[data-panel='1'] [data-vw=split]")
    tab.keyboard.press("Home")
    assert round(side()) == 40 and tab.is_hidden("[data-panel='1'] [data-vw=side-body]")
    tab.keyboard.press("ArrowRight")
    tab.keyboard.press("End")
    assert tab.is_visible("[data-panel='1'] [data-vw=side-body]") and side() > 280
    # a double-click resets it, and the width is kept for the panel
    tab.dblclick("[data-panel='1'] [data-vw=split]")
    assert round(side()) == 280


def test_two_panels_fill_the_window_side_by_side(page, simulation, tmp_path):
    from viewer_simulations import nemo_simulation

    tab, app = page
    _apply(tab, 1, simulation)
    tab.click("#vw-add")
    tab.click("[data-panel='2'] [data-vw=idle-btn]")
    _apply(tab, 2, nemo_simulation(str(tmp_path / "other"), years=(2000,)))
    one, two = (tab.locator(f"[data-panel='{n}']").bounding_box() for n in (1, 2))
    assert abs(one["y"] - two["y"]) < 2 and one["x"] + one["width"] <= two["x"] + 1
    assert one["width"] > 600 and two["width"] > 600


def test_a_panel_opened_in_another_tab_follows_the_first_both_ways(page, simulation):
    tab, app = page
    _apply(tab, 1, simulation)
    tab.wait_for_selector("[data-panel='1'] [data-vw=list] li")
    tab.click("[data-panel='1'] [data-vw=list] li >> nth=0")
    tab.wait_for_selector("[data-panel='1'] [data-vw=plot-wrap] [class^=bk]", timeout=30000)
    with tab.context.expect_page() as opened:
        tab.click("[data-panel='1'] [data-vw=open-tab]")
    other = opened.value
    errors = []
    other.on("pageerror", lambda error: errors.append(str(error)))
    other.wait_for_selector("[data-panel='1'] [data-vw=body]:not([hidden])", timeout=60000)
    # that panel alone, with nothing that would end the other tab's work, and no button to open a third
    assert other.locator("[data-panel]").count() == 1
    for hidden in ("#back-open", "#restart-open", "#vw-add", "[data-vw=remove]", "[data-vw=open-tab]"):
        assert other.is_hidden(hidden)
    ticked = "[data-panel='1'] [data-vw=list] li.is-chosen"
    other.wait_for_selector(ticked, timeout=30000)
    # a choice in the new tab reaches the first
    other.click("[data-panel='1'] [data-vw=list] li >> nth=2")
    tab.wait_for_function("() => [...document.querySelectorAll(\"[data-panel='1'] [data-vw=list] li.is-chosen\")].some((li) => li.textContent.includes('votemper'))", timeout=30000)
    # and one in the first reaches the new tab
    tab.click("[data-panel='1'] [data-vw=next]")
    step = tab.input_value("[data-panel='1'] [data-vw=step]")
    other.wait_for_function(f"() => document.querySelector(\"[data-panel='1'] [data-vw=step]\").value === '{step}'", timeout=30000)
    # Play and Pause
    other.click("[data-panel='1'] [data-vw=play]")
    tab.wait_for_function("() => document.querySelector(\"[data-panel='1'] [data-vw=play]\").getAttribute('aria-pressed') === 'true'", timeout=30000)
    other.click("[data-panel='1'] [data-vw=play]")
    tab.wait_for_function("() => document.querySelector(\"[data-panel='1'] [data-vw=play]\").getAttribute('aria-pressed') === 'false'", timeout=30000)
    # the first going back to the first page tells the new tab
    tab.click("#back-open")
    other.wait_for_selector("#mirror-closed:not([hidden])", timeout=30000)
    assert errors == []
    other.close()
