"""
Tests for agent.scanner — RepositoryScanner
"""
from __future__ import annotations

import textwrap
from datetime import datetime
from pathlib import Path

import pytest

from agent.scanner import (
    CodeSmell,
    DocGap,
    DependencyManifest,
    FileInfo,
    RepositoryScanner,
    RepositorySnapshot,
)


# ---------------------------------------------------------------------------
# Minimal config fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return {
        "scanner": {
            "include_extensions": [".py", ".md", ".toml", ".json"],
            "exclude_dirs": [".git", "__pycache__", "node_modules"],
            "max_file_size_kb": 500,
        }
    }


@pytest.fixture
def scanner(config):
    return RepositoryScanner(config)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, rel: str, content: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# scan() — basic snapshot
# ---------------------------------------------------------------------------

class TestScanBasic:
    def test_returns_snapshot_type(self, scanner, tmp_path):
        snap = scanner.scan(str(tmp_path))
        assert isinstance(snap, RepositorySnapshot)

    def test_snapshot_root_is_resolved(self, scanner, tmp_path):
        snap = scanner.scan(str(tmp_path))
        assert snap.root == str(tmp_path.resolve())

    def test_snapshot_has_scanned_at(self, scanner, tmp_path):
        snap = scanner.scan(str(tmp_path))
        assert isinstance(snap.scanned_at, datetime)

    def test_empty_dir_no_files(self, scanner, tmp_path):
        snap = scanner.scan(str(tmp_path))
        assert snap.files == []
        assert snap.summary["total_files"] == 0

    def test_summary_keys_present(self, scanner, tmp_path):
        snap = scanner.scan(str(tmp_path))
        for key in ("total_files", "doc_gaps", "code_smells", "dep_manifests", "test_ratio"):
            assert key in snap.summary


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

class TestFileDiscovery:
    def test_picks_up_py_file(self, scanner, tmp_path):
        _write(tmp_path, "hello.py", "x = 1\n")
        snap = scanner.scan(str(tmp_path))
        paths = [f.path for f in snap.files]
        assert "hello.py" in paths

    def test_excludes_wrong_extension(self, scanner, tmp_path):
        _write(tmp_path, "binary.exe", "junk")
        snap = scanner.scan(str(tmp_path))
        assert snap.files == []

    def test_excludes_hidden_dirs(self, scanner, tmp_path):
        _write(tmp_path, ".hidden/secret.py", "x = 1\n")
        snap = scanner.scan(str(tmp_path))
        assert snap.files == []

    def test_excludes_configured_dirs(self, scanner, tmp_path):
        _write(tmp_path, "__pycache__/compiled.py", "x = 1\n")
        snap = scanner.scan(str(tmp_path))
        assert all("__pycache__" not in f.path for f in snap.files)

    def test_file_info_fields(self, scanner, tmp_path):
        _write(tmp_path, "sample.py", "x = 1\n")
        snap = scanner.scan(str(tmp_path))
        fi = snap.files[0]
        assert isinstance(fi, FileInfo)
        assert fi.language == "python"
        assert fi.size_bytes > 0
        assert isinstance(fi.last_modified, datetime)

    def test_language_detection_md(self, scanner, tmp_path):
        _write(tmp_path, "NOTES.md", "# hello\n")
        snap = scanner.scan(str(tmp_path))
        assert snap.files[0].language == "markdown"

    def test_nested_files_discovered(self, scanner, tmp_path):
        _write(tmp_path, "pkg/sub/mod.py", "x = 1\n")
        snap = scanner.scan(str(tmp_path))
        assert any("mod.py" in f.path for f in snap.files)


# ---------------------------------------------------------------------------
# Documentation gaps — Python
# ---------------------------------------------------------------------------

class TestPythonDocGaps:
    def test_missing_module_docstring(self, scanner, tmp_path):
        _write(tmp_path, "mod.py", """\
            x = 1
        """)
        snap = scanner.scan(str(tmp_path))
        kinds = [g.kind for g in snap.doc_gaps]
        assert "missing_module_docstring" in kinds

    def test_no_module_gap_when_docstring_present(self, scanner, tmp_path):
        _write(tmp_path, "mod.py", '''\
            """Module docstring."""
            x = 1
        ''')
        snap = scanner.scan(str(tmp_path))
        module_gaps = [g for g in snap.doc_gaps if g.kind == "missing_module_docstring"]
        assert module_gaps == []

    def test_missing_function_docstring(self, scanner, tmp_path):
        _write(tmp_path, "mod.py", '''\
            """Mod."""
            def foo():
                return 1
        ''')
        snap = scanner.scan(str(tmp_path))
        fn_gaps = [g for g in snap.doc_gaps if g.kind == "missing_function_docstring"]
        assert any(g.symbol == "foo" for g in fn_gaps)

    def test_no_function_gap_when_docstring_present(self, scanner, tmp_path):
        _write(tmp_path, "mod.py", '''\
            """Mod."""
            def foo():
                """Does foo."""
                return 1
        ''')
        snap = scanner.scan(str(tmp_path))
        fn_gaps = [g for g in snap.doc_gaps if g.symbol == "foo"]
        assert fn_gaps == []

    def test_missing_class_docstring(self, scanner, tmp_path):
        _write(tmp_path, "mod.py", '''\
            """Mod."""
            class Bar:
                pass
        ''')
        snap = scanner.scan(str(tmp_path))
        cls_gaps = [g for g in snap.doc_gaps if g.kind == "missing_class_docstring"]
        assert any(g.symbol == "Bar" for g in cls_gaps)

    def test_doc_gap_has_line_number(self, scanner, tmp_path):
        _write(tmp_path, "mod.py", '''\
            """Mod."""
            def foo():
                return 1
        ''')
        snap = scanner.scan(str(tmp_path))
        fn_gaps = [g for g in snap.doc_gaps if g.symbol == "foo"]
        assert fn_gaps[0].line >= 1

    def test_missing_readme_detected(self, scanner, tmp_path):
        snap = scanner.scan(str(tmp_path))
        readme_gaps = [g for g in snap.doc_gaps if g.kind == "missing_readme"]
        assert len(readme_gaps) == 1

    def test_no_readme_gap_when_readme_present(self, scanner, tmp_path):
        _write(tmp_path, "README.md", "# Project\n")
        snap = scanner.scan(str(tmp_path))
        readme_gaps = [g for g in snap.doc_gaps if g.kind == "missing_readme"]
        assert readme_gaps == []


# ---------------------------------------------------------------------------
# Code smells
# ---------------------------------------------------------------------------

class TestCodeSmells:
    def test_todo_detected(self, scanner, tmp_path):
        _write(tmp_path, "mod.py", '''\
            """Mod."""
            # TODO: fix this
            x = 1
        ''')
        snap = scanner.scan(str(tmp_path))
        todos = [s for s in snap.code_smells if s.kind == "todo"]
        assert len(todos) >= 1
        assert todos[0].line >= 1

    def test_fixme_detected(self, scanner, tmp_path):
        _write(tmp_path, "mod.py", '''\
            """Mod."""
            # FIXME: broken
            x = 1
        ''')
        snap = scanner.scan(str(tmp_path))
        fixmes = [s for s in snap.code_smells if s.kind == "todo"]
        assert len(fixmes) >= 1

    def test_long_function_detected(self, scanner, tmp_path):
        # Build a function with 65 lines — write directly to avoid dedent issues
        lines = ['"""Mod."""', "def big_func():", '    """Big."""']
        lines += [f"    x{i} = {i}" for i in range(65)]
        lines.append("    return 0")
        (tmp_path / "mod.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
        snap = scanner.scan(str(tmp_path))
        long_fns = [s for s in snap.code_smells if s.kind == "long_function"]
        assert len(long_fns) >= 1
        assert "big_func" in long_fns[0].description

    def test_missing_return_type_hint(self, scanner, tmp_path):
        _write(tmp_path, "mod.py", '''\
            """Mod."""
            def compute(x):
                """Does thing."""
                return x * 2
        ''')
        snap = scanner.scan(str(tmp_path))
        hints = [s for s in snap.code_smells if s.kind == "missing_type_hints"]
        assert any("compute" in s.description for s in hints)

    def test_init_excluded_from_type_hint_check(self, scanner, tmp_path):
        _write(tmp_path, "mod.py", '''\
            """Mod."""
            class Foo:
                """Foo."""
                def __init__(self):
                    """Init."""
                    pass
        ''')
        snap = scanner.scan(str(tmp_path))
        init_hints = [s for s in snap.code_smells
                      if s.kind == "missing_type_hints" and "__init__" in s.description]
        assert init_hints == []

    def test_code_smell_dataclass_fields(self, scanner, tmp_path):
        _write(tmp_path, "mod.py", '''\
            """Mod."""
            # TODO: improve this
            x = 1
        ''')
        snap = scanner.scan(str(tmp_path))
        smell = snap.code_smells[0]
        assert isinstance(smell, CodeSmell)
        assert smell.file_path
        assert smell.kind
        assert smell.line >= 1


# ---------------------------------------------------------------------------
# Dependency manifests
# ---------------------------------------------------------------------------

class TestDependencyManifests:
    def test_requirements_txt_detected(self, scanner, tmp_path):
        _write(tmp_path, "requirements.txt", "requests>=2.28\n")
        snap = scanner.scan(str(tmp_path))
        manifests = [m for m in snap.dep_manifests if m.manager == "pip"]
        assert len(manifests) >= 1

    def test_package_json_detected(self, scanner, tmp_path):
        _write(tmp_path, "package.json", '{"name":"foo","dependencies":{}}')
        snap = scanner.scan(str(tmp_path))
        manifests = [m for m in snap.dep_manifests if m.manager == "npm"]
        assert len(manifests) >= 1

    def test_manifest_has_raw_content(self, scanner, tmp_path):
        content = "requests>=2.28\n"
        _write(tmp_path, "requirements.txt", content)
        snap = scanner.scan(str(tmp_path))
        m = snap.dep_manifests[0]
        assert isinstance(m, DependencyManifest)
        assert "requests" in m.raw_content


# ---------------------------------------------------------------------------
# Test ratio
# ---------------------------------------------------------------------------

class TestTestRatio:
    def test_no_tests_ratio_zero(self, scanner, tmp_path):
        _write(tmp_path, "app.py", '"""App."""\nx = 1\n')
        snap = scanner.scan(str(tmp_path))
        assert snap.test_ratio == 0.0

    def test_test_file_counted(self, scanner, tmp_path):
        _write(tmp_path, "app.py", '"""App."""\nx = 1\n')
        _write(tmp_path, "test_app.py", '"""Tests."""\ndef test_x(): pass\n')
        snap = scanner.scan(str(tmp_path))
        assert snap.test_ratio > 0.0

    def test_ratio_capped_at_1(self, scanner, tmp_path):
        _write(tmp_path, "test_a.py", '"""Tests."""\ndef test_x(): pass\n')
        snap = scanner.scan(str(tmp_path))
        assert snap.test_ratio <= 1.0
