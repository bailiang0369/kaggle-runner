# kk 命令速查

## 两种模式

- `clone`（默认）：上传约 25 行 driver，kernel 内 `git clone` 仓库 → 装 requirements → 跑入口。
  改代码只需 `git push`，不用重推 kernel。
- `direct`：入口脚本自包含（自己 clone / 自己 pip），整个文件当 code_file 上传。
  适合 `run_eth.py` 这类自带 bootstrap 的脚本。

## push / run

    kk run --entry orderbook/fusion_kl.py --env KAGGLE_OFFSETS=0 --env-from CHD_API_KEY --dataset-in byh850515/btcusdt-orderbook-features --pattern ".*pred.*parquet" --dest tmp/out

参数：

| 参数 | 说明 |
|---|---|
| `--entry PATH` | 仓库内相对路径，可含子目录 |
| `--repo URL` | 默认取 `git remote get-url origin` |
| `--ref BRANCH` | clone 指定分支/标签 |
| `--slug user/slug` | 默认由 title 反推；title 与 slug 必须同源，否则 Kaggle 会改写 id |
| `--title TEXT` | 默认 `<仓库名> <入口路径>` |
| `--env K=V` | 直接给值，**凭据不要用** |
| `--env-from NAME` | 从本地环境变量取值注入，**凭据用这个** |
| `--dataset-in user/ds` | 挂载数据，可重复 |
| `--mode clone\|direct` | 见上 |
| `--run-as auto\|module\|file` | auto = 文件名是合法标识符就走 `python -m` |
| `--gpu` | 默认关闭 |
| `--public` | 默认私有 |
| `--dry-run` | 只生成并打印 driver / kernel_main.py |

## 其它子命令

    kk status [--slug]
    kk wait [--slug] [--timeout 7200] [--interval 30]
    kk logs [--slug] [--grep 正则] [--tail N] [--save 文件]
    kk pull [--slug] [--dest DIR] [--pattern 正则]
    kk publish --dataset user/ds [--from DIR] [--artifact "*.parquet"] [--create]
    kk install-skill [--force]

最近一次 slug 记在 `~/.kk/<cwd-hash>.json`，不污染项目仓库。
