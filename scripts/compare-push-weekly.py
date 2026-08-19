#!/usr/bin/env python3
"""Compare the 26 target Push metrics between two complete captures."""
import argparse
import importlib.util
from decimal import Decimal
from pathlib import Path

METRICS = ["曝光用户数的日均", "点击用户数的日均", "曝光点击率", "注册转化率", "注册转化用户数"]


def load_validator():
    path = Path(__file__).with_name("validate-push-weekly.py")
    spec = importlib.util.spec_from_file_location("push_validator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def values(viz, targets):
    ids = {name: key for key, name in viz["aliasMap"].items()}
    required = {"workflow_name", "task_name", *METRICS}
    if missing := required - ids.keys():
        raise SystemExit(f"Missing fields: {sorted(missing)}")
    records = {}
    for row in viz["datasets"]:
        key = (row.get(ids["workflow_name"]), row.get(ids["task_name"]))
        if key in targets:
            records.setdefault(key, []).append(row)
    invalid = [key for key in targets if len(records.get(key, [])) != 1]
    if invalid:
        raise SystemExit(f"Expected one row per target: {invalid}")
    return {key: [Decimal(str(records[key][0][ids[metric]])) for metric in METRICS] for key in targets}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    validator = load_validator()
    targets = {(flow, task) for _, flow, task in validator.template_rows(args.template)}
    _, baseline = validator.selected_response(args.baseline, args.start, args.end)
    _, candidate = validator.selected_response(args.candidate, args.start, args.end)
    old, new = values(baseline, targets), values(candidate, targets)
    mismatches = [(key, METRICS[index], old[key][index], new[key][index]) for key in targets for index in range(len(METRICS)) if old[key][index] != new[key][index]]
    print(f"Compared {len(targets)} targets × {len(METRICS)} metrics = {len(targets) * len(METRICS)} values")
    if mismatches:
        for key, metric, before, after in mismatches:
            print(f"MISMATCH {key}: {metric}: {before} != {after}")
        raise SystemExit(f"Replay mismatch: {len(mismatches)} values differ")
    print("PASS: all target metric values match exactly")


if __name__ == "__main__":
    main()
