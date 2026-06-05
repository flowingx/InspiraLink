# 灵息 / InspiraLink

居家型低成本多模态智能呼吸监控与辅助系统

## 项目命名

**中文名：灵息**

"灵"代表 AI 的灵动、感知与辅助决策能力；"息"代表呼吸、生命体征与持续看护。灵息希望表达一种更轻巧、更实时、更有温度的呼吸安全守护方式。

**英文名：InspiraLink**

`Inspira` 取自 `Inspiration`，兼具"吸气/呼吸"与"灵感"的含义；`Link` 代表连接人与设备、传感器与执行机构、监测与辅助动作。

## 项目简介

灵息 / InspiraLink 是一个面向课程实践与半实物验证的多模态智能健康监护系统。系统以 Raspberry Pi 为核心，集成以下功能模块：

- **夜间模式**：通过 BMP280/AHT20 压差+温湿度检测呼吸活动，MAX30102 监测血氧/心率趋势，舵机驱动球囊进行台架辅助泵气
- **白天模式**：通过摄像头 + YOLOv8n-pose 姿态估计实现跌倒检测

前端提供白天/黑夜模式切换，一键在两种工作模式间切换。

## 重要免责声明

本项目仅供学术研究、实验教学和原型演示使用。

- 本系统及其硬件结构、软件代码均不是医疗器械，未经过 NMPA、FDA 等监管机构认证。
- 禁止将本项目用于真实临床治疗、生命支持、急救或真实病人的日常护理。
- 所有自动泵气动作仅用于台架验证，不应连接真实人体气道。

## 核心功能

### 夜间模式（呼吸监测 + 血氧 + 气囊辅助）

- **多特征呼吸活动检测**：BMP280 压差脉冲 + AHT20 湿度上升沿 + 温度上升沿，三者任一满足即确认呼吸活动
- **自适应呼吸暂停判定**：学习用户呼吸间隔，动态调整暂停判定时间（12-15秒范围内）
- **血氧趋势保护**：MAX30102 读数不稳定时不以低血氧触发泵气；SpO2 连续恢复到阈值以上 3 秒后暂停辅助泵气
- **辅助泵气**：MG996R 舵机挤压球囊，泵气后屏蔽压差干扰并进入恢复观察期
- **安全互锁**：冷却时间、泵气次数限制、传感器异常阻断、退出时自动释放舵机 PWM、LED 报警

### 白天模式（跌倒检测）

- **YOLOv8n-pose 姿态估计**：通过摄像头实时推理人体骨架
- **身体角度判定**：计算肩膀中心到髋部中心的角度，接近水平持续多帧则判定跌倒
- **实时画面展示**：白天模式仪表盘中间区域显示摄像头 MJPEG 画面
- **报警联动**：跌倒时 LED 报警 + 事件日志记录

### Web 实时仪表盘

- Flask + ECharts 实时展示压差波形、状态卡片、SpO2/心率、舵机状态和白天摄像头画面
- 白天/黑夜模式切换按钮
- 参数在线调节、手动泵气测试、LED 测试
- 事件日志持久化到 JSONL 文件

## 代码结构

```text
.
├── README.md
├── requirements.txt
├── .gitignore
├── LICENSE
└── src/
    ├── app.py                  # Flask 入口 + 路由 + 启动逻辑
    ├── config.py               # 系统配置常量
    ├── state.py                # 共享状态 + 锁 + 事件日志
    ├── tests.py                # 硬件模块测试函数
    ├── sensors/
    │   ├── bmp280.py           # BMP280 气压传感器驱动
    │   ├── aht20.py            # AHT20 温湿度传感器驱动（smbus2 直连）
    │   └── max30102.py         # MAX30102 血氧传感器包装
    ├── detectors/
    │   ├── breath.py           # 呼吸活动检测 + 暂停控制 + SpO2 评估
    │   └── fall.py             # YOLOv8n-pose 跌倒检测
    ├── actuators/
    │   └── gpio.py             # GPIO 初始化 + LED + 舵机泵气
    ├── loops/
    │   ├── night.py            # 夜间模式主循环
    │   └── day.py              # 白天模式主循环
    ├── max30102_driver/        # MAX30102 底层 I2C 驱动 + 心率/血氧算法
    │   ├── max30102.py
    │   ├── hrcalc.py
    │   └── heartrate_monitor.py
    ├── models/
    │   └── yolov8n-pose2.onnx  # 跌倒检测 ONNX 模型
    └── templates/
        └── index.html          # ECharts 实时仪表盘
```

## 快速运行

在 Raspberry Pi 项目目录下：

```bash
cd /home/xzy/wk/InspiraLink
pip install -r requirements.txt
python src/app.py
```

浏览器访问：

```text
http://<树莓派IP>:5000
```

启动参数：

```bash
python src/app.py                    # 默认夜间模式
python src/app.py --mode day         # 白天跌倒检测模式
python src/app.py --host 0.0.0.0 --port 5000
```

## 模块测试

```bash
python src/app.py --test pressure      # BMP280 气压读数
python src/app.py --test environment   # AHT20 温湿度读数
python src/app.py --test spo2          # MAX30102 血氧/心率（15秒）
python src/app.py --test fall          # 摄像头跌倒检测（15秒）
python src/app.py --test led           # LED 闪烁
python src/app.py --test servo         # 舵机动作
python src/app.py --test routes        # Flask 路由冒烟测试
python src/app.py --test apnea         # 暂停阈值上限验证
```

## 硬件连接

| 模块 | 接口 | 引脚/地址 |
|------|------|-----------|
| LED | GPIO | GPIO27 |
| 舵机 (MG996R) | PWM | GPIO18 |
| BMP280 | I2C | 0x76/0x77 |
| AHT20 | I2C | 0x38 |
| MAX30102 | I2C | 0x57 |
| 摄像头 | USB | /dev/video0 |
| I2C 总线 | SDA/SCL | GPIO2/GPIO3 |

舵机接入 Raspberry Pi GPIO18 PWM 控制，接线时需确认 GND 连接可靠、机构限位明确。

## Flask API

| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 仪表盘页面 |
| `/data` | GET | 实时系统状态 JSON |
| `/config` | GET/POST | 读取/更新配置参数 |
| `/calibrate` | POST | 重新校准压力基准 |
| `/pump_test` | POST | 手动泵气测试 |
| `/led_test` | POST | LED 测试 |
| `/logs` | GET | 事件日志 |
| `/mode` | GET/POST | 查看/切换白天黑夜模式 |
| `/camera_feed` | GET | 白天模式摄像头 MJPEG 画面流 |

## 面罩安装建议

传感器做成外置"小夹子"，夹在面罩原有呼气孔外侧：

- 不堵死呼气孔，不破坏面罩气密性
- 传感器位于浅采样腔内，感受呼气孔附近的压差和湿度变化
- 线沿面罩边缘/绑带固定，使用带锁扣线或焊接小转接板

## 参考资料

- Raspberry Pi Documentation: https://www.raspberrypi.com/documentation/computers/raspberry-pi.html
- MAX30102 Product Page: https://www.analog.com/en/products/max30102.html
- Bosch BMP280 Datasheet: https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmp280-ds001.pdf
- YOLOv8: https://docs.ultralytics.com/models/yolov8/

## License

Apache License 2.0
