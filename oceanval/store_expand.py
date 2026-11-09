"""Filling the data store on purpose: the window's "Expand" on the View the data
store page.

What is asked for is a set of built-in datasets, a range of years, an area
and whether to keep the water column. It is fetched by data_store.gridded and
data_store.ices with the arguments a matchup gives them, so the store grows by
the same rules (areas are widened to cover what is held, a piece is replaced
only by one that covers it, what is held is not downloaded again).

check() is the window's: it says what is wrong with a form. run() is done in a
process of its own (oceanval.app_child, "expand"), as matchup is, as CDO and
nctoolkit's pools are not for the window's own process.
"""

import datetime

from oceanval import data_store, prompts, server_trouble
from oceanval.create_recipes import _WOA23_PERIODS, RECIPE_CATALOGUE
from oceanval.parsers import find_recipe
from oceanval.recipes_gui import _check_pair, check_limits

# the datasets kept a year at a time, or by the files of a year
YEARLY = ("cobe2", "occci", "ices")
# whose files have levels, so that only the top may be kept (as a matchup does)
LEVELLED = ("woa23",)
# a plain file, which is downloaded and kept whole, whatever the area
WHOLE = ("glodap",)

_decadal = {(e["variable"], e["recipe"]) for e in RECIPE_CATALOGUE if e.get("decadal")}


def needs_years(name, recipe):
    """Whether the years asked for make a difference to a dataset: WOA23's
    temperature and salinity are for decades, which the years choose between."""
    return recipe in YEARLY or (name, recipe) in _decadal


def needs_levels(recipe):
    return recipe in LEVELLED


def needs_area(recipe):
    return recipe not in WHOLE


def check(form, builtin):
    """What the Expand form asks for, as (request, errors): request is the
    arguments of run(), None if errors, {box name: what is wrong}, is not
    empty. builtin is the built-in datasets (app.BUILTIN_DATASETS)."""
    errors = {}
    text = lambda name: str(form.get(name) if form.get(name) is not None else "").strip()  # noqa: E731
    known = {(x["name"], x["recipe"]) for x in builtin}
    chosen, seen = [], set()
    for item in form.get("datasets") or []:
        key = (item.get("name"), item.get("recipe")) if isinstance(item, dict) else None
        if key in known and key not in seen:
            seen.add(key)
            chosen.append({"name": key[0], "recipe": key[1]})
    if not chosen:
        errors["datasets"] = "Choose at least one dataset."

    years = None
    if any(needs_years(x["name"], x["recipe"]) for x in chosen):
        years = _check_pair(text, errors, "start", "end", "the years")
        if years is None and not any(k in errors for k in ("start", "end")):
            errors["start"] = "Give the first and last year to fetch."
        elif years is not None and years[1] > datetime.date.today().year:
            errors["end"] = "The last year cannot be in the future."

    lon_lim = lat_lim = None
    if any(needs_area(x["recipe"]) for x in chosen):
        limits = check_limits(text, errors)
        if limits is None and not any(k in errors for k in ("lon_min", "lon_max", "lat_min", "lat_max")):
            errors["lon_min"] = "Give the area to fetch: the lowest and highest longitude and latitude."
        elif limits is not None:
            lon_lim, lat_lim = limits
            if lon_lim[0] < -180 or lon_lim[1] > 180:
                errors["lon_min"] = "The data store keeps longitudes between -180 and 180."
            elif data_store.snap(lon_lim, lat_lim) is None:
                errors["lon_min"] = "That area is not one the data store can keep."

    levels = None
    if any(needs_levels(x["recipe"]) for x in chosen):
        answer = text("water_column").lower()
        if answer not in ("yes", "no"):
            errors["water_column"] = "Say whether to keep the water column."
        else:
            levels = "all" if answer == "yes" else "top"

    if errors:
        return None, errors
    return {"datasets": chosen, "years": list(years) if years else None,
            "lon_lim": lon_lim, "lat_lim": lat_lim, "levels": levels}, errors


def describe(request, builtin):
    """What a request is for, in a line the window lists it by."""
    names = [_label(x["name"], x["recipe"], builtin) for x in request["datasets"]]
    shown = ", ".join(names[:2]) + (f" and {len(names) - 2} more" if len(names) > 2 else "")
    parts = [shown]
    if request["years"]:
        first, last = request["years"]
        parts.append(str(first) if first == last else f"{first}–{last}")
    if request["lon_lim"]:
        (west, east), (south, north) = request["lon_lim"], request["lat_lim"]
        parts.append(f"lon {west:g} to {east:g}, lat {south:g} to {north:g}")
    if request["levels"]:
        parts.append("the water column" if request["levels"] == "all" else "the surface only")
    return "; ".join(parts)


# --------------------------------------------------------------------------
# what is done


def _label(name, recipe, builtin):
    for row in builtin:
        if (row["name"], row["recipe"]) == (name, recipe):
            return f"{row['variable']} ({row['dataset']})"
    return f"{name}, {recipe}"


def _decades(first, last):
    """WOA23's periods with any of first to last in them."""
    return [(a, b) for a, b in _WOA23_PERIODS if a <= last and first <= b]


def _gridded_jobs(name, recipe, years, levels):
    """The calls to data_store.gridded that fetch a dataset: [{"files",
    "thredds", "variable", "years", "levels", "total"}]. They are as a matchup
    makes them from find_recipe: OCCCI's files are picked by their years, and
    WOA23's temperature and salinity are one call for each decade."""
    if (name, recipe) in _decadal:
        periods = _decades(*years)
        if not periods:
            raise ValueError(
                f"WOA23's temperature and salinity are for {_WOA23_PERIODS[0][0]} to {_WOA23_PERIODS[-1][1]}: "
                f"there are none for {years[0]}-{years[1]}."
            )
        found = [find_recipe({name: recipe}, start=a, end=b) for a, b in periods]
    else:
        found = [find_recipe({name: recipe})]
    jobs = []
    for each in found:
        files = each["obs_path"]
        wanted = list(range(years[0], years[1] + 1)) if years else None
        if recipe == "occci":
            from oceanval.gridded import _filter_occci_paths

            files = _filter_occci_paths(list(files), set(wanted), range(1, 13))
            if not files:
                raise ValueError(f"There are no OCCCI files for {years[0]}-{years[1]}.")
        by_year = recipe in data_store.BY_YEAR
        jobs.append({
            "builtin": each["builtin"], "files": files, "thredds": bool(each.get("thredds")),
            "variable": each["obs_variable"], "years": wanted if by_year else None,
            "levels": levels if recipe in LEVELLED else "all",
            "total": len(wanted) if by_year else (1 if isinstance(files, str) else len(files)),
        })
    return jobs


def _ices_codes(name):
    codes = find_recipe({name: "ices"})["ices_dataset"]
    return [codes] if isinstance(codes, str) else list(codes)


def _problem(error, files=None):
    """What went wrong, as text."""
    answer = server_trouble.answer_of(error)
    if answer is not None:
        return " ".join(server_trouble.explain(answer, server_trouble.host_of(files)))
    return f"{type(error).__name__}: {error}"


def run(datasets, years, lon_lim, lat_lim, levels, builtin=None, progress=None):
    """Fetch what check() made a request of into the data store saved in the
    settings, telling progress (prompts.tell, by default) how each is going: a
    first {"kind": "expand", "items": [{key, label, total}]}, then one
    {"kind": "expand", "item": key, "status": "working" | "done" | "failed",
    "done", "total", "saved", "held", "error"} as each goes on. Returns
    whether every one was fetched."""
    tell = progress or prompts.tell
    if builtin is None:
        from oceanval import app

        builtin = app.BUILTIN_DATASETS
    store = data_store.config(None)
    if store is None:
        raise ValueError("There is no data store: set a folder in Tools & checks, Preferences, first.")
    # held data is not fetched again, whichever way the store is used for matchups
    data_store.start(dict(store, use="always"))
    if data_store.active() is None:
        raise ValueError("The data store could not be used: see the message above.")
    box = None
    if lon_lim is not None:
        box = (lon_lim[0], lon_lim[1], lat_lim[0], lat_lim[1])
    elif any(not needs_area(x["recipe"]) for x in datasets):
        box = tuple(data_store.GLOBAL)

    items, codes = [], {}
    for each in datasets:
        name, recipe = each["name"], each["recipe"]
        if recipe == "ices":
            for code in _ices_codes(name):
                codes.setdefault(code, []).append(name)
            continue
        item = {"key": f"{name}/{recipe}", "label": _label(name, recipe, builtin), "name": name, "recipe": recipe}
        try:
            item["jobs"] = _gridded_jobs(name, recipe, years, levels)
            item["total"] = sum(j["total"] for j in item["jobs"])
        except Exception as error:
            item["jobs"], item["total"], item["error"] = [], 0, _problem(error)
        items.append(item)
    for code, names in codes.items():
        n = (years[1] - years[0] + 1) if years else 0
        items.append({"key": f"ices/{code}", "label": f"ICES {code} export", "code": code,
                      "total": n, "jobs": []})
    tell({"kind": "expand", "items": [{"key": i["key"], "label": i["label"], "total": i["total"]} for i in items]})

    everything = True
    for item in items:
        state = {"done": 0, "saved": 0, "held": 0}

        def news(status, **more):
            tell(dict({"kind": "expand", "item": item["key"], "status": status, "total": item["total"],
                       "done": state["done"], "saved": state["saved"], "held": state["held"]}, **more))

        def on_piece(what, status):
            state["done"] += 1
            if status == "saved":
                state["saved"] += 1
            elif status == "held":
                state["held"] += 1
            news("working")

        if item.get("error"):
            news("failed", error=item["error"])
            everything = False
            continue
        news("working")
        try:
            failed = 0
            if "code" in item:
                counts = data_store.ices(
                    [item["code"]], years[0], years[1], box[:2], box[2:], _ices_download(),
                    store_only=True, on_piece=on_piece,
                )
                if counts is None:
                    raise ValueError("The data store could not keep that area.")
            else:
                for job in item["jobs"]:
                    counts = data_store.gridded(
                        {"builtin": job["builtin"], "obs_variable": job["variable"]}, job["files"],
                        job["thredds"], box, job["levels"], years=job["years"],
                        label=item["label"], store_only=True, on_piece=on_piece,
                    )
                    if counts is None:
                        raise ValueError("The data store could not be used for it.")
                    failed += counts["failed"]
            if failed:
                news("failed", error=f"{failed} could not be downloaded: the server did not answer, so what the store has was kept.")
                everything = False
            else:
                state["done"] = item["total"]
                news("done")
        except Exception as error:
            files = item["jobs"][0]["files"] if item["jobs"] else None
            news("failed", error=_problem(error, files))
            everything = False
    return everything


def _ices_download():
    from oceanval import ices

    return ices._download_exports
