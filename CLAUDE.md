# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OceanVal is a Python package that automates ocean model validation: it matches model output
against observational datasets (point and gridded), computes skill metrics, and generates a
Jupyter Book–based HTML validation report (with optional PDF/Word export). Core public API lives
in `oceanval/__init__.py`: `matchup()` (model↔obs matching), `validate()` (build the report),
`rebuild()`, `compare()` (multi-simulation comparison report), `analyze()` (a model-only
climatology/phenology/time-series report, no observations), `compare_simulations()` (the same for 2–5
simulations side by side), `create_recipes()` (scaffold a
matchup script from model output), `add_gridded_comparison()`/`add_point_comparison()` (register
custom obs datasets), `deep_clean()`, `fix_toc()`.

## The app never runs on a phone

The OceanVal app (the `oceanval` command's browser windows: `oceanval_app.html`, `create_recipes_gui.html`, `gui.css`, `gui.js`) will
**never run on a phone** (the user's rule, 2026-11-04). Write no phone-specific code: no `<meta name="viewport">`, no phone-width
(≤640px) media queries or `matchMedia` layouts, no phone layouts, and no tests that set a phone-sized viewport (~390px) or check
that the page fits one. A narrowed desktop window is still handled (the 720px-and-wider rules, e.g. the grid's ≤860px sizes). The public docs
website (`docs-site/`, `docs/source/`) and the generated HTML report (its ≤720px sidebar) are read on any device and keep their own responsive CSS.

## Environment setup

Conda is the supported environment (`oceanval.yml`) — R packages (rpy2, r-tidyverse, r-ggtext,
etc.) and `cdo`/`nco` are required and not pip-installable:

```sh
conda env create -f oceanval.yml
conda activate oceanval
Rscript -e "install.packages('Rcpp', repos = 'https://cloud.r-project.org/')"
pip install .
```

Supported Python: 3.10–3.13.

## Running tests

**Use the `ov_testing` conda env for all oceanval testing** (2026-11-10, the user's rule): `source /local1/data/scratch/pmlersem/rwi/miniforge3/etc/profile.d/conda.sh && conda activate ov_testing`, or call `/local1/data/scratch/pmlersem/rwi/miniforge3/envs/ov_testing/bin/python -m pytest` directly. It is `oceanval.yml` plus pytest, pytest-timeout, pytest-cov, playwright with Chromium, jupyter-book 2 and pandoc, so the browser tests run instead of skipping and nothing needs borrowing from another env. oceanval itself is **not** installed in it: the scratch copy is what is imported (report tests that start kernels need `PYTHONPATH=<scratch copy>`). If playwright or Chromium go missing, fix them there (`pip install playwright && playwright install chromium`), not by symlinks.

**Always `python -m pytest`, never bare `pytest`.** The bare `pytest` entry point resolves the
*installed* oceanval in site-packages rather than the repo copy, so tests silently run against
stale code.

**Never run the matchup/validate tests from the repo root.** Several fixtures and test bodies
`shutil.rmtree` the `oceanval_matchups/`, `oceanval_report/`, and `oceanval_results/` directories
in the current working directory — real gitignored output can live under those names at the repo
root. Copy `oceanval/ tests/ data/ setup.py pyproject.toml` into a scratch directory and run
`python -m pytest` from there instead.

```sh
python -m pytest tests/                     # full suite (matchup/validate set: ~4 min)
python -m pytest tests/test_gridded_failures.py   # ~1 min
python -m pytest tests/test_create_recipes.py -k some_test  # single test
```

Coverage config (`.coveragerc`) excludes `tests/` and `oceanval/fvcom.py` (undocumented).

CI (`.github/workflows/python-app.yml`) builds a micromamba env with nctoolkit, cartopy, R/tidyverse,
jupyter-book, pandoc, texlive, then `pip install .` and runs the tests as six jobs side by side (matrix `group`: five named sets of test files balanced by run time, and a sixth that runs every file not named, so a new test file is still run); each job has its own checkout, which the cwd-relative `oceanval_matchups` folders need, and prints its 30 slowest tests (`--durations`) and uploads every test's time as a junit artifact (`durations-linux-<group>`, `durations-macos-<group>`): rebalance the groups from those (largest file first into the group with least time so far; `test_sim_plots.py` alone is over 10 minutes). A new slow test file should be added to a group in the workflow. **A figure test draws a figure once** (`test_sim_plots.draw` keeps each one drawn; every figure runs its script in a Python of its own, seconds each), and **two tests that would run the same `matchup()`/`analyze()`/`compare_simulations()` share one run** (a class/module fixture, or one test with both sets of checks). `.github/workflows/macos-app.yml`
runs the same groups on `macos-latest` (Apple silicon, 90 minute limit per job): keep the two
workflows' group lists the same.

On macOS (see the fixes from a Mac, 2026-10-22): there is no `timeout` command (use pytest's
`--timeout`), no `os.sched_getaffinity` (`matchall._index_workers` falls back to `os.cpu_count()`;
tests patch it with `raising=False`), and the report notebooks' kernels import the *installed*
oceanval, so reinstall (`pip install --no-deps .`) before running report tests against changed code.
`_run_filtered` waits for the process, not the end of its pipe (`_OUTPUT_GRACE`): nctoolkit's Manager
servers in a kernel that died keep the pipe open, which hung the interim report's builder.
"resource_tracker: There appear to be 1 leaked semaphore objects" at quit is harmless (the tracker removes it): a
process that made a spawn-context Lock (tqdm's `create_mp_lock` does, in a forked process later killed) ended without its
finalizers. `OCEANVAL_TRACE_SEMAPHORES=<file> oceanval` records every semaphore made (`oceanval/semaphore_trace.py`), and
`python -m oceanval.semaphore_trace <file>` lists those never released, with the stack that made each.
Never put conda's `lib` on `DYLD_LIBRARY_PATH` (`utils.restrict_r_to_conda` sets `LD_LIBRARY_PATH` on Linux only):
dyld swaps libraries by file name, so ImageIO's own `libPng.dylib` became conda's libpng (the file system ignores
case) and every report kernel inheriting it could die in a `%%R` figure (SIGBUS in `QuartzBitmap_Close`).

## Architecture

The detail of each area is in `docs/dev/`. Read the file for the area you are changing.

- **Report pipeline** (`validate()`, `compare()`, the Jupyter Book, HTML as the baseline): [docs/dev/report-pipeline.md](docs/dev/report-pipeline.md)
- **Matching and recipes** (`matchall.py`, `gridded.py`, `parsers.py`, `create_recipes.py`, the domain, the matchups' keys): [docs/dev/matching-and-recipes.md](docs/dev/matching-and-recipes.md)
- **Recipes of the user's own** (`.oceanvalrc`): [docs/dev/user-recipes.md](docs/dev/user-recipes.md)
- **What OceanVal remembers** (`.oceanvalcache`, the simulation bank): [docs/dev/cache-and-bank.md](docs/dev/cache-and-bank.md)
- **The data store** (`data_store.py`, Expand, the store view): [docs/dev/data-store.md](docs/dev/data-store.md)
- **File workers and `cdo sinfo` checks** (`file_workers.py`, `cdo_check.py`): [docs/dev/files-and-cdo.md](docs/dev/files-and-cdo.md)
- **Analyze** (`analysis.py`, FVCOM, transects, map changes): [docs/dev/analyze.md](docs/dev/analyze.md)
- **Compare simulations** (`sim_compare.py`, unify, domain mask, units): [docs/dev/compare-simulations.md](docs/dev/compare-simulations.md)
- **Compatibility checks, and view simulation output** (`compat_check.py`, `pattern_checks.py`, `viewer_*.py`): [docs/dev/tools-and-viewer.md](docs/dev/tools-and-viewer.md)
- **The `oceanval` command and its windows** (`app.py`, the pages, Back, Restart, Preferences): [docs/dev/app-windows.md](docs/dev/app-windows.md)

Rules that apply across the code:

1. **Model variables are read only by `oceanval/model_expr.py`.** Never `split("+")` a model variable. `tests/test_model_expr.py` checks it against CDO; `OceanValGUI.expression` in `gui.js` is a port, so change both.
2. **Import nctoolkit through `oceanval/quiet_nc.py`** (`from oceanval.quiet_nc import nc`).
3. **Ask every question through `oceanval/prompts.py`** (`ask`, `interactive`, `answered_by`). Never call `input()` or `isatty()` directly.
4. **Never `.load()` or read the whole values of a model file.** Model output is processed by nctoolkit/CDO, which streams it. xarray is for coordinates and small files OceanVal writes itself.
5. **Before `topvalue`, `bottomvalue` or `vertical_mean`, ask for `surface.finite_missing(ds)`** (`setmissval,1e20`). A NaN missing value breaks CDO 2.4.1 for those operators. A new one must do the same (see [docs/dev/files-and-cdo.md](docs/dev/files-and-cdo.md)).
6. **Every window read of netCDF holds `oceanval.netcdf_lock.LOCK`.** CDO runs in its own process and needs no lock.
7. **Every typed box has `autocomplete="off"`, and every colour is a token** (`:root` in `data/gui.css`), never hex.
8. **Every user-visible change needs the three documentation surfaces**, listed below.

### Network dependency for gridded recipes

Built-in gridded recipes point at remote THREDDS/data servers (NCEI for WOA23, oceancolour.org for
OCCCI, ICDC for NSBC). These are frequently unreachable/slow from development machines. When
testing code that registers recipes, pass `file_check=False` and expect registering the full
recipe set to take significant time even then; prefer `oceanval.parsers.find_recipe` directly
(pure, no network) to validate recipe arguments quickly.

### Documentation surfaces (keep in sync, they drift independently)

There are three places describing behavior, which must be updated together after any
user-facing change to the package:

1. **`docs-site/`** — the marketing/docs site deployed by `.github/workflows/pages.yml` to
   `PlymouthMarineLaboratory.github.io/OceanVal`. Plain static HTML, no build step or templating engine, so
   shared header/nav/footer markup is duplicated across all eleven `.html` files and must be
   edited in each if changed structurally. See `docs-site/README.md` for full details (version
   badge mechanics, the `archive/` snapshot system, page↔`.rst` mapping).
2. **`docs/source/*.rst`** — the Sphinx/Read the Docs source that `docs-site/`'s pages were
   originally redesigned from and roughly mirror 1:1 (e.g. `recipes.rst` ↔ `recipes.html`).
3. **`README.md`** (repo root) — usage examples/feature list.

Don't hand-edit generated pieces: `docs-site/archive/`, the
`<!-- OCEANVAL_VERSION_HISTORY_START/END -->` block in `version-history.html`, and each page's
`#oceanval-docs-version` badge — all are maintained automatically (see `docs-site/README.md` and
`claude_updates.md`, which has the full prompt/checklist for post-change doc sync plus specific
pitfalls to avoid, e.g. `.gitignore`'s `/oceanval_*` rule must stay root-anchored).

## Known environment quirks (re-verify before relying on these)

- `weasyprint` may be present but broken (version/tinycss2 mismatch) in some installs, causing
  `validate(pdf=True)` to warn and silently skip PDF generation rather than error.
- Report-output changes (PDF/Word/HTML) are best verified by actually rendering and visually
  inspecting the artifact (rasterize PDFs, screenshot HTML with a headless browser using a virtual
  time budget for MathJax, etc.) rather than reasoning from code — these bugs tend to be purely
  visual.
