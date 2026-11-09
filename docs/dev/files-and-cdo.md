# File workers and cdo sinfo checks

Moved out of `CLAUDE.md` to keep it short. The core rules stay there.

### How many workers read the simulation's files (`oceanval/file_workers.py`)

- Every read of what is in a simulation's files (times, years, `cdo sinfo`, sizes, modification times) uses `file_workers.workers()`:
  **min(6, half the cores this process may use), at least 1** (the user's rule, 2026-11-05). It imports nothing of OceanVal, as the scripts run
  by path (`output_years`, `viewer_times`) cannot use it: the parent passes the count in the request (`output_years.Reading`), and
  `viewer_times.default_cores` has its own copy of the rule (`tests/test_viewer_times.py` checks they agree). Used by `output_years.Reading`,
  `viewer_times`, `cdo_check._workers`, `matchall._index_workers` (an explicit `workers=` still wins).
- HDF5/netCDF reads stay in spawn pools or worker subprocesses (h5py holds a global lock, so threads would not run in parallel).
  Only thread-safe waiting work uses threads: `file_workers.stat_all` (`os.stat` of every file by a `ThreadPoolExecutor`, with `stopped=`),
  used by `output_years.signature`, `model_check.save_times_index`/`load_times_index` and the bank's size sum. A new loop of `os.stat`/`getsize`
  over a simulation's files must use it.

### `cdo sinfo` on every model file (`oceanval/cdo_check.py`)

- `cdo sinfo file.nc` must never segfault; a file it crashes on means OceanVal **cannot work with the data** (everything reads model files
  with CDO) and that the data may not be CF-compliant. Once the files are identified, `cdo_check.Check` (a thread plus up to 4 workers, a
  killable process group per file, 300 s a file) runs it on **all** of them and keeps `results` per path (`ok`/`segfault`/`error`/`unknown`;
  a missing `cdo`, a timeout or a CDO error that is not a crash is never a fault). `cdo_check.message` is the one wording: how many files, the
  CF-compliance warning and https://anaconda.org/conda-forge/cdo (try another CDO version).
- **Not a warning: it blocks.** Python API: `cdo_check.start(paths)` where `matchup()` has its files (`_files_found`), `analysis.locate_rows` (so
  `analyze()` and `compare_simulations()`), then `cdo_check.require(check)` raises `ValueError` before any work (matchup: after the time_shift
  question; locate_rows: before its other problems). `start` returns None when `news_marker()` is set (the window checked already), when the
  files are none, or when `cdo_check.ENABLED` is False (the tests' autouse `no_cdo_check` fixture; mark a test `cdo_check` and use `fake_cdo`
  to turn it on).
- Window: `App._start_cdo_check(simulations)` (Validate/Match up `setup`, Analyze `_analyze_setup`, Compare `simcompare_sims`, Viewer `_viewer_setup`;
  a no-op for the same files), `_cdo_fault` on the first segfault (and again when all are looked at): stops a run going, cancels the recipes
  window (whose heartbeat answers `blocked`, so `gui.js` goes to the main page), and `start_run` refuses while `_cdo_failed` is set.
  `state()["cdo_check"]` shows the pop-out `#cdo-failed` (`renderCdoFailed`): modal, above every other pop-out, **no Escape, backdrop or
  Back; Start again (`/api/restart_all`) is the only way on** (`reset` and `quit` drop the check).

