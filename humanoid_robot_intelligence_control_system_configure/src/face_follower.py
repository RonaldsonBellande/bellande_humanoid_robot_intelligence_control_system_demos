#!/usr/bin/env python3

# Copyright (C) 2024 Bellande Robotics Sensors Research Innovation Center, Ronaldson Bellande
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import rospy
import math
from sensor_msgs.msg import JointState
from std_msgs.msg import String, Bool
from geometry_msgs.msg import Point
from humanoid_robot_intelligence_control_system_walking_module_msgs.msg import WalkingParam
from humanoid_robot_intelligence_control_system_walking_module_msgs.srv import GetWalkingParam

class FaceFollower:
    def __init__(self):
        rospy.init_node('face_follower')
        
        self.FOV_WIDTH = 35.2 * math.pi / 180
        self.FOV_HEIGHT = 21.6 * math.pi / 180
        self.count_not_found = 0
        self.count_to_approach = 0
        self.on_following = False
        self.approach_face_position = "NotFound"
        self.CAMERA_HEIGHT = 0.46
        self.NOT_FOUND_THRESHOLD = 50
        self.MAX_FB_STEP = 40.0 * 0.001
        self.MAX_RL_TURN = 15.0 * math.pi / 180
        self.IN_PLACE_FB_STEP = -3.0 * 0.001
        self.MIN_FB_STEP = 5.0 * 0.001
        self.MIN_RL_TURN = 5.0 * math.pi / 180
        self.UNIT_FB_STEP = 1.0 * 0.001
        self.UNIT_RL_TURN = 0.5 * math.pi / 180
        self.SPOT_FB_OFFSET = 0.0 * 0.001
        self.SPOT_RL_OFFSET = 0.0 * 0.001
        self.SPOT_ANGLE_OFFSET = 0.0
        self.hip_pitch_offset = 7.0
        self.current_pan = -10
        self.current_tilt = -10
        self.current_x_move = 0.005
        self.current_r_angle = 0
        self.curr_period_time = 0.6
        self.accum_period_time = 0.0
        self.DEBUG_PRINT = False

        self.current_joint_states_sub = rospy.Subscriber(
            "/humanoid_robot_intelligence_control_system/goal_joint_states", JointState, self.current_joint_states_callback)
        self.set_walking_command_pub = rospy.Publisher(
            "/humanoid_robot_intelligence_control_system/walking/command", String, queue_size=1)
        self.set_walking_param_pub = rospy.Publisher(
            "/humanoid_robot_intelligence_control_system/walking/set_params", WalkingParam, queue_size=1)
        self.get_walking_param_client = rospy.ServiceProxy(
            "/humanoid_robot_intelligence_control_system/walking/get_params", GetWalkingParam)

        self.face_position_sub = rospy.Subscriber(
            "/face_detector/face_position", Point, self.face_position_callback)

        self.prev_time = rospy.Time.now()
        self.current_walking_param = WalkingParam()

    def start_following(self):
        self.on_following = True
        rospy.loginfo("Start Face following")
        self.set_walking_command("start")
        result = self.get_walking_param()
        if result:
            self.hip_pitch_offset = self.current_walking_param.hip_pitch_offset
            self.curr_period_time = self.current_walking_param.period_time
        else:
            self.hip_pitch_offset = 7.0 * math.pi / 180
            self.curr_period_time = 0.6

    def stop_following(self):
        self.on_following = False
        self.count_to_approach = 0
        rospy.loginfo("Stop Face following")
        self.set_walking_command("stop")

    def current_joint_states_callback(self, msg):
        for i, name in enumerate(msg.name):
            if name == "head_pan":
                self.current_pan = msg.position[i]
            elif name == "head_tilt":
                self.current_tilt = msg.position[i]

    def face_position_callback(self, msg):
        if self.on_following:
            self.process_following(msg.x, msg.y, msg.z)

    def calc_footstep(self, target_distance, target_angle, delta_time):
        next_movement = self.current_x_move
        target_distance = max(0, target_distance)
        fb_goal = min(target_distance * 0.1, self.MAX_FB_STEP)
        self.accum_period_time += delta_time
        if self.accum_period_time > (self.curr_period_time / 4):
            self.accum_period_time = 0.0
            if (target_distance * 0.1 / 2) < self.current_x_move:
                next_movement -= self.UNIT_FB_STEP
            else:
                next_movement += self.UNIT_FB_STEP
        fb_goal = min(next_movement, fb_goal)
        fb_move = max(fb_goal, self.MIN_FB_STEP)

        rl_goal = 0.0
        if abs(target_angle) * 180 / math.pi > 5.0:
            rl_offset = abs(target_angle) * 0.2
            rl_goal = min(rl_offset, self.MAX_RL_TURN)
            rl_goal = max(rl_goal, self.MIN_RL_TURN)
            rl_angle = min(abs(self.current_r_angle) + self.UNIT_RL_TURN, rl_goal)
            if target_angle < 0:
                rl_angle *= -1
        else:
            rl_angle = 0

        return fb_move, rl_angle

    def process_following(self, x_angle, y_angle, face_size):
        curr_time = rospy.Time.now()
        delta_time = (curr_time - self.prev_time).to_sec()
        self.prev_time = curr_time

        self.count_not_found = 0

        if self.current_tilt == -10 and self.current_pan == -10:
            rospy.logerr("Failed to get current angle of head joints.")
            self.set_walking_command("stop")
            self.on_following = False
            self.approach_face_position = "NotFound"
            return False

        self.approach_face_position = "OutOfRange"

        distance_to_face = self.CAMERA_HEIGHT * math.tan(math.pi * 0.5 + self.current_tilt - self.hip_pitch_offset - face_size)
        distance_to_face = abs(distance_to_face)

        distance_to_approach = 0.5  # Adjust this value as needed

        if (distance_to_face < distance_to_approach) and (abs(x_angle) < 25.0):
            self.count_to_approach += 1
            if self.count_to_approach > 20:
                self.set_walking_command("stop")
                self.on_following = False
                self.approach_face_position = "OnLeft" if x_angle > 0 else "OnRight"
                return True
            elif self.count_to_approach > 15:
                self.set_walking_param(self.IN_PLACE_FB_STEP, 0, 0)
                return False
        else:
            self.count_to_approach = 0

        distance_to_walk = distance_to_face - distance_to_approach
        fb_move, rl_angle = self.calc_footstep(distance_to_walk, self.current_pan, delta_time)
        self.set_walking_param(fb_move, 0, rl_angle)
        return False

    def set_walking_command(self, command):
        if command == "start":
            self.get_walking_param()
            self.set_walking_param(self.IN_PLACE_FB_STEP, 0, 0, True)
        msg = String()
        msg.data = command
        self.set_walking_command_pub.
