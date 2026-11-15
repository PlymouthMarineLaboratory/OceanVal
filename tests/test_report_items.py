"""The figures and analyses a validation report can leave out (validate's
omit, see oceanval.report_items), and that the report's notebooks ask for
each of them."""

import glob
import os
import re

import pytest

from oceanval import report_items, selection

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "oceanval", "data")

# the notebooks and chunks a validation report is made of
TEMPLATES = ["gridded_template.ipynb", "point_template.ipynb", "summary.ipynb"] + [
    os.path.basename(path)
    for path in glob.glob(os.path.join(DATA, "chunk_*.pytemplate"))
    if os.path.basename(path)
    in (
        "chunk_clim.pytemplate", "chunk_bias.pytemplate", "chunk_seasonal.pytemplate",
        "chunk_point.pytemplate", "chunk_transect.pytemplate", "chunk_verticalstats.pytemplate",
        "chunk_density.pytemplate", "chunk_peakmonth.pytemplate", "chunk_zonal.pytemplate",
        "chunk_profiles.pytemplate", "chunk_depthmaps.pytemplate", "chunk_totals.pytemplate",
    )
]

MATCHUPS = [
    {"kind": "gridded", "variable": "temperature", "source": "cobe2", "layer": None,
     "vertical": False, "title": "Temperature", "key": "temperature/cobe2"},
    {"kind": "point", "variable": "nitrate", "source": "ices", "layer": "surface",
     "vertical": False, "title": "Nitrate", "key": "nitrate/ices/surface"},
]


def templates_text():
    text = ""
    for name in TEMPLATES:
        with open(os.path.join(DATA, name)) as file:
            text += file.read()
    return text


class TestCatalogue:
    def test_ids_are_unique_and_each_has_what_the_window_shows(self):
        found = report_items.ids()
        assert len(found) == len(set(found))
        for item in report_items.ITEMS:
            assert item.page in report_items.PAGES
            assert item.label and item.text
            assert all(condition in report_items.WHEN for condition in item.when)
        assert [item["id"] for item in report_items.as_json()] == found

    def test_every_id_is_asked_for_by_the_report(self):
        text = templates_text()
        for item in report_items.ids():
            # the notebooks are json, which escapes the quotes
            assert re.search(rf'wanted\(\\?"{item}\\?"\)', text), f"{item} is not in any notebook"

    def test_the_notebooks_ask_for_no_id_that_is_not_there(self):
        asked = set(re.findall(r'wanted\(\\?"([a-z_]+)\\?"\)', templates_text()))
        assert asked <= set(report_items.ids()), asked - set(report_items.ids())

    def test_the_notebooks_leave_out_the_removed_items(self):
        # the placeholder validate fills in is in the chunk every notebook starts with
        with open(os.path.join(DATA, "chunk_start.pytemplate")) as file:
            assert "the_removed_items" in file.read()


class TestCheckOmit:
    def test_nothing_is_nothing(self):
        assert report_items.check_omit(None) is None
        assert report_items.check_omit([]) is None
        assert report_items.check_omit({}) is None
        assert report_items.check_omit({"temperature": []}) is None

    def test_a_list_is_for_every_page_in_the_order_of_the_catalogue(self):
        assert report_items.check_omit(["taylor", "monthly_maps", "taylor"]) == {
            "*": ["monthly_maps", "taylor"]
        }
        assert report_items.check_omit("taylor") == {"*": ["taylor"]}

    def test_a_dict_is_for_the_matchups_it_names_and_the_summary(self):
        omit = report_items.check_omit({"temperature/COBE2": ["bias_map"], "Summary": "taylor"})
        assert omit == {"temperature/COBE2": ["bias_map"], "summary": ["taylor"]}

    @pytest.mark.parametrize(
        "omit, message",
        [
            (["nonsense"], "not a figure or analysis"),
            ({"temperature": ["nonsense"]}, "not a figure or analysis"),
            ({"summary": ["bias_map"]}, "not on the summary page"),
            ({"temperature": ["taylor"]}, "summary page"),
            ({"": ["bias_map"]}, "keys must be"),
            ({3: ["bias_map"]}, "keys must be"),
            ({"temperature": {"bias_map": 1}}, "list of figure ids"),
            (3, "list of figure ids"),
        ],
    )
    def test_what_cannot_be_used_is_refused(self, omit, message):
        with pytest.raises(ValueError, match=message):
            report_items.check_omit(omit)

    def test_a_matchup_that_is_not_there_is_refused(self):
        omit = report_items.check_omit({"nitrate/nowhere": ["bias_map"]})
        with pytest.raises(ValueError, match="no matchups of 'nitrate/nowhere'"):
            report_items.check_against(omit, MATCHUPS)

    def test_a_figure_that_is_not_on_the_page_of_a_matchup_is_refused(self):
        # a point matchup has no gridded figures
        omit = report_items.check_omit({"nitrate": ["clim_map"]})
        with pytest.raises(ValueError, match="not on the point page"):
            report_items.check_against(omit, MATCHUPS)
        # and one matchup of each kind has both
        report_items.check_against(report_items.check_omit({"temperature": ["clim_map"]}), MATCHUPS)
        report_items.check_against(report_items.check_omit(["clim_map", "taylor"]), MATCHUPS)
        report_items.check_against(None, MATCHUPS)


class TestRemovedFor:
    omit = report_items.check_omit(
        {"temperature": ["bias_map", "monthly_maps"], "nitrate/ices": ["point_bias_map"], "summary": ["taylor"]}
    )

    def test_each_page_has_its_own(self):
        gridded = report_items.removed_for(self.omit, "gridded", "temperature", "cobe2")
        point = report_items.removed_for(self.omit, "point", "nitrate", "ices", "surface")
        summary = report_items.removed_for(self.omit, "summary")
        assert gridded == {"bias_map", "monthly_maps"}
        assert point == {"point_bias_map"}
        assert summary == {"taylor"}

    def test_a_matchup_not_named_leaves_nothing_out(self):
        assert report_items.removed_for(self.omit, "gridded", "salinity", "cobe2") == set()
        assert report_items.removed_for(self.omit, "point", "nitrate", "other", "surface") == set()

    def test_a_list_is_for_every_matchup_but_only_the_pages_the_figure_is_on(self):
        omit = report_items.check_omit(["bias_map", "point_bias_map", "taylor"])
        assert report_items.removed_for(omit, "gridded", "a", "b") == {"bias_map"}
        assert report_items.removed_for(omit, "point", "a", "b", "all") == {"point_bias_map"}
        assert report_items.removed_for(omit, "summary") == {"taylor"}

    def test_nothing_is_left_out_without_omit(self):
        assert report_items.removed_for(None, "gridded", "a", "b") == set()

    def test_a_layer_picks_the_layer_of_a_point_matchup(self):
        omit = report_items.check_omit({"nitrate/ices/all": ["depth_table"]})
        assert report_items.removed_for(omit, "point", "nitrate", "ices", "all") == {"depth_table"}
        assert report_items.removed_for(omit, "point", "nitrate", "ices", "surface") == set()


def test_the_keys_are_those_selection_gives():
    # omit's keys are the matchups' keys, as variables takes them
    for matchup in MATCHUPS:
        assert selection.wanted([matchup["key"]], matchup["variable"], matchup["source"], matchup["layer"])
