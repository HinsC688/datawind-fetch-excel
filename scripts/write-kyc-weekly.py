#!/usr/bin/env python3
"""把 DataWind 抓取的 KYC 触达数据按写入方案写进飞书三个 tab。

设计要点（都是踩过坑总结出来的，别改）：
- 逐行调用 `sheets +cells-set`，不用批量 `--writes`（坑#6：批量会被 SIGKILL）
- 字段一律按中文名从当次响应的 aliasMap 动态反查 uniqueId，不硬编码字段ID（坑#7）
- 只采纳带 detail 筛选、未被 limit 截断、维度不含 p_date 的那个 vizQuery 请求
- 唯一键是 (触达工具, 触达详情, reg_range)，即"流程名称与档位一一对应"，其他档位数据不计入
- 默认 dry-run，必须显式加 --apply 才真正写入

用法：
    # 先看要写什么（不写入）
    python3 scripts/write-kyc-weekly.py --tab push
    python3 scripts/write-kyc-weekly.py --tab all

    # 确认无误后写入
    python3 scripts/write-kyc-weekly.py --tab push --apply
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

CONFIG_PATH = Path("config/write-plan-0812-0817.json")
QUERY_MARKER = "vizQuery/query"

# 2026-09-02 新增：同一个 (触达工具,触达详情,reg_range) 有时会被 DataWind 按
# "业务类型" 拆成多行（同一任务在周期内被打了不同的业务类型标签，属于正常业务口径，
# 不是坑#17那种因未命名维度导致的碎片）。这种情况下要把绝对数值字段求和，
# 比率字段按分子/分母重新计算，不能直接对比率取值或覆盖。
# 分母字段来自实际验证：点击率=点击用户/曝光用户；其余"24hXX率"=对应24h指标/点击用户。
RATE_FORMULAS = {
    "点击率": ("点击用户", "曝光用户"),
    "24h内FTD率": ("触达24h内FTD", "点击用户"),
    "24h内eFTD率": ("触达24h内eFTD", "点击用户"),
    "24h内eFTTc率": ("触达24h内eFTTc", "点击用户"),
    "24h内kyc率": ("触达24h内kyc", "点击用户"),
}
COUNT_FIELDS = ["曝光用户", "点击用户", "触达24h内FTD", "触达24h内eFTD", "触达24h内eFTTc", "触达24h内kyc"]


def column_index(letter):
    result = 0
    for char in letter:
        result = result * 26 + (ord(char.upper()) - 64)
    return result


def index_to_column(index):
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def pick_query(capture_path, date_start, date_end):
    """挑出唯一可用的 vizQuery 请求。

    判据（缺一不可）：
      - 带 detail 筛选（说明是按目标名单查的）
      - p_date 是匹配目标周期的绝对日期范围
      - 维度不含 p_date（整周期聚合，不是按天拆）
      - 未被 limit 截断
      - **(触达工具, 触达详情, reg_range) 唯一键不重复**

    最后这条最关键：界面上筛选的渐进过程中，有些请求会多带一个未命名维度，
    导致同一个业务记录被拆成多行，直接按唯一键取数会取到碎片值。
    要求唯一键不重复，就能排除这类请求。

    2026-09-02 更新：发现同一个 (触达工具,触达详情,reg_range) 有时会被 DataWind
    按"业务类型"合法拆成多行（同一任务在周期内被打了不同业务类型标签，用户已确认
    这是正常口径，下游 build_lookup() 会按业务类型分组求和/重算比率合并）。
    所以这里的重复检测改成按 (触达工具,触达详情,reg_range,业务类型) 四元组去查重，
    这样仍能拦住坑#17那种因未命名维度导致的碎片请求，又不会误杀合法的业务类型拆分。
    """
    bodies = json.loads(Path(capture_path).read_text(encoding="utf-8"))
    candidates, rejected = [], []
    for index, item in enumerate(bodies, start=1):
        if QUERY_MARKER not in (item.get("url") or ""):
            continue
        try:
            parsed = json.loads(item.get("post_data") or "")
        except json.JSONDecodeError:
            continue
        query = parsed.get("query") or {}
        limit = query.get("limit")
        wheres = query.get("whereList") or []

        has_detail = any(where.get("name") == "detail" for where in wheres)
        date_ok = any(
            where.get("name") == "p_date" and where.get("op") == "between"
            and date_start in str((where.get("val") or [""])[0])
            and date_end in str((where.get("val") or ["", ""])[1])
            for where in wheres
        )

        names_by_id = {str(entry.get("id")): entry.get("name") for entry in query.get("dimMetList") or []}
        location_dims = ((query.get("locations") or {}).get("dimensions")) or []
        dim_names = [names_by_id.get(str(entry.get("id") if isinstance(entry, dict) else entry)) for entry in location_dims]
        has_p_date_dim = any((name or "").strip() == "p_date" for name in dim_names)

        viz = ((item.get("body") or {}).get("data") or {}).get("vizData") or {}
        rows = [row for row in (viz.get("datasets") or []) if isinstance(row, dict)]
        truncated = isinstance(limit, int) and len(rows) >= limit

        if not (has_detail and date_ok and not has_p_date_dim and not truncated and rows):
            continue

        # 唯一键去重检查：(触达工具,触达详情,reg_range,业务类型) 四元组不重复即可。
        # 业务类型也纳入键是因为同一任务在周期内可能合法地被打上不同业务类型标签
        # （2026-09-02发现，用户已确认按业务类型分组求和是正确口径），
        # 这种情况不应被当成坑#17的碎片请求拦掉；但三元组内仍不能有更细的重复。
        alias = viz.get("aliasMap") or {}
        key_ids = {}
        for uid, label in alias.items():
            key_ids.setdefault("".join(str(label).split()), uid)
        try:
            ids = [key_ids[name] for name in ("触达工具", "触达详情", "reg_range")]
            biz_uid = key_ids.get("业务类型")
        except KeyError:
            continue
        dedupe_ids = ids + ([biz_uid] if biz_uid else [])
        composites = [tuple(row.get(uid) for uid in dedupe_ids) for row in rows]
        duplicates = len(composites) - len(set(composites))
        if duplicates:
            rejected.append((index, len(rows), duplicates))
            continue
        candidates.append((index, len(rows), viz))

    for index, count, duplicates in rejected:
        print(f"⏭️  跳过请求 #{index}（{count}行）：唯一键有 {duplicates} 处重复，"
              f"说明多带了拆分维度，取数会取到碎片值")

    if not candidates:
        raise SystemExit("❌ 抓取文件里没有符合条件的请求"
                         "（需要：带detail筛选/绝对日期匹配/未截断/无p_date维度/唯一键不重复）")
    if len(candidates) > 1:
        print(f"⚠️  有 {len(candidates)} 个都符合条件 {[c[0] for c in candidates]}，取行数最多的")
    index, count, viz = max(candidates, key=lambda c: c[1])
    print(f"✅ 使用抓取文件里的请求 #{index}（{count} 行，唯一键无重复）")
    return viz


def build_lookup(viz):
    """按 (触达工具, 触达详情, reg_range) 建索引，字段名动态反查。

    2026-09-02 新增：同一个唯一键如果被"业务类型"拆成多行（同一任务在周期内
    被打了不同业务类型标签，属于正常口径），这里会把绝对数值字段求和、
    比率字段按分子/分母重新计算，合并成一行再放进索引（用户已确认这样处理）。
    """
    alias = viz.get("aliasMap") or {}
    name_to_id = {}
    for uid, label in alias.items():
        # 中文名里可能夹杂不可见空格，统一去空格后再建索引（坑#7）
        name_to_id.setdefault("".join(str(label).split()), uid)

    def field(label):
        key = "".join(label.split())
        if key not in name_to_id:
            raise SystemExit(f"❌ 响应的 aliasMap 里找不到字段 {label!r}；实际有: {sorted(set(alias.values()))}")
        return name_to_id[key]

    keys = {name: field(name) for name in ("触达工具", "触达详情", "reg_range")}
    grouped = {}
    for row in viz.get("datasets") or []:
        if not isinstance(row, dict):
            continue
        composite = tuple(row.get(keys[name]) for name in ("触达工具", "触达详情", "reg_range"))
        grouped.setdefault(composite, []).append(row)

    lookup = {}
    merged_report = []
    for composite, rows in grouped.items():
        if len(rows) == 1:
            lookup[composite] = rows[0]
            continue
        merged = dict(rows[0])  # 保留非数值字段（如业务类型，取第一条，不用于写入）
        for count_name in COUNT_FIELDS:
            try:
                uid = field(count_name)
            except SystemExit:
                continue
            total = sum(to_number(row.get(uid)) or 0 for row in rows)
            merged[uid] = total
        for rate_name, (numerator_name, denominator_name) in RATE_FORMULAS.items():
            try:
                rate_uid = field(rate_name)
                num_uid = field(numerator_name)
                den_uid = field(denominator_name)
            except SystemExit:
                continue
            denominator = merged.get(den_uid) or 0
            merged[rate_uid] = (merged.get(num_uid) or 0) / denominator if denominator else 0
        lookup[composite] = merged
        merged_report.append((composite, len(rows)))

    if merged_report:
        print(f"ℹ️  {len(merged_report)} 个唯一键在本次抓取里按业务类型拆成了多行，已合并累加（用户已确认口径）：")
        for composite, count in merged_report:
            print(f"      {composite} ×{count}")

    return lookup, field


def to_number(raw):
    """DataWind 返回的都是字符串，转成数值；空/占位符返回 None。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if text in ("", " ", "-", "null", "None"):
        return None
    try:
        value = float(text)
    except ValueError:
        return text
    return int(value) if value.is_integer() else value


def build_rows(tab_config, lookup, field, period_label):
    """生成每一行的单元格值，返回 (行内偏移, 值列表, 缺失标记)。"""
    layout = tab_config["column_layout"]
    letters = sorted(layout, key=column_index)
    first, last = letters[0], letters[-1]

    prepared, missing = [], []
    for offset, spec in enumerate(tab_config["rows"]):
        composite = (tab_config["tool"], spec["detail"], spec["reg_range_dw"])
        source = lookup.get(composite)
        if source is None:
            missing.append(f"{spec['detail']} / {spec['reg_range_dw']}")
            continue

        values = []
        for letter in letters:
            role = layout[letter]
            if role == "period_label":
                # 只有每组第一行写周期标签，其余留空（跟表里现有写法一致）
                values.append(period_label if offset == 0 else "")
            elif role == "tool":
                values.append(tab_config["tool"])
            elif role == "detail":
                values.append(spec["detail"])
            elif role == "label_reg_range":
                values.append(spec["labels"]["reg_range"])
            elif role == "label_uj_type":
                values.append(spec["labels"]["uj_type"])
            elif role == "label_is_kyc_show":
                values.append(spec["labels"]["is_kyc_show"])
            elif role == "":
                values.append("")
            else:
                values.append(to_number(source.get(field(role))))
        prepared.append((offset, values))
    return prepared, missing, first, last, letters


def write_row(token, sheet_id, first, last, row_number, values, apply_changes):
    cells = [[{"value": value} for value in values]]
    command = [
        "npx", "--yes", "@larksuite/cli@latest", "sheets", "+cells-set",
        "--spreadsheet-token", token,
        "--sheet-id", sheet_id,
        "--range", f"{first}{row_number}:{last}{row_number}",
        "--cells", json.dumps(cells, ensure_ascii=False),
    ]
    if not apply_changes:
        command.append("--dry-run")
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"   ❌ 第{row_number}行写入失败: {result.stderr.strip()[:300]}")
        return False
    return True


def is_merged_region_error(stderr_text):
    return "inside a merged region" in stderr_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tab", required=True, choices=["push", "邮件", "弹窗", "all"])
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--apply", action="store_true", help="真正写入；不加则只打印预览")
    parser.add_argument("--start-row", type=int, default=None,
                        help="覆盖配置里的写入起始行（弹窗需要插入行后手动指定）")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    token = config["spreadsheet_token"]
    period_label = config["period_label"]
    capture = config["capture"]

    viz = pick_query(capture["file"], capture["date_start"], capture["date_end"])
    lookup, field = build_lookup(viz)

    tabs = ["push", "邮件", "弹窗"] if args.tab == "all" else [args.tab]
    any_missing = False

    for tab_name in tabs:
        tab_config = config["tabs"][tab_name]
        prepared, missing, first, last, letters = build_rows(tab_config, lookup, field, period_label)

        start_row = args.start_row if (args.start_row and len(tabs) == 1) else tab_config.get("write_start_row")
        print(f"\n{'=' * 78}")
        print(f"【{tab_name}】sheet_id={tab_config['sheet_id']}  列范围 {first}~{last}")
        if tab_config.get("needs_row_insert") and not args.start_row:
            print(f"  ⚠️  该tab需要先插入行（App区块末尾第{tab_config['app_block_last_row']}行之后，"
                  f"第{tab_config['web_header_row']}行是'Web/H5'标题，不能覆盖）")
            insert_count = len(tab_config["rows"]) + 1
            insert_at = tab_config["blank_separator_row"]
            print(f"      插入命令（--inherit-style before 会继承前一行数据行的样式，"
                  f"这样百分比/千位分隔符格式自动带上，见坑#19）：")
            print(f"        npx --yes @larksuite/cli@latest sheets +dim-insert \\")
            print(f"          --spreadsheet-token {token} --sheet-id {tab_config['sheet_id']} \\")
            print(f"          --position {insert_at} --count {insert_count} --inherit-style before")
            print(f"      插完数据行是 {insert_at + 1}~{insert_at + len(tab_config['rows'])}，"
                  f"用 --start-row {insert_at + 1} 再跑一次")
            start_row = None

        if missing:
            any_missing = True
            print(f"  ❌ 有 {len(missing)} 行在抓取数据里找不到对应记录:")
            for item in missing:
                print(f"      - {item}")

        print(f"  准备写入 {len(prepared)} 行" + (f"，起始行 {start_row}" if start_row else "（起始行未定）"))
        # 逐行纵向打印，避免中文宽度导致表格错位
        for offset, values in prepared:
            row_number = (start_row + offset) if start_row else "??"
            pairs = []
            for letter, value in zip(letters, values):
                role = tab_config["column_layout"][letter]
                if role in ("", "period_label", "tool") and not value:
                    continue
                if isinstance(value, float):
                    shown = f"{value:.6g}"
                elif value is None:
                    shown = "(空)"
                else:
                    shown = str(value)
                pairs.append(f"{letter}={shown}")
            print(f"    行{row_number}: " + "  ".join(pairs))

        if not args.apply:
            print("  ℹ️  dry-run，未写入。确认无误后加 --apply")
            continue
        if not start_row:
            print("  ⏭️  起始行未确定，跳过写入")
            continue

        # 2026-09-02 新增：A 列(period_label)在整组行里是纵向合并单元格
        # （跟坑#22类似情况：只有组内第一行能写这一列，非首行写A会被飞书拒绝
        # "inside a merged region"）。做法：offset==0 正常写 first~last；
        # offset>0 时把写入范围起点从 first 挪到第二列，跳过合并的 A 列。
        merged_first_col = tab_config["column_layout"].get(first) == "period_label"
        second = letters[1] if len(letters) > 1 else first

        ok = 0
        for offset, values in prepared:
            row_number = start_row + offset
            if offset > 0 and merged_first_col:
                success = write_row(token, tab_config["sheet_id"], second, last, row_number, values[1:], True)
            else:
                success = write_row(token, tab_config["sheet_id"], first, last, row_number, values, True)
            if success:
                ok += 1
                print(f"   ✅ 已写入第 {row_number} 行")
        print(f"  写入完成: {ok}/{len(prepared)} 行成功")

    if any_missing:
        print("\n⚠️  存在缺失记录，请先核对再写入。")
        sys.exit(1)


if __name__ == "__main__":
    main()
