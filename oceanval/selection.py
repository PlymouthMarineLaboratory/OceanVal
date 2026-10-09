"""Which matchups a report validates: validate()'s variables option.

Each matchup is a variable matched against the observations of a source,
point or gridded. validate() makes a page for each one under data_dir's
oceanval_matchups, unless variables picks some of them: each of its entries
is a variable, for all its sources, or "variable/source", for one.
"""

import glob
import os

# a matchup's key, as variables gives it
SEPARATOR = "/"

# what point matchups are matched through, as their layer says
LAYERS = {
    "surface": "at the surface",
    "all": "through the water column",
    "bottom": "near the bottom",
}


def key(variable, source):
    """A matchup's "variable/source", as variables takes it."""
    return f"{variable}{SEPARATOR}{source}"


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
            "key": key(variable, source),
        }
        for (kind, variable, source, layer), vertical in found.items()
    ]
    matchups.sort(
        key=lambda m: (m["kind"] != "gridded", m["title"].lower(), m["source"].lower(), m["layer"] or "")
    )
    return matchups


def check_variables(variables):
    """variables checked, as a list of entries, each a variable or
    "variable/source". None, for every matchup. Raises ValueError if it
    cannot be used: what it picks is checked against the matchups with
    check_against, once they are there."""
    if variables is None:
        return None
    if isinstance(variables, str):
        variables = [variables]
    if isinstance(variables, dict) or not hasattr(variables, "__iter__"):
        raise ValueError('variables must be a list of variables, or of "variable/source"')
    entries = []
    for entry in variables:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(
                f'variables must be a list of variables, or of "variable/source", not {entry!r}'
            )
        parts = [part.strip() for part in entry.split(SEPARATOR)]
        if len(parts) > 2 or not all(parts):
            raise ValueError(
                f'variables takes a variable, or "variable/source", not {entry!r}'
            )
        entries.append(SEPARATOR.join(parts))
    if not entries:
        raise ValueError("variables must name at least one variable to validate")
    return entries


def _matches(entry, variable, source):
    parts = entry.lower().split(SEPARATOR)
    if parts[0] != variable.lower():
        return False
    return len(parts) == 1 or parts[1] == source.lower()


def wanted(variables, variable, source):
    """Whether variables (as check_variables gives it) picks the matchup of
    variable with source."""
    return variables is None or any(_matches(entry, variable, source) for entry in variables)


def check_against(variables, matchups):
    """The matchups (as on_disk gives them) that variables picks. Raises
    ValueError if an entry picks none of them."""
    if variables is None:
        return list(matchups)
    for entry in variables:
        if not any(_matches(entry, m["variable"], m["source"]) for m in matchups):
            there = sorted({m["key"] for m in matchups}, key=str.lower)
            raise ValueError(
                f"variables: there are no matchups of {entry!r}. The matchups are "
                f"{', '.join(there)}"
            )
    return [m for m in matchups if wanted(variables, m["variable"], m["source"])]
