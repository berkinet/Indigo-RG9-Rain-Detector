import importlib.util
import logging
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "RG-9 Rain Detector.indigoPlugin" / "Contents" / "Server Plugin"
sys.path.insert(0, str(SERVER))


class PluginBase:
    class StopThread(Exception):
        pass

    def __init__(self, plugin_id, display_name, version, prefs):
        self.pluginId = plugin_id
        self.pluginPrefs = prefs
        self.logger = logging.getLogger("test")

    def deviceStartComm(self, dev):
        pass

    def deviceStopComm(self, dev):
        pass


class Devices(dict):
    def subscribeToChanges(self):
        pass

    def __iter__(self):
        return iter(self.values())


class Variables(dict):
    pass


indigo = ModuleType("indigo")
indigo.PluginBase = PluginBase
indigo.Dict = dict
indigo.devices = Devices()
indigo.variables = Variables()
indigo.variable = SimpleNamespace(updateValue=Mock())
indigo.kStateImageSel = SimpleNamespace(SensorOn="sensor-on", SensorOff="sensor-off")
sys.modules["indigo"] = indigo

spec = importlib.util.spec_from_file_location("rg9_plugin", SERVER / "plugin.py")
plugin_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin_module
spec.loader.exec_module(plugin_module)


class PluginTests(unittest.TestCase):
    def setUp(self):
        indigo.devices.clear()
        indigo.variables.clear()
        indigo.variables[1208422529] = SimpleNamespace(
            id=1208422529, value="0"
        )
        indigo.variable.updateValue.reset_mock()
        self.plugin = plugin_module.Plugin("plugin.id", "RG-9", "0.1.0", {})
        self.detector = SimpleNamespace(
            id=1,
            name="Rain detector",
            deviceTypeId="rainDetector",
            pluginProps={
                "sourceDeviceId": "924647097",
                "secondDetectionWindowSeconds": "60",
                "minimumDetectionDurationSeconds": "60",
                "dryPeriodSeconds": "60",
            },
            states={},
            updateStatesOnServer=Mock(),
            updateStateImageOnServer=Mock(),
        )
        self.plugin.deviceStartComm(self.detector)

    def test_only_rising_edges_are_detections(self):
        source_off = SimpleNamespace(id=924647097, states={"onOffState": False})
        source_on = SimpleNamespace(id=924647097, states={"onOffState": True})
        state = self.plugin._states[1]

        self.plugin.deviceUpdated(source_off, source_on)
        self.plugin.deviceUpdated(source_on, source_off)

        self.assertEqual(state.detections_today, 1)

    def test_textual_source_states_are_treated_as_real_edges(self):
        source_off = SimpleNamespace(
            id=924647097, states={"onOffState": "false"}
        )
        source_on = SimpleNamespace(
            id=924647097, states={"onOffState": "true"}
        )
        state = self.plugin._states[1]

        self.plugin.deviceUpdated(source_off, source_on)
        self.plugin.deviceUpdated(source_on, source_off)
        self.plugin.deviceUpdated(source_off, source_on)

        self.assertEqual(state.detections_today, 2)
        self.assertTrue(state.is_raining)

    def test_textual_off_state_is_restored_as_low(self):
        indigo.devices[924647097] = SimpleNamespace(
            id=924647097, states={"onOffState": "0"}
        )

        state = self.plugin._restore_state(self.detector)

        self.assertFalse(state.source_high)

    def test_unrelated_device_is_ignored(self):
        original = SimpleNamespace(id=2, states={"onOffState": False})
        updated = SimpleNamespace(id=2, states={"onOffState": True})
        self.plugin.deviceUpdated(original, updated)
        self.assertEqual(self.plugin._states[1].detections_today, 0)

    def test_configuration_validation(self):
        indigo.devices[924647097] = object()
        valid, values = self.plugin.validateDeviceConfigUi(
            {
                "sourceDeviceId": "924647097",
                "secondDetectionWindowSeconds": "30",
                "minimumDetectionDurationSeconds": "15",
                "dryPeriodSeconds": "60",
            },
            "rainDetector",
            0,
        )
        self.assertTrue(valid)
        self.assertEqual(values["secondDetectionWindowSeconds"], "30")
        self.assertEqual(values["minimumDetectionDurationSeconds"], "15")

    def test_old_confirmation_setting_is_used_for_both_new_timers(self):
        self.detector.pluginProps = {
            "sourceDeviceId": "924647097",
            "confirmationWindowSeconds": "45",
            "dryPeriodSeconds": "60",
        }
        state = self.plugin._restore_state(self.detector)
        self.assertEqual(state.second_detection_seconds, 45)
        self.assertEqual(state.minimum_high_seconds, 45)

    def test_plugin_config_controls_debug_logging(self):
        self.assertFalse(self.plugin.debug)
        self.plugin.closedPrefsConfigUi({"showDebugInfo": True}, False)
        self.assertTrue(self.plugin.debug)
        self.plugin.closedPrefsConfigUi({"showDebugInfo": False}, True)
        self.assertTrue(self.plugin.debug)

    def test_source_menu_only_includes_devices_with_on_off_state(self):
        indigo.devices.clear()
        indigo.devices[10] = SimpleNamespace(
            id=10, name="Switch", states={"onOffState": False}
        )
        indigo.devices[11] = SimpleNamespace(id=11, name="Other", states={})
        self.assertEqual(self.plugin.availableSourceDevices(), [("10", "Switch")])

    def test_active_rain_timestamps_are_restored(self):
        self.detector.states = {
            "dayKey": plugin_module.datetime.now().date().isoformat(),
            "accumulatedSeconds": 12,
            "detectionsToday": 4,
            "candidateAt": "",
            "rainingSince": "2026-08-17 10:00:00",
            "lastDetection": "2026-08-17 10:01:00",
        }
        state = self.plugin._restore_state(self.detector)
        self.assertTrue(state.is_raining)
        self.assertEqual(state.accumulated_seconds, 12)
        self.assertEqual(state.last_detection.minute, 1)

    def test_days_since_last_rain_variable_increments_existing_count(self):
        variable = SimpleNamespace(id=1208422529, value="7")
        indigo.variables[1208422529] = variable
        indigo.variable.updateValue.reset_mock()
        self.plugin._days_counter_day[1] = plugin_module.datetime(
            2026, 8, 15
        ).date()

        self.plugin._update_days_since_last_rain(
            1, plugin_module.datetime(2026, 8, 17, 0, 1)
        )

        indigo.variable.updateValue.assert_called_once_with(
            1208422529, value="9"
        )

    def test_confirmed_rain_resets_days_variable(self):
        variable = SimpleNamespace(id=1208422529, value="7")
        indigo.variables[1208422529] = variable
        indigo.variable.updateValue.reset_mock()
        source_off = SimpleNamespace(id=924647097, states={"onOffState": False})
        source_on = SimpleNamespace(id=924647097, states={"onOffState": True})

        self.plugin.deviceUpdated(source_off, source_on)
        self.plugin.deviceUpdated(source_on, source_off)
        self.plugin.deviceUpdated(source_off, source_on)

        indigo.variable.updateValue.assert_called_with(1208422529, value="0")

    def test_falling_edge_is_recorded_as_end_of_rainfall(self):
        source_off = SimpleNamespace(id=924647097, states={"onOffState": False})
        source_on = SimpleNamespace(id=924647097, states={"onOffState": True})
        state = self.plugin._states[1]

        self.plugin.deviceUpdated(source_off, source_on)
        self.plugin.deviceUpdated(source_on, source_off)

        self.assertFalse(state.source_high)
        self.assertIsNotNone(state.low_since)

    def test_day_with_rain_does_not_increment_at_midnight(self):
        variable = SimpleNamespace(id=1208422529, value="0")
        indigo.variables[1208422529] = variable
        indigo.variable.updateValue.reset_mock()
        state = self.plugin._states[1]
        start = plugin_module.datetime(2026, 8, 16, 23, 59, 30)
        state.detection(start)
        state.detection(start.replace(second=40))
        self.plugin._days_counter_day[1] = start.date()
        self.plugin._last_rain_detected[1] = start.replace(second=40)

        self.plugin._update_days_since_last_rain(
            1, plugin_module.datetime(2026, 8, 17, 0, 0, 10)
        )

        indigo.variable.updateValue.assert_not_called()
        self.assertEqual(
            self.plugin._days_counter_day[1],
            plugin_module.datetime(2026, 8, 17).date(),
        )

    def test_only_full_rain_free_day_increments_counter(self):
        variable = SimpleNamespace(id=1208422529, value="0")
        indigo.variables[1208422529] = variable
        indigo.variable.updateValue.reset_mock()
        self.plugin._days_counter_day[1] = plugin_module.datetime(
            2026, 8, 16
        ).date()
        self.plugin._last_rain_detected[1] = plugin_module.datetime(
            2026, 8, 16, 12, 0
        )

        self.plugin._update_days_since_last_rain(
            1, plugin_module.datetime(2026, 8, 18, 0, 0, 1)
        )

        indigo.variable.updateValue.assert_called_once_with(
            1208422529, value="1"
        )

    def test_last_rain_ended_uses_final_detection_time(self):
        state = self.plugin._states[1]
        start = plugin_module.datetime(2026, 8, 17, 10, 0, 0)
        state.detection(start)
        state.detection(start.replace(second=10))
        state.detection(start.replace(second=30))
        self.assertTrue(state.advance(start.replace(minute=2)))
        self.plugin._last_rain_ended[1] = state.last_detection

        self.plugin._publish(self.detector, state, start.replace(minute=2), force=True)

        updates = self.detector.updateStatesOnServer.call_args.args[0]
        update_by_key = {item["key"]: item for item in updates}
        values = {key: item["value"] for key, item in update_by_key.items()}
        self.assertEqual(values["lastRainEnded"], "2026-08-17 10:00:30")
        self.assertEqual(
            update_by_key["lastRainEnded"]["uiValue"],
            "2026-08-17 10:00",
        )

    def test_timestamp_display_omits_seconds_without_losing_stored_precision(self):
        state = self.plugin._states[1]
        state.last_detection = plugin_module.datetime(2026, 8, 20, 14, 2, 5)

        self.plugin._publish(
            self.detector, state, plugin_module.datetime.now(), force=True
        )

        updates = {
            item["key"]: item
            for item in self.detector.updateStatesOnServer.call_args.args[0]
        }
        self.assertEqual(
            updates["lastDetection"]["value"], "2026-08-20 14:02:05"
        )
        self.assertEqual(
            updates["lastDetection"]["uiValue"], "2026-08-20 14:02"
        )

    def test_on_off_state_has_ui_value_and_matching_icon(self):
        self.detector.updateStatesOnServer.reset_mock()
        self.detector.updateStateImageOnServer.reset_mock()
        state = self.plugin._states[1]

        self.plugin._publish(
            self.detector, state, plugin_module.datetime.now(), force=True
        )

        updates = self.detector.updateStatesOnServer.call_args.args[0]
        on_off = next(item for item in updates if item["key"] == "onOffState")
        self.assertEqual(on_off["uiValue"], "Dry")
        self.detector.updateStateImageOnServer.assert_called_once_with("sensor-off")

    def test_icon_updates_when_indigo_immediately_caches_new_state(self):
        state = self.plugin._states[1]
        self.detector.states["onOffState"] = True
        self.detector.updateStateImageOnServer.reset_mock()

        def cache_updates(updates):
            for update in updates:
                self.detector.states[update["key"]] = update["value"]

        self.detector.updateStatesOnServer.side_effect = cache_updates

        self.plugin._publish(
            self.detector, state, plugin_module.datetime.now()
        )

        self.assertFalse(self.detector.states["onOffState"])
        self.detector.updateStateImageOnServer.assert_called_once_with("sensor-off")


if __name__ == "__main__":
    unittest.main()
