"""
Tests for stuck job detection and recovery.

The watchdog checks for jobs that are stuck in 'in progress' state
and resets them for reassignment.
"""

import pytest
from datetime import datetime, timedelta

from util.renderRequest import RenderStatus, RenderRequest, upsert_worker
from requestManager import check_stuck_jobs, WORKER_TIMEOUT


class TestStuckJobDetection:
    """Test the check_stuck_jobs function."""

    def test_job_with_offline_worker_is_reset(
        self, isolated_database, create_job, register_worker
    ):
        """Jobs assigned to offline workers should be reset."""
        # Register a worker that was last seen long ago
        old_time = (datetime.now() - timedelta(seconds=WORKER_TIMEOUT + 10)).isoformat()
        upsert_worker({
            'name': 'offline-node',
            'status': 'rendering',
            'last_seen': old_time
        })

        # Create an in-progress job assigned to that worker
        job = create_job(
            status=RenderStatus.in_progress,
            worker='offline-node'
        )

        check_stuck_jobs()

        # Job should be reset
        reloaded = RenderRequest.from_db(job.uid)
        assert reloaded.status == RenderStatus.ready_to_start
        assert reloaded.worker == ''

    def test_job_with_online_worker_is_not_reset(
        self, isolated_database, create_job, register_worker
    ):
        """Jobs with online workers should not be touched."""
        register_worker('online-node', status='rendering')

        job = create_job(
            status=RenderStatus.in_progress,
            worker='online-node'
        )

        check_stuck_jobs()

        reloaded = RenderRequest.from_db(job.uid)
        assert reloaded.status == RenderStatus.in_progress
        assert reloaded.worker == 'online-node'

    def test_job_with_unregistered_worker_is_reset(
        self, isolated_database, create_job
    ):
        """Jobs assigned to workers that never registered should be reset."""
        job = create_job(
            status=RenderStatus.in_progress,
            worker='ghost-node'  # Never registered
        )

        check_stuck_jobs()

        reloaded = RenderRequest.from_db(job.uid)
        assert reloaded.status == RenderStatus.ready_to_start
        assert reloaded.worker == ''

    def test_job_with_no_worker_is_reset(self, isolated_database, create_job):
        """In-progress jobs with no worker assigned should be reset."""
        job = create_job(status=RenderStatus.in_progress, worker='')

        check_stuck_jobs()

        reloaded = RenderRequest.from_db(job.uid)
        assert reloaded.status == RenderStatus.ready_to_start

    def test_non_in_progress_jobs_are_ignored(
        self, isolated_database, create_job
    ):
        """Jobs not in 'in progress' state should be ignored."""
        job_ready = create_job(status=RenderStatus.ready_to_start, worker='')
        job_finished = create_job(status=RenderStatus.finished, worker='node-01')
        job_errored = create_job(status=RenderStatus.errored, worker='node-02')

        check_stuck_jobs()

        # All should be unchanged
        assert RenderRequest.from_db(job_ready.uid).status == RenderStatus.ready_to_start
        assert RenderRequest.from_db(job_finished.uid).status == RenderStatus.finished
        assert RenderRequest.from_db(job_errored.uid).status == RenderStatus.errored

    def test_long_running_job_with_online_worker_is_not_reset(
        self, isolated_database, create_job, register_worker
    ):
        """Long-running jobs should not be reset as long as worker is online."""
        register_worker('online-node', status='rendering')

        # Create job that started hours ago - should still not be reset
        old_start = (datetime.now() - timedelta(hours=5)).isoformat()
        job = create_job(
            status=RenderStatus.in_progress,
            worker='online-node',
            started_at=old_start
        )

        check_stuck_jobs()

        reloaded = RenderRequest.from_db(job.uid)
        assert reloaded.status == RenderStatus.in_progress

    def test_reset_job_gets_error_message(
        self, isolated_database, create_job
    ):
        """Reset jobs should have an error message explaining why."""
        job = create_job(
            status=RenderStatus.in_progress,
            worker='ghost-node'
        )

        check_stuck_jobs()

        reloaded = RenderRequest.from_db(job.uid)
        assert reloaded.error_message != ''
        assert 'Reset' in reloaded.error_message


class TestWorkerStatusCalculation:
    """Test worker online/offline determination."""

    def test_worker_online_within_timeout(self, isolated_database, client, register_worker):
        """Workers with recent heartbeat should be online."""
        register_worker('fresh-node', status='idle')

        response = client.get('/api/workers')
        workers = response.json['workers']

        node = next(w for w in workers if w['name'] == 'fresh-node')
        assert node['online'] is True

    def test_worker_offline_after_timeout(self, isolated_database, client):
        """Workers with old heartbeat should be offline."""
        old_time = (datetime.now() - timedelta(seconds=WORKER_TIMEOUT + 10)).isoformat()
        upsert_worker({
            'name': 'stale-node',
            'status': 'idle',
            'last_seen': old_time
        })

        response = client.get('/api/workers')
        workers = response.json['workers']

        node = next(w for w in workers if w['name'] == 'stale-node')
        assert node['online'] is False


class TestWorkerAssignment:
    """Test job assignment to workers."""

    def test_job_assigned_to_available_worker(
        self, isolated_database, client, register_worker
    ):
        """New jobs should be assigned to available workers."""
        register_worker('idle-node', status='idle')

        response = client.post('/api/post', json={
            'name': 'New Job',
            'umap_path': '/Game/Maps/Test'
        })

        assert response.status_code == 200
        # Job should be assigned and ready to start
        assert response.json['worker'] == 'idle-node'
        assert response.json['status'] == RenderStatus.ready_to_start

    def test_job_unassigned_when_no_workers(self, isolated_database, client):
        """Jobs should stay unassigned when no workers available."""
        # No workers registered

        response = client.post('/api/post', json={
            'name': 'Lonely Job',
            'umap_path': '/Game/Maps/Test'
        })

        assert response.status_code == 200
        assert response.json['worker'] == ''
        assert response.json['status'] == RenderStatus.unassigned

    def test_busy_worker_not_assigned(
        self, isolated_database, client, register_worker
    ):
        """Workers that are busy should not get new jobs."""
        register_worker('busy-node', status='rendering')

        response = client.post('/api/post', json={
            'name': 'Waiting Job',
            'umap_path': '/Game/Maps/Test'
        })

        assert response.status_code == 200
        assert response.json['worker'] == ''  # Not assigned to busy worker
