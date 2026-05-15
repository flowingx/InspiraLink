# 灵息 / InspiraLink

居家型低成本多模态智能呼吸监控与辅助系统

## 项目命名

**中文名：灵息**

“灵”代表 AI 的灵动、感知与辅助决策能力；“息”代表呼吸、生命体征与持续看护。灵息希望表达一种更轻巧、更实时、更有温度的呼吸安全守护方式。

**英文名：InspiraLink**

`Inspira` 取自 `Inspiration`，兼具“吸气/呼吸”与“灵感”的含义；`Link` 代表连接人与设备、传感器与执行机构、监测与辅助动作。

## 项目简介

灵息 / InspiraLink 是一个面向课程实践与半实物验证的睡眠呼吸监测和辅助呼吸演示系统。系统以 Raspberry Pi 为核心，通过固定在氧气面罩呼气孔外侧的小型采样腔读取 BMP280/AHT20 的压差、湿度和温度变化，并使用舵机驱动简易球囊完成台架上的辅助泵气演示。

当前代码处于 **Phase 1：BMP280 压差 + 舵机 + LED 实测阶段**。MAX30102 还未接入，SpO2/心率相关代码已禁用，不生成假血氧数据，也不会用 SpO2 参与报警或控制。

默认辅助策略是 `apnea_only`：检测到正常呼吸活动时只记录呼吸并清除呼吸暂停报警，不驱动舵机。呼吸活动不再依赖单点尖峰，而是使用 `breath_window_seconds` 窗口内的压差峰峰值，兼容慢呼吸、弱呼吸、呼气正压和吸气负压。系统会学习用户近期呼吸间隔，并在 `apnea_min_seconds` 和 `apnea_max_seconds` 范围内自适应呼吸暂停判定时间；进入呼吸暂停后按 `assist_interval_seconds` 周期触发台架泵气。每次泵气后会在 `pump_artifact_ignore_seconds` 时间内忽略压差触发，并暂停基准更新；随后进入 `post_pump_recovery_seconds` 恢复观察期，使用更敏感的阈值捕捉被轻微唤醒后的弱呼吸。

项目参考 `radar` 示例中的架构思想，采用“后台硬件线程 + Flask API + ECharts 实时仪表盘”的方式，将传感器数据、呼吸判定、舵机状态、报警信息和调试参数集中展示，便于实验记录和答辩演示。

## 重要免责声明

本项目仅供学术研究、实验教学和原型演示使用。

- 本系统及其硬件结构、软件代码均不是医疗器械，未经过 NMPA、FDA 等监管机构认证。
- 禁止将本项目用于真实临床治疗、生命支持、急救或真实病人的日常护理。
- 所有自动泵气动作仅用于台架验证，不应连接真实人体气道。
- 任何涉及呼吸支持或护理的真实行为，必须遵循专业医师指导并使用认证医疗设备。

## 核心功能

- **多特征呼吸活动检测**：使用 BMP280 读取 Pa 级压差，并可选接入 AHT20 读取湿度/温度变化，通过窗口幅度和自适应阈值判断呼吸活动。
- **SpO2 暂不接入**：MAX30102 到货前不显示假 SpO2/心率，不参与报警或控制。
- **半实物辅助泵气演示**：使用 MG996R/SG90 舵机挤压简易呼吸球囊，默认用于无呼吸超时后的看门狗泵气演示。
- **安全互锁**：加入冷却时间、手动泵气次数限制、看门狗报警、传感器异常阻断和 LED 报警。
- **Web 实时仪表盘**：通过 Flask + ECharts 展示压差波形、吸气阈值、呼吸状态、舵机状态、报警和事件日志。

## 代码结构

```text
.
├── README.md
├── plan.md
└── sleep_assist/
    ├── app.py
    ├── README.md
    └── templates/
        └── index.html
```

## 快速运行

请在 Raspberry Pi 的项目目录运行。当前版本默认读取真实 BMP280，并控制真实舵机和 LED；如果 BMP280 读取失败，界面会显示错误，不会生成模拟波形。

```bash
cd /home/xzy/wk/InspiraLink
python3 sleep_assist/app.py
```

浏览器打开：

```text
http://127.0.0.1:5000
```

如果用手机热点局域网访问树莓派，请在同一网络下打开：

```text
http://10.176.40.66:5000
```

## 模块测试命令

每个测试都只操作当前项目代码声明的硬件模块：

```bash
python3 sleep_assist/app.py --test pressure
python3 sleep_assist/app.py --test environment
python3 sleep_assist/app.py --test led
python3 sleep_assist/app.py --test servo
python3 sleep_assist/app.py --test all
python3 sleep_assist/app.py --test routes
```

- `pressure`：连续读取 BMP280，打印气压均值、范围和标准差。
- `environment`：连续读取 AHT20，打印湿度/温度范围。若模块未连接、地址不通或 CRC 校验失败，会直接报出真实错误。
- `led`：闪烁 GPIO27 LED。
- `servo`：让 GPIO18 舵机在复位角和压下角之间动作。
- `all`：依次测试 BMP280、LED、舵机。
- `routes`：只测试 Flask 路由，不访问硬件。

BMP280/AHT20 均走 I2C。AHT20 使用项目内置的 `smbus2` 直连读写实现，协议参考商家资料中的 `ATH20.c/.h`，不依赖 `adafruit_ahtx0`。若已安装 Pimoroni `bmp280`，通常已经同时安装了 `smbus2`。

## 面罩安装建议

不建议给氧气面罩开孔，也不建议用鼻管。推荐把 BMP280/AHT20 做成外置“小夹子”，夹在面罩原有呼气孔外侧：

- 不堵死呼气孔，只覆盖一个孔或半覆盖一组孔。
- 传感器位于浅采样腔内，感受呼气孔附近的压差、湿度和温度变化。
- 使用硅胶圈、海绵圈或 3D 打印夹具贴合面罩外侧，不破坏面罩本体气密性。
- 杜邦线改为带锁扣线或焊接小转接板，线沿面罩边缘/绑带固定。

## 硬件连接

- LED: GPIO27
- 舵机 PWM: GPIO18
- I2C SDA: GPIO2
- I2C SCL: GPIO3

舵机必须使用独立 5V/3A 电源，并与 Raspberry Pi GND 共地。MG996R 高负载时电流较大，严禁直接从 Raspberry Pi 5V 引脚供电。

## Flask 接口

- `GET /data`：获取实时系统状态、压差历史和事件日志。
- `GET /config`：读取阈值、冷却时间、看门狗时间、舵机角度等配置。
- `POST /config`：更新可调参数。
- `POST /calibrate`：重新开始 10 秒静息校准。
- `POST /pump_test`：执行一次受限的手动泵气测试。
- `GET /logs`：读取事件日志。

## 实验计划

- 静息噪声测试：记录 5 分钟空置压力数据，评估误触发率。
- 模拟呼吸测试：将传感器夹具固定在面罩呼气孔外侧，观察压差、湿度和温度活动幅度。
- 阈值对比测试：比较压差窗口幅度、湿度活动、温度活动和组合阈值。
- 舵机台架测试：记录空载、挤压球囊、堵转、电源温升和动作延迟。
- 看门狗测试：停止模拟呼吸超过 15 秒，验证报警和一次辅助泵气演示。
- 前端演示测试：确认压差波形、状态卡片、参数更新和日志实时刷新。

## 参考资料

- FDA Respiratory Devices: https://www.fda.gov/medical-devices/products-and-medical-procedures/respiratory-devices
- FDA Ventilators and Ventilator Accessories: https://www.fda.gov/medical-devices/coronavirus-covid-19-and-medical-devices/ventilators-and-ventilator-accessories-covid-19
- Raspberry Pi Documentation: https://www.raspberrypi.com/documentation/computers/raspberry-pi.html
- MAX30102 Product Page: https://www.analog.com/en/products/max30102.html
- Bosch BMP280 Datasheet: https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmp280-ds001.pdf

## License

Apache License 2.0
