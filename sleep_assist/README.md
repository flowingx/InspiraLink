# 睡眠监测与辅助呼吸半实物验证原型

这是参考 `radar` 项目实现方式搭建的 Flask 原型：后台线程持续采集/模拟传感器数据，前端通过 `/data` 实时刷新 ECharts 仪表盘。

## 运行

```powershell
cd F:\资料\大三\学科实践(四)\final_project
python .\sleep_assist\app.py
```

浏览器打开 `http://127.0.0.1:5000`。

当前版本是 Phase 1 实测版：只测试 BMP280 压差、GPIO18 舵机和 GPIO27 LED。MAX30102/SpO2 暂未接入，代码不会生成假 SpO2 或假心率。

如果 BMP280 读取失败，界面会显示错误，不会自动切换到模拟波形。

## 模块测试

```powershell
python .\sleep_assist\app.py --test pressure
python .\sleep_assist\app.py --test led
python .\sleep_assist\app.py --test servo
python .\sleep_assist\app.py --test all
python .\sleep_assist\app.py --test routes
```

## GPIO

- LED: GPIO27
- Servo PWM: GPIO18
- I2C SDA: GPIO2
- I2C SCL: GPIO3

舵机必须使用独立 5V/3A 电源，并与树莓派 GND 共地。不要从树莓派 5V 引脚直接给 MG996R 供电。
