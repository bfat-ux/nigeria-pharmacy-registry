"""Test configuration — make hyphenated agent directories importable."""

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Register hyphenated directories as importable Python packages.
# pytest loads conftest.py before collecting test modules, so this
# runs early enough for `from agent_03_deduplication...` imports to work.

# Each entry maps: alias → (package_path, list_of_subpackages_to_register)
_ALIASES: dict[str, tuple[Path, list[str]]] = {
    "agent_03_deduplication": (ROOT / "agent-03-deduplication", ["algorithms"]),
    "agent_05_platform_api": (ROOT / "agent-05-platform-api", ["src"]),
}

for alias, (pkg_path, subpackages) in _ALIASES.items():
    if alias in sys.modules:
        continue

    # Find an __init__.py to anchor the top-level package.
    # Try the first subpackage's __init__.py, or use a bare namespace.
    init_file = None
    for sub in subpackages:
        candidate = pkg_path / sub / "__init__.py"
        if candidate.exists():
            init_file = candidate
            break

    if init_file is None:
        # Bare namespace package
        spec = importlib.machinery.ModuleSpec(
            alias,
            None,
            is_package=True,
        )
        spec.submodule_search_locations = [str(pkg_path)]
    else:
        spec = importlib.util.spec_from_file_location(
            alias,
            init_file,
            submodule_search_locations=[str(pkg_path)],
        )

    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod

    # Register each subpackage explicitly.
    for sub in subpackages:
        sub_path = pkg_path / sub
        sub_init = sub_path / "__init__.py"
        if not sub_init.exists():
            continue

        sub_fqn = f"{alias}.{sub}"
        if sub_fqn in sys.modules:
            continue

        sub_spec = importlib.util.spec_from_file_location(
            sub_fqn,
            sub_init,
            submodule_search_locations=[str(sub_path)],
        )
        sub_mod = importlib.util.module_from_spec(sub_spec)
        sys.modules[sub_fqn] = sub_mod
        sub_spec.loader.exec_module(sub_mod)
