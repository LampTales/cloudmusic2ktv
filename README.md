# CloudMusic2KTV

CloudMusic2KTV 是一个将用户有权访问的网易云歌曲素材转换为带封面、歌词和频谱效果的 KTV 风格视频，并通过网页播放或投屏的自托管工具。

推荐使用 Docker 部署。项目也保留 Python 直接运行方式，适合开发、排查问题和本地调试。

请只处理你有权访问的歌曲，并仅用于个人或获授权的使用场景。本项目不绕过网易云音乐的付费或播放权限；音频、歌词、封面和生成视频可能受到版权及服务条款限制。

## 正式部署：Docker + HTTPS 反向代理

### 运行要求

- 一台 Linux 服务器（有公网 IP，或能被域名解析到）；
- Docker Engine 和 Docker Compose Plugin；
- 一个已经配置 DNS 的域名；
- Nginx、Caddy 或其他能够提供 HTTPS 的反向代理；
- 服务器能够访问 Docker Registry。只有选择“服务器自行构建”时，才需要额外访问 Debian 软件源和 PyPI。

正式部署不需要在服务器上安装 Python。Docker 镜像会包含 Python、FFmpeg、Noto CJK 字体和项目依赖。

### 1. 准备运行目录和配置

直接拉取镜像时，服务器不需要完整源码，只需要 `docker-compose.yml`、一个用于配置的 `.env` 文件和运行数据目录。可以同时取得仓库中的 `.env.example` 作为模板，也可以直接创建 `.env`：

~~~
mkdir -p cloudmusic2ktv/docker-data/instance cloudmusic2ktv/docker-data/outputs
cd cloudmusic2ktv
cp .env.example .env
~~~

如果没有取得 `.env.example`，可以直接创建 `.env`：

~~~
touch .env
~~~

然后按照下面的示例填写镜像地址、子路径和反代设置。

如果应用发布在域名的 /ktv/ 子路径，.env 使用：

~~~
CLOUDMUSIC2KTV_IMAGE=ghcr.io/<OWNER>/<REPOSITORY>:latest
CLOUDMUSIC2KTV_BASE_PATH=/ktv
CLOUDMUSIC2KTV_TRUST_PROXY=1
CLOUDMUSIC2KTV_BIND_ADDRESS=127.0.0.1
~~~

BASE_PATH 是应用所在的 URL 前缀；TRUST_PROXY=1 只在完全控制反向代理时启用；BIND_ADDRESS=127.0.0.1 可避免容器端口直接暴露到公网。新安装时，建议先暂时只允许内网访问，完成管理员初始化后再开放公网。

### 2. 方案 A：直接拉取 GitHub Container Registry 镜像（推荐）

GitHub Actions 会在 `main` 更新后构建并推送 `latest` 镜像。服务器上执行：

~~~
docker compose pull
docker compose up -d --no-build
docker compose ps
~~~

如果 GHCR 镜像是私有的，先使用具有 `read:packages` 权限的 Token 登录：

~~~
docker login ghcr.io
~~~

该方式不需要在服务器上安装 Python，也不会把网易云 Cookie 或本地媒体放进镜像。

### 3. 方案 B：服务器从源码自行构建

如果不想使用 GHCR，或需要部署尚未推送到镜像仓库的代码：

~~~
git clone <你的仓库地址> cloudmusic2ktv
cd cloudmusic2ktv
cp .env.example .env
~~~

确认 `.env` 中使用：

~~~
CLOUDMUSIC2KTV_IMAGE=cloudmusic2ktv:local
~~~

然后执行：

~~~
mkdir -p docker-data/instance docker-data/outputs
docker compose build
docker compose up -d
docker compose ps
~~~

只需要传递代码和 Docker 文件。不要复制本地的 instance/、outputs/、docker-data/、.env 或 .venv/；其中可能包含网站账号、网易云 Cookie、会话和本地媒体。

两种方式都会把账号、网易云绑定、歌曲素材和生成视频保存在 `docker-data/`，该目录不会被复制进镜像，也不会因为重新构建镜像而删除。

查看日志或停止服务：

~~~
docker compose logs -f
docker compose down
~~~

### 4. 配置 Nginx 子路径反代

假设访问地址为 https://example.com/ktv/：

~~~nginx
location /ktv/ {
    proxy_pass http://127.0.0.1:7860/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Prefix /ktv;
    client_max_body_size 32m;
    proxy_read_timeout 300s;
}
~~~

proxy_pass 末尾的 / 很重要：它会把 /ktv 剥离后转发到容器。反向代理必须覆盖而不是信任客户端传入的 X-Forwarded-* 请求头，并负责 HTTPS 证书。

应用会自动让 API、静态资源、视频链接和会话 Cookie 跟随 /ktv 前缀。未使用反向代理时不要设置 CLOUDMUSIC2KTV_TRUST_PROXY=1。

### 5. 完成初始化和更新

通过 HTTPS 打开网页后，先使用自己的网易云账号注册并确认成为管理员，再添加朋友账号。网易云 Cookie 保存在服务器的 docker-data/instance/ 中，服务器管理员可以读取该目录。

使用 GHCR 镜像更新时，等待当前视频生成任务完成并备份数据：

~~~
docker compose pull
docker compose up -d --no-build
~~~

如果使用源码构建，则执行：

~~~
git pull
docker compose build
docker compose up -d
~~~

当前视频任务只保存在 Python 进程内，重启会丢失队列状态并中断正在生成的视频。

## Docker 局域网调试

这一步适合先在 macOS Docker Desktop 上验证 Linux 容器行为。

确保 .env 中保持：

~~~
CLOUDMUSIC2KTV_BASE_PATH=
CLOUDMUSIC2KTV_TRUST_PROXY=0
CLOUDMUSIC2KTV_BIND_ADDRESS=0.0.0.0
~~~

在项目根目录执行：

~~~
mkdir -p docker-data/instance docker-data/outputs
docker compose build
docker compose up -d
docker compose ps
~~~

Mac 本机访问 http://127.0.0.1:7860/；局域网其他设备访问 http://<Mac 局域网 IP>:7860/。使用 ipconfig getifaddr en0 查看 Mac IP。首次 Docker 测试建议重新登录并重新生成测试素材，不要把当前调试用的 instance/ 或 outputs/ 映射到容器。

## 基本使用流程

1. 在登录窗口注册或登录网站账号；首次注册需要验证允许名单中的网易云账号。
2. 在“02 选择歌曲”中输入歌曲 ID、网易云链接或歌名和歌手。
3. 选择音质并下载完整素材。
4. 在“04 生成 KTV 视频”中选择歌词、背景、扫色、分辨率和画质，然后加入队列。
5. 在“03 投屏已有视频”中播放、下载或尝试投屏。

视频由 CPU 渲染，1080p 或较长歌曲可能需要较长时间。生成期间不要重启容器。

## Python 直接运行（开发和调试）

Docker 是推荐的部署方式。需要调试源码时，也可以使用 Python 3.11 或更高版本。

Windows PowerShell：

~~~
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
~~~

macOS / Linux：

~~~
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
~~~

直接运行时默认监听 0.0.0.0:7860。本机访问 http://127.0.0.1:7860/，局域网设备访问运行电脑的局域网 IP。直接运行使用 Flask 开发服务器，不适合公网部署。局域网 HTTPS 测试可以设置 CLOUDMUSIC2KTV_TLS_CERT 和 CLOUDMUSIC2KTV_TLS_KEY。

## 文件和数据

歌曲素材和视频保存在 outputs/<歌曲ID>_<歌手>_<歌名>/。网站账号、网易云绑定和会话保存在 instance/；Docker 部署时对应 docker-data/instance/。这些目录都不应提交到公开仓库或复制到 Docker 镜像。

## 常见问题和测试

### Docker 构建超时

首次构建需要访问 Docker Hub、Debian 软件源和 PyPI。可以先执行：

~~~
docker pull python:3.11-slim-bookworm
~~~

如果超时，应配置 Docker Desktop 或 Docker daemon 的网络代理。

### 生成速度较慢

尝试 720p、较小画质或关闭动态频谱。生成期间不要重启容器。

### 运行离线测试

macOS / Linux：

~~~
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
node --check static/app.js
~~~

Windows PowerShell：

~~~
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
node --check static\app.js
~~~

开发者和后续维护者需要了解代码结构时，请阅读 ARCHITECTURE.md。
