# services/

One directory per microservice, populated according to the roadmap (`../IMPLEMENTATION_PLAN.md`).

- `registry-service/` — service discovery (P1-S1, Concept 3.2a)
- `audit-service/` — hash-chained event log (P1-S2, Concept 3.4/5.3)
- `auth-service/` — OIDC broker in front of Keycloak (P2-S1, Concept 4.4)
- `permission-service/` — RBAC with folder inheritance and permission cache (P2-S2, Concept 4.1)
- `storage-service/` — storage abstraction layer (local FS/NFS-via-PVC + S3/MinIO) (P3-S1, Concept 3.6)
- `document-service/` — documents: CRUD, durable versioning, edit lock including force-unlock/conflict copy (P3-S2, Concept 2.1/2.1a/4.2)
- `object-type-service/` — object type definitions + constraint validation (P3-S3, Concept 2.2/4.5)
- `folder-service/` — folder hierarchy including structure events for the Permission Service (P3-S3, Concept 2.1)

Layout per service: see `../docs/service-template.md`.
