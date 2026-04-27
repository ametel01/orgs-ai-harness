"""Read and write `harness.yml` without dropping supported sections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """Raised when `harness.yml` cannot be parsed for supported fields."""


@dataclass(frozen=True)
class ConfigBlock:
    key: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class HarnessConfig:
    org_name: str
    skills_version: str
    blocks: tuple[ConfigBlock, ...]

    def to_text(self) -> str:
        rendered: list[str] = []
        seen: set[str] = set()

        for block in self.blocks:
            seen.add(block.key)
            if block.key == "org":
                rendered.extend(_render_org_block(block, self.org_name, self.skills_version))
            else:
                rendered.extend(block.lines)
            rendered.append("")

        for block in _default_missing_blocks(seen):
            rendered.extend(block.lines)
            rendered.append("")

        while rendered and rendered[-1] == "":
            rendered.pop()
        return "\n".join(rendered) + "\n"


def render_default_harness_config(org_name: str) -> str:
    return HarnessConfig(
        org_name=org_name,
        skills_version="1",
        blocks=(
            ConfigBlock("org", ("org:", "  name: acme", "  skills_version: 1")),
            ConfigBlock("providers", ("providers: []",)),
            ConfigBlock("repos", ("repos: []",)),
            ConfigBlock("redaction", ("redaction:", "  globs: []", "  regexes: []")),
            ConfigBlock("command_permissions", ("command_permissions: []",)),
        ),
    ).to_text()


def load_harness_config(path: Path) -> HarnessConfig:
    return parse_harness_config(path.read_text(encoding="utf-8"))


def save_harness_config(path: Path, config: HarnessConfig) -> None:
    path.write_text(config.to_text(), encoding="utf-8")


def parse_harness_config(text: str) -> HarnessConfig:
    blocks = split_top_level_blocks(text)
    org_block = next((block for block in blocks if block.key == "org"), None)
    if org_block is None:
        raise ConfigError("harness.yml missing required field: org")

    org_name = read_block_scalar(org_block, "name")
    if org_name is None:
        raise ConfigError("harness.yml missing required field: org.name")

    skills_version = read_block_scalar(org_block, "skills_version")
    if skills_version is None:
        raise ConfigError("harness.yml missing required field: org.skills_version")

    return HarnessConfig(org_name=org_name, skills_version=skills_version, blocks=blocks)


def split_top_level_blocks(text: str) -> tuple[ConfigBlock, ...]:
    blocks: list[ConfigBlock] = []
    current_key: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        if not line.strip():
            if current_lines:
                current_lines.append(line)
            continue

        if not line.startswith(" ") and ":" in line:
            if current_key is not None:
                blocks.append(ConfigBlock(current_key, tuple(_trim_blank_tail(current_lines))))
            current_key = line.split(":", 1)[0]
            current_lines = [line]
            continue

        if current_key is not None:
            current_lines.append(line)

    if current_key is not None:
        blocks.append(ConfigBlock(current_key, tuple(_trim_blank_tail(current_lines))))

    return tuple(blocks)


def read_block_scalar(block: ConfigBlock, field: str) -> str | None:
    prefix = f"  {field}:"
    for line in block.lines[1:]:
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return None


def block_has_field(block: ConfigBlock, field: str) -> bool:
    prefix = f"  {field}:"
    return any(line.startswith(prefix) for line in block.lines[1:])


def _render_org_block(block: ConfigBlock, org_name: str, skills_version: str) -> list[str]:
    rendered = ["org:"]
    wrote_name = False
    wrote_version = False

    for line in block.lines[1:]:
        if line.startswith("  name:"):
            rendered.append(f"  name: {org_name}")
            wrote_name = True
        elif line.startswith("  skills_version:"):
            rendered.append(f"  skills_version: {skills_version}")
            wrote_version = True
        else:
            rendered.append(line)

    if not wrote_name:
        rendered.append(f"  name: {org_name}")
    if not wrote_version:
        rendered.append(f"  skills_version: {skills_version}")

    return rendered


def _default_missing_blocks(seen: set[str]) -> tuple[ConfigBlock, ...]:
    defaults = []
    if "providers" not in seen:
        defaults.append(ConfigBlock("providers", ("providers: []",)))
    if "repos" not in seen:
        defaults.append(ConfigBlock("repos", ("repos: []",)))
    if "redaction" not in seen:
        defaults.append(ConfigBlock("redaction", ("redaction:", "  globs: []", "  regexes: []")))
    if "command_permissions" not in seen:
        defaults.append(ConfigBlock("command_permissions", ("command_permissions: []",)))
    return tuple(defaults)


def _trim_blank_tail(lines: list[str]) -> list[str]:
    while lines and not lines[-1].strip():
        lines.pop()
    return lines

