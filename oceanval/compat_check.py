"""An interactive check of whether a model output file is compatible with OceanVal.

The window's Help | Examples → "Is my data compatible with OceanVal?" asks for
one file, and for one of its variables if it has more than one, and answers six
questions about that variable, a row each, in this order:

    cdo          Can CDO open my data?                          (cdo sinfo)
    regrid       Can my data be regridded?                      (to 360x180, bilinear and nearest neighbour)
    mean         Can a spatial average be calculated?           (cell areas)
    time         Is the time axis understood?                   (dates, resolution, calendar)
    interactive  Can my data be plotted interactively?          (the map of View simulation output)
    climatology  Can my data be plotted as Analyze does?        (a map of the sea surface's climatology)

The variables are those View simulation output finds in the file
(`viewer_catalogue`), and the level used is its surface (`surface_level`).

Raw FVCOM output is told first (`create_recipes._looks_like_fvcom`): it should be
compatible, CDO cannot read it (OceanVal regrids it itself), so the cdo, regrid and
mean rows are skipped, as is the climatology map (Analyze does not work with FVCOM
output yet). The time check and the interactive plot are made.

The checks run in a process of their own (`Compat`, which runs `main`), killed by
`cancel` or if a step takes longer than STEP_TIMEOUT: CDO can crash, and
nctoolkit's pools can hang. The child prints one line per event, after MARKER;
anything else it prints (CDO's own warnings) is ignored. When the file has more
than one variable the child asks which to use (a "variables" event with ask) and
waits for a line on stdin. Nothing here raises.
"""

import base64
import contextlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import warnings

from oceanval import cdo_check

MARKER = "OCEANVAL-COMPAT-CHECK "

# the questions, in the order they are answered
ROWS = (
    ("cdo", "Can CDO open my data?"),
    ("regrid", "Can my data be regridded?"),
    ("mean", "Can a spatial average be calculated from my data?"),
    ("time", "Is the time axis understood?"),
    ("interactive", "Can my data be plotted interactively, as in View simulation output?"),
    ("climatology", "Can my data be plotted as a climatology, as in Analyze simulations?"),
)
QUESTIONS = dict(ROWS)

WAITING, WORKING, OK, WARNING, FAILED, SKIPPED = "waiting", "working", "ok", "warning", "failed", "skipped"

# the longest a step may go without telling us anything, in seconds
STEP_TIMEOUT = 300
# the grid the data is regridded to: 360 x 180
REGRID = {"lon": [-180, 180], "lat": [-90, 90], "res": [1, 1]}
METHODS = (("bil", "bilinear"), ("nn", "nearest-neighbour"))
# the climatology is made of the first this many time steps of the file: a model file can be many GB
CLIMATOLOGY_STEPS = 12

GENERIC = (
    "The file has a generic grid: it has no longitudes and latitudes that CDO can interpret, so the "
    "cells cannot be placed on the globe and the files cannot be regridded. Add CF-compliant "
    "longitude and latitude coordinates to the file."
)

_GRID = re.compile(r"^\s*\d+ : (\w+)\s*: points=(\d+)(?: \((.*?)\))?")
_VARIABLE = re.compile(r"^\s*\d+ : \S+\s+\S+\s+[vcf]\s+\w+\s+\d+\s+\d+\s+\d+")
_STEPS = re.compile(r"(\d+) steps?")


def _short(error, most=300):
    text = " ".join(str(error).split()) or type(error).__name__
    return text if len(text) <= most else text[: most - 3] + "..."


def _plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


# ---- the path box: what can be chosen from where the user has got to ----

# the most folders and files listed at once, the rest being counted
MOST_ENTRIES = 200
EXTENSION = ".nc"


def listing(text, cwd=None):
    """What can be chosen from the path typed so far, as the box lists it: a dict of
    kind "empty" (nothing typed), "file" (an existing file: size), "directory" (an existing
    directory: what is in it), "partial" (the start of a name in an existing directory: the
    names that begin so) or "missing" (nothing there). base is the text typed up to and
    including its last "/", which a choice is put after; entries are the non-hidden folders
    then the .nc files (links followed, broken ones left out) in natural order, at most
    MOST_ENTRIES of them (more is the number left out); folders and nc_files count what is in
    the directory whatever has been typed after the "/". Relative paths, and ~, are as everywhere
    in the window: from cwd. Never raises: error says in words why a directory could not be read."""
    from oceanval.viewer_times import natural_key

    typed = text.strip() if isinstance(text, str) else ""
    found = {
        "kind": "empty", "directory": None, "base": "", "entries": [], "folders": 0, "nc_files": 0,
        "more": 0, "size": None, "error": None,
    }
    if not typed:
        return found
    full = os.path.normpath(os.path.join(os.path.abspath(cwd or os.getcwd()), os.path.expanduser(typed)))
    cut = typed.rfind("/") + 1
    found["base"] = typed[:cut]
    if os.path.isfile(full) and not typed.endswith("/"):
        found["kind"] = "file"
        with contextlib.suppress(OSError):
            found["size"] = os.path.getsize(full)
        return found
    if os.path.isdir(full):
        directory, prefix, found["kind"] = full, "", "directory"
        if not typed.endswith("/"):
            # the directory itself, typed without its "/": what is chosen goes inside it
            found["base"] = typed + "/"
    else:
        directory, prefix, found["kind"] = os.path.dirname(full), typed[cut:].lower(), "partial"
        if not os.path.isdir(directory):
            found["kind"] = "missing"
            return found
    found["directory"] = directory
    folders, files = [], []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    if entry.is_dir():
                        if not entry.name.startswith("."):
                            folders.append(entry.name)
                    elif entry.name.endswith(EXTENSION) and entry.is_file():
                        files.append(entry.name)
                except OSError:
                    # a link to something that has gone
                    continue
    except PermissionError:
        found["error"] = "You do not have permission to open this directory."
        return found
    except OSError as error:
        found["error"] = f"This directory cannot be opened: {error.strerror or error}."
        return found
    found["folders"], found["nc_files"] = len(folders), len(files)
    order = lambda name: (natural_key(name.lower()), name)  # noqa: E731
    names = [(name, "folder") for name in sorted(folders, key=order) if name.lower().startswith(prefix)]
    names += [(name, "file") for name in sorted(files, key=order) if name.lower().startswith(prefix)]
    found["entries"] = [{"name": name, "type": kind} for name, kind in names[:MOST_ENTRIES]]
    found["more"] = max(0, len(names) - MOST_ENTRIES)
    if found["kind"] == "partial" and not names:
        found["kind"] = "missing"
    return found


# ---- what CDO says ----


def explain(error, grid_types=()):
    """A sentence for the user from the error text of a CDO or nctoolkit regridding that
    failed, on a grid of the types grid_types (from cdo sinfo)."""
    text = " ".join(str(error).split())
    low = text.lower()
    if "generic" in low or (grid_types and set(grid_types) == {"generic"}):
        return GENERIC
    if "unsupported grid" in low or "unsupported array" in low or "unstructured" in low:
        return "CDO does not support this type of grid for regridding (" + _short(text, 160) + ")."
    if "bounds" in low or "corner" in low:
        return "The grid has no cell bounds (corners), which CDO needs to regrid it: " + _short(text, 160)
    if "memory" in low or "alloc" in low:
        return "CDO ran out of memory regridding the file."
    if "no such file" in low or "open failed" in low:
        return "CDO could not open the file."
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    return _short(lines[-1] if lines else text)


def read_sinfo(output):
    """What `cdo sinfo`'s output (a string) says: {"variables": count, "grids": [(type,
    points, shape)], "steps": count or None}."""
    grids, variables, steps = [], 0, None
    for line in output.splitlines():
        found = _GRID.match(line)
        if found:
            grids.append((found.group(1), int(found.group(2)), found.group(3) or ""))
        elif _VARIABLE.match(line):
            variables += 1
        else:
            more = _STEPS.search(line)
            if more and "time" in line.lower():
                steps = int(more.group(1))
    return {"variables": variables, "grids": grids, "steps": steps}


# ---- the checks, run in the child ----


class _Stop(Exception):
    """A row cannot be answered: the message says why (for rows that cannot go on)."""

    def __init__(self, message, status=FAILED):
        super().__init__(message)
        self.status = status


def _row(emit, key, status, message=""):
    emit({"event": "row", "key": key, "status": status, "message": message})


def _cdo(path, emit, found):
    """cdo sinfo: it must not crash and must give a variable on a grid."""
    _row(emit, "cdo", WORKING)
    try:
        done = subprocess.run(
            ["cdo", "sinfo", path],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=cdo_check.TIMEOUT,
        )
    except FileNotFoundError:
        return _row(emit, "cdo", FAILED, "CDO is not installed (no `cdo` on the path), so OceanVal cannot read any files.")
    except subprocess.TimeoutExpired:
        return _row(emit, "cdo", FAILED, f"`cdo sinfo` did not finish in {cdo_check.TIMEOUT} seconds.")
    if cdo_check.crashed(done.returncode, done.stderr):
        return _row(
            emit,
            "cdo",
            FAILED,
            "`cdo sinfo` segfaulted on this file. OceanVal cannot work with it, as it reads files with CDO. "
            "This indicates that the data could potentially not be CF-compliant. "
            f"Try changing the CDO version: {cdo_check.LINK}",
        )
    if done.returncode != 0:
        lines = [line.strip() for line in done.stderr.splitlines() if line.strip()]
        return _row(emit, "cdo", FAILED, "CDO could not read the file: " + _short(lines[-1] if lines else "no error given"))
    sinfo = read_sinfo(done.stdout)
    if not sinfo["variables"] or not sinfo["grids"] or not any(points > 0 for _, points, _ in sinfo["grids"]):
        return _row(emit, "cdo", FAILED, "CDO opened the file but found no variables on a grid in it.")
    found["grid_types"] = [kind for kind, _, _ in sinfo["grids"]]
    kind, points, shape = sinfo["grids"][0]
    where = f"{kind} grid, {points} points" + (f" ({shape})" if shape else "")
    return _row(emit, "cdo", OK, f"CDO read the file: {_plural(sinfo['variables'], 'variable')} on a {where}.")


def _slice(path, variable, level):
    """The first time step and surface level of variable, as an nctoolkit dataset that has been run."""
    from oceanval import grid_align

    try:
        return grid_align.field_for_cdo(path, variable, level=level)
    except ValueError as error:
        raise _Stop(str(error))


def _regrid(ds, found, emit):
    """Regrid the slice to 360x180, by bilinear and nearest neighbour."""
    _row(emit, "regrid", WORKING)
    worked, failed = {}, {}
    for method, name in METHODS:
        work = ds.copy()
        try:
            with warnings.catch_warnings(record=True):
                work.to_latlon(lon=REGRID["lon"], lat=REGRID["lat"], res=REGRID["res"], method=method)
                work.run()
            worked[method] = work
        except Exception as error:
            failed[method] = explain(error, found.get("grid_types", ()))
    found["generic"] = GENERIC in failed.values()
    variable = found["variable"]
    if len(worked) == 2:
        return _row(emit, "regrid", OK, f"{variable} regridded to a 360x180 grid by bilinear and nearest-neighbour methods.")
    if worked:
        good = dict(METHODS)[next(iter(worked))]
        bad, why = next(iter(failed.items()))
        return _row(
            emit, "regrid", WARNING, f"Only {good} regridding works. {dict(METHODS)[bad].capitalize()} failed: {why}"
        )
    why = next(iter(failed.values()))
    if why == GENERIC:
        return _row(emit, "regrid", FAILED, why)
    return _row(emit, "regrid", FAILED, "Neither bilinear nor nearest-neighbour regridding works. " + why)


def _mean(ds, found, emit):
    """Whether a spatial mean can be weighted by cell areas, as the Analyze report does."""
    from oceanval import analysis_series

    _row(emit, "mean", WORKING)
    variable = found["variable"]
    try:
        with warnings.catch_warnings(record=True):
            values, note = analysis_series.robust_spatial_mean(ds.copy(), variable, 1)
    except Exception as error:
        if found.get("generic"):
            return _row(
                emit, "mean", FAILED,
                "The grid has no cell areas, and it is generic so it cannot be regridded to work around that."
            )
        return _row(emit, "mean", FAILED, "No spatial average could be worked out: " + _short(error))
    if note:
        return _row(
            emit, "mean", WARNING,
            "The grid has no cell areas (no cell bounds). OceanVal will regrid to a regular lon-lat grid to "
            "calculate spatial averages, and the report will say so.",
        )
    return _row(emit, "mean", OK, "The grid has cell areas, so spatial averages are weighted by them.")


def _resolution(label, count):
    if count < 2:
        return "only one time step, so the resolution cannot be told"
    if label == "monthly":
        return "monthly (or coarser)"
    if label == "1d":
        return "daily (or finer)"
    return f"every {label[:-1]} days"


def _describe_times(times, calendar):
    import pandas as pd

    from oceanval import time_res

    frame = pd.DataFrame(
        {"year": [t.year for t in times], "month": [t.month for t in times], "day": [t.day for t in times]}
    )
    first, last = times[0].strftime("%Y-%m-%d"), times[-1].strftime("%Y-%m-%d")
    resolution = _resolution(time_res.label(frame), len(times))
    return f"{_plural(len(times), 'time step')}, {first} to {last}, {resolution}" + (
        f", {calendar} calendar" if calendar else ""
    )


def _time(path, variable, emit):
    """The time axis of variable: it must decode to dates; times that look like the ends of their periods are
    warned about."""
    from oceanval import time_stamps

    _row(emit, "time", WORKING)
    try:
        read = time_stamps._read(path, variable)
    except Exception as error:
        return _row(emit, "time", FAILED, "The time axis could not be decoded: " + _short(error))
    if read is None or not read["times"]:
        return _row(
            emit, "time", FAILED,
            f"{variable} has no time axis with units OceanVal can turn into dates, so model and "
            "observations cannot be matched in time.",
        )
    text = _describe_times(read["times"], read["calendar"])
    try:
        evidence = time_stamps._evidence([path], variable)
    except Exception:
        evidence = None
    if evidence is not None:
        return _row(
            emit, "time", WARNING,
            f"{text}. The times look like the ends of their periods ({evidence['reason']}). "
            "OceanVal will ask whether to shift them back a day (time_shift=-1) when matching up.",
        )
    return _row(emit, "time", OK, text + ".")


def _fvcom_time(path, emit):
    from oceanval import fvcom, time_res

    _row(emit, "time", WORKING)
    try:
        frame = fvcom.fvcom_times(path)
    except Exception as error:
        return _row(emit, "time", FAILED, "The FVCOM time axis could not be decoded: " + _short(error))
    if not len(frame):
        return _row(emit, "time", FAILED, "The file has no times.")
    first = f"{frame.year.iloc[0]:04d}-{frame.month.iloc[0]:02d}-{frame.day.iloc[0]:02d}"
    last = f"{frame.year.iloc[-1]:04d}-{frame.month.iloc[-1]:02d}-{frame.day.iloc[-1]:02d}"
    resolution = _resolution(time_res.label(frame), len(frame))
    return _row(emit, "time", OK, f"{_plural(len(frame), 'time step')}, {first} to {last}, {resolution}.")


def _interactive(catalogue, variable, emit):
    """The map View simulation output draws of the variable at the surface of the first time step."""
    from oceanval import viewer_plots

    _row(emit, "interactive", WORKING)
    try:
        about = catalogue.combined("0", [variable])
        data, _ = catalogue.field("0", [variable], 0, about["surface_level"], [])
        item, notes, _ = viewer_plots.map_plot(
            data, about, about["name"], mesh=catalogue.mesh("0") if about.get("mesh") else None
        )
    except Exception as error:
        return _row(emit, "interactive", FAILED, "The map could not be drawn: " + _short(error))
    emit({"event": "item", "item": item})
    if not data.notnull().any():
        return _row(emit, "interactive", WARNING, f"Drawn, but every value of {variable} is missing in its first time step at the surface.")
    if about.get("lon") is None and not about.get("mesh"):
        return _row(
            emit, "interactive", WARNING,
            "Drawn, but by grid index: the file has no longitudes and latitudes OceanVal can use to place the cells.",
        )
    return _row(emit, "interactive", OK, f"Drawn from {variable}, first time step, at the surface. Zoom and pan it below.")


def _png():
    """The current matplotlib figure as a data: URI, which is closed."""
    import matplotlib.pyplot as plt

    buffer = io.BytesIO()
    plt.gcf().savefig(buffer, format="png", dpi=80, bbox_inches="tight")
    plt.close("all")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _surface_mean(path, variable, level):
    """The mean of variable at its surface level over the first CLIMATOLOGY_STEPS time steps of path, the monthly
    climatology's annual mean as Analyze makes it, as a run nctoolkit dataset."""
    from oceanval.quiet_nc import nc

    choices = (
        f"name={variable},levidx={level + 1},timestep=1/{CLIMATOLOGY_STEPS}",
        f"name={variable},timestep=1/{CLIMATOLOGY_STEPS}",
        f"name={variable},levidx={level + 1}",
        f"name={variable}",
    )
    last = None
    for choice in choices:
        try:
            with warnings.catch_warnings(record=True):
                ds = nc.open_data(path, checks=False)
                ds.cdo_command(f"-select,{choice}")
                ds.run()
            break
        except Exception as error:
            last = error
    else:
        raise ValueError(f"{variable} could not be read by CDO: {_short(last)}")
    with warnings.catch_warnings(record=True):
        try:
            ds.tmean("month")
            ds.tmean()
            ds.run()
        except Exception:
            # a variable with no times is drawn as it is
            pass
    return ds


def _climatology(path, variable, about, emit):
    """The map Analyze simulations draws of the annual mean of the surface's climatology."""
    from oceanval import analysis_plots

    _row(emit, "climatology", WORKING)
    try:
        ds = _surface_mean(path, variable, about["surface_level"])
        saved = ds.current[0]
        options = {
            "var": variable,
            "robust": True,
            "legend": analysis_plots.legend(about.get("long_name") or variable, about.get("units") or ""),
        }
        if analysis_plots._takes_grid_labels():
            options["grid_labels"] = True

        def code_for(grid):
            return analysis_plots._code(
                analysis_plots.MAP_IMPORTS,
                analysis_plots._open_code(saved, variable, grid),
                analysis_plots._call("ds.pub_plot", options),
            )

        import oceanval

        drawn = analysis_plots._map(
            code_for, saved, {"sim_dir": path, "oceanval": getattr(oceanval, "__version__", "")}, "compatibility check"
        )
        image = _png()
    except Exception as error:
        import matplotlib.pyplot as plt

        plt.close("all")
        if about.get("lon") is None:
            return _row(
                emit, "climatology", FAILED,
                "The map could not be drawn: the variable has no longitudes and latitudes OceanVal can use to place its cells.",
            )
        return _row(emit, "climatology", FAILED, "The map could not be drawn: " + _short(error))
    emit({"event": "image", "data": image})
    if drawn.regridded:
        return _row(
            emit, "climatology", WARNING,
            "The map is drawn only after regridding the model's grid to a regular lon-lat grid, "
            "as the reports do for grids that cannot be drawn as they are.",
        )
    return _row(emit, "climatology", OK, f"Drawn: the mean of {variable} at the surface over the file's first {_plural(CLIMATOLOGY_STEPS, 'time step')}.")


def _variables(path):
    """(the viewer's Catalogue of the file, its variables {name: about}, why not): the variables View simulation
    output finds in it. The Catalogue is None if the file cannot be read."""
    from oceanval import viewer_catalogue

    try:
        catalogue = viewer_catalogue.scan(path, 0)
    except Exception as error:
        return None, {}, _short(error)
    stream = catalogue.streams[0]
    if stream.get("error"):
        return None, {}, _short(stream["error"])
    return catalogue, dict(stream["variables"]), None


def _describe(variables):
    """What the page is shown of each variable."""
    return {
        name: {"long_name": about.get("long_name") or "", "units": about.get("units") or "", "levels": about.get("levels", 1)}
        for name, about in variables.items()
    }


def run(path, emit, variable=None, choose=None):
    """Check the file at path, telling emit each event (a dict): "fvcom" (with a notice), "variables" (the
    variables of the file; ask says whether one is to be chosen), "variable" (the one used), "row" (key,
    status, message), "item" (the interactive map's Bokeh json_item), "image" (the climatology map as a
    data: URI) and, last, "done".

    The variable checked is `variable` if given, the only one if the file has only one, else the one
    choose(variables) returns (variables is what the "variables" event holds; the first, if choose is None)."""
    try:
        if not os.path.isfile(path):
            raise _Stop("There is no file at that path.")
        from oceanval.create_recipes import _looks_like_fvcom

        fvcom = _looks_like_fvcom(path)
        catalogue, variables, trouble = _variables(path)
        if variable is None and len(variables) > 1:
            emit({"event": "variables", "ask": True, "variables": _describe(variables)})
            variable = choose(_describe(variables)) if choose is not None else next(iter(variables))
        elif variables:
            emit({"event": "variables", "ask": False, "variables": _describe(variables)})
        if variable is not None and variable not in variables:
            raise _Stop(f"The file has no variable {variable}.")
        if variable is None and variables:
            variable = next(iter(variables))
        if variable is not None:
            emit({"event": "variable", "name": variable})
        if fvcom:
            emit({
                "event": "fvcom",
                "notice": "This is FVCOM output. It should be compatible with OceanVal. CDO cannot read raw FVCOM "
                "files, so OceanVal regrids them itself and the CDO, regridding and spatial-average checks do not "
                "apply. The time check and the interactive plot are made.",
            })
            for key in ("cdo", "regrid", "mean"):
                _row(emit, key, SKIPPED, "Not needed for FVCOM output: OceanVal reads and regrids it itself.")
            _fvcom_time(path, emit)
            if catalogue is None:
                _row(emit, "interactive", FAILED, "The map could not be drawn: " + (trouble or "the file could not be read."))
            else:
                _interactive(catalogue, variable, emit)
            _row(emit, "climatology", SKIPPED, "Analyze simulations does not work with FVCOM output yet.")
            return
        found = {"variable": variable}
        _cdo(path, emit, found)
        if found.get("grid_types") is None:
            # CDO could not read it: nothing else can be tried
            for key in ("regrid", "mean", "time", "interactive", "climatology"):
                _row(emit, key, SKIPPED, "Not tried: CDO cannot open the file.")
            return
        if variable is None:
            why = "The variables of the file could not be read: " + (trouble or "it has none that can be drawn.")
            for key in ("regrid", "mean", "time", "interactive", "climatology"):
                _row(emit, key, SKIPPED, why)
            return
        about = variables[variable]
        try:
            ds = _slice(path, variable, about["surface_level"])
        except _Stop as stop:
            for key in ("regrid", "mean"):
                _row(emit, key, SKIPPED, str(stop))
            ds = None
        if ds is not None:
            _regrid(ds, found, emit)
            _mean(ds, found, emit)
        _time(path, variable, emit)
        _interactive(catalogue, variable, emit)
        _climatology(path, variable, about, emit)
    except _Stop as stop:
        _row(emit, "cdo", stop.status, str(stop))
        for key in ("regrid", "mean", "time", "interactive", "climatology"):
            _row(emit, key, SKIPPED, "Not tried.")
    except Exception as error:
        emit({"event": "crashed", "message": _short(error)})
    finally:
        emit({"event": "done"})


def main():
    """Entry point of the child process: the request is JSON on stdin ({"path": ...}), and, if the
    file has more than one variable and none was given, a second line {"variable": ...} answers the
    "variables" event."""
    import matplotlib

    matplotlib.use("Agg")
    request = json.loads(sys.stdin.readline() or "{}")

    def emit(event):
        os.write(1, (MARKER + json.dumps(event) + "\n").encode("utf-8"))

    def choose(variables):
        line = sys.stdin.readline()
        if not line:
            # the window has gone
            os._exit(0)
        return json.loads(line).get("variable")

    run(request.get("path", ""), emit, request.get("variable"), choose)


# ---- the window's side ----


class Compat:
    """The check of one file, in a process of its own. changed() is called (from a thread) when
    anything changes; state() is what the page is shown, and item() the interactive map. When the
    file has more than one variable the status is "choose" until choose(variable) is called."""

    def __init__(self, path, changed=None, step_timeout=None, variable=None):
        self.path = path
        self.changed = changed
        self.step_timeout = STEP_TIMEOUT if step_timeout is None else step_timeout
        self.rows = {key: {"key": key, "question": question, "status": WAITING, "message": ""} for key, question in ROWS}
        self.notice = None
        self.image = None
        self.map = None
        self.variables = {}
        self.variable = variable
        self.status = "running"
        self.process = None
        self.cancelled = False
        self._lock = threading.Lock()
        self._heard = time.monotonic()
        self._timed_out = False

    # what the page is shown
    def state(self):
        with self._lock:
            return {
                "path": self.path,
                "status": self.status,
                "fvcom": self.notice is not None,
                "notice": self.notice,
                "rows": [dict(row) for row in self.rows.values()],
                "image": self.image,
                "map": self.map is not None,
                "variable": self.variable,
                "variables": self.variables if self.status == "choose" else {},
            }

    def item(self):
        """The Bokeh json_item of the interactive map, or None."""
        with self._lock:
            return self.map

    def start(self):
        threading.Thread(target=self._run, name="oceanval-compat-check", daemon=True).start()
        return self

    def choose(self, variable):
        """Answer the question of which variable to use. Returns whether it was taken."""
        with self._lock:
            if self.status != "choose" or variable not in self.variables:
                return False
            self.variable = variable
            self.status = "running"
            self._heard = time.monotonic()
        try:
            self.process.stdin.write(json.dumps({"variable": variable}) + "\n")
            self.process.stdin.flush()
        except Exception:
            return False
        self._tell()
        return True

    def cancel(self):
        self.cancelled = True
        self._stop()

    def _stop(self):
        process = self.process
        if process is not None and process.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(process.pid, signal.SIGKILL)

    def _tell(self):
        if self.changed is not None and not self.cancelled:
            with contextlib.suppress(Exception):
                self.changed()

    def _take(self, event):
        with self._lock:
            kind = event.get("event")
            if kind == "fvcom":
                self.notice = event["notice"]
            elif kind == "variables":
                if event.get("ask"):
                    self.variables = event["variables"]
                    self.status = "choose"
            elif kind == "variable":
                self.variable = event["name"]
            elif kind == "row" and event.get("key") in self.rows:
                self.rows[event["key"]].update(status=event["status"], message=event.get("message", ""))
            elif kind == "item":
                self.map = event["item"]
            elif kind == "image":
                self.image = event["data"]
            elif kind == "crashed":
                self._fail_rest("The check stopped: " + event.get("message", ""))
        self._tell()

    def _fail_rest(self, message):
        """Rows not answered: the one in hand (else the first waiting) failed with message, the
        others not tried. The lock is held."""
        pending = [row for row in self.rows.values() if row["status"] in (WORKING, WAITING)]
        pending.sort(key=lambda row: row["status"] != WORKING)
        for number, row in enumerate(pending):
            if number == 0:
                row.update(status=FAILED, message=message)
            else:
                row.update(status=SKIPPED, message="Not tried.")

    def _watch(self):
        while self.process is not None and self.process.poll() is None:
            # nobody is timed while they choose
            if self.status != "choose" and time.monotonic() - self._heard > self.step_timeout:
                self._timed_out = True
                self._stop()
                return
            time.sleep(0.5)

    def _run(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = f"import sys; sys.path.insert(0, {root!r}); from oceanval.compat_check import main; main()"
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-u", "-c", code],
                env=dict(os.environ, PYTHONUNBUFFERED="1"),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
            self.process.stdin.write(json.dumps({"path": self.path, "variable": self.variable}) + "\n")
            self.process.stdin.flush()
            self._heard = time.monotonic()
            threading.Thread(target=self._watch, name="oceanval-compat-watch", daemon=True).start()
            for line in self.process.stdout:
                self._heard = time.monotonic()
                if line.startswith(MARKER):
                    with contextlib.suppress(ValueError):
                        self._take(json.loads(line[len(MARKER):]))
            self.process.wait()
        except Exception as error:
            with self._lock:
                self._fail_rest("The check could not be run: " + _short(error))
        if self.cancelled:
            return
        with self._lock:
            if any(row["status"] in (WORKING, WAITING) for row in self.rows.values()):
                if self._timed_out:
                    message = f"This step took more than {self.step_timeout} seconds, so the check was stopped."
                else:
                    message = "The check stopped unexpectedly: the process ended without finishing this step."
                self._fail_rest(message)
            self.status = "done"
        self._tell()
