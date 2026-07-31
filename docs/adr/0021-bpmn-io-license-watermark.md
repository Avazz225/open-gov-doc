# 0021 — bpmn.io-Lizenz (Wasserzeichen) für den Process Designer akzeptiert

**Status:** akzeptiert
**Kontext:** Roadmap-Vorausplanung nach P6-S2 (Rest von Phase 6 vorab recherchiert, damit künftige Sessions schneller starten können). Konzept 1a/8 nennt `bpmn-js` explizit als Beispiel für die BPMN-2.0-Modellierungskomponente des Process Designer (jetzt P6-S8, ehemals P6-S6), `bpmn-js-spiffworkflow` (sartography) ergänzt die SpiffWorkflow-spezifischen Eigenschaften (Skript-Editor, Properties Panel). Bei der Lizenzprüfung festgestellt: `bpmn-js`/`dmn-js`/`form-js`/`cmmn-js` (die bpmn.io-Toolkits) stehen unter der eigenen **"bpmn.io License"**, nicht unter MIT/Apache-2.0. `bpmn-js-spiffworkflow` selbst ist MIT-lizenziert, hat aber `bpmn-js` als Kernabhängigkeit und vererbt damit dessen Lizenzbedingung faktisch.

## Entscheidung

`bpmn-js`/`bpmn-js-spiffworkflow` werden wie im Konzept vorgeschlagen als unveränderte Dependency für den Process Designer (P6-S8) verwendet, samt dem von der bpmn.io-Lizenz vorgeschriebenen, fest einprogrammierten Wasserzeichen (Link zu bpmn.io) auf jedem gerenderten BPMN-Diagramm.

## Begründung

- **Die bpmn.io-Lizenz erlaubt uneingeschränkte kommerzielle Nutzung, Modifikation und Weiterverbreitung** — die einzige Sonderbedingung gegenüber einer Standard-MIT-Lizenz ist: der Code, der das bpmn.io-Wasserzeichen rendert, darf nicht entfernt oder verändert werden, und das Wasserzeichen muss in jeder Einbindung vollständig sichtbar bleiben. Keine gefundene kostenpflichtige Option, das Wasserzeichen zu entfernen (im Gegensatz zu manchen ähnlich lizenzierten Toolkits anderer Anbieter).
- **Gleiches Muster wie ADR 0018** (SpiffWorkflow, LGPLv3): eine im Konzept explizit vorgeschlagene, im BPMN-Ökosystem quasi-Standard-Bibliothek wird mit ihren Lizenzbedingungen unverändert akzeptiert, statt eine Eigenentwicklung oder eine unerprobte Alternative zu suchen.
- **Eine Eigenentwicklung einer BPMN-2.0-Rendering-/Modellierungskomponente** (Canvas-Engine, Palette, Properties Panel, XML-Im-/Export, Undo/Redo) wäre ein Vielfaches des Aufwands einer einzelnen Session (P6-S8) und stünde in keinem Verhältnis zum alleinigen Ziel, ein Wasserzeichen zu vermeiden — insbesondere, da `bpmn-js-spiffworkflow` bereits die für dieses Projekt nötige SpiffWorkflow-Integration mitbringt und ein Ersatz auch diese Integration neu bauen müsste (dieselbe Erwägung, die ADR 0018 bereits für die Engine-Seite festgehalten hat).
- Diese Einschätzung ist auch hier **keine Rechtsberatung**, sondern eine pragmatische Bewertung für den aktuellen internen Entwicklungs-/Testbetrieb (analog ADR 0018).

## Konsequenzen

- Jede Ansicht des Process Designer (P6-S8) zeigt sichtbar den bpmn.io-Schriftzug/Link — kein technischer Blocker für interne Verwaltungssoftware, aber vor einem etwaigen späteren Fremdvertrieb/White-Label-Bedarf gegenüber Stakeholdern zu kommunizieren.
- Sollte künftig eine White-Label-Anforderung entstehen, wäre diese Entscheidung zu revisitieren (Alternative Bibliothek oder Eigenentwicklung) — aktuell kein Bedarf, daher nicht vorgezogen.
- `bpmn-js-spiffworkflow`s eigene MIT-Lizenz wirft keine zusätzliche Einschränkung auf; sie nutzt `bpmn-js` lediglich als Peer-Dependency, ohne dessen Lizenzbedingungen zu verändern.
