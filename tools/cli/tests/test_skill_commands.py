"""The admin skill (`skills/og-doc-admin`, P96-S2, ADR 0253 §6) names CLI
commands in its recipes and references. These tests keep it in step with the
CLI: every `dms …` command it mentions must exist with the options it uses,
and its category lists must match config-service's."""

import re
import shlex
from pathlib import Path

import pytest
import typer.main
from dms_cli.main import app

SKILL = Path(__file__).resolve().parents[3] / "skills" / "og-doc-admin"
_INLINE = re.compile(r"`(dms [^`]+)`")
_LINE = re.compile(r"^\s*(dms\s.+)$", re.MULTILINE)


def _mentions() -> list[tuple[str, str]]:
    found = []
    for path in sorted(SKILL.rglob("*")):
        if path.suffix not in (".md", ".sh"):
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in (_INLINE, _LINE):
            for match in pattern.finditer(text):
                found.append((path.relative_to(SKILL).as_posix(), match.group(1)))
    return found


def _tokens(command: str) -> list[str]:
    command = command.replace("[", " ").replace("]", " ").replace("…", " ")
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return parts[1:]  # drop "dms"


def _expand(tokens: list[str]) -> list[list[str]]:
    """`list|create|delete` stands for three commands."""
    variants = [[]]
    for token in tokens:
        options = token.split("|") if "|" in token and not token.startswith("-") else [token]
        variants = [v + [o] for v in variants for o in options if o]
    return variants


def _is_group(node) -> bool:
    # Typer ships its own Click copy; check by shape, not by class.
    return hasattr(node, "commands")


def _check(root, tokens: list[str]) -> str | None:
    tokens = list(tokens)
    if tokens[:1] == ["-o"]:
        tokens = tokens[2:]
    node = root
    while _is_group(node) and tokens and not tokens[0].startswith("-"):
        name = tokens.pop(0)
        child = node.commands.get(name)
        if child is None:
            if name.startswith("<"):
                return None  # a placeholder in prose, e.g. "dms <command>"
            return f"unknown subcommand {name!r} under {node.name!r}"
        node = child
    if _is_group(node):
        return None  # a group mentioned on its own
    known = {opt for param in node.params for opt in getattr(param, "opts", [])}
    known |= {opt for param in node.params for opt in getattr(param, "secondary_opts", [])}
    for token in tokens:
        if token.startswith("--"):
            for option in token.split("=", 1)[0].split("/"):
                if option.startswith("--") and option not in known:
                    return f"unknown option {option!r} for {node.name!r}"
    return None


def test_the_skill_exists_and_mentions_commands():
    assert (SKILL / "SKILL.md").is_file()
    assert len(_mentions()) > 40


@pytest.mark.parametrize("source,command", _mentions())
def test_every_command_in_the_skill_exists(source, command):
    root = typer.main.get_command(app)
    for variant in _expand(_tokens(command)):
        problem = _check(root, variant)
        assert problem is None, f"{source}: `{command}` - {problem}"


def test_the_skill_lists_every_configuration_category():
    from config_service.schemas import CATEGORIES

    packages = (SKILL / "references" / "packages.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `([a-z_]+)` \|", packages, re.MULTILINE))
    assert listed == set(CATEGORIES)
    script = (SKILL / "scripts" / "preview-package.sh").read_text(encoding="utf-8")
    assert set(re.findall(r'"([a-z_]+)"', script.split("known = {", 1)[1].split("}", 1)[0])) == set(
        CATEGORIES
    )
