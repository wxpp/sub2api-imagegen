# sub2api-imagegen

一个可复用的个人 Codex Skill，用于通过用户自己配置的 OpenAI-compatible Images API 生成和编辑图片。

## 前置条件

- Codex；
- [`uv`](https://docs.astral.sh/uv/)；
- 一个支持 OpenAI-compatible Images API 的服务；
- 该服务提供的 API Key 和 Base URL。

你的网关至少需要兼容以下一种或两种能力：

- `POST /images/generations`
- `POST /images/edits`

具体模型、尺寸、质量和输出格式由你的服务商决定。

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

Windows PowerShell，当前终端临时生效：

```powershell
$env:OPENAI_API_KEY = "<YOUR_API_KEY>"
```

macOS / Linux，当前终端临时生效：

```bash
export OPENAI_API_KEY="<YOUR_API_KEY>"
```

### Base URL

Base URL 没有默认值，必须使用以下方式之一显式配置。

方式一：设置 `OPENAI_BASE_URL`。

Windows PowerShell：

```powershell
$env:OPENAI_BASE_URL = "https://your-image-api.example/v1"
```

macOS / Linux：

```bash
export OPENAI_BASE_URL="https://your-image-api.example/v1"
```

方式二：在已安装的 Skill 目录中创建 `config.local.json`。

```json
{
  "base_url": "https://your-image-api.example/v1"
}
```

可以从 `sub2api-imagegen/config.example.json` 复制。`config.local.json` 已被 `.gitignore` 忽略，只能保存 Base URL，不能保存 API Key。若同时配置，`OPENAI_BASE_URL` 优先。

## 使用

通常直接让 Codex 调用即可：

```text
使用 $sub2api-imagegen 生成一张草地上的小机器人图片。
```

默认模型为 `gpt-image-2`。如果网关使用其他模型，请添加 `--model <MODEL_ID>`。仅在网关明确支持时传入 `--quality` 或 `--output-format`。

## 安全说明

- 仓库不包含任何真实 API Key 或 Base URL；
- 脚本不会打印、保存或硬编码 API Key；
- `--dry-run` 会显示 Base URL 和请求参数，但不会显示或读取 API Key；
- 输出文件已存在时，脚本会拒绝覆盖；只有确认需要覆盖时才使用 `--force`；
- 请只使用你信任的 API 网关，因为请求和图片内容会经过该服务。

## 常见错误

- `OPENAI_BASE_URL is required`：设置 `OPENAI_BASE_URL`，或创建合法的 `config.local.json`；
- `OPENAI_API_KEY is required`：执行真实请求前设置 `OPENAI_API_KEY`；
- `403` 或请求被拦截：确认网关接受该 User-Agent，并检查是否还有网关侧安全规则；
- `400` 或参数不支持：检查模型 ID、尺寸、质量和输出格式，去掉网关不支持的可选参数；
- `refusing to overwrite existing output`：更换输出路径，或确认后添加 `--force`；
- 编辑失败：确认输入图片存在，并确认网关实现了 Images Edit API。
