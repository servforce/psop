# image-wireframe

`image-wireframe` is a PSOP-local build tool that launches a Codex agent to turn a source reference image into a terminal-friendly technical wireframe image.

This tool does not install or define a Codex skill. It is a normal command-line tool under `tools/` so PSOP builder code can call it after a package build discovers reference images.

## Why This Tool Starts Codex

The tool intentionally uses a Codex-agent execution model:

```text
PSOP builder -> image-wireframe CLI -> codex exec -> Codex agent -> output image
```

It does not call a hidden `codex.generate_wireframe(...)` function. Instead, it starts a Codex run, gives the run an input image path, an output image path, and strict instructions, then validates that the output image was actually created.

## Requirements

- Codex CLI available as `codex`, or pass `--codex-bin`.
- A Codex runtime/login/configuration capable of generating or editing images.
- Python 3.9+.

## Usage

```powershell
python ./tools/image-wireframe/scripts/generate_wireframe.py `
  --input ./png/png1.png `
  --output ./derived/png1.codex-wireframe.png `
  --workdir . `
  --overwrite `
  --codex-arg=--sandbox=workspace-write
```

If the Codex executable has a different path:

```powershell
python .\tools\image-wireframe\scripts\generate_wireframe.py `
  --codex-bin "C:\Path\To\codex.exe" `
  --input .\png\1_frame_032.png `
  --output .\derived\1_frame_032.wireframe.png
```

Use `--dry-run` to print the exact prompt and write a metadata preview without launching Codex.

For manual debugging, add `--passthrough` so Codex output is shown directly in the terminal. Without `--passthrough`, the tool captures Codex output for metadata and prints heartbeat messages while waiting.

## JSON Output

The command prints a JSON object to stdout on success and writes a metadata JSON file. Builder code should parse the metadata file or stdout rather than scrape human-readable logs.

Successful metadata includes:

- `ok`
- `kind`
- `source.path`
- `source.sha256`
- `derived.path`
- `derived.sha256`
- `derived.mimeType`
- `generator.engine`
- `generator.promptVersion`
- `generator.exitCode`

## Builder Integration

After PSOP/PSkill build detects a reference image, call this tool and add a derived asset relation:

```json
{
  "type": "reference-image-wireframe",
  "path": "assets/derived/foo.wireframe.png",
  "sha256": "...",
  "derivedFrom": {
    "assetId": "ref.image.foo",
    "sha256": "..."
  },
  "generator": {
    "engine": "codex-agent",
    "promptVersion": "image-wireframe-codex@0.1.0"
  }
}
```

