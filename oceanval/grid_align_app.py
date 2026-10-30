"""The oceanval window's step for aligning the simulation's grid with other
simulations', which oceanval.app.App takes on.

A matchup run (to validate too, or alone) asks last whether the simulation's
grid needs aligning with other simulations' (the report options' or the output
step's "align_grids"). Yes shows the view "align_grids", while matchup still
waits for the answer to whether the matchups are right: the
simulation's grid (the first file and model variable of the matchups), the
second simulation's and, added by a plus on the page, up to three more
(a file, a variable in it and its missing values,
each drawn as the viewer draws a map, see oceanval.viewer_plots), and the grid
they share, which "Generate unified grid" draws (see oceanval.grid_align), on
the grid of the baseline chosen, with the regridding method chosen.

The mask of the grid generated is kept in the window's own draft folder, as
nothing is written in the output directory before the matchups are confirmed.
"Match up and validate" (from the report options) or "Match up" (from the
output step) then carries on as that step would have, with the mask and the
method sent along with yes: matchup keeps the mask in its oceanval_matchups
folder and masks the matchups by it (see oceanval.unified_grid), and the
script names that copy. Back goes to the step the question was asked on, with
the boxes as they were left.
"""

import os
import traceback

from oceanval import grid_align, recipes_gui, sim_unify, unified_grid, viewer_catalogue

# the simulation's own panel and those of the other simulations
PANELS = 5
# what the panels are called, as the baseline list names them
TITLES = ["This simulation"] + [f"Simulation {number}" for number in range(2, PANELS + 1)]
PANEL_BOXES = ("path", "ndown", "file", "variable", "missing_from", "missing_to")
# the most file names a panel is sent: a directory can hold many thousands
MAX_FILES = 5000


def default_align_form():
    """The step's boxes, as they start: nothing chosen. The first panel's
    path and variable are the simulation's (App.align_first), not boxes."""
    return {
        "baseline": "",
        "method": "",
        "panels": [{name: "" for name in PANEL_BOXES} for _ in range(PANELS)],
    }


def clean_align(form):
    """What the page sent for the step, limited to its own boxes."""
    form = form if isinstance(form, dict) else {}
    sent = form.get("panels") if isinstance(form.get("panels"), list) else []
    panels = []
    for number in range(PANELS):
        panel = sent[number] if number < len(sent) and isinstance(sent[number], dict) else {}
        panels.append({name: str(panel.get(name) or "").strip()[:4096] for name in PANEL_BOXES})
    baseline = str(form.get("baseline") or "").strip()
    method = str(form.get("method") or "").strip()
    return {
        "baseline": baseline if baseline in {str(number) for number in range(1, PANELS + 1)} else "",
        "method": method if method in grid_align.REGRID_METHODS else "",
        "panels": panels,
    }


def first_of(details):
    """The simulation's panel, from what the matchups question says (see
    matchall): {"path", "variables"}, the first file of the first model
    variable's files and the variable's parts (a sum's), or None if it does
    not say."""
    try:
        row = details["rows"][0]
        path = os.path.join(details["sim_dir"], details["files"][row["pattern"]][0])
        parts = [part.strip() for part in str(row["model_variable"]).split("+") if part.strip()]
    except (KeyError, IndexError, TypeError):
        return None
    return {"path": path, "variables": parts} if parts else None


def panel_files(panel, cwd):
    """The files of a panel's directory or file (its path, a directory
    ndown directories down, or one file, used as it is): (whether it is
    one file, the files' absolute paths in natural order, an error in words
    for the user or None). An empty path has no files and no error."""
    from oceanval.app import _path

    text = panel["path"]
    if not text:
        return False, [], None
    path = _path(text, cwd)
    if os.path.isfile(path):
        return True, [path], None
    if not os.path.isdir(path):
        return False, [], "There is no file or directory there."
    try:
        depth = int(panel["ndown"])
        if depth < 0:
            raise ValueError
    except ValueError:
        return False, [], "Say how many directories down the files are: a whole number, 0 or more."
    files = viewer_catalogue.viewer_paths(path, depth)
    if not files:
        where = "in the directory itself" if depth == 0 else f"{depth} director{'y' if depth == 1 else 'ies'} down"
        return False, [], f"There are no netCDF files {where}."
    return False, sorted(files, key=viewer_catalogue.natural_key), None


def _relative(panel, path, cwd):
    """The name of a file of the panel's directory, as the page shows it."""
    from oceanval.app import _path

    return os.path.relpath(path, _path(panel["path"], cwd))


def chosen_file(panel, files, cwd):
    """The file of files (see panel_files) a panel uses: the one its file box
    names (relative to its directory), or the only one there is, or None."""
    if len(files) == 1:
        return files[0]
    return next((path for path in files if panel["file"] and _relative(panel, path, cwd) == panel["file"]), None)


def _ranges(panel):
    """The panel's missing value boxes as viewer ranges [(low, high)], and
    an error, in words for the user, or None."""
    errors = {}
    missing = recipes_gui.check_missing(lambda name: panel.get(name, ""), errors)
    if errors:
        return [], next(iter(errors.values()))
    if missing is None:
        return [], None
    low, high = missing if isinstance(missing, list) else (missing, missing)
    return [(low, high)], None


class GridAlignSteps:
    """The grid alignment step's part of App (see the module's docstring)."""

    def _init_align(self):
        self.align_form = default_align_form()
        # the simulation's own panel (see first_of), and the step the question was asked on
        self.align_first = None
        self.align_from = None
        # what that step carries on with (see _show_align), and the unified grid
        # generated: {"path", "method", "form", "first"}
        self.align_pending = None
        self.align_made = None
        # what generating the unified grid is doing, as words for the page, or None
        self.align_stage = None

    def _show_align(self, source, question, missing, pending):
        """Show the step, from the step source ("report_options" or
        "output"), for the matchups question, which it still holds the
        answer to. missing is source's missing value boxes, which the
        simulation's panel starts with when its own are empty, and pending
        what source carries on with: {"report", "cores", "matchup"} (see
        App._match_up_and_validate) or {"cores", "matchup"} (see
        App._match_up_now). Called with the lock held."""
        self.align_from = source
        self.align_pending = pending
        self.align_first = first_of(question.details or {})
        own = self.align_form["panels"][0]
        if not (own["missing_from"] or own["missing_to"]):
            own["missing_from"], own["missing_to"] = missing
        self.view = "align_grids"
        self._notify()

    def _align_back(self, sent):
        """Back to the step the question was asked on, keeping the boxes.
        Called with the lock held."""
        if isinstance(sent.get("form"), dict):
            self.align_form = clean_align(sent["form"])
        self.view = self.align_from or "report_options"
        self._notify()
        return True

    def _align_made_for(self, form):
        """The unified grid generated (see align_generate), if it was with
        the boxes as form has them and for the simulation's panel as it is,
        else None. Called with the lock held."""
        made = self.align_made
        if made is None or made["form"] != form or made["first"] != self.align_first:
            return None
        return made

    def _use_unified(self, unified):
        """Send the unified grid along with yes (unified, {"path", "method"},
        the mask in the draft folder), or none, and write it into the script
        as the copy matchup keeps in the output directory. Called with the
        lock held."""
        kept = None
        if unified is None:
            if self._run_output is not None:
                self._run_output.pop("unified_grid", None)
        else:
            if self._run_output is None:
                self._run_output = {}
            self._run_output["unified_grid"] = dict(unified)
            directory = self.results_dir or self.out_dir
            kept = {
                "path": os.path.join(directory, "oceanval_matchups", unified_grid.FILE),
                "method": unified["method"],
            }
        self._keep_in_script(unified_grid=kept)

    def _align_state(self):
        first = self.align_first
        return {
            "from": self.align_from,
            "form": self.align_form,
            "first": None if first is None else {
                "file": os.path.basename(first["path"]),
                "path": first["path"],
                "variable": " + ".join(first["variables"]),
            },
            "titles": TITLES,
            "stage": self.align_stage,
            "methods": grid_align.REGRID_METHODS,
            "fvcom_note": grid_align.NN_ONLY_NOTE,
        }

    def _align_panel_source(self, number, panel, first, listed=None):
        """The file and variables of panel number (counted from 1), with the
        error, in words for the user, if it has none. first is the
        simulation's own (see first_of), and listed panel_files(panel) for
        the others (made here if not given). It lists directories, so it is
        called without the lock held."""
        if number == 1:
            if first is None:
                return None, [], "The simulation's files are not known."
            return first["path"], first["variables"], None
        _, files, error = listed or panel_files(panel, self.cwd)
        if error is not None:
            return None, [], error
        path = chosen_file(panel, files, self.cwd)
        if path is None:
            return None, [], None
        return path, [panel["variable"]] if panel["variable"] else [], None

    def _panel_listing(self, number, panel, listed):
        """What the page needs of a panel's files (listed is panel_files'
        answer): {"is_file", "files" (the names, relative to its directory,
        at most MAX_FILES), "files_total", "file" (the one used, "" if
        none)} for panels 2 onwards, else {}."""
        if number == 1:
            return {}
        is_file, files, _ = listed
        path = chosen_file(panel, files, self.cwd)
        names = [] if is_file else [_relative(panel, path_, self.cwd) for path_ in files[:MAX_FILES]]
        return {
            "is_file": is_file,
            "files": names,
            "files_total": 0 if is_file else len(files),
            "file": "" if path is None or is_file else _relative(panel, path, self.cwd),
        }

    def align_panel(self, payload):
        """The map of a panel the page asks for, with what its boxes hold:
        {"ok", "variables" (those the file has that can be mapped, by
        name, with their long names), "item" (a Bokeh json_item, if a
        variable is chosen), "notes"}, or {"ok": False, "error"}, and for
        panels 2 onwards the files found (see _panel_listing). Returns the
        HTTP status and the reply."""
        from oceanval import viewer_plots

        try:
            number = int(payload.get("number"))
            assert 1 <= number <= PANELS
        except (TypeError, ValueError, AssertionError):
            return 400, {"ok": False, "error": "There is no such panel."}
        panel = clean_align({"panels": [payload.get("panel")] * PANELS})["panels"][0]
        with self._lock:
            if self.view != "align_grids":
                return 409, {"ok": False, "error": "This step is over."}
            first = self.align_first
        listed = panel_files(panel, self.cwd) if number > 1 else None
        path, names, error = self._align_panel_source(number, panel, first, listed)
        listing = self._panel_listing(number, panel, listed)
        if error is not None:
            return 200, dict(listing, ok=False, error=error)
        if path is None:
            return 200, dict(listing, ok=True, variables={}, item=None, notes=[])
        missing, error = _ranges(panel)
        if error is not None:
            return 200, dict(listing, ok=False, error=error)
        found = None
        try:
            found = viewer_catalogue.scan(path, 0, wait=False)
            stream = found.streams[0]
            if stream["error"]:
                return 200, dict(listing, ok=False, error=stream["error"])
            variables = {
                name: about["long_name"] or ""
                for name, about in sorted(stream["variables"].items(), key=lambda item: item[0].lower())
                if viewer_catalogue.mappable(about)
            }
            reply = dict(listing, ok=True, variables=variables, item=None, notes=[])
            if not names:
                return 200, reply
            unknown = [name for name in names if name not in variables]
            if unknown:
                return 200, dict(reply, ok=False, error=f"{unknown[0]} is not a variable in the file that can be mapped.")
            about = found.combined(0, names)
            data, _ = found.field(0, names, 0, about.get("surface_level", 0), missing)
            item, notes, _ = viewer_plots.map_plot(
                data, about, about["name"],
                coast=viewer_plots.has_geoviews(),
                mesh=found.mesh(0) if about.get("mesh") else None,
            )
            if not data.notnull().any():
                notes.append("Every cell is missing, so there is nothing to show. Check the missing values.")
            if about.get("mesh") and found.mesh(0).cartesian:
                notes.append("This FVCOM mesh has no longitudes and latitudes, so it cannot be put on a longitude-latitude grid.")
            return 200, dict(reply, item=item, notes=notes, fvcom=bool(about.get("mesh")))
        except ValueError as problem:
            return 200, dict(listing, ok=False, error=str(problem))
        except Exception as problem:  # noqa: BLE001 - told to the page
            traceback.print_exc()
            return 200, dict(listing, ok=False, error=f"{type(problem).__name__}: {problem}")
        finally:
            if found is not None:
                found.close()

    def align_generate(self, form):
        """Generate the unified grid, from the step's boxes (form): every
        panel with a file and a variable is put on the baseline's grid with
        the regridding method chosen (see grid_align.unified). Returns the
        HTTP status and the reply: {"ok", "item", "notes", "shared",
        "total"}, or {"ok": False, "errors"} by box ("baseline", "method",
        "panels") or "error"."""
        from oceanval import viewer_plots

        form = clean_align(form)
        with self._lock:
            if self.view != "align_grids":
                return 409, {"ok": False, "error": "This step is over."}
            self.align_form = form
            first = self.align_first
            # nothing is written in the output directory before the matchups are confirmed
            keep = os.path.join(os.path.dirname(self._draft_script()), unified_grid.FILE)
        sources = {}
        errors = {}
        for number, panel in enumerate(form["panels"], 1):
            path, names, error = self._align_panel_source(number, panel, first)
            if error is not None:
                errors["panels"] = f"{TITLES[number - 1]}: {error}"
            elif path is not None and names:
                sources[str(number)] = (path, names[0], panel)
        if len(sources) < 2 and "panels" not in errors:
            errors["panels"] = "Draw at least one other simulation's grid."
        if not form["baseline"]:
            errors["baseline"] = "Choose the baseline grid."
        elif form["baseline"] not in sources:
            errors["baseline"] = "The baseline must be a simulation whose grid is drawn."
        if not form["method"]:
            errors["method"] = "Choose a regridding method."
        if errors:
            return 400, {"ok": False, "errors": errors}
        fields = {}
        meshes = {}
        try:
            for number, (path, name, panel) in sources.items():
                title = TITLES[int(number) - 1]
                missing, error = _ranges(panel)
                if error is not None:
                    return 400, {"ok": False, "errors": {"panels": f"{title}: {error}"}}
                found = viewer_catalogue.scan(path, 0, wait=False)
                try:
                    about = found.combined(0, [name])
                    if about.get("mesh"):
                        # FVCOM: the surface values at the nodes (or elements), which CDO cannot read
                        meshes[number] = found.mesh(0)
                        data, _ = found.field(0, [name], 0, about.get("surface_level", 0), missing)
                        fields[number] = grid_align.fvcom_field(meshes[number], data)
                        continue
                finally:
                    found.close()
                fields[number] = grid_align.field_for_cdo(path, name, about.get("surface_level", 0), missing)
            if form["baseline"] in meshes:
                return 400, {"ok": False, "errors": {"baseline": "An FVCOM simulation cannot be the baseline grid. Choose another simulation's."}}
            if meshes and form["method"] != grid_align.FVCOM_METHOD:
                return 400, {"ok": False, "errors": {"method": "FVCOM output can only be put on another grid by nearest neighbour."}}
            with self._lock:
                # the grid kept until now is replaced
                self.align_made = None
            try:
                mask, shared, total = grid_align.unified(
                    fields, form["baseline"], form["method"], fvcom=meshes, keep=keep, progress=self._align_progress)
            finally:
                with self._lock:
                    self.align_stage = None
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
        with self._lock:
            if self.view != "align_grids":
                return 409, {"ok": False, "error": "This step is over."}
            self.align_made = {"path": keep, "method": form["method"], "form": form, "first": first}
            self._notify()
        return 200, {"ok": True, "item": item, "notes": notes, "shared": shared, "total": total}

    def _align_progress(self, stage, simulation=None):
        """Say what generating the unified grid is doing, to the page (see
        sim_unify.stage_text); simulation is a panel's number, as unified() has it."""
        name = None if simulation is None else TITLES[int(simulation) - 1]
        with self._lock:
            self.align_stage = sim_unify.stage_text(stage, name)
            self._notify()

    def align_continue(self, form):
        """Carry on from the step, with its boxes (form), as the step it was
        asked on would have, with the unified grid generated for them sent
        along with yes. Returns the HTTP status and the reply for the page:
        {"ok"}, or {"ok": False, "errors": {"panels"}} if no grid has been
        generated with these boxes."""
        form = clean_align(form)
        with self._lock:
            if self.view != "align_grids" or self._held_matchups() is None:
                return 409, {"ok": False, "error": "This step is over."}
            self.align_form = form
            made = self._align_made_for(form)
            if made is None or not os.path.isfile(made["path"]):
                self._notify()
                return 400, {"ok": False, "errors": {"panels": "Generate the unified grid with these choices first."}}
            unified = {"path": made["path"], "method": made["method"]}
            pending = self.align_pending or {}
            if self.align_from == "report_options":
                report = pending.get("report")
            else:
                question = self._held_matchups()
                self._match_up_now(pending.get("cores"), pending.get("matchup") or {}, unified)
                writer = self._script_writer
                self._notify()
        if self.align_from == "report_options":
            return self._match_up_and_validate(
                "align_grids", report, pending.get("cores"), pending.get("matchup") or {}, unified
            )
        self._write_script(writer)
        self.console.write("y\n")
        question.respond(self._yes({}, question))
        return 200, {"ok": True}
