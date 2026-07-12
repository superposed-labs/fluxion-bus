<p align="center">
  <img src="assets/brand/fluxion-logo.svg" width="132" alt="Fluxion logo">
</p>

<h1 align="center">Fluxion Bus</h1>

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

https://github.com/user-attachments/assets/7ff8be14-f4e6-4bd9-9ceb-bbf425fafba3

_演示视频采用预置数据展示，仅用于功能说明，非实时录屏。_

Fluxion Bus 是 Fluxion macOS 客户端及本地 Agent 网关背后的开源核心项目。

**Fluxion** 允许您的主 AI Agent 通过一个本地 MCP 服务，将特定范围的子任务（scoped subtasks）委派给 **Codex**、**Claude Code** 和 **Antigravity** 执行。

您无需离开当前的主 Agent 对话，Fluxion 即可自动将任务路由到另一个 AI 服务商，保持原生会话状态，实时汇报进度与结果，并记录所有文件变更以便于审查或安全回滚（Revert）。

此外，Fluxion 还能智能读取服务商报告的配额窗口。在检测到配额重置时，它不仅会向您发送通知，还能在重置后自动触发一次极简的 Agent 调用（Auto-ping），从而立即开启下一个滚动配额周期。

配额和使用量数据统计不限于通过 Fluxion 委派的任务。Fluxion 会直接从服务商 API、本地 Agent 服务或本地历史记录中提取这些指标，而不会单纯根据通过 Fluxion 路由的任务记录来估算。

本地优先、单租户、完全自托管。无须注册 Fluxion 账号，无 SaaS 依赖，默认仅监听 `127.0.0.1`。

## 为什么选择 Fluxion

### 无需离开主 Agent 的跨服务商任务委派

```text
┌────────────────────────────────────────────┐
│ 主 Agent                                   │
│ Codex / Claude Code / Antigravity          │
└─────────────────────┬──────────────────────┘
                      │ MCP: 委派特定范围的子任务
                      ▼
              ┌──────────────────┐
              │ Fluxion MCP      │
              │ 路由 + 监督      │
              └────────┬─────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
         Codex     Claude Code   Antigravity
           │            │            │
           └────────────┼────────────┘
                        ▼
         状态 / 结果 / 变更文件 / 安全回滚 (Revert)
                        │
                        ▼
                     主 Agent
```

- **智能任务路由**：将不同的子任务自动路由到相应的 AI 服务商（Provider）。
- **会话持久化**：支持在多次调用之间延续任务 Agent（Executor）的原生会话。
- **权限安全控制**：支持只读调查模式或明确授权的写入（修改）模式。
- **任务状态透明**：轻松检查异步任务的运行状态、实时日志、产物（Artifacts）以及变更的文件。
- **一键变更回滚**：支持对可恢复的文本文件变更进行审查，并一键回滚（Revert）至修改前的状态。

### 将配额重置转化为可用窗口

```text
  Claude 配额       Codex 配额      Antigravity 配额
       └───────────────┬──────────────────┘
                       ▼
              ┌─────────────────┐
              │ Fluxion 配额    │
              │ 监听 + 调度     │
              └────────┬────────┘
                       │
          ┌────────────┼──────────────┐
          ▼            ▼              ▼
        Web UI      macOS 应用       检测到重置
                                       │
                                  自动 Ping + 通知
                             微信/飞书/QQ/Slack/Telegram/LINE
```

- **统一配额面板**：直观查看不同服务商的剩余配额及重置倒计时。
- **全局用量监控**：实时监听服务商/账号配额（包括在 Fluxion 外部产生的使用量）。
- **多端控制台**：在 macOS 或 Linux 上使用基于浏览器的 Web 控制台进行管理。
- **原生 macOS 体验**：提供精致的原生 macOS 菜单栏应用，方便快速查看配额、控制服务和完成初始化设置。
- **重置自动侦测**：自动检测服务商端的配额重置事件。
- **主动窗口激活**：检测到重置后，自动发起一次极小的 Agent 调用以立即激活下一个滚动配额窗口。
- **全平台通知触达**：支持通过微信、飞书、QQ、Slack、Telegram 或 LINE 发送配额重置通知。

### 远程控制本地 Agent

即便您远离电脑，也能通过微信、飞书、QQ、Slack、Telegram 或 LINE 发送任务。Fluxion 会将消息路由给本地的 Codex、Claude Code 或 Antigravity 任务 Agent，并在同一个对话中实时返回进度更新和最终结果。

```text
手机 / 远程设备
微信/飞书/QQ/Slack/Telegram/LINE
          │
          ▼
   Fluxion 消息网关
          │
          ▼
Codex / Claude / Antigravity
          │
          ▼
   进度更新 + 最终结果
```

远程对话会保留任务 Agent 的会话上下文，后续消息可以无缝延续之前的任务。用户还可以通过内置的控制命令，随时查询最近的任务列表、检查网关状态、重置对话或取消排队中/运行中的任务。

微信端采用安全的 iLink 二维码登录。只需绑定一次账号并启用通道，即可直接通过统一的消息网关与其他聊天通道一起使用。

## 平台支持

| 功能特性 | macOS | Linux | Windows |
| --- | --- | --- | --- |
| MCP 跨服务商委派 | 支持 | 预期支持（暂未验证） | 未验证 |
| Web 配额控制台 | 支持 | 预期支持（暂未验证） | 未验证 |
| 自动 Ping 与重置通知 | 支持 | 预期支持（暂未验证） | 未验证 |
| 原生 macOS 应用 | macOS 12+ | 不支持 | 不支持 |

根据当前的技术实现，非原生功能在 Linux 系统上理论上可以直接运行，但目前尚未进行完整的手动验证。

原生菜单栏应用最低支持 macOS 12；其中“开机自启”选项需要 macOS 13+ 支持，在 macOS 12 上会自动禁用。

服务商配额探测依赖于兼容的本地凭据或服务。例如，要获取 Antigravity 的实时配额，必须确保其本地 Sidecar 正在运行。监控显示的用量直接来源于对应服务商或 Agent 的源数据，而不是简单统计通过 Fluxion 路由的任务数。

## 安装与验证

前置要求：

- **任务 Agent 客户端**：系统已安装并认证了至少一个任务 Agent CLI 工具：`codex`、`claude` 或 `agy`。
  - Codex：可以使用独立 CLI，也可以直接运行 Codex 桌面应用 —— 它的捆绑 CLI 会在 macOS 上被自动检测，并且登录状态能满足认证要求。
- **Python 环境**：用于 CLI 和后端安装的 Python 3.12+（推荐使用 Python 3.13）。macOS 原生应用在需要时可通过 Homebrew 自动为您安装 `python@3.13`。
- **Node.js**：仅当您需要在本地重新构建 Web 控制台前端时才需要 Node 18+。

### macOS 桌面应用（推荐）

桌面应用仅支持 Apple Silicon（M 系列芯片）的 Mac。Intel 用户请从源码构建，或使用下方的 CLI 方式安装。

最省事的方式是用 [Homebrew](https://brew.sh)：

```bash
brew install --cask superposed-labs/tap/fluxion
```

该 cask 会自动为你移除隔离标记，无需任何 Gatekeeper 操作 —— 装完直接打开即可。之后用 `brew upgrade --cask fluxion` 更新。

**没有 Homebrew？** 从 [最新 Release](https://github.com/superposed-labs/fluxion-bus/releases/latest) 下载 `Fluxion.app` DMG（建议先核对 `SHA256SUMS`），拖入 `/Applications`。应用未签名也未公证，因此首次启动会被 Gatekeeper 拦截：要么打开 **系统设置 → 隐私与安全性** 点击 **仍要打开**，要么在终端运行下面的指令移除隔离标记：

```bash
xattr -dr com.apple.quarantine /Applications/Fluxion.app
```

首次启动时，Fluxion 会以 `~/.local/share/fluxion` 作为后台管理路径，并提供 **Install / Repair (安装/修复)** 选项。确认后，应用将基于内置的源码快照及依赖库 Wheel 包自动完成初始化安装 —— 创建虚拟环境 `.venv`、初始化 `.env` 配置文件并启动本地后台服务，这期间不需要使用 git、网络请求、Xcode 命令行工具或在本地编译 Node 项目。

如果您的系统上没有可用的 Python 3.12+ 环境，且已安装 Homebrew，安装器会自动通过 Homebrew 安装 `python@3.13`；如果没有安装 Homebrew，应用会在安装前引导您前往 python.org 官网下载并运行 Python 安装包。请注意，像 `codex`、`claude` 或 `agy` 这样的任务 Agent CLI 仍需要您手动另行安装和配置认证。

### 由 AI Agent 自动配置 CLI / MCP

如果您偏好命令行操作、需要进行手动 MCP 注册，或是进行非桌面环境的安装，您可以直接让您当前正在使用的 AI Agent 帮您一键全自动搞定后台安装（包括前置环境排查、安装脚本运行、当前客户端的 MCP 服务注册及功能验证）。

在您打算将 Fluxion 关联为工作区的项目根目录下，直接向 Claude Code、Codex 或 Antigravity 发送以下提示词即可：

```text
Read https://raw.githubusercontent.com/superposed-labs/fluxion-bus/main/docs/agent-install.md
and follow it to install and configure Fluxion on this machine. Use the current
directory as the first authorized workspace, register the MCP server with the
client you are running in, then run the verification steps and report the results.
```

Agent 会严格按照 [docs/agent-install.md](docs/agent-install.md) 引导的流程（调用相同的安装脚本）进行操作。安装完成后，它会为您输出一份涵盖后台 CLI 状态、MCP 注册情况和 Web 控制台静态资源编译结果的组件级状态报告。请注意，macOS 原生菜单栏应用需通过 Release DMG 单独获取。

### 手动安装

为当前用户安装或更新 Fluxion 后端：

```bash
curl -fsSL https://raw.githubusercontent.com/superposed-labs/fluxion-bus/main/scripts/install.sh \
  | bash -s -- --no-desktop
```

安装脚本会创建后台托管目录 `~/.local/share/fluxion`，并将相关命令符号链接到 `~/.local/bin` 中，从而完成 CLI、消息网关和 MCP 命令的安装。预编译的 macOS 应用需通过 Release DMG 分发，而该源码安装脚本非常适合 CLI 后端管理和开发者本地工作流。后续如果需要更新后端版本，只需重复运行此命令即可，您的 `.env` 配置文件和 `data/` 数据目录会被安全保留。

默认情况下，您在哪个目录下执行此安装命令，该目录就会被自动注册为第一个获得写入权限的授权工作区。若需要指定其他工作区路径，可以使用环境变量覆盖：

```bash
curl -fsSL https://raw.githubusercontent.com/superposed-labs/fluxion-bus/main/scripts/install.sh \
  | FLUXION_WORKSPACE=/absolute/path/to/project bash -s -- --no-desktop
```

卸载 Fluxion（会自动在备份中保留当前的配置和运行数据，并附带时间戳）：

```bash
~/.local/share/fluxion/scripts/uninstall.sh
```

只有当您想要将配置文件及所有任务历史数据彻底删除时，才需要附加 `--purge` 参数。

### 开发安装

如果您需要克隆源码进行本地 Fluxion 开发，请参考以下流程：

```bash
git clone git@github.com:superposed-labs/fluxion-bus.git
cd fluxion-bus

python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 检测本地已安装的任务 Agent 并自动生成包含真实绝对路径的极简 .env 文件
fluxion init

# 检查当前配置、任务 Agent 可用状态及工作区授权状态
fluxion doctor

# 通过一个只读的测试任务验证本地运行链路
fluxion run "Summarize this project and explain how to run its tests."
```

若要在命令行中明确允许写入/修改操作：

```bash
fluxion run --write "Fix the failing tests."
```

为其他工作区目录初始化授权：

```bash
fluxion init --workspace /absolute/path/to/project
fluxion doctor --workspace /absolute/path/to/project
fluxion run --workspace /absolute/path/to/project "Inspect this project."
```

`fluxion init` 生成的 `.env` 仅包含核心配置，您也可以参考 [`.env.example`](.env.example) 结构进行手动配置。更详细的高级配置选项，请参阅 [`.env.advanced.example`](.env.advanced.example)、[参数配置指南](docs/configuration.md) 以及 [`scripts/install.sh`](scripts/install.sh) 文件。

## MCP 委派快速上手

在您经常使用的主 Agent（如 Claude Code 等）中注册 `fluxion-mcp`。有关 Claude Code、Codex 和 Antigravity 的完整客户端配置示例，请参考 [MCP 参考文档](docs/mcp.md#client-setup)。

以注册到 Claude Code 为例：

```bash
claude mcp add -s user \
  -e FLUXION_ENV_FILE=<fluxion-repo>/.env \
  -e FLUXION_WORKSPACE_ROOT=<fluxion-repo> \
  -e FLUXION_DATA_DIR=<fluxion-repo>/data \
  fluxion -- <fluxion-repo>/.venv/bin/fluxion-mcp
```

注册完成后，主 Agent 即可将具体的定向子任务委派给 Fluxion 执行：

```json
{
  "agent": "claude",
  "project": "web",
  "profile": "inspect",
  "mode": "read-only",
  "prompt": "Investigate why the login form is submitting twice."
}
```

Fluxion 接收后会立即返回一个任务标识符 `run_id`。主 Agent 可以直接通过同一个 MCP 服务实时查询运行进度、获取最终执行结果、取消执行、查看被修改的文件，或者安全回滚已审查的写入操作。

如果您需要管理多个项目，建议配置 `FLUXION_PROJECTS_FILE` 来启用项目别名映射，详情参阅 [项目参数配置指南](docs/configuration.md#project-registry)。

## 配额监控快速上手

### Web 控制台

如果您是通过桌面应用或手动安装脚本配置的后端，控制台服务已准备就绪。在命令行中运行即可启动：

```bash
fluxion-web                  # 启动后在浏览器中访问 http://127.0.0.1:8765
```

*（注：如果您是通过 Git 仓库克隆的源码且需要重新编译前端，请在运行该命令前在项目根目录下执行 `cd web && npm install && npm run build && cd ..` 以构建静态资源）*。

### macOS 菜单栏应用

如果您下载的是预编译的 `Fluxion.dmg` 安装包，只需将其拖入 `/Applications` 中打开即可。

如果您需要基于本地源码克隆构建 macOS 菜单栏应用（系统将针对您的机器架构，如 Apple Silicon 或 Intel 芯片，进行原生编译）：

```bash
./desktop/build.sh
open desktop/Fluxion.app
```

原生菜单栏应用提供了方便的可视化界面，可用于配置和管理配额监控、自动 Ping 激活、重置通知以及伴随守护进程的起停。实际的后台监测、调度和通知任务由 `fluxion-scheduler` 执行。在 Linux 系统上，该调度器完全脱离菜单栏应用独立稳定运行。

编译生成的应用可以留在项目目录中直接运行，也可以复制到 `/Applications`。如果应用是从项目目录外启动的，它会弹窗引导您选择 Fluxion 源码路径，并将配置保存在 `~/Library/Application Support/Fluxion/` 目录下。

在终端中启动调度守护进程：

```bash
fluxion-scheduler
```

有关服务商用量数据源、规则配置和常驻服务的详细部署，请参阅 [配额监控指南](docs/quota-monitoring.md) 以及 [调度器指南](docs/scheduler.md)。

## 消息通道

`fluxion-gateway` 能够安全接收来自微信、飞书、QQ、Slack、Telegram 和 LINE 等多个聊天平台的远程任务，并使用与 MCP 和本地 CLI 相同的路由内核进行任务分发，同时在同一个对话窗口中实时向您反馈执行状态与最终结果。

启动消息网关：

```bash
fluxion-gateway
```

更详细的平台配置和工作区授权步骤，请参阅 [参数配置指南](docs/configuration.md#messaging-channel-configuration)。

## 文档目录

- [系统架构](docs/architecture.md) — 系统的完整架构拓扑图、接口定义、共享状态说明及项目物理布局
- [Agent 辅助安装](docs/agent-install.md) — 专为 AI Agent 设计的步进式自动安装及配置说明
- [MCP 参考文档](docs/mcp.md) — 客户端配置、工具集定义、状态机生命周期、取消及回滚控制流
- [配额监控指南](docs/quota-monitoring.md) — 各服务商数据接口配置、Web 控制台管理、macOS 菜单栏应用、隐私策略及通知通道
- [macOS 应用指南](desktop/README.md) — 桌面安装包编译打包、托管后台配置、系统安装路径及开发调试指南
- [使用率统计](docs/usage-statistics.md) — 历史数据解析范围、独立用量统计、费用预估算法以及 Fast 模式局限性说明
- [调度器配置](docs/scheduler.md) — 自动 Ping（窗口激活）、配额重置监听规则、Cron 任务设置及守护进程常驻部署
- [参数配置指南](docs/configuration.md) — 任务 Agent 适配、工作区鉴权、消息网关、Web UI 及环境变量详述
- [服务部署说明](deploy/README.md) — 提供 launchd 和 systemd 系统守护服务的配置模板

## 开源协议

[Apache License 2.0](LICENSE) — 请一并参阅 [NOTICE](NOTICE) 说明。
