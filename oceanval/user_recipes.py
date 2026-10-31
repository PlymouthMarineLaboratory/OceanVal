"""Data recipes of your own, kept in a ``.oceanvalrc`` file.

OceanVal's own recipes, e.g. ``recipe={"chlorophyll": "occci"}``, are built
in. The recipes you register are kept in JSON files, and used the same way,
by the key your source is known by: a source called ``MySat`` for chlorophyll
is ``recipe={"chlorophyll": "mysat"}``.

Two files are read. ``.oceanvalrc`` in the directory OceanVal runs from holds
the recipes for that directory, and ``.oceanvalrc`` in your home directory (or
the file the ``OCEANVALRC`` environment variable names, if it is set) holds
those for everywhere. Where the same recipe is in both, the directory's wins.

This module only reads and writes the files, and finds and checks recipes
for OceanVal's other modules. It imports nothing else from OceanVal, so that
oceanval.parsers can use it. Checking the data a recipe points at is in
oceanval.recipe_checks.
"""

import contextlib
import datetime
import json
import os
import re
import tempfile
import textwrap
import threading

from oceanval.utils import loud_warning

FILE_NAME = ".oceanvalrc"
VERSION = 1

# the places a recipe can be kept, in the order they are shown
WHERE = ("local", "global")
WHERE_LABELS = {
    "local": "this directory only",
    "global": "everywhere (your home directory)",
}

# the keys of the recipes OceanVal comes with, which cannot be used for your own
BUILTIN_SOURCES = ("cobe2", "glodap", "ices", "nsbc", "occci", "woa23")

# what a variable's recipes are called in the report, for the variables
# OceanVal has built-in recipes for: (short_name, long_name, short_title)
VARIABLE_LABELS = {
    "chlorophyll": ("chlorophyll concentration", "chlorophyll a concentration", "Chlorophyll"),
    "oxygen": ("dissolved oxygen", "dissolved oxygen concentration", "Oxygen"),
    "temperature": ("sea temperature", "sea water temperature", "Temperature"),
    "salinity": ("salinity", "sea water salinity", "Salinity"),
    "nitrate": ("nitrate concentration", "nitrate concentration", "Nitrate"),
    "ammonium": ("ammonium concentration", "ammonium concentration", "Ammonium"),
    "phosphate": ("phosphate concentration", "phosphate concentration", "Phosphate"),
    "silicate": ("silicate concentration", "silicate concentration", "Silicate"),
    "kd490": ("KD490", "diffuse attenuation coefficient at 490 nm", "KD490"),
    "ph": ("pH", "sea water pH", "pH"),
    "alkalinity": ("total alkalinity", "sea water total alkalinity", "Total Alkalinity"),
}

# what the variables are called in the window's tables of datasets (the
# recipes window's two pages and the Info panel), never in the report, which
# keeps VARIABLE_LABELS
DISPLAY_NAMES = {
    "alkalinity": "Total alkalinity of sea water",
    "ammonium": "Ammonium concentration in sea water",
    "chlorophyll": "Chlorophyll a concentration in sea water",
    "kd490": "Diffuse attenuation coefficient at 490 nm",
    "nitrate": "Nitrate concentration in sea water",
    "oxygen": "Dissolved oxygen concentration in sea water",
    "ph": "Sea water pH",
    "phosphate": "Phosphate concentration in sea water",
    "salinity": "Sea water salinity",
    "silicate": "Silicate concentration in sea water",
    "temperature": "Sea water temperature",
}


def display_name(variable, fallback):
    """A variable's name in the window's tables of datasets: DISPLAY_NAMES',
    or fallback (its title) for one OceanVal has no recipes for."""
    return DISPLAY_NAMES.get(str(variable).lower(), fallback)

# names on a Validator, which a variable cannot be called
RESERVED_VARIABLES = (
    "keys",
    "reset",
    "remove",
    "add_gridded_comparison",
    "add_point_comparison",
)

LOCATIONS = ("disk", "thredds", "url")
_NAME = re.compile("^[A-Za-z0-9]+$")
_LOWER = re.compile("^[a-z0-9]+$")

# a file that is unreadable is said so once, not on every look
_warned = set()
_cache = {}
_lock = threading.Lock()


def local_path(cwd=None):
    """The ``.oceanvalrc`` for the directory OceanVal runs from."""
    return os.path.join(os.path.abspath(cwd or os.getcwd()), FILE_NAME)


def global_path():
    """The ``.oceanvalrc`` for everywhere: ``$OCEANVALRC`` if that is set,
    otherwise the one in your home directory."""
    custom = os.environ.get("OCEANVALRC", "").strip()
    if custom:
        return os.path.abspath(os.path.expanduser(custom))
    return os.path.join(os.path.expanduser("~"), FILE_NAME)


def path_for(where, cwd=None):
    if where not in WHERE:
        raise ValueError(f"where must be one of {WHERE}")
    return local_path(cwd) if where == "local" else global_path()


def same_file(cwd=None):
    """Whether the two files are one, as when OceanVal runs in your home
    directory."""
    return os.path.realpath(local_path(cwd)) == os.path.realpath(global_path())


def _read(path):
    """The recipes in one file, as {(variable, key): recipe}, and why the
    file could not be read, if it could not. A missing file is empty."""
    try:
        stat = os.stat(path)
    except OSError:
        return {}, None
    signature = (path, stat.st_mtime_ns, stat.st_size)
    with _lock:
        if signature in _cache:
            return _cache[signature]
    found, problem = {}, None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or not isinstance(data.get("recipes", {}), dict):
            raise ValueError('it should hold {"version": 1, "recipes": {...}}')
        for variable, keys in data.get("recipes", {}).items():
            for key, recipe in (keys.items() if isinstance(keys, dict) else []):
                reason = _invalid(variable, key, recipe)
                if reason:
                    _say(path, stat.st_mtime_ns, f"{variable}/{key} was ignored: {reason}")
                    continue
                found[variable, key] = dict(recipe, variable=variable, key=key, path=path)
    except (OSError, ValueError) as error:
        problem = f"{error}"
        _say(path, stat.st_mtime_ns, f"the file could not be read, so it was ignored: {problem}")
        found = {}
    with _lock:
        if len(_cache) > 64:
            _cache.clear()
        _cache[signature] = (found, problem)
    return found, problem


def _say(path, stamp, text):
    if (path, stamp, text) in _warned:
        return
    _warned.add((path, stamp, text))
    loud_warning(
        f"PROBLEM IN {path}",
        [text, "", "Fix or remove it. OceanVal's other recipes are not affected."],
        warning=f"{path}: {text}",
    )


def _invalid(variable, key, recipe):
    """Why one recipe in a file cannot be used, or None."""
    if not isinstance(variable, str) or not _LOWER.match(variable):
        return "the variable must be lower-case letters and numbers"
    if not isinstance(key, str) or not _LOWER.match(key):
        return "the recipe's key must be lower-case letters and numbers"
    if key in BUILTIN_SOURCES:
        return f"{key} is the key of a recipe OceanVal comes with"
    if not isinstance(recipe, dict):
        return "it should be an object"
    kind = recipe.get("kind")
    if kind not in ("gridded", "point"):
        return 'kind must be "gridded" or "point"'
    source = recipe.get("source")
    if not isinstance(source, str) or not _NAME.match(source) or source.lower() != key:
        return "source must be the key, letters and numbers only"
    obs_path = recipe.get("obs_path")
    if kind == "point":
        if not isinstance(obs_path, str) or not obs_path:
            return "obs_path must be a csv file or the directory of csv files"
        return None
    if recipe.get("location") not in LOCATIONS:
        return f"location must be one of {', '.join(LOCATIONS)}"
    if recipe["location"] == "thredds":
        ok = isinstance(obs_path, str) or (
            isinstance(obs_path, list) and obs_path and all(isinstance(x, str) for x in obs_path)
        )
    else:
        ok = isinstance(obs_path, str) and bool(obs_path)
    if not ok:
        return "obs_path must be a path or a web address"
    if not isinstance(recipe.get("obs_variable"), str) or not recipe["obs_variable"]:
        return "obs_variable must be the name of the variable in the data"
    if not isinstance(recipe.get("climatology"), bool):
        return "climatology must be true or false"
    return None


def _both(cwd=None):
    """The recipes of the file for this directory and of the global file."""
    local, _ = _read(local_path(cwd))
    if same_file(cwd):
        return local, {}
    return local, _read(global_path())[0]


def problem_in_files(cwd=None):
    """Why either file could not be read, as {where: reason}."""
    problems = {}
    for where in WHERE:
        path = path_for(where, cwd)
        reason = _read(path)[1]
        if reason:
            problems[where] = reason
        if where == "local" and same_file(cwd):
            break
    return problems


def listing(cwd=None):
    """Every recipe in both files, with where it is kept ("local" or
    "global"), the file ("path"), and whether the other file's recipe of the
    same variable and key takes its place for this directory ("shadowed")."""
    local, home = _both(cwd)
    rows = []
    for where, recipes in (("local", local), ("global", home)):
        for (variable, key), recipe in recipes.items():
            rows.append(
                dict(
                    recipe,
                    where=where,
                    shadowed=where == "global" and (variable, key) in local,
                )
            )
    rows.sort(key=lambda r: (r["variable"], r["key"], WHERE.index(r["where"])))
    return rows


def load(cwd=None):
    """The recipes in force for this directory: the global file's, with the
    directory's own in place of any with the same variable and key. Each has
    "variable", "key", "where" and "path"."""
    return [row for row in listing(cwd) if not row["shadowed"]]


def get(variable, key, cwd=None):
    """The recipe in force for a variable and key, or None."""
    variable, key = str(variable).lower(), str(key).lower()
    for row in load(cwd):
        if row["variable"] == variable and row["key"] == key:
            return row
    return None


def variables(cwd=None):
    """The variables the recipes in force are for."""
    return list(dict.fromkeys(row["variable"] for row in load(cwd)))


def labels(variable, cwd=None):
    """(short_name, long_name, short_title) for a variable: OceanVal's own
    for a variable it has recipes for, otherwise those the user gave it, or
    None if there are none."""
    variable = variable.lower()
    if variable in VARIABLE_LABELS:
        return VARIABLE_LABELS[variable]
    for row in listing(cwd):
        if row["variable"] == variable and row.get("short_title"):
            return (
                row.get("short_name") or variable,
                row.get("long_name") or variable,
                row["short_title"],
            )
    return None


def find(variable, key, cwd=None):
    """A recipe in the form oceanval.parsers.find_recipe gives the ones that
    are built in, or None if there is no such recipe."""
    recipe = get(variable, key, cwd)
    if recipe is None:
        return None
    variable = recipe["variable"]
    found = labels(variable, cwd)
    short_name, long_name, short_title = found or (variable, variable, variable.title())
    output = {
        "vertical": None,
        "point": recipe["kind"] == "point",
        "short_name": short_name,
        "long_name": long_name,
        "short_title": short_title,
        "source": recipe["source"],
        "source_info": recipe.get("source_info") or f"Source for {recipe['source']}",
        "name": variable,
        "obs_path": recipe["obs_path"],
        "user": True,
        "where": recipe["where"],
        "path": recipe["path"],
        "units": recipe.get("units"),
    }
    if recipe["kind"] == "point":
        output["obs_variable"] = "observation"
        output["vertical"] = None if recipe.get("depth_resolved") else False
        return output
    output.update(
        thredds=recipe["location"] == "thredds",
        climatology=recipe["climatology"],
        obs_variable=recipe["obs_variable"],
        vertical=None if recipe.get("depth_resolved") else False,
    )
    if recipe["location"] == "url":
        # a web address is downloaded when it is matched up, so is not checked
        # when it is registered
        output["file_check"] = False
    return output


def where_of(variable, key, cwd=None):
    """Where the recipe in force is kept, "local" or "global", or None."""
    recipe = get(variable, key, cwd)
    if recipe is None:
        return None
    local, _ = _both(cwd)
    return "local" if (recipe["variable"], recipe["key"]) in local else "global"


def key_of(source):
    return str(source).strip().lower()


def name_problems(variable, source, strict=True):
    """What is wrong with the names of a recipe's variable and source, as
    {box: reason}. A blank one is only a problem if strict."""
    errors = {}
    variable, source = str(variable).strip(), str(source).strip()
    if not variable:
        if strict:
            errors["variable"] = "This is needed."
    elif not _NAME.match(variable):
        errors["variable"] = "Use letters and numbers only."
    elif variable.lower() in RESERVED_VARIABLES or variable.startswith("_"):
        errors["variable"] = f"{variable} cannot be used as a variable name."
    if not source:
        if strict:
            errors["source"] = "This is needed."
    elif not _NAME.match(source):
        errors["source"] = "Use letters and numbers only: no spaces or underscores."
    elif key_of(source) in BUILTIN_SOURCES:
        errors["source"] = (
            f"{source} is the name of one of OceanVal's own recipes. Choose another."
        )
    return errors


def problems(recipe, where, cwd=None):
    """Whether a recipe can be saved to where ("local" or "global").

    recipe is the recipe as it would be stored, plus "variable". Returns
    (errors, warnings): errors maps each box that cannot be used to the
    reason, with "" for one that is about the recipe as a whole, and
    warnings are things to know that do not stop it being saved.
    """
    warnings = []
    variable = str(recipe.get("variable", "")).strip()
    source = str(recipe.get("source", "")).strip()
    errors = name_problems(variable, source)
    if where not in WHERE:
        errors["where"] = "Choose where to save it."
    if errors:
        return errors, warnings

    variable, key = variable.lower(), key_of(source)
    mine = _read(path_for(where, cwd))[0]
    reason = problem_in_files(cwd).get(where)
    if reason:
        errors[""] = (
            f"{path_for(where, cwd)} could not be read ({reason}), so OceanVal will not "
            "write to it. Fix or remove it first."
        )
        return errors, warnings
    if (variable, key) in mine:
        errors["source"] = (
            f"{source} is already a {variable} recipe in {path_for(where, cwd)}. "
            "Remove that first, or choose another source name."
        )
        return errors, warnings
    if not same_file(cwd):
        other = "global" if where == "local" else "local"
        there = _read(path_for(other, cwd))[0]
        if (variable, key) in there:
            where_file = path_for(other, cwd)
            if there[variable, key]["kind"] != recipe.get("kind"):
                # a recipe is picked by its variable and key, so these must
                # not be one thing in one place and another in the other
                errors["source"] = (
                    f"{source} is already a {there[variable, key]['kind']} {variable} "
                    f"recipe in {where_file}. Choose another source name."
                )
                return errors, warnings
            if where == "local":
                warnings.append(
                    f"There is a {variable} recipe called {key} in {where_file} too. "
                    "In this directory, this one is used in its place."
                )
            else:
                warnings.append(
                    f"There is a {variable} recipe called {key} in {where_file} too. "
                    "In that directory, that one is used in place of this."
                )
    given = (recipe.get("short_name"), recipe.get("long_name"), recipe.get("short_title"))
    known = labels(variable, cwd)
    if known is not None and all(given) and given != tuple(known):
        # a variable's labels are the same for all its recipes
        errors[""] = (
            f"{variable} already has its own names in the report: "
            f"{known[2]}. Its recipes all share them."
        )
    return errors, warnings


def _write(path, data):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=FILE_NAME + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=2, ensure_ascii=False)
            out.write("\n")
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(temporary)
        raise


def _read_raw(path):
    """The whole of a file as it is, so that what OceanVal does not use is
    kept when it is written again."""
    if not os.path.exists(path):
        return {"version": VERSION, "recipes": {}}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("it should hold a JSON object")
    if not isinstance(data.get("recipes"), dict):
        data["recipes"] = {}
    data.setdefault("version", VERSION)
    return data


def save(recipe, where, cwd=None):
    """Add a recipe to the file for where. recipe holds "variable", and the
    rest as it is stored (see the file format in the documentation). Raises
    ValueError if it cannot be saved (see problems). Returns the path."""
    errors, _ = problems(recipe, where, cwd)
    if errors:
        raise ValueError("; ".join(f"{k or 'recipe'}: {v}" for k, v in errors.items()))
    variable = str(recipe["variable"]).lower()
    stored = {k: v for k, v in recipe.items() if k != "variable" and v is not None}
    stored["source"] = str(recipe["source"]).strip()
    stored.setdefault("added", datetime.date.today().isoformat())
    reason = _invalid(variable, key_of(stored["source"]), stored)
    if reason:
        raise ValueError(reason)
    path = path_for(where, cwd)
    data = _read_raw(path)
    data["recipes"].setdefault(variable, {})[key_of(stored["source"])] = stored
    _write(path, data)
    return path


def remove(variable, key, where, cwd=None):
    """Take one recipe out of the file for where. Returns whether it was
    there."""
    variable, key = str(variable).lower(), str(key).lower()
    path = path_for(where, cwd)
    if not os.path.exists(path):
        return False
    data = _read_raw(path)
    keys = data["recipes"].get(variable)
    if not isinstance(keys, dict) or key not in keys:
        return False
    del keys[key]
    if not keys:
        del data["recipes"][variable]
    _write(path, data)
    return True


def wrap(text, width=76):
    return textwrap.wrap(" ".join(str(text).split()), width) or [""]
