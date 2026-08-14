# 0102 — Helm: Frontend-Apps als `services:`-Einträge, mit gehartem NEXT_PUBLIC_*-Build-Zeit-Limit

**Status:** akzeptiert (P26-S5, siehe `IMPLEMENTATION_PLAN.md`)
**Kontext:** Konzept 8, betrifft `infra/k8s/dms/` (Phase 26, Fortsetzung von [ADR 0099](0099-helm-single-chart-values-driven-service-map.md)/[ADR 0100](0100-helm-secrets-existing-secret-pattern.md)/[ADR 0101](0101-storage-cronjob-single-job-no-bulk-verify.md)), betrifft die 6 Next.js-Frontend-Apps unter `apps/` ([ADR 0006](0006-user-ui-static-export-spa.md))

## Entscheidung

Die 6 Next.js-Frontend-Apps (`user-ui`, `admin-ui`, `process-designer`,
`reviewer-ui`, `migration-console`, `office-addin`) werden als weitere
Einträge in `values.yaml`s `services:`-Map geführt und laufen über
dieselben generischen `templates/deployment.yaml`/`service.yaml`/
`hpa.yaml`/`pdb.yaml`-Templates wie die 32 zustandslosen FastAPI-Services
(kein neues Template nötig — aus k8s-Sicht sind sie ebenfalls "ein
Container auf einem Port", siehe ADR 0099). Zwei neue optionale, generische
Felder machen die strukturellen Unterschiede explizit statt sie zu
verstecken:

- **`healthCheckPath`** (Default `"/healthz"`): Liveness-/Readiness-Probe-
  Pfad. Die 6 Frontend-Apps setzen `"/"` (nginx liefert nur statische
  Dateien aus, kein FastAPI-Health-Endpunkt). Als Nebeneffekt dieser Session
  bekommen ALLE 37 bisherigen Deployments erstmals echte Probes —
  `templates/deployment.yaml` hatte seit P26-S1 GAR KEINE, obwohl jeder
  FastAPI-Service laut `docs/service-template.md` einen `/healthz`-Endpunkt
  mitbringt (siehe main.py jedes Services unter `services/`) — ein bislang
  unbemerkter Gap, siehe "Konsequenzen".
- **`staticFrontend: true`** (nur bei den 6 Frontend-Apps gesetzt): lässt
  `helm template`/`helm lint` mit `fail` hart abbrechen, sobald `env:`
  einen Schlüssel mit Präfix `NEXT_PUBLIC_` enthält.

**Kernentscheidung, die diesen Guard nötig macht**: Next.js' statischer
Export (`output: "export"`, ADR 0006) backt `NEXT_PUBLIC_*`-Variablen beim
`docker build` in den JS-Bundle ein (`apps/<name>/Dockerfile`: `ARG`/`ENV`
VOR `RUN npm run build`), nicht beim Container-Start — es gibt zur Laufzeit
keinen Node-Prozess, der eine Umgebungsvariable lesen könnte (`nginx:1.27-
alpine`-Stage liefert nur bereits fertige Dateien aus). Dieses Chart kann
`NEXT_PUBLIC_*`-Werte deshalb **grundsätzlich nicht** über `values.yaml`
o. Ä. zur Deploy-Zeit setzen — das ist keine übersehene Lücke, sondern eine
reale Eigenschaft von Next.js Static Export. Statt so zu tun, als wäre das
konfigurierbar (ein `env:`-Eintrag, der beim `helm template` klaglos
rendert, aber im laufenden Cluster nichts bewirkt), macht der `fail`-Guard
den Versuch, es trotzdem zu tun, sofort sichtbar statt es einen Betreiber
erst nach einem verwirrenden Live-Debugging-Vorgang selbst herausfinden zu
lassen.

**Produktiver Weg**: jede Zielumgebung mit einer eigenen öffentlichen
Gateway-Adresse (`NEXT_PUBLIC_GATEWAY_BASE_URL`) braucht ein EIGENES, vorab
per `docker build --build-arg NEXT_PUBLIC_GATEWAY_BASE_URL=... apps/<name>`
gebautes Image, dessen `image.tag` diese Zielumgebung mit-kodiert (z. B.
`prod-eu-2026-08` statt `latest`) — BEVOR dieses Chart es referenziert.
`values.yaml`s `image.repository`/`.tag` bei den 6 Frontend-Einträgen sind
deshalb keine reinen Versionsnummern wie bei den 32 FastAPI-Services,
sondern faktisch auch Umgebungs-Bezeichner.

## Begründung

- **`services:`-Map statt eigenem Frontend-Template**: die 6 Apps sind aus
  reiner k8s-Perspektive genauso "ein Container, ein Port, ein
  Deployment+Service(+HPA/PDB)" wie jeder FastAPI-Service — ein separates
  Template hätte den in ADR 0099 explizit vermiedenen Boilerplate-
  Vervielfachungseffekt reproduziert, ohne einen echten strukturellen Grund
  (anders als z. B. bei Postgres/Keycloak/MinIO/NATS/Redis, die eigene
  PVCs/Secrets/Healthchecks brauchen, siehe ADR 0099/0100). Die beiden
  neuen optionalen Felder (`healthCheckPath`/`staticFrontend`) reichen aus,
  um die tatsächlichen Unterschiede abzubilden.
- **`fail`-Guard statt reiner Doku-Warnung**: eine Doku-Warnung allein hätte
  denselben stillen Fehlschlag riskiert wie der `${DMS_POSTGRES_PASSWORD}`-
  Platzhalter aus P26-S1 (siehe ADR 0099/0100 "Konsequenzen") — syntaktisch
  gültig, semantisch wirkungslos, ohne jede Fehlermeldung. Ein `fail` zur
  Render-Zeit ist die einzige Helm-eigene Möglichkeit, diesen konkreten
  Fehlerfall (NEXT_PUBLIC_* in `env:`) nicht erst im laufenden Cluster
  bemerkbar werden zu lassen.
- **`healthCheckPath`-Default `"/healthz"` statt eines Pflichtfelds**: hält
  alle 32 bestehenden `values.yaml`-Einträge unverändert (kein
  Migrationsschritt für P26-S1..S4s Arbeit nötig) und behebt den Probe-Gap
  für sie automatisch als Nebeneffekt — siehe "Konsequenzen" für die
  Einordnung, warum das trotzdem in dieser Session mit erledigt statt nur
  dokumentiert wird (klein, risikoarm, direkt für die Korrektheit der
  Frontend-Probes ohnehin nötig).
- **`image.tag` als faktischer Umgebungs-Bezeichner statt eines Versuchs,
  das Problem "wegzuautomatisieren"**: denkbare Alternativen (z. B. ein
  Init-Container, der zur Laufzeit ein `config.json` generiert und die App
  clientseitig nachlädt) hätten eine echte Code-Änderung an allen 6 Apps
  bedeutet (neuer Fetch-Call vor dem eigentlichen App-Start, eigene
  Konfigurationsschicht) — außerhalb des Scopes einer reinen Helm-Chart-
  Session (P26-S5 betrifft laut `IMPLEMENTATION_PLAN.md` `infra/k8s/dms/`,
  nicht `apps/*`-Code) und wäre ohne eigene Tests/ADR/Doku-Update der
  betroffenen Apps unvollständig. Siehe "Konsequenzen" für einen
  Gestaltungsvorschlag, analog zu ADR 0101s Vorschlag für einen künftigen
  Bulk-Verify-Endpunkt.

## Konsequenzen

- **Kein `values.yaml`-Feld steuert die tatsächliche Gateway-Adresse einer
  laufenden Frontend-App** — das ist eine bewusst dokumentierte, nicht
  auflösbare v1-Grenze dieses Charts, kein Implementierungsversehen. Ein
  Betreiber, der die Gateway-Adresse ändern will, MUSS die betroffenen 6
  Images neu bauen (neuer `--build-arg`), nicht nur `helm upgrade` mit
  einem neuen `--set` aufrufen.
- **`services.gateway-service.ingress.host`** (siehe [ADR 0103](0103-helm-ingress-not-openshift-route.md))
  MUSS mit dem `NEXT_PUBLIC_GATEWAY_BASE_URL`-Build-Arg übereinstimmen, mit
  dem die 6 Frontend-Images gebaut wurden — kein Helm-Mechanismus in diesem
  Chart kann das automatisch synchron halten, da der Wert bereits vor
  `helm install` fest im Image steht. Ein Betreiber, der `ingress.host`
  ändert, ohne die Frontend-Images neu zu bauen, bekommt keinen
  Chart-Fehler (`helm template`/`lint` rendern klaglos), sondern erst zur
  Laufzeit fehlschlagende Cross-Origin-Fetches im Browser — dokumentierte
  Grenze, kein automatisierbarer Guard möglich (der Guard in dieser Session
  deckt nur den EINEN konkret prüfbaren Fall ab: NEXT_PUBLIC_* in `env:`).
- **`office-addin` ist eine Stufe extremer**: `apps/office-addin/
  manifest.xml` enthält fest eingebrannte `https://localhost:3006`-URLs
  (IconUrl/SupportUrl/AppDomain) — nicht mal ein Build-Arg, sondern eine im
  Quellcode/Image liegende XML-Datei (siehe deren eigener Kommentarblock,
  `docs/services/office-addin.md` "Offene Punkte"). Dieses Chart kann auch
  das nicht zur Deploy-Zeit umschreiben — betrifft denselben
  Umgebungs-Image-Bau-Vorbehalt wie oben, nur eine Ebene früher (Quelldatei
  statt Build-Arg).
- **Alle 37 bisherigen Deployments bekommen ab dieser Session echte
  Liveness-/Readiness-Probes** (vorher keine, siehe "Entscheidung") — ein
  in P26-S1..S4 unbemerkt gebliebener Gap, hier als Nebeneffekt des für die
  Frontend-Apps ohnehin nötigen `healthCheckPath`-Feldes behoben, nicht als
  eigener Arbeitsauftrag dieser Session. Probe-Timing (`initialDelaySeconds`/
  `periodSeconds`/`failureThreshold`) ist ein generischer, nicht je Service
  konfigurierbarer Kompromisswert (5s/10s/3 Readiness, 15s/20s/3 Liveness) —
  ausreichend für die schlanken FastAPI-Services und nginx-Frontends dieses
  Charts, aber kein Ersatz für eine service-individuelle Feinabstimmung,
  falls ein künftiger Service deutlich langsamer startet (analog zu
  Keycloaks bereits einzeln abgestimmten, großzügigeren Werten in
  `templates/keycloak.yaml`).
- **Gestaltungsvorschlag für eine künftige Session** (nicht Teil dieser
  Session, analog zu ADR 0101s Vorschlag für storage-service): ein
  Runtime-`/config.json`-Muster (ein kleines, von nginx ausgeliefertes
  JSON, das die App beim Start per `fetch` lädt, bevor sie die
  Gateway-Adresse tatsächlich braucht) würde `NEXT_PUBLIC_GATEWAY_BASE_URL`
  durch einen echten, zur Laufzeit über ein `ConfigMap`+Volume-Mount
  injizierbaren Wert ersetzen — würde aber eine Code-Änderung an allen 6
  Apps (`apps/*/src`) plus eigene Tests/ADR/Doku-Updates dieser Apps
  erfordern, kein reiner Helm-Chart-Baustein mehr.
