#!/usr/bin/env python3
"""Capture DataWind network activity through Chrome CDP for query discovery."""
import json
import time
from pathlib import Path
from urllib.request import urlopen

import websocket

CDP_URL = "http://127.0.0.1:9222/json"
DASHBOARD_ID = "41204"
CAPTURE_SECONDS = 75
OUTPUT = Path("artifacts/datawind-network-events.json")
NETWORK_METHODS = {
    "Network.requestWillBeSent",
    "Network.responseReceived",
    "Network.loadingFailed",
}


def get_tab_socket():
    with urlopen(CDP_URL, timeout=5) as response:
        tabs = json.load(response)
    tab = next((item for item in tabs if DASHBOARD_ID in item.get("url", "")), None)
    if not tab:
        raise RuntimeError(f"No Chrome tab found for dashboard {DASHBOARD_ID}")
    return tab["webSocketDebuggerUrl"]


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-reload", action="store_true", help="Listen for manual table actions.")
    parser.add_argument("--seconds", type=int, default=CAPTURE_SECONDS)
    args = parser.parse_args()

    ws = websocket.create_connection(get_tab_socket(), timeout=5)
    message_id = 0
    events = []
    requests = {}

    def record(message):
        method = message.get("method")
        if method in NETWORK_METHODS:
            events.append(message)
        if method == "Network.requestWillBeSent":
            requests[message["params"]["requestId"]] = message["params"]

    def send(method, params=None):
        nonlocal message_id
        message_id += 1
        ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        return message_id

    def wait_for(command_id):
        while True:
            message = json.loads(ws.recv())
            record(message)
            if message.get("id") == command_id:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result", {})

    wait_for(send("Network.enable"))
    wait_for(send("Network.setCacheDisabled", {"cacheDisabled": True}))
    wait_for(send("Page.enable"))
    if args.no_reload:
        print("Listening now: open Push任务明细表, set the period, then click refresh in Chrome.")
    else:
        wait_for(send("Page.reload", {"ignoreCache": True}))

    deadline = time.time() + args.seconds
    print(f"Capturing network activity for {args.seconds} seconds...")
    while time.time() < deadline:
        try:
            record(json.loads(ws.recv()))
        except websocket.WebSocketTimeoutException:
            continue

    bodies = []
    for event in events:
        if event.get("method") != "Network.responseReceived":
            continue
        params = event["params"]
        if params.get("type") not in {"XHR", "Fetch"}:
            continue
        request_id = params["requestId"]
        request = requests.get(request_id, {}).get("request", {})
        try:
            result = wait_for(send("Network.getResponseBody", {"requestId": request_id}))
        except RuntimeError as error:
            bodies.append({"request_id": request_id, "url": request.get("url"), "error": str(error)})
            continue
        body = result.get("body", "")
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            pass
        bodies.append({"request_id": request_id, "url": request.get("url"),
                       "method": request.get("method"), "post_data": request.get("postData"), "body": body})

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    bodies_path = OUTPUT.with_name("datawind-response-bodies.json")
    bodies_path.write_text(json.dumps(bodies, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [event for event in events if event.get("method") == "Network.loadingFailed"]
    print(f"Captured {len(requests)} requests, {len(bodies)} XHR/Fetch bodies, and {len(failures)} failures.")
    print(f"Saved results to {OUTPUT} and {bodies_path}")
    ws.close()


if __name__ == "__main__":
    main()
