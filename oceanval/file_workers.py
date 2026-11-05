"""How many workers read a simulation's files, and the reads that can be shared out.

Reading what is in a simulation's files (their times, years, sizes, whether
``cdo sinfo`` runs on them) is mostly waiting for shared disks, so a few reads
at a time are much faster than one at a time. Every such read uses
:func:`workers`, so they agree: at most 6, and no more than half the cores this
process may use (and at least 1), so the rest of the machine stays free.

This module imports nothing from OceanVal. The scripts that are run by path
(``output_years``, ``viewer_times``) cannot import it, so they are told the
count by the process that starts them.
"""

import concurrent.futures
import os

MOST_WORKERS = 6


def cores():
    """The cores this process may use, which respects the limits a job
    scheduler sets (macOS and Windows have no sched_getaffinity)."""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


def workers():
    """min(6, half the cores), and at least 1."""
    return max(1, min(MOST_WORKERS, cores() // 2))


def stat_all(paths, workers=None, stopped=None):
    """{path: os.stat_result} of each of paths that can be looked at, in the
    order given. The files are looked at by several threads (os.stat waits for
    the disk without holding the GIL), which is much faster than one at a time
    on a network disk. stopped(), if given, is asked as the files are looked at
    and ends the search early, leaving out the files not yet looked at."""
    paths = list(dict.fromkeys(os.fspath(path) for path in paths))
    count = min(globals()["workers"]() if workers is None else max(1, int(workers)), len(paths))

    def one(path):
        if stopped is not None and stopped():
            return None
        try:
            return os.stat(path)
        except OSError:
            return None

    if count <= 1:
        found = [one(path) for path in paths]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
            # big chunks: a stat is far smaller than the cost of handing it to a thread
            found = list(pool.map(one, paths, chunksize=max(1, len(paths) // (4 * count))))
    return {path: stat for path, stat in zip(paths, found) if stat is not None}
