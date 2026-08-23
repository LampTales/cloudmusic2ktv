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
├─ instance/                     运行时 Cookie；不应公开或提交
└─ outputs/                      下载素材、缓存、预览和视频；用户数据
```

`instance/` 和 `outputs/` 是运行时状态，不应当在重构或测试中随意删除。

## 4. 顶层 Web 应用

`app.py` 在模块导入时创建三个单例：

- `client = NeteaseClient(...)`：共享 `requests.Session` 和网易云 Cookie；
- `downloads = SongDownloadService(...)`：将素材写到 `outputs/`；
- `video_jobs = VideoJobManager(...)`：进程内视频任务管理器。

默认监听 `127.0.0.1:7860`，可用 `CLOUDMUSIC2KTV_HOST` 和 `CLOUDMUSIC2KTV_PORT` 覆盖。Flask 以 `threaded=True` 启动，但视频编码器自己的执行池只有一个 worker。

### API 一览

| 方法与路径 | 作用 | 主要输入 |
| --- | --- | --- |
| `GET /` | WebUI | 无 |
| `GET /api/status` | 查询登录状态 | 无 |
| `POST /api/auth/captcha` | 发送短信验证码 | `phone`, `country_code` |
| `POST /api/auth/login` | 手机号验证码登录 | `phone`, `captcha`, `country_code` |
| `POST /api/auth/logout` | 退出并删除本地 Cookie | 无 |
| `GET /api/search?q=...` | 搜索歌曲，最多 12 条 | 查询字符串 |
| `POST /api/song/inspect` | 按 ID/链接读取歌曲信息 | `song` |
| `POST /api/song/download` | 下载并校验全部素材 | `song`, `level` |
| `POST /api/video/preview` | 生成静态预览 | `song`, `options` |
| `POST /api/video/background` | 保存当前歌曲自定义背景 | multipart `song`, `background` |
| `POST /api/video/render` | 创建后台视频任务 | `song`, `options` |
| `GET /api/video/jobs/<job_id>` | 轮询任务状态 | job ID |
| `GET /api/video/artifact/<song_id>/<filename>` | 返回预览或视频 | 受文件名白名单限制 |

自定义背景上传上限由 Flask 的 `MAX_CONTENT_LENGTH = 32 * 1024 * 1024` 限制为 32 MiB。视频 artifact 路由只允许 `video_preview.png`、`ktv_1080p.mp4`、`ktv_720p.mp4`。

异常约定：

- `NeteaseError` 通常映射为 502；登录/权限相关错误映射为 401；
- `VideoError` 映射为 400；
- 未处理异常返回通用 500，并在终端记录堆栈；
- Flask 自己的 `HTTPException` 保持原状态，例如未知路由仍是 404。

## 5. 网易云访问层

`cloudmusic2ktv/netease.py` 中的 `NeteaseClient` 管理所有网易云请求。

### 会话与登录

- 使用一个长生命周期 `requests.Session`；
- User-Agent、Referer、Origin 模拟网易云网页播放器；
- 手机验证码发送使用 `/api/sms/captcha/sent`；
- 登录使用 `/weapi/login/cellphone`；
- Cookie 只持久化到 `instance/netease_cookies.json`；
- Cookie 文件解析失败时会清空内存会话，但不会阻止 UI 启动；
- 登出无论远端请求是否成功，都会清空内存 Cookie 并删除本地 Cookie 文件。

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
- 计算当前歌词/下一句、间奏空白和倒计时状态；
- 使用遮罩完成歌词从左到右的匀速扫色；
- 自动匹配封面强调色；
- 根据文本内容选择中文或日文字体。

当前字体候选写死为 Windows 字体路径：微软雅黑、游ゴシック、Meiryo，最后回退 Arial。这是移植到 macOS/Linux 前必须处理的约束。

### 固定时间规则

常量位于 `video.py` 顶部：

- `FPS = 30`；
- `OPENING_SECONDS = 4.0`；
- `INTERLUDE_THRESHOLD_MS = 15_000`；
- `INTERLUDE_COUNTDOWN_MS = 4_000`；
- `MAX_INTERLUDE_SWEEP_MS = 8_000`。

如果开场已启用且第一句歌词在歌曲开始 4 秒内出现，`VideoProject.pre_roll_ms()` 会添加完整 4 秒静音预卷。FFmpeg 通过 `-itsoffset` 后移原音频，视频总时长也相应增加。

### 频谱

`SpectrumData.load_or_create()` 使用 FFmpeg 将音频解码为 11025 Hz 单声道 float PCM，然后用 NumPy FFT 计算 64 个对数频带、30 FPS 的频谱数据。

缓存写入当前歌曲目录的 `spectrum_30fps.npz`。只要缓存时间不早于音频文件，就会复用。保存值为 float16，加载后转回 float32。

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
  "status": "queued | running | done | error",
  "progress": 0,
  "message": "...",
  "result": null,
  "error": null
}
```

后端重启会丢失全部任务状态，并终止正在运行的 FFmpeg/渲染过程。不要在视频生成期间重启服务。

### static/app.js

前端没有框架，核心状态为：

- `selectedSong`：02 区最后一次成功读取/点击的歌曲；
- `cloudmusic2ktv.selectedSongId`：localStorage 中用于刷新后恢复选歌的 ID；
- `previewRequest`：丢弃过期预览响应，避免较慢响应覆盖较新设置；
- `renderJobActive` / `renderTaskSong`：显示当前生成任务锁定的歌曲。

选歌规则是重要产品约束：

1. 只修改 ID 输入框不会修改 `selectedSong`；
2. “读取”成功或点击搜索结果才会调用 `showSong()`；
3. `showSong()` 同步 02/03 区、保存 ID、隐藏旧结果并请求新预览；
4. 点击“生成完整视频”时复制并冻结当时的歌曲和选项；
5. 生成期间再次选歌只改变下一次任务，不改变已经提交的任务。

任务 ID 目前只存在于页面 JavaScript 内存中，没有保存到 localStorage。刷新页面后后台任务仍可能继续，但 WebUI 无法恢复对该任务的轮询。这是一个明确的后续改进点。

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
| `video_preview.png` | 每次预览覆盖 |
| `ktv_1080p.mp4`、`ktv_720p.mp4` | 同分辨率再次生成时覆盖 |

由于 `VideoProject.load()` 当前取第一个 `audio.*`/`cover.*`，扩展名变化后残留多个文件可能导致选中旧文件。精细调整阶段建议优先引入 manifest 或在安全确认目标目录后清理同类旧文件。

## 10. 测试覆盖

当前测试均为离线测试，不会登录网易云或下载真实歌曲：

- `test_crypto.py`：固定密钥下 weapi 输出字段、RSA 长度、AES block；
- `test_lyrics.py`：LRC 小数位、多时间戳、翻译/罗马音合并；
- `test_service.py`：歌曲 ID/链接解析、Windows 非法文件名替换；
- `test_video.py`：选项校验、开场预卷、间奏状态、预览尺寸、歌词过滤、后台任务完成；
- `test_web.py`：未知路由保持 404。

修改后最低验证：

```powershell
.venv\Scripts\python.exe -m pytest -q
node --check static\app.js
```

涉及 UI 时还应启动本地服务并实际验证：读取歌曲、03 区目标同步、刷新恢复选歌、预览更新。涉及视频布局时，至少生成静态预览；涉及音视频同步、频谱或 FFmpeg 参数时，应生成一段受限时长测试视频或一首完整测试视频。

## 11. 已知限制与优先改进点

1. 网易云内部接口可能变化，错误诊断优先查看终端日志、`lyrics_raw.json` 和 `metadata.json`。
2. 歌词扫色以逐行时间轴匀速估算，不是真正逐字同步；原始 `klyric` 已保留，但尚未解析成逐字模型。
3. 视频渲染为 CPU 密集型 Python 逐帧流程，1080p 完整歌曲较慢。
4. 任务仅在内存中，刷新无法恢复轮询，后端重启无法恢复任务。
5. 任务执行池只有一个 worker，没有取消、暂停和队列管理 UI。
6. 字体搜索只支持当前 Windows 路径。
7. 同歌多版本/不同音频扩展名缺少 manifest，可能选到残留旧文件。
8. 前端是单文件原生 JavaScript，继续增加复杂状态前可考虑拆分模块，但应保留当前清晰的选歌/任务锁定语义。

## 12. 修改时必须保留的安全边界

- 不要把 `instance/netease_cookies.json` 输出到日志、测试结果或对话；
- 不要删除或覆盖 `outputs/` 中用户文件，除非目标和授权非常明确；
- 不要用未登录方案绕过付费或播放权限；只使用当前账号依法拥有的播放权益；
- 下载仍应使用 `.part` 临时文件和成功后替换，避免中断留下伪完整文件；
- 视频任务运行时不要重启服务；
- WebUI 默认保持仅监听 `127.0.0.1`，避免将登录入口和本地文件服务暴露到局域网；
- 新增可下载文件时同步更新 artifact 路由白名单，避免任意路径读取。
