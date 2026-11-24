"""Pairing detached signatures with the attachments they sign (P92-S4,
ADR 0248 Decision 2).

A message often carries a file and its detached CMS signature side by side.
Rules, in order:

1. `name.ext` + `name.ext.p7s` (case-insensitive) belong together.
2. Otherwise, exactly one remaining file plus exactly one remaining `.p7s`
   belong together.

Everything else stays unpaired - a `.p7s` without a partner is filed as a
plain document. The message body (the synthetic first part) never takes part.
"""


def is_detached_signature(filename: str) -> bool:
    return filename.lower().endswith(".p7s")


def pair_detached_signatures(
    filenames: list[str], *, skip: set[int] = frozenset()
) -> dict[int, int]:
    """Maps the index of each paired `.p7s` to the index of the file it signs.
    Indexes in `skip` (the body text) are ignored."""
    candidates = [i for i in range(len(filenames)) if i not in skip]
    signatures = [i for i in candidates if is_detached_signature(filenames[i])]
    files = [i for i in candidates if not is_detached_signature(filenames[i])]
    by_name = {filenames[i].lower(): i for i in files}
    pairs: dict[int, int] = {}
    for sig in signatures:
        target = by_name.get(filenames[sig].lower()[: -len(".p7s")])
        if target is not None and target not in pairs.values():
            pairs[sig] = target
    rest_signatures = [s for s in signatures if s not in pairs]
    rest_files = [f for f in files if f not in pairs.values()]
    if len(rest_signatures) == 1 and len(rest_files) == 1:
        pairs[rest_signatures[0]] = rest_files[0]
    return pairs
