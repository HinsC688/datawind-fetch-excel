#!/usr/bin/env python3
"""直接重放 DataWind 的 vizQuery 请求取数，不需要手工在界面上点筛选。

原理：通过 Chrome CDP 在**已登录的 DataWind 页面上下文里**执行 fetch()，
浏览器会自动带上会话 cookie，所以不需要提取/维护任何凭据。
请求体来自 config/vizquery-template.json（上次成功抓取的请求），
脚本只替换其中的 p_date 日期范围和 detail 名单。

相比"启动监听 + 手工点界面"的老流程，这样做的好处：
  - 不用手工设6个筛选、不用手工取消 p_date 维度 → 少了最容易出错的环节
  - 筛选条件完全确定，不会出现"抓到的是筛选中间状态"的问题（坑#16/#17）
  - limit 可以自己设大，不受界面默认1000的截断限制（坑#5）
  - 不需要等固定的监听窗口，请求返回即结束

前提：
  - FortiVPN 已连接（能访问 datawind.xiaoxiame.com）
  - 已用 scripts/open-chrome.sh 打开带CDP的Chrome，并且**已登录 DataWind**
    （会话过期时需要人工登录一次，这一步没法自动化）

用法：
    python3 scripts/fetch-datawind.py --start 2026-08-19 --end 2026-08-25
    python3 scripts/fetch-datawind.py --start 2026-08-19 --end 2026-08-25 \
        --details config/touch-details-0819-0825.txt

输出与 capture-datawind-requests.py 相同的格式（artifacts/{时间戳}-response-bodies.json），
所以 check-capture.py / write-kyc-weekly.py 可以直接复用，无需改动。
"""
import argparse
import json
import time
from pathlib import Path
from urllib.request import urlopen

import websocket

CDP_URL = "http://127.0.0.1:9222/json"
TEMPLATE = Path("config/vizquery-template.json")
OUTPUT_DIR = Path("artifacts")


def find_page(dashboard_id):
    with urlopen(CDP_URL, timeout=5) as response:
        tabs = json.load(response)
    pages = [tab for tab in tabs if tab.get("type") == "page"]
    match = next((tab for tab in pages if dashboard_id in tab.get("url", "")), None)
    if match:
        return match
    # 退而求其次：只要是 datawind 域名的页面就行（会话是按域名走的）
    match = next((tab for tab in pages if "datawind.xiaoxiame.com" in tab.get("url", "")), None)
    if match:
        print(f"⚠️  没找到 dashboard {dashboard_id} 的页面，改用同域名页面：{match.get('url')[:90]}")
        return match
    raise SystemExit(
        f"❌ 没找到 DataWind 页面。请先运行：\n"
        f"   bash scripts/open-chrome.sh \"https://datawind.xiaoxiame.com/bi/pages/dashboard/{dashboard_id}?appId=2&sheetId=14003\"\n"
        f"   并在弹出的 Chrome 里登录 DataWind。"
    )


def build_body(template_body, start, end, details, limit):
    """替换日期范围和 detail 名单，其余筛选沿用模板。"""
    body = json.loads(json.dumps(template_body))  # 深拷贝
    query = body["query"]
    query["limit"] = limit
    # 关掉缓存，确保拿到的是实时结果而不是上次的缓存
    if isinstance(query.get("cache"), dict):
        query["cache"]["enable"] = False

    replaced = {"p_date": False, "detail": False}
    for where in query.get("whereList") or []:
        if where.get("name") == "p_date" and where.get("op") == "between":
            where["val"] = [f"{start} 00:00:00", f"{end} 23:59:59"]
            replaced["p_date"] = True
        elif where.get("name") == "detail":
            where["val"] = list(details)
            option = where.get("valOption")
            if isinstance(option, dict):
                # valOption 里也可能带一份名单副本，一并同步
                for key, value in list(option.items()):
                    if isinstance(value, list):
                        option[key] = list(details)
            replaced["detail"] = True

    missing = [name for name, done in replaced.items() if not done]
    if missing:
        raise SystemExit(f"❌ 模板里找不到要替换的筛选条件: {missing}；"
                         f"模板可能来自不同的看板，请重新抓一次并更新 config/vizquery-template.json")
    return body


def cdp_fetch(ws_url, url, body, timeout):
    """在页面上下文里执行 fetch，复用浏览器已有会话。"""
    connection = websocket.create_connection(ws_url, timeout=timeout + 10)
    message_id = 0

    def call(method, params):
        nonlocal message_id
        message_id += 1
        connection.send(json.dumps({"id": message_id, "method": method, "params": params}))
        while True:
            message = json.loads(connection.recv())
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result", {})

    script = """
    (async () => {
      const url = %s;
      const payload = %s;
      const response = await fetch(url, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json, text/plain, */*",
          "App-Id": "2",
          "Content-Language": "zh-CN",
          "Data-Format-Unit": "auto",
          "X-Aeolus-Gray-Env": "aeolus-online",
        },
        body: JSON.stringify(payload),
      });
      const text = await response.text();
      return JSON.stringify({ status: response.status, text: text });
    })()
    """ % (json.dumps(url), json.dumps(body, ensure_ascii=False))

    result = call("Runtime.evaluate", {
        "expression": script,
        "awaitPromise": True,
        "returnByValue": True,
        "timeout": timeout * 1000,
    })
    connection.close()

    if result.get("exceptionDetails"):
        raise SystemExit(f"❌ 页面内 fetch 抛异常: {json.dumps(result['exceptionDetails'], ensure_ascii=False)[:500]}")
    raw = (result.get("result") or {}).get("value")
    if not raw:
        raise SystemExit(f"❌ fetch 没有返回内容: {json.dumps(result, ensure_ascii=False)[:500]}")
    envelope = json.loads(raw)
    if envelope["status"] != 200:
        raise SystemExit(f"❌ HTTP {envelope['status']}；响应前500字符：{envelope['text'][:500]}")
    return json.loads(envelope["text"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="周期开始日期，如 2026-08-19")
    parser.add_argument("--end", required=True, help="周期结束日期，如 2026-08-25")
    parser.add_argument("--details", type=Path, default=Path("config/touch-details-0812-0817.txt"),
                        help="触达详情名单，每行一个")
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    parser.add_argument("--dashboard-id", default="40450")
    parser.add_argument("--limit", type=int, default=5000, help="放大 limit，避免截断")
    parser.add_argument("--timeout", type=int, default=180, help="查询超时秒数（DataWind可能要跑1-2分钟）")
    args = parser.parse_args()

    template = json.loads(args.template.read_text(encoding="utf-8"))
    details = [line.strip() for line in args.details.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"名单 {args.details}：{len(details)} 个触达详情")
    print(f"周期：{args.start} ~ {args.end}   limit={args.limit}")

    body = build_body(template["post_data"], args.start, args.end, details, args.limit)
    page = find_page(args.dashboard_id)
    print(f"使用页面：{page.get('url')[:90]}")

    print("发起查询（DataWind 后端可能需要1-2分钟）...")
    started = time.time()
    response = cdp_fetch(page["webSocketDebuggerUrl"], template["url"], body, args.timeout)
    elapsed = time.time() - started

    data = response.get("data") or {}
    viz = data.get("vizData") or {}
    rows = [row for row in (viz.get("datasets") or []) if isinstance(row, dict)]
    print(f"✅ 返回 {len(rows)} 行（耗时 {elapsed:.1f}s，code={response.get('code')}，"
          f"total={data.get('total')}，atLeast={data.get('atLeast')}）")
    if isinstance(data.get("total"), int) and data["total"] >= args.limit:
        print(f"⚠️  行数达到 limit={args.limit}，可能仍被截断，考虑加大 --limit")

    # 存成跟 capture-datawind-requests.py 一致的格式，让下游脚本可以直接复用
    run_id = time.strftime("%Y%m%dT%H%M%S")
    output = OUTPUT_DIR / f"{run_id}-response-bodies.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps([{
        "request_id": f"api-replay-{run_id}",
        "url": template["url"],
        "method": "POST",
        "post_data": json.dumps(body, ensure_ascii=False),
        "body": response,
    }], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已保存：{output}")
    print(f"\n下一步（切回 Clash 后）：")
    print(f"  python3 scripts/check-capture.py {output}")
    print(f"  python3 scripts/write-kyc-weekly.py --tab all   # 先dry-run")


if __name__ == "__main__":
    main()
