# Seamless Tile Inspector

Local web tool for inspecting an offset/mask/inpaint/inverse-offset seamless tile pipeline.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Optional `.env` values:

```bash
OPENAI_API_KEY=sk-...
FAL_KEY=...
```

## Run

```bash
.venv/bin/uvicorn server:app --reload --port 8000
```

Open `http://localhost:8000`.

## CLI

```bash
.venv/bin/python cli.py path/to/tile.png --output runs/cli --mask-width 60
```

## Models

- `local-preview`: no API call. Blurs the masked seam area so the full pipeline and UI can be tested without cost.
- `gpt-image-2`: calls the OpenAI image edit endpoint and requires `OPENAI_API_KEY`.
- `fal-sdxl`: reserved endpoint path. It currently checks for `FAL_KEY` and returns a clear not-implemented error.

## Test

```bash
.venv/bin/python -m unittest discover -s tests
```
