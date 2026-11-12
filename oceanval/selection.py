"""Which matchups a report validates: validate()'s variables option.

Each matchup is a variable matched against the observations of a source,
point or gridded. validate() makes a page for each one under data_dir's
oceanval_matchups, unless variables picks some of them: each of its entries
is a variable, for all its sources, or "variable/source", for one.
previous=False leaves out the matchups an earlier matchup() run made there,
as matchup() records those of the last run (see save_last_run).
"""

import glob
import json
import os

# a matchup's key, as variables gives it
SEPARATOR = "/"

# the record, in oceanval_matchups, of the matchups the last matchup() run
# there made or kept: validate(previous=False) validates only those
LAST_RUN = "last_matchup.json"

# what point matchups are matched through, as their layer says
LAYERS = {
    "surface": "at the surface",
    "all": "through the water column",
    "bottom": "near the bottom",
}


def key(variable, source, layer=None):
    """A matchup's "variable/source", as variables takes it, with "/layer"
    after it for a point matchup (a gridded matchup has no layer). A
    "variable/source" entry picks every layer."""
    parts = [variable, source] + ([layer] if layer else [])
    return SEPARATOR.join(parts)


def _titles(matchup_dir):
    """The report's name for each variable, as matchup saved them."""
    import dill

    try:
        with open(os.path.join(matchup_dir, "short_titles.pkl"), "rb") as file:
            titles = dill.load(file)
    except Exception:
        return {}
    return titles if isinstance(titles, dict) else {}


def on_disk(data_dir):
    """The matchups in data_dir's oceanval_matchups that validate() makes a
    page for, as dicts of kind ("point" or "gridded"), variable, source,
    layer (a point matchup's, or None), whether it is through the water
    column (vertical), its title and its key - sorted by title, then source,
    gridded first. The file names are read as validate() reads them."""
    matchup_dir = os.path.join(data_dir, "oceanval_matchups")
    titles = _titles(matchup_dir)
    found = {}
    for path in glob.glob(os.path.join(glob.escape(matchup_dir), "point", "*", "*", "*", "*.csv")):
        name = os.path.basename(path)
        parts = name[: -len(".csv")].split("_")
        if "unit" in name or len(parts) != 3:
            continue
        source, layer, variable = parts
        found[("point", variable, source, layer)] = layer == "all"
    for path in glob.glob(os.path.join(glob.escape(matchup_dir), "gridded", "*", "*.nc")):
        parts = os.path.basename(path)[: -len(".nc")].split("_")
        if len(parts) < 2:
            continue
        source, variable = parts[0], parts[1]
        vertical = parts[-1] == "vertical"
        found[("gridded", variable, source, None)] = (
            found.get(("gridded", variable, source, None), False) or vertical
        )
    matchups = [
        {
            "kind": kind,
            "variable": variable,
            "source": source,
            "layer": layer,
            "vertical": vertical,
            "title": titles.get(variable) or variable.title(),
            "key": key(variable, source, layer),
        }
        for (kind, variable, source, layer), vertical in found.items()
    ]
    matchups.sort(
        key=lambda m: (m["kind"] != "gridded", m["title"].lower(), m["source"].lower(), m["layer"] or "")
    )
    return matchups


def _identity(kind, variable, source, layer):
    # a gridded matchup has no layer, whatever it was told with
    return (kind, variable, source, layer if kind == "point" else None)


def identity(matchup):
    """A matchup (as on_disk gives it) as (kind, variable, source, layer),
    as last_run gives them."""
    return _identity(matchup["kind"], matchup["variable"], matchup["source"], matchup["layer"])


def save_last_run(matchup_dir, made):
    """Record the matchups a matchup() run made or kept, each as (kind,
    variable, source, layer), in matchup_dir. Written to a file beside it
    first, so that the record is always whole."""
    path = os.path.join(matchup_dir, LAST_RUN)
    partial = path + ".part"
    with open(partial, "w") as file:
        json.dump({"matchups": [list(_identity(*m)) for m in made]}, file, indent=1)
    os.replace(partial, path)


def forget_last_run(matchup_dir):
    """Remove the record of the last run, as a run starts matching up, so
    that one that does not finish leaves none behind."""
    try:
        os.remove(os.path.join(matchup_dir, LAST_RUN))
    except FileNotFoundError:
        pass


def last_run(data_dir):
    """The matchups the last matchup() run into data_dir made or kept, as a
    set of (kind, variable, source, layer). Raises ValueError if there is no
    record of them."""
    matchup_dir = os.path.join(data_dir, "oceanval_matchups")
    try:
        with open(os.path.join(matchup_dir, LAST_RUN)) as file:
            made = json.load(file)["matchups"]
        return {_identity(*m) for m in made}
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError(
            f"{matchup_dir} has no record of which matchups the last matchup() run "
            "made: it did not finish, or was made by an older OceanVal. Run matchup() "
            "again, or validate with previous=True"
        ) from None


def of_last_run(matchups, record, previous=False):
    """The matchups (as on_disk gives them) that the last run made or kept,
    as last_run gives them, and the others that previous, validate's, picks
    (see picks)."""
    return [
        m for m in matchups
        if identity(m) in record or picks(previous, m["variable"], m["source"], m["layer"])
    ]


def picks(choice, variable, source, layer=None):
    """Whether choice, matchup's overwrite or validate's previous, picks the
    matchup of variable with source (at layer, if it is a point matchup): True
    or False, for every matchup, or a list as check_variables gives it, for
    those it picks."""
    if isinstance(choice, bool):
        return choice
    return wanted(choice, variable, source, layer)


def check_variables(variables, name="variables"):
    """variables checked, as a list of entries, each a variable or
    "variable/source". None, for every matchup. Raises ValueError if it
    cannot be used: what it picks is checked against the matchups with
    check_against, once they are there. name is the argument's, as the
    errors say it: matchup's overwrite and validate's previous take such a
    list too."""
    if variables is None:
        return None
    if isinstance(variables, str):
        variables = [variables]
    if isinstance(variables, dict) or not hasattr(variables, "__iter__"):
        raise ValueError(f'{name} must be a list of variables, or of "variable/source"')
    entries = []
    for entry in variables:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(
                f'{name} must be a list of variables, or of "variable/source", not {entry!r}'
            )
        parts = [part.strip() for part in entry.split(SEPARATOR)]
        if len(parts) > 3 or not all(parts):
            raise ValueError(
                f'{name} takes a variable, "variable/source", or, for one layer of a point '
                f'matchup, "variable/source/layer", not {entry!r}'
            )
        if len(parts) == 3 and parts[2].lower() not in LAYERS:
            raise ValueError(
                f"{name}: the layer of {entry!r} must be one of {', '.join(LAYERS)}, "
                f"not {parts[2]!r}"
            )
        entries.append(SEPARATOR.join(parts))
    if not entries:
        raise ValueError(f"{name} must name at least one variable")
    return entries


def _matches(entry, variable, source, layer=None):
    parts = entry.lower().split(SEPARATOR)
    if parts[0] != variable.lower():
        return False
    if len(parts) > 1 and parts[1] != source.lower():
        return False
    # a layer is only for a point matchup: a gridded one has none to match
    return len(parts) < 3 or (layer is not None and parts[2] == layer.lower())


def wanted(variables, variable, source, layer=None):
    """Whether variables (as check_variables gives it) picks the matchup of
    variable with source, at layer if it is a point matchup."""
    return variables is None or any(_matches(entry, variable, source, layer) for entry in variables)


def check_against(variables, matchups, name="variables"):
    """The matchups (as on_disk gives them) that variables picks. Raises
    ValueError if an entry picks none of them."""
    if variables is None:
        return list(matchups)
    for entry in variables:
        if not any(_matches(entry, m["variable"], m["source"], m["layer"]) for m in matchups):
            there = sorted({m["key"] for m in matchups}, key=str.lower)
            raise ValueError(
                f"{name}: there are no matchups of {entry!r}. The matchups are "
                f"{', '.join(there)}"
            )
    return [m for m in matchups if wanted(variables, m["variable"], m["source"], m["layer"])]


class UnsafeRemoval(Exception):
    """remove_matchups refused to delete anything."""


_REMOVABLE = (".nc", ".csv", ".pkl")


def remove_matchups(data_dir, keys):
    """Delete the files of the matchups in data_dir's oceanval_matchups whose
    key (see key) is one of keys, so that a matchup() run with overwrite=False
    makes them again: a point matchup's csv, definitions and matchup_dict.pkl,
    or the gridded files of a variable and source. Keys are compared in lower
    case. Returns the number of files removed.

    Deliberately paranoid: only single files are ever removed (never a
    directory), and nothing at all is removed, with UnsafeRemoval raised,
    unless the folder is a real (not linked) one with "oceanval" in its name
    and every file is a regular file of a known kind that really lies inside
    it. All files are checked before the first is removed."""
    keys = [k for k in keys if isinstance(k, str) and k.strip()]
    if not keys:
        return 0
    wanted = {k.lower() for k in keys}
    matchup_dir = os.path.join(data_dir, "oceanval_matchups")
    if not os.path.isdir(matchup_dir) or os.path.islink(matchup_dir):
        raise UnsafeRemoval(f"{matchup_dir} is not a plain folder of matchups")
    root = os.path.realpath(matchup_dir)
    if "oceanval" not in os.path.basename(root).lower():
        raise UnsafeRemoval(f"{root} does not have oceanval in its name")
    candidates = []
    for matchup in on_disk(data_dir):
        if matchup["key"].lower() not in wanted:
            continue
        variable, source, layer = matchup["variable"], matchup["source"], matchup["layer"]
        gridded = os.path.join(glob.escape(matchup_dir), "gridded", "*")
        if matchup["kind"] == "gridded":
            paths = glob.glob(os.path.join(gridded, glob.escape(f"{source}_{variable}") + "_*"))
            paths += glob.glob(os.path.join(gridded, glob.escape(f"{source}_matchup_dict.pkl")))
        else:
            folder = os.path.join(glob.escape(matchup_dir), "point", layer, variable, source)
            paths = glob.glob(os.path.join(folder, glob.escape(f"{source}_{layer}_{variable}") + "*"))
            paths += glob.glob(os.path.join(folder, "matchup_dict.pkl"))
        for path in paths:
            if os.path.isdir(path) and not os.path.islink(path):
                continue
            real = os.path.realpath(path)
            if (
                os.path.islink(path)
                or not os.path.isfile(path)
                or not path.endswith(_REMOVABLE)
                or os.path.commonpath([root, real]) != root
                or real == root
            ):
                raise UnsafeRemoval(f"{path} is not a matchup file inside {root}")
            candidates.append(path)
    for path in candidates:
        os.remove(path)
    return len(candidates)
