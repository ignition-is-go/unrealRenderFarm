"""
Client request utility functions with retry logic for resilient API calls.
"""

import logging
import os

import requests

from . import renderRequest
from .retry import retry


LOGGER = logging.getLogger(__name__)

SERVER_URL = os.environ.get('RENDER_SERVER_URL', 'http://127.0.0.1:5000')
SERVER_API_URL = SERVER_URL + '/api'

# Timeout for all requests (connect, read) in seconds
REQUEST_TIMEOUT = (5, 30)


@retry(max_attempts=3, backoff=2, exceptions=(requests.exceptions.RequestException,))
def get_all_requests():
    """
    Call a 'GET' method for all render requests from the server

    :return: [renderRequest.RenderRequest]. request objects
    """
    response = requests.get(SERVER_API_URL + '/get', timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    results = response.json()['results']
    return [renderRequest.RenderRequest.from_dict(result) for result in results]


@retry(max_attempts=3, backoff=2, exceptions=(requests.exceptions.RequestException,))
def get_my_jobs(worker_name):
    """
    Get only jobs assigned to this worker (more efficient than get_all_requests).

    :param worker_name: str. worker name
    :return: [renderRequest.RenderRequest]. request objects assigned to this worker
    """
    response = requests.get(
        SERVER_API_URL + '/jobs/mine/{}'.format(worker_name),
        timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    results = response.json().get('jobs', [])
    return [renderRequest.RenderRequest.from_dict(result) for result in results]


@retry(max_attempts=3, backoff=2, exceptions=(requests.exceptions.RequestException,))
def get_request(uid):
    """
    Call a 'GET' method for a specific render request from the server

    :param uid: str. request uid
    :return: renderRequest.RenderRequest. request object
    """
    response = requests.get(
        SERVER_API_URL + '/get/{}'.format(uid),
        timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    return renderRequest.RenderRequest.from_dict(response.json())


@retry(max_attempts=3, backoff=2, exceptions=(requests.exceptions.RequestException,))
def add_request(d):
    """
    Call a 'POST' method to add a render request to the server

    :param d: dict. render request represented as dictionary
    :return: renderRequest.RenderRequest. request object created
    """
    response = requests.post(
        SERVER_API_URL + '/post',
        json=d,
        timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    return renderRequest.RenderRequest.from_dict(response.json())


@retry(max_attempts=3, backoff=2, exceptions=(requests.exceptions.RequestException,))
def remove_request(uid):
    """
    Call a 'DELETE' method to remove a render request from the server

    :param uid: str. render request uid
    :return: dict. response
    """
    response = requests.delete(
        SERVER_API_URL + '/delete/{}'.format(uid),
        timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def send_heartbeat(worker_name, status='idle', current_job=None,
                   cpu_percent=None, memory_percent=None, unreal_pid=None,
                   render_started=None):
    """
    Send heartbeat to server to register worker with system metrics.

    :param worker_name: str. worker name (hostname)
    :param status: str. worker status (idle/rendering)
    :param current_job: str. UID of current job being rendered
    :param cpu_percent: float. CPU usage percentage
    :param memory_percent: float. memory usage percentage
    :param unreal_pid: int. PID of Unreal process if rendering
    :param render_started: str. ISO timestamp when current render started
    """
    data = {
        'worker_name': worker_name,
        'status': status
    }
    if current_job is not None:
        data['current_job'] = current_job
    if cpu_percent is not None:
        data['cpu_percent'] = cpu_percent
    if memory_percent is not None:
        data['memory_percent'] = memory_percent
    if unreal_pid is not None:
        data['unreal_pid'] = unreal_pid
    if render_started is not None:
        data['render_started'] = render_started

    try:
        requests.post(
            SERVER_API_URL + '/worker/heartbeat',
            json=data,
            timeout=REQUEST_TIMEOUT
        )
    except requests.exceptions.RequestException as e:
        LOGGER.warning('failed to send heartbeat: %s', e)


def update_request(uid, progress=None, status=None, time_estimate=None,
                   error_message=None, started_at=None, completed_at=None):
    """
    Call a 'PUT' method to update a render request on the server.

    :param uid: str. render request uid to update
    :param progress: int. updated progress (optional)
    :param status: renderRequest.RenderStatus. updated status (optional)
    :param time_estimate: str. updated estimate remaining time (optional)
    :param error_message: str. error message if job failed (optional)
    :param started_at: str. ISO timestamp when render started (optional)
    :param completed_at: str. ISO timestamp when render completed (optional)
    :return: renderRequest.RenderRequest. updated render request object
    """
    data = {}
    if progress is not None:
        data['progress'] = progress
    if status is not None:
        data['status'] = status
    if time_estimate is not None:
        data['time_estimate'] = time_estimate
    if error_message is not None:
        data['error_message'] = error_message
    if started_at is not None:
        data['started_at'] = started_at
    if completed_at is not None:
        data['completed_at'] = completed_at

    try:
        response = requests.put(
            SERVER_API_URL + '/put/{}'.format(uid),
            json=data,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return renderRequest.RenderRequest.from_dict(response.json())
    except requests.exceptions.RequestException as e:
        LOGGER.error('failed to update request %s: %s', uid, e)
        return None


def report_error(worker_name, message, job_uid=None):
    """
    Report an error to the server for logging/display.

    :param worker_name: str. worker name that encountered the error
    :param message: str. error message
    :param job_uid: str. UID of related job (optional)
    """
    data = {
        'worker': worker_name,
        'message': message
    }
    if job_uid:
        data['job_uid'] = job_uid

    try:
        requests.post(
            SERVER_API_URL + '/worker/error',
            json=data,
            timeout=REQUEST_TIMEOUT
        )
    except requests.exceptions.RequestException as e:
        LOGGER.warning('failed to report error to server: %s', e)
