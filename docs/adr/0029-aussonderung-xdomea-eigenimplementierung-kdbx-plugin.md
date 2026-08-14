# 0029 — Records disposal: custom XDOMEA implementation instead of a library + KDBX as an optional, non-bundled plugin

**Status:** accepted
**Context:** P7-S0 (phase kickoff planning for Phase 7, Concept 5.6 "Records Disposal & Long-Term Archiving"), `IMPLEMENTATION_PLAN.md` explicitly named clarifying the external dependencies/licenses for XDOMEA tooling and the KDBX library as a prerequisite for this session, before P7-S3. A pure research/check-in session, no implementation code.

## Decision

**XDOMEA**: There is no maintained, generally usable Python library for XDOMEA — only isolated state-archive toolkits for test disposal messages (e.g. `Landesarchiv-Thueringen/xdomea_toolkit`), no PyPI package. XDOMEA 3.0.0 replaced the previous mandatory version 2.3.0 of the IT Planning Council and is the current standard published by KoSIT (`xoev.de`/`xrepository.de`) with freely available XSD schemas. The disposal interface (P7-S3) itself serializes/validates against the official XDOMEA 3.0.0 XSD schemas, technically via `lxml` (BSD-style licensed, no license concern) — no new third-party library for the format itself, only the established XML processing library.

**KDBX**: The only established Python library (`pykeepass`) is licensed under **GPL-3.0** — unlike `SpiffWorkflow` (LGPLv3, ADR 0018), GPL-3.0 has no exception for unmodified use as a library dependency; if it is built into a distributed Docker image, that subjects the whole service to the GPL copyleft obligation upon distribution of the image. Decided at session start via `AskUserQuestion`: **KDBX support is implemented as an optional plugin that is not bundled by default** — analogous to the existing storage-backend plugin principle (3.3/3.6, ADR 0017). The core of the disposal service (P7-S3) defines only a thin interface for an "external key store"; `pykeepass` is **not** included in its `pyproject.toml`/standard Docker image, but documented as a separately installable extension that an operator deliberately adds themselves if they want to use this optional feature (which the concept already describes as "not mandatory" per 5.6).

## Rationale

- **GPL-3.0 differs categorically from LGPL, not just in degree**: the argument underlying ADR 0018 ("unmodified use as a dependency is exempted from the copyleft obligation") does not apply to GPL-3.0 — there, distributing a program that incorporates the library creates a "combined work" that as a whole falls under the GPL. Since this project's Docker images are built self-contained and potentially distributed (`CONTRIBUTING.md`: "libs/ is copied and installed locally during image build"), a bundled `pykeepass` would be a structurally different situation than SpiffWorkflow.
- **Plugin approach instead of switching libraries or a custom implementation**: a self-written KDBX3/4 writer (a third option considered) would be additional implementation effort for a feature the concept explicitly describes as optional and relatively rarely used — disproportionate. The existing plugin principle for storage backends (ADR 0017) already provides the matching pattern: the core stays license-free/GPL-free, the optional extension is up to the operator, who adds it deliberately and thereby uses it only for their own, non-redistributed operation — exactly the case that GPL-3.0 permits without restriction.
- **XDOMEA as an open standard with no license question**: unlike KDBX, this is not about a copyleft library but about the lack of a mature implementation — the decision is purely technical (custom serialization against official schemas instead of an untrustworthy/immature third-party library), not a license trade-off.
- **This assessment is not legal advice** (same caveat as ADR 0018): a pragmatic, technical evaluation for the current development phase. Before any third-party distribution of the overall system, the license question around KDBX/GPL in particular must be reviewed again — even though the plugin approach already significantly reduces the risk for the standard image.

## Consequences

- P7-S3 designs a plugin interface for external key stores (Concept 5.6) instead of importing `pykeepass` directly; the disposal service's standard Docker image remains GPL-free.
- Anyone wanting to use the optional KDBX encryption must add the plugin package themselves in their own environment — to be documented accordingly (`docs/services/<disposal-service>.md`, operations documentation).
- Larger installations that instead connect a full-fledged external KMS/HSM (already provided for as an alternative per the concept) are unaffected by this license question regardless — that remains the preferred path for installations that do not want to include a GPL plugin.
- XDOMEA export/import (P7-S3) will be implemented via `lxml` against the XDOMEA 3.0.0 XSD schemas itself; no further third-party library planned for the format.

## Addendum P7-S3b: XDOMEA version updated to 4.0.0

**Context:** During the actual implementation of XDOMEA disposal for circulation folders (P7-S3b, `archival-service`/`case-service`), the real schema sourcing was checked (see `docs/services/archival-service.md` "XDOMEA Disposal for Circulation Folders"). This revealed: the version 3.0.0 named above was, per the official KoSIT registration, due to expire within a few weeks at the time of implementation (`datumGueltigkeitBis` only a short time in the future) and at that point could only be fully found via a GPL-3.0-licensed third-party mirror (GitHub, Landesarchiv Thüringen), no longer via an official, license-unproblematic source for all required schema files. **4.0.0 is the current standard** and, at the same point in time, was fully obtainable cleanly via the official KoSIT infrastructure (`https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/` for the actual `xdomea-*.xsd` files, `xoev.de` directly for the three required shared XÖV base modules).

**Decision:** P7-S3b implements against **XDOMEA 4.0.0** instead of 3.0.0 — confirmed with the user via `AskUserQuestion` at session start, presented with exactly this rationale.

**Rationale:**
- The core argument of this ADR ("XDOMEA as an open standard with no license question") remains unchanged and valid — only the specific version number changes, no new license trade-off needed.
- Choosing an already-expiring version for a new implementation would have been technically needlessly risky, especially since the current version was obtainable more cleanly (without a GPL-mirror detour) than the one originally named in the ADR.
- The complete schema dependency chain for the message actually needed (`Aussonderung.Aussonderung.0503`) was actually downloaded before implementation and successfully compiled against `lxml.etree.XMLSchema` (7 files) — not a speculative version switch, but one verified against the real schema.

**Consequence:** The vendored schema files in `services/archival-service/src/archival_service/xdomea_schema/` are 4.0.0, not 3.0.0. The rest of this ADR's decision (custom serialization via `lxml`, no third-party library for the format) remains fully valid — only the target schema version number has changed. The KDBX decision (plugin approach) is unaffected by this addendum.
