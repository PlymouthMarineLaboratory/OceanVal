"""validate's transect option, through a real vertical matchup: the gridded
report page maps the transect, plots its months and draws a section of the
water column, in the full report and in the interim one."""

import glob
import html
import json
import re

import nctoolkit as nc
import pytest

import oceanval
from oceanval import live, transects

pytestmark = pytest.mark.skipif(
    not transects.available(), reason="needs nctoolkit 1.3.6 or later"
)

# the example model's grid is a few cells across
NORTH_SOUTH = {"start": [3.2, 54.0], "end": [3.2, 54.46]}
EAST_WEST = {"start": [3.11, 54.2], "end": [3.44, 54.2]}


@pytest.fixture(scope="module")
def matchups(tmp_path_factory):
    """A vertical matchup of the example model's temperature with
    observations that are the model's own monthly means on its 51 uneven
    levels, with the interim report matchup builds as it goes."""
    folder = tmp_path_factory.mktemp("transects")
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
        vertical=True,
    )
    # a second source, as an interim report is not built for only one
    oceanval.add_gridded_comparison(
        name="temperature",
        obs_path=str(obs),
        source="baz",
        model_variable="votemper",
        obs_variable="votemper",
        climatology=True,
        start=2000,
        end=2000,
        vertical=True,
    )
    oceanval.matchup(
        sim_dir="data/example",
        start=2000,
        end=2000,
        thickness="z_level",
        ask=False,
        cores=1,
        out_dir=str(folder),
        live_validation={"transect": NORTH_SOUTH, "test": True},
    )
    yield folder
    oceanval.reset()


def page_text(page):
    """What a built report page says."""
    text = open(page).read()
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))


def figures(text):
    """The numbers of the figures with captions, in order."""
    return [int(x) for x in re.findall(r"Figure (\d+) :", text)]


def test_the_vertical_matchup_has_a_depth_axis_each(matchups):
    ff = matchups / "oceanval_matchups/gridded/temperature/foo_temperature_vertical.nc"
    ds = nc.open_data(str(ff), checks=False)

    assert len(ds.levels) == 51
    # the transect is extracted from each separately
    frame = transects.section_frame(str(ff), transects.check_transect(NORTH_SOUTH), 8)
    assert len(frame) == 30 * frame.position.nunique()


def test_the_interim_report_has_the_transect(matchups):
    pages = matchups / live.FOLDER / "oceanval_report/_build/html/notebooks"
    text = page_text(pages / "foo_temperature.html")

    assert "along the transect?" in text
    assert "runs north–south along 3.2°E, from 54°N to 54.46°N" in text
    assert "This is getting to the end!" in text


@pytest.fixture(scope="module")
def reports(matchups):
    """The full report, along each transect and with none: the gridded page of each."""
    pages = {}
    for name, transect in (("north_south", NORTH_SOUTH), ("east_west", EAST_WEST), ("none", None)):
        out_dir = matchups / name
        oceanval.validate(
            data_dir=str(matchups), out_dir=str(out_dir), transect=transect, test=True
        )
        # numbered by source within the variable (baz, then foo)
        [page] = glob.glob(str(out_dir / "oceanval_report/_build/html/notebooks/*_foo_temperature.html"))
        pages[name] = page_text(page)
    return pages


@pytest.mark.parametrize("name, direction", [
    ("north_south", "north–south along 3.2°E, from 54°N to 54.46°N"),
    ("east_west", "east–west along 54.2°N, from 3.11°E to 3.44°E"),
])
def test_the_report_maps_and_plots_the_transect(reports, name, direction):
    text = reports[name]
    section = text[text.index("along the transect?"): text.index("Data Sources for validation")]

    assert f"runs {direction}" in section
    assert re.search(r"onto \d+ evenly spaced points", section)
    # the map, the months and the water column
    assert "mapped in Figure" in section
    assert "monthly climatology of sea surface temperature along the transect" in section
    assert "interpolated onto 30 evenly spaced depths, from 3.04 m to 5822.17 m" in section
    assert len(re.findall(r"Figure \d+ :", section)) == 3
    # nothing in it failed, and the page ran to the end
    assert "could not be" not in section
    assert "This is getting to the end!" in text


def test_without_a_transect_the_page_is_unchanged(reports):
    text = reports["none"]

    assert "along the transect" not in text
    # the page's own figures keep their numbers, and the transect's come after
    for name in ("north_south", "east_west"):
        numbers = figures(reports[name])
        assert numbers == list(range(1, len(numbers) + 1))
        assert numbers[: len(figures(text))] == figures(text)
        assert len(numbers) == len(figures(text)) + 3
