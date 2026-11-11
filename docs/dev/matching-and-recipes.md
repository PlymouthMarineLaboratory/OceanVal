# Matching and recipes

Moved out of `CLAUDE.md` to keep it short. The core rules stay there.

### Matching and recipes

- **nctoolkit is imported through `oceanval/quiet_nc.py`** (`from oceanval.quiet_nc import nc`), which silences its import warnings and output (errors still raise); the report templates do the same. Scripts that must stand alone (`analysis_plots.NC_IMPORT`) wrap the import in `warnings.catch_warnings()` instead. `tests/test_quiet_nc.py` fails on a direct import.

- **A model variable is one name, a sum (`a+b`) or a formula in CDO's `expr` (`a+2*b`), and `oceanval/model_expr.py` is the one
  place it is read** (the user's design, 2026-11-09): never `split("+")` a model variable. `parse` (raises `ExpressionError(message, start,
  end)`) gives `text` (no spaces), `names`, `kind` (`NAME`/`SUM`/`FORMULA`) and `tree`; `names`/`kind`/`normalise`/`tidied` never raise
  (what cannot be parsed is read as names joined by `+`, as before); `problem(text)` is the check; `cdo_operator(target, text)` is the
  quoted `-expr,'t=...;'` nctoolkit's shell needs; `describe` is "Sum of a, b" / "Expression based on a, b". A text with no operator,
  bracket, comma or space is one variable whatever its name. The grammar is CDO 2.4/2.6's, found by probing CDO (precedence loosest
  first: `?:` (nested needs brackets, none unbracketed in a function's arguments, one needs a variable), `&& ||`, all comparisons at one
  level, `+ -`, `* /`, unary `- !`, `^` right-assoc); `FUNCTIONS` are element-wise only, `REFUSED` (statistics over grid/levels, level
  selection, grid/time functions) say why. `evaluate(tree, values)` is it in numpy as CDO works it out (missing in, missing out, but
  `0*missing` is 0; domain errors are missing), for raw FVCOM, which `fvcom_climatology` (run by path) loads **by its path**
  (`_load_model_expr`; `parts` there is a list of names or a formula string, `parts_of(row)` gives `row["formula"]` or the names).
  `tests/test_model_expr.py` checks random formulas against CDO itself and **`OceanValGUI.expression` in `gui.js`, a port of the
  parser** (its tables are `model_expr.tables()`, put in by `render_page` for `/*__OCEANVAL_EXPR_TABLES__*/`), so change both
  together. Where it is applied: point matchups `-expr,'total=...'` (was `sum_all`), gridded sums after the monthly means as before but
  a FORMULA per time step in `gridded.model_dataset(formula=, target=)` (the mean of a product is not the product of the means),
  Analyze/Compare at the end of `analysis._model_data` (`row["formula"]`). The point cache keeps each matchup's normalised model
  variable (an entry of another, or of none, is not used). A formula's units are its first variable's: `units._check` and
  `sim_compare_forms.unit_rows` give status `formula` (`unit_conversion.formula`), and the units steps show the row in amber with a
  Formula tag (`unitsFormula`, `tr.is-formula`); Compare always shows a formula's row, except for raw FVCOM on its mesh (`formulas=False`).
  In a `multi` `variableBox` the search is the name under the caret (`nameAt`; `*`/`?` are the formula's, not wildcards; a sum keeps
  the part between `+`s), a click inserts where the formula waits for a value, a formula's names cannot be taken out by -/× (edit it),
  and `.vars__expr` under the box says "Sum of ..."/"Expression based on ..." (unknown names marked) or marks the wrong part once typing
  pauses (`EXPR_PAUSE`; the message only with `explain`, the own data box: elsewhere the page's row check says it).
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
- **User gridded data** (`obs_path`): a directory is searched recursively for `.nc` files by `parsers.gridded_files` (nctoolkit's `glob` on
  `dir/**/*.nc`; a path with a wildcard is expanded by `glob.glob`, never `nc.glob`, and the matchup gets that list); `recipe_checks._disk`, `own_data.check_entry`, `add_gridded_comparison` and `gridded.py` (`_obs_files`) all use it. The own data
  step's gridded model variable box uses `variableBox(keepSearch)` like Analyze's rows.
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
- **The domain (global or `nwes`) is never asked** (the user's rule, 2026-11-06; the setup page's "What spatial coverage..." question and the form's
  `domain` key are gone). `create_recipes(domain=None)` calls `oceanval/domain_detect.detect`: `nwes` when more than 80% (`SHARE`) of the cells
  with values of one level of one time step of the model lie inside the `FullDomain` ("Full Domain") variable of `data/nwes_subdomains.nc`, else `global`
  (anything unreadable too; it never raises). As `domain_mask` does it, nctoolkit puts the shelf on the model's grid (`nn`, `domain_mask._no_extrapolation`
  so a global model is not stretched over) and multiplies; raw FVCOM is looked up node by node. An explicit `domain=` is still honoured. Tests'
  autouse `domain_global` fixture makes `detect` return `global` (mark a test `real_domain`, or patch `detect`, for another).
- **The ICES point recipes are the "European" datasets** (the user's rule, 2026-11-11): their catalogue region is `European` (tag `EUR`, tooltip
  "European and neighbouring seas"), not "Northwest European Shelf" (NSBC keeps that, tag `NWES`). The domain key stays `nwes`:
  `create_recipes.POINT_DOMAIN_REGIONS` maps it to `European` for the point catalogue (`default_selection`, the script's point sections), and the
  window's `context["regions"]` lists both so both kinds of chip are marked as the domain's. The `ices` recipe key, `source="ICES"` and the
  acknowledgements are unchanged.
- `oceanval/create_recipes.py` scans model output directories, maps model variables to
  observational variables via netCDF `long_name` matching, and writes out a matchup script with
  live/commented recipe blocks for built-in datasets (WOA23, NSBC, OCCCI, GLODAP, Hansell, ...).
  **`doc` and `poc` ignore the rates named after them**: `_candidate_long_names` drops long_names with a process word (loss,
  uptake, production, release, remineralisation, scavenging, grazing, depth, integrated, ...), and `variable_identify.fitting`
  drops those with flux or per-area units. `poc` finds ERSEM's carbon by `pom`/`particulate organic carbon` and refuses a
  `POC` that is the object of a process ("loss to POC", "uptake of POC"; "concentration of POC" is fine), so
  `R4_c+R6_c+R8_c` is found and `Z4_fZIRPc` ("mesozooplankton loss to POC") is not. Both sum several matches unless
  exactly one is a "total". Tested on the COMFORT `ptrc` files' long_names in `tests/test_create_recipes.py`.
- `oceanval/pyplots.py` holds the plotting functions (maps, scatter, Taylor diagrams, seasonal
  grids, etc.) used inside the generated notebooks.
- **Earlier matchups**: `matchup()` never removes matchups from `out_dir`. It records the ones each run made or kept
  in `oceanval_matchups/last_matchup.json` (`selection.save_last_run`, from `matchall._Told.made`; removed by
  `forget_last_run` once the matchups are confirmed, so a run that does not finish leaves none). `validate(previous=)`
  is True (all), False (`selection.last_run` only) or a list of earlier ones as `variables` takes them;
  `matchup(overwrite=)` also takes such a list (`selection.picks`). **A matchup's key is `variable/source` for a gridded one and `variable/source/layer` for a point one** (`selection.key`; layer `surface`/`all`/`bottom`, `selection.LAYERS`), as one point variable and source can be on disk at both layers from different runs: the Validate ticks and the output step's grid have a row for each layer, and `variables`/`previous`/`overwrite` entries of `variable/source` still pick every layer, `variable/source/layer` one (`selection.wanted`/`picks`/`_matches` take the matchup's `layer`; a gridded matchup never matches a 3-part entry). A new place that picks matchups must pass the layer. **In the window, where the matchups are saved is asked
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
  **The script's name starts as the first free one in the directory worked in**: `matchup.py`, else `matchup1.py`,
  `matchup2.py`... (`app.free_name(cwd, stem)`, used by `default_output_form(cwd)`, `analysis_forms.default_setup_form` for
  `analysis.py`, `sim_compare_forms.default_output_form` for `simulation_comparison.py`; made with the form, so Restart sees a script
  an earlier run wrote). "This file already exists, and will be replaced" is shown only for a name the user typed (form `out_typed`,
  set by the box's input event, kept for Back, in `analyze`'s `analyzeOutput.typed`). The old "There are matchups here already"
  line under the directory box is gone, as the grid says it (the folder browser's still says `has_matchups`).
  The output step's grid, "What do you want to do with existing matchups?", has a row per "variable/source" (point: "variable/source/layer")
  (`app.matchups_asked_about`, each with `made`: whether this run matches it up, from the question's
  `details["matchups"]`, kept in `output_made`; the probe marks the rows while the view is `output`): per row one of
  two, Use and Replace if the run makes it, or Ignore if not (left out of the report); **a row the run makes starts on
  Replace and any other on Use** (the user's rule, 2026-10-30; until the user picks something, form `existing_chosen` is False and
  `unused` is not looked at, in `check_output` and the page's `existingChoice`; `chooseExisting` makes `unused` the user's own
  and sets it); a row the run does not make is listed only with a report to follow. Radios with the tick look; a column only for an option some
  row has; a lone radio at the top of each column chooses it for every row that has it. **Only a used matchup is kept
  as it is** (form `unused`; `check_output` gives overwrite = those replaced, of those made, previous = those used,
  all → True, none → False). A confirmation tick under the grid (`existing_confirmed`) is required, as users may not
  notice what every row starts on; it is reset when the list shown changes.
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
  No meta strip (the simulation path is not shown). A row with a dataset ticked on the page (or the user's own data) is tinted `--teal-50` with a teal left edge (`tr.is-selected`, set in `update(row)`). **The Environmental variable column (both pages) and the Info panel's
  Built-in datasets tables show `user_recipes.DISPLAY_NAMES`** ("Nitrate concentration in sea water", row `display`,
  `user_recipes.display_name`; user recipes keep their title); everything else, the report included, keeps `title`/`VARIABLE_LABELS`. The standalone `create_recipes()` window keeps its other settings
  (subset, missing values, output, file filters, cores, report options) in `#settings-more` under page 2's table.
  **Every ticked dataset needs its From/To** (`optionErrors`, `is-required` red; `outsideOutput` compares any years with the output's: partly outside is an amber warning on the dataset and in the years groups, none in the output is an error that blocks Continue); OceanVal fills the boxes not typed in
  (`dataset.typed`, `fill`/`filledValue`, red and bold `is-oceanval`): point datasets from **What years do you want to
  validate?** (`point_start`/`point_end`, required when a point dataset is ticked) and **Match point observations by**
  (no "Global setting" option; written only when typed, `sentOptions`), climatologies from **What simulation years do you
  want to use for climatologies?** (`clim_start`/`clim_end` → `settings["clim_years"]`), else the years create_recipes was
  given (standalone; the app fills none). Matchup's `start`/`end` are worked out by the page
  (`matchupYears`: the span of the ticked datasets' years). WOA23 temperature/salinity (`decadal`) need years inside one
  `_WOA23_PERIODS` decade: filled years that fit none are an error on the dataset (the `#woa-dialog` is gone; the server
  still takes `woa23_start`/`woa23_end` → `settings["woa_years"]`). Each built-in gridded source has an **i**
  (`coverageInfo`, from `create_recipes._COVERAGE` → the catalogue's `coverage`: years covered, a note and a link).

