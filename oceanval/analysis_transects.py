"""Transects in analyze(): the model's output along straight north-south or
east-west lines, which a user can add as many of as they like, for each
variable on its own.

Each transect can have any of three things worked out along it, each over
years of its own:

- "climatology": the monthly climatology at the surface, by month and
  position (a line if the output is annual), as validate shows the surface.
- "vertical": the annual mean through the water column, by depth and
  position, as validate shows its section.
- "trends": at the surface, along the line, how it changed over the years:
  "absolute" (the linear trend of the annual means, per year),
  "absolute_change" (the annual mean of the last year minus that of the first)
  or "percentage_change" (that as a percentage of the first year's mean, of its
  absolute value, so that an increase is positive).

The monthly means are taken first, as for the climatology and the time series,
then extracted along the line with nctoolkit's to_transect, and what is worked
out from them is saved as a small CSV for the report's figures to draw (see
oceanval.analysis_plots). The lines are checked as validate's transect is (see
oceanval.transects).
"""

import os
import warnings

import numpy as np
import pandas as pd
import xarray as xr

from oceanval import transects
from oceanval.analysis import SURFACE, Z_LEVEL, _say, _time_dim, _year, monthly_means_of, thickness_data
from oceanval.analysis_series import EARTH_KM, _grid, _spacing, months_per_year

CLIMATOLOGY = "climatology"
VERTICAL = "vertical"
TRENDS = "trends"
# what can be worked out along a transect, and what each is called in the report
OPTIONS = {
    CLIMATOLOGY: "Surface climatology",
    VERTICAL: "Vertical climatology",
    TRENDS: "Trends",
}

ABSOLUTE = "absolute"
ABSOLUTE_CHANGE = "absolute_change"
PERCENTAGE_CHANGE = "percentage_change"
# the ways of showing a trend, as the window's drop-down has them
HOWS = {
    ABSOLUTE: "Absolute",
    ABSOLUTE_CHANGE: "Absolute change",
    PERCENTAGE_CHANGE: "Percentage change",
}
# the ones that compare the last year with the first
CHANGES = (ABSOLUTE_CHANGE, PERCENTAGE_CHANGE)
# the years an absolute trend needs, and a change
MIN_TREND_YEARS = 3
MIN_CHANGE_YEARS = 2

# the layer of monthly_means_of that keeps every level, for a vertical transect
LEVELS = "levels"

# the keys of each transect, as analyze() takes them
LINE_KEYS = ("start", "end") + tuple(OPTIONS)

# a vertical section of a model whose cells are not at the same depths everywhere
# is put on this many evenly spaced depths, from the shallowest to the deepest
N_DEPTHS = 50

# the most points a transect is sampled at, and how many it has if the model's
# grid cannot be read to say
MOST_STEPS = 1000
STEPS_UNKNOWN = 100


# ---- the arguments ----


def _option_years(entry, which):
    """The start and end years of one option of a transect (which says
    where, for an error): (start, end)."""
    if not isinstance(entry, dict):
        raise ValueError(f"{which} must be a dict of start and end years.")
    for part in ("start", "end"):
        if part not in entry:
            raise ValueError(f"{which} has no {part} year.")
    start, end = _year(entry["start"], f"The start of {which}"), _year(entry["end"], f"The end of {which}")
    if end < start:
        raise ValueError(f"The end of {which}, {end}, is before its start, {start}.")
    return start, end


def check_lines(lines, name):
    """The transects of one variable in analyze()'s transects, checked (name
    is the variable): a list of {"start": [lon, lat], "end": [lon, lat],
    "climatology": None or {"start", "end"}, "vertical": None or {"start",
    "end"}, "trends": None or {"start", "end", "how"}}. Raises ValueError saying
    what is wrong."""
    where = f"transects[{name!r}]"
    if not isinstance(lines, (list, tuple)) or not lines:
        raise ValueError(
            f"{where} must be a list of transects, each a dict of \"start\" and \"end\" ([lon, lat]) "
            'and at least one of "climatology", "vertical" and "trends", each a dict of "start" and "end" years '
            '(and, for "trends", "how": ' + ", ".join(f'"{key}"' for key in HOWS) + ")."
        )
    checked = []
    for number, line in enumerate(lines, start=1):
        which = f"Transect {number} of {where}"
        if not isinstance(line, dict):
            raise ValueError(f"{which} must be a dict.")
        unknown = sorted(set(line) - set(LINE_KEYS))
        if unknown:
            raise ValueError(f"{which} has {', '.join(repr(key) for key in unknown)}, which it cannot have.")
        for key in ("start", "end"):
            if key not in line:
                raise ValueError(f"{which} has no {key!r}.")
        try:
            ends = transects.check_transect({"start": line["start"], "end": line["end"]})
        except (TypeError, ValueError) as error:
            raise ValueError(f"{which}: {error}")
        out = {"start": ends["start"], "end": ends["end"], CLIMATOLOGY: None, VERTICAL: None, TRENDS: None}
        for option in OPTIONS:
            if line.get(option) is None:
                continue
            entry = line[option]
            start, end = _option_years(entry, f"the {option} of {which}")
            out[option] = {"start": start, "end": end}
            if option != TRENDS:
                if set(entry) - {"start", "end"}:
                    raise ValueError(f"The {option} of {which} has only start and end years.")
                continue
            how = entry.get("how")
            if how not in HOWS:
                raise ValueError(
                    f"The trends of {which} need \"how\", one of {', '.join(repr(key) for key in HOWS)}, not {how!r}."
                )
            if set(entry) - {"start", "end", "how"}:
                raise ValueError(f"The trends of {which} have only start and end years, and how.")
            span = end - start + 1
            needed = MIN_TREND_YEARS if how == ABSOLUTE else MIN_CHANGE_YEARS
            if span < needed:
                raise ValueError(
                    f"The {HOWS[how].lower()} trends of {which} need at least {needed} years: "
                    f"{start}-{end} is {span}."
                )
            out[TRENDS]["how"] = how
        if not any(out[option] for option in OPTIONS):
            raise ValueError(f'{which} needs at least one of "climatology", "vertical" and "trends".')
        checked.append(out)
    return checked


# ---- the line ----


def steps_for(path, key, line):
    """How many points to sample a transect at: about one for each grid cell
    of the model it crosses, from the grid of the variable key in the file
    path, as finer steps add nothing the grid does not have."""
    start, end = transects.line_ends(line)
    if transects.direction(line) == "north-south":
        length = abs(end[1] - start[1]) * np.pi / 180 * EARTH_KM
    else:
        length = abs(end[0] - start[0]) * np.pi / 180 * EARTH_KM * np.cos(np.radians(start[1]))
    try:
        with xr.open_dataset(path, decode_times=False) as data:
            lon, lat = _grid(data, data[key])
        spacing = _spacing(lon, lat)
    except (KeyError, StopIteration, ValueError):
        return STEPS_UNKNOWN
    if not np.isfinite(spacing) or spacing <= 0:
        return STEPS_UNKNOWN
    return int(min(max(round(length / spacing) + 1, 2), MOST_STEPS))


def _along(path, key, line, vertical):
    """The values along a transect, in the file path that to_transect wrote,
    as (positions, depths, values): the latitudes of a north-south line or the
    longitudes of an east-west one, in order, the depths (None unless
    vertical), and the values by time, then depth if vertical, then position.

    The points of a line are a lon-lat grid one cell across if they happen
    to fall on one, and a list of cells (a dimension of their own, with the
    longitudes and latitudes as variables) if they do not."""
    with xr.open_dataset(path, decode_times=False) as data:
        variable = data[key]
        try:
            time_dim = _time_dim(variable, data)
        except ValueError:
            # a thickness that does not change has no time
            time_dim = None
        axis = "lat" if transects.direction(line) == "north-south" else "lon"
        name = transects._coordinate(list(data.coords) + list(data.variables), axis)
        coordinate = data[name]
        if name in variable.dims:
            position_dim = name
        elif coordinate.ndim == 1 and coordinate.dims[0] in variable.dims:
            position_dim = coordinate.dims[0]
        else:
            raise ValueError(f"The transect's {'latitudes' if axis == 'lat' else 'longitudes'} could not be found in the values extracted.")
        # a line is one cell across, and a level dimension has one level at the surface
        single = [dim for dim in variable.dims if dim not in (time_dim, position_dim) and variable.sizes[dim] == 1]
        variable = variable.isel({dim: 0 for dim in single})
        levels = [dim for dim in variable.dims if dim not in (time_dim, position_dim)]
        if vertical:
            if len(levels) != 1:
                raise ValueError("The depths of the water column could not be found in the values extracted.")
            variable = variable.transpose(*([time_dim] if time_dim else []), levels[0], position_dim)
            depths = np.abs(np.asarray(data[levels[0]].values, dtype="float64"))
        else:
            if levels:
                raise ValueError("The values extracted have depths: the surface was expected.")
            variable = variable.transpose(*([time_dim] if time_dim else []), position_dim)
            depths = None
        positions = np.asarray(coordinate.values, dtype="float64")
        values = np.asarray(variable.values, dtype="float64")
    if time_dim is None:
        values = values[None]
    order = np.argsort(positions, kind="stable")
    return positions[order], depths, values[..., order]


# ---- what is worked out ----


def _annual(values, times, start, end, per_year):
    """The mean of each year's monthly means, as an array by year (from start
    to end, then as values): missing for a year with fewer than per_year months."""
    years = list(range(start, end + 1))
    out = np.full((len(years),) + values.shape[1:], np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for number, year in enumerate(years):
            which = [index for index, (y, _) in enumerate(times) if y == year]
            if len(which) >= per_year:
                out[number] = np.nanmean(values[which], axis=0)
    return years, out


def slope(annual, years):
    """The least-squares trend per year of annual (by year, then at each
    point), and the number of years it is from. Missing where fewer than
    MIN_TREND_YEARS years have values."""
    x = np.asarray(years, dtype="float64").reshape((-1,) + (1,) * (annual.ndim - 1))
    valid = np.isfinite(annual)
    count = valid.sum(axis=0)
    x = np.where(valid, x, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        x_mean = np.nanmean(x, axis=0)
        y_mean = np.nanmean(annual, axis=0)
        sxx = np.nansum((x - x_mean) ** 2, axis=0)
        sxy = np.nansum((x - x_mean) * (annual - y_mean), axis=0)
        trend = np.where((count >= MIN_TREND_YEARS) & (sxx > 0), sxy / np.where(sxx > 0, sxx, 1), np.nan)
    return trend, count


def monthly_climatology(values, times):
    """The mean of each month of the year, over the years, by month (the
    months there are, in order) then as values: (months, climatology)."""
    months = sorted({month for _, month in times})
    out = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for month in months:
            which = [index for index, (_, m) in enumerate(times) if m == month]
            out.append(np.nanmean(values[which], axis=0))
    return months, np.array(out)


def _table(positions, depths, columns):
    """A data frame of position (and depth, if depths), then the columns,
    each given as an array by depth, then position (or just position)."""
    if depths is None:
        data = {"position": positions}
    else:
        depth_grid, position_grid = np.meshgrid(depths, positions, indexing="ij")
        data = {"position": position_grid.ravel(), "depth": depth_grid.ravel()}
        columns = {name: values.ravel() for name, values in columns.items()}
    data.update(columns)
    return pd.DataFrame(data)


def cell_depths(thickness):
    """The depth of the middle of each cell, from the cell thickness by depth
    then position: its sum from the top, less half of its own. Missing where
    the thickness is."""
    thick = np.where(np.isfinite(thickness) & (thickness > 0), thickness, 0.0)
    depths = np.cumsum(thick, axis=0) - thick / 2
    return np.where(thick > 0, depths, np.nan)


def on_common_depths(depths, values):
    """values by depth then position, where each position has depths of its
    own (depths, the same shape), on N_DEPTHS evenly spaced depths that all
    positions share, from the shallowest to the deepest: (the depths, the
    values). Each position is linearly interpolated between its cells, and
    is missing outside them, where it is the seabed or the surface."""
    valid = np.isfinite(depths) & np.isfinite(values)
    if not valid.any():
        return np.linspace(0.0, 1.0, N_DEPTHS), np.full((N_DEPTHS, values.shape[1]), np.nan)
    common = np.linspace(depths[valid].min(), depths[valid].max(), N_DEPTHS)
    out = np.full((N_DEPTHS, values.shape[1]), np.nan)
    for position in range(values.shape[1]):
        keep = valid[:, position]
        if keep.sum() == 0:
            continue
        x, y = depths[keep, position], values[keep, position]
        order = np.argsort(x)
        out[:, position] = np.interp(common, x[order], y[order], left=np.nan, right=np.nan)
    return common, out


def _depths_along(row, line, steps, ends, thickness, thickness_for):
    """The depth of each cell along a transect, by depth then position, from
    the cell thickness (see work_out)."""
    if row["pattern"] not in thickness_for:
        thickness_for[row["pattern"]] = thickness_data(thickness, row["files"], row.get("as_missing"))
    ds = thickness_for[row["pattern"]].copy()
    name = ds.variables[0]
    ds.to_transect(start=ends[0], end=ends[1], nsteps=steps)
    ds.run()
    _, _, values = _along(ds.current[0], name, line, True)
    return cell_depths(values[0])


def _in_years(values, times, start, end):
    """values and times, those in the years start to end."""
    which = [index for index, (year, _) in enumerate(times) if start <= year <= end]
    return values[which], [times[index] for index in which]


def derive(option, positions, depths, values, times, spec, per_year):
    """What one option of a transect works out from values (see _along, with
    their times, each (year, month)): (a data frame, notes). spec is the
    option's {"start", "end"} (and "how", for the trends). The frame has
    position, then depth for the vertical, then month for the surface
    climatology (unless the output is annual), then value, and, for an
    absolute trend, years, the number of years it is from. notes are what the
    report must say of years that were left out."""
    start, end = spec["start"], spec["end"]
    notes = []
    if option in (CLIMATOLOGY, VERTICAL):
        inside, inside_times = _in_years(values, times, start, end)
        if len(inside) == 0:
            shown = None
            if option == VERTICAL:
                shown = depths if depths.ndim == 1 else np.arange(depths.shape[0], dtype="float64")
            return _table(positions, shown, {"value": np.full(values.shape[1:], np.nan)}), notes
        months, climatology = monthly_climatology(inside, inside_times)
        if option == VERTICAL:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                mean = np.nanmean(climatology, axis=0)
            if depths.ndim == 2:
                # each position has its own depths
                depths, mean = on_common_depths(depths, mean)
            return _table(positions, depths, {"value": mean}), notes
        if len(months) > 1:
            grid_month, grid_position = np.meshgrid(months, positions, indexing="ij")
            frame = pd.DataFrame(
                {"position": grid_position.ravel(), "month": grid_month.ravel(), "value": climatology.ravel()}
            )
            return frame, notes
        return _table(positions, None, {"value": climatology[0]}), notes
    years, annual = _annual(values, times, start, end, per_year)
    left_out = [year for year, row in zip(years, annual) if not np.isfinite(row).any()]
    how = spec["how"]
    if how == ABSOLUTE:
        if left_out:
            notes.append(_left_out(left_out, per_year))
        trend, count = slope(annual, years)
        return _table(positions, None, {"value": trend, "years": count}), notes
    first, last = annual[0], annual[-1]
    for year in (start, end):
        if year in left_out:
            notes.append(f"{year} " + ("does not have all 12 months of output" if per_year > 1 else "has no output") + ", so there is no change.")
    change = last - first
    if how == PERCENTAGE_CHANGE:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            change = np.where(first != 0, 100 * change / np.abs(first), np.nan)
    return _table(positions, None, {"value": change}), notes


def _left_out(years, per_year):
    """What the report says of years that were left out of a trend."""
    many = len(years) != 1
    return (
        f"{', '.join(str(year) for year in years)} {'were' if many else 'was'} left out, as the output does not "
        + (f"have all 12 months in {'them' if many else 'it'}." if per_year > 1 else "have any.")
    )


def _units(units, option, how=None):
    """What the values of an option are in."""
    if option == TRENDS and how == PERCENTAGE_CHANGE:
        return "%"
    if option == TRENDS and how == ABSOLUTE:
        return f"{units} per year" if units else "per year"
    return units


def work_out(row, results_dir, time_shift, thickness=None, thickness_for=None):
    """The transects that row asks for (see check_arguments, with its files
    and times added by analyze), saved in results_dir. Returns what INFO_FILE
    says of them: {"start", "end", "files", "dates", "lines": [...]}, each
    transect with its position in lines (from 1), its ends, and "options":
    {option: its years, CSV and what the report says about it}. thickness is
    analyze()'s, which says whether the model is on fixed depth levels
    (Z_LEVEL), whose levels are the depths of the cells, or where to read the
    cell thickness from, which places each cell at its depth; thickness_for
    keeps what it was read as, for each file pattern."""
    asked = row["transects"]
    thickness_for = {} if thickness_for is None else thickness_for
    present = {
        (int(y), int(m))
        for df in row["times"].values()
        for y, m in zip(df.year, df.month)
        if asked["start"] <= int(y) <= asked["end"]
    }
    per_year = months_per_year(present)
    # the monthly means of each layer, once, over every year any transect asks of it
    monthly = {}
    for vertical in (False, True):
        used = [
            line[option]
            for line in asked["lines"]
            for option in ((VERTICAL,) if vertical else (CLIMATOLOGY, TRENDS))
            if line[option]
        ]
        if not used:
            continue
        _say(
            f"Working out the transects of {row['name']}: "
            + ("through the water column" if vertical else "at the surface")
        )
        years = (min(x["start"] for x in used), max(x["end"] for x in used))
        ds = monthly_means_of(asked["files"], row, LEVELS if vertical else SURFACE, years, time_shift, row["levels"])
        ds.run()
        monthly[vertical] = (ds, [(time.year, time.month) for time in ds.times])
    saved = []
    for number, line in enumerate(asked["lines"], start=1):
        steps = steps_for(row["files"][0], row["parts"][0], line)
        ends = transects.line_ends(line)
        along = {}
        for vertical, (ds, times) in monthly.items():
            if not any(line[option] for option in ((VERTICAL,) if vertical else (CLIMATOLOGY, TRENDS))):
                continue
            extracted = ds.copy()
            extracted.to_transect(start=ends[0], end=ends[1], nsteps=steps)
            extracted.run()
            positions, depths, values = _along(extracted.current[0], row["key"], line, vertical)
            if vertical and thickness != Z_LEVEL:
                depths = _depths_along(row, line, steps, ends, thickness, thickness_for)
            along[vertical] = (positions, depths, values, times)
        options = {}
        for option in OPTIONS:
            spec = line[option]
            if not spec:
                continue
            positions, depths, values, times = along[option == VERTICAL]
            frame, notes = derive(option, positions, depths, values, times, spec, per_year)
            csv = f"{row['key']}_transect_{number}_{option}.csv"
            frame.to_csv(os.path.join(results_dir, csv), index=False)
            how = spec.get("how")
            options[option] = {
                "title": OPTIONS[option] if option != TRENDS else f"{OPTIONS[option]}: {HOWS[how].lower()}",
                "start": spec["start"],
                "end": spec["end"],
                "how": how,
                "units": _units(row["units"], option, how),
                "depths": None if option != VERTICAL else ("levels" if thickness == Z_LEVEL else "thickness"),
                "csv": csv,
                "months": int(frame["month"].nunique()) if "month" in frame else 0,
                # no part of it has values: it is all on land, or outside the model
                "empty": bool(not np.isfinite(frame["value"].to_numpy(dtype="float64")).any()),
                "notes": notes,
            }
        saved.append(
            {
                "index": number,
                "start": line["start"],
                "end": line["end"],
                "direction": transects.direction(line),
                "description": transects.describe(line),
                "steps": steps,
                "empty": all(entry["empty"] for entry in options.values()),
                "options": options,
            }
        )
    return {
        "start": asked["start"],
        "end": asked["end"],
        "files": len(asked["files"]),
        "dates": asked["dates"],
        "lines": saved,
    }
