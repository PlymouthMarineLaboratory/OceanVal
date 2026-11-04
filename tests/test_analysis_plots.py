"""The figures of the analysis report (see oceanval.analysis_plots): each is
drawn by running its own code, the script its "Copy code to reproduce
figure" button copies, and that script draws the same figure in a Python of
its own."""

import ast
import base64
import functools
import http.server
import io
import os
import re
import subprocess
import sys
import threading

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
import xarray as xr  # noqa: E402

import oceanval  # noqa: E402
from oceanval import analysis, analysis_report  # noqa: E402
from conftest import no_book  # noqa: E402
from oceanval import analysis_plots as ap  # noqa: E402
from simulations import (  # noqa: E402
    ANALYSIS_LATS,
    ANALYSIS_LONS,
    write_analysis_regions,
    write_analysis_simulation,
    write_curvilinear_simulation,
)
from viewer_simulations import fvcom_months  # noqa: E402

CASES = ("daily", "series", "many", "curvilinear", "transects", "changes", "curvilinear_changes", "fvcom")
SERIES = {"Temperature": {"start": 2012, "end": 2014, "surface": True, "bottom": True, "water_column": "mean"}}


def write_many_regions(path, count=ap.MANY_REGIONS + 1):
    """A regions file with more regions than fit on one axes: a cell each."""
    lat, lon = np.array(ANALYSIS_LATS), np.array(ANALYSIS_LONS)
    cells = [(i, j) for i in range(len(lat)) for j in range(len(lon))][:count]
    regions = {}
    for number, (i, j) in enumerate(cells):
        mask = np.full((len(lat), len(lon)), np.nan)
        mask[i, j] = 1.0
        regions[f"r{number}"] = (("lat", "lon"), mask, {"long_name": f"Region {number}"})
    xr.Dataset(
        regions,
        coords={
            "lat": ("lat", lat, {"units": "degrees_north", "standard_name": "latitude"}),
            "lon": ("lon", lon, {"units": "degrees_east", "standard_name": "longitude"}),
        },
    ).to_netcdf(path)


@pytest.fixture(scope="module")
def analyses(tmp_path_factory):
    """An analysis with each kind of figure, without its report: {case: its
    results directory}."""
    root = tmp_path_factory.mktemp("figures")
    daily = str(root / "daily_sim")
    write_analysis_simulation(daily)
    monthly = str(root / "series_sim")
    write_analysis_simulation(monthly, years=(2012, 2013, 2014), daily=False, trend=1.0, east_west=0.5)
    curvilinear = str(root / "curvilinear_sim")
    write_curvilinear_simulation(curvilinear)
    regions = str(root / "regions.nc")
    write_analysis_regions(regions)
    many = str(root / "many.nc")
    write_many_regions(many)
    one = {"SST": {"start": 2013, "end": 2013, "surface": True}}
    curvilinear_two = str(root / "curvilinear_two_sim")
    write_curvilinear_simulation(curvilinear_two, years=(2013, 2014))
    runs = {
        "daily": dict(
            sim_dir=daily, variables={"Chlorophyll": "P1_Chl+P2_Chl", "Temperature": "thetao"},
            climatology={
                "Temperature": {"start": 2013, "end": 2014, "surface": True, "bottom": True, "water_column": "mean"},
                "Chlorophyll": {"start": 2013, "end": 2014, "surface": True},
            },
            phenology={"Chlorophyll": {"start": 2013, "end": 2014}}, thickness="e3t",
        ),
        "series": dict(
            sim_dir=monthly, variables={"Temperature": "thetao"}, long_term=SERIES, seasonal=SERIES,
            thickness="e3t", subregions=regions, stations={"Near": [-3.1, 51.2], "Far": [-0.2, 52.8]},
        ),
        "many": dict(
            sim_dir=monthly, variables={"Temperature": "thetao"}, long_term=SERIES, seasonal=SERIES,
            thickness="e3t", subregions=many,
        ),
        "curvilinear": dict(sim_dir=curvilinear, variables={"SST": "sst"}, climatology=one, long_term=one, seasonal=one),
    }
    years = {"start": 2012, "end": 2014}
    lines = [
        {"start": [-2.0, 50.0], "end": [-2.0, 53.0], "climatology": years},
        {"start": [-4.0, 51.5], "end": [0.0, 51.5], "climatology": years},
        {"start": [-4.0, 51.0], "end": [0.0, 51.0], "vertical": years},
        {"start": [-2.0, 50.0], "end": [-2.0, 53.0], "vertical": years},
        {"start": [-4.0, 51.0], "end": [0.0, 51.0], "trends": dict(years, how="absolute")},
        {"start": [-2.0, 50.0], "end": [-2.0, 53.0], "trends": dict(years, how="absolute_change")},
        {"start": [-4.0, 51.5], "end": [0.0, 51.5], "trends": dict(years, how="percentage_change")},
        {"start": [-2.0, 50.0], "end": [-2.0, 53.0], "trends": dict(years, how="absolute_change", window=2)},
        {"start": [-4.0, 51.5], "end": [0.0, 51.5], "trends": dict(years, how="percentage_change", window=2)},
        # the changes through the water column, of each kind, along each direction
        {"start": [-4.0, 51.0], "end": [0.0, 51.0], "vertical_trends": dict(years, how="absolute")},
        {"start": [-2.0, 50.0], "end": [-2.0, 53.0], "vertical_trends": dict(years, how="absolute_change")},
        {"start": [-4.0, 51.5], "end": [0.0, 51.5], "vertical_trends": dict(years, how="percentage_change", window=2)},
        # all four, over years of their own
        {"start": [-2.0, 50.0], "end": [-2.0, 53.0], "climatology": {"start": 2012, "end": 2013}, "vertical": years,
         "trends": {"start": 2013, "end": 2014, "how": "absolute_change"},
         "vertical_trends": {"start": 2012, "end": 2014, "how": "absolute_change"}},
    ]
    runs["transects"] = dict(
        sim_dir=monthly, variables={"Temperature": "thetao"}, transects={"Temperature": lines}, thickness="e3t"
    )
    runs["curvilinear"]["transects"] = {
        "SST": [{"start": [-7.0, 46.0], "end": [-7.0, 50.0], "climatology": {"start": 2013, "end": 2013}}]
    }
    # the maps of changes: two future periods and a plot of the changes, one future period and no maps of
    # the periods but the historical one, and one with no changes; all on a grid with something to see
    periods = {"historical": {"start": 2012, "end": 2012}, "future": [{"start": 2013, "end": 2013}, {"start": 2014, "end": 2014}]}
    runs["changes"] = dict(
        sim_dir=monthly, variables={"Temperature": "thetao", "Deep": "thetao", "Plain": "thetao"}, thickness="e3t",
        map_changes={
            "Temperature": dict(periods, plot="absolute_changes", change="percentage", surface=True, bottom=True, water_column="mean"),
            "Deep": dict(periods, future=periods["future"][:1], plot="changes", change="absolute", surface=True),
            "Plain": dict(periods, future=periods["future"][:1], plot="absolute", change="absolute", surface=True),
        },
    )
    runs["curvilinear_changes"] = dict(
        sim_dir=curvilinear_two, variables={"SST": "sst"},
        map_changes={"SST": {"historical": {"start": 2013, "end": 2013}, "future": [{"start": 2014, "end": 2014}],
                             "plot": "absolute_changes", "change": "absolute", "surface": True}},
    )
    # raw FVCOM output, on the nodes and on the elements, mapped on its mesh: its climatology at the surface,
    # the bottom and through the water column, its phenology, series (with a station), map changes and transects
    fvcom = fvcom_months(str(root / "fvcom_sim"))
    fvcom_years = {"start": 2012, "end": 2013}
    runs["fvcom"] = dict(
        sim_dir=fvcom, variables={"Temperature": "temp", "Velocity": "u"},
        climatology={
            "Temperature": dict(fvcom_years, surface=True, bottom=True, water_column="integral"),
            "Velocity": dict(fvcom_years, surface=True, water_column="mean"),
        },
        phenology={"Temperature": fvcom_years},
        long_term={"Temperature": dict(fvcom_years, surface=True)},
        seasonal={"Velocity": dict(fvcom_years, surface=True)},
        stations={"Middle": [-4.52, 50.31]},
        map_changes={"Temperature": {"historical": {"start": 2012, "end": 2012}, "future": [{"start": 2013, "end": 2013}],
                                     "plot": "absolute_changes", "change": "absolute", "surface": True}},
        # interpolated from the mesh: at the surface and through the water column, on the nodes and the elements
        transects={
            "Temperature": [{"start": [-5.0, 50.25], "end": [-4.0, 50.25], "climatology": fvcom_years, "vertical": fvcom_years}],
            "Velocity": [{"start": [-4.47, 50.0], "end": [-4.47, 51.0], "climatology": fvcom_years}],
        },
    )
    found = {}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(analysis_report, "InterimBook", no_book([]))
        for case, arguments in runs.items():
            out = str(root / case)
            oceanval.analyze(n_dirs_down=1, out_dir=out, cores=1, view=False, **arguments)
            found[case] = os.path.join(out, analysis.FOLDER, analysis.RESULTS)
    return found


def figures(info):
    """Each figure of an analysis's report: (the function, its arguments)."""
    for variable in info["variables"]:
        key = variable["key"]
        if variable["climatology"]:
            for layer in variable["climatology"]["layers"]:
                yield ap.annual_map, (key, layer)
                yield ap.monthly_maps, (key, layer)
        if variable["map_changes"]:
            for layer in variable["map_changes"]["layers"]:
                yield ap.change_maps, (key, layer)
        if variable["phenology"]:
            yield ap.peak_day_map, (key,)
            yield ap.peak_value_map, (key,)
            yield ap.daily_cycle, (key,)
        if variable["transects"]:
            for line in variable["transects"]["lines"]:
                yield ap.transect_map, (key, line["index"])
                for option in line["options"]:
                    yield ap.transect_figure, (key, line["index"], option)
        for kind in ("long_term", "seasonal"):
            if variable[kind]:
                for layer in variable[kind]["layers"]:
                    if kind == "long_term":
                        yield ap.long_term_plot, (key, layer)
                    else:
                        yield ap.seasonal_cycle_regions, (key, layer)
                    yield ap.station_plot, (key, kind, layer)


def png_now():
    """The figure drawn last, as a PNG, which is then closed."""
    buffer = io.BytesIO()
    plt.gcf().savefig(buffer, format="png", bbox_inches="tight")
    plt.close("all")
    return buffer.getvalue()


def pixels(data):
    return mpimg.imread(io.BytesIO(data), format="png")


# each script is run as a file of its own, with nothing of the others'
RUN_EACH = """
import runpy, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
for path in sys.argv[1:]:
    runpy.run_path(path, run_name="__main__")
    plt.close("all")
"""


@pytest.mark.parametrize("case", CASES)
def test_each_figure_is_drawn_by_the_code_it_copies(analyses, case, tmp_path):
    results = analyses[case]
    info = ap.load(results)
    drawn, shown, scripts = [], [], []
    for function, args in figures(info):
        figure = function(results, *args)
        if figure is None:
            # a station figure with no stations
            assert function is ap.station_plot and not info["stations"]
            continue
        shown.append(png_now())
        drawn.append((function.__name__, args, figure))
        path = str(tmp_path / f"figure_{len(scripts)}.py")
        text = ap.script(figure, f"Figure {len(scripts) + 1} on the {case} page")
        assert text.endswith("plt.show()\n")
        with open(path, "w") as file:
            file.write(text.replace("plt.show()\n", f'plt.gcf().savefig({path[:-3] + ".png"!r}, bbox_inches="tight")\n'))
        scripts.append(path)
    assert drawn
    # run elsewhere, as a user would: every path is written out in full. The
    # oceanval is this one, as it is the user's own that ran the analysis
    other = tmp_path / "elsewhere"
    other.mkdir()
    package = os.path.dirname(os.path.dirname(os.path.abspath(oceanval.__file__)))
    path = os.pathsep.join(x for x in (package, os.environ.get("PYTHONPATH")) if x)
    run = subprocess.run(
        [sys.executable, "-c", RUN_EACH, *scripts], cwd=str(other), capture_output=True, text=True,
        env=dict(os.environ, MPLBACKEND="Agg", PYTHONPATH=path),
    )
    assert run.returncode == 0, run.stderr[-3000:]
    for (name, args, figure), image, script in zip(drawn, shown, scripts):
        alone = open(script[:-3] + ".png", "rb").read()
        assert np.array_equal(pixels(alone), pixels(image)), f"{name}{args} is not drawn as its code draws it"
        ast.parse(figure.code)
        # a transect's map needs only where it runs
        assert (os.path.abspath(results) in figure.code) is (name != "transect_map") and "../" not in figure.code
        # only the curvilinear grid's maps need regridding
        maps = name in ("annual_map", "monthly_maps", "peak_day_map", "peak_value_map", "change_maps")
        assert figure.regridded is (maps and case.startswith("curvilinear"))
        assert ("to_latlon" in figure.code) is figure.regridded
        # more regions than fit on one axes have a panel each
        assert ("panels = dict(zip(regions, grid.flat))" in figure.code) is (case == "many")
        # the time series figures give the full domain a panel of its own, first
        if case == "many" and name in ("long_term_plot", "seasonal_cycle_regions"):
            assert "#999999" not in figure.code
            assert re.search(r'^regions = \[\n    "Full Domain",\n', figure.code, flags=re.M)


def test_the_maps_of_changes_are_titled_by_their_years(analyses):
    results = analyses["changes"]
    for key, titles in (
        ("temperature", ["2012", "2013", "2014", "2013 relative to 2012", "2014 relative to 2012"]),
        ("deep", ["2012", "2013 minus 2012"]),
        ("plain", ["2012", "2013"]),
    ):
        figure = ap.change_maps(results, key, "surface")
        plt.close("all")
        assert re.findall(r'^    \("([^"]*)", "', figure.code, flags=re.M) == titles
        assert "Historical" not in figure.code and "Future" not in figure.code
        # a colour bar below the maps of the periods, which share one, and one below the changes, if there are any
        bars = ast.literal_eval(re.search(r"^bars = (.*)$", figure.code, flags=re.M).group(1))
        assert [(first, last) for first, last, _, _ in bars] == {
            "temperature": [(0, 3), (3, 5)], "deep": [(0, 1), (1, 2)], "plain": [(0, 2)],
        }[key]
        assert 'location="bottom"' in figure.code and "legend_position" in figure.code
        # a row with fewer maps than the others is centred: the changes of two future periods are under the middle
        places = ast.literal_eval(re.search(r"^places = (.*)$", figure.code, flags=re.M).group(1))
        assert places == {
            "temperature": [(0, 0), (0, 2), (0, 4), (1, 1), (1, 3)], "deep": [(0, 0), (0, 2)], "plain": [(0, 0), (0, 2)],
        }[key]
    # a one-year period is its year
    assert ap._years_title(2013, 2013) == "2013" and ap._years_title(1970, 1989) == "1970-1989"
    # the changes are on a scale symmetric about zero, and the periods on one of their own
    code = ap.change_maps(results, "temperature", "surface").code
    plt.close("all")
    limits = ast.literal_eval(re.search(r"^limits = (.*)$", code, flags=re.M).group(1))
    assert limits[0] == limits[1] == limits[2] and limits[3] == limits[4] and limits[3][0] == -limits[3][1]
    assert re.search(r'^colours = \["auto", "auto", "auto", "RdBu_r", "RdBu_r"\]$', code, flags=re.M)


def test_what_the_figures_find(analyses):
    results = analyses["daily"]
    cycle = ap.daily_cycle(results, "chlorophyll")
    plt.close("all")
    assert set(cycle.found) == {"day", "month_day", "value", "note"}
    assert 1 <= cycle.found["day"] <= 365 and isinstance(cycle.found["note"], str)
    assert ap.day_text(cycle.found["month_day"])
    series = analyses["series"]
    cycle = ap.seasonal_cycle_regions(series, "temperature", "bottom")
    plt.close("all")
    assert sorted(cycle.found["means"]) == list(range(1, 13))
    assert ap.long_term_plot(series, "temperature", "surface").found == {"regions": 2}
    stations = ap.station_plot(series, "temperature", "seasonal", "surface")
    plt.close("all")
    assert stations.found == {"names": ["Near", "Far"]}
    # only the climatological cycle is drawn, never each year's months
    assert not hasattr(ap, "seasonal_series_plot")
    assert 'cycle = frame.groupby(["kind", "name", "month"]' in stations.code and "calendar.month_abbr" in stations.code


def test_the_script_says_what_it_is(analyses):
    figure = ap.annual_map(analyses["daily"], "temperature", "surface")
    plt.close("all")
    text = ap.script(figure, "Figure 1 on the Temperature page")
    first = text.split("\n")[0]
    assert first.startswith("# Figure 1 on the Temperature page of the OceanVal analysis of ")
    assert f"(oceanval {oceanval.__version__})" in first
    assert "conda environment OceanVal ran in" in text
    assert text.startswith("#") and text.endswith(figure.code + "plt.show()\n")
    assert ap.script(figure).startswith("# A figure of the OceanVal analysis of ")


def test_the_copy_block_holds_the_code_safely():
    code = 'title = "</textarea><script>alert(1)</script> & more"\n'
    block = ap.copy_block(b"\x89PNG data", code)
    assert ap.COPY_LABEL in block
    assert base64.b64encode(b"\x89PNG data").decode() in block
    assert "</textarea><script>alert" not in block
    assert "&lt;/textarea&gt;&lt;script&gt;alert(1)&lt;/script&gt; &amp; more" in block
    # base64 text is taken as it is
    assert ap.copy_block("QUJD", code).count("data:image/png;base64,QUJD") == 1


class TestShow:
    def test_without_code_it_is_plt_show(self, monkeypatch):
        shown = []
        monkeypatch.setattr(plt, "show", lambda: shown.append(True))
        ap.show(ap.Drawn("import matplotlib.pyplot as plt\n"), False, "Figure 1")
        ap.show(None, True)
        # outside a notebook too
        ap.show(ap.Drawn("import matplotlib.pyplot as plt\n"), True, "Figure 1")
        assert shown == [True, True, True]

    def test_with_code_it_shows_the_figure_with_its_button(self, monkeypatch):
        import IPython
        import IPython.display

        class Formatter:
            def format(self, fig, include=None):
                assert include == ["image/png"]
                return {"image/png": b"the figure"}, {}

        class Shell:
            display_formatter = Formatter()

        shown = []
        monkeypatch.setattr(IPython, "get_ipython", lambda: Shell())
        monkeypatch.setattr(IPython.display, "display", lambda thing: shown.append(thing))
        monkeypatch.setattr(plt, "show", lambda: pytest.fail("plt.show was used"))
        plt.plot([1, 2])
        ap.show(ap.Drawn("import matplotlib.pyplot as plt\nx = 1\n", about="the analysis"), True, "Figure 3 on the X page")
        assert plt.get_fignums() == []
        (html,) = shown
        assert base64.b64encode(b"the figure").decode() in html.data
        assert "# Figure 3 on the X page of the analysis." in html.data
        assert "x = 1\nplt.show()\n" in html.data


@pytest.fixture
def served(tmp_path):
    """tmp_path served on 127.0.0.1, which a browser counts as secure, as it
    does the oceanval window's report: (its URL)."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_the_button_copies_the_code(browser, served, tmp_path):
    plt.subplots(figsize=(6, 3))[1].plot([1, 2, 3])
    image = png_now()
    code = '# a figure\nimport matplotlib.pyplot as plt\nplt.title("a < b & c")\nplt.show()\n'
    blocks = ap.copy_block(image, code) + ap.copy_block(image, code.replace("a figure", "another"))
    (tmp_path / "page.html").write_text(
        f'<!doctype html><html><head><meta charset="utf-8"></head><body><main style="width:900px">{blocks}</main></body></html>'
    )
    context = browser.new_context(viewport={"width": 1000, "height": 900})
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=served)
    errors = []
    try:
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{served}/page.html")
        button = page.locator(".oceanval-copy-code").first
        img = page.locator(".oceanval-figure img").first
        assert button.inner_text() == ap.COPY_LABEL
        # at the figure's top right, above it, so that it hides none of it
        at, of = button.bounding_box(), img.bounding_box()
        assert abs((at["x"] + at["width"]) - (of["x"] + of["width"])) < 2
        assert at["y"] + at["height"] <= of["y"] + 0.5
        # and the code is not shown
        assert page.locator(".oceanval-figure__code").first.is_hidden()
        page.locator(".oceanval-copy-code").nth(1).click()
        page.wait_for_function("() => document.querySelectorAll('.oceanval-copy-code')[1].textContent === 'Copied'")
        assert page.evaluate("navigator.clipboard.readText()") == code.replace("a figure", "another")
        button.click()
        page.wait_for_function("() => document.querySelector('.oceanval-copy-code').textContent === 'Copied'")
        assert page.evaluate("navigator.clipboard.readText()") == code
        # it says what it is again
        page.wait_for_function(f"() => document.querySelector('.oceanval-copy-code').textContent === {ap.COPY_LABEL!r}")
        assert errors == []
    finally:
        context.close()
