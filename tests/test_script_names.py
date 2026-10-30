"""The script a window run starts with is named after the first free name,
so an earlier one is not written over unless its name is typed."""

import pytest

from oceanval import analysis_forms, sim_compare_forms
from oceanval.app import default_output_form, free_name


def test_the_first_free_name_is_used(tmp_path):
    cwd = str(tmp_path)
    assert free_name(cwd, "matchup") == "matchup.py"
    (tmp_path / "matchup.py").write_text("")
    assert free_name(cwd, "matchup") == "matchup1.py"
    (tmp_path / "matchup1.py").write_text("")
    (tmp_path / "matchup3.py").write_text("")
    # the first gap, not the one after the last
    assert free_name(cwd, "matchup") == "matchup2.py"
    # a directory of that name is as taken as a file
    (tmp_path / "matchup2.py").mkdir()
    assert free_name(cwd, "matchup") == "matchup4.py"
    # other names are unaffected
    assert free_name(cwd, "analysis") == "analysis.py"


@pytest.mark.parametrize(
    "stem, form",
    [
        ("matchup", lambda cwd: default_output_form(cwd)),
        ("analysis", lambda cwd: analysis_forms.default_setup_form(cwd)),
        ("simulation_comparison", lambda cwd: sim_compare_forms.default_output_form(cwd)),
    ],
)
def test_each_window_starts_with_a_free_name(tmp_path, stem, form):
    cwd = str(tmp_path)
    assert form(cwd)["out"] == f"{stem}.py"
    (tmp_path / f"{stem}.py").write_text("")
    assert form(cwd)["out"] == f"{stem}1.py"
    (tmp_path / f"{stem}1.py").write_text("")
    assert form(cwd)["out"] == f"{stem}2.py"
    # nothing is typed to start with
    assert form(cwd)["out_typed"] is False


def test_what_the_user_typed_is_kept_by_the_comparison_form(tmp_path):
    cwd = str(tmp_path)
    cleaned = sim_compare_forms.clean_output_form({"out": "mine.py", "out_typed": True}, cwd)
    assert cleaned["out"] == "mine.py" and cleaned["out_typed"] is True
    assert sim_compare_forms.clean_output_form({"out": "mine.py"}, cwd)["out_typed"] is False


def test_the_app_starts_with_free_names(tmp_path):
    from oceanval.app import App

    for name in ("matchup.py", "analysis.py", "simulation_comparison.py", "analysis1.py"):
        (tmp_path / name).write_text("")
    app = App(cwd=str(tmp_path))
    try:
        assert app.output_form["out"] == "matchup1.py"
        assert app.analyze_setup_form["out"] == "analysis2.py"
        assert app.sc_output_form["out"] == "simulation_comparison1.py"
    finally:
        app.close()
