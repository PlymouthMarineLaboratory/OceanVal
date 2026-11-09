# Recipes of the user's own (.oceanvalrc)

Moved out of `CLAUDE.md` to keep it short. The core rules stay there.

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
  kinds) is an `OceanValGUI.variableBox(multi)` (`listIn`; the note under it in `#o-<kind>-model_variable-pick`); `add_own_data`/`own_future` refuse a name not in
  `available` (`_unknown_simulation_variable`), but only once status is `done`, so a slow or failed read never blocks. `own_data.check_entry`
  itself knows nothing of the simulation. **Observation variable** (gridded) is a single `variableBox` over `POST /api/own_inspect`
  (`App.own_inspect` → `recipe_checks.inspect_gridded("disk")`: a file, a directory or an `.nc` pattern, the first file's variables), asked
  500 ms after `obs_path` stops changing (token-guarded `lookAtObservations`); never for a URL or the THREDDS tick, and its errors are notes,
  not refusals. A new box that offers the simulation's or observations' variables goes through `listIn`/`setPick`.
  **Every search of variables (the variable boxes, the viewer's `#vw-search`, Analyze's Add multiple variables) goes through
  `OceanValGUI.searchMatcher`**: words are all required; a plain word is a substring of the fields joined, a word with `*`/`?` is
  a wildcard matched within one field (never from a name into its long_name), unanchored, all else literal. A new search must use it.
- **A variable is asked for in one box, `OceanValGUI.variableBox(input, {available, longNames, multi, extra})`** (`data/gui.js`; the user's
  rule, 2026-10-30: no "Select from the model's variables" link any more): the box opens the old pop-out (`.vars__popover`, fixed, placed
  under the box: a **+/- button** (the user's rule, 2026-11-09: no tick boxes in any variable selector, the viewer's list included; `.vars__pm`, `.vw-pm` in `gui.css`, a grey + adds, a teal - takes out, a chosen row is `is-picked`, tinted with a teal edge; a + that cannot add is `disabled`, greyed, `pointer-events: none` so the click falls to the row), Variable, long_name, Selected at the top, the counts) on focus, and **the box is its search**: the rows shown match the
  part being typed (`searchMatcher`; with `multi`, the part between the `+`s the caret is in for a sum, the name under the caret in a formula, see `model_expr` above). Names in the box are chosen, so `P1_Chl+` typed
  chooses P1_Chl and shows every row; `+` is just typed (never completed). A click on a row, its + button too, adds it (replacing a half-typed search,
  else `+name` at the end; single: the value, and the pop-out closes); on a chosen row (its - button) or its × it is taken out (single: every other row's + replaces the value). With `keepSearch` (Analyze and
  Compare simulations' rows only; the user's rule, 2026-11-06) a click keeps the search typed before it (`kept`), so the rows it finds can be
  added too (`P1_Chl`, then `P2_Chl`); a typed `+` or any typing starts a new search, and only then does every row show. Its "Selected" list is under the table (`is-picked-below`), not above, so the rows do not move as more are added. The row's own
  click handles every click, the +/- button too (it has no handler of its own; `refresh()` sets its sign, `aria-pressed` and `data-name`; the count reads "N chosen, summed"). Without keepSearch (the recipes
  window, own data) every row is shown after a click and Selected stays above. The pop-out's `mousedown` is prevented so the focus stays in the box; blur and Escape close it. Every change is an `input` event, so
  the caller's input handler takes it. A scroll elsewhere on the page never closes it; an ancestor's scroll moves it with the box. Used by
  the Analyze/Compare variable rows, the Analyze and recipes thickness (single, z_level first), the own data steps and the recipes window's
  rows; a new variable box must use it.
- The own data step can save an entry as a recipe too ("Add this data for use now and in
  future"): view `own_future` (`App.own_future`/`own_future_save`), built from the entry by
  `recipe_forms.from_own_data`/`missing_boxes`/`with_boxes`, assumptions shown in amber.
  `create_recipes.replaced_recipes` leaves a user recipe with the same variable and source as
  an own data entry out of that run's recipes window, so it is not registered twice.
- The window's settings (`oceanval/user_settings.py`: the data store)
  live under `"settings"` in the *global* rc only, beside `"recipes"`; `load()` never raises.
  There are no colour themes (removed on request, as a burden to maintain); a `"theme"` left in an rc is ignored.
  Colours in the pages are tokens (`:root` in `data/gui.css`), never hex.

