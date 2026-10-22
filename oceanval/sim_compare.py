"""Compare simulations: compare_simulations().

Up to five simulations of the same variables are analysed with everything
analyze() can do (climatologies, phenology, long-term and seasonal series,
map changes and transects), and shown side by side in one HTML report, a
colour for each simulation.

Each simulation is analysed by the code analyze() uses, into a folder of its own:

    oceanval_simulation_comparison/report.html     a link to the report
    oceanval_simulation_comparison/results/        comparison.json, which says what
                                                   is in the results, a folder for
                                                   each simulation (named as its
                                                   name is in variable_key), and
                                                   unify, with what the simulations
                                                   share if their grids are unified
    oceanval_simulation_comparison/report/         the report's book

The simulations can be put on one grid (see oceanval.sim_unify), with a cell
missing in any of them missing in all.

The oceanval window (see oceanval.sim_compare_app) writes a script calling
compare_simulations() and runs it.
"""

import datetime
import json
import os
import warnings
import webbrowser

import nctoolkit as nc
import numpy as np
import xarray as xr

from oceanval import netcdf_lock, sim_unify
from oceanval.analysis import (
    BOTTOM,
    KINDS,
    LAYERED,
    SURFACE,
    WATER_COLUMN,
    INFO_FILE as INFO_FILE_OF_ANALYSIS,
    _remove,
    _say,
    _work_out,
    check_arguments,
    check_area,
    check_as_missing,
    first_step_of,
    locate_rows,
    thickness_data,
    variable_key,
)
from oceanval.matchall import _restores_nctoolkit_options

# the folder compare_simulations() puts everything in, in its out_dir, and what is in it
FOLDER = "oceanval_simulation_comparison"
RESULTS = "results"
REPORT = "report"
REPORT_LINK = "report.html"
UNIFY = "unify"
# the file in RESULTS that says what is in it
INFO_FILE = "comparison.json"
# the report's first page
LANDING = "000_overview"

MOST_SIMULATIONS = 5
# the colour of each simulation in the figures, in the order of the simulations:
# the Okabe-Ito colours, which can be told apart with any colour vision
COLOURS = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#D55E00"]
# the layer of a transect's vertical section (see analysis_transects)
LEVELS = "levels"

SIMULATION_KEYS = (
    "name", "sim_dir", "n_dirs_down", "exclude", "require", "time_shift", "thickness", "as_missing", "times_index"
)


def _listing(names):
    return ", ".join(repr(name) for name in names)


def check_simulations(simulations, thickness=None, time_shift=None, as_missing=None):
    """simulations, checked, as a list of

        {"name", "key", "sim_dir", "n_dirs_down", "exclude", "require",
         "time_shift", "thickness", "as_missing"}

    in order. simulations is a list of dicts, each with a "name" and a "sim_dir"
    (and "n_dirs_down", "exclude", "require", "time_shift", "thickness" and
    "as_missing", as analyze() has them), or a dict of those by name. Anything a
    simulation does not give is thickness, time_shift and as_missing here, 2
    directories down, and no filters. Raises ValueError saying what is wrong."""
    from oceanval import time_stamps

    if isinstance(simulations, dict):
        simulations = [{"name": name, **(given if isinstance(given, dict) else {"sim_dir": given})} for name, given in simulations.items()]
    if not isinstance(simulations, (list, tuple)) or not all(isinstance(x, dict) for x in simulations):
        raise ValueError(
            "simulations must be a list of dicts, each with a name and a sim_dir, "
            'e.g. [{"name": "Baseline", "sim_dir": "/data/base"}, {"name": "Warm", "sim_dir": "/data/warm"}].'
        )
    if len(simulations) < 2:
        raise ValueError("Give at least two simulations to compare.")
    if len(simulations) > MOST_SIMULATIONS:
        raise ValueError(f"At most {MOST_SIMULATIONS} simulations can be compared, and {len(simulations)} were given.")
    checked = []
    names = {}
    keys = {}
    for given in simulations:
        unknown = set(given) - set(SIMULATION_KEYS)
        if unknown:
            raise ValueError(f"A simulation has {', '.join(sorted(unknown))}, which is not something it can have.")
        name = given.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Each simulation needs a name.")
        name = name.strip()
        key = variable_key(name)
        if not key:
            raise ValueError(f"{name!r} needs a letter or a digit in it, to name its files.")
        if name in names or key in keys:
            raise ValueError(
                f"Two simulations are called {name!r}, or would name their files the same: "
                "give them names that differ by more than case and punctuation."
            )
        names[name] = True
        keys[key] = name
        sim_dir = given.get("sim_dir")
        if sim_dir is None or not str(sim_dir).strip():
            raise ValueError(f"Give the sim_dir of {name!r}, the directory its output is in.")
        sim_dir = os.path.abspath(os.path.expanduser(str(sim_dir)))
        if not os.path.isdir(sim_dir):
            raise ValueError(f"There is no directory {sim_dir}, the sim_dir of {name!r}.")
        try:
            n_dirs_down = int(given.get("n_dirs_down", 2))
            if n_dirs_down < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError(f"The n_dirs_down of {name!r} must be a whole number, 0 or more.") from None
        lists = {}
        for filtered in ("exclude", "require"):
            value = given.get(filtered)
            lists[filtered] = [value] if isinstance(value, str) else list(value or [])
        own_shift = given.get("time_shift", time_shift)
        if not time_stamps.valid(own_shift):
            raise ValueError(f"The time_shift of {name!r} must be None or a whole number of days, e.g. -1.")
        try:
            own_missing = check_as_missing(given.get("as_missing", as_missing))
        except ValueError as error:
            raise ValueError(f"{name!r}: {error}") from None
        times_index = given.get("times_index")
        if times_index is not None and not isinstance(times_index, (str, os.PathLike)):
            raise ValueError(f"The times_index of {name!r} must be the path of a file, or left out.")
        times_index = None if times_index is None else os.path.abspath(os.fspath(times_index))
        checked.append(
            {
                "name": name,
                "key": key,
                "sim_dir": sim_dir,
                "n_dirs_down": n_dirs_down,
                "exclude": lists["exclude"],
                "require": lists["require"],
                "time_shift": own_shift,
                "thickness": given.get("thickness", thickness),
                "as_missing": own_missing,
                "times_index": times_index,
            }
        )
    return checked


def check_variables(variables, names):
    """variables, as {variable name: {simulation name: model variable}},
    checked against the names of the simulations; a model variable alone, for a
    variable with the same one in every simulation, is taken to be that. Returns
    {simulation name: {variable name: model variable}}. Raises ValueError
    saying what is wrong."""
    if not isinstance(variables, dict) or not variables:
        raise ValueError(
            "variables must be a dict of the names to give the variables and, for each, the model variable "
            'holding it in each simulation, e.g. {"Temperature": {"Base": "thetao", "Warm": "votemper"}}.'
        )
    by_sim = {name: {} for name in names}
    problems = []
    for variable, given in variables.items():
        if isinstance(given, str):
            given = {name: given for name in names}
        if not isinstance(given, dict):
            problems.append(f"{variable!r} needs its model variable in each simulation, as a dict by simulation name.")
            continue
        unknown = [name for name in given if name not in names]
        if unknown:
            problems.append(f"{variable!r} has {_listing(unknown)}, which is not one of the simulations ({_listing(names)}).")
        absent = [name for name in names if not isinstance(given.get(name), str) or not given[name].strip()]
        if absent:
            problems.append(f"{variable!r} needs a model variable in {_listing(absent)}.")
        for name in names:
            if isinstance(given.get(name), str):
                by_sim[name][variable] = given[name]
    if problems:
        raise ValueError("\n".join(problems))
    return by_sim


def layers_of(row):
    """The layers monthly_means_of (or daily_climatology_of) will be asked for
    row's variable in, in order: SURFACE, BOTTOM, the water column's, and LEVELS."""
    layers = set()
    for kind in LAYERED + ("map_changes",):
        if row[kind]:
            layers.update(row[kind]["layers"])
    if row["phenology"]:
        layers.add(SURFACE)
    for line in row["transects"]["lines"] if row["transects"] else []:
        if line["climatology"] or line["trends"]:
            layers.add(SURFACE)
        if line["vertical"] or line["vertical_trends"]:
            layers.add(LEVELS)
    order = [SURFACE, BOTTOM, *WATER_COLUMN, LEVELS]
    return [layer for layer in order if layer in layers]


def _thickness_of(thickness, thickness_for, row):
    if row["pattern"] not in thickness_for:
        thickness_for[row["pattern"]] = thickness_data(thickness, row["files"], row.get("as_missing"))
    return thickness_for[row["pattern"]]


def make_common_grid(unify, folder, sims, rows_by_sim, thickness_for):
    """Make what the simulations share, if their grids are unified: for each
    variable and layer, the mask and (for a baseline) the grid, in folder (see
    oceanval.sim_unify.prepare). Returns how many cells each variable's layers
    have in common, as {variable key: {layer: [shared cells, cells]}}."""
    cells = {}
    for number, row in enumerate(rows_by_sim[sims[0]["name"]]):
        cells[row["key"]] = {}
        for layer in layers_of(row):
            _say(f"Putting {row['name']} ({layer}) on one grid")
            fields = {}
            for sim in sims:
                own = rows_by_sim[sim["name"]][number]
                ds_thickness = _thickness_of(sim["thickness"], thickness_for[sim["name"]], own) if layer in WATER_COLUMN else None
                fields[sim["name"]] = first_step_of(own["files"], own, layer, own["levels"], ds_thickness)
            cells[row["key"]][layer] = list(sim_unify.prepare(folder, unify, row["key"], layer, fields))
    return cells


def grid_of(path, name=None):
    """The longitudes and latitudes of model file path, as sim_unify._lonlat
    gives them, or None if they can not be told. A model file can be many GB,
    so CDO first writes one level of one time step of variable name (of every
    variable, if None) to a small file, and only that is opened: select reads
    only what it chooses."""
    chosen = f"name={name}," if name else ""
    # a variable with no times has no first time step to choose
    for choice in (f"-select,{chosen}levidx=1,timestep=1", f"-select,{chosen}levidx=1"):
        try:
            with warnings.catch_warnings(record=True):
                ds = nc.open_data(path, checks=False)
                ds.cdo_command(choice)
                ds.run()
            # the window looks at the grids in a thread of its own
            with netcdf_lock.LOCK, xr.open_dataset(ds.current[0], decode_times=False) as data:
                return sim_unify._lonlat(data)
        except Exception:
            continue
    return None


def first_parts(rows_by_sim, sims):
    """The first file of the first variable of each simulation, and the model
    variable read from it (None if not known), by simulation name."""
    out = {}
    for sim in sims:
        row = rows_by_sim[sim["name"]][0]
        out[sim["name"]] = (row["files"][0], (row.get("parts") or [None])[0])
    return out


def _same_grid(rows_by_sim, sims):
    """Whether the simulations are on the same horizontal grid, as the first
    file of the first variable of each says: True, False, or None if it can not
    be told."""
    first = None
    try:
        for path, name in first_parts(rows_by_sim, sims).values():
            lonlat = grid_of(path, name)
            if lonlat is None:
                return None
            if first is None:
                first = lonlat
            elif not sim_unify.same_lonlat(first, lonlat):
                return False
        return True if len(sims) > 1 else None
    except Exception:
        return None


def build_report(folder, info):
    """Build the report in folder (the comparison's FOLDER) from info (see
    INFO_FILE). Returns the path of its first page."""
    from oceanval import sim_report

    return sim_report.build_report(folder, info)


@_restores_nctoolkit_options
def compare_simulations(
    simulations=None,
    variables=None,
    climatology=None,
    phenology=None,
    long_term=None,
    seasonal=None,
    transects=None,
    map_changes=None,
    stations=None,
    subregions=None,
    lon_lim=None,
    lat_lim=None,
    unify=None,
    thickness=None,
    time_shift=None,
    as_missing=None,
    out_dir=".",
    cores=None,
    figure_code=False,
    view=True,
):
    """
    Compare up to five simulations of the same variables, with everything
    analyze() can do, in one HTML report: the simulations side by side, with a
    colour for each in the time series.

    Parameters
    -------------
    simulations : list
        The simulations, two to five, each a dict of "name" and "sim_dir" (the
        directory its output is in), and, if they are not the same as for the
        others, "n_dirs_down" (how many directories below sim_dir the output
        files are; default 2), "exclude", "require", "time_shift", "thickness"
        and "as_missing", as in analyze(), e.g.
        [{"name": "Base", "sim_dir": "/data/base"}, {"name": "Warm", "sim_dir": "/data/warm", "n_dirs_down": 1}].
        A simulation can also have a "times_index": the path of the times of its files already read, which
        the window's check of the dates leaves for the run so that the files' times are read once; any file
        that has changed since is read again, and a path that is not there means all are.
    variables : dict
        The variables to compare: the name to give each, and for each, the model
        variable holding it in each simulation, several summed as "a+b", e.g.
        {"Temperature": {"Base": "thetao", "Warm": "votemper"}}. A model variable
        alone is for a variable with the same one in every simulation. The
        simulations must give the variable in the same units.
    climatology, phenology, long_term, seasonal, transects, map_changes, stations, subregions, lon_lim, lat_lim :
        As in analyze(). Everything is the same for every simulation, and the
        years must be years that every simulation has.
    unify : dict or None
        What to do about the simulations being on different grids. None leaves
        each on its own. {"lon": [west, east], "lat": [south, north], "res":
        degrees} puts all of them on a lon-lat grid of that box and
        resolution (with nctoolkit's to_latlon). {"baseline": name} puts the
        others on the grid of simulation name (with nctoolkit's regrid). In
        both, a cell with no value in any one simulation is missing in all of
        them (judged from each one's first time step), so every simulation is
        compared over exactly the same cells.
    thickness, time_shift, as_missing :
        As in analyze(), for the simulations that do not give their own.
    out_dir : str
        The directory to save the comparison in: everything goes in a folder
        called oceanval_simulation_comparison in it, which replaces any already
        there.
    cores : int
        The number of cores nctoolkit may use. There is no default: you must
        say how many.
    figure_code : bool
        Give each figure in the report a "Copy code to reproduce figure"
        button. Default is False.
    view : bool
        Open the report in a web browser once it is built.

    Returns
    -------------
    str
        The path of the report's first page.
    """
    from oceanval import user_cache

    if isinstance(cores, bool) or not isinstance(cores, (int, np.integer)) or cores < 1:
        raise ValueError("Give cores, the number of cores OceanVal may use, a whole number of 1 or more.")
    if not isinstance(figure_code, (bool, np.bool_)):
        raise ValueError("figure_code must be True or False.")
    # the cores the user gave are the ones nctoolkit uses for everything that follows
    nc.options(cores=int(cores))
    if simulations is None:
        raise ValueError("Give simulations, the two to five simulations to compare.")
    sims = check_simulations(simulations, thickness, time_shift, as_missing)
    names = [sim["name"] for sim in sims]
    by_sim = check_variables(variables, names)
    unify = sim_unify.check_unify(unify, names)
    out_dir = os.path.abspath(out_dir or ".")
    if os.path.isfile(out_dir):
        raise ValueError(f"out_dir, {out_dir}, is a file, not a directory.")

    # the same analyses of the same variables in every simulation, so the rows are the same in each
    rows_by_sim = {}
    for sim in sims:
        try:
            rows = check_arguments(
                by_sim[sim["name"]],
                climatology,
                phenology,
                sim["thickness"],
                sim["time_shift"],
                long_term,
                seasonal,
                transects,
                map_changes,
            )
        except ValueError as error:
            if "thickness" in str(error):
                raise ValueError(f"{sim['name']}: {error}") from None
            raise
        for row in rows:
            row["as_missing"] = sim["as_missing"]
        rows_by_sim[sim["name"]] = rows
    area = check_area(rows_by_sim[names[0]], subregions, lon_lim, lat_lim, stations)

    problems = []
    for sim in sims:
        try:
            locate_rows(
                rows_by_sim[sim["name"]],
                sim["sim_dir"],
                sim["n_dirs_down"],
                sim["exclude"],
                sim["require"],
                sim["time_shift"],
                sim["times_index"],
            )
        except ValueError as error:
            problems.extend(f"{sim['name']}: {line}" for line in str(error).splitlines())
    if not problems:
        for number, row in enumerate(rows_by_sim[names[0]]):
            units = {name: rows_by_sim[name][number]["units"] for name in names}
            if len({value for value in units.values() if value}) > 1:
                shown = "; ".join(f"{name}: {value or 'none'}" for name, value in units.items())
                problems.append(f"{row['name']} is not in the same units in every simulation ({shown}).")
    if unify is not None and not problems:
        for number, row in enumerate(rows_by_sim[names[0]]):
            counts = {name: rows_by_sim[name][number]["levels"] for name in names}
            if LEVELS in layers_of(row) and len(set(counts.values())) > 1:
                problems.append(
                    f"The vertical transects of {row['name']} need the same number of levels in every simulation to "
                    f"share a grid, and they have {', '.join(f'{name}: {count}' for name, count in counts.items())}."
                )
    if problems:
        raise ValueError("\n".join(problems))

    # any comparison there before is replaced, as analyze() replaces its analysis
    folder = os.path.join(out_dir, FOLDER)
    _remove(folder)
    results_dir = os.path.join(folder, RESULTS)
    os.makedirs(results_dir)

    thickness_for = {name: {} for name in names}
    cells = None
    if unify is not None:
        unify_dir = os.path.join(results_dir, UNIFY)
        os.makedirs(unify_dir)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r".* missing in the dataset")
            cells = make_common_grid(unify, unify_dir, sims, rows_by_sim, thickness_for)
        for sim in sims:
            for row in rows_by_sim[sim["name"]]:
                row["unify"] = sim_unify.row_unify(unify, unify_dir, sim["name"])
    same_grid = True if unify is not None else _same_grid(rows_by_sim, sims)

    entries = {}
    masks_for = {}
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r".* missing in the dataset")
        for sim in sims:
            _say(f"Working out the simulation {sim['name']}")
            sim_results = os.path.join(results_dir, sim["key"])
            os.makedirs(sim_results)
            entries[sim["name"]] = [
                _work_out(row, sim_results, sim["thickness"], thickness_for[sim["name"]], sim["time_shift"], area, masks_for)
                for row in rows_by_sim[sim["name"]]
            ]

    from oceanval import __version__

    # each simulation's results are laid out as analyze()'s are, so that the figures of analysis_plots can draw them
    for sim in sims:
        with open(os.path.join(results_dir, sim["key"], INFO_FILE_OF_ANALYSIS), "w") as file:
            json.dump(
                {
                    "version": 1,
                    "oceanval": __version__,
                    "created": datetime.datetime.now().isoformat(timespec="seconds"),
                    "sim_dir": sim["sim_dir"],
                    "n_dirs_down": sim["n_dirs_down"],
                    "exclude": sim["exclude"],
                    "require": sim["require"],
                    "thickness": sim["thickness"],
                    "time_shift": sim["time_shift"] or 0,
                    "as_missing": sim["as_missing"],
                    "subregions": area["subregions"],
                    "lon_lim": area["lon_lim"],
                    "lat_lim": area["lat_lim"],
                    "stations": {name: {"lon": lon, "lat": lat} for name, (lon, lat) in area["stations"].items()},
                    "figure_code": bool(figure_code),
                    "variables": entries[sim["name"]],
                },
                file,
                indent=2,
            )

    colours = {name: COLOURS[number] for number, name in enumerate(names)}
    variables_info = []
    for number, row in enumerate(rows_by_sim[names[0]]):
        first = entries[names[0]][number]
        variables_info.append(
            {
                "name": row["name"],
                "key": row["key"],
                "long_name": first["long_name"],
                "units": first["units"],
                "kinds": [kind for kind in KINDS if first.get(kind)],
                "model_variables": {name: rows_by_sim[name][number]["model_variable"] for name in names},
                "sims": {name: entries[name][number] for name in names},
            }
        )
    info = {
        "version": 1,
        "oceanval": __version__,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "simulations": [{k: v for k, v in sim.items()} for sim in sims],
        "colours": colours,
        "unify": unify,
        "unify_text": sim_unify.describe(unify),
        "same_grid": same_grid,
        "shared_cells": cells,
        "subregions": area["subregions"],
        "lon_lim": area["lon_lim"],
        "lat_lim": area["lat_lim"],
        "stations": {name: {"lon": lon, "lat": lat} for name, (lon, lat) in area["stations"].items()},
        "figure_code": bool(figure_code),
        "variables": variables_info,
    }
    with open(os.path.join(results_dir, INFO_FILE), "w") as file:
        json.dump(info, file, indent=2)

    _say("Building the report")
    landing = build_report(folder, info)
    for sim in sims:
        user_cache.record_sim_dir(sim["sim_dir"])
    _say(f"The comparison is in {folder}: open {os.path.join(folder, REPORT_LINK)} to see the report")
    if view:
        webbrowser.open("file://" + os.path.abspath(landing))
    return landing


def load(results_dir):
    """What compare_simulations() saved in results_dir (see INFO_FILE)."""
    with open(os.path.join(results_dir, INFO_FILE)) as file:
        return json.load(file)
