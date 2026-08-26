# CloudMusic2KTV 仓库导览与架构说明

本文面向维护者和后续接手仓库的 Agent，记录当前实现的真实结构、数据流、关键约束与修改入口。用户安装和操作说明见 [README.md](README.md)。

## 1. 项目边界

CloudMusic2KTV 是一个本地 Flask WebUI，完成两条连续流水线：

```text
网易云账号与歌曲
    ↓
NeteaseClient：登录、搜索、歌曲信息、歌词、播放地址、流式下载
    ↓
SongDownloadService：校验并落盘音频/封面/歌词/时间轴
    ↓
VideoProject：从 outputs/ 重新装载一首歌的本地素材
    ↓
FrameRenderer + SpectrumData：逐帧画面与频谱
    ↓
FFmpeg：H.264 视频 + AAC 音频 → MP4
```

项目不调用网易云客户端下载功能，也没有浏览器扩展。完整音频来自网页播放器接口，能否取得由当前登录账号的实际播放权益决定。

## 2. 新会话快速上手

建议按以下顺序阅读：

1. `README.md`：用户流程和当前产品行为；
2. `app.py`：全部 HTTP 路由和顶层对象；
3. `cloudmusic2ktv/netease.py`：网易云会话与接口；
4. `cloudmusic2ktv/service.py`、`cloudmusic2ktv/lyrics.py`：素材落盘和歌词时间轴；
5. `cloudmusic2ktv/video.py`：视频选项、渲染器、频谱和后台任务；
6. `static/app.js`：前端状态机、预览请求和任务轮询；
7. `tests/`：已有行为契约。

常用命令：

```powershell
.venv\Scripts\python.exe -X utf8 app.py
.venv\Scripts\python.exe -m pytest -q
node --check static\app.js
```

当前工作区的 `.venv` 使用 Python 3.11，来源是本机 Anaconda Python；它启用了 `include-system-site-packages`。这是当前机器的环境状态，不是项目必须依赖 Anaconda 的要求。

## 3. 仓库结构

```text
cloudmusic2ktv/
├─ app.py                         Flask 应用、API 路由、错误映射
├─ requirements.txt              Python 运行依赖
├─ README.md                     面向用户的使用文档
├─ ARCHITECTURE.md               本文
├─ cloudmusic2ktv/
│  ├─ __init__.py                对外导出客户端、异常和下载服务
│  ├─ netease.py                 网易云 HTTP 会话、weapi 加密、Cookie
│  ├─ sessions.py                按浏览器隔离的文件会话、过期与清理
│  ├─ service.py                 歌曲 ID 解析、素材下载、文件校验与落盘
│  ├─ lyrics.py                  LRC 解析和统一时间轴合并
│  └─ video.py                   视频选项、素材装载、逐帧渲染、频谱、任务队列
├─ templates/
│  └─ index.html                 单页 WebUI 结构
├─ static/
│  ├─ app.js                     WebUI 交互与前端状态
│  └─ app.css                    页面样式
├─ tests/
│  ├─ test_crypto.py             weapi 加密结构
│  ├─ test_lyrics.py             LRC 与多语言时间轴
│  ├─ test_service.py            ID/链接解析和安全文件名
│  ├─ test_video.py              视频选项、间奏、预览与后台任务
│  └─ test_web.py                Flask 错误路由行为
├─ instance/sessions/            按浏览器隔离的运行时 Cookie；不应公开或提交
└─ outputs/                      下载素材、缓存、预览和视频；用户数据
```

`instance/` 和 `outputs/` 是运行时状态，不应当在重构或测试中随意删除。

## 4. 顶层 Web 应用

`app.py` 在模块导入时创建主要单例：

- `auth_sessions = FileSessionStore(...)`：用浏览器随机 Cookie 映射网站账号会话文件；
- `allowlist = AllowlistStore(...)`：在 `instance/allowlist.json` 保存网易云 `userId` 与 `admin/user` 角色；首个成功登录的账号自动初始化为管理员；
- `website_accounts = WebsiteAccountStore(...)`：在 `instance/accounts.json` 保存网站用户名、密码哈希和不可换绑的网易云 `userId`；
- `netease_bindings = NeteaseBindingStore(...)`：在 `instance/netease_bindings.json` 按网站账号绑定的网易云 `userId` 保存共享 Cookie；
- `video_jobs = VideoJobManager(...)`：进程内视频任务管理器。

`NeteaseClient` 和 `SongDownloadService` 不再全局共享，而是根据当前网站账号绑定的网易云 Cookie 或匿名模式为每次请求创建。歌曲素材、下载互斥状态和视频队列仍是全局共享状态。

默认监听 `0.0.0.0:7860`，可用 `CLOUDMUSIC2KTV_HOST` 和 `CLOUDMUSIC2KTV_PORT` 覆盖；只允许本机访问时将 `CLOUDMUSIC2KTV_HOST` 设为 `127.0.0.1`。Flask 以 `threaded=True` 启动，但视频编码器自己的执行池只有一个 worker。局域网测试可以同时设置 `CLOUDMUSIC2KTV_TLS_CERT` 和 `CLOUDMUSIC2KTV_TLS_KEY`，让内置服务器直接使用本地 CA 证书提供 HTTPS；两个路径支持相对于项目根目录的写法。未设置时保持 HTTP。长期公网部署时也可由外层反向代理终止正式 HTTPS；此时仅在代理可信且会覆盖转发头时设置 `CLOUDMUSIC2KTV_TRUST_PROXY=1`，应用会同时信任 `X-Forwarded-Proto`、`X-Forwarded-Host` 和 `X-Forwarded-Prefix`。如果应用发布在域名子路径（例如 `/ktv`），设置 `CLOUDMUSIC2KTV_BASE_PATH=/ktv`，并让反代剥离该前缀后转发；前端 API、静态资源、artifact URL 和会话 Cookie Path 会跟随该前缀。直接暴露应用时不要设置 `CLOUDMUSIC2KTV_TRUST_PROXY`。

### API 一览

| 方法与路径 | 作用 | 主要输入 |
| --- | --- | --- |
| `GET /` | WebUI | 无 |
| `GET /api/status` | 查询登录状态 | 无 |
| `GET /api/auth/csrf` | 为 Cookie 导入创建一次 CSRF 令牌并建立临时会话 | 无 |
| `GET /api/auth/netease-status` | 使用当前绑定账号的用户主页检查网易云 Cookie 是否有效 | 名单成员会话 |
| `POST /api/auth/captcha` | 为新建网站用户或重新验证网易云账号发送短信验证码 | `phone`, `country_code` |
| `POST /api/auth/qr/start` | 创建当前网易云网页扫码验证二维码 | 无 |
| `POST /api/auth/qr/poll` | 轮询扫码验证状态；成功后得到网易云身份 | 无 |
| `POST /api/auth/login` | 网站用户名密码登录，不调用网易云登录接口 | `username`, `password` |
| `POST /api/auth/register` | 验证名单内网易云账号并创建不可换绑的网站用户 | 网站账号、`cookies` + `csrf_token`，或旧版手机号/验证码、`qr: true` |
| `POST /api/auth/reauth` | 重新验证当前绑定的网易云账号并更新共享 Cookie | `cookies` + `csrf_token`，或旧版手机号/验证码、`qr: true` |
| `POST /api/auth/logout` | 退出网站账号，不删除共享网易云绑定 | 无 |
| `GET /api/search?q=...` | 搜索歌曲（名单成员） | 查询字符串 |
| `POST /api/song/inspect` | 按 ID/链接读取歌曲信息（名单成员） | `song` |
| `GET /api/song/local/<song_id>` | 查询共享素材状态（名单成员） | song ID |
| `POST /api/song/download` | 下载并校验全部素材（名单成员） | `song`, `level` |
| `POST /api/video/preview` | 生成静态预览（名单成员） | `song`, `options` |
| `POST /api/video/background` | 保存当前歌曲自定义背景（名单成员） | multipart `song`, `background` |
| `POST /api/video/render` | 创建后台视频任务（名单成员） | `song`, `options` |
| `GET /api/video/queue` | 查询当前任务、等待数量和最近结果（名单成员） | 无 |
| `GET /api/video/local/<song_id>` | 扫描当前歌曲可投屏的本地 MP4（名单成员） | song ID |
| `GET /api/video/jobs/<job_id>` | 轮询任务状态（名单成员） | job ID |
| `GET /api/video/artifact/<song_id>/<filename>` | 返回预览或视频（名单成员）；`download=1` 时作为附件下载并使用歌曲名/作者命名 | 受文件名白名单限制 |
| `GET /api/admin/users`、`/api/admin/search-users` | 查看名单、搜索网易云用户（管理员） | 查询字符串 |
| `POST/DELETE /api/admin/users` | 添加或删除名单账号（管理员；不能删除管理员） | 用户 ID、角色 |

自定义背景上传上限由 Flask 的 `MAX_CONTENT_LENGTH = 32 * 1024 * 1024` 限制为 32 MiB。视频 artifact 路由只允许旧版固定文件名或符合 `video_preview_<12位哈希>.png`、`ktv_<分辨率>_<12位哈希>.mp4` 的文件名，并支持 HEAD 与 HTTP Range。artifact 查找不依赖源素材仍然完整存在，但会拒绝输出目录之外的解析路径。下载附件时，后端根据 `metadata.json` 将视频命名为 `ktv_<分辨率>_<作者>_<歌曲名>.mp4`；服务器内部文件仍保留选项哈希，以区分不同配置。

异常约定：

- `NeteaseError` 通常映射为 502；登录/权限相关错误映射为 401；
- `VideoError` 映射为 400；
- 未处理异常返回通用 500，并在终端记录堆栈；
- Flask 自己的 `HTTPException` 保持原状态，例如未知路由仍是 404。

## 5. 网易云访问层

`cloudmusic2ktv/netease.py` 中的 `NeteaseClient` 管理所有网易云请求。

### 会话与登录

- 每个浏览器通过 `HttpOnly`、`SameSite=Lax` 的随机会话 Cookie 标识网站账号；
- 网站用户名密码保存在 `instance/accounts.json`，密码使用 scrypt 哈希；
- 网易云 Cookie 按绑定的网易云 `userId` 保存在 `instance/netease_bindings.json`，同一网站账号的不同设备共享这份绑定；
- Cookie 导入支持浏览器 JSON 文件和粘贴的 JSON 数组；服务端只保留 `music.163.com` 域 Cookie，过滤其他域并按名称去重，避免多个 `__csrf` Cookie 造成请求歧义；
- Cookie 导入请求需要一次性会话 CSRF 令牌，并默认要求 HTTPS；本机回环地址可用于开发测试，受信任内网临时测试可显式设置 `CLOUDMUSIC2KTV_ALLOW_INSECURE_COOKIE_IMPORT=1`；
- Cookie 导入完成后只调用一次账号状态接口验证身份，失败时清空临时 Cookie，不写入绑定文件；成功后仅保存筛选后的 Cookie，并校验名单和不可换绑的 `userId` 关系；
- 已登录浮窗的网易云状态检查只调用当前绑定 Cookie 的登录状态接口并校验返回的 `userId`，不会修改绑定文件；网易云鉴权失败时返回需要重新验证的状态，网络等其他错误仍按服务错误处理；
- 身份使用时进行单文件惰性过期检查，默认 90 天；服务启动和成功登录后遍历清理过期文件；
- 网站登录成功后轮换浏览器会话 Cookie；退出网站不会注销网易云或删除绑定；
- 绑定文件使用 `.part` 原子替换；
- User-Agent、Referer、Origin 模拟网易云网页播放器；
- `requests.Session.trust_env` 默认关闭，避免继承启动环境中的无效 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`；确需环境代理时可显式构造 `NeteaseClient(trust_env_proxy=True)`；
- 手机验证码发送使用 `/api/sms/captcha/sent`；
- 扫码验证使用当前网页实际发出的加密接口 `/weapi/login/qrcode/unikey` 与 `/weapi/login/qrcode/client/login`，二维码短期凭证记录在网站服务端会话中；浏览器会尝试生成当前网页的 `ydDeviceToken` 并只在轮询请求中转发，不写入本地文件；
- 网易云返回 8821/8830 时表示扫码授权链路被风控拒绝，前端会停止轮询并要求刷新二维码；这不是名单权限校验失败；
- 二维码轮询间隔约 1.2 秒，服务端凭证有效期为 5 分钟；前端在成功、过期、错误、关闭浮窗或切换短信/Cookie 方式时停止轮询，并防止网络较慢时产生重叠请求。
- 短信登录仍保留 `/weapi/login/cellphone` 兼容回退；网易云当前网页的 `/api/login/cellphone` 需要浏览器私有的 encrypt-fetch 与反作弊令牌，不能仅靠普通服务端请求替代；
- 未登录网站账号时只能访问网站登录入口；网站账号已登录但网易云 Cookie 失效时，公开歌曲接口和免费歌曲下载可以回退匿名客户端，本地资源仍可访问，付费歌曲会要求重新验证原绑定账号。

### 歌曲接口

- 搜索：`/weapi/search/get`；
- 歌曲详情：`/api/song/detail/`；
- 歌词：`/api/song/lyric/v1`；
- 播放地址：`/weapi/song/enhance/player/url/v1`；
- 封面和音频本体通过 `Session.get(..., stream=True)` 分块下载。

`weapi_payload()` 实现网易云网页端的双层 AES-CBC 和 RSA `encSecKey` 封装。相关公钥参数位于该模块顶部，`test_crypto.py` 只验证结构稳定性，不做线上接口测试。

`normalize_song()` 将网易云多种响应结构统一成：

```json
{
  "id": 642810,
  "name": "歌曲名",
  "artists": ["歌手"],
  "artist": "歌手",
  "album": "专辑",
  "cover_url": "https://...",
  "duration_ms": 123000,
  "fee": 1,
  "copyright": 1
}
```

标准化前的源对象只在内部 `source` 字段中保留，返回给 WebUI 或写入标准 metadata 前会由 `public_song()` 去除。

## 6. 素材下载与歌词时间轴

`SongDownloadService.download()` 的顺序是：

1. 读取歌曲详情；
2. 请求歌词；
3. 请求当前音质的完整播放地址；
4. 创建 `outputs/<ID>_<歌手>_<歌名>/`；
5. 下载 1200 × 1200 封面；
6. 流式下载音频；
7. 校验网易云提供的 size 和可选 MD5；
8. 生成统一歌词时间轴；
9. 写入 metadata、原始响应和各类 LRC。

音频和封面先写入 `.part` 临时文件，成功后才替换正式文件；异常时会清理临时文件。

### 歌词时间轴

`lyrics.py` 支持标准 LRC、多时间戳行，以及 1～3 位小数时间。`build_timeline()` 以原文歌词为主轴，通过完全相同的 `start_ms` 合并翻译和罗马音：

```json
[
  {
    "start_ms": 1000,
    "end_ms": 3500,
    "text": "第一句",
    "translation": "first line",
    "romanization": null
  }
]
```

当前时间轴是逐行时间轴，不是逐字时间轴。`end_ms` 通常取下一句的 `start_ms`；最后一句默认延长 5 秒。渲染器据此做行内匀速扫色。

## 7. 视频渲染

视频实现集中在 `cloudmusic2ktv/video.py`。

### VideoOptions

`VideoOptions` 是 frozen dataclass；进入后端后会验证所有枚举、颜色和数值范围。

| 字段 | 允许值/范围 | 默认值 |
| --- | --- | --- |
| `lyric_mode` | `original`, `translation`, `romanization` | `original` |
| `background_mode` | `blur`, `gradient`, `solid`, `custom` | `blur` |
| `background_color` | `#RRGGBB` | `#171b26` |
| `accent_mode` | `blue`, `cover`, `custom` | `blue` |
| `accent_color` | `#RRGGBB` | `#4f8cff` |
| `spectrum` | bool | `true` |
| `spectrum_opacity` | 0.1～1.0 | 0.65 |
| `resolution` | `1080p`, `720p` | `1080p` |
| `quality` | `high`, `balanced`, `compact` | `balanced` |
| `opening` | bool | `true` |
| `interlude_cue` | bool | `true` |

画质映射到 FFmpeg：

- `high` → CRF 17、preset slow；
- `balanced` → CRF 20、preset medium；
- `compact` → CRF 24、preset fast。

### VideoProject

`VideoProject.load()` 按歌曲 ID 从 `outputs/` 查找目录，并要求存在：

- `metadata.json`；
- `lyrics_timeline.json`；
- 一个 `audio.*`；
- 一个 `cover.*`。

加载时会过滤空歌词、`~music~`、`instrumental`、`间奏`等非显示标记。当前实现对匹配目录、音频和封面都取排序/遍历得到的第一个结果；如果未来支持同歌多版本或重复下载不同扩展名，这里需要重构为明确 manifest。

### FrameRenderer

同一个 `FrameRenderer` 同时服务静态预览与完整视频。主要职责：

- 根据分辨率计算缩放比例；
- 生成封面模糊、封面渐变、纯色或自定义背景；
- 绘制歌名、歌手、封面、右上角时间；
- 绘制频谱；
- 计算当前歌词/下一句、间奏空白和倒计时状态；间奏前隐藏下一句，间奏后的第一句重置到左上歌词位；
- 间奏倒计时条显示在左上歌词上方并靠左，避免占用画面底部；
- 使用遮罩完成歌词从左到右的匀速扫色；
- 自动匹配封面强调色；
- 根据文本内容选择中文或日文字体。

字体候选支持 `CLOUDMUSIC2KTV_FONT_DIR`，并内置 Linux Noto CJK、macOS PingFang/Hiragino、Windows 微软雅黑/游ゴシック/Meiryo 路径；Docker 镜像安装 `fonts-noto-cjk`。如果运行环境使用其他字体，可通过该变量指定字体目录。

### 固定时间规则

常量位于 `video.py` 顶部：

- `FPS = 30`；
- `OPENING_SECONDS = 4.0`；
- `OPENING_HOLD_MS = 3_000`、`OPENING_TRANSITION_MS = 1_000`；
- 片头先静止展示封面和歌曲信息 3 秒，最后 1 秒先让封面移动并让片头文字快速淡出，待封面到位后再让固定位置的黑胶快速淡入，正片组件也只做快速淡入；4 秒后直接进入正常画面；
- `INTERLUDE_THRESHOLD_MS = 15_000`；
- `INTERLUDE_COUNTDOWN_MS = 4_000`；
- `MAX_INTERLUDE_SWEEP_MS = 8_000`。

如果开场已启用且第一句歌词在歌曲开始 4 秒内出现，`VideoProject.pre_roll_ms()` 会添加完整 4 秒静音预卷。FFmpeg 通过 `-itsoffset` 后移原音频，视频总时长也相应增加。

### 频谱

`SpectrumData.load_or_create()` 使用 FFmpeg 将音频解码为 11025 Hz 单声道 float PCM，然后用 NumPy FFT 计算 64 个对数频带、30 FPS 的频谱数据。对数边界会映射到实际 FFT bin，并保证每个频带至少包含一个 bin，避免低频段出现恒为零的空频带。

缓存写入当前歌曲目录的 `spectrum_30fps.npz`，包含分频算法版本标记；只有缓存时间不早于音频文件且版本匹配时才会复用。保存值为 float16，加载后转回 float32。分频算法升级后旧缓存会自动重新分析。

### 编码

完整视频由 Pillow 逐帧生成 RGB24 字节，经 stdin 输送给 `imageio-ffmpeg` 提供的 FFmpeg：

- 视频：libx264、yuv420p；
- 音频：AAC 256 kbps；
- MP4 使用 `+faststart`；
- 先写 `ktv_<resolution>.part.mp4`，成功后替换正式文件；
- 编码失败会清理临时视频。

## 8. 后台任务与前端状态

### VideoJobManager

`VideoJobManager` 使用 `ThreadPoolExecutor(max_workers=1)`，因此所有视频任务串行执行。任务对象只保存在当前 Python 进程内：

```json
{
  "id": "uuidhex",
  "song_id": 123,
  "song": {"name": "歌曲名", "artist": "歌手", "cover_url": "..."},
  "resolution": "720p",
  "status": "queued | running | done | error",
  "progress": 0,
  "message": "...",
  "result": null,
  "error": null
}
```

任务不保存提交者身份；`POST /api/video/render` 在提交时检查当前浏览器的名单成员身份。相同歌曲和完全相同选项的活动任务会去重。视频队列和 artifact 接口也要求名单成员会话。

后端重启会丢失全部任务状态，并终止正在运行的 FFmpeg/渲染过程。不要在视频生成期间重启服务。

### static/app.js

前端没有框架，核心状态为：

- `selectedSong`：02 区最后一次成功读取/点击的歌曲；
- `selectedSongLocal`：当前歌曲共享素材的 `missing/partial/downloading/ready/error` 状态；
- `accountLoggedIn`：当前浏览器的网站账号登录状态；网易云绑定是否存在由状态接口单独返回；
- `cloudmusic2ktv.selectedSongId`：localStorage 中用于刷新后恢复选歌的 ID；
- `previewRequest`：丢弃过期预览响应，避免较慢响应覆盖较新设置；
- `queueTimer`：仅在网站账号已登录时轮询当前名单成员可见的视频队列；未登录时停止定时器，避免反复请求受保护接口并污染错误日志。
- `selectedSongVideos`：03 区为当前歌曲从本地扫描到的可投屏 MP4；不依赖登录或素材完整状态；
- `videoStatusRequest`：丢弃切歌后才返回的过期本地视频查询。

选歌规则是重要产品约束：

1. 只修改 ID 输入框不会修改 `selectedSong`；
2. “读取”成功或点击搜索结果才会调用 `showSong()`；
3. 搜索结果列表不依赖搜索接口中的封面字段；点击结果后按歌曲 ID 调用 inspect，重新取得完整详情和下载区封面；
4. `showSong()` 同步 02/03 区、保存 ID、隐藏旧结果并查询共享素材；
5. 本地已有完整 metadata、歌词、音频和封面时直接开放预览，不依赖登录或网易云网络；
6. 视频提交按钮只有在素材完整且当前浏览器的网站账号已登录时可用；提交成功后立即恢复，不跟踪“我的任务”。

前端不保存任务 ID，也不建立“我的任务”关系。刷新页面后通过全局队列接口恢复当前任务和进度视图；“等待 n”按钮打开的浮窗直接展示全局当前任务与完整等待列表。

03 区的“使用投屏应用打开”优先使用 Web Share API 分享基于当前页面 origin 的视频 URL，因此局域网访问和未来公网反向代理无需分别配置媒体主机名。非安全上下文（典型为 `http://192.168.x.x`）无法使用 Web Share 时退化为复制 URL，供投屏 App 手动打开。DLNA 接收端只读取受名单成员授权的视频 artifact，不接触网易云 Cookie。

同一区域的实验按钮使用一个不可见但实际挂载媒体 URL 的 `<video>`：优先调用 `HTMLMediaElement.remote.prompt()`，Safari 下退回 `webkitShowPlaybackTargetPicker()`。调用直接发生在按钮点击栈中，以保留浏览器要求的瞬时用户激活；前端监听 Remote Playback 的 `connecting/connect/disconnect` 事件更新提示。此入口只委托浏览器打开设备列表，不直接实现 SSDP、DLNA SOAP 或设备发现。

## 9. 输出目录与覆盖行为

每首歌共用一个输出目录：

```text
outputs/<歌曲ID>_<歌手>_<歌名>/
```

关键文件的写入行为：

| 文件 | 行为 |
| --- | --- |
| `metadata.json`、歌词文件 | 再次下载时覆盖 |
| `audio.<ext>`、`cover.<ext>` | 相同扩展名覆盖；扩展名变化时旧文件可能保留 |
| `custom_background.png` | 再次上传时覆盖 |
| `spectrum_30fps.npz` | 音频更新后重新生成 |
| `video_preview_<hash>.png` | 按视频选项哈希区分 |
| `ktv_<resolution>_<hash>.mp4` | 按视频选项哈希区分，避免不同配置互相覆盖 |

用户通过“下载视频到本地”取得的附件不使用上述内部哈希文件名，而是使用 `ktv_<分辨率>_<作者>_<歌曲名>.mp4`；如果缺少有效 metadata，则回退为服务器文件名。

由于 `VideoProject.load()` 当前取第一个 `audio.*`/`cover.*`，扩展名变化后残留多个文件可能导致选中旧文件。精细调整阶段建议优先引入 manifest 或在安全确认目标目录后清理同类旧文件。

## 10. 测试覆盖

当前测试均为离线测试，不会登录网易云或下载真实歌曲：

- `test_crypto.py`：固定密钥下 weapi 输出字段、RSA 长度、AES block；
- `test_lyrics.py`：LRC 小数位、多时间戳、翻译/罗马音合并；
- `test_service.py`：歌曲 ID/链接解析、Windows 非法文件名替换；
- `test_sessions.py`：文件会话保存、恢复、惰性过期和清理；
- `test_video.py`：选项校验、开场预卷、间奏状态、预览尺寸、歌词过滤、后台任务完成；
- `test_web.py`：未知路由保持 404。

修改后最低验证：

```powershell
.venv\Scripts\python.exe -m pytest -q
node --check static\app.js
```

涉及 UI 时还应启动本地服务并实际验证：读取歌曲、03 区本地视频同步、04 区目标同步、刷新恢复选歌、预览更新。涉及视频布局时，至少生成静态预览；涉及音视频同步、频谱或 FFmpeg 参数时，应生成一段受限时长测试视频或一首完整测试视频。

## 11. 已知限制与优先改进点

1. 网易云内部接口可能变化，错误诊断优先查看终端日志、`lyrics_raw.json` 和 `metadata.json`。
2. 歌词扫色以逐行时间轴匀速估算，不是真正逐字同步；原始 `klyric` 已保留，但尚未解析成逐字模型。
3. 视频渲染为 CPU 密集型 Python 逐帧流程，1080p 完整歌曲较慢。
4. 任务仅在内存中；页面刷新可恢复全局视图，但后端重启无法恢复任务。
5. 任务执行池只有一个 worker，没有取消、暂停或持久化恢复。
6. 字体搜索只支持当前 Windows 路径。
7. 同歌多版本/不同音频扩展名缺少 manifest，可能选到残留旧文件。
8. 前端是单文件原生 JavaScript，继续增加复杂状态前可考虑拆分模块，但应保留当前清晰的选歌/任务锁定语义。

## 12. 修改时必须保留的安全边界

- 不要把 `instance/sessions/` 中的文件或浏览器会话 Cookie 输出到日志、测试结果或对话；
- 不要删除或覆盖 `outputs/` 中用户文件，除非目标和授权非常明确；
- 不要用未登录方案绕过付费或播放权限；只使用当前账号依法拥有的播放权益；
- 下载仍应使用 `.part` 临时文件和成功后替换，避免中断留下伪完整文件；
- 视频任务运行时不要重启服务；
- WebUI 默认监听 `0.0.0.0` 以支持局域网设备；不要将开发服务器直接暴露到公网，需要本机模式时显式设置 `CLOUDMUSIC2KTV_HOST=127.0.0.1`；
- 新增可下载文件时同步更新 artifact 路由白名单，避免任意路径读取。
