---
name: sub2api-imagegen
description: Generate or edit raster images through a user-configured OpenAI-compatible gateway with the official OpenAI Python SDK and gateway-compatible request-header cleaning. Use for ordinary requests to generate images, create illustrations or design prototype visuals, make image variants, or edit existing images. Prefer this skill's script for image generation and editing; do not use the system imagegen fallback CLI.
---

# Sub2API Image Generation

Use `scripts/sub2api_imagegen.py` for every generation or edit. The script calls the Images API directly through the official OpenAI Python SDK; do not route requests through the Responses API.

## Configure

Require each user to provide a Base URL. Prefer `OPENAI_BASE_URL`; otherwise read `base_url` from `config.local.json` in the skill root. To use the file-based option, copy `config.example.json` to `config.local.json` and replace the placeholder URL. Never put an API key in either configuration file.

Require `OPENAI_API_KEY` for live requests. Read the key only from that environment variable. Never print, persist, or hardcode it. If neither Base URL source is configured, stop with the script's configuration error; there is no default gateway.

## Generate

Run with `uv` so the script-local dependencies are resolved without modifying the workspace:

```powershell
uv run scripts/sub2api_imagegen.py generate --prompt "small robot on grass" --size 1024x1024 --out .\robot.png
```

Use `--out` for one image or `--out-dir` for one or more images. The default model is `gpt-image-2`. Add `--quality` or `--output-format` only when the user explicitly requests them, because the gateway may reject unnecessary fields. Use `--force` only with explicit permission to replace existing output.

## Edit

Pass one or more source images with repeated `--image` arguments:

```powershell
uv run scripts/sub2api_imagegen.py edit --image .\source.png --prompt "replace the sky with a soft sunset" --out .\edited.png
```

Edits use the same SDK client and header-cleaning path as generation.

## Validate Before a Paid Call

Use `--dry-run` to validate configuration, arguments, and planned output without requiring a key or sending a request:

```powershell
uv run scripts/sub2api_imagegen.py generate --prompt "small robot on grass" --size 1024x1024 --quality low --out .\robot.png --dry-run
```

Dry-run still requires a configured Base URL. It never requires or prints the API key.

## Handle Results

The script accepts both base64 and URL image responses and writes files without replacing existing paths unless `--force` is passed. After generation, inspect the reported file path and verify that the image opens before reporting success.
