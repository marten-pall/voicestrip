#include "leds.h"

#include "settings.h"

namespace leds {
namespace {

constexpr uint32_t PWM_HZ = 25000;  // above the audio band
constexpr uint8_t PWM_BITS = 8;
// Brightness steps for "heledam" / "tumedam" (scaled by LED_MAX_BRIGHTNESS)
constexpr uint8_t LEVELS[] = {12, 25, 50, 100, 170, 255};
constexpr int N_LEVELS = sizeof(LEVELS) / sizeof(LEVELS[0]);
constexpr int DEFAULT_LEVEL = 4;
constexpr uint32_t FADE_PERIOD_MS = 8000;
constexpr uint32_t BLINK_PERIOD_MS = 1000;

enum class Effect : uint8_t { Steady, Fade, Blink };

struct State {
  Status status = Status::None;
  bool on = false;
  uint8_t r = 255, g = 255, b = 255;
  int level = DEFAULT_LEVEL;
  Effect effect = Effect::Steady;
  uint32_t effectStart = 0;
  uint8_t flashR = 0, flashG = 0, flashB = 0;
  uint32_t flashUntil = 0;
};

State state;
portMUX_TYPE lock = portMUX_INITIALIZER_UNLOCKED;

void write(uint8_t pin, uint8_t level) {
  ledcWrite(pin, LED_ACTIVE_LOW ? 255 - level : level);
}

uint8_t scale(uint8_t value, uint32_t brightness) {
  return static_cast<uint8_t>(value * brightness / 255);
}

// hue 0..1535 -> fully saturated RGB
void hueToRgb(uint32_t hue, uint8_t &r, uint8_t &g, uint8_t &b) {
  uint8_t x = hue & 255;
  switch ((hue >> 8) % 6) {
    case 0: r = 255; g = x; b = 0; break;
    case 1: r = 255 - x; g = 255; b = 0; break;
    case 2: r = 0; g = 255; b = x; break;
    case 3: r = 0; g = 255 - x; b = 255; break;
    case 4: r = x; g = 0; b = 255; break;
    default: r = 255; g = 0; b = 255 - x; break;
  }
}

void statusColor(Status status, uint32_t ms, uint8_t &r, uint8_t &g, uint8_t &b) {
  bool slow = (ms / 500) % 2, fast = (ms / 125) % 2;
  r = g = b = 0;
  switch (status) {
    case Status::Wifi:        if (slow) b = 255; break;
    case Status::NoServer:    if (slow) { r = 255; g = 140; } break;
    case Status::Idle:        g = 255; break;
    case Status::Monitor:     g = 120; b = 255; break;
    case Status::Recording:   r = 255; break;
    case Status::UploadError:
    case Status::Error:       if (fast) r = 255; break;
    case Status::None:        break;
  }
  r = scale(r, LED_BRIGHTNESS);
  g = scale(g, LED_BRIGHTNESS);
  b = scale(b, LED_BRIGHTNESS);
}

void change(void (*fn)(State &)) {
  portENTER_CRITICAL(&lock);
  fn(state);
  portEXIT_CRITICAL(&lock);
}

}  // namespace

void begin() {
  ledcAttach(PIN_LED_R, PWM_HZ, PWM_BITS);
  ledcAttach(PIN_LED_G, PWM_HZ, PWM_BITS);
  ledcAttach(PIN_LED_B, PWM_HZ, PWM_BITS);
  tick();
}

void tick() {
  static uint32_t last = UINT32_MAX;
  portENTER_CRITICAL(&lock);
  State s = state;
  portEXIT_CRITICAL(&lock);

  uint32_t ms = millis();
  uint8_t r = 0, g = 0, b = 0;
  if (static_cast<int32_t>(s.flashUntil - ms) > 0) {
    r = scale(s.flashR, LED_BRIGHTNESS);
    g = scale(s.flashG, LED_BRIGHTNESS);
    b = scale(s.flashB, LED_BRIGHTNESS);
  } else if (s.status != Status::None) {
    statusColor(s.status, ms, r, g, b);
  } else if (s.on) {
    r = s.r, g = s.g, b = s.b;
    uint32_t t = ms - s.effectStart;
    if (s.effect == Effect::Fade) hueToRgb((t % FADE_PERIOD_MS) * 1536 / FADE_PERIOD_MS, r, g, b);
    if (s.effect == Effect::Blink && (t % BLINK_PERIOD_MS) >= BLINK_PERIOD_MS / 2) r = g = b = 0;
    uint32_t brightness = static_cast<uint32_t>(LEVELS[s.level]) * LED_MAX_BRIGHTNESS / 255;
    r = scale(r, brightness);
    g = scale(g, brightness);
    b = scale(b, brightness);
  }

  uint32_t packed = (r << 16) | (g << 8) | b;
  if (packed == last) return;
  last = packed;
  write(PIN_LED_R, r);
  write(PIN_LED_G, g);
  write(PIN_LED_B, b);
}

void setStatus(Status status) {
  portENTER_CRITICAL(&lock);
  state.status = status;
  portEXIT_CRITICAL(&lock);
}

void flash(uint8_t r, uint8_t g, uint8_t b, uint32_t ms) {
  portENTER_CRITICAL(&lock);
  state.flashR = r, state.flashG = g, state.flashB = b;
  state.flashUntil = millis() + ms;
  portEXIT_CRITICAL(&lock);
}

void turnOn() {
  change([](State &s) { s.on = true; });
}

void turnOff() {
  change([](State &s) { s.on = false; s.effect = Effect::Steady; });
}

void setColor(uint8_t r, uint8_t g, uint8_t b) {
  portENTER_CRITICAL(&lock);
  state.r = r, state.g = g, state.b = b;
  state.on = true;
  state.effect = Effect::Steady;
  portEXIT_CRITICAL(&lock);
}

void startFade() {
  portENTER_CRITICAL(&lock);
  state.on = true;
  state.effect = Effect::Fade;
  state.effectStart = millis();
  portEXIT_CRITICAL(&lock);
}

void startBlink() {
  portENTER_CRITICAL(&lock);
  state.on = true;
  state.effect = Effect::Blink;
  state.effectStart = millis();
  portEXIT_CRITICAL(&lock);
}

void brighter() {
  change([](State &s) { s.on = true; if (s.level < N_LEVELS - 1) s.level++; });
}

void dimmer() {
  change([](State &s) { if (s.level > 0) s.level--; });
}

}  // namespace leds
