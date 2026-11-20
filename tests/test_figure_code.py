"""The figure code of the validation and comparison reports (oceanval.figure_code):
the arguments of a figure's call are saved and read back as they were, the
code makes the same call again, the figure is shown as plt.show() shows it
without figure code, and validate, compare and the window ask for it. The
reports themselves are checked in test_report_figures, test_report_compare,
test_transect_report and test_report_totals (see figure_code_checks)."""

import glob
import os

import matplotlib

matplotlib.use("agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
import xarray as xr  # noqa: E402

import oceanval  # noqa: E402
from oceanval import app, figure_code, gridded_plots  # noqa: E402
from oceanval.figure_code import Figures, Of  # noqa: E402

# what draw was last called with, by the code a figure copies as well as by the page
CALLS = []


def draw(*args, **kwargs):
    """A figure function of the test's own: it keeps what it was given."""
    CALLS.append((args, kwargs))
    fig = plt.figure()
    plt.plot([1, 2], [3, 4])
    return fig


def build(*args, **kwargs):
    """A value made for a figure's call (an Of)."""
    return ("built", args, kwargs)


@pytest.fixture(autouse=True)
def closed():
    CALLS.clear()
    Figures.failures.clear()
    yield
    plt.close("all")


def script_of(figures, number=3):
    """The code of the figure just drawn, as its button copies it."""
    [(fig, record)] = list(figures._records.values())
    figures._records.clear()
    return figures._script(record, number)


def run(code, monkeypatch):
    """Run code as a user would, after the page: what draw was called with."""
    monkeypatch.setattr(plt, "show", lambda *a, **k: None)
    CALLS.clear()
    exec(compile(code, "<figure code>", "exec"), {"__name__": "__main__"})
    [called] = CALLS
    return called


def test_without_figure_code_the_call_is_only_made(tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(plt, "show", lambda *a, **k: shown.append(True))
    figures = Figures(False, str(tmp_path / "figure_data"))
    fig = figures.call(draw, pd.DataFrame({"a": [1]}), label="x")
    assert isinstance(fig, matplotlib.figure.Figure)
    assert len(CALLS) == 1
    figures.show(1)
    assert shown == [True]
    assert not (tmp_path / "figure_data").exists()


def test_a_table_is_read_back_as_it_was(tmp_path, monkeypatch):
    df = pd.DataFrame({
        "index": [10, 20, 30],
        "when": pd.to_datetime(["2000-01-01", "2000-02-01", "2000-03-01"]),
        "region": pd.Categorical(["b", "a", "b"], categories=["b", "a"], ordered=True),
        "name": ["NA", "x", "None"],
        "value": [1.5, np.nan, 3.0],
    }, index=pd.Index([5, 6, 7]))
    series = pd.Series([1.0, 2.0], name="model")
    figures = Figures(True, str(tmp_path / "figure_data"), "p", "a test")
    figures.call(draw, df, series, n=3)
    code = script_of(figures)
    (got, got_series), options = run(code, monkeypatch)
    pd.testing.assert_frame_equal(got, df)
    pd.testing.assert_series_equal(got_series, series)
    assert options == {"n": 3}
    # saved under the figure's number, as .csv
    assert sorted(os.path.basename(x) for x in glob.glob(str(tmp_path / "figure_data/*"))) == [
        "figure_3_arg_1.csv", "figure_3_arg_2.csv",
    ]
    assert not glob.glob(str(tmp_path / "figure_data/.pending_*"))


def test_arrays_lists_and_netcdf_are_read_back(tmp_path, monkeypatch):
    small = np.array([1.0, 2.5, np.nan])
    large = np.arange(100.0).reshape(10, 10)
    times = np.array(["2000-01-01", "2000-02-01"], dtype="datetime64[ns]")
    ds = xr.Dataset({"model": ("x", np.arange(30.0))}, coords={"x": np.arange(30)})
    density = gridded_plots.Density(0, 10, log=True)
    density.add(np.array([1.0, 2.0, 2.1]), np.array([1.5, 2.0, 9.0]))
    figures = Figures(True, str(tmp_path / "figure_data"), "p", "a test")
    figures.call(draw, small, large, times, ds, [("All", density, {"n": 3, "r": 0.5})], list(range(40)),
                 {"Model": (small, small)}, cmap=plt.get_cmap("twilight", 12),
                 norm=matplotlib.colors.Normalize(vmin=0, vmax=2), made=Of(build, 1, k="v"))
    code = script_of(figures)
    args, options = run(code, monkeypatch)
    np.testing.assert_array_equal(args[0], small)
    np.testing.assert_array_equal(args[1], large)
    np.testing.assert_array_equal(args[2], times)
    xr.testing.assert_identical(args[3], ds)
    [(title, again, stats)] = args[4]
    assert title == "All" and stats == {"n": 3, "r": 0.5}
    np.testing.assert_array_equal(again.counts, density.counts)
    np.testing.assert_array_equal(again.edges, density.edges)
    assert again.log is True
    assert args[5] == list(range(40))
    np.testing.assert_array_equal(args[6]["Model"][1], small)
    assert options["cmap"](np.linspace(0, 1, 12)).tolist() == plt.get_cmap("twilight", 12)(np.linspace(0, 1, 12)).tolist()
    assert (options["norm"].vmin, options["norm"].vmax) == (0, 2)
    assert options["made"] == ("built", (1,), {"k": "v"})
    # the small array is written into the code, the large one saved
    assert "np.array([1.0, 2.5, float(\"nan\")])" in code
    assert glob.glob(str(tmp_path / "figure_data/figure_3_*.npy"))


def test_a_dataset_is_saved_once_for_the_figures_that_draw_it(tmp_path, monkeypatch):
    nc = pytest.importorskip("nctoolkit")
    path = sorted(glob.glob("data/example/2000/*/*grid_T.nc"))[0]
    ds = nc.open_data(path, checks=False)
    ds.subset(variables="votemper")
    ds.run()
    figures = Figures(True, str(tmp_path / "figure_data"), "p", "a test")
    figures.call(draw, {"Model": ds, "Observation": ds})
    first = script_of(figures, 1)
    figures.call(draw, ds)
    second = script_of(figures, 2)
    saved = glob.glob(str(tmp_path / "figure_data/*.nc"))
    assert len(saved) == 1, saved
    assert saved[0] in first and saved[0] in second
    (panels,), _ = run(first, monkeypatch)
    assert panels["Model"] is panels["Observation"]
    assert panels["Model"].variables == ["votemper"]


class Map:
    """Stands in for a DataSet, whose pub_plot draws the map."""

    def pub_plot(self, *args, **kwargs):
        return plt.figure()


def test_the_code_of_a_map_puts_the_land_beneath_it(tmp_path):
    figures = Figures(True, str(tmp_path / "figure_data"), "the page", "a test")
    figures.method(Map(), "pub_plot", "bias", robust=True)
    code = script_of(figures, 4)
    assert code.startswith("# Figure 4 on the page the page, in a test.\n")
    assert ".pub_plot(\"bias\", robust=True)" in code
    assert "def land_below(" in code and code.rstrip().endswith("plt.show()")


def test_the_figure_is_shown_with_its_button(tmp_path, monkeypatch):
    import IPython
    import IPython.display

    shown = []
    monkeypatch.setattr(IPython, "get_ipython", lambda: object())
    monkeypatch.setattr(IPython.display, "display", lambda value: shown.append(value))
    monkeypatch.setattr(figure_code, "_png", lambda fig: b"png")
    figures = Figures(True, str(tmp_path / "figure_data"), "p", "a test")
    figures.call(draw, pd.DataFrame({"a": [1]}), label="<b>")
    figures.show(2)
    figures.call(draw, pd.DataFrame({"a": [1]}))
    figures.show(2)
    [first, second] = [value.data for value in shown]
    assert figure_code.COPY_LABEL in first
    assert "Figure 2 on the page p" in first and "Figure 2 (part 2)" in second
    assert "the data OceanVal saved with the report" in first
    # the code is escaped in the page
    assert 'label=&quot;&lt;b&gt;&quot;' in first and "<b>" not in first
    assert sorted(os.path.basename(x) for x in glob.glob(str(tmp_path / "figure_data/*"))) == [
        "figure_2_arg_1.csv", "figure_2_part2_arg_1.csv",
    ]
    assert not plt.get_fignums()


def test_a_figure_whose_code_cannot_be_kept_is_still_shown(tmp_path, monkeypatch):
    class Unsaveable:
        def __reduce__(self):
            raise TypeError("cannot be saved")

    shown = []
    monkeypatch.setattr(plt, "show", lambda *a, **k: shown.append(True))
    figures = Figures(True, str(tmp_path / "figure_data"), "p", "a test")
    figures.call(draw, pd.DataFrame({"a": [1]}), Unsaveable())
    figures.show(1)
    assert shown == [True]
    assert Figures.failures and "cannot be saved" in Figures.failures[0][1]
    assert not glob.glob(str(tmp_path / "figure_data/*")) and not glob.glob(str(tmp_path / "figure_data/.pending_*"))


def test_nothing_drawn_keeps_nothing(tmp_path):
    figures = Figures(True, str(tmp_path / "figure_data"), "p", "a test")
    assert figures.call(lambda df: None, pd.DataFrame({"a": [1]})) is None
    assert figures._records == {}


def test_validate_and_compare_take_true_or_false():
    with pytest.raises(ValueError, match="figure_code must be True or False"):
        oceanval.validate(data_dir="nowhere", figure_code="yes")
    with pytest.raises(ValueError, match="figure_code must be True or False"):
        oceanval.compare(model_dict={}, figure_code="yes")


@pytest.mark.parametrize("choice, given, error", [
    ("yes", True, None), ("No", None, None), ("", None, app.FIGURE_CODE_ASK), ("maybe", None, app.FIGURE_CODE_ASK),
])
def test_the_window_asks_for_it(tmp_path, choice, given, error):
    form = dict(app.default_validate_form(), figure_code=choice, customize="no")
    arguments, errors = app.check_report(form, str(tmp_path))
    assert arguments.get("figure_code") == given
    assert errors.get("figure_code") == error

    for name in ("control", "mixing"):
        (tmp_path / name / "oceanval_results" / "annual_mean").mkdir(parents=True)
    compare_form = dict(app.default_compare_form(), figure_code=choice, name_1="control", name_2="mixing",
                        dir_1=str(tmp_path / "control"), dir_2=str(tmp_path / "mixing"))
    arguments, errors = app.check_compare(compare_form, str(tmp_path))
    assert arguments.get("figure_code") == given
    assert errors.get("figure_code") == error
    if given:
        assert app._compare_call(arguments, str(tmp_path)).endswith(", figure_code=True)")
        assert "figure_code=True" in app._validate_call({"data_dir": ".", "figure_code": True}, str(tmp_path))


def test_the_question_starts_unanswered(monkeypatch):
    # the tests that are not about it start it as "no" (conftest); the window starts it empty
    monkeypatch.setattr(app, "DEFAULT_FIGURE_CODE", "")
    assert app.default_validate_form()["figure_code"] == ""
    assert app.default_compare_form()["figure_code"] == ""


@pytest.mark.figure_code_question
def test_the_question_is_boxed_in_red_until_it_is_answered(browser, tmp_path, monkeypatch):
    from oceanval.app import App
    from test_app import write_matchups

    write_matchups(tmp_path / "gridded")
    window = App(cwd=str(tmp_path))
    window.choose("validate_menu")
    monkeypatch.setattr(window, "start_run", lambda args, label: None)
    window.choose("validate", data_dir=str(tmp_path / "gridded"))
    url = window.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#v-options:not([hidden])")
        row = "#v-code-row"
        assert "needs-answer" in page.get_attribute(row, "class")
        assert page.evaluate(f"getComputedStyle(document.querySelector('{row}')).borderTopWidth") == "3px"
        assert page.is_disabled("#v-next")
        assert page.get_attribute("#v-next", "title") == "Say whether the report should have code to reproduce its figures"
        page.check("#v-code-yes")
        assert "needs-answer" not in page.get_attribute(row, "class")
        assert page.is_enabled("#v-next")
        # the answer goes to the server with the form
        assert page.evaluate("document.querySelector('#v-code-yes').checked")

        page.close()
    finally:
        window.close()

    # and the comparison of validations asks it too
    window = App(cwd=str(tmp_path))
    window.choose("validate_menu")
    window.choose("compare")
    url = window.start()
    try:
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector("#view-compare:not([hidden])")
        for row_number, name in ((1, "control"), (2, "mixing")):
            (tmp_path / name / "oceanval_results" / "annual_mean").mkdir(parents=True)
            page.fill(f"#c-name_{row_number}", name)
            page.fill(f"#c-dir_{row_number}", str(tmp_path / name))
        assert "needs-answer" in page.get_attribute("#c-code-row", "class")
        assert page.is_disabled("#compare")
        assert page.get_attribute("#compare", "title") == "Say whether the report should have code to reproduce its figures"
        page.check("#c-code-no")
        assert "needs-answer" not in page.get_attribute("#c-code-row", "class")
        assert page.get_attribute("#compare", "title") != "Say whether the report should have code to reproduce its figures"
    finally:
        window.close()


def test_word_has_the_figure_without_its_button():
    # as nbconvert writes the block: its attributes reordered
    block = (
        '<div class="oceanval-figure"><div class="oceanval-figure__bar"><button class="oceanval-copy-code" '
        'onclick="oceanvalCopyCode(this)" type="button">Copy code to reproduce figure</button></div>'
        '<img alt="" src="data:image/png;base64,AAAA"/><textarea aria-label="The code of the figure" '
        'class="oceanval-figure__code" hidden="" readonly="">import nctoolkit as nc\n</textarea></div>'
    )
    source = oceanval._word_report_source(f"<p>Before</p>{block}<p>After</p>")
    assert "Copy code" not in source and "nctoolkit" not in source
    assert '<img alt="" src="data:image/png;base64,AAAA"/>' in source
    assert "<p>Before</p>" in source and "<p>After</p>" in source
