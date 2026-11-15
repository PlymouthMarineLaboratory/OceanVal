"""The figures of compare() from the gap report: the skill heat map of the
simulations against the variables (option 12). The Taylor diagram is
oceanval.taylor's and the zonal lines gridded_plots.zonal_lines'.

They only draw: every number is oceanval.skill's, which nctoolkit works out from
the matchups on the mask the simulations share.
"""

import numpy as np

INK = "#0b0b0b"
MUTED = "#52514e"

# the heat map's panels: the column of the table, its title, and whether a
# smaller value is better (r: a larger one)
PANELS = (
    ("bias_n", "|Bias| / σ$_{obs}$", True),
    ("crmsd_n", "Centred RMSD / σ$_{obs}$", True),
    ("r", "Correlation (r)", False),
)


def ranks(values, smaller_better=True):
    """The rank of each value, 1 for the best, the same for a tie; nan where it is nan."""
    values = np.asarray(values, dtype=float)
    out = np.full(values.shape, np.nan)
    ok = np.isfinite(values)
    if not ok.any():
        return out
    key = values[ok] if smaller_better else -values[ok]
    order = np.sort(np.unique(key))
    out[ok] = np.searchsorted(order, key) + 1
    return out


def heatmap(frame):
    """The skill heat map of frame, a table with a row for each simulation and
    variable: simulation, variable, bias_n (|bias| over the observations'
    standard deviation), crmsd_n (the centred RMSD over it) and r. Each cell is
    coloured by its simulation's rank for that variable (dark is best) and shows
    its value. Returns the matplotlib figure."""
    import matplotlib.pyplot as plt

    simulations = list(dict.fromkeys(frame.simulation))
    variables = list(dict.fromkeys(frame.variable))
    n_sim, n_var = len(simulations), len(variables)
    width = min(max(2.2 + 0.9 * n_var, 5.0), 16.0)
    height = 0.8 + 0.42 * n_sim
    fig, axes = plt.subplots(len(PANELS), 1, figsize=(width, height * len(PANELS) + 0.4), layout="constrained")
    cmap = plt.get_cmap("Blues_r")
    for ax, (column, title, smaller_better) in zip(np.atleast_1d(axes), PANELS):
        values = np.full((n_sim, n_var), np.nan)
        for _, row in frame.iterrows():
            values[simulations.index(row.simulation), variables.index(row.variable)] = row[column]
        shown = np.abs(values) if column == "bias_n" else values
        rank = np.full(values.shape, np.nan)
        for j in range(n_var):
            rank[:, j] = ranks(shown[:, j], smaller_better)
        # rank 1 is darkest, the worst lightest, whatever the number of simulations
        scaled = (rank - 1) / max(n_sim - 1, 1) * 0.75
        ax.imshow(np.ma.masked_invalid(scaled), cmap=cmap, vmin=0, vmax=1, aspect="auto")
        for i in range(n_sim):
            for j in range(n_var):
                if np.isfinite(shown[i, j]):
                    dark = scaled[i, j] < 0.35
                    ax.text(j, i, f"{shown[i, j]:.2f}", ha="center", va="center", fontsize=8,
                            color="white" if dark else INK, fontweight="bold" if rank[i, j] == 1 else "normal")
        ax.set_xticks(range(n_var))
        ax.set_xticklabels(variables, fontsize=8, rotation=30 if n_var > 4 else 0, ha="right" if n_var > 4 else "center")
        ax.set_yticks(range(n_sim))
        ax.set_yticklabels(simulations, fontsize=8)
        ax.set_title(title, loc="left", fontsize=9, color=INK)
        ax.tick_params(length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
    return fig
