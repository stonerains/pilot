#!/usr/bin/env python3
import numpy as np
import unittest

import panda.tests.safety.common as common

from panda import Panda
from panda.tests.safety import libpandasafety_py
from panda.tests.safety.common import CANPackerPanda

MSG_EngBrakeData = 0x165          # RX from PCM, for driver brake pedal and cruise state
MSG_EngVehicleSpThrottle = 0x204  # RX from PCM, for driver throttle input
MSG_Steering_Data_FD1 = 0x083     # TX by OP, various driver switches and LKAS/CC buttons
MSG_ACCDATA_3 = 0x18A             # TX by OP, ACC/TJA user interface
MSG_Lane_Assist_Data1 = 0x3CA     # TX by OP, Lane Keep Assist
MSG_LateralMotionControl = 0x3D3  # TX by OP, Traffic Jam Assist
MSG_IPMA_Data = 0x3D8             # TX by OP, IPMA and LKAS user interface


class Buttons:
  CANCEL = 0
  RESUME = 1
  TJA_TOGGLE = 2


class TestFordSafety(common.PandaSafetyTest):
  STANDSTILL_THRESHOLD = 1
  RELAY_MALFUNCTION_ADDR = MSG_IPMA_Data
  RELAY_MALFUNCTION_BUS = 0

  TX_MSGS = [
    [MSG_Steering_Data_FD1, 0], [MSG_Steering_Data_FD1, 2], [MSG_ACCDATA_3, 0], [MSG_Lane_Assist_Data1, 0],
    [MSG_LateralMotionControl, 0], [MSG_IPMA_Data, 0],
  ]
  FWD_BLACKLISTED_ADDRS = {2: [MSG_ACCDATA_3, MSG_Lane_Assist_Data1, MSG_LateralMotionControl, MSG_IPMA_Data]}
  FWD_BUS_LOOKUP = {0: 2, 2: 0}

  def setUp(self):
    self.packer = CANPackerPanda("ford_lincoln_base_pt")
    self.safety = libpandasafety_py.libpandasafety
    self.safety.set_safety_hooks(Panda.SAFETY_FORD, 0)
    self.safety.init_tests()

  # Driver brake pedal
  def _user_brake_msg(self, brake: bool):
    # brake pedal and cruise state share same message, so we have to send
    # the other signal too
    enable = self.safety.get_controls_allowed()
    values = {
      "BpedDrvAppl_D_Actl": 2 if brake else 1,
      "CcStat_D_Actl": 5 if enable else 0,
    }
    return self.packer.make_can_msg_panda("EngBrakeData", 0, values)

  # Standstill state
  def _speed_msg(self, speed: float):
    values = {"VehStop_D_Stat": 1 if speed <= self.STANDSTILL_THRESHOLD else 0}
    return self.packer.make_can_msg_panda("DesiredTorqBrk", 0, values)

  # Drive throttle input
  def _user_gas_msg(self, gas: float):
    values = {"ApedPos_Pc_ActlArb": gas}
    return self.packer.make_can_msg_panda("EngVehicleSpThrottle", 0, values)

  # Cruise status
  def _pcm_status_msg(self, enable: bool):
    # brake pedal and cruise state share same message, so we have to send
    # the other signal too
    brake = self.safety.get_brake_pressed_prev()
    values = {
      "BpedDrvAppl_D_Actl": 2 if brake else 1,
      "CcStat_D_Actl": 5 if enable else 0,
    }
    return self.packer.make_can_msg_panda("EngBrakeData", 0, values)

  # LKAS command
  def _lkas_command_msg(self, action: int):
    values = {
      "LkaActvStats_D2_Req": action,
    }
    return self.packer.make_can_msg_panda("Lane_Assist_Data1", 0, values)

  # TJA command
  def _tja_command_msg(self, enabled: bool, path_offset: float, path_angle: float, curvature: float, curvature_rate: float):
    values = {
      "LatCtl_D_Rq": 1 if enabled else 0,
      "LatCtlPathOffst_L_Actl": path_offset,     # Path offset [-5.12|5.11] meter
      "LatCtlPath_An_Actl": path_angle,          # Path angle [-0.5|0.5235] radians
      "LatCtlCurv_NoRate_Actl": curvature_rate,  # Curvature rate [-0.001024|0.00102375] 1/meter^2
      "LatCtlCurv_No_Actl": curvature,           # Curvature [-0.02|0.02094] 1/meter
    }
    return self.packer.make_can_msg_panda("LateralMotionControl", 0, values)

  # Cruise control buttons
  def _acc_button_msg(self, button: int, bus: int):
    values = {
      "CcAslButtnCnclPress": 1 if button == Buttons.CANCEL else 0,
      "CcAsllButtnResPress": 1 if button == Buttons.RESUME else 0,
      "TjaButtnOnOffPress": 1 if button == Buttons.TJA_TOGGLE else 0,
    }
    return self.packer.make_can_msg_panda("Steering_Data_FD1", bus, values)

  def test_steer_allowed(self):
    path_offsets = np.arange(-5.12, 5.11, 1).round()
    path_angles = np.arange(-0.5, 0.5235, 0.1).round(1)
    curvature_rates = np.arange(-0.001024, 0.00102375, 0.001).round(3)
    curvatures = np.arange(-0.02, 0.02094, 0.01).round(2)

    for controls_allowed in (True, False):
      for steer_control_enabled in (True, False):
        for path_offset in path_offsets:
          for path_angle in path_angles:
            for curvature_rate in curvature_rates:
              for curvature in curvatures:
                self.safety.set_controls_allowed(controls_allowed)
                enabled = steer_control_enabled or curvature != 0

                should_tx = path_offset == 0 and path_angle == 0 and curvature_rate == 0
                should_tx = should_tx and (not enabled or controls_allowed)
                self.assertEqual(should_tx, self._tx(self._tja_command_msg(steer_control_enabled, path_offset, path_angle, curvature, curvature_rate)))

  def test_prevent_lkas_action(self):
    self.safety.set_controls_allowed(1)
    self.assertFalse(self._tx(self._lkas_command_msg(1)))

    self.safety.set_controls_allowed(0)
    self.assertFalse(self._tx(self._lkas_command_msg(1)))

  def test_acc_buttons(self):
    for allowed in (0, 1):
      self.safety.set_controls_allowed(allowed)
      for enabled in (True, False):
        self._rx(self._pcm_status_msg(enabled))
        self.assertTrue(self._tx(self._acc_button_msg(Buttons.TJA_TOGGLE, 2)))

    for allowed in (0, 1):
      self.safety.set_controls_allowed(allowed)
      for bus in (0, 2):
        self.assertEqual(allowed, self._tx(self._acc_button_msg(Buttons.RESUME, bus)))

    for enabled in (True, False):
      self._rx(self._pcm_status_msg(enabled))
      for bus in (0, 2):
        self.assertEqual(enabled, self._tx(self._acc_button_msg(Buttons.CANCEL, bus)))


if __name__ == "__main__":
  unittest.main()
