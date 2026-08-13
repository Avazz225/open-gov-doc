// Zur Build-Zeit fest eingebrannt (statischer Export, Konzept 8 - kein Server,
// der zur Laufzeit Konfiguration nachladen könnte). Für einen anderen
// Gateway-Endpunkt muss das Image mit einem anderen Wert neu gebaut werden.
export const GATEWAY_BASE_URL =
  process.env.NEXT_PUBLIC_GATEWAY_BASE_URL ?? "http://localhost:8009";

// Office-Direktbearbeitung (Post-Roadmap-Feature): webdav-connector ist ein
// eigenständiger, dem Browser direkt bekannter Dienst (anders als jeder
// andere Aufruf in api.ts, die alle über den Gateway laufen) - die
// Office-URI-Schema-Start-URL muss ihn direkt ansprechen, kein Proxy dazwischen.
export const WEBDAV_CONNECTOR_BASE_URL =
  process.env.NEXT_PUBLIC_WEBDAV_CONNECTOR_BASE_URL ?? "http://localhost:8027";
