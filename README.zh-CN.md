# RoboRadar

[English](README.md) | **简体中文**

RoboRadar 是一个本地运行、带网页界面的机器人领域情报工具。它每天定时从 arXiv cs.RO 中按自定义关键词筛选新论文，并生成论文总结日报；同时追踪关注的机器人领域top公司与研究者在 YouTube、GitHub、官网和个人主页上的最新工作和博客等，支持导出与推送。

![RoboRadar 界面：论文日报（左）与圈内动态（右）](docs/images/overview.png)

## 安装

### 0. 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Linux（已在 Ubuntu 24.04 上测试）。macOS 可以运行，但开机自启要自己配置；Windows 只能手动启动 |
| Python | 3.10 及以上（推荐 3.12） |
| 网络 | 能访问 arxiv.org、youtube.com、github.com。国内网络一般需要代理，可以用系统代理、TUN 模式，或在设置里填 HTTP 代理 |
| 总结模型（四选一） | ① 本机已登录的 Claude Code（`claude` 命令，默认）；② Anthropic API Key；③ OpenAI 兼容接口（DeepSeek、Qwen、Kimi 等）的 Key；④ 不使用 LLM（只做规则提取，没有中文总结） |

### 1. 获取代码

```bash
git clone <仓库地址> robo-radar     # 也可以直接把整个目录拷过来
cd robo-radar
```

下文所有命令都在 `robo-radar/` 目录下执行。

### 2. 创建 Python 环境并安装依赖

**方式 A：conda（推荐）**

```bash
conda create -n roboradar python=3.12 -y
conda activate roboradar
pip install -r requirements.txt
```

**方式 B：venv**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

装完检查一下：

```bash
python -c "import fastapi, apscheduler, feedparser, pymupdf; print('依赖 OK')"
```

> `run.sh` 和服务安装脚本默认使用 `~/miniconda3/envs/roboradar/bin/python`。如果你的 Python 在别处（比如 venv 或 anaconda3），请先设置环境变量：
> ```bash
> export ROBORADAR_PYTHON=$(which python)   # 在已激活的环境里执行
> ```

### 3. 准备总结模型（LLM）

根据你的情况任选一种。启动后可以在网页「定时与设置 → 总结模型」里随时切换，并点「测试连接」检查。

**① 本机 Claude Code（默认，不需要 API Key）**

按 [Claude Code 官方文档](https://code.claude.com/docs) 安装后登录一次，然后确认命令行可用：

```bash
claude -p "回复 OK"        # 能输出内容就说明可用
```

模型名可以填 `sonnet`（默认）、`opus` 或 `haiku`。

**② Anthropic API**

在设置页选择「Anthropic API」并填入 API Key，或者在启动前设置环境变量 `ANTHROPIC_API_KEY`。默认模型是 `claude-opus-5-5`，可以改。

**③ OpenAI 兼容接口（DeepSeek / Qwen / Kimi 等）**

在设置页填 Base URL、模型名和 Key。以 DeepSeek 为例：`https://api.deepseek.com`，模型 `deepseek-chat`。

**④ 不使用 LLM**

选「不使用 LLM」。标签直接用命中的关键词主题；通讯作者和单位按规则从 PDF 首页提取；方法和结果取摘要原句。

### 4. 第一次启动

```bash
./run.sh            # 默认端口 8770，启动后会自动打开浏览器
./run.sh 9000       # 换端口
```

浏览器访问 **http://127.0.0.1:8770**。第一次启动时程序会：

- 在 `data/` 下创建设置文件 `settings.json` 和数据库 `roboradar.db`，并写入默认关键词主题和默认关注列表；
- 如果当天的 arXiv 定时点已过，就自动补跑一次论文日报，同时抓取一次关注列表。页面顶部会显示进度条，大约 1～2 分钟完成。

按 `Ctrl+C` 可以停止前台运行。

### 5. 在网页里完成初始配置

1. **⚙️ 定时与设置**
   - 设置 arXiv 日报的运行时间（默认 09:30）。arXiv 大约在北京时间 08:00（夏令时）/ 09:00（冬令时）发布新公告，建议设在这之后。
   - 设置关注列表的抓取时间（默认 08:00、12:30、20:00），可以添加多个。
   - 选择 LLM 后端，然后点「🔌 测试连接」。
   - 可选：配置推送（飞书、钉钉、企业微信、Slack Webhook，或 SMTP 邮件），点「📨 发送测试通知」验证。
   - 可选：如果访问 YouTube / arXiv 不稳定，填写 HTTP 代理（如 `http://127.0.0.1:7897`）。
   - 可选：填写 GitHub Token，把 API 限额从每小时 60 次提高到 5000 次。
   - 点「💾 保存设置」，定时任务立即按新时间生效。
2. **🏷️ 关键词主题**：每个主题是一个中文标签加一组英文关键词，每行写一个。
   - 「筛选 + 标签」主题：命中就收录进日报。
   - 「仅标签」主题：只用来给论文打标签。
   - 关键词写成 `a + b`，表示两个词都必须出现。
   - 改完点「保存并重新匹配最近一天」，可以立刻看到效果。
3. **👥 关注列表**：点「＋ 添加关注」，填写名称、类型，再粘贴一个或多个网址。程序会自动识别网址类型，可以先点「🔍 试抓」预览抓到的内容。支持的网址格式：

   | 写法 | 抓取内容 |
   |---|---|
   | `https://www.youtube.com/@figureai` | 频道最新视频 |
   | `https://github.com/qiayuanl`（用户或组织） | 新建仓库 |
   | `https://github.com/owner/repo` | Release；没有 Release 时取最新提交 |
   | `https://openai.com/news/rss.xml` | 任意 RSS / Atom，也可以用 RSSHub 订阅 B 站、X 等 |
   | 官网新闻页、博客、个人主页 | 先自动寻找页面上的 RSS；找不到就检测页面上新增的论文或文章链接 |
   | `arxiv-author:Qiayuan Liao` | 该作者在 arXiv 上的新论文 |
   | `arxiv-author:Chong Zhang\|cs.RO` | 同上，并限定 cs.RO 分类（同名作者多时使用） |

   OpenAI、Tesla 这类综合性公司建议填写「过滤关键词」（如 `robot, humanoid, optimus`），只保留和机器人相关的动态。

### 6. 安装为后台服务（开机自启，推荐）

定时任务由程序自带的调度器执行，所以**程序必须一直在运行**。在 Linux 上可以一键安装成 systemd 用户服务：

```bash
# 如果第 4 步的前台程序还开着，先 Ctrl+C 停掉，避免端口冲突
./scripts/install_service.sh          # 默认端口 8770；换端口：./scripts/install_service.sh 9000
```

这个脚本会：

- 写入 `~/.config/systemd/user/roboradar.service`，并设置为登录后自动启动；
- 在应用菜单里添加「RoboRadar 机器人雷达」快捷方式，点开就在浏览器里打开界面。

验证服务状态：

```bash
systemctl --user status roboradar      # 应显示 active (running)
curl -s http://127.0.0.1:8770/api/status | head -c 300
```

如果希望**不登录桌面也照常运行**（比如放在服务器上），再执行一次：

```bash
loginctl enable-linger $USER
```

> **不使用 systemd 的替代方案**
> - 用 tmux 或 screen 长期运行 `./run.sh`；
> - 或者不启动网页服务，只用 cron 定时调用命令行（结果仍然写入数据库，之后打开网页就能看到）：
>   ```cron
>   30 9 * * *        cd /path/to/robo-radar && $HOME/miniconda3/envs/roboradar/bin/python -m roboradar arxiv
>   0 8,12,20 * * *   cd /path/to/robo-radar && $HOME/miniconda3/envs/roboradar/bin/python -m roboradar watch
>   ```

### 7. 日常管理、更新与卸载

```bash
journalctl --user -u roboradar -f          # 查看实时日志
systemctl --user restart roboradar         # 更新代码后重启（只改了 static/ 下的前端文件时，刷新浏览器即可）
systemctl --user stop roboradar            # 临时停止

# 卸载服务（不会删除数据）
systemctl --user disable --now roboradar
rm ~/.config/systemd/user/roboradar.service ~/.local/share/applications/roboradar.desktop
systemctl --user daemon-reload
```

更新依赖：`pip install -r requirements.txt --upgrade`。

---

## 使用说明

### 界面

| 页面 | 功能 |
|---|---|
| 📄 论文日报 | 切换日期；回溯最近 5 个公告日；按标签或“有实机实验”筛选；搜索、排序；收藏、标记已读、重新总结；勾选“显示当天全部”后，可把漏筛的论文一键 ➕收录；导出 Markdown / HTML；手动推送 |
| 📺 圈内动态 | 按关注对象、类型（视频 / 新仓库 / 发布 / 博客 / 论文 / 网页更新）和时间筛选；新内容带 NEW 标记；一键全部已读；导出当日动态 |
| ⭐ 我的收藏 | 收藏的论文和动态；搜索全部历史论文 |
| 👥 关注列表 | 管理公司、研究者、实验室及其网址；单独抓取某个对象；查看每个源最近一次是否抓取成功 |
| 🏷️ 关键词主题 | 管理关键词主题；开关 LLM 语义补筛（让 LLM 从没命中关键词的论文里再挑出相关的） |
| ⚙️ 定时与设置 | 运行时间、arXiv 分类、LLM、推送、代理、GitHub Token |
| 🧾 运行记录 | 每次任务的结果，以及失败的源和原因 |

### 命令行

```bash
python -m roboradar serve [--port 8770] [--open]   # 启动网页界面和定时任务
python -m roboradar arxiv                          # 立即汇总最新公告日
python -m roboradar arxiv --date 2026-10-23        # 汇总指定公告日（recent 页只保留最近 5 个）
python -m roboradar arxiv --force                  # 按当前关键词重新匹配，并重新总结
python -m roboradar watch                          # 立即抓取一次关注列表
```

### 工作流程

- **论文日报**：
  1. 从 recent 页取最新公告日的论文列表（失败时改用 RSS）；
  2. 通过 arXiv API 批量获取标题、摘要、作者和 comments；
  3. 用关键词匹配；
  4. 可选：LLM 语义补筛；
  5. 对相关论文下载 PDF 首页（PDF 过大时改用 HTML 版），解析作者、单位和通讯标记；
  6. LLM 生成中文标题、标签、实验平台、通讯作者、单位、方法和结果；
  7. 写入数据库，生成报告，并推送。
- **圈内动态**：逐个抓取已启用的源，把新条目写入数据库（只收录最近 N 天发布的内容，N 可在设置里改），再用一次 LLM 请求为所有新动态生成一句话中文摘要。
- **补跑**：程序启动时，如果当天的 arXiv 定时点已过且还没成功运行过，就补跑一次；关注列表超过 6 小时没抓取也会补跑。定时任务只在有新内容时才推送，周末不会重复推送旧日报。

### 数据位置

全部数据保存在 `data/` 目录下（可以用环境变量 `ROBORADAR_DATA` 指定其它目录）。要备份，拷贝整个 `data/` 即可。

| 文件 | 内容 |
|---|---|
| `data/settings.json` | 全部设置和关键词主题 |
| `data/roboradar.db` | SQLite 数据库：论文、关注对象、动态、运行记录 |
| `data/reports/arxiv-YYYY-MM-DD.{md,html}` | 每日论文报告 |
| `data/reports/watch-YYYY-MM-DD.md` | 每日动态报告 |

---

## 常见问题

**页面顶部提示“未找到公告日的论文”？**
arXiv 周末和节假日不发布新论文，属于正常情况。可以用「📅 回溯其它日期」查看前几天的论文。

**论文卡片上显示“规则提取”或“LLM 失败”？**
说明 LLM 没有正常工作。到设置页点「测试连接」，查看具体报错。如果使用 Claude Code 后端，并且以服务方式运行，需要确认 `claude` 在 `~/.local/bin`、`/usr/local/bin` 或 `/usr/bin` 里；服务的 PATH 只包含这几个目录，可以在 `install_service.sh` 里修改。修好后在卡片上点 🔄，可以单篇重新总结。

**某个关注源显示红点？**
把鼠标悬停在红点上可以看到错误原因，「🧾 运行记录」里也有。常见原因：
- GitHub 限额用尽：填写 GitHub Token。对个人账号，程序会自动改用公开动态订阅兜底。
- 网站需要 JS 渲染或有反爬验证：改用该网站的 RSS 或 YouTube 频道。
- 网络不通：在设置里填写代理。

**YouTube 抓不到或很慢？**
YouTube 官方 RSS 经常返回错误，程序会自动改为解析频道页面。频道页面给出的是“3 天前”这类相对时间，所以视频发布日期是近似值。如果一直失败，请检查代理。

**个人主页的内容为什么没有出现在动态里？**
个人主页没有发布日期，程序只能通过“新增链接”来发现新内容。第一次抓取时记录下来的已有链接不算新动态，默认隐藏，勾选「含主页现有内容」或按关注对象筛选就能看到。

**想监控 cs.RO 以外的分类？**
在设置页的「arXiv 分类」里填多个分类，例如 `cs.RO, cs.CV, cs.LG`。

**LLM 费用大概多少？**
每天只有命中关键词的论文才会调用 LLM（默认最多 40 篇，可设置）。每篇一次调用，约 3k 输入 token、300 输出 token。动态摘要每 25 条合并成一次调用。

---

## 代码结构

```
robo-radar/
├── run.sh                    前台启动
├── scripts/install_service.sh  安装 systemd 用户服务和应用菜单快捷方式
├── requirements.txt
├── docs/images/              README 效果图
├── data/                     运行数据（自动创建，不进版本库）
└── roboradar/
    ├── __main__.py           命令行入口
    ├── app.py                FastAPI 接口
    ├── config.py             设置读写、默认关键词主题
    ├── db.py                 SQLite 表结构、默认关注列表
    ├── arxiv.py              recent 页 / RSS / API 抓取，PDF 首页解析，关键词匹配，规则提取
    ├── digest.py             论文日报流程与 LLM 提示词
    ├── sources.py            YouTube / GitHub / RSS / 网页 / arXiv 作者抓取器
    ├── watch.py              关注列表抓取流程与动态摘要
    ├── llm.py                LLM 后端（Claude CLI / Anthropic / OpenAI 兼容）
    ├── report.py             Markdown / HTML 报告
    ├── notify.py             桌面通知 / Webhook / 邮件
    ├── scheduler.py          定时任务与补跑
    └── static/               前端（原生 HTML/CSS/JS，无需构建）
```
