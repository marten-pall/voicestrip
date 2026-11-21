// COLLECTOR_MODE 1: WiFi microphone for the data collector (Data/app.py).
//
// The ESP32 is always the HTTP client:
//   idle       GET  /api/esp32/poll   every 300 ms (also the heartbeat -> "online" in the UI)
//   streaming  POST /api/esp32/chunk  ~0.25 s of raw 16 kHz int16 PCM per request
// Every reply is one command line: "idle" | "stream <id>" | "stop <id>" (Data/esp32_link.py).
#pragma once

namespace collector {

void begin();  // starts WiFi
void step();   // call from loop()

}  // namespace collector
