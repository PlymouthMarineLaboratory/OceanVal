"""The interim validation report matchup(live_validation=...) builds as it goes.

Each time a model-observation matchup is made, its page is added to the
interim report, the summary is run again, and every page's navigation is
brought up to date, so the pages can be read long before the last matchup is
made. The interim report is HTML only; the full report is still built by
validate(), once the matchups are done.

The report is built by a process of its own, which matchup tells about each
matchup as it is made (LiveValidation). None of the building happens in
matchup's process: point matchups fork a pool of workers, and a fork can
deadlock the child if another thread is printing, or reading a netCDF file,
at the time.

The interim report is laid out as validate() lays out the full report, with
oceanval_interim_report as its out_dir, so that the notebooks find the
matchups and write their results just as they do for validate():

    <out_dir>/oceanval_interim_report/
        status.json   how far it has got, which the oceanval window reads
        build.log     what the notebooks printed as they ran
        oceanval_report/_build/html/notebooks/summary.html   where to start
        oceanval_report/...                                  the rest of it
        oceanval_results/   what the notebooks work out, which the summary reads
"""

import contextlib
import datetime
import gc
import glob
import html
import importlib.resources
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback

import oceanval
from oceanval import depths, selection, transects

# where the interim report is built, in the report's out_dir
FOLDER = "oceanval_interim_report"

# validate()'s arguments that live_validation takes
OPTIONS = (
    "out_dir", "lon_lim", "lat_lim", "subregions", "fixed_scale", "concise", "transect",
    "depth_bins", "variables", "test",
)

# validate()'s arguments for the other forms of the full report
EXPORTS = ("pdf", "word", "zip")

# the interim report matchup is building, for abandon()
current = None


def matchup_key(kind, variable, source, layer=None):
    """The name of a matchup, as matchup tells the oceanval window of it (see
    oceanval.prompts.tell) and status.json gives its page."""
    return f"{kind}|{variable}|{source}|{layer or ''}"


def check_options(live_validation, out_dir):
    """The interim report's options, from matchup's live_validation.

    None, or False, is no interim report, and None is returned. True builds
    it with validate()'s defaults, and a dict with the validate() arguments
    in it (see OPTIONS), checked as validate() checks them. The report goes
    in out_dir, matchup's own, unless live_validation gives another.
    """
    if live_validation is None or live_validation is False:
        return None
    if live_validation is True:
        live_validation = {}
    if not isinstance(live_validation, dict):
        raise TypeError(
            "live_validation must be True, or a dict of validate()'s report options"
        )
    exports = [name for name in EXPORTS if name in live_validation]
    if exports:
        raise ValueError(
            f"live_validation does not take {', '.join(exports)}: the interim report "
            "is HTML only. Give them to validate(), which builds the full report"
        )
    if "data_dir" in live_validation:
        raise ValueError(
            "live_validation does not take data_dir: the interim report is built "
            "from the matchups matchup() makes, in its out_dir"
        )
    unknown = sorted(str(name) for name in live_validation if name not in OPTIONS)
    if unknown:
        raise ValueError(
            f"live_validation does not take {', '.join(unknown)}. It takes "
            f"{', '.join(OPTIONS)}"
        )
    options = {
        "lon_lim": live_validation.get("lon_lim"),
        "lat_lim": live_validation.get("lat_lim"),
        "concise": live_validation.get("concise", True),
        "fixed_scale": live_validation.get("fixed_scale", False),
        "test": live_validation.get("test", False),
    }
    subregions, region_file, n_regions = oceanval._check_report_options(
        options["lon_lim"],
        options["lat_lim"],
        options["concise"],
        options["fixed_scale"],
        live_validation.get("subregions"),
    )
    if not isinstance(options["test"], bool):
        raise ValueError("test must be a boolean")
    options["transect"] = transects.check_transect(live_validation.get("transect"))
    options["depth_bins"] = depths.check_depth_bins(live_validation.get("depth_bins"))
    # checked against the matchups as they are made: those it does not pick are left out
    options["variables"] = selection.check_variables(live_validation.get("variables"))
    report_dir = live_validation.get("out_dir", out_dir)
    if not isinstance(report_dir, str):
        raise TypeError("live_validation's out_dir must be a string")
    options.update(
        out_dir=os.path.abspath(os.path.expanduser(report_dir)),
        subregions=subregions,
        region_file=region_file,
        n_regions=n_regions,
    )
    return options


def _clear(folder):
    """Remove the interim report an earlier matchup left. On NFS, a folder
    holding a file that is still open cannot be removed until it is closed
    (see matchall._remove_fvcom_dir), so this tries a few times."""
    error = None
    for _ in range(3):
        if not os.path.exists(folder):
            return
        gc.collect()
        try:
            shutil.rmtree(folder)
            return
        except OSError as e:
            error = e
            time.sleep(1)
    print(
        f"The interim validation report left in {folder} could not all be removed "
        f"({error}), so the new one is built over it."
    )


# the builder's process: the same oceanval as matchup's, wherever that was
# imported from, as for the oceanval window's runs (see oceanval.app.Run)
_BOOT = "import sys\nsys.path.insert(0, {root!r})\nfrom oceanval.live import main\nmain()\n"


class LiveValidation:
    """The interim report, as matchup sees it.

    start() starts the process that builds it, matched() tells that process
    about each matchup made, and finish() waits for it to have added them
    all. expected is the matchups matchup is to make, as (kind, variable,
    source, layer), and short_titles the names of the report's sections.
    """

    def __init__(self, options, data_dir, expected, short_titles):
        self.options = options
        self.data_dir = data_dir
        # only the matchups the report validates, if variables picks some
        self.expected = [
            {"kind": kind, "variable": variable, "source": source, "layer": layer}
            for kind, variable, source, layer in expected
            if selection.wanted(options.get("variables"), variable, source)
        ]
        self.short_titles = dict(short_titles)
        self.folder = os.path.join(options["out_dir"], FOLDER)
        self.log_path = os.path.join(self.folder, "build.log")
        self.process = None

    def start(self):
        global current
        # one left by an earlier matchup that never finished, in this session
        abandon()
        _clear(self.folder)
        os.makedirs(self.folder, exist_ok=True)
        if self.options["region_file"] is not None:
            # where the notebooks look for it, as validate puts it
            results = os.path.join(self.folder, "oceanval_results")
            os.makedirs(results, exist_ok=True)
            shutil.copyfile(
                self.options["region_file"],
                os.path.join(results, "custom_subdomains.nc"),
            )
        # the builder prints what it adds where matchup prints, which may be
        # the oceanval window, and everything else to the log
        try:
            console = os.dup(1)
        except OSError:
            console = None
        config = {
            "folder": self.folder,
            "data_dir": self.data_dir,
            "options": self.options,
            "expected": self.expected,
            "short_titles": self.short_titles,
            "parent": os.getpid(),
            "console": console,
        }
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            with open(self.log_path, "w") as log:
                self.process = subprocess.Popen(
                    [sys.executable, "-u", "-c", _BOOT.format(root=root), json.dumps(config)],
                    stdin=subprocess.PIPE,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    # a group of its own, so that it can stop everything it
                    # has started without stopping matchup
                    start_new_session=True,
                    pass_fds=() if console is None else (console,),
                )
        except OSError as error:
            print(
                f"The interim validation report could not be started ({error}), so "
                "the matchups are made without it."
            )
            return
        finally:
            if console is not None:
                os.close(console)
        current = self
        print(
            "An interim validation report is built as the matchups are made, in "
            f"{self.folder}. Each matchup's page is added as soon as it is made.",
            flush=True,
        )

    def matched(self, kind, variable, source, layer=None):
        """Tell the builder that the matchup of variable with the kind
        ("point" or "gridded") of observations of source is made."""
        if not selection.wanted(self.options.get("variables"), variable, source):
            return
        self._send({"kind": kind, "variable": variable, "source": source, "layer": layer})

    def _send(self, message):
        if self.process is None:
            return False
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
            return True
        except (OSError, ValueError):
            print(
                "The interim validation report stopped, so the matchups are made "
                f"without it. See {self.log_path}.",
                flush=True,
            )
            self.stop()
            return False

    def finish(self):
        """Wait for the builder to have added every matchup made."""
        global current
        if self.process is not None and self._send({"finish": True}):
            print("Finishing the interim validation report.", flush=True)
            # still current while waiting, so that Ctrl+C stops it (see abandon)
            returncode = self.process.wait()
            with contextlib.suppress(OSError, ValueError):
                self.process.stdin.close()
            self.process = None
            if returncode != 0:
                print(
                    "The interim validation report could not be finished. "
                    f"See {self.log_path}.",
                    flush=True,
                )
        if current is self:
            current = None

    def stop(self):
        """Stop the builder, and whatever it is running."""
        process, self.process = self.process, None
        if process is None:
            return
        with contextlib.suppress(OSError, ValueError):
            process.stdin.close()
        if process.poll() is None:
            # it stops what it has started, then itself
            process.terminate()
            try:
                process.wait(timeout=10)
                return
            except subprocess.TimeoutExpired:
                pass
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def abandon():
    """Stop the interim report matchup started, if it did not finish it:
    matchup stopped with an error, or was interrupted."""
    global current
    live, current = current, None
    if live is not None:
        live.stop()


# ---- the builder's process ----


def _banner(made, expected, complete):
    """The note at the top of every page of the interim report."""
    if complete:
        if made == expected:
            text = f"complete, with all {made} matchups"
        else:
            text = f"complete, with the {made} of the {expected} matchups that could be made"
        text += ". The full validation report is built from the same matchups next."
    else:
        when = datetime.datetime.now().strftime("%H:%M")
        text = (
            f"built from {made} of {expected} matchups so far, at {when}. Each "
            "matchup is added as it is made: reload the page to see the latest. "
            "The full validation report is built once they all have been."
        )
    return banner_html("Interim validation report", text)


def banner_html(title, text):
    """The note at the top of every page of an interim report: title, then
    text. The analysis's (see oceanval.analysis_report) has it too."""
    return (
        '<div class="oceanval-interim-banner" role="note" style="margin:20px 0 18px;'
        "padding:12px 16px;border:1px solid #f0c36d;border-left:6px solid #d9952b;"
        "border-radius:6px;background:#fff8e7;color:#203047;"
        'font-family:Arial,sans-serif;font-size:14px;line-height:1.5">'
        f"<strong>{html.escape(title)}</strong>, {html.escape(text)}</div>"
    )


class InterimReport:
    """The builder's side: makes the interim report's pages, from the
    matchups matchup says it has made (see main)."""

    def __init__(self, config):
        self.folder = config["folder"]
        self.data_dir = config["data_dir"]
        self.options = config["options"]
        self.expected = config["expected"]
        self.short_titles = config["short_titles"]
        self.parent = config["parent"]
        self.console = (
            None
            if config.get("console") is None
            else os.fdopen(config["console"], "w", buffering=1, encoding="utf-8")
        )
        self.book_dir = os.path.join(self.folder, "oceanval_report")
        self.notebooks_dir = os.path.join(self.book_dir, "notebooks")
        # the pages as nbconvert writes them, which the navigation is added to
        self.raw_dir = os.path.join(self.book_dir, "_build", "raw")
        self.html_dir = os.path.join(self.book_dir, "_build", "html")
        self.status_path = os.path.join(self.folder, "status.json")
        # stem: (variable, label) for each matchup's page, in the order made
        self.pages = {}
        self.landing = None
        # for the oceanval window's grid of the matchups (see matchup_key):
        # the page of each matchup added, once it can be opened, and the
        # matchups whose page could not be made
        self.added = {}
        self.failed = []

    # ---- telling matchup's user, and the oceanval window ----

    def say(self, text):
        print(text, flush=True)
        if self.console is None:
            return
        try:
            self.console.write(text + "\n")
            self.console.flush()
        except (OSError, ValueError):
            # nowhere to say it any more
            self.console = None

    def write_status(self, state, building=None, building_key=None):
        status = {
            "state": state,
            "pages": len(self.pages),
            "expected": len(self.expected),
            "built": [label for _, label in self.pages.values()],
            "building": building,
            "landing": self.landing,
            "updated": datetime.datetime.now().isoformat(timespec="seconds"),
            # each matchup's, by matchup_key
            "included": [self.key(matchup) for matchup in self.expected],
            "building_key": building_key,
            "added": self.added,
            "failed": self.failed,
        }
        oceanval._write_atomically(self.status_path, json.dumps(status, indent=1))

    def title(self, variable):
        return self.short_titles.get(variable) or variable.title()

    def key(self, matchup):
        return matchup_key(matchup["kind"], matchup["variable"], matchup["source"], matchup.get("layer"))

    def label(self, matchup):
        """A matchup's name, as the report's updates give it."""
        source = matchup["source"].upper()
        if matchup["kind"] == "point":
            source += " point data"
        return f"{self.title(matchup['variable'])} ({source})"

    # ---- the notebooks ----

    def finish_notebook(self, path):
        """Put the chunks and the report options in a notebook just written
        from its template, as validate() does for the full report."""
        from oceanval.chunkers import add_chunks

        options = self.options
        script = path[: -len(".ipynb")] + ".py"
        oceanval._run_jupytext(["--set-formats", "ipynb,py:percent"], glob.escape(path))
        add_chunks(paths=[script])
        oceanval._rewrite_notebook_source(
            script,
            options["lon_lim"],
            options["lat_lim"],
            options["fixed_scale"],
            options["concise"],
            options["test"],
            transect=options.get("transect"),
            depth_bins=options.get("depth_bins"),
        )
        oceanval._run_jupytext(["--sync"], glob.escape(path))
        oceanval._fill_notebook_paths(path, self.data_dir, self.folder)
        if os.path.exists(script):
            os.remove(script)

    def write_notebook(self, stem, text):
        path = os.path.join(self.notebooks_dir, f"{stem}.ipynb")
        with open(path, "w") as file:
            file.write(text)
        self.finish_notebook(path)
        return path

    def template(self, name):
        return importlib.resources.files("oceanval").joinpath(f"data/{name}").read_text()

    def run_notebook(self, path):
        oceanval._execute_notebooks([path])
        oceanval._notebooks_to_html([path], self.raw_dir)

    def make_page(self, matchup):
        """Write the notebook for one matchup, as validate() would. Returns
        its path and stem."""
        import dill

        kind = matchup["kind"]
        variable = matchup["variable"]
        source = matchup["source"]
        options = self.options
        if kind == "point":
            layer = matchup["layer"]
            stem = f"{source}_{layer}_{variable}"
            csv = os.path.join(
                self.data_dir, "oceanval_matchups", "point", layer, variable, source, f"{stem}.csv"
            )
            with open(csv.replace(".csv", "_definitions.pkl"), "rb") as file:
                definitions = dill.load(file)
            text = oceanval._point_notebook_text(
                source,
                layer,
                variable,
                definitions[variable].short_name,
                definitions[variable].n_levels,
                self.data_dir,
                self.folder,
            )
        else:
            stem = f"{source}_{variable}"
            [ff_def, *_] = glob.glob(
                f"{glob.escape(self.data_dir)}/oceanval_matchups/gridded/{variable}/{source}_*definitions*.pkl"
            )
            with open(ff_def, "rb") as file:
                definitions = dill.load(file)
            text = oceanval._gridded_notebook_text(
                source,
                variable,
                definitions[variable].short_name,
                oceanval._gridded_seasonal(self.data_dir, variable, source),
                self.data_dir,
                options["subregions"],
                options["region_file"],
                options["n_regions"],
                transect=options.get("transect"),
            )
        return self.write_notebook(stem, text), stem

    # ---- the report ----

    def prepare(self):
        for directory in (self.notebooks_dir, self.raw_dir, os.path.join(self.html_dir, "notebooks")):
            os.makedirs(directory, exist_ok=True)
        # beside the pages, as for the full report
        shutil.copyfile(
            importlib.resources.files("oceanval").joinpath("data/oceanval_wordmark.svg"),
            os.path.join(self.html_dir, "notebooks", "oceanval_wordmark.svg"),
        )
        self.methods = self.write_notebook(
            "methods",
            self.template("001_methods.ipynb").replace("info_text", "Validation metrics summary"),
        )
        self.summary = self.write_notebook(
            "summary", self.template("summary.ipynb").replace("domain_title", "Full domain")
        )
        self.write_status("starting")
        try:
            self.run_notebook(self.methods)
        except Exception:
            traceback.print_exc()

    def add(self, matchups):
        """Add the pages of the matchups made, then run the summary again and
        bring every page's navigation up to date."""
        added = []
        # their pages, which can be opened once bound
        pages = {}
        for matchup in matchups:
            label = self.label(matchup)
            key = self.key(matchup)
            self.write_status("building", building=label, building_key=key)
            try:
                path, stem = self.make_page(matchup)
                self.run_notebook(path)
            except Exception:
                traceback.print_exc()
                self.say(
                    f"Interim validation report: the page for {label} could not be "
                    f"made. See {os.path.join(self.folder, 'build.log')}."
                )
                if key not in self.failed:
                    self.failed.append(key)
                continue
            # made again, if it was already there
            self.pages.pop(stem, None)
            self.pages[stem] = (matchup["variable"], label)
            added.append(label)
            pages[key] = os.path.join(self.html_dir, "notebooks", f"{stem}.html")
            if key in self.failed:
                self.failed.remove(key)
        if added:
            try:
                self.run_notebook(self.summary)
            except Exception:
                traceback.print_exc()
            announced = self.landing is not None
            self.bind()
            self.added.update(pages)
            where = "" if announced or self.landing is None else f" Open it at {self.landing}"
            self.say(
                f"Interim validation report: added {', '.join(added)} "
                f"({len(self.pages)} of {len(self.expected)} matchups).{where}"
            )
        self.write_status("building")

    def bind(self, complete=False):
        """The report's contents, in the order validate() gives the full
        report: the summaries, then each variable's pages. Each page is
        written again with the navigation to them all."""
        stems = [
            stem
            for stem in ("methods", "summary")
            if os.path.exists(os.path.join(self.raw_dir, f"{stem}.html"))
        ]
        toc = [
            "format: jb-book",
            "root: intro",
            "parts:",
            "- caption: Summaries",
            "  chapters:",
            *[f"  - file: notebooks/{stem}.ipynb" for stem in stems],
        ]
        by_variable = {}
        for stem, (variable, _) in self.pages.items():
            by_variable.setdefault(variable, []).append(stem)
        for variable in sorted(by_variable):
            toc += [f"- caption: {self.title(variable)}", "  chapters:"]
            for stem in sorted(by_variable[variable]):
                toc.append(f"  - file: notebooks/{stem}.ipynb")
                stems.append(stem)
        # read by the navigation, as fix_toc's is for the full report
        with open(os.path.join(self.book_dir, "_toc.yml"), "w") as file:
            file.write("\n".join(toc) + "\n")
        oceanval._write_offline_report_pages(
            self.html_dir,
            [os.path.join(self.notebooks_dir, f"{stem}.ipynb") for stem in stems],
            raw_dir=self.raw_dir,
            banner=_banner(len(self.pages), len(self.expected), complete),
        )
        landing = "summary" if "summary" in stems else stems[0] if stems else None
        if landing is not None:
            self.landing = os.path.join(self.html_dir, "notebooks", f"{landing}.html")

    def complete(self):
        if self.pages:
            self.bind(complete=True)
        self.write_status("complete")
        with contextlib.suppress(Exception):
            oceanval._remove_notebook_temp_files(self.book_dir)
        if self.pages:
            self.say(
                f"The interim validation report is complete, with {len(self.pages)} of "
                f"{len(self.expected)} matchups: {self.landing}"
            )
        else:
            self.say("No matchups were made, so the interim validation report is empty.")

    def stop(self, *_):
        """Stop at once, with whatever is running: matchup has stopped, or
        gone."""
        with contextlib.suppress(Exception):
            self.write_status("stopped")
        # the group is this process's own, so this stops nbconvert, whose
        # kernel then stops itself
        with contextlib.suppress(OSError):
            os.killpg(os.getpgrp(), signal.SIGTERM)
        os._exit(1)

    def watch_parent(self):
        """Stop if matchup has gone, as when the oceanval window's Stop ends
        it, which does not reach this process's group."""
        while True:
            time.sleep(1)
            if os.getppid() != self.parent:
                self.stop()

    def run(self, messages):
        """Build the report from the messages matchup sends: a matchup made,
        then finish, or None once matchup has gone without finishing."""
        self.prepare()
        finishing = False
        while not finishing:
            batch = [messages.get()]
            # the matchups made while the last were being added are added together
            while True:
                try:
                    batch.append(messages.get_nowait())
                except queue.Empty:
                    break
            matchups = []
            for message in batch:
                if message is None:
                    if not finishing:
                        self.stop()
                elif message.get("finish"):
                    finishing = True
                else:
                    matchups.append(message)
            if matchups:
                self.add(matchups)
        self.complete()


def _read_messages(stream, messages):
    for line in stream:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict):
            messages.put(message)
    messages.put(None)


def main(argv=None):
    """The builder's process, which LiveValidation.start() starts with its
    configuration as the argument, and the matchups made on stdin."""
    argv = sys.argv[1:] if argv is None else argv
    report = InterimReport(json.loads(argv[0]))
    signal.signal(signal.SIGTERM, report.stop)
    signal.signal(signal.SIGINT, report.stop)
    messages = queue.Queue()
    threading.Thread(
        target=_read_messages,
        args=(sys.stdin, messages),
        name="oceanval-interim-messages",
        daemon=True,
    ).start()
    threading.Thread(
        target=report.watch_parent, name="oceanval-interim-parent", daemon=True
    ).start()
    report.run(messages)
