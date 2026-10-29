# Nordic Power Profiler Kit II (PPK2) – CLI capture

- [1. Requirements](#1-requirements)
- [2. Installation](#2-installation)
- [3. Usage](#3-usage)
  - [Source mode: start-up or steady state?](#source-mode-start-up-or-steady-state)
  - [Bench supply](#bench-supply)
- [4. Output format](#4-output-format)
- [5. Triggering from other tools / agents](#5-triggering-from-other-tools--agents)

Product page: [Power Profiler Kit II](https://www.nordicsemi.com/Products/Development-hardware/Power-Profiler-Kit-2)  
Python library: [IRNAS/ppk2-api-python](https://github.com/IRNAS/ppk2-api-python)

Capture PPK2 current measurements headless from the command line, using the [Makefile](Makefile) and
[ppk2_capture python api](ppk2_capture.py)  
-> write them to `measurements/` as CSV with a JSON summary.  
**No nRF Connect desktop app needed!**

___

## 1. Requirements

1. `python3` (≥ 3.8) + `python3-venv`  
   -> runs the capture script in a local venv (`.venv/`)
2. `PPK2` connected to USB  
   -> use the USB **DATA/POWER** port. For DUT currents above ~500 mA, also connect the **POWER ONLY** port!


## 2. Installation

**1. Check that a PPK2 is recognized:**

```bash
$ lsusb | grep 1915:c00a
Bus 002 Device 049: ID 1915:c00a Nordic Semiconductor ASA PPK2
$ ls -l /dev/serial/by-id/ | grep PPK2
usb-Nordic_Semiconductor_PPK2_C4286EDC4FF9-if01 -> ../../ttyACM1   # data port (used)
usb-Nordic_Semiconductor_PPK2_C4286EDC4FF9-if03 -> ../../ttyACM2

$ make check-ppk
Bus 002 Device 049: ID 1915:c00a Nordic Semiconductor ASA PPK2
/dev/ttyACM1    serial=C4286EDC4FF9     location=2-1.1:1.1
```

**2. Install the system package and the Python venv:**

```bash
$ make install    # creates .venv/ and installs requirements.txt

$ # if you get a permission error with the serial port, add your user to the dialout group
$ sudo usermod -aG dialout $USER   # then log out and back in
```

## 3. Usage

Specify each measurement with its mode and all parameters explicitly.  
The wrong voltage in source mode can damage the DUT!  
A missing or invalid parameter aborts before the PPK2 is touched!

```bash
$ make help
Makefile targets for the Nordic Power Profiler Kit II (PPK2)
Usage: make [target] [VAR=value ...]

Targets:
  help                  Show this help message
  install               Create the Python venv and install requirements (ppk2-api)
  check-ppk             Check the attached PPK2 (USB 1915:c00a) and list its port(s)
  measure-voltage       Source meter: PPK2 POWERS the DUT at VOLTAGE DURATION FREQUENCY [SETTLE] [POWER_ON_DELAY] [NAME]
  measure-ampere        Ampere meter: PPK2 in series, DUT has its own supply VOLTAGE DURATION FREQUENCY [NAME]
  power-on              power the DUT at VOLTAGE until Ctrl+C
  summary               Show the JSON summary of the latest measurement [NAME]
  clean                 Delete ALL measurements in measurements/

Parameters:
  VOLTAGE=mV        required  800-5000 (integer)
  DURATION=seconds  required  seconds, decimals allowed (e.g. 2.5); 0 = until Ctrl+C / SIGTERM
  FREQUENCY=Hz      required  1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000
  NAME=string       optional  label in the file name, letters/digits/._- only
  POWER_ON_DELAY=s  optional  DUT starts OFF, switched ON s seconds into the recording (< DURATION).
  SETTLE=s          optional  DUT switched ON, runs s seconds UNRECORDED, then recording starts.
  PORT=/dev/ttyACMx optional  PPK2 serial port (default: auto-detect)

Examples:
  make measure-voltage VOLTAGE=3300 DURATION=10 FREQUENCY=1000 NAME=idle
  make measure-voltage VOLTAGE=3300 DURATION=10 FREQUENCY=1000 SETTLE=2 NAME=settled
  make measure-voltage VOLTAGE=3300 DURATION=5 FREQUENCY=10000 POWER_ON_DELAY=1 NAME=boot
  make measure-ampere  VOLTAGE=1800 DURATION=60 FREQUENCY=100 NAME=current
  make power-on VOLTAGE=3300     (bench supply until Ctrl+C)
```

| Target | Mode | Wiring |
|--------|------|--------|
| `measure-voltage` | Source meter: the PPK2 **outputs** `VOLTAGE` on VOUT and measures the current | DUT supplied from PPK2 VOUT/GND |
| `measure-ampere` | Ampere meter: the PPK2 sits in series and outputs **nothing**. VIN → VOUT is only connected **during the measurement** (DUT power switch), before/after the DUT is cut off | external supply → VIN, VOUT → DUT, GND shared |

| Parameter | Required | Valid values | Meaning |
|-----------|----------|--------------|---------|
| `VOLTAGE` | yes | 800–5000 (mV, integer) | `measure-voltage`/`power-on`: output voltage to the DUT. `measure-ampere`: the DUT's supply voltage. It is not output, only used for calibration and `energy_mJ`. |
| `DURATION` | yes | seconds, decimals allowed; `0` | Measurement length. `0` = until `SIGINT`/`SIGTERM` (Ctrl+C / `kill`). |
| `FREQUENCY` | yes | 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000 | CSV rows per second. The PPK2 always samples at 100 kHz, and each row is the mean of 100000/`FREQUENCY` samples. `100000` = raw. |
| `NAME` | no | letters, digits, `.` `_` `-` | Label added to the file name. |
| `POWER_ON_DELAY` | no, `measure-voltage` only, not with `SETTLE` | seconds ≥ 0, < `DURATION` | Start with the DUT **off**, switch it on after this many seconds. The start-up is recorded. |
| `SETTLE` | no, `measure-voltage` only, not with `POWER_ON_DELAY` | seconds ≥ 0 | Switch the DUT on, let it run this many seconds **unrecorded**, then record. Steady state only. |
| `PORT` | no | e.g. `/dev/ttyACM1` | Serial port (default: auto-detect). |

While measuring, a live status line shows elapsed/total time, the current of the last second (`now`) and the running
average (`avg`). It is only drawn when stderr is a terminal, so redirected or piped output stays clean:

```
   4.2 / 10.0 s [########------------]  42%  now    1.523 mA  avg    1.498 mA
```

### Source mode: start-up or steady state?

`measure-voltage` powers the DUT itself, so every measurement is a full power cycle: switch on, record, switch off.
By default the DUT is switched on together with the start of the recording, so the inrush current and the start-up
are in the data, like with a normal power supply. Optionally **one** of these changes that:

| | default | `POWER_ON_DELAY=s` | `SETTLE=s` |
|---|---|---|---|
| DUT at start of recording | switched on (1–2 ms) | off, on after `s` seconds | already on for `s` seconds |
| inrush current / boot in the CSV | **yes** | **yes**, plus off baseline | **no** |
| use for | start-up + operation | clear before/after comparison | idle/sleep current, steady-state averages |

```bash
$ make measure-voltage VOLTAGE=3300 DURATION=10 FREQUENCY=1000                    NAME=run    # with start-up
$ make measure-voltage VOLTAGE=3300 DURATION=5  FREQUENCY=10000 POWER_ON_DELAY=1  NAME=boot   # off baseline first
$ make measure-voltage VOLTAGE=3300 DURATION=10 FREQUENCY=1000  SETTLE=2          NAME=idle   # steady state only
```

The PPK2 can't keep the DUT powered between two commands (see [Bench supply](#bench-supply)). To measure a DUT that has
been running for a while, use `SETTLE`, e.g. `SETTLE=60`.

**Measuring a clean start-up:** with `POWER_ON_DELAY=s`, the measurement starts with the DUT **off** and the PPK2 switches
it on `s` seconds into the measurement. The CSV then contains the off baseline, the inrush current and the complete
boot. The actual switch-on time is stored as `power_on_at_s` in the JSON. That is the time the Pi sent the command, so the
current jump in the CSV is the exact reference.
The status line shows `DUT off`/`DUT on`. Note that `avg_uA`, `charge_uC` and `energy_mJ` include the off phase.
Use a high `FREQUENCY` (e.g. 10000 or 100000) to resolve the inrush current.
Measured with the test DUT, the current starts 1–2 ms after the command.

Before switching on, the script waits 0.5 s after setting the voltage. Measured: right after the voltage is set, the
PPK2 delays the switch-on by ~315 ms, presumably because the regulator is still ramping.

### Bench supply

```bash
$ make power-on VOLTAGE=3300          # DUT powered until Ctrl+C
```

The PPK2 switches the DUT **off as soon as its serial port is closed**. So `power-on` keeps running and holds the port
until Ctrl+C (or SIGTERM). If `power-on` is killed hard (`kill -9`, crash), the port is closed and the PPK2 switches the
DUT off by itself.

While `power-on` runs, no measurement is possible (the port is in use, `measure-*` reports that). For a powered DUT
**with** recording, use `measure-voltage ... DURATION=0`, which runs until Ctrl+C.

## 4. Output format

Each measurement creates two files in the repo's `measurements/` directory, named `YYYYmmdd-HHMMSS[_NAME]`. That is
also the script's default when it is called from another directory. Only the script can write somewhere else
(`--out-dir`). The `csv` path in the JSON is relative to the repo root, or absolute if outside the repo.
`make clean` deletes all measurements in `measurements/` (the venv is kept).

**`…csv`**: one row per averaged sample:

```csv
time_s,current_uA,logic
0.000000,1523.412,0
0.001000,1498.007,1
```

- `time_s`: seconds since the start of the measurement
- `current_uA`: mean current over the averaging window, in µA
- `logic`: state of the 8 digital inputs D0–D7 as a byte (bit *n* = D*n*) at the end of the window

**`…json`**: summary. Statistics are always computed from the full 100 kHz data, whatever `FREQUENCY` is set to:

```json
{
  "csv": "measurements/20261022-212347_check.csv",
  "started": "2026-10-22T21:23:47+02:00",
  "port": "/dev/ttyACM1",
  "mode": "ampere",
  "voltage_mV": 3300,
  "duration_s": 1.9952,
  "samples": 199520,
  "rows": 1995,
  "frequency_Hz": 1000,
  "settle_s": null,
  "power_on_at_s": null,
  "avg_uA": 12.466,
  "min_uA": 1.549,
  "max_uA": 4210.028,
  "charge_uC": 24.87,
  "energy_mJ": 0.082
}
```

`energy_mJ` = charge × `VOLTAGE`, so in ampere mode it is only as accurate as the `VOLTAGE` you pass.
File size: about 19 B per row, so 1 kHz ≈ 70 MB/hour and raw 100 kHz ≈ 2 MB/s.

## 5. Triggering from other tools / agents

The progress log goes to **stderr**. **stdout** gets exactly one JSON line (the summary), and the exit code is non-zero
if no samples were captured:

```bash
# Fixed-length measurement, parse the result
$ make -s measure-voltage VOLTAGE=3300 DURATION=20 FREQUENCY=1000 SETTLE=1 NAME=idle 2>/dev/null | jq .avg_uA

# Open-ended: start in the background, run the workload, stop the capture
$ make -s measure-ampere VOLTAGE=3300 DURATION=0 FREQUENCY=1000 NAME=ota > result.json &
$ ./run_workload.sh
$ kill $!; wait            # SIGTERM -> files are finalized, summary written to result.json
```

When `make` is killed it exits with 143, but the CSV/JSON are complete.  
Call `.venv/bin/python ppk2_capture.py measure -m ampere -v 3300 -d 0 -f 1000` directly if you need a clean exit code `0`.

Hint: Only one process can use the PPK2 at a time (the port is opened exclusively).
