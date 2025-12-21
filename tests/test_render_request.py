"""
Tests for the RenderRequest class and database operations.

These tests verify:
- Object creation and defaults
- Serialization (to_dict/from_dict)
- Database CRUD operations
- Field updates
"""

import pytest
from hypothesis import given, strategies as st

from util.renderRequest import (
    RenderRequest,
    RenderStatus,
    MAX_RETRIES,
    read_all,
    read_db_safe,
    write_db,
    remove_db,
    remove_all,
)


class TestRenderRequestCreation:
    """Test RenderRequest initialization and defaults."""

    def test_creates_uid_if_not_provided(self):
        """New requests should get a unique ID."""
        request = RenderRequest()
        assert request.uid is not None
        assert len(request.uid) == 8

    def test_uid_is_unique(self):
        """Each new request should have a different UID."""
        r1 = RenderRequest()
        r2 = RenderRequest()
        assert r1.uid != r2.uid

    def test_default_status_is_unassigned(self):
        """New requests should start as unassigned."""
        request = RenderRequest()
        assert request.status == RenderStatus.unassigned

    def test_default_dimensions(self):
        """Check default width/height/framerate."""
        request = RenderRequest()
        assert request.width == 1280
        assert request.height == 720
        assert request.frame_rate == 30

    def test_default_format(self):
        """Check default output format."""
        request = RenderRequest()
        assert request.format == 'JPG'

    def test_owner_defaults_to_hostname(self):
        """Owner should default to the machine hostname."""
        import socket
        request = RenderRequest()
        assert request.owner == socket.gethostname()

    def test_tags_default_to_empty_list(self):
        """Tags should default to empty list, not None."""
        request = RenderRequest()
        assert request.tags == []
        assert isinstance(request.tags, list)

    def test_custom_values_override_defaults(self):
        """Explicitly provided values should override defaults."""
        request = RenderRequest(
            uid='custom123',
            name='My Job',
            width=1920,
            height=1080,
            status=RenderStatus.ready_to_start
        )
        assert request.uid == 'custom123'
        assert request.name == 'My Job'
        assert request.width == 1920
        assert request.height == 1080
        assert request.status == RenderStatus.ready_to_start


class TestRenderRequestSerialization:
    """Test to_dict and from_dict methods."""

    def test_to_dict_returns_all_fields(self):
        """to_dict should include all instance attributes."""
        request = RenderRequest(name='Test Job')
        d = request.to_dict()

        expected_fields = [
            'uid', 'name', 'owner', 'worker', 'status',
            'umap_path', 'useq_path', 'uconfig_path',
            'progress', 'time_estimate', 'error_message',
            'retry_count', 'started_at', 'completed_at'
        ]
        for field in expected_fields:
            assert field in d

    def test_from_dict_creates_equivalent_object(self):
        """from_dict should recreate an equivalent object."""
        original = RenderRequest(
            name='Test Job',
            umap_path='/Game/Maps/Test',
            progress=50,
            status=RenderStatus.in_progress
        )

        d = original.to_dict()
        recreated = RenderRequest.from_dict(d)

        assert recreated.uid == original.uid
        assert recreated.name == original.name
        assert recreated.umap_path == original.umap_path
        assert recreated.progress == original.progress
        assert recreated.status == original.status

    def test_from_dict_handles_partial_data(self):
        """from_dict should handle missing fields gracefully."""
        partial_data = {
            'name': 'Partial Job',
            'umap_path': '/Game/Maps/Partial'
        }
        request = RenderRequest.from_dict(partial_data)

        assert request.name == 'Partial Job'
        assert request.umap_path == '/Game/Maps/Partial'
        assert request.status == RenderStatus.unassigned  # default

    def test_from_dict_handles_empty_dict(self):
        """from_dict should work with empty dict (all defaults)."""
        request = RenderRequest.from_dict({})
        assert request.uid is not None
        assert request.status == RenderStatus.unassigned


class TestRenderRequestDatabase:
    """Test database operations."""

    def test_write_and_read(self, isolated_database):
        """Jobs should be readable after writing."""
        request = RenderRequest(name='DB Test')
        request.write_json()

        loaded = RenderRequest.from_db(request.uid)
        assert loaded is not None
        assert loaded.name == 'DB Test'

    def test_read_nonexistent_returns_none(self, isolated_database):
        """Reading a non-existent UID should return None."""
        result = RenderRequest.from_db('nonexistent123')
        assert result is None

    def test_read_all_returns_list(self, isolated_database, create_job):
        """read_all should return a list of RenderRequest objects."""
        create_job(name='Job 1')
        create_job(name='Job 2')
        create_job(name='Job 3')

        all_jobs = read_all()
        assert len(all_jobs) == 3
        assert all(isinstance(j, RenderRequest) for j in all_jobs)

    def test_remove_deletes_job(self, isolated_database, create_job):
        """remove_db should delete the job."""
        job = create_job(name='To Delete')
        uid = job.uid

        remove_db(uid)

        assert RenderRequest.from_db(uid) is None

    def test_remove_nonexistent_is_safe(self, isolated_database):
        """Removing a non-existent job should not raise."""
        remove_db('nonexistent123')  # Should not raise

    def test_remove_all_clears_database(self, isolated_database, create_job):
        """remove_all should clear all jobs."""
        create_job(name='Job 1')
        create_job(name='Job 2')

        remove_all()

        assert read_all() == []

    def test_write_is_idempotent(self, isolated_database):
        """Writing the same job twice should update, not duplicate."""
        request = RenderRequest(name='Idempotent Test')
        request.write_json()
        request.write_json()

        all_jobs = read_all()
        assert len(all_jobs) == 1


class TestRenderRequestUpdate:
    """Test the update method."""

    def test_update_progress(self, isolated_database):
        """update() should modify progress."""
        request = RenderRequest(name='Update Test')
        request.write_json()

        request.update(progress=75)

        loaded = RenderRequest.from_db(request.uid)
        assert loaded.progress == 75

    def test_update_status(self, isolated_database):
        """update() should modify status."""
        request = RenderRequest(name='Status Test')
        request.write_json()

        request.update(status=RenderStatus.in_progress)

        loaded = RenderRequest.from_db(request.uid)
        assert loaded.status == RenderStatus.in_progress

    def test_update_only_changes_provided_fields(self, isolated_database):
        """update() should not change fields that aren't provided."""
        request = RenderRequest(
            name='Selective Update',
            progress=50,
            time_estimate='5 min'
        )
        request.write_json()

        request.update(progress=75)

        loaded = RenderRequest.from_db(request.uid)
        assert loaded.progress == 75
        assert loaded.time_estimate == '5 min'  # unchanged
        assert loaded.name == 'Selective Update'  # unchanged

    def test_update_error_message(self, isolated_database):
        """update() should handle error_message field."""
        request = RenderRequest(name='Error Test')
        request.write_json()

        request.update(
            status=RenderStatus.errored,
            error_message='GPU memory exhausted'
        )

        loaded = RenderRequest.from_db(request.uid)
        assert loaded.status == RenderStatus.errored
        assert loaded.error_message == 'GPU memory exhausted'


class TestRenderRequestAssign:
    """Test worker assignment."""

    def test_assign_sets_worker(self, isolated_database):
        """assign() should set the worker field."""
        request = RenderRequest(name='Assign Test')
        request.write_json()

        request.assign('render-node-01')

        loaded = RenderRequest.from_db(request.uid)
        assert loaded.worker == 'render-node-01'


class TestMaxRetries:
    """Test retry count limits."""

    def test_max_retries_constant_exists(self):
        """MAX_RETRIES should be defined and reasonable."""
        assert MAX_RETRIES >= 1
        assert MAX_RETRIES <= 10  # Sanity check

    def test_retry_count_starts_at_zero(self):
        """New jobs should have zero retries."""
        request = RenderRequest()
        assert request.retry_count == 0
