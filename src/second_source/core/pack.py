"""Pack loading: how domain logic plugs into the domain-neutral core.

A pack is a directory with a ``pack.yaml`` manifest, for example::

    id: customer_support_demo
    version: 0.1.0
    kind: agent_type            # or: overlay
    description: Refund claims made by support agents.
    claim_types: [REFUND_ISSUED]
    checks:
      - checks/refund_issued.yaml
    functions: my_package.my_pack:register   # optional: adds CEL helper functions

Each check file holds one check definition or a list of them. Check ids must start with the
pack id (``customer_support_demo.refund_issued``) so packs can never clash, and every check
must apply only to claim types the pack declares.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from second_source.core.checks import CheckDefinition, CheckDefinitionError
from second_source.core.expr import FunctionRegistry

PACK_KINDS = frozenset({"agent_type", "overlay"})
_PACK_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_FUNCTION_REF = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")  # "module.path:callable"

RegisterFunctions = Callable[[FunctionRegistry], None]


class PackError(ValueError):
    """A pack manifest or one of its files is invalid."""


@dataclass(frozen=True)
class Pack:
    id: str
    version: str
    kind: str
    claim_types: tuple[str, ...]
    checks: tuple[CheckDefinition, ...]
    description: str = ""
    register_functions: RegisterFunctions | None = None


def load_pack(directory: str | Path) -> Pack:
    """Load and validate a pack from a directory. Raises PackError with a clear message."""
    root = Path(directory).resolve()
    manifest = _read_yaml(root / "pack.yaml")
    if not isinstance(manifest, dict):
        raise PackError(f"{root / 'pack.yaml'}: expected a mapping")

    pack_id = str(manifest.get("id", ""))

    def fail(problem: str) -> PackError:
        return PackError(f"pack {pack_id or root.name}: {problem}")

    # Manifest fields.
    if not _PACK_ID.match(pack_id):
        raise fail("id must be lower_snake_case")
    version = str(manifest.get("version", ""))
    if not _VERSION.match(version):
        raise fail("version must be semantic, e.g. '0.1.0'")
    kind = manifest.get("kind")
    if kind not in PACK_KINDS:
        raise fail(f"kind must be one of {sorted(PACK_KINDS)}")
    claim_types = manifest.get("claim_types") or []
    if not claim_types or not all(isinstance(t, str) and t for t in claim_types):
        raise fail("claim_types must be a non-empty list")

    # Check files: resolved inside the pack directory only (no path traversal).
    checks: list[CheckDefinition] = []
    for relative in manifest.get("checks") or []:
        path = (root / str(relative)).resolve()
        if root not in path.parents:
            raise fail(f"check file {relative!r} is outside the pack directory")
        for raw in _as_list(_read_yaml(path)):
            try:
                check = CheckDefinition.from_dict(raw)
            except CheckDefinitionError as error:
                raise fail(f"{relative}: {error}") from error
            if not check.id.startswith(f"{pack_id}."):
                raise fail(f"check {check.id} must be named '{pack_id}.<name>'")
            unknown = set(check.applies_to) - set(claim_types)
            if unknown:
                raise fail(f"check {check.id} applies to undeclared claim types {sorted(unknown)}")
            checks.append(check)

    # Optional Python hook that registers CEL helper functions.
    register = None
    reference = manifest.get("functions")
    if reference:
        register = _import_callable(str(reference), fail)

    return Pack(
        id=pack_id,
        version=version,
        kind=kind,
        claim_types=tuple(claim_types),
        checks=tuple(checks),
        description=str(manifest.get("description", "")),
        register_functions=register,
    )


def build_function_registry(packs: Iterable[Pack]) -> FunctionRegistry:
    """Collect every pack's helper functions into one registry (name clashes are errors)."""
    registry = FunctionRegistry()
    for pack in packs:
        if pack.register_functions is not None:
            pack.register_functions(registry)
    return registry


def _read_yaml(path: Path) -> Any:
    """Read a YAML file with the safe loader (never executes tags)."""
    try:
        with path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError as error:
        raise PackError(f"{path}: file not found") from error
    except yaml.YAMLError as error:
        raise PackError(f"{path}: invalid YAML: {error}") from error


def _as_list(value: Any) -> list[dict[str, Any]]:
    """A check file may contain one definition (a mapping) or several (a list)."""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    raise PackError("a check file must contain a mapping or a list of mappings")


def _import_callable(reference: str, fail: Callable[[str], PackError]) -> RegisterFunctions:
    """Import ``module.path:callable``. Packs are trusted code shipped with the package."""
    if not _FUNCTION_REF.match(reference):
        raise fail(f"functions must look like 'module.path:callable', got {reference!r}")
    module_name, attribute = reference.split(":")
    try:
        function = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as error:
        raise fail(f"cannot import {reference!r}: {error}") from error
    if not callable(function):
        raise fail(f"{reference!r} is not callable")
    return function
