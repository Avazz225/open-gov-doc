# 0103 — Helm: vanilla `Ingress` statt OpenShift `Route`, host-basiertes Routing

**Status:** akzeptiert (P26-S5, siehe `IMPLEMENTATION_PLAN.md`)
**Kontext:** Konzept 8/3.5, betrifft `infra/k8s/dms/` (Phase 26 — "Helm-Charts für k8s/**OCP**", Fortsetzung von [ADR 0099](0099-helm-single-chart-values-driven-service-map.md)/[ADR 0102](0102-helm-frontend-apps-buildtime-env-limitation.md)), löst den seit P26-S1 in `values.yaml`s `gateway-service`-Kommentar offen gelassenen Punkt ("reale OCP-Route/Ingress folgt in P26-S2/S5") ein

## Entscheidung

`templates/ingress.yaml` (neu, P26-S5) rendert ein `networking.k8s.io/v1`
**`Ingress`** (vanilla Kubernetes) je Eintrag in `.Values.services` mit
`ingress.enabled: true` — genutzt von `gateway-service` sowie den 6
Frontend-Apps (7 ausgehende öffentliche Routen insgesamt in dieser Session).
Es gibt **kein** natives OpenShift-`Route`-Objekt in diesem Chart.

Jeder Eintrag bekommt einen **eigenen Hostnamen** (`ingress.host`, z. B.
`user-ui.dms.local`), **nicht** einen gemeinsamen Host mit
Pfad-Präfix-Routing (z. B. `dms.local/user-ui`).

## Begründung

- **Ingress statt Route, obwohl die Phase explizit "k8s/OCP" heißt**:
  OpenShifts Standard-Router (HAProxy-basiert) nimmt reguläre
  `networking.k8s.io/v1`-`Ingress`-Objekte seit mehreren OCP-Versionen
  nativ entgegen (intern per Route-Adapter umgesetzt) — ein einziges
  `Ingress`-Template deckt damit sowohl vanilla-Kubernetes-Cluster als auch
  OpenShift-Cluster ab, ohne zwei parallele Template-Sätze pflegen zu
  müssen (derselbe DRY-Grundsatz wie in ADR 0099: keine unnötige
  Duplikation, wo ein gemeinsamer Nenner ausreicht). Ein natives
  `Route`-Objekt böte zusätzlich OCP-spezifische Fähigkeiten (z. B.
  `haproxy.router.openshift.io/*`-Annotationen, native
  edge/passthrough/reencrypt-TLS-Terminierungsarten, Wildcard-Routen ohne
  expliziten DNS-Eintrag je Host) — für die Bedürfnisse dieser Phase
  (öffentlicher HTTP(S)-Zugriff auf 6 statische Frontend-Apps + einen
  API-Gateway) reicht der `Ingress`-gemeinsame Nenner vollständig aus.
- **Bewusster v1-Scope-Cut, kein übersehener Teil des Phasennamens**: ein
  zusätzliches `templates/route.yaml` (OpenShift-`route.openshift.io/v1`)
  wäre eine reine Additiv-Ergänzung — dieselbe `services:`-Map, dieselben
  `ingress.*`-Felder, nur ein zweites Template mit anderer `apiVersion`/
  `kind` und ggf. `annotations`-Handling für Route-spezifische Extras. Für
  diese Session bewusst zurückgestellt (siehe "Konsequenzen") statt sie
  ungetestet/unverifiziert mitzuliefern — dieses Chart wurde ausschließlich
  gegen `helm lint`/`helm template` verifiziert (kein echtes OCP-Cluster in
  dieser Entwicklungsumgebung verfügbar, siehe ADR 0099 "Kein echtes
  Cluster-Deployment in Phase 26 gefordert"), ein tatsächlich auf einem
  realen OCP-Router getesteter Route-Baustein ließe sich mit dieser
  Einschränkung ohnehin nicht seriös als "verifiziert" behaupten.
- **Host-basiert statt Pfad-Präfix-basiert**: geprüft (`grep basePath
  apps/*/next.config.mjs`, kein Treffer) — keiner der 6 Next.js-Static-
  Exports setzt `basePath`/`assetPrefix`. Ihre `/_next/...`-Asset-Pfade
  sind deshalb root-relativ; mehrere Apps unter demselben Host mit
  reinem Pfad-Präfix (z. B. `dms.local/admin-ui/`) würden sich bei diesen
  Asset-URLs gegenseitig überschreiben (jede App würde versuchen,
  `/_next/...` an der Host-Wurzel zu laden, nicht unter ihrem eigenen
  Präfix) — ein `Ingress`-`path`-Rewrite allein löst das nicht, es bräuchte
  zusätzlich eine `next.config.mjs`-Änderung (`basePath` setzen) UND einen
  Rebuild aller 6 Images. Host-basiertes Routing braucht keine App-seitige
  Änderung und funktioniert mit dem bestehenden Next.js-Export unverändert.
- **`ingress.host`-Werte sind Dev-/Demo-Platzhalter** (`*.dms.local`,
  analog zu `global.installationId: "local-dev"` an anderer Stelle in
  `values.yaml`) — bewusst je Service `values.yaml`-konfigurierbar (kein
  hartkodierter Wert im Template), damit eine echte Installation sie ohne
  Chart-Änderung durch reale DNS-Namen ersetzen kann (`--set
  services.user-ui.ingress.host=...` oder eine eigene values-Overlay-Datei).
- **`office-addin` mit `tls.enabled: true` als einzige Ausnahme unter den
  6 Apps**: `docs/services/office-addin.md` dokumentiert eine harte
  HTTPS-Pflicht (Office lädt Add-in-Webinhalte grundsätzlich nur über
  HTTPS, abgesehen von wenigen lokalen Entwicklungsausnahmen) — die übrigen
  5 Apps sowie `gateway-service` haben `tls.enabled: false` als Default
  (funktionsfähig auch ohne TLS, ein produktiver Einsatz würde es
  typischerweise trotzdem aktivieren, ist hier aber keine harte
  Voraussetzung wie bei `office-addin`).
- **Kein cert-manager-Anbindung/automatische Zertifikatsausstellung**:
  `ingress.tls.secretName` erwartet ein bereits vorhandenes TLS-Secret,
  dieses Chart legt keins an. `ingress.annotations` steht als freies Feld
  zur Verfügung, über das ein Betreiber z. B.
  `cert-manager.io/cluster-issuer` selbst ergänzen kann, ohne dass dieses
  Chart eine bestimmte Zertifikatslösung voraussetzt oder mitbringt —
  gleiches Operator-agnostisches Prinzip wie bei `existingSecret`
  (ADR 0100).

## Konsequenzen

- Ein Betreiber, der die OCP-**nativen** Route-Fähigkeiten braucht (z. B.
  Wildcard-Subdomain-Routing ohne expliziten DNS-Eintrag je Host,
  HAProxy-Annotationen für Sticky-Sessions/Timeouts, native
  Passthrough-TLS-Terminierung), muss dafür entweder eigene, außerhalb
  dieses Charts verwaltete `Route`-Objekte anlegen (referenzieren dieselben
  `<fullname>-<service>`-Kubernetes-Services, die `templates/service.yaml`
  bereits rendert — kein Chart-Umbau nötig, nur ein zusätzliches, separat
  gepflegtes Manifest) oder auf eine künftige Chart-Erweiterung
  (`templates/route.yaml`, gleiche `services.<name>.ingress`-Felder wie
  hier) warten. Dokumentierter Gap, kein stiller.
- `services.gateway-service.ingress.host` MUSS mit dem
  `NEXT_PUBLIC_GATEWAY_BASE_URL`-Build-Arg übereinstimmen, mit dem die 6
  Frontend-Images gebaut wurden (siehe ADR 0102 "Konsequenzen") — eine
  Änderung an `ingress.host` allein (ohne Image-Rebuild) macht die
  Frontend-Apps nicht automatisch wieder funktionsfähig.
- `gateway-service.env.DMS_CORS_ALLOWED_ORIGINS` muss von Hand mit den 6
  `ingress.host`-Werten synchron gehalten werden (`values.yaml` listet
  aktuell alle 6 `https://<app>.dms.local`-Platzhalter plus den
  bestehenden `http://localhost:3000`-Compose-Dev-Fall) — kein
  Helm-Templating leitet das eine automatisch aus dem anderen ab (beide
  Werte stehen in derselben `values.yaml`-Datei, aber
  `DMS_CORS_ALLOWED_ORIGINS` ist ein roher JSON-String innerhalb von
  `services.gateway-service.env`, kein strukturiertes Feld, aus dem sich
  eine Cross-Referenz zu den 6 anderen `services.<name>.ingress.host`-
  Werten sauber ableiten ließe, ohne den generischen `env:`-Mechanismus für
  diesen einen Sonderfall aufzubrechen).
