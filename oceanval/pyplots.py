"""
Pure-Python (matplotlib) equivalents of the R/ggplot2 plots used in the
oceanval report notebooks. These are used when the user selects
``plots="Python"`` in :func:`oceanval.validate`, instead of the R/rpy2
plotting cells.
"""
import re
import importlib.resources

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from oceanval.depths import DEFAULT_DEPTH_BINS, depth_labels

_WORLD_MAP = None


def _strip_html(text):
    if text is None:
        return ""
    from oceanval.tidiers import celsius

    # degrees_C, deg_C, Celsius... are all shown as °C
    return celsius(re.sub(r"<[^>]+>", "", str(text)))


def world_map():
    """Return a cached GeoDataFrame of the bundled world borders shapefile."""
    global _WORLD_MAP
    if _WORLD_MAP is None:
        import geopandas as gpd

        shp_path = importlib.resources.files("oceanval").joinpath(
            "data/TM_WORLD_BORDERS-0.3.shp"
        )
        _WORLD_MAP = gpd.read_file(str(shp_path))
    return _WORLD_MAP


def _add_coastline(ax, xlim=None, ylim=None):
    try:
        world_map().plot(ax=ax, facecolor="grey", edgecolor="grey", zorder=2)
    except Exception:
        pass
    if xlim is not None:
        ax.set_xlim(xlim)
    if ylim is not None:
        ax.set_ylim(ylim)


def _cap(values, prob=0.98):
    values = pd.Series(values).dropna()
    if len(values) == 0:
        return 0.0
    return values.quantile(prob)


def plot_locations_map(df_locs):
    """Scatter of matchup locations. Equivalent of the df_locs R map."""
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(df_locs["lon"], df_locs["lat"], s=8, alpha=0.6, color="black", zorder=3)
    xlim = (df_locs["lon"].min(), df_locs["lon"].max())
    ylim = (df_locs["lat"].min(), df_locs["lat"].max())
    _add_coastline(ax, xlim, ylim)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    plt.show()


def plot_variable_map(df, variable, unit, layer_long, vv_name):
    """Two-panel map of model vs observation, coloured by value.

    Equivalent of the R cell that gathers model/observation into a single
    ``variable``/``value`` pair and facets by variable.
    """
    xlim = (df["lon"].min(), df["lon"].max())
    ylim = (df["lat"].min(), df["lat"].max())
    p98 = _cap(pd.concat([df["model"], df["observation"]]), 0.98)

    fig, axes = plt.subplots(1, 2, figsize=(9, 4.5))
    label = _strip_html(f"{layer_long.title()} {vv_name} ({unit})")
    for ax, col, title in zip(axes, ["model", "observation"], ["Model", "Observation"]):
        values = df[col].clip(upper=p98)
        sc = ax.scatter(df["lon"], df["lat"], c=values, cmap="viridis", s=10, zorder=3)
        _add_coastline(ax, xlim, ylim)
        ax.set_title(title)
        ax.set_xticks([])
        ax.set_yticks([])
    fig.colorbar(sc, ax=axes, orientation="horizontal", fraction=0.05, pad=0.05, label=label)
    plt.show()


def plot_bias_map(df, unit, vv_name, layer_long, n_levels=1, layer_select="surface"):
    """Map of model - observation bias, capped at the 98th percentile."""
    df = df.assign(bias=df["model"] - df["observation"])
    bias_high = df["bias"].abs().quantile(0.98)
    df["bias"] = df["bias"].clip(lower=-bias_high, upper=bias_high)

    xlim = (df["lon"].min(), df["lon"].max())
    ylim = (df["lat"].min(), df["lat"].max())
    title = _strip_html(f"Bias in {layer_long} {vv_name} ({unit})")

    if "month" in df.columns and df["month"].nunique() > 1:
        months = sorted(df["month"].unique())
        n = len(months)
        ncols = min(4, n)
        nrows = int(np.ceil(n / ncols))
        fig, axes = plt.subplots(nrows, ncols, figsize=(3.2 * ncols, 3 * nrows), squeeze=False)
        for ax, month in zip(axes.flat, months):
            df_month = df.query("month == @month")
            sc = ax.scatter(
                df_month["lon"], df_month["lat"], c=df_month["bias"],
                cmap="bwr", vmin=-bias_high, vmax=bias_high, s=10, zorder=3,
            )
            _add_coastline(ax, xlim, ylim)
            ax.set_title(f"Month {month}")
            ax.set_xticks([])
            ax.set_yticks([])
        for ax in axes.flat[n:]:
            ax.axis("off")
        fig.colorbar(sc, ax=axes, orientation="horizontal", fraction=0.05, pad=0.05, label=title)
    else:
        fig, ax = plt.subplots(figsize=(6, 4.5))
        sc = ax.scatter(
            df["lon"], df["lat"], c=df["bias"], cmap="bwr",
            vmin=-bias_high, vmax=bias_high, s=10, zorder=3,
        )
        _add_coastline(ax, xlim, ylim)
        ax.set_xticks([])
        ax.set_yticks([])
        fig.colorbar(sc, ax=ax, orientation="horizontal", fraction=0.05, pad=0.08, label=title)
    plt.show()


def plot_model_obs_scatter(df, vv_name, unit, facet_by_month=False):
    """Scatter of model vs observation, with a 1:1 line and regression fit."""
    x_lab = _strip_html(f"Model {vv_name} ({unit})")
    y_lab = _strip_html(f"Observed {vv_name} ({unit})")

    def _panel(ax, data, title=None):
        ax.scatter(data["model"], data["observation"], s=10, alpha=0.6)
        lo = min(data["model"].min(), data["observation"].min())
        hi = max(data["model"].max(), data["observation"].max())
        ax.plot([lo, hi], [lo, hi], "k--", linewidth=1)
        if len(data) > 1:
            coeffs = np.polyfit(data["model"], data["observation"], 1)
            xs = np.linspace(lo, hi, 50)
            ax.plot(xs, np.polyval(coeffs, xs), color="tab:blue")
        if title is not None:
            ax.set_title(title)

    if facet_by_month and "month" in df.columns and df["month"].nunique() > 1:
        months = sorted(df["month"].unique())
        n = len(months)
        ncols = min(4, n)
        nrows = int(np.ceil(n / ncols))
        fig, axes = plt.subplots(nrows, ncols, figsize=(3.2 * ncols, 3 * nrows), squeeze=False, sharex=True, sharey=True)
        for ax, month in zip(axes.flat, months):
            _panel(ax, df.query("month == @month"), title=f"Month {month}")
        for ax in axes.flat[n:]:
            ax.axis("off")
        fig.text(0.5, 0.0, x_lab, ha="center")
        fig.text(0.0, 0.5, y_lab, va="center", rotation="vertical")
        fig.tight_layout()
    else:
        fig, ax = plt.subplots(figsize=(5, 5))
        _panel(ax, df)
        ax.set_xlabel(x_lab)
        ax.set_ylabel(y_lab)
        fig.tight_layout()
    plt.show()


def plot_seasonal_grid(df_model, df_obs, df_diff, model_unit, months, fixed_scale=False):
    """3-row (model, observation, difference) x N-month grid of maps."""
    if len(months) == 0:
        return

    df_model = df_model[df_model["month"].isin(months)]
    df_obs = df_obs[df_obs["month"].isin(months)]
    df_diff = df_diff[df_diff["month"].isin(months)]

    model_98 = _cap(df_model["model"], 0.98)
    obs_98 = _cap(df_obs["observation"], 0.98)
    if fixed_scale:
        model_98 = obs_98 = max(model_98, obs_98)
    diff_high = _cap(df_diff["diff"].abs(), 0.98)

    xlim = (df_model["lon"].min(), df_model["lon"].max())
    ylim = (df_model["lat"].min(), df_model["lat"].max())
    unit_label = _strip_html(model_unit)

    n = len(months)
    fig, axes = plt.subplots(3, n, figsize=(2.6 * n, 8), squeeze=False)
    rows = [
        (df_model, "model", "Model", model_98 * -1 if False else 0, model_98, "viridis"),
        (df_obs, "observation", "Observation", 0, obs_98, "viridis"),
        (df_diff, "diff", "Model - Observation", -diff_high, diff_high, "bwr"),
    ]
    for row_idx, (data, col, row_title, vmin, vmax, cmap) in enumerate(rows):
        for col_idx, month in enumerate(months):
            ax = axes[row_idx, col_idx]
            df_month = data[data["month"] == month]
            sc = ax.scatter(
                df_month["lon"], df_month["lat"], c=df_month[col].clip(vmin, vmax),
                cmap=cmap, vmin=vmin, vmax=vmax, s=8, zorder=3,
            )
            _add_coastline(ax, xlim, ylim)
            ax.set_xticks([])
            ax.set_yticks([])
            if row_idx == 0:
                ax.set_title(f"Month {month}")
            if col_idx == 0:
                ax.set_ylabel(row_title)
        fig.colorbar(sc, ax=axes[row_idx, :].tolist(), fraction=0.03, pad=0.01, label=unit_label)
    plt.show()


def plot_regional_timeseries(df_all):
    """Line plot of monthly spatial-average model/observation by region."""
    regions = list(df_all["long_name"].unique())
    n = len(regions)
    if n == 0:
        return
    ncols = min(3, n)
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 3 * nrows), squeeze=False, sharex=True)
    for ax, region in zip(axes.flat, regions):
        df_region = df_all.query("long_name == @region")
        for label, group in df_region.groupby("variable"):
            group = group.sort_values("month")
            ax.plot(group["month"], group["value"], marker="o", label=label)
        ax.set_title(region)
        ax.set_xlabel("Month")
    for ax in axes.flat[n:]:
        ax.axis("off")
    axes.flat[0].legend()
    fig.tight_layout()
    plt.show()


def plot_depth_locations_map(df_mapped, depth_order=None):
    """Facet of matchup locations by depth bin, in the order of depth_order,
    the bins' labels (see oceanval.depths), or the default bins'."""
    if depth_order is None:
        depth_order = depth_labels(DEFAULT_DEPTH_BINS)
    depths = [d for d in depth_order if d in df_mapped["depth"].unique()]
    n = len(depths)
    if n == 0:
        return
    ncols = min(4, n)
    nrows = int(np.ceil(n / ncols))
    xlim = (df_mapped["lon"].min(), df_mapped["lon"].max())
    ylim = (df_mapped["lat"].min(), df_mapped["lat"].max())
    fig, axes = plt.subplots(nrows, ncols, figsize=(3 * ncols, 2.8 * nrows), squeeze=False)
    for ax, depth in zip(axes.flat, depths):
        df_depth = df_mapped.query("depth == @depth")
        ax.scatter(df_depth["lon"], df_depth["lat"], s=4, color="black", zorder=3)
        _add_coastline(ax, xlim, ylim)
        ax.set_title(depth)
        ax.set_xticks([])
        ax.set_yticks([])
    for ax in axes.flat[n:]:
        ax.axis("off")
    fig.tight_layout()
    plt.show()


def plot_taylor_like(df_taylor):
    """Simplified Taylor diagram: normalized standard deviation vs correlation."""
    variables = list(df_taylor["variable"].unique())
    fig = plt.figure(figsize=(5, 5))
    ax = fig.add_subplot(111, projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_thetamin(0)
    ax.set_thetamax(90)

    markers = ["o", "s", "^", "D", "v", "P", "X", "*"]
    max_r = 1.0
    for i, variable in enumerate(variables):
        df_vv = df_taylor.query("variable == @variable")
        std_model = df_vv["model"].std()
        std_obs = df_vv["observation"].std()
        if std_obs == 0 or np.isnan(std_obs):
            continue
        nsd = std_model / std_obs
        corr = df_vv["model"].corr(df_vv["observation"])
        theta = np.arccos(np.clip(corr, -1, 1))
        ax.scatter(theta, nsd, label=_strip_html(variable), marker=markers[i % len(markers)], s=60)
        max_r = max(max_r, nsd)

    corr_ticks = [0, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 1.0]
    ax.set_xticks([np.arccos(c) for c in corr_ticks])
    ax.set_xticklabels([str(c) for c in corr_ticks])
    ax.set_rlim(0, max_r * 1.2)
    ax.set_title("Correlation")
    ax.legend(loc="upper right", bbox_to_anchor=(1.3, 1.1))
    fig.tight_layout()
    plt.show()


def plot_correlation_map(df_cor, global_grid=False):
    """Map of the spatial correlation between model and observations, by variable."""
    variables = list(df_cor["variable"].unique())
    n = len(variables)
    if n == 0:
        return
    ncols = min(3, n)
    nrows = int(np.ceil(n / ncols))
    min_val = df_cor["cor"].min()
    max_val = df_cor["cor"].max()
    if min_val < 0 < max_val:
        cmap, vmin, vmax = "bwr", -1, 1
    else:
        cmap, vmin, vmax = "viridis", min_val, max_val
    xlim = (df_cor["lon"].min(), df_cor["lon"].max())
    ylim = (df_cor["lat"].min(), df_cor["lat"].max())
    fig, axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 3.3 * nrows), squeeze=False)
    for ax, variable in zip(axes.flat, variables):
        df_vv = df_cor.query("variable == @variable")
        sc = ax.scatter(df_vv["lon"], df_vv["lat"], c=df_vv["cor"], cmap=cmap, vmin=vmin, vmax=vmax, s=10, zorder=3)
        _add_coastline(ax, xlim, ylim)
        ax.set_title(_strip_html(variable))
        ax.set_xticks([])
        ax.set_yticks([])
    for ax in list(axes.flat)[n:]:
        ax.axis("off")
    fig.colorbar(sc, ax=axes.flat[:n].tolist(), orientation="horizontal", fraction=0.05, pad=0.05, label="Correlation coefficient")
    plt.show()


def plot_depth_matchup_map(df_map, depth_order=None):
    """Facet grid of matchup locations by variable (rows) and depth bin
    (columns), in the order of depth_order, as for plot_depth_locations_map."""
    if depth_order is None:
        depth_order = depth_labels(DEFAULT_DEPTH_BINS)
    depths = [d for d in depth_order if d in df_map["depth_bin"].unique()]
    variables = list(df_map["variable"].unique())
    if len(depths) == 0 or len(variables) == 0:
        return
    xlim = (df_map["lon"].min(), df_map["lon"].max())
    ylim = (df_map["lat"].min(), df_map["lat"].max())
    fig, axes = plt.subplots(
        len(variables), len(depths),
        figsize=(2.6 * len(depths), 2.4 * len(variables)),
        squeeze=False,
    )
    for row_idx, variable in enumerate(variables):
        for col_idx, depth in enumerate(depths):
            ax = axes[row_idx, col_idx]
            df_sub = df_map.query("variable == @variable and depth_bin == @depth")
            ax.scatter(df_sub["lon"], df_sub["lat"], s=4, color="black", zorder=3)
            _add_coastline(ax, xlim, ylim)
            ax.set_xticks([])
            ax.set_yticks([])
            if row_idx == 0:
                ax.set_title(depth)
            if col_idx == 0:
                ax.set_ylabel(_strip_html(variable))
    fig.tight_layout()
    plt.show()
