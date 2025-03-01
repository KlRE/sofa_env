from copy import deepcopy
import numpy as np
from sofa_env.utils.human_input import XboxController
from sofa_env.scenes.ligating_loop.ligating_loop_env import LigatingLoopEnv, ObservationType, ActionType, RenderMode
from sofa_env.wrappers.trajectory_recorder import TrajectoryRecorder
from sofa_env.wrappers.realtime import RealtimeWrapper
import time
from collections import deque
import cv2
import argparse
from pathlib import Path
from typing import Tuple

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Setup human input behavior.")
    parser.add_argument("-rv", "--record_video", action="store_true", help="Record video of the trajectory.")
    parser.add_argument("-rt", "--record_trajectory", action="store_true", help="Record the full trajectory.")
    parser.add_argument("-i", "--info", action="store", type=str, help="Additional info to store in the metadata.")
    args = parser.parse_args()

    controller = XboxController()
    time.sleep(0.1)
    if not controller.is_alive():
        raise RuntimeError("Could not find Xbox controller.")

    image_shape = (1024, 1024)
    image_shape_to_save = (256, 256)

    env = LigatingLoopEnv(
        observation_type=ObservationType.STATE,
        render_mode=RenderMode.HUMAN,
        action_type=ActionType.CONTINUOUS,
        image_shape=image_shape,
        frame_skip=1,
        time_step=1 / 30,
        settle_steps=50,
        randomize_marking_position=False,
        band_width=8.0,
        disable_in_cavity_checks=True,
        create_scene_kwargs={
            "stiff_loop": False,
            "num_rope_points": 60,
            "loop_radius": 20,
        },
    )

    env = RealtimeWrapper(env)

    if args.record_video:
        video_folder = Path("videos")
        video_folder.mkdir(exist_ok=True)
        video_name = time.strftime("%Y%m%d-%H%M%S")
        video_path = video_folder / f"{video_name}.mp4"
        video_writer = cv2.VideoWriter(
            str(video_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            1 / (env.time_step / env.frame_skip),
            image_shape[::-1],
        )
    else:
        video_writer = None

    if args.record_trajectory:

        def save_instrument_state(self: TrajectoryRecorder):
            self.trajectory["loop_tpsdc_state"].append(deepcopy(self.env.loop.get_articulated_state()))
            self.trajectory["loop_pose"].append(deepcopy(self.env.loop.get_pose()))

            if len(self.trajectory["loop_tpsdc_state"]) == 1:
                loop_tpsdc_velocity = np.zeros_like(self.trajectory["loop_tpsdc_state"][0])
            else:
                previous_loop_tpsdc_state = self.trajectory["loop_tpsdc_state"][-2]
                loop_tpsdc_velocity = (self.env.loop.get_articulated_state() - previous_loop_tpsdc_state) / (self.env.time_step * self.env.frame_skip)

            self.trajectory["loop_tpsdc_velocity"].append(loop_tpsdc_velocity)

        def save_deformable_object_points(self: TrajectoryRecorder):
            cavity_state = self.cavity.get_state()

            self.trajectory["cavity_tracking_positions"].append(deepcopy(cavity_state[self.env.cavity_tracking_point_indices].ravel()))
            self.trajectory["marking_tracking_positions"].append(deepcopy(cavity_state[self.env.marking_tracking_point_indices].ravel()))
            self.trajectory["loop_tracking_positions"].append(deepcopy(self.env.loop.get_loop_positions()[self.env.loop_tracking_point_indices].ravel()))

        def store_rgb_obs(self: TrajectoryRecorder, shape: Tuple[int, int] = image_shape_to_save):
            observation = self.env.render()
            observation = cv2.resize(
                observation,
                shape,
                interpolation=cv2.INTER_AREA,
            )
            self.trajectory["rgb"].append(observation)

        def save_time(self: TrajectoryRecorder):
            self.trajectory["time"].append(len(self.trajectory["time"]) * self.env.time_step * self.env.frame_skip)

        metadata = {
            "frame_skip": env.frame_skip,
            "time_step": env.time_step,
            "observation_type": env.observation_type.name,
            "reward_amount_dict": env.reward_amount_dict,
            "user_info": args.info,
        }

        env = TrajectoryRecorder(
            env,
            log_dir="trajectories",
            metadata=metadata,
            store_info=True,
            save_compressed_keys=[
                "observation",
                "terminal_observation",
                "rgb",
                "info",
                "loop_tpsdc_state",
                "loop_tpsdc_velocity",
                "loop_pose",
                "cavity_tracking_positions",
                "marking_tracking_positions",
                "loop_tracking_positions",
                "time",
            ],
            after_step_callbacks=[
                store_rgb_obs,
                save_instrument_state,
                save_deformable_object_points,
                save_time,
            ],
            after_reset_callbacks=[
                store_rgb_obs,
                save_instrument_state,
                save_deformable_object_points,
                save_time,
            ],
        )

    reset_obs, reset_info = env.reset()
    if video_writer is not None:
        video_writer.write(env.render()[:, :, ::-1])
    done = False

    fps_list = deque(maxlen=100)

    while not done:
        start = time.perf_counter()
        lx, ly, rx, ry, lt, rt = controller.read()
        action = np.zeros_like(env.action_space.sample())
        action[0] = rx
        action[1] = ry
        action[2] = lx
        action[3] = rt - lt
        action[4] = controller.a - controller.b
        obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated

        if video_writer is not None:
            video_writer.write(env.render()[:, :, ::-1])

        if controller.x:
            cv2.imwrite("exit_image.png", env.render()[:, :, ::-1])
            break

        end = time.perf_counter()
        fps = 1 / (end - start)
        fps_list.append(fps)
        print(f"FPS Mean: {np.mean(fps_list):.5f}    STD: {np.std(fps_list):.5f}")

    if video_writer is not None:
        video_writer.release()
