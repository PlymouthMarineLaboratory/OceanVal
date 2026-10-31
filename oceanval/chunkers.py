from oceanval.matchall import matchup
import pandas as pd
import glob
import os
import importlib

from oceanval.quiet_nc import nc
import webbrowser

import re


def is_chunk(x):
    x = x.replace("\n", "")
    try:
        return re.findall(r"chunk_.[a-z]*", x)[0] == x
    except:
        return False


def add_chunks( dir=None, paths=None):
    """Put the chunk_* templates in place in the report's .py:percent
    notebooks, or only in those listed in paths."""

    if paths is None:
        paths = glob.glob(f"{dir}/oceanval_report/notebooks/*.py")
        # compare() passes its own notebooks as paths
        if dir is not None:
            paths += glob.glob(f"{dir}/*.py")

    for path in paths:
        # read file line by line
        # Using readlines()
        file1 = open(path, "r")
        Lines = file1.readlines()

        count = 0
        # Strips the newline character
        # generate new lines
        new_lines = []
        for line in Lines:
            if is_chunk(line):
                # get the file names
                # .pytemplate avoids these R/magic-laden snippets being byte-compiled as Python
                chunk_file = line.replace("\n", "") + ".pytemplate"


                data_path = importlib.resources.files("oceanval").joinpath(f"data/{chunk_file}")

                # read the chunk file in line by line

                # Using readlines()
                file2 = open(data_path, "r")
                chunk_lines = file2.readlines()

                # add the chunk lines to the new lines
                for chunk_line in chunk_lines:

                    new_lines.append(chunk_line)

                # close the chunk file
                file2.close()

                count += 1
            else:
                new_lines.append(line)

        # close the original file
        file1.close()

        # write the new lines to the file
        file1 = open(path, "w")
        file1.writelines(new_lines)
        file1.close()
