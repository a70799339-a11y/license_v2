#!/usr/bin/env python3
"""T7 CI 门禁：新增代码禁用 datetime.utcnow（L4 时区口径）。

用法：
  python3 ci/check_utcnow.py                    # 校验（CI 调用；出现基线外调用即退出 1）
  python3 ci/check_utcnow.py --update-baseline  # 存量消除后人工确认并刷新基线

基线 ci/utcnow_baseline.txt 冻结存量调用的内容指纹（relpath<TAB>sha1(调用行文本)）：
只识别真实调用行（utcnow 紧跟 ASCII 左括号，且非纯注释行），注释/文档不误伤；
改行文本=新指纹=拒绝，因此「删一处加一处」也拦得住。
"""
import hashlib
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "utcnow_baseline.txt"
SCAN_DIRS = ("backend", "tests", "tools", "ci")
SKIP_PARTS = {".git", "node_modules", ".venv", "venv", "__pycache__",
              "target", "dist", "ui-shots", "release"}
CALL_RE = re.compile(r"utcnow\s*\(")


def iter_py_files():
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            if SKIP_PARTS.intersection(p.parts):
                continue
            yield p


def collect():
    """返回 [(relpath, sha1(去空白行文本), 行文本)] 调用清单"""
    hits = []
    for p in iter_py_files():
        rel = p.relative_to(ROOT).as_posix()
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or not CALL_RE.search(line):
                continue
            digest = hashlib.sha1(s.encode("utf-8")).hexdigest()
            hits.append((rel, digest, s))
    return hits


def load_baseline() -> Counter:
    entries = Counter()
    if BASELINE.exists():
        for line in BASELINE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rel, digest = line.split("\t")[:2]
            entries[(rel, digest)] += 1
    return entries


def main(argv) -> int:
    update = "--update-baseline" in argv
    hits = collect()
    current = Counter((rel, digest) for rel, digest, _ in hits)

    if update:
        lines = [
            "# utcnow 存量基线（内容指纹：relpath<TAB>sha1(调用行文本)）",
            "# 由 ci/check_utcnow.py --update-baseline 生成；基线外的调用会被门禁拒绝。",
        ]
        for rel, digest, _ in sorted(hits):
            lines.append(f"{rel}\t{digest}")
        BASELINE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"基线已写入 {BASELINE.relative_to(ROOT)}（{len(hits)} 处存量）")
        return 0

    baseline = load_baseline()
    fresh = current - baseline
    if fresh:
        print("✗ utcnow 门禁失败：检测到基线外的调用（L4：新代码禁用该接口，请用 timezone-aware 时间 API）")
        shown = set()
        for rel, digest, text in hits:
            key = (rel, digest)
            if key in fresh and key not in shown:
                shown.add(key)
                print(f"  [新增] {rel}: {text[:120]}")
        print("  （误报或确属改动后合法重排：人工确认后跑 --update-baseline 并提交基线）")
        return 1

    shrunk = baseline - current
    total = sum(baseline.values())
    if shrunk:
        print(f"✓ utcnow 门禁通过（存量 {sum(current.values())}/{total} 处；"
              f"{sum(shrunk.values())} 处已消除，可跑 --update-baseline 收紧基线）")
    else:
        print(f"✓ utcnow 门禁通过（存量 {sum(current.values())} 处，无新增）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
