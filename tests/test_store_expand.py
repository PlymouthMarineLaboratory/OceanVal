"""Expand: filling the data store on purpose (oceanval.store_expand), from the
View the data store page.

The servers are stood in for by the fakes of test_data_store.py, so nothing is
downloaded. What is checked is that a request is fetched as a matchup would
fetch it (the store's own rules for areas, levels and what is held), and that
the page's form is checked as it should be.
"""

import datetime
import json
import os

import pytest
from test_data_store import (  # noqa: F401 - the fixtures
    FakeIcesApi,
    cobe_file,
    entries,
    ices_api,
    retired,
    server,
    woa_files,
)

from oceanval import app as app_module
from oceanval import data_store, store_expand, store_view

BUILTIN = app_module.BUILTIN_DATASETS
NITRATE = {"name": "nitrate", "recipe": "woa23"}
TEMPERATURE_WOA = {"name": "temperature", "recipe": "woa23"}
COBE2 = {"name": "temperature", "recipe": "cobe2"}
OCCCI = {"name": "chlorophyll", "recipe": "occci"}
NSBC = {"name": "nitrate", "recipe": "nsbc"}
GLODAP = {"name": "ph", "recipe": "glodap"}
ICES_TEMPERATURE = {"name": "temperature", "recipe": "ices"}
ICES_NITRATE = {"name": "nitrate", "recipe": "ices"}
THIS_YEAR = datetime.date.today().year


def form(*datasets, **boxes):
    """The Expand form, with a small area unless it is given another."""
    body = {"datasets": list(datasets), "lon_min": "3", "lon_max": "4", "lat_min": "54", "lat_max": "55"}
    body.update(boxes)
    return body


@pytest.fixture
def folder(tmp_path, oceanvalrc):
    """A data store folder saved in the settings, as the window has it."""
    path = tmp_path / "store"
    path.mkdir()
    oceanvalrc.write_text(json.dumps({"settings": {"data_store": str(path)}}))
    yield path
    data_store.start(None)


def root_of(folder):
    return data_store.root(str(folder))


def run(request, news=None, **more):
    """store_expand.run, told to the list news; the request is as check() made it."""
    news = [] if news is None else news
    request = dict(request, **more)
    done = store_expand.run(**request, progress=news.append)
    return done, news


def request_of(*datasets, years=None, lon=(3, 4), lat=(54, 55), levels=None):
    return {"datasets": list(datasets), "years": list(years) if years else None,
            "lon_lim": list(lon) if lon else None, "lat_lim": list(lat) if lat else None, "levels": levels}


def finals(news):
    """The last thing said of each item."""
    last = {}
    for each in news:
        if "item" in each:
            last[each["item"]] = each
    return last


# --------------------------------------------------------------------------
# the form


class TestCheck:
    def test_a_good_form_for_a_year_based_dataset(self):
        request, errors = store_expand.check(form(COBE2, start="2000", end="2001"), BUILTIN)
        assert errors == {}
        assert request == {"datasets": [COBE2], "years": [2000, 2001], "lon_lim": [3.0, 4.0], "lat_lim": [54.0, 55.0],
                           "levels": None}

    def test_the_water_column_is_asked_only_for_woa23(self):
        _, errors = store_expand.check(form(NITRATE), BUILTIN)
        assert list(errors) == ["water_column"]
        request, errors = store_expand.check(form(NITRATE, water_column="Yes"), BUILTIN)
        assert errors == {} and request["levels"] == "all" and request["years"] is None
        request, errors = store_expand.check(form(NITRATE, water_column="no"), BUILTIN)
        assert request["levels"] == "top"
        # nothing else has levels to choose between
        request, errors = store_expand.check(form(NSBC), BUILTIN)
        assert errors == {} and request["levels"] is None

    def test_years_are_asked_only_for_the_datasets_they_matter_to(self):
        # nitrate from WOA23 is one climatology; temperature is a decade at a time
        _, errors = store_expand.check(form(NITRATE, water_column="yes"), BUILTIN)
        assert errors == {}
        request, errors = store_expand.check(form(TEMPERATURE_WOA, water_column="yes"), BUILTIN)
        assert set(errors) == {"start"}
        request, errors = store_expand.check(form(TEMPERATURE_WOA, water_column="yes", start="1990", end="2000"), BUILTIN)
        assert errors == {} and request["years"] == [1990, 2000]
        # a mix needs them for the ones that want them
        assert set(store_expand.check(form(NSBC, ICES_TEMPERATURE), BUILTIN)[1]) == {"start"}

    @pytest.mark.parametrize("start,end,box", [
        ("abc", "2000", "start"), ("2000", "1999", "end"), ("2000", "", "end"), ("", "2000", "start"),
        ("2000", str(THIS_YEAR + 1), "end"), ("2000.5", "2001", "start"),
    ])
    def test_the_years_must_be_whole_years_in_order_and_not_to_come(self, start, end, box):
        request, errors = store_expand.check(form(COBE2, start=start, end=end), BUILTIN)
        assert request is None and box in errors

    @pytest.mark.parametrize("boxes", [
        {"lon_min": ""}, {"lat_max": ""}, {"lon_min": "x"}, {"lon_min": "5", "lon_max": "4"},
        {"lat_min": "56", "lat_max": "55"}, {"lat_min": "-91"}, {"lat_max": "91"},
        {"lon_min": "-181"}, {"lon_max": "181"},
    ])
    def test_the_area_must_be_a_whole_box_inside_the_globe(self, boxes):
        request, errors = store_expand.check(form(COBE2, start="2000", end="2000", **boxes), BUILTIN)
        assert request is None and errors

    def test_the_whole_globe_is_a_valid_area(self):
        request, errors = store_expand.check(
            form(COBE2, start="2000", end="2000", lon_min="-180", lon_max="180", lat_min="-90", lat_max="90"), BUILTIN)
        assert errors == {}

    def test_glodap_is_whole_so_it_needs_no_area_years_or_levels(self):
        request, errors = store_expand.check({"datasets": [GLODAP]}, BUILTIN)
        assert errors == {} and request["lon_lim"] is None and request["years"] is None and request["levels"] is None

    def test_a_dataset_must_be_chosen_and_be_a_built_in_one(self):
        assert "datasets" in store_expand.check(form(), BUILTIN)[1]
        assert "datasets" in store_expand.check(form({"name": "nitrate", "recipe": "nope"}), BUILTIN)[1]
        assert "datasets" in store_expand.check({"datasets": "everything"}, BUILTIN)[1]
        assert "datasets" in store_expand.check({"datasets": [5, None, "x"]}, BUILTIN)[1]
        # picked twice is picked once
        request, _ = store_expand.check(form(NSBC, NSBC), BUILTIN)
        assert request["datasets"] == [NSBC]

    def test_a_request_is_described_in_a_line(self):
        request, _ = store_expand.check(form(COBE2, OCCCI, NSBC, start="2000", end="2001"), BUILTIN)
        text = store_expand.describe(request, BUILTIN)
        assert "Sea water temperature (COBE-SST 2)" in text and "and 1 more" in text
        assert "2000–2001" in text and "lon 3 to 4, lat 54 to 55" in text
        request, _ = store_expand.check(form(NITRATE, water_column="no"), BUILTIN)
        assert "the surface only" in store_expand.describe(request, BUILTIN)


# --------------------------------------------------------------------------
# what is fetched, by the store's own rules


class TestRun:
    def test_woa23_is_kept_cut_to_the_area_and_said_so(self, folder, server):
        done, news = run(request_of(NITRATE, levels="top"))
        assert done
        kept = entries(root_of(folder))
        assert len(kept) == 12 and len(server.opened) == 12
        assert {tuple(x["box"]) for x in kept} == {(3, 4, 54, 55)}
        assert {x["levels"] for x in kept} == {"top"}
        # told first what there is to do, then each file as it goes
        assert news[0]["kind"] == "expand"
        assert news[0]["items"] == [{"key": "nitrate/woa23", "label": "Nitrate concentration in sea water (World Ocean Atlas 2023)",
                                     "total": 12}]
        last = finals(news)["nitrate/woa23"]
        assert (last["status"], last["done"], last["total"], last["saved"], last["held"]) == ("done", 12, 12, 12, 0)
        assert [x["done"] for x in news if x.get("status") == "working"][-1] == 12

    def test_what_is_held_is_not_fetched_again(self, folder, server):
        run(request_of(NITRATE, levels="top"))
        server.opened.clear()
        done, news = run(request_of(NITRATE, levels="top"))
        assert done and server.opened == []
        last = finals(news)["nitrate/woa23"]
        assert (last["saved"], last["held"]) == (0, 12)

    def test_a_smaller_area_is_taken_from_what_is_held(self, folder, server):
        run(request_of(NITRATE, levels="top", lon=(0, 8), lat=(50, 58)))
        server.opened.clear()
        run(request_of(NITRATE, levels="top"))
        assert server.opened == []

    def test_a_wider_area_widens_the_pieces_to_cover_both(self, folder, server):
        root = root_of(folder)
        run(request_of(NITRATE, levels="top"))
        run(request_of(NITRATE, levels="top", lon=(5, 6), lat=(54, 55)))
        kept = entries(root)
        assert len(kept) == 12
        # as a matchup does it: the piece covers what was held and what is asked for now
        assert {tuple(x["box"]) for x in kept} == {(3, 6, 54, 55)}
        assert len(retired(root)) == 12

    def test_the_water_column_replaces_the_surface_but_not_the_other_way(self, folder, server):
        root = root_of(folder)
        run(request_of(NITRATE, levels="top"))
        run(request_of(NITRATE, levels="all"))
        assert {x["levels"] for x in entries(root)} == {"all"}
        server.opened.clear()
        done, news = run(request_of(NITRATE, levels="top"))
        assert done and server.opened == [] and {x["levels"] for x in entries(root)} == {"all"}
        assert finals(news)["nitrate/woa23"]["held"] == 12

    def test_cobe2_is_kept_a_year_at_a_time(self, folder, server):
        root = root_of(folder)
        done, news = run(request_of(COBE2, years=(2000, 2000)))
        assert done
        assert [x["year"] for x in entries(root)] == [2000]
        assert finals(news)["temperature/cobe2"]["total"] == 1
        server.opened.clear()
        server.downloaded.clear()
        # one more year only
        done, news = run(request_of(COBE2, years=(2000, 2001)))
        assert sorted(x["year"] for x in entries(root)) == [2000, 2001]
        last = finals(news)["temperature/cobe2"]
        assert (last["saved"], last["held"], last["total"]) == (1, 1, 2)
        # a year the file does not have all of is not kept, as a matchup does not keep it
        done, news = run(request_of(COBE2, years=(2002, 2002)))
        assert sorted(x["year"] for x in entries(root)) == [2000, 2001]

    def test_a_server_that_does_not_answer_is_reported_and_what_is_held_stays(self, folder, server):
        root = root_of(folder)
        run(request_of(COBE2, years=(2000, 2000)))
        server.down = True
        server.download_down = True
        done, news = run(request_of(COBE2, years=(2000, 2001)))
        assert not done
        last = finals(news)["temperature/cobe2"]
        assert last["status"] == "failed" and last["error"]
        assert [x["year"] for x in entries(root)] == [2000]

    def test_ices_is_fetched_once_for_every_variable_from_it(self, folder, ices_api):
        root = root_of(folder)
        done, news = run(request_of(ICES_TEMPERATURE, ICES_NITRATE, years=(2000, 2001)))
        assert done
        # temperature is from CTD and ICE, nitrate from ICE: ICE is asked of once
        asked = sorted(x[0] for x in ices_api.asked)
        assert asked == ["CTD", "ICE"]
        assert sorted((x["dataset"], x["year"]) for x in entries(root)) == [
            ("CTD", 2000), ("CTD", 2001), ("ICE", 2000), ("ICE", 2001)]
        # the box is whole degrees, as a matchup's
        assert {tuple(x["box"]) for x in entries(root)} == {(3, 4, 54, 55)}
        assert {"ices/CTD", "ices/ICE"} == set(finals(news))
        assert all(x["status"] == "done" and x["done"] == 2 for x in finals(news).values())

    def test_ices_years_held_are_not_asked_for_again(self, folder, ices_api):
        run(request_of(ICES_NITRATE, years=(2000, 2001)))
        ices_api.asked.clear()
        done, news = run(request_of(ICES_NITRATE, years=(2000, 2002)))
        assert done
        assert [x[:3] for x in ices_api.asked] == [("ICE", "2002", "2002")]
        last = finals(news)["ices/ICE"]
        # the year ICES has nothing for is kept as a year with no data: neither downloaded data nor held
        assert (last["done"], last["total"], last["saved"], last["held"]) == (3, 3, 0, 2)
        assert sorted((x["year"], x["rows"]) for x in entries(root_of(folder)))[-1] == (2002, 0)

    def test_ices_not_answering_is_reported(self, folder, ices_api):
        ices_api.down = True
        done, news = run(request_of(ICES_NITRATE, years=(2000, 2001)))
        assert not done and finals(news)["ices/ICE"]["status"] == "failed"
        # nothing was got, so nothing is kept
        assert not os.path.exists(os.path.join(root_of(folder), data_store.INDEX)) or entries(root_of(folder)) == []

    def test_the_store_is_used_whatever_the_matchups_use_it_for(self, folder, server, oceanvalrc):
        # in the fallback mode a matchup downloads each time; filling the store does not
        oceanvalrc.write_text(json.dumps({"settings": {"data_store": str(folder), "data_store_use": "fallback"}}))
        run(request_of(NITRATE, levels="top"))
        server.opened.clear()
        done, news = run(request_of(NITRATE, levels="top"))
        assert server.opened == [] and finals(news)["nitrate/woa23"]["held"] == 12

    def test_without_a_store_it_says_so(self, oceanvalrc):
        oceanvalrc.write_text("{}")
        with pytest.raises(ValueError, match="no data store"):
            run(request_of(NITRATE, levels="top"))

    def test_the_store_that_is_filled_is_found_by_the_page(self, folder, server):
        run(request_of(NITRATE, levels="top"))
        view = store_view.view(BUILTIN, {"data_store": str(folder)})
        row = next(x for x in view["rows"] if (x["name"], x["recipe"]) == ("nitrate", "woa23"))
        assert row["downloaded"] and row["water_column"] == "Surface only"
        assert row["year"] == "12 files"
        assert row["extent"].startswith("lon 3") and "lat 5" in row["extent"]


class TestWhatIsCalled:
    """The calls to the store for datasets there is no stand-in server for."""

    @pytest.fixture
    def calls(self, folder, monkeypatch):
        made = []

        def gridded(comparison, files, thredds, box, levels, years=None, label=None, store_only=False, on_piece=None):
            made.append(dict(comparison=comparison, files=files, thredds=thredds, box=box, levels=levels, years=years,
                             store_only=store_only))
            return {"saved": 0, "held": 1, "failed": 0}

        monkeypatch.setattr(data_store, "gridded", gridded)
        return made

    def test_nothing_is_opened_or_read_back(self, calls):
        run(request_of(NSBC))
        assert calls and all(x["store_only"] for x in calls)

    def test_woa23_temperature_is_a_call_for_each_decade_the_years_touch(self, calls):
        done, news = run(request_of(TEMPERATURE_WOA, years=(1990, 2010), levels="top"))
        assert done
        assert len(calls) == 3
        assert {x["levels"] for x in calls} == {"top"}
        urls = [x["files"] if isinstance(x["files"], str) else x["files"][0] for x in calls]
        assert len({u for u in urls}) == 3
        assert all(x["comparison"]["builtin"] == "woa23" for x in calls)

    def test_years_outside_every_decade_fail_that_dataset_only(self, calls):
        done, news = run(request_of(TEMPERATURE_WOA, NITRATE, years=(1800, 1850), levels="top"))
        assert not done
        last = finals(news)
        assert last["temperature/woa23"]["status"] == "failed" and "1955" in last["temperature/woa23"]["error"]
        assert last["nitrate/woa23"]["status"] == "done"

    def test_occci_files_are_those_of_the_years(self, calls):
        done, news = run(request_of(OCCCI, years=(2000, 2001)))
        assert done
        files = calls[0]["files"]
        assert files and all(("2000" in f or "2001" in f) for f in files)
        assert calls[0]["years"] is None

    def test_cobe2_is_given_its_years(self, calls):
        run(request_of(COBE2, years=(2000, 2002)))
        assert calls[0]["years"] == [2000, 2001, 2002]

    def test_glodap_is_asked_for_the_whole_globe(self, calls):
        done, news = run(request_of(GLODAP, years=None, lon=None, lat=None))
        assert done and tuple(calls[0]["box"]) == tuple(data_store.GLOBAL)

    def test_a_nsbc_is_asked_for_the_area_with_no_levels_to_choose(self, calls):
        run(request_of(NSBC))
        assert tuple(calls[0]["box"]) == (3, 4, 54, 55) and calls[0]["levels"] == "all"

    def test_one_dataset_failing_does_not_stop_the_next(self, folder, monkeypatch):
        made = []

        def gridded(comparison, files, thredds, box, levels, years=None, label=None, store_only=False, on_piece=None):
            made.append(comparison["builtin"])
            if comparison["builtin"] == "nsbc":
                raise ConnectionError("Connection timed out")
            return {"saved": 1, "held": 0, "failed": 0}

        monkeypatch.setattr(data_store, "gridded", gridded)
        done, news = run(request_of(NSBC, GLODAP, lon=(3, 4), lat=(54, 55)))
        assert not done and "nsbc" in made and "glodap" in made
        last = finals(news)
        assert last["nitrate/nsbc"]["status"] == "failed" and "ConnectionError" in last["nitrate/nsbc"]["error"]
        assert last["ph/glodap"]["status"] == "done"

    def test_files_that_could_not_be_downloaded_are_a_failure_not_a_success(self, folder, monkeypatch):
        monkeypatch.setattr(data_store, "gridded", lambda *a, **k: {"saved": 0, "held": 1, "failed": 2})
        done, news = run(request_of(NSBC))
        assert not done and "2 could not be downloaded" in finals(news)["nitrate/nsbc"]["error"]


class TestTheStoreKeywords:
    """store_only and on_piece change nothing for a matchup."""

    def test_without_them_the_dataset_is_returned(self, folder, server):
        from test_data_store import WOA_URLS, MODEL_BOX, woa_comparison

        data_store.start({"folder": str(folder), "root": root_of(folder), "use": "always"})
        told = []
        ds = data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top", on_piece=lambda *a: told.append(a))
        assert ds is not None and not isinstance(ds, dict)
        assert [s for _, s in told] == ["saved"] * 12
        counts = data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top", store_only=True)
        assert counts == {"saved": 0, "held": 12, "failed": 0}

    def test_a_callback_that_raises_does_not_stop_the_work(self, folder, server):
        from test_data_store import WOA_URLS, MODEL_BOX, woa_comparison

        def boom(*args):
            raise RuntimeError("no")

        data_store.start({"folder": str(folder), "root": root_of(folder), "use": "always"})
        counts = data_store.gridded(woa_comparison(), WOA_URLS, True, MODEL_BOX, "top", store_only=True, on_piece=boom)
        assert counts["saved"] == 12


# --------------------------------------------------------------------------
# the window: jobs in the background, one at a time

from test_app import _tools_page, post, wait_for  # noqa: E402
from oceanval.app import App  # noqa: E402


@pytest.fixture
def started(monkeypatch):
    """The processes the app would start, recorded and not started, and the ones it is asked to stop."""
    made, stopped = [], []

    def start(run):
        made.append(run)

    monkeypatch.setattr(app_module.Run, "start", start)
    monkeypatch.setattr(app_module.Run, "stop", lambda run: stopped.append(run))
    return made, stopped


@pytest.fixture
def window(tmp_path, folder):
    app = App(cwd=str(tmp_path))
    app.start()
    yield app
    app.close()


def asked(app, *datasets, **boxes):
    return post(app, "/api/store_expand", {"form": form(*datasets, **boxes)})


def statuses(app):
    return [(x["id"], x["status"]) for x in app.state()["store_expand"]["jobs"]]


def finish(app, job, code=0):
    job["run"].returncode = code
    app._store_expand_finished(job, job["run"])


class TestJobs:
    def test_the_routes_need_the_token(self, window):
        for route in ("/api/store_expand", "/api/store_expand_cancel", "/api/store_expand_clear"):
            assert post(window, route, {}, token="wrong")[0] == 403

    def test_a_form_that_is_wrong_starts_nothing(self, window, started):
        status, reply = asked(window, NSBC, lon_min="")
        assert status == 200 and not reply["ok"] and "lon_min" in reply["errors"]
        status, reply = post(window, "/api/store_expand", {"form": "nonsense"})
        assert not reply["ok"] and "datasets" in reply["errors"]
        assert started[0] == [] and statuses(window) == []

    def test_without_a_store_nothing_is_started(self, tmp_path, oceanvalrc, started):
        oceanvalrc.write_text("{}")
        app = App(cwd=str(tmp_path))
        app.start()
        try:
            status, reply = asked(app, NSBC)
            assert not reply["ok"] and "no data store" in reply["errors"]["general"].lower()
            assert started[0] == []
        finally:
            app.close()

    def test_a_job_is_a_process_of_its_own_and_the_next_waits(self, window, started):
        made, _ = started
        assert asked(window, NSBC)[1] == {"ok": True}
        assert asked(window, GLODAP)[1] == {"ok": True}
        # one at a time
        assert len(made) == 1 and made[0].args[0] == "expand"
        assert json.loads(made[0].args[1])["datasets"] == [NSBC]
        assert statuses(window) == [(1, "working"), (2, "queued")]
        first = window._store_jobs[0]
        assert "Nitrate concentration in sea water (North Sea Biogeochemical Climatology)" in first["summary"]
        # busy, so closing the browser does not kill it
        assert window._busy()
        finish(window, first)
        assert len(made) == 2 and statuses(window)[0][1] in ("done", "failed") and statuses(window)[1] == (2, "working")
        finish(window, window._store_jobs[1])
        assert not window._busy()

    def test_news_is_kept_item_by_item_and_the_end_is_what_was_done(self, window, started):
        asked(window, NSBC, GLODAP)
        job = window._store_jobs[0]
        window._store_expand_news(job, {"kind": "expand", "items": [
            {"key": "nitrate/nsbc", "label": "Nitrate (NSBC)", "total": 1},
            {"key": "ph/glodap", "label": "pH (GLODAP)", "total": 1}]})
        window._store_expand_news(job, {"kind": "expand", "item": "nitrate/nsbc", "status": "working", "total": 1,
                                        "done": 0, "saved": 0, "held": 0})
        items = window.state()["store_expand"]["jobs"][0]["items"]
        assert [x["status"] for x in items] == ["working", "todo"]
        window._store_expand_news(job, {"kind": "expand", "item": "nitrate/nsbc", "status": "done", "total": 1,
                                        "done": 1, "saved": 1, "held": 0})
        window._store_expand_news(job, {"kind": "expand", "item": "ph/glodap", "status": "failed", "total": 1,
                                        "done": 0, "saved": 0, "held": 0, "error": "ConnectionError: down"})
        # news of other kinds, or nonsense, is not taken
        window._store_expand_news(job, {"kind": "matchups"})
        window._store_expand_news(job, "nonsense")
        finish(window, job)
        done = window.state()["store_expand"]["jobs"][0]
        assert done["status"] == "failed"
        assert [(x["key"], x["status"], x["error"]) for x in done["items"]] == [
            ("nitrate/nsbc", "done", None), ("ph/glodap", "failed", "ConnectionError: down")]

    def test_every_item_done_is_a_job_done(self, window, started):
        asked(window, NSBC)
        job = window._store_jobs[0]
        window._store_expand_news(job, {"kind": "expand", "items": [{"key": "nitrate/nsbc", "label": "x", "total": 1}]})
        window._store_expand_news(job, {"kind": "expand", "item": "nitrate/nsbc", "status": "done", "total": 1,
                                        "done": 1, "saved": 1, "held": 0})
        finish(window, job)
        assert statuses(window) == [(1, "done")]

    def test_a_process_that_died_is_a_failed_job_with_its_last_words(self, window, started):
        asked(window, NSBC)
        job = window._store_jobs[0]
        job["tail"].write("Traceback (most recent call last):\nValueError: There is no data store: set a folder\n")
        finish(window, job, code=1)
        done = window.state()["store_expand"]["jobs"][0]
        assert done["status"] == "failed" and "ValueError" in done["error"]

    def test_stop_ends_a_working_job_and_takes_a_waiting_one_out_of_the_queue(self, window, started):
        made, stopped = started
        asked(window, NSBC)
        asked(window, GLODAP)
        assert post(window, "/api/store_expand_cancel", {"id": 2})[1] == {"ok": True}
        assert statuses(window) == [(1, "working"), (2, "stopped")]
        assert stopped == []
        post(window, "/api/store_expand_cancel", {"id": 1})
        assert statuses(window) == [(1, "stopped"), (2, "stopped")] and stopped == [made[0]]
        # the process ending afterwards changes nothing, and nothing waiting is started
        finish(window, window._store_jobs[0], code=-15)
        assert statuses(window) == [(1, "stopped"), (2, "stopped")] and len(made) == 1
        assert not window._busy()

    def test_news_of_a_stopped_job_is_ignored(self, window, started):
        asked(window, NSBC)
        job = window._store_jobs[0]
        post(window, "/api/store_expand_cancel", {"id": 1})
        window._store_expand_news(job, {"kind": "expand", "items": [{"key": "a", "label": "a", "total": 1}]})
        assert window.state()["store_expand"]["jobs"][0]["items"] == []

    def test_clear_forgets_what_has_ended_and_never_what_is_going(self, window, started):
        asked(window, NSBC)
        asked(window, GLODAP)
        finish(window, window._store_jobs[0])
        assert post(window, "/api/store_expand_clear", {"id": 2})[1] == {"ok": True}
        assert statuses(window) == [(1, "done")] or statuses(window)[0][0] == 1
        post(window, "/api/store_expand_clear", {})
        assert [s for _, s in statuses(window)] == ["working"]

    def test_restart_stops_every_job_and_forgets_them(self, window, started):
        made, stopped = started
        asked(window, NSBC)
        asked(window, GLODAP)
        window.reset()
        assert statuses(window) == [] and stopped == [made[0]]
        assert not window._busy()

    def test_quitting_stops_the_job(self, tmp_path, folder, started):
        made, stopped = started
        app = App(cwd=str(tmp_path))
        app.start()
        asked(app, NSBC)
        app.close()
        assert stopped == [made[0]]

    def test_the_child_runs_a_request_and_tells_the_window(self, folder, server, capfd):
        from oceanval import app_child

        request = request_of(NITRATE, levels="top")
        app_child.main(["expand", json.dumps(request)])
        out = capfd.readouterr().out
        # (the marker's first character is a line break to str.splitlines)
        lines = [json.loads(x.split("oceanval-progress ", 1)[1]) for x in out.splitlines()
                 if x.startswith("oceanval-progress ")]
        assert lines[0]["items"][0]["key"] == "nitrate/woa23"
        assert lines[-1]["status"] == "done" and lines[-1]["saved"] == 12
        assert len(entries(root_of(folder))) == 12

    def test_a_request_that_cannot_be_read_exits_with_an_error(self, folder):
        from oceanval import app_child

        with pytest.raises(BaseException):
            app_child.main(["expand", "{ not json"])


# --------------------------------------------------------------------------
# the page


def _open_page(browser, app, width=1400):
    url = _tools_page(app)
    page = browser.new_page(viewport={"width": width, "height": 1100})
    page.goto(url)
    page.wait_for_selector("#view-help:not([hidden])")
    page.click("#help-datastore")
    page.wait_for_selector("#store-table-out tbody tr")
    return page


def _pick(page, text):
    page.locator("#sx-list tr", has_text=text).first.click()


class TestPage:
    def test_the_top_button_is_a_primary_button_with_its_name(self, browser, window, started):
        page = _open_page(browser, window)
        assert page.text_content("#store-expand-open").strip() == "Expand multiple variables"
        assert "btn--primary" in page.get_attribute("#store-expand-open", "class")
        assert page.is_enabled("#store-expand-open") and page.is_hidden("#store-expand-spin")
        # a button on every row, and the same look
        rows = page.locator("#store-table-out tbody tr").count()
        buttons = page.locator("#store-table-out .store__act .btn")
        assert buttons.count() == rows and buttons.first.text_content().strip() == "Expand"
        assert "btn--primary" in buttons.first.get_attribute("class")

    def test_the_form_asks_only_what_the_datasets_chosen_need(self, browser, window, started):
        page = _open_page(browser, window)
        page.click("#store-expand-open")
        page.wait_for_selector("#store-expand:not([hidden])")
        assert page.is_disabled("#sx-start-job")
        assert page.locator("#sx-list tr.is-picked").count() == 0
        for row in ("#sx-row-years", "#sx-row-area", "#sx-row-water"):
            assert page.is_hidden(row)
        _pick(page, "COBE-SST 2")
        assert page.is_visible("#sx-row-years") and page.is_visible("#sx-row-area") and page.is_hidden("#sx-row-water")
        assert page.is_enabled("#sx-start-job")
        _pick(page, "World Ocean Atlas 2023")
        assert page.is_visible("#sx-row-water")
        # and the one that is picked can be taken out again
        page.locator("#sx-list tr.is-picked").first.click()
        assert page.locator("#sx-list tr.is-picked").count() == 1
        # nothing is a tick box
        assert page.locator("#store-expand input[type=checkbox]").count() == 0

    def test_the_search_narrows_the_list(self, browser, window, started):
        page = _open_page(browser, window)
        page.click("#store-expand-open")
        everything = page.locator("#sx-list tr").count()
        page.fill("#sx-search", "glodap")
        assert 0 < page.locator("#sx-list tr").count() < everything
        page.fill("#sx-search", "zzzz")
        assert "No dataset matches" in page.inner_text("#sx-list")

    def test_a_form_with_boxes_missing_says_which_and_stays_open(self, browser, window, started):
        made, _ = started
        page = _open_page(browser, window)
        page.click("#store-expand-open")
        _pick(page, "COBE-SST 2")
        _pick(page, "World Ocean Atlas 2023")
        page.click("#sx-start-job")
        page.wait_for_function("document.getElementById('sx-msg-years').textContent !== ''")
        assert page.text_content("#sx-msg-area") and page.text_content("#sx-msg-water")
        assert not page.is_hidden("#store-expand") and made == []
        # editing a box takes its message away
        page.fill("#sx-start", "2000")
        assert page.text_content("#sx-msg-years") == ""

    def test_asking_closes_the_form_and_a_circle_turns_while_it_goes(self, browser, window, started):
        made, _ = started
        page = _open_page(browser, window)
        page.click("#store-expand-open")
        _pick(page, "COBE-SST 2")
        page.fill("#sx-start", "2000")
        page.fill("#sx-end", "2001")
        for name, value in (("lon_min", "3"), ("lon_max", "4"), ("lat_min", "54"), ("lat_max", "55")):
            page.fill(f"#sx-{name}", value)
        page.click("#sx-start-job")
        page.wait_for_selector("#store-expand", state="hidden")
        page.wait_for_selector("#store-jobs .store__job")
        assert page.is_visible("#store-expand-spin")
        assert json.loads(made[0].args[1]) == request_of(COBE2, years=(2000, 2001), lon=(3, 4), lat=(54, 55))
        # the user can go on: the form is empty and another can be asked for at once
        page.click("#store-expand-open")
        assert page.locator("#sx-list tr.is-picked").count() == 0 and page.input_value("#sx-start") == ""
        _pick(page, "GLODAP")
        page.click("#sx-start-job")
        page.wait_for_selector("#store-expand", state="hidden")
        page.wait_for_function("document.querySelectorAll('#store-jobs .store__job').length === 2")
        assert [x.get_attribute("data-status") for x in page.locator("#store-jobs .store__job").all()] == ["working", "queued"]
        assert len(made) == 1

    def test_the_page_follows_the_job_and_refreshes_the_table_when_it_ends(self, browser, window, started):
        page = _open_page(browser, window)
        asked(window, NSBC)
        page.wait_for_selector("#store-jobs .store__job")
        job = window._store_jobs[0]
        window._store_expand_news(job, {"kind": "expand", "items": [{"key": "nitrate/nsbc", "label": "Nitrate (NSBC)", "total": 4}]})
        window._store_expand_news(job, {"kind": "expand", "item": "nitrate/nsbc", "status": "working", "total": 4,
                                        "done": 1, "saved": 1, "held": 0})
        page.wait_for_function("document.querySelector('#store-jobs').textContent.includes('1 of 4')")
        assert page.is_visible("#store-expand-spin")
        # what it adds shows in the first table when it is done
        TestStored.fill(window)
        window._store_expand_news(job, {"kind": "expand", "item": "nitrate/nsbc", "status": "done", "total": 4,
                                        "done": 4, "saved": 4, "held": 0})
        finish(window, job)
        page.wait_for_function("document.querySelector('#store-jobs').textContent.includes('Added')")
        page.wait_for_selector("#store-expand-spin", state="hidden")
        page.wait_for_function("document.querySelector('#store-in-title').textContent.includes('(1)')")
        assert "lon" in page.inner_text("#store-table-in")
        # Clear takes the line away
        page.click("#store-jobs .store__job .btn")
        page.wait_for_selector("#store-jobs-wrap", state="hidden")

    def test_stop_in_the_page_stops_the_job(self, browser, window, started):
        made, stopped = started
        page = _open_page(browser, window)
        asked(window, NSBC)
        page.wait_for_selector("#store-jobs .store__job")
        assert page.text_content("#store-jobs .store__job .btn").strip() == "Stop"
        page.click("#store-jobs .store__job .btn")
        page.wait_for_selector('#store-jobs .store__job[data-status="stopped"]')
        assert stopped == [made[0]]
        page.wait_for_selector("#store-expand-spin", state="hidden")

    def test_a_row_expands_that_dataset_alone(self, browser, window, started):
        made, _ = started
        page = _open_page(browser, window)
        row = page.locator("#store-table-out tbody tr", has_text="North Sea Biogeochemical").filter(has_text="Nitrate")
        row.locator(".btn").click()
        page.wait_for_selector("#store-expand:not([hidden])")
        assert "Nitrate concentration in sea water (North Sea Biogeochemical Climatology)" in page.text_content("#store-expand-title")
        # no list to pick from, and only the boxes this dataset makes a difference to
        assert page.is_hidden("#sx-block-datasets") and page.is_visible("#sx-row-area")
        assert page.is_hidden("#sx-row-years") and page.is_hidden("#sx-row-water")
        assert "Nothing of this dataset is in the data store yet" in page.inner_text("#sx-held")
        assert page.is_enabled("#sx-start-job")
        for name, value in (("lon_min", "3"), ("lon_max", "4"), ("lat_min", "54"), ("lat_max", "55")):
            page.fill(f"#sx-{name}", value)
        page.click("#sx-start-job")
        page.wait_for_selector("#store-expand", state="hidden")
        page.wait_for_selector("#store-jobs .store__job")
        assert json.loads(made[0].args[1])["datasets"] == [NSBC]

    def test_a_dataset_of_woa23_asks_about_the_water_column_and_years_for_decades(self, browser, window, started):
        page = _open_page(browser, window)
        page.locator("#store-table-out tbody tr", has_text="World Ocean Atlas").filter(has_text="Sea water temperature").locator(".btn").click()
        page.wait_for_selector("#store-expand:not([hidden])")
        assert page.is_visible("#sx-row-years") and page.is_visible("#sx-row-water")
        assert "decades" in page.inner_text("#sx-years-note")
        # the other WOA23 ones are one climatology each
        page.click("#sx-close")
        page.locator("#store-table-out tbody tr", has_text="World Ocean Atlas").filter(has_text="Nitrate").locator(".btn").click()
        assert page.is_hidden("#sx-row-years") and page.is_visible("#sx-row-water")

    def test_a_row_of_the_store_shows_what_is_held_and_escape_closes_the_form(self, browser, window, started):
        TestStored.fill(window)
        page = _open_page(browser, window)
        page.wait_for_selector("#store-table-in tbody tr")
        page.locator("#store-table-in tbody tr .btn").first.click()
        page.wait_for_selector("#store-expand:not([hidden])")
        assert "In the data store now" in page.inner_text("#sx-held")
        page.keyboard.press("Escape")
        page.wait_for_selector("#store-expand", state="hidden")
        # the page behind is usable again
        assert page.evaluate("document.getElementById('store-expand-open').closest('[inert]') === null")

    def test_without_a_store_nothing_can_be_expanded(self, browser, tmp_path, oceanvalrc, started):
        oceanvalrc.write_text("{}")
        app = App(cwd=str(tmp_path))
        try:
            page = _open_page(browser, app)
            assert page.is_disabled("#store-expand-open")
            assert page.locator("#store-table-out .store__act .btn:not([disabled])").count() == 0
        finally:
            app.close()


class TestStored:
    @staticmethod
    def fill(app):
        """NSBC nitrate in the store the app was given (a real, small file), as the job would leave it."""
        import numpy as np
        import xarray as xr

        folder = app.settings["data_store"]
        root = data_store.prepare(folder)
        lon, lat = np.arange(3.0, 5.0), np.arange(54.0, 56.0)

        def write(path):
            xr.Dataset({"no3": (("lat", "lon"), np.zeros((2, 2)))}, coords={"lon": lon, "lat": lat}).to_netcdf(path)

        variable = store_view._recipe("nitrate", "nsbc")[0]
        assert data_store._save(
            root, dict(kind="gridded", dataset="nsbc", url="u", variable=variable, year=None, levels="all", box=[3, 4, 54, 55]),
            write, ".nc", lambda old: False)
        return root
