"""Map temporal changes in analyze(): maps of how a variable changed between
a historical period and one or two future periods.

Each variable has its own periods (a first and last year for each: the
historical one, and one or two future ones), its own layers (the surface, the
bottom, the water column, as for the climatology) and its own choice of what
is drawn (see PLOTS) and of how the change is given (see CHANGES):

- "absolute": the future period's mean minus the historical period's.
- "percentage": that as a percentage of the historical mean, of its absolute
  value, so that an increase is positive, and missing where the historical
  mean is 0.

The map of a period is the mean of its years' annual means, as for the
climatology's annual map: each month of each year is averaged first, then the
months of the period. The maps and the changes are saved as small netCDF
files, one for each period and each change, that the report's figures draw
(see oceanval.analysis_plots).
"""

import os
import warnings

import numpy as np
import xarray as xr

from oceanval.analysis import (
    LAYER_TITLES,
    WATER_COLUMN,
    _labelled,
    _layers,
    _save,
    _say,
    _years,
    climatology_of,
    integral_units,
    thickness_data,
)

HISTORICAL = "historical"
ABSOLUTE = "absolute"
PERCENTAGE = "percentage"
# how the change is given, as the window's drop-down has them
CHANGES = {ABSOLUTE: "Absolute change", PERCENTAGE: "Percentage change"}
# what is drawn, as the window's drop-down has them
PLOTS = {
    "absolute": "Historical + Future (absolute values)",
    "absolute_changes": "Historical + Future (absolute values) + Changes",
    "changes": "Historical + Changes",
}
# the most future periods there can be
MAX_FUTURE = 2


def future_name(number):
    """The name of the number-th future period (from 1)."""
    return f"future{number}"


def check_entry(entry, name):
    """One variable's entry in map_changes, checked, as

        {"plot", "change", "layers", "periods": [{"name", "title", "start", "end"}, ...]}

    with the historical period first, then the one or two future ones. Raises
    ValueError saying what is wrong."""
    kind = "map_changes"
    if not isinstance(entry, dict):
        raise ValueError(
            f"{kind}[{name!r}] must be a dict with a historical period, one or two future "
            "periods, a plot and a change."
        )
    plot = entry.get("plot")
    if plot not in PLOTS:
        raise ValueError(
            f"The plot of {kind}[{name!r}] must be one of {', '.join(repr(x) for x in PLOTS)}, not {plot!r}."
        )
    change = entry.get("change")
    if change not in CHANGES:
        raise ValueError(
            f"The change of {kind}[{name!r}] must be \"absolute\" or \"percentage\", not {change!r}."
        )
    if HISTORICAL not in entry:
        raise ValueError(f"{kind}[{name!r}] has no historical period.")
    future = entry.get("future")
    if isinstance(future, dict):
        future = [future]
    if not isinstance(future, (list, tuple)) or not future:
        raise ValueError(f"{kind}[{name!r}] needs a future: a list of one or two periods, each with a start and an end.")
    if len(future) > MAX_FUTURE:
        raise ValueError(f"{kind}[{name!r}] can have at most {MAX_FUTURE} future periods, not {len(future)}.")
    periods = []
    for period_name, given in [(HISTORICAL, entry[HISTORICAL])] + [
        (future_name(number), x) for number, x in enumerate(future, start=1)
    ]:
        label = "the historical period" if period_name == HISTORICAL else f"future period {period_name[len('future'):]}"
        start, end = _years(given, name, f"{label} of {kind}")
        title = "Historical" if period_name == HISTORICAL else f"Future {period_name[len('future'):]}"
        periods.append({"name": period_name, "title": title, "start": start, "end": end})
    for number, period in enumerate(periods):
        for other in periods[:number]:
            if (period["start"], period["end"]) == (other["start"], other["end"]):
                raise ValueError(
                    f"The {period['title'].lower()} and {other['title'].lower()} periods of {kind}[{name!r}] are "
                    f"both {period['start']}-{period['end']}: there is no change between them."
                )
    return {
        "plot": plot,
        "change": change,
        "layers": _layers(entry, name, kind),
        "periods": periods,
        "start": min(x["start"] for x in periods),
        "end": max(x["end"] for x in periods),
    }


def change_between(historical, future, change):
    """The change from historical to future, both arrays: future minus
    historical, or that as a percentage of the absolute value of historical
    (missing where it is 0)."""
    difference = future - historical
    if change == PERCENTAGE:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return np.where(historical != 0, 100 * difference / np.abs(historical), np.nan)
    return difference


def change_units(units, change):
    """What the change is in."""
    return "%" if change == PERCENTAGE else units


def derive(historical_path, future_path, key, change, out, units, what):
    """Save the change from the file historical_path to the file future_path
    (each with a variable called key, from a single time) in out, with
    the time and the grid of the future's."""
    with xr.open_dataset(historical_path) as ds_historical, xr.open_dataset(future_path) as ds_future:
        result = ds_future.copy(deep=True)
        values = change_between(ds_historical[key].values.astype("float64"), ds_future[key].values.astype("float64"), change)
        result[key] = (ds_future[key].dims, values.astype("float32"), dict(ds_future[key].attrs))
        result[key].attrs["units"] = units
        result[key].attrs["long_name"] = what
        result[key].encoding["_FillValue"] = np.float32(-1e20)
        # the dataset is read lazily: the data must be in memory before out can replace a file it came from
        result.load()
    if os.path.exists(out):
        os.remove(out)
    result.to_netcdf(out)
    return out


def work_out(row, results_dir, time_shift, thickness=None, thickness_for=None):
    """The map changes that row asks for (see check_arguments, with its files
    and dates added by analyze), saved in results_dir. Returns what INFO_FILE
    says of them: {"plot", "change", "start", "end", "periods": [...],
    "layers": {layer: {"title", "units", "change_units", "historical",
    "futures": [file names], "changes": [file names]}}}."""
    asked = row["map_changes"]
    thickness_for = {} if thickness_for is None else thickness_for
    layers = {}
    for layer in asked["layers"]:
        ds_thickness = None
        if layer in WATER_COLUMN and not row.get("fvcom"):
            if row["pattern"] not in thickness_for:
                thickness_for[row["pattern"]] = thickness_data(thickness, row["files"], row.get("as_missing"))
            ds_thickness = thickness_for[row["pattern"]]
        units = integral_units(row["units"]) if layer == "integral" else row["units"]
        shown_in = change_units(units, asked["change"])
        saved = {}
        for period in asked["periods"]:
            _say(
                f"Working out the map of {row['name']}, {LAYER_TITLES[layer].lower()}, "
                f"{period['title'].lower()} period ({period['start']}-{period['end']})"
            )
            what = f"{LAYER_TITLES[layer].lower()}, mean of {period['start']}-{period['end']}"
            path = os.path.join(results_dir, f"{row['key']}_{layer}_{period['name']}.nc")
            if row.get("fvcom"):
                # raw FVCOM output, on its own mesh (see oceanval.fvcom_climatology)
                from oceanval import fvcom_climatology
                from oceanval.analysis import file_years

                mesh, _ = fvcom_climatology.work_out(
                    period["files"], row["parts"], [period["start"], period["end"]], row["key"], None, path, units,
                    f"{row['name']}, {what}", time_shift=time_shift, as_missing=row.get("as_missing"),
                    cores=row.get("cores", 1), layer=layer, file_years=file_years(row),
                )
            else:
                ds = climatology_of(
                    period["files"], row, layer, (period["start"], period["end"]), time_shift, row["levels"], ds_thickness
                )
                with warnings.catch_warnings(record=True):
                    ds.tmean()
                    ds.run()
                _labelled(ds, row, units, what)
                _save(ds, path)
            saved[period["name"]] = path
        changes = []
        for number, period in enumerate(asked["periods"][1:], start=1):
            out = os.path.join(results_dir, f"{row['key']}_{layer}_change{number}.nc")
            derive(
                saved[HISTORICAL],
                saved[period["name"]],
                row["key"],
                asked["change"],
                out,
                shown_in,
                f"{row['name']}, {LAYER_TITLES[layer].lower()}, {CHANGES[asked['change']].lower()} from "
                f"{asked['periods'][0]['start']}-{asked['periods'][0]['end']} to {period['start']}-{period['end']}",
            )
            changes.append(os.path.basename(out))
        layers[layer] = {
            "title": LAYER_TITLES[layer],
            "units": units,
            "change_units": shown_in,
            "historical": os.path.basename(saved[HISTORICAL]),
            "futures": [os.path.basename(saved[x["name"]]) for x in asked["periods"][1:]],
            "changes": changes,
        }
        if row.get("fvcom"):
            layers[layer]["mesh"] = mesh
    return {
        "plot": asked["plot"],
        "change": asked["change"],
        "start": asked["start"],
        "end": asked["end"],
        "periods": [
            {k: period[k] for k in ("name", "title", "start", "end", "dates")}
            | {"files": len(period["files"])}
            for period in asked["periods"]
        ],
        "layers": layers,
    }
