"""The user's own observations, as the oceanval window asks for them.

Each entry is one call of ``add_point_comparison`` or
``add_gridded_comparison``. POINT_FIELDS and GRIDDED_FIELDS describe every
argument of those calls that is not a recipe's, so the window can build its
forms from them, marking which arguments have to be given. ``check_entry``
turns what a form sent into the arguments of the call, having tried the call
itself, so that whatever it would refuse is shown beside the form rather than
after a run has started.
"""

import contextlib
import glob
import io
import os
import re
import threading
import warnings

from oceanval import session
from oceanval.parsers import Validator, point_files

KINDS = ("point", "gridded")

# the calls' defaults, for arguments left alone not to be written out
_DEFAULTS = {
    "start": -1000,
    "end": 3000,
    "vertical": False,
    "obs_multiplier": 1,
    "obs_adder": 0,
    "thredds": False,
    "file_check": True,
}


def _field(
    name,
    label,
    kind="text",
    required=False,
    help="",
    placeholder="",
    choices=None,
):
    return {
        "name": name,
        "label": label,
        "kind": kind,
        "required": required,
        "default": _DEFAULTS.get(name),
        "help": help,
        "placeholder": placeholder,
        "choices": choices,
    }


_LABELS = [
    _field(
        "long_name",
        "Long name",
        help="The variable's full name, used in the report. Defaults to the name.",
    ),
    _field(
        "short_name",
        "Short name",
        help="Used for the variable's figures and files. Defaults to the name.",
    ),
    _field(
        "short_title",
        "Short title",
        help="The title of the variable's report page. Defaults to the name.",
    ),
    _field(
        "source_info",
        "Source information",
        help="A description of the source, shown in the report.",
    ),
]

_UNITS = [
    _field(
        "obs_multiplier",
        "Observation multiplier",
        "number",
        help="The observations are multiplied by this, to convert their units.",
        placeholder="default: 1",
    ),
    _field(
        "obs_adder",
        "Observation adder",
        "number",
        help="This is added to the observations, after they are multiplied.",
        placeholder="default: 0",
    ),
]

_NAME = _field(
    "name",
    "Variable name",
    required=True,
    help="Letters and numbers only. The same name for another source adds "
    "it to that variable's report. Choose a recipe variable or enter your own.",
    placeholder="e.g. chlorophyll",
)
_SOURCE = _field(
    "source",
    "Source",
    required=True,
    help="What the observations are called in the report. It cannot contain "
    "an underscore.",
    placeholder="e.g. mycruise",
)
_MODEL_VARIABLE = _field(
    "model_variable",
    "Simulation variable",
    required=True,
    help="The simulation variable to compare with. Use a+b+c to add variables together.",
)

POINT_FIELDS = [
    _NAME,
    _SOURCE,
    _MODEL_VARIABLE,
    _field(
        "obs_path",
        "Observations file or directory",
        required=True,
        help="A csv file, or a directory of csv files. Each needs lon, lat and "
        "observation columns, and can have year, month, day, depth and source.",
        placeholder="path/to/data.csv or path/to/csv_directory",
    ),
    *_LABELS,
    _field(
        "start",
        "First year",
        "int",
        help="The first year of observations to use. Defaults to all of them.",
        placeholder="e.g. 2000",
    ),
    _field(
        "end",
        "Last year",
        "int",
        help="The last year of observations to use. Defaults to all of them.",
        placeholder="e.g. 2010",
    ),
    _field(
        "vertical",
        "Vertical",
        "bool",
        help="Validate through the water column. The csv files need a depth column.",
    ),
    *_UNITS,
    _field(
        "binning",
        "Binning",
        "numbers2",
        help="Longitude and latitude resolution to bin the observations to, "
        "as two numbers separated by a comma.",
        placeholder="e.g. 0.5, 0.5",
    ),
    _field(
        "point_time_res",
        "Time resolution",
        "multi",
        help="Which of these the observations are matched to the simulation by, "
        "in place of matchup's own setting.",
        choices=["year", "month", "day"],
    ),
]

GRIDDED_FIELDS = [
    _NAME,
    _SOURCE,
    _MODEL_VARIABLE,
    _field(
        "obs_path",
        "Observations",
        required=True,
        help="A netCDF file, a pattern that matches several (e.g. obs/*.nc), "
        "or a URL. A list of paths can be given in the script itself.",
        placeholder="path/to/observations.nc",
    ),
    _field(
        "obs_variable",
        "Observation variable",
        required=True,
        help="The variable in the observation files to compare with.",
    ),
    _field(
        "climatology",
        "Climatology",
        "yesno",
        required=True,
        help="Yes if the observations are a climatology, with one value per "
        "month, rather than a time series.",
    ),
    *_LABELS,
    _field(
        "start",
        "First year",
        "int",
        help="The first year of observations to use. Defaults to all of them.",
        placeholder="e.g. 2000",
    ),
    _field(
        "end",
        "Last year",
        "int",
        help="The last year of observations to use. Defaults to all of them.",
        placeholder="e.g. 2010",
    ),
    _field(
        "vertical",
        "Vertical",
        "bool",
        help="Validate through the water column.",
    ),
    *_UNITS,
    _field(
        "thredds",
        "THREDDS / OPeNDAP",
        "bool",
        help="The observations are a URL served by a THREDDS or OPeNDAP server.",
    ),
    _field(
        "file_check",
        "Check the observations first",
        "bool",
        help="Open the observation file to check the variable is in it. "
        "Untick this for remote data that may be slow or unreachable.",
    ),
]

FIELDS = {"point": POINT_FIELDS, "gridded": GRIDDED_FIELDS}

_NAME_PATTERN = re.compile("^[A-Za-z0-9]+$")

# the calls register on shared state (see _try), so one at a time
_lock = threading.Lock()


def _blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _number(value):
    number = float(value)
    return int(number) if number.is_integer() else number


def _coerce(field, value, errors):
    """The typed value of one box, or None for a box left as it was.
    Problems are added to errors."""
    name, kind = field["name"], field["kind"]
    if kind in ("bool", "yesno"):
        if isinstance(value, str):
            value = {"yes": True, "no": False, "true": True, "false": False}.get(
                value.strip().lower()
            )
        if not isinstance(value, bool):
            return None
        if kind == "bool" and value == field["default"]:
            return None
        return value
    if kind == "multi":
        if isinstance(value, str):
            value = [value]
        if _blank(value) or not value:
            return None
        if not isinstance(value, list) or any(
            item not in field["choices"] for item in value
        ):
            errors[name] = "Choose from " + ", ".join(field["choices"]) + "."
            return None
        # in the order offered, without repeats
        return [item for item in field["choices"] if item in value]
    if _blank(value):
        return None
    if kind == "text":
        return str(value).strip()
    if kind == "numbers2":
        parts = [part for part in re.split(r"[,\s]+", str(value).strip()) if part]
        try:
            if len(parts) != 2:
                raise ValueError
            return [_number(part) for part in parts]
        except ValueError:
            errors[name] = "Give two numbers separated by a comma, e.g. 0.5, 0.5."
            return None
    try:
        number = _number(value)
        if kind == "int" and (isinstance(number, float) or "." in str(value)):
            raise ValueError
    except (TypeError, ValueError):
        errors[name] = (
            "This must be a whole number, e.g. 2011."
            if kind == "int"
            else "This must be a number."
        )
        return None
    if field["default"] is not None and number == field["default"]:
        return None
    return number


def _remote(arguments):
    """Whether the observations are a URL rather than files."""
    return bool(arguments.get("thredds")) or "://" in arguments.get("obs_path", "")


def _register(validator, kind, arguments):
    call = (
        validator.add_point_comparison
        if kind == "point"
        else validator.add_gridded_comparison
    )
    call(**arguments)


def _try(kind, arguments, existing):
    """Make the calls for the entries before this one, and then this one,
    on a validator of its own. Returns what the call refused with, if it
    did.

    The calls also note each variable's short title where matchup() does,
    so that is put back afterwards, and what they print is dropped.
    """
    with _lock:
        titles = dict(session.session_info["short_title"])
        validator = Validator()
        # Validator keeps its variables' names in a list of the class's
        # own unless it has one, and the window's process has to keep
        # oceanval's definitions to itself
        validator.keys = []
        session.session_info["short_title"].clear()
        try:
            with contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
                warnings.simplefilter("ignore")
                for other_kind, other in existing:
                    # these were checked when they were added
                    if other_kind == "gridded":
                        other = dict(other, file_check=False)
                    _register(validator, other_kind, other)
                _register(validator, kind, arguments)
        except Exception as error:
            return str(error) or type(error).__name__
        finally:
            session.session_info["short_title"].clear()
            session.session_info["short_title"].update(titles)
    return None


def refusal(kind, arguments, existing=()):
    """What the call would refuse arguments with (as check_entry gives them,
    perhaps with more added since), after the entries in existing, or None
    if it takes them. Their data is not looked at again."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if kind == "gridded":
        arguments = dict(arguments, file_check=False)
    return _try(kind, arguments, existing)


def check_entry(kind, form, existing=(), cwd=None):
    """Turn a form's boxes into the arguments of add_point_comparison or
    add_gridded_comparison.

    existing is the entries already added, as (kind, arguments) pairs, so
    that conflicts with them are found. Returns (arguments, errors), where
    errors maps each box that cannot be used to the reason, and "" to
    a reason that is about the entry as a whole.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    form = form if isinstance(form, dict) else {}
    errors, arguments = {}, {}
    for field in FIELDS[kind]:
        value = _coerce(field, form.get(field["name"]), errors)
        if field["name"] in errors:
            continue
        if value is None:
            if field["required"]:
                errors[field["name"]] = (
                    "Choose yes or no."
                    if field["kind"] == "yesno"
                    else "This is needed."
                )
            continue
        arguments[field["name"]] = value

    name, source = arguments.get("name"), arguments.get("source")
    if name and not _NAME_PATTERN.match(name):
        errors["name"] = "Use letters and numbers only."
    if source and "_" in source:
        errors["source"] = "This cannot contain an underscore."

    obs_path = arguments.get("obs_path")
    if obs_path and "obs_path" not in errors:
        remote = kind == "gridded" and _remote(arguments)
        if not remote:
            path = os.path.normpath(
                os.path.join(cwd or os.getcwd(), os.path.expanduser(obs_path))
            )
            if kind == "point":
                if not os.path.exists(path):
                    errors["obs_path"] = "There is no file or directory at this path."
                elif not point_files(path):
                    errors["obs_path"] = "There are no csv files in this directory."
                else:
                    arguments["obs_path"] = path
            elif obs_path != "auto":
                if not glob.glob(path):
                    errors["obs_path"] = "No files match this path."
                else:
                    arguments["obs_path"] = path

    if errors:
        return arguments, errors
    # the calls' own checks: the csv columns, the variable being in the file,
    # names that clash with an earlier entry...
    reason = _try(kind, arguments, existing)
    if reason is not None:
        errors[_blame(kind, reason)] = reason
    return arguments, errors


def _blame(kind, reason):
    """The box a refusal is about, or "" if it is about none."""
    lowered = reason.lower()
    for name, pattern in (
        ("vertical", "vertical is set"),
        ("obs_variable", "obs_variable"),
        ("obs_path", "obs path|observation path|obs_path|point directory|point data"),
        ("model_variable", "model variable"),
        ("short_title", "short title"),
        ("source", "source"),
        ("binning", "binning"),
        ("obs_multiplier", "obs_multiplier"),
        ("obs_adder", "obs_adder"),
        ("start", "start and end"),
        ("name", "name can only"),
    ):
        if re.search(pattern, lowered):
            return name
    return ""
