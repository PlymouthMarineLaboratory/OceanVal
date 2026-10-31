"""The temporary files earlier OceanVal sessions left behind, which the
oceanval window offers to remove when it opens."""

import glob
import os
import re

from oceanval.quiet_nc import nc

# where nctoolkit (and so cdo and nco) writes its temporary files
TEMP_DIRS = ("/tmp/", "/var/tmp/", "/usr/tmp/")

# what OceanVal adds to nctoolkit's stamp, which every temporary file's name
# contains, and the process that made them, so a file can be told from one of
# a session still running
STAMP = f"_oceanval_output_p{os.getpid()}_"
# the name used before the rename, still in the files older versions left
_PROCESS = re.compile(r"_oceanval_output_p(\d+)_")
# the whole name of a temporary file OceanVal made: nctoolkit's stamp (its
# name, the user's and a random string), then OceanVal's, the process that made
# it for newer files, and nctoolkit's own tmp name and .nc
_TEMP_NAME = re.compile(r"^nctoolkit_.+nctoolkit_(?:oceanval|ecoval)_output_.*tmp.*\.nc$")


def is_oceanval_temp(path):
    """Whether a path is named like a temporary file OceanVal made, in full:
    a file of someone's own that merely has the words in its name is not.
    Only the file's own name counts, not the directories above it."""
    return bool(_TEMP_NAME.match(os.path.basename(path)))


def process_alive(pid):
    """Whether a process is running."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # it exists, and belongs to someone else
        return True
    except OSError:
        return False
    return True


def in_use(path):
    """Whether the session that made a file is still running. Files from
    older versions do not say which process made them, so are never in use."""
    found = _PROCESS.search(os.path.basename(path))
    return bool(found) and process_alive(int(found.group(1)))


def _directories():
    session_dir = nc.session_info.get("temp_dir")
    directories = list(TEMP_DIRS)
    if session_dir and session_dir not in directories:
        directories.append(session_dir)
    return directories


def find_leftovers():
    """The user's own files left behind by earlier sessions: a list of dicts
    with path, size and mtime, oldest first.

    Other users' files are skipped, as a shared temp directory would not
    let them be removed, and so are those of sessions still running, this
    one included."""
    stamp = nc.session_info.get("stamp")
    found = []
    for directory in _directories():
        for path in glob.glob(os.path.join(directory, "*")):
            if not is_oceanval_temp(path) or (stamp and stamp in path) or in_use(path):
                continue
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            if info.st_uid != os.getuid():
                continue
            found.append({"path": path, "size": info.st_size, "mtime": info.st_mtime})
    return sorted(found, key=lambda item: (item["mtime"], item["path"]))


def remove_leftovers(paths):
    """Remove the given files, but only those a fresh scan still finds, so a
    request can never remove anything else. Returns how many were removed,
    the bytes freed and the paths that could not be removed."""
    wanted = set(paths)
    removed = 0
    freed = 0
    failed = []
    for item in find_leftovers():
        if item["path"] not in wanted:
            continue
        # never remove a file that is not named like one OceanVal made
        if not is_oceanval_temp(item["path"]):
            failed.append(item["path"])
            continue
        try:
            os.remove(item["path"])
        except OSError:
            failed.append(item["path"])
            continue
        removed += 1
        freed += item["size"]
    return {"removed": removed, "freed_bytes": freed, "failed": failed}
