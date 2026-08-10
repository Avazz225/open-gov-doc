# office-addin

Microsoft-Office-Add-in (Office.js, nur **Word**) für native OG-Doc-Integration
(Konzept 3.3a), P14-S8. Öffnen/Speichern eines Dokuments direkt aus/in
OG Doc, inline Metadatenbearbeitung, Workflow-Start/-Fortsetzung, zentrale
rollenbasierte Vorlagenbibliothek — ohne die DMS-Oberfläche separat aufrufen
zu müssen. Spricht ausschließlich bereits bestehende `document-service`/
`workflow-service`/`object-type-service`/`folder-service`/`search-service`-
Endpunkte an, kein neuer Backend-Code (siehe
[ADR 0045](../../docs/adr/0045-office-addin-word-only-reused-endpoints-settings-linking.md)).

Reines Client-Side-Rendering, kein Node-Prozess in Produktion (identisches
Muster wie `apps/user-ui`, ADR 0006).

Ausführliche Doku: [`docs/services/office-addin.md`](../../docs/services/office-addin.md).

## Lokale Entwicklung

```bash
npm install
npm run dev
```

Erwartet ein laufendes Gateway auf `http://localhost:8009`:

```bash
cd ../../infra && docker compose up -d
```

## Build (statischer Export)

```bash
npm run build
```

## Tests

```bash
npm run typecheck
npm run lint
npm test
npx office-addin-manifest validate manifest.xml
```

## Docker

```bash
cd ../../infra && docker compose up -d --build office-addin
curl localhost:3006/
```

## Lokales Sideload-Testen (echter Word-Host)

Office lädt Add-in-Webinhalte nur über HTTPS. Dieser Stack läuft in
Entwicklung durchgehend über HTTP wie jeder andere Dienst — vor einem echten
Sideload-Test in Word sind zwei zusätzliche Schritte nötig, die **nicht**
Teil von `docker compose up` sind:

1. **TLS bereitstellen**, z. B. mit Microsofts eigenem Entwicklungszertifikat-
   Tool (erzeugt ein lokal vertrauenswürdiges Zertifikat):
   ```bash
   npx office-addin-dev-certs install
   ```
   und einen Reverse-Proxy (z. B. `nginx`/`caddy`) davor, der HTTPS auf
   Port 3006 terminiert - oder `npm run dev` durch einen HTTPS-fähigen
   Dev-Server ersetzen.
2. **`manifest.xml` anpassen**: alle `https://localhost:3006`-Platzhalter
   (`IconUrl`, `SourceLocation`, `bt:Url`, `AppDomain`) durch die tatsächlich
   erreichbare HTTPS-Adresse ersetzen.

Danach sideloaden:

```bash
npx office-addin-debugging start manifest.xml desktop
```

öffnet Word und aktiviert das Add-in automatisch. Alternativ manuell über
Word → Einfügen → Meine Add-ins → Hochladen meines Add-ins → `manifest.xml`
auswählen.

**Wichtig**: In dieser Entwicklungsumgebung (kein Windows/Office installiert)
konnte dieser Schritt selbst nicht durchgeführt werden - nur die
Manifest-Struktur wurde mit dem offiziellen `office-addin-manifest`-Tool
validiert (`npx office-addin-manifest validate manifest.xml` → "The manifest
is valid."). Ein Mensch sollte den Sideload-Test vor Produktivnutzung einmal
tatsächlich durchführen.
