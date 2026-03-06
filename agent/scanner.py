"""
Repository Scanner
------------------
Perceives the repository environment by collecting structured snapshots of:
  - File inventory (paths, sizes, last-modified)
  - Documentation coverage
  - Dependency manifests
  - Code smell indicators (TODOs, long functions, missing type hints)
  - Test coverage hints (ratio of test files to source files)
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import toml


@dataclass
class FileInfo:
    path: str
    size_bytes: int
    last_modified: datetime
    language: str


@dataclass
class DocGap:
    file_path: str
    kind: str          # "missing_module_docstring" | "missing_function_docstring" | "missing_readme"
    symbol: str        # function/class name, or "" for module-level
    line: int


@dataclass
class CodeSmell:
    file_path: str
    kind: str          # "todo" | "fixme" | "long_function" | "missing_type_hints"
    description: str
    line: int


@dataclass
class DependencyManifest:
    file_path: str
    manager: str       # "pip" | "npm" | "cargo" | "go"
    raw_content: str


@dataclass
class RepositorySnapshot:
    root: str
    scanned_at: datetime
    files: list[FileInfo] = field(default_factory=list)
    doc_gaps: list[DocGap] = field(default_factory=list)
    code_smells: list[CodeSmell] = field(default_factory=list)
    dep_manifests: list[DependencyManifest] = field(default_factory=list)
    test_ratio: float = 0.0      # test files / total source files
    summary: dict[str, Any] = field(default_factory=dict)


class RepositoryScanner:
    """
    Walks a repository and produces a RepositorySnapshot — the agent's
    perception of the codebase's current health state.
    """

    LANGUAGE_MAP = {
        ".py": "python", ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript", ".go": "go",
        ".rs": "rust", ".java": "java", ".rb": "ruby",
        ".md": "markdown", ".rst": "rst",
    }

    TODO_PATTERN = re.compile(r"#\s*(TODO|FIXME|HACK|XXX|BUG|NOTE)\b[:\s]*(.*)", re.IGNORECASE)
    FEATURE_PATTERN = re.compile(r"#\s*(FEATURE|MISSING)\b[:\s]*(.*)", re.IGNORECASE)

    def __init__(self, config: dict):
        self.include_exts: set[str] = set(config["scanner"].get("include_extensions", []))
        self.exclude_dirs: set[str] = set(config["scanner"].get("exclude_dirs", []))
        self.max_file_size: int = config["scanner"].get("max_file_size_kb", 500) * 1024

    def scan(self, repo_root: str) -> RepositorySnapshot:
        root = Path(repo_root).resolve()
        snapshot = RepositorySnapshot(root=str(root), scanned_at=datetime.utcnow())

        source_count = 0
        test_count = 0

        for file_path in self._walk(root):
            rel = str(file_path.relative_to(root))
            ext = file_path.suffix.lower()
            lang = self.LANGUAGE_MAP.get(ext, "unknown")
            size = file_path.stat().st_size
            mtime = datetime.fromtimestamp(file_path.stat().st_mtime)

            snapshot.files.append(FileInfo(path=rel, size_bytes=size, last_modified=mtime, language=lang))

            if size > self.max_file_size:
                continue

            content = self._read(file_path)
            if content is None:
                continue

            if lang == "python":
                source_count += 1
                if "test" in file_path.name.lower() or "test" in file_path.parts:
                    test_count += 1
                snapshot.doc_gaps.extend(self._python_doc_gaps(rel, content))
                snapshot.code_smells.extend(self._python_smells(rel, content))
            elif lang in ("typescript", "javascript"):
                source_count += 1
                if "test" in file_path.name.lower() or "spec" in file_path.name.lower():
                    test_count += 1

            # Generic TODO scan for all text files
            snapshot.code_smells.extend(self._todo_scan(rel, content))
            snapshot.code_smells.extend(self._feature_scan(rel, content))

            # Dependency manifests
            manifest = self._detect_manifest(rel, content)
            if manifest:
                snapshot.dep_manifests.append(manifest)

        # Check for missing top-level README
        if not (root / "README.md").exists() and not (root / "README.rst").exists():
            snapshot.doc_gaps.append(DocGap(
                file_path="README.md",
                kind="missing_readme",
                symbol="",
                line=0,
            ))

        snapshot.test_ratio = (test_count / source_count) if source_count > 0 else 0.0
        snapshot.summary = {
            "total_files": len(snapshot.files),
            "doc_gaps": len(snapshot.doc_gaps),
            "code_smells": len(snapshot.code_smells),
            "dep_manifests": len(snapshot.dep_manifests),
            "test_ratio": round(snapshot.test_ratio, 2),
        }
        return snapshot

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _walk(self, root: Path):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in self.exclude_dirs and not d.startswith(".")
            ]
            for fname in filenames:
                fpath = Path(dirpath) / fname
                if fpath.suffix.lower() in self.include_exts:
                    yield fpath

    def _read(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

    def _python_doc_gaps(self, rel_path: str, content: str) -> list[DocGap]:
        gaps: list[DocGap] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return gaps

        # Module-level docstring
        if not (tree.body and isinstance(tree.body[0], ast.Expr) and
                isinstance(tree.body[0].value, ast.Constant)):
            gaps.append(DocGap(file_path=rel_path, kind="missing_module_docstring", symbol="", line=1))

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not (node.body and isinstance(node.body[0], ast.Expr) and
                        isinstance(node.body[0].value, ast.Constant)):
                    gaps.append(DocGap(
                        file_path=rel_path,
                        kind=f"missing_{'class' if isinstance(node, ast.ClassDef) else 'function'}_docstring",
                        symbol=node.name,
                        line=node.lineno,
                    ))
        return gaps

    def _python_smells(self, rel_path: str, content: str) -> list[CodeSmell]:
        smells: list[CodeSmell] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return smells

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", node.lineno)
                length = end - node.lineno
                if length > 60:
                    smells.append(CodeSmell(
                        file_path=rel_path,
                        kind="long_function",
                        description=f"Function '{node.name}' is {length} lines long (>60)",
                        line=node.lineno,
                    ))

                # Missing return type annotation
                if node.returns is None and node.name not in ("__init__", "__new__"):
                    smells.append(CodeSmell(
                        file_path=rel_path,
                        kind="missing_type_hints",
                        description=f"Function '{node.name}' has no return type annotation",
                        line=node.lineno,
                    ))
        return smells

    def _todo_scan(self, rel_path: str, content: str) -> list[CodeSmell]:
        smells: list[CodeSmell] = []
        for lineno, line in enumerate(content.splitlines(), 1):
            m = self.TODO_PATTERN.search(line)
            if m:
                smells.append(CodeSmell(
                    file_path=rel_path,
                    kind="todo",
                    description=f"{m.group(1).upper()}: {m.group(2).strip()}",
                    line=lineno,
                ))
        return smells

    def _feature_scan(self, rel_path: str, content: str) -> list[CodeSmell]:
        smells: list[CodeSmell] = []
        for lineno, line in enumerate(content.splitlines(), 1):
            m = self.FEATURE_PATTERN.search(line)
            if m:
                smells.append(CodeSmell(
                    file_path=rel_path,
                    kind="missing_feature",
                    description=f"{m.group(1).upper()}: {m.group(2).strip()}",
                    line=lineno,
                ))
        return smells

    def _detect_manifest(self, rel_path: str, content: str) -> DependencyManifest | None:
        fname = Path(rel_path).name
        if fname == "requirements.txt":
            return DependencyManifest(rel_path, "pip", content)
        if fname == "package.json":
            return DependencyManifest(rel_path, "npm", content)
        if fname == "Cargo.toml":
            return DependencyManifest(rel_path, "cargo", content)
        if fname == "go.mod":
            return DependencyManifest(rel_path, "go", content)
        if fname in ("pyproject.toml", "setup.cfg", "setup.py"):
            return DependencyManifest(rel_path, "pip", content)
        return None
