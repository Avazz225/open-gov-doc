# 0231 — `webdav-connector` root-listing timeout: fix the real N+1, not WebDAV-level pagination

**Status:** accepted
**Context:** P79-S2 (Phase 79, second session, run after P79-S1 closed). The root `PROPFIND` O(N) shape
had been flagged repeatedly across multiple gap-analysis rounds ("costs proportional to total item count
regardless of batching"), and was confirmed as genuinely **timing out** (not just slow) at Phase 74's own
phase-close full regression: 15-16 of 18 `test_webdav.py` tests failed with `httpcore.ReadTimeout` even at
a 30s client timeout, reproducible on demand, with this dev stack's accumulated 1,639 folders/1,675
documents named as the trigger. The plan for this session assumed the fix would need either genuine
WebDAV-protocol-level pagination of the root listing, or a practical page-size cap with a documented
client-side workaround.

Investigation before writing any code first re-confirmed the failure was still live (it was — full
`webdav-connector` suite: 6 failed / 12 passed, 239s, same `ReadTimeout` shape) — but the CURRENT item
counts under this dev stack's `root` turned out to be far smaller than the historical 1,639/1,675 figure
(175 folders / 131 documents directly under `root` at the time of this session, likely thinned out by
intervening cleanup work across the ~5 phases since). A ~300-item listing timing out even at 30s pointed at
something other than "the payload itself is too big to transfer" — total payload size for 300 WebDAV
property-list entries is trivial.

## Decision

Fix the actual root cause — a real N+1 bug in `webdav_connector.dav_provider.DmsDavFolder` — instead of
building WebDAV-protocol-level pagination.

**Root cause**: `wsgidav`'s own `DAVCollection.get_member_list()` (the method its `PROPFIND` handler
actually calls to enumerate a directory's contents) has this default implementation:

```python
def get_member_list(self):
    memberList = []
    for name in self.get_member_names():
        member = self.get_member(name)
        ...
    return memberList
```

It calls `get_member_names()` **once**, then `get_member(name)` **once per name**. `DmsDavFolder`'s own
`get_member(name)` (added in the original P12-S1 implementation) re-fetches the **entire** folder's
`list_children()` — 2-3 HTTP round trips to `folder-service`/`document-service` each (folder children,
document listing, batched current-version metadata) — on every single call, to then find the one requested
`name` inside it. For a directory with N members, this multiplies into **N+1 full-listing fetches** for
one `PROPFIND Depth: 1` request, not just N+1 single-item lookups — 300 members meant 300+ redundant full
listing round trips, easily explaining multi-minute timeouts at real item counts, and still slow enough at
today's smaller counts to blow past a 30s client timeout.

**Fix**: override `get_member_list()` directly on `DmsDavFolder`, calling `list_children()` exactly **once**
and constructing every child `DmsDavFolder`/`DmsDavDocument` resource object straight from that single
result — `wsgidav`'s own base implementation explicitly documents this method as overridable "for
performance reasons." `get_member_names()`/`get_member()` are left unchanged: they remain correct (and
sufficiently cheap, since they're each called with a known single name/whole-name-list rather than in an
N+1 loop) for their other call sites (`resolve_path`'s segment-by-segment path traversal, `get_resource_inst`).

**WebDAV-protocol-level pagination was investigated and rejected**: RFC 4918 defines no pagination
mechanism for `PROPFIND` — a `Depth: 1` request is inherently "give me the complete listing of this
collection's direct members" as a single operation, and real WebDAV clients (Windows Explorer, macOS
Finder, `webdav4`) issue exactly one such request per directory view with no notion of "give me page 2."
Building a bespoke pagination scheme (e.g. a custom header, or capping the response and requiring a
follow-up request) would deviate from the protocol in a way no real client understands, effectively
silently truncating a directory listing for any unmodified client — worse than the timeout it would
replace. Once the actual bottleneck (the N+1) is fixed, a genuinely complete `Depth: 1` listing of a
few hundred items is fast (single-digit round trips, not hundreds) and needs no page-size cap at all.

## Rationale

- **The plan's own framing ("O(N) in total item count regardless of batching") was accurate about the
  symptom but not fully precise about the cause** — the per-document N+1 (version metadata fetched once
  per document) had already been fixed in Phase 50 Session 4 via a batch endpoint; what remained was a
  DIFFERENT, WebDAV-layer-specific N+1 in how `wsgidav` itself enumerates a collection, invisible from
  `dms-connector-sdk`'s own code (which only ever sees single `list_children()` calls, each individually
  fast) and only apparent once the request-scoped call PATTERN (one call per candidate wsgidav resource
  object) is examined.
- **Why `get_member_list()` and not something else**: it is the single call site `wsgidav`'s `PROPFIND`
  handler (`request_server.py`'s `do_PROPFIND` → `get_descendants` → `get_member_list`) actually uses for
  a directory listing — overriding it is a complete, surgical fix with zero WebDAV response-shape change,
  rather than patching around the symptom (e.g., caching `list_children()` results for the duration of one
  request, which would fix this specific pattern but leave the next similarly-shaped N+1 undiagnosed).
- **`cmis-connector` does not share this bug** despite sharing `dms-connector-sdk`'s `DmsTreeClient`: it is
  a plain FastAPI service that calls `list_children()` directly, once per real endpoint handler — it has no
  `wsgidav`-style `get_member_names()`/`get_member()` split to begin with, so this specific N+1 shape is
  unique to the WebDAV provider layer, not the shared SDK.

## Consequences

- `services/webdav-connector/src/webdav_connector/dav_provider.py`: `DmsDavFolder` gains a
  `get_member_list()` override — single `list_children()` call, builds all child resource objects
  directly. `get_member_names()`/`get_member()` unchanged.
- New test: `test_listing_a_folder_with_many_children_stays_fast` — creates a dedicated folder (not `root`
  itself, to stay independent of whatever this shared dev stack has already accumulated there) with 60
  children, asserts a `Depth: 1` listing of it completes in under 5 seconds. Full suite: 239s/6 failed → 
  35-40s/19 passed (18 pre-existing + this one new test).
- No page-size cap, no protocol-level pagination, no client-facing behavior change at all — a `Depth: 1`
  listing still returns the complete, unbounded set of direct members in one response, now cheaply.
- `docs/services/webdav-connector.md`'s "Open Points" bullet on this topic closed with the real root cause
  and fix recorded (not the pagination approach originally speculated in the phase plan).
