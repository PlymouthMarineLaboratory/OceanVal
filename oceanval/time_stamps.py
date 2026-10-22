"""Model times that mark the end of the period they average, and shifting
them back so that each falls within it.

Some models (GETM among them) date a time mean at the end of the period it
covers: January's mean at 00:00 on 1 February, a day's mean at 00:00 the
next day. Matched up as they are, each mean is taken for the next month or
day, and the year's last falls in the next year. Without time bounds a file
cannot say whether its times are the start or the end of their periods, so
matchup never decides this itself: check_rows finds the variables whose times
look like ends, and matchup asks whether to shift every model time back a day
(time_shift=-1), before it shows the matchups. Shifted back a day, a day's mean is on its own day and a
month's on the month's last day, which is all matchup compares.
"""

import datetime
import os
import pickle
import re
import warnings

import cftime
import xarray as xr

from oceanval import netcdf_lock

# what matchup suggests for times that look like ends
SUGGESTED = -1

# cell_methods for a value over a period of time, rather than at an instant
_OVER_TIME = re.compile(r"\btime\s*:\s*(mean|maximum|minimum|sum|median|mode|variance|standard_deviation)\b")

# calendars cftime knows by these names
_CALENDARS = {
    "standard", "gregorian", "proleptic_gregorian", "noleap", "365_day",
    "all_leap", "366_day", "360_day", "julian",
}


def valid(time_shift):
    """Whether time_shift is one matchup accepts: None or a whole number of
    days."""
    return time_shift is None or (isinstance(time_shift, int) and not isinstance(time_shift, bool))


def describe(days):
    """The shift in words, e.g. "back one day"."""
    way = "back" if days < 0 else "forward"
    count = abs(days)
    return f"{way} {'one' if count == 1 else count} day{'' if count == 1 else 's'}"


def _calendar_name(name):
    name = str(name or "standard").lower()
    return name if name in _CALENDARS else "standard"


def _time_name(ds, variable):
    """The name of the variable's time dimension, if it has one with a
    coordinate."""
    for dim in ds[variable].dims:
        if "time" in dim.lower() and dim in ds.variables:
            return dim
    return None


def calendar(path):
    """The calendar of the times in a model output file: "standard" unless
    the file says otherwise, or cannot be read."""
    try:
        with netcdf_lock.LOCK, xr.open_dataset(path, decode_times=False) as ds:
            for name in ds.dims:
                if "time" in name.lower() and name in ds.variables:
                    return _calendar_name(ds[name].attrs.get("calendar"))
    except Exception:
        pass
    return "standard"


def _read(path, variable):
    """What one file says about the times of variable: its cell_methods, the
    decoded times and, if the file has them, their upper bounds. None if the
    variable or its times are not in the file."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # the window reads the times of each simulation it compares in a thread of its own
        with netcdf_lock.LOCK, xr.open_dataset(path, decode_times=False) as ds:
            if variable not in ds.variables:
                return None
            time_name = _time_name(ds, variable)
            if time_name is None:
                return None
            time = ds[time_name]
            units = time.attrs.get("units")
            if units is None:
                return None
            cal = _calendar_name(time.attrs.get("calendar"))
            times = list(cftime.num2date(time.values, units, calendar=cal))
            upper = None
            bounds = time.attrs.get("bounds") or time.attrs.get("climatology")
            if bounds and bounds in ds.variables:
                values = ds[bounds].values
                if values.ndim == 2 and values.shape[0] == len(times):
                    upper = list(cftime.num2date(values.max(axis=1), units, calendar=cal))
            return {
                "cell_methods": str(ds[variable].attrs.get("cell_methods", "")),
                "times": times,
                "upper": upper,
                "has_bounds": bool(bounds and bounds in ds.variables),
                "calendar": cal,
            }


def _sample(paths):
    """The files read for a pattern: the first, and the last two."""
    paths = list(paths)
    return list(dict.fromkeys(paths[:1] + paths[-2:]))


def _midnight(time):
    return (time.hour, time.minute, time.second, getattr(time, "microsecond", 0)) == (0, 0, 0, 0)


def _stamp(time):
    return time.strftime("%Y-%m-%d %H:%M")


def _month_text(time):
    return time.strftime("%B %Y")


def _previous_month(time):
    return (time.year - 1, 12) if time.month == 1 else (time.year, time.month - 1)


def _spill(read, paths):
    """Why the times of the files read look like the ends of their periods,
    or None. read is the _read of each path, all without bounds."""
    first = read[paths[0]]["times"][0]
    # a file of several means, all in one month but the last, which is at
    # 00:00 on the 1st of the next: GETM's daily means, a file a month
    for path in paths:
        times = read[path]["times"]
        if len(times) < 2:
            continue
        last = times[-1]
        if last.day != 1 or _midnight(times[0]) and times[0].day == 1:
            continue
        if all((x.year, x.month) == _previous_month(last) for x in times[:-1]):
            return (
                f"{os.path.basename(path)} has its times in {_month_text(times[0])} "
                f"but the last, which is {_stamp(last)}"
            )
    # the last time is 00:00 on 1 January, the only one in its year, and the
    # first is not the start of a year: GETM's monthly means, one a file
    times = sorted(x for path in paths for x in read[path]["times"])
    last = times[-1]
    if (
        len(times) > 1
        and (last.month, last.day) == (1, 1)
        and times[-2].year < last.year
        and not ((first.month, first.day) == (1, 1) and _midnight(first))
    ):
        return f"the last time, {_stamp(last)}, is the only one in {last.year}"
    return None


def _evidence(paths, model_variable):
    """What makes the times of model_variable in paths (one file pattern's
    files, sorted) look like the ends of the periods its values are over, as
    a dict, or None if they do not."""
    variable = model_variable.split("+")[0].strip()
    read = {}
    for path in _sample(paths):
        try:
            found = _read(path, variable)
        except Exception:
            found = None
        if found is not None and len(found["times"]) > 0:
            read[path] = found
    if not read:
        return None
    sampled = list(read)
    head = read[sampled[0]]
    match = _OVER_TIME.search(head["cell_methods"])
    if match is None:
        return None
    times = [x for path in sampled for x in read[path]["times"]]
    if all(read[path]["upper"] is not None for path in sampled):
        # the bounds say where each time is in its period
        ends = all(
            abs((time - upper).total_seconds()) < 1
            for path in sampled
            for time, upper in zip(read[path]["times"], read[path]["upper"])
        )
        if not ends:
            return None
        reason = "its time bounds show each time is the end of its period"
    elif any(read[path]["has_bounds"] for path in sampled):
        return None
    else:
        if not all(_midnight(x) for x in times):
            return None
        reason = _spill(read, sampled)
        if reason is None:
            return None
    step = datetime.timedelta(days=SUGGESTED)
    first, last = read[sampled[0]]["times"][0], read[sampled[-1]]["times"][-1]
    return {
        "cell_methods": head["cell_methods"],
        "has_bounds": head["has_bounds"],
        "calendar": head["calendar"],
        "first": _stamp(first),
        "last": _stamp(last),
        "first_shifted": _stamp(first + step),
        "last_shifted": _stamp(last + step),
        "reason": reason,
    }


def check_rows(chosen, files, titles=None):
    """The model variables whose times look like the ends of the periods
    their values are over, as dicts to show and ask about.

    chosen has a row for each variable matched up, with its model_variable
    and file pattern, and files the sorted paths of each pattern. Only a few
    files of each pattern are read. A row is only given for a time mean (or
    other statistic over time, by its cell_methods) whose time bounds put
    each time at the end of its period or, without bounds, whose times are
    all at 00:00 and run one past the end of a month or a year: a file of
    one month's times that ends at 00:00 on the 1st of the next, or a last
    time at 00:00 on 1 January that is alone in its year.
    """
    rows = []
    seen = {}
    for row in chosen.itertuples():
        paths = files.get(row.pattern) or []
        key = (row.pattern, row.model_variable)
        if key not in seen:
            seen[key] = _evidence(paths, row.model_variable) if paths else None
        evidence = seen[key]
        if evidence is None:
            continue
        rows.append(
            dict(
                {
                    "key": row.variable,
                    "variable": row.variable,
                    "title": (titles or {}).get(row.variable, row.variable),
                    "model_variable": row.model_variable,
                    "pattern": row.pattern,
                },
                **evidence,
            )
        )
    return rows


def warning(rows, asked=True):
    """What a terminal is told about the variables in rows. asked is whether
    the question follows; if not, it says how to shift the times."""
    lines = [
        "******************************",
        "Possible problem with the model's times:",
        "these model variables are time means whose times look like the END of the",
        "period each mean covers. If so, each mean is dated in the next day or month:",
        "a January mean is matched up as February's, and the year's last mean falls",
        "in the next year.",
    ]
    if not any(row["has_bounds"] for row in rows):
        lines[-1] += " Their files have no time bounds to say for certain."
    for row in rows:
        bounds = "time bounds" if row["has_bounds"] else "no time bounds"
        lines.append(
            f"  {row['title']}: {row['model_variable']} in {row['pattern']}, "
            f"cell_methods \"{row['cell_methods']}\", {bounds}"
        )
        lines.append(f"    times {row['first']} to {row['last']}: {row['reason']}")
        lines.append(
            f"    shifted {describe(SUGGESTED)}: {row['first_shifted']} to {row['last_shifted']}"
        )
    lines.append(f"Shifted {describe(SUGGESTED)}, each mean is in the period it covers.")
    if not asked:
        lines.append(
            "The times are left as they are. To shift them, add "
            f"time_shift={SUGGESTED} to your matchup call, or time_shift=0 to stop this check."
        )
    return "\n".join(lines)


def end_message(rows):
    """What is said again at the end of a run that left the times as they
    are without asking."""
    names = ", ".join(sorted({row["model_variable"] for row in rows}))
    return (
        f"The times of {names} look like the end of the period each mean covers, and were "
        f"not shifted. If they are, run matchup again with time_shift={SUGGESTED}."
    )


def ask(rows, ask):
    """Ask whether to shift the model's times, through ask(question,
    choices, details) (oceanval.prompts.ask): the window shows the rows with
    the question. Returns the shift in days: SUGGESTED or 0."""
    details = {"kind": "time_shift", "rows": rows, "suggested": SUGGESTED}
    while True:
        answer = ask(
            f"Shift the model's times {describe(SUGGESTED)}? (y/n) ", ("y", "n"), details=details
        ).strip().lower()
        if answer in ("y", "n"):
            return SUGGESTED if answer == "y" else 0
        print("Provide y or n")


def shift_dates(df, days, calendar="standard"):
    """df (with year, month and day columns, one row for each time) with its
    dates moved by days, in calendar. The rows stay in order. A date moved by
    whole days is the same whatever the time of day it had."""
    if days == 0 or len(df) == 0:
        return df
    step = datetime.timedelta(days=days)
    cal = _calendar_name(calendar)
    moved = [
        cftime.datetime(int(y), int(m), int(d), calendar=cal) + step
        for y, m, d in zip(df.year, df.month, df.day)
    ]
    df = df.copy()
    df["year"] = [x.year for x in moved]
    df["month"] = [x.month for x in moved]
    df["day"] = [x.day for x in moved]
    return df


def shift_index(times, days, calendar_of):
    """Move the dates of every file in times (as index_times gives them,
    {path: DataFrame}) by days, in place. calendar_of(path) is the calendar
    of the file's times."""
    if days == 0:
        return times
    for path in list(times):
        times[path] = shift_dates(times[path], days, calendar_of(path))
    return times


def stored_time_shift(path):
    """The time_shift a matchup was made with, from its matchup_dict.pkl:
    0 if it does not say, as a matchup made before there was a time_shift
    was not shifted, or if it cannot be read."""
    try:
        with open(path, "rb") as f:
            return int(pickle.load(f).get("time_shift", 0) or 0)
    except Exception:
        return 0


def report_text(days):
    """What a report page says about a matchup made with the model's times
    shifted by days: nothing if they were not."""
    if not days:
        return ""
    text = f"The model's times were shifted {describe(days)} before matching up"
    if days == SUGGESTED:
        text += ", as they mark the end of the period each model value covers"
    return text + "."
