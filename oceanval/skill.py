"""Skill statistics shared by the report pages, so the gridded, summary and
compare pages work each one out the same way.

Every calculation on gridded data is nctoolkit's (CDO): sums() weights each
cell by its area, keeps only the cells where both the model and the
observation are present, and adds up, over the grid, the area and the area
times the model, the observation, their squares, their product, their squared
and absolute difference. Python then only combines those few sums into the
statistics (stats_from_sums), pooling time steps (every cell and month counts
by its area) or depth levels (by the thickness of the layer each stands for).
Point matchups are tables, so their statistics stay with pandas on the point
pages, using weighted_stats.

Chlorophyll and kd490 are roughly log-normal, so their statistics are also
given on log10 values (LOG_VARIABLES), as the CMEMS quality documents and most
papers give them; values that are not positive have no log and are left out.
"""

import math
import warnings

import numpy as np

# variables whose statistics are also given on log10 values
LOG_VARIABLES = ("chlorophyll", "kd490")

# the statistics, in the order the tables show them
NAMES = (
    "n", "obs_mean", "model_mean", "obs_sd", "model_sd", "bias", "pbias", "mae",
    "rmsd", "crmsd", "crmsd_n", "r", "nsd",
)

# the sums sums() adds up over the grid, for each time step and level: the area of the cells
# with both (w), the area times the model (sm) and the observation (so), the area times the
# squared deviations of each from its area-weighted mean over the grid (smm, soo) and their
# product (smo), the squared and absolute difference of the two (sdd, sad), and the number of
# cells with both (n). The deviations are taken in CDO (its expr's fldsum), so the variance is
# not the difference of two large numbers, which loses it when a field's spread is small next
# to its mean
SUMS = ("w", "sm", "so", "smm", "soo", "smo", "sdd", "sad", "n")

# radians in a degree, for the cos(latitude) weights of a grid without cell areas
_RAD = 0.0174532925199433


def is_log(variable):
    """Whether variable's statistics are also given on log10 values."""
    return str(variable).lower() in LOG_VARIABLES


# the variables whose seasonal cycle is timed by its annual low, not its peak: dissolved
# oxygen is lowest at the surface when warming drives it out, so its low is what is timed
LOW_VARIABLES = ("oxygen",)


def times_the_low(variable):
    """Whether the gridded page times variable's annual low (the month of the
    minimum of its monthly climatology), not its peak."""
    return str(variable).lower() in LOW_VARIABLES


def _expr(log, area):
    """The CDO expr that sums() runs: log10 first if log (missing where not
    positive), the area of each cell (area: an expression, or cell_area), and
    the weighted terms, missing wherever either value is."""
    parts = []
    if log:
        parts += [
            "model=(model>0)?log10(model):missval(model)",
            "observation=(observation>0)?log10(observation):missval(observation)",
        ]
    parts += [
        "_v=(isMissval(model)||isMissval(observation))?missval(model):1",
        f"w={area}*_v",
        "sm=w*model",
        "so=w*observation",
        "_mm=fldsum(sm)/fldsum(w)",
        "_om=fldsum(so)/fldsum(w)",
        "smm=w*(model-_mm)*(model-_mm)",
        "soo=w*(observation-_om)*(observation-_om)",
        "smo=w*(model-_mm)*(observation-_om)",
        "sdd=w*(model-observation)*(model-observation)",
        "sad=w*abs(model-observation)",
        "n=_v",
    ]
    return "expr,'" + ";".join(parts) + ";'"


def paired(ds, log=False):
    """ds (an nctoolkit dataset with model and observation) made ready for a
    figure, in place: only model and observation, each missing wherever either
    is, and log10 first if log (missing where not positive)."""
    parts = []
    if log:
        parts += [
            "model=(model>0)?log10(model):missval(model)",
            "observation=(observation>0)?log10(observation):missval(observation)",
        ]
    # a ternary, as CDO's expr makes 0 times missing 0
    parts += [
        "_mo=model",
        "_ob=observation",
        "model=(isMissval(_mo)||isMissval(_ob))?missval(_mo):_mo",
        "observation=(isMissval(_mo)||isMissval(_ob))?missval(_ob):_ob",
    ]
    ds.subset(variables=["model", "observation"])
    ds.cdo_command("expr,'" + ";".join(parts) + ";'")
    return ds


def sums(ds, log=False):
    """The area sums of an nctoolkit dataset with the variables model and
    observation, for each time step and depth level, as a DataFrame with the
    columns SUMS (and the time and depth, if any). The dataset is not changed.
    Cells are weighted by their area (CDO's gridarea), or by cos(latitude)
    if CDO cannot work the areas out. With log, the values are
    log10 first and cells where either is not positive are left out."""
    from oceanval.quiet_nc import nc  # noqa: F401  (nctoolkit's options are set on import)

    def worked_out(with_areas):
        work = ds.copy()
        work.subset(variables=["model", "observation"])
        if with_areas:
            work.cdo_command(_expr(log, "gridarea(model)"))
        else:
            work.cdo_command(_expr(log, f"cos(clat(model)*{_RAD})"))
        work.spatial_sum()
        # written in double precision (nctoolkit forgets the precision at each run, so it is set
        # for the run that writes the sums)
        work.set_precision("F64")
        work.run()
        return work.to_dataframe().reset_index()

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            frame = worked_out(True)
        except Exception:
            frame = worked_out(False)
    keep = [x for x in frame.columns if x in SUMS or "time" in x.lower() or "depth" in x.lower() or x.lower() in ("lev", "level", "z")]
    return frame.loc[:, keep]


def stats_from_sums(frame, weights=None):
    """The statistics (a dict of NAMES) of the sums of sums(), pooled over its
    rows, each row weighted by weights (e.g. a layer's thickness) or equally.
    Each row's sums of squared deviations are about its own mean, so the pooled
    variance adds the spread of the rows' means about the pooled mean. The
    standard deviations are population ones, so that
    crmsd**2 = obs_sd**2 + model_sd**2 - 2 * obs_sd * model_sd * r,
    as on a Taylor diagram. pbias is the bias as a percentage of the observed
    mean, crmsd_n the centred RMSD over obs_sd, nsd model_sd over obs_sd.
    Everything but n is nan if there are no cells with both."""
    factor = np.ones(len(frame)) if weights is None else np.asarray(weights, dtype="float64")
    rows = {name: np.nan_to_num(np.asarray(frame[name], dtype="float64")) * factor for name in SUMS}
    out = dict.fromkeys(NAMES, np.nan)
    out["n"] = int(round(np.sum(np.nan_to_num(np.asarray(frame["n"], dtype="float64")))))
    w = rows["w"].sum()
    if not w > 0:
        return out
    w = float(w)
    mm, om = float(rows["sm"].sum() / w), float(rows["so"].sum() / w)
    has = rows["w"] > 0
    row_mm = np.where(has, rows["sm"] / np.where(has, rows["w"], 1), mm)
    row_om = np.where(has, rows["so"] / np.where(has, rows["w"], 1), om)
    model_var = float((rows["smm"].sum() + np.sum(rows["w"] * (row_mm - mm) ** 2)) / w)
    obs_var = float((rows["soo"].sum() + np.sum(rows["w"] * (row_om - om) ** 2)) / w)
    cov = float((rows["smo"].sum() + np.sum(rows["w"] * (row_mm - mm) * (row_om - om))) / w)
    msd, osd = math.sqrt(max(model_var, 0.0)), math.sqrt(max(obs_var, 0.0))
    rmsd = math.sqrt(max(float(rows["sdd"].sum() / w), 0.0))
    bias = mm - om
    out.update(
        obs_mean=om,
        model_mean=mm,
        obs_sd=osd,
        model_sd=msd,
        bias=bias,
        mae=float(rows["sad"].sum() / w),
        rmsd=rmsd,
        # the centred RMSD from the deviations, not as sqrt(rmsd^2 - bias^2), which loses it when
        # the bias is large
        crmsd=math.sqrt(max(model_var + obs_var - 2 * cov, 0.0)),
        pbias=100 * bias / om if om != 0 else np.nan,
        r=min(max(cov / (msd * osd), -1.0), 1.0) if msd > 0 and osd > 0 else np.nan,
        nsd=msd / osd if osd > 0 else np.nan,
    )
    out["crmsd_n"] = out["crmsd"] / osd if osd > 0 else np.nan
    return out


def dataset_stats(ds, log=False):
    """stats_from_sums of sums(ds), pooled over all its time steps and levels."""
    out = stats_from_sums(sums(ds, log))
    if log:
        out["pbias"] = np.nan
    return out


def file_stats(path, log=False):
    """dataset_stats of a gridded file OceanVal wrote, with the variables model
    and observation (an annual or monthly mean, or a matchup)."""
    from oceanval.quiet_nc import nc

    return dataset_stats(nc.open_data(path, checks=False), log)


def level_stats(ds, log=False):
    """The statistics at each depth level of an nctoolkit dataset with model and
    observation through the water column: (depths, [dict of NAMES with
    "depth"], frame), frame being sums()'s, one row per level."""
    frame = sums(ds, log)
    depth_name = [x for x in frame.columns if "depth" in x.lower() or x.lower() in ("lev", "level", "z")][0]
    frame = frame.groupby(depth_name, sort=True).sum(numeric_only=True).reset_index()
    rows = []
    for k in range(len(frame)):
        row = stats_from_sums(frame.iloc[[k]])
        row["depth"] = float(frame[depth_name].iloc[k])
        rows.append(row)
    return frame[depth_name].to_numpy(dtype="float64"), rows, frame


def layer_thickness(depths):
    """The thickness (m) of the layer each depth level stands for: from halfway
    to the level above (the surface for the first) to halfway to the level
    below (the last as thick as the gap above it)."""
    depths = np.asarray(depths, dtype="float64")
    if depths.size == 1:
        return np.ones(1)
    mid = (depths[1:] + depths[:-1]) / 2
    top = np.concatenate([[min(0.0, depths[0])], mid])
    bottom = np.concatenate([mid, [depths[-1] + (depths[-1] - depths[-2]) / 2]])
    return bottom - top


def bin_stats(depths, frame, depth_bins):
    """The statistics of the levels in each depth bin (oceanval.depths, a level
    being in a bin as a point depth is), from level_stats's frame: the levels'
    sums pooled, each weighted by the thickness of its layer, so a bin of a few
    thick levels is not dominated by its thin ones. A list of dicts, each with
    "depth" (the bin's label), leaving out bins with no level or no cells."""
    from oceanval.depths import bin_depth, depth_label

    depths = np.asarray(depths, dtype="float64")
    thickness = layer_thickness(depths)
    labels = [bin_depth(d, depth_bins) for d in depths]
    rows = []
    for low, high in depth_bins:
        label = depth_label(low, high)
        inside = [k for k, x in enumerate(labels) if x == label]
        if not inside:
            continue
        row = stats_from_sums(frame.iloc[inside], weights=thickness[inside])
        if row["n"] == 0:
            continue
        row["depth"] = label
        rows.append(row)
    return rows


def weighted_stats(model, obs, weights=None):
    """The statistics of paired values in a table (point matchups, which are
    csv tables rather than grids): stats_from_sums of their sums, each pair
    weighted by weights or equally. Pairs with a value missing are left out."""
    import pandas as pd

    model = np.asarray(model, dtype="float64").ravel()
    obs = np.asarray(obs, dtype="float64").ravel()
    w = np.ones_like(obs) if weights is None else np.broadcast_to(np.asarray(weights, dtype="float64"), obs.shape).ravel()
    ok = np.isfinite(model) & np.isfinite(obs) & np.isfinite(w) & (w > 0)
    m, o, w = model[ok], obs[ok], w[ok]
    total = w.sum()
    dm = m - (np.sum(w * m) / total if total > 0 else 0.0)
    do = o - (np.sum(w * o) / total if total > 0 else 0.0)
    frame = pd.DataFrame({
        "w": [total], "sm": [np.sum(w * m)], "so": [np.sum(w * o)], "smm": [np.sum(w * dm * dm)],
        "soo": [np.sum(w * do * do)], "smo": [np.sum(w * dm * do)], "sdd": [np.sum(w * (m - o) ** 2)],
        "sad": [np.sum(w * np.abs(m - o))], "n": [ok.sum()],
    })
    return stats_from_sums(frame)


def log_stats(model, obs, weights=None):
    """weighted_stats of the log10 of paired values in a table, leaving out
    pairs where either is not positive (pbias is nan: a percentage of a log
    has no meaning)."""
    model = np.asarray(model, dtype="float64")
    obs = np.asarray(obs, dtype="float64")
    ok = (model > 0) & (obs > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = weighted_stats(np.where(ok, np.log10(np.where(ok, model, 1.0)), np.nan),
                             np.where(ok, np.log10(np.where(ok, obs, 1.0)), np.nan), weights)
    out["pbias"] = np.nan
    return out


def best(values, how):
    """The position of the best of values (nan ignored), how being "min",
    "max", "abs" (closest to 0) or "one" (closest to 1), or None if all are
    nan."""
    values = np.asarray(values, dtype="float64")
    if not np.isfinite(values).any():
        return None
    key = {
        "min": values,
        "max": -values,
        "abs": np.abs(values),
        "one": np.abs(values - 1),
    }[how]
    return int(np.nanargmin(np.where(np.isfinite(values), key, np.inf)))
