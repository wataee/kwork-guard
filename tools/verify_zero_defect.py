from __future__ import annotations

import ast
import io
from pathlib import Path
import sys
import tokenize

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from guard.bsl_ls_runner import BslLanguageServerService

TARGET_DIRECTORIES = [
    "guard",
    "cockpit",
]

TARGET_FILES = [
    "edt_guard.py",
    "launch_cockpit.pyw",
    "tools/cost_report.py",
    "tools/order.py",
    "tools/install_shortcut.py",
    "tools/generate_icon.py",
    "tests/test_guard_v2.py",
    "tests/test_cockpit_services.py",
    "tests/test_cockpit_gui_smoke.py",
    "tests/test_coverage_boost.py",
    "tests/test_platform_context.py",
    "tests/test_bsl_ls_runner.py",
    "tests/test_compact_schema.py",
    "tests/test_bsl_linter_v2.py",
    "tests/test_new_macros.py",
    "tests/test_dual_backend.py",
    "tests/test_optimization_v21.py",
    "tests/test_cockpit_v21.py",
]


class CodeDefectDetector(ast.NodeVisitor):
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.violations: list[str] = []

    def visit_Pass(self, node: ast.Pass) -> None:
        self.violations.append(f"{self.file_path}:{node.lineno}: FORBIDDEN 'pass' keyword detected")
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        if isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Name):
            if node.exc.func.id == "NotImplementedError":
                self.violations.append(f"{self.file_path}:{node.lineno}: FORBIDDEN NotImplementedError detected")
        elif isinstance(node.exc, ast.Name) and node.exc.id == "NotImplementedError":
            self.violations.append(f"{self.file_path}:{node.lineno}: FORBIDDEN NotImplementedError detected")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_docstring(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_docstring(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._check_docstring(node)
        self.generic_visit(node)

    def _check_docstring(self, node: ast.AST) -> None:
        if getattr(node, "body", None):
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                self.violations.append(f"{self.file_path}:{first.lineno}: FORBIDDEN docstring inside function/class detected")


def check_file_comments(file_path: Path) -> list[str]:
    violations: list[str] = []
    text = file_path.read_text(encoding="utf-8")

    if "TODO" in text:
        for idx, line in enumerate(text.splitlines(), start=1):
            if "TODO" in line:
                violations.append(f"{file_path}:{idx}: FORBIDDEN 'TODO' detected")

    tokens = tokenize.tokenize(io.BytesIO(text.encode("utf-8")).readline)
    for tok_type, tok_string, (srow, scol), _, _ in tokens:
        if tok_type == tokenize.COMMENT:
            violations.append(f"{file_path}:{srow}: FORBIDDEN comment '{tok_string}' detected")

    return violations


def scan_codebase(root_dir: Path) -> list[str]:
    all_violations: list[str] = []
    python_files: list[Path] = []

    for d in TARGET_DIRECTORIES:
        folder = root_dir / d
        if folder.is_dir():
            for p in folder.rglob("*.py"):
                python_files.append(p)

    for rel_path in TARGET_FILES:
        target = root_dir / rel_path
        if target.is_file():
            python_files.append(target)

    for py_file in python_files:
        comment_errors = check_file_comments(py_file)
        all_violations.extend(comment_errors)

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            detector = CodeDefectDetector(py_file)
            detector.visit(tree)
            all_violations.extend(detector.violations)
        except Exception as exc:
            all_violations.append(f"{py_file}: Parse error: {exc}")

    return all_violations


def verify_bsl_directory(target_dir: Path, final_validation: bool = False) -> tuple[bool, list[str]]:
    if not target_dir.is_dir():
        return True, []

    ls_service = BslLanguageServerService()
    findings: list[str] = []
    has_errors = False

    for bsl_file in sorted(target_dir.rglob("*.bsl")):
        raw_bytes = bsl_file.read_bytes()
        if not raw_bytes.startswith(b"\xef\xbb\xbf"):
            findings.append(f"{bsl_file.name}: missing UTF-8 BOM")
        is_obj = bsl_file.name == "ObjectModule.bsl"
        diags = ls_service.run_hybrid_diagnostics(
            bsl_file,
            final_validation=final_validation,
            is_object_module=is_obj,
        )
        for diag in diags:
            if diag.severity.upper() in ("ERROR", "CRITICAL"):
                has_errors = True
            findings.append(f"{bsl_file.name}:{diag.line}:{diag.col} [{diag.code}] {diag.message}")

    return not has_errors, findings


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    violations = scan_codebase(root)
    if "--bsl" in sys.argv:
        idx = sys.argv.index("--bsl")
        bsl_dir = Path(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else root
        bsl_ok, bsl_findings = verify_bsl_directory(bsl_dir, final_validation=True)
        if not bsl_ok:
            violations.extend(bsl_findings)
    if violations:
        sys.stderr.write(f"Zero-Defect Scan FAILED: {len(violations)} violations found:\n")
        for v in violations:
            sys.stderr.write(f"  • {v}\n")
        return 1
    sys.stdout.write("Zero-Defect Scan PASSED: ZERO comments, ZERO docstrings inside functions/classes, ZERO pass/TODO.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
