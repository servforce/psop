# PSOP Static Web

该目录包含 PSOP 的静态管理端与运行终端，使用本地 TailwindCSS v4、Alpine.js
和 Jest，不依赖新的前端框架。

运行页对 terminal、IO 和 Replay 采用首次激活挂载并保留 DOM 的策略；媒体 part
按 MIME/kind 归一后互斥渲染，图片 lazy/async 解码，音视频在用户播放前不预加载。

## 本地开发

```bash
cd static
npm ci
npm run build:css
npm run dev
```

或从仓库根目录运行：

```bash
scripts/dev/build-web.sh
scripts/dev/test-web.sh
scripts/dev/run-web.sh
```
