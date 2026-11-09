# Compare simulations

Moved out of `CLAUDE.md` to keep it short. The core rules stay there.

### Compare simulations (`oceanval/sim_compare.py`)

- `compare_simulations(simulations, variables, ...)` does everything `analyze()` does for 2–5 simulations
  and builds one report, simulations side by side. `variables={name: {simulation: model variable}}`; every other
  analysis argument is `analyze()`'s, **the same for every simulation, with one set of years that every simulation
  has** (each simulation is checked by `analysis.locate_rows`, which `analyze()` uses too). It is
  not the validate-side `compare()` (`oceanval_comparison`, the window's "Compare existing validations"): every name of
  this feature is `simcompare`/`sim_compare`/`sc-`, its folder is `oceanval_simulation_comparison` (in
  `data_store.CLEARED_FOLDERS`).
- Each simulation is worked out by `analysis._work_out` into `results/<sim key>/`, with the existing file names and
  CSV schemas, and an `analysis.json` of its own, so `analysis_plots` can still draw one simulation at a time
  (`monthly_maps(limits=)` and `change_maps(also=)` take the scale all the simulations share). `comparison.json` says
  what is where, and each simulation's colour (`sim_compare.COLOURS`, Okabe-Ito, in the order of the simulations).
  **With nothing unified, masked or converted, a simulation's results are exactly `analyze()`'s of it alone**: the
  session fixtures `compared_monthly`/`compared_daily` and `analysed_*` in `tests/conftest.py` are run once a session,
  checked as analyses in `tests/test_sim_compare.py` (`TestTheSimulationsOfAComparison`), which compares one simulation of
  its comparisons with them (`conftest.same_as_analyze`: the same files, each identical). **A check of a simulation's values in
  a comparison with nothing unified, masked or converted is `same_as_analyze`'s**: test one simulation of each kind that way and
  the analyses themselves, and give the other tests only what differs (several simulations, unify, mask, units). The every-option
  runs carry `COMPARE_TRANSECT` too (`simulations.every_option()`), and comparisons asked of more than one test are class or
  session fixtures (`conftest.fvcom_comparison`, shared with `test_sim_plots.py`; it takes seconds, so the two files need not
  share a CI group), and only those two files run them.
- **Raw FVCOM output in Compare** (`compare_simulations`, the window's Compare tile): all simulations FVCOM, on **one mesh**, or none; **a mix is regridded**, below. Where all are FVCOM (`sim_compare.fvcom_problems`, before any work: a different mesh (`fvcom_climatology.mesh_arrays`/`same_mesh`: `lon`, `lat`, `nv`... only, never a data variable), a variable on the nodes in one and the elements in another, `domain_mask` and `unit_conversions` are ValueErrors; `unify` is allowed only as a lon-lat box, below). Each simulation is `_work_out` as `analyze()` does it (`row["cores"]` set, `require_thickness` after `locate_rows` without the FVCOM rows, `check_mesh_area`), so its results are `analyze()`'s exactly (`info["fvcom"]` in each `analysis.json` and in `comparison.json`; `same_grid` is True = same mesh; `unify_text` is `FVCOM_TEXT`). The combined maps of a climatology's annual mean and of the phenology are `mesh_maps.mesh_sim_plot` (`sim_plots._mesh_panels_code`: a mesh panel for each simulation, one shared scale and bar); the rest read CSVs or delegate to `analysis_plots`' mesh branches. Window: `placed` rows carry `fvcom`; the variables step runs the mesh checks (outside the lock); the files step goes to the grids step for FVCOM too (user's rule, 2026-11-04: offer the unify grid for FVCOM): All-FVCOM asks Yes/No; **No keeps the native mesh** (as before), **Yes is a box only** (`sim_fvcom.problems_of`, `sim_compare_forms.check_unify(fvcom=, mixed=)` refuses a baseline), and then the FVCOM output is regridded to that box by `sim_fvcom.regrid_rows` (`fvcom_matchup_files(res=, lon_lim=, lat_lim=)`, nearest neighbour, levels 0-150 m by 5 m if a layer below the surface is asked for, `fvcom_regridded`, thickness `z_level`) and is ordinary output (`compare_simulations`' `regridded`; `unify_text` adds `sim_fvcom.BOX_TEXT`). `analyze_found["fvcom"]` hides the thickness rows only while the output stays on the mesh (`_sc_regridded_sims`). The grids page gets `state.simcompare.fvcom`/`mixed` (`_sc_fvcom_kinds`): FVCOM simulations' extents are `fvcom.fvcom_extent`, their map is drawn on the mesh by the viewer's code (`map_plot(mesh=)`, `_sc_field_step(fvcom=True)` counts times as the run does) and Generate unified grid works: `grid_align.unified(meshes=)` puts each FVCOM field (`fvcom_field`, a point grid of one surface time step) on the baseline's grid cropped to the meshes' extent, or on the box, by nearest neighbour with the cells outside the mesh missing, as the run does; the mask question is hidden. Tests: `TestFVCOM` in `test_sim_compare.py` (`same_as_analyze` of Base run against the `analysed_fvcom_a` session fixture of `conftest.py`, the comparison being `conftest.fvcom_comparison`, which `test_sim_plots.py` draws, options `simulations.FVCOM_COMPARE_OPTIONS`, `fvcom_months(trend=, offset=)`), the `fvcom` case of `test_sim_plots.py`, `TestFVCOM` in `test_sim_compare_app.py`.
- **FVCOM beside output on a grid in Compare** (`oceanval/sim_fvcom.py`; the user's rule, 2026-11-03: Continue on the variables step did nothing for FVCOM + NEMO-ERSEM): some simulations raw FVCOM, some not is allowed (`sim_fvcom.problems_of`: `unify` is a baseline that is not FVCOM or, since 2026-11-04, a box (then regridded to the box, with the levels of the first non-FVCOM simulation; `unify_of` gives it no `crop`), no baseline that is FVCOM, no `domain_mask`, no mesh in metres, no simulation with some variables FVCOM and some not). `compare_simulations` sets `unify = {"baseline": first non-FVCOM simulation, "crop": box}` (`sim_fvcom.unify_of`; `crop` is `sim_unify.padded_box` of the meshes' extent, which `sim_unify.prepare`/`_regrid` crop the baseline to, so the baseline's grid is that of the FVCOM output regridded to it), then `sim_fvcom.regrid_rows` (after the folder is made, before `make_common_grid`) regrids each FVCOM row's files with `fvcom.fvcom_matchup_files(grid=, levels=)` (nearest neighbour onto a cropped surface field of the baseline's, written as `results/fvcom_regridded/<key>_grid.nc`; `fvcom._mesh_mask_curvilinear` for a grid with 2-D lon/lat; **surface only unless some layer other than the surface is asked for, then interpolated to the baseline's own depths, so every simulation has the same levels**) and makes the row ordinary output: `row["fvcom"]` False, `row["fvcom_regridded"]` True, every file list of the row (`files`, `series_files`, each entry's and period's `files`, `times`) swapped to the regridded files, and the simulation's `thickness` `"z_level"` (whatever it was given). `info["fvcom"]` is False and `info["fvcom_regridded"]` names the simulations; `unify_text` adds `sim_fvcom.TEXT`. A new file list on a row must be added to `_needed`/`_swap_files`. The window shows the grids step with **no Yes/No** (`mixed`: `check_unify` takes need as yes), the baseline starting on the first non-FVCOM simulation and listing only those (the thickness is looked for in the non-FVCOM simulations only), and `unit_conversions` work. Tests: `TestFVCOMWithOtherOutput` (fixture `mixed_simulations`), `test_sim_compare_app.py::TestFVCOM`. The variables step's refusal message is `scVars.message`, kept until a box is edited (`refreshScVariables` used to clear it, so a refusal was silent).

- **Transect depths, monthly sections and differences in Compare** (the user's design, 2026-11-06): a transect line takes `depth_increment` and `max_depth`
  (`analysis_transects.DEPTH_KEYS`, `_depth_settings`, `depth_axis`, `cut_levels`; the window's two boxes under a transect's Vertical part, `analysis_forms.TRANSECT_DEPTHS`).
  With an increment a section is on 0, inc, 2*inc... up to the deepest depth with a value (or `max_depth`) instead of `N_DEPTHS` evenly spaced depths; z_level levels are only
  cut at `max_depth`. Every transect figure's script drops missing values (`dropna(subset=["value"])`) as it reads the CSVs. `compare_simulations` sets
  `row["monthly_sections"]`, so `analysis_transects._work_out` also saves `vertical_monthly` (`derive_monthly`: position, depth, month, value; Analyze does not), drawn by
  `sim_plots._monthly_sections_code`. With exactly two simulations `sim_plots.transect_figure(..., difference=True)` draws first minus second for every option, from a CSV
  `<key>_transect_<n>_<option>_difference.csv` written next to the results by `transect_difference`/`on_first_grid`, which **interpolates** the second onto the first's
  depths and positions (never an exact join: without an increment each simulation's 50 depths differ, and an exact join once gave no figure); the template says so when
  there are no places in common. `tests/test_sim_plots.py::TestTransectDifference`.
- **Two simulations get difference maps** (the user's rule, 2026-11-04; `sim_plots.difference_pair`/`has_difference`: exactly two simulations,
  both with the climatology layer, the same months, not raw FVCOM, which keeps today's figures): `annual_maps` then draws, in one row, the first,
  the second and the **first minus the second** (title in bold, `difference_title`), and `monthly_difference_maps` replaces the two
  per-simulation 12-month figures with one: months across (`DIFFERENCE_COLUMNS` = 4, in blocks), a row for each simulation and one for the
  difference in each block, **each row named on the right** (`rotation=270`). The simulations share one colour scale and the differences another
  (`RdBu_r`, `_scale(diverging=True)`), a bar for each under the maps (`_bars_code`). The difference is `ds_first - ds_on_first` in the script
  (`_open_pair_code`, which `_difference_values` also runs for the limits); with `info["same_grid"]` not True the second is first regridded to the
  first's grid (bilinear, `regrid(grid=<first's file>)`), for the difference only (`Drawn.found["difference_regridded"]`, `sim_report.difference_text`
  in the captions of `chunk_simclimatology.pytemplate`). With 3+ simulations the figures are as before. Tests: `tests/test_sim_plots.py` (`daily` and `grids` cases).
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
  `make_common_grid` (before any analysis) from the **second time step** of each simulation's field of that layer (`analysis.second_step_of`: the
  first file's second, else the second file's first, else the first; the user's rule, 2026-10-30, as the first step of their output was empty
  and made every cell missing; the grids step's maps and preview use the same rule through `_sc_field_step`; `prepare` raises if no cell is
  shared), on the common grid,
  **in xarray, written with an explicit `_FillValue`** — CDO arithmetic on NaN-fill-valued files does not propagate missing,
  so `x*0` / `ds_mask > 0` tricks (as the validate-side `compare()` notebooks use) wrongly give 0 where a value is missing and
  turn a real 0 into missing. Vertical transects need the same number of levels in every simulation.
- **Domain mask** (`compare_simulations(domain_mask=path)`, the same file and `oceanval/domain_mask.py` as validate's, used unchanged): checked
  by `domain_mask.check` before any work, kept by `keep` as `results/domain_mask.nc` (`comparison.json`'s `domain_mask`, `domain_mask_text`) and put
  in every row as `row["domain_mask"]` (`{"path", "method": "nn", "variable"}`). It is applied by `domain_mask.mask_gridded` **right after
  `sim_unify.apply`** in `analysis.monthly_means_of`/`daily_climatology_of` and the transects' thickness (`_depths_along`), so every analysis sees
  the same masked cells and every simulation ends on the mask's grid (`same_grid` True; with a mask and no unify the report's Model grids says
  only the mask's sentence). `analysis_transects.unified_steps` takes the points of a line from the mask's grid. A row without it is `analyze()`'s,
  unchanged. In the window it is asked on the **grids step**, under the unify question, with validate's wording and map
  (`domainMaskQuestion("scg", ...)`, `POST /api/domain_mask`, which `App.domain_mask_map` serves on `simcompare_grids` too): its boxes
  (`domain_mask`, `domain_mask_path`) are in `unify_form` (so Back keeps them) but the page keeps its own `scMaskForm`; `simcompare_grids` checks them
  with `app.check_domain_mask` into `sc_domain_mask`, which `sim_compare_forms.arguments(domain_mask=)` writes into the script.
- **Units: the first simulation's are the comparison's.** A variable another simulation gives in other units is converted into them by
  `compare_simulations(unit_conversions={variable: {simulation: {"multiplier", "adder"}}})` (value × multiplier + adder, as validate's
  `obs_multiplier`/`obs_adder`; `sim_compare.check_unit_conversions`: never the first simulation, multiplier > 0, and an explicit 1/0 says units
  written otherwise are the same); without one, different units are refused. After `locate_rows`, a converted row gets `row["convert"]`,
  `row["units_given"]` and the first simulation's `row["units"]`, so `_labelled`, `integral_units` and the report say the first's. **The conversion
  is applied in one place, the end of `analysis._model_data`** (`-mulc`/`-addc`, after the rename/sum, after `as_missing`, which stays in the
  simulation's own units), so every analysis, the unify masks and a water column's integral are of converted values; a new read of model
  values for an analysis must go through `_model_data` too. `comparison.json` keeps `unit_conversions` and each variable's `conversions`
  (`describe_conversion`'s text), which the variable's page and the Methods (`sim_report.conversions_text`, `methods`) say. In the window, view
  **`simcompare_units`** comes after the variables step only when `sim_compare_forms.unit_rows` finds units written otherwise than the first
  simulation's (one side missing counts): validate's units table (`unitsRow` with a spec, `SC_UNITS`; `unit_conversion.suggest(name, this
  simulation's units, the first's)` fills it, red and bold), **a page for each simulation that needs one** (client-side, `scUnits.page`), the
  tick to confirm on each; `POST /api/simcompare_units` (`check_unit_boxes`; every row shown is sent, blank as 1/0) → `sc_conversions` →
  `arguments(unit_conversions=)`. Back keeps the boxes (`sc_unit_boxes`, by `unit_row_signature`).
- The window: the landing page's third tile, views `simcompare_sims` / `_variables` / (`_units`) / `_files` / `_grids` (mixin
  `oceanval/sim_compare_app.py`, boxes in `sim_compare_forms.py`, JS block "Compare simulations" in `oceanval_app.html`). **The grids step
  has maps** (one `.ag-card` for each simulation, its first variable at the surface and **the simulation's second time step** if it has one, the
  first file's second or else the second file's first, `_sc_field_step`, which Generate unified grid uses too, `POST /api/simcompare_panel`;
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

