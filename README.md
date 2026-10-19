# Continue Gaming

让大模型的视觉决策在实时环境中准时执行。Continue Gaming 将提前观察、并发判断、时延校准和独立执行队列组合成可运行的工作流，并提供可核验的对照实验。

## Codex 五组对照

历史巡检线 awake-v1 确认序列中，原生 Codex 标准组 **0/5 通过**，增加完整工作流后 **5/5 通过**。

| 指标 | 标准 Codex | Codex + 完整工作流 |
| --- | ---: | ---: |
| 正式任务通过 | 0/5 | **5/5** |
| 各组缺陷拦截数（每组 12 件） | 2、10、1、5、4 | **12、11、11、11、11** |
| 含练习及子调用的已知 token | 8,909,019 | **7,151,851** |

这组实验使用 `gpt-6-astra / medium`、Codex CLI `0.157.0`，每次 900 秒预算。标准组拥有完整编程、后台调度和同模型子调用权限。五组覆盖同一批 24 张图片、两个 SKU 的不同出现时序，比较的是完整工作流。

[**查看实测报告**](codex_comparison/README.md) · [逐组指标](codex_comparison/summary.json) · [十次完整证据](codex_comparison/evidence/)

## 工作流优势

- **准时执行**：模型提前给出具体动作时间，独立执行器持续运行，衔接视觉判断与物理动作窗口。
- **并发处理**：四路单件短上下文调用，让多个对象的判断可以同时推进。
- **实测校准**：在开发环境测量延迟，用数据确定执行提前量。
- **完整可追溯**：该确认序列的 120 个正式判断、57 次实际动作均可沿模型输出、执行队列和签名回执核对，实际动作无过期。
- **可复用结构**：将观察、判断、调度和执行分开组织，适合持续变化且动作有截止时间的任务设计。

## 两个任务环境

- **TraceLine / 巡检线**：比较真实电路板 query 与独立正常参考照片，在 ±350 ms 时间窗内完成四通道分拣。
- **Aster / 星航**：实时弹幕环境，用于开发和评估视觉控制工作流。

上面的五组成绩来自历史巡检线确认序列。当前任务包提供统一命名的环境、界面和评测入口。

[打开预览](continuum_lab/preview/index.html) · [评测工具说明](continuum_lab/evaluator/README.md)

## 证据与复现

仓库包含任务代码、素材、隔离运行时、公开模型轨迹、输入图片、动作日志、独立评分和用量账本。签名 session 保留原字节，并提供 SHA-256 文件映射和历史审计记录。

[证据映射](codex_comparison/evidence-map.json) · [登记方案](codex_comparison/audit/registered-plan.json) · [实验档案](codex_comparison/prior-attempts/) · [运行观察](codex_comparison/audit/runtime-result.json)

正式评测通过 Harbor 隔离运行，评分产物统一为 `/app/output/session.json`。包内 signing.key 为评测夹具；独立盲评可生成新的隔离密钥。第三方许可和素材声明保留在对应目录。
