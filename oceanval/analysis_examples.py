"""What the Analyze window's info buttons say.

Beside the options of each pop-out (Surface, Bottom, Water column, ...) the
window has an i. It opens a pop-out that says in words what the option will
plot. Nothing is drawn: a figure takes seconds, and the words say what the
report will show.

EXAMPLES[kind][option] holds the title and the paragraphs of each, and
describe() gives them to the page (in state.analyze.examples). A new option's i
is a new entry here and a withInfo(...) at its tick in oceanval_app.html.
"""

# ---- what each option says ----

MONTHS_TEXT = (
    "OceanVal works out the mean of each month of the year over the years from Start to End, and the annual mean "
    "of those months. The report maps the annual mean, and shows the months together on one colour scale, for "
    "each variable you tick."
)

EXAMPLES = {
    "climatology": {
        "surface": {
            "title": "Surface",
            "text": [
                "Maps of the model's values at the sea surface, which is its top level.",
                MONTHS_TEXT,
            ],
        },
        "bottom": {
            "title": "Bottom",
            "text": [
                "Maps of the model's values at the sea bed: in each place, the value in the deepest cell that is not "
                "missing.",
                "On a shallow shelf that is close to the surface, and offshore it is the deepest level, so the maps "
                "follow the shape of the sea floor. They are made as the surface's are: the annual mean, and each "
                "month on one colour scale.",
            ],
        },
        "water_column": {
            "title": "Water column",
            "text": [
                "Maps of the whole water column, reduced to one value in each place. Choose how in the box under "
                "the tick.",
                "Vertical mean is the mean of the values through the water column, weighted by the thickness of "
                "each cell, in the variable's units.",
                "Depth integral is the sum of each cell's value times its thickness, in the variable's units times "
                "metres (per square metre for a concentration), so it is largest where the water is deep.",
                "Both need the cell thickness: the name of its variable, a file, or z_level. Each is mapped as the "
                "surface is, as the annual mean and for each month.",
            ],
        },
    },
}

# what each layer is, in the sentence that begins a pop-out's words, and what more is said of the bottom and the
# water column
LAYER_WORDS = {
    "surface": "at the sea surface (its top level)",
    "bottom": "at the sea bed",
    "water_column": "through the whole water column",
}
LAYER_DETAIL = {
    "surface": None,
    "bottom": "The bottom is the value in the deepest cell that is not missing, in each place.",
    "water_column": (
        "The water column is reduced to one value in each place in the way chosen in the box under the tick: the "
        "vertical mean, weighted by the thickness of each cell, or the depth integral, the sum of each cell's value "
        "times its thickness. Both need the cell thickness: the name of its variable, a file, or z_level."
    ),
}

LONG_TERM_TEXT = (
    "Each year's value is the mean of its months, taken over the model's domain as a whole and over each region and "
    "station you choose. A year with fewer than twelve months of output is drawn as an open circle. A rolling mean of "
    "the annual means can be taken, and the series shown as the annual means or as their change from the first year, "
    "as set in the other columns."
)
SEASONAL_TEXT = (
    "It is taken over the model's domain as a whole and over each region and station you choose, so that each has a "
    "line of twelve months."
)
CHANGES_TEXT = (
    "Each period is mapped as the mean of its years' annual means, and the change is the future period minus the "
    "historical one, or that as a percentage of it, as you choose. What is drawn depends on the Plot you choose: the "
    "periods and the change between them, or just the changes."
)


def _layered(intro, extra):
    """The entries of Surface, Bottom and Water column of a pop-out: intro.format(where) begins each, then what each
    layer is, and extra, which they all have."""
    titles = {"surface": "Surface", "bottom": "Bottom", "water_column": "Water column"}
    return {
        option: {
            "title": title,
            "text": [text for text in (intro.format(LAYER_WORDS[option]), LAYER_DETAIL[option], extra) if text],
        }
        for option, title in titles.items()
    }


EXAMPLES["long_term"] = _layered(
    "A time series of the model's values {}: the mean of each year, from Start to End.", LONG_TERM_TEXT)
EXAMPLES["seasonal"] = _layered(
    "The mean seasonal cycle of the model's values {}: the mean of each month of the year over the years from "
    "Start to End.", SEASONAL_TEXT)
EXAMPLES["map_changes"] = _layered(
    "Maps of how the model's values {} change between a historical period and a future period.", CHANGES_TEXT)

EXAMPLES["phenology"] = {
    "peak": {
        "title": "Peak day",
        "text": [
            "The time of the seasonal peak at the surface: in each grid cell, the day of the year with the highest "
            "value in the variable's daily climatology, over the years from Start to End. It needs daily output.",
            "The report maps the day of the peak on a colour scale that is cyclic and labelled by month, so that the "
            "end of the year is close in colour to its start, and maps the value of the peak. It also draws the "
            "daily climatology over the model's domain, with its peak marked.",
        ],
    },
}

EXAMPLES["transects"] = {
    "surface_climatology": {
        "title": "Surface climatology",
        "text": [
            "The model's values at the sea surface along the line, for each month of the year: the mean of each month "
            "over the years from Start to End, at each point along the line.",
            "The report shows a map of where the line runs, and then the position along the line by month, as colours.",
        ],
    },
    "surface_changes": {
        "title": "Surface changes",
        "text": [
            "How the model's values at the sea surface along the line changed over the years: the position along the "
            "line by year, as colours, worked out from the annual means over the years from Start to End.",
            "Choose how in the drop-down: Absolute is the annual means, Absolute change is each minus the first, and "
            "Percentage change is that as a percentage of the first. A rolling mean of the annual means, in years, "
            "smooths them, each year then being the last of its window, and the changes are then from the first of "
            "the rolling means.",
        ],
    },
    "vertical_climatology": {
        "title": "Vertical climatology",
        "text": [
            "A section through the water column along the line: the annual mean over the years from Start to End, "
            "by depth and position along the line.",
            "It needs the cell thickness, which says where each cell is: the name of its variable, a file, or "
            "z_level. Where there is no water, as at the sea bed, the section is grey.",
        ],
    },
    "vertical_changes": {
        "title": "Vertical changes",
        "text": [
            "How the model's values through the water column along the line changed over the years: a map of the "
            "position along the line by year for each of a few depths of the section, as for the surface changes, "
            "with the same choices of how.",
            "It needs the cell thickness, as the vertical climatology does.",
        ],
    },
}


def describe():
    """EXAMPLES as the page needs it, for state: {kind: {option: {"title", "text"}}}."""
    return {
        kind: {option: {"title": found["title"], "text": list(found["text"])} for option, found in options.items()}
        for kind, options in EXAMPLES.items()
    }
