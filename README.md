# CloudMusic2KTV

CloudMusic2KTV 将选定的网易云音乐歌曲制作成带歌词的 KTV 视频，并提供网页播放、下载和投屏入口。

项目采用单仓库、前后端分离架构：

- 前端负责静态 WebUI，并把 `/api/*` 反向代理到后端；
- 后端负责账号、网易云请求、素材下载、视频生成和全部持久化数据；
- 前后端可以部署在同一台或不同机器上；
- 两者不需要位于同一局域网，只要前端机器能通过 ZeroTier、其他 VPN 或专用网络访问后端即可。

## 文档导航

本文面向使用者，集中说明项目功能、账号使用、部署方式和常见配置。
开发、调试和数据流细节见 [ARCHITECTURE.md](ARCHITECTURE.md)。

使用网易云音乐内容时，请遵守当地法律、平台条款和版权方要求。本项目不提供或分发音乐版权。

## 镜像与端口

推荐直接使用 GitHub Actions 发布到 Docker Hub 的镜像：

```text
docker.io/lamptales/cloudmusic2ktv-frontend:latest
docker.io/lamptales/cloudmusic2ktv-backend:latest
```

同一份构建结果也会发布到 `ghcr.io/lamptales/cloudmusic2ktv-frontend` 和 `ghcr.io/lamptales/cloudmusic2ktv-backend`，可作为备用镜像源。

默认端口：

| 服务 | 容器端口 | 说明 |
| --- | ---: | --- |
| frontend | 80 | 静态页面和 `/api/*` 代理 |
| backend | 7860 | API-only，不提供页面 |

前端是用户唯一需要访问的入口。后端保存 `instance/`、`outputs/`，前端机器默认不保存视频文件。

## 配置参数

参数按运行方式分为 Compose 部署参数、后端运行参数和开发前端参数。同名参数在不同终端中需要分别设置；前端节点不会自动读取后端节点的 `.env`。

设置格式：

```dotenv
# Docker Compose 的 .env
CLOUDMUSIC2KTV_BACKEND_PORT=7860
```

```bash
# Linux / macOS 源码运行
export CLOUDMUSIC2KTV_PORT=7860
```

```powershell
# Windows PowerShell 源码运行
$env:CLOUDMUSIC2KTV_PORT = "7860"
```

布尔开关只有值为 `1` 时才启用；使用 `0` 或不设置表示关闭。

### 正式分机部署：后端 `.env`

这些参数由 `deploy/compose.backend.yml` 读取：

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `CLOUDMUSIC2KTV_BACKEND_IMAGE` | `docker.io/lamptales/cloudmusic2ktv-backend:latest` | 后端镜像及标签，可固定为版本或 `sha-*` 标签 |
| `CLOUDMUSIC2KTV_BACKEND_BIND_ADDRESS` | `127.0.0.1` | 后端发布到宿主机的监听地址；分机部署应填私有/VPN IP，不应填公网 IP |
| `CLOUDMUSIC2KTV_BACKEND_PORT` | `7860` | 后端发布到宿主机的端口，前端节点通过这个端口连接 |
| `CLOUDMUSIC2KTV_BASE_PATH` | 空 | 公网页面的路径前缀；根路径部署留空，部署到 `/ktv/` 时填 `/ktv` |
| `CLOUDMUSIC2KTV_TRUST_PROXY` | `1` | 信任一层受控代理传来的 `X-Forwarded-*`；只有后端仅允许受控前端代理访问时才能启用 |
| `CLOUDMUSIC2KTV_ALLOW_INSECURE_COOKIE_IMPORT` | `0` | 允许通过非 HTTPS 导入网易云 Cookie；仅可信测试网络临时设为 `1` |
| `CLOUDMUSIC2KTV_CORS_ORIGINS` | 空 | 允许浏览器跨域直连后端的 Origin，多个值用逗号分隔；同源前端代理模式应留空 |
| `CLOUDMUSIC2KTV_SESSION_DAYS` | `90` | 网站登录会话有效天数 |
| `CLOUDMUSIC2KTV_MEDIA_URL_TTL_SECONDS` | `3600` | 投屏短期签名 URL 的有效秒数 |
| `CLOUDMUSIC2KTV_PLAYLIST_CACHE_TTL_SECONDS` | `21600` | 歌单及歌曲内存缓存的绝对有效秒数，访问不会续期 |
| `CLOUDMUSIC2KTV_PLAYLIST_CACHE_MAX_ENTRIES` | `32` | 内存中最多保留的歌单歌曲缓存数，超出后按 LRU 淘汰 |
| `CLOUDMUSIC2KTV_MEDIA_SIGNING_KEY` | 自动生成 | 投屏 URL 签名密钥；正式部署应妥善保管 |
| `LYRIC_MODELS_DIR` | 必填 | 宿主机上的模型根目录，以只读方式挂载到容器 `/models` |
| `LYRIC_DEMUCS_MODEL_PATH` | 已验证的 HTDemucs snapshot | Demucs 模型在容器内的本地路径 |
| `LYRIC_CTC_MODEL_PATH` | 已验证的日语 wav2vec2 snapshot | CTC 模型在容器内的本地路径 |
| `LYRIC_DEVICE` | `cpu` | 模型推理设备；使用 GPU 镜像和运行时前不要改为 `cuda` |
| `CLOUDMUSIC2KTV_MODEL_SWEEP_GAP_THRESHOLD_MS` | `100` | Beta 平滑扫色合并相邻短空隙的阈值，不在前端暴露 |

后端容器内部固定监听 `0.0.0.0:7860`，`CLOUDMUSIC2KTV_BACKEND_PORT` 只调整宿主机一侧的发布端口。例如设置为 `17860` 后，端口映射为 `17860:7860`，前端应使用 `http://<BACKEND_PRIVATE_IP>:17860`。同时调整防火墙规则和健康检查地址。

### 正式分机部署：前端 `.env`

这些参数由 `deploy/compose.frontend.yml` 读取：

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `CLOUDMUSIC2KTV_FRONTEND_IMAGE` | `docker.io/lamptales/cloudmusic2ktv-frontend:latest` | 前端镜像及标签 |
| `CLOUDMUSIC2KTV_FRONTEND_BIND_ADDRESS` | `127.0.0.1` | 前端容器发布到宿主机的地址；由同机公网 Nginx 代理时保留 `127.0.0.1` |
| `CLOUDMUSIC2KTV_FRONTEND_PORT` | `8080` | 前端容器发布到宿主机的端口 |
| `CLOUDMUSIC2KTV_BACKEND_UPSTREAM` | 必填 | 前端 Nginx 访问后端的完整源地址，例如 `http://10.0.0.2:17860` |

仓库根目录的 `docker-compose.yml` 用于同机调试。它还使用 `CLOUDMUSIC2KTV_BIND_ADDRESS` 控制前端宿主机监听地址；后端只在 Compose 内部网络监听，不发布后端宿主机端口。

### 后端直接从源码运行

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `CLOUDMUSIC2KTV_HOST` | `0.0.0.0` | 后端进程监听地址；仅本机访问时建议设为 `127.0.0.1` |
| `CLOUDMUSIC2KTV_PORT` | `7860` | 后端进程实际监听端口，例如端口冲突时可改为 `17860` |
| `CLOUDMUSIC2KTV_BASE_PATH` | 空 | 外部反向代理使用的路径前缀，也决定网站会话 Cookie 的 Path |
| `CLOUDMUSIC2KTV_TRUST_PROXY` | 关闭 | 是否信任一层代理提供的客户端 IP、协议、Host 和路径前缀 |
| `CLOUDMUSIC2KTV_ALLOW_INSECURE_COOKIE_IMPORT` | 关闭 | 是否允许非 HTTPS Cookie 导入，仅用于可信调试网络 |
| `CLOUDMUSIC2KTV_CORS_ORIGINS` | 空 | 浏览器可跨域访问后端的 Origin 白名单，使用逗号分隔 |
| `CLOUDMUSIC2KTV_SESSION_DAYS` | `90` | 网站登录会话有效天数 |
| `CLOUDMUSIC2KTV_MEDIA_URL_TTL_SECONDS` | `3600` | 投屏短期签名 URL 的有效秒数 |
| `CLOUDMUSIC2KTV_PLAYLIST_CACHE_TTL_SECONDS` | `21600` | 歌单及歌曲内存缓存的绝对有效秒数，访问不会续期 |
| `CLOUDMUSIC2KTV_PLAYLIST_CACHE_MAX_ENTRIES` | `32` | 内存中最多保留的歌单歌曲缓存数，超出后按 LRU 淘汰 |
| `CLOUDMUSIC2KTV_MEDIA_SIGNING_KEY` | 自动生成 | 投屏 URL 签名密钥；正式部署应妥善保管 |
| `CLOUDMUSIC2KTV_TLS_CERT` | 空 | 后端直接提供 HTTPS 时使用的证书文件路径，必须与私钥同时设置 |
| `CLOUDMUSIC2KTV_TLS_KEY` | 空 | 后端直接提供 HTTPS 时使用的私钥文件路径，必须与证书同时设置 |
| `CLOUDMUSIC2KTV_FFMPEG` | 自动查找 | FFmpeg 可执行文件的明确路径 |
| `CLOUDMUSIC2KTV_FONT_DIR` | 自动查找 | 中日韩字体目录；找不到合适字体时设置 |
| `LYRIC_DEMUCS_MODEL_PATH` | 空 | Demucs 模型的本地目录或 Hugging Face snapshot 路径 |
| `LYRIC_CTC_MODEL_PATH` | 空 | Transformers CTC checkpoint 的本地路径 |
| `LYRIC_DEVICE` | `cpu` | 模型推理设备 |
| `LYRIC_G2P_BACKEND` | `sudachi` | 歌词读音后端；KTV 正式路径只验证 Sudachi |
| `CLOUDMUSIC2KTV_MODEL_SWEEP_GAP_THRESHOLD_MS` | `100` | Beta 平滑扫色的短空隙阈值 |

正式部署通常由公网代理终止 HTTPS，因此无需给后端设置 `CLOUDMUSIC2KTV_TLS_CERT` 和 `CLOUDMUSIC2KTV_TLS_KEY`。

### 开发前端直接从源码运行

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `CLOUDMUSIC2KTV_BACKEND_ORIGIN` | `http://127.0.0.1:7860` | 开发代理访问的后端源地址 |
| `CLOUDMUSIC2KTV_FRONTEND_HOST` | `127.0.0.1` | 开发前端监听地址；局域网测试可设为 `0.0.0.0` |
| `CLOUDMUSIC2KTV_FRONTEND_PORT` | `8080` | 开发前端监听端口 |
| `CLOUDMUSIC2KTV_FRONTEND_BASE_PATH` | 空 | 开发前端的实际挂载路径；设为 `/ktv` 后使用 `/ktv/`、`/ktv/static/*` 和 `/ktv/api/*` |
| `CLOUDMUSIC2KTV_API_ORIGIN` | 空 | 浏览器直连的 API Origin；推荐留空，让 `/api/*` 经过同源开发代理 |

如果系统配置了 HTTP(S) 出站代理，而后端使用局域网或 VPN 地址，还应在操作系统的 `NO_PROXY` 中加入后端 IP；`NO_PROXY` 是通用系统变量，不是项目专用参数。

同一个后端供多个前端访问时，每个前端必须使用与后端 `CLOUDMUSIC2KTV_BASE_PATH` 相同的外部路径。例如后端为 `/ktv`，公网入口和局域网开发前端都应通过各自地址下的 `/ktv/` 访问；域名和端口可以不同。

## 账号与权限

允许名单保存在后端 `instance/allowlist.json`，角色字段有三种值：`root`、`admin`、`user`。

- 允许名单为空时，第一个成功创建网站账号的用户自动获得 `root`；
- `root` 是不可转让的所有者，可添加管理员和普通用户、在两者之间调整权限，并移除非 root 账号；
- `admin` 只能添加和移除普通用户，不能管理管理员或 root；
- `root` 不能通过网页或 API 添加、降级或删除，只能直接编辑后端 JSON；
- 移除用户是可恢复的访问撤销：会阻止后续登录和请求，但保留网站账号及网易云绑定记录。

现有部署从两级权限升级时，应先部署支持 `root` 的后端代码，再备份并编辑两套环境的 `allowlist.json`，将原首位管理员的 `"role": "admin"` 改为 `"role": "root"`。旧版本代码不识别 `root`，回滚时需要同步改回 `admin`。

## 正式部署：前后端分机，直接拉取镜像

这是推荐的正式部署方式。以下将两台机器称为：

- **后端节点**：有足够 CPU、内存和磁盘，负责下载和视频生成；
- **前端节点**：拥有公网 HTTPS 入口，负责页面和反向代理。

开始前确认：

1. 两台机器已通过 ZeroTier 或其他私有网络互通；
2. 前端节点可以访问 `http://<BACKEND_PRIVATE_IP>:<BACKEND_PORT>/api/healthz`；
3. 后端防火墙只允许前端节点的私有 IP 访问所选后端端口；
4. 公网域名和 HTTPS 证书由前端节点管理。

### 1. 部署后端节点

创建部署目录：

```bash
mkdir -p cloudmusic2ktv-backend/docker-data/instance
mkdir -p cloudmusic2ktv-backend/docker-data/outputs
cd cloudmusic2ktv-backend
```

模型权重不在镜像中。后端节点需要准备完整的 Hugging Face 缓存目录，默认放在
`/var/lib/cloudmusic2ktv/models`。必须复制整个 `huggingface/`，包括 `hub` 下的
`blobs`、`refs` 和 `snapshots`，不能只复制 snapshot 中指向 blobs 的符号链接：

```bash
sudo mkdir -p /var/lib/cloudmusic2ktv/models
```

```text
/var/lib/cloudmusic2ktv/models/
└── huggingface/
    └── hub/
        ├── models--adefossez--HTDemucs/
        └── models--jonatasgrosman--wav2vec2-large-xlsr-53-japanese/
```

容器以只读方式挂载该目录；模型由部署者单独准备，并遵守对应模型的许可证。

下载 Compose 和环境模板：

```bash
curl -fsSLo compose.yml https://raw.githubusercontent.com/LampTales/cloudmusic2ktv/main/deploy/compose.backend.yml
curl -fsSLo .env https://raw.githubusercontent.com/LampTales/cloudmusic2ktv/main/deploy/backend.env.example
```

编辑 `.env`：

```dotenv
CLOUDMUSIC2KTV_BACKEND_IMAGE=docker.io/lamptales/cloudmusic2ktv-backend:latest
CLOUDMUSIC2KTV_BACKEND_BIND_ADDRESS=<BACKEND_PRIVATE_IP>
CLOUDMUSIC2KTV_BACKEND_PORT=7860
CLOUDMUSIC2KTV_BASE_PATH=/ktv
LYRIC_MODELS_DIR=/var/lib/cloudmusic2ktv/models
LYRIC_DEVICE=cpu
```

如果最终地址位于域名根路径，例如 `https://ktv.example.com/`，将 `CLOUDMUSIC2KTV_BASE_PATH` 留空。如果最终地址是 `https://example.com/ktv/`，则设为 `/ktv`。

Linux 宿主机需要确保容器用户 UID 10001 可以写入数据目录：

```bash
sudo chown -R 10001:10001 docker-data
sudo chmod -R a+rX /var/lib/cloudmusic2ktv/models
```

启动并检查：

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs -f backend
```

在后端节点上验证：

```bash
curl http://<BACKEND_PRIVATE_IP>:<BACKEND_PORT>/api/healthz
```

### 2. 部署前端节点

创建目录并下载配置：

```bash
mkdir -p cloudmusic2ktv-frontend
cd cloudmusic2ktv-frontend
curl -fsSLo compose.yml https://raw.githubusercontent.com/LampTales/cloudmusic2ktv/main/deploy/compose.frontend.yml
curl -fsSLo .env https://raw.githubusercontent.com/LampTales/cloudmusic2ktv/main/deploy/frontend.env.example
```

编辑 `.env`：

```dotenv
CLOUDMUSIC2KTV_FRONTEND_IMAGE=docker.io/lamptales/cloudmusic2ktv-frontend:latest
CLOUDMUSIC2KTV_FRONTEND_BIND_ADDRESS=127.0.0.1
CLOUDMUSIC2KTV_FRONTEND_PORT=8080
CLOUDMUSIC2KTV_BACKEND_UPSTREAM=http://<BACKEND_PRIVATE_IP>:<BACKEND_PORT>
```

启动并验证前端到后端的代理：

```bash
docker compose pull
docker compose up -d
docker compose ps
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/api/healthz
```

### 3. 配置公网 HTTPS

如果公开地址是 `https://example.com/ktv/`，外层 Nginx 可以使用：

```nginx
location /ktv/ {
    # 结尾 / 会在转发前剥离 /ktv/ 前缀。
    proxy_pass http://127.0.0.1:8080/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /ktv;
    proxy_read_timeout 3600;
    proxy_send_timeout 3600;
}
```

如果使用独立子域名并部署在根路径，将 `location` 改为 `/`，去掉 `X-Forwarded-Prefix`，并确保后端的 `CLOUDMUSIC2KTV_BASE_PATH` 为空。

最终检查：

```text
https://example.com/ktv/              能打开前端
https://example.com/ktv/api/healthz  返回 healthy
```

客户端看到的播放和下载 URL 始终是公网前端地址。视频实际从后端读取，经前端节点流式转发；除非主动启用 Nginx 缓存，前端节点不会保存视频副本。

### 更新与回滚

在对应节点执行：

```bash
docker compose pull
docker compose up -d
```

需要回滚时，把 `.env` 中的 `latest` 改成同一版本或提交 SHA 标签，例如：

```dotenv
CLOUDMUSIC2KTV_BACKEND_IMAGE=docker.io/lamptales/cloudmusic2ktv-backend:sha-0123456
CLOUDMUSIC2KTV_FRONTEND_IMAGE=docker.io/lamptales/cloudmusic2ktv-frontend:sha-0123456
```

前端和后端可以独立更新，但建议使用同一提交或版本标签，避免 API 契约不一致。

## 调试部署

### 方式 A：同机 Docker，拉取远端镜像

适合在一台机器上快速验证完整前后端链路。

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
CLOUDMUSIC2KTV_BACKEND_IMAGE=docker.io/lamptales/cloudmusic2ktv-backend:latest
CLOUDMUSIC2KTV_FRONTEND_IMAGE=docker.io/lamptales/cloudmusic2ktv-frontend:latest
CLOUDMUSIC2KTV_BIND_ADDRESS=127.0.0.1
CLOUDMUSIC2KTV_FRONTEND_PORT=8080
LYRIC_MODELS_DIR=./docker-data/models
```

启动时禁止本地构建：

```powershell
docker compose pull
docker compose up -d --no-build
```

访问 `http://127.0.0.1:8080/`。后端只存在于 Compose 内部网络，不发布到宿主机。

### 方式 B：同机 Docker，从源码构建

适合验证当前工作区的 Dockerfile：

```powershell
Copy-Item .env.example .env
docker compose build
docker compose up -d
```

访问 `http://127.0.0.1:8080/`。停止服务：

```powershell
docker compose down
```

### 方式 C：同机直接从源码运行

需要 Python 3.11、FFmpeg 和可用的中日韩字体。

安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

Beta 模式还需要安装固定版本的模型运行依赖：

```powershell
python -m pip install -r requirements-model.txt
```

分别启动：

```powershell
# 终端 1：后端
$env:CLOUDMUSIC2KTV_HOST = "127.0.0.1"
$env:CLOUDMUSIC2KTV_PORT = "17860"
python app.py

# 终端 2：前端和开发代理
$env:CLOUDMUSIC2KTV_BACKEND_ORIGIN = "http://127.0.0.1:17860"
$env:CLOUDMUSIC2KTV_FRONTEND_HOST = "127.0.0.1"
$env:CLOUDMUSIC2KTV_FRONTEND_PORT = "18080"
python frontend_server.py
```

访问 `http://127.0.0.1:18080/`。`http://127.0.0.1:17860/` 不提供页面，只有 `/api/*` 有效。

### 方式 D：异机调试

异机调试与正式部署的网络路径一致，但可以使用 HTTP 和开发进程。

后端节点直接从源码运行：

```bash
export CLOUDMUSIC2KTV_HOST=0.0.0.0
export CLOUDMUSIC2KTV_PORT=7860
export CLOUDMUSIC2KTV_BASE_PATH=/ktv
# 仅在可信测试网络通过 HTTP 测试 Cookie 导入时使用：
export CLOUDMUSIC2KTV_ALLOW_INSECURE_COOKIE_IMPORT=1
python app.py
```

前端节点直接从源码运行：

```bash
export CLOUDMUSIC2KTV_BACKEND_ORIGIN=http://<BACKEND_PRIVATE_IP>:7860
export CLOUDMUSIC2KTV_FRONTEND_HOST=0.0.0.0
export CLOUDMUSIC2KTV_FRONTEND_PORT=18080
export CLOUDMUSIC2KTV_FRONTEND_BASE_PATH=/ktv
python frontend_server.py
```

访问 `http://<FRONTEND_PRIVATE_IP>:18080/ktv/`。开发前端会将根路径重定向到 `/ktv/`，并把 `/ktv/api/*` 剥离前缀后代理到后端 `/api/*`。如果后端 `CLOUDMUSIC2KTV_BASE_PATH` 留空，则开发前端也应留空 `CLOUDMUSIC2KTV_FRONTEND_BASE_PATH`，并从 `/` 访问。

只在受信任网络中监听 `0.0.0.0`，并用防火墙限制测试来源；仅在前端节点本机访问时可改为 `127.0.0.1`。

也可以任意组合：

- 后端使用远端镜像，前端从源码运行；
- 前端使用远端镜像，后端从源码运行；
- 两端分别从源码构建自己的镜像；
- 两端均直接拉取远端镜像。

判断是否配置正确只需要遵循一个原则：前端节点必须能访问后端的 `/api/healthz`，客户端只访问前端节点。

## 开发附录

以下内容主要供维护者和自动化开发代理参考。面向用户的部署和使用说明见上文。

### 仓库结构

```text
frontend/                    独立静态前端和 Nginx 配置
app.py                       API-only Flask 入口
cloudmusic2ktv/              后端业务模块
frontend_server.py           本地前端服务器和开发代理
Dockerfile.frontend          前端镜像
Dockerfile.backend           后端镜像
docker-compose.yml           同机调试配置
deploy/                      正式分机部署配置
tests/                       自动化测试
instance/                    源码运行时的后端状态
outputs/                     源码运行时的素材和视频
```

`instance/`、`outputs/`、`docker-data/` 不应提交或复制进镜像。

### 任务恢复与数据

#### 实验性模型歌词对齐

视频制作页的“实验性模型预处理”会在同一个视频队列任务中依次执行 reading、Demucs 和 CTC，然后继续编码视频；用户无需先单独提交预处理任务。传统模式完全不依赖模型库。

后端 Docker 镜像通过 `requirements-model.txt` 从公开 GitHub 仓库安装固定
commit 的 `lyric-align[models]`。源码开发也可安装该文件，或使用相邻仓库的
editable package。正式 KTV 路径固定使用经过完整测试的 Sudachi；pykakasi 和
OpenJTalk 只作为 `lyric-align` 的实验性可选后端，不包含在 KTV 镜像中。

模型权重不进入镜像，运行时必须配置本地模型路径：

```text
LYRIC_DEMUCS_MODEL_PATH=/models/huggingface/hub/models--adefossez--HTDemucs/snapshots/<revision>
LYRIC_CTC_MODEL_PATH=/models/huggingface/hub/models--jonatasgrosman--wav2vec2-large-xlsr-53-japanese/snapshots/<revision>
LYRIC_DEVICE=cpu                 # 或 cuda
LYRIC_G2P_BACKEND=sudachi
# Model sweep rendering bridges short gaps between adjacent visible characters.
# This backend-only tuning value is not exposed in the web UI (default: 100 ms).
CLOUDMUSIC2KTV_MODEL_SWEEP_GAP_THRESHOLD_MS=100
# 仅相邻仓库源码开发时需要；Docker 镜像不设置
LYRIC_ALIGN_PATH=/opt/lyric_align/src
```

模型对象会在单 worker 进程内缓存，后续歌曲复用已加载模型；`alignment.json`、伴奏和歌词时间结果按歌曲目录缓存。纯伴奏选项仅在模型模式下可用。

模型预处理阶段保持队列进度为 `0%`，预处理完成后视频编码独占
`0–99%` 的进度范围；因此进度条只表示实际视频渲染时间。预处理消息中
显示的 `aligned line N/M` 只是诊断状态，并不是可恢复的句子级 checkpoint：
任务中断后会从头重新执行。为避免每句回调造成频繁状态文件写入，后端对这类
消息只更新运行时内存，不写入持久化队列日志；歌词和模型中间结果仍由
`lyric_align` 在阶段结束时按歌曲目录缓存。

视频任务状态保存在后端 `instance/video_jobs.json`：

- 队列保持单 worker；
- 后端重启后，`queued/running` 任务会重新排队并从头渲染；
- 已完成视频和素材不受重启影响；
- 队列面板会保留并展示最近 10 个已完成的视频任务；
- `video_jobs.json` 最多保留 50 条完成记录和 50 条失败记录，清理只删除任务元数据，不删除已生成视频；
- 任务记录新增展示字段时兼容旧 JSON，不需要删除 `instance/video_jobs.json`；
- 队列有活跃任务时每 1.5 秒刷新，空闲时降为 15 秒，页面隐藏时暂停；队列查询不会续期或重写登录会话；
- 当前没有取消和暂停功能。

所有需要备份的数据都在后端节点：

```text
docker-data/instance/
docker-data/outputs/
```

### CI 与镜像发布

同一仓库的 GitHub Actions 会运行测试，然后分别构建：

```text
Dockerfile.frontend → cloudmusic2ktv-frontend
Dockerfile.backend  → cloudmusic2ktv-backend
```

两个镜像均以同一组 `latest`、`sha-*` 和 `v*.*.*` 标签发布到 Docker Hub 和 GHCR，并支持 `linux/amd64`、`linux/arm64`。Pull Request 只测试和构建，不登录或推送镜像仓库。

后端镜像固定使用 `requirements-model.txt` 中的 `lyric-align` commit 和
`constraints-model.txt` 中经过验证的模型运行时版本。构建过程只下载 Python
包和公开源码，不读取模型权重；Torch/TorchAudio 明确使用官方 CPU wheel，
不会把 CUDA 运行时装入当前 CPU 镜像。权重在容器运行时通过 `/models:ro`
挂载。

Docker Hub 发布需要在 GitHub Actions 中配置仓库变量 `DOCKERHUB_USERNAME` 和仓库 Secret `DOCKERHUB_TOKEN`。前者填写 Docker Hub 用户名，后者使用具有 Read & Write 权限的 Docker Hub Access Token。也可以从 Actions 页面手动运行此工作流。

### 验证

```powershell
python -m pytest -q
node --check frontend/static/app.js
docker compose config
```

涉及视频时还应验证 HEAD、HTTP Range、拖动播放和下载文件名。

浏览器播放和下载需要网站会话。点击“投屏链接”时，后端会为已登录用户签发短期签名媒体 URL；投屏设备访问该 URL 不需要网站 Cookie，过期后自动失效。签名密钥保存在后端 `instance/media_signing.key`（或由 `CLOUDMUSIC2KTV_MEDIA_SIGNING_KEY` 提供），不得暴露给前端。

### 安全边界

- 正式部署只通过 HTTPS 公开前端；
- 后端端口仅绑定私有/VPN 地址，并限制为前端节点可访问；
- `instance/` 包含密码哈希、网站会话和网易云绑定 Cookie，必须限制权限并备份；
- `outputs/` 包含音频、歌词、封面和视频；
- 后端必须保持一个 Gunicorn worker；
- 只在受控代理后启用 `CLOUDMUSIC2KTV_TRUST_PROXY=1`；
- `CLOUDMUSIC2KTV_ALLOW_INSECURE_COOKIE_IMPORT=1` 只用于可信测试网络；
- 不使用未登录方案绕过付费内容权限。
