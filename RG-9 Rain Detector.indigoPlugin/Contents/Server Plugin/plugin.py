"""Indigo plugin for debouncing Hydreon RG-9 rain detections."""

from __future__ import annotations

import threading
from datetime import datetime

import indigo

from rain_logic import RainState, format_duration


DEVICE_TYPE = "rainDetector"
DAYS_SINCE_LAST_RAIN_VARIABLE_ID = 1208422529


class Plugin(indigo.PluginBase):
    def __init__(self, plugin_id, plugin_display_name, plugin_version, plugin_prefs):
        super().__init__(plugin_id, plugin_display_name, plugin_version, plugin_prefs)
        self._lock = threading.RLock()
        self._states = {}
        self._devices = {}
        self._last_rain_ended = {}
        self._last_rain_detected = {}
        self._days_counter_day = {}
        self._missing_variable_logged = False

    def startup(self):
        indigo.devices.subscribeToChanges()
        self.logger.info("RG-9 Rain Detector plugin started")

    def shutdown(self):
        self.logger.info("RG-9 Rain Detector plugin stopped")

    def runConcurrentThread(self):
        try:
            while True:
                now = datetime.now()
                with self._lock:
                    for device_id, state in list(self._states.items()):
                        ended = state.advance(now)
                        dev = self._devices.get(device_id)
                        if dev is not None:
                            if ended:
                                self._last_rain_ended[device_id] = state.last_detection
                                self.logger.info("Rain ended for %s", dev.name)
                            self._publish(dev, state, now)
                            self._update_days_since_last_rain(device_id, now)
                self.sleep(1)
        except self.StopThread:
            pass

    def deviceStartComm(self, dev):
        super().deviceStartComm(dev)
        if dev.deviceTypeId != DEVICE_TYPE:
            return
        with self._lock:
            state = self._restore_state(dev)
            self._states[dev.id] = state
            self._devices[dev.id] = dev
            self._last_rain_ended[dev.id] = self._datetime_state(
                dev, "lastRainEnded"
            )
            self._last_rain_detected[dev.id] = self._datetime_state(
                dev, "lastRainDetected"
            )
            self._days_counter_day[dev.id] = self._date_state(
                dev, "daysCounterDay"
            )
            if state.advance(datetime.now()):
                self._last_rain_ended[dev.id] = state.last_detection
            self._publish(dev, state, datetime.now(), force=True)
            self._update_days_since_last_rain(dev.id, datetime.now())

    def deviceStopComm(self, dev):
        with self._lock:
            self._states.pop(dev.id, None)
            self._devices.pop(dev.id, None)
            self._last_rain_ended.pop(dev.id, None)
            self._last_rain_detected.pop(dev.id, None)
            self._days_counter_day.pop(dev.id, None)
        super().deviceStopComm(dev)

    def deviceUpdated(self, original_dev, new_dev):
        if original_dev.id not in self._source_ids():
            return
        before = bool(original_dev.states.get("onOffState", False))
        after = bool(new_dev.states.get("onOffState", False))
        if before or not after:
            return

        now = datetime.now()
        with self._lock:
            for device_id, state in self._states.items():
                dev = self._devices.get(device_id)
                if dev is None or self._source_id(dev) != new_dev.id:
                    continue
                confirmed = state.detection(now)
                if confirmed:
                    self._last_rain_detected[device_id] = now
                    self.logger.info("Rain confirmed for %s", dev.name)
                self._publish(dev, state, now, force=True)
                self._update_days_since_last_rain(
                    device_id, now, reset=confirmed
                )

    def validateDeviceConfigUi(self, values_dict, type_id, dev_id):
        errors = indigo.Dict()
        for key, label, minimum, maximum in (
            ("confirmationWindowSeconds", "Confirmation window", 1, 3600),
            ("dryPeriodSeconds", "Dry period", 1, 86400),
        ):
            try:
                value = int(str(values_dict.get(key, "60")))
                if not minimum <= value <= maximum:
                    raise ValueError
                values_dict[key] = str(value)
            except (TypeError, ValueError):
                errors[key] = f"{label} must be {minimum}-{maximum} seconds"

        try:
            source_id = int(str(values_dict.get("sourceDeviceId", "")))
            if source_id <= 0 or source_id not in indigo.devices:
                raise ValueError
            values_dict["sourceDeviceId"] = str(source_id)
        except (TypeError, ValueError):
            errors["sourceDeviceId"] = "Enter the ID of an existing Indigo device"

        if errors:
            errors["showAlertText"] = "Please correct the highlighted settings."
            return False, values_dict, errors
        return True, values_dict

    def availableSourceDevices(self, filter="", valuesDict=None, typeId="", targetId=0):
        available = []
        for device in indigo.devices:
            if device.id == targetId:
                continue
            if "onOffState" in getattr(device, "states", {}):
                available.append((str(device.id), device.name))
        return sorted(available, key=lambda item: item[1].casefold())

    def actionControlDevice(self, action, dev):
        self.logger.warning("%s is controlled automatically by RG-9 detections", dev.name)

    def _restore_state(self, dev):
        now = datetime.now()
        day_key = str(dev.states.get("dayKey", ""))
        if day_key != now.date().isoformat():
            day_key = now.date().isoformat()
            accumulated = 0
            detections = 0
            candidate_at = None
            raining_since = None
            last_detection = None
        else:
            accumulated = self._number_state(dev, "accumulatedSeconds", 0)
            detections = self._number_state(dev, "detectionsToday", 0)
            candidate_at = self._datetime_state(dev, "candidateAt")
            raining_since = self._datetime_state(dev, "rainingSince")
            last_detection = self._datetime_state(dev, "lastDetection")
        return RainState(
            confirmation_seconds=self._setting(dev, "confirmationWindowSeconds", 60),
            dry_seconds=self._setting(dev, "dryPeriodSeconds", 60),
            day_key=day_key,
            accumulated_seconds=accumulated,
            detections_today=int(detections),
            candidate_at=candidate_at,
            raining_since=raining_since,
            last_detection=last_detection,
        )

    def _publish(self, dev, state, now, force=False):
        seconds = state.total_seconds(now)
        status = "Raining" if state.is_raining else (
            "Waiting for confirmation" if state.candidate_at else "Dry"
        )
        last_detection = (
            state.last_detection.strftime("%Y-%m-%d %H:%M:%S")
            if state.last_detection else "Never"
        )
        values = {
            "onOffState": state.is_raining,
            "rainfallTodaySeconds": seconds,
            "rainfallToday": format_duration(seconds),
            "lastDetection": last_detection,
            "lastRainEnded": self._format_datetime(
                self._last_rain_ended.get(dev.id)
            ) or "Never",
            "detectionsToday": state.detections_today,
            "status": status,
            "dayKey": state.day_key,
            "accumulatedSeconds": int(state.accumulated_seconds),
            "candidateAt": self._format_datetime(state.candidate_at),
            "rainingSince": self._format_datetime(state.raining_since),
            "lastRainDetected": self._format_datetime(
                self._last_rain_detected.get(dev.id)
            ),
            "daysCounterDay": self._format_date(
                self._days_counter_day.get(dev.id)
            ),
        }
        updates = []
        for key, value in values.items():
            if force or dev.states.get(key) != value:
                updates.append({"key": key, "value": value})
        if updates:
            dev.updateStatesOnServer(updates)

    def _update_days_since_last_rain(self, device_id, now, reset=False):
        try:
            variable = indigo.variables[DAYS_SINCE_LAST_RAIN_VARIABLE_ID]
        except (IndexError, KeyError, TypeError):
            if not self._missing_variable_logged:
                self.logger.error(
                    "Indigo variable %s (daysSinceLastRain) was not found",
                    DAYS_SINCE_LAST_RAIN_VARIABLE_ID,
                )
                self._missing_variable_logged = True
            return
        self._missing_variable_logged = False
        today = now.date()
        if reset:
            if str(variable.value) != "0":
                indigo.variable.updateValue(variable.id, value="0")
            self._days_counter_day[device_id] = today
            return
        counter_day = self._days_counter_day.get(device_id)
        if counter_day is None:
            self._days_counter_day[device_id] = today
            return
        elapsed_days = (today - counter_day).days
        state = self._states.get(device_id)
        if elapsed_days <= 0:
            return
        try:
            current_value = int(str(variable.value))
        except (TypeError, ValueError):
            current_value = 0
        new_value = 0 if state is not None and state.is_raining else (
            max(0, current_value) + elapsed_days
        )
        if str(variable.value) != str(new_value):
            indigo.variable.updateValue(variable.id, value=str(new_value))
        self._days_counter_day[device_id] = today

    def _source_ids(self):
        return {self._source_id(dev) for dev in self._devices.values()}

    def _source_id(self, dev):
        return self._setting(dev, "sourceDeviceId", 0)

    @staticmethod
    def _setting(dev, key, default):
        try:
            return int(str(dev.pluginProps.get(key, default)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _number_state(dev, key, default):
        try:
            return float(dev.states.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _format_datetime(value):
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""

    @staticmethod
    def _format_date(value):
        return value.isoformat() if value else ""

    @staticmethod
    def _datetime_state(dev, key):
        value = str(dev.states.get(key, "")).strip()
        if not value or value == "Never":
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    @staticmethod
    def _date_state(dev, key):
        value = str(dev.states.get(key, "")).strip()
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
