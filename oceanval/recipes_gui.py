"""The create_recipes window.

create_recipes shows what it identified in a page in your web browser: a
row for each observational variable, with the model variable found for it,
which you can change, and a tick-box for every gridded and point dataset
OceanVal has for it, each with its own years and, where its observations
are resolved in depth, a Vertical tick-box. The page is served by a small
server that only runs while create_recipes waits for you, only listens on
127.0.0.1, and only answers requests carrying the random token in the link
it prints. The page loads nothing from anywhere else, so it works offline.
Run from the oceanval command (see oceanval.app), the window is one of its
pages instead (see hosted_by).
"""

import base64
import contextlib
import copy
import getpass
import http.server
import importlib.resources
import json
import math
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import urllib.parse
import webbrowser

from oceanval.create_recipes import (
    USER_REGION,
    _WOA23_PERIODS,
    _has_vertical_option,
    _is_climatology,
    _model_variable_names,
    _unknown_variables,
    climatology_years,
    default_selection,
    gridded_catalogue,
    matchup_defaults,
    point_catalogue,
    recipe_variables,
    validate_defaults,
)
from oceanval import data_store, own_data, time_res, user_recipes, user_settings

# how each recipe's dataset is named in the window
DATASET_LABELS = {
    "cobe2": "COBE2",
    "glodap": "GLODAP",
    "ices": "ICES",
    "nsbc": "NSBC",
    "occci": "OC-CCI",
    "woa23": "WOA23",
}

# the tag each of the catalogue's regions is shown with
REGION_TAGS = {
    "Global": "Global",
    "Northwest European Shelf": "NWES",
    # the recipes the user registered (see oceanval.user_recipes)
    USER_REGION: "Yours",
}

# ICES covers the shelf, though its catalogue entries carry no region
POINT_REGION = "Northwest European Shelf"


# what the "Match observations by" dropdowns offer, as (point_time_res, label,
# hint)
POINT_TIME_RES_OPTIONS = time_res.POINT_TIME_RES_OPTIONS
_TIME_RES = {",".join(value): list(value) for value, _, _ in POINT_TIME_RES_OPTIONS}

_LIMITS = ("lon_min", "lon_max", "lat_min", "lat_max")

ERSEM_VARIABLES = frozenset(
    importlib.resources.files("oceanval")
    .joinpath("data/nemo_ersem_variables.txt")
    .read_text(encoding="utf-8")
    .splitlines()
)


def is_likely_nemo_ersem(variables):
    """Whether more than 20 supplied model-variable names match the stored
    NEMO-ERSEM signature."""
    return len(ERSEM_VARIABLES.intersection(variables)) > 20

# why the oceanval app's window cannot carry on without a thickness
THICKNESS_NEEDED = (
    "A dataset is set to Vertical, so a thickness is needed: z_level, a cell "
    "thickness variable or a file."
)


def default_settings(start, end):
    """What the Global settings start as: start and end as create_recipes was
    given them, and matchup's and validate's own defaults for the rest."""
    settings = dict(matchup_defaults(), **validate_defaults())
    settings.update(start=start, end=end)
    # matchup's exclude default is a list shared between calls
    return copy.deepcopy(settings)


def default_form(start, end, exclude=None, require=None):
    """The Global settings as the page's boxes show them to start with:
    as default_settings, with any file filters create_recipes was given."""
    settings = default_settings(start, end)
    if exclude is not None:
        settings["exclude"] = list(exclude)
    if require is not None:
        settings["require"] = list(require)

    def limit(name, index):
        value = settings[name]
        return "" if value is None else str(value[index])

    missing = settings["as_missing"]
    if missing is not None and not isinstance(missing, list):
        missing = [missing]
    return {
        "start": str(start),
        "end": str(end),
        # the years of the point datasets, which the page fills each in with;
        # matchup's start and end are worked out from every dataset's
        "point_start": "" if start is None else str(start),
        "point_end": "" if end is None else str(end),
        "clim_start": "",
        "clim_end": "",
        # what FVCOM output is regridded to, which only an FVCOM simulation is asked
        "fvcom_res": str(settings["fvcom_res"]),
        "fvcom_min_depth": str(settings["fvcom_min_depth"]),
        "fvcom_max_depth": str(settings["fvcom_max_depth"]),
        "fvcom_dz": str(settings["fvcom_dz"]),
        "lon_min": limit("lon_lim", 0),
        "lon_max": limit("lon_lim", 1),
        "lat_min": limit("lat_lim", 0),
        "lat_max": limit("lat_lim", 1),
        "out_dir": settings["out_dir"] or "",
        "overwrite": settings["overwrite"],
        "thickness": settings["thickness"] or "",
        "missing_from": "" if missing is None else str(missing[0]),
        "missing_to": "" if missing is None or len(missing) < 2 else str(missing[1]),
        "point_time_res": ",".join(settings["point_time_res"]),
        "cores": str(settings["cores"]),
        "ask": settings["ask"],
        "exclude": " ".join(settings["exclude"]),
        "require": " ".join(settings["require"] or []),
        "pdf": settings["pdf"],
        "word": settings["word"],
        "concise": settings["concise"],
        "subregions": settings["subregions"] or "",
        # the path typed when the regional summaries come from a file
        "subregions_file": "",
    }


def _number(text):
    """A finite number typed into a box, or None if it is not one."""
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _strings(text):
    """The words in a box, separated by spaces."""
    return text.split()


def check_settings(form, available, fvcom=False):
    """Turn the page's Global settings into matchup() and validate()
    arguments.

    Returns (settings, errors), where errors maps each box whose contents
    cannot be used to the reason. Paths can only be checked here, not in the
    page.
    """
    form = form if isinstance(form, dict) else {}

    def text(name):
        value = form.get(name)
        return "" if value is None else str(value).strip()

    settings = default_settings(None, None)
    errors = {}

    # with nothing ticked the window has no years to give: matchup's are then
    # left for the script to say
    no_years = not text("start") and not text("end")
    for name in ("start", "end"):
        if no_years:
            settings[name] = None
            continue
        try:
            settings[name] = int(text(name))
        except ValueError:
            errors[name] = f"{'First' if name == 'start' else 'Last'} must be a year, e.g. 2011."
    if not errors and not no_years and settings["end"] < settings["start"]:
        errors["end"] = "Last year must not be before the first year."

    # the years climatology datasets average the simulation over, if not the
    # years being validated, and the WOA23 years asked for when those do not
    # sit inside one decade (not matchup() arguments, so not in the footer).
    # The window fills each climatology's own years in with them, and works
    # out start and end from every dataset's, so they need not overlap those
    clim_years = _check_pair(text, errors, "clim_start", "clim_end", "climatologies")
    if clim_years:
        settings["clim_years"] = clim_years
    woa_years = _check_pair(text, errors, "woa23_start", "woa23_end", "WOA23")
    if woa_years:
        settings["woa_years"] = woa_years
    if woa_years and not _in_one_period(woa_years):
        errors["woa23_end"] = (
            "These years must sit inside one WOA23 decade, e.g. 2005–2014."
        )

    limits = check_limits(text, errors)
    if limits is not None:
        settings["lon_lim"], settings["lat_lim"] = limits

    if fvcom:
        _check_fvcom(text, errors, settings)

    settings["out_dir"] = text("out_dir")
    if settings["out_dir"] and os.path.isfile(os.path.expanduser(settings["out_dir"])):
        errors["out_dir"] = "This is a file, not a directory."
    overwrite = form.get("overwrite", True)
    # a list of the matchups to overwrite; the oceanval window hides the tick
    # and chooses them in its output step (see oceanval.app.check_output)
    settings["overwrite"] = (
        [entry for entry in overwrite if isinstance(entry, str)] or False
        if isinstance(overwrite, list)
        else bool(overwrite)
    )

    thickness = text("thickness")
    # matchup regrids FVCOM output onto z-levels, so it needs no thickness
    if thickness and not fvcom:
        if re.fullmatch(r"z[ _-]level", thickness):
            settings["thickness"] = "z_level"
        elif thickness in available or os.path.exists(os.path.expanduser(thickness)):
            settings["thickness"] = thickness
        else:
            errors["thickness"] = (
                "Not a variable in the simulation output, or a file that exists."
            )

    missing = check_missing(text, errors)
    if missing is not None:
        settings["as_missing"] = missing

    if text("point_time_res") in _TIME_RES:
        settings["point_time_res"] = list(_TIME_RES[text("point_time_res")])
    else:
        errors["point_time_res"] = "Choose one of the options."

    try:
        settings["cores"] = int(text("cores"))
        if settings["cores"] < 1:
            raise ValueError
    except ValueError:
        errors["cores"] = (
            "Choose the number of cores." if not text("cores")
            else "Cores must be a whole number, 1 or more."
        )
    cpu_count = os.cpu_count()
    if "cores" not in errors and cpu_count and settings["cores"] > cpu_count:
        errors["cores"] = f"This machine has {cpu_count} cores. Choose {cpu_count} or fewer."
    settings["ask"] = bool(form.get("ask", True))

    settings["exclude"] = _strings(text("exclude"))
    settings["require"] = _strings(text("require")) or None

    settings["pdf"] = bool(form.get("pdf"))
    settings["word"] = bool(form.get("word"))
    concise = form.get("concise", True)
    if isinstance(concise, bool):
        settings["concise"] = concise
    elif str(concise).lower() in ("true", "false"):
        settings["concise"] = str(concise).lower() == "true"
    else:
        errors["concise"] = "Choose concise or detailed."
    settings["subregions"] = check_subregions(text, errors)
    return settings, errors


# the boxes of what FVCOM output is regridded to: the matchup() argument, the
# name the page uses for it, and whether it has to be more than 0 (a depth can be 0)
_FVCOM_BOXES = (
    ("fvcom_res", "The resolution", True),
    ("fvcom_min_depth", "The minimum depth", False),
    ("fvcom_max_depth", "The maximum depth", True),
    ("fvcom_dz", "The spacing", True),
)


def _check_fvcom(text, errors, settings):
    """The FVCOM resolution and depths, put in settings as numbers. Whole
    numbers are kept whole, so the script reads fvcom_max_depth=200."""
    found = {}
    for name, label, positive in _FVCOM_BOXES:
        value = _number(text(name)) if text(name) else None
        if not text(name):
            errors[name] = f"{label} is needed."
        elif value is None:
            errors[name] = f"{label} must be a number."
        elif value <= 0 if positive else value < 0:
            errors[name] = f"{label} must be {'more than' if positive else ''} 0{'' if positive else ' or more'}."
        else:
            found[name] = int(value) if value == int(value) else value
    if "fvcom_min_depth" in found and "fvcom_max_depth" in found:
        if found["fvcom_max_depth"] <= found["fvcom_min_depth"]:
            errors["fvcom_max_depth"] = "The maximum depth must be more than the minimum."
    if not errors.keys() & {name for name, _, _ in _FVCOM_BOXES}:
        settings.update(found)


def _in_one_period(years):
    """Whether years sit inside one of the decades WOA23 publishes."""
    return any(first <= years[0] and years[1] <= last for first, last in _WOA23_PERIODS)


def _check_pair(text, errors, first_name, last_name, what):
    """A first and last year from two boxes: (first, last), or None if both
    are empty or errors says what is wrong with them."""
    given = [name for name in (first_name, last_name) if text(name)]
    if not given:
        return None
    years = []
    for name in (first_name, last_name):
        try:
            years.append(int(text(name)))
        except ValueError:
            errors[name] = (
                f"Give both years for {what}, or leave them both empty."
                if not text(name)
                else "Years must be whole numbers."
            )
    if len(years) == 2 and years[1] < years[0]:
        errors[last_name] = "Last year must not be before the first year."
    return tuple(years) if len(years) == 2 and last_name not in errors else None


def check_limits(text, errors):
    """The lon_lim and lat_lim in the lon_min, lon_max, lat_min and lat_max
    boxes, as ([lon_min, lon_max], [lat_min, lat_max]).

    text(name) is what a box holds. None if the boxes are empty, or cannot
    be used, in which case errors says why.
    """
    given = [name for name in _LIMITS if text(name)]
    limits = {name: _number(text(name)) for name in given}
    for name in given:
        if limits[name] is None:
            errors[name] = "Limits must be numbers."
    if given and len(given) < len(_LIMITS):
        for name in _LIMITS:
            if name not in given:
                errors[name] = "Fill in all four limits, or leave them all empty."
    elif given and not any(name in errors for name in _LIMITS):
        for axis, low, high in (("lon", -180, 360), ("lat", -90, 90)):
            first, last = limits[f"{axis}_min"], limits[f"{axis}_max"]
            label = "Longitude" if axis == "lon" else "Latitude"
            if first < low or last > high:
                errors[f"{axis}_min"] = f"{label} must be between {low} and {high}."
            elif first >= last:
                errors[f"{axis}_max"] = (
                    f"The maximum {label.lower()} must be more than the minimum."
                )
        if not any(name in errors for name in _LIMITS):
            return (
                [limits["lon_min"], limits["lon_max"]],
                [limits["lat_min"], limits["lat_max"]],
            )
    return None


def check_missing(text, errors):
    """matchup's as_missing from the missing_from and missing_to boxes: one
    value, or [first, last] for everything in a range.

    text(name) is what a box holds. None if the boxes are empty, or cannot
    be used, in which case errors says why.
    """
    low, high = text("missing_from"), text("missing_to")
    if not (low or high):
        return None
    first, last = _number(low), _number(high)
    if not low:
        errors["missing_from"] = "Fill in the value, or the start of a range."
    elif first is None:
        errors["missing_from"] = "This must be a number."
    elif high and last is None:
        errors["missing_to"] = "This must be a number."
    elif high and last < first:
        errors["missing_to"] = "The end of the range must not be less than its start."
    else:
        return first if not high else [first, last]
    return None


def check_subregions(text, errors):
    """The regional summaries chosen in the subregions and subregions_file
    boxes: None, "nwes", "global" or the path to a .nc file.

    text(name) is what a box holds. None as well if they cannot be used, in
    which case errors says why.
    """
    choice = text("subregions")
    if choice == "file":
        path = text("subregions_file")
        if not path:
            errors["subregions_file"] = "Enter the path to a .nc file."
        elif not path.endswith(".nc"):
            errors["subregions_file"] = "This must be a .nc file."
        elif not os.path.isfile(os.path.expanduser(path)):
            errors["subregions_file"] = "There is no file at this path."
        else:
            return path
    elif choice in ("", "nwes", "global"):
        return choice or None
    else:
        errors["subregions"] = "Choose one of the options."
    return None


def _check_years(form, years):
    """The years typed for one dataset, as ({"start", "end"}, errors).

    years are the first and last years being matched up, which the dataset's
    own years must overlap, or None if they are not known.
    """
    options = {"start": None, "end": None}
    errors = {}
    for name in ("start", "end"):
        value = str(form.get(name) or "").strip()
        if value:
            try:
                options[name] = int(value)
            except ValueError:
                errors[name] = "Years must be whole numbers."

    start, end = options["start"], options["end"]
    if errors:
        pass
    elif start is not None and end is not None and start > end:
        errors["end"] = "To must not be before From."
    elif years is not None and start is not None and start > years[1]:
        errors["start"] = f"These years miss the {years[0]}–{years[1]} being matched up."
    elif years is not None and end is not None and end < years[0]:
        errors["end"] = f"These years miss the {years[0]}–{years[1]} being matched up."
    return options, errors


def check_point_options(form, years):
    """Turn one point dataset's options into add_point_comparison arguments.

    years are the first and last years being matched up, which the dataset's
    own years must overlap, or None if they are not known. Returns (options,
    errors).
    """
    form = form if isinstance(form, dict) else {}
    years_chosen, errors = _check_years(form, years)
    options = dict(years_chosen, point_time_res=None, vertical=None)
    # unticked is surface-only, which is add_point_comparison's own default
    if form.get("vertical"):
        options["vertical"] = True
    choice = str(form.get("point_time_res") or "").strip()
    if choice:
        if choice in _TIME_RES:
            options["point_time_res"] = list(_TIME_RES[choice])
        else:
            errors["point_time_res"] = "Choose one of the options."
    return options, errors


def check_gridded_options(
    form,
    years,
    vertical_option=False,
    decadal=False,
    climatology=False,
    clim_years=None,
    woa_years=None,
):
    """Turn one gridded dataset's options into add_gridded_comparison
    arguments.

    years are as for check_point_options. vertical_option is whether the
    dataset can be validated through the full water column at all, and
    decadal whether it is one WOA23 publishes per decade, whose years must
    then both be given and sit inside one decade. climatology is whether it
    is a climatology, which is given clim_years, the years to average the
    simulation over, if it has none of its own. A decadal one whose years
    (clim_years, else years) sit inside no decade is given woa_years, the
    ones asked for in the window, or is refused. Returns (options, errors).
    """
    form = form if isinstance(form, dict) else {}
    options, errors = _check_years(form, years)
    # the page only offers Vertical where the recipe takes it
    options["vertical"] = True if vertical_option and form.get("vertical") else None

    start, end = options["start"], options["end"]
    if errors:
        return options, errors
    if start is not None or end is not None:
        if decadal:
            if start is None:
                errors["start"] = "WOA23 publishes this per decade, so give both years."
            if end is None:
                errors["end"] = "WOA23 publishes this per decade, so give both years."
            if start is not None and end is not None and not _in_one_period((start, end)):
                errors["end"] = (
                    "These years must sit inside one WOA23 decade, e.g. 2005–2014."
                )
        return options, errors
    if not climatology:
        return options, errors

    # none of its own years
    chosen = climatology_years(decadal, years, clim_years, woa_years)
    target = clim_years or years
    if chosen:
        options["start"], options["end"] = chosen
    elif decadal and target is not None and not _in_one_period(target):
        errors["start"] = errors["end"] = (
            "WOA23 publishes this per decade, so give both years."
        )
    return options, errors


# the boxes the window has for one of the user's own datasets
OWN_BOXES = {
    "point": ("point_time_res", "start", "end", "vertical"),
    "gridded": ("start", "end", "vertical"),
}


def own_options(kind, arguments):
    """One of the user's own datasets' options, from the arguments of its
    call, as the window's boxes for them show them."""
    options = {
        name: "" if arguments.get(name) is None else str(arguments[name])
        for name in ("start", "end")
    }
    options["vertical"] = bool(arguments.get("vertical"))
    if kind == "point":
        options["point_time_res"] = ",".join(arguments.get("point_time_res") or [])
    return options


def check_own_options(kind, arguments, form, years):
    """One of the user's own datasets (the arguments of its call) with the
    options the window sent for it (form), checked as a recipe's are.
    Returns (arguments, errors). A time resolution the window does not
    offer, which the own data step can give, is kept while it is unchanged."""
    form = form if isinstance(form, dict) else {}
    if kind == "point":
        choice = str(form.get("point_time_res") or "").strip()
        kept = choice not in _TIME_RES and choice == own_options(kind, arguments)["point_time_res"]
        options, errors = check_point_options(
            dict(form, point_time_res="" if kept else choice), years
        )
        if kept and choice:
            options["point_time_res"] = list(arguments["point_time_res"])
    else:
        # the calls take Vertical for any data
        options, errors = check_gridded_options(form, years, vertical_option=True)
    # an option left as the call's default is not given, as in the own data step
    changed = {name: value for name, value in arguments.items() if name not in options}
    changed.update((name, value) for name, value in options.items() if value is not None)
    return changed, errors


def _title(variable, cwd=None, own=()):
    """How a variable is named in the window, e.g. "pH" or "KD490".

    Taken from the notes its first recipe opens with, "<title> - <source>",
    or the title of a recipe of the user's, for a variable only they have.
    A variable only the user's own data in the matchup is for (own, the
    arguments of its calls) is named as add_point_comparison and
    add_gridded_comparison name it.
    """
    entry = next(
        (
            entry
            for entry in gridded_catalogue(cwd) + point_catalogue(cwd)
            if entry["variable"] == variable
        ),
        None,
    )
    if entry is not None:
        return entry.get("title") or entry["notes"][0].split(" - ")[0]
    labels = user_recipes.labels(variable, cwd)
    if labels is not None:
        return labels[2]
    for arguments in own:
        if arguments.get("short_title"):
            return str(arguments["short_title"])
    return variable.title()


def _description(entry):
    """A dataset's source and details, for its tooltip.

    The notes are written for the script, so the recipe line is dropped, as
    are pointers to the notes that follow them there.
    """
    notes = [line for line in entry["notes"] if not line.startswith("recipe:")]
    source = notes[0].split(" - ", 1)[-1]
    details = " ".join(notes[1:])
    details = re.sub(r"\s*\(see below\)", "", details)
    details = re.sub(r"\s*— see the WOA23 note below[^.]*", "", details)
    return source, details


def recipe_rows(mapping, domain, cwd=None, replaced=(), own_data=None):
    """One row of the window per observational variable, alphabetically.

    A dataset starts ticked where the script would register it were nothing
    changed (default_selection), so writing the script straight away gives
    the one gui=False would. "default" is whether it would be ticked if the
    variable had been identified, which the page ticks it by when you fill
    in a variable that was not. The recipes the user registered for the
    directory cwd (see oceanval.user_recipes) are in the rows too, and are
    ticked wherever there is a model variable for them, except those in
    replaced, (variable, recipe) pairs that the user's own data in the
    matchup takes the place of (see create_recipes.replaced_recipes), which
    are left out, as is a row left with no datasets.

    That data, own_data ({"point": [...], "gridded": [...]}, the arguments
    of each of its calls), is matched up whatever the window says, so is
    shown in its variable's row as "own", ticked for good, with the options
    a recipe's dataset has ("opts", see own_options): a variable only it is
    for has a row with no datasets but it.
    """
    replaced = {tuple(pair) for pair in replaced}
    own = {}
    for kind in ("gridded", "point"):
        for index, arguments in enumerate((own_data or {}).get(kind) or []):
            variable = str(arguments.get("name", "")).lower()
            source = str(arguments.get("source", ""))
            own.setdefault(variable, []).append(
                {
                    "kind": kind,
                    # where it is in own_data, for the page to send its options back
                    "index": index,
                    "label": source,
                    "model_variable": str(arguments.get("model_variable") or ""),
                    "path": str(arguments.get("obs_path") or ""),
                    # saved as a recipe too, which it takes the place of
                    "saved": (variable, user_recipes.key_of(source)) in replaced,
                    "opts": own_options(kind, arguments),
                    # given the window's years for climatologies, as a recipe's
                    "climatology": kind == "gridded" and bool(arguments.get("climatology")),
                    "arguments": arguments,
                }
            )
    variables = recipe_variables(cwd)
    variables += tuple(variable for variable in own if variable not in variables)
    ticked = default_selection(mapping, domain, cwd)
    defaults = default_selection({variable: variable for variable in variables}, domain, cwd)

    def shown(entry):
        return not (entry.get("user") and (entry["variable"], entry["recipe"]) in replaced)

    def dataset(entry, region):
        key = (entry["variable"], entry["recipe"])
        source, details = _description(entry)
        return {
            "recipe": entry["recipe"],
            "label": entry.get("label") or DATASET_LABELS[entry["recipe"]],
            "region": REGION_TAGS[region],
            "region_name": region,
            "user": bool(entry.get("user")),
            "path": entry.get("path"),
            "source": source,
            "details": details,
            "default": key in defaults,
            "ticked": key in ticked,
            # whether the page offers Vertical, and makes the years sit
            # inside one WOA23 decade
            "vertical_option": _has_vertical_option(entry),
            "decadal": bool(entry.get("decadal")),
            # averaged over the simulation's years: given the window's
            # climatology years
            "climatology": _is_climatology(entry),
            # the years its observations cover, and its web page, for the i beside it
            "coverage": entry.get("coverage"),
        }

    rows = [
        {
            "variable": variable,
            "title": _title(
                variable, cwd, [item["arguments"] for item in own.get(variable, [])]
            ),
            "model_variable": mapping.get(variable) or "",
            "gridded": [
                dataset(entry, entry["region"])
                for entry in gridded_catalogue(cwd)
                if entry["variable"] == variable and shown(entry)
            ],
            "point": [
                dataset(entry, entry.get("region", POINT_REGION))
                for entry in point_catalogue(cwd)
                if entry["variable"] == variable and shown(entry)
            ],
            "own": [
                {key: value for key, value in item.items() if key != "arguments"}
                for item in own.get(variable, [])
            ],
        }
        for variable in sorted(variables)
    ]
    for row in rows:
        # the name in the Environmental variable column (the title stays the report's)
        row["display"] = user_recipes.display_name(row["variable"], row["title"])
    return [row for row in rows if row["gridded"] or row["point"] or row["own"]]


def render_page(template, state):
    """One of the pages in oceanval/data, with state for its script.

    The stylesheet and scripts every page shares are inlined, so that the
    page loads nothing from anywhere else. The page is in the colours of the
    window's settings from the start, rather than changing once it has loaded.
    """
    data = importlib.resources.files("oceanval").joinpath("data")

    def read(name):
        return data.joinpath(name).read_text(encoding="utf-8")

    # the state sits in a <script> element, which a "</script>" in, say, a
    # variable name would otherwise close
    state = (
        json.dumps(state)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    def picture(name, kind):
        encoded = base64.b64encode(data.joinpath(name).read_bytes()).decode("ascii")
        return f"data:{kind};base64,{encoded}"

    html = read(template)
    return (
        html
        .replace("/*__OCEANVAL_GUI_CSS__*/", read("gui.css"))
        .replace(
            "/*__OCEANVAL_GUI_JS__*/",
            # the Info panel's logo, inlined as the page loads nothing from elsewhere
            read("gui.js")
            .replace("__OCEANVAL_PML_LOGO__", picture("pml_logo.jpg", "image/jpeg")),
        )
        # last, so nothing inlined above can be mistaken for it
        .replace("__OCEANVAL_STATE__", state)
    )


def _unknown_message(unknown):
    if "" in unknown:
        return 'Remove the empty name beside "+".'
    verb = "is" if len(unknown) == 1 else "are"
    return f"{', '.join(unknown)} {verb} not in the simulation output."


# set by hosted_by: the oceanval app, which shows the create_recipes window
# as one of its own pages rather than starting a server of its own
_host = None


@contextlib.contextmanager
def hosted_by(host):
    """Show the create_recipes window inside the block as one of host's own
    pages, with host.show_recipes(page), which returns what page.wait()
    does. See oceanval.app."""
    global _host
    previous, _host = _host, host
    try:
        yield
    finally:
        _host = previous


class _Server(http.server.ThreadingHTTPServer):
    # a page still open in the browser must not hold up closing the server
    block_on_close = False


class _Handler(http.server.BaseHTTPRequestHandler):
    page = None  # set on the subclass each RecipePage makes

    def log_message(self, format, *args):
        # the terminal belongs to the user, and every click would print
        pass

    def _reply(self, status, body, content_type):
        # text, or the bytes of a file (as the oceanval window serves its reports)
        data = body.encode("utf-8") if isinstance(body, str) else body
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            # the page went before the reply came, e.g. on to another step
            pass

    def _reply_json(self, status, payload):
        self._reply(status, json.dumps(payload), "application/json")

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        token = urllib.parse.parse_qs(url.query).get("token", [""])[0]
        if url.path != "/":
            self._reply(404, "Not found", "text/plain; charset=utf-8")
        elif not self.page.authorised(token):
            self._reply(
                403,
                "This is not the link create_recipes printed.",
                "text/plain; charset=utf-8",
            )
        else:
            self._reply(200, self.page.html(), "text/html; charset=utf-8")

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self.page.authorised(self.headers.get("X-OceanVal-Token", "")):
            self._reply_json(403, {"ok": False, "error": "Forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._reply_json(
                400, {"ok": False, "error": "The request could not be read."}
            )
            return
        if path == "/write":
            status, reply = self.page.submit(payload)
            try:
                self._reply_json(status, reply)
            finally:
                # only now, so the reply is sent before create_recipes returns
                if reply["ok"]:
                    self.page.finish()
        elif path == "/cancel":
            self.page.cancel(payload)
            try:
                self._reply_json(200, {"ok": True})
            finally:
                self.page.finish()
        elif path == "/settings":
            # the Preferences panel, when the window is not one of the app's pages
            try:
                saved = user_settings.save(
                    payload.get("settings") if isinstance(payload, dict) else None
                )
            except (OSError, ValueError) as error:
                self._reply_json(400, {"ok": False, "error": f"{error}"})
            else:
                self._reply_json(
                    200, {"ok": True, "settings": dict(saved), "data_store": data_store.panel(saved)}
                )
        elif path == "/data_store_check":
            # looks at the saved data store, and changes nothing
            self._reply_json(*data_store.check_reply(user_settings.load()))
        else:
            self._reply_json(404, {"ok": False, "error": "Not found"})


class RecipePage:
    """The server behind one create_recipes window.

    start() serves the page and returns its link, wait() blocks until the
    page is submitted or cancelled, and close() stops serving it. write is
    called with the (mapping, selection, settings, point_options,
    gridded_options) the page submits, from the server's thread, so that the
    page can say whether the script was written.
    """

    def __init__(self, rows, available, context, write):
        self.rows = rows
        self.available = set(available)
        self.context = context
        self.write = write
        self.form = default_form(
            context.get("start"),
            context.get("end"),
            context.get("exclude"),
            context.get("require"),
        )
        self.token = secrets.token_urlsafe(24)
        self.url = None
        self.result = None
        # what the page last sent, to write the script or with Back, which
        # the oceanval app shows the window again with
        self.sent = None
        # set once the script is written or the window cancelled, after
        # which the page can do nothing more, unless it is reopened
        self._used = False
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._server = None

    def authorised(self, token):
        return secrets.compare_digest(
            token.encode("utf-8"), self.token.encode("utf-8")
        )

    def _own(self):
        """The user's own data in the matchup (see recipe_rows), whose
        entries the window's options for them are written into."""
        return self.context.get("own_data") or {}

    def _rows_now(self):
        """The rows, with the user's own data's options as they are now:
        the window may have changed them, before it is shown again."""
        own, rows = self._own(), []
        for row in self.rows:
            items = []
            for item in row["own"]:
                with contextlib.suppress(KeyError, IndexError, TypeError):
                    arguments = own[item["kind"]][item["index"]]
                    item = dict(item, opts=own_options(item["kind"], arguments))
                items.append(item)
            rows.append(dict(row, own=items))
        return rows

    def _own_changes(self, sent, years):
        """The user's own datasets whose options the page changed, from what
        it sent for them (sent: {kind: {index: options}}), as
        ({(kind, index): arguments}, errors): errors are {kind: {index:
        {box: reason}}}. Those it sent nothing for are left as they are."""
        sent = sent if isinstance(sent, dict) else {}
        changes, errors = {}, {}
        for kind, entries in self._own().items():
            forms = sent.get(kind) if isinstance(sent.get(kind), dict) else {}
            for index, arguments in enumerate(entries or []):
                if str(index) not in forms:
                    continue
                changed, problems = check_own_options(kind, arguments, forms[str(index)], years)
                if problems:
                    errors.setdefault(kind, {})[str(index)] = problems
                elif changed != arguments:
                    changes[kind, index] = changed
        return changes, errors

    def _refused(self, changes):
        """What the calls refuse the changed datasets with, as for
        _own_changes' errors, as the own data step checks them: e.g.
        Vertical, for csv files with no depth column."""
        own = self._own()
        entries = [
            ((kind, index), changes.get((kind, index), arguments))
            for kind in own_data.KINDS
            for index, arguments in enumerate(own.get(kind) or [])
        ]
        errors = {}
        for (kind, index), arguments in entries:
            if (kind, index) not in changes:
                continue
            existing = [(where[0], other) for where, other in entries if where != (kind, index)]
            reason = own_data.refusal(kind, arguments, existing)
            if reason is not None:
                box = own_data._blame(kind, reason)
                errors.setdefault(kind, {})[str(index)] = {
                    box if box in OWN_BOXES[kind] else "": reason
                }
        return errors

    def _keep_own(self, changes):
        """Write the changed options into the user's own data, whose lists
        the script is written from (and, in the oceanval app, the steps
        after this one)."""
        own = self._own()
        for (kind, index), arguments in changes.items():
            own[kind][index] = arguments

    def html(self):
        # app imports this module, so it is imported only now
        import oceanval
        from oceanval import app

        state = {
            "rows": self._rows_now(),
            "available": sorted(self.available, key=lambda v: (v.lower(), v)),
            # each model variable's long_name, for the table under its box
            "long_names": self.context.get("long_names") or {},
            "nemo_ersem": bool(self.context.get("app"))
            and is_likely_nemo_ersem(self.available),
            "context": self.context,
            "token": self.token,
            "settings": self.form,
            "time_res_options": [
                {"value": ",".join(value), "label": label, "hint": hint}
                for value, label, hint in POINT_TIME_RES_OPTIONS
            ],
            "woa23_periods": _WOA23_PERIODS,
            "cpu_count": os.cpu_count(),
            # for the header's Preferences and About panels, as the app has them
            "oceanval_version": oceanval.__version__,
            "datasets": app.BUILTIN_DATASETS,
            "user_settings": {
                "values": user_settings.load(),
                "path": user_settings.path(),
                "data_store": data_store.panel(user_settings.load()),
            },
        }
        return render_page("create_recipes_gui.html", state)

    def submit(self, payload):
        """Check what the page sent, and write the script if it is sound.

        Returns the HTTP status and the reply for the page. A model variable
        that is not in the output, and settings that cannot be used, are sent
        back to be fixed, as the terminal prompt asks again for a variable. A
        request without settings keeps the defaults.
        """
        with self._lock:
            if self._used:
                return 409, {
                    "ok": False,
                    "error": "This window has already been used. Run "
                    "create_recipes again to start over.",
                }
            if isinstance(payload, dict):
                self.sent = payload
            rows = {row["variable"]: row for row in self.rows}
            sent = payload.get("rows") if isinstance(payload, dict) else None
            if not isinstance(sent, list):
                return 400, {"ok": False, "error": "The request held no rows."}

            settings, setting_errors = check_settings(
                payload.get("settings") or self.form,
                self.available,
                bool(self.context.get("fvcom")),
            )
            years = None
            if "start" not in setting_errors and "end" not in setting_errors:
                years = (
                    None if settings["start"] is None
                    else (settings["start"], settings["end"])
                )

            mapping, selection, errors = {}, set(), {}
            point_options, point_errors = {}, {}
            gridded_options, gridded_errors = {}, {}
            own_changes, own_errors = self._own_changes(payload.get("own"), years)
            for item in sent:
                if not isinstance(item, dict) or item.get("variable") not in rows:
                    return 400, {
                        "ok": False,
                        "error": "The request named a variable OceanVal has no "
                        "recipe for.",
                    }
                variable = item["variable"]
                answer = str(item.get("model_variable") or "").strip()
                if not answer or not (rows[variable]["gridded"] or rows[variable]["point"]):
                    # nothing to match up, or only the user's own data,
                    # which has its own model variable
                    continue
                unknown = _unknown_variables(answer, self.available)
                if unknown:
                    errors[variable] = _unknown_message(unknown)
                    continue
                mapping[variable] = "+".join(_model_variable_names(answer))

                recipes = {
                    dataset["recipe"]
                    for dataset in rows[variable]["gridded"] + rows[variable]["point"]
                }
                chosen = item.get("selected") or []
                if not isinstance(chosen, list) or not all(
                    recipe in recipes for recipe in chosen
                ):
                    return 400, {
                        "ok": False,
                        "error": f"The request ticked a dataset {variable} does "
                        "not have.",
                    }
                selection.update((variable, recipe) for recipe in chosen)

                # only ticked datasets show their options in the page
                for kind, chosen_options, kind_errors in (
                    ("gridded", gridded_options, gridded_errors),
                    ("point", point_options, point_errors),
                ):
                    forms = item.get(kind) if isinstance(item.get(kind), dict) else {}
                    for dataset in rows[variable][kind]:
                        recipe = dataset["recipe"]
                        if (variable, recipe) not in selection or recipe not in forms:
                            continue
                        if kind == "point":
                            options, problems = check_point_options(
                                forms[recipe], years
                            )
                        else:
                            options, problems = check_gridded_options(
                                forms[recipe],
                                years,
                                dataset["vertical_option"],
                                dataset["decadal"],
                                dataset["climatology"],
                                settings.get("clim_years"),
                                settings.get("woa_years"),
                            )
                        if problems:
                            kind_errors.setdefault(variable, {})[recipe] = problems
                        elif any(value is not None for value in options.values()):
                            chosen_options[(variable, recipe)] = options

            app = self.context.get("app")
            if app:
                # the app answers matchup's questions in its window
                settings["ask"] = True
                own = [
                    own_changes.get((kind, index), arguments)
                    for kind, entries in self._own().items()
                    for index, arguments in enumerate(entries or [])
                ]
                own_vertical = (
                    any(arguments.get("vertical") for arguments in own)
                    if own
                    else app.get("own_vertical")
                )
                vertical = own_vertical or any(
                    options.get("vertical")
                    for options in [*gridded_options.values(), *point_options.values()]
                )
                if (
                    vertical
                    and not self.context.get("fvcom")
                    and settings["thickness"] is None
                    and "thickness" not in setting_errors
                ):
                    setting_errors["thickness"] = THICKNESS_NEEDED

            if not (errors or setting_errors or point_errors or gridded_errors or own_errors):
                # only once all else is right, as it opens the data
                own_errors = self._refused(own_changes)
            if errors or setting_errors or point_errors or gridded_errors or own_errors:
                return 400, {
                    "ok": False,
                    "errors": errors,
                    "setting_errors": setting_errors,
                    "point_errors": point_errors,
                    "gridded_errors": gridded_errors,
                    "own_errors": own_errors,
                }
            self._keep_own(own_changes)
            try:
                out = self.write(
                    mapping, selection, settings, point_options, gridded_options
                )
            except Exception as error:
                return 500, {
                    "ok": False,
                    "error": f"The script could not be written: {error}",
                }
            self.result = (
                mapping, selection, settings, point_options, gridded_options
            )
            self._used = True
            return 200, {"ok": True, "out": out}

    def cancel(self, sent=None):
        """Cancel the window, with what the page held when it was left, if
        it sent it."""
        with self._lock:
            if not self._used:
                if isinstance(sent, dict):
                    self.sent = sent
                    if self.context.get("app"):
                        # kept for Back, as the rest of the window is: those
                        # that can be used, as the years are checked again
                        changes, _ = self._own_changes(sent.get("own"), None)
                        refused = self._refused(changes)
                        self._keep_own({
                            (kind, index): arguments
                            for (kind, index), arguments in changes.items()
                            if str(index) not in refused.get(kind, {})
                        })
                self.result = None
                self._used = True

    def reopen(self):
        """Let the page be used again, as when the oceanval app shows the
        window again for Back from a later step."""
        with self._lock:
            self._used = False
            self.result = None
            self._done.clear()

    def finish(self):
        self._done.set()

    def start(self):
        handler = type("Handler", (_Handler,), {"page": self})
        self._server = _Server(("127.0.0.1", 0), handler)
        port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{port}/?token={self.token}"
        threading.Thread(
            target=self._server.serve_forever,
            name="oceanval-recipes-window",
            daemon=True,
        ).start()
        return self.url

    def wait(self):
        """Block until the page is submitted or cancelled.

        Returns the (mapping, selection, settings, point_options,
        gridded_options) the script was written with, or None if the window
        was cancelled - from the page, or with Ctrl+C here.
        """
        try:
            # waiting in steps, so Ctrl+C is not held up until the page answers
            while not self._done.wait(0.25):
                pass
        except KeyboardInterrupt:
            self.cancel()
        return self.result

    def close(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


SSH_PORT = 8765


def over_ssh():
    """Whether this process is running in an SSH session."""
    return any(os.environ.get(name) for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"))


def remote_hint(port):
    """What to tell someone who has to reach the window on port from another
    machine: the command to forward it, ready to paste, if they are in an
    SSH session."""
    if not over_ssh():
        return f"On a remote machine, forward port {port} to reach it (VS Code does this for you)."
    login = f"{getpass.getuser()}@{socket.gethostname()}"
    return (
        f"You are connected over SSH, so forward port {port} first. In a new "
        "terminal on your own computer (not this one), run:\n"
        f"  ssh -L {port}:127.0.0.1:{port} {login}\n"
        "and leave it open while you use OceanVal (use your usual ssh login if "
        "it differs). VS Code does this for you."
    )


def _can_open_browser():
    """Whether a graphical web browser can be opened from here.

    Without a display, webbrowser falls back on a text browser such as lynx,
    which would take over the terminal create_recipes is waiting in. BROWSER
    is set by VS Code's remote terminals, to open links on your own machine.
    Over SSH without it, a DISPLAY is an X11 forward (MobaXterm sets one), so
    a browser would open on the remote machine, not the one being used.
    OCEANVAL_BROWSER overrides: 1 opens one, 0 (`oceanval --no-browser`) never.
    """
    chosen = os.environ.get("OCEANVAL_BROWSER", "").strip().lower()
    if chosen in ("1", "0"):
        return chosen == "1"
    if os.environ.get("BROWSER"):
        return True
    if over_ssh():
        return False
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _vscode_sockets():
    """The sockets VS Code's browser helper (BROWSER in its remote terminals)
    could reach a VS Code window by, to try in turn: the terminal's own, then
    the others of this user's that can be connected to, newest first, as the
    window open now is the one reloaded last. Empty if BROWSER is not the
    helper.

    VS Code brings a terminal back after its window is reloaded with the
    socket it had, which is gone by then, or still there but never answered,
    so the helper fails, or waits for ever."""
    given = os.environ.get("VSCODE_IPC_HOOK_CLI")
    if not given or os.path.basename(os.environ.get("BROWSER", "")) != "browser.sh":
        return []
    folder = os.path.dirname(given)
    others = []
    with contextlib.suppress(OSError):
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if name.startswith("vscode-ipc-") and name.endswith(".sock") and path != given:
                with contextlib.suppress(OSError):
                    others.append((os.path.getmtime(path), path))
    others.sort(reverse=True)
    return [given] + [path for _, path in others]


def _connectable(path):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(1)
        try:
            connection.connect(path)
        except OSError:
            return False
    return True


def _open_in_vscode(url, sockets, tries=3, timeout=4):
    """Open url with VS Code's browser helper, through the first of sockets
    that it answers by, trying at most tries of those that can be connected
    to. Whether it was opened."""
    for path in sockets:
        if tries == 0:
            break
        if not _connectable(path):
            continue
        tries -= 1
        try:
            helper = subprocess.Popen(
                [os.environ["BROWSER"], url],
                env=dict(os.environ, VSCODE_IPC_HOOK_CLI=path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # so the node it runs goes too if it waits too long
                start_new_session=True,
            )
        except OSError:
            continue
        try:
            if helper.wait(timeout) == 0:
                return True
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                os.killpg(helper.pid, signal.SIGKILL)
            helper.wait()
    return False


def _open_browser(url):
    sockets = _vscode_sockets()
    if sockets:
        # VS Code opens it in the browser of the computer it runs on, and
        # forwards its port
        return _open_in_vscode(url, sockets)
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def choose_recipes(mapping, domain, available, context, write, open_browser=True):
    """Show the create_recipes window, and wait until it is finished with.

    mapping is what create_recipes identified, available every variable in
    the model output, and context what the page's header shows, along with
    the start and end the Global settings begin with, the user's own data
    in the matchup ("own_data"), which the window shows ticked, and the
    recipes of the user's that it takes the place of ("replaced"), which the
    window leaves out (see recipe_rows). When the page is submitted,
    write(mapping, selection, settings, point_options, gridded_options)
    writes the script and returns its path. Returns what write was called
    with, or None if the window was cancelled. Inside hosted_by, the window
    is one of the host's pages instead.
    """
    page = RecipePage(
        recipe_rows(
            mapping,
            domain,
            context.get("cwd"),
            context.get("replaced") or (),
            context.get("own_data"),
        ),
        available,
        context,
        write,
    )
    if _host is not None:
        return _host.show_recipes(page)
    url = page.start()
    try:
        if open_browser and _can_open_browser() and _open_browser(url):
            print(
                "The create_recipes window is open in your web browser:\n"
                f"  {url}\n"
                "Choose the recipes there, then click Write script. Press "
                "Ctrl+C (or interrupt the notebook) to cancel.",
                flush=True,
            )
        else:
            port = urllib.parse.urlsplit(url).port
            print(
                "Open this link in a web browser to choose the recipes:\n"
                f"  {url}\n"
                f"{remote_hint(port)} Press Ctrl+C (or interrupt the notebook) "
                "to cancel.",
                flush=True,
            )
        return page.wait()
    finally:
        page.close()
