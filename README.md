# CloudMusic2KTV

CloudMusic2KTV 将选定的网易云音乐歌曲制作成带歌词的 KTV 视频，并提供网页播放和投屏所需的 HTTP 接口。项目适合在局域网或带 HTTPS 反向代理的服务器上运行。

使用网易云音乐内容时，请遵守当地法律、平台条款和版权方要求。本项目不提供或分发音乐版权；部署者需要自行确认账号、下载和公开访问的合规性。

## 仓库结构

```text
cloudmusic2ktv/
├─ Dockerfile                 Docker 镜像构建文件
├─ docker-compose.yml         Docker Compose 服务定义（仓库根目录）
├─ .env.example               Compose 配置模板（仓库根目录）
├─ .dockerignore              Docker 构建上下文排除规则
├─ .github/workflows/docker.yml  CI、测试和镜像构建
├─ app.py                     Flask 应用入口
├─ cloudmusic2ktv/             业务代码
├─ static/                     前端静态资源
├─ templates/                  HTML 模板
├─ tests/                      自动化测试
├─ instance/                   Python 直接运行时数据（本地生成，不提交）
├─ outputs/                    Python 直接运行时媒体（本地生成，不提交）
└─ docker-data/                Docker 挂载的数据目录（本地生成，不提交）
   ├─ instance/
   └─ outputs/
```

`docker-compose.yml` 和 `.env.example` 位于仓库根目录。`instance/`、`outputs/` 和 `docker-data/` 是运行数据，不需要放进镜像，也不应提交到 Git。

## 正式部署：直接拉取 GHCR 镜像

这是服务器部署的推荐方式。

在服务器上准备目录：

```bash
mkdir -p cloudmusic2ktv-deploy/docker-data/instance cloudmusic2ktv-deploy/docker-data/outputs
cd cloudmusic2ktv-deploy
# Linux 主机上容器以 UID 10001 运行；确保挂载目录可写
sudo chown -R 10001:10001 docker-data
```

从本仓库根目录下载 `docker-compose.yml`，并创建 `.env` （可以`cp .env.example .env`），最终目录结构如下：

```text
cloudmusic2ktv-deploy/
├─ docker-compose.yml
├─ .env
└─ docker-data/
   ├─ instance/
   └─ outputs/
```

编辑 `.env`。本项目的 GHCR 镜像地址为 `ghcr.io/lamptales/cloudmusic2ktv`：

```dotenv
CLOUDMUSIC2KTV_IMAGE=ghcr.io/lamptales/cloudmusic2ktv:latest
CLOUDMUSIC2KTV_BASE_PATH=/ktv
CLOUDMUSIC2KTV_TRUST_PROXY=1
CLOUDMUSIC2KTV_BIND_ADDRESS=127.0.0.1
```

如果需要固定到某一次构建，可以使用提交 SHA 标签，例如：

```dotenv
CLOUDMUSIC2KTV_IMAGE=ghcr.io/lamptales/cloudmusic2ktv:sha-04a1235
```

如果服务直接通过服务器端口访问而不是挂在子路径下，将 `CLOUDMUSIC2KTV_BASE_PATH` 设为空，将 `CLOUDMUSIC2KTV_TRUST_PROXY` 设为 `0`，并按需要调整绑定地址。

公开 GHCR 镜像可直接拉取。私有镜像先登录：

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <GITHUB_USER> --password-stdin
```

启动服务：

```bash
docker compose pull
docker compose up -d --no-build
docker compose ps
docker compose logs -f cloudmusic2ktv
```

### 反向代理到域名子路径

例如服务公开地址为 `https://example.com/ktv/`，容器只监听服务器本机的 `127.0.0.1:7860`。Nginx 可使用类似配置（按实际证书和域名调整）：

```nginx
location /ktv/ {
    # The trailing slash removes /ktv/ before forwarding to Flask.
    proxy_pass http://127.0.0.1:7860/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Prefix /ktv;
    proxy_read_timeout 3600;
    proxy_send_timeout 3600;
}
```

`CLOUDMUSIC2KTV_BASE_PATH=/ktv` 必须与代理的 `location` 前缀一致，并保留结尾 `/` 的访问形式。只有在反向代理可信且确实覆盖转发请求头时才设置 `CLOUDMUSIC2KTV_TRUST_PROXY=1`。

### 公网部署前检查

- 只通过 HTTPS 域名提供访问，不要把容器端口直接暴露到公网；
- `CLOUDMUSIC2KTV_BIND_ADDRESS=127.0.0.1`，并在防火墙中限制 7860 端口；
- `docker-data/instance` 保存网站密码哈希、会话和网易云绑定 Cookie，必须限制文件权限并纳入安全备份；
- 第一个成功注册的网易云账号会成为管理员。完成初始化后，只把朋友的网易云账号加入允许名单，不要把管理入口和 Cookie 分享给他人；
- 应用本身不是完整的公网 SaaS 防护层，建议在反向代理或防火墙增加访问控制、日志和必要的限流。

## 备用方式：服务器从源码构建

需要安装 Git、Docker 和 Docker Compose 插件：

```bash
git clone https://github.com/lamptales/cloudmusic2ktv.git cloudmusic2ktv
cd cloudmusic2ktv
cp .env.example .env
```

编辑 `.env`（公网子路径部署通常使用上一节的值），然后构建并启动：

```bash
docker compose build
docker compose up -d
```

如果要明确使用本地镜像，可在 `.env` 中设置：

```dotenv
CLOUDMUSIC2KTV_IMAGE=cloudmusic2ktv:local
```

## 更新、回滚和数据

GHCR 镜像部署更新：

```bash
docker compose pull
docker compose up -d --no-build
```

源码部署更新：

```bash
git pull
docker compose build
docker compose up -d
```

`docker-data/instance` 和 `docker-data/outputs` 是宿主机挂载目录，更新镜像不会删除其中的数据。重建或重启会中断正在执行的视频任务；需要回滚时，将 `.env` 中的镜像标签改为历史 SHA 或版本标签，再执行 `docker compose pull && docker compose up -d --no-build`。

## Docker 局域网调试

局域网测试时，在仓库根目录创建 `.env`，使用：

```dotenv
CLOUDMUSIC2KTV_IMAGE=cloudmusic2ktv:local
CLOUDMUSIC2KTV_BASE_PATH=
CLOUDMUSIC2KTV_TRUST_PROXY=0
CLOUDMUSIC2KTV_BIND_ADDRESS=0.0.0.0
```

然后执行：

```bash
docker compose build
docker compose up -d
docker compose logs -f cloudmusic2ktv
```

本机访问 `http://127.0.0.1:7860/`；同一局域网的其他设备访问运行 Docker 主机的局域网 IP。停止服务：

```bash
docker compose down
```

## 基本使用

打开网页后登录网易云音乐，选择歌曲并下载所需素材，等待视频生成完成后播放。投屏时让播放设备和运行服务的主机处于可互相访问的网络中；公网部署则通过 HTTPS 域名访问。

## Python 直接运行（开发调试）

Docker 不是开发调试的唯一方式。需要 Python 3.11、FFmpeg 和可用字体；依赖安装：

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python app.py
```

macOS/Linux：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python app.py
```

默认监听 `http://127.0.0.1:7860/`。本地运行产生的 `instance/` 和 `outputs/` 不要提交到 Git。

## 测试和 GitHub Actions CI

本地运行测试：

```bash
python -m pytest -q
```

`.github/workflows/docker.yml` 会在代码、依赖、测试或 Docker 配置变化时运行测试并构建镜像：

- `main` 分支推送会构建并推送 `latest` 和提交 SHA 标签到 GHCR；
- `v*.*.*` 标签推送会额外生成对应版本标签；
- Pull Request 只测试和构建，不推送镜像；
- 仅修改 `README.md`、`ARCHITECTURE.md` 等未列入路径过滤的文档不会触发该工作流；
- 提交信息包含 `[skip ci]` 或 `[ci skip]` 时可跳过本次工作流。

镜像由 CI 构建为 `linux/amd64` 和 `linux/arm64`，便于常见服务器和开发机使用。

## 常见问题

- **拉取镜像超时**：确认 Docker 守护进程能访问 Docker Hub/GHCR；必要时为 Docker Desktop 或服务器 Docker 配置代理或镜像源。
- **反代后资源路径错误**：检查 `CLOUDMUSIC2KTV_BASE_PATH` 是否与代理前缀完全一致，并确认代理发送 `X-Forwarded-*` 请求头。
- **视频生成失败**：查看 `docker compose logs -f cloudmusic2ktv`，确认 `docker-data/outputs` 可写且磁盘空间充足。
- **字体或 FFmpeg 问题**：官方镜像已安装 FFmpeg 和 Noto CJK/DejaVu 字体；Python 直接运行时请自行安装对应系统依赖。
