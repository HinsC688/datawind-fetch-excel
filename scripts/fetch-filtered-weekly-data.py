#!/usr/bin/env python3
"""Query the tracked DataWind Push tasks through the authenticated CDP Chrome tab."""
import argparse
import json
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import urlopen

import websocket

DASHBOARD_ID = "41204"
CDP_URL = "http://127.0.0.1:9222/json"


def get_tab_socket():
    with urlopen(CDP_URL, timeout=5) as response:
        tabs = json.load(response)
    tab = next((tab for tab in tabs if DASHBOARD_ID in tab.get("url", "")), None)
    if not tab:
        raise RuntimeError("DataWind dashboard 41204 is not open in the CDP Chrome window.")
    return tab["webSocketDebuggerUrl"]


def cdp_call(ws, message_id, method, params):
    message_id += 1
    ws.send(json.dumps({"id": message_id, "method": method, "params": params}))
    while True:
        message = json.loads(ws.recv())
        if message.get("id") == message_id:
            if "error" in message:
                raise RuntimeError(message["error"])
            return message_id, message.get("result", {})


def load_template(path):
    records = json.loads(path.read_text(encoding="utf-8"))
    return next(record for record in records if "/vizQuery/query" in record.get("url", ""))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--week", required=True)
    parser.add_argument("--tasks", type=Path, default=Path("config/tracked_tasks.json"))
    args = parser.parse_args()

    template = load_template(args.capture)
    tasks = json.loads(args.tasks.read_text(encoding="utf-8"))
    payload = json.loads(template["post_data"])
    query = payload["query"]
    query["limit"] = 1000
    query["whereList"].append({
        "name": "task_name", "id": "1675541", "preRelation": "and",
        "uniqueId": int(time.time() * 1000), "op": "in", "val": tasks,
        "valOption": {"labelValueMap": {}},
        "option": {"isReportFilter": False, "isWhereInAggr": True,
                   "displayType": "multiDropDownList", "filterPattern": "Accurate"},
    })

    parsed = urlparse(template["url"])
    params = parse_qs(parsed.query)
    params["requestId"] = [f"kiro.weekly.{uuid.uuid4()}"]
    request_url = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))
    expression = """(async () => {
      const payload = %s;
      const response = await fetch(%s, {
        method: 'POST', credentials: 'include',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      return await response.text();
    })()""" % (json.dumps(payload, ensure_ascii=False), json.dumps(request_url))

    ws = websocket.create_connection(get_tab_socket(), timeout=30)
    _, result = cdp_call(ws, 0, "Runtime.evaluate", {
        "expression": expression, "awaitPromise": True, "returnByValue": True,
    })
    ws.close()
    remote = result.get("result", {})
    if "exceptionDetails" in result or "value" not in remote:
        raise RuntimeError(f"DataWind query failed: {result}")
    response = json.loads(remote["value"])
    if response.get("code") != "aeolus/ok":
        raise RuntimeError(f"DataWind returned {response.get('code')}: {response.get('msg')}")

    data = response["data"]
    columns = {column["name"].strip(): str(column["unique_id"]) for column in data["columns"]}
    datasets = data["vizData"]["datasets"]
    task_key = columns["task_name"]
    week_key = columns["p_date"]
    rows = [row for row in datasets if not row.get("combined") and row.get(task_key) in tasks and row.get(week_key) == args.week]
    found = {row[task_key] for row in rows}
    missing = sorted(set(tasks) - found)

    output = {
        "week": args.week, "tracked_task_count": len(tasks), "matched_row_count": len(rows),
        "matched_task_count": len(found), "missing_tasks": missing, "columns": columns, "rows": rows,
    }
    output_dir = Path("artifacts")
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{time.strftime('%Y%m%dT%H%M%S')}-weekly-filtered.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(rows)} W31 rows for {len(found)}/{len(tasks)} tracked tasks to {path}")
    if missing:
        raise SystemExit(f"Missing {len(missing)} tracked task names; no flysheet upload should run.")


if __name__ == "__main__":
    main()
