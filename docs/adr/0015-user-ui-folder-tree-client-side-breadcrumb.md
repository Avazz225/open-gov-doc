# 0015 — Baumansicht: Breadcrumb-Pfad clientseitig aus dem bereits aufgeklappten Baum rekonstruiert, kein neuer Backend-Endpunkt

**Status:** akzeptiert
**Kontext:** Konzept 8 (Explorer-Baumansicht), Session P5b-S4

## Entscheidung

Die neue Baumansicht des User-UI-Explorers (`FolderTree.tsx`) navigiert per Klick auf einen beliebigen, ggf. tief verschachtelten Ordnerknoten direkt zu diesem Ordner - inklusive korrektem Breadcrumb-Pfad in `DocumentWorkspace`s `trail`-State. Statt dafür einen neuen Folder-Service-Endpunkt für den vollständigen Pfad eines Ordners zu bauen (offener Punkt seit P3-S3, siehe `docs/services/folder-service.md`), rekonstruiert `FolderTree` den Pfad **clientseitig aus bereits geladenen Daten**: da ein Knoten im Baum nur sichtbar/anklickbar ist, nachdem alle seine Vorfahren aufgeklappt wurden, kennt die Komponente zum Zeitpunkt des Klicks bereits die vollständige Kette der übergeordneten `Folder`-Objekte (jedes davon kam als Eintrag aus dem `listChildFolders()`-Aufruf seines jeweiligen Elternknotens). Ein neuer Callback `onNavigateToFolder(path: Folder[])` ersetzt in `DocumentWorkspace` den gesamten `trail` (statt ihn wie das bestehende `onOpenFolder` nur um eine Ebene zu verlängern).

## Begründung

- **Keine zusätzliche Backend-Änderung nötig.** Der Folder Service hat bewusst keinen "vollständiger Pfad"-Endpunkt (siehe `docs/services/folder-service.md` "Offene Punkte") - nur `GET /folders/{id}/children` existiert. Eine Baumansicht, die ohnehin von der Wurzel aus rekursiv aufklappt, hat die Pfadinformation als Nebenprodukt ihrer eigenen Navigation bereits vorliegen; sie erneut vom Backend abzufragen wäre eine unnötige Verdopplung.
- **Kein zusätzlicher Netzwerk-Roundtrip beim Klick.** Da der Pfad aus bereits im Speicher gehaltenen `Folder`-Objekten zusammengesetzt wird, ist die Navigation sofort verfügbar, ohne auf eine weitere Anfrage zu warten.
- **Funktioniert nur, weil die Baumansicht von der Wurzel aus aufklappt** - ein hypothetischer "Sprung" zu einem noch nie im Baum sichtbar gemachten Ordner (z. B. über eine Freitext-Ordner-ID) wäre mit diesem Ansatz nicht möglich. Das ist aktuell keine Einschränkung, da es keinen solchen Eingabeweg gibt (keine Ordner-ID-Adressleiste, kein Deep-Link) - würde ein solcher Bedarf später entstehen, bräuchte er doch den in `folder-service.md` offen gelassenen Pfad-Endpunkt.
- **`onNavigateToFolder` ersetzt den Trail, statt ihn zu verlängern** - anders als `onOpenFolder` (Listenansicht, geht immer genau eine Ebene tiefer als der aktuell gezeigte Ordner) kann ein Baum-Klick auf einen beliebigen Knoten (Geschwister, Vorfahre, tief verschachteltes Kind) erfolgen; ein einfaches Anhängen an den bestehenden Trail wäre in den meisten Fällen falsch.

## Konsequenzen

- `ExplorerPane`/`DocumentWorkspace` bekommen eine neue Prop/Funktion `onNavigateToFolder`, unabhängig vom bestehenden `onOpenFolder` (Listenansicht) - beide führen letztlich zum selben `trail`-State, unterscheiden sich nur darin, wie der neue Trail gebildet wird (anhängen vs. ersetzen).
- Die Baumansicht lädt Kinder eines Knotens **lazy erst beim Aufklappen** (`listChildFolders`/`listDocumentsInFolder` pro Knoten) - ein noch nie aufgeklappter Teilbaum verursacht keine Anfragen, dafür baut sich der Pfad für einen Klick auf einen bereits sichtbaren Knoten garantiert korrekt zusammen.
- Sollte künftig ein Deep-Link/eine direkte Ordner-ID-Adressierung nötig werden (nicht Teil dieser Session), wäre das der Moment, den bislang aufgeschobenen "vollständiger Pfad"-Endpunkt im Folder Service tatsächlich zu bauen - diese Entscheidung bleibt dafür kein Hindernis, da sie rein clientseitig ist.
