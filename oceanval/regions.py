"""The built-in sets of regions (subregions=) of validate(), analyze() and
compare_simulations(), each a file data/<name>_subdomains.nc whose variables are
maps that are 1 in a region and missing elsewhere.

Every place that takes subregions asks this module whether a name is built in
and where its file is, so a new set is added here (and its file in data/) only.
The three made from the gap report (latbands, cmems, biomes) are written by
scripts/make_region_files.py, which says where their boundaries come from.
"""

import importlib.resources

# name: what the windows call it
BUILT_IN = {
    "nwes": "Northwest European Shelf subregions",
    "global": "Global subregions",
    "latbands": "Global latitude bands",
    "cmems": "CMEMS global sub-regions",
    "biomes": "Global ocean biomes (Fay and McKinley 2014)",
}

# where each set's regions come from, for the report's Data Sources
SOURCES = {
    "latbands": "Latitude bands as in Tjiputra et al. (2020) and Schwinger et al. (2016): the North Atlantic and North "
    "Pacific north of 20°N, the tropics (20°S–20°N), the southern subtropics (20–40°S) and the Southern Ocean "
    "(south of 40°S).",
    "cmems": "Approximations of the sub-regions mapped in Figure 1 of the Copernicus Marine quality information document "
    "GLO-QUID-001-028 (global ocean biogeochemistry analysis and forecast), which gives them as a map only.",
    "biomes": "The mean biomes of Fay, A. R. and McKinley, G. A. (2014), Global open ocean biomes: mean and temporal "
    "variability, Earth Syst. Sci. Data, 6, 273–284, https://doi.org/10.5194/essd-6-273-2014 "
    "(data: https://doi.org/10.1594/PANGAEA.828650, CC BY 4.0).",
}


# how a report's text names each set ("over each of ...")
DESCRIPTIONS = {
    "nwes": "the northwest European Shelf regions",
    "global": "the global regions",
    "latbands": "the global latitude bands",
    "cmems": "the CMEMS global sub-regions",
    "biomes": "the global ocean biomes of Fay and McKinley (2014)",
}


def description(subregions, own):
    """How a report's text names the set subregions; own for a file of the user's own."""
    return DESCRIPTIONS.get(subregions, own) if isinstance(subregions, str) else own


def is_built_in(subregions):
    """Whether subregions names a built-in set."""
    return isinstance(subregions, str) and subregions in BUILT_IN


def path(name):
    """The full path of the file of the built-in set name."""
    if not is_built_in(name):
        raise ValueError(f"{name} is not a built-in set of regions: they are {', '.join(repr(x) for x in BUILT_IN)}")
    return str(importlib.resources.files("oceanval").joinpath(f"data/{name}_subdomains.nc"))


def choices_text():
    """The subregions a function takes, for its error messages."""
    names = ", ".join(f"'{x}'" for x in BUILT_IN)
    return f"{names} or a path to a .nc file"
