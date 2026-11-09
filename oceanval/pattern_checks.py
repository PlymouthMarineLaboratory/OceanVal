"""The compatibility check (see compat_check) of each file pattern of a matchup run.

While the window shows the mapping between variables and simulation files, one check is made
for each file pattern: of the first file of the pattern, for the first variable matched up
from it. The results are shown on the output step, and no matchup can be run while any row of
any pattern has failed (a warning, or a row skipped, does not stop it). Each check runs in a
process of its own (`compat_check.Compat`), at most MAX_PARALLEL at once. Nothing here raises.
"""

import contextlib
import os
import threading

from oceanval import compat_check, model_expr

# the checks drawn maps and regrid files: not many at once
MAX_PARALLEL = 2


def requests(details, sim_dir):
    """What is checked, from the details of the question of whether the matchups are right: a
    dict for each file pattern, in the order of the rows, {"pattern", "variable" (the simulation
    variable of the first row with the pattern, the first name if it is a formula), "path" (the
    pattern's first file, absolute, or None if it has none)}."""
    found, order = {}, []
    files = details.get("files") if isinstance(details.get("files"), dict) else {}
    for row in details.get("rows") or []:
        if not isinstance(row, dict) or not row.get("model_variable") or not row.get("pattern"):
            continue
        pattern = row["pattern"]
        if pattern in found:
            continue
        names = model_expr.names(row["model_variable"])
        paths = files.get(pattern) or []
        found[pattern] = {
            "pattern": pattern,
            "variable": names[0] if names else row["model_variable"],
            "path": os.path.abspath(os.path.join(sim_dir, paths[0])) if paths else None,
        }
        order.append(pattern)
    return [found[pattern] for pattern in order]


class PatternChecks:
    """The checks of a question's file patterns. changed() is called (from a thread) when anything
    changes; state() is what the page is shown, and item(number) the interactive map of one pattern."""

    def __init__(self, question, wanted, changed=None):
        self.question = question
        self.changed = changed
        self.items = [{"request": request, "check": None, "error": None} for request in wanted]
        self.cancelled = False
        self._lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._run, name="oceanval-pattern-checks", daemon=True).start()
        return self

    def cancel(self):
        self.cancelled = True
        with self._lock:
            checks = [item["check"] for item in self.items if item["check"] is not None]
        for check in checks:
            check.cancel()

    def _tell(self):
        if self.changed is not None and not self.cancelled:
            with contextlib.suppress(Exception):
                self.changed()

    def _run(self):
        waiting = list(self.items)
        running = []
        while (waiting or running) and not self.cancelled:
            while waiting and len(running) < MAX_PARALLEL:
                item = waiting.pop(0)
                request = item["request"]
                if request["path"] is None or not os.path.isfile(request["path"]):
                    with self._lock:
                        item["error"] = "The pattern has no file to check."
                    continue
                check = compat_check.Compat(request["path"], self._tell, variable=request["variable"])
                with self._lock:
                    item["check"] = check
                check.start()
                running.append(item)
            running = [item for item in running if item["check"].status != "done"]
            self._tell()
            threading.Event().wait(0.5)

    def _one(self, item):
        check = item["check"]
        request = item["request"]
        out = {
            "pattern": request["pattern"],
            "variable": request["variable"],
            "file": request["path"],
            "error": item["error"],
        }
        if check is None:
            out.update(status="failed" if item["error"] else "waiting", rows=[], fvcom=False, notice=None, image=None, map=False)
            return out
        state = check.state()
        out.update(
            status="done" if state["status"] == "done" else "running",
            rows=state["rows"],
            fvcom=state["fvcom"],
            notice=state["notice"],
            image=state["image"],
            map=state["map"],
        )
        return out

    def state(self):
        """What the page shows: {"patterns": [...], "done": whether every pattern has been checked,
        "passed": done, with at least one pattern and no row failed, "failed": patterns with a failed
        row (or no file), "total"}."""
        with self._lock:
            patterns = [self._one(item) for item in self.items]
        failed = [
            one for one in patterns
            if one["error"] or any(row["status"] == compat_check.FAILED for row in one["rows"])
        ]
        done = all(one["status"] in ("done", "failed") for one in patterns)
        return {
            "patterns": patterns,
            "done": done,
            "passed": bool(patterns) and done and not failed,
            "failed": len(failed),
            "total": len(patterns),
        }

    def item(self, number):
        """The Bokeh json_item of the interactive map of pattern number, or None."""
        with self._lock:
            if not isinstance(number, int) or not 0 <= number < len(self.items):
                return None
            check = self.items[number]["check"]
        return check.item() if check is not None else None
