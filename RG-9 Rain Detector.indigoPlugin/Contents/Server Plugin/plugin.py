"""Indigo plugin for debouncing Hydreon RG-9 rain detections."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

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
        self.debug = self._as_bool(plugin_prefs.get("showDebugInfo", False))

    def startup(self):
        indigo.devices.subscribeToChanges()
        self.logger.info("RG-9 Rain Detector plugin started")
        self._debug_log("Debug logging enabled")

    def shutdown(self):
        self.logger.info("RG-9 Rain Detector plugin stopped")

    def runConcurrentThread(self):
        try:
            while True:
                now = datetime.now()
                with self._lock:
                    for device_id, state in list(self._states.items()):
                        candidate_before = state.candidate_at
                        confirmed = state.confirm_sustained_high(now)
                        ended = state.advance(now)
                        dev = self._devices.get(device_id)
                        if dev is not None:
                            if confirmed:
                                self._last_rain_detected[device_id] = now
                                self.logger.info("Rain confirmed for %s", dev.name)
                                self._debug_log(
                                    "%s confirmed by continuous On input after %ss",
                                    dev.name,
                                    state.minimum_high_seconds,
                                )
                            elif (
                                candidate_before is not None
                                and state.candidate_at is None
                                and not state.is_raining
                            ):
                                self._debug_log(
                                    "%s candidate expired after %ss without a second detection",
                                    dev.name,
                                    state.second_detection_seconds,
                                )
                            if ended:
                                self._last_rain_ended[device_id] = state.rain_ended_at
                                self.logger.info("Rain ended for %s", dev.name)
                                self._debug_log(
                                    "%s rain ended at %s; today total is %ss",
                                    dev.name,
                                    self._format_datetime(state.rain_ended_at),
                                    state.total_seconds(now),
                                )
                            self._publish(dev, state, now)
                            self._update_days_since_last_rain(
                                device_id, now, reset=confirmed
                            )
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
                self._last_rain_ended[dev.id] = state.rain_ended_at
            self._publish(dev, state, datetime.now(), force=True)
            self._update_days_since_last_rain(dev.id, datetime.now())
            self._debug_log(
                "%s restored: source=%s, status=%s, second-window=%ss, minimum-On=%ss, dry=%ss",
                dev.name,
                "On" if state.source_high else "Off",
                "Raining" if state.is_raining else "Dry",
                state.second_detection_seconds,
                state.minimum_high_seconds,
                state.dry_seconds,
            )

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
        if before == after:
            return

        now = datetime.now()
        with self._lock:
            for device_id, state in self._states.items():
                dev = self._devices.get(device_id)
                if dev is None or self._source_id(dev) != new_dev.id:
                    continue
                was_raining = state.is_raining
                candidate_before = state.candidate_at
                self._debug_log(
                    "%s source changed %s -> %s",
                    dev.name,
                    "On" if before else "Off",
                    "On" if after else "Off",
                )
                confirmed = state.input_changed(after, now)
                rain_detection = after and (confirmed or was_raining)
                if rain_detection:
                    self._last_rain_detected[device_id] = now
                if confirmed:
                    self.logger.info("Rain confirmed for %s", dev.name)
                    reason = "continuous On input" if not after else "second detection"
                    self._debug_log("%s confirmed by %s", dev.name, reason)
                elif (
                    after
                    and candidate_before is None
                    and state.candidate_at is not None
                ):
                    self._debug_log(
                        "%s candidate started; waiting up to %ss for a second detection or %ss continuous On",
                        dev.name,
                        state.second_detection_seconds,
                        state.minimum_high_seconds,
                    )
                self._publish(dev, state, now, force=True)
                self._update_days_since_last_rain(
                    device_id, now, reset=rain_detection
                )

    def validateDeviceConfigUi(self, values_dict, type_id, dev_id):
        errors = indigo.Dict()
        for key, label, minimum, maximum in (
            ("secondDetectionWindowSeconds", "Second detection window", 1, 3600),
            ("minimumDetectionDurationSeconds", "Minimum continuous detection", 1, 3600),
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

    def closedPrefsConfigUi(self, values_dict, user_cancelled):
        if not user_cancelled:
            self.debug = self._as_bool(values_dict.get("showDebugInfo", False))
            self.logger.info(
                "Debug logging %s", "enabled" if self.debug else "disabled"
            )

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
            candidate_off_at = None
            high_since = None
            low_since = None
        else:
            accumulated = self._number_state(dev, "accumulatedSeconds", 0)
            detections = self._number_state(dev, "detectionsToday", 0)
            candidate_at = self._datetime_state(dev, "candidateAt")
            raining_since = self._datetime_state(dev, "rainingSince")
            last_detection = self._datetime_state(dev, "lastDetection")
            candidate_off_at = self._datetime_state(dev, "candidateOffAt")
            high_since = self._datetime_state(dev, "highSince")
            low_since = self._datetime_state(dev, "lowSince")
        source_high = self._source_is_high(dev)
        if source_high and high_since is None:
            high_since = now
        if not source_high and low_since is None:
            low_since = last_detection
        return RainState(
            second_detection_seconds=self._setting(
                dev,
                "secondDetectionWindowSeconds",
                self._setting(dev, "confirmationWindowSeconds", 60),
            ),
            minimum_high_seconds=self._setting(
                dev,
                "minimumDetectionDurationSeconds",
                self._setting(dev, "confirmationWindowSeconds", 60),
            ),
            dry_seconds=self._setting(dev, "dryPeriodSeconds", 60),
            day_key=day_key,
            accumulated_seconds=accumulated,
            detections_today=int(detections),
            candidate_at=candidate_at,
            raining_since=raining_since,
            last_detection=last_detection,
            candidate_off_at=candidate_off_at,
            source_high=source_high,
            high_since=high_since,
            low_since=low_since,
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
            "candidateOffAt": self._format_datetime(state.candidate_off_at),
            "rainingSince": self._format_datetime(state.raining_since),
            "sourceHigh": state.source_high,
            "highSince": self._format_datetime(state.high_since),
            "lowSince": self._format_datetime(state.low_since),
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
                update = {"key": key, "value": value}
                if key == "onOffState":
                    update["uiValue"] = "Raining" if value else "Dry"
                updates.append(update)
        if updates:
            dev.updateStatesOnServer(updates)
        if force or dev.states.get("onOffState") != state.is_raining:
            image = (
                indigo.kStateImageSel.SensorOn
                if state.is_raining
                else indigo.kStateImageSel.SensorOff
            )
            dev.updateStateImageOnServer(image)

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
        if elapsed_days <= 0:
            return
        try:
            current_value = int(str(variable.value))
        except (TypeError, ValueError):
            current_value = 0
        rain_day = self._last_rain_detected.get(device_id)
        rain_date = rain_day.date() if rain_day is not None else None
        rain_free_days = sum(
            1
            for offset in range(elapsed_days)
            if (counter_day + timedelta(days=offset)) != rain_date
        )
        new_value = max(0, current_value) + rain_free_days
        if str(variable.value) != str(new_value):
            indigo.variable.updateValue(variable.id, value=str(new_value))
        self._days_counter_day[device_id] = today

    def _source_ids(self):
        return {self._source_id(dev) for dev in self._devices.values()}

    def _source_id(self, dev):
        return self._setting(dev, "sourceDeviceId", 0)

    def _source_is_high(self, dev):
        try:
            source = indigo.devices[self._source_id(dev)]
            return bool(source.states.get("onOffState", False))
        except (IndexError, KeyError, TypeError, AttributeError):
            return False

    def _debug_log(self, message, *args):
        if self.debug:
            self.logger.info("Debug: " + message, *args)

    @staticmethod
    def _as_bool(value):
        return value is True or str(value).strip().lower() in (
            "1", "true", "yes", "on"
        )

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
