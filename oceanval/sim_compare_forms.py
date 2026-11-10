"""The boxes of the oceanval window's Compare simulations steps, and what they are
turned into (see oceanval.sim_compare_app): the simulations, the variables in each,
whether and how to put the simulations on one grid, and the arguments of
compare_simulations() the steps end in. The analyses are chosen on the options
step Analyze has (see oceanval.analysis_forms)."""

import os

from oceanval import analysis, analysis_forms, history_forms, model_expr, sim_compare, sim_unify

MOST_SIMULATIONS = sim_compare.MOST_SIMULATIONS
# the simulation rows the step starts with
START_ROWS = 2
MOST_ROWS = analysis_forms.MOST_ROWS


# ---- the simulations ----


def blank_sim():
    return {"name": "", "simdir": "", "ndown": "", "exclude": "restart spinup", "require": ""}


def row_is_blank(row):
    """Whether a simulation's boxes are all empty, bar the Skip list as it
    starts (see blank_sim)."""
    start = blank_sim()
    return not any(value for box, value in row.items() if value != start.get(box))


def default_sims_form(cwd):
    """The simulations step's boxes, as they start."""
    return {"rows": [blank_sim() for _ in range(START_ROWS)]}


def default_output_form(cwd):
    """The last page's boxes, as they start. replace_analysis is whether the
    user has ticked to replace the comparison the output directory has
    already, which it must have if it does (the box Analyze has, on the same
    page). out is simulation_comparison.py, or the first of
    simulation_comparison1.py... that is free in cwd, and out_typed whether
    the user typed it (the only name warned of if the file exists)."""
    from oceanval.app import free_name

    return {
        "out_dir": cwd,
        "out": free_name(cwd, "simulation_comparison"),
        "out_typed": False,
        "replace_analysis": False,
        **history_forms.defaults(),
    }


def clean_output_form(form, cwd):
    """The last page's boxes as the page sent them, limited to its own."""
    form = form if isinstance(form, dict) else {}
    return {
        "out_dir": str(form.get("out_dir") if form.get("out_dir") is not None else cwd).strip(),
        "out": str(form.get("out") or "").strip(),
        "out_typed": bool(form.get("out_typed")),
        "replace_analysis": bool(form.get("replace_analysis")),
        **history_forms.clean(form),
    }


def check_output(form, cwd):
    """The last page's boxes (see clean_output_form) as the full paths
    {"out_dir", "out"} the script and the comparison are saved at: (paths,
    errors), where errors maps each box that cannot be used to the reason. A
    comparison in the output directory is replaced by the new one, so the user
    must have ticked to say so, and named the directory."""
    return analysis_forms.check_output(
        form, cwd, sim_compare.FOLDER, NAME_THE_DIRECTORY, kind=history_forms.SIMCOMPARE
    )


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
        if row_is_blank(row):
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
                    sim: model_expr.tidied(str(models.get(sim) or "")) for sim in names
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
    # units that are not the first simulation's are converted on the units step (see unit_rows)
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


# ---- the units ----


def _units_differ(first, units):
    """Whether units are written otherwise than the first simulation's: one with
    none, beside one with some, too."""
    return str(first or "").strip() != str(units or "").strip()


def unit_rows(placed_by_sim, formulas=True):
    """The units step's rows: one for each variable of each simulation after the
    first whose units are written otherwise than the first simulation's, which it
    is converted into, as validate's units step converts observations into the
    model's (see oceanval.units). Each is {"key", "simulation", "variable",
    "model_variable", "units", "baseline": {"simulation", "model_variable",
    "units"}, "check"}, check being unit_conversion.suggest's (or
    unit_conversion.unknown's) for this simulation's units into the first's,
    or unit_conversion.formula's for a variable that is a formula in either
    (always shown, as its units cannot be told). An empty list if every
    simulation is in the first one's units and none is a formula. With
    formulas False (raw FVCOM output on its mesh, which cannot be converted), a
    formula is shown only if the units are written otherwise."""
    from oceanval import unit_conversion

    names = list(placed_by_sim)
    rows = []
    is_formula = lambda row: model_expr.kind(row["model_variable"]) == model_expr.FORMULA  # noqa: E731
    for position, sim in enumerate(names[1:], start=1):
        for number, first in enumerate(placed_by_sim[names[0]]):
            own = placed_by_sim[sim][number]
            # a formula's units cannot be told, so it is always shown, for the user to check
            flagged = formulas and (is_formula(own) or is_formula(first))
            if not _units_differ(first["units"], own["units"]) and not flagged:
                continue
            if flagged and is_formula(own):
                check = unit_conversion.formula(
                    model_expr.normalise(own["model_variable"]), own["parts"][0], own["units"]
                )
            elif flagged:
                check = unit_conversion.formula(
                    model_expr.normalise(first["model_variable"]), first["parts"][0], first["units"]
                )
                check["note"] = check["note"].replace("This is a formula", f"In {names[0]}, this is a formula", 1)
            elif not str(first["units"] or "").strip():
                check = unit_conversion.unknown(f"the files of {names[0]} do not give its units")
            elif not str(own["units"] or "").strip():
                check = unit_conversion.unknown(f"the files of {sim} do not give its units")
            else:
                check = unit_conversion.suggest(first["name"], own["units"], first["units"])
            rows.append(
                {
                    "key": f"{position}:{number}",
                    "simulation": sim,
                    "variable": first["name"],
                    "model_variable": own["model_variable"],
                    "units": own["units"] or None,
                    "baseline": {
                        "simulation": names[0],
                        "model_variable": first["model_variable"],
                        "units": first["units"] or None,
                    },
                    "check": check,
                }
            )
    return rows


def unit_row_signature(row):
    """What a units step's row converts, so that what its boxes held is kept only
    for the same row (see oceanval.app._units_row)."""
    return [row["simulation"], row["variable"], row["model_variable"], row["units"], row["baseline"]["units"]]


def check_unit_boxes(sent, rows):
    """The units step's boxes, sent as {key: {"multiplier": text, "adder":
    text}} for rows (see unit_rows), as compare_simulations()'s
    unit_conversions: (conversions, errors), errors {key: message}. A blank box
    is 1 or 0, which is kept: it says the units are the first simulation's."""
    from oceanval import units

    sent = sent if isinstance(sent, dict) else {}
    numbers, errors = units.check_conversions({row["key"]: sent.get(row["key"]) or {} for row in rows})
    conversions = {}
    for row in rows:
        if row["key"] not in numbers:
            continue
        multiplier, adder = numbers[row["key"]]
        if multiplier < 0:
            errors[row["key"]] = "Multiply by a number more than 0: a change of units never makes values negative."
            continue
        conversions.setdefault(row["variable"], {})[row["simulation"]] = {"multiplier": multiplier, "adder": adder}
    return (None, errors) if errors else (conversions, {})


# ---- the grids ----

UNIFY_BOXES = ("lon_min", "lon_max", "lat_min", "lat_max", "res")


def default_unify_form():
    """The grids step's boxes, as they start: nothing is chosen for the user."""
    return {
        "need": "", "method": "", "baseline": "", "regrid": "", **{box: "" for box in UNIFY_BOXES},
        # whether part of the domain is masked out, by a file (see oceanval.app.check_domain_mask): No to start
        "domain_mask": "no", "domain_mask_path": "",
    }


def clean_unify(form):
    form = form if isinstance(form, dict) else {}
    clean = {box: str(form.get(box) or "").strip()[:60] for box in default_unify_form() if box != "domain_mask_path"}
    clean["domain_mask_path"] = str(form.get("domain_mask_path") or "").strip()[:4096]
    clean["domain_mask"] = "yes" if str(form.get("domain_mask")).lower() == "yes" else "no"
    clean["need"] = clean["need"].lower() if clean["need"].lower() in ("yes", "no") else ""
    clean["method"] = clean["method"].lower() if clean["method"].lower() in ("box", "baseline") else ""
    # how they are regridded; blank is bilinear
    clean["regrid"] = clean["regrid"].lower() if clean["regrid"].lower() in sim_unify.METHODS else ""
    return clean


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def check_unify(form, names, fvcom=(), mixed=False):
    """The grids step's boxes (see clean_unify) as compare_simulations()'s unify:
    (unify, errors). unify is None for "no", and errors maps the boxes that cannot
    be used to the reason, "need" to the question's, and "method" to the second's.
    fvcom are the simulations that are raw FVCOM output (a mesh): if all of them
    are, only a box is a grid to put them on, and mixed says that some of the
    others are not, so there is no "no": the FVCOM output is put on some grid."""
    errors = {}
    regrid = {"regrid": form["regrid"]} if form.get("regrid") else {}
    need = "yes" if mixed else form["need"]
    if not need:
        return None, {"need": "Say whether the simulations' grids need to be put on one grid."}
    if need == "no":
        return None, {}
    if not form["method"]:
        return None, {"method": "Choose how to put the simulations on one grid."}
    if form["method"] == "baseline":
        if fvcom and not mixed:
            return None, {"method": "FVCOM output on its own can only be put on a lon-lat box."}
        if not form["baseline"]:
            return None, {"baseline": "Choose the simulation whose grid the others are put on."}
        if form["baseline"] not in names:
            return None, {"baseline": "This is not one of the simulations."}
        if form["baseline"] in fvcom:
            return None, {"baseline": "This is FVCOM output, a mesh: choose a simulation on a grid."}
        return {"baseline": form["baseline"], **regrid}, {}
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
        **regrid,
    }
    try:
        sim_unify.check_unify(unify, names)
    except ValueError as error:
        text = str(error)
        box = "lon_max" if "lon" in text else "lat_max" if "lat" in text else "res"
        return None, {box: text}
    return unify, {}


# ---- what to treat as missing ----

ALL = "All"


def blank_missing_row():
    return {"apply": "", "from": "", "to": ""}


def clean_missing_rows(rows):
    """The missing values page's rows as sent: a list of {"apply", "from", "to"}
    of strings, whatever was sent."""
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        out.append({key: str(row.get(key) or "").strip() for key in ("apply", "from", "to")})
    return out


def check_missing_rows(rows, names, final=True):
    """The values to treat as missing, from the rows of the options step:
    (as_missing for all, {simulation: as_missing}, errors). Each row says what
    it applies to (All, or a simulation's name) and gives a value, or a range as
    From and To. A simulation's own row wins over the All row. Rows with nothing
    in them are left out. errors is {row number: {box: reason}} with the boxes
    apply, missing_from and missing_to; with final False (while the boxes are
    being filled in) a row with only one of an Apply to and a value is not an
    error yet."""
    rows = clean_missing_rows(rows)
    everyone, own, errors = None, {}, {}
    seen = {}
    for number, row in enumerate(rows):
        if not (row["apply"] or row["from"] or row["to"]):
            continue
        problems = {}
        target = row["apply"]
        if target and target != ALL and target not in names:
            problems["apply"] = f"{target} is not one of the simulations."
        elif target and target in seen:
            problems["apply"] = f"{target} is already set in row {seen[target] + 1}."
        elif target:
            seen[target] = number
        elif row["from"] or row["to"]:
            if final:
                problems["apply"] = "Choose what this applies to."
        value = None
        if row["from"] or row["to"] or final:
            value, boxes = analysis_forms.check_missing(row["from"], row["to"])
            if boxes:
                problems.update(boxes)
            elif value is None and not problems and (final or row["from"] or row["to"]):
                problems["missing_from"] = "Fill in the value, or the start of a range."
        if problems:
            errors[number] = problems
        elif value is not None and target:
            if target == ALL:
                everyone = value
            else:
                own[target] = value
    return everyone, own, errors


# ---- the run ----


def arguments(
    sims,
    placed_by_sim,
    shifts,
    unify,
    out_dir,
    times_index=None,
    domain_mask=None,
    unit_conversions=None,
    as_missing_by_sim=None,
    **analyses,
):
    """compare_simulations()'s arguments, from the steps. sims are check_sims's,
    shifts {simulation: days or None}, times_index {simulation: the path of the
    times of its files the check of the dates read}, domain_mask the path of the
    mask file (or None), unit_conversions the units step's (see
    check_unit_boxes), and analyses analysis_forms.arguments's keywords (without
    the setup)."""
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
        if (as_missing_by_sim or {}).get(sim["name"]) is not None:
            entry["as_missing"] = as_missing_by_sim[sim["name"]]
        simulations.append(entry)
    result = {"simulations": simulations, "variables": variables}
    result.update(out)
    if unify is not None:
        result["unify"] = unify
    if domain_mask is not None:
        result["domain_mask"] = domain_mask
    # only of the variables compared
    conversions = {name: by_sim for name, by_sim in (unit_conversions or {}).items() if name in variables}
    if conversions:
        result["unit_conversions"] = conversions
    return result


def script_text(arguments):
    return analysis_forms.script_text(arguments, "compare_simulations", "An OceanVal comparison of simulations")


def out_path(path):
    return os.path.abspath(path)
