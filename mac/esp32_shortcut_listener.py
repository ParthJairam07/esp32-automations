#!/usr/bin/env python3
"""Run a macOS Shortcut when the ESP32 BOOT button is pressed over USB."""

from __future__ import annotations

import argparse
import glob
import json
import os
import select
import signal
import subprocess
import sys
import termios
import time
from pathlib import Path

CONFIG_DIR = Path.home() / "Library" / "Application Support" / "esp32-automations"
DEFAULT_CONFIG = CONFIG_DIR / "config.json"
PRESS_COOLDOWN_S = 1.5
QUIET_RECONNECT_S = 15.0
IDENTIFY_TIMEOUT_S = 5.0
SHORTCUT_TIMEOUT_S = 180.0
IDENTIFYING = ("ready", "armed", "ping")
PORT_PREFIXES = (
    "cu.usbserial",
    "cu.wchusbserial",
    "cu.usbmodem",
    "cu.SLAB_USBtoUART",
)

stop_requested = False


def request_stop(signum, _frame) -> None:
    del signum
    global stop_requested
    stop_requested = True


def log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{stamp} {message}", file=sys.stderr, flush=True)


def is_candidate_port(path: str) -> bool:
    name = os.path.basename(path)
    return any(name.startswith(prefix) for prefix in PORT_PREFIXES)


def list_candidate_ports() -> list[str]:
    found: list[str] = []
    for pattern in (
        "/dev/cu.usbserial*",
        "/dev/cu.wchusbserial*",
        "/dev/cu.usbmodem*",
        "/dev/cu.SLAB_USBtoUART*",
    ):
        found.extend(glob.glob(pattern))
    return sorted({path for path in found if is_candidate_port(path)})


def line_action(line: str) -> str | None:
    if not line:
        return None
    token = line.split()[0]
    return {
        "READY": "ready",
        "ARMED": "armed",
        "PING": "ping",
        "BTN": "button",
    }.get(token)


def drain_lines(buffer: bytes, chunk: bytes) -> tuple[bytes, list[str]]:
    buffer += chunk
    lines: list[str] = []
    while b"\n" in buffer:
        raw, buffer = buffer.split(b"\n", 1)
        text = raw.decode("utf-8", "replace").strip("\r\x00 ")
        if text:
            lines.append(text)
    if len(buffer) > 4096:
        buffer = buffer[-1024:]
    return buffer, lines


def accept_press(now: float, last_press: float | None, cooldown: float = PRESS_COOLDOWN_S) -> bool:
    if last_press is None:
        return True
    return (now - last_press) >= cooldown


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")
    shortcut = str(data.get("shortcut", "")).strip()
    if not shortcut:
        raise ValueError('config is missing "shortcut"')
    return {
        "shortcut": shortcut,
        "confirm_sound": bool(data.get("confirm_sound", True)),
    }


def resolve_config(path: Path, cli_shortcut: str | None) -> dict:
    loaded = load_config(path) if path.exists() else None
    if cli_shortcut is not None:
        name = cli_shortcut.strip()
        if not name:
            raise ValueError("shortcut name is empty")
        return {
            "shortcut": name,
            "confirm_sound": loaded["confirm_sound"] if loaded else True,
        }
    if loaded is None:
        raise FileNotFoundError(
            f"no config at {path}. Run ./mac/install.sh \"Shortcut Name\" on the Mac."
        )
    return loaded


def process_chunk(
    buffer: bytes,
    chunk: bytes,
    session: BoardSession,
    config: dict | None,
    now: float,
    already_identified: bool,
) -> tuple[bytes, bool]:
    buffer, lines = drain_lines(buffer, chunk)
    identified = already_identified
    for line in lines:
        action = line_action(line)
        if action in IDENTIFYING:
            identified = True
        if identified and action is not None:
            session.handle(line, config, now)
    return buffer, identified


def configure_port(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    cc = attrs[6]
    cc[termios.VMIN] = 0
    cc[termios.VTIME] = 0
    termios.tcsetattr(
        fd,
        termios.TCSANOW,
        [
            0,
            0,
            termios.CS8 | termios.CREAD | termios.CLOCAL,
            0,
            termios.B115200,
            termios.B115200,
            cc,
        ],
    )
    termios.tcflush(fd, termios.TCIOFLUSH)


def lock_port(fd: int) -> None:
    flag = getattr(termios, "TIOCEXCL", None)
    if flag is None:
        return
    try:
        import fcntl

        fcntl.ioctl(fd, flag)
    except OSError as exc:
        log(f"could not lock the serial port: {exc}")


def open_serial(path: str) -> int:
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        configure_port(fd)
        lock_port(fd)
    except Exception:
        os.close(fd)
        raise
    return fd


def play_confirm() -> None:
    sound = "/System/Library/Sounds/Pop.aiff"
    if not os.path.exists(sound):
        return
    subprocess.Popen(
        ["/usr/bin/afplay", sound],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run_shortcut(name: str) -> None:
    log(f"running shortcut: {name}")
    binary = "/usr/bin/shortcuts"
    if not os.path.exists(binary):
        log("the shortcuts command is not on this Mac (it comes with macOS Monterey and later)")
        return
    try:
        completed = subprocess.run(
            [binary, "run", name],
            check=False,
            timeout=SHORTCUT_TIMEOUT_S,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        log(f"shortcut timed out after {int(SHORTCUT_TIMEOUT_S)}s: {name}")
        return
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        log(f"shortcut failed ({completed.returncode}): {detail}")
        return
    log("shortcut finished")


class BoardSession:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.button_live = False
        self.last_press: float | None = None
        self.saw_traffic = False
        self.accepted_presses = 0

    def handle(self, line: str, config: dict | None, now: float) -> None:
        action = line_action(line)
        if action is None:
            return
        self.saw_traffic = True
        if action == "ready":
            log(f"board connected: {line}")
            return
        if action in ("armed", "ping"):
            if not self.button_live:
                self.button_live = True
                log("BOOT button is live")
            return
        if action != "button":
            return
        if not self.button_live:
            log("ignored a press during board startup")
            return
        if not accept_press(now, self.last_press):
            log("ignored a duplicate press")
            return
        if config is None or not config.get("shortcut"):
            log("press received, and no shortcut name is configured")
            return
        self.last_press = now
        self.accepted_presses += 1
        log("BOOT pressed")
        if self.dry_run:
            return
        if config.get("confirm_sound", True):
            play_confirm()
        run_shortcut(str(config["shortcut"]))


def _read_chunk(fd: int, timeout: float) -> bytes | None:
    """Return bytes, b'' on timeout, or None when the port has failed."""
    try:
        ready, _, _ = select.select([fd], [], [], timeout)
    except InterruptedError:
        return b""
    if not ready:
        return b""
    try:
        return os.read(fd, 512)
    except BlockingIOError:
        return b""
    except OSError as exc:
        log(f"serial read failed: {exc}")
        return None


def open_board(
    ports: list[str],
    config: dict | None,
    identify_timeout: float = IDENTIFY_TIMEOUT_S,
    dry_run: bool = False,
) -> tuple[int, str, bytes, BoardSession] | None:
    for path in ports:
        if stop_requested:
            return None
        try:
            fd = open_serial(path)
        except OSError as exc:
            log(f"could not open {path}: {exc}")
            continue
        session = BoardSession(dry_run=dry_run)
        buffer = b""
        identified = False
        deadline = time.monotonic() + identify_timeout
        while time.monotonic() < deadline and not stop_requested:
            remaining = deadline - time.monotonic()
            chunk = _read_chunk(fd, min(0.4, max(remaining, 0.0)))
            if chunk is None:
                break
            if not chunk:
                continue
            buffer, identified = process_chunk(
                buffer,
                chunk,
                session,
                config,
                time.monotonic(),
                identified,
            )
            if identified:
                return fd, path, buffer, session
        os.close(fd)
    return None


def read_loop(
    fd: int,
    path: str,
    buffer: bytes,
    session: BoardSession,
    config_path: Path,
    cli_shortcut: str | None,
    config: dict,
) -> None:
    last_byte = time.monotonic()
    current = config
    while not stop_requested:
        if not os.path.exists(path):
            log(f"{path} disappeared")
            return
        chunk = _read_chunk(fd, 1.0)
        now = time.monotonic()
        if chunk is None:
            return
        if not chunk:
            if session.saw_traffic and (now - last_byte) > QUIET_RECONNECT_S:
                log("no data from the board, reconnecting")
                return
            continue
        last_byte = now
        try:
            current = resolve_config(config_path, cli_shortcut)
        except Exception as exc:
            log(f"config problem, still using {current['shortcut']}: {exc}")
        buffer, _identified = process_chunk(buffer, chunk, session, current, now, True)


def sleep_interruptible(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not stop_requested and time.monotonic() < deadline:
        time.sleep(min(0.2, deadline - time.monotonic()))


def serve(config_path: Path, cli_shortcut: str | None) -> None:
    last_message = None
    while not stop_requested:
        try:
            config = resolve_config(config_path, cli_shortcut)
        except Exception as exc:
            log(str(exc))
            sleep_interruptible(10)
            continue

        ports = list_candidate_ports()
        if not ports:
            message = "waiting for the ESP32 on USB"
            if message != last_message:
                log(message)
                last_message = message
            sleep_interruptible(2)
            continue

        found = open_board(ports, config)
        if found is None:
            message = "USB serial is connected, still waiting for the button sketch"
            if message != last_message:
                log(message)
                last_message = message
            sleep_interruptible(2)
            continue

        fd, path, buffer, session = found
        last_message = None
        log(f"listening on {path}")
        try:
            read_loop(fd, path, buffer, session, config_path, cli_shortcut, config)
        finally:
            os.close(fd)
        sleep_interruptible(0.5)


def doctor(config_path: Path, cli_shortcut: str | None) -> int:
    try:
        config = resolve_config(config_path, cli_shortcut)
    except Exception as exc:
        print(f"Config: {exc}")
        config = None
    else:
        sound = "on" if config["confirm_sound"] else "off"
        print(f"Shortcut: {config['shortcut']}")
        print(f"Confirm sound: {sound}")

    ports = list_candidate_ports()
    if not ports:
        print("No USB serial ports. Plug the ESP32 into the Mac with a data cable.")
        return 1
    print("USB serial ports:")
    for port in ports:
        print(f"  {port}")
    print("Waiting up to 8 seconds for the sketch to say hello...")
    found = open_board(ports, config, identify_timeout=8.0, dry_run=True)
    if found is None:
        print("The board did not answer. Flash the sketch, then run this check again.")
        return 1
    fd, path, _buffer, session = found
    os.close(fd)
    state = "ready for BOOT" if session.button_live else "connected"
    print(f"Board is {state} on {path}.")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a macOS Shortcut when the ESP32 BOOT button is pressed."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--shortcut",
        help="Shortcut name. Overrides the name saved in the config file.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Check the config and the USB connection, then exit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    if args.doctor:
        return doctor(args.config, args.shortcut)
    serve(args.config, args.shortcut)
    return 0


if __name__ == "__main__":
    sys.exit(main())
