"""The units of each gridded and point matchup, for the oceanval window's units
step, and how to convert the observations into the model's units, where
OceanVal can tell (see oceanval.unit_conversion).

The model's units are read from the netCDF files of the simulation, and the
observations' from RECIPE_CATALOGUE and POINT_RECIPE_CATALOGUE for the recipes
OceanVal ships with, which the Units column of
https://PlymouthMarineLaboratory.github.io/OceanVal/recipes.html repeats, from the recipes
the user registered (see oceanval.user_recipes), or, for the
user's own gridded data, from the netCDF file they supplied. Their own point
data is csv files, which have no units, so those are left to them.
"""

import glob
import math
import os

import xarray as xr

from oceanval.create_recipes import (
    _model_variable_names,
    _WOA23_PERIODS,
    gridded_catalogue,
    point_catalogue,
    simulation_files,
)
from oceanval.parsers import find_recipe
from oceanval.unit_conversion import suggest, unknown

# what to show where the units are not known
NOT_AVAILABLE = None
# what to tell the user about their own point data's units
POINT_NOTE = "csv files have no units: make sure they match the simulation's"


def _description(attributes):
    """A variable's long_name and standard_name, which can tell, e.g., Absolute
    Salinity from Practical, as one string."""
    return " ".join(
        str(attributes[name])
        for name in ("long_name", "standard_name")
        if attributes.get(name) is not None
    )


def model_attributes(simdir, ndown, names, exclude=None, require=None):
    """The units and description of each of the model variables in names.

    Returned as {name: {"units": units, "description": description}}, the
    units None for a variable whose files do not give them, and the
    description its long_name and standard_name. One file per output stream
    is read, as only one has to be.
    """
    wanted = set(names)
    found = {}
    for path in simulation_files(simdir, ndown, exclude, require):
        if wanted <= set(found):
            break
        try:
            # xarray, as raw FVCOM has variables CDO cannot read
            with xr.open_dataset(
                path,
                drop_variables=["siglay", "siglev"],
                decode_times=False,
                decode_cf=False,
            ) as ds:
                for name in wanted - set(found):
                    if name in ds.variables:
                        attributes = ds[name].attrs
                        units = attributes.get("units")
                        found[name] = {
                            "units": str(units) if units is not None else None,
                            "description": _description(attributes),
                        }
        except Exception:
            continue
    return {
        name: found.get(name, {"units": None, "description": ""}) for name in names
    }


def model_units(simdir, ndown, names, exclude=None, require=None):
    """The units attribute of each of the model variables in names.

    Returned as {name: units}, with None for a variable whose files do not
    give units (see model_attributes).
    """
    return {
        name: found["units"]
        for name, found in model_attributes(simdir, ndown, names, exclude, require).items()
    }


def _local_files(obs_path, cwd="."):
    """The files a path or glob names, if they are on this machine."""
    if not isinstance(obs_path, str) or "://" in obs_path:
        return []
    return sorted(glob.glob(os.path.join(cwd, os.path.expanduser(obs_path))))


def own_obs_attributes(arguments, cwd="."):
    """The units of the observations in one of the user's own gridded
    datasets, and their description (long_name and standard_name), read from
    its netCDF file. The units are None where that cannot be done (a URL, a
    THREDDS server, "auto", or a file that cannot be read), and the
    description is then "". A relative path is relative to cwd."""
    variable = arguments.get("obs_variable")
    files = _local_files(arguments.get("obs_path"), cwd)
    if not variable or not files:
        return NOT_AVAILABLE, ""
    try:
        with xr.open_dataset(files[0], decode_times=False, decode_cf=False) as ds:
            attributes = dict(ds[variable].attrs)
    except Exception:
        return NOT_AVAILABLE, ""
    units = attributes.get("units")
    return str(units) if units is not None else None, _description(attributes)


def own_obs_units(arguments, cwd="."):
    """The units of the observations in one of the user's own gridded
    datasets, or None (see own_obs_attributes)."""
    return own_obs_attributes(arguments, cwd)[0]


def _recipe_entry(variable, recipe, point=False, cwd=None):
    catalogue = point_catalogue(cwd) if point else gridded_catalogue(cwd)
    for entry in catalogue:
        if entry["variable"] == variable and entry["recipe"] == recipe:
            return entry
    return None


def recipe_info(variable, recipe, cwd=None):
    """What find_recipe says of a recipe, without the network, or None."""
    # WOA23 temperature and salinity need a decade to name their files
    years = {"start": _WOA23_PERIODS[-2][0], "end": _WOA23_PERIODS[-2][1]}
    try:
        return find_recipe({variable: recipe}, cwd=cwd, **years)
    except Exception:
        return None


def _recipe_obs_variable(variable, recipe, cwd=None):
    """The variable in a recipe's observation files, without the network."""
    found = recipe_info(variable, recipe, cwd)
    if found is None:
        return None
    # ICES observations are named by their parameter code
    return found.get("obs_variable") or found.get("ices_parameter")


def recipe_source(variable, recipe, cwd=None):
    """The source a recipe's matchups are named by, as add_gridded_comparison
    and add_point_comparison take it from find_recipe, without the network,
    or None. It is the same whatever the years."""
    found = recipe_info(variable, recipe, cwd)
    return None if found is None else found.get("source")


def _model_side(model_variable, units):
    names = _model_variable_names(model_variable)
    return {
        "variable": model_variable,
        "parts": [{"name": name, "units": units.get(name)} for name in names],
    }


def _check(variable, obs_units, model, descriptions=None, obs_description=None):
    """What OceanVal makes of a row's units (see unit_conversion.suggest). A sum
    of model variables is checked only if they are all in the same units.
    descriptions is {model variable: its long_name and standard_name}, and
    obs_description the observations', where known."""
    parts = [part["units"] for part in model["parts"]]
    if not parts or None in parts:
        return suggest(variable, obs_units, None)
    # units written the same way are the same, whether OceanVal can read them
    # or not, and ones it cannot read are named, not called different
    written = list(dict.fromkeys(str(units).strip() for units in parts))
    for units in written:
        read = suggest(variable, units, units)
        if read["status"] == "unknown":
            return read
    for other in written[1:]:
        if suggest(variable, other, written[0])["status"] != "same":
            return unknown("the simulation variables in the sum are in different units")
    first = model["parts"][0]["name"]
    return suggest(
        variable,
        obs_units,
        parts[0],
        model_description=(descriptions or {}).get(first),
        obs_description=obs_description,
    )


def _recipe_rows(
    point, mapping, selection, options, model_unit_lookup, cwd=None, descriptions=None
):
    """The rows for the selected recipes of one kind, point or gridded, that
    have a model variable."""
    catalogue = point_catalogue(cwd) if point else gridded_catalogue(cwd)
    order = {entry["variable"]: index for index, entry in enumerate(catalogue)}
    rows = []
    for variable, recipe in sorted(
        selection, key=lambda pair: (order.get(pair[0], 99), pair)
    ):
        entry = _recipe_entry(variable, recipe, point, cwd)
        if entry is None or variable not in mapping:
            # the other kind of recipe, or one with no model variable
            continue
        chosen = options.get((variable, recipe)) or {}
        model = _model_side(mapping[variable], model_unit_lookup)
        rows.append(
            {
                "kind": "point" if point else "gridded",
                "key": f"{'point' if point else 'recipe'}:{variable}:{recipe}",
                "title": f"{variable} ({recipe}{', point' if point else ''})",
                "model": model,
                "obs_variable": _recipe_obs_variable(variable, recipe, cwd),
                "obs_units": entry["units"],
                "obs_multiplier": chosen.get("obs_multiplier", 1),
                "obs_adder": chosen.get("obs_adder", 0),
                "check": _check(variable, entry["units"], model, descriptions),
            }
        )
    return rows


def matchups(
    mapping,
    selection,
    gridded_options,
    point_options,
    own_data,
    model_unit_lookup,
    cwd=".",
    model_descriptions=None,
):
    """One row for each gridded and point matchup the script makes.

    mapping, selection, gridded_options and point_options are what the
    recipes window gave create_recipes, own_data the arguments of the user's
    own calls, and model_unit_lookup {model variable: units}. Each row holds a
    kind ("gridded" or "point"), a key the page sends the conversion back
    under, the matchup's title, the
    model variable with its units (several if it is a sum), the observation
    variable and its units, the obs_multiplier and obs_adder it already
    has, and a check: whether the units are the same, with the conversion
    OceanVal suggests if not (see unit_conversion.suggest), which is kept
    apart from what the script already has. The gridded recipes come first,
    then the point recipes, then the user's own gridded and point data. The units of the user's own point data
    are None, with a note saying so, as csv files have none, and their
    observation variable is the csv's observation column.
    model_descriptions, {model variable: its long_name and standard_name}, is
    what tells the check of Absolute Salinity and Conservative Temperature.
    """
    rows = _recipe_rows(
        False, mapping, selection, gridded_options, model_unit_lookup, cwd, model_descriptions
    )
    rows += _recipe_rows(
        True, mapping, selection, point_options, model_unit_lookup, cwd, model_descriptions
    )
    for kind, prefix in (("gridded", "own"), ("point", "ownpoint")):
        point = kind == "point"
        for index, arguments in enumerate(own_data.get(kind) or []):
            what = "your own point data" if point else "your own data"
            model = _model_side(arguments.get("model_variable", ""), model_unit_lookup)
            obs_units, obs_description = (
                (None, "") if point else own_obs_attributes(arguments, cwd)
            )
            rows.append(
                {
                    "kind": kind,
                    "key": f"{prefix}:{index}",
                    "title": f"{arguments.get('name')} ({what})",
                    "model": model,
                    "obs_variable": "observation" if point else arguments.get("obs_variable"),
                    "obs_units": obs_units,
                    "obs_note": POINT_NOTE if point else None,
                    "obs_multiplier": arguments.get("obs_multiplier", 1),
                    "obs_adder": arguments.get("obs_adder", 0),
                    # its name is the variable, if it is one OceanVal knows
                    "check": _check(
                        arguments.get("name"),
                        obs_units,
                        model,
                        model_descriptions,
                        obs_description,
                    ),
                }
            )
    return rows


def model_variables(mapping, selection, own_data, cwd=None):
    """Every model variable named by a gridded or point matchup, so their
    units can be read in one go."""
    names = []
    for variable, recipe in selection:
        if variable in mapping and (
            _recipe_entry(variable, recipe, cwd=cwd) is not None
            or _recipe_entry(variable, recipe, point=True, cwd=cwd) is not None
        ):
            names.extend(_model_variable_names(mapping[variable]))
    for kind in ("gridded", "point"):
        for arguments in own_data.get(kind) or []:
            if arguments.get("model_variable"):
                names.extend(_model_variable_names(arguments["model_variable"]))
    return list(dict.fromkeys(names))


def check_conversions(sent):
    """Turn what the page sent for each matchup into numbers.

    sent is {key: {"multiplier": text, "adder": text}}; a blank box leaves
    the default, 1 and 0. Returns ({key: (multiplier, adder)}, {key: message}).
    """
    conversions, errors = {}, {}
    sent = sent if isinstance(sent, dict) else {}
    for key, boxes in sent.items():
        boxes = boxes if isinstance(boxes, dict) else {}
        values = {}
        for name, default in (("multiplier", 1), ("adder", 0)):
            text = str(boxes.get(name, "")).strip()
            if not text:
                values[name] = default
                continue
            try:
                value = float(text)
            except ValueError:
                value = math.nan
            if not math.isfinite(value) or (name == "multiplier" and value == 0):
                errors[key] = (
                    "Multiply by a number that is not 0, and add a number."
                )
                break
            values[name] = int(value) if value == int(value) else value
        else:
            conversions[key] = (values["multiplier"], values["adder"])
    return conversions, errors


def apply_conversions(conversions, gridded_options, own_data, point_options=None):
    """Put the conversions into the gridded and point options and the own
    data they belong to, in place. A conversion of 1 and 0 leaves the
    argument out."""
    point_options = {} if point_options is None else point_options
    for key, (multiplier, adder) in conversions.items():
        kind, _, rest = key.partition(":")
        all_options = None
        if kind in ("recipe", "point"):
            variable, _, recipe = rest.partition(":")
            all_options = gridded_options if kind == "recipe" else point_options
            options = all_options.setdefault((variable, recipe), {})
        elif kind in ("own", "ownpoint"):
            entries = own_data.get("gridded" if kind == "own" else "point") or []
            if not rest.isdigit() or int(rest) >= len(entries):
                continue
            options = entries[int(rest)]
        else:
            continue
        for name, value, default in (
            ("obs_multiplier", multiplier, 1),
            ("obs_adder", adder, 0),
        ):
            if value == default:
                options.pop(name, None)
            else:
                options[name] = value
        if all_options is not None and all(value is None for value in options.values()):
            del all_options[(variable, recipe)]
