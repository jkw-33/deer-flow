# CI Trace

CI Trace 诊断公开 GitHub Actions 失败。提交运行链接即可查看对应轮次、失败步骤、证据缺口和可下载的报告。首页也有内置回放案例。

如果 GitHub Actions 仍在排队或运行，报告只记录该轮次的当前状态。运行结束后可重新提交链接。采集中断后，打开原诊断页可从已写入的 Job 继续。

## 容器启动

启动 Docker Desktop，在任意 PowerShell 目录运行：

```powershell
docker compose -f D:\ci-trace\ci-trace\compose.yaml up --build -d
```

打开 [诊断页](http://127.0.0.1:3300/diagnoses)。端口仅绑定本机，可在 `.env` 中通过 `CI_TRACE_WEB_PORT` 修改。实时诊断需要访问 GitHub。可按 [.env.example](.env.example) 设置公开仓库读令牌。

深度分析可在页面配置真实模型。模型密钥加密保存，SQLite 与本机生成的加密密钥共用 `trace-data` 卷。自定义模型地址需列入 `CI_TRACE_MODEL_BASE_URLS`。基础诊断无需模型。

诊断结果页直接显示证据采集时间线。深度分析区按顺序显示模型启动、工具调用、Job 调查和结论，并提供可跳转的证据引用。JSON 和文本报告可另行导出。

可选公开验证会把候选交给独立的受限验证服务。已有验证服务时，设置 `CI_TRACE_RUNNER_URL` 和 `CI_TRACE_RUNNER_TOKEN`。未配置时，时间线会记录跳过原因。

```powershell
docker compose -f D:\ci-trace\ci-trace\compose.yaml down
```

`down` 保留诊断数据。评测脚本和参考输入见 `scripts/`、`contracts/`。[结项说明](closeout.md)记录当前验收范围和优化方向。

项目从 DeerFlow fork 提取，沿用 [MIT 许可证](LICENSE)。
