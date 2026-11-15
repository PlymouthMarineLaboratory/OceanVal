"""A figure is drawn once in the gridded report. The notebooks run with IPython's
ast_node_interactivity = "all", which displays the value of a bare expression
statement inside an if/loop/try, so a call that returns a Figure and is followed
by plt.show() would show it twice. The chunks assign the figure instead."""

import glob
import os
import re

import oceanval

DATA = os.path.join(os.path.dirname(oceanval.__file__), "data")
BARE = re.compile(
    r"^\s*(density\.figure|gridded_plots\.(profiles|total_series|zonal_lines|hovmoller)|transects\.plot_\w+)\("
)


def test_figure_returning_calls_are_assigned():
    found = []
    for path in glob.glob(os.path.join(DATA, "chunk_*.pytemplate")):
        for number, line in enumerate(open(path), 1):
            if BARE.match(line):
                found.append(f"{os.path.basename(path)}:{number}: {line.strip()}")
    assert not found, found
