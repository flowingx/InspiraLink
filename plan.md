# 睡眠监测与辅助呼吸半实物验证方案

> 安全边界：本项目仅用于课程半实物台架验证与答辩演示，不连接真实人体气道，不作为医疗设备使用。呼吸机和呼吸辅助设备属于高风险医疗设备，真实应用必须满足医疗器械法规、临床验证、电气安全、气路安全和故障保护要求。

## 1. 项目目标

本项目实现一个“睡眠呼吸监测 + 低风险辅助呼吸演示”的半实物系统。系统通过安装在氧气面罩原有呼气孔外侧的小型采样腔读取 BMP280/AHT20 的压差、湿度和温度变化，用舵机挤压简易球囊完成台架上的辅助泵气动作，并通过 Flask + ECharts 实时展示数据、报警和控制状态。

参考 `radar` 文件夹中的实现方式，系统采用后台硬件线程持续采集数据，Flask 提供接口，浏览器前端周期性拉取 `/data` 并刷新图表。与雷达项目不同，本项目不复用雷达图逻辑，而是改为压差波形、状态卡片、参数控制和事件日志。

## 2. 硬件清单与预算

| 模块 | 组件名称 | 关键规格 | 估算价格 | 用途 |
| --- | --- | --- | --- | --- |
| 主控 | Raspberry Pi 4B/5 | 已有 | - | 运行 Python、Flask、传感器采集与舵机控制 |
| 呼吸检测 | BMP280 + AHT20 | I2C, 气压/温湿度 | 3.5-5 CNY | 捕捉面罩呼气孔附近压差、湿度和温度变化 |
| 血氧监测 | MAX30102 | I2C, 心率/血氧 | 20 CNY | 展示 SpO2 和心率趋势 |
| 辅助通气 | 简易呼吸球囊 | 婴儿/儿童型阻力较小 | 20 CNY | 台架泵气演示 |
| 执行器 | MG996R 舵机 | 约 13 kg·cm | 18-25 CNY | 推荐用于挤压球囊 |
| 备选执行器 | SG90 舵机 | 约 1.6 kg·cm | 7-9 CNY | 仅用于空载或轻载演示 |
| 结构件 | 软管、采样腔、夹具 | 4mm 输液软管、密封盒 | 3-5 CNY | 气压采样与固定 |
| 安全件 | 急停开关、限位件、独立电源 | 5V/3A 电源 | 20-40 CNY | 降低堵转、过流和误动作风险 |

## 3. 物理架构

### 3.1 面罩呼气孔外夹式采样结构

不建议给氧气面罩开孔，也不使用鼻管。BMP280/AHT20 做成一个外置“小夹子”，固定在氧气面罩原有呼气孔外侧。夹具只覆盖一个呼气孔或半覆盖一组呼气孔，形成浅采样腔，让呼气孔附近的气流、湿度和温度变化进入采样腔，同时保留其他呼气孔通畅。

结构建议：

- 使用 3D 打印夹具、硅胶圈或海绵圈贴合面罩外侧，不破坏面罩本体气密性。
- BMP280/AHT20 位于浅采样腔内部，可加薄纱布或透气膜降低冷凝水影响。
- 不堵死呼气孔，避免影响面罩排气。
- 杜邦线改为焊接小转接板或带锁扣线，线沿面罩边缘和绑带固定，减小拉扯。
- 算法同时使用压差、湿度变化和温度变化判断呼吸活动。

### 3.2 舵机挤压机构

执行机构采用固定球囊 + 舵机连杆 + 弧形夹片方案。MG996R 舵机通过连杆推动夹片，从侧面挤压球囊，模拟人工挤压动作。夹片建议使用木片、亚克力片或 3D 打印件，形状贴合球囊曲面，避免单点压迫导致效率低或球囊损坏。

机构必须增加：

- 机械限位：限制最大挤压行程，避免过度压缩球囊。
- 可调行程：通过舵机角度或连杆孔位调节挤压量。
- 急停开关：异常时直接切断舵机电源。
- 手动断电：调试和拆装时先断开舵机外部电源。

### 3.3 接线方案

- LED: GPIO27
- 舵机 PWM: GPIO18
- I2C SDA: GPIO2
- I2C SCL: GPIO3

舵机必须使用独立 5V/3A 电源供电，并与树莓派 GND 共地。MG996R 高负载时电流较大，严禁直接从树莓派 5V 引脚供电。

## 4. 软件架构

系统分为四层：

1. 传感采集层：20Hz 读取 BMP280，若 AHT20 可用则同步读取温湿度；MAX30102 暂未接入。
2. 呼吸判定层：滤波、校准、动态阈值、冷却和看门狗。
3. 执行控制层：舵机泵气、LED 报警、安全互锁。
4. Web 可视化层：Flask 接口 + ECharts 仪表盘。

新增原型位于 `sleep_assist/`：

- `sleep_assist/app.py`：Flask 后端、模拟/硬件适配、呼吸判定、泵气控制、接口。
- `sleep_assist/templates/index.html`：实时仪表盘、参数控制、事件日志。
- `sleep_assist/README.md`：运行说明和接线说明。

当前版本默认读取真实 BMP280，并按需读取 AHT20 温湿度；传感器不可用时界面和测试命令会显示真实错误，不生成模拟压差或假 SpO2 数据。MAX30102 暂未接入，后续到货后再补齐适配器。

## 5. 后端接口

### `/data`

返回系统实时状态：

- `pressure_pa`：当前滤波气压。
- `baseline_pressure_pa`：基准气压。
- `delta_pressure_pa`：当前压差。
- `noise_sigma_pa`：噪声估计。
- `effective_inhale_threshold_pa`：实际吸气阈值。
- `breath_state`：`calibrating`、`monitoring`、`inhale_detected` 或异常状态。
- `last_breath_age_s`：距上次有效呼吸时间。
- `resp_rate_est`：估算呼吸频率。
- `spo2`、`heart_rate`：血氧和心率趋势。
- `servo_state`：舵机状态。
- `alarms`：当前报警列表。
- `history.pressure`：压差历史。
- `history.events`：事件日志。

### `/config`

`GET` 读取参数，`POST` 更新参数。支持：

- 吸气阈值 `min_inhale_delta_pa`
- 噪声倍率 `noise_sigma_multiplier`
- 冷却时间 `cooldown_seconds`
- 看门狗时间 `watchdog_seconds`
- 舵机复位角 `servo_rest_angle`
- 舵机压下角 `servo_press_angle`
- 泵气保持时间 `pump_hold_seconds`
- 手动泵气冷却 `pump_cooldown_seconds`
- SpO2 报警阈值 `spo2_alarm_threshold`

### `/calibrate`

重新开始 10 秒静息校准。校准阶段应保持面罩与外置采样腔相对静止，避免人为吹气或移动夹具。

### `/pump_test`

台架/调试模式下执行一次手动泵气测试。接口包含冷却时间和最大次数限制，避免连续触发导致舵机过热或机构卡死。

### `/logs`

返回事件日志，用于答辩展示和调试复盘。

## 6. 呼吸判定算法

1. 启动后进入 10 秒校准阶段，记录静息压力均值和噪声标准差。
2. BMP280 原始气压统一转换为 Pa。
3. 使用 5 点滑动平均滤波。
4. 使用约 10 秒滚动窗口估计基准气压。
5. 计算压差：

   ```text
   delta_pressure_pa = current_pressure_pa - baseline_pressure_pa
   ```

6. 吸气触发阈值：

   ```text
   effective_threshold = min(-5 Pa, -3 * noise_sigma)
   ```

7. 当 `delta_pressure_pa <= effective_threshold`，且距离上次触发超过 1.5 秒时，判定一次吸气。
8. 触发后进入冷却期，并等待压差回到阈值附近以上，避免一次吸气重复触发。

## 7. 安全逻辑

- 15 秒未检测到有效呼吸时，触发 `apnea_watchdog` 报警，并在台架模式下执行一次辅助泵气演示。
- 传感器异常、舵机异常或电源异常时，禁止自动泵气，只报警并点亮 LED。
- MAX30102 只作为趋势显示和报警参考，不单独驱动舵机动作。
- SpO2 低于阈值时只触发报警，不自动追加泵气。
- 手动泵气测试有冷却时间和次数限制。
- 所有自动动作仅用于台架验证，不连接真实人体气道。

## 8. 前端仪表盘

前端通过 ECharts 和原生 JavaScript 实现：

- 压差波形：显示 `delta_pressure_pa`、0 Pa 基准线和吸气阈值线。
- 状态卡片：压差、呼吸状态、距上次呼吸、SpO2、心率、舵机状态。
- 控制区：保存参数、重新校准、手动泵气测试、读取参数。
- 硬件区：显示当前是否为模拟模式，以及 GPIO 映射。
- 日志区：显示校准、吸气触发、看门狗、泵气和异常事件。

与 `radar/templates/index.html` 相比，新前端保留了“周期性请求 `/data` 并局部刷新图表”的模式，同时清理重复函数定义，改为单一 `loadData()` 刷新流程。

## 9. 实验与调试计划

1. 静息噪声测试：采集 5 分钟空置压力数据，统计噪声标准差和误触发率，目标误触发少于每分钟 1 次。
2. 模拟呼吸测试：用软管、注射器或手捏采样腔制造负压，验证吸气识别率、触发延迟和冷却逻辑。
3. 阈值对比测试：比较固定 -5Pa、`3 * sigma` 自适应阈值、二者组合方案，选择误触发低且响应稳定的配置。
4. 舵机台架测试：先空载，再挤压球囊，记录角度、动作时间、是否堵转、电源温升和电压跌落。
5. 看门狗测试：停止模拟呼吸超过 15 秒，验证报警、LED、日志和一次辅助泵气演示。
6. 前端演示测试：确认压力波形、事件标记、SpO2/心率、舵机状态和参数更新实时生效。

## 10. 运行方式

```powershell
cd F:\资料\大三\学科实践(四)\final_project
python .\sleep_assist\app.py
```

浏览器打开：

```text
http://127.0.0.1:5000
```

默认模拟模式不需要硬件即可运行。接入树莓派硬件时，安装 `flask`、`gpiozero`、`pigpio`、`smbus2`、`bme280` 等库，并启动 `pigpiod`。

## 11. 参考资料

- FDA Respiratory Devices: https://www.fda.gov/medical-devices/products-and-medical-procedures/respiratory-devices
- FDA Ventilators and Ventilator Accessories: https://www.fda.gov/medical-devices/coronavirus-covid-19-and-medical-devices/ventilators-and-ventilator-accessories-covid-19
- Raspberry Pi Documentation: https://www.raspberrypi.com/documentation/computers/raspberry-pi.html
- MAX30102 Product Page: https://www.analog.com/en/products/max30102.html
- Bosch BMP280 Datasheet: https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmp280-ds001.pdf
