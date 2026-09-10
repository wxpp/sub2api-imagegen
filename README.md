# sub2api-imagegen

一个可复用的 Codex Skill，用于通过用户自己配置的 OpenAI-compatible Images API 生成、批量生成和编辑图片。

## 前置条件

- Codex；
- [`uv`](https://docs.astral.sh/uv/)；
- 一个支持 OpenAI-compatible Images API 的服务；
- 支持生图的 API Key 和 Base URL。

你的网关至少需要兼容以下一种或两种能力：

- `POST /images/generations`
- `POST /images/edits`

具体模型和参数是否可用，最终由你的服务商决定。

## 使用 Skill Installer 安装

在 Codex 中发送：

```text
使用 $skill-installer 安装 GitHub 仓库 wxpp/sub2api-imagegen 中路径 sub2api-imagegen 的 Skill。
```

安装参数是：

- repo：`wxpp/sub2api-imagegen`
- path：`sub2api-imagegen`

安装完成后新建一个 Codex 任务，以加载新 Skill。

## 手动安装

### Windows PowerShell

```powershell
git clone https://github.com/wxpp/sub2api-imagegen.git sub2api-imagegen-repo
New-Item -ItemType Directory -Force "$HOME\.codex\skills" | Out-Null
Copy-Item -Recurse -LiteralPath ".\sub2api-imagegen-repo\sub2api-imagegen" -Destination "$HOME\.codex\skills\sub2api-imagegen"
```

### macOS / Linux

```bash
git clone https://github.com/wxpp/sub2api-imagegen.git sub2api-imagegen-repo
mkdir -p "$HOME/.codex/skills"
cp -R ./sub2api-imagegen-repo/sub2api-imagegen "$HOME/.codex/skills/sub2api-imagegen"
```

手动安装后同样需要新建一个 Codex 任务。

## 配置

Skill 可以自动读取 CC Switch 当前启用的 Codex Provider，也可以独立使用本地配置文件或环境变量。配置优先级如下：

- Base URL：CC Switch 当前 Codex Provider → `config.local.json` → `OPENAI_BASE_URL`；
- API Key：CC Switch 当前 Codex Provider → `OPENAI_API_KEY`。

如果没有安装 CC Switch，或者其配置缺失、被锁定、已损坏、结构不兼容、无法唯一确定当前 Codex Provider，Skill 会安全回退到下一种配置来源，不会自行猜测 Provider。

### 使用 CC Switch（可选）

在 CC Switch 中启用一个 Codex Provider 后，Skill 会从默认目录 `~/.cc-switch` 只读获取该 Provider 的 API Key 和 Base URL，不需要重复设置环境变量。

Skill 只接受 CC Switch 明确选中的当前 Codex Provider；如果选择记录不一致，则只在数据库中恰好有一个当前 Codex Provider 时使用它。读取过程中不会修改 CC Switch 数据库，也不会输出 Key 或 Base URL。

### API Key

如果不使用 CC Switch，或当前 Codex Provider 没有可用的 Key，请通过 `OPENAI_API_KEY` 环境变量提供。不要把 Key 写进仓库、`config.local.json`、提示词或日志。

Windows 用户级永久设置：

```powershell
[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "<YOUR_API_KEY>", "User")
```

设置后重启 Codex，使新环境变量生效。

macOS / Linux 持久设置：根据当前使用的 shell，将下面一行添加到 `~/.zshrc` 或 `~/.bashrc`：

```bash
export OPENAI_API_KEY="<YOUR_API_KEY>"
```

保存后重启终端和 Codex。

### Base URL

Base URL 没有默认值。如果不使用 CC Switch，或当前 Codex Provider 没有可用的 Base URL，必须使用以下方式之一显式配置。

方式一：在已安装的 Skill 目录中创建 `config.local.json`。

安装目录：

- Windows：`C:\Users\<用户名>\.codex\skills\sub2api-imagegen`
- macOS / Linux：`~/.codex/skills/sub2api-imagegen`

Windows PowerShell，从安装目录中的示例文件复制：

```powershell
Copy-Item -LiteralPath "$HOME\.codex\skills\sub2api-imagegen\config.example.json" -Destination "$HOME\.codex\skills\sub2api-imagegen\config.local.json"
```

macOS / Linux，从安装目录中的示例文件复制：

```bash
cp "$HOME/.codex/skills/sub2api-imagegen/config.example.json" "$HOME/.codex/skills/sub2api-imagegen/config.local.json"
```

然后编辑 `config.local.json`：

```json
{
  "base_url": "https://your-image-api.example/v1"
}
```

方式二：设置永久环境变量 `OPENAI_BASE_URL`。

Windows 用户级永久设置：

```powershell
[Environment]::SetEnvironmentVariable("OPENAI_BASE_URL", "https://your-image-api.example/v1", "User")
```

设置后重启 Codex，使新环境变量生效。

macOS / Linux 持久设置：根据当前使用的 shell，将下面一行添加到 `~/.zshrc` 或 `~/.bashrc`：

```bash
export OPENAI_BASE_URL="https://your-image-api.example/v1"
```

保存后重启终端和 Codex。如果两种独立配置同时存在，`config.local.json` 优先于 `OPENAI_BASE_URL`；CC Switch 当前 Codex Provider 的优先级最高。

## 使用

通常直接让 Codex 调用即可：

```text
使用 $sub2api-imagegen 生成一张草地上的小机器人图片。
```

CLI 的生成和批处理默认使用 `gpt-image-2.5-flare`，编辑默认使用 `gpt-image-2.5-sunburst`；其余默认值为 `size=auto`、`quality=medium`、`output_format=png`，默认输出到 `output/imagegen/output.png`。Flare 用于日常生成、快速迭代和批量任务；Sunburst 用于精细编辑和参考图保持。仍可通过 `--model gpt-image-2` 手动使用旧模型。

2.5 模型已在兼容网关上实际验证生成与编辑。由于 2.5 的完整参数能力尚未逐项验证，本 Skill 对未知能力采取保守策略：2.5 暂只接受 `auto`、`1024x1024`、`1536x1024`、`1024x1536`，并暂不开放透明背景和 `input_fidelity`。这些限制会在可靠验证后更新。

Skill 同时支持长提示词文件、多图编辑、Mask、提示词结构字段、1–10 张变体、透明背景校验、可选下采样，以及带并发和失败策略的批处理输入（纯提示词行或 JSON 对象）。它会处理 Images API 返回的 Base64 图片或图片 URL。完整参数由安装后的 `sub2api-imagegen/references/cli.md` 说明。

生成、编辑和批处理请求默认最多尝试 3 次。重试完全交给官方 OpenAI Python SDK 处理，适用于 `408`、`409`、`429`、服务器 `5xx`、连接错误和超时；SDK 会处理 `Retry-After` 和退避。可用 `--max-attempts 1..10` 调整总尝试次数，设为 `1` 可关闭重试。

编辑最多接受 16 张输入图。每张输入图和 Mask 必须小于 50MB；达到或超过 50MB 会在发送请求前直接失败，避免把已知无效的大文件上传到网关。

`--dry-run` 会检查 Base URL、参数、输入和输出路径，但不会查询 CC Switch 中的 Key、读取 `OPENAI_API_KEY` 或发送请求。除非明确使用 `--force`，已有文件不会被覆盖。

## 安全说明

- 仓库不包含任何真实 API Key 或 Base URL；
- 脚本不会打印、保存或硬编码 API Key；
- CC Switch 数据库始终以 SQLite 只读模式打开；
- `--dry-run` 会显示请求参数和输出计划，可能读取 Base URL，但不会查询、显示或读取 API Key；
- 输出文件已存在时，脚本会拒绝覆盖；只有确认需要覆盖时才使用 `--force`；
- 请只使用你信任的 API 网关，因为请求和图片内容会经过该服务。

## 常见错误

- `a Base URL is required`：在 CC Switch 中启用有效的 Codex Provider、创建合法的 `config.local.json`，或设置 `OPENAI_BASE_URL`；
- `an API key is required`：在 CC Switch 中启用包含 Key 的 Codex Provider，或在真实请求前设置 `OPENAI_API_KEY`；
- `403` 或请求被拦截：确认网关接受该 User-Agent，并检查是否还有网关侧安全规则；
- `400` 或参数不支持：检查网关是否支持当前模型及 CLI 发送的默认或显式参数；
- `503`：通常是网关或上游暂时不可用；Skill 会自动重试，达到 `--max-attempts` 后仍失败才退出；
- `image/mask must be smaller than 50MB`：压缩或缩小输入文件后再试；
- `refusing to overwrite existing output`：更换输出路径，或确认后添加 `--force`；
- 编辑失败：确认输入图片存在，并确认网关实现了 Images Edit API。

## 许可证

本项目采用 [MIT License](LICENSE)。
