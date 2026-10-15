"""The time resolution of model output, and whether point matchups by day
suit it.

matchup labels each file pattern "monthly" or "Nd" ("1d", "2d", "5d", ...),
uses the finest pattern holding each variable, and shows the label with the
matchups. Observations matched by day (a point_time_res with "day" in it) are
matched to model output on the same day, so against output coarser than
daily most of them find nothing to match: matchup asks whether to change the
point_time_res of those point datasets (see check_rows and apply).
"""

import statistics

# what point_time_res can be chosen as, as (point_time_res, label, hint): the
# combinations the docs recommend and the report describes
POINT_TIME_RES_OPTIONS = (
    (("year", "month", "day"), "Year, month, day", "Exact dates"),
    (("year", "month"), "Year, month", "For monthly simulation output"),
    (("month", "day"), "Month, day", "Climatological: the year is ignored"),
    (("month",), "Month", "Climatological, by month"),
)

# what OceanVal suggests for datasets matched by day against coarser output
SUGGESTED = ["year", "month"]

MONTHLY = "monthly"
DAILY = "1d"


def label(df_times):
    """The time resolution of model output, from the year, month and day of
    its times (a DataFrame with those columns).

    "monthly" if there is no more than one time in any month, which includes
    annual output and a single time. Otherwise "Nd", N being the usual number
    of days between one date and the next in the same month, so output more
    often than daily is "1d". Only the day numbers within a month are
    compared, which holds for any calendar (360-day, noleap, ...).
    """
    dates = sorted(
        {(int(y), int(m), int(d)) for y, m, d in zip(df_times.year, df_times.month, df_times.day)}
    )
    times = df_times.loc[:, ["year", "month"]]
    if len(times.drop_duplicates()) == len(df_times):
        return MONTHLY
    gaps = [
        later[2] - earlier[2]
        for earlier, later in zip(dates, dates[1:])
        if earlier[:2] == later[:2]
    ]
    if not gaps:
        # several times, all on one day of each month: as often as can be told
        return DAILY
    return f"{max(1, round(statistics.median(gaps)))}d"


def sort_key(resolution):
    """Finest first: the number of days, with monthly after any of them."""
    if resolution and resolution.endswith("d") and resolution[:-1].isdigit():
        return int(resolution[:-1])
    return 10**6


def needs_check(resolution, point_time_res):
    """Whether observations matched by point_time_res may mostly go
    unmatched against output of this resolution: they are matched by day,
    and the output is coarser than daily."""
    return resolution is not None and resolution != DAILY and "day" in point_time_res


def key(variable, source):
    """How a point dataset is named in a choice (see apply)."""
    return f"{variable}/{source}"


def valid(point_time_res):
    """Whether point_time_res is one add_point_comparison accepts: a
    non-empty list of "year", "month" and "day", none twice."""
    return (
        isinstance(point_time_res, list)
        and len(point_time_res) > 0
        and all(x in ("year", "month", "day") for x in point_time_res)
        and len(set(point_time_res)) == len(point_time_res)
    )


def check_rows(point, resolutions, comparisons, default, titles=None):
    """The point datasets whose point_time_res needs checking, as dicts.

    point is the (variable, source) pairs being matched up, resolutions the
    time resolution of each variable, comparisons(variable, source) the
    dataset's add_point_comparison settings, and default matchup's
    point_time_res, which a dataset without its own uses.
    """
    rows = []
    seen = set()
    for variable, source in point:
        if (variable, source) in seen:
            continue
        seen.add((variable, source))
        own = comparisons(variable, source).get("point_time_res")
        used = list(own or default)
        resolution = resolutions.get(variable)
        if not needs_check(resolution, used):
            continue
        rows.append(
            {
                "key": key(variable, source),
                "variable": variable,
                "title": (titles or {}).get(variable, variable),
                "source": source,
                "time_res": resolution,
                "point_time_res": used,
                "own": bool(own),
            }
        )
    return rows


def clean_choice(choice, rows):
    """A choice of point_time_res for the datasets in rows, checked, or
    raises ValueError.

    A choice is {"default": [...] or None, "datasets": {key: [...]}}:
    default is a new point_time_res for all of them, datasets one for each.
    None, or neither, keeps them as they are.
    """
    if choice is None:
        return {"default": None, "datasets": {}}
    if not isinstance(choice, dict):
        raise ValueError("A choice of point_time_res must be a dict")
    default = choice.get("default")
    if default is not None and not valid(default):
        raise ValueError(f"{default!r} is not a point_time_res")
    keys = {row["key"] for row in rows}
    datasets = {}
    for name, value in (choice.get("datasets") or {}).items():
        if name not in keys:
            raise ValueError(f"{name} is not a point dataset being checked")
        if not valid(value):
            raise ValueError(f"{value!r} is not a point_time_res for {name}")
        datasets[name] = list(value)
    return {"default": list(default) if default else None, "datasets": datasets}


def changes(choice, rows):
    """What a cleaned choice changes: the new matchup point_time_res (or
    None), and the new point_time_res of each dataset that has its own.

    A new point_time_res for all of them is matchup's, and also each
    dataset's that has one of its own, as that would otherwise win.
    """
    default = choice["default"]
    datasets = {}
    if default is not None:
        datasets.update({row["key"]: list(default) for row in rows if row["own"]})
    datasets.update(choice["datasets"])
    return default, datasets


def apply(choice, rows, session_info, definitions):
    """Use a cleaned choice for the rest of the matchup. Returns what
    changed, as lines to print."""
    default, datasets = changes(choice, rows)
    said = []
    if default is not None:
        session_info["point_time_res"] = list(default)
        said.append(f"point_time_res is now {default} for the point datasets checked")
    by_key = {row["key"]: row for row in rows}
    for name, value in datasets.items():
        row = by_key[name]
        definitions[row["variable"]].point_comparisons[row["source"]]["point_time_res"] = list(value)
        if name in choice["datasets"]:
            said.append(f"point_time_res for {row['title']} ({row['source']}) is now {value}")
    return said


def warning(rows):
    """What a terminal is told about the datasets in rows."""
    lines = [
        "******************************",
        "Possible problem with point matchups:",
        "these point datasets are matched to the model by day, but the model output",
        "for their variable is coarser than daily, so most observations may find no",
        "model output on the same day and be left out.",
    ]
    for row in rows:
        lines.append(
            f"  {row['title']} ({row['source']}): output {row['time_res']}, "
            f"point_time_res {row['point_time_res']}"
        )
    return "\n".join(lines)


def ask_at_terminal(rows, ask):
    """Ask at a terminal what to do about the datasets in rows, through
    ask(question, choices), and return the choice (see clean_choice)."""
    options = "\n".join(
        f"  {number}: {text} ({hint})"
        for number, (_, text, hint) in enumerate(POINT_TIME_RES_OPTIONS, 1)
    )
    numbers = tuple(str(n) for n in range(1, len(POINT_TIME_RES_OPTIONS) + 1))

    def pick(question):
        while True:
            answer = ask(f"{options}\n{question} ", numbers).strip()
            if answer in numbers:
                return list(POINT_TIME_RES_OPTIONS[int(answer) - 1][0])
            print(f"Choose one of {', '.join(numbers)}")

    while True:
        answer = ask(
            "Change point_time_res for (a)ll of them, for (e)ach one, or (k)eep it? (a/e/k) ",
            ("a", "e", "k"),
        ).strip().lower()
        if answer in ("a", "e", "k"):
            break
        print("Provide a, e or k")
    if answer == "k":
        return {"default": None, "datasets": {}}
    if answer == "a":
        return {"default": pick("Match all of them by:"), "datasets": {}}
    return {
        "default": None,
        "datasets": {
            row["key"]: pick(f"Match {row['title']} ({row['source']}) by:") for row in rows
        },
    }
