"""Checks of a report built with figure code (validate's and compare's
figure_code, see oceanval.figure_code), which the report tests share: every
figure has its "Copy code to reproduce figure" button, and the code each
button copies draws the figure again, run in a Python of its own."""

import base64
import glob
import html
import io
import os
import re
import subprocess
import sys

import numpy as np

BLOCK = re.compile(
    r'<div class="oceanval-figure">.*?<img[^>]*src="data:image/png;base64,([^"]+)".*?'
    r"<textarea[^>]*>(.*?)</textarea>",
    re.S,
)


def pages(html_dir):
    found = sorted(glob.glob(os.path.join(html_dir, "**", "*.html"), recursive=True))
    return [path for path in found if "/_static/" not in path]


def figures(page):
    """The figures of a page with their code: [(PNG bytes, code)]."""
    text = open(page).read()
    return [(base64.b64decode(png), html.unescape(code)) for png, code in BLOCK.findall(text)]


def plain_figures(page):
    """The number of PNG figures on a page without the button."""
    text = BLOCK.sub("", open(page).read())
    return len(re.findall(r'<img[^>]*src="data:image/png', text))


def check_every_figure_has_its_code(html_dir, results_dir):
    """Every figure of every page has the button, and nothing was left half saved."""
    shown = {os.path.basename(page): (len(figures(page)), plain_figures(page)) for page in pages(html_dir)}
    assert sum(n for n, _ in shown.values()) > 0, shown
    assert all(plain == 0 for _, plain in shown.values()), shown
    leftover = glob.glob(os.path.join(results_dir, "figure_data", "*", ".pending_*"))
    assert not leftover, leftover
    return shown


RUNNER = """
import runpy, sys, traceback
import matplotlib
matplotlib.use("agg")
import matplotlib.pyplot as plt
failed = []
for path in sys.argv[1:]:
    plt.close("all")
    try:
        runpy.run_path(path, run_name="__main__")
    except Exception:
        failed.append(path)
        traceback.print_exc()
print("FAILED", failed)
sys.exit(1 if failed else 0)
"""


def check_the_code_draws_each_figure(html_dir, folder):
    """Run the code of every figure of every page, each a script in folder, in one
    fresh Python (OceanVal from the working directory, as the tests import it): each
    must draw its figure again, the same as the report's."""
    from PIL import Image

    os.makedirs(folder, exist_ok=True)
    scripts, wanted = [], {}
    for page in pages(html_dir):
        for k, (png, code) in enumerate(figures(page)):
            name = f"{os.path.splitext(os.path.basename(page))[0]}_{k + 1}"
            drawn = os.path.join(folder, f"{name}.png")
            assert code.rstrip().endswith("plt.show()"), name
            script = os.path.join(folder, f"{name}.py")
            with open(script, "w") as file:
                file.write(code.replace("plt.show()\n", f"plt.savefig({drawn!r}, bbox_inches='tight', dpi=plt.gcf().dpi)\n"))
            scripts.append(script)
            wanted[drawn] = png
    runner = os.path.join(folder, "run_figures.py")
    with open(runner, "w") as file:
        file.write(RUNNER)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")]))
    done = subprocess.run([sys.executable, runner, *scripts], cwd=folder, env=env, capture_output=True, text=True, timeout=1800)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-6000:]
    for drawn, png in wanted.items():
        report = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=float)
        again = np.asarray(Image.open(drawn).convert("RGB"), dtype=float)
        assert report.shape == again.shape, (drawn, report.shape, again.shape)
        assert np.abs(report - again).mean() < 1, drawn
    return len(scripts)


def check_no_figure_code(html_dir, results_dir):
    """A report without figure code: no button, and nothing saved for one."""
    for page in pages(html_dir):
        assert "oceanval-copy-code" not in open(page).read(), page
    assert not os.path.exists(os.path.join(results_dir, "figure_data"))
