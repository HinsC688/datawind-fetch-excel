#!/usr/bin/env python3
"""Build the weekly legacy Push target list from the strategy-change sheet."""
import argparse
import csv
import importlib.util
import json
import re
from collections import OrderedDict
from pathlib import Path


def load_validator():
    path = Path(__file__).with_name("validate-push-weekly.py")
    spec = importlib.util.spec_from_file_location("push_validator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sheet_rows(path):
    rows = {}
    text = json.loads(path.read_text())["annotated_csv"]
    pattern = r"^\[row=(\d+)\]\s*(.*?)(?=^\[row=\d+\]|\Z)"
    for match in re.finditer(pattern, text, re.MULTILINE | re.DOTALL):
        rows[int(match.group(1))] = next(csv.reader([match.group(2).rstrip("\n")]))
    return rows


def entries(section):
    pattern = r"流程名称\s*[:：]\s*[\"“]([^\"”]+)[\"”]\s*;\s*对应任务名称\s*[:：]\s*((?:[\"“][^\"”]+[\"”]\s*&?\s*)+)"
    result = []
    for flow, task_text in re.findall(pattern, section):
        tasks = re.findall(r"[\"“]([^\"”]+)[\"”]", task_text)
        result.append((flow.strip(), [task.strip() for task in tasks]))
    return result


def changes(text):
    text = text.replace("\r\n", "\n")
    online = re.split(r"下线流程(?:流程)?名称及任务名称\s*[:：]", text, maxsplit=1)
    up = re.split(r"上线流程名称及任务名称\s*[:：]", online[0], maxsplit=1)
    return entries(up[1] if len(up) == 2 else ""), entries(online[1] if len(online) == 2 else "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--strategy", type=Path, required=True)
    parser.add_argument("--period-label", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    validator = load_validator()
    targets = OrderedDict()
    for _, flow, task in validator.template_rows(args.template):
        targets.setdefault(flow, []).append(task)
    rows = sheet_rows(args.strategy)
    record = next(((row, cells) for row, cells in rows.items() if cells and cells[0].strip() == args.period_label), None)
    if not record:
        raise SystemExit(f"No strategy record for {args.period_label}; add its A/B row before fetching.")
    row, cells = record
    status = cells[1].strip() if len(cells) > 1 else ""
    no_change = rows.get(2, ["", "", "", "", ""])[4].strip()
    if not status:
        raise SystemExit(f"Strategy record row {row} has no change status.")
    online, offline = ([], []) if status == no_change else changes(status)
    if status != no_change and not (online or offline):
        raise SystemExit(f"Strategy record row {row} is not the no-change template and could not be parsed.")
    for flow, tasks in offline:
        if flow not in targets:
            raise SystemExit(f"Offline workflow is not in the existing target list: {flow}")
        absent = [task for task in tasks if task not in targets[flow]]
        if absent:
            raise SystemExit(f"Offline tasks are not in the existing target list for {flow}: {absent}")
        targets[flow] = [task for task in targets[flow] if task not in tasks]
        if not targets[flow]:
            del targets[flow]
    for flow, tasks in online:
        current = targets.setdefault(flow, [])
        duplicates = [task for task in tasks if task in current]
        if duplicates:
            raise SystemExit(f"Online tasks already exist for {flow}: {duplicates}")
        current.extend(tasks)
    result = {"period_label": args.period_label, "strategy_row": row, "status": status, "online": online, "offline": offline, "targets": [{"workflow_name": flow, "task_names": tasks} for flow, tasks in targets.items()]}
    print(f"Strategy row {row}: {status}")
    print(f"Final targets: {sum(len(tasks) for tasks in targets.values())} tasks across {len(targets)} workflows")
    for flow, tasks in targets.items():
        print(f"{flow}:\n" + "\n".join(f"  - {task}" for task in tasks))
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
