#!/usr/bin/env python3
import os

from fastapi import FastAPI, HTTPException, UploadFile, File, status, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.config import Config
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from pathlib import Path

from app.database import init_db
from app.db_migration import run_migrations
from app.config import settings
from app.tasks.process_document import process_document  # Updated import
from app.tasks.upload_to_dropbox import upload_to_dropbox
from app.tasks.upload_to_paperless import upload_to_paperless
from app.tasks.upload_to_nextcloud import upload_to_nextcloud
from app.tasks.send_to_all import send_to_all_destinations

from app.api import router as api_router
from app.frontend import router as frontend_router
from app.auth import router as auth_router

# Load configuration from .env for the session key
config = Config(".env")
SESSION_SECRET = config(
    "SESSION_SECRET",
    default="YOUR_DEFAULT_SESSION_SECRET_MUST_BE_32_CHARS_OR_MORE"
)

app = FastAPI(title="DocuNova")

# 1) Session Middleware (for request.session to work)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

# 2) Respect the X-Forwarded-* headers from Traefik
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# 3) (Optional but recommended) Restrict valid hosts:
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[
    settings.external_hostname,
    "localhost",
    "127.0.0.1"
])

# Mount the static folder for CSS/JS:
frontend_static_dir = Path(__file__).parent.parent / "frontend" / "static"
app.mount("/static", StaticFiles(directory=frontend_static_dir), name="static")

@app.on_event("startup")
def on_startup():
    init_db()  # Create tables if they don't exist
    run_migrations()  # Run migrations to add any missing columns

@app.post("/process/")
def process(file_path: str):
    """
    API Endpoint to start document processing.
    This enqueues document processing which handles the full pipeline.
    """
    if not os.path.isabs(file_path):
        file_path = os.path.join(settings.workdir, file_path)

    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=400, detail=f"File {file_path} not found."
        )

    task = process_document.delay(file_path)  # Updated function call
    return {"task_id": task.id, "status": "queued"}

@app.post("/send_to_dropbox/")
def send_to_dropbox(file_path: str):
    if not os.path.isabs(file_path):
        file_path = os.path.join(settings.workdir, 'processed', file_path)
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=400, detail=f"File {file_path} not found."
        )
    task = upload_to_dropbox.delay(file_path)
    return {"task_id": task.id, "status": "queued"}

@app.post("/send_to_paperless/")
def send_to_paperless(file_path: str):
    if not os.path.isabs(file_path):
        file_path = os.path.join(settings.workdir, 'processed', file_path)
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=400, detail=f"File {file_path} not found."
        )
    task = upload_to_paperless.delay(file_path)
    return {"task_id": task.id, "status": "queued"}

@app.post("/send_to_nextcloud/")
def send_to_nextcloud(file_path: str):
    if not os.path.isabs(file_path):
        file_path = os.path.join(settings.workdir, 'processed', file_path)
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=400, detail=f"File {file_path} not found."
        )
    task = upload_to_nextcloud.delay(file_path)
    return {"task_id": task.id, "status": "queued"}

@app.post("/send_to_all_destinations/")
def send_to_all_destinations_endpoint(file_path: str):
    """
    Call the aggregator task that sends this file to dropbox, nextcloud, and paperless.
    """
    if not os.path.isabs(file_path):
        file_path = os.path.join(settings.workdir, 'processed', file_path)

    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=400, detail=f"File {file_path} not found."
        )

    task = send_to_all_destinations.delay(file_path)
    return {"task_id": task.id, "status": "queued", "file_path": file_path}

@app.post("/processall")
def process_all_pdfs_in_workdir():
    """
    Finds all .pdf files in <workdir> and enqueues them for processing.
    """
    target_dir = settings.workdir
    if not os.path.exists(target_dir):
        raise HTTPException(
            status_code=400, detail=f"Directory {target_dir} does not exist."
        )

    pdf_files = []
    for filename in os.listdir(target_dir):
        if filename.lower().endswith(".pdf"):
            pdf_files.append(filename)

    if not pdf_files:
        return {"message": "No PDF files found in that directory."}

    task_ids = []
    for pdf in pdf_files:
        file_path = os.path.join(target_dir, pdf)
        task = process_document.delay(file_path)  # Updated function call
        task_ids.append(task.id)

    return {
        "message": f"Enqueued {len(pdf_files)} PDFs to upload_to_s3",
        "pdf_files": pdf_files,
        "task_ids": task_ids
    }

@app.post("/ui-upload")
async def ui_upload(file: UploadFile = File(...)):
    """Endpoint to accept a user-uploaded file and enqueue it for processing."""
    workdir = "/workdir"
    target_path = os.path.join(workdir, file.filename)
    try:
        with open(target_path, "wb") as f:
            content = await file.read()
            f.write(content)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save file: {e}"
        )

    task = process_document.delay(target_path)  # Updated function call
    return {"task_id": task.id, "status": "queued"}

# Custom 404 - we can still return the Jinja2 template, or the old static file:
# For a dynamic 404 using the base layout, see "frontend/404.html" usage below:
@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: HTTPException):
    # Serve the 404 template directly
    templates = Jinja2Templates(directory=str(frontend_static_dir.parent / "templates"))
    return templates.TemplateResponse(
        "404.html",
        {"request": request},
        status_code=status.HTTP_404_NOT_FOUND
    )

@app.exception_handler(500)
async def custom_500_handler(request: Request, exc: Exception):
    templates = Jinja2Templates(directory=str(frontend_static_dir.parent / "templates"))
    # Option 1: Keep it simple, just show a funny 500 message:
    return templates.TemplateResponse(
        "500.html",
        {"request": request, "exc": exc},
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
    )

@app.get("/test-500")
def test_500():
    raise RuntimeError("Testing forced 500 error!")

# Include the frontend and auth routers
app.include_router(frontend_router)
app.include_router(auth_router)
app.include_router(api_router, prefix="/api")
