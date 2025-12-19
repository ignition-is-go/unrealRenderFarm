"""
Client to work/process render request, which launches executor locally and
updates status to the server
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

from util import client
from util import renderRequest


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

MODULE_PATH = os.path.dirname(os.path.abspath(__file__))

# render worker specific configuration (via environment variables)
# Defaults to hostname if WORKER_NAME not set (useful for cloned VMs)
WORKER_NAME = os.environ.get('WORKER_NAME', socket.gethostname())
UNREAL_EXE = os.environ.get('UNREAL_EXE', '')
UNREAL_PROJECT = os.environ.get('UNREAL_PROJECT', '')


def log_output(pipe, prefix='UE'):
    """Read and log output from subprocess pipe"""
    # Only show our executor logs
    patterns = [
        r'=== MyExecutor',
        r'HTTP PUT',
        r'HTTP response',
        r'SERVER_API_URL',
        r'Engine Warm Up Frame',
        r'Progress:.*%',
        r'Render finished',
    ]
    pattern = re.compile('|'.join(patterns))

    for line in iter(pipe.readline, ''):
        line = line.rstrip()
        if pattern.search(line):
            LOGGER.info('[%s] %s', prefix, line)
    pipe.close()


def render(uid, umap_path, useq_path, uconfig_path):
    """
    Render a job locally using the custom executor (myExecutor.py)

    Polls for cancellation during render and kills the process if cancelled.

    :param uid: str. render request uid
    :param umap_path: str. Unreal path to the map/level asset
    :param useq_path: str. Unreal path to the sequence asset
    :param uconfig_path: str. Unreal path to the preset/config asset
    :return: bool. True if completed, False if cancelled
    """
    command = [
        UNREAL_EXE,
        UNREAL_PROJECT,

        umap_path,
        "-JobId={}".format(uid),
        "-LevelSequence={}".format(useq_path),
        "-MoviePipelineConfig={}".format(uconfig_path),

        # use custom Python executor
        "-game",
        "-MoviePipelineLocalExecutorClass=/Script/MovieRenderPipelineCore.MoviePipelinePythonHostExecutor",
        "-ExecutorPythonClass=/Engine/PythonTypes.MyExecutor",

        "-windowed",
        "-resX=1280",
        "-resY=720",

        # logging
        "-StdOut",
        "-FullStdOutLogOutput"
    ]
    env = os.environ.copy()
    env["UE_PYTHONPATH"] = MODULE_PATH.replace('\\', '/')

    LOGGER.info("UE_PYTHONPATH: %s", env["UE_PYTHONPATH"])
    LOGGER.info("Command: %s", ' '.join(command))

    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        bufsize=1
    )

    # Start thread to read and log output
    log_thread = threading.Thread(target=log_output, args=(proc.stdout,))
    log_thread.daemon = True
    log_thread.start()

    # Poll for completion or cancellation
    while proc.poll() is None:
        # Check if job was cancelled
        rrequest = client.get_request(uid)
        if rrequest and rrequest.status == renderRequest.RenderStatus.cancelled:
            LOGGER.info('job %s cancelled, killing render process', uid)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            return False
        time.sleep(2)

    log_thread.join(timeout=2)
    return True


if __name__ == '__main__':
    # Validate required environment variables
    if not UNREAL_EXE:
        LOGGER.error('UNREAL_EXE environment variable not set')
        raise SystemExit(1)
    if not UNREAL_PROJECT:
        LOGGER.error('UNREAL_PROJECT environment variable not set')
        raise SystemExit(1)

    LOGGER.info('Starting render worker %s', WORKER_NAME)
    LOGGER.info('Unreal Editor: %s', UNREAL_EXE)
    LOGGER.info('Project: %s', UNREAL_PROJECT)
    while True:
        client.send_heartbeat(WORKER_NAME, status='idle')
        rrequests = client.get_all_requests() or []
        uids = [rrequest.uid for rrequest in rrequests
                if rrequest.worker == WORKER_NAME and
                rrequest.status == renderRequest.RenderStatus.ready_to_start]

        for uid in uids:
            LOGGER.info('rendering job %s', uid)
            client.send_heartbeat(WORKER_NAME, status='rendering')

            rrequest = client.get_request(uid)
            completed = render(
                uid,
                rrequest.umap_path,
                rrequest.useq_path,
                rrequest.uconfig_path
            )
            if completed:
                LOGGER.info("finished rendering job %s", uid)
                # Update server with finished status
                client.update_request(uid, progress=100, time_estimate='N/A', status='finished')
            else:
                LOGGER.info("job %s was cancelled", uid)

        time.sleep(10)
