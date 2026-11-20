"""The gridded page's density scatters are drawn with plotnine: the panels share
their axes, each axis title is given once, and the box says Bias."""

import calendar

import numpy as np
import pytest

from oceanval import gridded_plots as g

LABEL = "nitrate concentration (mmol N m$^{-3}$)"


def _stats(model, obs):
    return {
        "n": len(model), "r": np.corrcoef(model, obs)[0, 1], "bias": float(np.mean(model - obs)),
        "rmsd": float(np.sqrt(np.mean((model - obs) ** 2))), "nsd": model.std() / obs.std(),
        "model_mean": model.mean(), "obs_mean": obs.mean(),
    }


def _panel(title, seed=0, n=500):
    rng = np.random.default_rng(seed)
    obs = rng.gamma(4, 2.5, n)
    model = obs * 1.1 + rng.normal(0, 2, n)
    density = g.Density(0, 35)
    density.add(model, obs)
    return title, density, _stats(model, obs)


@pytest.fixture(autouse=True)
def close_figures():
    yield
    import matplotlib.pyplot as plt

    plt.close("all")


def _texts(fig):
    return [t.get_text() for t in fig.findobj(lambda a: hasattr(a, "get_text")) if t.get_text()]


def test_the_twelve_months_share_their_axis_titles():
    from matplotlib.figure import Figure

    fig = g.density_panels([_panel(calendar.month_name[m], m) for m in range(1, 13)], LABEL, ncols=4)
    assert isinstance(fig, Figure)
    texts = _texts(fig)
    assert texts.count("Observed " + LABEL) == 1
    assert texts.count("Modelled " + LABEL) == 1
    assert sum(t.startswith("r = ") for t in texts) == 12
    assert all(calendar.month_name[m] in texts for m in range(1, 13))


def test_the_box_says_bias_with_a_capital():
    texts = _texts(g.density_panels([_panel("All months"), _panel("Annual means", 1)], LABEL))
    boxes = [t for t in texts if t.startswith("r = ")]
    assert len(boxes) == 2
    assert all("\nBias = " in t and "bias" not in t for t in boxes)
    assert all("\nRMSD = " in t and "\nN = " in t for t in boxes)


def test_a_panel_without_values_shows_only_n():
    empty = g.Density(0, 35)
    stats = {"n": 0, "r": np.nan, "bias": np.nan, "rmsd": np.nan, "nsd": np.nan, "model_mean": np.nan, "obs_mean": np.nan}
    texts = _texts(g.density_panels([_panel("January"), ("February", empty, stats)], LABEL))
    assert "N = 0" in texts


def test_a_log_variable_is_labelled_log10():
    density = g.Density(0, 3, log=True)
    density.add(np.array([1.0, 2.0, 2.5]), np.array([1.1, 1.9, 2.4]))
    texts = _texts(density.figure(_stats(np.array([1.0, 2.0, 2.5]), np.array([1.1, 1.9, 2.4])), "chlorophyll"))
    assert "Observed log10 chlorophyll" in texts


def test_the_one_to_one_and_fit_lines_are_drawn_in_every_panel():
    from matplotlib.collections import LineCollection
    from matplotlib.colors import to_hex

    fig = g.density_panels([_panel("a"), _panel("b", 1)], LABEL)
    panels = [ax for ax in fig.axes if any(isinstance(c, LineCollection) for c in ax.collections)]
    assert len(panels) == 2
    for ax in panels:
        colours = {to_hex(c.get_colors()[0]) for c in ax.collections if isinstance(c, LineCollection)}
        assert {g.INK, g.MODEL} <= colours
