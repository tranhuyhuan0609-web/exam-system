"""Architecture rules that the import linter cannot express on its own."""

import configparser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = ROOT / "app" / "modules"


def _module_packages() -> list[str]:
    return sorted(p.name for p in MODULES_DIR.iterdir() if p.is_dir() and (p / "__init__.py").exists())


def _protected_internals() -> set[str]:
    config = configparser.ConfigParser()
    config.read(ROOT / ".importlinter", encoding="utf-8")
    protected: set[str] = set()
    for section in config.sections():
        if config.get(section, "type", fallback="") == "protected":
            protected.update(config.get(section, "protected_modules").split())
    return protected


def test_every_module_keeps_its_internals_private():
    protected = _protected_internals()
    missing = [name for name in _module_packages() if f"app.modules.{name}.internal" not in protected]
    assert not missing, f"add a 'protected' contract to .importlinter for: {missing}"


def test_every_module_has_the_standard_layout():
    for name in _module_packages():
        package = MODULES_DIR / name
        assert (package / "public.py").exists(), f"app/modules/{name}/public.py is the module's only entry point"
        assert (package / "internal" / "__init__.py").exists(), f"app/modules/{name}/internal/ holds private code"
