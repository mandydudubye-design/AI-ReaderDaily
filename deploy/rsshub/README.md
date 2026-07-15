# 自建 RSSHub（Twitter / 小红书）

Maren AI Radar 的公众号目前可用公共 RSSHub；**Twitter 和小红书必须走自建实例**（公共实例不开放鉴权路由）。

## 1. 本地或 VPS 启动

```bash
cd deploy/rsshub
cp .env.example .env
# 编辑 .env，填入 TWITTER_AUTH_TOKEN、XIAOHONGSHU_COOKIE
docker compose up -d
```

验证：

```bash
curl -s "http://localhost:1200/wechat/sogou/机器之心" | head -c 200
curl -s "http://localhost:1200/twitter/user/sama" | head -c 200
```

第二条若返回 RSS 条目（而非 `Welcome to RSSHub`），说明 Twitter 鉴权已生效。

## 2. 暴露到公网（供 GitHub Actions 调用）

任选其一：

- **有公网 IP 的 VPS**：开放 `1200` 端口，或用 Nginx 反代到 `https://rsshub.你的域名.com`
- **仅本机**：用 Cloudflare Tunnel / frp 把 `localhost:1200` 映射出去

GitHub Actions **无法访问你电脑上的 localhost**，必须把 RSSHub 部署到 Actions 能访问的地址。

## 3. 接入 AI Radar

在 GitHub 仓库 **Settings → Secrets → Actions** 添加：

| Secret | 值 |
| --- | --- |
| `RSSHUB_BASE_URL` | `https://rsshub.你的域名.com`（不要末尾 `/`） |

保存后等下一次 hourly 工作流跑完，看板「信源健康」里 Twitter / 小红书 `item_count` 应大于 0。

## 4. 获取 Token / Cookie

### Twitter `TWITTER_AUTH_TOKEN`

1. 浏览器登录 [x.com](https://x.com)
2. 开发者工具 → Application → Cookies → `auth_token`
3. 填入 `deploy/rsshub/.env` 的 `TWITTER_AUTH_TOKEN`

### 小红书 `XIAOHONGSHU_COOKIE`

1. 浏览器登录 [xiaohongshu.com](https://www.xiaohongshu.com)
2. 开发者工具 → Network → 任意请求 → 复制完整 `Cookie` 头
3. 填入 `XIAOHONGSHU_COOKIE`

Cookie 会过期，失效后重新复制并 `docker compose up -d` 重启。

## 5. 常用运维

```bash
docker compose logs -f rsshub    # 看日志
docker compose pull && docker compose up -d   # 更新镜像
docker compose down            # 停止
```
