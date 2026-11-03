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

## 长任务前必查

1. 脚本有重试吗？单次 `ProtocolError` 就能毁掉 7 小时。
2. 有 probe / smoke 小样吗？先小样，再全量。
3. 中间产物在 `/tmp` 还是 `/kaggle/working`？后者会进 output，下载变慢。
4. 失败后要重来多久？有没有断点续传？
