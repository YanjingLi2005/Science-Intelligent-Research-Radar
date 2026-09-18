"""Run the deterministic consistency checks over a real manuscript.

Reads the manuscript text from a local tenant database (default: the
single-user data/research_radar.db) or from a plain text/markdown file, then
prints every finding with its surrounding quote so precision can be judged by
eye before any of this is wired into the product.

    python scripts/try_consistency_check.py
    python scripts/try_consistency_check.py --db data/users/<name>/research_radar.db
    python scripts/try_consistency_check.py --file paper.md
    python scripts/try_consistency_check.py --dump-measurements
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.services.consistency_service import (  # noqa: E402
    check_manuscript,
    extract_measurements,
)


def load_from_db(db_path: Path) -> tuple[str, str]:
    if not db_path.exists():
        raise SystemExit(f"数据库不存在：{db_path}")
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    row = connection.execute(
        "SELECT file_name, content_text FROM manuscript_versions "
        "ORDER BY is_current DESC, version_no DESC LIMIT 1"
    ).fetchone()
    connection.close()
    if row is None:
        raise SystemExit(f"{db_path} 里没有手稿。")
    return row[0], row[1] or ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/research_radar.db")
    parser.add_argument("--file", default=None)
    parser.add_argument("--dump-measurements", action="store_true")
    args = parser.parse_args()

    if args.file:
        path = Path(args.file)
        name, content = path.name, path.read_text(encoding="utf-8")
    else:
        name, content = load_from_db(Path(args.db))

    print(f"手稿：{name}（{len(content)} 字符）\n")

    measurements = extract_measurements(content)
    print(f"抽取到 {len(measurements)} 个疑似结果数值")
    if args.dump_measurements:
        for item in measurements:
            print(f"  {item.metric:12s} {item.value:>8g}{item.unit:<2s} [{item.section}]")
    print()

    result = check_manuscript(content)
    print(f"发现 {result['finding_count']} 处疑似前后不一致\n")
    for index, finding in enumerate(result["findings"], start=1):
        print(f"[{index}] {finding['severity'].upper()} · {finding['metric']}")
        print(f"    {finding['summary']}")
        for occurrence in finding["occurrences"]:
            print(
                f"      - {occurrence['value']:g}{occurrence['unit']} "
                f"@ {occurrence['section']} (offset {occurrence['start_offset']})"
            )
            print(f"        …{occurrence['quote'][:150]}…")
        print()


if __name__ == "__main__":
    main()
