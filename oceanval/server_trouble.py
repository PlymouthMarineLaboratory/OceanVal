"""Whether observations could not be read because their server is down.

When a data server is down or overloaded, a matchup fails with whatever
was reading from it: CDO reading over thredds gets the server's HTML error
page in place of the data, and gives it back as a syntax error, e.g.

    ValueError: syntax error, ... context: <!DOCTYPE^ HTML PUBLIC ...><html>
    <head><title>503 Service Unavailable</title></head><body><h1>Service
    Unavailable</h1><p>The server is temporarily unable to service your
    request ...

answer_of() finds what the server answered in such an error, and tell()
tells the oceanval window (see oceanval.prompts.tell), which says at the
bottom of its page that the server is not answering. Only answers that are
the server's own trouble count: a server error (5xx), or too many requests
(429). A file that is not there (404) is a problem with the recipe.
"""

import re
import urllib.error
import urllib.parse

from oceanval import prompts

# who runs each of the servers OceanVal's own datasets come from (see
# data_store.BUILTIN_HOSTS), as the window names them. Any other server is
# named by its address
SERVERS = {
    "www.ncei.noaa.gov": "NOAA",
    "psl.noaa.gov": "NOAA",
    "downloads.psl.noaa.gov": "NOAA",
    "www.oceancolour.org": "ESA's Ocean Colour CCI",
    "icdc.cen.uni-hamburg.de": "ICDC (University of Hamburg)",
    "ocean.ices.dk": "ICES",
}

# what is said for an answer whose page gives no reason
REASONS = {
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}

# the title of a server's HTML error page, e.g. <title>502 Proxy Error</title>
_TITLE = re.compile(r"<title>\s*(5\d\d|429)\s+([^<]+?)\s*</title>", re.IGNORECASE)
# an answer quoted without its page
_BARE = re.compile(
    r"\b(50[0234])\s+(Internal Server Error|Service Unavailable|Bad Gateway|Proxy Error|Gateway Time-?out)\b",
    re.IGNORECASE,
)
# the words of the page apache serves for a 503, if its title was cut off
_UNAVAILABLE = re.compile(r"temporarily unable to\s*service", re.IGNORECASE)


def _is_trouble(status):
    return isinstance(status, int) and (status == 429 or 500 <= status <= 599)


def _chain(error):
    """error and the exceptions it was raised from or while handling."""
    seen = []
    while error is not None and not any(error is x for x in seen):
        seen.append(error)
        error = error.__cause__ or error.__context__
    return seen


def _status_of(error):
    """The HTTP status of an error that has one (urllib's, requests'), or None."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def answer_of(error):
    """What a server answered in error, an exception (or its text), if it is
    the server's own trouble: {"status": 503, "reason": "Service
    Unavailable"}, say. None if it is not."""
    errors = _chain(error) if isinstance(error, BaseException) else [error]
    for each in errors:
        status = _status_of(each) if isinstance(each, BaseException) else None
        if _is_trouble(status):
            return {"status": status, "reason": REASONS.get(status, "Server Error")}
    for each in errors:
        text = str(each)
        found = _TITLE.search(text) or _BARE.search(text)
        if found:
            return {"status": int(found.group(1)), "reason": " ".join(found.group(2).split())}
        if _UNAVAILABLE.search(text):
            return {"status": 503, "reason": REASONS[503]}
        if "429" in text and "Too Many Requests" in text:
            return {"status": 429, "reason": REASONS[429]}
    return None


def host_of(path):
    """The server of path, a url or a list of them (the first), or None for
    a file on disk."""
    if isinstance(path, (list, tuple)):
        path = path[0] if len(path) > 0 else None
    if not isinstance(path, str):
        return None
    host = urllib.parse.urlparse(path).hostname
    return host or None


def server_of(host):
    """Who runs host, as the window names them."""
    return SERVERS.get(host, host)


def explain(answer, host):
    """What to say at a terminal of answer, from host."""
    server = server_of(host) if host else None
    if server and server != host:
        who, whose = f"{server}'s server ({host})", f"{server}'s"
    elif host:
        who, whose = f"The server {host}", "its"
    else:
        who, whose = "The data server", "its"
    lines = [f"{who} answered HTTP {answer['status']} {answer['reason']}."]
    if answer["status"] == 429:
        return lines + [
            "It is limiting how fast you can read from it.",
            "Wait for a while before trying again: it will let you back in.",
        ]
    return lines + [
        f"It is down or overloaded at {whose} end, not a problem with your simulation.",
        "Try again later.",
    ]


def tell(error, path, key, title):
    """Tell the window, if error is a server's trouble, that the matchup
    key (see live.matchup_key), shown as title, could not read its
    observations from path's server. Returns the answer, or None. Nothing is
    told at a terminal."""
    answer = answer_of(error)
    if answer is None:
        return None
    host = host_of(path)
    prompts.tell(
        {
            "kind": "server_trouble",
            "key": key,
            "title": title,
            "host": host or "",
            "server": server_of(host) if host else "",
            "status": answer["status"],
            "reason": answer["reason"],
        }
    )
    return answer
