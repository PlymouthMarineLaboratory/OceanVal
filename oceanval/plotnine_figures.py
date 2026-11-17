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
# the colours of the regional cycles' lines, in order (the FiveThirtyEight palette)
FIVETHIRTYEIGHT = ["#008fd5", "#fc4f30", "#e5ae38", "#6d904f", "#8b8b8b", "#810f7c"]


def _panels(n):
    """(ncol, nrow) of n facets: 3 in a row, else a square as near as can be."""
    ncol = 3 if n == 3 else max(1, math.ceil(math.sqrt(n)))
    return ncol, max(1, math.ceil(n / ncol))


def show(plot, width, height):
    """Draw plot (or a composition) in the notebook, width by height pixels."""
    import matplotlib.pyplot as plt
    from plotnine import theme

    plot = plot & theme(figure_size=(width / 72, height / 72), dpi=72) if _is_composition(plot) else (
        plot + theme(figure_size=(width / 72, height / 72), dpi=72)
    )
    figure = plot.draw()
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
    Full Domain first."""
    from plotnine import (aes, facet_wrap, geom_line, ggplot, labs, scale_colour_manual, scale_x_continuous,
                          theme, theme_bw)

    data = df_all.assign(
        variable=df_all.variable.str.title(),
        long_name=pd.Categorical(df_all.long_name, _full_domain_first(df_all.long_name, "Full Domain")),
    )
    ncol, nrow = _panels(data.long_name.nunique())
    plot = ggplot(data) + geom_line(aes("month", "value", colour="variable"))
    plot += facet_wrap("long_name", ncol=ncol, nrow=nrow)
    plot += labs(y=label, x="Month", colour="")
    plot += scale_colour_manual(values=FIVETHIRTYEIGHT)
    plot += scale_x_continuous(breaks=[1, 4, 7, 10], labels=["Jan", "Apr", "Jul", "Oct"])
    plot += theme_bw(base_size=12)
    return plot + theme(legend_position="top")


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


def _rectangles(df):
    """df (lat, depth, ...) with each cell's edges, for geom_rect."""
    lat_low, lat_high = _cells(df.lat)
    depth_low, depth_high = _cells(df.depth)
    return df.assign(
        xmin=df.lat.map(lat_low), xmax=df.lat.map(lat_high),
        ymin=df.depth.map(depth_low), ymax=df.depth.map(depth_high),
    )


def zonal_sections(df_zonals, name, units):
    """The gridded page's zonal mean sections through the water column of each
    region (df_zonals: lat, depth, value, variable "Model"/"Observation",
    region): the model and observations side by side, then their bias, for
    each region in turn, stacked."""
    from plotnine import (aes, coord_cartesian, element_text, facet_wrap, geom_rect, ggplot, ggtitle, labs,
                          scale_fill_cmap, scale_fill_gradient2, scale_x_continuous, scale_y_reverse, theme,
                          theme_bw)

    def latitudes(values):
        return _degrees(values, "N", "S")

    parts = []
    for region in dict.fromkeys(df_zonals.region):
        data = _rectangles(df_zonals[df_zonals.region == region].dropna())
        ncol, nrow = _panels(data.variable.nunique())
        top = (
            ggplot(data)
            + geom_rect(aes(xmin="xmin", xmax="xmax", ymin="ymin", ymax="ymax", fill="value"))
            + facet_wrap("variable", ncol=ncol, nrow=nrow)
            + scale_y_reverse()
            + scale_fill_cmap("viridis")
            + scale_x_continuous(labels=latitudes)
            + theme_bw(base_size=10)
            + theme(legend_title=element_text(rotation=-90, va="center"))
            + labs(x="", y="Depth (m)", fill=f"{name} ({units})" if units else name)
            + coord_cartesian(expand=False)
            + ggtitle(region)
        )
        wide = data.pivot_table(index=["lat", "depth"], columns="variable", values="value").reset_index()
        wide = wide.dropna(subset=["Model", "Observation"]).assign(bias=lambda x: x.Model - x.Observation)
        bottom = (
            ggplot(_rectangles(wide))
            + geom_rect(aes(xmin="xmin", xmax="xmax", ymin="ymin", ymax="ymax", fill="bias"))
            + scale_y_reverse()
            + scale_fill_gradient2(low="blue", mid="white", high="red")
            + scale_x_continuous(labels=latitudes)
            + theme_bw(base_size=10)
            + theme(legend_title=element_text(rotation=-90, va="center"))
            + labs(x="", y="Depth (m)", fill=f"Bias ({units})" if units else "Bias")
            + coord_cartesian(expand=False)
        )
        parts.append(top / bottom)
    composition = parts[0]
    for part in parts[1:]:
        composition = composition / part
    return composition
