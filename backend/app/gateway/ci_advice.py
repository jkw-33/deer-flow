"""Conservative repair suggestions based on observable CI errors."""

import re


def repair_advice(log: str | None) -> list[str]:
    if not log:
        return ["打开失败步骤的完整日志，找出第一条实际报错，再决定修改哪一处。"]
    advice = []
    if "ERR_PNPM_OUTDATED_LOCKFILE" in log:
        advice.append("使用项目指定的 pnpm 版本更新 pnpm-lock.yaml，再执行 pnpm install --frozen-lockfile 验证。")
    if re.search(r"curl: \(22\) The requested URL returned error: 50[234]", log):
        advice.append("重跑失败 Job。若下载仍返回 502、503 或 504，从同类 runner 检查下载地址和网络路径。")
    if "$'\\r': command not found" in log or re.search(r"bad interpreter[^\n]*(?:\^M|\\r)", log):
        advice.append("检查报错所指 shell 脚本的原始换行。若确认含 CRLF，仅转换该脚本为 LF，再重跑原命令。")
    if re.search(r"\berror TS\d{4}:", log):
        advice.append("打开第一条 TypeScript 错误指向的文件和行，修正类型后重跑原类型检查命令。")
    return advice or ["从失败步骤的第一条错误开始核对命令、配置和对应提交，再用同一检查验证候选改动。"]
