"""
Tests for Flask API endpoints.

These tests use the Flask test client to simulate HTTP requests
without running a real server.
"""

import pytest

from util.renderRequest import RenderRequest, RenderStatus


class TestHealthEndpoint:
    """Test the /api/health endpoint."""

    def test_health_returns_200(self, client):
        """Health check should return 200."""
        response = client.get('/api/health')
        assert response.status_code == 200

    def test_health_returns_status(self, client):
        """Health check should include status field."""
        response = client.get('/api/health')
        data = response.json
        assert 'status' in data
        assert data['status'] == 'healthy'


class TestJobCRUD:
    """Test basic job CRUD operations."""

    def test_create_job(self, client):
        """POST /api/post should create a job."""
        response = client.post('/api/post', json={
            'name': 'Test Job',
            'umap_path': '/Game/Maps/Test',
            'useq_path': '/Game/Sequences/Test',
        })
        assert response.status_code == 200
        data = response.json
        assert 'uid' in data
        assert data['name'] == 'Test Job'

    def test_get_job(self, client, create_job):
        """GET /api/get/<uid> should return the job."""
        job = create_job(name='Fetch Test')

        response = client.get(f'/api/get/{job.uid}')
        assert response.status_code == 200
        assert response.json['name'] == 'Fetch Test'

    def test_get_nonexistent_job_returns_404(self, client):
        """GET /api/get/<uid> should return 404 for missing jobs."""
        response = client.get('/api/get/nonexistent123')
        assert response.status_code == 404

    def test_get_all_jobs(self, client, create_job):
        """GET /api/get should return all jobs."""
        create_job(name='Job 1')
        create_job(name='Job 2')

        response = client.get('/api/get')
        assert response.status_code == 200
        assert 'results' in response.json
        assert len(response.json['results']) == 2

    def test_delete_job(self, client, create_job):
        """DELETE /api/delete/<uid> should remove the job."""
        job = create_job(name='To Delete')

        response = client.delete(f'/api/delete/{job.uid}')
        assert response.status_code == 200

        # Verify it's gone
        get_response = client.get(f'/api/get/{job.uid}')
        assert get_response.status_code == 404

    def test_update_job(self, client, create_job):
        """PUT /api/put/<uid> should update the job."""
        job = create_job(name='Update Test', status=RenderStatus.ready_to_start)

        response = client.put(f'/api/put/{job.uid}', json={
            'progress': 50,
            'status': RenderStatus.in_progress
        })
        assert response.status_code == 200
        assert response.json['progress'] == 50
        assert response.json['status'] == RenderStatus.in_progress

    def test_update_nonexistent_job_returns_404(self, client):
        """PUT /api/put/<uid> should return 404 for missing jobs."""
        response = client.put('/api/put/nonexistent123', json={
            'progress': 50
        })
        assert response.status_code == 404


class TestWorkerEndpoints:
    """Test worker-related endpoints."""

    def test_heartbeat(self, client):
        """POST /api/worker/heartbeat should register worker."""
        response = client.post('/api/worker/heartbeat', json={
            'worker_name': 'test-node-01',
            'status': 'idle'
        })
        assert response.status_code == 200
        assert response.json['ok'] is True

    def test_heartbeat_requires_worker_name(self, client):
        """Heartbeat without worker_name should fail."""
        response = client.post('/api/worker/heartbeat', json={
            'status': 'idle'
        })
        assert response.status_code == 400

    def test_get_workers(self, client, register_worker):
        """GET /api/workers should return registered workers."""
        register_worker('node-01', status='idle')
        register_worker('node-02', status='rendering')

        response = client.get('/api/workers')
        assert response.status_code == 200
        assert 'workers' in response.json
        assert len(response.json['workers']) == 2

    def test_get_my_jobs(self, client, create_job):
        """GET /api/jobs/mine/<worker> should return assigned jobs."""
        create_job(name='Job 1', worker='node-01', status=RenderStatus.ready_to_start)
        create_job(name='Job 2', worker='node-01', status=RenderStatus.in_progress)
        create_job(name='Job 3', worker='node-02', status=RenderStatus.ready_to_start)

        response = client.get('/api/jobs/mine/node-01')
        assert response.status_code == 200
        assert len(response.json['jobs']) == 2


class TestCancelAndRetry:
    """Test cancel and retry endpoints."""

    def test_cancel_job(self, client, create_job):
        """POST /api/cancel/<uid> should cancel the job."""
        job = create_job(status=RenderStatus.in_progress)

        response = client.post(f'/api/cancel/{job.uid}')
        assert response.status_code == 200
        assert response.json['status'] == RenderStatus.cancelled

    def test_cancel_nonexistent_returns_404(self, client):
        """Cancelling non-existent job should return 404."""
        response = client.post('/api/cancel/nonexistent123')
        assert response.status_code == 404

    def test_retry_errored_job(self, client, create_job):
        """POST /api/retry/<uid> should retry errored jobs."""
        job = create_job(status=RenderStatus.errored)

        response = client.post(f'/api/retry/{job.uid}')
        assert response.status_code == 200
        assert response.json['status'] == RenderStatus.ready_to_start
        assert response.json['retry_count'] == 1

    def test_retry_cancelled_job(self, client, create_job):
        """POST /api/retry/<uid> should work for cancelled jobs."""
        job = create_job(status=RenderStatus.cancelled)

        response = client.post(f'/api/retry/{job.uid}')
        assert response.status_code == 200
        assert response.json['status'] == RenderStatus.ready_to_start

    def test_retry_in_progress_fails(self, client, create_job):
        """Cannot retry a job that's still in progress."""
        job = create_job(status=RenderStatus.in_progress)

        response = client.post(f'/api/retry/{job.uid}')
        assert response.status_code == 400

    def test_max_retries_exceeded(self, client, create_job):
        """Retrying beyond MAX_RETRIES should fail."""
        from util.renderRequest import MAX_RETRIES

        # Create job that's already been retried MAX_RETRIES times
        job = create_job(status=RenderStatus.errored)
        job.retry_count = MAX_RETRIES
        job.write_json()

        response = client.post(f'/api/retry/{job.uid}')
        assert response.status_code == 400
        assert 'max retries' in response.json.get('error', '').lower()


class TestDashboard:
    """Test dashboard endpoint."""

    def test_dashboard_returns_aggregates(self, client, create_job, register_worker):
        """Dashboard should return aggregated stats."""
        create_job(status=RenderStatus.in_progress)
        create_job(status=RenderStatus.finished)
        create_job(status=RenderStatus.finished)
        register_worker('node-01', status='idle')

        response = client.get('/api/dashboard')
        assert response.status_code == 200

        data = response.json
        assert 'workers' in data
        assert 'jobs' in data
        assert data['jobs']['total'] == 3
        assert data['jobs']['by_status'].get(RenderStatus.finished) == 2


class TestErrorLogging:
    """Test error logging endpoints."""

    def test_log_worker_error(self, client):
        """POST /api/worker/error should log errors."""
        response = client.post('/api/worker/error', json={
            'worker': 'node-01',
            'message': 'GPU memory exhausted',
            'job_uid': 'abc123'
        })
        assert response.status_code == 200

    def test_get_errors(self, client):
        """GET /api/errors should return recent errors."""
        # Log some errors first
        client.post('/api/worker/error', json={
            'worker': 'node-01',
            'message': 'Error 1'
        })
        client.post('/api/worker/error', json={
            'worker': 'node-02',
            'message': 'Error 2'
        })

        response = client.get('/api/errors')
        assert response.status_code == 200
        assert 'errors' in response.json
        assert len(response.json['errors']) == 2
