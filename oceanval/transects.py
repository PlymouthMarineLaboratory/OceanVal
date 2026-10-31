"""Validation along a transect: validate()'s transect option.

A transect is a straight line between two points, which must run north-south
(the same longitude at both ends) or east-west (the same latitude at both
ends), so that the values along it can be plotted against latitude or
longitude. The gridded notebooks (data/chunk_transect.pytemplate) extract the
gridded matchups along it with nctoolkit's to_transect, and plot them here: a
map of the line, the surface values in each month, and, for vertical
matchups, a section through the water column. Values are viridis and the bias
is blue-white-red, as in the rest of the report.
"""

import calendar
import math
import numbers
import re

import numpy as np
import pandas as pd

# the map's land and line, as nctoolkit's transect gallery draws them
LAND = "#d9dee0"
LINE = "#1b3b45"

# a vertical section is interpolated onto this many evenly spaced depths,
# from the shallowest to the deepest, as the matchup's own can be uneven
N_DEPTHS = 30

# the most points a transect is sampled at
MAX_STEPS = 1000

RULE = (
    "transect must run north-south (the same longitude at both ends) or "
    "east-west (the same latitude at both ends)"
)

TOO_OLD = (
    "transect needs nctoolkit 1.3.6 or later, which can extract transects "
    "(to_transect). Upgrade it, e.g. with pip install --upgrade nctoolkit"
)

# the land as one shape, made the first time a map is drawn
_LAND = None


def available():
    """Whether the installed nctoolkit can extract a transect."""
    from oceanval.quiet_nc import nc

    return hasattr(nc.DataSet, "to_transect")


def _is_number(value):
    return (
        isinstance(value, numbers.Real)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def check_transect(transect):
    """validate()'s transect, checked: None, or a dict of start and end, each
    [lon, lat] as floats.

    Raises TypeError or ValueError if it cannot be used, which includes an
    nctoolkit too old to extract it.
    """
    if transect is None:
        return None
    if not isinstance(transect, dict):
        raise TypeError(
            "transect must be a dict of start and end, each [lon, lat], "
            'e.g. {"start": [-30, 0], "end": [-30, 65]}'
        )
    if set(transect) != {"start", "end"}:
        raise ValueError("transect must have two keys, start and end, each [lon, lat]")
    ends = {}
    for name in ("start", "end"):
        point = transect[name]
        if not (
            isinstance(point, (list, tuple))
            and len(point) == 2
            and all(_is_number(value) for value in point)
        ):
            raise ValueError(f"transect's {name} must be [lon, lat], two numbers")
        lon, lat = float(point[0]), float(point[1])
        if not -180 <= lon <= 360:
            raise ValueError(f"transect's {name} longitude must be between -180 and 360")
        if not -90 <= lat <= 90:
            raise ValueError(f"transect's {name} latitude must be between -90 and 90")
        ends[name] = [lon, lat]
    if ends["start"] == ends["end"]:
        raise ValueError("transect's start and end are the same point")
    if ends["start"][0] != ends["end"][0] and ends["start"][1] != ends["end"][1]:
        raise ValueError(RULE)
    if not available():
        raise ValueError(TOO_OLD)
    return ends


def direction(transect):
    """"north-south" or "east-west"."""
    if transect["start"][0] == transect["end"][0]:
        return "north-south"
    return "east-west"


def _degrees(value):
    return f"{abs(value):.2f}".rstrip("0").rstrip(".")


def format_lon(lon):
    """A longitude as the report writes it: 30°W, 0°, 10.5°E."""
    lon = ((float(lon) + 180) % 360) - 180
    text = _degrees(lon)
    if text in ("0", "180"):
        return f"{text}°"
    return f"{text}°{'E' if lon > 0 else 'W'}"


def format_lat(lat):
    """A latitude as the report writes it: 10°S, 0°, 65°N."""
    text = _degrees(lat)
    if text == "0":
        return "0°"
    return f"{text}°{'N' if lat > 0 else 'S'}"


def format_depth(depth):
    """A depth as the report writes it: 3.04 m, 500 m."""
    return f"{_degrees(depth)} m"


def describe(transect):
    """Where the transect runs, in words: "north–south along 30°W, from 0°
    to 65°N"."""
    (lon0, lat0), (lon1, lat1) = transect["start"], transect["end"]
    if direction(transect) == "north-south":
        return (
            f"north–south along {format_lon(lon0)}, "
            f"from {format_lat(lat0)} to {format_lat(lat1)}"
        )
    return (
        f"east–west along {format_lat(lat0)}, "
        f"from {format_lon(lon0)} to {format_lon(lon1)}"
    )


def _wrap(lon):
    return ((lon + 180) % 360) - 180


def line_ends(transect):
    """The start and end the transect is drawn and extracted between, with
    longitudes in -180 to 180, as the matchups have them, unless that would
    take an east-west line the long way round the globe."""
    (lon0, lat0), (lon1, lat1) = transect["start"], transect["end"]
    if math.isclose(_wrap(lon1) - _wrap(lon0), lon1 - lon0):
        lon0, lon1 = _wrap(lon0), _wrap(lon1)
    return [lon0, lat0], [lon1, lat1]


def _coordinate(names, axis):
    """Which of names is the longitude ("lon") or latitude ("lat")."""
    found = [
        name
        for name in names
        if axis in name.lower() and "bnds" not in name and "bounds" not in name
    ]
    if not found:
        raise ValueError(f"The matchup has no {axis} coordinate")
    return found[0]


def transect_steps(path, transect):
    """How many points to sample the transect at: about one per grid cell of
    the matchup in path that it crosses, as finer steps add nothing the grid
    does not have."""
    import xarray as xr

    axis = "lat" if direction(transect) == "north-south" else "lon"
    with xr.open_dataset(path, decode_times=False) as data:
        name = _coordinate(list(data.coords) + list(data.variables), axis)
        values = np.asarray(data[name].values, dtype=float).ravel()
    values = np.unique(values[np.isfinite(values)])
    spacing = np.diff(values)
    spacing = spacing[spacing > 0]
    start, end = line_ends(transect)
    index = 1 if axis == "lat" else 0
    length = abs(end[index] - start[index])
    if len(spacing) == 0:
        return 2
    steps = int(round(length / float(np.median(spacing)))) + 1
    return int(min(max(steps, 2), MAX_STEPS))


def _lon_gaps(lons):
    """Longitudes in -180 to 180, sorted and unique, and the gap east of each
    to the next, round the globe."""
    lons = np.asarray(lons, dtype=float).ravel()
    lons = np.unique(_wrap(lons[np.isfinite(lons)]))
    if len(lons) == 0:
        return lons, lons
    return lons, np.append(np.diff(lons), 360 - (lons[-1] - lons[0]))


def wraps_round(lons):
    """Whether longitudes (cell centres, 1D or curvilinear) go all the way
    round the globe: no gap between them, round the globe, is much wider
    than a cell, as the one beyond the edges of a grid that does not is (two
    cells for a half degree grid stopping at 179.5°W and 179.5°E)."""
    values = np.asarray(lons, dtype=float)
    lons, gaps = _lon_gaps(values)
    if len(lons) < 2:
        return False
    if values.ndim > 1:
        # a cell is the step along a row of the grid
        steps = np.abs(_wrap(np.diff(values, axis=-1))).ravel()
    else:
        steps = np.diff(lons)
    steps = steps[np.isfinite(steps) & (steps > 0)]
    return len(steps) > 0 and gaps.max() <= 1.5 * float(np.median(steps))


def check_on_grid(path, transect):
    """Raise a ValueError if the transect runs along a longitude (north-south)
    or latitude (east-west) outside the matchup in path, where to_transect can
    only give missing values: such as 180° in a matchup made on a grid that
    stops at 179.5°W and 179.5°E."""
    import xarray as xr

    axis = "lon" if direction(transect) == "north-south" else "lat"
    with xr.open_dataset(path, decode_times=False) as data:
        name = _coordinate(list(data.coords) + list(data.variables), axis)
        values = np.asarray(data[name].values, dtype=float).ravel()
    values = values[np.isfinite(values)]
    start, _ = line_ends(transect)
    if axis == "lat":
        lat = start[1]
        if len(values) and not values.min() <= lat <= values.max():
            raise ValueError(
                f"the transect runs along {format_lat(lat)}, outside the matchup's "
                f"latitudes, {format_lat(values.min())} to {format_lat(values.max())}"
            )
        return
    lons, gaps = _lon_gaps(values)
    if len(lons) < 2 or wraps_round(values):
        return
    # the widest gap between the longitudes, round the globe, is outside the grid
    widest = int(gaps.argmax())
    west, east = lons[(widest + 1) % len(lons)], lons[widest]
    beyond = (start[0] - east) % 360
    if 1e-6 < beyond < gaps[widest] - 1e-6:
        raise ValueError(
            f"the transect runs along {format_lon(start[0])}, outside the matchup's "
            f"longitudes, {format_lon(west)} to {format_lon(east)}"
        )


def _is_time(data, name):
    if "time" in name.lower():
        return True
    if name in data.variables:
        return (
            np.issubdtype(data[name].dtype, np.datetime64)
            or str(data[name].attrs.get("axis", "")).upper() == "T"
        )
    return False


def _is_horizontal(name):
    lowered = name.lower()
    return (
        "lon" in lowered
        or "lat" in lowered
        or lowered in ("ncells", "cell", "cells", "node", "nod2", "x", "y", "i", "j")
    )


def depth_name(data, variable="observation"):
    """The name of the depth coordinate of a vertical matchup, as an xarray
    Dataset. It is the observations' own, so it can be depth, deptht, lev, z
    and so on: what the file says about its coordinates decides, then the
    usual names, then the one dimension that is neither time nor horizontal.
    """
    dims = list(data[variable].dims) if variable in data.variables else list(data.dims)
    candidates = [
        name for name in dims if not _is_time(data, name) and not _is_horizontal(name)
    ]
    for name in candidates:
        attrs = data[name].attrs if name in data.variables else {}
        standard_name = str(attrs.get("standard_name", "")).lower()
        if (
            str(attrs.get("axis", "")).upper() == "Z"
            or "positive" in attrs
            or "depth" in standard_name
        ):
            return name
    for name in candidates:
        lowered = name.lower()
        if lowered.startswith(("depth", "lev", "olevel")) or lowered in (
            "z",
            "zlev",
            "st_ocean",
            "nav_lev",
        ):
            return name
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError("The depth coordinate of the vertical matchup could not be identified")


def _time_column(df):
    found = [name for name in df.columns if "time" in str(name).lower() and "bnds" not in str(name)]
    return found[0] if found else None


def _positions(df, transect):
    """Where each row of a transect's data frame is along the line: its
    latitude, or its longitude."""
    axis = "lat" if direction(transect) == "north-south" else "lon"
    name = _coordinate([str(name) for name in df.columns], axis)
    return df[name].astype(float).round(6).values


def _tidy(frame):
    """frame, with the bias, and limited to the stretch of the line that has
    both model and observed values. Empty if fewer than two points have."""
    frame = frame.assign(bias=frame.model - frame.observation)
    both = frame.model.notna() & frame.observation.notna()
    frame.loc[~both, ["model", "observation", "bias"]] = np.nan
    if frame.position[both].nunique() < 2:
        return frame.iloc[0:0].reset_index(drop=True)
    low, high = frame.position[both].min(), frame.position[both].max()
    frame = frame[(frame.position >= low) & (frame.position <= high)]
    return frame.reset_index(drop=True)


def surface_frame(path, transect, steps):
    """The surface matchup in path along the transect, as a monthly
    climatology: a data frame of position (latitude, or longitude), month,
    model, observation and bias."""
    from oceanval.quiet_nc import nc

    check_on_grid(path, transect)
    start, end = line_ends(transect)
    ds = nc.open_data(path, checks=False)
    ds.subset(variables=["model", "observation"])
    ds.tmean("month")
    ds.to_transect(start=start, end=end, nsteps=steps)
    df = ds.to_dataframe().reset_index()
    time = _time_column(df)
    months = [getattr(value, "month", 1) for value in df[time]] if time else [1] * len(df)
    frame = pd.DataFrame(
        {
            "position": _positions(df, transect),
            "month": months,
            "model": df["model"].astype(float).values,
            "observation": df["observation"].astype(float).values,
        }
    )
    return _tidy(frame)


def _section_of(path, variable, transect, steps, depths=None):
    """One variable of the vertical matchup in path along the transect, as an
    annual mean on depths: a data frame of position, depth and the variable,
    with the depths it was interpolated to. If depths is None, they are N_DEPTHS
    evenly spaced ones from the shallowest level to the deepest, which is
    None if there are fewer than two levels."""
    from oceanval.quiet_nc import nc
    import xarray as xr

    start, end = line_ends(transect)
    ds = nc.open_data(path, checks=False)
    ds.subset(variables=variable)
    ds.tmean()
    ds.to_transect(start=start, end=end, nsteps=steps)
    if depths is None:
        levels = sorted(set(ds.levels))
        if len(levels) < 2:
            return None, None
        depths = np.linspace(min(levels), max(levels), N_DEPTHS)
    ds.vertical_interp(levels=[float(depth) for depth in depths], fixed=True)
    ds.run()
    with xr.open_dataset(ds.current[0], decode_times=False) as data:
        name = depth_name(data, variable)
    df = ds.to_dataframe().reset_index()
    frame = pd.DataFrame(
        {
            "position": _positions(df, transect),
            "depth": np.abs(df[name].astype(float).values),
            variable: df[variable].astype(float).values,
        }
    )
    return frame, depths


def section_frame(path, transect, steps):
    """The vertical matchup in path along the transect, as an annual mean
    interpolated onto N_DEPTHS evenly spaced depths: a data frame of
    position, depth (in metres, positive down), model, observation and
    bias. None if the matchup has fewer than two depths.

    The matchup's depths are the observations'. They can be uneven, and the
    model and observations can each have a depth axis of their own, which
    would pair every depth of one with every depth of the other if they
    were not extracted separately, so they are, onto the same depths.
    """
    check_on_grid(path, transect)
    observation, depths = _section_of(path, "observation", transect, steps)
    if observation is None:
        return None
    model, _ = _section_of(path, "model", transect, steps, depths)
    # each is joined by the depth it was interpolated to, the same for both
    for frame in (observation, model):
        frame["level"] = np.abs(frame.depth.values[:, None] - depths[None, :]).argmin(axis=1)
    frame = observation.merge(
        model.drop(columns="depth"), on=["position", "level"], how="outer"
    )
    frame["depth"] = depths[frame.level.values]
    return _tidy(frame[["position", "depth", "model", "observation"]])


def unit_text(path, variable):
    """The matchup's unit, as the report's colour bars show it: tidied as
    fix_unit does, in mathtext, and °C for temperature, as in
    chunk_seasonal."""
    import xarray as xr

    from oceanval.tidiers import fix_unit

    if variable == "temperature":
        return "°C"
    with xr.open_dataset(path, decode_times=False) as data:
        unit = data["model"].attrs.get("units") or data["observation"].attrs.get("units")
    unit = fix_unit(str(unit or ""))
    unit = re.sub(r"<sup>(.*?)</sup>", r"$^{\1}$", unit)
    return re.sub(r"<sub>(.*?)</sub>", r"$_{\1}$", unit)


def _label(name, unit):
    name = name[:1].upper() + name[1:]
    return f"{name} ({unit})" if unit else name


def _extend(values, low, high):
    """Which ends of a colour bar need arrows, for values beyond it."""
    below = len(values) > 0 and values.min() < low
    above = len(values) > 0 and values.max() > high
    return "both" if below and above else "min" if below else "max" if above else "neither"


def _colour_scales(frame, log=False):
    """The colour scales of a transect's plots: model and observations on
    one viridis scale, limited to the 2nd and 98th percentiles of the two
    together (log10 if log, as for chlorophyll), and the bias on
    blue-white-red, symmetric about zero and limited to its 98th percentile.

    Returns (values norm, values extend, bias norm, bias extend).
    """
    from matplotlib.colors import LogNorm, Normalize

    values = pd.concat([frame.model, frame.observation]).dropna()
    low, high = (float(x) for x in np.percentile(values, [2, 98]))
    if high <= low:
        pad = abs(low) * 0.01 or 0.5
        low, high = low - pad, high + pad
    if log and low > 0:
        values_norm = LogNorm(vmin=low, vmax=high)
    else:
        values_norm = Normalize(vmin=low, vmax=high)
    bias = frame.bias.dropna()
    largest = float(np.percentile(bias.abs(), 98)) if len(bias) else 0.0
    if largest <= 0:
        largest = float(bias.abs().max()) if len(bias) else 0.0
    if largest <= 0:
        largest = 1.0
    bias_norm = Normalize(vmin=-largest, vmax=largest)
    return (
        values_norm,
        _extend(values, low, high),
        bias_norm,
        _extend(bias, -largest, largest),
    )


def _position_axis(transect):
    """The label and tick formatter of the latitude or longitude axis."""
    from matplotlib.ticker import FuncFormatter

    if direction(transect) == "north-south":
        return "Latitude", FuncFormatter(lambda value, _: format_lat(value))
    return "Longitude", FuncFormatter(lambda value, _: format_lon(value))


# the panels of each plot: title, and the data frame's column
PANELS = (
    ("Model", "model"),
    ("Observation", "observation"),
    ("Model − observation", "bias"),
)


def _colour_bars(fig, axes, meshes, values_extend, bias_extend, name, unit):
    """One colour bar for the model and observations, which share a scale,
    and one for the bias."""
    fig.colorbar(
        meshes[1], ax=list(axes[:2]), extend=values_extend, label=_label(name, unit)
    )
    fig.colorbar(
        meshes[2],
        ax=axes[2],
        extend=bias_extend,
        label=_label("model − observation", unit),
    )


def plot_months(frame, transect, unit, name, log=False):
    """The model, observations and bias along the transect in each month.

    A north-south line is shown by month (across) and latitude (up), and an
    east-west one by longitude (across) and month (down), so north is up
    and east is right, as on the map.
    """
    import matplotlib.pyplot as plt

    months = sorted(int(month) for month in frame.month.unique())
    positions = np.sort(frame.position.unique())
    values_norm, values_extend, bias_norm, bias_extend = _colour_scales(frame, log)
    position_label, formatter = _position_axis(transect)
    month_names = [calendar.month_abbr[month] for month in months]
    north_south = direction(transect) == "north-south"
    # about as wide as the report's other figures
    if north_south:
        fig, axes = plt.subplots(1, 3, figsize=(10, 4.6), sharey=True, layout="constrained")
    else:
        fig, axes = plt.subplots(
            3, 1, figsize=(8.5, 8.5), sharex=True, sharey=True, layout="constrained"
        )
    meshes = []
    for ax, (title, column) in zip(axes, PANELS):
        grid = frame.pivot_table(
            index="position", columns="month", values=column, dropna=False
        ).reindex(index=positions, columns=months)
        values = np.ma.masked_invalid(grid.values)
        colours = "bwr" if column == "bias" else "viridis"
        norm = bias_norm if column == "bias" else values_norm
        if north_south:
            mesh = ax.pcolormesh(
                np.arange(len(months)), positions, values,
                cmap=colours, norm=norm, shading="nearest",
            )
            # a third of the figure is too narrow for the month names side by side
            ax.set_xticks(range(len(months)), month_names, rotation=90)
            ax.yaxis.set_major_formatter(formatter)
            ax.set_xlabel("Month")
        else:
            mesh = ax.pcolormesh(
                positions, np.arange(len(months)), values.T,
                cmap=colours, norm=norm, shading="nearest",
            )
            ax.set_yticks(range(len(months)), month_names)
            ax.xaxis.set_major_formatter(formatter)
            ax.set_ylabel("Month")
        # what has no values shows as land, as on the map
        ax.set_facecolor(LAND)
        ax.set_title(title)
        meshes.append(mesh)
    if north_south:
        axes[0].set_ylabel(position_label)
    else:
        # January at the top, in all three, as they share the month axis
        axes[0].invert_yaxis()
        axes[-1].set_xlabel(position_label)
    _colour_bars(fig, axes, meshes, values_extend, bias_extend, name, unit)
    return fig


def plot_line(frame, transect, unit, name):
    """The model and observations along the transect, for a matchup with a
    single time step, which has no months to show."""
    import matplotlib.pyplot as plt

    data = frame.groupby("position")[["model", "observation"]].mean()
    position_label, formatter = _position_axis(transect)
    fig, ax = plt.subplots(figsize=(8, 4.2), layout="constrained")
    ax.plot(data.index, data.model, color="#d62728", linewidth=2, label="Model")
    ax.plot(data.index, data.observation, color="#1f77b4", linewidth=2, label="Observation")
    ax.xaxis.set_major_formatter(formatter)
    ax.set_xlabel(position_label)
    ax.set_ylabel(_label(name, unit))
    ax.grid(alpha=0.3)
    ax.legend(frameon=False)
    return fig


def plot_section(frame, transect, unit, name, log=False):
    """The model, observations and bias along the transect through the water
    column: latitude or longitude across, and depth down."""
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    positions = np.sort(frame.position.unique())
    depths = np.sort(frame.depth.unique())
    values_norm, values_extend, bias_norm, bias_extend = _colour_scales(frame, log)
    position_label, formatter = _position_axis(transect)
    fig, axes = plt.subplots(1, 3, figsize=(10, 4.2), sharey=True, layout="constrained")
    meshes = []
    for ax, (title, column) in zip(axes, PANELS):
        grid = frame.pivot_table(
            index="depth", columns="position", values=column, dropna=False
        ).reindex(index=depths, columns=positions)
        mesh = ax.pcolormesh(
            positions, depths, np.ma.masked_invalid(grid.values),
            cmap="bwr" if column == "bias" else "viridis",
            norm=bias_norm if column == "bias" else values_norm,
            shading="nearest",
        )
        # the seabed, and anything else without values, shows as land
        ax.set_facecolor(LAND)
        # few enough latitudes or longitudes for their labels to fit a third of the figure
        ax.xaxis.set_major_locator(MaxNLocator(4))
        ax.xaxis.set_major_formatter(formatter)
        ax.set_xlabel(position_label)
        ax.set_title(title)
        meshes.append(mesh)
    axes[0].set_ylabel("Depth (m)")
    # the surface at the top, and the axis from the shallowest depth to the
    # deepest, rather than half a cell beyond each
    axes[0].set_ylim(depths.max(), depths.min())
    _colour_bars(fig, axes, meshes, values_extend, bias_extend, name, unit)
    return fig


def _land():
    """The land as one shape, from the world borders oceanval ships, so the
    map needs no download and outlines only the coast."""
    global _LAND
    if _LAND is None:
        from shapely.ops import unary_union

        from oceanval.pyplots import world_map

        _LAND = unary_union([shape.buffer(0) for shape in world_map().geometry])
    return _LAND


def map_extent(transect):
    """[west, east, south, north] of the transect's map: the line with room
    around it, about as nctoolkit's transect gallery frames its own."""
    start, end = line_ends(transect)
    lons = sorted([start[0], end[0]])
    lats = sorted([start[1], end[1]])
    length = max(lons[1] - lons[0], lats[1] - lats[0])
    along = max(5.0, 0.2 * length)
    across = max(10.0, 0.5 * length)
    if direction(transect) == "north-south":
        west, east = lons[0] - across, lons[1] + across
        south, north = lats[0] - along, lats[1] + along
    else:
        west, east = lons[0] - along, lons[1] + along
        south, north = lats[0] - across, lats[1] + across
    if lons[1] <= 180:
        west, east = max(west, -180.0), min(east, 180.0)
    return [west, east, max(south, -90.0), min(north, 90.0)]


def plot_map(transect):
    """Where the transect runs: the line, its ends and their latitudes or
    longitudes, over a map of the land, as nctoolkit's transect gallery
    draws it."""
    import cartopy.crs as ccrs
    import matplotlib.pyplot as plt
    from matplotlib.transforms import offset_copy

    start, end = line_ends(transect)
    west, east, south, north = map_extent(transect)
    # a line given beyond 180°E is drawn on a map centred on the date line
    central = 180 if max(start[0], end[0]) > 180 else 0
    width = 6.5
    height = min(max(width * (north - south) / (east - west), 3.0), 8.0)
    fig = plt.figure(figsize=(width, height))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=central))
    ax.set_extent([west, east, south, north], crs=ccrs.PlateCarree())
    ax.add_geometries(
        [_land()], crs=ccrs.PlateCarree(),
        facecolor=LAND, edgecolor="black", linewidth=0.5, zorder=1,
    )
    grid = ax.gridlines(
        draw_labels=True, linewidth=0.5, color="grey", linestyle="--", alpha=0.8, zorder=2
    )
    grid.right_labels = False
    grid.bottom_labels = False
    lons, lats = [start[0], end[0]], [start[1], end[1]]
    ax.plot(lons, lats, transform=ccrs.PlateCarree(), color=LINE, linewidth=2.5, zorder=10)
    ax.scatter(
        lons, lats, transform=ccrs.PlateCarree(),
        color=LINE, s=45, edgecolor="white", linewidth=1.2, zorder=11,
    )
    # each end is labelled outside the line, so that the labels cannot meet
    # however short it is: the northern end above and the southern below, to
    # the side of a north-south line, or the western end to the left of its dot
    # and the eastern to the right, above an east-west one
    north_south = direction(transect) == "north-south"
    geodetic = ccrs.PlateCarree()._as_mpl_transform(ax)
    low, high = sorted((start, end), key=lambda point: point[1] if north_south else point[0])
    for point, is_high in ((low, False), (high, True)):
        if north_south:
            shift, align = (8, 3 if is_high else -3), ("left", "bottom" if is_high else "top")
            label = format_lat(point[1])
        else:
            shift, align = (5 if is_high else -5, 6), ("left" if is_high else "right", "bottom")
            label = format_lon(point[0])
        ax.text(
            point[0], point[1], label,
            transform=offset_copy(geodetic, fig=fig, x=shift[0], y=shift[1], units="points"),
            color=LINE, fontsize=10, fontweight="bold",
            ha=align[0], va=align[1], zorder=12,
        )
    return fig
