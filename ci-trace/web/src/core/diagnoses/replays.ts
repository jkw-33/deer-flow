import type { DiagnosisReplay } from "./types";

const runUrl = "https://github.com/pnpm/pnpm/actions/runs/42583599525";
const jobUrl = `${runUrl}/job/129047950518`;
const checkoutSha = "5d62d6f284c4f45171c84198796ae550b8292a09";
const sourcePath = ".github/actions/wasm-toolchain/action.yml";

export const pnpmReplay: DiagnosisReplay = {
  id: "pnpm-wasi-504",
  repository: "pnpm/pnpm",
  workflow: "pnpm WebAssembly",
  attempt: 1,
  runUrl,
  headSha: "5446aad11b679dc7d988ec92b1fc08e0e3875b8e",
  checkoutSha,
  events: [
    {
      seq: 1,
      phase: "resolve",
      title: "固定运行轮次",
      summary: "Run 42583599525 · 第 1 次运行",
      status: "completed",
      evidenceIds: ["run"],
    },
    {
      seq: 2,
      phase: "collect",
      title: "定位失败步骤",
      summary: "第 5 步 · Install pinned WebAssembly toolchain",
      status: "completed",
      evidenceIds: ["job"],
    },
    {
      seq: 3,
      phase: "collect",
      title: "读取 Job 日志",
      summary: "授权读取成功 · 记录了 4 次 HTTP 504",
      status: "completed",
      evidenceIds: ["download"],
    },
    {
      seq: 4,
      phase: "investigate",
      title: "确认实际 checkout",
      summary: "日志显示 runner 检出了 PR 合并提交 5d62d6f",
      status: "completed",
      evidenceIds: ["checkout", "source"],
    },
    {
      seq: 5,
      phase: "publish",
      title: "WASI SDK 下载失败",
      summary: "curl 首次请求及 3 次重试均收到 HTTP 504",
      status: "finding",
      evidenceIds: ["download", "source"],
    },
  ],
  evidence: [
    {
      id: "run",
      kind: "run",
      title: "GitHub Actions 运行",
      location: "42583599525 · 第 1 次运行",
      url: runUrl,
    },
    {
      id: "job",
      kind: "run",
      title: "失败 Job",
      location: "Build and run pnpm in WebContainers · 第 5 步",
      url: jobUrl,
    },
    {
      id: "checkout",
      kind: "log",
      title: "Checkout 日志",
      location: "Job 129047950518 · 已保存摘录",
      url: jobUrl,
      excerpts: [
        {
          line: 155,
          text: "HEAD is now at 5d62d6f Merge 5446aad11b679dc7d988ec92b1fc08e0e3875b8e into 5e52de7af190b07a6a9dfbb85a747c6800c90d92",
        },
        { line: 158, text: checkoutSha },
      ],
    },
    {
      id: "download",
      kind: "log",
      title: "下载失败日志",
      location: "Job 129047950518 · 已保存摘录",
      url: jobUrl,
      excerpts: [269, 274, 279, 284].map((line) => ({
        line,
        text: "curl: (22) The requested URL returned error: 504",
      })),
    },
    {
      id: "source",
      kind: "source",
      title: "WASI SDK 下载命令",
      location: `${sourcePath}:21`,
      url: `https://github.com/pnpm/pnpm/blob/${checkoutSha}/${sourcePath}#L21`,
      excerpts: [
        {
          line: 21,
          text: "curl --fail --location --retry 3 --output wasi-sdk.tar.gz https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-34/wasi-sdk-34.0-x86_64-linux.tar.gz",
        },
      ],
    },
  ],
  report: {
    summary:
      "WASI SDK 下载请求返回 HTTP 504，导致工具链安装失败。curl 最终以退出码 22 结束。",
    limits: "现有证据无法确定 504 来自上游服务、代理还是 runner 的网络路径。",
    validation: "未执行修复验证。这次回放使用原始运行的日志与源码摘录。",
    evidenceIds: ["download", "source"],
  },
};
