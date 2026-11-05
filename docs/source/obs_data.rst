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








Check that your data is compatible
----------------------------------

The OceanVal app can check one of your model output files before you start. Run ``oceanval``, open **Tools & info** and choose **Is my data compatible with OceanVal?**. Type the path of a file in the box: if you type a directory, the folders and netCDF files in it are listed as you type, and you pick one. If the file has more than one variable, you choose the one to check. OceanVal then answers, with a turning circle while it works and then a tick, a warning or a cross, and a message under each:

* **Can CDO open my data?** ``cdo sinfo`` must run without crashing and find a variable on a grid. A crash means the data may not be CF-compliant; try another CDO version.
* **Can my data be regridded?** The first time step at the surface is regridded to a 360x180 grid, by bilinear and by nearest-neighbour interpolation. If only one works you are warned, with CDO's reason. A *generic* grid, with no longitudes and latitudes CDO can use, cannot be regridded.
* **Can a spatial average be calculated from my data?** A grid without cell areas gets a warning: OceanVal will regrid it to work out spatial averages, and the report says so.
* **Is the time axis understood?** The dates, the time resolution and the calendar are read; times that look like the ends of their periods are flagged.
* **Can my data be plotted interactively?** The map View simulation output draws, which you can zoom and pan.
* **Can my data be plotted as a climatology?** The map Analyze simulations draws: the sea surface's climatology, a mean over the file's first 12 time steps.

FVCOM output is recognised first. It should be compatible, as OceanVal regrids it itself, so the CDO, regridding and spatial-average checks are skipped (as is the climatology map, as Analyze simulations does not work with FVCOM output yet), and the time check and interactive plot are made.


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
