"""The oceanval window's Analyze steps, which oceanval.app.App takes on.

An analysis starts from the landing page's Analyze tile, and goes through
these views:

    "setup"              the simulation (the setup page, without the domain)
    "analyze_variables"  the variables to analyse, as "Your name" and the
                         model variable holding it, which OceanVal fills in
                         first with those it knows
    "analyze_files"      the files holding them, with the dates missing from
                         them (a model_check.ModelCheck), asking first, if
                         their times look like the end of the period each
                         mean covers, whether to shift them (see
                         oceanval.time_stamps)
    "analyze_options"    the analyses, each in a pop-out: climatology,
                         phenology, the long-term and seasonal time
                         series (which share the regions and area they are
                         of, and their stations), maps of temporal changes
                         (a historical and one or two future periods), and
                         transects (as many as are wanted, each a north-south
                         or east-west line)
    "running"            the script the window writes, which calls
                         oceanval.analyze(), as a matchup script is run
    "script_saved"       the script written, to be run later, when the user
                         chose to save it rather than run it

Back goes a step the other way from each, keeping what was entered. The
forms and their checks are in oceanval.analysis_forms.
"""

import atexit
import os
import shutil
import tempfile
import threading

import pandas as pd

from oceanval import (
    analysis,
    analysis_examples,
    analysis_forms,
    missing_probe,
    model_check,
    recipes_gui,
    sim_compare_forms,
    time_stamps,
)
from oceanval import time_res as time_resolution

# the id the check of the dates of an analysis's files is made for (see
# model_check.ModelCheck), which a matchup's question never has
CHECK_ID = "analyze"
# the action of the Compare simulations steps (see oceanval.sim_compare_app), which
# end in this module's options step
SIMCOMPARE = "simcompare"

KINDS = ("climatology", "phenology", "long_term", "seasonal", "map_changes", "transects")
# the pop-outs that choose the surface and/or the water column, with the same boxes
LAYERED_KINDS = ("climatology", "long_term", "seasonal")
# those that need the cell thickness: the water column, and a vertical transect
THICKNESS_KINDS = LAYERED_KINDS + ("map_changes", "transects")
# what each is called when it is asked for
LABELS = {
    "climatology": "climatology",
    "phenology": "phenology",
    "long_term": "long-term time series",
    "seasonal": "seasonal cycles",
    "map_changes": "map changes",
    "transects": "transects",
}

# what the window says of raw FVCOM output
FVCOM_NOTE = (
    "This is raw FVCOM output, mapped on its own mesh: the cell thickness of the water column is worked out from "
    "the sigma levels (siglev), h and zeta, the spatial means are weighted by the areas of the mesh's cells, and "
    "transects are interpolated from the mesh."
)


def _same_simulation(old, new):
    keys = ("simdir", "ndown", "exclude", "require")
    return old is not None and all(old.get(key) == new.get(key) for key in keys)


def _placed_key(placed):
    """What the files and dates of the variables depend on."""
    return [(row["name"], row["model_variable"], row["pattern"]) for row in placed or []]


class AnalyzeSteps:
    """The Analyze steps of oceanval.app.App: its lock, _notify, view,
    action and the rest are the App's own."""

    def _init_analyze(self):
        # the simulation step's boxes for an analysis, and the arguments
        # they were turned into
        self.analyze_setup_form = analysis_forms.default_setup_form(self.cwd)
        self.analyze_setup = None
        # the simulation's streams as they are read: {"status": "reading",
        # "done" or "failed", "progress", "error", "streams", "picker"}
        self.analyze_found = None
        self._analyze_reading = 0
        # the variables step's rows as last left, and how a change of
        # simulation changed them
        self.analyze_rows = None
        self.analyze_refilled = False
        # the variables to analyse, as analysis_forms.check_rows placed them
        self.analyze_placed = None
        # whether the files' times look like ends, and what was chosen:
        # {"status": "checking", "asked" or "none", "rows", "choice"}
        self.analyze_shift = None
        self._analyze_shift_number = 0
        # the check of the dates of the files, and the shift it was made with
        self.analyze_check = None
        self._analyze_check_shift = None
        # the folder the check's times are kept in for a run (see _analyze_keep_times)
        self.analyze_times_dir = None
        # each pop-out's boxes as last confirmed, and what they chose
        self.analyze_forms = {kind: None for kind in KINDS}
        self.analyze_chosen = {kind: None for kind in KINDS}
        self.analyze_thickness = None
        # the regions, lon/lat box and stations of the time series, as the
        # boxes last left, and what they were checked to
        self.analyze_area = analysis_forms.default_area()
        # the number of cores, as typed: there is no default
        self.analyze_cores = ""
        # what to treat as missing, as the boxes were left; OceanVal fills
        # them in for an ERSEM simulation until the user has set them
        self.analyze_missing = {"from": "", "to": ""}
        self._analyze_missing_set = False
        # whether the boxes hold a value found in the model's data (see
        # oceanval.missing_probe), which the user must check
        self.analyze_missing_probed = False
        self._analyze_probe_number = 0
        # whether the report has code to reproduce its figures, "yes" or
        # "no": not required, and "no" to start with
        self.analyze_figure_code = "no"

    # ---- the simulation ----

    def _analyze_setup(self, form):
        """The simulation step, for an analysis."""
        with self._lock:
            if self.view != "setup":
                return 409, {"ok": False, "error": "This step is over."}
            self.analyze_setup_form = self._analyze_form(form)
            arguments, errors = analysis_forms.check_setup(self.analyze_setup_form, self.cwd)
            if errors:
                self._notify()
                return 400, {"ok": False, "errors": errors}
            self.setup_error = None
            same = _same_simulation(self.analyze_setup, arguments) and (
                self.analyze_found is not None and self.analyze_found["status"] != "failed"
            )
            self.analyze_setup = arguments
            self._start_cdo_check([arguments])
            self.view = "analyze_variables"
            if not same:
                self._forget_after_simulation()
                self._read_simulation()
            self._notify()
        return 200, {"ok": True}

    def _forget_after_simulation(self):
        """What was worked out from the files of the simulation before, once
        another is chosen: the variables' files, the answer about the times and
        the check of the dates, which the variables step would otherwise keep
        as the rows are the same. Called with the lock held."""
        self.analyze_placed = None
        self.analyze_shift = None
        self._analyze_shift_number += 1
        self._analyze_probe_number += 1
        self._drop_analyze_check()
        # the pop-outs' boxes (years, thickness, regions, transects) and the missing
        # values were the other simulation's
        self.analyze_forms = {kind: None for kind in KINDS}
        self.analyze_chosen = {kind: None for kind in KINDS}
        self.analyze_thickness = None
        self.analyze_area = analysis_forms.default_area()
        self.analyze_times_dir = None
        self.analyze_missing = {"from": "", "to": ""}
        self._analyze_missing_set = False
        self.analyze_missing_probed = False

    def _analyze_form(self, form):
        from oceanval.app import _form

        return _form(form, analysis_forms.default_setup_form(self.cwd))

    def _read_simulation(self):
        """Read the simulation's streams in a thread, in place of any read
        before. Called with the lock held."""
        self._analyze_reading += 1
        number = self._analyze_reading
        self.analyze_found = {
            "status": "reading",
            "progress": {"done": 0, "total": 0},
            "error": None,
            "streams": None,
            "picker": None,
        }
        arguments = dict(self.analyze_setup)
        threading.Thread(
            target=self._read_streams, args=(number, arguments), name="oceanval-analyze-read", daemon=True
        ).start()

    def _read_streams(self, number, arguments):
        def progress(done, total):
            with self._lock:
                if number == self._analyze_reading and self.analyze_found is not None:
                    self.analyze_found["progress"] = {"done": done, "total": total}
                    self._notify()

        error = None
        found = picker = prefilled = preset_names = None
        ersem = False
        try:
            found = analysis.streams(
                arguments["simdir"],
                arguments["ndown"],
                arguments.get("exclude"),
                arguments.get("require"),
                progress=progress,
                stopped=lambda: number != self._analyze_reading,
            )
            if not found:
                error = "No netCDF files OceanVal could read were found."
            else:
                picker = analysis_forms.picker_state(found)
                ersem = recipes_gui.is_likely_nemo_ersem(picker["available"])
                # nothing is chosen for the user: the rows start empty (see
                # analysis_forms.prefill, which finds what OceanVal could fill in)
                prefilled = []
                # offered in "Your name"'s drop-down, with the model variable
                # the matchups would find for each
                preset_names = analysis_forms.presets(found, self.cwd)
        except Exception as problem:
            error = f"The simulation could not be read: {problem}"
        with self._lock:
            if number != self._analyze_reading:
                return
            if error:
                self.analyze_found = dict(self.analyze_found, status="failed", error=error)
            else:
                self.analyze_found = dict(
                    self.analyze_found,
                    status="done",
                    streams=found,
                    picker=picker,
                    presets=preset_names,
                    ersem=ersem,
                    # raw FVCOM output, worked out on its own mesh
                    fvcom=any(stream.get("fvcom") for stream in found),
                )
                if not self._analyze_missing_set:
                    # as in validate, ERSEM output has zeros where there is no water
                    self.analyze_missing = {"from": "0", "to": "0"} if ersem else {"from": "", "to": ""}
                    self.analyze_missing_probed = False
                self._fill_rows(prefilled)
            self._notify()

    def _fill_rows(self, prefilled):
        """The variables step's rows for a simulation just read: OceanVal's
        own, the first time, or, after a change of simulation, the rows the
        user gave kept and OceanVal's filled in again. Called with the lock
        held."""
        if self.analyze_rows is None:
            self.analyze_rows = list(prefilled)
            self.analyze_refilled = False
            return
        if not prefilled:
            # OceanVal fills nothing in: a row marked as filled in was set from
            # the drop-down of names, and is the user's own to correct
            self.analyze_rows = [row for row in self.analyze_rows if row["name"] or row["model_variable"]]
            self.analyze_refilled = False
            return
        kept = [row for row in self.analyze_rows if not row.get("filled") and (row["name"] or row["model_variable"])]
        names = {analysis.variable_key(row["name"]) for row in kept}
        refilled = [row for row in prefilled if analysis.variable_key(row["name"]) not in names]
        self.analyze_refilled = any(row.get("filled") for row in self.analyze_rows) or bool(refilled)
        self.analyze_rows = kept + refilled

    # ---- the variables ----

    def analyze_variables(self, rows):
        """The variables step's Continue, with its rows."""
        with self._lock:
            if self.view != "analyze_variables":
                return 409, {"ok": False, "error": "This step is over."}
            found = self.analyze_found
            if found is None or found["status"] != "done":
                return 409, {"ok": False, "error": "The simulation has not been read yet."}
            self.analyze_rows = analysis_forms.clean_rows(rows)
            rows = list(self.analyze_rows)
            streams = found["streams"]
        # outside the lock: choosing between streams reads their times
        placed, errors = analysis_forms.check_rows(rows, streams)
        if errors:
            return 400, {"ok": False, "errors": errors}
        with self._lock:
            if self.view != "analyze_variables":
                return 409, {"ok": False, "error": "This step is over."}
            changed = _placed_key(placed) != _placed_key(self.analyze_placed)
            self.analyze_placed = placed
            self.analyze_refilled = False
            self.view = "analyze_files"
            if changed or self.analyze_shift is None or self.analyze_check is None:
                self._drop_analyze_check()
                self._check_time_stamps()
            self._notify()
        return 200, {"ok": True}

    def _check_time_stamps(self):
        """Look, in a thread, at whether the times of the files look like
        the end of the period each mean covers, which is asked about before
        their dates are checked. Called with the lock held."""
        self._analyze_shift_number += 1
        number = self._analyze_shift_number
        kept = (self.analyze_shift or {}).get("choice")
        self.analyze_shift = {"status": "checking", "rows": [], "choice": kept}
        placed = [dict(row) for row in self.analyze_placed]
        threading.Thread(
            target=self._time_stamps_found, args=(number, placed), name="oceanval-analyze-stamps", daemon=True
        ).start()

    def _time_stamps_found(self, number, placed):
        chosen = pd.DataFrame(
            [
                {"variable": row["name"], "model_variable": row["model_variable"], "pattern": row["pattern"]}
                for row in placed
            ]
        )
        files = {row["pattern"]: row["files"] for row in placed}
        try:
            rows = time_stamps.check_rows(chosen, files, {row["name"]: row["name"] for row in placed})
        except Exception:
            rows = []
        with self._lock:
            if number != self._analyze_shift_number:
                return
            if rows:
                self.analyze_shift = dict(self.analyze_shift, status="asked", rows=rows)
                if self.analyze_shift["choice"] is not None:
                    # the answer given before, for the same files
                    self._start_analyze_check(self._shift_days())
            else:
                self.analyze_shift = {"status": "none", "rows": [], "choice": None}
                self._start_analyze_check(None)
            self._notify()

    def _shift_days(self):
        shift = self.analyze_shift or {}
        return time_stamps.SUGGESTED if shift.get("choice") == "shift" else None

    def analyze_time_shift(self, choice):
        """The answer to whether to shift the files' times: "shift" or
        "keep"."""
        with self._lock:
            shift = self.analyze_shift
            if self.view != "analyze_files" or shift is None or shift["status"] != "asked":
                return False
            if choice not in ("shift", "keep"):
                return False
            if shift["choice"] == choice and self.analyze_check is not None:
                return True
            shift["choice"] = choice
            self._drop_analyze_check()
            self._start_analyze_check(self._shift_days())
            self._notify()
            return True

    def _start_analyze_check(self, shift):
        """Check the dates of the files of the variables, with the times
        shifted by shift days, if any. Called with the lock held."""
        rows = []
        files = {}
        for row in self.analyze_placed:
            rows.append(
                {
                    "variable": row["name"],
                    "pattern": row["pattern"],
                    # worked out from the files' times (see model_check.check)
                    "time_res": None,
                    "groups": [
                        {
                            "years": [model_check.NO_START, model_check.NO_END],
                            "sources": [],
                            "gridded": False,
                        }
                    ],
                }
            )
            files.setdefault(row["pattern"], list(row["files"]))
        request = {
            "sim_dir": self.analyze_setup["simdir"],
            "years": [model_check.NO_START, model_check.NO_END],
            # the times of raw FVCOM output are read as matchup(fvcom=True) reads them
            "fvcom": self._analyze_fvcom(),
            "time_shift": shift or 0,
            "rows": rows,
            "files": files,
        }
        self.analyze_check = model_check.ModelCheck(CHECK_ID, request, self._notify)
        self._analyze_check_shift = shift
        threading.Thread(target=self.analyze_check.start, name="oceanval-analyze-check", daemon=True).start()

    def _analyze_keep_times(self):
        """Keep the times of the files the check read, which the run would
        otherwise read again (the check's folder is removed before it starts):
        copy them to a folder of its own, and return the path, or None. The
        folder goes with _sc_drop_times. Called with the lock held."""
        self._sc_drop_times()
        check = self.analyze_check
        index = getattr(check, "index", None)
        if check is None or not check.finished or not index or not os.path.isfile(index):
            return None
        try:
            folder = tempfile.mkdtemp(prefix="oceanval_times_")
            self.analyze_times_dir = folder
            atexit.register(shutil.rmtree, folder, True)
            path = os.path.join(folder, "1.pkl")
            shutil.copyfile(index, path)
            return path
        except OSError:
            return None

    def _drop_analyze_check(self):
        """Stop the check of the dates of the files, if there is one, and
        remove its files, in a thread. Called with the lock held."""
        check, self.analyze_check = self.analyze_check, None
        if check is not None:
            threading.Thread(target=check.remove, name="oceanval-analyze-check-remove").start()

    def analyze_check_files(self, variable):
        """The files of variable the check found, relative to the
        simulation's directory, or None."""
        with self._lock:
            check = self.analyze_check
        return check.files_of(variable, 0) if check is not None else None

    # ---- the files ----

    def _check_rows(self):
        """What the check found of each variable, by name. Called with the
        lock held."""
        if self.action == SIMCOMPARE:
            return self._sc_check_rows()
        check = self.analyze_check
        if check is None:
            return {}
        return {row["variable"]: row for row in check.state()["rows"]}

    def _years_of(self, found):
        return lambda name: analysis_forms.output_years(found.get(name))

    def _known_years(self, found):
        return lambda name: analysis_forms.output_years(found.get(name))

    def _time_res_of(self, found):
        placed = {row["name"]: row for row in self.analyze_placed or []}

        def time_res_of(name):
            row = found.get(name) or {}
            return row.get("time_res") or (placed.get(name) or {}).get("time_res")

        return time_res_of

    def analyze_files(self):
        """The files step's Continue."""
        with self._lock:
            if self.view != "analyze_files":
                return 409, {"ok": False, "error": "This step is over."}
            shift = self.analyze_shift or {}
            if shift.get("status") == "checking":
                return 409, {"ok": False, "error": "OceanVal is still looking at the files' times."}
            if shift.get("status") == "asked" and shift.get("choice") is None:
                return 409, {"ok": False, "error": "Say whether to shift the times first."}
            check = self.analyze_check
            if check is not None and check.status == "running":
                return 409, {"ok": False, "error": "The dates of the files are still being read."}
            self._enter_analyze_options()
            self._notify()
        return 200, {"ok": True}

    def _enter_analyze_options(self):
        """Go to the options step, with the pop-outs' boxes made again for the
        variables, which may have changed. Called with the lock held."""
        found = self._check_rows()
        placed = self.analyze_placed
        for kind in KINDS:
            form = self.analyze_forms[kind]
            if form is None:
                continue
            # variables added since start as they do the first time,
            # and those removed are left out
            defaults = self._default_form(kind, placed, found)
            form["rows"] = {
                row["name"]: form["rows"].get(row["name"], defaults["rows"][row["name"]])
                for row in placed
            }
            self._recheck(kind)
        self.view = "analyze_options"
        self._probe_missing()

    def _probe_missing(self):
        """Look for the model's missing value in a thread, unless the user
        has set the boxes. Called with the lock held."""
        if self._analyze_missing_set or not self.analyze_placed:
            return
        self._analyze_probe_number += 1
        row = self.analyze_placed[0]
        threading.Thread(
            target=self._probe_missing_thread,
            args=(self._analyze_probe_number, list(row["files"]), row["parts"][0]),
            name="oceanval-analyze-missing",
            daemon=True,
        ).start()

    def _probe_missing_thread(self, number, files, name):
        value = missing_probe.probe(files, name)
        with self._lock:
            if number != self._analyze_probe_number or self._analyze_missing_set:
                return
            if value is not None:
                self.analyze_missing = {"from": value, "to": value}
            else:
                ersem = bool((self.analyze_found or {}).get("ersem"))
                self.analyze_missing = {"from": "0", "to": "0"} if ersem else {"from": "", "to": ""}
            self.analyze_missing_probed = value is not None
            self._notify()

    def _default_form(self, kind, placed, found):
        if kind == "map_changes":
            return analysis_forms.default_map_changes(placed)
        if kind == "transects":
            return analysis_forms.default_transects(placed)
        if kind in LAYERED_KINDS:
            return analysis_forms.default_climatology(placed, self._years_of(found))
        return analysis_forms.default_phenology(placed, self._years_of(found), self._time_res_of(found))

    def _recheck(self, kind):
        """Check a pop-out's boxes again, as the variables may have changed,
        keeping what still holds. Called with the lock held."""
        chosen, errors = self._check_option(kind, self.analyze_forms[kind])
        if errors:
            # shown again in the pop-out, which is not confirmed until they are put right
            self.analyze_chosen[kind] = None
        else:
            self.analyze_chosen[kind] = chosen

    # ---- the analyses ----

    def _check_option(self, kind, form):
        """Check a pop-out's boxes. Returns (chosen, errors): chosen is None
        if there are errors, and nothing ticked is one (errors["rows"]).
        Called with the lock held."""
        found = self._check_rows()
        placed = self.analyze_placed
        if kind in LAYERED_KINDS:
            available = (self.analyze_found or {}).get("picker", {}).get("available", [])
            chosen, thickness, errors = analysis_forms.check_climatology(
                form, placed, self._known_years(found), set(available), self.cwd, LABELS[kind],
                series=kind == "long_term", fvcom=self._analyze_fvcom(),
            )
            if errors:
                return None, errors
            return {"rows": chosen, "thickness": thickness}, {}
        if kind == "map_changes":
            available = (self.analyze_found or {}).get("picker", {}).get("available", [])
            chosen, thickness, errors = analysis_forms.check_map_changes(
                form, placed, self._known_years(found), set(available), self.cwd, fvcom=self._analyze_fvcom()
            )
            if errors:
                return None, errors
            return {"rows": chosen, "thickness": thickness}, {}
        if kind == "transects":
            available = (self.analyze_found or {}).get("picker", {}).get("available", [])
            chosen, errors = analysis_forms.check_transects(
                form, placed, self._known_years(found), set(available), self.cwd, fvcom=self._analyze_fvcom()
            )
            return (None, errors) if errors else (chosen, {})
        chosen, errors = analysis_forms.check_phenology(
            form, placed, self._known_years(found), self._time_res_of(found)
        )
        if errors:
            return None, errors
        return {"rows": chosen}, {}

    def _analyze_fvcom(self):
        """Whether the simulation Analyze is working on is raw FVCOM output.
        Called with the lock held."""
        return bool((self.analyze_found or {}).get("fvcom"))

    def analyze_option(self, kind, form):
        """A pop-out's Confirm, with its boxes. The time series' also hold the
        regional subdomains and area they share, and the stations."""
        with self._lock:
            if self.view != "analyze_options" or (
                kind not in KINDS and kind not in ("cores", "missing", "figure_code")
            ):
                return 409, {"ok": False, "error": "This step is over."}
            if kind == "missing":
                given = form if isinstance(form, dict) else {}
                self.analyze_missing = {
                    "from": str(given.get("from") or "").strip(),
                    "to": str(given.get("to") or "").strip(),
                }
                self._analyze_missing_set = True
                self.analyze_missing_probed = False
                _, errors = analysis_forms.check_missing(self.analyze_missing["from"], self.analyze_missing["to"])
                self._notify()
                if errors:
                    return 400, {"ok": False, "errors": errors}
                return 200, {"ok": True}
            if kind == "figure_code":
                choice = str((form or {}).get("choice") or "").strip() if isinstance(form, dict) else ""
                _, error = analysis_forms.check_figure_code(choice)
                if error:
                    return 400, {"ok": False, "errors": {"figure_code": error}}
                self.analyze_figure_code = choice.lower()
                self._notify()
                return 200, {"ok": True}
            if kind == "cores":
                text = str((form or {}).get("cores") or "").strip() if isinstance(form, dict) else ""
                self.analyze_cores = text
                _, error = analysis_forms.check_cores(text)
                self._notify()
                if error:
                    return 400, {"ok": False, "errors": {"cores": error}}
                return 200, {"ok": True}
            if isinstance(form, dict) and form.get("remove"):
                # the analysis is left out of the run; the boxes are kept for next time
                self.analyze_chosen[kind] = None
                self._notify()
                return 200, {"ok": True}
            placed = self.analyze_placed
            if kind == "map_changes":
                clean = analysis_forms.clean_map_changes(form, placed)
            elif kind == "transects":
                clean = analysis_forms.clean_transects(form, placed)
            elif kind in LAYERED_KINDS:
                clean = analysis_forms.clean_climatology(form, placed)
            else:
                clean = analysis_forms.clean_phenology(form, placed)
            chosen, errors = self._check_option(kind, clean)
            area = None
            if kind in ("long_term", "seasonal") and isinstance(form, dict):
                area = analysis_forms.clean_area({**(form.get("area") or {}), "stations": form.get("stations")})
                _, area_errors = analysis_forms.check_area(area, self.cwd)
                # the stations' errors are by their row
                if "stations" in area_errors:
                    errors["stations"] = area_errors.pop("stations")
                if area_errors:
                    errors["area"] = area_errors
                area["stations"] = [row for row in area["stations"] if any(row)]
            if errors:
                return 400, {"ok": False, "errors": errors}
            self.analyze_forms[kind] = clean
            self.analyze_chosen[kind] = chosen
            if area is not None:
                self.analyze_area = area
            self._notify()
        return 200, {"ok": True}

    def analyze_run(self, save=False, form=None):
        """Write the analysis script, and run it, or, with save, only write
        it, to be run later. form is the last page's boxes (out_dir, out and,
        for an analysis, replace_analysis); without it the ones kept are used."""
        from oceanval.app import _path, _shown

        with self._lock:
            if self.view != "analyze_options":
                return 409, {"ok": False, "error": "This step is over."}
            chosen = self.analyze_chosen
            if not any(chosen[kind] for kind in KINDS):
                return 400, {
                    "ok": False,
                    "error": "Choose the climatology, the phenology, a time series, map changes, transects, or more than one.",
                }
            # one cell thickness for the water column of all of them
            thicknesses = {
                chosen[kind]["thickness"]
                for kind in THICKNESS_KINDS
                if chosen[kind] and chosen[kind]["thickness"]
            }
            if len(thicknesses) > 1:
                return 400, {
                    "ok": False,
                    "error": "The cell thickness is not the same in all the analyses that need it: "
                    "open them and use one.",
                }
            cores, error = analysis_forms.check_cores(self.analyze_cores)
            if error:
                return 400, {"ok": False, "errors": {"cores": error}, "error": error}
            figure_code, error = analysis_forms.check_figure_code(self.analyze_figure_code)
            if error:
                return 400, {"ok": False, "errors": {"figure_code": error}, "error": error}
            as_missing, errors = analysis_forms.check_missing(
                self.analyze_missing["from"], self.analyze_missing["to"]
            )
            if errors:
                return 400, {"ok": False, "errors": {"missing": errors}, "error": "What is treated as missing needs putting right."}
            area = None
            if chosen["long_term"] or chosen["seasonal"]:
                area, errors = analysis_forms.check_area(self.analyze_area, self.cwd)
                errors.pop("stations", None)
                if errors:
                    return 400, {
                        "ok": False,
                        "errors": {"area": errors},
                        "error": "The regions and area need putting right first.",
                    }
            paths, errors = self._check_output(form)
            if errors:
                return 400, {
                    "ok": False,
                    "errors": errors,
                    "error": "Where the output is saved needs putting right.",
                }
            if self.action == SIMCOMPARE:
                self.sc_paths = paths
                reply, run = self.simcompare_run(
                    chosen, thicknesses, cores, figure_code, as_missing, area, save=save
                )
                if run is None:
                    return reply
                label_run = run
            else:
                self.analyze_setup = dict(self.analyze_setup, **paths)
                # the times the check read are kept for a run now, not for a script kept for later
                times_index = None if save else self._analyze_keep_times()
                arguments = analysis_forms.arguments(
                    self.analyze_setup,
                    self.analyze_placed,
                    chosen["climatology"]["rows"] if chosen["climatology"] else None,
                    chosen["phenology"]["rows"] if chosen["phenology"] else None,
                    next(iter(thicknesses), None),
                    self._shift_days(),
                    chosen["long_term"]["rows"] if chosen["long_term"] else None,
                    chosen["seasonal"]["rows"] if chosen["seasonal"] else None,
                    area,
                    cores,
                    as_missing,
                    figure_code,
                    chosen["transects"],
                    chosen["map_changes"]["rows"] if chosen["map_changes"] else None,
                    times_index=times_index,
                )
                script = _path(self.analyze_setup["out"], self.cwd)
                try:
                    os.makedirs(os.path.dirname(script) or ".", exist_ok=True)
                    with open(script, "w") as file:
                        file.write(analysis_forms.script_text(arguments))
                except OSError as error:
                    return 400, {"ok": False, "error": f"The script could not be written: {error}"}
                self.results_dir = self.analyze_setup["out_dir"]
                self._drop_analyze_check()
                if save:
                    self._script_saved(script)
                    return 200, {"ok": True}
                self.view = "running"
                self.status = "starting"
                self.console.clear()
                label_run = (["analyze", script], f"python {_shown(script, self.cwd)}")
                reply = (200, {"ok": True})
        self.start_run(*label_run)
        return reply

    def _check_output(self, form):
        """Keep and check the last page's boxes, from the page or, if none were
        sent, as last kept: (paths, errors). Called with the lock held."""
        from oceanval.app import _form

        if self.action == SIMCOMPARE:
            if isinstance(form, dict):
                self.sc_output_form = sim_compare_forms.clean_output_form(form, self.cwd)
            return sim_compare_forms.check_output(self.sc_output_form, self.cwd)
        if isinstance(form, dict):
            kept = _form(form, analysis_forms.default_setup_form(self.cwd))
            self.analyze_setup_form = dict(
                self.analyze_setup_form, **{name: kept[name] for name in ("out_dir", "out", "out_typed", "replace_analysis")}
            )
        return analysis_forms.check_output(self.analyze_setup_form, self.cwd)

    def _script_saved(self, script):
        """Stop at the script, written to be run later: the page that says
        where it is, and the command to run it. Called with the lock held."""
        self.view = "script_saved"
        self.matchup_script_saved = True
        self.script_path = os.path.abspath(os.path.join(self.cwd, script))
        self._notify()

    # ---- Back ----

    def _analyze_back(self, sent):
        """Back from an Analyze step, keeping what its boxes hold. Called
        with the lock held. Returns None if the view is not one of them."""
        if self.view == "analyze_variables":
            # rows made before the read was in are not the user's
            if isinstance(sent.get("rows"), list) and (self.analyze_found or {}).get("status") == "done":
                self.analyze_rows = analysis_forms.clean_rows(sent["rows"])
            # a read still going is of no use if another directory is chosen next
            if (self.analyze_found or {}).get("status") == "reading":
                self._analyze_reading += 1
                self.analyze_found = None
                self.analyze_setup = None
            self.view = "setup"
        elif self.view == "analyze_files":
            self.view = "analyze_variables"
        elif self.view == "analyze_options":
            self.view = "analyze_files"
        else:
            return None
        self._notify()
        return True

    # ---- what the page shows ----

    def _analyze_state(self):
        """What the Analyze steps show. Called with the lock held."""
        found = self.analyze_found
        check = self.analyze_check
        rows = self._check_rows()
        placed = self.analyze_placed or []
        forms = {}
        for kind in KINDS:
            form = self.analyze_forms[kind]
            forms[kind] = form if form is not None else (
                self._default_form(kind, placed, rows) if placed else None
            )
        return {
            "found": None if found is None else {
                "status": found["status"],
                "progress": found["progress"],
                "error": found["error"],
                "streams": len(found["streams"] or []),
            },
            # the model's variables, for the pop-outs of the variables step
            # and of the thickness, only
            "picker": (
                (found or {}).get("picker")
                if self.view in ("analyze_variables", "analyze_options")
                else None
            ),
            # the names "Your name" offers, with the model variable of each
            "presets": (
                (found or {}).get("presets") or [] if self.view == "analyze_variables" else []
            ),
            # the last page's boxes
            "output": (
                self.sc_output_form
                if self.action == SIMCOMPARE
                else {name: self.analyze_setup_form[name] for name in ("out_dir", "out", "out_typed", "replace_analysis")}
            ),
            "rows": self.analyze_rows,
            "refilled": self.analyze_refilled,
            "placed": [
                {
                    "name": row["name"],
                    "model_variable": row["model_variable"],
                    "pattern": row["pattern"],
                    "files": len(row["files"]),
                    "streams": row["streams"],
                    "units": row["units"],
                    "levels": row["levels"],
                    "time_res": self._time_res_of(rows)(row["name"]),
                    # a comparison: the resolution of each simulation's output
                    "time_res_by_sim": (rows.get(row["name"]) or {}).get("time_res_by_sim"),
                    "years": analysis_forms.output_years(rows.get(row["name"])),
                    # what the pop-outs start with, in amber until changed
                    "default_years": analysis_forms.output_years(rows.get(row["name"])),
                }
                for row in placed
            ],
            "sim_dir": (self.analyze_setup or {}).get("simdir"),
            "time_shift": self.analyze_shift,
            "suggested_shift": time_stamps.describe(time_stamps.SUGGESTED),
            "check": None if check is None else dict(check.state(), number=check.number),
            "forms": forms,
            "chosen": dict(self.analyze_chosen),
            "area": self.analyze_area,
            "cores": self.analyze_cores,
            "cpu_count": os.cpu_count(),
            "missing": self.analyze_missing,
            "missing_probed": self.analyze_missing_probed,
            "figure_code": self.analyze_figure_code,
            "nemo_ersem": bool((found or {}).get("ersem")),
            # raw FVCOM output, worked out on its own mesh, which needs no cell thickness
            "fvcom": self._analyze_fvcom(),
            "fvcom_note": FVCOM_NOTE,
            "daily": time_resolution.DAILY,
            "z_level": analysis.Z_LEVEL,
            # what the info buttons of the pop-outs say (see oceanval.analysis_examples)
            "examples": analysis_examples.describe() if self.view == "analyze_options" else {},
            "thickness_suggested": (
                "e3t" if found and found.get("picker") and "e3t" in found["picker"]["available"] else ""
            ),
        }
