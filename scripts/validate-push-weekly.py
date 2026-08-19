#!/usr/bin/env python3
"""Validate one complete Push weekly DataWind capture against a Lark template."""
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


def selected_response(path, start, end):
    wanted = [f"{start} 00:00:00", f"{end} 23:59:59"]
    candidates = []
    for item in json.loads(path.read_text()):
        if "/vizQuery/query" not in item.get("url", ""):
            continue
        body, post = item.get("body", {}), json.loads(item.get("post_data", "{}"))
        data = body.get("data", {})
        viz, query = data.get("vizData", {}), post.get("query", {})
        dates = any(w.get("name") == "p_date" and w.get("op") == "between" and w.get("val") == wanted for w in query.get("whereList", []))
        aliases = set(viz.get("aliasMap", {}).values())
        total, limit = data.get("total"), query.get("limit")
        if dates and {"workflow_name", "task_name"} <= aliases and isinstance(total, int) and total < limit:
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
    args = parser.parse_args()
    _, viz = selected_response(args.response, args.start, args.end)
    ids = {name: key for key, name in viz["aliasMap"].items()}
    keys = [(row.get(ids["workflow_name"]), row.get(ids["task_name"])) for row in viz["datasets"]]
    counts = Counter(key for key in keys if all(key))
    missing = [(row, flow, task) for row, flow, task in template_rows(args.template) if (flow, task) not in counts]
    targets = {(flow, task) for _, flow, task in template_rows(args.template)}
    duplicates = [key for key in targets if counts[key] > 1]
    print(f"Selected complete capture: {len(viz['datasets'])} datasets; template targets: {len(template_rows(args.template))}")
    print(f"Matched: {len(template_rows(args.template)) - len(missing)}; missing: {len(missing)}; duplicate target keys: {len(duplicates)}")
    if missing:
        print("Missing:\n" + "\n".join(f"row {r}: {f} / {t}" for r, f, t in missing))
    if duplicates:
        print("Duplicate target keys:\n" + "\n".join(f"{f} / {t}" for f, t in duplicates))
    if missing or duplicates:
        raise SystemExit("Capture cannot be used for writing.")


if __name__ == "__main__":
    main()
