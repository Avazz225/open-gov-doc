# XDOMEA 4.0.0 XSD-Schemata (vendort)

Unveränderte Kopien der offiziellen XDOMEA-4.0.0-Schemadateien (5.6, P7-S3b,
[ADR 0029](../../../../../docs/adr/0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md)) -
genau die Abhängigkeitskette, die `xdomea.py` für die Nachricht
`Aussonderung.Aussonderung.0503` benötigt (per `lxml.etree.XMLSchema`
verifiziert kompilierbar). Keine GPL-/Copyleft-Frage: alle Dateien stammen
direkt von der offiziellen KoSIT-Schemainfrastruktur, nicht von einem
Drittanbieter-Mirror.

| Datei | Quelle |
|---|---|
| `xdomea-Baukasten.xsd` | `https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/xdomea-Baukasten.xsd` |
| `xdomea-Datentypen.xsd` | `https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/xdomea-Datentypen.xsd` |
| `xdomea-Nachrichten-AussonderungDurchfuehren.xsd` | `https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/xdomea-Nachrichten-AussonderungDurchfuehren.xsd` |
| `xdomea-Typen-AussonderungDurchfuehren.xsd` | `https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/xdomea-Typen-AussonderungDurchfuehren.xsd` |
| `xoev-code.xsd` | `http://xoev.de/schemata/code/1_0/xoev-code.xsd` |
| `xoev-basisnachricht-unqualified-g2g_1.1.xsd` | `http://xoev.de/schemata/basisnachricht/unqualified/g2g/1_1/xoev-basisnachricht-unqualified-g2g_1.1.xsd` |
| `din-norm-91379-datatypes.xsd` | `https://xoev.de/schemata/din/91379/2022-08/din-norm-91379-datatypes.xsd` |

`xdomea.py` löst die in den Dateien enthaltenen `xs:import`-`schemaLocation`-URLs
über einen `lxml.etree.Resolver` auf diese lokalen Dateien auf - kein
Netzwerkzugriff zur Laufzeit oder in Tests.

Nur die für die 0503-Aussonderungsnachricht benötigte Teilmenge ist vendort,
nicht der komplette XDOMEA-Schema-Umfang (andere Nachrichtenfamilien wie
Abgabe/Geschäftsgang/Fachverfahren sind nicht Teil dieser Session, siehe
`docs/services/archival-service.md`).
