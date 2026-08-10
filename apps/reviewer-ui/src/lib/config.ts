// Zur Build-Zeit fest eingebrannt (statischer Export, Konzept 8 - kein Server,
// der zur Laufzeit Konfiguration nachladen könnte). Für einen anderen
// Gateway-Endpunkt muss das Image mit einem anderen Wert neu gebaut werden.
export const GATEWAY_BASE_URL =
  process.env.NEXT_PUBLIC_GATEWAY_BASE_URL ?? "http://localhost:8009";
