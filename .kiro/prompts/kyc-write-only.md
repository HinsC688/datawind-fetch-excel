执行 KYC 週度數據同步的【写入段】。数据已经抓取并校验完毕，你只需要把本地已有的抓取结果写进飞书三个 tab，不要再抓取、不要切换网络、不要重新跑 DataWind。请先读 HANDOFF.md 第 7A 节了解业务口径和坑，尤其是坑 #19（插行继承格式）、坑 #22/#24（合并单元格、A列纵向合并）、坑 #6（逐行写不用批量）。

**本期参数（0909-0915，2026-09-16 周三运营）：**
- 写入方案配置：`config/write-plan-0909-0915.json`
- 抓取结果：`artifacts/20260916T113720-response-bodies.json`（71行，已校验：vizQuery/query、绝对日期、无 p_date 维度、唯一键无重复、total=atLeast=71 未截断）
- 周期标签：`0909-0915`
- spreadsheet_token：`Wmq4s0mJHh3HZ7tITvbu261ZsFb`

**第0步：确认飞书 CLI 登录有效**
`npx --yes @larksuite/cli@latest auth status`，确认 user identity ready、token valid。失效则 `auth login --recommend`。

**第1步：dry-run 复核（必做）**
`python3 scripts/write-kyc-weekly.py --tab all --config config/write-plan-0909-0915.json`
预期结果（跟这个对不上就停下来问用户，不要写）：
- push：21 行匹配，1 行缺失 `中台-已注册未KYC-90日后-0716 / 注册30天以上`（用户已确认照旧跳过）
- 邮件：20 行匹配，同样缺失 `90日后-0716`（照旧跳过）
- 弹窗 App：8 行全部匹配
这批是**追加新一期**（三个 tab 最后一组都是 0902-0908），不是覆盖。

**第2步：逐个 tab 先插带格式的行，再写入。顺序 push → 邮件 → 弹窗。**
每个 tab 都是"先 +dim-insert 插行（继承格式）→ 再 write-kyc-weekly.py --apply 写入 → 回读验证"。

push（sheet_id BHIztA）：
  1) 插行：`npx --yes @larksuite/cli@latest sheets +dim-insert --spreadsheet-token Wmq4s0mJHh3HZ7tITvbu261ZsFb --sheet-id BHIztA --position 207 --count 22 --inherit-style before`
  2) 写入：`python3 scripts/write-kyc-weekly.py --tab push --config config/write-plan-0909-0915.json --start-row 208 --apply`
     （数据写在 208-228，21行，跳过缺失行）

邮件（sheet_id 6kyjFR）：
  1) 插行：`npx --yes @larksuite/cli@latest sheets +dim-insert --spreadsheet-token Wmq4s0mJHh3HZ7tITvbu261ZsFb --sheet-id 6kyjFR --position 198 --count 21 --inherit-style before`
  2) 写入：`python3 scripts/write-kyc-weekly.py --tab 邮件 --config config/write-plan-0909-0915.json --start-row 199 --apply`
     （数据写在 199-218，20行，跳过缺失行）

弹窗 App（sheet_id b45c2f，注意第125行是 Web/H5 标题，绝不能覆盖）：
  1) 插行：`npx --yes @larksuite/cli@latest sheets +dim-insert --spreadsheet-token Wmq4s0mJHh3HZ7tITvbu261ZsFb --sheet-id b45c2f --position 124 --count 9 --inherit-style before`
  2) 写入：`python3 scripts/write-kyc-weekly.py --tab 弹窗 --config config/write-plan-0909-0915.json --start-row 125 --apply`
     （数据写在 125-132，8行；Web/H5 标题会被下移到第 134 行）

注意：write-kyc-weekly.py 已内置处理 A 列纵向合并（非首行自动跳过 A 列，从第二列开始写），不需要手动分两次调用。逐行写入模式已固化，不要改成批量 --writes（坑#6）。

**第3步：回读验证（每个 tab 都要）**
用 `+csv-get` 读回写入区域，确认：
- A 列首行标签是 `0909-0915`
- 百分比列显示成 `X.XX%` 而不是裸小数 `0.0248`
- 整数列有千位分隔符
- 弹窗 tab 确认 Web/H5 标题完好（在第134行）
push 读 `A206:J230`、邮件读 `A197:Q220`、弹窗读 `A122:M136`。

**第4步：更新文档**
把本期周期、写入行号（push 208-228 / 邮件 199-218 / 弹窗 125-132）、缺失行、量级环比追加到 HANDOFF.md 第7A节，并把"下一期"更新为 `0916-0922`（最早 2026-09-23 周三运行）。

全程遵守：写入前已 dry-run；只做追加不做删除/清空；任何数值或匹配跟上面预期对不上就停下来问用户，不要自己猜。
