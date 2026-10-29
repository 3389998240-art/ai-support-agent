"""语句覆盖率（标准库实现，不依赖 pytest-cov）。

为什么自己写：环境装不上 pytest-cov，而覆盖率是"知道盲区在哪"最便宜的手段。
口径是**语句覆盖**（ast.stmt 的行号是否被执行过），不是分支覆盖，数值会略高于
真正的分支覆盖，用来看趋势和找"整块没测到"的文件足够。

用法：
    cd backend && PYTHONPATH=. python scripts/coverage.py
"""

from __future__ import annotations

import ast
import sys
import trace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]          # backend/
APP = ROOT / "app"


def statement_lines(path: Path) -> set[int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    return {node.lineno for node in ast.walk(tree) if isinstance(node, ast.stmt)}


def main() -> None:
    sys.path.insert(0, str(ROOT))

    tracer = trace.Trace(count=1, trace=0, ignoredirs=[sys.prefix, sys.exec_prefix])
    exit_code = tracer.runfunc(pytest.main, ["-q", "tests"])
    counts = tracer.results().counts

    covered: dict[str, int] = {}
    for (filename, lineno), hits in counts.items():
        if hits <= 0:
            continue
        path = Path(filename).resolve()
        try:
            path.relative_to(APP)
        except ValueError:
            continue
        covered[str(path)] = covered.get(str(path), 0) + 1

    rows: list[tuple[str, int, int, float]] = []
    total_stmts = total_hit = 0

    for file_path in sorted(APP.rglob("*.py")):
        lines = statement_lines(file_path)
        if not lines:
            continue
        hit = min(len(lines), covered.get(str(file_path.resolve()), 0))
        total_stmts += len(lines)
        total_hit += hit
        rows.append(
            (str(file_path.relative_to(ROOT)), len(lines), hit, 100.0 * hit / len(lines))
        )

    print("=" * 72)
    print(f"{'file':<46}{'stmts':>7}{'hit':>7}{'cover':>8}")
    print("-" * 72)
    for name, stmts, hit, ratio in rows:
        flag = "  " if ratio >= 80 else ("! " if ratio >= 50 else "!!")
        print(f"{flag}{name:<44}{stmts:>7}{hit:>7}{ratio:>7.1f}%")
    print("-" * 72)
    overall = 100.0 * total_hit / total_stmts if total_stmts else 0.0
    print(f"{'TOTAL':<46}{total_stmts:>7}{total_hit:>7}{overall:>7.1f}%")
    print(f"pytest exit={exit_code}")
    print("=" * 72)


if __name__ == "__main__":
    main()
