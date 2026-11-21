// Optional: copy to config.h (same folder) to change the defaults in settings.h.
// config.h is gitignored, so your WiFi password never ends up in git.
// Valikuline: kopeeri config.h-ks, et muuta settings.h vaikeväärtusi.
#pragma once

// ---- Mode / režiim -----------------------------------------------------------
// 0 = voice control (default) / häälkäsklused (vaikimisi)
// 1 = WiFi microphone for the data collector / koguja WiFi mikrofon
// #define COLLECTOR_MODE 1

// ---- Collector mode only: WiFi + server / ainult koguja režiimis ------------------
// #define WIFI_SSID      "my-wifi"        // 2.4 GHz only
// #define WIFI_PASSWORD  "my-password"
// #define SERVER_HOST    "192.168.1.10"   // PC running `python app.py --host 0.0.0.0` (it prints its IP)
// #define SERVER_PORT    5000

// ---- LED strip ----------------------------------------------------------------
// #define LED_MAX_BRIGHTNESS 255   // lower it (e.g. 80) if your 12 V adapter is below ~6 A for 5 m of strip
// #define LED_ACTIVE_LOW     1     // 1 = NPN level shifter board (GPIO LOW = on); 0 = MOSFET gate driven directly
// #define LED_BRIGHTNESS     24    // collector mode status colours, 0 = dark

// ---- Voice control tuning -------------------------------------------------------
// #define KWS_THRESHOLD      0.80f // higher = fewer false triggers, but commands need to be clearer
// #define KWS_DEBUG          1     // print every inference to the Serial Monitor (115200)
// #define KWS_SELFTEST       1     // at boot, check the features/model against test_clip.h

// ---- Microphone -------------------------------------------------------------------
// #define I2S_MIC_SLOT   I2S_STD_SLOT_RIGHT  // if the level is always 0 (INMP441 L/R on VDD instead of GND)
// #define PIN_I2S_SCK 4
// #define PIN_I2S_WS  3
// #define PIN_I2S_SD  2
// #define PIN_LED_R   9
// #define PIN_LED_G   10
// #define PIN_LED_B   11
