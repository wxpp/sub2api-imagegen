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

### API Key

`OPENAI_API_KEY` 只能通过环境变量提供。不要把 Key 写进仓库、`config.local.json`、提示词或日志。

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

Base URL 没有默认值，必须使用以下方式之一显式配置。

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

保存后重启终端和 Codex。若同时配置，`OPENAI_BASE_URL` 优先。

## 使用

通常直接让 Codex 调用即可：

```text
使用 $sub2api-imagegen 生成一张草地上的小机器人图片。
```

CLI 默认使用 `gpt-image-2`、`size=auto`、`quality=medium`、`output_format=png`，默认输出到 `output/imagegen/output.png`。如果网关使用其他 GPT Image 模型，需要在请求中指定对应模型 ID。

Skill 同时支持长提示词文件、多图编辑、Mask、提示词结构字段、1–10 张变体、透明背景校验、可选下采样，以及带并发、重试和失败策略的批处理输入（纯提示词行或 JSON 对象）。它会处理 Images API 返回的 Base64 图片或图片 URL。完整参数由安装后的 `sub2api-imagegen/references/cli.md` 说明。

`--dry-run` 会检查 Base URL、参数、输入和输出路径，但不会读取 API Key 或发送请求。除非明确使用 `--force`，已有文件不会被覆盖。

## 安全说明

- 仓库不包含任何真实 API Key 或 Base URL；
- 脚本不会打印、保存或硬编码 API Key；
- `--dry-run` 会显示请求参数和输出计划，但不会显示或读取 API Key；
- 输出文件已存在时，脚本会拒绝覆盖；只有确认需要覆盖时才使用 `--force`；
- 请只使用你信任的 API 网关，因为请求和图片内容会经过该服务。

## 常见错误

- `OPENAI_BASE_URL is required`：设置 `OPENAI_BASE_URL`，或创建合法的 `config.local.json`；
- `OPENAI_API_KEY is required`：执行真实请求前设置 `OPENAI_API_KEY`；
- `403` 或请求被拦截：确认网关接受该 User-Agent，并检查是否还有网关侧安全规则；
- `400` 或参数不支持：检查网关是否支持当前模型及 CLI 发送的默认或显式参数；
- `refusing to overwrite existing output`：更换输出路径，或确认后添加 `--force`；
- 编辑失败：确认输入图片存在，并确认网关实现了 Images Edit API。

## 许可证

本项目采用 [MIT License](LICENSE)。
