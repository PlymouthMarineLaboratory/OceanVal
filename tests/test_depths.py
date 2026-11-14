"""The depth bins point matchups through the water column are summarised in
(oceanval.depths), which validate() takes as depth_bins."""

import math

import pytest

from oceanval import depths
from oceanval.depths import bin_depth, check_depth_bins, depth_labels, depth_ranges_text


def test_the_default_bins_are_oceanvals_own():
    assert depth_labels(check_depth_bins(None)) == [
        "0-10m", "10-30m", "30-60m", "60-100m", "100-150m",
        "150-300m", "300-600m", "600-1000m", ">1000m",
    ]


@pytest.mark.parametrize(
    "depth, label",
    [
        (0, "0-10m"),
        (-1, "0-10m"),
        (10, "0-10m"),
        (10.01, "10-30m"),
        (1000, "600-1000m"),
        (1000.5, ">1000m"),
        (5000, ">1000m"),
        (math.nan, None),
    ],
)
def test_the_default_bins_bin_depths_as_they_always_have(depth, label):
    assert bin_depth(depth, depths.DEFAULT_DEPTH_BINS) == label


def test_depths_in_a_gap_or_outside_every_bin_are_in_none():
    bins = check_depth_bins([[5, 20], [50, 100]])

    assert bin_depth(0, bins) is None
    assert bin_depth(5, bins) == "5-20m"
    assert bin_depth(30, bins) is None
    assert bin_depth(50, bins) is None
    assert bin_depth(50.5, bins) == "50-100m"
    assert bin_depth(101, bins) is None


def test_the_bins_are_sorted_and_labelled():
    bins = check_depth_bins([[200, None], [0, 20.0], (20, 200)])

    assert bins == [(0, 20), (20, 200), (200, None)]
    assert depth_labels(bins) == ["0-20m", "20-200m", ">200m"]
    assert depth_labels(check_depth_bins([[0, 2.5], [2.5, 7.25]])) == ["0-2.5m", "2.5-7.25m"]


@pytest.mark.parametrize(
    "bins, problem",
    [
        ([], "at least one bin"),
        ("0-10", "list of [min, max] pairs"),
        ([[0, 10, 20]], "list of [min, max] pairs"),
        ([[None, 10]], "needs a min"),
        ([["0", 10]], "must be a number"),
        ([[True, 10]], "must be a number"),
        ([[0, math.inf]], "finite"),
        ([[-5, 10]], "0 or more"),
        ([[10, 10]], "deeper than its min, not 10-10m"),
        ([[0, None], [10, 20]], "only the deepest bin can have no max"),
        ([[0, 20], [10, 30]], "0-20m and 10-30m overlap"),
        ([[0, 20], [0, 20]], "overlap"),
    ],
)
def test_bins_that_cannot_be_used_are_refused(bins, problem):
    with pytest.raises(ValueError, match="depth_bins"):
        check_depth_bins(bins)
    with pytest.raises(ValueError) as error:
        check_depth_bins(bins)
    assert problem in str(error.value)


def test_the_caption_lists_the_bins():
    assert depth_ranges_text(depth_labels(depths.DEFAULT_DEPTH_BINS)) == (
        "The depth ranges are 0-10m, 10-30m, 30-60m, 60-100m, 100-150m, "
        "150-300m, 300-600m, 600-1000m, and >1000m."
    )
    assert depth_ranges_text(["0-20m", ">20m"]) == "The depth ranges are 0-20m and >20m."
    assert depth_ranges_text([">0m"]) == "The depth range is >0m."


class TestMapDepths:
    """validate's map_depths: the depths gridded matchups through the water column are mapped at."""

    def test_none_is_the_defaults_and_false_is_none(self):
        assert depths.check_map_depths(None) is None
        assert depths.check_map_depths(False) is False
        assert depths.map_depths_for("oxygen", None) == [200, 500]
        assert depths.map_depths_for("nitrate", None) == [500]
        assert depths.map_depths_for("oxygen", False) == []

    def test_a_list_is_sorted_once_each_and_used_for_every_variable(self):
        assert depths.check_map_depths([1000, 200, 200.0]) == [200, 1000]
        assert depths.map_depths_for("oxygen", [50]) == [50]

    @pytest.mark.parametrize("value", ["200", {"a": 1}, [], [-5], ["deep"], 7])
    def test_what_cannot_be_used_is_refused(self, value):
        with pytest.raises(ValueError):
            depths.check_map_depths(value)

    def test_the_nearest_level_must_be_near(self):
        levels = [3.0, 9.4, 23.3, 102.8, 454.1]
        assert depths.nearest_level(levels, 20) == 2
        assert depths.nearest_level(levels, 500) == 4  # 46 m off, within a fifth of 500 m
        assert depths.nearest_level(levels, 2000) is None
        assert depths.nearest_level([], 20) is None
