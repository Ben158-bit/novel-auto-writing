# DeterminFlow 自动化写作 · 运维手册

> 全自动产出小说章节：每天固定 2 章，Windows 计划任务定时触发，单本《东野异象-3》。

## 一、快速开始（手动跑一次）

```bat
cd /d D:\DeterminFlow-dy
.venv\Scripts\python.exe pipeline.py --daily 2
```

脚本会自动：探测并拉起 Core(8020) / Novel Studio(8031) / PostgreSQL(可选) → 推进策划四阶段 → 巡航产出章节 → 导出 txt → 写批次报告。

常用参数：

| 命令 | 作用 |
|---|---|
| `pipeline.py --daily 2` | 每天跑一批（默认 2 章，从已产出章节续写） |
| `pipeline.py --chapters 7 8` | 指定章节区间 |
| `pipeline.py --status` | 查看进度（需服务在运行） |
| `pipeline.py --review` | 人工审阅模式（不自动确认阶段） |
| `pipeline.py --no-polish` | 巡航跳过 polish 润色 |
| `pipeline.py --min-balance 1.0` | DeepSeek 余额低于 1 元则熔断停止（默认 1.0） |
| `pipeline.py --max-tokens 200000` | 单批 token 估算上限，超过熔断（默认 200000） |
| `pipeline.py --skip-services` | 跳过服务检查 |

## 二、每日自动运行（计划任务）

已注册计划任务 **`DeterminFlow-Daily-Novel`**：每天 **19:00** 运行 `D:\DeterminFlow-dy\run_daily.bat`（内部调用 `pipeline.py --daily 2`）。

### 管理命令（在 cmd / PowerShell 中执行）

```bat
:: 查看任务详情
schtasks /query /tn "DeterminFlow-Daily-Novel" /v /fo LIST

:: 立即手动触发（等同跑一次每日任务）
schtasks /run /tn "DeterminFlow-Daily-Novel"

:: 改触发时间（例如改成 21:00）
schtasks /change /tn "DeterminFlow-Daily-Novel" /st 21:00

:: 禁用 / 启用
schtasks /change /tn "DeterminFlow-Daily-Novel" /disable
schtasks /change /tn "DeterminFlow-Daily-Novel" /enable

:: 删除
schtasks /delete /tn "DeterminFlow-Daily-Novel" /f
```

## 三、调整产出节奏

- 每天章节数：改 `run_daily.bat` 里的 `--daily 2` 为 `--daily N`，或用 `schtasks /run` 前临时改。
- 章节字数：书配置 `words_per_chapter`（当前 3000-4000），改 `data/books/东野异象-3/.studio/book.json`。

## 四、产出文件位置

| 内容 | 路径 |
|---|---|
| 每章 txt | `D:\DeterminFlow-output\东野异象-3\第N章-标题.txt` |
| 章节原始文件 | `data\books\东野异象-3\story\NNNN\chapter.md` |
| 每日批次报告 | `logs\pipeline_YYYYMMDD.log` |
| 运行日志 | `logs\pipeline.log`、`logs\run_daily.log` |
| 断点续跑状态 | `data\books\东野异象-3\.studio\pipeline_state.json` |

## 五、常见故障排查

1. **Core 启动即崩、报 `installed plugin content hash mismatch`**
   插件 checkout 内容被改动过。重新计算 sha256 并更新 `data/plugins/plugins.lock.json` 里 `bishu-novel.active_revision.content_sha256`（算法见 `src/plugin_system/store.py::_content_sha256`，排除 `.git` 和 `__pycache__`）。

2. **插件 `runtime_status=degraded`、报 `[WinError 5] 拒绝访问`**
   Windows 目录监控/杀软导致 `os.replace` 重命名非空目录失败。`src/extension_host/resource_preparation.py::_atomic_replace_directory` 已加「复制+删除」回退；若仍出现，关闭目录实时扫描或重启后重试。

3. **`找不到 Bishu Novel 工作流：xxx`**
   插件未加载成功（见第 2 条）。确认 `GET http://127.0.0.1:8020/api/plugins` 里 `runtime_status=running`、`/api/workflows` 有 7 条 `bishu-novel-*` 工作流。

4. **`UnicodeEncodeError` / 中文路径报错**
   确保 `PYTHONUTF8=1`（`run_daily.bat` 已设置）。

5. **批次中途失败**
   看 `logs\pipeline.log` 末尾；单阶段失败会自动重试 3 次，连续失败停止。`pipeline.py --status` 可查当前阶段/巡航状态。

## 六、产出保障（监视与兜底）

`pipeline.py` 内置了自愈能力，另有独立监视脚本 `verify_output.py`：

- **巡航失败自动补跑**：某章 mvp/polish/post-hoc 失败时，pipeline 会检测缺失章节并自动从缺失处重跑（最多 3 次），不直接退出。
- **收尾幂等**：txt 导出会覆盖同名文件；微信推送用 `pipeline_state.json` 的 `pushed` 列表去重，不会重复推。
- **部分失败也不跳过收尾**：即使巡航部分失败，已完整产出的章节仍会导出 txt + 推送微信。

### 监视脚本

```bat
cd /d D:\DeterminFlow-dy
.venv\Scripts\python.exe verify_output.py --daily 2          :: 检查最近 2 章并补做缺失收尾
.venv\Scripts\python.exe verify_output.py --all --check      :: 全量检查（不修复）
.venv\Scripts\python.exe verify_output.py --chapters 9 10    :: 指定区间
```

每章输出 `正文/txt/微信` 三态，缺失的「导出/推送」会自动补做；正文本身缺失则提示用 `pipeline.py --chapters N N` 补跑。

## 七、待办（下一步）

- **重启恢复验证**：重启电脑后计划任务自动恢复整条链路。
