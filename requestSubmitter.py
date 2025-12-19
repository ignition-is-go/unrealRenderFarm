"""
Submit render jobs from a project config file
Usage: uv run python requestSubmitter.py projects/hrlv.json
"""

from dotenv import load_dotenv
load_dotenv()

import json
import logging
import sys

from util import client


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


def send(d):
    """
    Send/Submit a new render request
    """
    rrequest = client.add_request(d)
    if rrequest:
        LOGGER.info('submitted job %s: %s', rrequest.uid, d['name'])


def submit_project(config_path):
    """
    Submit all sequences from a project config file
    """
    with open(config_path) as f:
        project = json.load(f)

    LOGGER.info('Submitting project: %s', project['name'])

    for seq in project['sequences']:
        # seq is the full path, extract name from it (last part before any dot)
        seq_name = seq.rstrip('/').split('/')[-1].split('.')[0]
        send({
            'name': seq_name,
            'umap_path': project['map'],
            'useq_path': seq,
            'uconfig_path': project['config'],
        })

    LOGGER.info('Submitted %d jobs', len(project['sequences']))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: uv run python requestSubmitter.py projects/hrlv.json")
        sys.exit(1)

    submit_project(sys.argv[1])
