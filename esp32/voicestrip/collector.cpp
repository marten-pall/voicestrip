#include "collector.h"

#include "settings.h"

#if COLLECTOR_MODE

#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <WiFi.h>

#include "audio.h"
#include "leds.h"

namespace collector {
namespace {

constexpr size_t CHUNK_BYTES = audio::BYTES_PER_SEC / 4;  // 0.25 s per POST
constexpr size_t MAX_CHUNK_BYTES = CHUNK_BYTES * 4;       // bigger POSTs while catching up after a WiFi stall
constexpr uint32_t POLL_MS = 300;
constexpr uint32_t RETRY_DELAY_MS = 200;
constexpr uint32_t FINAL_TRIES = 10;  // "stop": attempts per chunk before giving up

struct Command {
  enum Kind { NONE, IDLE, STREAM, STOP } kind;
  String id;
};

WiFiClient wifiClient;
HTTPClient http;
String serverIp;
String streamId;  // "" = idle; "m<N>" = mic test monitor; otherwise a recording_id
uint32_t seqNo = 0;
uint8_t chunk[MAX_CHUNK_BYTES];
size_t chunkLen = 0;
bool chunkPending = false;  // `chunk` holds data the server has not accepted yet
uint32_t uploadErrors = 0;

leds::Status streamStatus() {
  if (streamId.isEmpty()) return leds::Status::Idle;
  return streamId.startsWith("m") ? leds::Status::Monitor : leds::Status::Recording;
}

bool resolveServer() {
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
int request(const String &uri, const uint8_t *body, size_t len, String &reply) {
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

String deviceQuery() {
  return String("rssi=") + static_cast<int>(WiFi.RSSI()) + "&fw=" FW_VERSION "&buf=" +
         String(static_cast<float>(audio::ringBytes()) / audio::BYTES_PER_SEC, 1) + "&up=" + (millis() / 1000);
}

Command parseCommand(const String &line) {
  if (line == "idle") return {Command::IDLE, ""};
  if (line.startsWith("stream ")) return {Command::STREAM, line.substring(7)};
  if (line.startsWith("stop ")) return {Command::STOP, line.substring(5)};
  return {Command::NONE, ""};
}

void beginStream(const String &id) {
  if (!streamId.isEmpty()) Serial.printf("Voog %s katkestatud\n", streamId.c_str());
  audio::setCapturing(false);
  audio::clear();  // a new stream starts from "now", not from old buffered audio
  streamId = id;
  seqNo = 0;
  audio::consumeDropped(audio::dropped());
  chunkPending = false;
  uploadErrors = 0;
  audio::setCapturing(true);
  leds::setStatus(streamStatus());
  Serial.printf("Voog %s algas (%s)\n", id.c_str(), id.startsWith("m") ? "mikrofoni test" : "salvestus");
}

void endStream() {
  audio::setCapturing(false);
  audio::clear();
  streamId = "";
  chunkPending = false;
  leds::setStatus(leds::Status::Idle);
}

// One upload attempt of `chunk`. True when the server took it (or rejected it for good).
bool postChunk(bool final, String &reply) {
  uint32_t dropped = audio::dropped();
  String uri = String("/api/esp32/chunk?stream=") + streamId + "&seq=" + seqNo + "&dropped=" + dropped +
               "&final=" + (final ? "1" : "0") + "&" + deviceQuery();
  int code = request(uri, chunk, chunkLen, reply);
  if (code == 200) {
    audio::consumeDropped(dropped);
    seqNo++;
    chunkPending = false;
    if (uploadErrors) {
      Serial.printf("Üleslaadimine taastus (%u ebaõnnestunud katset)\n", static_cast<unsigned>(uploadErrors));
    }
    uploadErrors = 0;
    leds::setStatus(streamStatus());
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
                  static_cast<float>(audio::available()) / audio::BYTES_PER_SEC);
  }
  leds::setStatus(leds::Status::UploadError);
  return false;
}

// "stop <id>": stop capturing, send everything still buffered (last chunk final=1), go idle.
void finishStream() {
  audio::setCapturing(false);
  for (;;) {
    if (!chunkPending) {
      chunkLen = audio::read(chunk, MAX_CHUNK_BYTES);
      chunkPending = true;
    }
    bool final = audio::available() == 0;
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

void applyCommand(const Command &cmd) {
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

void streamStep() {
  if (!chunkPending) {
    size_t avail = audio::available();
    if (avail < CHUNK_BYTES) {
      delay(10);
      return;
    }
    chunkLen = audio::read(chunk, avail < MAX_CHUNK_BYTES ? avail : MAX_CHUNK_BYTES);
    chunkPending = true;
  }
  String reply;
  if (postChunk(false, reply)) {
    applyCommand(parseCommand(reply));
  } else {
    delay(RETRY_DELAY_MS);  // WiFi stall: audio keeps collecting in the ring meanwhile
  }
}

void idleStep() {
  static uint32_t lastPoll = 0;
  static uint32_t failures = 0;
  static bool serverOk = false;
  if (millis() - lastPoll < POLL_MS) {
    delay(5);
    return;
  }
  lastPoll = millis();
  if (WiFi.status() != WL_CONNECTED) {
    leds::setStatus(leds::Status::Wifi);
    return;
  }
  String reply;
  int code = resolveServer() ? request(String("/api/esp32/poll?") + deviceQuery(), nullptr, 0, reply) : -1;
  if (code != 200) {
    leds::setStatus(leds::Status::NoServer);
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
  leds::setStatus(leds::Status::Idle);
  applyCommand(parseCommand(reply));
}

void watchWifi() {
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

}  // namespace

void begin() {
  Serial.printf("Režiim: koguja WiFi mikrofon, server %s:%d\n", SERVER_HOST, SERVER_PORT);
  leds::setStatus(leds::Status::Wifi);
  WiFi.mode(WIFI_STA);
  WiFi.setHostname("voicestrip-mic");
  WiFi.setSleep(false);  // modem sleep adds latency and stalls the upload
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("WiFi: ühendan võrku \"%s\"...\n", WIFI_SSID);
}

void step() {
  watchWifi();
  if (streamId.isEmpty()) {
    idleStep();
  } else {
    streamStep();
  }
}

}  // namespace collector

#endif  // COLLECTOR_MODE
