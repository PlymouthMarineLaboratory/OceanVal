"""The report's figures drawn with plotnine (the grammar of graphics in Python):
the point pages' maps and scatter plots, the summary's map of the point
matchups at each depth, the gridded pages' regional seasonal cycles and
depth-latitude sections, and the comparison report's regional cycles.

Each function takes the data frame the page made and returns a plotnine plot
(or composition), which show() draws: a size is given in pixels and drawn at
72 dpi, so the fonts are the size the figures have always had on the page.

The land under the maps is Natural Earth's, as on the gridded pages' maps
(cartopy downloads it once): at 1:10m for an area no more than 50° across, a
lake for example, which a world map has too little detail for, else at
1:50m, with Natural Earth's lakes taken out of it where they are to hand. If
it cannot be read, the world borders bundled with OceanVal are used.

Units are written by oceanval.unit_display, in mathtext.
"""

import calendar
import functools
import math

import numpy as np
import pandas as pd

# an area no wider or taller than this (degrees) has the detailed land
SMALL_AREA = 50

# ---- the house style of the figures that are not maps (theme_oceanval) ----

# the observations' and the model's lines, the grey of bands, frames and grids, and the text
OBS = "#3a3a3a"
MODEL = "#d03b3b"
BAND = "#c9c8c2"
INK = "#0b0b0b"
MUTED = "#52514e"
# what has no values in a transect's panels (the seabed, land), as on its map
LAND = "#d9dee0"
# the two lines of every model-and-observations figure, named as their legend names them
LINES = {"Observations": OBS, "Model": MODEL}
# the colours of values, and of a bias (model minus observations) on a scale symmetric about 0,
# as on the page's maps (nctoolkit's pub_plot)
VALUE_COLOURS = "viridis"
BIAS_COLOURS = "RdBu_r"
# a colour bar's length and width (pixels) and the characters on a line of its title
BAR_LENGTH = 90
BAR_WIDTH = 10
BAR_TITLE_WIDTH = 16


def theme_oceanval(base_size=10):
    """The look of every figure that is not a map: white panels in a thin grey
    frame with a light grid, panel titles plain and to the left, legends and
    colour bars without boxes."""
    from plotnine import element_blank, element_line, element_rect, element_text, theme, theme_bw

    return theme_bw(base_size=base_size) + theme(
        text=element_text(colour=INK),
        axis_text=element_text(colour=MUTED, size=base_size - 1),
        panel_border=element_rect(colour=BAND, fill=None),
        panel_grid_major=element_line(colour=BAND, size=0.35),
        panel_grid_minor=element_blank(),
        axis_ticks=element_line(colour=BAND),
        strip_background=element_blank(),
        strip_text=element_text(ha="left", size=base_size, colour=INK),
        legend_key=element_blank(),
        legend_title=element_text(ha="left", size=base_size - 1),
        legend_text=element_text(size=base_size - 1),
        plot_title=element_text(ha="left", size=base_size + 1),
    )


def _line_legend(colours=None):
    """The colours of the lines (LINES, or colours: name -> colour, in order),
    their legend across the top without a title."""
    from plotnine import element_blank, scale_colour_manual, theme

    colours = colours or LINES
    return [
        scale_colour_manual(values=colours, breaks=list(colours), name=""),
        theme(legend_position="top", legend_title=element_blank()),
    ]


def _bar_theme():
    """A colour bar beside its panels: its title written across above it, a bar
    short enough to sit beside a row of panels."""
    from plotnine import element_text, theme

    return theme(legend_title=element_text(ha="left", size=9), legend_key_height=BAR_LENGTH,
                 legend_key_width=BAR_WIDTH)


def draw(plot, size):
    """The matplotlib figure of plot (or a composition), size (width, height)
    inches, open in pyplot as a figure of matplotlib's own is, so the page's
    plt.show() shows it (once)."""
    from plotnine import theme

    sized = theme(figure_size=size)
    figure = (plot & sized if _is_composition(plot) else plot + sized).draw()
    _keep_open(figure)
    return figure


def _keep_open(figure):
    """Give pyplot back the figure plotnine's draw() closed (its plot_context
    closes it, so a later plt.show() would show nothing): the way pyplot.figure
    makes a figure current."""
    from matplotlib import _pylab_helpers

    manager = getattr(figure.canvas, "manager", None)
    if manager is not None:
        _pylab_helpers.Gcf._set_new_active_manager(manager)


def _split_label(label):
    """("name", "units") of a label "name (units)"; ("label", "") without units."""
    label = str(label)
    if label.endswith(")") and " (" in label:
        name, _, units = label.rpartition(" (")
        return name, units[:-1]
    return label, ""


def _percentile_limits(values, log=False):
    """A colour scale's limits: the 2nd and 98th percentiles of values (positive
    values only if log), or None if there are none."""
    values = np.asarray(values, dtype="float64")
    values = values[np.isfinite(values)]
    if log:
        values = values[values > 0]
    if not values.size:
        return None
    low, high = (float(x) for x in np.percentile(values, [2, 98]))
    if high <= low:
        pad = abs(low) * 0.01 or 0.5
        low, high = low - pad, high + pad
    return low, high


def bias_span(values):
    """Half the width of a bias's colour scale: the 98th percentile of its size
    (its largest if that is 0, else 1)."""
    values = np.abs(np.asarray(values, dtype="float64"))
    values = values[np.isfinite(values)]
    if not values.size:
        return 1.0
    span = float(np.percentile(values, 98))
    if span <= 0:
        span = float(values.max())
    return span if span > 0 else 1.0


def _value_scale(values, title, log=False):
    """viridis from the 2nd to the 98th percentile of values (log10 if log),
    what is beyond shown as the end's colour."""
    from mizani.bounds import squish
    from plotnine import scale_fill_cmap

    limits = _percentile_limits(values, log)
    options = {"limits": limits, "oob": squish, "name": title}
    if log and limits is not None and limits[0] > 0:
        options["trans"] = "log10"
    return scale_fill_cmap(VALUE_COLOURS, **options)


def _bias_scale(values, title):
    """RdBu_r, symmetric about 0 (bias_span), what is beyond shown as the end's colour."""
    from mizani.bounds import squish
    from plotnine import scale_fill_cmap

    span = bias_span(values)
    return scale_fill_cmap(BIAS_COLOURS, limits=(-span, span), oob=squish, name=title)


def _panels(n):
    """(ncol, nrow) of n facets: 3 in a row, else a square as near as can be."""
    ncol = 3 if n == 3 else max(1, math.ceil(math.sqrt(n)))
    return ncol, max(1, math.ceil(n / ncol))


def sized(plot, width, height):
    """The matplotlib figure of plot (or a composition), width by height
    pixels, open in pyplot for the page's plt.show() (see show)."""
    from plotnine import theme

    plot = plot & theme(figure_size=(width / 72, height / 72), dpi=72) if _is_composition(plot) else (
        plot + theme(figure_size=(width / 72, height / 72), dpi=72)
    )
    figure = plot.draw()
    _keep_open(figure)
    return figure


def show(plot, width, height):
    """Draw plot (or a composition) in the notebook, width by height pixels."""
    import matplotlib.pyplot as plt

    figure = sized(plot, width, height)
    plt.show()
    return figure


def _is_composition(plot):
    try:
        from plotnine.composition import Compose
    except ImportError:
        return False
    return isinstance(plot, Compose)


def land_scale(box):
    """Natural Earth's scale for the land of box (lon_min, lat_min, lon_max,
    lat_max, 1° beyond the points): 1:10m for points no more than SMALL_AREA
    degrees apart either way, else 1:50m."""
    small = (box[2] - box[0]) <= SMALL_AREA + 2 and (box[3] - box[1]) <= SMALL_AREA + 2
    return "10m" if small else "50m"


@functools.lru_cache(maxsize=16)
def _land(box):
    """The land in box (lon_min, lat_min, lon_max, lat_max), as a GeoDataFrame
    clipped to it, or None if no land could be read."""
    import geopandas as gpd
    from shapely.geometry import box as shape

    area = shape(*box)
    scale = land_scale(box)
    land = None
    try:
        from cartopy.io import shapereader

        land = gpd.read_file(shapereader.natural_earth(scale, "physical", "land"), bbox=area)
        try:
            lakes = gpd.read_file(shapereader.natural_earth(scale, "physical", "lakes"), bbox=area)
            if len(lakes):
                land = gpd.GeoDataFrame(geometry=land.geometry.difference(lakes.union_all()), crs=land.crs)
        except Exception:
            pass
    except Exception:
        try:
            from oceanval.pyplots import world_map

            land = world_map()
        except Exception:
            return None
    land = gpd.clip(land, area)
    land = land[~land.geometry.is_empty]
    return land if len(land) else None


def _land_layer(lon, lat, fill, colour=None):
    """geom_map of the land around the points lon, lat (1° beyond them), or []."""
    from plotnine import geom_map

    lon, lat = np.asarray(lon, dtype="float64"), np.asarray(lat, dtype="float64")
    box = (
        float(np.floor(np.nanmin(lon))) - 1, float(np.floor(np.nanmin(lat))) - 1,
        float(np.ceil(np.nanmax(lon))) + 1, float(np.ceil(np.nanmax(lat))) + 1,
    )
    land = _land(box)
    if land is None:
        return []
    return [geom_map(data=land, fill=fill, colour=colour if colour else None, size=0.2 if colour else 0)]


def _limits(values):
    values = np.asarray(values, dtype="float64")
    low, high = float(np.nanmin(values)), float(np.nanmax(values))
    if high <= low:
        low, high = low - 0.5, high + 0.5
    return low, high


def _no_axes():
    from plotnine import element_blank, labs, theme

    return [
        theme(axis_text_x=element_blank(), axis_text_y=element_blank(), axis_ticks=element_blank(),
              axis_title_x=element_blank(), axis_title_y=element_blank(), plot_margin=0),
        labs(x="", y=""),
    ]


def _map_base(lon, lat, ratio=1.5):
    """A map of the points' extent: their land, then coord_fixed (degrees of
    latitude 1.5 times as tall as those of longitude)."""
    from plotnine import coord_fixed

    return [coord_fixed(ratio=ratio, xlim=_limits(lon), ylim=_limits(lat), expand=False)]


def locations_map(df_locs):
    """The point page's map of where the observations matched up are."""
    from plotnine import aes, geom_point, ggplot, theme_gray

    plot = ggplot() + _land_layer(df_locs.lon, df_locs.lat, "#999999")
    plot += geom_point(df_locs, aes("lon", "lat"))
    plot += theme_gray(base_size=14)
    plot += _map_base(df_locs.lon, df_locs.lat)
    return plot + _no_axes()


def mean_map(df, label):
    """The point page's map of the average model and observed value at each
    place (data with no months): a panel each, the colours capped at the 98th
    percentile. label is the colour bar's title."""
    from plotnine import aes, element_text, facet_wrap, geom_point, ggplot, scale_colour_cmap, theme, theme_gray

    df_map = df.melt(id_vars=["lon", "lat"], value_vars=["model", "observation"], var_name="variable")
    df_map["value"] = np.minimum(df_map.value, df_map.value.quantile(0.98))
    df_map["variable"] = pd.Categorical(df_map.variable.str.title(), ["Model", "Observation"])
    ncol, nrow = _panels(df_map.variable.nunique())
    plot = ggplot() + _land_layer(df.lon, df.lat, "#999999")
    plot += geom_point(df_map, aes("lon", "lat", colour="value"))
    plot += theme_gray(base_size=14)
    plot += _map_base(df.lon, df.lat)
    plot += facet_wrap("variable", ncol=ncol, nrow=nrow)
    plot += scale_colour_cmap("viridis", name=label)
    plot += theme(legend_position="bottom", legend_direction="horizontal", legend_key_width=8.0 / 2.54 * 72,
                  legend_key_height=1.0 / 2.54 * 72, legend_title=element_text(size=20, ha="center"),
                  legend_title_position="bottom")
    return plot + _no_axes()


def capped_bias(df):
    """df with its bias (model minus observation), capped at plus or minus the
    98th percentile of its absolute value, and that cap."""
    df = df.assign(bias=df.model - df.observation)
    high = float(np.abs(df.bias).quantile(0.98))
    return df.assign(bias=df.bias.clip(-high, high)), high


def _month_names(months):
    """Month numbers as their abbreviations, ordered as the year."""
    names = [calendar.month_abbr[int(x)] for x in months]
    return pd.Categorical(names, [calendar.month_abbr[k] for k in range(1, 13)])


def bias_map(df, high, label):
    """The point page's map of the model's bias at each observation (capped_bias's
    df and high), a panel for each month if the data have months."""
    from plotnine import (aes, element_text, facet_wrap, geom_point, ggplot, scale_colour_gradient2, theme,
                          theme_dark)

    plot = ggplot() + _land_layer(df.lon, df.lat, "#b3b3b3")
    data = df.sort_values("month") if "month" in df.columns else df
    if "month" in data.columns:
        data = data.assign(month=_month_names(data.month))
    plot += geom_point(data, aes("lon", "lat", colour="bias"))
    plot += theme_dark(base_size=24)
    plot += _map_base(df.lon, df.lat)
    plot += scale_colour_gradient2(low="blue", mid="white", high="red", limits=(-high, high), name=label)
    width = 12.0 if "month" in data.columns and data.month.nunique() >= 9 else 9.0
    plot += theme(legend_position="bottom", legend_direction="horizontal", legend_key_width=width / 2.54 * 72,
                  legend_key_height=1.0 / 2.54 * 72, legend_title=element_text(size=20, ha="center"),
                  legend_title_position="bottom")
    if "month" in data.columns:
        ncol, nrow = _panels(data.month.nunique())
        plot += facet_wrap("month", ncol=ncol, nrow=nrow)
    return plot + _no_axes()


def scatter(df, x_label, y_label, by_month=True, base_size=24):
    """Modelled against observed values, with the 1:1 line and the least-squares
    line: a panel of all months and one for each month, if by_month and the data
    have months."""
    from plotnine import aes, facet_wrap, geom_abline, geom_point, geom_smooth, ggplot, labs, theme_gray

    data = df
    months = by_month and "month" in df.columns
    if months:
        data = df.sort_values("month").assign(month=lambda x: [calendar.month_abbr[int(m)] for m in x.month])
        data = pd.concat([df.assign(month="All months"), data], ignore_index=True)
        data["month"] = pd.Categorical(data.month, ["All months"] + [calendar.month_abbr[k] for k in range(1, 13)])
    plot = ggplot(data, aes("model", "observation")) + geom_point()
    plot += theme_gray(base_size=base_size)
    plot += geom_abline()
    plot += geom_smooth(method="lm", se=False, colour="#3366ff")
    plot += labs(x=x_label, y=y_label)
    if months:
        ncol, nrow = _panels(data.month.nunique())
        plot += facet_wrap("month", ncol=ncol, nrow=nrow)
    return plot


def density_scatter(tiles, fits, boxes, titles, limits, top, bin_width, x_label, y_label, ncol, fontsize, size):
    """The gridded page's density scatters: a panel for each of titles, in order,
    on the same axes (limits, both ways) and one colour scale (a count from 1 to
    top, on a log scale). tiles is the bins with values (panel, x, y, count), each
    bin_width wide; fits the red least-squares line of each panel (panel, slope,
    intercept); boxes the text of each panel's statistics (panel, text). The axes
    are shared, so each axis title is given once. size is (width, height) in inches."""
    from plotnine import (
        aes, coord_cartesian, element_blank, element_text, facet_wrap, geom_abline, geom_label, geom_tile, ggplot,
        labs, scale_fill_cmap, theme,
    )

    low, high = limits
    inset = (high - low) * 0.03
    for frame in (tiles, fits, boxes):
        frame["panel"] = pd.Categorical(frame["panel"], titles, ordered=True)
    boxes = boxes.assign(x=low + inset, y=high - inset)
    plot = (
        ggplot(tiles, aes("x", "y", fill="count"))
        + geom_tile(width=bin_width, height=bin_width)
        + scale_fill_cmap("viridis", trans="log10", limits=(1, top), name="Number of matched values",
                          breaks=[10 ** k for k in range(int(math.log10(top)) + 1)])
        + geom_abline(slope=1, intercept=0, linetype="dashed", colour=INK, size=0.5)
        + geom_abline(aes(slope="slope", intercept="intercept"), data=fits, colour=MODEL, size=0.7, inherit_aes=False)
        + geom_label(aes("x", "y", label="text"), data=boxes, ha="left", va="top", size=fontsize, colour=INK,
                     fill="white", boxstyle="round,pad=0.3", inherit_aes=False)
        + coord_cartesian(xlim=limits, ylim=limits, expand=False)
        + facet_wrap("panel", ncol=ncol)
        + labs(x=x_label, y=y_label)
        + theme_oceanval(base_size=fontsize + 2)
        + theme(
            figure_size=size, aspect_ratio=1, panel_grid=element_blank(), panel_spacing=0.02,
            legend_title=element_text(ha="left", size=fontsize + 1), legend_key_height=size[1] * 72 * 0.3,
            legend_key_width=14,
        )
    )
    return plot


def depth_locations_map(df_mapped, depth_levels):
    """The point page's map of the matchups in each depth range, a panel each."""
    from plotnine import aes, facet_wrap, geom_point, ggplot, theme_gray

    data = df_mapped.assign(depth=pd.Categorical(df_mapped.depth, list(depth_levels)))
    ncol, nrow = _panels(data.depth.nunique())
    plot = ggplot() + _land_layer(data.lon, data.lat, "#999999")
    plot += geom_point(data, aes("lon", "lat"), size=0.2)
    plot += theme_gray(base_size=14)
    plot += _map_base(data.lon, data.lat)
    plot += facet_wrap("depth", ncol=ncol, nrow=nrow)
    return plot + _no_axes()


def _degrees(values, north, south, zero="0°"):
    return [f"{v:g}°{north}" if v > 0 else (f"{abs(v):g}°{south}" if v < 0 else zero) for v in values]


def depth_matchup_map(df_map, depth_levels):
    """The summary's map of the point matchups of each variable (rows) in each
    depth range (columns), with degrees on the axes."""
    from plotnine import (aes, coord_cartesian, element_blank, facet_grid, geom_point, ggplot, scale_x_continuous,
                          scale_y_continuous, theme, theme_bw)

    data = df_map.assign(depth_bin=pd.Categorical(df_map.depth_bin, list(depth_levels)))
    plot = ggplot() + _land_layer(data.lon, data.lat, "grey", colour="grey")
    plot += geom_point(data, aes("lon", "lat"), size=0.5)
    plot += coord_cartesian(xlim=_limits(data.lon), ylim=_limits(data.lat), expand=False)
    plot += facet_grid("variable ~ depth_bin")
    plot += theme_bw(base_size=8)
    plot += scale_y_continuous(labels=lambda values: _degrees(values, "N", "S", "0°N"))
    plot += scale_x_continuous(labels=lambda values: _degrees(values, "E", "W", "0°E"))
    return plot + theme(plot_margin=0.01, axis_title_x=element_blank(), axis_title_y=element_blank())


def _full_domain_first(names, full):
    names = list(dict.fromkeys(names))
    return ([full] if full in names else []) + [x for x in names if x != full]


def regional_cycles(df_all, label):
    """The gridded page's seasonal cycle of the model and observations in each
    region (df_all: month, value, variable "model"/"observation", long_name),
    Full Domain first, in the house style (the model red, the observations grey)."""
    from plotnine import aes, facet_wrap, geom_line, ggplot, labs, scale_x_continuous

    data = df_all.assign(
        variable=df_all.variable.str.lower().map({"model": "Model", "observation": "Observations"}),
        long_name=pd.Categorical(df_all.long_name, _full_domain_first(df_all.long_name, "Full Domain")),
    )
    ncol, nrow = _panels(data.long_name.nunique())
    plot = ggplot(data) + geom_line(aes("month", "value", colour="variable"), size=0.8)
    plot += facet_wrap("long_name", ncol=ncol, nrow=nrow)
    plot += labs(y=label, x="Month")
    plot += scale_x_continuous(breaks=[1, 4, 7, 10], labels=["Jan", "Apr", "Jul", "Oct"])
    plot += theme_oceanval(base_size=12)
    return plot + _line_legend()


def _measure_panels(df):
    """The measures of a stratification matchup (oceanval.stratification) as
    the panels' titles, "label (unit)", in the order of MEASURES."""
    from oceanval import stratification, unit_display

    titles = {
        x: f"{stratification.LABELS[x]} ({unit_display.mathtext(stratification.UNITS[x])})"
        for x in stratification.MEASURES
    }
    order = [titles[x] for x in stratification.MEASURES if x in set(df.measure)]
    return df.assign(panel=pd.Categorical(df.measure.map(titles), order))


def stratification_cycle(df):
    """The stratification page's seasonal cycle: the mean of each measure over
    the casts of each month (df: month, measure, observation, model), observed
    and modelled, a panel for each measure on its own scale. A month with no
    casts has no point, so the lines join the months that have them."""
    from plotnine import aes, facet_wrap, geom_line, geom_point, ggplot, labs, scale_x_continuous

    means = df.groupby(["measure", "month"], as_index=False)[["observation", "model"]].mean()
    data = means.melt(id_vars=["measure", "month"], value_vars=["observation", "model"], var_name="variable")
    data = _measure_panels(data.assign(variable=data.variable.map({"model": "Model", "observation": "Observations"})))
    ncol, nrow = _panels(data.panel.nunique())
    plot = ggplot(data, aes("month", "value", colour="variable"))
    plot += geom_line(size=0.8)
    plot += geom_point(size=1.6)
    plot += facet_wrap("panel", ncol=ncol, nrow=nrow, scales="free_y")
    plot += labs(y="", x="Month")
    plot += scale_x_continuous(breaks=[1, 4, 7, 10], labels=["Jan", "Apr", "Jul", "Oct"], limits=(1, 12))
    plot += theme_oceanval(base_size=12)
    return plot + _line_legend()


def stratification_scatter(df):
    """The stratification page's scatter: each cast's measure modelled against
    observed (df: measure, observation, model), with the 1:1 line, a panel for
    each measure on its own scales, the model on the x axis as on the point
    pages' scatter plots."""
    from plotnine import aes, facet_wrap, geom_abline, geom_point, ggplot, labs

    data = _measure_panels(df)
    ncol, nrow = _panels(data.panel.nunique())
    plot = ggplot(data, aes("model", "observation")) + geom_point(alpha=0.5, size=1.2, colour=OBS)
    plot += geom_abline(colour=MODEL)
    plot += facet_wrap("panel", ncol=ncol, nrow=nrow, scales="free")
    plot += labs(x="Model", y="Observations")
    return plot + theme_oceanval(base_size=12)


def comparison_cycles(df, label):
    """The comparison report's seasonal cycle of each simulation and the
    observations in each region (df: month, value, variable, long_name), each
    region with its own scale, from 0."""
    from plotnine import (aes, element_blank, element_text, expand_limits, facet_wrap, geom_line, ggplot, labs,
                          scale_x_continuous, theme, theme_gray)

    plot = ggplot(df) + geom_line(aes("month", "value", colour="variable"))
    plot += facet_wrap("long_name", scales="free")
    plot += theme_gray()
    plot += theme(legend_title=element_blank(), legend_position="top",
                  axis_text_x=element_text(rotation=45, ha="right"))
    plot += expand_limits(y=0)
    plot += scale_x_continuous(breaks=[2, 4, 6, 8, 10, 12], labels=["Feb", "Apr", "Jun", "Aug", "Oct", "Dec"])
    return plot + labs(y=label, x="Month")


def _cells(values):
    """The edges of the cells centred on values (sorted, unique): halfway to
    each neighbour, the first and last as wide as the gap beside them."""
    values = np.asarray(sorted(set(values)), dtype="float64")
    if len(values) == 1:
        return {values[0]: values[0] - 0.5}, {values[0]: values[0] + 0.5}
    mid = (values[1:] + values[:-1]) / 2
    low = np.concatenate([[values[0] - (mid[0] - values[0])], mid])
    high = np.concatenate([mid, [values[-1] + (values[-1] - mid[-1])]])
    return dict(zip(values, low)), dict(zip(values, high))


def _rectangles(df, x="lat", y="depth"):
    """df with each cell's edges (xmin, xmax, ymin, ymax) around its x and y, for geom_rect."""
    x_low, x_high = _cells(df[x])
    y_low, y_high = _cells(df[y])
    return df.assign(
        xmin=df[x].map(x_low), xmax=df[x].map(x_high),
        ymin=df[y].map(y_low), ymax=df[y].map(y_high),
    )


def _bar_title(name, units):
    """A colour bar's title, written across: name wrapped to a few words a line,
    then the units (mathtext, never wrapped) on a line of their own."""
    import textwrap

    lines = textwrap.wrap(name, BAR_TITLE_WIDTH) or [name]
    return "\n".join(lines + ([f"({units})"] if units else []))


def _label_title(label):
    """_bar_title of a label "name (units)"."""
    return _bar_title(*_split_label(label))


# the panels of a model-and-observations heat map, in order, and the bias's
PAIR = ["Model", "Observation"]
BIAS_PANEL = "Bias"


def paired_heat(values, bias, *, value_title, bias_title, scales, labels, ncol=2, log=False, background="white",
                layers=(), title=None, base_size=10):
    """The house layout of a heat map of the model and the observations: the two
    side by side (ncol=2) or stacked (ncol=1) on one viridis scale, then their bias
    (model minus observations) below, as wide as both, on RdBu_r symmetric about 0,
    each with its colour bar beside it. As the zonal sections, the Hovmöller
    diagrams and the transects all are.

    values is rectangles (xmin, xmax, ymin, ymax, value, panel "Model"/"Observation")
    and bias rectangles of the bias (value). scales are the x and y scales of both,
    labels (x title, y title). layers are drawn over the model and the
    observations side by side (an oxygen section's contours; ncol=2 only, their
    data with the panel column), background is what shows where
    there are no values, title is above the two."""
    from plotnine import aes, coord_cartesian, element_rect, facet_wrap, geom_rect, ggplot, ggtitle, labs, theme

    from plotnine import geom_blank

    rect = aes(xmin="xmin", xmax="xmax", ymin="ymin", ymax="ymax", fill="value")
    # the axes reach every cell, those without values too (a transect's seabed shows as background)
    extent = pd.DataFrame({"x": [values.xmin.min(), values.xmax.max()], "y": [values.ymin.min(), values.ymax.max()]})
    reach = geom_blank(extent, aes("x", "y"), inherit_aes=False)
    values = values.dropna(subset=["value"])
    if log:
        values = values[values.value > 0]
    values = values.assign(panel=pd.Categorical(values.panel, PAIR))
    bias = bias.dropna(subset=["value"]).assign(panel=BIAS_PANEL)
    def common():
        # made anew for each plot: a theme added to one plot must not be shared with another
        return [
            reach, *scales, coord_cartesian(expand=False), labs(x=labels[0], y=labels[1]), theme_oceanval(base_size),
            # a gap between the two side by side wide enough that their end labels do not meet
            _bar_theme(), theme(panel_background=element_rect(fill=background), panel_spacing_x=0.04),
        ]

    bottom = ggplot(bias) + geom_rect(rect) + facet_wrap("panel") + _bias_scale(bias.value, bias_title) + common()
    if ncol == 2:
        top = ggplot(values) + geom_rect(rect) + facet_wrap("panel", ncol=2) + _value_scale(values.value, value_title, log)
        top = top + list(layers) + common()
        if title:
            top += ggtitle(title)
        return top / bottom
    # stacked: a plot each, so the three are the same height (a composition shares its height out by plot);
    # the model's colour bar is the observations', so it is given once, and the panels still line up
    plots = []
    for k, panel in enumerate(PAIR):
        one = ggplot(values[values.panel == panel]) + geom_rect(rect) + facet_wrap("panel")
        one = one + _value_scale(values.value, value_title, log) + common()
        if k == 0:
            one += theme(legend_position="none")
            if title:
                one += ggtitle(title)
        plots.append(one)
    return plots[0] / plots[1] / bottom


def _latitudes(values):
    return _degrees(values, "N", "S")


# the zonal sections' height for each region (and the full domain): two rows of panels, the region's title and axes
ZONAL_REGION_HEIGHT = 450


def zonal_height(n_regions):
    """The height (pixels) of the zonal sections of n_regions regions and the full domain."""
    return max(2000, ZONAL_REGION_HEIGHT * (n_regions + 1))


def zonal_sections(df_zonals, name, units):
    """The gridded page's zonal mean sections through the water column of each
    region (df_zonals: lat, depth, value, variable "Model"/"Observation",
    region): paired_heat's model and observations side by side, then their bias,
    for each region in turn, stacked."""
    from plotnine import scale_x_continuous, scale_y_reverse

    parts = []
    for region in dict.fromkeys(df_zonals.region):
        data = df_zonals[df_zonals.region == region].dropna()
        wide = data.pivot_table(index=["lat", "depth"], columns="variable", values="value").reset_index()
        wide = wide.dropna(subset=["Model", "Observation"]).assign(value=lambda x: x.Model - x.Observation)
        parts.append(paired_heat(
            _rectangles(data.rename(columns={"variable": "panel"})), _rectangles(wide),
            value_title=_bar_title(name, units), bias_title=_bar_title("Bias", units),
            scales=[scale_y_reverse(), scale_x_continuous(labels=_latitudes)], labels=("", "Depth (m)"),
            title=region,
        ))
    # one stack of every row, so each region has the same height (stacking stacks would give the first
    # region's two rows a share each and each later region's two one share between them)
    from plotnine.composition import Stack

    return Stack([row for part in parts for row in part])


# ---- the line figures: zonal means, profiles and totals ----


def _lines_frame(series):
    """A frame (x, value, name) of series: [(name, x, values)], without the missing values, names in order."""
    frames = [pd.DataFrame({"x": np.asarray(x, dtype="float64"), "value": np.asarray(v, dtype="float64"), "name": name})
              for name, x, v in series]
    frame = pd.concat(frames, ignore_index=True)
    frame = frame[np.isfinite(frame.value)]
    return frame.assign(name=pd.Categorical(frame.name, list(dict.fromkeys(n for n, _, _ in series))))


def zonal_lines(lat, obs, lines, label, colours, band=None, log=False, title=None):
    """The zonal mean of the observations and of each of lines ({name: (lat, values)})
    against latitude, coloured by colours ({name: colour}, the observations first),
    with band (lat, low, high), the observations' interquartile range across
    longitude, under them."""
    from plotnine import aes, geom_line, geom_ribbon, ggplot, labs, scale_fill_manual, scale_x_continuous, scale_y_log10

    series = [("Observations", lat, obs)] + [(name, x, v) for name, (x, v) in lines.items()]
    frame = _lines_frame(series)
    plot = ggplot()
    if band is not None:
        band_lat, low, high = (np.asarray(x, dtype="float64") for x in band)
        ok = np.isfinite(low) & np.isfinite(high)
        shade = pd.DataFrame({"x": band_lat[ok], "low": low[ok], "high": high[ok],
                              "what": "Observations, interquartile range across longitude"})
        plot += geom_ribbon(shade, aes("x", ymin="low", ymax="high", fill="what"))
        plot += scale_fill_manual(values=[BAND], name="")
    plot += geom_line(frame, aes("x", "value", colour="name"), size=0.8)
    plot += scale_x_continuous(labels=_latitudes)
    if log:
        plot += scale_y_log10()
    plot += labs(x="Latitude", y=label, title=title or "")
    return plot + theme_oceanval() + _line_legend(colours)


def profiles(depths, lines, label, title=None):
    """Mean profiles, depth downwards, a panel for each of lines ({panel: (obs,
    model)}, each an array over depths), 4 to a row on one depth axis. depths are
    numbers (gridded levels, in metres) or labels (point depth ranges, evenly
    spaced, each a point on its line)."""
    from plotnine import aes, facet_wrap, geom_path, geom_point, ggplot, labs, scale_y_reverse

    named = len(depths) > 0 and isinstance(depths[0], str)
    y = np.arange(len(depths), dtype="float64") if named else np.asarray(depths, dtype="float64")
    panels = list(lines)
    frames = []
    for panel in panels:
        obs, model = lines[panel]
        frame = _lines_frame([("Observations", y, obs), ("Model", y, model)])
        frames.append(frame.assign(panel=panel))
    frame = pd.concat(frames, ignore_index=True).sort_values(["panel", "name", "x"], kind="stable")
    frame["panel"] = pd.Categorical(frame.panel, panels)
    plot = ggplot(frame, aes("value", "x", colour="name", group="name")) + geom_path(size=0.8)
    if named:
        plot += geom_point(size=1.5)
        plot += scale_y_reverse(breaks=list(y), labels=list(depths))
    else:
        plot += scale_y_reverse()
    plot += facet_wrap("panel", ncol=min(len(panels), 4), scales="free_x")
    plot += labs(x=label, y="Depth" if named else "Depth (m)", title=title or "")
    return plot + theme_oceanval() + _line_legend()


def total_series(times, model, obs, label, references=(), monthly=True):
    """The total over the matched cells of each month, the model and the
    observations, with each published range (references: low, high, citation,
    period) as a grey band. times are month numbers (a climatology) or decimal years."""
    from plotnine import aes, geom_line, geom_point, geom_rect, ggplot, labs, scale_fill_manual, scale_x_continuous

    times = np.asarray(times, dtype="float64")
    frame = _lines_frame([("Observations", times, obs), ("Model", times, model)])
    plot = ggplot()
    if len(references):
        bands = pd.DataFrame([
            {"low": row["low"], "high": row["high"],
             "what": f"{row['citation']}, {row['period']}" if row.get("period") else row["citation"]}
            for row in references
        ])
        bands["what"] = pd.Categorical(bands.what, list(dict.fromkeys(bands.what)))
        plot += geom_rect(bands, aes(ymin="low", ymax="high", fill="what"), xmin=-np.inf, xmax=np.inf, alpha=0.55)
        plot += scale_fill_manual(values=[BAND, "#e4e3de", "#efeeea", "#f6f5f2"][: len(bands)], name="")
    plot += geom_line(frame, aes("x", "value", colour="name"), size=0.8)
    if monthly:
        plot += geom_point(frame, aes("x", "value", colour="name"), size=1.2)
        plot += scale_x_continuous(breaks=list(range(1, 13)), labels=[calendar.month_abbr[m][0] for m in range(1, 13)])
    plot += labs(x="" if monthly else "Year", y=label)
    return plot + theme_oceanval() + _line_legend()


def transect_line(frame, position_label, position_labels, value_label):
    """A transect's model and observations along it (frame: position, model,
    observation), for a matchup of a single time step."""
    from plotnine import aes, geom_line, ggplot, labs, scale_x_continuous

    data = frame.groupby("position")[["model", "observation"]].mean()
    lines = _lines_frame([("Observations", data.index, data.observation), ("Model", data.index, data.model)])
    plot = ggplot(lines, aes("x", "value", colour="name")) + geom_line(size=0.8)
    plot += scale_x_continuous(labels=position_labels)
    plot += labs(x=position_label, y=value_label)
    return plot + theme_oceanval() + _line_legend()


# ---- the heat maps: Hovmöller diagrams and transects ----


def _year_breaks(times):
    """A break at the start of each year (every few, for a long matchup), as whole
    years: the cells reach half a month either side of their mid-month times."""
    first, last = math.ceil(times.min() - 1 / 24 - 1e-9), math.floor(times.max() + 1 / 24 + 1e-9)
    years = list(range(first, last + 1)) or [round(times.min())]
    step = max(1, math.ceil(len(years) / 8))
    return years[::step]


def hovmoller(times, lat, obs, model, diff, label, log=False, monthly=True, title=None):
    """paired_heat of latitude (up) by time (across) of the zonal means obs and
    model (time, band) and their difference diff (log10 ratio if log). times are
    month numbers (monthly, a climatology) or decimal years; a long series has
    the model and the observations stacked, so time runs across the page."""
    from plotnine import scale_x_continuous, scale_y_continuous

    times = np.asarray(times, dtype="float64")
    lat = np.asarray(lat, dtype="float64")

    def cells(field, panel=None):
        grid = pd.DataFrame(np.asarray(field, dtype="float64"), index=times, columns=lat)
        frame = grid.stack(future_stack=True).rename_axis(["time", "lat"]).reset_index(name="value")
        frame = _rectangles(frame, x="time", y="lat")
        return frame if panel is None else frame.assign(panel=panel)

    values = pd.concat([cells(model, "Model"), cells(obs, "Observation")], ignore_index=True)
    if monthly:
        x_scale = scale_x_continuous(breaks=[1, 4, 7, 10], labels=["Jan", "Apr", "Jul", "Oct"])
    else:
        breaks = _year_breaks(times)
        x_scale = scale_x_continuous(breaks=breaks, labels=[str(y) for y in breaks])
    name, units = _split_label(label)
    value_title = _bar_title(f"log10 {name}" if log else name, units)
    bias_title = _bar_title("log10(model / observation)", "") if log else _bar_title("Bias", units)
    wide = (not monthly) and times.size > 36
    return paired_heat(
        values, cells(diff), value_title=value_title, bias_title=bias_title,
        scales=[x_scale, scale_y_continuous(labels=_latitudes)], labels=("" if monthly else "Year", ""),
        ncol=1 if wide else 2, title=title,
    )


def hovmoller_size(monthly, n_times):
    """The size (inches) of hovmoller's figure."""
    return (10, 9.5) if (not monthly) and n_times > 36 else (10, 6.4)


def _contour_paths(frame, column, levels, panel):
    """The lines of frame's column (position, depth) at levels, a frame (x, y,
    group, panel) for geom_path, worked out by contourpy (matplotlib's own)."""
    import contourpy

    grid = frame.pivot_table(index="depth", columns="position", values=column, dropna=False)
    if np.isfinite(grid.values).sum() <= 3:
        return []
    generator = contourpy.contour_generator(
        x=grid.columns.values.astype("float64"), y=grid.index.values.astype("float64"),
        z=np.ma.masked_invalid(grid.values),
    )
    paths = []
    for level in sorted(levels):
        for k, line in enumerate(generator.lines(level)):
            if len(line) > 1:
                paths.append(pd.DataFrame({"x": line[:, 0], "y": line[:, 1], "group": f"{panel} {level} {k}",
                                           "panel": panel}))
    return paths


def transect_section(frame, position_label, position_labels, name, unit, log=False, contours=None):
    """paired_heat of a transect through the water column (frame: position,
    depth, model, observation, bias): position across, depth down from the
    shallowest to the deepest, the seabed grey. contours are values drawn as
    black lines on the model and the observations (oxygen's)."""
    from mizani.breaks import breaks_extended
    from plotnine import aes, geom_path, scale_x_continuous, scale_y_reverse

    shallow, deep = float(frame.depth.min()), float(frame.depth.max())

    def cells(column, panel=None):
        out = _rectangles(frame[["position", "depth", column]].rename(columns={column: "value"}), x="position", y="depth")
        out = out.assign(ymin=out.ymin.clip(shallow, deep), ymax=out.ymax.clip(shallow, deep))
        return out if panel is None else out.assign(panel=panel)

    values = pd.concat([cells("model", "Model"), cells("observation", "Observation")], ignore_index=True)
    layers = []
    if contours:
        paths = _contour_paths(frame, "model", contours, "Model") + _contour_paths(frame, "observation", contours, "Observation")
        if paths:
            lines = pd.concat(paths, ignore_index=True)
            lines["panel"] = pd.Categorical(lines.panel, PAIR)
            layers.append(geom_path(lines, aes("x", "y", group="group"), colour=INK, size=0.5, inherit_aes=False))
    return paired_heat(
        values, cells("bias"), value_title=_bar_title(name, unit), bias_title=_bar_title("Bias", unit),
        scales=[scale_x_continuous(breaks=breaks_extended(n=4), labels=position_labels), scale_y_reverse()],
        labels=(position_label, "Depth (m)"), log=log, background=LAND, layers=layers,
    )


def transect_months(frame, north_south, position_label, position_labels, name, unit, log=False):
    """paired_heat of a transect in each month (frame: position, month, model,
    observation, bias). A north-south line is by month (across) and latitude
    (up), the model and observations side by side; an east-west one by
    longitude (across) and month (down, January at the top), the two stacked,
    so north is up and east is right, as on the map. No values is grey."""
    from plotnine import scale_x_continuous, scale_y_continuous, scale_y_reverse

    months = sorted(int(m) for m in frame.month.unique())
    index = {m: k for k, m in enumerate(months)}
    names = [calendar.month_abbr[m] for m in months]
    data = frame.assign(step=frame.month.astype(int).map(index).astype("float64"))
    x, y = ("step", "position") if north_south else ("position", "step")

    def cells(column, panel=None):
        out = _rectangles(data[["position", "step", column]].rename(columns={column: "value"}), x=x, y=y)
        return out if panel is None else out.assign(panel=panel)

    values = pd.concat([cells("model", "Model"), cells("observation", "Observation")], ignore_index=True)
    steps = list(range(len(months)))
    if north_south:
        scales = [scale_x_continuous(breaks=steps, labels=names), scale_y_continuous(labels=position_labels)]
        labels = ("Month", position_label)
    else:
        scales = [scale_x_continuous(labels=position_labels), scale_y_reverse(breaks=steps, labels=names)]
        labels = (position_label, "Month")
    return paired_heat(
        values, cells("bias"), value_title=_bar_title(name, unit), bias_title=_bar_title("Bias", unit),
        scales=scales, labels=labels, ncol=2 if north_south else 1, log=log, background=LAND,
    )
