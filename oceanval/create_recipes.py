"""Write a ready-to-run matchup script for one simulation.

``create_recipes`` scans a simulation directory, works out which model
variable holds each observational variable OceanVal has a recipe for, and
writes out the script in ``docs-site/recipe-examples.py`` with those names
filled in. Recipes it could not find a model variable for are still written
out, but commented, so nothing is silently dropped. When run interactively,
it first asks you for the model variable of each one it could not identify.
Output that looks like raw FVCOM is read with xarray, as CDO skips FVCOM's
mesh variables, and the script's matchup call is given fvcom=True - once you
have confirmed it is FVCOM. By default (gui=True), what was identified is
shown in a page in your web browser instead (see oceanval.recipes_gui), where
the model variables, the datasets to validate against, and each dataset's
years and whether to validate it through the water column can be changed
before the script is written.

The identification follows the approach ecoval takes in its ``matchup``:
model output rarely names its variables the way an observational dataset
does, so the match is made on the variables' ``long_name`` attributes
rather than on their names.
"""

import functools
import glob
import inspect
import json
import os
import re
import textwrap
import warnings

from oceanval.quiet_nc import nc
import xarray as xr

from oceanval import prompts
from oceanval import user_recipes
from oceanval import live as interim_report
from oceanval.fvcom import fvcom_contents
from oceanval.own_data import FIELDS as OWN_DATA_FIELDS


# The region argument on create_recipes, keyed by the catalogue's own
# "region" label (used for the section heading each recipe is printed
# under).
DOMAIN_REGIONS = {
    "global": "Global",
    "nwes": "Northwest European Shelf",
}

# Every recipe on https://PlymouthMarineLaboratory.github.io/OceanVal/recipe-examples.py,
# in the order that page lists them. "arguments" are the source lines of the
# add_gridded_comparison call that follow the recipe, and "example_variable"
# is the placeholder name the page uses, kept for the commented-out blocks.
RECIPE_CATALOGUE = (
    {
        "variable": "alkalinity",
        "recipe": "glodap",
        "region": "Global",
        "example_variable": "talk",
        "notes": (
            "Alkalinity - GLODAPv2.2016b (https://www.glodap.info/)",
            "recipe: 'glodap'",
            "Annual climatology covering 1972–2013, surface-only. Reported in",
            "micromoles per kilogram. See the GLODAPv2 reference",
            "(https://doi.org/10.5194/essd-8-325-2016).",
        ),
        "arguments": ("climatology=True,",),
    },
    {
        "variable": "chlorophyll",
        "recipe": "occci",
        "region": "Global",
        "example_variable": "chl",
        "notes": (
            "Chlorophyll - Ocean Colour CCI (https://esa-oceancolour-cci.org/)",
            "recipe: 'occci'",
            "Surface chlorophyll, reported in milligrams per cubic metre. The occci",
            "recipe also provides KD490.",
        ),
        "arguments": ("climatology=False,",),
    },
    {
        "variable": "kd490",
        "recipe": "occci",
        "region": "Global",
        "example_variable": "kd490",
        "notes": (
            "KD490 - Ocean Colour CCI (https://esa-oceancolour-cci.org/)",
            "recipe: 'occci'",
            "Diffuse attenuation coefficient at 490 nm, reported in inverse metres.",
        ),
        "arguments": ("climatology=False,",),
    },
    {
        "variable": "nitrate",
        "recipe": "woa23",
        "region": "Global",
        "example_variable": "no3",
        "notes": (
            "Nitrate - World Ocean Atlas 2023 "
            "(https://www.ncei.noaa.gov/products/world-ocean-atlas)",
            "recipe: 'woa23'",
            "Vertically resolved to ~800 m. WOA23 nutrients use a single long-term",
            "climatology — see the WOA23 note below for temperature/salinity decadal",
            "periods.",
        ),
        "arguments": (
            "climatology=True,",
            "vertical=False,  # set True to validate the full water column",
        ),
    },
    {
        "variable": "oxygen",
        "recipe": "woa23",
        "region": "Global",
        "example_variable": "o2",
        "notes": (
            "Oxygen - World Ocean Atlas 2023 "
            "(https://www.ncei.noaa.gov/products/world-ocean-atlas)",
            "recipe: 'woa23'",
            "Vertically resolved to ~800 m.",
        ),
        "arguments": (
            "climatology=True,",
            "vertical=False,  # set True to validate the full water column",
        ),
    },
    {
        "variable": "ph",
        "recipe": "glodap",
        "region": "Global",
        "example_variable": "ph",
        "notes": (
            "pH - GLODAPv2.2016b (https://www.glodap.info/)",
            "recipe: 'glodap'",
            "Annual climatology, 1972–2013, surface-only. Reported on the total scale.",
        ),
        "arguments": ("climatology=True,",),
    },
    {
        "variable": "phosphate",
        "recipe": "woa23",
        "region": "Global",
        "example_variable": "po4",
        "notes": (
            "Phosphate - World Ocean Atlas 2023 "
            "(https://www.ncei.noaa.gov/products/world-ocean-atlas)",
            "recipe: 'woa23'",
            "Vertically resolved to ~800 m.",
        ),
        "arguments": (
            "climatology=True,",
            "vertical=False,  # set True to validate the full water column",
        ),
    },
    {
        "variable": "salinity",
        "recipe": "woa23",
        "region": "Global",
        "example_variable": "so",
        "decadal": True,
        "notes": (
            "Salinity - World Ocean Atlas 2023 "
            "(https://www.ncei.noaa.gov/products/world-ocean-atlas)",
            "recipe: 'woa23'",
            "Uses a decadal climatology — start/end must fall within one supported",
            "period (see below).",
        ),
        "arguments": (
            "climatology=True,",
            "vertical=False,  # set True to validate the full water column",
        ),
    },
    {
        "variable": "silicate",
        "recipe": "woa23",
        "region": "Global",
        "example_variable": "si",
        "notes": (
            "Silicate - World Ocean Atlas 2023 "
            "(https://www.ncei.noaa.gov/products/world-ocean-atlas)",
            "recipe: 'woa23'",
            "Vertically resolved to ~800 m.",
        ),
        "arguments": (
            "climatology=True,",
            "vertical=False,  # set True to validate the full water column",
        ),
    },
    {
        "variable": "temperature",
        "recipe": "cobe2",
        "region": "Global",
        "example_variable": "thetao",
        "notes": (
            "Temperature - COBE-SST 2 (https://psl.noaa.gov/data/gridded/data.cobe2.html)",
            "recipe: 'cobe2'",
            "Monthly sea surface temperature, useful for validating against a full time",
            "series rather than a climatology.",
        ),
        "arguments": ("climatology=False,",),
    },
    {
        "variable": "temperature",
        "recipe": "woa23",
        "region": "Global",
        "example_variable": "thetao",
        "decadal": True,
        "notes": (
            "Temperature - World Ocean Atlas 2023 "
            "(https://www.ncei.noaa.gov/products/world-ocean-atlas)",
            "recipe: 'woa23'",
            "Uses a decadal climatology, vertically resolved to ~800 m. start/end must",
            "fall within one supported period (see below).",
        ),
        "arguments": (
            "climatology=True,",
            "vertical=False,  # set True to validate the full water column",
        ),
    },
)

_SHELF_RECIPES = (
    ("ammonium", "Ammonium"),
    ("chlorophyll", "Chlorophyll"),
    ("nitrate", "Nitrate"),
    ("oxygen", "Oxygen"),
    ("phosphate", "Phosphate"),
    ("salinity", "Salinity"),
    ("silicate", "Silicate"),
    ("temperature", "Temperature"),
)

RECIPE_CATALOGUE = RECIPE_CATALOGUE + tuple(
    {
        "variable": variable,
        "recipe": "nsbc",
        "region": "Northwest European Shelf",
        "example_variable": variable,
        "notes": (
            f"{title} - North Sea Biogeochemical Climatology",
            "recipe: 'nsbc'",
        ),
        "arguments": (
            "climatology=True,",
            "vertical=False,  # set True to validate the full water column",
        ),
    }
    for variable, title in _SHELF_RECIPES
)

# The units each gridded recipe's observations are in, as the observation files
# state them (those for temperature in the WOA23 and NSBC recipes, and for KD490,
# are the documented ones, as their files are not held locally). They are shown
# in the units step of the oceanval window and in the Units column of
# https://PlymouthMarineLaboratory.github.io/OceanVal/recipes.html, which a test keeps the same.
_GRIDDED_UNITS = {
    ("alkalinity", "glodap"): "micro-mol kg-1",
    ("chlorophyll", "occci"): "milligram m-3",
    ("kd490", "occci"): "m-1",
    ("nitrate", "woa23"): "micromoles_per_kilogram",
    ("oxygen", "woa23"): "micromoles_per_kilogram",
    ("ph", "glodap"): "total scale",
    ("phosphate", "woa23"): "micromoles_per_kilogram",
    ("salinity", "woa23"): "1",
    ("silicate", "woa23"): "micromoles_per_kilogram",
    ("temperature", "cobe2"): "degC",
    ("temperature", "woa23"): "degC",
    ("ammonium", "nsbc"): "mmol/m^3",
    ("chlorophyll", "nsbc"): "mg/m^3",
    ("nitrate", "nsbc"): "mmol/m^3",
    ("oxygen", "nsbc"): "mmol/m^3",
    ("phosphate", "nsbc"): "mmol/m^3",
    ("salinity", "nsbc"): "1",
    ("silicate", "nsbc"): "mmol/m^3",
    ("temperature", "nsbc"): "degC",
}

# The years each gridded recipe's observations cover, and the web page about
# them, which the create_recipes window shows in the i beside each source. The
# climatologies are averages over these years: NSBC's from its files' names and
# ICDC's page, WOA23's and GLODAPv2.2016b's from their documentation (WOA23's
# nutrients are from bottles alone, its oxygen from bottles, CTDs and floats).
_WOA23_PAGE = "https://www.ncei.noaa.gov/products/world-ocean-atlas"
_GLODAP_PAGE = "https://www.glodap.info/index.php/mapped-data-product/"
_COVERAGE = {
    "glodap": {
        "years": "1972–2013",
        "note": "A climatology of the data from these years, normalised to 2002.",
        "url": _GLODAP_PAGE,
    },
    "occci": {
        "years": "1997 to the present",
        "note": "Monthly observations, not a climatology.",
        "url": "https://www.oceancolour.org/",
    },
    "cobe2": {
        "years": "1850 to the present",
        "note": "Monthly observations, not a climatology.",
        "url": "https://psl.noaa.gov/data/gridded/data.cobe2.html",
    },
    "woa23": {
        "years": "1965–2022",
        "note": "A climatology of all the data from these years.",
        "url": _WOA23_PAGE,
    },
    "nsbc": {
        "years": "1960–2014",
        "note": "A climatology of the data from these years.",
        "url": "https://www.geo.uni-hamburg.de/en/service/icdc/data/ocean/nsbc.html",
    },
}
# the decadal periods WOA23 publishes temperature and salinity for, as
# find_recipe() understands them
_WOA23_PERIODS = (
    (1955, 1964),
    (1965, 1974),
    (1975, 1984),
    (1985, 1994),
    (1995, 2004),
    (2005, 2014),
    (2015, 2022),
)

# WOA23's temperature and salinity are published for each decade
_DECADAL_COVERAGE = {
    "years": "1955–2022, as a climatology of each decade: "
    + ", ".join(f"{first}–{last}" for first, last in _WOA23_PERIODS),
    "note": "The decade used is the one the years given for it sit inside.",
    "url": _WOA23_PAGE,
}

RECIPE_CATALOGUE = tuple(
    dict(
        entry,
        units=_GRIDDED_UNITS[entry["variable"], entry["recipe"]],
        coverage=_DECADAL_COVERAGE if entry.get("decadal") else _COVERAGE[entry["recipe"]],
    )
    for entry in RECIPE_CATALOGUE
)

# ICES point (in-situ) recipes. Unlike the gridded catalogue above, these are
# only written into the generated script for domain="nwes", or when one is
# selected by hand (see build_recipe_script), rather than for every domain
# commented-out - ICES has no global equivalent, so there is nothing useful
# to show a global-domain user.
_ICES_RECIPES = (
    ("temperature", "Temperature", "CTD profiles and bottle data", "degrees Celsius"),
    ("salinity", "Salinity", "CTD profiles and bottle data", "practical salinity units"),
    ("alkalinity", "Total Alkalinity", "Bottle and low resolution CTD data", "milliequivalents per litre"),
    ("ammonium", "Ammonium", "Bottle and low resolution CTD data", "micromoles per litre"),
    ("chlorophyll", "Chlorophyll", "Bottle and low resolution CTD data", "micrograms per litre"),
    ("nitrate", "Nitrate", "Bottle and low resolution CTD data", "micromoles per litre"),
    ("oxygen", "Oxygen", "Bottle and low resolution CTD data", "millilitres per litre"),
    ("ph", "pH", "Bottle and low resolution CTD data", "pH units"),
    ("phosphate", "Phosphate", "Bottle and low resolution CTD data", "micromoles per litre"),
    ("silicate", "Silicate", "Bottle and low resolution CTD data", "micromoles per litre"),
)

# the units of the ICES observations, as the Units column of the point recipes
# table at https://PlymouthMarineLaboratory.github.io/OceanVal/recipes.html gives them,
# which a test keeps the same
_POINT_UNITS = {
    "temperature": "\u00b0C",
    "salinity": "dimensionless",
    "alkalinity": "mEq/l",
    "ammonium": "\u00b5mol/l",
    "chlorophyll": "\u00b5g/l",
    "nitrate": "\u00b5mol/l",
    "oxygen": "ml/l",
    "ph": "pH units",
    "phosphate": "\u00b5mol/l",
    "silicate": "\u00b5mol/l",
}

POINT_RECIPE_CATALOGUE = tuple(
    {
        "variable": variable,
        "recipe": "ices",
        "units": _POINT_UNITS[variable],
        "example_variable": variable,
        "notes": (
            f"{title} - ICES Oceanographic database (https://ocean.ices.dk)",
            "recipe: 'ices'",
            f"{dataset}, reported in {units}.",
            "Only observations with good (quality flag 0) flags are kept.",
        ),
        "arguments": ("vertical=False,  # set True to validate the full water column",),
    }
    for variable, title, dataset, units in _ICES_RECIPES
)

# the observational variables the catalogues can match a model variable to
RECIPE_VARIABLES = tuple(
    dict.fromkeys(
        entry["variable"] for entry in RECIPE_CATALOGUE + POINT_RECIPE_CATALOGUE
    )
)

# where the recipes the user registered are listed, in place of a region
USER_REGION = "Your recipes"


def _user_entry(recipe, cwd=None):
    """The catalogue entry for one recipe in a .oceanvalrc file: as the
    built-in ones, with "user" set, the key for "recipe" and the source for
    its "label"."""
    variable, key = recipe["variable"], recipe["key"]
    short_name, long_name, title = (
        user_recipes.labels(variable, cwd) or (variable, variable, variable.title())
    )
    point = recipe["kind"] == "point"
    units = recipe.get("units")
    notes = [
        f"{title} - {recipe['source']} (your recipe, in {recipe['path']})",
        f"recipe: '{key}'",
        *user_recipes.wrap(recipe.get("source_info") or ""),
    ]
    if units:
        notes.append(f"Reported in {units}.")
    arguments = ()
    if recipe.get("depth_resolved"):
        arguments = ("vertical=False,  # set True to validate the full water column",)
    return {
        "variable": variable,
        "recipe": key,
        "region": USER_REGION,
        "label": recipe["source"],
        "title": title,
        "long_name": long_name,
        "example_variable": variable,
        "notes": tuple(notes),
        "arguments": arguments,
        "units": units,
        "user": True,
        "climatology": bool(recipe.get("climatology")) and not point,
        "where": recipe["where"],
        "path": recipe["path"],
        "point": point,
    }


def _user_entries(cwd=None):
    return [_user_entry(recipe, cwd) for recipe in user_recipes.load(cwd)]


def gridded_catalogue(cwd=None):
    """RECIPE_CATALOGUE, and then the user's own gridded recipes (see
    oceanval.user_recipes) for the directory cwd, the one worked in by
    default."""
    return RECIPE_CATALOGUE + tuple(e for e in _user_entries(cwd) if not e["point"])


def point_catalogue(cwd=None):
    """POINT_RECIPE_CATALOGUE, and then the user's own point recipes."""
    return POINT_RECIPE_CATALOGUE + tuple(e for e in _user_entries(cwd) if e["point"])


def recipe_variables(cwd=None):
    """RECIPE_VARIABLES, and then the variables only the user's own recipes
    are for."""
    return RECIPE_VARIABLES + tuple(
        variable
        for variable in dict.fromkeys(e["variable"] for e in _user_entries(cwd))
        if variable not in RECIPE_VARIABLES
    )


# The matchup() and validate() arguments the create_recipes window can set, in
# the order they are written into the script. Only ones changed from the
# function's own default are written. time_shift has no box: the oceanval
# window writes the answer to matchup's question about the model's times
MATCHUP_SETTINGS = (
    "lon_lim",
    "lat_lim",
    "cores",
    "thickness",
    "point_time_res",
    "time_shift",
    "overwrite",
    "ask",
    "out_dir",
    "exclude",
    "require",
    "as_missing",
    "fvcom_res",
    "fvcom_min_depth",
    "fvcom_max_depth",
    "fvcom_dz",
    "domain_mask",
)
VALIDATE_SETTINGS = ("subregions", "pdf", "word", "concise")


def _defaults(function, names):
    parameters = inspect.signature(function).parameters
    return {name: parameters[name].default for name in names}


def matchup_defaults():
    """matchup()'s own default for each argument the window can set."""
    from oceanval.matchall import matchup

    return _defaults(matchup, MATCHUP_SETTINGS)


def validate_defaults():
    """validate()'s own default for each argument the window can set."""
    # validate is defined in oceanval/__init__.py, after this module is imported
    import oceanval

    return _defaults(oceanval.validate, VALIDATE_SETTINGS)


def _changed_settings(settings):
    """The settings that differ from matchup's and validate's defaults.

    Returned as ([(name, value)], [(name, value)]), matchup's then validate's.
    """
    if not settings:
        return [], []
    return tuple(
        [
            (name, settings[name])
            for name, default in defaults.items()
            if name in settings and settings[name] != default
        ]
        for defaults in (matchup_defaults(), validate_defaults())
    )


def _literal(value):
    """A setting's value as Python source: 5 rather than 5.0, and strings in
    double quotes like the rest of the script."""
    if value is None or isinstance(value, bool):
        return repr(value)
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return (
            "{"
            + ", ".join(f"{_literal(key)}: {_literal(item)}" for key, item in value.items())
            + "}"
        )
    return "[" + ", ".join(_literal(item) for item in value) + "]"


def _long_names(contents):
    return [str(name) for name in contents.long_name]


def _matching_variables(contents, long_names):
    """The model variables whose long_name is one of long_names."""
    matched = contents.loc[[name in long_names for name in _long_names(contents)]]
    return list(dict.fromkeys(matched.variable))


def _candidate_long_names(variable, contents):
    """The long_names in a file that describe one observational variable.

    This is ecoval's generate_mapping, narrowed to the variables OceanVal
    has recipes for. Each rule leans on how the long_name is worded, and
    rules out the benthic and riverine variables that are named after the
    same quantity.
    """
    names = _long_names(contents)

    def plain(term, *excluded):
        # sea ice's own salinity and temperature are output beside the
        # ocean's, on a category axis that makes them look like water column
        return [
            name
            for name in names
            if term in name.lower()
            and not any(word in name.lower() for word in ("benthic", "river"))
            and not re.search(r"\b(?:ice|snow)\b", name.lower())
            and not any(word in name.lower() for word in excluded)
        ]

    if variable == "temperature":
        # air temperature is stored alongside sea temperature in coupled runs
        return plain("temperature", "air")

    if variable == "oxygen":
        return plain("oxygen", "saturation", "util", "flux")

    if variable == "silicate":
        # some models name it after the silicic acid rather than the salt
        matches = plain("silicate")
        return matches + [name for name in plain("silicic") if name not in matches]

    if variable == "ph":
        # ecoval's own rule reads ("pH" in x) or (" ph " in x) and ..., which
        # binds so that a benthic pH slips through; the exclusions belong to
        # both spellings
        return [
            name
            for name in names
            if ("pH" in name or " ph " in name.lower())
            and not any(word in name.lower() for word in ("benthic", "river"))
        ]

    if variable == "kd490":
        return [
            name
            for name in names
            if "atten" in name.lower() and "coeff" in name.lower()
        ]

    if variable == "chlorophyll":
        matches = plain("chlorophyll")
        if len(matches) > 1:
            # a model reporting a total as well as its phytoplankton types
            # would otherwise double count
            matches = [name for name in matches if "total" not in name.lower()]
        return matches

    return plain(variable)


def _nitrate_fallback(contents, mapping):
    """Use a single nitrogen nutrient variable when nitrate is not named."""
    if mapping.get("nitrate") is not None or mapping.get("ammonium") is not None:
        return None
    nitrogen = contents.loc[
        [
            "nitrogen" in name.lower() and "nutrient" in name.lower()
            for name in _long_names(contents)
        ]
    ]
    if len(nitrogen) != 1:
        return None
    warnings.warn("No nitrate variable found, using the nitrogen nutrient variable")
    return list(nitrogen.variable)[0]


def _user_variable_match(variable, long_name, contents):
    """The one model variable that is, or describes, a variable only the
    user's own recipes are for, or None.

    There are no rules for such a variable to go by, so it is the model
    variable named for it, or else the one whose long_name holds its long
    name, and only if there is just one: a guess that could be wrong is
    left for the user to make.
    """
    names = list(dict.fromkeys(contents.variable))
    named = [name for name in names if str(name).lower() == variable.lower()]
    if len(named) == 1:
        return named[0]
    phrase = long_name.lower()
    matches = [
        name
        for name, described in zip(contents.variable, _long_names(contents))
        if phrase
        and phrase in described.lower()
        and not any(word in described.lower() for word in ("benthic", "river"))
    ]
    matches = list(dict.fromkeys(matches))
    return matches[0] if len(matches) == 1 else None


def generate_recipe_mapping(path, fvcom=False, cwd=None):
    """Map each recipe variable to the model variable holding it in one file.

    Returns a dict keyed by the observational variable name. Variables the
    file does not hold, and ones it describes ambiguously, map to None.
    fvcom=True reads a raw FVCOM file. cwd is the directory the user's own
    recipes are read for (see oceanval.user_recipes).
    """
    return _recipe_mapping(path, fvcom, cwd)[0]


def _recipe_mapping(path, fvcom=False, cwd=None):
    """generate_recipe_mapping's mapping, and the names of the file's
    variables with more than one vertical level."""
    if fvcom:
        # CDO skips the variables on FVCOM's mesh, temperature and salinity
        # among them
        contents = fvcom_contents(path)
    else:
        contents = nc.open_data(path, checks=False).contents
    return contents_mapping(contents, cwd)


def contents_mapping(contents, cwd=None):
    """_recipe_mapping's mapping and water-column variables, from a file's
    contents as nctoolkit gives them (variable, long_name and nlevels)."""
    contents = contents.assign(long_name=[str(x) for x in contents.long_name])

    surface_contents = contents.query("nlevels == 1").reset_index(drop=True)
    depth_contents = contents.query("nlevels > 1").reset_index(drop=True)
    # the water column is searched first: a model reporting both a 3D field
    # and a surface diagnostic of the same quantity means the 3D one, and
    # searching the two together would only make the pair look ambiguous
    searches = [depth_contents, surface_contents]

    mapping = {}
    for variable in RECIPE_VARIABLES:
        mapping[variable] = None
        for search in searches:
            if len(search) == 0:
                continue
            model_variables = _matching_variables(
                search, _candidate_long_names(variable, search)
            )
            if len(model_variables) > 1 and variable != "chlorophyll":
                # no way to tell which of them the recipe wants
                break
            if model_variables:
                mapping[variable] = "+".join(model_variables)
                break

    nitrogen_source = depth_contents if len(depth_contents) > 0 else surface_contents
    fallback = _nitrate_fallback(nitrogen_source, mapping)
    if fallback is not None:
        mapping["nitrate"] = fallback

    # variables only the user's own recipes are for
    for variable in recipe_variables(cwd):
        if variable in mapping:
            continue
        mapping[variable] = None
        long_name = (user_recipes.labels(variable, cwd) or (None, variable))[1]
        for search in searches:
            if len(search) == 0:
                continue
            found = _user_variable_match(variable, long_name, search)
            if found is not None:
                mapping[variable] = found
                break

    # these carry no long_name worth matching on, so they go by name
    variables = list(contents.variable)
    for variable, model_variable in (
        ("temperature", "votemper"),
        ("salinity", "vosaline"),
        ("ph", "ph"),
    ):
        if mapping.get(variable) is None and model_variable in variables:
            mapping[variable] = model_variable

    return mapping, set(depth_contents.variable)


def _file_pattern(path):
    """A file name with its dates blanked out, so one stream groups together."""
    name = os.path.basename(path)
    name = re.sub(r"\d{4,}", "**", name)
    return re.sub(r"\d{2,}", "**", name)


def _passes_filters(path, exclude=None, require=None):
    """Whether matchup() would use a file, given its exclude and require.

    As there, only the file's name is checked: it is left out if it contains
    any word in exclude, or if it lacks any word in require.
    """
    name = os.path.basename(path)
    return not any(word in name for word in exclude or ()) and all(
        word in name for word in require or ()
    )


def simulation_paths(simdir, ndown, exclude=None, require=None):
    """Every output file ndown directories below simdir, leaving out those
    matchup() would, given its exclude and require."""
    pattern = os.path.join(simdir, *(["*"] * ndown), "*.nc")
    return [
        path
        for path in sorted(glob.glob(pattern))
        if _passes_filters(path, exclude, require)
    ]


_YEAR_DIR = re.compile(r"\d{4}")
_MONTH_DIRS = [f"{month:02d}" for month in range(1, 13)]


def _has_nc(directory):
    """Whether directory holds an output file."""
    with os.scandir(directory) as entries:
        return any(entry.name.endswith(".nc") for entry in entries if entry.is_file())


def folder_layout(simdir):
    """The layout of the output in simdir that tells how many directories
    down its files are, or None if it is not one OceanVal knows.

    {"pattern": "YYYY/MM/**.nc", "ndown": 2}: a year directory (four digits) with all
    twelve month directories, 01 to 12, one of which has output files.
    {"pattern": "MM/*.nc", "ndown": 1}: the twelve month directories, 01 to 12, in
    simdir itself, one of which has output files.
    {"pattern": "*.nc", "ndown": 0}: no subdirectories (those starting with a dot
    are not counted) and output files in the directory itself.

    Only a few directories are listed, as simdir may be on a network drive.
    Never raises.
    """
    try:
        with os.scandir(simdir) as entries:
            entries = sorted(entries, key=lambda entry: entry.name)
        directories = [
            entry for entry in entries if entry.is_dir() and not entry.name.startswith(".")
        ]
        if not directories:
            return {"pattern": "*.nc", "ndown": 0} if _has_nc(simdir) else None
        if set(_MONTH_DIRS) <= {entry.name for entry in directories}:
            if any(_has_nc(os.path.join(simdir, month)) for month in _MONTH_DIRS):
                return {"pattern": "MM/*.nc", "ndown": 1}
        for year in directories:
            if not _YEAR_DIR.fullmatch(year.name):
                continue
            with os.scandir(year.path) as inside:
                months = {entry.name for entry in inside if entry.is_dir()}
            if not set(_MONTH_DIRS) <= months:
                continue
            if any(_has_nc(os.path.join(year.path, month)) for month in _MONTH_DIRS):
                return {"pattern": "YYYY/MM/**.nc", "ndown": 2}
    except OSError:
        pass
    return None


def simulation_files(simdir, ndown, exclude=None, require=None):
    """One example file per output stream, ndown directories below simdir.

    A simulation holds one file per stream per time step, and every step
    writes the same variables, so only one file per stream has to be read.
    """
    examples = {}
    for path in simulation_paths(simdir, ndown, exclude, require):
        examples.setdefault(_file_pattern(path), path)
    return list(examples.values())


def extract_recipe_variable_mapping(
    simdir, ndown, fvcom=False, exclude=None, require=None, cwd=None
):
    """Work out which model variable holds each recipe variable.

    Scans one file per output stream and keeps the model variable that the
    most streams agree on, so a diagnostic file holding a stray copy of a
    variable cannot outvote the stream that is actually reporting it.
    fvcom=True reads the files as raw FVCOM output, and exclude and require
    leave out the files matchup() would. cwd is the directory the user's own
    recipes are read for.
    """
    paths = simulation_files(simdir, ndown, exclude, require)
    if not paths:
        filtered = " that pass the exclude and require filters" if exclude or require else ""
        raise ValueError(
            f"No netCDF files were found {ndown} directories below {simdir}{filtered}. "
            "Check the ndown argument and the simulation directory structure."
        )

    found = []
    for path in paths:
        try:
            found.append(_recipe_mapping(path, fvcom, cwd))
        except Exception:
            # a stream OceanVal cannot read is not a reason to give up on
            # the rest of the simulation
            continue
    return voted_mapping(found, cwd)


def voted_mapping(found, cwd=None):
    """The model variable most of the streams agree on for each recipe
    variable, from each stream's (mapping, water-column variables), as
    _recipe_mapping gives them."""
    votes = {variable: {} for variable in recipe_variables(cwd)}
    # the model variables seen with more than one vertical level
    water_column = set()
    for mapping, depth_variables in found:
        for variable, model_variable in mapping.items():
            if model_variable is None:
                continue
            counts = votes[variable]
            counts[model_variable] = counts.get(model_variable, 0) + 1
            if model_variable.split("+")[0] in depth_variables:
                water_column.add(model_variable)

    mapping = {}
    for variable, counts in votes.items():
        if not counts:
            continue
        # most streams first, then on a tie the water column over a surface
        # field (a 3D temperature over a sea surface temperature, which
        # cannot be matched at depth), then alphabetically, so the result is
        # the same every time the generator is run over the same simulation
        mapping[variable] = sorted(
            counts, key=lambda name: (-counts[name], name not in water_column, name)
        )[0]
    return mapping


def _variable_long_names(simdir, ndown, exclude=None, require=None):
    """Every variable in the simulation's example files, with its long_name.

    The long_name is "" for a variable without one.
    """
    names = {}
    for path in simulation_files(simdir, ndown, exclude, require):
        # xarray rather than nctoolkit: CDO skips variables on grids it does
        # not support (e.g. raw FVCOM salinity), which would then be refused
        try:
            with xr.open_dataset(path, decode_times=False, decode_cf=False) as ds:
                found = {
                    str(name): str(variable.attrs.get("long_name", ""))
                    for name, variable in ds.variables.items()
                }
        except Exception:
            try:
                contents = nc.open_data(path, checks=False).contents
                found = {
                    str(name): "" if str(long_name) == "nan" else str(long_name)
                    for name, long_name in zip(contents.variable, contents.long_name)
                }
            except Exception:
                continue
        for name, long_name in found.items():
            if long_name or name not in names:
                names[name] = long_name
    return names


def _available_variables(simdir, ndown, exclude=None, require=None):
    """The names of every variable in the simulation's example files."""
    return set(_variable_long_names(simdir, ndown, exclude, require))


def _model_variable_names(answer):
    """The model variables named in one answer, several joined with "+"."""
    return [name.strip() for name in answer.split("+")]


def _unknown_variables(answer, available):
    """The names in one answer that are not in the model output."""
    return [name for name in _model_variable_names(answer) if name not in available]


def _ask_for_missing_variables(mapping, missing, available):
    """Offer the user the chance to name the model variable for each gap.

    Blank input skips a variable, leaving its recipes commented out. Several
    model variables can be given joined with "+", as the automatic
    identification does.
    """
    mapping = dict(mapping)
    for variable in missing:
        print(f"{variable} could not be identified in the model output.")
        while True:
            try:
                answer = prompts.ask(
                    f"What is the model variable for {variable}? (press Enter to skip): "
                ).strip()
            except EOFError:
                return mapping
            if not answer:
                break
            unknown = _unknown_variables(answer, available)
            if unknown:
                print(f"{', '.join(unknown)} not found in the model output")
                continue
            mapping[variable] = "+".join(_model_variable_names(answer))
            break
    return mapping


def _looks_like_fvcom(path):
    """Whether one file looks like raw FVCOM output.

    Goes by the unstructured mesh, which matchup(fvcom=True) regrids from,
    rather than the "source" attribute, which FVCOM output that has already
    been regridded still carries.
    """
    try:
        # siglay and siglev are 2D but named after their dimension
        with xr.open_dataset(
            path,
            drop_variables=["siglay", "siglev"],
            decode_times=False,
            decode_cf=False,
        ) as ds:
            return {"node", "nele"} <= set(ds.dims) and "nv" in ds.variables
    except Exception:
        return False


def _ask_if_fvcom(simdir):
    """Ask the user to confirm that output which looks like FVCOM is."""
    # in the question itself, as a window shows the question and not what was printed
    question = (
        f"The output in {simdir} looks like raw FVCOM output "
        "(an unstructured mesh with node, nele and nv).\n"
        "Is this FVCOM output? (y/n) "
    )
    while True:
        try:
            answer = prompts.ask(question, ("y", "n")).strip().lower()
        except EOFError:
            return True
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Provide y or n")


def _years_in(text):
    """The years named in one path, whether on their own or inside a date."""
    for match in re.findall(r"(?<!\d)(\d{4})(?!\d)", text):
        yield int(match)
    # NEMO and friends name files by yyyymmdd rather than filing them by year
    for match in re.findall(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)", text):
        year, month, day = (int(part) for part in match)
        if 1 <= month <= 12 and 1 <= day <= 31:
            yield year


def simulation_years(simdir, paths):
    """The first and last year named in a simulation's paths, if any.

    Model output is almost always filed under a year, or names one, and the
    matchup needs a start and an end year to run at all. Only the part of
    each path below simdir is read, so a year in the directory the user
    happens to keep their simulations in cannot be mistaken for one.
    """
    years = {
        year
        for path in paths
        for year in _years_in(os.path.relpath(path, simdir))
        if 1900 <= year <= 2100
    }
    if not years:
        return None
    return min(years), max(years)


def _woa23_period(years):
    """The WOA23 decadal period covering a simulation, if one does."""
    if years is None:
        return None
    start, end = years
    for period_start, period_end in _WOA23_PERIODS:
        if start >= period_start and end <= period_end:
            return period_start, period_end
    return None


def _comment(lines):
    return [f"# {line}".rstrip() for line in lines]


def climatology_years(decadal, years, clim_years=None, woa_years=None):
    """The start and end to give a climatology recipe that has none of its
    own: the years chosen for climatologies, or None to leave the recipe's
    own default, which for one WOA23 publishes per decade is the decade
    covering years, the ones being validated.

    A decadal recipe whose years (clim_years, else years) sit inside no
    decade is given woa_years, the ones asked for in the window.
    """
    target = clim_years or years
    if decadal and target is not None and _woa23_period(target) is None:
        return tuple(woa_years) if woa_years else None
    return tuple(clim_years) if clim_years else None


def _climate_options(entry, options, years, settings):
    """options for a gridded recipe, with the years for climatologies it has
    none of its own for."""
    options = dict(options or {})
    if (
        settings
        and _is_climatology(entry)
        and options.get("start") is None
        and options.get("end") is None
    ):
        chosen = climatology_years(
            bool(entry.get("decadal")),
            years,
            settings.get("clim_years"),
            settings.get("woa_years"),
        )
        if chosen:
            options["start"], options["end"] = chosen
    return options


def _is_climatology(entry):
    """Whether a gridded recipe is a climatology, which the simulation is
    averaged over the years of."""
    return bool(entry.get("climatology")) or any(
        argument.replace(" ", "").startswith("climatology=True")
        for argument in entry["arguments"]
    )


def _has_vertical_option(entry):
    """Whether a recipe can be validated through the full water column.

    Only the recipes whose observations are resolved in depth are written
    with a vertical argument, so that is what says so.
    """
    return any(argument.startswith("vertical=") for argument in entry["arguments"])


def _option_arguments(options):
    """The source lines for the options chosen for one recipe in the window,
    leaving out those not set, which keep add_*_comparison's defaults."""
    return [
        f"{name}={_literal(value)}," for name, value in options.items() if value is not None
    ]


def _entry_arguments(entry, vertical, fvcom=False):
    """The recipe's own arguments, with its vertical=False line made the full
    water column if that was chosen."""
    if not vertical:
        return list(entry["arguments"])
    note = "the full water column"
    if not fvcom:
        # FVCOM output is regridded onto z-levels, so needs none
        note += ", so matchup also needs thickness"
    return [
        f"vertical=True,  # {note}" if argument.startswith("vertical=") else argument
        for argument in entry["arguments"]
    ]


def _unselected_note(variable, live_entries):
    """Why a recipe that has a model variable is commented out.

    live_entries are the recipes left live for the same variable, which,
    depending on the requested domain, can print either before or after
    the block, so the message names their region rather than a position.
    """
    if not live_entries:
        return [
            "Not selected when this script was generated. To validate against",
            "this source, uncomment this block.",
        ]
    regions = " and ".join(dict.fromkeys(entry["region"] for entry in live_entries))
    if len(live_entries) == 1:
        registered = f"The {regions} recipe for {variable} is registered instead."
    else:
        registered = f"The {regions} recipes for {variable} are registered instead."
    return textwrap.wrap(registered, 76) + [
        "To validate against this source as well, uncomment this block."
    ]


def _recipe_block(
    entry,
    model_variable,
    period,
    years=None,
    selected=True,
    live_entries=(),
    options=None,
    fvcom=False,
):
    """The source lines for one recipe.

    A recipe is commented out when no model variable was found for it, and
    when it was not selected - see _unselected_note. options holds any start,
    end and vertical chosen for it in the window. A WOA23 recipe published
    per decade given no years of its own gets those of the decade covering
    the simulation.
    """
    lines = list(_comment(entry["notes"]))

    options = dict(options or {})
    vertical = options.pop("vertical", None)
    arguments = _option_arguments(options)
    if entry.get("decadal") and not arguments:
        if period is None:
            if years is None:
                note = [
                    "WOA23 publishes this per decade, and start and end must sit",
                    "inside one period - set them to your simulation's years.",
                ]
            else:
                note = [
                    f"No decadal WOA23 period covers {years[0]}-{years[1]} - set start",
                    "and end below to a range inside one of the periods WOA23",
                    "publishes.",
                ]
            lines.extend(_comment(note))
            decadal = "start=2005, end=2014,"
        else:
            decadal = f"start={period[0]}, end={period[1]},"
        arguments.append(decadal)
    arguments += _entry_arguments(entry, vertical, fvcom)

    call = [
        "oceanval.add_gridded_comparison(",
        f'    name="{entry["variable"]}",',
        f'    model_variable="{model_variable or entry["example_variable"]}",',
        f'    recipe={{"{entry["variable"]}": "{entry["recipe"]}"}},',
        *[f"    {argument}" for argument in arguments],
        ")",
    ]

    if model_variable is None:
        lines.extend(
            _comment(
                [
                    f"No {entry['variable']} variable was found in the model output.",
                    "Set model_variable to the name your model uses, then uncomment",
                    "this block.",
                ]
            )
        )
        lines.extend(_comment(call))
    elif not selected:
        lines.extend(_comment(_unselected_note(entry["variable"], live_entries)))
        lines.extend(_comment(call))
    else:
        lines.extend(call)
    return lines


def _point_recipe_block(
    entry, model_variable, selected=True, options=None, fvcom=False
):
    """The source lines for one ICES point recipe.

    Simpler than _recipe_block: ICES is the only point source, so a point
    recipe that was not selected has no alternative to name, and it never
    competes with a gridded one (add_point_comparison and
    add_gridded_comparison keep separate registrations per name). options
    holds any start, end, point_time_res and vertical chosen for it in the
    window.
    """
    lines = list(_comment(entry["notes"]))

    options = dict(options or {})
    vertical = options.pop("vertical", None)
    arguments = _option_arguments(options) + _entry_arguments(entry, vertical, fvcom)
    call = [
        "oceanval.add_point_comparison(",
        f'    name="{entry["variable"]}",',
        f'    model_variable="{model_variable or entry["example_variable"]}",',
        f'    recipe={{"{entry["variable"]}": "{entry["recipe"]}"}},',
        *[f"    {argument}" for argument in arguments],
        ")",
    ]

    if model_variable is None:
        lines.extend(
            _comment(
                [
                    f"No {entry['variable']} variable was found in the model output.",
                    "Set model_variable to the name your model uses, then uncomment",
                    "this block.",
                ]
            )
        )
        lines.extend(_comment(call))
    elif not selected:
        lines.extend(_comment(_unselected_note(entry["variable"], ())))
        lines.extend(_comment(call))
    else:
        lines.extend(call)
    return lines


def _header(simdir, ndown, mapping, years, domain, fvcom=False, user=False):
    found = ", ".join(sorted(mapping)) if mapping else "none"
    lines = [
        '"""OceanVal matchup script, generated by oceanval.create_recipes.',
        "",
        f"Simulation directory: {simdir}",
        f"Output files found {ndown} director{'y' if ndown == 1 else 'ies'} down.",
        f"Model variables identified: {found}.",
        f"Domain: {domain}.",
    ]
    if fvcom:
        lines.append("Model output: raw FVCOM, regridded by matchup(fvcom=True).")
    if user:
        lines += [
            "",
            "It also registers recipes of your own, from a .oceanvalrc file in the",
            "directory this is run from or in your home directory, in their own",
            "section below.",
        ]
    lines += [
        "",
        "Every recipe OceanVal ships with is below. The live ones are the",
        "comparisons matchup will make; the rest are commented out, and where",
        "no model variable was found, the model variable is left as a",
        "placeholder for you to fill in.",
        "",
        "A variable can be validated against more than one source. Where a",
        f"variable has a recipe in more than one region, only the {domain} one is",
        "live by default and the others are commented out as alternatives -",
        "uncomment any you want as well.",
        "",
        "See https://PlymouthMarineLaboratory.github.io/OceanVal/recipes.html",
        '"""',
        "",
        "import oceanval",
        "",
    ]
    if years is None:
        lines.extend(
            [
                "",
                "# The years this simulation covers could not be read from the file",
                "# names - set start and end on the matchup call at the bottom.",
            ]
        )
    return lines


def _section(title):
    rule = "=" * 74
    return ["", f"# {rule}", f"# {title}", f"# {rule}", ""]


def _own_data_block(own_data):
    """The calls registering the user's own observations, as lines of the
    script. own_data holds the arguments of each call, under "point" and
    "gridded"."""
    lines = []
    for kind in ("point", "gridded"):
        order = [field["name"] for field in OWN_DATA_FIELDS[kind]]
        for arguments in own_data.get(kind) or []:
            lines.append(f"oceanval.add_{kind}_comparison(")
            for name in sorted(arguments, key=order.index):
                lines.append(f"    {name}={_literal(arguments[name])},")
            lines.extend([")", ""])
    return lines


def _footer(
    simdir, ndown, years, fvcom=False, settings=None, validate=True, report=None
):
    start, end = years if years is not None else (1995, 2004)
    suffix = "" if years is not None else "  # set to your simulation's years"
    if fvcom:
        note = [
            "# fvcom=True regrids the raw FVCOM output before matching it, so no",
            "# thickness is needed, and lon_lim and lat_lim default to the extent",
            "# of the mesh.",
        ]
    else:
        note = [
            "# If you set vertical=True on any recipe above, matchup also needs",
            '# thickness - either "z_level" or the name of a cell thickness variable.',
        ]
    matchup_changed, validate_changed = _changed_settings(settings)
    interim = None
    if report is not None:
        # the report options chosen in the oceanval window, after the recipes
        validate_changed = list(report.items())
        # which the interim report matchup builds as it goes has too, other
        # than the full report's other forms and whether it has earlier matchups
        interim = {
            name: value
            for name, value in report.items()
            if name not in interim_report.NOT_INTERIM
        }
        note = [
            *note,
            "#",
            "# live_validation builds an interim HTML report as the matchups are made,",
            "# in oceanval_interim_report, with the report's options other than pdf,",
            "# word and zip.",
        ]
    validate_arguments = [
        f"{name}={_literal(value)}," for name, value in validate_changed
    ]
    out_dir = dict(matchup_changed).get("out_dir")
    if out_dir:
        # the report is built from the matchups, wherever they were written
        validate_arguments[:0] = [
            f"data_dir={_literal(out_dir)},",
            f"out_dir={_literal(out_dir)},",
        ]
    if validate_arguments:
        validate_call = [
            "oceanval.validate(",
            *[f"    {argument}" for argument in validate_arguments],
            ")",
        ]
    else:
        validate_call = ["oceanval.validate()"]
    return [
        "# Pair the registered observations with your model output, then build the",
        "# report. start and end must match the years your simulation covers - and",
        "# for the World Ocean Atlas temperature and salinity recipes they must sit",
        "# inside a single decadal period.",
        "#",
        *note,
        "oceanval.matchup(",
        f'    sim_dir="{simdir}",',
        f"    start={start},{suffix}",
        f"    end={end},{suffix}",
        f"    n_dirs_down={ndown},",
        *[f"    {name}={_literal(value)}," for name, value in matchup_changed],
        *(["    fvcom=True,"] if fvcom else []),
        *(
            [f"    live_validation={_literal(interim or True)},"]
            if interim is not None
            else []
        ),
        ")",
        "",
        *(
            [
                "# word=True and pdf=True also write Word and PDF versions of the report,",
                "# and concise=False builds the full report rather than the concise one.",
                *validate_call,
            ]
            if validate
            else [
                "# Build the report once the matchups are made, by running this. word=True",
                "# and pdf=True also write Word and PDF versions of the report, and",
                "# concise=False builds the full report rather than the concise one.",
                *_comment(validate_call),
            ]
        ),
        "",
    ]


def _preferred_entries(mapping, domain):
    """The one catalogue entry to leave live for each matched variable.

    A variable with a matched model variable in more than one region (e.g.
    temperature, which both the Global and NWES recipes cover) has one
    entry preferred: the one for the requested domain if it has a match,
    the first matched entry otherwise - so asking for "nwes" falls back to
    the Global recipe for a variable NWES has none for.
    """
    wanted_region = DOMAIN_REGIONS[domain]
    preferred = {}
    for entry in RECIPE_CATALOGUE:
        variable = entry["variable"]
        if mapping.get(variable) is None:
            continue
        current = preferred.get(variable)
        if current is None or (
            entry["region"] == wanted_region and current["region"] != wanted_region
        ):
            preferred[variable] = entry
    return preferred


def default_selection(mapping, domain, cwd=None):
    """The recipes left live when none are chosen by hand.

    For each variable a model variable was found for, the one gridded recipe
    _preferred_entries picks for the domain, and for domain="nwes" its ICES
    point recipe as well, along with every recipe of the user's own (see
    oceanval.user_recipes) for it. The create_recipes window starts with
    these ticked. Returned as a set of (variable, recipe) pairs.
    """
    selection = {
        (variable, entry["recipe"])
        for variable, entry in _preferred_entries(mapping, domain).items()
    }
    if domain == "nwes":
        selection.update(
            (entry["variable"], entry["recipe"])
            for entry in POINT_RECIPE_CATALOGUE
            if mapping.get(entry["variable"]) is not None
        )
    selection.update(
        (entry["variable"], entry["recipe"])
        for entry in _user_entries(cwd)
        if mapping.get(entry["variable"]) is not None
    )
    return selection


def replaced_recipes(own_data, cwd=None):
    """The recipes of the user's (see oceanval.user_recipes) that their own
    data in a matchup takes the place of, as (variable, recipe) pairs: those
    for the same variable and source as one of its entries, as when an entry
    has been saved as a recipe too. Registering both would be the same data
    twice, or a clash. own_data is as _create_recipes takes it."""
    own = {
        (str(arguments.get("name", "")).lower(), user_recipes.key_of(arguments.get("source", "")))
        for kind in ("point", "gridded")
        for arguments in (own_data or {}).get(kind) or []
    }
    return {
        (entry["variable"], entry["recipe"])
        for entry in _user_entries(cwd)
        if (entry["variable"], entry["recipe"]) in own
    }


def build_recipe_script(
    simdir,
    ndown,
    mapping,
    years=None,
    domain="global",
    fvcom=False,
    selection=None,
    settings=None,
    point_options=None,
    gridded_options=None,
    validate=True,
    own_data=None,
    report=None,
    cwd=None,
):
    """The text of the matchup script for one simulation's variable mapping.

    selection is the set of (variable, recipe) pairs to leave live, which
    defaults to default_selection's one recipe per variable - though a
    variable can be validated against as many sources as are selected. The
    rest are still written out, commented. settings holds matchup() and
    validate() arguments, of which those changed from the defaults are
    written, point_options the start, end, point_time_res and vertical for
    each (variable, recipe) point recipe that has them, and gridded_options
    the start, end and vertical for each gridded one. own_data holds the
    arguments of the user's own add_point_comparison and
    add_gridded_comparison calls, under "point" and "gridded", which are
    written after the recipes. With validate=False, the validate() call is
    written commented out. report, if given, holds the validate() arguments
    to write, other than data_dir and out_dir, in place of those in settings.
    cwd is the directory the user's own recipes (see oceanval.user_recipes)
    are read for, the one worked in by default.
    """
    point_options = point_options or {}
    gridded_options = gridded_options or {}
    if selection is None:
        selection = default_selection(mapping, domain, cwd)
    user_entries = _user_entries(cwd)

    def live(entry):
        return (
            mapping.get(entry["variable"]) is not None
            and (entry["variable"], entry["recipe"]) in selection
        )

    lines = _header(simdir, ndown, mapping, years, domain, fvcom, bool(user_entries))
    period = _woa23_period(years)

    region = None
    for entry in RECIPE_CATALOGUE:
        if entry["region"] != region:
            region = entry["region"]
            lines.extend(_section(region))
        variable = entry["variable"]
        live_entries = [
            other
            for other in RECIPE_CATALOGUE
            if other["variable"] == variable and live(other)
        ]
        lines.extend(
            _recipe_block(
                entry,
                mapping.get(variable),
                period,
                years,
                live(entry),
                live_entries,
                _climate_options(
                    entry, gridded_options.get((variable, entry["recipe"])), years, settings
                ),
                fvcom,
            )
        )
        lines.append("")

    if domain == "nwes" or any(live(entry) for entry in POINT_RECIPE_CATALOGUE):
        lines.extend(_section("Northwest European Shelf - ICES point observations"))
        for entry in POINT_RECIPE_CATALOGUE:
            model_variable = mapping.get(entry["variable"])
            options = point_options.get((entry["variable"], entry["recipe"]))
            lines.extend(
                _point_recipe_block(entry, model_variable, live(entry), options, fvcom)
            )
            lines.append("")

    if user_entries:
        lines.extend(_section("Your recipes, from .oceanvalrc"))
        for entry in user_entries:
            variable = entry["variable"]
            if entry["point"]:
                options = point_options.get((variable, entry["recipe"]))
                lines.extend(
                    _point_recipe_block(
                        entry, mapping.get(variable), live(entry), options, fvcom
                    )
                )
            else:
                options = _climate_options(
                    entry, gridded_options.get((variable, entry["recipe"])), years, settings
                )
                lines.extend(
                    _recipe_block(
                        entry, mapping.get(variable), None, years, live(entry), (), options, fvcom
                    )
                )
            lines.append("")

    own_lines = _own_data_block(own_data or {})
    if own_lines:
        lines.extend(_section("Your own observations"))
        lines.extend(own_lines)

    lines.extend(_section("Matchup and report"))
    lines.extend(_footer(simdir, ndown, years, fvcom, settings, validate, report))
    return "\n".join(lines).rstrip("\n") + "\n"


def _write_script(out, script):
    directory = os.path.dirname(os.path.abspath(out))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(out, "w") as generated:
        generated.write(script)


def _report(out, mapping, selection, settings=None, cwd=None):
    """Say what was written, and which variables were left commented out."""
    print(f"Wrote {out}")
    for variable in sorted(mapping):
        print(f"  {variable}: {mapping[variable]}")
    missing = [
        variable for variable in recipe_variables(cwd) if mapping.get(variable) is None
    ]
    if missing:
        print(f"  commented out (no model variable found): {', '.join(missing)}")
    # only possible from the window, where every dataset can be unticked
    live = {variable for variable, _ in selection}
    unselected = [variable for variable in sorted(mapping) if variable not in live]
    if unselected:
        print(f"  commented out (no dataset selected): {', '.join(unselected)}")
    changed = [item for items in _changed_settings(settings) for item in items]
    if changed:
        listed = ", ".join(f"{name}={_literal(value)}" for name, value in changed)
        print(f"  settings: {listed}")


def _words(value, name):
    """exclude or require, as matchup() takes them: a string or a list of
    strings. None if there are none."""
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(word, str) for word in value):
        raise TypeError(f"{name} must be a string or a list of strings")
    return value or None


def _create_recipes(
    simdir=None,
    ndown=None,
    out=None,
    domain=None,
    start=None,
    end=None,
    ask=True,
    fvcom=None,
    gui=True,
    validate=True,
    exclude=None,
    require=None,
    own_data=None,
    cwd=None,
):
    """Write a matchup script for a simulation, with its variables filled in.

    Deprecated: use the ``oceanval`` command, which opens the same window
    in your web browser and goes on to run the matchup and the report. This
    function will be removed in a future release.

    Scans the netCDF files in simdir, identifies which model variable holds
    each observational variable OceanVal has a recipe for, and writes out a
    script registering those recipes. By default, what was identified is
    shown in a page in your web browser, where it can be changed before the
    script is written. With gui=False, the script is written straight away,
    and for each variable that could not be identified you are told so and
    asked for the model variable to use (press Enter to skip). Recipes still
    without a model variable are written out commented, so you can see what
    was missed and fill the variable in by hand. If the output looks like
    raw FVCOM output you are asked to confirm it is, and the script then
    calls matchup with fvcom=True.

    Parameters
    -------------
    simdir : str
        The directory holding the model output. Required.
    ndown : int
        How many directories below simdir the output files sit. Required.
    out : str
        Path of the Python file to write. Required.
    domain : str
        Either "global" or "nwes". Where a variable has a recipe in both
        regions (e.g. temperature), the recipe for this domain is left
        live and the other is commented out as an alternative; a variable
        with a recipe only outside this domain still gets that one.
        "nwes" also adds the ICES in-situ point recipes (temperature,
        salinity and eight other variables), which have no global
        equivalent and so are not included at all when domain="global".
        Required.
    start : int
        First year of the simulation to validate. Passed straight through
        to the generated script's matchup() call, and used to pick the
        WOA23 decadal period for temperature and salinity. Required.
    end : int
        Last year of the simulation to validate, passed through the same
        way as start. Required.
    ask : bool
        Whether to ask whether output that looks like FVCOM is, and for the
        model variable of anything that could not be identified. Only asks
        when run from an interactive terminal. With gui=True the model
        variables are filled in in the window instead. Defaults to True.
    fvcom : bool or None
        Whether simdir holds raw FVCOM output. If True, the variables are
        read with xarray, as CDO cannot read FVCOM's, and the script calls
        matchup with fvcom=True. Defaults to None, which checks the files
        for FVCOM's unstructured mesh and, if they have one, asks you to
        confirm - or, when it cannot ask, assumes they are FVCOM output and
        says so.
    gui : bool
        Whether to choose the recipes in a page in your web browser before
        the script is written. It lists every observational variable with
        the model variable identified for it (blank if none was), which you
        can change, and the gridded and point datasets available for it as
        tick-boxes, ticked where the script would otherwise use them. Each
        ticked dataset is written out live, so a variable can be validated
        against several, and the rest commented out. A ticked dataset can
        be given years of its own and, if its observations are resolved in
        depth, be validated through the full water column (Vertical). The
        page is served from this machine only, and its link is printed in
        case the browser cannot be opened for you (on a remote machine,
        forward its port). Cancelling, in the page or with Ctrl+C, writes
        nothing. create_recipes waits for the page, so pass gui=False
        where no one can answer it, e.g. in a batch job. Defaults to True.
    validate : bool
        Whether the script builds the validation report, with validate(),
        once matchup() has run. With validate=False the call is still
        written, commented out, to run once the matchups are made. Defaults
        to True.
    exclude : str or list of str
        Leave out output files whose names contain any of these, as
        matchup() does. Passed straight through to the script's matchup()
        call. Defaults to None.
    require : str or list of str
        Only use output files whose names contain each of these, as
        matchup() does. Passed straight through to the script's matchup()
        call. Defaults to None.
    own_data : dict
        Your own observations to register in the script, as the arguments
        of add_point_comparison calls under "point" and of
        add_gridded_comparison calls under "gridded", each a list of
        dictionaries. They are written after the recipes, and a recipe of
        your own for the same variable and source is left out in its place.
        The oceanval window fills this in. Defaults to None.
    cwd : str
        The directory your own recipes (a .oceanvalrc file in it, and one in
        your home directory) are read for. Defaults to the directory
        worked in.

    Returns
    -------------
    out : str or None
        The path written to, or None if the window was cancelled.
    """
    if simdir is None:
        raise ValueError("Please provide simdir, the model output directory")
    if ndown is None:
        raise ValueError(
            "Please provide ndown, the number of directories below simdir "
            "that the output files sit in"
        )
    if out is None:
        raise ValueError("Please provide out, the Python file to write")
    if domain is None:
        raise ValueError(
            'Please provide domain, either "global" or "nwes"'
        )
    if start is None:
        raise ValueError("Please provide start, the first year to validate")
    if end is None:
        raise ValueError("Please provide end, the last year to validate")

    if not isinstance(simdir, str):
        raise TypeError("simdir must be a string")
    if not isinstance(out, str):
        raise TypeError("out must be a string")
    if isinstance(ndown, bool) or not isinstance(ndown, int):
        raise TypeError("ndown must be an integer")
    if ndown < 0:
        raise ValueError("ndown must be a positive integer")
    if not isinstance(domain, str):
        raise TypeError("domain must be a string")
    if domain.lower() not in DOMAIN_REGIONS:
        raise ValueError('domain must be either "global" or "nwes"')
    domain = domain.lower()
    if isinstance(start, bool) or not isinstance(start, int):
        raise TypeError("start must be an integer")
    if isinstance(end, bool) or not isinstance(end, int):
        raise TypeError("end must be an integer")
    if end < start:
        raise ValueError("end must not be before start")
    if fvcom is not None and not isinstance(fvcom, bool):
        raise TypeError("fvcom must be True, False or None")
    if not isinstance(gui, bool):
        raise TypeError("gui must be True or False")
    if not isinstance(validate, bool):
        raise TypeError("validate must be True or False")
    exclude = _words(exclude, "exclude")
    require = _words(require, "require")
    filters = {"exclude": exclude, "require": require}
    if not os.path.isdir(simdir):
        raise ValueError(f"{simdir} is not a directory")

    cwd = os.path.abspath(cwd or os.getcwd())
    interactive = ask and prompts.interactive()
    if fvcom is None:
        fvcom = any(
            _looks_like_fvcom(path) for path in simulation_files(simdir, ndown, **filters)
        )
        if fvcom and interactive:
            fvcom = _ask_if_fvcom(simdir)
        elif fvcom:
            print(
                f"The output in {simdir} looks like raw FVCOM output, so the "
                "script calls matchup with fvcom=True. Pass fvcom=False if it "
                "is not."
            )

    mapping = extract_recipe_variable_mapping(simdir, ndown, fvcom, cwd=cwd, **filters)
    # the user's own data registers these itself
    replaced = replaced_recipes(own_data, cwd)

    def write(
        mapping,
        selection,
        settings=None,
        point_options=None,
        gridded_options=None,
        report=None,
        out=out,
    ):
        # out: the oceanval window's output step names the script after the recipes window
        selection = set(selection) - replaced
        # the window can change the years along with everything else
        years = (start, end)
        if settings:
            years = (
                None if settings["start"] is None
                else (settings["start"], settings["end"])
            )
        if not mapping:
            warnings.warn(
                "No model variables could be identified, so every recipe in "
                f"{out} is commented out."
            )
        script = build_recipe_script(
            os.path.abspath(simdir),
            ndown,
            mapping,
            years,
            domain,
            fvcom,
            selection,
            settings,
            point_options,
            gridded_options,
            validate,
            own_data,
            report,
            cwd,
        )
        _write_script(out, script)
        return os.path.abspath(out)

    if gui:
        # only needed when asked for, and it imports this module
        from oceanval.recipes_gui import choose_recipes

        long_names = _variable_long_names(simdir, ndown, **filters)
        available = set(long_names)
        # what was identified is in the output, even where xarray and CDO
        # disagree about a variable's name
        for model_variable in mapping.values():
            available.update(_model_variable_names(model_variable))
        context = {
            "simdir": os.path.abspath(simdir),
            "ndown": ndown,
            "domain": domain,
            "region": DOMAIN_REGIONS[domain],
            "start": start,
            "end": end,
            "fvcom": fvcom,
            "out": os.path.abspath(out),
            "cwd": cwd,
            # the Global settings start with them
            "exclude": exclude,
            "require": require,
            # for the table of the model's variables
            "long_names": long_names,
            # shown in their variables' rows, ticked, as the script registers them
            "own_data": own_data or {},
            # left out, as the user's own data registers them
            "replaced": sorted(replaced),
        }
        chosen = choose_recipes(mapping, domain, available, context, write)
        if chosen is None:
            print("create_recipes was cancelled, so nothing was written.")
            return None
        mapping, selection, settings, _, _ = chosen
    else:
        missing = [
            variable for variable in recipe_variables(cwd) if mapping.get(variable) is None
        ]
        if missing and interactive:
            mapping = _ask_for_missing_variables(
                mapping, missing, _available_variables(simdir, ndown, **filters)
            )
        selection = default_selection(mapping, domain, cwd) - replaced
        settings = None
        if exclude or require:
            # so the script's matchup() call is given them
            settings = dict(matchup_defaults(), **validate_defaults(), start=start, end=end)
            settings.update(exclude=exclude or [], require=require)
        write(mapping, selection, settings)

    _report(out, mapping, selection, settings, cwd)
    return out


@functools.wraps(_create_recipes)
def create_recipes(*args, **kwargs):
    warnings.warn(
        "create_recipes is deprecated and will be removed in a future "
        "release. Run the oceanval command instead, which opens the same "
        "window in your web browser.",
        FutureWarning,
        stacklevel=2,
    )
    return _create_recipes(*args, **kwargs)
