from oceanval.quiet_nc import nc
import re
import pandas as pd
import os
import glob
import warnings
from oceanval.session import session_info
from oceanval.utils import loud_warning
from oceanval import erddap, model_expr, obs_convert, user_recipes


def _checked_model_variable(model_variable, name):
    """model_variable as OceanVal keeps it (see oceanval.model_expr: one
    variable, a sum or a formula, without spaces). Raises ValueError, saying
    what is wrong, if CDO's expr could not work it out. None is kept."""
    if model_variable is None:
        return None
    try:
        return model_expr.parse(model_variable).text
    except model_expr.ExpressionError as error:
        raise ValueError(
            f"The model variable for {name}, {str(model_variable).strip()}, cannot be used: {error.message}"
        ) from None

# whether registering a gridded comparison opens its observations on a
# THREDDS server to check them. The oceanval window turns it off, so that
# the matchups are shown without waiting on the servers: a server that is
# down is caught when the matchup is made (see gridded.remote_gridded_source)
check_servers = True


def read_point(ff, nrows = None):
    try:
        df = pd.read_csv(ff, nrows=nrows)
        return df
    except Exception:
        pass
    compressions = ['infer', 'gzip', 'bz2', 'zip', 'xz']
    for compression in compressions:
        try:
            df = pd.read_csv(ff, compression = compression, nrows=nrows)
            return df
        except Exception:
            pass
    raise ValueError(f"Could not read file {ff} with any of the following compression types: {compressions}.")


def gridded_files(obs_path):
    """The netCDF files of gridded observations, sorted.

    A path with a wildcard is expanded with ``glob.glob`` (never nctoolkit's
    glob, and ``**`` goes through subdirectories); a directory is searched
    recursively (nctoolkit's glob, ``dir/**/*.nc``); a file is used as it is.
    A path with no files gives an empty list.
    """
    if glob.has_magic(obs_path) and not os.path.exists(obs_path):
        return sorted(glob.glob(obs_path, recursive=True))
    if os.path.isdir(obs_path):
        obs_path = os.path.join(obs_path, "**", "*.nc")
    elif os.path.isfile(obs_path):
        return [obs_path]
    try:
        return sorted(nc.glob(obs_path, recursive=True))
    except ValueError:
        return []


def point_files(obs_path):
    """The csv files of a point comparison's observations.

    obs_path is one file, which is used as it is, or a directory, whose csv
    files are all used. A path that is neither, or a directory without csv
    files, gives no files.
    """
    if os.path.isfile(obs_path):
        return [obs_path]
    if os.path.isdir(obs_path):
        return sorted(glob.glob(os.path.join(obs_path, "*.csv")))
    return []


# the GLODAPv2.2016b mapped climatologies: the file (and variable) for each
# variable, all at NCEI
GLODAP_MAPPED_URL = "https://www.ncei.noaa.gov/data/oceans/archive/arc0221/0286118/1.1/data/0-data/GLODAPv2.2016b_MappedClimatologies"
GLODAP_MAPPED = {
    "alkalinity": "TAlk",
    "dic": "TCO2",
    "nitrate": "NO3",
    "omegaaragonite": "OmegaA",
    "omegacalcite": "OmegaC",
    "oxygen": "oxygen",
    "ph": "pHtsinsitutp",
    "phosphate": "PO4",
    "silicate": "silicate",
}
# the variables of the GLODAPv3 bottle data (see oceanval.glodap)
GLODAPV3_VARIABLES = (
    "alkalinity", "chlorophyll", "dic", "doc", "nitrate", "oxygen", "ph", "phosphate", "silicate",
)
# the variables of the Hansell et al. DOM compilation (see oceanval.hansell)
HANSELL_VARIABLES = ("doc", "poc")

# WOA23's apparent oxygen utilization and oxygen saturation: variable ->
# (the folder, the letter its files' names have, the variable in them)
WOA23_OXYGEN_VOLUME = {
    "aou": ("AOU", "A", "A_an"),
    "o2sat": ("o2sat", "O", "O_an"),
}
WOA23_OXYGEN_CITATION = (
    "Garcia, H.E., C. Bouchard, S.L. Cross, C.R. Paver, Z. Wang, J.R. Reagan, T.P. Boyer, R.A. Locarnini, "
    "A.V. Mishonov, O. Baranova, D. Seidov, and D. Dukhovskoy. World Ocean Atlas 2023, Volume 3: Dissolved "
    "Oxygen, Apparent Oxygen Utilization, and Oxygen Saturation. A. Mishonov, Tech. Ed. NOAA Atlas NESDIS 91, "
    "doi.org/10.25923/rb67-ns53"
)

# the recipes whose data is made into a netCDF file (see oceanval.obs_convert), by
# recipe key: the source, its citation, the file's address, the variable of the
# file for each of OceanVal's, and whether it is a climatology. depth_mean is
# the depths, in metres, the data is a mean of.
BUILT_RECIPES = {
    "mpisom": {
        "source": "MPI-SOM-FFN",
        "source_info": (
            "Jersild, A., Landschützer, P., Gruber, N., and Bakker, D. C. E.: An observation-based global "
            "monthly gridded sea surface pCO2 and air-sea CO2 flux product from 1982 onward and its monthly "
            "climatology (NCEI Accession 0160558), version 2025, NOAA National Centers for Environmental "
            "Information, https://doi.org/10.7289/v5z899n6, 2026. Method: Landschützer, P., Gruber, N., and "
            "Bakker, D. C. E.: Decadal variations and trends of the global ocean carbon sink, Global "
            "Biogeochem. Cycles, 30, 1396–1417, https://doi.org/10.1002/2015GB005359, 2016."
        ),
        "obs_path": obs_convert.MPISOM_URL,
        "variables": {"pco2": "spco2_clim", "co2flux": "fgco2_clim"},
        "climatology": True,
    },
    "socat": {
        "source": "SOCATv2026",
        "source_info": (
            "Bakker, D. C. E., et al.: Surface Ocean CO2 Atlas Database Version 2026 (SOCATv2026) (NCEI "
            "Accession 0315110), NOAA National Centers for Environmental Information, "
            "https://doi.org/10.25921/8dba-fr90, 2026. The monthly 1° gridded product, www.socat.info."
        ),
        "obs_path": obs_convert.SOCAT_URL,
        "variables": {"pco2": "fco2_ave_weighted"},
        "climatology": False,
    },
    "copepod": {
        "source": "COPEPOD",
        "source_info": (
            "O'Brien, T. D.: COPEPOD: A Global Plankton Database. NOAA Technical Memorandum "
            "NMFS-F/SPO-73, 2005. The COPEPOD-2012 zooplankton carbon biomass fields, NOAA NMFS, "
            "https://www.st.nmfs.noaa.gov/copepod/biomass/biomass-fields.html"
        ),
        "obs_path": obs_convert.COPEPOD_URL,
        "variables": {"mesozooplankton": "cmass"},
        "climatology": True,
        "depth_mean": (0, 200),
    },
    # a file for each group: obs_path and source_info are by variable
    "maredat": {
        "source": "MAREDAT",
        "source_info": {
            "diatoms": (
                "Leblanc, K., et al.: A global diatom database – abundance, biovolume and biomass in the "
    {"temperature": "oisst"},
    {"temperature": "coraltemp"},
                "world ocean, Earth Syst. Sci. Data, 4, 149–165, https://doi.org/10.5194/essd-4-149-2012, "
                "2012. Data: https://doi.org/10.1594/PANGAEA.777384"
            ),
            "picophytoplankton": (
                "Buitenhuis, E. T., et al.: Picophytoplankton biomass distribution in the global ocean, "
                "Earth Syst. Sci. Data, 4, 37–46, https://doi.org/10.5194/essd-4-37-2012, 2012. Data: "
                "https://doi.org/10.1594/PANGAEA.777385"
            ),
            "bacteria": (
                "Buitenhuis, E. T., Li, W. K. W., Lomas, M. W., Karl, D. M., Landry, M. R., and Jacquet, S.: "
                "Picoheterotroph (Bacteria and Archaea) biomass distribution in the global ocean, Earth Syst. "
                "Sci. Data, 4, 101–106, https://doi.org/10.5194/essd-4-101-2012, 2012. Data: "
                "https://doi.org/10.1594/PANGAEA.779142"
            ),
            "microzooplankton": (
                "Buitenhuis, E. T., Rivkin, R. B., Sailley, S., and Le Quéré, C.: Biogeochemical fluxes "
                "through microzooplankton, Global Biogeochem. Cycles, 24, GB4015, "
                "https://doi.org/10.1029/2009GB003601, 2010. Data: https://doi.org/10.1594/PANGAEA.779970"
            ),
            "mesozooplankton": (
                "Moriarty, R. and O'Brien, T. D.: Distribution of mesozooplankton biomass in the global "
                "ocean, Earth Syst. Sci. Data, 5, 45–55, https://doi.org/10.5194/essd-5-45-2013, 2013. "
                "Data: https://doi.org/10.1594/PANGAEA.785501"
            ),
        },
        "obs_path": dict(obs_convert.MAREDAT_URLS),
        "variables": {name: "biomass" for name in obs_convert.MAREDAT_URLS},
        "climatology": True,
    },
}

recipe_list = [
    {"chlorophyll": "occci"},
    {"oxygen": "woa23"},
    {"temperature": "cobe2"},
    {"salinity": "woa23"},
    {"nitrate": "woa23"},
    {"ammonium": "nsbc"},
    {"phosphate": "woa23"},
    {"silicate": "woa23"},
    {"aou": "woa23"},
    {"o2sat": "woa23"},
    {"pco2": "mpisom"},
    {"co2flux": "mpisom"},
    {"pco2": "socat"},
    {"mesozooplankton": "copepod"},
    {"diatoms": "maredat"},
    {"picophytoplankton": "maredat"},
    {"bacteria": "maredat"},
    {"microzooplankton": "maredat"},
    {"mesozooplankton": "maredat"},
    {"kd490": "occci"},
    {"ph": "glodap"},
    {"alkalinity": "glodap"},
    {"nitrate": "glodap"},
    {"phosphate": "glodap"},
    {"silicate": "glodap"},
    {"oxygen": "glodap"},
    {"dic": "glodap"},
    {"omegacalcite": "glodap"},
    {"omegaaragonite": "glodap"},
    {"chlorophyll": "nsbc"},
    {"oxygen": "nsbc"},
    {"temperature": "nsbc"},
    {"salinity": "nsbc"},
    {"nitrate": "nsbc"},
    {"ammonium": "nsbc"},
    {"phosphate": "nsbc"},
    {"silicate": "nsbc"},
]


def find_recipe(x, start=None, end=None, cwd=None):
    """What a recipe, such as {"nitrate": "woa23"}, stands for.

    Recipes OceanVal comes with are worked out here. Any other is looked for
    in the .oceanvalrc files (see oceanval.user_recipes), those of the
    directory cwd (the one worked in, by default) and of the home directory.
    """
    output = dict()
    if len(x.keys()) != 1:
        raise ValueError("Recipe dictionary must have exactly one key")

    name = list(x.keys())[0]
    value = x[name]
    if value is None:
        raise ValueError(f"Recipe value for {name} is not valid")

    output["vertical"] = None
    output["point"] = False

    labels = user_recipes.VARIABLE_LABELS.get(str(name).lower())
    if labels is not None:
        output["short_name"], output["long_name"], output["short_title"] = labels

    value = str(value).lower()
    name = str(name)

    if value not in user_recipes.BUILTIN_SOURCES:
        found = user_recipes.find(name, value, cwd)
        if found is not None:
            return found
        raise ValueError(
            f"Recipe value {value} is not valid for recipe name {name}. It is not one of "
            "OceanVal's recipes, nor is there a recipe of yours for it in "
            f"{user_recipes.local_path(cwd)} or {user_recipes.global_path()}."
        )

    # which of OceanVal's own datasets this is, so its downloads can be kept
    # in the data store (see oceanval.data_store), which never holds yours
    output["builtin"] = value

    if value == "glodap":
        obs_variable = GLODAP_MAPPED.get(name.lower())
        if obs_variable is not None:
            output["obs_path"] = f"{GLODAP_MAPPED_URL}/GLODAPv2.2016b.{obs_variable}.nc"
            output["source"] = "GLODAPv2.2016b"
            output["source_info"] = "Lauvset, S. K., Key, R. M., Olsen, A., van Heuven, S., Velo, A., Lin, X., Schirnick, C., Kozyr, A., Tanhua, T., Hoppema, M., Jutterström, S., Steinfeldt, R., Jeansson, E., Ishii, M., Perez, F. F., Suzuki, T., and Watelet, S.: A new global interior ocean mapped climatology: the 1° × 1° GLODAP version 2, Earth Syst. Sci. Data, 8, 325–340, https://doi.org/10.5194/essd-8-325-2016, 2016."
            output["name"] = name.lower()
            output["thredds"] = False
            output["climatology"] = True
            output["obs_variable"] = obs_variable
            return output

    if value == "glodapv3":
        if name.lower() in GLODAPV3_VARIABLES:
            output["source"] = "GLODAPv3"
            output["source_info"] = "Lange, N., Lauvset, S. K., Carter, B. R., Humphreys, M. P., Woosley, R. J., Olsen, A., Bittig, H. C., Kozyr, A., Álvarez, M., Azetsu-Scott, K., Becker, S., Brown, P. J., Cotrim da Cunha, L., Dias, L., Hoppema, M., Ishii, M., Jeansson, E., Murata, A., Müller, J. D., Pérez, F. F., Schirnick, C., Steinfeldt, R., Ulfsbo, A., Velo, A., and Tanhua, T.: The Global Ocean Data Analysis Project version 3 (GLODAPv3) – an internally consistent biogeochemical data product for the world ocean, Earth Syst. Sci. Data Discuss., https://doi.org/10.5194/essd-2026-496, 2026. Data: NOAA NCEI Accession 0315582, https://doi.org/10.25921/m6tp-mj50."
            output["name"] = name.lower()
            output["point"] = True
            output["glodap_variable"] = name.lower()
    if value == "oisst":
        if name.lower() == "temperature":
            output["obs_path"] = "https://psl.noaa.gov/thredds/dodsC/Datasets/noaa.oisst.v2.highres/sst.mon.mean.nc"
            output["source"] = "OISST"
            output["source_info"] = "NOAA OI SST V2.1 High Resolution data provided by the NOAA PSL, Boulder, Colorado, USA, from their website at https://psl.noaa.gov/data/gridded/data.noaa.oisst.v2.highres.html. Huang, B., C. Liu, V. Banzon, E. Freeman, G. Graham, B. Hankins, T. Smith, and H.-M. Zhang, 2021: Improvements of the Daily Optimum Interpolation Sea Surface Temperature (DOISST) Version 2.1, Journal of Climate, 34, 2923-2939, https://doi.org/10.1175/JCLI-D-20-0166.1."
            output["name"] = name.lower()
            output["thredds"] = True
            output["climatology"] = False
            output["vertical"] = False
            output["obs_variable"] = "sst"
            return output

    if value == "coraltemp":
        if name.lower() == "temperature":
            # cut to the model's area by the ERDDAP server (see oceanval.erddap)
            output["obs_path"] = erddap.DATASETS["coraltemp"]["url"]
            output["source"] = "CoralTemp"
            output["source_info"] = "NOAA Coral Reef Watch, 2026, NOAA Coral Reef Watch Version 3.1 Monthly Global 5km Satellite Sea Surface Temperature (CoralTemp v3.1), served by NOAA CoastWatch's ERDDAP at https://coastwatch.pfeg.noaa.gov/erddap/griddap/NOAA_DHW_monthly. Skirving, W., B. Marsh, J. De La Cour, G. Liu, A. Harris, E. Maturi, E. Geiger, and C.M. Eakin, 2020: CoralTemp and the Coral Reef Watch Coral Bleaching Heat Stress Product Suite Version 3.1, Remote Sensing, 12, 3856, https://doi.org/10.3390/rs12233856."
            output["name"] = name.lower()
            output["thredds"] = False
            output["file_check"] = False
            output["climatology"] = False
            output["vertical"] = False
            output["obs_variable"] = erddap.DATASETS["coraltemp"]["variable"]
            return output

            return output

    if value == "hansell":
        if name.lower() in HANSELL_VARIABLES:
            output["source"] = "Hansell"
            output["source_info"] = "Hansell, D. A., Carlson, C. A., Amon, R. M. W., Álvarez-Salgado, X. A., Yamashita, Y., Romera-Castillo, C., and Bif, M. B.: Compilation of dissolved organic matter (DOM) data obtained from global ocean observations from 1994 to 2021, Version 2 (NCEI Accession 0227166), NOAA National Centers for Environmental Information, https://doi.org/10.25921/s4f4-ye35, 2021."
            output["name"] = name.lower()
            output["point"] = True
            output["hansell_variable"] = name.lower()
            return output

    if value in BUILT_RECIPES and name.lower() in BUILT_RECIPES[value]["variables"]:
        # products that oceanval.obs_convert makes into a netCDF file
        built = BUILT_RECIPES[value]
        by_variable = lambda x: x[name.lower()] if isinstance(x, dict) else x  # noqa: E731
        output["source"] = built["source"]
        output["source_info"] = by_variable(built["source_info"])
        output["name"] = name.lower()
        output["obs_path"] = by_variable(built["obs_path"])
        output["obs_variable"] = built["variables"][name.lower()]
        output["climatology"] = built["climatology"]
        output["thredds"] = False
        output["file_check"] = False
        if "depth_mean" in built:
            output["depth_mean"] = list(built["depth_mean"])
        return output

    if value == "cobe2":
        if name.lower() == "temperature":
            output["obs_path"] = "https://psl.noaa.gov/thredds/dodsC/Datasets/COBE2/sst.mon.mean.nc"

            output["source"] = "COBE2"
            output["source_info"] = "COBE-SST 2 and Sea Ice data provided by the NOAA PSL, Boulder, Colorado, USA, from their website at https://psl.noaa.gov/data/gridded/data.cobe2.html."
            output["name"] = name.lower()
            output["thredds"] = True
            output["climatology"] = False
            output["vertical"] = False
            output["obs_variable"] = "sst"
            return output

    if value == "woa23":
        output["source"] = "WOA23"
        output["source_info"] = "Garcia, H.E., C. Bouchard, S.L. Cross, C.R. Paver, Z. Wang, J.R. Reagan, T.P. Boyer, R.A. Locarnini, A.V. Mishonov, O. Baranova, D. Seidov, and D. Dukhovskoy. World Ocean Atlas 2023, Volume 4: Dissolved Inorganic Nutrients (phosphate, nitrate, silicate). A. Mishonov, Tech. Ed. NOAA Atlas NESDIS 92, doi.org/10.25923/39qw-7j08"
        output["climatology"] = True
        output["name"] = name.lower()
        output["thredds"] = True

        if name.lower() == "nitrate":
            urls = [f"https://www.ncei.noaa.gov/thredds-ocean/dodsC/woa23/DATA/nitrate/netcdf/all/1.00/woa23_all_n{month:02d}_01.nc" for month in range(1, 13)]
            output["obs_path"] = urls
            output["obs_variable"] = "n_an"
            return output

        if name.lower() == "oxygen":
            urls = [f"https://www.ncei.noaa.gov/thredds-ocean/dodsC/woa23/DATA/oxygen/netcdf/all/1.00/woa23_all_o{month:02d}_01.nc" for month in range(1, 13)]
            output["obs_path"] = urls
            output["obs_variable"] = "o_an"
            return output

        if name.lower() == "phosphate":
            urls = [f"https://www.ncei.noaa.gov/thredds-ocean/dodsC/woa23/DATA/phosphate/netcdf/all/1.00/woa23_all_p{month:02d}_01.nc" for month in range(1, 13)]
            output["obs_path"] = urls
            output["obs_variable"] = "p_an"
            return output

        if name.lower() == "silicate":
            urls = [f"https://www.ncei.noaa.gov/thredds-ocean/dodsC/woa23/DATA/silicate/netcdf/all/1.00/woa23_all_i{month:02d}_01.nc" for month in range(1, 13)]
            output["obs_path"] = urls
            output["obs_variable"] = "i_an"
            return output

        if name.lower() in WOA23_OXYGEN_VOLUME:
            # apparent oxygen utilization and oxygen saturation: Volume 3, with
            # the oxygen itself, 12 monthly files of the long-term climatology
            folder, letter, obs_variable = WOA23_OXYGEN_VOLUME[name.lower()]
            output["source_info"] = WOA23_OXYGEN_CITATION
            output["obs_path"] = [
                f"https://www.ncei.noaa.gov/thredds-ocean/dodsC/woa23/DATA/{folder}/netcdf/all/1.00/woa23_all_{letter}{month:02d}_01.nc"
                for month in range(1, 13)
            ]
            output["obs_variable"] = obs_variable
            return output

        if name.lower() in ["salinity", "temperature"]:
            if start is None or end is None:
                raise ValueError("Start and end depth must be provided for salinity and temperature WOA23 recipes")
            if end - start > 9:
                raise ValueError("Start and end depth must fall within a single WOA23 climatological period (10 year periods)")
            if start >= 1955 and end <= 1964:
                period = "5564"
            elif start >= 1965 and end <= 1974:
                period = "6574"
            elif start >= 1975 and end <= 1984:
                period = "7584"
            elif start >= 1985 and end <= 1994:
                period = "8594"
            elif start >= 1995 and end <= 2004:
                period = "95A4"
            elif start >= 2005 and end <= 2014:
                period = "A5B4"
            elif start >= 2015 and end <= 2022:
                period = "B5C2"
            else:
                raise ValueError("End year cannot be greater than 2022 for WOA23 recipes")

            if name.lower() == "temperature":
                output["obs_variable"] = "t_an"
            else:
                output["obs_variable"] = "s_an"
            urls = [
                f"https://www.ncei.noaa.gov/thredds-ocean/dodsC/woa23/DATA/{name.lower()}/netcdf/{period}/1.00/woa23_{period}_{name[0].lower()}{month:02d}_01.nc"
                for month in range(1, 13)
            ]
            output["obs_path"] = urls
            return output

    if value == "nsbc":
        output["source"] = "NSBC"
        output["source_info"] = "Nutrient and biogeochemical climatology from the National Shelf Biogeochemistry climatology."
        output["climatology"] = True
        output["thredds"] = True
        output["name"] = name.lower()
        if name.lower() == "chlorophyll":
            output["obs_variable"] = "chlorophyll_a_mean"
        elif name.lower() == "oxygen":
            output["obs_variable"] = "oxygen_mean"
        elif name.lower() == "temperature":
            output["obs_variable"] = "temperature_mean"
        elif name.lower() == "salinity":
            output["obs_variable"] = "salinity_mean"
        elif name.lower() == "nitrate":
            output["obs_variable"] = "nitrate_mean"
        elif name.lower() == "ammonium":
            output["obs_variable"] = "ammonium_mean"
        elif name.lower() == "phosphate":
            output["obs_variable"] = "phosphate_mean"
        elif name.lower() == "silicate":
            output["obs_variable"] = "silicate_mean"
        nsbc_variable = "chlorophyll_a" if name.lower() == "chlorophyll" else name.lower()
        output["obs_path"] = (
            "https://icdc.cen.uni-hamburg.de/thredds/dodsC/ftpthredds/nsbc/"
            "level_3/climatological_monthly_mean/"
            f"NSBC_Level3_{nsbc_variable}__UHAM_ICDC__v1.1__0.25x0.25deg__OAN_1960_2014.nc"
        )
        return output

    if value == "occci":
        output["source"] = "OCCCI"
        output["source_info"] = "Ocean Colour Climate Change Initiative (OC-CCI) monthly datasets."
        output["climatology"] = False
        output["thredds"] = True
        output["name"] = name.lower()
        if name.lower() == "chlorophyll":
            output["obs_variable"] = "chlor_a"
            output["obs_path"] = [
                "https://www.oceancolour.org/thredds/dodsC/cci/v6.0-release/"
                f"geographic/monthly/chlor_a/{year}/ESACCI-OC-L3S-CHLOR_A-MERGED-"
                f"1M_MONTHLY_4km_GEO_PML_OCx-{year}{month:02d}-fv6.0.nc"
                for year in range(1998, 2025)
                for month in range(1, 13)
            ]
            return output
        if name.lower() == "kd490":
            output["obs_variable"] = "kd_490"
            output["obs_path"] = [
                "https://www.oceancolour.org/thredds/dodsC/cci/v6.0-release/"
                f"geographic/monthly/kd/{year}/ESACCI-OC-L3S-K_490-MERGED-"
                f"1M_MONTHLY_4km_GEO_PML_KD490_Lee-{year}{month:02d}-fv6.0.nc"
                for year in range(1998, 2025)
                for month in range(1, 13)
            ]
            return output

    if value == "ices":
        output["source"] = "ICES"
        output["source_info"] = "In-situ observations from the ICES Oceanographic database (https://ocean.ices.dk), International Council for the Exploration of the Sea, Copenhagen. Licensed under CC BY 4.0."
        output["name"] = name.lower()
        output["point"] = True
        # temperature and salinity are in both the CTD and the bottle
        # datasets, listed in order of preference (see ices.py)
        if name.lower() == "temperature":
            output["ices_parameter"] = "TEMPPR01"
            output["ices_dataset"] = ["CTD", "ICE"]
            return output
        if name.lower() == "salinity":
            output["ices_parameter"] = "PSALPR01"
            output["ices_dataset"] = ["CTD", "ICE"]
            return output
        if name.lower() == "alkalinity":
            output["ices_parameter"] = "ALKYZZXX"
            output["ices_dataset"] = "ICE"
            return output
        if name.lower() == "ammonium":
            output["ices_parameter"] = "AMONZZXX"
            output["ices_dataset"] = "ICE"
            return output
        if name.lower() == "chlorophyll":
            output["ices_parameter"] = "CPHLZZXX"
            output["ices_dataset"] = "ICE"
            return output
        if name.lower() == "nitrate":
            output["ices_parameter"] = "NTRAZZXX"
            output["ices_dataset"] = "ICE"
            return output
        if name.lower() == "oxygen":
            output["ices_parameter"] = "DOXYZZXX"
            output["ices_dataset"] = "ICE"
            return output
        if name.lower() == "ph":
            output["ices_parameter"] = "PHXXZZXX"
            output["ices_dataset"] = "ICE"
            return output
        if name.lower() == "phosphate":
            output["ices_parameter"] = "PHOSZZXX"
            output["ices_dataset"] = "ICE"
            return output
        if name.lower() == "silicate":
            output["ices_parameter"] = "SLCAZZXX"
            output["ices_dataset"] = "ICE"
            return output

    raise ValueError(f"Recipe value {value} is not valid for recipe name {name}")


# create a validator class
# create a variable class to hold metadata

def _thredds_unavailable(name, model_variable, source_name, obs_variable,
                         sample_file, obs_path, reason):
    """Say loudly that a THREDDS server let us down, and skip the variable.

    A server being down or misbehaving says nothing about whether the rest
    of the validation can run, so the variable is dropped rather than every
    other registration going down with it.
    """
    details = [
        f"Variable:       {name}",
        f"Model variable: {model_variable}",
        f"Observations:   {source_name}",
        f"Obs variable:   {obs_variable}",
        f"THREDDS server: {sample_file}",
    ]
    if isinstance(obs_path, list) and len(obs_path) > 1:
        details.append(f"                (the first of {len(obs_path)} files)")
    details += [
        "",
        reason,
        "",
        "The THREDDS server is not available, so this variable is being",
        "IGNORED and will not be validated. Everything else carries on.",
    ]
    loud_warning(
        f"THREDDS SERVER NOT AVAILABLE - IGNORING {str(name).upper()}",
        details,
        warning=(
            f"THREDDS server unavailable for {name} ({sample_file}): "
            f"{reason} This variable is being ignored."
        ),
    )
    session_info["end_messages"] += [
        f"{name} was not registered: the THREDDS server {sample_file} "
        "was unavailable, so it has not been validated."
    ]


class Variable:
    def __str__(self):
    # add a print method for each atrribute
        attrs = vars(self)
        return '\n'.join("%s: %s" % item for item in attrs.items())  
    # add a repr method
    def __repr__(self):
        attrs = vars(self)
        return '\n'.join("%s: %s" % item for item in attrs.items()) 


class Validator:

    #keys = session_info["keys"]
    keys = [] 
    # add a deleter that removes from keys list
    def __delattr__(self, name):
        if name != "keys":
            if name in self.keys:
                self.keys.remove(name)
        super().__delattr__(name)
    # add remove method
    def remove(self, name):
        if name != "keys":
            if name in self.keys:
                self.keys.remove(name)
        super().__delattr__(name)

    # add reset method
    def reset(self):
        for key in self.keys:
            super().__delattr__(key)
        self.keys = []

    # ensure self.x = y, adds x to the keys list
    def __setattr__(self, name, value):
        if name != "keys":
            if name not in self.keys:
                self.keys.append(name)
                # ensure this can be accessed via self[name]

        super().__setattr__(name, value)

    # create a [] style accessor
    # make Validator subsettable, so that validator["chlorophyll"] returns the chlorophyll variable
    def __getitem__(self, key):
        return getattr(self, key, None)
    
    
    # 
    def add_gridded_comparison(self, 
                               name = None, 
                               long_name = None, 
                               short_name = None, 
                               short_title = None, 
                               source = None, 
                               source_info = None, 
                               model_variable = None, 
                               obs_path = None, 
                               obs_variable = None, 
                               start = -1000, 
                               end = 3000, 
                               vertical = False, 
                               climatology = None, 
                               obs_multiplier = 1,
                               obs_adder = 0,
                               thredds = False,
                               recipe = None,
                               file_check = True,
                               depth_mean = None
                                   ): 
        """

        Add a gridded comparison variable to the Validator

        Parameters:

        name (str): Name of the variable

        long_name (str): Long name of the variable

        short_name (str): Short name of the variable

        short_title (str): Short title of the variable

        source (str): Source of the variable

        source_info (str): Source information of the variable

        model_variable (str): Model variable name, several added as "a+b", or a
            formula of them in the language of CDO's expr, such as "a+2*b"

        obs_path (str): Directory or path of the observations

        obs_variable (str): Observation variable name

        start (int): Start depth of the variable

        end (int): End depth of the variable

        vertical (bool): Whether the variable is vertical

        climatology (bool): Whether to use climatology

        obs_multiplier (float): Multiplier for the observation

        obs_adder (float): Adder for the observation

        file_check (bool): Whether to check if the obs_path exists and variables are valid

        depth_mean (list): [top, bottom] depths, in metres, that the observations
            are a mean over (COPEPOD's mesozooplankton is of the top 200 m), so
            that the simulation is averaged over them, cell thickness weighted,
            where the surface is used otherwise. A recipe that needs it gives it.

        """

        # maybe include an averaging option: daily, monthly, annual etc.

        builtin = None
        if recipe is not None:
            recipe_info = find_recipe(recipe, start=start, end=end)
            if recipe_info["point"]:
                raise ValueError(f"Recipe {recipe} is for point data, so use add_point_comparison")
            # data given in place of the recipe's own is not OceanVal's
            if obs_path is None:
                builtin = recipe_info.get("builtin")
                obs_path = recipe_info["obs_path"]
            if source is None:
                source = recipe_info["source"]
            if depth_mean is None and builtin is not None:
                depth_mean = recipe_info.get("depth_mean")
            if source == "GLODAPv2.2016b" or recipe_info.get("file_check") is False:
                file_check = False
            if source_info is None:
                source_info = recipe_info["source_info"]
            thredds = recipe_info["thredds"]
            if climatology is None:
                climatology = recipe_info["climatology"]
            if name is None:
                name = recipe_info["name"]
            if short_name is None:
                short_name = recipe_info["short_name"]
            if long_name is None:
                long_name = recipe_info["long_name"]
            if short_title is None:
                short_title = recipe_info["short_title"]
            if not vertical and recipe_info["vertical"] is not None:
                vertical = recipe_info["vertical"]
            if obs_variable is None:
                obs_variable = recipe_info["obs_variable"]
            recipe = True
        else:
            recipe = False

        if name is None:
            raise ValueError("Name must be supplied for gridded comparison")
        
        if depth_mean is not None:
            try:
                depth_mean = [float(x) for x in depth_mean]
            except (TypeError, ValueError):
                raise ValueError("depth_mean must be [top, bottom], two depths in metres")
            if len(depth_mean) != 2 or not 0 <= depth_mean[0] < depth_mean[1]:
                raise ValueError("depth_mean must be [top, bottom], two depths in metres, the top above the bottom")
            if vertical:
                raise ValueError("depth_mean cannot be given with vertical=True, which uses every depth")

        # name can only have str or numbers
        if not re.match("^[A-Za-z0-9]+$", name):
            raise ValueError("Name can only contain letters and numbers")

        if source is None:
            raise ValueError("Source must be supplied")
        if model_variable is None:
            raise ValueError("Model variable must be supplied")
        # climatology must be provideded
        if climatology is None:
            raise ValueError("Climatology must be provided for gridded comparison")
        # obs_path is needed
        if obs_path is None:
            raise ValueError("obs_path must be provided for gridded comparison")
        # must be boolean
        if not isinstance(climatology, bool):
            raise ValueError("Climatology must be a boolean value")
        try:
            obs_multiplier  = float(obs_multiplier)
        except:
            raise ValueError("obs_multiplier must be a number")

        try:
            obs_adder  = float(obs_adder)
        except:
            raise ValueError("obs_adder must be a number")

        assumed = []

        if long_name is None:
            try:
                long_name = self[name].long_name
            except:
                long_name = name
                assumed.append("long_name")

        if short_name is None:
            # use it, if it already exists
            try:
                short_name = self[name].short_name
            except:
                short_name = name
                assumed.append("short_name")
        if short_title is None:
            try:
                short_title = self[name].short_title
            except:
                short_title = name.title()
                assumed.append("short_title")

        if source_info is None:
            source_info = f"Source for {source}"
            assumed.append("source_info")

        source_name = source
        # ensure the sourc key does not included "_"
        if "_" in source_name:
            raise ValueError("Source cannot contain '_'")
        if not isinstance(obs_variable, str):
            raise ValueError("obs_variable be provided")

        gridded_dir = obs_path

        if thredds is True and not check_servers:
            file_check = False

        if file_check:
            if gridded_dir != "auto":
                if thredds is False:
                    # a pattern such as obs/*.nc is matched up as it is
                    if not os.path.exists(gridded_dir) and not glob.glob(gridded_dir):
                        raise ValueError(f"Gridded directory {gridded_dir} does not exist")
        # thredds must be boolean
        if not isinstance(thredds, bool):
            raise ValueError("thredds must be a boolean value")
        
        # figure out if obs_variable exists in the files. file_check=False
        # means "do not go and look", which for thredds also means not
        # contacting the server at all, so a variable can still be set up
        # while a server is down and matched up once it is back
        if file_check:
            if isinstance(obs_path, list):
                sample_file = obs_path[0]
            else:
                if obs_path.endswith(".nc") and not glob.has_magic(obs_path):
                    sample_file = obs_path
                else:
                    files = gridded_files(obs_path)
                    if not files:
                        raise ValueError(f"No netCDF files found for {obs_path}")
                    sample_file = files[0]
            try:
                if thredds is True:
                    ds = nc.open_thredds(sample_file)
                else:
                    ds = nc.open_data(sample_file, checks = False )
            except Exception as exc:
                if thredds is not True:
                    raise ValueError(f"Could not open observation data file {sample_file}")
                _thredds_unavailable(
                    name, model_variable, source_name, obs_variable,
                    sample_file, obs_path, f"{type(exc).__name__}: {exc}",
                )
                return None

            try:
                ds_variables = ds.variables
            except Exception as exc:
                if thredds is not True:
                    raise
                _thredds_unavailable(
                    name, model_variable, source_name, obs_variable,
                    sample_file, obs_path,
                    f"Could not read the contents: {type(exc).__name__}: {exc}",
                )
                return None

            # a struggling thredds server can answer without actually serving
            # anything - the file opens, but it lists no variables at all,
            # which would otherwise be reported as obs_variable being missing
            if thredds is True and len(ds_variables) == 0:
                _thredds_unavailable(
                    name, model_variable, source_name, obs_variable,
                    sample_file, obs_path,
                    "The server opened the file but served no variables at all.",
                )
                return None

            if obs_variable not in ds_variables:
                raise ValueError(f"obs_variable {obs_variable} not found in observation data files")

        if name in session_info["short_title"]:
            if short_title is not None:
                if short_title != session_info["short_title"][name]:
                    raise ValueError(f"Short title for {name} already exists as {session_info['short_title'][name]}, cannot change to {short_title}")


        # Figure out if name is already

        # one variable, a sum or a formula, as oceanval.model_expr reads it
        model_variable = _checked_model_variable(model_variable, name)

        # figure out if self[name] exists already
        if getattr(self, name, None) is None:
            var = Variable()
            setattr(self, name, var)
            self[name].model_variable = model_variable
            self[name].sources = dict()
            self[name].gridded_comparisons = dict()
            self[name].point_comparisons = dict()

        else:
            if self[name].model_variable != model_variable:
                raise ValueError(f"Model variable for {name} already exists as {self[name].model_variable}, cannot change to {model_variable}")
            if source_name in self[name].sources:
                # ensure the value is the same
                if self[name].sources[source_name] != source_info:
                    raise ValueError(f"Source {source_name} already exists with a different value")

        # each source is its own comparison, so repeated calls with different
        # sources add to the variable, while the same source again replaces it
        self[name].gridded_comparisons[source_name] = dict(
            obs_path = obs_path,
            obs_variable = obs_variable,
            start = start,
            end = end,
            vertical = vertical,
            climatology = climatology,
            obs_multiplier = obs_multiplier,
            obs_adder = obs_adder,
            thredds = thredds,
            recipe = recipe,
            builtin = builtin,
            depth_mean = depth_mean,
        )
        self[name].sources[source_name] = source_info
        self[name].n_levels = 1
        self[name].long_name = long_name
        # if this is None set to Name
        self[name].short_name = short_name
        if self[name].short_name is None:
            self[name].short_name = name
            assumed.append("short_name")    
        self[name].short_title = short_title
        if self[name].short_title is None:
            self[name].short_title = name.title()
            assumed.append("short_title")
        # check if this is c
        session_info["short_title"][name] = self[name].short_title

        # ensure nothing is None
        # warnings for assumptions
        if len(assumed) > 0:
            print(f"Warning: The following attributes were missing and were assumed for variable {name}: {assumed}")


    def add_point_comparison(self, 
                             name = None, 
                             long_name = None, 
                             vertical = False, 
                             short_name = None, 
                             short_title = None, 
                             source = None, 
                             source_info = None, 
                             model_variable = None, 
                             start = -1000, 
                             end = 3000, 
                             obs_path = None, 
                             obs_multiplier = 1,
                             obs_adder = 0,
                             binning = None,
                             recipe = None,
                             point_time_res = None  ):
        """

        Add a point comparison variable to the Validator

        Parameters:

        name (str): Name of the variable

        long_name (str): Long name of the variable

        vertical (bool): Whether the variable is vertical

        short_name (str): Short name of the variable

        short_title (str): Short title of the variable

        source (str): Source of the variable

        source_info (str): Source information of the variable

        model_variable (str): Model variable name, several added as "a+b", or a
            formula of them in the language of CDO's expr, such as "a+2*b"

        start (int): First year of observations to use

        end (int): Last year of observations to use

        obs_path (str): A csv file of the observations, or a directory whose csv files are all used

        obs_multiplier (float): Multiplier for the observation, if needed to convert units

        binning (list): Binning information [lon_resolution, lat_resolution]

        recipe (dict): Built-in point recipe, e.g. {"temperature": "ices"} or
        {"nitrate": "glodapv3"}. The observations are downloaded during matchup
        for the years and lon/lat range being matched, so obs_path is not used.

        point_time_res (str or list): Which of "year", "month" and "day" the
        observations are matched to the model by, in place of the
        point_time_res given to matchup. Defaults to None, which uses
        matchup's.

        """
        if recipe is not None:
            recipe_info = find_recipe(recipe)
            if not recipe_info["point"]:
                raise ValueError(f"Recipe {recipe} is for gridded data, so use add_gridded_comparison")
            if obs_path is not None:
                raise ValueError("obs_path cannot be supplied with a recipe, as the recipe supplies its own data")
            # the recipes of the user's own point data are csv files, which
            # are read and checked as any others are
            user_point = recipe_info.get("user", False)
            if user_point:
                obs_path = recipe_info["obs_path"]
            if name is None:
                name = recipe_info["name"]
            if source is None:
                source = recipe_info["source"]
            if source_info is None:
                source_info = recipe_info["source_info"]
            if long_name is None:
                long_name = recipe_info["long_name"]
            if short_name is None:
                short_name = recipe_info["short_name"]
            if short_title is None:
                short_title = recipe_info["short_title"]
            if user_point:
                recipe = None
            elif recipe_info["builtin"] == "glodapv3":
                recipe = dict(kind="glodapv3", variable=recipe_info["glodap_variable"])
            elif recipe_info["builtin"] == "hansell":
                recipe = dict(kind="hansell", variable=recipe_info["hansell_variable"])
            else:
                recipe = dict(
                    kind="ices",
                    parameter = recipe_info["ices_parameter"],
                    dataset = recipe_info["ices_dataset"],
                )

        if name is None:
            raise ValueError("Name must be supplied")

        # check what is supplied is valid
        # name can only have str or numbers
        if not re.match("^[A-Za-z0-9]+$", name):
            raise ValueError("Name can only contain letters and numbers")

        if source is None:
            raise ValueError("Source must be supplied")
        source_name = source
        # ensure the sourc key does not included "_"
        if "_" in source: 
            raise ValueError("Source key cannot contain '_'")


        try:
            obs_multiplier= float(obs_multiplier)
        except:
            raise ValueError("obs_multiplier must be a number")
        try:
            obs_adder = float(obs_adder)
        except:
            raise ValueError("obs_adder must be a number")
        # vertical must be a boolean
        if not isinstance(vertical, bool):
            raise ValueError("vertical must be a boolean value")

        # check these are int or can be cast to int
        try:
            start = int(start)
            end = int(end)
        except:
            raise ValueError("start and end must be integers")

        if point_time_res is not None:
            if isinstance(point_time_res, str):
                point_time_res = [point_time_res]
            if (
                not isinstance(point_time_res, list)
                or len(point_time_res) == 0
                or not all(x in ("year", "month", "day") for x in point_time_res)
                or len(set(point_time_res)) != len(point_time_res)
            ):
                raise ValueError(
                    'point_time_res must be "year", "month" or "day", or a list of them'
                )

        assumed = []
        if source_info is None:
            source_info = f"Source for {source}"
            assumed.append("source_info")
        if long_name is None:
            try:
                long_name = self[name].long_name
            except:
                long_name = name
                assumed.append("long_name")
        if short_name is None:
            # use it, if it already exists
            try:
                short_name = self[name].short_name
            except:
                short_name = name
                assumed.append("short_name")
        if short_title is None:
            try:
                short_title = self[name].short_title
            except:
                short_title = name.title()
                assumed.append("short_title")

        if name in session_info["short_title"]:
            if short_title != session_info["short_title"][name]:
                raise ValueError(f"Short title for {name} already exists as {session_info['short_title'][name]}, cannot change to {short_title}")
        
        # recipes download their data during matchup, so there are no files to check
        if recipe is None:
            # check obs path exists
            if obs_path is None or os.path.exists(obs_path) is False:
                raise ValueError(f"Observation path {obs_path} does not exist")

            files = point_files(obs_path)
            # if no files exists, raise error
            if len(files) == 0:
                raise ValueError(f"No csv files found in point directory {obs_path}")
            valid_vars = ["lon", "lat", "year", "month", "day", "depth", "observation", "source"]
            vertical_option = False
            for vv in files:
                # read in the first row
                df = read_point(vv, nrows=1)
                # throw error something else is in there
                bad_cols = [col for col in df.columns if col not in valid_vars]
                if len(bad_cols) > 0:
                    raise ValueError(f"Invalid columns {bad_cols} found in point data file {vv}")
                if "depth" in df.columns:
                    vertical_option = True
                # lon/lat/observation *must* be in df
                for req_col in ["lon", "lat", "observation"]:
                    if req_col not in df.columns:
                        raise ValueError(f"Required column {req_col} not found in point data file {vv}")
            if vertical_option is False:
                if vertical:
                    raise ValueError("vertical is set to True but no depth column found in point data files. You cannot vertically validate this data.")
        # if binning is supplied, ensure it is a 2 variable list
        if binning is not None:
            if not isinstance(binning, list) or len(binning) != 2:
                raise ValueError("binning must be a list of two values: [spatial_resolution, depth_resolution]")
        # ensure each element of binning is a number
            for res in binning:
                try:
                    float(res)
                except:
                    raise ValueError("Each element of binning must be a number")
        # one variable, a sum or a formula, as oceanval.model_expr reads it
        model_variable = _checked_model_variable(model_variable, name)
        # figure out if self[name] exists already
        if getattr(self, name, None) is None:
            # add it
            var = Variable()
            setattr(self, name, var)
            self[name].model_variable = model_variable
            self[name].sources = dict()
            self[name].gridded_comparisons = dict()
            self[name].point_comparisons = dict()
        else:
            # ensure short title is the same
            if short_title != session_info["short_title"][name]:
                raise ValueError(f"Short title for {name} already exists as {session_info['short_title'][name]}, cannot change to {short_title}")

            if self[name].model_variable != model_variable:
                old_model_variable = self[name].model_variable
                raise ValueError(f"Model variable for {name} already exists as {old_model_variable}, cannot change to {model_variable}")
            if source_name in self[name].sources:
                # ensure the value is the same
                if self[name].sources[source_name] != source_info:
                    raise ValueError(f"Source {source_name} already exists with a different value")

        # each source is its own comparison, so repeated calls with different
        # sources add to the variable, while the same source again replaces it
        self[name].point_comparisons[source_name] = dict(
            obs_path = obs_path,
            start = start,
            end = end,
            vertical = vertical,
            obs_multiplier = obs_multiplier,
            obs_adder = obs_adder,
            binning = binning,
            recipe = recipe,
            point_time_res = point_time_res,
        )
        self[name].sources[source_name] = source_info
        self[name].n_levels = 1
        self[name].long_name = long_name
        if self[name].long_name is None:
            self[name].long_name = name
            assumed.append("long_name")

        self[name].short_name = short_name
        if self[name].short_name is None:
            self[name].short_name = name
            assumed.append("short_name")

        self[name].short_title = short_title
        if self[name].short_title is None:
            self[name].short_title = name.title()
            assumed.append("short_title")
        #
        for vv in assumed:
            print(f"Warning: The attribute {vv} was missing and was assumed for variable {name}")
        session_info["short_title"][name] = short_title

definitions = Validator()


def generate_mapping(ds):
    """
    Generate mapping of model and observational variables
    """

    model_dict = {}
    try:
        candidate_variables = definitions.keys
        if session_info.get("fvcom", False):
            from oceanval.fvcom import fvcom_contents

            ds_contents = fvcom_contents(ds[0])
        else:
            ds1 = nc.open_data(ds[0], checks=False)
            ds_contents = ds1.contents

        ds_contents["long_name"] = [str(x) for x in ds_contents["long_name"]]

        ds_contents_top = ds_contents.query("nlevels == 1").reset_index(drop=True)
        n_levels = int(ds_contents.nlevels.max())
        if n_levels > session_info["n_levels"]:
            session_info["n_levels"] = n_levels
        # number of rows in ds_contents
        if len(ds_contents) == 0:
            ds_contents = ds_contents_top
    except:
        return model_dict

    for vv in candidate_variables:
        variables = model_expr.names(definitions[vv].model_variable)
        include = True
        for var in variables:
            if var not in ds_contents.variable.values:
                include = False
        if include:
            model_dict[vv] = definitions[vv].model_variable
            n_levels = ds_contents.query("variable in @variables")["nlevels"].max()
            if n_levels > definitions[vv].n_levels:
                definitions[vv].n_levels = n_levels 
            continue

    return model_dict
