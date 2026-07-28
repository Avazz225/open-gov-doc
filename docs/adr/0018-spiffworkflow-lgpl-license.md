# 0018 — SpiffWorkflow als LGPLv3-Abhängigkeit akzeptiert

**Status:** akzeptiert
**Kontext:** Konzept 13 (offener Punkt "SpiffWorkflow-Lizenz"), `IMPLEMENTATION_PLAN.md` benannte den Lizenz-Check explizit als Voraussetzung vor P6-S1 (Workflow Engine Grundgerüst). Getroffen im Rahmen einer Konsolidierungs-Session offener Entscheidungen nach Abschluss von Phase 5b, nicht innerhalb einer eigenen P-Session.

## Entscheidung

`SpiffWorkflow` (LGPLv3) wird als unveränderte Python-Dependency für die Workflow Engine (P6-S1) akzeptiert. Kein Wechsel auf eine alternative Engine, keine Verzögerung von P6-S1 auf eine förmliche externe Rechtsprüfung.

## Begründung

- **LGPLv3 unterscheidet zwischen der Bibliothek selbst und ihrer Nutzung als Abhängigkeit**: Die Copyleft-Pflicht (Quelloffenlegung bei Verbreitung) greift bei unveränderter Nutzung als Library-Dependency nicht auf den Gesamtcode des nutzenden Systems durch — sie greift nur, wenn `SpiffWorkflow` selbst modifiziert und diese Modifikation verbreitet wird. Dieses Projekt bindet `SpiffWorkflow` unverändert über `uv`/PyPI ein (kein Fork, kein Patch), genau der Fall, den LGPL von der stärkeren GPL-Copyleft-Wirkung ausnimmt.
- **Kein struktureller Unterschied zu bereits bestehenden Abhängigkeiten dieses Repos**: Das Projekt nutzt bereits diverse Open-Source-Bibliotheken unter verschiedenen Lizenzen (Apache-2.0, MIT, BSD) als reine Dependencies, ohne deren Lizenzbedingungen auf den eigenen Code durchschlagen zu lassen — SpiffWorkflow als Dependency unterscheidet sich lizenzrechtlich nicht in der Kategorie, nur in der spezifischen Lizenzfamilie (Copyleft statt permissiv).
- **Kein Ersatz mit vergleichbarer Reife verfügbar**: `bpmn-js-spiffworkflow` (Frontend-Gegenstück für P6-S6) ist speziell für SpiffWorkflow gebaut — ein Wechsel der Engine hätte auch den Process-Designer-Ansatz betroffen. Keine andere Python-BPMN-Engine mit vergleichbarem Funktionsumfang (Manual/Automatic Tasks, Timer/Boundary Events für P6-S2, Signature-Task-Typ-Erweiterbarkeit für P6-S5) wurde identifiziert, die eine permissivere Lizenz hätte.
- **Diese Einschätzung ist keine Rechtsberatung**: Sie ist eine technische/pragmatische Bewertung im Rahmen der Projektentwicklung, keine Ersetzung einer förmlichen juristischen Prüfung. Falls das System künftig extern (an Dritte, als Closed-Source-Produkt) vertrieben werden soll, ist diese Einschätzung vor einem solchen Schritt erneut zu prüfen — für den aktuellen internen Entwicklungs-/Testbetrieb wird sie als ausreichend angesehen, um P6-S1 nicht länger zu blockieren.

## Konsequenzen

- P6-S1 kann ohne weitere Vorbedingung starten — der bisher in `IMPLEMENTATION_PLAN.md` als Gate formulierte "Lizenz-Check LGPLv3 zuerst"-Zusatz entfällt.
- Falls SpiffWorkflow künftig selbst modifiziert werden müsste (z. B. ein Patch für einen fehlenden BPMN-Task-Typ), greift die LGPL-Copyleft-Pflicht für genau diese Modifikation — dieser Fall ist aktuell nicht geplant, aber als Bedingung dieser Entscheidung festgehalten.
- Bei einem künftigen Fremdvertrieb des Gesamtsystems ist diese ADR als "vorläufig, für internen Betrieb" zu kennzeichnen und die Lizenzfrage erneut mit tatsächlicher Rechtsberatung zu klären.
