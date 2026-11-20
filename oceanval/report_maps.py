"""Maps of the validation and comparison reports that take more than one
nctoolkit call to draw: the monthly maps of a gridded matchup's page
(seasonal_month_maps) and the bias maps of the comparison (bias_maps).

Each is one function of the netCDF it maps, so the report's figure code
(oceanval.figure_code) can draw it again from the files it saves.
"""

import calendar
import re

import numpy as np


def seasonal_frames(ds_model, ds_obs):
    """The monthly climatologies of a matchup's model and observations, and
    the model minus the observations, as tables (lon, lat, month and model,
    observation or diff), from nctoolkit DataSets of the surface matchup: (df_model,
    df_obs, df_diff, xlim, ylim), xlim and ylim the model's extent, before
    its longitudes are put into -180 to 180."""
    mod_var = ds_model.variables[0]
    obs_var = ds_obs.variables[0]
    time_name = [x for x in list(ds_model.to_xarray().coords) if "time" in x][0]
    lon_name = [x for x in list(ds_obs.to_xarray().coords) if "lon" in x][0]
    lat_name = [x for x in list(ds_obs.to_xarray().coords) if "lat" in x][0]

    df_model = (
        ds_model
        .to_dataframe()
        .reset_index()
        .rename(columns = {time_name: "time"})
        .rename(columns = {lon_name: "lon"})
        .rename(columns = {lat_name: "lat"})
        .assign(month = lambda x: x.time.dt.month)
        .loc[:,["lon", "lat", "month", mod_var ]]
        .rename(columns = {mod_var: "model"})
        .dropna()
    )

    xlim = np.array([df_model.lon.min(), df_model.lon.max()])
    ylim = np.array([df_model.lat.min(), df_model.lat.max()])

    df_obs = (
        ds_obs
        .to_dataframe()
        .reset_index()
    )

    time_name = [x for x in list(ds_obs.to_xarray().coords) if "time" in x][0]
    lon_name = [x for x in list(ds_obs.to_xarray().coords) if "lon" in x][0]
    lat_name = [x for x in list(ds_obs.to_xarray().coords) if "lat" in x][0]

    df_obs = (
        df_obs
        .rename(columns = {time_name: "time"}  )
        .rename(columns = {lon_name: "lon"}  )
        .rename(columns = {lat_name: "lat"}  )
        .assign(month = lambda x: x.time.dt.month)
        .loc[:,["lon", "lat", "month", obs_var ]]
        .rename(columns = {obs_var: "observation"})
        .dropna()

    )

    df_diff = (
        df_model
        .merge(df_obs, on = ["lon", "lat", "month"])
        .assign(diff = lambda x: x.model - x.observation)
    )
    df_model["lon" ] = [x if x < 180 else x -360 for x in df_model.lon]
    df_obs["lon" ] = [x if x < 180 else x -360 for x in df_obs.lon]
    df_diff["lon" ] = [x if x < 180 else x -360 for x in df_diff.lon]
    return df_model, df_obs, df_diff, xlim, ylim


def unit_label(variable, unit):
    """A monthly map's colour bar label: the unit only (°C for temperature),
    with mathtext instead of html."""
    unit_text = "°C" if variable == "temperature" else unit
    unit_text = re.sub(r"<sup>(.*?)</sup>", r"$^{\1}$", unit_text)
    unit_text = re.sub(r"<sub>(.*?)</sub>", r"$_{\1}$", unit_text)
    return unit_text


def seasonal_month_maps(ds_model, ds_obs, months, unit_text, divider = False, fixed_scale = False, global_grid = False):
    """Draw the model, observation and difference maps of these months with panel_plot,
    from nctoolkit DataSets of the monthly climatologies of a matchup's model and
    observations. unit_text labels the colour bars (unit_label); fixed_scale puts the
    model and observations on one range; global_grid draws the whole globe.

    divider draws a line under the difference maps, to separate them from the next months.
    Returns the figure, or None if none of the months has values.
    """
    import matplotlib.pyplot as plt
    from oceanval.quiet_nc import nc
    from oceanval.map_land import land_below
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from matplotlib.lines import Line2D

    df_model, df_obs, df_diff, xlim, ylim = seasonal_frames(ds_model, ds_obs)

    months = [int(month) for month in months if month in set(df_model.month)]
    if len(months) == 0:
        return None

    # colour limits are the mean over the year of each month's quantile
    def cap(df, column, prob):
        return df.groupby("month")[column].quantile(prob).mean()

    def colour_range(df, column, low = None, high = None):
        # range of the values in the months shown, once capped
        values = df.loc[df.month.isin(months), column]
        lo, hi = values.min(), values.max()
        if low is not None:
            lo = max(lo, low)
        if high is not None:
            hi = min(hi, high)
        return float(lo), float(hi)

    model_high = cap(df_model, "model", 0.98)
    obs_high = cap(df_obs, "observation", 0.98)
    if fixed_scale:
        # model and observation share one range
        shared_caps = (
            min(cap(df_model, "model", 0.02), cap(df_obs, "observation", 0.02)),
            max(model_high, obs_high),
        )
        model_caps = obs_caps = shared_caps
    else:
        model_caps = (None, model_high)
        obs_caps = (None, obs_high)
    diff_caps = (cap(df_diff, "diff", 0.02), cap(df_diff, "diff", 0.98))

    def diff_colours(lo, hi):
        # a diverging scale from blue to red: white at zero, and the
        # same colours for the same size of difference on either side of it
        blue_white_red = LinearSegmentedColormap.from_list("blue_white_red", ["blue", "white", "red"])
        largest = max(abs(lo), abs(hi))
        if largest == 0:
            return blue_white_red
        start = 0.5 + lo / (2 * largest)
        end = 0.5 + hi / (2 * largest)
        return LinearSegmentedColormap.from_list("difference", blue_white_red(np.linspace(start, end, 256)))

    ds_diff = ds_model.copy()
    ds_diff - ds_obs
    ds_diff.run()
    diff_range = colour_range(df_diff, "diff", *diff_caps)
    rows = [
        ("Model", ds_model, "viridis", colour_range(df_model, "model", *model_caps), df_model, "model"),
        ("Observation", ds_obs, "viridis", colour_range(df_obs, "observation", *obs_caps), df_obs, "observation"),
        ("Model - Observation", ds_diff, diff_colours(*diff_range), diff_range, df_diff, "diff"),
    ]

    # one row of maps per dataset, at most 12 inches wide: the height follows from the shape of
    # the maps, plus room for the titles above each row
    if global_grid:
        aspect = 2.0
    else:
        aspect = (xlim[1] - xlim[0]) * np.cos(np.radians(np.mean(ylim))) / (ylim[1] - ylim[0])
        aspect = min(max(aspect, 0.6), 2.5)
    width = min(12, len(months) * (3 * aspect + 0.2) + 1.4)
    map_height = (width - 1.4) / len(months) / aspect
    height = 3 * map_height + 1.6

    # all three rows are one figure, so the lines marking out these months join up.
    # Panel keys must be unique, and are only placeholders for the titles set below
    panels = dict()
    colours = []
    norms = []
    for title, ds, row_colours, (lo, hi), df, column in rows:
        for month in months:
            ds_month = ds.copy()
            ds_month.subset(months = month)
            ds_month.run()
            panels[f"{title} {month}"] = ds_month
            colours.append(row_colours)
            norms.append(Normalize(vmin = lo, vmax = hi))
    nc.panel_plot(
        panels,
        ncol = len(months),
        nrow = 3,
        size = [width, height],
        shared_colourbar = False,
        colours = colours,
        norm = norms,
        legend_position = None,
        extent = None if global_grid else [xlim[0], xlim[1], ylim[0], ylim[1]],
        land = "#bebebe",
        grid_labels = False,
    )
    land_below()
    fig = plt.gcf()
    axes = fig.axes[:3 * len(months)]

    row_groups = [axes[i * len(months):(i + 1) * len(months)] for i in range(3)]
    colour_bars = []
    for i, (title, ds, row_colours, (lo, hi), df, column) in enumerate(rows):
        row_axes = row_groups[i]
        for ax in row_axes:
            ax.set_title("")
        # the full month names are only given once, above the model maps
        if i == 0:
            for ax, month in zip(row_axes, months):
                ax.set_title(calendar.month_name[month])
        # each row is labelled on its left, rotated to run up the side of the maps
        row_axes[0].text(
            -0.04, 0.5, title.replace(" - ", " -\n"), transform = row_axes[0].transAxes,
            rotation = 90, ha = "right", va = "center", multialignment = "center", fontsize = 11,
        )

        # arrows on the colour bar show values beyond its limits in the months shown
        shown = df.loc[df.month.isin(months), column]
        below = shown.min() < lo
        above = shown.max() > hi
        extend = "both" if below and above else "min" if below else "max" if above else "neither"
        cb = fig.colorbar(
            ScalarMappable(norm = Normalize(vmin = lo, vmax = hi), cmap = row_colours),
            ax = row_axes,
            location = "right",
            extend = extend,
            shrink = 0.8,
            pad = 0.05,
        )
        cb.ax.set_ylabel(unit_text)
        colour_bars.append(cb)

    # panel_plot leaves white space round maps that keep their shape, so the axes are placed by
    # hand: each map is as wide as its column allows and the figure is as tall as the maps need
    fig.canvas.draw()
    x_range, y_range = axes[0].get_xlim(), axes[0].get_ylim()
    ratio = abs((y_range[1] - y_range[0]) / (x_range[1] - x_range[0]))
    margin_left, margin_right, margin_top, margin_bottom = 0.65, 1.0, 0.3, 0.1
    gap_x, gap_y = 0.05, 0.08
    map_width = (width - margin_left - margin_right - (len(months) - 1) * gap_x) / len(months)
    map_height = map_width * ratio
    height = margin_top + margin_bottom + 3 * map_height + 2 * gap_y
    fig.set_layout_engine("none")
    fig.set_size_inches(width, height)
    for i, (row_axes, cb) in enumerate(zip(row_groups, colour_bars)):
        y0 = height - margin_top - i * (map_height + gap_y) - map_height
        for j, ax in enumerate(row_axes):
            x0 = margin_left + j * (map_width + gap_x)
            ax.set_position([x0 / width, y0 / height, map_width / width, map_height / height])
        cb.ax.set_position([
            (width - margin_right + 0.15) / width, (y0 + 0.1 * map_height) / height,
            0.12 / width, 0.8 * map_height / height,
        ])
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    inverse = fig.transFigure.inverted()
    fig_width, fig_height = fig.get_size_inches()

    def figure_box(ax):
        # x0, y0, x1, y1 of everything an axes draws, including its titles and labels
        return inverse.transform(ax.get_tightbbox(renderer)).flatten()

    bottom = min(ax.get_position().y0 for ax in fig.axes) - 0.15 / fig_height
    # left of the row labels, and level with the top of the month names
    left = min(figure_box(ax)[0] for ax in axes[::len(months)]) - 0.08 / fig_width
    top = max(figure_box(ax)[3] for ax in axes[:len(months)])
    line = dict(transform = fig.transFigure, color = "#c8c8c8", linewidth = 1)
    # a thin line down the left of the maps of these months
    fig.add_artist(Line2D([left, left], [bottom, top], **line))
    if divider:
        # and one under them, across the whole figure, separating them from the next months
        fig.add_artist(Line2D([left, 0.99], [bottom, bottom], **line))
    return fig


def bias_maps(panels, limits, difference = None, difference_title = None):
    """The comparison's bias maps: panels, {simulation: nctoolkit DataSet of its
    bias}, on one colour scale (limits) and colour bar, and, for two simulations,
    difference, a DataSet of the first's model minus the second's, in the third
    column, titled difference_title, with its own scale. Returns the figure."""
    import matplotlib.pyplot as plt
    from oceanval.quiet_nc import nc
    from oceanval.map_land import land_below

    if difference is not None:
        # leave the third column free for the difference between the models
        nc.panel_plot(panels, ncol = 3, nrow = 1, limits = limits, grid_labels = False)
        land_below()
    else:
        nc.panel_plot(panels, ncol = 3 if len(panels) == 3 else None, limits = limits, grid_labels = False)
        land_below()
    fig = plt.gcf()
    if difference is not None:
        # the difference has its own scale, so it gets its own colour bar in the free column
        gs = fig.axes[0].get_subplotspec().get_gridspec()
        difference.pub_plot(fig = fig, gs = gs[0, 2], title = difference_title, limits = ["2%", "98%"], grid_labels = False)
        land_below(fig)
        # widen the figure to make room for the second colour bar
        width, height = fig.get_size_inches()
        fig.set_size_inches(width + 1.0, height)
    return fig
