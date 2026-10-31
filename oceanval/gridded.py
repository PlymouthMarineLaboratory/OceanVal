import calendar
import contextlib
import glob
import copy
import datetime
import math
import os
import re
import traceback
import warnings
import pickle
import pandas as pd
import time
import numpy as np
from oceanval.quiet_nc import nc
import xarray as xr

from oceanval import data_store
from oceanval import extent
from oceanval import live
from oceanval import prompts
from oceanval import server_trouble
from oceanval import surface
from oceanval import time_res
from oceanval import domain_mask
from oceanval.fixers import tidy_warnings
from oceanval.fvcom import fvcom_matchup_files
from oceanval.utils import extension_of_directory, loud_warning
from oceanval.selection import picks
from oceanval.session import session_info
from oceanval.time_stamps import stored_time_shift
from oceanval.transects import wraps_round
from oceanval.parsers import Validator, definitions

# the grid, in degrees, that a model and observations spanning more than 90
# degrees are both put on
COMMON_RES = 0.5


def _failure_log():
    """Path of the log listing gridded variables that could not be matched up."""
    return session_info.get("out_dir", "") + "oceanval_matchups/matchup_failures.log"


def _is_url(path):
    return isinstance(path, str) and path.startswith(("http://", "https://", "ftp://"))


def _open_obs(path, thredds):
    """Open observational data: over thredds, downloaded from a url, or local."""
    if thredds:
        return nc.open_thredds(path, checks=False)
    if _is_url(path):
        # a plain file served over http, e.g. GLODAP: downloaded to a temp file
        return nc.open_url(path)
    return nc.open_data(path, checks=False)


def _year_months(times):
    """(year, month) of each time step, in order, whether the times are datetimes or strings."""
    try:
        return [(x.year, x.month) for x in times]
    except AttributeError:
        return [
            (int(str(x).split("T")[0].split("-")[0]), int(str(x).split("T")[0].split("-")[1]))
            for x in times
        ]


def common_times(model_times, obs_times):
    """The time steps of the model and of the observations whose year and month the other lacks.

    Returns (model indices, observation indices, the (year, month)s of the observations
    with no model match, those of the model with no observation match).
    """
    model_pairs = _year_months(model_times)
    obs_pairs = _year_months(obs_times)
    model_set = set(model_pairs)
    obs_set = set(obs_pairs)
    model_drop = [i for i, x in enumerate(model_pairs) if x not in obs_set]
    obs_drop = [i for i, x in enumerate(obs_pairs) if x not in model_set]
    return (
        model_drop,
        obs_drop,
        sorted({obs_pairs[i] for i in obs_drop}),
        sorted({model_pairs[i] for i in model_drop}),
    )


def describe_months(pairs):
    """Year/months condensed by year for a message, e.g. "2015 (May-Dec), 2016 (Jan, Mar)"."""
    by_year = {}
    for year, month in sorted(pairs):
        by_year.setdefault(year, []).append(month)
    out = []
    for year, months in by_year.items():
        if len(months) == 12:
            out.append(str(year))
            continue
        runs = []
        for month in months:
            if runs and month == runs[-1][1] + 1:
                runs[-1][1] = month
            else:
                runs.append([month, month])
        names = [
            calendar.month_abbr[a] if a == b else f"{calendar.month_abbr[a]}-{calendar.month_abbr[b]}"
            for a, b in runs
        ]
        out.append(f"{year} ({', '.join(names)})")
    return ", ".join(out)


def complete_years(times, months):
    """Split the years of a dataset's time steps by whether they have every month in `months`.

    Returns ({year: how many of the months it has} for the years that do not,
    [the years that do]). A year without every month cannot give a correct annual average.
    """
    pairs = set(_year_months(times))
    months = set(months)
    found = {}
    for year, month in pairs:
        if month in months:
            found[year] = found.get(year, 0) + 1
    years = sorted({year for year, _ in pairs})
    complete = [y for y in years if found.get(y, 0) == len(months)]
    incomplete = {y: found.get(y, 0) for y in years if y not in complete}
    return incomplete, complete


def keep_complete_years(ds, months=range(1, 13)):
    """Drop the years of a matched up dataset that lack some of `months`, so annual means are right.

    Does nothing for a dataset of one year or less (a climatology), or when every year is complete.
    """
    times = ds.times
    if len(times) <= 12:
        return
    incomplete, _ = complete_years(times, months)
    if len(incomplete) > 0:
        ds.drop(year=list(incomplete))
        ds.run()


def _occci_year_month(path):
    """(year, month) of an OCCCI file, read from its path rather than its contents.

    The file name carries both, e.g. ...-201807-fv6.0.nc. If only the year
    directory is found the month is None, and if there is no year at all
    the result is (None, None).
    """
    found = re.search(r"-(\d{4})(\d{2})-fv", path)
    if found:
        return int(found.group(1)), int(found.group(2))
    found = re.search(r"/(\d{4})/", path)
    if found:
        return int(found.group(1)), None
    return None, None


def _filter_occci_paths(paths, years, months):
    """The OCCCI paths that are for one of the years and months wanted.

    Opening every monthly file over thredds only to subset it afterwards is
    slow, so the years and months are taken from the paths instead. A path
    whose year cannot be read is kept, so it is left to be subset once opened.
    """
    keep = []
    for path in paths:
        year, month = _occci_year_month(path)
        if year is None:
            keep.append(path)
        elif year in years and (month is None or month in months):
            keep.append(path)
    return keep


def remote_gridded_source(vv, source):
    """Whether a variable's observational data from source is read over the network.

    Only these are worth skipping past and retrying: a local file that
    errors is a problem with the data or the settings, which retrying will
    not fix, so those are left to raise as they always have.
    """
    comparison = definitions[vv].gridded_comparisons[source]
    if comparison["thredds"]:
        return True
    obs_path = comparison["obs_path"]
    if isinstance(obs_path, str):
        return obs_path.startswith(("http://", "https://"))
    if isinstance(obs_path, (list, tuple)):
        return any(str(x).startswith(("http://", "https://")) for x in obs_path)
    return False


def record_gridded_failure(vv, source, error):
    """Log a failed remote matchup, say so loudly, and keep it for the retry."""
    vv_name = definitions[vv].long_name or vv
    session_info["failed_gridded"].append(
        {
            "variable": vv,
            "source": source,
            "error": f"{type(error).__name__}: {error}",
        }
    )

    log_file = _failure_log()
    try:
        if not os.path.exists(os.path.dirname(log_file)):
            os.makedirs(os.path.dirname(log_file))
        with open(log_file, "a") as log:
            log.write("=" * 78 + "\n")
            log.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {vv} ({source})\n")
            log.write("=" * 78 + "\n")
            log.write("".join(traceback.format_exception(type(error), error, error.__traceback__)))
            log.write("\n")
    except Exception:
        # a failure to write the log must not take the matchup down with it
        log_file = None

    details = [
        f"Variable:     {vv}",
        f"Observations: {source}",
        "",
        f"{type(error).__name__}: {error}",
        "",
        "This observational data is read over the network, so this is usually",
        "a connection problem rather than a problem with your model output.",
        "Moving on to the next variable.",
    ]
    # the server's own trouble is told to the window straight away, which
    # says so at the bottom of its page
    obs_path = definitions[vv].gridded_comparisons[source]["obs_path"]
    answer = server_trouble.tell(
        error, obs_path, live.matchup_key("gridded", vv, source), f"{vv} ({source})"
    )
    if answer is not None:
        details[5:5] = server_trouble.explain(answer, server_trouble.host_of(obs_path)) + [""]
    if log_file is not None:
        details += ["", f"Logged to {log_file}"]

    loud_warning(
        f"COULD NOT MATCH UP {str(vv_name).upper()} ({str(source).upper()})",
        details,
        warning=f"Could not match up gridded {vv} ({source}): {error}",
    )



def _lonlat_bounds(ds):
    """(lon_min, lon_max, lat_min, lat_max) of a dataset's cells with values
    (at its first time and top level, see oceanval.extent), or of its whole
    grid if they cannot be told.

    The whole grid can be far larger than the model's domain: NEMO puts the
    cells of the land processors it leaves out at lon -1, lat -1. Only one
    slice of the field is read, as this is only used to size a target grid.
    """
    ds_xr = ds.to_xarray()
    found = extent.valid_lonlat(ds_xr)
    if found is not None:
        lons, lats = found
    else:
        lon_name = [x for x in ds_xr.coords if "lon" in x][0]
        lat_name = [x for x in ds_xr.coords if "lat" in x][0]
        lons = ds_xr[lon_name].values
        lats = ds_xr[lat_name].values
    # handle lon > 180 properly, same convention used elsewhere in this module
    lons = ((lons + 180) % 360) - 180
    return float(np.nanmin(lons)), float(np.nanmax(lons)), float(np.nanmin(lats)), float(np.nanmax(lats))


def _obs_resolution(comparison, downloaded=None):
    """[lon, lat] grid spacing of a gridded observational source, in degrees.

    Only the coordinates of the first file are read. downloaded is the
    DataSet of an observational file already fetched over http, which is read
    in place of downloading it again.
    """
    obs_path = comparison["obs_path"]
    if isinstance(obs_path, (list, tuple)):
        obs_path = obs_path[0]
    if not comparison["thredds"] and not obs_path.endswith(".nc"):
        obs_path = nc.create_ensemble(obs_path)[0]
    if not comparison["thredds"] and _is_url(obs_path):
        # xarray cannot read a plain file over http, so it has to be
        # downloaded. nctoolkit deletes a download once no DataSet holds it,
        # so the DataSet must outlive the read below
        if downloaded is None:
            downloaded = _open_obs(obs_path, False)
        obs_path = downloaded[0]
    with xr.open_dataset(obs_path, decode_times=False) as ds:
        names = list(ds.coords) + [x for x in ds.variables if x not in ds.coords]
        lon_name = [x for x in names if "lon" in x][0]
        lat_name = [x for x in names if "lat" in x][0]
        lons = ds[lon_name].values
        lats = ds[lat_name].values
    # works for 1D coordinates and 2D (lat, lon) ones
    lon_res = float(np.nanmedian(np.abs(np.diff(lons, axis=-1))))
    lat_res = float(np.nanmedian(np.abs(np.diff(lats, axis=0))))
    return [lon_res, lat_res]


def _regrid_onto_common_grid(ds_model, ds_obs):
    """Put the model onto the observations' grid, ready for comparison.

    ds_obs is first cropped to the model's own extent, so a global product
    (WOA23, GLODAP, COBE2...) validated against a regional model is judged
    on the region actually being validated, not its full global footprint.

    Bilinear-regridding the model directly onto the observations' native
    grid works fine once that crop is applied, but for a high resolution
    observational product spanning a large area (a satellite product over
    most of the globe, say) it still means building interpolation weights
    across a huge, fine target grid - slow, and memory-hungry. If the
    (cropped) observations still span more than 90 degrees of longitude or
    latitude, both datasets are instead put on a regular 0.5x0.5 degree
    grid covering that extent, which is far cheaper and plenty fine enough
    for validation purposes.
    """
    model_lon_min, model_lon_max, model_lat_min, model_lat_max = _lonlat_bounds(ds_model)
    ds_xr = ds_model.to_xarray()
    if wraps_round(ds_xr[[x for x in ds_xr.coords if "lon" in x][0]].values):
        # a global model's outermost centres would cut the obs' edge columns
        model_lon_min, model_lon_max = -180, 180
    ds_obs.crop(lon=[model_lon_min, model_lon_max], lat=[model_lat_min, model_lat_max])

    lon_min, lon_max, lat_min, lat_max = _lonlat_bounds(ds_obs)
    if (lon_max - lon_min) > 90 or (lat_max - lat_min) > 90:
        lons = [lon_min, lon_max]
        ds_xr = ds_obs.to_xarray()
        lon_name = [x for x in ds_xr.coords if "lon" in x][0]
        if wraps_round(ds_xr[lon_name].values):
            # all the way round the globe, or there is a gap at the date line
            # that nothing can be interpolated across (a 1 degree product's
            # centres, -179.5 to 179.5, gave 719 cells and a transect at 180
            # found nothing): 720 cells, on the same lattice as before
            west = lon_min - COMMON_RES * math.floor((lon_min + 180) / COMMON_RES + 1e-9)
            lons = [west, west + 360 - COMMON_RES]
        ds_model.to_latlon(lon=lons, lat=[lat_min, lat_max], res=COMMON_RES)
        ds_obs.to_latlon(lon=lons, lat=[lat_min, lat_max], res=COMMON_RES)
    else:
        ds_model.regrid(ds_obs, method="bil")


def _crop_to_model_data(ds):
    """Crop ds to the lon/lat extent that actually has non-missing model
    values - the model grid's own coordinates can span a wider box than the
    area it has data for. A grid all the way round the globe keeps all its
    longitudes, as dropping an empty column would leave a gap at the date line."""
    df_test = ds.to_dataframe().reset_index()
    lon_name = [x for x in df_test.columns if "lon" in x][0]
    lat_name = [x for x in df_test.columns if "lat" in x][0]
    wraps = wraps_round(df_test[lon_name].values)
    df_test = df_test[df_test["model"].notna()]
    lons = ((df_test[lon_name].values + 180) % 360) - 180
    lon_min, lon_max = lons.min(), lons.max()
    if wraps:
        lon_min, lon_max = -180, 180
    lat_min, lat_max = df_test[lat_name].min(), df_test[lat_name].max()
    ds.subset(lon=[lon_min, lon_max], lat=[lat_min, lat_max])


def _levels_of(path, names):
    """{variable: its number of vertical levels} in the file path, from its
    metadata, or None if that cannot be read."""
    try:
        with warnings.catch_warnings(record=True):
            contents = nc.open_data(path, checks=False).contents
        return {name: int(contents.query("variable == @name").nlevels.iloc[0]) for name in names}
    except Exception:
        return None


def model_dataset(
    paths, selection, sim_years, time_shift, as_missing, surface_only, fvcom=False, month_per_file=False
):
    """The model's output in paths of the variables selection in sim_years, as
    a dataset whose chain has been asked for but not run, whether the surface
    is read from the first level only, and whether CDO is given a small chunk
    cache while it is (see oceanval.surface). With
    surface_only, a variable with levels is taken at the surface, as a
    matchup does, before the missing value is set. With month_per_file (see
    time_res.one_month_per_file), each file's mean, which is its month's, is
    taken last.

    The chain is the cutting down first, as the files can be many GB: the
    years, the variables and the surface, and only then the shift (which the
    files of the years need) and the missing value."""
    ds_model = nc.open_data(paths, checks=False)
    levels = _levels_of(paths[0], selection) if surface_only else None
    if surface_only and levels is None:
        # as it was worked out before: from the variables of the first file, selected
        ds_zz = nc.open_data(paths[0], checks=False)
        ds_zz.subset(variables=selection)
        ds_zz.run()
        levels = {selection[0]: int(ds_zz.contents.nlevels.values[0])}
    more_than_one_level = surface_only and levels.get(selection[0], 1) > 1
    fast = small_cache = False
    if more_than_one_level and not fvcom:
        fast, why = surface.first_level_is_surface(paths[0], selection, levels)
        small_cache = fast and surface.small_cache_helps(paths[0], selection, levels)
        print(f"{'+'.join(selection)}: {surface.how_read(fast, why, small_cache)}")
    # times that mark the end of the period each mean covers
    # (see oceanval.time_stamps) are shifted before the years
    # are selected, so the year's last mean is in its year
    if not month_per_file and not time_shift:
        ds_model.subset(years=sim_years)
    surface.choose(ds_model, selection, fast)
    if more_than_one_level and not fast:
        ds_model.cdo_command("topvalue")
    if time_shift:
        ds_model.shift(days=time_shift)
        # not subset(years=...), which leaves out the files
        # without those years on disk, before they are
        # shifted: the file of the year's last mean, dated in
        # the next. The files were chosen by their shifted times
        ds_model.cdo_command(f"-selyear,{','.join(str(x) for x in sim_years)}")
    if as_missing is not None:
        ds_model.as_missing(as_missing)
    if month_per_file:
        # the month's mean, dated at its first time as the mean of each month is
        ds_model.tmean(align="left")
    return ds_model, fast, small_cache


def gridded_matchup(
    df_mapping=None,
    folder=None,
    var_choice=None,
    exclude=None,
    sim_start=None,
    sim_end=None,
    lon_lim=None,
    lat_lim=None,
    times_dict=None,
    example_files = {},
    on_matched=None,
    reset_failures=True,
    on_matching=None,
    on_failed=None,
    reading=None,
    on_observing=None,
    on_observed=None,
):
    """
    Function to create gridded matchups for a given set of variables

    Parameters
    ----------
    df_mapping : pandas.DataFrame
        DataFrame containing the mapping between model variables and gridded observations
    folder : str
        Path to folder containing model data
    var_choice : list
        List of (variable, source) pairs to create matchups for
    exclude : list
        List of strings to exclude from the file search
    sim_start : int
        Start year for model simulations
    sim_end : int
        End year for model simulations
    lon_lim : list
        Longitude limits for subsetting
    lat_lim : list
        Latitude limits for subsetting
    times_dict : dict
        Dictionary with file paths as keys and corresponding time DataFrames as values
    on_matched : callable or None
        Called with ("gridded", variable, source) for each matchup made, or
        kept from an earlier run, as for the interim report (see oceanval.live)
    reset_failures : bool
        Whether to start a fresh list of failures. matchup makes the surface and
        vertical matchups in separate runs and keeps the failures of both.
    on_matching, on_failed : callable or None
        Called as on_matched is, as each matchup is started, and when one fails
        in a way a retry might fix, for the oceanval window's grid of them
    reading : callable or None
        reading("gridded", variable, source, paths) is the context the model
        files of each matchup, paths, are read in, which tells the window how
        many have been (see oceanval.cdo_watch)
    on_observing, on_observed : callable or None
        Called as on_matched is, as each matchup's observations start to be
        read (or downloaded), and once they have been, for the window's grid
    Returns
    -------
    None

    """

    start_cores = session_info["cores"]

    # each run reports its own failures - retry_failed_gridded reads the
    # names it needs out of here before calling back in
    if reset_failures:
        session_info["failed_gridded"] = []

    all_df = df_mapping
    # if model_variable is None remove from all_df
    good_model_vars = [x for x in all_df.model_variable if x is not None]

    all_df = all_df.query("model_variable in @good_model_vars").reset_index(drop=True)

    all_df = all_df.dropna()

    # every source of a variable is matched up separately
    vars = []
    for x in definitions.keys:
        for source in definitions[x].gridded_comparisons:
            vars.append((x, source))

    vars = [x for x in vars if x in var_choice]
    vars.sort()

    if len(vars) > 0:
        # first up, do the top

        mapping = dict()

        for vv, vv_source in vars:
            # nc.open_thredds, which opens the observations of the previous
            # variable, sets nctoolkit's cores to 1 for the rest of the
            # session, and the model files of this one are read with them
            nc.options(cores=start_cores)
            # only a variable whose observations come over the network can
            # fail in a way a later retry might fix - anything else still
            # raises, so a genuine data or settings problem is not buried
            retryable = remote_gridded_source(vv, vv_source)
            try:
                comparison = definitions[vv].gridded_comparisons[vv_source]
                vertical_gridded = comparison["vertical"]
                climatology = comparison["climatology"]
                    # if global is not selected, stop
                # a dictionary for summarizing things
                var_dict = {}
                out_dir = session_info["out_dir"]
                out = glob.glob(
                    out_dir + f"oceanval_matchups/gridded/{vv}/{vv_source}_{vv}_surface.nc"
                )
                if len(out) > 0:
                    if not picks(session_info["overwrite"], vv, vv_source):
                        if stored_time_shift(
                            out[0].replace(os.path.basename(out[0]), f"{vv_source}_matchup_dict.pkl")
                        ) == session_info.get("time_shift", 0):
                            if on_matched is not None:
                                on_matched("gridded", vv, vv_source)
                            continue
                        print(
                            f"The {vv} ({vv_source}) matchup was made with the model's times shifted "
                            "differently, so it is made again"
                        )
                if on_matching is not None:
                    on_matching("gridded", vv, vv_source)
                # figure out the data source
                #
                # check if this directory is empty
                dir_var = comparison["obs_path"]

                #
                vv_name = definitions[vv].long_name

                print(
                    f"Matching up surface {vv_name} with {vv_source.upper()} gridded data"
                )
                print("**********************")
                df = df_mapping.query("variable == @vv").reset_index(drop=True)

                mapping[vv] = list(df.query("variable == @vv").model_variable)[0]

                selection = mapping[vv].split("+")

                patterns = set(df.pattern)
                pattern = list(patterns)[0]

                final_extension = extension_of_directory(folder)
                paths = glob.glob(folder + final_extension + pattern)
                if session_info["strict_names"]:
                    len_example = len(os.path.basename(example_files[pattern]))
                    paths = [x for x in paths if len(os.path.basename(x)) == len_example]

                for exc in exclude:
                    paths = [
                        x for x in paths if f"{exc}" not in os.path.basename(x)
                    ]
            
                # handle required
                if session_info["require"] is not None:
                    for req in session_info["require"]:
                        paths = [x for x in paths if f"{req}" in os.path.basename(x)]


                all_years = []
                for ff in paths:
                    all_years += list(times_dict[ff].year)

                all_years = list(set(all_years))
                n_years = len(all_years)

                sim_years = range(sim_start, sim_end + 1)
                sim_years = [x for x in all_years if x in sim_years]
                min_year = min(sim_years)
                max_year = max(sim_years)

                start = comparison["start"]
                end = comparison["end"]
                if start is not None:
                    sim_years = [x for x in sim_years if x >= start]
                if end is not None:
                    sim_years = [x for x in sim_years if x <= end]

                if len(sim_years) == 0:
                    # specific error for glodap
                    session_info["end_messages"] += [f"No simulation years found for {vv} ({vv_source}). Please check start and end args!"]
                    warnings.warn(f"No simulation years found for {vv} ({vv_source}). Please check start and end args!")
                    continue
                # now simplify paths, so that only the relevant years are used
                new_paths = []
                year_options = list(
                    set(
                        pd.concat([x for x in times_dict.values()])
                        .loc[:, ["year", "month"]]
                        .drop_duplicates()
                        .groupby("year")
                        .size()
                        # must be at least 12
                        .pipe(lambda x: x[x >= 12])
                        .reset_index()
                        .year
                    )
                )
                old_years = sim_years
                n_years = len(sim_years)
                month_sel = range(1, 13)
                # against a time series of observations a year with only some months is kept:
                # the years and months both have are matched up further down
                if climatology is True or len(year_options) == 0:
                    sim_years = [x for x in sim_years if x in year_options]
                if len(sim_years) == 0:
                    sim_years = old_years
                    month_sel = list(
                        set(
                            pd.concat([x for x in times_dict.values()])
                            .loc[:, ["year", "month"]]
                            .drop_duplicates()
                            .month
                        )
                    )

                for ff in paths:
                    if len([x for x in times_dict[ff].year if x in sim_years]) > 0:
                        new_paths.append(ff)

                paths = list(set(new_paths))
                paths.sort()

                # the times are those of the files on disk (shifted, if they are),
                # which FVCOM's regridded files below keep, one for each
                month_per_file = time_res.one_month_per_file({ff: times_dict[ff] for ff in paths})
                if month_per_file:
                    print(f"{'+'.join(selection)}: each file is one month, so its monthly means are taken as the files are read")

                obs_download = None
                if session_info.get("fvcom", False):
                    # regrid FVCOM output at twice the resolution of the observations
                    if _is_url(dir_var) and dir_var.endswith(".nc") and not comparison["thredds"]:
                        # downloaded once, for the resolution and the matchup
                        if data_store.active() is not None and data_store.keeps(comparison):
                            # a whole file, which the store keeps whole
                            obs_download = data_store.gridded(
                                comparison, dir_var, False, data_store.GLOBAL, "all",
                                label=f"{vv_source} {vv}",
                            )
                        if obs_download is None:
                            obs_download = _open_obs(dir_var, False)
                    fvcom_res = [
                        x / 2 for x in _obs_resolution(comparison, obs_download)
                    ]
                    fvcom_paths = fvcom_matchup_files(
                        paths,
                        selection,
                        session_info["fvcom_dir"],
                        vertical=vertical_gridded,
                        res=fvcom_res,
                        lon_lim=lon_lim,
                        lat_lim=lat_lim,
                        cores=session_info["cores"],
                        levels=session_info.get("fvcom_levels"),
                    )
                    paths = [fvcom_paths[x] for x in paths]

                # handle 

                var_dict["clim_years"] = [min(sim_years), max(sim_years)]

                # get the number of paths

                with warnings.catch_warnings(record=True) as w:

                    new_paths = copy.deepcopy(paths)

                    ds_model, _, small_cache = model_dataset(
                        paths,
                        selection,
                        sim_years,
                        session_info.get("time_shift"),
                        session_info["as_missing"],
                        surface_only=vertical_gridded is False,
                        fvcom=bool(session_info.get("fvcom", False)),
                        month_per_file=month_per_file,
                    )
                    # the first run of the chain: the model files are read here
                    with surface.chunk_cache(small_cache), (
                        reading("gridded", vv, vv_source, paths)
                        if reading is not None
                        else contextlib.nullcontext()
                    ):
                        ds_model.merge("time")
                    if not month_per_file:
                        ds_model.tmean(
                            ["year", "month"], align="left"
                        )

                    # the code below needs to be simplifed
                    # essentially anything with a + in the mapping should be split out
                    # and then the command should be run for each variable

                    var_unit = None
                    for vv in list(df.variable):
                        if "+" in mapping[vv]:
                            command = f"-aexpr,{vv}=" + mapping[vv]
                            ds_model.cdo_command(command)
                            drop_these = mapping[vv].split("+")
                            ds_contents = ds_model.contents
                            ds_contents = ds_contents.query(
                                "variable in @drop_these"
                            )
                            var_unit = ds_contents.unit[0]
                            ds_model.drop(variables=drop_these)

                            ds_model.run()
                            if var_unit is not None:
                                ds_model.set_units(
                                    {vv: var_unit}
                                )

                            ds_model.run()

                    # the monthly means on the domain mask's grid, only its cells of 1
                    domain_mask.mask_gridded(ds_model, session_info.get("domain_mask"), fvcom=session_info.get("fvcom", False))

                tidy_warnings(w)

                # figure out the start and end year
                with warnings.catch_warnings(record=True) as w:
                    start_year = min(ds_model.years)
                    end_year = max(ds_model.years)

                    # Read in the monthly observational data
                    if on_observing is not None:
                        on_observing("gridded", vv, vv_source)
                    thredds = comparison["thredds"]
                    if thredds == False:
                        if dir_var.endswith(".nc"):
                            vv_file = dir_var
                        else:
                            vv_file = nc.create_ensemble(dir_var)
                    else:
                        vv_file = dir_var  # thredds URL

                    # OCCCI is a file per month: pick the files from their paths
                    if vv_source == "OCCCI" and isinstance(vv_file, (list, tuple)):
                        vv_file = _filter_occci_paths(vv_file, sim_years, month_sel)
                        if len(vv_file) == 0:
                            raise ValueError("There do not appear to be any years in common between model and observation!  ")

                    if obs_download is not None:
                        ds_obs = obs_download.copy()
                    else:
                        ds_obs = None
                        if data_store.active() is not None and data_store.keeps(comparison):
                            # OceanVal's own data comes by way of the data store, as
                            # local files cut to (at least) the model's area
                            ds_obs = data_store.gridded(
                                comparison,
                                vv_file,
                                thredds,
                                _lonlat_bounds(ds_model),
                                # only the first level is used of WOA23's at the surface (below)
                                "top" if (vertical_gridded is False and comparison["recipe"] and vv_source == "WOA23") else "all",
                                years=sim_years,
                                label=f"{vv_source} {vv}",
                            )
                        if ds_obs is not None:
                            thredds = False
                            vv_file = ds_obs[0]
                        else:
                            download = None
                            if thredds and comparison.get("builtin") and isinstance(vv_file, str):
                                # a server turning requests away is not asked for more
                                download = data_store.download_instead(comparison["builtin"], vv_file)
                            if download is not None:
                                data_store.told_to_download(
                                    f"{vv_source} {vv}", "the thredds server is turning requests away (HTTP 429)"
                                )
                                thredds = False
                                vv_file = download
                            ds_obs = _open_obs(vv_file, thredds)
                    bad_clim = False

                    if vertical_gridded is False:
                        if thredds:
                            ds_zz = nc.open_thredds(ds_obs[0], checks = False)
                        elif _is_url(vv_file):
                            # already downloaded for ds_obs
                            ds_zz = nc.open_data(ds_obs[0], checks = False)
                        else:
                            ds_zz = nc.open_data(vv_file, checks =False)
                        variable = comparison["obs_variable"]
                        if variable == "auto":
                            variable = ds_zz.variables[0]
                        if ds_zz.contents.query("variable == @variable").nlevels.values[0] > 1:
                            if comparison["recipe"] and vv_source == "WOA23":
                                # the first level, which is the surface at 0 m
                                ds_obs.top()
                            else:
                                ds_obs.cdo_command("topvalue")


                    try:
                        min_obs_year = min(ds_obs.years)
                        max_obs_year = max(ds_obs.years)
                        if climatology is True:
                            if (min_obs_year < max_obs_year):
                                bad_clim = True
                        if climatology is False:
                            if (min_obs_year < max_obs_year):
                                if sim_start > max_obs_year or sim_end < min_obs_year:
                                    session_info["end_messages"] += [f"No observation years found for gridded {vv} ({vv_source}). Please check start and end args!"]
                                    continue
                        if climatology is False:
                            ds_obs.subset(years=sim_years)
                        ds_obs.subset(variables=comparison["obs_variable"])
                        ds_obs.tmean(["year", "month"], align="left")
                        ds_obs.merge("time")
                        ds_obs.tmean(["year", "month"], align="left")
                    except:
                        pass

                    if climatology is False:
                        year_sel = [x for x in ds_model.years if x in ds_obs.years]
                        if len(year_sel) == 0:
                            raise ValueError("There do not appear to be any years in common between model and observation!  ")
                        sim_years = year_sel
                        min_year = min(year_sel)
                        max_year = max(year_sel)
                        ds_obs.subset(years=year_sel)
                        ds_model.subset(years=year_sel)
                        ds_obs.subset(variables=comparison["obs_variable"])
                        ds_obs.run()

                    obs_unit_multiplier = comparison["obs_multiplier"]
                    if obs_unit_multiplier != 1:
                        ds_obs *  obs_unit_multiplier
                    obs_adder = comparison["obs_adder"]
                    if obs_adder != 0:
                        ds_obs + obs_adder
            
                    if bad_clim:
                        raise ValueError(f"Observational data for {vv} appears to not be a climatology, but the climatology argument is set to True. Please fix this!") 

                    if len(month_sel) < 12:
                        try:
                            months = ds_obs.months
                            if len(months) > 1:
                                ds_obs.subset(months=month_sel)
                        except:
                            pass
                
                    if comparison["obs_variable"] != "auto":
                        ds_obs.subset(variables=comparison["obs_variable"])

                    if climatology is False:
                        obs_years = ds_obs.years
                        min_obs_year = min(obs_years)
                        max_obs_year = max(obs_years)

                tidy_warnings(w)

                with warnings.catch_warnings(record=True) as w:
                    if climatology is True:
                        ds_model.tmean("month", align="left")
                    try:
                        if len(obs_years) == 1:
                            ds_obs.tmean("month", align="left")
                    except:
                        pass

                    if len(ds_obs) > 1:
                        ds_obs.merge("time")
                        ds_obs.run()

                    lons = session_info["lon_lim"]
                    lats = session_info["lat_lim"]
                    if lons is None:
                        lons = [-180, 180]
                        lats = [-90, 90]

                    # # figure out the lon/lat extent in the model

                    ds_obs.rename({ds_obs.variables[0]: "observation"})
                    ds_model.rename({ds_model.variables[0]: "model"})
                    ds_model.run()
                    ds_obs.run()

                    # it is possible the years do not overlap, e.g. with satellite Chl
                    if len(ds_model.times) > 12:
                        years1 = ds_model.years
                        years2 = ds_obs.years
                        all_years = [x for x in years1 if x in years2]
                        if len(all_years) != len(years1):
                            if len(all_years) != len(years2):
                                ds_obs.subset(years=all_years)
                                ds_model.subset(years=all_years)
                                ds_obs.run()
                                ds_model.run()
                    if len(ds_obs) > 1:
                        ds_obs.merge("time")

                    ds_obs.run()
                    # the observations are read: what follows matches them up
                    if on_observed is not None:
                        on_observed("gridded", vv, vv_source)
                    ds_model.run()

                    if vertical_gridded is False:
                        contents = ds_model.contents
                        nlevels = contents.nlevels[0]
                        if nlevels > 1:
                            ds_obs.top()
                    try:
                        n_times = len(ds_obs.times)
                    except:
                        n_times = 1
                    try:
                        n_years = len(ds_obs.years)
                    except:
                        n_years = 1

                    ds_obs.run()
                    ds_model.run()

                    # a time series of observations is matched up month by month: only the
                    # years and months both have are kept (the report makes its annual means
                    # from the monthly climatology, so a year with months missing does no harm)
                    paired = climatology is False and n_times > 1
                    if paired:
                        model_drop, obs_drop, obs_only, model_only = common_times(ds_model.times, ds_obs.times)
                        if len(model_drop) == len(ds_model.times) or len(obs_drop) == len(ds_obs.times):
                            raise ValueError(
                                f"There are no years and months in common between model and observations for gridded {vv} ({vv_source})!"
                            )
                        if len(model_drop) > 0:
                            ds_model.drop(time=model_drop)
                            ds_model.run()
                        if len(obs_drop) > 0:
                            ds_obs.drop(time=obs_drop)
                            ds_obs.run()
                        kept = sorted(set(ds_model.years))
                        sim_years = [x for x in sim_years if x in kept]
                        min_year = min(kept)
                        max_year = max(kept)
                        if len(obs_only) > 0:
                            session_info["end_messages"] += [
                                f"Observations for gridded {vv} ({vv_source}) with no model output for the same month were not used: {describe_months(obs_only)}"
                            ]
                        if len(model_only) > 0:
                            session_info["end_messages"] += [
                                f"Model output with no observations for the same month was not used for gridded {vv} ({vv_source}): {describe_months(model_only)}"
                            ]

                    if len(ds_model.times) == 12 and not paired:
                        ds_model.set_year(2000)

                    if len(ds_model.times) < 12 and not paired:
                        sel_months = list(set(ds_model.months))
                        sel_months.sort()
                        if n_times > 1: 
                            ds_obs.subset(months=sel_months)

                    # now do the surface
                    ds_model_surface = ds_model.copy()
                    contents = ds_model_surface.contents
                    nlevels = contents.nlevels[0]
                    if nlevels > 1:
                        ds_model_surface.cdo_command("topvalue")
                    ds_obs_surface = ds_obs.copy()
                    contents = ds_obs_surface.contents
                    nlevels = contents.nlevels[0]
                    if nlevels > 1: 
                        ds_obs_surface.cdo_command("topvalue")
                    _regrid_onto_common_grid(ds_model_surface, ds_obs_surface)

                    n_obs_times = len(ds_obs_surface.times)
                    if n_obs_times <= 1:
                        ds_model_surface.tmean()
                    ds_obs_surface.append(ds_model_surface)

                    if paired or len(ds_model_surface.times) > 12:
                        ds_obs_surface.merge("variable", match=["year", "month"])
                    else:
                        # run both
                        ds_obs_surface.run()
                        ds_model_surface.run()
                        ds_obs_surface.merge("variable", match="month")

                    ds_obs_surface.set_fill(-9999)
                    ds_mask = ds_obs_surface.copy()
                    ds_mask.assign( mask_these=lambda x: -1e30 * ((isnan(x.observation) + isnan(x.model)) > 0), drop=True,)
                    ds_mask.as_missing([-1e40, -1e20])
                    ds_mask.run()
                    ds_obs_surface + ds_mask

                    out_file = (
                        session_info["out_dir"]
                        + f"oceanval_matchups/gridded/{vv}/{vv_source}_{vv}_surface.nc"
                    )
                    out_file_vertical = (
                        session_info["out_dir"]
                        + f"oceanval_matchups/gridded/{vv}/{vv_source}_{vv}_vertical.nc"
                    )

                    # check directory exists for out_file
                    if not os.path.exists(os.path.dirname(out_file)):
                        os.makedirs(os.path.dirname(out_file))
                    # remove the file if it exists
                    if os.path.exists(out_file):
                        os.remove(out_file)
                    ds_obs_surface.set_precision("F32")
                    ds_model_surface = ds_obs_surface.copy()

                    if lon_lim is not None and lat_lim is not None:
                        ds_model_surface.subset(lon=lon_lim, lat=lat_lim)

                    ds_model_surface.run()

                    # unit may need some fiddling
                    out1 = out_file.replace(
                        os.path.basename(out_file), f"{vv_source}_matchup_dict.pkl"
                    )
                    the_dict = {
                        "start": min_year,
                        "end": max_year,
                        "time_shift": session_info.get("time_shift", 0),
                        "months": list(month_sel),
                    }
                    # write to pickle
                    with open(out1, "wb") as f:
                        pickle.dump(the_dict, f)

                    ds_model_surface.subset(lon=lons, lat=lats)
                    ds_model_surface.run()

                    # special handling of temperature
                    if vv == "temperature":
                        max_model = ds_model_surface.to_xarray().model.max()
                        max_obs = ds_model_surface.to_xarray().observation.max()
                        abs_diff = abs(max_model - max_obs)
                        if abs_diff > 40:
                            if max_model > max_obs:
                                add_to_obs = +273.15
                            else:
                                add_to_obs = -273.15
                            ds_model_surface.assign(observation = lambda x: x.observation + add_to_obs)
                        else:
                            add_to_obs = 0

                    _crop_to_model_data(ds_model_surface)

                    ds_model_surface.to_nc(out_file, zip=True, overwrite=True)
                    out_file = out_file.replace(".nc", "_definitions.pkl")
                    # save definitions
                    # change start to min year
                    comparison["start"] = min_year
                    comparison["end"] = max_year
                    with open(out_file, "wb") as f:
                        pickle.dump(definitions, f)


                    if vertical_gridded:

                        if vertical_gridded is True:
                            levels = ds_obs.levels
                            if session_info["ds_depths"] != "z_level":
                                # the thickness was turned surface first if its
                                # surface is at the bottom, so the model has to be too
                                if session_info.get("invert"):
                                    ds_model.invert_levels()
                                ds_model.vertical_interp(levels, thickness = session_info["ds_thickness"])
                            else:
                                ds_model.vertical_interp(levels , fixed = True)

                        _regrid_onto_common_grid(ds_model, ds_obs)
                        n_obs_times = len(ds_obs.times)
                        if n_obs_times <= 1:
                            ds_model.tmean()
                        ds_obs.append(ds_model)

                        if n_obs_times <= 1:
                                ds_obs.merge("variable")
                        else:
                            if paired or len(ds_model.times) > 12:
                                ds_obs.merge("variable", match=["year", "month"])
                            else:
                                ds_obs.merge("variable", match="month")
                        ds_obs.set_fill(-9999)
                        ds_mask = ds_obs.copy()
                        ds_mask.assign( mask_these=lambda x: -1e30 * ((isnan(x.observation) + isnan(x.model)) > 0), drop=True,)
                        ds_mask.as_missing([-1e40, -1e20])
                        ds_mask.run()
                        ds_obs + ds_mask

                        out_file = (
                            session_info["out_dir"]
                            + f"oceanval_matchups/gridded/{vv}/{vv_source}_{vv}_surface.nc"
                        )

                        # check directory exists for out_file
                        if not os.path.exists(os.path.dirname(out_file_vertical)):
                            os.makedirs(os.path.dirname(out_file_vertical))
                        # remove the file if it exists
                        if os.path.exists(out_file_vertical):
                            os.remove(out_file_vertical)
                        ds_obs.set_precision("F32")
                        ds_model = ds_obs.copy()

                        if lon_lim is not None and lat_lim is not None:
                            ds_model.subset(lon=lon_lim, lat=lat_lim)

                        ds_model.run()

                        # unit may need some fiddling
                        out1 = out_file.replace(
                            os.path.basename(out_file), f"{vv_source}_matchup_dict.pkl"
                        )
                        the_dict = {
                            "start": min_year,
                            "end": max_year,
                            "time_shift": session_info.get("time_shift", 0),
                            "months": list(month_sel),
                        }
                        # write to pickle
                        with open(out1, "wb") as f:
                            pickle.dump(the_dict, f)

                        # special handling of temperature
                        if vv == "temperature":
                            max_model = ds_model.to_xarray().model.max()
                            max_obs = ds_model.to_xarray().observation.max()
                            abs_diff = abs(max_model - max_obs)
                            if abs_diff > 40:
                                if max_model > max_obs:
                                    add_to_obs = +273.15
                                else:
                                    add_to_obs = -273.15
                                ds_model.assign(observation = lambda x: x.observation + add_to_obs)
                            else:
                                add_to_obs = 0

                        _crop_to_model_data(ds_model)

                        ds_model.to_nc(out_file_vertical, zip=True, overwrite=True)
                    out_file = out_file.replace(".nc", "_definitions.pkl")
                    # save definitions
                    with open(out_file, "wb") as f:
                        pickle.dump(definitions, f)

                tidy_warnings(w)

                out = (
                    session_info["out_dir"]
                    + f"oceanval_matchups/gridded/{vv}/{vv_source}_{vv}_summary.pkl"
                )
                if not os.path.exists(os.path.dirname(out)):
                    os.makedirs(os.path.dirname(out))
                var_dict["clim_years"] = [min(sim_years), max(sim_years)]
                with open(out, "wb") as f:
                    pickle.dump(var_dict, f)
                if on_matched is not None:
                    on_matched("gridded", vv, vv_source)
            except Exception as exc:
                if not retryable:
                    raise
                record_gridded_failure(vv, vv_source, exc)
                if on_failed is not None:
                    on_failed("gridded", vv, vv_source)
                continue
        
        session_info["cores"] = start_cores
        nc.options(cores = start_cores)

        return None


def _ask_yes_no(question):
    """Ask a yes/no question, deciding on the first letter of the answer."""
    while True:
        answer = prompts.ask(question, ("y", "n")).strip().lower()
        if answer.startswith("y"):
            return True
        if answer.startswith("n"):
            return False
        print("Please answer y or n")


def _ask_minutes(question):
    """Ask how many minutes to keep trying for."""
    while True:
        answer = prompts.ask(question).strip()
        try:
            minutes = float(answer)
        except ValueError:
            print("Please give a number of minutes")
            continue
        if minutes <= 0:
            print("Please give a number of minutes greater than zero")
            continue
        return minutes


def report_gridded_failures():
    """Print the gridded variables that could not be matched up."""
    failures = session_info["failed_gridded"]
    print("")
    print("#" * 78)
    print("These gridded variables could not be matched up:")
    for failure in failures:
        print(f"  {failure['variable']} ({failure['source']}) - {failure['error']}")
    if os.path.exists(_failure_log()):
        print("")
        print(f"Full details are logged in {_failure_log()}")
    print("#" * 78)


def retry_failed_gridded(ask=True, **matchup_args):
    """Offer to reattempt gridded variables whose remote data could not be read.

    Their observations are downloaded as the matchup runs, so a failure is
    usually a connection that was down at the time rather than anything
    wrong with the simulation. The user is told what failed and can spend a
    stretch of time reattempting just those variables, which is worth doing
    before losing the rest of a long matchup.
    """
    while len(session_info["failed_gridded"]) > 0:
        report_gridded_failures()

        failed_names = [
            f"{x['variable']} ({x['source']})" for x in session_info["failed_gridded"]
        ]

        if not ask:
            session_info["end_messages"] += [
                "These gridded variables could not be matched up: "
                + ", ".join(failed_names)
                + f". See {_failure_log()}."
            ]
            return None

        if not _ask_yes_no("Do you want to try to match them up again? (y/n) "):
            session_info["end_messages"] += [
                "These gridded variables could not be matched up: "
                + ", ".join(failed_names)
                + f". See {_failure_log()}."
            ]
            return None

        minutes = _ask_minutes("How long are you willing to wait, in minutes? ")
        deadline = time.time() + minutes * 60
        pause = 30
        attempt = 0

        while True:
            attempt += 1
            # only the sources that failed, not every source of the variable
            retry_these = [
                (x["variable"], x["source"]) for x in session_info["failed_gridded"]
            ]
            print("")
            print(
                f"Attempt {attempt}: retrying "
                + ", ".join(f"{vv} ({source})" for vv, source in retry_these)
            )
            gridded_matchup(var_choice=retry_these, **matchup_args)

            if len(session_info["failed_gridded"]) == 0:
                print("")
                print("Everything that failed earlier has now been matched up.")
                return None

            left = deadline - time.time()
            if left <= 0:
                break
            wait_for = min(pause, left)
            print(
                f"Still failing. Waiting {int(wait_for)} seconds before trying again "
                f"({int(left / 60)} minutes of the time you gave left)."
            )
            time.sleep(wait_for)
            pause = min(pause * 2, 300)

        print("")
        print(f"The {minutes:g} minutes you gave are up.")
