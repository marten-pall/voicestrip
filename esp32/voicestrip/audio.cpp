#include "audio.h"

#include <algorithm>
#include <atomic>
#include <cstring>
#include <driver/i2s_std.h>
#include <esp_heap_caps.h>

#include "leds.h"
#include "settings.h"

namespace audio {
namespace {

constexpr size_t RING_BYTES_PSRAM = 1u << 20;  // 32.8 s - rides out WiFi stalls in collector mode
constexpr size_t RING_BYTES_SRAM = 1u << 17;   //  4.1 s - boards without PSRAM
constexpr size_t I2S_FRAMES = 256;             // 16 ms per DMA read

// Single producer (capture task) / single consumer (loop) byte ring. The size is a power
// of two so the free-running counters stay consistent when they wrap around.
class Ring {
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

  void clear() { tail_.store(head_.load(std::memory_order_acquire), std::memory_order_release); }

 private:
  uint8_t *buf_ = nullptr;
  size_t size_ = 0;
  std::atomic<size_t> head_{0};
  std::atomic<size_t> tail_{0};
};

i2s_chan_handle_t rxChan = nullptr;
Ring ring;
std::atomic<bool> capturing{false};
std::atomic<uint32_t> droppedSamples{0};

void halt(const char *what, esp_err_t err) {
  Serial.printf("VIGA: %s ebaõnnestus (%s)\n", what, esp_err_to_name(err));
  leds::setStatus(leds::Status::Error);
  for (;;) {
    leds::tick();
    delay(20);
  }
}

void setupI2S() {
  i2s_chan_config_t chanCfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  chanCfg.dma_desc_num = 8;
  chanCfg.dma_frame_num = I2S_FRAMES;
  esp_err_t err = i2s_new_channel(&chanCfg, nullptr, &rxChan);
  if (err != ESP_OK) halt("i2s_new_channel", err);

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
  if (err != ESP_OK) halt("i2s_channel_init_std_mode", err);
  err = i2s_channel_enable(rxChan);
  if (err != ESP_OK) halt("i2s_channel_enable", err);
}

void setupRing() {
  bool ok = psramFound() && ring.begin(RING_BYTES_PSRAM, MALLOC_CAP_SPIRAM);
  if (!ok) ok = ring.begin(RING_BYTES_SRAM, MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL);
  if (!ok) halt("helipuhvri eraldamine", ESP_ERR_NO_MEM);
  Serial.printf("Helipuhver: %.1f s (%s)\n", static_cast<float>(ring.size()) / BYTES_PER_SEC,
                ring.size() == RING_BYTES_PSRAM ? "PSRAM" : "sisemine RAM, PSRAM puudub/väljas");
}

// Reads the mic all the time (keeps the DMA from overflowing) and copies to the ring while
// capturing. Also drives the LEDs - it wakes up every 16 ms anyway.
void captureTask(void *) {
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
    leds::tick();
  }
}

}  // namespace

void begin() {
  setupRing();
  setupI2S();
  xTaskCreatePinnedToCore(captureTask, "i2s_mic", 4096, nullptr, 5, nullptr, 1);
}

void setCapturing(bool on) {
  capturing = on;
  if (!on) delay(40);  // let a write that may be in progress finish before the caller clears the ring
}

size_t available() { return ring.available(); }
size_t read(uint8_t *dst, size_t max) { return ring.read(dst, max); }
void clear() { ring.clear(); }
size_t ringBytes() { return ring.size(); }
uint32_t dropped() { return droppedSamples.load(); }
void consumeDropped(uint32_t n) { droppedSamples -= n; }

}  // namespace audio
