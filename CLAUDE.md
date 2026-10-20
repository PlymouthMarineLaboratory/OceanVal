# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OceanVal is a Python package that automates ocean model validation: it matches model output
against observational datasets (point and gridded), computes skill metrics, and generates a
Jupyter Book–based HTML validation report (with optional PDF/Word export). Core public API lives
in `oceanval/__init__.py`: `matchup()` (model↔obs matching), `validate()` (build the report),
`rebuild()`, `compare()` (multi-simulation comparison report), `analyze()` (a model-only
climatology/phenology/time-series report, no observations), `create_recipes()` (scaffold a
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
jupyter-book, pandoc, texlive, then `pip install .` and runs the tests as six jobs side by side (matrix `group`: five named sets of test files balanced by run time, and a sixth that runs every file not named, so a new test file is still run); each job has its own checkout, which the cwd-relative `oceanval_matchups` folders need, and prints its 30 slowest tests (`--durations`). A new slow test file should be added to a group in the workflow.

## Architecture

### Report pipeline

`validate()` and `compare()` don't render HTML directly — they build a **Jupyter Book**:

1. Notebook/chunk templates live in `oceanval/data/` (`*.ipynb`, `chunk_*.pytemplate`,
   `gridded_template.ipynb`, `point_template.ipynb`, `_config.yml`, `_toc.yml`).
2. `oceanval/chunkers.py` (`add_chunks`) and code in `__init__.py` assemble per-variable notebooks
   by string-substituting `chunk_*` placeholders (e.g. `chunk_point_surface`, `chunk_seasonal`)
   depending on what data/options are present, then use `jupytext` to sync `.py:percent` ⇄ `.ipynb`.
3. `_build_book()` in `__init__.py` shells out to `jupyter-book build` (behavior branches on
   jupyter-book major version) to produce the HTML report; `_render_offline_report_pdfs`/
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
  setup page lists them (`state()["setup"]["recent"]`, those that still exist) in a list opened by an arrow inside the `#f-simdir` box.
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
  the seasonal time series' mean seasonal cycle already gives) and/or phenology (peak day of the surface daily climatology, daily output
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
- Long-term (annual) and seasonal (monthly) time series are `oceanval/analysis_series.py`: the
  monthly means per layer (`analysis.monthly_means_of`, which `climatology_of` also uses), then
  domain/region/station means saved as small CSVs (year/month integers, so 360-day calendars
  work); annual = mean of a year's monthly means, a year with months missing is flagged, not
  dropped. **All spatial means are nctoolkit `spatial_mean`** (`analysis_series.robust_spatial_mean`: on a copy, warnings caught; if nctoolkit warns the grid has no cell bounds it is redone after `to_latlon` by `nn` and by `bil`, grid resolution/extent estimated by `estimate_grid`, bil used, the report notes it), regions are the
  `<nwes|global>_subdomains.nc` or the user's file, already lon-lat grids, so `region_values` regrids the raw model
  (bil) to the file's grid once, and each region (`as_missing(0)` mask, `multiply`) is a `spatial_mean` there (the mask is
  never regridded to the model, and no `to_latlon` fallback of its own; the domain mean keeps `robust_spatial_mean`);
  the domain series is `analysis_series.DOMAIN` = "Full Domain" (the whole model grid; a file region of that name is skipped in `region_values`), and the seasonal figures (`analysis_plots.seasonal_series_plot`/`seasonal_cycle_regions`, `_layout(domain_panel=True)`) give it a panel of its own beyond `MANY_REGIONS` regions, where the long-term figure keeps it as a grey line in each panel;
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
- Every start/end year box of the Analyze pop-outs (`yearBox`, transects' `optionYears`) goes through `withYears` in `oceanval_app.html`: an arrow at its right end (or Down) opens one shared `year-list` (fixed, so the pop-out body can't clip it) of `first..last` of `placed[].years` (the dates check has no year missing between). A new year box must be made with `yearBox`/`withYears`.
- The window's Analyze steps live in `oceanval/analysis_app.py` (`AnalyzeSteps`, a mixin of
  `App`; views `analyze_variables`, `analyze_files`, `analyze_options`; six pop-outs, the
  time series' two sharing one dialog with the climatology, the regions/area card, the
  stations, the map changes' own, `chg-dialog`, and the transects' own, `trans-dialog`, with a card for each transect), their forms and
  checks in `oceanval/analysis_forms.py`. The files page reuses `model_check.ModelCheck`
  (a row with no `time_res` has its files' own worked out) and asks the time_shift
  question first when `time_stamps.check_rows` finds end-stamped times. The run writes
  `analysis.py` and runs it as `app_child analyze <path>`.

### The `oceanval` command and its browser windows

- `oceanval/app.py` is the `oceanval`/`OceanVal` console script (also `python -m oceanval`): a
  127.0.0.1 server with a token, whose page (`data/oceanval_app.html`) opens on a landing
  page (view `home`) of Analyze (see above) and Validate, which leads to the validation page
  (view `start`) for choosing matchup+validate / matchup only / validate, the simulation (live-checked via `/api/probe`),
  the create_recipes window, and a progress panel for the run (its console is shown only if the run fails).
- create_recipes runs in a thread of the app; `recipes_gui.hosted_by(app)` makes its window
  one of the app's pages (`/recipes/`) instead of a server of its own. Matchup/validate run in a
  separate process (`oceanval/app_child.py`) whose stdout is the console.
- Every question OceanVal asks goes through `oceanval/prompts.py` (`ask`, `interactive`,
  `answered_by`) — never call `input()`/`isatty()` directly, or the window can't answer it. In
  the child process a question is a `QUESTION_MARKER` line on stdout, answered on stdin.
- The window's Info panel (the **i** beside Settings) shows the version as `oceanval.__version__`, which is the
  installed package's metadata and so comes from `setup.py`'s `version=`. Never type the number in the page or app.py.
  Its dataset table (`app.BUILTIN_DATASETS`) is built from the built-in catalogues, not the user's recipes.
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
