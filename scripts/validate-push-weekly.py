#!/usr/bin/env python3
"""Validate one complete Push weekly DataWind capture against approved targets."""
import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path


def template_rows(path):
    flow, rows = "", []
    for raw in json.loads(path.read_text())["annotated_csv"].splitlines()[1:]:
        match = re.match(r"^\[row=(\d+)\]\s*(.*)$", raw)
        if not match:
            continue
        row, cells = int(match.group(1)), next(csv.reader([match.group(2)]))
        flow = cells[0].strip() or flow
        if cells[2].strip():
            rows.append((row, flow, cells[2].strip()))
    return rows


def planned_targets(path):
    data = json.loads(path.read_text())
    return [(flow["workflow_name"], task) for flow in data["targets"] for task in flow["task_names"]]


def selected_response(path, start, end):
    wanted = [f"{start} 00:00:00", f"{end} 23:59:59"]
    candidates = []
    for item in json.loads(path.read_text()):
        if "/vizQuery/query" not in item.get("url", ""):
            continue
        body, post = item.get("body", {}), json.loads(item.get("post_data", "{}"))
        data = body.get("data", {})
        viz, query = data.get("vizData", {}), post.get("query", {})
        def has_filter(name, value):
            return any(w.get("name") == name and value in (w.get("val") or []) for w in query.get("whereList", []))
        dates = any(w.get("name") == "p_date" and w.get("op") == "between" and w.get("val") == wanted for w in query.get("whereList", []))
        aliases = set(viz.get("aliasMap", {}).values())
        total, limit = data.get("total"), query.get("limit")
        if dates and has_filter("push_type", "apppush") and has_filter("UJ生命周期", "未注册") and {"workflow_name", "task_name"} <= aliases and isinstance(total, int) and total < limit:
            candidates.append((item, viz))
    if len(candidates) != 1:
        raise SystemExit(f"Expected one complete Push query for {start}~{end}; found {len(candidates)}")
    return candidates[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--targets", type=Path, help="Strategy-adjusted target plan JSON.")
    args = parser.parse_args()
    requested = planned_targets(args.targets) if args.targets else [(flow, task) for _, flow, task in template_rows(args.template)]
    _, viz = selected_response(args.response, args.start, args.end)
    ids = {name: key for key, name in viz["aliasMap"].items()}
    keys = [(row.get(ids["workflow_name"]), row.get(ids["task_name"])) for row in viz["datasets"]]
    counts = Counter(key for key in keys if all(key))
    missing = [key for key in requested if key not in counts]
    duplicates = [key for key in requested if counts[key] > 1]
    print(f"Selected complete capture: {len(viz['datasets'])} datasets; requested targets: {len(requested)}")
    print(f"Matched: {len(requested) - len(missing)}; missing: {len(missing)}; duplicate target keys: {len(duplicates)}")
    if missing:
        print("Missing:\n" + "\n".join(f"{flow} / {task}" for flow, task in missing))
    if duplicates:
        print("Duplicate target keys:\n" + "\n".join(f"{flow} / {task}" for flow, task in duplicates))
    if missing or duplicates:
        raise SystemExit("Capture cannot be used for writing.")


if __name__ == "__main__":
    main()
