"""What the window's "View the data store" page shows: every built-in dataset,
with what the data store holds of it (if anything). It only looks: see
data_store.pieces."""

import datetime
import os

from oceanval import data_store, netcdf_lock, store_expand
from oceanval.create_recipes import _WOA23_PERIODS, RECIPE_CATALOGUE
from oceanval.parsers import find_recipe

NOT_SET = (
    "No data store is set, so nothing is kept. Set a folder in Tools & checks, Preferences, "
    "to keep the observations OceanVal downloads."
)
EMPTY = "There is nothing in the data store yet."
NOT_DOWNLOADED = "Not downloaded"

_coverage = {(e["variable"], e["recipe"]): e["coverage"]["years"].split(",")[0] for e in RECIPE_CATALOGUE}
_recipes = {}

# A dataset is complete when the store holds all of it: every year (or file) it
# has, its whole area and, for WOA23, the whole water column. Then the page shows
# Complete, not Expand, as expanding it would fetch nothing.
#
# the datasets whose past years keep gaining observations, so the store never
# holds all of them
LIVE = ("ices",)
# the years of a dataset kept a year at a time, (first, last), last None for "to
# the present" (the catalogue's range)
_ranges = {e["recipe"]: e["coverage"]["range"] for e in RECIPE_CATALOGUE if e["coverage"].get("range")}
# the first year kept, where it is not the first of the range: a year without
# all 12 months is not kept, and OISST starts in September 1981
_FIRST_KEPT = {"oisst": 1982}


def _last_full_year():
    """The last year a dataset that runs to the present can have all 12 months
    of: the year now is never kept, as it has not got them yet."""
    return datetime.date.today().year - 1


def _urls(name, recipe):
    """The server's files a gridded built-in is fetched from, every one (each
    decade's, for WOA23's temperature and salinity), and whether it is a
    climatology."""
    key = ("urls", name, recipe)
    if key not in _recipes:
        try:
            found = [find_recipe({name: recipe})]
        except ValueError:
            found = [find_recipe({name: recipe}, start=first, end=last) for first, last in _WOA23_PERIODS]
        urls = []
        for each in found:
            paths = each.get("obs_path")
            urls += [paths] if isinstance(paths, str) else list(paths or [])
        _recipes[key] = (urls, bool(found[0].get("climatology")), len(found) > 1)
    return _recipes[key]


def _whole(piece, recipe):
    """Whether a piece has all of a dataset's area (and water column) in it."""
    box = piece.get("box")
    domain = data_store.DOMAINS.get(recipe, data_store.GLOBAL)
    return (
        isinstance(box, list) and len(box) == 4 and data_store.contains(box, domain)
        and (not store_expand.needs_levels(recipe) or piece.get("levels") == "all")
    )


def _gridded_state(name, recipe, mine):
    """What of a gridded dataset the store holds all of, as (years, complete):
    years is the text the page shows when every year (decade, month) is held,
    or None; complete is whether every one of them is held whole."""
    if recipe in data_store.BY_YEAR:
        first, last = _ranges[recipe]
        first = _FIRST_KEPT.get(recipe, first)
        last = _last_full_year() if last is None else last
        units = {(None, year) for year in range(first, last + 1)}
        label = "All years"
    else:
        urls, climatology, decadal = _urls(name, recipe)
        units = {(url, None) for url in urls}
        label = "All decades" if decadal else "All months" if climatology and len(urls) > 1 else "All years"
    held, whole = set(), set()
    for piece in mine:
        unit = (None, piece.get("year")) if recipe in data_store.BY_YEAR else (piece.get("url"), None)
        if unit in units:
            held.add(unit)
            if _whole(piece, recipe):
                whole.add(unit)
    if not units or held != units:
        return None, False
    return label, whole == units


def _recipe(name, recipe):
    """What find_recipe says of a built-in (no network is used), kept: the
    variable in the data's files and, for ICES, the datasets it is from."""
    if (name, recipe) not in _recipes:
        try:
            found = find_recipe({name: recipe})
        except ValueError:
            # WOA23 temperature and salinity are published for each decade
            first, last = _WOA23_PERIODS[-1]
            found = find_recipe({name: recipe}, start=first, end=last)
        codes = found.get("ices_dataset")
        _recipes[name, recipe] = (
            found.get("obs_variable"),
            [codes] if isinstance(codes, str) else list(codes or []),
        )
    return _recipes[name, recipe]


def years_text(years):
    """Years as the runs of them: [1995, 1997, 1998, 1999] is "1995, 1997–1999"."""
    runs = []
    for year in sorted(set(years)):
        if runs and year == runs[-1][1] + 1:
            runs[-1][1] = year
        else:
            runs.append([year, year])
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in runs)


def extent_text(boxes):
    """The area the boxes (west, east, south, north) cover together, as text."""
    west = round(min(b[0] for b in boxes), 2)
    east = round(max(b[1] for b in boxes), 2)
    south = round(min(b[2] for b in boxes), 2)
    north = round(max(b[3] for b in boxes), 2)
    return f"lon {west:g} to {east:g}, lat {south:g} to {north:g}"


def _wrapped(lon):
    """Longitudes as -180 to 180: the files of some datasets run 0 to 360, and
    OceanVal reads those above 180 as lon - 360."""
    import numpy as np

    lon = np.asarray(lon, dtype=float)
    return np.where(lon > 180, lon - 360, lon)


_looked_at = {}


def _stat(path):
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size


def _box_of_file(path, kind):
    """The area a stored file holds data for, (west, east, south, north), from
    the file itself, or None if that cannot be told. Looked at once for each
    file as it is, as an ICES export is a few MB to read."""
    key = (path, _stat(path), kind)
    if key in _looked_at:
        return _looked_at[key]
    box = None
    try:
        if kind in ("glodapv3", "hansell"):
            import pandas as pd

            found = pd.read_csv(path, usecols=["lon", "lat"])
            if len(found):
                box = (float(found.lon.min()), float(found.lon.max()), float(found.lat.min()), float(found.lat.max()))
        elif kind == "ices":
            import pandas as pd

            found = pd.read_csv(
                path, usecols=lambda name: name.startswith(("Longitude", "Latitude")), low_memory=False
            )
            lon = [c for c in found.columns if c.startswith("Longitude")]
            lat = [c for c in found.columns if c.startswith("Latitude")]
            lons, lats = _wrapped(found[lon[0]].dropna()), found[lat[0]].dropna()
            if len(lons) and len(lats):
                box = (float(lons.min()), float(lons.max()), float(lats.min()), float(lats.max()))
        else:
            import xarray as xr

            # a read of a netCDF file in this process holds the lock all of them do
            with netcdf_lock.LOCK:
                with xr.open_dataset(path, decode_times=False, decode_cf=False) as ds:
                    names = {name.lower(): name for name in ds.variables}
                    lon = ds.variables[names["lon" if "lon" in names else "longitude"]].values
                    lat = ds.variables[names["lat" if "lat" in names else "latitude"]].values
            lon = _wrapped(lon)
            box = (float(lon.min()), float(lon.max()), float(lat.min()), float(lat.max()))
    except Exception:
        box = None
    if len(_looked_at) > 2000:
        _looked_at.clear()
    _looked_at[key] = box
    return box


def _extent(pieces):
    """The area the files of these pieces cover together, as text. Files that are
    the same grid (the same area asked for) are looked at once."""
    boxes, groups = [], {}
    for piece in pieces:
        if piece.get("path"):
            groups.setdefault((piece["kind"], tuple(piece.get("box") or ())), []).append(piece)
    for (kind, _), group in groups.items():
        # ICES files differ in where their stations are, so each is looked at
        for piece in (group if kind == "ices" else group[:1]):
            box = _box_of_file(piece["path"], kind)
            if box is not None:
                boxes.append(box)
    return extent_text(boxes) if boxes else "Could not be read"


def _stored(row, held, extent=True):
    """How a dataset is held: the year, the water column, the extent (None if
    not extent, as the files are read for it), the text shown when all its years
    are held (None if they are not) and whether it is complete."""
    area = _extent if extent else (lambda pieces: None)
    if row["point"] and row["recipe"] in ("glodapv3", "hansell"):
        # the whole globe and every year of the variable, in one piece: complete once held
        mine = [p for p in held if p["kind"] == row["recipe"] and p["variable"] == row["name"]]
        if not mine:
            return None, None, None, None, False
        if row["recipe"] == "hansell":
            from oceanval.hansell import YEARS

            first, last = YEARS[row["name"]]
            years = f"{first}–{last}"
        else:
            years = "1972–2023"
        return years if mine[0].get("rows") else "No data found", "All depths", area(mine), "All years", True
    if row["point"]:
        # one export of an ICES dataset and year serves every variable from it;
        # its years keep gaining observations, so it is never complete (LIVE)
        mine = [p for p in held if p["kind"] == "ices" and p["dataset"] in _recipe(row["name"], row["recipe"])[1]]
        with_data = [p for p in mine if p.get("rows")]
        if not with_data:
            return ("No data found" if mine else None), None, None, None, False
        return (
            years_text(p["year"] for p in with_data),
            "All depths",
            area(with_data),
            None,
            False,
        )
    variable = _recipe(row["name"], row["recipe"])[0]
    mine = [p for p in held if p["kind"] == "gridded" and p["dataset"] == row["recipe"] and p["variable"] == variable]
    if not mine:
        return None, None, None, None, False
    years = [p["year"] for p in mine if isinstance(p.get("year"), int)]
    if years:
        year = years_text(years)
    else:
        year = f"{len(mine)} file" + ("" if len(mine) == 1 else "s")
    if all(p.get("levels") == "top" for p in mine) or not row["water_column"]:
        column = "Surface only"
    else:
        column = "All depths"
    all_years, complete = (None, False) if row["recipe"] in LIVE else _gridded_state(row["name"], row["recipe"], mine)
    return year, column, area(mine), all_years, complete


def complete(datasets, settings):
    """The (name, recipe) of each of datasets the data store saved in settings
    holds all of (see _stored), without reading its files."""
    folder = (settings or {}).get("data_store") or ""
    root = data_store.root(folder) if folder else None
    if not root or not os.path.isdir(root):
        return set()
    held, problem = data_store.pieces(root)
    if problem:
        return set()
    return {(row["name"], row["recipe"]) for row in datasets if _stored(row, held, extent=False)[4]}


def view(datasets, settings):
    """The page's reply, as {"ok", "has_store", "root", "note", "rows"}: a row
    for each of datasets (app.BUILTIN_DATASETS), whether in the store or not,
    with whether the store has all its years (years_complete) and all of it
    (complete).
    The folder is the one saved in settings, never one the page names."""
    folder = (settings or {}).get("data_store") or ""
    root = data_store.root(folder) if folder else None
    held, note = [], None
    if not folder:
        note = NOT_SET
    elif not os.path.isdir(root):
        note = EMPTY
    else:
        held, problem = data_store.pieces(root)
        if problem:
            note = f"The data store's index could not be read, so nothing is shown of it: {problem}"
        elif not held:
            note = EMPTY
    rows = []
    for row in datasets:
        year, column, extent, all_years, whole = _stored(row, held)
        covered = None if row["point"] else _coverage.get((row["name"], row["recipe"]))
        rows.append(
            {
                "name": row["name"],
                "recipe": row["recipe"],
                "variable": row["variable"],
                "source": row["dataset"],
                "url": row["url"],
                "year": year or NOT_DOWNLOADED,
                "year_covered": covered,
                "downloaded": year is not None,
                "water_column": column or ("Yes" if row["water_column"] else "No"),
                "water_column_stored": column is not None,
                "extent": extent or "—",
                # "All years" (decades, months) when every one is held, else None
                "years_complete": all_years,
                "complete": whole,
            }
        )
    # what the Expand form can fetch: each dataset, and which of its boxes make a difference to it
    fetchable = [
        {
            "name": row["name"], "recipe": row["recipe"], "variable": row["variable"],
            "source": row["dataset"], "point": row["point"],
            "years": store_expand.needs_years(row["name"], row["recipe"]),
            "levels": store_expand.needs_levels(row["recipe"]),
            "area": store_expand.needs_area(row["recipe"]),
        }
        for row in datasets
    ]
    return {"ok": True, "has_store": bool(folder), "root": root, "note": note, "rows": rows, "datasets": fetchable}
