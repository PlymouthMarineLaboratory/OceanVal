# What OceanVal remembers (.oceanvalcache)

Moved out of `CLAUDE.md` to keep it short. The core rules stay there.

### What OceanVal remembers (`.oceanvalcache`)

- `oceanval/user_cache.py` keeps a JSON `~/.oceanvalcache` (or `$OCEANVALCACHE`, which the
  autouse `oceanvalcache` fixture in `tests/conftest.py` points at a temp file) of the simulation
  directories matched up before. `matchup()` records its `sim_dir` as soon as the user confirms the matchups ("Are you happy…" → y); the window's
  setup page lists them (`state()["setup"]["recent"]`, those that still exist) in a list opened by an arrow inside the `#f-simdir` box (Validate and Analyze share it; Compare simulations' `#sc-dir_N` boxes have `makeRecent`).
  Each entry has an x (`recentEntry`/`forgetRecent` in `oceanval_app.html`, empty button with the × drawn by CSS so an entry's text stays its path)
  that posts `/api/forget_sim_dir` → `App.forget_sim_dir` → `user_cache.forget_sim_dir`; Delete does the same for the marked entry.
- `user_cache.has_seen(name)`/`record_seen(name)` keep a `"seen"` map of first-time messages. The first time Validate is opened (`state.validate_intro`,
  `#validate-intro`, a pop-out with a Close button, `POST /api/validate_intro_seen`) the window says OceanVal downloads observational data and that the
  Preferences page (Tools & checks → Preferences; the text names the path, there is no gear any more) sets the data store folder to keep it; it is recorded on closing, and not shown if a data store is set.
  An unreadable cache counts as seen. The tests' `browser` fixture closes it unless `window.__keepValidateIntro`.
- **The simulation bank** (`user_cache.bank`/`add_to_bank`/`remove_from_bank`; the user's design, 2026-11-05): a `"bank"` list of `{name, path, added}` in
  the same file, a Name→Directory map the user fills on purpose (not recorded automatically, unlike `sim_dirs`). `add_to_bank` refuses, with the
  reason, a name (casefolded) already there (for the same directory: "Already in the bank."; for another) and a directory already there under another
  name: nothing is ever replaced. Paths are made absolute (a web address is kept as it is). The window: `state()["bank"]` (`App._bank_state`, with `exists`),
  `POST /api/bank_add` (relative to `App.cwd`) and `/api/bank_remove`; view `bank` (Tools & checks' second tile `#help-bank`, beside the compatibility check; the landing page has four tiles, 2 x 2,
  Back → `help`; `#view-bank` lists each and a × removes it, never touching files). **Add to bank + the Name drop-down are `makeBankList(nameId, dirId, buttonId)`**
  (every Compare row, `sc-name_N`/`sc-dir_N`/`sc-bank_N`, the button left of the boxes; and Analyze's setup page, `f-name`/`f-simdir`/`f-bank`, a Name box with no
  label below the directory, placeholder "Give it a name, e.g. ...", `setup["name"]`, never an `analyze()` argument): the arrow lists
  the bank (**both the Name and the Directory arrows list only the bank**: the old "used before" lists are gone from the page, though `sim_dirs` is still recorded; the setup page's lists serve Analyze, Validate/Matchup and Viewer alike, so the Name box and Add to bank are on all of them) and a pick in either fills Directory then Name through `input` events; **the simulation directory boxes (`#f-simdir`, `#sc-dir_N`) have no `list=`/datalist** (the browser's own folder suggestions would be a second drop-down; Browse… stays, and the type-ahead loop skips a box with no `<id>-folders`, so other directory boxes keep theirs); the directory's question (`#f-simdir-hint`, `#sc-dir_N-hint`) says "(Use the drop-down for simulations in the bank)" while the bank has entries; the button is disabled until both are filled and reads "In bank" when this exact pair is
  there (`bankKey` resolves a relative directory against `state.cwd`); results are toasts. Every such box is in `bankLists`, refreshed by `render`,
  `refreshSetup` and `refreshScSims`. Tests: `tests/test_simulation_bank.py`.
  **Add to the bank from the bank page** (the user's request, 2026-11-05): a primary **Add to the bank** button (`#bank-add-open`, in `#view-bank` directly under the table card, at the left, there when the bank is empty
  too; the user's correction: not in the footer bar, which has Back only) opens `#bank-add` (`openBankAdd`: `bank-add-name`, `bank-add-dir` with Browse… through
  `DESCRIBE["bank-add-dir"]`, a status line `bank-add-note`). It uses the shared `addToBank` (which now returns the reply; `/api/bank_add`, no new server
  code), shows a refusal in the pop-out and leaves it open, and blocks a directory that is not there by an `/api/probe` check as it is typed (a web address
  skips it). `#bank-add` is before `#picker` in the page so the folder browser sits above it, and `openPicker`/`closePicker` make it inert meanwhile.
  **Simulation info** (the user's design, 2026-11-05): the bank page's right-hand columns **Number of files / Number of variables / Years / Size of simulation** are empty until extracted;
  an entry with no `info` has a single **Add simulation info** button across the four cells (none for a web address, which shows dashes), and an entry with
  it has a **Regenerate** button in its own column right of them (before the ×), which reopens the same pop-out `#bank-info` with the settings used. The pop-out
  (`openBankInfo`; `bank-ndown` a `depthBox` filled from `folder_layout` when blank, `bank-exclude`, `bank-require`) counts files live through `/api/probe`
  (never a probe of its own), offers the random-subdirectory viewer by reusing `#sample` (`openSample(opener, source)`; it sits above `#bank-info`, which is made
  inert meanwhile) and has **Extract simulation info** at the bottom. `App.bank_extract` (`/api/bank_extract`, `_cancel`, `_clear`; state `bank_extract`
  = `{name, status, error}`, `renderBankExtract`) runs a thread: the filtered files by `simulation_paths`, their sizes summed, the variables (`analysis.streams`: the distinct names in the first file of each stream, as Analyze lists them; `None`, shown "—", if none can be read, which does not fail the extraction), the years from the files' times by
  `output_years.Reading` (its own, not `_output_years`, which is the setup page's), else `simulation_years` from the names; `user_cache.set_bank_info` keeps
  `{ndown, exclude, require, files, variables, years, size, extracted}` as the entry's optional `"info"` (`_bank_info` drops a badly formed one, so `bank()` entries with no
  info are still exactly `{name, path}`). Closing the pop-out, Restart and quit stop it (`_drop_bank_extract`). Nothing in Analyze/Compare uses the info yet.
- The package never ships or creates the file ahead of use, and writes are read-merge-write: other
  keys and entries survive, and an unreadable or newer-version file is left untouched. It must
  never raise, as a cache must not stop a run.

