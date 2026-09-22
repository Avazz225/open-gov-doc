#!/usr/bin/env bash
# Per-service Postgres roles + schema ownership (Konzept 3.1, P68-S1).
# Each service connects with its OWN role instead of the shared superuser
# (see infra/docker-compose.yml's per-service DMS_POSTGRES_DSN), and each
# role OWNS exactly its own schema, pre-created here.
#
# Each role also gets "GRANT CREATE ON DATABASE" - NOT because it needs to
# create schemas it doesn't already own (it never will), but because every
# service unconditionally runs "CREATE SCHEMA IF NOT EXISTS <own>" at every
# startup (no Alembic at this project stage, see CONTRIBUTING.md), and Postgres
# checks CREATE-ON-DATABASE privilege BEFORE checking whether the schema
# already exists - "IF NOT EXISTS" only suppresses the "already exists" error,
# it does not skip the permission check first (confirmed live during this
# session: every service's own startup failed with a real "permission denied
# for database" error without this grant, even though its schema already
# existed). This is a deliberate, documented trade-off, not an oversight: a
# compromised/buggy service could create an ADDITIONAL, empty schema of its
# own choosing, but still cannot read or write into any OTHER service's
# existing schema - the actual data-isolation guarantee Konzept 3.1 asks for
# remains fully intact. Schema ownership (via AUTHORIZATION below) is what
# keeps create_all/the ad-hoc ALTER TABLE ... ADD COLUMN IF NOT EXISTS
# migrations working unchanged for tables the role itself owns.
#
# A .sh script, not .sql, so each password can come from an env var (same
# "${VAR:-dev-default}" convention docker-compose.yml already uses for
# POSTGRES_PASSWORD) instead of a value hardcoded once and shared across every
# environment - docker-entrypoint-initdb.d runs .sh files (sourced or executed)
# same as .sql files via psql, both only on a FRESH data volume. An existing
# deployment upgrading to this needs the equivalent statements applied
# manually once (CREATE ROLE + GRANT CREATE + ALTER SCHEMA/TABLE/SEQUENCE
# OWNER TO, since an already-existing schema's AUTHORIZATION clause above is
# a no-op for it) - see docs/adr/0199-per-service-postgres-roles.md.

set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE svc_archival LOGIN PASSWORD '${POSTGRES_PASSWORD_ARCHIVAL:-archival_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_archival;
    CREATE SCHEMA IF NOT EXISTS archival AUTHORIZATION svc_archival;
    CREATE ROLE svc_audit LOGIN PASSWORD '${POSTGRES_PASSWORD_AUDIT:-audit_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_audit;
    CREATE SCHEMA IF NOT EXISTS audit AUTHORIZATION svc_audit;
    CREATE ROLE svc_auth LOGIN PASSWORD '${POSTGRES_PASSWORD_AUTH:-auth_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_auth;
    CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION svc_auth;
    CREATE ROLE svc_case LOGIN PASSWORD '${POSTGRES_PASSWORD_CASE:-case_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_case;
    CREATE SCHEMA IF NOT EXISTS "case" AUTHORIZATION svc_case;
    CREATE ROLE svc_document LOGIN PASSWORD '${POSTGRES_PASSWORD_DOCUMENT:-document_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_document;
    CREATE SCHEMA IF NOT EXISTS document AUTHORIZATION svc_document;
    CREATE ROLE svc_favorite LOGIN PASSWORD '${POSTGRES_PASSWORD_FAVORITE:-favorite_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_favorite;
    CREATE SCHEMA IF NOT EXISTS favorite AUTHORIZATION svc_favorite;
    CREATE ROLE svc_federation LOGIN PASSWORD '${POSTGRES_PASSWORD_FEDERATION:-federation_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_federation;
    CREATE SCHEMA IF NOT EXISTS federation AUTHORIZATION svc_federation;
    CREATE ROLE svc_fleet LOGIN PASSWORD '${POSTGRES_PASSWORD_FLEET:-fleet_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_fleet;
    CREATE SCHEMA IF NOT EXISTS fleet AUTHORIZATION svc_fleet;
    CREATE ROLE svc_folder LOGIN PASSWORD '${POSTGRES_PASSWORD_FOLDER:-folder_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_folder;
    CREATE SCHEMA IF NOT EXISTS folder AUTHORIZATION svc_folder;
    CREATE ROLE svc_license LOGIN PASSWORD '${POSTGRES_PASSWORD_LICENSE:-license_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_license;
    CREATE SCHEMA IF NOT EXISTS license AUTHORIZATION svc_license;
    CREATE ROLE svc_mail_connector LOGIN PASSWORD '${POSTGRES_PASSWORD_MAIL_CONNECTOR:-mail_connector_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_mail_connector;
    CREATE SCHEMA IF NOT EXISTS mail_connector AUTHORIZATION svc_mail_connector;
    CREATE ROLE svc_migration LOGIN PASSWORD '${POSTGRES_PASSWORD_MIGRATION:-migration_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_migration;
    CREATE SCHEMA IF NOT EXISTS migration AUTHORIZATION svc_migration;
    CREATE ROLE svc_monitoring LOGIN PASSWORD '${POSTGRES_PASSWORD_MONITORING:-monitoring_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_monitoring;
    CREATE SCHEMA IF NOT EXISTS monitoring AUTHORIZATION svc_monitoring;
    CREATE ROLE svc_notification LOGIN PASSWORD '${POSTGRES_PASSWORD_NOTIFICATION:-notification_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_notification;
    CREATE SCHEMA IF NOT EXISTS notification AUTHORIZATION svc_notification;
    CREATE ROLE svc_object_type LOGIN PASSWORD '${POSTGRES_PASSWORD_OBJECT_TYPE:-object_type_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_object_type;
    CREATE SCHEMA IF NOT EXISTS object_type AUTHORIZATION svc_object_type;
    CREATE ROLE svc_ocr LOGIN PASSWORD '${POSTGRES_PASSWORD_OCR:-ocr_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_ocr;
    CREATE SCHEMA IF NOT EXISTS ocr AUTHORIZATION svc_ocr;
    CREATE ROLE svc_permission LOGIN PASSWORD '${POSTGRES_PASSWORD_PERMISSION:-permission_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_permission;
    CREATE SCHEMA IF NOT EXISTS permission AUTHORIZATION svc_permission;
    CREATE ROLE svc_orchestration LOGIN PASSWORD '${POSTGRES_PASSWORD_ORCHESTRATION:-orchestration_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_orchestration;
    CREATE SCHEMA IF NOT EXISTS orchestration AUTHORIZATION svc_orchestration;
    CREATE ROLE svc_query LOGIN PASSWORD '${POSTGRES_PASSWORD_QUERY:-query_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_query;
    CREATE SCHEMA IF NOT EXISTS query AUTHORIZATION svc_query;
    CREATE ROLE svc_registry LOGIN PASSWORD '${POSTGRES_PASSWORD_REGISTRY:-registry_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_registry;
    CREATE SCHEMA IF NOT EXISTS registry AUTHORIZATION svc_registry;
    CREATE ROLE svc_rendering LOGIN PASSWORD '${POSTGRES_PASSWORD_RENDERING:-rendering_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_rendering;
    CREATE SCHEMA IF NOT EXISTS rendering AUTHORIZATION svc_rendering;
    CREATE ROLE svc_reporting LOGIN PASSWORD '${POSTGRES_PASSWORD_REPORTING:-reporting_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_reporting;
    CREATE SCHEMA IF NOT EXISTS reporting AUTHORIZATION svc_reporting;
    CREATE ROLE svc_search LOGIN PASSWORD '${POSTGRES_PASSWORD_SEARCH:-search_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_search;
    CREATE SCHEMA IF NOT EXISTS search AUTHORIZATION svc_search;
    CREATE ROLE svc_signature LOGIN PASSWORD '${POSTGRES_PASSWORD_SIGNATURE:-signature_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_signature;
    CREATE SCHEMA IF NOT EXISTS signature AUTHORIZATION svc_signature;
    CREATE ROLE svc_storage LOGIN PASSWORD '${POSTGRES_PASSWORD_STORAGE:-storage_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_storage;
    CREATE SCHEMA IF NOT EXISTS storage AUTHORIZATION svc_storage;
    CREATE ROLE svc_teamspace LOGIN PASSWORD '${POSTGRES_PASSWORD_TEAMSPACE:-teamspace_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_teamspace;
    CREATE SCHEMA IF NOT EXISTS teamspace AUTHORIZATION svc_teamspace;
    CREATE ROLE svc_virus_scan LOGIN PASSWORD '${POSTGRES_PASSWORD_VIRUS_SCAN:-virus_scan_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_virus_scan;
    CREATE SCHEMA IF NOT EXISTS virus_scan AUTHORIZATION svc_virus_scan;
    CREATE ROLE svc_workflow LOGIN PASSWORD '${POSTGRES_PASSWORD_WORKFLOW:-workflow_dev_only}';
    GRANT CONNECT, CREATE ON DATABASE $POSTGRES_DB TO svc_workflow;
    CREATE SCHEMA IF NOT EXISTS workflow AUTHORIZATION svc_workflow;
EOSQL

