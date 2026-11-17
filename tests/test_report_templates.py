from types import SimpleNamespace
import json
import re
from pathlib import Path

import pytest


def test_gridded_climatology_template_displays_figure_once():
    template = Path("oceanval/data/chunk_clim.pytemplate").read_text()

    assert '\nfig\n' not in template


def test_seasonal_template_shows_global_maps_four_months_at_a_time():
    template = Path("oceanval/data/chunk_seasonal.pytemplate").read_text()
    code = template[template.index("if global_grid:\n    seasonal_periods"):]
    code = code[:code.index("\n\n")]

    periods = dict()
    exec(code, {"global_grid": True}, periods)
    assert [list(x) for x in periods["seasonal_periods"]] == [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]]

    # regional maps: half of the months available in each figure, one figure for 5 or fewer
    for months, expected in [
        (range(1, 13), [[1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12]]),
        (range(5, 13), [[5, 6, 7, 8], [9, 10, 11, 12]]),
        (range(1, 8), [[1, 2, 3, 4], [5, 6, 7]]),
        (range(6, 12), [[6, 7, 8], [9, 10, 11]]),
        (range(8, 13), [[8, 9, 10, 11, 12]]),
        ([3], [[3]]),
    ]:
        periods = dict()
        exec(code, {"global_grid": False, "df_model": SimpleNamespace(month=list(months) * 2)}, periods)
        assert [list(x) for x in periods["seasonal_periods"]] == expected


def test_the_maps_are_drawn_by_panel_plot_alone():
    # nctoolkit is pinned to a version with panel_plot, so there is no other way to draw them
    for path in Path("oceanval/data").iterdir():
        if path.suffix in (".ipynb", ".pytemplate"):
            text = path.read_text()
            for flag in ("use_panel_plot", "region_panel_plot", "summary_panel_plot", "seasonal_panel"):
                assert flag not in text, (path.name, flag)


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


def _templates():
    for path in sorted(Path("oceanval/data").iterdir()):
        if path.suffix in (".ipynb", ".pytemplate"):
            yield path.name, path.read_text()


@pytest.mark.parametrize("name, text", list(_templates()))
def test_no_report_page_needs_r(name, text):
    # every figure is drawn in Python: plotnine where ggplot2 drew it
    for word in ("%%R", "rpy2", "library(", "run_cell_magic", "restrict_r_to_conda"):
        assert word not in text, (name, word)


def test_every_map_of_the_point_pages_is_drawn_by_plotnine():
    # chunk_point's three, the matchup locations of the point page and the summary's
    point = Path("oceanval/data/chunk_point.pytemplate").read_text()
    for name in ("locations_map", "mean_map", "bias_map"):
        assert f"plotnine_figures.{name}(" in point
    assert "plotnine_figures.depth_locations_map(" in Path("oceanval/data/point_template.ipynb").read_text()
    assert "plotnine_figures.depth_matchup_map(" in Path("oceanval/data/summary.ipynb").read_text()


def test_the_gridded_notebook_makes_annual_means_from_the_monthly_climatology():
    cells = ["".join(cell["source"]) for cell in json.loads(Path("oceanval/data/gridded_template.ipynb").read_text())["cells"]]
    code = "\n".join(cells)
    # every month matched up is used: a year with months missing is not dropped,
    # as the annual means are those of the monthly climatology
    assert "keep_complete_years" not in code
    assert "ds_verticals.tmean(\"month\")\n    ds_verticals.tmean()" in code
    assert "annual_note = " in code
    # every caption about annual means says which months they cover, when not all
    chunks = code + Path("oceanval/data/chunk_clim.pytemplate").read_text() + Path("oceanval/data/chunk_bias.pytemplate").read_text()
    assert chunks.count("{annual_note}") == 5


def test_the_gridded_seasonal_sections_need_only_two_months():
    cells = ["".join(cell["source"]) for cell in json.loads(Path("oceanval/data/gridded_template.ipynb").read_text())["cells"]]
    code = "\n".join(cells)
    # (and the user has not left its figures out: validate's omit)
    assert "if n_times > 1 and seasonal_wanted:\n    md(f\"## Can the model reproduce seasonality" in code
    assert "if n_times < 2:\n    regional = False" in code
    assert "n_times < 12" not in code
    # the captions say which months there are, when not all 12
    seasonal = Path("oceanval/data/chunk_seasonal.pytemplate").read_text()
    assert seasonal.count("{months_note}") == 2
    assert seasonal.count("{cor_note}") == 1
    assert "The spatial average is taken over the region.{months_note}" in code


def _map_calls(code):
    """Each nctoolkit map call in code, with the code that follows it."""
    return [code[m.start():] for m in re.finditer(r"\b(pub_plot|panel_plot)\(", code)]


# the validate notebook's own maps
LAND_ASKED_FOR = {"chunk_bias.pytemplate", "chunk_seasonal.pytemplate", "chunk_clim.pytemplate", "summary.ipynb", "gridded_template.ipynb"}


@pytest.mark.parametrize("path", sorted(
    x for x in Path("oceanval/data").iterdir()
    if x.suffix in (".ipynb", ".pytemplate") and re.search(r"\b(pub_plot|panel_plot)\(", x.read_text())
), ids=lambda x: x.name)
def test_every_map_puts_the_land_beneath_the_data(path):
    # nctoolkit draws Natural Earth's land, lakes too, over the data: Lake Erie came out all land
    if path.suffix == ".ipynb":
        codes = ["".join(cell["source"]) for cell in json.loads(path.read_text())["cells"] if cell["cell_type"] == "code"]
    else:
        codes = [path.read_text()]
    calls = [call for code in codes for call in _map_calls(code)]
    assert calls
    for call in calls:
        # the call's closing bracket, then the next line
        depth = 0
        for end, character in enumerate(call):
            depth += {"(": 1, ")": -1}.get(character, 0)
            if character == ")" and depth == 0:
                break
        assert call[end + 1:].lstrip().startswith("land_below("), (path.name, call[:80])
        # nctoolkit draws no land of its own unless asked, and land_below leaves such a map alone
        if path.name in LAND_ASKED_FOR:
            assert "land =" in call[:end], (path.name, call[:80])


def test_land_below_is_imported_where_the_notebooks_start():
    assert "from oceanval.map_land import land_below\n" in Path("oceanval/data/chunk_start.pytemplate").read_text()


def test_the_notebooks_use_the_inline_backend_whatever_the_ipython_profile_says():
    # a profile with c.InteractiveShellApp.matplotlib = "tk" left the report's figures unshown (Agg)
    assert "\n%matplotlib inline\n" in Path("oceanval/data/chunk_start.pytemplate").read_text()
