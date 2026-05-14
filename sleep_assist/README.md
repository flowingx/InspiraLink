# 睡眠监测与辅助呼吸半实物验证原型

这是参考 `radar` 项目实现方式搭建的 Flask 原型：后台线程持续采集/模拟传感器数据，前端通过 `/data` 实时刷新 ECharts 仪表盘。

## 运行

```powershell
cd F:\资料\大三\学科实践(四)\final_project
python .\sleep_assist\app.py
```

浏览器打开 `http://127.0.0.1:5000`。

默认启用模拟硬件模式，便于无树莓派环境下演示。接入树莓派后可在 `sleep_assist/app.py` 中将 `simulate_hardware` 改为 `False`，并补齐 MAX30102 适配器。

## GPIO

- LED: GPIO27
- Servo PWM: GPIO18
- I2C SDA: GPIO2
- I2C SCL: GPIO3

舵机必须使用独立 5V/3A 电源，并与树莓派 GND 共地。不要从树莓派 5V 引脚直接给 MG996R 供电。
