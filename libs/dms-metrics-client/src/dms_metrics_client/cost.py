"""The sensor cost scale (P88-S2c, ADR 0243 amendment): 1 (very cheap) to 5
(very expensive), replacing the binary cheap/expensive at the user's
request, so admins can weigh sensors before switching them on.

Rubric - what a level means, judged by the work per measurement and the
number of series it produces:
1. in-memory counters and values (no I/O, few series);
2. one indexed count or a cheap syscall/statvfs;
3. grouped counts, labelled counters/histograms with moderate cardinality,
   network probes to one dependency;
4. timing histograms on hot paths;
5. high-cardinality breakdowns and active probes that do real work.

The old string values stay readable: "cheap" counts as 2 and "expensive" as
4 for a declared cost; an old override key `cost:cheap` stands for levels
1-3 and `cost:expensive` for 4-5."""

from __future__ import annotations

COST_LEVELS: tuple[int, ...] = (1, 2, 3, 4, 5)
LEGACY_COST_LEVEL = {"cheap": 2, "expensive": 4}
LEGACY_KEY_LEVELS = {"cheap": (1, 2, 3), "expensive": (4, 5)}


def normalize_cost(value: int | str) -> int:
    """A declared cost as a level; accepts the legacy strings and digits."""
    if isinstance(value, str):
        if value in LEGACY_COST_LEVEL:
            return LEGACY_COST_LEVEL[value]
        if value.isdigit():
            value = int(value)
        else:
            raise ValueError(f"Unbekannte Sensor-Kosten {value!r}")
    if value not in COST_LEVELS:
        raise ValueError(f"Sensor-Kosten muessen 1-5 sein, nicht {value!r}")
    return int(value)
