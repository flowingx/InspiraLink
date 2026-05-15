# AGENTS.md

本文件给后续协作者和智能体使用，用来快速理解灵息 / InspiraLink 当前项目状态、运行边界和开发注意事项。请优先以当前代码为准，文档中若有历史描述不一致，应回到 `sleep_assist/app.py` 核对实际行为。

## 项目定位

- 中文名：灵息。
- 英文名：InspiraLink。
- 项目性质：课程实践与半实物验证原型。
- 核心目标：用 Raspberry Pi 读取呼吸相关传感器数据，在 Web 仪表盘中实时展示，并用舵机挤压球囊做台架上的低风险辅助泵气演示。
- 当前阶段：Phase 1 实测版，重点是 BMP280 压差、可选 AHT20 温湿度、GPIO18 舵机、GPIO27 LED。
- MAX30102 暂未接入。SpO2 和心率字段保留为 `None`/禁用状态，不允许生成假血氧或假心率数据。

## 安全边界

- 本项目不是医疗器械，不能用于真实临床治疗、生命支持、急救或病人护理。
- 自动泵气只允许台架演示，不应连接真实人体气道。
- 呼吸辅助设备属于高风险医疗设备范畴，真实应用需要法规认证、临床验证、电气安全、气路安全和故障保护。
- 舵机动作前必须确认球囊、连杆、限位和供电安全。MG996R 等舵机必须使用独立 5V/3A 电源，并与树莓派共地，严禁直接从树莓派 5V 引脚给大力矩舵机供电。
- 对硬件测试命令要谨慎：`--test servo`、`--test led`、`--test all` 会操作真实硬件；`--test routes` 不访问硬件。

## 推荐硬件结构

- 主控：Raspberry Pi 4B/5。
- 压力传感器：BMP280，I2C。
- 温湿度传感器：AHT20，I2C，可选但推荐用于面罩呼气孔外夹式采样结构。
- 执行器：优先 MG996R；SG90 仅适合空载或轻载演示。
- 指示灯：LED 接 GPIO27。
- 舵机 PWM：GPIO18。
- I2C：GPIO2 SDA，GPIO3 SCL。

面罩安装建议：

- 不要给氧气面罩开孔。
- 不使用鼻管作为主要方案，因为鼻管容易错位。
- 推荐把 BMP280/AHT20 放进外置浅采样腔，夹在氧气面罩原有呼气孔外侧。
- 夹具只覆盖一个呼气孔或半覆盖一组呼气孔，不能堵死排气。
- 传感器线束应从杜邦线升级为焊接小转接板、带锁扣线或固定线束，沿面罩边缘/绑带走线。

## 代码结构

```text
.
├── AGENTS.md
├── README.md
├── plan.md
├── LICENSE
└── sleep_assist/
    ├── app.py
    ├── README.md
    └── templates/
        └── index.html
```

关键文件：

- `sleep_assist/app.py`：Flask 后端、硬件初始化、BMP280/AHT20 读取、呼吸活动判定、舵机/LED 控制、接口和测试命令。
- `sleep_assist/templates/index.html`：ECharts 实时仪表盘、参数控制、硬件状态、事件日志。
- `README.md`：面向项目展示的总说明。
- `plan.md`：半实物验证方案说明，部分早期内容可能比 `app.py` 落后，修改前请核对。

## 后端架构

当前后端采用“后台硬件线程 + Flask API + 前端轮询”的结构：

- 启动 Flask 时会调用 `start_background_threads()`，后台线程运行 `monitor_loop()`。
- `monitor_loop()` 会先执行 `init_hardware()`。
- `init_hardware()` 会初始化 GPIO、舵机、LED、BMP280，并尝试初始化 AHT20。
- 如果 BMP280 或 GPIO 初始化失败，系统进入 `hardware_error`，不会生成模拟压力波形。
- 如果 AHT20 不可用，系统进入 pressure-only 模式，记录真实错误，不造假温湿度数据。
- 全局 `system_state` 和 `config` 分别由锁保护。

## 传感器实现

### BMP280

- 类：`HardwarePressureReader`。
- 优先使用 Pimoroni `bmp280` 包：
  - `from bmp280 import BMP280`
  - `from smbus2 import SMBus`
  - `BMP280.get_pressure()` 返回 hPa，代码中乘以 100 转为 Pa。
- 兼容尝试 `bme280` 包作为 fallback。
- 默认尝试 I2C 地址 `0x76` 和 `0x77`。
- 初始化后会 warmup 多次读数，避免第一次读数异常。

### AHT20

- 类：`OptionalAHT20Reader`。
- 不依赖 `adafruit_ahtx0`。
- 使用 `smbus2` 直接 I2C 读写。
- 默认地址：`0x38`。
- 初始化命令：`0xBE 0x08 0x00`。
- 软复位命令：`0xBA`。
- 测量命令：`0xAC 0x33 0x00`。
- 校准判断参考商家资料 `ATH20.c/.h`：`(status & 0x68) == 0x08`。
- 读数支持 7 字节带 CRC，也兼容 6 字节数据。
- CRC、busy 超时、读数范围异常都会抛出真实错误。

### MAX30102

- 暂未接入。
- `spo2`、`heart_rate` 保持为 `None`。
- `spo2_status` 为 `disabled_not_connected`。
- 不得用随机数或固定假值填充 SpO2/心率。

## 呼吸判定逻辑

当前默认 `assist_mode` 是 `apnea_only`：

- 正常呼吸活动只记录、清除呼吸暂停报警，不驱动舵机。
- 只有超过自适应呼吸暂停时间后，才请求台架辅助泵气。

主要参数在 `DEFAULT_CONFIG` 中：

- `sample_hz`: 默认 20Hz。
- `calibration_seconds`: 默认 10 秒。
- `breath_window_seconds`: 呼吸活动窗口，默认 4 秒。
- `min_breath_activity_pa`: 压差活动最小阈值，默认 1.5 Pa。
- `humidity_activity_threshold`: 湿度活动阈值，默认 1.0%。
- `temperature_activity_threshold`: 温度活动阈值，默认 0.15 C。
- `cooldown_seconds`: 呼吸触发冷却，默认 2 秒。
- `apnea_min_seconds`: 呼吸暂停最短判定时间，默认 12 秒。
- `apnea_max_seconds`: 呼吸暂停最长判定时间，默认 25 秒。
- `adaptive_apnea_factor`: 用近期呼吸间隔估算暂停阈值的倍率，默认 2.5。
- `assist_interval_seconds`: 呼吸暂停期间辅助泵气间隔，默认 5 秒。
- `pump_artifact_ignore_seconds`: 泵气后压差屏蔽时间，默认 2 秒。
- `post_pump_recovery_seconds`: 泵气后恢复观察期，默认 8 秒。
- `post_pump_threshold_factor`: 泵气后阈值降低倍率，默认 0.65。

判定过程：

- 启动后进入校准阶段，收集压力样本，估计基准和噪声。
- 压力经 5 点滑动平均。
- 基准由滚动窗口估计，泵气屏蔽期内暂停更新基准。
- 呼吸活动不只看单个负压尖峰，而看窗口内压差峰峰值。
- 若 AHT20 可用，湿度活动和温度活动也能参与呼吸活动判定。
- 检测到呼吸后会记录时间、估算呼吸频率，并用近期呼吸间隔更新自适应暂停时间。
- 泵气动作后会进入压差忽略期，避免舵机/球囊动作造成的气压扰动被误判为呼吸。

## 执行控制

- `execute_pump()` 控制舵机从复位角运动到压下角，保持 `pump_hold_seconds` 后回位。
- 默认复位角：30 度。
- 默认压下角：105 度。
- 默认保持时间：0.55 秒。
- 有 `pump_cooldown_seconds` 冷却限制。
- 若 `servo_error` 或 `hardware_init_error` 报警存在，会阻止泵气。
- `/pump_test` 只在当前 Phase 1 模式下可用，并受最大次数限制。

## Flask 接口

- `GET /`：返回仪表盘页面。
- `GET /data`：返回 `system_state` 快照。
- `GET /config`：返回当前配置。
- `POST /config`：更新允许修改的配置字段。
- `POST /calibrate`：重新校准压力基准和状态。
- `POST /pump_test`：执行一次受限手动泵气测试，会动真实舵机。
- `POST /led_test`：执行一次 LED 测试，会点亮真实 LED。
- `GET /logs`：返回事件日志。
- 运行事件会持久化到 `sleep_assist/runtime_logs/events.jsonl`。如果 Flask 进程已经停止，仍可通过该文件复盘最近事件；`/logs` 会合并内存事件和文件中的最近事件。

## 前端仪表盘

- 文件：`sleep_assist/templates/index.html`。
- 使用 ECharts CDN：`https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js`。
- 前端每 250ms 请求一次 `/data`。
- 展示内容包括：
  - 压差曲线。
  - 正/负活动阈值线。
  - 呼吸状态、距上次呼吸、自适应暂停判定时间。
  - AHT20 湿度、湿度活动、温度活动。
  - 舵机状态、报警状态、硬件连接、事件日志。
- 参数控制区可以更新部分 `DEFAULT_CONFIG` 字段。

注意：AHT20 环境数据卡片应标为 `AHT20 温湿度` 或类似文案，不要误写成 `MAX30102`。

## 运行方式

在树莓派项目目录运行：

```bash
cd /home/xzy/wk/InspiraLink
python3 sleep_assist/app.py
```

局域网访问：

```text
http://10.176.40.66:5000
```

本地只做接口 smoke test：

```bash
python sleep_assist/app.py --test routes
```

模块测试：

```bash
python3 sleep_assist/app.py --test pressure
python3 sleep_assist/app.py --test environment
python3 sleep_assist/app.py --test led
python3 sleep_assist/app.py --test servo
python3 sleep_assist/app.py --test all
```

测试安全性说明：

- `pressure`：只读 BMP280。
- `environment`：只读 AHT20。
- `routes`：只测 Flask 路由，不访问硬件。
- `led`：会闪烁 GPIO27 LED。
- `servo`：会移动 GPIO18 舵机。
- `all`：会依次读传感器并动作 LED/舵机，不要随手运行。

## 依赖和环境

树莓派上已知需要：

- Python 3。
- Flask。
- gpiozero。
- pigpio，并需要启动 `pigpiod`。
- smbus2。
- Pimoroni `bmp280` 包。
- 可选 fallback：`bme280`。

AHT20 不需要 `adafruit_ahtx0`，也不应重新引入该依赖。

## 开发注意事项

- 不要恢复模拟压力波形或假 SpO2/心率。
- 不要把 MAX30102 数据接入舵机控制，除非经过明确设计和验证。当前原则是 SpO2 只做趋势和报警参考。
- 后续 MAX30102 到货后，推荐用真实 SpO2 趋势评估泵气有效性：多次泵气后 SpO2 仍低或下降，应升级报警并触发轻度唤醒提示；SpO2 逐步恢复到安全范围，则暂停泵气并回到观察状态。不要用单个低血氧瞬时值直接驱动舵机。
- 修改呼吸判定时，要考虑慢呼吸、弱呼吸、呼气正压、吸气负压、泵气伪影、基准漂移和面罩安装距离。
- 修改泵气逻辑时，要保留冷却、报警阻断、泵后压差屏蔽和恢复观察期。
- 修改 AHT20 读法前，先确认商家资料中的 `ATH20.c/.h` 协议，不要直接套用不可维护的第三方库。
- 前端依赖 CDN 加载 ECharts；如果答辩环境无网络，需改成本地静态文件。
- 在没有真实树莓派硬件时，只运行 `--test routes` 和语法检查，不要假装硬件测试通过。

## 推荐验证命令

本地代码检查：

```bash
python -m py_compile sleep_assist/app.py
python sleep_assist/app.py --test routes
```

树莓派安全读数测试：

```bash
python3 sleep_assist/app.py --test pressure
python3 sleep_assist/app.py --test environment
```

硬件动作测试必须人工确认机械结构和供电安全后再运行：

```bash
python3 sleep_assist/app.py --test led
python3 sleep_assist/app.py --test servo
```

## 已知限制和后续方向

- AHT20 需要在真实硬件上进一步验证 6/7 字节读法、CRC 和实际响应。
- 前端环境数据卡片已改为 AHT20 温湿度；若后续新增 MAX30102，应单独增加血氧/心率卡片，不要复用 AHT20 卡片。
- `plan.md` 中部分接口字段仍保留早期 `min_inhale_delta_pa`、`watchdog_seconds` 等旧名，后续应同步为当前配置字段。
- MAX30102 到货后需要新增真实驱动、测试函数和趋势显示；仍不得使用假数据。
- 若要稳定答辩演示，建议把 ECharts 从 CDN 改为本地静态资源。
- 面罩外夹式采样结构需要实物测试，重点记录距离、角度、湿度响应、压差响应和泵气伪影。
