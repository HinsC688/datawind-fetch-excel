#!/usr/bin/env python3
"""列出抓取结果里每个渠道下实际出现的"触达详情"名称，用于确定筛选清单。

用途：在 DataWind 界面上直接按"触达详情"筛选目标名单可以规避1000行上限，
但前提是要知道本周期实际存在哪些名称（名称可能改过、可能有新增/下线）。
本脚本把抓到的名称按渠道分组列出，供跟飞书表里的历史名单做人工比对。

用法：
    python3 scripts/list-touch-details.py artifacts/{时间戳}-response-bodies.json
    python3 scripts/list-touch-details.py artifacts/*.json --tool 弹窗
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

QUERY_MARKER = "vizQuery/query"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("response_bodies", nargs="+")
    parser.add_argument("--tool", default=None, help="只看某个渠道，如 push / 邮件 / 弹窗")
    parser.add_argument("--include-truncated", action="store_true",
                        help="也统计被截断的请求（默认包含，因为这里只是看名称清单，不做数值计算）")
    args = parser.parse_args()

    # 渠道 -> 触达详情 -> 出现过的 reg_range 集合
    catalog = defaultdict(lambda: defaultdict(set))

    for path in args.response_bodies:
        bodies = json.loads(Path(path).read_text(encoding="utf-8"))
        for item in bodies:
            if QUERY_MARKER not in (item.get("url") or ""):
                continue
            body = item.get("body")
            if not isinstance(body, dict):
                continue
            viz = (body.get("data") or {}).get("vizData") or {}
            alias = viz.get("aliasMap") or {}
            for raw in viz.get("datasets") or []:
                if not isinstance(raw, dict):
                    continue
                row = {alias.get(key, key): value for key, value in raw.items()}
                tool = row.get("触达工具")
                detail = row.get("触达详情")
                reg = row.get("reg_range")
                if not tool or not detail:
                    continue
                catalog[tool][detail].add(reg)

    tools = [args.tool] if args.tool else sorted(catalog, key=lambda x: str(x))
    for tool in tools:
        details = catalog.get(tool)
        if not details:
            print(f"\n=== 渠道 {tool!r}：无数据 ===")
            continue
        print(f"\n{'=' * 70}")
        print(f"=== 渠道 {tool!r}：{len(details)} 个不同的触达详情 ===")
        for detail in sorted(details, key=lambda x: str(x)):
            regs = sorted(str(reg) for reg in details[detail] if reg)
            print(f"  {detail}")
            print(f"      reg_range: {', '.join(regs) if regs else '(空)'}")


if __name__ == "__main__":
    main()
