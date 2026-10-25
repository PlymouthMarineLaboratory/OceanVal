# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OceanVal is a Python package that automates ocean model validation: it matches model output
against observational datasets (point and gridded), computes skill metrics, and generates a
Jupyter Book–based HTML validation report (with optional PDF/Word export). Core public API lives
in `oceanval/__init__.py`: `matchup()` (model↔obs matching), `validate()` (build the report),
`rebuild()`, `compare()` (multi-simulation comparison report), `analyze()` (a model-only
climatology/phenology/time-series report, no observations), `compare_simulations()` (the same for 2–5
simulations side by side), `create_recipes()` (scaffold a
matchup script from model output), `add_gridded_comparison()`/`add_point_comparison()` (register
custom obs datasets), `deep_clean()`, `fix_toc()`.

## Environment setup

Conda is the supported environment (`oceanval.yml`) — R packages (rpy2, r-tidyverse, r-ggtext,
etc.) and `cdo`/`nco` are required and not pip-installable:

```sh
conda env create -f oceanval.yml
conda activate oceanval
Rscript -e "install.packages('Rcpp', repos = 'https://cloud.r-project.org/')"
pip install .
```

Supported Python: 3.10–3.13.

## Running tests

**Always `python -m pytest`, never bare `pytest`.** The bare `pytest` entry point resolves the
*installed* oceanval in site-packages rather than the repo copy, so tests silently run against
stale code.

**Never run the matchup/validate tests from the repo root.** Several fixtures and test bodies
`shutil.rmtree` the `oceanval_matchups/`, `oceanval_report/`, and `oceanval_results/` directories
in the current working directory — real gitignored output can live under those names at the repo
root. Copy `oceanval/ tests/ data/ setup.py pyproject.toml` into a scratch directory and run
`python -m pytest` from there instead.

```sh
python -m pytest tests/                     # full suite (matchup/validate set: ~4 min)
python -m pytest tests/test_gridded_failures.py   # ~1 min
python -m pytest tests/test_create_recipes.py -k some_test  # single test
```

Coverage config (`.coveragerc`) excludes `tests/` and `oceanval/fvcom.py` (undocumented).

CI (`.github/workflows/python-app.yml`) builds a micromamba env with nctoolkit, cartopy, R/tidyverse,
jupyter-book, pandoc, texlive, then `pip install .` and runs the tests as six jobs side by side (matrix `group`: five named sets of test files balanced by run time, and a sixth that runs every file not named, so a new test file is still run); each job has its own checkout, which the cwd-relative `oceanval_matchups` folders need, and prints its 30 slowest tests (`--durations`). A new slow test file should be added to a group in the workflow. `.github/workflows/macos-app.yml`
runs the same groups on `macos-latest` (Apple silicon, 90 minute limit per job): keep the two
workflows' group lists the same.

On macOS (see the fixes from a Mac, 2026-10-22): there is no `timeout` command (use pytest's
`--timeout`), no `os.sched_getaffinity` (`matchall._index_workers` falls back to `os.cpu_count()`;
tests patch it with `raising=False`), and the report notebooks' kernels import the *installed*
oceanval, so reinstall (`pip install --no-deps .`) before running report tests against changed code.
`_run_filtered` waits for the process, not the end of its pipe (`_OUTPUT_GRACE`): nctoolkit's Manager
servers in a kernel that died keep the pipe open, which hung the interim report's builder.
Never put conda's `lib` on `DYLD_LIBRARY_PATH` (`utils.restrict_r_to_conda` sets `LD_LIBRARY_PATH` on Linux only):
dyld swaps libraries by file name, so ImageIO's own `libPng.dylib` became conda's libpng (the file system ignores
case) and every report kernel inheriting it could die in a `%%R` figure (SIGBUS in `QuartzBitmap_Close`).

## Architecture

### Report pipeline

`validate()` and `compare()` don't render HTML directly — they build a **Jupyter Book**:

1. Notebook/chunk templates live in `oceanval/data/` (`*.ipynb`, `chunk_*.pytemplate`,
   `gridded_template.ipynb`, `point_template.ipynb`, `_config.yml`, `_toc.yml`).
2. `oceanval/chunkers.py` (`add_chunks`) and code in `__init__.py` assemble per-variable notebooks
   by string-substituting `chunk_*` placeholders (e.g. `chunk_point_surface`, `chunk_seasonal`)
   depending on what data/options are present, then use `jupytext` to sync `.py:percent` ⇄ `.ipynb`.
3. `_build_book()` in `__init__.py` runs the notebooks with `nbconvert` and writes each as an HTML
   page with the navigation (`_write_offline_report_pages`, which reads the book's `_toc.yml` captions)
   to produce the HTML report. OceanVal needs jupyter-book >= 2 (pinned in `requirements.txt`) but never
   runs `jupyter-book` itself; there is no jupyter-book 1 (Sphinx) path or `_config.yml` any more, so
   don't add version checks. `_render_offline_report_pdfs`/
   `_render_offline_report_word` (pandoc-based, with custom python-docx post-processing in
   `_polish_word_report`) produce the optional PDF/Word exports.
4. **The HTML report is the baseline** — it's what users read day to day, and each HTML page is a
   standalone document numbering its own figures/tables from 1. PDF/Word are *derived* exports;
   changes to export code must leave HTML output (including figure/table numbering) unchanged.
   Only the *combined* PDF/Word documents (concatenating all notebooks) get chapter-prefixed
   numbering — per-page PDF/Word exports mirror their HTML page and still number from 1.

### Matching and recipes

- `oceanval/matchall.py` (`matchup`) and `oceanval/gridded.py` (`gridded_matchup`) do the actual
  model↔observation matching for point and gridded data respectively; `oceanval/fvcom.py` handles
  FVCOM-specific regridding/preprocessing (deliberately excluded from coverage/docs).
- `oceanval/parsers.py` defines `Validator` (the `definitions` singleton, exposed as
  `add_point_comparison`/`add_gridded_comparison`) — a dynamic attribute-tracking object recipes
  register themselves onto — and `find_recipe`, which builds obs-source URLs without touching the
  network (useful for validating recipe arguments offline).
- `oceanval/create_recipes.py` scans model output directories, maps model variables to
  observational variables via netCDF `long_name` matching, and writes out a matchup script with
  live/commented recipe blocks for built-in datasets (WOA23, NSBC, OCCCI, GLODAP, ...).
- `oceanval/pyplots.py` holds the plotting functions (maps, scatter, Taylor diagrams, seasonal
  grids, etc.) used inside the generated notebooks.
- **Earlier matchups**: `matchup()` never removes matchups from `out_dir`. It records the ones each run made or kept
  in `oceanval_matchups/last_matchup.json` (`selection.save_last_run`, from `matchall._Told.made`; removed by
  `forget_last_run` once the matchups are confirmed, so a run that does not finish leaves none). `validate(previous=)`
  is True (all), False (`selection.last_run` only) or a list of earlier ones as `variables` takes them;
  `matchup(overwrite=)` also takes such a list (`selection.picks`). In the window, an out_dir with matchups
  (`app.existing_matchups`, a row per "variable/source") gets a grid on the setup page, "What do you want to do with
  existing matchups?": per row one of two, Use / Replace/Ignore (radios with the tick look, Use to start with; a radio
  at the top of each column chooses it for all rows). Replacing and ignoring were one and the same, so they are one
  column. **Only a used matchup is kept as it is**: one replaced is made again if this run matches it up, and left
  out of the report if not (form `overwrite` only; `check_setup` gives overwrite = those replaced, previous = those
  used, all → True, none → False). Matchup only calls the column Replace, and those used are reused. A confirmation tick under the grid (`existing_confirmed`) is required in both, as users may
  not notice every row starts on Use; it is reset when the list shown changes. The demo's own folder (`check_setup(demo_dir=)`, `out_dir_is_demo`), where every model tried keeps its
  matchups, has no grid: overwrite=True, previous=False. Analyze's `analysis_forms.check_setup` calls the same `check_setup`
  but shows no grid, so it drops the answers (`_NOT_ANALYZED`: overwrite, previous, existing_confirmed); an error kept for a
  box the page does not show disables Continue with nothing said. `previous` is never passed to the interim report
  (`live.NOT_INTERIM`), and `_checked_report` adds the earlier keys to `variables` when some of the run's are unticked.
  Analyze's own rule is about an analysis in the output directory (`oceanval_analysis`, which `analyze()` replaces whole): the setup page
  (`#f-replace_analysis`, shown when the probe's `out_dir_has_analysis`) and `analysis_forms.check_setup` (`replace_analysis`) require a tick
  to replace it **and** a named directory (an empty `out_dir` box means the cwd and is refused, `NAME_THE_DIRECTORY`); the tick is reset when
  `out_dir` is edited. Compare simulations' `oceanval_simulation_comparison` warning is still only a warning.

### Recipes of the user's own (`.oceanvalrc`)

- `oceanval/user_recipes.py` reads and writes the JSON `.oceanvalrc` files: one in the
  directory worked in (wins) and one in the home directory, or the file `$OCEANVALRC` names
  (which the tests point at a temp file, see the autouse `oceanvalrc` fixture in
  `tests/conftest.py`). It imports nothing from OceanVal but `utils`, so `parsers` can use it.
- `parsers.find_recipe` falls back to it for any key that is not in `user_recipes.BUILTIN_SOURCES`;
  a user point recipe becomes `obs_path` with `recipe=None` (csv files), unlike ICES.
- `create_recipes.gridded_catalogue/point_catalogue/recipe_variables(cwd)` merge the user's
  recipes into the built-in catalogues, which stay as constants (tests parametrise over them
  at import). `cwd` is threaded explicitly, never read from the process, so the window's `App.cwd` wins.
- `recipe_checks.py` opens the data as matchup will (disk/THREDDS/url); server data is looked at
  in a killable subprocess (`RemoteCheck`). `recipe_forms.py` is the window's form logic.
- A variable only the user's recipes are for is identified by name or whole-phrase `long_name`,
  only if exactly one model variable matches; otherwise its model variable is left blank.
- The own data step can save an entry as a recipe too ("Add this data for use now and in
  future"): view `own_future` (`App.own_future`/`own_future_save`), built from the entry by
  `recipe_forms.from_own_data`/`missing_boxes`/`with_boxes`, assumptions shown in amber.
  `create_recipes.replaced_recipes` leaves a user recipe with the same variable and source as
  an own data entry out of that run's recipes window, so it is not registered twice.
- The window's settings (`oceanval/user_settings.py`: colour theme, whether the demo is offered)
  live under `"settings"` in the *global* rc only, beside `"recipes"`; `load()` never raises.
  `recipes_gui.render_page` puts `data-theme` on each page's `<html>`; the themes are token
  overrides in `data/gui.css` (`[data-theme="dark"]` etc.), so colours in the pages must be
  tokens, never hex, and the default theme must stay pixel-identical.

### What OceanVal remembers (`.oceanvalcache`)

- `oceanval/user_cache.py` keeps a JSON `~/.oceanvalcache` (or `$OCEANVALCACHE`, which the
  autouse `oceanvalcache` fixture in `tests/conftest.py` points at a temp file) of the simulation
  directories matched up before. `matchup()` records its `sim_dir` as soon as the user confirms the matchups ("Are you happy…" → y); the window's
  setup page lists them (`state()["setup"]["recent"]`, those that still exist) in a list opened by an arrow inside the `#f-simdir` box (Validate and Analyze share it; Compare simulations' `#sc-dir_N` boxes have `makeRecent`).
  Each entry has an x (`recentEntry`/`forgetRecent` in `oceanval_app.html`, empty button with the × drawn by CSS so an entry's text stays its path)
  that posts `/api/forget_sim_dir` → `App.forget_sim_dir` → `user_cache.forget_sim_dir`; Delete does the same for the marked entry.
- The package never ships or creates the file ahead of use, and writes are read-merge-write: other
  keys and entries survive, and an unreadable or newer-version file is left untouched. It must
  never raise, as a cache must not stop a run.

### The data store (`oceanval/data_store.py`)

- A folder set in Preferences (`user_settings` `"data_store"`, `"data_store_use"`: `"always"` |
  `"fallback"`) keeping downloaded **built-in** observations for later matchups; every
  `matchup()` uses it (`data_store=False` opts out). Only comparisons with `builtin` set (the
  recipe key, kept by `add_gridded_comparison` only when the recipe's own data is used) and ICES
  recipes go in it, never user data.
- Everything lives in `<folder>/oceanval_datastore/`. Its safety rules, keep them:
  **every write, removal and mkdir goes through `_guard`** (path as written and realpath must be
  inside that folder); only files named as OceanVal names pieces (`PIECE_NAME`, uuid hex) are
  ever used or removed, whatever the index says, and links never; an existing
  `oceanval_datastore` with content is only used if it has the `MARKER` file or an index; the
  store is refused inside a folder OceanVal empties (`CLEARED_FOLDERS`: oceanval_report,
  _results, _interim_report, _comparison, _matchups, _analysis); folders are made one level at a time, and
  the chosen folder is never created. Replaced pieces are "retired" in the index and removed by
  `_tidy` a day later (another run may be reading them). `tests/test_data_store.py`'s
  `TestNothingOfYoursIsRemoved` records every remove/move to check this.
- Pieces in `oceanval_data_store.json`: gridded = one server file (COBE2: one year) × variable ×
  box (model extent snapped out to whole degrees, whole globe at ≥90% area, union on widening) ×
  levels (`top` only for WOA23 surface); ICES = dataset code × year × box, raw export rows.
  Hooked in at `gridded.py` before `_open_obs` and in `ices.download_point_data`.
  Preferences' **Run a check on the data store** button (`data_store.check`, endpoints
  `/api/data_store_check` and `/data_store_check`) only reports, via `check_reply`: it never
  writes, removes, makes or locks anything (a test fails if it does).
  COBE2's THREDDS server rate limits (HTTP 429): `data_store.download_instead` swaps in the
  plain file download (`DOWNLOADS`) when it turns requests away or fails, with or without a store.

### Analyze (`oceanval/analysis.py`)

- `analyze()` works on model output alone: per variable (name → model variable, `+` sums),
  a monthly + annual climatology (surface = `topvalue`, bottom = nctoolkit's
  `bottom(choice="value")` i.e. the deepest non-missing value, water column = `vertical_mean` or
  `vertical_integration` with the thickness, at its first time, or `fixed=True` for
  `"z_level"`; maps of the annual mean and of each month, but no domain-mean monthly line, which
  the seasonal cycles' mean seasonal cycle already gives) and/or phenology (peak day of the surface daily climatology, daily output
  only). Each month of each year is averaged first. Everything goes in
  `<out_dir>/oceanval_analysis/` (`results/` netCDF + `analysis.json`, `report/` book,
  `report.html` link), which it replaces whole.
- The peak day is worked out in `analysis.peak` from the climatology sorted by month/day,
  not nctoolkit's `phenology()`: CDO dates a daily climatology's days in the last year
  having each (29 Feb comes first), which nctoolkit would split by year.
- The report reuses validate's machinery (`chunk_start`/`chunk_end`, `add_chunks`,
  `_run_jupytext`, `_build_book`) with its own templates: `analysis_overview.ipynb`,
  `analysis_template.ipynb`, `chunk_climatology`/`chunk_phenology` (blanked when not
  chosen); figures come from `oceanval/analysis_plots.py` (nctoolkit `pub_plot`/
  `panel_plot`, regridded to lon-lat when a grid cannot be drawn).
- **The report is built a variable at a time**: `oceanval/analysis_report.py`'s `InterimBook`, a
  thread of the run, gets each variable once `_work_out` is done and `analysis.json` is rewritten
  (`analysis.save_info`, atomically, the variables so far), runs its page and the overview with nbconvert
  into `report/_build/raw`, and writes the navigation with `_write_offline_report_pages(raw_dir=, banner=)`
  (`live.banner_html`) while the next variable is worked out. `finish()` takes the banner off and makes
  `report.html`: the pages are the final report, nothing is built again. `<folder>/status.json` is followed by the window as
  validate's interim report is (`App._analysis_interim`, `interim["which"]` = `analysis`/`simcompare`).
  `compare_simulations` does the same, its loop variable-first. **The thread must take no lock a forked
  nctoolkit worker could need**: nbconvert's output goes to `report/build.log`, its own lines by `os.write`.
  The pieces Analyze and Compare share: `analysis.Templates`, `prepare_book`, `write_page`, `link_report`.
  Tests that do not need the report use the `no_report` fixture (`tests/conftest.py`'s `no_book`, a
  stand-in for `InterimBook` that keeps the saved info).
- **Every Analyze figure is drawn by running its own code**, the script the report's
  "Copy code to reproduce figure" button copies (`analyze(figure_code=True)`, the window's
  required Yes/No): each `analysis_plots` function builds a standalone script (nctoolkit/
  pandas/matplotlib, everything it uses written out as literals, OceanVal only for
  `domain_mean` and a transect's map, `oceanval.transects.plot_map`), runs it with `_run`, and returns a `Drawn`; the chunks show it with
  `ap.show(drawn, figure_code, title)`. **Any new Analyze feature that adds or changes a
  figure must keep this up**: build it as such a script in `analysis_plots` (never draw it
  directly in a chunk or helper the script can't see), show it with `ap.show`, and add it to
  `tests/test_analysis_plots.py`, which runs every copied script in a fresh process and checks
  it draws the same image. With `figure_code` off, the report must stay as `plt.show()` made it.
- **The order of the CDO chain matters on big files** (`analysis._model_data`): nctoolkit applies the last operator asked for last, so
  what cuts the data down is asked for first: `selname` (the variables; before the first time step and before a `time_shift`), then
  the surface (`topvalue` of each part of a sum, which are then added; before `as_missing`, as `matchup` takes it), then `as_missing`,
  then the bottom (`bottomvalue`, which needs the missing value set), and the rename/`expr` last. The surface is not nctoolkit's `top()` (`-sellevidx,1`): CDO's `selname`/`seltimestep`/`sellevidx`/`topvalue` read every record
  and drop the rest, and `top()` keeps a vertical dimension of one level. **`oceanval/surface.py`** (shared by Analyze, Compare and matchup)
  reads the surface by one `-select,name=..,levidx=1[,timestep=..]` (CDO's `select` filters records before reading them) followed by
  `-setzaxis` (a surface axis, as `topvalue` leaves it, so no vertical dimension of one level), with `CDI_CHUNK_CACHE` (CDO < 2.6) and
  `CDI_CACHESIZE_IN` (CDO 2.6+, which ignores the old name; `surface.CACHE_VARIABLES`) at 1 MB while it
  runs (`surface.chunk_cache`): the default 64 MB cache holds a whole chunk (a time step of all 51 levels, 22.7 MB, in the user's
  `ptrc_T`), so HDF5 would read it all to give one level. It reads the first level when `surface.first_level_is_surface` finds, from the first
  file, a vertical axis `positive: down` and the first level missing exactly where `topvalue` is on the first step; anything else keeps
  `topvalue`. The small cache is a separate answer, `surface.small_cache_helps`: only with no compression and a CDO that says it takes
  the cache size (`CDI_CHUNK_CACHE_INFO`/`CDI_CACHE_INFO`; 2.4.1 and before cannot). **Never set a small cache for compressed files**:
  HDF5 would decompress the whole chunk again for every level. A compressed file's first level is read with HDF5's own cache, which
  still decompresses each chunk once, as `topvalue` does, but spares CDO the other levels (measured on a month of deflate-9 daily
  `ptrc_T`, 51 levels a chunk, CDO 2.6.5: 3.0 s -> 2.3 s from the page cache, 6.1 s -> 5.5 s from NFS). Analyze/Compare keep the answers
  in `row["surface_fast"]`/`row["surface_small_cache"]` (`locate_rows`); matchup decides once per variable (`matchall._surface_is_first_level`
  in `point_match`, passed to `mm_match` as `surface_fast`/`small_cache`; `gridded.model_dataset`, which returns both,
  for the gridded model read, which also selects the variables, surface and shift in that order). `mm_match` chooses the days with
  `select,timestep=` for every layer (`seltimestep` reads every time step of the month), measured on the user's files at 8 s -> 1.6 s
  for six days of one variable, 0.35 s with the surface read from the first level.
  `tests/test_analysis.py::TestOrderOfTheChain` checks the chain's order (`ds.history`) without running it.
  **A file a month**: when each file holds the times of one month of one year and no month is in two files
  (`time_res.one_month_per_file`, on the shifted times; `row["month_per_file"]` set in `locate_rows`, `month_per_file` in
  `gridded_matchup` from its final `paths`), `monthly_means_of`/`gridded.model_dataset` end the chain with `tmean(align="left")`,
  so `merge("time")` (which runs the chain on each file first) writes one time per file, and the `tmean(["year", "month"])`
  after the merge is left out. Point matchups and the daily fields (phenology, `first_step_of`) do not use it.
- **Model output is only ever processed by nctoolkit/CDO, which streams it.** Model files can be many GB: xarray is for
  coordinates (lon/lat) and the small reduced files OceanVal writes itself, **never `.load()` or a whole `.values` of a
  model file** (`sim_compare.grid_of`, which the grid checks of `compare_simulations` and of the window's grids step use,
  has CDO write one level of one time step of one variable first and opens only that, `analysis.peak` reads a band of rows at a time,
  `analysis_series.station_values` a year of times at a time; `tests/test_sim_compare.py::TestSameGridIsLookedAtLightly`
  fails if a data variable is read).
- **Every netCDF read in the window's own process holds `oceanval.netcdf_lock.LOCK`** (`analysis._FILE_READ_LOCK` is it):
  netCDF-C/HDF5 are not thread-safe and xarray gives each file a lock of its own, so two of the window's threads opening
  files at once (the grids and the missing-value probe did) crash it with a segmentation fault. A new read in a thread of
  the window (xarray, netCDF4, nctoolkit's `contents`) must take it; CDO runs in its own process and needs none.
- Long-term (annual) and seasonal (monthly) time series are `oceanval/analysis_series.py`: the
  monthly means per layer (`analysis.monthly_means_of`, which `climatology_of` also uses), then
  domain/region/station means saved as small CSVs (year/month integers, so 360-day calendars
  work); annual = mean of a year's monthly means, a year with months missing is flagged, not
  dropped. **All spatial means are nctoolkit `spatial_mean`** (`analysis_series.robust_spatial_mean`: on a copy, warnings caught; if nctoolkit warns the grid has no cell bounds it is redone after `to_latlon` by `nn` and by `bil`, grid resolution/extent estimated by `estimate_grid`, bil used, the report notes it), regions are the
  `<nwes|global>_subdomains.nc` or the user's file, already lon-lat grids, so `region_values` regrids the raw model
  (bil) to the file's grid once, and each region (`as_missing(0)` mask, `multiply`) is a `spatial_mean` there (the mask is
  never regridded to the model, and no `to_latlon` fallback of its own; the domain mean keeps `robust_spatial_mean`);
  the domain series is `analysis_series.DOMAIN` = "Full Domain" (the whole model grid; a file region of that name is skipped in `region_values`), and the time series figures (`analysis_plots.long_term_plot`/`seasonal_cycle_regions`, `_layout`) give it a panel of its own, first, beyond `MANY_REGIONS` regions (never a line in each region's panel). **The seasonal series are only ever drawn as the climatological cycle** (the mean of each month of the year over the years, `analysis_plots.cycle_code`, for the domain, regions and stations; Compare's `sim_plots` too): each year's months are not drawn, though the monthly CSV keeps them;
  stations are the nearest cell with values (haversine, refused beyond 2 cells), not cut by
  `lon_lim`/`lat_lim`. The chunks are `chunk_longterm`/`chunk_seasonalseries` (letters only:
  `chunkers.is_chunk` matches `chunk_.[a-z]*`).
- `analyze(cores=)` has **no default** (it must be given, as the window's empty cores box
  enforces), the years of every analysis must lie inside the output's (`analysis_forms._years` in the window,
  `analyze()` against `row["times"]`: a start before its first year or an end after its last is refused), and `as_missing` is applied in `_model_data` and to the thickness, as matchup
  does. **Nothing is pre-selected in the window**: the forms start empty (rows, ticks, years,
  thickness, `how`); the years of the output are only placeholders/hints. The assumptions are
  the missing-value boxes', until the user edits them: the value `oceanval/missing_probe.py`
  finds (xarray, the surface at the first time step of up to 10 files of the first placed variable,
  the value many cells share in every file; started in a thread when the options step opens by
  `AnalyzeSteps._probe_missing`, `state.analyze.missing_probed`, shown in amber `is-found`), else for a
  NEMO-ERSEM simulation (`recipes_gui.is_likely_nemo_ersem`) 0–0 in the `is-ersem` style.
  `analysis_forms.prefill` (what OceanVal could fill the variables with) is unused for that reason;
  the "Your name" box instead has an arrow listing `analysis_forms.presets` (every recipe variable
  with the model variable `create_recipes.voted_mapping` finds, `state.analyze.presets`), through the
  `yearList` machinery (`input.onPick`); choosing one fills Model name in red/bold unless the user typed it.
- The long-term series also take `window` (rolling mean of the annual means, trailing, 1 = none) and `show` (`absolute` | `absolute_change` | `percentage_change`, from the first year with a value), `analysis.SHOWS`/`_series_options`, applied in `analysis_series.smooth_and_change` after `annual_series` (the missing/incomplete years are worked out before, from the means as they are); the csv holds the values as shown and `saved["show"]`/units `%` say what they are. In the window they are two columns after Water column in the long-term pop-out only (`is-series` cells), `analysis_forms._check_series_options`.
- Map temporal changes are `oceanval/analysis_changes.py` (`map_changes={name: {"historical":
  {start, end}, "future": [1-2 of those], "plot": "absolute"|"absolute_changes"|"changes",
  "change": "absolute"|"percentage", + climatology's layers}}`, checked by `check_entry`): each
  period is a climatology's annual mean (`analysis.climatology_of` then `tmean()`) of its own
  years, saved as `<key>_<layer>_historical/future1/future2.nc`, and the changes
  (`derive`: future minus historical, or 100·diff/|historical|, missing at 0, done in xarray on
  the saved files) as `<key>_<layer>_change1/2.nc`, all always, whatever `plot` says: `plot` only
  chooses which maps are in the figure: one figure and one caption per layer,
  `analysis_plots.change_maps`, a panel for the historical period, the futures and/or the changes,
  each titled by its years only (`2000-2018`, `2000-2018 minus 1970-1989`, `... relative to ...`),
  the maps of the periods on one scale and under one colour bar, the changes (`RdBu_r`, symmetric
  about 0) under another, both at the bottom, drawn as a script: each map by `pub_plot` into a
  gridspec of half-width columns (`places`), so a short row (two changes under three periods) is
  centred, `panel_plot` being unable to; the bars are made by `fig.colorbar` over each group's axes.
  **It is not in the generic per-kind loop of `analyze()`** (its files and dates are per period,
  so the years between periods need not exist), whose `entry["start"/"end"]` is the union only to
  pick files in `check_arguments`. Chunk `chunk_mapchanges`. In the window: `chg-dialog` (a
  table, the layers as climatology's, and a row of its own under each variable for Plot and
  Change, both empty to start, in `changesRows`), `analysis_forms.default/clean/check_map_changes`
  (the period boxes are `historical|future1|future2` + `_start|_end`, errors prefixed with the
  period's name; the second future is both years or neither), and it shares the cell thickness
  with the other pop-outs (`THICKNESS_KINDS`, `LAYER_ROWS` in the page's JS).
- Transects are `oceanval/analysis_transects.py`: for each variable on its own (`transects=
  {name: [lines]}`), any number of north-south/east-west lines (checked by
  `oceanval.transects.check_transect`, as validate's), each with its ends and at least one of
  `climatology` (surface monthly climatology), `trends` (surface, `how` "absolute" slope per year /
  "absolute_change" last year minus first / "percentage_change", with an optional `window`: a nctoolkit
  `rolling_mean` of the annual means, default 1 = none), `vertical` (annual-mean section) and
  `vertical_trends` (the same `how`/`window`, at each depth of the section: `annual_means(vertical=True)`,
  `derive_trends(depths=)`), **each option with its own `start`/`end` years**. The window shows them as
  **Surface** and **Vertical** parts, each a Climatology and its Changes ("Changes" is the UI's word for
  trends), the ends in one line, the rolling mean right of the changes' years. The trends' annual means are nctoolkit's
  (`annual_means`: complete years only, `tmean("year")`, then `rolling_mean`), not numpy. The monthly means come
  first (`analysis.monthly_means_of`, once per layer over the union of years, the surface or
  `LEVELS`), then nctoolkit's `to_transect` on a copy, then `derive` makes a small CSV per
  option (`<key>_transect_<n>_<option>.csv`: `position`, `depth` or `month`, `value`) that the
  figures (`analysis_plots.transect_map`/`transect_figure`, `chunk_transects`) read. `vertical`
  and `vertical_trends` need `thickness` like the water column: `z_level` uses the levels as depths, otherwise the
  thickness is extracted along the line and each cell put at its depth, then the section put on
  `N_DEPTHS` shared depths. A year with months missing is left out of trends, and the points of a
  line need not form a lon-lat grid (`_along` reads both). In the window each variable has its own
  transects (`analysis_forms.default_transects`/`clean_transects`/`check_transects`; the cell
  thickness box is shared with the other pop-outs, `THICKNESS_KINDS`). Needs nctoolkit 1.3.6+ (the
  `oceanval` env's 1.3.0 reports it as validate's transect does).
- Every start/end year box of the Analyze pop-outs (`yearBox`, transects' `optionYears`) goes through `withYears` in `oceanval_app.html`: an arrow at its right end (or Down) opens one shared `year-list` (fixed, so the pop-out body can't clip it) of `first..last` of `placed[].years` (the dates check has no year missing between). A new year box must be made with `yearBox`/`withYears`. The list itself is `OceanValGUI.withChoices` in `data/gui.js` (CSS in `gui.css`), shared with the recipes window's First/Last and the name boxes' presets (`choiceHints`, `onPick`).
- The window's Analyze steps live in `oceanval/analysis_app.py` (`AnalyzeSteps`, a mixin of
  `App`; views `analyze_variables`, `analyze_files`, `analyze_options`; six pop-outs, the
  time series' two sharing one dialog with the climatology, the regions/area card, the
  stations, the map changes' own, `chg-dialog`, and the transects' own, `trans-dialog`, with a card for each transect), their forms and
  checks in `oceanval/analysis_forms.py`. The files page reuses `model_check.ModelCheck`
  (a row with no `time_res` has its files' own worked out) and asks the time_shift
  question first when `time_stamps.check_rows` finds end-stamped times. The run writes
  `analysis.py` and runs it as `app_child analyze <path>`.

### Compare simulations (`oceanval/sim_compare.py`)

- `compare_simulations(simulations, variables, ...)` does everything `analyze()` does for 2–5 simulations
  and builds one report, simulations side by side. `variables={name: {simulation: model variable}}`; every other
  analysis argument is `analyze()`'s, **the same for every simulation, with one set of years that every simulation
  has** (each simulation is checked by `analysis.locate_rows`, which `analyze()` uses too). Units must agree. It is
  not the validate-side `compare()` (`oceanval_comparison`, the window's "Compare existing validations"): every name of
  this feature is `simcompare`/`sim_compare`/`sc-`, its folder is `oceanval_simulation_comparison` (in
  `data_store.CLEARED_FOLDERS`).
- Each simulation is worked out by `analysis._work_out` into `results/<sim key>/`, with the existing file names and
  CSV schemas, and an `analysis.json` of its own, so `analysis_plots` can still draw one simulation at a time
  (`monthly_maps(limits=)` and `change_maps(also=)` take the scale all the simulations share). `comparison.json` says
  what is where, and each simulation's colour (`sim_compare.COLOURS`, Okabe-Ito, in the order of the simulations).
- Combined figures are `oceanval/sim_plots.py`, with the same contract as Analyze's: each is drawn by its own script
  (colours and files as literals), `tests/test_sim_plots.py` redraws each in a fresh process and compares pixels. Maps are
  a panel for each simulation on **one colour scale**; series are lines coloured by simulation, **a panel for each
  series** (domain, each region, each station); transects are lines coloured by simulation, or a panel each. The report
  is Analyze's `InterimBook` with its own `analysis.Templates` (`sim_report.templates()`, `simcompare_*.ipynb`,
  `chunk_sim*.pytemplate`: chunk names are letters only).
- **Grid unification** (`oceanval/sim_unify.py`): `unify={"lon","lat","res"}` (nctoolkit `to_latlon`) or `{"baseline": name}`
  (`regrid`, the baseline itself untouched; a simulation already on the baseline's grid is not regridded, as bilinear
  remapping spreads missing cells). It is applied through `row["unify"]` in `analysis.monthly_means_of`,
  `daily_climatology_of` and the transects' thickness: **after the vertical reduction** (surface/bottom/column mean) of each
  layer, so all analyses see the same cells. A cell missing in any simulation is missing in all: the mask is made in
  `make_common_grid` (before any analysis) from the **first time step** of each simulation's field of that layer, on the common grid,
  **in xarray, written with an explicit `_FillValue`** — CDO arithmetic on NaN-fill-valued files does not propagate missing,
  so `x*0` / `ds_mask > 0` tricks (as the validate-side `compare()` notebooks use) wrongly give 0 where a value is missing and
  turn a real 0 into missing. Vertical transects need the same number of levels in every simulation.
- The window: the landing page's third tile, views `simcompare_sims` / `_variables` / `_files` / `_grids` (mixin
  `oceanval/sim_compare_app.py`, boxes in `sim_compare_forms.py`, JS block "Compare simulations" in `oceanval_app.html`), then
  **Analyze's own options step** (`analyze_options`, `state.analyze`) with the variables as they are in all the simulations:
  `analyze_placed` is `sim_compare_forms.combined` (the fewest levels), the dates check is `_sc_check_rows` (the years every
  simulation has, the coarsest time resolution), the thickness name must be in every simulation's output (or a file, or
  `z_level`), and `analyze_run` hands over to `simcompare_run` (script → `app_child simcompare`). `App._analyze_owner` says whose the
  `analyze_*` state is; choosing the other tile starts it afresh. One time-shift question covers the simulations whose times look like ends.
  **The files' times are read once**: the dates check (`model_check.check`) reads every file and saves the unshifted times (`times.pkl`);
  `simcompare_run` copies each to a temporary folder of its own (`_sc_keep_times`, removed when the simulations change or at exit) before the checks' folders are
  removed, and the script gives each simulation a `times_index`, which `locate_rows` loads (`model_check.load_times_index`: only files whose size and
  modification time are unchanged) so `index_times` reads the rest. `locate_rows` also asks a stream for its time resolution only to
  choose between streams, and reads a calendar once per pattern.

### The `oceanval` command and its browser windows

- `oceanval/app.py` is the `oceanval`/`OceanVal` console script (also `python -m oceanval`; `setup.py` defines
  `OceanVal` only where file names are case sensitive, as on macOS/Windows the two are one file and pip
  failed to upgrade over them): a
  127.0.0.1 server with a token, whose page (`data/oceanval_app.html`) opens on a landing
  page (view `home`) of Analyze (see above) and Validate, which leads to the validation page
  (view `start`) for choosing matchup+validate / matchup only / validate, the simulation (live-checked via `/api/probe`),
  the create_recipes window, and a progress panel for the run (its console is shown only if the run fails).
- create_recipes runs in a thread of the app; `recipes_gui.hosted_by(app)` makes its window
  one of the app's pages (`/recipes/`) instead of a server of its own. Matchup/validate run in a
  separate process (`oceanval/app_child.py`) whose stdout is the console.
- **The years a validation can choose** come from the files' time axes, never names (`oceanval/output_years.py`):
  `App.probe` (validate actions, view `setup`) starts `App._read_output_years`, an `output_years.Reading` that runs
  the module **as a script by path** (`python -I`, importing no OceanVal: the package import takes 2.6 s) and keeps the
  answer in `.oceanvalcache` (`user_cache.output_years`, stamped with `output_years.RULE` + the files' count/size/mtime;
  bump `RULE` when the answer for the same files changes). Only the main output counts: streams with at least
  `max(2, largest/10)` files (all, if every stream is one file); a post-processed one-off running past the output
  (1985–2022 beside 1981–2017 in the user's MIZER run) is left out unread. Per counted stream: first/last by natural name
  order and by mtime, then 30 random files as a check, a full read if one is outside. h5py reads only the time variable
  (4x faster than netCDF4, which reads every variable's metadata on open; netCDF4 for netCDF-3), shared over a spawn pool
  of 4 only when run as the script (a worker of `oceanval.output_years` would import the package). `show_recipes`
  waits for it (`OUTPUT_YEARS_WAIT`) and passes `context["output_years"]`: the recipes window's First/Last boxes get
  those years as their list (`withChoices`) and placeholders; any year can still be typed (the years found can be
  wrong), one outside them is only warned about. The setup page shows no years for validate; the `preparing` view
  is a centred box ("Finding key information about your simulation", `renderPrepare`) with no page title, whose
  tracker says "Found output for the years …" once read. The same box, with only "Identifying files that meet criteria. Please wait!",
  is the `running` view while matchup finds the files (`run.identifying`, no question yet): no page title, text or code.
- **The grid of the matchups** (`#progress-grid`, `renderMatchupGrid`): matchup tells the window of each matchup through
  `prompts.tell` (nothing at a terminal; `app_child.PROGRESS_MARKER` lines, which `Run._take` passes to `App._matchup_news`,
  never the console): `matchall._matchups_news` lists them first, then `matchall._Told` says each is `matching`/`made`/`not_made`
  (gridded through `gridded_matchup(on_matching=, on_failed=)`), keyed by `live.matchup_key`, then `matchups_finished`.
  The interim report's `status.json` gives each page by the same key (`included`, `building_key`, `added`, `failed`).
  `state()["run"]["progress"]` is kept through the report run after it. **The grid is timed**: a Time column beside Matchup (counting
  up while it is `matching`, its time as it ended after, blank for one told `made` without being started) and a "Time elapsed" row
  under it (the matchup run plus the report run, not the wait between them). The times are taken by `App._matchup_news` as the news
  arrives (`started`/`took`/`banked`/`since` on `matchup_progress`; `_run_finished` stops the clock, `start_run` of the validate run
  resumes it), never in the child; `App._clock` is the one clock (tests patch it, never `time.monotonic`). The page is sent seconds as of
  each state (`item["seconds"]`, `progress["elapsed"]`/`["running"]`) and counts on from there itself (`setClock`/`tickClocks`,
  `performance.now()`), as a state is only sent when something changes. The run's output (`#run`) is shown **only when a
  run fails** (the user's rule), and **the command that runs (`python analysis.py`) is never shown**: not as a label on the
  progress panel or the output's header, nor echoed as `$ ...` in the console (the user's rule: it is of no interest to them;
  `start_run`'s `label` is kept but not displayed). Only the two pages that tell the user how to run a script they chose
  to save, not run, show one (the window's script-saved view, create_recipes' "Script written" page); the interim report's card (`#interim`) is above the grid. **Model extraction** (the column
  before Matchup, which still covers the whole matchup) is how many of a matchup's model files have been read, news
  `{"kind": "extraction", key, total, done, finished}` (`cdo_watch.news`; `App._matchup_news` keeps counts from going down and clears
  them when a matchup is started again). Point matchups count in their own loop over the files (`_Told.files`, at most a count a second,
  but always the first and last). A gridded matchup's files are all read in nctoolkit (`ds_model.merge("time")`), which says nothing,
  so `_Told.reading` (`gridded_matchup(reading=)`) runs **`oceanval/cdo_watch.py`**, a process of its own, run as a script by path
  and importing no OceanVal code. Every 0.5 s it lists the matchup's descendant processes (psutil) and counts the files whose CDO run
  has been seen and has ended: never more than were read, and files read faster than that may be missed until the end. It writes its
  counts straight to the shared stdout, one `os.write` per line. It is not a thread because nctoolkit forks its pool meanwhile. It runs
  only in a window (`prompts.news_marker()`, which `told_by(teller, marker)` sets in `app_child`). Do not count them by patching
  nctoolkit's tqdm, by running the files in batches, or by a callback in nctoolkit (the user ruled these out as unreliable, or as changing
  too much). **Observation extraction** (between Model extraction and Matchup) says only whether a matchup's
  observations are being read or downloaded, or have been, with no count (the user's choice): news `{"kind": "observations", key,
  state: "reading" | "read"}` from `_Told.observing`/`observed`. A point matchup's span is the making of its dataframe in
  `point_match` (the ICES download or the csv files, then the year/month/day clean-up), before its model files; a gridded
  matchup's is `gridded_matchup(on_observing=, on_observed=)`, from "Read in the monthly observational data" (after the model's
  `merge`) to the `ds_obs.run()` after the observations' merge, before they are cut to the surface and regridded. At ≤860px the
  grid is made smaller so every column stays in sight. At ≤640px (`NARROW_GRID`) both extraction columns are left out, so the
  table still fits, and the count is under the Matchup circle (`countUnder`). Note that `str.splitlines()` splits at the markers' `\x1e`. In the app's recipes window cores has no default
  (`show_recipes` sets `form["cores"] = ""`): "Choose the number of cores" must be picked.
- A multiprocessing Manager is made with `matchall._manager()`, when it is needed, never at import: its server is a
  process of its own that only its parent stops, so one made by a window that crashed ran for ever (`_manager`'s server
  exits once its parent is gone). `matchall.session_warnings` is made by `_session_warnings()` before a pool is forked.
- Every question OceanVal asks goes through `oceanval/prompts.py` (`ask`, `interactive`,
  `answered_by`) — never call `input()`/`isatty()` directly, or the window can't answer it. In
  the child process a question is a `QUESTION_MARKER` line on stdout, answered on stdin.
- The window's Info panel (the **i** beside Settings) shows the version as `oceanval.__version__`, which is the
  installed package's metadata and so comes from `setup.py`'s `version=`. Never type the number in the page or app.py.
  Its dataset table (`app.BUILTIN_DATASETS`) is built from the built-in catalogues, not the user's recipes.
- **A choice among boxes must be unmistakable**: the chosen one is filled with the accent colour (or tinted, with a thick
  accent border), bold and a little bigger. Radio rows (`.ptr-mode`, `.demo-detail__option`, `.prefs__use`, `.reg-where label`) and
  the theme cards are styled together at the end of `data/gui.css`; `.segmented` (the domain picker), `.choice.is-chosen` and the
  existing matchups' ticks are in `oceanval_app.html`. A new pick-one control must use or extend these, with tokens only.
- **The "directories down" boxes** (`#f-ndown`, shared by Matchup, Validate and Analyze, and `#sc-ndown_1`..`5` of Compare
  simulations) are `depthBox`es: `withChoices` boxes whose arrow lists 0 to 5 ("0 in the directory itself", "1 directory
  down", ...), and any whole number, 0 or more, can still be typed. A new box asking for a depth must be made with `depthBox`.
  A list wider than its box is moved in from the window's edge, and a blank box has none of its list chosen.
- Both windows inline `data/gui.css` and `data/gui.js` (shared look and helpers, including
  `OceanValGUI.variablesPicker`, the pop-out of the model's variables) via
  `recipes_gui.render_page`. The browser tests (`browser` fixture in `tests/conftest.py`) skip
  where playwright/Chromium is missing, as in CI.

### Network dependency for gridded recipes

Built-in gridded recipes point at remote THREDDS/data servers (NCEI for WOA23, oceancolour.org for
OCCCI, ICDC for NSBC). These are frequently unreachable/slow from development machines. When
testing code that registers recipes, pass `file_check=False` and expect registering the full
recipe set to take significant time even then; prefer `oceanval.parsers.find_recipe` directly
(pure, no network) to validate recipe arguments quickly.

### Documentation surfaces (keep in sync, they drift independently)

There are three places describing behavior, which must be updated together after any
user-facing change to the package:

1. **`docs-site/`** — the marketing/docs site deployed by `.github/workflows/pages.yml` to
   `pmlmodelling.github.io/OceanVal`. Plain static HTML, no build step or templating engine, so
   shared header/nav/footer markup is duplicated across all eleven `.html` files and must be
   edited in each if changed structurally. See `docs-site/README.md` for full details (version
   badge mechanics, the `archive/` snapshot system, page↔`.rst` mapping).
2. **`docs/source/*.rst`** — the Sphinx/Read the Docs source that `docs-site/`'s pages were
   originally redesigned from and roughly mirror 1:1 (e.g. `recipes.rst` ↔ `recipes.html`).
3. **`README.md`** (repo root) — usage examples/feature list.

Don't hand-edit generated pieces: `docs-site/archive/`, the
`<!-- OCEANVAL_VERSION_HISTORY_START/END -->` block in `version-history.html`, and each page's
`#oceanval-docs-version` badge — all are maintained automatically (see `docs-site/README.md` and
`claude_updates.md`, which has the full prompt/checklist for post-change doc sync plus specific
pitfalls to avoid, e.g. `.gitignore`'s `/oceanval_*` rule must stay root-anchored).

## Known environment quirks (re-verify before relying on these)

- `weasyprint` may be present but broken (version/tinycss2 mismatch) in some installs, causing
  `validate(pdf=True)` to warn and silently skip PDF generation rather than error.
- Report-output changes (PDF/Word/HTML) are best verified by actually rendering and visually
  inspecting the artifact (rasterize PDFs, screenshot HTML with a headless browser using a virtual
  time budget for MathJax, etc.) rather than reasoning from code — these bugs tend to be purely
  visual.
