"""What each of OceanVal's built-in variables and datasets can show, so that the
oceanval window offers only the report options that apply to them.

PROFILES is the lookup: one entry for each built-in recipe (the rows of
create_recipes.RECIPE_CATALOGUE and POINT_RECIPE_CATALOGUE), written out here
as data so it can be read at a glance. Each entry has

  depth   "profile" if the observations are resolved in depth (a matchup of it
          can be through the water column), else "surface"
  months  how the observations vary in time: "annual" (one value, with no
          seasonal cycle), "climatology" (a monthly climatology), "series"
          (months of many years) or "dated" (point samples, by date)
  total   what its total over the area matched is: "flux" (a flux per area, summed
          over the cells), "inventory" (a concentration, summed over the volume,
          through the water column) or None
  log     whether its statistics are of log10 values (skill.LOG_VARIABLES)
  region  "global", or "regional" for a dataset of one area, which the default
          transect sections are not drawn on

tests/test_option_profiles.py checks every field that the catalogues or totals
and skill can work out against them, and that no recipe is left without an entry.

items_for(matchup) is the figures and analyses (oceanval.report_items) a matchup
can show or feed, and overall_options(matchups) the report options that apply
to a set of them. A matchup with no profile (the user's own data) is assumed to
show everything its kind and its layer allow: nothing that might apply is
hidden. Only its total is judged from its units, when they are known (annotate
reads them from a gridded matchup's file): the total over the area matched is
of a flux per area (oceanval.totals.kind_of), so a concentration is never
offered one. The boxes that hang on what the user has typed (subregions, transect)
are the page's, as they are not about the matchup.
"""

import os

from oceanval import release_figures, report_items

GRIDDED = "gridded"
POINT = "point"

PROFILE = "profile"
SURFACE = "surface"

ANNUAL = "annual"
CLIMATOLOGY = "climatology"
SERIES = "series"
DATED = "dated"

FLUX = "flux"
INVENTORY = "inventory"

GLOBAL = "global"
REGIONAL = "regional"


def _profile(depth, months, total=None, log=False, region=GLOBAL):
    return {"depth": depth, "months": months, "total": total, "log": log, "region": region}


# the GLODAPv2 climatologies: one value for each place, through the water column
_GLODAP = _profile(PROFILE, ANNUAL)
_GLODAP_INVENTORY = _profile(PROFILE, ANNUAL, INVENTORY)
# the World Ocean Atlas, a climatology of each month, through the water column
_WOA = _profile(PROFILE, CLIMATOLOGY)
_WOA_INVENTORY = _profile(PROFILE, CLIMATOLOGY, INVENTORY)
# the North Sea Biogeochemical Climatology, the same on the northwest European Shelf
_NSBC = _profile(PROFILE, CLIMATOLOGY, region=REGIONAL)
_NSBC_INVENTORY = _profile(PROFILE, CLIMATOLOGY, INVENTORY, region=REGIONAL)
# point samples, by date
_POINT = _profile(PROFILE, DATED)
_POINT_REGIONAL = _profile(PROFILE, DATED, region=REGIONAL)

# keyed by (kind, variable, source), all lower case
PROFILES = {
    # GLODAPv2.2016b
    (GRIDDED, "alkalinity", "glodap"): _GLODAP_INVENTORY,
    (GRIDDED, "dic", "glodap"): _GLODAP_INVENTORY,
    (GRIDDED, "nitrate", "glodap"): _GLODAP_INVENTORY,
    (GRIDDED, "oxygen", "glodap"): _GLODAP_INVENTORY,
    (GRIDDED, "phosphate", "glodap"): _GLODAP_INVENTORY,
    (GRIDDED, "silicate", "glodap"): _GLODAP_INVENTORY,
    (GRIDDED, "omegaaragonite", "glodap"): _GLODAP,
    (GRIDDED, "omegacalcite", "glodap"): _GLODAP,
    (GRIDDED, "ph", "glodap"): _GLODAP,
    # World Ocean Atlas 2023
    (GRIDDED, "nitrate", "woa23"): _WOA_INVENTORY,
    (GRIDDED, "oxygen", "woa23"): _WOA_INVENTORY,
    (GRIDDED, "phosphate", "woa23"): _WOA_INVENTORY,
    (GRIDDED, "silicate", "woa23"): _WOA_INVENTORY,
    (GRIDDED, "aou", "woa23"): _WOA_INVENTORY,
    (GRIDDED, "o2sat", "woa23"): _WOA,
    (GRIDDED, "salinity", "woa23"): _WOA,
    (GRIDDED, "temperature", "woa23"): _WOA,
    # North Sea Biogeochemical Climatology
    (GRIDDED, "ammonium", "nsbc"): _NSBC_INVENTORY,
    (GRIDDED, "nitrate", "nsbc"): _NSBC_INVENTORY,
    (GRIDDED, "oxygen", "nsbc"): _NSBC_INVENTORY,
    (GRIDDED, "phosphate", "nsbc"): _NSBC_INVENTORY,
    (GRIDDED, "silicate", "nsbc"): _NSBC_INVENTORY,
    (GRIDDED, "chlorophyll", "nsbc"): _profile(PROFILE, CLIMATOLOGY, log=True, region=REGIONAL),
    (GRIDDED, "salinity", "nsbc"): _NSBC,
    # surface data
    (GRIDDED, "chlorophyll", "occci"): _profile(SURFACE, SERIES, log=True),
    (GRIDDED, "kd490", "occci"): _profile(SURFACE, SERIES, log=True),
    (GRIDDED, "temperature", "cobe2"): _profile(SURFACE, SERIES),
    (GRIDDED, "temperature", "oisst"): _profile(SURFACE, SERIES),
    (GRIDDED, "temperature", "coraltemp"): _profile(SURFACE, SERIES),
    (GRIDDED, "temperature", "ostia"): _profile(SURFACE, SERIES),
    (GRIDDED, "pco2", "socat"): _profile(SURFACE, SERIES),
    (GRIDDED, "pco2", "mpisom"): _profile(SURFACE, CLIMATOLOGY),
    (GRIDDED, "co2flux", "mpisom"): _profile(SURFACE, CLIMATOLOGY, FLUX),
    (GRIDDED, "mesozooplankton", "copepod"): _profile(SURFACE, CLIMATOLOGY),
    # point observations
    (POINT, "alkalinity", "glodapv3"): _POINT,
    (POINT, "chlorophyll", "glodapv3"): _profile(PROFILE, DATED, log=True),
    (POINT, "dic", "glodapv3"): _POINT,
    (POINT, "doc", "glodapv3"): _POINT,
    (POINT, "nitrate", "glodapv3"): _POINT,
    (POINT, "oxygen", "glodapv3"): _POINT,
    (POINT, "ph", "glodapv3"): _POINT,
    (POINT, "phosphate", "glodapv3"): _POINT,
    (POINT, "silicate", "glodapv3"): _POINT,
    (POINT, "doc", "hansell"): _POINT,
    (POINT, "poc", "hansell"): _POINT,
    (POINT, "temperature", "ices"): _POINT_REGIONAL,
    (POINT, "salinity", "ices"): _POINT_REGIONAL,
    (POINT, "alkalinity", "ices"): _POINT_REGIONAL,
    (POINT, "ammonium", "ices"): _POINT_REGIONAL,
    (POINT, "chlorophyll", "ices"): _profile(PROFILE, DATED, log=True, region=REGIONAL),
    (POINT, "nitrate", "ices"): _POINT_REGIONAL,
    (POINT, "oxygen", "ices"): _POINT_REGIONAL,
    (POINT, "ph", "ices"): _POINT_REGIONAL,
    (POINT, "phosphate", "ices"): _POINT_REGIONAL,
    (POINT, "silicate", "ices"): _POINT_REGIONAL,
    # the sea bed's animals, which have no depth: a matchup of them is never through the water column
    (POINT, "macrobenthos", "nsbs"): _profile(SURFACE, DATED, region=REGIONAL),
    # EN4's profiles, of the whole ocean
    (POINT, "temperature", "en4"): _POINT,
    (POINT, "salinity", "en4"): _POINT,
    # casts of temperature and salinity, whose stratification is worked out (oceanval.stratification)
    (POINT, "stratification", "ices"): _POINT_REGIONAL,
    (POINT, "stratification", "en4"): _POINT,
}


def profile_for(matchup):
    """The profile of a matchup (as selection.on_disk gives it), or None if it
    is not a built-in one: the user's own data, or a variable matched up with
    a source OceanVal has no recipe for."""
    key = (
        str(matchup.get("kind", "")).lower(),
        str(matchup.get("variable", "")).lower(),
        str(matchup.get("source", "")).lower(),
    )
    return PROFILES.get(key)


def _holds(condition, matchup, profile):
    """Whether a condition of a report item (report_items.WHEN) is true of one
    matchup. Those that hang on what the user has typed (subregions and
    transect) are the page's, so they are true here. Without a profile, what is
    not known is taken to be true."""
    gridded = matchup.get("kind") == GRIDDED
    vertical = bool(matchup.get("vertical"))
    if condition == "gridded":
        return gridded
    if condition == "vertical":
        return gridded and vertical
    if condition == "all_layer":
        return matchup.get("kind") == POINT and matchup.get("layer") == "all"
    stratification = matchup.get("kind") == POINT and matchup.get("variable") == "stratification"
    if condition == "stratification":
        return stratification
    if condition == "values":
        # the page of any other point matchup, of the values matched
        return not stratification
    if condition == "depths":
        # a stratification matchup has no depth summary for the summary page
        return (vertical or matchup.get("layer") == "all") and not stratification
    if condition == "seasonal":
        return profile is None or profile["months"] != ANNUAL
    if condition == "series":
        return profile is None or profile["months"] == SERIES
    if condition in ("flux", "totals"):
        if profile is not None:
            total = profile["total"]
        elif matchup.get("units") is not None:
            # the user's own data, whose units say what its total is
            from oceanval import totals

            total = totals.kind_of(matchup["units"], matchup.get("variable", ""))
        else:
            return True
        if condition == "flux":
            return total == FLUX
        return total == FLUX or (total == INVENTORY and vertical)
    return True


def items_for(matchup):
    """The ids of the figures and analyses a matchup can show on its own page
    or feed on the summary page, in the order of report_items.ITEMS. Which of
    the summary's one matchup feeds is for the summary to take the union of."""
    profile = profile_for(matchup)
    released = release_figures.in_release()
    return [
        item.id
        for item in report_items.ITEMS
        # one not in release_figures.csv is not offered
        if item.id in released
        # taylor and the summary's tables are of the gridded matchups, and depth_performance of any through
        # the water column: a matchup feeds the items of the summary whose conditions it meets
        and item.page in (report_items.SUMMARY, matchup.get("kind"))
        and all(_holds(condition, matchup, profile) for condition in item.when)
    ]


def default_sections(matchup):
    """Whether the default transect sections (the Equator and 155°W) can be
    drawn on a matchup: it is not of one region only. True if that is not known."""
    profile = profile_for(matchup)
    return profile is None or profile["region"] == GLOBAL


def _gridded_units(matchup, data_dir):
    """The units of the model in a gridded matchup's surface file in data_dir,
    from its header, or None if there is no such file or it has none."""
    if not data_dir or matchup.get("kind") != GRIDDED:
        return None
    import netCDF4

    from oceanval import netcdf_lock

    variable, source = matchup.get("variable", ""), matchup.get("source", "")
    path = os.path.join(data_dir, "oceanval_matchups", GRIDDED, variable, f"{source}_{variable}_surface.nc")
    if not os.path.exists(path):
        return None
    try:
        with netcdf_lock.LOCK, netCDF4.Dataset(path) as ds:
            return getattr(ds.variables["model"], "units", None)
    except Exception:
        return None


def annotate(matchups, data_dir=None):
    """The matchups (as selection.on_disk gives them), each with its items (see
    items_for), the report options it takes (options_for) and whether it has the default sections, for the window. None
    stays None. With data_dir, the folder of the matchups, a gridded matchup
    of the user's own data has its model's units read, to judge its total."""
    if matchups is None:
        return None
    matchups = [
        matchup if profile_for(matchup) is not None or "units" in matchup
        else dict(matchup, units=_gridded_units(matchup, data_dir))
        for matchup in matchups
    ]
    return [
        dict(
            matchup,
            items=items_for(matchup),
            options=options_for(matchup),
            default_sections=default_sections(matchup),
            known=profile_for(matchup) is not None,
        )
        for matchup in matchups
    ]


# the report options that are about one kind of matchup, with the form's boxes each is made of
OPTION_BOXES = {
    "subregions": ("subregions", "subregions_file"),
    "transect": ("transect",),
    "map_depths": ("map_depths",),
    "fixed_scale": ("fixed_scale",),
    "depth_bins": ("depth_bins",),
}


def options_for(matchup):
    """The report options (the keys of OPTION_BOXES) that apply to one matchup.

    Regional summaries and transects are of gridded matchups. Maps at depth
    are of gridded matchups through the water column. The fixed scale is of the
    monthly maps, which a gridded matchup with a seasonal cycle has. The depth
    bins summarise any matchup through the water column, point or gridded."""
    gridded = matchup.get("kind") == GRIDDED
    vertical = bool(matchup.get("vertical"))
    return [
        option
        for option, applies in (
            ("subregions", gridded),
            ("transect", gridded),
            ("map_depths", gridded and vertical),
            ("fixed_scale", gridded and _holds("seasonal", matchup, profile_for(matchup))),
            ("depth_bins", vertical),
        )
        if applies
    ]


def overall_options(matchups):
    """Which of the report options apply to matchups (as selection.on_disk
    gives them): True or False for each key of OPTION_BOXES, True if any of
    the matchups takes it."""
    found = {option for matchup in matchups for option in options_for(matchup)}
    return {option: option in found for option in OPTION_BOXES}
