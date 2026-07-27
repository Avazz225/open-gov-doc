# 0011 — OCR: Tesseract statt PaddleOCR als tatsächlich verdrahtete Standard-Engine

**Status:** akzeptiert
**Kontext:** Konzept 3.9, Session P5-S3

## Entscheidung

Der neue `ocr-service` implementiert die Konzept-Anforderung "automatische Erkennung, ob OCR überhaupt nötig ist" (3.9) über zwei Engines hinter einem gemeinsamen Plugin-Interface (`TextLayerExtractor`):

- **`NativeTextLayerEngine`**: liest bei PDFs mit einem bereits vorhandenen, nutzbaren Textlayer (Seite 1 ≥ 20 nicht-leere Zeichen) dessen Wörter samt Koordinaten direkt über PyMuPDF aus — keine Bilderkennung, Konfidenz immer `100.0`.
- **`TesseractEngine`**: für gescannte PDFs ohne nutzbaren Textlayer und für Rasterbilder direkt — `pytesseract` gegen den `tesseract`-Systembinary (`apt-get install tesseract-ocr tesseract-ocr-deu`).

**PaddleOCR, die vom Konzept namentlich genannte Standard-Engine, wird nicht implementiert** — nur die Plugin-Schnittstelle lässt eine künftige `PaddleOcrEngine`-Klasse zu, ohne bestehenden Code zu ändern.

## Begründung

- **PaddleOCR bringt `paddlepaddle` mit**, ein vollständiges ML-Framework (mehrere hundert MB, GPU-/CPU-Wheel-Auswahl, spürbare Downloadzeit) — für ein reproduzierbares `docker compose up --build` in dieser Entwicklungsumgebung nicht geeignet, gleiche Grundabwägung wie bei `ClamdEngine` vs. `EicarSignatureEngine` in [ADR 0010](0010-virus-scan-synchronous-gating.md).
- **Anders als bei ClamdEngine gibt es hier keine leichtgewichtige Teilimplementierung**: `ClamdEngine` ist ein dünner Protokoll-Client (INSTREAM gegen einen separat betriebenen `clamd`-Daemon) und wurde trotzdem vollständig gebaut, weil der Client selbst klein bleibt — nur der Daemon selbst ist die schwere Komponente und wird separat betrieben. PaddleOCR läuft dagegen **in-process** im OCR Service selbst; es gibt kein "nur der Client ist leicht"-Äquivalent. Eine PaddleOCR-Anbindung zu bauen hieße zwangsläufig, genau die schwere Abhängigkeit zu installieren, die vermieden werden soll. Deshalb: dokumentiert, nicht implementiert.
- **Tesseract ist bereits eine vollwertige, im Konzept selbst genannte Alternative** ("ressourcenschonende Alternative") — kein Kompromiss bei der fachlichen Abdeckung, nur eine andere Standardwahl als im Beispieltext vorgeschlagen.
- **Automatische Textlayer-Erkennung spart in der Praxis die meisten OCR-Läufe ohnehin ein**: born-digital-PDFs (die häufigste Dokumentart in einem DMS-Kontext) durchlaufen `NativeTextLayerEngine`, nicht Tesseract — die Wahl der Bild-OCR-Engine betrifft nur den kleineren Anteil tatsächlich gescannter/bildbasierter Dokumente.
- **Schwellenwerte bewusst einfach gehalten**: "Nutzbarer Textlayer" ab 20 nicht-leeren Zeichen auf Seite 1 (filtert leere/dekorative Deckblätter, ohne kurze aber echte Seiten falsch einzuordnen) und `needs_review` unterhalb `average_confidence < 70.0` (gängige Tesseract-Community-Heuristik) sind bewusst grobe, aber defensible Richtwerte — keine kalibrierte Studie, beides leicht über `Settings` anpassbar.

## Konsequenzen

- Mehrsprachige/mehrspaltige/tabellarische Layouts, bei denen PaddleOCR laut Konzept robuster sein soll, werden von Tesseract mit geringerer Genauigkeit erkannt — akzeptiert, da die Textlayer-Erkennung den PDF-Regelfall ohnehin nicht betrifft und Tesseract für den verbleibenden Bild-OCR-Anteil ausreichend ist.
- Ein Wechsel zu PaddleOCR (oder eine dritte Engine) bleibt jederzeit möglich, ohne `pipeline.py`/`select_engine()` zu ändern — nur eine weitere `TextLayerExtractor`-Implementierung registrieren.
- `tesseract-ocr`/`tesseract-ocr-deu` müssen als Systempaket im Docker-Image vorhanden sein (siehe `services/ocr-service/Dockerfile`) — in dieser lokalen Entwicklungsumgebung (außerhalb des Containers) ist kein `tesseract`-Binary installiert; die entsprechenden Tests sind mit `pytest.mark.skipif` versehen und werden stattdessen per Live-E2E im echten Container verifiziert (siehe `docs/services/ocr-service.md`).
