import contextlib
import copy
import functools
import gc
import json
import queue
import sys
import threading
import time
from oceanval.quiet_nc import nc
import re
import importlib
import glob
import subprocess
import platform
if platform.system() == "Linux":
    import multiprocessing as mp
    from multiprocessing.managers import SyncManager
else:
    import multiprocess as mp
    from multiprocess.managers import SyncManager
import pathlib
import os
import pandas as pd
import string
import random
import warnings
import pickle
import shutil
import xarray as xr
import oceanval.parsers as parsers
from oceanval.session import session_info
from oceanval.parsers import Validator, definitions, point_files
from tqdm import tqdm
from oceanval.utils import extension_of_directory
from oceanval import cdo_check
from oceanval import domain_mask as domain_mask_mod
from oceanval.parsers import generate_mapping
from oceanval.gridded import gridded_matchup, retry_failed_gridded, _lonlat_bounds
from oceanval.fvcom import fvcom_matchup_files, fvcom_extent, fvcom_times
from oceanval.fvcom import check_matchup_settings as check_fvcom_settings
from oceanval.fvcom import default_res as fvcom_default_res
from oceanval.fvcom import default_min_depth as fvcom_default_min_depth
from oceanval.fvcom import default_max_depth as fvcom_default_max_depth
from oceanval.fvcom import default_dz as fvcom_default_dz
from oceanval import data_store as obs_store
from oceanval import cdo_watch
from oceanval import extent
from oceanval import ices
from oceanval import live
from oceanval import surface
from oceanval import model_check
from oceanval import prompts
from oceanval import selection
from oceanval import server_trouble
from oceanval import time_res
from oceanval import time_stamps
from oceanval import user_cache


def read_point(ff, nrows = None):
    try:
        df = pd.read_csv(ff, nrows=nrows)   
        return df
    except:
        pass 
    compressions = ['infer', 'gzip', 'bz2', 'zip', 'xz']
    for compression in compressions: 
        try:
            df = pd.read_csv(ff, compression = compression, nrows=nrows)   
            return df
        except:
            pass
    raise ValueError(f"Could not read file {ff} with any of the following compression types: {compressions}. Error: {e}")



def _axis_name(da, axis):
    """The name of da's longitude, latitude or time coordinate, or None if it has none"""
    try:
        for x in da.metpy.coordinates(axis):
            return x.name
    except AttributeError:
        return None


def is_z_up(ff, variable=None):
    """
    Whether the first vertical level of variable (the file's first variable
    if None) is at the bottom, as its vertical coordinate's positive
    attribute says: True for "up", False for "down". The variable can have
    a time dimension or not, as a static cell thickness has none.
    """
    import netCDF4 as nc4

    try:
        ds1 = nc.open_data(ff, checks=False)
        if variable is None:
            var = ds1.variables[0]
        else:
            var = variable
        # select variable using nco
        ds1.nco_command(f"ncks -v {var}")
        ds1.run()
        ds = xr.open_dataset(ds1[0])
        horizontal = [_axis_name(ds[var], axis) for axis in ["longitude", "latitude", "time"]]
        coords = [x for x in list(ds.coords) if x not in horizontal]
        coords = [x for x in coords if "time" not in x.lower()]
    except Exception:
        raise ValueError(
            f"Could not read the vertical axis of {variable or 'the first variable'} in {ff}."
        )

    if len(coords) == 0:
        raise ValueError(
            f"{var} in {ff} has no vertical coordinate, so OceanVal cannot tell whether "
            "its first level is the surface."
        )
    if len(coords) > 1:
        raise ValueError(
            f"{var} in {ff} has more than one possible vertical coordinate "
            f"({', '.join(coords)}), so OceanVal cannot tell whether its first level is the surface."
        )
    with nc4.Dataset(ff) as ds:
        z = ds.variables[coords[0]]
        if not hasattr(z, "positive"):
            raise ValueError(
                f"The vertical coordinate {coords[0]} of {var} in {ff} has no positive "
                "attribute, so OceanVal cannot tell whether its first level is the surface. "
                'Add one: "down" if depths increase downwards, as in NEMO, e.g. '
                f"ncatted -a positive,{coords[0]},c,c,down {ff}"
            )
        return z.positive != "down"


def _levels_are_heights(ff, variable):
    """
    Whether the vertical levels of variable in ff are heights, i.e. its
    vertical coordinate is positive up or has negative values, not the
    depths, positive down, that matching on z-levels needs. Levels whose
    direction cannot be read are taken as depths.
    """
    with warnings.catch_warnings(record=True):
        ds = nc.open_data(ff, checks=False)
        ds.subset(variables=variable)
        levels = ds.levels
    if len(levels) < 2:
        return False
    if min(levels) < 0:
        return True
    try:
        return is_z_up(ff, variable)
    except ValueError:
        return False


def _thickness_from_file(path):
    """
    The name of the thickness variable in a thickness file: its only
    variable, or the one with cell_thickness in its name.
    """
    import netCDF4 as nc4

    variables = nc.open_data(path, checks=False).variables
    # CDO, which OceanVal uses, leaves out a 4D variable whose first
    # dimension is not a time axis, as e3t_0(t, z, y, x) in NEMO mesh files
    with nc4.Dataset(path) as ds:
        skipped = [name for name, x in ds.variables.items() if x.ndim == 4 and name not in variables]
    if len(skipped) > 0:
        raise ValueError(
            f"{', '.join(skipped)} in {path} cannot be read by CDO, which OceanVal uses: it is "
            "4D and its first dimension is not a time axis, as e3t_0(t, z, y, x) is in NEMO "
            "mesh files. Give a file with only the thickness in it, without that dimension, "
            f"e.g. made with: ncks -v {skipped[0]} {path} thickness_4d.nc && ncwa -a t thickness_4d.nc thickness.nc"
        )
    if len(variables) == 1:
        return variables[0]
    named = [x for x in variables if "cell_thickness" in x]
    if len(named) == 1:
        return named[0]
    raise ValueError(
        f"The thickness file {path} has more than one variable ({', '.join(variables)}), so "
        "OceanVal cannot tell which is the cell thickness. Give a file with only the thickness "
        f"in it, e.g. made with: ncks -v e3t {path} thickness.nc"
    )


def _thickness_and_depths(ds_selected, as_missing, invert):
    """The model's cell thickness, from ds_selected, its selected variable,
    with as_missing set missing, as (bathymetry, thickness, depths): the
    thickness as it is in the files, for the bathymetry, the thickness with
    the sea surface at the top (inverted, if invert), and the depth of the
    middle of each cell. ds_selected is not changed."""
    ds_thickness = ds_selected.copy()
    if as_missing is not None:
        ds_thickness.as_missing(as_missing)
    ds_thickness.run()
    bathymetry = ds_thickness.copy()
    if invert:
        ds_thickness.invert_levels()
        ds_thickness.run()
    ds_thickness_sim = ds_thickness.copy()

    ds_depths = ds_thickness.copy()
    ds_depths.vertical_cumsum()
    ds_thickness / 2
    ds_depths - ds_thickness
    ds_depths.run()
    ds_depths.rename({ds_depths.variables[0]: "depth"})
    ds_depths.run()
    return bathymetry, ds_thickness_sim, ds_depths


def _thickness_levels(ds_thickness, thickness, label):
    """
    The selection of thickness from ds_thickness, as long as it has more
    than one level: an empty selection would leave CDO waiting for ever
    """
    contents = ds_thickness.contents
    var_sel = contents.loc[(contents.variable == thickness) & (contents.nlevels > 1)].variable
    if len(var_sel) == 0:
        raise ValueError(
            f"The thickness, {label}, has only one vertical level as CDO reads it, so it "
            "cannot be the cell thickness."
        )
    return list(var_sel)


def _thickness_file(paths, thickness):
    """
    The first of paths with a variable named exactly thickness. Only the
    exact name will do: model output can have other variables with it in
    their names, such as ERSEM's depth integrated trends (O3_c_e3t).
    """
    similar = set()
    for ff in paths:
        print("Checking file for thickness: " + ff)
        # do this quietly
        with warnings.catch_warnings(record=True):
            variables = nc.open_data(ff, checks=False).variables
        if thickness in variables:
            return ff
        similar.update(x for x in variables if thickness in x)
    message = f"No variable named {thickness} was found in the simulation output."
    if len(similar) > 0:
        message += f" Variables with {thickness} in their names: {', '.join(sorted(similar))}."
    message += " Give the exact name of the cell thickness variable as thickness, or a thickness file."
    raise ValueError(message)


def _check_water_column_variables(comparisons, all_df):
    """
    Stop if a comparison through the water column has a model variable with
    only one vertical level, such as a sea surface temperature: matched at
    depth it gives nothing, without saying why.

    comparisons is a list of (variable, source), all_df the mapping of each
    variable to its model variable and an example file.
    """
    for vv, source in comparisons:
        rows = all_df.loc[all_df.variable == vv].dropna(subset=["model_variable", "example_file"])
        if len(rows) == 0:
            continue
        name = rows.model_variable.iloc[0].split("+")[0]
        ff = rows.example_file.iloc[0]
        with warnings.catch_warnings(record=True):
            contents = nc.open_data(ff, checks=False).contents
        nlevels = contents.loc[contents.variable == name].nlevels
        if len(nlevels) > 0 and int(nlevels.iloc[0]) == 1:
            raise ValueError(
                f"{name} in {os.path.basename(ff)} has only one vertical level, so {vv} ({source}) "
                "cannot be matched through the water column. Give the model's 3D variable as "
                "model_variable, or set vertical=False."
            )


def _checked_cores(cores):
    """matchup's cores, checked: the default, 6, is cut to the system's."""
    if cores == 6:
        if cores > os.cpu_count():
            cores = os.cpu_count()
            print(
                f"Setting cores to {cores} as this is the number of cores available on your system"
            )
    # error for cores < 1
    if cores < 1:
        raise ValueError("cores must be a positive integer")
    return cores


def _checked_limits(lon_lim, lat_lim):
    """matchup's lon_lim and lat_lim, checked: both None, or both lists of
    two numbers."""
    if lon_lim is None and lat_lim is None:
        return None, None
    if lon_lim is None or lat_lim is None:
        raise TypeError("lon_lim and lat_lim must be lists")
    if len(lon_lim) != 2 or len(lat_lim) != 2:
        raise ValueError(
            "lon_lim and lat_lim must be lists of two floats, e.g. [-18, 9] and [42, 63]"
        )
    return list(lon_lim), list(lat_lim)


def _checked_missing(as_missing):
    """matchup's as_missing, checked: None, a number, or a list of them."""
    if as_missing is None:
        return None
    if isinstance(as_missing, list):
        for am in as_missing:
            if not isinstance(am, (int, float)):
                raise TypeError("as_missing list elements must be float or int")
    elif not isinstance(as_missing, (int, float)):
        raise TypeError("as_missing must be a float, int, list or None")
    return as_missing


def _model_box(path, model_variable):
    """The model's extent, ([lon_min, lon_max], [lat_min, lat_max]), from the
    cells of its first file that have values (see oceanval.extent): NEMO puts
    the cells of the land processors it leaves out at lon -1, lat -1, so the
    whole grid can be far larger than its domain. The whole grid if they can
    not be told."""
    name = re.split(r"[+,]", str(model_variable))[0].strip() or None
    found = extent.of_file(path, name, session_info.get("as_missing"))
    if found is None:
        lon_min, lon_max, lat_min, lat_max = _lonlat_bounds(nc.open_data(path, checks=False))
        return [lon_min, lon_max], [lat_min, lat_max]
    lons = ((found[0] + 180) % 360) - 180
    return [float(lons.min()), float(lons.max())], [float(found[1].min()), float(found[1].max())]


def _checked_overwrite(overwrite):
    """matchup's overwrite, checked: True, False, or a list of variables or
    "variable/source"."""
    if not isinstance(overwrite, bool):
        if not isinstance(overwrite, (list, tuple)):
            raise TypeError('overwrite must be a boolean, or a list of variables or "variable/source"')
        # none of them
        overwrite = selection.check_variables(overwrite, name="overwrite") if overwrite else False
    return overwrite


def _full_out_dir(out_dir):
    """matchup's out_dir, checked, as a full path."""
    if not isinstance(out_dir, str):
        raise TypeError("out_dir must be a string")
    return os.path.abspath(os.path.expanduser(out_dir))


def _saved_titles(out_dir):
    """The report's names of the variables matched up in out_dir before."""
    ff = out_dir + "oceanval_matchups/short_titles.pkl"
    if os.path.exists(ff):
        with open(ff, "rb") as f:
            return pickle.load(f)
    return dict()


def _remove_fvcom_dir(fvcom_dir):
    """
    Remove the regridded FVCOM files

    nctoolkit's contents leaves netCDF files open until garbage collection, and on NFS
    a directory cannot be removed while a file in it is open ("Device or resource busy").
    """
    if "fvcom_tmp" not in fvcom_dir:
        raise ValueError("Refusing to remove fvcom_dir because it does not contain 'fvcom_tmp'")
    error = None
    for attempt in range(3):
        if not os.path.exists(fvcom_dir):
            return
        gc.collect()
        try:
            shutil.rmtree(fvcom_dir)
            return
        except OSError as e:
            error = e
            time.sleep(1)
    warnings.warn(
        f"Unable to remove the temporary FVCOM files in {fvcom_dir} ({error}). They can be deleted manually."
    )


def _exit_with(parent):
    """Run in a Manager's server process: exit once parent, the process that
    started it, is gone. A crash or SIGKILL of the parent runs no atexit, so
    nothing else would stop the server, which would run for ever."""

    def watch():
        while os.getppid() == parent:
            time.sleep(1)
        os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def _manager():
    """A started multiprocessing Manager whose server exits with this process
    (see _exit_with). Every Manager is made with this, when it is needed,
    never at import."""
    manager = SyncManager()
    manager.start(initializer=_exit_with, initargs=(os.getpid(),))
    return manager


class _Told:
    """Tells the oceanval window of each matchup as it is started, made or
    not (see oceanval.prompts.tell), how many of its model files have been
    read and whether its observations have been, for its grid of them, and
    the interim report's builder, if there is one, of those made. At a
    terminal nothing is told."""

    def __init__(self, builder):
        self.builder = builder
        # those made or kept, as (kind, variable, source, layer), for the
        # record of the run (see selection.save_last_run)
        self.made_keys = []
        # when each matchup's count of files read was last told
        self.counted = {}

    def _clock(self):
        """The time the counts of files read are spaced by, in seconds."""
        return time.monotonic()

    def files(self, kind, variable, source, total, done, layer=None, finished=False):
        """Tell how many of the total model files of a matchup have been read,
        done (None while it is not known), at most every cdo_watch.EVERY
        seconds, but always the first count, of none, and the last, finished,
        when all have been. A matchup with no files to read has no count."""
        if total == 0:
            return
        key = live.matchup_key(kind, variable, source, layer)
        now = self._clock()
        if done and not finished and now - self.counted.get(key, float("-inf")) < cdo_watch.EVERY:
            return
        self.counted[key] = now
        prompts.tell(cdo_watch.news(key, total, done, finished))

    @contextlib.contextmanager
    def reading(self, kind, variable, source, paths, layer=None):
        """Tell how many of paths, a matchup's model files, have been read
        while the block reads them all at once in nctoolkit, which says
        nothing of them itself (see oceanval.cdo_watch), and that all have
        been once it is over."""
        self.files(kind, variable, source, len(paths), None, layer)
        with cdo_watch.watching(paths, live.matchup_key(kind, variable, source, layer)):
            yield
        self.files(kind, variable, source, len(paths), len(paths), layer, finished=True)

    @staticmethod
    def _observations(state, kind, variable, source, layer):
        key = live.matchup_key(kind, variable, source, layer)
        prompts.tell({"kind": "observations", "key": key, "state": state})

    def observing(self, kind, variable, source, layer=None):
        """Tell that a matchup's observations are being read or downloaded."""
        self._observations("reading", kind, variable, source, layer)

    def observed(self, kind, variable, source, layer=None):
        """Tell that a matchup's observations have been read."""
        self._observations("read", kind, variable, source, layer)

    @staticmethod
    def _tell(state, kind, variable, source, layer):
        key = live.matchup_key(kind, variable, source, layer)
        prompts.tell({"kind": "matchup", "key": key, "state": state})

    def matching(self, kind, variable, source, layer=None):
        self._tell("matching", kind, variable, source, layer)

    def made(self, kind, variable, source, layer=None):
        if (kind, variable, source, layer) not in self.made_keys:
            self.made_keys.append((kind, variable, source, layer))
        self._tell("made", kind, variable, source, layer)
        if self.builder is not None:
            self.builder.matched(kind, variable, source, layer)

    def not_made(self, kind, variable, source, layer=None):
        self._tell("not_made", kind, variable, source, layer)


def _matchups_news(items, titles, builder):
    """The matchups to make, as (kind, variable, source, layer) in the order
    they are made, as the oceanval window's grid shows them: each with
    whether the interim report has a page for it."""
    included = set()
    if builder is not None and builder.process is not None:
        included = {
            live.matchup_key(m["kind"], m["variable"], m["source"], m["layer"])
            for m in builder.expected
        }
    rows = []
    for kind, variable, source, layer in items:
        key = live.matchup_key(kind, variable, source, layer)
        name = source.upper()
        if kind == "point":
            name += " point data (surface)" if layer == "surface" else " point data (all depths)"
        rows.append(
            {
                "key": key,
                "variable": variable,
                "title": titles.get(variable) or variable.title(),
                "source": name,
                "report": key in included,
            }
        )
    return {"kind": "matchups", "items": rows}


# the warnings of a matchup and its workers (see _session_warnings)
session_warnings = None


def _session_warnings():
    """The list the warnings of a matchup and its workers are kept in, a
    Manager's, made the first time it is asked for, which is before a pool is
    forked, so the workers share it."""
    global session_warnings
    if session_warnings is None:
        session_warnings = _manager().list()
    return session_warnings

nc.options(progress=False)


# how far below the centre of the top cell an observation above it is
# matched, keeping it inside the model's depths despite rounding (the depths
# are float32), as nctoolkit does not extrapolate past them (max_extrap=0)
TOP_CELL_OFFSET = 1e-4


def _top_cell_depths(locs, ds_depths, path, model_variable):
    """
    The depth of the centre of the model's top cell at each lon/lat of locs,
    as a dataframe of lon, lat and top_depth, or as one number for output on
    z-levels (ds_depths is None), whose depths are the file's levels.
    Returns None if it cannot be worked out.
    """
    if ds_depths is None:
        ds = nc.open_data(path, checks=False)
        ds.subset(variables=model_variable.split("+"))
        levels = ds.levels
        if len(levels) < 2:
            return None
        return float(min(levels))
    ds = nc.open_data(ds_depths[0], checks=False)
    # the depths are surface first, so the top cell's is the smallest. CDO's
    # topvalue cannot be used: on an axis without levels it knows, such as
    # GETM's, it takes the last level, which is the bottom once inverted
    ds.vertical_min()
    try:
        top = ds.match_points(locs.loc[:, ["lon", "lat"]].drop_duplicates(), quiet=True)
    except ValueError:
        # no location is in the sea
        return None
    if top is None:
        return None
    return (
        top.loc[:, ["lon", "lat", "depth"]]
        .drop_duplicates(["lon", "lat"])
        .rename(columns={"depth": "top_depth"})
        .astype({"top_depth": "float64"})
    )


def _match_above_top_cell(df, top):
    """
    Observations shallower than the centre of the model's top cell, as at
    0 m, are in the top cell, but nctoolkit only interpolates between cell
    centres. So they are matched just below the top cell's centre. The depth
    observed is kept as obs_depth, to be put back once matched.
    """
    if isinstance(top, pd.DataFrame):
        top_depth = pd.Series(
            df.loc[:, ["lon", "lat"]].merge(top, how="left").top_depth.values,
            index=df.index,
        )
    else:
        top_depth = pd.Series(top, index=df.index)
    # a location with no top cell (land) is never above it
    above = df.depth < top_depth
    return df.assign(
        obs_depth=df.depth,
        depth=df.depth.where(~above, top_depth + TOP_CELL_OFFSET),
    )


def _surface_is_first_level(path, model_variable):
    """Whether the surface of model_variable can be read from the first vertical
    level of the files like path (see oceanval.surface), as it is for a variable
    with levels whose first is the surface, and whether CDO is given a small
    chunk cache while it is (surface.small_cache_helps). Says which it is."""
    names = model_variable.split("+")
    try:
        with warnings.catch_warnings(record=True):
            contents = nc.open_data(path, checks=False).contents
        nlevels = {name: int(contents.query("variable == @name").nlevels.iloc[0]) for name in names}
    except Exception:
        return False, False
    if nlevels[names[0]] < 2:
        return False, False
    found, why = surface.first_level_is_surface(path, names, nlevels)
    small_cache = found and surface.small_cache_helps(path, names, nlevels)
    print(f"{model_variable}: {surface.how_read(found, why, small_cache)}")
    return found, small_cache


def mm_match(
    ff, model_variable, df, df_times, ds_depths, variable, df_all, layer=None, surface_fast=False, small_cache=False
):
    """
    Parameters
    -------------
    ff: str
        Path to file
    model_variable: str
        Variable name in the simulation
    df: pd.DataFrame
        Dataframe of observational data
    df_times: pd.DataFrame
        Dataframe of observational data 
    ds_depths: list
        Depths to match

    """


    # the cache holds matchups of unshifted times only, as it does not say
    # how the times were shifted
    use_cache = session_info["cache"] and not session_info.get("time_shift")

    if use_cache:
        try:
            ff_read = (
                session_info["cache_mapping"]
                .query("path == @ff & layer == @layer & variable == @variable")
                .output
            )
            # read this pickle file in
            with open(ff_read.values[0], "rb") as f:
                df_ff = pickle.load(f)
                df_all.append(df_ff)
                return None
        except:
            pass

    df_ff = None

    if ds_depths is not None:
        nc.session.append_safe(ds_depths[0])
    try:
        with warnings.catch_warnings(record=True) as w:
            ds = nc.open_data(ff, checks=False)
            var_match = model_variable.split("+")

            valid_locs = ["lon", "lat", "year", "month", "day", "depth"]
            valid_locs = [x for x in valid_locs if x in df.columns]

            valid_times = (
                "year" in df.columns or "month" in df.columns or "day" in df.columns
            )

            if valid_times:
                df_locs = (
                    df_times.query("path == @ff")
                    .merge(df)
                    .loc[:, valid_locs]
                    .drop_duplicates()
                    .reset_index(drop=True)
                )
            else:
                df_locs = df.loc[:, valid_locs]

            # df_times only has the times in the years asked for
            ff_times = df_times.query("path == @ff")
            if valid_times:
                ff_times = ff_times.merge(df_locs)
            ff_indices = sorted(set(int(x) for x in ff_times.time_index))

            if len(ff_indices) == 0:
                return None

            ds1 = nc.open_data(ds[0], checks=False)
            ds_contents = ds1.contents
            the_var = model_variable.split("+")[0]
            more_than_one_level = list(ds_contents.query("variable == @the_var").nlevels)[0] > 1
            fast = bool(surface_fast) and layer == "surface" and more_than_one_level

            # one select of the variables and the time steps wanted (and, for the surface, its
            # first level), so that CDO reads only those: selname and seltimestep read every
            # time step of every level and drop what is not wanted (see oceanval.surface)
            surface.choose(ds, var_match, fast, ff_indices)

            if more_than_one_level:
                if layer == "surface":
                    if not fast:
                        ds.cdo_command("topvalue")
                else:
                    if session_info["invert"]:
                        ds.invert_levels()
            # times that mark the end of the period each mean covers (see
            # oceanval.time_stamps), shifted as the times index was, so that
            # match_points finds the dates the index gives
            if session_info.get("time_shift"):
                ds.shift(days=session_info["time_shift"])
            if session_info["as_missing"] is not None:
                ds.as_missing(session_info["as_missing"])
            with surface.chunk_cache(fast and bool(small_cache)):
                ds.run()


            if len(var_match) > 1:
                ds.sum_all()
            the_dict = {"model_variable": model_variable}
            session_info["adhoc"] = copy.deepcopy(the_dict)

            if len(df_locs) > 0:
                ds.run()
                if ds_depths is not None:
                    df_ff = ds.match_points(
                        df_locs, depths=ds_depths, quiet=True, max_extrap=0
                    )
                else:
                    df_ff = ds.match_points(
                        df_locs, quiet=True, max_extrap=0
                    )
                if df_ff is not None:
                    df_ff = df_ff.dropna().reset_index(drop=True)

                if df_ff is not None:
                    valid_vars = ["lon", "lat", "year", "month", "day", "depth"]
                    for vv in ds.variables:
                        valid_vars.append(vv)
                    valid_vars = [x for x in valid_vars if x in df_ff.columns]
                    df_ff = df_ff.loc[:, valid_vars]
                    # add this to the cache if necessary
                    if use_cache:
                        cache_dir = session_info["cache_dir"]
                        if cache_dir is not None:
                            # create a random string
                            random_string = "".join(
                                random.choices(
                                    string.ascii_lowercase + string.digits, k=10
                                )
                            )
                            cache_file = (
                                cache_dir
                                + "output/"
                                + "/matchup_"
                                + layer
                                + "_"
                                + variable
                                + "_"
                                + random_string
                                + ".pkl"
                            )
                            if not os.path.exists(cache_dir + "/output"):
                                os.makedirs(cache_dir + "/output")
                            with open(cache_file, "wb") as f:
                                pickle.dump(df_ff, f)
                            # add a mapping to session_info["cache_files"]
                            # add to session_info["cache_dir"] + "/mappings"
                            mapping_file = (
                                cache_dir
                                + "/mappings/mapping_"
                                + layer
                                + "_"
                                + variable
                                + "_"
                                + random_string
                                + ".pkl"
                            )
                            if not os.path.exists(cache_dir + "/mappings"):
                                os.makedirs(cache_dir + "/mappings")
                            # dump ff
                            with open(mapping_file, "wb") as f:
                                output_dir = {ff: cache_file}
                                pickle.dump(output_dir, f)

                    df_all.append(df_ff)
            else:
                return None
        if df_ff is not None:
            for ww in w:
                if str(ww.message) not in _session_warnings():
                    _session_warnings().append(str(ww.message))

    except Exception as e:
        print(e)

example_files = dict()


def _stream_files(paths, example, strict_names, exclude=(), require=None):
    """The files in paths that belong to one output stream, whose example
    file is example: those the file filters keep and, with strict_names, whose
    names are as long as the example's. A "*" in a pattern also matches the
    words that tell a stream's sibling apart, as in grid_T_globmean_*.nc beside
    grid_T_*.nc, and only the length of the name does."""
    for req in require or ():
        paths = [x for x in paths if f"{req}" in os.path.basename(x)]
    for exc in exclude or ():
        paths = [x for x in paths if f"{exc}" not in os.path.basename(x)]
    if strict_names and example is not None:
        len_example = len(os.path.basename(example))
        paths = [x for x in paths if len(os.path.basename(x)) == len_example]
    return paths


def get_time_res(x, folder=None, exclude=()):
    """
    Get the time resolution of the netCDF files

    Parameters
    -------------
    x : str
        The extension of the file
    folder : str
        The folder containing the netCDF files
    exclude : list
        Words that leave out the files whose names have them, as matchup's
        exclude does

    Returns
    -------------
    res : str
        The time resolution of the netCDF files: "monthly", or "1d", "2d",
        ... for output every so many days (see oceanval.time_res.label)

    """

    final_extension = extension_of_directory(folder)

    if final_extension[0] == "/":
        final_extension = final_extension[1:]

    wild_card = final_extension + x
    wild_card = wild_card.replace("**", "*")
    # replace double stars with 1
    wild_card = wild_card.replace("**", "*")

    wild_card = os.path.basename(wild_card)
    paths = sorted(str(y) for y in pathlib.Path(folder).glob(wild_card))
    # the files matchup reads for the pattern, and no others
    paths = _stream_files(
        paths,
        example_files.get(x),
        session_info.get("strict_names", True),
        exclude,
        session_info.get("require"),
    ) or paths

    # output with one time in each file says nothing about its resolution
    # alone, so the times of the next files are added to it. A file with the
    # times of one already read is another output of the same period, not
    # later times of this one
    df_times = []
    read = []
    for path in paths[:6]:
        times = _file_times(path)
        dates = list(zip(times.year, times.month, times.day))
        if dates in read:
            continue
        read.append(dates)
        df_times.append(times)
        if len(pd.concat(df_times)) > 1 or len(read) >= 3:
            break
    df_times = pd.concat(df_times).reset_index(drop=True)

    return time_res.label(df_times)


def _file_times(path):
    """The year, month and day of each time in one file."""
    if session_info.get("fvcom", False):
        return fvcom_times(path)
    ds = nc.open_data(path, checks=False)
    ds_times = ds.times
    try:
        months = [x.month for x in ds_times]
        days = [x.day for x in ds_times]
        years = [x.year for x in ds_times]
    except:
        years = [int(str(x).split("T")[0].split("-")[0]) for x in ds.times]
        months = [int(str(x).split("T")[0].split("-")[1]) for x in ds.times]
        days = [int(str(x).split("T")[0].split("-")[2]) for x in ds.times]
    return pd.DataFrame({"month": months, "day": days, "year": years})


# index_times reads files in this process until this many have been read, or
# this many seconds have passed. If the rest would then take more than
# _PARALLEL_FROM seconds, at the rate seen, it is shared out to worker
# processes: starting them costs a couple of seconds, so a few files are not
_INDEX_HEAD = (20, 1.5)
_PARALLEL_FROM = 5.0

# What each worker process runs. It imports only xarray, as importing
# oceanval takes seconds and hundreds of MB. It reads a JSON request from each
# line of stdin and answers with a line starting @@ (importing can print other
# things). Processes are used because threads cannot read netCDF files at the
# same time (xarray and netCDF4 share a lock), and they are started afresh
# rather than forked, as forked workers have deadlocked on netCDF files.
_TIMES_WORKER = r"""
import json
import sys

import xarray as xr


def times_of(path, time_name, fvcom):
    if fvcom:
        # as oceanval.fvcom.fvcom_times
        with xr.open_dataset(path, drop_variables=["siglay", "siglev"], decode_times=False) as ds:
            time = xr.decode_cf(ds[["time"]]).time.dt
            return [time.year.values.astype(int).tolist(),
                    time.month.values.astype(int).tolist(),
                    time.day.values.astype(int).tolist()]
    with xr.open_dataset(path) as ds:
        time = ds[time_name].dt
        return [time.year.values.astype(int).tolist(),
                time.month.values.astype(int).tolist(),
                time.day.values.astype(int).tolist()]


for line in sys.stdin:
    request = json.loads(line)
    try:
        reply = {"times": times_of(request["path"], request["time_name"], request["fvcom"])}
    except Exception as error:
        reply = {"error": f"{type(error).__name__}: {error}"}
    sys.stdout.write("@@" + json.dumps(reply) + "\n")
    sys.stdout.flush()
"""


def _index_workers(workers=None):
    """How many worker processes index_times may use: workers if given,
    otherwise the cores this process may use, which respects the limits a
    job scheduler sets. This is not matchup's cores option, which limits the
    heavy nctoolkit work; reading the times needs little memory."""
    if workers is not None:
        return max(int(workers), 0)
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


def _read_times(ff, fvcom, time_name):
    """The year, month and day of each time in one model output file."""
    if fvcom:
        return fvcom_times(ff)
    try:
        with xr.open_dataset(ff) as ds:
            time = ds[time_name].dt
            return pd.DataFrame(
                {
                    "year": time.year.values.astype(int),
                    "month": time.month.values.astype(int),
                    "day": time.day.values.astype(int),
                }
            )
    except Exception:
        ds = nc.open_data(ff, checks=False)
        ds_times = ds.times
        return pd.DataFrame(
            {
                "year": [int(x.year) for x in ds_times],
                "month": [int(x.month) for x in ds_times],
                "day": [int(x.day) for x in ds_times],
            }
        )


def _read_times_in_workers(paths, fvcom, time_name, count, done):
    """Read the times of paths in count worker processes (see
    _TIMES_WORKER), calling done(path, DataFrame) for each as it is read, from
    the thread of its worker. Files a worker could not read, or that were left
    when it stopped, are not passed to done."""
    jobs = queue.Queue()
    for ff in paths:
        jobs.put(ff)

    def drive(process):
        try:
            while True:
                try:
                    ff = jobs.get_nowait()
                except queue.Empty:
                    return
                process.stdin.write(
                    json.dumps({"path": ff, "time_name": time_name, "fvcom": fvcom}) + "\n"
                )
                process.stdin.flush()
                reply = None
                for line in process.stdout:
                    if line.startswith("@@"):
                        reply = json.loads(line[2:])
                        break
                if reply is None:
                    # the worker has gone
                    return
                if "times" in reply:
                    year, month, day = reply["times"]
                    done(ff, pd.DataFrame({"year": year, "month": month, "day": day}))
        except (OSError, ValueError):
            return

    processes = []
    threads = []
    try:
        for _ in range(count):
            try:
                processes.append(
                    subprocess.Popen(
                        [sys.executable, "-c", _TIMES_WORKER],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                        encoding="utf-8",
                    )
                )
            except OSError:
                break
        for process in processes:
            thread = threading.Thread(target=drive, args=(process,), daemon=True)
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
    finally:
        for process in processes:
            with contextlib.suppress(OSError):
                process.stdin.close()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdout.close()


def index_times(paths, fvcom=False, times=None, progress=None, workers=None):
    """The year, month and day of each time in each model output file in
    paths, as {path: DataFrame with year, month and day columns}.

    Files already in times (a dict of the same, which is added to and
    returned) are not read again. The name of the
    time dimension comes from the first file read. progress(done, total) is
    called as each file is read, if given; otherwise a bar is shown.

    Many files are read in worker processes, as many as the cores this
    process may use, or workers if given (0 or 1 reads them all here).
    """
    times = dict() if times is None else times
    todo = [ff for ff in dict.fromkeys(paths) if ff not in times]
    if len(todo) == 0:
        return times

    time_name = None
    if not fvcom:
        try:
            with xr.open_dataset(todo[0]) as ds:
                time_name = [x for x in list(ds.dims) if "time" in x][0]
        except:
            with xr.open_dataset(todo[0], decode_times=False) as ds:
                time_name = [x for x in list(ds.dims) if "time" in x][0]

    total = len(todo)
    found = dict()
    lock = threading.Lock()
    bar = tqdm(total=total, disable=progress is not None)

    def done(ff, df):
        with lock:
            found[ff] = df
            bar.update(1)
            if progress is not None:
                progress(len(found), total)

    try:
        count = _index_workers(workers)
        head_files, head_seconds = _INDEX_HEAD
        started = time.monotonic()
        position = 0
        parallel = False
        while position < total:
            done(todo[position], _read_times(todo[position], fvcom, time_name))
            position += 1
            elapsed = time.monotonic() - started
            if count > 1 and position < total and (position >= head_files or elapsed >= head_seconds):
                # at the rate seen, is the rest worth sharing out?
                if elapsed / position * (total - position) >= _PARALLEL_FROM:
                    parallel = True
                break
        rest = todo[position:]
        if parallel:
            _read_times_in_workers(rest, fvcom, time_name, min(count, len(rest)), done)
        # all of them, if not shared out, and otherwise what a worker could
        # not read, which is read as it always was
        for ff in rest:
            if ff not in found:
                done(ff, _read_times(ff, fvcom, time_name))
    finally:
        bar.close()

    for ff in todo:
        times[ff] = found[ff]
    return times


random_files = []
raw_options = []


def _files_found(sim_dir, patterns, exclude, require, strict_names):
    """The model output files matchup reads for each file pattern: those
    matching it that the file filters keep, sorted."""
    final_extension = extension_of_directory(sim_dir)
    found = dict()
    for pattern in patterns:
        paths = _stream_files(
            glob.glob(sim_dir + final_extension + pattern),
            example_files[pattern] if strict_names else None,
            strict_names,
            exclude,
            require,
        )
        found[pattern] = sorted(x for x in paths)
    return found


def extract_variable_mapping(folder, exclude=[], n_check=None):
    """
    Find paths to netCDF files
    Parameters
    -------------
    folder : str
        The folder containing the netCDF files
    exclude : list
        List of strings to exclude

    Returns
    -------------
    all_df : pd.DataFrame
        A DataFrame containing the paths to the netCDF files
    """

    # the files of this simulation only: kept from an earlier matchup in the
    # session, its files were searched for the cell thickness too, and one of
    # another simulation holding it could be used
    raw_options.clear()
    random_files.clear()

    n = 0
    while True:

        levels = session_info["levels_down"]

        new_directory = folder + "/"
        if levels > 0:
            for i in range(levels + 1):
                dir_glob = glob.glob(new_directory + "/**")
                # randomize dir_glob

                random.shuffle(dir_glob)
                for x in dir_glob:
                    # figure out if the the base directory is an integer
                    try:
                        if levels != 0:
                            y = int(os.path.basename(x))
                        new_directory = x + "/"
                    except:
                        pass
        options = glob.glob(new_directory + "/**.nc")
        # if n_check is not None and an integer, limit options to n_check
        if n_check is not None and isinstance(n_check, int):
            options = random.sample(options, min(n_check, len(options)))

        if len([x for x in options if ".nc" in x]) > 0:
            break

        n += 1

        if n > 10* 10000:
            raise ValueError("Unable to find any netCDF files in the provided directory. Check n_dirs_down arg and simulation directory structure.")

    all_df = []
    print("********************************")
    print("Parsing model information from netCDF files")

    for x in options:
        raw_options.append(x)
    # randomize raw_options
    random.shuffle(raw_options)
    # remove any files from options if parts of exclude are in them
    for exc in exclude:
        options = [x for x in options if f"{exc}" not in os.path.basename(x)]
    
    # handle required
    if session_info["require"] is not None:
        for req in session_info["require"]:
            options = [x for x in options if f"{req}" in os.path.basename(x)]

    print("Searching through files in a random directory to identify variable mappings")
    # randomize options
    for ff in tqdm(options):
        random_files.append(ff)
        ds = nc.open_data(ff, checks=False)
        stop = True
        ds_dict = generate_mapping(ds)
        try:
            ds_dict = generate_mapping(ds)
            stop = False
        # output error and ff
        except:
            pass
        if stop:
            continue

        ds_vars = ds.variables

        if len([x for x in ds_dict.values() if x is not None]) > 0:
            new_name = ""
            for x in os.path.basename(ff).split("_"):
                try:
                    y = int(x)
                    if len(new_name) > 0:
                        new_name = new_name + "_**"
                    else:
                        new_name = new_name + "**"
                except:
                    if len(new_name) > 0:
                        new_name = new_name + "_" + x
                    else:
                        new_name = x
            # replace integers in new_name with **

            new_dict = dict()
            for key in ds_dict:
                if ds_dict[key] is not None:
                    #new_dict[ds_dict[key]] = [key]
                    new_dict[key] = [ds_dict[key]]
            # new_name. Replace numbers between _ with **

            # replace integers with 4 or more digits with **
            new_name = re.sub(r"\d{4,}", "**", new_name)
            # replace strings of the form _12. with _**.
            new_name = re.sub(r"\d{2,}", "**", new_name)
            example_files[new_name] = ff

            all_df.append(
                pd.DataFrame.from_dict(new_dict).melt().assign(pattern=new_name)
            )
    
    try: 
        all_df = pd.concat(all_df).reset_index(drop=True)
    except:
        raise ValueError("No netCDF files found with any of the model variables.")
    #  rename variable-value, and value-variable
    all_df = all_df.rename(columns={"variable": "value", "value": "variable"}) 


    patterns = set(all_df.pattern)
    resolution_dict = dict()
    for folder in patterns:
        resolution_dict[folder] = get_time_res(folder, new_directory, exclude)
    all_df["time_res"] = [resolution_dict[x] for x in all_df.pattern]

    # the finest output holding each variable
    all_df = (
        all_df.sort_values("time_res", key=lambda x: x.map(time_res.sort_key), kind="stable")
        .groupby("value")
        .head(1)
        .reset_index(drop=True)
    )
    all_df = all_df.rename(columns={"variable": "model_variable"})
    all_df = all_df.rename(columns={"value": "variable"})
    all_df = all_df.loc[:, ["variable", "model_variable", "pattern", "time_res"]]

    # add example file column
    all_df["example_file"] = [
        example_files[x] for x in all_df.pattern
    ]

    return all_df


# matchup's default number of cores: 6, or the system's if it has fewer
DEFAULT_CORES = min(6, os.cpu_count() or 6)


def _stops_live_validation(function):
    """matchup, stopping the interim report it started (see oceanval.live)
    if it does not get to finish it: an error, or Ctrl+C."""

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        finally:
            live.abandon()

    return wrapper


def _restores_nctoolkit_options(function):
    """matchup, putting nctoolkit's cores and parallel options back as they
    were when it ends, however it ends. matchup sets them for its own work,
    and nctoolkit would otherwise go on using them for anything else in the
    session: with more than one core, it runs NCO, and CDO on multi-file
    datasets, in pools of its own."""

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        before = {name: nc.session_info[name] for name in ("cores", "parallel")}
        try:
            return function(*args, **kwargs)
        finally:
            for name, value in before.items():
                if nc.session_info[name] != value:
                    nc.options(**{name: value})

    return wrapper


@_stops_live_validation
@_restores_nctoolkit_options
def matchup(
    sim_dir=None,
    start=None,
    end=None,
    lon_lim=None,
    lat_lim=None,
    cores=DEFAULT_CORES,
    thickness=None,
    n_dirs_down=2,
    point_time_res=["year", "month", "day"],
    overwrite=True,
    ask=True,
    out_dir="",
    exclude=[],
    require = None,
    cache=False,
    n_check=None,
    as_missing=None,
    strict_names = True,
    fvcom = False,
    live_validation = None,
    time_shift = None,
    data_store = None,
    fvcom_res = fvcom_default_res,
    fvcom_min_depth = fvcom_default_min_depth,
    fvcom_max_depth = fvcom_default_max_depth,
    fvcom_dz = fvcom_default_dz,
    domain_mask = None,
):
    """
    Match up model with observational data

    Parameters
    -------------

    sim_dir : str
        Folder containing model output
    start : int
        Start year. First year of the simulations to matchup.
        This must be supplied
    end : int
        End year. Final year of the simulations to matchup.
        This must be supplied
    lon_lim : list
        List of two floats, which must be provided. The first is the minimum longitude, the second is the maximum longitude. Default is None.
    lat_lim : list
        List of two float. Default is None, so no spatial subsetting will occur. The first is the minimum latitude, the second is the maximum latitude. Default is None.
    cores : int
        Number of cores to use for parallel extraction and matchups of data.
        Default is 6, or the system cores if less than 6.
        If you use a large number of cores you may run into RAM issues, so keep an eye on things.
        nctoolkit's own cores option is put back as it was once matchup finishes.
    thickness : str
        Path to a thickness file, i.e. cell vertical thickness or the name of the thickness variable. This only needs to be supplied if the variable is missing from the raw data.
        A name must be the variable's exact name in the model output, e.g. "e3t": a variable that only has it in its name, such as O3_c_e3t, is not used.
        A thickness file must hold only the thickness variable (or one with cell_thickness in its name). The thickness can have a time dimension or not,
        and its vertical coordinate needs a positive attribute ("down" for NEMO), to tell whether its first level is the surface.
        With "z_level", the model's levels must be depths (positive down); output on heights (positive up) needs the cell thickness instead.
        If the cell_thickness variable is in the raw data, it will be used, and thickness does not need to be supplied.
    n_dirs_down : int
        Number of levels down to look for netCDF files. Default is 2, ie. the files are of the format ``*/*/*.nc``.
    point_time_res : list or str
        Which of "year", "month" and "day" point observations are matched to the model by. Default is ['year', 'month', 'day'].
        Leave out "year" to compare the observations climatologically, and "day" for monthly model output.
        A point_time_res given to add_point_comparison is used for that dataset instead.
    overwrite : bool or list
        If True, existing matched data will be overwritten. Default is True. False keeps any matchup this run would make that is in out_dir already, and uses it as it is.
        A list overwrites only the existing matchups it picks, each entry a variable, for all its sources, or "variable/source", for one, as validate's variables takes them, e.g. ["temperature/COBE2"]: the others are kept.
        Matchups of variables or sources this run does not match up are left in out_dir either way (see validate's previous).
    ask : bool
        If True, the user will be asked if they are happy with the matchups. Default is True.
    out_dir : str
        Path to output directory. Default is "", so the output will be saved in the current directory.
        Nothing is written in it until the matchups are confirmed. The oceanval window chooses out_dir,
        overwrite and cores along with that answer.
    exclude : list
        List of strings to exclude. This is useful if you have files in the directory that you do not want to include in the matchup.
    require : list
        List of strings to require. This is useful if you want to only include files that have certain strings in their names. Defaults to None, so there are no requirements.
    cache : bool
        If True, caching will be used to speed up future matchups. Default is False.
    n_check : int
        Number of files to check when extracting variable mappings. Default is None, so all files will be checked.
    as_missing : float or list
        Value(s) to treat as missing in the model data. Default is None.
    strict_names : bool
        If True, variable names must match exactly those in the definitions. Default is True.
    fvcom : bool
        Set to True if sim_dir contains raw FVCOM output. Default is False.
        Each model file is then regridded with ``fvcom_preprocess`` before matching:
        surface values for comparisons with vertical=False, and the depths given by
        fvcom_min_depth, fvcom_max_depth and fvcom_dz (0-150 m every 5 m by default)
        for comparisons with vertical=True. Point comparisons use a grid of fvcom_res
        degrees (0.05 by default), and gridded comparisons use a grid at twice the
        resolution of the observations.
        thickness is not needed, and lon_lim and lat_lim default to the extent of the mesh.
        The regridded files are deleted once the matchups are done.
    fvcom_res : float
        The lon-lat resolution, in degrees, of the grid FVCOM output is regridded to for point
        comparisons. Default is 0.05. Only used if fvcom is True.
    fvcom_min_depth : float
        The shallowest depth (m, positive down) FVCOM output is interpolated to for comparisons
        with vertical=True. Default is 0. Only used if fvcom is True.
    fvcom_max_depth : float
        The deepest depth (m) FVCOM output is interpolated to for comparisons with
        vertical=True. Default is 150. Observations deeper than this are not matched.
        Only used if fvcom is True.
    fvcom_dz : float
        The vertical spacing (m) of the depths between fvcom_min_depth and fvcom_max_depth.
        Default is 5. Only used if fvcom is True.
    live_validation : bool or dict
        Build an interim validation report, HTML only, as the matchups are made: each matchup's
        page is added as soon as the matchup is made, and the summary is run again, so the
        results can be looked at long before the last matchup is made. True builds it with
        validate's defaults. A dict gives validate's report options: out_dir, lon_lim, lat_lim,
        subregions, fixed_scale, concise and transect (pdf, word and zip are for validate's
        full report).
        It is built in oceanval_interim_report, in out_dir (matchup's, unless the dict gives
        another), and matchup waits for it to be finished before returning. Build the full
        report with validate afterwards, as usual. Default is None, no interim report.
    time_shift : int
        Number of days to shift every model time by before matching up, e.g. -1 for output that
        dates each time mean at the end of the period it covers (00:00 on 1 February for a
        January mean): shifted back a day, each mean falls in the period it covers.
        Default is None: matchup checks whether the times of time means look like the ends of
        their periods and, if so, asks whether to shift them back a day (with ask=False it only
        says so, and leaves them as they are). 0 leaves the times as they are, without checking.
    data_store : None or False
        Whether to use your data store, the folder set in the oceanval window's Preferences
        where the built-in observational data downloaded (WOA23, OCCCI, NSBC, COBE2, GLODAP and
        ICES) is kept for later matchups, used as the Preferences say. Your own data is never
        kept in it. Default is None, to use it if one is set; False does not use it for this run.
    domain_mask : str
        Match up only part of the domain: the path of a netCDF file with a single variable at a
        single time step, 1 in the cells wanted and 0 or missing elsewhere. It is copied, as 1
        and missing, to oceanval_matchups/domain_mask.nc. Gridded matchups put the model's monthly
        means on the file's grid by nearest neighbour and keep only the cells of 1; point matchups keep only the locations that are in a
        cell of 1, by nearest neighbour. Default is None, the whole domain.

    Returns
    -------------
    None
    Data will be stored in the matched directory.

  """
  # check if the sim_dir exists
    if sim_dir is None:
        raise ValueError("Please provide a sim_dir directory")
    if not os.path.exists(sim_dir):
        raise ValueError(f"{sim_dir} does not exist")
    # convert sim_dir to hard path
    if sim_dir is not None:
        sim_dir = os.path.abspath(sim_dir)

    if start is None:
        raise ValueError("Please provide a start year")
    if isinstance(start, int) is False:
        raise TypeError("Start must be an integer")

    if end is None:
        raise ValueError("Please provide an end year")
    if isinstance(end, int) is False:
        raise TypeError("End must be an integer")

    # check lon_lim and lat_lim are lists
    lon_lim, lat_lim = _checked_limits(lon_lim, lat_lim)

    # add this info to session_info
    session_info["lon_lim"] = lon_lim
    session_info["lat_lim"] = lat_lim

    cores = _checked_cores(cores)
    nc.options(cores=cores)
    session_info["cores"] = cores

    # kept in the matchups folder, and said so in session_info, once the matchups are confirmed
    domain_mask = domain_mask_mod.check(domain_mask)
    session_info["domain_mask"] = None

    if not isinstance(fvcom, bool):
        raise TypeError("fvcom must be a boolean")
    session_info["fvcom"] = fvcom
    session_info["fvcom_files"] = dict()
    session_info["fvcom_res"], session_info["fvcom_levels"] = check_fvcom_settings(
        fvcom_res, fvcom_min_depth, fvcom_max_depth, fvcom_dz
    )
    if fvcom:
        # regridded FVCOM output is on z-levels, so no thickness is needed
        if thickness is not None and thickness not in ["z_level", "z-level", "z level"]:
            warnings.warn("thickness is ignored when fvcom=True")
        thickness = "z_level"

    if thickness is not None:
        if isinstance(thickness, str):
            # if it ends with .nc check it exists
            if thickness.endswith(".nc"):
                if not os.path.exists(thickness):
                    raise FileNotFoundError(f"{thickness} does not exist")
    if thickness == "z-level":
        thickness = "z_level"
    if thickness == "z level":
        thickness = "z_level"
    if thickness == "z_level":
        session_info["z_level"] = True
    else:
        session_info["z_level"] = False

    # check n_dirs_down is an integer
    if not isinstance(n_dirs_down, int):
        raise TypeError("n_dirs_down must be an integer")
    if n_dirs_down < 0:
        raise ValueError("n_dirs_down must be a positive integer")
    session_info["levels_down"] = n_dirs_down

    if isinstance(point_time_res, str):
        point_time_res = [point_time_res]
    if isinstance(point_time_res, list) is False:
        raise TypeError("point_time_res must be a list or a string")
    session_info["point_time_res"] = copy.deepcopy(point_time_res)

    overwrite = _checked_overwrite(overwrite)
    session_info["overwrite"] = overwrite

    if not isinstance(ask, bool):
        raise TypeError("ask must be a boolean")

    # where the built-in observations downloaded are kept, if anywhere
    obs_store.start(obs_store.config(data_store))

    out_dir = _full_out_dir(out_dir)
    # add out_dir to session_info
    session_info["out_dir"] = out_dir + "/"

    # the interim report, built as the matchups are made (see oceanval.live)
    live_options = live.check_options(live_validation, out_dir)

    # regridded FVCOM files only live for the duration of the matchup
    session_info["fvcom_dir"] = session_info["out_dir"] + "oceanval_matchups/fvcom_tmp"
    if fvcom:
        # left behind by a matchup that did not finish
        _remove_fvcom_dir(session_info["fvcom_dir"])

    # check if exclude is a list or str
    if not isinstance(exclude, list):
        if isinstance(exclude, str):
            exclude = [exclude]
        else:
            raise TypeError("exclude must be a list or a string")
    # need to check each item in exclude is a string
    for ex in exclude:
        if not isinstance(ex, str):
            raise TypeError("each item in exclude must be a string")

    if require is not None:
        if isinstance(require, str):
            require = [require]
        if not isinstance(require, list):
            raise TypeError("require must be a list or a string")
        # need to check each item in require is a string
        for rq in require:
            if not isinstance(rq, str):
                raise TypeError("each item in require must be a string")

    session_info["require"] = require


    # check cache is a boolean
    if not isinstance(cache, bool):
        raise TypeError("cache must be a boolean")

    # check n_check is None or an integer
    if n_check is not None:
        if not isinstance(n_check, int):
            raise TypeError("n_check must be an integer")
    # ensure n_check is positive
        if n_check < 1:
            raise ValueError("n_check must be a positive integer")

    # check as_missing is None, float or list
    session_info["as_missing"] = as_missing = _checked_missing(as_missing)

    # check strict_names is a boolean
    if not isinstance(strict_names, bool):
        raise TypeError("strict_names must be a boolean")
    session_info["strict_names"] = strict_names

    if not time_stamps.valid(time_shift):
        raise TypeError("time_shift must be a whole number of days, or None")
    # until the times have been checked, nothing is shifted
    session_info["time_shift"] = 0

    gridded = []

    point = dict()
    point["all"] = []
    point["surface"] = []

    if len(definitions.keys) == 0:
        raise ValueError("You do not appear to have asked for any variables to be validated!")
    # each source of a variable is its own comparison, so these hold
    # (variable, source) pairs
    for key in definitions.keys:
        for source, comparison in definitions[key].point_comparisons.items():
            if comparison["vertical"]:
                point["all"].append((key, source))
            else:
                point["surface"].append((key, source))
        # do the same for gridded
        for source in definitions[key].gridded_comparisons:
            gridded.append((key, source))

    # if cache is True, create a cache directory in out_dir
    if cache:
        cache_dir = out_dir + "/.cache_oceanval/"
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        session_info["cache_dir"] = cache_dir
        session_info["cache"] = True
        # create a mappings directory in cache_dir
        mappings_dir = cache_dir + "/mappings/"
        if not os.path.exists(mappings_dir):
            os.makedirs(mappings_dir)
        # list files in mappings_dir
        paths = glob.glob(mappings_dir + "/*.pkl")
        mapping_df = []
        for ff in paths:
            with open(ff, "rb") as f:
                output_dir = pickle.load(f)
            ff_path = list(output_dir.keys())[0]
            ff_output = list(output_dir.values())[0]
            ff_layer = os.path.basename(ff).split("_")[1]
            ff_variable = os.path.basename(ff).split("_")[2]
            mapping_df.append(
                pd.DataFrame(
                    {
                        "variable": [ff_variable],
                        "layer": [ff_layer],
                        "path": [ff_path],
                        "output": [ff_output],
                    }
                )
            )
        if len(mapping_df) > 0:
            mapping_df = pd.concat(mapping_df).reset_index(drop=True)
        session_info["cache_mapping"] = mapping_df

    else:
        session_info["cache_dir"] = None
        session_info["cache"] = False

    ds_depths = None


    session_info["short_title"] = _saved_titles(session_info["out_dir"]) | session_info["short_title"]

    sim_start = -1000
    sim_end = 10000

    if end is not None:
        sim_end = end

    if start is not None:
        sim_start = start

    # check validity of variables chosen

    all_vars = definitions.keys

    all_df = extract_variable_mapping(sim_dir, exclude=exclude, n_check=n_check)
    # check if any model_variable is None
    var_found = list(all_df.variable.unique())
    missing = ",".join([x for x in all_vars if x not in var_found])
    # 
    if len(missing) > 0:
        error = f"The model variables specified do not appear to be in the simulation output for the following: {missing}. Please check the model_variable names and try again."
        raise ValueError(error)

    if fvcom and lon_lim is None:
        # FVCOM output has to be regridded onto a lon/lat box
        lon_lim, lat_lim = fvcom_extent(all_df.example_file.iloc[0])
        session_info["lon_lim"] = lon_lim
        session_info["lat_lim"] = lat_lim

    # add in anything that is missing

    missing_df = pd.DataFrame({"variable": all_vars}).assign(
        model_variable=None, pattern=None, time_res=None
    )

    all_df = (
        pd.concat([all_df, missing_df])
        .groupby("variable")
        .head(1)
        .reset_index(drop=True)
        )
    # check if the variables are in all_df

    pattern = all_df.reset_index(drop=True).iloc[0, :].pattern

    final_extension = extension_of_directory(sim_dir)

    if final_extension[0] == "/":
        final_extension = final_extension[1:]

    wild_card = final_extension + pattern
    wild_card = wild_card.replace("**", "*")
    for x in pathlib.Path(sim_dir).glob(wild_card):
        path = x
        # convert to string
        path = str(path)
        break

    try:
        ds = nc.open_data(path, checks=False)
    except:
        raise ValueError("Problems finding files. Check n_dirs_down arg")

    # check length of lon_lim and lat_lim
    if session_info["lon_lim"] is not None:
        if len(session_info["lon_lim"]) != 2 or len(session_info["lat_lim"]) != 2:
            raise ValueError(
                "lon_lim and lat_lim must be lists of two floats, e.g. [-18, 9] and [42, 63]"
            )


    vars_available = list(
        all_df
        # drop rows where pattern is None
        .dropna()
        # get all variables
        .variable
    )
    # check variables chosen are valid

    remove = []

    gridded = [x for x in gridded if x[0] in vars_available]
    for key in point.keys():
        point[key] = [x for x in point[key] if x[0] in vars_available]
    gridded_vertical = [
        x for x in gridded if definitions[x[0]].gridded_comparisons[x[1]]["vertical"]
    ]
    gridded_surface = [x for x in gridded if x not in gridded_vertical]

    var_chosen = [vv for vv, source in gridded + point["all"] + point["surface"]]
    var_chosen = list(set(var_chosen))

    # nothing is written in out_dir until the matchups are confirmed: the oceanval
    # window chooses out_dir along with yes. The thickness's bathymetry is kept until then
    bathymetry = None
    # the thickness before its missing values are set, if it is needed
    thickness_selected = None

    invert_thickness = False
    point_all = point["all"] + point["surface"]

    # go through variables in definitions
    thick_check = False
    for vv, source in gridded:
        # identifical if vertical_gridded is True
        if definitions[vv].gridded_comparisons[source]["vertical"]:
            thick_check = True
    if len(point["all"]) > 0:
        thick_check = True

    # FVCOM output is regridded onto depths later
    if thick_check and not fvcom:
        _check_water_column_variables(gridded_vertical + point["all"], all_df)

    if session_info["z_level"]:
        # on z-levels the model's levels are matched as depths, so they
        # cannot be heights. FVCOM output is regridded onto depths
        if thick_check and not fvcom:
            vertical = [vv for vv, source in gridded if definitions[vv].gridded_comparisons[source]["vertical"]]
            vertical += [vv for vv, source in point["all"]]
            for model_variable in dict.fromkeys(definitions[vv].model_variable for vv in vertical):
                name = model_variable.split("+")[0]
                files = list(all_df.loc[all_df.model_variable == model_variable].example_file)
                ff = files[0] if len(files) > 0 else next(
                    (x for x in raw_options if name in nc.open_data(x, checks=False).variables), None
                )
                if ff is not None and _levels_are_heights(ff, name):
                    raise ValueError(
                        f"The vertical levels of {name} in {ff} are heights (positive up), but "
                        'with thickness="z_level" OceanVal matches observations by depth, positive '
                        "down. Give the cell thickness as thickness instead, or use output on depth levels."
                    )
        thick_check = False

    if thick_check:
        print("Sorting out thickness")
        ds_depths = False
        with warnings.catch_warnings(record=True) as w:
            # extract the thickness dataset
            cell_thickness_found = False
            if thickness is not None and os.path.exists(thickness):
                ds_thickness = nc.open_data(thickness, checks=False)
                thickness_variable = _thickness_from_file(thickness)
                invert_thickness = is_z_up(ds_thickness[0], thickness_variable)
                ds_thickness.rename({thickness_variable: "cell_thickness"})
                thickness_label = f"{thickness_variable} in {thickness}"
                cell_thickness_found = True
                thickness = "cell_thickness"
            else:
                print(
                    "Vertical thickness is required for your matchups, but they are not supplied"
                )
                print("Searching through simulation output to find it")
                if thickness is None:
                    raise ValueError(
                        "Please provide the name of the thickness variable"
                    )
                ff = _thickness_file(raw_options, thickness)
                ds_thickness = nc.open_data(ff, checks=False)
                thickness_label = f"{thickness} in {ff}"
                cell_thickness_found = True
                invert_thickness = is_z_up(ff, thickness)

            if not cell_thickness_found:
                raise ValueError("Unable to find cell_thickness")

            # only the variable of exactly this name, not others with it in
            # their names
            if os.path.exists(thickness) == False:
                if len(ds_thickness.times) > 0:
                    ds_thickness.subset(time=0, variables=thickness)
                else:
                    ds_thickness.subset(variables=thickness)
            ds_thickness.run()
            var_sel = _thickness_levels(ds_thickness, thickness, thickness_label)
            ds_thickness.subset(variables=var_sel)
            ds_thickness.run()
            print(f"Thickness variable is {ds_thickness.variables[0]} ({thickness_label})")
            # as it is before its missing values are set, for the oceanval
            # window, which gives them along with yes
            thickness_selected = ds_thickness

            if invert_thickness:
                if ask:
                    # user check
                    x = prompts.ask(
                        "The thickness data appears to have the sea surface at the bottom (i.e. increasing depth values down. DO NOT PROCEED IF THIS IS A NEMO SIMULATION. Is this correct? (y/n) ",
                        ("y", "n"),
                    )
                    if x.lower() == "n":
                        return None
                else:
                    print("###### Inverting thickness automatically")
                    print(
                        "The thickness data appears to have the sea surface at the bottom (i.e. increasing depth values down. This has been inverted automatically."
                    )
                    print("##########################################################################")

            # the bathymetry is written in out_dir once the matchups are confirmed
            bathymetry, ds_thickness_sim, ds_depths = _thickness_and_depths(
                thickness_selected, session_info["as_missing"], invert_thickness
            )

        for ww in w:
            if str(ww.message) not in _session_warnings():
                _session_warnings().append(str(ww.message))
        if ds_depths is False:
            raise ValueError(
                "You have asked for variables that require the specification of thickness"
            )
        print("Thickness is sorted out")

        ds_depths.run()
    if ds_depths is not None:
        session_info["ds_depths"] = ds_depths[0]
        session_info["ds_thickness"] = ds_thickness_sim[0]
    else:
        session_info["ds_depths"] = thickness
        session_info["ds_thickness"] = None 

    session_info["invert"] = invert_thickness

    # add the global checker here
    # sort all_df alphabetically by variable
    all_df = all_df.sort_values("variable").reset_index(drop=True)
    gridded.sort()

    chosen = all_df.query("variable in @var_chosen").dropna()
    files = _files_found(
        sim_dir, sorted(set(chosen.pattern)), exclude, session_info["require"], strict_names
    )
    # the files are looked at by CDO while the times are checked
    cdo_checked = cdo_check.start(path for found in files.values() for path in found)

    # time means whose times look like the end of the period each covers are
    # asked about before the matchups are shown, so that the files and dates
    # shown with them are those matched up (see oceanval.time_stamps). The
    # times are only shifted if the user says so, here or in their matchup
    # call. Raw FVCOM output is not checked
    if time_shift is None:
        time_shift = 0
        rows = [] if fvcom else time_stamps.check_rows(
            chosen, files, {vv: definitions[vv].short_title for vv in var_chosen}
        )
        if rows and ask:
            print(time_stamps.warning(rows))
            time_shift = time_stamps.ask(rows, prompts.ask)
            if time_shift:
                print(f"Add time_shift={time_shift} to your matchup call to keep this next time.")
            else:
                print("Add time_shift=0 to your matchup call to leave the times without checking next time.")
            print("******************************")
        elif rows:
            print(time_stamps.warning(rows, asked=False))
            print("******************************")
            session_info["end_messages"].append(time_stamps.end_message(rows))
    session_info["time_shift"] = time_shift
    cdo_check.require(cdo_checked)
    if time_shift:
        print(f"The model's times are shifted {time_stamps.describe(time_shift)} before matching up")

    print("Variables that will be matched up")
    print("******************************")
    if len(gridded) > 0:
        print(
            f"The following variables will be matched up with gridded surface data: {', '.join(f'{vv} ({source})' for vv, source in gridded)}"
        )

    print("******************************")
    print(f"** Inferred mapping of model variable names from {sim_dir}")

    all_df_print = copy.deepcopy(all_df).reset_index(drop=True)

    # new tidied variable
    new_variable = []
    for i in range(len(all_df_print)):
        if all_df.variable[i] in var_chosen:
            if all_df.pattern[i] is not None:
                new_variable.append(all_df.variable[i] + "**")
            else:
                new_variable.append(all_df.variable[i])
        else:
            new_variable.append(all_df.variable[i])
    all_df_print["variable"] = new_variable
    all_df_print = all_df_print.sort_values("variable")
    # move variable with ** in it to the top of the data frame
    all_df_print = all_df_print.sort_values(
        by="variable", key=lambda x: x.str.replace(r".*\*\*", "", regex=True)
    )
    all_df_print = all_df_print.reset_index(drop=True)
    # add a new row with --- in it after variables with ** in them
    new_row = pd.DataFrame(
        {"variable": ["---"], "model_variable": ["---"], "pattern": ["---"], "time_res": ["---"]}
    )
    all_df_print = pd.concat([all_df_print, new_row], ignore_index=True)
    all_df_print = all_df_print.sort_values(
        by="variable", key=lambda x: x.str.replace(r".*\*\*", "", regex=True)
    )
    all_df_print = all_df_print.reset_index(drop=True)

    print(all_df_print.to_string(index=False))

    observations = dict()
    # each dataset, with the years of the run it is matched up for, its own
    # start and end within the run's: the window lists each variable once for
    # each of these, with the files in them and their dates (see
    # oceanval.model_check.year_groups)
    datasets = dict()

    def own_years(comparison):
        start, end = comparison.get("start"), comparison.get("end")
        return [
            sim_start if start is None else max(sim_start, int(start)),
            sim_end if end is None else min(sim_end, int(end)),
        ]

    for vv, source in gridded:
        observations.setdefault(vv, []).append(source)
        datasets.setdefault(vv, []).append(
            {"source": source, "gridded": True,
             "years": own_years(definitions[vv].gridded_comparisons[source])}
        )
    for vv, source in point["all"] + point["surface"]:
        observations.setdefault(vv, []).append(f"{source} (point)")
        dataset = {"source": f"{source} (point)", "gridded": False,
                   "years": own_years(definitions[vv].point_comparisons[source])}
        # a point dataset matched up at the surface and through the water column
        if dataset not in datasets.setdefault(vv, []):
            datasets[vv].append(dataset)
    # the matchups to make, as (kind, variable, source, layer), in the order
    # they are made
    items = (
        [("gridded", vv, source, None) for vv, source in gridded_surface]
        + [("point", vv, source, "surface") for vv, source in point["surface"]]
        + [("gridded", vv, source, None) for vv, source in gridded_vertical]
        + [("point", vv, source, "all") for vv, source in point["all"]]
    )
    # what the oceanval window shows in place of the table printed above
    details = {
        "kind": "matchups",
        "sim_dir": sim_dir,
        "years": [sim_start, sim_end],
        # how the times of the files are read, for the window's check of
        # their dates (see oceanval.model_check), and the days they are
        # shifted by, so that it shows the dates matched up
        "fvcom": bool(fvcom),
        "time_shift": time_shift,
        "rows": [
            {
                "variable": row.variable,
                "title": definitions[row.variable].short_title,
                "model_variable": row.model_variable,
                "pattern": row.pattern,
                "observations": observations.get(row.variable, []),
                "groups": model_check.year_groups(
                    datasets.get(row.variable, []), [sim_start, sim_end]
                ),
                "files": len(files[row.pattern]),
                "time_res": row.time_res,
            }
            for row in chosen.itertuples()
        ],
        "files": {
            pattern: [os.path.relpath(path, sim_dir) for path in paths]
            for pattern, paths in files.items()
        },
        # each matchup, which the window lists to choose those the report validates
        "matchups": [
            {
                "kind": kind,
                "variable": vv,
                "source": source,
                "layer": layer,
                "vertical": layer == "all" or (vv, source) in gridded_vertical,
                "title": definitions[vv].short_title,
                "key": selection.key(vv, source),
            }
            for kind, vv, source, layer in items
        ],
    }

    # point datasets matched by day against output coarser than daily, which
    # the window asks about once the matchups are right (see oceanval.time_res)
    time_res_rows = time_res.check_rows(
        point["all"] + point["surface"],
        dict(zip(chosen.variable, chosen.time_res)),
        lambda vv, source: definitions[vv].point_comparisons[source],
        session_info["point_time_res"],
        {vv: definitions[vv].short_title for vv in var_chosen},
    )
    if time_res_rows:
        details["point_time_res"] = {
            "default": list(session_info["point_time_res"]),
            "rows": time_res_rows,
        }

    question = "Are you happy with these matchups? (y/n) "
    if ask:
        x = prompts.ask(question, ("y", "n"), details=details)
    else:
        print("Are you happy with these matchups? Y/N")
        x = "y"

    if x.lower() not in ["y", "n"]:
        print("Provide Y or N")
        x = prompts.ask(question, ("y", "n"), details=details)

    if x.lower() == "n":
        print("Please adjust your variable names and try again")
        return None

    # the matchups are confirmed, so the simulation is one to offer again
    user_cache.record_sim_dir(sim_dir)

    # in the oceanval window, where the matchups go, which of those there already
    # are made again, and the cores are chosen along with yes (see oceanval.app)
    given = getattr(x, "settings", {})
    if "out_dir" in given:
        out_dir = _full_out_dir(given["out_dir"])
        session_info["out_dir"] = out_dir + "/"
        session_info["fvcom_dir"] = session_info["out_dir"] + "oceanval_matchups/fvcom_tmp"
        if fvcom:
            _remove_fvcom_dir(session_info["fvcom_dir"])
        session_info["short_title"] = _saved_titles(session_info["out_dir"]) | session_info["short_title"]
    if "overwrite" in given:
        overwrite = session_info["overwrite"] = _checked_overwrite(given["overwrite"])
    if "cores" in given:
        cores = session_info["cores"] = _checked_cores(given["cores"])
        nc.options(cores=cores)
    # the spatial subset and the missing values, which the window asks along
    # with the cores: nothing before this has used them, but the thickness
    if "lon_lim" in given or "lat_lim" in given:
        lon_lim, lat_lim = _checked_limits(given.get("lon_lim"), given.get("lat_lim"))
        session_info["lon_lim"], session_info["lat_lim"] = lon_lim, lat_lim
    if "domain_mask" in given:
        # the window's domain mask (see oceanval.app.check_domain_mask)
        domain_mask = domain_mask_mod.check(given["domain_mask"])
    if "as_missing" in given:
        as_missing = _checked_missing(given["as_missing"])
        if as_missing != session_info["as_missing"]:
            session_info["as_missing"] = as_missing
            if thickness_selected is not None:
                bathymetry, ds_thickness_sim, ds_depths = _thickness_and_depths(
                    thickness_selected, as_missing, invert_thickness
                )
                session_info["ds_depths"] = ds_depths[0]
                session_info["ds_thickness"] = ds_thickness_sim[0]

    os.makedirs(session_info["out_dir"] + "oceanval_matchups", exist_ok=True)
    session_info["domain_mask"] = domain_mask_mod.keep(domain_mask, session_info["out_dir"] + "oceanval_matchups")
    ff_bath = session_info["out_dir"] + "oceanval_matchups/model_bathymetry.nc"
    if bathymetry is not None and not os.path.exists(ff_bath):
        bathymetry.vertical_sum()
        bathymetry.to_nc(ff_bath, zip=True)

    if time_res_rows:
        print(time_res.warning(time_res_rows))
        choice = getattr(x, "settings", {}).get("point_time_res")
        if choice is None and ask and not hasattr(x, "settings"):
            # at a terminal: the window chooses along with yes
            choice = time_res.ask_at_terminal(time_res_rows, prompts.ask)
        choice = time_res.clean_choice(choice, time_res_rows)
        changed = time_res.apply(choice, time_res_rows, session_info, definitions)
        for line in changed:
            print(line)
        if not changed:
            print("point_time_res is unchanged.")
        elif not hasattr(x, "settings"):
            print("Change point_time_res in your matchup script to keep this next time.")
        print("******************************")

    # in the oceanval window, the report's options are chosen along with yes,
    # and the interim report is built with them
    chosen = getattr(x, "settings", {}).get("live_validation")
    if chosen is not None:
        live_options = live.check_options(chosen, out_dir)
    builder = None
    if live_options is not None:
        # with one variable and source, the interim report would be the full
        # report built twice, so validate() is left to build it
        wanted = {
            (vv, source)
            for _, vv, source, _ in items
            if selection.wanted(live_options["variables"], vv, source)
        }
        if len(wanted) > 1:
            builder = live.LiveValidation(
                live_options,
                out_dir,
                items,
                session_info["short_title"],
            )
            builder.start()
        else:
            print("Only one variable and source is matched up, so no interim report is built.")
    # the last run's record is this run's once it has finished
    selection.forget_last_run(session_info["out_dir"] + "oceanval_matchups")
    told = _Told(builder)
    prompts.tell(_matchups_news(items, session_info["short_title"], builder))

    out = session_info["out_dir"] + "/oceanval_matchups/mapping.csv"
    # check directory exists for out
    out_folder = os.path.dirname(out)
    if not os.path.exists(out_folder):
        os.makedirs(out_folder)

    df_out = all_df.drop(columns="time_res").dropna().reset_index(drop=True)
    final_extension = extension_of_directory(sim_dir)
    df_out["pattern"] = [sim_dir + final_extension + x for x in df_out.pattern]
    df_out.to_csv(out, index=False)
    # restrict all_df to only variables chosen
    all_df = all_df.query("variable in @var_chosen").reset_index(drop=True)

    final_extension = extension_of_directory(sim_dir)

    # combine all variables into a list
    all_vars = [vv for vv, source in gridded + point["all"] + point["surface"]]
    all_vars = list(set(all_vars))

    df_variables = all_df.query("variable in @all_vars").reset_index(drop=True)
    # remove rows where model_variable is None
    df_variables = df_variables.dropna().reset_index(drop=True)

    patterns = list(set(df_variables.pattern))

    times_dict = dict()
    # in the oceanval window, the check of the model output made before yes
    # has read the files' times already (see oceanval.model_check)
    times_index = getattr(x, "settings", {}).get("times_index")
    if times_index:
        listed = [path for pattern in patterns for path in files[pattern]]
        times_dict = model_check.load_times_index(times_index, listed)
        if times_dict:
            print(f"The times of {len(times_dict)} files were read when the model output was checked")

    print("*************************************")
    print("Indexing file time information")
    # the files listed with the question, for all the patterns at once, so
    # that they can be shared out between worker processes together
    index_times(
        [path for pattern in patterns for path in files[pattern]],
        fvcom=fvcom,
        times=times_dict,
    )
    if time_shift:
        # the dates every file's times are matched up by, in its pattern's calendar
        pattern_of = {path: pattern for pattern in patterns for path in files[pattern]}
        calendars = {
            pattern: time_stamps.calendar(files[pattern][0]) for pattern in patterns if files[pattern]
        }
        time_stamps.shift_index(
            times_dict, time_shift, lambda path: calendars.get(pattern_of.get(path), "standard")
        )

    # figure out the lon/lat extent in the model
    with warnings.catch_warnings(record=True) as w:
        lons = session_info["lon_lim"]
        lats = session_info["lat_lim"]

    all_df = all_df.dropna().reset_index(drop=True)
    df_mapping = all_df
    good_model_vars = [x for x in all_df.model_variable if x is not None]

    df_mapping = all_df

    point_all = point["all"] + point["surface"]
    point_announced = False

    def match_point_layer(key):
        """Match up the point observations of one layer, "surface" or "all"."""
        nonlocal point_announced
        if len(point[key]) == 0:
            return None
        if not point_announced:
            print("********************************")
            print("Matching up with observational point data")
            print("********************************")
            point_announced = True

        # if model_variable is None remove from all_df

        value = point[key]
        point_vars = value
        depths = copy.deepcopy(key)
        layer = depths

        # sort the list
        point_vars.sort()

        for vv, source in point_vars:


            variable = vv

            out = f"{session_info['out_dir']}/oceanval_matchups/point/{layer}/{variable}/{source}/{source}_{layer}_{variable}.csv"
            csv = out

            if os.path.exists(out) and not selection.picks(overwrite, variable, source):
                if time_stamps.stored_time_shift(
                    os.path.join(os.path.dirname(out), "matchup_dict.pkl")
                ) == session_info["time_shift"]:
                    # made already, and still in the report
                    told.made("point", vv, source, layer)
                    continue
                print(
                    f"The {layer} {vv} ({source}) matchup was made with the model's times "
                    "shifted differently, so it is made again"
                )
            told.matching("point", vv, source, layer)

            all_df = df_mapping
            # not a query: it cannot see good_model_vars from in here
            all_df = all_df[all_df.model_variable.isin(good_model_vars)].reset_index(
                drop=True
            )

            all_df = all_df.dropna()
            all_df = all_df.query("variable == @vv").reset_index(drop=True)
            patterns = list(set(all_df.pattern))

            for pattern in patterns:
                final_extension = extension_of_directory(sim_dir)
                ensemble = _stream_files(
                    glob.glob(sim_dir + final_extension + pattern),
                    example_files[pattern] if strict_names else None,
                    strict_names,
                    exclude,
                )

                df_times = []
                days = []
                for ff in ensemble:
                    df_ff = times_dict[ff]
                    df_times.append(
                        pd.DataFrame(
                            {
                                "month": df_ff.month,
                                "year": df_ff.year,
                                "day": df_ff.day,
                            }
                        ).assign(path=ff)
                    )
                df_times = pd.concat(df_times)

                # Idea: figure out if it is monthly or daily data
                # This might help speed things up

                sim_paths = list(set(df_times.path))
                sim_paths.sort()
                # write to the report

                min_year = df_times.year.min()
                max_year = df_times.year.max()
                session_info["min_year"] = min_year
                # factor in start
                session_info["min_year"] = max(session_info["min_year"], sim_start)
                session_info["max_year"] = max_year
                # factor in end
                session_info["max_year"] = min(session_info["max_year"], sim_end)

                def point_match(
                    variable, source, layer="all", ds_depths=None, df_times=None
                ):
                    with warnings.catch_warnings(record=True) as w:
                        point_variable = variable
                        model_variable = list(
                            all_df.query(
                                "variable == @point_variable"
                            ).model_variable
                        )[0]

                        comparison = definitions[variable].point_comparisons[source]

                        out = f"{session_info['out_dir']}/oceanval_matchups/point/{layer}/{variable}/{source}/{source}_{layer}_{variable}.csv"

                        # the observations, for the oceanval window's grid
                        told.observing("point", variable, source, layer)
                        if comparison["recipe"] is not None:
                            # recipes download the observations for the
                            # years and area being matched, instead of
                            # reading csvs from obs_path
                            if lon_lim is not None:
                                recipe_lon, recipe_lat = lon_lim, lat_lim
                            else:
                                recipe_lon, recipe_lat = _model_box(sim_paths[0], model_variable)
                            try:
                                df = ices.download_point_data(
                                    comparison["recipe"],
                                    max(session_info["min_year"], comparison["start"]),
                                    min(session_info["max_year"], comparison["end"]),
                                    recipe_lon,
                                    recipe_lat,
                                )
                            except Exception as error:
                                # the window says so at the bottom of its page
                                server_trouble.tell(
                                    error,
                                    ices.API_BASE,
                                    live.matchup_key("point", variable, source, layer),
                                    f"{variable} ({source}, {layer})",
                                )
                                raise
                        else:
                            paths = point_files(comparison["obs_path"])

                            # a file that was named is always used
                            if not os.path.isfile(comparison["obs_path"]):
                                for exc in exclude:
                                    paths = [
                                        x
                                        for x in paths
                                        if f"{exc}" not in os.path.basename(x)
                                    ]

                            def read_csv_simyears(ff, layer = None):
                                df = read_point(ff)
                                # longitudes of 0 to 360 are made -180 to 180
                                if "lon" in df.columns:
                                    df = df.assign(
                                        lon=lambda x: x.lon.where(x.lon <= 180, x.lon - 360)
                                    )
                                min_year = session_info["min_year"]
                                max_year = session_info["max_year"]
                                if "year" in df.columns:
                                    df = df.query(
                                        "year >= @min_year and year <= @max_year"
                                    ).reset_index(drop=True)
                                if layer == "surface":
                                    if "depth" in df.columns:
                                        df = df.query("depth <= 5").reset_index(
                                            drop=True
                                        )
                                        # drop depth
                                return df

                            df = pd.concat([read_csv_simyears(x, layer) for x in paths])
                        # ensure year is int
                        if "year" in df.columns:
                            df = df.assign(year=lambda x: x.year.astype(int))
                        if "year" in df.columns:
                            # find point_start
                            point_start = comparison["start"]
                            point_end = comparison["end"]
                            df = df.query(
                                "year >= @point_start and year <= @point_end"
                            ).reset_index(drop=True)

                        if "source" in df.columns:
                            df = df.drop(columns=["source"])
                        # if it exists, coerce year to int
                        if "year" in df.columns:
                            df = df.assign(year=lambda x: x.year.astype(int))
                            # subset to
                        if "month" in df.columns:
                            df = df.assign(month=lambda x: x.month.astype(int))
                        if "day" in df.columns:
                            df = df.assign(day=lambda x: x.day.astype(int))
                        if layer == "surface":
                            if "depth" in df.columns:
                                df = df.query("depth <= 5").reset_index(drop=True)
                                # drop depth
                                df = df.drop(columns=["depth"])
                        told.observed("point", variable, source, layer)

                        # a comparison can set its own point_time_res, in
                        # place of the one given to matchup
                        point_time_res = copy.deepcopy(
                            comparison.get("point_time_res")
                            or session_info["point_time_res"]
                        )
                        for x in [
                            x
                            for x in ["year", "month", "day"]
                            if x not in point_time_res
                        ]:
                            if x in df.columns:
                                df = df.drop(columns=x)

                        # observations without a year would otherwise be
                        # matched with every year of the simulation, so only
                        # keep model times in the years asked for. Each
                        # time's position in its file is kept for subsetting
                        year_start = max(session_info["min_year"], comparison["start"])
                        year_end = min(session_info["max_year"], comparison["end"])
                        df_times = df_times.reset_index(drop=True)
                        df_times["time_index"] = df_times.groupby("path").cumcount()
                        df_times = df_times.query(
                            "year >= @year_start and year <= @year_end"
                        ).reset_index(drop=True)

                        sel_these = point_time_res
                        sel_these = [x for x in df.columns if x in sel_these]
                        if "year" in df.columns:
                            paths = list(
                                set(
                                    df.loc[:, sel_these]
                                    .drop_duplicates()
                                    .merge(df_times)
                                    .path
                                )
                            )
                        else:
                            paths = list(set(df_times.path))

                        if len(paths) == 0:
                            print(f"No matching times for {variable}")

                        manager = _manager()

                        df_times_new = copy.deepcopy(df_times)

                        if fvcom:
                            # match against regridded copies of the FVCOM files.
                            # They keep the same times, so time_index still applies
                            fvcom_paths = fvcom_matchup_files(
                                paths,
                                model_variable.split("+"),
                                session_info["fvcom_dir"],
                                vertical=(layer == "all"),
                                res=session_info["fvcom_res"],
                                lon_lim=lon_lim,
                                lat_lim=lat_lim,
                                cores=cores,
                                levels=session_info["fvcom_levels"],
                            )
                            paths = [fvcom_paths[x] for x in paths]
                            df_times_new["path"] = [
                                fvcom_paths.get(x, x) for x in df_times_new.path
                            ]


                        valid_cols = [
                            "lon",
                            "lat",
                            "day",
                            "month",
                            "year",
                            "depth",
                            "observation",
                        ]
                        select_these = [x for x in df.columns if x in valid_cols]

                        if len(df) == 0:
                            print("No data for this variable")
                            return None

                        if layer == "all" and "depth" in df.columns and len(paths) > 0:
                            top = _top_cell_depths(df, ds_depths, paths[0], model_variable)
                            if top is not None:
                                df = _match_above_top_cell(df, top)

                        if "year" not in df.columns:
                            try:
                                point_time_res.remove("year")
                            except:
                                pass
                        if "month" not in df.columns:
                            try:
                                point_time_res.remove("month")
                            except:
                                pass
                        if "day" not in df.columns:
                            try:
                                point_time_res.remove("day")
                            except:
                                pass

                        df_all = manager.list()

                        # whether the surface can be read from the first level alone (once for the variable,
                        # from the first file; not FVCOM's, which are on another vertical axis), and with
                        # a small chunk cache
                        surface_fast, small_cache = (
                            _surface_is_first_level(paths[0], model_variable)
                            if layer == "surface" and not fvcom and len(paths) > 0
                            else (False, False)
                        )

                        grid_setup = False

                        pbar = tqdm(total=len(paths), position=0, leave=True)
                        results = dict()
                        # the files read, for the oceanval window's grid
                        told.files("point", variable, source, len(paths), 0, layer)

                        if cores > 1:
                            nc.options(parallel = True)
                            # made before the workers are forked, so they share it
                            _session_warnings()
                            pool = mp.Pool(cores)
                            try:
                                for ff in paths:

                                    temp = pool.apply_async(
                                        mm_match,
                                        [
                                            ff,
                                            model_variable,
                                            df,
                                            df_times_new,
                                            ds_depths,
                                            point_variable,
                                            df_all,
                                            layer,
                                            surface_fast,
                                            small_cache,
                                        ],
                                    )

                                    results[ff] = temp

                                for k, v in results.items():
                                    value = v.get()
                                    pbar.update(1)
                                    told.files("point", variable, source, len(paths), pbar.n, layer)
                            finally:
                                # closed and joined, never terminated: the
                                # workers inherit nctoolkit's SIGTERM handler,
                                # which cleans up rather than stopping them,
                                # so terminating them would wait for ever
                                pool.close()
                                pool.join()
                                nc.options(parallel = False)
                        else:
                            for ff in paths:
                                value = mm_match(
                                    ff,
                                    model_variable,
                                    df,
                                    df_times_new,
                                    ds_depths,
                                    point_variable,
                                    df_all,
                                    layer,
                                    surface_fast,
                                    small_cache,
                                )
                                pbar.update(1)
                                told.files("point", variable, source, len(paths), pbar.n, layer)
                        told.files("point", variable, source, len(paths), len(paths), layer, finished=True)

                        df_all = list(df_all)
                        manager.shutdown()
                        df_all = [x for x in df_all if x is not None]
                        # do nothing when there is no data
                        if len(df_all) == 0:
                            print(f"No data for {variable}")
                            time.sleep(1)
                            return False

                        df_all = pd.concat(df_all)

                        change_this = [
                            x
                            for x in df_all.columns
                            if x
                            not in [
                                "lon",
                                "lat",
                                "year",
                                "month",
                                "day",
                                "depth",
                                "observation",
                            ]
                        ][0]
                        #
                        df_all = df_all.rename(
                            columns={change_this: "model"}
                        ).merge(df)
                        # observations above the top cell were matched below
                        # it, so put back the depth they were observed at
                        if "obs_depth" in df_all.columns:
                            df_all = df_all.assign(depth=df_all.obs_depth).drop(
                                columns="obs_depth"
                            )
                        # add model to name column names with frac in them
                        df_all = df_all.dropna().reset_index(drop=True)
                        # fix the observations based on obs_unit_multiplier
                        multiplier = comparison["obs_multiplier"]
                        if multiplier != 1:
                            df_all = df_all.assign(
                                observation=lambda x: x.observation * multiplier
                            )
                        adder = comparison["obs_adder"]
                        if adder != 0:
                            df_all = df_all.assign(
                                observation=lambda x: x.observation + adder
                            )

                        grouping = copy.deepcopy(point_time_res)
                        grouping.append("lon")
                        grouping.append("lat")
                        grouping.append("depth")
                        grouping = [x for x in grouping if x in df_all.columns]
                        grouping = list(set(grouping))
                        df_all = df_all.dropna().reset_index(drop=True)
                        df_all = df_all.groupby(grouping).mean().reset_index()


                        # create directory for out if it does not exists
                        if not os.path.exists(os.path.dirname(out)):
                            os.makedirs(os.path.dirname(out))
                        if lon_lim is not None:
                            df_all = df_all.query(
                                f"lon > {lon_lim[0]} and lon < {lon_lim[1]}"
                            )
                        if lat_lim is not None:
                            df_all = df_all.query(
                                f"lat > {lat_lim[0]} and lat < {lat_lim[1]}"
                            )
                        # only those in the domain mask's cells of 1
                        df_all = domain_mask_mod.mask_points(df_all, session_info["domain_mask"])

                        if len(df_all) > 0:

                            if "year" not in point_time_res:
                                try:
                                    df_all = df_all.drop(columns="year")
                                except:
                                    pass
                            if "day" not in point_time_res:
                                try:
                                    df_all = df_all.drop(columns="day")
                                except:
                                    pass
                            if "month" not in point_time_res:
                                try:
                                    df_all = df_all.drop(columns="month")
                                except:
                                    pass
                            # special handling of temperature
                            if variable == "temperature":
                                max_model = df_all.model.max()
                                max_obs = df_all.observation.max()
                                if max_model > 100 and max_obs < 100:
                                    df_all = df_all.assign(
                                        observation=lambda x: x.observation + 273.15
                                    )   
                                if max_obs > 100 and max_model < 100:
                                    df_all = df_all.assign(
                                        observation=lambda x: x.observation - 273.15
                                    )

                            df_all.to_csv(out, index=False)
                            # save the definitions
                            out_definitions = out.replace(
                                ".csv", "_definitions.pkl"
                            )
                            import dill
                            # get the model unit
                            ds = nc.open_data(paths[0], checks=False)
                            the_variable = model_variable.split("+")[0]
                            model_unit = list(
                                ds.contents.query(
                                    "variable == @the_variable"
                                ).unit
                            )[0]
                            definitions[variable].model_unit = model_unit
                            dill.dump( definitions, file=open(out_definitions, "wb"))

                            out1 = out.replace(
                                os.path.basename(out), "matchup_dict.pkl"
                            )
                            # read in the adhoc dict in mm_match

                            point_start = -5000
                            point_end = 10000
                            try:
                                point_start = comparison["start"]
                                point_end = comparison["end"]
                            except:
                                pass

                            min_year = session_info["min_year"]
                            max_year = session_info["max_year"]

                            if point_start > min_year:
                                min_year = point_start
                            if point_end < max_year:
                                max_year = point_end

                            the_dict = {
                                "start": min_year,
                                "end": max_year,
                                "point_time_res": point_time_res,
                                "model_variable": model_variable,
                                "time_shift": session_info["time_shift"],
                            }
                            # remove the adhoc dict
                            # write to pickle
                            with open(out1, "wb") as f:
                                pickle.dump(the_dict, f)

                            return None
                        else:
                            print(f"No data for {variable}")
                            time.sleep(1)
                            return False

                vv_variable = definitions[vv].long_name

                out = glob.glob(
                    session_info["out_dir"]
                    + "/"
                    + f"oceanval_matchups/point/{key}/{vv}/{source}/{source}_{key}_{vv}.csv"
                )

                if len(out) > 0:
                    # unless it was made with the model's times shifted
                    # differently, said above
                    if not selection.picks(session_info["overwrite"], vv, source) and time_stamps.stored_time_shift(
                        out[0].replace(os.path.basename(out[0]), "matchup_dict.pkl")
                    ) == session_info["time_shift"]:
                        continue

                print(
                    f"Matching up model output of {key} {vv_variable} with {source} in-situ observational data"
                )

                # try:
                if True:
                    point_match(
                        vv, source, ds_depths=ds_depths, df_times=df_times, layer=key
                    )
                # except:
                #     pass

                output_warnings = []
                for ww in _session_warnings():
                    if ww is not None:
                        if ww in output_warnings:
                            continue
                        if "CDO found more than one time variable" in ww:
                            continue
                        if "coordinates variable time" in ww:
                            continue
                        output_warnings.append(str(ww))

                if len(output_warnings) > 0:
                    output_warnings = list(set(output_warnings))
                    print(f"Warnings for {vv_variable}")
                    for ww in output_warnings:
                        warnings.warn(message=ww)
                # empty session warnings

            if os.path.exists(csv):
                told.made("point", vv, source, layer)
            else:
                told.not_made("point", vv, source, layer)
        while session_warnings is not None and len(session_warnings) > 0:
            session_warnings.pop()

    gridded_args = dict(
        df_mapping=df_mapping,
        folder=sim_dir,
        exclude=exclude,
        sim_start=sim_start,
        sim_end=sim_end,
        lon_lim=lon_lim,
        lat_lim=lat_lim,
        times_dict=times_dict,
        example_files=example_files,
        on_matched=told.made,
        on_matching=told.matching,
        on_failed=told.not_made,
        reading=told.reading,
        on_observing=told.observing,
        on_observed=told.observed,
    )

    # the quick matchups first, so there are results to look at early: the
    # non-vertical ones, gridded then point, and then the vertical ones
    gridded_matchup(var_choice=gridded_surface, **gridded_args)
    match_point_layer("surface")
    gridded_matchup(var_choice=gridded_vertical, reset_failures=False, **gridded_args)
    match_point_layer("all")

    retry_failed_gridded(ask=ask, **gridded_args)
    # anything not made by now is not made
    prompts.tell({"kind": "matchups_finished"})

    if len(session_info["end_messages"]) > 0:
        print("########################################")
        print("########################################")
        print("Important messages about matchups:")
        print("*" * 30)
        # write this info to a md report

        for x in session_info["end_messages"]:
            print(x)
        print("########################################")
        print("########################################")

    # store definitions as a pickle
    ff = session_info["out_dir"] + "oceanval_matchups/definitions.pkl"
    if os.path.exists(ff):
        os.remove(ff)
    import dill

    dill.dump(definitions, file=open(ff, "wb"))

    # output short titles
    ff = session_info["out_dir"] + "oceanval_matchups/short_titles.pkl"
    short_titles = session_info["short_title"]
    with open(ff, "wb") as f:
        pickle.dump(short_titles, f)
    
    # now add to the list of variables matched up
    ff = session_info["out_dir"] + "oceanval_matchups/variables_matched.pkl"
    if os.path.exists(ff):
        with open(ff, "rb") as f:
            variables_matched = pickle.load(f)
    else:
        variables_matched = []
    for vv in list(df_mapping.variable):
        if vv not in variables_matched:
            variables_matched.append(vv)
    with open(ff, "wb") as f:
        pickle.dump(variables_matched, f)

    # which of the matchups here this run made or kept, for validate(previous=False)
    selection.save_last_run(session_info["out_dir"] + "oceanval_matchups", told.made_keys)

    if fvcom:
        _remove_fvcom_dir(session_info["fvcom_dir"])
        session_info["fvcom_files"] = dict()
        session_info["fvcom"] = False

    if builder is not None:
        builder.finish()


