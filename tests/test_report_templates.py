import json
from pathlib import Path

import pytest


def test_gridded_climatology_template_displays_figure_once():
    template = Path("oceanval/data/chunk_clim.pytemplate").read_text()

    assert '\nfig\n' not in template


def test_seasonal_template_r_figures_are_guarded_by_panel_plot_flags():
    template = Path("oceanval/data/chunk_seasonal.pytemplate").read_text()

    # the R figures of each half of the year are only drawn if panel_plot did not draw them
    assert template.count("-i seasonal_panel ") == 2
    assert template.count("!seasonal_panel){") == 2


def test_seasonal_template_shows_global_maps_four_months_at_a_time():
    template = Path("oceanval/data/chunk_seasonal.pytemplate").read_text()
    code = template[template.index("if global_grid:\n    seasonal_periods"):]
    code = code[:code.index("\n\n")]

    periods = dict()
    exec(code, {"global_grid": True}, periods)
    assert [list(x) for x in periods["seasonal_periods"]] == [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]]

    periods = dict()
    exec(code, {"global_grid": False}, periods)
    assert [list(x) for x in periods["seasonal_periods"]] == [[1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12]]


@pytest.mark.parametrize("notebook", ["gridded_template.ipynb", "comparison_regional.ipynb"])
def test_region_map_r_figure_is_guarded_by_panel_plot_flag(notebook):
    text = Path(f"oceanval/data/{notebook}").read_text()

    assert "-i region_panel_plot " in text
    assert "!region_panel_plot){" in text


def test_regional_time_series_r_cell_loads_the_tidyverse_itself():
    # the R cells that used to load it earlier are skipped when panel_plot draws the maps
    cells = json.loads(Path("oceanval/data/gridded_template.ipynb").read_text())["cells"]
    sources = ["".join(cell["source"]) for cell in cells]
    source = [x for x in sources if "%%R -i df_all -i regional" in x][0]

    assert source.index("library(tidyverse") < source.index("%>%")


def test_gridded_r_cells_import_model_unit_when_they_use_it():
    # model_unit is only set by the seasonal chunk, which is left out without a full year
    cells = json.loads(Path("oceanval/data/gridded_template.ipynb").read_text())["cells"]
    sources = ["".join(cell["source"]) for cell in cells]
    r_cells = [x for x in sources if x.startswith("%%R")]

    assert r_cells
    for source in r_cells:
        magic, body = source.split("\n", 1)
        if "model_unit" in body:
            assert "-i model_unit" in magic


def test_seasonal_template_separates_groups_of_months_with_a_line():
    template = Path("oceanval/data/chunk_seasonal.pytemplate").read_text()

    # every group of months but the last is followed by a line under its difference maps
    assert "def plot_seasonal_months(months, divider = False):" in template
    assert "divider = i < len(seasonal_periods) - 1" in template


def test_seasonal_template_titles_maps_with_full_month_names():
    template = Path("oceanval/data/chunk_seasonal.pytemplate").read_text()
    source = template[template.index("def plot_seasonal_months"):template.index("    return True\n")]

    assert "calendar.month_name[month]" in source
    assert "month_abbr" not in source


def test_the_depth_bins_are_filled_in_where_the_notebooks_start(tmp_path):
    import oceanval

    script = tmp_path / "notebook.py"
    script.write_text(Path("oceanval/data/chunk_start.pytemplate").read_text())

    oceanval._rewrite_notebook_source(
        str(script), None, None, False, True, True, depth_bins=[(0, 20), (20, None)]
    )
    assert "    depth_bins = [[0, 20], [20, None]]\n" in script.read_text()

    # and OceanVal's own, if none are given
    script.write_text(Path("oceanval/data/chunk_start.pytemplate").read_text())
    oceanval._rewrite_notebook_source(str(script), None, None, False, True, True)
    assert "    depth_bins = [[0, 10], [10, 30], " in script.read_text()


@pytest.mark.parametrize("notebook", ["point_template.ipynb", "summary.ipynb"])
def test_the_depth_bins_are_not_written_into_the_notebooks(notebook):
    # but taken from depth_bins, which the notebooks start with
    text = Path(f"oceanval/data/{notebook}").read_text()

    assert "600-1000m" not in text
    assert "def bin_depth(" not in text
    assert "depth_levels" in text


@pytest.mark.parametrize("time_shift, said", [(-1, True), (0, False), (None, False)])
def test_the_gridded_matchup_procedure_says_when_the_model_times_were_shifted(tmp_path, time_shift, said):
    import os
    import pickle

    cells = json.loads(Path("oceanval/data/gridded_template.ipynb").read_text())["cells"]
    source = ["".join(cell["source"]) for cell in cells if "**Matchup procedure**" in "".join(cell["source"])][0]
    code = source[source.index("# the days the model's times"):source.index("md(f\"**Matchup procedure**")]
    matchup_dict = {"start": 2000, "end": 2000}
    if time_shift is not None:
        matchup_dict["time_shift"] = time_shift
    with open(tmp_path / "bar_matchup_dict.pkl", "wb") as f:
        pickle.dump(matchup_dict, f)

    found = {"ff": [str(tmp_path / "bar_temperature_surface.nc")], "source": "bar", "os": os, "pickle": pickle}
    exec(code, found)
    assert ("shifted back one day" in found["shifted"]) is said


def test_point_pages_say_when_the_model_times_were_shifted():
    template = Path("oceanval/data/chunk_point.pytemplate").read_text()

    assert 'time_shift = matchup_dict.get("time_shift", 0) or 0' in template
    assert "md_markdown(report_text(time_shift))" in template
