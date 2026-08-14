// Baked in at build time (static export, concept 8 - no server
// that could reload configuration at runtime). To use a different
// gateway endpoint, the image must be rebuilt with a different value.
export const GATEWAY_BASE_URL =
  process.env.NEXT_PUBLIC_GATEWAY_BASE_URL ?? "http://localhost:8009";

// Name of the root folder that serves as the central template library (3.3a) -
// deliberately solved via a naming convention instead of a fixed folder ID:
// an admin creates a folder with this name under the document root and
// grants read permissions like for any other folder (role-basedness is
// thus the already-existing `permission-service` folder check, no
// new permission logic). See docs/services/office-addin.md.
export const TEMPLATE_LIBRARY_FOLDER_NAME =
  process.env.NEXT_PUBLIC_TEMPLATE_LIBRARY_FOLDER_NAME ?? "Vorlagen";
