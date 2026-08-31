# XDOMEA 4.0.0 XSD Schemas (vendored)

Unmodified copies of the official XDOMEA 4.0.0 schema files (5.6/14.2,
[ADR 0029](../../../../../docs/adr/0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md)/
[ADR 0126](../../../../../docs/adr/0126-xdomea-general-exchange-split-download-upload-not-federation-hub.md)) -
exactly the dependency chain that `xdomea.py` needs for the two messages it
builds/validates, `Aussonderung.Aussonderung.0503` (P7-S3b) and
`Abgabe.Abgabe.0401` (Post-Roadmap Phase 31 Session 13a) - both verified
compilable via `lxml.etree.XMLSchema`. No GPL/copyleft concern: all files
come directly from the official KoSIT schema infrastructure, not from a
third-party mirror.

| File | Source |
|---|---|
| `xdomea-Baukasten.xsd` | `https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/xdomea-Baukasten.xsd` |
| `xdomea-Datentypen.xsd` | `https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/xdomea-Datentypen.xsd` |
| `xdomea-Nachrichten-AussonderungDurchfuehren.xsd` | `https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/xdomea-Nachrichten-AussonderungDurchfuehren.xsd` |
| `xdomea-Typen-AussonderungDurchfuehren.xsd` | `https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/xdomea-Typen-AussonderungDurchfuehren.xsd` |
| `xdomea-Nachrichten-AbgabeDurchfuehren.xsd` | `https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/xdomea-Nachrichten-AbgabeDurchfuehren.xsd` |
| `xdomea-Typen-AbgabeDurchfuehren.xsd` | `https://schema.kdo.de/schema/urn/xoev-de/xdomea/schema/4.0.0/xdomea-Typen-AbgabeDurchfuehren.xsd` |
| `xoev-code.xsd` | `http://xoev.de/schemata/code/1_0/xoev-code.xsd` |
| `xoev-basisnachricht-unqualified-g2g_1.1.xsd` | `http://xoev.de/schemata/basisnachricht/unqualified/g2g/1_1/xoev-basisnachricht-unqualified-g2g_1.1.xsd` |
| `din-norm-91379-datatypes.xsd` | `https://xoev.de/schemata/din/91379/2022-08/din-norm-91379-datatypes.xsd` |

`xdomea.py` resolves the `xs:import` `schemaLocation` URLs contained in the files
to these local files via an `lxml.etree.Resolver` - no
network access at runtime or in tests.

**A real, schema-verified surprise found while adding the Abgabe files**: the
0503 message's `Schriftgutobjekt/Vorgang` is typed `VorgangAussonderungType`
(`xdomea-Typen-AussonderungDurchfuehren.xsd`, requires a `Kontextobjekt`
element), NOT the generic `xdomea:VorgangType` (`xdomea-Baukasten.xsd`, no
such field) that the 0401 message's `Schriftgutobjekt/Vorgang` actually uses
- these are two different, message-family-specific types despite both being
named "Vorgang," confirmed only by compiling both messages against the real
vendored schema and reading the validator's own rejection. `xdomea.py`
therefore has two separate Vorgang-builders (`_build_vorgang_aussonderung`/
`_build_vorgang_generic`), not one shared one - see their docstrings.

Only the subset needed for the 0503 transfer-to-archive and 0401 general
handoff messages is vendored, not the entire XDOMEA schema scope (other
message families such as business process routing (Geschäftsgang)/
specialist-procedure integration (Fachverfahren)/intermediate archiving
(Zwischenarchivierung) are not part of either session, see
`docs/services/archival-service.md`).
