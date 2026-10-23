"""The interim validation report matchup(live_validation=...) builds as it goes."""

import json
import os
import time

import nbformat
import pytest

import oceanval
from oceanval import live, prompts
from oceanval.live import InterimReport, LiveValidation, check_options

needs_to_transect = pytest.mark.skipif(
    not oceanval.transects.available(), reason="needs nctoolkit 1.3.6 or later"
)


class TestOptions:
    def test_none_and_false_are_no_interim_report(self):
        assert check_options(None, "/run") is None
        assert check_options(False, "/run") is None

    def test_true_is_validates_defaults_in_matchups_out_dir(self):
        assert check_options(True, "/run") == {
            "lon_lim": None,
            "lat_lim": None,
            "concise": True,
            "fixed_scale": False,
            "test": False,
            "out_dir": "/run",
            "subregions": None,
            "region_file": None,
            "n_regions": None,
            "transect": None,
            "depth_bins": oceanval.depths.DEFAULT_DEPTH_BINS,
            "variables": None,
        }

    def test_validates_report_options(self, tmp_path):
        options = check_options(
            {
                "lon_lim": [-20, 10],
                "lat_lim": [40, 65],
                "subregions": "nwes",
                "fixed_scale": True,
                "concise": False,
                "out_dir": str(tmp_path / "report"),
            },
            "/run",
        )

        assert options["lon_lim"] == [-20, 10]
        assert options["lat_lim"] == [40, 65]
        assert options["subregions"] == "nwes"
        assert (options["fixed_scale"], options["concise"]) == (True, False)
        assert options["out_dir"] == str(tmp_path / "report")

    @needs_to_transect
    def test_a_transect_is_checked_and_kept(self):
        options = check_options(
            {"transect": {"start": (-30, 0), "end": [-30, 65.0]}}, "/run"
        )

        # as validate has it: lists of floats
        assert options["transect"] == {"start": [-30.0, 0.0], "end": [-30.0, 65.0]}
        assert all(isinstance(x, float) for x in options["transect"]["start"])

    def test_depth_bins_are_checked_and_kept(self):
        options = check_options({"depth_bins": [[20, None], [0, 20]]}, "/run")

        assert options["depth_bins"] == [(0, 20), (20, None)]
        with pytest.raises(ValueError, match="overlap"):
            check_options({"depth_bins": [[0, 20], [10, 30]]}, "/run")

    def test_variables_are_checked_and_kept(self):
        options = check_options({"variables": ["temperature", "nitrate / woa23"]}, "/run")

        assert options["variables"] == ["temperature", "nitrate/woa23"]
        with pytest.raises(ValueError, match="variables"):
            check_options({"variables": []}, "/run")

    @pytest.mark.parametrize("name", ["pdf", "word", "zip"])
    def test_the_full_reports_other_forms_are_for_validate(self, name):
        with pytest.raises(ValueError, match="the interim report is HTML only"):
            check_options({name: True}, "/run")

    def test_the_matchups_are_matchups_own(self):
        with pytest.raises(ValueError, match="does not take data_dir"):
            check_options({"data_dir": "/elsewhere"}, "/run")

    def test_what_validate_does_not_take(self):
        with pytest.raises(ValueError, match="does not take colour. It takes out_dir"):
            check_options({"colour": "red"}, "/run")
        with pytest.raises(TypeError, match="live_validation must be True, or a dict"):
            check_options("yes", "/run")

    # the others are validate's own checks, tested in test_validate.py
    @pytest.mark.parametrize(
        "options, error",
        [
            ({"fixed_scale": 1}, "fixed_scale must be a boolean"),
            ({"test": "yes"}, "test must be a boolean"),
        ],
    )
    def test_checked_as_validate_checks_them(self, options, error):
        with pytest.raises(ValueError, match=error):
            check_options(options, "/run")

    def test_matchup_checks_them_before_matching_anything(self, tmp_path):
        with pytest.raises(ValueError, match="the interim report is HTML only"):
            oceanval.matchup(
                sim_dir="data/example",
                start=2000,
                end=2001,
                ask=False,
                out_dir=str(tmp_path),
                live_validation={"pdf": True},
            )
        assert not os.path.exists(tmp_path / "oceanval_matchups")


class TestAnswers:
    def test_an_answer_with_settings_is_the_answer(self):
        answer = prompts.Answer("Y", {"live_validation": {"concise": False}})

        assert answer == "Y" and answer.lower() == "y"
        assert answer.settings == {"live_validation": {"concise": False}}
        assert prompts.Answer("n").settings == {}


def _builder(tmp_path):
    """matchup's side of an interim report in tmp_path, with one matchup to make."""
    return LiveValidation(
        check_options(True, str(tmp_path)),
        str(tmp_path),
        [("gridded", "temperature", "foo", None)],
        {"temperature": "Temperature"},
    )


class TestMatchupsSide:
    def test_a_builder_that_dies_does_not_stop_the_matchups(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(live, "_BOOT", "import sys\nsys.exit(3)\n")
        builder = _builder(tmp_path)
        builder.start()
        assert live.current is builder
        builder.process.wait()

        builder.matched("gridded", "temperature", "foo")
        builder.finish()

        assert live.current is None
        assert builder.process is None
        out = capsys.readouterr().out
        assert "An interim validation report is built as the matchups are made" in out
        assert "interim validation report stopped" in out or "could not be finished" in out

    def test_an_unfinished_builder_is_stopped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(live, "_BOOT", "import time\ntime.sleep(120)\n")
        builder = _builder(tmp_path)
        builder.start()
        process = builder.process
        started = time.time()

        live.abandon()

        assert process.wait(timeout=30) is not None
        assert time.time() - started < 30
        assert live.current is None

    def test_matchup_stops_it_however_it_ends(self, monkeypatch):
        stopped = []

        class Builder:
            def stop(self):
                stopped.append(True)

        monkeypatch.setattr(live, "current", Builder())
        with pytest.raises(ValueError, match="Please provide a sim_dir"):
            oceanval.matchup()

        assert stopped == [True]
        assert live.current is None

    def test_only_the_matchups_variables_picks_are_added(self, tmp_path):
        """Those it leaves out are still matched up, but have no page."""
        builder = LiveValidation(
            check_options({"variables": ["temperature/foo"]}, str(tmp_path)),
            str(tmp_path),
            [
                ("gridded", "temperature", "foo", None),
                ("gridded", "temperature", "bar", None),
                ("point", "nitrate", "foo", "all"),
            ],
            {"temperature": "Temperature"},
        )
        assert builder.expected == [
            {"kind": "gridded", "variable": "temperature", "source": "foo", "layer": None}
        ]
        sent = []
        builder._send = sent.append
        builder.matched("gridded", "temperature", "bar")
        builder.matched("point", "nitrate", "foo", "all")
        builder.matched("gridded", "temperature", "foo")
        assert sent == [{"kind": "gridded", "variable": "temperature", "source": "foo", "layer": None}]

    def test_an_earlier_report_is_cleared(self, tmp_path, monkeypatch):
        monkeypatch.setattr(live, "_BOOT", "import sys\nsys.exit(0)\n")
        old = tmp_path / live.FOLDER / "oceanval_report" / "old.html"
        old.parent.mkdir(parents=True)
        old.write_text("old")
        builder = _builder(tmp_path)
        builder.start()
        builder.finish()

        assert not old.exists()
        assert (tmp_path / live.FOLDER / "build.log").exists()


def _raw_page(path, title):
    path.write_text(
        f'<html><head><title>{title}</title></head><body class="jp-Notebook">'
        f"<main><h1>{title}</h1></main></body></html>"
    )


@pytest.fixture
def report(tmp_path):
    """An interim report with its pages as nbconvert writes them, and
    nothing run."""
    folder = tmp_path / live.FOLDER
    report = InterimReport(
        {
            "folder": str(folder),
            "data_dir": str(tmp_path),
            "options": check_options(True, str(tmp_path)),
            "expected": [
                {"kind": "gridded", "variable": "temperature", "source": "woa23", "layer": None},
                {"kind": "point", "variable": "oxygen", "source": "ices", "layer": "all"},
                {"kind": "gridded", "variable": "nitrate", "source": "woa23", "layer": None},
            ],
            "short_titles": {"temperature": "Temperature", "oxygen": "Oxygen"},
            "parent": os.getpid(),
            "console": None,
        }
    )
    for directory in (report.notebooks_dir, report.raw_dir, os.path.join(report.html_dir, "notebooks")):
        os.makedirs(directory)
    titles = {
        "methods": "Validation metrics summary",
        "summary": "Full domain summary statistics of model performance",
        "woa23_temperature": "Sea surface Temperature validation using gridded observations from WOA23",
        "ices_all_oxygen": "Validation of Oxygen using point observations from ices",
    }
    for stem, title in titles.items():
        nbformat.write(
            nbformat.v4.new_notebook(cells=[nbformat.v4.new_markdown_cell(f"# {title}")]),
            os.path.join(report.notebooks_dir, f"{stem}.ipynb"),
        )
        _raw_page(tmp_path / live.FOLDER / "oceanval_report" / "_build" / "raw" / f"{stem}.html", title)
    return report


class TestBinding:
    def test_the_pages_are_bound_together_in_validates_order(self, report):
        report.pages = {
            "woa23_temperature": ("temperature", "Temperature (WOA23)"),
            "ices_all_oxygen": ("oxygen", "Oxygen (ICES point data)"),
        }
        report.bind()

        with open(os.path.join(report.book_dir, "_toc.yml")) as file:
            toc = file.read()
        assert toc.index("caption: Summaries") < toc.index("notebooks/methods.ipynb")
        assert toc.index("notebooks/summary.ipynb") < toc.index("caption: Oxygen")
        # a section for each variable, in the order of their names
        assert toc.index("caption: Oxygen") < toc.index("caption: Temperature")
        assert report.landing == os.path.join(report.html_dir, "notebooks", "summary.html")

        page = open(os.path.join(report.html_dir, "notebooks", "woa23_temperature.html")).read()
        for stem in ("methods", "summary", "woa23_temperature", "ices_all_oxygen"):
            assert f'href="{stem}.html"' in page
        assert "oceanval-sidebar" in page
        assert "<strong>Interim validation report</strong>, built from 2 of 3 matchups so far" in page
        assert "reload the page to see the latest" in page
        # the page nbconvert wrote is left as it was, to be bound again
        raw = open(os.path.join(report.raw_dir, "woa23_temperature.html")).read()
        assert "oceanval-sidebar" not in raw

    def test_bound_again_as_pages_are_added(self, report):
        report.pages = {"woa23_temperature": ("temperature", "Temperature (WOA23)")}
        report.bind()
        page = os.path.join(report.html_dir, "notebooks", "woa23_temperature.html")
        assert 'href="ices_all_oxygen.html"' not in open(page).read()

        report.pages["ices_all_oxygen"] = ("oxygen", "Oxygen (ICES point data)")
        report.bind()

        assert 'href="ices_all_oxygen.html"' in open(page).read()
        assert open(page).read().count("oceanval-sidebar\"") == 1

    def test_complete(self, report):
        report.pages = {
            "woa23_temperature": ("temperature", "Temperature (WOA23)"),
            "ices_all_oxygen": ("oxygen", "Oxygen (ICES point data)"),
        }
        report.complete()

        page = open(os.path.join(report.html_dir, "notebooks", "summary.html")).read()
        assert (
            "complete, with the 2 of the 3 matchups that could be made. The full "
            "validation report is built from the same matchups next."
        ) in page
        status = json.load(open(report.status_path))
        assert status["state"] == "complete"
        assert (status["pages"], status["expected"]) == (2, 3)
        assert status["built"] == ["Temperature (WOA23)", "Oxygen (ICES point data)"]
        assert status["landing"] == report.landing

    def test_each_matchups_page_is_in_the_status(self, report, monkeypatch):
        """For the oceanval window's grid of the matchups: the page being
        made, the pages that can be opened, and those that could not be made."""
        temperature = live.matchup_key("gridded", "temperature", "woa23")
        oxygen = live.matchup_key("point", "oxygen", "ices", "all")

        def make_page(matchup):
            if matchup["variable"] == "oxygen":
                raise RuntimeError("no page")
            return os.path.join(report.notebooks_dir, "woa23_temperature.ipynb"), "woa23_temperature"

        keys = []
        write_status = report.write_status

        def written(state, building=None, building_key=None):
            keys.append(building_key)
            write_status(state, building, building_key)

        monkeypatch.setattr(report, "make_page", make_page)
        monkeypatch.setattr(report, "run_notebook", lambda path: None)
        monkeypatch.setattr(report, "write_status", written)
        report.summary = os.path.join(report.notebooks_dir, "summary.ipynb")
        report.add([
            {"kind": "gridded", "variable": "temperature", "source": "woa23", "layer": None},
            {"kind": "point", "variable": "oxygen", "source": "ices", "layer": "all"},
        ])

        # each is said to be made as it is, and none once they are
        assert keys == [temperature, oxygen, None]
        status = json.load(open(report.status_path))
        assert status["included"] == [temperature, oxygen, live.matchup_key("gridded", "nitrate", "woa23")]
        # only once it is bound, so that it can be opened
        assert status["added"] == {
            temperature: os.path.join(report.html_dir, "notebooks", "woa23_temperature.html")
        }
        assert os.path.exists(status["added"][temperature])
        assert status["failed"] == [oxygen]
        assert status["building_key"] is None

    def test_labels(self, report):
        assert report.label({"kind": "gridded", "variable": "nitrate", "source": "woa23"}) == "Nitrate (WOA23)"
        assert (
            report.label({"kind": "point", "variable": "oxygen", "source": "ices", "layer": "all"})
            == "Oxygen (ICES point data)"
        )


@pytest.fixture
def example_matchups():
    """Observations of the example simulation's temperature, gridded and
    point, as tests/test_gridded_matchups.py registers them."""
    oceanval.reset()
    oceanval.add_gridded_comparison(
        name="temperature",
        obs_path="data/evaldata/gridded/nws/temperature",
        source="foo",
        model_variable="votemper",
        obs_variable="votemper",
        climatology=True,
        start=2000,
        end=2010,
        obs_adder=273.15,
    )
    oceanval.add_point_comparison(
        name="temperature",
        obs_path="data/evaldata/point/nws/all/temperature",
        source="bar",
        model_variable="votemper",
        vertical=True,
        obs_adder=273.15,
    )
    yield
    oceanval.reset()


def test_one_variable_and_source_has_no_interim_report(tmp_path, capfd):
    # it would be the full report built twice
    oceanval.reset()
    oceanval.add_gridded_comparison(
        name="temperature",
        obs_path="data/evaldata/gridded/nws/temperature",
        source="foo",
        model_variable="votemper",
        obs_variable="votemper",
        climatology=True,
        start=2000,
        end=2010,
        obs_adder=273.15,
    )
    try:
        oceanval.matchup(
            sim_dir="data/example",
            start=2000,
            end=2001,
            ask=False,
            cores=1,
            thickness="data/example/e3t.nc",
            out_dir=str(tmp_path),
            live_validation={"test": True},
        )
    finally:
        oceanval.reset()

    assert not (tmp_path / live.FOLDER).exists()
    assert live.current is None
    assert (tmp_path / "oceanval_matchups").exists()
    out = capfd.readouterr().out
    assert "Only one variable and source is matched up, so no interim report is built." in out


def test_an_interim_report_is_built_as_the_matchups_are_made(
    tmp_path, example_matchups, capfd
):
    oceanval.matchup(
        sim_dir="data/example",
        start=2000,
        end=2001,
        ask=False,
        cores=1,
        thickness="data/example/e3t.nc",
        out_dir=str(tmp_path),
        live_validation={"test": True},
    )

    folder = tmp_path / live.FOLDER
    status = json.load(open(folder / "status.json"))
    assert status["state"] == "complete"
    assert (status["pages"], status["expected"]) == (2, 2)
    pages = folder / "oceanval_report" / "_build" / "html" / "notebooks"
    assert status["landing"] == str(pages / "summary.html")
    for stem in ("methods", "summary", "foo_temperature", "bar_all_temperature"):
        page = (pages / f"{stem}.html").read_text()
        assert "<strong>Interim validation report</strong>, complete, with all 2 matchups" in page
        assert 'href="bar_all_temperature.html"' in page and 'href="foo_temperature.html"' in page
    # the notebooks ran to the end, with the report's own results
    for stem in ("foo_temperature", "bar_all_temperature"):
        assert "This is getting to the end!" in (pages / f"{stem}.html").read_text()
    assert (folder / "oceanval_results" / "annual_mean" / "annualmean_temperature_foo.nc").exists()
    # matchup builds only the interim report
    assert not (tmp_path / "oceanval_report").exists()
    out = capfd.readouterr().out
    assert "Interim validation report: added" in out
    assert "The interim validation report is complete, with 2 of 2 matchups" in out

    # the full report is still validate's, beside it
    oceanval.validate(test=True, data_dir=str(tmp_path), out_dir=str(tmp_path))
    assert (tmp_path / "oceanval_report.html").exists()
    assert (pages / "summary.html").exists()
