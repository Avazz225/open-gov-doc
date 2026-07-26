# 0005 — Gateway: Registry-basiertes Routing, In-Prozess-Rate-Limiting, zentrale Auth-Validierung

**Status:** akzeptiert
**Kontext:** Konzept 3.5, Session P4-S1 (API-Gateway/BFF)

## Entscheidung

1. Das Gateway löst Ziele **dynamisch über die Registry** auf (`GET /instances/{service_type}`,
   kurz gecached), statt Backend-Adressen statisch zu konfigurieren. Dafür registrieren sich
   erstmals tatsächlich Backend-Services selbst bei der Registry — bisher (seit P1-S1) existierte
   nur die Registry-API selbst, ohne einen einzigen Konsumenten/Producer. Die Selbst-Registrierung
   ist in eine neue geteilte Lib `dms-registry-client` ausgelagert (Register-beim-Start +
   periodischer Heartbeat + Deregister-beim-Shutdown) und in sieben Services verdrahtet
   (auth/permission/storage/document/object-type/folder/audit-service).
2. Auth-Validierung (JWT gegen Keycloak-JWKS) läuft **zentral im Gateway**, nicht mehr potenziell
   dupliziert in jedem einzelnen Backend-Service. Erfolgreich geprüfte Identität wird als
   `X-DMS-*`-Header an den Downstream weitergereicht, aber von den Backend-Services aktuell noch
   nicht konsumiert (siehe Konsequenzen).
3. Rate Limiting ist ein einfacher **in-process Sliding-Window-Zähler** je Client (kein Redis o. Ä.).

## Begründung

- **Registry statt statischer Konfiguration**: Das Konzept verlangt explizit "Routing zu Backend-
  Services (unter Nutzung der Registry)" (3.5). Eine statische Adressliste im Gateway hätte die
  Registry als bereits existierenden, aber bisher ungenutzten Baustein ignoriert und wäre bei jeder
  neuen Service-Instanz manuell nachzupflegen gewesen — widerspricht dem "Dazustellen"-Prinzip.
- **Selbst-Registrierung als geteilte Lib statt Kopieren in sieben Services**: Register/Heartbeat/
  Deregister ist reine Boilerplate ohne fachlichen Bezug zum jeweiligen Service (analog zu
  `dms-eventbus-client`/`dms-auth-client`). Fehler beim Erreichen der Registry werden geloggt, aber
  nicht weitergeworfen — ein Backend-Service darf nicht an einer kurzzeitig nicht erreichbaren
  Registry scheitern, Discovery ist ein Zusatznutzen, kein Hard-Dependency.
- **Zentrale statt verteilter Auth-Validierung**: Entspricht direkt dem BFF-Muster aus 3.5
  ("Zentrales API-Gateway für Authentifizierung"). Vermeidet, dass künftig jeder neue Service seine
  eigene JWKS-Validierungslogik mitbringen muss.
- **In-Prozess-Rate-Limiting statt Redis**: Für eine einzelne Gateway-Instanz (aktueller Stand,
  keine horizontale Skalierung vorgesehen) ist ein gemeinsamer externer Store unnötige Komplexität.
  Die Grenze ist bewusst dokumentiert (siehe Konsequenzen), keine versteckte Annahme.

## Konsequenzen

- Backend-Services konsumieren die vom Gateway weitergereichten Identitäts-Header
  (`X-DMS-Principal`/`X-DMS-Username`/`X-DMS-Roles`) noch nicht — Endpunkte, die einen Principal
  brauchen (z. B. `permission-service`s `/check`), nehmen ihn weiterhin explizit als Parameter
  entgegen. Das Verdrahten folgt, sobald eine UI/BFF-Session (P4-S2/S3) diese Header tatsächlich
  benötigt; kein Bruch bestehender Schnittstellen, da es sich um zusätzliche, optionale Header
  handelt.
- Backend-Services selbst validieren weiterhin keine Bearer-Token (bis auf den Auth Service selbst,
  der `/me` schon vorher validierte) — sie vertrauen implizit darauf, nur über das Gateway erreicht
  zu werden. In der aktuellen Docker-Compose-Umgebung sind ihre Ports trotzdem direkt nach außen
  veröffentlicht (Entwickler-Komfort); ein echtes Netzwerk-Perimeter (Backend-Ports nicht öffentlich)
  ist ein späterer Deployment-/Infrastruktur-Schritt, kein Code-Thema dieser Session.
- Skaliert das Gateway horizontal, umgeht ein Client das Rate Limit über mehrere Instanzen (kein
  geteilter Zähler) — bei Bedarf später auf einen externen Store (Redis) umstellen, ohne die
  `RateLimiter`-Schnittstelle (`allow(key) -> bool`) selbst zu ändern.
- Instanzauswahl bei mehreren gesunden Kandidaten ist reines Zufalls-Load-Balancing, kein
  Least-Connections/Latenz-bewusstes Routing — ausreichend für den aktuellen Entwicklungsstand ohne
  echte parallele Skalierung eines Backend-Service-Typs.
