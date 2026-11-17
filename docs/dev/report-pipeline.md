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
   which fills `the_removed_items` in `chunk_start.pytemplate`; the notebooks ask `wanted("id")` (or a `show_...` flag set from
   it). `compare()` and `analyze()` fill the placeholder with `[]`. Three rules: **a gated item takes its lead-in text, plot and
   caption out together** (the `i_figure`/`i_table` counters then renumber on their own); **a gated item never gates what another
   page reads** (`chunk_results`, the `temporals` files, the regionals csv, `*_depth_summary.csv`, and the point bias csv are
   written whatever is shown); and a section whose items are all left out loses its heading. A new
   figure needs an `Item`, a `wanted()` gate and a line in `tests/test_report_omit.py`; `tests/test_report_items.py` checks the
   catalogue and the notebooks agree.
   `oceanval/release_figures.csv` chooses which items a PyPI release has (Y/N per id): the window offers only the Y ones, in amber as in
   development, and leaves the N ones out of its reports; see [app-windows.md](app-windows.md).
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
   **What a built-in variable and dataset can show is `oceanval/option_profiles.py`** (`PROFILES`, keyed `(kind, variable, source)`: depth, months,
   total, log, region), which `tests/test_option_profiles.py` checks against the catalogues, `totals.kind_of` and `skill.is_log`, and fails for a recipe
   with no entry. `annotate()` gives each matchup the window lists (`_report_matchups`, `matchups_found`) its `items` (the ids it can show or
   feed on the summary: `items_for`), its `options` (the report options it takes: `options_for`, which `overall_options` and `choose_matchups` use to
   put a hidden option back to its default), `default_sections` and `known`. The page shows an item only if some matchup of its page has it in `items`,
   and a report option only if some ticked matchup has it in `options`; the boxes that hang on what the user typed (subregions, transect) stay the
   page's. The user's own data has no profile, so it shows all its kind and layer allow. A new built-in recipe needs a `PROFILES` entry; a new report
   item needs its `when` conditions (`_holds` in `option_profiles.py` for those about the variable or dataset).
8. **The gridded page's figures from the gap report** (options 1, 3, 4, 8, 9, 13 and the peak month of 10) are chunks of their own, each
   with an item (`report_items`) and a heading of its own, so leaving a neighbour out never strands one: `chunk_density` (after
   `chunk_bias`), `chunk_peakmonth` (after `chunk_seasonal`, needs its `ds_ts` and 12 months), `chunk_zonal` (after the spatial table:
   `zonal_lines`, `hovmoller_clim`, `hovmoller_years`), and `chunk_profiles` and `chunk_depthmaps` (after `chunk_verticalstats`, whose
   `ds_levels` and `level_rows` the profiles use and then delete). Every value is nctoolkit's: `zonal_mean`/`zonpctl` (a curvilinear
   grid goes `to_latlon` first), `skill.paired` for the masks and log10, the peak month as `tmax` of `cmonth()` where the field equals
   its `tmax` (the last such month on a tie), its lag wrapped to -5..6 in `expr`, `sellevidx` for the maps at depth (`validate(map_depths=)`,
   `depths.check_map_depths`/`map_depths_for`/`nearest_level`, a level with no cell matched is skipped without a figure number).
   `oceanval/gridded_plots.py` only draws what they give it; the density scatter is the one figure that bins values (numpy, a time
   step at a time, the user's exception), and its numbers and line are `skill`'s. Files written whatever is shown:
   `oceanval_results/zonal_mean/zonalmean_{variable}_{source}.csv` (for compare) and `peak_month/peakmonth_{variable}_{source}.nc`.
   Axis labels take their unit as mathtext (`transects.unit_text`, `gridded_plots.mathtext_unit`), never `fix_unit`'s HTML. The point
   page's depth summary also writes `Model mean`/`Observation mean` (pandas) for `point_profiles`; the table on the page leaves them
   out. `tests/test_report_figures.py` checks the zonal means and the peak months against xarray, and every id against `omit`.
9. **Named sections** (the gap report's option 2) are `transects.SECTIONS` (`equator` 0°→360°E so the Pacific is in the middle,
   `pacific_155w`, `atlantic_25w`): straight transects with a `name`, so the extraction and drawing are the old ones.
   `check_transects` gives validate's (and live's) transect as None (the default: `default_sections` of the page's vertical file,
   `DEFAULT_SECTIONS` only if its longitudes `wraps_round`), False (`_gridded_notebook_text` drops `chunk_transect`) or a list.
   `chunk_transect` is one top-level loop over the list: `md()` only shows from a top-level loop (IPython's `ast_node_interactivity
   = "all"` prints expression statements in loops, not in functions), and `plt.show()` after each figure keeps them in order.
   A helper that returns its figure is called as `_fig = ...`, never as a bare statement: under `"all"` a bare call displays the figure once as the
   statement's value and again at `plt.show()` (`tests/test_figures_once.py` checks the chunks).
   `map_extent` is None for a line most of the way round the globe (`set_global`). Oxygen sections draw `OXYGEN_CONTOURS`
   (20, 60). The app's ticks `section_<name>` come first in the list, then the line of the user's own; none ticked passes nothing.
10. **compare()'s spatial page** (the gap report's options 6, 12, 13): `comparison_spatial` cell 7's loop, which already regrids each
    simulation's annual mean onto the mask every simulation has, takes `skill.dataset_stats` of it (log10 for `is_log`) before
    `cor_space` overwrites it, into `df_skill`, written to `compare_skill.csv` beside the notebooks. `oceanval/taylor.py` (the
    Cartesian sector of the original Taylor prototype, a colour per simulation, a number per variable) and `compare_plots.heatmap`
    (colour by rank within a variable) only draw. The zonal lines read each validation's `oceanval_results/zonal_mean` csvs;
    `gridded_plots.zonal_lines` takes `(lat, line)` for a simulation on a grid of its own. The best value of compare's tables is
    `<b>…</b>`: `df_display` returns a Styler, which renders HTML, so the old `x**` never showed bold.
11. **Totals** (the gap report's options 5 and 34, validate's part) are `oceanval/totals.py`: `sums` is one CDO expr (`gridarea`,
    or cos(latitude) times a degree square if CDO cannot) on the cells with both, then `spatial_sum`, per step and level; `factor`
    reads the matchup's units with `unit_conversion._parse` and pint (carbon's molar mass between moles and mass, seawater at 1025 kg
    m-3 for per kilogram); `inventory` weights the levels by `skill.layer_thickness`. `chunk_totals` (after `chunk_zonal`) writes
    `oceanval_results/totals/{variable}_{source}_totals.csv` for every flux or inventory matchup, and draws the flux's monthly total
    (`global_series`, `gridded_plots.total_series`); summary cell 23 is the table (`global_totals`). `data/reference_totals.csv`
    holds only ranges quoted from a paper in `research/papers`, with the quote, the paper's file and its DOI.
12. **analyze()'s totals** (`long_term={"NPP": {..., "total": True}}`, checked in `analysis.check_arguments`): `analysis_series.total_series`
    takes the layer's monthly means (in the lon/lat box), `totals.field_series` (nctoolkit) and the units' factor, a FLUX for the surface,
    bottom or vertical mean and an INVENTORY for the depth integral (the concentration's own units times the volume), and saves the yearly
    mean of the monthly totals as `{key}_{layer}_long_term_total.csv`, with `saved["total"]` in the info file; `analysis_plots.total_plot`
    draws it with the published ranges, and `chunk_longterm` shows it. FVCOM output and units OceanVal cannot total get a note instead.
13. **validate()'s summary Taylor diagram** (`summary.ipynb` cells 6-8) is `taylor.figure`, the
    same drawing as compare()'s. Cell 6 builds one row per variable, labelled "Model" as there is one simulation, from `skill.file_stats`
    (the spatial table's nsd and r), and cell 7 draws it; a variable's colour is the same for every marker, as in compare().


## The figures plotnine draws (2026-11-17)

Every report figure is Python; OceanVal needs no R. The figures ggplot2 used to draw are plotnine's, in
`oceanval/plotnine_figures.py`, one function per figure taking the data frame the page made: the point page's
`locations_map`, `mean_map`, `bias_map` (with `capped_bias`, whose csv is written whatever is shown) and `scatter` (detailed: all
months and each month; concise: all together), `point_template.ipynb`'s `depth_locations_map`, the summary's `depth_matchup_map`,
the gridded page's `regional_cycles` and `zonal_sections` (each region's model and observations, then their bias, stacked with
plotnine's composition), and `comparison_regional.ipynb`'s `comparison_cycles`. `show(plot, width, height)` draws at 72 dpi, so a size
in pixels is the page's. Units are `oceanval.unit_display.mathtext`. `tests/test_report_templates.py` fails if a template has
`%%R`, `rpy2` or `library(` again.

## Units (2026-11-17)

`oceanval/unit_display.py` writes every unit: `html` (the text and tables; `tidiers.fix_unit` is it), `mathtext` (figure labels;
`gridded_plots.mathtext_unit`, `transects.unit_text`, `analysis_plots.unit_text` call it) and `plain`. It reads a unit as factors
(a divisor's power is negative, `m3`/`m^3`/`m**3`/`m-3` are powers, O2/CO2/NO3 have subscripts, `umol` is µmol), so
`mmol O2/m**3` is mmol O<sub>2</sub> m<sup>-3</sup> and `mmol/m3` keeps its space. A unit that has been written already is left as
it is. `md()` no longer edits units: a template writes a unit with `fix_unit` before putting it in text (`**3` in markdown is
bold). `tests/test_unit_display.py` checks every catalogue unit.

## Report view and Gallery view (2026-11-17)

`oceanval/report_gallery.py`'s style and script are put in the pages of `validate()`'s report and its interim report
(`_write_offline_report_pages(gallery=True)`, from `_build_book_pages` and `live.py`), not in analyze()'s or compare_simulations()'s.
The script adds **Report view | Gallery view** at the top of the sidebar (kept in `localStorage`, Report view by default) and builds
the gallery from the page itself: a figure or table is every image (or table) in the `.jp-OutputArea-child`ren since the last caption
or `h2` before a caption (a `**Figure N**`/`**Table N**` paragraph); its tile is a thumbnail (a table icon) with its number and the
section's heading, and opens a pop-out with copies of them and the caption. Each figure is on the page once (a page opened from
disk cannot fetch fragments). PDF and Word are made from the page before this is added. `tests/test_report_gallery.py`.
