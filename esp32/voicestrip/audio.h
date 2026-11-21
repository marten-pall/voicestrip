// INMP441 capture: I2S DMA -> int16 PCM -> single-producer/single-consumer ring buffer.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace audio {

constexpr uint32_t SAMPLE_RATE = 16000;
constexpr uint32_t BYTES_PER_SEC = SAMPLE_RATE * 2;

// Starts I2S and the capture task (which also calls leds::tick() every 16 ms). Halts with a
// Serial message if the hardware cannot be set up.
void begin();

// While false the mic keeps running but its audio is dropped (nothing piles up in the ring).
void setCapturing(bool on);

size_t available();                      // bytes waiting in the ring
size_t read(uint8_t *dst, size_t max);   // consumer side only
void clear();                            // drop everything buffered (consumer side only)
size_t ringBytes();                      // ring capacity

// Samples dropped because the ring was full. consumeDropped(n) subtracts what was reported.
uint32_t dropped();
void consumeDropped(uint32_t n);

}  // namespace audio
