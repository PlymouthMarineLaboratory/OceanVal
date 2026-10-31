import pandas as pd
import shutil
import glob
import subprocess
import sys
import threading
import warnings
from oceanval.quiet_nc import nc

from oceanval import leftovers
from oceanval.leftovers import STAMP, in_use, is_oceanval_temp

nc.session_info["stamp"] = nc.session_info["stamp"] + STAMP
import copy
from oceanval.matchall import matchup
from oceanval.create_recipes import create_recipes
from oceanval.analysis import analyze
from oceanval.sim_compare import compare_simulations
import dill

from oceanval import depths
from oceanval import prompts
from oceanval import selection
from oceanval import transects
from oceanval.session import session_info
from oceanval.utils import restrict_r_to_conda
restrict_r_to_conda()
import webbrowser
from oceanval.chunkers import add_chunks
import os
import re
import html
import nbformat
import base64
import io
from oceanval.fvcom import fvcom_preprocess
import importlib
import zipfile

from oceanval.parsers import Validator, definitions#, summaries


def reset():
    # add docstring
    """
    Reset the matchup definitions to their default state.
    This function resets the matchup definitions used in oceanval to their default state.
    """
    # reset session_info["short_title"] to empty dict
    session_info["short_title"] = dict()
    definitions.reset()

notebook_dict = dict()
add_point_comparison = definitions.add_point_comparison
add_gridded_comparison = definitions.add_gridded_comparison


# Recent ipykernel versions log a benign-but-alarming warning whenever a kernel starts on the
# default TCP transport ("Kernel is running over TCP without encryption..."), and nbconvert's
# notebook execution has no simple knob that reaches the KernelManager it creates to switch
# transport. Every build here is local-only,
# so there's nothing to actually secure - drop just that line from the kernel-launching build
# subprocess's output rather than changing how it runs.
_KERNEL_WARNING_SNIPPETS = ("Kernel is running over TCP without encryption",)
# seconds _run_filtered waits for the rest of a process's output once it has ended
_OUTPUT_GRACE = 5


def _run_filtered(cmd, check=False, out=None, **kwargs):
    """Like subprocess.run, but drops output lines matching _KERNEL_WARNING_SNIPPETS.
    Streams output live (stdout/stderr merged, since that's the only way to filter
    lines while still showing build/notebook-execution progress as it happens), to
    out, a file, or stdout."""
    out = sys.stdout if out is None else out
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        **kwargs,
    )
    output_lines = []

    def read_output():
        for line in proc.stdout:
            output_lines.append(line)
            if not any(snippet in line for snippet in _KERNEL_WARNING_SNIPPETS):
                out.write(line)
                out.flush()

    # The output is read on a thread and the process waited for, not the end
    # of its output: the pipe only ends once every process holding it has
    # closed it, and nctoolkit's Manager servers in a kernel that died keep it
    # open for ever, which hung the interim report's builder.
    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    proc.wait()
    reader.join(timeout=_OUTPUT_GRACE)
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, output="".join(list(output_lines))
        )
    return proc


def _run_jupytext(args, notebook_glob):
    """Run jupytext over the notebooks matched by notebook_glob, or in it if
    it is a list of paths, quietly.

    jupytext prints a "Reading .../Writing ..." line per notebook, which is noise
    users don't need to see; only surface output if the command actually fails.
    """
    notebooks = glob.glob(notebook_glob) if isinstance(notebook_glob, str) else list(notebook_glob)
    if not notebooks:
        return
    proc = subprocess.run(
        ["jupytext", *args, *notebooks], capture_output=True, text=True
    )
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)


def _execute_notebooks(notebooks, log=None):
    """Run the notebooks, keeping what they show in them, without the
    warnings and other diagnostics. What nbconvert prints goes to log, a
    file, if given."""
    _run_filtered(
        [
            "jupyter",
            "nbconvert",
            "--to",
            "notebook",
            "--execute",
            "--inplace",
            "--allow-errors",
            "--ExecutePreprocessor.timeout=500",
            *notebooks,
        ],
        check=True,
        out=log,
    )
    _remove_diagnostic_outputs(notebooks)


def _notebooks_to_html(notebooks, html_dir, log=None):
    """Write each run notebook as an HTML page in html_dir, without its code.
    What nbconvert prints goes to log, a file, if given."""
    os.makedirs(html_dir, exist_ok=True)
    subprocess.run(
        [
            "jupyter",
            "nbconvert",
            "--to",
            "html",
            "--no-input",
            "--output-dir",
            html_dir,
            *notebooks,
        ],
        check=True,
        **({} if log is None else {"stdout": log, "stderr": subprocess.STDOUT}),
    )


def _build_book(book_dir, validation_links=None, pdf=False, word=False):
    try:
        _build_book_pages(book_dir, validation_links, pdf, word)
    finally:
        # also when the build fails or is interrupted, as the next build
        # removes the trackers that say which temporary files these are
        _remove_notebook_temp_files(book_dir)


def _build_book_pages(book_dir, validation_links=None, pdf=False, word=False):
    """Run the book's notebooks with nbconvert and write each as an HTML page,
    with the navigation to them all (OceanVal needs jupyter-book 2 or later,
    and never runs jupyter-book itself)."""
    notebooks = glob.glob(os.path.join(book_dir, "notebooks", "*.ipynb"))
    notebooks.sort(
        key=lambda notebook: ("summary" in os.path.basename(notebook), notebook)
    )
    output_dir = os.path.join(book_dir, "_build", "html")
    if notebooks:
        _execute_notebooks(notebooks)
        _notebooks_to_html(notebooks, os.path.join(output_dir, "notebooks"))
    os.makedirs(output_dir, exist_ok=True)
    # keep the wordmark alongside the notebook pages (not one level up)
    # so the notebooks/ directory is self-contained and can be copied
    # elsewhere on its own
    os.makedirs(os.path.join(output_dir, "notebooks"), exist_ok=True)
    shutil.copyfile(
        os.path.join(book_dir, "oceanval_wordmark.svg"),
        os.path.join(output_dir, "notebooks", "oceanval_wordmark.svg"),
    )
    _write_offline_report_pages(
        output_dir, notebooks, validation_links=validation_links, pdf=pdf, word=word
    )


def _remove_diagnostic_outputs(notebooks):
    for notebook_path in notebooks:
        notebook = nbformat.read(notebook_path, as_version=4)
        kept_cells = []
        for cell in notebook.cells:
            if cell.cell_type == "code":
                cleaned_outputs = []
                for output in cell.outputs:
                    if output.output_type == "stream":
                        continue
                    elif output.output_type in {"display_data", "update_display_data"}:
                        data = getattr(output, "data", {}) or {}
                        if data and set(data) != {"text/plain"}:
                            cleaned_outputs.append(output)
                    elif output.output_type == "error" and output.ename == "RInterpreterError":
                        cleaned_outputs.append(output)
                    elif output.output_type == "execute_result" and set(output.data) != {"text/plain"}:
                        cleaned_outputs.append(output)
                cell.outputs = cleaned_outputs
            kept_cells.append(cell)
        notebook.cells = kept_cells
        nbformat.write(notebook, notebook_path)


def _offline_report_sections(output_dir, notebooks):
    notebooks_by_stem = {
        os.path.splitext(os.path.basename(notebook))[0]: notebook
        for notebook in notebooks
    }
    toc_path = os.path.normpath(os.path.join(output_dir, "..", "..", "_toc.yml"))
    sections = []
    section = None
    if os.path.exists(toc_path):
        with open(toc_path, "r") as toc:
            for line in toc:
                if line.startswith("- caption: "):
                    section = [line.removeprefix("- caption: ").strip(), []]
                    sections.append(section)
                elif section is not None and "file: notebooks/" in line:
                    stem = os.path.splitext(line.split("file: notebooks/", 1)[1].strip())[0]
                    if stem in notebooks_by_stem:
                        section[1].append(notebooks_by_stem.pop(stem))

    if notebooks_by_stem:
        # no caption: these are ungrouped (e.g. compare()'s report, which has
        # no named sections at all), so leave the heading off rather than
        # showing a redundant catch-all title above the only section present
        sections.append((None, sorted(notebooks_by_stem.values())))
    return sections


def _offline_report_title(notebook_path):
    notebook = nbformat.read(notebook_path, as_version=4)
    for cell in notebook.cells:
        if cell.cell_type != "markdown":
            continue
        for line in cell.source.splitlines():
            if line.startswith("# "):
                return line.removeprefix("# ").strip()
    stem = os.path.splitext(os.path.basename(notebook_path))[0]
    return re.sub(r"^\d+_", "", stem).replace("_", " ").title()


def _zip_offline_report(build_html_dir, zip_path):
    """Zip up a built offline report (the HTML pages, wordmark, and any PDFs -
    all of which live together in build_html_dir/notebooks) into a single
    self-contained archive with no subfolders, so the report can be copied
    elsewhere and viewed by just extracting and opening it."""
    if os.path.exists(zip_path):
        os.remove(zip_path)
    pages_dir = os.path.join(build_html_dir, "notebooks")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(os.listdir(pages_dir)):
            zf.write(os.path.join(pages_dir, name), os.path.join("oceanval_report", name))


def _summary_report_page(build_html_dir):
    """Path to the full-domain summary page of a built offline report (there is no index.html to land on)."""
    matches = sorted(glob.glob(os.path.join(build_html_dir, "notebooks", "*_summary.html")))
    if not matches:
        raise FileNotFoundError(
            f"No summary report page found under {build_html_dir}/notebooks"
        )
    return matches[-1]


def _offline_report_navigation(
    output_dir, notebooks, pdf_href, validation_links=None,
    wordmark_path=None, word_href=None,
):
    sections = []
    for title, section_notebooks in _offline_report_sections(output_dir, notebooks):
        items = []
        for notebook in section_notebooks:
            stem = os.path.splitext(os.path.basename(notebook))[0]
            label = _offline_report_title(notebook)
            items.append(
                f'<li><a href="{html.escape(stem)}.html">'
                f"{html.escape(label)}</a></li>"
            )
        if items:
            heading = f"<h2>{html.escape(title)}</h2>" if title else ""
            sections.append(f"<section>{heading}<ul>{''.join(items)}</ul></section>")

    validation_block = ""
    if validation_links:
        links = "".join(
            f'<a class="oceanval-validation-link" href="{html.escape(href)}">{html.escape(label)}</a>'
            for label, href in validation_links
        )
        validation_block = (
            '<div class="oceanval-validation-links">'
            '<h2 class="oceanval-validation-heading">REPORTS COMPARED</h2>'
            f"{links}</div>"
        )

    wordmark_block = ""
    if wordmark_path:
        wordmark_block = (
            '<a href="https://PlymouthMarineLaboratory.github.io/OceanVal/" class="oceanval-wordmark-link" '
            'target="_blank" rel="noopener">'
            '<span class="oceanval-wordmark-label">Produced by</span>'
            f'<img class="oceanval-wordmark" src="{wordmark_path}" alt="oceanval">'
            '</a>'
        )

    pdf_button = (
        f'<a href="{pdf_href}" class="oceanval-viewpdf-btn">View all as pdf</a>'
        if pdf_href else ""
    )

    word_button = (
        f'<a href="{word_href}" class="oceanval-word-btn" download>Download as Word</a>'
        if word_href else ""
    )

    return (
        "<aside class=\"oceanval-sidebar\">"
        f"<div class=\"oceanval-nav-sections\">{''.join(sections)}</div>"
        f"{pdf_button}"
        f"{word_button}"
        f"{validation_block}"
        "</aside>"
        f"{wordmark_block}"
    )


def _offline_report_style():
    return (
        "<style>body{margin:0;color:#203047;background:#fff;font-family:Georgia,'Times New Roman',serif}"
        ".oceanval-sidebar{position:fixed!important;top:0;bottom:auto;left:0;width:300px;height:100vh;"
        "max-height:100vh;overflow:hidden;background:#0f7c7c;border-right:1px solid #0a5f5f;padding:28px 24px;"
        "z-index:10;color:#fff;display:flex;flex-direction:column;box-sizing:border-box}"
        ".oceanval-nav-sections{flex:1 1 auto;overflow-y:auto;min-height:0;padding-bottom:16px}"
        ".oceanval-viewpdf-btn,.oceanval-word-btn{display:block;flex:0 0 auto;margin-top:12px;background:#fff;"
        "border-radius:6px;padding:10px 12px;font-family:Arial,sans-serif;font-size:13px;font-weight:700;"
        "text-align:center;text-decoration:none;box-sizing:border-box}"
        ".oceanval-viewpdf-btn:hover,.oceanval-word-btn:hover{background:rgba(255,255,255,0.85)}"
        ".oceanval-validation-links{flex:0 0 auto;border-top:1px solid rgba(255,255,255,0.4);"
        "margin-top:12px;padding-top:12px;display:flex;flex-direction:column;gap:8px}"
        ".oceanval-validation-heading{margin:0 0 4px!important}"
        ".oceanval-validation-link{display:block;background:#fff;color:#0f7c7c;border-radius:6px;"
        "padding:10px 12px;font-family:Arial,sans-serif;font-size:13px;font-weight:700;"
        "text-align:center;text-decoration:none;box-sizing:border-box}"
        ".oceanval-validation-link:hover{background:rgba(255,255,255,0.85)}"
        ".oceanval-sidebar h2{font-family:Arial,sans-serif;font-size:14px;font-weight:700;letter-spacing:0;"
        "margin:25px 0 10px;text-transform:uppercase;color:#fff}.oceanval-sidebar ul{list-style:none;"
        "margin:0;padding:0}.oceanval-sidebar li{margin:0}.oceanval-sidebar a{color:#fff;text-decoration:none}"
        ".oceanval-sidebar li a{display:block;border-left:3px solid transparent;padding:8px 9px;"
        "font-family:Arial,sans-serif;font-size:14px;line-height:1.35}.oceanval-sidebar li a:hover"
        "{background:rgba(255,255,255,0.16);border-left-color:#fff}"
        ".oceanval-sidebar a.oceanval-viewpdf-btn,.oceanval-sidebar a.oceanval-word-btn{color:#0f7c7c}"
        ".oceanval-sidebar a.oceanval-validation-link{color:#0f7c7c}"
        ".jp-Notebook{margin-left:340px!important;margin-right:300px!important;font-size:1.15rem}"
        ".jp-Notebook p,.jp-Notebook li,.jp-Notebook dd{margin-top:0;margin-bottom:0.9em}"
        ".jp-Notebook table{margin-left:auto!important;margin-right:auto!important}"
        ".jp-Notebook img,.jp-Notebook figure{display:block;margin-left:auto;margin-right:auto}"
        ".jp-Notebook figcaption{text-align:center}"
        ".oceanval-page-downloads{position:fixed;top:24px;right:24px;z-index:20;width:220px;"
        "display:flex;flex-direction:column;gap:8px}"
        ".oceanval-download-btn{width:100%;box-sizing:border-box;"
        "background:#0f7c7c;color:#fff;border:1px solid #0a5f5f;border-radius:6px;padding:10px 14px;"
        "font-family:Arial,sans-serif;font-size:13px;font-weight:700;cursor:pointer;text-align:center;"
        "text-decoration:none;display:block}"
        ".oceanval-download-btn:hover{background:#0a5f5f}"
        ".oceanval-wordmark-link{position:fixed;right:24px;bottom:20px;z-index:15;font-family:Arial,sans-serif;font-size:13px;color:#0e3a45;text-decoration:none}"
        ".oceanval-wordmark-label{display:block;margin-bottom:4px}"
        ".oceanval-wordmark{width:180px;display:block}"
        ".oceanval-wordmark-link:hover{color:#0f7c7c}"
        ".oceanval-wordmark-link:hover .oceanval-wordmark{opacity:0.85}"
        "@media(max-width:720px){.oceanval-sidebar{position:static!important;width:auto;height:auto;max-height:none;padding:20px;overflow:visible}.oceanval-nav-sections{overflow:visible;padding-bottom:0}"
        ".jp-Notebook{margin-left:auto!important;margin-right:0!important}"
        ".oceanval-page-downloads{position:static;width:auto;margin:16px 0 0 20px;align-items:flex-start}"
        ".oceanval-download-btn{width:auto}.oceanval-wordmark-link{position:static;margin:16px 0 0 20px}.oceanval-wordmark{width:132px}}</style>"
    )


def _offline_report_sidebar_script():
    return (
        "<script>(function(){function restore(){var nav=document.querySelector("
        "'.oceanval-nav-sections');if(!nav){return;}var key='oceanval-sidebar-scroll';"
        "var saved=sessionStorage.getItem(key);if(saved!==null){nav.scrollTop=Number(saved);}"
        "nav.addEventListener('scroll',function(){sessionStorage.setItem(key,nav.scrollTop);});}"
        "if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',restore);}"
        "else{restore();}})();</script>"
    )


def _offline_report_pdf_style():
    return (
        "<style>@page{size:A4;margin:18mm 16mm 20mm;"
        "@bottom-left{content:'Page ' counter(page) ' of ' counter(pages);"
        "font-family:Georgia,'Times New Roman',serif;font-size:8.5pt;color:#6b7a8d}"
        "@bottom-right{content:element(oceanvalfooter)}}"
        "body{font-family:Georgia,'Times New Roman',serif;color:#203047;"
        "margin:0;line-height:1.55;font-size:12.5pt}"
        "h1{font-size:20pt;margin:0 0 12pt}h2{font-size:15pt;margin:18pt 0 8pt}"
        "h3{font-size:12.5pt;margin:14pt 0 6pt}"
        "p{margin:0 0 8pt}"
        "img{max-width:100%;display:block;margin:8pt auto}"
        "table{border-collapse:collapse;width:100%;margin:10pt auto;"
        "page-break-inside:avoid;break-inside:avoid}"
        "tr,td,th{page-break-inside:avoid;break-inside:avoid}"
        "td,th{border:1px solid #ccc;padding:5px 8px;font-size:9.5pt}"
        "pre{white-space:pre-wrap;word-break:break-word;font-size:9.5pt}"
        # width, height and baseline offset are set per expression, in em, by
        # _render_latex_for_pdf
        ".oceanval-math-inline{display:inline;margin:0}"
        ".oceanval-math-display{display:block;height:auto;max-width:100%;margin:12pt auto}"
        ".oceanval-pdf-footer{position:running(oceanvalfooter)}"
        ".oceanval-pdf-footer img{height:5.5mm;margin:0;display:block}"
        ".headerlink,.anchor-link{display:none}</style>"
    )


def _offline_report_pdf_footer():
    """Wordmark linking to the OceanVal site, repeated in each PDF page's footer."""
    wordmark = importlib.resources.files(__name__).joinpath(
        "data/oceanval_wordmark.svg"
    ).read_bytes()
    encoded = base64.b64encode(wordmark).decode("ascii")
    return (
        '<div class="oceanval-pdf-footer">'
        '<a href="https://PlymouthMarineLaboratory.github.io/OceanVal/">'
        f'<img src="data:image/svg+xml;base64,{encoded}" alt="OceanVal">'
        "</a></div>"
    )


def _render_latex_for_pdf(body_content):
    patterns = [
        (r'<span class="math inline">\\\((.*?)\\\)</span>', False),
        (r'<div class="math">\\\[(.*?)\\\]</div>', True),
        (r'\\\[(.*?)\\\]', True),
        (r'\\\((.*?)\\\)', False),
        (r'\$\$(.*?)\$\$', True),
        (r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)', False),
    ]
    if not any(re.search(pattern, body_content, flags=re.S) for pattern, _ in patterns):
        return body_content

    from matplotlib.font_manager import FontProperties
    from matplotlib.mathtext import MathTextParser, math_to_image

    # matplotlib reports the size of a rendered expression in points for a
    # given font size, so rendering at a known size lets those be turned into
    # em - the maths then scales with the surrounding text instead of being
    # forced to a fixed height, which blew up short expressions like "m" and
    # squashed tall ones like a fraction. stix is a Times-like maths font, so
    # equations sit better alongside the report's serif body text than
    # matplotlib's sans-serif default.
    reference_size = 12.0
    prop = FontProperties(size=reference_size, math_fontfamily="stix")
    parser = MathTextParser("path")

    def replace_math(match, display):
        expression = match.group(1).strip()
        buffer = io.BytesIO()
        try:
            width, height, depth = parser.parse(f"${expression}$", dpi=72, prop=prop)[:3]
            math_to_image(
                f"${expression}$",
                buffer,
                prop=prop,
                dpi=200,
                format="svg",
                color="#203047",
            )
        except Exception:
            return match.group(0)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        if display:
            # height is left to the aspect ratio so that an equation too wide
            # for the page scales down rather than being squashed
            class_name = "oceanval-math-display"
            style = f"width:{width / reference_size:.3f}em"
        else:
            class_name = "oceanval-math-inline"
            style = (
                f"width:{width / reference_size:.3f}em;"
                f"height:{height / reference_size:.3f}em;"
                # sits the image on the text baseline, allowing for descenders
                f"vertical-align:-{depth / reference_size:.3f}em"
            )
        return (
            f'<img class="{class_name}" style="{style}" '
            f'src="data:image/svg+xml;base64,{encoded}" alt="{html.escape(expression)}">'
        )

    for pattern, display in patterns:
        body_content = re.sub(
            pattern,
            lambda match, display=display: replace_math(match, display),
            body_content,
            flags=re.S,
        )
    return body_content


def _chapter_numbered_body(body, chapter):
    """Number one notebook's title, figures and tables as a chapter.

    Each notebook numbers its own figures from 1, which collides once they
    are gathered into a single document, so in the combined reports
    "Figure 1" of chapter 3 becomes "Figure 3.1". Cross-references in the
    prose ("Figure 2 shows...") are written the same way, so the one
    substitution covers both them and the captions.
    """
    body = re.sub(
        r"\b(Figure|Table)(\s+)(\d+)\b",
        lambda match: f"{match.group(1)}{match.group(2)}{chapter}.{match.group(3)}",
        body,
    )
    return re.sub(r"(<h1[^>]*>)", rf"\g<1>{chapter}. ", body, count=1)


def _combined_report_chapters(output_dir, notebooks, notebook_bodies):
    """Notebook bodies, in reading order, each numbered as a chapter."""
    chapter = 0
    for _, section_notebooks in _offline_report_sections(output_dir, notebooks):
        for notebook in section_notebooks:
            body = notebook_bodies.get(notebook)
            if body is None:
                continue
            chapter += 1
            yield _chapter_numbered_body(body, chapter)


def _combined_report_pdf_source(output_dir, notebooks, notebook_bodies, pdf_style):
    # concatenates each page's PDF body in the same order as the sidebar navigation
    parts = []
    for body in _combined_report_chapters(output_dir, notebooks, notebook_bodies):
        break_style = "" if not parts else "page-break-before:always;break-before:page;"
        parts.append(f'<section style="{break_style}">{body}</section>')
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>OceanVal validation report</title>{pdf_style}</head><body>"
        f"{_offline_report_pdf_footer()}{''.join(parts)}</body></html>"
    )


def _word_report_source(body):
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>OceanVal validation report</title></head><body>{body}</body></html>"
    )


def _render_offline_report_word(documents, resource_path):
    """Convert report pages to Word documents, using pandoc.

    documents: iterable of (html_source, docx_path, title, chapter_breaks)
    tuples, where the title is the document heading pandoc adds, or None
    for a page that already opens with its own heading, and chapter_breaks
    says whether the document runs several chapters together.

    The sources still hold their LaTeX, so the maths becomes real Word
    equations rather than pictures of equations.
    """
    for source, docx_path, title, chapter_breaks in documents:
        command = [
            "pandoc",
            "--from",
            "html+tex_math_dollars",
            "--to",
            "docx",
            "--resource-path",
            resource_path,
            "--output",
            docx_path,
        ]
        if title:
            command[-2:-2] = ["--metadata", f"title={title}"]
        try:
            subprocess.run(
                command, input=source.encode(), check=True, capture_output=True
            )
        except FileNotFoundError:
            warnings.warn(
                "pandoc is not installed, so the Word report was not generated. "
                "Install it with 'conda install -c conda-forge pandoc'."
            )
            return
        except subprocess.CalledProcessError as error:
            warnings.warn(
                f"Could not generate {os.path.basename(docx_path)} "
                f"({error.stderr.decode().strip()})."
            )
            continue

        _polish_word_report(docx_path, chapter_breaks=chapter_breaks)


def _word_page_number_field(paragraph, instruction, placeholder):
    # Word computes PAGE/NUMPAGES when the document opens; the placeholder is
    # what readers that don't evaluate fields fall back to. Each part of the
    # field needs its own run, or readers style the computed number oddly.
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    def add_run(element):
        paragraph.add_run()._r.append(element)

    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction_element = OxmlElement("w:instrText")
    instruction_element.set(qn("xml:space"), "preserve")
    instruction_element.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")

    add_run(begin)
    add_run(instruction_element)
    add_run(separate)
    paragraph.add_run(placeholder)
    add_run(end)


# where a <w:tblBorders>/<w:tcBorders> element has to sit among its siblings
# for the document to stay schema valid
_WORD_TBL_BORDER_SUCCESSORS = (
    "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook",
    "w:tblCaption", "w:tblDescription", "w:tblPrChange",
)
_WORD_TBL_CELL_MAR_SUCCESSORS = (
    "w:tblLook", "w:tblCaption", "w:tblDescription", "w:tblPrChange",
)
_WORD_TC_BORDER_SUCCESSORS = (
    "w:shd", "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText",
    "w:vAlign", "w:hideMark", "w:headers", "w:cellIns", "w:cellDel",
    "w:cellMerge", "w:tcPrChange",
)


def _word_set_borders(properties, tag, successors, edges):
    """Put a <w:tblBorders> or <w:tcBorders> on a properties element.

    edges: (name, visible) pairs, which must be given in schema order.
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    for existing in properties.findall(qn(tag)):
        properties.remove(existing)

    borders = OxmlElement(tag)
    for edge, visible in edges:
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single" if visible else "none")
        element.set(qn("w:sz"), "8" if visible else "0")
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), "000000" if visible else "auto")
        borders.append(element)
    properties.insert_element_before(borders, *successors)


def _word_header_rows(table):
    """The rows pandoc marked as the table's repeating header."""
    from docx.oxml.ns import qn

    return [
        row
        for row in table.rows
        if row._tr.find(f"{qn('w:trPr')}/{qn('w:tblHeader')}") is not None
    ]


def _style_word_table(table):
    """Rule the table like a book table, and embolden its header.

    pandoc's own table style draws a single line under the header row,
    which leaves the table floating in the prose. The rules above the
    header and below the last row are what close it off, and no line is
    drawn between the body rows or between the columns.
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    # pandoc gives every column the same width, which wraps a long label
    # while a column of short numbers sits half empty; autofit only takes
    # effect once the widths it wrote stop being asked for
    table.autofit = True
    for column in table._tbl.tblGrid.gridCol_lst:
        column.set(qn("w:w"), "0")

    tbl_pr = table._tbl.tblPr
    _word_set_borders(
        tbl_pr,
        "w:tblBorders",
        _WORD_TBL_BORDER_SUCCESSORS,
        [
            ("top", True),
            ("left", False),
            ("bottom", True),
            ("right", False),
            ("insideH", False),
            ("insideV", False),
        ],
    )

    # without a little room around the text, the rules crowd it
    for existing in tbl_pr.findall(qn("w:tblCellMar")):
        tbl_pr.remove(existing)
    cell_margins = OxmlElement("w:tblCellMar")
    for edge, width in (("top", 60), ("left", 108), ("bottom", 60), ("right", 108)):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:w"), str(width))
        element.set(qn("w:type"), "dxa")
        cell_margins.append(element)
    tbl_pr.insert_element_before(cell_margins, *_WORD_TBL_CELL_MAR_SUCCESSORS)

    header_rows = _word_header_rows(table)
    if not header_rows:
        return

    for row in header_rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.bold = True

    # a cell's own borders win over the table's, and pandoc's table style
    # rules under the first row whether or not more header rows follow, so
    # every header row has to say where its lines go
    for index, row in enumerate(header_rows):
        for cell in row.cells:
            _word_set_borders(
                cell._tc.get_or_add_tcPr(),
                "w:tcBorders",
                _WORD_TC_BORDER_SUCCESSORS,
                [
                    ("top", index == 0),
                    ("left", False),
                    ("bottom", index == len(header_rows) - 1),
                    ("right", False),
                    ("insideH", False),
                    ("insideV", False),
                ],
            )


# the hidden bookmarks Word makes itself. They cannot be told from pandoc's by a
# leading underscore: from pandoc 3.12 every heading's bookmark has one
_WORD_BOOKMARKS = ("_GoBack", "_Toc", "_Ref", "_Hlk", "_Hlt")


def _strip_word_bookmarks(document):
    """Drop the bookmarks pandoc writes for every heading id.

    Word draws a marker in the margin for each of them, and the report has
    no use for them; any bookmark an internal link points at is kept, so
    that the links still work, as are Word's own hidden bookmarks.
    """
    from docx.oxml.ns import qn

    body = document.element.body
    anchors = {
        link.get(qn("w:anchor"))
        for link in body.iter(qn("w:hyperlink"))
        if link.get(qn("w:anchor"))
    }

    kept = set()
    for start in list(body.iter(qn("w:bookmarkStart"))):
        name = start.get(qn("w:name")) or ""
        if name in anchors or name.startswith(_WORD_BOOKMARKS):
            kept.add(start.get(qn("w:id")))
            continue
        start.getparent().remove(start)
    for end in list(body.iter(qn("w:bookmarkEnd"))):
        if end.get(qn("w:id")) not in kept:
            end.getparent().remove(end)


def _polish_word_report(docx_path, chapter_breaks=False):
    """Centre the figures and tables, and add the OceanVal footer.

    pandoc gives images and prose the same paragraph style, so the figures
    can only be centred after the fact, and it writes no footer at all.

    chapter_breaks starts every chapter of the combined report on a fresh
    page, as the PDF report does.
    """
    try:
        import docx
        from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
        from docx.shared import Mm, Pt, RGBColor
    except ImportError:
        warnings.warn(
            "python-docx is not installed, so the Word report was left "
            "unformatted. Install it with 'pip install python-docx'."
        )
        return

    document = docx.Document(docx_path)

    for paragraph_element in document.element.body.iter(qn("w:p")):
        if paragraph_element.find(f".//{qn('w:drawing')}") is not None:
            docx.text.paragraph.Paragraph(
                paragraph_element, document
            ).alignment = WD_ALIGN_PARAGRAPH.CENTER

    for table in document.tables:
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        _style_word_table(table)

    _strip_word_bookmarks(document)

    if chapter_breaks:
        chapters = [
            paragraph
            for paragraph in document.paragraphs
            if paragraph.style is not None and paragraph.style.name == "Heading 1"
        ]
        # the first chapter carries on from the report title rather than
        # leaving a page holding nothing but that title
        for chapter in chapters[1:]:
            chapter.paragraph_format.page_break_before = True

    section = document.sections[0]
    # pandoc leaves the page setup empty, so Word would fall back to whatever
    # its locale default is - pin it to A4, to match the PDF report
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    section.top_margin = Mm(18)
    section.bottom_margin = Mm(20)
    section.left_margin = Mm(16)
    section.right_margin = Mm(16)

    footer = section.footer
    footer.is_linked_to_previous = False
    for paragraph in list(footer.paragraphs):
        paragraph._element.getparent().remove(paragraph._element)

    layout = footer.add_table(
        rows=1, cols=2, width=section.page_width - section.left_margin - section.right_margin
    )
    wordmark_cell, page_cell = layout.rows[0].cells

    # the wordmark stands on its own in the Word report - no caption above it
    # and no link off to the docs site
    wordmark = wordmark_cell.paragraphs[0]
    wordmark.add_run().add_picture(
        io.BytesIO(
            importlib.resources.files(__name__)
            .joinpath("data/oceanval_wordmark.png")
            .read_bytes()
        ),
        width=Mm(32),
    )

    page_cell.vertical_alignment = WD_ALIGN_VERTICAL.BOTTOM
    page_paragraph = page_cell.paragraphs[0]
    page_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    page_paragraph.add_run("Page ")
    _word_page_number_field(page_paragraph, " PAGE ", "1")
    page_paragraph.add_run(" of ")
    _word_page_number_field(page_paragraph, " NUMPAGES ", "1")
    for run in page_paragraph.runs:
        run.font.size = Pt(8.5)
        run.font.name = "Arial"
        run.font.color.rgb = RGBColor(0x6B, 0x7A, 0x8D)

    document.save(docx_path)


def _render_offline_report_pdfs(pages):
    # pages: iterable of (html_string, pdf_path, base_url) tuples
    try:
        from weasyprint import HTML
    except ImportError:
        warnings.warn(
            "weasyprint is not installed, so PDF downloads were not generated. "
            "Install it with 'pip install weasyprint'."
        )
        return
    for html_string, pdf_path, base_url in pages:
        try:
            HTML(string=html_string, base_url=base_url).write_pdf(pdf_path)
        except Exception as error:
            warnings.warn(
                f"Could not generate the PDF download for {os.path.basename(pdf_path)} ({error})."
            )


def _write_atomically(path, text):
    """Write text to path through a temporary file beside it, so that a
    browser reading the page never finds it half written."""
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w") as file:
        file.write(text)
    os.replace(temporary, path)


def _write_offline_report_pages(
    output_dir, notebooks, validation_links=None, pdf=False, word=False,
    raw_dir=None, banner=None,
):
    """Add the navigation (and the download buttons) to each notebook's HTML
    page in output_dir/notebooks.

    The pages are read from raw_dir, if given, rather than rewritten where
    they are, so that the navigation can be put in again as pages are
    added, as it is for the interim report (see oceanval.live). banner, an
    HTML snippet, goes at the top of every page.
    """
    validation_links = validation_links or []
    page_validation_links = [
        (label, os.path.relpath(target, os.path.join(output_dir, "notebooks")))
        for label, target in validation_links
    ]
    style = _offline_report_style()
    pdf_style = _offline_report_pdf_style() if pdf else None
    page_navigation = _offline_report_navigation(
        output_dir, notebooks, "oceanval_report.pdf" if pdf else None,
        validation_links=page_validation_links, wordmark_path="oceanval_wordmark.svg",
        word_href="oceanval_report.docx" if word else None,
    )

    pdf_jobs = []
    word_jobs = []
    page_updates = []
    notebook_bodies = {}
    word_bodies = {}
    for notebook in notebooks:
        stem = os.path.splitext(os.path.basename(notebook))[0]
        page = os.path.join(output_dir, "notebooks", f"{stem}.html")
        source = page if raw_dir is None else os.path.join(raw_dir, f"{stem}.html")
        with open(source, "r") as report_page:
            raw_html = report_page.read()

        page_downloads = []
        if pdf or word:
            title_match = re.search(r"<title>(.*?)</title>", raw_html, re.S)
            title = title_match.group(1).strip() if title_match else stem
            body_match = re.search(r"<body[^>]*>(.*)</body>", raw_html, re.S)
            body_content = body_match.group(1) if body_match else raw_html
            # strip heading anchor links (e.g. the clickable "¶") from the export
            body_content = re.sub(
                r'<a[^>]*class="[^"]*(?:headerlink|anchor-link)[^"]*"[^>]*>.*?</a>',
                "",
                body_content,
                flags=re.S,
            )

        if word:
            # left as LaTeX, which pandoc turns into real Word equations
            word_bodies[notebook] = body_content
            # the page's own Word file mirrors its HTML page, so it is not
            # chapter numbered - only the combined report is
            word_jobs.append(
                (
                    _word_report_source(body_content),
                    os.path.join(output_dir, "notebooks", f"{stem}.docx"),
                    None,
                    False,
                )
            )
            page_downloads.append(
                f'<a class="oceanval-download-btn" href="{stem}.docx" download>'
                "Download page as Word</a>"
            )

        if pdf:
            body_content = _render_latex_for_pdf(body_content)
            notebook_bodies[notebook] = body_content
            pdf_source = (
                f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                f"<title>{html.escape(title)}</title>{pdf_style}</head>"
                f"<body>{_offline_report_pdf_footer()}{body_content}</body></html>"
            )
            pdf_path = os.path.join(output_dir, "notebooks", f"{stem}.pdf")
            pdf_jobs.append((pdf_source, pdf_path, os.path.dirname(page)))

            page_downloads.insert(
                0,
                f'<a class="oceanval-download-btn" href="{stem}.pdf" download>View page as pdf</a>',
            )

        downloads = (
            f'<div class="oceanval-page-downloads">{"".join(page_downloads)}</div>'
            if page_downloads
            else ""
        )
        page_html = re.sub(
            r"(<body[^>]*>)",
            lambda match: f"{match.group(1)}{page_navigation}{downloads}{banner or ''}",
            raw_html,
            count=1,
        )
        page_html = page_html.replace(
            "</head>", f"{style}{_offline_report_sidebar_script()}</head>", 1
        )
        page_updates.append((page, page_html))

    if pdf and notebook_bodies:
        combined_source = _combined_report_pdf_source(
            output_dir, notebooks, notebook_bodies, pdf_style
        )
        combined_path = os.path.join(output_dir, "notebooks", "oceanval_report.pdf")
        pdf_jobs.append((combined_source, combined_path, os.path.join(output_dir, "notebooks")))

    if pdf:
        _render_offline_report_pdfs(pdf_jobs)

    if word and word_bodies:
        combined_body = "".join(
            _combined_report_chapters(output_dir, notebooks, word_bodies)
        )
        word_jobs.append(
            (
                _word_report_source(combined_body),
                os.path.join(output_dir, "notebooks", "oceanval_report.docx"),
                "OceanVal validation report",
                True,
            )
        )

    if word_jobs:
        _render_offline_report_word(word_jobs, os.path.join(output_dir, "notebooks"))

    for page, page_html in page_updates:
        _write_atomically(page, page_html)


def fix_toc(concise=True, data_dir=None, out_dir=None):
    short_titles = dill.load(
        open(f"{data_dir}/oceanval_matchups/short_titles.pkl", "rb")
    )
    paths = glob.glob(f"{out_dir}/oceanval_report/notebooks/*.ipynb")
    variables = dill.load(
        open(f"{data_dir}/oceanval_matchups/variables_matched.pkl", "rb")
    )
    variables.sort()

    vv_dict = dict()
    for vv in variables:
        vv_paths = [os.path.basename(x) for x in paths if f"_{vv}.ip" in x]
        if len(vv_paths) > 0:
            vv_dict[vv] = vv_paths
    # get summary docs
    ss_paths = [os.path.basename(x) for x in paths if "summary" in x]

    out = f"{out_dir}/oceanval_report/_toc.yml"
    myst_out = f"{out_dir}/oceanval_report/myst.yml"

    # write line by line to out
    i_chapter = 1
    with open(out, "w") as f, open(myst_out, "w") as myst:
        # "format: jb-book"
        x = f.write("format: jb-book\n")
        x = f.write("root: intro\n")
        x = f.write("parts:\n")

        myst.write("version: 1\n")
        myst.write("project:\n")
        myst.write("  title: OceanVal validation report\n")
        myst.write("  toc:\n")
        myst.write("    - file: intro.md\n")

        x = f.write(f"- caption: Summaries\n")
        x = f.write("  chapters:\n")

        myst.write("    - title: Summaries\n")
        myst.write("      children:\n")

        x = f.write(f"  - file: notebooks/001_methods.ipynb\n")
        myst.write("        - file: notebooks/001_methods.ipynb\n")

        # open notebook and replace book_chapter with i_chapter

        # open notebook and replace book_chapter with i_chapter
        with open(
            f"{out_dir}/oceanval_report/notebooks/001_methods.ipynb", "r"
        ) as file:
            filedata = file.read()

        # Replace the target string
        filedata = filedata.replace("book_chapter", str(i_chapter))
        filedata = filedata.replace("info_text", "Validation metrics summary")

        with open(
            f"{out_dir}/oceanval_report/notebooks/001_methods.ipynb", "w"
        ) as file:
            file.write(filedata)
        i_chapter += 1
        for ff in ss_paths:
            x = f.write(f"  - file: notebooks/{ff}\n")
            myst.write(f"        - file: notebooks/{ff}\n")
            # open notebook and replace book_chapter with i_chapter
            with open(f"{out_dir}/oceanval_report/notebooks/{ff}", "r") as file:
                filedata = file.read()

            filedata = filedata.replace("book_chapter", str(i_chapter))

            # Replace the target string
            # Write the file out again
            with open(f"{out_dir}/oceanval_report/notebooks/{ff}", "w") as file:
                file.write(filedata)
            i_chapter += 1

        # loop over variables in each vv_dict
        # value is the file in the chapter section
        # key is the variable name, so is the section
        for vv in vv_dict.keys():
            # capitalize if not ph
            vv_out = short_titles[vv]

            x = f.write(f"- caption: {vv_out}\n")
            x = f.write("  chapters:\n")
            myst.write(f"    - title: {vv_out}\n")
            myst.write("      children:\n")
            for ff in vv_dict[vv]:
                x = f.write(f"  - file: notebooks/{ff}\n")
                myst.write(f"        - file: notebooks/{ff}\n")

                # open notebook and replace book_chapter with i_chapter
                with open(f"{out_dir}/oceanval_report/notebooks/{ff}", "r") as file:
                    filedata = file.read()

                # Replace the target string
                filedata = filedata.replace("book_chapter", str(i_chapter))

                # Write the file out again

                with open(f"{out_dir}/oceanval_report/notebooks/{ff}", "w") as file:
                    file.write(filedata)
                i_chapter += 1

        myst.write("site:\n")
        myst.write("  template: book-theme\n")
        myst.write("  options:\n")
        myst.write("    logo: pml_logo.jpg\n")
        myst.write("    folders: true\n")



def _check_region_file(region_file):
    """
    Check a user-supplied regions netCDF and return the number of regions in it.

    Each variable must be a 2D map holding only 1, 0 or missing values. The
    notebooks multiply the matched data by these maps, so any other value
    would silently rescale the model and observations.
    """
    if not os.path.exists(region_file):
        raise ValueError(f"subregions file {region_file} does not exist")
    ds = nc.open_data(region_file, checks=False)
    contents = ds.contents
    for _, row in contents.iterrows():
        if (row.nlevels or 1) > 1 or (row.ntimes or 1) > 1:
            raise ValueError(
                f"subregions file variable {row.variable} must be a 2D map, with a single time step and vertical level"
            )
    ds_xr = ds.to_xarray()
    for variable in contents.variable:
        values = ds_xr[variable]
        if not (values.isin([0, 1]) | values.isnull()).all():
            raise ValueError(
                f"subregions file variable {variable} must only contain 1, 0 or missing values"
            )
    return len(contents)


def _check_matchups(data_dir):
    """
    Check that data_dir holds the output of a finished matchup run.

    validate builds the report from these files, so without this check it
    fails part way through with a missing-file error from fix_toc.
    """
    matchup_dir = os.path.join(data_dir, "oceanval_matchups")
    point_paths = glob.glob(f"{matchup_dir}/point/**/**/**/**.csv")
    gridded_paths = glob.glob(f"{matchup_dir}/gridded/**/**.nc")
    if len(point_paths) == 0 and len(gridded_paths) == 0:
        raise ValueError(
            f"No matchups found in {matchup_dir}. Run oceanval.matchup() first, "
            "or set data_dir to the directory you ran it from"
        )
    for ff in ["short_titles.pkl", "variables_matched.pkl"]:
        if not os.path.exists(os.path.join(matchup_dir, ff)):
            raise ValueError(
                f"{matchup_dir} is missing {ff}, so oceanval.matchup() did not finish. "
                "Run it again before calling validate"
            )


def _check_report_options(
    lon_lim=None, lat_lim=None, concise=True, fixed_scale=False, subregions=None,
    region=None,
):
    """
    Check validate()'s report options, which matchup's live_validation takes
    too (see oceanval.live).

    Returns (subregions, region_file, n_regions): subregions, with the
    deprecated region in its place if that was given instead, the full path
    of a regions file of the user's own, or None, and how many regions the
    file has, or None.
    """
    # if lon_lim  is not None, make sure it's a list
    if lon_lim is not None:
        if isinstance(lon_lim, list) == False:
            raise ValueError("lon_lim must be a list")
        else:
            if len(lon_lim) != 2:
                raise ValueError("lon_lim must be a list of length 2")
    if lat_lim is not None:
        if isinstance(lat_lim, list) == False:
            raise ValueError("lat_lim must be a list")
        else:
            if len(lat_lim) != 2:
                raise ValueError("lat_lim must be a list of length 2")

    # concise must be boolean
    if isinstance(concise, bool) == False:
        raise ValueError("concise must be a boolean")

    # checked fixed_scale is bool
    if isinstance(fixed_scale, bool) == False:
        raise ValueError("fixed_scale must be a boolean")

    if region is not None:
        if subregions is not None:
            raise ValueError("give subregions or region, not both")
        warnings.warn("region is deprecated, use subregions instead", FutureWarning)
        subregions = region
    region_file = None
    n_regions = None
    if subregions is not None and subregions not in ["nwes", "global"]:
        if not str(subregions).endswith(".nc"):
            raise ValueError("subregions must be 'nwes', 'global' or a path to a .nc file")
        region_file = os.path.abspath(os.path.expanduser(subregions))
        n_regions = _check_region_file(region_file)
    return subregions, region_file, n_regions


def _point_notebook_text(source, layer, variable, title, n_levels, data_dir, out_dir):
    """point_template.ipynb, filled in for the matchup of variable with the
    point observations of source, at layer."""
    file1 = importlib.resources.files(__name__).joinpath("data/point_template.ipynb")
    with open(file1, "r") as file:
        filedata = file.read()

    if layer in ["all", "surface"]:
        filedata = filedata.replace("chunk_point_surface", "chunk_point")
    else:
        filedata = filedata.replace("chunk_point_surface", "")
    if layer in ["bottom", "all"]:
        if variable.lower() not in ["pco2"]:
            filedata = filedata.replace("chunk_point_bottom", "chunk_point")
        else:
            filedata = filedata.replace("chunk_point_bottom", "")
    else:
        filedata = filedata.replace("chunk_point_bottom", "")

    # Replace the target string
    filedata = filedata.replace("point_variable", variable)
    if layer != "all":
        if n_levels > 1:
            filedata = filedata.replace(
                "Validation of point_layer", f"Validation of {layer}"
            )
        else:
            filedata = filedata.replace("Validation of point_layer", f"Validation of ")
    else:
        filedata = filedata.replace("Validation of point_layer", f"Validation of ")

    filedata = filedata.replace("point_layer", layer)
    filedata = filedata.replace("point_obs_source", source)
    filedata = filedata.replace("template_title", title)
    filedata = filedata.replace("data_dir_value", data_dir)
    filedata = filedata.replace("out_dir_value", out_dir)
    return filedata


def _gridded_seasonal(data_dir, variable, source):
    """Whether the gridded matchup of variable with source has more than one
    month, for the notebook's seasonal section."""
    ff_nc = glob.glob(
        f"{data_dir}/oceanval_matchups/gridded/{variable}/{source}_*surface*.nc"
    )[0]
    ds = nc.open_data(ff_nc, checks=False)
    try:
        n_months = len(ds.months)
    except:
        n_months = 12
    return n_months > 1


def _gridded_notebook_text(
    source, variable, title, seasonal, data_dir, subregions, region_file, n_regions,
    transect=None,
):
    """gridded_template.ipynb, filled in for the matchup of variable with the
    gridded observations of source, with the transect section if there is a
    transect."""
    file1 = importlib.resources.files(__name__).joinpath("data/gridded_template.ipynb")
    with open(file1, "r") as file:
        filedata = file.read()

    # Replace the target string
    filedata = filedata.replace("template_variable", variable)
    filedata = filedata.replace("template_title", title)
    filedata = filedata.replace("data_dir_value", data_dir)
    filedata = filedata.replace("source_name", source)
    if subregions == "nwes":
        filedata = filedata.replace("zonal_height", "6000")
    elif region_file is not None:
        filedata = filedata.replace(
            "zonal_height", str(max(2000, 350 * (n_regions + 1)))
        )
    else:
        filedata = filedata.replace("zonal_height", "2000")
    # make every letter a capital
    source_capital = source.upper()
    filedata = filedata.replace("source_title", source_capital)
    if seasonal is False:
        filedata = filedata.replace("chunk_seasonal", "")
    if transect is None:
        filedata = filedata.replace("chunk_transect", "")
    if region_file is not None:
        filedata = filedata.replace("sub_regions_value", "custom")
    elif subregions is not None:
        filedata = filedata.replace("sub_regions_value", str(subregions))
    return filedata


def _rewrite_notebook_source(
    path, lon_lim, lat_lim, fixed_scale, concise, test, transect=None, depth_bins=None
):
    """Fill in the report options in a notebook's paired .py:percent file,
    and silence R's warnings in each R cell. depth_bins are as
    depths.check_depth_bins gives them, or None for the default ones."""
    if depth_bins is None:
        depth_bins = depths.DEFAULT_DEPTH_BINS
    depth_bins = repr([list(pair) for pair in depth_bins])
    with open(path, "r") as file:
        filedata = file.read()

    # loop through line by line, and rewrite the original file
    lines = filedata.split("\n")
    new_lines = []
    for line in lines:
        if "%%R" in line:
            new_lines.append(line)
            new_lines.append("options(warn=-1)")
        else:
            new_lines.append(line)
    # loop through all lines in lines and replace the_test_status with True
    for i in range(len(new_lines)):
        new_lines[i] = new_lines[i].replace("latexpagebreak", "")
        if "the_test_status" in new_lines[i]:
            if test:
                new_lines[i] = new_lines[i].replace("the_test_status", "True")
            else:
                new_lines[i] = new_lines[i].replace("the_test_status", "False")
        if '"gam"' in new_lines[i]:
            new_lines[i] = new_lines[i].replace('"gam"', '"lm"')

        new_lines[i] = new_lines[i].replace("the_lon_lim", str(lon_lim))
        new_lines[i] = new_lines[i].replace("the_lat_lim", str(lat_lim))
        new_lines[i] = new_lines[i].replace("the_transect", repr(transect))
        new_lines[i] = new_lines[i].replace("the_depth_bins", depth_bins)
        new_lines[i] = new_lines[i].replace("fixed_scale_value", str(fixed_scale))
        # replace concice_value with concice
        if "concise_value" in new_lines[i]:
            if concise:
                new_lines[i] = new_lines[i].replace("concise_value", "True")
            else:
                new_lines[i] = new_lines[i].replace("concise_value", "False")

    # write the new lines to the file
    with open(path, "w") as file:
        for line in new_lines:
            file.write(line + "\n")


def _fill_notebook_paths(path, data_dir, out_dir, fast_plot=False):
    """Fill in where a notebook reads the matchups from, and writes its
    results to."""
    with open(path, "r") as file:
        filedata = file.read()

    # Replace the target string
    filedata = filedata.replace("fast_plot_value", str(fast_plot))
    filedata = filedata.replace("data_dir_value", data_dir)
    filedata = filedata.replace("out_dir_value", out_dir)

    # Write the file out again
    with open(path, "w") as file:
        file.write(filedata)


def _remove_notebook_temp_files(book_dir):
    """Remove the temporary files the notebooks of a report left, which each
    marked in its notebooks/.trackers."""
    # a tracker is the session's stamp, which some notebooks give a .txt suffix
    stamps = [
        os.path.basename(x).removesuffix(".txt")
        for x in glob.glob(f"{book_dir}/notebooks/.trackers/*")
    ]

    delete = []
    for x in stamps:
        for directory in leftovers.TEMP_DIRS:
            delete += glob.glob(os.path.join(directory, "*" + glob.escape(x) + "*"))

    for ff in delete:
        name = os.path.basename(ff)
        # only files made by nctoolkit in an oceanval session
        if is_oceanval_temp(name) and os.path.isfile(ff):
            os.remove(ff)


def validate(
    lon_lim=None,
    lat_lim=None,
    concise=True,
    fixed_scale=False,
    subregions=None,
    data_dir=".",
    out_dir=".",
    pdf=False,
    word=False,
    zip=False,
    test=False,
    region=None,
    transect=None,
    depth_bins=None,
    variables=None,
    previous=True,
):
    # docstring
    """
    Run the model evaluation for all of the available datasets, and generate a validation report.

    Parameters
    ----------
    lon_lim : list or None
        The longitude limits for the validation. Default is None
    lat_lim : list or None
        The latitude limits for the validation. Default is None
    fixed_scale : bool
        Whether to use a fixed scale for the seasonal plots. Default is False. If True, the minimum and maximum values are capped to cover the 2nd and 98th percentiles of both model and observations.
    subregions : str or None
        The sub-regions used for the regional summaries. Either "nwes" (northwest European Shelf), "global", or a path to your own regions netCDF (.nc) file. The file should have one 2D (lon/lat) variable per region, with 1 for grid cells in the region and missing values elsewhere. Any 0 is set to missing and ignored in regional summaries. Each region is named by its long_name attribute, or its variable name if it has none. Default is None.
    pdf : bool
        Whether to also generate PDF downloads of the report (a per-page PDF and a combined PDF of the whole report). Default is False, since generating them is slow and most users only need the HTML report.
    word : bool
        Whether to also generate Word versions of the report (one per page, plus a combined oceanval_report.docx), with the maths as editable Word equations. Default is False. Requires pandoc.
    zip : bool
        Whether to also bundle the report into a zip archive (oceanval_report.zip) in the output directory, containing just the files needed to view the complete report (the HTML pages, plus the PDFs and Word file if those were generated). Default is False.
    test : bool
        Default is False. Ignore, unless you are testing oceanval.
    region : str or None
        Deprecated, use subregions instead.
    transect : dict or None
        A transect to validate the gridded matchups along, as a dict of its start and end, each [lon, lat], e.g. {"start": [-30, 0], "end": [-30, 65]}. It must run north-south (the same longitude at both ends) or east-west (the same latitude at both ends). Each gridded matchup's page then maps it, shows the monthly climatology of the surface values along it, and, for matchups made through the water column (vertical=True), a section of the annual mean along it, interpolated onto 30 evenly spaced depths. Requires nctoolkit 1.3.6 or later. Default is None.
    depth_bins : list or None
        The depth ranges, in metres, that point matchups made through the water column (vertical=True) are summarised in, as a list of [min, max] pairs, e.g. [[0, 20], [20, 200], [200, None]]. A depth is in a bin if it is deeper than min and no deeper than max (the shallowest bin also takes its min). The deepest bin can have a max of None, for everything below its min. Bins can leave gaps, and observations in a gap are left out, but cannot overlap. The same bins are used for every point dataset, and in the summary. Default is None, which uses 0-10, 10-30, 30-60, 60-100, 100-150, 150-300, 300-600, 600-1000 and >1000 m.
    variables : list or None
        The matchups to validate, if not all of them. Each entry is a variable, which validates it against all its sources, or "variable/source", for one, e.g. ["temperature", "nitrate/WOA23"]. Both are as named in the matchups (oceanval_matchups), and are not case sensitive. Each entry must match at least one matchup. The summary covers only the matchups validated. Default is None, which validates every matchup.
    previous : bool or list
        Whether to validate the matchups in data_dir that the last matchup() run there did not make: those of earlier runs into the same directory, which matchup() leaves there when it matches up other variables or datasets. Default is True. False validates only the matchups the last run made or kept, as it recorded them. A list validates those and the earlier matchups it picks, each entry a variable or "variable/source", as for variables, e.g. ["chlorophyll/OCCCI"]. variables then picks among the matchups validated.

    Returns
    -------
    None
    """

    subregions, region_file, n_regions = _check_report_options(
        lon_lim, lat_lim, concise, fixed_scale, subregions, region
    )
    transect = transects.check_transect(transect)
    depth_bins = depths.check_depth_bins(depth_bins)
    # convert data_dir to absolute path
    data_dir = os.path.expanduser(data_dir)
    data_dir = os.path.abspath(data_dir)
    # check before removing any previous report or results
    _check_matchups(data_dir)
    variables = selection.check_variables(variables)
    matchups = selection.on_disk(data_dir)
    # the matchups validated, if not all of those there: the last matchup()
    # run's, and the earlier ones previous picks
    chosen = None
    if previous is not True:
        if previous is not False:
            previous = selection.check_variables(previous, name="previous")
            selection.check_against(previous, matchups, name="previous")
        matchups = selection.of_last_run(matchups, selection.last_run(data_dir), previous)
        if not matchups:
            raise ValueError(
                f"The last matchup() run in {data_dir} made no matchups, so with "
                "previous=False there is nothing to validate"
            )
        chosen = {selection.identity(m) for m in matchups}
    selection.check_against(variables, matchups)
    # ensure proper handling of ~
    out_dir = os.path.expanduser(out_dir)
    out_dir = os.path.abspath(out_dir)

    book_dir = os.path.join(out_dir, "oceanval_report")
    # if it doesn't exist, create it
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
    

    path_df = []

    fast_plot = False

    empty = True

    # book directory is book, book1, book2, book10 etc.

    # create a new name if one already exists
    i = 0

    if os.path.exists(book_dir):
        # the old trackers say which temporary files a build that did not
        # finish left behind, and are removed with the book
        _remove_notebook_temp_files(book_dir)
        shutil.rmtree(book_dir)



    # remove the results directory, which the notebooks write next to the report
    x_path = os.path.join(out_dir, "oceanval_results")
    if os.path.exists(x_path):
        shutil.rmtree(x_path)

    # keep a copy of the regions file with the results, so the notebooks and
    # compare can find it
    if region_file is not None:
        os.makedirs(f"{out_dir}/oceanval_results", exist_ok=True)
        shutil.copyfile(region_file, f"{out_dir}/oceanval_results/custom_subdomains.nc")

    if empty:
        from shutil import copyfile

        if not os.path.exists(book_dir):
            os.mkdir(book_dir)
        if not os.path.exists(f"{book_dir}/notebooks"):
            os.mkdir(f"{book_dir}/notebooks")


        data_path = importlib.resources.files(__name__).joinpath(
            "data/001_methods.ipynb"
        )
        if not os.path.exists(f"{book_dir}/notebooks/001_methods.ipynb"):
            copyfile(data_path, f"{book_dir}/notebooks/001_methods.ipynb")
        # open this file and replace model_name with model

        # the book's introduction, which fix_toc's myst.yml starts with
        data_path = importlib.resources.files(__name__).joinpath("data/intro.md")
        out = f"{book_dir}/" + os.path.basename(data_path)
        copyfile(data_path, out)

        data_path = importlib.resources.files(__name__).joinpath(
            "data/requirements.txt"
        )
        out = f"{book_dir}/" + os.path.basename(data_path)
        copyfile(data_path, out)

        path_df = []

        # loop through the point matchups and generate notebooks

        point_paths = glob.glob(f"{data_dir}/oceanval_matchups/point/**/**/**/**.csv")
        point_paths = [x for x in point_paths if "unit" not in os.path.basename(x)]
        # loop through the paths
        for pp in point_paths:
            if not selection.wanted(
                variables,
                os.path.basename(pp).split("_")[2].replace(".csv", ""),
                os.path.basename(pp).split("_")[0],
            ):
                continue
            if chosen is not None and (
                "point",
                os.path.basename(pp).split("_")[2].replace(".csv", ""),
                os.path.basename(pp).split("_")[0],
                os.path.basename(pp).split("_")[1],
            ) not in chosen:
                continue
            ff_def = pp.replace(".csv", "_definitions.pkl")
            definitions = dill.load(open(ff_def, "rb"))
            vv = os.path.basename(pp).split("_")[2].replace(".csv", "")
            source = os.path.basename(pp).split("_")[0]
            variable = vv
            layer = os.path.basename(pp).split("_")[1].replace(".csv", "")
            Variable = definitions[variable].short_name

            vv_file = pp
            vv_file_find = pp.replace("../../", "")

            if os.path.exists(vv_file_find):
                if (
                    len(
                        glob.glob(
                            f"{book_dir}/notebooks/*point_{layer}_{variable}.ipynb"
                        )
                    )
                    == 0
                ):
                    out = f"{book_dir}/notebooks/{source}_{layer}_{variable}.ipynb"
                    filedata = _point_notebook_text(
                        source,
                        layer,
                        variable,
                        Variable,
                        definitions[variable].n_levels,
                        data_dir,
                        out_dir,
                    )

                    # Write the file out again
                    with open(out, "w") as file:
                        file.write(filedata)

                    path_df.append(
                        pd.DataFrame(
                            {
                                "variable": [variable],
                                "path": out,
                            }
                        )
                    )

        # Loop through the gridded matchups and generate notebooks
        # identify gridded variables in matched data
        gridded_paths = glob.glob(f"{data_dir}/oceanval_matchups/gridded/**/**.nc")

        if len(gridded_paths) > 0:
            for vv in [
                os.path.basename(x).split("_")[1].replace(".nc", "")
                for x in gridded_paths
            ]:
                for source in [
                    os.path.basename(x).split("_")[0]
                    for x in glob.glob(
                        f"{data_dir}/oceanval_matchups/gridded/**/**_{vv}_**.nc"
                    )
                ]:

                    variable = vv
                    if not selection.wanted(variables, variable, source):
                        continue
                    if chosen is not None and ("gridded", variable, source, None) not in chosen:
                        continue
                    if not os.path.exists(
                        f"{book_dir}/notebooks/{source}_{variable}.ipynb"
                    ):
                        ff_def = glob.glob(
                            f"{data_dir}/oceanval_matchups/gridded/{variable}/{source}_*definitions*.pkl"
                        )[0]
                        definitions = dill.load(open(ff_def, "rb"))
                        Variable = definitions[variable].short_name
                        seasonal = _gridded_seasonal(data_dir, variable, source)

                        if (
                            len(
                                glob.glob(
                                    f"{book_dir}/notebooks/*{source}_{variable}.ipynb"
                                )
                            )
                            == 0
                        ):
                            filedata = _gridded_notebook_text(
                                source,
                                variable,
                                Variable,
                                seasonal,
                                data_dir,
                                subregions,
                                region_file,
                                n_regions,
                                transect=transect,
                            )

                            # Write the file out again
                            with open(
                                f"{book_dir}/notebooks/{source}_{variable}.ipynb", "w"
                            ) as file:
                                file.write(filedata)

                            variable = vv
                            path_df.append(
                                pd.DataFrame(
                                    {
                                        "variable": [variable],
                                        "path": [
                                            f"{book_dir}/notebooks/{source}_{variable}.ipynb"
                                        ],
                                    }
                                )
                            )

        # need to start by figuring out whether anything has already been run...

        i = 0

        for ff in [
            x for x in glob.glob("{book_dir}/notebooks/*.ipynb") if "info" not in x
        ]:
            try:
                i_ff = int(os.path.basename(ff).split("_")[0])
                if i_ff > i:
                    i = i_ff
            except:
                pass

        i_orig = i

        if len(path_df) > 0:
            path_df = pd.concat(path_df)
            path_df = path_df.sort_values(["variable", "path"]).reset_index(drop=True)

        for i in range(len(path_df)):
            file1 = path_df.path.values[i]
            # pad i with zeros using zfill
            i_pad = str(i + 1).zfill(3)
            new_file = (
                os.path.dirname(file1) + "/" + i_pad + "_" + os.path.basename(file1)
            )
            os.rename(file1, new_file)

        # copy the summary.ipynb notebook and add i_pad to the name

        i = i + 2
        i_pad = str(i).zfill(3)

        file1 = importlib.resources.files(__name__).joinpath("data/summary.ipynb")
        if len(glob.glob(f"{book_dir}/notebooks/*summary.ipynb")) == 0:
            copyfile(file1, f"{book_dir}/notebooks/{i_pad}_summary.ipynb")

        # change domain_title to "Full domain"

        with open(f"{book_dir}/notebooks/{i_pad}_summary.ipynb", "r") as file:
            filedata = file.read()

        # Replace the target string
        filedata = filedata.replace("domain_title", "Full domain")
        filedata = filedata.replace("data_dir_value", data_dir)
        filedata = filedata.replace("out_dir_value", out_dir)

        # Write the file out again
        with open(f"{book_dir}/notebooks/{i_pad}_summary.ipynb", "w") as file:
            file.write(filedata)

        # pair the notebooks using jupyter text

        _run_jupytext(
            ["--set-formats", "ipynb,py:percent"], f"{book_dir}/notebooks/*.ipynb"
        )

        # add the chunks
        add_chunks(out_dir)

        # loop through the notebooks and set r warnings options
        for ff in glob.glob(f"{book_dir}/notebooks/*.py"):
            _rewrite_notebook_source(
                ff, lon_lim, lat_lim, fixed_scale, concise, test, transect=transect,
                depth_bins=depth_bins,
            )

        # sync the notebooks
        #
        _run_jupytext(["--sync"], f"{book_dir}/notebooks/*.ipynb")

    # loop through notebooks and change fast_plot_value to fast_plot

    for ff in glob.glob(f"{book_dir}/notebooks/*.ipynb"):
        _fill_notebook_paths(ff, data_dir, out_dir, fast_plot)

    # fix the toc using the function

    fix_toc(concise=concise, data_dir=data_dir, out_dir=out_dir)

    for ff in glob.glob(f"{book_dir}/notebooks/*.ipynb"):
        ff_clean = ff.replace(".ipynb", ".py")
        if os.path.exists(ff_clean):
            os.remove(ff_clean)

    # move pml_logo and the oceanval wordmark to book directory

    shutil.copyfile(
        importlib.resources.files(__name__).joinpath("data/pml_logo.jpg"),
        f"{book_dir}/pml_logo.jpg",
    )
    shutil.copyfile(
        importlib.resources.files(__name__).joinpath("data/oceanval_wordmark.svg"),
        f"{book_dir}/oceanval_wordmark.svg",
    )

    _build_book(book_dir, pdf=pdf, word=word)

    if zip:
        _zip_offline_report(
            os.path.join(book_dir, "_build", "html"),
            os.path.join(out_dir, "oceanval_report.zip"),
        )

    out_ff = _summary_report_page(f"{book_dir}/_build/html")

    # create a symlink to the html file
    # (lexists, not exists: the old symlink can be broken if the summary
    # page's numeric prefix shifted between runs, and exists() follows
    # symlinks so it would miss a broken one and leave it for os.symlink
    # to trip over)
    if os.path.lexists(f"{out_dir}/oceanval_report.html"):
        os.remove(f"{out_dir}/oceanval_report.html")
    # create a symlink with relative directory
    os.symlink(os.path.relpath(out_ff, out_dir), f"{out_dir}/oceanval_report.html")
    if test is False:
        webbrowser.open("file://" + os.path.abspath(out_ff))


def rebuild(data_dir=".", pdf=False):
    """
    Rebuild the validation report after modifying notebooks.
    Use this if you have modified the notebooks generated and want to create a new validation report.

    Parameters
    ----------
    data_dir : str
        The directory where the oceanval_report directory is located. Default is current directory.
    pdf : bool
        Whether to also generate PDF downloads of the report. Default is False.
    """
    # check data_dir exists
    if not os.path.exists(data_dir):
        raise ValueError(f"data_dir {data_dir} does not exist")
    # add a deprecation notice
    data_dir = os.path.expanduser(data_dir)
    data_dir = os.path.abspath(data_dir)

    _build_book(f"{data_dir}/oceanval_report", pdf=pdf)

    out_ff = _summary_report_page(f"{data_dir}/oceanval_report/_build/html")
    webbrowser.open("file://" + os.path.abspath(out_ff))


def compare(model_dict=None, view=True, ask=True, pdf=False, word=False, out_dir="."):
    """
    Compare pre-validated simulations.
    This function will compare the validation output from multiple simulations.

    Parameters
    ----------
    model_dict : dict
        A dictionary mapping model names to the paths of their validation outputs.
    view : bool
        Open the comparison report in a web browser after it is generated.
    ask : bool
        If the comparison directory already exists, ask before replacing it.
    pdf : bool
        Whether to also generate PDF downloads of the report. Default is False.
    word : bool
        Whether to also generate Word versions of the report (one per page, plus a combined oceanval_report.docx), with the maths as editable Word equations. Default is False. Requires pandoc.
    out_dir : str
        The directory to build the comparison in: it goes in oceanval_comparison, inside it. Default is the current directory.

    Each path in model_dict is a directory validate() built its report in (its out_dir), which holds oceanval_report and oceanval_results. The comparison reads oceanval_results, and compares the gridded datasets validated in at least two of the simulations.
    """
    if model_dict is None:
        raise AttributeError("model_dict must be provided")
    if not isinstance(model_dict, dict):
        raise AttributeError("model_dict must be a dictionary")

    for key in model_dict.keys():
        if not os.path.exists(model_dict[key]):
            raise ValueError(f"Path {model_dict[key]} does not exist")
        model_dict[key] = os.path.abspath(model_dict[key])

    # everything is built in out_dir, never in whatever the current directory is
    comparison_dir = os.path.join(
        os.path.abspath(os.path.expanduser(out_dir)), "oceanval_comparison"
    )
    book_dir = os.path.join(comparison_dir, "compare")
    notebooks_dir = os.path.join(book_dir, "notebooks")

    if os.path.exists(comparison_dir):
        if ask:
            user_input = prompts.ask(
                f"{comparison_dir} already exists. This will be emptied and replaced. Do you want to proceed? (y/n): ",
                ("y", "n"),
            )
            if user_input.lower() != "y":
                print("Exiting")
                return None

        while True:
            files = glob.glob(f"{glob.escape(comparison_dir)}/**/**/**", recursive=True)
            for ff in files:
                if ff.startswith(comparison_dir + os.sep):
                    try:
                        os.remove(ff)
                    except Exception:
                        pass
            files = glob.glob(f"{glob.escape(comparison_dir)}/**/**/**", recursive=True)
            files = [x for x in files if os.path.isfile(x)]
            if len(files) == 0:
                break

    os.makedirs(notebooks_dir, exist_ok=True)

    shutil.copyfile(
        os.path.join(os.path.dirname(__file__), "data", "oceanval_wordmark.svg"),
        os.path.join(book_dir, "oceanval_wordmark.svg"),
    )

    comparison_notebooks = [
        "comparison_seasonal.ipynb",
        "comparison_spatial.ipynb",
        "comparison_regional.ipynb",
        "comparison_bias.ipynb",
    ]
    for notebook_name in comparison_notebooks:
        data_path = importlib.resources.files(__name__).joinpath(f"data/{notebook_name}")
        dest_path = os.path.join(notebooks_dir, notebook_name)
        shutil.copyfile(data_path, dest_path)

    model_dict_str = str(model_dict)
    for notebook_name in comparison_notebooks:
        path = os.path.join(notebooks_dir, notebook_name)
        with open(path, "r") as file:
            filedata = file.read()
        filedata = filedata.replace("model_dict_str", model_dict_str)
        with open(path, "w") as file:
            file.write(filedata)

    _run_jupytext(
        ["--set-formats", "ipynb,py:percent"],
        f"{glob.escape(notebooks_dir)}/*.ipynb",
    )
    add_chunks(paths=glob.glob(f"{glob.escape(notebooks_dir)}/*.py"))
    for book in glob.glob(f"{glob.escape(notebooks_dir)}/*.py"):
        with open(book, "r") as file:
            filedata = file.read()
        filedata = filedata.replace("the_test_status", "False")
        filedata = filedata.replace("the_lon_lim", "None")
        filedata = filedata.replace("the_lat_lim", "None")
        filedata = filedata.replace("concise_value", "False")
        filedata = filedata.replace("fast_plot_value", "False")
        with open(book, "w") as file:
            file.write(filedata)
    _run_jupytext(["--sync"], f"{glob.escape(notebooks_dir)}/*.ipynb")
    validation_links = []
    for key in model_dict:
        try:
            validation_links.append(
                (key, _summary_report_page(os.path.join(model_dict[key], "oceanval_report", "_build", "html")))
            )
        except FileNotFoundError:
            pass
    _build_book(
        book_dir,
        validation_links=validation_links,
        pdf=pdf,
        word=word,
    )

    if view:
        first_notebook_html = os.path.splitext(comparison_notebooks[0])[0] + ".html"
        landing_page = os.path.join(book_dir, "_build", "html", "notebooks", first_notebook_html)
        webbrowser.open("file://" + os.path.abspath(landing_page))


try:
    from importlib.metadata import version as _version
except ImportError:
    from importlib_metadata import version as _version

try:
    __version__ = _version("oceanval")
except Exception:
    __version__ = "999"


import tempfile


def temp_check():
    """
    Function to check temp files
    """

    mylist = [f for f in glob.glob("/tmp/*")]
    mylist = mylist + [f for f in glob.glob("/var/tmp/*")]
    mylist = mylist + [f for f in glob.glob("/usr/tmp/*")]
    mylist = [f for f in mylist if is_oceanval_temp(f) and not in_use(f)]

    session_info["old_files"] = mylist

    if len(mylist) > 0:
        if len(mylist) == 1:
            print(
                f"{len(mylist)} temporary file was created by oceanval in prior or current "
                f"sessions. Consider running oceanval.deep_clean!"
            )
        else:
            print(
                f"{len(mylist)} temporary files were created by oceanval in prior or current"
                f" sessions. Consider running oceanval.deep_clean!"
            )


temp_check()


def deep_clean():
    """
    Deep temp file cleaner.
    Remove all temporary files ever created by oceanval
    across all previous and current sesions
    """

    candidates = session_info["old_files"]

    mylist = [f for f in candidates if is_oceanval_temp(f)]
    for ff in mylist:
        if ff in session_info["old_files"]:
            os.remove(ff)
