# CI Trace

- `ci-trace/api/main.py` 是 FastAPI 入口。`ci-trace/web/` 是独立 Next.js 应用。
- 仅通过 [Docker Compose](ci-trace/README.md) 启动应用。后端测试位于 `ci-trace/tests/`，开发依赖见 `ci-trace/requirements-dev.txt`。
- `ci_pipeline.py` 编排公开证据采集和验证。`ci_store.py` 管理 SQLite 状态、租约和模型设置。`ci_model.py` 管理模型请求和自定义地址白名单。
- 诊断必须固定 GitHub Actions attempt，确认公开可见后才读取 Job。快照、流和下载须检查会话归属并拒绝旧私有记录。原始日志、模型密钥及 GitHub 凭据不得写入诊断轨迹。
- 模型观察和推断必须引用 Job 范围内允许的证据 ID。显式重试保留旧尝试。
- `ci-trace/scripts/` 和 `ci-trace/contracts/` 保存轨迹评测入口和参考案例。
- 本地数据目录、密钥和运行环境留在 Git 之外。保留 `CLAUDE.md` 作为本文件的导入入口。
