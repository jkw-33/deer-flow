# CI Trace 结项说明

## 最终状态

- 作品集演示入口为 `/diagnoses`。公开 GitHub Actions 运行只需链接，即可查看固定轮次、失败 Job 和步骤、检查注释及证据缺口，并下载报告和 JSON 轨迹。首页保留可直接打开的真实失败案例。
- 可选深度分析按 Job 读取受限证据，要求观察和推断引用证据 ID。分析可刷新回放，失败后可显式重试。私有授权和 GitHub 托管复跑为实验能力。
- 前端使用 Next.js、React 和 TypeScript。后端使用 Python、FastAPI 和 SQLite，通过 DeerFlow Gateway 提供服务。运行配置见 [CI Trace 配置](../backend/README.md#ci-trace)。
- CI Trace 与路由回归 105 项通过。阻塞 I/O 检查 344 项通过、1 项跳过。公开单 Job、双 Job 和本地 CRLF 验证已通过，模型流程使用本地测试模型验收。DeerFlow 完整后端套件曾有 39 项失败，其中 30 项集中在扩展管理。这些非 CI Trace 项不阻塞作品集结项。

## 优化方向

1. 用真实模型跑单 Job 和双 Job，保留诊断与模型轨迹，人工核对证据引用、覆盖范围和根因判断。
2. 增加不同运行轮次、日志缺失和 CRLF 的固定对照案例。沿用 `scripts/evaluate_ci_trace.py` 和 `contracts/ci-trace-*.json`。
3. 如需把实验能力纳入正式演示，分别验证私有仓库的访问隔离和撤权，以及 GitHub 托管复跑的实际 Job 结果。
