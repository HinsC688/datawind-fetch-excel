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
    ws = websocket.create_connection(get_tab_socket(), timeout=5)
    message_id = 0
    events = []

    def record(message):
        if message.get("method") in NETWORK_METHODS:
            events.append(message)

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
                    raise RuntimeError(f"CDP command failed: {message['error']}")
                return

    wait_for(send("Network.enable"))
    wait_for(send("Network.setCacheDisabled", {"cacheDisabled": True}))
    wait_for(send("Page.enable"))
    wait_for(send("Page.reload", {"ignoreCache": True}))

    print(f"Capturing network activity for {CAPTURE_SECONDS} seconds...")
    deadline = time.time() + CAPTURE_SECONDS
    while time.time() < deadline:
        try:
            record(json.loads(ws.recv()))
        except websocket.WebSocketTimeoutException:
            continue

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    requests = [event for event in events if event.get("method") == "Network.requestWillBeSent"]
    failures = [event for event in events if event.get("method") == "Network.loadingFailed"]
    print(f"Captured {len(requests)} requests and {len(failures)} failed loads.")
    for event in requests:
        request = event["params"]["request"]
        kind = event["params"].get("type", "")
        if kind in {"XHR", "Fetch"}:
            print(f"{kind} {request['method']} {request['url']}")
    for event in failures:
        params = event["params"]
        print(f"FAILED {params.get('errorText')} ({params.get('blockedReason', '')})")
    print(f"Saved all events to {OUTPUT}")
    ws.close()


if __name__ == "__main__":
    main()
