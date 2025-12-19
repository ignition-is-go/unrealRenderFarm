"""
Client to work/process render request, which launches executor locally and
updates status to the server
"""

from dotenv import load_dotenv
load_dotenv()

import logging
import os
import socket
import subprocess
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


def render(uid, umap_path, useq_path, uconfig_path):
    """
    Render a job locally using the custom executor (myExecutor.py)

    Note:
    I only listed the necessary arguments here,
    we can easily add custom commandline flags like '-StartFrame', '-FrameRate' etc.
    but we also need to implement in the MyExecutor class as well

    :param uid: str. render request uid
    :param umap_path: str. Unreal path to the map/level asset
    :param useq_path: str. Unreal path to the sequence asset
    :param uconfig_path: str. Unreal path to the preset/config asset
    :return: (str. str). output and error messages
    """
    command = [
        UNREAL_EXE,
        UNREAL_PROJECT,

        umap_path,
        "-JobId={}".format(uid),
        "-LevelSequence={}".format(useq_path),
        "-MoviePipelineConfig={}".format(uconfig_path),

        # required
        "-game",
        "-MoviePipelineLocalExecutorClass=/Script/MovieRenderPipelineCore.MoviePipelinePythonHostExecutor",
        "-ExecutorPythonClass=/Engine/PythonTypes.MyExecutor",

        # render preview
        "-windowed",
        "-resX=1280",
        "-resY=720",

        # logging
        "-StdOut",
        "-FullStdOutLogOutput"
    ]
    env = os.environ.copy()
    env["UE_PYTHONPATH"] = MODULE_PATH.replace('\\', '/')
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env
    )
    return proc.communicate()


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
            output = render(
                uid,
                rrequest.umap_path,
                rrequest.useq_path,
                rrequest.uconfig_path
            )
            LOGGER.info("finished rendering job %s", uid)

        time.sleep(10)
