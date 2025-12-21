"""
Client to work/process render request, which launches executor locally and
updates status to the server.

Hardened for production with:
- Exception handling in main loop
- Render timeout detection
- Unreal crash detection
- System metrics reporting
"""

from dotenv import load_dotenv
load_dotenv()

import logging
import os
import re
import socket
import subprocess
import threading
import time
from datetime import datetime

import psutil

from util import client
from util import renderRequest


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

MODULE_PATH = os.path.dirname(os.path.abspath(__file__))

# Configuration from environment variables
WORKER_NAME = os.environ.get('WORKER_NAME', socket.gethostname())
UNREAL_EXE = os.environ.get('UNREAL_EXE', '')
UNREAL_PROJECT = os.environ.get('UNREAL_PROJECT', '')
RENDER_TIMEOUT = int(os.environ.get('RENDER_TIMEOUT', '3600'))  # 1 hour default
POLL_INTERVAL = int(os.environ.get('POLL_INTERVAL', '10'))  # seconds


def log_output(pipe, prefix='UE'):
    """Read and log output from subprocess pipe"""
    # Patterns to INCLUDE in logs
    include_patterns = [
        r'=== MyExecutor',
        r'HTTP PUT',
        r'SERVER_API_URL',
        r'Progress:.*%',
        r'Render finished',
        r'LogPython: Error',
        r'LogPython: Warning',
        r'Pipeline initialized',
        r'FATAL:',
    ]
    # Patterns to EXCLUDE (noisy warnings)
    exclude_patterns = [
        r'Anima4D',
        r'UAnima4DStreamInfo',
        r'RshipTargetComponent',
        r'Subsystem not found',
        r'BeginDestroy',
        r'Destructor',
    ]
    include_re = re.compile('|'.join(include_patterns))
    exclude_re = re.compile('|'.join(exclude_patterns))

    try:
        for line in iter(pipe.readline, ''):
            line = line.rstrip()
            if include_re.search(line) and not exclude_re.search(line):
                LOGGER.info('[%s] %s', prefix, line)
    except Exception as e:
        LOGGER.warning('log_output error: %s', e)
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def get_system_metrics():
    """Get current system CPU and memory usage"""
    try:
        return {
            'cpu_percent': psutil.cpu_percent(interval=0.1),
            'memory_percent': psutil.virtual_memory().percent
        }
    except Exception as e:
        LOGGER.warning('Failed to get system metrics: %s', e)
        return {'cpu_percent': 0, 'memory_percent': 0}


def render(uid, umap_path, useq_path, uconfig_path):
    """
    Render a job locally using the custom executor (myExecutor.py)

    Polls for cancellation during render and kills the process if cancelled.
    Detects Unreal crashes and reports them correctly.

    :param uid: str. render request uid
    :param umap_path: str. Unreal path to the map/level asset
    :param useq_path: str. Unreal path to the sequence asset
    :param uconfig_path: str. Unreal path to the preset/config asset
    :return: tuple (success: bool, error_message: str or None)
    """
    command = [
        UNREAL_EXE,
        UNREAL_PROJECT,
        umap_path,
        "-JobId={}".format(uid),
        "-LevelSequence={}".format(useq_path),
        "-MoviePipelineConfig={}".format(uconfig_path),
        "-game",
        "-MoviePipelineLocalExecutorClass=/Script/MovieRenderPipelineCore.MoviePipelinePythonHostExecutor",
        "-ExecutorPythonClass=/Engine/PythonTypes.MyExecutor",
        "-windowed",
        "-resX=1280",
        "-resY=720",
        "-StdOut",
        "-FullStdOutLogOutput"
    ]
    env = os.environ.copy()
    env["UE_PYTHONPATH"] = MODULE_PATH.replace('\\', '/')

    LOGGER.info("UE_PYTHONPATH: %s", env["UE_PYTHONPATH"])
    LOGGER.info("Command: %s", ' '.join(command))

    start_time = datetime.now()

    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
            bufsize=1
        )
    except Exception as e:
        error_msg = f"Failed to start Unreal: {e}"
        LOGGER.error(error_msg)
        return False, error_msg

    # Start thread to read and log output
    log_thread = threading.Thread(target=log_output, args=(proc.stdout,))
    log_thread.daemon = True
    log_thread.start()

    # Update job with started timestamp
    client.update_request(uid, started_at=start_time.isoformat())

    # Poll for completion, cancellation, or timeout
    while proc.poll() is None:
        elapsed = (datetime.now() - start_time).total_seconds()

        # Check for timeout
        if elapsed > RENDER_TIMEOUT:
            LOGGER.error('Job %s timed out after %d seconds', uid, RENDER_TIMEOUT)
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            return False, f"Render timed out after {RENDER_TIMEOUT} seconds"

        # Check if job was cancelled
        try:
            rrequest = client.get_request(uid)
            if rrequest and rrequest.status == renderRequest.RenderStatus.cancelled:
                LOGGER.info('Job %s cancelled, killing render process', uid)
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return False, "Cancelled by user"
        except Exception as e:
            LOGGER.warning('Failed to check job status: %s', e)

        # Send heartbeat with metrics during render
        metrics = get_system_metrics()
        client.send_heartbeat(
            WORKER_NAME,
            status='rendering',
            current_job=uid,
            cpu_percent=metrics['cpu_percent'],
            memory_percent=metrics['memory_percent'],
            unreal_pid=proc.pid,
            render_started=start_time.isoformat()
        )

        time.sleep(2)

    # Process finished - check return code
    log_thread.join(timeout=2)

    if proc.returncode != 0:
        error_msg = f"Unreal exited with code {proc.returncode}"
        LOGGER.error('Job %s failed: %s', uid, error_msg)
        return False, error_msg

    return True, None


def process_job(uid):
    """
    Process a single job with full error handling.

    :param uid: str. job UID to process
    :return: bool. True if job completed successfully
    """
    try:
        rrequest = client.get_request(uid)
        if not rrequest:
            LOGGER.error('Job %s not found', uid)
            return False

        LOGGER.info('Starting job %s: %s', uid, rrequest.name)

        # Update status to in_progress
        client.update_request(uid, status=renderRequest.RenderStatus.in_progress)

        # Run the render
        success, error_message = render(
            uid,
            rrequest.umap_path,
            rrequest.useq_path,
            rrequest.uconfig_path
        )

        completed_at = datetime.now().isoformat()

        if success:
            LOGGER.info("Finished job %s successfully", uid)
            client.update_request(
                uid,
                progress=100,
                status=renderRequest.RenderStatus.finished,
                time_estimate='N/A',
                completed_at=completed_at
            )
            return True
        else:
            LOGGER.error("Job %s failed: %s", uid, error_message)
            client.update_request(
                uid,
                status=renderRequest.RenderStatus.errored,
                error_message=error_message,
                completed_at=completed_at
            )
            client.report_error(WORKER_NAME, error_message, job_uid=uid)
            return False

    except Exception as e:
        LOGGER.exception('Unexpected error processing job %s', uid)
        try:
            client.update_request(
                uid,
                status=renderRequest.RenderStatus.errored,
                error_message=str(e),
                completed_at=datetime.now().isoformat()
            )
            client.report_error(WORKER_NAME, str(e), job_uid=uid)
        except Exception:
            pass
        return False


def main():
    """Main worker loop with exception handling."""
    # Validate required environment variables
    if not UNREAL_EXE:
        LOGGER.error('UNREAL_EXE environment variable not set')
        raise SystemExit(1)
    if not UNREAL_PROJECT:
        LOGGER.error('UNREAL_PROJECT environment variable not set')
        raise SystemExit(1)

    LOGGER.info('Starting render worker: %s', WORKER_NAME)
    LOGGER.info('Unreal Editor: %s', UNREAL_EXE)
    LOGGER.info('Project: %s', UNREAL_PROJECT)
    LOGGER.info('Render timeout: %d seconds', RENDER_TIMEOUT)

    server_connected = False
    ever_connected = False

    while True:
        try:
            # Send heartbeat with current metrics
            metrics = get_system_metrics()
            client.send_heartbeat(
                WORKER_NAME,
                status='idle',
                cpu_percent=metrics['cpu_percent'],
                memory_percent=metrics['memory_percent']
            )

            # Get jobs assigned to this worker using efficient filtered API
            jobs = client.get_my_jobs(WORKER_NAME)
            if jobs is None:
                if server_connected:
                    LOGGER.warning('Lost connection to server, will keep retrying...')
                    server_connected = False
                jobs = []
            elif not server_connected:
                if ever_connected:
                    LOGGER.info('Reconnected to server at %s', client.SERVER_URL)
                else:
                    LOGGER.info('Connected to server at %s', client.SERVER_URL)
                    ever_connected = True
                server_connected = True

            # Filter for ready_to_start jobs
            ready_jobs = [
                job for job in jobs
                if job.status == renderRequest.RenderStatus.ready_to_start
            ]

            # Process one job at a time
            for job in ready_jobs:
                process_job(job.uid)
                break  # Only process one job per poll cycle

        except Exception as e:
            LOGGER.exception('Worker error: %s', e)
            try:
                client.report_error(WORKER_NAME, f"Worker error: {e}")
            except Exception:
                pass
            # Wait longer before retrying after an error
            time.sleep(30)
            continue

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
