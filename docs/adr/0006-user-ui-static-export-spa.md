# 0006 — User-UI: Next.js als statisch exportierte SPA unter `apps/`, kein Node-Laufzeitserver

**Status:** akzeptiert
**Kontext:** Konzept 8, Session P4-S2 (User-UI Grundgerüst)

## Entscheidung

1. Das erste Frontend des Projekts (`apps/user-ui`) liegt unter einem neuen Top-Level-Ordner `apps/`, nicht unter `services/` — `docs/service-template.md` (Layout, `pyproject.toml`, Dockerfile) ist explizit auf Python-Services zugeschnitten und passt nicht auf eine Node/React-Build-Toolchain.
2. Next.js wird ausschließlich als React-Build-/Routing-Tooling genutzt, mit `output: "export"` (statischer Export). Es gibt **keinen Node-Prozess zur Laufzeit** — das Produktions-Image ist ein zweistufiger Build (Node nur im Build-Stage, `nginx:alpine` liefert die fertigen statischen Dateien aus).
3. Die Anwendung ist eine reine SPA: Login-Zustand, Ordner-Navigation, Upload/Download laufen komplett clientseitig gegen das API-Gateway (`/api/{service_type}/{path}`, siehe ADR 0005). Es gibt keinen eigenen Backend-Prozess für die UI selbst.
4. Tokens (Access + Refresh) liegen im `localStorage` des Browsers, nicht in einem httpOnly-Cookie.

## Begründung

- Punkt 2 und 3 setzen die im Konzept bereits **getroffene** Entscheidung (nicht nur Empfehlung) für Client-Side-Rendering direkt um: "SEO spielt keine Rolle" (Anwendung liegt hinter Login) und "vermeidet eine zusätzliche Node-Laufzeitschicht im Backend" (die Python-Services sollen nicht durch einen zusätzlichen Node-SSR-Prozess ergänzt werden müssen, der bei jedem Seitenaufruf gegen sie nachlädt). Ein statischer Export erfüllt exakt diese Vorgabe, ohne auf React/Next.js als Tooling zu verzichten.
- `apps/` statt `services/`: Vermeidet, das bewährte Python-Service-Template künstlich zu verbiegen (kein `pyproject.toml`, kein `pytest`, kein `uv sync --package`). Die Definition of Done aus `CONTRIBUTING.md` (README, Tests, Dockerfile, Compose-Eintrag, Service-Doku) gilt inhaltlich trotzdem, nur mit Node-typischem Tooling (`npm`, `vitest`, `eslint`) statt der Python-Äquivalente.
- **localStorage statt httpOnly-Cookie für Tokens**: Ein httpOnly-Cookie bräuchte einen Server, der es setzt (z. B. das Gateway müsste einen Session-Endpoint anbieten, der Login-Response in einen Cookie umwandelt) — das widerspräche der bewusst serverlosen Auslieferung dieser SPA (Punkt 2). `localStorage` ist die pragmatische Standardlösung für reine SPAs gegen eine JSON-API und für ein "Grundgerüst" ausreichend, birgt aber ein bekanntes XSS-Risiko (ein injizierter Skript-Schnipsel könnte Tokens auslesen). Bewusst dokumentierte Vereinfachung, keine übersehene Lücke.
- **Gateway-Adresse zur Build-Zeit fest eingebrannt** (`NEXT_PUBLIC_GATEWAY_BASE_URL`): Konsequenz aus "kein Server zur Laufzeit" — es gibt keinen Prozess, der zur Laufzeit Konfiguration nachladen könnte. Ein anderer Gateway-Endpunkt (z. B. andere Umgebung) erfordert einen Image-Rebuild mit anderem Build-Arg, analog zu anderen Umgebungsvariablen-Konventionen dieses Projekts, nur zur Build- statt Laufzeit ausgewertet.

## Konsequenzen

- Jede künftige Frontend-Anwendung (Admin-UI P4-S3, Reviewer-UI, Migrations-Konsole, Konzept 8) folgt demselben Muster (`apps/<name>`, statischer Export, nginx-Auslieferung) — eine analoge Kurzanleitung könnte künftig als `docs/frontend-template.md` festgehalten werden, sobald eine zweite Frontend-App entsteht (noch nicht Teil dieser Session, da mit nur einer Instanz noch keine stabile Schablone ableitbar ist).
- Ein härteres Auth-Modell (httpOnly-Session-Cookie über das Gateway, CSRF-Schutz) ist ein späterer Schritt, sobald Sicherheitsanforderungen über das Grundgerüst hinausgehen — erfordert dann einen eigenen, nicht mehr rein statischen Baustein (z. B. einen schlanken Session-Endpoint im Gateway).
- Kein Server-seitiges Datenvorladen möglich (kein SSR) — jede Seite zeigt beim ersten Laden kurz einen Ladezustand, bis der clientseitige Fetch gegen das Gateway abgeschlossen ist. Für die authentifizierte Kernanwendung wie im Konzept vorgesehen unproblematisch.
- Rendering/Preview (3.7/2.4) ist bewusst nur als Stub vorhanden (Modal mit Hinweistext) — echte Vorschauen (Thumbnails, PDF-Rendering) folgen mit dem Rendering/Preview Service (P5-S2) und einer entsprechenden Erweiterung dieser Komponente, keine Neuentwicklung der UI-Struktur nötig.
