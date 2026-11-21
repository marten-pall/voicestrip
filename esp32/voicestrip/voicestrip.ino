// voicestrip - voice-controlled RGB LED strip on an ESP32-S3 + INMP441 microphone.
//
// Two modes (config.h, see config.example.h):
//   COLLECTOR_MODE 0 (default)  voice control: recognises Estonian commands on the chip and
//                               drives the strip. Needs features.h + model_data.h from
//                               trainer/export.py (the published model is already here).
//   COLLECTOR_MODE 1            WiFi microphone for the data collector (Data/app.py).
//
// Toolchain: Arduino IDE + "esp32" by Espressif 3.x, board "ESP32S3 Dev Module",
// PSRAM "OPI PSRAM". Setup and wiring: README.md in the repository root.
//
// Files: audio.*      INMP441 capture (I2S) into a ring buffer
//        leds.*       MOSFET board PWM: status colours, light state, effects
//        kws.*        log-mel + TFLite Micro keyword spotting   (COLLECTOR_MODE 0)
//        collector.*  WiFi streaming to the collector          (COLLECTOR_MODE 1)

#include "settings.h"

#include "audio.h"
#include "collector.h"
#include "kws.h"
#include "leds.h"

#if !COLLECTOR_MODE

// What each recognised command does. Unknown labels (e.g. a model trained on new words) are
// only printed.
static void onCommand(const char *label, float confidence) {
  Serial.printf("Käsklus: %s (%.2f)\n", label, confidence);
  if (!strcmp(label, "sisse")) leds::turnOn();
  else if (!strcmp(label, "valja")) leds::turnOff();
  else if (!strcmp(label, "punane")) leds::setColor(255, 0, 0);
  else if (!strcmp(label, "roheline")) leds::setColor(0, 255, 0);
  else if (!strcmp(label, "sinine")) leds::setColor(0, 0, 255);
  else if (!strcmp(label, "valge")) leds::setColor(255, 255, 255);
  else if (!strcmp(label, "hajuv")) leds::startFade();
  else if (!strcmp(label, "vilkuv")) leds::startBlink();
  else if (!strcmp(label, "heledam")) leds::brighter();
  else if (!strcmp(label, "tumedam")) leds::dimmer();
}

#endif

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println("voicestrip " FW_VERSION);
  Serial.printf("INMP441: SCK=%d WS=%d SD=%d · LED R=%d G=%d B=%d (%s)\n", PIN_I2S_SCK, PIN_I2S_WS, PIN_I2S_SD,
                PIN_LED_R, PIN_LED_G, PIN_LED_B, LED_ACTIVE_LOW ? "LOW = põleb" : "HIGH = põleb");

  leds::begin();
  audio::begin();
#if COLLECTOR_MODE
  collector::begin();
#else
  if (!kws::begin(onCommand)) {
    leds::setStatus(leds::Status::Error);
    Serial.println("Häälkäsklused ei käivitunud - vaata ülal olevat viga.");
    for (;;) delay(1000);
  }
  leds::flash(0, 255, 0, 600);  // short green flash: ready, listening
  Serial.println("Kuulan. Ütle nt \"sisse\".");
#endif
}

void loop() {
#if COLLECTOR_MODE
  collector::step();
#else
  kws::step();
#endif
}
