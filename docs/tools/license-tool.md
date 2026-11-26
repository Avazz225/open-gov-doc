# tools/license-tool — issuing, inspecting and verifying licences

**Responsibility:** the licensor's tool for OG Doc licences ([ADR 0266](../adr/0266-license-tool.md)).
It issues licences signed with the licensor's private key, shows what a licence file contains, and
checks whether an installation would accept it. It talks to no service; everything is local.

**Not a service and not shipped:** no container, not part of a customer delivery. The code holds no
key; the private key lives outside every git work tree.

## Setup

```bash
# in the repository (uv workspace member)
uv run --package og-license-tool og-license --help
```

## Keys

| | |
|---|---|
| Key home | `$OG_LICENSE_HOME`, else `~/.config/og-doc-license/` (mode 0700) |
| `private.pem` | RSA 3072, PKCS#8, mode 0600. **Never share; back it up offline.** |
| `public.pem` | goes to every installation as `DMS_LICENSE_PUBLIC_KEY_PEM` |
| `issued.jsonl` | the record of every licence issued (id, licensee, claims, SHA-256 of the token) |

Guards:
- `keygen` refuses to write into a git work tree, and refuses to overwrite an existing key without
  `--force`;
- `issue` refuses a private key that sits in a git work tree without being ignored there, or that is
  readable by others;
- `.gitignore` ignores `*.pem`, `*.license` and `issued.jsonl`.

**Development key:** `--dev` signs with `services/license-service/tests/fixtures/dev_private_key.pem`.
That file exists only on developer machines (never in git). Its public half is license-service's
built-in default, so `--dev` licences work on the dev stack and on any installation that hasn't set
`DMS_LICENSE_PUBLIC_KEY_PEM`. A real installation must set it, see below.

## Commands

| Command | What it does |
|---|---|
| `og-license keygen [--out DIR] [--bits N] [--force]` | create the licensor key pair (once; again only for a rotation) |
| `og-license public-key [--key F \| --dev]` | print the public key for `DMS_LICENSE_PUBLIC_KEY_PEM` |
| `og-license issue --licensee … (--until YYYY-MM-DD \| --days N) --user-model named\|concurrent (--component C … \| --all-components \| --no-components) [--max-users N] [--storage-gb N] [--documents N] [--not-before YYYY-MM-DD] [--installation-id ID] [-o file] [--key F \| --dev] [--yes]` | show the licence, ask, sign, write it, and record it |
| `og-license inspect <file\|token\|->` `[--json]` | show the contents **without** checking the signature |
| `og-license verify <file\|token\|->` `[--public-key F] [--previous-public-key F] [--installation-id ID] [--dev]` | decide like license-service; exit 0 valid, 1 not |
| `og-license issued [--dir D]` | list the issued licences |

`issue` makes some choices explicit:
- the components must be chosen (`--all-components` means the claim `null`, which licenses every
  licensable component, including future ones);
- exactly one of `--until` (end of that day, UTC) or `--days`;
- limits left out are unlimited (`null`).

## The licence format (what license-service reads)

A JWT, RS256 (ADR 0032). Claims:
- `iat`, `exp`, optional `nbf`;
- `user_model` (`named` counts users, `concurrent` sessions), `max_users`;
- `storage_limit_gb`, `document_limit`;
- `licensed_components` (`null` = all; registry-service's default licensable components are
  `workflow-service`, `webdav-connector`, `migration-service`, `cmis-connector`);
- optional `installation_id`, which must equal the installation's `DMS_INSTALLATION_ID`.

The tool adds `jti` (licence id), `sub` (licensee) and `iss`. The service ignores them.

`verify` applies the service's checks in its order:
1. the signature against the current key, then the previous one (rotation, ADR 0084);
2. the installation binding;
3. `exp`, then `nbf`.

It doesn't check usage limits; those depend on the installation's live counts (admin UI „Lizenzstatus").

## Giving an installation the public key

- **Helm:** `services.license-service.env.DMS_LICENSE_PUBLIC_KEY_PEM` as a block scalar, or
  `--set-file services.license-service.env.DMS_LICENSE_PUBLIC_KEY_PEM=public.pem`. Checked with
  `helm template`: the newlines arrive intact.
- **Compose:** an override file with the PEM as a block scalar (see `infra/docker-compose.yml` at
  license-service).

**Without this setting, license-service accepts only the development key.** That is right for the dev
stack and wrong for every real installation.

## Air-gapped installations

Verification is fully offline: license-service checks the signature against the configured public key
and counts users, storage and documents locally. Nothing calls home, and the fleet licence push is
optional. A licence for an air-gapped site is issued like any other, ideally bound with
`--installation-id`, and travels as a file. Upload it in the admin UI („Lizenzstatus") or with
`dms license upload`.

**An operator's own key** works the same way. Whoever controls the deployment decides which public key
license-service trusts (`DMS_LICENSE_PUBLIC_KEY_PEM`). An operator can therefore run `og-license keygen`
themselves, configure their public key and sign their own licences. That is a deliberate consequence of
ADR 0032, not a separate mode: the licensing protects against tampering with a licence file, not against
the operator of the installation.

## Rotating the key

1. Back up the old `public.pem`.
2. `og-license keygen --force`.
3. Installations set the new key as `DMS_LICENSE_PUBLIC_KEY_PEM` and the old one as
   `DMS_LICENSE_PREVIOUS_PUBLIC_KEY_PEM`.
4. Reissue the licences.
5. Remove the previous key once every installation runs a new licence (ADR 0084).

`og-license verify --previous-public-key old.pem` shows whether an old licence still passes during the
window.

## Tests

`tools/license-tool/tests/` (10):
- key modes and the git guards;
- issue → verify, and the issuance log;
- the claim shapes;
- wrong installation, expiry and a tampered signature;
- rotation;
- the explicit component choice;
- `inspect`.

A licence issued with `--dev` was also checked against license-service's own `license_verifier.decode`.
