"""The oceanval command: OceanVal in a window in your web browser.

Run ``oceanval`` (or ``OceanVal``) in a terminal, in the directory to work
in, and a page opens in your web browser that takes you through:

1. choosing to match up new data and validate it, to match up only, or to
   validate matchups made earlier;
2. the simulation to match up, which create_recipes then reads - or, to
   validate only, the options for validate();
3. the create_recipes window, whose button then starts the matchup rather
   than only writing the script;
4. the run: the script create_recipes wrote, or validate(), in a process of
   its own (see oceanval.app_child). The page shows its output as it comes,
   which is printed in the terminal too, and asks any question it asks.
   While it asks whether the matchups are right, the page reads the dates
   of the files to be matched up and shows which are missing (see
   oceanval.model_check). To match up and validate, once the matchups are
   checked, a last step asks for the report's options before anything is
   matched up, and validate() is run with them after the script. matchup
   builds an interim report with them as it goes (see oceanval.live), which
   the page links to once its first page is made.

Every step after the first has Back, until anything is matched up, and
what was entered in a step is kept for when it is reached again: the
recipes window is shown again as it was left, and Back from the matchups
stops the run, which is waiting for its answer, for the units step (see
App.back).

Like the create_recipes window (see oceanval.recipes_gui), the page is
served only on 127.0.0.1, only to requests carrying the random token in its
link, and loads nothing from anywhere else. It serves the reports too, the
interim one and the full one, so that they open from the page, which a
file:// link would not, and over a forwarded port.
"""

import argparse
import calendar
import codecs
import contextlib
import atexit
import copy
import datetime
import functools
import glob
import io
import itertools
import json
import mimetypes
import os
import queue
import random
import re
import secrets
import select
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse

import oceanval
from oceanval import domain_mask, leftovers, live, netcdf_lock, prompts, recipes_gui
from oceanval.app_child import ANSWER_MARKER, PROGRESS_MARKER, QUESTION_MARKER
from oceanval.analysis_app import SIMCOMPARE, AnalyzeSteps
from oceanval.sim_compare_app import SimCompareSteps
from oceanval.viewer_app import VIEWER, ViewerSteps
from oceanval import viewer_app
from oceanval import sim_compare
from oceanval import analysis
from oceanval import analysis_report
from oceanval import data_store
from oceanval import cdo_check, depths, model_check, output_years, own_data, recipe_checks, recipe_forms, selection, time_res, time_stamps, transects, units, user_cache, user_recipes, user_settings
from oceanval.create_recipes import (
    DOMAIN_REGIONS,
    POINT_RECIPE_CATALOGUE,
    RECIPE_CATALOGUE,
    _create_recipes,
    _literal,
    _unknown_variables,
    _variable_long_names,
    recipe_variables,
    folder_layout,
    simulation_paths,
    simulation_years,
)


# the regions of the built-in recipes, as https://PlymouthMarineLaboratory.github.io/OceanVal/recipes.html
# names them (the ICES catalogue entries carry none)
_DATASET_REGIONS = {"Northwest European Shelf": "NW European Shelf"}
_POINT_DATASET_REGION = "Northeast Atlantic"
# the web pages of the datasets whose catalogue entries give none, or a better one
_DATASET_URLS = {
    "nsbc": "https://www.cen.uni-hamburg.de/en/icdc/data/ocean/nsbc.html",
    "ices": "https://www.ices.dk/data/data-portals/Pages/ocean.aspx",
}
_DATASET_NOTE = re.compile(r"^(?P<title>.+?) - (?P<dataset>.+?)(?: \((?P<url>https?://\S+)\))?$")


def builtin_datasets():
    """The built-in recipes for the Info panel's table: what the recipes
    page of the website lists, a dict of the region, variable, recipe,
    dataset (and its url, if there is one), whether it is water-column
    ("vertical" is an option) and the units, for the gridded recipes and then
    the point ones. The user's own recipes are left out."""
    rows = []
    for point, catalogue in ((False, RECIPE_CATALOGUE), (True, POINT_RECIPE_CATALOGUE)):
        for entry in catalogue:
            note = _DATASET_NOTE.match(entry["notes"][0])
            region = entry.get("region")
            rows.append(
                {
                    "point": point,
                    "region": _POINT_DATASET_REGION if point else _DATASET_REGIONS.get(region, region),
                    "variable": user_recipes.display_name(entry["variable"], note["title"]),
                    "recipe": entry["recipe"],
                    "dataset": note["dataset"],
                    "url": _DATASET_URLS.get(entry["recipe"], note["url"]),
                    "water_column": any("vertical" in line for line in entry["arguments"]),
                    "units": entry["units"],
                }
            )
    return rows


BUILTIN_DATASETS = builtin_datasets()

# what the validation page offers: the last registers recipes of your own
# (see oceanval.user_recipes), which is not a run. The landing page offers
# "analyze" (see oceanval.analysis_app), and VALIDATE_MENU, the validation page
ACTIONS = ("matchup_validate", "matchup", "validate", "compare", "register")
ANALYZE = "analyze"
VALIDATE_MENU = "validate_menu"

# closing the browser quits the command: a page open in it has a request
# waiting on the app (the state's, or a heartbeat held for HEARTBEAT_HOLD
# seconds), which keeps it known to the app, and once every page has been
# gone for PAGE_TIMEOUT seconds the window counts as closed. A page in a tab
# not looked at says so (see page_visibility), as a browser may slow or
# freeze it then (Safari does), and is given HIDDEN_PAGE_TIMEOUT instead. A
# run is never stopped for it: the window quits once the run is over
HEARTBEAT_HOLD = 10
PAGE_TIMEOUT = 15
HIDDEN_PAGE_TIMEOUT = 30 * 60
# a page that said it is being unloaded (tab or browser closed, or reloaded: the
# new page is heard from at once), though a closing tab says it is hidden first
CLOSING_PAGE_TIMEOUT = 4

# how many simulations the compare step has rows for
COMPARE_ROWS = 5

# where compare() builds the comparison's pages, in its out_dir, and the one
# it opens first
COMPARISON_HTML = ("oceanval_comparison", "compare", "_build", "html")
COMPARISON_LANDING = ("notebooks", "comparison_seasonal.html")

# what an Analyze or Compare simulations run tells of its variables (see
# oceanval.analysis._tell), for the page's grid of them
ANALYSIS_NEWS = ("analyses", "analysis_variable", "analysis")

# how long the recipes window waits for the years of the output to be read
# from its files' times (a few seconds, usually done before it opens), before
# its years are boxes to type in instead of lists of them
OUTPUT_YEARS_WAIT = 120

# the folder browser lists at most this many folders, and counts the rest
_MOST_FOLDERS = 2000

# the sample of a simulation's files lists at most this many, and counts the rest
_MOST_FILES = 500

# the demo: one of three CMIP6 models, each from a different ESGF server, of
# which the user chooses 1-3 of sea surface temperature, surface nitrate and
# sea surface salinity. They are downloaded into DEMO_FOLDER, in the directory
# worked in, and matched up for DEMO_YEAR only, a year in all of the files
def _demo_model(label, full, server, member, folder, period, versions, megabytes):
    """A model of the demo: where each variable's file is, and its size."""
    files = {}
    for variable in DEMO_VARIABLES:
        name = f"{variable}_Omon_{label}_historical_{member}_gn_{period}.nc"
        files[variable] = {
            "name": name,
            "url": f"{folder}/{variable}/gn/{versions[variable]}/{name}",
            "megabytes": megabytes[variable],
        }
    first, last = period.split("-")
    covers = " to ".join(
        f"{calendar.month_name[int(text[4:])]} {text[:4]}" for text in (first, last)
    )
    return {
        "label": label,
        "full": full,
        "server": server,
        "member": member,
        "covers": covers,
        "files": files,
    }


# the demo's variables, in the order of the page's columns
DEMO_VARIABLES = ("tos", "no3os", "sos")

DEMO_MODELS = {
    model["label"]: model
    for model in (
        _demo_model(
            "NorESM2-LM",
            "the Norwegian Earth System Model (low-resolution atmosphere)",
            "noresg.nird.sigma2.no",
            "r3i1p1f1",
            "https://noresg.nird.sigma2.no/thredds/fileServer/esg_dataroot/cmor/CMIP6/CMIP/"
            "NCC/NorESM2-LM/historical/r3i1p1f1/Omon",
            "201001-201412",
            # nitrate was published later than the others, so its version differs
            {"tos": "v20190920", "sos": "v20190920", "no3os": "v20191108"},
            {"tos": 17.2, "no3os": 18.2, "sos": 14.9},
        ),
        _demo_model(
            "MPI-ESM1-2-LR",
            "the Max Planck Institute Earth System Model (low resolution)",
            "esgf3.dkrz.de",
            "r1i1p1f1",
            "https://esgf3.dkrz.de/thredds/fileServer/cmip6/CMIP/MPI-M/MPI-ESM1-2-LR/historical/r1i1p1f1/Omon",
            "201001-201412",
            {"tos": "v20190710", "sos": "v20190710", "no3os": "v20190710"},
            {"tos": 9.2, "no3os": 9.6, "sos": 8.3},
        ),
        _demo_model(
            "UKESM1-0-LL",
            "the UK Earth System Model (low resolution)",
            "esgf.ceda.ac.uk",
            "r1i1p1f2",
            "https://esgf.ceda.ac.uk/thredds/fileServer/esg_cmip6/CMIP6/CMIP/MOHC/UKESM1-0-LL/"
            "historical/r1i1p1f2/Omon",
            # its files cover 1950 to 2014, so they are much bigger
            "195001-201412",
            {"tos": "v20190627", "sos": "v20190627", "no3os": "v20191105"},
            {"tos": 159.3, "no3os": 162.0, "sos": 136.2},
        ),
    )
}

DEMO_FOLDER = "oceanval_demo"
DEMO_YEAR = 2010

# the limits the demo's models' grids are matched up within, which matchup is
# given with the cores, once the matchups are confirmed
DEMO_LIMITS = {
    "lon_min": "-180",
    "lon_max": "180",
    "lat_min": "-90",
    "lat_max": "90",
}


def default_setup_form():
    """The simulation step's boxes, as they start."""
    return {
        "simdir": "",
        "ndown": "",
        "domain": "",
        "start": "",
        "end": "",
        "exclude": "",
        "require": "",
    }


def default_output_form(cwd=None):
    """The output step's boxes, the last before a matchup starts, as they
    start: where the matchups (and the report) are saved, and the script,
    named matchup.py, or the first of matchup1.py... that is free in cwd."""
    return {
        "out_dir": "",
        # asked only if out_dir has matchups already (see check_output): the
        # "variable/source" of those not used, replaced if this run makes them
        # and ignored if not (the others are used), and whether the user has
        # confirmed what is done with them. Until the user chooses in the grid
        # (existing_chosen) unused is not looked at: the matchups this run makes
        # are replaced to start with, and the others used
        "unused": [],
        "existing_chosen": False,
        "existing_confirmed": False,
        "out": free_name(cwd, "matchup"),
        # whether the user typed the script's name, the only one that is warned
        # of when the file exists already
        "out_typed": False,
        # matchup alone's, which has no report options to ask them with: the
        # cores (chosen every time), the spatial subset and the missing values
        "cores": "",
        "lon_min": "",
        "lon_max": "",
        "lat_min": "",
        "lat_max": "",
        "missing_from": "",
        "missing_to": "",
        # whether part of the domain is masked out, by a file (see check_domain_mask)
        "domain_mask": "no",
        "domain_mask_path": "",
    }


def inspect_domain_mask(text, cwd):
    """The domain mask in the file typed (see oceanval.domain_mask.inspect),
    relative to cwd: (absolute path, mask, error in words for the user)."""
    if not text:
        return None, None, "Give the path of the netCDF file."
    path = _path(text, cwd)
    with netcdf_lock.LOCK:
        mask, error = domain_mask.inspect(path)
    return path, mask, error


def check_domain_mask(form, cwd, errors):
    """matchup's domain_mask, from form's domain_mask ("yes" or "no", No to
    start) and domain_mask_path: the absolute path of the file, or None, with
    an error in errors if Yes has no file that is a mask."""
    if form.get("domain_mask") != "yes":
        return None
    path, _, error = inspect_domain_mask(form.get("domain_mask_path", ""), cwd)
    if error is not None:
        errors["domain_mask_path"] = error
        return None
    return path


def default_validate_form():
    """The report options step's boxes, as they start."""
    return {
        "data_dir": "",
        "out_dir": "",
        "lon_min": "",
        "lon_max": "",
        "lat_min": "",
        "lat_max": "",
        "subregions": "",
        "subregions_file": "",
        "fixed_scale": False,
        "pdf": False,
        "word": False,
        "zip": False,
        "concise": True,
        "transect": False,
        "transect_start_lon": "",
        "transect_start_lat": "",
        "transect_end_lon": "",
        "transect_end_lat": "",
        "depth_bins": default_depth_boxes(),
        # the matchups listed whose ticks were taken off, by their
        # "variable/source": any others, as of matchups found later, are ticked
        "unticked": [],
        # matchup's, asked with the report options before anything is matched
        # up: the cores, the missing values, and the spatial subset (the
        # lon/lat boxes, which are validate's only when nothing is to be matched up)
        "cores": "",
        "missing_from": "",
        "missing_to": "",
        # whether part of the domain is masked out, by a file (see check_domain_mask)
        "domain_mask": "no",
        "domain_mask_path": "",
    }


def default_depth_boxes():
    """The depth bins' boxes, as they start: OceanVal's own bins, each a
    [from, to] pair as typed, with to empty for the deepest."""
    return [
        ["" if depth is None else str(depth) for depth in pair]
        for pair in depths.DEFAULT_DEPTH_BINS
    ]


def demo_setup_form(model):
    """The simulation step's boxes, as the demo fills them in: the
    downloaded files. The files of every model tried share a directory,
    so only the chosen model's are used: those with its name in."""
    return dict(
        default_setup_form(),
        simdir=os.path.join(DEMO_FOLDER, "simulation"),
        ndown="0",
        domain="global",
        require=model,
        start=str(DEMO_YEAR),
        end=str(DEMO_YEAR),
    )


def demo_output_form():
    """The output step's boxes, as the demo fills them in: the matchups,
    the report and the script beside the downloaded files, in DEMO_FOLDER.
    Nothing is asked about the matchups of a model tried before there (see
    check_output)."""
    return dict(
        default_output_form(),
        out_dir=DEMO_FOLDER,
        out=os.path.join(DEMO_FOLDER, "matchup.py"),
        **DEMO_LIMITS,
    )


# the detailed demo's transect: south to north, down the middle of the Pacific
# along the date line, from the Southern Ocean to the Bering Strait
DEMO_TRANSECT = {"start": [180, -70], "end": [180, 65]}


def demo_report_form(concise=True):
    """The report options step's boxes, as the demo fills them in: concise
    or detailed, as chosen on the instructions. The concise report has no
    subregions or transect, and the detailed one has the global subregions
    and a transect down the middle of the Pacific, with PDF and Word versions
    too."""
    form = dict(
        default_validate_form(),
        subregions="" if concise else "global",
        concise=concise,
        pdf=not concise,
        word=not concise,
        transect=not concise,
        **DEMO_LIMITS,
    )
    if not concise:
        (start_lon, start_lat), (end_lon, end_lat) = DEMO_TRANSECT["start"], DEMO_TRANSECT["end"]
        form.update(
            transect_start_lon=str(start_lon),
            transect_start_lat=str(start_lat),
            transect_end_lon=str(end_lon),
            transect_end_lat=str(end_lat),
        )
    return form


# the boxes the demo fills in, which the page marks as OceanVal's
DEMO_PREFILLED = {
    "setup": ["simdir", "ndown", "require"],
    "output": ["out_dir", "out", *DEMO_LIMITS],
    "report": [
        *DEMO_LIMITS, "subregions", "concise", "pdf", "word", "transect",
        "transect_start_lon", "transect_start_lat", "transect_end_lon", "transect_end_lat",
    ],
}


class _Cancelled(Exception):
    """The demo's download was stopped, as OceanVal closed."""


def _fetch(url, path, progress, cancelled):
    """Download url to path, calling progress(done, total) as it goes, where
    total is None if the server does not say. Written to path + ".part"
    first, so that a file at path is always complete. Raises _Cancelled if
    cancelled is set before it has finished."""
    # only needed for the demo
    import requests

    partial = path + ".part"
    try:
        with requests.get(url, stream=True, timeout=60) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0) or None
            done = 0
            with open(partial, "wb") as file:
                for chunk in response.iter_content(1 << 16):
                    if cancelled.is_set():
                        raise _Cancelled
                    file.write(chunk)
                    done += len(chunk)
                    progress(done, total)
        os.replace(partial, path)
    finally:
        with contextlib.suppress(OSError):
            os.remove(partial)


# the boxes of the transect's two ends, in validate()'s order: [lon, lat] of
# the start, then of the end
TRANSECT_BOXES = (
    "transect_start_lon",
    "transect_start_lat",
    "transect_end_lon",
    "transect_end_lat",
)

# why the transect boxes cannot be used, as the page says it too
TRANSECT_EMPTY = "Fill in the longitude and latitude of both ends."
TRANSECT_RULE = (
    "The transect must run north–south or east–west: give both ends the same "
    "longitude, or the same latitude."
)


def _path(text, cwd):
    """A path typed into the page, which is relative to the directory the
    app works in."""
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(text)))


def free_name(cwd, stem, extension=".py"):
    """The first of stem.py, stem1.py, stem2.py... that does not exist in cwd:
    the name a script starts with, so an earlier one is not written over
    unless the user types its name."""
    name, number = stem + extension, 0
    while os.path.lexists(os.path.join(cwd or ".", name)):
        number += 1
        name = f"{stem}{number}{extension}"
    return name


def _shown(path, cwd):
    """A path as the page shows it: relative, if it is inside cwd."""
    relative = os.path.relpath(path, cwd)
    return path if relative.startswith("..") else relative


# a point_time_res as the page sends it, "year,month", and back
_TIME_RES_VALUES = {",".join(value): list(value) for value, _, _ in time_res.POINT_TIME_RES_OPTIONS}


def default_point_time_res_form(rows):
    """What the point_time_res step starts with: OceanVal's suggestion for
    all of the datasets it lists in rows, each of which keeps its own until
    changed."""
    return {
        "mode": "all",
        "all": ",".join(time_res.SUGGESTED),
        "each": {row["key"]: ",".join(row["point_time_res"]) for row in rows},
    }


def point_time_res_choice(form, rows):
    """The choice (see oceanval.time_res.clean_choice) the point_time_res
    step's form makes, and the form as it is kept, or raises ValueError."""
    defaults = default_point_time_res_form(rows)
    form = form if isinstance(form, dict) else {}
    mode = form.get("mode") if form.get("mode") in ("all", "each", "keep") else None
    if mode is None:
        raise ValueError("Choose what to do with them.")
    every = form.get("each") if isinstance(form.get("each"), dict) else {}
    kept = {
        "mode": mode,
        "all": str(form.get("all") or defaults["all"]),
        "each": {key: str(every.get(key) or value) for key, value in defaults["each"].items()},
    }
    if mode == "keep":
        return time_res.clean_choice(None, rows), kept
    if mode == "all":
        if kept["all"] not in _TIME_RES_VALUES:
            raise ValueError("Choose how to match them.")
        choice = {"default": _TIME_RES_VALUES[kept["all"]]}
    else:
        if any(value not in _TIME_RES_VALUES for value in kept["each"].values()):
            raise ValueError("Choose how to match each of them.")
        choice = {
            "datasets": {
                row["key"]: _TIME_RES_VALUES[kept["each"][row["key"]]]
                for row in rows
                # only what is changed
                if _TIME_RES_VALUES[kept["each"][row["key"]]] != row["point_time_res"]
            }
        }
    return time_res.clean_choice(choice, rows), kept


def _time_shift_kept(form):
    """The question of whether to shift the model's times, as its card was
    left, to show it so again (see oceanval.time_stamps), or None."""
    form = form if isinstance(form, dict) else {}
    return {"mode": form["mode"]} if form.get("mode") in ("shift", "keep") else None


def _form(form, defaults):
    """What the page sent for a step, limited to its own boxes."""
    form = form if isinstance(form, dict) else {}
    return {
        name: (
            bool(form.get(name, value))
            if isinstance(value, bool)
            else _keys(form.get(name, value))
            if name in KEY_LISTS
            else _pairs(form.get(name, value))
            if isinstance(value, list)
            else str(form.get(name, value) or "").strip()
        )
        for name, value in defaults.items()
    }


# the boxes that are lists of names, such as the matchups unticked
KEY_LISTS = ("unticked", "unused")


def _keys(names):
    """A list of names the page sent, leaving out anything that is not one."""
    if not isinstance(names, list):
        return []
    return [name.strip() for name in names if isinstance(name, str) and name.strip()]


def _pairs(rows):
    """A grid of boxes the page sent, such as the depth bins: a list of
    pairs of what was typed, leaving out anything that is not a pair."""
    if not isinstance(rows, list):
        return []
    return [
        [str(box if box is not None else "").strip() for box in row]
        for row in rows
        if isinstance(row, list) and len(row) == 2
    ]


def existing_matchups(out_dir):
    """The matchups in out_dir already, as the simulation step lists them to
    choose what is done with them: a row for each "variable/source", which
    matchup's overwrite and validate's previous pick them by, with its title,
    variable and source, and kinds, each matchup's kind ("gridded" or
    "point") and layer (a point matchup's, or None)."""
    rows = {}
    # asked as each box is typed in, so nothing is looked for where there is no folder of them
    there = os.path.isdir(os.path.join(out_dir, "oceanval_matchups"))
    for matchup in selection.on_disk(out_dir) if there else []:
        row = rows.setdefault(matchup["key"], {
            "key": matchup["key"],
            "title": matchup["title"],
            "variable": matchup["variable"],
            "source": matchup["source"],
            "kinds": [],
        })
        row["kinds"].append({"kind": matchup["kind"], "layer": matchup["layer"]})
    return list(rows.values())


def _ticked_of(ticked, keys):
    """The matchups ticked, of those whose "variable/source" are keys, as
    matchup's overwrite and validate's previous take them: True for all of
    them, False for none, or the list of those ticked."""
    ticked = [key for key in keys if key in set(ticked)]
    return True if len(ticked) == len(keys) else ticked or False


def check_setup(form, cwd):
    """Turn the simulation step's boxes into create_recipes arguments. Where
    the output is saved is asked last (see check_output).

    Returns (arguments, errors), where errors maps each box that cannot be
    used to the reason.
    """
    form = _form(form, default_setup_form())
    arguments, errors = {}, {}

    if not form["simdir"]:
        errors["simdir"] = "Enter the directory the simulation output is in."
    elif not os.path.isdir(_path(form["simdir"], cwd)):
        errors["simdir"] = "There is no directory at this path."
    else:
        arguments["simdir"] = _path(form["simdir"], cwd)
    try:
        arguments["ndown"] = int(form["ndown"])
        if arguments["ndown"] < 0:
            raise ValueError
    except ValueError:
        arguments.pop("ndown", None)
        errors["ndown"] = "This must be a whole number, 0 or more."
    if form["domain"] in DOMAIN_REGIONS:
        arguments["domain"] = form["domain"]
    else:
        errors["domain"] = (
            "Choose one of the domains." if form["domain"] else ""
        )
    for name in ("start", "end"):
        try:
            arguments[name] = int(form[name])
        except ValueError:
            errors[name] = f"{name.title()} must be a year, e.g. 2011."
    if (
        "start" in arguments
        and "end" in arguments
        and arguments["end"] < arguments["start"]
    ):
        errors["end"] = "End must not be before start."
    # the file filters, as words separated by spaces
    arguments["exclude"] = form["exclude"].split() or None
    arguments["require"] = form["require"].split() or None
    return arguments, errors


def check_cores(text):
    """The number of cores matchup is to use, as chosen in a box: (cores,
    None), or (None, the reason it cannot be used). It has no default: the
    right number depends on the machine and the run."""
    text = str(text if text is not None else "").strip()
    if not text:
        return None, "Choose the number of cores."
    try:
        cores = int(text)
    except ValueError:
        cores = 0
    if cores < 1:
        return None, "Cores must be a whole number, 1 or more."
    count = os.cpu_count()
    if count and cores > count:
        return None, f"This machine has {count} cores. Choose {count} or fewer."
    return cores, None


def check_matchup_boxes(form):
    """matchup's spatial subset (lon_lim and lat_lim) and missing values
    (as_missing), from the boxes asked once the matchups are confirmed, with
    the cores: the output step's, for matchup alone, or the report options'.
    Returns (arguments, errors), as check_setup does: an argument is only
    given when its boxes are filled in."""
    errors, arguments = {}, {}

    def text(name):
        return str(form.get(name) or "").strip()

    limits = recipes_gui.check_limits(text, errors)
    if limits is not None:
        arguments["lon_lim"], arguments["lat_lim"] = limits
    missing = recipes_gui.check_missing(text, errors)
    if missing is not None:
        arguments["as_missing"] = missing
    return arguments, errors


def matchups_asked_about(out_dir, made=None, report=True):
    """The matchups in out_dir already (see existing_matchups) that the output
    step asks about, each with made: whether this run matches it up, so that it
    can be used as it is or replaced. made is the "variable/source" of each
    matchup the run makes, in lower case (see App._show_output), or None
    for every one. One the run does not make can be used in the report or
    ignored, so it is asked about only if a report follows (report)."""
    rows = []
    for row in existing_matchups(out_dir):
        again = made is None or row["key"].lower() in made
        if again or report:
            rows.append(dict(row, made=again))
    return rows


def check_output(form, cwd, report=True, demo_dir=None, made=None):
    """Turn the output step's boxes, the last before a matchup starts, into
    out_dir, the directory the matchups and the report are saved in, out,
    the script, and matchup's overwrite and validate's previous.

    If out_dir has matchups already, the user says of each whether it is used
    or not (unused): one this run makes (see matchups_asked_about) is then
    replaced (matchup's overwrite), and one it does not make is ignored, left
    out of the report (validate's previous). Until the user has chosen
    (existing_chosen) those this run makes are replaced and the others used.
    Only one used is kept as it is, and validated if a report follows
    (report). The user must tick to confirm it, as they may not notice what
    each starts on.
    Without matchups to ask about, overwrite and previous are True. In
    demo_dir, the demo's own, where every model tried keeps its matchups,
    nothing is asked: this run's are made again and no others are in the
    report.

    Returns (arguments, errors), as check_setup does.
    """
    form = _form(form, default_output_form())
    arguments, errors = {}, {}
    # empty is the directory worked in
    out_dir = _path(form["out_dir"] or ".", cwd)
    if os.path.isfile(out_dir):
        errors["out_dir"] = "This is a file, not a directory."
    else:
        arguments["out_dir"] = out_dir
        # each "variable/source" the page has a row for
        rows = matchups_asked_about(out_dir, made, report)
        keys = [row["key"] for row in rows]
        again = [row["key"] for row in rows if row["made"]]
        unused = set(form["unused"] if form["existing_chosen"] else again)
        if not keys:
            arguments["overwrite"] = arguments["previous"] = True
        elif out_dir == demo_dir:
            arguments["overwrite"], arguments["previous"] = True, not report
        else:
            arguments["overwrite"] = _ticked_of(unused, again)
            # with a report, only those used are in it as they are
            arguments["previous"] = (
                _ticked_of([key for key in keys if key not in unused], keys) if report else True
            )
            if not form["existing_confirmed"]:
                errors["existing_confirmed"] = ""
    if not form["out"]:
        errors["out"] = "Enter the file to write the script to."
    elif os.path.isdir(_path(form["out"], cwd)):
        errors["out"] = "This is a directory. Add the name of the script."
    else:
        arguments["out"] = _path(form["out"], cwd)
    return arguments, errors


def check_validate(form, cwd):
    """Turn the report options step's boxes into validate() arguments.

    Returns (arguments, errors), as check_setup does. validate's own checks
    of the matchups and of a regions file are made here, so that what they
    find is shown beside the box rather than after the run has started.
    """
    form = _form(form, default_validate_form())
    errors = {}
    arguments = {
        "data_dir": _path(form["data_dir"] or ".", cwd),
        "out_dir": _path(form["out_dir"] or ".", cwd),
    }
    matchups = None
    try:
        oceanval._check_matchups(arguments["data_dir"])
        matchups = selection.on_disk(arguments["data_dir"])
    except ValueError as error:
        errors["data_dir"] = str(error)
    if os.path.isfile(arguments["out_dir"]):
        errors["out_dir"] = "This is a file, not a directory."
    # the options of matchups that are not there, or not ticked, are not asked about
    form, variables = choose_matchups(form, matchups, errors)
    report, report_errors = check_report(form, cwd)
    arguments.update(report)
    errors.update(report_errors)
    if variables is not None:
        arguments["variables"] = variables
    return arguments, errors


# why the report cannot be built from the matchups ticked, as the page says it too
NONE_TICKED = "Tick at least one dataset to validate."


def choose_matchups(form, matchups, errors):
    """The report options step's boxes, with those for matchups of a kind
    none of which are ticked put back as they start, so that they are not
    used: a transect, regional summaries and fixed colour scales are for
    gridded matchups, and the depth bins for point matchups through the
    water column. Returned with validate()'s variables, the "variable/source"
    of each matchup ticked, or None if they all are. matchups are as
    selection.on_disk gives them, or None if they are not known, which
    leaves the boxes as they are. If none is ticked, errors["variables"]
    says so."""
    if matchups is None:
        return form, None
    unticked = set(form.get("unticked") or ())
    ticked = [matchup for matchup in matchups if matchup["key"] not in unticked]
    if matchups and not ticked:
        errors["variables"] = NONE_TICKED
    form = dict(form)
    defaults = default_validate_form()
    if not any(matchup["kind"] == "gridded" for matchup in ticked):
        for name in GRIDDED_ONLY:
            form[name] = defaults[name]
    if not any(matchup["kind"] == "point" and matchup["vertical"] for matchup in ticked):
        form["depth_bins"] = defaults["depth_bins"]
    if len(ticked) == len(matchups):
        return form, None
    return form, list(dict.fromkeys(matchup["key"] for matchup in ticked))


# the report options that are only for gridded matchups
GRIDDED_ONLY = ("subregions", "subregions_file", "fixed_scale", "transect")


def check_report(form, cwd):
    """Turn the report options step's boxes, other than its directories,
    into validate() arguments: what the last step of a matchup and validate
    run asks, before the matchups are made.

    Returns (arguments, errors), as check_validate does.
    """
    form = _form(form, default_validate_form())
    errors = {}
    arguments = {}

    def text(name):
        # a regions file is relative to the directory worked in, too
        if name == "subregions_file" and form[name]:
            return _path(form[name], cwd)
        return form[name]

    limits = recipes_gui.check_limits(text, errors)
    if limits is not None:
        arguments["lon_lim"], arguments["lat_lim"] = limits
    subregions = recipes_gui.check_subregions(text, errors)
    if subregions not in (None, "nwes", "global"):
        try:
            oceanval._check_region_file(subregions)
        except ValueError as error:
            errors["subregions_file"] = str(error)
    if subregions is not None:
        arguments["subregions"] = subregions
    transect = check_transect_boxes(form, errors)
    if transect is not None:
        arguments["transect"] = transect
    for name in ("fixed_scale", "pdf", "word", "zip"):
        if form[name]:
            arguments[name] = True
    # validate's own default is the concise report
    if not form["concise"]:
        arguments["concise"] = False
    bins = check_depth_boxes(form["depth_bins"], errors)
    # validate's own default is OceanVal's bins
    if bins is not None and bins != depths.DEFAULT_DEPTH_BINS:
        arguments["depth_bins"] = [list(pair) for pair in bins]
    return arguments, errors


# why the depth bins cannot be used, as the page says it too
DEPTH_FROM = "Each bin needs a From depth."
DEPTH_NUMBER = "Depths must be numbers."
DEPTH_NEGATIVE = "Depths must be 0 or more."
DEPTH_ORDER = "A bin's To depth must be deeper than its From depth."
DEPTH_NONE = "Add at least one bin."
DEPTH_OPEN = "Only the deepest bin can leave To empty, for everything below it."


def check_depth_boxes(rows, errors):
    """validate()'s depth_bins, from the depth bins' boxes, sorted from the
    shallowest. Rows left empty are ignored. None if the boxes cannot be
    used, in which case errors["depth_bins"] says why, as the page does
    while they are typed in."""
    bins = []
    for low, high in rows:
        if not low and not high:
            continue
        if not low:
            errors["depth_bins"] = DEPTH_FROM
            return None
        pair = (recipes_gui._number(low), recipes_gui._number(high) if high else None)
        if pair[0] is None or (high and pair[1] is None):
            errors["depth_bins"] = DEPTH_NUMBER
            return None
        if pair[0] < 0 or (pair[1] is not None and pair[1] < 0):
            errors["depth_bins"] = DEPTH_NEGATIVE
            return None
        if pair[1] is not None and pair[1] <= pair[0]:
            errors["depth_bins"] = DEPTH_ORDER
            return None
        bins.append(pair)
    if not bins:
        errors["depth_bins"] = DEPTH_NONE
        return None
    bins.sort(key=lambda pair: pair[0])
    for (low, high), (next_low, next_high) in zip(bins, bins[1:]):
        if high is None:
            errors["depth_bins"] = DEPTH_OPEN
            return None
        if next_low < high:
            errors["depth_bins"] = (
                f"The bins {depths.depth_label(low, high)} and "
                f"{depths.depth_label(next_low, next_high)} overlap."
            )
            return None
    return depths.check_depth_bins(bins)


def check_transect_boxes(form, errors):
    """validate()'s transect, from the report options step's boxes, if the
    transect is ticked. None if it is not, or if the boxes cannot be used, in
    which case errors says why, box by box, as the page does while they are
    typed in."""
    if not form["transect"]:
        return None
    given = [name for name in TRANSECT_BOXES if form[name]]
    values = {}
    for name in given:
        values[name] = recipes_gui._number(form[name])
        if values[name] is None:
            errors[name] = "Coordinates must be numbers."
    for name in TRANSECT_BOXES:
        if name not in given:
            errors[name] = TRANSECT_EMPTY
    if any(name in errors for name in TRANSECT_BOXES):
        return None
    for name in TRANSECT_BOXES:
        label, low, high = (
            ("Longitude", -180, 360) if name.endswith("_lon") else ("Latitude", -90, 90)
        )
        if not low <= values[name] <= high:
            errors[name] = f"{label} must be between {low} and {high}."
    if any(name in errors for name in TRANSECT_BOXES):
        return None
    lon0, lat0, lon1, lat1 = (values[name] for name in TRANSECT_BOXES)
    if lon0 == lon1 and lat0 == lat1:
        errors["transect_end_lon"] = errors["transect_end_lat"] = (
            "The two ends are the same point."
        )
        return None
    if lon0 != lon1 and lat0 != lat1:
        for name in TRANSECT_BOXES:
            errors[name] = TRANSECT_RULE
        return None
    try:
        return transects.check_transect({"start": [lon0, lat0], "end": [lon1, lat1]})
    except ValueError as error:
        # an nctoolkit too old to extract it
        errors["transect"] = str(error)
        return None


def default_compare_form():
    """The compare step's boxes, as they start: a name and a validation
    directory for each simulation, and where to build the comparison."""
    form = {"out_dir": ""}
    for row in range(1, COMPARE_ROWS + 1):
        form[f"name_{row}"] = ""
        form[f"dir_{row}"] = ""
    return form


def check_compare(form, cwd):
    """Turn the compare step's boxes into compare() arguments: model_dict,
    in the order of the rows, and out_dir.

    Returns (arguments, errors), as check_validate does. errors["rows"] is
    a problem with the rows as a whole.
    """
    form = _form(form, default_compare_form())
    errors = {}
    model_dict = {}
    names, directories = {}, {}
    for row in range(1, COMPARE_ROWS + 1):
        name, directory = form[f"name_{row}"], form[f"dir_{row}"]
        if not name and not directory:
            continue
        if not directory:
            errors[f"dir_{row}"] = "Enter the directory of this simulation's validation."
        if not name:
            errors[f"name_{row}"] = "Give the simulation a name."
        elif name in names:
            errors[f"name_{row}"] = "Each simulation needs a name of its own."
        else:
            names[name] = row
        if not directory:
            continue
        path = _path(directory, cwd)
        if not os.path.isdir(path):
            errors[f"dir_{row}"] = "There is no directory at this path."
        elif not os.path.isdir(os.path.join(path, "oceanval_results", "annual_mean")):
            errors[f"dir_{row}"] = (
                "There are no validation results here (no oceanval_results/annual_mean): "
                "choose the directory validate() built its report in."
            )
        elif os.path.realpath(path) in directories:
            errors[f"dir_{row}"] = "This validation is in another row too."
        else:
            directories[os.path.realpath(path)] = row
        if f"name_{row}" not in errors and f"dir_{row}" not in errors:
            model_dict[name] = path
    complete = sum(
        1 for row in range(1, COMPARE_ROWS + 1) if form[f"name_{row}"] and form[f"dir_{row}"]
    )
    if complete < 2:
        errors["rows"] = "Fill in at least two simulations to compare."
    out_dir = _path(form["out_dir"] or ".", cwd)
    if os.path.isfile(out_dir):
        errors["out_dir"] = "This is a file, not a directory."
    return {"model_dict": model_dict, "out_dir": out_dir}, errors


def _compare_call(arguments, cwd):
    """How a compare run is shown: the call it makes."""
    model_dict = {name: _shown(path, cwd) for name, path in arguments["model_dict"].items()}
    return (
        f"oceanval.compare(model_dict={_literal(model_dict)}, "
        f"out_dir={_literal(_shown(arguments['out_dir'], cwd))})"
    )


def _validate_call(arguments, cwd):
    """How a validate run is shown: the call it makes."""
    shown = {
        name: _shown(value, cwd) if name in ("data_dir", "out_dir") else value
        for name, value in arguments.items()
    }
    listed = ", ".join(f"{name}={_literal(value)}" for name, value in shown.items())
    return f"oceanval.validate({listed})"


# ---- what the steps keep, for Back ----

# the recipes window's Global settings that the app shows, which it starts
# with again as they were left; the rest are chosen in other steps
RECIPES_KEPT = (
    "point_start",
    "point_end",
    "clim_start",
    "clim_end",
    "woa23_start",
    "woa23_end",
    "fvcom_res",
    "fvcom_min_depth",
    "fvcom_max_depth",
    "fvcom_dz",
    "thickness",
    "point_time_res",
    "cores",
)

# what the units step gives back for Back, rather than the conversions
_BACK = object()


def _simulation(where):
    """The files a simulation is read from, as create_recipes takes them in
    where: its directory, how far down they are, and the file filters."""
    return (
        os.path.abspath(where["simdir"]),
        where["ndown"],
        tuple(where.get("exclude") or ()),
        tuple(where.get("require") or ()),
    )


def _output_key(key):
    """A simulation's files (see _simulation) as the page compares them."""
    return json.dumps(list(key))


def _dataset_options(sent):
    """One ticked dataset's options, as the recipes window sent them."""
    sent = sent if isinstance(sent, dict) else {}
    options = {
        name: "" if sent[name] is None else str(sent[name])
        for name in ("start", "end", "point_time_res")
        if name in sent
    }
    if "vertical" in sent:
        options["vertical"] = bool(sent["vertical"])
    # the boxes the user typed in, which the window does not fill in again
    typed = sent.get("typed")
    options["typed"] = [name for name in typed if name in options] if isinstance(typed, list) else []
    return options


def recipes_choices(sent, rows, domain):
    """What the recipes window held when it was left, with its script
    written or with Back: sent is what its page sent then, and rows and
    domain what it was shown with. Keeps the Global settings in
    RECIPES_KEPT, the page of it shown and, for each row, its model
    variable, the datasets ticked and their options, and whether the user
    edited any of that, rather than leaving it as OceanVal found it (the
    years and how point observations are matched it fills in are not the
    user's, unless typed over)."""
    sent = sent if isinstance(sent, dict) else {}
    settings = sent.get("settings")
    settings = settings if isinstance(settings, dict) else {}
    found = {row["variable"]: row for row in rows}
    choices = {
        "domain": domain,
        "settings": {
            name: "" if settings[name] is None else str(settings[name])
            for name in RECIPES_KEPT
            if name in settings
        },
        "page": "point" if sent.get("page") == "point" else "gridded",
        "rows": {},
    }
    sent_rows = sent.get("rows")
    for item in sent_rows if isinstance(sent_rows, list) else []:
        if not isinstance(item, dict) or item.get("variable") not in found:
            continue
        row = found[item["variable"]]
        value = str(item.get("model_variable") or "").strip()
        selected = item.get("selected")
        selected = [
            recipe for recipe in (selected if isinstance(selected, list) else [])
            if isinstance(recipe, str)
        ]
        options = {}
        for kind in ("gridded", "point"):
            given = item.get(kind)
            options[kind] = {
                str(recipe): _dataset_options(opts)
                for recipe, opts in (given.items() if isinstance(given, dict) else [])
            }
        ticked = {
            dataset["recipe"]
            for dataset in row["gridded"] + row["point"]
            if dataset["ticked"]
        }
        edited = (
            value != row["model_variable"]
            or set(selected) != ticked
            # a dataset's own years or how it is matched, typed in, or Vertical
            or any(
                opts.get("typed") or opts.get("vertical")
                for kind in options.values() for opts in kind.values()
            )
        )
        choices["rows"][item["variable"]] = dict(
            model_variable=value, selected=selected, edited=edited, **options
        )
    return choices


def recipes_restore(choices, rows, available, domain, same=False):
    """What the recipes window, shown with rows, the model variables
    available and domain, starts with, from the choices it was last left
    with (see recipes_choices), or None if there are none.

    same is whether it is the window they were made in, shown again, which
    starts as it was left. A window made afresh, for the simulation read
    again, starts with the Global settings and with each row the user
    edited, if its model variable is in the output, and only with their
    ticks and options if the domain is the same, as the rows are ticked
    afresh for another. Returned as {"settings", "rows", "dropped",
    "retick"}: "dropped" holds the model variables chosen before that are
    not in the output, and "retick" whether edited rows were ticked afresh,
    for the window to say so, and "page", the page of it shown: as it was
    left, in the window shown again, else the first."""
    if not choices:
        return None
    variables = {row["variable"] for row in rows}
    ticks = same or choices["domain"] == domain
    kept, dropped = {}, {}
    for variable, row in choices["rows"].items():
        if variable not in variables or not row["edited"]:
            continue
        value = row["model_variable"]
        if not same and value and _unknown_variables(value, available):
            dropped[variable] = value
            continue
        kept[variable] = {
            "model_variable": value,
            "selected": row["selected"] if ticks else None,
            "gridded": row["gridded"] if ticks else {},
            "point": row["point"] if ticks else {},
        }
    return {
        "settings": dict(choices["settings"]),
        "rows": kept,
        "dropped": dropped,
        "retick": not ticks and any(row["edited"] for row in choices["rows"].values()),
        "page": choices.get("page", "gridded") if same else "gridded",
    }


def _for_future(text):
    """Why one of the user's own datasets cannot be saved as a recipe, for
    its form, which can still add it for use now only."""
    if text.startswith("Use "):
        return "To save this for future use, u" + text[1:]
    return f"To save this for future use: {text}"


def _future_problems(kind, arguments, errors):
    """What stops one of the user's own datasets (the arguments
    own_data.check_entry made of its form, with the errors it found) being
    saved as a recipe, when nothing stops it being used now: a recipe's names
    are stricter, and it needs the data itself. As {box: reason}."""
    found = {}
    names = user_recipes.name_problems(
        arguments.get("name", ""), arguments.get("source", ""), strict=False
    )
    for box, name in (("name", "variable"), ("source", "source")):
        if name in names and box not in errors:
            found[box] = _for_future(names[name])
    if kind == "gridded" and arguments.get("obs_path") == "auto" and "obs_path" not in errors:
        found["obs_path"] = "To save this for future use, give the observations' path, not auto."
    return found


def _future_identity(kind, arguments):
    """Which dataset the own_future step is for, so that what was typed in
    it is only shown again for the same one."""
    return [
        kind,
        arguments.get("name"),
        arguments.get("source"),
        str(arguments.get("obs_path")),
        arguments.get("obs_variable"),
    ]


def _future_errors(errors, boxes):
    """What recipe_forms.check_save found wrong, for the own_future step:
    each error of one of its boxes at that box, a recipe of the same name in
    the chosen file at where it is saved, and the rest, which are about what
    was given before the step, for the step as a whole."""
    shown, general = {}, []
    for name, text in errors.items():
        if name in boxes:
            shown[name] = text
        elif name == "source":
            shown.setdefault("where", text)
        else:
            general.append(text)
    if general:
        shown[""] = " ".join(dict.fromkeys(general))
    return shown


def _units_row(row):
    """What a row of the units step converts, which a conversion typed for
    it is kept for: everything but its key, and what OceanVal makes of it."""
    return {name: value for name, value in row.items() if name not in ("key", "check")}


def _inside(folder, path):
    """Whether path is folder or below it, both real paths."""
    try:
        return os.path.commonpath([folder, path]) == folder
    except ValueError:
        return False


def _served_file(root, path, allowed=None):
    """The file at path, a URL's path below root, or None. Nothing outside
    root is served, however the path is written, nor, if allowed is given,
    outside the folders it lists."""
    root = os.path.realpath(root)
    found = os.path.realpath(os.path.join(root, urllib.parse.unquote(path)))
    if not _inside(root, found) or not os.path.isfile(found):
        return None
    if allowed is not None and not any(
        _inside(os.path.realpath(folder), found) for folder in allowed
    ):
        return None
    return found


def _partial_marker(text):
    """How much of the end of text could be the start of a question, or of
    news of how the run is going."""
    longest = max(len(QUESTION_MARKER), len(PROGRESS_MARKER))
    for size in range(min(len(text), longest - 1), 0, -1):
        if QUESTION_MARKER.startswith(text[-size:]) or PROGRESS_MARKER.startswith(text[-size:]):
            return size
    return 0


class Console:
    """What has been printed, which the page reads in pieces as it comes.

    Everything is also written to the terminal oceanval was started from.
    Only the last LIMIT characters are kept. clear() starts afresh, for the
    next run, which the page finds out from epoch.
    """

    LIMIT = 5_000_000

    def __init__(self, changed):
        self._changed = changed
        self._lock = threading.Lock()
        self._chunks = []
        self._size = 0
        self._last = 0
        self.epoch = 1
        self.dropped = False

    def write(self, text):
        if not text:
            return
        with self._lock:
            self._last += 1
            self._chunks.append((self._last, text))
            self._size += len(text)
            while self._size > self.LIMIT and len(self._chunks) > 1:
                _, old = self._chunks.pop(0)
                self._size -= len(old)
                self.dropped = True
        self.note(text)
        self._changed()

    def empty(self):
        with self._lock:
            return not self._chunks

    def note(self, text):
        """Write text to the terminal only."""
        try:
            sys.__stdout__.write(text)
            sys.__stdout__.flush()
        except (AttributeError, OSError, ValueError):
            # no terminal to write to
            pass

    def clear(self):
        with self._lock:
            self._chunks = []
            self._size = 0
            self.epoch += 1
            self.dropped = False
        self._changed()

    def since(self, after, epoch):
        """The text printed since chunk number after of epoch, for the page.

        "reset" says the page should start its copy afresh: it is from
        another epoch, or has missed chunks dropped to keep within LIMIT.
        """
        with self._lock:
            reset = epoch != self.epoch or (
                bool(self._chunks) and self._chunks[0][0] > after + 1
            )
            if reset:
                after = 0
            return {
                "epoch": self.epoch,
                "text": "".join(
                    text for number, text in self._chunks if number > after
                ),
                "last": self._last,
                "reset": reset,
                "dropped": self.dropped,
            }


class _ConsoleStream(io.TextIOBase):
    """Stands in for sys.stdout and sys.stderr while create_recipes runs, so
    that what it prints reaches the page too."""

    def __init__(self, console):
        self._console = console

    def writable(self):
        return True

    def write(self, text):
        self._console.write(text)
        return len(text)


class Question:
    """A question put in the page, what to show with it, and what to do with
    its answer."""

    _numbers = itertools.count(1)

    def __init__(self, text, choices, respond, details=None):
        self.id = next(Question._numbers)
        self.text = text
        self.choices = list(choices) if choices else None
        self.respond = respond
        self.details = details if isinstance(details, dict) else None

    @property
    def matchups(self):
        """Whether it is matchup's, of whether the matchups are right."""
        return bool(self.details) and self.details.get("kind") == "matchups"

    @property
    def time_res_rows(self):
        """The point datasets matchup asks to check the point_time_res of,
        once the matchups are right (see oceanval.time_res)."""
        if not self.matchups:
            return []
        rows = (self.details.get("point_time_res") or {}).get("rows")
        return rows if isinstance(rows, list) else []

    @property
    def time_shift(self):
        """Whether it is matchup's, of whether to shift the model's times, as
        they look like the end of the period each mean covers, asked before
        the matchups are (see oceanval.time_stamps)."""
        return bool(self.details) and self.details.get("kind") == "time_shift"

    def files(self, pattern):
        """The files the details list for one file pattern, or None."""
        files = (self.details or {}).get("files")
        found = files.get(pattern) if isinstance(files, dict) else None
        return found if isinstance(found, list) else None

    def as_dict(self):
        # the files can be many, so the page asks for them a pattern at a time
        details = self.details and {
            key: value for key, value in self.details.items() if key != "files"
        }
        return {
            "id": self.id,
            "text": self.text,
            "choices": self.choices,
            "details": details,
        }


class Run:
    """matchup or validate, running in a process of its own.

    args are oceanval.app_child's. Its output goes to console, each question
    it asks to ask(question, choices, respond, details), what it tells of how
    it is going (see oceanval.prompts.tell) to told(news), and finished(run)
    is called once it has stopped.
    """

    def __init__(self, args, cwd, console, ask, finished, told=None):
        self.args = list(args)
        self.cwd = cwd
        self.console = console
        self._ask = ask
        self._told = told
        self._finished = finished
        self.process = None
        self.returncode = None
        self.stopped = False
        self._reader = None

    def start(self):
        # the same oceanval as this process, wherever that was imported from,
        root = os.path.dirname(os.path.dirname(os.path.abspath(oceanval.__file__)))
        code = (
            f"import sys; sys.path.insert(0, {root!r}); "
            "from oceanval.app_child import main; main()"
        )
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-c", code, *self.args],
            cwd=self.cwd,
            env=dict(os.environ, PYTHONUNBUFFERED="1"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # in the order it was printed, as a terminal shows it
            stderr=subprocess.STDOUT,
            # a process group of its own, so that Stop reaches everything it
            # starts, and Ctrl+C in the terminal reaches oceanval first
            start_new_session=True,
        )
        self._reader = threading.Thread(
            target=self._read, name="oceanval-run-output", daemon=True
        )
        self._reader.start()
        threading.Thread(target=self._wait, name="oceanval-run", daemon=True).start()

    def _read(self):
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        stream = self.process.stdout.fileno()
        while True:
            try:
                data = os.read(stream, 65536)
            except OSError:
                break
            if not data:
                break
            pending = self._take(pending + decoder.decode(data))
        self.console.write(pending + decoder.decode(b"", final=True))

    def _take(self, text):
        """Pass text on to the console, questions to ask, and news of how
        the run is going to told. Returns what is left over: an unfinished
        question or piece of news, or what might start one."""
        while True:
            found = [
                (start, marker)
                for marker in (QUESTION_MARKER, PROGRESS_MARKER)
                for start in [text.find(marker)]
                if start >= 0
            ]
            if not found:
                keep = _partial_marker(text)
                self.console.write(text[: len(text) - keep])
                return text[len(text) - keep :]
            start, marker = min(found)
            self.console.write(text[:start])
            end = text.find("\n", start)
            if end < 0:
                return text[start:]
            try:
                message = json.loads(text[start + len(marker) : end])
                if marker == PROGRESS_MARKER:
                    if not isinstance(message, dict):
                        raise TypeError("news is a dict")
                    if self._told is not None:
                        self._told(message)
                else:
                    self._ask(
                        message["question"],
                        message.get("choices"),
                        self.answer,
                        message.get("details"),
                    )
            except (ValueError, KeyError, TypeError):
                # printed by something else, then
                self.console.write(text[start : end + 1])
            text = text[end + 1 :]

    def answer(self, text):
        try:
            self.process.stdin.write((text + "\n").encode("utf-8"))
            self.process.stdin.flush()
        except (OSError, ValueError):
            # it has already stopped
            pass

    def _wait(self):
        self.returncode = self.process.wait()
        # something it started can hold its output open after it has gone,
        # so that is not waited for forever
        self._reader.join(timeout=5)
        with contextlib.suppress(OSError, ValueError):
            self.process.stdin.close()
        self._finished(self)

    def running(self):
        return self.process is not None and self.process.poll() is None

    def stop(self):
        """Stop the run, and everything it has started."""
        if not self.running():
            return
        self.stopped = True
        self._signal(signal.SIGTERM)
        threading.Thread(target=self._kill_later, daemon=True).start()

    def _kill_later(self):
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._signal(signal.SIGKILL)

    def _signal(self, number):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.process.pid, number)

    def join(self, timeout=None):
        if self.process is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=timeout)


class App(AnalyzeSteps, SimCompareSteps, ViewerSteps):
    """What the oceanval window shows, and the server showing it.

    view is the step the page is on: "home", the landing page, whose View
    simulation output tile opens the viewer (see oceanval.viewer_app), whose
    Analyze tile starts an analysis (see oceanval.analysis_app) and whose Validate
    tile goes to "start", the validation page. From there: "setup", "own_data" (whether
    there are observations of your own), "point_data" and "gridded_data" (adding
    them, with "own_future" between for one to be saved as a recipe too, see
    own_future), "preparing" (while create_recipes reads the simulation) and
    "recipes" (its window) to match up, then "units_table" (the model's and
    the observations' units, with the conversions OceanVal suggests, to check,
    change and always confirm), "output" (where the matchups are saved, what
    is done with those there already, and the script, asked last as the
    choices depend on the matchups chosen), or "validate" for the report options, and then
    "running" and "finished". To match up and validate, once the matchups are
    checked in "running", "report_options" asks for the report's options
    before anything is matched up, while matchup waits for the answer to
    whether the matchups are right; "point_time_res" comes before it, when
    point datasets are matched by day against coarser output. The demo is a matchup and validate run
    that starts at "demo", which downloads its model output, and goes on to
    "setup" with the boxes filled in. Registering recipes of your own is
    "register_kind" (point or gridded data), and then "register_point" or
    "register_gridded", where each is described, checked and saved to a
    .oceanvalrc file (see oceanval.user_recipes). Back goes a step the other way, until
    anything is matched up, keeping what was entered (see back). Everything
    the page does goes through the methods here, which the server's threads
    call.
    """

    def __init__(self, cwd=None):
        self.cwd = os.path.abspath(cwd or os.getcwd())
        self.token = secrets.token_urlsafe(24)
        self.url = None
        self.port = None
        self.closed = threading.Event()
        # why the window closed, when it was not Quit or Ctrl+C
        self.closed_reason = None
        # the pages open in a browser, by id, and when each was last heard
        # from (see touch); with how long one is silent before it counts as
        # closed, which is longer than the state request it waits on
        self.page_timeout = PAGE_TIMEOUT
        self.hidden_page_timeout = HIDDEN_PAGE_TIMEOUT
        self.closing_page_timeout = CLOSING_PAGE_TIMEOUT
        self._pages = {}
        self._page_seen = False
        # the pages in a tab not looked at, and whether the terminal has been
        # told that the pages have all gone quiet while a run goes on
        self._hidden = set()
        self._closing = set()
        self._quiet_told = False
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self.version = 0
        self.console = Console(self._notify)
        self.view = "home"
        self.action = None
        self.setup_form = default_setup_form()
        self.setup_error = None
        self.setup_arguments = None
        # where the matchups and the report are saved
        self.out_dir = self.cwd
        # matchup's overwrite and validate's previous, as the output step chose them
        self.overwrite = True
        self.previous = True
        # the output step, once the matchups are confirmed: its boxes, the
        # "variable/source" (lower case) of the matchups the run makes, and
        # what matchup is given along with yes (see _yes): out_dir, overwrite
        # and cores, or None until it is carried on from
        self.output_form = dict(default_output_form(self.cwd), out_dir=self.cwd)
        self.output_made = None
        self._run_output = None
        # where the recipes window writes its script, which the output step
        # writes to the path it names (see _draft_script)
        self._draft_dir = None
        # the user's own observations, as the arguments of the calls to
        # register them, and the recipes saved from them for future use too
        # (see own_future_save)
        self.own_data = {"point": [], "gridded": []}
        self.own_saved = []
        # the dataset being saved as a recipe too, in the own_future step:
        # its arguments, the page's boxes for it, what is in its data, and
        # the boxes typed into, kept for Back (see own_future)
        self.own_pending = None
        # registering recipes of the user's own: which kind was chosen last,
        # what each kind's boxes held when left, what has been saved since
        # the step was chosen, and the check of data on a server that is
        # running or was last made (see register_data), which the
        # own_future step makes too
        self.register_answer = None
        self.register_forms = {
            kind: recipe_forms.default_form(kind) for kind in recipe_forms.KINDS
        }
        self.register_saved = []
        self.register_job = None
        self._register_check = None
        self._register_jobs = 0
        self.validate_form = default_validate_form()
        # the data directory choose() filled in, so that one the user entered is told from it
        self._validate_data_dir_default = None
        self.compare_form = default_compare_form()
        # the compare() arguments of the comparison being made, whose report,
        # and the validation reports it links to, the window serves
        self.compare_arguments = None
        # the validate() arguments chosen in the report options step of a
        # matchup and validate run, and what writes the script again with them
        self.report_arguments = None
        self._script_writer = None
        # what the point_time_res step chose, and its boxes as last left,
        # once the matchups are checked (see point_time_res)
        self.point_time_res_choice = None
        self.point_time_res_form = None
        # how the question of whether to shift the model's times was left,
        # which is shown so again after Back (see _time_shift_kept)
        self.time_shift_form = None
        # the check of the dates of the model output, made while the
        # matchups are asked about (see _new_model_check)
        self.model_check = None
        self.question = None
        # the recipes window, while it is being shown
        self.recipes_page = None
        # the matchups' units, while the units step is being shown
        self.units_rows = None
        self._units_done = threading.Event()
        self._units_conversions = None
        self._units_back = False
        # what was entered in the steps from the own data step on, which
        # they are shown with again, for this run only (see _forget_steps):
        # the last answer to whether there is data of your own, what the
        # recipes window held when it was left (see recipes_choices) and the
        # window it was in, and what the units step's boxes held, by row
        self.own_answer = None
        self.recipes_choices = None
        self._choices_from = None
        self.units_boxes = {}
        # the simulation's variables, read while the own data steps are answered
        # (see _read_own_simulation), for their variable pickers
        self._own_sim = None
        self._own_sim_number = 0
        # the recipes window for the simulation, kept to be shown again for
        # Back from the steps after it; the units last read, as (what the
        # window chose, rows); the user's own data as given, before the
        # units step wrote conversions into it; and the files of the
        # simulation last read, with whether they are FVCOM output
        self._recipes = None
        self._units_read = None
        self._own_given = None
        self._fvcom = None
        # the years the output of the simulation in the simulation step covers,
        # read from its files' times as it is typed in (see _read_output_years)
        self._output_years = None
        # `cdo sinfo` on every file of the simulation(s) chosen, in the background
        # (see _start_cdo_check): the simulations it is for, the check, and what the
        # page is told. A file it crashes on stops everything (see _cdo_fault)
        self._cdo_key = None
        self._cdo_check = None
        self._cdo_number = 0
        self._cdo_failed = None
        self.run = None
        self.run_label = None
        self.status = None
        self.returncode = None
        self.results_dir = None
        # whether a matchup is still finding the files to ask about
        self.identifying = False
        self.matchups_rejected = False
        self.matchup_script_saved = False
        self.script_path = None
        # the interim report a matchup and validate run builds as it goes:
        # its status.json, as last read (see _follow_interim), or None
        self.interim = None
        # the matchups a matchup run is making, as it tells of them (see
        # _matchup_news), for the page's grid of them, or None
        self.matchup_progress = None
        # the data servers a matchup run could not read its observations
        # from, as it tells of them (see _server_trouble_news), by host, for
        # the notice at the bottom of the page
        self.server_trouble = {}
        # the variables an Analyze or Compare simulations run is working out,
        # and their analyses, as it tells of them (see _analysis_news), for
        # the page's grid of them, or None
        self.analysis_progress = None
        # the temporary files earlier sessions left behind, which the page
        # offers to remove once, and whether the user has answered
        self.leftovers = leftovers.find_leftovers()
        self.leftovers_asked = False
        # the demo, while it is the run being set up or made: its download's
        # status ("idle", "downloading", "done" or "failed"), how much of it
        # has come, and why it failed, or None
        self.demo = None
        self._demo_cancel = threading.Event()
        # the window's settings, from the home .oceanvalrc as it opens
        self.settings = user_settings.load()
        self._server = None
        self._init_analyze()
        self._init_simcompare()
        self._init_viewer()
        # whose steps the state Analyze's options step keeps is
        self._analyze_owner = ANALYZE
        # how many times the window has been restarted (see reset), which the
        # threads of the steps before it check to know theirs are over
        self._reset_count = 0

    # ---- state ----

    def _notify(self):
        with self._changed:
            self.version += 1
            self._changed.notify_all()

    def touch(self, page):
        """Note that the page with this id is open in a browser."""
        if not page or self.closed.is_set():
            return
        with self._lock:
            self._pages[str(page)[:64]] = time.monotonic()
            self._page_seen = True
            self._quiet_told = False

    def page_visibility(self, page, hidden, closing=False):
        """Note that the page with this id is in a tab not looked at (hidden),
        or is looked at again. A browser may slow or freeze such a page, so it
        is given longer to be heard from (see _watch). A page that is closing
        (it is being unloaded) is given only a moment: a browser says a tab it
        closes is hidden first, which must not count as a tab left in the
        background."""
        if not page:
            return
        page = str(page)[:64]
        with self._lock:
            if closing:
                self._hidden.discard(page)
                self._closing.add(page)
                return
            if page in self._closing:
                # said to be hidden as it closed, and heard after saying so
                return
            if hidden:
                self._hidden.add(page)
            else:
                self._hidden.discard(page)
        self.touch(page)

    def _busy(self):
        """Whether a run is starting or going: one is never stopped because
        the pages went quiet. A matchup run handing over to its report run
        keeps the status "running" between the two (see _run_finished)."""
        return self.status in ("starting", "running")

    def heartbeat(self, page, hold=HEARTBEAT_HOLD, connected=None):
        """What the pages with no state request of their own (the recipes
        window) ask: held for hold seconds, or until the window closes, so
        that the browser keeps no timer going. Returns whether it closed.
        connected says whether the page is still there to be answered."""
        deadline = time.monotonic() + hold
        while (
            not self.closed.is_set()
            and self._cdo_failed is None
            and (connected is None or connected())
        ):
            self.touch(page)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self.closed.wait(min(1, remaining))
        return self.closed.is_set()

    def _watch(self):
        """Quit once the pages that were open in the browser are all gone,
        which is how closing the browser reaches the terminal: no browser
        reliably says that it is closing, so the pages are heard from
        instead. Nothing happens before the first page has been. A page in a
        tab not looked at may be frozen by the browser rather than closed,
        so a run going on is left to finish, and the terminal says how to
        see it again; the window quits once it is over."""
        while not self.closed.wait(1):
            tell = False
            with self._lock:
                now = time.monotonic()
                for page, seen in list(self._pages.items()):
                    if page in self._closing:
                        timeout = self.closing_page_timeout
                    elif page in self._hidden:
                        timeout = self.hidden_page_timeout
                    else:
                        timeout = self.page_timeout
                    if now - seen > timeout:
                        del self._pages[page]
                        self._hidden.discard(page)
                        self._closing.discard(page)
                gone = self._page_seen and not self._pages
                busy = gone and self._busy()
                if busy and not self._quiet_told:
                    self._quiet_told = tell = True
            if tell:
                print(
                    "The OceanVal window has gone quiet (its tab is in the background, or "
                    "the browser was closed). The run carries on: open\n"
                    f"  {self.url}\n"
                    "to see it again, or press Ctrl+C here to stop it.",
                    flush=True,
                )
            if gone and not busy:
                self.quit("The browser window was closed.")

    def authorised(self, token):
        return secrets.compare_digest(
            str(token).encode("utf-8"), self.token.encode("utf-8")
        )

    def state(
        self,
        after=0,
        epoch=None,
        version=None,
        timeout=20,
        console=True,
        page=None,
        connected=None,
    ):
        """What the page shows, and what has been printed since it last
        asked. Given the version the page has, waits up to timeout seconds
        for something to change first, while page (its id) is known to be
        open, as long as connected (if given) says it is still there to be
        answered. Without console, what has been printed is left for the
        page to ask for."""
        with self._changed:
            if version is not None:
                deadline = time.monotonic() + timeout
                while (
                    self.version == version
                    and not self.closed.is_set()
                    and (connected is None or connected())
                ):
                    self.touch(page)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self._changed.wait(min(1, remaining))
            report = None
            which = "report"
            if self.action in ("matchup_validate", "validate") and self.results_dir:
                report = os.path.join(self.results_dir, "oceanval_report.html")
            elif self.action == "compare" and self.compare_arguments is not None:
                report = os.path.join(
                    self.compare_arguments["out_dir"], *COMPARISON_HTML, *COMPARISON_LANDING
                )
                which = "comparison"
            elif self.action == ANALYZE and self.results_dir:
                report = os.path.join(self.results_dir, analysis.FOLDER, analysis.REPORT_LINK)
                which = "analysis"
            elif self.action == SIMCOMPARE and self.results_dir:
                report = os.path.join(self.results_dir, sim_compare.FOLDER, sim_compare.REPORT_LINK)
                which = "simcompare"
            report_exists = bool(report and os.path.exists(report))
            return {
                "version": self.version,
                "closed": self.closed.is_set(),
                "view": self.view,
                "action": self.action,
                "cwd": self.cwd,
                "setup": {
                    "form": (
                        self.analyze_setup_form if self.action == ANALYZE
                        else self.viewer_setup_form if self.action == VIEWER
                        else self.setup_form
                    ),
                    "error": self.setup_error,
                    "recent": [
                        d for d in user_cache.recent_sim_dirs()
                        if os.path.isdir(d) or (self.action == VIEWER and os.path.isfile(d))
                    ],
                    "output_years": self._output_years_state(),
                },
                "validate": {
                    "form": self.validate_form,
                    "depth_defaults": default_depth_boxes(),
                },
                "compare": {"form": self.compare_form},
                "report": {
                    "given": self.report_arguments is not None,
                    "dir": self.results_dir or self.out_dir,
                    "gridded": self._report_gridded(),
                    "vertical": self._report_vertical(),
                    "matchups": (
                        self._report_matchups() if self.view == "report_options" else None
                    ),
                },
                "own": {
                    "entries": self.own_data,
                    "saved": self._own_saved_state(),
                    "fields": own_data.FIELDS,
                    "recipe_variables": sorted(recipe_variables(self.cwd)),
                    "simulation": self._own_simulation_state(),
                    "answer": self.own_answer,
                    "future": self._own_future_state(),
                },
                "register": self._register_state(),
                "units": {"rows": self.units_rows, "boxes": self._units_boxes_shown()},
                "output": {"form": self.output_form},
                # whether the simulation looks like NEMO-ERSEM, whose missing
                # values are assumed to be 0 where they are asked, with the cores
                "nemo_ersem": self._recipes is not None
                and recipes_gui.is_likely_nemo_ersem(self._recipes.available),
                # for the lists of how many cores to use
                "cpu_count": os.cpu_count(),
                "point_time_res": self._point_time_res_state(),
                "time_shift": self._time_shift_state(),
                "model_check": self._model_check_state(),
                "analyze": self._analyze_state(),
                "simcompare": self._simcompare_state(),
                "viewer": self._viewer_state(),
                "cdo_check": self._cdo_check_state(),
                "leftovers": {
                    "asked": self.leftovers_asked,
                    "count": len(self.leftovers),
                    "bytes": sum(item["size"] for item in self.leftovers),
                    "files": self.leftovers,
                },
                "interim": self._interim_state(),
                # the version is setup.py's: the installed package's metadata, never typed in here
                "oceanval_version": oceanval.__version__,
                "datasets": BUILTIN_DATASETS,
                "user_settings": {
                    "values": dict(self.settings),
                    "path": user_settings.path(),
                    "data_store": data_store.panel(self.settings),
                },
                "demo": (
                    None
                    if self.demo is None
                    else dict(
                        self.demo,
                        folder=os.path.join(self.cwd, DEMO_FOLDER),
                        models=self._demo_models(),
                        files=[name for name, _url, _path in self._demo_downloads()],
                        year=DEMO_YEAR,
                        downloaded=bool(self.demo["variables"])
                        and all(os.path.isfile(path) for _name, _url, path in self._demo_downloads()),
                        prefilled=self._demo_prefilled(),
                    )
                ),
                "question": self.question.as_dict() if self.question else None,
                "run": {
                    "label": self.run_label,
                    "status": self.status,
                    "returncode": self.returncode,
                    "results": self.results_dir,
                    "matchups": (
                        os.path.join(self.results_dir, "oceanval_matchups")
                        if self.results_dir
                        else None
                    ),
                    "kind": self.run.args[0] if self.run is not None else None,
                    "report": report,
                    "report_exists": report_exists,
                    # validate links oceanval_report.html to the report's first page
                    "report_href": (
                        self._report_href(which, os.path.realpath(report))
                        if report_exists
                        else None
                    ),
                    "identifying": self.identifying,
                    "progress": self._matchup_progress_state(),
                    "server_trouble": self._server_trouble_state(),
                    "analysis": self._analysis_progress_state(),
                    # whether Back can leave it for the units step
                    "back": self._run_back() or self._failed_back(),
                    # the file filters the script was written with, which
                    # the matchups page can change
                    "filters": self._run_filters(),
                    "rejected": self.matchups_rejected,
                    "script_saved": self.matchup_script_saved,
                    "script": self.script_path if self.matchup_script_saved else None,
                    "script_command": (
                        f"python {shlex.quote(self.script_path)}"
                        if self.matchup_script_saved and self.script_path
                        else None
                    ),
                },
                "console": (
                    self.console.since(after, epoch)
                    if console
                    else {
                        "epoch": None,
                        "text": "",
                        "last": 0,
                        "reset": True,
                        "dropped": False,
                    }
                ),
            }

    def html(self):
        return recipes_gui.render_page(
            "oceanval_app.html",
            {
                "token": self.token,
                "domains": [[key, region] for key, region in DOMAIN_REGIONS.items()],
                # what has been printed can be long, so the page asks for it
                "state": self.state(console=False),
            },
        )

    # ---- the reports, which the page links to ----

    def report_root(self, which):
        """The directory of the HTML pages served at /<which>/<token>/:
        which is "interim", for the interim report a matchup and validate
        run builds as it goes, "report", for the full report, "analysis",
        for an analysis's report (see oceanval.analysis), "simcompare", for
        a comparison of simulations (see oceanval.sim_compare), or "comparison",
        for a comparison and the validation reports it links to (see
        report_folders)."""
        if which == "comparison":
            folders = self.report_folders(which)
            return os.path.commonpath(folders) if folders else None
        if self.results_dir is None:
            return None
        if which == "interim":
            return os.path.join(
                self.results_dir, live.FOLDER, "oceanval_report", "_build", "html"
            )
        if which == "report":
            return os.path.join(self.results_dir, "oceanval_report", "_build", "html")
        if which == "analysis":
            return os.path.join(self.results_dir, analysis.FOLDER, analysis.REPORT, "_build", "html")
        if which == "simcompare":
            return os.path.join(self.results_dir, sim_compare.FOLDER, sim_compare.REPORT, "_build", "html")
        return None

    def report_folders(self, which):
        """The only folders served below report_root(which), or None if
        everything below it is. A comparison links to each simulation's
        validation report by a relative path, so the root it is served from
        has them all below it, and only the reports themselves are served."""
        if which != "comparison":
            return None
        if self.compare_arguments is None:
            return None
        return [
            os.path.realpath(os.path.join(self.compare_arguments["out_dir"], *COMPARISON_HTML))
        ] + [
            os.path.realpath(os.path.join(path, "oceanval_report", "_build", "html"))
            for path in self.compare_arguments["model_dict"].values()
        ]

    def _report_href(self, which, page):
        """The page's link to page, one of a report's pages, or None if it
        is not one of them."""
        root = self.report_root(which)
        if root is None or not page:
            return None
        relative = os.path.relpath(os.path.realpath(page), os.path.realpath(root))
        if relative.startswith(".."):
            return None
        return f"{which}/{self.token}/{urllib.parse.quote(relative)}"

    def _interim_state(self):
        """What the page shows of the interim report, if this run builds one."""
        if self.interim is None:
            return None
        status = self.interim["status"] or {}
        which = self.interim.get("which", "interim")
        added = status.get("added")
        return {
            "state": status.get("state", "waiting"),
            "pages": status.get("pages", 0),
            "expected": status.get("expected"),
            # a validation's, or the report of an analysis or a comparison of simulations
            "what": {"analysis": "analysis", "simcompare": "comparison"}.get(which, "validation"),
            "href": self._report_href(which, status.get("landing")),
            # each matchup's, by live.matchup_key, for the grid of the matchups
            "included": status.get("included"),
            "building_key": status.get("building_key"),
            "added": (
                {key: self._report_href(which, path) for key, path in added.items()}
                if isinstance(added, dict)
                else {}
            ),
            "failed": status.get("failed") or [],
        }

    def _matchup_news(self, run, news):
        """What matchup tells of the matchups as it makes them (see
        matchall._Told): the matchups to make, then each as it is started,
        how many of its model files have been read, whether its observations
        are being read or have been, and whether it is made or not, then that
        they are finished. The times the grid shows are taken here, as the
        news arrives."""
        if news.get("kind") in ANALYSIS_NEWS:
            self._analysis_news(run, news)
            return
        if news.get("kind") == "server_trouble":
            self._server_trouble_news(run, news)
            return
        with self._lock:
            if run is not self.run:
                return
            kind = news.get("kind")
            progress = self.matchup_progress
            now = self._clock()
            if kind == "matchups":
                items = news.get("items")
                self.matchup_progress = {
                    "items": [
                        item
                        for item in (items if isinstance(items, list) else [])
                        if isinstance(item, dict) and isinstance(item.get("key"), str)
                    ],
                    "states": {},
                    # how many of each one's model files have been read
                    # (see oceanval.cdo_watch.news)
                    "extraction": {},
                    # whether each one's observations are being read, or have been
                    "observations": {},
                    "finished": False,
                    # when each matchup was started and how long it then took
                    "started": {},
                    "took": {},
                    # the seconds of the runs that are over, and when the run
                    # that is going began (None between runs)
                    "banked": 0.0,
                    "since": now,
                }
            elif progress is None:
                return
            elif kind == "matchup" and news.get("state") in ("matching", "made", "not_made"):
                key = str(news.get("key"))
                progress["states"][key] = news["state"]
                if news["state"] == "matching":
                    # started again, if it is tried again: the last try's time is shown
                    progress["started"][key] = now
                    progress["took"].pop(key, None)
                    progress["extraction"].pop(key, None)
                    progress["observations"].pop(key, None)
                elif key in progress["started"]:
                    # one already made is told as made without being started: no time
                    progress["took"][key] = now - progress["started"].pop(key)
                if news["state"] == "made":
                    # its server answered when it was tried again
                    self._server_answered(key)
            elif kind == "extraction" and isinstance(news.get("total"), int):
                key = str(news.get("key"))
                done = news.get("done")
                was = progress["extraction"].get(key) or {}
                if isinstance(was.get("done"), int):
                    # the watcher's counts and the matchup's own can come in
                    # either order: a count never goes down
                    done = max(done, was["done"]) if isinstance(done, int) else was["done"]
                progress["extraction"][key] = {
                    "total": news["total"],
                    "done": done if isinstance(done, int) else None,
                    "finished": bool(news.get("finished")) or bool(was.get("finished")),
                }
            elif kind == "observations" and news.get("state") in ("reading", "read"):
                # the last said: a point matchup reads them again for each pattern of its files
                progress["observations"][str(news.get("key"))] = news["state"]
            elif kind == "matchups_finished":
                progress["finished"] = True
            else:
                return
            self._notify()

    def _server_trouble_news(self, run, news):
        """A matchup could not read its observations because their server
        is down or overloaded (see oceanval.server_trouble.tell), which the
        page says at its bottom: each server with what it answered, when,
        and the matchups it stopped. The latest answer is the one shown."""
        with self._lock:
            if run is not self.run:
                return
            host = str(news.get("host") or "")
            status = news.get("status")
            trouble = self.server_trouble.setdefault(host, {"matchups": {}})
            trouble.update(
                {
                    "host": host,
                    "server": str(news.get("server") or host),
                    "status": status if isinstance(status, int) else None,
                    "reason": str(news.get("reason") or ""),
                    "at": self._wall_clock().strftime("%H:%M"),
                }
            )
            key = str(news.get("key") or "")
            title = str(news.get("title") or key)
            for item in (self.matchup_progress or {}).get("items", []):
                if item["key"] == key and item.get("title"):
                    # as the grid of the matchups names it
                    title = str(item["title"])
            trouble["matchups"].pop(key, None)
            trouble["matchups"][key] = title
            self._notify()

    def _server_answered(self, key):
        """The matchup key has been made, so its server is no longer said to
        be down for it, nor at all once none of its matchups is left.
        Called with the lock held."""
        for host in list(self.server_trouble):
            self.server_trouble[host]["matchups"].pop(key, None)
            if not self.server_trouble[host]["matchups"]:
                del self.server_trouble[host]

    def _server_trouble_state(self):
        """The servers that are down, for the notice at the bottom of the page."""
        return [
            dict(
                {name: value for name, value in trouble.items() if name != "matchups"},
                matchups=list(trouble["matchups"].values()),
            )
            for trouble in self.server_trouble.values()
        ]

    def _wall_clock(self):
        """The time of day a server is said to have been down at."""
        return datetime.datetime.now()

    def _clock(self):
        """The time the grid of the matchups is timed by, in seconds."""
        return time.monotonic()

    def _matchups_clock_stop(self):
        """Stop the clock of the grid of the matchups, which a run's end
        does, keeping the seconds it has run. Called with the lock held."""
        progress = self.matchup_progress
        if progress is not None and progress["since"] is not None:
            progress["banked"] += self._clock() - progress["since"]
            progress["since"] = None

    def _matchups_clock_resume(self):
        """Start the clock of the grid of the matchups again, for the run
        that builds the report, which keeps the grid. Called with the lock
        held."""
        progress = self.matchup_progress
        if progress is not None and progress["since"] is None:
            progress["since"] = self._clock()

    def _matchup_progress_state(self):
        """The grid of the matchups: each with its state, "waiting",
        "matching", "made" or "not_made", the seconds it has taken, or
        has taken so far while it is matching (None if not started, or made
        without being started), how many of its model files have been read,
        {"total", "done", "finished"} (None before they are read, or if none
        were), and its observations, "reading" or "read" (None before they
        are read, or if they were not). "elapsed" is the seconds the runs have
        gone on, "running" whether one is going now."""
        progress = self.matchup_progress
        if progress is None:
            return None
        now = self._clock()
        rows = []
        for item in progress["items"]:
            state = progress["states"].get(item["key"], "waiting")
            if progress["finished"] and state != "made":
                # left out without a word, or stopped half way
                state = "not_made"
            seconds = progress["took"].get(item["key"])
            if seconds is None and state == "matching" and item["key"] in progress["started"]:
                seconds = now - progress["started"][item["key"]]
            rows.append({
                "key": item["key"],
                "title": str(item.get("title") or item.get("variable") or ""),
                "variable": str(item.get("variable") or ""),
                "source": str(item.get("source") or ""),
                "report": bool(item.get("report")),
                "state": state,
                "seconds": seconds,
                "extraction": dict(progress["extraction"][item["key"]])
                if item["key"] in progress["extraction"]
                else None,
                "observations": progress["observations"].get(item["key"]),
            })
        since = progress["since"]
        return {
            "items": rows,
            "finished": progress["finished"],
            "elapsed": progress["banked"] + (0.0 if since is None else now - since),
            "running": since is not None,
        }

    def _analysis_news(self, run, news):
        """What an Analyze or Compare simulations run tells of its variables
        as it works them out (see oceanval.analysis._tell): the variables and
        the analyses each is to have, then each variable as it is started and
        done, and each of its analyses (once for each simulation). The times
        the grid shows are taken here, as the news arrives."""
        with self._lock:
            if run is not self.run:
                return
            kind = news.get("kind")
            progress = self.analysis_progress
            now = self._clock()
            if kind == "analyses":
                items = news.get("items")
                simulations = news.get("simulations")
                self.analysis_progress = {
                    "simulations": simulations if isinstance(simulations, int) and simulations > 0 else 1,
                    "items": [
                        {
                            "key": item["key"],
                            "title": str(item.get("title") or item["key"]),
                            "kinds": [kind for kind in analysis.KINDS if kind in (item.get("kinds") or [])],
                        }
                        for item in (items if isinstance(items, list) else [])
                        if isinstance(item, dict) and isinstance(item.get("key"), str)
                    ],
                    # each variable's "working" or "done"
                    "states": {},
                    # the (key, analysis) being worked out now, and how many
                    # simulations each has been worked out in
                    "working": set(),
                    "done": {},
                    # when each variable was started and how long it then took
                    "started": {},
                    "took": {},
                    # the seconds of the run, once it is over, when it began
                    # (None once it is over), and when it ended
                    "banked": 0.0,
                    "since": now,
                    "ended": None,
                }
            elif progress is None:
                return
            elif kind == "analysis_variable" and news.get("state") in ("working", "done"):
                key = str(news.get("key"))
                progress["states"][key] = news["state"]
                if news["state"] == "working":
                    progress["started"][key] = now
                elif key in progress["started"]:
                    progress["took"][key] = now - progress["started"].pop(key)
            elif kind == "analysis" and news.get("state") in ("working", "done"):
                cell = (str(news.get("key")), str(news.get("analysis")))
                if news["state"] == "working":
                    progress["working"].add(cell)
                else:
                    progress["working"].discard(cell)
                    progress["done"][cell] = progress["done"].get(cell, 0) + 1
            else:
                return
            self._notify()

    def _analysis_clock_stop(self):
        """Stop the clock of the grid of the variables, which the run's end
        does. Called with the lock held."""
        progress = self.analysis_progress
        if progress is not None and progress["since"] is not None:
            progress["ended"] = self._clock()
            progress["banked"] += progress["ended"] - progress["since"]
            progress["since"] = None

    def _analysis_progress_state(self):
        """The grid of the variables of an Analyze or Compare simulations
        run: "kinds", the analyses any variable has, in the order of
        analysis.KINDS, and each variable with its state, "waiting",
        "working" or "done", the seconds it has taken (so far, while it is
        working; None if not started), and "cells", the state of each of its
        analyses: "waiting", "working", "done" once worked out in every
        simulation, "partial" in some ("done" says how many), or "failed",
        for one being worked out when the run ended. "elapsed" is the
        seconds the run has gone on, "running" whether it is going now."""
        progress = self.analysis_progress
        if progress is None:
            return None
        now = self._clock()
        running = progress["since"] is not None
        simulations = progress["simulations"]
        rows = []
        for item in progress["items"]:
            key = item["key"]
            cells = {}
            for kind in item["kinds"]:
                done = progress["done"].get((key, kind), 0)
                if (key, kind) in progress["working"]:
                    state = "working" if running else "failed"
                elif done >= simulations:
                    state = "done"
                else:
                    state = "partial" if done else "waiting"
                cells[kind] = {"state": state, "done": done}
            state = progress["states"].get(key, "waiting")
            seconds = progress["took"].get(key)
            if seconds is None and key in progress["started"]:
                # the time it had taken when the run ended, if it did
                seconds = (now if running else progress["ended"]) - progress["started"][key]
            rows.append({
                "key": key,
                "title": item["title"],
                "kinds": item["kinds"],
                "state": state,
                "seconds": seconds,
                "cells": cells,
            })
        used = {kind for item in progress["items"] for kind in item["kinds"]}
        since = progress["since"]
        return {
            "simulations": simulations,
            "kinds": [kind for kind in analysis.KINDS if kind in used],
            "items": rows,
            "elapsed": progress["banked"] + (0.0 if since is None else now - since),
            "running": running,
        }

    def _analysis_interim(self, kind):
        """The report an Analyze (kind "analyze") or Compare simulations
        ("simcompare") run builds a variable at a time (see
        oceanval.analysis_report), to follow as validate's interim report
        is. Called with the lock held."""
        if not self.results_dir:
            return None
        folder = analysis.FOLDER if kind == "analyze" else sim_compare.FOLDER
        status_path = os.path.join(self.results_dir, folder, analysis_report.STATUS)
        # an earlier run's, which would be shown until the run clears it
        with contextlib.suppress(OSError):
            os.remove(status_path)
        return {
            "status_path": status_path,
            "status": None,
            "which": "analysis" if kind == "analyze" else "simcompare",
        }

    def _follow_interim(self, interim):
        """Keep interim["status"] up to date with the status.json the
        interim report's builder writes (see oceanval.live), for as long as
        it is this run's, until the report is complete or stopped."""
        seen = None
        while not self.closed.wait(1):
            with self._lock:
                if self.interim is not interim:
                    return
            try:
                info = os.stat(interim["status_path"])
            except OSError:
                continue
            # it is replaced, rather than written over, each time
            stamp = (info.st_ino, info.st_mtime_ns, info.st_size)
            if stamp == seen:
                continue
            try:
                with open(interim["status_path"]) as file:
                    status = json.load(file)
            except (OSError, ValueError):
                continue
            seen = stamp
            with self._lock:
                if self.interim is not interim:
                    return
                interim["status"] = status
                self._notify()
            if status.get("state") in ("complete", "stopped"):
                return

    # ---- the steps ----

    def choose(self, action, data_dir=None):
        """Take the first step: match up and validate, match up only, or
        validate, or compare - or try the demo, which is a matchup and
        validate run. data_dir fills in the matchups to validate."""
        with self._lock:
            if action == VALIDATE_MENU:
                # the landing page's Validate tile
                if self.view != "home":
                    return False
                self.view = "start"
                self._notify()
                return True
            if action == ANALYZE:
                if self.view not in ("home", "finished"):
                    return False
                self._end_demo()
                self._forget_steps()
                self.question = None
                self.interim = None
                if self._analyze_owner != ANALYZE:
                    # the options step's state was a comparison's
                    self._init_analyze()
                    self._analyze_owner = ANALYZE
                self.action = action
                self.setup_error = None
                self.view = "setup"
                self._notify()
                return True
            if action == VIEWER:
                if self.view != "home":
                    return False
                self._end_demo()
                self._forget_steps()
                self.question = None
                self.interim = None
                self.action = action
                self.setup_error = None
                self.view = "setup"
                self._notify()
                return True
            if action == SIMCOMPARE:
                if self.view not in ("home", "finished"):
                    return False
                self._end_demo()
                self._forget_steps()
                self.question = None
                self.interim = None
                if self._analyze_owner != SIMCOMPARE:
                    # the options step's state was an analysis's: it starts afresh
                    self._init_analyze()
                    self._init_simcompare()
                    self._analyze_owner = SIMCOMPARE
                self.action = action
                self.setup_error = None
                self.view = "simcompare_sims"
                self._notify()
                return True
            if action not in ACTIONS + ("demo",) or self.view not in ("start", "finished"):
                return False
            self._end_demo()
            self._forget_steps()
            self.question = None
            self.interim = None
            if action == "register":
                self._forget_register()
                self.action = action
                self.view = "register_kind"
                self._notify()
                return True
            if action == "demo":
                self.action = "matchup_validate"
                self.demo = {
                    "status": "idle",
                    "bytes": 0,
                    "total": None,
                    "file": 0,
                    "error": None,
                    "model": None,
                    "variables": [],
                    # the model whose files the boxes were filled in for
                    "filled": None,
                    # the report is concise (and fast), or detailed (and slower)
                    "concise": True,
                    # the detail the report's boxes were filled in with
                    "report_filled": None,
                }
                self.view = "demo"
                self._notify()
                return True
            self.action = action
            if action == "validate":
                # the full path, never "." for the directory worked in; one the
                # user entered is kept, unless the matchups just made are given
                shown = self.validate_form["data_dir"]
                if data_dir or shown in ("", self._validate_data_dir_default):
                    self._validate_data_dir_default = _path(data_dir or self.out_dir, self.cwd)
                    self.validate_form["data_dir"] = self._validate_data_dir_default
                self.view = "validate"
            elif action == "compare":
                self.view = "compare"
            else:
                self.setup_error = None
                self.view = "setup"
            self._notify()
            return True

    def back(self, sent=None):
        """Go back a step, keeping what the step's boxes hold, in sent, for
        when it is reached again. Back from the units step shows the recipes
        window again, as it was left. Back from the matchups stops the run,
        which is waiting for the answer, so has matched nothing up, for the
        units step. Once anything is matched up, there is no going back."""
        sent = sent if isinstance(sent, dict) else {}
        if self.action == VIEWER and self.view in ("setup", "viewer_scanning", "viewer"):
            with self._lock:
                old, went = self._viewer_back(sent)
            # outside the lock: it waits for any file being read
            if old is not None:
                old.close()
            return went
        with self._lock:
            if self.view == "units_table":
                self._keep_units_boxes(sent.get("conversions"))
                # the thread waiting on the step shows the window again
                self._units_back = True
                self._units_done.set()
                return True
            if self._failed_back():
                # nothing is running, so there is nothing to stop
                page = self._recipes
                self._leave_run()
                self.interim = None
                self.report_arguments = None
                self.returncode = None
                self.console.clear()
                self._show_units(page.result)
                threading.Thread(
                    target=self._units_again,
                    args=(page,),
                    name="oceanval-units",
                    daemon=True,
                ).start()
                return True
            if self._run_back():
                question = self.question
                if question is not None and question.time_shift:
                    # shown so again, when it is asked again
                    self.time_shift_form = _time_shift_kept(sent.get("form")) or self.time_shift_form
                run, page = self.run, self._recipes
                self._leave_run()
                self._show_units(page.result)
            else:
                return self._step_back(sent)
        # outside the lock: stopping it can take a moment
        run.stop()
        self.console.write(
            "\nOceanVal: stopped before anything was matched up, to go back to the units.\n"
        )
        threading.Thread(
            target=self._units_again,
            args=(page,),
            name="oceanval-units",
            daemon=True,
        ).start()
        return True

    def _step_back(self, sent):
        """Back from a step with no thread waiting on it. Called with the
        lock held."""
        if self.action == ANALYZE:
            if self.view == "setup":
                # the boxes are shown again as they were left
                if isinstance(sent.get("form"), dict):
                    self.analyze_setup_form = self._analyze_form(sent["form"])
                self.view = "home"
                self._notify()
                return True
            went = self._analyze_back(sent)
            if went is not None:
                return went
        if self.action == SIMCOMPARE:
            went = self._simcompare_back(sent)
            if went is not None:
                return went
        earlier = {
            "start": "home",
            "setup": "demo_instructions" if self.demo is not None else "start",
            "demo_instructions": "demo",
            "validate": "start",
            "compare": "start",
            "own_data": "setup",
            "point_data": "own_data",
            "gridded_data": "point_data",
            "register_kind": "start",
            "register_point": "register_kind",
            "register_gridded": "register_kind",
        }
        if self.demo is not None and self.demo["status"] != "downloading":
            earlier["demo"] = "start"
        held = self._held_matchups()
        if self.view == "report_options" and held:
            # where the matchups are saved
            earlier["report_options"] = "output"
            if isinstance(sent.get("form"), dict):
                self.validate_form = _form(sent["form"], default_validate_form())
        if self.view == "output" and held:
            # the point_time_res step, or the matchups again, whose question
            # is still being asked
            earlier["output"] = "point_time_res" if held.time_res_rows else "running"
            if isinstance(sent.get("form"), dict):
                self.output_form = _form(sent["form"], default_output_form())
            # chosen afresh, once the matchups are confirmed again
            self._run_output = None
        if self.view == "validate" and isinstance(sent.get("form"), dict):
            self.validate_form = _form(sent["form"], default_validate_form())
        if self.view == "point_time_res" and held:
            earlier["point_time_res"] = "running"
            if isinstance(sent.get("form"), dict):
                with contextlib.suppress(ValueError):
                    self.point_time_res_form = point_time_res_choice(
                        sent["form"], held.time_res_rows
                    )[1]
        future = self.own_pending
        if self.view == "own_future" and future is not None:
            # to the dataset's form, which the page still holds
            earlier["own_future"] = f"{future['kind']}_data"
            if isinstance(sent.get("boxes"), dict):
                # shown again, if the same dataset is to be saved
                future["typed"] = recipe_forms.clean_boxes(sent["boxes"])
            self._cancel_register_check()
        if self.view not in earlier:
            return False
        if self.view in ("register_point", "register_gridded"):
            # the boxes are shown again as they were left
            kind = self.view.partition("_")[2]
            if isinstance(sent.get("form"), dict):
                self.register_forms[kind] = recipe_forms.clean(kind, sent["form"])
            self._cancel_register_check()
        self.view = earlier[self.view]
        if self.view == "start":
            self._end_demo()
        self._notify()
        return True

    def _forget_steps(self):
        """Forget what was entered in the steps from the own data step on,
        and the recipes window, as another run starts. Called with the lock
        held."""
        self.own_answer = None
        self.own_pending = None
        self.recipes_choices = None
        self._choices_from = None
        self.units_boxes = {}
        self._recipes = None
        self._units_read = None
        self._own_given = None
        self._fvcom = None

    # ---- the demo ----

    def _demo_folder(self):
        return os.path.join(self.cwd, DEMO_FOLDER, "simulation")

    def _demo_downloads(self):
        """The chosen model's files that were asked for: (name, url, path)."""
        model = DEMO_MODELS.get(self.demo["model"]) if self.demo else None
        if model is None:
            return []
        return [
            (file["name"], file["url"], os.path.join(self._demo_folder(), file["name"]))
            for file in (model["files"][variable] for variable in self.demo["variables"])
        ]

    def _demo_models(self):
        """The models, for the page's grid, and whether each file is already
        in the demo's directory."""
        return [
            {
                "label": model["label"],
                "full": model["full"],
                "server": model["server"],
                "member": model["member"],
                "covers": model["covers"],
                "files": {
                    variable: dict(
                        file,
                        present=os.path.isfile(os.path.join(self._demo_folder(), file["name"])),
                    )
                    for variable, file in model["files"].items()
                },
            }
            for model in DEMO_MODELS.values()
        ]

    def _end_demo(self):
        """Leave the demo, if it is the run, so that what it filled in is
        not carried over into another. Called with the lock held."""
        if self.demo is None:
            return
        self.demo = None
        self._demo_cancel.set()
        self.setup_form = default_setup_form()
        self.output_form = dict(default_output_form(self.cwd), out_dir=self.cwd)
        self.validate_form = default_validate_form()

    def demo_download(self, model=None, variables=None):
        """Download the chosen model's output for the variables chosen,
        unless it has been already, and go on to the simulation step with its
        boxes filled in. Returns the HTTP status and the reply for the page."""
        with self._lock:
            if self.view != "demo" or self.demo is None:
                return 409, {"ok": False, "error": "This step is over."}
            if self.demo["status"] == "downloading":
                return 409, {"ok": False, "error": "It is being downloaded already."}
            if model not in DEMO_MODELS:
                return 400, {"ok": False, "error": "Choose a model."}
            if (
                not isinstance(variables, list)
                or not variables
                or not all(variable in DEMO_VARIABLES for variable in variables)
            ):
                return 400, {"ok": False, "error": "Choose one to three variables."}
            self.demo.update(
                status="downloading",
                bytes=0,
                total=None,
                file=0,
                error=None,
                model=model,
                # in the page's order, once each
                variables=[variable for variable in DEMO_VARIABLES if variable in variables],
            )
            # a fresh one, as an earlier download's thread may still hold the last
            self._demo_cancel = threading.Event()
            demo, cancelled = self.demo, self._demo_cancel
            self._notify()
        threading.Thread(
            target=self._download_demo,
            args=(demo, cancelled),
            name="oceanval-demo",
            daemon=True,
        ).start()
        return 200, {"ok": True}

    def _download_demo(self, demo, cancelled):
        shown = [0.0]

        def progress(done, total):
            # the page is told twice a second, not for every chunk
            now = time.monotonic()
            with self._lock:
                demo.update(bytes=done, total=total)
                if now - shown[0] >= 0.5 or done == total:
                    shown[0] = now
                    self._notify()

        try:
            with self._lock:
                downloads = self._demo_downloads()
            for number, (_name, url, path) in enumerate(downloads, 1):
                if os.path.isfile(path):
                    continue
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with self._lock:
                    # the page says which of the files it is on
                    demo.update(file=number, bytes=0, total=None)
                    self._notify()
                _fetch(url, path, progress, cancelled)
        except _Cancelled:
            return
        except Exception as error:
            with self._lock:
                if self.demo is demo:
                    demo.update(
                        status="failed",
                        error=f"The model output could not be downloaded: {error}",
                    )
                    self._notify()
            return
        with self._lock:
            if self.demo is not demo or self.view != "demo":
                return
            demo["status"] = "done"
            self._remove_unwanted_demo_files(demo)
            if demo["filled"] is None:
                # only the first time, so that what was changed after Back is kept
                self.setup_form = demo_setup_form(demo["model"])
                self.output_form = demo_output_form()
                self.validate_form = demo_report_form(demo["concise"])
                demo["report_filled"] = demo["concise"]
            elif demo["filled"] != demo["model"]:
                # another model is chosen: the files to use are now its, unless
                # the box was changed by hand
                if self.setup_form["require"] == demo_setup_form(demo["filled"])["require"]:
                    self.setup_form["require"] = demo["model"]
            demo["filled"] = demo["model"]
            self.setup_error = None
            # told what to do, before the steps
            self.view = "demo_instructions"
            self._notify()

    def demo_detail(self, concise):
        """Choose, on the demo's instructions, whether the report is concise
        (and fast) or detailed (and slower)."""
        if not isinstance(concise, bool):
            return False
        with self._lock:
            if self.view != "demo_instructions" or self.demo is None:
                return False
            self.demo["concise"] = concise
            self._notify()
            return True

    def demo_continue(self):
        """Go on from the demo's instructions to the simulation step. The
        report options are filled in with the detail chosen there, unless
        they were changed by hand since."""
        with self._lock:
            if self.view != "demo_instructions" or self.demo is None:
                return False
            demo = self.demo
            if demo["report_filled"] is not None:
                # the boxes it filled in, unless they were changed by hand since
                earlier = demo_report_form(demo["report_filled"])
                later = demo_report_form(demo["concise"])
                for name in DEMO_PREFILLED["report"]:
                    if self.validate_form[name] == earlier[name]:
                        self.validate_form[name] = later[name]
                demo["report_filled"] = demo["concise"]
            self.view = "setup"
            self._notify()
            return True

    def _remove_unwanted_demo_files(self, demo):
        """Remove the files of the chosen model, from an earlier download,
        for variables that were not asked for this time: they would be
        matched up too. Only the demo's own files are ever removed. Called
        with the lock held."""
        wanted = {name for name, _url, _path in self._demo_downloads()}
        for file in DEMO_MODELS[demo["model"]]["files"].values():
            if file["name"] not in wanted:
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(self._demo_folder(), file["name"]))

    def _demo_prefilled(self):
        """The boxes the demo filled in that still hold what it filled in,
        which the page marks as OceanVal's, for each step."""
        if self.demo["filled"] is None:
            return {step: [] for step in DEMO_PREFILLED}
        filled = {
            "setup": demo_setup_form(self.demo["filled"]),
            "output": demo_output_form(),
            "report": demo_report_form(self.demo["report_filled"]),
        }
        forms = {"setup": self.setup_form, "output": self.output_form, "report": self.validate_form}
        return {
            step: [name for name in names if forms[step].get(name) == filled[step][name]]
            for step, names in DEMO_PREFILLED.items()
        }

    def answer_leftovers(self, action):
        """Remove the temporary files earlier sessions left behind, or keep
        them. Either way the question is not asked again."""
        if action not in ("remove", "keep"):
            return 400, {"ok": False, "error": "Choose to remove or keep the files."}
        with self._lock:
            if self.leftovers_asked:
                return 409, {"ok": False, "error": "That has been answered."}
            self.leftovers_asked = True
            found = self.leftovers
        result = {"removed": 0, "freed_bytes": 0, "failed": []}
        if action == "remove":
            result = leftovers.remove_leftovers([item["path"] for item in found])
        self._notify()
        return 200, dict(result, ok=True)

    def apply_settings(self, settings):
        """Save the window's settings in the home .oceanvalrc, for this window
        and the next. Returns the HTTP status and the reply for the page."""
        try:
            saved = user_settings.save(settings)
        except (OSError, ValueError) as error:
            return 400, {"ok": False, "error": f"{error}"}
        with self._lock:
            self.settings = saved
            self._notify()
        return 200, {"ok": True, "settings": dict(saved), "data_store": data_store.panel(saved)}

    def check_data_store(self):
        """Look at the saved data store for what is wrong with it, and say so.
        It changes nothing. Returns the HTTP status and the reply."""
        with self._lock:
            settings = dict(self.settings)
        return data_store.check_reply(settings)

    def reset(self):
        """Start again from the first page, from any step, with nothing the
        user entered kept: the forms, the user's own data, the run and the
        steps' threads all go. What is kept is not the run's: the window's
        settings, the recipes saved in .oceanvalrc, the simulations
        remembered in .oceanvalcache and the question of temporary files left
        by earlier sessions, which has been answered once. A run in progress
        is stopped."""
        with self._lock:
            if self.closed.is_set():
                return False
            # the steps' threads that are waiting find theirs is over
            self._reset_count += 1
            run, self.run = self.run, None
            page, self.recipes_page = self.recipes_page, None
            reading, self._output_years = self._output_years, None
            self._drop_cdo_check()
            self._own_sim = None
            self._own_sim_number += 1
            draft, self._draft_dir = self._draft_dir, None
            self._cancel_register_check()
            self._drop_model_check()
            self._drop_analyze_check()
            self._sc_drop_checks()
            self._sc_drop_times()
            viewed = self._viewer_drop()
            self.viewer_setup_form = viewer_app.default_setup_form()
            self.viewer_missing = []
            self._end_demo()
            self._forget_steps()
            self._forget_register()
            self.view = "home"
            self.action = None
            self.setup_form = default_setup_form()
            self.setup_error = None
            self.setup_arguments = None
            self.out_dir = self.cwd
            self.overwrite = True
            self.previous = True
            self.output_form = dict(default_output_form(self.cwd), out_dir=self.cwd)
            self.output_made = None
            self._run_output = None
            self.own_data = {"point": [], "gridded": []}
            self.own_saved = []
            self.validate_form = default_validate_form()
            self._validate_data_dir_default = None
            self.compare_form = default_compare_form()
            self.compare_arguments = None
            self.report_arguments = None
            self._script_writer = None
            self.point_time_res_choice = None
            self.point_time_res_form = None
            self.time_shift_form = None
            self.question = None
            self.units_rows = None
            self._units_conversions = None
            self._units_back = False
            self._units_done.clear()
            self.run_label = None
            self.status = None
            self.returncode = None
            self.results_dir = None
            self.identifying = False
            self.matchups_rejected = False
            self.matchup_script_saved = False
            self.script_path = None
            self.interim = None
            self.matchup_progress = None
            self.server_trouble = {}
            self.analysis_progress = None
            self.console.clear()
            # the Analyze and Compare steps start afresh, but their reads and
            # probes still going must not match the numbers of the new ones
            counted = (
                self._analyze_reading,
                self._analyze_shift_number,
                self._analyze_probe_number,
                self._sc_shift_number,
                self._sc_grids_number,
            )
            sims_read = dict(self._sc_reading)
            self._init_analyze()
            self._init_simcompare()
            self._analyze_owner = ANALYZE
            (
                self._analyze_reading,
                self._analyze_shift_number,
                self._analyze_probe_number,
                self._sc_shift_number,
                self._sc_grids_number,
            ) = (number + 1 for number in counted)
            self._sc_reading = {name: number + 1 for name, number in sims_read.items()}
            self._notify()
        # outside the lock: stopping them can take a moment
        if run is not None:
            run.stop()
        if reading is not None:
            reading["reading"].cancel()
        if page is not None:
            page.cancel()
            page.finish()
        if draft is not None:
            shutil.rmtree(draft, ignore_errors=True)
        if viewed is not None:
            viewed.close()
        return True

    def setup(self, form):
        """Read the simulation with create_recipes, from the simulation step's
        boxes. Returns the HTTP status and the reply for the page."""
        if self.action == ANALYZE:
            return self._analyze_setup(form)
        if self.action == VIEWER:
            return self._viewer_setup(form)
        with self._lock:
            if self.view != "setup":
                return 409, {"ok": False, "error": "This step is over."}
            self.setup_form = _form(form, default_setup_form())
            arguments, errors = check_setup(self.setup_form, self.cwd)
            if errors:
                self._notify()
                return 400, {"ok": False, "errors": errors}
            self.setup_error = None
            # written where the output step says, once it is carried on from
            arguments["out"] = self._draft_script()
            self.setup_arguments = arguments
            self._start_cdo_check([arguments])
            self.view = "own_data"
            if self.demo is None:
                self._read_own_simulation(arguments)
            self._notify()
            demo = self.demo is not None
        if demo:
            # only OceanVal's own datasets, so there is nothing to ask
            self.has_own_data(False)
        return 200, {"ok": True}

    # ---- recipes of the user's own ----

    def _forget_register(self):
        """Start registering recipes afresh. Called with the lock held."""
        self._cancel_register_check()
        self.register_forms = {
            kind: recipe_forms.default_form(kind) for kind in recipe_forms.KINDS
        }
        self.register_saved = []
        self.register_answer = None
        self.register_job = None

    def _cancel_register_check(self):
        """Stop looking at data on a server, if that is being done. Called
        with the lock held."""
        check, self._register_check = self._register_check, None
        self.register_job = None
        if check is not None:
            check.cancel()

    def _register_state(self):
        return {
            "kind": self.register_answer,
            "forms": self.register_forms,
            "saved": self.register_saved,
            "job": self.register_job,
            "recipes": user_recipes.listing(self.cwd),
            "files": {
                where: user_recipes.path_for(where, self.cwd) for where in user_recipes.WHERE
            },
            "same_file": user_recipes.same_file(self.cwd),
            "problems": user_recipes.problem_in_files(self.cwd),
            "builtin": list(user_recipes.BUILTIN_SOURCES),
            "labels": {
                variable: user_recipes.labels(variable, self.cwd)
                for variable in sorted(
                    set(user_recipes.VARIABLE_LABELS) | set(user_recipes.variables(self.cwd))
                )
            },
        }

    def register_kind(self, kind):
        """Choose to register point or gridded data, on the way to its page."""
        with self._lock:
            if self.view != "register_kind" or kind not in recipe_forms.KINDS:
                return False
            self.register_answer = kind
            self.view = f"register_{kind}"
            self._notify()
            return True

    def register_names(self, kind, form):
        """What is wrong with the names in a form, and where it would be
        saved, as they are typed. Returns the HTTP status and the reply."""
        if kind not in recipe_forms.KINDS:
            return 400, {"ok": False, "error": "Choose point or gridded data."}
        errors, warnings, info = recipe_forms.check_names(kind, form, self.cwd)
        return 200, {"ok": True, "errors": errors, "warnings": warnings, "info": info}

    def register_data(self, kind, form):
        """Look at the data of a form. Data on this machine is looked at
        straight away, and the reply says what is in it. Data on a server
        takes as long as the server does, so it is looked at in a process of
        its own, and the reply only says that this has begun: the job is in
        the state, when it is done."""
        if kind not in recipe_forms.KINDS:
            return 400, {"ok": False, "error": "Choose point or gridded data."}
        form = recipe_forms.clean(kind, form)
        if not recipe_forms.is_remote(kind, form):
            return 200, dict(recipe_forms.check_data(kind, form, self.cwd), running=False)
        try:
            recipe_checks.check_addresses(
                form["location"], recipe_checks.addresses(form["obs_path"])
            )
        except recipe_checks.CheckFailed as error:
            return 200, {"ok": False, "error": str(error), "running": False}
        with self._lock:
            if self.view != f"register_{kind}":
                return 409, {"ok": False, "error": "This step is over."}
            check = self._check_remote(kind, form)
        check.start()
        return 200, {"ok": True, "running": True}

    def _check_remote(self, kind, form):
        """Begin looking at the data on a server a form points at, whose
        addresses have been checked, in a process of its own: the job is in
        the state until the next is begun. Returns the check, to be started
        once the lock is let go. Called with the lock held."""
        location = form["location"]
        urls = recipe_checks.addresses(form["obs_path"])
        self._cancel_register_check()
        self._register_jobs += 1
        number = self._register_jobs
        self.register_job = {
            "id": number,
            "kind": kind,
            "signature": recipe_forms.signature(kind, form, self.cwd),
            "status": "running",
            "error": None,
            "found": None,
        }
        check = recipe_checks.RemoteCheck(
            location,
            urls if location == "thredds" else urls[0],
            lambda reply: self._register_checked(number, reply),
            cwd=self.cwd,
        )
        self._register_check = check
        self._notify()
        return check

    def _register_checked(self, number, reply):
        with self._lock:
            job = self.register_job
            if job is None or job["id"] != number:
                return
            job["status"] = "ok" if reply.get("ok") else "failed"
            job["error"] = reply.get("error")
            job["found"] = reply.get("found")
            self._register_check = None
            self._notify()

    def register_save(self, kind, form):
        """Check a form, and add its recipe to the file it is to be saved
        in. Returns the HTTP status and the reply for the page."""
        if kind not in recipe_forms.KINDS:
            return 400, {"ok": False, "error": "Choose point or gridded data."}
        with self._lock:
            if self.view != f"register_{kind}":
                return 409, {"ok": False, "error": "This step is over."}
            job = self.register_job
            if job is not None and job["status"] == "running":
                return 400, {
                    "ok": False,
                    "errors": {"obs_path": "The data is still being checked."},
                }
            remote = (
                None
                if job is None
                else {
                    "signature": job["signature"],
                    "ok": job["status"] == "ok",
                    "error": job["error"],
                    "found": job["found"],
                }
            )
        # outside the lock: it opens the data
        recipe, errors, warnings, _ = recipe_forms.check_save(kind, form, self.cwd, remote)
        if errors:
            return 400, {"ok": False, "errors": errors, "warnings": warnings}
        where = recipe_forms.clean(kind, form)["where"]
        try:
            path = user_recipes.save(recipe, where, self.cwd)
        except (ValueError, OSError) as error:
            return 400, {"ok": False, "errors": {"": str(error)}, "warnings": warnings}
        key = user_recipes.key_of(recipe["source"])
        saved = {
            "variable": recipe["variable"],
            "key": key,
            "source": recipe["source"],
            "kind": kind,
            "where": where,
            "path": path,
            "recipe": f'recipe={{"{recipe["variable"]}": "{key}"}}',
        }
        with self._lock:
            self.register_saved.append(saved)
            # the next recipe starts afresh, in the same place
            fresh = recipe_forms.default_form(kind)
            fresh["where"] = where
            if kind == "gridded":
                fresh["location"] = recipe_forms.clean(kind, form)["location"]
            self.register_forms[kind] = fresh
            self._cancel_register_check()
            self._notify()
        return 200, {"ok": True, "saved": saved, "warnings": warnings}

    def register_remove(self, variable, key, where):
        """Take a recipe out of the file it is in. Returns the HTTP status
        and the reply for the page."""
        with self._lock:
            if self.view not in ("register_kind", "register_point", "register_gridded"):
                return 409, {"ok": False, "error": "This step is over."}
        if where not in user_recipes.WHERE:
            return 400, {"ok": False, "error": "Choose which file it is in."}
        try:
            removed = user_recipes.remove(variable, key, where, self.cwd)
        except (ValueError, OSError) as error:
            return 400, {"ok": False, "error": str(error)}
        if not removed:
            return 409, {"ok": False, "error": "That recipe is not in the file."}
        self._notify()
        return 200, {"ok": True}

    def register_done(self):
        """Finish registering recipes, and go back to the start."""
        with self._lock:
            if self.view not in ("register_kind", "register_point", "register_gridded"):
                return False
            self._cancel_register_check()
            self.view = "start"
            self.action = None
            self._notify()
            return True

    # ---- the user's own observations ----

    def has_own_data(self, answer):
        """Answer whether there are observations of your own to add. If not,
        the recipes are next."""
        with self._lock:
            if self.view != "own_data":
                return False
            # shown with the step, when it is reached again
            self.own_answer = bool(answer)
            if answer:
                self.view = "point_data"
                self._notify()
                return True
            self.own_data = {"point": [], "gridded": []}
            self.own_pending = None
            self._forget_own_boxes()
        self._begin_prepare()
        return True

    def add_own_data(self, kind, form):
        """Add one entry of point or gridded data. Returns the HTTP status
        and the reply for the page."""
        with self._lock:
            if kind not in own_data.KINDS or self.view != f"{kind}_data":
                return 409, {"ok": False, "error": "This step is over."}
            existing = [
                (other, arguments)
                for other in own_data.KINDS
                for arguments in self.own_data[other]
            ]
        # outside the lock: it can open files
        arguments, errors = own_data.check_entry(kind, form, existing, self.cwd)
        errors.update(self._unknown_simulation_variable(arguments, errors))
        if errors:
            return 400, {"ok": False, "errors": errors}
        with self._lock:
            if self.view != f"{kind}_data":
                return 409, {"ok": False, "error": "This step is over."}
            self.own_data[kind].append(arguments)
            self._forget_own_boxes()
            self._notify()
        return 200, {"ok": True}

    def own_inspect(self, obs_path):
        """What is in gridded observations of your own, for the Observation
        variable's pick-list: the variables of the first file of a netCDF file,
        a directory of them or a pattern, as recipe_checks.inspect_gridded
        finds them. A reply of {"ok": False, "error": text} says why they could
        not be looked at; it never stops an entry being added, which is
        checked as it is added. Data on a server is not looked at here."""
        with self._lock:
            if self.view != "gridded_data":
                return 409, {"ok": False, "error": "This step is over."}
        text = obs_path.strip() if isinstance(obs_path, str) else ""
        if not text:
            return 200, {"ok": False, "error": ""}
        if text.lower().startswith(("http://", "https://", "ftp://")):
            return 200, {"ok": False, "error": ""}
        try:
            found = recipe_checks.inspect_gridded("disk", text, self.cwd)
        except recipe_checks.CheckFailed as error:
            return 200, {"ok": False, "error": str(error)}
        except Exception as error:
            return 200, {"ok": False, "error": recipe_checks._short(error)}
        return 200, {
            "ok": True,
            "sample": found["sample"],
            "files": found["files"],
            "variables": found["variables"],
        }

    def _read_own_simulation(self, arguments):
        """Start reading the simulation's variables for the own data steps'
        pickers, unless that is done or being done for these files. Called
        with the lock held."""
        key = _simulation(arguments)
        if self._own_sim is not None and self._own_sim["key"] == key:
            return
        self._own_sim_number += 1
        self._own_sim = {"key": key, "status": "reading", "long_names": {}}
        threading.Thread(
            target=self._own_simulation_variables,
            args=(self._own_sim_number, self._reset_count, key),
            name="oceanval-own-variables",
            daemon=True,
        ).start()

    def _own_simulation_variables(self, number, reset, key):
        try:
            with netcdf_lock.LOCK:
                names = _variable_long_names(
                    key[0], key[1], list(key[2]) or None, list(key[3]) or None
                )
        except Exception:
            names = {}
        with self._lock:
            if number != self._own_sim_number or reset != self._reset_count:
                return
            self._own_sim = dict(
                self._own_sim, status="done" if names else "failed", long_names=names
            )
            self._notify()

    def _own_simulation_state(self):
        """The simulation's variables for the own data steps' pickers. Called
        with the lock held."""
        found = self._own_sim
        if found is None:
            return {"status": "none", "read": 0, "available": [], "long_names": {}}
        shown = self.view in ("own_data", "point_data", "gridded_data", "own_future")
        names = found["long_names"] if shown else {}
        return {
            "status": found["status"],
            "read": self._own_sim_number,
            "available": sorted(names, key=lambda name: (name.lower(), name)),
            "long_names": names,
        }

    def _unknown_simulation_variable(self, arguments, errors):
        """The error for a simulation variable that is not in the simulation's
        output, as {"model_variable": text}, or {}. Only once the simulation's
        variables are read: a slow or failed read never stops an entry."""
        with self._lock:
            found = self._own_sim
            available = found["long_names"] if found and found["status"] == "done" else None
        if available is None or "model_variable" in errors:
            return {}
        unknown = _unknown_variables(str(arguments.get("model_variable", "")), available)
        if not unknown:
            return {}
        return {
            "model_variable": f"{', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} "
            "not in the simulation output."
        }

    def remove_own_data(self, kind, index):
        with self._lock:
            if (
                kind not in own_data.KINDS
                or self.view != f"{kind}_data"
                or isinstance(index, bool)
                or not isinstance(index, int)
                or not 0 <= index < len(self.own_data[kind])
            ):
                return False
            # a recipe it was saved as stays saved
            del self.own_data[kind][index]
            self._forget_own_boxes()
            self._notify()
            return True

    def forget_sim_dir(self, sim_dir):
        """Take one simulation directory out of the list of those used before."""
        if not isinstance(sim_dir, str) or not sim_dir:
            return False
        with self._lock:
            if not user_cache.forget_sim_dir(sim_dir):
                return False
            self._notify()
            return True

    def next_own_data(self):
        """Move on from the point data step, to the gridded data step, and
        from that to the recipes."""
        with self._lock:
            if self.view == "point_data":
                self.view = "gridded_data"
                self._notify()
                return True
            if self.view != "gridded_data":
                return False
        self._begin_prepare()
        return True

    # ---- the user's own observations, saved for future use too ----

    def own_future(self, kind, form):
        """Add one entry of point or gridded data for use now and in future:
        check it as add_own_data does, and as a recipe's names and data are
        checked, and go on to the own_future step. That asks for what a recipe
        needs that the entry does not say (see recipe_forms.missing_boxes),
        and adds the entry once the recipe is saved (see own_future_save).
        Data on a server is looked at there, in a process of its own. Returns
        the HTTP status and the reply for the page."""
        with self._lock:
            if kind not in own_data.KINDS or self.view != f"{kind}_data":
                return 409, {"ok": False, "error": "This step is over."}
            existing = [
                (other, arguments)
                for other in own_data.KINDS
                for arguments in self.own_data[other]
            ]
        # outside the lock: it opens the data
        arguments, errors = own_data.check_entry(kind, form, existing, self.cwd)
        errors.update(self._unknown_simulation_variable(arguments, errors))
        errors.update(_future_problems(kind, arguments, errors))
        if errors:
            return 400, {"ok": False, "errors": errors}
        recipe_form = recipe_forms.from_own_data(kind, arguments)
        remote = recipe_forms.is_remote(kind, recipe_form)
        found = None
        try:
            if remote:
                recipe_checks.check_addresses(
                    recipe_form["location"], recipe_checks.addresses(recipe_form["obs_path"])
                )
            else:
                reply = recipe_forms.check_data(kind, recipe_form, self.cwd)
                if not reply["ok"]:
                    raise recipe_checks.CheckFailed(reply["error"] or "The data could not be read.")
                found = reply["found"]
        except recipe_checks.CheckFailed as error:
            return 400, {"ok": False, "errors": {"obs_path": _for_future(str(error))}}
        with self._lock:
            if self.view != f"{kind}_data":
                return 409, {"ok": False, "error": "This step is over."}
            identity = _future_identity(kind, arguments)
            kept = self.own_pending
            self.own_pending = {
                "kind": kind,
                "arguments": arguments,
                "form": recipe_form,
                "found": found,
                "identity": identity,
                # as Back left them, for the same dataset
                "typed": dict(kept["typed"]) if kept and kept["identity"] == identity else {},
            }
            check = self._check_remote(kind, recipe_form) if remote else None
            self.view = "own_future"
            self._notify()
        if check is not None:
            check.start()
        return 200, {"ok": True}

    def own_future_check(self):
        """Look at the own_future step's data on a server again, as when it
        could not be checked."""
        with self._lock:
            future = self.own_pending
            if self.view != "own_future" or future is None:
                return False
            job = self.register_job
            if not recipe_forms.is_remote(future["kind"], future["form"]) or (
                job is not None and job["status"] == "running"
            ):
                return False
            check = self._check_remote(future["kind"], future["form"])
        check.start()
        return True

    def _own_saved_state(self):
        """For each of the user's own datasets, the recipe of the same kind,
        variable and source saved from the own_future step, or None. Called
        with the lock held."""

        def saved(kind, entry):
            name = (kind, str(entry.get("name", "")).lower(), user_recipes.key_of(entry.get("source", "")))
            for recipe in self.own_saved:
                if (recipe["kind"], recipe["variable"], recipe["key"]) == name:
                    return recipe
            return None

        return {
            kind: [saved(kind, entry) for entry in self.own_data[kind]]
            for kind in own_data.KINDS
        }

    def _own_future_data(self, future):
        """What is known of the data of the own_future step's dataset, as
        (status, error, found): status is "ok", "checking" or "failed", and
        found is what recipe_checks found in it. Called with the lock held."""
        if future["found"] is not None:
            return "ok", None, future["found"]
        job = self.register_job
        if job is None or job["signature"] != recipe_forms.signature(
            future["kind"], future["form"], self.cwd
        ):
            return "failed", "The data has not been checked.", None
        if job["status"] == "running":
            return "checking", None, None
        if job["status"] == "ok":
            return "ok", None, job["found"]
        return "failed", job["error"] or "The data could not be checked.", None

    def _own_future_state(self):
        """What the own_future step shows, while it is shown: the dataset,
        the recipe it is to be, the boxes the step asks (see
        recipe_forms.missing_boxes) with what was typed in them, what stops
        it being saved in either file, and what is known of the data. Called
        with the lock held."""
        future = self.own_pending
        if future is None or self.view != "own_future":
            return None
        kind, arguments, form = future["kind"], future["arguments"], future["form"]
        status, error, found = self._own_future_data(future)
        places = {}
        for where in user_recipes.WHERE:
            errors, warnings, info = recipe_forms.check_names(
                kind, dict(form, where=where), self.cwd
            )
            places[where] = {
                "path": user_recipes.path_for(where, self.cwd),
                "errors": list(dict.fromkeys(errors.values())),
                "warnings": warnings,
            }
        labels = user_recipes.labels(arguments["name"], self.cwd)
        return {
            "kind": kind,
            "entry": arguments,
            "recipe": info["recipe"],
            "labels": list(labels) if labels else None,
            "boxes": [
                dict(box, value=future["typed"].get(box["name"], ""))
                for box in recipe_forms.missing_boxes(kind, arguments, found, self.cwd)
            ],
            "where": places,
            "same_file": user_recipes.same_file(self.cwd),
            "data": {"status": status, "error": error},
        }

    def own_future_save(self, sent):
        """Save the own_future step's dataset as a recipe, with what its boxes
        hold (sent: {box: text}, where a box left blank takes what OceanVal
        assumes for it), then add it to the run as add_own_data does, with the
        source information and names typed in, and go back to its own data
        step. Nothing is saved or added unless both can be. Returns the HTTP
        status and the reply for the page."""
        typed = recipe_forms.clean_boxes(sent)
        with self._lock:
            future = self.own_pending
            if self.view != "own_future" or future is None:
                return 409, {"ok": False, "error": "This step is over."}
            kind, arguments, form = future["kind"], future["arguments"], future["form"]
            status, error, found = self._own_future_data(future)
            if status == "checking":
                return 400, {"ok": False, "errors": {"": "The data is still being checked."}}
            remote = None
            if recipe_forms.is_remote(kind, form):
                remote = {
                    "signature": (self.register_job or {}).get("signature"),
                    "ok": status == "ok",
                    "error": error,
                    "found": found,
                }
            existing = [
                (other, entry)
                for other in own_data.KINDS
                for entry in self.own_data[other]
            ]
        boxes = recipe_forms.missing_boxes(kind, arguments, found, self.cwd)
        names = {box["name"] for box in boxes}
        filled = recipe_forms.with_boxes(form, boxes, typed)
        # outside the lock: it opens the data
        recipe, errors, warnings, _ = recipe_forms.check_save(kind, filled, self.cwd, remote)
        if errors:
            return 400, {"ok": False, "errors": _future_errors(errors, names), "warnings": warnings}
        # what was typed for the report is used now, as well as in future
        entry = dict(arguments)
        for name in ("source_info", "long_name", "short_name", "short_title"):
            if name in names and name in typed:
                entry[name] = typed[name]
        reason = own_data.refusal(kind, entry, existing)
        if reason is not None:
            return 400, {"ok": False, "errors": {"": reason}, "warnings": warnings}
        try:
            path = user_recipes.save(recipe, filled["where"], self.cwd)
        except (ValueError, OSError) as error:
            return 400, {"ok": False, "errors": {"": str(error)}, "warnings": warnings}
        key = user_recipes.key_of(recipe["source"])
        saved = {
            "variable": recipe["variable"],
            "key": key,
            "where": filled["where"],
            "path": path,
            "recipe": f'recipe={{"{recipe["variable"]}": "{key}"}}',
        }
        with self._lock:
            if self.own_pending is not future:
                return 409, {
                    "ok": False,
                    "error": f"It was saved, as {saved['recipe']}, but this step is over, "
                    "so it was not added to this run.",
                }
            self.own_data[kind].append(entry)
            self.own_saved.append(dict(saved, kind=kind))
            self.own_pending = None
            self._cancel_register_check()
            self._forget_own_boxes()
            if self.view == "own_future":
                self.view = f"{kind}_data"
            self._notify()
        return 200, {"ok": True, "saved": saved, "warnings": warnings}

    def _before_recipes(self):
        """The step Back from the recipes goes to: the last of the steps
        for the user's own observations, if they were used."""
        if self.demo is not None:
            # the own data step is not shown
            return "setup"
        return "gridded_data" if any(self.own_data.values()) else "own_data"

    def _begin_prepare(self):
        with self._lock:
            arguments = self.setup_arguments
            self.status = None
            self.view = "preparing"
            self.console.clear()
            self._notify()
        threading.Thread(
            target=self._prepare,
            args=(arguments,),
            name="oceanval-recipes",
            daemon=True,
        ).start()

    def _prepare(self, arguments):
        with self._lock:
            known = self._fvcom
            number = self._reset_count
        # asked already, for the same files
        fvcom = known[1] if known is not None and known[0] == _simulation(arguments) else None
        stream = _ConsoleStream(self.console)
        try:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                with prompts.answered_by(self._ask_in_window), recipes_gui.hosted_by(
                    self
                ):
                    out = _create_recipes(
                        **arguments,
                        fvcom=fvcom,
                        ask=True,
                        gui=True,
                        validate=self.action == "matchup_validate",
                        own_data=self.own_data,
                        cwd=self.cwd,
                    )
                with self._lock:
                    page = self._recipes
                if out is not None and page is not None and not self.closed.is_set():
                    # the units step, and then the run
                    self._units_onwards(page, page.result)
        except Exception as error:
            self._went_wrong(error, number)
            return
        if out is None or self.closed.is_set():
            # Back, in the recipes window
            with self._lock:
                if (
                    self.view in ("preparing", "recipes")
                    and not self.closed.is_set()
                    and number == self._reset_count
                ):
                    self.view = self._before_recipes()
                    self._notify()

    def _went_wrong(self, error, number=None):
        """Go back to the simulation step, saying what went wrong while it
        was read, or in the steps after it. number is the count of restarts
        (see reset) the steps were begun in: after another, there is nothing
        to go back to."""
        if number is not None and number != self._reset_count:
            return
        # create_recipes' own checks say what to change; anything else
        # is worth seeing in full
        if not isinstance(error, (ValueError, TypeError)):
            self.console.write(
                "".join(traceback.format_exception(type(error), error, error.__traceback__))
            )
        with self._lock:
            self.setup_error = str(error)
            self.view = "setup"
            self._notify()

    def show_recipes(self, page):
        """Show the create_recipes window as the recipes step, and wait for
        it to be used (see recipes_gui.hosted_by). The window is kept, to be
        shown again for Back from the steps after it (see _units_onwards)."""
        with self._lock:
            if self.view != "preparing":
                # the window was restarted while the simulation was read
                return None
        page.token = self.token
        page.context["app"] = {
            "action": self.action,
            # matchup needs a thickness for these, as for Vertical in the window
            "own_vertical": any(
                arguments.get("vertical")
                for entries in self.own_data.values()
                for arguments in entries
            ),
            # the window's point_time_res applies to these too
            "own_point": bool(self.own_data["point"]),
        }
        page.form["start"] = ""
        page.form["end"] = ""
        # each point dataset's are typed, or filled in from these
        page.form["point_start"] = ""
        page.form["point_end"] = ""
        # the draft script's: the cores are chosen once the matchups are confirmed
        # (see output_continue and report), so the window does not show the box
        page.form["cores"] = "1"
        # the years to choose from, read from the output's times
        page.context["output_years"] = self.output_years_of(_simulation(page.context))
        with self._lock:
            self._recipes = page
            # not asked again, while the simulation is read from the same files
            self._fvcom = (_simulation(page.context), page.context["fvcom"])
        return self._recipes_window(page)

    def _recipes_window(self, page):
        """Show the recipes window, page, as it was last left (see
        recipes_restore), and wait for it to be used. Returns what it
        chose, once it has written the script, or None for Back, which goes
        to the step before it."""
        with self._lock:
            if self.closed.is_set():
                return None
            number = self._reset_count
            restore = recipes_restore(
                self.recipes_choices,
                page.rows,
                page.available,
                page.context["domain"],
                same=self._choices_from is page,
            )
            if restore is None:
                page.context["app"].pop("restore", None)
            else:
                page.context["app"]["restore"] = restore
            page.reopen()
            self.recipes_page = page
            self.view = "recipes"
            self._notify()
        result = page.wait()
        with self._lock:
            self.recipes_page = None
            if page.sent is not None:
                self.recipes_choices = recipes_choices(
                    page.sent, page.rows, page.context["domain"]
                )
                self._choices_from = page
            if result is None:
                # the steps before it can change the simulation, so it is
                # made afresh once they are carried on from
                self._recipes = None
                if not self.closed.is_set() and number == self._reset_count:
                    self.view = self._before_recipes()
                    self._notify()
        return result

    def _units_onwards(self, page, result, shown=False):
        """The units step, for what the recipes window, page, chose
        (result), with Back from it to the window, and then the run, once
        the units are carried on from. shown is whether the units step is
        being shown already, for Back from the matchups. The run starts from
        a draft of the script, in a folder of OceanVal's own (see
        _draft_script), with nothing written in the output directory: where
        that is, what is done with the matchups there and the cores are
        chosen once the matchups are confirmed (see output_continue), and
        given to matchup along with yes (see _yes)."""
        while True:
            conversions = self._units_step(page, result, shown)
            shown = False
            if conversions is None:
                return
            if conversions is not _BACK:
                break
            result = self._recipes_window(page)
            if result is None:
                return
        mapping, selection, settings, point_options, gridded_options = result
        draft = os.path.dirname(page.context["out"])
        settings = dict(settings or {}, out_dir=draft, overwrite=True, cores=1)
        result = (mapping, selection, settings, point_options, gridded_options)
        if conversions:
            result = self._convert_units(result, conversions)
        page.write(*result)
        with self._lock:
            if self.closed.is_set():
                return
            # to write the script again with what is chosen once the matchups
            # are confirmed, where the output step says
            self._script_writer = (page.write, result)
            # none, until the script is started, so there is nothing to leave
            self.run = None
            self.view = "running"
            self.status = "starting"
            # the script, once written, starts by finding the files
            self.identifying = True
            self.matchup_progress = None
            self.server_trouble = {}
            self._notify()
        out = page.context["out"]
        # to validate as well, the report is built once the script has run,
        # with the options chosen after the matchups are checked
        kind = "matchup" if self.action == "matchup_validate" else "script"
        self.start_run([kind, out], f"python {_shown(out, self.cwd)}")

    def refilter(self, exclude, require):
        """Read the simulation again with other file filters (exclude and
        require, as words separated by spaces), for the matchups page. The
        run asking whether the matchups are right has matched nothing up, so
        is stopped, as for Back, and the recipes window is shown again for
        the files that pass them, as the model variables in them can differ,
        starting with what was chosen in it where that still applies (see
        recipes_restore)."""
        exclude = str(exclude or "").split()
        require = str(require or "").split()
        with self._lock:
            if not (self._run_back() and self._held_matchups() is not None):
                return False
            run = self.run
            self._leave_run()
            self.setup_form["exclude"] = " ".join(exclude)
            self.setup_form["require"] = " ".join(require)
            self.setup_arguments["exclude"] = exclude or None
            self.setup_arguments["require"] = require or None
            # made afresh, for the files that pass them
            self._recipes = None
        # outside the lock: stopping it can take a moment
        run.stop()
        self._begin_prepare()
        return True

    def _units_again(self, page):
        """The units step and the steps after it, again, for Back from the
        matchups, in a thread of its own."""
        stream = _ConsoleStream(self.console)
        with self._lock:
            number = self._reset_count
        try:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                self._units_onwards(page, page.result, shown=True)
        except Exception as error:
            self._went_wrong(error, number)

    def _convert_units(self, result, conversions):
        """The recipes window's choices, result, with the conversions chosen
        in the units step added, for the script to be written with."""
        mapping, selection, settings, point_options, gridded_options = result
        gridded_options = {key: dict(value) for key, value in gridded_options.items()}
        point_options = {key: dict(value) for key, value in point_options.items()}
        with self._lock:
            # the conversions are written into the user's own data, so it is
            # kept as given, for Back from the matchups to put back
            self._own_given = copy.deepcopy(self.own_data)
        units.apply_conversions(
            conversions, gridded_options, self.own_data, point_options
        )
        return mapping, selection, settings, point_options, gridded_options

    # ---- the units step ----

    def _show_units(self, result):
        """Show the units step for what the recipes window chose (result),
        with the rows read for it already, if they have been, as for Back
        from the matchups. Returns whether they are still to be read. Called
        with the lock held."""
        read = self._units_read if self._units_read and self._units_read[0] is result else None
        # the page says they are being read until they have been
        self.units_rows = read[1] if read else None
        self._units_conversions = None
        self._units_back = False
        self._units_done.clear()
        self.view = "units_table"
        self._notify()
        return read is None

    def _units_step(self, page, result, shown=False):
        """Show the units of the matchups the recipes window chose (result),
        and wait for the step to be carried on from. Returns the conversions
        to make, only those that differ from what the script has, _BACK for
        Back, or None if OceanVal is closed. shown is whether the step is
        being shown already."""
        with self._lock:
            if self.closed.is_set():
                return None
            number = self._reset_count
            to_read = self.units_rows is None if shown else self._show_units(result)
            setup, own = dict(self.setup_arguments), self.own_data
        if to_read:
            rows = self._read_units(result, setup, own)
            with self._lock:
                if number != self._reset_count:
                    return None
                self._units_read = (result, rows)
                if self.view == "units_table":
                    self.units_rows = rows
                    self._notify()
        while not self._units_done.wait(0.25):
            if self.closed.is_set() or number != self._reset_count:
                return None
        with self._lock:
            return _BACK if self._units_back else self._units_conversions

    def _keep_units_boxes(self, sent):
        """Keep what the units step's boxes hold, as sent, with the rows they
        were filled in for: they are filled in again while those rows are the
        same. Called with the lock held."""
        if not isinstance(sent, dict):
            return
        for row in self.units_rows or []:
            boxes = sent.get(row["key"])
            if isinstance(boxes, dict):
                self.units_boxes[row["key"]] = {
                    "multiplier": str(boxes.get("multiplier") or ""),
                    "adder": str(boxes.get("adder") or ""),
                    "row": _units_row(row),
                }

    def _units_boxes_shown(self):
        """What the units step's boxes are filled in with again: for each row
        whose boxes were kept, if it is the same as when they were."""
        shown = {}
        for row in self.units_rows or []:
            kept = self.units_boxes.get(row["key"])
            if kept is not None and kept["row"] == _units_row(row):
                shown[row["key"]] = {"multiplier": kept["multiplier"], "adder": kept["adder"]}
        return shown

    def _forget_own_boxes(self):
        """Forget the units step's boxes for the user's own data, whose keys
        are their places in the lists, as the lists change. Called with the
        lock held."""
        self.units_boxes = {
            key: kept
            for key, kept in self.units_boxes.items()
            if not key.startswith(("own:", "ownpoint:"))
        }

    def _read_units(self, choices, setup, own):
        """The units step's table: the units of each matchup the recipes
        window chose, and the conversion OceanVal suggests for each (see
        units.matchups). It reads the simulation's files, so is not called
        with the lock held."""
        mapping, selection, settings, point_options, gridded_options = choices
        attributes = units.model_attributes(
            setup["simdir"],
            setup["ndown"],
            units.model_variables(mapping, selection, own, self.cwd),
            # the window can change the file filters along with the rest
            exclude=settings.get("exclude"),
            require=settings.get("require"),
        )
        return units.matchups(
            mapping,
            selection,
            gridded_options,
            point_options,
            own,
            {name: found["units"] for name, found in attributes.items()},
            self.cwd,
            model_descriptions={
                name: found["description"] for name, found in attributes.items()
            },
        )

    def units_continue(self, sent, confirmed=False):
        """Carry on from the units table, with the conversions its boxes
        hold, but only once the units have been confirmed: they always have to
        be, and the page's button being disabled is not what makes sure of it.
        Returns the HTTP status and the reply for the page."""
        with self._lock:
            if self.view != "units_table":
                return 409, {"ok": False, "error": "That cannot be done now."}
            if self.units_rows is None:
                return 409, {"ok": False, "error": "The units are still being read."}
            # for Back from the steps after it, to show the step as it was left
            self._keep_units_boxes(sent)
            if confirmed is not True:
                return 400, {"ok": False, "error": "Confirm the units before carrying on."}
            keys = {row["key"] for row in self.units_rows}
        conversions, errors = units.check_conversions(sent)
        errors.update({key: "This matchup is not in the table." for key in conversions if key not in keys})
        if errors:
            return 400, {"ok": False, "errors": errors}
        # only what differs from what the script already has
        changed = {}
        for row in self.units_rows:
            chosen = conversions.get(row["key"])
            if chosen and chosen != (row["obs_multiplier"], row["obs_adder"]):
                changed[row["key"]] = chosen
        with self._lock:
            if self.view != "units_table":
                return 409, {"ok": False, "error": "That cannot be done now."}
            self._units_conversions = changed
            self._units_done.set()
        return 200, {"ok": True}

    # ---- the output step, once the matchups are confirmed ----

    def _draft_script(self):
        """Where the recipes window writes the script, and the run starts it
        from: a folder of OceanVal's own, removed at exit, as the user names
        the script, and the output directory, only once the matchups are
        confirmed (see output_continue). Called with the lock held."""
        if self._draft_dir is None or not os.path.isdir(self._draft_dir):
            self._draft_dir = tempfile.mkdtemp(prefix="oceanval_script_")
            atexit.register(shutil.rmtree, self._draft_dir, True)
        return os.path.join(self._draft_dir, "matchup.py")

    def _output_first(self):
        """Whether the output step is still to come before this run matches
        anything up: it is a window's matchup, asking whether the matchups
        are right, with its output not chosen yet. Called with the lock held."""
        return (
            self.run is not None
            and self.run.args[:1] in (["matchup"], ["script"])
            and self._run_output is None
        )

    def _show_output(self, question):
        """Show the output step, for the matchups question lists, which it
        holds the answer to. Called with the lock held."""
        details = question.details or {}
        self.output_made = sorted(
            {str(matchup.get("key", "")).lower() for matchup in details.get("matchups") or []}
        )
        self.view = "output"
        self._notify()

    def _check_matchup_output(self, form):
        """check_output for this run. (Not _check_output, which is Analyze's
        own, see AnalyzeSteps.) Called with the lock held."""
        return check_output(
            form,
            self.cwd,
            # matchup alone builds no report, so has nothing to ask of it
            report=self.action == "matchup_validate",
            demo_dir=os.path.join(self.cwd, DEMO_FOLDER) if self.demo is not None else None,
            made=set(self.output_made or ()),
        )

    def _keep_in_script(self, out=None, **settings):
        """Put settings into what the script is written from, and write it
        to out from now on, if given. Called with the lock held."""
        if self._script_writer is None:
            return
        write, result = self._script_writer
        if out is not None:
            write = functools.partial(getattr(write, "func", write), out=out)
            self.script_path = out
        result = list(result)
        result[2] = dict(result[2] or {}, **settings)
        self._script_writer = (write, tuple(result))

    def _write_script(self, writer, **report):
        """Write the script, from writer, as _script_writer holds it, saying
        so in the console if it cannot be. Called without the lock held."""
        if writer is None:
            return
        write, choices = writer
        try:
            write(*choices, **report)
        except OSError as error:
            self.console.write(f"The script could not be written: {error}\n")

    def output_continue(self, form):
        """Carry on from the output step, with its boxes (form): where the
        matchups are saved, what is done with those there already, the
        script's name, and, for matchup alone, the cores, the spatial subset
        and the missing values (see check_matchup_boxes). A matchup and
        validate run goes on to the report options; matchup alone carries
        on. Returns the HTTP status and the reply for the page."""
        with self._lock:
            question = self._held_matchups()
            if self.view != "output" or question is None:
                return 409, {"ok": False, "error": "That cannot be done now."}
            self.output_form = _form(form, default_output_form())
            arguments, errors = self._check_matchup_output(self.output_form)
            cores, matchup = None, {}
            if self.action == "matchup":
                cores, error = check_cores(self.output_form["cores"])
                if error is not None:
                    errors["cores"] = error
                matchup, matchup_errors = check_matchup_boxes(self.output_form)
                errors.update(matchup_errors)
                matchup["domain_mask"] = check_domain_mask(self.output_form, self.cwd, errors)
            if errors:
                self._notify()
                return 400, {"ok": False, "errors": errors}
            self.out_dir = arguments["out_dir"]
            self.overwrite = arguments["overwrite"]
            self.previous = arguments["previous"]
            # the matchups, and the report, go where it says
            self.results_dir = self.out_dir
            self._run_output = {"out_dir": self.out_dir, "overwrite": self.overwrite}
            self._keep_in_script(
                out=arguments["out"],
                # the directory worked in is matchup's default
                out_dir="" if self.out_dir == self.cwd else self.out_dir,
                overwrite=self.overwrite,
            )
            report = self.action == "matchup_validate"
            if report:
                self.view = "report_options"
            else:
                self._match_up_now(cores, matchup)
            writer = self._script_writer
            self._notify()
        # where the user said, now that it is named
        self._write_script(writer)
        if report:
            return 200, {"ok": True}
        self.console.write("y\n")
        question.respond(self._yes({}, question))
        return 200, {"ok": True}

    def domain_mask_map(self, text):
        """The map of the domain mask in the file typed (see
        check_domain_mask), for the output step or the report options (or
        Compare simulations' grids step):
        {"ok", "item" (a Bokeh json_item), "notes", "kept", "total"} (the
        cells kept, of all), or {"ok": False, "error"}. Returns the HTTP
        status and the reply."""
        from oceanval import grid_align, viewer_plots

        with self._lock:
            # Compare simulations asks it on its grids step, where no matchups are held
            compare = self.view == "simcompare_grids"
            if not compare and (self.view not in ("output", "report_options") or self._held_matchups() is None):
                return 409, {"ok": False, "error": "This step is over."}
        _, mask, error = inspect_domain_mask(str(text or "").strip(), self.cwd)
        if error is not None:
            return 200, {"ok": False, "error": error}
        try:
            kept, total = domain_mask.cells(mask)
            item, notes, _ = viewer_plots.map_plot(
                mask, grid_align.about(mask), "Domain mask",
                cmap="Blues", vmin=0, vmax=1, coast=viewer_plots.has_geoviews(),
            )
        except Exception as problem:  # noqa: BLE001 - told to the page
            traceback.print_exc()
            return 200, {"ok": False, "error": f"The file could not be drawn: {type(problem).__name__}: {problem}"}
        return 200, {"ok": True, "item": item, "notes": notes, "kept": kept, "total": total}

    def _match_up_now(self, cores, matchup):
        """Matchup alone carries on, with the cores, the spatial subset and
        the missing values (see check_matchup_boxes), sent along with yes,
        which the caller then answers. Called with the lock held."""
        self._run_output.update(cores=cores, **matchup)
        self._keep_in_script(cores=cores, **matchup)
        self.view = "running"
        self.question = None

    def validate(self, form):
        """Build the report, from the report options step's boxes."""
        with self._lock:
            if self.view != "validate":
                return 409, {"ok": False, "error": "This step is over."}
            self.validate_form = _form(form, default_validate_form())
            form = dict(self.validate_form)
        # outside the lock: reading a regions file can take a moment
        arguments, errors = check_validate(form, self.cwd)
        if errors:
            return 400, {"ok": False, "errors": errors}
        with self._lock:
            if self.view != "validate":
                return 409, {"ok": False, "error": "This step is over."}
            # so that a second click does not start a second run
            self.view = "running"
            self.status = "starting"
            self.results_dir = arguments["out_dir"]
            self.console.clear()
        self.start_run(
            ["validate", json.dumps(arguments)], _validate_call(arguments, self.cwd)
        )
        return 200, {"ok": True}

    def compare(self, form):
        """Compare the validations of the simulations in the compare step's
        rows. Returns the HTTP status and the reply for the page."""
        with self._lock:
            if self.view != "compare":
                return 409, {"ok": False, "error": "This step is over."}
            self.compare_form = _form(form, default_compare_form())
            form = dict(self.compare_form)
        arguments, errors = check_compare(form, self.cwd)
        if errors:
            return 400, {"ok": False, "errors": errors}
        with self._lock:
            if self.view != "compare":
                return 409, {"ok": False, "error": "This step is over."}
            # so that a second click does not start a second run
            self.view = "running"
            self.status = "starting"
            self.results_dir = arguments["out_dir"]
            self.compare_arguments = arguments
            self.console.clear()
        self.start_run(
            ["compare", json.dumps(arguments)], _compare_call(arguments, self.cwd)
        )
        return 200, {"ok": True}

    # ---- the report options of a matchup and validate run ----

    def _run_filters(self):
        """The file filters the matchup script was written with, as words
        separated by spaces, or None without one."""
        if self._script_writer is None:
            return None
        settings = self._script_writer[1][2] or {}
        return {
            name: " ".join(settings.get(name) or [])
            for name in ("exclude", "require")
        }

    def _held_matchups(self):
        """The question of whether the matchups are right, while its answer
        is held back for the report options to be chosen, or None."""
        question = self.question
        return question if question is not None and question.matchups else None

    def _run_back(self):
        """Whether Back can leave the run for the output step: it is the
        matchup the output step started, still finding the files or asking
        whether the matchups are right, so it has matched nothing up."""
        run = self.run
        return (
            self.view == "running"
            and run is not None
            and self._recipes is not None
            and run.args[:1] in (["matchup"], ["script"])
            and self.report_arguments is None
            and (
                self.identifying
                or self._held_matchups() is not None
                or (self.question is not None and self.question.time_shift)
            )
        )

    def _failed_back(self):
        """Whether Back can leave a run that failed (or was stopped), in the
        matchup or the report, for the units step, as it can the matchups
        question: the choices made since are still kept."""
        return (
            self.view == "finished"
            and self.status in ("failed", "stopped")
            and self.action in ("matchup_validate", "matchup")
            and self.run is not None
            and self._recipes is not None
            and not self.matchup_script_saved
        )

    def _leave_run(self):
        """Leave the run, for Back from the matchups: it is forgotten, so
        that its end goes unnoticed once it is stopped, and the user's own
        data is put back as given, without the conversions the units step
        wrote into it. Called with the lock held."""
        self.run = None
        self.run_label = None
        self.question = None
        self.status = None
        self.identifying = False
        self.matchup_progress = None
        self.server_trouble = {}
        self.analysis_progress = None
        self._script_writer = None
        self._run_output = None
        self._drop_model_check()
        if self._own_given is not None:
            # into the same dictionary, which the recipes window writes the script from
            for kind in own_data.KINDS:
                self.own_data[kind] = self._own_given[kind]
            self._own_given = None

    def _report_gridded(self):
        """Whether the run's matchups include gridded ones, which a transect
        is for: from the recipes chosen and the user's own gridded data. None
        if that is not known here, as when validating matchups made before."""
        if self._script_writer is None:
            return None
        mapping, selection = self._script_writer[1][:2]
        if any(
            variable in mapping and units._recipe_entry(variable, recipe) is not None
            for variable, recipe in selection
        ):
            return True
        return bool(self.own_data["gridded"])

    def _report_matchups(self):
        """The matchups the report options of a matchup and validate run
        list, to choose those the report validates (see choose_matchups):
        those matchup asks about, or, if it has made them without asking,
        those it made. None if they are not known."""
        question = self._held_matchups()
        if question is not None:
            listed = question.details.get("matchups")
            return listed if isinstance(listed, list) else None
        if self.view != "report_options" or self.report_arguments is not None:
            return None
        directory = self.results_dir or self.out_dir
        found = selection.on_disk(directory) if directory else []
        if found and self.previous is not True:
            # those the report validates of them, as the simulation step chose
            with contextlib.suppress(ValueError):
                found = selection.of_last_run(found, selection.last_run(directory), self.previous)
        return found or None

    def _report_vertical(self):
        """Whether the run's matchups include point ones through the water
        column, which the depth bins are for: from the point recipes chosen
        with Vertical and the user's own point data. None if that is not
        known here, as when validating matchups made before."""
        if self._script_writer is None:
            return None
        point_options = self._script_writer[1][3]
        if any(options.get("vertical") for options in point_options.values()):
            return True
        return any(entry.get("vertical") for entry in self.own_data["point"])

    def _time_shift_state(self):
        """How the question of whether to shift the model's times starts,
        whose rows are in its details. Called with the lock held."""
        return {
            # OceanVal's suggestion, until it is changed
            "form": self.time_shift_form or {"mode": "shift"},
            "suggested": time_stamps.describe(time_stamps.SUGGESTED),
        }

    def _point_time_res_state(self):
        """What the point_time_res step shows. Called with the lock held."""
        question = self._held_matchups()
        rows = question.time_res_rows if question is not None else []
        return {
            "rows": rows,
            "form": self.point_time_res_form or default_point_time_res_form(rows),
            "options": [
                {"value": ",".join(value), "label": label, "hint": hint}
                for value, label, hint in time_res.POINT_TIME_RES_OPTIONS
            ],
            "suggested": ",".join(time_res.SUGGESTED),
        }

    # ---- the check of the dates of the model output ----

    def _model_check_request(self, question):
        """What a check of the dates of the model output is given (see
        model_check.check), from the question of whether the matchups are
        right: a row for each variable, and its files, by file pattern."""
        details = question.details
        sim_dir = os.path.join(self.cwd, str(details.get("sim_dir") or ""))
        years = list(details.get("years") or [None, None])
        rows = []
        files = {}
        for row in details.get("rows") or []:
            if not isinstance(row, dict) or not row.get("model_variable") or not row.get("pattern"):
                continue
            rows.append(
                {
                    "variable": row.get("variable"),
                    "pattern": row["pattern"],
                    "time_res": row.get("time_res"),
                    "groups": _year_groups(row, years),
                }
            )
            if row["pattern"] not in files:
                files[row["pattern"]] = [
                    os.path.abspath(os.path.join(sim_dir, path))
                    for path in question.files(row["pattern"]) or []
                ]
        return {
            "sim_dir": os.path.abspath(sim_dir),
            "years": years,
            "fvcom": bool(details.get("fvcom")),
            # shown as shifted, as matchup shifts them (see oceanval.time_stamps)
            "time_shift": details.get("time_shift") or 0,
            "rows": rows,
            "files": files,
        }

    def _drop_model_check(self):
        """Stop the check of the dates of the model output, if there is one,
        and remove its files, in a thread, as that can take a moment. Called
        with the lock held."""
        check, self.model_check = self.model_check, None
        if check is not None:
            # not a daemon, so its files are removed before oceanval ends
            threading.Thread(target=check.remove, name="oceanval-model-check-remove").start()

    def _new_model_check(self, question):
        """A check of the dates of the files question lists, in place of any
        earlier one, to be started once the lock is let go. It goes on while
        the matchups are asked about, and through the steps that follow them
        until matchup is answered (see _yes). Called with the lock held."""
        self._drop_model_check()
        self.model_check = model_check.ModelCheck(
            question.id, self._model_check_request(question), self._notify
        )
        return self.model_check

    def _check_for(self, question):
        """The check of the dates of the files question lists, or None.
        Called with the lock held."""
        check = self.model_check
        if question is None or check is None or check.question != question.id:
            return None
        return check

    def model_check_files(self, variable, group):
        """The files of variable the check of the dates found in the years
        of its group numbered group (see model_check.year_groups), relative
        to the simulation's directory, or None."""
        with self._lock:
            check = self._check_for(self._held_matchups())
        return check.files_of(variable, group) if check is not None else None

    def _model_check_state(self):
        """What the matchups page shows of the check of the dates of the
        files, or None. Called with the lock held."""
        check = self._check_for(self._held_matchups())
        if check is None:
            return None
        return dict(check.state(), number=check.number)

    def _yes(self, settings, question=None):
        """Yes, the matchups are right, as answered at a terminal, with
        settings chosen along with it, the point_time_res step's choice, what
        the output step (and the report options') chose: out_dir, overwrite
        and cores, and, if the dates of the files question lists have been
        checked, where the check saved their times, for matchup to use. A
        check still going is stopped, as matchup reads the times itself."""
        if self.point_time_res_choice is not None:
            settings = dict(settings, point_time_res=self.point_time_res_choice)
        with self._lock:
            if self._run_output is not None:
                settings = dict(settings, **self._run_output)
            check = self._check_for(question)
        if check is not None and check.finished and check.index:
            settings = dict(settings, times_index=check.index)
        elif check is not None:
            check.stop()
        if not settings:
            return "y"
        return ANSWER_MARKER + json.dumps({"answer": "y", "settings": settings})

    def _point_sources(self):
        """Where the script writes each point dataset matchup names
        variable/source: ("recipe", (variable, recipe)) for a recipe, or
        ("own", index) for data of the user's own. Called with the lock held."""
        found = {}
        if self._script_writer is None:
            return found
        selection = self._script_writer[1][1]
        for variable, recipe in selection or ():
            info = units.recipe_info(variable, recipe, self.cwd)
            if info and info.get("point"):
                found[time_res.key(variable, info["source"])] = ("recipe", (variable, recipe))
        for index, entry in enumerate(self.own_data["point"]):
            found[time_res.key(entry.get("name"), entry.get("source"))] = ("own", index)
        return found

    def _keep_point_time_res(self, choice, rows):
        """Write the point_time_res step's choice into what the script is
        written from, so that it says what was run. Called with the lock
        held."""
        if self._script_writer is None:
            return
        default, datasets = time_res.changes(choice, rows)
        write, result = self._script_writer
        result = list(result)
        if default is not None:
            result[2] = dict(result[2] or {}, point_time_res=list(default))
        point_options = {key: dict(value) for key, value in (result[3] or {}).items()}
        sources = self._point_sources()
        for name, value in datasets.items():
            where = sources.get(name)
            if where is None:
                continue
            if where[0] == "recipe":
                point_options[where[1]] = dict(point_options.get(where[1], {}), point_time_res=list(value))
            else:
                self.own_data["point"][where[1]] = dict(
                    self.own_data["point"][where[1]], point_time_res=list(value)
                )
        result[3] = point_options
        self._script_writer = (write, tuple(result))

    def _keep_time_shift(self, choice):
        """Put the answer to whether to shift the model's times into what
        the script is written from, so that it says what was run and does
        not ask again. It is written once the matchups are confirmed: until
        then, Back goes to the units, and the run started after them asks
        again. Called with the lock held."""
        if self._script_writer is None:
            return
        write, result = self._script_writer
        result = list(result)
        result[2] = dict(result[2] or {}, time_shift=choice)
        self._script_writer = (write, tuple(result))

    def point_time_res(self, form):
        """Take the point_time_res step's choice, for the point datasets
        matched by day against output coarser than daily. Then where the
        matchups are saved is chosen (and, to validate too, the report
        options), or matchup carries on with it. Returns the HTTP status and
        the reply."""
        with self._lock:
            question = self._held_matchups()
            if self.view != "point_time_res" or question is None:
                return 409, {"ok": False, "error": "This step is over."}
            rows = question.time_res_rows
            try:
                choice, kept = point_time_res_choice(form, rows)
            except ValueError as error:
                return 400, {"ok": False, "error": str(error)}
            self.point_time_res_form = kept
            self.point_time_res_choice = choice
            self._keep_point_time_res(choice, rows)
            writer = self._script_writer
            if self._output_first():
                self._show_output(question)
                return 200, {"ok": True}
            self.view = "running"
            self.question = None
            self._notify()
        if writer is not None:
            write, choices = writer
            try:
                write(*choices)
            except OSError as error:
                self.console.write(
                    f"The script could not be written again with the new point_time_res: {error}\n"
                )
        self.console.write("y\n")
        question.respond(self._yes({}, question))
        return 200, {"ok": True}

    def _checked_report(self, form):
        """The validate() arguments the boxes of the report options step
        give, the cores matchup is to use and its other arguments asked
        there (see check_matchup_boxes), and the errors in them, as (report,
        cores, matchup, errors). Before anything is matched up, the spatial
        subset is matchup's, not validate's. Called without the lock held."""
        with self._lock:
            self.validate_form = _form(form, default_validate_form())
            form = dict(self.validate_form)
        errors = {}
        matchups = self._report_matchups()
        form, variables = choose_matchups(form, matchups, errors)
        if matchups is None:
            # the options for gridded matchups are only asked for with them
            if self._report_gridded() is False:
                for name in GRIDDED_ONLY:
                    form[name] = default_validate_form()[name]
            # and depth bins only with point matchups through the water column
            if self._report_vertical() is False:
                form["depth_bins"] = default_depth_boxes()
        # outside the lock: reading a regions file can take a moment
        report, report_errors = check_report(form, self.cwd)
        errors.update(report_errors)
        # only before anything is matched up
        cores, matchup = None, {}
        with self._lock:
            matching = self._held_matchups() is not None
        if matching:
            cores, error = check_cores(form["cores"])
            if error is not None:
                errors["cores"] = error
            # the subset only applies to the matchups, which then hold nothing
            # outside it, so validate is not given it too
            report.pop("lon_lim", None)
            report.pop("lat_lim", None)
            matchup, matchup_errors = check_matchup_boxes(form)
            errors.update(matchup_errors)
            matchup["domain_mask"] = check_domain_mask(form, self.cwd, errors)
        if variables is not None and not errors:
            # the earlier matchups chosen for the report are not listed here,
            # so stay in it whatever is unticked
            report["variables"] = variables + self._earlier_in_report(matchups)
        if self.previous is not True:
            report["previous"] = self.previous
        return report, cores, matchup, errors

    def _earlier_in_report(self, listed):
        """The "variable/source" of the matchups there already, other than
        those listed in the report options (this run's), that the report has
        as the simulation step chose (validate's previous). Called without
        the lock held."""
        if self.previous is False or not listed:
            return []
        directory = self.results_dir or self.out_dir
        mine = {matchup["key"] for matchup in listed}
        return list(dict.fromkeys(
            matchup["key"]
            for matchup in (selection.on_disk(directory) if directory else [])
            if matchup["key"] not in mine
            and selection.picks(self.previous, matchup["variable"], matchup["source"])
        ))

    def save_script(self, form):
        """Stop at the report options step, before anything is matched up,
        and keep the script, with the report options as they are in the
        boxes, to be run later. Returns the HTTP status and the reply for
        the page."""
        with self._lock:
            if self.view != "report_options" or self._held_matchups() is None:
                return 409, {"ok": False, "error": "This step is over."}
        report, cores, matchup, errors = self._checked_report(form)
        if errors:
            return 400, {"ok": False, "errors": errors}
        with self._lock:
            if self.view != "report_options" or self._held_matchups() is None:
                return 409, {"ok": False, "error": "This step is over."}
            self.view = "running"
            self.question = None
            self.matchup_script_saved = True
            self._keep_in_script(cores=cores, **matchup)
            writer = self._script_writer
            self._notify()
        self._write_script(writer, report=report)
        self.stop()
        return 200, {"ok": True}

    def report(self, form):
        """Take the report options of a matchup and validate run, from the
        boxes of the step after the matchups are checked. They are written
        into the script, and then matchup carries on - or, if it has run
        already, the report is built. Returns the HTTP status and the reply
        for the page."""
        with self._lock:
            if self.view != "report_options":
                return 409, {"ok": False, "error": "This step is over."}
        report, cores, matchup, errors = self._checked_report(form)
        if errors:
            return 400, {"ok": False, "errors": errors}
        with self._lock:
            if self.view != "report_options":
                return 409, {"ok": False, "error": "This step is over."}
        return self._match_up_and_validate("report_options", report, cores, matchup)

    def _match_up_and_validate(self, view, report, cores, matchup):
        """Carry on from view (the report options) with the report options
        (see _checked_report): matchup carries on and builds the interim
        report - or, if it has run already, the report is built. Returns the HTTP status and
        the reply for the page."""
        with self._lock:
            if self.view != view:
                return 409, {"ok": False, "error": "This step is over."}
            # so that a second click does nothing more
            self.view = "running"
            question = self._held_matchups()
            self.question = None
            # the report goes beside the matchups, as the output step said
            directory = self.results_dir or self.out_dir
            self.report_arguments = {
                "data_dir": directory,
                "out_dir": directory,
                **report,
            }
            arguments = dict(self.report_arguments)
            # matchup's cores, subset and missing values, asked for here,
            # unless it has run already
            if cores is not None:
                if self._run_output is not None:
                    self._run_output.update(cores=cores, **matchup)
                self._keep_in_script(cores=cores, **matchup)
            writer = self._script_writer
            self._notify()
        self._write_script(writer, report=report)
        if question is not None:
            # matchup builds an interim report with the report's options as
            # it goes, which the page links to (see oceanval.live)
            status_path = os.path.join(directory, live.FOLDER, "status.json")
            # an earlier run's, which would be shown until matchup clears it
            with contextlib.suppress(OSError):
                os.remove(status_path)
            interim = {"status_path": status_path, "status": None}
            with self._lock:
                self.interim = interim
                self._notify()
            threading.Thread(
                target=self._follow_interim,
                args=(interim,),
                name="oceanval-interim",
                daemon=True,
            ).start()
            options = {
                name: value for name, value in report.items() if name not in live.NOT_INTERIM
            }
            # yes, the matchups are right, as answered at a terminal, with
            # the report's options along with it
            self.console.write("y\n")
            question.respond(self._yes({"live_validation": options}, question))
        else:
            # matchup has run already, without asking about the matchups
            self.start_run(
                ["validate", json.dumps(arguments)], _validate_call(arguments, self.cwd)
            )
        return 200, {"ok": True}

    def matchups_found(self, data_dir):
        """What the validate step says about the matchups as their directory
        is typed in: status "found", with the matchups validate() can make
        a page for (as selection.on_disk gives them), or "missing" (no such
        directory), "none" (no matchups in it) or "unfinished" (matchup did
        not finish), with the error that says so. Empty is the directory
        worked in, as for validate()."""
        path = _path(data_dir.strip() or ".", self.cwd)
        if not os.path.isdir(path):
            return {"status": "missing", "error": "There is no directory at this path.", "matchups": []}
        try:
            oceanval._check_matchups(path)
        except ValueError as error:
            status = "unfinished" if "did not finish" in str(error) else "none"
            return {"status": status, "error": str(error), "matchups": []}
        return {"status": "found", "error": None, "matchups": selection.on_disk(path)}

    def probe(self, simdir, ndown, out, exclude="", require="", out_dir=""):
        """What the simulation step says about the simulation as it is typed
        in: whether the directory is there, how many output files are ndown
        directories below it that pass the file filters (exclude and
        require, as words separated by spaces) - and how many there are
        without them - and the years their names cover. Also whether out_dir, where the
        matchups are saved, is a directory, and the matchups it has already
        (see existing_matchups)."""
        filters = {
            "exclude": exclude.split() or None,
            "require": require.split() or None,
        }
        filtered = bool(filters["exclude"] or filters["require"])
        found = {
            "directory": "empty",
            "ndown": None,
            "files": None,
            "all_files": None,
            "years": None,
            "layout": None,
            "file": False,
        }
        found["out_exists"] = bool(out.strip()) and os.path.isfile(
            _path(out.strip(), self.cwd)
        )
        results = _path(out_dir.strip() or ".", self.cwd)
        found["out_dir"] = (
            "file"
            if os.path.isfile(results)
            else "found" if os.path.isdir(results) else "missing"
        )
        with self._lock:
            output_step = self.view == "output"
            made = set(self.output_made or ())
            report = self.action == "matchup_validate"
        # in the output step, those it asks about, each with whether this run makes it
        found["out_dir_matchups"] = (
            matchups_asked_about(results, made, report) if output_step else existing_matchups(results)
        )
        found["out_dir_has_matchups"] = bool(
            found["out_dir_matchups"] if not output_step else existing_matchups(results)
        )
        # whose matchups, of the models tried before, nothing is asked about (see check_setup)
        found["out_dir_is_demo"] = self.demo is not None and results == os.path.join(
            self.cwd, DEMO_FOLDER
        )
        found["out_dir_has_analysis"] = os.path.isdir(os.path.join(results, analysis.FOLDER))
        found["out_dir_has_comparison"] = os.path.isdir(os.path.join(results, sim_compare.FOLDER))
        if not simdir.strip():
            return found
        path = _path(simdir.strip(), self.cwd)
        if self.action == VIEWER and os.path.isfile(path):
            # one file, used as it is: no directories down, not filtered
            found.update(directory="found", file=True, files=1, all_files=1)
            return found
        if not os.path.isdir(path):
            found["directory"] = "missing"
            return found
        found["directory"] = "found"
        # the layout of the folders (whatever depth is typed), which tells the depth
        found["layout"] = folder_layout(path)
        try:
            depth = int(ndown)
            if depth < 0:
                raise ValueError
        except ValueError:
            depth = None
        found["ndown"] = depth
        if depth is not None:
            paths = simulation_paths(path, depth, **filters)
            found["files"] = len(paths)
            found["all_files"] = (
                len(simulation_paths(path, depth)) if filtered else len(paths)
            )
            if paths:
                years = simulation_years(path, paths)
                found["years"] = list(years) if years else None
                if self.view == "setup" and self.action not in (ANALYZE, VIEWER):
                    # the years the recipes window offers, read from the files' times
                    key = _simulation({"simdir": path, "ndown": depth, **filters})
                    found["output_key"] = _output_key(key)
                    self._read_output_years(key, paths)
        return found

    # ---- `cdo sinfo` on the simulation's files ----

    def _start_cdo_check(self, simulations):
        """Run `cdo sinfo` on every output file of simulations (dicts with simdir,
        ndown and, if any, exclude and require), in the background, unless that
        is being done or done already for them. What was checked for others is
        stopped and forgotten. A file it segfaults on blocks the window (see
        _cdo_fault). Called with the lock held."""
        key = tuple(_simulation(simulation) for simulation in simulations)
        if not cdo_check.ENABLED or self._cdo_key == key:
            return
        self._drop_cdo_check()
        self._cdo_key = key
        number = self._cdo_number
        threading.Thread(
            target=self._cdo_run, args=(number, list(simulations)), name="oceanval-cdo-check", daemon=True
        ).start()

    def _cdo_run(self, number, simulations):
        """Find the files and start their check (see _start_cdo_check)."""
        from oceanval.viewer_catalogue import viewer_paths

        paths = []
        try:
            for simulation in simulations:
                paths += viewer_paths(
                    simulation["simdir"],
                    simulation["ndown"],
                    simulation.get("exclude") or None,
                    simulation.get("require") or None,
                )
        except Exception:
            return
        if not paths:
            return
        check = cdo_check.Check(
            paths,
            on_fault=lambda path: self._cdo_fault(number),
            done=lambda finished: self._cdo_fault(number) if finished.faulty else None,
        )
        with self._lock:
            if number != self._cdo_number or self.closed.is_set():
                return
            self._cdo_check = check
            self._notify()
        check.start()

    def _cdo_fault(self, number):
        """CDO segfaulted on a file: from here nothing can be done with the
        simulation. The run going, if any, is stopped, and the page says what
        is wrong and offers only to start again. Called again when every file
        has been looked at, to say how many."""
        with self._lock:
            check = self._cdo_check
            if number != self._cdo_number or check is None or self.closed.is_set():
                return
            self._cdo_failed = check.message()
            run = self.run if self.status in ("starting", "running") else None
            # the recipes window makes no state requests: it is left (see heartbeat)
            page = self.recipes_page if self.view in ("preparing", "recipes") else None
            self._notify()
        if run is not None:
            run.stop()
        if page is not None:
            page.cancel()

    def _drop_cdo_check(self):
        """Stop the check of the files, if there is one, and forget what it
        found. Called with the lock held."""
        check, self._cdo_check = self._cdo_check, None
        self._cdo_key = None
        self._cdo_failed = None
        self._cdo_number += 1
        if check is not None:
            check.cancel()

    def _cdo_check_state(self):
        """What the page is told of the check of the files with CDO: whether it
        is going, and, once a file has crashed it, what to tell the user. The
        page then allows nothing but starting again."""
        check = self._cdo_check
        if check is None:
            return None
        return {
            "status": "done" if check.finished else "checking",
            "total": check.total,
            "faulty": check.faulty,
            "message": self._cdo_failed,
            "link": cdo_check.LINK,
        }

    # ---- the years of the simulation's output ----

    def _read_output_years(self, key, paths=None):
        """Start reading the years the output of the simulation key (see
        _simulation) covers from its files' times (see oceanval.output_years),
        unless that is being done or done already. What was read for another
        simulation is stopped and forgotten. paths are its files, found here
        if not given. Returns the reading, as {"key", "reading", "result",
        "done"}: result is None until done is set."""
        with self._lock:
            current = self._output_years
            if current is not None and current["key"] == key:
                return current
        if paths is None:
            paths = simulation_paths(
                key[0], key[1], exclude=list(key[2]) or None, require=list(key[3]) or None
            )
        entry = {"key": key, "result": None, "done": threading.Event()}

        def done(result):
            with self._lock:
                if self._output_years is entry:
                    entry["result"] = result
                    self._notify()
            entry["done"].set()

        entry["reading"] = output_years.Reading(key, paths, done)
        with self._lock:
            current = self._output_years
            if current is not None and current["key"] == key:
                # started by another request meanwhile
                return current
            self._output_years = entry
        if current is not None:
            current["reading"].cancel()
            # nothing waits on it any more
            current["done"].set()
        entry["reading"].start()
        return entry

    def _output_years_state(self):
        """What the page is told of the reading of the output's years: the
        simulation it is for, as the probe's output_key, whether it is done,
        and the years, if any were found."""
        entry = self._output_years
        if entry is None:
            return None
        result = entry["result"]
        return {
            "key": _output_key(entry["key"]),
            "status": "reading" if result is None else "done",
            "years": result.get("years") if result else None,
        }

    def output_years_of(self, key, timeout=OUTPUT_YEARS_WAIT):
        """The first and last year of the output of the simulation key (see
        _simulation), read from its files' times, waiting up to timeout
        seconds for them; None if they could not be found in time."""
        entry = self._read_output_years(key)
        entry["done"].wait(timeout)
        result = entry["result"]
        years = result.get("years") if result else None
        return list(years) if years else None

    def sample_files(self, simdir, ndown, avoid=""):
        """The netCDF files in one directory of the simulation, chosen at
        random from those ndown directories below simdir that hold any, for
        the page to show what the file names look like - the names the file
        filters are made of. avoid is a directory not to choose again, if
        there is another.

        Every file is listed, whatever the file filters say, and none is left
        out for being a restart file, so the page can show what there is to
        filter.
        """
        found = {
            "directory": "empty",
            "ndown": None,
            "path": None,
            "directories": 0,
            "files": [],
            "more": 0,
        }
        if not simdir.strip():
            return found
        path = _path(simdir.strip(), self.cwd)
        if not os.path.isdir(path):
            found["directory"] = "missing"
            return found
        found["directory"] = "found"
        try:
            depth = int(ndown)
            if depth < 0:
                raise ValueError
        except ValueError:
            return found
        found["ndown"] = depth
        pattern = os.path.join(path, *(["*"] * depth), "*.nc")
        directories = sorted({os.path.dirname(file) for file in glob.glob(pattern)})
        found["directories"] = len(directories)
        if not directories:
            return found
        avoided = _path(avoid, self.cwd) if avoid.strip() else None
        chosen = random.choice(
            [directory for directory in directories if directory != avoided]
            or directories
        )
        files = sorted(
            (name for name in os.listdir(chosen) if name.endswith(".nc")),
            key=lambda name: (name.lower(), name),
        )
        found["path"] = _shown(chosen, self.cwd)
        found["files"] = files[:_MOST_FILES]
        found["more"] = max(0, len(files) - _MOST_FILES)
        return found

    def browse(self, path):
        """What one directory holds, for the page's folder browser and the
        folders it suggests as a path is typed: its folders, how many netCDF
        files are directly in it, and whether it has matchups.

        path is as typed, relative to the working directory. If it is not a
        directory, the nearest one above it is listed instead, and exact is
        False. The listing is of the machine oceanval runs on, which the
        browser need not be on, so it cannot use a folder picker of its own.
        """
        requested = _path(path.strip() or ".", self.cwd)
        directory = requested
        while not os.path.isdir(directory) and os.path.dirname(directory) != directory:
            directory = os.path.dirname(directory)
        parent = os.path.dirname(directory)
        found = {
            "path": directory,
            "shown": _shown(directory, self.cwd),
            "parent": None if parent == directory else parent,
            "exact": directory == requested,
            "folders": [],
            "more": 0,
            "nc_files": 0,
            "csv_files": 0,
            "example": None,
            "has_matchups": False,
            "error": None,
            "places": [
                {"label": "Working directory", "path": self.cwd},
                {"label": "Home", "path": os.path.expanduser("~")},
            ],
        }
        folders, files, csv_files = [], [], 0
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir():
                            if not entry.name.startswith("."):
                                folders.append(entry.name)
                        elif entry.name.endswith(".nc"):
                            files.append(entry.name)
                        elif entry.name.endswith(".csv"):
                            csv_files += 1
                    except OSError:
                        # e.g. a link to something that has gone
                        continue
        except PermissionError:
            found["error"] = "You do not have permission to open this directory."
            return found
        except OSError as error:
            found["error"] = (
                f"This directory cannot be opened: {error.strerror or error}."
            )
            return found
        folders.sort(key=lambda name: (name.lower(), name))
        found["folders"] = [
            {"name": name, "path": os.path.join(directory, name)}
            for name in folders[:_MOST_FOLDERS]
        ]
        found["more"] = max(0, len(folders) - _MOST_FOLDERS)
        found["nc_files"] = len(files)
        found["csv_files"] = csv_files
        found["example"] = min(files) if files else None
        found["has_matchups"] = "oceanval_matchups" in folders
        # a validation, for the compare step
        found["has_results"] = "oceanval_results" in folders
        return found

    # ---- the run ----

    def start_run(self, args, label):
        run = Run(
            args,
            self.cwd,
            self.console,
            self._put_question,
            self._run_finished,
            told=lambda news: self._matchup_news(run, news),
        )
        with self._lock:
            if self.closed.is_set() or self._cdo_failed is not None:
                # nothing is run on files CDO crashes on
                return
            self.run = run
            self.run_label = label
            self.status = "running"
            self.returncode = None
            self.question = None
            script = args[:1] in (["script"], ["matchup"])
            if script:
                # a report run after it keeps the interim report's link
                self.interim = None
            if args[:1] != ["validate"]:
                # the report run after a matchup keeps its grid of the
                # matchups, and the notice of the servers that were down
                self.matchup_progress = None
                self.server_trouble = {}
            else:
                # and its clock goes on
                self._matchups_clock_resume()
            self.analysis_progress = None
            self.identifying = script
            self.matchups_rejected = False
            self.matchup_script_saved = False
            self.script_path = (
                os.path.abspath(os.path.join(self.cwd, args[1]))
                if script and len(args) > 1
                else None
            )
            if args[:1] == ["matchup"]:
                # chosen once the matchups are checked
                self.report_arguments = None
            if script:
                # chosen in the output step, once the matchups are checked
                self._run_output = None
            interim = None
            if args[:1] in (["analyze"], ["simcompare"]):
                # its report is built a variable at a time, which the page
                # links to as it goes (see oceanval.analysis_report)
                self.interim = interim = self._analysis_interim(args[0])
            if script:
                self.point_time_res_choice = None
                self.point_time_res_form = None
                self._drop_model_check()
            self.view = "running"
            self._notify()
        if interim is not None:
            threading.Thread(
                target=self._follow_interim,
                args=(interim,),
                name="oceanval-interim",
                daemon=True,
            ).start()
        # set apart from what create_recipes printed before it (the command
        # that runs is not shown: it is of no interest to the user)
        if not self.console.empty():
            self.console.write("\n")
        try:
            run.start()
        except OSError as error:
            self.console.write(f"The run could not be started: {error}\n")
            run.returncode = -1
            self._run_finished(run)
            return
        with self._lock:
            # left with Back as it started
            left = run is not self.run
        if left:
            run.stop()

    def _run_finished(self, run):
        validate = None
        with self._lock:
            if run is not self.run:
                return
            self._matchups_clock_stop()
            self._analysis_clock_stop()
            if run.stopped:
                status = "stopped"
            else:
                status = "finished" if run.returncode == 0 else "failed"
            self.question = None
            self.identifying = False
            # matchup has read the times the check saved, if it was to
            self._drop_model_check()
            # a matchup and validate run builds the report once its script
            # has run, with the options chosen after the matchups were checked
            report_next = (
                status == "finished"
                and run.args[:1] == ["matchup"]
                and not self.matchup_script_saved
                and not self.closed.is_set()
            )
            if report_next and self.report_arguments is not None:
                validate = dict(self.report_arguments)
            else:
                self.status = status
                self.returncode = run.returncode
                if report_next:
                    # matchup did not ask whether the matchups are right, so
                    # the report options are asked for now
                    self.view = "report_options"
                else:
                    self.view = "script_saved" if self.matchup_script_saved else "finished"
            self._notify()
        if validate is not None:
            self.start_run(
                ["validate", json.dumps(validate)], _validate_call(validate, self.cwd)
            )
            return
        if report_next:
            return
        self.console.note(
            "\nOceanVal: the run has "
            + {"stopped": "been stopped", "finished": "finished", "failed": "failed"}[
                self.status
            ]
            + ". Start again or quit in the window, or press Ctrl+C to quit.\n"
        )

    def stop(self):
        with self._lock:
            run = self.run if self.view == "running" else None
        if run is None:
            return False
        run.stop()
        return True

    # ---- questions ----

    def _put_question(self, text, choices, respond, details=None):
        check = None
        with self._lock:
            self.question = Question(text, choices, respond, details)
            if self.question.matchups:
                self.identifying = False
                # the dates of the files are read while the matchups are checked
                check = self._new_model_check(self.question)
            self._notify()
        if check is not None:
            check.start()
        # as a terminal shows a question: at the end of the output, which the
        # answer then follows
        self.console.write(text)
        self.console.note("(answer this in the OceanVal window) ")

    def _ask_in_window(self, text, choices, details=None):
        """Ask a question in the page, for prompts.answered_by."""
        answers = queue.Queue()
        with self._lock:
            number = self._reset_count
        self._put_question(text, choices, answers.put, details)
        while True:
            try:
                return answers.get(timeout=0.25)
            except queue.Empty:
                if self.closed.is_set():
                    raise EOFError(
                        "OceanVal was closed before the question was answered"
                    )
                if number != self._reset_count:
                    raise EOFError("The window was restarted before the question was answered")

    def answer(self, number, text):
        """Answer the question numbered number, if it is still being asked.
        No to whether the matchups are right stops the run instead, as
        there is then nothing for it to do. Yes, in a matchup and validate
        run, is held back while the report options are chosen (see report),
        so that nothing is matched up before then."""
        # one line, as typed at a terminal, which cannot pose as an answer
        # with settings (see app_child.ANSWER_MARKER)
        text = str(text).replace("\r", " ").replace("\n", " ").replace("\x1e", "")
        with self._lock:
            question = self.question
            if (
                question is None
                or question.id != number
                or self.view in ("report_options", "point_time_res", "output")
            ):
                return False
            answer = text.strip().lower()
            if question.time_shift and answer in ("y", "n"):
                # shown so again if it is asked again, after Back
                self.time_shift_form = {"mode": "shift" if answer == "y" else "keep"}
                self._keep_time_shift(time_stamps.SUGGESTED if answer == "y" else 0)
            if question.matchups and answer == "y" and question.time_res_rows:
                # the point datasets to check are asked about first
                self.view = "point_time_res"
                self._notify()
                return True
            if question.matchups and answer == "y" and self._output_first():
                # where the matchups are saved, and then, to validate too, the report options
                self._show_output(question)
                return True
            self.question = None
            writer = None
            if question.matchups and answer in ("y", "save") and self._script_writer is not None:
                # the answer to whether to shift the model's times, which no
                # step after this one writes into the script
                if "time_shift" in (self._script_writer[1][2] or {}):
                    writer = self._script_writer
            rejected = question.matchups and answer == "n"
            save_script = question.matchups and answer == "save"
            if rejected:
                self.matchups_rejected = True
            if save_script:
                self.matchup_script_saved = True
            self._notify()
        self.console.write(text + "\n")
        if writer is not None:
            write, choices = writer
            try:
                write(*choices)
            except OSError as error:
                self.console.write(
                    f"The script could not be written again with the new time_shift: {error}\n"
                )
        if (rejected or save_script) and self.stop():
            return True
        if question.matchups and answer == "y":
            # with where the check of the dates saved the times of the files
            question.respond(self._yes({}, question))
        else:
            question.respond(text)
        return True

    def question_files(self, number, pattern):
        """The files the question numbered number lists for one file
        pattern, or None if it is no longer being asked."""
        with self._lock:
            question = self.question
        if question is None or question.id != number:
            return None
        return question.files(pattern)

    # ---- the server ----

    def start(self, port=0):
        """Serve the page, and return its link."""
        handler = type("Handler", (_Handler,), {"app": self})
        self._server = recipes_gui._Server(("127.0.0.1", port), handler)
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/?token={self.token}"
        threading.Thread(
            target=self._server.serve_forever, name="oceanval-window", daemon=True
        ).start()
        threading.Thread(target=self._watch, name="oceanval-watch", daemon=True).start()
        return self.url

    def quit(self, reason=None):
        """Stop whatever is running, and let main() return."""
        with self._lock:
            if self.closed.is_set():
                return
            self.closed_reason = reason
            self.closed.set()
            self._demo_cancel.set()
            run, page = self.run, self.recipes_page
            self._cancel_register_check()
            self._drop_model_check()
            self._drop_analyze_check()
            self._drop_cdo_check()
            reading, self._output_years = self._output_years, None
            viewed = self._viewer_drop()
            self._notify()
        if viewed is not None:
            viewed.close()
        if reading is not None:
            reading["reading"].cancel()
        if run is not None:
            run.stop()
        if page is not None:
            page.cancel()
            page.finish()

    def close(self):
        self.quit()
        if self.run is not None:
            self.run.join(timeout=6)
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def _year_groups(row, years):
    """A row of matchup's question's details: its datasets grouped by the
    years they are matched up for (see model_check.year_groups), which the
    matchups page shows a row of the table for each of. A matchup that does
    not send them has one, for the run's years."""
    groups = row.get("groups")
    if isinstance(groups, list) and groups:
        return groups
    observations = [str(source) for source in row.get("observations") or []]
    return [
        {
            "years": list(years),
            "sources": observations,
            # point datasets are listed as "<source> (point)"
            "gridded": any(not source.endswith("(point)") for source in observations),
        }
    ]


def _number(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


class _Handler(recipes_gui._Handler):
    app = None  # set on the subclass each App makes

    def _connected(self):
        """Whether the browser is still waiting for the reply: the request
        has been read, so what can be read from the connection is its end."""
        try:
            if not select.select([self.connection], [], [], 0)[0]:
                return True
            return self.connection.recv(1, socket.MSG_PEEK) != b""
        except OSError:
            return False

    def _redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _report_file(self, path):
        """A file of the interim report, the full report, an analysis's
        report or a comparison: path is /interim/<token>/<page>,
        /report/<token>/<page>, /analysis/<token>/<page> or
        /comparison/<token>/<page>. The token is in the path, not the query,
        so that the reports' own relative links carry it from page to page."""
        which, _, rest = path[1:].partition("/")
        token, _, page = rest.partition("/")
        if not self.app.authorised(urllib.parse.unquote(token)):
            self._reply(
                403,
                "This is not the link oceanval printed.",
                "text/plain; charset=utf-8",
            )
            return
        root = self.app.report_root(which)
        found = (
            None
            if root is None
            else _served_file(root, page, self.app.report_folders(which))
        )
        data = None
        if found is not None:
            with contextlib.suppress(OSError):
                with open(found, "rb") as file:
                    data = file.read()
        if data is None:
            self._reply(404, "Not found", "text/plain; charset=utf-8")
            return
        content_type = mimetypes.guess_type(found)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in (
            "application/javascript",
            "application/json",
            "image/svg+xml",
        ):
            content_type += "; charset=utf-8"
        self._reply(200, data, content_type)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path.startswith(("/interim/", "/report/", "/analysis/", "/simcompare/", "/comparison/")):
            self._report_file(url.path)
            return
        query = {
            key: values[0] for key, values in urllib.parse.parse_qs(url.query).items()
        }
        if url.path not in (
            "/",
            "/recipes/",
            "/api/state",
            "/api/heartbeat",
            "/api/probe",
            "/api/matchups",
            "/api/browse",
            "/api/files",
            "/api/question_files",
            "/api/model_check_files",
            "/api/analyze_check_files",
            "/api/simcompare_check_files",
            "/api/viewer/catalogue",
            "/api/viewer/plot",
            "/api/viewer/limits",
            "/api/viewer/missing",
            "/bokeh.min.js",
        ):
            self._reply(404, "Not found", "text/plain; charset=utf-8")
        elif not self.app.authorised(query.get("token", "")):
            self._reply(
                403,
                "This is not the link oceanval printed.",
                "text/plain; charset=utf-8",
            )
        elif url.path == "/":
            self._reply(200, self.app.html(), "text/html; charset=utf-8")
        elif url.path == "/recipes/":
            page = self.app.recipes_page
            if page is None:
                # the recipes step is over
                self._redirect("/?token=" + urllib.parse.quote(self.app.token))
            else:
                self._reply(200, page.html(), "text/html; charset=utf-8")
        elif url.path == "/api/heartbeat":
            self._reply_json(
                200,
                {
                    "closed": self.app.heartbeat(
                        query.get("page"), connected=self._connected
                    ),
                    # CDO crashed on the simulation's files: the main page says so
                    "blocked": self.app._cdo_failed is not None,
                },
            )
        elif url.path == "/api/state":
            self.app.touch(query.get("page"))
            self._reply_json(
                200,
                self.app.state(
                    after=_number(query.get("after")) or 0,
                    epoch=_number(query.get("epoch")),
                    version=_number(query.get("version")),
                    page=query.get("page"),
                    connected=self._connected,
                ),
            )
        elif url.path == "/bokeh.min.js":
            # the library the viewer's maps are drawn by, from the bokeh installed
            with open(viewer_app.bokeh_js(), "rb") as file:
                self._reply(200, file.read(), "text/javascript; charset=utf-8")
        elif url.path == "/api/viewer/catalogue":
            self._reply_json(200, self.app.viewer_summary())
        elif url.path == "/api/viewer/plot":
            self._reply_json(200, self.app.viewer_plot(query))
        elif url.path == "/api/viewer/limits":
            self._reply_json(200, self.app.viewer_limits(query))
        elif url.path == "/api/viewer/missing":
            self._reply_json(200, self.app.viewer_missing_guess(query))
        elif url.path == "/api/browse":
            self._reply_json(200, self.app.browse(query.get("path", "")))
        elif url.path == "/api/matchups":
            self._reply_json(200, self.app.matchups_found(query.get("data_dir", "")))
        elif url.path == "/api/files":
            self._reply_json(
                200,
                self.app.sample_files(
                    query.get("simdir", ""),
                    query.get("ndown", ""),
                    query.get("avoid", ""),
                ),
            )
        elif url.path == "/api/question_files":
            files = self.app.question_files(
                _number(query.get("id")), query.get("pattern", "")
            )
            if files is None:
                self._reply_json(
                    409, {"ok": False, "error": "That question has been answered."}
                )
            else:
                self._reply_json(200, {"ok": True, "files": files})
        elif url.path == "/api/analyze_check_files":
            files = self.app.analyze_check_files(query.get("variable", ""))
            if files is None:
                self._reply_json(409, {"ok": False, "error": "The files are not known yet."})
            else:
                self._reply_json(200, {"ok": True, "files": files})
        elif url.path == "/api/simcompare_check_files":
            files = self.app.simcompare_check_files(query.get("simulation", ""), query.get("variable", ""))
            if files is None:
                self._reply_json(409, {"ok": False, "error": "The files are not known yet."})
            else:
                self._reply_json(200, {"ok": True, "files": files})
        elif url.path == "/api/model_check_files":
            files = self.app.model_check_files(query.get("variable", ""), _number(query.get("group")))
            if files is None:
                self._reply_json(409, {"ok": False, "error": "The files are not known yet."})
            else:
                self._reply_json(200, {"ok": True, "files": files})
        else:
            self._reply_json(
                200,
                self.app.probe(
                    query.get("simdir", ""),
                    query.get("ndown", ""),
                    query.get("out", ""),
                    query.get("exclude", ""),
                    query.get("require", ""),
                    query.get("out_dir", ""),
                ),
            )

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self.app.authorised(self.headers.get("X-OceanVal-Token", "")):
            self._reply_json(403, {"ok": False, "error": "Forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._reply_json(
                400, {"ok": False, "error": "The request could not be read."}
            )
            return
        payload = payload if isinstance(payload, dict) else {}

        if path in ("/recipes/write", "/recipes/cancel"):
            self._recipes(path, payload)
            return
        app = self.app
        if path == "/api/visibility":
            app.page_visibility(
                payload.get("page"), bool(payload.get("hidden")), bool(payload.get("closing"))
            )
            self._reply_json(200, {"ok": True})
            return
        if path == "/api/setup":
            self._reply_json(*app.setup(payload.get("form")))
            return
        if path == "/api/settings":
            self._reply_json(*app.apply_settings(payload.get("settings")))
            return
        if path == "/api/data_store_check":
            self._reply_json(*app.check_data_store())
            return
        if path == "/api/validate":
            self._reply_json(*app.validate(payload.get("form")))
            return
        if path == "/api/compare":
            self._reply_json(*app.compare(payload.get("form")))
            return
        if path == "/api/analyze_variables":
            self._reply_json(*app.analyze_variables(payload.get("rows")))
            return
        if path == "/api/analyze_files":
            self._reply_json(*app.analyze_files())
            return
        if path == "/api/simcompare_sims":
            self._reply_json(*app.simcompare_sims(payload.get("form")))
            return
        if path == "/api/simcompare_variables":
            self._reply_json(*app.simcompare_variables(payload.get("rows")))
            return
        if path == "/api/simcompare_units":
            self._reply_json(*app.simcompare_units(payload.get("conversions"), payload.get("confirmed")))
            return
        if path == "/api/simcompare_files":
            self._reply_json(*app.simcompare_files())
            return
        if path == "/api/simcompare_grids":
            self._reply_json(*app.simcompare_grids(payload.get("form")))
            return
        if path == "/api/simcompare_panel":
            self._reply_json(*app.simcompare_panel(payload.get("number")))
            return
        if path == "/api/simcompare_generate":
            self._reply_json(*app.simcompare_generate(payload.get("form")))
            return
        if path == "/api/domain_mask":
            self._reply_json(*app.domain_mask_map(payload.get("path")))
            return
        if path == "/api/analyze_option":
            self._reply_json(*app.analyze_option(payload.get("kind"), payload.get("form")))
            return
        if path == "/api/analyze_run":
            self._reply_json(*app.analyze_run(form=payload.get("form")))
            return
        if path == "/api/analyze_save_script":
            self._reply_json(*app.analyze_run(save=True, form=payload.get("form")))
            return
        if path == "/api/report":
            self._reply_json(*app.report(payload.get("form")))
            return
        if path == "/api/save_script":
            self._reply_json(*app.save_script(payload.get("form")))
            return
        if path == "/api/point_time_res":
            self._reply_json(*app.point_time_res(payload.get("form")))
            return
        if path == "/api/units_continue":
            self._reply_json(
                *app.units_continue(payload.get("conversions"), payload.get("confirmed"))
            )
            return
        if path == "/api/output":
            self._reply_json(*app.output_continue(payload.get("form")))
            return
        if path == "/api/own_add":
            self._reply_json(
                *app.add_own_data(payload.get("kind"), payload.get("form"))
            )
            return
        if path == "/api/own_inspect":
            self._reply_json(*app.own_inspect(payload.get("obs_path")))
            return
        if path == "/api/own_future":
            self._reply_json(*app.own_future(payload.get("kind"), payload.get("form")))
            return
        if path == "/api/own_future_save":
            self._reply_json(*app.own_future_save(payload.get("boxes")))
            return
        if path == "/api/recipe_names":
            self._reply_json(*app.register_names(payload.get("kind"), payload.get("form")))
            return
        if path == "/api/recipe_data":
            self._reply_json(*app.register_data(payload.get("kind"), payload.get("form")))
            return
        if path == "/api/recipe_save":
            self._reply_json(*app.register_save(payload.get("kind"), payload.get("form")))
            return
        if path == "/api/recipe_remove":
            self._reply_json(
                *app.register_remove(
                    payload.get("variable"), payload.get("key"), payload.get("where")
                )
            )
            return
        if path == "/api/demo_download":
            self._reply_json(*app.demo_download(payload.get("model"), payload.get("variables")))
            return
        if path == "/api/leftovers":
            self._reply_json(*app.answer_leftovers(payload.get("action")))
            return
        if path == "/api/quit":
            try:
                self._reply_json(200, {"ok": True})
            finally:
                app.quit()
            return
        steps = {
            "/api/choose": lambda: app.choose(
                payload.get("action"), payload.get("data_dir")
            ),
            "/api/own_data": lambda: app.has_own_data(bool(payload.get("answer"))),
            "/api/own_remove": lambda: app.remove_own_data(
                payload.get("kind"), payload.get("index")
            ),
            "/api/forget_sim_dir": lambda: app.forget_sim_dir(payload.get("path")),
            "/api/demo_detail": lambda: app.demo_detail(payload.get("concise")),
            "/api/demo_continue": app.demo_continue,
            "/api/own_next": app.next_own_data,
            "/api/own_future_check": app.own_future_check,
            "/api/register_kind": lambda: app.register_kind(payload.get("kind")),
            "/api/register_done": app.register_done,
            "/api/analyze_time_shift": lambda: app.analyze_time_shift(payload.get("choice")),
            "/api/simcompare_time_shift": lambda: app.simcompare_time_shift(payload.get("choice")),
            # with what the step's boxes hold
            "/api/back": lambda: app.back(payload),
            "/api/restart_all": app.reset,
            "/api/stop": app.stop,
            "/api/refilter": lambda: app.refilter(
                payload.get("exclude"), payload.get("require")
            ),
            "/api/answer": lambda: app.answer(
                payload.get("id"), payload.get("answer", "")
            ),
        }
        if path not in steps:
            self._reply_json(404, {"ok": False, "error": "Not found"})
        elif steps[path]():
            self._reply_json(200, {"ok": True})
        else:
            self._reply_json(409, {"ok": False, "error": "That cannot be done now."})

    def _recipes(self, path, payload):
        """The create_recipes window's own requests, as its server answers
        them."""
        page = self.app.recipes_page
        if page is None:
            self._reply_json(409, {"ok": False, "error": "The recipes step is over."})
        elif path.endswith("/write"):
            status, reply = page.submit(payload)
            try:
                self._reply_json(status, reply)
            finally:
                # only now, so the reply is sent before create_recipes returns
                if reply["ok"]:
                    page.finish()
        else:
            # Back, with what the window holds, which it is shown with again
            page.cancel(payload)
            try:
                self._reply_json(200, {"ok": True})
            finally:
                page.finish()


def main(argv=None):
    """The oceanval command."""
    # OCEANVAL_TRACE_SEMAPHORES: where to record what made each semaphore, to
    # find the owner of one the resource tracker says leaked (see the module)
    from oceanval import semaphore_trace

    semaphore_trace.install_from_environment()
    parser = argparse.ArgumentParser(
        prog="oceanval",
        description=(
            "Validate an ocean model in a window in your web browser: match "
            "its output up with observations, and build the validation "
            "report. Run it in the directory to work in."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="the port to serve the window on, e.g. to forward it from a "
        f"remote machine (default: {recipes_gui.SSH_PORT} in an SSH session "
        "where no browser can be opened from here, if it is free, otherwise "
        "any free port)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="do not open a web browser on this machine: print the link to "
        "open instead (the default over SSH, where OCEANVAL_BROWSER=1 "
        "opens one anyway)",
    )
    arguments = parser.parse_args(argv)
    if arguments.no_browser:
        # the run's own process (reports it opens) reads it too
        os.environ["OCEANVAL_BROWSER"] = "0"

    # the usual port is only for the ssh -L command printed when no browser
    # can be opened, so it is the same each time. When one is opened (by VS
    # Code, say), a port of its own each run keeps an earlier run's forward
    # or tab from being reached instead
    can_open = recipes_gui._can_open_browser()
    app = App()
    try:
        try:
            url = app.start(
                recipes_gui.SSH_PORT
                if arguments.port is None and recipes_gui.over_ssh() and not can_open
                else arguments.port or 0
            )
        except OSError:
            if arguments.port is not None:
                raise
            # the usual port is taken, by another run perhaps
            url = app.start(0)
    except OSError as error:
        parser.exit(
            1,
            f"oceanval: port {arguments.port} cannot be used ({error.strerror or error}). "
            "Choose another with --port.\n",
        )
    if can_open and recipes_gui._open_browser(url):
        print(
            f"OceanVal is open in your web browser:\n  {url}\n"
            "Press Ctrl+C here to quit, or close the window.",
            flush=True,
        )
    else:
        print(
            f"Open this link in a web browser to use OceanVal:\n  {url}\n"
            f"{recipes_gui.remote_hint(app.port)}\n"
            "Press Ctrl+C here to quit, or close the window.",
            flush=True,
        )
    try:
        # waiting in steps, so Ctrl+C is not held up
        while not app.closed.wait(0.25):
            pass
        print(
            f"{app.closed_reason} OceanVal is quitting."
            if app.closed_reason
            else "OceanVal was closed from its window.",
            flush=True,
        )
    except KeyboardInterrupt:
        print("\nClosing OceanVal.", flush=True)
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
