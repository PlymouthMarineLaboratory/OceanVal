"""oceanval.compare(): where it builds the comparison, and what it builds it from.

The book itself is not built here (see _build_book): only what compare puts
together for it, which is quick.
"""

import glob
import json
import os

import nbformat
import pytest

import oceanval
from oceanval import prompts


def validation(directory):
    """A directory as validate() leaves it, with the results compare reads."""
    (directory / "oceanval_results" / "annual_mean").mkdir(parents=True)
    (directory / "oceanval_report" / "_build" / "html" / "notebooks").mkdir(parents=True)
    return directory


@pytest.fixture
def built(monkeypatch):
    """The books compare would build, recorded rather than built."""
    books = []
    monkeypatch.setattr(
        oceanval,
        "_build_book",
        lambda book_dir, validation_links=None, pdf=False, word=False: books.append(
            (book_dir, validation_links)
        ),
    )
    return books


def test_the_comparison_is_built_in_out_dir(tmp_path, monkeypatch, built):
    control = validation(tmp_path / "runs" / "control")
    mixing = validation(tmp_path / "runs" / "mixing")
    # run from somewhere else entirely, with relative paths to the validations
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    oceanval.compare(
        model_dict={"control": "../runs/control", "mixing": str(mixing)},
        out_dir=str(tmp_path / "comparisons"),
        view=False,
    )

    book = tmp_path / "comparisons" / "oceanval_comparison" / "compare"
    assert built == [(str(book), [])]
    # nothing in the directory it was run from
    assert os.listdir(elsewhere) == []
    # the wordmark the pages show beside them
    assert (book / "oceanval_wordmark.svg").exists()
    notebooks = sorted(os.path.basename(x) for x in glob.glob(str(book / "notebooks" / "*.ipynb")))
    assert notebooks == [
        "comparison_bias.ipynb", "comparison_regional.ipynb",
        "comparison_seasonal.ipynb", "comparison_spatial.ipynb",
    ]
    for name in notebooks:
        nb = nbformat.read(book / "notebooks" / name, as_version=4)
        source = "\n".join(cell.source for cell in nb.cells)
        # the simulations, by their full paths, and the chunks put in place
        assert repr({"control": str(control), "mixing": str(mixing)}) in source
        assert "model_dict_str" not in source
        assert "\nchunk_start\n" not in f"\n{source}\n"
        assert "from oceanval.quiet_nc import nc" in source


def test_out_dir_defaults_to_the_current_directory(tmp_path, monkeypatch, built):
    validation(tmp_path / "a")
    validation(tmp_path / "b")
    monkeypatch.chdir(tmp_path)

    oceanval.compare(model_dict={"a": "a", "b": "b"}, view=False)

    assert built[0][0] == str(tmp_path / "oceanval_comparison" / "compare")


@pytest.mark.parametrize("answer", ["y", "n"])
def test_an_earlier_comparison_is_replaced_only_if_wanted(tmp_path, built, answer):
    validation(tmp_path / "a")
    validation(tmp_path / "b")
    earlier = tmp_path / "out" / "oceanval_comparison" / "compare" / "notebooks" / "old.ipynb"
    earlier.parent.mkdir(parents=True)
    earlier.write_text("{}")
    asked = []

    def answerer(question, choices, details=None):
        asked.append(question)
        return answer

    with prompts.answered_by(answerer):
        oceanval.compare(
            model_dict={"a": str(tmp_path / "a"), "b": str(tmp_path / "b")},
            out_dir=str(tmp_path / "out"),
            view=False,
        )

    # the question names the directory it would empty
    assert asked == [
        f"{tmp_path / 'out' / 'oceanval_comparison'} already exists. This will be "
        "emptied and replaced. Do you want to proceed? (y/n): "
    ]
    assert earlier.exists() is (answer == "n")
    assert len(built) == (1 if answer == "y" else 0)


def test_a_missing_validation_is_refused(tmp_path, built):
    validation(tmp_path / "a")

    with pytest.raises(ValueError, match="does not exist"):
        oceanval.compare(
            model_dict={"a": str(tmp_path / "a"), "b": str(tmp_path / "b")},
            out_dir=str(tmp_path),
            view=False,
        )
    assert not (tmp_path / "oceanval_comparison").exists()
