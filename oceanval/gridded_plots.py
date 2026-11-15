"""Figures of the gridded validation page that are not maps (the gap report's
options 3, 4, 8, 9 and 13), drawn with matplotlib.

They only draw: every value they are given (zonal means, profiles,
statistics) is worked out by nctoolkit in the page, from the matchups, and
the statistics they quote are oceanval.skill's, so they agree with the page's
tables. The one thing done here is the binning of a density scatter's values
for drawing. Each figure function returns the matplotlib figure.
"""

import calendar
import math
import re

import numpy as np

# the colours of the observations and the model in every line figure, and the
# grey of reference bands (as in transects.py and the gap report)
OBS = "#3a3a3a"
MODEL = "#d03b3b"
BAND = "#c9c8c2"
INK = "#0b0b0b"
MUTED = "#52514e"
MONTHS = [calendar.month_abbr[m] for m in range(1, 13)]


def _plt():
    # imported when a figure is drawn, so the module can be imported without a display
    import matplotlib.pyplot as plt

    return plt


def mathtext_unit(unit):
    """A unit as the report's colour bars show it (as transects.unit_text does):
    tidied by fix_unit, its superscripts and subscripts in mathtext."""
    from oceanval.tidiers import fix_unit

    unit = fix_unit(str(unit or ""))
    unit = re.sub(r"<sup>(.*?)</sup>", r"$^{\1}$", unit)
    return re.sub(r"<sub>(.*?)</sub>", r"$_{\1}$", unit)


def lat_label(value):
    """A latitude as a label: 20°N, 20°S, 0°."""
    if abs(value) < 1e-9:
        return "0°"
    return f"{abs(value):g}°{'N' if value > 0 else 'S'}"


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
    """Latitude x time panels of the zonal means obs and model (time, band) and
    their difference diff, each worked out by nctoolkit (zonal_mean, and log10
    first for a log variable, so diff is then the log10 ratio). times are month
    numbers (monthly=True, a climatology) or decimal years. The colour scale of
    obs and model is shared, from the 2nd to 98th percentiles of what is drawn."""
    plt = _plt()
    lat, (obs, model, diff) = _trim(np.asarray(lat, dtype="float64"), *(np.asarray(x, dtype="float64") for x in (obs, model, diff)))
    shown = np.concatenate([obs[np.isfinite(obs)], model[np.isfinite(model)]])
    low, high = (np.percentile(shown, [2, 98]) if shown.size else (0, 1))
    if high <= low:
        high = low + 1
    span = np.nanpercentile(np.abs(diff), 98) if np.isfinite(diff).any() else 1
    span = span if span > 0 else 1
    times = np.asarray(times, dtype="float64")
    wide = (not monthly) and times.size > 36
    ncols = 1 if wide else 3
    nrows = 3 if wide else 1
    fig, axes = plt.subplots(nrows, ncols, figsize=(10, 7.5) if wide else (11, 3.6), constrained_layout=True, squeeze=False)
    axes = axes.ravel()
    value_label = f"log10 {label}" if log else label
    diff_label = "log10(model / observation)" if log else "Model - observation"
    for ax, field, name, cmap, limits, cbar in (
        (axes[0], obs, "Observation", "viridis", (low, high), value_label),
        (axes[1], model, "Model", "viridis", (low, high), value_label),
        (axes[2], diff, "Difference", "RdBu_r", (-span, span), diff_label),
    ):
        mesh = ax.pcolormesh(times, lat, np.ma.masked_invalid(field.T), cmap=cmap, vmin=limits[0], vmax=limits[1], shading="nearest")
        ax.set_title(name, loc="left", fontsize=10)
        ticks = ax.get_yticks()
        ax.set_yticks([t for t in ticks if lat.min() <= t <= lat.max()])
        ax.set_yticklabels([lat_label(t) for t in ax.get_yticks()])
        if monthly:
            ax.set_xticks([1, 4, 7, 10])
            ax.set_xticklabels(["Jan", "Apr", "Jul", "Oct"])
        else:
            # a tick at the start of each year (every few, for a long matchup), as a whole year
            # the cells reach half a month either side of their mid-month times
            first, last = math.ceil(times.min() - 1 / 24 - 1e-9), math.floor(times.max() + 1 / 24 + 1e-9)
            years = list(range(first, last + 1)) or [round(times.min())]
            step = max(1, math.ceil(len(years) / 8))
            ax.set_xticks(years[::step])
            ax.set_xticklabels([str(y) for y in years[::step]])
            ax.set_xlabel("Year")
        colourbar = fig.colorbar(mesh, ax=ax, shrink=0.9)
        colourbar.set_label(cbar, fontsize=8)
    if title:
        fig.suptitle(title, fontsize=10, x=0.01, ha="left")
    return fig


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
    plt = _plt()
    lat = np.asarray(lat, dtype="float64")
    models = model if isinstance(model, dict) else {"Model": model}
    fig, ax = plt.subplots(figsize=(8, 3.6), constrained_layout=True)
    if obs_low is not None and obs_high is not None:
        ok = np.isfinite(obs_low) & np.isfinite(obs_high)
        ax.fill_between(lat[ok], np.asarray(obs_low)[ok], np.asarray(obs_high)[ok], color=BAND, lw=0,
                        label="Observations, interquartile range across longitude")
    ok = np.isfinite(obs)
    ax.plot(lat[ok], np.asarray(obs)[ok], color=OBS, lw=1.6, label="Observations")
    colours = [MODEL] if len(models) == 1 else simulation_colours(len(models))
    for (name, line), colour in zip(models.items(), colours):
        # a simulation on a grid of its own gives (its latitudes, its line)
        line_lat, line = line if isinstance(line, tuple) else (lat, line)
        line_lat, line = np.asarray(line_lat, dtype="float64"), np.asarray(line, dtype="float64")
        ok = np.isfinite(line)
        ax.plot(line_lat[ok], line[ok], color=colour, lw=1.6, label=name)
    if log:
        ax.set_yscale("log")
    ax.set_xlabel("Latitude")
    ax.set_ylabel(label)
    ticks = [t for t in ax.get_xticks() if lat.min() - 1e-9 <= t <= lat.max() + 1e-9]
    ax.set_xticks(ticks)
    ax.set_xticklabels([lat_label(t) for t in ticks])
    ax.grid(axis="y", color=BAND, lw=0.5)
    ax.legend(fontsize=7, frameon=False)
    if title:
        ax.set_title(title, loc="left", fontsize=10)
    return fig


def simulation_colours(n):
    """n distinct colours for simulations, in order (compare())."""
    palette = ["#1f5fae", "#d03b3b", "#0ca30c", "#fab219", "#7b3fa0", "#1b9e9e", "#a65628", "#e7298a"]
    return [palette[k % len(palette)] for k in range(n)]


# ---- density scatter (option 9) ----


class Density:
    """A 2-D histogram of matched observed and modelled values, for drawing
    only: built a time step at a time (add) so a long matchup is never held
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

    def figure(self, stats, label, title=None):
        """The figure, with stats (oceanval.skill's, of the values drawn): the
        line is the least-squares fit of model on observation, slope r times
        nsd through the two means."""
        plt = _plt()
        from matplotlib.colors import LogNorm

        fig, ax = plt.subplots(figsize=(5.4, 4.8), constrained_layout=True)
        counts = np.ma.masked_equal(self.counts.T, 0)
        mesh = ax.pcolormesh(self.edges, self.edges, counts, cmap="viridis",
                             norm=LogNorm(vmin=1, vmax=max(2, counts.max() if counts.count() else 2)))
        low, high = self.edges[0], self.edges[-1]
        ax.plot([low, high], [low, high], color=INK, lw=1, ls=(0, (4, 3)), label="1:1")
        slope = stats["r"] * stats["nsd"]
        if np.isfinite(slope):
            intercept = stats["model_mean"] - slope * stats["obs_mean"]
            ax.plot([low, high], [intercept + slope * low, intercept + slope * high],
                    color=MODEL, lw=1.4, label=f"Least squares: slope {slope:.2f}")
        ax.set_xlim(low, high)
        ax.set_ylim(low, high)
        ax.set_aspect("equal")
        prefix = "log10 " if self.log else ""
        ax.set_xlabel(f"Observed {prefix}{label}")
        ax.set_ylabel(f"Modelled {prefix}{label}")
        text = f"r = {stats['r']:.2f}\nbias = {stats['bias']:.3g}\nRMSD = {stats['rmsd']:.3g}\nN = {stats['n']:,}"
        ax.text(0.03, 0.97, text, transform=ax.transAxes, va="top", fontsize=8, color=INK,
                bbox={"facecolor": "white", "edgecolor": BAND, "boxstyle": "round,pad=0.3"})
        ax.legend(loc="lower right", fontsize=7, frameon=False)
        colourbar = fig.colorbar(mesh, ax=ax, shrink=0.85)
        colourbar.set_label("Number of matched values", fontsize=8)
        if title:
            ax.set_title(title, loc="left", fontsize=10)
        return fig


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
    plt = _plt()
    panels = list(lines)
    n = len(panels)
    ncols = min(n, 4)
    nrows = math.ceil(n / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(2.6 * ncols + 0.6, 3.6 * nrows), constrained_layout=True,
                             squeeze=False, sharey=True)
    labels = isinstance(depths[0], str) if len(depths) else False
    y = np.arange(len(depths)) if labels else np.asarray(depths, dtype="float64")
    for ax, name in zip(axes.ravel(), panels):
        obs, model = (np.asarray(x, dtype="float64") for x in lines[name])
        for values, colour, what in ((obs, OBS, "Observations"), (model, MODEL, "Model")):
            ok = np.isfinite(values)
            ax.plot(values[ok], y[ok], color=colour, lw=1.6, marker="o" if labels else None, ms=3, label=what)
        ax.set_title(name, loc="left", fontsize=9)
        ax.grid(color=BAND, lw=0.5)
        ax.set_xlabel(label, fontsize=8)
    for ax in axes.ravel()[n:]:
        ax.set_visible(False)
    first = axes.ravel()[0]
    if labels:
        first.set_yticks(y)
        first.set_yticklabels(depths)
    first.invert_yaxis()
    first.set_ylabel("Depth" if labels else "Depth (m)")
    first.legend(fontsize=7, frameon=False)
    if title:
        fig.suptitle(title, fontsize=10, x=0.01, ha="left")
    return fig
