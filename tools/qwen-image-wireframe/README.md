# qwen-image-wireframe

`qwen-image-wireframe` is a PSOP-local reusable CLI tool for converting a reference photo into a clean white-background, black-line technical wireframe by using a domestic Alibaba Cloud Bailian / DashScope image editing model.

This is an asset-generation tool, not a PSOP Skill. It does not define a user-facing Skill, does not compile to EG, and does not own runtime state.

## Model Choice

Use an image editing / image-to-image generation model for this task.

Recommended model family:

- `qwen-image-2.0-pro` for best quality and stronger instruction following.
- `qwen-image-2.0` when speed matters more.
- `qwen-image-edit-max` or `qwen-image-edit-plus` when you specifically want the image-edit series.
- `qwen-image-edit` only when you want the legacy/simpler model. It supports only one output image and does not support custom `size`.

Do not use a vision-understanding-only model such as `qwen-vl-*` as the primary generator. A VL model can inspect the source image and produce text, but it cannot directly return the required output PNG. It is useful only as an optional pre-pass for captioning, OCR, or quality review.

Avoid pure text-to-image models for the main path unless no image-editing model is available. Text-to-image models will often lose exact layout, finger position, wire routing, screen placement, and small operational labels.

The expected capability is:

```text
input image + instruction prompt -> edited/generated image
```

For this project, that means: preserve the source photo's object geometry and operational relationship, but redraw it as a simplified operation-manual line drawing.

## Provider

The initial provider is:

- Provider: `bailian-qwen-image-edit`
- API style: DashScope/Bailian synchronous multimodal generation HTTP call
- Default base URL: `https://dashscope.aliyuncs.com`
- Optional Beijing workspace base URL: `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com`
- Optional Singapore workspace base URL: `https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com`
- Submit path: `/api/v1/services/aigc/multimodal-generation/generation`
- Default model: `qwen-image-2.0-pro`

HTTP request shape:

```http
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
Content-Type: application/json
Authorization: Bearer $DASHSCOPE_API_KEY
```

```json
{
  "model": "qwen-image-2.0-pro",
  "input": {
    "messages": [
      {
        "role": "user",
        "content": [
          { "image": "data:image/png;base64,..." },
          { "text": "Convert the input photo into a clean black-line wireframe drawing..." }
        ]
      }
    ]
  },
  "parameters": {
    "n": 1,
    "negative_prompt": "color, shading, gray fuzzy strokes, hatching, texture, watermark, logo, brand text",
    "prompt_extend": true,
    "watermark": false,
    "size": "1536*1024"
  }
}
```

The response returns the generated image URL at:

```text
output.choices[0].message.content[*].image
```

The image URL is temporary. The CLI downloads it immediately and writes `--output`.

Keep the exact model id and domain configurable with CLI flags:

- `--image-model`
- `--base-url`
- `--submit-path`
- `--workspace-id` for optional workspace-specific domains
- `--region` for optional workspace-specific domains

If Bailian shows a newer exact model id, pass it with `--image-model`.

## Environment

The tool checks these API key variables in order:

```bash
DASHSCOPE_API_KEY=<your-bailian-or-dashscope-api-key>
BAILIAN_API_KEY=<your-bailian-or-dashscope-api-key>
QWEN_API_KEY=<your-bailian-or-dashscope-api-key>
```

For local PSOP development, the CLI checks the process environment first, then falls back to the PSOP repo root `.env`, then `backend/.env`.

## Usage

Minimal PowerShell usage:

```powershell
python .\tools\qwen-image-wireframe\scripts\generate_wireframe.py `
  --input .\png\png4.png `
  --output .\derived\png4-qwen.png
```

By default the CLI uses the public DashScope domain `https://dashscope.aliyuncs.com`. No `WorkspaceId` is required for the default path.

If you explicitly want to use a workspace-specific Beijing domain, pass:

```powershell
python .\tools\qwen-image-wireframe\scripts\generate_wireframe.py `
  --workspace-id "<your-workspace-id>" `
  --input .\png\png4.png `
  --output .\derived\png4-qwen.png
```

If you explicitly want to use a workspace-specific Singapore domain, pass `--region ap-southeast-1` with `--workspace-id`.

If the model market lists a specific model id:

```powershell
python .\tools\qwen-image-wireframe\scripts\generate_wireframe.py `
  --image-model qwen-image-edit-max `
  --input .\png\png4.png `
  --output .\derived\png4-qwen.png
```

If you want to use a public/OSS URL instead of local Base64:

```powershell
python .\tools\qwen-image-wireframe\scripts\generate_wireframe.py `
  --input .\png\png4.png `
  --input-url "https://example.com/png4.png" `
  --output .\derived\png4-qwen.png
```

For the legacy `qwen-image-edit` model:

```powershell
python .\tools\qwen-image-wireframe\scripts\generate_wireframe.py `
  --image-model qwen-image-edit `
  --size auto `
  --input .\png\png4.png `
  --output .\derived\png4-qwen.png
```

## JSON Output

The command prints one JSON object to stdout. PSOP callers should parse that object instead of scraping human-readable text.

Successful output includes:

- `ok`
- `input`
- `output`
- `provider`
- `api`
- `base_url`
- `model`
- `tool`
- `size`
- `prompt_version`
- `image_url`

## Notes

- Existing output files are not overwritten unless `--overwrite` is passed.
- The script accepts local images. By default, it sends the image as a base64 `data:` URL.
- Input content follows the official `messages` format: 1-3 `{ "image": "..." }` entries plus exactly one `{ "text": "..." }` instruction.
- The generated image URL is valid for 24 hours according to the Alibaba Cloud docs; the CLI downloads it immediately.
