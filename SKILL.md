---
name: kaggle-runner
description: 在任意 Python 项目中把代码推到 GitHub 并在 Kaggle 上运行指定 py 入口、跟进状态、拉日志、拉取产物并发布为 dataset 时使用。触发语：「推到 kaggle 跑」「跑一下这个实验」「跑到哪了」「看看 kernel 日志」「还在跑吗/停了没」「重跑一遍」「拉产物」「发布 dataset」。
---

# Kaggle Runner

工具 `kk`，优先用本 skill 自带副本：`python ~/.codebuddy/skills/kaggle-runner/scripts/kk.py`
参数细节见 `references/cli.md`，报错处理见 `references/troubleshooting.md`。

## 默认流程

1. **推 GitHub**：先 `git status`。commit message 由助手按改动内容自行拟定，无需询问用户。
2. **选模式**：入口脚本自己能 clone/装依赖 → `--mode direct`；普通项目代码 → 默认 clone 模式。
3. **凭据一律注入**：`--env-from CHD_API_KEY`（从本地环境变量取值，不打印、不进仓库）。
   本地没设过就让用户 `setx` 一次，之后每次都自动带上。
4. **长任务先小样**：`--env MODE=probe`（或 smoke）通过后再推全量。
5. **跟进**：`kk status` 或 `kk wait --slug X --timeout N`。失败先 `kk logs --grep "Error|Traceback"`，不要盲目重推。
6. **收产物**：`kk pull --pattern "正则"` 只拉要的文件；要留存再 `kk publish --dataset user/ds`。
7. **报告**：状态、关键指标行、dataset 链接。

## 硬性约束

- **不加 `--gpu`**，除非用户明确要求。
- **凭据只走 `--env-from` 注入私有 kernel**（`is_private=true`，kk 默认）。
  不要依赖网页 attach Kaggle Secret：那是 kernel 设置，`kernels push` 会覆盖，一重推就丢；
  注入写在 code_file 里，重推照样在——**这是整条链路能全自动的前提**。
  绝不用 `--env KEY=值`（会进命令历史），绝不进公开 kernel。
- **push 前声明影响范围**：只说"影响 `<slug>`"。不要对不是本次创建的 kernel 动手。
- **dataset 默认私有**，要公开必须用户点头。
- driver 由 kk 生成，不要手写或提交仓库；clone 目标必须在 `/tmp`。
- 产物 GB 级不走 `publish`，改用 kernel 内写 GitHub Release。

## 时间与状态

- **Kaggle 时间戳全是 UTC**，本地 +8。说"多久之前"前先换算；拿不准就用刚推送的 kernel 交叉验证。
- **运行中 `logs` 可能返回空**（只有几字节），以 `kk status` 为准，别误判卡死。
- 长任务（>1h）开工前确认：脚本有重试/续跑吗？失败一次要重来多久？中间产物在 `/tmp` 还是 `/kaggle/working`？
