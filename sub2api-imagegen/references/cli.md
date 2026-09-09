# CLI controls

Use this reference when an image request needs controls beyond a basic generation or edit.

## Commands

- `generate`: one prompt, with `--n 1..10` variants.
- `edit`: one prompt plus repeated `--image`; optionally add one `--mask` and supported `--input-fidelity`.
- `generate-batch`: JSONL generation jobs under a required `--out-dir`.

All commands accept either `--prompt` or `--prompt-file`. They also accept `--model`, `--size`, `--quality`, `--background`, `--output-format`, `--output-compression`, `--moderation`, `--max-attempts`, `--force`, `--dry-run`, and output/downscale controls.

Edit input images and masks must each be smaller than 50MB; files at or above 50MB are rejected before a request is sent. A non-PNG mask produces a warning because masks are expected to be PNG files with an alpha channel. Edit accepts at most 16 input images.

## Retries

`--max-attempts N` controls the total number of live request attempts from 1 through 10; the default is `3`. This applies to generation, editing, and batch jobs. Set it to `1` to disable retries.

Retries are delegated to the official OpenAI Python SDK. Its transient policy covers HTTP `408`, `409`, `429`, server `5xx`, connection errors, and timeouts, and handles `Retry-After` and bounded backoff. Invalid parameters, authentication and permission failures, response decoding errors, and filesystem failures are not retried.

## Prompt fields

Augmentation is on unless `--no-augment` is passed. Available fields are:

- `--use-case`
- `--scene`
- `--subject`
- `--style`
- `--composition`
- `--lighting`
- `--palette`
- `--materials`
- `--text`
- `--constraints`
- `--negative`

Use `--augment` to explicitly enable the default behavior. If both flags occur, the last one controls the result.

## Models and formats

The CLI accepts GPT Image model IDs only.

- `gpt-image-2`: `auto` or a numeric size whose edges are multiples of 16, no edge exceeds 3840, aspect ratio is at most 3:1, and total pixels are between 655,360 and 8,294,400.
- Other `gpt-image-*` models: `auto`, `1024x1024`, `1536x1024`, or `1024x1536`.
- Quality: `low`, `medium`, `high`, or `auto`.
- Format: `png`, `jpeg`/`jpg`, or `webp`.
- Background: `auto`, `opaque`, or `transparent`.
- Compression: integer from 0 through 100.
- Moderation: `auto` or `low`.

Native transparency is rejected for `gpt-image-2`. It also requires PNG or WebP. `input_fidelity` is edit-only, accepts `low` or `high`, and must be omitted for `gpt-image-2`.

## Outputs

Without an output option, a one-off job writes `output/imagegen/output.png`. Multiple variants add `-1`, `-2`, and so on. With `--out-dir`, names are `image_1.<format>`, `image_2.<format>`, and so on. Existing targets cause failure unless `--force` is set.

`--downscale-max-dim N` writes an extra copy bounded to `N` pixels on its longest edge. The default extra suffix is `-web`; change it with `--downscale-suffix`.

## Batch input

The input file accepts up to 500 jobs. Blank lines and lines beginning with `#` are ignored. Every remaining line is either a plain prompt or a JSON object with a non-empty `prompt`.

JSON jobs may override generation values, prompt fields, output filename, or downscale values. Prompt fields may appear directly on the job or inside a nested `fields` object; a non-null flat value wins over the corresponding nested value. Unknown top-level or nested fields are ignored, allowing forward-compatible job files. A job-level `out` must be a relative filename under the batch output directory.

Use:

- `--concurrency N` to cap parallel live requests from 1 through 25; default `5`.
- `--max-attempts N` uses the common retry policy described above; default `3`.
- `--fail-fast` to cancel work that has not started after the first failure.

Batch dry-runs parse and validate every job and all limits but do not start workers or query/read the API key. They may read the Base URL from the current CC Switch Codex provider.
