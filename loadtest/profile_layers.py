"""Where a service's CPU goes, by layer (P95-S1) - from a py-spy `--format raw`
recording (folded stacks: `frame;frame;... count`), taken under load with
`--nonblocking` (only samples where the process was running Python).

Each sample is attributed twice:
- inclusive per layer: does the stack pass through the layer at all
  (a middleware is "inclusive" of everything it wraps, so only the leaf view
  below adds up to 100 %);
- leaf: the layer of the innermost frame - where the CPU actually is.

Usage: uv run python loadtest/profile_layers.py <raw.txt> [...]
"""

import sys
from collections import Counter

# (layer, substrings of a frame "function (path:line)") - first match wins.
LAYERS = [
    ("idle/event loop", ("select.py", "selectors.py", "base_events.py:_run_once", "epoll")),
    ("tracing (OpenTelemetry)", ("opentelemetry",)),
    ("HTTP sensors (metrics)", ("dms_metrics_client", "prometheus_client")),
    (
        "access log + JSON logging",
        ("dms_common/access_log", "dms_common/logging", "logging/__init__"),
    ),
    ("acting identity / settings", ("dms_common/acting", "pydantic_settings")),
    (
        "serialisation (pydantic/json)",
        ("pydantic", "json/", "orjson", "fastapi/encoders", "jsonable_encoder"),
    ),
    ("database driver (sqlalchemy/asyncpg)", ("sqlalchemy", "asyncpg")),
    ("HTTP client (httpx/httpcore)", ("httpx", "httpcore", "h11", "anyio")),
    ("NATS client", ("nats/",)),
    ("routing/ASGI (starlette/fastapi/uvicorn)", ("starlette", "fastapi", "uvicorn")),
    ("service code", ("_service/", "_connector/", "dms_")),
]


def layer_of(frame: str) -> str:
    for name, needles in LAYERS:
        if any(n in frame for n in needles):
            return name
    return "other (stdlib, libs)"


def analyse(path: str) -> None:
    inclusive: Counter = Counter()
    leaf: Counter = Counter()
    hot: Counter = Counter()
    total = 0
    for line in open(path, errors="replace"):
        line = line.rstrip()
        if not line:
            continue
        stack, _, count = line.rpartition(" ")
        if not count.isdigit():
            continue  # a sample torn by --nonblocking reads
        n = int(count)
        frames = stack.split(";")
        total += n
        for name in {layer_of(f) for f in frames}:
            inclusive[name] += n
        leaf[layer_of(frames[-1])] += n
        # The innermost service-code frame, to name the hot spots.
        for frame in reversed(frames):
            if layer_of(frame) == "service code":
                hot[frame.split(" (")[0] + " (" + frame.split("/")[-1]] += n
                break
    print(f"== {path.rsplit('/', 1)[-1]}: {total} samples")
    print("  leaf (where the CPU is)                     inclusive (stack passes through)")
    for name, n in leaf.most_common():
        print(f"  {name:<42} {n / total:6.1%}   {inclusive[name] / total:6.1%}")
    print("  hottest service-code frames (innermost):")
    for frame, n in hot.most_common(8):
        print(f"    {n / total:6.1%}  {frame[:110]}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        analyse(p)
