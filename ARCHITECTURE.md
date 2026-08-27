# CloudMusic2KTV 架构说明

本文记录当前 `divide` 分支的真实结构、进程边界、数据流和修改约束。用户和部署说明见 [README.md](README.md)。

## 1. 架构决策

项目采用单仓库、双运行时、双镜像结构，不拆分 Git 仓库。

```text
frontend runtime                     backend runtime
────────────────                     ───────────────
frontend/index.html                  app.py
frontend/config.js                   cloudmusic2ktv/*.py
frontend/static/*                    instance/
Nginx / frontend_server.py           outputs/
        │
        └──────── /api/* ────────────► Flask/Gunicorn
```

边界约定：

- 后端只注册 `/api/*`，不提供页面、静态文件和前端运行配置；
- 前端只提供静态资源，并将 `/api/*` 流式代理到后端；
- 后端返回 artifact 的相对 URL，不生成内部主机名；
- 浏览器永远以当前前端 origin 解析 API、预览和视频 URL；
- 所有持久化数据只属于后端。

## 2. 部署数据流

```text
客户端
  │ GET https://public.example/ktv/
  │ GET https://public.example/ktv/api/...
  ▼
公网前端服务器
  │ 静态文件本地返回
  │ /api/* 经私有网络/VPN 转发
  ▼
后端节点
  │ 读取/写入 instance、outputs
  │ 调用网易云、Pillow、NumPy、FFmpeg
  ▼
响应沿原连接返回客户端
```

前端节点默认不缓存或持久化 MP4。视频字节经过前端节点转发，因此前端节点仍承担出口带宽，但不承担视频生成和主存储。

## 3. 目录与运行时

### 前端

`frontend/` 是生产静态资源的唯一来源：

- `index.html`：页面结构；
- `config.js`：生产默认同源 API；
- `static/app.js`：状态机、API 调用、预览、队列轮询和投屏入口；
- `static/app.css`：页面样式；
- `nginx.conf.template`：静态服务和 `/api/` 流式代理。

`frontend_server.py` 只用于本地开发。它提供同一批静态资源，并通过
`CLOUDMUSIC2KTV_BACKEND_ORIGIN` 转发 `/api/*`。代理保留 Cookie、Range、
Content-Range、Content-Length 和 Content-Disposition。

前端 URL 配置：

- `CLOUDMUSIC2KTV_API_ORIGIN` 为空时使用同源 `/api`；
- `CLOUDMUSIC2KTV_BASE_PATH` 为空时，从页面路径推断反向代理前缀；
- 生产部署使用同源代理，不启用浏览器跨域；
- 显式 API origin 只保留给跨端口诊断。

### 后端

`app.py` 是 API-only Flask 入口。模块启动时创建：

- `FileSessionStore(instance/sessions/)`；
- `AllowlistStore(instance/allowlist.json)`；
- `WebsiteAccountStore(instance/accounts.json)`；
- `NeteaseBindingStore(instance/netease_bindings.json)`；
- `VideoJobManager(outputs/, instance/video_jobs.json)`。

后端只允许单进程部署。Gunicorn 可以使用多线程处理轮询、下载和 Range 请求，但 worker 数必须保持为 1，否则每个 worker 会拥有独立任务队列和内存锁。

## 4. HTTP API

公共接口：

- `GET /api/healthz`；
- `GET /api/status`；
- 注册、登录前所需的 `/api/auth/*` 接口。

名单成员接口：

- 搜索和读取歌曲；
- 素材状态和下载；
- 视频预览、自定义背景、生成、队列和任务状态；
- artifact HEAD/Range/下载。

管理员接口：

- 读取、搜索、添加和删除允许名单成员。

所有错误返回 JSON。Flask 的 HTTPException 保持原状态；未知根路径和静态路径返回 404。

## 5. 会话、代理和 URL

网站会话使用随机 HttpOnly Cookie，后端状态保存在文件中。正式部署由公网 HTTPS 代理终止 TLS，后端使用受信任的 `X-Forwarded-*` 信息确定安全 Cookie 和外部路径。

`CLOUDMUSIC2KTV_BASE_PATH` 的作用：

- 设置会话 Cookie Path；
- 为后端返回的相对 artifact URL 添加公网前缀。

外层代理必须剥离公网前缀后再转发。前端 Nginx 继续保留 `X-Forwarded-Proto`、`X-Forwarded-Host` 和 `X-Forwarded-Prefix` 给后端。

artifact URL 形如：

```text
/ktv/api/video/artifact/642723/ktv_720p_ca7862f7bcd0.mp4?version=...
```

它描述的是对外代理路径，不代表文件保存在前端节点。后端根据歌曲 ID 和白名单文件名从 `outputs/` 定位文件。

## 6. 网易云和素材流水线

```text
NeteaseClient
  ├─ 登录/账号状态
  ├─ 搜索/歌曲详情/歌词
  └─ 播放 URL 与流式下载
        ▼
SongDownloadService
  ├─ 封面和音频 .part 原子落盘
  ├─ 大小/MD5 校验
  ├─ metadata 和原始歌词
  └─ 统一逐行时间轴
        ▼
outputs/<song_id>_<artist>_<name>/
```

网易云 Cookie 按绑定的 userId 存在后端 `instance/netease_bindings.json`，不会发送给前端或保存到前端节点磁盘。

## 7. 视频流水线

`VideoProject` 从歌曲目录读取 metadata、时间轴、音频和封面；`FrameRenderer` 同时用于预览和逐帧视频；`SpectrumData` 使用 FFmpeg 解码和 NumPy FFT；`render_video()` 将 Pillow RGB24 帧写入 FFmpeg stdin，输出 H.264/AAC MP4。

保持以下约束：

- 完整视频先写 `.part.mp4`，成功后原子替换；
- artifact 文件名必须通过固定正则白名单；
- artifact 解析路径必须仍在歌曲目录内；
- 视频支持 HEAD 和 HTTP Range；
- 前端和所有代理必须保留 Range 相关头。

同分辨率多视频当前通过选项指纹区分。用户可选择文件，但指纹不适合作为人类可读说明；更清晰的展示元数据和文件管理留作后续独立设计。

## 8. 任务持久化与恢复

`VideoJobManager` 使用 `ThreadPoolExecutor(max_workers=1)`。任务包含完整 `VideoOptions`，并原子写入 `instance/video_jobs.json`。

启动恢复规则：

- `queued`、`running` 统一恢复为 `queued`；
- 按持久化记录顺序重新提交；
- 被中断的渲染从头执行；
- 无效选项的恢复任务标记为 `error`；
- 完成和失败记录保留用于最近任务展示。

当前没有取消、暂停、多后端实例协调和任务所有者隔离。

## 9. Docker 与 CI

`Dockerfile.frontend`：

- 基于 Nginx Alpine；
- 只复制 `frontend/`；
- 使用 `BACKEND_UPSTREAM` 配置后端；
- 不包含 Python、FFmpeg、账号或媒体文件。

`Dockerfile.backend`：

- 基于 Python slim；
- 安装 FFmpeg 和 Noto CJK；
- 只复制 `app.py`、依赖和 `cloudmusic2ktv/`；
- 以 UID 10001 运行；
- 挂载 `instance/`、`outputs/`；
- Gunicorn 单 worker、四线程。

GitHub Actions 在同一提交上测试代码，然后通过矩阵构建两个 amd64/arm64 镜像。两个镜像共享版本标签但可以独立部署和回滚，因此无需拆仓库。

## 10. 测试契约

最低验证：

```powershell
python -m pytest -q
node --check frontend/static/app.js
python -m py_compile app.py frontend_server.py cloudmusic2ktv/video.py
docker compose config
```

测试覆盖：

- weapi 加密结构；
- LRC 和多语言时间轴；
- 文件会话、过期和账号权限；
- 素材状态和路径安全；
- 视频选项、预览、间奏、频谱和任务恢复；
- API 鉴权、artifact Range/下载；
- 后端 API-only 边界；
- 前端资源、运行配置和开发代理。

## 11. 安全与已知限制

- 不记录或提交 `instance/`、`outputs/`、Cookie 和用户媒体；
- 后端端口只绑定私有/VPN 地址，并限制为前端节点可访问；
- 只有受控代理才能设置 `CLOUDMUSIC2KTV_TRUST_PROXY=1`；
- Cookie 导入只允许 HTTPS 或显式本地调试例外；
- 当前允许名单成员共享 outputs 和全局队列，没有按用户隔离；
- 当前 artifact 路由要求网站会话。浏览器播放正常，但不携带浏览器 Cookie 的独立投屏应用可能收到 401；正式公网投屏前应实现短期签名媒体 URL；
- 前端开发代理使用 Flask，仅用于开发；生产使用 Nginx 镜像；
- 后端 JSON 存储适合可信、低流量、单实例部署，不是多节点数据库。
