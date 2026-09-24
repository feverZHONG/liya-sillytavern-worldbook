# 莉娅的酒馆世界书方法 · SillyTavern Worldbook

> SillyTavern 世界书（Lorebook / `character_book`）的机制实证与工具链——判定链逐行核过源码，工具能体检、能模拟触发、能生成独立书。
> 技术口径全部以官方仓库 [SillyTavern/SillyTavern](https://github.com/SillyTavern/SillyTavern)（1.18.0）为 ground truth。

## 这是什么

世界书最容易踩的坑是「关键词明明写了，它就是不上」。这个仓库解决的就是这类问题——**先把机制查到源码，再谈怎么写**。

覆盖这些事：

- **机制** —— 四道触发门的完整判定链（带 `world-info.js` 行号）：主键匹配 → `selective` 二级键门槛 → 概率 / 包含组 → 预算截断
- **两处反直觉的实证** —— `selective: true` + `secondary_keys` 是**强制门槛**（不是可选修饰，默认 AND_ANY）；卡内 `character_book` 的 `characterFilter` 引擎**根本不读**
- **两种书的分界** —— 卡内嵌书 vs 独立 `.json`：多卡共用、按角色分流，只有独立书做得到
- **写法** —— 不写标题行 / 首句自报家门 / 只留事实 / 90-110 字一条（同一对条目按这五条重写，实测 423 → 247 token）
- **设计** —— 多卡共用世界书的边界怎么切：只装「世界」不装「人」（机构 / 地点 / 事件 / 术语进共用书，人物关系留在各自卡内书）
- **工具** —— 台账 / 触发体检 / 关键词矩阵 / 触发模拟 / 生成独立书 / 去隐形门槛 / 37 项自测
- **接卡** —— 独立书怎么关联到卡（`data.extensions.world` 的字段位置是查官方 `charaFormatData` 定的，写在顶层不生效）

## 怎么装

```bash
git clone https://github.com/feverZHONG/liya-sillytavern-worldbook.git ~/.hermes/skills/sillytavern-worldbook
```

纯 Python 标准库，无需安装依赖。token 统计若装了 `tiktoken` 会用同口径精确值。

文档里出现的 `wb …` 是作者本机给这个 CLI 起的短名。直接从这个仓库用，加个别名即可：

```bash
alias wb='python3 ~/.hermes/skills/sillytavern-worldbook/scripts/worldbook_tools.py'
```

## 快速上手

```bash
# 1. 看一本世界书里都有什么（自动识别卡内书 / 独立书 / 条目清单三种形状）
wb ls 你的卡.json
wb ls --all

# 2. 触发体检：哪些条目不会按你预期触发
wb check --all     # 二级键门槛 / 通用词主键 / 跨卡撞车 / 摆设字段 / 预算占用

# 3. 关键词矩阵：谁跟谁撞
wb keys --all

# 4. 触发模拟：把「我以为会触发」变成「实际会不会」
wb sim 你的卡.json "公主岛那边有消息吗" --book 世界书.json

# 5. 从条目清单（可读可改的 JSON）生成独立世界书
wb new 清单.json --out my-world.json

# 6. 改过引擎代码 → 必跑自测（37 项，对拍官方判定链）
wb selftest
```

体检会点出这几类问题，都是源码里核过的：

| 症状 | 真相 |
|:-----|:-----|
| 条目怎么都不触发 | 写了 `secondary_keys` 但 `selective` 没开 → 整段被忽略；开了 → 二级键变强制门槛 |
| 条目天天乱触发 | 主键用了通用词（「老师」「过去」「决定」）——角色名与用户名默认参与扫描 |
| 多卡互相打架 | 主键跨卡撞车；`order` 是**全局**排序键，预算截断时按它降序保留 |
| 调了深度/预算没用 | 卡内 book 级 `scan_depth` / `token_budget` / `recursive_scanning` 引擎没有读取点，是摆设 |

## 目录

```
SKILL.md                                实用口径：两种书的分界 / 硬规则 / 写法规范 / 共用书设计 / 接卡
references/12-worldbook-mechanics.md    机制全解析（ST 1.18.0 源码实证，带行号）
scripts/worldbook_tools.py              引擎：台账 / 体检 / 矩阵 / 模拟 / 生成 / 去门槛 / 自测
```

## 姊妹仓库

- [liya-sillytavern-cards](https://github.com/feverZHONG/liya-sillytavern-cards) —— 写卡本体（V2/V3 格式规格、PList + Ali:Chat 写法、三个工具）；本仓库管「世界」，那边管「人」
- [liya-persona-authoring](https://github.com/feverZHONG/liya-persona-authoring) —— 给 AI agent 写它**自己**的身份文件（跟写卡规则相反，别混用）
- [liya-subtraction-skill](https://github.com/feverZHONG/liya-subtraction-skill) —— 技能库精简与维护
- [liya-chat-game-referee](https://github.com/feverZHONG/liya-chat-game-referee) · [liya-spy-game](https://github.com/feverZHONG/liya-spy-game) · [liya-sea-turtle-soup](https://github.com/feverZHONG/liya-sea-turtle-soup) —— 聊天里能玩的三件（回合制裁判引擎 / 谁是卧底 / 海龟汤）
- [liya-vision-recognition-traps](https://github.com/feverZHONG/liya-vision-recognition-traps) —— 视觉模型识图陷阱：19 条实测陷阱 + 真 OCR 通道 + AI 生图物理体检 + 两图差分

## 提思路 / 提修正

- 新的机制发现、踩坑 → 开 [Issue](https://github.com/feverZHONG/liya-sillytavern-worldbook/issues)，说清场景（什么书、什么模型、出现什么现象）
- 想直接改 → Fork + PR，改动请写清「为什么（源码哪一段支持这个结论）」

## 许可

MIT（方法与工具）。
