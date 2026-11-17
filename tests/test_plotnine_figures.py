"""The report's plotnine figures (oceanval.plotnine_figures): each is drawn once,
from made-up data, and the maps put the land under the points."""

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


def _sections():
    rows = [
        (lat, depth, variable, region, 20 - depth / 60 + lat / 10 + (np.sin(lat / 20) if variable == "Model" else 0))
        for region in ("Full domain", "Atlantic")
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
}


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
