#!/usr/bin/env python3
"""Write W31 weekly metrics from a captured DataWind response to a Lark template."""
import argparse
import csv
import json
import re
import subprocess
from pathlib import Path

SKIP_ROWS = {38, 44}
METRICS = {
    "曝光用户数的日均": "exposure_avg",
    "点击用户数的日均": "click_avg",
    "曝光点击率": "click_through_rate",
    "注册转化率": "registration_rate",
    "注册转化用户数": "registrations",
}


def read_template(path: Path):
    data = json.loads(path.read_text())
    flow, items = "", []
    for raw in data["annotated_csv"].splitlines()[1:]:
        match = re.match(r"^\[row=(\d+)\]\s*(.*)$", raw)
        if not match:
            continue
        row, cells = int(match.group(1)), next(csv.reader([match.group(2)]))
        flow = cells[0].strip() or flow
        if cells[2].strip():
            items.append((row, flow, cells[2].strip()))
    return items


def number(value):
    value = float(value)
    return int(value) if value.is_integer() else value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--spreadsheet-token", required=True)
    parser.add_argument("--sheet-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    responses = json.loads(args.response.read_text())
    response = next(item for item in responses if "/vizQuery/query" in item.get("url", ""))
    viz_data = response["body"]["data"]["vizData"]
    aliases = viz_data["aliasMap"]
    field_id = {label: next(key for key, name in aliases.items() if name == label) for label in ["workflow_name", "task_name", "p_date", *METRICS]}
    lookup = {(row.get(field_id["workflow_name"]), row.get(field_id["task_name"])): row for row in viz_data["datasets"]}
    week = next(row[field_id["p_date"]] for row in viz_data["datasets"] if row.get(field_id["p_date"]) != "总计")

    rows_to_write, missing = [], []
    for row_number, workflow, task in read_template(args.template):
        if row_number in SKIP_ROWS:
            continue
        data = lookup.get((workflow, task))
        if not data:
            missing.append(f"row {row_number}: {workflow} / {task}")
            continue
        values = [number(data[field_id[label]]) for label in METRICS]
        rows_to_write.append((row_number, week, task, values))
    if missing:
        raise SystemExit("Missing template matches:\n" + "\n".join(missing))

    print(f"Prepared {len(rows_to_write)} W31 rows; skipped rows: {sorted(SKIP_ROWS)}")
    for row_number, week, task, values in rows_to_write:
        cells = [[{"value": week}, {"value": task}, *({"value": value} for value in values)]]
        command = ["npx", "--yes", "@larksuite/cli@latest", "sheets", "+cells-set", "--spreadsheet-token", args.spreadsheet_token, "--sheet-id", args.sheet_id, "--range", f"B{row_number}:H{row_number}", "--cells", json.dumps(cells, ensure_ascii=False)]
        if not args.apply:
            command.append("--dry-run")
        print(f"Writing row {row_number}")
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
