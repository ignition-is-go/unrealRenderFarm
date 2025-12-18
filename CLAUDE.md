# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Unreal Engine 5 distributed render farm using Flask REST API and Movie Render Queue. Workers poll the server for assigned jobs and launch Unreal Engine to execute renders using a custom Python executor.

## Infrastructure

Running on Proxmox VMs:
- **Server VM** (Linux) - Runs `requestManager.py` Flask server
- **Render node VMs** (Windows) - Run `requestWorker.py`, each with Unreal Engine installed and GPU passthrough

Workers default to using hostname as `WORKER_NAME`, making it easy to clone VMs without reconfiguration.

## Commands

```bash
# Install dependencies (uses uv)
uv sync

# Run the manager/server (coordinates jobs, serves web UI)
uv run python requestManager.py

# Run a worker (polls for jobs, executes Unreal renders)
uv run python requestWorker.py

# Submit test render jobs
uv run python requestSubmitter.py
```

## Architecture

**Three main components:**

1. **requestManager.py** - Flask server on port 5000
   - REST API (`/api/get`, `/api/post`, `/api/put/<uid>`, `/api/delete/<uid>`)
   - Web UI at `/` showing job status
   - Assigns incoming jobs to workers via `new_request_trigger()`

2. **requestWorker.py** - Render worker process
   - Polls server every 10s for jobs assigned to its `WORKER_NAME`
   - Launches Unreal via subprocess with Movie Render Queue flags
   - Sets `UE_PYTHONPATH` so Unreal can import `myExecutor.py`

3. **myExecutor.py** - Unreal Python executor (runs inside Unreal Engine)
   - Custom `MoviePipelinePythonHostExecutor` subclass
   - Parses command-line args for job ID, sequence, config
   - Sends HTTP PUT requests to update progress/status during render

**Data flow:**
```
requestSubmitter → POST /api/post → requestManager (assigns worker)
                                         ↓
requestWorker ← polls /api/get ← finds ready_to_start job
      ↓
Unreal Engine + myExecutor → PUT /api/put/<uid> → updates progress
```

**Persistence:** JSON files in `database/` directory (one file per job UID). See `util/renderRequest.py` for the `RenderRequest` model.

## Configuration

Copy `.env.example` to `.env` and configure:
- `RENDER_SERVER_URL` - Server address for workers/submitters
- `WORKER_NAME` - Worker identifier (defaults to hostname)
- `UNREAL_EXE` - Path to UnrealEditor.exe
- `UNREAL_PROJECT` - Path to .uproject file

## Key Files

- `util/renderRequest.py` - `RenderRequest` class and `RenderStatus` enum
- `util/client.py` - HTTP client wrapper for API calls
- `templates/index.html` - Web UI template
