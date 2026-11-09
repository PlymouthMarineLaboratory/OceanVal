"""What the window's "View the data store" page shows: every built-in dataset,
with what the data store holds of it (if anything). It only looks: see
data_store.pieces."""

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
        if kind == "ices":
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


def _stored(row, held):
    """How a dataset is held: the year, the water column and the extent."""
    if row["point"]:
        # one export of an ICES dataset and year serves every variable from it
        mine = [p for p in held if p["kind"] == "ices" and p["dataset"] in _recipe(row["name"], row["recipe"])[1]]
        with_data = [p for p in mine if p.get("rows")]
        if not with_data:
            return ("No data found" if mine else None), None, None
        return (
            years_text(p["year"] for p in with_data),
            "All depths",
            _extent(with_data),
        )
    variable = _recipe(row["name"], row["recipe"])[0]
    mine = [p for p in held if p["kind"] == "gridded" and p["dataset"] == row["recipe"] and p["variable"] == variable]
    if not mine:
        return None, None, None
    years = [p["year"] for p in mine if isinstance(p.get("year"), int)]
    if years:
        year = years_text(years)
    else:
        year = f"{len(mine)} file" + ("" if len(mine) == 1 else "s")
    if all(p.get("levels") == "top" for p in mine) or not row["water_column"]:
        column = "Surface only"
    else:
        column = "All depths"
    return year, column, _extent(mine)


def view(datasets, settings):
    """The page's reply, as {"ok", "has_store", "root", "note", "rows"}: a row
    for each of datasets (app.BUILTIN_DATASETS), whether in the store or not.
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
        year, column, extent = _stored(row, held)
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
