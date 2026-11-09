"""oceanval.model_expr: a model variable as one name, a sum or a formula in
the language of CDO's expr, checked as CDO reads it, and the window's own
reading of it (OceanValGUI.expression in gui.js), which must say the same."""

import itertools
import json
import os
import random
import shutil
import subprocess
import sys

import numpy as np
import pytest
import xarray as xr

from oceanval import model_expr as me

GUI_JS = os.path.join(os.path.dirname(me.__file__), "data", "gui.js")


class TestWhatIsParsed:
    @pytest.mark.parametrize(
        "text, kind, names, normalised",
        [
            ("P1_Chl", me.NAME, ["P1_Chl"], "P1_Chl"),
            (" P1_Chl ", me.NAME, ["P1_Chl"], "P1_Chl"),
            ("(P1_Chl)", me.NAME, ["P1_Chl"], "P1_Chl"),
            # a text with no operator in it is one variable, whatever its name, as before formulas
            ("2d_sst", me.NAME, ["2d_sst"], "2d_sst"),
            ("odd.name", me.NAME, ["odd.name"], "odd.name"),
            ("P1_Chl+P2_Chl", me.SUM, ["P1_Chl", "P2_Chl"], "P1_Chl+P2_Chl"),
            (" P1_Chl + P2_Chl ", me.SUM, ["P1_Chl", "P2_Chl"], "P1_Chl+P2_Chl"),
            ("(a + b) + c", me.SUM, ["a", "b", "c"], "a+b+c"),
            ("a.b+c", me.SUM, ["a.b", "c"], "a.b+c"),
            ("P1_Chl+2*P2_Chl", me.FORMULA, ["P1_Chl", "P2_Chl"], "P1_Chl+2*P2_Chl"),
            ("P1_Chl + 2 * P2_Chl", me.FORMULA, ["P1_Chl", "P2_Chl"], "P1_Chl+2*P2_Chl"),
            ("(N3_n+N4_n)/2", me.FORMULA, ["N3_n", "N4_n"], "(N3_n+N4_n)/2"),
            # a name twice is not a sum of the names
            ("a+a", me.FORMULA, ["a"], "a+a"),
            ("a-b", me.FORMULA, ["a", "b"], "a-b"),
            ("max(thetao, 0)", me.FORMULA, ["thetao"], "max(thetao,0)"),
            ("a > 0.5 ? a : 0", me.FORMULA, ["a"], "a>0.5?a:0"),
            ("isMissval(a) ? 0 : a", me.FORMULA, ["a"], "isMissval(a)?0:a"),
            ("-a^2", me.FORMULA, ["a"], "-a^2"),
            ("a*-b", me.FORMULA, ["a", "b"], "a*-b"),
            ("a--b", me.FORMULA, ["a", "b"], "a--b"),
            (".5*a+1e-3*b+1.5E+3*c+5.*d", me.FORMULA, ["a", "b", "c", "d"], ".5*a+1e-3*b+1.5E+3*c+5.*d"),
            ("a*M_PI/M_E", me.FORMULA, ["a"], "a*M_PI/M_E"),
            # a variable named as a function is a variable, unless it is called
            ("min(min, ln)", me.FORMULA, ["min", "ln"], "min(min,ln)"),
            ("x ? (y ? 1 : 2) : z", me.FORMULA, ["x", "y", "z"], "x?(y?1:2):z"),
            ("(x ? y : z) ? 1 : 2", me.FORMULA, ["x", "y", "z"], "(x?y:z)?1:2"),
            ("1 ? x : 2", me.FORMULA, ["x"], "1?x:2"),
        ],
    )
    def test_it_is_read_as_cdo_reads_it(self, text, kind, names, normalised):
        found = me.parse(text)
        assert (found.kind, found.names, found.text) == (kind, names, normalised)
        assert me.kind(text) == kind and me.names(text) == names and me.normalise(text) == normalised

    @pytest.mark.parametrize(
        "text, words, where",
        [
            ("", "Give a formula", (0, 0)),
            ("P1_Chl+", "A + needs something on each side", (6, 7)),
            ("+P1_Chl", "A + needs something on each side", (0, 1)),
            ("a*", "A * needs something on each side", (1, 2)),
            ("a+*b", "A + needs something on each side", (1, 2)),
            ("2.5x*a", "Write 2.5*x, not 2.5x", (0, 4)),
            ("1e-3x", "“1e-3x” is not a number", (0, 5)),
            ("P1_Chl**2", "Use ^ for powers, not **", (6, 8)),
            ("a and b", "Use &&, not “and”", (2, 5)),
            ("a or b", "Use ||, not “or”", (2, 4)),
            ("a & b", "Use &&, not &", (2, 3)),
            ("a | b", "Use ||, not |", (2, 3)),
            ("a exp 2", "Use ^ for powers", (2, 5)),
            ("2P1_Chl*a", "Write 2*P1_Chl, not 2P1_Chl", (0, 7)),
            ("5e*x", "“5e” is not a number", (0, 2)),
            ("1.2.3*x", "“1.2.3” is not a number", (0, 5)),
            ("(a+b", "There is a ( with no ) after it", (0, 1)),
            ("a+b)", "There is a ) with no ( before it", (3, 4)),
            ("()", "There is nothing between ( and )", (0, 2)),
            ("a b", "Put an operator (such as + or *) between “a” and “b”", (2, 3)),
            ("a,b", "A comma can only part the values a function is given", (1, 2)),
            ("a:b", "There is a : with no ? before it", (1, 2)),
            ("a?b", "The ? needs a :", (3, 3)),
            ("x?y?1:2:z", "Put brackets around the ?: inside another", (3, 4)),
            ("x?y:z?1:2", "Put brackets around the ?: inside another", (5, 6)),
            ("max(x?1:2,y)", "Put brackets around a ?: given to max", (5, 6)),
            ("1?2:x*0+3", None, None),
            ("1?2:3", "A ?: needs a variable of the simulation in it", (0, 5)),
            ("max(a)", "max takes 2 values, not 1", (0, 6)),
            ("max(a,b,c)", "max takes 2 values, not 3", (0, 10)),
            ("abs()", "abs takes 1 value, not 0", (0, 5)),
            ("max(a,)", "Give a value after the comma", (5, 6)),
            ("fldmean(a)", "“fldmean” works out a statistic over the grid", (0, 7)),
            ("vertmean(a)", "“vertmean” works out a statistic over the levels", (0, 8)),
            ("zonmean(a)", "“zonmean” works out a statistic over each line of latitude", (0, 7)),
            ("sellevel(a,1)", "“sellevel” chooses levels", (0, 8)),
            ("clat(a)", "“clat” gives the grid's coordinates", (0, 4)),
            ("missval(a)", "“missval” gives a property of a variable", (0, 7)),
            ("a*cyear()", "“cyear” depends on the time step", (2, 7)),
            ("remove(a)", "“remove” removes a variable", (0, 6)),
            ("a+rand(a)", "“rand” gives random numbers", (2, 6)),
            ("foo(a)", "“foo” is not a function OceanVal can use in a formula. Did you mean floor?", (0, 3)),
            ("ABS(a)", "Did you mean abs?", (0, 3)),
            ("x(y)", "“x” is not a function OceanVal can use", (0, 1)),
            ("M_PI(a)", "M_PI is a number, not a function", (0, 4)),
            ("a=b", "Give only the formula, without “=”", (1, 2)),
            ("v=a;", "Give only the formula, without “=”", (1, 2)),
            ("a;b", "Give one formula, without “;”", (1, 2)),
            ("a%b*2", "“%” cannot be used in a formula", (1, 2)),
            ("_ALL_*2", "_ALL_ cannot be used in a formula", (0, 5)),
            ("1+2", "The formula has no variable of the simulation in it", (0, 3)),
            ("M_PI", "The formula has no variable of the simulation in it", (0, 4)),
            ("2", "The formula has no variable of the simulation in it", (0, 1)),
            ("a/0", "The formula divides by 0", (0, 3)),
            ("mod(a,0)", "The formula divides by 0", (0, 8)),
        ],
    )
    def test_what_cdo_could_not_work_out_is_refused_saying_why_and_where(self, text, words, where):
        if words is None:
            me.parse(text)
            return
        with pytest.raises(me.ExpressionError) as caught:
            me.parse(text)
        assert words in caught.value.message
        assert (caught.value.start, caught.value.end) == where
        assert me.problem(text).message == caught.value.message

    def test_the_place_that_is_wrong_is_marked(self):
        error = me.problem("P1_Chl+2*")
        assert error.pointer() == "P1_Chl+2*\n        ^"

    def test_a_text_that_cannot_be_parsed_is_read_as_before(self):
        # the helpers never raise: what cannot be parsed is names joined by +, as before formulas
        assert me.names("a+(b") == ["a", "(b"]
        assert me.kind("a+(b") == me.SUM
        assert me.normalise(" a + (b ") == "a+(b"
        assert me.tidied(" max(a, ") == "max(a,"
        assert me.tidied(" a + b ") == "a+b"
        assert me.tree("a+b") == ["op", "+", ["var", "a"], ["var", "b"]]

    def test_the_operator_for_cdo_is_quoted_and_has_no_spaces(self):
        # nctoolkit runs CDO in a shell and splits commands at spaces
        assert me.cdo_operator("total", "P1_Chl + 2 * P2_Chl") == "-expr,'total=P1_Chl+2*P2_Chl;'"
        assert me.cdo_operator("chl", "a+b", append=True) == "-aexpr,'chl=a+b;'"
        assert me.cdo_operator("v", "a > 0 ? a : 0") == "-expr,'v=a>0?a:0;'"

    def test_what_it_is_made_of_is_said(self):
        assert me.describe("P1_Chl") == ""
        assert me.describe("P1_Chl+P2_Chl") == "Sum of P1_Chl, P2_Chl"
        assert me.describe("P1_Chl+2*P2_Chl") == "Expression based on P1_Chl, P2_Chl"
        assert me.describe("P1_Chl+") == ""

    def test_the_tables_the_window_reads_are_json(self):
        tables = json.loads(json.dumps(me.tables()))
        assert tables["functions"]["max"] == 2 and tables["functions"]["sqrt"] == 1
        assert "fldmean" in tables["refused"] and tables["constants"] == ["M_E", "M_PI"]
        assert not set(tables["functions"]) & set(tables["refused"])


class TestWorkingItOut:
    """evaluate works a formula out as CDO's expr does, for raw FVCOM output,
    which CDO cannot read (see oceanval.fvcom_climatology)."""

    def values(self):
        return {"a": np.array([np.nan, 0.0, 2.0, 3.0]), "b": np.array([1.0, np.nan, 0.0, 2.0])}

    def work_out(self, text):
        return me.evaluate(me.parse(text).tree, self.values())

    def test_a_missing_value_in_a_variable_is_missing(self):
        np.testing.assert_array_equal(self.work_out("a+b"), [np.nan, np.nan, 2.0, 5.0])
        np.testing.assert_array_equal(self.work_out("a>b"), [np.nan, np.nan, 1.0, 1.0])
        np.testing.assert_array_equal(self.work_out("max(a,b)"), [np.nan, np.nan, 2.0, 3.0])
        np.testing.assert_array_equal(self.work_out("a^0"), [np.nan, 1.0, 1.0, 1.0])

    def test_nought_times_anything_is_nought(self):
        # as CDO multiplies: 0 times a missing value is 0
        np.testing.assert_array_equal(self.work_out("a*b"), [np.nan, 0.0, 0.0, 6.0])

    def test_a_division_by_nought_is_missing(self):
        np.testing.assert_array_equal(self.work_out("a/b"), [np.nan, np.nan, np.nan, 1.5])

    def test_a_ternary_is_missing_only_where_its_condition_or_its_choice_is(self):
        np.testing.assert_array_equal(self.work_out("b?a:7"), [np.nan, np.nan, 7.0, 3.0])
        np.testing.assert_array_equal(self.work_out("isMissval(a)?0:a"), [0.0, 0.0, 2.0, 3.0])

    def test_logic_gives_one_or_nought(self):
        np.testing.assert_array_equal(self.work_out("!a"), [np.nan, 1.0, 0.0, 0.0])
        np.testing.assert_array_equal(self.work_out("a&&b"), [np.nan, np.nan, 0.0, 1.0])
        np.testing.assert_array_equal(self.work_out("a<=>b"), [np.nan, np.nan, 1.0, 1.0])

    def test_precedence_is_cdos(self):
        values = {"a": np.array([2.0]), "b": np.array([1.0]), "c": np.array([0.0])}
        work_out = lambda text: float(me.evaluate(me.parse(text).tree, values)[0])  # noqa: E731
        assert work_out("-a^2") == -4.0
        assert work_out("a^b^a") == 2.0
        # the comparisons are one level, from the left
        assert work_out("a<b==c") == 1.0
        assert work_out("b==a<b") == 1.0
        # && and || are one level, from the left
        assert work_out("b||b&&c") == 0.0
        assert work_out("!c*a") == 2.0


def _cdo():
    return shutil.which("cdo")


def _render(tree):
    """tree written with as few brackets as CDO's precedence needs, so that a
    precedence OceanVal had wrong would read otherwise in CDO."""
    level = {op: index for index, ops in enumerate(me._LEVELS_OF) for op in ops}

    def tightness(node):
        if node[0] == "if":
            return -1
        if node[0] == "op":
            return 10 if node[1] == "^" else level[node[1]]
        if node[0] in ("neg", "not"):
            return 9
        return 11

    def write(node):
        kind = node[0]
        if kind == "num":
            return repr(node[1])
        if kind in ("var", "const"):
            return node[1]
        if kind == "call":
            return f"{node[1]}({','.join(('(' + write(a) + ')') if tightness(a) < 0 else write(a) for a in node[2])})"
        if kind in ("neg", "not"):
            inner = write(node[1])
            return ("-" if kind == "neg" else "!") + (f"({inner})" if tightness(node[1]) < 9 else inner)
        if kind == "if":
            parts = [f"({write(part)})" if tightness(part) < 0 else write(part) for part in node[1:]]
            return f"{parts[0]}?{parts[1]}:{parts[2]}"
        operator, left, right = node[1], node[2], node[3]
        own = tightness(node)
        a, b = write(left), write(right)
        if operator == "^":
            a = f"({a})" if tightness(left) <= 10 else a
            b = f"({b})" if tightness(right) < 9 else b
        else:
            a = f"({a})" if tightness(left) < own else a
            b = f"({b})" if tightness(right) <= own else b
        return f"{a}{operator}{b}"

    return write(tree)


def _random_tree(depth, rng):
    # no function or power that can fail on a value (sqrt(-1), ln(0), 0^-1): those give a NaN
    # CDO itself treats unevenly, which is not what is checked here
    operators = [op for ops in me._LEVELS_OF for op in ops]
    one = ["abs", "floor", "ceil", "int", "nint", "sqr", "sin", "cos", "atan", "tanh", "rad", "deg", "isMissval", "float"]
    two = ["mod", "min", "max", "hypot", "atan2"]
    pick = rng.random()
    if depth == 0 or pick < 0.2:
        leaf = rng.random()
        if leaf < 0.6:
            return ["var", rng.choice("xyz")]
        if leaf < 0.7:
            return ["const", rng.choice(["M_PI", "M_E"])]
        return ["num", rng.choice([0.0, 0.5, 1.0, 2.0, 3.0])]
    if pick < 0.55:
        return ["op", rng.choice(operators), _random_tree(depth - 1, rng), _random_tree(depth - 1, rng)]
    if pick < 0.6:
        return ["op", "^", _random_tree(depth - 1, rng), ["num", rng.choice([2.0, 3.0])]]
    if pick < 0.7:
        return [rng.choice(["neg", "not"]), _random_tree(depth - 1, rng)]
    if pick < 0.8:
        return ["if", _random_tree(depth - 1, rng), _random_tree(depth - 1, rng), _random_tree(depth - 1, rng)]
    if pick < 0.9:
        return ["call", rng.choice(one), [_random_tree(depth - 1, rng)]]
    return ["call", rng.choice(two), [_random_tree(depth - 1, rng), _random_tree(depth - 1, rng)]]


@pytest.mark.skipif(_cdo() is None, reason="CDO is not installed")
class TestTheSameAsCDO:
    """Random formulas, written with as few brackets as OceanVal's precedence
    needs, are worked out by CDO itself and by evaluate on every combination
    of values (missing ones too): they must agree, so the grammar's precedence
    and evaluate are CDO's."""

    COUNT = 120

    def test_cdo_works_them_out_as_oceanval_does(self, tmp_path):
        values = [np.nan, -2.0, 0.0, 0.5, 1.0, 2.5]
        combos = np.array(list(itertools.product(values, repeat=3)))
        ds = xr.Dataset(
            {name: (("lat", "lon"), combos[:, i][None, :]) for i, name in enumerate("xyz")},
            coords={"lat": [0.0], "lon": np.arange(len(combos), dtype=float)},
        )
        for name in "xyz":
            ds[name].encoding["_FillValue"] = 1e20
        source = tmp_path / "values.nc"
        ds.to_netcdf(source)
        given = {name: ds[name].values.ravel() for name in "xyz"}
        rng = random.Random(20261109)
        checked = 0
        while checked < self.COUNT:
            text = _render(_random_tree(3, rng))
            try:
                found = me.parse(text)
            except me.ExpressionError as error:
                # numbers alone, which CDO refuses too
                assert "variable" in error.message or "divides by 0" in error.message, (text, error.message)
                continue
            out = tmp_path / "out.nc"
            done = subprocess.run(
                ["cdo", "-s", "-O", "-b", "F64", f"-expr,v={found.text};", str(source), str(out)],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
            )
            assert done.returncode == 0, (text, done.stderr)
            with xr.open_dataset(out) as result:
                cdo_values = result.v.values.ravel().astype("float64")
            ours = me.evaluate(found.tree, given)
            same = np.isclose(cdo_values, ours, rtol=1e-4, atol=1e-5, equal_nan=True)
            assert same.all(), (text, combos[~same][:3], cdo_values[~same][:3], ours[~same][:3])
            checked += 1


def _node():
    return shutil.which("node")


@pytest.mark.skipif(_node() is None, reason="node is not installed")
class TestTheWindowReadsItTheSame:
    """OceanValGUI.expression (gui.js) is a port of parse: on the same texts it
    says the same, its kind, names and normalised text, or the same mistake at
    the same place (its "Did you mean" is only for a function of another case)."""

    def test_gui_js_and_model_expr_agree(self):
        source = open(GUI_JS, encoding="utf-8").read()
        start = source.index("  // ---- a model variable: one variable")
        end = source.index("  // how long typing pauses")
        block = source[start:end].replace(
            "/*__OCEANVAL_EXPR_TABLES__*/ { functions: {}, constants: [], refused: {} }", json.dumps(me.tables())
        )
        rng = random.Random(7)
        pieces = ["a", "b", "P1_Chl", "2", ".5", "1e3", "+", "-", "*", "/", "^", "(", ")", ",", "?", ":", "<", "==",
                  "&&", "!", "max", "abs", " ", "M_PI", "fldmean", "1.", "**", "=", ";", "and"]
        texts = [text for text, *_ in TestWhatIsParsed.test_it_is_read_as_cdo_reads_it.pytestmark[0].args[1]]
        texts += [text for text, *_ in TestWhatIsParsed.test_what_cdo_could_not_work_out_is_refused_saying_why_and_where.pytestmark[0].args[1]]
        texts += ["".join(rng.choice(pieces) for _ in range(rng.randint(1, 9))) for _ in range(1500)]
        script = block + f"\nprocess.stdout.write(JSON.stringify({json.dumps(texts)}.map((text) => expression(text))));"
        replies = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
        for text, js in zip(texts, replies):
            if not text.strip():
                assert js["empty"]
                continue
            try:
                found = me.parse(text)
            except me.ExpressionError as error:
                assert not js["ok"], text
                assert js["error"]["message"].split(" Did you mean")[0] == error.message.split(" Did you mean")[0], text
                assert (js["error"]["start"], js["error"]["end"]) == (error.start, error.end), text
                continue
            assert js["ok"], (text, js)
            assert (js["kind"], js["names"], js["text"]) == (found.kind, found.names, found.text), text
            assert js["description"] == me.describe(text), text


def test_fvcom_climatology_reads_formulas_when_run_by_its_path():
    """fvcom_climatology runs as a script by its path (python -I), importing no
    OceanVal: it loads model_expr by its path, and works a formula out with it."""
    from oceanval import fvcom_climatology

    path = fvcom_climatology.__file__
    code = (
        "import importlib.util, sys, numpy as np\n"
        f"spec = importlib.util.spec_from_file_location('fc', {path!r})\n"
        "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n"
        "assert 'oceanval' not in sys.modules, sorted(m for m in sys.modules if m.startswith('oceanval'))\n"
        "values = {'a': np.array([1.0, np.nan]), 'b': np.array([2.0, 3.0])}\n"
        "print(module._combined('a+2*b', values).tolist(), module._combined(['a', 'b'], values).tolist())\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.split("] [") == ["[5.0, nan", "3.0, nan]\n"]
    # imported as part of the package, it is the package's model_expr
    assert fvcom_climatology.model_expr is me
    assert fvcom_climatology.names_of("a+2*b") == ["a", "b"] and fvcom_climatology.names_of(["a"]) == ["a"]
    assert fvcom_climatology.parts_of({"parts": ["a", "b"], "formula": None}) == ["a", "b"]
    assert fvcom_climatology.parts_of({"parts": ["a", "b"], "formula": "a+2*b"}) == "a+2*b"
