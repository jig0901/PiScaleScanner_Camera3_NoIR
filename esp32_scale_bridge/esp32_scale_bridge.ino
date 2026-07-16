#include <Arduino.h>
#include <Preferences.h>

constexpr int HX_DOUT = 14;
constexpr int HX_SCK = 21;
constexpr uint32_t READY_TIMEOUT_MS = 1200;
Preferences prefs;
int32_t offset = 0;
float referenceUnit = 1.0f;
bool calibrated = false;

bool waitReady(uint32_t timeoutMs = READY_TIMEOUT_MS) {
  uint32_t start = millis();
  while (digitalRead(HX_DOUT) == HIGH) {
    if (millis() - start >= timeoutMs) return false;
    delay(1);
  }
  return true;
}

void resetHx711() {
  digitalWrite(HX_SCK, HIGH);
  delayMicroseconds(80);
  digitalWrite(HX_SCK, LOW);
  delay(120);
}

bool readRaw(int32_t &result) {
  if (!waitReady()) {
    resetHx711();
    if (!waitReady()) return false;
  }
  uint32_t value = 0;
  noInterrupts();
  for (int i = 0; i < 24; ++i) {
    digitalWrite(HX_SCK, HIGH);
    delayMicroseconds(1);
    value = (value << 1) | digitalRead(HX_DOUT);
    digitalWrite(HX_SCK, LOW);
    delayMicroseconds(1);
  }
  digitalWrite(HX_SCK, HIGH);  // Channel A, gain 128 for the next conversion.
  delayMicroseconds(1);
  digitalWrite(HX_SCK, LOW);
  interrupts();
  if (value & 0x800000UL) value |= 0xFF000000UL;
  result = static_cast<int32_t>(value);
  return true;
}

void sortValues(int32_t *values, int count) {
  for (int i = 1; i < count; ++i) {
    int32_t key = values[i];
    int j = i - 1;
    while (j >= 0 && values[j] > key) { values[j + 1] = values[j]; --j; }
    values[j + 1] = key;
  }
}

bool filteredRaw(int32_t &result, int count = 7) {
  int32_t values[15];
  count = constrain(count, 3, 15);
  for (int i = 0; i < count; ++i) if (!readRaw(values[i])) return false;
  sortValues(values, count);
  int64_t total = 0;
  for (int i = 1; i < count - 1; ++i) total += values[i];
  result = total / (count - 2);
  return true;
}

void reply(const char *type, const char *cmd, long id, const char *extra = nullptr) {
  Serial.printf("{\"type\":\"%s\",\"cmd\":\"%s\",\"id\":%ld", type, cmd, id);
  if (extra) Serial.printf(",%s", extra);
  Serial.println("}");
}

void processCommand(String line) {
  line.trim();
  int first = line.indexOf(' ');
  String cmd = first < 0 ? line : line.substring(0, first);
  String rest = first < 0 ? "" : line.substring(first + 1);
  cmd.toUpperCase();
  long id = rest.toInt();
  if (cmd == "CALIBRATE") {
    int split = rest.indexOf(' ');
    float grams = rest.substring(0, split).toFloat();
    id = rest.substring(split + 1).toInt();
    int32_t raw;
    if (grams <= 0 || !filteredRaw(raw, 15) || abs(raw - offset) < 100) {
      reply("error", "calibrate", id, "\"error\":\"Calibration failed; check weight and HX711 wiring\"");
      return;
    }
    referenceUnit = float(raw - offset) / grams;
    calibrated = true;
    prefs.putInt("offset", offset);
    prefs.putFloat("reference", referenceUnit);
    prefs.putBool("calibrated", true);
    char extra[96];
    snprintf(extra, sizeof(extra), "\"offset\":%ld,\"reference_unit\":%.6f", long(offset), referenceUnit);
    reply("ack", "calibrate", id, extra);
  } else if (cmd == "TARE") {
    int32_t raw;
    if (!filteredRaw(raw, 15)) {
      reply("error", "tare", id, "\"error\":\"HX711 DOUT stayed high\""); return;
    }
    offset = raw;
    prefs.putInt("offset", offset);
    char extra[48]; snprintf(extra, sizeof(extra), "\"offset\":%ld", long(offset));
    reply("ack", "tare", id, extra);
  } else if (cmd == "START" || cmd == "PING") {
    reply("ack", cmd == "START" ? "start" : "ping", id);
  } else {
    reply("error", "unknown", id, "\"error\":\"Unknown command\"");
  }
}

void setup() {
  pinMode(HX_DOUT, INPUT);
  pinMode(HX_SCK, OUTPUT);
  digitalWrite(HX_SCK, LOW);
  Serial.begin(115200);
  prefs.begin("scale", false);
  offset = prefs.getInt("offset", 0);
  referenceUnit = prefs.getFloat("reference", 1.0f);
  calibrated = prefs.getBool("calibrated", false);
  resetHx711();
}

void loop() {
  static String input;
  static uint32_t lastReading = 0;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { processCommand(input); input = ""; }
    else if (c != '\r' && input.length() < 120) input += c;
  }
  if (millis() - lastReading >= 200) {
    lastReading = millis();
    int32_t raw;
    if (filteredRaw(raw, 5)) {
      float grams = calibrated ? float(raw - offset) / referenceUnit : 0.0f;
      Serial.printf("{\"type\":\"reading\",\"raw\":%ld,\"weight_g\":%.2f,\"offset\":%ld,\"reference_unit\":%.6f,\"mode\":\"scale\",\"calibrated\":%s}\n",
                    long(raw), grams, long(offset), referenceUnit, calibrated ? "true" : "false");
    } else {
      Serial.println("{\"type\":\"reading\",\"mode\":\"error\",\"error\":\"HX711 DOUT stayed high; check power and wiring\"}");
    }
  }
}
