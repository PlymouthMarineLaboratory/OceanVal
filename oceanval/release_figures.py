"""Which report figures and analyses are in this release of OceanVal.

``release_figures.csv`` (beside this file) has one row per item of
``oceanval.report_items.ITEMS``: Kind (gridded, point or summary), Figure name
(the item's id) and In release (Y or N). To stage a release, set a figure to N:
the OceanVal app then does not offer it and the app's reports do not draw it.
A figure that is Y is offered, on a tile marked amber as in development.

The figures in ALWAYS were in OceanVal before the phased release: they are not
in the file, are always offered, and are never amber.
"""

import csv
import functools
import importlib.resources

from oceanval import report_items

FILE = "release_figures.csv"

# the figures OceanVal had before the release was staged: always in, never in the file
ALWAYS = (
    "clim_map", "bias_map", "seasonal_cor_map", "monthly_maps", "spatial_cor_table",
    "taylor", "bias_table", "spatial_table", "temporal_maps", "temporal_table",
    "locations_map", "mean_map", "point_bias_map", "scatter", "stats_table", "regression_table",
    "depth_locations_map", "depth_table", "point_profiles", "depth_performance",
)


@functools.lru_cache(maxsize=1)
def _table():
    path = importlib.resources.files("oceanval").joinpath(FILE)
    with open(path, newline="", encoding="utf-8") as file:
        return {
            row["Figure name"].strip(): (row["Kind"].strip().lower(), row["In release"].strip().upper())
            for row in csv.DictReader(file)
        }


def problems():
    """What is wrong with the file, as a list of sentences (empty if it is right)."""
    found = []
    table = _table()
    for name, (kind, flag) in table.items():
        if name not in report_items.BY_ID:
            found.append(f"{name} is not a figure of the report")
        elif name in ALWAYS:
            found.append(f"{name} is always in the release: take it out of {FILE}")
        elif kind != report_items.BY_ID[name].page:
            found.append(f"{name} is {report_items.BY_ID[name].page}, not {kind}")
        if flag not in ("Y", "N"):
            found.append(f"{name}: In release must be Y or N, not {flag!r}")
    found += [f"{item} is not in {FILE}" for item in report_items.ids() if item not in table and item not in ALWAYS]
    return found


def in_release():
    """The ids of the figures marked Y, and those always in. One missing from the file is not in the release."""
    marked = {name for name, (_, flag) in _table().items() if flag == "Y" and name in report_items.BY_ID}
    return marked | set(ALWAYS)


def in_development():
    """The ids of the figures in the release that are still in development (amber): those marked Y."""
    return in_release() - set(ALWAYS)


def not_released():
    """The ids of the figures that are not in the release, in the order of report_items.ids()."""
    released = in_release()
    return [item for item in report_items.ids() if item not in released]


def detailed_only():
    """The ids of the figures a concise report leaves out: those OceanVal did not
    have before the phased release (the others in ALWAYS are the concise report's)."""
    return [item for item in report_items.ids() if item not in ALWAYS]
