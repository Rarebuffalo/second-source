"""Coding pack: verify claims made by coding agents in pull requests.

See docs/packs/coding.md for the design and the evidence contract.
"""

from pathlib import Path

from second_source.core.pack import Pack, load_pack

# The pack's manifest and check files live next to this module.
PACK_DIR = Path(__file__).parent


def load() -> Pack:
    """Load the coding pack shipped with Second Source."""
    return load_pack(PACK_DIR)
