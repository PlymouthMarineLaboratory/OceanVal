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
- **FVCOM is told first** (`fvcom.is_fvcom`): it should be compatible, CDO cannot read it (OceanVal opens it with nctoolkit's `open_fvcom`), so `cdo`, `regrid` and `mean` are
  skipped; `time`, `interactive` and `climatology` are made, the climatology drawn on the mesh (`compat_check._fvcom_climatology`, `mesh_maps`) from the surface layer.

### View simulation output (`oceanval/viewer_*.py`)

- Ported from the standalone ncviewer (2026-10-28): the landing page's first tile (`#home-viewer`, `data-action="viewer"`,
  `viewer_app.VIEWER`; Tools & checks is last); the landing page's four tiles are 2 x 2 (`.choices.is-four`). Mixin
  `viewer_app.ViewerSteps` of `App`. **Up to four simulations are shown side by side (2026-11-10, the user's redesign).** The tile opens the
  viewer page at once (`App.view == "viewer"` for the whole action; there is no setup page and `viewer_scanning` is no longer a view):
  each simulation is a panel, a `viewer_app.ViewerSlot` kept in `App.viewers {1..VIEWERS_MOST: slot}` (`slot.view`
  is `setup` → `viewer_scanning` → `viewer`; the catalogue, missing values, scan, times, the reading counter `reading`, the missing-value
  guess and the search it kept are the slot's own). `_viewer_current(slot, number)` is how a thread knows it is still wanted (the panel is
  still there and nothing was dropped). **Every viewer endpoint takes `viewer=<panel number>`** (`_viewer_slot_of`: none means panel 1;
  nothing read is "Nothing has been read yet."); `/api/setup` takes it as `form["viewer"]`; `/api/viewer/add` (the lowest number free, up to
  four) and `/api/viewer/remove` (not panel 1) are steps. Opening or setting up one panel never closes another. Back drops every
  slot (`_viewer_drop_all`) and keeps each form in `App.viewer_kept` (the forms the page sends as `forms {number: form}` win, they are the
  boxes not applied) for the next `choose` to start from (`_viewer_enter`: panel 1 and each kept panel, their readings not kept); Restart
  clears both. **The times of the files are read one panel's at a time** (`_viewer_turn`, a Lock waited for in `_viewer_their_turn`
  while still wanted; each reading starts processes of its own) and **maps are drawn one at a time** (`_viewer_drawing` around
  `map_plot`: geoviews keeps state between maps). `state.viewer` is `{panels: [slot.state()], most, coast}`.
  **The panel's box is the shared setup form, moved into a pop-out** (`#vw-setup-pop`, one instance: `openSetup(number)` moves
  `#setup-error` and `#setup-form` into it under the panel asked, `closeSetup` moves them back into `#view-setup`; a draft of what was
  typed and not applied is kept per panel in `panelDrafts`). So the directories-down
  logic, `depthBox`, "Finding files", the recent-directories arrow and the `restart` Skip box are OceanVal's own (`fillSetup` fills from the
  panel's form or draft, `setupForm()` sends `viewer`, `refreshSetup` enables `#vw-apply`, `submitSetup` closes the box on success;
  `simulationOnly()` hides the domain/years as for Analyze); only
  the missing value is the panel's own, set in the **Map options** box (`mv-from`/`mv-to`, the bottom of the box; one From and one optional To, the user's rule, 2026-10-30; `viewer_app.parse_missing` takes the JSON rows, sent by `api/viewer/set_missing`). The setup page has no missing value box (removed 2026-11-11). **The server applies the guess** (`viewer_missing.guess`, asked by the setup page's `askMissingGuess` through `/api/viewer/missing` once files are found, and kept by `_viewer_guess_thread`): `_apply_guess` puts it in the panel when the guess is for the panel's simulation (`ViewerSlot.missing_key`, `_guess_key`) and the user has not typed a value (`missing_source == "user"`), with `missing_source` `found` (amber) or `ersem` (assumed). `state()` gives `missing_looking` for the spinner line "Attempting to identify missing value range". Continue keeps the panel's own value only for the same simulation. Typing in a box drops the colour at once. The map is drawn again when the server changes the value. `simulation_paths`. The missing value search (`viewer_missing.guess(keep=)`) keeps the `Catalogue` it read as `_viewer_scanned = (key, catalogue)`
  and `_viewer_setup` takes it over (`_viewer_taken`, key = simdir, ndown, split exclude, split require) instead of scanning again; `_viewer_drop` closes
  one nobody took. The scanning page therefore says only "Reading the variables of each file type: n of m". `viewer_times.load_cache` stats by threads.
  **`viewer_catalogue._lonlat` must read through `ds.variables[...]`, never `ds[...]`** (a DataArray each time made it quadratic: a 335-variable NEMO
  restart took 36 s, a whole scan 82 s); `tests/test_viewer_catalogue.py::TestAFileOfManyVariables` fails on it.
- Maps: `viewer_plots.map_plot` (hvplot → Bokeh json_item; holoviews/hvplot/bokeh imported only when a map is drawn, a test
  checks) and `viewer_mesh` (FVCOM Mesh/Raster, numpy only, apart from `oceanval/fvcom.py`). GET `/api/viewer/catalogue`,
  `/api/viewer/plot`, `/api/viewer/limits`, `/bokeh.min.js` (the installed BokehJS, loaded by the page only once the viewer
  opens, `ensureBokeh`). **A map keeps its true proportions**: `viewer_plots._render` adds `item["aspect"]`
  (`frame_aspect`: the frame's height/width from the figure's ranges, a degree of longitude being cos(mid latitude) of
  one of latitude; metres as they are; None for grid indices, which fill the box). The axes are hidden (`xaxis=None, yaxis=None` in `map_plot`: no labels, numbers or ticks; the labels stay in the json, `visible` false), and the colour bar has `clabel=""`. The page's `fitFigure` sets the
    figure `fixed` with `frame_width`/`frame_height` as large as fit the panel's plot box (what is round the frame measured from
  the laid-out figure; `invalidate_layout`, as a new frame size alone is not laid out), again on resize
  (a `ResizeObserver` for each panel); box zoom has `match_aspect`. **The page's JS block "View simulation output" is the `ViewerPanel` class,
  one per panel**: the panel's markup is `<template id="vw-panel-tpl">`, its elements found by `this.$("name")` (`data-vw="name"`, so no ids
  but the two figure containers' `vw-<n>-plot`, which Bokeh embeds by id), its variables, time, colours, years, animation, frames and figures
  its own, the arrow keys going to the panel last used (`activePanel`). `renderViewers()` reconciles the panels with `state.viewer.panels`;
  `enterView` disposes them on leaving. The figure helpers `figures`/`aspects`/`release`/`fitFigure`/`fitAndSettle` after the block are the
  shared ones of the other windows' maps (Compare, the domain mask, the report's pattern maps), not the viewer's.
  **Layout** (`.vw-panels[data-count]`): one panel fills the window with the dashed `+` beside it; two fill its height side by side with the
  `+` bar under them; three and four are 2 x 2 and the `+` goes at four. An empty panel's box opens by itself in its middle, for a new panel or an emptied one
  (`.is-empty`, `wantsBox`, set in `ViewerPanel.update` and acted on in `renderViewers`, which `openSetup` serves). It has no close button:
  it closes when a simulation is given, the panel is removed, or Back is used, and the **+** is hidden while any panel is empty. A loaded panel
  has no button to change its simulation (Back, or × and a new panel). **The variable column** is a container-query grid (`@container
  (min-width: 520px)`) with a draggable divider (`.vw-split`: pointer capture, arrow keys, Home/End, double-click resets; 40 px to 55 % of the
  panel, `SIDE_*` constants; the contents hide below `SIDE_USABLE`; the width is kept per panel number in `localStorage`
  `oceanval-vw-side-<n>`). The depth is filled in from the folders' layout by `useProbe`, with no pop-out (removed 2026-11-10). A panel with its box is `z-index: 20`, and the `.picker` dialogs (25) open over it. The box has no outside-click or Escape
  handler now; the drop-downs' lists are on the body.
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
  `test_viewer_app.py` reaches a panel as `app.viewers[n]` (`_view(app, sim, panel=n)`, `_wait_for(app, view, panel=n)`); `test_viewer_browser.py`
  drives the page by `[data-panel="n"] [data-vw="name"]`.

- **Open in another tab (2026-11-10).** A panel's `open-tab` button opens the page's own address with `?mirror=<panel>` (`MIRROR` in
  `oceanval_app.html`): that tab (`body.is-mirror`) shows only that panel, with no Back/Restart/+/×, and the server needs nothing new
  (a slot is already shared by every page). The tabs follow each other over `BroadcastChannel("oceanval-viewer")`:
  `ViewerPanel.broadcast` sends `{panel, pick, playing}` after any input/change/click/keydown in the panel when it differs from the last
  snapshot sent or received (so no echo), leaving `step` out while playing (each tab plays its own frames); `applyMirror` sets `pick`,
  redraws and starts or stops Play, held in `mirrorPending` until the catalogue is loaded. A new tab sends `hello` and the other answers.
  Zoom is not mirrored. When the panel's slot goes (Back/Restart), `#mirror-closed` covers the page.

- `viewer_remove` (`/api/viewer/remove`): panel 1's × empties it (a fresh `ViewerSlot(1)`, its box reopens blank via `ViewerPanel.justEmptied` in `renderViewers`); panels 2–4 are removed. Panel 1's × shows only while it has a simulation.
