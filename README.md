# ManwaDownloader

> 漫蛙漫画批量下载器 · 纯异步流水线 + 章节预取 + 全局并发，单文件源码即可运行

命令行漫画下载工具。基于 `aiohttp` 做全异步下载，API 请求、图片落盘、AES 解密全部丢进独立线程池，配合章节预取与全局并发控制，在同等带宽下把下载吞吐压到接近网络上限。

---

## 特性

| 特性 | 说明 |
|------|------|
| **纯异步流水线** | 单事件循环 + `aiohttp`；API 请求、落盘、解密全部走独立线程池，不阻塞下载 |
| **章节预取** | 下载当前章节时，后续章节的图片列表已在后台取好，API 延迟被隐藏 |
| **全局并发** | 信号量按「总并发」分配，小章节不再占满额度让其他章节干等 |
| **并发域名探测** | 多域名同时探测并**用 API 验证**，只认真正可用的站点（结果缓存 6 小时） |
| **多重容错** | 图源回退 + 章级别重试 + 动态 Referer + TLS 握手重置自动重连 |
| **智能续传** | 自动跳过已下载章节，中断后可接续 |
| **Rich 进度条** | 章节级进度 + 实时速度 / 带宽 |
| **优雅退出** | Ctrl+C 清理 `.tmp` 残留，已完成的图片不受影响 |
| **AES-256-CBC 解密** | 自动识别并解密加密 CDN 返回的图片 |
| **代理支持** | API 与图片下载均可走 HTTP 代理 |

本地基准（40 章 × 30 图，250 ms 延迟）：v4 约 107 img/s → v5 默认并发 205 img/s，128 并发 335 img/s。

---

## 环境要求

- Python **3.9+**
- Windows / macOS / Linux（`运行下载.bat` 为 Windows 便捷入口）

---

## 快速开始

```bash
git clone https://github.com/Hpeter2004/manwa-downloader.git
cd manwa-downloader

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
```

查看漫画信息（不下载）：

```bash
python download.py 1728 --info
```

下载：

```bash
python download.py 1728                 # 全部章节
python download.py 1728 --end 10        # 只下载前 10 章
python download.py 1728 --start 50 --end 100
```

Windows 下也可双击 `运行下载.bat`，它会依次尝试 `.venv`、`dist\ManwaDownloader.exe`、系统 `python`，自带交互式输入。

---

## 使用说明

### 交互模式

双击 `运行下载.bat`（或直接运行 exe）后按提示输入：

1. 漫画 ID 或链接 —— `1728`、`https://www.manwaqi.cc/comic/1728` 均可
2. 下载范围：

| 输入 | 效果 |
|------|------|
| `1` | 只下载第 1 话 |
| `1-50` | 下载第 1 ~ 50 话 |
| `all` | 全部章节 |
| 回车（留空） | 测试：下载前 5 话 |
| `q` | 退出 |

### 命令行参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `comic` | 漫画 ID 或 URL（必填） | — |
| `--start N` | 起始章节号 | 1 |
| `--end N` | 结束章节号 | 全部 |
| `--source N` | 图源索引（按配置动态生成） | 0 |
| `--workers N` | **全局**并发图片数 | 64 |
| `--ch-workers N` | 并行章节数 | 8 |
| `--retry N` | 章级别 API 重试次数 | 2 |
| `--no-resume` | 忽略已下载内容，重新下载 | 关 |
| `--output DIR` | 输出目录 | 当前目录 |
| `--info` | 仅查看信息，不下载 | 关 |

```bash
# 换图源 + 提高并发
python download.py 1728 --source 1 --workers 96 --ch-workers 12

# 极速档（带宽允许时）
python download.py 1728 --workers 128 --ch-workers 16
```

---

## 配置 `config.json`

放在工作目录（exe 同目录 / 当前目录）即可生效，删除则恢复默认并自动探测域名。

```json
{
    "base_url": "",
    "proxy": "",
    "image_sources": [
        "https://tu.mwzu.cc",
        "https://svip.mwtt.cc",
        "https://tu.mwla.cc",
        "https://fm.mwtt.cc"
    ],
    "workers": 64,
    "ch_workers": 8,
    "default_source": 0,
    "retry": 2,
    "rate_limit": 0.05,
    "connect_timeout": 5,
    "read_timeout": 25
}
```

| 字段 | 说明 | 默认 |
|------|------|------|
| `base_url` | 留空 = 自动探测；填写 = 强制使用该域名 | `""` |
| `proxy` | 代理地址，如 `http://127.0.0.1:7890`；留空 = 直连 | `""` |
| `image_sources` | 图片 CDN 地址列表 | 见上 |
| `workers` | 全局并发图片数（40 ~ 128 之间调） | 64 |
| `ch_workers` | 同时下载的章节数 | 8 |
| `default_source` | 默认图源索引 | 0 |
| `retry` | API 失败重试次数 | 2 |
| `rate_limit` | API 调用最小间隔（秒，带突发额度） | 0.05 |
| `connect_timeout` | 建连超时（秒） | 5 |
| `read_timeout` | 读超时（秒） | 25 |

调优口诀：**快了就加，报错了就减**。速度上不去先把 `workers` 往上调（64 → 96 → 128）；若日志里大量出现 `fail:http_429` / `fail:timeout`，说明触发限流或带宽打满，把 `workers` 降回 48 以下。

---

## 输出结构

```
当前目录/
├── 漫画名称/
│   ├── 0001_第1话/
│   │   ├── 0001.jpg
│   │   └── ...
│   └── 0002_第2话/
└── manwa_download.log     ← 错误 / 警告日志（自动生成）
```

漫画 ID 获取方式：进入漫画详情页，地址栏 `/comic/1728` 中的数字即为 ID。

---

## 打包为 exe

```bash
pip install pyinstaller
pyinstaller ManwaDownloader.spec
```

产物位于 `dist/ManwaDownloader.exe`，会自动带上同目录的 `config.json`。仓库不收录编译产物，如需分发请发布到 Releases。

---

## 项目结构

```
manhua_download/
├── download.py              # 入口脚本
├── manhua_download/
│   ├── api.py               # 章节列表 / 图片列表 API（同步阻塞，交给线程池）
│   ├── cli.py               # 参数解析 + 交互模式 + 异步编排
│   ├── config.py            # 配置加载、域名探测、限速器、日志、信号处理
│   ├── crypto_utils.py      # AES-256-CBC 图片解密
│   ├── downloader.py        # 异步下载流水线、线程池、统计
│   └── utils.py             # 文件名清洗、章节号解析等纯函数
├── bench/                   # 本地基准测试（假服务器 + 死镜像模拟）
├── config.json              # 默认配置
├── requirements.txt
├── ManwaDownloader.spec     # PyInstaller 打包配置
├── repair_existing.py       # 修复历史下载（扩展名纠正 / 就地解密）
├── 运行下载.bat              # Windows 交互式入口
└── 使用前观看.md             # 详细中文说明与 FAQ
```

---

## 常见问题

**窗口一闪就没了？**

v5.1 起出错不再静默退出：域名探测改为直接调用章节列表 API 验证（停放域名首页返回 200 但 `/api/` 全 404 的情况已被排除）；拉不到章节列表会自动换镜像重试一次；任何异常都会打印原因并写入 `manwa_download.log`。

**提示「所有镜像都连不上」/ TLS 握手被中断？**

站点走 Cloudflare，握手可能被重置。开代理后在 `config.json` 填 `"proxy": "http://127.0.0.1:7890"`，或换时间段再试。

**站点换域名了？**

`config.json` 的 `base_url` 填新域名；或留空让它自动探测（并发探测 + API 验证，结果缓存 6 小时）。

**杀毒软件报毒？**

PyInstaller 打包的 exe 常被误报，源码可完整审查，也可直接用 `python download.py` 运行。

更多问答见 [`使用前观看.md`](./使用前观看.md)。

---

## 版本历史

### v5.1 (2026-10-24)
修复域名探测误判（停放域名导致启动即失败）、增加探测结果缓存、失败自动换镜像重试、异常不再无声崩溃、HTTP 层自动重试 TLS 重置、支持代理。

### v5 (2026-10-24)
修复同步 API 阻塞事件循环的致命问题、章节预取流水线、全局并发信号量、续传检查由每图 6 次 `stat()` 改为整章 1 次 `scandir()`、令牌桶限速、修复 `BASE_URL` 导入时写死与进度条图片数恒为 0。

### v4 (2026-07-09)
架构重写为纯异步、并发域名探测、动态 Referer、SIGINT 优雅退出、Rich 进度条增强、错误日志、API 限速、Session 单例复用。

### v3 (2026-07-09)
初版模块化拆分（config / utils / api / downloader / cli）、aiohttp 异步下载、域名串行探测、`config.json` 支持。

---

## 免责声明

本项目仅供编程学习与技术交流使用，请勿用于商业用途。所有漫画内容的版权归原作者及发布平台所有，请支持正版。使用者应自行承担因使用本工具产生的一切后果。
