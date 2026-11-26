"""`og-license`: issue, inspect and verify OG Doc licences (licensor tool)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

from og_license_tool import keys, licenses

app = typer.Typer(
    help=(
        "Lizenzen für OG Doc ausstellen, inspizieren und prüfen. Der private Schlüssel liegt "
        "außerhalb jedes Git-Repositorys (Standard: ~/.config/og-doc-license, änderbar mit "
        "OG_LICENSE_HOME)."
    ),
    no_args_is_help=True,
    add_completion=False,
)


def _fail(message: str, code: int = 2) -> None:
    typer.secho(f"Fehler: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=code)


def _parse_date(value: str, option: str, *, end_of_day: bool) -> datetime:
    """`YYYY-MM-DD` (start or end of that day, UTC) or a full ISO timestamp."""
    try:
        if len(value) == 10:
            day = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
            return day + timedelta(days=1, seconds=-1) if end_of_day else day
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        _fail(f"{option}: Datum als JJJJ-MM-TT oder ISO-Zeitstempel angeben, nicht {value!r}")
        raise  # unreachable, keeps type checkers calm


def _read_token(source: str) -> str:
    """A path to a licence file, `-` for stdin, or the token itself."""
    if source == "-":
        return sys.stdin.read()
    # A token is far longer than a file name may be; probing it as a path
    # raises "File name too long".
    try:
        path = Path(source).expanduser()
        if path.is_file():
            return path.read_text(encoding="ascii")
    except OSError:
        pass
    return source


def _print_rows(rows: list[tuple[str, str]]) -> None:
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        typer.echo(f"{label.ljust(width)} : {value}")


@app.command()
def keygen(
    out: Path = typer.Option(None, "--out", help="Zielverzeichnis (Standard: Schlüssel-Home)."),
    bits: int = typer.Option(3072, "--bits", min=2048, help="RSA-Schlüssellänge."),
    force: bool = typer.Option(False, "--force", help="Vorhandenes Schlüsselpaar überschreiben."),
) -> None:
    """Ein neues Lizenzgeber-Schlüsselpaar erzeugen (einmalig, bzw. zur Rotation)."""
    try:
        private_path, public_path = keys.generate_key_pair(
            out or keys.key_home(), bits=bits, force=force
        )
    except keys.KeyError_ as exc:
        _fail(str(exc))
    typer.echo(f"Privater Schlüssel:     {private_path} (0600, nie weitergeben, sichern!)")
    typer.echo(f"Öffentlicher Schlüssel: {public_path}")
    typer.echo("")
    typer.echo("Installationen bekommen den öffentlichen Schlüssel als DMS_LICENSE_PUBLIC_KEY_PEM")
    typer.echo("(Helm: services.license-service.env, Compose: Override-Datei). Ohne diese")
    typer.echo("Einstellung akzeptiert license-service nur den eingebauten Entwicklungsschlüssel.")


@app.command("public-key")
def public_key(
    key: Path = typer.Option(None, "--key", help="Privater Schlüssel (Standard: Schlüssel-Home)."),
    dev: bool = typer.Option(False, "--dev", help="Entwicklungsschlüssel des Repositorys."),
) -> None:
    """Den öffentlichen Schlüssel zum privaten ausgeben (für DMS_LICENSE_PUBLIC_KEY_PEM)."""
    try:
        pem, _path, _is_dev = keys.load_private_key(key, dev=dev)
    except keys.KeyError_ as exc:
        _fail(str(exc))
    typer.echo(keys.public_pem_from_private(pem.encode("ascii")), nl=False)


@app.command()
def issue(
    licensee: str = typer.Option(..., "--licensee", help="Lizenznehmer, z. B. die Behörde."),
    until: str = typer.Option(None, "--until", help="Gültig bis (JJJJ-MM-TT, Tagesende UTC)."),
    days: int = typer.Option(None, "--days", min=1, help="Alternativ: gültig für N Tage ab jetzt."),
    not_before: str = typer.Option(None, "--not-before", help="Gültig ab (JJJJ-MM-TT)."),
    user_model: str = typer.Option(..., "--user-model", help="named oder concurrent."),
    max_users: int = typer.Option(None, "--max-users", min=1, help="Ohne Angabe: unbegrenzt."),
    storage_gb: float = typer.Option(None, "--storage-gb", help="Ohne Angabe: unbegrenzt."),
    documents: int = typer.Option(None, "--documents", min=1, help="Ohne Angabe: unbegrenzt."),
    component: list[str] = typer.Option(
        [], "--component", help=f"Wiederholbar. Bekannt: {', '.join(licenses.KNOWN_COMPONENTS)}."
    ),
    all_components: bool = typer.Option(
        False, "--all-components", help="Alle lizenzierbaren Komponenten (auch künftige)."
    ),
    no_components: bool = typer.Option(
        False, "--no-components", help="Nur der Kern, keine Zusatzkomponente."
    ),
    installation_id: str = typer.Option(
        None, "--installation-id", help="An eine Installation binden (DMS_INSTALLATION_ID)."
    ),
    output: Path = typer.Option(None, "-o", "--output", help="Lizenzdatei (Standard: Ausgabe)."),
    key: Path = typer.Option(None, "--key", help="Privater Schlüssel (Standard: Schlüssel-Home)."),
    dev: bool = typer.Option(
        False, "--dev", help="Mit dem Entwicklungsschlüssel signieren (nur Dev-Stack)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Ohne Rückfrage ausstellen."),
) -> None:
    """Eine Lizenz ausstellen und signieren."""
    if (until is None) == (days is None):
        _fail("genau eines von --until oder --days angeben")
    chosen = sum([bool(component), all_components, no_components])
    if chosen != 1:
        _fail(
            "Komponenten ausdrücklich wählen: --component … (wiederholbar), --all-components "
            "oder --no-components"
        )
    unknown = [c for c in component if c not in licenses.KNOWN_COMPONENTS]
    if unknown:
        typer.secho(
            f"Hinweis: {', '.join(unknown)} ist keine Standard-Komponente; nur sinnvoll, wenn die "
            "Installation sie in licensable_components führt.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    now = datetime.now(UTC)
    spec = licenses.LicenseSpec(
        licensee=licensee,
        expires_at=(
            _parse_date(until, "--until", end_of_day=True) if until else now + timedelta(days=days)
        ),
        not_before=(
            _parse_date(not_before, "--not-before", end_of_day=False) if not_before else None
        ),
        user_model=user_model,
        max_users=max_users,
        storage_limit_gb=storage_gb,
        document_limit=documents,
        licensed_components=None if all_components else sorted(set(component)),
        installation_id=installation_id,
        issued_at=now,
    )
    try:
        claims = licenses.build_claims(spec)
        pem, key_path, is_dev = keys.load_private_key(key, dev=dev)
    except (ValueError, keys.KeyError_) as exc:
        _fail(str(exc))

    typer.echo("Lizenz:", err=True)
    for label, value in licenses.describe(claims, now=now):
        typer.echo(f"  {label}: {value}", err=True)
    typer.echo(
        f"  Signatur: {'ENTWICKLUNGSSCHLÜSSEL (nur Dev-Stack)' if is_dev else key_path}", err=True
    )
    if not yes and not typer.confirm("Ausstellen?", default=False, err=True):
        _fail("abgebrochen", code=1)

    token = licenses.sign(claims, pem)
    if output:
        output.expanduser().write_text(token + "\n", encoding="ascii")
        typer.echo(f"Geschrieben: {output}", err=True)
    else:
        typer.echo(token)

    if not is_dev:
        # The licensor's own record of what was issued, next to the key.
        log = key_path.parent / keys.ISSUED_LOG_NAME
        entry = {
            "license_id": claims["jti"],
            "licensee": licensee,
            "issued_at": now.isoformat(),
            "claims": claims,
            "sha256": licenses.token_fingerprint(token),
        }
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


@app.command()
def inspect(
    source: str = typer.Argument(..., help="Lizenzdatei, `-` für Standardeingabe, oder das Token."),
    raw: bool = typer.Option(False, "--json", help="Header und Claims als JSON."),
) -> None:
    """Eine Lizenz lesbar anzeigen, OHNE die Signatur zu prüfen (dafür: verify)."""
    try:
        header, claims = licenses.decode_unverified(_read_token(source))
    except ValueError as exc:
        _fail(str(exc))
    if raw:
        typer.echo(json.dumps({"header": header, "claims": claims}, indent=2, ensure_ascii=False))
        return
    _print_rows([("Algorithmus", str(header.get("alg")))] + licenses.describe(claims))
    typer.secho("Signatur nicht geprüft – dafür `og-license verify`.", fg=typer.colors.YELLOW)


@app.command()
def verify(
    source: str = typer.Argument(..., help="Lizenzdatei, `-` für Standardeingabe, oder das Token."),
    public_key_file: Path = typer.Option(
        None,
        "--public-key",
        help="Öffentlicher Schlüssel (Standard: DMS_LICENSE_PUBLIC_KEY_PEM, dann Schlüssel-Home).",
    ),
    previous_public_key: Path = typer.Option(
        None, "--previous-public-key", help="Vorgänger-Schlüssel während einer Rotation (ADR 0084)."
    ),
    installation_id: str = typer.Option(
        None, "--installation-id", help="Prüfen wie die Installation mit dieser ID."
    ),
    dev: bool = typer.Option(False, "--dev", help="Gegen den Entwicklungsschlüssel prüfen."),
) -> None:
    """Prüfen, ob license-service die Lizenz annehmen würde. Exit 0 = gültig, 1 = nicht."""
    try:
        public_pem = keys.load_public_key(public_key_file, dev=dev)
        previous_pem = (
            previous_public_key.expanduser().read_text(encoding="ascii")
            if previous_public_key
            else None
        )
        result = licenses.verify(
            _read_token(source),
            public_key_pem=public_pem,
            previous_public_key_pem=previous_pem,
            installation_id=installation_id,
        )
    except (ValueError, keys.KeyError_) as exc:
        _fail(str(exc))
    signature = (
        "gültig" + (" (Vorgänger-Schlüssel)" if result.key_used == "previous" else "")
        if result.signature_ok
        else "UNGÜLTIG"
    )
    _print_rows([("Signatur", signature)] + licenses.describe(result.claims))
    if result.valid:
        typer.secho("Ergebnis: GÜLTIG", fg=typer.colors.GREEN)
        return
    typer.secho(f"Ergebnis: NICHT GÜLTIG – {'; '.join(result.problems)}", fg=typer.colors.RED)
    raise typer.Exit(code=1)


@app.command("issued")
def issued(
    key_dir: Path = typer.Option(None, "--dir", help="Schlüssel-Home (Standard: siehe oben)."),
) -> None:
    """Die bisher ausgestellten Lizenzen (Protokoll neben dem Schlüssel)."""
    log = (key_dir or keys.key_home()).expanduser() / keys.ISSUED_LOG_NAME
    if not log.exists():
        typer.echo("Noch keine Lizenz ausgestellt.")
        return
    for line in log.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        claims = entry["claims"]
        expires = datetime.fromtimestamp(claims["exp"], tz=UTC).date().isoformat()
        typer.echo(
            f"{entry['issued_at'][:10]}  {entry['license_id']}  bis {expires}  "
            f"{claims.get('installation_id') or '-':<16}  {entry['licensee']}"
        )


def run() -> None:
    app()
