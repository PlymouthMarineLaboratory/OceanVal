"""The figures and analyses of a validation report, which validate()'s omit
leaves out.

Each item is a figure or table (with the text that leads up to it) on one
kind of page: a gridded matchup's, a point matchup's or the summary's. Its
id is unique across the three, so a list of ids is never ambiguous. The
report's notebooks ask for each by wanted("id") (chunk_start.pytemplate),
or, in an R cell, by a show_... flag set from it.

An item that is left out is only not shown: everything another page reads
(the annual means, the temporal correlations, the regional and depth
summaries) is still written, so the summary and compare() are unchanged.

The oceanval window shows the items as boxes the user can remove (the
report pages of view-validate), from as_json().
"""

from collections import namedtuple

from oceanval import selection

# the pages a report has for each matchup, and the summary page
GRIDDED = "gridded"
POINT = "point"
SUMMARY = "summary"
PAGES = (GRIDDED, POINT, SUMMARY)

# omit's key for the summary page, beside the matchups' keys
SUMMARY_KEY = "summary"

# omit as a list is normalised to a dict with this key, for every page
EVERY_PAGE = "*"

# when an item can be on its page, each of its conditions (none for always):
# seasonal (the observations have more than one month), subregions
# (validate's subregions), vertical (the matchup is through the water
# column), transect (validate's transect), all_layer (a point matchup
# through the water column), gridded (the report has a gridded matchup)
WHEN = ("seasonal", "subregions", "vertical", "transect", "all_layer", "gridded")

Item = namedtuple("Item", ["id", "page", "label", "text", "when"])

ITEMS = [
    # a gridded matchup's page
    Item("clim_map", GRIDDED, "Annual mean maps",
         "Maps of the annual mean in the model and the observations.", ()),
    Item("bias_map", GRIDDED, "Annual bias map",
         "A map of the annual mean bias, model minus observations.", ()),
    Item("seasonal_cor_map", GRIDDED, "Seasonal correlation map",
         "The correlation of the model's and observations' monthly climatologies in each grid cell.",
         ("seasonal",)),
    Item("monthly_maps", GRIDDED, "Monthly maps",
         "Model, observation and difference maps for each month.", ("seasonal",)),
    Item("spatial_cor_table", GRIDDED, "Spatial correlation table",
         "The spatial correlation of the model and observations, for the annual mean and each month.",
         ()),
    Item("regional_map", GRIDDED, "Map of the regions",
         "A map of each region the matchup is summarised in.", ("subregions",)),
    Item("regional_cycle", GRIDDED, "Regional seasonal cycles",
         "The seasonal cycle of the model and observations in each region.", ("subregions",)),
    Item("regional_table", GRIDDED, "Regional summary table",
         "Bias, RMSD and correlation in each region.", ("subregions",)),
    Item("zonal_sections", GRIDDED, "Depth-latitude sections",
         "Zonal mean sections through the water column, and their bias.", ("vertical",)),
    Item("transect_map", GRIDDED, "Transect map",
         "A map of the transect.", ("transect",)),
    Item("transect_surface", GRIDDED, "Values along the transect",
         "The monthly climatology of the model and observations along the transect.", ("transect",)),
    Item("transect_section", GRIDDED, "Section along the transect",
         "The annual mean through the water column along the transect.", ("transect", "vertical")),
    # a point matchup's page
    Item("locations_map", POINT, "Map of the matchups",
         "Where the observations matched up with the model are.", ()),
    Item("mean_map", POINT, "Map of average values",
         "The average model and observed values at each place, if the data have no months.", ()),
    Item("point_bias_map", POINT, "Bias map",
         "The model's bias at each observation, for each month.", ()),
    Item("scatter", POINT, "Model versus observations",
         "Scatter plots of the model against the observations, overall and for each month.", ()),
    Item("stats_table", POINT, "Summary statistics table",
         "Bias, RMSD, correlation and the number of observations, overall and for each month.",
         ()),
    Item("regression_table", POINT, "Regression table",
         "A linear regression of the observations on the model, overall and for each month.", ()),
    Item("depth_locations_map", POINT, "Map of the matchups at each depth",
         "Where the observations are, in each depth range.", ("all_layer",)),
    Item("depth_table", POINT, "Depth summary table",
         "Bias, RMSD and correlation in each depth range.", ("all_layer",)),
    # the summary page
    Item("taylor", SUMMARY, "Taylor diagram",
         "A Taylor diagram of the gridded matchups' annual means at the sea surface.", ("gridded",)),
    Item("bias_table", SUMMARY, "Bias table",
         "The model and observed spatial means of each gridded matchup, and the bias.", ("gridded",)),
    Item("spatial_table", SUMMARY, "Spatial performance table",
         "The spatial correlation and RMSD of each gridded matchup.", ("gridded",)),
    Item("temporal_maps", SUMMARY, "Temporal correlation maps",
         "Maps of the seasonal correlation of each gridded matchup.", ("gridded",)),
    Item("temporal_table", SUMMARY, "Temporal correlation table",
         "The spatial mean of each gridded matchup's seasonal correlation.", ("gridded",)),
    Item("depth_performance", SUMMARY, "Performance across depths",
         "A map of the point matchups at each depth range, and RMSD, bias and correlation tables.",
         ("all_layer",)),
]

BY_ID = {item.id: item for item in ITEMS}


def ids(page=None):
    """The items' ids, in the order of their pages, or of one page."""
    return [item.id for item in ITEMS if page is None or item.page == page]


def as_json():
    """The items, as the oceanval window takes them."""
    return [item._asdict() for item in ITEMS]


def _check_ids(value, name):
    if isinstance(value, str):
        value = [value]
    if isinstance(value, dict) or not hasattr(value, "__iter__"):
        raise ValueError(f"{name} must be a list of figure ids, e.g. ['monthly_maps']")
    found = []
    for item in value:
        if item not in BY_ID:
            raise ValueError(
                f"{name}: {item!r} is not a figure or analysis of the report. The ids are "
                f"{', '.join(ids())}"
            )
        if item not in found:
            found.append(item)
    return found


def check_omit(omit):
    """omit, validate's, checked: None for nothing left out, or a dict of
    each key (EVERY_PAGE for a list, a matchup's key as variables takes it,
    or SUMMARY_KEY) and the ids it leaves out. Raises ValueError if it
    cannot be used. What the keys pick is checked with check_against, once
    the matchups are there."""
    if omit is None:
        return None
    if not isinstance(omit, dict):
        found = sorted(_check_ids(omit, "omit"), key=ids().index)
        return {EVERY_PAGE: found} if found else None
    checked = {}
    for key, value in omit.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f'omit\'s keys must be matchups ("variable/source") or "summary", not {key!r}')
        key = key.strip()
        if key == EVERY_PAGE:
            name = "omit"
        elif key.lower() == SUMMARY_KEY:
            key = SUMMARY_KEY
            name = "omit['summary']"
        else:
            # a matchup, as variables takes it
            key = selection.check_variables([key], name="omit")[0]
            name = f"omit[{key!r}]"
        found = _check_ids(value, name)
        if key == SUMMARY_KEY:
            wrong = [item for item in found if BY_ID[item].page != SUMMARY]
            if wrong:
                raise ValueError(
                    f"{name}: {', '.join(wrong)} are not on the summary page, whose figures "
                    f"are {', '.join(ids(SUMMARY))}"
                )
        elif key != EVERY_PAGE:
            wrong = [item for item in found if BY_ID[item].page == SUMMARY]
            if wrong:
                raise ValueError(
                    f"{name}: {', '.join(wrong)} are on the summary page: give them under 'summary'"
                )
        checked[key] = sorted(set(checked.get(key, [])) | set(found), key=ids().index)
    checked = {key: value for key, value in checked.items() if value}
    return checked or None


def check_against(omit, matchups):
    """Raises ValueError if a key of omit (as check_omit gives it) picks none
    of the matchups (as selection.on_disk gives them), or leaves out an item
    that none of the pages it picks has."""
    if not omit:
        return
    for key, found in omit.items():
        if key in (EVERY_PAGE, SUMMARY_KEY):
            continue
        picked = selection.check_against([key], matchups, name="omit")
        kinds = {m["kind"] for m in picked}
        wrong = [item for item in found if BY_ID[item].page not in kinds]
        if wrong:
            raise ValueError(
                f"omit[{key!r}]: {', '.join(wrong)} are not on the {' or '.join(sorted(kinds))} "
                f"page of {key}"
            )


def removed_for(omit, page, variable=None, source=None, layer=None):
    """The ids omit (as check_omit gives it) leaves out of one page: a
    matchup's, of variable with source (at layer, for a point matchup), or
    the summary's (page SUMMARY)."""
    if not omit:
        return set()
    removed = set()
    for key, found in omit.items():
        if key == EVERY_PAGE:
            picks = True
        elif key == SUMMARY_KEY:
            picks = page == SUMMARY
        else:
            picks = page != SUMMARY and selection.wanted([key], variable, source, layer)
        if picks:
            removed |= {item for item in found if BY_ID[item].page == page}
    return removed
