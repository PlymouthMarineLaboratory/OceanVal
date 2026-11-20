"""The "Copy code to reproduce figure" button of the report figures: its HTML
(copy_block), the pieces of the code it copies, and, for the validation and
comparison reports, Figures, which keeps the data each figure was drawn from.

The analysis reports (oceanval.analysis_plots) draw each figure by running a
script of its own. The validation and comparison reports work their figures
out as the page runs, so there the code redraws the figure from its data,
saved when it was drawn: each figure is one call (an OceanVal drawing
function, or nctoolkit's pub_plot or panel_plot), and Figures.call makes that
call, saves its arguments to files (tables as .csv, netCDF as .nc, arrays as
.npy) and writes the code that reads them and makes the same call again.
Figures.show then shows the figure, with the button if figure_code is on, as
plt.show() does if it is off, when nothing is saved and the page is as before.
"""

import base64
import html
import inspect
import json
import os
import re
import shutil
import sys

import numpy as np

from oceanval.map_land import LAND_CODE

COPY_LABEL = "Copy code to reproduce figure"

COPY_STYLE = """<style>
.oceanval-figure{width:fit-content;max-width:100%;margin:0 auto}
.oceanval-figure__bar{text-align:right;margin:0 0 2px}
.oceanval-copy-code{font:12px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;color:#24425c;background:#fff;
border:1px solid #b9c3cc;border-radius:4px;padding:2px 9px;cursor:pointer}
.oceanval-copy-code:hover{background:#eef3f7;border-color:#8a9aa8}
.oceanval-copy-code:focus-visible{outline:2px solid #1b6f8a;outline-offset:1px}
.oceanval-figure img{display:block;max-width:100%;height:auto;margin:0 auto}
.oceanval-figure__code{display:block;box-sizing:border-box;width:100%;min-height:12em;margin-top:4px;
font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.oceanval-figure__code[hidden]{display:none}
@media print{.oceanval-figure__bar{display:none}}
</style>"""

COPY_SCRIPT = """<script>
window.oceanvalCopyCode = window.oceanvalCopyCode || function (button) {
  var box = button.closest(".oceanval-figure").querySelector(".oceanval-figure__code");
  var code = box.value;
  var label = button.getAttribute("data-label") || button.textContent;
  button.setAttribute("data-label", label);
  function done(ok) {
    if (ok) {
      button.textContent = "Copied";
      setTimeout(function () { button.textContent = label; }, 2000);
    } else {
      box.hidden = false;
      box.focus();
      box.select();
      button.textContent = "Copy failed: select the code below and press Ctrl+C";
    }
  }
  function fallback() {
    var temp = document.createElement("textarea");
    temp.value = code;
    temp.setAttribute("readonly", "");
    temp.style.position = "fixed";
    temp.style.top = "-1000px";
    document.body.appendChild(temp);
    temp.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (error) { ok = false; }
    document.body.removeChild(temp);
    done(ok);
  }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(code).then(function () { done(true); }, fallback);
  } else {
    fallback();
  }
};
</script>"""

NC_IMPORT = "import warnings\nwith warnings.catch_warnings():\n    warnings.simplefilter(\"ignore\")\n    import nctoolkit as nc\n"


def copy_block(png, code, about="the analysis's saved results"):
    """The HTML of a figure, png (its PNG, as bytes or base64 text), with a
    button above its top right corner that copies code. about is what the
    code draws the figure from, for the button's tooltip."""
    if isinstance(png, bytes):
        png = base64.b64encode(png).decode("ascii")
    return (
        '<div class="oceanval-figure">'
        '<div class="oceanval-figure__bar">'
        '<button type="button" class="oceanval-copy-code" onclick="oceanvalCopyCode(this)" '
        f'title="Copies the Python code that draws this figure, from {html.escape(about)}, '
        'to run in the conda environment OceanVal ran in">'
        f"{COPY_LABEL}</button></div>"
        f'<img src="data:image/png;base64,{png}" alt="">'
        f'<textarea class="oceanval-figure__code" readonly hidden aria-label="The code of the figure">{html.escape(code)}</textarea>'
        "</div>"
        f"{COPY_STYLE}{COPY_SCRIPT}"
    )


def _png(fig):
    """fig as the PNG plt.show() would put in the notebook: by the
    notebook's own formatter, or as the inline backend draws it."""
    from IPython import get_ipython

    shell = get_ipython()
    if shell is not None:
        data, _ = shell.display_formatter.format(fig, include=["image/png"])
        if data.get("image/png") is not None:
            return data["image/png"]
    from IPython.core.pylabtools import print_figure

    return print_figure(fig, "png", bbox_inches="tight")


# ---- code ----


class _Code(str):
    """Code, written into a script as it is, not as a value."""


def _py(value):
    """value as Python code, with strings in double quotes."""
    if isinstance(value, _Code):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (bool, np.bool_, type(None))):
        return repr(bool(value)) if isinstance(value, np.bool_) else repr(value)
    if isinstance(value, (int, np.integer)):
        return repr(int(value))
    if isinstance(value, (float, np.floating)):
        value = float(value)
        if not np.isfinite(value):
            return f'float("{value}")'
        return repr(value)
    if isinstance(value, tuple):
        inner = ", ".join(_py(x) for x in value)
        return f"({inner},)" if len(value) == 1 else f"({inner})"
    if isinstance(value, list):
        return "[" + ", ".join(_py(x) for x in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_py(k)}: {_py(v)}" for k, v in value.items()) + "}"
    return repr(value)


def _call(function, options, args=(), width=88):
    """function(*args, **options) as code, on one line, or on a line for
    each argument if that is longer than width."""
    parts = [_py(value) for value in args] + [f"{name}={_py(value)}" for name, value in options.items()]
    line = f"{function}({', '.join(parts)})"
    if len(line) <= width and "\n" not in line:
        return line
    return f"{function}(\n" + "".join(f"    {_indent(part)},\n" for part in parts) + ")"


def _indent(text):
    return text.replace("\n", "\n    ")


def _code(*blocks):
    """The blocks of a script, each a line or lines, as one script. Empty
    blocks are left out. A script drawing nctoolkit maps ends by putting their
    land beneath the data, with the lakes as water (see oceanval.map_land)."""
    blocks = [block for block in blocks if block]
    if any(re.search(r"\b(pub_plot|panel_plot)\(", block) for block in blocks):
        blocks += [
            "# nctoolkit draws the land over the maps, lakes too: put it beneath them,\n"
            "# with the lakes drawn as water",
            LAND_CODE,
            "land_below()",
        ]
    return "\n".join(blocks) + "\n"


# ---- the validation and comparison reports' figures ----


class Of:
    """A call made for its value, as an argument of the figure's call: e.g.
    Of(plotnine_figures.regional_cycles, df, label), the plot that
    plotnine_figures.sized draws."""

    def __init__(self, function, *args, **kwargs):
        self.function = function
        self.args = args
        self.kwargs = kwargs

    def value(self):
        return self.function(*_values(self.args), **_values(self.kwargs))


def _values(value):
    """value with each Of in it made (its value)."""
    if isinstance(value, Of):
        return value.value()
    if isinstance(value, tuple):
        return tuple(_values(x) for x in value)
    if isinstance(value, list):
        return [_values(x) for x in value]
    if isinstance(value, dict):
        return {k: _values(v) for k, v in value.items()}
    return value


# rows of a table beyond which its .csv is gzipped (pandas reads either)
GZIP_ROWS = 50_000
# numbers in a list or array written into the code, not saved
INLINE_VALUES = 24


def _slug(text):
    text = re.sub(r"[^0-9A-Za-z]+", "_", str(text)).strip("_").lower()
    return text[:40] or "value"


class _Record:
    """The code of one figure's call, its files saved under their pending names."""

    def __init__(self):
        self.imports = []
        self.lines = []
        self.call = ""
        self.files = []  # [pending path, final name stem, extension]

    def need(self, line):
        if line not in self.imports:
            self.imports.append(line)


class Figures:
    """The figures of a page of a validation or comparison report.

    on is whether the report has figure code (validate's figure_code);
    folder where each figure's data is saved (oceanval_results/figure_data/
    <page>); page the page, and about the report, for the first line of each
    script. Off, call only makes the call and show only shows the figure."""

    def __init__(self, on=False, folder=None, page="", about="the OceanVal report"):
        self.on = bool(on) and folder is not None
        self.folder = os.path.abspath(folder) if folder else None
        self.page = page
        self.about = about
        self._records = {}  # id(figure) -> (figure, record)
        self._k = 0
        # netCDF saved for an earlier figure, by the files it was saved from,
        # so a DataSet several figures draw is saved once
        self._netcdf_saved = {}
        # the final path of each file of a figure shown
        self._final = {}
        self._shown = {}

    # -- drawing --

    def call(self, function, *args, **kwargs):
        """function(*args, **kwargs), an argument that is an Of made first;
        with figure code, the code that makes the call again from the
        arguments, saved, is kept for show."""
        result = function(*_values(args), **_values(kwargs))
        if self.on:
            self._keep(result, lambda: self._record(function, None, args, kwargs))
        return result

    def method(self, obj, name, *args, **kwargs):
        """obj.name(*args, **kwargs), e.g. a DataSet's pub_plot, kept as call keeps one."""
        result = getattr(obj, name)(*_values(args), **_values(kwargs))
        if self.on:
            self._keep(result, lambda: self._record(None, (obj, name), args, kwargs))
        return result

    # what went wrong keeping a figure's code, for the tests: (what, error)
    failures = []

    def _keep(self, result, record):
        """Keep the code of the figure result drew, record() making it. If that
        fails, the figure is shown without its code, never not at all."""
        import matplotlib.pyplot as plt
        from matplotlib.figure import Figure

        if isinstance(result, Figure):
            fig = result
        elif plt.get_fignums():
            fig = plt.gcf()
        else:
            # nothing was drawn
            return
        try:
            self._records[id(fig)] = (fig, record())
        except Exception as error:
            # the files it saved are of no use without it
            for pending, *_ in getattr(self, "_building", _Record()).files:
                if pending not in self._final and os.path.exists(pending):
                    os.remove(pending)
            self._netcdf_saved = {
                key: value for key, value in self._netcdf_saved.items()
                if all(os.path.exists(path) or path in self._final for path in value[0])
            }
            Figures.failures.append((self.page, repr(error)))
            print(f"OceanVal could not keep the code of a figure: {error!r}", file=sys.stderr)

    # -- showing --

    def show(self, number, fig=None):
        """Show the figure just drawn (or fig), Figure number on the page: as
        plt.show() does (display(fig) for fig), or, with figure code, with the
        button that copies its code."""
        import matplotlib.pyplot as plt
        from IPython import get_ipython

        record = None
        if self.on:
            figure = plt.gcf() if fig is None else fig
            record = self._records.pop(id(figure), (None, None))[1]
        if record is None or get_ipython() is None:
            if fig is None:
                plt.show()
            else:
                from IPython.display import display

                display(fig)
            return
        from IPython.display import HTML, display

        try:
            code = self._script(record, number)
            png = _png(figure)
        except Exception as error:
            Figures.failures.append((self.page, repr(error)))
            print(f"OceanVal could not show the code of a figure: {error!r}", file=sys.stderr)
            if fig is None:
                plt.show()
            else:
                display(fig)
            return
        plt.close(figure)
        display(HTML(copy_block(png, code, "the data OceanVal saved with the report")))

    def _script(self, record, number):
        """The script of record, as Figure number, its files given their final names."""
        part = self._shown.get(str(number), 0) + 1
        self._shown[str(number)] = part
        stem = f"figure_{number}" + (f"_part{part}" if part > 1 else "")
        title = f"Figure {number}" + (f" (part {part})" if part > 1 else "")
        call = record.call
        lines = list(record.lines)
        for pending, name, extension in record.files:
            final = self._final.get(pending)
            if final is None:
                final = os.path.join(self.folder, f"{stem}_{name}{extension}")
                if os.path.exists(pending):
                    os.replace(pending, final)
                self._final[pending] = final
            lines = [line.replace(pending, final) for line in lines]
        where = f" on the page {self.page}" if self.page else ""
        header = (
            f"# {title}{where}, in {self.about}.\n"
            f"# It draws the figure again from the data OceanVal saved in {self.folder}:\n"
            "# run it in the conda environment OceanVal ran in.\n"
        )
        return header + _code("\n".join(record.imports), "\n".join(lines), call) + "plt.show()\n"

    # -- the code of a call --

    def _record(self, function, method, args, kwargs):
        self._k += 1
        os.makedirs(self.folder, exist_ok=True)
        record = self._building = _Record()
        record.need("import matplotlib.pyplot as plt")
        saved = {}
        names = _names(function, args)
        values = [self._value(value, name, record, saved) for value, name in zip(args, names)]
        options = {key: self._value(value, key, record, saved) for key, value in kwargs.items()}
        if method is not None:
            obj, name = method
            target = self._value(obj, "data", record, saved)
            record.call = _call(f"{target}.{name}", options, values)
        else:
            record.call = _call(self._function(function, record), options, values)
        return record

    def _function(self, function, record):
        """The name a script calls function by, importing what it needs."""
        module = getattr(function, "__module__", "") or ""
        name = function.__name__
        if module.startswith("nctoolkit"):
            record.need(NC_IMPORT)
            return f"nc.{name}"
        if module.startswith("oceanval."):
            short = module.split(".")[-1]
            record.need(f"from oceanval import {short}")
            return f"{short}.{name}"
        record.need(f"from {module} import {name}")
        return name

    def _value(self, value, name, record, saved):
        """value as code in the script: a literal, or a variable read from a
        file it is saved in. saved keeps what this call saved already, so an
        argument given twice (a DataSet in two panels) is saved once."""
        import pandas as pd
        import xarray as xr

        if id(value) in saved:
            return saved[id(value)]
        if isinstance(value, Of):
            options = {k: self._value(v, k, record, saved) for k, v in value.kwargs.items()}
            names = _names(value.function, value.args)
            args = [self._value(v, n, record, saved) for v, n in zip(value.args, names)]
            return _Code(_call(self._function(value.function, record), options, args))
        if isinstance(value, (str, bool, int, float, type(None), np.bool_, np.integer, np.floating)):
            return value
        if isinstance(value, (list, tuple)):
            if len(value) > INLINE_VALUES and all(isinstance(x, (int, float, np.integer, np.floating)) for x in value):
                code = self._array(np.asarray(value), name, record, saved)
                return _Code(f"{code}.tolist()" if isinstance(value, list) else f"tuple({code})")
            items = [self._value(x, f"{name}_{i + 1}", record, saved) for i, x in enumerate(value)]
            return items if isinstance(value, list) else tuple(items)
        if isinstance(value, dict):
            return {k: self._value(v, f"{name}_{k}", record, saved) for k, v in value.items()}
        if isinstance(value, (pd.Timestamp, np.datetime64)):
            record.need("import pandas as pd")
            return _Code(f"pd.Timestamp({_py(str(pd.Timestamp(value)))})")
        if isinstance(value, pd.DataFrame):
            return self._keep_value(value, self._frame(value, name, record), saved)
        if isinstance(value, pd.Series):
            frame_name = value.name if isinstance(value.name, str) else "value"
            code = self._frame(value.to_frame(frame_name), name, record)
            return self._keep_value(value, _Code(f"{code}[{_py(frame_name)}].rename({_py(value.name)})"), saved)
        if isinstance(value, np.ndarray):
            return self._keep_value(value, _Code(self._array(value, name, record, saved)), saved)
        if isinstance(value, (xr.Dataset, xr.DataArray)):
            path = self._pending(name, ".nc", record)
            value.to_netcdf(path)
            record.need("import xarray as xr")
            opener = "xr.load_dataset" if isinstance(value, xr.Dataset) else "xr.load_dataarray"
            return self._keep_value(value, self._variable(name, f"{opener}({_py(path)})", record), saved)
        if _is_dataset(value):
            return self._keep_value(value, self._netcdf(value, name, record), saved)
        if type(value).__name__ == "Density" and hasattr(value, "to_dataset"):
            path = self._pending(name, ".nc", record)
            value.to_dataset().to_netcdf(path)
            record.need("import xarray as xr")
            record.need("from oceanval import gridded_plots")
            code = f"gridded_plots.Density.from_dataset(xr.load_dataset({_py(path)}))"
            return self._keep_value(value, self._variable(name, code, record), saved)
        colour = _colour_code(value, record)
        if colour is not None:
            return colour
        # anything else: pickled
        path = self._pending(name, ".pkl", record)
        pd.to_pickle(value, path)
        record.need("import pandas as pd")
        return self._keep_value(value, self._variable(name, f"pd.read_pickle({_py(path)})", record), saved)

    def _keep_value(self, value, code, saved):
        saved[id(value)] = code
        return code

    def _variable(self, name, code, record):
        """A variable of the script, name, set to code: its name."""
        variable = _slug(name)
        taken = {line.split(" = ", 1)[0] for line in record.lines}
        base, i = variable, 2
        while variable in taken or variable in ("plt", "pd", "np", "xr", "nc"):
            variable = f"{base}_{i}"
            i += 1
        record.lines.append(f"{variable} = {code}")
        return _Code(variable)

    def _pending(self, name, extension, record):
        path = os.path.join(self.folder, f".pending_{self._k}_{len(record.files) + 1}{extension}")
        record.files.append([path, _slug(name), extension])
        return path

    def _frame(self, df, name, record):
        """df saved as a .csv (gzipped if long), read back as it was: its
        index, dates and categories kept."""
        import pandas as pd

        record.need("import pandas as pd")
        frame = df
        index_names = None
        default = isinstance(frame.index, pd.RangeIndex) and frame.index.start == 0 and frame.index.step == 1
        if not default:
            # the index, as columns named for it (named index_1... if it has no names, or
            # names a column has)
            index_names = [
                str(x) if x is not None and str(x) not in frame.columns else f"index_{i + 1}"
                for i, x in enumerate(frame.index.names)
            ]
            frame = frame.rename_axis(index_names).reset_index()
        extension = ".csv.gz" if len(frame) > GZIP_ROWS else ".csv"
        path = self._pending(name, extension, record)
        frame.to_csv(path, index=False)
        options = {}
        dates = [str(c) for c in frame.columns if pd.api.types.is_datetime64_any_dtype(frame[c])]
        if dates:
            options["parse_dates"] = dates
        strings = [
            str(c) for c in frame.columns
            if (frame[c].dtype == object or pd.api.types.is_string_dtype(frame[c].dtype))
            and not isinstance(frame[c].dtype, pd.CategoricalDtype)
            and all(isinstance(x, str) for x in frame[c].dropna())
        ]
        if strings:
            # "NA" and the like stay text, as they were
            options["dtype"] = {c: "str" for c in strings}
            options["keep_default_na"] = False
            options["na_values"] = [""]
        code = _call("pd.read_csv", options, [path])
        variable = self._variable(name, code, record)
        for column in frame.columns:
            if isinstance(frame[column].dtype, pd.CategoricalDtype):
                categories = list(frame[column].cat.categories)
                record.lines.append(
                    f"{variable}[{_py(str(column))}] = pd.Categorical({variable}[{_py(str(column))}], "
                    f"categories={_py(categories)}, ordered={_py(bool(frame[column].cat.ordered))})"
                )
        if index_names is not None:
            restored = [None if x is None else str(x) for x in df.index.names]
            record.lines.append(f"{variable} = {variable}.set_index({_py(index_names)})")
            if restored != index_names:
                record.lines.append(f"{variable}.index.names = {_py(restored)}")
        return variable

    def _array(self, array, name, record, saved):
        record.need("import numpy as np")
        if array.ndim == 1 and array.size <= INLINE_VALUES and array.dtype.kind in "iuf":
            return _call("np.array", {}, [[_Code(_py(x)) for x in array.tolist()]])
        path = self._pending(name, ".npy", record)
        allow = array.dtype.kind == "O"
        np.save(path, array, allow_pickle=allow)
        return str(self._variable(name, f"np.load({_py(path)}" + (", allow_pickle=True)" if allow else ")"), record))

    def _netcdf(self, ds, name, record):
        """An nctoolkit DataSet, its files saved as they are now."""
        record.need(NC_IMPORT)
        ds = ds.copy()
        ds.run()
        key = tuple(os.path.abspath(x) for x in ds.current)
        if key in self._netcdf_saved:
            paths, files = self._netcdf_saved[key]
            record.files.extend(files)
        else:
            paths = []
            for i, current in enumerate(ds.current):
                path = self._pending(name if len(ds.current) == 1 else f"{name}_{i + 1}", ".nc", record)
                shutil.copyfile(current, path)
                paths.append(path)
            self._netcdf_saved[key] = (paths, record.files[-len(paths):])
        opened = paths[0] if len(paths) == 1 else paths
        return self._variable(name, f"nc.open_data({_py(opened)}, checks=False)", record)


def _is_dataset(value):
    return type(value).__name__ == "DataSet" and type(value).__module__.startswith("nctoolkit")


def _names(function, args):
    """The names of function's parameters that args are given for."""
    try:
        kinds = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        parameters = [
            name for name, parameter in inspect.signature(function).parameters.items()
            if parameter.kind in kinds and name != "self"
        ]
    except (TypeError, ValueError):
        parameters = []
    names = []
    for i, _ in enumerate(args):
        names.append(parameters[i] if i < len(parameters) else f"arg_{i + 1}")
    return names


def _colour_code(value, record):
    """A matplotlib colour map or norm as code, or None for anything else."""
    try:
        import matplotlib
        from matplotlib import colors
    except ImportError:
        return None
    if isinstance(value, colors.Colormap):
        try:
            same = matplotlib.colormaps[value.name].resampled(value.N)
            if np.allclose(same(np.linspace(0, 1, value.N)), value(np.linspace(0, 1, value.N))):
                record.need("import matplotlib")
                return _Code(f"matplotlib.colormaps[{_py(value.name)}].resampled({value.N})")
        except (KeyError, ValueError):
            pass
        record.need("from matplotlib.colors import ListedColormap")
        rgba = [colors.to_hex(x, keep_alpha=True) for x in value(np.linspace(0, 1, value.N))]
        return _Code(f"ListedColormap({_py(rgba)}, name={_py(value.name)})")
    if isinstance(value, colors.Normalize):
        kind = type(value).__name__
        record.need(f"from matplotlib.colors import {kind}")
        return _Code(_call(kind, {"vmin": value.vmin, "vmax": value.vmax}))
    return None


def from_options(options, page=""):
    """The Figures of a page from the report's figure options, (on, folder,
    about) as validate() and compare() fill them in, or None for none."""
    if not options:
        return Figures(False)
    on, folder, about = options
    return Figures(on, folder, page or os.path.basename(folder), about)
