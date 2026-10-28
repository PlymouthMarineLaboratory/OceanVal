import json
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

    periods = dict()
    exec(code, {"global_grid": False}, periods)
    assert [list(x) for x in periods["seasonal_periods"]] == [[1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12]]


def test_the_maps_are_drawn_by_panel_plot_alone():
    # nctoolkit is pinned to a version with panel_plot, so there is no other way to draw them
    for path in Path("oceanval/data").iterdir():
        if path.suffix in (".ipynb", ".pytemplate"):
            text = path.read_text()
            for flag in ("use_panel_plot", "region_panel_plot", "summary_panel_plot", "seasonal_panel"):
                assert flag not in text, (path.name, flag)


def test_regional_time_series_r_cell_loads_the_tidyverse_itself():
    # no R cell before it loads it
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


def _r_cells_with_a_world_map():
    """The source of every R cell of the point pages that draws the world map, as
    (name, R code)"""
    cells = []
    template = Path("oceanval/data/chunk_point.pytemplate").read_text()
    for number, part in enumerate(template.split("# %%R ")[1:]):
        if "world_map" in part:
            cells.append((f"chunk_point {number}", part.split("\n# %%")[0]))
    for notebook in ("point_template.ipynb", "summary.ipynb"):
        for cell in json.loads(Path(f"oceanval/data/{notebook}").read_text())["cells"]:
            source = "".join(cell["source"])
            if "world_map" in source:
                cells.append((notebook, source))
    return cells


def test_every_map_of_the_point_pages_is_found():
    # chunk_point's three, the matchup locations of the point page and the summary's
    assert len(_r_cells_with_a_world_map()) == 5


@pytest.mark.parametrize("name, code", _r_cells_with_a_world_map())
def test_the_maps_of_small_areas_have_more_detail(name, code):
    # lon/lat ranges of more than 50 degrees use the world map, as they always have
    assert "diff(range(" in code
    assert "> 50" in code
    assert 'map_data("world")' in code
    # smaller ones, lakes for example, use the more detailed one, without the
    # lakes, which are water
    assert 'map_data("worldHires"' in code
    assert 'region != "Great Lakes"' in code
    # and without r-mapdata the world map is used
    assert 'requireNamespace("mapdata"' in code


@pytest.mark.parametrize("name, code", _r_cells_with_a_world_map())
def test_the_land_is_drawn_before_the_points(name, code):
    # layers are drawn in the order they are added. The summary's breaks are those
    # of its points, so it puts the land first once they are worked out
    if "gg$layers <- c(\n    list(geom_polygon(data = world_map" in code:
        assert code.count("geom_polygon(data = world_map") == 1
        return
    after_the_map = code[code.index("geom_polygon(data = world_map"):]
    assert "geom_point" not in code[:code.index("geom_polygon(data = world_map")].split("ggplot()")[-1]
    assert "geom_point" in after_the_map
    assert code.count("geom_polygon(data = world_map") == 1


def test_the_detailed_map_is_the_same_in_every_cell():
    import re

    blocks = set()
    for name, code in _r_cells_with_a_world_map():
        block = code[code.index("# a small area"):]
        block = block[:block.index("\n}\n") + 3]
        # only the data frame it is for differs
        blocks.add(re.sub(r"\b(df_locs|df_mapped|df_map|df)\b", "DF", block))
    assert len(blocks) == 1
