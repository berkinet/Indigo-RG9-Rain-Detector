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
sys.modules["indigo"] = indigo

spec = importlib.util.spec_from_file_location("rg9_plugin", SERVER / "plugin.py")
plugin_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = plugin_module
spec.loader.exec_module(plugin_module)


class PluginTests(unittest.TestCase):
    def setUp(self):
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
                "confirmationWindowSeconds": "60",
                "dryPeriodSeconds": "60",
            },
            states={},
            updateStatesOnServer=Mock(),
        )
        self.plugin.deviceStartComm(self.detector)

    def test_only_rising_edges_are_detections(self):
        source_off = SimpleNamespace(id=924647097, states={"onOffState": False})
        source_on = SimpleNamespace(id=924647097, states={"onOffState": True})
        state = self.plugin._states[1]

        self.plugin.deviceUpdated(source_off, source_on)
        self.plugin.deviceUpdated(source_on, source_off)

        self.assertEqual(state.detections_today, 1)

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
                "confirmationWindowSeconds": "30",
                "dryPeriodSeconds": "60",
            },
            "rainDetector",
            0,
        )
        self.assertTrue(valid)
        self.assertEqual(values["confirmationWindowSeconds"], "30")

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
        self.plugin.deviceUpdated(source_off, source_on)

        indigo.variable.updateValue.assert_called_with(1208422529, value="0")

    def test_active_rain_keeps_days_variable_zero_at_midnight(self):
        variable = SimpleNamespace(id=1208422529, value="0")
        indigo.variables[1208422529] = variable
        indigo.variable.updateValue.reset_mock()
        state = self.plugin._states[1]
        start = plugin_module.datetime(2026, 8, 16, 23, 59, 30)
        state.detection(start)
        state.detection(start.replace(second=40))
        self.plugin._days_counter_day[1] = start.date()

        self.plugin._update_days_since_last_rain(
            1, plugin_module.datetime(2026, 8, 17, 0, 0, 10)
        )

        indigo.variable.updateValue.assert_not_called()
        self.assertEqual(
            self.plugin._days_counter_day[1],
            plugin_module.datetime(2026, 8, 17).date(),
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
        values = {item["key"]: item["value"] for item in updates}
        self.assertEqual(values["lastRainEnded"], "2026-08-17 10:00:30")


if __name__ == "__main__":
    unittest.main()
