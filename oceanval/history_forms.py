"""The question every piece of work ends with in the window: whether to add it to the
user's OceanVal history (see oceanval.user_cache), and the name to give it if so.

The boxes are ``add_to_history`` ("yes" or "no", with nothing chosen to start) and
``work_name``. A yes becomes the ``name`` argument of analyze(), compare_simulations(),
validate() or compare(); a no leaves it out, so the work is not kept.
"""

import os

from oceanval import user_cache

# the kinds of work, the same as user_cache.HISTORY_KINDS
ANALYSIS, SIMCOMPARE, VALIDATION, COMPARISON = user_cache.HISTORY_KINDS

# what the question starts as: nothing chosen, which must be answered (the tests that are not about
# the question start it as "no", see tests/conftest.py)
DEFAULT_ANSWER = ""

ASK = "Say whether to add this to your OceanVal history."

# the words of each kind, for the page
WORDS = {
    ANALYSIS: "analysis",
    SIMCOMPARE: "comparison of simulations",
    VALIDATION: "validation",
    COMPARISON: "comparison of validations",
}


def defaults():
    """The two boxes, as they start: nothing chosen."""
    return {"add_to_history": DEFAULT_ANSWER, "work_name": "", "overwrite_history": False}


def clean(form):
    """The two boxes as the page sent them (anything else in form is ignored)."""
    form = form if isinstance(form, dict) else {}
    answer = str(form.get("add_to_history", DEFAULT_ANSWER) or "").strip().lower()
    return {
        "add_to_history": answer if answer in ("yes", "no") else "",
        "work_name": str(form.get("work_name") or "")[:500],
        # the user has agreed to replace work in the history that this run overwrites
        "overwrite_history": bool(form.get("overwrite_history")),
    }


def report_path(kind, out_dir):
    """Where the report of work of this kind built in out_dir will be (the file the
    history opens; its existence is what keeps the work in the list)."""
    from oceanval import analysis, sim_compare

    if kind == ANALYSIS:
        return os.path.join(out_dir, analysis.FOLDER, analysis.REPORT_LINK)
    if kind == SIMCOMPARE:
        return os.path.join(out_dir, sim_compare.FOLDER, sim_compare.REPORT_LINK)
    if kind == VALIDATION:
        return os.path.join(out_dir, "oceanval_report.html")
    return os.path.join(out_dir, "oceanval_comparison", "compare", "_build", "html", "notebooks", "comparison_seasonal.html")


def overwritten(kind, out_dir):
    """The work in the history whose report is in out_dir, which a run of this kind there
    replaces: [] if there is none (or out_dir is not known)."""
    if not out_dir:
        return []
    own = user_cache.history_id(kind, report_path(kind, os.path.abspath(out_dir)))
    return [entry for entry in user_cache.history(kind) if entry["id"] == own]


def forget_overwritten(kind, out_dir):
    """Take the work a run in out_dir replaces out of the history (its files are replaced
    by the run itself)."""
    for entry in overwritten(kind, out_dir):
        user_cache.remove_history(kind, entry["id"])


def check(form, kind, out_dir=None):
    """The two boxes: (name, errors). name is the name to give the work, or None if the
    answer is no; errors maps ``add_to_history`` or ``work_name`` to the reason it cannot
    be used; ``overwrite_history`` is the name of work in the history that the run would
    replace, which the user has not yet agreed to replace. out_dir is where the report will be built, if it is known, so that running
    the same work again may keep its name."""
    form = clean(form)
    # work in the history is about to be replaced: the user must agree (the window asks in a
    # pop-out and sends the form again with overwrite_history)
    replaced = overwritten(kind, out_dir)
    if replaced and not form["overwrite_history"]:
        return None, {"overwrite_history": replaced[0]["name"]}
    if not form["add_to_history"]:
        return None, {"add_to_history": ASK}
    if form["add_to_history"] == "no":
        return None, {}
    report = report_path(kind, os.path.abspath(out_dir)) if out_dir else None
    why = user_cache.history_problem(kind, form["work_name"], report)
    if why:
        return None, {"work_name": why}
    return user_cache.clean_history_name(form["work_name"]), {}
