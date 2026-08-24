# DeterminFlow 小说自动化写作流程

基于 [DeterminFlow](https://github.com/alikon-art/DeterminFlow-dy) 与 [`bishu-novel`](https://github.com/alikon-art/DeterminFlow-Plugins/tree/main/plugins/bishu-novel) 的**全自动小说产出流水线**：建书 → 策划四阶段 → 逐章正文（mvp → polish → post-hoc）→ 导出 txt → 批次报告，全程无人值守，Windows 计划任务定时触发。

> 本仓库只包含「自动化写小说流程」的核心脚本与文档，不含 DeterminFlow 框架源码。运行前需先部署 DeterminFlow 环境（见下文依赖）。

## 功能

- **全自动产出**：每天固定产出章节数（默认 2 章），每章约 3000-4000 字
- **策划阶段链**：build → character → story-plan → outline 幂等推进，已完成自动跳过
- **逐章三阶段**：mvp（初稿）→ polish（润色）→ post-hoc（后验），章节完整产出保障
- **熔断保护**：DeepSeek 余额低于阈值、单批 token 超上限自动停止，避免烧钱
- **缺失补跑**：巡航失败自动检测缺失章节并补跑
- **收尾自动化**：导出每章 txt + 批次报告（微信推送可选，默认关闭）
- **可移植配置**：所有路径/标识可用环境变量覆盖（见 `.env.example`）

## 目录结构

```
├── pipeline.py          # 主流程：服务拉起 → 阶段链 → 正文巡航 → 导出 → 报告
├── run_daily.bat        # 每日计划任务入口（pipeline.py --daily 2）
├── run_ch8.bat          # 手动/补跑指定章节示例
├── verify_output.py     # 产出完整性监视/兜底脚本（补做缺失的导出）
├── AUTOMATION.md        # 运维手册（计划任务、故障排查、监视）
├── .env.example         # 配置模板（复制为 .env 后填写）
└── .gitignore           # 排除 .env / data / logs 等
```

## 依赖

| 依赖 | 说明 |
|------|------|
| [DeterminFlow](https://github.com/alikon-art/DeterminFlow-dy) | Core(8020) + Novel Studio(8031) 框架 |
| [bishu-novel](https://github.com/alikon-art/DeterminFlow-Plugins/tree/main/plugins/bishu-novel) | AI 小说 Workflow 插件（7 条 workflow） |
| PostgreSQL | 可选（v0.2.2 不依赖也可运行） |
| DeepSeek API Key | 正文/代理模型供应商 |

## 快速开始

1. **部署 DeterminFlow 环境**（含 bishu-novel 插件），确保 Core 与 Novel Studio 可启动。
2. **复制配置模板并填写密钥**：
   ```bat
   copy .env.example .env
   ```
   编辑 `.env`，填入 `DEEPSEEK_API_KEY`（必填）。`.env` 已被 `.gitignore` 排除，**切勿提交真实密钥**。
3. **手动跑一批**（首次会自动建书并推进策划四阶段）：
   ```bat
   .venv\Scripts\python.exe pipeline.py --daily 2
   ```
   或直接运行 `run_daily.bat`。
4. **注册每日计划任务**（Windows）：
   ```bat
   schtasks /create /tn DeterminFlow-Daily-Novel /sc daily /st 19:00 /tr "D:\你的路径\run_daily.bat" /f
   ```

## 常用命令

| 命令 | 作用 |
|---|---|
| `pipeline.py --daily 2` | 跑一批（默认 2 章，从已产出章节续写） |
| `pipeline.py --chapters 7 8` | 指定章节区间 |
| `pipeline.py --status` | 查看进度（需服务在运行） |
| `pipeline.py --review` | 人工审阅模式（不自动确认阶段） |
| `pipeline.py --no-polish` | 巡航跳过 polish 润色 |
| `pipeline.py --skip-services` | 跳过服务检查 |
| `verify_output.py --all` | 检查全部章节产出完整性并补做缺失导出 |

## 配置项（环境变量，见 `.env.example`）

| 变量 | 默认 | 说明 |
|------|------|------|
| `DEEPSEEK_API_KEY` | （必填） | DeepSeek 密钥，仅存于本地 `.env` |
| `PG_BIN_DIR` / `PG_DATA_DIR` / `PG_LOG_FILE` | `D:/pg17/...` | PostgreSQL 路径（其他机器请覆盖） |
| `OUTPUT_DIR` | `D:/DeterminFlow-output` | 章节 txt 输出目录 |
| `DEFAULT_BOOK` | `东野异象-3` | 默认书名（`--book` 未指定时） |
| `CHAPTERS_PER_BATCH` | `2` | 每天产出章节数 |
| `MIN_BALANCE` | `1.0` | 余额熔断阈值（元） |
| `MAX_TOKENS` | `200000` | 单批 token 估算上限 |
| `CORE_URL` / `STUDIO_URL` | `http://127.0.0.1:8020/8031` | 服务地址 |

## 安全说明

- **API 密钥只放在 `.env`**，该文件已在 `.gitignore` 中，不会进入版本库。
- 仓库不包含任何真实密钥、书稿数据或本地会话记录。
- 微信推送默认关闭（`PUSHPLUS_TOKEN` 置空即跳过）；如需开启，填入 PushPlus token 并保留代码中的 `push_chapters` 调用。

## 文档

- 完整运维手册见 [`AUTOMATION.md`](AUTOMATION.md)：计划任务管理、产出文件位置、故障排查、产出保障监视。

## 许可

- 流程脚本部分：MIT
- 框架与插件版权归 [DeterminFlow](https://github.com/alikon-art/DeterminFlow-dy) / [bishu-novel](https://github.com/alikon-art/DeterminFlow-Plugins) 所有
