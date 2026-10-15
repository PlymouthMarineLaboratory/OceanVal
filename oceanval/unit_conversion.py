"""How to turn observations into the model's units, for the oceanval window's
units step.

suggest() compares the units of a matchup's observations with the model's and,
where it can tell, works out the obs_multiplier and obs_adder that turn one into
the other: observations x multiplier + adder.

The arithmetic is pint's, but pint cannot be given units as model files and
observation datasets write them. It reads the element in "mmol P/m^3" as poise
and the one in "mmol N/m^3" as newton, and cannot read "degrees_C", "1e-3" or
"micromoles_per_kilogram" at all. So each unit is read here first, word by word,
against the short list of units ocean concentrations are given in, and anything
else is left for the user to check. Some conversions need more than units, too:
a seawater density between per kilogram and per volume, the micromoles in a
millilitre of oxygen, for oxygen in ml/l, an equivalent taken as a mole, for
alkalinity, and a molar mass, between a mass and moles, which the units have to
name the substance for, as "mg N/m^3" does (oxygen is taken as O2). Salinity
and pH are never converted: 1, 1e-3 and psu are the same scale of salinity, and
pH has no units.

Nor are Absolute Salinity and Conservative Temperature, which models using
TEOS-10 give, and their long_name or standard_name says: observations are seldom
on these scales, and no multiplier and adder turn one into the other, so the
user is warned of them instead.
"""

import functools
import math
import re

import pint

# the density of seawater assumed between per kilogram and per volume, in kg/m3
SEAWATER_DENSITY = 1025
# the micromoles in a millilitre of oxygen (Garcia and Gordon, 1992)
OXYGEN_UMOL_PER_ML = 44.661

# the molar masses, in g/mol, of what units can name the substance they count
# by as, to go between a mass and moles
_MOLAR_MASSES = {
    "N": 14.007,
    "P": 30.974,
    "Si": 28.085,
    "C": 12.011,
    "O2": 31.998,
    "O_2": 31.998,
    "Fe": 55.845,
    "NO3": 62.004,
    "PO4": 94.970,
    "NH4": 18.039,
    "SiO4": 92.081,
    "SiOH4": 96.113,
    "Si(OH)4": 96.113,
    "CO2": 44.009,
}

# what each variable with rules of its own can be counted as, so that, e.g., a
# mass of phosphorus is not taken for nitrate
_SUBSTANCES = {
    "nitrate": {"N", "NO3"},
    "ammonium": {"N", "NH4"},
    "phosphate": {"P", "PO4"},
    "silicate": {"Si", "SiO4", "SiOH4", "Si(OH)4"},
    "oxygen": {"O2", "O_2"},
}

# what a model's long_name or standard_name says when it gives a TEOS-10
# variable, and what to tell the user of it. Absolute Salinity is 35.16504/35
# times Practical Salinity give or take the water's composition, which is why
# no conversion is suggested.
_TEOS10 = {
    "salinity": (
        "absolute salinity",
        "the simulation's salinity is Absolute Salinity, and observations are "
        "usually Practical Salinity, which is about 0.5% lower (0.16 at a "
        "salinity of 35): check which these observations are",
    ),
    "temperature": (
        "conservative temperature",
        "the simulation's temperature is Conservative Temperature, and "
        "observations are usually in-situ or potential temperature, which "
        "differ from it slightly: no multiplier and adder turn one into the "
        "other",
    ),
}

# what else a variable can be called, for the variables with rules of their own
_ALIASES = {
    "temp": "temperature",
    "sst": "temperature",
    "thetao": "temperature",
    "sal": "salinity",
    "sss": "salinity",
    "so": "salinity",
    "o2": "oxygen",
    "alk": "alkalinity",
    "ta": "alkalinity",
    "talk": "alkalinity",
}

# the kinds of unit a matchup can be in, by pint's dimensions for them, and
# ratios, which have none
_KINDS = {
    "amount per volume": "[substance] / [length] ** 3",
    "amount per mass": "[substance] / [mass]",
    "mass per volume": "[mass] / [length] ** 3",
    "per length": "1 / [length]",
}

# prefixes, as written, and pint's names for them
_PREFIXES = {
    "p": "pico",
    "n": "nano",
    "u": "micro",
    "m": "milli",
    "c": "centi",
    "d": "deci",
    "k": "kilo",
}
_PREFIXES.update({name: name for name in _PREFIXES.values()})

# units, as written, and pint's names for them. Equivalents, which alkalinity
# is measured in, are not pint's, and are taken as moles (see suggest).
_UNITS = {
    "mol": "mole",
    "mole": "mole",
    "moles": "mole",
    "M": "molar",
    "g": "gram",
    "gram": "gram",
    "grams": "gram",
    "gramme": "gram",
    "grammes": "gram",
    "l": "liter",
    "L": "liter",
    "litre": "liter",
    "litres": "liter",
    "liter": "liter",
    "liters": "liter",
    "m": "meter",
    "metre": "meter",
    "metres": "meter",
    "meter": "meter",
    "meters": "meter",
    "eq": "equivalent",
    "Eq": "equivalent",
    "equivalent": "equivalent",
    "equivalents": "equivalent",
}

# a unit, with its prefix and power: "mmol", "m-3", "m^3", "kg**-1"
_UNIT = re.compile(
    r"(?P<prefix>" + "|".join(sorted(_PREFIXES, key=len, reverse=True)) + r")?"
    r"(?P<unit>" + "|".join(sorted(_UNITS, key=len, reverse=True)) + r")"
    r"(?:\^|\*\*)?(?P<power>[-+]?\d+)?"
)
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")

# what a unit can name the substance it counts by, as in "mmol N/m^3" or
# "mg Chl m-3", which is not a unit
_LABELS = {
    "N",
    "P",
    "Si",
    "C",
    "O",
    "O2",
    "O_2",
    "Fe",
    "NO3",
    "PO4",
    "SiO4",
    "SiOH4",
    "Si(OH)4",
    "NH4",
    "CO2",
    "DIC",
    "TA",
    "Chl",
    "chl",
    "CHL",
    "Chla",
    "chla",
    "Chl_a",
    "chl_a",
    "Chl-a",
    "chl-a",
}

# a label written onto the unit it counts by: "mmolN", "mgChl", "mol-C"
_GLUED = re.compile(
    r"(?<![A-Za-z])((?:"
    + "|".join(sorted(_PREFIXES, key=len, reverse=True))
    + r")?(?:moles|mole|mol|grammes|gramme|grams|gram|g|eq|Eq))[-_]?("
    + "|".join(re.escape(label) for label in sorted(_LABELS, key=len, reverse=True))
    + r")(?![A-Za-z0-9])"
)

_TEMPERATURE = re.compile(
    r"(?:deg(?:ree)?s?\s?)?(?P<scale>c|celsius|centigrade|k|kelvin|f|fahrenheit)"
)
_TEMPERATURE_UNITS = {"c": "degC", "k": "kelvin", "f": "degF"}

# salinity as it is written, all on one scale
_SALINITY = {
    "1",
    "1e-3",
    "0.001",
    "10^-3",
    "-",
    "dimensionless",
    "unitless",
    "none",
    "psu",
    "pss",
    "pss-78",
    "pss78",
    "practical salinity unit",
    "practical salinity units",
    "practical salinity scale",
    "ppt",
    "‰",
    "o/oo",
    "permil",
    "per mil",
    "g/kg",
    "g kg-1",
    "g kg^-1",
}


@functools.lru_cache(maxsize=None)
def _registry():
    """pint's units, with seawater's density to go between per kilogram and per
    volume. Made when first needed, as it takes a moment."""
    registry = pint.UnitRegistry()
    density = registry.Quantity(SEAWATER_DENSITY, "kg / m ** 3")
    seawater = pint.Context("seawater")
    seawater.add_transformation(
        "[substance] / [mass]",
        "[substance] / [length] ** 3",
        lambda _, value: value * density,
    )
    seawater.add_transformation(
        "[substance] / [length] ** 3",
        "[substance] / [mass]",
        lambda _, value: value / density,
    )
    registry.add_context(seawater)
    return registry


def _variable(name):
    """The variable a matchup is of, as named in the recipes."""
    name = str(name or "").strip().lower()
    return _ALIASES.get(name, name)


def _words(text):
    return re.sub(r"[\s_]+", " ", text).strip().lower()


def _temperature(text, variable):
    """pint's name for a temperature's units, or None if they are not one."""
    degree = "°" in text or "º" in text
    words = _words(text.replace("°", " ").replace("º", " "))
    match = _TEMPERATURE.fullmatch(words)
    if match is None:
        return None
    # alone, C and F could be anything, e.g. carbon: only a temperature's are
    # read as degrees
    if words in ("c", "f") and not (degree or variable == "temperature"):
        return None
    return _TEMPERATURE_UNITS[match["scale"][0]]


def _prepare(text):
    """Units made regular: symbols, superscripts, and dividing written out."""
    for old, new in (
        ("µ", "u"),
        ("μ", "u"),
        ("·", " "),
        ("⋅", " "),
        ("−", "-"),
        ("⁻", "-"),
        ("¹", "1"),
        ("²", "2"),
        ("³", "3"),
    ):
        text = text.replace(old, new)
    text = re.sub(r"(?i)^per\s+", "1/", text.strip())
    text = re.sub(r"(?i)_per_|\s+per\s+", "/", text)
    # micro-mol
    text = re.sub(r"(?i)\b(pico|nano|micro|milli|centi|deci|kilo)-", r"\1", text)
    # powers as Python and Fortran write them, m**3, which would otherwise be
    # split at the *s
    text = text.replace("**", "^")
    # /m
    text = re.sub(r"^/", "1/", text)
    return _GLUED.sub(r"\1 \2", text)


class _Unread(Exception):
    """A word in some units that OceanVal does not know."""

    def __init__(self, word):
        super().__init__(word)
        self.word = word


def _parse(text, variable):
    """What one of the units in text is, as a pint quantity, with what had to
    be assumed to read it and the labels naming what it counts. None if
    OceanVal does not know them, and _Unread for a word it cannot read."""
    factor = 1.0
    # (unit, prefix, power), in pint's names
    items = []
    labels = []
    for index, part in enumerate(_prepare(text).split("/")):
        sign = 1 if index == 0 else -1
        tokens = [
            token
            for token in re.split(r"[\s*]+|(?<=[A-Za-z0-9])\.(?=[A-Za-z])", part)
            if token
        ]
        read = False
        chlorophyll = False
        for token in tokens:
            if token in _LABELS:
                chlorophyll = token.lower().startswith("chl")
                labels.append(token)
                continue
            # chl a
            if chlorophyll and token == "a":
                chlorophyll = False
                continue
            chlorophyll = False
            read = True
            if _NUMBER.fullmatch(token):
                factor *= float(token) ** sign
                continue
            match = _UNIT.fullmatch(token)
            if match is None:
                raise _Unread(token)
            prefix = _PREFIXES.get(match["prefix"] or "", "")
            items.append(
                (_UNITS[match["unit"]], prefix, int(match["power"] or 1) * sign)
            )
        # nothing but labels, e.g. "C", is not a unit
        if not read:
            return None

    # "mmol eq/m^3" is millimoles of equivalents: eq is only a label beside moles
    if any(unit == "mole" for unit, _, _ in items):
        items = [item for item in items if item[:2] != ("equivalent", "")]

    registry = _registry()
    quantity = registry.Quantity(factor)
    assumed = set()
    for unit, prefix, power in items:
        if unit == "equivalent":
            if variable != "alkalinity":
                return None
            assumed.add("equivalents")
            quantity *= registry.Quantity(1, prefix + "mole") ** power
        elif (
            unit == "liter"
            and power == 1
            and variable == "oxygen"
            and not any(item[0] in ("mole", "molar") for item in items)
            and "oxygen" not in assumed
        ):
            # oxygen measured by the volume it takes up as a gas
            assumed.add("oxygen")
            millilitres = registry.Quantity(1, prefix + "liter").to("milliliter")
            quantity *= registry.Quantity(
                millilitres.magnitude * OXYGEN_UMOL_PER_ML, "micromole"
            )
        else:
            quantity *= registry.Quantity(1, prefix + unit) ** power
    if _kind(quantity) is None:
        # e.g. a rate, or labels OceanVal does not know read as units
        return None
    return quantity, assumed, list(dict.fromkeys(labels))


def _kind(quantity):
    """Which of _KINDS a quantity's units are, or None."""
    if quantity.dimensionless:
        return "ratio"
    registry = _registry()
    for kind, dimensions in _KINDS.items():
        if quantity.dimensionality == registry.get_dimensionality(dimensions):
            return kind
    return None


def _number(value):
    """value to 6 significant figures, as a whole number where it is one."""
    value = float(f"{value:.6g}")
    return int(value) if value == int(value) else value


def _missing(units):
    return units is None or not str(units).strip()


def _salinity_scale(units):
    """Whether units are one of the ways the salinity scale is written."""
    words = _words(units)
    if words in _SALINITY:
        return True
    try:
        # 1.e-3, 1.0e-3
        return float(words) in (1.0, 1e-3)
    except ValueError:
        return False


def _molar_mass(variable, labels, units):
    """The molar mass, in g/mol, of what a mass in units is of, by the labels
    naming it, with what was assumed for it. None, and why, if OceanVal cannot
    tell."""
    if not labels:
        if variable == "oxygen":
            return _MOLAR_MASSES["O2"], "oxygen mass"
        return None, (
            "OceanVal can only turn a mass into moles where the units say what "
            f"it is a mass of, as “mg N/m^3” does, and “{units}” does not"
        )
    label = labels[0]
    if (
        len(labels) > 1
        or label not in _MOLAR_MASSES
        or label not in _SUBSTANCES.get(variable, {label})
    ):
        return None, f"OceanVal cannot turn the mass in “{units}” into moles of {variable}"
    return _MOLAR_MASSES[label], f"molar mass:{label}"


def _assumption(name):
    """What OceanVal assumed, as the note says it."""
    if name.startswith("molar mass:"):
        label = name.partition(":")[2]
        return f"a molar mass of {_MOLAR_MASSES[label]} g/mol for {label.replace('_', '')}"
    return {
        "density": f"a seawater density of {SEAWATER_DENSITY} kg/m³",
        "oxygen": f"a millilitre of oxygen as {OXYGEN_UMOL_PER_ML} µmol",
        "equivalents": "an equivalent of alkalinity as a mole",
        "oxygen mass": f"the oxygen as O₂, of {_MOLAR_MASSES['O2']} g/mol",
    }[name]


def same(note="OceanVal thinks these are the same units, so no conversion should be needed."):
    return {"status": "same", "multiplier": 1, "adder": 0, "note": note}


def unknown(reason):
    return {
        "status": "unknown",
        "multiplier": None,
        "adder": None,
        "note": f"Not checked: {reason}. Fill in the boxes if the units differ.",
    }


def _converted(multiplier, adder, assumed=()):
    if not (math.isfinite(multiplier) and math.isfinite(adder)) or multiplier == 0:
        # e.g. units written with a 0 in them
        return unknown("OceanVal could not read these units")
    multiplier, adder = _number(multiplier), _number(adder)
    if (multiplier, adder) == (1, 0):
        return same()
    note = "Suggested by OceanVal"
    if assumed:
        note += ", taking " + " and ".join(_assumption(name) for name in sorted(assumed))
    return {
        "status": "convert",
        "multiplier": multiplier,
        "adder": adder,
        "note": note + ".",
    }


def suggest(variable, obs_units, model_units, model_description=None, obs_description=None):
    """How to turn a matchup's observations, in obs_units, into the model's
    model_units, as observations x multiplier + adder.

    variable is the one the matchup is of, as the recipes name it, e.g.
    "nitrate", which some conversions depend on. model_description and
    obs_description are the long_name and standard_name of the model's
    variable and of the observations', where they are known, which tell
    Absolute Salinity and Conservative Temperature apart. Returns a dict
    holding the status, "same", "convert", "warn" (the same units, but not
    the same quantity) or "unknown" (when OceanVal cannot tell), the
    multiplier and adder (None if unknown), and a note on what was found.
    """
    variable = _variable(variable)
    try:
        found = _suggest(variable, obs_units, model_units)
    except (pint.PintError, ArithmeticError, ValueError, TypeError):
        # e.g. a unit with 0 in it: never a reason to stop the units step
        found = unknown("OceanVal could not read these units")
    return _teos10(variable, found, model_description, obs_description)


def _teos10(variable, found, model_description, obs_description):
    """found, with a warning if the model gives Absolute Salinity or
    Conservative Temperature and the observations are not said to."""
    if variable not in _TEOS10:
        return found
    phrase, warning = _TEOS10[variable]
    if phrase not in _words(str(model_description or "")) or phrase in _words(
        str(obs_description or "")
    ):
        return found
    if found["status"] == "same":
        return dict(
            found,
            status="warn",
            note=f"OceanVal thinks the units are the same, but {warning}.",
        )
    return dict(found, note=f"{found['note']} Also, {warning}.")


def _suggest(variable, obs_units, model_units):
    if variable == "ph":
        return same("pH has no units, so OceanVal thinks no conversion is needed.")
    if _missing(obs_units):
        return unknown("the observations' units are not known")
    if _missing(model_units):
        return unknown("the simulation's files do not give its units")
    obs_units, model_units = str(obs_units), str(model_units)
    if variable == "salinity":
        if _salinity_scale(obs_units) and _salinity_scale(model_units):
            return same(
                "OceanVal thinks salinity is on the same scale in these units, "
                "so no conversion should be needed."
            )
        return unknown("OceanVal does not recognise these units of salinity")

    temperatures = _temperature(obs_units, variable), _temperature(
        model_units, variable
    )
    if all(temperatures):
        registry = _registry()
        start, end = temperatures
        adder = registry.Quantity(0, start).to(end).magnitude
        multiplier = registry.Quantity(1, start).to(end).magnitude - adder
        return _converted(multiplier, adder)
    if any(temperatures):
        return unknown("only one of them is a temperature")

    sides = []
    for units in (obs_units, model_units):
        try:
            parsed = _parse(units, variable)
        except _Unread as unread:
            if unread.word != units.strip():
                return unknown(
                    f"OceanVal does not recognise “{unread.word}” in the units “{units}”"
                )
            parsed = None
        if parsed is None:
            return unknown(f"OceanVal does not recognise the units “{units}”")
        sides.append(list(parsed))

    # a mass compared with moles: the mass is turned into moles of what it is of
    kinds = [_kind(quantity) for quantity, _, _ in sides]
    if "mass per volume" in kinds and {"amount per volume", "amount per mass"} & set(kinds):
        for side, kind, units in zip(sides, kinds, (obs_units, model_units)):
            if kind != "mass per volume":
                continue
            molar_mass, assumption = _molar_mass(variable, side[2], units)
            if molar_mass is None:
                return unknown(assumption)
            side[0] = (side[0] / _registry().Quantity(molar_mass, "g / mol")).to(
                "mol / m ** 3"
            )
            side[1] = side[1] | {assumption}

    (obs, obs_assumed, _), (model, model_assumed, _) = sides
    assumed = obs_assumed | model_assumed
    kinds = {_kind(obs), _kind(model)}
    contexts = ()
    if kinds == {"amount per volume", "amount per mass"}:
        assumed.add("density")
        contexts = ("seawater",)
    elif len(kinds) > 1:
        return unknown("OceanVal cannot convert one of these units into the other")
    multiplier = obs.to(model.units, *contexts).magnitude / model.magnitude
    if kinds == {"ratio"} and _number(multiplier) != 1:
        # 1 and 1e-3 are more often two ways of writing one scale, as for
        # salinity, than a factor of 1000
        return unknown("ratios such as 1 and 1e-3 are often the same scale")
    return _converted(multiplier, 0, assumed)
