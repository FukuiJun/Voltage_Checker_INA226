// M5Stack Unit INA226-1A -> Arduino -> シリアルにCSV出力
// 配線（Uno）: 黒=GND, 赤=5V, 黄=SDA(A4), 白=SCL(A5)
//
// PC からのコマンド（1 行、改行で終わる）:
//   INTERVAL <ms>   出力周期を変える（10〜1000 ms）。INA226 の平均回数は周期に合わせて自動で選ぶ。
// 応答:
//   # interval_ms=<ms> avg=<平均回数>
//   # error: <内容>
// 起動時に「# ina226_logger fw=2」と名乗る（PC 側はこれで周期変更に対応した版だと分かる）。
// リセット直後は 50 ms（平均 16 回）。データ行の形式は周期によらず同じ。

#include <Wire.h>

const uint8_t INA226_ADDR = 0x41;
const float SHUNT_OHM = 0.080;                  // 1A版のシャント抵抗
const unsigned long DEFAULT_INTERVAL_MS = 50;   // リセット直後の出力間隔
const unsigned long MIN_INTERVAL_MS = 10;
const unsigned long MAX_INTERVAL_MS = 1000;

const uint8_t REG_CONFIG = 0x00;
const uint8_t REG_SHUNT  = 0x01;
const uint8_t REG_BUS    = 0x02;
const uint8_t REG_MFG_ID = 0xFE;

// CONFIG: AVG=(可変), VBUSCT=1.1ms, VSHCT=1.1ms, 連続測定(シャント+バス)
const uint16_t CONFIG_BASE = 0x4000 | (0b100 << 6) | (0b100 << 3) | 0b111;
// AVG のビット値 0〜7 に対応する平均回数
const uint16_t AVG_COUNTS[8] = {1, 4, 16, 64, 128, 256, 512, 1024};
const float CONVERSION_MS = 1.1 * 2;  // シャント + バスの変換時間（平均 1 回あたり）

unsigned long intervalMs = DEFAULT_INTERVAL_MS;
uint16_t avgCount = 16;

char cmdBuf[32];
uint8_t cmdLen = 0;

bool writeReg(uint8_t reg, uint16_t val) {
  Wire.beginTransmission(INA226_ADDR);
  Wire.write(reg);
  Wire.write((uint8_t)(val >> 8));
  Wire.write((uint8_t)(val & 0xFF));
  return Wire.endTransmission() == 0;
}

bool readReg(uint8_t reg, uint16_t &val) {
  Wire.beginTransmission(INA226_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(INA226_ADDR, (uint8_t)2) != 2) return false;
  uint8_t hi = Wire.read();
  uint8_t lo = Wire.read();
  val = ((uint16_t)hi << 8) | lo;
  return true;
}

void scanI2C() {
  Serial.println(F("# I2C scan..."));
  uint8_t found = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      Serial.print(F("#  found 0x"));
      Serial.println(a, HEX);
      found++;
    }
  }
  if (found == 0) Serial.println(F("#  no device"));
}

// 周期を設定し、変換時間の合計が周期の 90% 以下になる最大の平均回数を選ぶ
void applyInterval(unsigned long ms) {
  uint8_t avgBits = 0;
  for (int8_t b = 7; b >= 0; b--) {
    if (AVG_COUNTS[b] * CONVERSION_MS <= ms * 0.9) {
      avgBits = b;
      break;
    }
  }
  intervalMs = ms;
  avgCount = AVG_COUNTS[avgBits];
  writeReg(REG_CONFIG, CONFIG_BASE | ((uint16_t)avgBits << 9));
}

void handleCommand(const char *cmd) {
  if (strncmp(cmd, "INTERVAL ", 9) == 0) {
    long ms = atol(cmd + 9);
    if (ms < (long)MIN_INTERVAL_MS || ms > (long)MAX_INTERVAL_MS) {
      Serial.println(F("# error: interval out of range"));
      return;
    }
    applyInterval((unsigned long)ms);
    Serial.print(F("# interval_ms="));
    Serial.print(intervalMs);
    Serial.print(F(" avg="));
    Serial.println(avgCount);
  } else if (cmd[0] != '\0') {
    Serial.println(F("# error: unknown command"));
  }
}

// 受信したコマンドを 1 文字ずつためる（測定を止めないようにブロックしない）
void pollCommand() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      cmdBuf[cmdLen] = '\0';
      handleCommand(cmdBuf);
      cmdLen = 0;
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println(F("# ina226_logger fw=2"));
  Wire.begin();

  scanI2C();

  while (true) {
    uint16_t id = 0;
    bool ok = readReg(REG_MFG_ID, id);
    if (ok && id == 0x5449) break;
    Serial.print(F("# INA226 not found (ok="));
    Serial.print(ok);
    Serial.print(F(", id=0x"));
    Serial.print(id, HEX);
    Serial.println(F(") retry..."));
    delay(1000);
  }

  applyInterval(DEFAULT_INTERVAL_MS);
  Serial.println(F("time_ms,bus_V,shunt_uV,current_mA"));
}

void loop() {
  pollCommand();

  static unsigned long last = 0;
  unsigned long now = millis();
  if (now - last < intervalMs) return;
  last = now;

  uint16_t rawShunt, rawBus;
  if (!readReg(REG_SHUNT, rawShunt) || !readReg(REG_BUS, rawBus)) {
    Serial.println(F("# read error"));
    return;
  }

  float shunt_uV   = (int16_t)rawShunt * 2.5;           // LSB 2.5uV（符号付き）
  float bus_V      = rawBus * 1.25e-3;                  // LSB 1.25mV
  float current_mA = shunt_uV / SHUNT_OHM / 1000.0;     // I = V / R

  Serial.print(now);         Serial.print(',');
  Serial.print(bus_V, 4);    Serial.print(',');
  Serial.print(shunt_uV, 1); Serial.print(',');
  Serial.println(current_mA, 3);
}
