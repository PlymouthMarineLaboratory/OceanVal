"""The questions OceanVal asks while it runs.

Everything in OceanVal that needs an answer from the user asks through
ask(), and checks interactive() before asking, rather than calling input()
and sys.stdin.isatty() itself. At a terminal nothing changes. The oceanval
command (see oceanval.app) puts the questions in its window instead, by
installing an answerer: both in its own process, for create_recipes' FVCOM
question, and in the process it runs matchup and validate in (see
oceanval.app_child).

tell() is the other way round: how a run is going, such as each matchup
matchup() starts and makes, which the window shows as it goes. At a
terminal it says nothing, as what the run prints says it already.
"""

import contextlib
import sys

# set by answered_by; None asks at the terminal
_answerer = None
# set by told_by; None tells no one
_teller = None


class Answer(str):
    """An answer given in a window, with settings chosen along with it.

    It is the answer as typed, so it is compared as any other is, and its
    settings are a dict: in the oceanval window, yes to whether the
    matchups are right comes with the report's options, as live_validation
    (see matchup).
    """

    def __new__(cls, text, settings=None):
        answer = super().__new__(cls, text)
        answer.settings = dict(settings or {})
        return answer


def interactive():
    """Whether a question can be put to anyone: a window is answering them,
    or input comes from a terminal."""
    return _answerer is not None or (sys.stdin is not None and sys.stdin.isatty())


def ask(question, choices=None, details=None):
    """Ask the user a question, and return their answer as typed.

    choices are the answers expected, e.g. ("y", "n"), which a window offers
    as buttons. details is what a window can show along with the question,
    such as the matchups matchup() asks about, which a terminal has been
    shown already. The caller still checks the answer, as anything can be
    typed at a terminal. A window can answer with an Answer, which carries
    settings chosen along with it. Raises EOFError where input() would.
    """
    if _answerer is not None:
        return _answerer(question, tuple(choices) if choices else None, details)
    return input(question)


@contextlib.contextmanager
def answered_by(answerer):
    """Put every question asked inside the block to answerer(question,
    choices, details), which returns the answer."""
    global _answerer
    previous, _answerer = _answerer, answerer
    try:
        yield
    finally:
        _answerer = previous


def tell(news):
    """Tell a window how the run is going: news is a dict that can be
    written as JSON. Nothing is said at a terminal."""
    if _teller is not None:
        _teller(news)


@contextlib.contextmanager
def told_by(teller):
    """Pass everything told inside the block to teller(news)."""
    global _teller
    previous, _teller = _teller, teller
    try:
        yield
    finally:
        _teller = previous
