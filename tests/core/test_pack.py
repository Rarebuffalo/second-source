"""Tests for pack loading (core/pack.py)."""

import textwrap

import pytest

from second_source.core.engine import Engine
from second_source.core.pack import PackError, build_function_registry, load_pack

CHECK = """
id: {pack}.always
version: 1.0.0
kind: rule
level: L2
applies_to: [{claim_type}]
outcomes:
  - {{otherwise: true, result: VERIFIED, reason: OK}}
"""


def write_pack(tmp_path, manifest, checks=None):
    """Create a pack directory with a manifest and optional check files."""
    (tmp_path / "pack.yaml").write_text(textwrap.dedent(manifest))
    for name, content in (checks or {}).items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


MANIFEST = """
id: demo
version: 0.1.0
kind: agent_type
claim_types: [THING_DONE]
checks: [checks/always.yaml]
"""


def test_loads_a_valid_pack(tmp_path):
    write_pack(
        tmp_path,
        MANIFEST,
        {"checks/always.yaml": CHECK.format(pack="demo", claim_type="THING_DONE")},
    )
    pack = load_pack(tmp_path)
    assert pack.id == "demo"
    assert [c.id for c in pack.checks] == ["demo.always"]


def test_check_ids_must_be_prefixed_with_the_pack_id(tmp_path):
    write_pack(
        tmp_path,
        MANIFEST,
        {"checks/always.yaml": CHECK.format(pack="other", claim_type="THING_DONE")},
    )
    with pytest.raises(PackError, match="must be named 'demo.<name>'"):
        load_pack(tmp_path)


def test_checks_must_use_declared_claim_types(tmp_path):
    write_pack(
        tmp_path,
        MANIFEST,
        {"checks/always.yaml": CHECK.format(pack="demo", claim_type="UNDECLARED")},
    )
    with pytest.raises(PackError, match="undeclared claim types"):
        load_pack(tmp_path)


def test_check_files_outside_the_pack_are_rejected(tmp_path):
    manifest = MANIFEST.replace("checks/always.yaml", "../escape.yaml")
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    write_pack(pack_dir, manifest)
    with pytest.raises(PackError, match="outside the pack directory"):
        load_pack(pack_dir)


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ("id: Demo", "lower_snake_case"),
        ("version: one", "semantic"),
        ("kind: plugin", "kind must be"),
        ("claim_types: []", "claim_types"),
    ],
)
def test_invalid_manifest_fields(tmp_path, bad, message):
    key = bad.split(":")[0]
    lines = [line for line in MANIFEST.strip().splitlines() if not line.startswith(key)]
    write_pack(tmp_path, "\n".join([*lines, bad]))
    with pytest.raises(PackError, match=message):
        load_pack(tmp_path)


def test_missing_and_malformed_files_give_clear_errors(tmp_path):
    with pytest.raises(PackError, match="file not found"):
        load_pack(tmp_path)
    write_pack(tmp_path, "id: [unclosed")
    with pytest.raises(PackError, match="invalid YAML"):
        load_pack(tmp_path)


def test_function_hook_is_imported_and_registered(tmp_path, monkeypatch):
    # A tiny importable module that registers one helper function.
    module_dir = tmp_path / "mods"
    module_dir.mkdir()
    (module_dir / "demo_functions.py").write_text(
        "from second_source.core.expr import to_cel\n"
        "def register(registry):\n"
        "    registry.register('double', lambda n: to_cel(int(n) * 2))\n"
    )
    monkeypatch.syspath_prepend(str(module_dir))
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    write_pack(
        pack_dir,
        MANIFEST + "functions: demo_functions:register\n",
        {
            "checks/always.yaml": CHECK.format(pack="demo", claim_type="THING_DONE"),
        },
    )
    pack = load_pack(pack_dir)
    assert "double" in build_function_registry([pack])
    Engine.from_packs([pack])  # builds without errors


def test_bad_function_reference_is_rejected(tmp_path):
    write_pack(tmp_path, MANIFEST.replace("checks: [checks/always.yaml]", "functions: not-a-ref"))
    with pytest.raises(PackError, match="module.path:callable"):
        load_pack(tmp_path)
