"""release_figures.csv: which report figures a release of OceanVal has."""

import pytest

from oceanval import app, option_profiles, release_figures, report_items


@pytest.fixture
def staged(monkeypatch):
    """Leave out one figure, as an N in the csv does."""
    table = dict(release_figures._table())
    kind, _ = table["density_scatter"]
    table["density_scatter"] = (kind, "N")
    monkeypatch.setattr(release_figures, "_table", lambda: table)
    return "density_scatter"


class TestTheFile:
    def test_it_has_a_row_for_every_figure_and_nothing_wrong(self):
        assert release_figures.problems() == []

    def test_every_figure_but_the_old_ones_is_once_in_the_file(self):
        assert set(release_figures._table()) == set(report_items.ids()) - set(release_figures.ALWAYS)

    def test_the_old_figures_are_in_the_catalogue(self):
        assert set(release_figures.ALWAYS) <= set(report_items.ids())

    def test_released_and_not_released_are_every_figure(self):
        assert release_figures.in_release() | set(release_figures.not_released()) == set(report_items.ids())


class TestAFigureNotInRelease:
    def test_it_is_not_offered_by_the_window(self, staged):
        assert staged in release_figures.not_released()
        matchup = {"kind": "gridded", "variable": "chl", "source": "x", "layer": None}
        assert staged not in option_profiles.items_for(matchup)

    def test_it_is_left_out_of_what_validate_draws(self, staged):
        assert app._with_unreleased(None) == [staged]
        assert staged in app._with_unreleased(["clim_map"])
        assert staged in app._with_unreleased({"chl/x": ["clim_map"]})["*"]

    def test_nothing_is_added_when_all_are_in_release(self):
        assert app._with_unreleased(None) is None


class TestAFigureInRelease:
    def test_the_window_marks_it_in_development(self):
        assert app.REPORT_ITEMS
        for item in app.REPORT_ITEMS:
            assert item["in_development"] == (item["id"] not in release_figures.ALWAYS)

    def test_the_old_figures_are_always_offered_and_never_amber(self, staged):
        shown = {item["id"]: item for item in app.REPORT_ITEMS}
        for name in release_figures.ALWAYS:
            assert name in shown and not shown[name]["in_development"]
        assert app._with_unreleased(None) == [staged]


def test_a_concise_report_leaves_out_every_figure_added_since_the_first_release():
    from oceanval import release_figures, report_items

    left_out = release_figures.detailed_only()
    assert set(left_out) == set(report_items.ids()) - set(release_figures.ALWAYS)
    # none of the figures the report always had is among them
    assert not set(left_out) & set(release_figures.ALWAYS)
