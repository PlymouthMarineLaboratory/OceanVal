"""Every OceanVal built-in recipe, in one place.

Each block below registers one observational dataset. They are not meant to
be run all at once: copy the blocks for the variables you care about into
your own script, set `model_variable` to the name used in your model output,
then run the matchup and validate step at the bottom.

Recipes download the observational data for you when matchup() is called.
See https://PlymouthMarineLaboratory.github.io/OceanVal/recipes.html
"""

import oceanval


# ==========================================================================
# Global
# ==========================================================================

# Alkalinity - GLODAPv2.2016b (https://www.glodap.info/)
# recipe: 'glodap'
# Annual climatology covering 1972–2013, surface-only. Reported in
# micromoles per kilogram. See the GLODAPv2 reference
# (https://doi.org/10.5194/essd-8-325-2016).
oceanval.add_gridded_comparison(
    name="alkalinity",
    model_variable="talk",
    recipe={"alkalinity": "glodap"},
    climatology=True,
)

# Chlorophyll - Ocean Colour CCI (https://esa-oceancolour-cci.org/)
# recipe: 'occci'
# Surface chlorophyll, reported in milligrams per cubic metre. The occci
# recipe also provides KD490.
oceanval.add_gridded_comparison(
    name="chlorophyll",
    model_variable="chl",
    recipe={"chlorophyll": "occci"},
    climatology=False,
)

# KD490 - Ocean Colour CCI (https://esa-oceancolour-cci.org/)
# recipe: 'occci'
# Diffuse attenuation coefficient at 490 nm, reported in inverse metres.
oceanval.add_gridded_comparison(
    name="kd490",
    model_variable="kd490",
    recipe={"kd490": "occci"},
    climatology=False,
)

# Nitrate - World Ocean Atlas 2023 (https://www.ncei.noaa.gov/products/world-ocean-atlas)
# recipe: 'woa23'
# Vertically resolved to ~800 m. WOA23 nutrients use a single long-term
# climatology — see the WOA23 note below for temperature/salinity decadal
# periods.
oceanval.add_gridded_comparison(
    name="nitrate",
    model_variable="no3",
    recipe={"nitrate": "woa23"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Oxygen - World Ocean Atlas 2023 (https://www.ncei.noaa.gov/products/world-ocean-atlas)
# recipe: 'woa23'
# Vertically resolved to ~800 m.
oceanval.add_gridded_comparison(
    name="oxygen",
    model_variable="o2",
    recipe={"oxygen": "woa23"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# pH - GLODAPv2.2016b (https://www.glodap.info/)
# recipe: 'glodap'
# Annual climatology, 1972–2013, surface-only. Reported on the total scale.
oceanval.add_gridded_comparison(
    name="ph",
    model_variable="ph",
    recipe={"ph": "glodap"},
    climatology=True,
)

# Phosphate - World Ocean Atlas 2023 (https://www.ncei.noaa.gov/products/world-ocean-atlas)
# recipe: 'woa23'
# Vertically resolved to ~800 m.
oceanval.add_gridded_comparison(
    name="phosphate",
    model_variable="po4",
    recipe={"phosphate": "woa23"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Salinity - World Ocean Atlas 2023 (https://www.ncei.noaa.gov/products/world-ocean-atlas)
# recipe: 'woa23'
# Uses a decadal climatology — start/end must fall within one supported
# period (see below).
oceanval.add_gridded_comparison(
    name="salinity",
    model_variable="so",
    recipe={"salinity": "woa23"},
    start=2005, end=2014,
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Silicate - World Ocean Atlas 2023 (https://www.ncei.noaa.gov/products/world-ocean-atlas)
# recipe: 'woa23'
# Vertically resolved to ~800 m.
oceanval.add_gridded_comparison(
    name="silicate",
    model_variable="si",
    recipe={"silicate": "woa23"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Temperature - COBE-SST 2 (https://psl.noaa.gov/data/gridded/data.cobe2.html)
# recipe: 'cobe2'
# Monthly sea surface temperature, useful for validating against a full time
# series rather than a climatology.
oceanval.add_gridded_comparison(
    name="temperature",
    model_variable="thetao",
    recipe={"temperature": "cobe2"},
    climatology=False,
)

# Temperature - World Ocean Atlas 2023 (https://www.ncei.noaa.gov/products/world-ocean-atlas)
# recipe: 'woa23'
# Uses a decadal climatology, vertically resolved to ~800 m. start/end must
# fall within one supported period (see below).
oceanval.add_gridded_comparison(
    name="temperature",
    model_variable="thetao",
    recipe={"temperature": "woa23"},
    start=2005, end=2014,
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# ==========================================================================
# Northwest European Shelf
# ==========================================================================

# Ammonium - North Sea Biogeochemical Climatology
# recipe: 'nsbc'
oceanval.add_gridded_comparison(
    name="ammonium",
    model_variable="ammonium",
    recipe={"ammonium": "nsbc"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Chlorophyll - North Sea Biogeochemical Climatology
# recipe: 'nsbc'
oceanval.add_gridded_comparison(
    name="chlorophyll",
    model_variable="chlorophyll",
    recipe={"chlorophyll": "nsbc"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Nitrate - North Sea Biogeochemical Climatology
# recipe: 'nsbc'
oceanval.add_gridded_comparison(
    name="nitrate",
    model_variable="nitrate",
    recipe={"nitrate": "nsbc"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Oxygen - North Sea Biogeochemical Climatology
# recipe: 'nsbc'
oceanval.add_gridded_comparison(
    name="oxygen",
    model_variable="oxygen",
    recipe={"oxygen": "nsbc"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Phosphate - North Sea Biogeochemical Climatology
# recipe: 'nsbc'
oceanval.add_gridded_comparison(
    name="phosphate",
    model_variable="phosphate",
    recipe={"phosphate": "nsbc"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Salinity - North Sea Biogeochemical Climatology
# recipe: 'nsbc'
oceanval.add_gridded_comparison(
    name="salinity",
    model_variable="salinity",
    recipe={"salinity": "nsbc"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Silicate - North Sea Biogeochemical Climatology
# recipe: 'nsbc'
oceanval.add_gridded_comparison(
    name="silicate",
    model_variable="silicate",
    recipe={"silicate": "nsbc"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Temperature - North Sea Biogeochemical Climatology
# recipe: 'nsbc'
oceanval.add_gridded_comparison(
    name="temperature",
    model_variable="temperature",
    recipe={"temperature": "nsbc"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)


# ==========================================================================
# Northwest European Shelf - ICES point observations
# ==========================================================================

# Temperature - ICES Oceanographic database (https://ocean.ices.dk)
# recipe: 'ices'
# CTD profiles and bottle data, reported in degrees Celsius.
# Only observations with good (quality flag 0) flags are kept.
oceanval.add_point_comparison(
    name="temperature",
    model_variable="temperature",
    recipe={"temperature": "ices"},
    vertical=False,  # set True to validate the full water column
)

# Salinity - ICES Oceanographic database (https://ocean.ices.dk)
# recipe: 'ices'
# CTD profiles and bottle data, reported in practical salinity units.
# Only observations with good (quality flag 0) flags are kept.
oceanval.add_point_comparison(
    name="salinity",
    model_variable="salinity",
    recipe={"salinity": "ices"},
    vertical=False,  # set True to validate the full water column
)

# Total Alkalinity - ICES Oceanographic database (https://ocean.ices.dk)
# recipe: 'ices'
# Bottle and low resolution CTD data, reported in milliequivalents per litre.
# Only observations with good (quality flag 0) flags are kept.
oceanval.add_point_comparison(
    name="alkalinity",
    model_variable="alkalinity",
    recipe={"alkalinity": "ices"},
    vertical=False,  # set True to validate the full water column
)

# Ammonium - ICES Oceanographic database (https://ocean.ices.dk)
# recipe: 'ices'
# Bottle and low resolution CTD data, reported in micromoles per litre.
# Only observations with good (quality flag 0) flags are kept.
oceanval.add_point_comparison(
    name="ammonium",
    model_variable="ammonium",
    recipe={"ammonium": "ices"},
    vertical=False,  # set True to validate the full water column
)

# Chlorophyll - ICES Oceanographic database (https://ocean.ices.dk)
# recipe: 'ices'
# Bottle and low resolution CTD data, reported in micrograms per litre.
# Only observations with good (quality flag 0) flags are kept.
oceanval.add_point_comparison(
    name="chlorophyll",
    model_variable="chlorophyll",
    recipe={"chlorophyll": "ices"},
    vertical=False,  # set True to validate the full water column
)

# Nitrate - ICES Oceanographic database (https://ocean.ices.dk)
# recipe: 'ices'
# Bottle and low resolution CTD data, reported in micromoles per litre.
# Only observations with good (quality flag 0) flags are kept.
oceanval.add_point_comparison(
    name="nitrate",
    model_variable="nitrate",
    recipe={"nitrate": "ices"},
    vertical=False,  # set True to validate the full water column
)

# Oxygen - ICES Oceanographic database (https://ocean.ices.dk)
# recipe: 'ices'
# Bottle and low resolution CTD data, reported in millilitres per litre.
# Only observations with good (quality flag 0) flags are kept.
oceanval.add_point_comparison(
    name="oxygen",
    model_variable="oxygen",
    recipe={"oxygen": "ices"},
    vertical=False,  # set True to validate the full water column
)

# pH - ICES Oceanographic database (https://ocean.ices.dk)
# recipe: 'ices'
# Bottle and low resolution CTD data, reported in pH units.
# Only observations with good (quality flag 0) flags are kept.
oceanval.add_point_comparison(
    name="ph",
    model_variable="ph",
    recipe={"ph": "ices"},
    vertical=False,  # set True to validate the full water column
)

# Phosphate - ICES Oceanographic database (https://ocean.ices.dk)
# recipe: 'ices'
# Bottle and low resolution CTD data, reported in micromoles per litre.
# Only observations with good (quality flag 0) flags are kept.
oceanval.add_point_comparison(
    name="phosphate",
    model_variable="phosphate",
    recipe={"phosphate": "ices"},
    vertical=False,  # set True to validate the full water column
)

# Silicate - ICES Oceanographic database (https://ocean.ices.dk)
# recipe: 'ices'
# Bottle and low resolution CTD data, reported in micromoles per litre.
# Only observations with good (quality flag 0) flags are kept.
oceanval.add_point_comparison(
    name="silicate",
    model_variable="silicate",
    recipe={"silicate": "ices"},
    vertical=False,  # set True to validate the full water column
)


# ==========================================================================
# Matchup and report
# ==========================================================================

# Pair the registered observations with your model output, then build the
# report. start/end must match the years your simulation covers - and for
# the World Ocean Atlas temperature and salinity recipes they must sit
# inside a single decadal period.
#
# If you set vertical=True on any recipe above, matchup also needs
# thickness - either "z_level" or the name of a cell thickness variable.
oceanval.matchup(sim_dir="/path/to/model/output", start=1995, end=2004)

# word=True and pdf=True also write Word and PDF versions of the report.
oceanval.validate()
