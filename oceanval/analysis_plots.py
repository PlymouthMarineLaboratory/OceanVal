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
import nctoolkit as nc
import numpy as np
import pandas as pd
import xarray as xr
from matplotlib.colors import to_hex

from oceanval.analysis import PEAK_DAY, PEAK_VALUE, load
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
    blocks are left out."""
    return "\n".join(block for block in blocks if block) + "\n"


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

MAP_IMPORTS = "import matplotlib.pyplot as plt\nimport nctoolkit as nc\n"


def _lon_lat(path):
    """The longitudes and latitudes of the grid in path, as arrays."""
    with xr.open_dataset(path, decode_times=False) as ds:
        names = list(ds.coords) + list(ds.variables)
        lon = next(name for name in names if "lon" in name.lower())
        lat = next(name for name in names if "lat" in name.lower())
        return np.asarray(ds[lon].values, dtype="float64"), np.asarray(ds[lat].values, dtype="float64")


def _plot_grid(path):
    """The regular lon-lat grid to draw the grid in path on, as to_latlon's
    lon, lat and res: covering it, at about its own resolution, but no finer
    than 1/20 degree or more than 1440 cells across."""
    lon, lat = _lon_lat(path)
    lon = np.where(lon > 180, lon - 360, lon)
    lon_min, lon_max = float(np.nanmin(lon)), float(np.nanmax(lon))
    lat_min, lat_max = float(np.nanmin(lat)), float(np.nanmax(lat))
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


def _panel_size(path, panels):
    """The size, in inches, of a figure of `panels` maps of the grid in path, in
    columns of up to 4: PANEL_WIDTH wide, with the maps as tall as their shape
    needs, and room for each one's title and a colour bar."""
    ncol = 4 if panels > 3 else panels
    nrow = int(np.ceil(panels / ncol))
    lon, lat = _lon_lat(path)
    lon = np.where(lon > 180, lon - 360, lon)
    lon_span, lat_span = float(np.nanmax(lon) - np.nanmin(lon)), float(np.nanmax(lat) - np.nanmin(lat))
    if lon_span > 340 and lat_span > 160:
        aspect = 2.0
    else:
        mid_lat = 0.5 * float(np.nanmax(lat) + np.nanmin(lat))
        aspect = lon_span * np.cos(np.radians(mid_lat)) / lat_span if lat_span > 0 else 1.5
    aspect = min(max(float(aspect), 0.6), 2.5)
    map_width = (PANEL_WIDTH - 1.6) / ncol
    return [PANEL_WIDTH, round(nrow * (map_width / aspect + 0.55) + 0.3, 1)]


def monthly_maps(results_dir, key, layer):
    """The maps of each month's mean of a variable's climatology in one
    layer, on one colour scale (the 2nd to 98th percentile of all of them)."""
    info = load(results_dir)
    variable = entry(info, key)
    saved = variable["climatology"]["layers"][layer]
    path = _path(results_dir, saved["monthly"])
    limits = _limits(path, key)
    label = legend(variable["name"], saved["units"])
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
    if hasattr(nc, "panel_plot"):
        imports = "import calendar\n\n" + MAP_IMPORTS
        call = _call(
            "nc.panel_plot",
            {"ncol": _Code("ncol"), "var": key, "limits": _Code("limits"), "legend": label,
             "grid_labels": False, "size": _Code("size")},
            args=(_Code("panels"),),
        )
        draw = 'with plt.rc_context({"font.size": font_size}):\n' + "\n".join("    " + line for line in call.split("\n"))
    else:
        # nctoolkit before 1.3.2 has no panel_plot: a grid of pub_plots
        imports = "import calendar\n\nimport matplotlib.pyplot as plt\nimport nctoolkit as nc\nimport numpy as np\n"
        options = {"var": key, "limits": _Code("limits"), "title": _Code("title"), "legend": label,
                   "fig": _Code("fig"), "gs": _Code("grid[index // ncol, index % ncol]")}
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


def domain_mean(path, variable):
    """The mean of variable over the model's domain at each time in path, as
    (times, values, note): worked out with nctoolkit's spatial_mean, as
    analysis_series.robust_spatial_mean does, and note what the report must say
    about a grid whose cell areas could not be worked out ("" if all was well)."""
    from oceanval.analysis_series import robust_spatial_mean

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

    def code_for(grid):
        return _code(
            MAP_IMPORTS,
            f"# the day of the year of the seasonal peak of surface {variable['name']}",
            _open_code(path, PEAK_DAY, grid),
            "# the day each month starts on in the model's calendar, and the months' names",
            f"month_starts = {_py(starts)}",
            f"month_names = {_py(names)}",
            "# the colours are cyclic, so the end of the year is close in colour to its start",
            _call("ds.pub_plot", options),
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
        )

    return _map(code_for, path, info, "peak_day_map")


def peak_value_map(results_dir, key):
    """The map of the value of a variable's seasonal peak."""
    info = load(results_dir)
    variable = entry(info, key)
    path = _path(results_dir, variable["phenology"]["file"])
    options = {"var": PEAK_VALUE, "robust": True, "legend": legend(f"Peak {variable['name']}", variable["units"])}

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
# the domain's line in each panel, when each region has a panel of its own
PANEL_DOMAIN_COLOUR = "#999999"
MANY_REGIONS = 6


def series_frame(results_dir, saved):
    """The time series saved for one layer of a long-term or seasonal analysis
    (its entry in what analyze() saved), as a DataFrame."""
    return pd.read_csv(os.path.join(results_dir, saved["csv"]))


def _layout(frame, panels_over=MANY_REGIONS):
    """How the domain's and regions' series in frame are drawn: on one axes
    or, if there are more than panels_over regions, on a panel for each
    region, with the domain's in each. Returns (the regions with a panel of
    their own, or None for one axes, and the lines, in the order they are
    drawn: (name, colour, width) for one axes, and (name, colour, width, the
    panels it is in) for panels)."""
    names = list(dict.fromkeys(frame.name))
    domain = [name for name in names if frame[frame.name == name].kind.iloc[0] == "domain"]
    others = [name for name in names if name not in domain]
    palette = plt.get_cmap("tab10" if len(others) <= 10 else "tab20")
    colours = {name: DOMAIN_COLOUR for name in domain}
    colours.update({name: to_hex(palette(i % palette.N)) for i, name in enumerate(others)})
    if len(others) > panels_over:
        lines = [
            (name, PANEL_DOMAIN_COLOUR, 1.0, _Code("regions")) if name in domain else (name, colours[name], 1.4, [name])
            for name in names
        ]
        return others, lines
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
            _listing("regions", panels, "the regions, each in a panel of its own"),
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
            _listing(
                "lines", lines,
                "each line: its name in the data, its colour, its width and the panels it is drawn in "
                "(the domain's is in each, in grey)",
            ),
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
        path, NOT_STATIONS, "frame", "part.year.values", legend(variable["name"], saved["units"]), YEAR_TICKS, hollow=True
    )
    done = _run(code, "long_term_plot")
    return Drawn(code, False, {"regions": _regions_in(done["frame"])}, _about(info))


def seasonal_series_plot(results_dir, key, layer):
    """The seasonal series: the monthly mean over the domain and over each
    region, month by month. Drawn.found holds "regions", the number of
    regions drawn."""
    info = load(results_dir)
    variable = entry(info, key)
    saved = variable["seasonal"]["layers"][layer]
    path = _path(results_dir, saved["csv"])
    code = _series_code(
        path, NOT_STATIONS, "frame", "part.year.values + (part.month.values - 0.5) / 12",
        legend(variable["name"], saved["units"]), YEAR_TICKS,
    )
    done = _run(code, "seasonal_series_plot")
    return Drawn(code, False, {"regions": _regions_in(done["frame"])}, _about(info))


def seasonal_cycle_regions(results_dir, key, layer):
    """The mean seasonal cycle: the mean of each month of the year, over the
    years of the series, over the domain and over each region. Drawn.found
    holds "means", the domain's, by month."""
    info = load(results_dir)
    variable = entry(info, key)
    saved = variable["seasonal"]["layers"][layer]
    path = _path(results_dir, saved["csv"])
    prepare = NOT_STATIONS + (
        "# the mean of each month of the year, over the years of the series\n"
        'cycle = frame.groupby(["kind", "name", "month"], sort=False).value.mean().reset_index()\n'
    )
    axis_lines = "\n".join(
        [
            "ax.set_xticks(range(1, 13))",
            "ax.set_xticklabels([calendar.month_abbr[month] for month in range(1, 13)], fontsize=8)",
            "ax.set_xlim(0.5, 12.5)",
        ]
    )
    code = _series_code(
        path, prepare, "cycle", "part.month.values", legend(variable["name"], saved["units"]), axis_lines
    )
    cycle = _run(code, "seasonal_cycle_regions")["cycle"]
    domain = cycle[cycle.kind == "domain"]
    return Drawn(code, False, {"means": dict(zip(domain.month, domain.value))}, _about(info))


def station_plot(results_dir, key, kind, layer):
    """The series at each station, by year (the long-term series) or by
    month (the seasonal series), or None if there are no stations.
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
    x = "part.year.values" if kind == "long_term" else "part.year.values + (part.month.values - 0.5) / 12"
    draw = [
        "    part = frame[frame.name == name]",
        f"    x = {x}",
        "    y = part.value.values",
        "    ax.plot(x, y, color=colour, label=name, linewidth=1.4)",
    ]
    if kind == "long_term":
        draw += [
            "    # years that are the mean of fewer than 12 months, as open circles",
            "    hollow = ~part.complete.values.astype(bool) & (part.months.values > 0)",
            "    if hollow.any():",
            '        ax.plot(x[hollow], y[hollow], "o", markerfacecolor="white", markeredgecolor=colour, markersize=5, linestyle="none")',
        ]
    code = _code(
        "import matplotlib.pyplot as plt\nimport pandas as pd\n",
        f"frame = pd.read_csv({_py(path)})",
        "# the series in the model cell nearest each station",
        'frame = frame[frame.kind == "station"]',
        _call("fig, ax = plt.subplots", {"figsize": (8, 3.8)}),
        _listing("stations", lines, "each station's name in the data, and its line's colour"),
        "for name, colour in stations:",
        "\n".join(draw),
        YEAR_TICKS,
        f"ax.set_ylabel({_py(legend(variable['name'], saved['units']))})",
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
    a line at zero, where a trend or a change turns over."""
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
    along it. Drawn.found holds "kind": "months", "line" or "section"."""
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
        label = {
            "absolute": legend(f"{variable['name']} trend", saved["units"]),
            "absolute_change": legend(f"{variable['name']} change", variable["units"]),
            "percentage_change": f"{variable['name']} change (%)",
        }[saved["how"]]
        title = saved["title"] + (f", {saved['window']}-year rolling mean" if (saved.get("window") or 1) > 1 else "")
        code = _line_code(path, line, title, label)
        kind = "line"
    _run(code, "transect_figure")
    return Drawn(code, False, {"kind": kind}, _about(info))


def _n_depths():
    from oceanval.analysis_transects import N_DEPTHS

    return N_DEPTHS


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
        f" The annual means were smoothed by a rolling mean of {window} years, with nctoolkit's rolling_mean, before this was worked out."
        if window > 1 and option == "trends"
        else ""
    )
    rolled = f" (after a {window}-year rolling mean)" if window > 1 else ""
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
                f" Each cell is put at its depth, worked out from the cell thickness, and the section is put on {_n_depths()} evenly spaced depths."
                if saved["depths"] == "thickness"
                else ""
            )
        )
        caption = f"Annual mean {name} ({units}) through the water column along the transect, {years}."
    elif saved["how"] == "absolute":
        method = (
            f"The trend is the slope of a straight line fitted by least squares to the annual means of surface {name} over {years}, at each point along the transect, in {unit_html(saved['units'])}. "
            "A year's mean is the mean of its months' means, worked out with nctoolkit, and it is left out if it has months missing."
            + rolling
        )
        caption = f"Trend in surface {name} along the transect, {years}, in {unit_html(saved['units'])}{rolled}."
    elif saved["how"] == "absolute_change":
        method = (
            (
                f"The change is the annual mean of surface {name} in {saved['end']} minus its annual mean in {saved['start']}, at each point along the transect. "
                "A year's mean is the mean of its months' means, worked out with nctoolkit."
                if window == 1
                else f"The change is the mean of the last {window} years of surface {name} ({saved['end'] - window + 1}-{saved['end']}) minus the mean of the first {window} ({saved['start']}-{saved['start'] + window - 1}), at each point along the transect."
            )
            + rolling
        )
        caption = f"Change in surface {name} ({units}) along the transect, from {saved['start']} to {saved['end']}{rolled}."
    else:
        method = (
            (
                f"The percentage change is the annual mean of surface {name} in {saved['end']} minus its annual mean in {saved['start']}, "
                if window == 1
                else f"The percentage change is the mean of the last {window} years of surface {name} ({saved['end'] - window + 1}-{saved['end']}) minus the mean of the first {window} ({saved['start']}-{saved['start'] + window - 1}), "
            )
            + "as a percentage of the absolute value of the first, so that an increase is always positive. "
            "It is missing where that is zero. A year's mean is the mean of its months' means."
            + rolling
        )
        caption = f"Percentage change in surface {name} along the transect, from {saved['start']} to {saved['end']}{rolled}."
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
