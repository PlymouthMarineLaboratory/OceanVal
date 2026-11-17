"""The examples of the report's figures and tables the window's tiles show
(oceanval.report_examples): each is drawn once, from made-up data, and nothing
is written to disk."""

import base64
import os

import pytest

from oceanval import report_examples, report_items


@pytest.fixture(scope="module")
def drawn(tmp_path_factory):
    # drawn in an empty folder, to show that nothing is written
    folder = tmp_path_factory.mktemp("examples")
    here = os.getcwd()
    os.chdir(folder)
    try:
        return {item: report_examples.example(item) for item in report_items.ids()}, folder
    finally:
        os.chdir(here)


def test_every_item_has_an_example():
    assert set(report_examples.DRAW) == set(report_items.BY_ID)


@pytest.mark.parametrize("item", report_items.ids())
def test_each_example_is_drawn(drawn, item):
    found = drawn[0][item]
    assert found["ok"], found.get("error")
    if found["kind"] == "image":
        assert base64.b64decode(found["data"])[:8] == b"\x89PNG\r\n\x1a\n"
    else:
        assert found["kind"] == "table" and "<table" in found["html"]


def test_tables_are_tables_and_figures_are_pictures(drawn):
    kinds = {item: found["kind"] for item, found in drawn[0].items()}
    for item in ("spatial_cor_table", "depth_level_table", "stats_table", "bias_table", "global_totals"):
        assert kinds[item] == "table", item
    for item in ("density_scatter", "monthly_maps", "taylor", "scatter", "zonal_sections"):
        assert kinds[item] == "image", item


def test_nothing_is_written_and_a_second_ask_is_kept(drawn):
    examples, folder = drawn
    assert os.listdir(folder) == []
    assert report_examples.example("taylor") is examples["taylor"]


def test_the_depth_table_example_has_the_reports_levels(drawn):
    assert drawn[0]["depth_level_table"]["html"].count("<tr") <= 11


def test_what_is_not_an_item_has_none():
    assert report_examples.example("nothing") == {"ok": False, "error": "There is no example of that."}
