---
name: sillytavern-worldbook
tier: T2  # T分级: T2=直接做 / T1=先请示 / T0=一律拒
description: SillyTavern 世界书（Lorebook/character_book）机制——触发链源码实证、共用世界观设计、独立书生成与触发模拟。触发：世界书、lorebook、character_book、共用世界观、触发条件、条目不触发。
---

# 酒馆世界书 · SillyTavern Worldbook

> 2026-09-16 从 `sillytavern-cards` 拆出来的独立线：卡是「一个人」，世界书是「一个世界」——
> 书的形状、触发条件、分发口径都不一样，工具也就该独立。写卡本体（PList/Ali:Chat/格式）仍在 `sillytavern-cards`。
> **已对外**：<https://github.com/feverZHONG/liya-sillytavern-worldbook>（2026-09-24 建仓，脱敏后发布）

## 触发

世界书 / lorebook / character_book / 共用世界观 / 触发条件 / 关键词不触发 / 条目不上

## 一、先分清两种书

| | 卡内 `character_book` | 独立世界书 `.json` |
|:--|:--|:--|
| 随卡走 | ✅ 导出时自动嵌入 | ❌ 要单独给 |
| 多卡共用 | ❌ 复制 N 份 | ✅ 一本 |
| 按角色过滤 `characterFilter` | ❌ 引擎不读（源码实证） | ✅ |
| 关联方式 | 卡数据里 | `extensions.world = 文件名`（**文件名必须 ASCII**） |

## 二、CLI：`wb`

> 引擎是 `scripts/worldbook_tools.py`（纯标准库、无依赖）。`wb` 是本机收口短名，等价于 `python3 scripts/worldbook_tools.py`。

```bash
wb ls       <文件...|--all>       # 世界书台账（自动识别卡内书 / 独立书）
wb check    <文件...|--all>       # 触发体检：二级键门槛/通用词主键/跨卡撞车/摆设字段/预算
wb keys     <文件...|--all>       # 关键词矩阵（谁跟谁撞）
wb sim      <卡> "文本" [--depth N] [--prev T] [--book 书.json]   # 触发模拟（双书同跑）
wb new      <清单.json...> [--out 书.json]     # 条目清单 → ST 格式独立书（可给多份分层清单，合并成一本）
wb unfilter <卡...> [--fix]       # 去 selective 隐形门槛（二级键分流）
wb selftest                       # 判定链自测（改代码后必跑）
```

`bin/tavern wb ...` 旧写法保留（本机壳，原样转发）。

`ls`／`keys`／`check`／`sim` 全套已适配 **`characterFilter` 分流**：

- `ls` 有「→ 生效卡」列（`全体` 或名单），书级给分流组数
- `keys` 认分流：同键分流到不同卡判 **`✔ 隔离`**（不会同场触发），`scope` 有交集才算 **`⚠️ 同册／跨卡`**
- `check` 的撞车只报**真撞车**（隔离的不计）；给 `--card-dir 卡库目录` 还能校验 **`names` 断链**（卡改名即断链，🔴 计入硬问题）
- `check` 的预算不再拿整册合计吓人：有分流就报「单卡最大 N 条 / X token」，整册合计单独标「不代表单卡注入」
- `check`／`sim` 名单外条目标 `filtered` 跳过
- `wb new` 可给**多份清单**（`wb new 世界清单.json 关系清单.json --out 书.json`）——分层维护、合并成一本

判据：**别拿整册合计当单卡注入**（本项目整册 3718 token，单卡实际命中 0-212）。

⚠️ **加选项要改两处**：`bin/wb` 是壳（自己的 argparse ＋ `cmd_*` 转发），引擎在 skill 里——新选项得同时加进壳的参数表、壳的转发、引擎的 parser，三处齐了才算通（这次 `--card-dir` 就是先在壳上撞了一次 unrecognized arguments）。

## 三、写书前必须知道的硬规则（每条都在源码里核过）

1. **触发是四道门**：`constant/sticky` → 主键 → 二级键(`selective`) → 概率·包含组·预算。只看关键词会写废。
2. **`selective` + `secondary_keys` 是强制门槛**（默认 AND_ANY = 主键且任一二级键）。想要「任一词触发」就别写 `secondary_keys`。
3. **只用专有名词做键**。通用词（店长/老师/过进/责任/决定…）会撞角色名与用户名——名称默认参与扫描，等于常年误触发。
4. **`order` 是全局排序键**：独立书与卡内书同时加载时，预算截断按 order 降序保留 → 共用书段位要压在卡内书之下（例：卡内 300-390，共用书 100-179）。手写书整理完可用官方 **Apply Current Sorting**（1.19 起支持起始值/步长/升降序，只动当前一本书）一把赋号——脚本生成的书别覆盖，详见机制文档 §13。
5. **卡内 book 级 `scan_depth` / `token_budget` / `recursive_scanning` 引擎不读**，是摆设；要控就写条目级 `extensions`（卡侧）或让用户在酒馆设（独立书）。
6. **递归默认关** → 条目内容里提到别的关键词不会连锁触发，可以放心互引名词。
7. **世界书写设定，不写台词本**。台词归 `description` 的 Ali:Chat；世界书全写成对话 = 触发时像插播历史消息（v0.8 教训）。
8. **条目级字段只有 `extensions.*`（卡侧）/ 驼峰顶层（独立书）生效**：卡侧顶层 `case_sensitive`、`use_regex` 引擎都不读。

## 四、写书的写法规范（小样复盘定案）

1. **不写标题行** —— `comment` 不参与注入，content 里再写一遍「【地名】」等于双倍浪费。
2. **首句自报家门**（「XX 是…」这类句式）——AI 一眼知道这段在讲什么，不需要标题。
3. **只留事实**：是什么 / 什么状态 / 和谁有关。砍掉抒情（「形成鲜明对比」「不惜任何牺牲」）与重复修饰。
4. **不复述关键词** —— 条目已经被关键词命中了，不必再强调。
5. 目标 **90-110 字/条**；平铺设定为主，不做台词本（台词归 `description` 的 Ali:Chat，v0.8 教训）。

实测对照：同一对条目按这五条重写，**423 → 247 token（-42%）**，信息一条没丢。

## 五、设计一本共用世界书

要点：**只装「世界」，不装「人」**——机构 / 地点 / 事件 / 术语进共用书；人物关系留在各自卡内书。
好处一次解决三件事：跨卡撞车归零、通用词风险归零、角色分流不需要（因为不装人）。

实测尺度（一个 16 角色项目）：公开层 **13 条**、**0 常驻**、全书约 **1600 token**（单条 90-110 字）——单卡一轮通常只命中 1-3 条（200-700 token），预算安全。

## 六、把书挂到卡上（接卡）

独立书建好**不会自动生效**——每张要用它的卡都要写一个字段：

（本机 CLI 收口；等价操作＝直接读写卡 JSON 的 `data.extensions.world`）

```bash
tavern world --all                                    # 体检：每张卡现在关联哪本书
tavern world --all --set my-world --fix   # 批量写入
```

- 字段位置**源码实证**：`data.extensions.world`（官方 `charaFormatData` 默认值 `''`，值＝书文件名不含 `.json`）。顶层 `extensions` 不参与读写，别往那儿写。
- 值必须**与文件名逐字一致**（ASCII）；中文名导入后被转成乱码 → 断链。
- 接卡是**批量改卡**：先改 1 张跑 `tavern verify` 验证，再 `--all`；改完做**结构化比对**确认「只多了 world 一个字段」（`git diff` 数行数不算证据——末尾换行规范化也会多出 2 行）。
- 实际加载 = 卡内 `character_book` **+** `extensions.world` 指的书，两本一起；验收用 `wb sim <卡> "文本" --book 书.json` 同跑。
- **分发**：独立书与卡要**一起给**。单卡分享时卡内书仍完整，但少了世界层。

## 七、引用

| 要查什么 | 打开 |
|:---------|:-----|
| **机制主档**（1.19.0 源码实证，带行号）：调用链五层 / 判定链 / 匹配 / 扫描源 / 位置 / 预算 / 字段 / 伪代码 / CLI | `references/12-worldbook-mechanics.md` |
| **机制附档**（非常规路径）：递归 / 包含组·outlet·向量化 / 角色过滤器 / 扫描状态机·时间效果 / 版本复核记录 | `references/12b-mechanics-advanced.md` |
| **条目设计规范**（动笔前读：靠什么进 / 放哪 / 要不要恒在场 + 硬判据 + 实测记录；**§六 关系层两套架构**＝A 卡内书〔单卡面〕／B 独立关系书〔成套〕，按分发形态选） | `references/13-entry-design.md` |
| 引擎实现（判定链 / 模拟器 / 生成器 / 自测） | `scripts/worldbook_tools.py` |
| **上游发新版后复核机制**（一条命令出差异判定） | `scripts/st-version-diff.py --tag-a X --tag-b Y` |
| 卡内书的字段位置与写卡侧 | `sillytavern-cards` skill |
| 卡库台账与精修流程 | `tavern-card-refinement` skill |

## 八、踩坑

- **卡本体 ≠ 只看顶层字段**：PList 在 `data.extensions.depth_prompt.prompt`（`data.depth_prompt` 是空格），`personality`／`scenario`／`mes_example` 生来就是空的（内容进了 PList 与 description）。做四道闸减法（闸①「卡本体已经写了吗」）之前**必须先读 PList**——不然会把「已写」误判成「没写」，反过来把重复内容写进世界书。
- **卡内书 book 级必带 `extensions: {}`**：V2 规范要求 `character_book` 同时有 `extensions`（对象）与 `entries`（数组）。新建卡内书只写 `entries` 会让官方 validator 判 `data.character_book.extensions/entries` 失败——`tavern verify` 一跑就抓（踩过）。
- **单字／通用词键要拿真句子试**：`唯` 会撞「唯一」、`姐姐`／`母亲`／`父亲` 会撞任何家庭话题——`wb sim <卡> "句子"` 实测再定；本体角色名当键＝隐性常驻（扫描区每轮带「名字: 」前缀），见 §三 硬判据 1。
- **中文文件名**：文件名就是关联键，中文名导入后会被转成一串乱码 → `extensions.world` 断链。一律 ASCII。
- **Python `\w` ≠ JS `\w`**：JS 只认 `[A-Za-z0-9_]`，中文字符算 `\W`。全词匹配边界照搬 `\W` 会让中文关键词与酒馆行为不一致（`wb selftest` 已覆盖）。
- **正则判定看字符串形状**（`/pattern/flags`），不是看 `use_regex` 开关。
- **搬动脚本后**：`bin/tavern wb` 是转发壳，真实现在本 skill；改引擎只改一处。
