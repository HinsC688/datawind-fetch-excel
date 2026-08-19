#!/usr/bin/env python3
"""Fetch one legacy Push weekly period through the logged-in Chrome CDP session."""
import argparse
import importlib.util
import json
import time
from pathlib import Path

DASHBOARD_ID = "41204"
DEFAULT_TEMPLATE = Path("artifacts/push-20260812-20260818-response-bodies.json")


def load_cdp():
    path = Path(__file__).with_name("fetch-datawind.py")
    spec = importlib.util.spec_from_file_location("datawind_fetch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def has_filter(query, name, value):
    return any(item.get("name") == name and value in (item.get("val") or []) for item in query.get("whereList", []))


def template_request(path):
    candidates = []
    for item in json.loads(path.read_text()):
        if "/vizQuery/query" not in item.get("url", ""):
            continue
        body, post = item.get("body", {}), json.loads(item.get("post_data", "{}"))
        data, query = body.get("data", {}), post.get("query", {})
        aliases = set((data.get("vizData", {}).get("aliasMap") or {}).values())
        total, limit = data.get("total"), query.get("limit")
        if {"workflow_name", "task_name"} <= aliases and has_filter(query, "push_type", "apppush") and has_filter(query, "UJ生命周期", "未注册") and isinstance(total, int) and total < limit:
            candidates.append(item)
    if len(candidates) != 1:
        raise SystemExit(f"Expected one complete Push query in {path}; found {len(candidates)}")
    return candidates[0]


def set_filter(where, value):
    where["val"] = value
    option = where.get("valOption")
    if isinstance(option, dict):
        for key, current in option.items():
            if isinstance(current, list):
                option[key] = value


def build_request(item, start, end, limit):
    post = json.loads(item["post_data"])
    query = post["query"]
    query["limit"] = limit
    if isinstance(query.get("cache"), dict):
        query["cache"]["enable"] = False
    found = set()
    for where in query.get("whereList", []):
        name = where.get("name")
        if name == "p_date":
            where["op"] = "between"
            set_filter(where, [f"{start} 00:00:00", f"{end} 23:59:59"])
            found.add(name)
        elif name == "push_type":
            set_filter(where, ["apppush"])
            found.add(name)
        elif name == "UJ生命周期":
            set_filter(where, ["未注册"])
            found.add(name)
    if found != {"p_date", "push_type", "UJ生命周期"}:
        raise SystemExit(f"Template missing required filters: {sorted({ 'p_date', 'push_type', 'UJ生命周期'} - found)}")
    return post


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--template-response", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--prefix")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    item = template_request(args.template_response)
    request = build_request(item, args.start, args.end, args.limit)
    query = request["query"]
    print(f"Push query: {args.start}~{args.end}; limit={query['limit']}")
    print("Required filters: push_type=apppush, UJ生命周期=未注册")
    if args.dry_run:
        return
    cdp = load_cdp()
    page = cdp.find_page(DASHBOARD_ID)
    print(f"Using Chrome page: {page.get('url')[:90]}")
    started = time.time()
    response = cdp.cdp_fetch(page["webSocketDebuggerUrl"], item["url"], request, args.timeout)
    data = response.get("data") or {}
    viz = data.get("vizData") or {}
    rows = [row for row in viz.get("datasets", []) if isinstance(row, dict)]
    print(f"Received {len(rows)} rows in {time.time() - started:.1f}s; total={data.get('total')}; atLeast={data.get('atLeast')}")
    prefix = args.prefix or f"push-{args.start.replace('-', '')}-{args.end.replace('-', '')}"
    output = Path("artifacts") / f"{prefix}-response-bodies.json"
    output.write_text(json.dumps([{"request_id": f"api-replay-{prefix}", "url": item["url"], "method": "POST", "post_data": json.dumps(request, ensure_ascii=False), "body": response}], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {output}")


if __name__ == "__main__":
    main()
