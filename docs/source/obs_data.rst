Compatible data
===============

OceanVal is designed to validate ocean model simulations using two types of observational data: gridded observations and in-situ observations.

This data will need to be in specific formats. However, it should be easy to convert your data into these formats.

Simulation output
---------------------------

OceanVal is designed to validate ocean model output that is in NetCDF format.

Files should be CF-compliant as far as possible. Most ocean model output files are CF-compliant, so there should be no compatibility issues.


**Unstructured grids**: OceanVal will not be compatible out-of-the-box with unstructured grid model output. However, if the unstructured grid data can be regridded onto a regular grid and saved as a CF-compliant NetCDF file, then OceanVal will be able to use it. FVCOM output is supported via ``oceanval.fvcom_preprocess``, or directly with ``matchup(fvcom=True)``, which regrids the files for you.

**Vertical grids**: OceanVal can handle both z-level files and files where the vertical grid varies, provided that the cell thickness is stored in the simulation output.

**Folder structure**: OceanVal requires that simulation files are stored in a folder with a consistent and logical file naming convention.
For example, files could be named YYYY/MM/model_output_YYYYMMDD.nc. OceanVal will automatically identify the naming convention of files and match up to the appropriate files. 

**Time requirements**: OceanVal requires at least a single year of model output. The simulation output time resolution can be daily or monthly.








Gridded observational data
---------------------------

If you want to validate your model against gridded observational data (e.g. satellite data), then the data should be in a NetCDF format.

Observations can either be contained in a single NetCDF file or multiple files. If multiple files are provided, OceanVal will merge them.

OceanVal requires files to be CF-compliant. However, gridded observational products almost always are, so there should be no compatibility issues.

Vertically-resolved files can be supplied. These should be files with depth as a dimension, and the vertical grid should be consistent, which will almost always be the case with gridded data products.

**Time requirements**: ideally, the observational data files will contain time information. If not, you may get an error as the lack of time information can make it impossible to figure out what to do with observational data. 


**Key OceanVal assumption**: if you supply a gridded file with only one step, OceanVal will assume it represents climatological annual averages.

In-situ observational data
--------------------------

If you want to validate your model against in-situ observational data (e.g. from research cruises or moorings), then the data should be in CSV format. You can give OceanVal the path of one CSV file, or of a directory, in which case all the CSV files in the directory are used.

The CSV file can contain the following columns, with required columns (lon/lat/observation) in bold:

- **lon**: Longitude of the observation (decimal degrees). Longitudes from 0 to 360 can be used: those above 180 are converted to the equivalent negative longitudes (e.g. 350 becomes -10) when the data is matched up
- **lat**: Latitude of the observation (decimal degrees)
- depth: Depth of the observation (metres)
- year: Year of the observation
- month: Month of the observation (1-12)
- day: Day of the observation (1-31)
- **observation**: Value of the observation (ideally units should match those of the model output)

If you supply anything other than these columns, OceanVal will throw an error. 
The depth variable is required if you are validating against subsurface model data.

If you are validating against surface simulation data only, then the depth variable is optional. If it is supplied, only the top 5 m will be used for surface validation.

OceanVal will check if you have supplied the required columns and will raise an error if any are missing.
