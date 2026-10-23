import importlib.resources
import os
import re
import shutil
from unittest.mock import patch

import nbformat
import pytest

import oceanval


def _mock_popen(popen, lines=(), returncode=0):
    """Give a mocked subprocess.Popen the streamed output and exit code that
    _run_filtered reads from the process it starts."""
    popen.return_value.stdout = iter(lines)
    popen.return_value.returncode = returncode


def test_run_filtered_drops_the_tcp_kernel_warning(capsys):
    with patch("oceanval.subprocess.Popen") as popen:
        _mock_popen(
            popen,
            [
                "reading sources... [100%] notebooks/test\n",
                "WARNING | Kernel is running over TCP without encryption. All "
                "communication (including code and outputs) is sent in plain text "
                "and is susceptible to eavesdropping. Use IPC transport or launch "
                "with kernel manager-provisioned CurveZMQ keys to enable transport "
                "encryption.\n",
                "build succeeded.\n",
            ],
        )

        oceanval._run_filtered(["jupyter-book", "build", "report"], check=True)

    # output is streamed line by line, so stderr is merged into stdout
    popen.assert_called_once_with(
        ["jupyter-book", "build", "report"],
        stdout=oceanval.subprocess.PIPE,
        stderr=oceanval.subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    out = capsys.readouterr().out
    assert "Kernel is running over TCP" not in out
    assert "reading sources... [100%] notebooks/test" in out
    assert "build succeeded." in out


def test_run_filtered_raises_on_nonzero_exit():
    with patch("oceanval.subprocess.Popen") as popen:
        _mock_popen(popen, ["boom\n"], returncode=1)

        with pytest.raises(oceanval.subprocess.CalledProcessError) as error:
            oceanval._run_filtered(["jupyter-book", "build", "report"], check=True)

    assert error.value.returncode == 1
    assert error.value.output == "boom\n"


def test_run_filtered_does_not_wait_for_a_process_left_holding_its_pipe():
    """A process that outlives the one run (as nctoolkit's servers in a dead
    kernel do) keeps its output pipe open: _run_filtered must return when the
    process it ran ends, not when the pipe does."""
    import signal
    import time

    started = time.monotonic()
    with patch.object(oceanval, "_OUTPUT_GRACE", 1):
        with pytest.raises(oceanval.subprocess.CalledProcessError) as error:
            oceanval._run_filtered(["sh", "-c", "echo before; sleep 60 & echo stray $!; exit 3"], check=True)
    elapsed = time.monotonic() - started
    stray = re.search(r"stray (\d+)", error.value.output)
    if stray:
        try:
            os.kill(int(stray.group(1)), signal.SIGKILL)
        except ProcessLookupError:
            pass
    assert error.value.returncode == 3
    assert "before" in error.value.output
    assert elapsed < 20


def test_build_book_runs_the_notebooks_with_nbconvert():
    with patch(
        "oceanval.glob.glob",
        return_value=[
            "report/notebooks/summary.ipynb",
            "report/notebooks/example.ipynb",
        ],
    ), patch("oceanval.os.makedirs"), patch("oceanval.shutil.copyfile"), patch(
        "oceanval._remove_diagnostic_outputs"
    ) as remove_diagnostics, patch(
        "oceanval._write_offline_report_pages"
    ) as write_pages, patch(
        "oceanval.subprocess.Popen"
    ) as popen, patch("oceanval.subprocess.run") as run:
        _mock_popen(popen)
        oceanval._build_book("report")

    # the kernel-launching --execute call goes through _run_filtered, which
    # streams it with Popen so the kernel warning can be dropped
    popen.assert_called_once_with(
        [
            "jupyter",
            "nbconvert",
            "--to",
            "notebook",
            "--execute",
            "--inplace",
            "--allow-errors",
            "--ExecutePreprocessor.timeout=500",
            "report/notebooks/example.ipynb",
            "report/notebooks/summary.ipynb",
        ],
        stdout=oceanval.subprocess.PIPE,
        stderr=oceanval.subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    # the --to html conversion afterwards never starts a kernel, so it's left
    # as a plain streamed subprocess.run call, unfiltered
    run.assert_called_once_with(
        [
            "jupyter",
            "nbconvert",
            "--to",
            "html",
            "--no-input",
            "--output-dir",
            "report/_build/html/notebooks",
            "report/notebooks/example.ipynb",
            "report/notebooks/summary.ipynb",
        ],
        check=True,
    )
    write_pages.assert_called_once_with(
        "report/_build/html",
        ["report/notebooks/example.ipynb", "report/notebooks/summary.ipynb"],
        validation_links=None,
        pdf=False,
        word=False,
    )
    remove_diagnostics.assert_called_once_with(
        ["report/notebooks/example.ipynb", "report/notebooks/summary.ipynb"]
    )


def test_remove_diagnostic_outputs_preserves_formatted_content(tmp_path):
    notebook_path = tmp_path / "report.ipynb"
    notebook = nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_code_cell(
                outputs=[
                    nbformat.v4.new_output("stream", text="warning"),
                    nbformat.v4.new_output("error", ename="Error", evalue="failure"),
                        nbformat.v4.new_output(
                            "error", ename="RInterpreterError", evalue="R plot failed"
                        ),
                    nbformat.v4.new_output("execute_result", data={"text/plain": "0"}),
                    nbformat.v4.new_output("execute_result", data={"text/html": "<table>"}),
                    nbformat.v4.new_output("display_data", data={"image/png": "data"}),
                ]
            )
        ]
    )
    nbformat.write(notebook, notebook_path)

    oceanval._remove_diagnostic_outputs([str(notebook_path)])

    outputs = nbformat.read(notebook_path, as_version=4).cells[0].outputs
    assert [output.output_type for output in outputs] == [
        "error",
        "execute_result",
        "display_data",
    ]
    assert outputs[0].ename == "RInterpreterError"


def test_remove_diagnostic_outputs_preserves_remove_cell_tags(tmp_path):
    notebook_path = tmp_path / "report.ipynb"
    notebook = nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_markdown_cell(
                "## Read in the data",
                metadata={"tags": ["remove-cell"]},
            ),
            nbformat.v4.new_markdown_cell("Visible content"),
        ]
    )
    nbformat.write(notebook, notebook_path)

    oceanval._remove_diagnostic_outputs([str(notebook_path)])

    written_notebook = nbformat.read(notebook_path, as_version=4)
    assert len(written_notebook.cells) == 2
    assert written_notebook.cells[0].source == "## Read in the data"
    assert written_notebook.cells[0].metadata["tags"] == ["remove-cell"]
    assert written_notebook.cells[1].source == "Visible content"


def test_remove_diagnostic_outputs_drops_warning_streams(tmp_path):
    notebook_path = tmp_path / "report.ipynb"
    notebook = nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_code_cell(
                outputs=[
                    nbformat.v4.new_output("stream", name="stdout", text="normal output"),
                    nbformat.v4.new_output(
                        "stream",
                        name="stderr",
                        text="Warning: plotting code produced a warning",
                    ),
                    nbformat.v4.new_output("display_data", data={"text/plain": "plot"}),
                ]
            )
        ]
    )
    nbformat.write(notebook, notebook_path)

    oceanval._remove_diagnostic_outputs([str(notebook_path)])

    written_notebook = nbformat.read(notebook_path, as_version=4)
    outputs = written_notebook.cells[0].outputs
    assert outputs == []


def test_remove_diagnostic_outputs_keeps_real_plot_outputs(tmp_path):
    notebook_path = tmp_path / "report.ipynb"
    notebook = nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_code_cell(
                outputs=[
                    nbformat.v4.new_output(
                        "display_data",
                        data={"text/plain": "<Figure size 640x480 with 0 Axes>"},
                    ),
                    nbformat.v4.new_output(
                        "display_data",
                        data={"image/png": "data:image/png;base64,abc123"},
                    ),
                ]
            )
        ]
    )
    nbformat.write(notebook, notebook_path)

    oceanval._remove_diagnostic_outputs([str(notebook_path)])

    written_notebook = nbformat.read(notebook_path, as_version=4)
    outputs = written_notebook.cells[0].outputs
    assert len(outputs) == 1
    assert outputs[0].data == {"image/png": "data:image/png;base64,abc123"}


def test_offline_report_pages_group_navigation_and_hide_code(tmp_path):
    output_dir = tmp_path / "_build" / "html"
    notebook_dir = output_dir / "notebooks"
    notebook_dir.mkdir(parents=True)
    source_notebook_dir = tmp_path / "notebooks"
    source_notebook_dir.mkdir()
    (tmp_path / "_toc.yml").write_text(
        "format: jb-book\nroot: intro\nparts:\n"
        "- caption: Summaries\n  chapters:\n"
        "  - file: notebooks/001_methods.ipynb\n"
        "  - file: notebooks/002_summary.ipynb\n"
        "- caption: Temperature\n  chapters:\n"
        "  - file: notebooks/003_foo_temperature.ipynb\n"
    )
    pages = [
        notebook_dir / "001_methods.html",
        notebook_dir / "002_summary.html",
        notebook_dir / "003_foo_temperature.html",
    ]
    for page in pages:
        page.write_text(
            '<html><head></head><body class="jp-Notebook"><main>Report</main></body></html>'
        )
    notebook_titles = {
        "001_methods": "Validation metrics summary",
        "002_summary": "Full domain summary statistics of model performance",
        "003_foo_temperature": "Sea surface temperature validation using gridded observations from FOO",
    }
    for stem, title in notebook_titles.items():
        notebook = nbformat.v4.new_notebook(
            cells=[nbformat.v4.new_markdown_cell(f"# {title}")]
        )
        nbformat.write(notebook, source_notebook_dir / f"{stem}.ipynb")

    oceanval._write_offline_report_pages(
        str(output_dir),
        [
            str(source_notebook_dir / "001_methods.ipynb"),
            str(source_notebook_dir / "002_summary.ipynb"),
            str(source_notebook_dir / "003_foo_temperature.ipynb"),
        ],
        pdf=True,
    )

    # there is no index.html: reports land straight on a notebook page
    assert not (output_dir / "index.html").exists()

    report_page = pages[-1].read_text()
    assert "Validation metrics summary" in report_page
    assert "Full domain summary statistics of model performance" in report_page
    assert "Temperature" in report_page
    assert "Sea surface temperature validation using gridded observations from FOO" in report_page
    assert 'href="003_foo_temperature.html"' in report_page
    assert ".jp-Notebook{margin-left:340px!important;margin-right:300px!important;font-size:1.15rem}" in report_page
    assert ".oceanval-nav-sections{flex:1 1 auto;overflow-y:auto;min-height:0" in report_page
    assert ".oceanval-sidebar{position:fixed!important" in report_page
    assert "sessionStorage" in report_page
    assert report_page.index('class="oceanval-nav-sections"') < report_page.index('class="oceanval-viewpdf-btn"')


def test_offline_report_exports_are_off_by_default(tmp_path):
    output_dir = tmp_path / "_build" / "html"
    notebook_dir = output_dir / "notebooks"
    notebook_dir.mkdir(parents=True)
    source_notebook_dir = tmp_path / "notebooks"
    source_notebook_dir.mkdir()

    page = notebook_dir / "001_methods.html"
    page.write_text(
        '<html><head></head><body class="jp-Notebook"><main>Report</main></body></html>'
    )
    notebook = nbformat.v4.new_notebook(
        cells=[nbformat.v4.new_markdown_cell("# Validation metrics summary")]
    )
    notebook_path = source_notebook_dir / "001_methods.ipynb"
    nbformat.write(notebook, notebook_path)

    oceanval._write_offline_report_pages(str(output_dir), [str(notebook_path)])

    report_page = page.read_text()
    # the CSS rules for these classes are always embedded in the stylesheet;
    # what must be absent is the actual element using them
    assert 'class="oceanval-viewpdf-btn"' not in report_page
    assert 'class="oceanval-download-btn"' not in report_page
    assert list(output_dir.rglob("*.pdf")) == []
    # nor is there a Word file
    assert 'class="oceanval-word-btn"' not in report_page
    assert "Download page as Word" not in report_page
    assert list(output_dir.rglob("*.docx")) == []


def test_offline_report_word_keeps_latex_for_pandoc(tmp_path):
    output_dir = tmp_path / "_build" / "html"
    notebook_dir = output_dir / "notebooks"
    notebook_dir.mkdir(parents=True)
    source_notebook_dir = tmp_path / "notebooks"
    source_notebook_dir.mkdir()

    (notebook_dir / "001_methods.html").write_text(
        "<html><head><title>Validation metrics summary</title></head>"
        '<body class="jp-Notebook"><main><p>a model $m$</p></main></body></html>'
    )
    notebook = nbformat.v4.new_notebook(
        cells=[nbformat.v4.new_markdown_cell("# Validation metrics summary")]
    )
    notebook_path = source_notebook_dir / "001_methods.ipynb"
    nbformat.write(notebook, notebook_path)

    # the mocked pandoc writes no file, so there is nothing to format
    with patch("oceanval.subprocess.run") as run, patch(
        "oceanval._polish_word_report"
    ):
        oceanval._write_offline_report_pages(
            str(output_dir), [str(notebook_path)], word=True
        )

    # one Word file per page, then the combined report
    outputs = [call.args[0][-1] for call in run.call_args_list]
    assert outputs == [
        str(notebook_dir / "001_methods.docx"),
        str(notebook_dir / "oceanval_report.docx"),
    ]
    assert all(
        call.args[0][0] == "pandoc" and "html+tex_math_dollars" in call.args[0]
        for call in run.call_args_list
    )

    # pandoc turns LaTeX into real Word equations, so unlike the PDF export
    # the maths must reach it as LaTeX rather than as images
    source = run.call_args.kwargs["input"].decode()
    assert "$m$" in source
    assert "data:image/svg+xml" not in source

    # both the whole report and the page itself are offered as downloads
    report_page = (notebook_dir / "001_methods.html").read_text()
    assert (
        '<a href="oceanval_report.docx" class="oceanval-word-btn" download>'
        "Download as Word</a>"
    ) in report_page
    assert (
        '<a class="oceanval-download-btn" href="001_methods.docx" download>'
        "Download page as Word</a>"
    ) in report_page


def test_chapter_numbered_body_renumbers_captions_and_references():
    body = (
        '<h1 id="x">Temperature validation</h1>'
        "<p>Figure 2 shows the bias.</p>"
        "<p><strong>Figure 2</strong>: Bias of temperature.</p>"
        "<p><strong>Table 1</strong>: Summary statistics.</p>"
    )

    numbered = oceanval._chapter_numbered_body(body, 3)

    assert '<h1 id="x">3. Temperature validation</h1>' in numbered
    # the cross-reference in the prose has to stay in step with its caption
    assert "Figure 3.2 shows the bias" in numbered
    assert "<strong>Figure 3.2</strong>" in numbered
    assert "<strong>Table 3.1</strong>" in numbered


def test_chapter_numbering_is_only_used_for_the_combined_report(tmp_path):
    output_dir = tmp_path / "_build" / "html"
    notebook_dir = output_dir / "notebooks"
    notebook_dir.mkdir(parents=True)
    source_notebook_dir = tmp_path / "notebooks"
    source_notebook_dir.mkdir()

    notebooks = []
    for stem, title in [("001_first", "First"), ("002_second", "Second")]:
        (notebook_dir / f"{stem}.html").write_text(
            f"<html><head><title>{title}</title></head>"
            f'<body class="jp-Notebook"><h1>{title}</h1>'
            "<p><strong>Figure 1</strong>: A figure.</p></body></html>"
        )
        notebook = nbformat.v4.new_notebook(
            cells=[nbformat.v4.new_markdown_cell(f"# {title}")]
        )
        path = source_notebook_dir / f"{stem}.ipynb"
        nbformat.write(notebook, path)
        notebooks.append(str(path))

    with patch("oceanval._render_offline_report_pdfs") as render:
        oceanval._write_offline_report_pages(str(output_dir), notebooks, pdf=True)

    # each HTML page stands alone, so it keeps numbering from 1
    for stem in ("001_first", "002_second"):
        assert "<strong>Figure 1</strong>" in (notebook_dir / f"{stem}.html").read_text()
        assert "Figure 1.1" not in (notebook_dir / f"{stem}.html").read_text()

    jobs = render.call_args.args[0]
    per_page = next(src for src, path, _ in jobs if path.endswith("002_second.pdf"))
    combined = next(src for src, path, _ in jobs if path.endswith("oceanval_report.pdf"))

    # the per-page PDF mirrors its HTML page, the combined one numbers by chapter
    assert "<strong>Figure 1</strong>" in per_page
    assert "<strong>Figure 1.1</strong>" in combined
    assert "<strong>Figure 2.1</strong>" in combined


def test_word_report_is_formatted(tmp_path):
    docx = pytest.importorskip("docx")

    document = docx.Document()
    document.add_paragraph("Some prose")
    document.add_paragraph().add_run().add_picture(
        str(
            importlib.resources.files("oceanval").joinpath(
                "data/oceanval_wordmark.png"
            )
        ),
        width=docx.shared.Mm(20),
    )
    document.add_table(rows=1, cols=2)
    report = tmp_path / "oceanval_report.docx"
    document.save(str(report))

    oceanval._polish_word_report(str(report))

    formatted = docx.Document(str(report))
    prose, figure = formatted.paragraphs[0], formatted.paragraphs[1]
    assert prose.alignment is None
    assert figure.alignment == docx.enum.text.WD_ALIGN_PARAGRAPH.CENTER
    assert (
        formatted.tables[0].alignment == docx.enum.table.WD_TABLE_ALIGNMENT.CENTER
    )

    footer = formatted.sections[0].footer
    footer_xml = footer._element.xml
    assert "PAGE" in footer_xml and "NUMPAGES" in footer_xml
    # the footer is the wordmark alone - no caption, and no link off the page
    assert "Produced by" not in footer_xml
    assert not [rel for rel in footer.part.rels.values() if rel.is_external]


def _docx_from_html(tmp_path, body, name="report.docx", title=None):
    source = oceanval._word_report_source(body)
    path = tmp_path / name
    oceanval._render_offline_report_word(
        [(source, str(path), title, title is not None)], str(tmp_path)
    )
    return path


def test_word_tables_are_ruled_like_a_book_table(tmp_path):
    docx = pytest.importorskip("docx")
    qn = pytest.importorskip("docx.oxml.ns").qn
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc is not installed")

    report = _docx_from_html(
        tmp_path,
        "<table><thead><tr><th>Variable</th><th>Bias</th></tr></thead>"
        "<tbody><tr><td>Temperature</td><td>0.14</td></tr>"
        "<tr><td>Salinity</td><td>-0.21</td></tr></tbody></table>",
    )
    table = docx.Document(str(report)).tables[0]

    def border(properties, edge):
        borders = properties.find(qn("w:tblBorders")) if properties.tag.endswith(
            "}tblPr"
        ) else properties.find(qn("w:tcBorders"))
        return borders.find(qn(f"w:{edge}")).get(qn("w:val"))

    # a rule above the table and below its last row, and none down the sides
    # or between the body rows
    tbl_pr = table._tbl.tblPr
    assert border(tbl_pr, "bottom") == "single"
    assert border(tbl_pr, "insideH") == "none"
    assert border(tbl_pr, "insideV") == "none"
    assert border(tbl_pr, "left") == "none"

    header = table.rows[0]
    for cell in header.cells:
        # the third rule, under a header the reader can pick out at a glance
        assert border(cell._tc.tcPr, "top") == "single"
        assert border(cell._tc.tcPr, "bottom") == "single"
        assert all(run.font.bold for run in cell.paragraphs[0].runs)

    for cell in table.rows[1].cells:
        assert all(not run.font.bold for run in cell.paragraphs[0].runs)


def test_word_report_drops_the_heading_bookmarks(tmp_path):
    docx = pytest.importorskip("docx")
    qn = pytest.importorskip("docx.oxml.ns").qn
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc is not installed")

    report = _docx_from_html(
        tmp_path,
        '<h1 id="summary">Summary</h1><p>See <a href="#detail">the detail</a>.</p>'
        '<h2 id="detail">Detail</h2><p>Prose.</p>',
    )
    body = docx.Document(str(report)).element.body
    names = [start.get(qn("w:name")) for start in body.iter(qn("w:bookmarkStart"))]

    # Word marks every bookmark in the margin, and the headings' own ones are
    # never pointed at; the one an internal link needs is kept. pandoc names it
    # after the heading's id, with a leading underscore from 3.12 on
    anchors = [link.get(qn("w:anchor")) for link in body.iter(qn("w:hyperlink"))]
    assert anchors and names == anchors
    assert names[0].lstrip("_") == "detail"
    assert [end.get(qn("w:id")) for end in body.iter(qn("w:bookmarkEnd"))] == [
        next(body.iter(qn("w:bookmarkStart"))).get(qn("w:id"))
    ]


def test_combined_word_report_starts_each_chapter_on_a_new_page(tmp_path):
    docx = pytest.importorskip("docx")
    if shutil.which("pandoc") is None:
        pytest.skip("pandoc is not installed")

    body = "<h1>First</h1><p>One.</p><h1>Second</h1><p>Two.</p><h1>Third</h1>"
    combined = docx.Document(
        str(_docx_from_html(tmp_path, body, "combined.docx", title="OceanVal"))
    )
    chapters = [
        paragraph
        for paragraph in combined.paragraphs
        if paragraph.style.name == "Heading 1"
    ]
    # the first chapter follows the report title rather than leaving a page
    # holding nothing but it
    assert [
        chapter.paragraph_format.page_break_before for chapter in chapters
    ] == [None, True, True]

    per_page = docx.Document(str(_docx_from_html(tmp_path, body, "page.docx")))
    assert all(
        paragraph.paragraph_format.page_break_before is None
        for paragraph in per_page.paragraphs
    )


def test_per_page_word_file_is_not_chapter_numbered(tmp_path):
    output_dir = tmp_path / "_build" / "html"
    notebook_dir = output_dir / "notebooks"
    notebook_dir.mkdir(parents=True)
    source_notebook_dir = tmp_path / "notebooks"
    source_notebook_dir.mkdir()

    notebooks = []
    for stem, title in [("001_first", "First"), ("002_second", "Second")]:
        (notebook_dir / f"{stem}.html").write_text(
            f"<html><head><title>{title}</title></head>"
            f'<body class="jp-Notebook"><h1>{title}</h1>'
            "<p><strong>Figure 1</strong>: A figure.</p></body></html>"
        )
        notebook = nbformat.v4.new_notebook(
            cells=[nbformat.v4.new_markdown_cell(f"# {title}")]
        )
        path = source_notebook_dir / f"{stem}.ipynb"
        nbformat.write(notebook, path)
        notebooks.append(str(path))

    with patch("oceanval.subprocess.run") as run, patch("oceanval._polish_word_report"):
        oceanval._write_offline_report_pages(str(output_dir), notebooks, word=True)

    sources = {
        call.args[0][-1]: call.kwargs["input"].decode() for call in run.call_args_list
    }
    second = sources[str(notebook_dir / "002_second.docx")]
    combined = sources[str(notebook_dir / "oceanval_report.docx")]

    # a page's own Word file mirrors its HTML page; only the combined report
    # is numbered by chapter
    assert "<strong>Figure 1</strong>" in second
    assert "Figure 1.1" not in second
    assert "<strong>Figure 1.1</strong>" in combined
    assert "<strong>Figure 2.1</strong>" in combined


def test_pdf_latex_is_embedded_as_svg():
    body = '<p>Inline \\(x^2\\) and $m$</p><div class="math">\\[E=mc^2\\]</div>'

    rendered = oceanval._render_latex_for_pdf(body)

    assert rendered.count("data:image/svg+xml;base64,") == 3
    assert "\\(" not in rendered
    assert "\\[" not in rendered
    assert "$m$" not in rendered


def test_pdf_latex_is_sized_relative_to_the_text():
    rendered = oceanval._render_latex_for_pdf(
        '<p>Inline $m$</p><div class="math">\\[E=mc^2\\]</div>'
    )

    inline = re.search(r'class="oceanval-math-inline" style="([^"]*)"', rendered)
    display = re.search(r'class="oceanval-math-display" style="([^"]*)"', rendered)

    # sized in em so the maths scales with the body text, and sat on the
    # baseline rather than being forced to a fixed height
    assert re.fullmatch(
        r"width:[\d.]+em;height:[\d.]+em;vertical-align:-[\d.]+em", inline.group(1)
    )
    # no height, so an over-wide equation scales instead of being squashed
    assert re.fullmatch(r"width:[\d.]+em", display.group(1))


def test_pdf_pages_are_numbered_and_carry_a_linked_wordmark():
    style = oceanval._offline_report_pdf_style()
    footer = oceanval._offline_report_pdf_footer()

    assert "@bottom-left{content:'Page ' counter(page) ' of ' counter(pages)" in style
    assert "@bottom-right{content:element(oceanvalfooter)}" in style
    assert ".oceanval-pdf-footer{position:running(oceanvalfooter)}" in style
    assert 'href="https://pmlmodelling.github.io/OceanVal/"' in footer
    assert "data:image/svg+xml;base64," in footer