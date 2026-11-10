# CI Trace

CI Trace 用于诊断公开 GitHub Actions 失败。粘贴运行链接，查看对应轮次、失败 Job、步骤和证据缺口，并下载诊断报告与可回放轨迹。可选深度分析按 Job 读取证据并引用证据 ID。

- [启动与测试](ci-trace/README.md)
- [结项状态与优化方向](ci-trace/closeout.md)
- [许可证](LICENSE)

项目从 DeerFlow fork 提取，现使用独立的 Next.js、FastAPI 和 SQLite。裁剪前的源码保存在 Git 提交 `56c0d52a0`。
