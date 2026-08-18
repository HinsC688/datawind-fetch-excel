#!/usr/bin/env python3
"""从一个或多个 DataWind 抓取结果里提取并合并 KYC 触达明细数据。

背景：DataWind 单次查询有 1000 行上限，无法一次抓全五档 reg_range，
所以做法是在同一个监听窗口内逐个切换 reg_range 筛选，产生多个"窄范围但完整"的请求，
再用本脚本按唯一键合并成一份完整数据。

只采纳满足以下条件的请求（避免用到截断或周期错误的数据）：
  - 有 p_date between 绝对日期筛选，且范围匹配 --start / --end
  - 返回行数未触及 limit（未被截断）
  - 维度里不含 p_date（保证是整周期聚合，不是按天拆）

用法：
    python3 scripts/extract-kyc-rows.py \
        artifacts/20260818T162744-response-bodies.json \
        artifacts/{补抓的时间戳}-response-bodies.json \
        --start 2026-08-12 --end 2026-08-17 \
        --out artifacts/kyc-0812-0817-rows.json
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

QUERY_MARKER = "vizQuery/query"
TARGET_TOOLS = {"push", "邮件", "弹窗"}

# 唯一键：一条业务记录由"渠道+触达详情+注册距今范围+用户阶段+是否kyc"共同确定
KEY_FIELDS = ("触达工具", "触达详情", "reg_range", "uj_type", "is_kyc_show")


def iter_queries(paths):
    for path in paths:
        bodies = json.loads(Path(path).read_text(encoding="utf-8"))
        for index, item in enumerate(bodies, start=1):
            if QUERY_MARKER in (item.get("url") or ""):
                yield Path(path).name, index, item


def parse_request(post_data):
    info = {"limit": None, "report_id": None, "has_p_date_dim": False, "date_range": None}
    try:
        parsed = json.loads(post_data)
    except (json.JSONDecodeError, TypeError):
        return info
    info["report_id"] = parsed.get("reportId")
    query = parsed.get("query") or {}
    info["limit"] = query.get("limit")

    names_by_id = {str(item.get("id")): item.get("name") for item in query.get("dimMetList") or []}
    location_dims = ((query.get("locations") or {}).get("dimensions")) or []
    dim_names = []
    for entry in location_dims:
        key = str(entry.get("id") if isinstance(entry, dict) else entry)
        dim_names.append(names_by_id.get(key, key))
    info["has_p_date_dim"] = any((name or "").strip() == "p_date" for name in dim_names)

    for where in query.get("whereList") or []:
        if where.get("name") == "p_date" and where.get("op") == "between":
            info["date_range"] = where.get("val")
    return info


def parse_rows(body):
    if not isinstance(body, dict):
        return []
    viz = (body.get("data") or {}).get("vizData") or {}
    alias = viz.get("aliasMap") or {}
    rows = []
    for row in viz.get("datasets") or []:
        if isinstance(row, dict):
            rows.append({alias.get(key, key): value for key, value in row.items()})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("response_bodies", nargs="+")
    parser.add_argument("--start", required=True, help="期望的周期开始日期，如 2026-08-12")
    parser.add_argument("--end", required=True, help="期望的周期结束日期，如 2026-08-17")
    parser.add_argument("--out", type=Path, default=None, help="输出合并结果的JSON路径")
    parser.add_argument("--keep-all-tools", action="store_true",
                        help="保留所有触达工具（默认只保留 push/邮件/弹窗，排除金刚区等）")
    args = parser.parse_args()

    accepted, rejected = [], []
    for source, index, query in iter_queries(args.response_bodies):
        req = parse_request(query.get("post_data"))
        rows = parse_rows(query.get("body"))
        label = f"{source}#{index}"

        date_range = req["date_range"]
        date_ok = bool(date_range) and len(date_range) == 2 \
            and args.start in str(date_range[0]) and args.end in str(date_range[1])
        truncated = isinstance(req["limit"], int) and len(rows) >= req["limit"]

        reason = None
        if not rows:
            reason = "无数据行"
        elif not date_ok:
            reason = f"日期范围不匹配({date_range})"
        elif req["has_p_date_dim"]:
            reason = "维度含p_date(按天拆分)"
        elif truncated:
            reason = f"被limit={req['limit']}截断({len(rows)}行)"

        if reason:
            rejected.append((label, reason, len(rows)))
        else:
            accepted.append((label, rows))

    print("=== 请求采纳情况 ===")
    for label, rows in accepted:
        print(f"  ✅ {label}: 采纳 {len(rows)} 行")
    for label, reason, count in rejected:
        print(f"  ⏭️  {label}: 跳过（{reason}）")

    if not accepted:
        print("\n❌ 没有任何可用请求，无法提取数据。")
        return

    merged = {}
    provenance = {}
    for label, rows in accepted:
        for row in rows:
            key = tuple(row.get(field) for field in KEY_FIELDS)
            # 同一条记录如果在多个请求里都出现，保留第一次（都是完整请求，数值应一致）
            if key not in merged:
                merged[key] = row
                provenance[key] = label

    print(f"\n合并后唯一记录数: {len(merged)}")

    if not args.keep_all_tools:
        before = len(merged)
        merged = {key: row for key, row in merged.items() if key[0] in TARGET_TOOLS}
        print(f"过滤触达工具（只保留 {sorted(TARGET_TOOLS)}）: {before} → {len(merged)}")

    tools = Counter(key[0] for key in merged)
    print(f"触达工具分布: {dict(tools)}")

    by_tool_reg = defaultdict(Counter)
    for key in merged:
        by_tool_reg[key[0]][key[2]] += 1
    all_regs = ["注册当天", "注册1-7天", "注册8-14天", "注册15-30天", "注册30天以上"]
    extra_regs = sorted({key[2] for key in merged} - set(all_regs), key=lambda x: str(x))
    columns = all_regs + extra_regs

    print("\n=== 渠道 × reg_range 覆盖矩阵 ===")
    print(f"{'渠道':<8}" + "".join(f"{str(reg):>13}" for reg in columns))
    for tool in sorted(by_tool_reg, key=lambda x: str(x)):
        print(f"{str(tool):<8}" + "".join(f"{by_tool_reg[tool].get(reg, 0):>13}" for reg in columns))

    missing = []
    for tool in TARGET_TOOLS:
        for reg in all_regs:
            if by_tool_reg.get(tool, {}).get(reg, 0) == 0:
                missing.append(f"{tool}/{reg}")
    if missing:
        print(f"\n⚠️  以下 渠道/reg_range 组合没有数据: {missing}")
        print("    可能是本周确实没有该组合的触达，也可能是抓取没覆盖到，建议跟界面核对。")
    else:
        print("\n✅ 三个渠道 × 五档 reg_range 全部有数据。")

    if args.out:
        payload = {
            "period": {"start": args.start, "end": args.end},
            "sources": args.response_bodies,
            "accepted_requests": [label for label, _ in accepted],
            "row_count": len(merged),
            "rows": list(merged.values()),
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写出合并结果: {args.out}")


if __name__ == "__main__":
    main()
