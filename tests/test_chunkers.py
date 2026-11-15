import glob
import importlib.resources
import os

from oceanval.chunkers import add_chunks, squeeze_blank_lines


def test_squeeze_keeps_one_blank_line_and_strings():
    text = "\n\na\n\n\n\nb\n'''x\n\n\ny'''\n\n\n\nc\n\n"
    assert squeeze_blank_lines(text) == "a\n\nb\n'''x\n\n\ny'''\n\nc\n"


def test_chunk_templates_have_no_double_blank_lines():
    folder = importlib.resources.files("oceanval").joinpath("data")
    for path in glob.glob(str(folder) + "/chunk_*.pytemplate"):
        text = open(path).read()
        if '"""' in text or "'''" in text:
            continue
        assert "\n\n\n" not in text, os.path.basename(path)


def test_add_chunks_leaves_one_blank_line(tmp_path):
    path = tmp_path / "page.py"
    path.write_text("# %%\nx = 1\n\n\n\nchunk_point\n\n\n\n# %%\ny = 2\n")
    add_chunks(paths=[str(path)])
    assert "\n\n\n" not in path.read_text()
