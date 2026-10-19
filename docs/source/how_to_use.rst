How to use OceanVal
=====================

Validating simulations using OceanVal involves three steps:

    **1**. Register the observational datasets you want to use for validation

    **2**. Matchup the model simulation output with the observational datasets

    **3**. Calculate validation statistics, generate plots and create an HTML summary of the performance of the simulation

**You should always create a new directory prior to running OceanVal for a new simulation, and then run all OceanVal commands from within that directory.**

Step 1: Register observational datasets
---------------------------------------

You first need to register the observational datasets you want to use for validation.
This involves specifying the location of the data files and any necessary metadata.
You can register both gridded and in-situ observational datasets using the `oceanval.add_point_comparison` and `oceanval.add_gridded_comparison` functions.

**Setting up point (in-situ) observational data**

To register an in-situ observational dataset, you will need to specify the following:

- `name`: A name for the dataset, e.g. "temperature". This is so that OceanVal can keep track of things. You can call this what you want, but it can only contain numbers and letters.
- `source`: The source of the observational data (e.g. "NOAA").
- `model_variable`: A string specifying the name of the model variable to compare against the observations.
- `obs_path`: The path to a file or directory containing the observational data files.

Note: when specifying a directory as `obs_path` ensure that the directory only contains files relevant to the observational variable being registered, as OceanVal will recursively identify and use all netCDF files in the directory.

The following optional parameters can also be specified:

- `source_info`: Additional information about the source of the data (e.g. publication details)
- `short_name`: A short name for the observational variable (e.g. "temp" for temperature)
- `short_title`: A short title for plots (e.g. "Nitrate Concentration")
- `long_name`: A long name for the observational variable (e.g. "sea surface temperature")
- `vertical`: A boolean indicating whether vertical validation should be carried out. This defaults to False, so only surface validation will occur.
- `start`: The first year of observations to use. If not specified, all years in the data will be used.
- `end`: The last year of observations to use. If not specified, all years in the data will be used.
- `obs_multiplier`: A multiplier to apply to the observational data (e.g. to convert units). This defaults to 1.0 (no change).
- `obs_adder`: An adder to apply to the observational data (e.g. to convert units). This defaults to 0.0 (no change). For example, set to 273.15 to convert from Kelvin to Celsius.
- `binning`: Specify if you want data to be spatially binned to a specific lon/lat resolution. This is of the format [lon_bin_size, lat_bin_size] in degrees. If not specified, no binning will be applied.

An example is shown below:

.. code:: ipython3

    oceanval.add_point_comparison(
        name="nitrate",
        source = "ICES",
        source_info = "In-situ observations from the International Council for the Exploration of the Sea",
        short_name = "nitrate concentration",
        model_variable="temp",
        obs_path="/path/to/obs_data/",
    )

Instead of `obs_path`, you can use a built-in point recipe, which downloads the observations for you when `oceanval.matchup` runs. Ten variables from the ICES Oceanographic database are available this way. The observations are downloaded for the years being matched and the `lon_lim`/`lat_lim` area given to `matchup` (or the model's own extent, if these are not given). The name, source and labels all come from the recipe:

.. code:: ipython3

    oceanval.add_point_comparison(
        model_variable="votemper",
        recipe={"temperature": "ices"},
        vertical=True,
    )

See :doc:`recipes` for details.

.. admonition:: How does OceanVal handle variable names?

    You can use whatever you want for the `name` parameter when registering observational datasets. This is only used internally by OceanVal to keep track of things, to name files etc.
    Reports and plots will use the `short_name`, `long_name` and `short_title` parameters for labelling. If you want a better looking report, you should set these parameters.

    You can validate a variable against more than one gridded or point (in-situ) dataset by calling `add_gridded_comparison` or `add_point_comparison` once per dataset, with the same `name` and a different `source`.



**Setting up gridded observational data**

To register a gridded observational dataset, you will need to specify the following:

- `name`: A name for the dataset, e.g. "temperature". This is so that OceanVal can keep track of things.
- `source`: The source of the observational data (e.g. "CMEMS").
- `model_variable`: A string specifying the name of the model variable to compare against the observations.
- `obs_path`: The path to the directory containing the observational data files.
- `obs_variable`: A string specifying the name of the variable in the observational data files.
- `climatology`: A boolean indicating whether the observational data is a climatology.

Note: if you do not provide `obs_variable`, OceanVal will assume there is only one variable in the observational data files, and will use that variable for validation.

The following optional parameters can also be specified:

- `source_info`: Additional information about the source of the data (e.g. publication details)
- `short_name`: A short name for the observational variable (e.g. "oxygen concentration")
- `long_name`: A long name for the observational variable (e.g. "dissolved oxygen concentration")
- `short_title`: A short title for plots (e.g. "Oxygen Concentration")
- `vertical`: A boolean indicating whether vertical validation should be carried out. This defaults to False, so only surface validation will occur.
- `start`: The first year of observations to use for validation.
- `end`: The last year of observations to use for validation.
- `obs_multiplier`: A multiplier to apply to the observational data (e.g. to convert units). This defaults to 1.0 (no change).
- `obs_adder`: An adder to apply to the observational data (e.g. to convert units). This defaults to 0.0 (no change). For example, set to 273.15 to convert from Kelvin to Celsius.

An example is shown below:

.. code:: ipython3

    oceanval.add_gridded_comparison(
        name="oxygen",
        source = "CMEMS",
        source_info = "Gridded observations from the Copernicus Marine Environment Monitoring Service",
        short_name = "oxygen concentration",
        model_variable="oxygen",
        obs_variable="O2_concentration",
        obs_path="/path/to/obs_data/",
        climatology=False,
    )


**Be consistent**

If you are registering the same variable separate for point and gridded data, make sure you are giving the same `short_name`, `long_name` and `short_title` for both datasets. This will ensure that plots and statistics are labelled consistently.
You will get an error if you are inconsistent.


.. admonition:: How does OceanVal handle gridded data?

    OceanVal works on the basis that gridded data can be converted to one of the following:

    1. A time series of multi-year monthly averages for each grid cell
    2. A climatological monthly average for each grid cell
    3. A climatological annual average for each grid cell

    If you provide multi-year observational data, OceanVal will calculate a multi-year observational average, which is compared with the model in a like-for-like manner.

    If you provide single-year observational data with monthly resolution, OceanVal will generate a comparable climatological monthly average from the model simulation output for comparison.
    This will be based on the year range you have specified.

    If you have provided a single-year observational dataset with only one time step, OceanVal will assume this is a climatological annual average.
    A model climatological annual average will be generated from the simulation output for comparison.

    **The simulation output will always be regridded to the observational grid.**



Step 2: Matchup model output with observations
----------------------------------------------
Once you have registered your observational datasets, you can matchup the model simulation output with the observations using the `oceanval.matchup` function.

You will need to specify the following:

- `sim_dir`: The path to the directory containing the model simulation output files.
- `start`: The first year of the simulation to use for validation.
- `end`: The last year of the simulation to use for validation.
- `cores`: The number of CPU cores to use for parallel processing.
- `lon_lim`: The longitude limits for the validation region (e.g. [-180, 180]).
- `lat_lim`: The latitude limits for the validation region (e.g. [-90, 90]).

The following variable is required if you are carrying out vertical validation:

- `thickness`: Either "z_level" or a string specifying the name of the variable in the model output files that contains the cell thickness information. The name must be exactly the variable's name, e.g. "e3t": a variable that only has it in its name, such as O3_c_e3t, is not used. The thickness can have a time dimension or not (a static thickness such as e3t_0), and its vertical coordinate needs a `positive` attribute ("down" for NEMO). A thickness file must hold only the thickness variable. With "z_level", the model's levels must be depths (positive down); output on heights (positive up) needs the cell thickness instead.

The following optional parameters can also be specified:

- `n_dirs_down`: The number of directory levels to search down for model output files. This defaults to 2, assuming files are stored in for example a YYYY/MM/ structure.
- `overwrite`: A boolean indicating whether to overwrite existing matchup files. This defaults to False.
- `ask`: A boolean indicating whether to ask for confirmation before overwriting existing matchup files. This defaults to True.
- `cache`: A boolean indicating whether to cache intermediate results. This defaults to False.
- `exclude`: A list of strings that should not appear in any simulation files paths.
- `out_dir`: The path to the directory where matchup files should be saved. If not specified, matchup files will be saved in the execution directory.
- `point_time_res`: The time resolution for the point (in-situ) observation matchup. This defaults ["year", "month", "day"] for totally precise matchups. Set to ["month", "day"], if you want to compare climatological simulation output with observations.
- `n_check`: The number of files to check when identifying the file naming convention. OceanVal checks all files in a random subdirectory. Set n_check for a random subset in cases where all simulation files are in a single directory.
- `as_missing`: A float or list of floats providing a range , i.e [min, max], specifying values to be treated as missing in the model output.
- `live_validation`: Build an interim HTML validation report as the matchups are made, so that each one's results can be looked at as soon as it is made. Needs jupyter-book 2 or later. See "Looking at the results while the matchups run" below.
- `time_shift`: The number of days to shift every model time by before matching up, e.g. -1 for output that dates each time mean at the end of the period it covers (as GETM does: January's mean at 00:00 on 1 February). By default OceanVal checks whether the model's times look like that and, if so, asks whether to shift them back a day. Set 0 to leave the times as they are without checking. See the Q&A.
- `data_store`: Whether to use your data store, the folder set in the oceanval window's Preferences where the built-in observational data downloaded (WOA23, OC-CCI, NSBC, COBE2, GLODAP and ICES) is kept for later matchups. Your own data is never kept in it. By default it is used if one is set; `False` leaves it out for this run.

An example is shown below:

.. code:: ipython3

    oceanval.matchup(
        sim_dir="/path/to/simulation/output/",
        start=2000,
        end=2010,
        cores=4,
        lon_lim=[-80, 0],
        lat_lim=[20, 60],
        thickness="cell_thickness",
    )
.. admonition:: Requirements for simulation folder structure

    OceanVal requires that simulation output is either stored in a single directory
    or in subdirectories that follow something like YYYY/MM/ structure.
    If there are subdirectories, **they must** only contain integers.
    This is the typical way of storing simulation output for ocean models.
    If you have a different folder structure, you can just create symbolic links to the relevant files in a single directory.

    Directories should ideally only contain results from a single simulation.
    If you have multiple simulations in the same directory, you can use the `require` parameter
    to specify strings that must appear in the file paths of the simulation files you want to use.

**Note**: If you are validating a simulation with only monthly resolution, then you probably want to set the `point_time_res` parameter to `["year", "month"]` when matching up in-situ observations.
This will result in day of year being ignored when matching up observations with the simulation output. If you use the default for `point_time_res`, then very few matchups will be found, as the day of year in the observations will almost never match that in the simulation output.
`matchup` shows the time resolution of the files it uses for each variable (`monthly`, or `1d`, `5d` and so on), and if a point dataset is matched by day against output coarser than daily, it warns you and offers to change `point_time_res` for all such datasets or for each one before anything is matched up.

**Summing up simulation output**

Sometimes observational data needs to be compared with the sum of multiple model variables.
You can do this by setting something like "var1+var2+var3" as the `model_variable` when registering the observational dataset.
In the `create_recipes` window you can tick the variables in the pop-out under the model variable box instead of typing the sum; the variables selected are listed with their long names at the top of the pop-out.

.. admonition:: How does OceanVal handle in-situ data?

    OceanVal will handle in-situ observational data depending on which of the following are provided:

    - year
    - month
    - day
    - depth

    If depth is not provided, OceanVal will assume this represents a surface observational dataset.
    If you do provide depth, OceanVal will interpolate to all available depth-resolved data if you have specified `vertical=True` in `add_point_comparison`; otherwise, it will only use the top 5 m of data.
    Through the water column, an observation shallower than the centre of the model's top cell, such as one at 0 m, is compared with the top cell, and one deeper than the centre of the bottom cell is left out.
    By default it only looks at the surface.

    If you provide year, month and day, OceanVal will look for model output at the exact date of the observation.

    If you provide year and month, but not day, OceanVal will look for model output for the whole month of the observation, and use the monthly average from the simulation.

    If you only provide year, OceanVal will look for model output for the whole year of the observation, and use the annual average from the simulation.

    If you provide month (and optionally day), but not year, OceanVal will use the average for that month (or day) over the simulation years from `start` to `end`.

    If no time information is provided, OceanVal will use the average over the simulation years from `start` to `end` for comparison with the observation.

    In some cases, you may want to ignore the year information in the observational data, and compare observations with the simulation's average for their month and day instead.
    In this case, you can set the `point_time_res` parameter in the `oceanval.matchup` function to specify which time information to use when matching up in-situ observations with the simulation output.
    Set this to `["month", "day"]` to ignore year information when matching up observations with the simulation output.
    Only observations from the years between `start` and `end` are used.


.. admonition:: Where does OceanVal save matchup files?

    By default, OceanVal will save matchup files in the directory where you run the `oceanval.matchup` function.
    You can find the files in oceanval_matchups/gridded or oceanval_matchups/point subdirectories.

    Gridded matchups will end with ".nc", while point matchups will end with ".csv".
    In each case, the model output will be named "model", while the observations will be named "observation".

Step 3: Calculate validation statistics and generate HTML summary
-----------------------------------------------------------------

Once you have matched up the model simulation output with the observations, you can calculate validation statistics and generate plots using the `oceanval.validate` function.

You can do this as follows:

.. code:: ipython3

    oceanval.validate()

This must be run in the same directory where the matchup files were created.

The following options are available:

- `variables`: The matchups to validate, if not all of them: each entry a variable, for all its sources, or `"variable/source"`, for one, e.g. `["temperature", "nitrate/WOA23"]`. See below. If not specified, every matchup is validated.
- `lon_lim`: The longitude limits for the validation region (e.g. [-180, 180]).
- `lat_lim`: The latitude limits for the validation region (e.g. [-90, 90]).
- `region`: A string specifying the region being validated. Only "global" and "nwes" (northwest European Shelf are currently available).
- `concise`: A boolean indicating whether to generate a concise HTML summary page. This defaults to True.
- `transect`: A transect to validate the gridded matchups along, as a dict of its start and end, each `[lon, lat]`, e.g. `{"start": [-30, 0], "end": [-30, 65]}`. It must run north-south (the same longitude at both ends) or east-west (the same latitude at both ends). See below.
- `depth_bins`: The depth ranges, in metres, that point matchups made through the water column (`vertical=True`) are summarised in, as a list of `[min, max]` pairs. See below.

This will then generate and open an html page that can be viewed in a web browser.

**Validating along a transect**

To see how well the model reproduces the observations along a line, give `validate` a transect. It must run north-south, with the same longitude at both ends, or east-west, with the same latitude at both ends:

.. code:: ipython3

    oceanval.validate(transect={"start": [-30, 0], "end": [-30, 65]})

Each gridded matchup's page then has a section with a map of the transect over the land; the monthly climatology of the surface model and observations along it, and their difference, by latitude (or longitude) and month; and, if the matchup was made through the water column (`vertical=True`), a section of the annual mean, with depth down and latitude (or longitude) across. The matchup's depths can be unevenly spaced, so they are first interpolated onto 30 evenly spaced depths between the shallowest and the deepest. The values are extracted with nctoolkit's `to_transect`, so this needs nctoolkit 1.3.6 or later. The `oceanval` command asks for the transect on its last page, and does not let you carry on until it is a straight north-south or east-west line.

**Choosing the depth bins**

Point matchups made through the water column (`vertical=True`) are summarised by depth, on each point dataset's page and in the summary. By default the depth ranges are 0-10, 10-30, 30-60, 60-100, 100-150, 150-300, 300-600, 600-1000 and >1000 m. Give `validate` your own as a list of `[min, max]` pairs, in metres:

.. code:: ipython3

    oceanval.validate(depth_bins=[[0, 20], [20, 200], [200, None]])

A depth is in a bin if it is deeper than its min and no deeper than its max (the shallowest bin also takes its min). The deepest bin can have a max of `None`, for everything below its min. Bins can leave gaps, and observations in a gap are left out, but they cannot overlap. The same bins are used for every point dataset and in the summary. The `oceanval` command asks for them on its last page when a point dataset is matched up with Vertical ticked, starting from the defaults: each bin can be changed or removed, and **Add a bin** adds another.

**Validating some of the matchups**

By default the report validates every matchup in `oceanval_matchups`. To validate only some, give `validate` their variables, each one for all its sources, or `"variable/source"` for one source:

.. code:: ipython3

    oceanval.validate(variables=["temperature", "nitrate/WOA23"])

The names are those of the matchups (the folders and files in `oceanval_matchups`), and are not case sensitive. Each entry must match at least one matchup, or `validate` says which matchups there are. The summary covers only the matchups validated, and the others stay in `oceanval_matchups` for a later report. The `oceanval` command lists them all, ticked, for you to untick any to leave out.

**Looking at the results while the matchups run**

Matching up a long simulation can take hours. With jupyter-book 2 or later, `oceanval.matchup` can build an interim validation report as it goes, so that you can look at each matchup's results as soon as it is made:

.. code:: ipython3

    oceanval.matchup(
        sim_dir="/path/to/simulation/output/",
        start=2000,
        end=2010,
        live_validation={"lon_lim": [-20, 10], "lat_lim": [40, 65]},
    )
    oceanval.validate(lon_lim=[-20, 10], lat_lim=[40, 65], pdf=True)

Each matchup's page is added to the interim report as soon as the matchup is made, and the summary is run again each time. `live_validation` takes `True`, for `validate`'s defaults, or a dict of `validate`'s report options: `out_dir`, `lon_lim`, `lat_lim`, `subregions`, `fixed_scale`, `concise`, `transect`, `depth_bins` and `variables` (the matchups it leaves out are still made, but have no page). The interim report is HTML only, so PDF, Word and zip versions are left to `validate`. It is built in `oceanval_interim_report`, in `out_dir`: open `oceanval_interim_report/oceanval_report/_build/html/notebooks/summary.html`, and reload it to see the latest. Each page says how many of the matchups it has. `matchup` waits for the interim report to be finished before it returns, and the full report is then built by `validate`, as usual. The `oceanval` command does all of this for you.

Using built-in observation recipes
----------------------------------

To make it easier to register standard observational datasets, OceanVal also supports built-in recipe definitions. These are especially useful when you want to use a standard climatology such as WOA23 or GLODAP without manually specifying all the metadata.

For example, to register a WOA23 temperature climatology recipe:

.. code:: ipython3

    oceanval.add_gridded_comparison(
        name="temperature",
        source="WOA23",
        model_variable="temp",
        recipe={"temperature": "woa23"},
        start=2005,
        end=2014,
        climatology=True,
    )

This uses the built-in metadata and file locations for the WOA23 temperature climatology. The `recipe` parameter can also be used with other supported variables, including salinity, oxygen, nitrate, phosphate, silicate, chlorophyll and pH.

The underlying recipe helper can also be used directly:

.. code:: ipython3

    recipe = oceanval.parsers.find_recipe({"temperature": "woa23"}, start=2005, end=2014)
    print(recipe["source"], recipe["obs_variable"])

Comparing validation outputs from multiple simulations
------------------------------------------------------

Once you have generated validation reports for different simulations, you can compare them using the `oceanval.compare` function. This creates a comparison report that summarises the differences between the simulations.

.. code:: ipython3

    oceanval.compare(
        model_dict={
            "model_a": "/path/to/model_a",
            "model_b": "/path/to/model_b",
        },
        view=True,
        ask=True,
    )

The `model_dict` should map a short name for each model to the directory containing that model's validation output. The HTML comparison report is written to `oceanval_comparison/compare/_build/html/index.html`.

Analyzing a simulation on its own
---------------------------------

`oceanval.analyze` works out climatologies, phenology and time series from a simulation's output alone, with no observations, and builds an HTML report of them the way `validate` builds the validation report.

.. code:: ipython3

    oceanval.analyze(
        sim_dir="/path/to/simulation",
        variables={"Chlorophyll": "P1_Chl+P2_Chl+P3_Chl+P4_Chl", "Temperature": "votemper"},
        climatology={
            "Temperature": {"start": 2000, "end": 2009, "surface": True, "bottom": True, "water_column": "mean"},
            "Chlorophyll": {"start": 2000, "end": 2009, "surface": True, "water_column": "integral"},
        },
        phenology={"Chlorophyll": {"start": 2000, "end": 2009}},
        long_term={"Temperature": {"start": 2000, "end": 2009, "surface": True}},
        seasonal={"Temperature": {"start": 2000, "end": 2009, "surface": True}},
        map_changes={
            "Temperature": {
                "historical": {"start": 1990, "end": 1999},
                "future": [{"start": 2040, "end": 2049}, {"start": 2090, "end": 2099}],
                "plot": "absolute_changes", "change": "percentage", "surface": True,
            },
        },
        transects={
            "Temperature": [
                {"start": [-30, 0], "end": [-30, 65],
                 "climatology": {"start": 2000, "end": 2009},
                 "vertical": {"start": 2000, "end": 2009},
                 "trends": {"start": 2000, "end": 2009, "how": "absolute"}},
                {"start": [-20, 50], "end": [0, 50],
                 "trends": {"start": 2000, "end": 2009, "how": "percentage_change"}},
            ],
        },
        stations={"L4": [-4.217, 50.25]},
        subregions="nwes",
        n_dirs_down=2,
        thickness="e3t",
        cores=4,
    )

- `variables`: the name to give each variable, and the model variable holding it. Several model variables can be summed, as `"a+b"`.
- `climatology`: for each variable, the `start` and `end` years, and `surface=True`, `bottom=True` (the deepest value that is not missing, taken with nctoolkit's `bottom` method with `choice="value"`) and/or `water_column`, `"mean"` (weighted by the cell thickness) or `"integral"` (the depth integral). The mean of each month of the year is worked out, and the annual mean; both are mapped. The water column needs `thickness`: the thickness variable in the output, a file with it, or `"z_level"`.
- `phenology`: for each variable, the `start` and `end` years. The day of the year of the seasonal peak at the surface is found in each grid cell from the variable's daily climatology, so it needs daily output.
- `long_term`: for each variable, the `start` and `end` years and `surface`, `bottom` and/or `water_column`, as for `climatology`. The mean of each year (the mean of its months' means) is worked out over the model's domain, over each region of `subregions`, and at each of `stations`. Years with output missing are the mean of the months there are, and the report marks them.
- `seasonal`: the same for each month from `start` to `end`, and so the mean seasonal cycle of each. It needs monthly output, or output more often.
- `map_changes`: for each variable, maps of how it changes between a historical period and one or two future periods: `historical` and `future` (a list of one or two) are dicts of `start` and `end` years, and `surface`, `bottom` and/or `water_column` are as for `climatology`. Each period is mapped as the mean of its years' annual means. `plot` is `"absolute"` (the historical and future maps), `"absolute_changes"` (those, and the maps of the changes) or `"changes"` (the historical map, and the changes), and `change` is `"absolute"` (future minus historical) or `"percentage"` (that as a percentage of the historical value, missing where it is 0). The periods share one colour scale, and the changes another, symmetric about zero. All the maps of a layer are drawn in one figure, each titled by the years it is of. Each period must be years the output has, but the years between them need not be.
- `transects`: for each variable, on its own, a list of straight lines to work the model's values out along, as many as you like. Each is a dict of `start` and `end` (`[lon, lat]`), which must run north-south (the same longitude at both ends) or east-west (the same latitude at both ends), and at least one of `climatology` (the monthly climatology at the surface), `trends` (at the surface, with `how` of `"absolute"`, the linear trend of the annual means per year, `"absolute_change"`, the last year's annual mean minus the first's, or `"percentage_change"`, that as a percentage of the first year's), `vertical` (the annual mean through the water column, in a section, as validate draws it; it needs `thickness`, which places each cell at its depth, or `"z_level"`) and `vertical_trends` (the same `how`, at each depth and position of the section). Each option is a dict of its own `start` and `end` years; `trends` and `vertical_trends` can also have a `window`, the number of years of a rolling mean of the annual means, worked out with nctoolkit's `rolling_mean` after the annual means (default 1: no rolling mean; with a window of N a change is the mean of the last N years minus the mean of the first N). Each month of each year is averaged first, a year's mean is the mean of its months' means, and years without all their months are left out of the trends. Trends need output in at least 3 years (2 for the changes); the app does not offer them for a variable with only one year. The report maps each transect and draws each option. It needs nctoolkit 1.3.6 or later.
- `stations`: a dict of the name of each station and its `[lon, lat]`. The nearest cell of the model's grid with values is used, which must be within two grid cells.
- `subregions`: `"nwes"`, `"global"` or the path of your own regions `.nc` file, as in `validate`, for the regional summaries of the time series. The model's domain as a whole is always done. `lon_lim` and `lat_lim` cut a box out of the domain first, for the domain and region means; stations are not cut by it. All spatial means are worked out with nctoolkit's `spatial_mean`. If nctoolkit warns that the model's grid has no cell bounds, so that the cell areas cannot be worked out, the mean is worked out again after regridding the grid to a regular lon-lat grid with `to_latlon`, by nearest neighbour and by bilinear interpolation (the bilinear values are used, and the report says how far the two differ), and the report says that the raw grid did not have enough information to calculate grid cell areas.
- `cores`: the number of cores OceanVal may use. There is no default: you must say how many.
- `as_missing`: a value, or `[minimum, maximum]`, to treat as missing in the model output, as in `matchup`.
- `figure_code`: `True` to give each figure in the report a **Copy code to reproduce figure** button at its top right, which copies the Python code that draws the figure from the analysis's saved results. Everything it uses (the files, colour scale, labels and colours) is written out in it, so you can change the figure and run the code in the conda environment OceanVal ran in. Default `False`.

Everything is saved in `oceanval_analysis` in `out_dir`: the fields as netCDF, with `analysis.json` saying what is in them, in `results`, the report in `report`, and `report.html`, a link to its first page. Any analysis already there is replaced. In the OceanVal app, the **Analyze** box on the first page does the same, with no Python. Nothing is chosen for you there: you tick what to work out, give the years, say how many cores to use, and answer whether the report should contain code to reproduce its figures; for a simulation that looks like NEMO-ERSEM, the app assumes zeros are missing values, as it does for validation. The years given must be ones the output has: `analyze()` refuses a start before the output's first year or an end after its last, and the app says so beside the box.


.. admonition:: Can I access and use OceanVal's validation code?

    Yes. OceanVal uses juypyter notebooks to carry out the validation calculations and generate plots.
    These notebooks can be found in the `oceanval_report/notebooks` directory where the validation output was stored.
    You can copy these notebooks and use them to create a more customized validation.
    The notebooks themselves are designed for internal use by OceanVal, and are not designed to be user-friendly, but they should be clear enough.
