# XJustiz 3.6.2 XSD Schemas (vendored)

Unmodified copies of the official XJustiz 3.6.2 schema files (14.2, Post-Roadmap Phase 31 Session 13c,
[ADR 0129](../../../../../docs/adr/0129-xjustiz-uebermittlungschriftgutobjekte-general-message.md)) - the
dependency chain `xjustiz.py` needs for the message `nachricht.gds.uebermittlungSchriftgutobjekte.0005005`
(verified compilable via `lxml.etree.XMLSchema`). No GPL/copyleft concern: all files come directly from the
official `xjustiz.justiz.de` schema infrastructure (published by the BLK-AG "IT-Standards in der Justiz"),
not from a third-party mirror.

| File | Source |
|---|---|
| `xjustiz_0000_grunddatensatz_3_6.xsd` | `https://xjustiz.justiz.de/system/zip/XJustiz_3_6_2_Nachlieferung_ZVSTR_08_2026_XSD.zip` |
| `xjustiz_0005_nachrichten_3_2.xsd` | same ZIP |
| `xjustiz_0010_cl_allgemein_3_7.xsd` | same ZIP |
| `xjustiz_0020_cl_gerichte_3_3.xsd` | same ZIP |
| `xjustiz_0030_cl_rechtsform_3_3.xsd` | same ZIP |
| `xjustiz_0040_cl_rollenbezeichnung_3_5.xsd` | same ZIP |
| `xjustiz_0050_cl_staaten_3_2.xsd` | same ZIP |
| `xjustiz_0060_cl_telekommunikation_3_1.xsd` | same ZIP |
| `xjustiz_0070_cl_justizvollzugsanstalt_3_1.xsd` | same ZIP |
| `xjustiz_0080_cl_register_3_3.xsd` | same ZIP |
| `xjustiz_0095_cl_personalstatut_3_0.xsd` | same ZIP |
| `xoev-code.xsd` | reused verbatim from `../xdomea_schema/` - the identical shared XÖV-framework file (`http://xoev.de/schemata/code/1_0/xoev-code.xsd`), both XDOMEA and XJustiz build on the same base XÖV modules |
| `din-norm-91379-datatypes.xsd` | reused verbatim from `../xdomea_schema/` - same reasoning |

The full ZIP (`XJustiz_3_6_2_Nachlieferung_ZVSTR_08_2026_XSD.zip`, downloaded directly via `curl` from
`https://xjustiz.justiz.de`) contains the schema files for EVERY XJustiz specialized module (35+ files,
family law, criminal law, insolvency, enforcement, etc.) - only the subset needed for the general,
cross-cutting `uebermittlungSchriftgutobjekte` message is vendored here, not the entire XJustiz schema
scope. This is the exact dependency closure `xjustiz_0005_nachrichten_3_2.xsd` resolves to (traced via
each file's own `xs:include`/`xs:import` statements, then verified by actually compiling the chain with
`lxml.etree.XMLSchema` before writing any project code) - no domain-specific module (family/criminal/
insolvency/etc.) is included or needed.

`xjustiz.py` resolves the `xs:import` `schemaLocation` for `din-norm-91379-datatypes.xsd` to this local
file via an `lxml.etree.Resolver` - no network access at runtime or in tests (same pattern as
`xdomea.py`'s `_LocalSchemaResolver`).

Two codelists used by the message are **Typ3** (externally versioned, not embedded in the XSD as an
enumeration) - `gds.dokumentklasse` (version 1.4 used) and `gds.aktentyp` is, in fact, **Typ2** (embedded
enumeration) despite superficially looking similar - confirmed by actually reading each type's own
`xs:restriction`, not assumed from the other's shape. The generic "Andere / Sonstige" code differs between
the two lists (`001` for `gds.dokumentklasse`, `017` for `gds.aktentyp`) - a real, concrete reminder that
codes are never assumed to line up across different codelists, only ever read from the real, current
values (`gds.dokumentklasse`'s fetched live via `https://www.xrepository.de/api/xrepository/urn:xoev-de:
xjustiz:codeliste:gds.dokumentklasse_1.4/genericode`, `gds.aktentyp`'s read directly from the vendored
XSD's own embedded enumeration).
