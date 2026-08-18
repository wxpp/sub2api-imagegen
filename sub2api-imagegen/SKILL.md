---
name: sub2api-imagegen
description: Generate, batch-generate, or edit raster images through a user-configured OpenAI-compatible gateway using the official OpenAI Python SDK. Use for ordinary image requests, illustrations, design prototypes, variants, masked edits, multi-image edits, or prompt-list/JSON batches. Prefer this skill's bundled CLI in this environment; do not switch to the system imagegen fallback CLI.
---

# Sub2API Image Generation

Use `scripts/sub2api_imagegen.py` for every image request. Run it through `uv`; its inline dependency metadata supplies the official OpenAI SDK, HTTP transport, and optional downscaling support. Call only the Images API, never the Responses API.

## Configure

Require each user to provide a Base URL. Prefer `OPENAI_BASE_URL`; otherwise read `base_url` from `config.local.json` in the skill root. To use the file-based option, copy `config.example.json` to `config.local.json` and replace the placeholder URL. Never put an API key in either configuration file.

Require `OPENAI_API_KEY` for live requests. Read the key only from that environment variable. Never print, persist, or hardcode it. If neither Base URL source is configured, stop with the script's configuration error; there is no default gateway.

## Choose a command

- Use `generate` for one prompt and `edit` when source images are provided.
- Use `generate-batch` only for a line-oriented prompt/JSON job list and always give it `--out-dir`.
- Read [references/cli.md](references/cli.md) before using masks, prompt fields, downscaling, batch concurrency, retry controls, or model-specific options.

Defaults match the image CLI workflow: `gpt-image-2`, `size=auto`, `quality=medium`, `output_format=png`, one image, and `output/imagegen/output.png`. Use `--prompt-file` instead of `--prompt` for long prompts. Use `--out` for a named output or `--out-dir` for generated names.

For edits, repeat `--image` in the intended order. Use at most 16 inputs. Pass `--mask` once; it applies to the first image. Do not pass `--input-fidelity` with `gpt-image-2`.

## Build prompts

Prompt augmentation is enabled by default. Supply any relevant fields such as `--style`, `--composition`, `--lighting`, `--palette`, `--text`, `--constraints`, or `--negative`; use `--no-augment` when the prompt must be sent unchanged.

## Validate and execute

Run `--dry-run` first when parameters or output paths are uncertain. Dry-run validates Base URL, prompt, model limits, inputs, and planned outputs without reading `OPENAI_API_KEY` or sending a request.

For live work, preserve the requested model and controls. If a gateway rejects an option, report the unsupported option instead of silently changing the request. `gpt-image-2` accepts constrained flexible sizes but not transparent output; older GPT Image models accept only `auto`, `1024x1024`, `1536x1024`, or `1024x1536`. Transparent output requires an older GPT Image model and PNG or WebP.

Use `--force` only after explicit permission to replace files. When `--downscale-max-dim` is set, keep the full-size file and also write the suffixed copy.

## Verify results

The CLI accepts both base64 image data and image URLs returned by the gateway. Open each reported file and verify that it is a valid image before reporting completion.
