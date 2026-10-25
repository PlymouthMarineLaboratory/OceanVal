"""The analysis report, built one variable at a time as analyze() works them out.

As soon as a variable is worked out, its page is built, the overview is run
again, and every page's navigation is brought up to date, so the report can be
read long before the last variable is done: until then it is an interim report,
each page with a note at its top saying so. Unlike validate's interim report
(see oceanval.live), it is not built again at the end: its pages are the
report's, and once the last is in, only the navigation is written again,
without the note. The simulation comparison (see oceanval.sim_compare) builds
its report the same way.

The pages are built by a thread of the analysis's own process, while the next
variable is worked out. nbconvert runs in processes of its own, in the
analysis's process group, so that whatever stops the analysis (the oceanval
window's Stop, Ctrl+C) stops them too. nctoolkit forks pools of workers as the
analysis goes on, and a worker would wait for ever for a lock the thread held
when it was forked, so the thread takes none a worker could need: what nbconvert
prints goes to the book's log, the thread's own lines go out by os.write, not
through sys.stdout's lock, and it reads no netCDF.

    <folder>/status.json     how far it has got, which the oceanval window reads
    <folder>/report/build.log   what nbconvert printed as the pages were built
"""

import contextlib
import datetime
import json
import os
import queue
import shutil
import sys
import threading
import traceback

import oceanval
from oceanval import analysis, live

# how far the report has got, in the analysis's folder
STATUS = "status.json"
# what nbconvert printed, in the report's book
LOG = "build.log"


def _banner(what, made, expected, stopped):
    title = f"Interim {what} report"
    if stopped:
        text = f"with the {made} of the {expected} variables worked out before the {what} stopped."
    else:
        when = datetime.datetime.now().strftime("%H:%M")
        text = (
            f"built from {made} of {expected} variables so far, at {when}. Each variable is "
            "added as it is worked out: reload the page to see the latest. The report is "
            "complete once they all have been."
        )
    return live.banner_html(title, text)


class InterimBook:
    """The report of the analysis in folder (the analysis's FOLDER), of
    expected variables, built from templates (see analysis.Templates) as
    they are worked out. what is the word for it: "analysis", or
    "comparison".

    start() starts it; add() is given each variable, once its results, and
    the info file saying what they are, have been saved; finish() waits for
    the last page and returns the report's first page; stop() is for an
    analysis that did not finish.
    """

    def __init__(self, folder, expected, templates, what="analysis"):
        self.folder = folder
        self.expected = expected
        self.templates = templates
        self.what = what
        self.book_dir = os.path.join(folder, analysis.REPORT)
        self.notebooks_dir = os.path.join(self.book_dir, "notebooks")
        # the pages as nbconvert writes them, which the navigation is added to
        self.raw_dir = os.path.join(self.book_dir, "_build", "raw")
        self.html_dir = os.path.join(self.book_dir, "_build", "html")
        self.status_path = os.path.join(folder, STATUS)
        self.overview = os.path.join(self.notebooks_dir, f"{analysis.LANDING}.ipynb")
        # number: (page, name) of each page built
        self.pages = {}
        # for the oceanval window's grid of the variables, by each one's
        # key: the page of each added to the report, and those whose page
        # could not be built
        self.added = {}
        self.failed = []
        self.queue = queue.Queue()
        # how many variables have been taken from the queue, and how many
        # had been when the overview was last run
        self.taken = 0
        self.overview_run = None
        self.landing = None
        self.log = None
        self.thread = None
        self.stopping = threading.Event()

    # ---- telling the user, and the oceanval window ----

    def say(self, text):
        line = f"Interim {self.what} report: {text}\n"
        try:
            # not print: see the module's docstring
            os.write(sys.stdout.fileno(), line.encode())
        except (AttributeError, OSError, ValueError):
            # stdout is not a file, as when pytest captures it
            sys.stdout.write(line)
            sys.stdout.flush()

    def write_status(self, state, building=None, building_key=None):
        status = {
            "state": state,
            "what": self.what,
            "pages": len(self.pages),
            "expected": self.expected,
            "built": [name for _, name in sorted(self.pages.values())],
            "building": building,
            "building_key": building_key,
            "added": self.added,
            "failed": self.failed,
            "landing": self.landing,
            "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        with contextlib.suppress(OSError):
            oceanval._write_atomically(self.status_path, json.dumps(status, indent=1))

    # ---- the analysis's side ----

    def start(self):
        analysis.prepare_book(self.folder, self.templates)
        os.makedirs(self.raw_dir, exist_ok=True)
        os.makedirs(os.path.join(self.html_dir, "notebooks"), exist_ok=True)
        # beside the pages, as for the report built all at once
        shutil.copyfile(
            os.path.join(self.book_dir, "oceanval_wordmark.svg"),
            os.path.join(self.html_dir, "notebooks", "oceanval_wordmark.svg"),
        )
        self.log = open(os.path.join(self.book_dir, LOG), "w")
        self.write_status("starting")
        self.thread = threading.Thread(target=self._work, name=f"oceanval-{self.what}-report", daemon=True)
        self.thread.start()

    def add(self, number, entry):
        """Build the page of the variable entry, the number-th, now that it
        has been worked out."""
        self.queue.put((number, entry))

    def finish(self):
        """Wait for the last page, then write the navigation of them all
        again, without the note that the report is interim. Returns the
        path of the report's first page."""
        self._wait()
        if self.overview_run != self.taken:
            # the info file has more in it than when the overview last ran
            self._run_overview()
        self._bind(complete=True)
        self._tidy()
        # only the pages with their navigation are kept
        shutil.rmtree(self.raw_dir, ignore_errors=True)
        analysis.link_report(self.folder)
        self.write_status("complete")
        return self.landing

    def stop(self, now=False):
        """The analysis stopped before it finished: the pages of the
        variables worked out are kept, with a note saying so. Those not built
        yet are, unless now, when only the one being built is waited for."""
        if self.thread is None:
            return
        if now:
            self.stopping.set()
        self._wait()
        with contextlib.suppress(Exception):
            if self.pages:
                self._bind(stopped=True)
        self._tidy()
        self.write_status("stopped")

    def _wait(self):
        self.queue.put(None)
        self.thread.join()
        self.thread = None

    def _tidy(self):
        with contextlib.suppress(Exception):
            oceanval._remove_notebook_temp_files(self.book_dir)
        if self.log is not None:
            with contextlib.suppress(OSError):
                self.log.close()
            self.log = None

    # ---- the builder's side ----

    def _work(self):
        finishing = False
        while not finishing:
            batch = [self.queue.get()]
            # the variables worked out while the last were being added are added together
            while True:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            finishing = None in batch
            variables = [item for item in batch if item is not None]
            if variables and not self.stopping.is_set():
                try:
                    self._add(variables)
                except Exception:
                    traceback.print_exc(file=self.log)

    def _run(self, path):
        oceanval._execute_notebooks([path], log=self.log)
        oceanval._notebooks_to_html([path], self.raw_dir, log=self.log)

    def _run_overview(self):
        self.overview_run = self.taken
        try:
            self._run(self.overview)
        except Exception:
            traceback.print_exc(file=self.log)

    def _add(self, variables):
        added = []
        for number, entry in variables:
            if self.stopping.is_set():
                return
            self.taken += 1
            self.write_status("building", building=entry["name"], building_key=entry["key"])
            try:
                path = analysis.write_page(self.folder, number, entry, self.templates)
                self._run(path)
            except Exception:
                traceback.print_exc(file=self.log)
                self.say(
                    f"the page of {entry['name']} could not be built. See "
                    f"{os.path.join(self.book_dir, LOG)}."
                )
                self.failed.append(entry["key"])
                continue
            self.pages[number] = (analysis.page_name(number, entry), entry["name"])
            added.append((number, entry))
        if not added or self.stopping.is_set():
            self.write_status("building")
            return
        self._run_overview()
        announced = self.landing is not None
        self._bind()
        for number, entry in added:
            # its page, now it has the navigation
            self.added[entry["key"]] = os.path.join(self.html_dir, "notebooks", f"{self.pages[number][0]}.html")
        added = [entry["name"] for _, entry in added]
        where = "" if announced else f" Open it at {self.landing}"
        self.say(
            f"added {', '.join(added)} ({len(self.pages)} of {self.expected} "
            f"variable{'s' if self.expected != 1 else ''}).{where}"
        )
        self.write_status("building")

    def _bind(self, complete=False, stopped=False):
        """The report's contents, the overview then each variable's page in
        the order analyze() was given them. Each page is written again with
        the navigation to them all."""
        pages = [page for _, (page, _) in sorted(self.pages.items())]
        analysis._toc(self.book_dir, pages)
        stems = [
            stem
            for stem in [analysis.LANDING, *pages]
            if os.path.exists(os.path.join(self.raw_dir, f"{stem}.html"))
        ]
        if not stems:
            return
        oceanval._write_offline_report_pages(
            self.html_dir,
            [os.path.join(self.notebooks_dir, f"{stem}.ipynb") for stem in stems],
            raw_dir=self.raw_dir,
            banner=None if complete else _banner(self.what, len(self.pages), self.expected, stopped),
        )
        first = stems[0]
        self.landing = os.path.join(self.html_dir, "notebooks", f"{first}.html")
