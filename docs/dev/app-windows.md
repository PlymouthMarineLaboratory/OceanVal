# The oceanval command and its browser windows

Moved out of `CLAUDE.md` to keep it short. The core rules stay there.

### The `oceanval` command and its browser windows

- `oceanval/app.py` is the `oceanval`/`OceanVal` console script (also `python -m oceanval`; `setup.py` defines
  `OceanVal` only where file names are case sensitive, as on macOS/Windows the two are one file and pip
  failed to upgrade over them): a
  127.0.0.1 server with a token, whose page (`data/oceanval_app.html`) opens on a landing
  page (view `home`, four tiles, 2 x 2: View simulation output, Analyze simulations, Validate, Tools & checks (see above)) - Analyze simulations (action `analyze_menu`, view `analyze_menu`: two tiles with no subtitles, **Analyze one simulation** = `analyze` and **Analyze and compare up to 5 simulations** = `simcompare` (see above); Back from either's first step returns to that menu, and from the menu to `home`) and Validate, which leads to the validation page
  (view `start`) for choosing matchup+validate / matchup only / validate, the simulation (live-checked via `/api/probe`),
  the create_recipes window, and a progress panel for the run (its console is shown only if the run fails).
- **Whether a browser is opened** is `recipes_gui._can_open_browser`: `OCEANVAL_BROWSER` 1/0 decides if set; else `BROWSER` (VS Code's
  helper) opens; **over SSH nothing else does**, as `DISPLAY` is then an X11 forward (MobaXterm sets one) and the browser would open on the
  remote machine; else a display, macOS or Windows. `oceanval --no-browser` sets `OCEANVAL_BROWSER=0` in `os.environ`, so `app_child`
  (which reads the same function for the reports it opens) obeys it. When none is opened the link and `remote_hint` (`ssh -L`) are printed.
- **Files are never left out unasked.** No code drops a file for having "restart" in its name: `create_recipes.simulation_paths`
  (Analyze, Compare, the window's live check and the output years), `_has_nc`, `model_check` and `matchall` leave that to `exclude`/`require`.
  **Every window's Skip file names containing list starts with `restart` and `spinup`** (the user's rule, 2026-11-10, reversing the
  2026-10-30 empty start): Validate, Analyze and View (`default_setup_form`), each Compare row (`sim_compare_forms.blank_sim`), the bank
  pop-out (`#bank-exclude`, when the simulation has no info yet) and the recipes window (`recipes_gui.default_form`). The words are removed
  with the × beside them. The Python API is unchanged: `matchup(exclude=[])` and `analyze(exclude=None)` still skip nothing by default, so
  `"restart"` must not be added to an `exclude` default there. `tests/test_app.py::test_the_files_skipped_start_as_they_should_in_a_browser` checks the windows. A Compare row is unused when all its boxes are empty (`sim_compare_forms.row_is_blank`, `scRowEmpty`).
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
  grid is made smaller so every column stays in sight. **A data server that is down** (`oceanval/server_trouble.py`): `answer_of` finds a 5xx/429 in an error (urllib/requests
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
- **Back is an arrow at the top, left of Restart** (the user's design, 2026-11-05; `#back-open`, `mountChrome(back=)` and `OceanValGUI.setBack` in
  `data/gui.js`; in the recipes window too, where it stands for the footer's `#cancel` while hosted by the app, which hides that button; the
  standalone `create_recipes()` window keeps its Cancel and has no arrow). **No view has a Back button in the bottom bar any more**: `renderBar` makes
  each view's Back with `backTo(handler, props)`, which registers it for the arrow (keeping each handler and what it sends as before), and a view that
  registers none leaves the arrow **greyed out** (a real `disabled`), the first page's too. A new step with a Back must call `backTo`. The bar is hidden
  where Back was its only button. **The header is sticky** (`.masthead`), and `--masthead-h` (kept by `keepMastheadHeight`) is its height: anything else
  that sticks to the top of the page sits below it (`.vw-side`, the recipes table's head). Tests click `#back-open`.
- **Restart** (the circular arrow beside Back, `mountChrome(restart=)` in `data/gui.js`, in the recipes window too) asks in a pop-up
  (`#restart-confirm`: only the sentence "Begin again with a clean slate" and the Cancel and Restart buttons, Cancel the default), then posts `/api/restart_all` →
  `App.reset`, which goes to `home` from **any** view with everything entered forgotten (forms, own data, recipes/units state, the run,
  the draft script folder, Analyze/Compare state); the page then reloads, so its own objects are fresh. Kept: `settings`, `leftovers`,
  `.oceanvalrc` recipes, `.oceanvalcache`, `version`. A run is stopped, and threads of the steps still going (`_prepare`,
  `_recipes_window`, `_units_step`, `_ask_in_window`, the Analyze/Compare reads) find theirs is over by `App._reset_count` or by their
  generation counters, which `reset` bumps: **a new thread that sets `view` after waiting must check them**. **Start again**
  (the button on `finished`/`script_saved`) is the same: it posts `/api/restart_all` and reloads, so nothing of the run just
  finished is kept (the user's rule, 2026-10-25; there is no separate `restart()`).
- **Preferences and About are pages of Tools & checks** (the user's design, 2026-11-05: the gear and the **i** are gone from the header, in the standalone `create_recipes()` window too, whose `/settings`/`/data_store_check` handlers and
  `user_settings`/`datasets` state went with them; `OceanValGUI.mountChrome` is only Back and Restart). They are Tools & checks' fourth and fifth tiles (`#help-preferences`, `#help-about`; Tools & checks has six tiles, **three across and two down**, the user's layout of 2026-11-09: compatibility, bank, data store / Preferences, About, previous work; `#view-help .choices.is-four` is given three columns and larger tiles in `oceanval_app.html`), views `preferences` and `about` (`App.choose`, valid only from `help`; Back → `help`; sections `#view-preferences`, `#view-about`, filled by `fillPreferences`/`fillAbout` in `enterView`). **Preferences**:
  Apply saves (`POST api/settings`, a toast, the page stays) and Back leaves unsaved changes; the data store check is `api/data_store_check`. **There is no demo** (removed on the user's request, 2026-11-06: the Validate page's Try a demo tile, its views, `/api/demo_*`, `check_output(demo_dir=)`, `out_dir_is_demo` and its tests): `user_settings.RETIRED` names `show_demo`, which `load()` ignores and `save()` takes out of the file; never bring a demo or its setting back without asking. **About** shows the version as `oceanval.__version__`, which is the
  installed package's metadata and so comes from `setup.py`'s `version=`. Never type the number in the page or app.py.
  Its dataset table (`app.BUILTIN_DATASETS`) is built from the built-in catalogues, not the user's recipes.
  It ends with a credit (`.info__credit`): the PML logo and "Developed by the Environmental Intelligence group at Plymouth Marine Laboratory" (as the website's footer), then "Released under the GNU GPLv3 license." and a GPLv3 badge. The logo is a `data:` URI that `recipes_gui.render_page` puts in for `__OCEANVAL_PML_LOGO__` in `gui.js` (`OceanValGUI.PML_LOGO`, set as the `<img>`'s `src`).
- **The browser never offers what was typed before** (the user's rule, 2026-10-30): every typed box has `autocomplete="oceanval-none"` (Chrome ignores `off` on boxes that look like address or name fields: the viewer's "first" box offered saved addresses, 2026-11-11; `el()` in `gui.js` defaults it),
  in the markup and, for boxes the pages make later, by the guard at the end of `data/gui.js` (`forget`, a `MutationObserver`
  and `focusin`). Never `chrome-off`, which Chrome ignores. Only the simulation directories are remembered, by OceanVal
  itself (`.oceanvalcache`). `tests/test_no_browser_memory.py` checks both windows.
- **A box that must be given is outlined in red while it is empty** (the user's rule, 2026-10-31; the red outline replaced the visible Required
  tags, eeab79f): the tags (`.own-req`, `.year-required`) stay in the markup, hidden by `gui.css`, so `syncRequired` still finds the boxes they ask
  for. `OceanValGUI.syncRequired` (`gui.js`; on input/change/click, DOM mutations and a 300 ms interval, as a script's `.value =` fires no event)
  adds `is-needed` (a 4px `--red` border, `fieldset.is-needed` an outline) to each such box while it is empty and takes it off once it is filled; it
  also toggles `is-satisfied` on the tag (and its `.own-field`) and `is-filled` on the boxes of the Analyze pop-outs and own-data fields; the page's
  own `hidden`/`is-required` are untouched. Only `syncRequired` writes `is-needed`: a page that adds it itself loses it on the next sync. The outline
  is only a cue and blocks nothing.
  The box is found from the tag (`data-required-for` ids, the label's `for`, the one box in its label/row/field, a legend's fieldset); a tag
  in a table head covers the columns it spans (`data-required-tick`: each ticked row has a layer; `data-required-any`: some row is ticked;
  otherwise every ticked row has the boxes filled). A new Required tag needs no code if it sits in the label/row/legend of its box.
  A box with **`data-check-needed`** needs no tag: it is outlined the same way while empty. The units step sets it on the Multiply by and Add
  boxes of a row OceanVal could not check (`check.status` `unknown`, or `formula`, whose units it only assumes; `unitsRow`), so the user has to look
  at it and type a number (1 and 0 if the units are the same). The confirm tick still gates Continue; the outline does not.
- **A choice among boxes must be unmistakable**: the chosen one is filled with the accent colour (or tinted, with a thick
  accent border), bold and a little bigger. Radio rows (`.ptr-mode`, `.prefs__use`, `.reg-where label`) are
  styled together at the end of `data/gui.css`; `.segmented` (the domain picker), `.choice.is-chosen` and the
  existing matchups' ticks are in `oceanval_app.html`. A new pick-one control must use or extend these, with tokens only.
- **The window's pop-out words** (the user's rule, 2026-11-06): the Analyze pop-outs have a one-sentence intro each (`KIND_INTROS`, the static
  intros of `#phen-dialog`/`#chg-dialog`/`#trans-dialog`), no "Marked Required is what has to be given" legend and no "Tick at least one"
  (the layer columns' tag is just "Required"; Confirm stays disabled until a variable is ticked, with no message). The compatibility check's
  page is one paragraph. "Only file names are checked, not directories." is said nowhere in the window.
- **The file filters** (the user's rules, 2026-11-10): every "Skip file names containing" and "Only include file names containing" pair is a word list, built by `OceanValGUI.wordFilters(host)` from `<div class="word-filters" data-skip="ID" data-only="ID">` (`buildWordFilters()` builds every host at load). The store inputs keep their ids (`#f-exclude`, `#sc-exclude_1..5`, `#review-exclude`, `#bank-exclude`, the recipes window's `#s-exclude`; `#s-require` etc.): a hidden input holding the words separated by spaces, written by the list. Words are typed and added with Enter, and each shows below with an × to remove it. Every window's Skip list starts with `restart` and `spinup` (`app.default_setup_form`, `sim_compare_forms.blank_sim`, `recipes_gui.default_form`); a new filter pair must use `wordFilters`, not a text box. Compare's
  filters are always shown (no "Only use some of the files" expander). **View the files in a random subdirectory** is a button
  (`.btn--sample`) right of the file count in the `.ndown-row` (setup page `#view-files`, bank info `#bank-info-view`), and
  `App.sample_files(exclude=, require=)` lists only the files the filters let through (`filtered_out` when none do). The viewer's
  THREDDS tick (`#row-f-thredds`, `refreshThredds`) is shown only while the simulation box holds a web address. Every Browse… button
  (`[data-browse]`) is light teal (`gui.css`).
- **The "directories down" boxes** (`#f-ndown`, shared by Matchup, Validate and Analyze, and `#sc-ndown_1`..`5` of Compare
  simulations) are `depthBox`es: `withChoices` boxes whose arrow lists 0 to 5 ("0 in the directory itself", "1 directory
  down", ...), and any whole number, 0 or more, can still be typed. A new box asking for a depth must be made with `depthBox`.
  A list wider than its box is moved in from the window's edge, and a blank box has none of its list chosen.
  **A blank box is filled from the folders' layout** (`create_recipes.folder_layout`, in `/api/probe`'s `layout`: `{pattern, ndown}`
  whatever depth is typed): a year directory with all of `01`..`12` and `.nc` files (`YYYY/MM/**.nc`, 2), the twelve months in the
  directory (`MM/*.nc`, 1), or no subdirectories and `.nc` files (`*.nc`, 0). It is only a few `scandir`s (NFS). The fill is bold
  (`is-detected` on a `.is-depth` box: ordinary colours, never amber, the user's rule, 2026-10-31). No pop-out tells it (the pop-out was
  removed, 2026-11-10, on every window: Visualize, Analyze, Validate, Compare simulations and the bank). `autoNdown` (setup) and `scSims.auto[n]`
  (Compare) say whether the value is OceanVal's; typing or picking sets the flag off and the value is the user's for good, and a directory without
  a layout empties a value OceanVal filled. The tests' `browser` fixture answers the history question and the Validate intro on every page
  (`DISMISS_POP_OUTS`) unless the test sets `window.__keepHistoryQuestion` or `window.__keepValidateIntro`. The probe is asked again once it fills, to count the files.
- Both windows inline `data/gui.css` and `data/gui.js` (shared look and helpers, including
  `OceanValGUI.variableBox`, the box that opens the pop-out of the model's variables) via
  `recipes_gui.render_page`. The browser tests (`browser` fixture in `tests/conftest.py`) skip
  where playwright/Chromium is missing, as in CI (`pip install playwright && python -m playwright install chromium` runs them).

