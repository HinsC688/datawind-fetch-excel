#!/usr/bin/env python3
"""自动探测三个 tab 下一批数据应该写在哪一行，省掉每周手改行号。

为什么需要：每周追加一组数据后，下一周的写入起始行就变了；
而且弹窗 tab 有 App / Web-H5 两个区块，App 数据不能盖到 Web/H5 标题上（坑#18）。

做法：读整个 tab（不能只信 current_region，空行之后往往还有数据），
按"关键列最后一个非空行"定位区块末尾。

用法：
    python3 scripts/detect-write-rows.py
    python3 scripts/detect-write-rows.py --update-config config/write-plan-0819-0825.json
"""
import argparse
import csv
import json
import re
import subprocess
import tempfile
from pathlib import Path

TOKEN = "Wmq4s0mJHh3HZ7tITvbu261ZsFb"
TABS = {
    "push": {"sheet_id": "BHIztA", "range": "A1:K300", "key_col": 1, "block_end_marker": None},
    "邮件": {"sheet_id": "6kyjFR", "range": "A1:Q300", "key_col": 1, "block_end_marker": None},
    # 弹窗的 App 区块以 "Web/H5" 标题行为界
    "弹窗": {"sheet_id": "b45c2f", "range": "A1:N300", "key_col": 2, "block_end_marker": "Web/H5"},
}


def read_tab(sheet_id, cell_range):
    # 飞书CLI要求 --output-path 是当前目录下的相对路径，不能用系统临时目录
    temp_dir = Path("artifacts/.tmp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"detect-{sheet_id}.json"
    try:
        command = [
            "npx", "--yes", "@larksuite/cli@latest", "sheets", "+csv-get",
            "--spreadsheet-token", TOKEN, "--sheet-id", sheet_id,
            "--range", cell_range, "--output-path", str(temp_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise SystemExit(f"❌ 读取失败: {result.stderr[:300]}")
        payload = json.loads(temp_path.read_text(encoding="utf-8"))
    finally:
        temp_path.unlink(missing_ok=True)

    rows = {}
    for line in payload["annotated_csv"].splitlines():
        match = re.match(r"^\[row=(\d+)\]\s*(.*)$", line)
        if not match:
            continue
        cells = next(csv.reader([match.group(2)]))
        rows[int(match.group(1))] = cells
    return rows


def analyze(name, config):
    rows = read_tab(config["sheet_id"], config["range"])
    key_col = config["key_col"]
    marker = config["block_end_marker"]

    def cell(row_number, index):
        cells = rows.get(row_number) or []
        return cells[index].strip() if index < len(cells) else ""

    marker_row = None
    if marker:
        for row_number in sorted(rows):
            if any(marker in (value or "") for value in (rows[row_number] or [])):
                marker_row = row_number
                break

    # 区块范围：从头到 marker 之前（弹窗）或整表（push/邮件）
    limit = (marker_row - 1) if marker_row else max(rows)
    last_data_row = None
    for row_number in sorted(rows):
        if row_number > limit:
            break
        if cell(row_number, key_col):
            last_data_row = row_number

    # 最后一组的周期标签（A列最后一个非空值）
    last_period = ""
    for row_number in sorted(rows):
        if row_number > limit:
            break
        value = cell(row_number, 0)
        if value and not re.fullmatch(r"App|Web/H5", value):
            last_period = value

    print(f"\n{'=' * 70}")
    print(f"【{name}】sheet_id={config['sheet_id']}")
    if marker_row:
        print(f"  区块分界：第 {marker_row} 行是 {marker!r} 标题，App区块到第 {marker_row - 1} 行为止")
    print(f"  最后一行数据：第 {last_data_row} 行")
    print(f"  最后一组周期标签：{last_period!r}")
    print(f"    末尾几行预览：")
    for row_number in range((last_data_row or 1) - 2, (last_data_row or 1) + 3):
        if row_number in rows:
            preview = ",".join((rows[row_number] or [])[:4])
            flag = "  ← 最后一行数据" if row_number == last_data_row else ""
            flag = f"  ← {marker} 标题" if row_number == marker_row else flag
            print(f"      [{row_number}] {preview[:80]}{flag}")

    insert_at = (last_data_row or 0) + 1
    print(f"\n  建议做法（先插入带格式的行，见坑#19）：")
    print(f"    npx --yes @larksuite/cli@latest sheets +dim-insert \\")
    print(f"      --spreadsheet-token {TOKEN} --sheet-id {config['sheet_id']} \\")
    print(f"      --position {insert_at} --count <行数+1> --inherit-style before")
    print(f"    插完：第 {insert_at} 行留空作分隔，数据从第 {insert_at + 1} 行开始")
    print(f"    然后：python3 scripts/write-kyc-weekly.py --tab {name} --start-row {insert_at + 1}")

    return {
        "last_data_row": last_data_row,
        "last_period": last_period,
        "marker_row": marker_row,
        "insert_at": insert_at,
        "write_start_row": insert_at + 1,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tab", choices=[*TABS, "all"], default="all")
    parser.add_argument("--update-config", type=Path, default=None,
                        help="把探测到的行号写回指定的 write-plan 配置文件")
    args = parser.parse_args()

    names = list(TABS) if args.tab == "all" else [args.tab]
    detected = {name: analyze(name, TABS[name]) for name in names}

    if args.update_config:
        config = json.loads(args.update_config.read_text(encoding="utf-8"))
        for name, info in detected.items():
            tab = config.get("tabs", {}).get(name)
            if not tab:
                continue
            tab["blank_separator_row"] = info["insert_at"]
            tab["write_start_row"] = info["write_start_row"]
            if info["marker_row"]:
                tab["web_header_row"] = info["marker_row"]
                tab["app_block_last_row"] = info["last_data_row"]
        args.update_config.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✅ 已把行号写回 {args.update_config}")

    print(f"\n{'=' * 70}")
    print("汇总：")
    for name, info in detected.items():
        print(f"  {name}: 最后数据行={info['last_data_row']}  "
              f"最后周期={info['last_period']!r}  "
              f"下批插入位置={info['insert_at']}  数据起始={info['write_start_row']}")


if __name__ == "__main__":
    main()
