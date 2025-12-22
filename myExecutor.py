"""
MRQ executor for command-line rendering with server callbacks.

Hardened for production with:
- Error handling around all Unreal API calls
- HTTP failure tolerance (continues render if server unreachable)
- Error message capture and reporting
"""

import json
import os
import time
import unreal


# Server URL from environment variable
SERVER_API_URL = os.environ.get('RENDER_SERVER_URL', 'http://127.0.0.1:5000') + '/api'

# Throttle progress updates (seconds between updates)
PROGRESS_UPDATE_INTERVAL = 5.0

# Maximum HTTP failures before giving up on updates
MAX_HTTP_FAILURES = 5

unreal.log("=== MyExecutor module loaded ===")
unreal.log("  SERVER_API_URL: {}".format(SERVER_API_URL))


def fix_asset_path(path):
    """
    Fix Unreal asset paths to include both package and asset name.

    WRONG:  /Game/Path/Asset         (loads Package, not the asset!)
    RIGHT:  /Game/Path/Asset.Asset   (loads the actual asset)
    """
    if path and '.' not in path.split('/')[-1]:
        asset_name = path.split('/')[-1]
        return "{}.{}".format(path, asset_name)
    return path


@unreal.uclass()
class MyExecutor(unreal.MoviePipelinePythonHostExecutor):

    pipeline = unreal.uproperty(unreal.MoviePipeline)
    queue = unreal.uproperty(unreal.MoviePipelineQueue)
    job_id = unreal.uproperty(unreal.Text)

    def _post_init(self):
        self.pipeline = None
        self.queue = None
        self.job_id = None
        self._last_update_time = 0.0
        self._last_progress = -1.0
        self._http_failures = 0
        self._error_message = None

        # Register HTTP response callback
        self.http_response_recieved_delegate.add_function_unique(
            self,
            "on_http_response"
        )

    @unreal.ufunction(override=True)
    def execute_delayed(self, queue):
        """Initialize and start the render pipeline."""
        try:
            # Parse commandline
            (cmd_tokens, cmd_switches, cmd_parameters) = unreal.SystemLibrary.\
                parse_command_line(unreal.SystemLibrary.get_command_line())

            if not cmd_tokens:
                self._fail("No map specified in command line")
                return

            map_path = fix_asset_path(cmd_tokens[0])
            seq_path = fix_asset_path(cmd_parameters.get('LevelSequence', ''))
            preset_path = fix_asset_path(cmd_parameters.get('MoviePipelineConfig', ''))
            self.job_id = cmd_parameters.get('JobId', '')

            unreal.log("=== MyExecutor: Job {} ===".format(self.job_id))
            unreal.log("  Map: {}".format(map_path))
            unreal.log("  Sequence: {}".format(seq_path))
            unreal.log("  Config: {}".format(preset_path))

            if not seq_path:
                self._fail("No LevelSequence specified")
                return

            if not preset_path:
                self._fail("No MoviePipelineConfig specified")
                return

            # Load preset
            try:
                preset_soft = unreal.SoftObjectPath(preset_path)
                u_preset = unreal.SystemLibrary.conv_soft_obj_path_to_soft_obj_ref(preset_soft)
            except Exception as e:
                self._fail("Failed to load preset path: {}".format(e))
                return

            if not u_preset:
                self._fail("Preset not found: {}".format(preset_path))
                return

            # Initialize pipeline
            try:
                self.pipeline = unreal.new_object(
                    self.target_pipeline_class,
                    outer=self.get_last_loaded_world(),
                    base_type=unreal.MoviePipeline
                )
            except Exception as e:
                self._fail("Failed to create pipeline: {}".format(e))
                return

            # Initialize queue with single job
            try:
                self.queue = unreal.new_object(unreal.MoviePipelineQueue, outer=self)
                job = self.queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
                job.map = unreal.SoftObjectPath(map_path)
                job.sequence = unreal.SoftObjectPath(seq_path)
                job.set_configuration(u_preset)
            except Exception as e:
                self._fail("Failed to configure job: {}".format(e))
                return

            # Register finished callback
            self.pipeline.on_movie_pipeline_work_finished_delegate.add_function_unique(
                self, "on_movie_pipeline_finished"
            )

            try:
                self.pipeline.initialize(job)
            except Exception as e:
                self._fail("Failed to initialize pipeline: {}".format(e))
                return

            unreal.log("Pipeline initialized, rendering...")

            # Send initial status
            self.send_status_update(0, 'Starting...', 'in progress')

        except Exception as e:
            self._fail("Unexpected error in execute_delayed: {}".format(e))

    def _fail(self, error_message):
        """Handle a fatal error - report to server and exit."""
        unreal.log_error("FATAL: {}".format(error_message))
        self._error_message = error_message
        self.send_status_update(0, 'N/A', 'errored', error_message=error_message)
        self.pipeline = None
        self.on_executor_finished_impl()

    @unreal.ufunction(ret=None, params=[unreal.MoviePipelineOutputData])
    def on_movie_pipeline_finished(self, results):
        """Called when render is complete"""
        try:
            success = results.success if results else False
            unreal.log("Render finished! Success: {}".format(success))

            if success:
                self.send_status_update(100, 'N/A', 'finished')
            else:
                error_msg = self._error_message or "Render failed (unknown reason)"
                self.send_status_update(100, 'N/A', 'errored', error_message=error_msg)

        except Exception as e:
            unreal.log_error("Error in on_movie_pipeline_finished: {}".format(e))

        self.pipeline = None
        self.on_executor_finished_impl()

    @unreal.ufunction(override=True)
    def is_rendering(self):
        """Required override"""
        return self.pipeline is not None

    @unreal.ufunction(override=True)
    def on_begin_frame(self):
        """Called every frame - send throttled progress updates"""
        try:
            super(MyExecutor, self).on_begin_frame()

            if not self.pipeline:
                return

            # Ensure attributes exist (in case _post_init wasn't called)
            if not hasattr(self, '_last_update_time'):
                self._last_update_time = 0.0
            if not hasattr(self, '_last_progress'):
                self._last_progress = -1.0
            if not hasattr(self, '_http_failures'):
                self._http_failures = 0

            # Get progress
            try:
                progress = 100 * unreal.MoviePipelineLibrary.get_completion_percentage(self.pipeline)
            except Exception:
                progress = 0

            # Get time estimate
            try:
                time_estimate = unreal.MoviePipelineLibrary.get_estimated_time_remaining(self.pipeline)
                if time_estimate:
                    days, hours, minutes, seconds, _ = time_estimate.to_tuple()
                    time_str = '{}h:{}m:{}s'.format(hours, minutes, seconds)
                else:
                    time_str = 'Calculating...'
            except Exception:
                time_str = 'Unknown'

            # Throttle updates
            current_time = time.time()
            time_since_update = current_time - self._last_update_time
            progress_delta = abs(progress - self._last_progress)

            should_update = (
                self._last_progress < 0 or
                time_since_update >= PROGRESS_UPDATE_INTERVAL or
                progress_delta >= 5.0
            )

            if progress == 0:
                unreal.log("Initializing...")
            else:
                unreal.log("Progress: {:.1f}% ETA: {}".format(progress, time_str))

            if should_update:
                self._last_update_time = current_time
                self._last_progress = progress
                self.send_status_update(progress, time_str, 'in progress')

        except Exception as e:
            unreal.log_warning("Error in on_begin_frame: {}".format(e))

    @unreal.ufunction(ret=None, params=[int, int, str])
    def on_http_response(self, index, code, message):
        """HTTP response callback - track failures"""
        try:
            if code >= 200 and code < 300:
                # Success - reset failure counter
                self._http_failures = 0
            else:
                self._http_failures += 1
                unreal.log_warning("HTTP error {} (failure {}/{}): {}".format(
                    code, self._http_failures, MAX_HTTP_FAILURES,
                    message[:100] if message else ""
                ))

                if self._http_failures >= MAX_HTTP_FAILURES:
                    unreal.log_warning("Server unreachable - continuing render without updates")
        except Exception as e:
            unreal.log_warning("Error in on_http_response: {}".format(e))

    def send_status_update(self, progress, time_estimate, status, error_message=None):
        """Send status update to render server"""
        if not self.job_id:
            unreal.log_warning("send_status_update: no job_id set!")
            return

        # Ensure _http_failures exists
        if not hasattr(self, '_http_failures'):
            self._http_failures = 0

        # Skip if too many failures (server probably down)
        if self._http_failures >= MAX_HTTP_FAILURES and status not in ('finished', 'errored'):
            return

        url = '{}/put/{}'.format(SERVER_API_URL, self.job_id)
        data = {
            'progress': progress,
            'time_estimate': time_estimate,
            'status': status
        }
        if error_message:
            data['error_message'] = error_message

        body = json.dumps(data)

        headers = unreal.Map(str, str)
        headers['Content-Type'] = 'application/json'

        unreal.log("HTTP PUT {} -> {}".format(url, body[:100]))

        try:
            self.send_http_request(url, "PUT", body, headers)
        except Exception as e:
            unreal.log_warning("Failed to send HTTP request: {}".format(e))
            self._http_failures += 1
