"""Figures of the gridded validation page that are not maps (the gap report's
options 3, 4, 8, 9 and 13), drawn with plotnine in the house style
(oceanval.plotnine_figures.theme_oceanval): each function here keeps its
arguments, works out what is drawn, and has plotnine_figures draw it.

They only draw: every value they are given (zonal means, profiles,
statistics) is worked out by nctoolkit in the page, from the matchups, and
the statistics they quote are oceanval.skill's, so they agree with the page's
tables. The one thing done here is the binning of a density scatter's values
for drawing. Each figure function returns the matplotlib figure plotnine drew.
"""

import math

import numpy as np

# the colours of the observations and the model in every line figure, and the
# grey of reference bands: the house style's (plotnine_figures)
from oceanval.plotnine_figures import BAND, INK, MODEL, MUTED, OBS  # noqa: F401


def mathtext_unit(unit):
    """A unit as the report's colour bars show it (as transects.unit_text does):
    oceanval.unit_display's mathtext."""
    from oceanval import unit_display

    return unit_display.mathtext(str(unit or ""))


def _trim(lat, *arrays):
    """lat and arrays (..., band) without the bands that are nan in all of them."""
    keep = np.zeros(lat.size, bool)
    for a in arrays:
        keep |= np.isfinite(np.asarray(a)).reshape(-1, lat.size).any(axis=0)
    if not keep.any():
        return lat, arrays
    first, last = np.where(keep)[0][[0, -1]]
    return lat[first : last + 1], tuple(np.asarray(a)[..., first : last + 1] for a in arrays)


# ---- Hovmöller diagrams (options 3 and 4) ----


def hovmoller(times, lat, obs, model, diff, label, log=False, monthly=True, title=None):
    """Latitude x time panels of the zonal means model and obs (time, band) and
    their difference diff, each worked out by nctoolkit (zonal_mean, and log10
    first for a log variable, so diff is then the log10 ratio). times are month
    numbers (monthly=True, a climatology) or decimal years. The colour scale of
    obs and model is shared, from the 2nd to 98th percentiles of what is drawn,
    and the difference's is symmetric about 0 (plotnine_figures.hovmoller)."""
    from oceanval import plotnine_figures as pf

    lat, (obs, model, diff) = _trim(np.asarray(lat, dtype="float64"), *(np.asarray(x, dtype="float64") for x in (obs, model, diff)))
    times = np.asarray(times, dtype="float64")
    plot = pf.hovmoller(times, lat, obs, model, diff, label, log=log, monthly=monthly, title=title)
    return pf.draw(plot, pf.hovmoller_size(monthly, times.size))


def decimal_years(times):
    """Decimal years of datetimes (numpy or cftime), at mid-month."""
    out = []
    for t in times:
        if hasattr(t, "year") and not isinstance(t, np.datetime64):
            out.append(t.year + (t.month - 0.5) / 12)
        else:
            d = np.datetime64(t, "M").astype(int)
            out.append(1970 + d // 12 + ((d % 12) + 0.5) / 12)
    return np.asarray(out, dtype="float64")


# ---- zonal-mean lines (option 13) ----


def zonal_lines(lat, obs, model, label, obs_low=None, obs_high=None, log=False, title=None):
    """The zonal-mean annual mean of the observations and the model against
    latitude, with the observations' interquartile range across longitude as a
    band. model may be a dict of name: line (compare(), one per simulation), each
    line (latitudes, values) if the simulation's grid is its own."""
    from oceanval import plotnine_figures as pf

    lat = np.asarray(lat, dtype="float64")
    models = model if isinstance(model, dict) else {"Model": model}
    # a simulation on a grid of its own gives (its latitudes, its line)
    lines = {name: (line if isinstance(line, tuple) else (lat, line)) for name, line in models.items()}
    colours = [MODEL] if len(models) == 1 else simulation_colours(len(models))
    palette = {"Observations": OBS, **dict(zip(lines, colours))}
    band = (lat, obs_low, obs_high) if obs_low is not None and obs_high is not None else None
    plot = pf.zonal_lines(lat, obs, lines, label, palette, band=band, log=log, title=title)
    return pf.draw(plot, (8, 3.9))


def simulation_colours(n):
    """n distinct colours for simulations, in order (compare())."""
    palette = ["#1f5fae", "#d03b3b", "#0ca30c", "#fab219", "#7b3fa0", "#1b9e9e", "#a65628", "#e7298a"]
    return [palette[k % len(palette)] for k in range(n)]


# ---- density scatter (option 9) ----


class Density:
    """A 2-D histogram of matched observed and modelled values, for drawing
    only (density_panels draws it, with plotnine): built a time step at a time (add) so a long matchup is never held
    whole. The values are already log10 for a log variable (nctoolkit's, as
    the page works them out). Every number on the figure (r, bias, RMSD, N and
    the least-squares line) comes from oceanval.skill's statistics, which
    nctoolkit works out from the same matchup."""

    BINS = 120

    def __init__(self, low, high, log=False):
        self.log = log
        self.edges = np.linspace(low, high, self.BINS + 1)
        self.counts = np.zeros((self.BINS, self.BINS))

    def add(self, model, obs):
        model = np.asarray(model, dtype="float64").ravel()
        obs = np.asarray(obs, dtype="float64").ravel()
        ok = np.isfinite(model) & np.isfinite(obs)
        if ok.any():
            counts, _, _ = np.histogram2d(obs[ok], model[ok], bins=[self.edges, self.edges])
            self.counts += counts

    def tiles(self, title):
        """The bins with a value in them, as a frame to draw: panel (title), x (the
        observed bin's centre), y (the modelled bin's centre) and count."""
        import pandas as pd

        centres = (self.edges[:-1] + self.edges[1:]) / 2
        obs, model = np.nonzero(self.counts)
        return pd.DataFrame({"panel": title, "x": centres[obs], "y": centres[model],
                             "count": self.counts[obs, model]})

    def max_count(self):
        return float(self.counts.max()) if self.counts.size else 0.0

    def to_dataset(self):
        """The bins as an xarray Dataset (edges and counts), to save with the
        report's figure code (oceanval.figure_code); from_dataset reads it back."""
        import xarray as xr

        return xr.Dataset(
            {
                "edges": ("edge", self.edges),
                "counts": (("observed_bin", "modelled_bin"), self.counts),
            },
            attrs={"log": int(bool(self.log))},
        )

    @classmethod
    def from_dataset(cls, ds):
        """The Density to_dataset saved."""
        density = cls.__new__(cls)
        density.log = bool(ds.attrs.get("log", 0))
        density.edges = np.asarray(ds["edges"].values, dtype="float64")
        density.counts = np.asarray(ds["counts"].values, dtype="float64")
        return density

    def figure(self, stats, label, title=None):
        """The figure of these bins alone (density_panels of one panel)."""
        return density_panels([(title, self, stats)], label)


def density_stats_text(stats):
    """The box on each density scatter: r, Bias, RMSD and N (oceanval.skill's
    statistics), or N alone if there are no values to correlate."""
    if stats["n"] > 0 and np.isfinite(stats["r"]):
        return f"r = {stats['r']:.2f}\nBias = {stats['bias']:.3g}\nRMSD = {stats['rmsd']:.3g}\nN = {stats['n']:,}"
    return f"N = {stats['n']:,}"


def density_panels(panels, label, ncols=None):
    """A figure of density scatters side by side, with the same axes and one
    colour scale: panels is [(title, Density, stats)], in order, e.g. all
    months and the annual means, or each month of the year (4 to a row). Drawn
    with plotnine, so the panels share their axes and the axis titles are given
    once. Each panel has the bins coloured by the number of values in them, the
    dashed 1:1 line, the red least-squares fit of model on observation (slope r
    times nsd, through the two means) and a box of r, Bias, RMSD and N from
    stats (oceanval.skill's, of the values drawn). The lines have no legend: the
    caption says what they are. Returns the matplotlib figure."""
    import pandas as pd
    from oceanval import plotnine_figures

    n = len(panels)
    ncols = ncols or min(n, 4)
    nrows = math.ceil(n / ncols)
    side = 4.6 if n <= 2 else 2.9
    fontsize = 8 if n <= 2 else 7
    titles = [title or "" for title, _, _ in panels]
    first = panels[0][1]
    low, high = first.edges[0], first.edges[-1]
    tiles = pd.concat([density.tiles(title) for title, (_, density, _) in zip(titles, panels)], ignore_index=True)
    fits = []
    for title, (_, density, stats) in zip(titles, panels):
        slope = stats["r"] * stats["nsd"]
        if np.isfinite(slope):
            fits.append({"panel": title, "slope": slope, "intercept": stats["model_mean"] - slope * stats["obs_mean"]})
    boxes = pd.DataFrame({"panel": titles, "text": [density_stats_text(stats) for _, _, stats in panels]})
    top = max([density.max_count() for _, density, _ in panels] + [2])
    prefix = "log10 " if first.log else ""
    plot = plotnine_figures.density_scatter(
        tiles, pd.DataFrame(fits, columns=["panel", "slope", "intercept"]), boxes, titles, (low, high), top,
        (first.edges[1] - first.edges[0]), f"Observed {prefix}{label}", f"Modelled {prefix}{label}", ncols,
        fontsize, (side * ncols + 1.2, side * nrows + 0.3),
    )
    # open in pyplot, so the page's plt.show() shows it (plotnine's draw closes it)
    return plotnine_figures.draw(plot, (side * ncols + 1.2, side * nrows + 0.3))


def value_limits(read, steps, log=False, q=(0.5, 99.5)):
    """The range of a density scatter's axes: the q percentiles of model and
    observation over a sample of time steps (read(k) gives the model and
    observation of step k)."""
    sample = []
    picks = sorted(set(np.linspace(0, steps - 1, min(steps, 12)).astype(int)))
    for k in picks:
        model, obs = read(k)
        model = np.asarray(model, dtype="float64").ravel()
        obs = np.asarray(obs, dtype="float64").ravel()
        values = np.concatenate([model, obs])
        values = values[np.isfinite(values)]
        if log:
            values = values[values > 0]
        sample.append(values)
    values = np.concatenate(sample) if sample else np.array([])
    if not values.size:
        return None
    low, high = np.percentile(values, q)
    if high <= low:
        high = low + (abs(low) or 1) * 0.1
        low = low - (abs(low) or 1) * 0.1
        if log:
            low = max(low, high / 100)
    return float(low), float(high)


# ---- mean profiles (option 8) ----


def profiles(depths, lines, label, title=None):
    """Mean profiles, depth downwards: lines is {panel: (obs, model)}, each an
    array over depths. depths are numbers (gridded levels) or labels (point
    depth ranges, evenly spaced)."""
    from oceanval import plotnine_figures as pf

    ncols = min(len(lines), 4)
    nrows = math.ceil(len(lines) / ncols)
    plot = pf.profiles(list(depths), lines, label, title=title)
    return pf.draw(plot, (2.6 * ncols + 0.8, 3.6 * nrows + 0.5))


# ---- totals over the matched cells (options 5 and 34) ----


def total_series(times, model, obs, label, references=(), monthly=True):
    """The total over the matched cells of each month, model and observations,
    with each published range (totals.references: low, high, citation, period)
    as a band. times are month numbers (a climatology) or decimal years."""
    from oceanval import plotnine_figures as pf

    plot = pf.total_series(times, model, obs, label, references=references, monthly=monthly)
    return pf.draw(plot, (8, 3.8))
