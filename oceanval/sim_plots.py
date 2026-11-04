"""The figures of the simulation comparison report, which its notebooks draw from
what compare_simulations() saved (see oceanval.sim_compare).

They are drawn as the analysis report's are (see oceanval.analysis_plots): each by
running its own code, a script with everything it uses written out, which the report's
"Copy code to reproduce figure" button copies. A figure of every simulation has
the simulations in the colours the comparison gives them (info["colours"]): lines are
a colour for each simulation, maps are a panel for each, on one colour scale.

The maps of each month and the maps of changes, which are too many to put the
simulations in one figure, are those of analysis_plots, a figure for each simulation, on
the scale all the simulations share.
"""

import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from oceanval import analysis_plots as ap
from oceanval.analysis import PEAK_DAY, PEAK_VALUE
from oceanval.analysis_plots import (
    CYCLE_AXIS,
    LINE_STYLE,
    NOT_STATIONS,
    YEAR_TICKS,
    Drawn,
    _all_values,
    _call,
    _code,
    _listing,
    _path,
    _plot_grid,
    _py,
    _run,
    _scale,
    cycle_code,
    legend,
    series_legend,
    show,
)
from oceanval.sim_compare import load

__all__ = ["show"]

# a figure of the series has panels this many across
SERIES_COLUMNS = 2
# a section or a month by position grid has a panel for each simulation, this many across
GRID_COLUMNS = 2


def entry(info, key):
    return ap.entry(info, key)


def names_of(info):
    return [sim["name"] for sim in info["simulations"]]


def folder_of(results_dir, info, sim):
    """The folder of one simulation's results."""
    return os.path.join(results_dir, next(x["key"] for x in info["simulations"] if x["name"] == sim))


def _about(info):
    return f"the OceanVal comparison of {', '.join(names_of(info))} (oceanval {info['oceanval']})"


def _own(variable, sim):
    """The entry of one simulation for the variable (as analyze() saves it)."""
    return variable["sims"][sim]


def _having(variable, kind):
    """The simulations the variable has kind for, in order."""
    return [sim for sim, own in variable["sims"].items() if own.get(kind)]


def _shared_limits(paths, key, low=2, high=98):
    """The 2nd and 98th percentiles of variable key over every file in paths,
    or None if they are all missing or all the same."""
    values = _all_values(paths, key)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return None
    lo, hi = np.percentile(values, [low, high])
    return None if lo == hi else [float(lo), float(hi)]


def _simulations_listing(info, sims, files):
    """The code of the list of the simulations drawn: each one's name, colour
    and file."""
    rows = [(sim, info["colours"][sim], path) for sim, path in zip(sims, files)]
    return _listing("simulations", rows, "each simulation: its name, its colour and the file of what it shows")


# ---- the maps ----


def _drawn_maps(code_for, info, name):
    """Draw the figure code_for(False) draws or, if that fails, the one code_for(True)
    draws, with each map regridded to a regular lon-lat grid."""
    try:
        code = code_for(False)
        _run(code, name)
        return Drawn(code, False, {}, _about(info))
    except Exception:
        plt.close("all")
    code = code_for(True)
    _run(code, name)
    return Drawn(code, True, {}, _about(info))


def _panels_code(variable_name, key, label, comment, sims, paths, limits, regrid, colours=None, font_extra=None, ncol=3):
    """The code of a figure of one map for each simulation, from the one-time
    datasets in paths, on one colour scale (limits), with nctoolkit's panel_plot."""
    columns = min(ncol, len(sims))
    size = ap._panel_size(paths[0], len(sims), columns)
    if _on_mesh(paths[0]):
        return _mesh_panels_code(key, label, comment, sims, paths, limits, colours, font_extra, columns, size)
    maps = [(sim, path, _plot_grid(path) if regrid else None) for sim, path in zip(sims, paths)]
    font = (
        "# the figure's size, in inches, and its font size: the report shows it at about the width of its page\n"
        f"size = {_py(size)}\n"
        f"font_size = {ap.PANEL_FONT}"
    )
    imports, font, draw = ap._panel_draw(key, label, font, colours=colours)
    scale = (
        "# the colour scale all the simulations share: the 2nd to the 98th percentile of all of them\n"
        f"limits = {_py(limits)}"
        if limits is not None
        else "# the colour scale all the simulations share: None leaves it to nctoolkit\nlimits = None"
    )
    open_maps = "\n".join(
        [
            "# a map for each simulation (regridded to a regular lon-lat grid if the model's grid cannot be drawn)",
            "panels = {}",
            "for title, path, grid in maps:",
            "    ds_map = nc.open_data(path, checks=False)",
            f"    ds_map.subset(variables={_py(key)})",
            "    ds_map.run()",
            "    if grid:",
            "        ds_map.to_latlon(**grid)",
            "        ds_map.run()",
            "    panels[title] = ds_map",
            f"ncol = {columns}",
        ]
    )
    return _code(
        imports,
        comment,
        _listing("maps", maps, "each simulation's name, the file of its map and the grid to regrid it to, if any"),
        scale,
        font,
        open_maps,
        draw,
        font_extra,
    )


def _on_mesh(path):
    """Whether the file in path is raw FVCOM output, on its own mesh (see oceanval.mesh_maps)."""
    import xarray as xr

    with xr.open_dataset(path, decode_times=False) as ds:
        return bool(ds.attrs.get("mesh"))


def _mesh_panels_code(key, label, comment, sims, paths, limits, colours, font_extra, columns, size):
    """_panels_code of raw FVCOM output: each simulation's map is drawn on its own mesh
    (see oceanval.mesh_maps.mesh_sim_plot), on one colour scale under one colour bar."""
    maps = [(sim, path) for sim, path in zip(sims, paths)]
    font = (
        "# the figure's size, in inches, and its font size: the report shows it at about the width of its page\n"
        f"size = {_py(size)}\n"
        f"font_size = {ap.PANEL_FONT}"
    )
    scale = (
        "# the colour scale all the simulations share: the 2nd to the 98th percentile of all of them\n"
        f"limits = {_py(limits)}"
        if limits is not None
        else "# the colour scale all the simulations share: None leaves it to oceanval\nlimits = None"
    )
    options = {"ncol": columns, "limits": ap._Code("limits"), "legend": label, "grid_labels": False, "size": ap._Code("size")}
    if colours:
        options["colours"] = colours
    call = _call("mesh_sim_plot", options, args=(ap._Code("maps"), key))
    draw = 'with plt.rc_context({"font.size": font_size}):\n' + "\n".join("    " + line for line in call.split("\n"))
    return _code(
        "import matplotlib.pyplot as plt\nfrom oceanval.mesh_maps import mesh_sim_plot\n",
        comment,
        ap.MESH_NOTE,
        _listing("maps", maps, "each simulation's name and the file of its map"),
        scale,
        font,
        draw,
        font_extra,
    )


def annual_maps(results_dir, key, layer):
    """The map of the annual mean of a variable's climatology in one layer, a panel
    for each simulation, on one colour scale (the 2nd to the 98th percentile of all of
    them)."""
    info = load(results_dir)
    variable = entry(info, key)
    sims = _having(variable, "climatology")
    saved = _own(variable, sims[0])["climatology"]["layers"][layer]
    paths = [
        _path(folder_of(results_dir, info, sim), _own(variable, sim)["climatology"]["layers"][layer]["annual"])
        for sim in sims
    ]
    limits = _shared_limits(paths, key)
    label = legend(variable["name"], saved["units"])

    def code_for(regrid):
        return _panels_code(
            variable["name"], key, label,
            f"# the annual mean of {variable['name']}'s climatology in each simulation: {saved['title'].lower()}",
            sims, paths, limits, regrid,
        )

    return _drawn_maps(code_for, info, "annual_maps")


def monthly_maps(results_dir, key, layer, sim):
    """The maps of each month's mean of a variable's climatology in one layer in one
    simulation, on the colour scale all the simulations share."""
    info = load(results_dir)
    variable = entry(info, key)
    paths = [
        _path(folder_of(results_dir, info, other), _own(variable, other)["climatology"]["layers"][layer]["monthly"])
        for other in _having(variable, "climatology")
    ]
    return ap.monthly_maps(folder_of(results_dir, info, sim), key, layer, limits=_shared_limits(paths, key))


def change_maps(results_dir, key, layer, sim):
    """The maps of a variable's map changes in one layer in one simulation, on the
    colour scales all the simulations share (see analysis_plots.change_maps)."""
    info = load(results_dir)
    variable = entry(info, key)
    others = {"periods": [], "changes": []}
    plot = _own(variable, sim)["map_changes"]["plot"]
    for other in _having(variable, "map_changes"):
        if other == sim:
            continue
        folder = folder_of(results_dir, info, other)
        saved = _own(variable, other)["map_changes"]["layers"][layer]
        if plot != "changes":
            others["periods"] += [_path(folder, file) for file in [saved["historical"], *saved["futures"]]]
        else:
            others["periods"].append(_path(folder, saved["historical"]))
        if plot != "absolute":
            others["changes"] += [_path(folder, file) for file in saved["changes"]]
    return ap.change_maps(folder_of(results_dir, info, sim), key, layer, also=others)


def _phenology_paths(results_dir, info, variable):
    sims = _having(variable, "phenology")
    return sims, [_path(folder_of(results_dir, info, sim), _own(variable, sim)["phenology"]["file"]) for sim in sims]


def peak_day_maps(results_dir, key):
    """The maps of the day of the year of a variable's seasonal peak, a panel for each
    simulation, on a cyclic colour scale labelled by month."""
    info = load(results_dir)
    variable = entry(info, key)
    sims, paths = _phenology_paths(results_dir, info, variable)
    first = sims[0]
    attrs = ap._phenology_attrs(folder_of(results_dir, info, first), _own(variable, first))
    starts, names = ap._month_ticks(attrs)
    days = int(attrs["days"])
    months = "\n".join(
        [
            "# the day each month starts on in the model's calendar, and the months' names",
            f"month_starts = {_py(starts)}",
            f"month_names = {_py(names)}",
            "# the colour bar is labelled by month: the colours are cyclic, so the end of the year is close in colour to its start",
            "fig = plt.gcf()",
            'bars = [ax for ax in fig.axes if not hasattr(ax, "projection")]',
            "if bars:",
            "    bar = bars[-1]",
            "    if bar.get_position().height > bar.get_position().width:",
            "        bar.set_yticks(month_starts)",
            "        bar.set_yticklabels(month_names)",
            "    else:",
            "        bar.set_xticks(month_starts)",
            "        bar.set_xticklabels(month_names)",
        ]
    )

    def code_for(regrid):
        code = _panels_code(
            variable["name"], PEAK_DAY, "Day of the seasonal peak",
            f"# the day of the year of the seasonal peak of surface {variable['name']} in each simulation",
            sims, paths, [1, days], regrid, colours="twilight", font_extra=months,
        )
        # the scale is the whole year, not the percentiles of the maps
        return code.replace(
            "# the colour scale all the simulations share: the 2nd to the 98th percentile of all of them",
            "# the colour scale all the simulations share: the whole year",
        )

    return _drawn_maps(code_for, info, "peak_day_maps")


def peak_value_maps(results_dir, key):
    """The maps of the value of a variable's seasonal peak, a panel for each
    simulation, on one colour scale."""
    info = load(results_dir)
    variable = entry(info, key)
    sims, paths = _phenology_paths(results_dir, info, variable)
    limits = _shared_limits(paths, PEAK_VALUE)

    def code_for(regrid):
        return _panels_code(
            variable["name"], PEAK_VALUE, legend(f"Peak {variable['name']}", variable["units"]),
            f"# the seasonal peak of surface {variable['name']} in each simulation: the highest value of its daily climatology",
            sims, paths, limits, regrid,
        )

    return _drawn_maps(code_for, info, "peak_value_maps")


def daily_cycle(results_dir, key):
    """The mean over the model's domain of a variable's daily climatology at the
    surface, a line for each simulation with its peak marked. Drawn.found holds
    "peaks", for each simulation its "day", its "month_day" and its "value" (all None if
    there are no values), and "notes", for each simulation what the report must say
    about how its domain's means were worked out (which may be nothing)."""
    info = load(results_dir)
    variable = entry(info, key)
    sims = _having(variable, "phenology")
    files = [_path(folder_of(results_dir, info, sim), _own(variable, sim)["phenology"]["daily"]) for sim in sims]
    starts, names = ap._month_ticks(ap._phenology_attrs(folder_of(results_dir, info, sims[0]), _own(variable, sims[0])))
    code = _code(
        "import matplotlib.pyplot as plt\nimport numpy as np\n"
        "from oceanval.analysis import day_order\nfrom oceanval.analysis_plots import domain_mean\n",
        f"# the area-weighted mean over the model's domain of each day of {variable['name']}'s daily climatology",
        _simulations_listing(info, sims, files),
        "# the day each month starts on, and the months' names",
        f"month_starts = {_py(starts)}",
        f"month_names = {_py(names)}",
        _call("fig, ax = plt.subplots", {"figsize": (8, 4.0)}),
        "peaks = {}",
        "notes = {}",
        "longest = 0",
        "for name, colour, path in simulations:",
        f"    _, values, notes[name] = domain_mean(path, {_py(key)})",
        "    # the days in order through the year, in the model's calendar, and the month and day of each",
        f"    order, month_days = day_order(path, {_py(key)})",
        "    values = values[order]",
        "    days = np.arange(1, len(values) + 1)",
        "    longest = max(longest, len(values))",
        "    ax.plot(days, values, color=colour, label=name)",
        "    # the peak, marked by a dashed line",
        "    top = int(np.nanargmax(values)) if np.isfinite(values).any() else None",
        "    if top is None:",
        "        peaks[name] = {\"day\": None, \"month_day\": None, \"value\": None}",
        "    else:",
        '        ax.axvline(days[top], color=colour, linestyle="--", linewidth=1)',
        '        ax.plot(days[top], values[top], marker="o", color=colour)',
        "        peaks[name] = {\"day\": int(days[top]), \"month_day\": month_days[top], \"value\": float(values[top])}",
        "ax.set_xticks(month_starts)",
        "ax.set_xticklabels(month_names)",
        "ax.set_xlim(1, longest)",
        f"ax.set_ylabel({_py(legend(variable['name'], variable['units']))})",
        LINE_STYLE,
        "fig.legend(*ax.get_legend_handles_labels(), loc=\"lower center\", ncol=len(simulations), frameon=False, fontsize=9)",
        "fig.tight_layout(rect=(0, 0.08, 1, 1))",
    )
    done = _run(code, "sim_daily_cycle")
    return Drawn(code, False, {"peaks": done["peaks"], "notes": done["notes"]}, _about(info))


# ---- the time series ----


def _series_code(info, sims, files, prepare, table, x, ylabel, axis_lines, panels, hollow=False):
    """The code of a figure of the series in files, a CSV file for each simulation
    of sims: prepare the code that makes table, the series to draw, from frame (every
    simulation's, read); x the code of the x of each line's part of table; axis_lines
    the code that sets each axes' x axis; panels the names of the panels, each a series
    of its own; and hollow, whether years that are the mean of fewer than 12 months are
    drawn as open circles."""
    columns = min(SERIES_COLUMNS, len(panels))
    nrow = int(np.ceil(len(panels) / columns))
    imports = "import matplotlib.pyplot as plt\nimport pandas as pd\n"
    if "calendar." in axis_lines:
        imports = "import calendar\n\n" + imports
    marks = [
        "    ax.plot(x, y, color=colour, label=name, linewidth=1.6)",
    ]
    if hollow:
        marks += [
            "    # years that are the mean of fewer than 12 months, as open circles",
            "    hollow = ~part.complete.values.astype(bool) & (part.months.values > 0)",
            "    if hollow.any():",
            '        ax.plot(x[hollow], y[hollow], "o", markerfacecolor="white", markeredgecolor=colour, markersize=5, linestyle="none")',
        ]
    size = (8, 4.4) if len(panels) == 1 else (4.8 * columns, 3.0 * nrow + 0.7)
    return _code(
        imports,
        "# the series of every simulation, one after the other",
        _simulations_listing(info, sims, files),
        "frame = pd.concat(\n"
        "    [pd.read_csv(path).assign(simulation=name) for name, _, path in simulations], ignore_index=True\n"
        ")",
        prepare.rstrip("\n"),
        f"# the panels: each is a series of its own\npanels = {_py(panels)}",
        _call(
            "fig, grid = plt.subplots",
            {"nrows": nrow, "ncols": columns, "figsize": size, "sharex": True, "squeeze": False},
        ),
        "for series, ax in zip(panels, grid.flat):",
        *(["    ax.set_title(series, fontsize=10)"] if len(panels) > 1 else []),
        "    for name, colour, _ in simulations:",
        f"        part = {table}[({table}.simulation == name) & ({table}.name == series)]",
        f"        x = {x}",
        "        y = part.value.values",
        *("    " + line for line in marks),
        "    " + "\n    ".join(axis_lines.split("\n")),
        "    " + "\n    ".join(LINE_STYLE.split("\n")),
        "for ax in list(grid.flat)[len(panels):]:",
        "    ax.set_visible(False)",
        "for ax in grid[:, 0]:",
        f"    ax.set_ylabel({_py(ylabel)})",
        "handles, labels = grid.flat[0].get_legend_handles_labels()",
        'fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False, fontsize=9)',
        "fig.tight_layout(rect=(0, 0.07, 1, 1))",
    )


def _series_names(files, kinds):
    """The names of the series of the kinds in the CSV files, in order: the domain first."""
    frames = [pd.read_csv(path) for path in files]
    frame = pd.concat(frames, ignore_index=True)
    frame = frame[frame.kind.isin(kinds)]
    names = list(dict.fromkeys(frame.name))
    domain = [name for name in names if frame[frame.name == name].kind.iloc[0] == "domain"]
    return domain + [name for name in names if name not in domain]


def _series_files(results_dir, info, variable, kind, layer):
    sims = _having(variable, kind)
    saved = [_own(variable, sim)[kind]["layers"][layer] for sim in sims]
    files = [_path(folder_of(results_dir, info, sim), one["csv"]) for sim, one in zip(sims, saved)]
    return sims, saved, files


def long_term_plot(results_dir, key, layer):
    """The long-term series: the annual mean over the domain and over each region, by
    year, a line for each simulation in a panel for each series. Years that are the
    mean of fewer than the 12 months are shown as open circles. Drawn.found holds
    "regions", the number of regions drawn."""
    info = load(results_dir)
    variable = entry(info, key)
    sims, saved, files = _series_files(results_dir, info, variable, "long_term", layer)
    panels = _series_names(files, ("domain", "region"))
    code = _series_code(
        info, sims, files, NOT_STATIONS, "frame", "part.year.values",
        series_legend(_own(variable, sims[0]), saved[0]), YEAR_TICKS, panels, hollow=True,
    )
    _run(code, "sim_long_term_plot")
    return Drawn(code, False, {"regions": max(len(panels) - 1, 0)}, _about(info))


# the columns that tell the series of a cycle apart
CYCLE_BY = ("simulation", "kind", "name")


def seasonal_cycle_regions(results_dir, key, layer):
    """The mean seasonal cycle: the mean of each month of the year, over the years of the
    series, over the domain and over each region, a line for each simulation in a panel for
    each series. Drawn.found holds "means", for each simulation the domain's, by month."""
    info = load(results_dir)
    variable = entry(info, key)
    sims, saved, files = _series_files(results_dir, info, variable, "seasonal", layer)
    panels = _series_names(files, ("domain", "region"))
    code = _series_code(
        info, sims, files, NOT_STATIONS + cycle_code(CYCLE_BY), "cycle", "part.month.values",
        legend(variable["name"], saved[0]["units"]), CYCLE_AXIS, panels,
    )
    cycle = _run(code, "sim_seasonal_cycle_regions")["cycle"]
    domain = cycle[cycle.kind == "domain"]
    means = {sim: dict(zip(part.month, part.value)) for sim, part in domain.groupby("simulation", sort=False)}
    return Drawn(code, False, {"means": means}, _about(info))


def station_plot(results_dir, key, kind, layer):
    """The series at each station, by year (the long-term series) or the mean of each
    month of the year (the seasonal series), a line for each simulation in a panel for each station, or None
    if there are no stations. Drawn.found holds "names", the stations'."""
    info = load(results_dir)
    variable = entry(info, key)
    sims, saved, files = _series_files(results_dir, info, variable, kind, layer)
    panels = _series_names(files, ("station",))
    if not panels:
        return None
    prepare = "# the series in the model cell nearest each station\n" 'frame = frame[frame.kind == "station"]\n'
    if kind == "long_term":
        code = _series_code(
            info, sims, files, prepare, "frame", "part.year.values", series_legend(_own(variable, sims[0]), saved[0]),
            YEAR_TICKS, panels, hollow=True,
        )
    else:
        code = _series_code(
            info, sims, files, prepare + cycle_code(CYCLE_BY), "cycle", "part.month.values",
            legend(variable["name"], saved[0]["units"]), CYCLE_AXIS, panels,
        )
    _run(code, "sim_station_plot")
    return Drawn(code, False, {"names": panels}, _about(info))


# ---- the transects ----


def transect_map(results_dir, key, index):
    """The map of where one transect of a variable runs, which is the same for every
    simulation (see analysis_plots.transect_map)."""
    info = load(results_dir)
    variable = entry(info, key)
    return ap.transect_map(folder_of(results_dir, info, _having(variable, "transects")[0]), key, index)


def transect_text(results_dir, key, index, option):
    """What the report says of one option of one transect of a variable (see
    analysis_plots.transect_text), from the first simulation's."""
    info = load(results_dir)
    variable = entry(info, key)
    return ap.transect_text(_own(variable, _having(variable, "transects")[0]), index, option)


def _grid_size(panels, width, height):
    columns = min(GRID_COLUMNS, panels)
    return columns, int(np.ceil(panels / columns)), (width * columns + 1.4, height * int(np.ceil(panels / columns)) + 0.6)


def _sim_frames_code(info, sims, files):
    return _code(
        _simulations_listing(info, sims, files),
        "frames = {name: pd.read_csv(path) for name, _, path in simulations}",
    )


def _sections_code(info, sims, files, line, limits, extend, cmap, label, size, columns, rows):
    """The code of a section through the water column along a transect, a panel
    for each simulation on one colour scale and with one colour bar."""
    position_code, position_label = ap._position_text(line)
    return _code(
        "import matplotlib.pyplot as plt\nimport numpy as np\nimport pandas as pd\n"
        "from matplotlib.ticker import FuncFormatter, MaxNLocator\n",
        _sim_frames_code(info, sims, files),
        "\n".join(position_code),
        f"# the colour scale all the simulations share\nlimits = {_py(limits)}",
        _call(
            "fig, axes = plt.subplots",
            {"nrows": rows, "ncols": columns, "figsize": tuple(size), "sharey": True, "squeeze": False,
             "layout": "constrained"},
        ),
        "axes = axes.ravel()",
        "mesh = None",
        "for ax, (name, _, _) in zip(axes, simulations):",
        '    grid = frames[name].pivot(index="depth", columns="position", values="value")',
        "    mesh = ax.pcolormesh(",
        "        grid.columns.values, grid.index.values, np.ma.masked_invalid(grid.values),",
        f'        cmap={_py(cmap)}, vmin=limits[0], vmax=limits[1], shading="nearest",',
        "    )",
        "    # the seabed, and anything else without values, shows as land",
        f"    ax.set_facecolor({_py(ap.TRANSECT_LAND)})",
        "    ax.set_xlim(grid.columns.min(), grid.columns.max())",
        "    ax.xaxis.set_major_locator(MaxNLocator(4))",
        "    ax.xaxis.set_major_formatter(FuncFormatter(position_text))",
        f"    ax.set_xlabel({_py(position_label)})",
        "    ax.set_title(name)",
        "for ax in axes[len(simulations):]:",
        "    ax.set_visible(False)",
        "# the surface at the top, and the axis from the shallowest depth to the deepest",
        "depths = pd.concat([frame.depth for frame in frames.values()])",
        "axes[0].set_ylim(depths.max(), depths.min())",
        'for ax in axes.reshape(' + str(rows) + ", " + str(columns) + ")[:, 0]:",
        '    ax.set_ylabel("Depth (m)")',
        f"fig.colorbar(mesh, ax=list(axes[:len(simulations)]), extend={_py(extend)}, label={_py(label)})",
    )


def _month_grids_code(info, sims, files, line, limits, extend, label, size, columns, rows):
    """The code of a transect's monthly climatology at the surface, month and position as
    colours, a panel for each simulation on one colour scale and with one colour bar."""
    position_code, position_label = ap._position_text(line)
    north_south = line["direction"] == "north-south"
    draw = (
        [
            "    mesh = ax.pcolormesh(np.arange(len(months)), grid.index.values, values, **options)",
            "    ax.set_ylim(grid.index.min(), grid.index.max())",
            "    ax.set_xticks(range(len(months)), month_names, rotation=90)",
            "    ax.yaxis.set_major_formatter(FuncFormatter(position_text))",
            f"    ax.set_ylabel({_py(position_label)})",
            '    ax.set_xlabel("Month")',
        ]
        if north_south
        else [
            "    mesh = ax.pcolormesh(grid.index.values, np.arange(len(months)), values.T, **options)",
            "    ax.set_xlim(grid.index.min(), grid.index.max())",
            "    ax.set_yticks(range(len(months)), month_names)",
            "    # January at the top",
            "    ax.invert_yaxis()",
            "    ax.xaxis.set_major_formatter(FuncFormatter(position_text))",
            f"    ax.set_xlabel({_py(position_label)})",
            '    ax.set_ylabel("Month")',
        ]
    )
    return _code(
        "import calendar\n\nimport matplotlib.pyplot as plt\nimport numpy as np\nimport pandas as pd\n"
        "from matplotlib.ticker import FuncFormatter\n",
        _sim_frames_code(info, sims, files),
        "\n".join(position_code),
        f"# the colour scale all the simulations share: the 2nd to the 98th percentile of the values\nlimits = {_py(limits)}",
        _call(
            "fig, axes = plt.subplots",
            {"nrows": rows, "ncols": columns, "figsize": tuple(size), "squeeze": False, "layout": "constrained"},
        ),
        "axes = axes.ravel()",
        'options = {"cmap": "viridis", "vmin": limits[0], "vmax": limits[1], "shading": "nearest"}',
        "mesh = None",
        "for ax, (name, _, _) in zip(axes, simulations):",
        '    grid = frames[name].pivot(index="position", columns="month", values="value")',
        "    months = list(grid.columns)",
        "    month_names = [calendar.month_abbr[month] for month in months]",
        "    values = np.ma.masked_invalid(grid.values)",
        *draw,
        "    ax.set_title(name)",
        "    # what has no values shows as land",
        f"    ax.set_facecolor({_py(ap.TRANSECT_LAND)})",
        "for ax in axes[len(simulations):]:",
        "    ax.set_visible(False)",
        f"fig.colorbar(mesh, ax=list(axes[:len(simulations)]), extend={_py(extend)}, label={_py(label)})",
    )


def _lines_code(info, sims, files, line, label, ylabel):
    """The code of a transect's values along it at the surface, a line for each
    simulation, with a line at zero."""
    position_code, position_label = ap._position_text(line)
    return _code(
        "import matplotlib.pyplot as plt\nimport pandas as pd\nfrom matplotlib.ticker import FuncFormatter\n",
        _sim_frames_code(info, sims, files),
        "\n".join(position_code),
        _call("fig, ax = plt.subplots", {"figsize": (8, 4.2)}),
        "for name, colour, _ in simulations:",
        "    frame = frames[name]",
        "    ax.plot(frame.position, frame.value, label=name, color=colour, linewidth=2)",
        'ax.axhline(0, color="#999999", linewidth=0.8)',
        f"ax.set_ylabel({_py(ylabel)})",
        f"ax.set_title({_py(label)}, fontsize=10)",
        "ax.xaxis.set_major_formatter(FuncFormatter(position_text))",
        f"ax.set_xlabel({_py(position_label)})",
        "ax.grid(alpha=0.3)",
        'for side in ("top", "right"):',
        "    ax.spines[side].set_visible(False)",
        "fig.legend(*ax.get_legend_handles_labels(), loc=\"lower center\", ncol=len(simulations), frameon=False, fontsize=9)",
        "fig.tight_layout(rect=(0, 0.08, 1, 1))",
    )


def transect_figure(results_dir, key, index, option):
    """The figure of one option of one transect of a variable (see
    oceanval.analysis_transects.OPTIONS), in every simulation, or None if no
    simulation has values along it. Lines are a colour for each simulation, and sections,
    grids of months and the changes' heat maps are a panel for each, on one colour scale. Drawn.found holds
    "kind": "months", "line", "section" or "heatmaps"."""
    info = load(results_dir)
    variable = entry(info, key)
    sims, files, savedes = [], [], []
    for sim in _having(variable, "transects"):
        own = ap.transect_of(_own(variable, sim), index)
        saved = own["options"][option]
        if saved["empty"]:
            continue
        sims.append(sim)
        savedes.append(saved)
        files.append(_path(folder_of(results_dir, info, sim), saved["csv"]))
    if not sims:
        return None
    first = _own(variable, sims[0])
    line = ap.transect_of(first, index)
    saved = savedes[0]
    frames = [pd.read_csv(path) for path in files]
    values = pd.concat([frame["value"] for frame in frames])
    value_label = legend(variable["name"], variable["units"])
    columns, rows = min(GRID_COLUMNS, len(sims)), int(np.ceil(len(sims) / min(GRID_COLUMNS, len(sims))))
    if option == "vertical":
        limits, extend = _scale(values)
        size = (3.6 * columns + 1.4, 3.4 * rows + 0.6)
        code = _sections_code(info, sims, files, line, limits, extend, "viridis", value_label, size, columns, rows)
        kind = "section"
    elif option == "climatology" and "month" in frames[0]:
        limits, extend = _scale(values)
        north_south = line["direction"] == "north-south"
        size = ((2.6 if north_south else 4.2) * columns + 1.4, (3.4 if north_south else 3.0) * rows + 0.6)
        code = _month_grids_code(info, sims, files, line, limits, extend, value_label, size, columns, rows)
        kind = "months"
    elif option == "climatology":
        code = _lines_code(info, sims, files, line, f"Mean, {ap._years_range(saved['start'], saved['end'])}", value_label)
        kind = "line"
    else:
        label, title = ap.changes_labels(saved, variable["name"], variable["units"])
        colours, diverging = ap.changes_colours(saved)
        limits, extend = _scale(values, diverging=diverging)
        if option == "vertical_trends":
            # a column for each simulation, and a row for each of its depths shown
            depths = [sorted(frame["depth"].unique()) for frame in frames]
            most = max(len(shown) for shown in depths)
            grid = (most, len(sims))
            panels = [
                (column, float(shown[row]), f"{sims[column]}, {ap._depth_text(shown[row])}") if row < len(shown) else None
                for row in range(most)
                for column, shown in enumerate(depths)
            ]
        else:
            grid = (rows, columns)
            panels = [(column, None, sim) for column, sim in enumerate(sims)]
        code = ap.changes_code(
            files, line, panels, grid, colours, limits, extend, label, ap.changes_size(line, grid), heading=title
        )
        kind = "heatmaps"
    _run(code, "sim_transect_figure")
    return Drawn(code, False, {"kind": kind}, _about(info))
