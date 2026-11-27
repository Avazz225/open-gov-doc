# 0271 — Software components: a file in every image, a runtime report, an inventory

**Status:** accepted (P100-S1; CVE matching follows in P100-S2/S3)
**Context:** user request 2026-11-27. Admins without an external scanner should see which libraries run
where, as the basis for checking a CVE against the installation, online and air-gapped.
- `scripts/sbom.sh` (P94-S3) wrote CycloneDX files from the lock files at build time, but nothing
  reached the running system or the admin UI.
- The base images' OS packages (OpenSSL & co.) weren't covered.

User suggestions in the session:
- carry the libraries in a file inside the image;
- ship no vulnerability data with OG Doc; fetch it live from a third party online (decided for
  P100-S2).

## Decision 1 — A components file in every image

- **Python services:** `python -m dms_common.components` runs in each image after the install and writes
  `/app/META-INF/components.cdx.json`: CycloneDX 1.6 with every Python distribution
  (`importlib.metadata`, the image's truth rather than the lock's) and every installed Debian package
  (`/var/lib/dpkg/status`). Example: permission-service has 141 components (54 PyPI, 87 Debian, incl.
  OpenSSL 3.5.5). The label `dms.components` points to it.
- **Frontends:** `scripts/frontend-components.mjs` runs in the Node build stage. It reads the npm
  production dependencies from the package-lock and the Alpine packages of the nginx runtime image
  (copied in from that image) and writes two files:
  - the full list, kept in the image at the same path and not served;
  - the npm part only, served at `/META-INF/components.cdx.json`. The frontends are static sites
    browsers fetch directly, so a served file is public. Their npm code reaches every browser anyway;
    the OS package list doesn't need to.
- Standard library / Node built-ins only: generating the file downloads nothing.
- The file can be read without running the service (`docker run --entrypoint cat`, a registry scanner)
  and is frozen with the image, so it can be signed and archived with it.

## Decision 2 — A runtime report as cross-check

`setup_observability` registers `GET /component-report` in every service. It returns the document this
process sees, its fingerprint (the component set, independent of time and serial number) and the
comparison with the image file (`matches_image`, `only_at_runtime`, `only_in_image`).

It answers only for `X-DMS-Principal: monitoring-service`: a version list helps an attacker, and the
gateway overwrites that header with the real user. It is not called `/components`, because that path is
monitoring-service's inventory API.

## Decision 3 — The inventory in monitoring-service

- **Collection:** every healthy instance (each replica on its own) plus the frontends' served files
  (`DMS_FRONTEND_COMPONENT_URLS`), cached for 60 s.
- **API (`admin.monitoring`):**
  - `GET /components`: per service, instances, counts per ecosystem, `replicas_consistent`,
    `matches_image`;
  - `GET /components/{service}`;
  - `GET /components/search?q=`: who runs a package, in which version;
  - `GET /components/export[?service=]`: CycloneDX for the installation, every component once with
    `dms:services`, for Dependency-Track, Trivy, Greenbone.
- **CLI:** `dms components list|show|search|export`.
- **Admin UI:** page „Komponenten" under „Sicherheit".

## Decision 4 — Dependencies before libraries in the Dockerfiles

`uv sync --frozen --package <svc> --no-install-workspace` installs the third-party dependencies from the
lock before `libs/` is copied. A library change then reuses that layer. Measured: a rebuild after a
change in `dms-common` took 10 s per image, where P99-S2's full rebuild took about 15 minutes.

## Consequences

- Images built before P100 answer `/component-report` with 404. The inventory shows them as „liefert
  keine Komponenten (Image vor P100)".
- **Not covered:** the base images' non-dpkg/apk content (e.g. the Python interpreter's own version
  appears via the Debian packages only partially). The `python:3.12-slim` interpreter is a source build
  outside dpkg; the CVE step should name it explicitly (P100-S2).
- **Tests:**
  - dms-common: 5 (dpkg parsing, document, fingerprint/compare, file writer, endpoint gate);
  - monitoring: 4 (summary with differing replicas and an old image, search and detail, export,
    gate);
  - CLI: 1;
  - admin-ui: 3.
