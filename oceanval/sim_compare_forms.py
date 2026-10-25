"""The boxes of the oceanval window's Compare simulations steps, and what they are
turned into (see oceanval.sim_compare_app): the simulations, the variables in each,
whether and how to put the simulations on one grid, and the arguments of
compare_simulations() the steps end in. The analyses are chosen on the options
step Analyze has (see oceanval.analysis_forms)."""

import os

from oceanval import analysis, analysis_forms, sim_compare, sim_unify

MOST_SIMULATIONS = sim_compare.MOST_SIMULATIONS
# the simulation rows the step starts with
START_ROWS = 2
MOST_ROWS = analysis_forms.MOST_ROWS


# ---- the simulations ----


def blank_sim():
    return {"name": "", "simdir": "", "ndown": "", "exclude": "", "require": ""}


def default_sims_form(cwd):
    """The simulations step's boxes, as they start."""
    return {"rows": [blank_sim() for _ in range(START_ROWS)]}


def default_output_form(cwd):
    """The last page's boxes, as they start. replace_analysis is whether the
    user has ticked to replace the comparison the output directory has
    already, which it must have if it does (the box Analyze has, on the same
    page)."""
    return {"out_dir": cwd, "out": "simulation_comparison.py", "replace_analysis": False}


def clean_output_form(form, cwd):
    """The last page's boxes as the page sent them, limited to its own."""
    form = form if isinstance(form, dict) else {}
    return {
        "out_dir": str(form.get("out_dir") if form.get("out_dir") is not None else cwd).strip(),
        "out": str(form.get("out") or "").strip(),
        "replace_analysis": bool(form.get("replace_analysis")),
    }


def check_output(form, cwd):
    """The last page's boxes (see clean_output_form) as the full paths
    {"out_dir", "out"} the script and the comparison are saved at: (paths,
    errors), where errors maps each box that cannot be used to the reason. A
    comparison in the output directory is replaced by the new one, so the user
    must have ticked to say so, and named the directory."""
    return analysis_forms.check_output(form, cwd, sim_compare.FOLDER, NAME_THE_DIRECTORY)


# said when a comparison is in the directory worked in and the box is empty; the page says it too
NAME_THE_DIRECTORY = (
    "There is a comparison in the directory you started OceanVal in. "
    "To replace it, name the directory here rather than leaving the box empty."
)


def clean_sims_form(form, cwd):
    """The simulations step's boxes as the page sent them, limited to its own."""
    form = form if isinstance(form, dict) else {}
    rows = []
    for row in (form.get("rows") if isinstance(form.get("rows"), list) else [])[:MOST_SIMULATIONS]:
        row = row if isinstance(row, dict) else {}
        rows.append({box: str(row.get(box) or "").strip()[:500] for box in blank_sim()})
    while len(rows) < START_ROWS:
        rows.append(blank_sim())
    return {"rows": rows}


def check_sims(form, cwd):
    """The simulations step's boxes (see clean_sims_form) as compare_simulations()
    takes the simulations: (sims, errors). sims is a list of {"name",
    "simdir", "ndown", "exclude", "require"} for the rows filled in, and errors
    maps "rows" to {the row's place: {box: reason}} and "simulations" to a
    reason for the step as a whole. A row left empty is skipped."""
    sims = []
    errors = {}
    keys = {}
    for index, row in enumerate(form["rows"]):
        if not any(row.values()):
            continue
        here = {}
        arguments, found = analysis_forms.check_setup(
            {"simdir": row["simdir"], "ndown": row["ndown"], "exclude": row["exclude"], "require": row["require"]},
            cwd,
        )
        for box in ("simdir", "ndown"):
            if box in found:
                here[box] = found[box]
        name = row["name"]
        key = analysis.variable_key(name)
        if not name:
            here["name"] = "Give this simulation a name."
        elif not key:
            here["name"] = "The name needs a letter or a digit in it."
        elif key in keys:
            here["name"] = f"This is the same name as {keys[key]!r}: give it another."
        else:
            keys[key] = name
        if here:
            errors.setdefault("rows", {})[str(index)] = here
            continue
        sims.append(
            {
                "name": name,
                "simdir": arguments["simdir"],
                "ndown": arguments["ndown"],
                "exclude": arguments.get("exclude"),
                "require": arguments.get("require"),
            }
        )
    if not errors and len(sims) < 2:
        errors["simulations"] = "Give at least two simulations to compare."
    if "rows" not in errors and len(sims) > MOST_SIMULATIONS:
        errors["simulations"] = f"At most {MOST_SIMULATIONS} simulations can be compared."
    if errors:
        return None, errors
    return sims, {}


# ---- the variables ----


def clean_rows(rows, names):
    """The variables step's rows as the page sent them: {"name", "models":
    {simulation: model variable}, "filled"}."""
    if not isinstance(rows, list):
        return []
    clean = []
    for row in rows[:MOST_ROWS]:
        if not isinstance(row, dict):
            continue
        models = row.get("models") if isinstance(row.get("models"), dict) else {}
        clean.append(
            {
                "name": str(row.get("name") or "").strip()[:120],
                "models": {
                    sim: "+".join(analysis.model_variable_names(str(models.get(sim) or ""))) for sim in names
                },
                "filled": bool(row.get("filled")),
            }
        )
    return clean


def check_rows(rows, found_by_sim):
    """Check the variables step's rows (see clean_rows) against the streams of each
    simulation (found_by_sim: {simulation: streams}). Returns (placed_by_sim,
    errors): placed_by_sim is {simulation: analysis_forms.check_rows's placed},
    with the same variables in the same order in each; errors maps a row's place to
    {"name": reason, "models": {simulation: reason}}, and "rows" to a reason for
    the step as a whole. A row left empty is skipped."""
    names = list(found_by_sim)
    errors = {}
    placed_by_sim = {}
    for sim in names:
        sim_rows = [{"name": row["name"], "model_variable": row["models"].get(sim, ""), "filled": row["filled"]} for row in rows]
        placed, found = analysis_forms.check_rows(sim_rows, found_by_sim[sim])
        placed_by_sim[sim] = placed
        for index, boxes in found.items():
            if index == "rows":
                continue
            for box, reason in boxes.items():
                if box == "name":
                    errors.setdefault(index, {})["name"] = reason
                else:
                    errors.setdefault(index, {}).setdefault("models", {})[sim] = reason
    for index, row in enumerate(rows):
        if not row["name"] and not any(row["models"].values()):
            continue
        here = errors.setdefault(str(index), {})
        if not row["name"] and "name" not in here:
            here["name"] = "Give this variable a name."
        for sim in names:
            # a variable is wanted in every simulation: a row skipped as empty for one of them is not empty
            if not row["models"].get(sim) and sim not in here.get("models", {}):
                here.setdefault("models", {})[sim] = "Give the model variable holding it."
        if not here:
            del errors[str(index)]
    if not errors:
        # one variable in every simulation, in the same units
        for number, row in enumerate(placed_by_sim[names[0]]):
            first = row["units"]
            for sim in names[1:]:
                units = placed_by_sim[sim][number]["units"]
                if first and units and first != units:
                    errors.setdefault(str(row["index"]), {}).setdefault("models", {})[sim] = (
                        f"This is in {units} here, and in {first} in {names[0]}: they must be the same to be compared."
                    )
    if errors:
        return None, errors
    if not placed_by_sim[names[0]]:
        return None, {"rows": "Add a variable to compare."}
    return placed_by_sim, {}


def combined(placed_by_sim):
    """The variables placed in all the simulations as the options step takes
    them (see analysis_forms.check_rows): one row for each variable, the first
    simulation's, with the fewest levels any simulation has (so that a bottom or
    a water column is only asked for where all have one)."""
    names = list(placed_by_sim)
    rows = []
    for number, row in enumerate(placed_by_sim[names[0]]):
        here = dict(row)
        here["levels"] = min(placed_by_sim[sim][number]["levels"] for sim in names)
        here["per_sim"] = {
            sim: {
                "model_variable": placed_by_sim[sim][number]["model_variable"],
                "pattern": placed_by_sim[sim][number]["pattern"],
                "files": len(placed_by_sim[sim][number]["files"]),
                "levels": placed_by_sim[sim][number]["levels"],
                "time_res": placed_by_sim[sim][number]["time_res"],
            }
            for sim in names
        }
        rows.append(here)
    return rows


# ---- the grids ----

UNIFY_BOXES = ("lon_min", "lon_max", "lat_min", "lat_max", "res")


def default_unify_form():
    """The grids step's boxes, as they start: nothing is chosen for the user."""
    return {"need": "", "method": "", "baseline": "", **{box: "" for box in UNIFY_BOXES}}


def clean_unify(form):
    form = form if isinstance(form, dict) else {}
    clean = {box: str(form.get(box) or "").strip()[:60] for box in default_unify_form()}
    clean["need"] = clean["need"].lower() if clean["need"].lower() in ("yes", "no") else ""
    clean["method"] = clean["method"].lower() if clean["method"].lower() in ("box", "baseline") else ""
    return clean


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def check_unify(form, names):
    """The grids step's boxes (see clean_unify) as compare_simulations()'s unify:
    (unify, errors). unify is None for "no", and errors maps the boxes that cannot
    be used to the reason, "need" to the question's, and "method" to the second's."""
    errors = {}
    if not form["need"]:
        return None, {"need": "Say whether the simulations' grids need to be put on one grid."}
    if form["need"] == "no":
        return None, {}
    if not form["method"]:
        return None, {"method": "Choose how to put the simulations on one grid."}
    if form["method"] == "baseline":
        if not form["baseline"]:
            return None, {"baseline": "Choose the simulation whose grid the others are put on."}
        if form["baseline"] not in names:
            return None, {"baseline": "This is not one of the simulations."}
        return {"baseline": form["baseline"]}, {}
    values = {}
    for box in UNIFY_BOXES:
        values[box] = _number(form[box])
        if not form[box]:
            errors[box] = "Fill this in."
        elif values[box] is None:
            errors[box] = "This must be a number."
    if errors:
        return None, errors
    unify = {
        "lon": [values["lon_min"], values["lon_max"]],
        "lat": [values["lat_min"], values["lat_max"]],
        "res": values["res"],
    }
    try:
        sim_unify.check_unify(unify, names)
    except ValueError as error:
        text = str(error)
        box = "lon_max" if "lon" in text else "lat_max" if "lat" in text else "res"
        return None, {box: text}
    return unify, {}


# ---- the run ----


def arguments(sims, placed_by_sim, shifts, unify, out_dir, times_index=None, **analyses):
    """compare_simulations()'s arguments, from the steps. sims are check_sims's,
    shifts {simulation: days or None}, times_index {simulation: the path of the
    times of its files the check of the dates read}, and analyses
    analysis_forms.arguments's keywords (without the setup)."""
    setup = {"simdir": sims[0]["simdir"], "ndown": sims[0]["ndown"], "out_dir": out_dir}
    out = analysis_forms.arguments(setup, combined(placed_by_sim), **analyses)
    for name in ("sim_dir", "n_dirs_down", "exclude", "require", "time_shift", "variables"):
        out.pop(name, None)
    used = set(analyses.get("climatology") or {}) | set(analyses.get("phenology") or {})
    used |= set(analyses.get("long_term") or {}) | set(analyses.get("seasonal") or {})
    used |= set(analyses.get("map_changes") or {}) | set((analyses.get("transects") or {}).get("rows", {}))
    names = [sim["name"] for sim in sims]
    variables = {}
    for number, row in enumerate(placed_by_sim[names[0]]):
        if row["name"] in used:
            variables[row["name"]] = {sim: placed_by_sim[sim][number]["model_variable"] for sim in names}
    simulations = []
    for sim in sims:
        entry = {"name": sim["name"], "sim_dir": sim["simdir"], "n_dirs_down": sim["ndown"]}
        if sim.get("exclude"):
            entry["exclude"] = sim["exclude"]
        if sim.get("require"):
            entry["require"] = sim["require"]
        if shifts.get(sim["name"]):
            entry["time_shift"] = shifts[sim["name"]]
        if (times_index or {}).get(sim["name"]):
            entry["times_index"] = times_index[sim["name"]]
        simulations.append(entry)
    result = {"simulations": simulations, "variables": variables}
    result.update(out)
    if unify is not None:
        result["unify"] = unify
    return result


def script_text(arguments):
    return analysis_forms.script_text(arguments, "compare_simulations", "An OceanVal comparison of simulations")


def out_path(path):
    return os.path.abspath(path)
