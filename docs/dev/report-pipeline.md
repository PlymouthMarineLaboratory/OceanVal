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

