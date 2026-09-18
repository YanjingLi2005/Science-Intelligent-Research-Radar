# 独立部署 Research Radar

本仓库的 GitHub Actions 工作流仅在手动点击 **Run workflow** 时运行。它在 GitHub 上构建镜像，推送到私有 GHCR，然后通过 SSH 在服务器上部署一套独立服务。服务器不需要从 GitHub 拉取源码，本地电脑也不需要运行 Docker Desktop。

新服务使用 `/root/research-radar-new`、Compose 项目 `research-radar-new` 和独立的 `data/` 目录。它只监听服务器本机的 `127.0.0.1:8502`，不会覆盖原有 `/root/research-radar`、其数据目录或占用 80/443/8080 端口的 Caddy。

## 首次运行前

1. 确认服务器已安装 Docker Engine、Docker Compose 和 `curl`，且 `127.0.0.1:8502` 未被占用。
2. 创建一对专用于部署的 SSH 密钥，将公钥加入服务器部署账号的 `authorized_keys`。当前工作流在服务器的 `/root` 下建立目录，因此 `SERVER_USER` 应为 `root`，除非同时修改部署路径和权限。不要把私钥提交到 Git。
3. 在仓库 **Settings → Secrets and variables → Actions** 中设置：

   | Secret | 值 |
   | --- | --- |
   | `SERVER_HOST` | 服务器 IP 或域名 |
   | `SERVER_PORT` | SSH 端口；留空时使用 `22` |
   | `SERVER_USER` | 当前脚本使用 `root` |
   | `SERVER_SSH_KEY` | 完整的部署 SSH 私钥，多行原样粘贴，不能设置口令 |
   | `SERVER_HOST_FINGERPRINT` | 与服务器提供商控制台核对过的 ED25519 主机指纹，格式为 `SHA256:...` |

工作流用该仓库自动生成的 `GITHUB_TOKEN` 登录 GHCR，并在服务器拉取本次构建的私有镜像；无需创建长期有效的 GHCR 令牌。镜像包必须允许本仓库的 Actions 访问。

## 部署

在仓库的 **Actions → Deploy Independent Research Radar → Run workflow** 中选择 `standard`。`enhanced` 会安装额外的本地 AI/PDF 依赖，镜像更大。工作流会把 `docker-compose.new.yml` 和 `scripts/deploy_new_server.sh` 放到新目录，拉取以当前提交 SHA 标记的镜像，启动服务，并检查 `http://127.0.0.1:8502/api/health`。

首次验证可在本机建立 SSH 隧道，不需要开放新公网端口：

```powershell
ssh -p 22 -L 8502:127.0.0.1:8502 root@<服务器地址>
```

保持该终端打开，然后在本机浏览器访问 `http://127.0.0.1:8502`。正式对外开放前，为新服务准备独立域名和可信 HTTPS 证书，再通过反向代理接入；不要直接把登录页面以明文 HTTP 暴露到公网。

## 查看状态

在服务器上运行：

```bash
cd /root/research-radar-new
docker compose -f docker-compose.new.yml ps
docker compose -f docker-compose.new.yml logs --tail=100 research-radar
curl -fsS http://127.0.0.1:8502/api/health
```

新服务的用户账号和论文数据保存在 `/root/research-radar-new/data`。更新容器时该目录保留；删除目录会丢失新服务的数据。
