"""The oceanval window's View simulation output steps, which oceanval.app.App
takes on.

The viewer page (App.view "viewer") shows up to VIEWERS_MOST simulations side
by side, each in a panel (ViewerSlot) with its own setup box: the simulation
(as Analyze's: the same directories-down box, filled in from the folders'
layout, and file filters) and the numbers to treat as missing. A panel goes
through these states (ViewerSlot.view):

    "setup"             no simulation read yet, or being set up again
    "viewer_scanning"   the variables of each file type being read (see
                        oceanval.viewer_catalogue.scan)
    "viewer"            the viewer: the variables, searched, mapped one at a
                        time or summed, at the time, level and colours chosen,
                        while the times of the files are read in the
                        background, in a process of their own

Panel 1 is there from the start; "add" and "remove" (not 1) add and remove the
others. Back goes from the viewer to the landing page, stopping every reading
and keeping each panel's form, for the viewer to start from as it was left.
Nothing is written but the cache of the files' times (see oceanval.viewer_times).

The page asks for each map (/api/viewer/plot), the colour scale of an
animation (/api/viewer/limits) and what the files hold
(/api/viewer/catalogue) as it needs them, each for its panel (a "viewer"
number in the query), and is drawn by BokehJS, which the window serves
(/bokeh.min.js), so that nothing is loaded from elsewhere.
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
# the most simulations the viewer shows at once, side by side, each in a panel (see ViewerSlot)
VIEWERS_MOST = 4
# how long, in seconds, a panel waits for its turn to read the times of its files before looking again
TURN_WAIT = 0.2


def default_setup_form():
    """The setup page's boxes for viewing, as they start: the simulation
    step's, and the numbers to treat as missing, as the page's JSON rows of
    [from, to] (see parse_missing)."""
    from oceanval.app import default_setup_form as setup_form

    # the common words for files that are not the output: they start in the Skip list, and can be removed
    return dict(setup_form(), exclude="restart spinup", missing="", thredds="")


def check_setup(form, cwd, find=None):
    """The setup page's boxes as (arguments, errors), as
    oceanval.app.check_setup gives them, without the domain and the years,
    which viewing has no use for, and with "missing", the ranges to treat as
    missing (see parse_missing). A directory with no files to look at is an
    error on the directories down.

    find(arguments), if given, says what the simulation's files are, in place
    of walking its directories: the window passes the live check's listing
    (app.App._simulation_files), which has them already. The files used are
    put in arguments["files"], so the caller need not find them a third
    time."""
    from oceanval.app import _form
    from oceanval.app import check_setup as check
    from oceanval.app import _path

    form = _form(form, default_setup_form())
    # a file is used as it is: checked as its directory, with no directories down
    url = viewer_catalogue.is_url(form["simdir"])
    path = form["simdir"] if url else _path(form["simdir"], cwd) if form["simdir"] else ""
    is_file = bool(path) and (url or os.path.isfile(path))
    # a web address is used as it is, as a file is: checked as the working directory, with none down
    shared = dict(form, simdir=cwd if url else os.path.dirname(path), ndown="0") if is_file else form
    arguments, errors = check(dict(shared, start="0", end="0"), cwd)
    if is_file:
        arguments["simdir"] = path
    elif form["thredds"]:
        errors["simdir"] = "A THREDDS address must start with https:// and end in .nc."
    for name in ("start", "end"):
        arguments.pop(name, None)
        errors.pop(name, None)
    try:
        arguments["missing"] = parse_missing(form["missing"])
    except ValueError as error:
        errors["missing"] = str(error)
    if not errors:
        arguments["files"] = (find or _walk_for)(arguments)
        if not arguments["files"]:
            errors["ndown"] = "There are no netCDF files there to look at."
    return arguments, errors


def _walk_for(arguments):
    """The simulation's files, by walking its directories: what check_setup
    does without a find of its own."""
    return viewer_catalogue.viewer_paths(
        arguments["simdir"], arguments["ndown"], arguments["exclude"], arguments["require"]
    )


def _file_types(paths):
    """How many kinds of file paths hold (see create_recipes._file_pattern),
    which is how many the variables are read from. String work only."""
    from oceanval.create_recipes import _file_pattern

    return len({_file_pattern(path) for path in paths})


def _guess_key(arguments):
    """The simulation a missing value search is of (see viewer_missing_guess),
    from check_setup's arguments: the same words, however they were spaced in
    the boxes, so a reading of these very files is recognised."""
    return (
        arguments["simdir"], int(arguments["ndown"] or 0),
        tuple(arguments["exclude"] or ()), tuple(arguments["require"] or ()),
    )


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


class ViewerSlot:
    """One panel of the viewer: the simulation it shows, the setup it was
    given, and what is read of its files. The App keeps the panels by their
    numbers (see ViewerSteps._init_viewer)."""

    def __init__(self, number, form=None):
        self.number = number
        # "setup" until the simulation is read (and while it is set up again),
        # then "viewer_scanning", then "viewer"
        self.view = "setup"
        self.form = form if form is not None else default_setup_form()
        # what went wrong reading the simulation, for the panel's setup box
        self.error = None
        # the numbers the user says are missing, as (low, high) ranges
        self.missing = []
        # how far the variables are being read: {"phase", "done", "total"}
        self.scan = None
        self.catalogue = None
        # how many catalogues have been read, which the page asks for again when it changes
        self.catalogue_number = 0
        # how far the times of the files not read at first are being read,
        # {"done", "total"} (or "error"), None when they are all in; and how
        # many times they have all come in
        self.times = None
        self.times_number = 0
        # bumped whenever what is being read is no longer wanted: a thread
        # reading for an earlier number gives up
        self.reading = 0
        # the guess at the missing value (see viewer_missing_guess): the
        # simulation asked about, whether it is still being looked for, and
        # the answer. guess_number says which is wanted
        self.guess = None
        self.guess_number = 0
        # what that search read of the output, as (the simulation, the
        # Catalogue), for Continue to carry on with rather than read again
        # (see ViewerSteps._viewer_taken). Closed by _viewer_drop if not taken
        self.scanned = None

    def state(self):
        return {
            "id": self.number,
            "view": self.view,
            "form": self.form,
            "error": self.error,
            "scan": dict(self.scan) if self.scan else None,
            "catalogue_number": self.catalogue_number if self.catalogue else None,
            "times": dict(self.times) if self.times else None,
            "times_number": self.times_number,
        }


def _panel_number(value):
    """The panel a request names, 1 to VIEWERS_MOST, or None."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= VIEWERS_MOST else None


class ViewerSteps:
    """The View simulation output steps of oceanval.app.App: its lock,
    _notify, view, action and the rest are the App's own. The viewer's
    panels (ViewerSlot) are kept in self.viewers by number."""

    def _init_viewer(self):
        # the panels, {number: ViewerSlot}: panel 1 is always there, and no more than VIEWERS_MOST
        self.viewers = {}
        # the forms of the panels Back left, by number, for the viewer to start from as it was left
        self.viewer_kept = {}
        # one panel's times are read at a time: each reading starts processes of its own
        self._viewer_turn = threading.Lock()
        # one map is drawn at a time: geoviews keeps state of its own between maps
        self._viewer_drawing = threading.Lock()

    def _viewer_state(self):
        return {
            "panels": [self.viewers[number].state() for number in sorted(self.viewers)],
            "most": VIEWERS_MOST,
            # coastlines need cartopy only, on a grid as on an FVCOM mesh (viewer_plots.coastline_overlay)
            "coast": _installed("cartopy", "shapely"),
        }

    def _viewer_enter(self):
        """Show the viewer: panel 1, and each panel Back kept, with its form as
        it was left (not its reading, which is done again). Called with the
        lock held."""
        numbers = sorted({1, *self.viewer_kept})[:VIEWERS_MOST]
        self.viewers = {number: ViewerSlot(number, self.viewer_kept.get(number)) for number in numbers}
        self.viewer_kept = {}

    def _viewer_slot_of(self, value):
        """The panel a request names by its number, or None."""
        number = _panel_number(value)
        return self.viewers.get(number) if number is not None else None

    def _viewer_drop(self, slot):
        """Stop whatever is being read for a panel, and let go of its files:
        returns the catalogue, which the caller closes once it has let go of
        the lock. Called with the lock held."""
        slot.reading += 1
        # a guess at the missing value still going gives up: its answer is not
        # wanted any more, and it holds the netCDF lock the reading below needs
        slot.guess_number += 1
        slot.guess = None
        # what that search read and nobody took over with (see _viewer_taken)
        scanned, slot.scanned = slot.scanned, None
        if scanned is not None:
            scanned[1].close()
        catalogue, slot.catalogue = slot.catalogue, None
        if catalogue is not None:
            catalogue.stop_times()
        slot.scan = None
        slot.times = None
        return catalogue

    def _viewer_drop_all(self):
        """Stop every panel's reading and let go of their files. Called with
        the lock held; returns the catalogues, which the caller closes."""
        dropped = [self._viewer_drop(slot) for slot in self.viewers.values()]
        return [catalogue for catalogue in dropped if catalogue is not None]

    def _viewer_current(self, slot, number):
        """Whether a thread reading for slot, under number, is still wanted:
        the panel is still there, and nothing has been dropped since."""
        return (
            number == slot.reading and self.viewers.get(slot.number) is slot and not self.closed.is_set()
        )

    # ---- the panels ----

    def viewer_add(self):
        """A new panel, with the lowest number free, up to VIEWERS_MOST. The
        steps' reply is whether it was added."""
        with self._lock:
            if self.action != VIEWER or self.view != "viewer" or len(self.viewers) >= VIEWERS_MOST:
                return False
            number = next(n for n in range(1, VIEWERS_MOST + 1) if n not in self.viewers)
            self.viewers[number] = ViewerSlot(number)
            self._notify()
            return True

    def viewer_remove(self, value):
        """Remove a panel, letting go of its simulation. Panel 1 stays. The
        steps' reply is whether it was removed."""
        number = _panel_number(value)
        with self._lock:
            if number in (None, 1) or number not in self.viewers or self.view != "viewer":
                return False
            old = self._viewer_drop(self.viewers.pop(number))
            self._notify()
        # outside the lock: it waits for any file being read
        if old is not None:
            old.close()
        return True

    # ---- the setup box of a panel ----

    def _viewer_form(self, form):
        from oceanval.app import _form

        return _form(form, default_setup_form())

    def _viewer_setup(self, form):
        """Continue from a panel's setup box: read the variables of each file
        type, in a thread, then show the panel's simulation. Nothing here
        looks for the files again: they come from the live check's listing,
        and when the missing value search has already read them (the usual
        way), its reading is taken over rather than done a second time."""
        form = form if isinstance(form, dict) else {}
        with self._lock:
            if self.view != "viewer":
                return 409, {"ok": False, "error": "This step is over."}
            slot = self._viewer_slot_of(form.get("viewer"))
            if slot is None:
                return 409, {"ok": False, "error": "That panel is not there."}
            slot.form = self._viewer_form(form)
            arguments, errors = check_setup(slot.form, self.cwd, find=self._found_files)
            if errors:
                self._notify()
                return 400, {"ok": False, "errors": errors}
            # taken before _viewer_drop, which closes a reading nobody is carrying on with
            scanned = self._viewer_taken(slot, arguments)
            old = self._viewer_drop(slot)
            number = slot.reading
            self._start_cdo_check([arguments])
            slot.error = None
            slot.missing = arguments["missing"]
            # the files are known, so the only reading left is the variables of each file type
            slot.scan = {"phase": "variables", "done": 0, "total": _file_types(arguments["files"])}
            slot.view = "viewer_scanning"
            self._notify()
        if old is not None:
            old.close()
        threading.Thread(
            target=self._viewer_read, args=(slot, number, arguments, scanned),
            name="oceanval-viewer", daemon=True,
        ).start()
        return 200, {"ok": True}

    def _found_files(self, arguments):
        """The simulation's files, from the listing the live check made
        however long ago (see app.App._simulation_files): what check_setup
        decides "There are no netCDF files there to look at." by, and what the
        reading below is given."""
        return self._simulation_files(
            arguments["simdir"], arguments["ndown"],
            arguments["exclude"], arguments["require"], kept=None,
        )

    def _viewer_taken(self, slot, arguments):
        """The reading the missing value search did for this panel, if it is
        of this very simulation, taken over so it is not done again (see
        _viewer_guess_thread). Called with the lock held; what is left behind
        is closed by _viewer_drop."""
        scanned, slot.scanned = slot.scanned, None
        if scanned is None:
            return None
        key, catalogue = scanned
        if key == _guess_key(arguments):
            return catalogue
        catalogue.close()
        return None

    def _viewer_read(self, slot, number, arguments, scanned=None):
        """Read what the files hold: the variables of each file type and the
        times of its first file, then the times of the other files, in the
        background, while the panel is used.

        scanned, if given, is that reading already done by the missing value
        search (see _viewer_taken), and is used as it is."""

        def progress(**news):
            with self._lock:
                if self._viewer_current(slot, number):
                    slot.scan = news
                    self._notify()

        filters = {key: " ".join(arguments[key] or ()) for key in ("exclude", "require")}
        found = None
        try:
            # the missing value search read these very files already: carry on with it
            found = scanned if scanned is not None else viewer_catalogue.scan(
                arguments["simdir"], arguments["ndown"], **filters,
                progress=progress, stopped=lambda: not self._viewer_current(slot, number), wait=False,
                paths=arguments["files"],
            )
        except viewer_catalogue.Stopped:
            return
        except Exception as error:  # noqa: BLE001 - told to the page
            traceback.print_exc()
            with self._lock:
                if self._viewer_current(slot, number):
                    slot.view = "setup"
                    slot.scan = None
                    slot.error = f"The files could not be read: {error}"
                    self._notify()
            return
        with self._lock:
            if not self._viewer_current(slot, number):
                kept = False
            else:
                kept = True
                slot.catalogue = found
                slot.catalogue_number += 1
                slot.view = "viewer"
                slot.scan = None
                slot.times = None if found.times_complete else {
                    "done": 0, "total": sum(len(stream["files"]) for stream in found.streams),
                }
                self._notify()
        if not kept:
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
                if not self._viewer_current(slot, number):
                    return
                slot.times = {"done": done, "total": total}
                now = time.monotonic()
                if done >= total or now - last[0] >= TIMES_NEWS:
                    last[0] = now
                    self._notify()

        if not self._viewer_their_turn(slot, number):
            return
        try:
            found.read_times(times_progress, stopped=lambda: not self._viewer_current(slot, number))
        except viewer_catalogue.Stopped:
            return
        except Exception as error:  # noqa: BLE001 - told to the page
            traceback.print_exc()
            with self._lock:
                if self._viewer_current(slot, number):
                    slot.times = {
                        "done": 0, "total": 0, "error": f"The times of the files could not be read: {error}",
                    }
                    self._notify()
            return
        finally:
            self._viewer_turn.release()
        with self._lock:
            if self._viewer_current(slot, number):
                slot.times = None
                slot.times_number += 1
                self._notify()

    def _viewer_their_turn(self, slot, number):
        """Wait for this panel's turn to read the times of its files: one
        panel's at a time, as each reading starts processes of its own. Returns
        whether it is the panel's turn, and False (holding nothing) once the
        reading is no longer wanted."""
        while not self._viewer_turn.acquire(timeout=TURN_WAIT):
            if not self._viewer_current(slot, number):
                return False
        return True

    def _viewer_back(self, sent):
        """Back from the viewer to the landing page: every panel's reading is
        stopped, and each panel's form is kept for the viewer to start from as
        it was left. The forms the page sent (its unapplied boxes, by panel
        number) are kept where given. Called with the lock held; returns the
        catalogues to close, and whether it went."""
        if self.view != "viewer":
            return [], False
        drafts = sent.get("forms") if isinstance(sent.get("forms"), dict) else {}
        for number, slot in self.viewers.items():
            draft = drafts.get(str(number))
            self.viewer_kept[number] = self._viewer_form(draft) if isinstance(draft, dict) else slot.form
        old = self._viewer_drop_all()
        self.viewers = {}
        self.view = "home"
        self._notify()
        return old, True

    # ---- the viewer ----

    def viewer_summary(self, query=None):
        slot = self._viewer_slot_of((query or {}).get("viewer"))
        found = slot.catalogue if slot is not None else None
        if found is None:
            return {"ok": False, "error": "Nothing has been read yet."}
        return {"ok": True, "number": slot.catalogue_number, **found.summary()}

    def viewer_missing_guess(self, query):
        """What a panel's missing value rows could start with for the
        simulation as typed in (see oceanval.viewer_missing.guess):
        {"status": "reading" | "done", "value", "ersem"}.

        The guess is made in a thread, not here: it opens a file of each file
        type, so holding the request (and oceanval.netcdf_lock.LOCK) for it
        would stall the rest of the window. The page asks again while the
        status is "reading". The answer is kept, as the page asks again
        whenever a box is edited, and a guess the user has moved on from gives
        up (see guess_number, bumped by _viewer_drop).

        What it reads of the output is kept too, for Continue to carry on with
        (see _viewer_taken): it is the very reading the panel needs."""
        from oceanval.app import _path

        slot = self._viewer_slot_of(query.get("viewer"))
        if slot is None:
            return {"status": "done", "value": None, "ersem": False}
        text = query.get("simdir", "").strip()
        simdir = text if viewer_catalogue.is_url(text) else _path(text, self.cwd) if text else ""
        try:
            # a file has no directories down
            ndown = 0 if viewer_catalogue.is_url(simdir) or os.path.isfile(simdir) else int(query.get("ndown", ""))
            assert ndown >= 0 and simdir
        except (ValueError, AssertionError):
            return {"status": "done", "value": None, "ersem": False}
        exclude, require = query.get("exclude", ""), query.get("require", "")
        # the same words however they were spaced, so Continue recognises this reading
        key = (simdir, ndown, tuple(exclude.split()), tuple(require.split()))
        with self._lock:
            guess = slot.guess
            if guess is not None and guess["key"] == key:
                return {"status": guess["status"], **guess["answer"]}
            slot.guess_number += 1
            number = slot.guess_number
            slot.guess = {
                "key": key, "status": "reading", "answer": {"value": None, "ersem": False},
            }
            threading.Thread(
                target=self._viewer_guess_thread, args=(slot, number, key),
                name="oceanval-viewer-missing", daemon=True,
            ).start()
            return {"status": "reading", "value": None, "ersem": False}

    def _viewer_guess_thread(self, slot, number, key):
        """Look for the missing value of the simulation key names, giving up
        if it is no longer the one asked about (see viewer_missing_guess)."""
        from oceanval import viewer_missing

        simdir, ndown, exclude, require = key
        wanted = lambda: number == slot.guess_number and self.viewers.get(slot.number) is slot  # noqa: E731
        answer = viewer_missing.guess(
            simdir, ndown, " ".join(exclude), " ".join(require),
            stopped=lambda: not wanted(),
            keep=lambda catalogue: self._viewer_keep(slot, number, key, catalogue),
            # the files the live check found, however long ago, not the directories walked again
            paths=self._simulation_files(simdir, ndown, exclude, require, kept=None) or None,
        )
        with self._lock:
            if not wanted() or self.closed.is_set():
                return
            slot.guess = {"key": key, "status": "done", "answer": answer}
            self._notify()

    def _viewer_keep(self, slot, number, key, catalogue):
        """Keep what a missing value search read, for Continue to carry on
        with. Returns whether it was taken: if it was not, the search closes
        it. Anything kept and not taken is closed by _viewer_drop."""
        with self._lock:
            if (
                number != slot.guess_number or self.closed.is_set()
                or self.viewers.get(slot.number) is not slot
            ):
                return False
            older, slot.scanned = slot.scanned, (key, catalogue)
        if older is not None:
            older[1].close()
        return True

    def viewer_plot(self, query):
        """The map the page asks for: {"ok", "item" (a Bokeh json_item),
        "notes", "clim" (the colour scale's limits), "when", "file"}."""
        from oceanval import viewer_plots

        slot = self._viewer_slot_of(query.get("viewer"))
        found = slot.catalogue if slot is not None else None
        if found is None:
            return {"ok": False, "error": "Nothing has been read yet."}
        stream = query.get("stream", "0")
        step, level = int(query.get("step") or 0), int(query.get("level") or 0)
        try:
            names = parse_names(query.get("vars"))
            about = found.combined(stream, names)
            name = about["name"]
            data, when = found.field(stream, names, step, level, slot.missing)
            n_missing = data.attrs.get("n_missing", 0)
            with self._viewer_drawing:
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
                "file": viewer_catalogue.relative(record["files"][index], found.simdir),
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
        slot = self._viewer_slot_of(query.get("viewer"))
        found = slot.catalogue if slot is not None else None
        if found is None:
            return {"ok": False, "error": "Nothing has been read yet."}
        try:
            names = parse_names(query.get("vars"))
            found.combined(query.get("stream", "0"), names)
            got = found.limits(
                query.get("stream", "0"), names, int(query.get("level") or 0), slot.missing,
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
