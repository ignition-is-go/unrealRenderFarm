"""
Pytest fixtures for render farm tests.

Key fixtures:
- temp_db: Isolated TinyDB database for each test
- client: Flask test client with isolated database
- sample_job_data: Example job data for creating test requests
"""

import os
import sys
import tempfile
import pytest
from datetime import datetime

# Add project root to path so we can import util, requestManager, etc.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Set up temp database BEFORE importing modules that use TinyDB
_temp_db_file = None


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path):
    """
    Provide an isolated TinyDB database for each test.

    This fixture runs automatically for every test and ensures
    tests don't interfere with each other or the real database.
    """
    # Create temp database file
    db_file = tmp_path / "test_jobs.json"
    db_dir = tmp_path

    # Patch the database paths before they're used
    monkeypatch.setattr('util.renderRequest.DATABASE_DIR', str(db_dir))
    monkeypatch.setattr('util.renderRequest.DATABASE_FILE', str(db_file))

    # Re-initialize TinyDB with the temp file
    from tinydb import TinyDB
    temp_db = TinyDB(str(db_file))

    import util.renderRequest as rr_module
    monkeypatch.setattr(rr_module, '_db', temp_db)
    monkeypatch.setattr(rr_module, '_jobs', temp_db.table('jobs'))
    monkeypatch.setattr(rr_module, '_workers', temp_db.table('workers'))
    monkeypatch.setattr(rr_module, '_errors', temp_db.table('errors'))

    yield temp_db

    # Cleanup
    temp_db.close()


@pytest.fixture
def client(isolated_database):
    """
    Flask test client with isolated database.

    Usage:
        def test_api(client):
            response = client.get('/api/health')
            assert response.status_code == 200
    """
    from requestManager import app

    app.config['TESTING'] = True
    # Disable rate limiting in tests
    app.config['RATELIMIT_ENABLED'] = False

    with app.test_client() as test_client:
        yield test_client


@pytest.fixture
def sample_job_data():
    """
    Sample job data for creating test RenderRequests.

    Returns a factory function to create unique job data.
    """
    counter = [0]

    def _make_job_data(**overrides):
        counter[0] += 1
        data = {
            'name': f'test_job_{counter[0]}',
            'umap_path': '/Game/Maps/TestMap',
            'useq_path': '/Game/Sequences/TestSeq',
            'uconfig_path': '/Game/Presets/TestConfig',
        }
        data.update(overrides)
        return data

    return _make_job_data


@pytest.fixture
def create_job(sample_job_data):
    """
    Factory fixture to create and persist a RenderRequest.

    Usage:
        def test_something(create_job):
            job = create_job(name='my_job', status='ready to start')
            assert job.uid is not None
    """
    from util.renderRequest import RenderRequest

    def _create_job(**overrides):
        data = sample_job_data(**overrides)
        job = RenderRequest.from_dict(data)
        job.write_json()
        return job

    return _create_job


@pytest.fixture
def register_worker():
    """
    Factory fixture to register a worker in the database.

    Usage:
        def test_worker(register_worker):
            register_worker('node-01', status='idle')
    """
    from util.renderRequest import upsert_worker

    def _register_worker(name, status='idle', **kwargs):
        worker_data = {
            'name': name,
            'status': status,
            'last_seen': datetime.now().isoformat(),
            **kwargs
        }
        upsert_worker(worker_data)
        return worker_data

    return _register_worker


@pytest.fixture
def mock_time(mocker):
    """
    Mock datetime.now() for testing time-dependent logic.

    Usage:
        def test_timeout(mock_time):
            mock_time(datetime(2025, 1, 1, 12, 0, 0))
            # ... test code that uses datetime.now()
    """
    from datetime import datetime as real_datetime

    mock_now = [real_datetime.now()]

    class MockDatetime:
        @classmethod
        def now(cls):
            return mock_now[0]

        @classmethod
        def fromisoformat(cls, s):
            return real_datetime.fromisoformat(s)

        def strftime(self, fmt):
            return mock_now[0].strftime(fmt)

    def set_time(dt):
        mock_now[0] = dt
        mocker.patch('requestManager.datetime', MockDatetime)
        mocker.patch('util.renderRequest.datetime', MockDatetime)

    return set_time
