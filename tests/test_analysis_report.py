"""The analysis report built one variable at a time, as analyze() and
compare_simulations() work them out (see oceanval.analysis_report): an interim
report until the last is in, which then is the report."""

import glob
import json
import os
import time

import nbformat
import pytest

import oceanval
from oceanval import analysis, analysis_report, sim_compare
from simulations import write_analysis_simulation

TWO = {"Chlorophyll": "P1_Chl+P2_Chl", "Temperature": "thetao"}
CLIMATOLOGY = {
    "Chlorophyll": {"start": 2013, "end": 2014, "surface": True},
    "Temperature": {"start": 2013, "end": 2014, "surface": True},
}


@pytest.fixture(scope="module")
def daily(tmp_path_factory):
    root = tmp_path_factory.mktemp("daily") / "sim"
    write_analysis_simulation(str(root))
    return str(root)


def pages_of(folder):
    return os.path.join(folder, analysis.REPORT, "_build", "html", "notebooks")


def status_of(folder):
    with open(os.path.join(folder, analysis_report.STATUS)) as file:
        return json.load(file)


def read(path):
    with open(path) as file:
        return file.read()


BANNER = 'class="oceanval-interim-banner"'


class TestTheBook:
    """InterimBook itself, with nbconvert's part done by a stand-in that
    writes each page as a heading."""

    @pytest.fixture
    def book(self, tmp_path, monkeypatch):
        ran = []

        def run(book, path):
            stem = os.path.basename(path)[: -len(".ipynb")]
            ran.append(stem)
            if stem == "002_broken":
                raise RuntimeError("nbconvert failed")
            with open(os.path.join(book.raw_dir, f"{stem}.html"), "w") as file:
                file.write(f"<html><head></head><body><h1>{stem}</h1></body></html>")

        def write_page(folder, number, entry, templates, finish=True):
            path = os.path.join(folder, analysis.REPORT, "notebooks", f"{analysis.page_name(number, entry)}.ipynb")
            notebook = nbformat.v4.new_notebook(cells=[nbformat.v4.new_markdown_cell(f"# {entry['name']}")])
            nbformat.write(notebook, path)
            return path

        monkeypatch.setattr(analysis_report.InterimBook, "_run", run)
        monkeypatch.setattr(analysis, "write_page", write_page)
        folder = str(tmp_path / analysis.FOLDER)
        os.makedirs(folder)

        def make(expected=2):
            book = analysis_report.InterimBook(folder, expected, analysis.Templates())
            book.ran = ran
            book.start()
            return book

        return make

    @staticmethod
    def wait_for(book, pages):
        for _ in range(200):
            status = status_of(book.folder)
            if status["pages"] >= pages and status["state"] == "building" and status["building"] is None:
                return status
            time.sleep(0.05)
        raise AssertionError("the page was not built")

    def test_a_variable_is_in_the_report_as_soon_as_it_is_added(self, book):
        book = book()
        assert status_of(book.folder)["state"] == "starting"
        book.add(1, {"key": "chlorophyll", "name": "Chlorophyll"})
        status = self.wait_for(book, 1)
        assert status["expected"] == 2 and status["built"] == ["Chlorophyll"]
        assert status["landing"] == os.path.join(pages_of(book.folder), "000_overview.html")
        overview = read(status["landing"])
        assert BANNER in overview and "built from 1 of 2 variables so far" in overview
        assert "001_chlorophyll.html" in overview
        # the overview is run again with each variable
        assert book.ran == ["001_chlorophyll", "000_overview"]
        book.finish()

    def test_once_finished_it_is_the_report(self, book):
        book = book()
        book.add(1, {"key": "chlorophyll", "name": "Chlorophyll"})
        book.add(2, {"key": "temperature", "name": "Temperature"})
        landing = book.finish()
        assert landing == analysis.landing_page(book.folder)
        assert sorted(os.listdir(pages_of(book.folder))) == [
            "000_overview.html", "001_chlorophyll.html", "002_temperature.html", "oceanval_wordmark.svg",
        ]
        for page in glob.glob(os.path.join(pages_of(book.folder), "*.html")):
            assert BANNER not in read(page)
            assert "002_temperature.html" in read(page)
        link = os.path.join(book.folder, analysis.REPORT_LINK)
        assert os.path.realpath(link) == os.path.realpath(landing)
        assert not os.path.exists(book.raw_dir)
        assert status_of(book.folder)["state"] == "complete"
        # the overview was last run after the last variable, and not again
        assert book.ran[-1] == "000_overview" and book.ran.count("002_temperature") == 1
        toc = read(os.path.join(book.book_dir, "_toc.yml"))
        assert toc.index("001_chlorophyll") < toc.index("002_temperature")

    def test_the_pages_are_in_the_order_of_the_variables(self, book):
        book = book()
        book.add(2, {"key": "temperature", "name": "Temperature"})
        book.add(1, {"key": "chlorophyll", "name": "Chlorophyll"})
        book.finish()
        toc = read(os.path.join(book.book_dir, "_toc.yml"))
        assert toc.index("001_chlorophyll") < toc.index("002_temperature")

    def test_a_page_that_cannot_be_built_is_left_out(self, book, capsys):
        book = book(expected=3)
        book.add(1, {"key": "chlorophyll", "name": "Chlorophyll"})
        book.add(2, {"key": "broken", "name": "Broken"})
        book.add(3, {"key": "temperature", "name": "Temperature"})
        book.finish()
        assert "the page of Broken could not be built" in capsys.readouterr().out
        assert sorted(os.path.basename(x) for x in glob.glob(os.path.join(pages_of(book.folder), "*.html"))) == [
            "000_overview.html", "001_chlorophyll.html", "003_temperature.html",
        ]
        assert status_of(book.folder)["pages"] == 2

    def test_an_analysis_that_stops_keeps_the_pages_of_what_was_worked_out(self, book):
        book = book(expected=3)
        book.add(1, {"key": "chlorophyll", "name": "Chlorophyll"})
        book.stop()
        overview = read(os.path.join(pages_of(book.folder), "000_overview.html"))
        assert "with the 1 of the 3 variables worked out before the analysis stopped" in overview
        assert status_of(book.folder)["state"] == "stopped"
        assert not os.path.lexists(os.path.join(book.folder, analysis.REPORT_LINK))

    def test_stopped_at_once_nothing_more_is_built(self, book):
        book = book()
        book.stopping.set()
        book.add(1, {"key": "chlorophyll", "name": "Chlorophyll"})
        book.stop(now=True)
        assert book.ran == []
        assert status_of(book.folder)["state"] == "stopped"


def test_the_report_of_analyze(daily, tmp_path, monkeypatch):
    """Each variable's page is built once it is worked out, with a note that
    the report is interim until the last is in."""
    bound = []
    original = analysis_report.InterimBook._bind

    def bind(book, complete=False, stopped=False):
        original(book, complete, stopped)
        overview = os.path.join(pages_of(book.folder), "000_overview.html")
        bound.append((sorted(page for _, (page, _) in book.pages.items()), BANNER in read(overview)))

    monkeypatch.setattr(analysis_report.InterimBook, "_bind", bind)
    out = str(tmp_path / "out")
    landing = oceanval.analyze(
        daily, TWO, climatology=CLIMATOLOGY, n_dirs_down=1, out_dir=out, cores=1, view=False,
    )
    folder = os.path.join(out, analysis.FOLDER)
    assert landing == analysis.landing_page(folder)
    # interim with the first variable, then complete
    assert bound[0] == (["001_chlorophyll"], True)
    assert bound[-1] == (["001_chlorophyll", "002_temperature"], False)
    assert status_of(folder)["state"] == "complete"
    pages = sorted(os.path.basename(x) for x in glob.glob(os.path.join(pages_of(folder), "*.html")))
    assert pages == ["000_overview.html", "001_chlorophyll.html", "002_temperature.html"]
    overview = read(landing)
    assert "Traceback" not in overview and "Temperature" in overview and "Chlorophyll" in overview
    assert "It covers 2 variables" in overview
    for page in pages:
        text = read(os.path.join(pages_of(folder), page))
        assert BANNER not in text and "Traceback" not in text
        # the navigation to every page
        assert all(f'href="{other}"' in text for other in pages)
    temperature = read(os.path.join(pages_of(folder), "002_temperature.html"))
    # a figure of each, numbered from 1 on each page
    assert "Climatology" in temperature and "Figure 1" in temperature
    assert not os.path.exists(os.path.join(folder, analysis.REPORT, "_build", "raw"))


def test_an_analysis_that_fails_keeps_the_pages_before(daily, tmp_path, monkeypatch):
    original = analysis._work_out

    def work_out(row, *arguments):
        if row["name"] == "Temperature":
            raise RuntimeError("CDO failed")
        return original(row, *arguments)

    monkeypatch.setattr(analysis, "_work_out", work_out)
    out = str(tmp_path / "out")
    with pytest.raises(RuntimeError, match="CDO failed"):
        oceanval.analyze(daily, TWO, climatology=CLIMATOLOGY, n_dirs_down=1, out_dir=out, cores=1, view=False)
    folder = os.path.join(out, analysis.FOLDER)
    assert status_of(folder)["state"] == "stopped"
    page = read(os.path.join(pages_of(folder), "001_chlorophyll.html"))
    assert "Traceback" not in page and "before the analysis stopped" in page
    assert not os.path.exists(os.path.join(pages_of(folder), "002_temperature.html"))


def test_the_report_of_compare_simulations(daily, tmp_path):
    """A page for each variable, once every simulation's is worked out."""
    out = str(tmp_path / "out")
    landing = oceanval.compare_simulations(
        simulations=[
            {"name": "Base run", "sim_dir": daily, "n_dirs_down": 1},
            {"name": "Again", "sim_dir": daily, "n_dirs_down": 1},
        ],
        variables={"Temperature": "thetao", "Chlorophyll": "P1_Chl+P2_Chl"},
        climatology=CLIMATOLOGY,
        out_dir=out, cores=1, view=False,
    )
    folder = os.path.join(out, sim_compare.FOLDER)
    assert status_of(folder)["state"] == "complete"
    assert status_of(folder)["what"] == "comparison"
    pages = sorted(os.path.basename(x) for x in glob.glob(os.path.join(pages_of(folder), "*.html")))
    assert pages == ["000_overview.html", "001_temperature.html", "002_chlorophyll.html"]
    for page in pages:
        text = read(os.path.join(pages_of(folder), page))
        assert "Traceback" not in text and BANNER not in text
    assert os.path.realpath(os.path.join(folder, sim_compare.REPORT_LINK)) == os.path.realpath(landing)
    # each simulation's results say what they are, as analyze()'s do
    for key in ("base_run", "again"):
        with open(os.path.join(folder, sim_compare.RESULTS, key, analysis.INFO_FILE)) as file:
            assert [entry["key"] for entry in json.load(file)["variables"]] == ["temperature", "chlorophyll"]
