// Voice Collector - ESP32-S3 + INMP441 WiFi microphone (source "esp32" in Data/app.py).
//
// While a recording started in the browser ("ESP32 mikrofon") runs, this streams
// 16 kHz mono 16-bit PCM to the Flask collector. The ESP32 is always the HTTP client:
//   idle       GET  /api/esp32/poll   every 300 ms (also the heartbeat -> "online" in the UI)
//   streaming  POST /api/esp32/chunk  ~0.25 s of raw PCM per request
// Every reply is one command line: "idle" | "stream <id>" | "stop <id>"
// (protocol: Data/esp32_link.py, setup in Estonian: Data/README.md).
//
// Toolchain: Arduino IDE + "esp32" by Espressif 3.x, board "ESP32S3 Dev Module".
//
// Wiring:  INMP441  SCK=GPIO4  WS=GPIO3  SD=GPIO2  VDD=3.3V  GND + L/R=GND
//          status LEDs via MOSFET board  R=GPIO9  G=GPIO10  B=GPIO11  (inverted: LOW = on)

#include <Arduino.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <algorithm>
#include <atomic>
#include <cstring>
#include <driver/i2s_std.h>
#include <esp_heap_caps.h>

#if __has_include("config.h")
#include "config.h"
#else
#error "Kopeeri config.example.h -> config.h ja täida WiFi ning serveri andmed"
#endif

#define FW_VERSION "collector-1.0"

// ----------------------------------------------------------------------------
// Configuration (anything here can be overridden in config.h)
// ----------------------------------------------------------------------------

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
#ifndef I2S_MIC_SLOT
#define I2S_MIC_SLOT I2S_STD_SLOT_LEFT  // INMP441 L/R pin on GND = left channel
#endif
#ifndef GAIN_SHIFT
#define GAIN_SHIFT 16  // int32 (24-bit sample in the top bits) >> 16 = same scale as the Pi I2S path
#endif
#ifndef LED_BRIGHTNESS
#define LED_BRIGHTNESS 24  // 0..255, 0 = LEDs stay dark
#endif
#ifndef LED_ACTIVE_LOW
#define LED_ACTIVE_LOW 1  // voicestrip MOSFET board is inverted: LOW = LED on (same as inference.py on the Pi)
#endif

constexpr uint32_t SAMPLE_RATE = 16000;
constexpr uint32_t BYTES_PER_SEC = SAMPLE_RATE * 2;
constexpr size_t CHUNK_BYTES = BYTES_PER_SEC / 4;      // 0.25 s per POST
constexpr size_t MAX_CHUNK_BYTES = CHUNK_BYTES * 4;    // bigger POSTs while catching up after a WiFi stall
constexpr size_t RING_BYTES_PSRAM = 1u << 20;          // 32.8 s - rides out WiFi stalls
constexpr size_t RING_BYTES_SRAM = 1u << 17;           //  4.1 s - boards without PSRAM
constexpr size_t I2S_FRAMES = 256;                     // 16 ms per DMA read
constexpr uint32_t POLL_MS = 300;
constexpr uint32_t RETRY_DELAY_MS = 200;
constexpr uint32_t FINAL_TRIES = 10;                   // "stop": attempts per chunk before giving up
constexpr uint32_t LED_PWM_HZ = 25000;                 // above the audio band
constexpr uint8_t LED_PWM_BITS = 8;

// ----------------------------------------------------------------------------
// Types
// ----------------------------------------------------------------------------

// Single producer (I2S task) / single consumer (loop) byte ring. Size must be a power of
// two so the free-running counters stay consistent when they wrap around.
class AudioRing {
 public:
  bool begin(size_t bytes, uint32_t caps) {
    buf_ = static_cast<uint8_t *>(heap_caps_malloc(bytes, caps));
    size_ = buf_ ? bytes : 0;
    return buf_ != nullptr;
  }
  size_t size() const { return size_; }
  size_t available() const { return head_.load(std::memory_order_acquire) - tail_.load(std::memory_order_relaxed); }
  size_t space() const { return size_ - (head_.load(std::memory_order_relaxed) - tail_.load(std::memory_order_acquire)); }

  void write(const uint8_t *src, size_t n) {  // producer only; caller checked space()
    size_t head = head_.load(std::memory_order_relaxed);
    size_t pos = head & (size_ - 1);
    size_t first = std::min(n, size_ - pos);
    memcpy(buf_ + pos, src, first);
    memcpy(buf_, src + first, n - first);
    head_.store(head + n, std::memory_order_release);
  }

  size_t read(uint8_t *dst, size_t max) {  // consumer only
    size_t tail = tail_.load(std::memory_order_relaxed);
    size_t n = std::min(max, head_.load(std::memory_order_acquire) - tail);
    size_t pos = tail & (size_ - 1);
    size_t first = std::min(n, size_ - pos);
    memcpy(dst, buf_ + pos, first);
    memcpy(dst + first, buf_, n - first);
    tail_.store(tail + n, std::memory_order_release);
    return n;
  }

  void clear() { tail_.store(head_.load(std::memory_order_acquire), std::memory_order_release); }  // consumer only

 private:
  uint8_t *buf_ = nullptr;
  size_t size_ = 0;
  std::atomic<size_t> head_{0};
  std::atomic<size_t> tail_{0};
};

enum LedState : uint8_t { LED_WIFI, LED_NO_SERVER, LED_IDLE, LED_MONITOR, LED_RECORDING, LED_UPLOAD_ERROR };

struct Command {
  enum Kind { NONE, IDLE, STREAM, STOP } kind;
  String id;
};

// ----------------------------------------------------------------------------
// State
// ----------------------------------------------------------------------------

static i2s_chan_handle_t rxChan = nullptr;
static AudioRing ring;
static std::atomic<bool> capturing{false};       // I2S task copies audio into the ring only while true
static std::atomic<uint32_t> droppedSamples{0};  // ring was full (WiFi slower than the mic)
static std::atomic<uint8_t> ledState{LED_WIFI};

static WiFiClient wifiClient;
static HTTPClient http;
static String serverIp;
static String streamId;  // "" = idle; "m<N>" = mic test monitor; otherwise a recording_id
static uint32_t seqNo = 0;
static uint8_t chunk[MAX_CHUNK_BYTES];
static size_t chunkLen = 0;
static bool chunkPending = false;  // `chunk` holds data not yet accepted by the server
static uint32_t uploadErrors = 0;

// ----------------------------------------------------------------------------
// Status LEDs (driven from the I2S task, so blinking keeps going while HTTP blocks)
// ----------------------------------------------------------------------------

static void ledOut(uint8_t pin, uint8_t level) {
  uint32_t duty = static_cast<uint32_t>(level) * LED_BRIGHTNESS / 255;
  ledcWrite(pin, LED_ACTIVE_LOW ? 255 - duty : duty);
}

static void ledTick() {
  static uint32_t last = UINT32_MAX;
  uint32_t ms = millis();
  bool slow = (ms / 500) % 2, fast = (ms / 125) % 2;
  uint8_t r = 0, g = 0, b = 0;
  switch (static_cast<LedState>(ledState.load())) {
    case LED_WIFI:         if (slow) b = 255; break;               // blue blink: connecting to WiFi
    case LED_NO_SERVER:    if (slow) { r = 255; g = 140; } break;  // yellow blink: server not answering
    case LED_IDLE:         g = 255; break;                         // green: online, waiting
    case LED_MONITOR:      g = 120; b = 255; break;                // cyan: mic test page open
    case LED_RECORDING:    r = 255; break;                         // red: recording
    case LED_UPLOAD_ERROR: if (fast) r = 255; break;               // fast red blink: uploads failing
  }
  uint32_t packed = (r << 16) | (g << 8) | b;
  if (packed == last) return;
  last = packed;
  ledOut(PIN_LED_R, r);
  ledOut(PIN_LED_G, g);
  ledOut(PIN_LED_B, b);
}

static void setupLeds() {
  ledcAttach(PIN_LED_R, LED_PWM_HZ, LED_PWM_BITS);
  ledcAttach(PIN_LED_G, LED_PWM_HZ, LED_PWM_BITS);
  ledcAttach(PIN_LED_B, LED_PWM_HZ, LED_PWM_BITS);
  ledTick();
}

static LedState streamLed() {
  if (streamId.isEmpty()) return LED_IDLE;
  return streamId.startsWith("m") ? LED_MONITOR : LED_RECORDING;
}

// ----------------------------------------------------------------------------
// Audio capture
// ----------------------------------------------------------------------------

static void haltWithError(const char *what, esp_err_t err) {
  Serial.printf("VIGA: %s ebaõnnestus (%s)\n", what, esp_err_to_name(err));
  ledState = LED_UPLOAD_ERROR;
  for (;;) {
    ledTick();
    delay(20);
  }
}

static void setupI2S() {
  i2s_chan_config_t chanCfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  chanCfg.dma_desc_num = 8;
  chanCfg.dma_frame_num = I2S_FRAMES;
  esp_err_t err = i2s_new_channel(&chanCfg, nullptr, &rxChan);
  if (err != ESP_OK) haltWithError("i2s_new_channel", err);

  // INMP441: Philips I2S, 24-bit data in a 32-bit slot, one channel selected by its L/R pin.
  i2s_std_config_t stdCfg = {
      .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
      .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
      .gpio_cfg =
          {
              .mclk = I2S_GPIO_UNUSED,
              .bclk = static_cast<gpio_num_t>(PIN_I2S_SCK),
              .ws = static_cast<gpio_num_t>(PIN_I2S_WS),
              .dout = I2S_GPIO_UNUSED,
              .din = static_cast<gpio_num_t>(PIN_I2S_SD),
              .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
          },
  };
  stdCfg.slot_cfg.slot_mask = I2S_MIC_SLOT;
  err = i2s_channel_init_std_mode(rxChan, &stdCfg);
  if (err != ESP_OK) haltWithError("i2s_channel_init_std_mode", err);
  err = i2s_channel_enable(rxChan);
  if (err != ESP_OK) haltWithError("i2s_channel_enable", err);
}

static void setupRing() {
  bool psram = psramFound();
  bool ok = psram && ring.begin(RING_BYTES_PSRAM, MALLOC_CAP_SPIRAM);
  if (!ok) ok = ring.begin(RING_BYTES_SRAM, MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL);
  if (!ok) haltWithError("helipuhvri eraldamine", ESP_ERR_NO_MEM);
  Serial.printf("Helipuhver: %.1f s (%s)\n", static_cast<float>(ring.size()) / BYTES_PER_SEC,
                ring.size() == RING_BYTES_PSRAM ? "PSRAM" : "sisemine RAM, PSRAM puudub/väljas");
}

// Reads the mic all the time (keeps the DMA from overflowing); copies to the ring only while
// a stream is active. Also drives the LEDs - it wakes up every 16 ms anyway.
static void i2sTask(void *) {
  static int32_t raw[I2S_FRAMES];
  static int16_t pcm[I2S_FRAMES];
  for (;;) {
    size_t bytesRead = 0;
    if (i2s_channel_read(rxChan, raw, sizeof(raw), &bytesRead, 1000) == ESP_OK) {
      size_t frames = bytesRead / sizeof(int32_t);
      for (size_t i = 0; i < frames; ++i) {
        int32_t s = raw[i] >> GAIN_SHIFT;
        pcm[i] = static_cast<int16_t>(std::max<int32_t>(-32768, std::min<int32_t>(32767, s)));
      }
      if (capturing.load()) {
        size_t bytes = frames * sizeof(int16_t);
        if (ring.space() >= bytes) {
          ring.write(reinterpret_cast<const uint8_t *>(pcm), bytes);
        } else {
          droppedSamples += static_cast<uint32_t>(frames);
        }
      }
    }
    ledTick();
  }
}

// ----------------------------------------------------------------------------
// HTTP
// ----------------------------------------------------------------------------

static bool resolveServer() {
  if (!serverIp.isEmpty()) return true;
  String host = SERVER_HOST;
  if (!host.endsWith(".local")) {
    serverIp = host;
    return true;
  }
  static bool mdnsStarted = false;
  if (!mdnsStarted) mdnsStarted = MDNS.begin("voicestrip-mic");
  IPAddress ip = MDNS.queryHost(host.substring(0, host.length() - 6), 2000);
  if (ip[0] == 0) {
    Serial.printf("mDNS: %s ei leitud - kasuta config.h-s IP-aadressi\n", SERVER_HOST);
    return false;
  }
  serverIp = ip.toString();
  Serial.printf("mDNS: %s = %s\n", SERVER_HOST, serverIp.c_str());
  return true;
}

// GET when body == nullptr, otherwise POST. Returns the HTTP status (<= 0: network error).
static int request(const String &uri, const uint8_t *body, size_t len, String &reply) {
  reply = "";
  if (!http.begin(wifiClient, serverIp, SERVER_PORT, uri)) return HTTPC_ERROR_CONNECTION_REFUSED;
  http.setConnectTimeout(2000);
  http.setTimeout(3000);
  int code;
  if (body == nullptr) {
    code = http.GET();
  } else {
    http.addHeader("Content-Type", "application/octet-stream");
    code = http.POST(const_cast<uint8_t *>(body), len);
  }
  if (code > 0) reply = http.getString();
  http.end();
  reply.trim();
  return code;
}

static String deviceQuery() {
  return String("rssi=") + static_cast<int>(WiFi.RSSI()) + "&fw=" FW_VERSION "&buf=" +
         String(static_cast<float>(ring.size()) / BYTES_PER_SEC, 1) + "&up=" + (millis() / 1000);
}

static Command parseCommand(const String &line) {
  if (line == "idle") return {Command::IDLE, ""};
  if (line.startsWith("stream ")) return {Command::STREAM, line.substring(7)};
  if (line.startsWith("stop ")) return {Command::STOP, line.substring(5)};
  return {Command::NONE, ""};
}

// ----------------------------------------------------------------------------
// Streams
// ----------------------------------------------------------------------------

static void stopCapture() {
  capturing = false;
  delay(40);  // let the I2S task finish a write that may be in progress
}

static void beginStream(const String &id) {
  if (!streamId.isEmpty()) Serial.printf("Voog %s katkestatud\n", streamId.c_str());
  stopCapture();
  ring.clear();  // a new stream starts from "now", not from old buffered audio
  streamId = id;
  seqNo = 0;
  droppedSamples = 0;
  chunkPending = false;
  uploadErrors = 0;
  capturing = true;
  ledState = streamLed();
  Serial.printf("Voog %s algas (%s)\n", id.c_str(), id.startsWith("m") ? "mikrofoni test" : "salvestus");
}

static void endStream() {
  stopCapture();
  ring.clear();
  streamId = "";
  chunkPending = false;
  ledState = LED_IDLE;
}

// One upload attempt of `chunk`. True when the server took it (or rejected it for good).
static bool postChunk(bool final, String &reply) {
  uint32_t dropped = droppedSamples.load();
  String uri = String("/api/esp32/chunk?stream=") + streamId + "&seq=" + seqNo + "&dropped=" + dropped +
               "&final=" + (final ? "1" : "0") + "&" + deviceQuery();
  int code = request(uri, chunk, chunkLen, reply);
  if (code == 200) {
    droppedSamples -= dropped;
    seqNo++;
    chunkPending = false;
    if (uploadErrors) {
      Serial.printf("Üleslaadimine taastus (%u ebaõnnestunud katset)\n", static_cast<unsigned>(uploadErrors));
    }
    uploadErrors = 0;
    ledState = streamLed();
    return true;
  }
  if (code >= 400 && code < 500) {  // the server will never accept this chunk: skip it
    Serial.printf("Server lükkas tüki tagasi: HTTP %d %s\n", code, reply.c_str());
    seqNo++;
    chunkPending = false;
    reply = "";
    return true;
  }
  if (uploadErrors++ % 10 == 0) {
    Serial.printf("Üleslaadimine ebaõnnestus (%s), puhvris %.1f s\n",
                  code > 0 ? (String("HTTP ") + code).c_str() : HTTPClient::errorToString(code).c_str(),
                  static_cast<float>(ring.available()) / BYTES_PER_SEC);
  }
  ledState = LED_UPLOAD_ERROR;
  return false;
}

// "stop <id>": stop capturing, send everything still buffered (last chunk final=1), go idle.
static void finishStream() {
  stopCapture();
  for (;;) {
    if (!chunkPending) {
      chunkLen = ring.read(chunk, MAX_CHUNK_BYTES);
      chunkPending = true;
    }
    bool final = ring.available() == 0;
    String reply;
    uint32_t tries = 0;
    while (!postChunk(final, reply) && ++tries < FINAL_TRIES) delay(RETRY_DELAY_MS);
    if (tries >= FINAL_TRIES) {
      Serial.println("Viimast osa ei saanud saata - server salvestab selle, mis kohale jõudis");
      break;
    }
    if (final) break;
  }
  Serial.printf("Voog %s lõpetatud (%u tükki)\n", streamId.c_str(), static_cast<unsigned>(seqNo));
  endStream();
}

static void applyCommand(const Command &cmd) {
  switch (cmd.kind) {
    case Command::STREAM:
      if (cmd.id != streamId) beginStream(cmd.id);
      break;
    case Command::STOP:
      if (cmd.id == streamId) finishStream();  // otherwise this stream is already finished
      break;
    case Command::IDLE:
      if (!streamId.isEmpty()) {
        Serial.printf("Voog %s lõpetati serveri poolt\n", streamId.c_str());
        endStream();
      }
      break;
    case Command::NONE:
      break;
  }
}

static void streamStep() {
  if (!chunkPending) {
    size_t avail = ring.available();
    if (avail < CHUNK_BYTES) {
      delay(10);
      return;
    }
    chunkLen = ring.read(chunk, avail < MAX_CHUNK_BYTES ? avail : MAX_CHUNK_BYTES);
    chunkPending = true;
  }
  String reply;
  if (postChunk(false, reply)) {
    applyCommand(parseCommand(reply));
  } else {
    delay(RETRY_DELAY_MS);  // WiFi stall: audio keeps collecting in the ring meanwhile
  }
}

static void idleStep() {
  static uint32_t lastPoll = 0;
  static uint32_t failures = 0;
  static bool serverOk = false;
  if (millis() - lastPoll < POLL_MS) {
    delay(5);
    return;
  }
  lastPoll = millis();
  if (WiFi.status() != WL_CONNECTED) {
    ledState = LED_WIFI;
    return;
  }
  String reply;
  int code = resolveServer() ? request(String("/api/esp32/poll?") + deviceQuery(), nullptr, 0, reply) : -1;
  if (code != 200) {
    ledState = LED_NO_SERVER;
    serverOk = false;
    if (failures++ % 10 == 0) {
      Serial.printf("Server http://%s:%d ei vasta (%s). Kas app.py jookseb --host 0.0.0.0 ja tulemüür lubab?\n",
                    SERVER_HOST, SERVER_PORT,
                    code > 0 ? (String("HTTP ") + code).c_str() : HTTPClient::errorToString(code).c_str());
    }
    if (String(SERVER_HOST).endsWith(".local")) serverIp = "";  // resolve again next time
    return;
  }
  if (!serverOk) Serial.printf("Server http://%s:%d vastab - valmis salvestama\n", serverIp.c_str(), SERVER_PORT);
  serverOk = true;
  failures = 0;
  ledState = LED_IDLE;
  applyCommand(parseCommand(reply));
}

static void watchWifi() {
  static bool wasConnected = false;
  bool connected = WiFi.status() == WL_CONNECTED;
  if (connected == wasConnected) return;
  wasConnected = connected;
  if (connected) {
    Serial.printf("WiFi ühendatud: IP %s, signaal %d dBm\n", WiFi.localIP().toString().c_str(),
                  static_cast<int>(WiFi.RSSI()));
  } else {
    Serial.println("WiFi ühendus puudub, ühendan...");
  }
}

// ----------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println("Voice Collector ESP32 mikrofon " FW_VERSION);
  Serial.printf("INMP441: SCK=%d WS=%d SD=%d · LED R=%d G=%d B=%d · server %s:%d\n", PIN_I2S_SCK, PIN_I2S_WS,
                PIN_I2S_SD, PIN_LED_R, PIN_LED_G, PIN_LED_B, SERVER_HOST, SERVER_PORT);

  setupLeds();
  setupRing();
  setupI2S();
  xTaskCreatePinnedToCore(i2sTask, "i2s_mic", 4096, nullptr, 5, nullptr, 1);

  WiFi.mode(WIFI_STA);
  WiFi.setHostname("voicestrip-mic");
  WiFi.setSleep(false);  // modem sleep adds latency and stalls the upload
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("WiFi: ühendan võrku \"%s\"...\n", WIFI_SSID);
}

void loop() {
  watchWifi();
  if (streamId.isEmpty()) {
    idleStep();
  } else {
    streamStep();
  }
}
