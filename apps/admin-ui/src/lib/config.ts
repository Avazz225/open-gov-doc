// Baked in at build time (static export, Concept 8 - no server
// that could reload configuration at runtime). For a different
// gateway endpoint, the image must be rebuilt with a different value.
export const GATEWAY_BASE_URL =
  process.env.NEXT_PUBLIC_GATEWAY_BASE_URL ?? "http://localhost:8009";

// Federation Hub (Phase 40 Session 3) - the ONLY service this admin-ui
// reaches directly instead of through the gateway above. federation-hub-
// service deliberately does NOT self-register with registry-service (it
// isn't an internal service of any one installation, see its own
// settings docstring), so the gateway's registry-based proxy can't reach
// it at all - this installation's admin talks to the shared hub the same
// way workflow-service/auth-service already do, via a direct base URL.
export const FEDERATION_HUB_BASE_URL =
  process.env.NEXT_PUBLIC_FEDERATION_HUB_BASE_URL ?? "http://localhost:8018";
