# 睡眠监测与辅助呼吸半实物验证原型

这是参考 `radar` 项目实现方式搭建的 Flask 原型：后台线程持续采集/模拟传感器数据，前端通过 `/data` 实时刷新 ECharts 仪表盘。

## 运行

```powershell
cd F:\资料\大三\学科实践(四)\final_project
python .\sleep_assist\app.py
```

浏览器打开 `http://127.0.0.1:5000`。

当前版本是 Phase 1 实测版：测试 BMP280 压差、可选 AHT20 温湿度、GPIO18 舵机和 GPIO27 LED。MAX30102/SpO2 暂未接入，代码不会生成假 SpO2 或假心率。

默认 `assist_mode` 为 `apnea_only`：正常呼吸活动只记录并清除呼吸暂停报警，不泵气。呼吸活动使用 `breath_window_seconds` 窗口内的压差峰峰值，而不是单点尖峰；系统会学习用户近期呼吸间隔，并在 `apnea_min_seconds` 和 `apnea_max_seconds` 范围内自适应暂停判定时间。当前 Phase 1 代码把该判定时间硬限制在 15 秒以内，避免一次慢呼吸把下一次暂停判断拖到 25 秒。每次泵气后会在 `pump_artifact_ignore_seconds` 内忽略压差触发，并暂停基准更新；随后进入 `post_pump_recovery_seconds` 恢复观察期，使用更敏感的阈值捕捉弱呼吸。

如果 BMP280 读取失败，界面会显示错误，不会自动切换到模拟波形。

## 模块测试

```powershell
python .\sleep_assist\app.py --test pressure
python .\sleep_assist\app.py --test environment
python .\sleep_assist\app.py --test led
python .\sleep_assist\app.py --test servo
python .\sleep_assist\app.py --test all
python .\sleep_assist\app.py --test routes
```

AHT20 使用项目内置的 `smbus2` 直连读写实现，协议参考商家资料中的 `ATH20.c/.h`，不依赖 `adafruit_ahtx0`。若已安装 Pimoroni `bmp280`，通常已经同时安装了 `smbus2`。

## GPIO

- LED: GPIO27
- Servo PWM: GPIO18
- I2C SDA: GPIO2
- I2C SCL: GPIO3

舵机必须使用独立 5V/3A 电源，并与树莓派 GND 共地。不要从树莓派 5V 引脚直接给 MG996R 供电。
