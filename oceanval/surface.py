"""Reading the surface of a model's output quickly (analyze, compare_simulations and matchup).

Model files can be many GB with a great many variables, written one time step
of every level at a time in a chunk (22.7 MB for 51 levels of a 375 by 297
grid). CDO's selname, seltimestep, sellevidx and topvalue read every record and
drop what is not wanted; its select filters the records before reading them.
And with HDF5's default chunk cache, which is larger than such a chunk, reading
one level reads the whole chunk. So the surface, which is what every analysis
and most matchups want, is read by

- one select of the variables, the first level and the time steps wanted,
- a setzaxis to a surface axis, which is what topvalue leaves (no vertical
  dimension of one level), and
- CDO's own cache size, set to less than a chunk while it runs: CDI_CHUNK_CACHE
  before CDO 2.6, CDI_CACHESIZE_IN from 2.6 on (each ignores the other's name),
  if small_cache_helps.

That is the same as topvalue only if the first level is the surface wherever
there is a value, which first_level_is_surface checks of the first file: its
vertical axis is positive down, and on the first time step the first level is
missing exactly where topvalue's is. Anything else keeps topvalue.

The small cache is only for chunks that are not compressed, with a CDO that
says it takes the cache size (2.4.1 and before cannot say so). A compressed
chunk has to be read whole, so a small cache would make each level decompress
it again; with HDF5's own cache the select still decompresses each chunk once,
as topvalue does, and spares CDO the other levels (a quarter of the time of a
month of daily NEMO-ERSEM output, compressed, with its 51 levels in a chunk).
"""

import contextlib
import importlib.resources
import os
import shutil
import subprocess
import tempfile
import warnings

from oceanval.quiet_nc import nc
import numpy as np
import xarray as xr

# the bytes of HDF5 chunk cache for each variable while a surface is read from the
# first level only: smaller than the chunk of a model's output
CHUNK_CACHE = 1000000
# the variables CDO takes it from (before 2.6, and from 2.6 on), and those that
# make it say what it was given
CACHE_VARIABLES = ("CDI_CHUNK_CACHE", "CDI_CACHESIZE_IN")
CACHE_INFO = ("CDI_CHUNK_CACHE_INFO", "CDI_CACHE_INFO")
# the most time steps one select is given
MOST_TIMESTEPS = 10000
# a description of a surface vertical axis, which is what topvalue gives
SURFACE_AXIS = "surface_zaxis.txt"

# what first_level_is_surface found, by file, its modification time and the variables
_found = {}


def axis_file():
    """The path of the description of a surface vertical axis. nctoolkit runs
    its commands in a shell and rewrites "cdo " in them, so a path with a space
    in it (or "cdo") is copied to one without."""
    path = str(importlib.resources.files("oceanval").joinpath(f"data/{SURFACE_AXIS}"))
    if " " not in path and "cdo" not in path:
        return path
    safe = os.path.join(tempfile.gettempdir(), f"oceanval_{os.getuid()}_{SURFACE_AXIS}")
    shutil.copyfile(path, safe)
    return safe


def choose(ds, names, fast, timesteps=None):
    """Choose in ds the variables names and, if timesteps (0-based indices) is
    given, those time steps only. If fast, only the first level too, as the
    surface (see the module). The one select reads only what it chooses;
    selname and seltimestep would read everything and then drop it."""
    choice = f"-select,name={','.join(names)}"
    if fast:
        choice += ",levidx=1"
    # the command is one argument of a shell, which Linux limits to 128 KB: more than this many
    # steps are chosen after the select, as before
    too_many = timesteps is not None and len(timesteps) > MOST_TIMESTEPS
    if timesteps is not None and not too_many:
        choice += ",timestep=" + ",".join(str(int(step) + 1) for step in timesteps)
    if not fast and timesteps is None:
        ds.subset(variables=list(names))
        return
    ds.cdo_command(choice)
    if fast:
        ds.cdo_command(f"-setzaxis,{axis_file()}")
    if too_many:
        ds.subset(time=[int(step) for step in timesteps])


@contextlib.contextmanager
def chunk_cache(active=True):
    """Give CDO a small HDF5 chunk cache while it runs, if active, and restore
    the environment after. nctoolkit's CDO processes, in its pool too, are
    started with the environment as it is when they are."""
    if not active:
        yield
        return
    before = {name: os.environ.get(name) for name in CACHE_VARIABLES}
    os.environ.update({name: str(CHUNK_CACHE) for name in CACHE_VARIABLES})
    try:
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def cdo_takes_a_chunk_cache(path):
    """Whether the CDO that nctoolkit runs takes the cache size (CDI_CHUNK_CACHE,
    or CDI_CACHESIZE_IN from 2.6 on), which it says when asked, for a file with
    chunks. A file with none (contiguous, or netCDF3) has no chunk to read whole,
    so there is nothing for the cache to do."""
    import netCDF4 as nc4

    environment = dict(os.environ)
    environment.update({name: str(CHUNK_CACHE) for name in CACHE_VARIABLES})
    environment.update({name: "1" for name in CACHE_INFO})
    try:
        out = subprocess.run(
            ["cdo", "-s", "showname", path], env=environment, capture_output=True, text=True, timeout=300
        )
        if f"chunkCacheSize={CHUNK_CACHE}" in out.stdout + out.stderr:
            return True
        with nc4.Dataset(path) as data:
            return not any(variable.chunking() not in ("contiguous", None) for variable in data.variables.values())
    except Exception:
        return False


def _kept(kind, path, names, work_out):
    """work_out()'s answer for path and names, kept for the file as it is."""
    try:
        key = (kind, os.path.abspath(path), os.stat(path).st_mtime_ns, tuple(names))
    except OSError:
        key = None
    if key is not None and key in _found:
        return _found[key]
    answer = work_out()
    if key is not None:
        _found[key] = answer
    return answer


def first_level_is_surface(path, names, nlevels):
    """Whether the surface of the variables names can be read from the first
    vertical level of the files like path, and a reason if not: (True, "") or
    (False, why). nlevels is {variable: its number of levels}; a variable
    with one is not looked at. Only path's attributes and its first time step
    are read. The answer is kept for the file as it is."""
    names = list(names)
    return _kept("first level", path, names, lambda: _first_level_is_surface(path, names, nlevels))


def compressed(path, names, nlevels):
    """The first of the variables names with levels that is compressed in path,
    and how, as "name (zlib)"; None if none is."""
    import netCDF4 as nc4

    with nc4.Dataset(path) as data:
        for name in names:
            if nlevels.get(name, 1) < 2:
                continue
            filters = {key: value for key, value in (data.variables[name].filters() or {}).items() if value}
            if filters:
                return f"{name} ({', '.join(filters)})"
    return None


def small_cache_helps(path, names, nlevels):
    """Whether CDO should be given a small chunk cache (see chunk_cache) while the
    surface of the variables names is read from the first level of the files
    like path: only if none of them is compressed and the CDO takes the size.
    The answer is kept for the file as it is."""
    names = list(names)

    def work_out():
        try:
            return compressed(path, names, nlevels) is None and cdo_takes_a_chunk_cache(path)
        except Exception:
            return False

    return _kept("small cache", path, names, work_out)


def how_read(fast, why, small_cache):
    """What is said of how a variable's surface is read."""
    if not fast:
        return f"all levels are read to find the surface, as {why}"
    if small_cache:
        return "the surface is read from the first level"
    return "the surface is read from the first level, with HDF5's own chunk cache"


def _first_level_is_surface(path, names, nlevels):
    import netCDF4 as nc4

    try:
        with nc4.Dataset(path) as data:
            for name in names:
                if nlevels.get(name, 1) < 2:
                    continue
                variable = data.variables[name]
                z = [dim for dim in variable.dimensions if dim in data.variables and hasattr(data.variables[dim], "positive")]
                if len(z) != 1:
                    return False, f"the vertical axis of {name} cannot be told"
                if data.variables[z[0]].positive != "down":
                    return False, f"the first level of {name} is not at the surface ({z[0]} is positive {data.variables[z[0]].positive})"
        one = nc.open_data(path, checks=False)
        choose(one, names, True, [0])
        top = nc.open_data(path, checks=False)
        top.cdo_command(f"-select,name={','.join(names)},timestep=1")
        top.cdo_command("topvalue")
        with chunk_cache(small_cache_helps(path, names, nlevels)), warnings.catch_warnings(record=True):
            one.run()
            top.run()
        with xr.open_dataset(one[0]) as first, xr.open_dataset(top[0]) as value:
            for name in names:
                if not np.array_equal(np.isnan(first[name].values), np.isnan(value[name].values)):
                    return False, f"{name} is missing at the first level where there is a value below it"
    except Exception as error:
        return False, f"it could not be told ({type(error).__name__})"
    return True, ""
