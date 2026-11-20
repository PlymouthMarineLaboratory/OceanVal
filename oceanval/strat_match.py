"""Matching casts for stratification (see oceanval.stratification), and the
temperature and salinity matchups that come with it.

A stratification matchup matches every observed depth of a cast with the
model, as a temperature matchup through the water column does, and also the
centres of the model's top and bottom cells (the surface and the bottom, the
user's definition, 2026-11-17), with the temperature and salinity both read
in the one pass. Each cast is matched with the model's water column nearest
it (not interpolated between the four around it, as other point matchups
are): where the sea bed shelves, a neighbour's missing cells would lose the
cast's deep samples and its bottom cell, and the top and bottom cells are
those of the one column. The casts' rows are told apart by a role column: "cast" for
the observed depths, "surface" and "bottom" for the model's two cells. Once
matched, they give the profiles and the casts that
oceanval.stratification.measures works the measures out from.

The same rows are a temperature matchup through the water column and, where
salinity was observed, a salinity one: matchup writes them as those matchups,
so they are not matched again (reused, see add_reused).

Only pandas is used here: matchall reads the model.
"""

import numpy as np
import pandas as pd

from oceanval import stratification

STRATIFICATION = "stratification"
# the variables whose matchups through the water column a stratification
# matchup gives, with the model variable each is of (the attribute of the
# stratification Variable)
REUSED = {"temperature": "model_variable", "salinity": "salinity_variable"}
# matched by these, always (see parsers.STRATIFICATION_POINT_TIME_RES)
TIME_COLUMNS = ["year", "month", "day"]
# how far inside the model's top and bottom cell centres a depth is matched,
# so that it stays within the model's depths despite rounding (as
# matchall.TOP_CELL_OFFSET)
OFFSET = 1e-4
# the names the model's temperature and salinity are worked out as
TEMPERATURE = "oceanval_temperature"
SALINITY = "oceanval_salinity"
CELL_COLUMNS = ["surface_depth", "bottom_depth", "water_depth"]


def first(point_vars):
    """point_vars, (variable, source) pairs, sorted with stratification first,
    so that the temperature and salinity matchups it gives are made before
    those variables come up."""
    return sorted(point_vars, key=lambda x: (x[0] != STRATIFICATION, x))


def _same(comparison, start, end, point_time_res):
    """Whether a temperature or salinity comparison is matched as a
    stratification comparison of the same source is: through the water column,
    by year, month and day, and over the same years."""
    given = comparison.get("point_time_res") or point_time_res
    return (
        comparison.get("vertical", False)
        and comparison.get("recipe") is not None
        and sorted(given) == sorted(TIME_COLUMNS)
        and comparison.get("start") == start
        and comparison.get("end") == end
    )


def add_reused(definitions, point_all, point_time_res):
    """The temperature and salinity matchups through the water column that the
    stratification comparisons in point_all ((variable, source) pairs) give,
    as {(variable, source): stratification source}. Those not asked for are
    added to definitions and point_all, matched by year, month and day over
    the stratification's years, so that the report has their pages. Those
    asked for, but matched otherwise (by month, or over other years, or of
    another model variable), are not given: they are matched on their own.
    point_time_res is matchup's, which a comparison without its own uses."""
    reused = {}
    for variable, source in list(point_all):
        if variable != STRATIFICATION:
            continue
        found = definitions[variable]
        comparison = found.point_comparisons[source]
        kind = comparison["recipe"].get("kind", "ices")
        for name, attribute in REUSED.items():
            model_variable = getattr(found, attribute, None)
            if model_variable is None:
                continue
            existing = getattr(definitions, name, None)
            if existing is not None and existing.model_variable != model_variable:
                print(
                    f"The {name} matchups are of {existing.model_variable}, but stratification's of "
                    f"{model_variable}, so stratification does not give the {name} ({source}) matchup"
                )
                continue
            if existing is not None and source in existing.point_comparisons:
                if _same(existing.point_comparisons[source], comparison["start"], comparison["end"], point_time_res):
                    reused[(name, source)] = source
                else:
                    print(
                        f"The {name} ({source}) matchup is not matched through the water column by year, month "
                        f"and day over the years of stratification, so it is matched on its own"
                    )
                continue
            definitions.add_point_comparison(
                name=name,
                model_variable=model_variable,
                recipe={name: kind},
                vertical=True,
                start=comparison["start"],
                end=comparison["end"],
                point_time_res=list(TIME_COLUMNS),
            )
            point_all.append((name, source))
            reused[(name, source)] = source
    return reused


def cells(places, top, bottom):
    """The model's cells at places (lon and lat): its surface (the depth of
    the centre of its top cell), its bottom (that of its bottom cell) and
    its water depth, as places with CELL_COLUMNS. top is a dataframe of lon,
    lat and top_depth, or one depth for all (z-levels); bottom one of lon,
    lat, bottom_depth and water_depth. Places with no sea are left out."""
    found = places.loc[:, ["lon", "lat"]].drop_duplicates()
    if isinstance(top, pd.DataFrame):
        found = found.merge(top.rename(columns={"top_depth": "surface_depth"}), on=["lon", "lat"], how="inner")
    else:
        found = found.assign(surface_depth=float(top))
    found = found.merge(bottom, on=["lon", "lat"], how="inner")
    found = found.loc[:, ["lon", "lat"] + CELL_COLUMNS].astype(
        {x: "float64" for x in CELL_COLUMNS}
    )
    sea = found.water_depth.gt(0) & found.bottom_depth.gt(0) & found.surface_depth.notna()
    return found.loc[sea].reset_index(drop=True)


def with_cell_rows(df, places):
    """The casts' rows to match: the observed depths of df (lon, lat, the
    times, depth, observation and salinity) with role "cast", each matched
    within the model's top and bottom cell centres (an observation above the
    top one, or below the bottom one, is in that cell), and a row at each
    cast's top and bottom cell centres, roles "surface" and "bottom". The
    depth observed is kept as obs_depth. Casts where the model has no sea
    (not in places, from cells) are left out."""
    keys = ["lon", "lat"] + [x for x in TIME_COLUMNS if x in df.columns]
    rows = df.merge(places, on=["lon", "lat"], how="inner")
    if len(rows) == 0:
        return rows.assign(role=pd.Series(dtype=object), obs_depth=pd.Series(dtype=float))
    top = rows.surface_depth + OFFSET
    base = np.maximum(rows.bottom_depth - OFFSET, top)
    rows = rows.assign(obs_depth=rows.depth, depth=rows.depth.clip(lower=top, upper=base), role="cast")
    casts = rows.drop_duplicates(keys).loc[:, keys + CELL_COLUMNS]
    surface = casts.assign(depth=casts.surface_depth + OFFSET, role="surface")
    bottom = casts.assign(
        depth=np.maximum(casts.bottom_depth - OFFSET, casts.surface_depth + OFFSET), role="bottom"
    )
    columns = keys + ["depth", "obs_depth", "observation", "salinity", "role"]
    if "salinity" not in rows.columns:
        rows = rows.assign(salinity=np.nan)
    extra = pd.concat([surface, bottom], ignore_index=True).assign(
        obs_depth=lambda x: x.depth, observation=np.nan, salinity=np.nan
    )
    return pd.concat([rows.loc[:, columns], extra.loc[:, columns]], ignore_index=True)


def model_columns(matched, keys):
    """The model's temperature and salinity columns of what match_points
    gave (the salinity None if it was not matched): TEMPERATURE and SALINITY,
    or the one column besides the keys and depth."""
    if TEMPERATURE in matched.columns:
        return TEMPERATURE, SALINITY if SALINITY in matched.columns else None
    others = [x for x in matched.columns if x not in keys + ["depth"]]
    return others[0], None


def split(matched, rows, places):
    """The profiles and the casts (as oceanval.stratification.measures reads
    them) of the matched model values, matched (the keys, depth and the
    model's temperature and salinity), at rows (with_cell_rows). The model's
    temperature is in degrees C (from Kelvin if its values are over 100).
    Returns (profiles, casts, kelvin): the profiles with obs_depth as their
    depth, and model_kelvin the model's own temperature if it was in Kelvin."""
    keys = ["lon", "lat"] + [x for x in TIME_COLUMNS if x in rows.columns]
    temperature, salinity = model_columns(matched, keys)
    both = matched.merge(rows, on=keys + ["depth"], how="inner")
    kelvin = len(both) > 0 and float(both[temperature].max()) > 100
    both = both.assign(model=both[temperature] - 273.15 if kelvin else both[temperature])
    if salinity is not None:
        both = both.assign(model_salinity=both[salinity])
    observed = both.loc[both.role == "cast"].assign(depth=lambda x: x.obs_depth)
    columns = keys + ["depth", "observation", "salinity", "model"] + (["model_salinity"] if salinity else [])
    profiles = observed.loc[:, columns].groupby(keys + ["depth"], as_index=False).mean()

    def at(role, name):
        part = both.loc[both.role == role].drop_duplicates(keys)
        out = part.loc[:, keys].assign(**{f"model_{name}": part.model.values})
        salt = part.model_salinity.values if salinity else np.nan
        return out.assign(**{f"model_{name}_salinity": salt})

    casts = (
        at("surface", "surface")
        .merge(at("bottom", "bottom"), on=keys, how="inner")
        .merge(places, on=["lon", "lat"], how="inner")
    )
    return profiles, casts.loc[:, keys + list(stratification.CAST_COLUMNS)], kelvin


def reused_frames(profiles, kelvin):
    """The temperature and salinity matchups (keys, depth, observation and
    model) of the profiles: the temperature in the model's own units (the
    observations in Kelvin too, if it was, as any temperature matchup has
    them), and the salinity only where both were given. A missing one is
    None."""
    keys = [x for x in ["lon", "lat"] + TIME_COLUMNS + ["depth"] if x in profiles.columns]
    temperature = profiles.loc[:, keys + ["observation", "model"]].dropna()
    if kelvin:
        temperature = temperature.assign(observation=temperature.observation + 273.15, model=temperature.model + 273.15)
    frames = {"temperature": temperature.reset_index(drop=True)}
    if "model_salinity" in profiles.columns:
        salt = (
            profiles.loc[:, keys + ["salinity", "model_salinity"]]
            .dropna()
            .rename(columns={"salinity": "observation", "model_salinity": "model"})
            .reset_index(drop=True)
        )
        frames["salinity"] = salt if len(salt) > 0 else None
    else:
        frames["salinity"] = None
    return frames


def profile_path(out):
    """Where the profiles of the stratification matchup out (its csv) are
    kept: not a .csv, which the report would take for a matchup."""
    return out.replace(".csv", "_profiles.csv.gz")


def casts_path(out):
    return out.replace(".csv", "_casts.csv.gz")
