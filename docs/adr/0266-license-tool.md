# 0266 — A licensor tool for issuing and checking licences

**Status:** accepted
**Context:** user request 2026-11-26, after P96-S3: a tool to issue, inspect and validate licences that
doesn't put the secrets needed for valid licences into git.

What exists:
- license-service verifies RS256 JWTs against an embedded public key (ADR 0032), with an optional
  previous key for rotation (ADR 0084);
- the only signing code was a test fixture (`license_factory.py`) with a development key that git
  ignores (`*.pem`, never committed; checked with `git log --all`);
- the Helm chart had no hint at `DMS_LICENSE_PUBLIC_KEY_PEM`, so an installation that forgets it trusts
  the development key.

## Decision 1 — The code in the repository, the key never

`tools/license-tool` (uv workspace member, `og-license`) is in git. It contains no key material; the
format it writes is visible in license-service anyway. What makes licences valid is the private key, so
the guards are on the key:
- **Outside the repository:** the default key home is `~/.config/og-doc-license` (or
  `$OG_LICENSE_HOME`).
- **`keygen`:** refuses a git work tree and doesn't overwrite a key without `--force`.
- **`issue`:** refuses a key that is in a git work tree but not ignored, or that others can read.
- **`.gitignore`:** also covers `*.license` and `issued.jsonl`.

Keeping the tool outside the repository was the alternative. It would drift from the claims
license-service reads. The tool's checks and the service's are kept in step by its tests and a
cross-check against `license_verifier.decode`.

## Decision 2 — The commands

- `keygen`, `public-key`;
- `issue`: shows the licence and asks; choices that change a licence's reach must be explicit:
  - components: `--component …`, `--all-components` (claim `null`) or `--no-components`;
  - validity: `--until` or `--days`;
- `inspect`: reads without verifying, and says so;
- `verify`: the service's decision (signature with current/previous key, installation binding,
  `exp`, `nbf`), exit code 0/1;
- `issued`: the licensor's record. `issue` appends id, licensee, claims and the token's SHA-256 to
  `issued.jsonl` next to the key. `--dev` licences aren't recorded.

`--dev` uses the development key on purpose, for the dev stack.

## Decision 3 — The development key stays the service default, Helm names the setting

Changing license-service's built-in public key would break the dev stack and the tests. Instead:
- `values.yaml` carries a commented `DMS_LICENSE_PUBLIC_KEY_PEM` block at license-service;
- the tool's docs and `keygen` output say that an installation without it accepts only the
  development key.

A multi-line PEM set through `services.license-service.env` (or `--set-file`) arrives intact; checked
with `helm template`.

## Consequences

- Issuing a production licence needs the licensor's machine with the key home. Back up `private.pem`
  offline: losing it means a key rotation for every installation.
- Should licences ever need more than these claims (for example feature flags), the tool, license-service
  and this ADR change together.
- ~~**Open:** the development private key exists only on developer machines; a fresh clone couldn't
  run license-service's tests that sign tokens.~~ **Closed in P98-S2:** the suite generates a throwaway
  key per session (`tests/conftest.py`).
