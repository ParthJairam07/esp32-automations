#!/usr/bin/env python3
"""Tests for the USB button protocol. No board and no Mac required."""

from __future__ import annotations

import json
import os
import pty
import tempfile
import termios
import threading
import time
import unittest
from pathlib import Path

import esp32_shortcut_listener as listener


CONFIG = {"shortcut": "Morning", "confirm_sound": False}


class ProtocolTests(unittest.TestCase):
    def test_line_action_ignores_bootloader_text(self) -> None:
        noise = [
            "ets Jul 29 2019 12:21:46",
            "rst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)",
            "configsip: 0, SPIWP:0xee",
            "BOOT",
            "",
        ]
        for line in noise:
            self.assertIsNone(listener.line_action(line))
        self.assertEqual(listener.line_action("READY esp32-shortcut-button v1 pin=0"), "ready")
        self.assertEqual(listener.line_action("ARMED"), "armed")
        self.assertEqual(listener.line_action("PING"), "ping")
        self.assertEqual(listener.line_action("BTN"), "button")

    def test_drain_lines_splits_partial_chunks(self) -> None:
        buffer, lines = listener.drain_lines(b"", b"ets Jul 29\r\nREA")
        self.assertEqual(lines, ["ets Jul 29"])
        buffer, lines = listener.drain_lines(buffer, b"DY esp32-shortcut-button v1 pin=0\nBTN\n")
        self.assertEqual(lines, ["READY esp32-shortcut-button v1 pin=0", "BTN"])
        self.assertEqual(buffer, b"")

    def test_drain_lines_caps_a_runaway_buffer(self) -> None:
        buffer, lines = listener.drain_lines(b"", b"x" * 5000)
        self.assertEqual(lines, [])
        self.assertLessEqual(len(buffer), 1024)

    def test_candidate_ports(self) -> None:
        self.assertTrue(listener.is_candidate_port("/dev/cu.usbserial-0001"))
        self.assertTrue(listener.is_candidate_port("/dev/cu.wchusbserial5A460752761"))
        self.assertTrue(listener.is_candidate_port("/dev/cu.usbmodem1101"))
        self.assertTrue(listener.is_candidate_port("/dev/cu.SLAB_USBtoUART"))
        self.assertFalse(listener.is_candidate_port("/dev/cu.Bluetooth-Incoming-Port"))
        self.assertFalse(listener.is_candidate_port("/dev/tty.usbserial-0001"))
        self.assertFalse(listener.is_candidate_port("/dev/cu.debug-console"))

    def test_press_cooldown(self) -> None:
        self.assertTrue(listener.accept_press(10.0, None))
        self.assertFalse(listener.accept_press(10.2, 10.0))
        self.assertTrue(listener.accept_press(11.6, 10.0))

    def test_startup_press_is_ignored_until_armed(self) -> None:
        session = listener.BoardSession()
        calls: list[str] = []
        original = listener.run_shortcut
        listener.run_shortcut = lambda name: calls.append(name)
        try:
            now = 50.0
            session.handle("READY esp32-shortcut-button v1 pin=0", CONFIG, now)
            session.handle("BTN", CONFIG, now + 0.2)
            self.assertEqual(calls, [])
            self.assertEqual(session.accepted_presses, 0)
            session.handle("ARMED", CONFIG, now + 1.3)
            self.assertTrue(session.button_live)
            session.handle("BTN", CONFIG, now + 1.4)
            session.handle("BTN", CONFIG, now + 1.5)
            session.handle("BTN", CONFIG, now + 4.0)
            self.assertEqual(calls, ["Morning", "Morning"])
            self.assertEqual(session.accepted_presses, 2)
        finally:
            listener.run_shortcut = original

    def test_ping_means_the_button_is_already_live(self) -> None:
        session = listener.BoardSession(dry_run=True)
        session.handle("PING", CONFIG, 20.0)
        session.handle("BTN", CONFIG, 21.0)
        self.assertEqual(session.accepted_presses, 1)

    def test_chunk_holds_a_press_that_arrives_before_ready(self) -> None:
        session = listener.BoardSession(dry_run=True)
        _buffer, identified = listener.process_chunk(
            b"",
            b"rst:0x1 boot:0x13\nBTN\nREADY esp32-shortcut-button v1 pin=9\n",
            session,
            CONFIG,
            10.0,
            False,
        )
        self.assertTrue(identified)
        self.assertEqual(session.accepted_presses, 0)
        _buffer, identified = listener.process_chunk(
            b"",
            b"ARMED\nBTN\n",
            session,
            CONFIG,
            12.0,
            True,
        )
        self.assertTrue(identified)
        self.assertEqual(session.accepted_presses, 1)

    def test_button_only_chunk_counts_once_the_board_is_known(self) -> None:
        session = listener.BoardSession(dry_run=True)
        session.button_live = True
        _buffer, identified = listener.process_chunk(b"", b"BTN\n", session, CONFIG, 30.0, True)
        self.assertTrue(identified)
        self.assertEqual(session.accepted_presses, 1)

    def test_load_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"shortcut": " Morning "}), encoding="utf-8")
            loaded = listener.load_config(path)
            self.assertEqual(loaded["shortcut"], "Morning")
            self.assertTrue(loaded["confirm_sound"])
            path.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                listener.load_config(path)

    def test_resolve_config_cli_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            resolved = listener.resolve_config(path, "Desk")
            self.assertEqual(resolved["shortcut"], "Desk")

    def test_open_board_reads_the_banner_from_a_pty(self) -> None:
        master, slave = pty.openpty()
        slave_name = os.ttyname(slave)

        def write_banner() -> None:
            time.sleep(0.2)
            os.write(master, b"READY esp32-shortcut-button v1 pin=0\nARMED\nBTN\n")

        thread = threading.Thread(target=write_banner)
        thread.start()
        found = None
        try:
            found = listener.open_board(
                [slave_name],
                CONFIG,
                identify_timeout=2.0,
                dry_run=True,
            )
            self.assertIsNotNone(found)
            assert found is not None
            fd, path, _buffer, session = found
            self.assertEqual(path, slave_name)
            self.assertTrue(session.button_live)
            self.assertEqual(session.accepted_presses, 1)
            os.close(fd)
        finally:
            thread.join(timeout=2)
            os.close(master)
            os.close(slave)

    def test_configure_port_accepts_a_pty(self) -> None:
        master, slave = pty.openpty()
        try:
            listener.configure_port(slave)
        except termios.error as exc:
            self.skipTest(f"pty rejected serial settings: {exc}")
        finally:
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
