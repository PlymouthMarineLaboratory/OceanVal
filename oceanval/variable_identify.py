"""Identify the model variable that holds an observational variable, where
the long_name rules of oceanval.create_recipes find none, or more than one.

Three kinds of evidence are weighed, in turn, and the first that gives one
answer is kept:

1. the variable's CF ``standard_name``, which CMIP output and much else has;
2. the names models give their variables (MODEL_NAMES): ERSEM's (from its
   FABM code, https://github.com/PlymouthMarineLaboratory/ersem), CMIP6's,
   and those of NEMO-PISCES, NEMO-MEDUSA, BFM, CESM-MARBL, GFDL-COBALT,
   HAMOCC and ROMS-Fennel, some as sums or formulas of several;
3. nothing else: a guess that could be wrong is left for the user to make.

A candidate whose units are of a flux or of an amount per area (per m2, per
second, per day) is never taken for a concentration.

It only fills gaps (create_recipes.contents_mapping calls it for a variable
its rules leave unmatched), so what OceanVal found before is found as before.
How each was found is kept (found_by), for the create_recipes window to say.
"""

import re

from oceanval import model_expr

# the CF standard names (https://cfconventions.org/standard-names.html) of each
# observational variable
STANDARD_NAMES = {
    "alkalinity": ("sea_water_alkalinity_expressed_as_mole_equivalent",),
    "ammonium": ("mole_concentration_of_ammonium_in_sea_water",),
    "chlorophyll": (
        "mass_concentration_of_chlorophyll_a_in_sea_water",
        "mass_concentration_of_chlorophyll_in_sea_water",
    ),
    "dic": ("mole_concentration_of_dissolved_inorganic_carbon_in_sea_water",),
    "doc": ("mole_concentration_of_dissolved_organic_carbon_in_sea_water",),
    "poc": ("mole_concentration_of_particulate_organic_matter_expressed_as_carbon_in_sea_water",),
    "kd490": ("volume_attenuation_coefficient_of_downwelling_radiative_flux_in_sea_water",),
    "mesozooplankton": ("mole_concentration_of_mesozooplankton_expressed_as_carbon_in_sea_water",),
    "microzooplankton": ("mole_concentration_of_microzooplankton_expressed_as_carbon_in_sea_water",),
    "diatoms": ("mole_concentration_of_diatoms_expressed_as_carbon_in_sea_water",),
    "picophytoplankton": ("mole_concentration_of_picophytoplankton_expressed_as_carbon_in_sea_water",),
    "bacteria": ("mole_concentration_of_bacteria_expressed_as_carbon_in_sea_water",),
    "nitrate": ("mole_concentration_of_nitrate_in_sea_water",),
    "oxygen": ("mole_concentration_of_dissolved_molecular_oxygen_in_sea_water",),
    "ph": ("sea_water_ph_reported_on_total_scale",),
    "co2flux": ("surface_downward_mass_flux_of_carbon_dioxide_expressed_as_carbon",
                "surface_downward_mole_flux_of_carbon_dioxide"),
    "pco2": ("surface_partial_pressure_of_carbon_dioxide_in_sea_water",),
    "phosphate": ("mole_concentration_of_phosphate_in_sea_water",),
    "salinity": ("sea_water_practical_salinity", "sea_water_salinity", "sea_water_absolute_salinity"),
    "silicate": ("mole_concentration_of_silicate_in_sea_water",),
    "temperature": (
        "sea_water_potential_temperature",
        "sea_water_conservative_temperature",
        "sea_water_temperature",
    ),
}

# what models call each observational variable in their output: one name, a
# sum of names (several types of phytoplankton for chlorophyll) or a formula
# (a saturation state, as the carbonate ion over its saturation value). Each
# family's are as its code or its documentation names them.
MODEL_NAMES = {
    # FABM writes <instance>_<variable>: N3 nitrate's n, O3 carbonate's c, TA,
    # pH, Om_cal and Om_arg, the four phytoplankton's Chl, and the labile,
    # semi-labile and semi-refractory dissolved organic matter's c (R1-R3)
    "ERSEM": {
        "alkalinity": ("O3_TA",),
        "ammonium": ("N4_n",),
        "aou": ("O2_AOU",),
        "chlorophyll": ("P1_Chl+P2_Chl+P3_Chl+P4_Chl",),
        "co2flux": ("O3_fair",),
        "dic": ("O3_c",),
        "doc": ("R1_c+R2_c+R3_c",),
        "mesozooplankton": ("Z4_c",),
        "microzooplankton": ("Z5_c",),
        "diatoms": ("P1_c",),
        "picophytoplankton": ("P3_c",),
        "bacteria": ("B1_c",),
        "nitrate": ("N3_n",),
        "omegaaragonite": ("O3_Om_arg",),
        "omegacalcite": ("O3_Om_cal",),
        "oxygen": ("O2_o",),
        "pco2": ("O3_pCO2",),
        "ph": ("O3_pH",),
        "phosphate": ("N1_p",),
        "silicate": ("N5_s",),
    },
    # ERSEM in NEMO before FABM, and the names some of its post-processing gives
    "ERSEM (NEMO)": {
        "alkalinity": ("O3TA", "bioalk"),
        "ammonium": ("N4n",),
        "chlorophyll": ("P1Chl+P2Chl+P3Chl+P4Chl", "P1chl+P2chl+P3chl+P4chl"),
        "dic": ("O3c",),
        "doc": ("R1c+R2c+R3c",),
        "nitrate": ("N3n",),
        "omegaaragonite": ("Om_arg", "OmArg"),
        "omegacalcite": ("Om_cal", "OmCal"),
        "oxygen": ("O2o",),
        "ph": ("pH",),
        "phosphate": ("N1p",),
        "silicate": ("N5s",),
    },
    "CMIP6": {
        "alkalinity": ("talk",),
        "ammonium": ("nh4",),
        "chlorophyll": ("chl",),
        "co2flux": ("fgco2",),
        "dic": ("dissic",),
        "doc": ("dissoc",),
        "mesozooplankton": ("zmeso",),
        "microzooplankton": ("zmicro",),
        "diatoms": ("phydiat",),
        "picophytoplankton": ("phypico",),
        "bacteria": ("bacc",),
        "nitrate": ("no3",),
        "omegaaragonite": ("co3/co3satarag",),
        "omegacalcite": ("co3/co3satcalc",),
        "oxygen": ("o2",),
        "pco2": ("spco2",),
        "ph": ("ph",),
        "phosphate": ("po4",),
        "salinity": ("so",),
        "silicate": ("si",),
        "temperature": ("thetao",),
    },
    "NEMO-PISCES": {
        "alkalinity": ("Alkalini",),
        "ammonium": ("NH4",),
        "chlorophyll": ("NCHL+DCHL",),
        "co2flux": ("Cflx",),
        "dic": ("DIC",),
        "doc": ("DOC",),
        "poc": ("POC+GOC",),
        "nitrate": ("NO3",),
        "oxygen": ("O2",),
        "pco2": ("pCO2",),
        "ph": ("PH",),
        "phosphate": ("PO4",),
        "silicate": ("Si",),
    },
    "NEMO-MEDUSA": {
        "alkalinity": ("ALK",),
        "chlorophyll": ("CHN+CHD",),
        "co2flux": ("CO2FLUX",),
        "dic": ("DiC",),
        "nitrate": ("DIN",),
        "omegaaragonite": ("OM_ARG3",),
        "omegacalcite": ("OM_CAL3",),
        "oxygen": ("OXY",),
        "pco2": ("PCO2W",),
        "silicate": ("SIL",),
    },
    "BFM": {
        "alkalinity": ("O3h",),
        "ammonium": ("N4n",),
        "chlorophyll": ("P1l+P2l+P3l+P4l",),
        "dic": ("O3c",),
        "doc": ("R1c+R2c+R3c",),
        "nitrate": ("N3n",),
        "omegaaragonite": ("OArag",),
        "omegacalcite": ("OCalc",),
        "oxygen": ("O2o",),
        "ph": ("pH",),
        "phosphate": ("N1p",),
        "silicate": ("N5s",),
    },
    "CESM-MARBL": {
        "alkalinity": ("ALK",),
        "ammonium": ("NH4",),
        "chlorophyll": ("totChl",),
        "co2flux": ("FG_CO2",),
        "dic": ("DIC",),
        "doc": ("DOC",),
        "nitrate": ("NO3",),
        "omegaaragonite": ("CO3/co3_sat_arag",),
        "omegacalcite": ("CO3/co3_sat_calc",),
        "oxygen": ("O2",),
        "pco2": ("pCO2SURF",),
        "ph": ("PH",),
        "phosphate": ("PO4",),
        "silicate": ("SiO3",),
    },
    "GFDL-COBALT": {
        "alkalinity": ("alk",),
        "ammonium": ("nh4",),
        "chlorophyll": ("chl",),
        "dic": ("dic",),
        "nitrate": ("no3",),
        "oxygen": ("o2",),
        "phosphate": ("po4",),
        "silicate": ("sio4",),
    },
    "HAMOCC": {
        "alkalinity": ("alkali",),
        "dic": ("sco212",),
        "nitrate": ("ano3",),
        "oxygen": ("oxygen",),
        "phosphate": ("phosph",),
        "silicate": ("silica",),
    },
    "ROMS-Fennel": {
        "alkalinity": ("alkalinity",),
        "ammonium": ("NH4",),
        "chlorophyll": ("chlorophyll",),
        "dic": ("TIC",),
        "nitrate": ("NO3",),
        "oxygen": ("oxygen",),
    },
}

# units of a flux, or of an amount per area: never those of a concentration
_FLUX_UNITS = re.compile(
    r"(?:m-2|m\^-2|m\*\*-2|m2-1|/m2|/m\^2|/m\*\*2|s-1|s\^-1|/s\b|/sec|d-1|/d\b|day-1|/day|yr-1|/yr|year-1|/year"
    # a concentration times a depth, how some models write a depth-integral
    r"|/m\*\*3\*m\b|/m\^3\*m\b|/m3\*m\b|m-3\*?m\b)"
)

# how each variable OceanVal filled in was found, by (variable, model variable)
_found_by = {}


def found_by(variable, model_variable):
    """How identify found model_variable for variable ("its standard_name",
    "the names ERSEM gives its variables", ...), or None if it did not (the
    long_name rules did, or the user typed it)."""
    return _found_by.get((variable, model_expr.normalise(model_variable or "")))


def is_flux(units):
    """Whether units are those of a flux, or of an amount per area."""
    return bool(units) and bool(_FLUX_UNITS.search(str(units).replace(" ", "")))


# what a variable's units must be like, for the variables that have a rule: one
# model variable can be named like another, but is a flux or a fraction. The
# others only must not be a flux. Unknown units (none given) never rule a variable out.
_FRACTIONS = {"%", "percent", "1", "-", "fraction", "ratio", "none", "(0-1)"}
_PRESSURES = ("atm", "pa", "1e-6", "ppm")


def units_fit(variable, units):
    """Whether a model variable with units can hold variable."""
    text = str(units).replace(" ", "").lower() if units is not None else ""
    if text in ("", "nan"):
        return True
    if variable == "co2flux":
        return is_flux(units)
    if variable == "o2sat":
        # the saturation as a percentage or fraction, not a concentration or a flux
        return text in _FRACTIONS
    if variable == "pco2":
        return not is_flux(units) and (any(x in text for x in _PRESSURES) or text in _FRACTIONS)
    return not is_flux(units)


def fitting(variable, contents):
    """The rows of contents whose units suit variable, for the variables with
    a rule of their own (those whose long_names are easily taken for another
    quantity); every row for the rest, whose matches have always been as they are."""
    if variable not in (
        "aou", "bacteria", "co2flux", "diatoms", "doc", "mesozooplankton", "microzooplankton", "o2sat",
        "pco2", "picophytoplankton", "poc",
    ):
        return contents
    units = _units_of(contents)
    return contents.loc[[units_fit(variable, units.get(str(x))) for x in contents.variable]]


def _units_of(contents):
    """{variable: units} of a file's contents (nctoolkit calls the column unit)."""
    column = "unit" if "unit" in contents.columns else ("units" if "units" in contents.columns else None)
    if column is None:
        return {}
    return {str(v): ("" if str(u) == "nan" else str(u)) for v, u in zip(contents.variable, contents[column])}


def identify(variable, contents, standard_names=None):
    """The model variable of contents (nctoolkit's, or the like: variable,
    long_name, unit) that holds variable, as a name, a sum or a formula, and
    how it was found, or (None, None). standard_names is {model variable:
    standard_name}, for what the contents do not have."""
    names = [str(x) for x in contents.variable]
    present = set(names)
    units = _units_of(contents)
    standard_names = dict(standard_names or {})
    if "standard_name" in contents.columns:
        for name, standard in zip(contents.variable, contents.standard_name):
            if isinstance(standard, str) and standard and str(name) not in standard_names:
                standard_names[str(name)] = standard

    def usable(expression):
        parts = model_expr.names(expression)
        return parts and all(x in present for x in parts) and all(units_fit(variable, units.get(x)) for x in parts)

    # 1. the standard_name
    wanted = STANDARD_NAMES.get(variable, ())
    matched = list(dict.fromkeys(x for x in names if standard_names.get(x) in wanted and usable(x)))
    if len(matched) == 1:
        return _keep(variable, matched[0], "its standard_name")
    if len(matched) > 1 and variable == "chlorophyll":
        # the phytoplankton types' chlorophyll, which add up to all of it
        return _keep(variable, "+".join(matched), "their standard_name")

    # 2. the names models give their variables
    found = {}
    for family, table in MODEL_NAMES.items():
        for expression in table.get(variable, ()):
            if usable(expression):
                found.setdefault(model_expr.normalise(expression), family)
                break
    if len(found) == 1:
        expression, family = next(iter(found.items()))
        return _keep(variable, expression, f"the names {family} gives its variables")
    return None, None


def _keep(variable, model_variable, how):
    _found_by[(variable, model_expr.normalise(model_variable))] = how
    return model_variable, how


def standard_names_of(path):
    """{variable: standard_name} of a netCDF file, read with xarray, or {} if it
    cannot be read. Holds oceanval.netcdf_lock.LOCK, as the window's threads read files."""
    import xarray as xr

    from oceanval.netcdf_lock import LOCK

    try:
        with LOCK, xr.open_dataset(path, decode_times=False, decode_cf=False) as ds:
            return {
                str(name): str(v.attrs["standard_name"])
                for name, v in ds.variables.items()
                if isinstance(v.attrs.get("standard_name"), str)
            }
    except Exception:
        return {}
