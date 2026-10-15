# M1 内存问题分析与优化记录

> 适用固件：`esphome-phicomm-m1.yaml`（含 web_server + api + captive_portal 的完整版）
> 芯片：Realtek **RTL8710BN**（庆科 EMW3080BE 模块，125MHz / 256KB RAM / 2MB Flash，应用区约 468KB）
> 编译工具：ESPHome 2026.9.1 + LibreTiny 1.10.0（`generic-rtl8710bn-2mb-468k`）

---

## 1. 现象回顾

### 1.1 早期版本（完整版含大量调试组件）

- 能正常进入 fallback AP **`Phicomm-M1-Setup`**；
- **只要有设备连接这个 AP，设备就立即重启**；
- 电脑连上 AP 后访问 `http://192.168.4.1`，浏览器显示**一行乱码**
  （`ﾭ�ﾭ�ﾭ�…`，即 HTTP 响应不是正常 HTML，而是损坏/截断的二进制数据），
  随后连接断开——因为设备已经重启。

### 1.2 精简版本（删除调试组件后）

- 删除了 cmd 0x0C RTC 调试传感器、GPIO 调试标记、协议调试按钮，
  问题大幅缓解，但**偶尔仍会出现内存相关异常**；
- 当前 `esphome-phicomm-m1.yaml`编译结果：

```
RAM:   [=         ]  14.4% (used 37668 bytes from 262144 bytes)
Flash: [==========]  97.8% (used 468652 bytes from 479232 bytes)
```

静态内存（.data + .bss）只占 14.4%，说明**问题不在静态内存，而在运行时堆（heap）**。

---

## 2. 根因分析

### 2.1 芯片可用堆很小

RTL8710BN 虽然有 256KB RAM，但 WiFi 协议栈、FreeRTOS、lwIP、mbedTLS 等
SDK 代码会占用大量内存，ESPHome 应用实际可用的**堆只有约 162KB**。
在这 162KB 里要同时跑：

- **WiFi STA**（连接家用路由器）
- **SoftAP**（fallback 配置热点，连不上主 WiFi 30s 后启动）
- **captive_portal**（端口 80，配置页服务器）
- **web_server**（端口 80，状态页 + **网页 OTA**，与 captive portal 共用端口）
- **api**（ESPHome 原生 API，端口 6053，与 Home Assistant 保持长连接）
- **ota**（原生 OTA）
- **mdns**、**sntp**、logger（DEBUG）、uart debug 等

### 2.2 崩溃的直接触发点：SoftAP 客户端接入 + HTTP 请求并发

当有设备连接 fallback AP 时，WiFi 驱动要为新 station 分配：

- 认证/加密密钥缓存；
- 收发缓冲区（TX/RX DMA buffer）；
- lwIP 为 AP 接口的每个 TCP 连接分配 socket 缓冲区。

与此同时，客户端打开 `192.168.4.1` 触发 captive portal / web_server 处理 HTTP 请求，
还要再分配：

- HTTP 请求/响应缓冲区；
- JSON 序列化缓冲区（web_server 用 ArduinoJson 生成页面数据）；
- 若开启网页 OTA，OTA handler 还会预留大块固件接收缓冲。

这些分配**在同一时刻叠加**，把 ~162KB 堆直接打满 → `malloc` 失败 →
ESPHome/LibreTiny 触发 `abort()` / 断言失败 / watchdog → **设备重启**。

### 2.3 192.168.4.1 出现乱码的原因

正常情况浏览器应收到 `Content-Type: text/html` 的 ESPHome 配置页。
出现乱码说明**响应内容损坏**：

1. 堆耗尽时 `malloc` 可能返回空或小块内存，页面字符串写入时越界/被覆盖；
2. 服务器在处理请求中途崩溃（连接被 RST），浏览器只收到 TCP 流前部的
   残缺/原始字节；
3. 页面数据或 Content-Length 不匹配，浏览器把二进制内容当文本显示，
   于是出现 `ﾭ�ﾭ�` 这类解码乱码。

### 2.4 为什么"偶尔"才出问题

- 设备正常连接主 WiFi、HA 通过 API 连接时，堆占用平稳，**不会**崩溃；
- 只有**主 WiFi 断开 → fallback AP 启动 → 有客户端连入并打开网页**，
  或同时有 web_server 页面访问 + API 长连接 + OTA 上传时，堆才到达临界点；
- 因此表现为"偶尔 / 特定场景下重启"，普通使用看起来一切正常。

### 2.5 当前 yaml 中可继续压榨的内存点

| 位置 | 问题 | 影响 |
|---|---|---|
| `web_server:`（裸写，无 `ota: false`） | **网页 OTA 默认开启**，`ota_web_server.cpp` 被编译进固件 | 增加 Flash 占用（当前 97.8% 已接近上限）+ 运行时 OTA 大缓冲 |
| `static std::vector<uint8_t> rx_buf`（uart debug lambda 内） | 每次 UART 回调都往动态数组 `insert` 字节，堆上反复分配/释放 | 堆碎片化，长期运行后可用内存变小 |
| `uart: rx_buffer_size: 512` | UART 驱动环形缓冲 512 字节 | 静态 RAM 占用（可降到 128/256） |
| `logger: level: DEBUG` | 每条帧都格式化打印，占用 logger 任务缓冲和 CPU | 若不需要逐帧 hex 可改 INFO |
| `captive_portal` + `wifi.ap` | SoftAP 支持代码 + 运行期 station 内存 | 不需要配置向导时可整体移除，**直接消除崩溃场景** |

---

## 3. 优化建议（按收益从大到小）

### 方案 A：关闭网页 OTA（强烈推荐，最小改动）

```yaml
web_server:
  port: 80
  ota: false
```

- 移除 `ota_web_server.cpp`，节省 Flash（97.8% 已很危险）并去掉一个堆大户；
- 原生 `ota` 组件仍保留，HA / `esphome run` 照常可以 OTA 刷机；

### 方案 B：个人固定 WiFi 固件，移除配置向导（消除崩溃场景）

自己用、WiFi 已写死时，不再需要 fallback AP：

```yaml
# 删除 captive_portal: 整段
# 删除 wifi.ap: 整段
```

- 不再编译 SoftAP + captive portal 代码，运行时也不会再启动配置热点；
- 从根本上消除"客户端连 AP → 堆耗尽重启"的崩溃路径；
- 代价：以后换 WiFi 需要重新编译，不能用手机配网。

> ⚠️ 若用 GitHub Actions 发布公共默认固件给不会编译的用户，
> **必须保留** `captive_portal` + `ap`（免编译配网向导），不能删。

### 方案 C：web_server 与 api 二选一

`web_server` 和 `api` 都常驻监听、各自维护连接缓冲区。二选一可显著降低峰值堆：

- 只用 Home Assistant → 保留 `api`，删 `web_server`；
- 不用 HA、只开浏览器控制 → 保留 `web_server`，删 `api`。

### 方案 D：UART 解析改用固定数组

把 uart debug lambda 里的 `static std::vector<uint8_t> rx_buf`
换成固定大小数组 + 游标（例如 `uint8_t rx_buf[64]`），避免堆上反复分配。

### 方案 E：降低 uart rx_buffer_size

```yaml
uart:
  rx_buffer_size: 256   # 原 512；SAMD20 帧只有 20 字节，256 足够
```

---

## 4. 实测编译数据

| 项目 | 数值 |
|---|---|
| 编译配置 | `esphome-phicomm-m1.yaml` |
| 静态 RAM | 37,668 B / 262,144 B（14.4%） |
| Flash | 468,652 B / 479,232 B（**97.8%**） |
| IRAM_ATTR | 424 B @ 0x10005b70 |
| 结论 | 能编译通过，但 Flash 余量仅 ~10.6KB；运行期堆才是瓶颈 |

---

## 5. 结论

- 当前 `esphome-phicomm-m1.yaml` **可以正常编译使用**，普通 STA 场景稳定；
- "偶尔 mem 问题"的根源是 **RTL8710BN 可用堆太小 + web_server/api/captive_portal
  常驻叠加 + SoftAP 客户端接入时的瞬时分配峰值**；
- 优先做 **方案 A（`web_server: ota: false`）**，若仍想彻底消除崩溃，
  个人固件直接做 **方案 B（移除 captive_portal + ap）**。