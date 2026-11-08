# 报错对照

| 现象 | 原因 / 处理 |
|---|---|
| `Permission 'kernels.get' was denied` / 404 | slug 不对。Kaggle 用 title 反推 slug，title 与 id 不同源会被改写；取 push 输出 URL 里的真实 slug |
| 重推后 `secret XXX unavailable` | Secret 是 kernel 设置，`kernels push` 会覆盖它。改用 `--env-from` 注入 |
| `relative import with no known parent package` | 入口用了相对导入。`--run-as module`，或改成绝对导入 |
| `ModuleNotFoundError` | 依赖没写进仓库根 `requirements.txt` |
| 运行中 `logs` 为空（几字节） | 正常，Kaggle 日志推送有延迟。以 `kk status` 为准 |
| 状态 error 但日志还是旧的 | 等 1~2 分钟再拉；或 `kaggle kernels logs <slug>/<version>` 看指定版本 |
| `kaggle kernels output` 有几十 MB | 入口把中间缓存写进 `/kaggle/working`（如 75MB K 线缓存）。用 `kk pull --pattern` 过滤 |
| 中文日志乱码 | kk 内部已强制 `PYTHONUTF8=1`；自己直接调 kaggle CLI 时也要设 |
| 时间对不上 | Kaggle 是 UTC，本地 +8 |
| Output 页没有 “Create Dataset” 按钮 | 新 UI 已移除。用容器内中转：开一个 maker kernel，里面 `kaggle kernels output <src>`（内网 56MB/s）→ `kaggle datasets create`。430MB 只需约 50 秒，本地零带宽 |
| 容器里 `python -m kaggle` 报 `No module named kaggle.__main__` | 镜像自带 2.0.2 没有 `__main__.py`。先 `pip install -U kaggle`，再调 `kaggle` 命令本身（console script） |
| `datasets create` 报 `id` 前缀错 | dataset id 前缀必须是 Kaggle **用户名**（不是 GitHub 用户名） |
| push 报 `409 Conflict` | 之前 title 与 slug 不同源导致 Kaggle 改写 id，再用旧 slug 就撞车。取 push 输出里的真实 slug |

## 长任务前必查

1. 脚本有重试吗？单次 `ProtocolError` 就能毁掉 7 小时。
2. 有 probe / smoke 小样吗？先小样，再全量。
3. 中间产物在 `/tmp` 还是 `/kaggle/working`？后者会进 output，下载变慢。
4. 失败后要重来多久？有没有断点续传？
