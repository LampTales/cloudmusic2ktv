# CloudMusic2KTV

CloudMusic2KTV 将选定的网易云音乐歌曲制作成带歌词的 KTV 视频，并提供网页播放、下载和投屏入口。

项目采用单仓库、前后端分离架构：

- 前端负责静态 WebUI，并把 `/api/*` 反向代理到后端；
- 后端负责账号、网易云请求、素材下载、视频生成和全部持久化数据；
- 前后端可以部署在同一台或不同机器上；
- 两者不需要位于同一局域网，只要前端机器能通过 ZeroTier、其他 VPN 或专用网络访问后端即可。

使用网易云音乐内容时，请遵守当地法律、平台条款和版权方要求。本项目不提供或分发音乐版权。

## 镜像与端口

推荐直接使用 GitHub Actions 发布的镜像：

```text
ghcr.io/lamptales/cloudmusic2ktv-frontend:latest
ghcr.io/lamptales/cloudmusic2ktv-backend:latest
```

默认端口：

| 服务 | 容器端口 | 说明 |
| --- | ---: | --- |
| frontend | 80 | 静态页面和 `/api/*` 代理 |
| backend | 7860 | API-only，不提供页面 |

前端是用户唯一需要访问的入口。后端保存 `instance/`、`outputs/`，前端机器默认不保存视频文件。

## 正式部署：前后端分机，直接拉取镜像

这是推荐的正式部署方式。以下将两台机器称为：

- **后端节点**：有足够 CPU、内存和磁盘，负责下载和视频生成；
- **前端节点**：拥有公网 HTTPS 入口，负责页面和反向代理。

开始前确认：

1. 两台机器已通过 ZeroTier 或其他私有网络互通；
2. 前端节点可以访问 `http://<BACKEND_PRIVATE_IP>:7860/api/healthz`；
3. 后端防火墙只允许前端节点的私有 IP 访问 7860；
4. 公网域名和 HTTPS 证书由前端节点管理。

### 1. 部署后端节点

创建部署目录：

```bash
mkdir -p cloudmusic2ktv-backend/docker-data/instance
mkdir -p cloudmusic2ktv-backend/docker-data/outputs
cd cloudmusic2ktv-backend
```

下载 Compose 和环境模板：

```bash
curl -fsSLo compose.yml https://raw.githubusercontent.com/LampTales/cloudmusic2ktv/main/deploy/compose.backend.yml
curl -fsSLo .env https://raw.githubusercontent.com/LampTales/cloudmusic2ktv/main/deploy/backend.env.example
```

编辑 `.env`：

```dotenv
CLOUDMUSIC2KTV_BACKEND_IMAGE=ghcr.io/lamptales/cloudmusic2ktv-backend:latest
CLOUDMUSIC2KTV_BACKEND_BIND_ADDRESS=<BACKEND_PRIVATE_IP>
CLOUDMUSIC2KTV_BASE_PATH=/ktv
```

如果最终地址位于域名根路径，例如 `https://ktv.example.com/`，将 `CLOUDMUSIC2KTV_BASE_PATH` 留空。如果最终地址是 `https://example.com/ktv/`，则设为 `/ktv`。

Linux 宿主机需要确保容器用户 UID 10001 可以写入数据目录：

```bash
sudo chown -R 10001:10001 docker-data
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
curl http://<BACKEND_PRIVATE_IP>:7860/api/healthz
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
CLOUDMUSIC2KTV_FRONTEND_IMAGE=ghcr.io/lamptales/cloudmusic2ktv-frontend:latest
CLOUDMUSIC2KTV_FRONTEND_BIND_ADDRESS=127.0.0.1
CLOUDMUSIC2KTV_FRONTEND_PORT=8080
CLOUDMUSIC2KTV_BACKEND_UPSTREAM=http://<BACKEND_PRIVATE_IP>:7860
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
CLOUDMUSIC2KTV_BACKEND_IMAGE=ghcr.io/lamptales/cloudmusic2ktv-backend:sha-0123456
CLOUDMUSIC2KTV_FRONTEND_IMAGE=ghcr.io/lamptales/cloudmusic2ktv-frontend:sha-0123456
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
CLOUDMUSIC2KTV_BACKEND_IMAGE=ghcr.io/lamptales/cloudmusic2ktv-backend:latest
CLOUDMUSIC2KTV_FRONTEND_IMAGE=ghcr.io/lamptales/cloudmusic2ktv-frontend:latest
CLOUDMUSIC2KTV_BIND_ADDRESS=127.0.0.1
CLOUDMUSIC2KTV_FRONTEND_PORT=8080
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
# 仅在可信测试网络通过 HTTP 测试 Cookie 导入时使用：
export CLOUDMUSIC2KTV_ALLOW_INSECURE_COOKIE_IMPORT=1
python app.py
```

前端节点直接从源码运行：

```bash
export CLOUDMUSIC2KTV_BACKEND_ORIGIN=http://<BACKEND_PRIVATE_IP>:7860
export CLOUDMUSIC2KTV_FRONTEND_HOST=0.0.0.0
export CLOUDMUSIC2KTV_FRONTEND_PORT=18080
python frontend_server.py
```

只在受信任网络中监听 `0.0.0.0`，并用防火墙限制测试来源；仅在前端节点本机访问时可改为 `127.0.0.1`。

也可以任意组合：

- 后端使用远端镜像，前端从源码运行；
- 前端使用远端镜像，后端从源码运行；
- 两端分别从源码构建自己的镜像；
- 两端均直接拉取远端镜像。

判断是否配置正确只需要遵循一个原则：前端节点必须能访问后端的 `/api/healthz`，客户端只访问前端节点。

## 仓库结构

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

## 任务恢复与数据

视频任务状态保存在后端 `instance/video_jobs.json`：

- 队列保持单 worker；
- 后端重启后，`queued/running` 任务会重新排队并从头渲染；
- 已完成视频和素材不受重启影响；
- 当前没有取消和暂停功能。

所有需要备份的数据都在后端节点：

```text
docker-data/instance/
docker-data/outputs/
```

## CI 与镜像发布

同一仓库的 GitHub Actions 会运行测试，然后分别构建：

```text
Dockerfile.frontend → cloudmusic2ktv-frontend
Dockerfile.backend  → cloudmusic2ktv-backend
```

两个镜像均发布 `latest`、`sha-*` 和 `v*.*.*` 标签，并支持 `linux/amd64`、`linux/arm64`。Pull Request 只测试和构建，不推送。

## 验证

```powershell
python -m pytest -q
node --check frontend/static/app.js
docker compose config
```

涉及视频时还应验证 HEAD、HTTP Range、拖动播放和下载文件名。

当前 artifact 路由要求网站会话。浏览器播放正常，但不携带浏览器 Cookie 的独立投屏应用可能收到 401；正式使用此类播放器前，需要实现短期签名媒体 URL。

## 安全边界

- 正式部署只通过 HTTPS 公开前端；
- 后端端口仅绑定私有/VPN 地址，并限制为前端节点可访问；
- `instance/` 包含密码哈希、网站会话和网易云绑定 Cookie，必须限制权限并备份；
- `outputs/` 包含音频、歌词、封面和视频；
- 后端必须保持一个 Gunicorn worker；
- 只在受控代理后启用 `CLOUDMUSIC2KTV_TRUST_PROXY=1`；
- `CLOUDMUSIC2KTV_ALLOW_INSECURE_COOKIE_IMPORT=1` 只用于可信测试网络；
- 不使用未登录方案绕过付费内容权限。
