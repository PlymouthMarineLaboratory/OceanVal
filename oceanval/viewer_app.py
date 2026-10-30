"""The oceanval window's View simulation output steps, which oceanval.app.App
takes on.

Viewing starts from the landing page's View simulation output tile, and goes
through these views:

    "setup"             the simulation (the setup page, without the domain,
                        and with the numbers to treat as missing), as
                        Analyze's: the same directories-down box, filled in
                        from the folders' layout, and file filters
    "viewer_scanning"   the variables of each file type being read (see
                        oceanval.viewer_catalogue.scan)
    "viewer"            the viewer: the variables, searched, mapped one at a
                        time or summed, at the time, level and colours chosen,
                        while the times of the files are read in the
                        background, in a process of their own

Back goes from the viewer to the setup page, with its boxes as they were, and
stops the times being read; from the setup page to the landing page. Nothing
is written but the cache of the files' times (see oceanval.viewer_times).

The page asks for each map (/api/viewer/plot), the colour scale of an
animation (/api/viewer/limits) and what the files hold
(/api/viewer/catalogue) as it needs them, and is drawn by BokehJS, which the
window serves (/bokeh.min.js), so that nothing is loaded from elsewhere.
"""

import importlib.util
import json
import math
import os
import threading
import time
import traceback

from oceanval import user_cache, viewer_catalogue

# the action of these steps, which the landing page's tile chooses
VIEWER = "viewer"
# the least time, in seconds, between telling the page how many files' times have been read
TIMES_NEWS = 0.5


def default_setup_form():
    """The setup page's boxes for viewing, as they start: the simulation
    step's, and the numbers to treat as missing, as the page's JSON rows of
    [from, to] (see parse_missing)."""
    from oceanval.app import default_setup_form as setup_form

    return dict(setup_form(), missing="")


def check_setup(form, cwd):
    """The setup page's boxes as (arguments, errors), as
    oceanval.app.check_setup gives them, without the domain and the years,
    which viewing has no use for, and with "missing", the ranges to treat as
    missing (see parse_missing). A directory with no files to look at is an
    error on the directories down."""
    from oceanval.app import _form
    from oceanval.app import check_setup as check
    from oceanval.app import _path

    form = _form(form, default_setup_form())
    # a file is used as it is: checked as its directory, with no directories down
    path = _path(form["simdir"], cwd) if form["simdir"] else ""
    is_file = bool(path) and os.path.isfile(path)
    shared = dict(form, simdir=os.path.dirname(path), ndown="0") if is_file else form
    arguments, errors = check(dict(shared, domain="global", start="0", end="0"), cwd)
    if is_file:
        arguments["simdir"] = path
    for name in ("domain", "start", "end"):
        arguments.pop(name, None)
        errors.pop(name, None)
    try:
        arguments["missing"] = parse_missing(form["missing"])
    except ValueError as error:
        errors["missing"] = str(error)
    if not errors and not viewer_catalogue.viewer_paths(
        arguments["simdir"], arguments["ndown"], arguments["exclude"], arguments["require"]
    ):
        errors["ndown"] = "There are no netCDF files there to look at."
    return arguments, errors


def parse_names(text):
    """The variables to map, from the page's JSON list of names. Raises
    ValueError, in words for the user, for anything else."""
    try:
        names = json.loads(text or "[]")
        assert isinstance(names, list) and all(isinstance(name, str) for name in names)
    except (ValueError, AssertionError):
        raise ValueError("The variables could not be read.") from None
    return names


def parse_missing(text):
    """The numbers the user says are missing, from the page's JSON: rows of
    [from, to], each a number as text, and to empty for a single number.
    Returns [(low, high), ...]. Rows with nothing in them are ignored.
    Raises ValueError, in words for the user, for anything else."""
    if not text:
        return []
    try:
        rows = json.loads(text)
        assert isinstance(rows, list)
    except (ValueError, AssertionError):
        raise ValueError("The missing values could not be read.") from None
    ranges = []
    for number, row in enumerate(rows, 1):
        parts = [str(part).strip() for part in (list(row) + ["", ""])[:2]]
        if not any(parts):
            continue
        values = []
        for part in parts:
            if part == "":
                values.append(None)
                continue
            try:
                value = float(part)
            except ValueError:
                value = float("nan")
            if not math.isfinite(value):
                raise ValueError(f"Missing value {number}: {part!r} is not a number.")
            values.append(value)
        if values[0] is None:
            raise ValueError(f"Missing value {number}: fill in From, not only To.")
        ranges.append((values[0], values[0] if values[1] is None else values[1]))
    return ranges


def _number(text):
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


def _installed(*names):
    try:
        return all(importlib.util.find_spec(name) is not None for name in names)
    except (ImportError, ValueError):
        return False


def bokeh_js():
    """The path of BokehJS of the bokeh installed, served to the page so it
    needs no internet."""
    from bokeh.util import paths

    # bokehjsdir before bokeh 3.4
    folder = paths.bokehjs_path() if hasattr(paths, "bokehjs_path") else paths.bokehjsdir()
    return os.path.join(str(folder), "js", "bokeh.min.js")


class ViewerSteps:
    """The View simulation output steps of oceanval.app.App: its lock,
    _notify, view, action and the rest are the App's own."""

    def _init_viewer(self):
        self.viewer_setup_form = default_setup_form()
        # the numbers the user says are missing, as (low, high) ranges
        self.viewer_missing = []
        # how far the variables are being read: {"phase", "done", "total"}
        self.viewer_scan = None
        self.viewer_catalogue = None
        # how many catalogues have been read, which the page asks for again when it changes
        self.viewer_catalogue_number = 0
        # how far the times of the files not read at first are being read,
        # {"done", "total"} (or "error"), None when they are all in; and how
        # many times they have all come in
        self.viewer_times = None
        self.viewer_times_number = 0
        # bumped whenever what is being read is no longer wanted: a thread
        # reading for an earlier number gives up
        self._viewer_number = 0
        # the last guess at the missing value, as (the simulation asked about, the answer)
        self._viewer_guessed = None

    def _viewer_state(self):
        return {
            "scan": dict(self.viewer_scan) if self.viewer_scan else None,
            "catalogue_number": self.viewer_catalogue_number if self.viewer_catalogue else None,
            "times": dict(self.viewer_times) if self.viewer_times else None,
            "times_number": self.viewer_times_number,
            # coastlines on a grid need geoviews; on an FVCOM mesh, cartopy only (viewer_plots.mesh_coastline)
            "coast": _installed("geoviews", "cartopy"),
            "coast_mesh": _installed("cartopy", "shapely"),
        }

    def _viewer_drop(self):
        """Stop whatever is being read, and let go of the files: returns the
        catalogue, which the caller closes once it has let go of the lock.
        Called with the lock held."""
        self._viewer_number += 1
        catalogue, self.viewer_catalogue = self.viewer_catalogue, None
        if catalogue is not None:
            catalogue.stop_times()
        self.viewer_scan = None
        self.viewer_times = None
        return catalogue

    def _viewer_current(self, number):
        return number == self._viewer_number and not self.closed.is_set()

    # ---- the setup page ----

    def _viewer_form(self, form):
        from oceanval.app import _form

        return _form(form, default_setup_form())

    def _viewer_setup(self, form):
        """Continue from the setup page: read the variables of each file
        type, in a thread, then show the viewer."""
        with self._lock:
            if self.view != "setup":
                return 409, {"ok": False, "error": "This step is over."}
            self.viewer_setup_form = self._viewer_form(form)
            arguments, errors = check_setup(self.viewer_setup_form, self.cwd)
            if errors:
                self._notify()
                return 400, {"ok": False, "errors": errors}
            old = self._viewer_drop()
            number = self._viewer_number
            self._start_cdo_check([arguments])
            self.setup_error = None
            self.viewer_missing = arguments["missing"]
            self.viewer_scan = {"phase": "files", "done": 0, "total": 0}
            self.view = "viewer_scanning"
            self._notify()
        if old is not None:
            old.close()
        threading.Thread(
            target=self._viewer_read, args=(number, arguments), name="oceanval-viewer", daemon=True
        ).start()
        return 200, {"ok": True}

    def _viewer_read(self, number, arguments):
        """Read what the files hold: the variables of each file type and the
        times of its first file, then the times of the other files, in the
        background, while the viewer is used."""

        def progress(**news):
            with self._lock:
                if self._viewer_current(number):
                    self.viewer_scan = news
                    self._notify()

        filters = {key: " ".join(arguments[key] or ()) for key in ("exclude", "require")}
        try:
            found = viewer_catalogue.scan(
                arguments["simdir"], arguments["ndown"], **filters,
                progress=progress, stopped=lambda: not self._viewer_current(number), wait=False,
            )
        except viewer_catalogue.Stopped:
            return
        except Exception as error:  # noqa: BLE001 - told to the page
            traceback.print_exc()
            with self._lock:
                if self._viewer_current(number):
                    self.view = "setup"
                    self.viewer_scan = None
                    self.setup_error = f"The files could not be read: {error}"
                    self._notify()
            return
        with self._lock:
            if not self._viewer_current(number):
                kept = False
            else:
                kept = True
                self.viewer_catalogue = found
                self.viewer_catalogue_number += 1
                self.view = "viewer"
                self.viewer_scan = None
                self.viewer_times = None if found.times_complete else {
                    "done": 0, "total": sum(len(stream["files"]) for stream in found.streams),
                }
                self._notify()
        if not kept:
            if found is not None:
                found.close()
            return
        # listed in the simulation box's "Used before" from now on, as an analysed or matched up one is
        user_cache.record_sim_dir(arguments["simdir"])
        if found.times_complete:
            return
        # the page is told how far it has got, a few times a second, and when they are all in
        last = [0.0]

        def times_progress(done, total):
            with self._lock:
                if not self._viewer_current(number):
                    return
                self.viewer_times = {"done": done, "total": total}
                now = time.monotonic()
                if done >= total or now - last[0] >= TIMES_NEWS:
                    last[0] = now
                    self._notify()

        try:
            found.read_times(times_progress, stopped=lambda: not self._viewer_current(number))
        except viewer_catalogue.Stopped:
            return
        except Exception as error:  # noqa: BLE001 - told to the page
            traceback.print_exc()
            with self._lock:
                if self._viewer_current(number):
                    self.viewer_times = {
                        "done": 0, "total": 0, "error": f"The times of the files could not be read: {error}",
                    }
                    self._notify()
            return
        with self._lock:
            if self._viewer_current(number):
                self.viewer_times = None
                self.viewer_times_number += 1
                self._notify()

    def _viewer_back(self, sent):
        """Back from a viewing step: from the setup page to the landing page,
        keeping its boxes as they were left, and from the viewer (or while it
        is being read) to the setup page, stopping what is being read. Called
        with the lock held; returns the catalogue to close, or None, and
        whether it went."""
        if self.view == "setup":
            if isinstance(sent.get("form"), dict):
                self.viewer_setup_form = self._viewer_form(sent["form"])
            self.view = "home"
            self._notify()
            return None, True
        if self.view in ("viewer_scanning", "viewer"):
            old = self._viewer_drop()
            self.view = "setup"
            self._notify()
            return old, True
        return None, False

    # ---- the viewer ----

    def viewer_summary(self):
        found = self.viewer_catalogue
        if found is None:
            return {"ok": False, "error": "Nothing has been read yet."}
        return {"ok": True, "number": self.viewer_catalogue_number, **found.summary()}

    def viewer_missing_guess(self, query):
        """What the setup page's missing value rows could start with for the
        simulation as typed in (see oceanval.viewer_missing.guess): {"value",
        "ersem"}. The last answer is kept, as the page asks again whenever a
        box is edited."""
        from oceanval import viewer_missing
        from oceanval.app import _path

        simdir = _path(query["simdir"].strip(), self.cwd) if query.get("simdir", "").strip() else ""
        try:
            # a file has no directories down
            ndown = 0 if os.path.isfile(simdir) else int(query.get("ndown", ""))
            assert ndown >= 0 and simdir
        except (ValueError, AssertionError):
            return {"value": None, "ersem": False}
        key = (simdir, ndown, query.get("exclude", ""), query.get("require", ""))
        with self._lock:
            if self._viewer_guessed is not None and self._viewer_guessed[0] == key:
                return self._viewer_guessed[1]
        answer = viewer_missing.guess(*key)
        with self._lock:
            self._viewer_guessed = (key, answer)
        return answer

    def viewer_plot(self, query):
        """The map the page asks for: {"ok", "item" (a Bokeh json_item),
        "notes", "clim" (the colour scale's limits), "when", "file"}."""
        from oceanval import viewer_plots

        found = self.viewer_catalogue
        if found is None:
            return {"ok": False, "error": "Nothing has been read yet."}
        stream = query.get("stream", "0")
        step, level = int(query.get("step") or 0), int(query.get("level") or 0)
        try:
            names = parse_names(query.get("vars"))
            about = found.combined(stream, names)
            name = about["name"]
            data, when = found.field(stream, names, step, level, self.viewer_missing)
            n_missing = data.attrs.get("n_missing", 0)
            item, notes, clim = viewer_plots.map_plot(
                data, about, name,
                cmap=query.get("cmap") or None,
                vmin=_number(query.get("vmin")),
                vmax=_number(query.get("vmax")),
                log=query.get("log") == "1",
                coast=query.get("coast") == "1",
                mesh=found.mesh(stream) if about.get("mesh") else None,
            )
            if about["units_differ"]:
                notes.append(
                    f"The units differ ({', '.join(u or 'none' for u in about['units_differ'])}), so the sum may not mean anything."
                )
            if n_missing and not data.notnull().any():
                notes.append("Every cell is missing, so there is nothing to show. Check the missing values.")
            record = found.streams[int(stream)]
            _, index, _ = record["steps"][min(max(step, 0), len(record["steps"]) - 1)]
            # said to the user, as the coastline they asked for is not all there
            warning = next((note for note in notes if note.startswith(viewer_plots.COAST_FAILED)), None)
            return {
                "ok": True, "item": item, "notes": notes, "clim": clim, "when": when, "n_missing": n_missing,
                "warning": warning,
                "file": os.path.relpath(record["files"][index], found.simdir),
            }
        except ValueError as error:
            # said in words for the user
            return {"ok": False, "error": str(error)}
        except Exception as error:  # noqa: BLE001 - told to the page
            traceback.print_exc()
            return {"ok": False, "error": f"{type(error).__name__}: {error}"}

    def viewer_limits(self, query):
        """The range of the variable (or sum) over the whole run, or the years
        chosen, so an animation can keep one colour scale: {"ok", "vmin",
        "vmax", "symmetric"}."""
        found = self.viewer_catalogue
        if found is None:
            return {"ok": False, "error": "Nothing has been read yet."}
        try:
            names = parse_names(query.get("vars"))
            found.combined(query.get("stream", "0"), names)
            got = found.limits(
                query.get("stream", "0"), names, int(query.get("level") or 0), self.viewer_missing,
                first=int(query.get("first") or 0), last=int(query["last"]) if query.get("last") else None,
            )
        except ValueError as error:
            return {"ok": False, "error": str(error)}
        except Exception as error:  # noqa: BLE001 - told to the page
            traceback.print_exc()
            return {"ok": False, "error": f"{type(error).__name__}: {error}"}
        if got is None:
            return {"ok": False, "error": "Every cell is missing, so there is nothing to show. Check the missing values."}
        return {"ok": True, "vmin": got[0], "vmax": got[1], "symmetric": got[2]}
