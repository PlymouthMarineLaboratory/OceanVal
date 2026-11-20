"""The stratification of casts: how stratified the water column is, in the
observations and in the model, cast by cast.

A cast is the observations at one place on one date (lon, lat and the times
the observations are matched by: oceanval matches each depth of a cast with
the model at that depth). Each measure is worked out the same way from the
observed cast and from the model at the cast's depths, so the two can be
compared:

    dt     surface minus bottom temperature (degrees C)
    pea_t  the potential energy anomaly from temperature alone, with the
           salinity held at 35 (J m-3)
    pea    the potential energy anomaly from temperature and salinity (J m-3)
    mld    the mixed layer depth: where the temperature first differs from
           its value at 10 m by more than 0.2 degrees C (m)

The surface and the bottom are the model's (the user's definition,
2026-11-17): the surface is the centre of the model's top cell, half its
thickness down, and the bottom is the centre of its bottom cell, the sum of
the thicknesses less half the bottom cell's. The sum of the thicknesses is
the water depth. The model's values there are those of its top and bottom
cells; the observed cast is interpolated to the same depths.

The potential energy anomaly (Simpson, 1981; Holt et al., 2017) is the energy
needed to mix the water column, per unit volume:

    phi = g / H * integral from 0 to H of (rho(d) - rho_mean) * d dd

with d the depth, positive down, and H the water depth (to 200 m at most). The
density is TEOS-10 potential density (gsw), from the temperature as potential
temperature and the salinity as practical salinity. Between samples the
density is linear in depth; above the top sample and below the bottom one it
is taken to be that sample's, so the integral of a cast is exact for its
samples. A cast is stratified when dt > 0.5 degrees C, or the potential
energy anomaly is over 10 J m-3 (Holt et al., 2010; Tinker et al., 2024).

Only pandas and numpy are used here: the casts are the matched table, not
netCDF.
"""

import numpy as np
import pandas as pd

G = 9.81
# a cast is stratified when its dt is over this (degrees C) or its potential
# energy anomaly over that (J m-3)
STRATIFIED_DT = 0.5
STRATIFIED_PEA = 10.0
# the potential energy anomaly is of the top 200 m at most (Holt et al., 2017)
PEA_MAX_DEPTH = 200.0
# the mixed layer depth's reference depth and threshold (de Boyer Montegut et al., 2004)
MLD_REFERENCE = 10.0
MLD_THRESHOLD = 0.2
# the salinity of the thermal potential energy anomaly
FIXED_SALINITY = 35.0
# the fewest observed depths a cast is used with
MIN_SAMPLES = 3
# how far an observed cast's shallowest sample may be below the surface, and
# its deepest above the bottom (or the depth the energy anomaly goes to), for
# its value there to be taken as the sample's (m)
SURFACE_TOLERANCE = 5.0
BOTTOM_TOLERANCE = 5.0
# or this fraction of the water depth, if more
BOTTOM_TOLERANCE_FRACTION = 0.1

MEASURES = ("dt", "pea_t", "pea", "mld")
LABELS = {
    "dt": "Surface minus bottom temperature",
    "pea_t": "Thermal potential energy anomaly",
    "pea": "Potential energy anomaly",
    "mld": "Mixed layer depth",
}
UNITS = {"dt": "°C", "pea_t": "J m-3", "pea": "J m-3", "mld": "m"}
# the measures with a threshold for being stratified
THRESHOLDS = {"dt": STRATIFIED_DT, "pea_t": STRATIFIED_PEA, "pea": STRATIFIED_PEA}

TIME_COLUMNS = ("year", "month", "day")
# the columns of the casts' model surface and bottom (see casts_table)
CAST_COLUMNS = (
    "surface_depth", "bottom_depth", "water_depth",
    "model_surface", "model_bottom", "model_surface_salinity", "model_bottom_salinity",
)


def cast_keys(df):
    """The columns that tell the casts of df apart: lon, lat and the times
    it has."""
    return ["lon", "lat"] + [x for x in TIME_COLUMNS if x in df.columns]


def bottom_tolerance(water_depth):
    """How far above the bottom a cast's deepest sample may be (m)."""
    return max(BOTTOM_TOLERANCE, BOTTOM_TOLERANCE_FRACTION * water_depth)


def value_at(depths, values, target, above=SURFACE_TOLERANCE, below=None):
    """The value of a profile at the depth target: linearly interpolated
    between the samples either side, or the shallowest sample's if target is
    above it by no more than above, or the deepest's if target is below it by
    no more than below (0 if None). NaN otherwise. depths must be sorted,
    shallowest first."""
    depths = np.asarray(depths, dtype=float)
    values = np.asarray(values, dtype=float)
    if len(depths) == 0 or not np.isfinite(target):
        return np.nan
    below = 0.0 if below is None else below
    if target < depths[0]:
        return values[0] if depths[0] - target <= above else np.nan
    if target > depths[-1]:
        return values[-1] if target - depths[-1] <= below else np.nan
    return float(np.interp(target, depths, values))


def density(temperature, salinity, depth, lon, lat):
    """TEOS-10 potential density (kg m-3), referenced to the surface, from the
    potential temperature (degrees C) and the practical salinity at lon/lat.
    The absolute salinity is that at the surface too, so a cast of one
    temperature and salinity has one density whatever its depth (depth is
    for the shape of the result only)."""
    import gsw

    temperature = np.asarray(temperature, dtype=float)
    salinity = np.broadcast_to(np.asarray(salinity, dtype=float), temperature.shape)
    pressure = np.zeros(np.broadcast_shapes(temperature.shape, np.shape(depth)))
    absolute = gsw.SA_from_SP(salinity, pressure, lon, lat)
    conservative = gsw.CT_from_pt(absolute, temperature)
    return gsw.sigma0(absolute, conservative) + 1000.0


def pea_from_density(depths, rho, water_depth):
    """The potential energy anomaly (J m-3) of the water column 0 to
    water_depth, whose density is rho at depths (sorted, within it): linear
    between them and that of the first and last above and below them."""
    depths = np.asarray(depths, dtype=float)
    rho = np.asarray(rho, dtype=float)
    if len(depths) == 0 or not water_depth > 0:
        return np.nan
    nodes = np.concatenate([[0.0], depths, [water_depth]])
    values = np.concatenate([[rho[0]], rho, [rho[-1]]])
    a, b = nodes[:-1], nodes[1:]
    ra, rb = values[:-1], values[1:]
    width = b - a
    # the exact integrals of rho and of rho * d over each segment, rho being linear in it
    mass = np.sum(width * (ra + rb) / 2)
    moment = np.sum(width * (ra * (2 * a + b) + rb * (a + 2 * b)) / 6)
    mean = mass / water_depth
    return float(G / water_depth * (moment - mean * water_depth**2 / 2))


def _pea(depths, temperature, salinity, surface, end, water_depth, lon, lat):
    """The potential energy anomaly of a cast, from its samples at depths
    between the surface and end (the bottom, or 200 m), and its values there
    (interpolated, within the tolerances). NaN if the cast does not reach."""
    keep = np.isfinite(temperature) & np.isfinite(salinity)
    depths, temperature, salinity = depths[keep], temperature[keep], salinity[keep]
    if len(depths) == 0:
        return np.nan
    below = bottom_tolerance(water_depth)
    top_t = value_at(depths, temperature, surface)
    top_s = value_at(depths, salinity, surface)
    end_t = value_at(depths, temperature, end, below=below)
    end_s = value_at(depths, salinity, end, below=below)
    if not all(np.isfinite([top_t, top_s, end_t, end_s])):
        return np.nan
    inside = (depths > surface) & (depths < end)
    nodes = np.concatenate([[surface], depths[inside], [end]])
    t = np.concatenate([[top_t], temperature[inside], [end_t]])
    s = np.concatenate([[top_s], salinity[inside], [end_s]])
    rho = density(t, s, nodes, lon, lat)
    return pea_from_density(nodes, rho, min(water_depth, PEA_MAX_DEPTH))


def _mld(depths, temperature, bottom, water_depth):
    """The mixed layer depth of a cast: the depth below 10 m at which its
    temperature first differs from that at 10 m by more than 0.2 degrees C,
    interpolated between samples. The water depth if it never does and the
    cast reaches the bottom; NaN if it never does and does not, or 10 m is not
    within the cast."""
    keep = np.isfinite(temperature)
    depths, temperature = depths[keep], temperature[keep]
    if len(depths) == 0 or depths[0] > MLD_REFERENCE or depths[-1] < MLD_REFERENCE:
        return np.nan
    reference = value_at(depths, temperature, MLD_REFERENCE)
    deeper = depths > MLD_REFERENCE
    nodes = np.concatenate([[MLD_REFERENCE], depths[deeper]])
    t = np.concatenate([[reference], temperature[deeper]])
    gap = np.abs(t - reference)
    crossed = np.flatnonzero(gap > MLD_THRESHOLD)
    if len(crossed) == 0:
        reaches = bottom - depths[-1] <= bottom_tolerance(water_depth)
        return float(water_depth) if reaches else np.nan
    i = crossed[0]
    # between the node before, whose gap is within the threshold, and this one
    fraction = (MLD_THRESHOLD - gap[i - 1]) / (gap[i] - gap[i - 1])
    return float(nodes[i - 1] + fraction * (nodes[i] - nodes[i - 1]))


def profile_measures(depths, temperature, salinity, surface, bottom, water_depth, lon, lat):
    """The measures (MEASURES, as a dict) of one profile, sampled at depths
    (any order), with its temperature and salinity there (NaN where not
    known; salinity None if not known at all), the model's surface and bottom
    depths and its water depth. A measure that cannot be worked out is NaN."""
    depths = np.asarray(depths, dtype=float)
    order = np.argsort(depths, kind="stable")
    depths = depths[order]
    temperature = np.asarray(temperature, dtype=float)[order]
    if salinity is None:
        salinity = np.full(len(depths), np.nan)
    else:
        salinity = np.asarray(salinity, dtype=float)[order]
    known = np.isfinite(temperature)
    t_depths, t_values = depths[known], temperature[known]
    out = dict.fromkeys(MEASURES, np.nan)
    if len(t_depths) == 0:
        return out
    below = bottom_tolerance(water_depth)
    top = value_at(t_depths, t_values, surface)
    base = value_at(t_depths, t_values, bottom, below=below)
    out["dt"] = top - base
    end = min(bottom, PEA_MAX_DEPTH)
    fixed = np.full(len(depths), FIXED_SALINITY)
    out["pea_t"] = _pea(depths, temperature, fixed, surface, end, water_depth, lon, lat)
    if np.isfinite(salinity).any():
        out["pea"] = _pea(depths, temperature, salinity, surface, end, water_depth, lon, lat)
    out["mld"] = _mld(depths, temperature, bottom, water_depth)
    return out


def measures(profiles, casts):
    """The measures of each cast, observed and modelled, as one row for each
    cast and measure that both have: the cast's keys (cast_keys), measure,
    observation, model, water_depth and n_samples.

    profiles is the matched table: the cast keys, depth, observation (the
    temperature), model (the model's there) and, when the salinity is matched
    too, salinity and model_salinity. casts has a row for each cast: its keys
    and CAST_COLUMNS, the model's surface and bottom depths, water depth and
    its temperature and salinity there (the salinity NaN if not matched).
    Casts with fewer than MIN_SAMPLES observed depths, or none within
    SURFACE_TOLERANCE of the surface, are left out."""
    keys = cast_keys(profiles)
    columns = keys + ["measure", "observation", "model", "water_depth", "n_samples"]
    if len(profiles) == 0 or len(casts) == 0:
        return pd.DataFrame(columns=columns)
    has_salinity = "salinity" in profiles.columns and "model_salinity" in profiles.columns
    rows = []
    found = casts.set_index(keys)
    for key, cast in profiles.groupby(keys, sort=True):
        if key not in found.index:
            continue
        info = found.loc[key]
        if isinstance(info, pd.DataFrame):
            info = info.iloc[0]
        cast = cast.sort_values("depth")
        depths = cast.depth.to_numpy(dtype=float)
        observed = cast.observation.to_numpy(dtype=float)
        if np.isfinite(observed).sum() < MIN_SAMPLES:
            continue
        surface, bottom, water = (float(info[x]) for x in ("surface_depth", "bottom_depth", "water_depth"))
        if not all(np.isfinite([surface, bottom, water])) or depths[np.isfinite(observed)][0] - surface > SURFACE_TOLERANCE:
            continue
        lon, lat = float(cast.lon.iloc[0]), float(cast.lat.iloc[0])
        obs = profile_measures(
            depths, observed, cast.salinity.to_numpy(dtype=float) if has_salinity else None,
            surface, bottom, water, lon, lat,
        )
        # the model at the cast's depths, and at its top and bottom cells' centres
        model_depths = np.concatenate([depths, [surface, bottom]])
        model_t = np.concatenate([cast.model.to_numpy(dtype=float), [info["model_surface"], info["model_bottom"]]])
        model_s = None
        if has_salinity:
            model_s = np.concatenate(
                [cast.model_salinity.to_numpy(dtype=float), [info["model_surface_salinity"], info["model_bottom_salinity"]]]
            )
        mod = profile_measures(model_depths, model_t, model_s, surface, bottom, water, lon, lat)
        key = key if isinstance(key, tuple) else (key,)
        for measure in MEASURES:
            if np.isfinite(obs[measure]) and np.isfinite(mod[measure]):
                rows.append((*key, measure, obs[measure], mod[measure], water, int(np.isfinite(observed).sum())))
    return pd.DataFrame(rows, columns=columns)


def stratified(frame):
    """Whether each row of a measures table is stratified, observed and
    modelled, as two boolean series (NaN for measures with no threshold)."""
    threshold = frame.measure.map(THRESHOLDS)
    return frame.observation > threshold, frame.model > threshold


def agreement(frame):
    """How often the model and the observations agree on whether the casts
    are stratified, for each measure with a threshold: a table of measure,
    both stratified (hits), observed only (misses), modelled only (false
    alarms), both mixed, and the share they agree on."""
    rows = []
    for measure in [x for x in MEASURES if x in THRESHOLDS]:
        part = frame.loc[frame.measure == measure]
        if len(part) == 0:
            continue
        obs = part.observation > THRESHOLDS[measure]
        mod = part.model > THRESHOLDS[measure]
        hits = int((obs & mod).sum())
        misses = int((obs & ~mod).sum())
        false_alarms = int((~obs & mod).sum())
        mixed = int((~obs & ~mod).sum())
        rows.append((measure, hits, misses, false_alarms, mixed, (hits + mixed) / len(part)))
    return pd.DataFrame(
        rows, columns=["measure", "both stratified", "observed only", "modelled only", "both mixed", "agreement"]
    )
