"""The rows of the Compare simulations options step that say what to treat as
missing (oceanval.sim_compare_forms.check_missing_rows)."""

import pytest

from oceanval import sim_compare_forms

NAMES = ["Base run", "Fine run"]


def row(apply="", low="", high=""):
    return {"apply": apply, "from": low, "to": high}


def test_all_and_a_simulations_own_value():
    everyone, own, errors = sim_compare_forms.check_missing_rows(
        [row("All", "-9"), row("Fine run", "0", "1"), row()], NAMES
    )
    assert errors == {} and everyone == -9.0 and own == {"Fine run": [0.0, 1.0]}


def test_nothing_in_the_rows_is_no_missing_value():
    assert sim_compare_forms.check_missing_rows([row()], NAMES) == (None, {}, {})
    assert sim_compare_forms.check_missing_rows([], NAMES) == (None, {}, {})
    assert sim_compare_forms.check_missing_rows("nonsense", NAMES) == (None, {}, {})


@pytest.mark.parametrize(
    "rows, errors",
    [
        ([row("", "0")], {0: {"apply": "Choose what this applies to."}}),
        ([row("Base run")], {0: {"missing_from": "Fill in the value, or the start of a range."}}),
        ([row("Nobody", "0")], {0: {"apply": "Nobody is not one of the simulations."}}),
        ([row("All", "0"), row("All", "1")], {1: {"apply": "All is already set in row 1."}}),
        ([row("All", "x")], {0: {"missing_from": "This must be a number."}}),
        ([row("All", "1", "0")], {0: {"missing_to": "The end of the range must not be less than its start."}}),
    ],
)
def test_what_is_refused_to_run(rows, errors):
    assert sim_compare_forms.check_missing_rows(rows, NAMES)[2] == errors


def test_half_filled_rows_are_not_errors_while_the_boxes_are_being_filled_in():
    rows = [row("", "0"), row("Base run")]
    assert sim_compare_forms.check_missing_rows(rows, NAMES, final=False)[2] == {}
    # numbers that are not numbers and repeats still are
    rows = [row("All", "x"), row("All", "1")]
    assert set(sim_compare_forms.check_missing_rows(rows, NAMES, final=False)[2]) == {0, 1}

