"""Taylor diagrams of compare() (the gap report's option 6): every simulation's
skill for every variable on one diagram, the observations' point at a
normalised standard deviation of 1 and a correlation of 1.

The layout follows the original Taylor-diagram prototype: a Cartesian sector (a quarter
circle, or a half circle when a correlation is negative) whose box has the
sector's own shape, standard-deviation arcs about the origin, centred-RMSD arcs
about the observations' point, correlation rays, and a numbered marker for each
variable with a table of them beside it. Here each simulation has a colour of
its own (gridded_plots.simulation_colours, in compare()'s order).

It only draws: the normalised standard deviations and correlations are
oceanval.skill's, which nctoolkit works out from the matchups.
"""

import math

import numpy as np

from oceanval.gridded_plots import simulation_colours

INK = "#0b0b0b"
MUTED = "#52514e"
GRID = "#c9c8c2"

# the table's row height and width, and the room round the diagram for its labels, in inches
ROW = 0.22
TABLE_W = 2.9
PAD_L, PAD_R, PAD_T, PAD_B = 0.40, 0.50, 0.30, 0.45


def sector_and_rmax(nsd, r, cap=2.0):
    """Whether the diagram needs a half circle (a negative correlation), and the
    largest normalised standard deviation it shows: at least 1.5, in steps of a
    quarter, and no more than cap (a point beyond it is pinned to the rim)."""
    nsd = np.asarray(nsd, dtype=float)
    r = np.asarray(r, dtype=float)
    ok = np.isfinite(nsd) & np.isfinite(r)
    if not ok.any():
        return False, 1.5
    half = bool(np.min(r[ok]) < 0)
    top = float(np.max(nsd[ok]))
    rmax = max(1.5, math.ceil(min(top * 1.08, cap) * 4) / 4)
    return half, min(rmax, cap)


def _frame(ax, half, rmax):
    """The sector: arcs, rays, the rim, and the observations' point."""
    th_max = np.pi if half else np.pi / 2
    xmin = -rmax if half else 0.0
    ax.set_xlim(xmin, rmax)
    ax.set_ylim(0, rmax)
    ax.set_aspect("equal")
    ax.axis("off")

    def arc(rho, a, b, **kw):
        t = np.linspace(a, b, 300)
        ax.plot(rho * np.cos(t), rho * np.sin(t), **kw)

    for k in np.arange(0.5, rmax + 1e-9, 0.5):
        arc(k, 0, th_max, color=GRID, lw=0.6)
        ax.text(k, -0.04 * rmax, f"{k:g}", ha="center", va="top", fontsize=8, color=MUTED)
    t = np.linspace(0, np.pi, 400)
    for d in (0.25, 0.5, 0.75, 1.0):
        x, y = 1 + d * np.cos(t), d * np.sin(t)
        inside = (np.hypot(x, y) <= rmax) & (y >= 0) & (x >= xmin)
        ax.plot(np.where(inside, x, np.nan), np.where(inside, y, np.nan), color=GRID, lw=0.8, ls=(0, (4, 3)))
    cors = [0, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 0.99] + ([-0.9, -0.8, -0.6, -0.4, -0.2] if half else [])
    for c in cors:
        th = math.acos(c)
        ax.plot([0, rmax * np.cos(th)], [0, rmax * np.sin(th)], color=GRID, lw=0.6)
        ax.text(1.06 * rmax * np.cos(th), 1.06 * rmax * np.sin(th), f"{c:g}", ha="center", va="center",
                fontsize=8, color=MUTED)
    ax.text((xmin + rmax) / 2, -0.14 * rmax, "Normalised standard deviation", ha="center", va="top",
            fontsize=9, color=MUTED, clip_on=False)
    a = th_max / 2
    ax.text(1.2 * rmax * np.cos(a), 1.2 * rmax * np.sin(a), "Correlation", ha="center", va="center",
            fontsize=9, color=MUTED, rotation=math.degrees(a) - 90, clip_on=False)
    arc(rmax, 0, th_max, color="black", lw=0.9)
    ax.plot([xmin, rmax], [0, 0], color="black", lw=0.9)
    ax.plot([1], [0], marker="*", color=INK, ms=14, ls="", zorder=5, clip_on=False)


def _spread(x, y, mind):
    """Points nudged apart until none is closer than mind to another, so each
    number can be read."""
    x, y = x.copy(), y.copy()
    for _ in range(60):
        for i in range(len(x)):
            for j in range(i + 1, len(x)):
                dx, dy = x[j] - x[i], y[j] - y[i]
                d = math.hypot(dx, dy)
                if d < mind:
                    if d < 1e-9:
                        dx, dy, d = 1e-3, 0, 1e-3
                    k = (mind - d) / 2 / d
                    x[i] -= dx * k
                    y[i] -= dy * k
                    x[j] += dx * k
                    y[j] += dy * k
    return x, np.maximum(y, 0.0)


def figure(frame, title=None):
    """The Taylor diagram of frame, a table with a row for each simulation and
    variable: simulation, variable (as the report names it), nsd and r. Each
    variable has a number, in its marker and in the table beside the diagram,
    and each simulation a colour. Returns the matplotlib figure."""
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    frame = frame[np.isfinite(frame.nsd) & np.isfinite(frame.r)].reset_index(drop=True)
    simulations = list(dict.fromkeys(frame.simulation))
    variables = list(dict.fromkeys(frame.variable))
    colours = dict(zip(simulations, simulation_colours(len(simulations))))
    numbers = {v: k + 1 for k, v in enumerate(variables)}
    half, rmax = sector_and_rmax(frame.nsd, frame.r)

    # with one simulation the table has no simulation rows: its colour needs no key
    one = len(simulations) == 1
    rows = len(variables) + 1 + (0 if one else 1 + len(simulations))
    table_h = rows * ROW
    if half:
        dw = max(4.6, 2 * (table_h - PAD_T - PAD_B))
        dh = dw / 2
    else:
        dw = max(3.4, table_h - PAD_T - PAD_B)
        dh = dw
    title_h = 0.3 if title else 0.0
    width = PAD_L + dw + PAD_R + 0.15 + TABLE_W
    height = title_h + PAD_T + dh + PAD_B
    fig = plt.figure(figsize=(width, height))
    ax = fig.add_axes([PAD_L / width, PAD_B / height, dw / width, dh / height])
    _frame(ax, half, rmax)

    rad = np.minimum(frame.nsd.values, rmax)
    th = np.arccos(np.clip(frame.r.values, -1, 1))
    x, y = _spread(rad * np.cos(th), rad * np.sin(th), 0.09 * rmax)
    for (_, row), xx, yy in zip(frame.iterrows(), x, y):
        colour = colours[row.simulation]
        pinned = row.nsd > rmax
        n = numbers[row.variable]
        ax.plot([xx], [yy], marker="o", ms=12, mfc=("white" if pinned else colour), mec=colour, mew=1.5,
                ls="", zorder=6, clip_on=False)
        ax.annotate(str(n), (xx, yy), ha="center", va="center", fontsize=7 if n < 10 else 6.5,
                    color=(colour if pinned else "white"), fontweight="bold", zorder=7, annotation_clip=False)

    # the table: the variables by number, then the simulations by colour
    body = PAD_T + dh + PAD_B
    y0 = max((body - table_h) / 2, 0)
    ta = fig.add_axes([(PAD_L + dw + PAD_R + 0.15) / width, y0 / height, TABLE_W / width, table_h / height])
    ta.axis("off")
    step = 1.0 / rows
    yy = 1 - step / 2
    ta.text(0.0, yy, "Variable", fontsize=8, fontweight="bold", color=INK, va="center", transform=ta.transAxes)
    for variable in variables:
        yy -= step
        ta.text(0.02, yy, str(numbers[variable]), fontsize=8, fontweight="bold", color=INK, va="center", ha="center",
                transform=ta.transAxes)
        ta.text(0.1, yy, variable, fontsize=8, color=INK, va="center", transform=ta.transAxes)
    if not one:
        yy -= step
        ta.text(0.0, yy, "Simulation", fontsize=8, fontweight="bold", color=INK, va="center", transform=ta.transAxes)
        for simulation in simulations:
            yy -= step
            ta.add_line(Line2D([0.02], [yy], marker="o", ms=8, color=colours[simulation], ls="", transform=ta.transAxes))
            ta.text(0.1, yy, simulation, fontsize=8, color=INK, va="center", transform=ta.transAxes)
    if title:
        fig.text(0.01, 1 - 0.05 / height, title, fontsize=10, fontweight="bold", va="top")
    return fig
