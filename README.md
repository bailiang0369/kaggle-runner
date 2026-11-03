# kaggle-runner

CodeBuddy / Claude 的 Skill：把任意 Python 项目推到 GitHub、在 Kaggle 上跑指定 py 入口、
跟进状态、拉产物并发布为 dataset。附带工具 `kk`（单文件、零第三方依赖）。

## 安装

整个仓库就是 skill 目录，直接 clone 到 skills 路径即可，**不需要跑任何安装命令**：

```bash
git clone https://github.com/bailiang0369/kaggle-runner ~/.codebuddy/skills/kaggle-runner
```

Windows 想放在别处，用目录联接：

```powershell
git clone https://github.com/bailiang0369/kaggle-runner e:/code/kaggle-runner
mklink /J "$env:USERPROFILE\.codebuddy\skills\kaggle-runner" e:/code/kaggle-runner
```

装完重启 CodeBuddy 或新建会话生效。

## 使用

```bash
python scripts/kk.py --help

# 一条龙：推 GitHub 代码 → Kaggle 跑 → 拉日志 → 收产物 → 发 dataset
python scripts/kk.py run --entry orderbook/fusion_kl.py --env-from CHD_API_KEY --dataset-out me/xxx
```

详细参数见 `references/cli.md`，报错处理见 `references/troubleshooting.md`。

## 目录

```
kaggle-runner/
├── SKILL.md                     # 给 AI 的流程与硬约束（触发后加载）
├── references/
│   ├── cli.md                   # kk 全量参数
│   └── troubleshooting.md       # 报错对照 + 长任务检查
└── scripts/kk.py                # 唯一源文件
```

## 设计要点

- **包里没有任何项目信息**：仓库地址从 `git remote` 推断，slug 从 title 推断，其余全是命令行参数。
- **driver 由工具临时生成**：Kaggle 只接受一个 `code_file`，所以 clone 模式会生成一个约 25 行的 driver，
  在 kernel 内 `git clone` 到 `/tmp`（不是 `/kaggle/working`，否则 output 会连代码一起打包下载）。
- **凭据走 `--env-from` 注入私有 kernel**：Kaggle Secret 是 kernel 设置，`kernels push` 会覆盖它；
  写进 code_file 反而不会丢，这是全自动链路能成立的前提。
- **默认 CPU**：不带 `--gpu`，不会误占 GPU 配额。
