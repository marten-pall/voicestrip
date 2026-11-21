// Build settings: config.h (optional, your own, gitignored) first, then defaults for
// everything it does not define. Every .cpp of the sketch includes this file.
#pragma once

#include <Arduino.h>

#if __has_include("config.h")
#include "config.h"
#endif

#define FW_VERSION "voicestrip-2.0"

// ---- mode -------------------------------------------------------------------
// 0 = voice control: recognises commands on the ESP32 and drives the LED strip (no WiFi)
// 1 = collector:     WiFi microphone for the data collector (Data/app.py)
#ifndef COLLECTOR_MODE
#define COLLECTOR_MODE 0
#endif

// ---- pins -------------------------------------------------------------------
#ifndef PIN_I2S_SCK
#define PIN_I2S_SCK 4
#endif
#ifndef PIN_I2S_WS
#define PIN_I2S_WS 3
#endif
#ifndef PIN_I2S_SD
#define PIN_I2S_SD 2
#endif
#ifndef PIN_LED_R
#define PIN_LED_R 9
#endif
#ifndef PIN_LED_G
#define PIN_LED_G 10
#endif
#ifndef PIN_LED_B
#define PIN_LED_B 11
#endif

// ---- microphone -------------------------------------------------------------
#ifndef I2S_MIC_SLOT
#define I2S_MIC_SLOT I2S_STD_SLOT_LEFT  // INMP441 L/R pin on GND = left channel
#endif
#ifndef GAIN_SHIFT
#define GAIN_SHIFT 16  // int32 (24-bit sample in the top bits) >> 16; must match how training data was recorded
#endif

// ---- LED strip --------------------------------------------------------------
#ifndef LED_ACTIVE_LOW
#define LED_ACTIVE_LOW 1  // NPN level shifter on the MOSFET board inverts: GPIO LOW = LED on
#endif
#ifndef LED_MAX_BRIGHTNESS
#define LED_MAX_BRIGHTNESS 255  // cap for the whole strip; lower it if the 12 V adapter is weaker than ~6 A
#endif
#ifndef LED_BRIGHTNESS
#define LED_BRIGHTNESS 24  // collector mode: brightness of the status colours (0 = dark)
#endif

// ---- voice control (COLLECTOR_MODE 0) ----------------------------------------
#ifndef KWS_THRESHOLD
#define KWS_THRESHOLD 0.80f  // averaged probability a command needs before it is executed
#endif
#ifndef KWS_AVERAGE
#define KWS_AVERAGE 3  // inferences averaged (one every KWS_HOP_MS)
#endif
#ifndef KWS_HOP_MS
#define KWS_HOP_MS 250  // run the model this often on the last 1 s of audio
#endif
#ifndef KWS_REFRACTORY_MS
#define KWS_REFRACTORY_MS 1500  // ignore new commands this long after one was executed
#endif
#ifndef KWS_DEBUG
#define KWS_DEBUG 0  // 1 = print the top classes of every inference to Serial
#endif
#ifndef KWS_SELFTEST
#define KWS_SELFTEST 0  // 1 = at boot, compare features + output on test_clip.h with what export.py computed
#endif

// ---- collector (COLLECTOR_MODE 1) --------------------------------------------
#if COLLECTOR_MODE
#if !defined(WIFI_SSID) || !defined(WIFI_PASSWORD) || !defined(SERVER_HOST)
#error "COLLECTOR_MODE 1 needs WIFI_SSID, WIFI_PASSWORD and SERVER_HOST in config.h (see config.example.h)"
#endif
#ifndef SERVER_PORT
#define SERVER_PORT 5000
#endif
#endif
