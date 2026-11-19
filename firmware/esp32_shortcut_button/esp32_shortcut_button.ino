// Press the BOOT button already on the board and this sketch sends one
// line over the USB serial port. A listener on the Mac runs a Shortcut.
//
// BOOT is wired to a GPIO on the dev board, so no extra parts are needed.
// The EN / RST button restarts the board. BOOT (sometimes labeled 0) is
// the button that runs the shortcut.

#include <Arduino.h>
#include <stdio.h>

#if defined(CONFIG_IDF_TARGET_ESP32C3) || defined(CONFIG_IDF_TARGET_ESP32C6) || \
    defined(CONFIG_IDF_TARGET_ESP32H2)
// BOOT on Espressif C3, C6, and H2 dev boards.
static const int BUTTON_PIN = 9;
#else
// BOOT on ESP32 and ESP32-S3 dev boards. Change this if your silkscreen
// button is on a different GPIO.
static const int BUTTON_PIN = 0;
#endif

#if defined(CONFIG_IDF_TARGET_ESP32)
static const int LED_PIN = 2;
static const bool HAS_LED = true;
#else
static const int LED_PIN = -1;
static const bool HAS_LED = false;
#endif

static const unsigned long DEBOUNCE_MS = 40;
static const unsigned long COOLDOWN_MS = 500;
static const unsigned long ARM_DELAY_MS = 1200;
static const unsigned long PING_MS = 3000;

static bool lastReading = HIGH;
static bool stable = HIGH;
static bool armed = false;
static unsigned long lastChange = 0;
static unsigned long lastPress = 0;
static unsigned long lastPing = 0;

static void emit(const char *line) {
  Serial.println(line);
  Serial.flush();
#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  Serial0.println(line);
  Serial0.flush();
#endif
}

static void blinkTimes(int times) {
  if (!HAS_LED) {
    return;
  }
  for (int i = 0; i < times; i++) {
    digitalWrite(LED_PIN, HIGH);
    delay(80);
    digitalWrite(LED_PIN, LOW);
    delay(80);
  }
}

void setup() {
  if (HAS_LED) {
    digitalWrite(LED_PIN, LOW);
    pinMode(LED_PIN, OUTPUT);
  }
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  Serial.begin(115200);
#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxTimeoutMs(50);
  Serial0.begin(115200);
  unsigned long waitStart = millis();
  while (!Serial && millis() - waitStart < 2000) {
    delay(10);
  }
#else
  delay(200);
#endif

  char ready[56];
  snprintf(ready, sizeof(ready), "READY esp32-shortcut-button v1 pin=%d", BUTTON_PIN);
  emit(ready);
  lastPing = millis();
}

void loop() {
  unsigned long now = millis();

  if (!armed && now >= ARM_DELAY_MS) {
    armed = true;
    stable = digitalRead(BUTTON_PIN);
    lastReading = stable;
    lastChange = now;
    emit("ARMED");
    blinkTimes(2);
  }

  if (now - lastPing >= PING_MS) {
    lastPing = now;
    emit("PING");
  }

  if (!armed) {
    return;
  }

  bool reading = digitalRead(BUTTON_PIN);
  if (reading != lastReading) {
    lastChange = now;
    lastReading = reading;
  }

  if ((now - lastChange) >= DEBOUNCE_MS && reading != stable) {
    stable = reading;
    if (stable == LOW && (now - lastPress) >= COOLDOWN_MS) {
      lastPress = now;
      emit("BTN");
      blinkTimes(1);
    }
  }
}
