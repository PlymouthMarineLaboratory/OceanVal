# Report pipeline

Moved out of `CLAUDE.md` to keep it short. The core rules stay there.

### Report pipeline

`validate()` and `compare()` don't render HTML directly — they build a **Jupyter Book**:

1. Notebook/chunk templates live in `oceanval/data/` (`*.ipynb`, `chunk_*.pytemplate`,
   `gridded_template.ipynb`, `point_template.ipynb`, `_config.yml`, `_toc.yml`).
2. `oceanval/chunkers.py` (`add_chunks`) and code in `__init__.py` assemble per-variable notebooks
   by string-substituting `chunk_*` placeholders (e.g. `chunk_point_surface`, `chunk_seasonal`)
   depending on what data/options are present, then use `jupytext` to sync `.py:percent` ⇄ `.ipynb`.
3. `_build_book()` in `__init__.py` runs the notebooks with `nbconvert` and writes each as an HTML
   page with the navigation (`_write_offline_report_pages`, which reads the book's `_toc.yml` captions)
   to produce the HTML report. OceanVal needs jupyter-book >= 2 (pinned in `requirements.txt`) but never
   runs `jupyter-book` itself; there is no jupyter-book 1 (Sphinx) path or `_config.yml` any more, so
   don't add version checks. `_render_offline_report_pdfs`/
   `_render_offline_report_word` (pandoc-based, with custom python-docx post-processing in
   `_polish_word_report`) produce the optional PDF/Word exports.
4. **The HTML report is the baseline** — it's what users read day to day, and each HTML page is a
   standalone document numbering its own figures/tables from 1. PDF/Word are *derived* exports;
   changes to export code must leave HTML output (including figure/table numbering) unchanged.
   Only the *combined* PDF/Word documents (concatenating all notebooks) get chapter-prefixed
   numbering — per-page PDF/Word exports mirror their HTML page and still number from 1.
5. **Figures and analyses the user leaves out (`validate(omit=)`)** are named in `oceanval/report_items.py` (`ITEMS`, one id for
   each, unique across the gridded, point and summary pages; `check_omit`, `check_against`, `removed_for`). `validate()` and the
   interim report (`live.OPTIONS`, `LiveValidation.make_page`) give each page its ids through `_rewrite_notebook_source(removed=)`,
   which fills `the_removed_items` in `chunk_start.pytemplate`; the notebooks ask `wanted("id")` (R cells: a `show_...` flag set from
   it). `compare()` and `analyze()` fill the placeholder with `[]`. Three rules: **a gated item takes its lead-in text, plot and
   caption out together** (the `i_figure`/`i_table` counters then renumber on their own); **a gated item never gates what another
   page reads** (`chunk_results`, the `temporals` files, the regionals csv, `*_depth_summary.csv`, the point bias csv and the R
   `title` its scatters use are written whatever is shown); and a section whose items are all left out loses its heading. A new
   figure needs an `Item`, a `wanted()` gate and a line in `tests/test_report_omit.py`; `tests/test_report_items.py` checks the
   catalogue and the notebooks agree.
6. **Statistics are worked out once, in `oceanval/skill.py`, and by nctoolkit** (the gap report's options 30–33; the user's rule,
   2026-11-14: every calculation on gridded data is nctoolkit's, never xarray or numpy). `sums(ds, log)` runs one CDO `expr` (area `gridarea(model)`, or `cos(clat())` weights if CDO cannot work areas out): the cells with both (`isMissval`), their area `w`, `w` times the model and the observation, and `w` times the squared deviations of each from its own area-weighted mean over the grid (`fldsum` inside the expr) and their product, the squared and absolute difference, then `spatial_sum()`, one row per time step and level, written in F64 (nctoolkit forgets `set_precision` at each run, so it is set for the last). The deviations are centred in CDO because E[x²] − E[x]² from raw sums lost the variance of a field whose spread is small next to its mean (r came out 1.0003). Only `stats_from_sums` is Python, pooling the rows (over months, or levels weighted by `layer_thickness`) with the spread of the rows' means added, into the means, population SDs, bias, pbias, MAE, RMSD, centred RMSD, r, nsd and crmsd/σo, so crmsd² = σo² + σm² − 2σoσm·r holds. CDO's `log10(0)` is −inf, so the log is `(x>0)?log10(x):missval(x)`; and `0*missing` is 0 in CDO's expr, so masks are ternaries (`skill.paired` masks a dataset for the figures). The tests check these numbers independently with xarray and xCDAT's area weights (`tests/test_skill.py`, `tests/test_report_statistics.py`). `LOG_VARIABLES` (chlorophyll, kd490; `is_log`) are also given on log10
   values (no pbias). The summary's bias and spatial tables (`file_stats` of the annual means), the gridded regional table (cell 21,
   `dataset_stats` of the climatology times each region's mask, written to `regionals/{source}_{variable}_regional_stats.csv` with
   `scale` linear/log10 and a "Full Domain" row when the region file has none), and `chunk_verticalstats` (`level_stats`, then
   `bin_stats` by `depth_bins`, written to `oceanval_results/{variable}_{source}_gridded_depth_summary.csv`, which the summary's depth
   tables read beside the point `*_depth_summary.csv`) all use it. Point matchups are csv tables, so the point page's log10 table uses
   `weighted_stats`/`log_stats` on its columns (pandas). `tidiers.df_display` drops " concentration" from a `Variable` column only if
   there is one.
7. **The built-in region sets are `oceanval/regions.py`** (`BUILT_IN`, `path`, `is_built_in`, `SOURCES` for the page's Data Sources):
   `nwes`, `global`, and `latbands`, `cmems`, `biomes`, the last three written by `scripts/make_region_files.py` on `global_subdomains.nc`'s
   1° grid, land masked with `TM_WORLD_BORDERS`. Every place that takes `subregions` (validate, live, analyze, compare_simulations, the
   windows' checks, `gridded_template` cell 18, `comparison_regional`) asks it, so a new set is a file and an entry there. The report item
   condition `depths` is a point all-layer matchup or a gridded vertical one (`itemApplies` in `oceanval_app.html`).
