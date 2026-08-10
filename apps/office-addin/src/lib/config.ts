// Zur Build-Zeit fest eingebrannt (statischer Export, Konzept 8 - kein Server,
// der zur Laufzeit Konfiguration nachladen könnte). Für einen anderen
// Gateway-Endpunkt muss das Image mit einem anderen Wert neu gebaut werden.
export const GATEWAY_BASE_URL =
  process.env.NEXT_PUBLIC_GATEWAY_BASE_URL ?? "http://localhost:8009";

// Name des Wurzelordners, der als zentrale Vorlagenbibliothek (3.3a) dient -
// bewusst über eine Namenskonvention statt einer festen Ordner-ID gelöst:
// ein Admin legt einen Ordner mit diesem Namen unter der Dokument-Root an und
// vergibt Leserechte wie für jeden anderen Ordner (Rollenbasiertheit ist
// damit die bereits bestehende `permission-service`-Ordnerprüfung, keine
// neue Berechtigungslogik). Siehe docs/services/office-addin.md.
export const TEMPLATE_LIBRARY_FOLDER_NAME =
  process.env.NEXT_PUBLIC_TEMPLATE_LIBRARY_FOLDER_NAME ?? "Vorlagen";
