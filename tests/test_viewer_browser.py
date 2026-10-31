"""View simulation output in a browser (ported from ncviewer's tests): the
landing page's tile, the setup page filling in the directories down from the
folders' layout and taking the missing values, Continue reading the files,
and the viewer mapping a variable once its file type is chosen, summing,
playing, and cutting to the years chosen. Skipped where Chromium is not
installed."""

import os
import time

import pytest

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
    tab.wait_for_selector("#view-setup:not([hidden])")
    yield tab, app
    assert errors == []
    tab.close()
    app.close()


def _times_in(tab, app):
    """Waits for the times of every file, which are read in the background once the viewer has opened."""
    for _ in range(600):
        if app.viewer_catalogue is not None and app.viewer_catalogue.times_complete and app.viewer_times is None:
            break
        time.sleep(0.1)
    assert app.viewer_catalogue.times_complete
    tab.wait_for_function("() => !document.getElementById('vw-first').disabled", timeout=60000)


def test_files_page_finds_the_depth(page, simulation):
    tab, app = page
    tab.fill("#f-simdir", simulation)
    tab.wait_for_function("() => document.querySelector('#f-ndown').value === '2'")
    tab.wait_for_selector("#ndown-found:not(.is-finding):not([hidden])")
    assert tab.text_content("#ndown-found") == "48 netCDF files found"
    # the layout is told in a pop-out (the conftest dismisses it); the box is bold
    assert tab.evaluate("() => getComputedStyle(document.querySelector('#f-ndown')).fontWeight") == "700"
    assert tab.locator("#ndown-layout").count() == 0
    assert "is-detected" in tab.get_attribute("#f-ndown", "class")
    assert tab.is_enabled("#continue")
    # the files the filters leave out are not mentioned, only the number there are
    tab.fill("#f-exclude", "grid_T")
    tab.wait_for_function("() => document.querySelector('#ndown-found').textContent.startsWith('24 ')")
    assert tab.text_content("#ndown-found") == "24 netCDF files found"
    tab.fill("#f-exclude", "")
    # typing a depth makes it the user's own
    tab.fill("#f-ndown", "1")
    tab.wait_for_selector("#m-simulation .is-nofiles")
    assert "is-detected" not in tab.get_attribute("#f-ndown", "class")
    assert not tab.is_enabled("#continue")


def test_missing_values_on_the_files_page(page, simulation):
    tab, app = page
    tab.fill("#f-simdir", simulation)
    tab.wait_for_function("() => !document.querySelector('#continue').disabled")
    # the boxes are there from the start, empty, and an untouched row stops nothing
    assert tab.locator("#missing-rows .missing-rows__row").count() == 1
    assert tab.is_visible("#missing-from-0") and tab.is_visible("#missing-to-0")
    assert tab.input_value("#missing-from-0") == ""
    assert tab.is_enabled("#continue")
    tab.fill("#missing-from-0", "abc")
    # a box that is not a number is marked, and Continue waits
    assert "is-invalid" in tab.get_attribute("#missing-from-0", "class")
    assert tab.is_disabled("#continue")
    assert "not a number" in tab.text_content("#m-missing-rows")
    # there is one From and one To, with no way to add another
    tab.fill("#missing-from-0", "-999")
    assert tab.locator("#missing-add").count() == 0
    assert tab.locator(".missing-rows__add, .missing-rows__remove").count() == 0
    assert tab.locator("#missing-rows .missing-rows__row").count() == 1
    tab.fill("#missing-to-0", "-900")
    assert tab.is_enabled("#continue")
    # clearing the From box leaves it to fill in again
    tab.fill("#missing-from-0", "")
    assert tab.input_value("#missing-from-0") == ""
    tab.fill("#missing-to-0", "")
    tab.fill("#missing-from-0", "10")
    tab.fill("#missing-to-0", "12.5")
    tab.click("#continue")
    tab.wait_for_function("() => document.querySelector('#view-viewer:not([hidden])')", timeout=60000)
    assert app.viewer_missing == [(10.0, 12.5)]
    # going back shows it again
    tab.click("#vw-back")
    tab.wait_for_selector("#view-setup:not([hidden])")
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
    assert tab.is_enabled("#continue")
    # typing over it makes it the user's: the amber goes, and nothing is put there again
    tab.fill("#missing-from-0", "-9")
    assert "is-found" not in tab.get_attribute("#missing-from-0", "class")
    assert tab.is_hidden("#missing-found")
    tab.fill("#f-require", "land_0")
    tab.wait_for_selector("#ndown-found:not(.is-finding):not([hidden])")
    time.sleep(0.5)
    assert tab.input_value("#missing-from-0") == "-9"
    # and what is in the rows is what Continue is given
    tab.click("#continue")
    tab.wait_for_selector("#view-viewer:not([hidden])", timeout=60000)
    assert app.viewer_missing == [(-9.0, -999.0)]


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
    assert tab.is_enabled("#continue")
    tab.click("#continue")
    tab.wait_for_selector("#view-viewer:not([hidden])", timeout=60000)
    assert [len(stream["files"]) for stream in app.viewer_catalogue.streams] == [1]
    # back to a directory: the depth is asked and found again
    tab.click("#vw-back")
    tab.wait_for_selector("#view-setup:not([hidden])")
    tab.fill("#f-simdir", root)
    tab.wait_for_function("() => !document.querySelector('#f-ndown').disabled && document.querySelector('#f-ndown').value === '0'")


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
    tab.wait_for_function("() => !document.querySelector('#continue').disabled")
    # the coldest cells of the first map, which the colour scale then leaves out
    tab.fill("#missing-from-0", "0")
    tab.fill("#missing-to-0", "11.3")
    tab.click("#continue")
    tab.wait_for_selector("#view-viewer:not([hidden])", timeout=60000)
    _times_in(tab, app)
    tab.fill("#vw-search", "chloro")
    assert tab.locator("#vw-list li").count() == 1
    # * and ? stand for characters, and are matched within the name, the long name or the units
    tab.fill("#vw-search", "ch*phyll")
    assert tab.locator("#vw-list li").count() == 1
    tab.fill("#vw-search", "*")
    assert tab.locator("#vw-list li").count() > 1
    tab.fill("#vw-search", "temp")
    tab.keyboard.press("Enter")
    # in two file types: nothing is plotted until one is chosen
    assert "Which do you want?" in tab.text_content("#vw-streams")
    assert tab.locator("#vw-plot-wrap [class^=bk]").count() == 0
    tab.click("#vw-streams label.ptr-mode >> nth=1")
    tab.wait_for_selector("#vw-plot-wrap [class^=bk]", timeout=30000)
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    # nothing is said at the bottom of the page (not the files, file types or directory)
    assert tab.text_content("#summary") == ""
    # the name and description are in the list above, so not said again
    assert "Name" not in tab.text_content("#vw-info") and "Description" not in tab.text_content("#vw-info")
    assert "Units" in tab.text_content("#vw-info")
    # the calendar is not shown
    assert "Calendar" not in tab.text_content("#vw-info")
    assert "Time resolutionmonthly" in tab.text_content("#vw-info")
    assert "Times" not in tab.text_content("#vw-info").replace("Time resolution", "")
    # the first and last dates are one row, without the time of day
    info = tab.text_content("#vw-info")
    assert "Dates2000-01-16 \u2013 2001-12-16" in info
    assert "From" not in info and "To2001" not in info
    # nothing is written under the map
    assert tab.locator("#plot-notes, .stage__notes").count() == 0
    # the map fills its box, not a strip of axes
    assert tab.locator("#vw-plot-wrap [class^=bk]").first.bounding_box()["height"] > 300
    tab.click("#vw-next")
    tab.wait_for_function("() => document.querySelector('#vw-when').textContent.startsWith('2000-02')")
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    # a deeper level is drawn too
    tab.select_option("#vw-level", "3")
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    assert tab.is_hidden("#vw-plot-error")
    # nothing but the map: no other plots to choose
    assert tab.locator("#vw-kind, #vw-point, #vw-c-cell").count() == 0
    tab.click("#vw-back")
    tab.wait_for_selector("#view-setup:not([hidden])")


_FRAME = """() => {
  const index = Bokeh.index;
  const views = index.roots ? [...index.roots] : (index[Symbol.iterator] ? [...index] : Object.values(index));
  const shown = views.find((view) => view.el.isConnected && !view.el.closest(".is-back"));
  const box = document.getElementById("vw-plot-wrap").getBoundingClientRect();
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
    tab.wait_for_function("() => !document.querySelector('#continue').disabled")
    tab.click("#continue")
    tab.wait_for_selector("#view-viewer:not([hidden])", timeout=60000)
    _times_in(tab, app)
    tab.fill("#vw-search", "sst")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("#vw-plot-wrap [class^=bk]", timeout=30000)
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    # 40-65N by 20W-10E, cells of a degree, reaching half a cell past the outer points
    aspect = 26 / (31 * np.cos(np.radians(52.5)))
    _fits(tab, aspect)
    # the next time step, drawn behind and then shown, is fitted too
    tab.click("#vw-next")
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
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
    tab.wait_for_function("() => !document.querySelector('#continue').disabled")
    tab.click("#continue")
    tab.wait_for_selector("#view-viewer:not([hidden])", timeout=60000)
    _times_in(tab, app)
    tab.fill("#vw-search", "temp")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("#vw-plot-wrap [class^=bk]", timeout=30000)
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    assert tab.locator("#vw-level option").all_text_contents() == ["0 (bottom)", "1", "2", "3", "4 (surface)"]
    assert tab.input_value("#vw-level") == "4"
    assert tab.is_hidden("#vw-plot-error")
    # a level chosen is kept
    tab.select_option("#vw-level", "0")
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    assert tab.input_value("#vw-level") == "0"


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
        tab.wait_for_function("() => !document.querySelector('#continue').disabled")
        tab.click("#continue")
        tab.wait_for_selector("#view-viewer:not([hidden])", timeout=60000)
        tab.fill("#vw-search", "sossheig")
        tab.keyboard.press("Enter")
        tab.wait_for_selector("#vw-plot-wrap [class^=bk]", timeout=30000)
        tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")

    view(simulation)
    assert len(asked) == 1
    tab.click("#vw-back")
    tab.wait_for_selector("#view-setup:not([hidden])")
    view(other)
    assert len(asked) == 2
    assert tab.text_content("#vw-when").startswith("2010-01")


def test_sum_of_variables(page, simulation):
    tab, app = page
    tab.fill("#f-simdir", simulation)
    tab.wait_for_function("() => !document.querySelector('#continue').disabled")
    tab.click("#continue")
    tab.wait_for_selector("#view-viewer:not([hidden])", timeout=60000)
    row = lambda name: f"#vw-list li[data-name='{name}']"  # noqa: E731
    # tick chl: ptrc_T is its only file type, so it is chosen and drawn
    tab.check(f"{row('chl')} input")
    tab.wait_for_selector("#vw-plot-wrap [class^=bk]", timeout=30000)
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    # sossheig cannot be added to chl: not the same dimensions
    assert "is-incompatible" in tab.get_attribute(row("sossheig"), "class")
    assert tab.is_disabled(f"{row('sossheig')} input")
    # votemper can (it is in two file types, but only ptrc_T has chl)
    tab.check(f"{row('votemper')} input")
    tab.wait_for_function("() => document.querySelector('#vw-info').textContent.includes('Sum of')")
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    assert "chl (mg m-3) + votemper (degC)" in tab.text_content("#vw-info")
    assert tab.is_hidden("#vw-plot-error")
    # the file type is not asked: only one holds both
    assert tab.is_hidden("#vw-streams")
    # Clear unticks everything
    tab.click("#vw-clear")
    assert tab.locator("#vw-list li input:checked").count() == 0
    assert tab.is_hidden("#vw-chosen")
    # a click on a row chooses that variable alone: a second click replaces the first
    tab.click(f"{row('chl')} .vw-list__name")
    tab.wait_for_selector("#vw-plot-wrap [class^=bk]", timeout=30000)
    tab.click(row("votemper"), position={"x": 200, "y": 10})
    assert tab.locator("#vw-list li input:checked").count() == 1
    assert tab.is_checked(f"{row('votemper')} input")
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    assert "Sum of" not in tab.text_content("#vw-info")
    # only the box adds to the sum
    tab.check(f"{row('chl')} input")
    assert tab.locator("#vw-list li input:checked").count() == 2
    tab.wait_for_function("() => document.querySelector('#vw-info').textContent.includes('Sum of')")
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    # an incompatible row cannot be ticked, but a click chooses it alone
    assert tab.is_disabled(f"{row('sossheig')} input")
    tab.click(f"{row('sossheig')} .vw-list__name")
    assert tab.locator("#vw-list li input:checked").count() == 1
    assert tab.is_checked(f"{row('sossheig')} input")
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    # unticking the box leaves nothing chosen
    tab.uncheck(f"{row('sossheig')} input")
    assert tab.locator("#vw-list li input:checked").count() == 0
    tab.click("#vw-back")
    tab.wait_for_selector("#view-setup:not([hidden])")


def _open_chl(tab, app, simulation):
    tab.fill("#f-simdir", simulation)
    tab.wait_for_function("() => !document.querySelector('#continue').disabled")
    tab.click("#continue")
    tab.wait_for_selector("#view-viewer:not([hidden])", timeout=60000)
    _times_in(tab, app)
    tab.fill("#vw-search", "chl")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("#vw-plot-wrap [class^=bk]", timeout=30000)
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")


def _watch_play(tab):
    """Records the dates shown, and any time the "reading the data" cover is up, from now on."""
    tab.evaluate("""() => {
        window.__seen = []; window.__covered = 0;
        const busy = document.getElementById('vw-plot-busy');
        new MutationObserver(() => { if (!busy.hidden) window.__covered += 1; }).observe(busy, {attributes: true});
        const when = document.getElementById('vw-when');
        new MutationObserver(() => window.__seen.push(when.textContent.slice(0, 10)))
            .observe(when, {childList: true, characterData: true, subtree: true});
    }""")


def test_play_is_an_animation(page, simulation):
    tab, app = page
    _open_chl(tab, app, simulation)
    plots = []
    tab.on("request", lambda request: plots.append(request.url) if "api/viewer/plot" in request.url else None)
    _watch_play(tab)
    tab.click("#vw-play")
    # it moves through the times, and the map is never covered or empty
    tab.wait_for_function("() => window.__seen.length >= 4", timeout=30000)
    assert tab.evaluate("() => window.__covered") == 0
    assert tab.locator("#vw-plot-wrap [class^=bk]:visible").count() > 0
    assert tab.get_attribute("#vw-play", "aria-pressed") == "true"
    # each date is the next one in the run
    seen = tab.evaluate("() => window.__seen")
    assert seen[:2] == ["2000-02-15", "2000-03-16"]
    # one colour scale for every frame that was asked for
    scales = {tuple(url.split(name + "=")[1].split("&")[0] for name in ("vmin", "vmax")) for url in plots[1:]}
    assert len(scales) == 1 and "" not in next(iter(scales))
    # pausing stops it
    tab.click("#vw-play")
    assert tab.get_attribute("#vw-play", "aria-pressed") == "false"
    settled = tab.evaluate("() => window.__seen.length")
    tab.wait_for_timeout(800)
    assert tab.evaluate("() => window.__seen.length") <= settled + 1
    assert tab.is_hidden("#vw-plot-error")


def test_play_speed(page, simulation):
    tab, app = page
    _open_chl(tab, app, simulation)
    assert tab.input_value("#vw-speed") == "normal"
    rates = {}
    for speed in ("slow", "fast"):
        tab.select_option("#vw-speed", speed)
        _watch_play(tab)
        tab.click("#vw-play")
        tab.wait_for_timeout(3000)
        tab.click("#vw-play")
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
    assert _figure_ranges(tab) == [-0.5, 9.5, -0.5, 11.5]
    # zoom in, then play: every frame is zoomed the same
    _figure_ranges(tab, [2, 6, 1, 5])
    _watch_play(tab)
    tab.click("#vw-play")
    tab.wait_for_function("() => window.__seen.length >= 3", timeout=30000)
    tab.click("#vw-play")
    assert _figure_ranges(tab) == [2, 6, 1, 5]
    # only the figure on show is kept, not one for every frame
    assert tab.evaluate("() => Object.values(Bokeh.index).filter((v) => v.model && v.model.type === 'Figure').length") == 1
    # the scale of the animation is kept after it, so the map does not change under the user,
    # until Automatic scale is chosen
    assert tab.input_value("#vw-vmin") == ""
    tab.click("#vw-auto")
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    assert tab.is_hidden("#vw-plot-error")
    # choosing a different variable starts from a fresh view
    tab.fill("#vw-search", "sossheig")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    assert _figure_ranges(tab) == [-0.5, 9.5, -0.5, 11.5]


def test_fvcom_output(page, tmp_path, monkeypatch):
    from viewer_simulations import fvcom_simulation

    tab, app = page
    # without geoviews, a mesh's coastline can still be drawn, with cartopy alone
    monkeypatch.setattr(viewer_app, "_installed", lambda *names: "geoviews" not in names)
    tab.reload()
    root = fvcom_simulation(str(tmp_path / "fvcom"))
    tab.fill("#f-simdir", root)
    tab.wait_for_function("() => !document.querySelector('#continue').disabled")
    tab.click("#continue")
    tab.wait_for_selector("#view-viewer:not([hidden])", timeout=60000)
    _times_in(tab, app)
    # pyfvcom2, whose way the mesh is drawn, is credited at the bottom right
    credit = tab.locator("#vw-fvcom-credit")
    assert credit.is_visible() and credit.text_content() == "Plotting is influenced by pyfvcom2"
    assert credit.get_attribute("href") == "https://github.com/PlymouthMarineLaboratory/pyfvcom2"
    tab.fill("#vw-search", "temperature")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("#vw-plot-wrap [class^=bk]", timeout=30000)
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    assert tab.is_hidden("#vw-plot-error")
    assert "values on the nodes" in tab.text_content("#vw-info")
    # the sigma layers, by name
    assert tab.locator("#vw-level option").all_text_contents() == ["Layer 1 (surface)", "Layer 2", "Layer 3", "Layer 4", "Layer 5 (bottom)"]
    tab.select_option("#vw-level", "4")
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    assert tab.is_hidden("#vw-plot-error")
    assert tab.is_enabled("#vw-coast")
    tab.check("#vw-coast")
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    assert tab.is_hidden("#vw-plot-error")
    tab.screenshot(path=os.environ.get("OCEANVAL_VIEWER_SCREENSHOT", "/dev/null"), type="png")
    # it plays
    _watch_play(tab)
    tab.click("#vw-play")
    tab.wait_for_function("() => window.__seen.length >= 3", timeout=30000)
    tab.click("#vw-play")
    assert tab.is_hidden("#vw-plot-error")
    # a variable on the elements
    tab.fill("#vw-search", "velocity")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    assert "values on the elements" in tab.text_content("#vw-info")
    assert tab.is_hidden("#vw-plot-error")
    # not for other output
    tab.click("#vw-back")
    tab.wait_for_selector("#view-setup:not([hidden])")
    assert tab.is_hidden("#vw-fvcom-credit")


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
    tab.wait_for_function("() => !document.querySelector('#continue').disabled")
    tab.click("#continue")
    tab.wait_for_selector("#view-viewer:not([hidden])", timeout=60000)


def test_the_viewer_opens_while_the_times_are_read(page, simulation, gated_times):
    tab, app = page
    _open_viewer(tab, simulation)
    # the years wait for the times, with a count of the files' times read
    tab.wait_for_selector("#vw-c-years:not([hidden])")
    assert tab.is_disabled("#vw-first") and tab.is_disabled("#vw-last")
    tab.wait_for_function("() => /Reading the times of the files: \\d+\\/48/.test(document.getElementById('vw-times-text').textContent)")
    assert tab.is_visible("#vw-times") and tab.is_visible("#vw-times-meter")
    # a variable is mapped meanwhile (its first time), and its time cannot be moved yet
    tab.fill("#vw-search", "chl")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("#vw-plot-wrap [class^=bk]", timeout=30000)
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    assert tab.is_hidden("#vw-plot-error")
    assert tab.is_disabled("#vw-step") and tab.is_disabled("#vw-play") and tab.is_disabled("#vw-next")
    assert "being read" in tab.text_content("#vw-info")
    assert "Dates" not in tab.text_content("#vw-info")
    assert tab.is_enabled("#vw-level") and tab.is_enabled("#vw-cmap")
    # the times come in: the years and the time can be used, and the map is as it was
    gated_times.set()
    tab.wait_for_function("() => !document.getElementById('vw-first').disabled", timeout=60000)
    assert tab.is_hidden("#vw-times") and tab.is_hidden("#vw-times-meter")
    assert tab.get_attribute("#vw-first", "placeholder") == "2000" and tab.get_attribute("#vw-last", "placeholder") == "2001"
    assert tab.is_enabled("#vw-step") and tab.is_enabled("#vw-play")
    assert tab.get_attribute("#vw-step", "max") == "23"
    assert "Dates2000-01-16 – 2001-12-16" in tab.text_content("#vw-info")
    assert tab.is_hidden("#vw-plot-error")
    assert "2000-01-16" in tab.text_content("#vw-when")


def test_first_and_last_year(page, simulation):
    tab, app = page
    _open_viewer(tab, simulation)
    _times_in(tab, app)
    tab.fill("#vw-search", "chl")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("#vw-plot-wrap [class^=bk]", timeout=30000)
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    # the arrow lists the years of the output
    tab.click("#vw-first", position={"x": tab.locator("#vw-first").bounding_box()["width"] - 8, "y": 10})
    assert tab.locator(".year-list li").all_text_contents() == ["2000", "2001"]
    tab.click(".year-list li >> text=2001")
    assert tab.input_value("#vw-first") == "2001"
    # the time is cut to 2001: the slider, the arrows and the dates
    tab.wait_for_function("() => document.getElementById('vw-step').min === '12'")
    assert tab.get_attribute("#vw-step", "max") == "23"
    tab.wait_for_function("() => document.getElementById('vw-when').textContent.startsWith('2001-01')")
    info = tab.text_content("#vw-info")
    assert "Time resolutionmonthly" in info and "Files12 of 24" in info and "Dates2001-01-16 – 2001-12-16" in info
    tab.wait_for_selector("#vw-plot-busy[hidden]", state="attached")
    assert tab.is_disabled("#vw-prev")
    for _ in range(11):
        tab.click("#vw-next")
    assert tab.is_disabled("#vw-next") and tab.get_attribute("#vw-step", "max") == tab.input_value("#vw-step")
    assert tab.text_content("#vw-when").startswith("2001-12")
    # Play stays in the years
    tab.fill("#vw-last", "2001")
    # (the years settle a moment after they are typed, and stop an animation that is going)
    tab.wait_for_timeout(800)
    _watch_play(tab)
    tab.click("#vw-play")
    tab.wait_for_function("() => window.__seen.length >= 4", timeout=30000)
    tab.click("#vw-play")
    assert all(date.startswith("2001-") for date in tab.evaluate("() => window.__seen"))
    assert tab.is_hidden("#vw-plot-error")
    # the colour scale of the animation is for those years only
    assert tab.input_value("#vw-vmin") == ""
    # one year: the slider's steps are the year's
    tab.fill("#vw-first", "2000")
    tab.fill("#vw-last", "2000")
    tab.wait_for_function("() => document.getElementById('vw-step').max === '11'")
    assert tab.get_attribute("#vw-step", "min") == "0"
    # the time shown (December 2001) was outside, so the map is moved to the nearest time inside
    tab.wait_for_function("() => document.getElementById('vw-when').textContent.startsWith('2000-12')")
    # blank boxes are no limit
    tab.fill("#vw-first", "")
    tab.fill("#vw-last", "")
    tab.wait_for_function("() => document.getElementById('vw-step').max === '23'")
    assert tab.is_hidden("#vw-years-note")


def test_years_that_cannot_be_used(page, simulation):
    tab, app = page
    _open_viewer(tab, simulation)
    _times_in(tab, app)
    tab.fill("#vw-search", "chl")
    tab.keyboard.press("Enter")
    tab.wait_for_selector("#vw-plot-wrap [class^=bk]", timeout=30000)
    tab.fill("#vw-first", "20x")
    tab.wait_for_selector("#vw-years-note:not([hidden])")
    assert "First year is a whole number" in tab.text_content("#vw-years-note")
    assert "is-invalid" in tab.get_attribute("#vw-first", "class")
    tab.fill("#vw-first", "2001")
    tab.fill("#vw-last", "2000")
    tab.wait_for_function("() => document.getElementById('vw-years-note').textContent.includes('after the last')")
    assert "is-invalid" in tab.get_attribute("#vw-last", "class")
    # nothing is cut until they are right
    assert tab.get_attribute("#vw-step", "min") == "0" and tab.get_attribute("#vw-step", "max") == "23"
    tab.fill("#vw-first", "1990")
    tab.fill("#vw-last", "1995")
    tab.wait_for_function("() => document.getElementById('vw-years-note').textContent.includes('no output between 1990 and 1995')")
    assert "from 2000 to 2001" in tab.text_content("#vw-years-note")
    assert tab.get_attribute("#vw-step", "max") == "23"
    # a year beyond the output is only mentioned: the rest of the range is used
    tab.fill("#vw-first", "1990")
    tab.fill("#vw-last", "2000")
    tab.wait_for_function("() => document.getElementById('vw-step').max === '11'")
    assert "is-invalid" not in tab.get_attribute("#vw-first", "class")
    assert "starts in 2000" in tab.text_content("#vw-years-note")
    assert tab.is_hidden("#vw-plot-error")


def test_years_are_forgotten_with_new_files(page, simulation):
    tab, app = page
    _open_viewer(tab, simulation)
    _times_in(tab, app)
    tab.fill("#vw-first", "2001")
    tab.click("#vw-back")
    tab.wait_for_selector("#view-setup:not([hidden])")
    tab.click("#continue")
    tab.wait_for_selector("#view-viewer:not([hidden])", timeout=60000)
    _times_in(tab, app)
    assert tab.input_value("#vw-first") == "" and tab.input_value("#vw-last") == ""


@pytest.mark.parametrize("width", [1300, 900, 400])
def test_the_landing_page_is_two_by_two(browser, tmp_path, width):
    """Four tiles, two by two, and one above another on a phone."""
    app = App(cwd=str(tmp_path))
    app.leftovers_asked = True
    url = app.start()
    try:
        tab = browser.new_page(viewport={"width": width, "height": 900})
        tab.goto(url)
        tab.wait_for_selector("#view-home:not([hidden])")
        boxes = [tab.locator(f"#home-{name}").bounding_box() for name in ("viewer", "analyze", "simcompare", "validate")]
        rows = sorted({round(box["y"]) for box in boxes})
        assert len(rows) == (4 if width <= 640 else 2)
        # the first is View simulation output, at the top left
        assert boxes[0]["y"] == min(box["y"] for box in boxes) and boxes[0]["x"] == min(box["x"] for box in boxes)
        # nothing is wider than the window
        assert tab.evaluate("document.documentElement.scrollWidth") <= width
        tab.close()
    finally:
        app.close()


def test_the_simulation_box_lists_those_used_before(page, simulation):
    """A simulation viewed is listed by the arrow in the simulation box, as one analysed or matched up is."""
    tab, app = page
    _open_viewer(tab, simulation)
    tab.click("#vw-back")
    tab.wait_for_selector("#view-setup:not([hidden])")
    tab.fill("#f-simdir", "")
    tab.click("#f-simdir-recent-toggle")
    assert tab.locator("#f-simdir-recent .combo__head").text_content() == "Used before"
    tab.locator("#f-simdir-recent .combo__option", has_text=simulation).first.click()
    assert tab.input_value("#f-simdir") == simulation
