"""A model variable: one variable of the simulation, a sum of several
(``P1_Chl+P2_Chl``), or a formula of them in the language of CDO's ``expr``
operator (``P1_Chl+2*P2_Chl``, ``(N3_n+N4_n)/2``, ``max(thetao,0)``).

This is the one place a model variable is read. ``parse`` checks a formula
against CDO's grammar, as CDO 2.4 and 2.6 read it, and says what is wrong and
where; ``names`` gives the simulation variables it needs, ``kind`` whether it
is one name, a sum or a formula, and ``cdo_operator`` the CDO operator that
works it out, which nctoolkit's ``cdo_command`` takes.

What CDO's expr takes, and OceanVal with it (tightest last):

- ``c ? x : y`` (a ternary inside another needs brackets);
- ``&&`` and ``||``, at one level, from the left;
- the comparisons ``==``, ``!=``, ``<``, ``>``, ``<=``, ``>=`` and ``<=>``
  (-1, 0 or 1), all at one level, from the left (``a<b==c`` is
  ``(a<b)==c``);
- ``+`` and ``-``;
- ``*`` and ``/``;
- ``-x`` and ``!x``;
- ``x ^ y``, from the right, and tighter than ``-x`` (``-2^2`` is -4);
- brackets, numbers (``5``, ``5.``, ``.5``, ``1e-3``), ``M_PI`` and ``M_E``,
  and the functions of FUNCTIONS, which work on each value on its own (a
  ternary given to a function needs brackets).

Functions that work out a statistic over the grid or the levels, choose
levels, or depend on the grid or the time step are refused (REFUSED): OceanVal
chooses the surface, bottom or water column itself, after the formula. A
missing value in any variable is missing in the result, as CDO takes it.

A text with no operator, bracket, comma or space in it is one variable,
whatever its name, as before formulas could be given. This module imports
nothing of OceanVal.
"""

import difflib
import math
import re

# the functions OceanVal takes in a formula, and how many values each takes. All
# work on each value on its own, as CDO's expr documents them
FUNCTIONS = {
    **{
        name: 1
        for name in (
            "abs", "floor", "ceil", "float", "int", "nint", "sqr", "sqrt", "exp", "ln", "log10",
            "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh", "asinh", "acosh",
            "atanh", "rad", "deg", "isMissval",
        )
    },
    **{name: 2 for name in ("mod", "min", "max", "pow", "hypot", "atan2")},
}

# CDO's named constants
CONSTANTS = {"M_PI": math.pi, "M_E": math.e}

_STATISTIC = "works out a statistic over the grid, which OceanVal does itself after the formula"
_ZONAL = "works out a statistic over each line of latitude, which a formula cannot be used for"
_VERTICAL = (
    "works out a statistic over the levels: OceanVal takes the surface, the bottom or the water column "
    "itself, after the formula"
)
_LEVELS = "chooses levels: OceanVal takes the surface, the bottom or the water column itself, after the formula"
_GRID = "gives the grid's coordinates or cells, not the variables' values, so it cannot be used in a formula"
_SIZE = "gives a property of a variable, not its values, so it cannot be used in a formula"
_TIME = "depends on the time step, which OceanVal chooses itself, so it cannot be used in a formula"

# CDO's other functions, which a formula cannot use, and why
REFUSED = {
    **{
        f"fld{stat}": _STATISTIC
        for stat in ("min", "max", "range", "sum", "mean", "avg", "std", "std1", "var", "var1", "skew", "kurt", "median")
    },
    **{
        f"zon{stat}": _ZONAL
        for stat in ("min", "max", "range", "sum", "mean", "avg", "std", "std1", "var", "var1", "skew", "kurt", "median")
    },
    **{
        f"vert{stat}": _VERTICAL
        for stat in ("min", "max", "range", "sum", "mean", "avg", "std", "std1", "var", "var1")
    },
    **{name: _LEVELS for name in ("sellevel", "sellevidx", "sellevelrange", "sellevidxrange")},
    **{name: _GRID for name in ("clon", "clat", "clev", "clevidx", "cthickness", "gridarea", "gridindex")},
    **{name: _SIZE for name in ("ngp", "nlev", "size", "missval")},
    **{
        name: _TIME
        for name in (
            "ctimestep", "cdate", "ctime", "cdeltat", "cday", "cmonth", "cyear", "csecond", "cminute", "chour",
        )
    },
    "remove": "removes a variable from CDO's output, so it cannot be used in a formula",
    "rand": "gives random numbers, so it cannot be used in a formula",
    "trimrel": "only trims how precisely the values are stored, so it cannot be used in a formula",
    "trimabs": "only trims how precisely the values are stored, so it cannot be used in a formula",
}

# the kinds of model variable
NAME = "name"
SUM = "sum"
FORMULA = "formula"

# the operators, longest first, as the tokenizer looks for them
_OPERATORS = ("<=>", "==", "!=", "<=", ">=", "&&", "||", "<", ">", "+", "-", "*", "/", "^", "!", "?", ":", "(", ")", ",")
# the levels of the binary operators, loosest first: each is from the left
_LEVELS_OF = (("&&", "||"), ("==", "!=", "<=>", "<", ">", "<=", ">="), ("+", "-"), ("*", "/"))
# a text with none of these in it is one variable, whatever its name
_FORMULA_CHARACTERS = re.compile(r"[\s+\-*/^()<>=!?:&|,;]")
_NUMBER = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_WORD = re.compile(r"[A-Za-z0-9_.]+")


class ExpressionError(ValueError):
    """What is wrong with a formula, and where: text[start:end] is the part
    that is wrong (start == end for something missing, at that place)."""

    def __init__(self, message, text="", start=0, end=None):
        super().__init__(message)
        self.message = message
        self.text = text
        self.start = start
        self.end = start if end is None else end

    def pointer(self):
        """The formula, and a line marking the place that is wrong under it."""
        width = max(1, self.end - self.start)
        return f"{self.text}\n{' ' * self.start}{'^' * width}"


class Expression:
    """A model variable, parsed: text is it as CDO is given it (no spaces),
    names the simulation variables it needs (each once, in the order they come),
    kind NAME, SUM or FORMULA, and tree it as nested lists (see evaluate)."""

    def __init__(self, text, names, kind, tree):
        self.text = text
        self.names = names
        self.kind = kind
        self.tree = tree

    def __repr__(self):
        return f"Expression({self.text!r}, kind={self.kind!r}, names={self.names!r})"


class _Token:
    __slots__ = ("kind", "text", "start", "end")

    def __init__(self, kind, text, start, end):
        self.kind = kind
        self.text = text
        self.start = start
        self.end = end


def is_one_name(text):
    """Whether text is one variable, as it is, whatever its name: it has no
    operator, bracket, comma or space in it."""
    text = str(text).strip()
    return (
        bool(text)
        and not _FORMULA_CHARACTERS.search(text)
        and not _NUMBER.fullmatch(text)
        and text not in CONSTANTS
    )


def _tokens(text):
    found = []
    i = 0
    while i < len(text):
        char = text[i]
        if char.isspace():
            i += 1
            continue
        if char.isdigit() or (char == "." and i + 1 < len(text) and text[i + 1].isdigit()):
            number = _NUMBER.match(text, i)
            # a number ends where something that is not a letter, a digit, _ or . comes (1e-3 is one)
            following = _WORD.match(text, number.end())
            if following is None:
                found.append(_Token("number", number.group(), i, number.end()))
                i = number.end()
                continue
            word = _WORD.match(text, i)
            word = word if word.end() >= following.end() else following
            whole = text[i:max(word.end(), following.end())]
            word_end = i + len(whole)
            digits = re.match(r"\d+(?:\.\d*)?", whole)
            rest = whole[digits.end():] if digits else ""
            if digits and _NAME.fullmatch(rest) and not re.fullmatch(r"[eE]\d*", rest):
                raise ExpressionError(
                    f"Write {digits.group()}*{rest}, not {whole}: CDO needs a * between a number and a variable.",
                    text, i, word_end,
                )
            raise ExpressionError(f"“{whole}” is not a number.", text, i, word_end)
        if char.isalpha() or char == "_":
            name = _NAME.match(text, i)
            found.append(_Token("name", name.group(), i, name.end()))
            i = name.end()
            continue
        if text.startswith("**", i):
            raise ExpressionError("Use ^ for powers, not **.", text, i, i + 2)
        operator = next((op for op in _OPERATORS if text.startswith(op, i)), None)
        if operator is not None:
            found.append(_Token("op", operator, i, i + len(operator)))
            i += len(operator)
            continue
        if char == "=":
            raise ExpressionError(
                "Give only the formula, without “=”: OceanVal names the result itself (use == to compare).",
                text, i, i + 1,
            )
        if char == ";":
            raise ExpressionError("Give one formula, without “;”.", text, i, i + 1)
        if char in "&|":
            raise ExpressionError(f"Use {char * 2}, not {char}.", text, i, i + 1)
        raise ExpressionError(f"“{char}” cannot be used in a formula.", text, i, i + 1)
    found.append(_Token("end", "", len(text), len(text)))
    return found


class _Parser:
    def __init__(self, text):
        self.text = text
        self.tokens = _tokens(text)
        self.at = 0
        self.names = []

    def peek(self):
        return self.tokens[self.at]

    def take(self):
        token = self.tokens[self.at]
        self.at += 1
        return token

    def fail(self, message, token=None, start=None, end=None):
        token = token or self.peek()
        raise ExpressionError(
            message, self.text, token.start if start is None else start, token.end if end is None else end
        )

    def is_op(self, *ops):
        token = self.peek()
        return token.kind == "op" and token.text in ops

    def whole(self):
        if self.peek().kind == "end":
            self.fail("Give a formula.")
        tree = self.ternary()
        token = self.peek()
        if token.kind != "end":
            self.unexpected(token)
        return tree

    def unexpected(self, token):
        """token where an operator or the end was wanted."""
        before = self.tokens[self.at - 1] if self.at else None
        if token.kind == "op" and token.text == ")":
            self.fail("There is a ) with no ( before it.", token)
        if token.kind == "op" and token.text == "?":
            self.fail("Put brackets around the ?: inside another: CDO cannot tell which is which.", token)
        if token.kind == "op" and token.text == ":":
            self.fail("There is a : with no ? before it.", token)
        if token.kind == "op" and token.text == ",":
            self.fail("A comma can only part the values a function is given.", token)
        if token.kind == "op" and token.text == "(" and before is not None and before.kind == "name":
            self.fail(f"“{before.text}” is not a CDO function.", before, end=token.end)
        if token.kind == "name" and token.text in ("and", "or"):
            self.fail(f"Use {'&&' if token.text == 'and' else '||'}, not “{token.text}”.", token)
        if token.kind == "name" and token.text == "exp":
            self.fail("Use ^ for powers: exp(x) is e to the power of x.", token)
        shown = token.text or "the end"
        prior = before.text if before is not None else ""
        self.fail(f"Put an operator (such as + or *) between “{prior}” and “{shown}”.", token)

    def ternary(self):
        start = self.peek()
        condition = self.binary(0)
        if not self.is_op("?"):
            return condition
        self.take()
        yes = self.binary(0)
        if self.is_op("?"):
            self.fail("Put brackets around the ?: inside another: CDO cannot tell which is which.")
        if not self.is_op(":"):
            self.fail(f"The ? needs a : after “{self.text[start.start:self.peek().start].strip()}”.")
        self.take()
        no = self.binary(0)
        if self.is_op("?"):
            self.fail("Put brackets around the ?: inside another: CDO cannot tell which is which.")
        found = ["if", condition, yes, no]
        if not _has_variable(found):
            # CDO: "expr?expr:expr: no data variable found!"
            self.fail(
                "A ?: needs a variable of the simulation in it: CDO cannot work one out of numbers alone.",
                start=start.start, end=self.tokens[self.at - 1].end,
            )
        return found

    def binary(self, level):
        if level == len(_LEVELS_OF):
            return self.unary()
        left = self.binary(level + 1)
        while self.is_op(*_LEVELS_OF[level]):
            operator = self.take()
            self.operand_after(operator)
            right = self.binary(level + 1)
            left = ["op", operator.text, left, right]
        return left

    def operand_after(self, operator):
        """An operator needs something after it."""
        token = self.peek()
        if token.kind == "end" or (token.kind == "op" and token.text in (")", ",", ":", "?") + _BINARY_ONLY):
            self.fail(f"A {operator.text} needs something on each side.", operator)

    def unary(self):
        token = self.peek()
        if token.kind == "op" and token.text in ("-", "!"):
            self.take()
            self.operand_after(token)
            return ["neg" if token.text == "-" else "not", self.unary()]
        if token.kind == "op" and token.text == "+":
            self.fail("A + needs something on each side.", token)
        if token.kind == "op" and token.text in _BINARY_ONLY:
            self.fail(f"A {token.text} needs something on each side.", token)
        return self.power()

    def power(self):
        base = self.primary()
        if self.is_op("^"):
            operator = self.take()
            self.operand_after(operator)
            return ["op", "^", base, self.unary()]
        return base

    def primary(self):
        token = self.take()
        if token.kind == "number":
            return ["num", float(token.text)]
        if token.kind == "op" and token.text == "(":
            if self.is_op(")"):
                self.fail("There is nothing between ( and ).", start=token.start, end=self.peek().end)
            inner = self.ternary()
            if not self.is_op(")"):
                if self.peek().kind == "end":
                    self.fail("There is a ( with no ) after it.", token)
                self.unexpected(self.peek())
            self.take()
            return inner
        if token.kind == "name":
            if self.is_op("("):
                return self.call(token)
            if token.text in CONSTANTS:
                return ["const", token.text]
            if token.text == "_ALL_":
                self.fail("_ALL_ cannot be used in a formula: name the variables.", token)
            if token.text not in self.names:
                self.names.append(token.text)
            return ["var", token.text]
        if token.kind == "end":
            self.fail("The formula ends too soon.", token)
        self.at -= 1
        self.unexpected(token)

    def call(self, name):
        function = name.text
        if function in REFUSED:
            self.fail(f"“{function}” {REFUSED[function]}.", name)
        if function not in FUNCTIONS:
            close = difflib.get_close_matches(function.lower(), [f.lower() for f in FUNCTIONS], n=1, cutoff=0.6)
            hint = ""
            if close:
                hint = " Did you mean " + next(f for f in FUNCTIONS if f.lower() == close[0]) + "?"
            if function in CONSTANTS:
                self.fail(f"{function} is a number, not a function.", name)
            self.fail(f"“{function}” is not a function OceanVal can use in a formula.{hint}", name)
        opening = self.take()
        args = []
        if not self.is_op(")"):
            while True:
                if self.peek().kind == "end":
                    self.fail(f"There is a ( with no ) after it, in {function}(.", opening)
                args.append(self.binary(0))
                if self.is_op("?"):
                    self.fail(f"Put brackets around a ?: given to {function}: CDO cannot read it without them.")
                if self.is_op(","):
                    comma = self.take()
                    if self.is_op(")"):
                        self.fail("Give a value after the comma.", comma)
                    continue
                break
        if not self.is_op(")"):
            if self.peek().kind == "end":
                self.fail(f"There is a ( with no ) after it, in {function}(.", opening)
            self.unexpected(self.peek())
        closing = self.take()
        wanted = FUNCTIONS[function]
        if len(args) != wanted:
            self.fail(
                f"{function} takes {wanted} value{'s' if wanted > 1 else ''}, not {len(args)}.",
                name, end=closing.end,
            )
        return ["call", function, args]


# operators that can only be between two values
_BINARY_ONLY = ("*", "/", "^", "==", "!=", "<=>", "<", ">", "<=", ">=", "&&", "||")


def _has_variable(tree):
    if tree[0] == "var":
        return True
    children = tree[2] if tree[0] == "call" else [part for part in tree[1:] if isinstance(part, list)]
    return any(_has_variable(child) for child in children)


def _is_sum(tree):
    if tree[0] == "var":
        return True
    return tree[0] == "op" and tree[1] == "+" and _is_sum(tree[2]) and _is_sum(tree[3])


def _divides_by_zero(tree):
    if tree[0] == "op" and tree[1] == "/" and tree[3] == ["num", 0.0]:
        return True
    if tree[0] == "call" and tree[1] == "mod" and tree[2][1] == ["num", 0.0]:
        return True
    children = tree[2] if tree[0] == "call" else [part for part in tree[1:] if isinstance(part, list)]
    return any(_divides_by_zero(child) for child in children)


def parse(text):
    """The model variable text as an Expression. Raises ExpressionError if
    CDO's expr could not work it out, or OceanVal could not use it (see the
    module's description)."""
    if not isinstance(text, str):
        raise ExpressionError("Give the model variable as text.", "")
    stripped = text.strip()
    if is_one_name(stripped):
        return Expression(stripped, [stripped], NAME, ["var", stripped])
    parser = _Parser(stripped)
    tree = parser.whole()
    if not parser.names:
        raise ExpressionError(
            "The formula has no variable of the simulation in it.", stripped, 0, len(stripped)
        )
    if _divides_by_zero(tree):
        raise ExpressionError("The formula divides by 0, which would make every value missing.", stripped, 0, len(stripped))
    names = parser.names
    if tree[0] == "var":
        # a name in brackets, or with spaces round it
        return Expression(names[0], names, NAME, tree)
    if _is_sum(tree) and len(_sum_names(tree)) == len(names):
        # each name once: a+a is a formula (2a)
        return Expression("+".join(_sum_names(tree)), names, SUM, tree)
    return Expression("".join(token.text for token in parser.tokens), names, FORMULA, tree)


def _sum_names(tree):
    if tree[0] == "var":
        return [tree[1]]
    return _sum_names(tree[2]) + _sum_names(tree[3])


def problem(text):
    """What is wrong with the model variable text, as ExpressionError, or None."""
    try:
        parse(text)
    except ExpressionError as error:
        return error
    return None


def _legacy(text):
    # how a model variable was read before formulas: names joined by +
    return [name.strip() for name in str(text).split("+") if name.strip()]


def names(text):
    """The simulation variables the model variable text needs, each once, in
    the order they come. Never raises: a text that cannot be parsed is read as
    names joined by "+", as it was before formulas."""
    try:
        return parse(text).names
    except ExpressionError:
        return _legacy(text)


def kind(text):
    """NAME, SUM or FORMULA (a text that cannot be parsed is a SUM if it has a
    + in it, else a NAME, as it was read before formulas)."""
    try:
        return parse(text).kind
    except ExpressionError:
        return SUM if len(_legacy(text)) > 1 else NAME


def normalise(text):
    """The model variable text as CDO is given it, without spaces ("a + b" is
    "a+b"). A text that cannot be parsed has its names joined by "+"."""
    try:
        return parse(text).text
    except ExpressionError:
        return "+".join(_legacy(text))


def tidied(text):
    """The model variable text as a form keeps it: normalised if it can be
    parsed, else as it was typed (without the spaces at its ends), so a
    formula half typed is not changed under the user."""
    try:
        return parse(text).text
    except ExpressionError:
        return str(text).strip()


def tree(text):
    """The model variable text as nested lists (see evaluate); a text that
    cannot be parsed is the sum of its names."""
    try:
        return parse(text).tree
    except ExpressionError:
        found = [["var", name] for name in _legacy(text)]
        total = found[0] if found else ["num", 0.0]
        for part in found[1:]:
            total = ["op", "+", total, part]
        return total


def cdo_operator(target, text, append=False):
    """The CDO operator that works out the model variable text as a variable
    named target: -expr (-aexpr, keeping the variables, if append), quoted for
    the shell nctoolkit runs CDO in. The formula has no spaces (nctoolkit splits
    commands at them) and cannot have a quote in it."""
    formula = normalise(text)
    return f"-{'aexpr' if append else 'expr'},'{target}={formula};'"


def describe(text):
    """A line saying what the model variable text is made of: "" for one
    variable, "Sum of a, b" or "Expression based on a, b"."""
    try:
        found = parse(text)
    except ExpressionError:
        return ""
    if found.kind == NAME:
        return ""
    return f"{'Sum of' if found.kind == SUM else 'Expression based on'} {', '.join(found.names)}"


def tables():
    """What the window's own reading of a formula (OceanValGUI.expression)
    needs to know, so the lists are written once: the functions, the
    constants and the functions refused, with why."""
    return {"functions": FUNCTIONS, "constants": sorted(CONSTANTS), "refused": REFUSED}


# ---- working a formula out on values, as CDO would ----


def evaluate(tree, values):
    """The formula tree (see parse) worked out with numpy on values ({name:
    array}), as CDO's expr works it out: a value missing (NaN) in any variable a
    value depends on is missing, comparisons and logic give 1 or 0, and
    anything not finite (x/0, ln(0)) is missing; but 0 times anything is 0, a
    missing value too, as CDO multiplies. A ?: is missing where its condition
    is, and otherwise the value it chooses. The arrays broadcast."""
    import numpy as np

    def finite(array):
        array = np.asarray(array, dtype="float64")
        return np.where(np.isfinite(array), array, np.nan)

    def truth(array):
        return np.where(np.isnan(array), np.nan, (array != 0).astype("float64"))

    def both(a, b, result):
        return np.where(np.isnan(a) | np.isnan(b), np.nan, np.asarray(result, dtype="float64"))

    def walk(node):
        what = node[0]
        if what == "num":
            return np.float64(node[1])
        if what == "const":
            return np.float64(CONSTANTS[node[1]])
        if what == "var":
            return finite(values[node[1]])
        if what == "neg":
            return -walk(node[1])
        if what == "not":
            inner = walk(node[1])
            return np.where(np.isnan(inner), np.nan, (inner == 0).astype("float64"))
        if what == "if":
            condition, yes, no = walk(node[1]), walk(node[2]), walk(node[3])
            return np.where(np.isnan(condition), np.nan, np.where(condition != 0, yes, no))
        if what == "op":
            operator, a, b = node[1], walk(node[2]), walk(node[3])
            with np.errstate(all="ignore"):
                if operator == "+":
                    return finite(a + b)
                if operator == "-":
                    return finite(a - b)
                if operator == "*":
                    return np.where((a == 0) | (b == 0), 0.0, finite(a * b))
                if operator == "/":
                    return finite(np.where(b == 0, np.nan, a / np.where(b == 0, 1.0, b)))
                if operator == "^":
                    return finite(both(a, b, np.power(a, b)))
                if operator == "<=>":
                    return both(a, b, np.sign(a - b))
                compare = {
                    "==": np.equal, "!=": np.not_equal, "<": np.less, ">": np.greater,
                    "<=": np.less_equal, ">=": np.greater_equal,
                }
                if operator in compare:
                    return both(a, b, compare[operator](a, b))
                if operator == "&&":
                    return both(a, b, (a != 0) & (b != 0))
                if operator == "||":
                    return both(a, b, (a != 0) | (b != 0))
            raise ValueError(f"Unknown operator {operator}")
        if what == "call":
            name, args = node[1], [walk(arg) for arg in node[2]]
            with np.errstate(all="ignore"):
                if name == "isMissval":
                    return np.isnan(args[0]).astype("float64")
                one = {
                    "abs": np.abs, "floor": np.floor, "ceil": np.ceil, "sqr": np.square, "sqrt": np.sqrt,
                    "exp": np.exp, "ln": np.log, "log10": np.log10, "sin": np.sin, "cos": np.cos,
                    "tan": np.tan, "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan, "sinh": np.sinh,
                    "cosh": np.cosh, "tanh": np.tanh, "asinh": np.arcsinh, "acosh": np.arccosh,
                    "atanh": np.arctanh, "rad": np.radians, "deg": np.degrees,
                    "float": lambda x: x.astype("float32").astype("float64") if hasattr(x, "astype") else np.float64(np.float32(x)),
                    "int": np.trunc,
                    # C's lround: halves away from 0
                    "nint": lambda x: np.sign(x) * np.floor(np.abs(x) + 0.5),
                }
                if name in one:
                    return finite(one[name](args[0]))
                two = {
                    "mod": np.fmod, "pow": np.power, "hypot": np.hypot, "atan2": np.arctan2,
                    "min": np.minimum, "max": np.maximum,
                }
                if name in two:
                    return finite(both(args[0], args[1], two[name](args[0], args[1])))
            raise ValueError(f"Unknown function {name}")
        raise ValueError(f"Unknown part of a formula: {what}")

    return walk(tree)
