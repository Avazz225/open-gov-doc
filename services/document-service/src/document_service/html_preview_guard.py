"""Serverseitiges Neutralisieren externer Subressourcen-Referenzen in
HTML-Vorschauinhalten (Post-Roadmap Phase 21 Session 3, ADR 0086) - siehe
`main.py`s `download_current_content`/`download_version_content`.

Grund: `user-ui`s `PreviewPane` rendert HTML-Dokumente über ein
`sandbox=""`-iframe mit `srcDoc` (kein `src` auf eine eigene Origin) -
`sandbox=""` blockiert Skriptausführung/Top-Navigation/Formulare, aber NICHT
das normale Nachladen von Subressourcen (Bilder, Stylesheets, ...), und ein
CSP-Header wirkt bei `srcDoc`-Inhalten ohne eigene Origin nur eingeschränkt
(kein fester Origin, an den sich ein Header binden ließe). Ein hochgeladenes
HTML-Dokument mit z. B. ``<img src="https://tracker.example/pixel.gif?...">``
würde diese Anfrage sonst beim bloßen Öffnen der Vorschau auslösen
(Tracking-/Datenleck-Risiko), unabhängig vom Sandbox-Attribut."""

from urllib.parse import urlsplit

from bs4 import BeautifulSoup

# mailto:/tel: lösen selbst bei einem Klick keinen Netzwerk-Request innerhalb
# der Seite aus (öffnen höchstens einen externen Handler), data: ist bereits
# vollständig im Dokument eingebettet - keines der drei ist ein externer
# Subressourcen-Request.
_ALLOWED_SCHEMES = {"data", "mailto", "tel"}


def _is_blocked(value: str) -> bool:
    value = value.strip()
    if not value:
        # Ein leerer `src`/`href`-Wert lässt manche Browser die aktuelle
        # Seite erneut anfordern (bekannte Eigenart) - sicherheitshalber
        # ebenfalls blockiert statt als harmlos behandelt.
        return True
    if value.startswith("#"):
        return False
    parts = urlsplit(value)
    if parts.scheme:
        return parts.scheme.lower() not in _ALLOWED_SCHEMES
    # Kein Schema: entweder schema-relativ ("//host/...", `urlsplit` liefert
    # dafür ein leeres `scheme` mit gesetztem `netloc`) oder ein relativer
    # Pfad - beides blockiert, da ein `srcDoc`-Inhalt keine sichere, im
    # Vorschaukontext auflösbare Basis-URL hat.
    return True


def rewrite_external_references(html_bytes: bytes) -> bytes:
    """Ersetzt jede externe `src`/`href`-Referenz irgendeines Tags durch
    nichts (Attribut entfernt, verhindert die Anfrage) und fügt direkt danach
    eine sichtbare Markierung ein - `data:`/`mailto:`/`tel:`-URIs und reine
    Fragment-Anker (``#...``) bleiben unverändert (kein Netzwerkzugriff).
    Attribut-getrieben statt Tag-Namen-getrieben (kein hartkodiertes
    ``img``/``script``/``iframe``/...-Set) - erfasst damit automatisch auch
    unübliche Tags mit `src`/`href`. Bewusst NICHT abgedeckt (siehe ADR 0086
    "Konsequenzen"): `srcset`, `poster`, `background`, sowie `url(...)`
    innerhalb von `style`-Attributen/`<style>`-Blöcken - der Plan nennt
    ausdrücklich nur `src`/`href`."""
    soup = BeautifulSoup(html_bytes, "html.parser")

    meta_charset = soup.find("meta", charset=True)
    if meta_charset is not None:
        meta_charset["charset"] = "utf-8"

    for tag in soup.find_all(True):
        for attribute in ("src", "href"):
            value = tag.get(attribute)
            if value is None or not _is_blocked(value):
                continue
            del tag[attribute]
            marker = soup.new_tag("span")
            marker["class"] = "dms-blocked-external-resource"
            marker["style"] = (
                "color:#b91c1c;background:#fee2e2;font-size:0.75rem;"
                "font-family:monospace;padding:0 0.25rem;border-radius:0.2rem;"
            )
            marker.string = f"[Blockierte externe Anfrage: {value}]"
            tag.insert_after(marker)

    return soup.encode("utf-8")
