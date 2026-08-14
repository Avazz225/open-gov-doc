// Baked in at build time (static export, concept 8 - no server that could
// reload configuration at runtime). To point at a different gateway
// endpoint, the image must be rebuilt with a different value.
export const GATEWAY_BASE_URL =
  process.env.NEXT_PUBLIC_GATEWAY_BASE_URL ?? "http://localhost:8009";

// Direct Office editing (post-roadmap feature): webdav-connector is a
// standalone service known directly to the browser (unlike every other call
// in api.ts, all of which go through the gateway) - the Office URI scheme
// launch URL must address it directly, no proxy in between.
export const WEBDAV_CONNECTOR_BASE_URL =
  process.env.NEXT_PUBLIC_WEBDAV_CONNECTOR_BASE_URL ?? "http://localhost:8027";
