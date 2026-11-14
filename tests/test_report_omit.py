"""validate's omit, through real matchups: figures and analyses the user leaves
out of the report are not in its pages, the rest are numbered from 1 without
a gap, and what the other pages read (the summary's inputs) is still written.
Both the full report and the interim report matchup builds as it goes."""

import glob
import html
import json
import os
import re

import nctoolkit as nc
import pytest

import oceanval
from oceanval import live

# what the second report leaves out of each page
OMIT = {
    "temperature/foo": ["bias_map", "monthly_maps", "spatial_cor_table"],
    "temperature/bar": ["point_bias_map", "regression_table", "stats_table", "depth_table", "scatter"],
    "summary": ["taylor", "depth_performance"],
}


@pytest.fixture(scope="module")
def matchups(tmp_path_factory):
    """A gridded and a point (through the water column) matchup of the example
    model's temperature, with the interim report matchup builds as it goes,
    which leaves the figures out too."""
    folder = tmp_path_factory.mktemp("omit")
    # observations that are the example model's own monthly means
    obs = folder / "obs"
    obs.mkdir()
    ds = nc.open_data(sorted(glob.glob("data/example/2000/*/*grid_T.nc")), checks=False)
    ds.subset(variables="votemper")
    ds.merge("time")
    ds.tmean("month")
    ds.to_nc(str(obs / "obs_2000.nc"), overwrite=True)

    oceanval.reset()
    oceanval.add_gridded_comparison(
        name="temperature",
        obs_path=str(obs),
        source="foo",
        model_variable="votemper",
        obs_variable="votemper",
        climatology=True,
        start=2000,
        end=2000,
    )
    oceanval.add_point_comparison(
        name="temperature",
        obs_path="data/evaldata/point/nws/all/temperature",
        source="bar",
        model_variable="votemper",
        vertical=True,
        obs_adder=273.15,
    )
    oceanval.matchup(
        sim_dir="data/example",
        start=2000,
        end=2001,
        ask=False,
        cores=1,
        thickness="data/example/e3t.nc",
        out_dir=str(folder),
        live_validation={"concise": False, "omit": OMIT, "test": True},
    )
    yield folder
    oceanval.reset()


def text_of(path):
    """What a built report page says."""
    text = open(path).read()
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))


def pages(folder):
    """The text of each page of the report in folder, by what is after its number."""
    found = {}
    for path in glob.glob(str(folder / "oceanval_report/_build/html/notebooks/*.html")):
        name = re.sub(r"^\d+_", "", os.path.basename(path))[: -len(".html")]
        found[name] = text_of(path)
    return found


def numbers(text, word="Figure"):
    # a caption is "Figure 1 :" or "Figure 1:", as the tags between are taken out
    return [int(x) for x in re.findall(rf"{word} (\d+) ?:", text)]


@pytest.fixture(scope="module")
def detailed(matchups):
    """The detailed report, with every figure."""
    out_dir = matchups / "detailed"
    oceanval.validate(data_dir=str(matchups), out_dir=str(out_dir), concise=False, test=True)
    return pages(out_dir)


@pytest.fixture(scope="module")
def omitted(matchups):
    """The report, with what OMIT leaves out."""
    out_dir = matchups / "omitted"
    oceanval.validate(
        data_dir=str(matchups), out_dir=str(out_dir), concise=False, omit=OMIT, test=True
    )
    return pages(out_dir)


def test_the_detailed_report_has_what_is_left_out_of_the_other(detailed):
    gridded = detailed["foo_temperature"]
    assert "Assessing model bias" in gridded
    assert re.search(r"Monthly mean .* for the model, observation and the difference", gridded)
    assert "Pearson correlation coefficient between modelled and observed" in gridded
    assert "Taylor diagram" in detailed["summary"]
    # (the notebook's own spelling of "Performance")
    assert "of model across depths" in detailed["summary"]
    # every page numbers its figures from 1 without a gap
    for name, text in detailed.items():
        found = numbers(text)
        assert found == list(range(1, len(found) + 1)), (name, found)


def test_a_gridded_page_leaves_out_the_figures_omitted(detailed, omitted):
    text = omitted["foo_temperature"]

    # the bias map, its heading and its words
    assert "Assessing model bias" not in text
    assert "shows the average bias" not in text
    assert "Bias of" not in text
    # the monthly maps and the spatial correlation table
    assert not re.search(r"Monthly mean .* for the model, observation and the difference", text)
    assert "can the model reproduce spatial patterns" not in text.lower()
    # what was not left out is still there
    assert "Annual average" in text
    assert "Seasonal temporal correlation between model and observations" in text
    # and is numbered from 1 without a gap, with fewer than the detailed report's
    found = numbers(text)
    assert found == list(range(1, len(found) + 1))
    assert len(found) == len(numbers(detailed["foo_temperature"])) - 2
    assert numbers(text, "Table") == list(range(1, len(numbers(text, "Table")) + 1))
    assert len(numbers(text, "Table")) == len(numbers(detailed["foo_temperature"], "Table")) - 1
    # the page still ran to the end
    assert "This is getting to the end!" in text


def test_a_point_page_leaves_out_the_figures_and_tables_omitted(detailed, omitted):
    before = detailed["bar_all_temperature"]
    text = omitted["bar_all_temperature"]

    # the bias map, the scatter plot and both of the tables, so the section of them is left out
    assert "shows the bias between the model and observational data" in before
    assert "shows the bias between the model and observational data" not in text
    assert "Bias in" in before and "Bias in" not in text
    assert "Simulated versus observed" in before and "Simulated versus observed" not in text
    assert "Summary statistics for" in before and "Summary statistics for" not in text
    assert "Linear regression analysis" in before and "Linear regression analysis" not in text
    # and the table of the depths, whose section is left out as the map of them is not
    depth_caption = "root-mean square difference (RMSD) and correlation coefficient of modelled and observed"
    assert depth_caption in before and depth_caption not in text
    assert "Depth-resolved comparisons" in text
    # the map of the matchups, which is not
    assert "Locations of matchups" in text
    # the figures and the tables are numbered from 1 without a gap
    for word, left_out in (("Figure", 2), ("Table", 3)):
        found = numbers(text, word)
        assert found == list(range(1, len(found) + 1)), (word, found)
        assert len(found) == len(numbers(before, word)) - left_out
    assert "This is getting to the end!" in text


def test_the_summary_leaves_out_the_figures_omitted(detailed, omitted):
    text = omitted["summary"]

    assert "Taylor diagram" not in text
    # its other tables are still there
    assert "Model biases based on gridded sea surface data" in text
    assert "Spatial performance of the model" in text
    assert "Temporal performance of the model" in text
    assert "Map of the locations of matchups at each depth range" not in text
    assert "Perfomance of model across depths" not in text
    # the Taylor diagram and the map of the depth ranges, and the three tables of the depths
    for word, left_out in (("Figure", 2), ("Table", 3)):
        found = numbers(text, word)
        assert found == list(range(1, len(found) + 1))
        assert len(found) == len(numbers(detailed["summary"], word)) - left_out


def test_what_other_pages_read_is_still_written(matchups, omitted):
    results = matchups / "omitted" / "oceanval_results"

    # the summary's inputs, whatever the pages show
    assert glob.glob(str(results / "annual_mean" / "*.nc"))
    assert glob.glob(str(results / "temporals" / "*.nc"))
    assert glob.glob(str(results / "*_depth_summary.csv"))
    # a bias map left out of a point page still leaves its csv
    assert glob.glob(str(results / "surface" / "temperature" / "*_bias.csv"))


def test_the_interim_report_leaves_them_out_too(matchups):
    notebooks = matchups / live.FOLDER / "oceanval_report/_build/html/notebooks"
    gridded = text_of(next(iter(glob.glob(str(notebooks / "*foo_temperature.html")))))

    assert "Assessing model bias" not in gridded
    assert "Annual average" in gridded
    assert "This is getting to the end!" in gridded
    status = json.load(open(matchups / live.FOLDER / "status.json"))
    assert status["failed"] == []


def test_an_omit_that_picks_no_matchup_is_refused_before_anything_is_removed(matchups, tmp_path):
    out_dir = tmp_path / "report"
    with pytest.raises(ValueError, match="there are no matchups of 'nitrate'"):
        oceanval.validate(
            data_dir=str(matchups), out_dir=str(out_dir), omit={"nitrate": ["bias_map"]}, test=True
        )
    assert not out_dir.exists()
