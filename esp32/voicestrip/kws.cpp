#include "kws.h"

#include "settings.h"

#if !COLLECTOR_MODE

#if !__has_include("model_data.h") || !__has_include("features.h")
#error "Voice control needs model_data.h + features.h: run trainer/export.py (or set COLLECTOR_MODE 1 in config.h)"
#endif

#include <cmath>
#include <cstring>
#include <esp_dsp.h>
#include <esp_heap_caps.h>

#include "audio.h"
#include "features.h"
#include "model_data.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/schema/schema_generated.h"

static_assert(FEATURES_EXPORT_ID == MODEL_EXPORT_ID,
              "features.h and model_data.h come from different export.py runs - export again");
static_assert(FEAT_SAMPLE_RATE == static_cast<int>(audio::SAMPLE_RATE), "sample rate mismatch");

#if KWS_SELFTEST
#include "test_clip.h"
static_assert(TEST_CLIP_EXPORT_ID == MODEL_EXPORT_ID, "test_clip.h comes from a different export.py run");
#endif

namespace kws {
namespace {

constexpr size_t ARENA_BYTES = 1536 * 1024;  // in PSRAM; the boot log prints what is really used
constexpr int HOP_SAMPLES = FEAT_SAMPLE_RATE * KWS_HOP_MS / 1000;
constexpr int N_FEATURES = FEAT_N_MELS * FEAT_N_FRAMES;

CommandHandler onCommand = nullptr;
tflite::MicroInterpreter *interpreter = nullptr;
int16_t window[FEAT_NUM_SAMPLES];        // the last 1 s of audio
float features[N_FEATURES];              // [mel][frame], normalised
float fftBuf[2 * FEAT_N_FFT];            // complex, interleaved re/im
float power[FEAT_N_BINS];
float history[KWS_AVERAGE][KWS_N_CLASSES];
int historyCount = 0, historyPos = 0;
uint32_t lastCommandMs = 0;
uint32_t lastInferenceUs = 0;

// Mirrors FirmwareFeatures in trainer/export.py: frames every FEAT_HOP samples (no centre
// padding), zero-padded Hann window, |FFT|^2, sparse mel filterbank, log, normalisation.
void computeFeatures(const int16_t *pcm) {
  for (int f = 0; f < FEAT_N_FRAMES; ++f) {
    const int16_t *x = pcm + f * FEAT_HOP;
    for (int i = 0; i < FEAT_N_FFT; ++i) {
      fftBuf[2 * i] = x[i] * (1.0f / 32768.0f) * FEAT_WINDOW[i];
      fftBuf[2 * i + 1] = 0.0f;
    }
    dsps_fft2r_fc32(fftBuf, FEAT_N_FFT);
    dsps_bit_rev_fc32(fftBuf, FEAT_N_FFT);
    for (int k = 0; k < FEAT_N_BINS; ++k) {
      float re = fftBuf[2 * k], im = fftBuf[2 * k + 1];
      power[k] = re * re + im * im;
    }
    for (int m = 0; m < FEAT_N_MELS; ++m) {
      const float *w = MEL_WEIGHTS + MEL_OFFSET[m];
      const float *p = power + MEL_START[m];
      float sum = 0.0f;
      for (int j = 0; j < MEL_LEN[m]; ++j) sum += w[j] * p[j];
      features[m * FEAT_N_FRAMES + f] = (logf(sum + FEAT_LOG_EPS) - FEAT_MEAN[m]) * FEAT_INV_STD[m];
    }
  }
}

// features -> softmax probabilities. False if the interpreter failed.
bool classify(float *probs) {
  TfLiteTensor *in = interpreter->input(0);
  if (in->type == kTfLiteInt8) {
    const float invScale = 1.0f / in->params.scale;
    const int zero = in->params.zero_point;
    for (int i = 0; i < N_FEATURES; ++i) {
      int q = static_cast<int>(lroundf(features[i] * invScale)) + zero;
      in->data.int8[i] = static_cast<int8_t>(q < -128 ? -128 : (q > 127 ? 127 : q));
    }
  } else {
    memcpy(in->data.f, features, sizeof(features));
  }

  uint32_t t0 = micros();
  if (interpreter->Invoke() != kTfLiteOk) return false;
  lastInferenceUs = micros() - t0;

  TfLiteTensor *out = interpreter->output(0);
  float maxLogit = -1e30f;
  for (int c = 0; c < KWS_N_CLASSES; ++c) {
    probs[c] = out->type == kTfLiteInt8 ? (out->data.int8[c] - out->params.zero_point) * out->params.scale
                                        : out->data.f[c];
    if (probs[c] > maxLogit) maxLogit = probs[c];
  }
  float sum = 0.0f;
  for (int c = 0; c < KWS_N_CLASSES; ++c) sum += (probs[c] = expf(probs[c] - maxLogit));
  for (int c = 0; c < KWS_N_CLASSES; ++c) probs[c] /= sum;
  return true;
}

int argmax(const float *values) {
  int best = 0;
  for (int c = 1; c < KWS_N_CLASSES; ++c) {
    if (values[c] > values[best]) best = c;
  }
  return best;
}

bool setupModel() {
  const tflite::Model *model = tflite::GetModel(g_model_data);
  if (model->version() != TFLITE_SCHEMA_VERSION) {
    Serial.printf("Mudeli skeemi versioon %lu, oodatud %d\n", static_cast<unsigned long>(model->version()),
                  TFLITE_SCHEMA_VERSION);
    return false;
  }
  // Every op the converter may emit for this kind of CNN (int8 or float). A missing one shows
  // up in AllocateTensors() as "Didn't find op for builtin opcode ..." - add it here.
  static tflite::MicroMutableOpResolver<20> resolver;
  resolver.AddConv2D();
  resolver.AddDepthwiseConv2D();
  resolver.AddFullyConnected();
  resolver.AddMaxPool2D();
  resolver.AddAveragePool2D();
  resolver.AddMean();
  resolver.AddSum();
  resolver.AddReshape();
  resolver.AddTranspose();
  resolver.AddSoftmax();
  resolver.AddRelu();
  resolver.AddQuantize();
  resolver.AddDequantize();
  resolver.AddAdd();
  resolver.AddMul();
  resolver.AddPad();
  resolver.AddLogistic();
  resolver.AddConcatenation();
  resolver.AddStridedSlice();
  resolver.AddSqueeze();

  uint8_t *arena = static_cast<uint8_t *>(heap_caps_aligned_alloc(16, ARENA_BYTES, MALLOC_CAP_SPIRAM));
  if (arena == nullptr) arena = static_cast<uint8_t *>(heap_caps_aligned_alloc(16, ARENA_BYTES, MALLOC_CAP_8BIT));
  if (arena == nullptr) {
    Serial.println("Mudeli mälu (tensor arena) eraldamine ebaõnnestus - kas PSRAM on Tools menüüs sisse lülitatud?");
    return false;
  }
  static tflite::MicroInterpreter staticInterpreter(model, resolver, arena, ARENA_BYTES);
  interpreter = &staticInterpreter;
  if (interpreter->AllocateTensors() != kTfLiteOk) {
    Serial.println("AllocateTensors ebaõnnestus (vt ülal puuduvat op-i)");
    return false;
  }

  TfLiteTensor *in = interpreter->input(0);
  TfLiteTensor *out = interpreter->output(0);
  size_t inCount = in->bytes / (in->type == kTfLiteInt8 ? 1 : sizeof(float));
  int outCount = out->dims->data[out->dims->size - 1];
  if ((in->type != kTfLiteInt8 && in->type != kTfLiteFloat32) || inCount != static_cast<size_t>(N_FEATURES) ||
      outCount != KWS_N_CLASSES) {
    Serial.printf("Mudeli sisend/väljund ei klapi: sisend %u väärtust (oodatud %d), väljund %d (oodatud %d)\n",
                  static_cast<unsigned>(inCount), N_FEATURES, outCount, KWS_N_CLASSES);
    return false;
  }
  Serial.printf("Mudel: %u baiti, %s, arena %u / %u KB\n", g_model_data_len, in->type == kTfLiteInt8 ? "int8" : "float32",
                static_cast<unsigned>(interpreter->arena_used_bytes() / 1024), static_cast<unsigned>(ARENA_BYTES / 1024));
  return true;
}

#if KWS_SELFTEST
void selfTest() {
  computeFeatures(TEST_CLIP_PCM);
  float worst = 0.0f;
  for (int i = 0; i < N_FEATURES; ++i) worst = fmaxf(worst, fabsf(features[i] - TEST_CLIP_FEATURES[i]));
  Serial.printf("SELFTEST tunnused: max |vahe| = %.2e (%s)\n", worst, worst < 1e-2f ? "OK" : "VIGA");
  float probs[KWS_N_CLASSES];
  if (!classify(probs)) {
    Serial.println("SELFTEST: Invoke ebaõnnestus");
    return;
  }
  Serial.printf("SELFTEST klipp \"%s\": %u ms\n", TEST_CLIP_LABEL, static_cast<unsigned>(lastInferenceUs / 1000));
  for (int c = 0; c < KWS_N_CLASSES; ++c) {
    Serial.printf("  %-9s ESP32 %.3f   %s %.3f\n", KWS_CLASS_NAMES[c], probs[c], TEST_CLIP_PROBS_SOURCE,
                  TEST_CLIP_PROBS[c]);
  }
}
#endif

}  // namespace

bool begin(CommandHandler handler) {
  onCommand = handler;
  Serial.printf("Režiim: häälkäsklused, klassid:");
  for (int c = 0; c < KWS_N_CLASSES; ++c) Serial.printf(" %s", KWS_CLASS_NAMES[c]);
  Serial.printf(" | lävi %.2f\n", KWS_THRESHOLD);
  if (dsps_fft2r_init_fc32(nullptr, FEAT_N_FFT) != ESP_OK) {
    Serial.println("FFT (esp-dsp) init ebaõnnestus");
    return false;
  }
  if (!setupModel()) return false;
#if KWS_SELFTEST
  selfTest();
#endif
  memset(window, 0, sizeof(window));
  audio::clear();
  audio::setCapturing(true);
  return true;
}

void step() {
  // Shift in every complete hop; with a backlog (slow inference) catch up first, then classify once.
  if (audio::available() < HOP_SAMPLES * sizeof(int16_t)) {
    delay(5);
    return;
  }
  while (audio::available() >= HOP_SAMPLES * sizeof(int16_t)) {
    memmove(window, window + HOP_SAMPLES, (FEAT_NUM_SAMPLES - HOP_SAMPLES) * sizeof(int16_t));
    audio::read(reinterpret_cast<uint8_t *>(window + FEAT_NUM_SAMPLES - HOP_SAMPLES), HOP_SAMPLES * sizeof(int16_t));
  }

  computeFeatures(window);
  float probs[KWS_N_CLASSES];
  if (!classify(probs)) {
    Serial.println("Invoke ebaõnnestus");
    return;
  }

  memcpy(history[historyPos], probs, sizeof(probs));
  historyPos = (historyPos + 1) % KWS_AVERAGE;
  if (historyCount < KWS_AVERAGE) historyCount++;
  float avg[KWS_N_CLASSES] = {};
  for (int h = 0; h < historyCount; ++h) {
    for (int c = 0; c < KWS_N_CLASSES; ++c) avg[c] += history[h][c] / historyCount;
  }
  int best = argmax(avg);

#if KWS_DEBUG
  Serial.printf("[%3u ms] %-9s %.2f (praegu %s %.2f)\n", static_cast<unsigned>(lastInferenceUs / 1000),
                KWS_CLASS_NAMES[best], avg[best], KWS_CLASS_NAMES[argmax(probs)], probs[argmax(probs)]);
#endif

  uint32_t now = millis();
  if (KWS_IS_COMMAND[best] && avg[best] >= KWS_THRESHOLD && now - lastCommandMs >= KWS_REFRACTORY_MS) {
    lastCommandMs = now;
    historyCount = 0;  // the same word must not trigger again from the averaged history
    if (onCommand) onCommand(KWS_CLASS_NAMES[best], avg[best]);
  }
}

}  // namespace kws

#endif  // !COLLECTOR_MODE
