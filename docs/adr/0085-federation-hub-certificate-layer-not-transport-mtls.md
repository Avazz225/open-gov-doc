# 0085 — federation-hub-service: Zertifikatsebene statt echtem Transport-mTLS

**Status:** akzeptiert (Session 2 von 4, siehe Phase 21 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 21 Session 2, betrifft `federation-hub-service`

## Entscheidung

Der Plan beschreibt diese Session als "mTLS / echte Installations-Identität": Installationen sollen sich
über Client-Zertifikate statt nur signaturbasiert authentisieren, mit einer kleinen eigenen CA analog zu
`signature-service`s bereits vorhandener interner CA ([ADR 0025](0025-signature-service-internal-ca-and-connector-plugin.md))
als Vorbild. Eine genaue Prüfung ergab: [ADR 0039](0039-federation-trust-hardening-request-signing-over-mtls.md)
hat echtes Transport-mTLS für genau diesen Hub bereits einmal explizit geprüft und verworfen, mit einer
Begründung, die unverändert gilt — **kein einziger Service dieses Repos terminiert TLS selbst oder
verifiziert Client-Zertifikate**, alle internen Aufrufe laufen über einfaches HTTP im Docker-Compose-Netz;
ein isolierter mTLS-Sonderfall allein für den Hub (eigene Zertifikatsausstellung/-verteilung,
`ssl_cert_reqs=CERT_REQUIRED` in uvicorn, Zertifikats-Mounting in `infra/docker-compose.yml`) hätte keinen
Wiederverwendungswert für den Rest des Systems und würde eine im Projekt bislang nicht existierende
Betriebs-/Zertifikatsverwaltungs-Disziplin einführen. Eine erneute Prüfung des gesamten Repos bestätigt:
daran hat sich nichts geändert — keine TLS-Terminierung, kein Reverse Proxy/Ingress existiert irgendwo im
Stack.

Diese Session löst das auf, indem sie den Plan-Wortlaut **wörtlich beim Wort "Zertifikatsebene ergänzt"**
nimmt, nicht bei "mTLS": eine echte Zertifikatsebene wird ergänzt, aber weiterhin vollständig auf
Anwendungsebene, genau wie die bereits bestehende Signaturprüfung (ADR 0039 "mTLS-äquivalent auf
Anwendungsebene").

1. **`HubIdentity` wird zusätzlich zur eigenen kleinen Root-CA** - dasselbe RSA-2048-Schlüsselpaar, das
   der Hub ohnehin schon für `X-Federation-Hub-Signature` besitzt, wird zusätzlich als selbstsigniertes
   X.509-Zertifikat verpackt (`ca_certificate_pem`, neues Feld) - KEIN separates Schlüsselpaar, reine
   Zertifikatshülle, gleiche Bibliothek/Konvention wie `signature-service.connectors.internal.
   generate_root_ca` (ADR 0025).
2. **Jede Installation bekommt bei Registrierung UND bei jeder Schlüsselrotation ein vom Hub signiertes
   X.509-Zertifikat** (`Installation.certificate_pem`/`certificate_not_after`), das ihren öffentlichen
   Schlüssel bindet - vereinfachtes CSR-Äquivalent: der Hub erzeugt kein neues Schlüsselpaar, sondern
   zertifiziert den von der Installation selbst eingereichten, bereits per Signatur nachgewiesenen
   öffentlichen Schlüssel. Gültigkeit 1 Jahr (deutlich kürzer als `signature-service`s 5 Jahre, ADR 0025 -
   dort verhindert eine lange Laufzeit ein fälschliches "abgelaufen" bei späterer Prüfung ohne
   Zeitstempeldienst; dieses Problem gibt es hier nicht, eine kürzere Laufzeit gibt der Zertifikatsebene
   stattdessen einen echten, wiederkehrenden Erneuerungs-Rhythmus).
3. **`authenticate_signed_request` prüft zusätzlich zur bestehenden Signatur die vollständige
   Zertifikatskette bis zur Hub-CA, das Gültigkeitsfenster, UND dass Zertifikats-`CommonName` sowie
   eingebetteter öffentlicher Schlüssel tatsächlich zur aufrufenden Installation gehören** - bewusst
   ZUSÄTZLICH, nicht als Ersatz für die Signaturprüfung.
4. **Neuer `GET /ca-certificate`-Endpunkt** (Pendant zu `GET /public-key`) - Installationen können das
   Root-CA-Zertifikat beim ersten Kontakt abrufen und lokal pinnen (Trust-on-First-Use, Certificate-
   Pinning-Äquivalent).
5. **Nachhol-Migration**: alle vor dieser Session registrierten Installationen (`certificate_pem IS
   NULL`) bekommen beim nächsten Hub-Start automatisch ein Zertifikat ausgestellt (`main.lifespan`) -
   `authenticate_signed_request` überspringt die Zertifikatsprüfung nur für den kurzen Zeitraum, in dem
   eine Zeile noch kein Zertifikat hat (Bestandsschutz, sollte danach nicht mehr vorkommen).

## Begründung

- **Warum die Zertifikatsebene rein auf Anwendungsebene bleibt statt echtem Transport-mTLS**: siehe oben
  - ADR 0039s Begründung gilt unverändert, nichts an der Infrastruktur dieses Projekts hat sich seither
  geändert. Echtes Transport-TLS/mTLS bleibt weiterhin eine reine Deployment-Entscheidung des Betreibers
  (Reverse Proxy/Ingress), wie für jeden anderen Service auch (Konzept 10.3) - unverändert gegenüber
  ADR 0039s eigener Schlussfolgerung.
- **Warum das Hub-eigene Schlüsselpaar als CA wiederverwendet wird statt eines neuen, separaten
  CA-Schlüssels**: der Hub hat bereits eine vertrauenswürdige, per Trust-on-First-Use verteilte Identität
  (`GET /public-key`) - ein zweites, unabhängiges Schlüsselpaar allein für die CA-Rolle hätte keinen
  Sicherheitsgewinn (beide müssten gleichermaßen gegen Kompromittierung geschützt werden) und nur die
  Betriebskomplexität erhöht (zwei statt eines Schlüssels zu sichern/rotieren).
- **Warum die Zertifikatsprüfung Kette+Gültigkeit+Identitätsbindung ALLE VIER zusammen prüft**: eine erste
  Fassung dieser Session prüfte nur Kette+Gültigkeit, ohne zu verifizieren, dass das Zertifikat tatsächlich
  zur aufrufenden Installation gehört - beim Schreiben der Tests fiel auf, dass sich dadurch ein beliebiges,
  gültig vom Hub ausgestelltes Zertifikat (z. B. das einer anderen, unabhängigen Installation) unbemerkt
  hätte unterschieben lassen können, ohne dass die reine Kettenprüfung das bemerkt hätte (die
  Signaturprüfung mit dem tatsächlichen privaten Schlüssel bleibt zwar weiterhin die eigentliche
  Besitz-Beweisführung und verhindert einen vollständigen Auth-Bypass, aber die Zertifikatsebene selbst
  hätte ihr eigentliches Versprechen - "dieser Schlüssel gehört geprüft zu dieser Installation" - nicht
  eingehalten). Behoben, bevor es in den Code gelangte.
- **Warum `certificate_not_after` nur ein denormalisierter Anzeigewert ist, keine eigenständige Prüfung**:
  die tatsächliche Sicherheitsprüfung passiert immer aus den Zertifikats-Bytes selbst
  (`crypto_utils.verify_installation_certificate`), das gespeicherte Datum ist nur eine für Admin-UI/
  Migrationserkennung praktische Kopie desselben Werts.
- **Warum `POST /installations/{id}/rotate-key` zwingend ein neues Zertifikat ausstellen MUSS**: ein
  Zertifikat für den alten Schlüssel bliebe nach der Rotation gültig ausstellbar-geprüft, würde aber einen
  nicht mehr aktuellen Schlüssel binden - dieselbe Kategorie Bug wie der in ADR 0080 dokumentierte
  `reset_for_retry`-Fund dieser Roadmap-Phase, hier aber vor der Live-Verifikation im Design bereits
  vermieden.

## Konsequenzen

- **Migration bereits laufender Installationen**: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` im Lifespan
  für `ca_certificate_pem` (`hub_identity`) sowie `certificate_pem`/`certificate_not_after`
  (`installation`), gefolgt von einer Nachhol-Ausstellung für alle bereits bestehenden Installationen
  ohne Zertifikat.
- **Tests**: 55 (vorher 43, +12) - Hub-CA ist selbstsigniert und stabil über wiederholte Aufrufe,
  Registrierung stellt ein von der Hub-CA signiertes Zertifikat mit korrektem `CommonName` aus, ein
  selbstsigniertes (nicht vom Hub ausgestelltes) Zertifikat wird abgelehnt, ein abgelaufenes Zertifikat
  wird abgelehnt, Rotation stellt ein neues, an den neuen Schlüssel gebundenes Zertifikat aus,
  `list_installations_without_certificate` filtert korrekt, Bestandsschutz für Installationen ohne
  Zertifikat, ein für eine ANDERE Installation ausgestelltes (aber gültig vom Hub signiertes) Zertifikat
  wird bei fehlender Identitätsbindung abgelehnt, `GET /ca-certificate` liefert ein gültiges
  selbstsigniertes Zertifikat, Registrierungsantwort enthält ein prüfbares Zertifikat, Rotation reicht
  ein neues Zertifikat über die API durch.
- **Live gegen den echten laufenden Stack verifiziert** (Image-Neubau + Neustart): die
  Nachhol-Migration lief gegen **219 real bereits bestehende Installationen** aus früheren
  Live-Verifikationen dieser und vorheriger Sessions (Startup-Dauer 6,2s statt der üblichen ~150ms,
  danach alle 219 Zeilen mit `certificate_pem` bestätigt); `GET /ca-certificate` liefert ein echtes,
  selbstsigniertes Zertifikat; eine frisch registrierte Installation bekam ein Zertifikat, das
  nachweislich bis zur Hub-CA kettet, den korrekten `CommonName` trägt und den eingereichten öffentlichen
  Schlüssel bindet; eine anschließende Schlüsselrotation stellte ein neues, an den neuen Schlüssel
  gebundenes Zertifikat aus, weiterhin kettend zur selben Hub-CA.
- Doku: `docs/services/federation-hub-service.md` (Vertrauensmodell-Abschnitt, API-Tabelle, Datenmodell,
  "Offene Punkte" — die dort bereits als veraltet erkannte "Kein mTLS"-Zeile korrigiert, Test-Übersicht).
