# Extension points for the backlog candidates from 12.2

**Session:** P14-S3 — "Prepare the backlog candidates from 12.2 ... cleanly as plugin extension points (do not implement)"
**Concept reference:** 12.2, 3.3, 3.8

This document is deliberately **not an implementation** of the three extension candidates remaining in Concept 12.2 (ERP/line-of-business connectors, native mobile clients, AI features) — for each candidate it shows concretely which **already existing** point in the system it would attach to, and why this is possible without touching the core. 12.2 already claims this generically ("the architecture ... is designed so that these can be added later without touching the core scaffolding") — this document checks that claim against the actual code, individually, for each of the three remaining candidates, instead of leaving it unsubstantiated.

All three sections follow the same structure: **Attachment point** (which already-existing mechanism applies), **What would newly be built** (the actual component deliberately not built here), **Deliberate non-decisions** (what this document explicitly leaves open because it would be a business/product-level decision, not a technical one).

---

## 1. Prebuilt standard connectors to ERP/line-of-business software (e.g. DATEV, SAP)

### Attachment point

Follows exactly the pattern already implemented twice for real by
`cmis-connector`/`webdav-connector` (3.3):

- **New service** `services/<x>-connector/` following the standard service template (`docs/service-template.md`), no special structure.
- **DMS-side tree access logic** via the already-existing, real connector SDK `libs/dms-connector-sdk` (`DmsTreeClient` — read/write/metadata/locking/versioning against `document-service`/`folder-service`, see `dms_connector_sdk/dms_tree_client.py`) instead of a fresh reimplementation of this side. An ERP connector would only need its own protocol/client library for the **ERP-side** connection (e.g. a DATEV API integration or an SAP RFC/OData client) — the same split that `cmis-connector` (CMIS 1.1 browser binding, ADR 0036) and `webdav-connector` (`wsgidav`) already each have for their external protocol side.
- **Capability description** via `dms_connector_sdk.ConnectorDescriptor`/`ConnectorCapability` (`protocol="datev"`, `capabilities=frozenset({...})` from `{"read","write","metadata","locking","versioning"}`, literally Concept 3.3's enumeration) — an ERP connector would realistically only declare a subset (e.g. only `read`+`metadata` for a first, purely read-only invoice reconciliation, without locking/versioning); the descriptor format does not require completeness.
- **Registration** via the already-existing `maybe_start_registration()` from `libs/dms-registry-client` — an identical call to any other service, no change needed to `registry-service` or `gateway-service`. The gateway is a fully generic proxy (`@app.api_route("/api/{service_type}/{path:path}")`, `services/gateway-service/src/gateway_service/main.py`) — a new `service_type` becomes automatically reachable under `/api/<service_type>/...` as soon as it registers itself.
- **Licensing** via a single new entry in `registry-service`'s `licensable_components` dict (`settings.py`, e.g. `"datev-connector": "demo"`) plus the same local self-enforcement mechanism that `cmis-connector`/`webdav-connector`/`migration-service`/`workflow-service` already each implement for themselves (own `LicenseStatusClient`, own `_check_license` gate before every write call) — `registry-service` itself blocks nothing, it is only the status source.

### What would newly be built

- The actual protocol integration with the respective third-party software (DATEV API client, SAP RFC/OData client, etc.) — this is the actual development effort that 12.2 rightly classifies as "preconfigured, but technically possible."
- A mapping layer between the ERP's own data structures (e.g. DATEV documents, SAP business objects) and the DMS tree model (folder/document/attributes) — comparable to the already-existing CMIS-to-DMS translation in `cmis-connector`.
- Optionally: a manifest at the Plugin Orchestration Service (`POST /plugins/{plugin_type}`, fields `scaling_type`/`resource_cpu_cores`/`resource_ram_mb`/`load_profile`/`dependencies` — see `docs/services/plugin-orchestration-service.md`). **Honest limitation**: today this service is a pure decision/audit engine without real container lifecycle access — in the real, existing Docker Compose environment (a single node, no Kubernetes/Swarm) it does make First-Fit-Decreasing recommendations, but nothing/no one automatically acts on them. A manifest for a new ERP connector would therefore already be technically possible today, but practically without consequence, until this gap (see `docs/services/plugin-orchestration-service.md` "Open Points") is closed in a dedicated future session.

### Deliberate non-decisions

- **Which ERP/line-of-business software first** (DATEV vs. SAP vs. something else) — a pure market-demand decision, see 12.2's own prioritization ("e.g. DATEV, SAP").
- **Bidirectional vs. purely read-only integration** — whether a first connector only reads documents into the DMS or also writes back is a scope decision for the actual implementation session, not to be preempted here.

---

## 2. Native mobile clients (iOS/Android)

### Attachment point

Unlike the other two candidates, a mobile client is not a new **backend**
service, but a new **consumer** of the already-existing backend API —
structurally closer to the existing web UIs than to a connector:

- **No new gateway/backend code needed for pure data access**: every existing web UI (`apps/user-ui`) already talks exclusively via `/api/{service_type}/{path}` to the gateway — a native app would use the same path, no separate "mobile API" needed. Concept 8's deliberate CSR decision (no SSR intermediate layer) also means the web UIs themselves are already pure JSON API consumers — a mobile client differs from the existing user-ui frontend technically only in rendering (native instead of Next.js/React DOM), not in the backend access pattern.
- **Authentication needs a second, dedicated Keycloak client, not a new auth architecture**: the existing `dms-api` client (`auth-service/bootstrap.py`, `ensure_realm_and_client()`) is a **confidential** client (`publicClient: False`) with resource-owner password grant (`keycloak_client.py`, `grant_type: "password"`) — appropriate for the already-existing, self-operated first-party clients (web UIs, CLI), but per OAuth2 best practice **not** suitable for a distributed, untrusted native app client (no client secret can be safely embedded). A mobile client would need a second Keycloak client (`dms-mobile` or similar, `publicClient: True`, no secret) with **Authorization Code + PKCE** instead of password grant — natively supported by Keycloak, without `auth-service` needing to change its existing token validation (`dms-auth-client`'s `TokenValidator` against Keycloak's JWKS): an access token issued via PKCE is indistinguishable, for any downstream service, from one issued via password grant.
- **Offline access with local encryption** (12.2, explicitly required) is a purely client-side property (local encrypted storage on the device) — it does not touch the backend.

### What would newly be built

- The actual native app (iOS/Android) — a complete, standalone development effort outside this backend system.
- The second Keycloak client (`dms-mobile`) plus the corresponding bootstrap addition in `auth-service` (analogous to the already-existing `ensure_realm_and_client()` idempotency for `dms-api`).
- **Push notifications as a fourth channel in `notification-service`**: the existing channel type is already a closed, extensible enumeration (`Channel = Literal["email", "in_app", "webhook"]`, `notification_service/schemas.py`) with a small dispatch branch in `repository.py` — a `"push"` channel (APNs/FCM) would structurally be the same minimal change as adding `"webhook"` was at the time, not an architectural overhaul.
- Full-text/geo search on the go (12.2 names this as a reference feature) — already technically mappable via `search-service`'s existing API; a geo component does not exist today and would be a standalone extension point of `search-service` itself, not planned here (not mobile-specific).

### Deliberate non-decisions

- **Whether mobile access itself becomes its own licensable dimension** (e.g. "mobile access" as a Concept 9.1 application component) or simply remains part of the existing user-count licensing — this document deliberately invents no mechanism for this, since it is unclear whether this is even desired from a business perspective (unlike the other two candidates, there is no obvious, already-established `licensable_components` use case here, since a mobile client is not a separately registrable `service_type`).
- **Concrete app framework** (native Swift/Kotlin vs. React Native/Flutter) — a pure implementation decision for the actual session, technically irrelevant to the backend since only the existing JSON API is consumed either way.

---

## 3. AI features (document chat, automatic summarization, process assistance)

### Attachment point

Modeled most closely on `signature-service`'s provider plugin pattern (ADR 0025), since both solve the same underlying problem: a domain capability, interchangeable behind a stable interface, with a deliberately provided but (as yet) unimplemented second provider type as a direct precedent:

- **New, standalone service** `services/ai-service/` (a genuine "add-on" principle, 1./3.8: absence is a normal state, no installation is required to deploy it) instead of hardwiring an AI capability into existing services.
- **Provider abstraction following the same pattern as `SignatureProviderConnector`** (`signature_service/connectors/interface.py`): an `AIProviderConnector` interface (e.g. `summarize(document_text: str) -> str`, `answer(document_text: str, question: str) -> str`) with a **provider type selectable per installation via configuration** — exactly like `SignatureProviderConfig.type: Literal["internal","qtsp"]` already leads today with a real, functioning type (`"internal"`) alongside a deliberately reserved but unimplemented type (`"qtsp"`, raises `ValueError` when selected, ADR 0025 "Consequences"), an `AIProviderConfig.type: Literal["local","external_api"]` could be structured the same way from the start — `"local"` as an actually runnable reference implementation (e.g. a self-hosted, open-source model), `"external_api"` as a deliberately reserved, not-yet-implemented placeholder for a later integration with an external AI provider. The same factory dispatch structure (`build_connector()`) as in `signature_service/connectors/__init__.py`.
- **No dedicated document-content extraction of its own** — an AI service would deliberately build on already-existing infrastructure instead of duplicating it: document content via `document-service`, an already-extracted text layer via the OCR service (3.9, `ocr_service.engines`), or the search index via `search-service` (3.7a) — the same reuse that 3.9 itself already describes between search and substitute rendering (2.4) ("the same infrastructure is used for two purposes").
- **Registration/licensing** identical to any other "add-on" service: `maybe_start_registration()`, a new entry in `registry-service`'s `licensable_components` (e.g. `"ai-service": "lock"` — a full lock instead of demo mode is more appropriate here, since a "demo" summarization would be functionally hard to meaningfully restrict, unlike pure read-access restrictions).
- **Already-established precedent for "deliberately left out"**: Concept 5.4 has already excluded AI/ML-based anomaly detection with an identical justification ("AI/ML-based anomaly detection is deliberately out of scope ... a later addition remains possible at any time thanks to the 'add-on' architecture") — this document merely generalizes the same justification to the use cases additionally named in 12.2 (chat/summarization/process assistance).

### What would newly be built

- The actual `ai-service`, including the provider interface and at least one real `"local"` implementation.
- A new workflow task type (7.1) for "process assistance" (e.g. an automatic suggestion at a gateway decision) — technically another Automatic/Service-Task-like building block, analogous to the already-existing generic `connector_call` service task mechanism from P12-S2 (`spiff_adapter.ConnectorServiceTask`), which can already call any external service from within a BPMN process today, without `workflow-service` needing to know the calling service.

### Deliberate non-decisions

- **Data-protection/governance question for an external AI provider** (`type: "external_api"`): as soon as document content goes to an external service, a new class of auditing/compliance requirements arises (which content leaves the installation, under what legal basis) — this document deliberately does not solve this; it only notes that the provider abstraction cleanly isolates this decision (an installation that does not want this simply activates `type: "local"` or does not deploy `ai-service` at all).
- **Concrete model choice** for the `"local"` reference implementation — a pure implementation decision for a later session, depending on the state of open-source models at the time of implementation.

---

## Summary: shared pattern

All three candidates confirm the same structural claim from 12.2: none of them requires a change to `registry-service`'s registration protocol, to `gateway-service`'s proxy logic, or to any of the existing core services (`document-service`/`folder-service`/`permission-service`/...). The three already-existing, differently-shaped extension mechanisms cover all three cases:

| Candidate | Closest real precedent | Type of extension |
|---|---|---|
| ERP/line-of-business connectors | `cmis-connector`/`webdav-connector` (3.3) | New service, connector SDK reuse |
| Native mobile clients | `apps/user-ui` (8) | New consumer of the same API, second Keycloak client |
| AI features | `signature-service` provider plugin (ADR 0025) | New service, provider abstraction with reserved second type |

The one real gap that still exists today and could potentially affect all three, should a future implementation need automatic placement/scaling: the Plugin Orchestration Service already makes real placement decisions today, but nothing automatically acts on them in the existing Docker Compose environment (see `docs/services/plugin-orchestration-service.md` "Open Points") — an existing but deliberately incomplete foundation, not a new finding of this session.
