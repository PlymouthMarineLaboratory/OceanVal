"""`cdo sinfo` on every model file (oceanval.cdo_check): a file it crashes on
means OceanVal cannot work with the data."""

import os
import time

import pytest

from oceanval import analysis, cdo_check, prompts
from oceanval.app import App

pytestmark = pytest.mark.cdo_check


def files(folder, *names):
    folder.mkdir(exist_ok=True)
    paths = []
    for name in names:
        path = folder / name
        path.write_text("x")
        paths.append(str(path))
    return paths


def finished(check):
    assert check.wait(30)
    return check


class TestCheck:
    def test_a_file_that_segfaults_is_found_and_the_others_are_not(self, tmp_path, fake_cdo):
        paths = files(tmp_path, "a.nc", "bad_b.nc", "c.nc")
        check = finished(cdo_check.Check(paths).start())
        assert check.faulty == [paths[1]]
        assert {path: check.results[path]["status"] for path in paths} == {
            paths[0]: cdo_check.OK, paths[1]: cdo_check.SEGFAULT, paths[2]: cdo_check.OK,
        }
        assert check.results[paths[1]]["returncode"] < 0

    def test_every_file_is_run_even_after_a_failure(self, tmp_path, fake_cdo):
        paths = files(tmp_path, "bad_a.nc", "b.nc", "bad_c.nc", "d.nc")
        check = finished(cdo_check.Check(paths, workers=1).start())
        assert fake_cdo.calls() == sorted(paths)
        assert check.faulty == [paths[0], paths[2]]

    def test_an_error_that_is_not_a_crash_is_not_a_fault(self, tmp_path, fake_cdo):
        paths = files(tmp_path, "error.nc")
        check = finished(cdo_check.Check(paths).start())
        assert check.faulty == []
        assert check.results[paths[0]]["status"] == cdo_check.ERROR
        assert "cdo: error" in check.results[paths[0]]["stderr"]

    def test_no_cdo_is_not_a_fault(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path))
        paths = files(tmp_path, "bad.nc")
        check = finished(cdo_check.Check(paths).start())
        assert check.faulty == [] and check.results[paths[0]]["status"] == cdo_check.UNKNOWN

    def test_a_file_that_takes_too_long_is_not_a_fault(self, tmp_path, fake_cdo):
        paths = files(tmp_path, "slow.nc")
        check = finished(cdo_check.Check(paths, timeout=0.5).start())
        assert check.faulty == [] and check.results[paths[0]]["status"] == cdo_check.UNKNOWN

    def test_the_first_fault_is_told_once_and_done_when_all_are_looked_at(self, tmp_path, fake_cdo):
        told, ended = [], []
        paths = files(tmp_path, "bad_a.nc", "bad_b.nc")
        check = cdo_check.Check(paths, on_fault=told.append, done=lambda c: ended.append(c.faulty), workers=1).start()
        check.wait(30)
        deadline = time.time() + 10
        while not ended and time.time() < deadline:
            time.sleep(0.05)
        assert len(told) == 1 and ended == [paths]

    def test_cancel_kills_what_is_running(self, tmp_path, fake_cdo):
        paths = files(tmp_path, "slow_a.nc", "slow_b.nc")
        check = cdo_check.Check(paths, workers=2).start()
        deadline = time.time() + 10
        while len(fake_cdo.calls()) < 2 and time.time() < deadline:
            time.sleep(0.05)
        start = time.time()
        check.cancel()
        assert check.wait(10) and time.time() - start < 10

    def test_a_path_is_checked_once(self, tmp_path, fake_cdo):
        paths = files(tmp_path, "a.nc")
        check = finished(cdo_check.Check(paths + paths).start())
        assert fake_cdo.calls() == paths


class TestMessage:
    def test_it_names_the_link_and_the_cf_compliance(self, tmp_path, fake_cdo):
        text = cdo_check.message(["/x/bad.nc"], 3)
        assert "https://anaconda.org/conda-forge/cdo" in text
        assert "CF-compliant" in text
        assert "1 of 3" in text and "/x/bad.nc" in text

    def test_only_a_few_files_are_named(self):
        faulty = [f"/x/bad{n}.nc" for n in range(9)]
        text = cdo_check.message(faulty, 20)
        assert "9 of 20" in text and "bad0.nc" in text and "bad8.nc" not in text and "and 4 more" in text


class TestInThePackage:
    def test_start_is_nothing_when_the_window_started_the_run(self, tmp_path, fake_cdo):
        paths = files(tmp_path, "bad.nc")
        with prompts.told_by(lambda news: None, "@@"):
            assert cdo_check.start(paths) is None
        assert cdo_check.start([]) is None

    def test_require_raises_for_a_segfault_and_not_otherwise(self, tmp_path, fake_cdo):
        cdo_check.require(None)
        cdo_check.require(cdo_check.start(files(tmp_path, "a.nc")))
        with pytest.raises(ValueError, match="CF-compliant"):
            cdo_check.require(cdo_check.start(files(tmp_path / "b", "bad.nc")))

    def test_analyze_refuses_files_cdo_crashes_on(self, tmp_path, fake_cdo):
        import oceanval
        from tests.simulations import write_simulation

        sim = tmp_path / "sim"
        write_simulation(sim)
        victim = sorted(sim.rglob("*.nc"))[0]
        victim.rename(victim.with_name("bad_" + victim.name))
        with pytest.raises(ValueError, match="segfaulted"):
            oceanval.analyze(
                str(sim), {"Temperature": "thetao"},
                climatology={"Temperature": {"start": 2011, "end": 2012, "surface": True}},
                n_dirs_down=2, out_dir=str(tmp_path / "out"), cores=1, view=False,
            )


def test_the_window_blocks_everything_once_a_file_crashes_cdo(tmp_path, fake_cdo):
    from tests.simulations import write_simulation

    sim = tmp_path / "sim"
    write_simulation(sim)
    bad = sorted(sim.rglob("*.nc"))[0]
    bad.rename(bad.with_name("bad_" + bad.name))
    app = App(cwd=str(tmp_path))
    try:
        app.choose("analyze")
        with app._lock:
            app._start_cdo_check([{"simdir": str(sim), "ndown": 2, "exclude": None, "require": None}])
        deadline = time.time() + 30
        while app._cdo_failed is None and time.time() < deadline:
            time.sleep(0.1)
        state = app._cdo_check_state()
        assert app._cdo_failed and "CF-compliant" in app._cdo_failed
        assert state["faulty"] and state["link"] == cdo_check.LINK
        # nothing is run after it
        app.start_run(["analyze", "x.py"], "python x.py")
        assert app.run is None
        # starting again forgets it
        app.reset()
        assert app._cdo_failed is None and app._cdo_check_state() is None
    finally:
        app.close()


def test_the_same_simulation_is_not_checked_twice(tmp_path, fake_cdo):
    from tests.simulations import write_simulation

    sim = tmp_path / "sim"
    write_simulation(sim)
    app = App(cwd=str(tmp_path))
    try:
        where = [{"simdir": str(sim), "ndown": 2, "exclude": None, "require": None}]
        with app._lock:
            app._start_cdo_check(where)
        deadline = time.time() + 30
        while (app._cdo_check is None or not app._cdo_check.finished) and time.time() < deadline:
            time.sleep(0.1)
        first = app._cdo_check
        with app._lock:
            app._start_cdo_check(where)
        assert app._cdo_check is first and first.faulty == []
        assert app._cdo_check_state()["message"] is None
    finally:
        app.close()


def test_the_page_shows_a_pop_out_with_no_way_on(browser, tmp_path, fake_cdo):
    from tests.simulations import write_simulation

    sim = tmp_path / "sim"
    write_simulation(sim)
    bad = sorted(sim.rglob("*.nc"))[0]
    bad.rename(bad.with_name("bad_" + bad.name))
    app = App(cwd=str(tmp_path))
    url = app.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#home-viewer")
        assert page.is_hidden("#cdo-failed")
        with app._lock:
            app._start_cdo_check([{"simdir": str(sim), "ndown": 2, "exclude": None, "require": None}])
        page.wait_for_selector("#cdo-failed", state="visible", timeout=30000)
        text = page.inner_text("#cdo-failed")
        assert "CF-compliant" in text and "Try changing the CDO version" in text
        assert page.get_attribute("#cdo-failed a", "href") == "https://anaconda.org/conda-forge/cdo"
        assert page.evaluate("document.querySelector('main').inert")
        # nothing closes it but starting again
        page.keyboard.press("Escape")
        assert page.is_visible("#cdo-failed")
        page.click("#cdo-failed-restart")
        page.wait_for_selector("#cdo-failed", state="hidden", timeout=30000)
        assert app._cdo_failed is None
    finally:
        app.close()
