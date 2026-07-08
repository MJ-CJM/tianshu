#!/usr/bin/env python3
"""成本基线测算 —— 从成本账本推算典型月成本区间(宣发前跑一周实测填 README)。

宣发拍板(spec §七 #7):卖成本治理的平台必须敢报自己的成本。用法:
真实使用天枢一周(别空跑),然后:

    python scripts/cost_baseline.py            # 读默认 DB
    python scripts/cost_baseline.py --db ~/.tianshu/tianshu.db --days 7

输出:实测区间 + 外推的典型月成本区间(P25–P75 日成本 × 30),供填 README。
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def _daily_costs(conn: sqlite3.Connection, days: int) -> list[float]:
    rows = conn.execute(
        """
        SELECT date(created_at) AS day, COALESCE(SUM(cost_cny), 0.0) AS cost
        FROM cost_ledger
        WHERE date(created_at) >= date('now', ?)
        GROUP BY day ORDER BY day
        """,
        (f"-{days} days",),
    ).fetchall()
    return [float(r["cost"]) for r in rows]


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="~/.tianshu/tianshu.db")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    db = Path(args.db).expanduser()
    if not db.exists():
        print(f"✗ DB not found: {db}")
        raise SystemExit(1)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        daily = _daily_costs(conn, args.days)
    finally:
        conn.close()

    if not daily:
        print(
            f"⚠ 近 {args.days} 天无成本记录。请真实使用天枢一段时间后再测算(空账本报不出可信区间)。"
        )
        raise SystemExit(0)

    total = sum(daily)
    active_days = len(daily)
    p25, p50, p75 = _pct(daily, 0.25), _pct(daily, 0.5), _pct(daily, 0.75)

    print(f"=== 成本基线(近 {args.days} 天,有记录 {active_days} 天)===")
    print(f"总成本:        ¥{total:.2f}")
    print(f"日成本 P25/中位/P75: ¥{p25:.2f} / ¥{p50:.2f} / ¥{p75:.2f}")
    print()
    print("典型月成本区间(P25–P75 日成本 × 30,供填 README):")
    print(f"  ¥{p25 * 30:.0f} – ¥{p75 * 30:.0f} / 月")
    print()
    print("⚠ 样本越小越不可信;建议真实使用 ≥7 天、覆盖典型工作负载后再定稿。")


if __name__ == "__main__":
    main()
