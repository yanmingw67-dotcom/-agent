# 产品参数评估工作台（P2.3 前端）

浏览器端工作台，用于普通售前用户完成：

1. 选择产品品类，上传《招标技术参数表》；
2. 核对系统提取的参数与原子子要求；
3. 启动参数评估，逐条查看“支持 / 不支持 / 无法确认”结论、证据与限制；
4. 提交人工复核意见；
5. 创建并下载可编辑的 Word / Excel 评估结果。

用户只选择产品品类，不选择产品版本，不上传源码或产品文档。产品能力知识由管理员维护；本工作台只消费已发布且已审核的知识快照。

## 技术栈

- React 19 + TypeScript 5.8 + Vite 6
- 无第三方路由库，使用 hash 路由
- 无第三方 UI 组件库；样式位于 `src/styles.css`
- 访问令牌保存在浏览器 `localStorage`，请求统一使用 `Authorization: Bearer <token>`

## 本地启动

先启动后端（在仓库 `backend` 目录）：

```bash
uv sync
uv run alembic upgrade head
export ADMIN_TOKEN='<管理员令牌>'
export READ_TOKEN='<普通用户令牌>'
export CORS_ORIGINS='http://127.0.0.1:5173,http://localhost:5173'
uv run uvicorn --factory app.main:create_app --host 127.0.0.1 --port 8000
```

再启动前端：

```bash
cd backend/frontend
npm ci
cp .env.example .env
npm run dev
```

默认前端地址为 `http://127.0.0.1:5173`。登录页粘贴后端配置的普通用户令牌即可。

`VITE_API_BASE` 用于指定后端地址，默认 `http://127.0.0.1:8000`。修改后需重启 Vite 开发服务。

## 后端跨域配置

独立部署前端时，后端必须允许前端来源跨域，否则浏览器预检会失败。

- `CORS_ORIGINS`：逗号分隔的允许来源，默认 `http://localhost:5173,http://127.0.0.1:5173`
- 留空表示关闭 CORS
- 不使用 Cookie，令牌通过 `Authorization` 请求头传递，因此不启用凭据跨域

生产环境建议由同一域名反向代理前后端，并将 `CORS_ORIGINS` 留空或仅配置实际前端域名。

## 页面入口

| 路由 | 页面 | 说明 |
| --- | --- | --- |
| `#/tasks` | 任务列表 | 任务状态、结论计数、复核进度、创建时间 |
| `#/new` | 新建评估 | 选择品类、上传参数表、确认提取结果 |
| `#/extractions/{id}` | 提取确认 | 核对参数、原子子要求、原文位置 |
| `#/evaluations/{id}` | 评估进度 | 等待评估完成，可继续进入结果页 |
| `#/tasks/{id}` | 评估结果 | 概览、逐条结论、证据、人工复核、导出下载 |

## 构建与检查

```bash
npm run typecheck
npm run build
```

构建产物位于 `dist/`，`node_modules`、`dist`、`.env`、`*.local`、`.vite` 均已被忽略，不进入版本库。

## 边界

- 本前端不实现账号、角色、组织架构或管理员知识维护界面。
- 人工复核只追加记录，不覆盖系统自动结论或证据。
- 下载链接绑定任务归属、导出任务和短期下载令牌；前端不自行拼接后端文件路径。
- 当前验收入口使用合成样例数据，不代表真实 WAF 产品能力已经验收。
