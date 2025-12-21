"""
Unreal render job request class for data representation and database operation
"""

import logging
import os
import socket
import uuid
from datetime import datetime

from tinydb import TinyDB, Query


LOGGER = logging.getLogger(__name__)

MODULE_PATH = os.path.dirname(os.path.abspath(__file__))
ROOT_PATH = os.path.dirname(MODULE_PATH)
DATABASE_DIR = os.path.join(ROOT_PATH, 'database')
DATABASE_FILE = os.path.join(DATABASE_DIR, 'jobs.json')

# Ensure database directory exists
os.makedirs(DATABASE_DIR, exist_ok=True)

# Initialize TinyDB (thread-safe by default)
_db = TinyDB(DATABASE_FILE)
_jobs = _db.table('jobs')
_workers = _db.table('workers')
_errors = _db.table('errors')


class RenderStatus(object):
    """
    Enum class to represent render job status
    """

    unassigned = 'un-assigned'
    ready_to_start = 'ready to start'
    in_progress = 'in progress'
    finished = 'finished'
    errored = 'errored'
    failed = 'failed'  # Terminal state after max retries
    cancelled = 'cancelled'
    paused = 'paused'

# Maximum retry attempts before marking as failed
MAX_RETRIES = 3


class RenderRequest(object):
    """
    An object representing request for an Unreal render job sent from a
    machine to the request manager (renderManager.py)
    """

    def __init__(
            self,
            uid='',
            name='',
            owner='',
            worker='',
            time_created='',
            priority=0,
            category='',
            tags=None,
            status='',
            umap_path='',
            useq_path='',
            uconfig_path='',
            output_path='',
            width=0,
            height=0,
            frame_rate=0,
            format='',
            start_frame=0,
            end_frame=0,
            time_estimate='',
            progress=0,
            warmup_current=0,
            warmup_total=0,
            error_message='',
            retry_count=0,
            started_at='',
            completed_at=''
    ):
        """
        Initialization

        :param uid: str. unique identifier, server as primary key for database
        :param name: str. job name
        :param owner: str. the name of the submitter
        :param worker: str. the name of the worker to render the job
        :param time_created: str. datetime in .strftime("%m/%d/%Y, %H:%M:%S") format
        :param priority: int. job priority [0 lowest to 100 highest]
        :param category: str.
        :param tags: [str].
        :param status: RenderStatus. job render status
        :param umap_path: str. Unreal path to the map/level asset
        :param useq_path: str. Unreal path to the sequence asset
        :param uconfig_path: str. Unreal path to the preset/config asset
        :param output_path: str. system path to the output directory
        :param width: int. output width
        :param height: int. output height
        :param frame_rate: int. output frame rate
        :param format: int. output format
        :param start_frame: int. custom render start frame
        :param end_frame: int. custom render end frame
        :param time_estimate: str. render time remaining estimate
        :param progress: int. render progress [0 to 100]
        :param warmup_current: int. current engine warmup frame
        :param warmup_total: int. total engine warmup frames
        :param error_message: str. error description if job failed
        :param retry_count: int. number of times job has been retried
        :param started_at: str. ISO timestamp when render started
        :param completed_at: str. ISO timestamp when render finished/failed
        """
        self.uid = uid or str(uuid.uuid4())[:8]
        self.name = name
        self.owner = owner or socket.gethostname()
        self.worker = worker
        self.time_created = time_created or datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
        self.priority = priority or 0
        self.category = category
        self.tags = tags if tags is not None else []
        self.status = status or RenderStatus.unassigned
        self.umap_path = umap_path
        self.useq_path = useq_path
        self.uconfig_path = uconfig_path
        self.output_path = output_path
        self.width = width or 1280
        self.height = height or 720
        self.frame_rate = frame_rate or 30
        self.format = format or 'JPG'
        self.start_frame = start_frame or 0
        self.end_frame = end_frame or 0
        self.length = self.end_frame - self.start_frame
        self.time_estimate = time_estimate
        self.progress = progress
        self.warmup_current = warmup_current
        self.warmup_total = warmup_total
        self.error_message = error_message
        self.retry_count = retry_count
        self.started_at = started_at
        self.completed_at = completed_at

    @classmethod
    def from_db(cls, uid):
        """
        re-create a request object from database using uid

        This is a fake database using json

        :param uid: str. unique id from database
        :return: RenderRequest. request object
        """
        request_dict = read_db_safe(uid)
        if request_dict is None:
            return None
        return cls.from_dict(request_dict)

    @classmethod
    def from_dict(cls, d):
        """
        Create a new request object from partial dictionary/json or.
        re-create a request object from function 'to_dict'

        :param d: dict. input dictionary
        :return: RenderRequest. request object
        """
        # has to assign a default value of '' or 0 for initialization
        # value to kick-in
        uid = d.get('uid') or ''
        name = d.get('name') or ''
        owner = d.get('owner') or ''
        worker = d.get('worker') or ''
        time_created = d.get('time_created') or ''
        priority = d.get('priority') or 0
        category = d.get('category') or ''
        tags = d.get('tags') or []
        status = d.get('status') or ''
        umap_path = d.get('umap_path') or ''
        useq_path = d.get('useq_path') or ''
        uconfig_path = d.get('uconfig_path') or ''
        output_path = d.get('output_path') or ''
        width = d.get('width') or 0
        height = d.get('height') or 0
        frame_rate = d.get('frame_rate') or 0
        format = d.get('format') or ''
        start_frame = d.get('start_frame') or 0
        end_frame = d.get('end_frame') or 0
        time_estimate = d.get('time_estimate') or ''
        progress = d.get('progress') or 0
        warmup_current = d.get('warmup_current') or 0
        warmup_total = d.get('warmup_total') or 0
        error_message = d.get('error_message') or ''
        retry_count = d.get('retry_count') or 0
        started_at = d.get('started_at') or ''
        completed_at = d.get('completed_at') or ''

        return cls(
            uid=uid,
            name=name,
            owner=owner,
            worker=worker,
            time_created=time_created,
            priority=priority,
            category=category,
            tags=tags,
            status=status,
            umap_path=umap_path,
            useq_path=useq_path,
            uconfig_path=uconfig_path,
            output_path=output_path,
            width=width,
            height=height,
            frame_rate=frame_rate,
            format=format,
            start_frame=start_frame,
            end_frame=end_frame,
            time_estimate=time_estimate,
            progress=progress,
            warmup_current=warmup_current,
            warmup_total=warmup_total,
            error_message=error_message,
            retry_count=retry_count,
            started_at=started_at,
            completed_at=completed_at
        )

    def to_dict(self):
        """
        Convert current request to a dictionary
        """
        return self.__dict__

    def write_json(self):
        """
        Write current request to the fake database (as a .json)
        """
        write_db(self.__dict__)

    def remove(self):
        """
        Remove current request from the fake database
        """
        remove_db(self.uid)

    def update(self, progress=None, status=None, time_estimate=None, warmup_current=None,
               warmup_total=None, error_message=None, started_at=None, completed_at=None):
        """
        Update current request progress in the database

        used by the render worker (renderWorker.py)

        :param progress: int. new progress
        :param status: RenderRequest. new render status
        :param time_estimate: str. new time remaining estimate
        :param warmup_current: int. current engine warmup frame
        :param warmup_total: int. total engine warmup frames
        :param error_message: str. error description if failed
        :param started_at: str. ISO timestamp when render started
        :param completed_at: str. ISO timestamp when render finished/failed
        """
        if progress is not None:
            self.progress = progress
        if status is not None:
            self.status = status
        if time_estimate is not None:
            self.time_estimate = time_estimate
        if warmup_current is not None:
            self.warmup_current = warmup_current
        if warmup_total is not None:
            self.warmup_total = warmup_total
        if error_message is not None:
            self.error_message = error_message
        if started_at is not None:
            self.started_at = started_at
        if completed_at is not None:
            self.completed_at = completed_at

        write_db(self.__dict__)

    def assign(self, worker):
        """
        Update current request assignment in the fake database

        used by the render manager (renderManager.py)

        :param worker: str. new worker assigned
        """
        self.worker = worker

        write_db(self.__dict__)


# region database utility (TinyDB)

Job = Query()


def read_all():
    """
    Read and convert everything in the database to RenderRequest objects

    :return: [RenderRequest]. request objects present in the database
    """
    all_jobs = _jobs.all()
    return [RenderRequest.from_dict(job) for job in all_jobs]


def read_db_safe(uid):
    """
    Read a database entry by UID.

    :param uid: str. request uid
    :return: dict or None. RenderRequest data as dictionary
    """
    result = _jobs.get(Job.uid == uid)
    return result


def write_db(d):
    """
    Write/overwrite a database entry (upsert).

    :param d: dict. RenderRequest object presented as a dictionary
    """
    uid = d['uid']
    LOGGER.info('writing to %s', uid)
    _jobs.upsert(d, Job.uid == uid)


def remove_db(uid):
    """
    Remove a RenderRequest object from the database

    :param uid: str. request uid
    """
    _jobs.remove(Job.uid == uid)


def remove_all():
    """
    Clear all jobs from database
    """
    _jobs.truncate()

# endregion


# region worker utilities

Worker = Query()


def get_worker(name):
    """Get worker info by name"""
    return _workers.get(Worker.name == name)


def get_all_workers():
    """Get all registered workers"""
    return _workers.all()


def upsert_worker(data):
    """Insert or update worker data"""
    _workers.upsert(data, Worker.name == data['name'])


def remove_worker(name):
    """Remove a worker from the database"""
    _workers.remove(Worker.name == name)

# endregion


# region error utilities

def log_error(worker_name, job_uid, message):
    """
    Log an error to the database.

    :param worker_name: str. name of the worker that encountered the error
    :param job_uid: str. UID of the job (optional, can be None)
    :param message: str. error message
    """
    _errors.insert({
        'timestamp': datetime.now().isoformat(),
        'worker': worker_name,
        'job_uid': job_uid,
        'message': message
    })


def get_recent_errors(limit=20):
    """
    Get recent errors, most recent first.

    :param limit: int. maximum number of errors to return
    :return: list of error dicts
    """
    all_errors = _errors.all()
    # Sort by timestamp descending and limit
    sorted_errors = sorted(all_errors, key=lambda e: e.get('timestamp', ''), reverse=True)
    return sorted_errors[:limit]


def clear_errors():
    """Clear all errors from the database"""
    _errors.truncate()

# endregion
