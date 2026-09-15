# CloudMusic2KTV 架构与开发指南

本文面向维护者、开发者和自动化 agents，描述当前代码的边界、数据流、持久化格式、修改约束和本地调试方法。实现和测试优先于本文；改动行为时应同时更新文档和测试。

本仓库地址是 [github.com/LampTales/cloudmusic2ktv](https://github.com/LampTales/cloudmusic2ktv)。歌词预处理依赖位于独立仓库 [github.com/LampTales/lyric_align](https://github.com/LampTales/lyric_align)；本文只记录集成边界。

## 仓库边界

本文只描述 `local` 仓库。仓库内包含前端、后端、渲染器、测试和 `deploy-examples/` 部署示例。

歌词读音与字级对齐由独立的 `lyric_align` 仓库作为外部依赖提供；本文只记录双方的集成契约，不把它当作本仓库目录。`deploy-examples/` 目录中的 Compose 文件和环境模板是本仓库提供的示例，实际运行数据由部署者在仓库外的挂载目录管理。

## 运行时拓扑

```text
浏览器 ── HTTPS ──► 前端 Nginx / frontend_server.py
                         │ 静态资源、Cookie、Range、/api 代理
                         ▼
                    Flask / Gunicorn（单 worker）
              ┌──────────┼──────────┬──────────┐
           instance/   outputs/   网易云 API  FFmpeg/lyric_align
```

后端是 API-only，不提供页面；前端是静态资源和代理。后端返回的 artifact URL 是外部代理路径，浏览器始终以当前前端 origin 解析它。正式部署中前端节点承担媒体出口带宽，后端节点保存媒体文件。

## 代码导航

| 文件 | 职责 |
| --- | --- |
| `app.py` | Flask 初始化、代理修正、认证装饰器、所有路由和错误转换 |
| `cloudmusic2ktv/access.py` | 允许名单、角色和访问撤销 |
| `cloudmusic2ktv/accounts.py` | 网站账号密码、网易云绑定记录 |
| `cloudmusic2ktv/sessions.py` | 服务端文件会话和 TTL |
| `cloudmusic2ktv/netease.py` | weapi 加密、网易云请求、歌曲/歌单规范化 |
| `cloudmusic2ktv/playlist_cache.py` | 按网易云用户隔离的 TTL/LRU 歌单缓存 |
| `cloudmusic2ktv/service.py` | 歌曲 ID、素材下载、时间轴、源文件和衍生文件清理 |
| `cloudmusic2ktv/lyrics.py` | LRC 解析和多语言统一时间轴 |
| `cloudmusic2ktv/video.py` | 视频选项、帧渲染、频谱、预览、模型预处理和任务队列 |
| `frontend/index.html`、`frontend/static/*` | 页面、状态机、API 调用、队列轮询和样式 |
| `frontend_server.py` | 本地静态服务器与 API 开发代理 |
| `Dockerfile.*`、`deploy-examples/*.yml` | 两个生产镜像和分机部署示例 |
| `tests/` | 后端、前端、渲染、持久化和部署配置契约 |

## 后端初始化与认证

`app.py` 导入时创建各类 JSON/文件 store、`VideoJobManager` 和 `PlaylistCache`。认证装饰器分为 `member_required`（验证并续期）、`read_only_member_required`（只读验证，队列轮询使用）和 `admin_required`（root/admin）。允许名单按网易云 `userId` 判断，空名单首次注册自动创建 root。Cookie 是 HttpOnly、服务端存储。

`CLOUDMUSIC2KTV_TRUST_PROXY=1` 时只信任一层受控代理的 `X-Forwarded-*`；`CLOUDMUSIC2KTV_BASE_PATH` 同时影响 Cookie Path、artifact URL 和应用根路径。生产优先同源代理，跨域直连仅用于诊断。

## HTTP API 分组

路由均位于 `/api` 下，错误统一返回 JSON；未知根路径和静态路径为 404。

| 分组 | 典型接口 | 权限 |
| --- | --- | --- |
| 健康与状态 | `/healthz`、`/status` | healthz 公共 |
| 网站认证 | `/auth/register`、`login`、`logout`、`csrf` | 按流程 |
| 网易云绑定 | 二维码、Cookie、身份确认、绑定状态 | 已登录成员 |
| 搜索与歌单 | `/search`、`/playlists`、`/playlists/<id>/tracks` | 成员 |
| 素材 | `/song/inspect`、`/song/local`、`/song/download` | 成员 |
| 视频 | `/video/preview`、`background`、`render`、`queue`、`job/<id>` | 成员 |
| artifact | `/video/artifact/<song_id>/<filename>`、`/video/share/...` | 会话或短期签名 |
| 管理 | `/admin/users` 及搜索、增删改角色 | root/admin |

新增接口必须同步更新鉴权要求、错误码、前端调用和 `tests/test_web.py`。

## 网易云与素材流水线

`SongDownloadService.download()` 按“歌曲详情 → 歌词 → 播放 URL → `.part` 下载封面/音频 → 大小和 MD5 校验 → 写 metadata、原始歌词、统一时间轴 → 清理旧衍生文件”的顺序工作。重新下载只有新源文件全部成功后才删除视频、频谱、对齐和 stems；同歌有 queued/running 视频任务时拒绝下载。

歌曲目录包含 `metadata.json`、`audio.*`、`cover.*`、四种 LRC、`lyrics_timeline.json`，以及模型产物 `alignment.json`、`preprocessing.json`、`stems/`、频谱和 `ktv_*.mp4`/预览图。

## 歌词对齐与视频流水线

`lyrics.py` 将网易云多语言 LRC 合并为毫秒时间轴；`VideoProject.load()` 过滤不可显示行并加载合法 alignment。`legacy` 仅使用网易云时间轴；`model` 在同一任务中执行 lyric_align 的 reading、可选 Demucs 和 CTC，再渲染视频。纯伴奏、发音标注和平滑 sweep 要求 model。渲染器不得重新推断字级时间。

`VideoOptions` 覆盖歌词语言、扫色、背景、强调色、音频、分辨率、画质、开场、间奏和频谱。`FrameRenderer` 同时用于预览和正式视频；FFmpeg 输出 H.264/AAC。视频先写 `.part.mp4` 后原子替换，artifact 文件名通过固定正则且必须位于歌曲目录内。

正片复用静态底图，频谱仅在柱形覆盖的局部区域做 alpha 合成。字体、字号适配和文字测量使用渲染器实例内的有界缓存；扫色仍按整句排版、按既有时间裁剪，仅缓存最多四张包含描边的局部高亮图层。缓存不落盘、不跨任务共享，也不保存播放进度，预览可直接跳到任意时间；恢复任务时由新渲染器重建。`tests/test_render_equivalence.py` 对照整帧合成路径验证两种对齐模式、两种分辨率、开头过渡和缓存重建的像素一致性。

## 任务队列与恢复

`VideoJobManager` 使用 `ThreadPoolExecutor(max_workers=1)`，任务和完整选项原子写入 `instance/video_jobs.json`。启动时 queued/running 恢复为 queued 并从头渲染；活跃任务按歌曲和选项指纹去重；最近 10 个完成任务展示，终态历史最多保留 50 条 done 和 50 条 error。模型进度回调只是内存诊断信息，不是 checkpoint。当前没有取消、暂停、任务所有者隔离或多后端协调。

前端有活跃任务时每 1.5 秒轮询，空闲时每 15 秒轮询，页面隐藏时暂停；队列查询使用只读 session，不续期会话。

## Artifact、URL 与前端代理

artifact 接口支持 HEAD、HTTP Range、下载文件名和流式响应；代理必须保留 `Range`、`Content-Range`、`Content-Length`、`Content-Disposition`。普通 URL 需要网站 Cookie，投屏 URL 使用后端 HMAC 密钥和过期时间，设备无需网站会话。签名密钥位于 `instance/media_signing.key` 或环境变量，不能进入前端。

`frontend/static/app.js` 从运行时 `config.js` 读取 API origin/base path，留空时使用同源 `/api`。`frontend_server.py` 仅用于开发，生产使用 Nginx 镜像。

## Docker、CI 与并发约束

前端镜像基于 Nginx Alpine，只包含静态资源；后端镜像基于 Python slim，安装 FFmpeg、Noto CJK 和固定模型运行时，以 UID 10001、Gunicorn 单 worker 运行。模型权重运行时只读挂载到 `/models`。CI 测试后构建 amd64/arm64 镜像并发布版本标签。

JSON 存储适合可信、低流量、单实例部署。修改会话、artifact、任务持久化、视频选项或模型 schema 时，应查阅并更新 `test_sessions.py`、`test_web.py`、`test_video.py`、`test_service.py`、`test_deployment_config.py` 和 `test_frontend.py`。

## 本地调试与验证

同机 Docker 构建适合验证当前工作区：在仓库根目录复制 `.env.example`，使用 `docker compose build && docker compose up -d`，访问 `http://127.0.0.1:8080/`。源码运行需要 Python 3.11、FFmpeg、CJK 字体和 `requirements-dev.txt`；后端运行 `app.py`，前端开发代理运行 `frontend_server.py`。模型模式另需安装 `requirements-model.txt` 并准备只读模型目录。

```bash
cd <local-repo>
CLOUDMUSIC2KTV_HOST=127.0.0.1 CLOUDMUSIC2KTV_PORT=17861 python app.py
CLOUDMUSIC2KTV_BACKEND_ORIGIN=http://127.0.0.1:17861 \
CLOUDMUSIC2KTV_FRONTEND_HOST=127.0.0.1 CLOUDMUSIC2KTV_FRONTEND_PORT=18080 \
python frontend_server.py
```

访问 `http://127.0.0.1:18080/`；健康检查为 `http://127.0.0.1:17861/api/healthz`。常用验证：

```bash
PYTHONPATH=. python -m pytest tests
node --check frontend/static/app.js
python -m py_compile app.py frontend_server.py cloudmusic2ktv/video.py
docker compose config
```

## 已知限制

允许名单成员共享 outputs 和全局队列；后端必须单 worker；视频任务不能取消或暂停；没有多节点协调和按用户媒体隔离。改变这些边界时应先更新本文和 README。
