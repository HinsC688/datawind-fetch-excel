#!/usr/bin/env python3
"""校验并摘要一次 DataWind 抓取结果。

用法：
    python3 scripts/check-capture.py artifacts/{时间戳}-response-bodies.json

为什么需要它（对应 SOP / HANDOFF 的几条硬性检查）：
1. 有没有真的抓到 vizQuery/query 数据请求（只有埋点请求 = 白抓，见"抓取失败记录"）
2. 筛选是绝对时间 between 还是相对时间 lastSync（坑#4）
3. 返回是否被 limit 截断（对比 total / atLeast / row_cnt 与 limit，坑#5/#13）
4. 维度里还有没有 p_date（本次需求要求取消 p_date，否则粒度是"每天一行"，跟飞书表对不上）
5. 打印 uniqueId → 中文名 的别名表，后续写入脚本必须按名称动态反查ID，不能硬编码（坑#7）

用户每改一次筛选都会触发一次查询，所以一次抓取里通常有多个 vizQuery 请求。
本脚本按抓取顺序编号列出每个请求的特征，最后给出"该用哪个"的建议。
"""
import argparse
import json
from collections import Counter
from pathlib import Path

QUERY_MARKER = "vizQuery/query"


def summarize_request(post_data):
    """从请求体里提取维度、筛选、limit 等关键特征。"""
    info = {"dimensions": [], "filters": [], "limit": None, "report_id": None, "has_p_date_dim": False}
    if not post_data:
        return info
    try:
        parsed = json.loads(post_data)
    except (json.JSONDecodeError, TypeError):
        return info

    info["report_id"] = parsed.get("reportId")
    query = parsed.get("query") or {}
    info["limit"] = query.get("limit")

    # dimMetList 里 roleType==0 的是维度，roleType==1 的是指标
    dim_names_by_id = {}
    for item in query.get("dimMetList") or []:
        dim_names_by_id[str(item.get("id"))] = item.get("name")
        if item.get("roleType") == 0:
            info["dimensions"].append(item.get("name"))

    # locations.dimensions 才是真正摆在"维度"区的字段（更准确）
    location_dims = ((query.get("locations") or {}).get("dimensions")) or []
    if location_dims:
        resolved = []
        for entry in location_dims:
            key = str(entry.get("id") if isinstance(entry, dict) else entry)
            resolved.append(dim_names_by_id.get(key, key))
        info["dimensions"] = resolved

    info["has_p_date_dim"] = any((name or "").strip() == "p_date" for name in info["dimensions"])

    for where in query.get("whereList") or []:
        info["filters"].append({
            "name": where.get("name"),
            "op": where.get("op"),
            "val": where.get("val"),
        })
    return info


def summarize_response(body):
    """从响应体里提取行数、截断信息、别名表、数据行。"""
    info = {"total": None, "at_least": None, "row_cnt": None, "alias_map": {},
            "field_map": {}, "rows": [], "is_partial": None, "code": None}
    if not isinstance(body, dict):
        return info
    info["code"] = body.get("code")
    data = body.get("data") or {}
    info["total"] = data.get("total")
    info["at_least"] = data.get("atLeast")
    info["row_cnt"] = (data.get("statistics") or {}).get("row_cnt")
    viz = data.get("vizData") or {}
    info["alias_map"] = viz.get("aliasMap") or {}
    info["field_map"] = viz.get("fieldMap") or {}
    info["rows"] = viz.get("datasets") or []
    info["is_partial"] = (viz.get("statistics") or {}).get("isPartialData")
    return info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("response_bodies", type=Path)
    parser.add_argument("--show-rows", type=int, default=2, help="每个请求打印几行数据样本")
    parser.add_argument("--show-alias", action="store_true", help="打印完整别名表（字段很多，默认只打印维度字段）")
    args = parser.parse_args()

    bodies = json.loads(args.response_bodies.read_text(encoding="utf-8"))
    print(f"响应体总数: {len(bodies)}")

    url_counter = Counter((item.get("url") or "").split("?")[0] for item in bodies)
    print("\n=== 抓到的接口 ===")
    for url, count in url_counter.most_common():
        print(f"  {count:3d}  {url}")

    queries = [item for item in bodies if QUERY_MARKER in (item.get("url") or "")]
    if not queries:
        print(f"\n❌ 无效抓取：没有任何包含 '{QUERY_MARKER}' 的请求。")
        print("   重抓要点：先启动监听脚本，再去浏览器触发查询（改筛选 / 取消维度 / Cmd+R 硬刷新）。")
        return

    print(f"\n✅ 抓到 {len(queries)} 个 {QUERY_MARKER} 请求\n")

    candidates = []
    for index, query in enumerate(queries, start=1):
        req = summarize_request(query.get("post_data"))
        res = summarize_response(query.get("body"))
        print("=" * 70)
        print(f"请求 #{index}   reportId={req['report_id']}   limit={req['limit']}   code={res['code']}")
        print(f"  维度({len(req['dimensions'])}): {req['dimensions']}")
        print(f"  含 p_date 维度: {'⚠️  是（需要在界面取消）' if req['has_p_date_dim'] else '✅ 否'}")

        if req["filters"]:
            print("  筛选条件:")
            for flt in req["filters"]:
                val = flt["val"]
                val_text = json.dumps(val, ensure_ascii=False)
                if len(val_text) > 120:
                    val_text = val_text[:120] + f"...(共{len(val) if hasattr(val, '__len__') else '?'}项)"
                flag = ""
                if flt["op"] == "lastSync":
                    flag = "  ⚠️ 相对时间，周期会漂移（坑#4）"
                elif flt["op"] == "between":
                    flag = "  ✅ 绝对时间"
                print(f"    - {flt['name']} {flt['op']} {val_text}{flag}")
        else:
            print("  筛选条件: 无")

        rows = res["rows"]
        print(f"  行数: datasets={len(rows)}  total={res['total']}  atLeast={res['at_least']}  row_cnt={res['row_cnt']}")
        if res["is_partial"]:
            print("    ⚠️  isPartialData=True，返回是部分数据")
        limit = req["limit"]
        biggest = max([value for value in (res["total"], res["at_least"], res["row_cnt"]) if isinstance(value, int)] or [0])
        if isinstance(limit, int) and biggest >= limit:
            print(f"    ⚠️  疑似被 limit={limit} 截断（真实总量至少 {biggest}），见坑#5/#13")
        elif isinstance(limit, int):
            print(f"    ✅ 未触及 limit={limit}")

        # 维度字段的别名（用于本地按渠道拆分）
        dim_fields = {uid: meta.get("alias") for uid, meta in res["field_map"].items()
                      if meta.get("location") == "dimensions"}
        if dim_fields:
            print(f"  维度字段 uniqueId → 名称:")
            for uid, alias in dim_fields.items():
                print(f"    {uid} -> {alias!r}")

        if args.show_alias and res["alias_map"]:
            print(f"  完整别名表({len(res['alias_map'])}项):")
            for uid, alias in res["alias_map"].items():
                print(f"    {uid} -> {alias!r}")

        dict_rows = [row for row in rows if isinstance(row, dict)]
        if rows and not dict_rows:
            print(f"  ⚠️  datasets 有 {len(rows)} 项但不是字典结构（可能是空结果或另一种图表格式），跳过样本")
        if dict_rows:
            print(f"  前 {args.show_rows} 行样本（uniqueId为键）:")
            for row in dict_rows[: args.show_rows]:
                readable = {res["alias_map"].get(key, key): value for key, value in row.items()}
                print(f"    {json.dumps(readable, ensure_ascii=False)[:600]}")

            # 渠道分布：找出可能表示触达渠道的维度字段
            for uid, alias in dim_fields.items():
                values = [row.get(uid) for row in dict_rows if uid in row]
                distinct = Counter(value for value in values if value not in (None, "", " "))
                if 1 <= len(distinct) <= 12:
                    print(f"  维度 {alias!r} 取值分布: {dict(distinct)}")

        date_filters = [flt for flt in req["filters"] if (flt["name"] or "") == "p_date"]
        has_between_date = any(flt["op"] == "between" for flt in date_filters)
        has_relative_date = any(flt["op"] in {"lastSync", "last"} for flt in date_filters)
        candidates.append({
            "index": index,
            "report_id": req["report_id"],
            "rows": len(dict_rows),
            "has_p_date": req["has_p_date_dim"],
            "dimensions": req["dimensions"],
            "filters": req["filters"],
            "truncated": isinstance(limit, int) and biggest >= limit,
            "has_between_date": has_between_date,
            "has_relative_date": has_relative_date,
            "date_val": [flt["val"] for flt in date_filters],
        })

    # 汇总一张对照表，便于快速比较多个请求
    print("\n" + "=" * 70)
    print("=== 全部请求对照表 ===")
    header = f"{'#':>3} {'reportId':>9} {'行数':>6} {'p_date维度':>10} {'截断':>5} {'绝对日期':>9} {'相对日期':>9}"
    print(header)
    for c in candidates:
        print(f"{c['index']:>3} {str(c['report_id']):>9} {c['rows']:>6} "
              f"{('有' if c['has_p_date'] else '无'):>10} "
              f"{('是' if c['truncated'] else '否'):>5} "
              f"{('是' if c['has_between_date'] else '否'):>9} "
              f"{('是' if c['has_relative_date'] else '否'):>9}")

    print("\n=== 选用建议 ===")
    # 理想请求：无p_date维度 + 有数据 + 绝对日期筛选 + 未被截断
    def score(c):
        return (
            c["rows"] > 0,
            not c["has_p_date"],
            c["has_between_date"],
            not c["truncated"],
            c["rows"],
        )

    ideal = [c for c in candidates
             if c["rows"] > 0 and not c["has_p_date"] and c["has_between_date"] and not c["truncated"]]
    if ideal:
        best = max(ideal, key=lambda c: (c["rows"], c["index"]))
        print(f"✅ 建议使用 请求 #{best['index']}（reportId={best['report_id']}，{best['rows']}行，"
              f"无p_date维度，绝对日期筛选，未截断）")
        print(f"   维度: {best['dimensions']}")
        print(f"   日期范围: {best['date_val']}")
        return

    print("⚠️  没有找到完全符合条件的请求。逐项诊断：")
    with_rows = [c for c in candidates if c["rows"] > 0]
    if not with_rows:
        print("   - 所有请求都没有数据行，检查查询是否失败或筛选过严。")
        return

    best_effort = max(with_rows, key=score)
    print(f"   - 目前最接近的是 请求 #{best_effort['index']}"
          f"（reportId={best_effort['report_id']}，{best_effort['rows']}行）")
    if best_effort["has_p_date"]:
        print("   - ❌ 仍含 p_date 维度 → 需要在 DataWind 界面取消该维度")
    if not best_effort["has_between_date"]:
        print("   - ❌ 日期筛选不是绝对范围（between）→ 需要在界面改成绝对日期 2026-08-12 ~ 2026-08-17")
        if best_effort["has_relative_date"]:
            print(f"        当前是相对时间筛选，取值 {best_effort['date_val']}")
    if best_effort["truncated"]:
        print("   - ❌ 被 limit 截断 → 需要按渠道分次筛选抓取，或在界面减少维度/缩小范围")


if __name__ == "__main__":
    main()
