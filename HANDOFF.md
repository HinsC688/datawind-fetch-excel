# 交接文档：DataWind → 飞书 自动化抓取项目

写给完全没有上下文的新对话看。请先整体读完这份文档，再动手操作，避免重复踩坑。

---

## 1. 任务目标

把 DataWind 看板（Push 任务明细表）里的数据，定期同步到飞书的两个表格 tab：

1. **周度数据汇总**：每周更新一批指定任务的周度指标。
2. **日纬度数据**（尚未开始）：更新"上周"的每日明细数据。

因为业务流程和网络环境限制，这不是一次性任务，而是**每周要重复执行**的流程。目标是先手动跑通一次完整链路，再考虑自动化排程。

### 需要抓取的指标（针对每个 task_name）
- 曝光用户数的日均（含环比）
- 点击用户数的日均（含环比）
- 曝光点击率（含环比）
- 注册转化用户数（含环比）
- 注册转化率（含环比）

### 飞书目标位置
- 文档：`PUSH 數據分析和優化` 副本
  - Wiki 链接：`https://l7jipx1bfq.larksuite.com/wiki/Ah29w0uDOicRpCkCtXVus268szd`
  - **spreadsheet_token: `AmzgsAmgNheVVGtcEHQuh2FYsic`**（这是最终确认的写入目标，不是原始表）
- 周度 tab：`周度数据汇总（0803-0906）`
  - **sheet_id: `QUgpS2`**
  - 列结构（A1:H1）：`流程名称,周,任务名称,曝光日均,点击用户日均,曝光点击率,注册转化率,日均注册人数`
  - 每个任务占 6 行模板（1 行数据 + 5 行同任务不同周 + 1 空行分隔），任务名称写在 C 列，流程名称只在每组第一行的 A 列
- 原始参照 tab（不要动）：`周度数据汇总（0629-0802）`，sheet_id `wrsQiu`，在同一个原始文档里（token `WuBQstPzDhWRG7taBotuR6besmf`），不是本次写入目标

### DataWind 看板信息
- 目标看板 URL：`https://datawind.xiaoxiame.com/bi/pages/dashboard/41204?appId=2&sheetId=15473`
- 看板名称：**触达业务价值转化**
- 目标组件：Push 任务明细表，`reportId=286219`
- 真实数据接口：`POST /bi/aeolus/vqs/api/v2/vizQuery/query`
- 关键字段（DataWind 内部字段名，不是中文名）：
  - `task_name`、`workflow_name`、`workflow_id`、`node_id`、`push_type`、`p_date`
  - 指标字段的中文名和 ID 映射保存在响应的 `vizData.aliasMap` 里，每次抓取都要重新读取，不能硬编码 ID（不同请求里同名指标的 ID 可能不同，见坑 #7）

---

## 2. 目前已完成的进度

1. **GitHub 仓库**已建好并连接：`https://github.com/HinsC688/datawind-fetch-excel.git`，本地已配置自动 commit（见坑 #1）。
2. **飞书 lark-cli** 已安装（用 `npx --yes @larksuite/cli@latest ...` 调用，不需要全局安装），App 已配置，用户 `Avery B` 已登录授权，可以读写飞书表格。
3. 已验证目标飞书副本文档可访问、可读、可写。
4. 已确认 DataWind 的真实查询接口、字段结构、`task_name` 与飞书任务名称的匹配关系。
5. **W31（2026-08-03~08-09）周度数据已成功写入**飞书 `周度数据汇总（0803-0906）` tab：
   - 24 行任务数据已写入并回读验证通过（行号：2,8,14,20,26,32,50,56,62,68,74,80,86,92,98,104,110,116,122,128,134,140,146,152）
   - 第 **38、44 行按用户要求保留空白**（原因见下方"未解决问题"）
6. 相关脚本已写好并验证可用：
   - `scripts/open-chrome.sh`：启动带 CDP 调试端口（9222）的独立 Chrome，用于抓包，不影响日常 Chrome。
   - `scripts/capture-datawind-requests.py`：监听 Chrome CDP 网络请求，把结果存到 `artifacts/`（带时间戳文件名，不会互相覆盖）。
   - `scripts/write-weekly-w31.py`：读取抓取结果 + 飞书模板，逐行写入飞书（已验证可用的正式写入方式，见坑 #6）。

---

## 3. 尚未解决的问题（新会话需要先处理）

### 问题 A：第 38、44 行数据缺失
飞书模板里这两行的配置是：

| 行 | 流程名称 | 任务名称 |
|---|---|---|
| 38 | 中台未注册-非首次-Airdrop-0803-延时90s/2min-App | 中台未注册-非Paid-非首次-airdrop-0805-App-深链 |
| 44 | 中台未注册-非首次-Airdrop-0803-延时90s/2min-App | 中台未注册-Paid-非首次-airdrop-0805-App-深链 |

DataWind W31 数据里，这两个 task_name 实际关联的 workflow_name 是 `中台未注册-非首次后第2日airdrop-0803-App`（或旧版本 `...-0512-App`），跟模板里写的流程名称不一致，所以精确匹配（流程名称+任务名称）找不到数据。

用户已确认的规则：**这两行本次先留空，不要用其他流程的数据填充**。但没有说明以后怎么处理——新会话如果要继续做后续周（W32 及之后），需要跟用户确认这两行是否要修正流程名称，或者是长期保持空白。

### 问题 B：日纬度 tab 还没做
用户要的第二部分需求——"更新上周的日纬度数据到日纬度表格 tab"——完全没开始。当前连"日纬度 tab"都还没创建。需要重新跟用户确认：
- tab 名称、列结构（用户之前提供的旧文档模板里有参考格式，是"流程名称,任务名称,日期,曝光/收到,点击/开信,点击率/开信率,FTD,EFTD..."这种结构，但这次的实际需求还没细化）
- 具体要不要参考 `PUSH 數據分析和優化` 副本里的其他 tab 结构

### 问题 C：抓取范围要不要扩展到 W32 之后
本次只做了 W31。后续每周怎么运作、要不要做成自动化脚本一键跑完"抓取+写入"，还没定。

---

## 4. 关键操作流程（照做即可）

### 抓取 DataWind 数据（必须在 FortiVPN 环境下）
```bash
cd "/Users/newair/Desktop/datawind fetch excel"
bash scripts/open-chrome.sh   # 打开独立CDP Chrome，自动导航到目标看板
```
然后在弹出的 Chrome 里登录 DataWind，进入 Push 任务明细表，把时间筛选设置为目标周（比如 W31 是 2026-08-03~08-09，注意用绝对日期筛选，不要用"最近7天"之类的相对筛选，否则周期会随时间漂移，见坑 #4）。

开新终端窗口：
```bash
cd "/Users/newair/Desktop/datawind fetch excel"
python3 scripts/capture-datawind-requests.py --no-reload --seconds 120
```
看到 `Listening now...` 提示后，回到 Chrome 点击表格刷新，等待脚本自动结束（120秒）。

结果会保存为：
```
artifacts/{时间戳}-network-events.json
artifacts/{时间戳}-response-bodies.json
```

### 写入飞书（必须在 Clash 环境下，见坑 #2）
先读取当前飞书 tab 模板（确认任务清单/行号，因为用户可能随时改动）：
```bash
npx --yes @larksuite/cli@latest sheets +csv-get \
  --spreadsheet-token "AmzgsAmgNheVVGtcEHQuh2FYsic" \
  --sheet-id "QUgpS2" --range "A1:H339" \
  --output-path "artifacts/weekly-template-latest.json"
```

然后用 `scripts/write-weekly-w31.py` 作为模板改写新脚本（这个脚本名字里带 W31 是历史遗留，实际逻辑是通用的：读模板 → 匹配 DataWind 响应 → 逐行写入）。执行示例：
```bash
python3 scripts/write-weekly-w31.py \
  --template artifacts/weekly-template-latest.json \
  --response artifacts/{时间戳}-response-bodies.json \
  --spreadsheet-token "AmzgsAmgNheVVGtcEHQuh2FYsic" \
  --sheet-id "QUgpS2" \
  --apply
```
不带 `--apply` 时是 dry-run，不会真正写入，**每次正式写入前务必先跑一次 dry-run 确认**。

---

## 5. 踩过的坑，新会话绝对不要重复踩

### 坑 #1：FortiVPN 和 Clash 不能同时连接
这是本项目最大的环境限制。DataWind 是公司内网（走 FortiVPN），飞书/GitHub 走的是 Clash 代理。两者互斥，切换网络时 Kiro 对话本身也可能断线。**解决方案**：把流程拆成两段，FortiVPN 下只做"抓取并存本地 JSON"，切回 Clash 后再"读本地 JSON 写飞书"，两段之间互不依赖网络状态。永远不要设计成"一个脚本里同时抓取和写入"，会在网络切换时失败。

### 坑 #2：自动 Git commit hook 在 FortiVPN 下会报错，这是正常现象
项目配置了 `agentStop` 触发的自动 commit+push hook（见 `.kiro/hooks/auto-commit-changes.kiro.hook`）。已经修复为：GitHub push 失败时不会导致 hook 报错，只会在本地 commit 成功后提示"稍后同步"。如果看到类似 `LibreSSL SSL_connect... unable to access github.com` 的报错，**不要慌，本地提交通常已经成功**，恢复 Clash 后下次 hook 触发会自动补推。

### 坑 #3：Shell 脚本里中文全角符号紧跟变量名会报错
`scripts/open-chrome.sh` 曾经因为 `$PORT）`（全角右括号紧贴变量名）导致 bash 把 `）` 当成变量名一部分，报 `unbound variable`。**教训**：shell 脚本里变量后面如果紧跟非空格字符（尤其是中文标点），一定要用 `${VAR}` 显式加大括号，不能直接写 `$VAR）`。

### 坑 #4：DataWind 的周筛选不要用相对时间
第一次抓取时看板用的是"最近7天"这种相对筛选（`op: lastSync`），抓到的是本周未完整结束的 W32，不能用。必须在 DataWind 里手动切换成**绝对日期范围**筛选（`op: between`），才能抓到完整、稳定的历史周数据。判断方法：抓取后检查请求体里 `query.whereList` 的 `op` 字段，`lastSync` 是相对时间（危险），`between` 才是绝对时间（安全）。

### 坑 #5：DataWind 单次查询有 1000 行上限，返回里会提示真实总量
第一次抓取时 `limit=1000`，但响应里 `atLeast: 4455` 说明实际有 4455+ 条记录，返回被截断了。**如果要抓全量数据（而不是按飞书模板里几十个指定任务筛选），必须处理分页/翻页，不能默认第一页就是全部**。本项目目前的做法是：飞书模板本身只追踪约20个指定任务（不是全部Push任务），所以从截断的1000条里做精确匹配是够用的，但如果以后需求变成"抓取全部任务"，这里要重新设计分页逻辑。

### 坑 #6：飞书 CLI 的批量写入 `--writes` 在本机环境会被 SIGKILL 杀掉
无论是一次性提交48个写入区域，还是分批8个一组提交，`npx @larksuite/cli sheets +cells-set --writes '[...]'` 都会在提交阶段被系统杀死进程（`Signals.SIGKILL`），原因未查明（可能是长命令行参数在某些系统上触发限制）。**每次被杀之前都要先回读验证是否已经部分写入**（本项目验证结果是：SIGKILL发生时飞书没有收到任何写入，是安全失败）。

**唯一验证有效的写入方式**：改成单行单次调用 `+cells-set --range "B{row}:H{row}" --cells '[[...]]'`（不用 `--writes` 批量参数，一次只写一行的 B 到 H 列），这样每次调用参数量小，全部24行逐次调用均成功。`scripts/write-weekly-w31.py` 已经改成这种逐行写入模式，**新会话不要改回批量 `--writes` 模式**，除非先在小范围重新验证过。

### 坑 #7：DataWind 指标的字段 ID 不是固定的，必须用别名表动态查找
一开始尝试直接硬编码字段 ID（比如 `1675528`、`avgday_1675528_1675586`）来取值，后来发现同一个指标"点击用户数的日均"在不同请求返回里，字段 ID 有时对应到 `avgday_1675528_1675585`，中文名字符串里还可能夹杂不可见空格导致按名称匹配失败（`点击用户数的 日均` vs `点击用户数的日均`）。**正确做法**：每次都从当次响应的 `vizData.aliasMap`（字段ID→中文名）动态反查，绝不要跨请求复用硬编码的字段 ID 或依赖中文字符串精确匹配。`scripts/write-weekly-w31.py` 里的 `field_id = {...}` 就是这样动态构建的，参考它的写法。

### 坑 #8：本地临时 JSON 文件容易在工具间产生冲突和损坏
之前尝试手写大段 JSON 到 `artifacts/w31-lark-writes.json`，先后遇到：文件被写入了不相关的乱码文本（怀疑是工具调用参数拼接错误）、编辑器和写入工具对同一文件产生"内容较新"冲突、文件写入后变成 0 字节。**教训**：不要用 fs_write/fs_append 拼接大段手写 JSON 作为中间产物，改成用 Python 脚本在内存里直接构建请求并直接调用 CLI（即 `write-weekly-w31.py` 的做法），完全不落地这种大 JSON 文件，从根本上避免这类冲突。

### 坑 #9：飞书新建 tab 用"复制"而不是"新建空表"
新 tab `周度数据汇总（0803-0906）` 是用 `sheets +sheet-copy` 复制旧 tab `周度数据汇总（0629-0802）` 得到的（保留列结构、格式、合并单元格），然后用 `+cells-clear --scope content` 只清空内容、保留格式。**不要用新建空表再手动搭格式**，容易和原表结构不一致导致后续写入错位。

### 坑 #10：不要把 DataWind 返回的全部任务/流程都写进飞书
DataWind 一次返回上千条各种任务的数据，但飞书模板只想追踪其中一小部分（大约20个 task_name，由用户在飞书表格里手动维护，跟着模板走）。**永远以飞书 tab 里当前已经填写的"流程名称+任务名称"这两列作为唯一的追踪范围来源**，去 DataWind 结果里做精确匹配，不要反过来用 DataWind 的全量结果去决定要写哪些任务。用户在对话里明确说过"以这个tab的流程名称和任务名称为准"。

### 坑 #11：同一个 task_name 可能对应多个 workflow_name，这是正常的业务逻辑
不要把它当成数据异常去"去重"或"合并"。必须用 `流程名称(workflow_name) + 任务名称(task_name)` 两个字段一起作为唯一键去匹配，单独用 task_name 匹配会匹配到多条不该合并的记录。

### 坑 #12：用户发的截图链接可能是过期的教程示例，不要重复使用
对话中用户多次发送一张 `open.feishu.cn/page/cli?user_code=HPP7-2C5D...` 的截图。经确认这是 lark-cli 官方文档/教程里的示例截图（配置码格式说明），不是本项目当前有效的授权链接。**当前项目真实使用的 App ID 和登录状态已经配置完成**（见下方"当前授权状态"），如果新会话又看到类似截图，先确认是不是文档示例，不要盲目重新走一遍配置流程。

---

## 6. 当前授权状态（供检查用，不要重新配置除非失效）

- 飞书 App ID: `cli_aafb0047f1385db8`
- 登录身份: 用户 `Avery B`（`ou_5fbd8bf917e8fb7f9aa59a04b1c1219f`）
- Token 有效期: 到 2026-08-13（refresh 到 2026-08-20）——**新会话时间是否已经过期需要先检查**，如果过期需要重新跑：
  ```bash
  npx --yes @larksuite/cli@latest auth status
  # 如果失效：
  npx --yes @larksuite/cli@latest auth login --recommend
  ```
- GitHub 远程: `https://github.com/HinsC688/datawind-fetch-excel.git`，分支 `main`，已有自动 commit hook。

---

## 7. 建议新会话的第一步

1. 检查飞书 CLI 登录状态是否还有效（`auth status`）。
2. 读一遍这份文档全文，尤其是第3节"尚未解决的问题"和第5节的坑。
3. 跟用户确认：
   - 第 38、44 行怎么处理（留空到什么时候，还是需要修正流程名称）；
   - 日纬度 tab 的具体需求（列结构、tab 命名规则）；
   - 是否要开始处理 W32 及后续周期，还是继续做日纬度。
4. 不要重新发起飞书 App 配置流程（已完成），除非 `auth status` 显示确实过期。
