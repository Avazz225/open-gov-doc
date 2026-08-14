// Baked in at build time (static export, concept 8 - no server that could
// reload configuration at runtime). For a different gateway endpoint, the
// image must be rebuilt with a different value.
export const GATEWAY_BASE_URL =
  process.env.NEXT_PUBLIC_GATEWAY_BASE_URL ?? "http://localhost:8009";
