"""The HTML report of compare_simulations() (see oceanval.sim_compare), built the
way the analysis report is (see oceanval.analysis_report) from its own
notebook templates, and the words its notebooks use."""

from oceanval import analysis

# the chunk of a variable's page for each kind of analysis: letters only, as chunkers.is_chunk needs
CHUNKS = {
    "climatology": "chunk_simclimatology",
    "phenology": "chunk_simphenology",
    "long_term": "chunk_simlongterm",
    "seasonal": "chunk_simseasonalseries",
    "map_changes": "chunk_simmapchanges",
    "transects": "chunk_simtransects",
}


def templates():
    """The notebooks the comparison report is built from (see
    analysis.Templates)."""
    return analysis.Templates(
        overview="simcompare_overview.ipynb",
        template="simcompare_template.ipynb",
        chunks=CHUNKS,
        kinds_of=lambda variable: variable["kinds"],
    )


# ---- the words of the notebooks ----

WHERE = {"surface": "surface", "bottom": "bottom", "mean": "depth-averaged", "integral": "depth-integrated"}


def join_words(items):
    """a, b and c."""
    items = list(items)
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def years_text(analysis_):
    if analysis_["start"] == analysis_["end"]:
        return str(analysis_["start"])
    return f"{analysis_['start']}-{analysis_['end']}"


def swatch(info, sim):
    """A simulation's name, in its colour: a square of it, then the name."""
    return (
        f'<span style="display:inline-block;width:0.9em;height:0.9em;border-radius:2px;'
        f'background:{info["colours"][sim]};margin-right:0.4em;vertical-align:-0.1em"></span>{sim}'
    )


def colour_names(info):
    """The simulations and the colour each is drawn in, as a sentence."""
    return join_words(f"**{sim}**" for sim in info["colours"])


def files_text(variable, kind):
    """How many files each simulation's analysis of kind was read from."""
    counts = {sim: own[kind]["files"] for sim, own in variable["sims"].items() if own.get(kind)}
    if len(set(counts.values())) == 1:
        number = next(iter(counts.values()))
        return f"read from {number} file{'s' if number != 1 else ''} of each simulation"
    return "read from " + join_words(f"{n} file{'s' if n != 1 else ''} of {sim}" for sim, n in counts.items())


def dates_text(variable, kind):
    """What the output of the simulations is missing in the years of an analysis."""
    said = {}
    summaries = {}
    for sim, own in variable["sims"].items():
        analysis_ = own.get(kind)
        if analysis_:
            dates = analysis_["dates"]
            if dates["issues"]:
                said[sim] = " ".join(dates["issues"])
            summaries[sim] = dates["summary"]
    if not said:
        return "No output is missing in these years from any simulation."
    return "Gaps in the output: " + " ".join(f"**{sim}**: {text}" for sim, text in said.items())


def series_area_text(info, analysis_, variable, kind):
    """What a long-term or seasonal series is the mean of."""
    where = "the model's domain"
    if info.get("lon_lim"):
        lon, lat = info["lon_lim"], info["lat_lim"]
        where += f" inside longitudes {lon[0]:g} to {lon[1]:g} and latitudes {lat[0]:g} to {lat[1]:g}"
    text = f"The series are area-weighted means over {where}"
    if analysis_["regions"]:
        which = {"nwes": "the northwest European Shelf regions", "global": "the global regions"}.get(
            info.get("subregions"), "the regions of your own file"
        )
        text += f", and over each of {which} ({', '.join(analysis_['regions'])})"
    if analysis_["stations"]:
        text += ", and at the cell of the model's grid nearest each station"
    text += "."
    notes = [
        f"**{sim}**: " + " ".join(own[kind]["notes"]) for sim, own in variable["sims"].items() if own.get(kind) and own[kind]["notes"]
    ]
    if notes:
        text += " " + " ".join(notes)
    return text


def station_rows(info, variable, kind, layer):
    """A table of the stations and the model cell each simulation has them in."""
    rows = []
    for sim, own in variable["sims"].items():
        saved = own[kind]["layers"][layer]
        for station, cell in saved["stations"].items():
            given = info["stations"][station]
            rows.append(
                {
                    "Station": station,
                    "Position": f"{given['lon']:.3f}, {given['lat']:.3f}",
                    "Simulation": swatch(info, sim),
                    "Model cell": f"{cell['lon']:.3f}, {cell['lat']:.3f}",
                    "Distance (km)": f"{cell['distance']:.1f}",
                }
            )
    return rows


def series_what(analysis_, saved):
    """What a long-term series' figure shows: the annual mean, or its change."""
    window = analysis_.get("window", 1)
    base = f"{window}-year rolling mean of the annual means" if window > 1 else "annual mean"
    what = {
        "absolute": base,
        "absolute_change": f"change in the {base}",
        "percentage_change": f"percentage change in the {base}",
    }[saved.get("show", "absolute")]
    return what[0].upper() + what[1:]


def series_shown_text(analysis_):
    """How the long-term series are taken from the annual means, if they are not as they are."""
    window, show = analysis_.get("window", 1), analysis_.get("show", "absolute")
    text = ""
    if window > 1:
        before = f"the {window - 1} years before it" if window > 2 else "the year before it"
        first = f"the first {window - 1} years have" if window > 2 else "the first year has"
        text += (
            f"Each year is the mean of that year's annual mean and those of {before} (a {window}-year rolling mean), "
            f"so {first} no value, nor has a year whose window has a year with no output. "
        )
    if show != "absolute":
        text += "Each year is shown as its change from the first year with a value" + (
            ", in per cent of that value" if show == "percentage_change" else ""
        ) + ". "
    return text


def incomplete_text(variable, kind, layer):
    """The years that are the mean of fewer than 12 months, and those with no output, in each simulation."""
    open_circles, gaps = [], []
    for sim, own in variable["sims"].items():
        saved = own[kind]["layers"][layer]
        if saved["incomplete"]:
            open_circles.append(f"**{sim}**: {', '.join(str(y) for y in saved['incomplete'])}")
        if saved["missing"]:
            gaps.append(f"**{sim}**: {', '.join(str(y) for y in saved['missing'])}")
    text = ""
    if open_circles:
        text += (
            " Open circles are years that are the mean of fewer than 12 months, because the output is missing some: "
            + "; ".join(open_circles)
            + "."
        )
    if gaps:
        text += " There is no output for " + "; ".join(gaps) + "."
    return text


def regridded_text(regridded):
    if regridded:
        return " The model's grid was regridded to a regular lon-lat grid to draw it."
    return ""


def conversions_text(variable, first):
    """How the simulations that give variable in other units than the first
    simulation, first, were converted into its units, as a sentence, or "" if
    none were."""
    said = [f"{sim}, {conversion['text']}" for sim, conversion in (variable.get("conversions") or {}).items()]
    if not said:
        return ""
    return f" It was converted into the units of {first} as it was read: " + "; ".join(said) + "."


def peak_text(found, info):
    """The day of the seasonal peak in each simulation, as a sentence."""
    from oceanval import analysis_plots as ap

    said = [
        f"{sim}: {ap.day_text(peak['month_day'])}, day {peak['day']}"
        for sim, peak in found["peaks"].items()
        if peak["day"]
    ]
    return (" Over the domain as a whole, the peak is on " + "; ".join(said) + ".") if said else ""


def sampled_text(variable, index):
    """How many points each simulation's transect is sampled at."""
    steps = {
        sim: next(line["steps"] for line in own["transects"]["lines"] if line["index"] == index)
        for sim, own in variable["sims"].items()
        if own.get("transects")
    }
    if len(set(steps.values())) == 1:
        return f"It is sampled at {next(iter(steps.values()))} points."
    return "It is sampled at " + join_words(f"{n} points in {sim}" for sim, n in steps.items()) + "."


def empty_in(variable, index):
    """The simulations whose model has no values along a transect."""
    return [
        sim
        for sim, own in variable["sims"].items()
        if own.get("transects") and next(line for line in own["transects"]["lines"] if line["index"] == index)["empty"]
    ]


def notes_of(variable, index, option):
    """What the analysis of a transect option noted, for each simulation."""
    notes = []
    for sim, own in variable["sims"].items():
        if not own.get("transects"):
            continue
        line = next(line for line in own["transects"]["lines"] if line["index"] == index)
        saved = line["options"].get(option)
        if saved and saved["notes"]:
            notes.append(f"**{sim}**: " + " ".join(saved["notes"]))
    return " ".join(notes)


def methods(info):
    """The methods of the report, as a list of paragraphs."""
    variables = info["variables"]

    def has(kind):
        return any(kind in variable["kinds"] for variable in variables)

    methods_ = [
        "**The simulations.** Every simulation is analysed in the same way, for the same years, from its own output, "
        "and shown beside the others, in a colour for each.",
        "**Climatology.** For each variable, the mean of each month of each year is taken first, so that a month of daily output counts as much as a month of monthly output. The climatology is the mean of each month of the year over the years chosen, and the annual mean is the mean of those monthly means. "
        "At the surface, the first value from the top of the water column that is not missing is taken, as in OceanVal's matchups. "
        "At the bottom, the deepest value that is not missing is taken (nctoolkit's `bottom` method with `choice=\"value\"`). "
        "The water column's vertical mean weights each layer by its thickness; its depth integral multiplies each layer by its thickness and sums them, so a concentration per cubic metre becomes an amount per square metre. "
        "The maps of the simulations share one colour scale, from the 2nd to the 98th percentile of all of them.",
    ]
    if info.get("fvcom"):
        methods_.append(
            "**FVCOM output.** The simulations are raw FVCOM output, on an unstructured mesh of triangles that every simulation shares, "
            "which is read as it is, not regridded: the surface is the top sigma layer and the bottom the deepest sigma layer with a value, "
            "and the maps are drawn on the mesh itself, a value on the nodes shaded linearly across each triangle and one on the elements "
            "filling its triangle, with the triangles that have a value missing left out. Through the water column, each layer is weighted "
            "by its thickness, its share of the sigma levels (siglev) of the depth of the water, h + zeta, at each time. The spatial means "
            "of the time series and the phenology's daily cycle are weighted by the areas of the mesh's cells, a region takes the cells "
            "whose positions are in it, and a station is the nearest node or element with values. The simulations are compared cell by "
            "cell on this one mesh."
        )
    converted = [variable for variable in variables if variable.get("conversions")]
    if converted:
        first = info["simulations"][0]["name"]
        methods_.append(
            f"**Units.** Every variable is compared in the units {first} gives it in. A simulation giving it in other units "
            "had each value multiplied by a number and had a number added to it as it was read from the output, before "
            "anything was worked out from it (values were taken as missing in the simulation's own units, before that): "
            + "; ".join(
                f"{variable['name']} in {sim}, {conversion['text']}"
                for variable in converted
                for sim, conversion in variable["conversions"].items()
            )
            + "."
        )
    if has("phenology"):
        methods_.append(
            "**Phenology.** The day of the seasonal peak is found in each grid cell from the variable's daily climatology at the surface: its mean on each day of the year over the years chosen. The peak is the day with the highest mean, or the first of them if there are more than one, and days are counted from 1 January in the model's own calendar."
        )
    if has("long_term"):
        methods_.append(
            "**Long-term time series.** The mean of each month of each year is taken first, as for the climatology, and a year's value is the mean of its months' means, "
            "so that a year with output missing is the mean of the months there are: it is shown as an open circle. "
            "The domain and region values are area-weighted spatial means of the monthly fields."
        )
    if has("seasonal"):
        methods_.append(
            "**Seasonal cycles.** The mean seasonal cycle: the mean of each month of the year over the years of the series, over the domain, over each region and at each station."
        )
    if has("map_changes"):
        methods_.append(
            "**Map temporal changes.** Each period (the historical one and one or two future ones) is mapped as the mean of its years' annual means. "
            "The *absolute change* is the future period's map minus the historical one's, and the *percentage change* is that as a percentage of the absolute value of the historical map, so that an increase is positive, and missing where the historical value is 0. "
            "The maps of the periods of all the simulations share one colour scale, and the maps of the changes share another, which is symmetric about zero."
        )
    if has("transects"):
        methods_.append(
            "**Transects.** Each transect is a straight line that runs north-south or east-west, with the options chosen for it and the years of each. The mean of each month of each year is taken first, as for the climatology, and extracted along the line with nctoolkit's `to_transect` (bilinear interpolation onto evenly spaced points), at the surface or, for the *vertical climatology* option, through the water column. "
            "The *surface climatology* is the mean of each month of the year over the years chosen, and the *vertical climatology* section is the annual mean at each depth. "
            "The *changes* are heat maps of the position along the transect by year, where a year with months missing is left out: the *absolute* ones show the annual means, the *absolute change* each annual mean minus the first, and the *percentage change* that as a percentage of the absolute value of the first, with a rolling mean of the annual means if one was chosen (each year is then the last of its window, and the first is the first rolling mean). In the water column they are shown at some of the depths. "
            "The sections, the grids of months and the heat maps of the simulations share one colour scale."
        )
    if info.get("subregions"):
        which = {"nwes": "the northwest European Shelf regions", "global": "the global regions"}.get(
            info["subregions"], f"the regions in `{info['subregions']}`"
        )
        methods_.append(
            f"**Regions.** The regional summaries use {which}, as OceanVal's validation does: the model's field is put on the regions' grid, multiplied by each region's map, and its area-weighted spatial mean taken. "
            "A region with no cells in the model's domain is left out."
        )
    if info.get("lon_lim"):
        methods_.append(
            f"**Spatial subset.** The domain and region means of the time series are of the part of the model's domain in longitudes {info['lon_lim'][0]:g} to {info['lon_lim'][1]:g} and latitudes {info['lat_lim'][0]:g} to {info['lat_lim'][1]:g}. The stations are not cut by it."
        )
    if info.get("stations"):
        methods_.append(
            "**Stations.** The series at a station are those of the cell of each model's grid, with values, that is nearest to it, by distance over the Earth's surface. The cells used are in the tables on each variable's page."
        )
    return methods_
