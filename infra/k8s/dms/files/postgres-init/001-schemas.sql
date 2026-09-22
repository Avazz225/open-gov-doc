-- Schema-pro-Service-Konvention (Konzept 3.1). Dieses Skript legt nur das
-- keycloak-Schema an (Keycloak verwaltet sein eigenes Schema selbst, kein
-- DMS-Service). Jeder DMS-Service bekommt sein eigenes Schema stattdessen
-- über 002-service-roles.sh (P68-S1) - dort auch mit einer eigenen,
-- rechtebeschränkten Postgres-Rolle statt dieses Superusers, siehe
-- docs/adr/0199-per-service-postgres-roles.md.
--
-- Kopie von infra/postgres-init/001-schemas.sql (P26-S3) — Helms `.Files.Glob`
-- kann nur Dateien innerhalb des Chart-Verzeichnisses lesen, deshalb liegt
-- hier eine zweite Kopie statt eines Verweises auf infra/postgres-init/.
-- Muss manuell synchron gehalten werden, wenn infra/postgres-init/ künftig um
-- weitere CREATE SCHEMA-Zeilen ergänzt wird (siehe templates/postgresql.yaml,
-- ConfigMap "<fullname>-postgresql-init", gemountet unter
-- /docker-entrypoint-initdb.d im bundled Postgres-Container — ohne dieses
-- Schema würde das bundled Keycloak-Deployment an KC_DB_URLs
-- "?currentSchema=keycloak" scheitern).

CREATE SCHEMA IF NOT EXISTS keycloak;
