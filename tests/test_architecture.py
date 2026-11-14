"""Architecture rules that the import linter cannot express on its own."""

import ast
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


# -- ADR-010: PyMuPDF (AGPLv3) is gone from the code and from the requirements ---------------------------

BANNED_IMPORT_ROOTS = frozenset({"fitz", "pymupdf", "pymupdf4llm", "pymupdf_layout"})


def _python_sources() -> list[Path]:
    return sorted(
        [*(ROOT / "app").rglob("*.py"), *(ROOT / "tests").rglob("*.py"), ROOT / "universal_ingestor.py"],
        key=str,
    )


def _banned_imports(path: Path) -> list[str]:
    """Executable uses of the banned packages: import statements and ``import_module("fitz")`` style calls.
    Names inside strings or comments (the SBOM audit's ban list, a docstring) are not imports and do not count."""
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in BANNED_IMPORT_ROOTS]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and (node.module or "").split(".")[0] in BANNED_IMPORT_ROOTS:
                found.append(node.module or "")
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            argument = node.args[0].value
            if callee in {"import_module", "__import__"} and str(argument).split(".")[0] in BANNED_IMPORT_ROOTS:
                found.append(str(argument))
    return found


def test_no_python_file_imports_pymupdf():
    offenders = {str(p.relative_to(ROOT)): hits for p in _python_sources() if (hits := _banned_imports(p))}
    assert not offenders, f"ADR-010: PyMuPDF/fitz is AGPLv3 and must not be imported anywhere: {offenders}"


def test_the_import_scan_sees_what_it_should(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import fitz\nimport pymupdf.utils\nfrom pymupdf import Rect\nimport importlib\n"
        "importlib.import_module('fitz')\nprint('import fitz')  # import pymupdf\nimport os\n",
        encoding="utf-8",
    )
    assert _banned_imports(sample) == ["fitz", "pymupdf.utils", "pymupdf", "fitz"]


def _requirement_lines(name: str) -> list[str]:
    return (ROOT / name).read_text(encoding="utf-8-sig").splitlines()


def test_requirements_pass_the_adr010_licence_audit():
    from app.ops.sbom_license_audit import audit_requirements_lines

    for name in ("requirements.txt", "requirements.lock"):
        lines = _requirement_lines(name)
        report = audit_requirements_lines(lines)
        assert report.passed is True and report.blocking_violations == (), f"{name}: {report.blocking_violations}"
        assert report.scanned_components > 10, f"{name} was not read"
        assert not [ln for ln in lines if ln.strip().lower().replace("_", "-").startswith("pymupdf")], name


def test_the_licence_audit_does_flag_pymupdf():
    from app.ops.sbom_license_audit import audit_requirements_lines

    report = audit_requirements_lines(["fastapi>=0.1", "pymupdf>=1.24  # AGPL"])
    assert report.passed is False and [v.package_name for v in report.blocking_violations] == ["pymupdf"]


def test_the_pdf_engine_dependencies_are_declared():
    names = {
        ln.split("#")[0].strip().split(">=")[0].split("==")[0].lower().replace("_", "-")
        for ln in _requirement_lines("requirements.txt")
    }
    assert {"pypdfium2", "pdfplumber", "pypdf", "reportlab", "pytesseract", "pillow"} <= names
