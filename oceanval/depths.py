"""The depth bins of the point validation: validate()'s depth_bins option.

Point matchups made through the water column (vertical=True) are summarised
in depth ranges, on each point page (data/point_template.ipynb) and in the
summary (data/summary.ipynb). Each bin is a [min, max] pair of depths in
metres, and the deepest may have no max (None), for everything below its
min. A depth is in a bin if min < depth <= max, and the shallowest bin also
takes a depth equal to its min, so that 0 m is in the first bin (and, if it
starts at 0 m, anything recorded above the surface). Bins may
leave gaps, and observations in a gap are left out, but may not overlap.
"""

import math
import numbers

# the bins OceanVal has always used
DEFAULT_DEPTH_BINS = [
    (0, 10),
    (10, 30),
    (30, 60),
    (60, 100),
    (100, 150),
    (150, 300),
    (300, 600),
    (600, 1000),
    (1000, None),
]


def _depth(value, name):
    """A min or max of a bin as a number, or None if it is None."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"depth_bins: each {name} must be a number, not {value!r}")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"depth_bins: each {name} must be a finite number")
    return int(value) if value.is_integer() else value


def check_depth_bins(depth_bins):
    """depth_bins checked, and sorted from the shallowest: a list of (min,
    max) tuples, where max is None for the deepest bin if it has none. None
    gives the default bins. Raises ValueError if they cannot be used."""
    if depth_bins is None:
        return list(DEFAULT_DEPTH_BINS)
    if isinstance(depth_bins, (str, dict)) or not hasattr(depth_bins, "__iter__"):
        raise ValueError("depth_bins must be a list of [min, max] pairs")
    bins = []
    for pair in depth_bins:
        if isinstance(pair, (str, dict)) or not hasattr(pair, "__len__") or len(pair) != 2:
            raise ValueError(
                f"depth_bins must be a list of [min, max] pairs, not {pair!r}"
            )
        low, high = _depth(pair[0], "min"), _depth(pair[1], "max")
        if low is None:
            raise ValueError("depth_bins: each bin needs a min")
        if low < 0:
            raise ValueError("depth_bins: depths must be 0 or more")
        if high is not None and high <= low:
            raise ValueError(
                f"depth_bins: the max of a bin must be deeper than its min, "
                f"not {depth_label(low, high)}"
            )
        bins.append((low, high))
    if not bins:
        raise ValueError("depth_bins must have at least one bin")
    bins.sort(key=lambda pair: pair[0])
    for (low, high), (next_low, next_high) in zip(bins, bins[1:]):
        if high is None:
            raise ValueError(
                "depth_bins: only the deepest bin can have no max, as it is "
                "everything below its min"
            )
        if next_low < high:
            raise ValueError(
                f"depth_bins: the bins {depth_label(low, high)} and "
                f"{depth_label(next_low, next_high)} overlap"
            )
    return bins


def _number(value):
    """A depth as a label writes it: 10, not 10.0, as the oceanval window
    writes it too."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return repr(value)


def depth_label(low, high):
    """What the report calls a bin, e.g. 0-10m, or >1000m for one with no
    max."""
    if high is None:
        return f">{_number(low)}m"
    return f"{_number(low)}-{_number(high)}m"


def depth_labels(depth_bins):
    """The bins' labels, from the shallowest: the order of the report's
    tables and panels."""
    return [depth_label(low, high) for low, high in depth_bins]


def depth_ranges_text(labels):
    """The sentence of the point pages' map caption that lists the bins."""
    if len(labels) == 1:
        return f"The depth range is {labels[0]}."
    if len(labels) == 2:
        return f"The depth ranges are {labels[0]} and {labels[1]}."
    return f"The depth ranges are {', '.join(labels[:-1])}, and {labels[-1]}."


def bin_depth(depth, depth_bins):
    """The label of the bin depth is in, or None if it is in none of them.
    depth_bins are sorted from the shallowest, as check_depth_bins gives
    them."""
    if depth != depth:
        return None
    for i, (low, high) in enumerate(depth_bins):
        if i == 0:
            # a bin from the surface also takes anything recorded above it
            above = depth >= low or low == 0
        else:
            above = depth > low
        if above and (high is None or depth <= high):
            return depth_label(low, high)
    return None


# validate()'s map_depths: the depths (m) a gridded matchup through the water column is mapped
# at (the gap report's option 1). By default oxygen at 200 m, which stands in for the oxygen
# minimum zones, and 500 m, below them; every other variable at 500 m, where ventilation and
# remineralisation biases show that the surface hides
MAP_DEPTHS_OXYGEN = (200, 500)
MAP_DEPTHS = (500,)
_OXYGEN = ("oxygen", "aou", "o2sat")


def check_map_depths(map_depths):
    """map_depths checked: None (the defaults), False (no maps) or a sorted list of
    depths in metres. Raises ValueError if it cannot be used."""
    if map_depths is None or map_depths is False:
        return map_depths
    if map_depths is True:
        return None
    if isinstance(map_depths, (str, dict)) or not hasattr(map_depths, "__iter__"):
        raise ValueError("map_depths must be a list of depths in metres, e.g. [200, 500], or False for no maps")
    found = []
    for value in map_depths:
        depth = _depth(value, "map depth")
        if depth is None or depth < 0:
            raise ValueError("map_depths: each depth must be a number of metres, 0 or more")
        if depth not in found:
            found.append(depth)
    if not found:
        raise ValueError("map_depths must have at least one depth, or be False for no maps")
    return sorted(found)


def map_depths_for(variable, map_depths):
    """The depths (m) the matchup of variable is mapped at: map_depths as
    check_map_depths gives it, the defaults for None, nothing for False."""
    if map_depths is False:
        return []
    if map_depths is None:
        return list(MAP_DEPTHS_OXYGEN if str(variable).lower() in _OXYGEN else MAP_DEPTHS)
    return list(map_depths)


def nearest_level(levels, depth, tolerance=0.2):
    """The index of the level nearest depth, or None if the nearest is further than
    tolerance times the depth (or 20 m, whichever is more), so a map is not labelled
    with a depth it is far from."""
    if len(levels) == 0:
        return None
    gaps = [abs(float(x) - depth) for x in levels]
    k = gaps.index(min(gaps))
    if gaps[k] > max(20.0, tolerance * depth):
        return None
    return k
