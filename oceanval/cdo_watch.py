"""How many of a matchup's model files CDO has read, while nctoolkit reads them.

A gridded matchup gives all its model files to nctoolkit at once
(ds_model.merge("time") in oceanval.gridded), which runs CDO on each file in a
pool of its own and says how far it has got only by a progress bar at a
terminal. Each CDO run is a process descended from the matchup's, whose
arguments name the file it reads, so a process of its own (watching) looks at
them: a file has been read once a CDO run of it has been seen and has ended.
It only looks, so it cannot change what is read, or how.

It looks every POLL seconds, so a file read faster than that may not be seen:
the count is never more than the files read, and the caller says when they
all have been. Without psutil, or if the matchup's processes cannot be looked
at, nothing is counted.

It is a process, not a thread: nctoolkit forks its pool while the files are
read, and a thread writing to stdout then could leave a worker holding its
lock. The matchup runs this file as a script, which imports nothing of
OceanVal, as importing the package takes seconds, and reads its request as
JSON on stdin: {"parent": the matchup's pid, "marker", "key", "paths"}. It
stops when it is told to (SIGTERM) or the matchup has gone. Each count is a
line of news on the stdout it shares with the matchup (see
oceanval.prompts.news_marker), written by one os.write, so that nothing the
matchup prints can split it.
"""

import contextlib
import json
import os
import signal
import subprocess
import sys
import time

# seconds between looks at the processes
POLL = 0.5
# the fewest seconds between the counts told
EVERY = 1.0


def news(key, total, done, finished=False):
    """How many of the total model files of the matchup key (see
    oceanval.live.matchup_key) have been read, done, as the oceanval window is
    told it: None while it is not known."""
    return {"kind": "extraction", "key": key, "total": total, "done": done, "finished": finished}


@contextlib.contextmanager
def watching(paths, key):
    """While the block reads paths, the model files of the matchup key, tell
    the window how many have been read, from a process of its own. Nothing is
    watched unless the window is told the news by lines on stdout (see
    oceanval.prompts.news_marker)."""
    from oceanval import prompts

    marker = prompts.news_marker()
    watcher = _start(marker, key, paths) if marker is not None and len(paths) > 0 else None
    try:
        yield
    finally:
        if watcher is not None:
            _stop(watcher)


def _start(marker, key, paths):
    # what was printed before comes before its counts
    sys.stdout.flush()
    sys.stderr.flush()
    request = json.dumps({"parent": os.getpid(), "marker": marker, "key": key, "paths": list(paths)})
    try:
        watcher = subprocess.Popen(
            [sys.executable, "-I", os.path.abspath(__file__)],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        with watcher.stdin:
            watcher.stdin.write(request.encode("utf-8"))
    except OSError:
        # it has stopped already, so nothing is counted
        pass
    return watcher


def _stop(watcher):
    """Stop the watcher and wait for it, so no count of its own comes after
    what the matchup says next."""
    with contextlib.suppress(OSError):
        watcher.terminate()
    try:
        watcher.wait(timeout=5)
    except subprocess.TimeoutExpired:
        watcher.kill()
        watcher.wait()


def _names(paths):
    """Each file's number, by each name CDO could be given it by."""
    names = {}
    for number, path in enumerate(paths):
        for name in (path, os.path.normpath(path), os.path.abspath(path), os.path.realpath(path)):
            names.setdefault(name, number)
    return names


def _reading(matchup, names, psutil):
    """The numbers of the files named in the arguments of the processes
    descended from matchup, which are CDO's reading them, or None if they
    cannot be looked at."""
    try:
        processes = matchup.children(recursive=True)
    except psutil.Error:
        return None
    reading = set()
    for process in processes:
        try:
            arguments = process.cmdline()
        except psutil.Error:
            # ended since, or not ours to look at
            continue
        for argument in arguments:
            number = names.get(argument)
            if number is None and os.sep in argument:
                number = names.get(os.path.normpath(argument))
            if number is not None:
                reading.add(number)
    return reading


def _say(marker, told):
    # one write of one line, which nothing else written can split
    os.write(1, (marker + json.dumps(told) + "\n").encode("utf-8"))


def main():
    # stopped between lines, never half way through one
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    request = json.load(sys.stdin)
    marker, key, paths = request["marker"], request["key"], request["paths"]
    # the matchup's, as it said: one gone before this got here is not looked
    # for in its place
    parent = request["parent"]
    try:
        import psutil

        matchup = psutil.Process(parent)
    except Exception:
        # not counted: the window says only that the files are being read
        return
    names = _names(paths)
    seen = set()
    done = 0
    told = None
    last = 0.0
    # until the matchup has gone, if it is not stopped first
    while os.getppid() == parent:
        reading = _reading(matchup, names, psutil)
        if reading is not None:
            seen |= reading
            # a file read again (by a second CDO run) is not taken off
            done = max(done, len(seen - reading))
            if done != told and (told is None or time.monotonic() - last >= EVERY):
                _say(marker, news(key, len(paths), done))
                told, last = done, time.monotonic()
        time.sleep(POLL)


if __name__ == "__main__":
    main()
