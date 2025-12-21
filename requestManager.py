"""
Remote Render HTTP Server with REST API

Hardened for production with:
- Rate limiting
- Worker persistence in database
- Filtered APIs for efficient polling
- Error tracking and reporting
- Job state machine validation
"""

import atexit
import logging
import os
import threading
import time
from datetime import datetime

from flask import Flask
from flask import request
from flask import render_template
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from util import renderRequest


MODULE_PATH = os.path.dirname(os.path.abspath(__file__))
HTML_FOLDER = os.path.join(MODULE_PATH, 'html')

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

# Configuration
WORKER_TIMEOUT = int(os.environ.get('WORKER_TIMEOUT', '30'))  # seconds
JOB_TIMEOUT = int(os.environ.get('JOB_TIMEOUT', '1800'))  # 30 minutes default
LAST_ASSIGNED_WORKER = None

# Valid state transitions for job state machine
VALID_TRANSITIONS = {
    renderRequest.RenderStatus.unassigned: [
        renderRequest.RenderStatus.ready_to_start,
        renderRequest.RenderStatus.cancelled,
    ],
    renderRequest.RenderStatus.ready_to_start: [
        renderRequest.RenderStatus.in_progress,
        renderRequest.RenderStatus.cancelled,
        renderRequest.RenderStatus.unassigned,  # For reassignment
    ],
    renderRequest.RenderStatus.in_progress: [
        renderRequest.RenderStatus.finished,
        renderRequest.RenderStatus.errored,
        renderRequest.RenderStatus.cancelled,
        renderRequest.RenderStatus.ready_to_start,  # For retries
    ],
    renderRequest.RenderStatus.finished: [],  # Terminal state
    renderRequest.RenderStatus.errored: [
        renderRequest.RenderStatus.ready_to_start,  # Allow retry
        renderRequest.RenderStatus.failed,  # Max retries exceeded
    ],
    renderRequest.RenderStatus.failed: [],  # Terminal state
    renderRequest.RenderStatus.cancelled: [
        renderRequest.RenderStatus.ready_to_start,  # Allow restart
    ],
    renderRequest.RenderStatus.paused: [
        renderRequest.RenderStatus.ready_to_start,
        renderRequest.RenderStatus.cancelled,
    ],
}

# region HTTP REST API
app = Flask(__name__)

# Rate limiting
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per minute"],
    storage_uri="memory://"
)


@app.route('/')
def index_page():
    """Server landing page"""
    rrequests = renderRequest.read_all()
    jsons = [rrequest.to_dict() for rrequest in rrequests]
    workers = get_workers_status()
    return render_template('index.html', requests=jsons, workers=workers)


@app.get('/partials/projects')
def partials_projects():
    """List available project configs"""
    import glob
    import json as json_module
    project_files = glob.glob(os.path.join(MODULE_PATH, 'projects', '*.json'))
    projects = []
    for pf in project_files:
        with open(pf) as f:
            data = json_module.load(f)
            data['_file'] = os.path.basename(pf)
            projects.append(data)
    return render_template('partials/projects.html', projects=projects)


@app.post('/api/submit/<project_file>')
def submit_project(project_file):
    """Submit all sequences from a project"""
    import json as json_module
    project_path = os.path.join(MODULE_PATH, 'projects', project_file)
    if not os.path.exists(project_path):
        return {'error': 'project not found'}, 404

    with open(project_path) as f:
        project = json_module.load(f)

    submitted = []
    for seq in project['sequences']:
        seq_name = seq.rstrip('/').split('/')[-1].split('.')[0]
        data = {
            'name': seq_name,
            'umap_path': project['map'],
            'useq_path': seq,
            'uconfig_path': project['config'],
        }
        rrequest = renderRequest.RenderRequest.from_dict(data)
        rrequest.write_json()
        new_request_trigger(rrequest)
        submitted.append(rrequest.uid)

    LOGGER.info('Submitted %d jobs from %s', len(submitted), project_file)
    return {'submitted': submitted}


@app.get('/partials/workers')
def partials_workers():
    """Partial template for htmx polling"""
    workers = get_workers_status()
    return render_template('partials/workers.html', workers=workers)


@app.get('/partials/jobs')
def partials_jobs():
    """Partial template for htmx polling"""
    rrequests = renderRequest.read_all()
    jsons = [rrequest.to_dict() for rrequest in rrequests]
    return render_template('partials/jobs.html', requests=jsons)


@app.get('/partials/summary')
def partials_summary():
    """Summary bar partial for htmx polling"""
    rrequests = renderRequest.read_all()
    jsons = [rrequest.to_dict() for rrequest in rrequests]
    workers = get_workers_status()
    return render_template('partials/summary.html', requests=jsons, workers=workers)


@app.get('/partials/errors')
def partials_errors():
    """Errors panel partial for htmx polling"""
    errors = renderRequest.get_recent_errors(limit=20)
    return render_template('partials/errors.html', errors=errors)


# API Endpoints

@app.get('/api/get')
def get_all_requests():
    """Get all render requests"""
    rrequests = renderRequest.read_all()
    jsons = [rrequest.to_dict() for rrequest in rrequests]
    return {"results": jsons}


@app.get('/api/get/<uid>')
def get_request(uid):
    """Get a specific render request by UID"""
    rr = renderRequest.RenderRequest.from_db(uid)
    if not rr:
        return {'error': 'job not found'}, 404
    return rr.to_dict()


@app.get('/api/jobs/mine/<worker_name>')
def get_my_jobs(worker_name):
    """
    Get only jobs assigned to a specific worker.
    More efficient than get_all_requests for workers.
    """
    rrequests = renderRequest.read_all()
    my_jobs = [rr.to_dict() for rr in rrequests if rr.worker == worker_name]
    return {'jobs': my_jobs}


@app.delete('/api/delete/<uid>')
def delete_request(uid):
    """Delete a render request"""
    renderRequest.remove_db(uid)
    return {'ok': True}


@app.delete('/api/delete-all')
def delete_all_requests():
    """Delete all render requests"""
    rrequests = renderRequest.read_all()
    count = len(rrequests)
    for rr in rrequests:
        renderRequest.remove_db(rr.uid)
    LOGGER.info('deleted all %d jobs', count)
    return {'ok': True, 'deleted': count}


@app.post('/api/post')
def create_request():
    """Create a new render request"""
    data = request.get_json(force=True)
    rrequest = renderRequest.RenderRequest.from_dict(data)
    rrequest.write_json()
    new_request_trigger(rrequest)
    return rrequest.to_dict()


def is_valid_transition(current_status, new_status):
    """Check if a status transition is valid."""
    if current_status == new_status:
        return True
    allowed = VALID_TRANSITIONS.get(current_status, [])
    return new_status in allowed


@app.put('/api/put/<uid>')
@limiter.limit("60 per minute")
def update_request(uid):
    """Update a render request"""
    data = request.get_json(force=True)

    rr = renderRequest.RenderRequest.from_db(uid)
    if not rr:
        return {'error': 'job not found'}, 404

    # Validate state transition
    new_status = data.get('status')
    if new_status and new_status != rr.status:
        if not is_valid_transition(rr.status, new_status):
            LOGGER.warning(
                'Invalid state transition for job %s: %s -> %s',
                uid, rr.status, new_status
            )
            return {
                'error': 'invalid state transition',
                'current_status': rr.status,
                'requested_status': new_status,
                'allowed_transitions': VALID_TRANSITIONS.get(rr.status, [])
            }, 400

    rr.update(
        progress=int(float(data.get('progress', 0))) if data.get('progress') is not None else None,
        time_estimate=data.get('time_estimate'),
        status=new_status,
        warmup_current=data.get('warmup_current'),
        warmup_total=data.get('warmup_total'),
        error_message=data.get('error_message'),
        started_at=data.get('started_at'),
        completed_at=data.get('completed_at')
    )
    return rr.to_dict()


@app.post('/api/cancel/<uid>')
def cancel_request(uid):
    """Cancel a render job"""
    rr = renderRequest.RenderRequest.from_db(uid)
    if not rr:
        return {'error': 'job not found'}, 404

    rr.update(status=renderRequest.RenderStatus.cancelled)
    LOGGER.info('cancelled job %s', uid)
    return rr.to_dict()


@app.post('/api/retry/<uid>')
def retry_request(uid):
    """Retry a failed/errored job"""
    rr = renderRequest.RenderRequest.from_db(uid)
    if not rr:
        return {'error': 'job not found'}, 404

    if rr.status not in (renderRequest.RenderStatus.errored, renderRequest.RenderStatus.cancelled):
        return {'error': 'can only retry errored or cancelled jobs'}, 400

    # Increment retry count
    new_retry_count = (rr.retry_count or 0) + 1

    if new_retry_count > renderRequest.MAX_RETRIES:
        rr.update(status=renderRequest.RenderStatus.failed)
        return {'error': 'max retries exceeded', 'retry_count': new_retry_count}, 400

    # Reset for retry
    rr.retry_count = new_retry_count
    rr.error_message = ''
    rr.progress = 0
    rr.update(status=renderRequest.RenderStatus.ready_to_start)

    LOGGER.info('retrying job %s (attempt %d)', uid, new_retry_count)
    return rr.to_dict()


# Worker API

@app.post('/api/worker/heartbeat')
def worker_heartbeat():
    """Worker heartbeat with metrics"""
    data = request.get_json(force=True)
    worker_name = data.get('worker_name')
    if not worker_name:
        return {'error': 'worker_name required'}, 400

    worker_data = {
        'name': worker_name,
        'status': data.get('status', 'idle'),
        'current_job': data.get('current_job'),
        'cpu_percent': data.get('cpu_percent'),
        'memory_percent': data.get('memory_percent'),
        'unreal_pid': data.get('unreal_pid'),
        'render_started': data.get('render_started'),
        'last_seen': datetime.now().isoformat()
    }

    renderRequest.upsert_worker(worker_data)
    LOGGER.debug('heartbeat from %s', worker_name)
    return {'ok': True}


@app.post('/api/worker/error')
def worker_error():
    """Log an error from a worker"""
    data = request.get_json(force=True)
    worker_name = data.get('worker')
    message = data.get('message', 'Unknown error')
    job_uid = data.get('job_uid')

    renderRequest.log_error(worker_name, job_uid, message)
    LOGGER.warning('Error from %s: %s', worker_name, message)
    return {'ok': True}


@app.get('/api/workers')
def get_workers():
    """Get all registered workers"""
    workers = get_workers_status()
    return {'workers': workers}


@app.get('/api/errors')
def get_errors():
    """Get recent errors"""
    errors = renderRequest.get_recent_errors(limit=20)
    return {'errors': errors}


@app.delete('/api/errors')
def clear_errors():
    """Clear all errors"""
    renderRequest.clear_errors()
    LOGGER.info('cleared error log')
    return {'ok': True}


@app.get('/api/dashboard')
def dashboard_api():
    """Dashboard summary endpoint - efficient aggregated data"""
    workers = get_workers_status()
    rrequests = renderRequest.read_all()

    # Count workers by status
    online_count = sum(1 for w in workers if w.get('online'))
    idle_count = sum(1 for w in workers if w.get('online') and w.get('status') == 'idle')
    rendering_count = sum(1 for w in workers if w.get('online') and w.get('status') == 'rendering')

    # Count jobs by status
    job_counts = {}
    for rr in rrequests:
        status = rr.status or 'unknown'
        job_counts[status] = job_counts.get(status, 0) + 1

    return {
        'workers': {
            'total': len(workers),
            'online': online_count,
            'idle': idle_count,
            'rendering': rendering_count
        },
        'jobs': {
            'total': len(rrequests),
            'by_status': job_counts
        },
        'recent_errors': renderRequest.get_recent_errors(limit=5)
    }


@app.get('/api/health')
def health_check():
    """Health check endpoint"""
    workers = get_workers_status()
    online_workers = sum(1 for w in workers if w.get('online'))

    return {
        'status': 'healthy',
        'workers_online': online_workers,
        'watchdog_running': _watchdog_thread is not None and _watchdog_thread.is_alive()
    }


# endregion


# region Helper functions

def get_workers_status():
    """Get all workers with online status calculated"""
    now = datetime.now()
    workers = renderRequest.get_all_workers()
    result = []

    for worker in workers:
        last_seen_str = worker.get('last_seen', '')
        if last_seen_str:
            try:
                last_seen = datetime.fromisoformat(last_seen_str)
                online = (now - last_seen).total_seconds() < WORKER_TIMEOUT
            except (ValueError, TypeError):
                online = False
        else:
            online = False

        result.append({
            'name': worker.get('name'),
            'status': worker.get('status', 'unknown'),
            'online': online,
            'current_job': worker.get('current_job'),
            'cpu_percent': worker.get('cpu_percent'),
            'memory_percent': worker.get('memory_percent'),
            'last_seen': last_seen_str
        })

    return result


def get_available_worker():
    """Get an available worker (online and idle) using round-robin"""
    global LAST_ASSIGNED_WORKER

    workers = get_workers_status()
    available = [w['name'] for w in workers if w.get('online') and w.get('status') == 'idle']

    if not available:
        return None

    # Round-robin
    if LAST_ASSIGNED_WORKER in available:
        idx = available.index(LAST_ASSIGNED_WORKER)
        next_idx = (idx + 1) % len(available)
    else:
        next_idx = 0

    LAST_ASSIGNED_WORKER = available[next_idx]
    return LAST_ASSIGNED_WORKER


def new_request_trigger(rrequest):
    """Triggers when a new render request is created"""
    if rrequest.worker:
        return

    worker = get_available_worker()
    if not worker:
        LOGGER.warning('no workers available for job %s', rrequest.uid)
        return

    assign_request(rrequest, worker)
    LOGGER.info('assigned job %s to %s', rrequest.uid, worker)


def assign_request(rrequest, worker):
    """Assign a render request to a worker"""
    rrequest.assign(worker)
    rrequest.update(status=renderRequest.RenderStatus.ready_to_start)


def check_stuck_jobs():
    """Check for stuck jobs and reset them"""
    now = datetime.now()
    rrequests = renderRequest.read_all()
    workers = {w['name']: w for w in get_workers_status()}

    for rr in rrequests:
        if rr.status != renderRequest.RenderStatus.in_progress:
            continue

        is_stuck = False
        reason = ''

        if rr.worker:
            worker = workers.get(rr.worker)
            if not worker:
                is_stuck = True
                reason = f'worker {rr.worker} not registered'
            elif not worker.get('online'):
                is_stuck = True
                reason = f'worker {rr.worker} is offline'
            elif rr.started_at:
                try:
                    started = datetime.fromisoformat(rr.started_at)
                    if (now - started).total_seconds() > JOB_TIMEOUT:
                        is_stuck = True
                        reason = f'job exceeded {JOB_TIMEOUT}s timeout'
                except (ValueError, TypeError):
                    pass
        else:
            is_stuck = True
            reason = 'no worker assigned'

        if is_stuck:
            LOGGER.warning('Resetting stuck job %s: %s', rr.uid, reason)
            rr.worker = ''
            rr.update(
                status=renderRequest.RenderStatus.ready_to_start,
                error_message=f'Reset: {reason}'
            )
            new_request_trigger(rr)

# endregion


# region Watchdog

_watchdog_thread = None
_watchdog_stop = threading.Event()


def watchdog_loop():
    """Background loop that checks for stuck jobs"""
    LOGGER.info('Job watchdog started')
    while not _watchdog_stop.wait(timeout=60):
        try:
            check_stuck_jobs()
        except Exception as e:
            LOGGER.error('Watchdog error: %s', e)
    LOGGER.info('Job watchdog stopped')


def start_watchdog():
    """Start the watchdog thread"""
    global _watchdog_thread
    if _watchdog_thread is None or not _watchdog_thread.is_alive():
        _watchdog_stop.clear()
        _watchdog_thread = threading.Thread(target=watchdog_loop, daemon=True)
        _watchdog_thread.start()


def stop_watchdog():
    """Stop the watchdog thread"""
    _watchdog_stop.set()
    if _watchdog_thread:
        _watchdog_thread.join(timeout=5)

# endregion


if __name__ == '__main__':
    start_watchdog()
    atexit.register(stop_watchdog)

    host = os.environ.get('RENDER_SERVER_HOST', '0.0.0.0')
    port = int(os.environ.get('RENDER_SERVER_PORT', '5000'))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')

    if debug:
        LOGGER.warning('Running in DEBUG mode - do not use in production!')

    app.run(host=host, port=port, debug=debug)
