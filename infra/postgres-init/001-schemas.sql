-- Schema-pro-Service-Konvention (Konzept 3.1). Dieses Skript legt nur das
-- keycloak-Schema an (Keycloak verwaltet sein eigenes Schema selbst, kein
-- DMS-Service). Jeder DMS-Service bekommt sein eigenes Schema stattdessen
-- über 002-service-roles.sh (P68-S1) - dort auch mit einer eigenen,
-- rechtebeschränkten Postgres-Rolle statt dieses Superusers, siehe
-- docs/adr/0199-per-service-postgres-roles.md.

CREATE SCHEMA IF NOT EXISTS keycloak;
