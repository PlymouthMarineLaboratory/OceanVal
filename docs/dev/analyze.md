# Analyze

Moved out of `CLAUDE.md` to keep it short. The core rules stay there.

### Analyze (`oceanval/analysis.py`)

- `analyze()` works on model output alone: per variable (name → model variable, `+` sums),
  a monthly + annual climatology (surface = `topvalue`, bottom = nctoolkit's
  `bottom(choice="value")` i.e. the deepest non-missing value, water column = `vertical_mean` or
  `vertical_integration` with the thickness, at its first time, or `fixed=True` for
  `"z_level"`; `water_column` is `"mean"`, `"integral"` or both as a list, each a layer of its own (`analysis._layers`);
  the window asks them as two columns, **Vertical mean** and **Vertical integration**, either or both (the user's rule,
  2026-11-06; row boxes `mean`/`integral`, `analysis_forms._water_column`, page `WATER_WAYS`/`waterTicks`/`waterCells`;
  no "how" drop-down any more); maps of the annual mean and of each month, but no domain-mean monthly line, which
  the seasonal cycles' mean seasonal cycle already gives) and/or phenology (peak day of the surface daily climatology, daily output
  only; opening the tile when no variable has daily output shows the `#no-daily` pop-out, `openNoDaily`, with a Close button back to the options
  page, instead of the phenology pop-out). Each month of each year is averaged first. Everything goes in
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
  `InterimBook`'s status (`added`/`building_key`/`failed`, by variable key, through `_interim_state`).
  **Everything the run does is a cell, none is a line of text under the title** (2026-11-13): the grid is told (`tell_analyses(rows,
  stages=)`) as soon as the rows are checked, before the files are found, and the stages before the analyses are columns
  (`analysis.STAGES`: Files, once per simulation, and Common grid, per variable, for Compare simulations with a unified grid, FVCOM
  regridding included) told by `analysis_stage` news (`tell_stage`, no row is every variable). `runStage` shows no `OceanVal:` line once
  the grid is shown; the console is only for a failed run.
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
  **A NaN missing value breaks CDO 2.4.1's `topvalue`, `bottomvalue` and `vertmean`** (all-missing, all-missing, wrong values; `vertmin`/`vertmax`/`vertsum`
  and CDO 2.6 are fine): files xarray wrote have one. Ask for `surface.finite_missing(ds)` (`setmissval,1e20`)
  just before each of those operators (`analysis._pick_level`/`_reduce_column`, `matchall`, `gridded`, `surface`'s probe) - a new `topvalue`/`bottom`/
  `vertical_mean` must do so too (nctoolkit's `open_fvcom` writes a finite fill, 1e20). `TestTheBottomOfANaNFilledFile` checks it.
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
- **Raw FVCOM output in Analyze** (the user's design, 2026-11-13: nctoolkit 1.4.0's `open_fvcom`): `analysis.streams` marks a stream
  `"fvcom"` (`fvcom.is_fvcom`; `_variables_of` → `fvcom.variables`, from the header: the variables on `node`/`nele`, not
  `viewer_mesh.MESH_VARIABLES`), `locate_rows` sets `row["fvcom"]`, reads its times with `index_times(fvcom=True)`, and refuses parts on the
  nodes and the elements together (`fvcom.location`; open_fvcom opens one at a time), a mesh in metres (`fvcom.has_lon_lat`: open_fvcom needs
  longitudes and latitudes), and the water column or a vertical transect of a part not on `siglay` (`fvcom.not_layered`). Everything else is
  **the ordinary chain**: `_model_data` opens the files with `fvcom.open_model(row["parts"], thickness=)` - only the parts, and the layer
  thickness (`fvcom.THICKNESS`) only for the water column and a transect's section (`layer == "levels"`; then a sum or formula is `aexpr` +
  `selname`, and a unit conversion an `aexpr` of the key alone) - and `_reduce_column` with no thickness uses nctoolkit's FVCOM-aware
  `vertical_mean`/`vertical_integration`, so the water column is of **the monthly means** of the values and of the thickness (as for any
  output; the old code took each time step's column). **The thickness is never asked for** (`require_thickness` without the FVCOM rows;
  `check_climatology(fvcom=)` in the window). Results computed on open_fvcom's grid (each node or element a cell with bounds; the file's
  `nctoolkit_fvcom` and `fvcom_source` attributes, which CDO keeps) are rewritten **on the mesh** by `fvcom.on_mesh` (`analysis._on_mesh`,
  after `_save` of the climatology, the phenology's daily climatology and each map-change period): the variable on `(time, node|nele)`,
  `month`, the mesh of the source file (`fvcom.MESH`: `lon`, `lat`, `nv`, ...), `fvcom.AREA` (each cell's area, from CDO's `gridarea` of the
  bounds) and `attrs["mesh"]`; the layer has `"mesh"` and `info["fvcom"]`. **`oceanval/mesh_maps.py`** draws them (pyfvcom2's tripcolor:
  gouraud on nodes, flat on elements, land beneath, true proportions), from `analysis_plots.annual_map`/`monthly_maps`, the peak maps and
  `change_maps`; `analysis.peak` keeps the mesh; `analysis_plots.domain_mean` of a mesh file is `fvcom.domain_mean` (weighted by `AREA`).
  **The series** are `analysis_series.region_values`/`station_values` as for any output: `spatial_mean` weighted by CDO's cell areas (the
  median-dual control volume of a node, FVCOM's `art1`; an element's triangle), regions by regridding (bilinear, on the mesh) onto the
  region file, and stations the nearest cell with values (`_grid` keeps an unstructured grid's 1-D cells as one row, and `_spacing` is the
  median nearest-neighbour distance of a sample, `SPACING_SAMPLE`). **Transects**: `to_transect` interpolates on the mesh (nctoolkit's
  bilinear weights; on the elements, the area-weighted means at the nodes, so an element's value no longer fills its triangle), the steps
  from the monthly means' file (`steps_for` on open_fvcom's grid), and a section's depths from the thickness extracted with the values
  (`_fvcom_depths`: `cell_depths` of its mean over the months; `saved["depths"] == "sigma"`). The window: `state.analyze.fvcom`/`fvcom_note`,
  the thickness rows hidden and `#clim-fvcom-thickness`/`#chg-fvcom-thickness`/`#trans-fvcom-thickness` saying how it is worked out
  (`refreshThickness`), the dates check with `fvcom: True`; `missing_probe` reads FVCOM's first sigma layer. `compare_simulations` works
  with it too (see compare-simulations.md). Tests: `tests/test_fvcom_analysis.py` (`viewer_simulations.fvcom_months`; values worked out by
  hand, uneven `siglev` and a changing `zeta`, the areas, regions, stations, a peak day per node and transects included; `month_fields` runs
  `monthly_means_of` on one file), the `fvcom` case of `test_analysis_plots.py` (every FVCOM figure).
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
  **HDF5 prints its error stack per thread**, so a thread of the window that makes a file (nctoolkit's `from_xarray`, the unified grid's mask)
  printed `HDF5-DIAG ... unable to open file ... No such file or directory` for a file only probed (the Compare grids step, FVCOM + NEMO).
  `netcdf_lock.silence_hdf5()` calls `H5Eset_auto2(H5E_DEFAULT, NULL, NULL)` on the libhdf5 netCDF4 loaded (found in `/proc/self/maps`:
  **h5py is a pip wheel with its own libhdf5, so `h5py._errors.silence_errors()` changes the wrong library**); `recipes_gui._Server` calls
  `netcdf_lock.silence_new_threads()` so every thread of a window does it first, and `grid_align.unified` calls it too. Errors still raise.
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
  thickness, `how`); the years of the output are only placeholders/hints. That includes each variable's own tick in
  the pop-outs (`use`, by its name), which starts unticked (the user's rule, 2026-10-31, reversing one of 2026-10-25), so
  the user ticks the variables to analyse; ticking a layer ticks it, and one ticked with no layer is an error on its row.
  With 2+ variables each pop-out's table starts with an **All variables** row (`allVariablesRow`, its own
  `<tbody class="analyze-all">` `#clim-all`/`#phen-all`/`#chg-all`, kept in `analyzeDialog.all`, never sent):
  a box set there is set in every row it applies to (`fanOut`, skipping disabled boxes, by dispatching the
  row's own events), and a row that differs has **Reset to All variables** (`resetLink`/`resetRow`; the tick is
  not reset). Transects have **Copy these transects to every variable** on each variable's section instead. The assumptions are
  the missing-value boxes', until the user edits them: the value `oceanval/missing_probe.py`
  finds (xarray, the surface at the first time step of up to 10 files of the first placed variable,
  the value many cells share in every file; started in a thread when the options step opens by
  `AnalyzeSteps._probe_missing`, `state.analyze.missing_probed`, shown in amber `is-found`), else for a
  NEMO-ERSEM simulation (`recipes_gui.is_likely_nemo_ersem`) 0–0 in the ordinary style (no colour, no note).
  `analysis_forms.prefill` (what OceanVal could fill the variables with) is unused for that reason;
  the "Environmental variable" box (was "Your name"; the model column is "Variable name in the simulation", with the hint "Enter variable names or search
  terms that will look up the variable and name and descriptions in files" under its heading) has no arrow (the user's rule, 2026-11-06: the drop-down was
  confusing): `OceanValGUI.nameBox` lists, under the box as it is typed in (and on focus), the recipe variables (hint under its heading: "Enter your own or select from available presets"; `analysis_forms.presets`, `state.analyze.presets`,
  `sim["presets"]` for Compare) whose name or model variable the search matches, each with the model variable it resolves to (`create_recipes.voted_mapping`);
  a name the model holds no variable for is left out of the list (filtered in the page; Compare keeps a name at least one simulation holds). A click or Enter on a row sets the name (as typed, an input event) and calls `onPick`, which fills the model variable in
  red/bold unless the user typed it.
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
- Every start/end year box of the Analyze pop-outs (`yearBox`, transects' `optionYears`) goes through `withYears` in `oceanval_app.html`: an arrow at its right end (or Down) opens one shared `year-list` (fixed, so the pop-out body can't clip it) of `first..last` of `placed[].years` (the dates check has no year missing between). A new year box must be made with `yearBox`/`withYears`. The list itself is `OceanValGUI.withChoices` in `data/gui.js` (CSS in `gui.css`), shared with the recipes window's First/Last and the depth boxes (`choiceHints`). The environmental variable names are not in it: they are `OceanValGUI.nameBox`, a search list (above).
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
  The variables page's buttons (each row's right-hand cell is one rounded box, `.analyze-pill` (`variablePill`, shared with Compare's `scVariableRow`), holding a **+** on the left, which adds a blank row directly below that row, and a **×** that removes it (the user's rule, 2026-10-31: there is no + under the table any more; any row can be removed, and removing the last leaves a blank row whose × is disabled, light grey, as does a lone blank row; `refresh*Variables` sets it); Add multiple variables, `#av-remove-all`) work in the page from `state.analyze.picker`:
  Add multiple variables (`addVariableRows`) adds the variables no row holds yet, tagging each 3D (`picker.levels > 1`, nctoolkit's `nlevels`, the rule
  Analyze's Vertical analyses use) or 2D; Remove all leaves one blank row. Tests read the picker with `picker_of(app)`: the page's own `state` is inside a
  closure, so `page.evaluate("() => state")` finds the `<script id="state">` element.

- **Info pop-outs** (`oceanval/analysis_examples.py`): an **i** (`infoButton`/`withInfo` in `oceanval_app.html`, `.info-btn`, beside the label, never inside it) next to
  each option of the Analyze pop-outs opens `#example-dialog`, which says **in words** what the option plots. **No figure is drawn or stored** (the user's rule, 2026-10-26:
  drawn examples were far too slow, even as plain lon/lat maps, so the pop-out is text only; `test_nothing_is_drawn_or_stored` fails if the package gains a picture other than
  its logos). `EXAMPLES[kind][option]` is the one place the wording is (`climatology`, `long_term`, `seasonal` and `map_changes` x `surface`/`bottom`/`mean`/`integral`;
  `phenology.peak`; `transects.surface_climatology`/`surface_changes`/`vertical_climatology`/`vertical_changes`); `describe()` sends it in `state.analyze.examples` (only in
  `analyze_options`), and the page shows an i only where `exampleOf(kind, option)` finds an entry, so a new option's i is a new entry and a `withInfo(...)` at its tick (the phenology's
  is beside its title, `#phen-info`).

