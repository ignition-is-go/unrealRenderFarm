"""
Remote Render HTTP Server with REST API

Also manages render request (which currently only involves assigning
jobs to worker)
"""

import logging
import time
import os

from flask import Flask
from flask import request
from flask import render_template

from util import renderRequest


MODULE_PATH = os.path.dirname(os.path.abspath(__file__))
HTML_FOLDER = os.path.join(MODULE_PATH, 'html')

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

# Worker registry: {worker_name: {status, last_seen, current_job}}
WORKERS = {}
WORKER_TIMEOUT = 30  # seconds before worker considered offline

# region HTTP REST API
app = Flask(__name__)


@app.route('/')
def index_page():
    """
    Server landing page
    """
    rrequests = renderRequest.read_all()
    jsons = [rrequest.to_dict() for rrequest in rrequests]

    # Get worker status
    now = time.time()
    workers = []
    for name, info in WORKERS.items():
        online = (now - info['last_seen']) < WORKER_TIMEOUT
        workers.append({
            'name': name,
            'status': info['status'],
            'online': online
        })

    return render_template('index.html', requests=jsons, workers=workers)


@app.get('/partials/projects')
def partials_projects():
    """List available project configs"""
    import glob
    project_files = glob.glob(os.path.join(MODULE_PATH, 'projects', '*.json'))
    projects = []
    for pf in project_files:
        with open(pf) as f:
            import json
            data = json.load(f)
            data['_file'] = os.path.basename(pf)
            projects.append(data)
    return render_template('partials/projects.html', projects=projects)


@app.post('/api/submit/<project_file>')
def submit_project(project_file):
    """Submit all sequences from a project"""
    import json
    project_path = os.path.join(MODULE_PATH, 'projects', project_file)
    if not os.path.exists(project_path):
        return {'error': 'project not found'}, 404

    with open(project_path) as f:
        project = json.load(f)

    submitted = []
    for seq in project['sequences']:
        # seq is the full path, extract name from it (last part before any dot)
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
    now = time.time()
    workers = []
    for name, info in WORKERS.items():
        online = (now - info['last_seen']) < WORKER_TIMEOUT
        workers.append({
            'name': name,
            'status': info['status'],
            'online': online
        })
    return render_template('partials/workers.html', workers=workers)


@app.get('/partials/jobs')
def partials_jobs():
    """Partial template for htmx polling"""
    rrequests = renderRequest.read_all()
    jsons = [rrequest.to_dict() for rrequest in rrequests]
    return render_template('partials/jobs.html', requests=jsons)


@app.get('/api/get')
def get_all_requests():
    """
    Server GET api response, query database

    :return: dict. an encapsulated dictionary with all render request serialized
    """
    rrequests = renderRequest.read_all()
    jsons = [rrequest.to_dict() for rrequest in rrequests]

    return {"results": jsons}


@app.get('/api/get/<uid>')
def get_request(uid):
    """
    Server GET api response for a specific uid request, query database

    :param uid: str. render request uid
    :return: dict. a render request serialized as dictionary
    """
    rr = renderRequest.RenderRequest.from_db(uid)
    return rr.to_dict()


@app.delete('/api/delete/<uid>')
def delete_request(uid):
    """
    Server DELETE api response, delete render request from database

    :param uid: str. render request uid
    """
    renderRequest.remove_db(uid)
    return {'ok': True}


@app.post('/api/post')
def create_request():
    """
    Server POST api response handling, with json data attached, creates
    a render request in database

    :return: dict. newly created render request serialized as dictionary
    """
    data = request.get_json(force=True)
    rrequest = renderRequest.RenderRequest.from_dict(data)
    rrequest.write_json()
    new_request_trigger(rrequest)

    return rrequest.to_dict()


@app.put('/api/put/<uid>')
def update_request(uid):
    """
    Server PUT api response handling, update render request in database

    :param uid: str. uid of render request to update
    :return: dict. updated render request serialized as dictionary
    """
    # unreal sends plain text
    content = request.data.decode('utf-8')
    progress, time_estimate, status = content.split(';')

    rr = renderRequest.RenderRequest.from_db(uid)
    if not rr:
        return {}

    rr.update(
        progress=int(float(progress)),
        time_estimate=time_estimate,
        status=status
    )
    return rr.to_dict()


@app.post('/api/cancel/<uid>')
def cancel_request(uid):
    """
    Cancel a render job - sets status to cancelled so workers skip it

    :param uid: str. uid of render request to cancel
    :return: dict. updated render request serialized as dictionary
    """
    rr = renderRequest.RenderRequest.from_db(uid)
    if not rr:
        return {'error': 'job not found'}, 404

    rr.update(status=renderRequest.RenderStatus.cancelled)
    LOGGER.info('cancelled job %s', uid)
    return rr.to_dict()


# Worker API

@app.post('/api/worker/heartbeat')
def worker_heartbeat():
    """
    Worker heartbeat - registers worker and updates last seen time
    """
    data = request.get_json(force=True)
    worker_name = data.get('worker_name')
    if not worker_name:
        return {'error': 'worker_name required'}, 400

    WORKERS[worker_name] = {
        'last_seen': time.time(),
        'status': data.get('status', 'idle')
    }
    LOGGER.info('heartbeat from %s', worker_name)
    return {'ok': True}


@app.get('/api/workers')
def get_workers():
    """
    Get all registered workers and their status
    """
    now = time.time()
    workers = {}
    for name, info in WORKERS.items():
        workers[name] = {
            'status': info['status'],
            'online': (now - info['last_seen']) < WORKER_TIMEOUT
        }
    return {'workers': workers}


# endregion


def get_available_worker():
    """
    Get an available worker (online and idle)
    """
    now = time.time()
    for name, info in WORKERS.items():
        if (now - info['last_seen']) < WORKER_TIMEOUT:
            return name
    return None


def new_request_trigger(rrequest):
    """
    Triggers when a client posts a new render request to the server
    """
    if rrequest.worker:
        return

    worker = get_available_worker()
    if not worker:
        LOGGER.warning('no workers available for job %s', rrequest.uid)
        return

    assign_request(rrequest, worker)
    LOGGER.info('assigned job %s to %s', rrequest.uid, worker)


def assign_request(rrequest, worker):
    """
    Assign render request to worker

    :param rrequest: renderRequest.RenderRequest. a render request object
    :param worker: str. worker name
    """
    rrequest.assign(worker)
    rrequest.update(status=renderRequest.RenderStatus.ready_to_start)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
