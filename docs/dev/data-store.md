# The data store

Moved out of `CLAUDE.md` to keep it short. The core rules stay there.

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
- Pieces in `oceanval_data_store.json`: gridded = one server file (COBE2, OISST, CoralTemp: one year) × variable ×
  box (model extent snapped out to whole degrees, whole globe at ≥90% area, union on widening) ×
  levels (`top` only for WOA23 surface); ICES = dataset code × year × box, raw export rows.
  Hooked in at `gridded.py` before `_open_obs` and in `ices.download_point_data`.
  Preferences' **Run a check on the data store** button (`data_store.check`, endpoints
  `/api/data_store_check` and `/data_store_check`) only reports, via `check_reply`: it never
  writes, removes, makes or locks anything (a test fails if it does).
  COBE2's and OISST's THREDDS server (PSL) rate limits (HTTP 429): `data_store.download_instead` swaps in the
  plain file download (`DOWNLOADS`) when it turns requests away or fails, with or without a store.
  CoralTemp (`BY_YEAR`) is not read over THREDDS: `_gridded_by_year` asks `erddap.years` for its years and
  `erddap.fetch` for the box (cut on the server, a request a year); it has no `DOWNLOADS` fallback.

### The data store page and Expand (`oceanval/store_view.py`, `store_expand.py`)

- **View the data store** (Tools & checks' third tile, view `datastore`, `POST /api/data_store_view`, `App.view_data_store`, the user's design, 2026-11-09): every
  built-in dataset (`app.BUILTIN_DATASETS`) in **two tables**, "In the data store" (Environmental variable, Data source, Web link, Year, Water-column, lon-lat extent) and
  "Not in the data store" (the years it covers, grey "Covers ..." beside the years held). `store_view.view` only looks (`data_store.pieces`: read-only, nothing written or
  locked). **The extent is read from the stored files themselves** (`_box_of_file`: netCDF lon/lat, ICES csv columns; longitudes above 180 wrapped), never the index's `box`,
  which is what was asked for (NSBC's file is lon -15..13, the index says -20..13). A file under a netCDF read holds `netcdf_lock.LOCK`. The folder is the one saved in the settings,
  never one the request names.
- **Expand** (any built-in dataset; a button on every row opens the form with that dataset fixed, **Expand multiple variables** at the top picks several with +/- buttons):
  year range (year-based datasets, and the decades of WOA23 temperature/salinity), lon-lat extent (not GLODAP or Hansell, kept whole), water column Yes/No (WOA23 only: `all`/`top`).
  `store_expand.check` validates the form, `store_expand.run` fetches **with the arguments a matchup gives**, through `data_store.gridded(..., store_only=True, on_piece=)` and
  `data_store.ices(..., store_only=True, on_piece=)` (nothing opened or read back; they return counts; a callback that raises never stops the work), with the use mode forced to
  "always" (what is held is not downloaded again), so areas widen, levels replace only as a matchup's do, and `top` never downgrades `all`. It runs in a killable child (`app_child expand <json>`,
  news by `prompts.tell` `{"kind": "expand", ...}`), **in the background and one job at a time** (`App._store_jobs`, `state()["store_expand"]["jobs"]`, routes
  `/api/store_expand`, `_cancel`, `_clear`): a circle turns beside the button while any is queued/working, the form closes on submit so another can be asked for, a working job
  counts in `_busy()` (closing the browser does not kill it), Restart and quit stop every job (`_drop_store_expand`), and what was downloaded stays. Tests: `tests/test_store_expand.py`.

