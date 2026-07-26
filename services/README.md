# services/

Ein Verzeichnis je Microservice, gefüllt gemäß der Roadmap (`../IMPLEMENTATION_PLAN.md`).

- `registry-service/` — Service Discovery (P1-S1, Konzept 3.2a)
- `audit-service/` — hash-verkettetes Ereignisprotokoll (P1-S2, Konzept 3.4/5.3)
- `auth-service/` — OIDC-Broker vor Keycloak (P2-S1, Konzept 4.4)
- `permission-service/` — RBAC mit Ordner-Vererbung und Rechte-Cache (P2-S2, Konzept 4.1)
- `storage-service/` — Storage-Abstraktionsschicht (Local-FS/NFS-via-PVC + S3/MinIO) (P3-S1, Konzept 3.6)
- `document-service/` — Dokumente: CRUD, dauerhafte Versionierung, Bearbeitungssperre inkl. Force-Unlock/Konfliktkopie (P3-S2, Konzept 2.1/2.1a/4.2)
- `object-type-service/` — Objekttyp-Definitionen + Constraint-Validierung (P3-S3, Konzept 2.2/4.5)
- `folder-service/` — Ordner-Hierarchie inkl. Struktur-Events für den Permission Service (P3-S3, Konzept 2.1)

Aufbau je Service: siehe `../docs/service-template.md`.
