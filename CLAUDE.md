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
- **User point data** (`obs_path`) is one csv file or a directory of csv files, whichever `parsers.point_files` finds: a file is used as
  it is (whatever its name, and not filtered by `exclude`), a directory gives its `*.csv`, not those below it. Registration (`add_point_comparison`),
  `matchall.point_match`, `recipe_checks.inspect_point`, `own_data.check_entry` and the window's wording all use it, so a new place that
  reads `obs_path` must too. **Longitudes above 180 in the csv are made `lon - 360` as the files are read** (`read_csv_simyears`, and the range
  `inspect_point` reports), before `lon_lim` and the matching: nctoolkit's `match_points` does not wrap.
- **Gridded time series are matched month by month** (`climatology=False`, observations of more than one time): `gridded.common_times` finds
  the time steps of each side whose (year, month) the other lacks and `ds_obs`/`ds_model` `drop(time=...)` them (listed in `end_messages` by
  `describe_months`; nothing in common is an error), then the two are merged by year and month (`paired`, never `set_year(2000)`). Years with
  only some months are kept, the model's too (`year_options` narrows the model's years only for a climatology). The report makes every annual
  mean from the monthly climatology (`tmean("month")` then `tmean()`, the vertical profiles too), so a year with months missing does not skew
  it; when a month of `matchup_dict.pkl`'s `months` never matched, `gridded_template.ipynb`'s `annual_note` names the months in each annual caption.
  The seasonal and regional sections need only more than one month (`_gridded_seasonal`, the template's `n_times < 2`; they go together, as the
  regional cells use `chunk_seasonal`'s `ds_ts`), and with fewer than 12 their captions say which months (`months_note`, `cor_note`).
- **FVCOM output** is regridded by `fvcom_matchup_files`, to `matchup(fvcom_res=0.05)` degrees for point matchups (gridded ones keep
  half the observations' resolution) and, for vertical ones, the depths `fvcom.matchup_depths(fvcom_min_depth=0, fvcom_max_depth=150, fvcom_dz=5)`
  (`session_info["fvcom_res"]`/`["fvcom_levels"]`, also read by `gridded.py`); the levels are in the cache key of the regridded files. The
  recipes window asks all four when the output is FVCOM (`group-fvcom`, `recipes_gui._check_fvcom`, started at those defaults), and as they
  are in `MATCHUP_SETTINGS` they are written into the script only when changed.
- **The maps of the point pages** (R, `chunk_point.pytemplate`'s three, `point_template.ipynb`, `summary.ipynb`) draw the land first and the
  points above it. A lon or lat range of more than 50 degrees (or no R `mapdata`) keeps `map_data("world")`; a smaller one uses
  `map_data("worldHires")` from `r-mapdata`, limited to the data's extent, without the "Great Lakes" region (the lakes are water; open
  ocean with no land falls back to `world`). The same block is in every cell (`tests/test_report_templates.py` checks that), so change them together.
- **The land of the gridded maps** (nctoolkit `pub_plot`/`panel_plot`) is Natural Earth's, which takes in the lakes and which
  nctoolkit draws over the data, so a model of Lake Erie came out all land. `oceanval/map_land.land_below()` puts the land
  beneath the data and draws Natural Earth's lakes over it as water (no lakes, if cartopy cannot download them). Every
  map call in the templates is followed by `land_below()` (imported in `chunk_start`; `tests/test_report_templates.py` checks),
  and every Analyze/Compare script that draws one writes it out (`map_land.LAND_CODE`) and calls it last (`analysis_plots._code`).
- `oceanval/create_recipes.py` scans model output directories, maps model variables to
  observational variables via netCDF `long_name` matching, and writes out a matchup script with
  live/commented recipe blocks for built-in datasets (WOA23, NSBC, OCCCI, GLODAP, ...).
- `oceanval/pyplots.py` holds the plotting functions (maps, scatter, Taylor diagrams, seasonal
  grids, etc.) used inside the generated notebooks.
- **Earlier matchups**: `matchup()` never removes matchups from `out_dir`. It records the ones each run made or kept
  in `oceanval_matchups/last_matchup.json` (`selection.save_last_run`, from `matchall._Told.made`; removed by
  `forget_last_run` once the matchups are confirmed, so a run that does not finish leaves none). `validate(previous=)`
  is True (all), False (`selection.last_run` only) or a list of earlier ones as `variables` takes them;
  `matchup(overwrite=)` also takes such a list (`selection.picks`). **In the window, where the matchups are saved is asked
  once the matchups are confirmed**: the run starts after the units step from a draft of the script in a folder of the
  app's own (`App._draft_script`; `out_dir` = that folder, `cores=1`), and `matchup` writes nothing in `out_dir` before
  the answer to "Are you happy with these matchups?" (the `oceanval_matchups` folder and `model_bathymetry.nc` are made
  after it). Yes is held while view `output` (after `point_time_res`, if asked; `/api/output` → `output_continue`,
  `check_output`; boxes `output_form`: `out_dir`, `unused`, `existing_confirmed`, `out`, and for Match up only `cores`,
  the missing values and the spatial subset) and then, to validate too, `report_options` ("One last thing...", which
  asks the **cores**, `check_cores`, then the **missing values**, `#v-group-missing`, and its subset boxes are then
  matchup's, not validate's: `_checked_report` pops `lon_lim`/`lat_lim` from the report) are answered: the recipes
  window asks none of these in the app (`#settings-more` hidden). `app.check_matchup_boxes` checks the subset and
  missing values (`recipes_gui.check_limits`/`check_missing`); a NEMO-ERSEM simulation (`state.nemo_ersem`) starts at
  0–0 in `is-ersem` (`assumeMissing`). `App._yes` sends `out_dir`, `overwrite`, `cores`, `lon_lim`/`lat_lim` and
  `as_missing` (`_run_output`) with yes, which `matchup` applies (`_full_out_dir`, `_checked_overwrite`,
  `_checked_cores`, `_checked_limits`, `_checked_missing`; the thickness, read before the question, is worked out again
  by `_thickness_and_depths` from `thickness_selected` when `as_missing` changes). The script is written
  under its chosen name from the output step on (`_keep_in_script(out=)`, `create_recipes`' `write(out=)`). Back:
  `report_options` → `output` → `point_time_res`/the matchups question → (Back from the run) the units step.
  The output step's grid, "What do you want to do with existing matchups?", has a row per "variable/source"
  (`app.matchups_asked_about`, each with `made`: whether this run matches it up, from the question's
  `details["matchups"]`, kept in `output_made`; the probe marks the rows while the view is `output`): per row one of
  two, Use (to start with) and Replace if the run makes it, or Ignore if not (left out of the report); a row the run
  does not make is listed only with a report to follow. Radios with the tick look; a column only for an option some
  row has; a lone radio at the top of each column chooses it for every row that has it. **Only a used matchup is kept
  as it is** (form `unused`; `check_output` gives overwrite = those replaced, of those made, previous = those used,
  all → True, none → False). A confirmation tick under the grid (`existing_confirmed`) is required, as users may not
  notice every row starts on Use; it is reset when the list shown changes. The demo's own folder
  (`check_output(demo_dir=)`, `out_dir_is_demo`), where every model tried keeps its matchups, has no grid:
  overwrite=True, previous=False.
  `previous` is never passed to the interim report
  (`live.NOT_INTERIM`), and `_checked_report` adds the earlier keys to `variables` when some of the run's are unticked.
  Analyze's own rule is about an analysis in the output directory (`oceanval_analysis`, which `analyze()` replaces whole): the options step's
  last page (`#ao-replace_analysis`, shown when the probe's `out_dir_has_analysis`) and `analysis_forms.check_output` (`replace_analysis`, checked by
  `analyze_run`) require a tick to replace it **and** a named directory (an empty `out_dir` box means the cwd and is refused,
  `NAME_THE_DIRECTORY`); the tick is reset when `out_dir` is edited. Compare simulations has the same rule for `oceanval_simulation_comparison`
  (which `compare_simulations()` replaces whole), with the same box and key (`replace_analysis`, its label switched; probe
  `out_dir_has_comparison`): `sim_compare_forms.check_output` is `analysis_forms.check_output(folder=sim_compare.FOLDER)` with its own
  `NAME_THE_DIRECTORY`.
  **Where the output is saved (`out_dir`, the script's `out`) is asked last, for Analyze and Compare**, as page 3 of the options step, not by
  the setup page or the simulations step: `check_setup`/`check_sims` leave it out, and `analyze_run(form=)` takes it and checks it
  (`analysis_forms.check_output`, `sim_compare_forms.check_output`; the boxes are kept in `analyze_setup_form` / `sc_output_form` and shown in
  `state["analyze"]["output"]`). Validate and Matchup ask it once the matchups are confirmed (above).

- **The recipes window is two pages** (`create_recipes_gui.html`, client-side, `showPage`; `page` is sent and kept for Back by
  `app.recipes_choices`/`recipes_restore`, the first page in a window made afresh): **Gridded data selection** (`#page-gridded`, the
  rows with gridded datasets, recipes or own) and **Point data selection** (`#page-point`, the rows with point ones, and a
  last column "Gridded data selected", `chosenCell`). A row on both has a simulation variable box on each (`row.dom.views`).
  No meta strip (the simulation path is not shown). The standalone `create_recipes()` window keeps its other settings
  (subset, missing values, output, file filters, cores, report options) in `#settings-more` under page 2's table.
  **Every ticked dataset needs its From/To** (`optionErrors`, `is-required` red; `outsideOutput` compares any years with the output's: partly outside is an amber warning on the dataset and in the years groups, none in the output is an error that blocks Continue); OceanVal fills the boxes not typed in
  (`dataset.typed`, `fill`/`filledValue`, red and bold `is-oceanval`): point datasets from **What years do you want to
  validate?** (`point_start`/`point_end`, required when a point dataset is ticked) and **Match point observations by**
  (no "Global setting" option; written only when typed, `sentOptions`), climatologies from **What simulation years do you
  want to use for climatologies?** (`clim_start`/`clim_end` → `settings["clim_years"]`), else the years create_recipes was
  given (standalone; the app, demo included, fills none). Matchup's `start`/`end` are worked out by the page
  (`matchupYears`: the span of the ticked datasets' years). WOA23 temperature/salinity (`decadal`) need years inside one
  `_WOA23_PERIODS` decade: filled years that fit none are an error on the dataset (the `#woa-dialog` is gone; the server
  still takes `woa23_start`/`woa23_end` → `settings["woa_years"]`). Each built-in gridded source has an **i**
  (`coverageInfo`, from `create_recipes._COVERAGE` → the catalogue's `coverage`: years covered, a note and a link).

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
- **The own data steps pick variables rather than asking for them typed** (`point_data`/`gridded_data`, `ownField` in `oceanval_app.html`):
  the setup answer starts `App._read_own_simulation`, a thread (`netcdf_lock.LOCK`, `create_recipes._variable_long_names`, keyed by
  `_simulation(arguments)`, dropped by `_own_sim_number`/`_reset_count`) whose answer is `state.own.simulation`
  (`status` reading/done/failed, `read`, and `available`/`long_names` only while an own data view is shown). **Simulation variable** (both
  kinds) gets `OceanValGUI.variablesPicker(multi)` in `#o-<kind>-model_variable-pick`; `add_own_data`/`own_future` refuse a name not in
  `available` (`_unknown_simulation_variable`), but only once status is `done`, so a slow or failed read never blocks. `own_data.check_entry`
  itself knows nothing of the simulation. **Observation variable** (gridded) gets a single-select picker over `POST /api/own_inspect`
  (`App.own_inspect` → `recipe_checks.inspect_gridded("disk")`: a file, a directory or an `.nc` pattern, the first file's variables), asked
  500 ms after `obs_path` stops changing (token-guarded `lookAtObservations`); never for a URL or the THREDDS tick, and its errors are notes,
  not refusals. A new box that offers the simulation's or observations' variables goes through these slots (`setPick`/`setBox`).
  **Every search of variables (the pop-out picker, the viewer's `#vw-search`, Analyze's Add multiple variables) goes through
  `OceanValGUI.searchMatcher`**: words are all required; a plain word is a substring of the fields joined, a word with `*`/`?` is
  a wildcard matched within one field (never from a name into its long_name), unanchored, all else literal. A new search must use it.
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
- **The grid of the variables** (Analyze and Compare simulations' run, `renderAnalysisGrid` in `#progress-grid`, beside validate's
  `renderMatchupGrid`): a row per variable, a column per analysis **some variable has** (none for the others), then Time and Report.
  The run tells it through `prompts.tell` (`analysis._tell`: `analyses` with the rows' kinds and `simulations`, then
  `analysis_variable` and `analysis` working/done, told in `_work_out`, so once per simulation; long-term and seasonal together),
  which `App._matchup_news` hands to `App._analysis_news` (`ANALYSIS_NEWS`) → `state()["run"]["analysis"]`: a cell is `done` once
  every simulation has done it, `partial` with the count between, `failed` if working when the run ended. Marks: hollow ring
  (`todo`, `.matchup-grid__todo`) to do, turning circle, tick, dash for an analysis the variable does not have. The Report column is
  `InterimBook`'s status (`added`/`building_key`/`failed`, by variable key, through `_interim_state`). At ≤640px (`NARROW_GRID`)
  the Report column is left out and the analyses' names are turned on their side (`.matchup-grid__sideways`), so every column fits.
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
- **A model's extent is that of its cells with values, never of its whole coordinate arrays** (`oceanval/extent.py`: `valid_lonlat`
  of a field's first time and top level, `first_slice`/`of_file` for a model file, read through CDO first). NEMO puts the cells of
  the land processors it leaves out at lon -1, lat -1 (36 k of AMM7's 111 k cells), so a whole grid's min/max can be lat -1 to 65.
  The unifier's crop box (`sim_unify.prepare`, `domain_mask.mask_gridded`; on a curvilinear grid CDO keeps the index box of the
  cells inside, which for a global ORCA025 and the whole-grid box was all 1.74 M cells), `gridded._lonlat_bounds` (the obs crop and
  the data store's download box), `matchall._model_box` (ICES), `analysis_plots._plot_grid`/`_panel_size`,
  `analysis_series.estimate_grid`, Compare's "The simulations cover" and `viewer_plots.coastline_scale` use it. FVCOM's extents
  (mesh nodes) are left as they are. A new extent of model output must use it.
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
  thickness, `how`); the years of the output are only placeholders/hints. The one exception (the user's rule,
  2026-10-25): each variable's own tick in the pop-outs (`use`, by its name; phenology only for daily output)
  starts ticked, so a variable is left out by unticking it, and one ticked with no layer is an error on its row.
  With 2+ variables each pop-out's table starts with an **All variables** row (`allVariablesRow`, its own
  `<tbody class="analyze-all">` `#clim-all`/`#phen-all`/`#chg-all`, kept in `analyzeDialog.all`, never sent):
  a box set there is set in every row it applies to (`fanOut`, skipping disabled boxes, by dispatching the
  row's own events), and a row that differs has **Reset to All variables** (`resetLink`/`resetRow`; the tick is
  not reset). Transects have **Copy these transects to every variable** on each variable's section instead. The assumptions are
  the missing-value boxes', until the user edits them: the value `oceanval/missing_probe.py`
  finds (xarray, the surface at the first time step of up to 10 files of the first placed variable,
  the value many cells share in every file; started in a thread when the options step opens by
  `AnalyzeSteps._probe_missing`, `state.analyze.missing_probed`, shown in amber `is-found`), else for a
  NEMO-ERSEM simulation (`recipes_gui.is_likely_nemo_ersem`) 0–0 in the `is-ersem` style.
  `analysis_forms.prefill` (what OceanVal could fill the variables with) is unused for that reason;
  the "Environmental variable" box (was "Your name"; the model column is "Variable name in the simulation") instead has an arrow listing `analysis_forms.presets` (every recipe variable
  with the model variable `create_recipes.voted_mapping` finds, `state.analyze.presets`), through the
  `yearList` machinery (`input.onPick`); choosing one fills the model variable in red/bold unless the user typed it.
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
  `climatology` (surface monthly climatology), `trends` (surface; the **Changes** are a position × year heat map,
  not a number per position: `how` "absolute" = the annual means, "absolute_change" = each minus the **first value of the rolled
  series**, "percentage_change" = that as a percentage of |first|, with an optional `window`: a nctoolkit
  `rolling_mean` of the annual means, default 1 = none, each year being the last of its window), `vertical` (annual-mean section) and
  `vertical_trends` (the same `how`/`window`, drawn as one heat map for each of `DEPTH_PANELS` depths of the section, `panel_depths`:
  `annual_means(vertical=True)`, `derive_changes(depths=)`), **each option with its own `start`/`end` years**. The window shows them as
  **Surface** and **Vertical** parts, each a Climatology and its Changes ("Changes" is the UI's word for
  trends), the ends in one line, the rolling mean right of the changes' years. The trends' annual means are nctoolkit's
  (`annual_means`: complete years only, `tmean("year")`, then `rolling_mean`), not numpy. The monthly means come
  first (`analysis.monthly_means_of`, once per layer over the union of years, the surface or
  `LEVELS`), then nctoolkit's `to_transect` on a copy, then `derive` makes a small CSV per
  option (`<key>_transect_<n>_<option>.csv`: `position`, `depth`, `month` or (the changes) `year`, `value`) that the
  figures (`analysis_plots.transect_map`/`transect_figure`, `chunk_transects`) read. `vertical`
  and `vertical_trends` need `thickness` like the water column: `z_level` uses the levels as depths, otherwise the
  thickness is extracted along the line and each cell put at its depth, then the section put on
  `N_DEPTHS` shared depths. A year with months missing is left out of the changes (a first period with one missing has none), and the points of a
  line need not form a lon-lat grid (`_along` reads both). In the window each variable has its own
  transects (`analysis_forms.default_transects`/`clean_transects`/`check_transects`; the cell
  thickness box is shared with the other pop-outs, `THICKNESS_KINDS`). Needs nctoolkit 1.3.6+ (the
  `oceanval` env's 1.3.0 reports it as validate's transect does).
- Every start/end year box of the Analyze pop-outs (`yearBox`, transects' `optionYears`) goes through `withYears` in `oceanval_app.html`: an arrow at its right end (or Down) opens one shared `year-list` (fixed, so the pop-out body can't clip it) of `first..last` of `placed[].years` (the dates check has no year missing between). A new year box must be made with `yearBox`/`withYears`. The list itself is `OceanValGUI.withChoices` in `data/gui.js` (CSS in `gui.css`), shared with the recipes window's First/Last and the name boxes' presets (`choiceHints`, `onPick`).
- The window's Analyze steps live in `oceanval/analysis_app.py` (`AnalyzeSteps`, a mixin of
  `App`; views `analyze_variables`, `analyze_files`, `analyze_options`, which the page shows as three pages of one server view (`analyzePage` in `oceanval_app.html`: the six analyses with Continue, then cores, missing values and figure code with Continue, then where the output is saved with Run/Save; Back on the second and third is client-only); six pop-outs, the
  time series' two sharing one dialog with the climatology, the regions/area card, the
  stations, the map changes' own, `chg-dialog`, and the transects' own, `trans-dialog`, with a card for each transect), their forms and
  checks in `oceanval/analysis_forms.py`. The files page reuses `model_check.ModelCheck`
  (a row with no `time_res` has its files' own worked out) and asks the time_shift
  question first when `time_stamps.check_rows` finds end-stamped times. The run writes
  `analysis.py` and runs it as `app_child analyze <path>`, with `times_index=` the files' times the check read (`_analyze_keep_times`, a temporary copy; not in a saved script), so `analyze()` does not read them again. **Save Python script to run later** (`#ao-save`, beside Run, enabled as it is; `/api/analyze_save_script` → `analyze_run(save=True)`) writes
  the same script, runs nothing, and shows view `script_saved` (`_script_saved`, shared with Compare simulations, whose saved script has no `times_index`,
  those files being temporary; the page's words by `savedScriptWords`).
  The variables page's buttons (`#av-add-all`, `#av-remove-all`, `#av-add-3d`, `#av-add-2d`) all work in the page from `state.analyze.picker`:
  `addVariables(kind)` adds the variables no row holds yet, 3D being `picker.levels > 1` (nctoolkit's `nlevels`, the rule Analyze's Vertical
  analyses use) and 2D the rest; Remove all leaves one blank row. Tests read the picker with `picker_of(app)`: the page's own `state` is inside a
  closure, so `page.evaluate("() => state")` finds the `<script id="state">` element.

- **Info pop-outs** (`oceanval/analysis_examples.py`): an **i** (`infoButton`/`withInfo` in `oceanval_app.html`, `.info-btn`, beside the label, never inside it) next to
  each option of the Analyze pop-outs opens `#example-dialog`, which says **in words** what the option plots. **No figure is drawn or stored** (the user's rule, 2026-10-26:
  drawn examples were far too slow, even as plain lon/lat maps, so the pop-out is text only; `test_nothing_is_drawn_or_stored` fails if the package gains a picture other than
  its logos). `EXAMPLES[kind][option]` is the one place the wording is (`climatology`, `long_term`, `seasonal` and `map_changes` x `surface`/`bottom`/`water_column`;
  `phenology.peak`; `transects.surface_climatology`/`surface_changes`/`vertical_climatology`/`vertical_changes`); `describe()` sends it in `state.analyze.examples` (only in
  `analyze_options`), and the page shows an i only where `exampleOf(kind, option)` finds an entry, so a new option's i is a new entry and a `withInfo(...)` at its tick (the phenology's
  is beside its title, `#phen-info`).

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
- **Grid unification** (`oceanval/sim_unify.py`): `unify={"lon","lat","res"}` (nctoolkit `to_latlon`) or `{"baseline": name}`,
  either with an optional `"regrid"` (`sim_unify.METHODS`: bil, nn, dis, con; kept in the dict only when given, bilinear if not;
  `sim_unify.METHODS` is the one list, `grid_align` uses it) (`regrid`, the baseline itself untouched;
  **every grid is cropped before it is regridded** to the target's extent a degree wider each way, `PAD`: `sim_unify.padded_box`, None for
  a target round the globe or across the dateline, and `crop_to`, which leaves the dataset as it was if CDO cannot crop it, so cropping only
  ever saves time; the baseline's box is kept by `prepare` in `box_path` JSON; `domain_mask.mask_gridded` crops to the mask's extent too, but
  never FVCOM output, `fvcom=`; a simulation already on the baseline's grid is not regridded, as bilinear
  remapping spreads missing cells). It is applied through `row["unify"]` in `analysis.monthly_means_of`,
  `daily_climatology_of` and the transects' thickness: **after the vertical reduction** (surface/bottom/column mean) of each
  layer, so all analyses see the same cells. A cell missing in any simulation is missing in all: the mask is made in
  `make_common_grid` (before any analysis) from the **first time step** of each simulation's field of that layer, on the common grid,
  **in xarray, written with an explicit `_FillValue`** — CDO arithmetic on NaN-fill-valued files does not propagate missing,
  so `x*0` / `ds_mask > 0` tricks (as the validate-side `compare()` notebooks use) wrongly give 0 where a value is missing and
  turn a real 0 into missing. Vertical transects need the same number of levels in every simulation.
- The window: the landing page's third tile, views `simcompare_sims` / `_variables` / `_files` / `_grids` (mixin
  `oceanval/sim_compare_app.py`, boxes in `sim_compare_forms.py`, JS block "Compare simulations" in `oceanval_app.html`). **The grids step
  has maps** (one `.ag-card` for each simulation, its first variable's first file at the surface and first time, `POST /api/simcompare_panel`;
  the unified grid card; the missing value `missing_probe` finds is used for the map): Yes shows them, then the method radios (box/baseline), the
  **Regridding method** select (`unify_form["regrid"]`, blank = bilinear) and **Generate unified grid** (`POST /api/simcompare_generate`,
  `grid_align.unified(box=)` or baseline), **an optional preview only**: Continue never waits for it, and the run still works the masks out per
  variable and layer. The maps are drawn by `drawMap(id, item)`/`emptyMap(id, text, busy)` (busy: a spinner beside the text, while the unified grid is generated, which says what is being done: **Cropping / Regridding <simulation>…, Unifying the simulations…**, from `sim_unify.prepare(progress=)` through `grid_align.unified(progress=)` to `App.sc_stage` in the states, which `showGenerateStage` writes into the placeholder's text in place); there is no plus (the simulations are given). Then
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

### View simulation output (`oceanval/viewer_*.py`)

- Ported from the standalone ncviewer (2026-10-28): the landing page's **first** tile (`#home-viewer`, `data-action="viewer"`,
  `viewer_app.VIEWER`); the landing page is **two by two** (`.choices.is-four`, one column at ≤640px). Mixin
  `viewer_app.ViewerSteps` of `App` (views `setup` → `viewer_scanning` → `viewer`). **Page 1 is the shared setup view**
  (`action == "viewer"`: `simulationOnly()` in the page hides the domain/years as for Analyze), so the directories-down
  logic, `depthBox`, "Finding files", the recent-directories arrow and the amber `restart` Skip box are OceanVal's own; only
  the multi-row **Missing values** fieldset (`#setup-missing`, `missingRows`, sent as JSON `missing` by `setupForm()`,
  `viewer_app.parse_missing`) is the viewer's. **Its rows start filled in from the files** (`viewer_missing.guess` via
  `/api/viewer/missing`, asked by `askMissingGuess` after each `/api/probe` answer with files; `scan(wait=False)`, then
  `missing_probe.probe` on the first mappable variable of the first file type, else 0 for `is_likely_nemo_ersem` names): amber
  `is-found` / `is-ersem` with Analyze's wording, "Attempting to identify missing value range" and a spinner while it runs. Typing in or
  removing a row (`missingEdited`) makes the rows the user's for good; rows OceanVal filled are emptied when the simulation or
  filters change. A simulation viewed is recorded in `.oceanvalcache` (`record_sim_dir`).
  **Editing the simulation directory searches afresh** (`newSimulationForViewer`): the directories down are blanked for the folders' layout to
  fill again, and the missing-value rows (even typed ones) are emptied and looked for again. Viewer only: Validate/Analyze keep a typed depth.
  **The simulation box also takes the path of one file** (`viewer_catalogue.viewer_paths`, the viewer's `check_setup`, `probe()["file"]`): used as it is, whatever its name, not filtered by exclude/require, with no directories down (the box is disabled; the label says "directory or file path"). Browse… stays a folder picker.
- `viewer_catalogue.scan(wait=False)` reads the variables of each file type's first file; `Catalogue.read_times` then reads
  every file's times **in a process of its own**: `viewer_times.py` is run as a script by path (`python -I`), imports nothing
  of OceanVal (a test checks), and has its own spawn pool (half the cores); `Reading.cancel` kills it, which Back and Restart
  do (`Catalogue.stop_times`). Fewer than `PARALLEL_FROM` files are read in the window's process under `netcdf_lock.LOCK`
  (a plain Lock: nothing in the viewer takes it twice). Times are cached in `~/.oceanvaltimes` / `$OCEANVALTIMES` (JSON,
  read-merge-write, never raises; autouse `oceanvaltimes` fixture), not `.oceanvalcache`.
- Maps: `viewer_plots.map_plot` (hvplot → Bokeh json_item; holoviews/hvplot/bokeh imported only when a map is drawn, a test
  checks) and `viewer_mesh` (FVCOM Mesh/Raster, numpy only, apart from `oceanval/fvcom.py`). GET `/api/viewer/catalogue`,
  `/api/viewer/plot`, `/api/viewer/limits`, `/bokeh.min.js` (the installed BokehJS, loaded by the page only once the viewer
  opens, `ensureBokeh`). **A map keeps its true proportions**: `viewer_plots._render` adds `item["aspect"]`
  (`frame_aspect`: the frame's height/width from the figure's ranges, a degree of longitude being cos(mid latitude) of
  one of latitude; metres as they are; None for grid indices, which fill the box), and the page's `fitFigure` sets the
  figure `fixed` with `frame_width`/`frame_height` as large as fit `#vw-plot-wrap` (what is round the frame measured from
  the laid-out figure; `invalidate_layout`, as a new frame size alone is not laid out), again on resize
  (`ResizeObserver`); box zoom has `match_aspect`. The page's JS block "View simulation output" uses `vw-` ids/classes throughout. A thread setting
  `view` checks `_viewer_number` (bumped by `_viewer_drop`: Back, Restart, quit, a new Continue).
- **Levels with no depths** (a vertical dim with no 1-D variable, as GETM's `z`/`zi`, written bottom first) are named
  "0 (bottom)" … "29 (surface)" when `viewer_catalogue._ends` can tell: from a vertical coordinate of the variable
  (`coordinates`/coords spanning the dim, `positive`/`standard_name`; GETM's `zct_av`, with -1e20 on land) or else from
  the wet cells of its first and last level (z-levels); else bare indices. Only a strided slice of the first time step
  is read. `surface_level` is where the page's level starts. Levels with depths and FVCOM's layers are unchanged.
- Tests: `tests/test_viewer_*.py` (simulations in `tests/viewer_simulations.py`, fixture `viewer_simulation`), CI group 3.

### The `oceanval` command and its browser windows

- `oceanval/app.py` is the `oceanval`/`OceanVal` console script (also `python -m oceanval`; `setup.py` defines
  `OceanVal` only where file names are case sensitive, as on macOS/Windows the two are one file and pip
  failed to upgrade over them): a
  127.0.0.1 server with a token, whose page (`data/oceanval_app.html`) opens on a landing
  page (view `home`, two by two) of View simulation output, Analyze, Compare simulations (see above) and Validate, which leads to the validation page
  (view `start`) for choosing matchup+validate / matchup only / validate, the simulation (live-checked via `/api/probe`),
  the create_recipes window, and a progress panel for the run (its console is shown only if the run fails).
- **Whether a browser is opened** is `recipes_gui._can_open_browser`: `OCEANVAL_BROWSER` 1/0 decides if set; else `BROWSER` (VS Code's
  helper) opens; **over SSH nothing else does**, as `DISPLAY` is then an X11 forward (MobaXterm sets one) and the browser would open on the
  remote machine; else a display, macOS or Windows. `oceanval --no-browser` sets `OCEANVAL_BROWSER=0` in `os.environ`, so `app_child`
  (which reads the same function for the reports it opens) obeys it. When none is opened the link and `remote_hint` (`ssh -L`) are printed.
- **Files are never left out unasked.** No code drops a file for having "restart" in its name: `create_recipes.simulation_paths`
  (Analyze, Compare, the window's live check and the output years), `_has_nc`, `model_check` and `matchall` leave that to `exclude`/`require`.
  The window starts the **Skip file names containing** box with `create_recipes.DEFAULT_EXCLUDE` (`"restart"`; Validate, Analyze and each
  Compare row: `default_setup_form`, `sim_compare_forms.blank_sim`, sent as `state.default_exclude`) in amber (`is-detected`, `isDefaultExclude`
  in the page) with a line saying so, until it is changed. A Compare row holding only that is unused (`sim_compare_forms.row_is_blank`,
  `scRowEmpty`). The Python defaults (`matchup(exclude=[])`, `analyze(exclude=None)`) are unchanged.
- **The live file check shows "Finding files"** (a spinner, `.is-finding`) from the edit of the directory, depth or filter boxes until the
  `/api/probe` answer is in: `probePending` in `askProbe` (the badge `#ndown-found`, Validate/Matchup/Analyze) and `scSims.pending[n]` in
  `scAskProbe` (`#sc-m-n`). The check's number is taken at the edit, so an earlier answer still on its way is dropped, and the last count is not
  shown meanwhile. Any new probed box must go through these.
- **Closing the browser quits the command, but a quiet window never stops a run** (the user's rule, 2026-10-25: Safari froze
  a tab in the background and the Analyze run was killed). `App._watch` drops a page silent for `PAGE_TIMEOUT` (15 s), or
  `HIDDEN_PAGE_TIMEOUT` (30 min) for one that said its tab is hidden (`OceanValGUI.tellVisibility`, a keepalive POST to
  `/api/visibility` on `visibilitychange` → `App.page_visibility`); **a closing tab says it is hidden first** (Chromium), so `tellVisibility` also posts `closing` on `pagehide`
  (not when `persisted`), which `page_visibility` gives only `CLOSING_PAGE_TIMEOUT` (4 s) and which a later "hidden" does not undo
  (without it, closing the browser left the command running for 30 min); with every page gone it quits only when not `_busy()`
  (status `starting`/`running`, which `_run_finished` keeps through a matchup's hand-over to its report run), otherwise it
  prints the window's link to the terminal once and quits when the run is over. `poll()` skips its 150 ms wait while hidden.
- **Masking part of the domain** (`oceanval/domain_mask.py`): `matchup(domain_mask=path)` (in `MATCHUP_SETTINGS`), a netCDF file of
  one variable at one time step (and level), 1 in the cells wanted, 0 or missing elsewhere (`inspect` refuses anything else, before any
  work). Once the matchups are confirmed `keep` writes it cleaned (1 and missing, explicit `_FillValue`) as
  `oceanval_matchups/domain_mask.nc` → `session_info["domain_mask"] = {"path", "method": "nn"}`, applied by
  `domain_mask.mask_gridded` (`ds * mask`, after regridding to the mask with crop to its cells, skipped if already on its grid) so the
  matchups end on the file's grid, and `domain_mask.mask_points` (nearest neighbour always, `REMAP_EXTRAPOLATE=off` so a point beyond the mask's grid is dropped, then merged on lon/lat rounded to 5 decimals). In the window it is asked with the other matchup boxes (`#v-group-domain-mask`, `#row-f-domain-mask`; boxes `domain_mask` "yes"/"no", **No to start**, and `domain_mask_path`),
  checked and drawn as it is typed (`domainMaskQuestion` in the page, `POST /api/domain_mask` → `App.domain_mask_map`), and
  `check_domain_mask` puts the user's own file into `matchup` (so into `_run_output` and the script; None for No).
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
  table still fits, and the count is under the Matchup circle (`countUnder`). **A data server that is down** (`oceanval/server_trouble.py`): `answer_of` finds a 5xx/429 in an error (urllib/requests
  status, or the server's HTML page CDO gives back, e.g. NOAA's `<title>503 Service Unavailable</title>`); `gridded.record_gridded_failure`
  (and matchall around the ICES download) tells the window news `{"kind": "server_trouble", key, title, host, server, status, reason}`
  as the matchup fails. `App._server_trouble_news` keeps it by host in `App.server_trouble` (`state()["run"]["server_trouble"]`; a
  matchup made later is taken off, kept through the report run like the grid), and the page shows it in `#server-trouble`, the top of the
  footer `#bar`, rendered on every state (`renderServerTrouble`, not in `renderBar`'s cache); × hides it until it changes. Note that `str.splitlines()` splits at the markers' `\x1e`. In the app the recipes window does not ask for the cores
  (its box is hidden, `show_recipes` sets `form["cores"] = "1"` for the draft): they are asked, with no default, on the
  report options ("One last thing...") or, for Match up only, on the output step.
- A multiprocessing Manager is made with `matchall._manager()`, when it is needed, never at import: its server is a
  process of its own that only its parent stops, so one made by a window that crashed ran for ever (`_manager`'s server
  exits once its parent is gone). `matchall.session_warnings` is made by `_session_warnings()` before a pool is forked.
- Every question OceanVal asks goes through `oceanval/prompts.py` (`ask`, `interactive`,
  `answered_by`) — never call `input()`/`isatty()` directly, or the window can't answer it. In
  the child process a question is a `QUESTION_MARKER` line on stdout, answered on stdin.
- **Restart** (the circular arrow beside the **i**, `mountChrome(restart=)` in `data/gui.js`, in the recipes window too) asks in a pop-up
  (`#restart-confirm`, Cancel is the default, and it says a run in progress will be stopped), then posts `/api/restart_all` →
  `App.reset`, which goes to `home` from **any** view with everything entered forgotten (forms, own data, recipes/units state, the run,
  the draft script folder, Analyze/Compare state); the page then reloads, so its own objects are fresh. Kept: `settings`, `leftovers`,
  `.oceanvalrc` recipes, `.oceanvalcache`, `version`. A run is stopped, and threads of the steps still going (`_prepare`,
  `_recipes_window`, `_units_step`, `_ask_in_window`, the Analyze/Compare reads) find theirs is over by `App._reset_count` or by their
  generation counters, which `reset` bumps: **a new thread that sets `view` after waiting must check them**. **Start again**
  (the button on `finished`/`script_saved`) is the same: it posts `/api/restart_all` and reloads, so nothing of the run just
  finished is kept (the user's rule, 2026-10-25; there is no separate `restart()`).
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
  **A blank box is filled from the folders' layout** (`create_recipes.folder_layout`, in `/api/probe`'s `layout`: `{pattern, ndown}`
  whatever depth is typed): a year directory with all of `01`..`12` and `.nc` files (`YYYY/MM/**.nc`, 2), the twelve months in the
  directory (`MM/*.nc`, 1), or no subdirectories and `.nc` files (`*.nc`, 0). It is only a few `scandir`s (NFS). The fill is amber
  (`is-detected`, tokens), with the line "Folder structure was identified as <pattern>" (`autoNdown` for setup, `scSims.auto[n]` for
  Compare); typing or picking sets the flag off and the value is the user's for good, and a directory without a layout empties a
  value OceanVal filled. The probe is asked again once it fills, to count the files.
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
   `PlymouthMarineLaboratory.github.io/OceanVal`. Plain static HTML, no build step or templating engine, so
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
