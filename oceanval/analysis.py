"""Analyze a simulation on its own, with no observations: analyze().

For each variable it is given, analyze() finds the files of the simulation
holding it, reads their times to find any gaps, and works out

- a climatology: the mean of each month of the year, and the annual mean,
  over the years asked for, at the surface and/or through the water column
  (its vertical mean or its depth integral), and
- its phenology: the day of the year of its seasonal peak at the surface,
  from its daily climatology (its mean on each day of the year), which needs
  daily output.

What it works out is kept as netCDF, and shown in an HTML report built the
way validate() builds the validation report, from notebook templates. All of
it is in one folder, oceanval_analysis, in out_dir:

    oceanval_analysis/report.html   a link to the report's first page
    oceanval_analysis/results/      the fields, and analysis.json, which says
                                    what is in them
    oceanval_analysis/report/       the report's book; its pages are in
                                    _build/html/notebooks

The oceanval window (see oceanval.app) writes a script calling analyze() and
runs it, as it does a matchup script.
"""

import datetime
import gc
import glob
import importlib.resources
import json
import os
import re
import shutil
import warnings
import webbrowser

import nctoolkit as nc
import numpy as np
import pandas as pd
import xarray as xr

from oceanval import time_res as time_resolution
from oceanval.create_recipes import _file_pattern, _looks_like_fvcom, simulation_paths
from oceanval.matchall import _restores_nctoolkit_options

# the folder analyze() puts everything in, in its out_dir, and what is in it
FOLDER = "oceanval_analysis"
RESULTS = "results"
REPORT = "report"
REPORT_LINK = "report.html"
# the file in RESULTS that says what is in it
INFO_FILE = "analysis.json"
# the report's first page
LANDING = "000_overview"

# a climatology's layers: the surface, the bottom, and the two ways of taking
# the water column, as climatology's water_column gives them
SURFACE = "surface"
BOTTOM = "bottom"
WATER_COLUMN = ("mean", "integral")
LAYER_TITLES = {
    SURFACE: "Surface",
    BOTTOM: "Bottom",
    "mean": "Water column: vertical mean",
    "integral": "Water column: depth integral",
}

# thickness for output on fixed depth levels
Z_LEVEL = "z_level"

# the netCDF variables of a phenology's file
PEAK_DAY = "peak_day"
PEAK_VALUE = "peak_value"
# everything analyze() can work out of a variable
KINDS = ("climatology", "phenology", "long_term", "seasonal", "map_changes", "transects")


# ---- the arguments ----


def variable_key(name):
    """A variable's name as its files and its netCDF variable are named:
    lower case letters, digits and underscores, e.g. "chlorophyll_a" for
    "Chlorophyll a"."""
    key = re.sub(r"[^0-9a-zA-Z]+", "_", str(name).strip()).strip("_").lower()
    if not key:
        return ""
    return key if not key[0].isdigit() else f"v_{key}"


def model_variable_names(model_variable):
    """The model variables in one model variable, several summed as "a+b"."""
    return [name.strip() for name in str(model_variable).split("+") if name.strip()]


def _year(value, what):
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        try:
            if isinstance(value, str) and re.fullmatch(r"\s*\d{1,4}\s*", value):
                return int(value)
        except ValueError:
            pass
        raise ValueError(f"{what} must be a year, e.g. 2011, not {value!r}.")
    return int(value)


def _years(entry, name, kind):
    """The start and end years of one variable's entry in climatology or
    phenology."""
    if not isinstance(entry, dict):
        raise ValueError(f"{kind}[{name!r}] must be a dict with start and end years.")
    for part in ("start", "end"):
        if part not in entry:
            raise ValueError(f"{kind}[{name!r}] has no {part} year.")
    start = _year(entry["start"], f"The start of {kind}[{name!r}]")
    end = _year(entry["end"], f"The end of {kind}[{name!r}]")
    if end < start:
        raise ValueError(f"The end of {kind}[{name!r}], {end}, is before its start, {start}.")
    return start, end


# the kinds of analysis that choose layers (the surface, the water column), and their names in the report
LAYERED = ("climatology", "long_term", "seasonal")


# the ways the long-term series can be shown, as the window's drop-down has them: the annual means
# as they are, or each year's change from the first year (the first with a value), in the units or in per cent
SHOW_ABSOLUTE = "absolute"
SHOW_ABSOLUTE_CHANGE = "absolute_change"
SHOW_PERCENTAGE_CHANGE = "percentage_change"
SHOWS = {
    SHOW_ABSOLUTE: "Absolute value",
    SHOW_ABSOLUTE_CHANGE: "Absolute change",
    SHOW_PERCENTAGE_CHANGE: "Percentage change",
}
# the least years a change needs, once the rolling mean has taken its toll
MIN_CHANGE_YEARS = 2


def _series_options(entry, name, start, end):
    """The "window" (the years of the rolling mean of the annual means, 1 for
    none) and "show" (see SHOWS) of a long_term entry, checked against its years:
    (window, show)."""
    window = entry.get("window", 1)
    if isinstance(window, bool) or not isinstance(window, (int, np.integer)) or window < 1:
        raise ValueError(f"The window of long_term[{name!r}] must be a whole number of years, 1 or more, not {window!r}.")
    window = int(window)
    show = entry.get("show", SHOW_ABSOLUTE)
    if show not in SHOWS:
        raise ValueError(f"The show of long_term[{name!r}] must be one of {', '.join(repr(x) for x in SHOWS)}, not {show!r}.")
    years = end - start + 1
    if window > years:
        raise ValueError(
            f"The window of long_term[{name!r}], {window} years, is more than the {years} years from {start} to {end}."
        )
    if show != SHOW_ABSOLUTE and years - window + 1 < MIN_CHANGE_YEARS:
        raise ValueError(
            f"A change in long_term[{name!r}] needs at least {MIN_CHANGE_YEARS + window - 1} years"
            + (f" with a rolling mean of {window}" if window > 1 else "")
            + f", not the {years} from {start} to {end}."
        )
    return window, show


def _layers(entry, name, kind):
    """The layers of one variable's entry in a layered kind (see LAYERED):
    SURFACE and/or BOTTOM and/or "mean" or "integral"."""
    layers = []
    if entry.get("surface", False):
        layers.append(SURFACE)
    if entry.get("bottom", False):
        layers.append(BOTTOM)
    water_column = entry.get("water_column")
    if water_column not in (None, False):
        if water_column not in WATER_COLUMN:
            raise ValueError(
                f"The water_column of {kind}[{name!r}] must be \"mean\", "
                f"\"integral\" or None, not {water_column!r}."
            )
        layers.append(water_column)
    if not layers:
        raise ValueError(
            f"{kind}[{name!r}] needs surface=True, bottom=True, or a water_column of "
            '"mean" or "integral".'
        )
    return layers


def check_arguments(
    variables,
    climatology=None,
    phenology=None,
    thickness=None,
    time_shift=None,
    long_term=None,
    seasonal=None,
    transects=None,
    map_changes=None,
):
    """analyze()'s variables, climatology, phenology, long_term, seasonal,
    transects and map_changes, checked, as

        [{"name", "key", "model_variable", "parts",
          "climatology", "long_term", "seasonal": None or
          {"start", "end", "layers": [SURFACE and/or "mean" or "integral"]},
          "phenology": None or {"start", "end"},
          "transects": None or {"start", "end", "lines": [...]},
          "map_changes": None or {"plot", "change", "layers", "periods": [...]}}]

    in the order of variables. Raises ValueError saying what is wrong."""
    from oceanval import time_stamps

    if not isinstance(variables, dict) or not variables:
        raise ValueError(
            'variables must be a dict of the names to give the variables and the model '
            'variables holding them, e.g. {"Chlorophyll": "P1_Chl+P2_Chl"}.'
        )
    given_by_kind = {
        "climatology": climatology or {},
        "phenology": phenology or {},
        "long_term": long_term or {},
        "seasonal": seasonal or {},
        "transects": transects or {},
        "map_changes": map_changes or {},
    }
    for kind, given in given_by_kind.items():
        if not isinstance(given, dict):
            raise ValueError(f"{kind} must be a dict, keyed by the names in variables.")
        unknown = [name for name in given if name not in variables]
        if unknown:
            raise ValueError(
                f"{kind} names {', '.join(repr(x) for x in unknown)}, which "
                + ("is" if len(unknown) == 1 else "are")
                + " not in variables."
            )
    if not any(given_by_kind.values()):
        raise ValueError(
            "Give climatology, phenology, long_term, seasonal, map_changes or transects, or more than one: there is nothing to analyze."
        )
    if not time_stamps.valid(time_shift):
        raise ValueError("time_shift must be None or a whole number of days, e.g. -1.")

    rows = []
    keys = {}
    for name, model_variable in variables.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Each variable needs a name.")
        key = variable_key(name)
        if not key:
            raise ValueError(f"{name!r} needs a letter or a digit in it, to name its files.")
        if key in keys:
            raise ValueError(
                f"{keys[key]!r} and {name!r} would name their files the same, {key}: "
                "give them names that differ by more than case and punctuation."
            )
        keys[key] = name
        if not isinstance(model_variable, str) or not model_variable_names(model_variable):
            raise ValueError(f"Give the model variable for {name!r}.")
        row = {
            "name": name.strip(),
            "key": key,
            "model_variable": "+".join(model_variable_names(model_variable)),
            "parts": model_variable_names(model_variable),
            "climatology": None,
            "phenology": None,
            "long_term": None,
            "seasonal": None,
            "transects": None,
            "map_changes": None,
        }
        for kind in LAYERED:
            if name in given_by_kind[kind]:
                entry = given_by_kind[kind][name]
                start, end = _years(entry, name, kind)
                row[kind] = {"start": start, "end": end, "layers": _layers(entry, name, kind)}
                if kind == "long_term":
                    row[kind]["window"], row[kind]["show"] = _series_options(entry, name, start, end)
                elif {"window", "show"} & set(entry):
                    raise ValueError(f"window and show are for long_term only, not {kind}[{name!r}].")
        if name in given_by_kind["phenology"]:
            start, end = _years(given_by_kind["phenology"][name], name, "phenology")
            row["phenology"] = {"start": start, "end": end}
        if name in given_by_kind["map_changes"]:
            from oceanval import analysis_changes

            row["map_changes"] = analysis_changes.check_entry(given_by_kind["map_changes"][name], name)
        if name in given_by_kind["transects"]:
            from oceanval import analysis_transects

            lines = analysis_transects.check_lines(given_by_kind["transects"][name], name)
            years = [x for line in lines for x in line.values() if isinstance(x, dict)]
            # the years of all of its options, which the files it needs are chosen from
            row["transects"] = {
                "start": min(x["start"] for x in years),
                "end": max(x["end"] for x in years),
                "lines": lines,
            }
        rows.append(row)

    if any(
        layer in WATER_COLUMN
        for row in rows
        for kind in LAYERED + ("map_changes",)
        if row[kind]
        for layer in row[kind]["layers"]
    ) or any(
        line["vertical"] or line["vertical_trends"]
        for row in rows
        if row["transects"]
        for line in row["transects"]["lines"]
    ):
        if not isinstance(thickness, str) or not thickness.strip():
            raise ValueError(
                "The water column, and a vertical transect, needs the cell thickness: give thickness as the name of "
                f'the thickness variable in the output (e.g. "e3t"), a thickness file, or "{Z_LEVEL}" '
                "for output on fixed depth levels."
            )
    return [row for row in rows if any(row[kind] for kind in KINDS)]


def check_as_missing(as_missing):
    """as_missing, as a float or a list of one or two floats, or None.
    Raises ValueError saying what is wrong."""
    if as_missing is None:
        return None
    values = as_missing if isinstance(as_missing, (list, tuple)) else [as_missing]
    if not 1 <= len(values) <= 2 or any(
        isinstance(x, bool) or not isinstance(x, (int, float, np.integer, np.floating)) for x in values
    ):
        raise ValueError("as_missing must be a number, or [minimum, maximum], or None.")
    if len(values) == 2 and values[1] < values[0]:
        raise ValueError("The maximum of as_missing must not be less than its minimum.")
    if isinstance(as_missing, (list, tuple)):
        return [float(x) for x in values]
    return float(as_missing)


def check_area(rows, subregions=None, lon_lim=None, lat_lim=None, stations=None):
    """The area the long-term and seasonal series are of, checked: {"subregions":
    None, "nwes", "global" or the full path of a regions file, "region_file": the
    file of the regions or None, "lon_lim", "lat_lim", "stations": {name: [lon,
    lat]}}. rows are check_arguments's. Raises ValueError saying what is wrong,
    including if the area is given with nothing it is the area of."""
    import oceanval
    from oceanval import analysis_series

    lon_lim, lat_lim = analysis_series.check_limits(lon_lim, lat_lim)
    stations = analysis_series.check_stations(stations)
    region_file = None
    if subregions is not None:
        if subregions in ("nwes", "global"):
            region_file = str(_data(f"{subregions}_subdomains.nc"))
        elif isinstance(subregions, str) and subregions.endswith(".nc"):
            subregions = os.path.abspath(os.path.expanduser(subregions))
            oceanval._check_region_file(subregions)
            region_file = subregions
        else:
            raise ValueError("subregions must be None, 'nwes', 'global' or a path to a .nc file.")
    series = any(row[kind] for row in rows for kind in ("long_term", "seasonal"))
    if not series and (subregions is not None or lon_lim is not None or stations):
        raise ValueError(
            "subregions, lon_lim, lat_lim and stations are for the long_term and seasonal series: "
            "give one of them, or leave them out."
        )
    return {
        "subregions": subregions,
        "region_file": region_file,
        "lon_lim": lon_lim,
        "lat_lim": lat_lim,
        "stations": stations,
    }


def _variables_of(path):
    """The variables of one file, {name: {"long_name", "units", "levels"}},
    as CDO reads them (so not coordinates or bounds), or as xarray reads its
    data variables if CDO cannot read it. None if neither can."""
    found = None
    try:
        with warnings.catch_warnings(record=True):
            contents = nc.open_data(path, checks=False).contents
        found = {
            str(row.variable): {
                "long_name": "" if pd.isna(row.long_name) else str(row.long_name),
                "units": "" if pd.isna(row.unit) else str(row.unit),
                "levels": int(row.nlevels),
            }
            for row in contents.itertuples()
        }
    except Exception:
        try:
            with xr.open_dataset(path, decode_times=False) as ds:
                found = {}
                for name, variable in ds.data_vars.items():
                    vertical = [
                        size
                        for dim, size in zip(variable.dims, variable.shape)
                        if any(word in dim.lower() for word in ("depth", "lev", "sig", "s_rho", "z"))
                        and "time" not in dim.lower()
                    ]
                    found[str(name)] = {
                        "long_name": str(variable.attrs.get("long_name", "")),
                        "units": str(variable.attrs.get("units", "")),
                        "levels": int(vertical[0]) if vertical else 1,
                    }
        except Exception:
            found = None
    # nctoolkit's contents leaves the file open until it is collected, which
    # stops its folder being removed on NFS
    gc.collect()
    return found


def streams(sim_dir, n_dirs_down, exclude=None, require=None, progress=None):
    """The output streams of a simulation: its files n_dirs_down directories
    below sim_dir that the filters keep (see
    create_recipes.simulation_paths), grouped by their names with the dates
    blanked out (see create_recipes._file_pattern), with the variables of
    each stream's first file, as

        [{"pattern", "files" (sorted paths), "variables": {name: {"long_name",
          "units", "levels"}}}]

    sorted by pattern. A stream whose first file cannot be read is left out.
    progress(done, total) is called as the streams are read."""
    grouped = {}
    for path in simulation_paths(sim_dir, n_dirs_down, exclude, require):
        grouped.setdefault(_file_pattern(path), []).append(path)
    found = []
    for done, (pattern, files) in enumerate(sorted(grouped.items())):
        if progress is not None:
            progress(done, len(grouped))
        variables = _variables_of(files[0])
        if variables is None:
            continue
        found.append({"pattern": pattern, "files": files, "variables": variables})
    if progress is not None:
        progress(len(grouped), len(grouped))
    return found


def _file_times(path):
    """The year, month and day of each time in one file."""
    from oceanval.matchall import _file_times as times_of

    return times_of(path)


def stream_time_res(files):
    """The time resolution of a stream's files (see oceanval.time_res.label),
    from the times of its first files: one file with a single time says
    nothing alone, so the next are read too, as matchup does."""
    read = []
    frames = []
    for path in files[:6]:
        try:
            times = _file_times(path)
        except Exception:
            continue
        dates = list(zip(times.year, times.month, times.day))
        if dates in read:
            continue
        read.append(dates)
        frames.append(times)
        if sum(len(x) for x in frames) > 1 or len(read) >= 3:
            break
    if not frames:
        return None
    return time_resolution.label(pd.concat(frames, ignore_index=True))


def place(model_variable, found):
    """The streams of found (see streams) holding every one of the model
    variables summed in model_variable. Raises ValueError if none does,
    saying which are not in the output, or that they are in different
    files."""
    parts = model_variable_names(model_variable)
    holding = [stream for stream in found if all(part in stream["variables"] for part in parts)]
    if holding:
        return holding
    missing = [part for part in parts if not any(part in stream["variables"] for stream in found)]
    if missing:
        raise ValueError(
            f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not in the "
            "simulation's output."
        )
    raise ValueError(
        f"{' and '.join(parts)} are not in the same files, so cannot be summed: "
        + "; ".join(
            f"{part} is in "
            + ", ".join(stream["pattern"] for stream in found if part in stream["variables"])
            for part in parts
        )
        + "."
    )


def choose_stream(holding, time_res_of=stream_time_res):
    """The stream to read a variable from, of those holding it: the one with
    the finest time resolution, as matchup chooses, then the most files,
    then by pattern. Returns (stream, time_res)."""
    if len(holding) == 1:
        return holding[0], time_res_of(holding[0]["files"])
    resolutions = [time_res_of(stream["files"]) for stream in holding]
    ranked = sorted(
        zip(holding, resolutions),
        key=lambda pair: (
            time_resolution.sort_key(pair[1] or time_resolution.MONTHLY),
            -len(pair[0]["files"]),
            pair[0]["pattern"],
        ),
    )
    return ranked[0]


def describe_variable(model_variable, stream):
    """The units, long_name and number of levels of model_variable in a
    stream, those of its first part where they differ."""
    parts = model_variable_names(model_variable)
    first = stream["variables"][parts[0]]
    return {
        "units": first["units"],
        "long_name": first["long_name"] if len(parts) == 1 else " + ".join(
            stream["variables"][part]["long_name"] or part for part in parts
        ),
        "levels": max(stream["variables"][part]["levels"] for part in parts),
    }


# ---- working them out ----


def integral_units(units):
    """The units of a depth integral of a variable in units: per m3 becomes
    per m2, anything else is multiplied by m."""
    units = str(units or "").strip()
    for volume, area in (
        (" m-3", " m-2"),
        ("/m3", "/m2"),
        ("/m^3", "/m^2"),
        ("/m**3", "/m**2"),
        ("m-3", "m-2"),
    ):
        if units.endswith(volume):
            return units[: -len(volume)] + area
        if f"{volume} " in units:
            return units.replace(f"{volume} ", f"{area} ", 1)
    return f"{units} m" if units else "m"


def _model_data(files, row, years, time_shift):
    """The model's output of row's variable in years, from files, as a
    dataset with one variable, named row["key"]."""
    ds = nc.open_data(files, checks=False)
    selected = ",".join(str(year) for year in range(years[0], years[1] + 1))
    if time_shift:
        # shifted before the years are chosen, as matchup does (see
        # oceanval.gridded): subset(years=) would leave out the files whose
        # years on disk are not the years they are shifted into
        ds.shift(days=time_shift)
        ds.cdo_command(f"-selyear,{selected}")
    else:
        ds.subset(years=list(range(years[0], years[1] + 1)))
    ds.subset(variables=row["parts"])
    if row.get("as_missing") is not None:
        # as matchup does: before the variables are summed, so that a cell with a missing part is missing
        ds.as_missing(row["as_missing"])
    if len(row["parts"]) > 1:
        ds.cdo_command(f"-expr,{row['key']}={'+'.join(row['parts'])}")
    else:
        ds.rename({row["parts"][0]: row["key"]})
    return ds


def _labelled(ds, row, units, what):
    ds.set_units({row["key"]: units})
    ds.set_longnames({row["key"]: f"{row['name']}, {what}"})


def thickness_data(thickness, files, as_missing=None):
    """The cell thickness for the water column, as a dataset with one
    variable, or Z_LEVEL. thickness is the name of the thickness variable in
    the output, found in files (a stream's), or a file with it, as matchup
    takes it. As in matchup, its first time is used, if it has times."""
    from oceanval.matchall import _thickness_file, _thickness_from_file, _thickness_levels

    if thickness == Z_LEVEL:
        return Z_LEVEL
    if os.path.isfile(thickness):
        name = _thickness_from_file(thickness)
        path = thickness
        label = f"{name} in {thickness}"
    else:
        name = thickness
        path = _thickness_file(files, thickness)
        label = f"{name} in {path}"
    ds = nc.open_data(path, checks=False)
    with warnings.catch_warnings(record=True):
        if len(ds.times) > 0:
            ds.subset(time=0, variables=name)
        else:
            ds.subset(variables=name)
        ds.run()
        ds.subset(variables=_thickness_levels(ds, name, label))
        ds.run()
        if as_missing is not None:
            ds.as_missing(as_missing)
            ds.run()
    return ds


def monthly_means_of(files, row, layer, years, time_shift, levels, thickness=None):
    """The mean of each month of each year of row's variable in one layer
    (see LAYER_TITLES) over years, as a dataset with a time for each."""
    ds = _model_data(files, row, years, time_shift)
    with warnings.catch_warnings(record=True):
        if layer == SURFACE and levels > 1:
            # the first value from the top that is not missing, as matchup takes the surface
            ds.cdo_command("topvalue")
        elif layer == BOTTOM:
            # the deepest value that is not missing, whatever level that is in each cell
            ds.bottom(choice="value")
        ds.merge("time")
        # each month of each year first, so a month of daily output counts
        # as much as a month of monthly output
        ds.tmean(["year", "month"], align="left")
        ds.run()
        if layer in WATER_COLUMN:
            how = ds.vertical_mean if layer == "mean" else ds.vertical_integration
            # not thickness == Z_LEVEL: a dataset's == compares its values
            if isinstance(thickness, str) and thickness == Z_LEVEL:
                how(fixed=True)
            else:
                how(thickness=thickness)
        ds.run()
    return ds


def climatology_of(files, row, layer, years, time_shift, levels, thickness=None):
    """The monthly climatology of row's variable in one layer (see
    LAYER_TITLES) over years, as a dataset of up to 12 times."""
    ds = monthly_means_of(files, row, layer, years, time_shift, levels, thickness)
    with warnings.catch_warnings(record=True):
        ds.tmean("month", align="left")
        ds.run()
    return ds


def daily_climatology_of(files, row, years, time_shift, levels):
    """The daily climatology of row's variable at the surface over years:
    its mean on each day of the year."""
    ds = _model_data(files, row, years, time_shift)
    with warnings.catch_warnings(record=True):
        if levels > 1:
            ds.cdo_command("topvalue")
        ds.merge("time")
        ds.tmean("day")
        ds.run()
    return ds


def _time_dim(variable, ds):
    for dim in variable.dims:
        if dim in ds.variables and "since" in str(ds[dim].attrs.get("units", "")):
            return dim
    for dim in variable.dims:
        if "time" in dim.lower():
            return dim
    raise ValueError(f"{variable.name} has no time dimension.")


def day_order(path, variable):
    """The days of the year of a daily climatology in path, as (order,
    month_days): order the indices of its times sorted by month and day,
    month_days the month and day of each, so sorted. Their years are not
    used: CDO dates each day of a climatology in the last year it has it
    in, so 29 February can come first, from an earlier leap year."""
    import cftime

    with xr.open_dataset(path, decode_times=False) as ds:
        time = ds[_time_dim(ds[variable], ds)]
        dates = cftime.num2date(
            time.values, time.attrs["units"], time.attrs.get("calendar", "standard")
        )
    month_days = [(int(date.month), int(date.day)) for date in np.atleast_1d(dates)]
    order = sorted(range(len(month_days)), key=lambda i: month_days[i])
    return order, [month_days[i] for i in order]


def month_starts(month_days):
    """The day of the year each month starts on, in a climatology whose days
    are month_days (sorted): {month: day}."""
    starts = {}
    for day, (month, _) in enumerate(month_days, start=1):
        starts.setdefault(month, day)
    return starts


def peak(path, variable, out):
    """The phenology of the daily climatology in path: the day of the year
    of variable's peak in each grid cell (1 for the first day of the
    climatology's own calendar), and the peak itself, saved in out as
    PEAK_DAY and PEAK_VALUE. The first day of the highest value is taken. A
    cell with no values has neither. Read a band of rows at a time, so a
    fine grid does not need all its days in memory at once."""
    order, month_days = day_order(path, variable)
    with xr.open_dataset(path) as ds:
        values = ds[variable]
        time_dim = _time_dim(values, ds)
        dims = [dim for dim in values.dims if dim != time_dim]
        shape = [values.sizes[dim] for dim in dims]
        days = np.full(shape, np.nan, dtype="float32")
        highest = np.full(shape, np.nan, dtype="float32")
        # the rows: the dimension before the last, or the only one
        band_dim = dims[-2] if len(dims) >= 2 else dims[-1]
        band_axis = dims.index(band_dim)
        size = values.sizes[band_dim]
        cells = int(np.prod(shape)) // max(size, 1)
        step = max(1, int(5e7 // max(cells * len(order), 1)))
        for start in range(0, size, step):
            band = values.isel({band_dim: slice(start, start + step)})
            data = band.transpose(time_dim, *dims).values[order].astype("float64")
            missing = np.isnan(data).all(axis=0)
            filled = np.where(np.isnan(data), -np.inf, data)
            first = filled.argmax(axis=0).astype("float32") + 1
            top = filled.max(axis=0).astype("float32")
            first[missing] = np.nan
            top[missing] = np.nan
            where = [slice(None)] * len(dims)
            where[band_axis] = slice(start, start + step)
            days[tuple(where)] = first
            highest[tuple(where)] = top
        coords = {
            name: coord for name, coord in values.coords.items() if time_dim not in coord.dims
        }
        starts = month_starts(month_days)
        result = xr.Dataset(
            {
                PEAK_DAY: (dims, days, {
                    "long_name": "Day of the year of the seasonal peak",
                    "units": "day of year",
                }),
                PEAK_VALUE: (dims, highest, {
                    "long_name": "Seasonal peak",
                    "units": str(values.attrs.get("units", "")),
                }),
            },
            coords=coords,
            attrs={
                "days": len(order),
                "month_starts": [starts[month] for month in sorted(starts)],
                "months": sorted(starts),
                "source": os.path.basename(path),
            },
        )
        for name in (PEAK_DAY, PEAK_VALUE):
            result[name].encoding["_FillValue"] = np.float32(-1e20)
        result.to_netcdf(out)
    return out


def _save(ds, path):
    if os.path.exists(path):
        os.remove(path)
    ds.to_nc(path, zip=True)


# ---- the report ----


def _data(name):
    return importlib.resources.files("oceanval").joinpath(f"data/{name}")


def _fill_notebook(source, out, replacements, drop_chunks=()):
    """Write the notebook template source to out, with each placeholder in
    replacements put in place in its cells' text, and the chunks in
    drop_chunks taken out."""
    import nbformat

    notebook = nbformat.read(str(source), as_version=4)
    for cell in notebook.cells:
        text = cell.source
        for chunk in drop_chunks:
            text = "\n".join(line for line in text.split("\n") if line.strip() != chunk)
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        cell.source = text
    nbformat.write(notebook, out)


def _toc(book_dir, pages):
    """The book's _toc.yml: the overview, then each variable's page. The
    sidebar of the pages is made from its captions."""
    with open(os.path.join(book_dir, "_toc.yml"), "w") as file:
        file.write("format: jb-book\nroot: intro\nparts:\n")
        file.write("- caption: Overview\n  chapters:\n")
        file.write(f"  - file: notebooks/{LANDING}.ipynb\n")
        if pages:
            file.write("- caption: Variables\n  chapters:\n")
            for page in pages:
                file.write(f"  - file: notebooks/{page}.ipynb\n")


def build_report(folder, info):
    """Build the analysis report in folder (the analysis's FOLDER) from what
    analyze() saved there (info, as in INFO_FILE), the way validate() builds
    its one. Returns the path of its first page."""
    import oceanval
    from oceanval.chunkers import add_chunks

    book_dir = os.path.join(folder, REPORT)
    notebooks_dir = os.path.join(book_dir, "notebooks")
    if os.path.exists(book_dir):
        oceanval._remove_notebook_temp_files(book_dir)
        gc.collect()
        shutil.rmtree(book_dir)
    os.makedirs(notebooks_dir)

    _fill_notebook(_data("analysis_overview.ipynb"), os.path.join(notebooks_dir, f"{LANDING}.ipynb"), {})
    pages = []
    for number, entry in enumerate(info["variables"], start=1):
        page = f"{number:03d}_{entry['key']}"
        drop = []
        if not entry.get("climatology"):
            drop.append("chunk_climatology")
        if not entry.get("phenology"):
            drop.append("chunk_phenology")
        if not entry.get("long_term"):
            drop.append("chunk_longterm")
        if not entry.get("seasonal"):
            drop.append("chunk_seasonalseries")
        if not entry.get("map_changes"):
            drop.append("chunk_mapchanges")
        if not entry.get("transects"):
            drop.append("chunk_transects")
        _fill_notebook(
            _data("analysis_template.ipynb"),
            os.path.join(notebooks_dir, f"{page}.ipynb"),
            {
                "template_key": entry["key"],
                "template_title": entry["name"],
                # where the figures' code reads the results from
                "template_results_dir": os.path.join(os.path.abspath(folder), RESULTS),
            },
            drop,
        )
        pages.append(page)

    with open(os.path.join(book_dir, "_config.yml"), "w") as file:
        # the scaffold the classic jupyter-book (<2) build needs, as compare() writes it
        file.write(
            "title:\n"
            "author:   \"Robert Wilson | Plymouth Marine Laboratory\"\n"
            "logo: \"pml_logo.jpg\"\n"
            "copyright:  Plymouth Marine Laboratory\n"
            "execute:\n"
            "  execute_notebooks: force\n"
            "  timeout: 500\n"
            "  allow_errors: true\n"
            "sphinx:\n"
            "  config:\n"
            "    html_static_path: ['_static']\n"
            "    html_css_files: ['custom.css']\n"
        )
    _toc(book_dir, pages)
    with open(os.path.join(book_dir, "intro.md"), "w") as file:
        file.write(
            "# An ocean model analysis using **oceanval**\n\n"
            "The simulation was analysed using the Python package **oceanval**.\n"
        )
    os.makedirs(os.path.join(book_dir, "_static"), exist_ok=True)
    shutil.copyfile(_data("_static/custom.css"), os.path.join(book_dir, "_static", "custom.css"))
    for name in ("pml_logo.jpg", "oceanval_wordmark.svg"):
        shutil.copyfile(_data(name), os.path.join(book_dir, name))

    pattern = f"{glob.escape(notebooks_dir)}/*.ipynb"
    oceanval._run_jupytext(["--set-formats", "ipynb,py:percent"], pattern)
    scripts = glob.glob(f"{glob.escape(notebooks_dir)}/*.py")
    add_chunks(paths=scripts)
    for script in scripts:
        with open(script) as file:
            text = file.read()
        # chunk_start's placeholders, as compare() fills them
        for placeholder, value in (
            ("the_test_status", "False"),
            ("the_lon_lim", "None"),
            ("the_lat_lim", "None"),
            ("concise_value", "False"),
            ("fast_plot_value", "False"),
        ):
            text = text.replace(placeholder, value)
        with open(script, "w") as file:
            file.write(text)
    oceanval._run_jupytext(["--sync"], pattern)
    for script in scripts:
        os.remove(script)

    oceanval._build_book(book_dir)
    landing = os.path.join(book_dir, "_build", "html", "notebooks", f"{LANDING}.html")
    link = os.path.join(folder, REPORT_LINK)
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(os.path.relpath(landing, folder), link)
    return landing


# ---- analyze() ----


def _remove(folder):
    if os.path.lexists(folder):
        gc.collect()
        if os.path.isdir(folder) and not os.path.islink(folder):
            shutil.rmtree(folder)
        else:
            os.remove(folder)


def _dates(times, start, end, time_res):
    """What is missing from times in start-end (see model_check.date_report),
    as the report says it."""
    from oceanval.model_check import date_report

    report = date_report(times, start, end, time_res or time_resolution.MONTHLY)
    return {
        "summary": report["summary"],
        # date_report speaks of matchups
        "issues": [issue.replace("the years matched up", "the years analysed") for issue in report["issues"]],
        "gaps": report["gaps"],
        "first": report["first"],
        "last": report["last"],
    }


def _say(text):
    print(f"OceanVal: {text}", flush=True)


def _work_out(row, results_dir, thickness, thickness_for, time_shift, area=None, masks_for=None):
    """Work out what row asks for (see check_arguments, with its files and
    times added by analyze), saving it in results_dir, and return what
    INFO_FILE says of it. thickness_for keeps the cell thickness of each
    file pattern, once it is read."""
    entry = {
        "name": row["name"],
        "key": row["key"],
        "model_variable": row["model_variable"],
        "long_name": row["long_name"],
        "units": row["units"],
        "levels": row["levels"],
        "pattern": row["pattern"],
        "files": len(row["files"]),
        "time_res": row["time_res"],
        "time_res_text": _resolution_text(row["time_res"]),
        "climatology": None,
        "phenology": None,
        "long_term": None,
        "seasonal": None,
        "transects": None,
        "map_changes": None,
    }
    own = row["times"]
    dates = sorted(
        (int(y), int(m), int(d)) for df in own.values() for y, m, d in zip(df.year, df.month, df.day)
    )
    entry["first"] = "%04d-%02d-%02d" % dates[0] if dates else None
    entry["last"] = "%04d-%02d-%02d" % dates[-1] if dates else None

    if row["climatology"]:
        years = (row["climatology"]["start"], row["climatology"]["end"])
        layers = {}
        for layer in row["climatology"]["layers"]:
            _say(f"Working out the climatology of {row['name']}: {LAYER_TITLES[layer].lower()}")
            ds_thickness = None
            if layer in WATER_COLUMN:
                if row["pattern"] not in thickness_for:
                    thickness_for[row["pattern"]] = thickness_data(thickness, row["files"], row.get("as_missing"))
                ds_thickness = thickness_for[row["pattern"]]
            ds = climatology_of(
                row["climatology"]["files"], row, layer, years, time_shift, row["levels"], ds_thickness
            )
            units = integral_units(row["units"]) if layer == "integral" else row["units"]
            _labelled(ds, row, units, LAYER_TITLES[layer].lower())
            monthly = os.path.join(results_dir, f"{row['key']}_{layer}_monthly.nc")
            _save(ds, monthly)
            ds_annual = nc.open_data(monthly, checks=False)
            ds_annual.tmean()
            annual = os.path.join(results_dir, f"{row['key']}_{layer}_annual.nc")
            _save(ds_annual, annual)
            layers[layer] = {
                "title": LAYER_TITLES[layer],
                "units": units,
                "monthly": os.path.basename(monthly),
                "annual": os.path.basename(annual),
                "months": len(nc.open_data(monthly, checks=False).times),
            }
        entry["climatology"] = {
            "start": years[0],
            "end": years[1],
            "files": len(row["climatology"]["files"]),
            "dates": row["climatology"]["dates"],
            "layers": layers,
        }

    if row["phenology"]:
        years = (row["phenology"]["start"], row["phenology"]["end"])
        _say(f"Working out the phenology of {row['name']}")
        ds = daily_climatology_of(row["phenology"]["files"], row, years, time_shift, row["levels"])
        _labelled(ds, row, row["units"], "daily climatology at the surface")
        daily = os.path.join(results_dir, f"{row['key']}_surface_daily.nc")
        _save(ds, daily)
        phenology_file = os.path.join(results_dir, f"{row['key']}_phenology.nc")
        peak(daily, row["key"], phenology_file)
        with xr.open_dataset(phenology_file) as ds_peak:
            days = int(ds_peak.attrs["days"])
        entry["phenology"] = {
            "start": years[0],
            "end": years[1],
            "files": len(row["phenology"]["files"]),
            "dates": row["phenology"]["dates"],
            "daily": os.path.basename(daily),
            "file": os.path.basename(phenology_file),
            "days": days,
        }

    if row["long_term"] or row["seasonal"]:
        from oceanval import analysis_series

        entry.update(
            analysis_series.work_out(
                row, results_dir, thickness, thickness_for, time_shift, area, {} if masks_for is None else masks_for
            )
        )
    if row["map_changes"]:
        from oceanval import analysis_changes

        entry["map_changes"] = analysis_changes.work_out(row, results_dir, time_shift, thickness, thickness_for)
    if row["transects"]:
        from oceanval import analysis_transects

        entry["transects"] = analysis_transects.work_out(row, results_dir, time_shift, thickness, thickness_for)
    return entry


@_restores_nctoolkit_options
def analyze(
    sim_dir=None,
    variables=None,
    climatology=None,
    phenology=None,
    n_dirs_down=2,
    exclude=None,
    require=None,
    thickness=None,
    time_shift=None,
    out_dir=".",
    cores=None,
    view=True,
    long_term=None,
    seasonal=None,
    stations=None,
    subregions=None,
    lon_lim=None,
    lat_lim=None,
    as_missing=None,
    figure_code=False,
    transects=None,
    map_changes=None,
):
    """
    Analyze a simulation on its own, with no observations, and build an HTML
    report of what is found.

    Parameters
    -------------
    sim_dir : str
        The directory the simulation's output is in.
    variables : dict
        The variables to analyze: the name to give each, and the model
        variable holding it, several summed as "a+b", e.g.
        {"Chlorophyll": "P1_Chl+P2_Chl+P3_Chl+P4_Chl", "Temperature": "votemper"}.
    climatology : dict
        The variables to work out climatologies of: for each, by its name in
        variables, a dict of "start" and "end" (the years to average over,
        which must be years the output has: all the years given to analyze()
        are checked against the times of the files),
        and "surface" (True for the surface), "bottom" (True for the deepest
        value that is not missing, found with nctoolkit's bottom method, with
        choice="value") and/or "water_column" ("mean" for the vertical mean,
        "integral" for the depth integral, or None), e.g.
        {"Chlorophyll": {"start": 2000, "end": 2009, "surface": True,
        "bottom": True, "water_column": "integral"}}. The mean of each month
        of the year is worked out, and the annual mean, which is mapped.
    phenology : dict
        The variables to work out the phenology of: for each, by its name in
        variables, a dict of "start" and "end" years, e.g. {"Chlorophyll":
        {"start": 2000, "end": 2009}}. The day of the year of the seasonal
        peak at the surface is found from the variable's daily climatology,
        so daily output is needed.
    n_dirs_down : int
        How many directories below sim_dir the output files are.
    exclude : list
        Leave out the files whose names contain any of these words.
    require : list
        Only use the files whose names contain all of these words.
    thickness : str
        The cell thickness, needed for the water column and for vertical
        transects (to place each cell at its depth): the name of the
        thickness variable in the output (e.g. "e3t"), a file with the
        thickness in it, or "z_level" for output on fixed depth levels.
    time_shift : int
        Days to move the model's times by before they are used, e.g. -1 for
        time means dated at the end of the period they cover (see matchup).
    out_dir : str
        The directory to save the analysis in: everything goes in a folder
        called oceanval_analysis in it, which replaces any already there.
    cores : int
        The number of cores nctoolkit may use. There is no default: you must
        say how many.
    view : bool
        Open the report in a web browser once it is built.
    long_term : dict
        The variables to work out long-term time series of, in the same form
        as climatology: for each, by its name in variables, "start" and "end"
        years, and "surface", "bottom" and/or "water_column". Each year's mean is the
        mean of its months' means, and is worked out for the model's domain
        as a whole, for each region of subregions, and at each of stations.
        Two more keys are for long_term only: "window", the number of years of a
        rolling mean of the annual means (each the mean of that year and the
        ones before it; 1, the default, is none), and "show", "absolute" (the
        default), "absolute_change" (each year minus the first year with a
        value) or "percentage_change" (that, in per cent of the first).
    seasonal : dict
        The variables to work out seasonal time series of, in the same form
        as long_term: the mean of every month from start to end, for the
        model's domain, each region and each station, and so the mean seasonal
        cycle of each. Needs monthly output, or output more often than that.
    stations : dict
        Places to work out the long_term and seasonal series of, as the name
        of each and its [lon, lat], e.g. {"L4": [-4.217, 50.25]}. The nearest
        cell of the model's grid with values is used, which must be within two
        grid cells.
    subregions : str or None
        The regions to work out the long_term and seasonal series of, as the
        subregions of validate: "nwes" (northwest European Shelf), "global", or
        the path of a regions netCDF (.nc) file, with a 2D variable for each
        region, 1 in it and missing elsewhere. The model's domain as a whole
        is always done. Default is None.
    lon_lim, lat_lim : list or None
        Cut a box out of the model's domain, [minimum, maximum] in each, for
        the means of the long_term and seasonal series over its domain and
        regions. Stations are not cut by it. Default is None, for all of it.
    as_missing : float or list
        Value(s) to treat as missing in the model data, as in matchup: a
        value, or [minimum, maximum] for everything in a range, e.g. 0 for
        the zeros ERSEM output has on land. The cell thickness is treated the
        same. Default is None.
    transects : dict
        The variables to work out transects of: the model's values along
        straight lines that run north-south (the same longitude at both ends) or
        east-west (the same latitude at both ends), as many as you like, for each
        variable on its own. For each variable, by its name in variables, a list
        of transects, each a dict of "start" and "end" ([lon, lat] of each end)
        and at least one of "climatology" (the monthly climatology at the
        surface), "trends" (at the surface: how it changed over the years),
        "vertical" (the annual mean through the water column, in a section) and
        "vertical_trends" (how that section changed over the years), each a
        dict of "start" and "end" years, and, for the two trends, "how":
        "absolute" (the linear trend of the annual means, per year),
        "absolute_change" (the annual mean of the last year minus that of the
        first) or "percentage_change" (that as a percentage of the first year's
        mean), e.g. {"Temperature": [{"start": [-30, 0], "end": [-30, 65],
        "climatology": {"start": 2000, "end": 2009}, "trends": {"start": 2000,
        "end": 2009, "how": "absolute"}}]}. Requires nctoolkit 1.3.6 or later.
        Each month of each year is averaged first, a year's mean is the mean of
        its months' means (worked out with nctoolkit), and years without all
        their months are left out of the trends. "trends" and "vertical_trends" can also have a
        "window", the number of years of a rolling mean of the annual means,
        worked out with nctoolkit's rolling_mean before the trend is: the
        default, 1, is no rolling mean, and with a window of N a change is
        the mean of the last N years minus the mean of the first N.
    map_changes : dict
        The variables to map the changes of, between a historical period and
        one or two future periods. For each variable, by its name in variables,
        a dict of "historical" (a dict of "start" and "end" years), "future" (a
        list of one or two such dicts), "plot" ("absolute" for the historical
        and future maps, "absolute_changes" for those and the maps of the
        changes, or "changes" for the historical map and the changes), "change"
        ("absolute" for future minus historical, or "percentage" for that as a
        percentage of the historical value) and "surface", "bottom" and/or
        "water_column", as in climatology, e.g. {"Temperature": {"historical":
        {"start": 1990, "end": 1999}, "future": [{"start": 2040, "end": 2049},
        {"start": 2090, "end": 2099}], "plot": "absolute_changes", "change":
        "absolute", "surface": True}}. Each period is mapped as the mean of its
        years' annual means, and all its years must be years the output has.
    figure_code : bool
        Give each figure in the report a "Copy code to reproduce figure"
        button, which copies the Python code that draws it from the analysis's
        saved results, to run (and change) in the conda environment OceanVal
        ran in. Default is False.

    Returns
    -------------
    str
        The path of the report's first page.
    """
    from oceanval import time_stamps, user_cache
    from oceanval.matchall import index_times
    from oceanval.model_check import files_in_years

    if isinstance(cores, bool) or not isinstance(cores, (int, np.integer)) or cores < 1:
        raise ValueError("Give cores, the number of cores OceanVal may use, a whole number of 1 or more.")
    if sim_dir is None:
        raise ValueError("Give sim_dir, the directory the simulation's output is in.")
    if not os.path.isdir(sim_dir):
        raise ValueError(f"There is no directory {sim_dir}.")
    try:
        n_dirs_down = int(n_dirs_down)
    except (TypeError, ValueError):
        raise ValueError("n_dirs_down must be a whole number, 0 or more.")
    if isinstance(exclude, str):
        exclude = [exclude]
    if isinstance(require, str):
        require = [require]
    if not isinstance(figure_code, (bool, np.bool_)):
        raise ValueError("figure_code must be True or False.")
    as_missing = check_as_missing(as_missing)
    rows = check_arguments(
        variables, climatology, phenology, thickness, time_shift, long_term, seasonal, transects, map_changes
    )
    for row in rows:
        row["as_missing"] = as_missing
    area = check_area(rows, subregions, lon_lim, lat_lim, stations)
    sim_dir = os.path.abspath(sim_dir)
    out_dir = os.path.abspath(out_dir or ".")
    if os.path.isfile(out_dir):
        raise ValueError(f"out_dir, {out_dir}, is a file, not a directory.")

    _say(f"Finding the files of the simulation in {sim_dir}")
    found = streams(sim_dir, n_dirs_down, exclude, require)
    if not found:
        filtered = " that pass the exclude and require filters" if exclude or require else ""
        raise ValueError(
            f"No netCDF files were found {n_dirs_down} directories below {sim_dir}{filtered}."
        )
    if _looks_like_fvcom(found[0]["files"][0]):
        raise ValueError("analyze does not work with FVCOM output yet.")

    problems = []
    for row in rows:
        try:
            stream, res = choose_stream(place(row["model_variable"], found))
        except ValueError as error:
            problems.append(f"{row['name']} ({row['model_variable']}): {error}")
            continue
        row.update(describe_variable(row["model_variable"], stream))
        row["pattern"] = stream["pattern"]
        row["files"] = stream["files"]
        row["time_res"] = res
        if row["levels"] < 2:
            layered = LAYERED + ("map_changes",)
            column = [
                layer for kind in layered if row[kind] for layer in row[kind]["layers"] if layer in WATER_COLUMN
            ]
            has_bottom = any(BOTTOM in row[kind]["layers"] for kind in layered if row[kind])
            if has_bottom:
                problems.append(
                    f"{row['name']} ({row['model_variable']}) has only one vertical level, so "
                    "it has no bottom to take: leave out its bottom."
                )
            if column:
                problems.append(
                    f"{row['name']} ({row['model_variable']}) has only one vertical level, so "
                    "it has no water column to take: leave out its water_column."
                )
            if row["transects"] and any(
                line["vertical"] or line["vertical_trends"] for line in row["transects"]["lines"]
            ):
                problems.append(
                    f"{row['name']} ({row['model_variable']}) has only one vertical level, so "
                    "it has no section through the water column to take along a transect: leave out "
                    "the vertical climatology and vertical changes of its transects."
                )
    if problems:
        raise ValueError("\n".join(problems))

    paths = sorted({path for row in rows for path in row["files"]})
    _say(f"Reading the times of {len(paths)} file{'s' if len(paths) != 1 else ''}")
    times = index_times(paths)
    if time_shift:
        calendars = {}
        for row in rows:
            calendar_of = time_stamps.calendar(row["files"][0])
            calendars.update({path: calendar_of for path in row["files"]})
        times = time_stamps.shift_index(dict(times), time_shift, lambda path: calendars.get(path, "standard"))

    for row in rows:
        own = {path: times[path] for path in row["files"] if path in times}
        if own:
            row["time_res"] = time_resolution.label(pd.concat(own.values(), ignore_index=True))
        row["times"] = own
        for kind in KINDS:
            entry = row[kind]
            if entry is None or kind == "map_changes":
                continue
            entry["files"] = files_in_years(own, entry["start"], entry["end"])
            if not entry["files"]:
                problems.append(
                    f"{row['name']} has no output in {entry['start']}-{entry['end']}, the years "
                    f"of its {kind}."
                )
            else:
                output = [int(year) for df in own.values() for year in df.year]
                if output and (entry["start"] < min(output) or entry["end"] > max(output)):
                    problems.append(
                        f"{row['name']}'s output is from {min(output)} to {max(output)}, so the years "
                        f"of its {kind}, {entry['start']}-{entry['end']}, are not all in it."
                    )
            entry["dates"] = _dates(own, entry["start"], entry["end"], row["time_res"])
        for period in row["map_changes"]["periods"] if row["map_changes"] else []:
            # each period has its own files and dates, not those of the years between them
            period["files"] = files_in_years(own, period["start"], period["end"])
            label = f"the {period['title'].lower()} period of its map_changes"
            if not period["files"]:
                problems.append(f"{row['name']} has no output in {period['start']}-{period['end']}, the years of {label}.")
            else:
                output = [int(year) for df in own.values() for year in df.year]
                if output and (period["start"] < min(output) or period["end"] > max(output)):
                    problems.append(
                        f"{row['name']}'s output is from {min(output)} to {max(output)}, so the years "
                        f"of {label}, {period['start']}-{period['end']}, are not all in it."
                    )
            period["dates"] = _dates(own, period["start"], period["end"], row["time_res"])
        if row["long_term"] or row["seasonal"]:
            first = min(row[kind]["start"] for kind in ("long_term", "seasonal") if row[kind])
            last = max(row[kind]["end"] for kind in ("long_term", "seasonal") if row[kind])
            row["series_files"] = files_in_years(own, first, last)
        if row["seasonal"]:
            from oceanval.analysis_series import months_per_year

            months = {(int(y), int(m)) for df in own.values() for y, m in zip(df.year, df.month)
                      if row["seasonal"]["start"] <= int(y) <= row["seasonal"]["end"]}
            if months and months_per_year(months) == 1:
                problems.append(
                    f"{row['name']}'s seasonal series needs monthly output, or more often, and its output "
                    f"({row['pattern']}) is {_resolution_text(row['time_res'])}."
                )
        for number, line in enumerate(row["transects"]["lines"] if row["transects"] else [], start=1):
            from oceanval import analysis_transects

            for option in analysis_transects.TREND_OPTIONS:
                trends = line[option]
                if not trends:
                    continue
                output = {
                    int(y) for df in row["times"].values() for y in df.year if trends["start"] <= int(y) <= trends["end"]
                }
                needed = (
                    analysis_transects.MIN_TREND_YEARS
                    if trends["how"] == analysis_transects.ABSOLUTE
                    else analysis_transects.MIN_CHANGE_YEARS
                ) + trends.get("window", 1) - 1
                if len(output) < needed:
                    problems.append(
                        f"The {analysis_transects.HOWS[trends['how']].lower()} {option} of transect {number} of "
                        f"{row['name']} need output in at least {needed} years of {trends['start']}-{trends['end']}, "
                        f"and it has {len(output)}."
                    )
        if row["phenology"] and row["time_res"] != time_resolution.DAILY:
            problems.append(
                f"{row['name']}'s phenology needs daily output, and its output "
                f"({row['pattern']}) is {_resolution_text(row['time_res'])}."
            )
    if problems:
        raise ValueError("\n".join(problems))

    # any analysis there before is replaced, as validate() replaces its report
    folder = os.path.join(out_dir, FOLDER)
    _remove(folder)
    results_dir = os.path.join(folder, RESULTS)
    os.makedirs(results_dir)
    nc.options(cores=int(cores))

    thickness_for = {}
    masks_for = {}
    info_rows = []
    with warnings.catch_warnings():
        # each file is said to be missing the years the others have, which
        # says nothing about the output as a whole
        warnings.filterwarnings("ignore", message=r".* missing in the dataset")
        for row in rows:
            info_rows.append(_work_out(row, results_dir, thickness, thickness_for, time_shift, area, masks_for))

    from oceanval import __version__

    info = {
        "version": 1,
        "oceanval": __version__,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "sim_dir": sim_dir,
        "n_dirs_down": n_dirs_down,
        "exclude": list(exclude or []),
        "require": list(require or []),
        "thickness": thickness,
        "time_shift": time_shift or 0,
        "as_missing": as_missing,
        "subregions": area["subregions"],
        "lon_lim": area["lon_lim"],
        "lat_lim": area["lat_lim"],
        "stations": {name: {"lon": lon, "lat": lat} for name, (lon, lat) in area["stations"].items()},
        "figure_code": bool(figure_code),
        "variables": info_rows,
    }
    with open(os.path.join(results_dir, INFO_FILE), "w") as file:
        json.dump(info, file, indent=2)

    _say("Building the report")
    landing = build_report(folder, info)
    user_cache.record_sim_dir(sim_dir)
    _say(f"The analysis is in {folder}: open {os.path.join(folder, REPORT_LINK)} to see the report")
    if view:
        webbrowser.open("file://" + os.path.abspath(landing))
    return landing


def _resolution_text(res):
    """A time resolution (see oceanval.time_res.label) in words."""
    if res is None:
        return "of unknown resolution"
    if res == time_resolution.MONTHLY:
        return "monthly"
    if res == time_resolution.DAILY:
        return "daily"
    return f"every {res[:-1]} days"


def load(results_dir):
    """What analyze() saved in results_dir (see INFO_FILE)."""
    with open(os.path.join(results_dir, INFO_FILE)) as file:
        return json.load(file)
