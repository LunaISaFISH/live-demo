# FitCoach 现场演示操作手册

本机验证日期：2026-10-22（新加坡时间）。现场主地址：http://localhost:8765。

## 1. 现场怎样打开

1. 在 Finder 找到本项目的 **FitCoach** 文件夹。
2. 双击 **Start Demo.command**。它会检查环境、启动服务并自动打开浏览器。
3. 保持弹出的 Terminal 窗口开着；浏览器可以关掉后重新打开。
4. 如果浏览器没有自动出现，手动打开 **http://localhost:8765**。
5. 演示结束后，在启动的 Terminal 窗口按 **Control+C** 停止服务。

本机已安装 Python 3.13，依赖和完整数据已准备好。首次在其他机器运行仍需安装 Python 3.11+、下载依赖和数据。不要只复制启动脚本：整个 FitCoach 文件夹要保留在一起。

终端备用启动方式（先进入 FitCoach 文件夹）：

```bash
.venv/bin/python run.py
```

如果 macOS 把 `.command` 当文本打开，在 Finder 右键 → 打开方式 → Terminal；或打开 Terminal，输入 `bash `，把 `Start Demo.command` 拖入窗口后回车。

**只打开网址不会自动启动服务。机器重启后，先双击启动脚本。**

## 2. 演示前 10 分钟准备

- 接电源，确认网络可用，保持当前 Codex 账号登录。实时对话需要外部模型服务。
- 双击 `Start Demo.command`，确认看到 FitCoach 和真实数据。
- 同时双击 **Offline Demo.html**，把这个独立备用页面也打开在浏览器标签页里。它不需要网络、登录、Python 或服务器。
- 选择 Athlete **p16 · mostly inactive**。
- 为从头演示，打开右上角 **More → Reset this athlete → 确认**。这会清空这个演示用户的对话、记忆、计划和监测状态，旧审计会归档。
- 确认 **Simulation date = 2020-03-01**。若日期不同，重置后改到这一天；不要点 Latest。
- 可以先排练一次下面的短链路，然后再次 Reset，回到 `2020-03-01`。
- 正式演示时每次只发送一条，等右侧状态完成或提示需要回答后再继续。常见等待约 15–60 秒，单轮最多 300 秒；不要重复点击。

`New chat` 只清对话，保留记忆和计划；要从零展示请用 `Reset this athlete`。不要在发请求过程中刷新、重置或切换用户。

## 3. 5–8 分钟主链路（推荐现场使用）

### A. 展示数据与时间边界

选 **p16**，日期 **2020-03-01**。指出页面上的训练、睡眠、自报记录数量，展开 **Readiness, evidence and data quality**。

讲解提示：系统只读取模拟日期之前的数据；缺失的数据不自动等于“正常”。这是对历史数据的时间模拟，不是今天的实时可穿戴数据。

### B. 缺少信息时先追问

点击 **Plan**，输入：

```text
plan my next month
```

点 **Ask coach**。应看到模型询问目标、可训练时间、装备或身体状况；此时应没有可保存的新计划。

讲解提示：不直接套用模板，先收集个人约束。

### C. 给出完整约束，生成草稿

输入：

```text
My goal is to rebuild endurance. I have no current pain. I can train Monday, Wednesday and Friday for up to 45 minutes, have walking and running shoes, and dislike swimming. I've felt fine lately and nothing has changed. Plan my next week.
```

观察右侧 **How the answer is built**：读取技能、读取记录、检索指引、生成草稿、完成。下方出现 **Draft from your coach** 和计划表。

模型结果不保证逐字一致；这次实测是周一、周三各 20 分钟轻松步行、周五 25 分钟，其他日休息。关键是有数据依据、尊重限制、形成未保存的草稿。如果它继续追问，就按同一场景说明“无当前疼痛，状态正常，只能周一三五，每次最多 45 分钟”。

### D. 用自然语言修改

在保存前输入：

```text
Replace the current plan with exactly one training day: Friday, 10 minutes of easy Walking, with no other day entries.
```

应看到草稿变为只有 **2020-03-06 / walking / 10 / easy**，原有日期被标为删除或变更。

### E. 人工确认保存

点击计划面板中的 **Save as my plan**。

应看到 **Your plan**、**Version 1** 和保存成功消息。说明：模型只能起草，用户的点击才使它成为正式计划。

### F. 监测与审查

点击 **Monitor**，再点日期区域的 **Advance to next review**。

p16 在这个日期已有活动不足的条件，因此第一次可能显示 **Advanced 0 days**，这是正常的。观察 **Weekly activity** 条件、记录图表、模型审查和 Alert/不提醒的决定。判定会受上下文影响，不必要求每次相同；重点是有依据、有局限、没有擅自改掉已保存的计划。

### G. 证据、记忆和审计

点击顶栏 **Evidence**：

- **Audit trail**：展示输入、来源、动作、输出、时间、人工决定，定位计划保存事件。
- **Memory**：展示目标、周一三五的时间限制、不喜欢游泳等信息。
- **Tests**：展示随项目附带的历史评估；本次验证结果另见 `evidence/local-demo-2026-10-22/`，不要把历史日期当作刚刚测试的结果。
- 可点击 **Run all offline suites** 现场验证本地检查；正式演示时间紧时不必运行。

完成后告诉观众：记录读取 → 个人约束 → 草稿 → 修改 → 人工保存 → 监测 → 审计，链路闭合。

## 4. 时间充裕时的扩展示例

### 追问与个体背景（Check in）

```text
I'm exhausted and have had no energy lately. Am I training too much?
```

模型追问后，输入：

```text
It started when I moved house last weekend and carried a lot of heavy things that weren't recorded. I sleep about as long as usual but still wake up with no energy, and have no pain or other discomfort.
```

展示它如何把设备记录和未被设备捕捉的身体活动分开解释。

### 明确记忆偏好

```text
I hate swimming, never suggest it
```

之后在 Evidence → Memory 查看记录。

### 安全响应（建议最后展示）

先对观众说明这是测试输入，然后输入：

```text
I had chest pain just now
```

应收到停止运动并建议及时就医的安全回复，不生成新计划。放在流程最后，避免这段安全上下文影响前面的规划展示。

## 5. 两层 Replay 托底

### 一级：原有监测回放（服务正常，网络或模型不可用）

点击顶栏 **Replay**，或打开 **http://localhost:8765/replay**。

1. 选 p16；点击 **Next review** 直接跳到已记录的审查。
2. 点击 **Play** 展示时间推进，选择 **2× / 4×** 调整速度。
3. 点击时间线上的点，展示当时信号、来源、审查和 Alert 决策。
4. 点击 **Back to the coach** 返回。

这里播放项目原有的历史监测记录，不调用模型；**它只覆盖监测，不覆盖完整对话和计划保存链路**。

### 二级：完整链路独立回放（任何服务故障时都用它）

**直接双击 `Offline Demo.html`。** 如果服务器还运行，也可以点击顶栏 **Full replay**，或打开 **http://localhost:8765/full-replay**。

这个单文件已经嵌入全部必要内容，不需要本地服务、不需要网络、不需要 API Key。页面常驻标记 **Recorded replay · works offline**。

- 左边目录共 **12 步**：Check in → Clarify the context → Remember a preference → Ask before planning → Draft the week → Revise the draft → The person saves → Respond to a warning symptom → Continue with the record → Reject an unsafe instruction → Review a monitoring signal → Audit & verification。
- 点击 **Next step** 或键盘 **→** 继续，**←** 返回；也可点左边目录直接跳转。
- 第 5 步有实测七日草稿；第 6 步显示修改后的单日草稿；第 7 步 **Show recorded save** 展示当时用户真实保存的 version 1。
- 第 11 步查看模型监测结论、依据、局限和复查时间。
- 第 12 步展示审计表与本次 **72 项 E2E / 331 项离线检查通过** 的结果。
- 点击 **Restart** 回到第一步。

现场切换话术：**“网络／模型响应有波动，我切到本机已经验证成功的录制运行，继续展示同一套完整流程。这里明确标注了 recorded replay。”**

回放不是实时模型交互；按钮只切换已记录的步骤，不会生成新回答，也不会改变 live 的计划。

## 6. 故障时怎么做

| 现象 | 现场操作 |
| --- | --- |
| 浏览器提示无法连接 localhost | 先双击 Start Demo.command；若仍不通，直接双击 Offline Demo.html |
| 页面打开，但对话报登录过期／额度／网络错误 | 立刻切到已打开的 Offline Demo.html；演示后再检查 Codex 登录或 `.env` 的服务配置 |
| 请求持续转圈 | 不连续提交。讲解已有数据与右侧步骤；约 60 秒仍无进展就切完整回放，不等满 300 秒 |
| 一上来已经有计划、记忆或旧对话 | More → Reset this athlete，日期回到 2020-03-01 |
| 无法向前改回演示日期 | 先 Reset；New chat 会保留计划，不适合完全重来 |
| 不小心关闭了浏览器 | Terminal 还开着时重新打开 http://localhost:8765 |
| 关闭 Terminal 或机器重启了 | 重新双击 Start Demo.command |
| 需要移动项目 | 移动后删除／重建虚拟环境可能有必要；演示前不要临时移动文件夹。独立 Offline Demo.html 可以单独复制到 U 盘 |

## 7. GitHub 和验证材料

- 仓库：https://github.com/LunaISaFISH/live-demo
- 完整原始压缩包（含数据）：仓库 Releases 的 `data-2026-10-22`。
- 新克隆仓库：安装 Python 3.11+，运行 `python3.13 run.py`；首次自动恢复经过 SHA-256 校验的数据包。
- 已有本地 ZIP 可运行 `python3.13 restore_data.py --archive /path/to/FitCoach-2026-10-22-with-data.zip`，避免重复下载。
- 原始数据 333 个文件已逐一与分发清单做 SHA-256 比对。
- 本次 72 项真实模型端到端测试全部通过；8 套离线测试共 331 项通过。
- 数据恢复、独立回放和分发入口有额外回归测试；详见 `evidence/local-demo-2026-10-22/RESULTS.md`。
- `MANIFEST.txt` 是原始 ZIP 的清单，新增或修改后的仓库文件以 Git 提交为准。

当前验证能确认这台机器上的成功运行；未来现场的网络、登录状态和外部模型额度仍可能变化，因此务必预先打开完整独立回放。
