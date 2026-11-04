"""The figures of the simulation comparison report (see oceanval.sim_plots): each
is drawn by running its own code, the script its "Copy code to reproduce figure"
button copies, and that script draws the same figure in a Python of its own, a
colour for each simulation."""

import ast
import io
import os
import subprocess
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

import oceanval  # noqa: E402
from oceanval import analysis_plots as ap  # noqa: E402
from oceanval import analysis_report, sim_compare  # noqa: E402
from conftest import no_book  # noqa: E402
from oceanval import sim_plots as sp  # noqa: E402
from simulations import FVCOM_COMPARE_OPTIONS, TREND_A, TREND_B, write_analysis_regions, write_analysis_simulation  # noqa: E402
from viewer_simulations import fvcom_months  # noqa: E402

B_LATS = list(np.arange(49.5, 53.6, 0.5))
B_LONS = list(np.arange(-4.5, 0.6, 0.5))
SERIES = {"Temperature": {"start": 2012, "end": 2014, "surface": True, "bottom": True, "water_column": "mean"}}
CASES = ("daily", "grids", "series", "transects", "changes", "fvcom")


@pytest.fixture(scope="module")
def comparisons(tmp_path_factory):
    """A comparison with each kind of figure, without its report: {case: its results directory}."""
    root = tmp_path_factory.mktemp("sim_figures")
    daily = [str(root / f"daily_{name}") for name in "ab"]
    write_analysis_simulation(daily[0], years=(2013, 2014), trend=1.0)
    write_analysis_simulation(daily[1], years=(2013, 2014), trend=2.0, lats=B_LATS, lons=B_LONS)
    # two simulations on different grids, which are not put on one
    grids = [str(root / f"grids_{name}") for name in "ab"]
    write_analysis_simulation(grids[0], years=(2013, 2014), trend=1.0)
    write_analysis_simulation(grids[1], years=(2013, 2014), trend=2.0, lats=B_LATS, lons=B_LONS)
    monthly = [str(root / f"monthly_{name}") for name in "abc"]
    write_analysis_simulation(monthly[0], years=(2012, 2013, 2014), daily=False, trend=1.0, east_west=0.5)
    write_analysis_simulation(monthly[1], years=(2012, 2013, 2014), daily=False, trend=2.0, east_west=0.5)
    write_analysis_simulation(monthly[2], years=(2012, 2013, 2014), daily=False, trend=0.0, east_west=0.25)
    regions = str(root / "regions.nc")
    write_analysis_regions(regions)

    def sims(paths):
        return [{"name": name, "sim_dir": path, "n_dirs_down": 1} for name, path in zip(("Base run", "Warm run", "Cold run"), paths)]

    years = {"start": 2012, "end": 2014}
    periods = {"historical": {"start": 2012, "end": 2012}, "future": [{"start": 2013, "end": 2013}, {"start": 2014, "end": 2014}]}
    runs = {
        "daily": dict(
            simulations=sims(daily), unify={"baseline": "Base run"},
            variables={"Chlorophyll": "P1_Chl+P2_Chl", "Temperature": "thetao"},
            climatology={
                "Temperature": {"start": 2013, "end": 2014, "surface": True, "bottom": True, "water_column": "mean"},
                "Chlorophyll": {"start": 2013, "end": 2014, "surface": True},
            },
            phenology={"Chlorophyll": {"start": 2013, "end": 2014}}, thickness="e3t",
        ),
        "grids": dict(
            simulations=sims(grids), variables={"Temperature": "thetao"}, thickness="e3t",
            climatology={"Temperature": {"start": 2013, "end": 2014, "surface": True}},
        ),
        "series": dict(
            simulations=sims(monthly), variables={"Temperature": "thetao"}, long_term=SERIES, seasonal=SERIES,
            climatology={"Temperature": {"start": 2012, "end": 2014, "surface": True}},
            thickness="e3t", subregions=regions, stations={"Near": [-3.1, 51.2], "Far": [-0.2, 52.8]},
        ),
        "transects": dict(
            simulations=sims(monthly[:2]), variables={"Temperature": "thetao"}, thickness="e3t",
            unify={"lon": [-4, 0], "lat": [50, 53], "res": 1},
            transects={
                "Temperature": [
                    {"start": [-2.0, 50.0], "end": [-2.0, 53.0], "climatology": years},
                    {"start": [-4.0, 51.0], "end": [0.0, 51.0], "vertical": years},
                    {"start": [-4.0, 51.0], "end": [0.0, 51.0], "trends": dict(years, how="absolute")},
                    {"start": [-2.0, 50.0], "end": [-2.0, 53.0], "vertical_trends": dict(years, how="absolute_change")},
                ]
            },
        ),
        "changes": dict(
            simulations=sims(monthly), variables={"Temperature": "thetao"}, thickness="e3t",
            map_changes={"Temperature": dict(periods, plot="absolute_changes", change="percentage", surface=True)},
        ),
    }
    # raw FVCOM output on one mesh, with every analysis: its maps are drawn on the mesh, a panel for each simulation
    fvcom = [fvcom_months(str(root / f"fvcom_{name}"), trend=trend) for name, trend in (("a", TREND_A), ("b", TREND_B))]
    runs["fvcom"] = dict(simulations=sims(fvcom), **FVCOM_COMPARE_OPTIONS)
    found = {}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(analysis_report, "InterimBook", no_book([]))
        for case, arguments in runs.items():
            out = str(root / case)
            oceanval.compare_simulations(out_dir=out, cores=1, view=False, **arguments)
            found[case] = os.path.join(out, sim_compare.FOLDER, sim_compare.RESULTS)
    return found


def figures(info):
    """Each figure of a comparison's report: (the function, its arguments)."""
    sims = [sim["name"] for sim in info["simulations"]]
    for variable in info["variables"]:
        key = variable["key"]
        first = variable["sims"][sims[0]]
        if first["climatology"]:
            for layer in first["climatology"]["layers"]:
                yield sp.annual_maps, (key, layer)
                if sp.difference_pair(info, variable, layer):
                    # two simulations: one figure of the months, with the difference
                    yield sp.monthly_difference_maps, (key, layer)
                    continue
                for sim in sims:
                    yield sp.monthly_maps, (key, layer, sim)
        if first["map_changes"]:
            for layer in first["map_changes"]["layers"]:
                for sim in sims:
                    yield sp.change_maps, (key, layer, sim)
        if first["phenology"]:
            yield sp.peak_day_maps, (key,)
            yield sp.peak_value_maps, (key,)
            yield sp.daily_cycle, (key,)
        if first["transects"]:
            for line in first["transects"]["lines"]:
                yield sp.transect_map, (key, line["index"])
                for option in line["options"]:
                    yield sp.transect_figure, (key, line["index"], option)
        for kind in ("long_term", "seasonal"):
            if first[kind]:
                for layer in first[kind]["layers"]:
                    if kind == "long_term":
                        yield sp.long_term_plot, (key, layer)
                    else:
                        yield sp.seasonal_cycle_regions, (key, layer)
                    yield sp.station_plot, (key, kind, layer)


def png_now():
    buffer = io.BytesIO()
    plt.gcf().savefig(buffer, format="png", bbox_inches="tight")
    plt.close("all")
    return buffer.getvalue()


def pixels(data):
    return mpimg.imread(io.BytesIO(data), format="png")


RUN_EACH = """
import runpy, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
for path in sys.argv[1:]:
    runpy.run_path(path, run_name="__main__")
    plt.close("all")
"""


@pytest.mark.parametrize("case", CASES)
def test_each_figure_is_drawn_by_the_code_it_copies(comparisons, case, tmp_path):
    results = comparisons[case]
    info = sim_compare.load(results)
    drawn, shown, scripts = [], [], []
    for function, args in figures(info):
        figure = function(results, *args)
        if figure is None:
            assert function is sp.station_plot
            continue
        shown.append(png_now())
        drawn.append((function.__name__, args, figure))
        path = str(tmp_path / f"figure_{len(scripts)}.py")
        text = ap.script(figure, f"Figure {len(scripts) + 1} on the {case} page")
        assert text.endswith("plt.show()\n")
        with open(path, "w") as file:
            file.write(text.replace("plt.show()\n", f'plt.gcf().savefig({path[:-3] + ".png"!r}, bbox_inches="tight")\n'))
        scripts.append(path)
    assert drawn
    other = tmp_path / "elsewhere"
    other.mkdir()
    package = os.path.dirname(os.path.dirname(os.path.abspath(oceanval.__file__)))
    path = os.pathsep.join(x for x in (package, os.environ.get("PYTHONPATH")) if x)
    run = subprocess.run(
        [sys.executable, "-c", RUN_EACH, *scripts], cwd=str(other), capture_output=True, text=True,
        env=dict(os.environ, MPLBACKEND="Agg", PYTHONPATH=path),
    )
    assert run.returncode == 0, run.stderr[-3000:]
    for (name, args, figure), image, script in zip(drawn, shown, scripts):
        alone = open(script[:-3] + ".png", "rb").read()
        assert np.array_equal(pixels(alone), pixels(image)), f"{name}{args} is not drawn as its code draws it"
        ast.parse(figure.code)
        assert "../" not in figure.code
        # the maps of one simulation at a time are those of its analysis, on the scale all share
        if name in ("monthly_maps", "change_maps", "transect_map"):
            assert figure.about.startswith("the OceanVal analysis of ")
        else:
            assert figure.about.startswith("the OceanVal comparison of Base run, ")
        # the figures of every simulation put each in its colour
        if name in ("long_term_plot", "seasonal_cycle_regions", "station_plot", "daily_cycle"):
            for sim, colour in info["colours"].items():
                if sim in figure.code:
                    assert colour in figure.code


def test_every_simulation_has_its_own_colour(comparisons):
    info = sim_compare.load(comparisons["series"])
    assert list(info["colours"]) == ["Base run", "Warm run", "Cold run"]
    assert len(set(info["colours"].values())) == 3
    figure = sp.long_term_plot(comparisons["series"], "temperature", "surface")
    plt.close("all")
    for colour in info["colours"].values():
        assert colour in figure.code


def test_a_panel_for_the_domain_and_each_region(comparisons):
    results = comparisons["series"]
    figure = sp.long_term_plot(results, "temperature", "surface")
    plt.close("all")
    assert figure.found == {"regions": 2}
    assert 'panels = ["Full Domain", ' in figure.code
    stations = sp.station_plot(results, "temperature", "seasonal", "surface")
    assert stations.found == {"names": ["Near", "Far"]}
    assert not hasattr(sp, "seasonal_series_plot")
    assert 'cycle = frame.groupby(["simulation", "kind", "name", "month"]' in stations.code
    plt.close("all")
    cycle = sp.seasonal_cycle_regions(results, "temperature", "bottom")
    plt.close("all")
    assert set(cycle.found["means"]) == {"Base run", "Warm run", "Cold run"}
    assert sorted(cycle.found["means"]["Warm run"]) == list(range(1, 13))


def test_the_maps_of_the_simulations_share_a_scale(comparisons):
    # two simulations: both on one scale, and the differences on another
    results = comparisons["daily"]
    figure = sp.annual_maps(results, "temperature", "surface")
    plt.close("all")
    assert "the colour scale the simulations share" in figure.code
    for sim in ("Base run", "Warm run"):
        assert f'"{sim}"' in figure.code
    # every month of the figure of the months has the same one
    monthly = sp.monthly_difference_maps(results, "temperature", "surface")
    plt.close("all")
    assert "the colour scale the simulations share" in monthly.code
    # more: the maps of each simulation's months are on the one scale all share
    series = comparisons["series"]
    one = sp.monthly_maps(series, "temperature", "surface", "Base run").code
    other = sp.monthly_maps(series, "temperature", "surface", "Warm run").code
    plt.close("all")
    assert [line for line in one.splitlines() if line.startswith("limits = ")] == [
        line for line in other.splitlines() if line.startswith("limits = ")
    ]


def test_the_maps_of_a_pair_are_5_mm_apart(comparisons):
    results = comparisons["daily"]
    for function in (sp.annual_maps, sp.monthly_difference_maps):
        function(results, "temperature", "surface")
        fig = plt.gcf()
        fig.canvas.draw()
        width, _ = fig.get_size_inches()
        maps = [a.get_position() for a in fig.axes if a.get_label() != "<colorbar>" and a.get_position().width > 0.05]
        starts = sorted({round(box.x0 * width, 2) for box in maps})
        map_width = max(box.width for box in maps) * width
        plt.close("all")
        for left, right in zip(starts, starts[1:]):
            # a map that is a little narrower than the others (its cells with values differ) sits in the middle of its place
            assert -0.03 < (right - left - map_width) - sp.GAP < 0.1
    assert abs(sp.GAP * 25.4 - 5) < 0.1


def test_the_peaks_are_found_for_each_simulation(comparisons):
    cycle = sp.daily_cycle(comparisons["daily"], "chlorophyll")
    plt.close("all")
    assert set(cycle.found["peaks"]) == {"Base run", "Warm run"} == set(cycle.found["notes"])
    for peak in cycle.found["peaks"].values():
        assert 1 <= peak["day"] <= 366 and ap.day_text(peak["month_day"])


def parsed(code, name):
    """The value of the line `name = ...` of a script."""
    line = next(x for x in code.splitlines() if x.startswith(f"{name} = "))
    return ast.literal_eval(line[len(name) + 3:])


def test_two_simulations_are_drawn_with_their_difference(comparisons):
    results = comparisons["daily"]
    info = sim_compare.load(results)
    variable = sp.entry(info, "temperature")
    assert sp.difference_pair(info, variable, "surface") == ("Base run", "Warm run")
    assert sp.has_difference(results, "temperature", "surface")
    for figure in (sp.annual_maps(results, "temperature", "surface"), sp.monthly_difference_maps(results, "temperature", "surface")):
        plt.close("all")
        assert figure.found["pair"] == ["Base run", "Warm run"]
        assert "Base run minus Warm run" in figure.code
        assert 'ds_difference.subtract(ds_on_first)' in figure.code
        # the simulations are on one grid, so nothing is regridded for the difference
        assert figure.found["difference_regridded"] is False and ".regrid(" not in figure.code
        low, high = parsed(figure.code, "difference_limits")
        assert low == -high and high > 0
        assert parsed(figure.code, "limits")[0] < parsed(figure.code, "limits")[1]
    # the months are rows of the first simulation, the second and the difference, named to the right
    monthly = sp.monthly_difference_maps(results, "temperature", "surface")
    plt.close("all")
    assert parsed(monthly.code, "rows") == ["Base run", "Warm run", "Base run minus Warm run"]
    assert "rotation=270" in monthly.code


def test_the_difference_is_the_first_minus_the_second(comparisons):
    import xarray as xr

    results = comparisons["daily"]
    info = sim_compare.load(results)
    variable = sp.entry(info, "temperature")
    pair, paths = sp._pair_files(results, info, variable, "surface", "annual")
    values = sp._difference_values(paths, "temperature", False)
    with xr.open_dataset(paths[0]) as one, xr.open_dataset(paths[1]) as other:
        expected = np.asarray(one["temperature"].values, dtype="float64") - np.asarray(other["temperature"].values, dtype="float64")
    assert np.allclose(np.squeeze(values), np.squeeze(expected), equal_nan=True)
    assert np.nanmean(values) < 0  # the warm run is the second


def test_the_second_is_regridded_to_the_first_if_they_are_on_different_grids(comparisons):
    results = comparisons["grids"]
    info = sim_compare.load(results)
    assert info["same_grid"] is False
    for figure in (sp.annual_maps(results, "temperature", "surface"), sp.monthly_difference_maps(results, "temperature", "surface")):
        plt.close("all")
        assert figure.found["difference_regridded"] is True
        assert "ds_on_first.regrid(grid=" in figure.code
    assert "regridded" in oceanval.sim_report.difference_text(figure.found)


def test_three_simulations_are_drawn_as_before(comparisons):
    results = comparisons["series"]
    info = sim_compare.load(results)
    variable = sp.entry(info, "temperature")
    assert len(info["simulations"]) == 3
    assert sp.difference_pair(info, variable, "surface") is None
    figure = sp.annual_maps(results, "temperature", "surface")
    plt.close("all")
    assert figure.found == {} and "minus" not in figure.code
    assert sp.monthly_maps(results, "temperature", "surface", "Cold run").code
    plt.close("all")


def test_raw_fvcom_output_is_drawn_on_its_mesh_without_a_difference(comparisons):
    results = comparisons["fvcom"]
    info = sim_compare.load(results)
    assert not sp.has_difference(results, "temperature", "surface")
