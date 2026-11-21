// RGB LED strip through the MOSFET board: status colours (collector mode, errors) and the
// light itself (voice control). tick() is called every ~16 ms from the audio capture task,
// so blinking and fading keep going while the main loop is busy.
#pragma once

#include <stdint.h>

namespace leds {

enum class Status : uint8_t {
  None,         // no status shown: the strip shows the light state (voice control)
  Wifi,         // blue blink: connecting to WiFi
  NoServer,     // yellow blink: WiFi ok, collector server not answering
  Idle,         // green: collector online, waiting
  Monitor,      // cyan: mic test page open
  Recording,    // red: recording
  UploadError,  // fast red blink: uploads failing
  Error,        // fast red blink: fatal error (see Serial)
};

void begin();
void tick();
void setStatus(Status status);
void flash(uint8_t r, uint8_t g, uint8_t b, uint32_t ms);  // short confirmation, then back to normal

// Voice control
void turnOn();
void turnOff();
void setColor(uint8_t r, uint8_t g, uint8_t b);  // also turns on, stops effects
void startFade();                                // slow colour cycle ("hajuv")
void startBlink();                               // blink the current colour ("vilkuv")
void brighter();
void dimmer();

}  // namespace leds
