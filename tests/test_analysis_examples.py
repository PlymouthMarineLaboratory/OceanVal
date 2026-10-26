"""What the Analyze window's info buttons say (see oceanval.analysis_examples)."""

import json
import os

from oceanval import analysis, analysis_examples as ex


def test_every_option_has_its_words():
    assert ex.EXAMPLES
    for kind, options in ex.EXAMPLES.items():
        assert kind in analysis.KINDS
        for option, found in options.items():
            assert found["title"].strip()
            assert found["text"] and all(isinstance(text, str) and text.strip() for text in found["text"]), (kind, option)


def test_each_pop_out_with_layers_has_them_all():
    for kind in ("climatology", "long_term", "seasonal", "map_changes"):
        assert list(ex.EXAMPLES[kind]) == ["surface", "bottom", "water_column"]
        assert [found["title"] for found in ex.EXAMPLES[kind].values()] == ["Surface", "Bottom", "Water column"]
    assert list(ex.EXAMPLES["phenology"]) == ["peak"]
    assert list(ex.EXAMPLES["transects"]) == [
        "surface_climatology", "surface_changes", "vertical_climatology", "vertical_changes"]


def test_the_sentences_are_whole():
    for kind, options in ex.EXAMPLES.items():
        for option, found in options.items():
            for text in found["text"]:
                assert text[0].isupper() and text.endswith("."), (kind, option, text)
                assert "{" not in text and ".:" not in text and " of at " not in text


def test_the_page_is_given_the_words_only():
    described = ex.describe()
    assert json.loads(json.dumps(described)) == described
    for kind, options in ex.EXAMPLES.items():
        for option, found in options.items():
            assert described[kind][option] == {"title": found["title"], "text": found["text"]}
    # a change to what is described is not a change to the words
    described["climatology"]["surface"]["text"].append("more")
    assert "more" not in ex.EXAMPLES["climatology"]["surface"]["text"]


def test_nothing_is_drawn_or_stored():
    """The info buttons say in words what is plotted: the only pictures in the package are its logos."""
    root = os.path.dirname(os.path.abspath(ex.__file__))
    pictures = sorted(
        os.path.relpath(os.path.join(folder, name), root)
        for folder, _, names in os.walk(root)
        for name in names
        if name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico"))
    )
    assert pictures == [
        os.path.join("data", "oceanval_wordmark.png"),
        os.path.join("data", "oceanval_wordmark.svg"),
        os.path.join("data", "pml_logo.jpg"),
    ]
