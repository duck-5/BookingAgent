from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
from .state import state

app = FastAPI(title="Booking Agent Monitor")

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def read_root():
    return FileResponse(os.path.join(static_dir, "index.html"))

@app.get("/api/status")
async def get_status():
    return state.get_snapshot()

@app.post("/api/action/{name}")
async def trigger_action(name: str):
    if state.trigger_action(name):
        return {"status": "ok", "message": f"Action '{name}' triggered"}
    raise HTTPException(status_code=404, detail="Action not found")

@app.post("/api/history/clear/{mode}")
async def clear_history(mode: str):
    if mode in ["all", "empty"]:
        state.clear_history(mode)
        return {"status": "ok", "message": f"History cleared: {mode}"}
    raise HTTPException(status_code=400, detail="Invalid mode. Must be 'all' or 'empty'.")
