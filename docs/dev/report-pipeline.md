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
