/*
  ESP32-C3 Codex 三色红绿灯控制程序

  硬件连接：
  - 红灯：GPIO0
  - 黄灯：GPIO2
  - 绿灯：GPIO1

  串口协议：
  以 115200 波特率发送一行字符串，支持：
  idle
  thinking
  ai
  success
  busy
  wait_confirm
  confirm
  waiting
  wait
  error
  off

  兼容旧版命令：
  writing -> ai
  running -> busy
  done    -> success
*/

#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

// 如果你的灯板是高电平点亮，把这里改成 1。
#define LED_ACTIVE_HIGH 0

// GPIO 修改集中放这里，后续换线只需要改这三个定义。
// 实测（GPIO 扫描确认）：
//   - 红灯 GPIO21
//   - 黄灯 GPIO2（不是源码原来的 GPIO10；GPIO10 上没接 LED）
//   - 绿灯 GPIO20
// 注意：客户使用说明里写的「红=GPIO0 / 黄=GPIO2 / 绿=GPIO1」只有黄灯是对的，
//       GPIO0 是 strapping pin，GPIO1 也无 LED。原始 .ino 把黄灯写成 GPIO10 是错的。
#define RED_LED_PIN 21
#define YELLOW_LED_PIN 2
#define GREEN_LED_PIN 20

// 每个灯独立使用一个 PWM 通道，限制最大占空比以降低 GPIO 电流。
const uint8_t RED_LED_CHANNEL = 0;
const uint8_t YELLOW_LED_CHANNEL = 1;
const uint8_t GREEN_LED_CHANNEL = 2;

const uint32_t LED_PWM_FREQ = 1000;
const uint8_t LED_PWM_RESOLUTION = 8;
const uint8_t LED_PWM_MAX = (1 << LED_PWM_RESOLUTION) - 1;
const uint8_t LED_PWM_LIMIT = 140;       // 约 55% 占空比
const uint8_t LED_PWM_SOFT = 84;         // 约 33% 占空比
const uint8_t LED_PWM_TRAIL = 28;        // 柔和拖尾亮度
const uint8_t LED_BREATH_MIN = 6;

const unsigned long IDLE_BREATH_STEP_MS = 14;
const unsigned long THINKING_CHASE_INTERVAL_MS = 110;
const unsigned long AI_CHASE_INTERVAL_MS = 240;
const unsigned long BUSY_BLINK_INTERVAL_MS = 550;
const unsigned long SUCCESS_HOLD_MS = 5000;
const unsigned long ERROR_BLINK_INTERVAL_MS = 130;
const unsigned long UNKNOWN_BLINK_INTERVAL_MS = BUSY_BLINK_INTERVAL_MS;  // unknown 三色齐闪：和 busy 同节奏

const char *BLE_DEVICE_NAME = "AgentCore-Light";
const char *BLE_SERVICE_UUID = "12345678-1234-5678-1234-56789abcdef0";
const char *BLE_CHARACTERISTIC_UUID = "12345678-1234-5678-1234-56789abcdef1";

enum LightState {
  STATE_IDLE,
  STATE_THINKING,
  STATE_AI,
  STATE_BUSY,
  STATE_SUCCESS,
  STATE_WAIT_CONFIRM,
  STATE_CONFIRM,
  STATE_WAITING,
  STATE_WAIT,
  STATE_ERROR,
  STATE_UNKNOWN,    // server 不确定 Agent 状态（长时间无 hook 事件）
  STATE_OFF
};

// 状态枚举 → 字符串（用于日志）
const char* stateName(LightState s) {
  switch (s) {
    case STATE_IDLE:         return "idle";
    case STATE_THINKING:     return "thinking";
    case STATE_AI:           return "ai";
    case STATE_BUSY:         return "busy";
    case STATE_SUCCESS:      return "success";
    case STATE_WAIT_CONFIRM: return "wait_confirm";
    case STATE_CONFIRM:      return "confirm";
    case STATE_WAITING:      return "waiting";
    case STATE_WAIT:         return "wait";
    case STATE_ERROR:        return "error";
    case STATE_UNKNOWN:      return "unknown";
    case STATE_OFF:          return "off";
    default:                 return "?";
  }
}

// 固件日志级别：
//   0 = 关（生产模式，最小串口负载）
//   1 = INFO（状态变化、BLE 连接、effect 帧切换）— 推荐
//   2 = TRACE（每次 setLightLevels 都打印实际 PWM duty）— 仅深度 debug 时用
#define FIRMWARE_LOG_LEVEL 1

#define FW_LOG_LEVEL_NONE  0
#define FW_LOG_LEVEL_INFO  1
#define FW_LOG_LEVEL_TRACE 2

#if FIRMWARE_LOG_LEVEL >= FW_LOG_LEVEL_INFO
  #define FW_INFO(msg)            do { Serial.print("[FW] "); Serial.println(msg); } while (0)
  #define FW_INFO_NUM(label, val) do { Serial.print("[FW] "); Serial.print(label); Serial.println(val); } while (0)
#else
  #define FW_INFO(msg)            do {} while (0)
  #define FW_INFO_NUM(label, val) do {} while (0)
#endif

#if FIRMWARE_LOG_LEVEL >= FW_LOG_LEVEL_TRACE
  #define FW_TRACE(msg)            do { Serial.print("[FW] "); Serial.println(msg); } while (0)
  #define FW_TRACE_NUM(label, val) do { Serial.print("[FW] "); Serial.print(label); Serial.println(val); } while (0)
#else
  #define FW_TRACE(msg)            do {} while (0)
  #define FW_TRACE_NUM(label, val) do {} while (0)
#endif

LightState currentState = STATE_IDLE;
String serialBuffer;
BLEServer *bleServer = nullptr;

unsigned long stateStartMs = 0;
unsigned long lastEffectFrameMs = 0;
uint8_t chaseIndex = 0;
bool blinkOn = false;
int breathBrightness = LED_BREATH_MIN;
int breathStep = 2;

uint8_t brightnessToDuty(uint8_t brightness) {
  uint8_t limited = brightness;
  if (limited > LED_PWM_LIMIT) {
    limited = LED_PWM_LIMIT;
  }

#if LED_ACTIVE_HIGH
  return limited;
#else
  return LED_PWM_MAX - limited;
#endif
}

void writeLed(uint8_t channel, uint8_t brightness) {
  ledcWriteChannel(channel, brightnessToDuty(brightness));
}

void setLightLevels(uint8_t red, uint8_t yellow, uint8_t green) {
  writeLed(RED_LED_CHANNEL, red);
  writeLed(YELLOW_LED_CHANNEL, yellow);
  writeLed(GREEN_LED_CHANNEL, green);
#if FIRMWARE_LOG_LEVEL >= FW_LOG_LEVEL_TRACE
  // TRACE 级别：每次 setLightLevels 都打印（用于深度 debug「灯为什么不亮」）
  Serial.print("[FW][TRACE] setLight r=");
  Serial.print(red);
  Serial.print(" y=");
  Serial.print(yellow);
  Serial.print(" g=");
  Serial.print(green);
  Serial.print(" | raw_duty r=");
  Serial.print(brightnessToDuty(red));
  Serial.print(" y=");
  Serial.print(brightnessToDuty(yellow));
  Serial.print(" g=");
  Serial.print(brightnessToDuty(green));
  Serial.println();
#endif
}

void setLight(bool red, bool yellow, bool green) {
  setLightLevels(
    red ? LED_PWM_LIMIT : 0,
    yellow ? LED_PWM_LIMIT : 0,
    green ? LED_PWM_LIMIT : 0
  );
}

void resetEffectState() {
  lastEffectFrameMs = 0;
  chaseIndex = 0;
  blinkOn = false;
  breathBrightness = LED_BREATH_MIN;
  breathStep = 2;
}

void enterState(LightState newState) {
  LightState oldState = currentState;
  currentState = newState;
  stateStartMs = millis();
  resetEffectState();
  setLight(false, false, false);

#if FIRMWARE_LOG_LEVEL >= FW_LOG_LEVEL_INFO
  // INFO 级别：状态机切换（核心日志，用于复盘）
  Serial.print("[FW][INFO] state: ");
  Serial.print(stateName(oldState));
  Serial.print(" -> ");
  Serial.print(stateName(newState));
  Serial.print(" @ ms=");
  Serial.println(stateStartMs);
#endif
}

bool setStatus(String status) {
  status.trim();
  status.toLowerCase();

  if (status.length() == 0) {
    return false;
  }

  if (status == "idle") {
    enterState(STATE_IDLE);
  } else if (status == "thinking") {
    enterState(STATE_THINKING);
  } else if (status == "ai" || status == "writing") {
    enterState(STATE_AI);
  } else if (status == "busy" || status == "running") {
    enterState(STATE_BUSY);
  } else if (status == "success" || status == "done") {
    enterState(STATE_SUCCESS);
  } else if (status == "wait_confirm") {
    enterState(STATE_WAIT_CONFIRM);
  } else if (status == "confirm") {
    enterState(STATE_CONFIRM);
  } else if (status == "waiting") {
    enterState(STATE_WAITING);
  } else if (status == "wait") {
    enterState(STATE_WAIT);
  } else if (status == "error") {
    enterState(STATE_ERROR);
  } else if (status == "unknown" || status == "uncertain") {
    enterState(STATE_UNKNOWN);
  } else if (status == "off") {
    enterState(STATE_OFF);
  } else {
    return false;
  }

  return true;
}

String extractJsonStringValue(const String &json, const char *key) {
  String pattern = "\"";
  pattern += key;
  pattern += "\"";

  int keyIndex = json.indexOf(pattern);
  if (keyIndex < 0) {
    return "";
  }

  int colonIndex = json.indexOf(':', keyIndex + pattern.length());
  if (colonIndex < 0) {
    return "";
  }

  int valueStart = colonIndex + 1;
  while (valueStart < json.length() && isspace(static_cast<unsigned char>(json[valueStart]))) {
    valueStart++;
  }

  if (valueStart >= json.length() || json[valueStart] != '"') {
    return "";
  }

  valueStart++;
  String value = "";

  for (int i = valueStart; i < json.length(); ++i) {
    char c = json[i];
    if (c == '\\' && i + 1 < json.length()) {
      i++;
      value += json[i];
      continue;
    }
    if (c == '"') {
      return value;
    }
    value += c;
  }

  return "";
}

class LightBleServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *server) override {
    Serial.println("BLE client connected.");
  }

  void onDisconnect(BLEServer *server) override {
    Serial.println("BLE client disconnected.");
    server->startAdvertising();
    Serial.println("BLE advertising restarted.");
  }
};

class LightBleCharacteristicCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *characteristic) override {
    String payload = characteristic->getValue();
    if (payload.length() == 0) {
      return;
    }

    String status = extractJsonStringValue(payload, "status");
    if (status.length() == 0) {
      Serial.print("BLE JSON missing status: ");
      Serial.println(payload);
      return;
    }

    if (!setStatus(status)) {
      Serial.print("Unknown BLE status: ");
      Serial.println(status);
      return;
    }

    Serial.print("BLE status changed to: ");
    Serial.println(status);
  }
};

void setupBle() {
  BLEDevice::init(BLE_DEVICE_NAME);

  bleServer = BLEDevice::createServer();
  bleServer->setCallbacks(new LightBleServerCallbacks());

  BLEService *service = bleServer->createService(BLE_SERVICE_UUID);
  BLECharacteristic *statusCharacteristic = service->createCharacteristic(
    BLE_CHARACTERISTIC_UUID,
    BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
  );

  statusCharacteristic->setCallbacks(new LightBleCharacteristicCallbacks());
  service->start();

  BLEAdvertising *advertising = bleServer->getAdvertising();
  advertising->addServiceUUID(BLE_SERVICE_UUID);
  advertising->start();

  Serial.print("BLE advertising as: ");
  Serial.println(BLE_DEVICE_NAME);
}

bool effectFrameDue(unsigned long intervalMs) {
  unsigned long now = millis();

  if (lastEffectFrameMs == 0 || now - lastEffectFrameMs >= intervalMs) {
    lastEffectFrameMs = now;
    return true;
  }

  return false;
}

void showThinkingChaseFrame() {
  switch (chaseIndex) {
    case 0:
      setLightLevels(LED_PWM_LIMIT, 0, 0);
      break;
    case 1:
      setLightLevels(LED_PWM_LIMIT, LED_PWM_LIMIT, 0);
      break;
    case 2:
      setLightLevels(0, LED_PWM_LIMIT, 0);
      break;
    case 3:
      setLightLevels(0, LED_PWM_LIMIT, LED_PWM_LIMIT);
      break;
    case 4:
      setLightLevels(0, 0, LED_PWM_LIMIT);
      break;
    default:
      setLightLevels(LED_PWM_LIMIT, 0, LED_PWM_LIMIT);
      break;
  }

  chaseIndex = (chaseIndex + 1) % 6;
}

void showAiChaseFrame() {
  switch (chaseIndex) {
    case 0:
      setLightLevels(LED_PWM_SOFT, LED_PWM_TRAIL, 0);
      break;
    case 1:
      setLightLevels(LED_PWM_TRAIL, LED_PWM_SOFT, 0);
      break;
    case 2:
      setLightLevels(0, LED_PWM_SOFT, LED_PWM_TRAIL);
      break;
    case 3:
      setLightLevels(0, LED_PWM_TRAIL, LED_PWM_SOFT);
      break;
    case 4:
      setLightLevels(LED_PWM_TRAIL, 0, LED_PWM_SOFT);
      break;
    default:
      setLightLevels(LED_PWM_SOFT, 0, LED_PWM_TRAIL);
      break;
  }

  chaseIndex = (chaseIndex + 1) % 6;
}

void updateIdleBreathing() {
  if (!effectFrameDue(IDLE_BREATH_STEP_MS)) {
    return;
  }

  breathBrightness += breathStep;
  if (breathBrightness >= LED_PWM_LIMIT) {
    breathBrightness = LED_PWM_LIMIT;
    breathStep = -2;
  } else if (breathBrightness <= LED_BREATH_MIN) {
    breathBrightness = LED_BREATH_MIN;
    breathStep = 2;
  }

  setLightLevels(0, 0, (uint8_t)breathBrightness);
}

void updateEffect() {
  switch (currentState) {
    case STATE_IDLE:
      updateIdleBreathing();
      break;

    case STATE_THINKING:
      if (effectFrameDue(THINKING_CHASE_INTERVAL_MS)) {
        showThinkingChaseFrame();
      }
      break;

    case STATE_AI:
      if (effectFrameDue(AI_CHASE_INTERVAL_MS)) {
        showAiChaseFrame();
      }
      break;

    case STATE_BUSY:
      if (effectFrameDue(BUSY_BLINK_INTERVAL_MS)) {
        blinkOn = !blinkOn;
        setLightLevels(0, blinkOn ? LED_PWM_SOFT : 0, 0);
      }
      break;

    case STATE_SUCCESS:
      setLightLevels(0, 0, LED_PWM_LIMIT);
      if (millis() - stateStartMs >= SUCCESS_HOLD_MS) {
        enterState(STATE_IDLE);
      }
      break;

    case STATE_WAIT_CONFIRM:
    case STATE_CONFIRM:
    case STATE_WAITING:
    case STATE_WAIT:
      setLightLevels(0, LED_PWM_LIMIT, 0);
      break;

    case STATE_ERROR:
      if (effectFrameDue(ERROR_BLINK_INTERVAL_MS)) {
        blinkOn = !blinkOn;
        setLightLevels(blinkOn ? LED_PWM_LIMIT : 0, 0, 0);
      }
      break;

    case STATE_UNKNOWN:
      // 三色齐亮齐灭：和 busy（黄灯慢闪）同节奏，但三个灯一起闪
      // 用途：长任务期间 server 不确定 Agent 在干嘛，提示用户「去看一眼」
      if (effectFrameDue(UNKNOWN_BLINK_INTERVAL_MS)) {
        blinkOn = !blinkOn;
        uint8_t level = blinkOn ? LED_PWM_SOFT : 0;
        setLightLevels(level, level, level);
      }
      break;

    case STATE_OFF:
      setLightLevels(0, 0, 0);
      break;
  }
}

void handleCommand(String command) {
  command.trim();
  command.toLowerCase();

  if (command.length() == 0) {
    return;
  }

  if (!setStatus(command)) {
    Serial.print("Unknown state: ");
    Serial.println(command);
    return;
  }

  Serial.print("State changed to: ");
  Serial.println(command);
}

void readSerialCommands() {
  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      if (serialBuffer.length() > 0) {
        handleCommand(serialBuffer);
        serialBuffer = "";
      }
    } else if (serialBuffer.length() < 31) {
      serialBuffer += c;
    }
  }
}

void setup() {
  Serial.begin(115200);
  serialBuffer.reserve(32);

  // ESP32 Arduino core v3 API（pioarduino 平台）
  // 第一个参数是 pin，channel 也显式指定；v2 API 的 ledcSetup/ledcAttachPin 在 GPIO10 上 PWM 不工作
  bool r1 = ledcAttachChannel(RED_LED_PIN, LED_PWM_FREQ, LED_PWM_RESOLUTION, RED_LED_CHANNEL);
  bool r2 = ledcAttachChannel(YELLOW_LED_PIN, LED_PWM_FREQ, LED_PWM_RESOLUTION, YELLOW_LED_CHANNEL);
  bool r3 = ledcAttachChannel(GREEN_LED_PIN, LED_PWM_FREQ, LED_PWM_RESOLUTION, GREEN_LED_CHANNEL);
#if FIRMWARE_LOG_LEVEL >= FW_LOG_LEVEL_INFO
  Serial.print("[FW][INFO] ledcAttach: red(GPIO");
  Serial.print(RED_LED_PIN); Serial.print(",ch0)=");
  Serial.print(r1 ? "OK" : "FAIL");
  Serial.print(" | yellow(GPIO");
  Serial.print(YELLOW_LED_PIN); Serial.print(",ch1)=");
  Serial.print(r2 ? "OK" : "FAIL");
  Serial.print(" | green(GPIO");
  Serial.print(GREEN_LED_PIN); Serial.print(",ch2)=");
  Serial.println(r3 ? "OK" : "FAIL");
#endif
  setLightLevels(0, 0, 0);
  setupBle();

  enterState(STATE_IDLE);

  Serial.println("ESP32-C3 traffic light ready.");
  Serial.println("Commands: idle, thinking, ai, success, busy, wait_confirm, confirm, waiting, wait, error, unknown, off");
}

void loop() {
  readSerialCommands();
  updateEffect();
}
