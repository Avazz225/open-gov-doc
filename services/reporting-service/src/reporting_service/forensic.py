from collections import defaultdict
from datetime import datetime, timedelta

_DOWNLOAD_SUFFIXES = (".downloaded",)
_VIEW_SUFFIXES = (".viewed",)
_DELETE_SUFFIXES = (".deleted", ".force_deleted", ".trash_purged")


def categorize_event_type(event_type: str) -> str:
    """Derives the action-type category for the forensic trace (5.4b) from
    the `event_type` suffix - a fixed categorization derivable from the
    already established event naming convention instead of maintaining a
    new taxonomy. Order matters: download/view/delete are checked first,
    everything else falls into the remaining "change" category."""
    if event_type.endswith(_DOWNLOAD_SUFFIXES):
        return "download"
    if event_type.endswith(_VIEW_SUFFIXES):
        return "view"
    if event_type.endswith(_DELETE_SUFFIXES):
        return "delete"
    return "change"


def detect_download_anomalies(
    events: list[dict], *, threshold_count: int, threshold_minutes: int
) -> list[str]:
    """Only anomaly rule type per concept 5.4b ("initially exclusively via
    configurable static thresholds"): more than `threshold_count` downloads
    by the same actor within a `threshold_minutes`-wide time window.
    Sliding window (two-pointer) per actor over their sorted download
    timestamps."""
    if threshold_count <= 0:
        return []
    window = timedelta(minutes=threshold_minutes)
    by_actor: dict[str, list[datetime]] = defaultdict(list)
    for event in events:
        if categorize_event_type(event["event_type"]) != "download":
            continue
        actor = event.get("actor")
        if not actor:
            continue
        by_actor[actor].append(event["occurred_at"])

    anomalies: list[str] = []
    for actor, timestamps in by_actor.items():
        timestamps.sort()
        left = 0
        max_in_window = 0
        for right in range(len(timestamps)):
            while timestamps[right] - timestamps[left] > window:
                left += 1
            max_in_window = max(max_in_window, right - left + 1)
        if max_in_window >= threshold_count:
            anomalies.append(
                f"Nutzer {actor!r} hat {max_in_window} Downloads innerhalb von "
                f"{threshold_minutes} Minuten (Schwellwert: {threshold_count}) - "
                "ungewöhnlich hohe Aktivität."
            )
    return anomalies
