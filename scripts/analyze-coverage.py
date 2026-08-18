#!/usr/bin/env python3
"""分析一次抓取里各个 vizQuery 请求的覆盖范围，判断能否拼出完整数据。

因为 DataWind 单次查询有 limit（通常1000行）上限，一次全量抓取容易被截断。
但用户在界面上改筛选会产生多个请求，不同请求的筛选范围不同，
其中一些窄范围请求可能是"完整未截断"的，可以拼接起来覆盖全量。

用法：
    python3 scripts/analyze-coverage.py artifacts/{时间戳}-response-bodies.json
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

QUERY_MARKER = "vizQuery/query"
TARGET_TOOLS = {"push", "邮件", "弹窗"}


def load_queries(path):
    bodies = json.loads(path.read_text(encoding="utf-8"))
    return [item for item in bodies if QUERY_MARKER in (item.get("url") or "")]


def parse_filters(post_data):
    try:
        parsed = json.loads(post_data)
    except (json.JSONDecodeError, TypeError):
        return {}, None, None
    query = parsed.get("query") or {}
    filters = {}
    for where in query.get("whereList") or []:
        name = where.get("name")
        filters.setdefault(name, []).append({"op": where.get("op"), "val": where.get("val")})
    return filters, query.get("limit"), parsed.get("reportId")


def parse_rows(body):
    if not isinstance(body, dict):
        return [], {}, {}
    data = body.get("data") or {}
    viz = data.get("vizData") or {}
    alias = viz.get("aliasMap") or {}
    field_map = viz.get("fieldMap") or {}
    rows = [row for row in (viz.get("datasets") or []) if isinstance(row, dict)]
    named = []
    for row in rows:
        named.append({alias.get(key, key): value for key, value in row.items()})
    return named, alias, field_map


def describe(filters, key):
    entries = filters.get(key) or []
    parts = []
    for entry in entries:
        val = entry["val"]
        parts.append(f"{entry['op']}={json.dumps(val, ensure_ascii=False)}")
    return "; ".join(parts) if parts else "-"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("response_bodies", type=Path)
    args = parser.parse_args()

    queries = load_queries(args.response_bodies)
    print(f"共 {len(queries)} 个 vizQuery 请求\n")

    records = []
    for index, query in enumerate(queries, start=1):
        filters, limit, report_id = parse_filters(query.get("post_data"))
        rows, alias, field_map = parse_rows(query.get("body"))
        truncated = isinstance(limit, int) and len(rows) >= limit
        date_desc = describe(filters, "p_date")
        records.append({
            "index": index, "report_id": report_id, "limit": limit, "rows": rows,
            "n": len(rows), "truncated": truncated, "filters": filters, "date": date_desc,
        })

    print("=== 各请求筛选范围对照 ===")
    for rec in records:
        flag = "⚠️截断" if rec["truncated"] else "✅完整"
        print(f"\n#{rec['index']}  reportId={rec['report_id']}  行数={rec['n']}  {flag}")
        print(f"   p_date      : {rec['date']}")
        print(f"   reg_range   : {describe(rec['filters'], 'reg_range')}")
        print(f"   function_type: {describe(rec['filters'], 'function_type')}")
        print(f"   uj_type     : {describe(rec['filters'], 'uj_type')}")
        print(f"   is_kyc_show : {describe(rec['filters'], 'is_kyc_show')}")
        if rec["rows"]:
            tools = Counter(row.get("触达工具") for row in rec["rows"])
            regs = Counter(row.get("reg_range") for row in rec["rows"])
            print(f"   触达工具分布 : {dict(tools)}")
            print(f"   reg_range分布: {dict(regs)}")

    # 只保留目标周期（0812-0817 绝对日期）且未截断的请求，尝试拼接
    print("\n" + "=" * 70)
    print("=== 拼接可行性分析（仅用 between 0812-0817 且未截断的请求）===")
    usable = [rec for rec in records
              if "between" in rec["date"] and "2026-08-12" in rec["date"] and not rec["truncated"] and rec["n"] > 0]
    print(f"可用请求: {[rec['index'] for rec in usable]}")

    if not usable:
        print("❌ 没有可用的完整请求。")
        return

    # 以 (触达工具, 触达详情, reg_range, uj_type, is_kyc_show) 为唯一键去重合并
    merged = {}
    source_of = {}
    for rec in usable:
        for row in rec["rows"]:
            key = (row.get("触达工具"), row.get("触达详情"), row.get("reg_range"),
                   row.get("uj_type"), row.get("is_kyc_show"))
            if key not in merged:
                merged[key] = row
                source_of[key] = rec["index"]
    print(f"合并后唯一记录数: {len(merged)}")

    tools = Counter(key[0] for key in merged)
    print(f"触达工具分布: {dict(tools)}")
    target = {key: row for key, row in merged.items() if key[0] in TARGET_TOOLS}
    print(f"其中目标渠道(push/邮件/弹窗)记录数: {len(target)}")

    by_tool_reg = defaultdict(Counter)
    for key in target:
        by_tool_reg[key[0]][key[2]] += 1
    print("\n目标渠道 × reg_range 覆盖矩阵:")
    all_regs = sorted({key[2] for key in target}, key=lambda x: str(x))
    print(f"{'渠道':<8}" + "".join(f"{str(reg):>12}" for reg in all_regs))
    for tool in sorted(by_tool_reg, key=lambda x: str(x)):
        print(f"{str(tool):<8}" + "".join(f"{by_tool_reg[tool].get(reg, 0):>12}" for reg in all_regs))

    missing = [reg for reg in all_regs if any(by_tool_reg[tool].get(reg, 0) == 0 for tool in by_tool_reg)]
    if missing:
        print(f"\n⚠️  以下 reg_range 在部分渠道下没有数据（可能是真的没有，也可能是抓取没覆盖）: {missing}")

    expected_regs = {"注册当天", "注册1-7天", "注册8-14天", "注册15-30天", "注册30天以上"}
    covered_regs = {key[2] for key in target}
    not_covered = expected_regs - covered_regs
    if not_covered:
        print(f"\n❌ 完全没有覆盖到的 reg_range: {sorted(not_covered)}")
        print("   → 需要在 DataWind 界面按这些 reg_range 单独筛选后补抓一次。")
    else:
        print("\n✅ 五档 reg_range 全部有数据覆盖。")


if __name__ == "__main__":
    main()
