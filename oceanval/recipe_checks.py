"""Looking at the data a recipe of your own is for.

The oceanval window uses this to check the data as a recipe is registered,
opening it as matchup will: gridded data on disk with nctoolkit's open_data,
on a THREDDS server with open_thredds, and from a web address by downloading
it with open_url. Point data is a csv file or a directory of csv files, read as
add_point_comparison reads it.

Data on a server is looked at in a process of its own (see RemoteCheck), as
nothing in nctoolkit stops a server that does not answer from holding it up
for ever, and that process can be stopped.
"""

import contextlib
import glob
import gc
import json
import os
import re
import signal
import subprocess
import sys
import threading

from oceanval.quiet_nc import nc

from oceanval.parsers import point_files, read_point

LOCATIONS = ("disk", "thredds", "url")

# the columns of csv files of point data, as add_point_comparison reads them
POINT_COLUMNS = ("lon", "lat", "year", "month", "day", "depth", "observation", "source")
POINT_REQUIRED = ("lon", "lat", "observation")

# how long data on a server is given to answer, in seconds
TIMEOUTS = {"thredds": 120, "url": 300}

RESULT_MARKER = "OCEANVAL-RECIPE-CHECK "


class CheckFailed(Exception):
    """The data cannot be used: the message says why, for the user."""


def _short(error):
    text = " ".join(str(error).split()) or type(error).__name__
    return text if len(text) < 400 else text[:397] + "..."


def resolve(text, cwd=None):
    """A path typed into the window, as a full path: relative to cwd, with ~
    for the home directory."""
    return os.path.normpath(
        os.path.join(os.path.abspath(cwd or os.getcwd()), os.path.expanduser(text.strip()))
    )


def addresses(text):
    """The web addresses in a box, one to a line (or separated by spaces or
    commas)."""
    return [part for part in re.split(r"[\s,]+", str(text).strip()) if part]


def _contents(ds):
    """The variables in a dataset: name, long name, units and number of levels."""
    contents = ds.contents
    return [
        {
            "name": str(row.variable),
            "long_name": "" if row.long_name is None else str(row.long_name),
            "units": None if row.unit is None or str(row.unit) == "" else str(row.unit),
            "nlevels": int(row.nlevels) if row.nlevels is not None else 1,
        }
        for row in contents.itertuples()
    ]


def _years(ds):
    try:
        years = [int(year) for year in ds.years]
    except Exception:
        return None
    return [min(years), max(years)] if years else None


def _look(first, last, opener):
    """What is in the data, from its first and last files, opened with
    opener. Returns (variables, years)."""
    ds = opener(first)
    variables = _contents(ds)
    if not variables:
        raise CheckFailed("The file opened, but it holds no variables.")
    years = _years(ds)
    if last != first:
        other = _years(opener(last))
        if years is None:
            years = other
        elif other is not None:
            years = [min(years[0], other[0]), max(years[1], other[1])]
    return variables, years


def _disk(text, cwd):
    path = resolve(text, cwd)
    if glob.has_magic(path) and not os.path.exists(path):
        # matchup opens a pattern as it is, which only works for netCDF files
        if not path.endswith(".nc"):
            raise CheckFailed("A pattern has to end in .nc, as in obs/chl_*.nc.")
        files = sorted(glob.glob(path))
        if not files:
            raise CheckFailed("No files match this pattern.")
    elif os.path.isdir(path):
        try:
            files = sorted(nc.create_ensemble(path))
        except ValueError:
            raise CheckFailed("There are no netCDF files in this directory.")
    elif os.path.isfile(path):
        if not path.endswith(".nc"):
            raise CheckFailed("The file has to end in .nc.")
        files = [path]
    else:
        raise CheckFailed("There is no file or directory at this path.")
    return path, files


def inspect_gridded(location, obs_path, cwd=None):
    """Open gridded observations and say what is in them.

    location is "disk" (obs_path a netCDF file, a directory of them, or a
    pattern ending in .nc), "thredds" (obs_path a list of OPeNDAP addresses,
    or one) or "url" (one address of a file to download). Raises CheckFailed
    if they cannot be used. Otherwise returns {"obs_path" (how it should be
    stored), "files", "sample" (the file looked at), "variables" (each with
    "name", "long_name", "units" and "nlevels") and "years" ([first, last],
    or None if there is no time axis)}.
    """
    if location not in LOCATIONS:
        raise ValueError(f"location must be one of {LOCATIONS}")
    try:
        if location == "disk":
            stored, files = _disk(obs_path, cwd)
            opener = lambda path: nc.open_data(path, checks=False)
        else:
            urls = obs_path if isinstance(obs_path, list) else addresses(obs_path)
            check_addresses(location, urls)
            stored = urls if location == "thredds" else urls[0]
            files = urls
            if location == "thredds":
                opener = lambda url: nc.open_thredds(url, checks=False)
            else:
                opener = lambda url: nc.open_url(url)
        try:
            variables, years = _look(files[0], files[-1], opener)
        except CheckFailed:
            raise
        except Exception as error:
            if location == "disk":
                raise CheckFailed(f"The file could not be read: {_short(error)}")
            raise CheckFailed(_why(location, files[0], error))
    finally:
        # nctoolkit keeps files open until its datasets are collected
        gc.collect()
    return {
        "obs_path": stored,
        "files": len(files),
        "sample": files[0],
        "variables": variables,
        "years": years,
    }


def _why(location, url, error):
    """Why data on a server could not be read, as well as can be told: what
    nctoolkit says is often only that the contents could not be parsed, so
    the server is asked what it makes of the address."""
    import requests

    # an OPeNDAP server describes any of its files at its address plus .dds
    ask = url + ".dds" if location == "thredds" else url
    try:
        reply = requests.get(ask, timeout=20, stream=True)
        status = reply.status_code
        reply.close()
    except requests.exceptions.Timeout:
        return f"The server at {url} did not answer."
    except requests.exceptions.RequestException as problem:
        text = str(problem)
        for pattern, reason in (
            ("Name or service not known|nodename nor servname|Temporary failure in name", "its name was not found"),
            ("Connection refused", "the connection was refused"),
            ("timed out", "it timed out"),
            ("CERTIFICATE|SSL", "its security certificate was not accepted"),
        ):
            if re.search(pattern, text):
                return f"The server at {url} could not be reached: {reason}."
        return f"The server at {url} could not be reached: {_short(problem)}"
    if status == 404:
        return f"The server answered that there is nothing at {url} (404)."
    if status >= 400:
        return f"The server refused {url} with the answer {status}."
    return f"The data at {url} could not be read: {_short(error)}"


def check_addresses(location, urls):
    if not urls:
        raise CheckFailed("Give the web address of the data.")
    if location == "url" and len(urls) > 1:
        raise CheckFailed("A web address to download is one file: give just one.")
    for url in urls:
        if not url.lower().startswith(("http://", "https://", "ftp://")):
            raise CheckFailed(f"{url} is not a web address: it should begin with https://.")
        lowered = url.lower()
        if "/catalog" in lowered or lowered.endswith((".html", ".xml")):
            raise CheckFailed(
                f"{url} is a catalogue page, not a file. Use the OPeNDAP address of the "
                "file, which has dodsC in it."
            )
        if location == "thredds" and "/filese" in lowered:
            raise CheckFailed(
                f"{url} is a download link. Use the OPeNDAP address of the file, which has "
                "dodsC in it, or choose a web address to download instead."
            )
        if location == "url" and "/dodsc/" in lowered:
            raise CheckFailed(
                f"{url} is an OPeNDAP address: choose THREDDS for it, as it is read over the "
                "server rather than downloaded."
            )
        if not lowered.endswith(".nc"):
            raise CheckFailed(f"{url} has to end in .nc.")


def inspect_point(obs_path, cwd=None):
    """Read point observations: a csv file or a directory of csv files. Raises CheckFailed
    if they cannot be used. Otherwise returns {"obs_path", "files",
    "columns" (those found), "depth" (whether there is a depth column),
    "years" and the "lon" and "lat" ranges, from the first file, or None}."""
    path = resolve(obs_path, cwd)
    if not os.path.exists(path):
        raise CheckFailed("There is no file or directory at this path.")
    files = point_files(path)
    if not files:
        raise CheckFailed("There are no csv files in this directory.")
    columns = []
    depth = False
    for file in files:
        try:
            frame = read_point(file, nrows=1)
        except Exception as error:
            raise CheckFailed(f"{os.path.basename(file)} could not be read: {_short(error)}")
        bad = [column for column in frame.columns if column not in POINT_COLUMNS]
        if bad:
            raise CheckFailed(
                f"{os.path.basename(file)} has columns OceanVal does not use: {', '.join(bad)}. "
                f"Only {', '.join(POINT_COLUMNS)} are allowed."
            )
        missing = [column for column in POINT_REQUIRED if column not in frame.columns]
        if missing:
            raise CheckFailed(
                f"{os.path.basename(file)} has no {', '.join(missing)} column"
                f"{'s' if len(missing) > 1 else ''}."
            )
        depth = depth or "depth" in frame.columns
        columns = list(dict.fromkeys(columns + list(frame.columns)))
    found = {"years": None, "lon": None, "lat": None}
    try:
        frame = read_point(files[0], nrows=200000)
        if "year" in frame.columns and len(frame):
            found["years"] = [int(frame.year.min()), int(frame.year.max())]
        # as matchup reads them, longitudes of 0 to 360 are -180 to 180
        frame = frame.assign(lon=frame.lon.where(frame.lon <= 180, frame.lon - 360))
        for name in ("lon", "lat"):
            found[name] = [round(float(frame[name].min()), 3), round(float(frame[name].max()), 3)]
    except Exception:
        pass
    return dict(obs_path=path, files=len(files), columns=columns, depth=depth, **found)


# ---- data on a server, looked at in a process of its own ----


def main():
    """What a RemoteCheck's process runs: read {"location", "obs_path"} from
    standard input, and print what inspect_gridded found."""
    request = json.loads(sys.stdin.read())
    try:
        reply = {"ok": True, "found": inspect_gridded(request["location"], request["obs_path"])}
    except CheckFailed as error:
        reply = {"ok": False, "error": str(error)}
    except Exception as error:
        reply = {"ok": False, "error": _short(error)}
    print(RESULT_MARKER + json.dumps(reply), flush=True)


class RemoteCheck:
    """Look at gridded data on a server (location "thredds" or "url") in a
    process of its own, which is stopped if it takes longer than timeout
    seconds, or when cancel is called. done(reply) is called from the thread
    that waits for it, with {"ok": True, "found": ...} as inspect_gridded
    returns, or {"ok": False, "error": ...}."""

    def __init__(self, location, obs_path, done, timeout=None, cwd=None):
        self.location = location
        self.obs_path = obs_path
        self.done = done
        self.timeout = timeout or TIMEOUTS[location]
        self.cwd = cwd
        self.process = None
        self.cancelled = False
        self.reply = None

    def start(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = (
            f"import sys; sys.path.insert(0, {root!r}); "
            "from oceanval.recipe_checks import main; main()"
        )
        threading.Thread(target=self._run, args=(code,), name="oceanval-recipe-check", daemon=True).start()

    def _run(self, code):
        reply = None
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-u", "-c", code],
                cwd=self.cwd,
                env=dict(os.environ, PYTHONUNBUFFERED="1"),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
            request = json.dumps({"location": self.location, "obs_path": self.obs_path})
            try:
                output, _ = self.process.communicate(request, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self._stop()
                # the pipes close once it has gone
                self.process.communicate()
                reply = {
                    "ok": False,
                    "error": f"The server did not answer in {self.timeout} seconds, so the "
                    "data could not be checked.",
                }
            else:
                for line in reversed(output.splitlines()):
                    if line.startswith(RESULT_MARKER):
                        reply = json.loads(line[len(RESULT_MARKER):])
                        break
                else:
                    reply = {
                        "ok": False,
                        "error": "The data could not be checked: "
                        + (_short(output.strip().splitlines()[-1]) if output.strip() else "no reply."),
                    }
        except Exception as error:
            reply = {"ok": False, "error": _short(error)}
        if self.cancelled:
            return
        self.reply = reply
        self.done(reply)

    def _stop(self):
        """Stop the process, and anything it started."""
        process = self.process
        if process is not None and process.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)

    def cancel(self):
        self.cancelled = True
        self._stop()
