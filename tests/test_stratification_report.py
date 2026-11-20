"""The stratification page of a validation report, through a real matchup of the
small model of strat_model and validate (concise): its sections, figures and
tables, numbered without a gap, what omit leaves out, the statistics written,
and the temperature and salinity pages that come with it."""

import glob
import html
import re

import pandas as pd
import pytest

import oceanval
from figure_code_checks import check_every_figure_has_its_code, check_the_code_draws_each_figure
from oceanval import ices
from strat_model import WET_LEVELS, _casts, _s_levels


def text_of(path):
    text = open(path).read()
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    folder = tmp_path_factory.mktemp("strat_report")
    centres = _s_levels(folder / "sim")
    observations = _casts(lambda i, j: centres[0, j, i], lambda i, j: centres[WET_LEVELS - 1, j, i])
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(ices, "download_point_data", lambda recipe, start, end, lon, lat: observations.copy())
        oceanval.reset()
        try:
            oceanval.add_point_comparison(
                name="stratification", model_variable="votemper", salinity_variable="vosaline",
                recipe={"stratification": "ices"}, start=2012, end=2012,
            )
            oceanval.matchup(sim_dir=str(folder / "sim"), start=2012, end=2012, ask=False, cores=1,
                             out_dir=str(folder), thickness="e3t")
        finally:
            oceanval.reset()
    out_dir = folder / "report"
    # the scatter plot left out of the stratification page only
    oceanval.validate(data_dir=str(folder), out_dir=str(out_dir), test=True, figure_code=True,
                      omit={"stratification": ["strat_scatter"]})
    yield folder, out_dir
    oceanval.reset()


def pages(out_dir):
    return glob.glob(str(out_dir / "oceanval_report/_build/html/notebooks/*.html"))


def page(out_dir, name):
    found = [x for x in pages(out_dir) if x.endswith(f"{name}.html")]
    assert len(found) == 1, pages(out_dir)
    return text_of(found[0])


def test_the_stratification_page_has_its_sections_numbered_without_a_gap(report):
    _, out_dir = report
    text = page(out_dir, "ICES_all_stratification")
    for heading in ("Seasonal cycle of stratification", "Stratification in summer", "Statistics",
                    "Data Sources for validation of stratification of the water column"):
        assert heading in text
    assert "5 casts of ICES observations (2012)" in text
    stats_measures = pd.read_csv(out_dir / "oceanval_results" / "stratification_ICES_statistics.csv").Measure
    assert not stats_measures.str.contains("<").any()
    # none of a point page's figures of values
    assert "Depth-resolved comparisons" not in text and "Performance of model" not in text
    for word, count in (("Figure", 3), ("Table", 2)):
        found = [int(x) for x in re.findall(rf"{word} (\d+) ?:", text)]
        assert found == list(range(1, count + 1)), (word, found)


def test_omit_leaves_out_the_scatter_plot_and_concise_the_map_of_the_casts(report):
    _, out_dir = report
    text = page(out_dir, "ICES_all_stratification")
    assert "Model versus observations" not in text
    assert "Locations of the ICES casts" not in text


def test_the_statistics_are_written_whatever_is_shown(report):
    _, out_dir = report
    stats = pd.read_csv(out_dir / "oceanval_results" / "stratification_ICES_statistics.csv")
    assert len(stats) == 4 and (stats["Number of casts"] == 5).all()
    # the casts are the model's own, so the model has no bias
    assert stats.Bias.abs().max() < 1e-3
    agreement = pd.read_csv(out_dir / "oceanval_results" / "stratification_ICES_agreement.csv")
    assert (agreement.Agreement == "100%").all()


def test_the_temperature_and_salinity_pages_come_with_it(report):
    _, out_dir = report
    assert "Depth-resolved comparisons" in page(out_dir, "ICES_all_temperature")
    assert "Depth-resolved comparisons" in page(out_dir, "ICES_all_salinity")
    assert page(out_dir, "summary")


def test_the_code_of_each_figure_draws_it_again(report, tmp_path):
    _, out_dir = report
    html_dir = str(out_dir / "oceanval_report/_build/html/notebooks")
    check_every_figure_has_its_code(html_dir, str(out_dir / "oceanval_results"))
    check_the_code_draws_each_figure(html_dir, str(tmp_path))
    drawn = "".join(open(p).read() for p in glob.glob(f"{tmp_path}/*.py"))
    assert "plotnine_figures.stratification_cycle(" in drawn
