# Indigo RG-9 Rain Detector

An Indigo plugin that turns short Hydreon RG-9 detections into a reliable
"raining" state while rejecting isolated false detections.

The RG-9 is connected through a Phidgets digital input represented by an
Indigo device. Each rising edge of that device's `onOffState` is treated as one
detection.

## Behaviour

- The first detection starts a confirmation window.
- A second detection within that window confirms rain.
- Once confirmed, the plugin device remains On until the configured dry period
  has elapsed without another detection.
- Confirmed rainfall time is measured from the first detection through the last
  detection. The dry period delays switching the detector Off, but is not added
  to rainfall time.
- Rainfall time and detection count reset at local midnight. An event spanning
  midnight remains On, while the new day's duration starts from midnight.
- `lastRainEnded` records the final drop time for the most recently completed
  confirmed event.
- Indigo variable `daysSinceLastRain` (ID `1208422529`) resets to `0` when rain
  is confirmed and then tracks local calendar days since that confirmation.

Defaults:

- Confirmation window: 60 seconds
- Dry period: 60 seconds

All three values are configured on the plugin's **RG-9 rain detector** device.
The source is selected by name from Indigo devices that expose an
`onOffState`; it is not hard-coded by the plugin.
For the original installation, select **CM-Rain sensor** (device ID
`924647097`).

## Installation

1. Download or clone this repository on the Indigo server Mac.
2. Double-click `RG-9 Rain Detector.indigoPlugin` to install it.
3. In Indigo, enable **RG-9 Rain Detector**.
4. Create an **RG-9 rain detector** device and verify its source device ID and
   timing settings.

## Device states

- `onOffState`: On while rain is confirmed
- `rainfallTodaySeconds`: accumulated rainfall time today
- `rainfallToday`: the same duration formatted as `HH:MM:SS`
- `lastDetection`: most recent RG-9 detection
- `lastRainEnded`: final drop time of the last completed rain event
- `detectionsToday`: rising edges received today
- `status`: `Dry`, `Waiting for confirmation`, or `Raining`

## Validation

Run the Indigo-independent tests with:

```sh
python3 -m unittest discover -s tests -v
```

These tests validate the state machine and mocked Indigo callbacks. They do not
replace testing with the real Phidgets device and Indigo server.
