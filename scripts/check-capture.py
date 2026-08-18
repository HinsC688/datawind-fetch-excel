#!/usr/bin/env python3
"""快速校验一次 DataWind 抓取结果是否有效、是否被截断、包含哪些字段和渠道。

用法：
    python3 scripts/check-capture.py artifacts/{时间戳}-response-bodies.json

设计目的：按 SOP/HANDOFF 的要求，抓取后第一件事就是确认
1) 有没有拿到真正的 vizQuery/query 数据请求（没有就是白抓）
2) 返回是否被 1000 行上限截断（atLeast / total / hasMore）
3) 有哪些维度字段和指标别名，供后续写入脚本动态取值（不硬编码字段ID，见坑#7）
"""
import argparse
import json
from collections import Counter
from pathlib import Path

QUERY_MARKER = "vizQuery/query"


def walk_find_keys(node, keys, found=None, path="$"):
    """递归查找可能表示总量/截断的字段。"""
    if found is None:
        found = {}
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys and not isinstance(value, (dict, list)):
                found.setdefault(f"{path}.{key}", value)
            walk_find_keys(value, keys, found, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node[:5]):
            walk_find_keys(value, keys, found, f"{path}[{index}]")
    return found


def find_viz_data(node, results=None, path="$"):
    """找到所有含 aliasMap 的 vizData 节点。"""
    if results is None:
        results = []
    if isinstance(node, dict):
        if "aliasMap" in node:
            results.append((path, node))
        for key, value in node.items():
            find_viz_data(value, results, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            find_viz_data(value, results, f"{path}[{index}]")
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("response_bodies", type=Path)
    parser.add_argument("--show-rows", type=int, default=3, help="打印前N行原始数据样本")
    args = parser.parse_args()

    bodies = json.loads(args.response_bodies.read_text(encoding="utf-8"))
    print(f"响应体总数: {len(bodies)}")

    url_counter = Counter(item.get("url", "").split("?")[0] for item in bodies)
    print("\n=== 抓到的接口 ===")
    for url, count in url_counter.most_common():
        print(f"  {count:3d}  {url}")

    queries = [item for item in bodies if QUERY_MARKER in (item.get("url") or "")]
    if not queries:
        print(f"\n❌ 无效抓取：没有找到任何包含 '{QUERY_MARKER}' 的请求。")
        print("   请重新抓取，注意顺序：先启动监听脚本，再去浏览器触发查询（改筛选或 Cmd+R 硬刷新）。")
        return

    print(f"\n✅ 找到 {len(queries)} 个 {QUERY_MARKER} 请求")

    for index, query in enumerate(queries):
        print(f"\n{'=' * 60}\n请求 #{index + 1}  request_id={query.get('request_id')}")
        body = query.get("body")
        if not isinstance(body, (dict, list)):
            print("  ⚠️  响应体不是JSON（可能抓取时被截断或出错），跳过")
            continue

        # 1) 截断检查
        truncation = walk_find_keys(body, {"atLeast", "total", "totalCount", "hasMore", "limit", "rowCount"})
        if truncation:
            print("  --- 行数/截断相关字段 ---")
            for path, value in truncation.items():
                print(f"    {path} = {value}")
        else:
            print("  --- 未找到 atLeast/total/hasMore 等字段 ---")

        # 2) 请求里的筛选条件（确认是绝对时间而不是相对时间，见坑#4）
        post_data = query.get("post_data")
        if post_data:
            try:
                parsed = json.loads(post_data)
                wheres = walk_find_keys(parsed, {"op"})
                if wheres:
                    ops = {value for value in wheres.values()}
                    print(f"  --- 筛选 op 类型: {sorted(ops)} ---")
                    if "lastSync" in ops:
                        print("    ⚠️  检测到 lastSync（相对时间筛选），周期会漂移，见坑#4")
                    if "between" in ops:
                        print("    ✅ 检测到 between（绝对时间筛选）")
            except json.JSONDecodeError:
                print("  --- post_data 不是JSON，跳过筛选检查 ---")

        # 3) 字段别名表 + 数据样本
        for viz_path, viz in find_viz_data(body):
            alias_map = viz.get("aliasMap") or {}
            rows = viz.get("data") or viz.get("rows") or []
            print(f"\n  --- vizData @ {viz_path} ---")
            print(f"      aliasMap 字段数: {len(alias_map)}   数据行数: {len(rows) if isinstance(rows, list) else 'n/a'}")
            if alias_map:
                print("      字段ID → 显示名:")
                for field_id, name in list(alias_map.items()):
                    print(f"        {field_id!r:45} -> {name!r}")
            if isinstance(rows, list) and rows:
                print(f"      前 {args.show_rows} 行样本:")
                for row in rows[: args.show_rows]:
                    print(f"        {json.dumps(row, ensure_ascii=False)[:400]}")
                # 渠道分布（尝试常见字段名）
                for channel_key in ("push_type", "touch_type", "channel", "生效类型", "触达工具"):
                    values = [row.get(channel_key) for row in rows if isinstance(row, dict) and channel_key in row]
                    if values:
                        print(f"      字段 {channel_key!r} 取值分布: {dict(Counter(values))}")


if __name__ == "__main__":
    main()
