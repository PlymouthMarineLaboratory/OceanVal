"""The oceanval window's check of how OceanVal will view the model output.

Once the matchups of a matchup and validate run are confirmed, the window
can show, for each variable to be matched up: a map of its sea surface at
one time step, as nctoolkit's plot() draws it, with the matchup's
as_missing applied; the files of it in the years matched up; and the
months and days missing from those years. It is worked out in a process of
its own (see main, which ModelCheck runs), as reading the times of every
file can take a minute or two, and so that it can be stopped. The times it
reads are saved, for matchup to use rather than read them all again (see
load_times_index).

    python -c "from oceanval.model_check import main; main()"

reads the request as JSON on its standard input, and writes what it finds
as it goes, one line each, MARKER followed by JSON.
"""

import calendar
import contextlib
import gc
import importlib.util
import itertools
import json
import os
import pickle
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

MARKER = "\x1eoceanval-check "

# how long a map is waited for before it is drawn again without a coastline,
# which cartopy can try to download
PLOT_TIMEOUT = 120

# the size of a map in the window, in pixels
PLOT_SIZE = {"frame_width": 360, "frame_height": 280}

# what nctoolkit's plot() loads from the web, and the package that has the
# same files: (pattern, library, the file's path in the library)
_CDN = (
    (
        re.compile(r"https://cdn\.bokeh\.org/bokeh/release/(bokeh(?:-[a-z]+)*)-\d[\w.]*?\.min\.js"),
        "bokeh",
        lambda match: f"js/{match.group(1)}.min.js",
    ),
    (
        re.compile(r"https://cdn\.holoviz\.org/panel/[^/\"']+/dist/([^\"'\s?#]+)"),
        "panel",
        lambda match: match.group(1),
    ),
    (
        re.compile(r"https://cdn\.jsdelivr\.net/npm/@holoviz/geoviews@[^/\"']+/dist/([^\"'\s?#]+)"),
        "geoviews",
        lambda match: match.group(1),
    ),
)

# where each library keeps the files: (package, folder in it)
LIBRARIES = {
    "bokeh": ("bokeh", "server/static"),
    "panel": ("panel", "dist"),
    "geoviews": ("geoviews", "dist"),
}


def library_root(name):
    """The folder of an installed library's static files, which the window
    serves in place of a CDN, or None. Nothing is imported to find it."""
    if name not in LIBRARIES:
        return None
    package, folder = LIBRARIES[name]
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    root = os.path.join(list(spec.submodule_search_locations)[0], folder)
    return root if os.path.isdir(root) else None


def localise(html):
    """html, as nctoolkit's plot() saves it, loading the files it would load
    from the web from lib/<library>/<path> beside it instead, where the
    installed library has them, so that the window needs nothing from
    anywhere else."""
    for pattern, library, local in _CDN:
        root = library_root(library)
        if root is None:
            continue

        def replace(match, root=root, library=library, local=local):
            path = local(match)
            if not os.path.isfile(os.path.join(root, path)):
                return match.group(0)
            return f"lib/{library}/" + urllib.parse.quote(path)

        html = pattern.sub(replace, html)
    return html


# ---- the times of the files ----


def _times_in(times, start, end):
    """All the times of the files in times ({path: DataFrame}), in the years
    start to end, as one DataFrame with path and index (the time's place in
    its file) columns too."""
    import pandas as pd

    frames = [
        df.assign(path=path, index=range(len(df))) for path, df in sorted(times.items())
    ]
    if not frames:
        return pd.DataFrame(columns=["year", "month", "day", "path", "index"])
    found = pd.concat(frames, ignore_index=True)
    return found[(found.year >= start) & (found.year <= end)].reset_index(drop=True)


def files_in_years(times, start, end):
    """The files of times ({path: DataFrame of year, month and day}) with any
    time in the years start to end, sorted, and the first of those times,
    as {"path", "index", "date"} (index is its place in the file), or None."""
    found = _times_in(times, start, end)
    if len(found) == 0:
        return [], None
    first = found.sort_values(["year", "month", "day", "path", "index"], kind="stable").iloc[0]
    return sorted(set(found.path)), {
        "path": first.path,
        "index": int(first["index"]),
        "date": f"{int(first.year):04d}-{int(first.month):02d}-{int(first.day):02d}",
    }


def _mode(values):
    """The most common of values, the largest of those tied."""
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return max(counts, key=lambda value: (counts[value], value))


def _years_text(years):
    """Years as runs, e.g. "1990-1994, 2002"."""
    runs = []
    for year in sorted(years):
        if runs and year == runs[-1][1] + 1:
            runs[-1][1] = year
        else:
            runs.append([year, year])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def _month_text(year, month):
    return f"{calendar.month_abbr[month]} {year}"


def date_report(times, start, end, time_res="monthly", gridded=False):
    """What is missing from the times of one variable's files in the years
    start to end: times is {path: DataFrame of year, month and day}, and
    time_res the output's (see oceanval.time_res.label). Returns

        {"years": [{"year", "months": [{"month", "status", "count",
                    "expected", "missing_days"}]}],
         "issues": [sentences], "summary": a sentence, "ok": bool,
         "gaps": bool, "first", "last" (dates), "steps": the number of times}

    gaps is whether any dates are missing: months with no output, months
    with fewer times than usual, or years asked for with no output. ok is
    that there is nothing at all to say, so is False too for times that
    are in more than one file.

    status is "complete", "partial" (output more often than monthly with
    fewer times than usual: for daily output, missing_days says which) or
    "missing". What is usual is worked out from the run itself, the most
    common number of times and last day of each month of the year, so
    calendars without leap days or with 30-day months look complete; where
    there is a 29 February, February has one in leap years only. The
    years shown run from the first with output to the last; the years
    asked for that have none are an issue. With gridded, years with fewer
    than 12 months are said to be left out of gridded matchups when others
    have all 12, as they are.
    """
    found = _times_in(times, start, end)
    if len(found) == 0:
        return {
            "years": [],
            "issues": [f"There is no output in the years {start}-{end}."],
            "summary": f"No output in {start}-{end}",
            "ok": False,
            "gaps": True,
            "first": None,
            "last": None,
            "steps": 0,
        }
    monthly = time_res == "monthly"
    # output more often than daily has several times a day in one file
    sub_daily = bool(found.duplicated(["path", "year", "month", "day"]).any())
    years = sorted(set(int(year) for year in found.year))
    shown = range(years[0], years[-1] + 1)

    present = {}
    for (year, month), group in found.groupby(["year", "month"]):
        present[(int(year), int(month))] = group
    expected_count = {}
    expected_last = {}
    # the last day of each month with output in each year it has any
    groups_last = {}
    for month in range(1, 13):
        groups = [group for (_, mm), group in present.items() if mm == month]
        if groups:
            groups_last[month] = [int(group.day.max()) for group in groups]
            expected_count[month] = _mode([len(set(group.day)) for group in groups])
            expected_last[month] = _mode(groups_last[month])

    issues = []
    missing_months = []
    partial_months = []
    report_years = []
    for year in shown:
        months = []
        for month in range(1, 13):
            group = present.get((year, month))
            entry = {"month": month, "count": 0, "expected": None, "missing_days": []}
            if group is None:
                entry["status"] = "missing"
                missing_months.append((year, month))
            else:
                days = set(int(day) for day in group.day)
                entry["count"] = len(group) if monthly else len(days)
                if monthly:
                    entry["status"] = "complete"
                elif time_res == "1d":
                    last = expected_last[month]
                    if month == 2 and max(groups_last[2]) == 29:
                        # a calendar with leap days has them in leap years only
                        last = calendar.monthrange(year, 2)[1]
                    expected = range(1, last + 1)
                    entry["expected"] = len(expected)
                    entry["missing_days"] = [day for day in expected if day not in days]
                    entry["status"] = "partial" if entry["missing_days"] else "complete"
                else:
                    entry["expected"] = expected_count[month]
                    entry["status"] = "partial" if len(days) < expected_count[month] else "complete"
                if entry["status"] == "partial":
                    partial_months.append((year, month))
            months.append(entry)
        report_years.append({"year": year, "months": months})

    without = [year for year in range(start, end + 1) if year not in years]
    if without:
        issues.append(
            f"No output in {_years_text(without)}, which "
            + ("is" if len(without) == 1 else "are")
            + f" in the years matched up ({start}-{end})."
        )
    if missing_months:
        listed = ", ".join(_month_text(year, month) for year, month in missing_months)
        issues.append(
            f"{len(missing_months)} month{'s' if len(missing_months) != 1 else ''} "
            f"with no output: {listed}."
        )
    if partial_months:
        parts = []
        for year, month in partial_months:
            entry = report_years[year - years[0]]["months"][month - 1]
            if entry["missing_days"]:
                days = entry["missing_days"]
                parts.append(
                    f"{_month_text(year, month)} (day{'s' if len(days) != 1 else ''} "
                    f"{_days_text(days)})"
                )
            else:
                parts.append(
                    f"{_month_text(year, month)} ({entry['count']} of {entry['expected']} times)"
                )
        issues.append(
            f"{len(partial_months)} month{'s' if len(partial_months) != 1 else ''} "
            f"with times missing: {', '.join(parts)}."
        )

    if not sub_daily:
        repeated = found.groupby(["year", "month", "day"]).path.nunique()
        repeated = repeated[repeated > 1]
        if len(repeated) > 0:
            dates = [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in repeated.index]
            shown_dates = ", ".join(dates[:5]) + (f" and {len(dates) - 5} more" if len(dates) > 5 else "")
            issues.append(
                f"{len(dates)} date{'s are' if len(dates) != 1 else ' is'} in more than one file: "
                f"{shown_dates}."
            )

    if gridded:
        whole = [year for year in years if all((year, month) in present for month in range(1, 13))]
        short = [year for year in years if year not in whole]
        if whole and short:
            issues.append(
                f"Gridded matchups leave out {_years_text(short)}, as "
                + ("it has" if len(short) == 1 else "they have")
                + " fewer than 12 months and other years have all 12."
            )

    first = found.sort_values(["year", "month", "day"]).iloc[0]
    last = found.sort_values(["year", "month", "day"]).iloc[-1]
    n_months = 12 * len(shown)
    if issues:
        gaps = len(missing_months) + len(partial_months)
        summary = (
            f"{gaps} of {n_months} months have gaps" if gaps else "See the notes below"
        )
    else:
        summary = (
            f"All {n_months} months from {shown[0]} to {shown[-1]} are there"
            if monthly
            else f"All {n_months} months from {shown[0]} to {shown[-1]} are complete"
        )
    return {
        "years": report_years,
        "issues": issues,
        "summary": summary,
        "ok": not issues,
        "gaps": bool(without or missing_months or partial_months),
        "first": f"{int(first.year):04d}-{int(first.month):02d}-{int(first.day):02d}",
        "last": f"{int(last.year):04d}-{int(last.month):02d}-{int(last.day):02d}",
        "steps": len(found),
    }


def _days_text(days):
    """Days of a month as runs, e.g. "3-5, 9"."""
    runs = []
    for day in days:
        if runs and day == runs[-1][1] + 1:
            runs[-1][1] = day
        else:
            runs.append([day, day])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def _stat(path):
    info = os.stat(path)
    return info.st_size, info.st_mtime_ns


def save_times_index(path, times):
    """Save the times of the files in times ({path: DataFrame}), with each
    file's size and modification time, for load_times_index."""
    index = {}
    for file, df in times.items():
        with contextlib.suppress(OSError):
            size, mtime = _stat(file)
            index[file] = {"size": size, "mtime_ns": mtime, "times": df}
    partial = path + ".partial"
    with open(partial, "wb") as file:
        pickle.dump({"version": 1, "files": index}, file)
    os.replace(partial, path)


def load_times_index(path, paths):
    """The times save_times_index saved in path of those of paths whose size
    and modification time are unchanged, as {path: DataFrame}. Anything
    else is left out, and nothing is raised: matchup reads what is missing
    itself."""
    try:
        with open(path, "rb") as file:
            index = pickle.load(file)
        files = index["files"]
    except Exception:
        return {}
    found = {}
    if not isinstance(files, dict):
        return {}
    for file in paths:
        # saved by absolute path, and matchup's can be relative
        entry = files.get(file) or files.get(os.path.abspath(file))
        if not isinstance(entry, dict):
            continue
        try:
            if _stat(file) == (entry["size"], entry["mtime_ns"]):
                found[file] = entry["times"]
        except (OSError, KeyError):
            continue
    return found


# ---- the map ----


class _TimedOut(Exception):
    pass


def _alarm(seconds):
    """Raise _TimedOut after seconds, in the main thread of the process."""

    def raise_it(*args):
        raise _TimedOut()

    signal.signal(signal.SIGALRM, raise_it)
    signal.alarm(seconds)


def surface_plot(path, index, model_variable, as_missing, out, title="", timeout=PLOT_TIMEOUT):
    """Draw the sea surface of model_variable at time index of the file
    path with nctoolkit's plot(), as a gridded matchup reads it: its top
    level, with as_missing applied, and a sum of variables "a+b". The page
    is saved to out (.html), loading what it needs from lib/ beside it (see
    localise). The map is drawn again without a coastline if drawing it
    fails or takes longer than timeout seconds."""
    import nctoolkit as nc

    selection = model_variable.split("+")
    ds = nc.open_data(path, checks=False)
    ds.subset(variables=selection)
    ds.subset(time=index)
    ds.run()
    if ds.contents.nlevels.values[0] > 1:
        ds.cdo_command("topvalue")
    if as_missing is not None:
        ds.as_missing(as_missing)
    if len(selection) > 1:
        ds.sum_all()
    ds.run()

    def draw(**kwargs):
        if os.path.exists(out):
            os.remove(out)
        ds.plot(out=out, title=title, **PLOT_SIZE, **kwargs)

    in_main = threading.current_thread() is threading.main_thread()
    try:
        if in_main and timeout:
            _alarm(timeout)
        try:
            draw()
        finally:
            if in_main and timeout:
                signal.alarm(0)
    except Exception:
        draw(coast=False)
    with open(out, encoding="utf-8") as file:
        html = file.read()
    with open(out, "w", encoding="utf-8") as file:
        file.write(localise(html))


# ---- the process that checks ----


def _short(error):
    text = str(error).strip() or type(error).__name__
    text = text.splitlines()[-1] if text.splitlines() else text
    return text if len(text) < 300 else text[:297] + "..."


def check(request, emit):
    """Check the model output request describes, saying what is found with
    emit(event) as it goes. request is

        {"sim_dir", "years": [start, end], "as_missing", "fvcom",
         "rows": [{"variable", "model_variable", "pattern", "time_res",
                   "gridded"}],
         "files": {pattern: [absolute paths]}, "out_dir"}

    and the events, as dicts with "event":
        "progress"  {"done", "total"}, as the times of the files are read
        "indexed"   {"index"}, where they are saved (see load_times_index)
        "files"     {"variable", "files" (relative to sim_dir), "total",
                     "first", "dates" (see date_report)}
        "plot"      {"variable", "file", "date"} or {"variable", "error"}
        "done"      {}
    """
    from oceanval.matchall import index_times

    sim_dir = request["sim_dir"]
    start, end = request["years"]
    out_dir = request["out_dir"]
    fvcom = bool(request.get("fvcom"))
    files = request["files"]

    listed = []
    for paths in files.values():
        for path in paths:
            if path not in listed and "restart" not in path:
                listed.append(path)
    total = len(listed)
    times = {}
    last = [0.0]

    said = [None]

    def progress(done, _of=None):
        now = time.monotonic()
        if done != said[0] and (done == total or now - last[0] > 0.25):
            last[0] = now
            said[0] = done
            emit({"event": "progress", "done": done, "total": total})

    progress(0)
    index_times(listed, fvcom=fvcom, times=times, progress=progress)
    progress(total)
    index = os.path.join(out_dir, "times.pkl")
    save_times_index(index, times)
    emit({"event": "indexed", "index": index})

    for number, row in enumerate(request["rows"], 1):
        variable = row["variable"]
        paths = files.get(row["pattern"]) or []
        own = {path: times[path] for path in paths if path in times}
        used, first = files_in_years(own, start, end)
        emit(
            {
                "event": "files",
                "variable": variable,
                "files": [os.path.relpath(path, sim_dir) for path in used],
                "total": len(paths),
                "first": first and dict(first, path=os.path.relpath(first["path"], sim_dir)),
                "dates": date_report(
                    own, start, end, row.get("time_res") or "monthly", row.get("gridded", False)
                ),
            }
        )
        if fvcom:
            emit(
                {
                    "event": "plot",
                    "variable": variable,
                    "error": "There is no map of FVCOM output: it is on an unstructured "
                    "grid, which is put on a regular one only as it is matched up.",
                }
            )
            continue
        if first is None:
            emit({"event": "plot", "variable": variable, "error": f"There is no output in {start}-{end} to draw."})
            continue
        name = f"plot_{number}.html"
        try:
            surface_plot(
                first["path"],
                first["index"],
                row["model_variable"],
                request.get("as_missing"),
                os.path.join(out_dir, name),
            )
        except Exception as error:
            emit({"event": "plot", "variable": variable, "error": "The map could not be drawn: " + _short(error)})
        else:
            emit({"event": "plot", "variable": variable, "file": name, "date": first["date"]})
        finally:
            gc.collect()
    emit({"event": "done"})


def main():
    """What a ModelCheck's process runs: the request as JSON on standard
    input, and the events on standard output (see check)."""
    request = json.loads(sys.stdin.read())
    out = sys.__stdout__

    def emit(event):
        out.write(MARKER + json.dumps(event) + "\n")
        out.flush()

    try:
        check(request, emit)
    except Exception as error:
        emit({"event": "failed", "error": _short(error)})
        sys.exit(1)


# ---- in the window ----


class ModelCheck:
    """A check of the model output for the window, in a process of its own.

    question is the id of the question of whether the matchups are right
    that it is for, and request what check() is given, without out_dir: a
    folder of its own is made for the maps and the times. changed() is
    called whenever something new is found. state() is what the page shows.
    """

    _numbers = itertools.count(1)

    def __init__(self, question, request, changed):
        self.number = next(ModelCheck._numbers)
        self.question = question
        self.request = dict(request)
        self.changed = changed
        self.folder = tempfile.mkdtemp(prefix="oceanval_check_")
        self.request["out_dir"] = self.folder
        self.status = "running"
        self.progress = {"done": 0, "total": 0}
        self.index = None
        self.error = None
        self.process = None
        self._lock = threading.Lock()
        self._stopped = False
        self._finished = False
        self._tail = []
        self.files = {}
        self.rows = {
            row["variable"]: {"files": None, "plot": None, "dates": None}
            for row in request["rows"]
        }

    def start(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = (
            f"import sys; sys.path.insert(0, {root!r}); "
            "from oceanval.model_check import main; main()"
        )
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-u", "-c", code],
                cwd=self.folder,
                env=dict(os.environ, PYTHONUNBUFFERED="1"),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
        except OSError as error:
            self._end("failed", f"The check could not be started: {error}")
            return
        threading.Thread(target=self._run, name="oceanval-model-check", daemon=True).start()

    def _run(self):
        process = self.process
        with contextlib.suppress(OSError, ValueError):
            process.stdin.write(json.dumps(self.request))
            process.stdin.close()
        for line in process.stdout:
            line = line.rstrip("\r\n")
            start = line.find(MARKER)
            if start < 0:
                # kept to say why, should it fail
                self._tail = (self._tail + [line])[-20:]
                continue
            with contextlib.suppress(ValueError, KeyError, TypeError):
                self._take(json.loads(line[start + len(MARKER):]))
        returncode = process.wait()
        if returncode != 0 and not self._finished:
            said = [line.strip() for line in self._tail if line.strip()]
            self._end("failed", self.error or (said[-1] if said else f"The check stopped ({returncode})."))
        elif not self._finished:
            self._end("failed", "The check stopped before it finished.")

    def _take(self, event):
        kind = event.get("event")
        with self._lock:
            if self._stopped:
                return
            if kind == "progress":
                self.progress = {"done": int(event["done"]), "total": int(event["total"])}
            elif kind == "indexed":
                self.index = event["index"]
            elif kind == "files" and event["variable"] in self.rows:
                row = self.rows[event["variable"]]
                self.files[event["variable"]] = list(event["files"])
                row["files"] = {
                    "count": len(event["files"]),
                    "total": int(event["total"]),
                    "first": event.get("first"),
                }
                row["dates"] = event.get("dates")
            elif kind == "plot" and event["variable"] in self.rows:
                row = self.rows[event["variable"]]
                if event.get("file"):
                    row["plot"] = {"file": event["file"], "date": event.get("date")}
                else:
                    row["plot"] = {"error": event.get("error") or "The map could not be drawn."}
            elif kind == "failed":
                self.error = event.get("error")
                return
            elif kind == "done":
                self._finished = True
                self.status = "done"
        self.changed()

    def _end(self, status, error=None):
        with self._lock:
            if self._stopped or self._finished:
                return
            self._finished = True
            self.status = status
            self.error = error
        self.changed()

    @property
    def finished(self):
        """Whether it found all it looks for."""
        return self.status == "done"

    def stop(self):
        """Stop the check, and anything it started, if it is still going."""
        with self._lock:
            if not self._finished and not self._stopped:
                self.status = "stopped"
            self._stopped = True
        process = self.process
        if process is not None and process.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGTERM)
            threading.Thread(target=self._kill_later, daemon=True).start()

    def _kill_later(self):
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.process.pid, signal.SIGKILL)

    def remove(self):
        """Stop the check and remove its folder, with the maps and the times."""
        self.stop()
        process = self.process
        if process is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=6)
        gc.collect()
        shutil.rmtree(self.folder, ignore_errors=True)

    def files_of(self, variable):
        """The files of variable in the years matched up, relative to the
        simulation's directory, or None if they are not known yet."""
        with self._lock:
            found = self.files.get(variable)
            return list(found) if found is not None else None

    def state(self, href):
        """What the page shows. href(file) is the link to a file of the
        check, a map."""
        with self._lock:
            rows = []
            for row in self.request["rows"]:
                found = self.rows[row["variable"]]
                plot = found["plot"]
                if plot is not None and plot.get("file"):
                    plot = dict(plot, href=href(plot["file"]))
                rows.append(
                    {
                        "variable": row["variable"],
                        "title": row.get("title") or row["variable"],
                        "model_variable": row["model_variable"],
                        "pattern": row["pattern"],
                        "time_res": row.get("time_res"),
                        "observations": list(row.get("observations") or []),
                        "files": found["files"],
                        "plot": plot,
                        "dates": found["dates"],
                    }
                )
            return {
                "status": self.status,
                "progress": dict(self.progress),
                "error": self.error,
                "rows": rows,
            }
