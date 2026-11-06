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
import textwrap

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from oceanval import analysis_plots as ap
from oceanval.quiet_nc import nc
from oceanval.analysis import PEAK_DAY, PEAK_VALUE
from oceanval.analysis_plots import (
    CYCLE_AXIS,
    LINE_STYLE,
    NOT_STATIONS,
    YEAR_TICKS,
    Drawn,
    _Code,
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


def region_map(results_dir):
    """A map of the subdomains the time series are taken over, or None."""
    info = load(results_dir)
    return ap.region_map(results_dir, info=info)


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
    them). Of two simulations it is the maps of both and the first minus the second (see
    annual_difference_maps)."""
    info = load(results_dir)
    variable = entry(info, key)
    if difference_pair(info, variable, layer):
        return annual_difference_maps(results_dir, key, layer)
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


# ---- the maps of two simulations, and the first minus the second ----

# the font size of these figures, and how many months are across a figure of the months
DIFFERENCE_FONT = 12
DIFFERENCE_COLUMNS = 4
# the colour map of a difference
DIFFERENCE_COLOURS = "RdBu_r"
# the space between the maps, in inches: 5 mm
GAP = 0.2
# the room, in inches, for a map's title above it, for the names of the rows to the right of them, and
# for the label of each colour bar (one under the other) below them
TITLE_ROOM, NAMES_ROOM, BAR_LABEL = 0.4, 0.8, 0.5


def drawn_aspect(path, key):
    """The width over the height of a map of variable key in path as nctoolkit draws it (its projection
    depends on where the map is), found by drawing it once on a figure of its own; ap.map_aspect's
    estimate if that fails."""
    try:
        ds = nc.open_data(path, checks=False)
        ds.subset(variables=key)
        ds.run()
        if len(ds.times) > 1:
            ds.subset(time=0)
            ds.run()
        fig = plt.figure(figsize=(4, 4))
        ds.pub_plot(var=key, legend_position=None, fig=fig)
        fig.canvas.draw()
        box = fig.axes[0].get_position()
        return float(box.width / box.height)
    except Exception:
        return float(ap.map_aspect(path))
    finally:
        plt.close("all")


def pair_size(path, key, columns, rows, side=0.3, bars=1, titles=1):
    """The size of a figure of rows of columns of maps of variable key in path, in inches: ap.PANEL_WIDTH wide,
    each map as tall as its shape makes it, GAP apart, with room for `titles` rows of titles, `side` to the right and
    `bars` colour bars below."""
    width = (ap.PANEL_WIDTH - side - 0.2 - (columns - 1) * GAP) / columns
    height = width / min(max(drawn_aspect(path, key), 0.3), 3.5)
    # a colour bar takes about a third of a map's height (its size and its padding), and room for its label
    return [ap.PANEL_WIDTH, round((rows + 0.3 * bars) * height + (rows - 1) * GAP + titles * TITLE_ROOM + bars * BAR_LABEL + 0.6, 1)]


# the layout of the figures: the maps are this far apart (the padding of constrained layout is each side)
SPACING = 'fig.get_layout_engine().set(w_pad=GAP / 2, h_pad=GAP / 2, wspace=0, hspace=0)'


def difference_pair(info, variable, layer):
    """The two simulations whose maps of a variable's climatology in a layer are drawn
    with their difference, (first, second), or None: when exactly two simulations are
    compared, both have the layer, with the same months, and it is not raw FVCOM output
    (which is drawn on its mesh)."""
    names = names_of(info)
    if len(names) != 2:
        return None
    layers = []
    for sim in names:
        own = variable["sims"].get(sim, {}).get("climatology")
        saved = own["layers"].get(layer) if own else None
        if saved is None or saved.get("mesh"):
            return None
        layers.append(saved)
    if layers[0]["months"] != layers[1]["months"]:
        return None
    return names[0], names[1]


def has_difference(results_dir, key, layer):
    """Whether the maps of a variable's climatology in a layer are drawn with the
    first simulation minus the second (see difference_pair)."""
    info = load(results_dir)
    return difference_pair(info, entry(info, key), layer) is not None


def difference_title(first, second):
    """What the first minus the second is called, e.g. "Base run minus Warm run"."""
    return f"{first} minus {second}"


def _pair_files(results_dir, info, variable, layer, kind):
    """The files of the two simulations' maps (kind: "annual" or "monthly") and their names."""
    pair = difference_pair(info, variable, layer)
    paths = [
        _path(folder_of(results_dir, info, sim), _own(variable, sim)["climatology"]["layers"][layer][kind]) for sim in pair
    ]
    return pair, paths


def _open_pair_code(paths, key, regrid_second):
    """The code that opens the maps of two simulations as ds_first and ds_second, and makes
    ds_difference, the first minus the second. If the simulations are on different grids,
    the second is regridded to the first's for it (bilinear)."""
    lines = ["# open the map of each simulation"]
    for name, path in zip(("first", "second"), paths):
        lines += [
            f"ds_{name} = nc.open_data({_py(path)}, checks=False)",
            f"ds_{name}.subset(variables={_py(key)})",
            f"ds_{name}.run()",
        ]
    lines.append("# the difference: the first simulation minus the second")
    if regrid_second:
        lines += [
            "# the simulations are on different grids, so the second is regridded to the first's for it",
            "ds_on_first = ds_second.copy()",
            f'ds_on_first.regrid(grid={_py(paths[0])}, method="bil")',
            "ds_on_first.run()",
        ]
    else:
        lines.append("ds_on_first = ds_second")
    lines += ["ds_difference = ds_first.copy()", "ds_difference.subtract(ds_on_first)", "ds_difference.run()"]
    return "\n".join(lines)


def _difference_values(paths, key, regrid_second):
    """The values of the first simulation's map minus the second's, worked out by
    the code of _open_pair_code."""
    namespace = {"nc": nc}
    exec(compile(_open_pair_code(paths, key, regrid_second), "<difference>", "exec"), namespace)
    return np.asarray(namespace["ds_difference"].to_xarray()[key].values, dtype="float64")


def _pair_scales(paths, key, regrid_second):
    """The colour scales of two simulations' maps and their difference:
    ((limits, extend) shared by the simulations, (limits, extend) of the difference,
    symmetric about zero)."""
    return (
        _scale(_all_values(paths, key)),
        _scale(_difference_values(paths, key, regrid_second), diverging=True),
    )


def _wrapped(label, width=28):
    """label on lines of at most width characters, to fit under a colour bar below one map."""
    return textwrap.fill(label, width)


def _bars_code():
    """The code that makes the colour bars below the maps, for the bars list (the first and last
    of the maps (panels) the bar is below, its label, whether it has arrows at its ends, the
    number of a map whose colours it shows, and how thin the bar is: 1 for a bar below one row of maps, less
    below more of them)."""
    return "\n".join(
        [
            "    # a colour bar below the maps that share a colour scale: the colours of one of them are used",
            "    fig = plt.gcf()",
            "    for first, last, label, extend, source, thin in bars:",
            "        below = panels[first:last]",
            "        image = next(x for x in panels[source].get_children() if hasattr(x, \"get_clim\") and x.get_array() is not None)",
            "        # a bar below a single map is wider than one below several, and has fewer ticks, so that they do not collide",
            "        single = last - first == 1",
            '        bar = fig.colorbar(\n            image, ax=below, location="bottom", extend=extend, shrink=0.9 if single else 0.6,\n            fraction=0.15 * thin, pad=0.1 * thin,\n        )',
            "        bar.set_label(label)",
            "        bar.locator = MaxNLocator(nbins=4 if single else 6)",
            "        bar.update_ticks()",
        ]
    )


def _pair_figure(key, comment, paths, regrid_second, scales, size, draw, calendar=False):
    """The function that makes the code of a figure of two simulations and their difference
    from the plot grids to regrid them to (None if they are drawn as they are, see _drawn_maps);
    draw is the code that draws the figure, from the open maps and the settings."""
    (limits, _), (difference_limits, _) = scales
    imports = ap.MAP_IMPORTS + "from matplotlib.ticker import MaxNLocator\n" + ("import calendar\n" if calendar else "")

    def code_for(grids):
        return _code(
            imports,
            comment,
            "# the colour scale the simulations share (the 2nd to the 98th percentile of both of them) and the one\n"
            "# the differences share (symmetric about zero, up to the 98th percentile of their size)\n"
            f"limits = {_py(limits)}\n"
            f"difference_limits = {_py(difference_limits)}",
            "# the figure's size, in inches, and its font size: the report shows it at about the width of its page\n"
            f"size = {_py(size)}\n"
            f"font_size = {DIFFERENCE_FONT}",
            _open_pair_code(paths, key, regrid_second),
            _grids_code(grids),
            draw,
        )

    return code_for


def _grids_code(grids):
    """The code that regrids the maps of two simulations and their difference to regular lon-lat
    grids, as _plot_grid says, if grids is not None."""
    if grids is None:
        return ""
    return "\n".join(
        [
            "# the model's grid cannot be drawn as it is, so each map is regridded to a regular lon-lat grid",
            "datasets = [ds_first, ds_second, ds_difference]",
            f"grids = {_py(grids)}",
            "for ds_map, grid in zip(datasets, grids):",
            "    ds_map.to_latlon(**grid)",
            "    ds_map.run()",
        ]
    )


def _pair_grids(paths):
    """The plot grids of the maps of two simulations and their difference: the difference is on the grid
    of the first."""
    first, second = _plot_grid(paths[0]), _plot_grid(paths[1])
    return [first, second, first]


def _plot_options(key, extra):
    """The options every map of a pair of simulations is drawn with."""
    options = {"var": key, "legend_position": None, "fig": _Code("fig")}
    if ap._takes_grid_labels():
        options["grid_labels"] = False
    options.update(extra)
    return options


def annual_difference_maps(results_dir, key, layer):
    """The map of the annual mean of a variable's climatology in one layer in two
    simulations and the first minus the second, in a row: the simulations on one colour
    scale, the difference, titled "first minus second", on another, symmetric about zero.
    If the simulations are not on the same grid, the second is regridded to the first's
    for the difference (Drawn.found["difference_regridded"])."""
    info = load(results_dir)
    variable = entry(info, key)
    pair, paths = _pair_files(results_dir, info, variable, layer, "annual")
    saved = _own(variable, pair[0])["climatology"]["layers"][layer]
    regrid_second = info.get("same_grid") is not True
    scales = _pair_scales(paths, key, regrid_second)
    title = difference_title(*pair)
    name = variable["name"]
    bars = [
        (0, 2, legend(name, saved["units"]), scales[0][1], 0, 1),
        (2, 3, _wrapped(legend(f"{name}, {title}", saved["units"])), scales[1][1], 2, 1),
    ]
    size = pair_size(paths[0], key, 3, 1)

    options = _plot_options(
        key,
        {
            "limits": _Code("[limits, limits, difference_limits][index]"),
            "colours": _Code('["auto", "auto", colours_of_difference][index]'),
            "title": _Code("title"),
            "gs": _Code("grid[0, index]"),
        },
    )
    draw = "\n".join(
        [
            "# the title of each map, and the colour map of the difference",
            f"titles = {_py([*pair, title])}",
            f"colours_of_difference = {_py(DIFFERENCE_COLOURS)}",
            "# each colour bar: the first and last of the maps it is below, its label, whether it has arrows at its",
            "# ends, and the number of a map with its colours",
            f"bars = {_py([tuple(bar) for bar in bars])}",
            "# the space between the maps, in inches: 5 mm",
            f"GAP = {GAP}",
            'with plt.rc_context({"font.size": font_size}):',
            '    fig = plt.figure(figsize=size, layout="constrained")',
            "    # the maps are 5 mm apart",
            "    " + SPACING,
            "    grid = fig.add_gridspec(1, 3)",
            "    for index, (title, ds_map) in enumerate(zip(titles, [ds_first, ds_second, ds_difference])):",
            "\n".join("        " + line for line in _call("ds_map.pub_plot", options).split("\n")),
            "    panels = list(fig.axes)",
            "    # the title of the difference is the one to look for",
            '    panels[2].title.set_fontweight("bold")',
            _bars_code(),
        ]
    )
    comment = (
        f"# the annual mean of {name}'s climatology in {pair[0]} and {pair[1]}, and the difference between them: "
        f"{saved['title'].lower()}"
    )
    code_for_figure = _pair_figure(key, comment, paths, regrid_second, scales, size, draw)

    def code_for(regrid):
        return code_for_figure(_pair_grids(paths) if regrid else None)

    drawn = _drawn_maps(code_for, info, "annual_maps")
    drawn.found = {"difference_regridded": regrid_second, "pair": list(pair)}
    return drawn


def monthly_difference_maps(results_dir, key, layer):
    """The maps of each month's mean of a variable's climatology in one layer in two
    simulations and the first minus the second: the months across, in blocks of
    DIFFERENCE_COLUMNS, and for each block a row of the first simulation, one of the second
    and one of the difference, each named on the right. The simulations share one colour
    scale and the differences another (see annual_difference_maps)."""
    info = load(results_dir)
    variable = entry(info, key)
    pair, paths = _pair_files(results_dir, info, variable, layer, "monthly")
    saved = _own(variable, pair[0])["climatology"]["layers"][layer]
    regrid_second = info.get("same_grid") is not True
    scales = _pair_scales(paths, key, regrid_second)
    title = difference_title(*pair)
    name = variable["name"]
    months = int(saved["months"])
    ncol = min(DIFFERENCE_COLUMNS, months)
    blocks = int(np.ceil(months / ncol))
    # both colour bars are below all the maps, one under the other (the one made last is the upper)
    thin = round(1 / (3 * blocks), 3)
    bars = [
        (0, months * 3, legend(f"{name}, {title}", saved["units"]), scales[1][1], 2, thin),
        (0, months * 3, legend(name, saved["units"]), scales[0][1], 0, thin),
    ]
    # a row of maps for the first simulation, the second and the difference in every block, and room for the colour bars
    size = pair_size(paths[0], key, ncol, 3 * blocks, side=NAMES_ROOM, bars=2, titles=blocks)
    # the names of the rows, wrapped so that a long name stays beside its row
    rows = [textwrap.fill(label, 24) for label in (*pair, title)]

    options = _plot_options(
        key,
        {
            "limits": _Code("[limits, limits, difference_limits][kind]"),
            "colours": _Code('["auto", "auto", colours_of_difference][kind]'),
            "title": _Code("calendar.month_abbr[time.month] if kind == 0 else None"),
            "gs": _Code("grid[3 * block + kind, column]"),
        },
    )
    draw = "\n".join(
        [
            "# the name of each row, which is written to the right of it, and the colour map of the difference",
            f"rows = {_py(rows)}",
            f"colours_of_difference = {_py(DIFFERENCE_COLOURS)}",
            f"ncol = {ncol}",
            "datasets = [ds_first, ds_second, ds_difference]",
            "# each colour bar: the first and last of the maps it is below, its label, whether it has arrows at its",
            "# ends, and the number of a map with its colours",
            f"bars = {_py([tuple(bar) for bar in bars])}",
            "# the space between the maps, in inches: 5 mm",
            f"GAP = {GAP}",
            'with plt.rc_context({"font.size": font_size}):',
            '    fig = plt.figure(figsize=size, layout="constrained")',
            "    # the maps are 5 mm apart",
            "    " + SPACING,
            "    # for each block of months a row for each of the simulations and the difference",
            f"    grid = fig.add_gridspec({3 * blocks}, ncol)",
            "    maps = {}",
            "    for index, time in enumerate(ds_first.times):",
            "        block, column = divmod(index, ncol)",
            "        for kind, ds_all in enumerate(datasets):",
            "            ds_month = ds_all.copy()",
            "            ds_month.subset(time=index)",
            "            ds_month.run()",
            "\n".join("            " + line for line in _call("ds_month.pub_plot", options).split("\n")),
            "            maps[3 * block + kind, column] = fig.axes[-1]",
            "    panels = list(maps.values())",
            "    # the name of each row, to the right of its last map, or of where it would be in the last block",
            f"    for row in range({3 * blocks}):",
            "        label = rows[row % 3]",
            "        ax = maps.get((row, ncol - 1))",
            "        if ax is None:",
            "            ax = fig.add_subplot(grid[row, ncol - 1])",
            '            ax.axis("off")',
            "        ax.text(",
            '            1.04, 0.5, label, transform=ax.transAxes, rotation=270, va="center", ha="left",',
            '            fontweight="bold" if row % 3 == 2 else "normal",',
            "        )",
            _bars_code(),
        ]
    )
    comment = (
        f"# the climatology of {name}, month by month, in {pair[0]} and {pair[1]}, and the difference between them: "
        f"{saved['title'].lower()}"
    )
    code_for_figure = _pair_figure(key, comment, paths, regrid_second, scales, size, draw, calendar=True)

    def code_for(regrid):
        return code_for_figure(_pair_grids(paths) if regrid else None)

    drawn = _drawn_maps(code_for, info, "monthly_difference_maps")
    drawn.found = {"difference_regridded": regrid_second, "pair": list(pair)}
    return drawn


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
    month of the year (the seasonal series), a line for each simulation in a panel for each
    station, or None if there are no stations. Panel titles show coordinates.
    Single station: found has "caption". Drawn.found holds "names", the stations'."""
    info = load(results_dir)
    variable = entry(info, key)
    sims, saved, files = _series_files(results_dir, info, variable, kind, layer)
    station_names = _series_names(files, ("station",))
    if not station_names:
        return None
    long_term = kind == "long_term"
    table = "frame" if long_term else "cycle"
    ylabel = series_legend(_own(variable, sims[0]), saved[0]) if long_term else legend(variable["name"], saved[0]["units"])
    axis_lines = YEAR_TICKS if long_term else CYCLE_AXIS
    imports = "import matplotlib.pyplot as plt\nimport pandas as pd\n"
    if not long_term:
        imports = "import calendar\n\n" + imports
    prepare = '# the series in the model cell nearest each station\nframe = frame[frame.kind == "station"]\n'
    if not long_term:
        prepare += cycle_code(CYCLE_BY)
    marks = [
        f"        ax.plot(x, y, color=colour, label=sim_name, linewidth=1.6)",
    ]
    if long_term:
        marks += [
            "        hollow = ~part.complete.values.astype(bool) & (part.months.values > 0)",
            "        if hollow.any():",
            '            ax.plot(x[hollow], y[hollow], "o", markerfacecolor="white", markeredgecolor=colour, markersize=5, linestyle="none")',
        ]
    titles = []
    for name in station_names:
        given = info["stations"][name]
        titles.append(f"{name} ({given['lon']:.2f}, {given['lat']:.2f})")
    station_listing = _listing(
        "stations",
        [(title, name) for title, name in zip(titles, station_names)],
        "each station's panel title and its name in the data",
    )
    columns = min(SERIES_COLUMNS, len(station_names))
    nrow = int(np.ceil(len(station_names) / columns))
    size = (8, 4.4) if len(station_names) == 1 else (4.8 * columns, 3.0 * nrow + 0.7)
    code = _code(
        imports,
        _simulations_listing(info, sims, files),
        "frame = pd.concat(\n"
        "    [pd.read_csv(path).assign(simulation=name) for name, _, path in simulations], ignore_index=True\n"
        ")",
        prepare.rstrip("\n"),
        station_listing,
        _call(
            "fig, grid = plt.subplots",
            {"nrows": nrow, "ncols": columns, "figsize": size, "sharex": True, "squeeze": False},
        ),
        "for (title, data_name), ax in zip(stations, grid.flat):",
        *(["    ax.set_title(title, fontsize=10)"] if len(station_names) > 1 else []),
        "    for sim_name, colour, _ in simulations:",
        f"        part = {table}[({table}.simulation == sim_name) & ({table}.name == data_name)]",
        f"        x = {'part.year.values' if long_term else 'part.month.values'}",
        "        y = part.value.values",
        *marks,
        "    " + "\n    ".join(axis_lines.split("\n")),
        "    " + "\n    ".join(LINE_STYLE.split("\n")),
        "for ax in list(grid.flat)[len(stations):]:",
        "    ax.set_visible(False)",
        "for ax in grid[:, 0]:",
        f"    ax.set_ylabel({_py(ylabel)})",
        "handles, labels = grid.flat[0].get_legend_handles_labels()",
        'fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False, fontsize=9)',
        "fig.tight_layout(rect=(0, 0.07, 1, 1))",
    )
    _run(code, "sim_station_plot")
    found = {"names": station_names}
    if len(station_names) == 1:
        found["caption"] = titles[0]
    return Drawn(code, False, found, _about(info))


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


# how many months across a monthly section has
MONTH_COLUMNS = 4


def _along(x, y, at):
    """y at the places at, linearly interpolated from (x, y) and missing outside x."""
    order = np.argsort(x)
    return np.interp(at, np.asarray(x)[order], np.asarray(y)[order], left=np.nan, right=np.nan)


def on_first_grid(first, second):
    """The values of the first simulation's frame of a transect option minus the second's,
    as a frame of the first's places and value, or None if they have none in common. The
    simulations need not be on the same positions or depths (each has depths of its own
    unless a depth increment was given): the second is linearly interpolated along depth,
    then along position, onto the first's, and is missing outside its own places, so the
    seabed of the shallower one stays missing."""
    keys = [name for name in ("month", "year") if name in first and name in second]
    has_depth = "depth" in first and "depth" in second
    parts = []
    for group, one in first.groupby(keys) if keys else [((), first)]:
        group = group if isinstance(group, tuple) else (group,)
        other = second
        for name, value in zip(keys, group):
            other = other[other[name] == value]
        if other.empty:
            continue
        if has_depth:
            # along depth at each of the second's positions, then along position at each of the first's depths
            depths = np.sort(one["depth"].unique())
            columns = {}
            for position, rows in other.groupby("position"):
                columns[position] = _along(rows["depth"].to_numpy(), rows["value"].to_numpy(), depths)
            if not columns:
                continue
            table = pd.DataFrame(columns, index=depths)
            at = np.sort(one["position"].unique())
            across = np.array([_along(table.columns.to_numpy(float), row, at) for row in table.to_numpy()])
            lookup = pd.DataFrame(across, index=depths, columns=at).stack().rename("other").reset_index()
            lookup.columns = ["depth", "position", "other"]
            merged = one.merge(lookup, on=["depth", "position"], how="left")
        else:
            at = one["position"].to_numpy(float)
            merged = one.assign(other=_along(other["position"].to_numpy(float), other["value"].to_numpy(float), at))
        merged["value"] = merged["value"] - merged["other"]
        parts.append(merged[[name for name in ("position", "depth", "month", "year") if name in one] + ["value"]])
    if not parts:
        return None
    both = pd.concat(parts, ignore_index=True).dropna(subset=["value"])
    return both if len(both) else None


def transect_difference(results_dir, key, index, option):
    """The first simulation's values along a transect minus the second's, for one option
    (see transect_figure), saved as a CSV in results_dir: (its path, the names of the
    two simulations), or None unless exactly two simulations have values and some
    positions (and depths, months, years) are in both."""
    info = load(results_dir)
    variable = entry(info, key)
    names = names_of(info)
    if len(names) != 2:
        return None
    paths = []
    for sim in names:
        if sim not in variable["sims"] or "transects" not in variable["sims"][sim]:
            return None
        saved = ap.transect_of(_own(variable, sim), index)["options"].get(option)
        if saved is None or saved["empty"]:
            return None
        paths.append(_path(folder_of(results_dir, info, sim), saved["csv"]))
    first, second = (pd.read_csv(path).dropna(subset=["value"]) for path in paths)
    both = on_first_grid(first, second)
    if both is None or both.empty:
        return None
    path = os.path.join(results_dir, f"{key}_transect_{index}_{option}_difference.csv")
    both.to_csv(path, index=False)
    return path, tuple(names)


def _grid_size(panels, width, height):
    columns = min(GRID_COLUMNS, panels)
    return columns, int(np.ceil(panels / columns)), (width * columns + 1.4, height * int(np.ceil(panels / columns)) + 0.6)


def _sim_frames_code(info, sims, files):
    return _code(
        _simulations_listing(info, sims, files),
        "# places without a value are left out before anything is drawn",
        'frames = {name: pd.read_csv(path).dropna(subset=["value"]) for name, _, path in simulations}',
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


def _monthly_sections_code(info, sims, files, line, limits, extend, cmap, label):
    """The code of a section through the water column along a transect for each month of
    the year, a block of panels (four across) for each simulation, on one colour scale
    and with one colour bar."""
    position_code, _ = ap._position_text(line)
    position_label = ap._position_text(line)[1]
    months = sorted({m for path in files for m in pd.read_csv(path, usecols=["month"])["month"].unique()})
    per = int(np.ceil(len(months) / MONTH_COLUMNS))
    size = (2.7 * MONTH_COLUMNS + 1.4, 2.4 * per * len(sims) + 0.8)
    return _code(
        "import calendar\n\nimport matplotlib.pyplot as plt\nimport numpy as np\nimport pandas as pd\n"
        "from matplotlib.ticker import FuncFormatter, MaxNLocator\n",
        _sim_frames_code(info, sims, files),
        "\n".join(position_code),
        f"# the colour scale all the simulations share\nlimits = {_py(limits)}",
        f"months = {_py([int(m) for m in months])}\nper_block = {per}",
        _call(
            "fig, axes = plt.subplots",
            {"nrows": per * len(sims), "ncols": MONTH_COLUMNS, "figsize": size, "sharex": True, "sharey": True,
             "squeeze": False, "layout": "constrained"},
        ),
        "mesh = None",
        "for block, (name, _, _) in enumerate(simulations):",
        "    for number in range(per_block * " + str(MONTH_COLUMNS) + "):",
        "        ax = axes[block * per_block + number // " + str(MONTH_COLUMNS) + ", number % " + str(MONTH_COLUMNS) + "]",
        "        if number >= len(months):",
        "            ax.set_visible(False)",
        "            continue",
        "        part = frames[name][frames[name].month == months[number]]",
        '        grid = part.pivot(index="depth", columns="position", values="value")',
        "        mesh = ax.pcolormesh(",
        "            grid.columns.values, grid.index.values, np.ma.masked_invalid(grid.values),",
        f'            cmap={_py(cmap)}, vmin=limits[0], vmax=limits[1], shading="nearest",',
        "        ) if grid.size else mesh",
        f"        ax.set_facecolor({_py(ap.TRANSECT_LAND)})",
        "        ax.xaxis.set_major_locator(MaxNLocator(3))",
        "        ax.xaxis.set_major_formatter(FuncFormatter(position_text))",
        '        ax.set_title((name + ": " if len(simulations) > 1 else "") + calendar.month_abbr[months[number]], fontsize=9)',
        "# the surface at the top, and the axis from the shallowest depth to the deepest",
        "depths = pd.concat([frame.depth for frame in frames.values()])",
        "axes[0, 0].set_ylim(depths.max(), depths.min())",
        'for ax in axes[:, 0]:\n    ax.set_ylabel("Depth (m)")',
        f"for ax in axes[-1]:\n    ax.set_xlabel({_py(position_label)})",
        f"fig.colorbar(mesh, ax=axes.ravel().tolist(), extend={_py(extend)}, label={_py(label)})",
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


def transect_figure(results_dir, key, index, option, difference=False):
    """The figure of one option of one transect of a variable (see
    oceanval.analysis_transects.OPTIONS, and "vertical_monthly", the vertical climatology
    of each month, which comes with "vertical"), in every simulation, or None if no
    simulation has values along it. Lines are a colour for each simulation, and sections,
    grids of months and the changes' heat maps are a panel for each, on one colour scale.
    With difference, and exactly two simulations, it is the first minus the second
    instead (see transect_difference), on a colour scale about zero. Missing values are left
    out before it is drawn. Drawn.found holds "kind": "months", "line", "section",
    "monthly_sections" or "heatmaps"."""
    info = load(results_dir)
    variable = entry(info, key)
    sims, files, savedes = [], [], []
    for sim in _having(variable, "transects"):
        own = ap.transect_of(_own(variable, sim), index)
        saved = own["options"].get(option)
        if saved is None or saved["empty"]:
            continue
        sims.append(sim)
        savedes.append(saved)
        files.append(_path(folder_of(results_dir, info, sim), saved["csv"]))
    if not sims:
        return None
    first = _own(variable, sims[0])
    line = ap.transect_of(first, index)
    saved = savedes[0]
    if difference:
        made = transect_difference(results_dir, key, index, option)
        if made is None:
            return None
        path, (one, two) = made
        title = difference_title(one, two)
        sims, files, savedes = [title], [path], [saved]
        info = {**info, "colours": {**info["colours"], title: "#000000"}}
    frames = [pd.read_csv(path).dropna(subset=["value"]) for path in files]
    values = pd.concat([frame["value"] for frame in frames])
    if values.empty:
        return None
    value_label = legend(variable["name"], variable["units"])
    if difference:
        value_label = legend(f"{variable['name']}, {title}", variable["units"])
    cmap = "RdBu_r" if difference else "viridis"
    columns, rows = min(GRID_COLUMNS, len(sims)), int(np.ceil(len(sims) / min(GRID_COLUMNS, len(sims))))
    if option == "vertical_monthly":
        limits, extend = _scale(values, diverging=difference)
        code = _monthly_sections_code(info, sims, files, line, limits, extend, cmap, value_label)
        kind = "monthly_sections"
    elif option == "vertical":
        limits, extend = _scale(values, diverging=difference)
        size = (3.6 * columns + 1.4, 3.4 * rows + 0.6)
        code = _sections_code(info, sims, files, line, limits, extend, cmap, value_label, size, columns, rows)
        kind = "section"
    elif option == "climatology" and "month" in frames[0]:
        limits, extend = _scale(values, diverging=difference)
        north_south = line["direction"] == "north-south"
        size = ((2.6 if north_south else 4.2) * columns + 1.4, (3.4 if north_south else 3.0) * rows + 0.6)
        code = _month_grids_code(info, sims, files, line, limits, extend, value_label, size, columns, rows)
        if difference:
            code = code.replace('"cmap": "viridis"', '"cmap": "RdBu_r"')
        kind = "months"
    elif option == "climatology":
        code = _lines_code(info, sims, files, line, f"Mean, {ap._years_range(saved['start'], saved['end'])}", value_label)
        kind = "line"
    else:
        label, title_ = ap.changes_labels(saved, variable["name"], variable["units"])
        colours, diverging = ap.changes_colours(saved)
        if difference:
            colours, diverging = "RdBu_r", True
            label = value_label
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
            files, line, panels, grid, colours, limits, extend, label, ap.changes_size(line, grid), heading=title_
        )
        kind = "heatmaps"
    _run(code, "sim_transect_figure")
    found = {"kind": kind}
    if difference:
        found["difference"] = title
    return Drawn(code, False, found, _about(info))
