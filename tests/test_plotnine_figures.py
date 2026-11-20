"""The report's plotnine figures (oceanval.plotnine_figures): each is drawn once,
from made-up data, the maps put the land under the points, and the figures that
are not maps share the house look and colours."""

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pandas as pd
import pytest

from oceanval import plotnine_figures as pf
from oceanval import unit_display

UNIT = unit_display.mathtext("mmol O2/m**3")


def _points(n=200, months=True):
    rng = np.random.default_rng(1)
    df = pd.DataFrame({"lon": rng.uniform(-12, 8, n), "lat": rng.uniform(47, 60, n)})
    if months:
        df["month"] = np.tile(np.arange(1, 13), n // 12 + 1)[:n]
    df["observation"] = 5 + rng.normal(0, 1, n)
    df["model"] = df.observation * 0.9 + rng.normal(0.3, 1, n)
    return df


def _regional():
    months = np.arange(1, 13)
    return pd.concat([
        pd.DataFrame({"month": months, "value": 5 + np.sin(months / 2) + k, "variable": variable, "long_name": region})
        for k, (variable, region) in enumerate(
            [(v, r) for v in ("model", "observation") for r in ("North Sea", "Full Domain", "Channel")]
        )
    ])


def _sections(regions=("Full domain", "Atlantic")):
    rows = [
        (lat, depth, variable, region, 20 - depth / 60 + lat / 10 + (np.sin(lat / 20) if variable == "Model" else 0))
        for region in regions
        for variable in ("Model", "Observation")
        for lat in np.arange(-60, 61, 10.0)
        for depth in (5, 10, 50, 200, 1000)
    ]
    return pd.DataFrame(rows, columns=["lat", "depth", "variable", "region", "value"])


DEPTHS = ["0-10m", "10-30m", "30-60m"]


def _depths(df):
    return df.assign(depth=np.resize(DEPTHS, len(df)))


FIGURES = {
    "locations_map": (lambda: pf.locations_map(_points()[["lon", "lat"]]), 600, 500),
    "mean_map": (lambda: pf.mean_map(_points(months=False), f"Sea Surface oxygen ({UNIT})"), 1000, 700),
    "bias_map": (lambda: pf.bias_map(*pf.capped_bias(_points()), f"Bias in oxygen ({UNIT})"), 1000, 1200),
    "scatter": (lambda: pf.scatter(_points(), f"Model oxygen ({UNIT})", f"Observed oxygen ({UNIT})"), 1000, 1200),
    "scatter_concise": (
        lambda: pf.scatter(_points(), "Model", "Observed", by_month=False, base_size=14), 600, 500,
    ),
    "depth_locations_map": (lambda: pf.depth_locations_map(_depths(_points()), DEPTHS), 1000, 800),
    "depth_matchup_map": (
        lambda: pf.depth_matchup_map(
            pd.concat([_depths(_points()).assign(variable=v) for v in ("Nitrate", "Oxygen")]).rename(
                columns={"depth": "depth_bin"}), DEPTHS,
        ),
        1000, 700,
    ),
    "regional_cycles": (lambda: pf.regional_cycles(_regional(), f"Spatial average oxygen ({UNIT})"), 1000, 700),
    "comparison_cycles": (lambda: pf.comparison_cycles(_regional(), f"Spatial average Oxygen ({UNIT})"), 1000, 700),
    "zonal_sections": (lambda: pf.zonal_sections(_sections(), "oxygen", UNIT), 1000, 1600),
    "zonal_lines": (lambda: pf.zonal_lines(*_zonal()), 800, 390),
    "hovmoller": (lambda: pf.hovmoller(*_hovmoller()), 1000, 640),
    "hovmoller_years": (lambda: pf.hovmoller(*_hovmoller(years=60), monthly=False), 1000, 950),
    "profiles": (lambda: pf.profiles(*_profiles()), 600, 410),
    "point_profiles": (lambda: pf.profiles(DEPTHS, {"All": ([5.0, 5.2, 5.1], [5.3, 5.0, 4.9])}, "oxygen"), 340, 410),
    "total_series": (
        lambda: pf.total_series(np.arange(1, 13), np.linspace(2, 3, 12), np.linspace(1.8, 2.9, 12), "flux",
                                [{"low": 1.7, "high": 2.9, "citation": "A range", "period": "2000-2010"}]),
        800, 380,
    ),
}


def _zonal():
    lat = np.arange(-60, 61, 5.0)
    obs = 5 + np.cos(np.radians(lat))
    lines = {"Model": (lat, obs * 1.1)}
    return lat, obs, lines, f"oxygen ({UNIT})", {"Observations": pf.OBS, "Model": pf.MODEL}, (lat, obs - 0.5, obs + 0.5)


def _hovmoller(years=0):
    lat = np.arange(-60, 61, 10.0)
    times = 2000 + (np.arange(years) + 0.5) / 12 if years else np.arange(1, 13)
    obs = np.cos(np.radians(lat))[None, :] + np.sin(np.asarray(times)[:, None])
    model = obs * 1.1
    return times, lat, obs, model, model - obs, f"oxygen ({UNIT})"


def _profiles():
    depths = [5.0, 50.0, 200.0, 1000.0]
    return depths, {"Full domain": ([5, 4, 3, 2.5], [5.2, 4.1, 3.4, 2.6]), "Tropics": ([6, 4, 2, 1], [6, 5, 3, 1])}, \
        f"oxygen ({UNIT})"


# the figures that are not maps: the house style (theme_oceanval) and its colours
LINE_FIGURES = ["regional_cycles", "zonal_lines", "profiles", "point_profiles", "total_series"]
HEAT_FIGURES = ["zonal_sections", "hovmoller", "hovmoller_years"]
VIRIDIS = ["#440154", "#21918c", "#fde725"]
RDBU_R = ["#053061", "#f6f7f7", "#67001f"]


def _plots(plot):
    return list(plot) if pf._is_composition(plot) else [plot]


@pytest.mark.parametrize("name", LINE_FIGURES + HEAT_FIGURES + ["density_scatter"])
def test_the_figures_that_are_not_maps_have_the_house_look(name):
    make = FIGURES[name][0] if name in FIGURES else _density
    for plot in _plots(make()):
        themeables = plot.theme.themeables
        # plain panel titles to the left, no grey strip boxes, a grey frame
        assert themeables["strip_background"].properties["visible"] is False
        assert themeables["panel_border"].properties["edgecolor"] == pf.BAND


def _density():
    tiles = pd.DataFrame({"panel": "All", "x": [1.0, 2.0], "y": [1.0, 2.0], "count": [3.0, 5.0]})
    fits = pd.DataFrame({"panel": ["All"], "slope": [1.0], "intercept": [0.0]})
    boxes = pd.DataFrame({"panel": ["All"], "text": ["N = 8"]})
    return pf.density_scatter(tiles, fits, boxes, ["All"], (0, 3), 5, 0.1, "Observed", "Modelled", 1, 8, (4, 4))


@pytest.mark.parametrize("name", LINE_FIGURES)
def test_the_model_is_red_and_the_observations_grey_on_every_line_figure(name):
    plot = FIGURES[name][0]()
    colours = plot.scales.get_scales("color").palette(2)
    assert colours["Model"] == pf.MODEL and colours["Observations"] == pf.OBS


@pytest.mark.parametrize("name", HEAT_FIGURES)
def test_every_heat_map_has_viridis_values_and_a_rdbu_bias_about_zero(name):
    for plot in _plots(FIGURES[name][0]()):
        fill = plot.scales.get_scales("fill")
        if set(plot.data.panel) == {"Bias"}:
            assert fill.palette([0.0, 0.5, 1.0]) == RDBU_R
            assert fill.limits[0] == -fill.limits[1]
        else:
            assert fill.palette([0.0, 0.5, 1.0]) == VIRIDIS
            assert set(plot.data.panel) <= {"Model", "Observation"}


def test_a_long_series_is_stacked_with_one_colour_bar_for_the_values():
    model, observation, bias = _plots(pf.hovmoller(*_hovmoller(years=60), monthly=False))
    assert set(model.data.panel) == {"Model"} and set(observation.data.panel) == {"Observation"}
    assert model.theme.themeables["legend_position"].properties["value"] == "none"
    assert model.scales.get_scales("fill").limits == observation.scales.get_scales("fill").limits


def test_a_drawn_figure_is_open_in_pyplot_so_the_page_shows_it(monkeypatch):
    # plotnine's draw() closes its figure; the page's plt.show() would then show nothing
    import matplotlib.pyplot as plt

    plt.close("all")
    figure = pf.draw(FIGURES["total_series"][0](), (8, 3.8))
    assert plt.get_fignums() == [figure.number]
    assert plt.gcf() is figure


def test_the_regions_of_the_zonal_sections_have_a_row_each():
    # one stack of every row, so each region is as tall as the next
    plots = _plots(pf.zonal_sections(_sections(("Full domain", "Atlantic", "Pacific")), "oxygen", UNIT))
    assert len(plots) == 6


@pytest.fixture(autouse=True)
def no_window(monkeypatch):
    import matplotlib.pyplot as plt

    monkeypatch.setattr(plt, "show", lambda *args, **kwargs: None)
    yield
    plt.close("all")


@pytest.mark.parametrize("name", FIGURES)
def test_each_figure_is_drawn_at_its_size(name):
    make, width, height = FIGURES[name]
    figure = pf.show(make(), width, height)
    assert tuple(figure.get_size_inches() * figure.dpi) == pytest.approx((width, height), abs=1)


def test_the_zonal_sections_colour_bars_are_within_the_figure():
    # the global set (five regions and the full domain), at the height the report gives it: the bars and their
    # titles, written across, are inside the figure, not off its right edge
    regions = ["Full domain", "Atlantic", "North Atlantic", "Pacific", "Indian", "Southern Ocean"]
    height = pf.zonal_height(len(regions) - 1)
    assert height == 2700
    figure = pf.show(pf.zonal_sections(_sections(regions), "nitrate concentration", UNIT), 1000, height)
    # drawn first, so the legends are where they are put (before, their boxes sit at 0, 0)
    figure.canvas.draw()
    box = figure.get_tightbbox(figure.canvas.get_renderer())
    assert box.x0 >= -1 / 72 and box.y0 >= -1 / 72
    assert box.x1 * 72 <= 1000 + 1 and box.y1 * 72 <= height + 1
    assert pf._bar_title("nitrate concentration", UNIT) == f"nitrate\nconcentration\n({UNIT})"
    assert pf._bar_title("Bias", "") == "Bias"


@pytest.mark.parametrize("name", ["locations_map", "mean_map", "bias_map", "depth_locations_map", "depth_matchup_map"])
def test_the_land_is_drawn_before_the_points(name):
    layers = [type(layer.geom).__name__ for layer in FIGURES[name][0]().layers]
    assert layers[0] == "geom_map"
    assert layers.index("geom_map") < layers.index("geom_point")


def test_small_areas_have_the_detailed_land():
    # a lake's points, 1° beyond them either way
    assert pf.land_scale((-84, 41, -78, 44)) == "10m"
    assert pf.land_scale((-20, 40, 32, 72)) == "10m"
    assert pf.land_scale((-80, 0, 20, 70)) == "50m"


def test_without_natural_earth_the_bundled_borders_are_used(monkeypatch):
    from cartopy.io import shapereader

    def offline(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(shapereader, "natural_earth", offline)
    pf._land.cache_clear()
    try:
        land = pf._land((-10.0, 49.0, 3.0, 60.0))
    finally:
        pf._land.cache_clear()
    assert land is not None and len(land) > 0


def test_the_bias_is_capped_at_the_98th_percentile():
    df = _points()
    capped, high = pf.capped_bias(df)
    assert high == pytest.approx(float(np.abs(df.model - df.observation).quantile(0.98)))
    assert capped.bias.abs().max() == pytest.approx(high)


def test_the_months_are_in_the_order_of_the_year():
    plot = pf.scatter(_points(), "x", "y")
    months = list(plot.data.month.cat.categories)
    assert months[0] == "All months" and months[1:] == ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul",
                                                         "Aug", "Sep", "Oct", "Nov", "Dec"]


def _measures():
    """Made-up casts of a stratification matchup: each measure, each month."""
    from oceanval import stratification

    rng = np.random.default_rng(3)
    rows = [
        {"measure": m, "month": month, "observation": obs, "model": obs + rng.normal()}
        for m in stratification.MEASURES
        for month in range(1, 13)
        for obs in rng.uniform(0, 10, 3)
    ]
    return pd.DataFrame(rows)


@pytest.mark.parametrize("make", ["stratification_cycle", "stratification_scatter"])
def test_the_stratification_figures_have_a_panel_for_each_measure_in_order(make):
    from oceanval import stratification

    df = _measures()
    plot = getattr(pf, make)(df)
    panels = list(plot.data.panel.cat.categories)
    assert len(panels) == len(stratification.MEASURES)
    assert [x.startswith(stratification.LABELS[m]) for x, m in zip(panels, stratification.MEASURES)] == [True] * 4
    figure = pf.show(plot, 1000, 800)
    assert len([ax for ax in figure.axes if ax.get_visible()]) >= 4
    # a measure that is missing has no panel
    assert getattr(pf, make)(df.query("measure != 'pea'")).data.panel.nunique() == 3
