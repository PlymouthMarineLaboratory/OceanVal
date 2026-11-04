"""Transects in analyze(): the model's output along straight north-south or
east-west lines, which a user can add as many of as they like, for each
variable on its own.

Each transect can have any of four things worked out along it, each over
years of its own, two at the surface and two through the water column:

- "climatology": the monthly climatology at the surface, by month and
  position (a line if the output is annual), as validate shows the surface.
- "trends": at the surface, along the line, how it changed over the years, as a
  heat map of the position along the line by year, from the annual means (worked
  out with nctoolkit, and with a "window" of more than 1 year smoothed by
  nctoolkit's rolling_mean, each year then being the last of its window):
  "absolute" (the annual means), "absolute_change" (each minus the first of them,
  the first rolling mean if there is a window) or "percentage_change" (that as a
  percentage of the first's absolute value, so that an increase is positive).
- "vertical": the annual mean through the water column, by depth and
  position, as validate shows its section.
- "vertical_trends": the same changes as "trends", a heat map for each of a few depths
  of the section (the window's "Changes" of its Vertical part).

The monthly means are taken first, as for the climatology and the time series,
then extracted along the line with nctoolkit's to_transect, and what is worked
out from them is saved as a small CSV for the report's figures to draw (see
oceanval.analysis_plots). The lines are checked as validate's transect is (see
oceanval.transects). Raw FVCOM output, which CDO cannot read, has its monthly
means worked out on its mesh and put on a strip of a lon-lat grid along each
line, which to_transect extracts the line from (see oceanval.fvcom_transects).
"""

import os
import shutil
import tempfile
import warnings

import numpy as np
import pandas as pd
import xarray as xr

from oceanval import domain_mask, sim_unify, transects
from oceanval.analysis import SURFACE, Z_LEVEL, _say, _time_dim, _year, monthly_means_of, thickness_data
from oceanval.analysis_series import EARTH_KM, _grid, _spacing, months_per_year
from oceanval.quiet_nc import nc

CLIMATOLOGY = "climatology"
VERTICAL = "vertical"
TRENDS = "trends"
VERTICAL_TRENDS = "vertical_trends"
# what can be worked out along a transect, and what each is called in the report
OPTIONS = {
    CLIMATOLOGY: "Surface climatology",
    TRENDS: "Surface changes",
    VERTICAL: "Vertical climatology",
    VERTICAL_TRENDS: "Vertical changes",
}
# the options that are changes over the years, and those worked out at the surface and through the water column
TREND_OPTIONS = (TRENDS, VERTICAL_TRENDS)
SURFACE_OPTIONS = (CLIMATOLOGY, TRENDS)
VERTICAL_OPTIONS = (VERTICAL, VERTICAL_TRENDS)

ABSOLUTE = "absolute"
ABSOLUTE_CHANGE = "absolute_change"
PERCENTAGE_CHANGE = "percentage_change"
# the ways of showing a trend, as the window's drop-down has them
HOWS = {
    ABSOLUTE: "Absolute",
    ABSOLUTE_CHANGE: "Absolute change",
    PERCENTAGE_CHANGE: "Percentage change",
}
# the ones that compare each year with the first
CHANGES = (ABSOLUTE_CHANGE, PERCENTAGE_CHANGE)
# the annual means the changes need: the first, and one to compare with it
MIN_CHANGE_YEARS = 2

# the layer of monthly_means_of that keeps every level, for a vertical transect
LEVELS = "levels"

# the keys of each transect, as analyze() takes them
LINE_KEYS = ("start", "end") + tuple(OPTIONS)

# a vertical section of a model whose cells are not at the same depths everywhere
# is put on this many evenly spaced depths, from the shallowest to the deepest
N_DEPTHS = 50
# the depths the vertical changes are shown at, one heat map (position by year) for each
DEPTH_PANELS = 4

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
    "end"}, "trends": None or {"start", "end", "how"}, "vertical_trends": None or
    {"start", "end", "how"}}. Raises ValueError saying
    what is wrong."""
    where = f"transects[{name!r}]"
    if not isinstance(lines, (list, tuple)) or not lines:
        raise ValueError(
            f"{where} must be a list of transects, each a dict of \"start\" and \"end\" ([lon, lat]) "
            'and at least one of "climatology", "trends", "vertical" and "vertical_trends", each a dict of "start" and "end" years '
            '(and, for the trends, "how": ' + ", ".join(f'"{key}"' for key in HOWS) + ")."
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
        out = {"start": ends["start"], "end": ends["end"], **{option: None for option in OPTIONS}}
        for option in OPTIONS:
            if line.get(option) is None:
                continue
            entry = line[option]
            start, end = _option_years(entry, f"the {option} of {which}")
            out[option] = {"start": start, "end": end}
            if option not in TREND_OPTIONS:
                if set(entry) - {"start", "end"}:
                    raise ValueError(f"The {option} of {which} has only start and end years.")
                continue
            how = entry.get("how")
            if how not in HOWS:
                raise ValueError(
                    f"The {option} of {which} need \"how\", one of {', '.join(repr(key) for key in HOWS)}, not {how!r}."
                )
            if set(entry) - {"start", "end", "how", "window"}:
                raise ValueError(f"The {option} of {which} have only start and end years, how and window.")
            window = entry.get("window", 1)
            if isinstance(window, bool) or not isinstance(window, (int, np.integer)) or window < 1:
                raise ValueError(
                    f"The window of the {option} of {which} is the number of years of the rolling mean, "
                    f"a whole number of 1 or more (1 is no rolling mean), not {window!r}."
                )
            span = end - start + 1
            needed = MIN_CHANGE_YEARS
            if span - window + 1 < needed:
                raise ValueError(
                    f"The {HOWS[how].lower()} {option} of {which} need at least {needed} years"
                    + (f" after a rolling mean of {window}, so {needed + window - 1}" if window > 1 else "")
                    + f": {start}-{end} is {span}."
                )
            out[option]["how"] = how
            out[option]["window"] = int(window)
        if not any(out[option] for option in OPTIONS):
            raise ValueError(f'{which} needs at least one of "climatology", "trends", "vertical" and "vertical_trends".')
        checked.append(out)
    return checked


# ---- the line ----


def line_length(line):
    """The length of a transect in km."""
    start, end = transects.line_ends(line)
    if transects.direction(line) == "north-south":
        return abs(end[1] - start[1]) * np.pi / 180 * EARTH_KM
    return abs(end[0] - start[0]) * np.pi / 180 * EARTH_KM * np.cos(np.radians(start[1]))


def steps_of(length, spacing):
    """How many points to sample a transect length km long at, a grid's cells
    being spacing km apart, or STEPS_UNKNOWN if spacing says nothing."""
    if not np.isfinite(spacing) or spacing <= 0:
        return STEPS_UNKNOWN
    return int(min(max(round(length / spacing) + 1, 2), MOST_STEPS))


def steps_for(path, key, line):
    """How many points to sample a transect at: about one for each grid cell
    of the model it crosses, from the grid of the variable key in the file
    path, as finer steps add nothing the grid does not have."""
    length = line_length(line)
    try:
        with xr.open_dataset(path, decode_times=False) as data:
            lon, lat = _grid(data, data[key])
        spacing = _spacing(lon, lat)
    except (KeyError, StopIteration, ValueError):
        return STEPS_UNKNOWN
    return steps_of(length, spacing)


def unified_steps(row, line):
    """How many points to sample a transect at when the simulations of a comparison
    are on one grid (see oceanval.sim_unify), so that every simulation has the same
    points: about one for each cell of that grid the line crosses, or None if that
    cannot be told. With a domain mask (see oceanval.domain_mask) the grid is the mask's."""
    unify = row.get("unify")
    mask = row.get("domain_mask")
    if mask is not None:
        # every simulation ends on the mask's grid
        return steps_for(mask["path"], mask["variable"], line)
    start, end = transects.line_ends(line)
    north_south = transects.direction(line) == "north-south"
    if unify["method"] == "baseline":
        for layer in (SURFACE, "levels"):
            path = sim_unify.grid_path(unify["folder"], row["key"], layer)
            if os.path.exists(path):
                return steps_for(path, row["key"], line)
        return None
    if north_south:
        length = abs(end[1] - start[1]) * np.pi / 180 * EARTH_KM
        spacing = unify["res"][1] * np.pi / 180 * EARTH_KM
    else:
        factor = np.pi / 180 * EARTH_KM * np.cos(np.radians(start[1]))
        length, spacing = abs(end[0] - start[0]) * factor, unify["res"][0] * factor
    if not np.isfinite(spacing) or spacing <= 0:
        return None
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


def complete_years(times, start, end, per_year):
    """The years from start to end that have all their months (or the one
    value of annual output) among times, each (year, month)."""
    return [
        year for year in range(start, end + 1) if sum(1 for y, _ in times if y == year) >= per_year
    ]


def annual_means(extracted, key, line, times, spec, per_year, vertical=False):
    """The annual means of a transect's monthly means, with nctoolkit,
    smoothed by its rolling_mean if the window is more than 1 year: (positions,
    the years, the values by year then position, or by year, depth and position
    if vertical), or (None, [], None) if no
    year has all its months, or there are fewer years than the window.
    extracted is the dataset of monthly means along the line, which is left as
    it is; times are its (year, month). Only years with all their months are
    used, so that a year with months missing does not count. The years are
    those of the output: with a window, each is the last year of its window."""
    window = spec.get("window", 1)
    years = complete_years(times, spec["start"], spec["end"], per_year)
    if not years or len(years) < window:
        return None, [], None
    ds = extracted.copy()
    ds.subset(years=years)
    ds.tmean("year")
    if window > 1:
        ds.rolling_mean(window)
    ds.run()
    out_years = [time.year for time in ds.times]
    positions, _, values = _along(ds.current[0], key, line, vertical)
    return positions, out_years, values


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


def on_common_depths(depths, values, common=None):
    """values by depth then position, where each position has depths of its
    own (depths, the same shape), on N_DEPTHS evenly spaced depths that all
    positions share, from the shallowest to the deepest: (the depths, the
    values). Each position is linearly interpolated between its cells, and
    is missing outside them, where it is the seabed or the surface. common
    is those depths, if they are already known (values of different years
    are then on the same ones)."""
    valid = np.isfinite(depths) & np.isfinite(values)
    if common is None:
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
    if row.get("unify"):
        # the thickness goes where the values do: onto the comparison's common grid
        sim_unify.apply(ds, row["unify"], row["key"], LEVELS)
    # and onto the domain mask's, masked
    domain_mask.mask_gridded(ds, row.get("domain_mask"))
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
    """What the surface climatology or the vertical section of a transect
    works out from values (see _along, with their times, each (year, month)):
    (a data frame, notes). spec is the option's {"start", "end"}. The frame has
    position, then depth for the vertical, then month for the surface
    climatology (unless the output is annual), then value. The changes are
    derive_changes."""
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
    raise ValueError(f"{option} is not something that derive works out.")


def panel_depths(depths):
    """The depths the vertical changes are shown at, from those there are
    (in order): DEPTH_PANELS of them, from the shallowest to three quarters
    of the way down (the deepest has values at the deepest place only)."""
    depths = np.asarray(depths, dtype="float64")
    if len(depths) <= DEPTH_PANELS:
        return depths
    wanted = depths[0] + (depths[-1] - depths[0]) * np.arange(DEPTH_PANELS) / DEPTH_PANELS
    return depths[sorted({int(np.abs(depths - target).argmin()) for target in wanted})]


def _changes_frame(positions, years, values, depths=None):
    """The frame of the changes: position, then depth (if depths), then year and
    value, from values by year then position (by year, depth and position
    if depths, those of the section)."""
    if depths is None:
        year_grid, position_grid = np.meshgrid(years, positions, indexing="ij")
        data = {"position": position_grid.ravel(), "year": year_grid.ravel()}
    else:
        year_grid, depth_grid, position_grid = np.meshgrid(years, depths, positions, indexing="ij")
        data = {"position": position_grid.ravel(), "depth": depth_grid.ravel(), "year": year_grid.ravel()}
    data["value"] = np.asarray(values, dtype="float64").ravel()
    return pd.DataFrame(data)


def _empty_changes(vertical):
    """The frame of changes there are none of."""
    return pd.DataFrame({name: [] for name in (["position", "depth"] if vertical else ["position"]) + ["year", "value"]})


def derive_changes(positions, years, values, spec, left_out, per_year, depths=None):
    """The changes of a transect over the years from its annual means (see
    annual_means), as a heat map of position by year: (a data frame, notes).
    positions are along the transect and spec the option's {"start", "end",
    "how", "window"}; left_out the years from start to end that were left out
    because they do not have all their months. The frame has position, year
    and value, a row for each of the annual means (with a rolling mean, a year
    is the last of its window). The value is the annual mean ("absolute"), or
    its difference from the first annual mean ("absolute_change"), or that as
    a percentage of the first ("percentage_change"), which is missing where the first
    is 0. The frame has no rows if values is None. For the section through the
    water column depths are those of the cells (see derive), values are by
    year, depth and position, and the frame has depth too, at
    panel_depths of the section."""
    start, how = spec["start"], spec["how"]
    window = spec.get("window", 1)
    vertical = depths is not None
    notes = []
    if left_out:
        notes.append(_left_out(left_out, per_year))
        if window > 1:
            notes.append("The rolling mean is over the years there is output for.")
    if values is None or len(values) == 0:
        return _empty_changes(vertical), notes
    if how != ABSOLUTE:
        # the first period the changes are from: the first window years of the years
        missing = [year for year in range(start, start + window) if year in left_out]
        if missing:
            notes.clear()
            for year in missing:
                notes.append(
                    f"{year} " + ("does not have all 12 months of output" if per_year > 1 else "has no output")
                    + ", so there is no change."
                )
            return _empty_changes(vertical), notes
    shown = None
    if vertical:
        if depths.ndim == 2:
            # each position has its own depths: all on the depths there are somewhere
            used = depths[np.isfinite(depths) & np.isfinite(values).any(axis=0)]
            if used.size == 0:
                return _empty_changes(vertical), notes
            common = np.linspace(used.min(), used.max(), N_DEPTHS)
            values = np.array([on_common_depths(depths, year_values, common)[1] for year_values in values])
            depths = common
        keep = np.isfinite(values).any(axis=(0, 2))
        shown = [depth for depth in panel_depths(depths) if keep[int(np.abs(depths - depth).argmin())]]
        if not shown:
            return _empty_changes(vertical), notes
        values = values[:, [int(np.abs(depths - depth).argmin()) for depth in shown], :]
    if how != ABSOLUTE:
        first = values[0]
        change = values - first
        if how == PERCENTAGE_CHANGE:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                change = np.where(first != 0, 100 * change / np.abs(first), np.nan)
        values = change
    return _changes_frame(positions, years, values, shown), notes


def _left_out(years, per_year):
    """What the report says of years that were left out of a trend."""
    many = len(years) != 1
    return (
        f"{', '.join(str(year) for year in years)} {'were' if many else 'was'} left out, as the output does not "
        + (f"have all 12 months in {'them' if many else 'it'}." if per_year > 1 else "have any.")
    )


def _units(units, option, how=None):
    """What the values of an option are in."""
    if option in TREND_OPTIONS and how == PERCENTAGE_CHANGE:
        return "%"
    return units


def _strip_depths(path, line, steps, ends):
    """The depth of each cell along a transect of raw FVCOM output, by depth
    then position, from the thickness of its layers on the line's strip (see
    oceanval.fvcom_transects)."""
    from oceanval.fvcom_transects import THICKNESS

    ds = nc.open_data(path, checks=False)
    ds.to_transect(start=ends[0], end=ends[1], nsteps=steps)
    ds.run()
    _, _, values = _along(ds.current[0], THICKNESS, line, True)
    return cell_depths(values[0])


def work_out(row, results_dir, time_shift, thickness=None, thickness_for=None):
    """The transects that row asks for (see check_arguments, with its files
    and times added by analyze), saved in results_dir. Returns what INFO_FILE
    says of them: {"start", "end", "files", "dates", "lines": [...]}, each
    transect with its position in lines (from 1), its ends, and "options":
    {option: its years, CSV and what the report says about it}. thickness is
    analyze()'s, which says whether the model is on fixed depth levels
    (Z_LEVEL), whose levels are the depths of the cells, or where to read the
    cell thickness from, which places each cell at its depth; thickness_for
    keeps what it was read as, for each file pattern. Raw FVCOM output (row's
    "fvcom") needs no thickness: each line is extracted from a strip of a
    lon-lat grid along it (see oceanval.fvcom_transects)."""
    if not row.get("fvcom"):
        return _work_out(row, results_dir, time_shift, thickness, thickness_for)
    folder = tempfile.mkdtemp(prefix="oceanval_fvcom_transects_")
    try:
        return _work_out(row, results_dir, time_shift, None, thickness_for, folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _work_out(row, results_dir, time_shift, thickness, thickness_for, strip_folder=None):
    """work_out, with strip_folder where raw FVCOM output's strips are kept."""
    asked = row["transects"]
    thickness_for = {} if thickness_for is None else thickness_for
    present = {
        (int(y), int(m))
        for df in row["times"].values()
        for y, m in zip(df.year, df.month)
        if asked["start"] <= int(y) <= asked["end"]
    }
    per_year = months_per_year(present)
    # the years of each layer: every year any transect asks of it
    layer_years = {}
    for vertical in (False, True):
        used = [
            line[option]
            for line in asked["lines"]
            for option in (VERTICAL_OPTIONS if vertical else SURFACE_OPTIONS)
            if line[option]
        ]
        if used:
            layer_years[vertical] = (min(x["start"] for x in used), max(x["end"] for x in used))
    fvcom = strip_folder is not None
    monthly = {}
    if fvcom:
        # raw FVCOM output: the monthly means on the mesh, put on a strip along each line
        from oceanval import fvcom_climatology, fvcom_transects
        from oceanval.analysis import file_years

        _say(f"Working out the transects of {row['name']} on the FVCOM mesh")
        needs = [
            (number, line, {vertical: any(line[option] for option in (VERTICAL_OPTIONS if vertical else SURFACE_OPTIONS))
                            for vertical in (False, True)})
            for number, line in enumerate(asked["lines"], start=1)
        ]
        fvcom_steps, strips = fvcom_transects.banded(
            row, asked["files"], needs, layer_years, time_shift, strip_folder, file_years(row)
        )
    else:
        # the monthly means of each layer, once
        for vertical, years in layer_years.items():
            _say(
                f"Working out the transects of {row['name']}: "
                + ("through the water column" if vertical else "at the surface")
            )
            ds = monthly_means_of(asked["files"], row, LEVELS if vertical else SURFACE, years, time_shift, row["levels"])
            ds.run()
            monthly[vertical] = (ds, [(time.year, time.month) for time in ds.times])
    extracted_for = {}
    saved = []
    for number, line in enumerate(asked["lines"], start=1):
        if fvcom:
            steps = fvcom_steps[number]
        else:
            steps = unified_steps(row, line) if row.get("unify") or row.get("domain_mask") else None
            if steps is None:
                steps = steps_for(row["files"][0], row["parts"][0], line)
        ends = transects.line_ends(line)
        along = {}
        for vertical in layer_years:
            if not any(line[option] for option in (VERTICAL_OPTIONS if vertical else SURFACE_OPTIONS)):
                continue
            if fvcom:
                path, times, thickness_path = strips[(number, vertical)]
                ds = nc.open_data(path, checks=False)
            else:
                ds, times = monthly[vertical]
            extracted = ds.copy()
            extracted.to_transect(start=ends[0], end=ends[1], nsteps=steps)
            extracted.run()
            positions, depths, values = _along(extracted.current[0], row["key"], line, vertical)
            if vertical and fvcom:
                depths = _strip_depths(thickness_path, line, steps, ends)
            elif vertical and thickness != Z_LEVEL:
                depths = _depths_along(row, line, steps, ends, thickness, thickness_for)
            along[vertical] = (positions, depths, values, times)
            extracted_for[vertical] = extracted
        options = {}
        for option in OPTIONS:
            spec = line[option]
            if not spec:
                continue
            positions, depths, values, times = along[option in VERTICAL_OPTIONS]
            if option in TREND_OPTIONS:
                vertical = option == VERTICAL_TRENDS
                _, series_years, series = annual_means(
                    extracted_for[vertical], row["key"], line, times, spec, per_year, vertical
                )
                left_out = [
                    year for year in range(spec["start"], spec["end"] + 1) if year not in complete_years(times, year, year, per_year)
                ]
                frame, notes = derive_changes(
                    positions, series_years, series, spec, left_out, per_year, depths if vertical else None
                )
            else:
                frame, notes = derive(option, positions, depths, values, times, spec, per_year)
            csv = f"{row['key']}_transect_{number}_{option}.csv"
            frame.to_csv(os.path.join(results_dir, csv), index=False)
            how = spec.get("how")
            options[option] = {
                "title": f"{OPTIONS[option]}: {HOWS[how].lower()}" if option in TREND_OPTIONS else OPTIONS[option],
                "start": spec["start"],
                "end": spec["end"],
                "how": how,
                "window": spec.get("window"),
                # the first and last of the years the heat map shows (with a rolling mean, the end of the first window)
                "years": [int(frame["year"].min()), int(frame["year"].max())] if "year" in frame and len(frame) else None,
                "units": _units(row["units"], option, how),
                "depths": None if option not in VERTICAL_OPTIONS else (
                    "sigma" if fvcom else "levels" if thickness == Z_LEVEL else "thickness"
                ),
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
    out = {
        "start": asked["start"],
        "end": asked["end"],
        "files": len(asked["files"]),
        "dates": asked["dates"],
        "lines": saved,
    }
    if fvcom:
        # the part of the FVCOM mesh the values were interpolated from
        out["mesh"] = fvcom_climatology.location(asked["files"][0], row["parts"])
    return out
