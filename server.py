import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from pipeline.runner import run_pipeline

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
RUNS_DIR = BASE_DIR / "runs"
UPLOADS_DIR = BASE_DIR / ".runs_tmp" / "uploads"
STATIC_DIR = BASE_DIR / "static"
RUNS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Seamless Tile Inspector")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunRequest(BaseModel):
    image_id: str
    mask_width: int = Field(default=60, ge=1, le=512)
    model: Literal["local-preview", "gpt-image-2", "fal-sdxl"] = "local-preview"
    prompt: str = "preserve the texture and pattern phase across the seam"
    strength: float = Field(default=0.7, ge=0.0, le=1.0)


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    image_id = uuid.uuid4().hex
    path = UPLOADS_DIR / f"{image_id}.png"

    try:
        image = Image.open(file.file)
        image.load()
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=400, detail="uploaded file is not a supported image") from exc

    if image.width != image.height:
        raise HTTPException(status_code=400, detail="image must be square")
    if image.width % 8 != 0:
        raise HTTPException(status_code=400, detail="image dimensions must be divisible by 8")

    image.convert("RGB").save(path)
    return {"image_id": image_id, "size": [image.width, image.height]}


@app.post("/api/run")
async def run(request: RunRequest):
    source_path = UPLOADS_DIR / f"{request.image_id}.png"
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="image_id not found")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_dir = RUNS_DIR / run_id
    image = Image.open(source_path)
    result = await run_pipeline(
        image=image,
        output_dir=output_dir,
        mask_width=request.mask_width,
        model=request.model,
        prompt=request.prompt,
        strength=request.strength,
    )
    shutil.copy2(source_path, output_dir / "uploaded.png")
    stages = {name: f"/runs/{run_id}/{path.name}" for name, path in result.stages.items()}
    return {"run_id": run_id, "stages": stages}


@app.get("/api/runs")
async def list_runs():
    if not RUNS_DIR.exists():
        return []

    runs = []
    for run_dir in sorted(RUNS_DIR.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        params_path = run_dir / "params.json"
        if not params_path.exists():
            continue
        params = json.loads(params_path.read_text(encoding="utf-8"))
        thumbnails = {
            name: f"/runs/{run_dir.name}/{name}.png"
            for name in ("final", "2x2", "3x3", "mask_overlay")
            if (run_dir / f"{name}.png").exists()
        }
        runs.append({"run_id": run_dir.name, "params": params, "thumbnails": thumbnails})
    return runs


@app.get("/runs/{run_id}/{filename}")
async def get_run_file(run_id: str, filename: str):
    return _png_response(RUNS_DIR / run_id / filename)


@app.get("/uploads/{filename}")
async def get_upload_file(filename: str):
    return _png_response(UPLOADS_DIR / filename)


def _png_response(path: Path):
    if path.name != path.parts[-1] or path.suffix.lower() != ".png" or not path.exists():
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(path, media_type="image/png")


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
