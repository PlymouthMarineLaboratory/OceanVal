"""The dates of the model output, for the oceanval window's matchups page.

While the window asks whether the matchups are right, it reads the times of
every file of each variable to be matched up. The page lists each variable
once for each of the years its datasets are matched up for (see
year_groups), with the files of it in those years and the months and days
missing from them. It is worked out in a process of its own (see main, which
ModelCheck runs), as reading the times of every file can take a minute or
two, and so that it can be stopped. The times it reads are saved, for
matchup to use rather than read them all again (see load_times_index).

    python -c "from oceanval.model_check import main; main()"

reads the request as JSON on its standard input, and writes what it finds
as it goes, one line each, MARKER followed by JSON.
"""

import calendar
import contextlib
import gc
import itertools
import json
import os
import pickle
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

MARKER = "\x1eoceanval-check "

# ---- the times of the files ----


def _times_in(times, start, end):
    """All the times of the files in times ({path: DataFrame}), in the years
    start to end, as one DataFrame with a path column too."""
    import pandas as pd

    frames = [df.assign(path=path) for path, df in sorted(times.items())]
    if not frames:
        return pd.DataFrame(columns=["year", "month", "day", "path"])
    found = pd.concat(frames, ignore_index=True)
    return found[(found.year >= start) & (found.year <= end)].reset_index(drop=True)


def files_in_years(times, start, end):
    """The files of times ({path: DataFrame of year, month and day}) with any
    time in the years start to end, sorted."""
    return sorted(set(_times_in(times, start, end).path))


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


# ---- the process that checks ----


def _short(error):
    text = str(error).strip() or type(error).__name__
    text = text.splitlines()[-1] if text.splitlines() else text
    return text if len(text) < 300 else text[:297] + "..."


# what a dataset's start and end are when it is given none (see
# oceanval.parsers), and matchup's when it is given neither: years as far
# out as these are no limit, so are taken as the output's own
NO_START = -1000
NO_END = 3000


def year_groups(datasets, years):
    """The datasets of a variable, {"source", "gridded", "years"}, grouped
    by the years they are matched up for, the run's years or their own
    within them: [{"years", "sources", "gridded"}], in the order they first
    come, with gridded whether any of a group's datasets are. A variable
    without datasets has one group, for the run's years. matchup sends these
    with its question, and the window shows a row for each."""
    groups = []
    for dataset in datasets or [{"source": None, "gridded": False, "years": years}]:
        found = list(dataset.get("years") or years)
        group = next((group for group in groups if group["years"] == found), None)
        if group is None:
            group = {"years": found, "sources": [], "gridded": False}
            groups.append(group)
        if dataset.get("source") is not None and dataset["source"] not in group["sources"]:
            group["sources"].append(dataset["source"])
        group["gridded"] = group["gridded"] or bool(dataset.get("gridded"))
    return groups


def _bounded(years, times):
    """years, with a start or end that is no limit (see NO_START) taken as
    the first or last year of times ({path: DataFrame of year})."""
    start, end = years
    all_years = [int(year) for df in times.values() for year in df.year]
    if start <= NO_START:
        start = min(all_years, default=start)
    if end >= NO_END:
        end = max(all_years, default=end)
    return [start, end]


def _time_res(times):
    """The time resolution of the times of a row's files ({path: DataFrame}),
    as oceanval.time_res.label gives it, or None if there are none."""
    import pandas as pd

    from oceanval import time_res

    if not times:
        return None
    return time_res.label(pd.concat(times.values(), ignore_index=True))


def check(request, emit):
    """Check the model output request describes, saying what is found with
    emit(event) as it goes. request is

        {"sim_dir", "years": [start, end], "fvcom", "time_shift",
         "rows": [{"variable", "pattern", "time_res",
                   "groups": [{"years", "sources", "gridded"}]}],
         "files": {pattern: [absolute paths]}, "out_dir"}

    where a row's groups are its datasets grouped by the years they are
    matched up for (see year_groups), and time_shift the days matchup shifts
    the model's times by (see oceanval.time_stamps), if any: the files and
    dates are said as shifted, as they are matched up, while the times saved
    are as read, as matchup shifts them itself. The events, as dicts with
    "event", are:
        "progress"  {"done", "total"}, as the times of the files are read
        "indexed"   {"index"}, where they are saved (see load_times_index)
        "files"     {"variable", "total", "time_res" (the row's, or its files'
                     own if it gave none), "groups": [{"years", "sources",
                     "files" (those with output in its years, relative to
                     sim_dir), "dates" (see date_report)}]}, for each row,
                     its groups in the same order, with years that are no
                     limit taken as the output's (see _bounded)
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
            if path not in listed:
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

    shift = request.get("time_shift") or 0
    if shift:
        from oceanval import time_stamps

        # the calendar of each pattern's files, from its first
        calendars = {}
        for paths in files.values():
            if paths:
                calendar_of = time_stamps.calendar(paths[0])
                calendars.update({path: calendar_of for path in paths})
        times = time_stamps.shift_index(dict(times), shift, lambda path: calendars.get(path, "standard"))

    for row in request["rows"]:
        paths = files.get(row["pattern"]) or []
        own = {path: times[path] for path in paths if path in times}
        # a row given no time resolution has its files' own (an analysis's:
        # see oceanval.analysis_app)
        resolution = row.get("time_res") or _time_res(own)
        groups = []
        for group in row.get("groups") or year_groups([], [start, end]):
            years = _bounded(group["years"], own)
            groups.append(
                {
                    "years": years,
                    "sources": list(group.get("sources") or []),
                    "files": [
                        os.path.relpath(path, sim_dir) for path in files_in_years(own, *years)
                    ],
                    "dates": date_report(
                        own, *years, resolution or "monthly", bool(group.get("gridded"))
                    ),
                }
            )
        emit(
            {
                "event": "files",
                "variable": row["variable"],
                "total": len(paths),
                "time_res": resolution,
                "groups": groups,
            }
        )
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
    """A check of the dates of the model output for the window, in a process
    of its own.

    question is the id of the question of whether the matchups are right
    that it is for, and request what check() is given, without out_dir: a
    folder of its own is made for the times. changed() is called whenever
    something new is found. state() is what the page shows.
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
        # the files of each variable's groups in their years, by (variable,
        # the group's place in its row)
        self.files = {}
        # what the page shows of each variable: how many files it has, and,
        # for each of its groups, how many are in its years and its dates,
        # None until they are found
        self.rows = {
            row["variable"]: {
                "total": None,
                "time_res": row.get("time_res"),
                "groups": [
                    {
                        "years": list(group["years"]),
                        "sources": list(group.get("sources") or []),
                        "count": None,
                        "dates": None,
                    }
                    for group in row.get("groups") or year_groups([], request["years"])
                ],
            }
            for row in request["rows"]
        }

    def start(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = (
            f"import sys; sys.path.insert(0, {root!r}); "
            "from oceanval.model_check import main; main()"
        )
        with self._lock:
            if self._stopped:
                return
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
                row["total"] = int(event["total"])
                row["time_res"] = event.get("time_res") or row["time_res"]
                row["groups"] = []
                for index, group in enumerate(event["groups"]):
                    self.files[(event["variable"], index)] = list(group["files"])
                    row["groups"].append(
                        {
                            "years": list(group["years"]),
                            "sources": list(group["sources"]),
                            "count": len(group["files"]),
                            "dates": group["dates"],
                        }
                    )
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
        """Stop the check and remove its folder, with the times."""
        self.stop()
        process = self.process
        if process is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=6)
        gc.collect()
        shutil.rmtree(self.folder, ignore_errors=True)

    def files_of(self, variable, group):
        """The files of variable with output in the years of its group
        numbered group (its place in the row's groups, see year_groups),
        relative to the simulation's directory, or None if they are not
        known yet."""
        with self._lock:
            found = self.files.get((variable, group))
            return list(found) if found is not None else None

    def state(self):
        """What the page shows: for each variable, how many files it has,
        and, for each of its groups, the years, how many of its files are in
        them and their dates (see check)."""
        with self._lock:
            rows = [
                {
                    "variable": row["variable"],
                    "total": self.rows[row["variable"]]["total"],
                    "time_res": self.rows[row["variable"]]["time_res"],
                    "groups": [dict(group) for group in self.rows[row["variable"]]["groups"]],
                }
                for row in self.request["rows"]
            ]
            return {
                "status": self.status,
                "progress": dict(self.progress),
                "error": self.error,
                "rows": rows,
            }
