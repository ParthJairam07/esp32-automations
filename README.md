# ESP32 shortcut button

The ESP32 stays plugged into the Mac through the dock. You open the Mac, press the **BOOT** button already on the board, and the Mac runs one Shortcut.

Nothing else is wired up. BOOT is the small button on the dev board (on some boards it is labeled **0**). The other button, **EN** or **RST**, restarts the board.

The sketch sends a line over the USB cable. A small program on the Mac reads that line and runs `shortcuts run`.

## 1. Install the listener on the Mac

The Shortcut in the Shortcuts app has to be named **Daddy**. That is the name the installer uses.

```bash
./mac/install.sh
```

That starts the listener at login and points it at Daddy. The Mac needs to be awake and unlocked when you press the button. To use a different Shortcut, pass the exact name: `./mac/install.sh "Other Name"`. `shortcuts list` shows every name.

python3 is required (`brew install python` if `python3` is missing). macOS Monterey or later has the `shortcuts` command.

Run `./mac/install.sh` again after you rename the Shortcut.

## 2. Flash the ESP32 once

Use the same USB-C cable and the same dock port. The cable has to carry data, so the board shows up as a serial port.

1. Install [Arduino IDE](https://www.arduino.cc/en/software).
2. Arduino IDE → Settings → Additional boards manager URLs. Add:

   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`

3. Boards Manager → install **esp32** by Espressif Systems.
4. Open `firmware/esp32_shortcut_button/esp32_shortcut_button.ino`.
5. Tools → Board, based on the chip printed on the metal can:
   - **ESP32-S3** → ESP32S3 Dev Module, and Tools → USB CDC On Boot → **Enabled**
   - **ESP32-C3** → ESP32C3 Dev Module, and USB CDC On Boot → **Enabled**
   - **ESP32-C6** → ESP32C6 Dev Module, and USB CDC On Boot → **Enabled**
   - anything else → ESP32 Dev Module
6. Tools → Port → the port that appears when the board is plugged in (`usbserial`, `wchusbserial`, `usbmodem`, or `SLAB_USBtoUART`).
7. Upload.

If upload fails, hold **BOOT**, tap **EN/RST**, release **BOOT**, and upload again. An S3 board with two USB-C ports can be flashed from either one; use the port that shows up in the Port menu.

Stop the listener before uploading a new sketch, or the listener holds the serial port:

```bash
./mac/uninstall.sh
```

Run `./mac/install.sh` again after the upload.

## 3. Morning

Open the Mac and wait until the desktop is up. The dock brings the USB port back a moment after wake. Then press **BOOT** once.

On a classic ESP32 dev board the onboard LED blinks twice when the button is live, and once when the press is sent. A short pop sound means the Mac saw the press. Then the Shortcut runs.

## If the button does nothing

Check the connection:

```bash
python3 "$HOME/Library/Application Support/esp32-automations/esp32_shortcut_listener.py" --doctor
```

The log is `~/Library/Logs/esp32-automations.log`.

- `waiting for the ESP32 on USB` means the Mac does not see the board. Reseat the cable in the dock, and use a data cable.
- `still waiting for the button sketch` means the board is there and this sketch is not on it yet.
- `shortcut failed` means the Shortcuts app has no shortcut named Daddy. Rename it to Daddy, or run `./mac/install.sh` with the name from `shortcuts list`.
- The READY line in the log includes `pin=0` or `pin=9`. That is the GPIO this build treats as BOOT. If a press still does nothing, change `BUTTON_PIN` at the top of the sketch to the GPIO your board's BOOT button uses, then flash again.

A press in the first second after the board powers up is ignored so plugging the dock in does not run the Shortcut by itself.

## Files

| Path | What it is |
| --- | --- |
| `firmware/esp32_shortcut_button/esp32_shortcut_button.ino` | Sketch for the board |
| `mac/esp32_shortcut_listener.py` | Reads the USB serial port and runs the Shortcut |
| `mac/install.sh` | Installs the listener for your Mac user |
| `mac/uninstall.sh` | Stops the listener |

The saved name lives in `~/Library/Application Support/esp32-automations/config.json`. Set `"confirm_sound": false` there if you want the press to stay quiet.
