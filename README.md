# 斐讯悟空 M1 空气检测仪 — ESPHome 固件（RTL8710B）

把斐讯「悟空 M1」空气检测仪的原厂 WiFi 模块（庆科 **EMW3080BE**，内部实为
Realtek **RTL8710BN**）刷成 **ESPHome** 固件，绕过斐讯已死的云端，纯本地接入
Home Assistant。

固件通过 UART 协议与主控 **SAMD20G18A** 通信：读取甲醛 / 温湿度 / PM2.5，
并控制数码管屏幕的亮度、开关与时钟。传感器和屏幕仍然由原厂主控驱动，本固件
只替换 WiFi 协处理器，不动 SAMD20，因此**所有原厂硬件功能完好**。

## 特性

- **纯本地接入**：ESPHome API + 原生 OTA，不依赖任何云端。
- **完整传感器数据**：甲醛（mg/m³）、温度（°C）、湿度（%RH）、PM2.5（µg/m³），
  来自 SAMD20 的 0x01 上报帧。
- **屏幕控制**：亮度（0 / 25 / 50 / 75 / 100）、开关、时钟设置（0x02 / 0x03 命令），
  重启后自动恢复上次状态。
- **状态灯**：WiFi 图标灯（PA5）+ 红叉灯（PA12），支持「指示灯总开关」，夜间可全灭。
- **查询间隔可调**：Home Assistant 里可动态调整传感器轮询频率。
- **可扩展**：模块还有几个空闲引脚（PA0 / PA8 / PA19 / PA22），可外接红外发射、
  继电器、气体传感器等，详见 [`docs/M1_hardware_expansion.md`](docs/M1_hardware_expansion.md)。

## 文件结构

```
├── esphome-phicomm-m1.yaml      # ESPHome 配置（固件本体，含全部 UART 协议逻辑）
├── secrets.yaml.example        # WiFi 凭据模板（复制为 secrets.yaml 后填写）
├── .github/workflows/build.yml  # GitHub Actions：打 tag 自动编译并发布 Release
├── docs/
│   ├── M1_protocol_spec_details.md   # UART 协议文档（帧格式 / 引脚映射 / 时序）
│   ├── M1_hardware_expansion.md      # 硬件扩展指南（空闲引脚 / 外接传感器方案）
│   ├── emw3080_pin.jpg               # EMW3080 模块引脚图（刷机接线参考）
│   ├── mx1290_pin.png                 # MX1290 芯片 QFN-32 引脚图（A_D Electronics 拆解）
│   └── rtl8710bn_pin.png              # RTL8710BN 芯片 QFN-32 引脚图（同上）
├── tools/serial_dump.py        # 串口原始数据 hexdump 工具（抓 UART 协议用）
└── LICENSE                     # GPL-3.0
```

## 刷机

### 1. 接线

M1 的 TTL 刷机口就是模块的日志/下载 UART（模块 **24 / 25** 脚 = PA30 / PA29）。
拆机后在主板上找到 WiFi 模块，参考下图接线：

![EMW3080 模块引脚](docs/emw3080_pin.jpg)

| USB-TTL | 模块引脚 | 说明 |
|---|---|---|
| 3.3V | 16（VDD33） | 给模块单独供电（可选，见下） |
| GND | 17（GND） | 必须共地 |
| RX | 24（PA30） | 模块 TX → TTL RX（日志/下载口） |
| TX | 25（PA29） | TTL TX → 模块 RX（日志/下载口） |

> - **推荐模块单独供电**：M1 整机断电，用 USB-TTL 的 3.3V 输出接模块 16 脚供电，
>   避免整机 5V 电源与 TTL 电平打架。
> - 也可以整机原电源供电（此时不接 VCC，只接 GND / TX / RX），
>   但**必须确认 USB-TTL 是 3.3V 电平**（不是 5V 的板子）。
> - 模块 24 / 25 脚与 21 / 22 脚为同信号双焊盘（21≡24、22≡25），我用杜邦线直接插入25 25脚的圆孔中可以成功。
> - 之前刷过猪头大佬或者zM1大佬固件的可能用的是[这种接线方式](https://github.com/a2633063/zM1/wiki/%E5%9B%BA%E4%BB%B6%E7%83%A7%E5%BD%95)，
>   那个接线方式中TTL的RX TX接第9 10引脚，但我实测用ltchiptool这样接线刷不了，jlink那种方式我没试过。

### 2. 备份原厂固件（强烈建议）

刷机前先用 `ltchiptool` 把整片 flash 读出来备份，之后可以刷回：

```
ltchiptool flash read -d COM3 RTL8710B m1_stock_backup.bin
```

- `ltchiptool` 是 LibreTiny 官方烧录工具。安装 PlatformIO + LibreTiny 后自带：
  `~/.platformio/penv/.libretiny/Scripts/ltchiptool.exe`（Windows），
  也可以 `pip install ltchiptool` 单独安装。
- 命令末尾的 `RTL8710B` 是芯片 family 名（等价于 `realtek-ambz`）。
- 备份文件大小应等于 flash 总容量（本机 2MB，即 2097152 字节），偏小说明有问题。

### 3. 刷入 ESPHome 固件



```
ltchiptool flash write -d COM3 firmware.bin
```

`ltchiptool` 会自动识别固件格式并写入正确地址（0x0）。刷完重新上电，
日志口（PA30，模块 24 脚）会以 **115200 8N1** 输出 ESPHome 日志，可确认启动状态。

### 4. 刷机注意事项

- **USB-TTL 时好时坏是常见现象**：模块下载口对信号质量敏感，接触不良 / 杜邦线过长 /
  电平不匹配都会导致连接失败。建议：
  - 优先用 CH343 芯片的 3.3V 电平 USB-TTL，线越短越好；
  - 失败就重新插拔 USB、重新给模块上电再试（ltchiptool 会自动进入下载模式）；
  - 多次失败时检查 GND 是否可靠、模块 16 脚供电是否稳定。
- **刷回原厂**：用第 2 步的备份文件：
  ```
  ltchiptool flash write -d COM3 -f RTL8710B -s 0x0 m1_stock_backup.bin
  ```
  整片 raw dump 没有文件头，必须手动指定 `-f RTL8710B -s 0x0`。
- 刷机不影响 SAMD20 主控；如果以后不想用了，刷回备份即可完全还原。

## 配置与编译

### 1. 配置 WiFi

复制凭据模板并填写：

```
cp secrets.yaml.example secrets.yaml
```

编辑 `secrets.yaml`：

```yaml
wifi_ssid: "你的WiFi名称"
wifi_password: "你的WiFi密码"
```

`secrets.yaml` 已被 `.gitignore` 忽略，不会误提交。

### 2. 本地编译

需要 Python 环境：

```
pip install esphome
esphome compile esphome-phicomm-m1.yaml
```

产物在 `.esphome/build/phicomm-m1/.pioenvs/phicomm-m1/`：

- `firmware.bin` —— 串口烧录用；
- `firmware.uf2` —— 支持 UF2 引导的板子用（本机一般用不到）。

也可以接好 USB-TTL 后直接编译并烧录：

```
esphome run esphome-phicomm-m1.yaml
```

后续小改动建议走 **OTA**（`esphome upload` 或 HA 里的 ESPHome 集成），不用拆机。

### 3. GitHub Actions 自动编译并发布 Release

仓库已带 `.github/workflows/build.yml`：

1. 把 `WIFI_SSID` 和 `WIFI_PASSWORD` 添加到仓库的
   **Settings → Secrets and variables → Actions**；
2. 推送形如 `v1.0.0` 的 tag，Actions 会自动编译固件并发布到 **GitHub Release**
   （`firmware.bin` + `firmware.uf2`）；
3. 也可以在 Actions 页面手动触发（`workflow_dispatch`）。

发布后下载 `firmware.bin`，用 `ltchiptool` 按上面第 3 步刷入即可。

## 相关文档

- **[UART 协议文档](docs/M1_protocol_spec_details.md)**：帧格式（0x23/0x21 定界）、
  命令字、引脚映射、启动时序、恢复出厂等，以及 `tools/serial_dump.py`
  的抓包方法。
- **[硬件扩展指南](docs/M1_hardware_expansion.md)**：哪些引脚空闲、怎么加红外 /
  继电器 / 第二路串口传感器。
- 图片来源：`emw3080_pin.jpg` 为模块实物引脚标注；
  `mx1290_pin.png` / `rtl8710bn_pin.png` 来自 A_D Electronics 对 EMW3080 的拆解
  （证实内部为 MX1290 = RTL8710BN），引脚映射依据见协议文档 §3.1。

## 免责声明

- 刷机有风险，操作前请备份原厂固件；本固件与本文档仅供学习研究，请自行承担后果。
- 固件中的 UART 协议为社区分析成果，可能不完整，请以实测为准。