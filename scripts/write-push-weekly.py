#!/usr/bin/env python3
"""Dry-run or write one complete Push weekly period to the copied Lark template."""
import argparse
import csv
import importlib.util
import json
import re
import subprocess
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


def next_rows(path, base_rows, forced_offset):
    rows = {}
    for raw in json.loads(path.read_text())["annotated_csv"].splitlines()[1:]:
        match = re.match(r"^\[row=(\d+)\]\s*(.*)$", raw)
        if match:
            rows[int(match.group(1))] = next(csv.reader([match.group(2)]))
    selected = {}
    for base in base_rows:
        candidates = [base + forced_offset] if forced_offset is not None else range(base, base + 6)
        row = next((candidate for candidate in candidates if candidate in rows and not rows[candidate][1].strip() and not any(rows[candidate][column].strip() for column in range(3, 8))), None)
        if row is None:
            raise SystemExit(f"No empty weekly row available for template block beginning at row {base}")
        selected[base] = row
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--period-label", required=True)
    parser.add_argument("--row-offset", type=int, help="Optional override; default finds the first empty row in every six-row block.")
    parser.add_argument("--targets", type=Path, help="Strategy-adjusted target plan JSON.")
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
    locations = {(flow, task): base for base, flow, task in validator.template_rows(args.template)}
    requested = validator.planned_targets(args.targets) if args.targets else list(locations)
    missing_blocks = [key for key in requested if key not in locations]
    if missing_blocks:
        raise SystemExit("New strategy targets need formatted task blocks in push数据 before writing:\n" + "\n".join(f"{flow} / {task}" for flow, task in missing_blocks))
    targets = [(locations[(flow, task)], flow, task) for flow, task in requested]
    wanted = set(requested)
    rows_by_key = {}
    for row in viz["datasets"]:
        key = (row.get(ids["workflow_name"]), row.get(ids["task_name"]))
        if key in wanted:
            rows_by_key.setdefault(key, []).append(row)
    invalid = [key for key in wanted if len(rows_by_key.get(key, [])) != 1]
    if invalid:
        raise SystemExit(f"Expected one row per target; invalid keys: {invalid}")
    destinations = next_rows(args.template, [base for base, _, _ in targets], args.row_offset)
    plan = []
    for base, flow, task in targets:
        values = [number(rows_by_key[(flow, task)][0][ids[metric]]) for metric in METRICS]
        plan.append((base, destinations[base], task, values))
    print(f"{'APPLY' if args.apply else 'DRY-RUN'} {args.period_label}: {len(plan)} rows")
    for base, row, task, values in plan:
        print(f"row {row} (block {base}): {args.period_label} | {task} | {values}")
    if not args.apply:
        return
    for base, row, task, values in plan:
        if row == base:
            writes = [(f"B{row}:H{row}", [[{"value": args.period_label}, {"value": task}, *({"value": value} for value in values)]])]
        else:
            writes = [(f"B{row}:B{row}", [[{"value": args.period_label}]]), (f"D{row}:H{row}", [[*({"value": value} for value in values)]])]
        for cell_range, cells in writes:
            command = ["npx", "--yes", "@larksuite/cli@latest", "sheets", "+cells-set", "--spreadsheet-token", args.spreadsheet_token, "--sheet-id", args.sheet_id, "--range", cell_range, "--cells", json.dumps(cells, ensure_ascii=False)]
            print(f"Writing {cell_range}")
            subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
