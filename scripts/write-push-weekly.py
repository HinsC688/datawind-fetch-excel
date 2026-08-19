#!/usr/bin/env python3
"""Dry-run or write one complete Push weekly period to the copied Lark template."""
import argparse
import importlib.util
import json
import subprocess
from collections import Counter
from pathlib import Path

METRICS = ["曝光用户数的日均", "点击用户数的日均", "曝光点击率", "注册转化率", "注册转化用户数"]


def load_validator():
    path = Path(__file__).with_name("validate-push-weekly.py")
    spec = importlib.util.spec_from_file_location("push_validator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def number(value):
    value = float(value)
    return int(value) if value.is_integer() else value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--period-label", required=True)
    parser.add_argument("--row-offset", type=int, required=True)
    parser.add_argument("--spreadsheet-token", required=True)
    parser.add_argument("--sheet-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    validator = load_validator()
    _, viz = validator.selected_response(args.response, args.start, args.end)
    ids = {name: key for key, name in viz["aliasMap"].items()}
    required = {"workflow_name", "task_name", *METRICS}
    if missing := required - ids.keys():
        raise SystemExit(f"Missing response fields: {sorted(missing)}")
    targets = validator.template_rows(args.template)
    wanted = {(flow, task) for _, flow, task in targets}
    rows_by_key = {}
    for row in viz["datasets"]:
        key = (row.get(ids["workflow_name"]), row.get(ids["task_name"]))
        if key in wanted:
            rows_by_key.setdefault(key, []).append(row)
    invalid = [key for key in wanted if len(rows_by_key.get(key, [])) != 1]
    if invalid:
        raise SystemExit(f"Expected one row per target; invalid keys: {invalid}")
    plan = []
    for base_row, flow, task in targets:
        data = rows_by_key[(flow, task)][0]
        values = [number(data[ids[metric]]) for metric in METRICS]
        plan.append((base_row + args.row_offset, task, values))
    print(f"{'APPLY' if args.apply else 'DRY-RUN'} {args.period_label}: {len(plan)} rows")
    for row, task, values in plan:
        print(f"row {row}: {args.period_label} | {task} | {values}")
    if not args.apply:
        return
    for row, task, values in plan:
        cells = [[{"value": args.period_label}, {"value": task}, *({"value": value} for value in values)]]
        command = ["npx", "--yes", "@larksuite/cli@latest", "sheets", "+cells-set", "--spreadsheet-token", args.spreadsheet_token, "--sheet-id", args.sheet_id, "--range", f"B{row}:H{row}", "--cells", json.dumps(cells, ensure_ascii=False)]
        print(f"Writing row {row}")
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
