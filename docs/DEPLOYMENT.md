# 自动部署（GitHub Actions）

仓库内置了一套自动部署流程：每次推送到 `main` 或 `experimental` 后，GitHub Actions 会先构建并推送 Docker 镜像，再通过 SSH 将部署脚本和 Compose 配置下发到服务器。服务器只拉取预构建镜像并重启服务，不需要执行 Git 同步，也不在服务器上安装 PyTorch、编译依赖或构建 Docker 镜像。

默认推送的是轻量 `standard` 镜像；在 GitHub Actions 的 **Run workflow** 中选择 `enhanced`，可以构建并发布包含本地 NLI、Docling 和 PaperQA2 依赖的增强镜像。

## 需要的 GitHub Secrets

在仓库 **Settings → Secrets and variables → Actions** 中添加：

| Secret 名称 | 说明 |
| --- | --- |
| `SERVER_HOST` | 服务器 IP，例如 `101.96.220.158` |
| `SERVER_PORT` | SSH 端口，默认 `22`，可不填 |
| `SERVER_USER` | SSH 用户，例如 `root` |
| `SERVER_SSH_KEY` | SSH 私钥（建议用专门的部署 key，不要用登录密码） |
| `SERVER_GHCR_USERNAME` | 使用私有 `ghcr.io` 镜像时，服务器登录 GHCR 的 GitHub 用户名；当前公开 NJU 镜像方案可不填 |
| `SERVER_GHCR_TOKEN` | 使用私有 `ghcr.io` 镜像时，具有 `read:packages` 权限的 GitHub PAT；当前公开 NJU 镜像方案可不填 |

CI 使用 `docker/build-push-action` 构建并缓存镜像，再通过 `webfactory/ssh-agent` 注入私钥。部署时会：

1. 构建并推送 branch tag 和不可变的 SHA tag；
2. 将 `scripts/deploy_server.sh` 和 `docker-compose.yml` 直接复制到服务器的 `/root/research-radar`；
3. 使用 `ghcr.nju.edu.cn` 上的 SHA tag 拉取镜像；
4. 执行 `docker compose up -d --no-build --remove-orphans`；
5. 轮询 `/api/health`，确认服务恢复后结束部署。

服务器端保留的 `data/`、`Caddyfile`、`certs/` 和 Caddy 数据卷不会被 CI 覆盖。应用代码和 Python/Node 依赖都来自镜像，不再依赖服务器工作区中的 Git 代码。

自动部署使用的镜像格式为：

```text
ghcr.nju.edu.cn/gz-november/research-radar:sha-<commit-sha前12位>-<profile>
```

镜像同时发布到 GHCR；国内服务器部署时通过 NJU 镜像地址拉取：

```text
ghcr.io/gz-november/research-radar:<branch>-standard
ghcr.io/gz-november/research-radar:<branch>-enhanced
ghcr.io/gz-november/research-radar:sha-<commit-sha前12位>-standard
ghcr.io/gz-november/research-radar:sha-<commit-sha前12位>-enhanced
```

## 手动部署与回滚

自动部署之外，如果需要手动部署或回滚，服务器只需要已有的 Compose 目录和部署脚本；不需要执行 `git pull`。推荐使用具体的 SHA tag：

```bash
cd /root/research-radar
RADAR_IMAGE_REPOSITORY=ghcr.nju.edu.cn/gz-november/research-radar \
RADAR_IMAGE_TAG=sha-<commit-sha前12位>-standard \
RADAR_PROFILE=standard \
bash scripts/deploy_server.sh
```

将 `RADAR_IMAGE_TAG` 换成之前成功发布的 SHA tag 即可回滚。若需要更新部署脚本或 Compose 配置，应从本地工作区或 GitHub Actions 重新复制这两个文件；服务器不会自动拉取仓库代码。

部署脚本会重试镜像拉取，并在服务启动后等待最多 180 秒的健康检查。部署成功后会清理服务器上未被容器使用且超过 7 天的 Docker 镜像；因此，超过 7 天的旧 SHA 镜像不保证仍可用于回滚。

如果改为直接从私有 `ghcr.io` 拉取镜像，首次使用前在服务器登录一次：

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io --username "$GHCR_USERNAME" --password-stdin
```

注意：当前自动部署的 Compose 镜像地址是 `ghcr.nju.edu.cn`。对 `ghcr.io` 的登录凭据不会自动作为 NJU 镜像的认证凭据；因此保持 GHCR 包公开，才是当前国内镜像方案下最可靠的自动部署配置。若要改为 private，需要同时调整镜像拉取地址和对应的认证方式。

标准镜像使用 Python 3.12、PyMuPDF 和远程/外部 embedding 配置，不安装本地
PyTorch、Docling 或 PaperQA2。增强镜像只在 CI 构建时安装这些可选依赖，服务器仍然只执行 `docker compose pull`。

本地开发仍然可以直接构建标准镜像：

```bash
docker compose up -d --build
```

本地需要增强镜像时：

```bash
RADAR_INSTALL_EXTRAS=full \
RADAR_IMAGE_TAG=local-enhanced \
docker compose up -d --build
```

增强镜像安装依赖后，高级能力仍由开关控制：

- `NLI_ENABLED=true`：启用本地 NLI 二次判断。
- `PDF_PARSER_BACKEND=docling`：启用 Docling PDF 解析。
- `PAPERQA2_ENABLED=true`：启用 PaperQA2 额外核验。

如果可选包缺失或运行时失败，当前代码会降级到 PyMuPDF、LLM 和规则判断，不会直接中断主扫描流程。

## 公网部署账号安全

公网部署建议关闭自助注册：

1. 第一次启动时保持 `ALLOW_REGISTRATION=true`，注册第一个账号。第一个账号会自动成为管理员。
2. 在服务器的 `data/settings.local.env` 中设置：

   ```env
   ALLOW_REGISTRATION=false
   ```

3. 重启应用：

   ```bash
   docker compose restart research-radar
   ```

4. 管理员进入应用的“用户与权限”页面，使用“创建用户”给团队成员创建普通账号。

认证接口对同一来源的连续登录/注册尝试有频率限制。账号数据库位于服务器的
`data/users.db`，与应用数据卷一起保留；不要删除该文件，否则会丢失账号和会话数据。

## 安全建议

- 不要把服务器密码/私钥提交进仓库。
- 建议在服务器上为 CI 创建单独的只读/部署用户，或使用专用的 deploy key。
