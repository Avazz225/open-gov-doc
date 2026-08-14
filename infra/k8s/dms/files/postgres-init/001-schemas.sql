-- Schema-pro-Service-Konvention (Konzept 3.1). Jeder neue Service ergänzt hier
-- seine eigene "CREATE SCHEMA IF NOT EXISTS <service>;"-Zeile in der Session, die ihn baut.
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
