"""
MRQ executor for command-line rendering with server callbacks
"""

import os
import unreal


# Server URL from environment variable
SERVER_API_URL = os.environ.get('RENDER_SERVER_URL', 'http://127.0.0.1:5000') + '/api'
unreal.log("=== MyExecutor module loaded ===")
unreal.log("  SERVER_API_URL: {}".format(SERVER_API_URL))


def fix_asset_path(path):
    """
    Fix Unreal asset paths to include both package and asset name.

    WRONG:  /Game/Path/Asset         (loads Package, not the asset!)
    RIGHT:  /Game/Path/Asset.Asset   (loads the actual asset)

    To get the correct path in Unreal Editor: Right-click asset -> "Copy Object Path"
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

        # Register HTTP response callback
        self.http_response_recieved_delegate.add_function_unique(
            self,
            "on_http_response"
        )

    @unreal.ufunction(override=True)
    def execute_delayed(self, queue):
        # Parse commandline
        (cmd_tokens, cmd_switches, cmd_parameters) = unreal.SystemLibrary.\
            parse_command_line(unreal.SystemLibrary.get_command_line())

        map_path = fix_asset_path(cmd_tokens[0])
        seq_path = fix_asset_path(cmd_parameters.get('LevelSequence', ''))
        preset_path = fix_asset_path(cmd_parameters.get('MoviePipelineConfig', ''))
        self.job_id = cmd_parameters.get('JobId', '')

        unreal.log("=== MyExecutor: Job {} ===".format(self.job_id))
        unreal.log("  Map: {}".format(map_path))
        unreal.log("  Sequence: {}".format(seq_path))
        unreal.log("  Config: {}".format(preset_path))

        # Load preset
        preset_soft = unreal.SoftObjectPath(preset_path)
        u_preset = unreal.SystemLibrary.conv_soft_obj_path_to_soft_obj_ref(preset_soft)

        if not u_preset:
            unreal.log_error("FAILED to load preset: {}".format(preset_path))
            self.send_status_update(0, 'N/A', 'error')
            return

        # Initialize pipeline
        self.pipeline = unreal.new_object(
            self.target_pipeline_class,
            outer=self.get_last_loaded_world(),
            base_type=unreal.MoviePipeline
        )

        # Initialize queue with single job
        self.queue = unreal.new_object(unreal.MoviePipelineQueue, outer=self)
        job = self.queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
        job.map = unreal.SoftObjectPath(map_path)
        job.sequence = unreal.SoftObjectPath(seq_path)
        job.set_configuration(u_preset)

        # Register finished callback
        self.pipeline.on_movie_pipeline_work_finished_delegate.add_function_unique(
            self, "on_movie_pipeline_finished"
        )

        self.pipeline.initialize(job)
        unreal.log("Pipeline initialized, rendering...")

        # Send initial status
        self.send_status_update(0, 'Starting...', 'in progress')

    @unreal.ufunction(ret=None, params=[unreal.MoviePipelineOutputData])
    def on_movie_pipeline_finished(self, results):
        """Called when render is complete"""
        unreal.log("Render finished! Success: {}".format(results.success))
        self.send_status_update(100, 'N/A', 'finished')
        self.pipeline = None
        self.on_executor_finished_impl()

    @unreal.ufunction(override=True)
    def is_rendering(self):
        """Required override"""
        return self.pipeline is not None

    @unreal.ufunction(override=True)
    def on_begin_frame(self):
        """Called every frame - send progress updates"""
        super(MyExecutor, self).on_begin_frame()

        if not self.pipeline:
            return

        # Get progress
        progress = 100 * unreal.MoviePipelineLibrary.get_completion_percentage(self.pipeline)

        # Get time estimate
        time_estimate = unreal.MoviePipelineLibrary.get_estimated_time_remaining(self.pipeline)
        if time_estimate:
            days, hours, minutes, seconds, _ = time_estimate.to_tuple()
            time_str = '{}h:{}m:{}s'.format(hours, minutes, seconds)
        else:
            time_str = 'Calculating...'

        # Log engine warm up vs rendering
        if progress == 0:
            current, total = unreal.MoviePipelineBlueprintLibrary.get_engine_warm_up_frame_count(self.pipeline, 0)
            warmup_str = "Warm Up {}/{}".format(current, total)
            unreal.log("Engine Warm Up Frame {}/{}".format(current, total))
            self.send_status_update(progress, warmup_str, 'in progress')
        else:
            unreal.log("Progress: {:.1f}% ETA: {}".format(progress, time_str))
            self.send_status_update(progress, time_str, 'in progress')

    @unreal.ufunction(ret=None, params=[int, int, str])
    def on_http_response(self, index, code, message):
        """HTTP response callback"""
        unreal.log("HTTP response: {} {}".format(code, message[:50] if message else ""))

    def send_status_update(self, progress, time_estimate, status):
        """Send status update to render server"""
        if not self.job_id:
            unreal.log_warning("send_status_update: no job_id set!")
            return

        url = '{}/put/{}'.format(SERVER_API_URL, self.job_id)
        body = '{};{};{}'.format(progress, time_estimate, status)

        unreal.log("HTTP PUT {} -> {}".format(url, body))
        self.send_http_request(url, "PUT", body, unreal.Map(str, str))
