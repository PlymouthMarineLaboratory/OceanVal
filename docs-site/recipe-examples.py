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
# Annual climatology covering 1972–2013, vertically resolved to 5500 m.
# Reported in micromoles per kilogram. See the GLODAPv2 reference
# (https://doi.org/10.5194/essd-8-325-2016).
oceanval.add_gridded_comparison(
    name="alkalinity",
    model_variable="talk",
    recipe={"alkalinity": "glodap"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
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

# Dissolved inorganic carbon - GLODAPv2.2016b (https://www.glodap.info/)
# recipe: 'glodap'
# Annual climatology, 1972–2013, vertically resolved to 5500 m.
# Reported in micromoles per kilogram.
oceanval.add_gridded_comparison(
    name="dic",
    model_variable="dissic",
    recipe={"dic": "glodap"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
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

# Nitrate - GLODAPv2.2016b (https://www.glodap.info/)
# recipe: 'glodap'
# Annual climatology, 1972–2013, vertically resolved to 5500 m.
# Reported in micromoles per kilogram.
oceanval.add_gridded_comparison(
    name="nitrate",
    model_variable="no3",
    recipe={"nitrate": "glodap"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Aragonite saturation state - GLODAPv2.2016b (https://www.glodap.info/)
# recipe: 'glodap'
# Annual climatology, 1972–2013, vertically resolved to 5500 m.
# Calculated at in situ temperature and pressure; no units.
oceanval.add_gridded_comparison(
    name="omegaaragonite",
    model_variable="omega_arg",
    recipe={"omegaaragonite": "glodap"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Calcite saturation state - GLODAPv2.2016b (https://www.glodap.info/)
# recipe: 'glodap'
# Annual climatology, 1972–2013, vertically resolved to 5500 m.
# Calculated at in situ temperature and pressure; no units.
oceanval.add_gridded_comparison(
    name="omegacalcite",
    model_variable="omega_cal",
    recipe={"omegacalcite": "glodap"},
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

# Oxygen - GLODAPv2.2016b (https://www.glodap.info/)
# recipe: 'glodap'
# Annual climatology, 1972–2013, vertically resolved to 5500 m.
# Reported in micromoles per kilogram.
oceanval.add_gridded_comparison(
    name="oxygen",
    model_variable="o2",
    recipe={"oxygen": "glodap"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# pH - GLODAPv2.2016b (https://www.glodap.info/)
# recipe: 'glodap'
# Annual climatology, 1972–2013, vertically resolved to 5500 m. Reported on
# the total scale at in situ temperature and pressure.
oceanval.add_gridded_comparison(
    name="ph",
    model_variable="ph",
    recipe={"ph": "glodap"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
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

# Phosphate - GLODAPv2.2016b (https://www.glodap.info/)
# recipe: 'glodap'
# Annual climatology, 1972–2013, vertically resolved to 5500 m.
# Reported in micromoles per kilogram.
oceanval.add_gridded_comparison(
    name="phosphate",
    model_variable="po4",
    recipe={"phosphate": "glodap"},
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

# Silicate - GLODAPv2.2016b (https://www.glodap.info/)
# recipe: 'glodap'
# Annual climatology, 1972–2013, vertically resolved to 5500 m.
# Reported in micromoles per kilogram.
oceanval.add_gridded_comparison(
    name="silicate",
    model_variable="si",
    recipe={"silicate": "glodap"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Apparent oxygen utilization - World Ocean Atlas 2023 (https://www.ncei.noaa.gov/products/world-ocean-atlas)
# recipe: 'woa23'
# The oxygen a water has lost since it left the surface, a measure of how
# much organic matter has been respired. Reported in micromoles per kilogram.
oceanval.add_gridded_comparison(
    name="aou",
    model_variable="aou",
    recipe={"aou": "woa23"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Oxygen saturation - World Ocean Atlas 2023 (https://www.ncei.noaa.gov/products/world-ocean-atlas)
# recipe: 'woa23'
# Dissolved oxygen as a percentage of its saturation value at the water's
# temperature and salinity.
oceanval.add_gridded_comparison(
    name="o2sat",
    model_variable="o2sat",
    recipe={"o2sat": "woa23"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Surface ocean pCO2 - MPI-SOM-FFN v2025 (https://www.ncei.noaa.gov/access/ocean-carbon-acidification-data-system/oceans/SPCO2_1982_present_ETH_SOM_FFN.html)
# recipe: 'mpisom'
# Monthly climatology of a gap-free reconstruction (a neural network trained on
# SOCAT's fCO2), 1 degree, surface only. Reported in micro-atmospheres.
oceanval.add_gridded_comparison(
    name="pco2",
    model_variable="spco2",
    recipe={"pco2": "mpisom"},
    climatology=True,
)

# Air-sea CO2 flux - MPI-SOM-FFN v2025 (https://www.ncei.noaa.gov/access/ocean-carbon-acidification-data-system/oceans/SPCO2_1982_present_ETH_SOM_FFN.html)
# recipe: 'mpisom'
# Monthly climatology of the flux worked out from the reconstructed pCO2, in
# moles of carbon per square metre per year. The product is negative where the
# ocean takes CO2 up: OceanVal reverses it, so that, as in CMIP6's fgco2 and
# ERSEM's fair, positive is into the ocean.
oceanval.add_gridded_comparison(
    name="co2flux",
    model_variable="fgco2",
    recipe={"co2flux": "mpisom"},
    climatology=True,
)

# Surface ocean fCO2 - SOCATv2026 (https://www.socat.info/)
# recipe: 'socat'
# Monthly 1 degree means of ship and mooring measurements (the cruise-weighted
# mean fCO2, flags A to D, WOCE 2), 1970 to 2025, with nothing where nothing
# was measured. It is fCO2, which is within about 0.5% of pCO2.
oceanval.add_gridded_comparison(
    name="pco2",
    model_variable="spco2",
    recipe={"pco2": "socat"},
    climatology=False,
)

# Mesozooplankton carbon biomass - NOAA COPEPOD (https://www.st.nmfs.noaa.gov/copepod/biomass/biomass-fields.html)
# recipe: 'copepod'
# Monthly climatology of net-sampled zooplankton (330 um mesh) as carbon, on a
# 0.25 degree grid where there are samples, in milligrams of carbon per cubic
# metre, a mean over the top 200 m: the simulation is averaged over 0-200 m too
# (or the sea floor, where that is shallower), which needs the cell thickness.
oceanval.add_gridded_comparison(
    name="mesozooplankton",
    model_variable="zmeso",
    recipe={"mesozooplankton": "copepod"},
    climatology=True,
)

# Diatom carbon biomass - MAREDAT (https://doi.org/10.1594/PANGAEA.777384)
# recipe: 'maredat'
# Monthly climatology of diatom samples as carbon, on a 1 degree grid of 33
# depths to 5500 m, with nothing where nothing was sampled, in milligrams
# of carbon per cubic metre. The samples are sparse and span orders of
# magnitude: read the comparison in log10.
oceanval.add_gridded_comparison(
    name="diatoms",
    model_variable="P1_c",
    recipe={"diatoms": "maredat"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Picophytoplankton carbon biomass - MAREDAT (https://doi.org/10.1594/PANGAEA.777385)
# recipe: 'maredat'
# Monthly climatology of Prochlorococcus, Synechococcus and picoeukaryote samples as carbon, on a 1 degree grid of 33
# depths to 5500 m, with nothing where nothing was sampled, in milligrams
# of carbon per cubic metre. The samples are sparse and span orders of
# magnitude: read the comparison in log10.
oceanval.add_gridded_comparison(
    name="picophytoplankton",
    model_variable="P3_c",
    recipe={"picophytoplankton": "maredat"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Heterotrophic bacteria carbon biomass - MAREDAT (https://doi.org/10.1594/PANGAEA.779142)
# recipe: 'maredat'
# Monthly climatology of heterotrophic bacteria and archaea samples as carbon, on a 1 degree grid of 33
# depths to 5500 m, with nothing where nothing was sampled, in milligrams
# of carbon per cubic metre. The samples are sparse and span orders of
# magnitude: read the comparison in log10.
oceanval.add_gridded_comparison(
    name="bacteria",
    model_variable="B1_c",
    recipe={"bacteria": "maredat"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Microzooplankton carbon biomass - MAREDAT (https://doi.org/10.1594/PANGAEA.779970)
# recipe: 'maredat'
# Monthly climatology of ciliate and heterotrophic dinoflagellate samples as carbon, on a 1 degree grid of 33
# depths to 5500 m, with nothing where nothing was sampled, in milligrams
# of carbon per cubic metre. The samples are sparse and span orders of
# magnitude: read the comparison in log10.
oceanval.add_gridded_comparison(
    name="microzooplankton",
    model_variable="Z5_c",
    recipe={"microzooplankton": "maredat"},
    climatology=True,
    vertical=False,  # set True to validate the full water column
)

# Mesozooplankton carbon biomass - MAREDAT (https://doi.org/10.1594/PANGAEA.785501)
# recipe: 'maredat'
# Monthly climatology of net-sampled mesozooplankton as carbon, on a 1 degree grid of 33
# depths to 5500 m, with nothing where nothing was sampled, in milligrams
# of carbon per cubic metre. The samples are sparse and span orders of
# magnitude: read the comparison in log10.
oceanval.add_gridded_comparison(
    name="mesozooplankton",
    model_variable="Z4_c",
    recipe={"mesozooplankton": "maredat"},
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
# Global - GLODAPv3 bottle data
# ==========================================================================

# Total Alkalinity - GLODAPv3 bottle data (https://glodap.info)
# recipe: 'glodapv3'
# Bottle samples, 1972–2023, reported in micromoles per kilogram.
# Only samples with good (WOCE flag 2) flags are kept.
oceanval.add_point_comparison(
    name="alkalinity",
    model_variable="alkalinity",
    recipe={"alkalinity": "glodapv3"},
    vertical=False,  # set True to validate the full water column
)

# Chlorophyll - GLODAPv3 bottle data (https://glodap.info)
# recipe: 'glodapv3'
# Bottle samples, 1972–2023, reported in micrograms per kilogram.
# Only samples with good (WOCE flag 2) flags are kept.
oceanval.add_point_comparison(
    name="chlorophyll",
    model_variable="chlorophyll",
    recipe={"chlorophyll": "glodapv3"},
    vertical=False,  # set True to validate the full water column
)

# Dissolved inorganic carbon - GLODAPv3 bottle data (https://glodap.info)
# recipe: 'glodapv3'
# Bottle samples, 1972–2023, reported in micromoles per kilogram.
# Only samples with good (WOCE flag 2) flags are kept.
oceanval.add_point_comparison(
    name="dic",
    model_variable="dic",
    recipe={"dic": "glodapv3"},
    vertical=False,  # set True to validate the full water column
)

# Dissolved organic carbon - GLODAPv3 bottle data (https://glodap.info)
# recipe: 'glodapv3'
# Bottle samples, 1972–2023, reported in micromoles per litre.
# Only samples with good (WOCE flag 2) flags are kept.
oceanval.add_point_comparison(
    name="doc",
    model_variable="doc",
    recipe={"doc": "glodapv3"},
    vertical=False,  # set True to validate the full water column
)

# Nitrate - GLODAPv3 bottle data (https://glodap.info)
# recipe: 'glodapv3'
# Bottle samples, 1972–2023, reported in micromoles per kilogram.
# Only samples with good (WOCE flag 2) flags are kept.
oceanval.add_point_comparison(
    name="nitrate",
    model_variable="nitrate",
    recipe={"nitrate": "glodapv3"},
    vertical=False,  # set True to validate the full water column
)

# Oxygen - GLODAPv3 bottle data (https://glodap.info)
# recipe: 'glodapv3'
# Bottle samples, 1972–2023, reported in micromoles per kilogram.
# Only samples with good (WOCE flag 2) flags are kept.
oceanval.add_point_comparison(
    name="oxygen",
    model_variable="oxygen",
    recipe={"oxygen": "glodapv3"},
    vertical=False,  # set True to validate the full water column
)

# pH - GLODAPv3 bottle data (https://glodap.info)
# recipe: 'glodapv3'
# Bottle samples, 1972–2023, on the total scale at in situ conditions.
# Only samples with good (WOCE flag 2) flags are kept.
oceanval.add_point_comparison(
    name="ph",
    model_variable="ph",
    recipe={"ph": "glodapv3"},
    vertical=False,  # set True to validate the full water column
)

# Phosphate - GLODAPv3 bottle data (https://glodap.info)
# recipe: 'glodapv3'
# Bottle samples, 1972–2023, reported in micromoles per kilogram.
# Only samples with good (WOCE flag 2) flags are kept.
oceanval.add_point_comparison(
    name="phosphate",
    model_variable="phosphate",
    recipe={"phosphate": "glodapv3"},
    vertical=False,  # set True to validate the full water column
)

# Silicate - GLODAPv3 bottle data (https://glodap.info)
# recipe: 'glodapv3'
# Bottle samples, 1972–2023, reported in micromoles per kilogram.
# Only samples with good (WOCE flag 2) flags are kept.
oceanval.add_point_comparison(
    name="silicate",
    model_variable="silicate",
    recipe={"silicate": "glodapv3"},
    vertical=False,  # set True to validate the full water column
)

# Dissolved organic carbon - Hansell et al. DOM compilation (https://www.ncei.noaa.gov/data/oceans/ncei/ocads/metadata/0227166.html)
# recipe: 'hansell'
# Bottle samples, 1994–2021, reported in micromoles per kilogram.
# Only samples with good (WOCE flag 2) flags, and values above zero, are kept.
oceanval.add_point_comparison(
    name="doc",
    model_variable="doc",
    recipe={"doc": "hansell"},
    vertical=False,  # set True to validate the full water column
)

# Particulate organic carbon - Hansell et al. DOM compilation (https://www.ncei.noaa.gov/data/oceans/ncei/ocads/metadata/0227166.html)
# recipe: 'hansell'
# Bottle samples, 1995–2018, reported in micromoles per kilogram.
# Only samples with good (WOCE flag 2) flags, and values above zero, are kept.
oceanval.add_point_comparison(
    name="poc",
    model_variable="poc",
    recipe={"poc": "hansell"},
    vertical=False,  # set True to validate the full water column
)


# ==========================================================================
# European - ICES point observations
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
