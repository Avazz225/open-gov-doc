from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "gateway-service"

    registry_service_base_url: str = "http://localhost:8001"
    instance_cache_ttl_seconds: float = 5.0

    keycloak_base_url: str = "http://localhost:8080"
    keycloak_realm: str = "dms"
    keycloak_client_id: str = "dms-api"

    # Browser-Frontends (user-ui/admin-ui, Konzept 8) laufen auf einer anderen
    # Origin als das Gateway - ohne CORS-Freigabe scheitert bereits der
    # Preflight-OPTIONS-Request mit 405, bevor der eigentliche Request
    # überhaupt gesendet wird (curl-basierte Tests decken das nicht ab, da
    # curl keinen Origin-Header setzt und damit keinen Preflight auslöst).
    cors_allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # "{service_type}:{path}" (Pfad ohne führenden Slash, exakter Match) - Routen,
    # die ohne Bearer-Token erreichbar sein müssen, allen voran Login/Refresh
    # selbst (man braucht ja erst einen Token, um einen Token zu bekommen).
    # Seit P6-S9 zusätzlich die beiden Federation-Hub-Inbound-Endpunkte (7.4) -
    # der Hub ist kein eingeloggter Principal, authentisiert sich stattdessen
    # über eine eigene Signatur (`X-Federation-Hub-Signature`, siehe
    # `workflow_service.main._verify_hub_signature`).
    # Seit P13-S2 zusätzlich vier Routen für den unabhängig betriebenen
    # `fleet-management-service` (3a) - der hat ebenfalls keinen eingeloggten
    # Principal dieser Installation. Die beiden Lese-Routen waren schon vor
    # dem Gateway-Zugriff ungegatet (`registry-service`/`license-service`
    # verlangen dort nie einen Principal); die beiden Schreib-Routen prüfen
    # stattdessen serverseitig den installationsweiten
    # `DMS_FLEET_AGENT_API_KEY` (siehe dortiges `_is_fleet_agent()`) - der
    # Gateway selbst kennt diesen Schlüssel nicht, lässt den `Authorization`-
    # Header nur unangetastet durch (kein Keycloak-Token-Zwang mehr auf
    # diesen vier Pfaden).
    # Seit P14-S10 zusätzlich die beiden öffentlichen Freigabelink-Endpunkte
    # (4.2a) - anonyme Betrachter besitzen keinen Bearer-Token, das Token des
    # Freigabelinks selbst reist stattdessen als Query-Parameter mit (`?token=`,
    # siehe document-service.main._resolve_active_share_link), damit diese
    # beiden Einträge simple, statische Exact-Match-Strings bleiben können,
    # ohne Wildcard-/Matching-Logik am Gateway selbst zu ändern.
    # Seit P15-S4 zusätzlich die föderierte Kontaktsuche (2.5/7.4) - eine
    # Peer-Installation besitzt keinen Bearer-Token dieser Installation,
    # authentisiert sich stattdessen über `X-Installation-Signature` (siehe
    # auth_service.main.federated_search_inbound, gleiches Prinzip wie
    # workflow-service's federation/inbound oben).
    # Seit P17-S1 `config-service:config/import` durch `config-service:config/
    # fleet-import` ersetzt (nicht nur ergänzt): der alte, gemeinsame Pfad für
    # sowohl den Fleet-Agent-Schlüssel als auch echte, eingeloggte Admins
    # bedeutete, dass der Gateway für JEDEN Aufruf dieses Pfads keinen
    # Bearer-Token validierte - `X-DMS-Principal` blieb dadurch auch für
    # echte Admins immer leer, der RBAC-Zweig von `config-service`s
    # `_require_import_permission` war faktisch unerreichbar (bei P17-S1
    # gefunden, als die erste Admin-UI-Anbindung für Konfigurationspakete,
    # 14.1, entstand). `POST /config/import` ist jetzt ein regulärer,
    # Keycloak-Token-pflichtiger Pfad; nur der neue, dedizierte
    # `config/fleet-import`-Pfad bleibt öffentlich, siehe
    # `config_service.main.fleet_import_config`.
    public_routes: list[str] = [
        "auth-service:login",
        "auth-service:refresh",
        "auth-service:users/directory/federated-search-inbound",
        "workflow-service:federation/inbound",
        "workflow-service:federation/inbound-result",
        "registry-service:installation",
        "license-service:license/status",
        "license-service:license",
        "config-service:config/fleet-import",
        "document-service:public/share-links",
        "document-service:public/share-links/content",
        # SSO/automatischer Login (Post-Roadmap-Feature): der Login-
        # Einstiegspunkt selbst und der Code-Austausch-Rückweg - an dieser
        # Stelle existiert noch kein DMS-Token, das der Bearer-Check prüfen
        # könnte.
        "auth-service:oidc/authorize",
        "auth-service:oidc/callback",
    ]

    # Not-Shutdown (4.8, P6-S6): während aktivem Wartungsmodus werden alle
    # proxied Requests mit 503 abgelehnt, außer diese Routen (Login/Refresh/Me/
    # Superuser-Status bleiben erreichbar, damit sich zumindest der Superuser
    # anmelden kann - `auth-service` selbst lehnt jeden anderen Benutzernamen
    # ab, siehe dortiges `POST /login`; die beiden Wartungsmodus-Endpunkte
    # selbst müssen naturgemäß während der Sperre erreichbar bleiben).
    maintenance_mode_allowed_routes: list[str] = [
        "auth-service:login",
        "auth-service:refresh",
        "auth-service:me",
        "auth-service:superuser/status",
        # SSO/automatischer Login (Post-Roadmap-Feature): gleiche Begründung
        # wie bei "auth-service:login" oben - auch während aktivem
        # Wartungsmodus muss sich zumindest der Superuser anmelden können,
        # unabhängig davon, über welchen der beiden Login-Wege.
        "auth-service:oidc/authorize",
        "auth-service:oidc/callback",
        "permission-service:maintenance-mode",
        "permission-service:maintenance-mode/lift",
    ]
    maintenance_cache_ttl_seconds: float = 5.0

    # Nutzer-Feedback: 120/60s löste bei ganz normaler interaktiver Nutzung
    # sehr schnell 429 aus - ein einzelner Seitenaufruf des Drei-Spalten-
    # Arbeitsbereichs feuert bereits ein Dutzend paralleler Aufrufe (Ordner,
    # Dokumente, Favoriten, Genehmigungskonfiguration, Kennzeichen-Config,
    # Objekttypen, Wartungsmodus-Poll alle 30s, ...), reales Klicken kam
    # dadurch innerhalb einer Minute leicht über 120 - kein Bug in Client
    # oder Auth, nur ein für dieses SPA zu knapp bemessener Default (siehe
    # PROGRESS.md). Deutlich großzügiger bemessen, bleibt aber ein reales
    # Sicherheitsnetz gegen tatsächlich außer Kontrolle geratene Clients.
    rate_limit_max_requests: int = 600
    rate_limit_window_seconds: float = 60.0

    upstream_timeout_seconds: float = 30.0
