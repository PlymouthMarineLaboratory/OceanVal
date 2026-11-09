# Tools & checks and view simulation output

Moved out of `CLAUDE.md` to keep it short. The core rules stay there.

### The compatibility check (`oceanval/compat_check.py`)

- **There is no page of its own any more** (removed 2026-11-09: the output step's per-pattern checks, `pattern_checks.py`, do the job). Tools & info has five tiles
  (`#view-help`: bank, data store, Preferences, About, previous work; three across, then two centred). `compat_check` is the engine `pattern_checks` runs for each file pattern.
- A grid of six questions per file, each with a turning circle (`gridMark`, the matchup grid's marks plus `warning`; `COMPAT_MARKS` in the page), then a tick,
  a warning or a cross, and a message under it. `compat_check.ROWS`: `cdo` (`cdo sinfo` runs and finds a variable on a grid; a crash is
  `cdo_check`'s wording), `regrid` (the first time step at the surface, to 360x180 by `bil` and `nn`; one failing is a warning, with
  `explain()`'s sentence; **a generic grid says in words it cannot be regridded**), `mean` (`analysis_series.robust_spatial_mean`: no cell
  areas is a warning), `time` (`time_stamps._read`/`_evidence`: dates, resolution, calendar, end-stamped times), `interactive` (the viewer's
  own `viewer_plots.map_plot` of `viewer_catalogue`) and `climatology` (Analyze's `pub_plot` map of the
  surface's mean over the file's first 12 steps, a PNG). Only the sea surface is used (`surface_level` of the catalogue).
- **Everything runs in a child process** (`Compat`, run as `compat_check.main`; killed by `cancel` and by `STEP_TIMEOUT`
  without news): CDO can crash and nctoolkit's pools can hang. Its events are `MARKER` lines on stdout. **The variable is asked first when the file has more than one**
  unless `variable=` is given (status `choose`; the child waits on stdin, and nobody is timed while they choose).
- **FVCOM is told first** (`create_recipes._looks_like_fvcom`): it should be compatible, CDO cannot read it, so `cdo`, `regrid` and `mean` are
  skipped, and so is `climatology` (Analyze does not work with FVCOM output yet); `time` and `interactive` are made.

### View simulation output (`oceanval/viewer_*.py`)

- Ported from the standalone ncviewer (2026-10-28): the landing page's first tile (`#home-viewer`, `data-action="viewer"`,
  `viewer_app.VIEWER`; Tools & checks is last); the landing page's four tiles are 2 x 2 (`.choices.is-four`). Mixin
  `viewer_app.ViewerSteps` of `App` (views `setup` → `viewer_scanning` → `viewer`). **Page 1 is the shared setup view**
  (`action == "viewer"`: `simulationOnly()` in the page hides the domain/years as for Analyze), so the directories-down
  logic, `depthBox`, "Finding files", the recent-directories arrow and the `restart` Skip box are OceanVal's own; only
  the **Missing values** fieldset (`#setup-missing`, `missingRows`; **one From and one optional To only, the user's rule, 2026-10-30: no + or ×, and `fillMissingRows`/`setupForm` keep only the first row**; sent as JSON `missing` by `setupForm()`,
  `viewer_app.parse_missing`, which still takes a list of ranges) is the viewer's. **Its row starts filled in from the files** (`viewer_missing.guess` via
  `/api/viewer/missing`, asked by `askMissingGuess` after each `/api/probe` answer with files; `scan(wait=False)`, then
  `missing_probe.probe` on the first mappable variable of the first file type, else 0 for `is_likely_nemo_ersem` names): amber
  `is-found` / `is-ersem` with Analyze's wording, "Attempting to identify missing value range" and a spinner while it runs. Typing in
  the boxes (`missingEdited`) makes them the user's for good; a value OceanVal filled is emptied when the simulation or
  filters change. A simulation viewed is recorded in `.oceanvalcache` (`record_sim_dir`).
  **Editing the simulation directory searches afresh** (`newSimulationForViewer`): the directories down are blanked for the folders' layout to
  fill again, and the missing-value boxes (even typed ones) are emptied and looked for again. Viewer only: Validate/Analyze keep a typed depth.
  **The simulation box also takes the path of one file** (`viewer_catalogue.viewer_paths`, the viewer's `check_setup`, `probe()["file"]`): used as it is, whatever its name, not filtered by exclude/require, with no directories down (the box is disabled; the label says "directory or file path"). Browse… stays a folder picker.
- `viewer_catalogue.scan(wait=False)` reads the variables of each file type's first file; `Catalogue.read_times` then reads
  every file's times **in a process of its own**: `viewer_times.py` is run as a script by path (`python -I`), imports nothing
  of OceanVal (a test checks), and has its own spawn pool (half the cores); `Reading.cancel` kills it, which Back and Restart
  do (`Catalogue.stop_times`). Fewer than `PARALLEL_FROM` files are read in the window's process under `netcdf_lock.LOCK`
  (a plain Lock: nothing in the viewer takes it twice). Times are cached in `~/.oceanvaltimes` / `$OCEANVALTIMES` (JSON,
  read-merge-write, never raises; autouse `oceanvaltimes` fixture), not `.oceanvalcache`.
- **A simulation's files are found once, and read once** (the user's complaint, 2026-11-05: Continue went back to "Finding the files"). The live
  check (`App.probe`) lists the directories into `App._simulation_listing` (unfiltered, keyed `(path, depth)`, `LISTING_KEPT` 5 s while typing; the
  filters are applied to it by `create_recipes.filter_paths`), and **every step after Continue takes it with `kept=None`**, however old, through
  `App._simulation_files`: `_cdo_run` (so Validate, Analyze, Compare and the viewer), `check_setup(find=)`, `scan(paths=)`, `analysis.streams(paths=)`,
  `simulation_files/_variable_long_names/extract_recipe_variable_mapping(paths=/files=)`. A new step that finds a simulation's files must use it, not
  `simulation_paths`. The missing value search (`viewer_missing.guess(keep=)`) keeps the `Catalogue` it read as `_viewer_scanned = (key, catalogue)`
  and `_viewer_setup` takes it over (`_viewer_taken`, key = simdir, ndown, split exclude, split require) instead of scanning again; `_viewer_drop` closes
  one nobody took. The scanning page therefore says only "Reading the variables of each file type: n of m". `viewer_times.load_cache` stats by threads.
  **`viewer_catalogue._lonlat` must read through `ds.variables[...]`, never `ds[...]`** (a DataArray each time made it quadratic: a 335-variable NEMO
  restart took 36 s, a whole scan 82 s); `tests/test_viewer_catalogue.py::TestAFileOfManyVariables` fails on it.
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
- **Grids a quadmesh cannot draw** (`oceanval/viewer_grid.py`, `viewer_plots.cells_image`/`needs_raster`): an unstructured grid (one horizontal
  dim with 1-D lon/lat on it, `about["cells"]`, which `mappable` accepts: ICON, FESOM) and a curvilinear one with a seam (|Δlon| > 180),
  cells in one place (a north fold) or cells without coordinates are drawn as an image on a regular lon-lat grid: each pixel takes the
  nearest cell (scipy cKDTree on the sphere) within its radius (the farthest row/column neighbour; an unstructured cell's 4th nearest),
  cells piled more than twice at one place (fill coordinates: NEMO's land processors, CESM's at -1, -1) left out, the two of a fold sharing
  the larger radius. Global maps are -180..180 (geoviews refuses a global 0..360 image). Its coastline is `coastline_overlay` (an `hv.Path`
  in the map's axes, as FVCOM's), never hvplot's `coastline=`: once a map past 180° is drawn, geoviews' shared coastline refuses every
  later one. `raster_for` keeps the last `KEPT` rasters. Its tests are not in git (the user's rule, 2026-11-01: they would
  bloat the repo): the gitignored `test_cmip6/` (`python -m pytest test_cmip6` from the repo root, never in CI) tests every one of
  the 33 grids of the CMIP6 models by a small piece of it (`test_cmip6/grids/`, `grids.json` lists each grid's models, made by
  `test_cmip6/make_grid_files.py`) and every model whole from the gitignored `cmip6/{model}/` (the smallest
  `tos_Omon_*_gn_*` file of each non lat-lon model from ESGF, `cmip6/inventory.csv`).
- **Levels with no depths** (a vertical dim with no 1-D variable, as GETM's `z`/`zi`, written bottom first) are named
  "0 (bottom)" … "29 (surface)" when `viewer_catalogue._ends` can tell: from a vertical coordinate of the variable
  (`coordinates`/coords spanning the dim, `positive`/`standard_name`; GETM's `zct_av`, with -1e20 on land) or else from
  the wet cells of its first and last level (z-levels); else bare indices. Only a strided slice of the first time step
  is read. `surface_level` is where the page's level starts. Levels with depths and FVCOM's layers are unchanged.
- Tests: `tests/test_viewer_*.py` (simulations in `tests/viewer_simulations.py`, fixture `viewer_simulation`), spread over the CI groups.

