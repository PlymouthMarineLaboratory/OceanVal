<a href="https://pmlmodelling.github.io/OceanVal/"><img src="docs-site/assets/img/oceanval_wordmark.svg" width="240" alt="OceanVal" align="left" /></a>
<p align="right">
  <a href="https://oceanval.readthedocs.io/en/latest/?badge=latest"><img src="https://readthedocs.org/projects/oceanval/badge/?version=latest" alt="Documentation Status" /></a>
  <a href="https://anaconda.org/channels/conda-forge/packages/oceanval/overview"><img src="https://anaconda.org/conda-forge/oceanval/badges/version.svg" alt="Conda Badge" /></a>
  <img src="https://github.com/pmlmodelling/oceanVal/actions/workflows/python-app.yml/badge.svg" alt="GitHub Testing" />
</p>
<br clear="left" />

Ocean model validation made easy in Python.

To learn more about the package, visit the [OceanVal website](https://pmlmodelling.github.io/OceanVal/). The

OceanVal is designed for the automated creation of validation reports. You provide the model and validation data. OceanVal does the rest. It can also analyse a simulation on its own, with no observations: the climatology and the phenology of its variables, in an HTML report (see [Analyzing a simulation](#analyzing-a-simulation)). A short example of what the report looks like can be found [here](https://pmlmodelling.github.io/oceanval_example/index.html). 

## Running OceanVal from the terminal

The browser window is in the development version only (`main`), not yet in a tagged
release; there is a [page and demo video on the website](https://pmlmodelling.github.io/OceanVal/app.html).

The quickest way needs no Python at all. In a terminal, in the directory to
work in, run `oceanval` (or `OceanVal`). A page opens in your web browser
that takes you through these steps:

1. **Choose** between **Analyze** (see [Analyzing a simulation in the
   window](#analyzing-a-simulation-in-the-window)) and **Validate**, which
   opens the choices to match up new data and validate it, to match up only,
   or to validate matchups made earlier.
   **Add your own validation data for future use** saves observations of your own as
   recipes in a `.oceanvalrc` file (see below), instead of running anything.
2. **Simulation**: where the model output is, how many directories down its
    files are, which files to skip or keep (the file filters), and the domain.
    Type the directory, with folders suggested as you go, or pick
   it with **Browse…**, which looks through the folders on the machine
   OceanVal runs on, so it works on a remote machine too. As you type, the
    page counts the files that pass the filters. The next page starts with
    years inferred from their names, which you can change there. It also asks
    where to save the matchups and the report: the
   directory you started `oceanval` in, unless you choose another. To
   validate matchups made earlier, you choose the report's options instead.
3. **Own data**: whether you have observations of your own, as well as
   OceanVal's built-in datasets. If you do, a page for point data (csv
   files) and one for gridded data (netCDF) let you add them one at a time,
   with every argument of `add_point_comparison` or `add_gridded_comparison`;
   the ones that have to be given are marked in red. Skip a page if you have
   none of that kind. The calls are written into the matchup script.
   **Add this data for use now only** adds a dataset to this run, and **Add
   this data for use now and in future** also saves it as a recipe (see
   below), after a page asking for what a recipe needs that the form did not,
   such as the units, with what OceanVal will assume in amber.
4. **Recipes**: the recipes window, to check the model
   variables found and choose the observations to validate against. Your own
   data from step 3 is shown in its variable's row, ticked and tagged **Your
   data**, as it is always matched up, with the same options as the other
   datasets (Match by for point data, the years and Vertical), which are
   written into its call; go back to change anything else about it, or remove
   it. A dataset validated through the water column (Vertical) needs a
   thickness before you can carry on.
5. **Units**: a table of the units of every gridded and point matchup, read
   from the model's netCDF files and, for the observations, from the recipes
   (the Units column of the recipes page) or from your own netCDF file. Where
   OceanVal thinks they differ, it suggests a multiplier and an adder for the
   observations, in red and bold, and says what it assumed, such as a seawater
   density of 1025 kg/m³ between per kilogram and per volume. It can get this
   wrong, so check every row, and fill in any it could not work out. Your own
   point data is csv files, which have no units, so it is up to you to make
   sure they match the model's. The units always have to be confirmed, with a
   box under the tables, before the button starts the matchup. The
   conversions are written into the matchup script as `obs_multiplier` and
   `obs_adder`.
6. **Files**: the page says it is identifying the files that meet your
   criteria (only the model output is read: observation servers are not
   contacted until the matchup is made). If the model's time means look
   dated at the end of the period each covers (January's mean at 00:00 on 1
   February, as GETM dates them), it first shows why, and asks whether to
   shift every model time back a day, so that the files and dates shown next
   are those matched up; once the matchups are confirmed, the choice is
   written into the matchup script as `time_shift`. It then shows what it found as a table,
   with a row for each variable and each of the years its datasets are
   matched up for (the run's years, or a dataset's own within them): the
   years, its model variable, the observations matched up for those years,
   the file pattern, and the time resolution (`monthly`, or `1d`, `5d` and so
   on; the finest is used). As the page is shown, OceanVal reads the date of
   every file, with a bar saying how far it has got (you need not wait for
   it): each row then counts only the files with output in its years, which
   **List files** lists, with their first and last dates, and says in its
   Dates column whether any dates are missing from those years. If they are,
   "Some dates appear to be missing", and **Show the months** opens a row
   under it with the months of each year that are and are not there, and
   what is missing, such as days from daily output, times in more than one
   file, and years that gridded matchups leave out. What it read is not read
   again when the matchup starts. Carry on if the matchups are right; No stops the run;
   **Back** stops it too, before anything is matched up, and goes back to
   the units. If the files are wrong, change the file filters under the
   table and click **Use these filters**: the simulation is read again with
   them and you go back to the recipes window, with your choices kept where
   the model variables are still in the files. If a point dataset is matched by day (its `point_time_res`
   includes `day`, as the default does) against output coarser than daily,
   a page then says most of its observations may go unmatched, and offers
   to change `point_time_res` for all such datasets (OceanVal suggests year
   and month) or for each one, or to keep it. The choice is written into the
   matchup script. At a terminal, `matchup()` asks the same.
7. **Report** (to match up and validate): "One last thing... How would you
   like your validation report?" The report's options, asked for before
   anything is matched up: a subregion to validate, regional summaries,
   a transect to validate the gridded datasets along (a start and an end
   longitude and latitude, which must run north-south or east-west, and the
   page does not let you carry on otherwise), fixed colour scales, PDF and
   Word versions, a zipped copy, a concise or detailed report, and, if a
   point dataset is matched up with Vertical, the depth bins its depth
   summaries use (each can be changed or removed, and more added). The
   report is built beside the matchups, in the
   directory chosen in step 2. **Back** shows the page about point datasets
   matched by day, if it was shown, or the matchups, and keeps the options. The options are written into the matchup script's
   `validate()` call. **Stop and store Python script for later** stops
   there, with nothing matched up, and keeps the script with the options;
   the page then says how to run it from a terminal.
8. **Run**: matchup, then, to validate as well, `validate()` with the
   report's options. The page says what the run is doing, and shows the output
   (which appears as it comes in the terminal) as the matchups are made and if the run fails; anything else OceanVal asks, such as whether to try again
   for observations a server could not supply, is asked in the page. With
   jupyter-book 2 or later, the matchup builds an interim validation report
   as it goes: the page links to it once the first matchup's page is in it. Each matchup's page is added as soon as the matchup is made, and the
   full report (with PDF and Word, if asked for) is built once they all are.
   The finished page links to the full report.

Every step after the first has **Back**, which keeps what you have entered:
get as far as the report's options, spot a mistake, go back as far as the
simulation to fix it, and carry on with everything else as you left it.
Whatever you change replaces what was there, in the steps after it too. The
recipes window comes back as you left it; go back before it, and the
simulation is read again, so the window keeps your global settings and the
rows you changed whose model variables are still in the output, ticks the
datasets afresh if you changed the domain, and says in red and bold what it
could not keep. The units always have to be confirmed again. Once anything
is matched up, there is no going back.

Simulations you have validated before (remembered as soon as you confirm the
matchups) are listed by the arrow at the right of the simulation directory box.
They are remembered in a `.oceanvalcache` JSON file in your home directory (`OCEANVALCACHE` names another file), which OceanVal only
adds to and a new version keeps.

The report's options list every variable and source to validate, gridded and
point, ticked, in a list that opens: untick any to leave them out of the
report, and only the options for those ticked are shown (a transect, regional
summaries and fixed colour scales for gridded data, depth bins for point data
through the water column). Building a report from matchups made before, the
page looks in the matchups directory as soon as you enter it, and shows the
options once it has found matchups there.

Closing the browser window quits `oceanval` in the terminal, within about 15
seconds, stopping anything it is running, whichever browser you use. So do
**Quit** (which is instant) and Ctrl+C.

The cog beside the OceanVal name at the top left of every page of the window (**Preferences**) sets how
the window looks (Default, Dark, Silver or Blue), whether the first page
offers **Try a demo**, and the **data store** (below). They are kept under `"settings"` in the `.oceanvalrc`
file in your home directory (or the file `OCEANVALRC` names), and read each
time the window opens. The **i** beside it shows the version of OceanVal, links to the website and
GitHub, and the built-in datasets.

The **data store** keeps the built-in observational data that is downloaded
(WOA23, OC-CCI, NSBC, COBE2, GLODAP and ICES) in a folder of your choosing, so
later matchups need not download it again; your own data is never kept in it.
Everything goes in an `oceanval_datastore` folder inside the one given, which
must already exist, and OceanVal never writes or removes anything outside it.
It needs a reasonable amount of free space. **Always use data from your data
store, if available** downloads only what it does not have; **Only use the
data store when download fails** downloads everything each time and uses the
store only for what cannot be downloaded. It keeps track of the area and years
it has (the model's area, rounded out to whole degrees, for gridded data; a
year at a time for ICES), so a matchup inside them is taken from it. Every
matchup uses it, including those run from Python; `matchup(...,
data_store=False)` leaves it out for one run.
**Run a check on the data store** in Preferences looks for data held twice, files that are
missing or not the size they were, and anything in the store that is not OceanVal's built-in
data, and says what it finds. It only looks: nothing is changed, removed or fixed.

Outside the window, `matchup(live_validation=...)` builds the same interim
report (HTML only, in `oceanval_interim_report`), with `True` or a dict of
`validate()`'s report options, before `validate()` builds the full report:

```python
oceanval.matchup(sim_dir="/path/to/output", start=2000, end=2010,
                 live_validation={"lon_lim": [-20, 10], "lat_lim": [40, 65]})
oceanval.validate(lon_lim=[-20, 10], lat_lim=[40, 65], pdf=True)
```

To see how it works first, choose **Try a demo**, below the four choices on
the first page. You choose one of three CMIP6 climate models, each from a
different ESGF server (NorESM2-LM, MPI-ESM1-2-LR and UKESM1-0-LL), and one to
three of its variables (sea surface temperature, surface nitrate and sea
surface salinity) in a grid; only those files are downloaded (up to 50 MB,
26 MB and 460 MB for the three models). It then takes you through the same
steps, after a short page of instructions (which asks whether you want the validation concise and fast, the default, or detailed but slower) and without the step for your own
data (only OceanVal's own datasets are used), with the options filled in, in
red and bold, including the model's name as the only file names to use, matching 2010 up with COBE-SST 2 (temperature)
and the World Ocean Atlas 2023 (nitrate and salinity). The units step suggests converting the observed nitrate, which
you check as you would for your own model. It shows
how OceanVal works, not how to validate a climate model, which needs many
years of output rather than one. It creates a directory called
`oceanval_demo`, with the data, matchups and report in it: remove it when you
have finished.

The script it runs is written first, to `matchup.py` unless you choose
another name, so you can run it again later with `python matchup.py`. On a
remote machine, open the link `oceanval` prints: VS Code forwards its port
for you. Over plain SSH (from a Windows terminal, say), `oceanval` prints the
`ssh -L` command to run in a new terminal on your own computer, then you open
the link in your browser. Then it uses port 8765 if that is free, so the
command is the same each time; `--port` chooses another. When it opens the
browser itself, as in VS Code, it uses any free port.

## Your own recipes

Observations you use again can be saved as recipes of your own, in a
`.oceanvalrc` file: in the directory you run OceanVal from, for that
directory, or in your home directory, for everywhere (the one in the directory
wins). Choose **Add your own validation data for future use** in the `oceanval` window,
then point (csv files) or gridded data (netCDF on this machine, on a THREDDS
server or at a web address), or **Add this data for use now and in future** in
a matchup's own data step. OceanVal opens the data to check it, and checks
the recipe against its own and your others, before it is saved. A source called
`MySat` for chlorophyll is then used like any other recipe, and is offered in
the recipes window, tagged **Yours**:

```python
oceanval.add_gridded_comparison(
    model_variable="chl",
    recipe={"chlorophyll": "mysat"},
)
```

See the [recipes page](https://pmlmodelling.github.io/OceanVal/recipes.html#your-recipes)
for the file's format. Recipes of your own are in the development version only
(`main`), not yet in a tagged release.

## Using built-in recipes

You can register a standard observation climatology without manually listing all metadata:

```python
import oceanval

oceanval.add_gridded_comparison(
    name="temperature",
    source="WOA23",
    model_variable="temp",
    recipe={"temperature": "woa23"},
    start=2005,
    end=2014,
    climatology=True,
)
```

This uses the v0.2.0 recipe system for datasets such as WOA23, NSBC, OCCCI and GLODAP.

There are also point (in-situ) recipes — currently on `main`, not yet in a
tagged release — from the [ICES Oceanographic database](https://ocean.ices.dk)
covering temperature, salinity, alkalinity, ammonium, chlorophyll, nitrate,
oxygen, pH, phosphate and silicate. Rather than reading csv files, they
download the observations during `matchup`, for the years being matched and
the `lon_lim`/`lat_lim` area (or the model's own extent); see the
[recipes page](https://pmlmodelling.github.io/OceanVal/recipes.html#point-recipes)
for the full list with units, and
[how ICES data is treated](https://pmlmodelling.github.io/OceanVal/recipes.html#ices-data)
for what is kept:

```python
oceanval.add_point_comparison(
    model_variable="votemper",
    recipe={"temperature": "ices"},
    vertical=True,
)
```

Point matchups made through the water column are summarised by depth in the
report, by default in 0-10, 10-30, 30-60, 60-100, 100-150, 150-300, 300-600,
600-1000 and >1000 m. `validate()` takes your own as `[min, max]` pairs in
metres, with a max of `None` for everything below the deepest:

```python
oceanval.validate(depth_bins=[[0, 20], [20, 200], [200, None]])
```

To validate only some of the matchups, give `validate()` their variables, each
for all its sources or as `"variable/source"` for one:

```python
oceanval.validate(variables=["temperature", "nitrate/WOA23"])
```

### Generating a matchup script

> **Deprecated:** `create_recipes` will be removed in a future release, and
> warns when called. Run the `oceanval` command instead, which opens the same
> window and goes on to run the matchup and the report.

`create_recipes` writes the script for you. It scans your model output,
works out which model variable holds each observational variable — matching
on the netCDF `long_name` attributes, so your variables need not be named
after the observations — and writes out every built-in gridded recipe.
Recipes it found a model variable for are live, with that variable filled
in; any it could not identify are commented out for you to complete by
hand, unless you fill them in first in the window described below. If
your output looks like raw FVCOM output (an unstructured mesh), it first
asks you to confirm that, then writes `fvcom=True` into the script's
`matchup()` call — pass `fvcom=True` or `fvcom=False` to skip the
question. Where a variable has a recipe in more than one region (e.g. temperature), `domain`
picks which one is left live — `"global"` or `"nwes"` (Northwest European
Shelf) — and a variable with a recipe only outside that domain still gets
that one. `domain="nwes"` also adds the ICES point recipes (see above) —
they have no global equivalent, so they're left out entirely for
`domain="global"` rather than written out commented.

```python
import oceanval

oceanval.create_recipes(
    simdir="/path/to/model/output",
    ndown=2,   # how many directories down the output files sit
    out="matchup.py",
    domain="global",
    start=2005,   # passed straight through to matchup()
    end=2014,
)
```

Before the script is written, a page opens in your web browser so you can
check and change all of this. It has a row for each observational
variable, holding the model variable identified for it (which you can
change; a pop-out under the box lists every variable in the model output
with its `long_name`; search it, and tick several to sum them as
`var1+var2+var3`, with the ones selected listed at the top) and tick-boxes for the gridded and point datasets available for
it. They start ticked as the script would otherwise be written; tick
several to validate a variable against each of them, or use **Clear all
selections** to untick everything and start from none. Each ticked dataset
can be given years of its own, and the ones whose observations are
resolved in depth (WOA23, NSBC and ICES) can be set to Vertical, to
validate the full water column rather than the surface alone.

Pass `gui=False` to write the script straight away, e.g. in a batch job.
`create_recipes` then asks in the terminal for the model variable of
anything it could not identify (press Enter to skip, or pass `ask=False`).
With `validate=False`, the script's `validate()` call is written commented
out, to build the report once the matchups are made. `exclude` and `require`
leave out output files by name, as `matchup()` does (e.g. `exclude="5d"`),
and are passed on to the script's `matchup()` call.

### Analyzing a simulation in the window

**Analyze**, on the first page, looks at a simulation on its own, with no
observations, through these steps:

1. **Simulation**: as for a validation, but with no domain, and the analysis
   is saved in a folder called `oceanval_analysis` in the directory you choose
   (an analysis already there is replaced).
2. **Variables**: "What variables do you want to analyse?" A row for each,
   with **Your name** for it and the **Model name** holding it, typed or
   ticked in the pop-out of the model's variables under the box (ticking
   several sums them, as `a+b`). Nothing is filled in for you: the rows start
   empty.
3. **Files**: the files holding each variable, its time resolution, and its
   dates, with any gaps in them (**Show the months** shows which). If the
   model's time means look dated at the end of the period each covers, the
   page first asks whether to shift every time back a day, as matchup does.
4. **Analyses**: six boxes, **Climatology**, **Phenology**, **Long-term time
   series**, **Seasonal time series**, **Map temporal changes** and **Transects**, each opening a pop-out with a row
   for each variable and **Cancel** and **Confirm** at the bottom. Nothing is
   ticked or filled in to start with. Climatology: the years from **Start** to
   **End**, and **Surface** and/or **Water column**, the vertical mean or the
   depth integral (which needs the cell thickness, the name of its variable,
   a file, or `z_level`). Phenology: the years, for each variable with daily
   output. The two time series have the same boxes, and a table of
   **Stations** (a name, longitude and latitude each). Once a time series is
   chosen, a card sets their **regions and area**: regional summaries (none,
   the northwest European Shelf, global, or your own `.nc` file) and a
   lon/lat box. Map temporal changes: for each variable ticked, the first and last
   years of a **Historical** period and of a **Future 1** period (both required), and
   optionally of a **Future 2** period, then **Surface**, **Bottom** and/or **Water column**
   as for the climatology, and, in a row of their own, **Plot**
   (*Historical + Future (absolute values)*, *Historical + Future (absolute values) +
   Changes* or *Historical + Changes*) and **Change** (absolute or percentage),
   neither chosen to start with. Transects: tick each variable you want transects of, and add as many
   transects as you like to each (they are separate for each variable). A
   transect is a card with the longitude and latitude of its **Start** and
   **End** in one line (it must run north–south, the same longitude at both
   ends, or east–west, the same latitude), and, below them, a **Surface** and a
   **Vertical** part, each with a **Climatology** (at the surface, the monthly
   climatology along the line, as in the validation; in the vertical, a section
   of the annual mean through the water column, as in the validation, which asks
   whether the simulation is z-level or where its cell thickness is) and its
   **Changes**, a drop-down of **Absolute** (the linear trend of the annual
   means, per year), **Absolute change** (the last year minus the first) and
   **Percentage change**, at the surface or at each depth of the section. Each
   has its own required **Start year** and **End year**. The absolute trend needs
   at least 3 years of output (the changes 2), and none is offered when a
   variable has only one year. A **Rolling mean (years)** box to the right of the years of
   the changes, 1 to start with (none), smooths the annual means with
   nctoolkit's `rolling_mean` before the change is worked out. Each variable's years of
   output are shown beside it as a hint. Below the boxes you must say **how many cores** OceanVal may use
   (there is no default), and what should be **treated as missing values**,
   which OceanVal fills in, as it does for validation, if the simulation looks
   like NEMO-ERSEM (zeros).
5. **Run**: the window writes the `oceanval.analyze()` call into a script
   (`analysis.py` unless you choose another name), runs it, and links to the
   report when it is built.

## Analyzing a simulation

`oceanval.analyze()` works out climatologies and phenology from a
simulation's output alone, and builds an HTML report of them the way
`validate()` builds the validation report:

```python
import oceanval

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
    cores=4,  # there is no default: say how many
)
```

- **Climatology**: the mean of each month of the year over the years from
  `start` to `end` (each month of each year first, so daily and monthly
  output count alike), and the annual mean, mapped (and each month, mapped), at
  the surface (the first value from the top that is not missing), at the
  bottom (`bottom=True`: the deepest value that is not missing, from
  nctoolkit's `bottom` method with `choice="value"`) and/or through the water
  column:
  `"mean"`, weighted by the cell thickness, or `"integral"`, the depth
  integral (per m³ becomes per m²). The water column needs `thickness`: the
  thickness variable in the output, a file with it, or `"z_level"`.
- **Phenology**: the day of the year of the seasonal peak at the surface in
  each grid cell, from the variable's daily climatology (its mean on each day
  of the year), so it needs daily output. Days are counted in the model's own
  calendar.
- **Long-term time series** (`long_term`): the mean of each year from `start`
  to `end` (the mean of its months' means), over the model's domain, over each
  region of `subregions` (`"nwes"`, `"global"` or the path of your own regions
  `.nc` file, as in `validate()`), and at each of `stations`, a dict of names
  and `[lon, lat]` (the nearest model cell with values, within two grid cells).
  `lon_lim` and `lat_lim` cut a box out of the domain for the domain and region
  means. Years with output missing are the mean of the months there are, and
  are marked as such in the report. Each variable can also have a `"window"`,
  the years of a rolling mean of the annual means (1, the default, is none;
  each year is the mean of it and the years before it), and a `"show"`:
  `"absolute"` (the default), `"absolute_change"` or `"percentage_change"`, each
  year's change from the first year with a value.
- **Seasonal time series** (`seasonal`): the same, month by month, and the mean
  seasonal cycle of each. It needs monthly output, or output more often.
- **Map temporal changes** (`map_changes`): maps of how a variable changes
  between a historical period and one or two future periods. For each
  variable, `historical` and `future` (a list of one or two) are dicts of
  `start` and `end` years, and the layers are `surface`, `bottom` and/or
  `water_column`, as for `climatology`. Each period is mapped as the mean of its
  years' annual means. `plot` is what is drawn: `"absolute"` (the historical
  and future maps), `"absolute_changes"` (those, and the maps of the changes) or
  `"changes"` (the historical map, and the changes); `change` is `"absolute"`
  (future minus historical) or `"percentage"` (that as a percentage of the
  historical value, missing where it is 0). The periods share one colour scale,
  and the changes another, symmetric about zero. All the maps of a layer are drawn in one
  figure, each titled by the years it is of. Each period must be years the
  output has, but the years between them need not be.
- **Transects** (`transects`): the model's values along straight lines that run
  north–south (the same longitude at both ends) or east–west (the same latitude),
  as many as you like, for each variable on its own. Each variable has a list of
  transects, each a dict of `start` and `end` (`[lon, lat]`) and at least one of
  `climatology` (the monthly climatology at the surface), `trends` (at the
  surface, with `how` of `"absolute"`, the linear trend of the annual means per
  year, `"absolute_change"`, the last year's annual mean minus the first's, or
  `"percentage_change"`, that as a percentage of the first year's), `vertical` (the
  annual mean through the water column, in a section; it needs `thickness`, which
  places each cell at its depth, or `"z_level"`) and `vertical_trends` (the same
  `how`, at each depth and position of the section), each a dict of
  its own `start` and `end` years. `trends` and `vertical_trends` can also have a `window`, the
  number of years of a rolling mean of the annual means (nctoolkit's
  `rolling_mean`, applied after the annual means are worked out): the default,
  1, is no rolling mean, and with a window of N a change is the mean of the last
  N years minus the mean of the first N. Each month of each year is averaged first, a
  year's mean is the mean of its months' means, and years without all their
  months are left out of the trends. The report maps each transect and draws each
  option. Needs nctoolkit 1.3.6 or later.
- `cores` has no default: you must say how many cores OceanVal may use.
  `as_missing` is a value, or `[minimum, maximum]`, to treat as missing, as in
  `matchup()`.
- `figure_code=True` gives each figure in the report a **Copy code to reproduce
  figure** button at its top right. It copies the Python code that draws the
  figure from the analysis's saved results, with its colour scale, labels and
  colours written out, so you can change it and run it in the conda environment
  OceanVal ran in. In the app, the options page asks whether you want this, and
  you must answer.
  All spatial means are worked out with nctoolkit's `spatial_mean`. If nctoolkit warns that the model's grid has no cell bounds, so that the cell areas cannot be worked out, the mean is worked out again after regridding the grid to a regular lon-lat grid with `to_latlon`, by nearest neighbour and by bilinear interpolation (the bilinear values are used, and the report says how far the two differ), and the report says that the raw grid did not have enough information to calculate grid cell areas.

Everything is saved in `oceanval_analysis/`, in `out_dir`: the fields as
netCDF (and `analysis.json`, which says what is in them) in `results/`, the
report's book in `report/`, and `report.html`, a link to its first page.
`time_shift=-1` shifts times dated at the end of each mean, as for matchup.
The years you give must be ones the output has: `analyze()` refuses a start before
the output's first year or an end after its last, and the app says so beside the box.

## Comparing multiple validation outputs

To compare validation reports from multiple simulations:

```python
import oceanval

oceanval.compare(
    model_dict={
        "model_a": "/path/to/model_a",
        "model_b": "/path/to/model_b",
    },
    view=True,
    ask=True,
)
```

This recreates the v0.2.0 comparison workflow and writes the shared comparison report to `oceanval_comparison/compare/_build/html/index.html`.

# Installation 

OceanVal should be used with Python versions 3.10-3.13.

You can install the latest release OceanVal from conda-forge as follows:

```sh
conda install conda-forge::oceanval
```

You can install the development version of OceanVal from GitHub using the following steps.

First, clone this directory:

```sh
git clone https://github.com/pmlmodelling/oceanval.git
```

Then move to this directory.

```sh
cd oceanval
```


Second, set up a conda environment. If you want the envionment to called something other than `oceanval`, you can change the name in the oceanval.yml file. 

```sh
conda env create -f oceanval.yml
```



Activate this environment.

```sh
conda activate oceanval
```

Now, sometimes R package installs go wrong in conda. Run the following command to ensure Rcpp is installed correctly.

```sh
Rscript -e "install.packages('Rcpp', repos = 'https://cloud.r-project.org/')"
```

Now, install the package.

```sh
pip install .

```sh
conda activate oceanval
```


Now, install the package.

```sh
pip install .

```
Alternatively, install the conda environment and package using the following commands:

```sh
    conda env create --name oceanval -f https://raw.githubusercontent.com/pmlmodelling/oceanval/main/oceanval.yml
    conda activate oceanval​
    pip install git+https://github.com/pmlmodelling/oceanval.git​
```

