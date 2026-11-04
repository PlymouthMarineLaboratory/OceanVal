"""The forms of the oceanval window's Analyze steps (see
oceanval.analysis_app): what they start with, and the checks of what is
entered in them. Nothing here changes the window.

The steps are the simulation (the setup page, without the domain), the
variables to analyse ("Environmental variable" and the model variable holding
it), the files holding them with any gaps in their times, and the analyses, each
in a pop-out: climatology, phenology, the long-term and seasonal time series,
which share the regions, lon/lat box and stations they are of, and
transects. Where the output is saved is asked last.
"""

import datetime
import os
import re

import pandas as pd

from oceanval import analysis
from oceanval import time_res as time_resolution

# the most rows the variables step takes
MOST_ROWS = 200

# the setup page's boxes that the Analyze steps have no use for
_NOT_ANALYZED = ("domain", "start", "end")


def default_setup_form(cwd):
    """The simulation step's boxes for an analysis, as they start. Its out_dir,
    out and replace_analysis are the last page's, which starts with them."""
    from oceanval.app import default_setup_form as setup_form, free_name

    # replace_analysis: whether the user has ticked to replace the analysis
    # the output directory has already, which it must have if it does
    # out is analysis.py, or the first of analysis1.py... that is free in cwd; out_typed
    # is whether the user typed it, the only name warned of if the file exists
    return dict(
        setup_form(),
        out_dir=cwd,
        out=free_name(cwd, "analysis"),
        out_typed=False,
        replace_analysis=False,
    )


def check_setup(form, cwd):
    """The simulation step's boxes as analyze() arguments: (arguments,
    errors), as oceanval.app.check_setup gives them, without the domain and
    the years, which an analysis has no use for. The output is asked for last
    (see check_output)."""
    from oceanval.app import _form
    from oceanval.app import check_setup as check

    form = _form(form, default_setup_form(cwd))
    arguments, errors = check(dict(form, domain="global", start="0", end="0"), cwd)
    for name in _NOT_ANALYZED:
        arguments.pop(name, None)
        errors.pop(name, None)
    return arguments, errors


def check_output(form, cwd, folder=analysis.FOLDER, name_the_directory=None):
    """The last page's boxes, out_dir, out and replace_analysis: (arguments,
    errors) with the full paths {"out_dir", "out"}, and for each box that cannot
    be used the reason.

    What is in folder (an analysis's, or a comparison's: see
    oceanval.sim_compare_forms) in the output directory is replaced by the new
    one, so the user must have ticked to say so (replace_analysis), and named
    the directory rather than left the box empty, which is the directory worked
    in (name_the_directory, NAME_THE_DIRECTORY unless given, says so)."""
    from oceanval.app import _form
    from oceanval.app import check_output as check

    form = _form(form, default_setup_form(cwd))
    # what is asked of the matchups there is matchup's, not an analysis's
    arguments, errors = check({"out_dir": form["out_dir"], "out": form["out"]}, cwd)
    arguments = {name: arguments[name] for name in ("out_dir", "out") if name in arguments}
    errors = {name: errors[name] for name in ("out_dir", "out") if name in errors}
    if "out_dir" in arguments and os.path.isdir(os.path.join(arguments["out_dir"], folder)):
        if not form["out_dir"]:
            errors["out_dir"] = name_the_directory or NAME_THE_DIRECTORY
        elif not form["replace_analysis"]:
            errors["replace_analysis"] = ""
    return arguments, errors


# said when an analysis is in the directory worked in and the box is empty; the page says it too
NAME_THE_DIRECTORY = (
    "There is an analysis in the directory you started OceanVal in. "
    "To replace it, name the directory here rather than leaving the box empty."
)


# ---- the variables ----


def picker_state(found):
    """What the variables step's pop-out of the model's variables lists,
    from the streams (see analysis.streams): the names, their long_names, and
    how many levels each has."""
    long_names = {}
    levels = {}
    for stream in found:
        for name, about in stream["variables"].items():
            if about["long_name"] or name not in long_names:
                long_names[name] = about["long_name"]
            levels[name] = max(levels.get(name, 1), int(about["levels"]))
    return {"available": sorted(long_names), "long_names": long_names, "levels": levels}


def _model_variables_for_recipes(found, cwd=None):
    """The model variable the streams hold for each variable the validation
    recipes know, {variable: model variable} (see create_recipes.voted_mapping),
    those it holds none for left out. These are matched as the matchups are."""
    from oceanval.create_recipes import contents_mapping, voted_mapping

    mappings = []
    for stream in found:
        contents = pd.DataFrame(
            {
                "variable": list(stream["variables"]),
                "long_name": [about["long_name"] for about in stream["variables"].values()],
                "nlevels": [about["levels"] for about in stream["variables"].values()],
            }
        )
        try:
            mappings.append(contents_mapping(contents, cwd))
        except Exception:
            continue
    return voted_mapping(mappings, cwd)


def prefill(found, cwd=None):
    """The variables step's rows as OceanVal could fill them in: each variable
    the validation recipes know that the simulation holds, named as the recipes
    window names it. The window does not use it: nothing is chosen for the
    user (see presets)."""
    from oceanval.recipes_gui import _title

    rows = [
        {"name": _title(variable, cwd), "model_variable": model_variable, "filled": True}
        for variable, model_variable in _model_variables_for_recipes(found, cwd).items()
    ]
    return sorted(rows, key=lambda row: row["name"].lower())


def presets(found, cwd=None):
    """The names the variables step's "Your name" box offers in its drop-down:
    every variable the validation recipes know, named as the recipes window
    names it, with the model variable the simulation holds for it, found as
    the matchups find it, or None if it holds none (or cannot tell which)."""
    from oceanval.create_recipes import recipe_variables
    from oceanval.recipes_gui import _title

    mapped = _model_variables_for_recipes(found, cwd)
    rows = [
        {"name": _title(variable, cwd), "model_variable": mapped.get(variable)}
        for variable in recipe_variables(cwd)
    ]
    return sorted(rows, key=lambda row: row["name"].lower())


def clean_rows(rows):
    """The variables step's rows as the page sent them."""
    if not isinstance(rows, list):
        return []
    clean = []
    for row in rows[:MOST_ROWS]:
        if not isinstance(row, dict):
            continue
        clean.append(
            {
                "name": str(row.get("name") or "").strip()[:120],
                "model_variable": "+".join(
                    analysis.model_variable_names(str(row.get("model_variable") or ""))
                ),
                "filled": bool(row.get("filled")),
            }
        )
    return clean


def check_rows(rows, found, time_res_of=analysis.stream_time_res):
    """Check the variables step's rows (see clean_rows) against the streams.
    Returns (placed, errors): placed has a dict for each row to analyse,
    in order, with its name, key, model_variable and parts, the stream it is
    read from (its pattern and files), and its units, long_name and levels;
    errors maps a row's place to {box: reason}, and "rows" to a reason for
    the step as a whole. A row left empty is skipped."""
    errors = {}
    placed = []
    keys = {}

    def wrong(index, box, reason):
        errors.setdefault(str(index), {})[box] = reason

    for index, row in enumerate(rows):
        name, model_variable = row["name"], row["model_variable"]
        if not name and not model_variable:
            continue
        if not name:
            wrong(index, "name", "Give this variable a name.")
        else:
            key = analysis.variable_key(name)
            if not key:
                wrong(index, "name", "The name needs a letter or a digit in it.")
            elif key in keys:
                wrong(index, "name", f"This is the same name as {keys[key]!r}, give it another.")
            else:
                keys[key] = name
        if not model_variable:
            wrong(index, "model_variable", "Give the model variable holding it.")
            continue
        try:
            holding = analysis.place(model_variable, found)
        except ValueError as error:
            wrong(index, "model_variable", str(error))
            continue
        if str(index) in errors:
            continue
        stream, res = analysis.choose_stream(holding, time_res_of)
        placed.append(
            dict(
                {
                    "index": index,
                    "name": name,
                    "key": analysis.variable_key(name),
                    "model_variable": model_variable,
                    "parts": analysis.model_variable_names(model_variable),
                    "pattern": stream["pattern"],
                    "files": list(stream["files"]),
                    "time_res": res,
                    "streams": len(holding),
                    # raw FVCOM output, worked out on its own mesh
                    "fvcom": bool(stream.get("fvcom")),
                },
                **analysis.describe_variable(model_variable, stream),
            )
        )
    if not placed and not errors:
        errors["rows"] = "Add a variable to analyse."
    return placed, errors


# ---- the analyses ----


def output_years(check_row):
    """The first and last years of a variable's output, from what the check
    of its dates found (a row of model_check.ModelCheck.state), or None."""
    groups = (check_row or {}).get("groups") or []
    dates = groups[0].get("dates") if groups else None
    if not dates or not dates.get("first"):
        return None
    return int(dates["first"][:4]), int(dates["last"][:4])


def _year(text):
    try:
        value = int(str(text).strip())
    except ValueError:
        return None
    return value


def _years(box, errors, known):
    """A row's start and end years, with errors added for what is wrong
    with them, including years the output does not have. known is the first
    and last years of the output, if known."""
    start, end = _year(box.get("start", "")), _year(box.get("end", ""))
    if start is None:
        errors["start"] = "Start must be a year, e.g. 2011."
    if end is None:
        errors["end"] = "End must be a year, e.g. 2011."
    if start is not None and end is not None and end < start:
        errors["end"] = "End must not be before start."
    if known and start is not None and end is not None and not errors:
        if end < known[0] or start > known[1]:
            errors["start"] = f"The output is from {known[0]} to {known[1]}."
        else:
            # the years must be ones the output has
            if start < known[0]:
                errors["start"] = f"The output starts in {known[0]}."
            if end > known[1]:
                errors["end"] = f"The output ends in {known[1]}."
    return start, end


def default_climatology(placed, years_of):
    """A layered pop-out's boxes (the climatology's, and the time series'), as
    they start: nothing ticked (the user's rule, 2026-10-31: the user ticks
    the variables to analyse), no layer, no years and no way to take the water column.
    Nothing else is chosen for the user: the years of each variable's output
    are only shown beside it (see output_years)."""
    return {
        "rows": {
            row["name"]: {"use": False, "start": "", "end": "", "surface": False, "bottom": False, "water_column": False, "how": "",
                # the long-term series' rolling mean, none to start with, and how it is shown
                "window": "1", "show": analysis.SHOW_ABSOLUTE,
            }
            for row in placed
        },
        "thickness": "",
    }


def default_phenology(placed, years_of, time_res_of):
    """The phenology pop-out's boxes, as they start: nothing ticked (a variable
    without daily output cannot have a phenology), and no years."""
    return {
        "rows": {
            row["name"]: {"use": False, "start": "", "end": ""} for row in placed
        }
    }


def clean_climatology(form, placed):
    """The climatology pop-out's boxes as the page sent them."""
    form = form if isinstance(form, dict) else {}
    given = form.get("rows") if isinstance(form.get("rows"), dict) else {}
    rows = {}
    for row in placed:
        box = given.get(row["name"]) if isinstance(given.get(row["name"]), dict) else {}
        how = str(box.get("how") or "")
        rows[row["name"]] = {
            "use": bool(box.get("use")),
            "start": str(box.get("start") or "").strip(),
            "end": str(box.get("end") or "").strip(),
            "surface": bool(box.get("surface")),
            "bottom": bool(box.get("bottom")),
            "water_column": bool(box.get("water_column")),
            "how": how if how in analysis.WATER_COLUMN else "",
            "window": str(box.get("window") or "1").strip() or "1",
            "show": box.get("show") if box.get("show") in analysis.SHOWS else analysis.SHOW_ABSOLUTE,
        }
    return {"rows": rows, "thickness": str(form.get("thickness") or "").strip()}


def clean_phenology(form, placed):
    """The phenology pop-out's boxes as the page sent them."""
    form = form if isinstance(form, dict) else {}
    given = form.get("rows") if isinstance(form.get("rows"), dict) else {}
    rows = {}
    for row in placed:
        box = given.get(row["name"]) if isinstance(given.get(row["name"]), dict) else {}
        rows[row["name"]] = {
            "use": bool(box.get("use")),
            "start": str(box.get("start") or "").strip(),
            "end": str(box.get("end") or "").strip(),
        }
    return {"rows": rows}


def check_thickness(thickness, available, cwd, errors):
    """The cell thickness in its box, as analyze() takes it: the name of a
    variable of the output (available), "z_level", or the full path of a file
    (cwd is where a relative one is). errors gets "thickness" if it cannot be
    used."""
    if not thickness:
        errors["thickness"] = (
            "The water column needs the cell thickness: the name of its variable, a "
            f'file with it, or "{analysis.Z_LEVEL}".'
        )
    elif thickness != analysis.Z_LEVEL and thickness not in available:
        path = os.path.normpath(os.path.join(cwd, os.path.expanduser(thickness)))
        if not os.path.isfile(path):
            errors["thickness"] = "This is not a variable in the output, nor a file."
        else:
            thickness = path
    return thickness


def _check_layers(box, row, found):
    """Add to found (the errors of a variable's row) what is wrong with the
    layers ticked in box: none, or the bottom or the water column of a
    variable with one vertical level, or a water column with no way to take it."""
    if not box["surface"] and not box["bottom"] and not box["water_column"]:
        found["surface"] = "Tick the surface, the bottom, the water column, or more than one, or untick the variable."
    if box["bottom"] and row["levels"] < 2:
        found["bottom"] = "It has only one vertical level."
    if box["water_column"] and row["levels"] < 2:
        found["water_column"] = "It has only one vertical level."
    elif box["water_column"] and box["how"] not in analysis.WATER_COLUMN:
        found["water_column"] = "Choose the vertical mean or the depth integral."


def _check_series_options(box, start, end, found):
    """Add to found (the errors of a variable's row) what is wrong with the
    rolling mean and the way of showing a long-term series, and return
    (window, show)."""
    window = 1
    if not re.fullmatch(r"\d+", box["window"]) or int(box["window"]) < 1:
        found["window"] = "The rolling mean is a whole number of years, 1 or more: 1 is none."
        return window, box["show"]
    window = int(box["window"])
    years = end - start + 1
    if window > years:
        found["window"] = f"The rolling mean of {window} years is more than the {years} years from {start} to {end}."
    elif box["show"] != analysis.SHOW_ABSOLUTE and years - window + 1 < analysis.MIN_CHANGE_YEARS:
        found["show"] = (
            f"A change needs at least {analysis.MIN_CHANGE_YEARS + window - 1} years"
            + (f" with a rolling mean of {window}" if window > 1 else "")
            + f", not the {years} from {start} to {end}."
        )
    return window, box["show"]


def check_climatology(form, placed, known_years, available, cwd, label="climatology", series=False, fvcom=False):
    """Check the climatology pop-out's boxes (see clean_climatology). series is
    True for the long-term series' pop-out, which also has a rolling mean and a
    way of showing the series. fvcom: the output is raw FVCOM output, whose
    cell thickness analyze() works out itself, so none is asked for.
    known_years(name) is the first and last years of a variable's output,
    or None; available the model's variable names, which a thickness can be
    one of. Returns (climatology, thickness, errors): climatology as
    analyze() takes it, errors {name: {box: reason}}, "thickness" and
    "rows"."""
    errors = {}
    chosen = {}
    column = False
    for row in placed:
        box = form["rows"][row["name"]]
        if not box["use"]:
            continue
        found = {}
        start, end = _years(box, found, known_years(row["name"]))
        _check_layers(box, row, found)
        window, show = 1, analysis.SHOW_ABSOLUTE
        if series and not found:
            window, show = _check_series_options(box, start, end, found)
        if found:
            errors[row["name"]] = found
            continue
        chosen[row["name"]] = {
            "start": start,
            "end": end,
            "surface": box["surface"],
            "bottom": box["bottom"],
            "water_column": box["how"] if box["water_column"] else None,
        }
        # only what is not the same as leaving it out is given
        if series and window > 1:
            chosen[row["name"]]["window"] = window
        if series and show != analysis.SHOW_ABSOLUTE:
            chosen[row["name"]]["show"] = show
        column = column or box["water_column"]
    thickness = None
    if column and not fvcom:
        thickness = check_thickness(form["thickness"], available, cwd, errors)
    if not chosen and not errors:
        errors["rows"] = f"Tick Surface, Bottom, Water column, or more than one, for at least one variable to work out the {label} of."
    return chosen, thickness, errors


def check_phenology(form, placed, known_years, time_res_of):
    """Check the phenology pop-out's boxes (see clean_phenology). Returns
    (phenology, errors), phenology as analyze() takes it."""
    errors = {}
    chosen = {}
    for row in placed:
        box = form["rows"][row["name"]]
        if not box["use"]:
            continue
        found = {}
        if time_res_of(row["name"]) != time_resolution.DAILY:
            found["use"] = "Its output is not daily."
        start, end = _years(box, found, known_years(row["name"]))
        if found:
            errors[row["name"]] = found
            continue
        chosen[row["name"]] = {"start": start, "end": end}
    if not chosen and not errors:
        errors["rows"] = "Tick at least one variable to work out the phenology of."
    return chosen, errors


# ---- the map changes ----

# the periods of a map changes row: the historical one, and up to two future ones
CHANGE_PERIODS = ("historical", "future1", "future2")
CHANGE_TITLES = {"historical": "Historical", "future1": "Future 1", "future2": "Future 2"}
CHANGE_BOXES = tuple(f"{period}_{end}" for period in CHANGE_PERIODS for end in ("start", "end"))


def default_map_changes(placed):
    """The map changes pop-out's boxes, as they start: nothing ticked,
    no layer, no years, no plot and no change."""
    blank = {box: "" for box in CHANGE_BOXES}
    return {
        "rows": {
            row["name"]: dict(
                blank, use=False, plot="", change="", surface=False, bottom=False, water_column=False, how=""
            )
            for row in placed
        },
        "thickness": "",
    }


def clean_map_changes(form, placed):
    """The map changes pop-out's boxes as the page sent them."""
    from oceanval import analysis_changes

    form = form if isinstance(form, dict) else {}
    given = form.get("rows") if isinstance(form.get("rows"), dict) else {}
    rows = {}
    for row in placed:
        box = given.get(row["name"]) if isinstance(given.get(row["name"]), dict) else {}
        how = str(box.get("how") or "")
        plot, change = str(box.get("plot") or ""), str(box.get("change") or "")
        rows[row["name"]] = dict(
            {name: str(box.get(name) or "").strip() for name in CHANGE_BOXES},
            use=bool(box.get("use")),
            plot=plot if plot in analysis_changes.PLOTS else "",
            change=change if change in analysis_changes.CHANGES else "",
            surface=bool(box.get("surface")),
            bottom=bool(box.get("bottom")),
            water_column=bool(box.get("water_column")),
            how=how if how in analysis.WATER_COLUMN else "",
        )
    return {"rows": rows, "thickness": str(form.get("thickness") or "").strip()}


def check_map_changes(form, placed, known_years, available, cwd, fvcom=False):
    """Check the map changes pop-out's boxes (see clean_map_changes). fvcom:
    the output is raw FVCOM output, whose cell thickness analyze() works out
    itself, so none is asked for. Returns (map_changes, thickness, errors):
    map_changes as analyze() takes it, errors {name: {box: reason}},
    "thickness" and "rows"."""
    errors = {}
    chosen = {}
    column = False
    for row in placed:
        box = form["rows"][row["name"]]
        if not box["use"]:
            continue
        found = {}
        years = {}
        for period in CHANGE_PERIODS:
            start, end = box[f"{period}_start"], box[f"{period}_end"]
            if period == "future2" and not start and not end:
                continue
            here = {}
            years[period] = _years({"start": start, "end": end}, here, known_years(row["name"]))
            found.update({f"{period}_{name}": f"{CHANGE_TITLES[period]}: {reason}" for name, reason in here.items()})
        clear = [x for x in years if f"{x}_start" not in found and f"{x}_end" not in found]
        for number, period in enumerate(clear):
            for other in clear[:number]:
                if years[period] == years[other]:
                    found[f"{period}_start"] = (
                        f"{CHANGE_TITLES[period]}: these are the same years as the {CHANGE_TITLES[other].lower()} period's."
                    )
                    break
        if not box["plot"]:
            found["plot"] = "Choose what to plot."
        if not box["change"]:
            found["change"] = "Choose absolute or percentage changes."
        _check_layers(box, row, found)
        if found:
            errors[row["name"]] = found
            continue
        chosen[row["name"]] = {
            "historical": {"start": years["historical"][0], "end": years["historical"][1]},
            "future": [{"start": years[x][0], "end": years[x][1]} for x in ("future1", "future2") if x in years],
            "plot": box["plot"],
            "change": box["change"],
            "surface": box["surface"],
            "bottom": box["bottom"],
            "water_column": box["how"] if box["water_column"] else None,
        }
        column = column or box["water_column"]
    thickness = None
    if column and not fvcom:
        thickness = check_thickness(form["thickness"], available, cwd, errors)
    if not chosen and not errors:
        errors["rows"] = "Tick Surface, Bottom, Water column, or more than one, for at least one variable to map the changes of."
    return chosen, thickness, errors


# ---- the transects ----

# the boxes of a transect's ends, in the order the transect_* boxes of
# oceanval.app.TRANSECT_BOXES have them
TRANSECT_BOXES = ("start_lon", "start_lat", "end_lon", "end_lat")
# the options of a transect that are ticked (the changes, at the surface and
# in the section, are chosen from drop-downs), each with boxes for its years
TRANSECT_TICKS = ("climatology", "vertical")
# the changes (trends), each with a drop-down for how, and a box for its rolling mean
TRANSECT_CHANGES = ("trends", "vertical_trends")


def blank_transect():
    """A transect's boxes as they start: nothing in them, nothing ticked, no
    trends chosen and no years."""
    return {
        **{box: "" for box in TRANSECT_BOXES},
        "climatology": False, "climatology_start": "", "climatology_end": "",
        "vertical": False, "vertical_start": "", "vertical_end": "",
        "trends": "", "trends_start": "", "trends_end": "", "trends_window": "1",
        "vertical_trends": "", "vertical_trends_start": "", "vertical_trends_end": "", "vertical_trends_window": "1",
    }


def default_transects(placed):
    """The transects pop-out's boxes, as they start: for each variable on its
    own, unticked, with one transect with nothing in it."""
    return {"rows": {row["name"]: {"use": False, "transects": [blank_transect()]} for row in placed}, "thickness": ""}


def _blank(line):
    # the window starts as 1, which is not something that was entered
    return not any(line[box] for box in blank_transect() if not box.endswith("_window"))


def clean_transects(form, placed):
    """The transects pop-out's boxes as the page sent them: for each variable
    whether it is ticked, and its transects, each with its ends, and each of
    its options (surface climatology and changes, vertical climatology and
    changes) with its years."""
    from oceanval import analysis_transects

    form = form if isinstance(form, dict) else {}
    given = form.get("rows") if isinstance(form.get("rows"), dict) else {}
    rows = {}
    for row in placed:
        box = given.get(row["name"]) if isinstance(given.get(row["name"]), dict) else {}
        lines = []
        for line in (box.get("transects") if isinstance(box.get("transects"), list) else [])[:MOST_ROWS]:
            if not isinstance(line, dict):
                continue
            clean = {box_name: str(line.get(box_name) or "").strip() for box_name in blank_transect() if box_name not in TRANSECT_TICKS}
            clean.update({name: bool(line.get(name)) for name in TRANSECT_TICKS})
            for name in TRANSECT_CHANGES:
                how = str(line.get(name) or "")
                clean[name] = how if how in analysis_transects.HOWS else ""
            lines.append(clean)
        rows[row["name"]] = {"use": bool(box.get("use")), "transects": lines or [blank_transect()]}
    return {"rows": rows, "thickness": str(form.get("thickness") or "").strip()}


def check_transects(form, placed, known_years, available=(), cwd=".", fvcom=False):
    """Check the transects pop-out's boxes (see clean_transects).
    known_years(name) is the first and last years of a variable's output, or
    None; available the model's variable names, which the cell thickness, which
    a vertical transect needs, can be one of. fvcom: the output is raw FVCOM
    output, whose layers' thickness analyze() works out from its mesh, so none
    is asked for. Returns (chosen, errors): chosen
    is {"rows": {name: [transect, ...]}, "thickness"}, the variables ticked with
    their transects as analyze() takes them, and the thickness (None if no
    transect is vertical). errors
    maps a variable to {"use": reason, "transects": {transect: {box: reason}}}
    (each transect by its number for the variable, from 0), and "rows" to a
    reason for the pop-out as a whole. A transect with nothing in it is left out."""
    from oceanval import analysis_transects
    from oceanval.app import TRANSECT_BOXES as APP_BOXES
    from oceanval.app import check_transect_boxes

    errors = {}
    chosen = {}
    for row in placed:
        box = form["rows"][row["name"]]
        if not box["use"]:
            continue
        kept = [(number, line) for number, line in enumerate(box["transects"]) if not _blank(line)]
        found_lines, lines = {}, []
        for number, line in kept:
            found = {}
            given = {"transect": True, **{app: line[name] for app, name in zip(APP_BOXES, TRANSECT_BOXES)}}
            raw = {}
            ends = check_transect_boxes(given, raw)
            for app, name in zip(APP_BOXES, TRANSECT_BOXES):
                if app in raw:
                    found[name] = raw[app]
            if "transect" in raw:
                errors["rows"] = raw["transect"]
            options = {}
            if not any(line[option] for option in analysis_transects.OPTIONS):
                found["options"] = "Tick a Climatology or choose Changes, in the Surface or the Vertical, or more than one."
            for option in analysis_transects.OPTIONS:
                if not line[option]:
                    continue
                years = {}
                start, end = _years(
                    {"start": line[f"{option}_start"], "end": line[f"{option}_end"]}, years, known_years(row["name"])
                )
                for name, reason in years.items():
                    found[f"{option}_{name}"] = reason
                options[option] = {"start": start, "end": end}
            if (line["vertical"] or line["vertical_trends"]) and row["levels"] < 2:
                reason = "It has only one vertical level, so it has no section through the water column."
                for option in ("vertical", "vertical_trends"):
                    if line[option]:
                        found[option] = reason
            for option in analysis_transects.TREND_OPTIONS:
                how = line[option]
                window = 1
                if how:
                    text = line[f"{option}_window"] or "1"
                    if not text.isdigit() or int(text) < 1:
                        found[f"{option}_window"] = "The window is a whole number of years, 1 or more: 1 is no rolling mean."
                    else:
                        window = int(text)
                output = known_years(row["name"])
                needed = analysis_transects.MIN_CHANGE_YEARS
                if how and output and f"{option}_window" not in found:
                    n_output = output[1] - output[0] + 1
                    if n_output - window + 1 < needed:
                        found[option] = (
                            f"{analysis_transects.HOWS[how]} needs at least {needed} years of output"
                            + (f" after a rolling mean of {window}, so {needed + window - 1}" if window > 1 else "")
                            + f": {row['name']}'s is {'from ' + str(output[0]) + ' to ' + str(output[1]) if n_output > 1 else 'only ' + str(output[0])}."
                        )
                if how and option in options and not any(name.startswith(f"{option}_") or name == option for name in found):
                    span = options[option]["end"] - options[option]["start"] + 1
                    if span - window + 1 < needed:
                        found[f"{option}_end"] = (
                            f"{analysis_transects.HOWS[how]} needs at least {needed} years"
                            + (f" after a rolling mean of {window}, so {needed + window - 1}" if window > 1 else "")
                            + f", and this is {span}."
                        )
                    options[option]["how"] = how
                    # 1 is no rolling mean, and is not written
                    if window > 1:
                        options[option]["window"] = window
            if found:
                found_lines[str(number)] = found
            elif ends is not None:
                lines.append(
                    {"start": ends["start"], "end": ends["end"], **{option: options.get(option) for option in analysis_transects.OPTIONS}}
                )
        problems = {}
        if found_lines:
            problems["transects"] = found_lines
        if not kept:
            problems["use"] = "Add a transect for this variable: the longitude and latitude of its start and end, and what to work out along it. Or untick it."
        if problems:
            errors[row["name"]] = problems
        else:
            chosen[row["name"]] = lines
    if not chosen and not errors:
        errors["rows"] = "Tick at least one variable to work out transects of."
    thickness = None
    if not fvcom and any(line["vertical"] or line["vertical_trends"] for lines in chosen.values() for line in lines):
        thickness = check_thickness(form["thickness"], set(available), cwd, errors)
    if errors:
        return None, errors
    return {"rows": chosen, "thickness": thickness}, {}


# ---- the area of the time series ----

AREA_BOXES = ("subregions", "subregions_file", "lon_min", "lon_max", "lat_min", "lat_max")


def default_area():
    """The boxes of the regions and area of the time series, as they start:
    no regional summaries, all of the model's domain, and no stations."""
    return {**{name: "" for name in AREA_BOXES}, "stations": []}


def clean_area(form):
    """The area's boxes as the page sent them."""
    form = form if isinstance(form, dict) else {}
    area = {name: str(form.get(name) or "").strip() for name in AREA_BOXES}
    stations = form.get("stations") if isinstance(form.get("stations"), list) else []
    area["stations"] = [
        [str(box if box is not None else "").strip() for box in row]
        for row in stations[:MOST_ROWS]
        if isinstance(row, list) and len(row) == 3
    ]
    return area


def check_stations(rows, errors=None):
    """The stations in rows, each [name, lon, lat], as analyze()'s stations:
    {name: [lon, lat]}. Rows with nothing in them are left out. errors, if
    given, gets {row: {box: reason}} for those that cannot be used, under
    "stations", and the result is then None."""
    stations, found = {}, {}
    for number, (name, lon, lat) in enumerate(rows):
        if not (name or lon or lat):
            continue
        problems = {}
        if not name:
            problems["name"] = "Name the station."
        elif name in stations:
            problems["name"] = "Another station has this name."
        values = {}
        for box, text, low, high in (("lon", lon, -180, 360), ("lat", lat, -90, 90)):
            try:
                values[box] = float(text)
            except ValueError:
                problems[box] = "This must be a number."
                continue
            if not low <= values[box] <= high:
                problems[box] = f"This must be between {low} and {high}."
        if problems:
            found[str(number)] = problems
            continue
        stations[name] = [values["lon"], values["lat"]]
    if found:
        if errors is not None:
            errors["stations"] = found
        return None
    return stations


def check_area(form, cwd):
    """Check the area's boxes (see clean_area). Returns (area, errors): area
    {"subregions", "lon_lim", "lat_lim", "stations"} as analyze() takes them,
    each None, or empty for the stations, if not asked for; errors maps the
    boxes that cannot be used to the reason, as the validation options step
    does. A regions file may be relative to cwd."""
    from oceanval import recipes_gui

    errors = {}

    def text(name):
        if name == "subregions_file" and form[name]:
            return os.path.normpath(os.path.join(cwd, os.path.expanduser(form[name])))
        return form[name]

    area = {"subregions": None, "lon_lim": None, "lat_lim": None, "stations": {}}
    limits = recipes_gui.check_limits(text, errors)
    if limits is not None:
        area["lon_lim"], area["lat_lim"] = limits
    subregions = recipes_gui.check_subregions(text, errors)
    if subregions not in (None, "nwes", "global"):
        import oceanval

        try:
            oceanval._check_region_file(subregions)
        except ValueError as error:
            errors["subregions_file"] = str(error)
    area["subregions"] = subregions
    stations = check_stations(form["stations"], errors)
    if stations is not None:
        area["stations"] = stations
    return area, errors


# ---- the script ----


def check_missing(low, high):
    """The values to treat as missing, from the boxes for the value (or the
    start of a range) and the end of the range, as validate's window reads
    them: (as_missing, errors), as_missing None if both are empty, a float
    or [low, high]; errors {box: reason}."""
    from oceanval import recipes_gui

    low, high = str(low or "").strip(), str(high or "").strip()
    if not low and not high:
        return None, {}
    first, last = recipes_gui._number(low), recipes_gui._number(high)
    if not low:
        return None, {"missing_from": "Fill in the value, or the start of a range."}
    if first is None:
        return None, {"missing_from": "This must be a number."}
    if high and last is None:
        return None, {"missing_to": "This must be a number."}
    if high and last < first:
        return None, {"missing_to": "The end of the range must not be less than its start."}
    return (first if not high else [first, last]), {}


def check_cores(text):
    """The number of cores chosen: (cores, error). There is no default,
    so nothing chosen is an error, as is more than this machine has."""
    text = str(text if text is not None else "").strip()
    if not text:
        return None, "Say how many cores OceanVal may use."
    if not text.isdigit() or int(text) < 1:
        return None, "This must be a whole number, 1 or more."
    cpu_count = os.cpu_count()
    if cpu_count and int(text) > cpu_count:
        return None, f"This machine has {cpu_count} cores. Choose {cpu_count} or fewer."
    return int(text), None


# the answers to whether the report has code to reproduce its figures
FIGURE_CODE_CHOICES = {"yes": True, "no": False}


def check_figure_code(choice):
    """The answer to whether the report should have code to reproduce its
    figures, "yes" or "no": (figure_code, error). Not answering is "no"."""
    choice = str(choice or "").strip().lower() or "no"
    if choice not in FIGURE_CODE_CHOICES:
        return None, "Say whether the report should have code to reproduce its figures."
    return FIGURE_CODE_CHOICES[choice], None


def arguments(
    setup,
    placed,
    climatology,
    phenology,
    thickness,
    time_shift,
    long_term=None,
    seasonal=None,
    area=None,
    cores=None,
    as_missing=None,
    figure_code=None,
    transects=None,
    map_changes=None,
    times_index=None,
):
    """analyze()'s arguments, from the steps. times_index is where the window's
    check of the dates saved the files' times, if they are not to be read again. area is what check_area gave,
    with the stations (see check_stations), if a time series is asked for.
    transects is what check_transects gave, if they are asked for, and
    map_changes what check_map_changes gave."""
    used = set(climatology or {}) | set(phenology or {}) | set(long_term or {}) | set(seasonal or {})
    used |= set(map_changes or {})
    used |= set((transects or {}).get("rows", {}))
    out = {
        "sim_dir": setup["simdir"],
        "variables": {row["name"]: row["model_variable"] for row in placed if row["name"] in used},
        "climatology": climatology or None,
        "phenology": phenology or None,
    }
    # the time series are left out of a script that has none
    if long_term:
        out["long_term"] = long_term
    if seasonal:
        out["seasonal"] = seasonal
    if map_changes:
        out["map_changes"] = map_changes
    if transects:
        out["transects"] = {
            name: [{key: value for key, value in line.items() if value is not None} for line in lines]
            for name, lines in transects["rows"].items()
        }
    if (long_term or seasonal) and area:
        if area.get("stations"):
            out["stations"] = area["stations"]
        for name in ("subregions", "lon_lim", "lat_lim"):
            if area.get(name) is not None:
                out[name] = area[name]
    out.update(
        {
            "n_dirs_down": setup["ndown"],
            "exclude": setup.get("exclude"),
            "require": setup.get("require"),
            "thickness": thickness,
            "time_shift": time_shift or None,
            "out_dir": setup["out_dir"],
            "cores": cores,
        }
    )
    if as_missing is not None:
        out["as_missing"] = as_missing
    if figure_code is not None:
        out["figure_code"] = figure_code
    if times_index:
        out["times_index"] = times_index
    return out


def script_text(arguments, function="analyze", what="An OceanVal analysis"):
    """The script that runs the analysis, which the window writes and runs,
    and which can be run again from a terminal. function is the oceanval
    function it calls, and what says what the script is."""
    lines = [
        f"# {what}, written by the oceanval window on "
        f"{datetime.date.today().isoformat()}.",
        "# Run it again with: python <this file>",
        "import oceanval",
        "",
        f"oceanval.{function}(",
    ]
    for name, value in arguments.items():
        if isinstance(value, list) and value and all(isinstance(entry, dict) for entry in value):
            # a line for each simulation
            lines.append(f"    {name}=[")
            lines.extend(f"        {entry!r}," for entry in value)
            lines.append("    ],")
        elif name == "transects" and value:
            # a line for each transect
            lines.append("    transects={")
            for variable, entries in value.items():
                lines.append(f"        {variable!r}: [")
                lines.extend(f"            {line!r}," for line in entries)
                lines.append("        ],")
            lines.append("    },")
        elif isinstance(value, dict) and value:
            # a line for each variable
            lines.append(f"    {name}={{")
            lines.extend(f"        {key!r}: {entry!r}," for key, entry in value.items())
            lines.append("    },")
        else:
            lines.append(f"    {name}={value!r},")
    lines.append(")")
    return "\n".join(lines) + "\n"
