"""Run `cdo sinfo` on every model file, in the background, once the files are
identified.

`cdo sinfo file.nc` must never segfault. A file that makes it do so cannot be
worked with by OceanVal (every step reads the model's files with CDO), and it
points at the data itself: it may not be CF-compliant. Nothing is done with the
output of sinfo, only whether CDO survived reading the file.

The window starts a check when a simulation is chosen (see
App._start_cdo_check) and blocks everything after it if a file fails; matchup(),
analyze() and compare_simulations() start one when they have found their files
and raise an error before doing any work if one fails (see require). A run the
window started leaves it to the window (prompts.news_marker()).
"""

import contextlib
import os
import signal
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

from oceanval import prompts

# where to find other versions of CDO
LINK = "https://anaconda.org/conda-forge/cdo"
# the longest one file is given, in seconds: a file that takes longer is not
# said to have failed
TIMEOUT = 300
# the files named in the message
LISTED = 5
# the most files looked at at once: they may be on a network disk
MOST_WORKERS = 4
# set False to turn the check off (the tests do, those of this module excepted)
ENABLED = True

OK = "ok"
SEGFAULT = "segfault"
# CDO ended with an error that is not a crash (not a netCDF file, say)
ERROR = "error"
# it could not be run (no cdo), or took too long, or was stopped
UNKNOWN = "unknown"


def _workers():
    try:
        cores = len(os.sched_getaffinity(0))
    except AttributeError:
        cores = os.cpu_count() or 1
    return max(1, min(MOST_WORKERS, cores // 2))


def crashed(returncode, stderr=""):
    """Whether a process that ended with returncode and printed stderr
    segfaulted: the signal it died of (-11), the shell's 128 + 11, or CDO or
    the shell saying so."""
    return returncode in (-signal.SIGSEGV, 128 + signal.SIGSEGV) or "segmentation fault" in (
        stderr or ""
    ).lower()


def message(faulty, total):
    """What to tell the user when `cdo sinfo` segfaulted on the files faulty,
    of total checked."""
    shown = [os.path.basename(path) if len(faulty) > 1 else path for path in faulty[:LISTED]]
    more = len(faulty) - len(shown)
    names = ", ".join(shown) + (f" and {more} more" if more > 0 else "")
    files = "file" if total == 1 else "files"
    return (
        f"`cdo sinfo` segfaulted on {len(faulty)} of {total} model {files} ({names}). "
        "OceanVal will not work with these files, as it reads them with CDO. "
        "This indicates that the data could potentially not be CF-compliant. "
        f"Try changing the CDO version: {LINK}"
    )


class Check:
    """Run `cdo sinfo` on paths in a thread, a few at a time.

    on_fault(path) is called from the thread for the first file that segfaults
    (the others are looked at after it, so that all are known), and done(self)
    when every file has been looked at. results is {path: {"status",
    "returncode", "stderr"}} (stderr only for files that did not end well).
    Nothing here raises."""

    def __init__(self, paths, on_fault=None, done=None, workers=None, timeout=None):
        self.paths = list(dict.fromkeys(os.fspath(path) for path in paths))
        self.on_fault = on_fault
        self.done = done
        self.workers = workers or _workers()
        self.timeout = TIMEOUT if timeout is None else timeout
        self.results = {}
        self.cancelled = False
        self._finished = threading.Event()
        self._lock = threading.Lock()
        self._running = set()
        self._told = False

    def start(self):
        threading.Thread(target=self._run, name="oceanval-cdo-check", daemon=True).start()
        return self

    @property
    def finished(self):
        return self._finished.is_set()

    @property
    def faulty(self):
        """The files that segfaulted so far, in the order given."""
        with self._lock:
            return [path for path in self.paths if self.results.get(path, {}).get("status") == SEGFAULT]

    @property
    def total(self):
        return len(self.paths)

    def message(self):
        """What to tell the user, or None if no file has segfaulted."""
        faulty = self.faulty
        return message(faulty, self.total) if faulty else None

    def wait(self, timeout=None):
        """Wait for every file to be looked at. Returns whether they have been."""
        return self._finished.wait(timeout)

    def cancel(self):
        self.cancelled = True
        with self._lock:
            running = list(self._running)
        for process in running:
            self._kill(process)

    @staticmethod
    def _kill(process):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(process.pid, signal.SIGKILL)

    def _run(self):
        try:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                list(pool.map(self._one, self.paths))
        except Exception:
            pass
        finally:
            self._finished.set()
            if self.done is not None and not self.cancelled:
                with contextlib.suppress(Exception):
                    self.done(self)

    def _one(self, path):
        if self.cancelled:
            return
        result = {"status": UNKNOWN, "returncode": None, "stderr": ""}
        try:
            process = subprocess.Popen(
                ["cdo", "sinfo", path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
        except OSError:
            # no cdo to run
            process = None
        if process is not None:
            with self._lock:
                self._running.add(process)
            try:
                try:
                    _, stderr = process.communicate(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    self._kill(process)
                    process.communicate()
                else:
                    code = process.returncode
                    if crashed(code, stderr):
                        status = SEGFAULT
                    elif code == 0:
                        status = OK
                    elif self.cancelled:
                        status = UNKNOWN
                    else:
                        status = ERROR
                    result = {
                        "status": status,
                        "returncode": code,
                        "stderr": "" if status == OK else (stderr or "")[-500:],
                    }
            finally:
                with self._lock:
                    self._running.discard(process)
        with self._lock:
            self.results[path] = result
            first = result["status"] == SEGFAULT and not self._told
            if first:
                self._told = True
        if first and self.on_fault is not None and not self.cancelled:
            with contextlib.suppress(Exception):
                self.on_fault(path)


def start(paths):
    """Start a check of the files paths for a function of the package, or
    return None: when the check is off, there are no files, or the window
    that started this run has checked them already."""
    paths = list(paths)
    if not ENABLED or not paths or prompts.news_marker() is not None:
        return None
    return Check(paths).start()


def require(check):
    """Wait for check (from start; None is nothing to wait for) to finish and
    raise a ValueError saying what is wrong if `cdo sinfo` segfaulted on a
    file."""
    if check is None:
        return
    check.wait()
    text = check.message()
    if text:
        raise ValueError(text)
