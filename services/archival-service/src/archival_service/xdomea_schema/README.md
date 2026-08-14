# XDOMEA 4.0.0 XSD Schemas (vendored)

Unmodified copies of the official XDOMEA 4.0.0 schema files (5.6, P7-S3b,
[ADR 0029](../../../../../docs/adr/0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md)) -
exactly the dependency chain that `xdomea.py` needs for the message
`Aussonderung.Aussonderung.0503` (verified compilable via
`lxml.etree.XMLSchema`). No GPL/copyleft concern: all files come
directly from the official KoSIT schema infrastructure, not from a
third-party mirror.

| File | Source |
|---|---|
| `xdomea-Baukasten.xsd` | `https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/xdomea-Baukasten.xsd` |
| `xdomea-Datentypen.xsd` | `https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/xdomea-Datentypen.xsd` |
| `xdomea-Nachrichten-AussonderungDurchfuehren.xsd` | `https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/xdomea-Nachrichten-AussonderungDurchfuehren.xsd` |
| `xdomea-Typen-AussonderungDurchfuehren.xsd` | `https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/xdomea-Typen-AussonderungDurchfuehren.xsd` |
| `xoev-code.xsd` | `http://xoev.de/schemata/code/1_0/xoev-code.xsd` |
| `xoev-basisnachricht-unqualified-g2g_1.1.xsd` | `http://xoev.de/schemata/basisnachricht/unqualified/g2g/1_1/xoev-basisnachricht-unqualified-g2g_1.1.xsd` |
| `din-norm-91379-datatypes.xsd` | `https://xoev.de/schemata/din/91379/2022-08/din-norm-91379-datatypes.xsd` |

`xdomea.py` resolves the `xs:import` `schemaLocation` URLs contained in the files
to these local files via an `lxml.etree.Resolver` - no
network access at runtime or in tests.

Only the subset needed for the 0503 transfer-to-archive message is vendored,
not the entire XDOMEA schema scope (other message families such as
handover (Abgabe)/business process routing (Geschäftsgang)/specialist-procedure
integration (Fachverfahren) are not part of this session, see
`docs/services/archival-service.md`).
