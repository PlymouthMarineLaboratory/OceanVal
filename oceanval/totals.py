"""Totals of a gridded field over the cells matched up (the gap report's options
5 and 34): a flux summed over the area, as an air-sea CO2 flux in Pg C yr-1, or
a concentration summed over the volume, as an inventory in Pmol, with the
published ranges of data/reference_totals.csv beside them.

Every sum is nctoolkit's: CDO's gridarea in an expr, then spatial_sum, one
value for each time step and level. Python only combines those numbers: the
factor that turns the units into the total's (unit_conversion's reading of the
units, and pint), and the levels weighted by the thickness of the layer each
stands for (skill.layer_thickness).

A total over the matched cells is not a global total: a matchup covers the
cells with observations, so the report says how much area the cells cover too.

The ranges in data/reference_totals.csv are each quoted from a paper in the
repository's research/papers, with the paper and its DOI: a quantity no paper
there gives a number for has no row.
"""

import csv
import functools
import importlib.resources

import numpy as np
import pandas as pd

from oceanval import skill, unit_conversion

# the units totals are given in, as the reference table writes them, and pint's names for them
TARGETS = {
    "Pg C yr-1": "petagram / year",
    "Tmol yr-1": "teramole / year",
    "Pmol": "petamole",
}

# what a total is of: a flux per area (summed over the area) or a concentration (over the volume)
FLUX = "flux"
INVENTORY = "inventory"


@functools.lru_cache(maxsize=1)
def _table():
    path = importlib.resources.files("oceanval").joinpath("data/reference_totals.csv")
    with open(path, newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    for row in rows:
        row["low"], row["high"] = float(row["low"]), float(row["high"])
        row["names"] = [x.strip().lower() for x in row["variables"].split("|") if x.strip()]
    return rows


def references(variable):
    """The published ranges of variable's total, as dicts of the table's columns
    (quantity, low, high, units, period, depth, quote, citation, paper, doi)."""
    name = str(variable or "").strip().lower()
    return [dict(row) for row in _table() if name in row["names"]]


def kind_of(units, variable):
    """FLUX for units per area per time, INVENTORY for a concentration (per
    volume or per mass), or None if the units are not one OceanVal can total."""
    try:
        parsed = unit_conversion._parse(str(units or ""), unit_conversion._variable(variable))
    except unit_conversion._Unread:
        return None
    if parsed is None:
        return None
    kind = unit_conversion._kind(parsed[0])
    if kind in ("amount per area per time", "mass per area per time"):
        return FLUX
    if kind in ("amount per volume", "amount per mass"):
        return INVENTORY
    return None


def _is_carbon(units, variable):
    """Whether a total of variable in units is of carbon: the units name it (C
    or CO2), it is the air-sea CO2 flux, or its published range is of carbon."""
    name = unit_conversion._variable(variable)
    if name == "co2flux" or any(row["units"].startswith("Pg C") for row in references(name)):
        return True
    try:
        parsed = unit_conversion._parse(str(units or ""), name)
    except unit_conversion._Unread:
        return False
    return bool(parsed and parsed[2] and parsed[2][0] in ("C", "CO2"))


def target_for(variable, kind, units=None):
    """The units a total of variable is given in: those of its published range;
    else Pg C yr-1 for a flux of carbon and Tmol yr-1 for one of anything else,
    and Pmol for an inventory."""
    found = references(variable)
    if found:
        return found[0]["units"]
    if kind == FLUX:
        return "Pg C yr-1" if _is_carbon(units, variable) else "Tmol yr-1"
    return "Pmol"


def factor(units, variable, target):
    """The number that turns a sum of value times cell area (m2), for a flux, or
    times cell volume (m3), for an inventory, in units, into target (one of
    TARGETS). None if OceanVal cannot tell, as for a mass of something it cannot
    turn into carbon."""
    variable = unit_conversion._variable(variable)
    kind = kind_of(units, variable)
    if kind is None or target not in TARGETS:
        return None
    quantity, _, labels = unit_conversion._parse(str(units), variable)
    registry = unit_conversion._registry()
    if kind == INVENTORY:
        if quantity.dimensionality == registry.get_dimensionality("[substance] / [mass]"):
            # per kilogram of seawater, at OceanVal's density of seawater
            quantity = quantity.to("mol / m ** 3", "seawater")
        total = quantity * registry.Quantity(1, "m ** 3")
    else:
        total = quantity * registry.Quantity(1, "m ** 2")
    wanted = registry.Quantity(1, TARGETS[target])
    has_mass = total.dimensionality == registry.get_dimensionality("[mass] / [time]") or \
        total.dimensionality == registry.get_dimensionality("[mass]")
    wants_mass = wanted.dimensionality in (
        registry.get_dimensionality("[mass] / [time]"), registry.get_dimensionality("[mass]"),
    )
    if has_mass != wants_mass:
        # between a mass and moles: only carbon's, for what is counted as carbon
        if not _is_carbon(units, variable):
            return None
        molar = registry.Quantity(unit_conversion._MOLAR_MASSES["C"], "gram / mole")
        total = total * molar if wants_mass else total / molar
    try:
        return float(total.to(wanted.units).magnitude)
    except Exception:
        return None


def _areas_expr(ds, expr):
    """ds with expr (a CDO expr using {area} for each cell's area), CDO's
    gridarea, or cos(latitude) times the area of a degree square of the Earth if
    CDO cannot work the areas out."""
    ds_try = ds.copy()
    try:
        ds_try.cdo_command(f"expr,'{expr.format(area='gridarea(model)')}'")
        ds_try.run()
        return ds_try
    except Exception:
        ds_try = ds.copy()
        # a degree square at the Equator, in m2, as gridarea's sphere (radius 6371 km) has it
        square = (6371000.0 * np.pi / 180) ** 2
        ds_try.cdo_command(f"expr,'{expr.format(area=f'{square}*cos(clat(model)*{np.pi / 180})')}'")
        ds_try.run()
        return ds_try


def sums(ds):
    """The sums over the cells with both model and observation (nctoolkit) of
    each one's value times the cell's area, and the area, for each time step
    and level: a DataFrame with columns model, observation and area (m2), and
    the time and level coordinates the file has."""
    ds = ds.copy()
    ds.subset(variables=["model", "observation"])
    # new names, with the area first: CDO keeps a variable written back under its own name on its
    # own z axis, but puts a new one on its first operand's, so every sum is on the model's. The
    # observations' axis can differ from it in name (depth and deptht) though not in levels, and the
    # table of the sums must have one row per level, not one per pair of them
    expr = (
        "_v=(isMissval(model)||isMissval(observation))?missval(model):1;"
        "area={area}*_v;tm=area*model;to=area*observation;"
    )
    ds = _areas_expr(ds, expr)
    ds.spatial_sum()
    ds.set_precision("F64")
    ds.run()
    frame = ds.to_dataframe().reset_index()
    return frame.rename(columns={"tm": "model", "to": "observation"})


def field_series(ds, key):
    """The sums over a single field's cells with values (nctoolkit) of its value
    times the cell's area, and the area, for each time step: a DataFrame of
    time, total and area (m2). For analyze()'s totals, where there are no
    observations."""
    ds = ds.copy()
    ds.subset(variables=key)
    expr = f"_v=isMissval({key})?missval({key}):1;area={{area}}*_v;total={key}*area;"
    ds_try = ds.copy()
    try:
        ds_try.cdo_command(f"expr,'{expr.format(area=f'gridarea({key})')}'")
        ds_try.run()
    except Exception:
        ds_try = ds.copy()
        square = (6371000.0 * np.pi / 180) ** 2
        ds_try.cdo_command(f"expr,'{expr.format(area=f'{square}*cos(clat({key})*{np.pi / 180})')}'")
        ds_try.run()
    ds_try.spatial_sum()
    ds_try.set_precision("F64")
    ds_try.run()
    frame = ds_try.to_dataframe().reset_index()
    name = time_name(frame)
    return pd.DataFrame({"time": frame[name] if name else 0, "total": frame.total.astype(float),
                         "area": frame.area.astype(float)})


def level_name(frame):
    """The depth column of a sums() frame, or None."""
    found = [x for x in frame.columns if "depth" in x.lower() or x.lower() in ("lev", "level", "z")]
    return found[0] if found else None


def time_name(frame):
    found = [x for x in frame.columns if "time" in x.lower()]
    return found[0] if found else None


def flux_series(ds, units, variable):
    """The total of a flux over the matched cells for each time step: a
    DataFrame of time, model and observation (in target_for's units) and area
    (m2), or None if the units cannot be totalled."""
    target = target_for(variable, FLUX, units)
    multiplier = factor(units, variable, target)
    if multiplier is None:
        return None, target
    frame = sums(ds)
    name = time_name(frame)
    out = pd.DataFrame({
        "time": frame[name] if name else 0,
        "model": frame.model.astype(float) * multiplier,
        "observation": frame.observation.astype(float) * multiplier,
        "area": frame.area.astype(float),
    })
    # a step with no cell matched has no total
    out.loc[out.area <= 0, ["model", "observation"]] = np.nan
    return out, target


def inventory(ds, units, variable):
    """The inventory over the matched cells of a matchup through the water
    column (ds holds one time step, e.g. the annual mean): (model, observation,
    volume in m3, target units), each level's sum weighted by the thickness of
    its layer; None for the totals if the units cannot be totalled."""
    target = target_for(variable, INVENTORY)
    multiplier = factor(units, variable, target)
    if multiplier is None:
        return None, None, None, target
    frame = sums(ds)
    name = level_name(frame)
    if name is None:
        return None, None, None, target
    frame = frame.groupby(name, sort=True).sum(numeric_only=True).reset_index()
    thickness = skill.layer_thickness(frame[name].to_numpy(dtype=float))
    model = float(np.sum(frame.model.to_numpy(dtype=float) * thickness)) * multiplier
    observation = float(np.sum(frame.observation.to_numpy(dtype=float) * thickness)) * multiplier
    volume = float(np.sum(frame.area.to_numpy(dtype=float) * thickness))
    return model, observation, volume, target


def outside(value, references_):
    """Whether value is outside every published range given (False if there are none)."""
    if not references_ or value is None or not np.isfinite(value):
        return False
    return all(not (row["low"] <= value <= row["high"]) for row in references_)
