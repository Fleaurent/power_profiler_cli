#!/usr/bin/env python3
"""Capture current measurements from a Nordic Power Profiler Kit II (PPK2).

Writes a CSV with the (optionally averaged) samples and a JSON summary next to it,
and prints the summary as a single JSON line on stdout so other tools/agents can parse it.
"""

import argparse
import csv
import json
import queue
import re
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import serial
import serial.tools.list_ports
from ppk2_api.ppk2_api import PPK2_API

MEASUREMENTS_DIR = Path(__file__).resolve().parent / "measurements"
REGULATOR_SETTLE_S = 0.5  # wait after setting the source voltage before switching the DUT on
PPK2_SAMPLE_RATE = 100_000  # Hz, fixed by the hardware
VOLTAGE_MIN, VOLTAGE_MAX = 800, 5000  # mV, PPK2 regulator range


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def find_ports():
    """Return PPK2 data ports (the CDC ACM interface x.1 of each device)."""
    ports = [p for p in serial.tools.list_ports.comports() if p.product == "PPK2"]
    data = [p for p in ports if (p.location or "").endswith(":1.1")] or ports
    return sorted(data, key=lambda p: p.device)


def fmt_current(ua):
    """Format a current in uA with a readable unit."""
    if abs(ua) >= 1000:
        return f"{ua / 1000:8.3f} mA"
    if abs(ua) >= 1 or ua == 0:
        return f"{ua:8.3f} uA"
    return f"{ua * 1000:8.1f} nA"


class Progress:
    """Live status line on stderr (only on a terminal): elapsed/total time, bar, current."""

    BAR = 20

    def __init__(self, duration, interval=1.0):
        self.duration = duration
        self.interval = interval
        self.enabled = sys.stderr.isatty()
        self.t_start = self.t_last = time.monotonic()
        self.last_sum = 0.0
        self.last_n = 0
        self.now_txt = "       -   "
        self.dut = None  # "on"/"off" when the DUT power is switched during the measurement

    def update(self, i_sum, n_total, force=False):
        now = time.monotonic()
        if not self.enabled or (not force and now - self.t_last < self.interval):
            return
        elapsed = now - self.t_start
        if self.duration > 0:
            elapsed = min(elapsed, self.duration)
            frac = elapsed / self.duration
            done = int(frac * self.BAR)
            head = f"{elapsed:6.1f} / {self.duration:.1f} s [{'#' * done}{'-' * (self.BAR - done)}] {frac:4.0%}"
        else:
            head = f"{elapsed:6.1f} s (Ctrl+C to stop)"
        n = n_total - self.last_n
        if n:
            self.now_txt = fmt_current((i_sum - self.last_sum) / n)
        avg_txt = fmt_current(i_sum / n_total) if n_total else "       -   "
        dut_txt = f"  DUT {self.dut:3}" if self.dut else ""
        sys.stderr.write(f"\r{head}  now {self.now_txt}  avg {avg_txt}{dut_txt} ")
        sys.stderr.flush()
        self.t_last, self.last_sum, self.last_n = now, i_sum, n_total

    def finish(self, i_sum, n_total):
        if self.enabled:
            self.update(i_sum, n_total, force=True)
            sys.stderr.write("\n")
            sys.stderr.flush()


def display_path(path):
    """Path relative to the repo if it is inside it, else absolute."""
    path = path.resolve()
    return str(path.relative_to(MEASUREMENTS_DIR.parent)) if path.is_relative_to(MEASUREMENTS_DIR.parent) else str(path)


def cmd_list(_args):
    ports = find_ports()
    if not ports:
        log("No PPK2 found")
        return 1
    for p in ports:
        print(f"{p.device}\tserial={p.serial_number}\tlocation={p.location}")
    return 0


def drain(ppk2, quiet_s=0.3, timeout_s=3):
    """Discard incoming bytes until the device has been silent for quiet_s."""
    deadline = time.monotonic() + timeout_s
    last_data = time.monotonic()
    while time.monotonic() < deadline and time.monotonic() - last_data < quiet_s:
        if ppk2.ser.in_waiting:
            ppk2.ser.read(ppk2.ser.in_waiting)
            last_data = time.monotonic()
        time.sleep(0.02)


def open_ppk2(port, mode=None, voltage_mv=None):
    if port is None:
        ports = find_ports()
        if not ports:
            raise SystemExit("Error: no PPK2 found (check with `make check-ppk`)")
        port = ports[0].device
    try:
        ppk2 = PPK2_API(port, timeout=1, write_timeout=1, exclusive=True)
    except serial.SerialException as e:
        raise SystemExit(f"Error: cannot open PPK2 {port}, is power-on or another measurement running? ({e})")
    # A previous (aborted) session may have left the device streaming: stop it and flush
    ppk2.stop_measuring()
    drain(ppk2)
    ppk2.get_modifiers()
    if mode == "source":
        ppk2.use_source_meter()
    elif mode == "ampere":
        ppk2.use_ampere_meter()
    if voltage_mv is not None:
        # In ampere mode this is the expected supply voltage of the DUT (used for calibration)
        ppk2.set_source_voltage(voltage_mv)
        if mode == "source":
            # Measured: DUT power ON only takes effect ~315 ms after REGULATOR_SET (1-2 ms after 0.5 s)
            time.sleep(REGULATOR_SETTLE_S)
    return ppk2, port


def cmd_power_on(args):
    # The PPK2 switches the DUT off when its serial port is closed, so power-on keeps
    # the port open (and the DUT powered) until it gets SIGINT/SIGTERM.
    ppk2, port = open_ppk2(args.port, "source", args.voltage)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        ppk2.toggle_DUT_power("ON")
        log(f"PPK2 {port}: DUT power ON ({args.voltage} mV), Ctrl+C to switch off")
        stop.wait()
    finally:
        ppk2.toggle_DUT_power("OFF")
        log(f"PPK2 {port}: DUT power OFF")
    return 0


def cmd_measure(args):
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = out_dir / (f"{stamp}_{args.name}" if args.name else stamp)
    csv_path, json_path = base.parent / (base.name + ".csv"), base.parent / (base.name + ".json")

    window = PPK2_SAMPLE_RATE // args.frequency
    rate = args.frequency

    ppk2, port = open_ppk2(args.port, args.mode, args.voltage)

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    if args.mode == "source":
        if args.settle is not None:
            ppk2.toggle_DUT_power("ON")
            log(f"DUT power ON, settling {args.settle:g} s before recording")
            if stop.wait(args.settle):
                ppk2.toggle_DUT_power("OFF")
                raise SystemExit("Aborted while settling, nothing recorded")
        else:
            ppk2.toggle_DUT_power("OFF")  # measure from a cold start, switched on by power_on below

    # Reader thread only drains the serial port so no data is lost; parsing happens here.
    chunks = queue.Queue()

    def reader():
        while not stop.is_set():
            data = ppk2.get_data()
            if data:
                chunks.put(data)
            else:
                time.sleep(0.001)

    duration_txt = f"{args.duration} s" if args.duration > 0 else "until SIGINT/SIGTERM"
    log(f"PPK2 {port}: {args.mode} mode, {args.voltage} mV, {rate:g} Hz output, {duration_txt}")
    if args.power_on_delay is not None:
        if args.power_on_delay == 0:
            log("DUT power ON with the start of the recording (start-up included)")
        else:
            log(f"DUT power is OFF, switching ON at {args.power_on_delay:g} s")
    log(f"Writing {display_path(csv_path)}")

    started = datetime.now().astimezone()
    ppk2.start_measuring()
    t_start = time.monotonic()
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    progress = Progress(args.duration)

    power_on_at = None
    power_timer = None
    if args.power_on_delay is not None:
        progress.dut = "off"

        def power_on():
            nonlocal power_on_at
            ppk2.toggle_DUT_power("ON")
            power_on_at = time.monotonic() - t_start
            progress.dut = "on"

        power_timer = threading.Timer(args.power_on_delay, power_on)
        power_timer.start()

    n_total = n_rows = 0
    i_sum = 0.0
    i_min, i_max = float("inf"), float("-inf")
    acc_sum, acc_n, acc_logic = 0.0, 0, 0

    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "current_uA", "logic"])

        def consume(buf):
            nonlocal n_total, n_rows, i_sum, i_min, i_max, acc_sum, acc_n, acc_logic
            samples, bits = ppk2.get_samples(buf)
            rows = []
            for i_ua, b in zip(samples, bits):
                n_total += 1
                i_sum += i_ua
                i_min = min(i_min, i_ua)
                i_max = max(i_max, i_ua)
                acc_sum += i_ua
                acc_n += 1
                acc_logic = b
                if acc_n == window:
                    rows.append((f"{n_rows / rate:.6f}", f"{acc_sum / acc_n:.3f}", acc_logic))
                    n_rows += 1
                    acc_sum, acc_n = 0.0, 0
            w.writerows(rows)

        while True:
            if args.duration > 0 and time.monotonic() - t_start >= args.duration:
                stop.set()
            if stop.is_set() and not thread.is_alive():
                break
            try:
                consume(chunks.get(timeout=0.1))
            except queue.Empty:
                pass
            progress.update(i_sum, n_total)
            if stop.is_set():
                thread.join()

        if power_timer:
            power_timer.cancel()
        ppk2.stop_measuring()
        while not chunks.empty():
            consume(chunks.get())
        drain(ppk2)
        progress.finish(i_sum, n_total)

    if args.mode == "source":
        ppk2.toggle_DUT_power("OFF")

    duration_s = n_total / PPK2_SAMPLE_RATE
    avg_ua = i_sum / n_total if n_total else None
    charge_uc = i_sum / PPK2_SAMPLE_RATE  # sum(uA * dt)
    summary = {
        "csv": display_path(csv_path),
        "started": started.isoformat(timespec="seconds"),
        "port": port,
        "mode": args.mode,
        "voltage_mV": args.voltage,
        "duration_s": round(duration_s, 6),
        "samples": n_total,
        "rows": n_rows,
        "frequency_Hz": rate,
        "settle_s": args.settle,
        "power_on_at_s": round(power_on_at, 3) if power_on_at is not None else None,
        "avg_uA": round(avg_ua, 3) if avg_ua is not None else None,
        "min_uA": round(i_min, 3) if n_total else None,
        "max_uA": round(i_max, 3) if n_total else None,
        "charge_uC": round(charge_uc, 3),
        "energy_mJ": round(charge_uc * args.voltage / 1e6, 6),
    }
    json_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    return 0 if n_total else 1


def voltage(text):
    mv = int(text)
    if not VOLTAGE_MIN <= mv <= VOLTAGE_MAX:
        raise argparse.ArgumentTypeError(f"must be {VOLTAGE_MIN}-{VOLTAGE_MAX} mV")
    return mv


def duration(text):
    s = float(text)
    if s < 0:
        raise argparse.ArgumentTypeError("must be >= 0 (0 = until SIGINT/SIGTERM)")
    return s


def delay(text):
    s = float(text)
    if s < 0:
        raise argparse.ArgumentTypeError("must be >= 0 seconds")
    return s


def frequency(text):
    hz = int(text)
    if hz < 1 or hz > PPK2_SAMPLE_RATE or PPK2_SAMPLE_RATE % hz:
        raise argparse.ArgumentTypeError(f"must divide {PPK2_SAMPLE_RATE} evenly, e.g. 1, 10, 100, 1000, 10000, 100000")
    return hz


def name(text):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", text):
        raise argparse.ArgumentTypeError("only letters, digits, '.', '_' and '-' are allowed")
    return text


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", help="serial port of the PPK2 (default: auto-detect)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list connected PPK2 devices").set_defaults(func=cmd_list)

    m = sub.add_parser("measure", help="capture a measurement to CSV + JSON")
    m.add_argument("-m", "--mode", required=True, choices=["source", "ampere"],
                   help="source = PPK2 powers the DUT at --voltage; ampere = PPK2 in series with the DUT's own supply")
    m.add_argument("-v", "--voltage", required=True, type=voltage,
                   help=f"mV, {VOLTAGE_MIN}-{VOLTAGE_MAX}; source: output voltage, ampere: DUT supply voltage (calibration + energy)")
    m.add_argument("-d", "--duration", required=True, type=duration, help="seconds, 0 = until SIGINT/SIGTERM")
    m.add_argument("-f", "--frequency", required=True, type=frequency,
                   help=f"CSV rows per second (averaged), must divide {PPK2_SAMPLE_RATE}; {PPK2_SAMPLE_RATE} = raw")
    m.add_argument("-n", "--name", type=name, help="label appended to the file name")
    m.add_argument("-o", "--out-dir", default=MEASUREMENTS_DIR, help="output directory (default: measurements/ in this repo)")
    m.add_argument("--settle", type=delay, metavar="SECONDS",
                   help="source mode: switch the DUT ON, wait SECONDS unrecorded, then record (steady state, no start-up)")
    m.add_argument("--power-on-delay", type=delay, metavar="SECONDS",
                   help="source mode: start with the DUT OFF and switch it ON SECONDS into the recording (start-up included, default: 0)")
    m.set_defaults(func=cmd_measure)

    on = sub.add_parser("power-on", help="bench supply: power the DUT until SIGINT/SIGTERM")
    on.add_argument("-v", "--voltage", required=True, type=voltage, help=f"output voltage in mV, {VOLTAGE_MIN}-{VOLTAGE_MAX}")
    on.set_defaults(func=cmd_power_on)

    args = p.parse_args()
    if args.cmd == "measure":
        given = [o for o, v in (("--settle", args.settle), ("--power-on-delay", args.power_on_delay)) if v is not None]
        if args.mode == "ampere" and given:
            p.error(f"{given[0]} only works in source mode (the PPK2 must power the DUT)")
        if len(given) > 1:
            p.error("give either --settle or --power-on-delay, not both")
        if args.mode == "source" and not given:
            args.power_on_delay = 0.0  # default: switch on with the recording, start-up included like a normal supply
        if args.power_on_delay is not None and args.duration and args.power_on_delay >= args.duration:
            p.error("--power-on-delay must be smaller than --duration")
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
