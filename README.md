# Nordic Power Profiler Kit II (PPK2) – CLI capture

- [1. Requirements](#1-requirements)
- [2. Installation](#2-installation)
- [3. Usage](#3-usage)
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
Targets:
  help                  Show this help message
  install               Create the Python venv and install requirements (ppk2-api)
  check-ppk             Check the PPK2 (USB 1915:c00a, venv, serial port, permissions) and list its port(s)
  measure-voltage       Source meter: PPK2 POWERS the DUT at VOLTAGE (VOLTAGE DURATION FREQUENCY [NAME])
  measure-ampere        Ampere meter: PPK2 in series, DUT has its own supply (VOLTAGE DURATION FREQUENCY [NAME])
  power-on              Power the DUT from the PPK2 at VOLTAGE mV, no measurement (VOLTAGE)
  power-off             Turn DUT power off
  summary               Show the JSON summary of the latest measurement, or the latest one named NAME ([NAME])
  clean                 Delete ALL measurements in measurements/ (keeps .gitkeep and the venv)

Parameters (required = no default, must be given):
  VOLTAGE=mV        required  800-5000 (integer)
  DURATION=seconds  required  seconds, decimals allowed (e.g. 2.5); 0 = until Ctrl+C / SIGTERM
  FREQUENCY=Hz      required  1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000
  NAME=string       optional  label in the file name, letters/digits/._- only
  PORT=/dev/ttyACMx optional  PPK2 serial port (default: auto-detect)

Examples:
  make measure-voltage VOLTAGE=3300 DURATION=10 FREQUENCY=1000 NAME=idle
  make measure-ampere  VOLTAGE=1800 DURATION=60 FREQUENCY=100
```

| Target | Mode | Wiring |
|--------|------|--------|
| `measure-voltage` | Source meter: the PPK2 **outputs** `VOLTAGE` on VOUT and measures the current | DUT supplied from PPK2 VOUT/GND |
| `measure-ampere` | Ampere meter: the PPK2 sits in series and outputs **nothing** | external supply → VIN, VOUT → DUT, GND shared |

| Parameter | Required | Valid values | Meaning |
|-----------|----------|--------------|---------|
| `VOLTAGE` | yes | 800–5000 (mV, integer) | `measure-voltage`/`power-on`: output voltage to the DUT. `measure-ampere`: the DUT's supply voltage. It is not output, only used for calibration and `energy_mJ`. |
| `DURATION` | yes | seconds, decimals allowed; `0` | Measurement length. `0` = until `SIGINT`/`SIGTERM` (Ctrl+C / `kill`). |
| `FREQUENCY` | yes | divisor of 100000: 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000 | CSV rows per second. The PPK2 always samples at 100 kHz, and each row is the mean of 100000/`FREQUENCY` samples. `100000` = raw. |
| `NAME` | no | letters, digits, `.` `_` `-` | Label added to the file name. |
| `PORT` | no | e.g. `/dev/ttyACM1` | Serial port (default: auto-detect). |

While measuring, a live status line shows elapsed/total time, the current of the last second (`now`) and the running
average (`avg`). It is only drawn when stderr is a terminal, so redirected or piped output stays clean:

```
   4.2 / 10.0 s [########------------]  42%  now    1.523 mA  avg    1.498 mA
```

In **source mode** (`measure-voltage`) the DUT is powered on, the script waits 0.5 s to let it settle (`--settle`), then
measures. Afterwards the DUT is powered **off** again, unless the script is called with `--keep-power`.

The script can also be called directly, with the same required arguments:
`.venv/bin/python ppk2_capture.py measure --mode source --voltage 3300 --duration 10 --frequency 1000`
(see `.venv/bin/python ppk2_capture.py measure --help`).

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
$ make -s measure-voltage VOLTAGE=3300 DURATION=20 FREQUENCY=1000 NAME=boot 2>/dev/null | jq .avg_uA

# Open-ended: start in the background, run the workload, stop the capture
$ make -s measure-ampere VOLTAGE=3300 DURATION=0 FREQUENCY=1000 NAME=ota > result.json &
$ ./run_workload.sh
$ kill $!; wait            # SIGTERM -> files are finalized, summary written to result.json
```

When `make` is killed it exits with 143, but the CSV/JSON are complete.  
Call `.venv/bin/python ppk2_capture.py measure -m ampere -v 3300 -d 0 -f 1000` directly if you need a clean exit code `0`.

Hint: Only one process can use the PPK2 at a time (the port is opened exclusively).
