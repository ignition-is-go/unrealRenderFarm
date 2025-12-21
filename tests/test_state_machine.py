"""
Tests for the job state machine.

The state machine defines which status transitions are valid.
This is critical for job reliability - invalid transitions could
leave jobs in broken states.
"""

import pytest
from hypothesis import given, strategies as st, settings

from util.renderRequest import RenderStatus
from requestManager import VALID_TRANSITIONS, is_valid_transition


# All possible statuses
ALL_STATUSES = [
    RenderStatus.unassigned,
    RenderStatus.ready_to_start,
    RenderStatus.in_progress,
    RenderStatus.finished,
    RenderStatus.errored,
    RenderStatus.failed,
    RenderStatus.cancelled,
    RenderStatus.paused,
]


class TestValidTransitions:
    """Test the VALID_TRANSITIONS configuration."""

    def test_all_statuses_have_transition_rules(self):
        """Every status should be a key in VALID_TRANSITIONS."""
        for status in ALL_STATUSES:
            assert status in VALID_TRANSITIONS, f"Missing transition rules for {status}"

    def test_terminal_states_have_no_transitions(self):
        """Terminal states (finished, failed) should not allow any transitions."""
        terminal_states = [RenderStatus.finished, RenderStatus.failed]
        for status in terminal_states:
            allowed = VALID_TRANSITIONS.get(status, [])
            assert allowed == [], f"{status} should be terminal but allows: {allowed}"

    def test_errored_can_retry_or_fail(self):
        """Errored jobs should be able to retry or mark as failed."""
        allowed = VALID_TRANSITIONS[RenderStatus.errored]
        assert RenderStatus.ready_to_start in allowed, "Errored jobs should allow retry"
        assert RenderStatus.failed in allowed, "Errored jobs should allow marking as failed"

    def test_in_progress_can_finish_or_error(self):
        """In-progress jobs should be able to finish, error, or cancel."""
        allowed = VALID_TRANSITIONS[RenderStatus.in_progress]
        assert RenderStatus.finished in allowed
        assert RenderStatus.errored in allowed
        assert RenderStatus.cancelled in allowed

    def test_cancelled_can_restart(self):
        """Cancelled jobs should be able to restart."""
        allowed = VALID_TRANSITIONS[RenderStatus.cancelled]
        assert RenderStatus.ready_to_start in allowed


class TestIsValidTransition:
    """Test the is_valid_transition function."""

    def test_same_status_always_valid(self):
        """Transitioning to the same status is always valid (no-op)."""
        for status in ALL_STATUSES:
            assert is_valid_transition(status, status) is True

    def test_valid_forward_transitions(self):
        """Test known valid transitions work."""
        valid_cases = [
            (RenderStatus.unassigned, RenderStatus.ready_to_start),
            (RenderStatus.ready_to_start, RenderStatus.in_progress),
            (RenderStatus.in_progress, RenderStatus.finished),
            (RenderStatus.in_progress, RenderStatus.errored),
            (RenderStatus.errored, RenderStatus.ready_to_start),  # retry
        ]
        for current, new in valid_cases:
            assert is_valid_transition(current, new) is True, \
                f"Expected {current} -> {new} to be valid"

    def test_invalid_backward_transitions(self):
        """Test that going backwards from terminal states is blocked."""
        invalid_cases = [
            (RenderStatus.finished, RenderStatus.in_progress),
            (RenderStatus.finished, RenderStatus.ready_to_start),
            (RenderStatus.failed, RenderStatus.errored),
            (RenderStatus.failed, RenderStatus.ready_to_start),
        ]
        for current, new in invalid_cases:
            assert is_valid_transition(current, new) is False, \
                f"Expected {current} -> {new} to be invalid"

    def test_cannot_skip_states(self):
        """Test that you can't skip intermediate states."""
        # Can't go directly from unassigned to in_progress
        assert is_valid_transition(RenderStatus.unassigned, RenderStatus.in_progress) is False
        # Can't go directly from unassigned to finished
        assert is_valid_transition(RenderStatus.unassigned, RenderStatus.finished) is False

    def test_unknown_status_returns_false(self):
        """Unknown statuses should not allow any transitions."""
        assert is_valid_transition('bogus_status', RenderStatus.ready_to_start) is False
        assert is_valid_transition(RenderStatus.unassigned, 'bogus_status') is False


class TestStateTransitionsViaAPI:
    """Test state transitions through the API endpoints."""

    def test_valid_transition_via_put(self, client, create_job):
        """Valid transitions should succeed via PUT."""
        job = create_job(status=RenderStatus.unassigned)

        response = client.put(
            f'/api/put/{job.uid}',
            json={'status': RenderStatus.ready_to_start}
        )
        assert response.status_code == 200
        assert response.json['status'] == RenderStatus.ready_to_start

    def test_invalid_transition_via_put_returns_400(self, client, create_job):
        """Invalid transitions should return 400 via PUT."""
        job = create_job(status=RenderStatus.finished)

        response = client.put(
            f'/api/put/{job.uid}',
            json={'status': RenderStatus.in_progress}
        )
        assert response.status_code == 400
        assert 'invalid state transition' in response.json.get('error', '')

    def test_error_response_includes_allowed_transitions(self, client, create_job):
        """Error responses should list what transitions are allowed."""
        job = create_job(status=RenderStatus.unassigned)

        response = client.put(
            f'/api/put/{job.uid}',
            json={'status': RenderStatus.finished}  # Invalid jump
        )
        assert response.status_code == 400
        assert 'allowed_transitions' in response.json


class TestHypothesisTransitions:
    """Property-based tests using Hypothesis."""

    @given(st.sampled_from(ALL_STATUSES), st.sampled_from(ALL_STATUSES))
    @settings(max_examples=200)
    def test_transitions_are_deterministic(self, status_a, status_b):
        """
        Transition validity should be deterministic.

        For any pair of statuses, is_valid_transition should always
        return the same result.
        """
        result1 = is_valid_transition(status_a, status_b)
        result2 = is_valid_transition(status_a, status_b)
        assert result1 == result2

    @given(st.sampled_from(ALL_STATUSES))
    def test_terminal_states_are_truly_terminal(self, status):
        """
        From terminal states, no transitions should be valid (except to self).
        """
        terminal = [RenderStatus.finished, RenderStatus.failed]
        if status in terminal:
            for target in ALL_STATUSES:
                if target != status:
                    assert is_valid_transition(status, target) is False

    @given(st.sampled_from(ALL_STATUSES), st.sampled_from(ALL_STATUSES))
    @settings(max_examples=100)
    def test_valid_transition_is_in_allowed_list(self, current, target):
        """
        If is_valid_transition returns True, the target should be in
        the VALID_TRANSITIONS list (or be the same status).
        """
        if is_valid_transition(current, target):
            if current == target:
                pass  # Same status is always valid
            else:
                allowed = VALID_TRANSITIONS.get(current, [])
                assert target in allowed
