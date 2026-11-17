"""An example of each figure and table of a validation report (oceanval.report_items),
for the oceanval window's customize tiles: drawn when its button is clicked,
from data made up there and then, never read from or written to disk.

The made-up data are small: a 4° global grid over 12 months, whose
"observations" have a seasonal cycle that peaks in summer, and whose "model" is
them a little too high, a month late and noisy; and a few hundred "observations"
around the Northwest European Shelf, for a point page. Each example is drawn
by the report's own code where the report draws it from a data frame or arrays
(oceanval.gridded_plots, oceanval.plotnine_figures, oceanval.taylor,
oceanval.transects, oceanval.regions); the maps the report draws from its
netCDF files with nctoolkit are drawn here from the arrays with cartopy, in the
same colours. A table is given as HTML.

example(item) gives {"ok": True, "kind": "image", "data": <base64 PNG>} or
{"ok": True, "kind": "table", "html": ...}, kept for the session, so a second
click is instant. One example is drawn at a time (matplotlib is not thread-safe).
"""

import base64
import calendar
import io
import threading

import numpy as np
import pandas as pd

from oceanval import report_items

_LOCK = threading.Lock()
_DRAWN = {}

LON = np.arange(-178.0, 180.0, 4.0)
LAT = np.arange(-78.0, 80.0, 4.0)
MONTHS = np.arange(1, 13)
NAME = "nitrate"
UNIT = "mmol m-3"


# ---- the made-up data ----


def _field():
    """(obs, model), each month x lat x lon, of a surface concentration."""
    rng = np.random.default_rng(7)
    lon, lat = np.meshgrid(LON, LAT)
    base = 2 + 10 * (1 - np.cos(np.radians(lat))) + 1.5 * np.sin(np.radians(2 * lon))
    peak = np.where(lat >= 0, 7, 1)
    amplitude = 0.6 + 4 * np.abs(np.sin(np.radians(lat)))
    phase = 2 * np.pi * (MONTHS[:, None, None] - peak[None]) / 12
    obs = base[None] + amplitude[None] * np.cos(phase)
    later = 2 * np.pi * (MONTHS[:, None, None] - 1 - peak[None]) / 12
    model = 1.08 * (base[None] + amplitude[None] * np.cos(later)) + 0.4 + rng.normal(0, 0.8, obs.shape)
    return np.maximum(obs, 0.05), np.maximum(model, 0.05)


def _profiles():
    """(depths, obs, model) of a mean profile."""
    depths = np.array([0, 10, 25, 50, 100, 200, 300, 500, 800, 1000, 1500, 2000.0])
    obs = 30 - 28 * np.exp(-depths / 300)
    model = 32 - 30 * np.exp(-depths / 250)
    return depths, obs, model


def _points(n=360):
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"lon": rng.uniform(-12, 9, n), "lat": rng.uniform(47, 61, n),
                       "month": np.resize(MONTHS, n)})
    df["observation"] = 6 + 4 * np.cos(2 * np.pi * (df.month - 1) / 12) + rng.normal(0, 1, n)
    df["observation"] = df.observation.clip(lower=0.1)
    df["model"] = (0.9 * df.observation + 0.5 + rng.normal(0, 1.1, n)).clip(lower=0.1)
    return df


def _stats(model, obs):
    """r, the bias, RMSD, nsd, the means and N of paired values (for the examples only)."""
    model, obs = np.ravel(model), np.ravel(obs)
    ok = np.isfinite(model) & np.isfinite(obs)
    model, obs = model[ok], obs[ok]
    return {
        "n": int(ok.sum()), "r": float(np.corrcoef(model, obs)[0, 1]), "bias": float(model.mean() - obs.mean()),
        "rmsd": float(np.sqrt(np.mean((model - obs) ** 2))), "nsd": float(model.std() / obs.std()),
        "model_mean": float(model.mean()), "obs_mean": float(obs.mean()),
    }


def _label():
    from oceanval import unit_display

    return f"{NAME} ({unit_display.mathtext(UNIT)})"


# ---- drawing ----


def _png(fig):
    import matplotlib.pyplot as plt

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=80, bbox_inches="tight")
    plt.close("all")
    return {"ok": True, "kind": "image", "data": base64.b64encode(buffer.getvalue()).decode("ascii")}


def _plotnine(plot, width, height):
    from plotnine import theme

    return _png((plot + theme(figure_size=(width / 72, height / 72), dpi=72)).draw())


def _table(frame):
    return {"ok": True, "kind": "table", "html": frame.to_html(index=False, border=0, classes="example-table",
                                                               float_format=lambda x: f"{x:.3g}")}


def _maps(panels, label, cmap="viridis", limits=None, ncols=None, centred=False):
    """Maps of lat x lon arrays side by side, with one colour bar: panels is
    [(title, values)]. centred: a diverging scale about 0."""
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    import matplotlib.pyplot as plt

    n = len(panels)
    ncols = ncols or min(n, 3)
    nrows = int(np.ceil(n / ncols))
    values = np.concatenate([np.ravel(v) for _, v in panels])
    if limits is None:
        if centred:
            span = float(np.nanpercentile(np.abs(values), 98))
            limits = (-span, span)
        else:
            limits = tuple(np.nanpercentile(values, [2, 98]))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.2 * ncols, 2.4 * nrows + 0.8), squeeze=False,
                             subplot_kw={"projection": ccrs.PlateCarree()}, constrained_layout=True)
    mesh = None
    for ax, (title, field) in zip(axes.ravel(), panels):
        mesh = ax.pcolormesh(LON, LAT, field, cmap=cmap, vmin=limits[0], vmax=limits[1], shading="nearest")
        ax.add_feature(cfeature.LAND.with_scale("110m"), facecolor="lightgrey", zorder=2)
        ax.coastlines("110m", lw=0.4, zorder=3)
        ax.set_global()
        ax.set_title(title, loc="left", fontsize=9)
    for ax in axes.ravel()[n:]:
        ax.set_visible(False)
    bar = fig.colorbar(mesh, ax=axes, orientation="horizontal", shrink=0.6, pad=0.03)
    bar.set_label(label, fontsize=8)
    return fig


def _annual():
    obs, model = _field()
    return obs.mean(axis=0), model.mean(axis=0)


def _peak_months(field):
    return 12 - np.argmax(field[::-1], axis=0)


def _temporal_correlation():
    obs, model = _field()
    o, m = obs - obs.mean(axis=0), model - model.mean(axis=0)
    return (o * m).sum(axis=0) / np.sqrt((o ** 2).sum(axis=0) * (m ** 2).sum(axis=0))


def _transect_frame():
    obs, model = _field()
    row = np.argmin(np.abs(LAT - 0))
    rows = [(lon, month, model[k, row, j], obs[k, row, j])
            for k, month in enumerate(MONTHS) for j, lon in enumerate(LON)]
    frame = pd.DataFrame(rows, columns=["position", "month", "model", "observation"])
    return frame.assign(bias=frame.model - frame.observation)


def _section_frame():
    depths, obs_profile, model_profile = _profiles()
    rows = [(lon, depth, model_profile[k] + np.sin(np.radians(lon)) * 1.5, obs_profile[k] + np.cos(np.radians(lon)))
            for lon in LON for k, depth in enumerate(depths)]
    frame = pd.DataFrame(rows, columns=["position", "depth", "model", "observation"])
    return frame.assign(bias=frame.model - frame.observation)


def _regional_frame():
    obs, model = _field()
    rows = []
    for name, band in (("Full Domain", LAT == LAT), ("Tropics", np.abs(LAT) < 20), ("North Atlantic", LAT > 20),
                       ("Southern Ocean", LAT < -40)):
        for variable, field in (("model", model), ("observation", obs)):
            for k, month in enumerate(MONTHS):
                rows.append((month, float(field[k][band].mean()), variable, name))
    return pd.DataFrame(rows, columns=["month", "value", "variable", "long_name"])


def _sections_frame():
    depths, obs_profile, model_profile = _profiles()
    rows = []
    for region in ("Full domain", "Tropics"):
        for lat in LAT:
            for k, depth in enumerate(depths):
                rows.append((lat, depth, "Observation", region, obs_profile[k] + np.cos(np.radians(lat)) * 3))
                rows.append((lat, depth, "Model", region, model_profile[k] + np.cos(np.radians(lat)) * 3.5))
    return pd.DataFrame(rows, columns=["lat", "depth", "variable", "region", "value"])


DEPTH_RANGES = ["0-10m", "10-30m", "30-60m", "60-100m"]


def _with_depths(df):
    return df.assign(depth=np.resize(DEPTH_RANGES, len(df)))


# ---- each item ----


def _clim_map():
    obs, model = _annual()
    return _maps([("Model", model), ("Observations", obs)], _label())


def _bias_map():
    obs, model = _annual()
    return _maps([("Model minus observations", model - obs)], f"Bias in {_label()}", cmap="RdBu_r", centred=True)


def _density_scatter():
    from oceanval import gridded_plots

    obs, model = _field()
    obs_year, model_year = obs.mean(axis=0), model.mean(axis=0)
    low, high = np.percentile(np.concatenate([obs.ravel(), model.ravel()]), [0.5, 99.5])
    every, annual = gridded_plots.Density(low, high), gridded_plots.Density(low, high)
    every.add(model, obs)
    annual.add(model_year, obs_year)
    return gridded_plots.density_panels(
        [("All months", every, _stats(model, obs)), ("Annual means", annual, _stats(model_year, obs_year))], _label(),
    )


def _spatial_cor_table():
    obs, model = _field()
    rows = [("Annual mean", np.corrcoef(model.mean(axis=0).ravel(), obs.mean(axis=0).ravel())[0, 1])]
    rows += [(calendar.month_abbr[m], np.corrcoef(model[k].ravel(), obs[k].ravel())[0, 1]) for k, m in enumerate(MONTHS)]
    return pd.DataFrame(rows, columns=["Time period", "Correlation coefficient"])


def _seasonal_cor_map():
    return _maps([("Seasonal correlation", _temporal_correlation())], "Correlation coefficient", cmap="viridis",
                 limits=(-1, 1))


def _monthly_maps():
    obs, model = _field()
    panels = []
    for k in (0, 3, 6, 9):
        month = calendar.month_abbr[k + 1]
        panels += [(f"{month}: model", model[k]), (f"{month}: observations", obs[k])]
    return _maps(panels, _label(), ncols=2)


def _peak_month():
    import matplotlib.pyplot as plt

    obs, model = _field()
    return _maps([("Observations", _peak_months(obs)), ("Model", _peak_months(model))],
                 "Month of the peak (1 is January)", cmap=plt.get_cmap("twilight", 12), limits=(0.5, 12.5))


def _zonal_lines():
    from oceanval import gridded_plots

    obs, model = _annual()
    return gridded_plots.zonal_lines(LAT, obs.mean(axis=1), model.mean(axis=1), _label(),
                                     np.percentile(obs, 25, axis=1), np.percentile(obs, 75, axis=1))


def _hovmoller(years):
    from oceanval import gridded_plots

    obs, model = _field()
    obs, model = obs.mean(axis=2), model.mean(axis=2)
    if years:
        times = 2000 + (np.arange(36) + 0.5) / 12
        obs, model = np.tile(obs, (3, 1)), np.tile(model, (3, 1)) * np.linspace(0.95, 1.05, 36)[:, None]
        return gridded_plots.hovmoller(times, LAT, obs, model, model - obs, _label(), monthly=False)
    return gridded_plots.hovmoller(MONTHS, LAT, obs, model, model - obs, _label())


def _global_series():
    from oceanval import gridded_plots

    obs = 2.1 + 0.6 * np.cos(2 * np.pi * (MONTHS - 2) / 12)
    model = obs * 1.1 + 0.1
    return gridded_plots.total_series(MONTHS, model, obs, "CO$_{2}$ flux (Pg C yr$^{-1}$)",
                                      [{"low": 1.7, "high": 2.9, "citation": "A published range", "period": ""}])


def _regional_map():
    from oceanval import regions

    return regions.panel_map("latbands")


def _regional_cycle():
    from oceanval import plotnine_figures

    return _plotnine(plotnine_figures.regional_cycles(_regional_frame(), f"Spatial average {_label()}"), 1000, 700)


def _regional_table():
    frame = _regional_frame()
    rows = []
    for region, group in frame.groupby("long_name", sort=False):
        model, obs = group[group.variable == "model"].value, group[group.variable == "observation"].value
        rows.append((region, obs.mean(), model.mean(), obs.std(), model.std(), model.mean() - obs.mean(),
                     np.sqrt(np.mean((model.values - obs.values) ** 2))))
    return pd.DataFrame(rows, columns=["Region", "Observed mean", "Modelled mean", "Observed SD", "Modelled SD",
                                       "Bias", "RMSD"])


def _zonal_sections():
    from oceanval import plotnine_figures, unit_display

    return _plotnine(plotnine_figures.zonal_sections(_sections_frame(), NAME, unit_display.mathtext(UNIT)),
                     1000, 1300)


def _depth_level_table():
    from oceanval import depths as depth_levels

    depths, obs, model = _profiles()
    keep = np.isin(depths, depth_levels.table_levels(depths))
    rng = np.random.default_rng(5)
    return pd.DataFrame({
        "Depth (m)": depths[keep], "Model mean": model[keep], "Observed mean": obs[keep],
        "Bias": (model - obs)[keep], "RMSD": np.abs(model - obs)[keep] * 1.3,
        "Correlation": rng.uniform(0.6, 0.95, keep.sum()), "Normalised standard deviation": rng.uniform(0.8, 1.2, keep.sum()),
        "Number of grid cells": rng.integers(2000, 9000, keep.sum()),
    })


def _mean_profiles():
    from oceanval import gridded_plots

    depths, obs, model = _profiles()
    return gridded_plots.profiles(depths, {"Full domain": (obs, model), "Tropics": (obs * 0.8, model * 0.85)}, _label())


def _depth_maps():
    obs, model = _annual()
    return _maps([("Model at 200 m", model * 2), ("Observations at 200 m", obs * 2),
                  ("Bias at 200 m", (model - obs) * 2)], _label(), ncols=3)


def _equator():
    from oceanval import transects

    return dict(transects.SECTIONS["equator"], name="equator")


def _transect_map():
    from oceanval import transects

    return transects.plot_map(_equator())


def _transect_surface():
    from oceanval import transects, unit_display

    return transects.plot_months(_transect_frame(), _equator(), unit_display.mathtext(UNIT), NAME)


def _transect_section():
    from oceanval import transects, unit_display

    return transects.plot_section(_section_frame(), _equator(), unit_display.mathtext(UNIT), NAME)


def _locations_map():
    from oceanval import plotnine_figures

    return _plotnine(plotnine_figures.locations_map(_points()[["lon", "lat"]].drop_duplicates()), 600, 500)


def _mean_map():
    from oceanval import plotnine_figures

    df = _points().drop(columns="month")
    return _plotnine(plotnine_figures.mean_map(df, f"Sea Surface {_label()}"), 1000, 700)


def _point_bias_map():
    from oceanval import plotnine_figures

    df, high = plotnine_figures.capped_bias(_points())
    return _plotnine(plotnine_figures.bias_map(df, high, f"Bias in sea surface {_label()}"), 1000, 1200)


def _scatter():
    from oceanval import plotnine_figures

    return _plotnine(plotnine_figures.scatter(_points(), f"Model {_label()}", f"Observed {_label()}"), 1000, 1200)


def _stats_table():
    df = _points()
    rows = [("All", *_point_row(df))]
    rows += [(calendar.month_abbr[m], *_point_row(g)) for m, g in df.groupby("month")]
    return pd.DataFrame(rows, columns=["Time period", "Bias", "RMSD", "Correlation", "Number of observations"])


def _point_row(df):
    stats = _stats(df.model.values, df.observation.values)
    return stats["bias"], stats["rmsd"], stats["r"], stats["n"]


def _regression_table():
    df = _points()
    rows = []
    for period, group in [("All", df)] + [(calendar.month_abbr[m], g) for m, g in df.groupby("month")]:
        slope, intercept = np.polyfit(group.model, group.observation, 1)
        r = np.corrcoef(group.model, group.observation)[0, 1]
        rows.append((period, slope, intercept, r ** 2))
    return pd.DataFrame(rows, columns=["Time period", "Slope", "Intercept", "R²"])


def _depth_locations_map():
    from oceanval import plotnine_figures

    return _plotnine(plotnine_figures.depth_locations_map(_with_depths(_points()), DEPTH_RANGES), 1000, 800)


def _depth_table():
    df = _with_depths(_points())
    rows = [(depth, *_point_row(group)) for depth, group in df.groupby("depth", sort=False)]
    return pd.DataFrame(rows, columns=["Depth range", "Bias", "RMSD", "Correlation", "Number of observations"])


def _point_profiles():
    from oceanval import gridded_plots

    df = _with_depths(_points())
    means = df.groupby("depth", sort=False)[["observation", "model"]].mean().reindex(DEPTH_RANGES)
    return gridded_plots.profiles(DEPTH_RANGES, {"All": (means.observation.values, means.model.values)}, _label())


VARIABLES = ["Temperature", "Salinity", "Nitrate", "Chlorophyll"]


def _taylor():
    from oceanval import taylor

    frame = pd.DataFrame({"simulation": "Model", "variable": VARIABLES, "nsd": [0.95, 1.1, 0.8, 1.3],
                          "r": [0.97, 0.85, 0.72, 0.55]})
    return taylor.figure(frame)


def _bias_table():
    return pd.DataFrame({"Variable": VARIABLES, "Modelled spatial mean": [14.2, 34.9, 6.1, 0.42],
                         "Observational spatial mean": [13.8, 35.0, 5.2, 0.35], "Model bias": [0.4, -0.1, 0.9, 0.07],
                         "Unit": ["°C", "PSU", "mmol m-3", "mg m-3"]})


def _spatial_table():
    return pd.DataFrame({"Variable": VARIABLES, "Correlation coefficient": [0.97, 0.85, 0.72, 0.55],
                         "RMSD": [1.1, 0.4, 2.3, 0.3], "Normalised standard deviation": [0.95, 1.1, 0.8, 1.3],
                         "Centred RMSD": [0.9, 0.3, 1.9, 0.25]})


def _temporal_maps():
    correlation = _temporal_correlation()
    return _maps([("Nitrate", correlation), ("Chlorophyll", np.clip(correlation * 0.8, -1, 1))],
                 "Correlation coefficient", limits=(-1, 1))


def _temporal_table():
    return pd.DataFrame({"Variable": VARIABLES, "Mean seasonal correlation": [0.95, 0.62, 0.71, 0.48]})


def _global_totals():
    return pd.DataFrame({"Variable": ["Air-sea CO2 flux", "Alkalinity inventory"], "Model total": [2.4, 3.2e19],
                         "Observed total": [2.1, 3.1e19], "Published range": ["1.7–2.9", "3.0e19–3.2e19"],
                         "Units": ["Pg C yr-1", "mol"]})


def _depth_performance():
    from oceanval import plotnine_figures

    df = _with_depths(_points())
    both = pd.concat([df.assign(variable=v) for v in ("Nitrate", "Oxygen")]).rename(columns={"depth": "depth_bin"})
    return _plotnine(plotnine_figures.depth_matchup_map(both, DEPTH_RANGES), 1000, 700)


DRAW = {
    "clim_map": _clim_map,
    "bias_map": _bias_map,
    "density_scatter": _density_scatter,
    "spatial_cor_table": _spatial_cor_table,
    "seasonal_cor_map": _seasonal_cor_map,
    "monthly_maps": _monthly_maps,
    "peak_month": _peak_month,
    "zonal_lines": _zonal_lines,
    "hovmoller_clim": lambda: _hovmoller(False),
    "hovmoller_years": lambda: _hovmoller(True),
    "global_series": _global_series,
    "regional_map": _regional_map,
    "regional_cycle": _regional_cycle,
    "regional_table": _regional_table,
    "zonal_sections": _zonal_sections,
    "depth_level_table": _depth_level_table,
    "mean_profiles": _mean_profiles,
    "depth_maps": _depth_maps,
    "transect_map": _transect_map,
    "transect_surface": _transect_surface,
    "transect_section": _transect_section,
    "locations_map": _locations_map,
    "mean_map": _mean_map,
    "point_bias_map": _point_bias_map,
    "scatter": _scatter,
    "stats_table": _stats_table,
    "regression_table": _regression_table,
    "depth_locations_map": _depth_locations_map,
    "depth_table": _depth_table,
    "point_profiles": _point_profiles,
    "taylor": _taylor,
    "bias_table": _bias_table,
    "spatial_table": _spatial_table,
    "temporal_maps": _temporal_maps,
    "temporal_table": _temporal_table,
    "global_totals": _global_totals,
    "depth_performance": _depth_performance,
}


def example(item):
    """The example of the report item item (an id of oceanval.report_items), drawn
    from made-up data: {"ok", "kind": "image", "data"} or {"ok", "kind": "table",
    "html"}, or {"ok": False, "error"}."""
    if item not in report_items.BY_ID or item not in DRAW:
        return {"ok": False, "error": "There is no example of that."}
    with _LOCK:
        if item not in _DRAWN:
            import matplotlib

            matplotlib.use("Agg")
            try:
                drawn = DRAW[item]()
            except Exception as error:
                return {"ok": False, "error": f"The example could not be drawn: {error}"}
            _DRAWN[item] = _table(drawn) if isinstance(drawn, pd.DataFrame) else (
                drawn if isinstance(drawn, dict) else _png(drawn)
            )
        return _DRAWN[item]
