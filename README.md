# 灵息 / InspiraLink

居家型低成本多模态智能呼吸监控与辅助系统

## 项目命名

**中文名：灵息**

“灵”代表 AI 的灵动、感知与辅助决策能力；“息”代表呼吸、生命体征与持续看护。灵息希望表达一种更轻巧、更实时、更有温度的呼吸安全守护方式。

**英文名：InspiraLink**

`Inspira` 取自 `Inspiration`，兼具“吸气/呼吸”与“灵感”的含义；`Link` 代表连接人与设备、传感器与执行机构、监测与辅助动作。

## 项目简介

灵息 / InspiraLink 是一个面向课程实践与半实物验证的睡眠呼吸监测和辅助呼吸演示系统。系统以 Raspberry Pi 为核心，通过 BMP280/AHT20 捕捉采样管内 Pa 级压差变化，通过 MAX30102 展示血氧和心率趋势，并使用舵机驱动简易球囊完成台架上的辅助泵气演示。

项目参考 `radar` 示例中的架构思想，采用“后台硬件线程 + Flask API + ECharts 实时仪表盘”的方式，将传感器数据、呼吸判定、舵机状态、报警信息和调试参数集中展示，便于实验记录和答辩演示。

## 重要免责声明

本项目仅供学术研究、实验教学和原型演示使用。

- 本系统及其硬件结构、软件代码均不是医疗器械，未经过 NMPA、FDA 等监管机构认证。
- 禁止将本项目用于真实临床治疗、生命支持、急救或真实病人的日常护理。
- 所有自动泵气动作仅用于台架验证，不应连接真实人体气道。
- 任何涉及呼吸支持或护理的真实行为，必须遵循专业医师指导并使用认证医疗设备。

## 核心功能

- **Pa 级呼吸压差检测**：使用 BMP280 读取气压，结合滑动平均、基准漂移补偿和动态阈值检测吸气负压。
- **多模态趋势监测**：使用 MAX30102 展示 SpO2 和心率趋势，作为报警和效果观察参考，不单独驱动泵气动作。
- **半实物辅助泵气演示**：使用 MG996R/SG90 舵机挤压简易呼吸球囊，完成台架上的同步泵气或看门狗泵气演示。
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

默认启用模拟硬件模式，没有 Raspberry Pi 和传感器也可以打开仪表盘演示。

```powershell
python .\sleep_assist\app.py
```

浏览器打开：

```text
http://127.0.0.1:5000
```

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
- 模拟呼吸测试：用软管、注射器或手捏采样腔制造负压，验证吸气识别。
- 阈值对比测试：比较固定 -5Pa、自适应 `3 * sigma` 和组合阈值。
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
