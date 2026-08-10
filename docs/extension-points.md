# Erweiterungspunkte für die Backlog-Kandidaten aus 12.2

**Session:** P14-S3 — "Backlog-Kandidaten aus 12.2 ... sauber als Plugin-Erweiterungspunkte vorbereiten (nicht implementieren)"
**Konzept-Referenz:** 12.2, 3.3, 3.8

Dieses Dokument ist bewusst **keine Implementierung** der drei in Konzept 12.2 verbliebenen Erweiterungskandidaten (ERP-/Fachverfahren-Konnektoren, native Mobile Clients, KI-Funktionen) — es zeigt für jeden Kandidaten konkret, an welcher **bereits existierenden** Stelle im System er andocken würde und warum das ohne Eingriff in den Kern möglich ist. 12.2 behauptet das bereits generisch ("die Architektur ... ist so ausgelegt, dass sie sich später ohne Eingriff ins Grundgerüst ergänzen lassen") — dieses Dokument prüft diese Behauptung für jeden der drei verbliebenen Kandidaten einzeln am tatsächlichen Code nach, statt sie unbelegt stehen zu lassen.

Alle drei Abschnitte folgen demselben Aufbau: **Andockpunkt** (welcher bereits bestehende Mechanismus greift), **Was neu entstünde** (die eigentliche, hier bewusst nicht gebaute Komponente), **Bewusste Nicht-Entscheidungen** (was dieses Dokument explizit offen lässt, weil es eine fachliche/produktbezogene Festlegung wäre, keine technische).

---

## 1. Vorgefertigte Standard-Konnektoren zu ERP-/Fachverfahrenssoftware (z. B. DATEV, SAP)

### Andockpunkt

Folgt exakt dem bereits zweimal real umgesetzten Muster von `cmis-connector`/`webdav-connector` (3.3):

- **Neuer Service** `services/<x>-connector/` nach dem Standard-Service-Template (`docs/service-template.md`), keine Sonderstruktur.
- **DMS-seitige Baumzugriffslogik** über das bereits bestehende, echte Connector-SDK `libs/dms-connector-sdk` (`DmsTreeClient` — Lesen/Schreiben/Metadaten/Sperren/Versionierung gegen `document-service`/`folder-service`, siehe `dms_connector_sdk/dms_tree_client.py`) statt eigener Neuimplementierung dieser Seite. Ein ERP-Konnektor bräuchte nur eine eigene Protokoll-/Client-Bibliothek für die **ERP-seitige** Verbindung (z. B. eine DATEV-API-Anbindung oder ein SAP-RFC-/OData-Client) — dieselbe Aufteilung, die `cmis-connector` (CMIS-1.1-Browser-Binding, ADR 0036) und `webdav-connector` (`wsgidav`) bereits jeweils für ihre externe Protokollseite haben.
- **Capability-Beschreibung** über `dms_connector_sdk.ConnectorDescriptor`/`ConnectorCapability` (`protocol="datev"`, `capabilities=frozenset({...})` aus `{"read","write","metadata","locking","versioning"}`, wörtlich Konzept 3.3s Aufzählung) — ein ERP-Konnektor würde realistisch nur eine Teilmenge deklarieren (z. B. nur `read`+`metadata` für einen ersten, rein lesenden Rechnungsabgleich, ohne Sperren/Versionierung), das Descriptor-Format erzwingt keine Vollständigkeit.
- **Registrierung** über die bereits bestehende `maybe_start_registration()` aus `libs/dms-registry-client` — identischer Aufruf wie bei jedem anderen Service, keine Änderung an `registry-service` oder `gateway-service` nötig. Der Gateway ist ein vollständig generischer Proxy (`@app.api_route("/api/{service_type}/{path:path}")`, `services/gateway-service/src/gateway_service/main.py`) — ein neuer `service_type` ist automatisch unter `/api/<service_type>/...` erreichbar, sobald er sich selbst registriert.
- **Lizenzierung** über einen einzigen neuen Eintrag in `registry-service`s `licensable_components`-Dict (`settings.py`, z. B. `"datev-connector": "demo"`) plus denselben lokalen Selbstdurchsetzungs-Mechanismus, den `cmis-connector`/`webdav-connector`/`migration-service`/`workflow-service` bereits jeweils für sich selbst implementieren (eigener `LicenseStatusClient`, eigener `_check_license`-Gate vor jedem schreibenden Aufruf) — `registry-service` selbst blockiert nichts, es ist nur die Status-Quelle.

### Was neu entstünde

- Die eigentliche Protokollanbindung an die jeweilige Fremdsoftware (DATEV-API-Client, SAP-RFC/OData-Client o. ä.) — das ist der tatsächliche Entwicklungsaufwand, den 12.2 zu Recht als "vorkonfiguriert, aber technisch möglich" einordnet.
- Eine Mapping-Schicht zwischen den ERP-eigenen Datenstrukturen (z. B. DATEV-Belegen, SAP-Geschäftsobjekten) und dem DMS-Baummodell (Ordner/Dokument/Attribute) — vergleichbar der bereits bestehenden CMIS-↔-DMS-Übersetzung im `cmis-connector`.
- Optional: ein Manifest beim Plugin Orchestration Service (`POST /plugins/{plugin_type}`, Felder `scaling_type`/`resource_cpu_cores`/`resource_ram_mb`/`load_profile`/`dependencies` — siehe `docs/services/plugin-orchestration-service.md`). **Ehrliche Einschränkung**: dieser Service ist heute eine reine Entscheidungs-/Audit-Engine ohne echten Container-Lifecycle-Zugriff — in der real existierenden Docker-Compose-Umgebung (ein einziger Knoten, kein Kubernetes/Swarm) trifft er zwar First-Fit-Decreasing-Empfehlungen, aber niemand/nichts setzt sie automatisch um. Ein Manifest für einen neuen ERP-Konnektor wäre also schon heute technisch möglich, aber praktisch folgenlos, bis diese Lücke (siehe `docs/services/plugin-orchestration-service.md` "Offene Punkte") in einer eigenen künftigen Session geschlossen wird.

### Bewusste Nicht-Entscheidungen

- **Welches ERP/welche Fachverfahrenssoftware zuerst** (DATEV vs. SAP vs. etwas anderes) — reine Marktnachfrage-Entscheidung, siehe 12.2s eigene Priorisierung ("z. B. DATEV, SAP").
- **Bidirektionale vs. rein lesende Anbindung** — ob ein erster Konnektor nur Belege in das DMS einliest oder auch zurückschreibt, ist eine Scope-Entscheidung für die tatsächliche Umsetzungssession, nicht heute vorwegzunehmen.

---

## 2. Native Mobile Clients (iOS/Android)

### Andockpunkt

Anders als die beiden übrigen Kandidaten ist ein Mobile Client kein neuer **Backend**-Service, sondern ein neuer **Konsument** der bereits bestehenden Backend-API — strukturell näher an den bestehenden Web-UIs als an einem Connector:

- **Kein neuer Gateway-/Backend-Code nötig für den reinen Datenzugriff**: jede vorhandene Web-UI (`apps/user-ui`) spricht bereits ausschließlich über `/api/{service_type}/{path}` mit dem Gateway - eine native App würde denselben Weg nutzen, keine separate "Mobile-API" nötig. Konzept 8s bewusste CSR-Entscheidung (kein SSR-Zwischenlayer) bedeutet außerdem, dass die Web-UIs selbst schon reine JSON-API-Konsumenten sind - ein Mobile Client unterscheidet sich vom bestehenden User-UI-Frontend technisch nur im Rendering (native statt Next.js/React-DOM), nicht im Backend-Zugriffsmuster.
- **Authentifizierung braucht einen zweiten, eigenen Keycloak-Client, keine neue Auth-Architektur**: der bestehende `dms-api`-Client (`auth-service/bootstrap.py`, `ensure_realm_and_client()`) ist ein **vertraulicher** Client (`publicClient: False`) mit Resource-Owner-Password-Grant (`keycloak_client.py`, `grant_type: "password"`) - passend für die bereits vorhandenen, selbst betriebenen Erstanbieter-Clients (Web-UIs, CLI), aber laut OAuth2-Best-Practice **nicht** geeignet für einen verteilten, nicht vertrauenswürdigen nativen App-Client (kein Client-Secret sicher einbettbar). Ein Mobile Client bräuchte einen zweiten Keycloak-Client (`dms-mobile` o. ä., `publicClient: True`, kein Secret) mit **Authorization Code + PKCE** statt Password Grant - von Keycloak nativ unterstützt, ohne dass `auth-service` seine bestehende Token-Validierung (`dms-auth-client`s `TokenValidator` gegen Keycloaks JWKS) ändern müsste: ein per PKCE ausgestelltes Access-Token ist für jeden nachgelagerten Service ununterscheidbar von einem per Password Grant ausgestellten.
- **Offline-Zugriff mit lokaler Verschlüsselung** (12.2, wörtlich gefordert) ist eine reine Client-seitige Eigenschaft (lokale verschlüsselte Ablage auf dem Gerät) - berührt das Backend nicht.

### Was neu entstünde

- Die eigentliche native App (iOS/Android) - vollständiger, eigenständiger Entwicklungsaufwand außerhalb dieses Backend-Systems.
- Der zweite Keycloak-Client (`dms-mobile`) plus die zugehörige Bootstrap-Ergänzung in `auth-service` (analog zur bereits bestehenden `ensure_realm_and_client()`-Idempotenz für `dms-api`).
- **Push-Benachrichtigungen als vierter Kanal in `notification-service`**: der bestehende Kanal-Typ ist bereits eine geschlossene, austauschbare Aufzählung (`Channel = Literal["email", "in_app", "webhook"]`, `notification_service/schemas.py`) mit einer kleinen Dispatch-Verzweigung in `repository.py` - ein `"push"`-Kanal (APNs/FCM) wäre strukturell derselbe minimale Eingriff wie das Hinzufügen von `"webhook"` seinerzeit, kein architektonischer Umbau.
- Volltext-/Geo-Suche unterwegs (12.2 nennt das als Referenzfunktion) - technisch bereits über `search-service`s bestehende API abbildbar, eine Geo-Komponente existiert heute nicht und wäre ein eigenständiger, hier nicht mitgeplanter Erweiterungspunkt von `search-service` selbst (nicht spezifisch für Mobile).

### Bewusste Nicht-Entscheidungen

- **Ob Mobile-Zugriff selbst eine eigene lizenzierbare Dimension wird** (z. B. "Mobile-Zugriff" als Konzept-9.1-Applikationskomponente) oder einfach Teil der bestehenden Nutzeranzahl-Lizenzierung bleibt - dieses Dokument erfindet dafür bewusst keinen Mechanismus, da unklar ist, ob das fachlich überhaupt gewünscht ist (anders als bei den beiden übrigen Kandidaten gibt es hier keinen naheliegenden, bereits etablierten `licensable_components`-Anwendungsfall, da ein Mobile Client kein separat registrierbarer `service_type` ist).
- **Konkretes App-Framework** (natives Swift/Kotlin vs. React Native/Flutter) - reine Umsetzungsentscheidung der tatsächlichen Session, technisch für das Backend irrelevant, da ohnehin nur die bestehende JSON-API konsumiert wird.

---

## 3. KI-Funktionen (Dokumenten-Chat, automatische Zusammenfassung, Prozessunterstützung)

### Andockpunkt

Modelliert am nächsten auf `signature-service`s Provider-Plugin-Muster (ADR 0025), da beide dasselbe Grundproblem lösen: eine fachliche Fähigkeit, hinter einer stabilen Schnittstelle austauschbar, mit einem bewusst vorgesehenen, aber (noch) nicht implementierten zweiten Anbietertyp als direktem Präzedenzfall:

- **Neuer, eigenständiger Service** `services/ai-service/` (echtes "Dazustellen"-Prinzip, 1./3.8: Abwesenheit ist ein regulärer Zustand, keine Installation muss ihn deployen) statt eine KI-Fähigkeit fest in bestehende Services einzubauen.
- **Provider-Abstraktion nach demselben Muster wie `SignatureProviderConnector`** (`signature_service/connectors/interface.py`): ein `AIProviderConnector`-Interface (z. B. `summarize(document_text: str) -> str`, `answer(document_text: str, question: str) -> str`) mit **installationsweise per Konfiguration wählbarem Anbietertyp** — genau wie `SignatureProviderConfig.type: Literal["internal","qtsp"]` bereits heute einen echten, funktionierenden Typ (`"internal"`) neben einem bewusst reservierten, aber nicht implementierten Typ (`"qtsp"`, wirft `ValueError` bei Auswahl, ADR 0025 "Konsequenzen") führt, könnte ein `AIProviderConfig.type: Literal["local","external_api"]` von Anfang an genauso strukturiert werden — `"local"` als tatsächlich lauffähige Referenzimplementierung (z. B. ein selbst gehostetes, quelloffenes Modell), `"external_api"` als bewusst reservierter, noch nicht implementierter Platzhalter für eine spätere Anbindung an einen externen KI-Anbieter. Dieselbe Factory-Dispatch-Struktur (`build_connector()`) wie bei `signature_service/connectors/__init__.py`.
- **Keine eigene Dokumenteninhalts-Erschließung** — ein KI-Service würde bewusst auf bereits vorhandener Infrastruktur aufsetzen statt sie zu duplizieren: Dokumentinhalt über `document-service`, bereits extrahierter Textlayer über den OCR-Service (3.9, `ocr_service.engines`) bzw. den Suchindex über `search-service` (3.7a) — dieselbe Wiederverwendung, die 3.9 selbst schon zwischen Suche und Ersatzdarstellung (2.4) beschreibt ("dieselbe Infrastruktur wird für zwei Zwecke genutzt").
- **Registrierung/Lizenzierung** identisch zu jedem anderen "Dazustellen"-Service: `maybe_start_registration()`, ein neuer Eintrag in `registry-service`s `licensable_components` (z. B. `"ai-service": "lock"` — vollständige Sperre statt Demo-Modus liegt näher, da eine "Demo"-Zusammenfassung fachlich schwer sinnvoll einzuschränken wäre, anders als bei reinen Lesezugriffs-Beschränkungen).
- **Bereits etablierter Präzedenzfall für "bewusst draußen gelassen"**: Konzept 5.4 hat KI-/ML-gestützte Anomalieerkennung bereits mit identischer Begründung ausgeschlossen ("KI-/ML-gestützte Anomalieerkennung ist bewusst out of scope ... eine spätere Ergänzung bleibt durch die 'Dazustellen'-Architektur jederzeit möglich") - dieses Dokument verallgemeinert dieselbe Begründung nur auf die in 12.2 zusätzlich genannten Anwendungsfälle (Chat/Zusammenfassung/Prozessunterstützung).

### Was neu entstünde

- Der eigentliche `ai-service` inkl. Provider-Interface und mindestens einer echten `"local"`-Implementierung.
- Neue Workflow-Task-Art (7.1) für "Prozessunterstützung" (z. B. ein automatischer Vorschlag an einer Gateway-Entscheidung) - technisch ein weiterer Automatic-/Service-Task-artiger Baustein, analog zum bereits bestehenden generischen `connector_call`-Service-Task-Mechanismus aus P12-S2 (`spiff_adapter.ConnectorServiceTask`), der bereits heute jeden beliebigen externen Service aus einem BPMN-Prozess heraus aufrufen kann, ohne dass `workflow-service` den aufrufenden Service kennen muss.

### Bewusste Nicht-Entscheidungen

- **Datenschutz-/Governance-Frage bei einem externen KI-Anbieter** (`type: "external_api"`): sobald Dokumentinhalte an einen externen Dienst gehen, entsteht eine neue Klasse von Auditierungs-/Compliance-Anforderungen (welche Inhalte verlassen die Installation, unter welcher Rechtsgrundlage) - dieses Dokument löst das bewusst nicht, es benennt nur, dass die Provider-Abstraktion diese Entscheidung sauber isoliert (eine Installation, die das nicht will, aktiviert einfach nur `type: "local"` oder deployt `ai-service` gar nicht).
- **Konkrete Modellwahl** für die `"local"`-Referenzimplementierung - reine Umsetzungsentscheidung einer späteren Session, abhängig vom Stand quelloffener Modelle zum Umsetzungszeitpunkt.

---

## Zusammenfassung: gemeinsames Muster

Alle drei Kandidaten bestätigen dieselbe strukturelle Aussage aus 12.2: keiner von ihnen verlangt eine Änderung an `registry-service`s Registrierungsprotokoll, an `gateway-service`s Proxy-Logik, oder an einem der bestehenden Kern-Services (`document-service`/`folder-service`/`permission-service`/...). Die drei bereits real existierenden, unterschiedlich gelagerten Erweiterungsmechanismen decken alle drei Fälle ab:

| Kandidat | Nächstliegendes reales Vorbild | Art der Erweiterung |
|---|---|---|
| ERP-/Fachverfahren-Konnektoren | `cmis-connector`/`webdav-connector` (3.3) | Neuer Service, Connector-SDK-Wiederverwendung |
| Native Mobile Clients | `apps/user-ui` (8) | Neuer Konsument derselben API, zweiter Keycloak-Client |
| KI-Funktionen | `signature-service`-Provider-Plugin (ADR 0025) | Neuer Service, Provider-Abstraktion mit reserviertem Zweit-Typ |

Einzige echte, heute noch bestehende Lücke, die alle drei potenziell beträfe, falls eine künftige Umsetzung automatische Platzierung/Skalierung braucht: der Plugin Orchestration Service trifft bereits heute reale Platzierungsentscheidungen, aber nichts setzt sie in der bestehenden Docker-Compose-Umgebung automatisch um (siehe `docs/services/plugin-orchestration-service.md` "Offene Punkte") - eine vorhandene, aber bewusst unvollständige Grundlage, keine neue Erkenntnis dieser Session.
