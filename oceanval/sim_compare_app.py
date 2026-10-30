"""The oceanval window's Compare simulations steps, which oceanval.app.App takes on.

A comparison starts from the landing page's Compare simulations tile, and goes
through these views:

    "simcompare_sims"       two to five simulations: a name, the directory its
                            output is in and how many directories down the files
                            are, as an Analyze simulation is
    "simcompare_variables"  the variables to compare, as "Your name" and the
                            model variable holding it in each simulation
    "simcompare_units"      only if a variable's units in a simulation are not
                            the first simulation's: how to convert them into the
                            first's, as validate's units step converts the
                            observations into the model's, a page for each
                            simulation that needs one (pages of the page's own)
    "simcompare_files"      the files holding them, with the dates missing from
                            them, for each simulation (a model_check.ModelCheck
                            each), asking first, if their times look like the end
                            of the period each mean covers, whether to shift them
    "simcompare_grids"      whether the simulations' grids need putting on one grid,
                            and how: a lon/lat box and resolution, or the grid of
                            one of the simulations, and by what regridding method;
                            a map of each simulation and, once Generate unified grid
                            is pressed, of the cells they all have (a preview only)
    "analyze_options"       the analyses, which are Analyze's own step (see
                            oceanval.analysis_app), with the variables as they are
                            in all the simulations: the years are those every
                            simulation has
    "running"               the script the window writes, which calls
                            oceanval.compare_simulations()

The state Analyze's options step keeps (analyze_*) is the comparison's while it is
the action: the variables the options are for are the combined ones (see
sim_compare_forms.combined), and the dates and time resolution those the
simulations share. Back goes a step the other way from each, keeping what was
entered. The forms and their checks are in oceanval.sim_compare_forms.
"""

import atexit
import os
import shutil
import tempfile
import threading
import traceback

import nctoolkit as nc
import numpy as np
import pandas as pd

from oceanval import (
    analysis,
    analysis_forms,
    extent,
    grid_align,
    missing_probe,
    model_check,
    sim_compare,
    sim_compare_forms,
    sim_unify,
    time_stamps,
    viewer_catalogue,
)
from oceanval import time_res as time_resolution

# the id the check of the dates of a simulation's files is made for (see model_check.ModelCheck)
CHECK_ID = "simcompare"


def _same_sims(old, new):
    """Whether two lists of checked simulations are the same simulations."""
    keys = ("name", "simdir", "ndown", "exclude", "require")
    return (
        old is not None
        and len(old) == len(new)
        and all(all(a.get(key) == b.get(key) for key in keys) for a, b in zip(old, new))
    )


def _placed_key(placed_by_sim):
    """What the files and dates of the variables depend on."""
    return [
        (sim, [(row["name"], row["model_variable"], row["pattern"]) for row in rows])
        for sim, rows in (placed_by_sim or {}).items()
    ]


class SimCompareSteps:
    """The Compare simulations steps of oceanval.app.App: its lock, _notify, view,
    action, the Analyze steps and the rest are the App's own."""

    def _init_simcompare(self):
        # the simulations step's boxes, the simulations they were turned into,
        # the last page's boxes and the paths of the script and of the
        # comparison they were turned into
        self.sc_sims_form = sim_compare_forms.default_sims_form(self.cwd)
        self.sc_sims = None
        self.sc_output_form = sim_compare_forms.default_output_form(self.cwd)
        self.sc_paths = None
        # each simulation's streams as they are read, by name: {"status":
        # "reading", "done" or "failed", "progress", "error", "streams",
        # "picker", "presets", "ersem"}
        self.sc_found = {}
        self._sc_reading = {}
        # the variables step's rows as last left: {"name", "models": {simulation:
        # model variable}, "filled"}
        self.sc_rows = None
        # the variables as analysis_forms.check_rows placed them, by simulation
        self.sc_placed = None
        # whether the files' times look like ends, and what was chosen:
        # {"status": "checking", "asked" or "none", "rows", "choice"}
        self.sc_shift = None
        self._sc_shift_number = 0
        # the check of the dates of each simulation's files, and the shift it was made with
        self.sc_checks = {}
        self.sc_times_dir = None
        self._sc_check_shifts = {}
        # the units step's rows (see sim_compare_forms.unit_rows), what its boxes
        # held, by key, with the row they were for, and the conversions they were
        # checked to, as compare_simulations()'s unit_conversions
        self.sc_unit_rows = None
        self.sc_unit_boxes = {}
        self.sc_conversions = None
        # the grids step's boxes, and what they were checked to
        self.sc_unify_form = sim_compare_forms.default_unify_form()
        self.sc_unify = None
        # the domain mask file the grids step chose (None for No)
        self.sc_domain_mask = None
        # what is known of the simulations' grids: {"status": "reading" or "done", "same", "extents"}
        self.sc_grids = None
        self._sc_grids_number = 0
        # what generating the unified grid is doing, as words for the page, or None
        self.sc_stage = None
        # the time step the grids step draws, by the first file of a simulation's
        # first variable: (path, step), see _sc_field_step
        self._sc_steps = {}

    # ---- the simulations ----

    def simcompare_sims(self, form):
        """The simulations step's Continue, with its boxes."""
        with self._lock:
            if self.view != "simcompare_sims":
                return 409, {"ok": False, "error": "This step is over."}
            self.sc_sims_form = sim_compare_forms.clean_sims_form(form, self.cwd)
            sims, errors = sim_compare_forms.check_sims(self.sc_sims_form, self.cwd)
            if errors:
                self._notify()
                return 400, {"ok": False, "errors": errors}
            same = _same_sims(self.sc_sims, sims) and all(
                self.sc_found.get(sim["name"], {}).get("status") != "failed" for sim in sims
            )
            if not same:
                kept = {sim["name"]: sim for sim in self.sc_sims or []}
                self.sc_sims = sims
                for sim in sims:
                    if kept.get(sim["name"]) is None or not _same_sims([kept[sim["name"]]], [sim]) or (
                        self.sc_found.get(sim["name"], {}).get("status") == "failed"
                    ):
                        self._sc_read(sim)
                self.sc_found = {sim["name"]: self.sc_found[sim["name"]] for sim in sims}
                self._sc_forget_after_sims()
            self._start_cdo_check(self.sc_sims)
            self.view = "simcompare_variables"
            self._notify()
        return 200, {"ok": True}

    def _sc_forget_after_sims(self):
        """What depends on the simulations, once they have changed. Called with
        the lock held."""
        self.sc_placed = None
        self.analyze_placed = None
        self.sc_unit_rows = None
        self.sc_unit_boxes = {}
        self.sc_conversions = None
        self.sc_shift = None
        self._sc_drop_checks()
        self._sc_drop_times()
        self.sc_unify = None
        self.sc_domain_mask = None
        self.sc_grids = None
        self._sc_grids_number += 1
        self._sc_shift_number += 1

    def _sc_read(self, sim):
        """Read a simulation's streams in a thread, in place of any read before.
        Called with the lock held."""
        name = sim["name"]
        number = self._sc_reading.get(name, 0) + 1
        self._sc_reading[name] = number
        self.sc_found[name] = {
            "status": "reading",
            "progress": {"done": 0, "total": 0},
            "error": None,
            "streams": None,
            "picker": None,
            "presets": [],
            "ersem": False,
        }
        threading.Thread(
            target=self._sc_read_streams, args=(name, number, dict(sim)), name="oceanval-simcompare-read", daemon=True
        ).start()

    def _sc_read_streams(self, name, number, sim):
        def progress(done, total):
            with self._lock:
                if number == self._sc_reading.get(name) and self.sc_found.get(name) is not None:
                    self.sc_found[name]["progress"] = {"done": done, "total": total}
                    self._notify()

        from oceanval import recipes_gui

        error = None
        found = picker = None
        presets = []
        ersem = False
        try:
            found = analysis.streams(sim["simdir"], sim["ndown"], sim.get("exclude"), sim.get("require"), progress=progress)
            if not found:
                error = "No netCDF files OceanVal could read were found."
            elif analysis.looks_like_fvcom(found[0]["files"][0]):
                error = "Compare simulations does not work with FVCOM output yet."
            else:
                picker = analysis_forms.picker_state(found)
                ersem = recipes_gui.is_likely_nemo_ersem(picker["available"])
                presets = analysis_forms.presets(found, self.cwd)
        except Exception as problem:
            error = f"The simulation could not be read: {problem}"
        with self._lock:
            if number != self._sc_reading.get(name) or name not in self.sc_found:
                return
            if error:
                self.sc_found[name] = dict(self.sc_found[name], status="failed", error=error)
            else:
                self.sc_found[name] = dict(
                    self.sc_found[name], status="done", streams=found, picker=picker, presets=presets, ersem=ersem
                )
                self._sc_fill_rows()
            self._notify()

    def _sc_fill_rows(self):
        """The variables step's rows, once the simulations are read: the rows
        the user gave, with a model variable for each simulation that has none
        yet. Called with the lock held."""
        names = [sim["name"] for sim in self.sc_sims or []]
        if self.sc_rows is None:
            self.sc_rows = []
            return
        self.sc_rows = [
            dict(row, models={sim: row["models"].get(sim, "") for sim in names}) for row in self.sc_rows
        ]

    def _sc_all_read(self):
        """Whether every simulation's streams have been read. Called with the lock held."""
        return bool(self.sc_sims) and all(
            self.sc_found.get(sim["name"], {}).get("status") == "done" for sim in self.sc_sims
        )

    # ---- the variables ----

    def simcompare_variables(self, rows):
        """The variables step's Continue, with its rows."""
        with self._lock:
            if self.view != "simcompare_variables":
                return 409, {"ok": False, "error": "This step is over."}
            if not self._sc_all_read():
                return 409, {"ok": False, "error": "The simulations have not all been read yet."}
            names = [sim["name"] for sim in self.sc_sims]
            self.sc_rows = sim_compare_forms.clean_rows(rows, names)
            rows = list(self.sc_rows)
            streams = {name: self.sc_found[name]["streams"] for name in names}
        # outside the lock: choosing between streams reads their times
        placed, errors = sim_compare_forms.check_rows(rows, streams)
        if errors:
            return 400, {"ok": False, "errors": errors}
        with self._lock:
            if self.view != "simcompare_variables":
                return 409, {"ok": False, "error": "This step is over."}
            changed = _placed_key(placed) != _placed_key(self.sc_placed)
            self.sc_placed = placed
            self.analyze_placed = sim_compare_forms.combined(placed)
            unit_rows = sim_compare_forms.unit_rows(placed)
            if unit_rows != self.sc_unit_rows:
                self.sc_unit_rows = unit_rows
                self.sc_conversions = None
            # the units are converted into the first simulation's, if any are not
            # its: the checks of the files below go on meanwhile
            self.view = "simcompare_units" if unit_rows else "simcompare_files"
            if not unit_rows:
                self.sc_conversions = {}
            if changed or self.sc_shift is None or not self.sc_checks:
                self._sc_drop_checks()
                self._sc_check_time_stamps()
                # looked at again, from the files of the variables
                self.sc_grids = None
                self._sc_grids_number += 1
            self._notify()
        return 200, {"ok": True}

    # ---- the units ----

    def simcompare_units(self, conversions, confirmed=False):
        """The units step's Continue (on its last page), with what its boxes hold:
        {key: {"multiplier", "adder"}}, and whether the user ticked that they are
        happy with the units, which they must have."""
        with self._lock:
            if self.view != "simcompare_units":
                return 409, {"ok": False, "error": "This step is over."}
            self._sc_keep_unit_boxes(conversions)
            if confirmed is not True:
                return 400, {"ok": False, "error": "Confirm the units before carrying on."}
            checked, errors = sim_compare_forms.check_unit_boxes(conversions, self.sc_unit_rows or [])
            if errors:
                return 400, {"ok": False, "errors": errors}
            self.sc_conversions = checked
            self.view = "simcompare_files"
            self._notify()
        return 200, {"ok": True}

    def _sc_keep_unit_boxes(self, sent):
        """Keep what the units step's boxes hold, with the row each is for, so that
        they are shown again for Back. Called with the lock held."""
        if not isinstance(sent, dict):
            return
        for row in self.sc_unit_rows or []:
            boxes = sent.get(row["key"])
            if isinstance(boxes, dict):
                self.sc_unit_boxes[row["key"]] = {
                    "multiplier": str(boxes.get("multiplier", ""))[:40],
                    "adder": str(boxes.get("adder", ""))[:40],
                    "row": sim_compare_forms.unit_row_signature(row),
                }

    def _sc_unit_boxes_shown(self):
        """What the units step's boxes are filled in with again, for each row that
        is the same as when they were left. Called with the lock held."""
        shown = {}
        for row in self.sc_unit_rows or []:
            kept = self.sc_unit_boxes.get(row["key"])
            if kept is not None and kept["row"] == sim_compare_forms.unit_row_signature(row):
                shown[row["key"]] = {"multiplier": kept["multiplier"], "adder": kept["adder"]}
        return shown

    def _sc_check_time_stamps(self):
        """Look, in a thread, at whether the times of the files look like the end
        of the period each mean covers, which is asked about before their dates
        are checked. Called with the lock held."""
        self._sc_shift_number += 1
        number = self._sc_shift_number
        kept = (self.sc_shift or {}).get("choice")
        self.sc_shift = {"status": "checking", "rows": [], "choice": kept}
        placed = {sim: [dict(row) for row in rows] for sim, rows in self.sc_placed.items()}
        threading.Thread(
            target=self._sc_time_stamps_found, args=(number, placed), name="oceanval-simcompare-stamps", daemon=True
        ).start()

    def _sc_time_stamps_found(self, number, placed):
        rows = []
        for sim, sim_rows in placed.items():
            chosen = pd.DataFrame(
                [
                    {"variable": row["name"], "model_variable": row["model_variable"], "pattern": row["pattern"]}
                    for row in sim_rows
                ]
            )
            files = {row["pattern"]: row["files"] for row in sim_rows}
            try:
                found = time_stamps.check_rows(chosen, files, {row["name"]: row["name"] for row in sim_rows})
            except Exception:
                found = []
            rows.extend(dict(row, simulation=sim) for row in found)
        with self._lock:
            if number != self._sc_shift_number:
                return
            if rows:
                self.sc_shift = dict(self.sc_shift, status="asked", rows=rows)
                if self.sc_shift["choice"] is not None:
                    # the answer given before, for the same files
                    self._sc_start_checks()
            else:
                self.sc_shift = {"status": "none", "rows": [], "choice": None}
                self._sc_start_checks()
            self._notify()

    def _sc_shifts(self):
        """The days each simulation's times are shifted by, {name: days or None}:
        the suggested shift for the simulations that look as if they need it, if
        that was chosen."""
        shift = self.sc_shift or {}
        flagged = {row["simulation"] for row in shift.get("rows") or []}
        days = time_stamps.SUGGESTED if shift.get("choice") == "shift" else None
        return {sim["name"]: (days if sim["name"] in flagged else None) for sim in self.sc_sims or []}

    def simcompare_time_shift(self, choice):
        """The answer to whether to shift the files' times: "shift" or "keep"."""
        with self._lock:
            shift = self.sc_shift
            if self.view != "simcompare_files" or shift is None or shift["status"] != "asked":
                return False
            if choice not in ("shift", "keep"):
                return False
            if shift["choice"] == choice and self.sc_checks:
                return True
            shift["choice"] = choice
            self._sc_drop_checks()
            self._sc_start_checks()
            self._notify()
            return True

    def _sc_start_checks(self):
        """Check the dates of the files of the variables in every simulation, with
        the times shifted as chosen. Called with the lock held."""
        shifts = self._sc_shifts()
        self._sc_check_shifts = dict(shifts)
        for sim in self.sc_sims:
            name = sim["name"]
            rows = []
            files = {}
            for row in self.sc_placed[name]:
                rows.append(
                    {
                        "variable": row["name"],
                        "pattern": row["pattern"],
                        # worked out from the files' times (see model_check.check)
                        "time_res": None,
                        "groups": [
                            {"years": [model_check.NO_START, model_check.NO_END], "sources": [], "gridded": False}
                        ],
                    }
                )
                files.setdefault(row["pattern"], list(row["files"]))
            request = {
                "sim_dir": sim["simdir"],
                "years": [model_check.NO_START, model_check.NO_END],
                "fvcom": False,
                "time_shift": shifts.get(name) or 0,
                "rows": rows,
                "files": files,
            }
            check = model_check.ModelCheck(CHECK_ID, request, self._notify)
            self.sc_checks[name] = check
            threading.Thread(target=check.start, name="oceanval-simcompare-check", daemon=True).start()

    def _sc_keep_times(self):
        """Keep the times of the files each check read, which the run would otherwise
        read again (the checks' folders are removed before it starts): copy each
        to a folder of its own, and return {simulation: path}. Called with the lock
        held."""
        self._sc_drop_times()
        kept = {}
        folder = None
        for name, check in self.sc_checks.items():
            index = getattr(check, "index", None)
            if not index or not os.path.isfile(index):
                continue
            try:
                if folder is None:
                    folder = tempfile.mkdtemp(prefix="oceanval_times_")
                    self.sc_times_dir = folder
                    atexit.register(shutil.rmtree, folder, True)
                path = os.path.join(folder, f"{len(kept) + 1}.pkl")
                shutil.copyfile(index, path)
                kept[name] = path
            except OSError:
                continue
        return kept

    def _sc_drop_times(self):
        """Remove the times kept for a run. Called with the lock held."""
        folder, self.sc_times_dir = getattr(self, "sc_times_dir", None), None
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
        folder, self.analyze_times_dir = getattr(self, "analyze_times_dir", None), None
        if folder:
            shutil.rmtree(folder, ignore_errors=True)

    def _sc_drop_checks(self):
        """Stop the checks of the dates of the files, and remove their files, in a
        thread. Called with the lock held."""
        checks, self.sc_checks = list(self.sc_checks.values()), {}
        for check in checks:
            threading.Thread(target=check.remove, name="oceanval-simcompare-check-remove").start()

    def simcompare_check_files(self, simulation, variable):
        """The files of variable in a simulation the check found, relative to its
        directory, or None."""
        with self._lock:
            check = self.sc_checks.get(simulation)
        return check.files_of(variable, 0) if check is not None else None

    def _sc_checks_done(self):
        """Whether the dates of every simulation's files have been read. Called
        with the lock held."""
        return bool(self.sc_checks) and len(self.sc_checks) == len(self.sc_sims or []) and all(
            check.status != "running" for check in self.sc_checks.values()
        )

    def _sc_check_rows(self):
        """What the options step takes the check of the dates to have found, by
        variable: its first and last year are those every simulation has (so the
        years asked for are all in all of them), and its time resolution the
        coarsest. Called with the lock held."""
        rows = {}
        for number, variable in enumerate((self.analyze_placed or [])):
            firsts, lasts, resolutions, by_sim = [], [], [], {}
            for sim in self.sc_sims or []:
                check = self.sc_checks.get(sim["name"])
                row = ({r["variable"]: r for r in check.state()["rows"]} if check is not None else {}).get(variable["name"])
                years = analysis_forms.output_years(row)
                if years is None:
                    firsts = lasts = None
                    break
                firsts.append(years[0])
                lasts.append(years[1])
                resolutions.append((row or {}).get("time_res") or variable["per_sim"][sim["name"]]["time_res"])
                by_sim[sim["name"]] = resolutions[-1]
            if firsts is None or not firsts:
                continue
            first, last = max(firsts), min(lasts)
            if first > last:
                rows[variable["name"]] = {
                    "groups": [{"dates": {"first": f"{first:04d}-01-01", "last": f"{first - 1:04d}-12-31"}}],
                    "time_res": None,
                    "no_overlap": True,
                }
                continue
            same = len(set(resolutions)) == 1
            rows[variable["name"]] = {
                "groups": [{"dates": {"first": f"{first:04d}-01-01", "last": f"{last:04d}-12-31"}}],
                "time_res": resolutions[0] if same else next((r for r in resolutions if r != time_resolution.DAILY), None),
                "time_res_by_sim": by_sim,
            }
        return rows

    def simcompare_files(self):
        """The files step's Continue."""
        with self._lock:
            if self.view != "simcompare_files":
                return 409, {"ok": False, "error": "This step is over."}
            shift = self.sc_shift or {}
            if shift.get("status") == "checking":
                return 409, {"ok": False, "error": "OceanVal is still looking at the files' times."}
            if shift.get("status") == "asked" and shift.get("choice") is None:
                return 409, {"ok": False, "error": "Say whether to shift the times first."}
            if not self._sc_checks_done():
                return 409, {"ok": False, "error": "The dates of the files are still being read."}
            rows = self._sc_check_rows()
            problems = [
                f"{name} has no years in common between the simulations."
                for name, row in rows.items()
                if row.get("no_overlap")
            ]
            if problems:
                return 400, {
                    "ok": False,
                    "error": " ".join(problems)
                    + " The simulations are compared over the years they all have: choose variables whose output overlaps.",
                }
            self.view = "simcompare_grids"
            self._sc_look_at_grids()
            self._notify()
        return 200, {"ok": True}

    # ---- the grids ----

    def _sc_look_at_grids(self):
        """Look, in a thread, at whether the simulations are on the same grid, and
        at the extent of each, for the grids step. Called with the lock held."""
        if self.sc_grids is not None and self.sc_grids["status"] in ("reading", "done"):
            return
        self._sc_grids_number += 1
        number = self._sc_grids_number
        self.sc_grids = {"status": "reading", "same": None, "extents": {}}
        parts = sim_compare.first_parts(self.sc_placed, self.sc_sims)
        threading.Thread(
            target=self._sc_grids_found, args=(number, parts), name="oceanval-simcompare-grids", daemon=True
        ).start()

    def _sc_grids_found(self, number, parts):
        extents = {}
        same = True
        first = None
        try:
            for name, (path, variable) in parts.items():
                # a model file can be many GB: only one level of one time step is read
                data = extent.first_slice(path, variable)
                lonlat = None if data is None else extent.lonlat(data)
                if lonlat is None:
                    same = None
                    continue
                # where the simulation has values, not its whole grid (NEMO's left-out land is at -1, -1)
                lon, lat = extent.valid_lonlat(data, variable) or lonlat
                extents[name] = {
                    "lon": [float(np.nanmin(lon)), float(np.nanmax(lon))],
                    "lat": [float(np.nanmin(lat)), float(np.nanmax(lat))],
                }
                if first is None:
                    first = lonlat
                elif same is not None and not sim_unify.same_lonlat(first, lonlat):
                    same = False
        except Exception:
            same = None
        with self._lock:
            if number != self._sc_grids_number:
                return
            self.sc_grids = {"status": "done", "same": same, "extents": extents}
            self._notify()

    def simcompare_grids(self, form):
        """The grids step's Continue, with its boxes."""
        with self._lock:
            if self.view != "simcompare_grids":
                return 409, {"ok": False, "error": "This step is over."}
            self.sc_unify_form = sim_compare_forms.clean_unify(form)
            names = [sim["name"] for sim in self.sc_sims]
            unify, errors = sim_compare_forms.check_unify(self.sc_unify_form, names)
            from oceanval.app import check_domain_mask

            domain_mask = check_domain_mask(self.sc_unify_form, self.cwd, errors)
            if errors:
                self._notify()
                return 400, {"ok": False, "errors": errors}
            self.sc_unify = unify
            self.sc_domain_mask = domain_mask
            self._sc_make_analyze_found()
            self._enter_analyze_options()
            self._notify()
        return 200, {"ok": True}

    def _sc_field_source(self, name):
        """What the grids step draws of simulation name: (the first file of its
        first variable, the model variable read from it, that variable's
        files). Called with the lock held."""
        row = self.sc_placed[name][0]
        files = list(row["files"])
        variable = (row.get("parts") or [None])[0]
        return files[0], variable, files

    def _sc_field_step(self, files):
        """The file and the time step in it (from 0) the grids step draws from
        files, a simulation's first variable's: the simulation's second time
        step, if it has one, which is the first file's second, or else the
        second file's first, and otherwise its first. Counting a file's times
        runs CDO, so it is done once for each first file, outside the lock."""
        with self._lock:
            known = self._sc_steps.get(files[0])
        if known is not None:
            return known
        try:
            count = len(nc.open_data(files[0], checks=False).times)
        except Exception:
            count = 1
        found = analysis.second_step(files, count)
        with self._lock:
            self._sc_steps[files[0]] = found
        return found

    @staticmethod
    def _sc_missing(files, variable):
        """The preview's missing values for the first variable of a simulation: the
        value the files hold in most of their cells (see missing_probe), as viewer
        ranges, and a note saying so, or none and no note. The values chosen later,
        on the options step, are the run's own."""
        value = missing_probe.probe(files, variable)
        if value is None:
            return [], None
        return [(float(value), float(value))], f"{value} is taken as missing here, as it is in most of the cells of the files that do not change."

    def simcompare_panel(self, number):
        """The map of simulation number's (counted from 1) first variable, at the
        surface and the simulation's second time step (see _sc_field_step), for
        the grids step: {"ok", "item", "notes", "variable", "file", "step"} (the
        file and the time step in it, from 1) or {"ok": False, "error"}. Returns
        the HTTP status and the reply."""
        from oceanval import viewer_plots

        with self._lock:
            if self.view != "simcompare_grids":
                return 409, {"ok": False, "error": "This step is over."}
            names = [sim["name"] for sim in self.sc_sims or []]
            try:
                name = names[int(number) - 1]
                assert int(number) >= 1
                path, variable, files = self._sc_field_source(name)
            except (TypeError, ValueError, AssertionError, IndexError, KeyError):
                return 400, {"ok": False, "error": "There is no such simulation."}
        if variable is None:
            return 200, {"ok": False, "error": "The model variable of this simulation could not be told."}
        found = None
        try:
            missing, note = self._sc_missing(files, variable)
            path, step = self._sc_field_step(files)
            found = viewer_catalogue.scan(path, 0, wait=False)
            about = found.combined(0, [variable])
            data, _ = found.field(0, [variable], step, about.get("surface_level", 0), missing)
            item, notes, _ = viewer_plots.map_plot(data, about, about["name"], coast=viewer_plots.has_geoviews())
            if not data.notnull().any():
                notes.append("Every cell is missing, so there is nothing to show.")
            if note:
                notes.append(note)
            return 200, {
                "ok": True, "item": item, "notes": notes, "variable": variable, "file": os.path.basename(path), "step": step + 1,
            }
        except ValueError as problem:
            return 200, {"ok": False, "error": str(problem)}
        except Exception as problem:  # noqa: BLE001 - told to the page
            traceback.print_exc()
            return 200, {"ok": False, "error": f"{type(problem).__name__}: {problem}"}
        finally:
            if found is not None:
                found.close()

    def _sc_progress(self, stage, simulation=None):
        """Say what generating the unified grid is doing, to the page (see sim_unify.stage_text)."""
        with self._lock:
            self.sc_stage = sim_unify.stage_text(stage, simulation)
            self._notify()

    def simcompare_generate(self, form):
        """The cells every simulation has a value in once they are on one grid, as
        the grids step's boxes say (baseline or box, and how they are regridded),
        judged from the first variable of each at the surface and the time step its map is of:
        a preview of what the run makes for every variable and layer. Returns the
        HTTP status and the reply, {"ok", "item", "notes", "shared", "total"}, or
        {"ok": False, "errors"} by box, or "error"."""
        from oceanval import viewer_plots

        form = sim_compare_forms.clean_unify(form)
        with self._lock:
            if self.view != "simcompare_grids":
                return 409, {"ok": False, "error": "This step is over."}
            names = [sim["name"] for sim in self.sc_sims or []]
            sources = {name: self._sc_field_source(name) for name in names}
        unify, errors = sim_compare_forms.check_unify(form, names)
        if unify is None and not errors:
            errors = {"need": "Say yes to put the simulations on one grid."}
        if errors:
            return 400, {"ok": False, "errors": errors}
        if any(variable is None for _, variable, _ in sources.values()):
            return 400, {"ok": False, "error": "The model variable of a simulation could not be told."}
        # the same checks the run makes, which add how the grid is made
        unify = sim_unify.check_unify(unify, names)
        fields = {}
        try:
            for name, (path, variable, files) in sources.items():
                missing, _ = self._sc_missing(files, variable)
                # the time step each simulation's map is of
                path, step = self._sc_field_step(files)
                found = viewer_catalogue.scan(path, 0, wait=False)
                try:
                    about = found.combined(0, [variable])
                finally:
                    found.close()
                fields[name] = grid_align.field_for_cdo(path, variable, about.get("surface_level", 0), missing, step)
            method = form["regrid"] or sim_unify.DEFAULT_METHOD
            try:
                if unify["method"] == "box":
                    mask, shared, total = grid_align.unified(fields, None, method, box=unify, progress=self._sc_progress)
                else:
                    mask, shared, total = grid_align.unified(fields, unify["baseline"], method, progress=self._sc_progress)
            finally:
                with self._lock:
                    self.sc_stage = None
                    self._notify()
            item, notes, _ = viewer_plots.map_plot(
                mask, grid_align.about(mask), "Unified grid",
                cmap="Blues", vmin=0, vmax=1, coast=viewer_plots.has_geoviews(),
            )
        except ValueError as problem:
            return 400, {"ok": False, "error": str(problem)}
        except Exception as problem:  # noqa: BLE001 - told to the page
            traceback.print_exc()
            return 400, {"ok": False, "error": f"{type(problem).__name__}: {problem}"}
        return 200, {"ok": True, "item": item, "notes": notes, "shared": shared, "total": total}

    def _sc_make_analyze_found(self):
        """Make what the options step takes the simulation's streams to be, from
        the simulations': the model variables they all hold, which the thickness
        is looked for among. Called with the lock held."""
        found = [self.sc_found[sim["name"]] for sim in self.sc_sims]
        pickers = [one["picker"] for one in found]
        available = sorted(set.intersection(*(set(picker["available"]) for picker in pickers)))
        ersem = any(one.get("ersem") for one in found)
        self.analyze_found = {
            "status": "done",
            "progress": {"done": 0, "total": 0},
            "error": None,
            "streams": [],
            "picker": {
                "available": available,
                "long_names": {name: pickers[0]["long_names"].get(name, "") for name in available},
                "levels": {name: min(picker["levels"].get(name, 1) for picker in pickers) for name in available},
            },
            "presets": [],
            "ersem": ersem,
        }
        if not self._analyze_missing_set:
            # as in validate, ERSEM output has zeros where there is no water
            self.analyze_missing = {"from": "0", "to": "0"} if ersem else {"from": "", "to": ""}
            self.analyze_missing_probed = False

    # ---- the run ----

    def simcompare_run(self, chosen, thicknesses, cores, figure_code, as_missing, area, save=False):
        """Write the comparison script, and run it, or, with save, only write it,
        to be run later. Called with the lock held, from analyze_run, which has
        checked the analyses; returns the HTTP status and the reply, and the run
        to start, if any."""
        from oceanval.app import _shown

        shifts = self._sc_shifts()
        arguments = sim_compare_forms.arguments(
            self.sc_sims,
            self.sc_placed,
            shifts,
            self.sc_unify,
            self.sc_paths["out_dir"],
            # the times the checks read are kept for a run now, not for a script kept for later
            times_index=None if save else self._sc_keep_times(),
            domain_mask=self.sc_domain_mask,
            unit_conversions=self.sc_conversions,
            climatology=chosen["climatology"]["rows"] if chosen["climatology"] else None,
            phenology=chosen["phenology"]["rows"] if chosen["phenology"] else None,
            thickness=next(iter(thicknesses), None),
            time_shift=None,
            long_term=chosen["long_term"]["rows"] if chosen["long_term"] else None,
            seasonal=chosen["seasonal"]["rows"] if chosen["seasonal"] else None,
            area=area,
            cores=cores,
            as_missing=as_missing,
            figure_code=figure_code,
            transects=chosen["transects"],
            map_changes=chosen["map_changes"]["rows"] if chosen["map_changes"] else None,
        )
        script = self.sc_paths["out"]
        try:
            os.makedirs(os.path.dirname(script) or ".", exist_ok=True)
            with open(script, "w") as file:
                file.write(sim_compare_forms.script_text(arguments))
        except OSError as error:
            return (400, {"ok": False, "error": f"The script could not be written: {error}"}), None
        self.results_dir = self.sc_paths["out_dir"]
        self._sc_drop_checks()
        self._drop_analyze_check()
        if save:
            self._script_saved(script)
            return (200, {"ok": True}), None
        self.view = "running"
        self.status = "starting"
        self.console.clear()
        return (200, {"ok": True}), (["simcompare", script], f"python {_shown(script, self.cwd)}")

    # ---- Back ----

    def _simcompare_back(self, sent):
        """Back from a Compare simulations step, keeping what its boxes hold.
        Called with the lock held. Returns None if the view is not one of them."""
        if self.view == "simcompare_sims":
            if isinstance(sent.get("form"), dict):
                self.sc_sims_form = sim_compare_forms.clean_sims_form(sent["form"], self.cwd)
            self.view = "home"
        elif self.view == "simcompare_variables":
            if isinstance(sent.get("rows"), list):
                self.sc_rows = sim_compare_forms.clean_rows(sent["rows"], [sim["name"] for sim in self.sc_sims or []])
            self.view = "simcompare_sims"
        elif self.view == "simcompare_units":
            self._sc_keep_unit_boxes(sent.get("conversions"))
            self.view = "simcompare_variables"
        elif self.view == "simcompare_files":
            self.view = "simcompare_units" if self.sc_unit_rows else "simcompare_variables"
        elif self.view == "simcompare_grids":
            if isinstance(sent.get("form"), dict):
                self.sc_unify_form = sim_compare_forms.clean_unify(sent["form"])
            self.view = "simcompare_files"
        elif self.view == "analyze_options":
            self.view = "simcompare_grids"
        else:
            return None
        self._notify()
        return True

    # ---- what the page shows ----

    def _simcompare_state(self):
        """What the Compare simulations steps show. Called with the lock held."""
        sims = self.sc_sims or []
        form_rows = self.sc_sims_form["rows"]
        in_variables = self.view == "simcompare_variables"
        colours = dict(zip([s["name"] for s in sims], sim_compare.COLOURS))
        placed = sim_compare_forms.combined(self.sc_placed) if self.sc_placed else []
        return {
            "form": self.sc_sims_form,
            "most": sim_compare.MOST_SIMULATIONS,
            "colours": list(sim_compare.COLOURS),
            "sims": [
                {
                    "name": sim["name"],
                    "simdir": sim["simdir"],
                    "ndown": sim["ndown"],
                    "colour": colours.get(sim["name"]),
                    "status": (self.sc_found.get(sim["name"]) or {}).get("status"),
                    "progress": (self.sc_found.get(sim["name"]) or {}).get("progress"),
                    "error": (self.sc_found.get(sim["name"]) or {}).get("error"),
                    "streams": len(((self.sc_found.get(sim["name"]) or {}).get("streams")) or []),
                    "picker": (self.sc_found.get(sim["name"]) or {}).get("picker") if in_variables else None,
                    "presets": ((self.sc_found.get(sim["name"]) or {}).get("presets") or []) if in_variables else [],
                    "check": (
                        dict(self.sc_checks[sim["name"]].state(), number=self.sc_checks[sim["name"]].number)
                        if sim["name"] in self.sc_checks
                        else None
                    ),
                }
                for sim in sims
            ],
            "rows": self.sc_rows,
            "placed": [
                {
                    "name": row["name"],
                    "units": row["units"],
                    "per_sim": row["per_sim"],
                }
                for row in placed
            ],
            "units": (
                {"rows": self.sc_unit_rows, "boxes": self._sc_unit_boxes_shown()}
                if self.view == "simcompare_units"
                else None
            ),
            "shift": self.sc_shift,
            "suggested_shift": time_stamps.describe(time_stamps.SUGGESTED),
            "unify_form": self.sc_unify_form,
            "grids": self.sc_grids,
            "stage": self.sc_stage,
            "regrid_methods": sim_unify.METHODS,
            # what the grids step draws of each simulation: its first variable's first file
            "grid_fields": (
                [
                    dict(zip(("file", "variable"), (os.path.basename(source[0]), source[1])))
                    for source in (self._sc_field_source(sim["name"]) for sim in sims)
                ]
                if self.view == "simcompare_grids" and self.sc_placed
                else []
            ),
            "form_rows": len(form_rows),
        }
