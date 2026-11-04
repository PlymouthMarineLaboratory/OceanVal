"""The figures of the analysis report, which its notebooks draw from what
analyze() saved (see oceanval.analysis).

Each figure is drawn by running its own code: a short script with everything
it uses written out (the paths of the files, the colour scale, the labels,
the colours), which is also what the report's "Copy code to reproduce figure"
button copies (see show and copy_block). So the code a user copies draws the
figure the report shows, and can be changed to draw it differently. The
scripts run in the conda environment OceanVal ran in: they use nctoolkit,
pandas and matplotlib, and OceanVal only for the area-weighted means over
the model's domain (domain_mean) and the map of a transect
(oceanval.transects.plot_map).

The maps are drawn with nctoolkit's pub_plot and panel_plot, as the
validation report's are. A grid they cannot draw (a curvilinear grid
pub_plot cannot read the coordinates of) is regridded to a regular lon-lat
grid first, in the script, and Drawn.regridded says so.
"""

import base64
import calendar
import dataclasses
import html
import inspect
import json
import linecache
import os
import re
import warnings

import matplotlib.pyplot as plt
from oceanval.quiet_nc import nc
import numpy as np
import pandas as pd
import xarray as xr
from matplotlib.colors import to_hex

from oceanval import extent
from oceanval.analysis import PEAK_DAY, PEAK_VALUE, load
from oceanval.map_land import LAND_CODE
from oceanval.tidiers import fix_unit


def entry(info, key):
    """The entry for the variable key in what analyze() saved."""
    return next(variable for variable in info["variables"] if variable["key"] == key)


def unit_text(units):
    """units as a matplotlib label: fix_unit's HTML superscripts and
    subscripts as mathtext."""
    text = fix_unit(str(units or ""))
    text = re.sub(r"<sup>(.*?)</sup>", r"$^{\1}$", text)
    return re.sub(r"<sub>(.*?)</sub>", r"$_{\1}$", text)


def unit_html(units):
    """units as the report's text shows them."""
    return fix_unit(str(units or ""))


def legend(name, units):
    """A colour bar's or axis's label: the variable's name and its units."""
    units = unit_text(units)
    return f"{name} ({units})" if units else name


def series_legend(variable, saved):
    """The axis label of a long-term series: the variable's name, with
    "change" when the series is the change from its first year, and its units."""
    name = variable["name"]
    if saved.get("show") in ("absolute_change", "percentage_change"):
        name = f"{name} change"
    return legend(name, saved["units"])


# ---- the code of a figure ----


@dataclasses.dataclass
class Drawn:
    """A figure the report drew. code is the script that drew it, without
    the plt.show() that ends it when it is copied (see script); regridded,
    whether the model's grid was regridded to a regular lon-lat grid to draw
    it; found, what the script worked out that the report says, by name; and
    about, what the analysis was of, for the script's first line."""

    code: str
    regridded: bool = False
    found: dict = dataclasses.field(default_factory=dict)
    about: str = ""


class _Code(str):
    """Code, written into a script as it is, not as a value."""


def _py(value):
    """value as Python code, with strings in double quotes."""
    if isinstance(value, _Code):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (bool, type(None))):
        return repr(value)
    if isinstance(value, (int, np.integer)):
        return repr(int(value))
    if isinstance(value, (float, np.floating)):
        return repr(float(value))
    if isinstance(value, tuple):
        inner = ", ".join(_py(x) for x in value)
        return f"({inner},)" if len(value) == 1 else f"({inner})"
    if isinstance(value, list):
        return "[" + ", ".join(_py(x) for x in value) + "]"
    return repr(value)


def _call(function, options, args=(), width=88):
    """function(*args, **options) as code, on one line, or on a line for
    each argument if that is longer than width."""
    parts = [_py(value) for value in args] + [f"{name}={_py(value)}" for name, value in options.items()]
    line = f"{function}({', '.join(parts)})"
    if len(line) <= width:
        return line
    return f"{function}(\n" + "".join(f"    {part},\n" for part in parts) + ")"


def _listing(name, rows, comment):
    """name = [rows], as code, a row on each line, below a comment."""
    lines = [f"# {comment}", f"{name} = ["]
    lines.extend(f"    {_py(row)}," for row in rows)
    lines.append("]")
    return "\n".join(lines)


def _code(*blocks):
    """The blocks of a script, each a line or lines, as one script. Empty
    blocks are left out. A script drawing nctoolkit maps ends by putting their
    land beneath the data, with the lakes as water (see oceanval.map_land)."""
    blocks = [block for block in blocks if block]
    if any(re.search(r"\b(pub_plot|panel_plot)\(", block) for block in blocks):
        blocks += [
            "# nctoolkit draws the land over the maps, lakes too: put it beneath them,\n"
            "# with the lakes drawn as water",
            LAND_CODE,
            "land_below()",
        ]
    return "\n".join(blocks) + "\n"


def _about(info):
    return f"the OceanVal analysis of {info['sim_dir']} (oceanval {info['oceanval']})"


def _run(code, name):
    """Run a figure's code, as a script of its own, and return what it
    left behind. Its lines are kept for tracebacks."""
    filename = f"<oceanval figure: {name}>"
    linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
    namespace = {"__name__": "__oceanval_figure__"}
    exec(compile(code, filename, "exec"), namespace)
    return namespace


def _path(results_dir, name):
    return os.path.abspath(os.path.join(results_dir, name))


def script(drawn, title=None):
    """The script that draws drawn again, as its Copy code button copies it:
    title says which figure it is, e.g. "Figure 2 on the Temperature page"."""
    what = f"{title} of {drawn.about}" if title else f"A figure of {drawn.about}"
    return (
        f"# {what}.\n"
        "# It draws the figure again from the analysis's saved results: run it in the\n"
        "# conda environment OceanVal ran in.\n"
        f"{drawn.code}"
        "plt.show()\n"
    )


# ---- the maps ----

# nctoolkit is imported quietly in the copied scripts too (they do not import oceanval)
NC_IMPORT = "import warnings\nwith warnings.catch_warnings():\n    warnings.simplefilter(\"ignore\")\n    import nctoolkit as nc\n"
MAP_IMPORTS = "import matplotlib.pyplot as plt\n" + NC_IMPORT


def _lon_lat(path):
    """The longitudes and latitudes of the grid in path, as arrays."""
    with xr.open_dataset(path, decode_times=False) as ds:
        names = list(ds.coords) + list(ds.variables)
        lon = next(name for name in names if "lon" in name.lower())
        lat = next(name for name in names if "lat" in name.lower())
        return np.asarray(ds[lon].values, dtype="float64"), np.asarray(ds[lat].values, dtype="float64")


def _valid_lon_lat(path):
    """The longitudes and latitudes of the cells of the field in path that have
    values (see oceanval.extent), or of the whole grid if they can not be told:
    a grid can have cells far from the model's domain (NEMO's at lon -1, lat -1)."""
    with xr.open_dataset(path, decode_times=False) as ds:
        found = extent.valid_lonlat(ds)
    return found if found is not None else _lon_lat(path)


def _plot_grid(path):
    """The regular lon-lat grid to draw the grid in path on, as to_latlon's
    lon, lat and res: covering its cells with values, at about its own
    resolution, but no finer than 1/20 degree or more than 1440 cells across."""
    lon, lat = _lon_lat(path)
    lon = np.where(lon > 180, lon - 360, lon)
    valid_lon, valid_lat = _valid_lon_lat(path)
    valid_lon = np.where(valid_lon > 180, valid_lon - 360, valid_lon)
    lon_min, lon_max = float(np.nanmin(valid_lon)), float(np.nanmax(valid_lon))
    lat_min, lat_max = float(np.nanmin(valid_lat)), float(np.nanmax(valid_lat))
    if lon.ndim == 2 and lon.shape[1] > 1:
        spacing = np.nanmedian(np.abs(np.diff(lon, axis=1)))
    elif lon.size > 1:
        spacing = np.nanmedian(np.abs(np.diff(np.unique(lon))))
    else:
        spacing = 1.0
    res = max(float(spacing) if np.isfinite(spacing) and spacing > 0 else 1.0, 0.05)
    res = max(res, (lon_max - lon_min) / 1440, (lat_max - lat_min) / 720)
    return {"lon": [lon_min, lon_max], "lat": [lat_min, lat_max], "res": [res, res]}


def regridded(ds):
    """ds on the regular lon-lat grid _plot_grid works out for it."""
    grid = _plot_grid(ds[0])
    out = ds.copy()
    with warnings.catch_warnings(record=True):
        out.to_latlon(**grid)
        out.run()
    return out


def _open_code(path, variable, grid):
    """The code that opens variable in path as ds, regridded to grid if it
    is given (see _plot_grid)."""
    lines = [
        f"ds = nc.open_data({_py(path)}, checks=False)",
        f"ds.subset(variables={_py(variable)})",
        "ds.run()",
    ]
    if grid:
        lines += [
            "# the model's grid cannot be drawn as it is, so it is regridded to a regular lon-lat grid",
            _call("ds.to_latlon", grid),
            "ds.run()",
        ]
    return "\n".join(lines)


def _takes_grid_labels():
    """Whether nctoolkit's pub_plot takes grid_labels: before 1.3.2 it
    does not, and draws the labels."""
    return "grid_labels" in inspect.signature(nc.DataSet.pub_plot).parameters


def _map(code_for, path, info, name):
    """Draw the map code_for(None) draws or, if that fails, the one
    code_for(grid) draws, regridded (see _plot_grid)."""
    try:
        code = code_for(None)
        _run(code, name)
        return Drawn(code, False, {}, _about(info))
    except Exception:
        plt.close("all")
    code = code_for(_plot_grid(path))
    _run(code, name)
    return Drawn(code, True, {}, _about(info))


def _limits(path, variable, low=2, high=98):
    """The 2nd and 98th percentiles of variable in path, over every time."""
    with xr.open_dataset(path, decode_times=False) as ds:
        values = np.asarray(ds[variable].values, dtype="float64")
    values = values[np.isfinite(values)]
    if values.size == 0:
        return None
    lo, hi = np.percentile(values, [low, high])
    if lo == hi:
        return None
    return [float(lo), float(hi)]


def annual_map(results_dir, key, layer):
    """The map of the annual mean of a variable's climatology in one layer."""
    info = load(results_dir)
    variable = entry(info, key)
    saved = variable["climatology"]["layers"][layer]
    path = _path(results_dir, saved["annual"])
    if saved.get("mesh"):
        return _mesh_annual_map(path, key, variable, saved, info)
    options = {"var": key, "robust": True, "legend": legend(variable["name"], saved["units"])}
    if _takes_grid_labels():
        options["grid_labels"] = True

    def code_for(grid):
        return _code(
            MAP_IMPORTS,
            f"# the annual mean of {variable['name']}'s climatology: {saved['title'].lower()}",
            _open_code(path, key, grid),
            "# the colours run from the 2nd to the 98th percentile of the map (robust=True)",
            _call("ds.pub_plot", options),
        )

    return _map(code_for, path, info, "annual_map")


# the maps of each month are drawn on a figure this wide, in inches, which the report
# shows at about the width of its page, with this font size, so that the text stays readable
PANEL_WIDTH = 12.0
PANEL_FONT = 14


def _panel_size(path, panels, ncol=None, bar_rows=0):
    """The size, in inches, of a figure of `panels` maps of the grid in path, in
    ncol columns (default: up to 4): PANEL_WIDTH wide, with the maps as tall as their
    shape needs, and room for each one's title and a colour bar: one at the right
    for all the maps, or, with bar_rows, one below each of that many rows."""
    ncol = ncol or (4 if panels > 3 else panels)
    nrow = int(np.ceil(panels / ncol))
    try:
        lon, lat = _valid_lon_lat(path)
    except StopIteration:
        # a mesh in metres has no longitudes and latitudes: its x and y give its shape
        with xr.open_dataset(path, decode_times=False) as ds:
            x, y = np.asarray(ds["x"].values, dtype="float64"), np.asarray(ds["y"].values, dtype="float64")
        y_span = float(np.nanmax(y) - np.nanmin(y))
        aspect = (float(np.nanmax(x) - np.nanmin(x)) / y_span) if y_span > 0 else 1.5
    else:
        lon = np.where(lon > 180, lon - 360, lon)
        lon_span, lat_span = float(np.nanmax(lon) - np.nanmin(lon)), float(np.nanmax(lat) - np.nanmin(lat))
        if lon_span > 340 and lat_span > 160:
            aspect = 2.0
        else:
            mid_lat = 0.5 * float(np.nanmax(lat) + np.nanmin(lat))
            aspect = lon_span * np.cos(np.radians(mid_lat)) / lat_span if lat_span > 0 else 1.5
    aspect = min(max(float(aspect), 0.6), 2.5)
    map_width = (PANEL_WIDTH - (0.4 if bar_rows else 1.6)) / ncol
    return [PANEL_WIDTH, round(nrow * (map_width / aspect + 0.55) + 0.9 * bar_rows + 0.3, 1)]


def _panel_draw(key, label, font, colours=None, with_calendar=False):
    """The imports, the font code and the code that draws the `panels` (a
    dict of titles and one-time datasets, in `ncol` columns, `size` and
    `limits` already set) as (imports, font, draw): with nctoolkit's
    panel_plot, or, before 1.3.2, which has none, a grid of pub_plots, which
    leaves out the font code. colours is the colour scale's name, if it is
    not the default."""
    lead = "import calendar\n\n" if with_calendar else ""
    if hasattr(nc, "panel_plot"):
        imports = lead + MAP_IMPORTS
        options = {"ncol": _Code("ncol"), "var": key, "limits": _Code("limits"), "legend": label}
        if colours:
            options["colours"] = colours
        options.update({"grid_labels": False, "size": _Code("size")})
        call = _call("nc.panel_plot", options, args=(_Code("panels"),))
        draw = 'with plt.rc_context({"font.size": font_size}):\n' + "\n".join("    " + line for line in call.split("\n"))
    else:
        # nctoolkit before 1.3.2 has no panel_plot: a grid of pub_plots
        imports = lead + "import matplotlib.pyplot as plt\n" + NC_IMPORT + "import numpy as np\n"
        options = {"var": key, "limits": _Code("limits"), "title": _Code("title"), "legend": label,
                   "fig": _Code("fig"), "gs": _Code("grid[index // ncol, index % ncol]")}
        if colours:
            options["colours"] = colours
        if _takes_grid_labels():
            options["grid_labels"] = False
        draw = "\n".join(
            [
                "nrow = int(np.ceil(len(panels) / ncol))",
                "fig = plt.figure(figsize=(4 * ncol, 3.2 * nrow))",
                "grid = fig.add_gridspec(nrow, ncol)",
                "for index, (title, ds_month) in enumerate(panels.items()):",
                "\n".join("    " + line for line in _call("ds_month.pub_plot", options).split("\n")),
            ]
        )
        # each of its panels has its own colour bar and labels, which the larger font would swamp
        font = ""
    return imports, font, draw


def monthly_maps(results_dir, key, layer, limits=None):
    """The maps of each month's mean of a variable's climatology in one
    layer, on one colour scale (the 2nd to 98th percentile of all of them, or
    limits, [low, high], if they are given, as when simulations share one)."""
    info = load(results_dir)
    variable = entry(info, key)
    saved = variable["climatology"]["layers"][layer]
    path = _path(results_dir, saved["monthly"])
    limits = _limits(path, key) if limits is None else limits
    label = legend(variable["name"], saved["units"])
    if saved.get("mesh"):
        return _mesh_monthly_maps(path, key, variable, saved, info, limits, label)
    if limits is None:
        scale = "# the colour scale all the months share: None leaves it to nctoolkit\nlimits = None"
    else:
        scale = (
            "# the colour scale all the months share: the 2nd to the 98th percentile of all of them\n"
            f"limits = {_py(limits)}"
        )
    panels = "\n".join(
        [
            "# a map of each month of the year",
            "panels = {}",
            "for index, time in enumerate(ds.times):",
            "    ds_month = ds.copy()",
            "    ds_month.subset(time=index)",
            "    ds_month.run()",
            "    panels[calendar.month_abbr[time.month]] = ds_month",
            "ncol = 4 if len(panels) > 3 else len(panels)",
        ]
    )
    size = _panel_size(path, int(saved["months"]))
    font = (
        "# the figure's size, in inches, and its font size: the report shows it at about the width of its page\n"
        f"size = {_py(size)}\n"
        f"font_size = {PANEL_FONT}"
    )
    imports, font, draw = _panel_draw(key, label, font, with_calendar=True)

    def code_for(grid):
        return _code(
            imports,
            f"# the climatology of {variable['name']}, month by month: {saved['title'].lower()}",
            _open_code(path, key, grid),
            scale,
            font,
            panels,
            draw,
        )

    return _map(code_for, path, info, "monthly_maps")


# ---- the maps of raw FVCOM output, on its own mesh (see oceanval.mesh_maps) ----

MESH_NOTE = "# raw FVCOM output, mapped on its own triangular mesh (see oceanval.mesh_maps)"


def _mesh_annual_map(path, key, variable, saved, info):
    """annual_map of raw FVCOM output."""
    options = {"robust": True, "legend": legend(variable["name"], saved["units"]), "grid_labels": True}
    code = _code(
        "import matplotlib.pyplot as plt\nfrom oceanval.mesh_maps import mesh_plot\n",
        f"# the annual mean of {variable['name']}'s climatology: {saved['title'].lower()}",
        MESH_NOTE,
        "# the colours run from the 2nd to the 98th percentile of the map (robust=True)",
        _call("mesh_plot", options, args=(path, key)),
    )
    _run(code, "annual_map")
    return Drawn(code, False, {}, _about(info))


def _mesh_monthly_maps(path, key, variable, saved, info, limits, label):
    """monthly_maps of raw FVCOM output."""
    if limits is None:
        scale = "# the colour scale all the months share: None leaves it to oceanval\nlimits = None"
    else:
        scale = (
            "# the colour scale all the months share: the 2nd to the 98th percentile of all of them\n"
            f"limits = {_py(limits)}"
        )
    size = _panel_size(path, int(saved["months"]))
    font = (
        "# the figure's size, in inches, and its font size: the report shows it at about the width of its page\n"
        f"size = {_py(size)}\n"
        f"font_size = {PANEL_FONT}"
    )
    panels = "\n".join(
        [
            "# a map of each month of the year: the months the climatology has",
            "with netCDF4.Dataset(" + _py(path) + ") as ds:",
            '    months = [int(month) for month in ds.variables["month"][:]]',
            "titles = [calendar.month_abbr[month] for month in months]",
            "ncol = 4 if len(titles) > 3 else len(titles)",
        ]
    )
    call = _call(
        "mesh_panel_plot",
        {"ncol": _Code("ncol"), "limits": _Code("limits"), "legend": label, "grid_labels": False, "size": _Code("size")},
        args=(path, key, _Code("titles")),
    )
    draw = 'with plt.rc_context({"font.size": font_size}):\n' + "\n".join("    " + line for line in call.split("\n"))
    code = _code(
        "import calendar\n\nimport matplotlib.pyplot as plt\nimport netCDF4\nfrom oceanval.mesh_maps import mesh_panel_plot\n",
        f"# the climatology of {variable['name']}, month by month: {saved['title'].lower()}",
        MESH_NOTE,
        scale,
        font,
        panels,
        draw,
    )
    _run(code, "monthly_maps")
    return Drawn(code, False, {}, _about(info))


def _all_values(paths, key):
    """Every value of variable key in the files in paths."""
    values = []
    for path in paths:
        with xr.open_dataset(path, decode_times=False) as ds:
            values.append(np.asarray(ds[key].values, dtype="float64").ravel())
    return np.concatenate(values)


def _years_title(start, end):
    """The years of a period as a panel's title: 2013, or 2013-2014."""
    return str(start) if start == end else f"{start}-{end}"


# the maps of a layer's map changes are drawn at most this many across, and with this font size
CHANGE_COLUMNS = 3
CHANGE_FONT = 12


def change_maps(results_dir, key, layer, also=None):
    """The maps of a variable's map changes in one layer, in one figure: the
    map of the historical period, then those of the future periods and the
    changes from the historical period to each of them, as its plot says,
    each titled with the years it is of. The maps of periods share one colour
    scale (the 2nd to 98th percentile of all of them) and one colour bar, below
    them; the changes share another, symmetric about zero, with a colour bar of its own.
    also is the maps of other simulations that are drawn on the same scales, as
    {"periods": [files], "changes": [files]}, whose values the scales take in too."""
    info = load(results_dir)
    variable = entry(info, key)
    asked = variable["map_changes"]
    saved = asked["layers"][layer]
    names = [_years_title(period["start"], period["end"]) for period in asked["periods"]]
    how = "minus" if asked["change"] == "absolute" else "relative to"
    periods = [(names[0], saved["historical"])]
    if asked["plot"] != "changes":
        periods += list(zip(names[1:], saved["futures"]))
    changes = []
    if asked["plot"] != "absolute":
        changes = [(f"{name} {how} {names[0]}", file) for name, file in zip(names[1:], saved["changes"])]
    maps = [(title, _path(results_dir, file)) for title, file in periods + changes]
    paths = [path for _, path in maps]
    also = also or {"periods": [], "changes": []}
    values = _all_values(paths[: len(periods)] + list(also["periods"]), key)
    limits, extend = (
        _scale(values) if len(periods) > 1 or also["periods"] else (_limits(paths[0], key) or [None, None], "neither")
    )
    # the colour scale of each map, and then the colour bars: the first and last map each of them is
    # below, its label and which of its ends have arrows
    scales = [limits] * len(periods)
    colours = ["auto"] * len(periods)
    bars = [(0, len(periods), legend(variable["name"], saved["units"]), extend)]
    if changes:
        change_limits, change_extend = _scale(
            _all_values(paths[len(periods):] + list(also["changes"]), key), diverging=True
        )
        scales += [change_limits] * len(changes)
        colours += ["RdBu_r"] * len(changes)
        bars.append((len(periods), len(maps), legend(f"{variable['name']}, change", saved["change_units"]), change_extend))
    ncol = min(CHANGE_COLUMNS, len(maps))
    size = _panel_size(paths[0], len(maps), ncol, bar_rows=int(np.ceil(len(maps) / ncol)))
    font = (
        "# the figure's size, in inches, and its font size: the report shows it at about the width of its page\n"
        f"size = {_py(size)}\n"
        f"font_size = {CHANGE_FONT}"
    )
    # each map is drawn by pub_plot into two of the figure's columns of half a map, so that a row with fewer
    # maps than the others (the changes of two future periods, below the three maps of the periods) is centred
    nrow = int(np.ceil(len(maps) / ncol))
    places = []
    for index in range(len(maps)):
        row, column = divmod(index, ncol)
        in_row = min(ncol, len(maps) - row * ncol)
        places.append((row, (ncol - in_row) + 2 * column))
    imports = MAP_IMPORTS + "from matplotlib.ticker import MaxNLocator\n"
    options = {"var": key, "limits": _Code("limits[index]"), "colours": _Code("colours[index]"),
               "title": _Code("title"), "legend_position": None, "fig": _Code("fig"),
               "gs": _Code("grid[row, start:start + 2]")}
    if _takes_grid_labels():
        options["grid_labels"] = False
    draw = "\n".join(
        [
            '    fig = plt.figure(figsize=size, layout="constrained")',
            f"    grid = fig.add_gridspec({nrow}, {2 * ncol})",
            "    for index, (title, ds_map) in enumerate(panels.items()):",
            "        row, start = places[index]",
            "\n".join("        " + line for line in _call("ds_map.pub_plot", options).split("\n")),
        ]
    )
    colour_bars = "\n".join(
        [
            "    # a colour bar below the maps that share a colour scale: the colours of the first of them are used",
            "    fig = plt.gcf()",
            "    axes = list(fig.axes)",
            "    # room for a colour bar's label between one row of maps and the titles of the next",
            "    fig.get_layout_engine().set(h_pad=0.25)",
            "    for first, last, label, extend in bars:",
            "        below = axes[first:last]",
            "        image = next(x for x in below[0].get_children() if hasattr(x, \"get_clim\") and x.get_array() is not None)",
            '        bar = fig.colorbar(image, ax=below, location="bottom", extend=extend, shrink=0.6, pad=0.08)',
            "        bar.set_label(label)",
            "        bar.locator = MaxNLocator(nbins=6)",
            "        bar.update_ticks()",
        ]
    )
    listing = _listing("maps", maps, "the title and file of each map")
    setup = "\n".join(
        [
            "# the colour scale and the colour map of each map, and each colour bar: the numbers of the first and",
            "# last of the maps it is below, its label, and whether it has arrows at its ends",
            f"limits = {_py(scales)}",
            f"colours = {_py(colours)}",
            f"bars = {_py([tuple(bar) for bar in bars])}",
            "# the row and the column (of half a map) each map is drawn in",
            f"places = {_py(places)}",
        ]
    )

    if saved.get("mesh"):
        # raw FVCOM output: each map drawn on its own mesh into the same places, without a colour bar of its own
        mesh_options = {"limits": _Code("limits[index]"), "colours": _Code("colours[index]"), "title": _Code("title"),
                        "legend_position": None, "grid_labels": False, "fig": _Code("fig"),
                        "gs": _Code("grid[row, start:start + 2]")}
        mesh_draw = "\n".join(
            [
                '    fig = plt.figure(figsize=size, layout="constrained")',
                f"    grid = fig.add_gridspec({nrow}, {2 * ncol})",
                "    for index, (title, path) in enumerate(maps):",
                "        row, start = places[index]",
                "\n".join("        " + line for line in _call("mesh_plot", mesh_options, args=(_Code("path"), key)).split("\n")),
            ]
        )
        code = _code(
            "import matplotlib.pyplot as plt\nfrom matplotlib.ticker import MaxNLocator\nfrom oceanval.mesh_maps import mesh_plot\n",
            f"# the maps of {variable['name']} in each period, and the changes between them: {saved['title'].lower()}",
            MESH_NOTE,
            listing, setup, font,
            'with plt.rc_context({"font.size": font_size}):\n' + mesh_draw + "\n" + colour_bars,
        )
        _run(code, "change_maps")
        return Drawn(code, False, {}, _about(info))

    def code_for(grid):
        lines = [
            "# open each map",
            "panels = {}",
            "for title, path in maps:",
            "    ds_map = nc.open_data(path, checks=False)",
            f"    ds_map.subset(variables={_py(key)})",
            "    ds_map.run()",
        ]
        if grid:
            lines.append("    # the model's grid cannot be drawn as it is, so it is regridded to a regular lon-lat grid")
            lines.extend("    " + line for line in _call("ds_map.to_latlon", grid).split("\n"))
            lines.append("    ds_map.run()")
        lines.append("    panels[title] = ds_map")
        return _code(
            imports,
            f"# the maps of {variable['name']} in each period, and the changes between them: {saved['title'].lower()}",
            listing, setup, font, "\n".join(lines),
            'with plt.rc_context({"font.size": font_size}):\n' + draw + "\n" + colour_bars,
        )

    return _map(code_for, paths[0], info, "change_maps")


def domain_mean(path, variable):
    """The mean of variable over the model's domain at each time in path, as
    (times, values, note): worked out with nctoolkit's spatial_mean, as
    analysis_series.robust_spatial_mean does, and note what the report must say
    about a grid whose cell areas could not be worked out ("" if all was well)."""
    from oceanval.analysis_series import robust_spatial_mean

    with xr.open_dataset(path, decode_times=False) as ds_xr:
        on_mesh = bool(ds_xr.attrs.get("mesh"))
    if on_mesh:
        # raw FVCOM output, on its own mesh, which CDO cannot read: weighted by the areas of its cells
        from oceanval.mesh_means import domain_mean as mesh_domain_mean

        return mesh_domain_mean(path, variable)
    ds = nc.open_data(path, checks=False)
    with warnings.catch_warnings(record=True):
        ds.subset(variables=variable)
        ds.run()
    times = ds.times
    values, note = robust_spatial_mean(ds, variable, len(times))
    return times, values, note


LINE_STYLE = "\n".join(
    [
        "ax.grid(alpha=0.3)",
        'for side in ("top", "right"):',
        "    ax.spines[side].set_visible(False)",
    ]
)


def _month_ticks(attrs):
    """The day each month starts on in a phenology's climatology, and the
    months' short names."""
    starts = [int(x) for x in np.atleast_1d(attrs["month_starts"])]
    months = [int(x) for x in np.atleast_1d(attrs["months"])]
    return starts, [calendar.month_abbr[month] for month in months]


def _phenology_attrs(results_dir, variable):
    with xr.open_dataset(os.path.join(results_dir, variable["phenology"]["file"]), decode_times=False) as ds:
        return dict(ds.attrs)


def peak_day_map(results_dir, key):
    """The map of the day of the year of a variable's seasonal peak, on a
    cyclic colour scale labelled by month."""
    info = load(results_dir)
    variable = entry(info, key)
    path = _path(results_dir, variable["phenology"]["file"])
    attrs = _phenology_attrs(results_dir, variable)
    starts, names = _month_ticks(attrs)
    days = int(attrs["days"])
    options = {"var": PEAK_DAY, "limits": [1, days], "colours": "twilight", "breaks": _Code("month_starts"),
               "legend": "Day of the seasonal peak"}
    if _takes_grid_labels():
        options["grid_labels"] = True
    month_bar = "\n".join(
        [
            "# the colour bar is labelled by month",
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
    months = "\n".join(
        [
            "# the day each month starts on in the model's calendar, and the months' names",
            f"month_starts = {_py(starts)}",
            f"month_names = {_py(names)}",
        ]
    )
    if variable["phenology"].get("mesh"):
        mesh_options = {"limits": [1, days], "colours": "twilight", "legend": "Day of the seasonal peak",
                        "grid_labels": True}
        code = _code(
            "import matplotlib.pyplot as plt\nfrom oceanval.mesh_maps import mesh_plot\n",
            f"# the day of the year of the seasonal peak of surface {variable['name']}",
            MESH_NOTE,
            months,
            "# the colours are cyclic, so the end of the year is close in colour to its start",
            _call("mesh_plot", mesh_options, args=(path, PEAK_DAY)),
            month_bar,
        )
        _run(code, "peak_day_map")
        return Drawn(code, False, {}, _about(info))

    def code_for(grid):
        return _code(
            MAP_IMPORTS,
            f"# the day of the year of the seasonal peak of surface {variable['name']}",
            _open_code(path, PEAK_DAY, grid),
            months,
            "# the colours are cyclic, so the end of the year is close in colour to its start",
            _call("ds.pub_plot", options),
            month_bar,
        )

    return _map(code_for, path, info, "peak_day_map")


def peak_value_map(results_dir, key):
    """The map of the value of a variable's seasonal peak."""
    info = load(results_dir)
    variable = entry(info, key)
    path = _path(results_dir, variable["phenology"]["file"])
    options = {"var": PEAK_VALUE, "robust": True, "legend": legend(f"Peak {variable['name']}", variable["units"])}
    if variable["phenology"].get("mesh"):
        mesh_options = {"robust": True, "legend": options["legend"], "grid_labels": True}
        code = _code(
            "import matplotlib.pyplot as plt\nfrom oceanval.mesh_maps import mesh_plot\n",
            f"# the seasonal peak of surface {variable['name']}: the highest value of its daily climatology",
            MESH_NOTE,
            "# the colours run from the 2nd to the 98th percentile of the map (robust=True)",
            _call("mesh_plot", mesh_options, args=(path, PEAK_VALUE)),
        )
        _run(code, "peak_value_map")
        return Drawn(code, False, {}, _about(info))

    def code_for(grid):
        return _code(
            MAP_IMPORTS,
            f"# the seasonal peak of surface {variable['name']}: the highest value of its daily climatology",
            _open_code(path, PEAK_VALUE, grid),
            "# the colours run from the 2nd to the 98th percentile of the map (robust=True)",
            _call("ds.pub_plot", options),
        )

    return _map(code_for, path, info, "peak_value_map")


def daily_cycle(results_dir, key):
    """A line of the mean over the model's domain of a variable's daily
    climatology at the surface, with its peak marked. Drawn.found holds
    "day", the day of the year of the peak, "month_day", its month and day,
    "value", the peak, (all None if there are no values) and "note", what the
    report must say about how the domain's means were worked out, which may
    be nothing."""
    info = load(results_dir)
    variable = entry(info, key)
    path = _path(results_dir, variable["phenology"]["daily"])
    starts, names = _month_ticks(_phenology_attrs(results_dir, variable))
    code = _code(
        "import matplotlib.pyplot as plt\nimport numpy as np\n"
        "from oceanval.analysis import day_order\nfrom oceanval.analysis_plots import domain_mean\n",
        f"# the area-weighted mean over the model's domain of each day of {variable['name']}'s daily climatology",
        f"path = {_py(path)}",
        f"_, values, note = domain_mean(path, {_py(key)})",
        "# the days in order through the year, in the model's calendar, and the month and day of each",
        f"order, month_days = day_order(path, {_py(key)})",
        "values = values[order]",
        "days = np.arange(1, len(values) + 1)",
        "# the day each month starts on, and the months' names",
        f"month_starts = {_py(starts)}",
        f"month_names = {_py(names)}",
        _call("fig, ax = plt.subplots", {"figsize": (8, 3.6)}),
        'ax.plot(days, values, color="#1b6f8a")',
        "# the peak, marked by a dashed line",
        "top = int(np.nanargmax(values)) if np.isfinite(values).any() else None",
        "if top is not None:",
        '    ax.axvline(days[top], color="#b5452f", linestyle="--", linewidth=1)',
        '    ax.plot(days[top], values[top], marker="o", color="#b5452f")',
        "ax.set_xticks(month_starts)",
        "ax.set_xticklabels(month_names)",
        "ax.set_xlim(1, len(values))",
        f"ax.set_ylabel({_py(legend(variable['name'], variable['units']))})",
        LINE_STYLE,
        "fig.tight_layout()",
    )
    done = _run(code, "daily_cycle")
    top = done["top"]
    if top is None:
        found = {"day": None, "month_day": None, "value": None, "note": done["note"]}
    else:
        found = {
            "day": int(done["days"][top]),
            "month_day": done["month_days"][top],
            "value": float(done["values"][top]),
            "note": done["note"],
        }
    return Drawn(code, False, found, _about(info))


def day_text(month_day):
    """A month and day as the report says it, e.g. "12 May"."""
    month, day = month_day
    return f"{day} {calendar.month_name[month]}"


# ---- the time series ----

DOMAIN_COLOUR = "#222222"
MANY_REGIONS = 6


def series_frame(results_dir, saved):
    """The time series saved for one layer of a long-term or seasonal analysis
    (its entry in what analyze() saved), as a DataFrame."""
    return pd.read_csv(os.path.join(results_dir, saved["csv"]))


def _layout(frame, panels_over=MANY_REGIONS):
    """How the domain's and regions' series in frame are drawn: on one axes
    or, if there are more than panels_over regions, on a panel for each
    region and one of its own for the domain, first. Returns (the panels, or None for one axes, and
    the lines, in the order they are drawn: (name, colour, width) for one
    axes, and (name, colour, width, the panels it is in) for panels)."""
    names = list(dict.fromkeys(frame.name))
    domain = [name for name in names if frame[frame.name == name].kind.iloc[0] == "domain"]
    others = [name for name in names if name not in domain]
    palette = plt.get_cmap("tab10" if len(others) <= 10 else "tab20")
    colours = {name: DOMAIN_COLOUR for name in domain}
    colours.update({name: to_hex(palette(i % palette.N)) for i, name in enumerate(others)})
    if len(others) > panels_over:
        lines = [
            (name, colours[name], 2.2 if name in domain else 1.4, [name])
            for name in names
        ]
        return domain + others, lines
    return None, [(name, colours[name], 2.2 if name in domain else 1.4) for name in names]


def _series_code(path, prepare, table, x, ylabel, axis_lines, hollow=False):
    """The code of a figure of series from the CSV file at path: prepare is
    the code that makes table, the series to draw, from frame, as read; x
    the code of the x of each line's part of table; axis_lines the code
    that sets each axes' x axis; and hollow, whether years that are the mean
    of fewer than 12 months are drawn as open circles."""
    # the series as the code makes them, to work out how they are drawn
    made = {"frame": pd.read_csv(path)}
    exec(prepare, made)
    panels, lines = _layout(made[table])
    imports = "import matplotlib.pyplot as plt\nimport pandas as pd\n"
    if "calendar." in axis_lines:
        imports = "import calendar\n\n" + imports
    draw = [
        f"    part = {table}[{table}.name == name]",
        f"    x = {x}",
        "    y = part.value.values",
    ]
    if hollow:
        draw += [
            "    # years that are the mean of fewer than 12 months, as open circles",
            "    hollow = ~part.complete.values.astype(bool) & (part.months.values > 0)",
        ]
    marks = [
        "ax.plot(x, y, label=name, color=colour, linewidth=width)",
    ]
    if hollow:
        marks += [
            "if hollow.any():",
            '    ax.plot(x[hollow], y[hollow], "o", markerfacecolor="white", markeredgecolor=colour, markersize=5, linestyle="none")',
        ]
    if panels is None:
        listing = _listing("lines", lines, "each line: its name in the data, its colour and its width")
        blocks = [
            _call("fig, ax = plt.subplots", {"figsize": (8, 3.8)}),
            f"ax.set_ylabel({_py(ylabel)})",
            listing,
            "for name, colour, width in lines:",
            "\n".join(draw),
            "\n".join("    " + line for line in marks),
            axis_lines,
            LINE_STYLE,
            _call("ax.legend", {"frameon": False, "fontsize": 8, "ncol": 2 if len(lines) > 4 else 1, "loc": "best"}),
        ]
    else:
        nrow = int(np.ceil(len(panels) / 3))
        blocks = [
            _listing("regions", panels, "the panels: the full domain and each region"),
            f"nrow = {nrow}",
            _call(
                "fig, grid = plt.subplots",
                {"nrows": _Code("nrow"), "ncols": 3, "figsize": _Code("(4.2 * 3, 2.6 * nrow)"), "sharex": True,
                 "squeeze": False},
            ),
            "panels = dict(zip(regions, grid.flat))",
            "for name, ax in panels.items():",
            "    ax.set_title(name, fontsize=9)",
            "for ax in list(grid.flat)[len(regions):]:",
            "    ax.set_visible(False)",
            "for ax in grid[:, 0]:",
            f"    ax.set_ylabel({_py(ylabel)})",
            _listing("lines", lines, "each line: its name in the data, its colour, its width and the panels it is drawn in"),
            "for name, colour, width, where in lines:",
            "\n".join(draw),
            "    for ax in [panels[region] for region in where]:",
            "\n".join("        " + line for line in marks),
            "for ax in panels.values():",
            "\n".join("    " + line for line in axis_lines.split("\n")),
            "for ax in panels.values():",
            "\n".join("    " + line for line in LINE_STYLE.split("\n")),
        ]
    return _code(imports, f"frame = pd.read_csv({_py(path)})", prepare.rstrip("\n"), *blocks, "fig.tight_layout()")


YEAR_TICKS = "ax.xaxis.set_major_locator(plt.MaxNLocator(integer=True))"
NOT_STATIONS = '# the domain and the regions: the stations have a figure of their own\nframe = frame[frame.kind != "station"]\n'


# the mean of each month of the year, over the years of the series, and the x axis it is drawn on
def cycle_code(by=("kind", "name")):
    """The code that makes cycle from frame: the mean of each month of the year over
    its years, of each series, which by are the columns that tell apart."""
    columns = ", ".join(_py(column) for column in (*by, "month"))
    return (
        "# the mean of each month of the year, over the years of the series\n"
        f"cycle = frame.groupby([{columns}], sort=False).value.mean().reset_index()\n"
    )


CYCLE = cycle_code()
CYCLE_AXIS = "\n".join(
    [
        "ax.set_xticks(range(1, 13))",
        "ax.set_xticklabels([calendar.month_abbr[month] for month in range(1, 13)], fontsize=8)",
        "ax.set_xlim(0.5, 12.5)",
    ]
)


def _regions_in(frame):
    return int((frame.kind == "region").groupby(frame.name).any().sum())


def long_term_plot(results_dir, key, layer):
    """The long-term series: the annual mean over the domain and over each
    region, by year. Years that are the mean of fewer than the 12 months are
    shown as open circles. Drawn.found holds "regions", the number of
    regions drawn."""
    info = load(results_dir)
    variable = entry(info, key)
    saved = variable["long_term"]["layers"][layer]
    path = _path(results_dir, saved["csv"])
    code = _series_code(
        path, NOT_STATIONS, "frame", "part.year.values", series_legend(variable, saved), YEAR_TICKS, hollow=True
    )
    done = _run(code, "long_term_plot")
    return Drawn(code, False, {"regions": _regions_in(done["frame"])}, _about(info))


def seasonal_cycle_regions(results_dir, key, layer):
    """The mean seasonal cycle: the mean of each month of the year, over the
    years of the series, over the domain and over each region. Drawn.found
    holds "means", the domain's, by month."""
    info = load(results_dir)
    variable = entry(info, key)
    saved = variable["seasonal"]["layers"][layer]
    path = _path(results_dir, saved["csv"])
    code = _series_code(
        path, NOT_STATIONS + CYCLE, "cycle", "part.month.values", legend(variable["name"], saved["units"]), CYCLE_AXIS
    )
    cycle = _run(code, "seasonal_cycle_regions")["cycle"]
    domain = cycle[cycle.kind == "domain"]
    return Drawn(code, False, {"means": dict(zip(domain.month, domain.value))}, _about(info))


def station_plot(results_dir, key, kind, layer):
    """The series at each station, by year (the long-term series) or the mean
    of each month of the year (the seasonal series), or None if there are no stations.
    Drawn.found holds "names", the stations'."""
    info = load(results_dir)
    variable = entry(info, key)
    saved = variable[kind]["layers"][layer]
    path = _path(results_dir, saved["csv"])
    frame = series_frame(results_dir, saved)
    frame = frame[frame.kind == "station"]
    if frame.empty:
        return None
    palette = plt.get_cmap("tab10")
    names = list(dict.fromkeys(frame.name))
    lines = [(name, to_hex(palette(i % 10))) for i, name in enumerate(names)]
    long_term = kind == "long_term"
    table = "frame" if long_term else "cycle"
    draw = [
        f"    part = {table}[{table}.name == name]",
        f"    x = {'part.year.values' if long_term else 'part.month.values'}",
        "    y = part.value.values",
        "    ax.plot(x, y, color=colour, label=name, linewidth=1.4)",
    ]
    if long_term:
        draw += [
            "    # years that are the mean of fewer than 12 months, as open circles",
            "    hollow = ~part.complete.values.astype(bool) & (part.months.values > 0)",
            "    if hollow.any():",
            '        ax.plot(x[hollow], y[hollow], "o", markerfacecolor="white", markeredgecolor=colour, markersize=5, linestyle="none")',
        ]
    code = _code(
        "import matplotlib.pyplot as plt\nimport pandas as pd\n" if long_term else "import calendar\n\nimport matplotlib.pyplot as plt\nimport pandas as pd\n",
        f"frame = pd.read_csv({_py(path)})",
        "# the series in the model cell nearest each station",
        'frame = frame[frame.kind == "station"]',
        *([] if long_term else [CYCLE.rstrip("\n")]),
        _call("fig, ax = plt.subplots", {"figsize": (8, 3.8)}),
        _listing("stations", lines, "each station's name in the data, and its line's colour"),
        "for name, colour in stations:",
        "\n".join(draw),
        YEAR_TICKS if long_term else CYCLE_AXIS,
        f"ax.set_ylabel({_py(series_legend(variable, saved) if long_term else legend(variable['name'], saved['units']))})",
        "ax.legend(frameon=False, fontsize=8)",
        LINE_STYLE,
        "fig.tight_layout()",
    )
    _run(code, "station_plot")
    return Drawn(code, False, {"names": names}, _about(info))


# ---- the transects ----

TRANSECT_LAND = "#d9dee0"
# the colour of the line of a transect's trends
TRANSECT_COLOUR = "#1b6f8a"


def transect_of(variable, index):
    """One transect of a variable's entry in what analyze() saved, by its
    number, which starts at 1."""
    return variable["transects"]["lines"][index - 1]


def _scale(values, diverging=False):
    """The colour limits of values and which ends of a colour bar need arrows,
    as ([low, high], extend): the 2nd to the 98th percentile, or, if diverging,
    symmetric about zero up to the 98th percentile of their size."""
    values = np.asarray(values, dtype="float64")
    values = values[np.isfinite(values)]
    if values.size == 0:
        return [0.0, 1.0], "neither"
    if diverging:
        largest = float(np.percentile(np.abs(values), 98)) or float(np.abs(values).max()) or 1.0
        low, high = -largest, largest
    else:
        low, high = (float(x) for x in np.percentile(values, [2, 98]))
        if high <= low:
            pad = abs(low) * 0.01 or 0.5
            low, high = low - pad, high + pad
    below, above = values.min() < low, values.max() > high
    extend = "both" if below and above else "min" if below else "max" if above else "neither"
    return [float(low), float(high)], extend


def _position_text(line):
    """The code of position_text, which writes a latitude or a longitude on
    an axis, and the axis's label."""
    if line["direction"] == "north-south":
        return (
            [
                "# how a latitude is written on the axis",
                "def position_text(value, _):",
                "    return f\"{abs(value):g}°{'N' if value > 0 else 'S' if value < 0 else ''}\"",
            ],
            "Latitude",
        )
    return (
        [
            "# how a longitude is written on the axis",
            "def position_text(value, _):",
            "    value = (value + 180) % 360 - 180",
            "    return f\"{abs(value):g}°{'E' if value > 0 else 'W' if value < 0 else ''}\"",
        ],
        "Longitude",
    )


def _years_range(start, end):
    return str(start) if start == end else f"{start}–{end}"


def transect_map(results_dir, key, index):
    """The map of where one transect of a variable runs: the line, its ends and
    their latitudes or longitudes, over the land (see oceanval.transects)."""
    info = load(results_dir)
    variable = entry(info, key)
    line = transect_of(variable, index)
    code = _code(
        "import matplotlib.pyplot as plt\nfrom oceanval.transects import plot_map\n",
        "# where the transect runs: the [lon, lat] of its start and its end",
        f"transect = {_py({'start': line['start'], 'end': line['end']})}",
        "# the line, its ends and their latitudes or longitudes, over a map of the land",
        "fig = plot_map(transect)",
    )
    _run(code, "transect_map")
    return Drawn(code, False, {}, _about(info))


def _section_code(path, line, panels, bars, size):
    """The code of a section through the water column along a transect: the
    panels, each (column, title, colour map, limits), side by side, with the
    colour bars, each (the panels it is for, its label, its extend)."""
    position_code, position_label = _position_text(line)
    listing = _listing(
        "panels", panels, "each panel: the column of the data it shows, its title, its colour map and colour limits"
    )
    bar_listing = _listing("bars", bars, "each colour bar: the panels it is for, its label, and which ends have arrows")
    return _code(
        "import matplotlib.pyplot as plt\nimport numpy as np\nimport pandas as pd\n"
        "from matplotlib.ticker import FuncFormatter, MaxNLocator\n",
        f"frame = pd.read_csv({_py(path)})",
        "\n".join(position_code),
        listing,
        bar_listing,
        _call(
            "fig, axes = plt.subplots",
            {"nrows": 1, "ncols": _Code("len(panels)"), "figsize": tuple(size), "sharey": True,
             "squeeze": False, "layout": "constrained"},
        ),
        "axes = axes[0]",
        "meshes = []",
        "for ax, (column, title, colours, limits) in zip(axes, panels):",
        '    grid = frame.pivot(index="depth", columns="position", values=column)',
        "    mesh = ax.pcolormesh(",
        "        grid.columns.values, grid.index.values, np.ma.masked_invalid(grid.values),",
        '        cmap=colours, vmin=limits[0], vmax=limits[1], shading="nearest",',
        "    )",
        "    # the seabed, and anything else without values, shows as land",
        f"    ax.set_facecolor({_py(TRANSECT_LAND)})",
        "    ax.set_xlim(grid.columns.min(), grid.columns.max())",
        "    ax.xaxis.set_major_locator(MaxNLocator(4))",
        "    ax.xaxis.set_major_formatter(FuncFormatter(position_text))",
        f"    ax.set_xlabel({_py(position_label)})",
        "    ax.set_title(title)",
        "    meshes.append(mesh)",
        'axes[0].set_ylabel("Depth (m)")',
        "# the surface at the top, and the axis from the shallowest depth to the deepest",
        'axes[0].set_ylim(frame.depth.max(), frame.depth.min())',
        "for which, bar_label, extend in bars:",
        "    fig.colorbar(meshes[which[0]], ax=[axes[i] for i in which], extend=extend, label=bar_label)",
    )


def _line_code(path, line, label, ylabel):
    """The code of a transect's values along it at the surface, as a line, with
    a line at zero."""
    position_code, position_label = _position_text(line)
    return _code(
        "import matplotlib.pyplot as plt\nimport pandas as pd\nfrom matplotlib.ticker import FuncFormatter\n",
        f"frame = pd.read_csv({_py(path)})",
        "\n".join(position_code),
        _call("fig, ax = plt.subplots", {"figsize": (8, 3.8)}),
        f'ax.plot(frame.position, frame.value, label={_py(label)}, color="{TRANSECT_COLOUR}", linewidth=2)',
        'ax.axhline(0, color="#999999", linewidth=0.8)',
        f"ax.set_ylabel({_py(ylabel)})",
        "ax.xaxis.set_major_formatter(FuncFormatter(position_text))",
        f"ax.set_xlabel({_py(position_label)})",
        "ax.grid(alpha=0.3)",
        'for side in ("top", "right"):',
        "    ax.spines[side].set_visible(False)",
        "fig.tight_layout()",
    )


def _depth_text(depth):
    """A depth, for the title of a panel."""
    return f"{depth:.1f} m" if abs(depth) < 10 else f"{depth:,.0f} m"


def changes_code(paths, line, panels, grid, colours, limits, extend, label, size, heading=None):
    """The code of the changes along a transect over the years: a heat map of
    the position along the transect (a longitude, or a latitude) by year, a
    panel for each of panels, each (which of the paths' CSVs it is of, the
    depth, or None at the surface, its title) or None for a place with no
    panel, in a grid of (rows, columns),
    under one colour bar. A longitude is on the x axis, and a latitude on the
    y axis, as the climatology's months are. heading is above them all."""
    position_code, position_label = _position_text(line)
    north_south = line["direction"] == "north-south"
    listing = _listing(
        "panels", panels, "each panel: the file of the data it shows, its depth (None at the surface) and its title; None for no panel"
    )
    if north_south:
        draw = [
            '    table = part.pivot(index="position", columns="year", values="value")',
            "    mesh = ax.pcolormesh(table.columns.values, table.index.values, np.ma.masked_invalid(table.values), **options)",
            "    ax.yaxis.set_major_formatter(FuncFormatter(position_text))",
            "    ax.xaxis.set_major_locator(MaxNLocator(integer=True))",
        ]
        labels = [
            f"for ax in axes[:, 0]:\n    ax.set_ylabel({_py(position_label)})",
            'for ax in axes[-1]:\n    ax.set_xlabel("Year")',
        ]
    else:
        draw = [
            '    table = part.pivot(index="year", columns="position", values="value")',
            "    mesh = ax.pcolormesh(table.columns.values, table.index.values, np.ma.masked_invalid(table.values), **options)",
            "    ax.xaxis.set_major_formatter(FuncFormatter(position_text))",
            "    ax.xaxis.set_major_locator(MaxNLocator(4))",
            "    ax.yaxis.set_major_locator(MaxNLocator(integer=True))",
        ]
        labels = [
            'for ax in axes[:, 0]:\n    ax.set_ylabel("Year")',
            f"for ax in axes[-1]:\n    ax.set_xlabel({_py(position_label)})",
        ]
    return _code(
        "import matplotlib.pyplot as plt\nimport numpy as np\nimport pandas as pd\n"
        "from matplotlib.ticker import FuncFormatter, MaxNLocator\n",
        f"frames = [pd.read_csv(path) for path in {_py(list(paths))}]",
        "\n".join(position_code),
        listing,
        f"# the colours: for changes they are about zero\ncolours = {_py(colours)}\nlimits = {_py(limits)}",
        _call(
            "fig, axes = plt.subplots",
            {"nrows": grid[0], "ncols": grid[1], "figsize": tuple(size), "sharex": True, "sharey": True,
             "squeeze": False, "layout": "constrained"},
        ),
        'options = {"cmap": colours, "vmin": limits[0], "vmax": limits[1], "shading": "nearest"}',
        "for ax, panel in zip(axes.ravel(), panels):",
        "    if panel is None:",
        "        ax.set_visible(False)",
        "        continue",
        "    which, depth, title = panel",
        "    part = frames[which]",
        "    if depth is not None:",
        "        part = part[np.isclose(part.depth, depth)]",
        *draw,
        "    # a year or a position without values shows as land",
        f"    ax.set_facecolor({_py(TRANSECT_LAND)})",
        "    ax.set_title(title)",
        "for ax in axes.ravel()[len(panels):]:",
        "    ax.set_visible(False)",
        *labels,
        *([f"fig.suptitle({_py(heading)})"] if heading else []),
        f"fig.colorbar(mesh, ax=axes.ravel().tolist(), extend={_py(extend)}, label={_py(label)})",
    )


def changes_size(line, grid):
    """The size of the figure of changes_code, from its grid of (rows, columns)."""
    rows, columns = grid
    if line["direction"] == "north-south":
        return (3.6 * columns + 1.6, 3.4 * rows + 0.8)
    return (4.8 * columns + 1.4, 3.0 * rows + 1.0)


def changes_labels(saved, name, units):
    """The label of the colour bar of changes, and its title, from what
    analyze() saved of the option, the name of the variable and its units."""
    label = {
        "absolute": legend(name, saved["units"]),
        "absolute_change": legend(f"{name} change", units),
        "percentage_change": f"{name} change (%)",
    }[saved["how"]]
    window = saved.get("window") or 1
    # what it is, then (on a line of its own, as the figure is not wide) how it was worked out
    how = [f"{window}-year rolling mean"] if window > 1 else []
    if saved["how"] != "absolute":
        how.append(f"relative to {_years_range(saved['start'], saved['start'] + window - 1)}")
    return label, saved["title"] + ("\n" + ", ".join(how) if how else "")


def changes_colours(saved):
    """The colour map of changes: a sequential one for the annual means, and, for the changes,
    one about zero."""
    return ("viridis", False) if saved["how"] == "absolute" else ("RdBu_r", True)


def _months_code(path, line, limits, extend, label, size):
    """The code of a transect's monthly climatology at the surface: month and
    position, as colours."""
    position_code, position_label = _position_text(line)
    north_south = line["direction"] == "north-south"
    draw = (
        [
            "mesh = ax.pcolormesh(np.arange(len(months)), grid.index.values, values, **options)",
            "ax.set_ylim(grid.index.min(), grid.index.max())",
            "ax.set_xticks(range(len(months)), month_names, rotation=90)",
            "ax.yaxis.set_major_formatter(FuncFormatter(position_text))",
            f"ax.set_ylabel({_py(position_label)})",
            'ax.set_xlabel("Month")',
        ]
        if north_south
        else [
            "mesh = ax.pcolormesh(grid.index.values, np.arange(len(months)), values.T, **options)",
            "ax.set_xlim(grid.index.min(), grid.index.max())",
            "ax.set_yticks(range(len(months)), month_names)",
            "# January at the top",
            "ax.invert_yaxis()",
            "ax.xaxis.set_major_formatter(FuncFormatter(position_text))",
            f"ax.set_xlabel({_py(position_label)})",
            'ax.set_ylabel("Month")',
        ]
    )
    return _code(
        "import calendar\n\nimport matplotlib.pyplot as plt\nimport numpy as np\nimport pandas as pd\n"
        "from matplotlib.ticker import FuncFormatter\n",
        f"frame = pd.read_csv({_py(path)})",
        "\n".join(position_code),
        f"# the colours run from the 2nd to the 98th percentile of the values\nlimits = {_py(limits)}",
        '# a month of the year in each column, and a position along the transect in each row',
        'grid = frame.pivot(index="position", columns="month", values="value")',
        "months = list(grid.columns)",
        "month_names = [calendar.month_abbr[month] for month in months]",
        "values = np.ma.masked_invalid(grid.values)",
        _call("fig, ax = plt.subplots", {"figsize": tuple(size), "layout": "constrained"}),
        'options = {"cmap": "viridis", "vmin": limits[0], "vmax": limits[1], "shading": "nearest"}',
        *draw,
        "# what has no values shows as land",
        f"ax.set_facecolor({_py(TRANSECT_LAND)})",
        f"fig.colorbar(mesh, ax=ax, extend={_py(extend)}, label={_py(label)})",
    )


def transect_figure(results_dir, key, index, option):
    """The figure of one option of one transect of a variable (see
    oceanval.analysis_transects.OPTIONS), or None if the model has no values
    along it. Drawn.found holds "kind": "months", "line", "section", "heatmap" or "heatmaps"."""
    info = load(results_dir)
    variable = entry(info, key)
    line = transect_of(variable, index)
    saved = line["options"][option]
    if saved["empty"]:
        return None
    path = _path(results_dir, saved["csv"])
    frame = pd.read_csv(path)
    value_label = legend(variable["name"], variable["units"])
    if option == "vertical":
        limits, extend = _scale(frame["value"])
        years = _years_range(saved["start"], saved["end"])
        code = _section_code(
            path, line, [("value", f"Mean, {years}", "viridis", limits)], [([0], value_label, extend)], (6.8, 4.2)
        )
        kind = "section"
    elif option == "climatology" and "month" in frame:
        limits, extend = _scale(frame["value"])
        size = (5.2, 4.6) if line["direction"] == "north-south" else (8.5, 4.2)
        code = _months_code(path, line, limits, extend, value_label, size)
        kind = "months"
    elif option == "climatology":
        code = _line_code(path, line, f"Mean, {_years_range(saved['start'], saved['end'])}", value_label)
        kind = "line"
    else:
        label, title = changes_labels(saved, variable["name"], variable["units"])
        colours, diverging = changes_colours(saved)
        limits, extend = _scale(frame["value"], diverging=diverging)
        if option == "vertical_trends":
            depths = sorted(frame["depth"].unique())
            columns = 2 if len(depths) > 1 else 1
            grid = (-(-len(depths) // columns), columns)
            panels = [(0, float(depth), _depth_text(depth)) for depth in depths]
            code = changes_code(
                [path], line, panels, grid, colours, limits, extend, label, changes_size(line, grid), heading=title
            )
            kind = "heatmaps" if len(depths) > 1 else "heatmap"
        else:
            code = changes_code(
                [path], line, [(0, None, title)], (1, 1), colours, limits, extend, label, changes_size(line, (1, 1))
            )
            kind = "heatmap"
    _run(code, "transect_figure")
    return Drawn(code, False, {"kind": kind}, _about(info))


def _n_depths():
    from oceanval.analysis_transects import N_DEPTHS

    return N_DEPTHS


def _depth_source(saved):
    """What the depths of the cells of a section are worked out from."""
    if saved["depths"] == "sigma":
        # raw FVCOM output (see oceanval.fvcom_transects)
        return "the mean depth of the water (h + zeta) and each sigma layer's share of it (from siglev)"
    return "the cell thickness"


def transect_text(variable, index, option):
    """What the report says of one option of one transect of a variable's entry
    in what analyze() saved: {"title", "method", "caption"}."""
    line = transect_of(variable, index)
    saved = line["options"][option]
    name = variable["name"]
    years = _years_range(saved["start"], saved["end"])
    units = unit_html(variable["units"])
    window = saved.get("window") or 1
    rolling = (
        f" The annual means were smoothed by a rolling mean of {window} years, with nctoolkit's rolling_mean, so that each year shown is the last of its {window} years."
        if window > 1 and option in ("trends", "vertical_trends")
        else ""
    )
    rolled = f" (after a {window}-year rolling mean)" if window > 1 else ""
    # what the changes are of, and where along the transect they are worked out
    if option == "vertical_trends":
        thing, at = name, "at each point along the transect and at each of the depths shown"
        in_section = " in the water column"
        depths = (
            f" Each cell is put at its depth, worked out from {_depth_source(saved)}, and the section is put on {_n_depths()} evenly spaced depths, "
            "of which the heat maps show some."
            if saved["depths"] in ("thickness", "sigma")
            else " The heat maps show some of the model's depths."
        )
    else:
        thing, at, in_section, depths = f"surface {name}", "at each point along the transect", "", ""
    # the first period the changes are from
    first = (
        f"its annual mean in {saved['start']}"
        if window == 1
        else f"the mean of the first {window} years ({saved['start']}-{saved['start'] + window - 1}), the first value of the rolling mean"
    )
    first_years = _years_range(saved["start"], saved["start"] + window - 1)
    if option == "climatology":
        if saved["months"] > 1:
            method = f"The surface climatology of {name} is its mean in each month of the year over {years}, at each point along the transect."
            caption = f"Monthly climatology of surface {name} ({units}) along the transect, {years}."
        else:
            method = f"The mean of surface {name} over {years} is shown at each point along the transect."
            caption = f"Mean surface {name} ({units}) along the transect, {years}."
    elif option == "vertical":
        method = (
            f"The annual mean of {name} is the mean of its monthly means over {years}, at each point along the transect and each depth, "
            "in a section through the water column."
            + (
                f" Each cell is put at its depth, worked out from {_depth_source(saved)}, and the section is put on {_n_depths()} evenly spaced depths."
                if saved["depths"] in ("thickness", "sigma")
                else ""
            )
        )
        caption = f"Annual mean {name} ({units}) through the water column along the transect, {years}."
    elif saved["how"] == "absolute":
        method = (
            f"The annual mean of {thing} is the mean of its months' means in each year of {years}, worked out with nctoolkit, {at}. "
            "A year is left out if it has months missing. The heat map has the position along the transect on one axis and the year on the other."
            + rolling
            + depths
        )
        caption = f"Annual mean {thing}{in_section} ({units}) along the transect, by year, {years}{rolled}."
    elif saved["how"] == "absolute_change":
        method = (
            f"The change in each year is its annual mean of {thing} minus {first}, {at}. "
            "A year's mean is the mean of its months' means, worked out with nctoolkit, and it is left out if it has months missing. "
            "The heat map has the position along the transect on one axis and the year on the other."
            + rolling
            + depths
        )
        caption = f"Change in {thing}{in_section} ({units}) along the transect, by year, relative to {first_years}, {years}{rolled}."
    else:
        method = (
            f"The percentage change in each year is its annual mean of {thing} minus {first}, "
            "as a percentage of the absolute value of the first, so that an increase is always positive. "
            f"It is missing where that is zero. A year's mean is the mean of its months' means, {at}. "
            "The heat map has the position along the transect on one axis and the year on the other."
            + rolling
            + depths
        )
        caption = f"Percentage change in {thing}{in_section} along the transect, by year, relative to {first_years}, {years}{rolled}."
    return {"title": saved["title"], "method": method, "caption": caption}


# ---- showing a figure, with its code ----

COPY_LABEL = "Copy code to reproduce figure"

COPY_STYLE = """<style>
.oceanval-figure{width:fit-content;max-width:100%;margin:0 auto}
.oceanval-figure__bar{text-align:right;margin:0 0 2px}
.oceanval-copy-code{font:12px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;color:#24425c;background:#fff;
border:1px solid #b9c3cc;border-radius:4px;padding:2px 9px;cursor:pointer}
.oceanval-copy-code:hover{background:#eef3f7;border-color:#8a9aa8}
.oceanval-copy-code:focus-visible{outline:2px solid #1b6f8a;outline-offset:1px}
.oceanval-figure img{display:block;max-width:100%;height:auto;margin:0 auto}
.oceanval-figure__code{display:block;box-sizing:border-box;width:100%;min-height:12em;margin-top:4px;
font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.oceanval-figure__code[hidden]{display:none}
@media print{.oceanval-figure__bar{display:none}}
</style>"""

COPY_SCRIPT = """<script>
window.oceanvalCopyCode = window.oceanvalCopyCode || function (button) {
  var box = button.closest(".oceanval-figure").querySelector(".oceanval-figure__code");
  var code = box.value;
  var label = button.getAttribute("data-label") || button.textContent;
  button.setAttribute("data-label", label);
  function done(ok) {
    if (ok) {
      button.textContent = "Copied";
      setTimeout(function () { button.textContent = label; }, 2000);
    } else {
      box.hidden = false;
      box.focus();
      box.select();
      button.textContent = "Copy failed: select the code below and press Ctrl+C";
    }
  }
  function fallback() {
    var temp = document.createElement("textarea");
    temp.value = code;
    temp.setAttribute("readonly", "");
    temp.style.position = "fixed";
    temp.style.top = "-1000px";
    document.body.appendChild(temp);
    temp.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (error) { ok = false; }
    document.body.removeChild(temp);
    done(ok);
  }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(code).then(function () { done(true); }, fallback);
  } else {
    fallback();
  }
};
</script>"""


def copy_block(png, code):
    """The HTML of a figure, png (its PNG, as bytes or base64 text), with a
    button above its top right corner that copies code."""
    if isinstance(png, bytes):
        png = base64.b64encode(png).decode("ascii")
    return (
        '<div class="oceanval-figure">'
        '<div class="oceanval-figure__bar">'
        '<button type="button" class="oceanval-copy-code" onclick="oceanvalCopyCode(this)" '
        'title="Copies the Python code that draws this figure, from the analysis\'s saved results, '
        'to run in the conda environment OceanVal ran in">'
        f"{COPY_LABEL}</button></div>"
        f'<img src="data:image/png;base64,{png}" alt="">'
        f'<textarea class="oceanval-figure__code" readonly hidden aria-label="The code of the figure">{html.escape(code)}</textarea>'
        "</div>"
        f"{COPY_STYLE}{COPY_SCRIPT}"
    )


def _png(fig):
    """fig as the PNG plt.show() would put in the notebook: by the
    notebook's own formatter, or as the inline backend draws it."""
    from IPython import get_ipython

    shell = get_ipython()
    if shell is not None:
        data, _ = shell.display_formatter.format(fig, include=["image/png"])
        if data.get("image/png") is not None:
            return data["image/png"]
    from IPython.core.pylabtools import print_figure

    return print_figure(fig, "png", bbox_inches="tight")


def show(drawn, with_code=False, title=None):
    """Show the figure just drawn, as plt.show() does, or, with_code, with a
    button above it that copies the script that draws it (see script):
    title says which figure it is, e.g. "Figure 2 on the Temperature page"."""
    from IPython import get_ipython

    if not with_code or drawn is None or get_ipython() is None:
        plt.show()
        return
    from IPython.display import HTML, display

    fig = plt.gcf()
    png = _png(fig)
    plt.close("all")
    display(HTML(copy_block(png, script(drawn, title))))
