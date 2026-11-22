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
| **漫画表格** | 漫画名称 ⇄ Comic ID 一对一登记（同名只留一条映射），`--list` 查看、`--watch` 实时刷新 |
| **网页控制台** | `--serve` 起本地页面：aiohttp 静态服务 + 原生 ES Module，零构建零新依赖，与终端共用同一份数据 |
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
| `comic` | 漫画 ID 或 URL（`--list` / `--watch` 时可省略） | — |
| `--list` | 只显示漫画表格（名称 / Comic ID），不下载 | 关 |
| `--watch` | 实时刷新漫画表格，数据变化自动更新 | 关 |
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

参数表补充：`--serve` / `--port` / `--allow-write` 见下方「网页控制台」。

---

## 漫画表格

下载器只接受 Comic ID，而标题只存在于被 `sname(name, 30)` 截断过的文件夹名里——事后想问「我下过哪些漫画、各自的 ID 是多少」是答不上来的。所以每次下载 / `--info` / 交互模式运行，都会把这本漫画登记进 `.comics.json`（与 `.domain_cache.json` 同目录，原子写入）。

```bash
# 查看表格
python download.py --list

# 实时刷新：另一个终端在下载时，这里会自动更新；Ctrl+C 退出
python download.py --watch
```

```
漫画表格

 #   Comic ID   漫画名称            章节   最后使用
 ──────────────────────────────────────────────────────
 1         31   东京食尸鬼           168   2026-11-20
 2       1728   一人之下             520   2026-11-22
 3       2000   斗破苍穹·重制版       300   2026-11-21
```

交互模式里输入 `list` 也能查看。

**映射规则——一个漫画名称只对应一个 Comic ID：**

- **同名不同 ID**（续作、同名作品）：先登记的赢，后来的 ID 记为 `aliases`，不会多出一行；
- **同一 ID 改名**：新名字覆盖旧名，旧名当场释放；
- **手改 `.comics.json` 造成重复**：读取时按同样规则自动修复，不会把坏数据带进表格；
- **删除某条记录**：直接编辑 `.comics.json`（UTF-8、人可读）。

---

## 网页控制台

不想盯终端表格时，用浏览器看：

```bash
python download.py --serve                    # 只读：只能看
python download.py --serve --allow-write      # 可写：能登记 / 删除
python download.py --serve --port 9000        # 换端口
```

打开 `http://127.0.0.1:8787/`。数据仍是同一个 `.comics.json`，终端和网页改的是同一份文件。

**为什么这么选**：项目原本没有前端，也没有 Node 工具链。为了不引入新依赖、不引入构建链，后端直接用已经装好的 `aiohttp.web` 提供静态页和 JSON API，前端写原生 ES Module——不需要 npm、不需要打包，改完刷新就能看到。

**安全边界**（本地 HTTP 服务最容易忽略的部分）：

- 默认只绑 `127.0.0.1`，不对局域网开放；
- 写操作必须带 `X-Requested-With: manwa-web` 且 Origin 与 Host 一致，挡住任意网页往 localhost 发 POST（CSRF）；
- 默认只读，`--allow-write` 才开写；**服务端二次校验**，不靠前端藏按钮。

**前端目录**：

```
web/
├── index.html                 # 唯一入口（含首屏主题，防闪烁）
└── assets/
    ├── css/
    │   ├── tokens.css         # 设计变量：颜色 / 间距 / 字号 / 圆角，明暗两套
    │   ├── base.css           # reset + 排版 + 可访问性
    │   ├── layout.css         # 顶栏 / 内容容器 / 栅格
    │   ├── components.css     # 按钮 / 卡片 / 表格 / 骨架 / Toast
    │   └── pages.css          # 页面级微调
    └── js/
        ├── main.js            # 装配：外壳 → 拉 meta → 建路由 → 启动
        ├── core/
        │   ├── api.js         # fetch 封装：信封解析 / 超时 / ApiError
        │   ├── router.js      # hash 路由 + 权限守卫 + 骨架屏
        │   ├── store.js       # 极简订阅式状态
        │   ├── guard.js       # 能力清单（comics:read / comics:write）
        │   ├── theme.js       # 浅色 / 深色 / 跟随系统
        │   ├── feedback.js    # 顶部加载条 + 全局错误出口
        │   └── dom.js         # h() 元素工厂
        ├── components/        # 外壳 / 表格 / 骨架·空·错三态 / Toast
        └── pages/             # 总览 / 漫画表格 / 设置 / 404
```

**加一个页面**：在 `pages/` 放一个导出 `title`、`skeleton`、`requires`、`load(ctx)`、`render(data, ctx)` 的模块，再到 `main.js` 的 `ROUTES` 里加一行即可。路由内置了三件事——守卫、骨架屏、错误兜底，页面自己不用管。

**接口**（统一信封 `{ok, data}` / `{ok:false, error:{code, message}}`）：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/meta` | 版本、运行模式、能力清单、数据路径 |
| GET | `/api/comics` | 漫画列表 |
| POST | `/api/comics` | 登记一条（需写权限） |
| DELETE | `/api/comics/{cid}` | 删除一条（需写权限） |
| GET | `/api/health` | 存活探针 |

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
│   ├── comics.py            # 漫画表格：名称 ⇄ Comic ID 登记 + 实时刷新
│   ├── config.py            # 配置加载、域名探测、限速器、日志、信号处理
│   ├── crypto_utils.py      # AES-256-CBC 图片解密
│   ├── downloader.py        # 异步下载流水线、线程池、统计
│   ├── utils.py             # 文件名清洗、章节号解析等纯函数
│   └── web.py               # 网页控制台：aiohttp 静态服务 + JSON API
├── web/                     # 前端（原生 ES Module，零构建）
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

### v5.3 (2026-11-22)
新增本地网页控制台：`--serve` 用已有的 `aiohttp.web` 提供页面与 JSON API，前端为原生 ES Module（零构建、零新依赖）。含全局外壳与 hash 路由、能力级权限与 CSRF 防护、统一请求封装 / 错误态 / 骨架屏 / Toast、明暗主题与四个页面模板（总览 / 表格 / 设置 / 404）。

### v5.2 (2026-11-22)
新增漫画表格：名称与 Comic ID 一对一登记（同名只留一条映射、重复 ID 记别名），`--list` 查看、`--watch` 实时刷新，下载 / `--info` / 交互模式运行时自动登记。

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
