"""Test configuration — make hyphenated agent directories importable."""

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Register hyphenated directories as importable Python packages.
# pytest loads conftest.py before collecting test modules, so this
# runs early enough for `from agent_03_deduplication...` imports to work.

_ALIASES = {
    "agent_03_deduplication": ROOT / "agent-03-deduplication",
}

for alias, pkg_path in _ALIASES.items():
    if alias in sys.modules:
        continue

    # Create a top-level package entry so `from agent_03_deduplication.algorithms...` works.
    spec = importlib.util.spec_from_file_location(
        alias,
        pkg_path / "algorithms" / "__init__.py",
        submodule_search_locations=[str(pkg_path)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod

    # Also register the algorithms subpackage explicitly.
    alg_path = pkg_path / "algorithms"
    alg_spec = importlib.util.spec_from_file_location(
        f"{alias}.algorithms",
        alg_path / "__init__.py",
        submodule_search_locations=[str(alg_path)],
    )
    alg_mod = importlib.util.module_from_spec(alg_spec)
    sys.modules[f"{alias}.algorithms"] = alg_mod
    alg_spec.loader.exec_module(alg_mod)
