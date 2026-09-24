# 世界书机制与触发条件（SillyTavern 1.18.0 源码实证）

> 2026-09-16 逐行查证本体源码，不抄社区二手说法：
> `public/scripts/world-info.js`（6289 行）、`src/endpoints/characters.js:663`（卡侧转换）、`src/endpoints/worldinfo.js`。
> 凡与社区文档冲突处标 ⚠️ 并附行号。写世界书条目、调触发条件前先翻这篇。
>
> 复核源码：`tar xzf downloads/sillytavern-release.tar.gz -C /tmp/stsrc --wildcards '*/public/scripts/world-info.js' '*/src/endpoints/characters.js'`

## 0. 一次求值的实际顺序（world-info.js:4780-4960）

```
① constant（🔵常数）  → 直接激活，不看关键词（仍占预算）
② sticky 生效中        → 直接激活
③ key 为空             → 跳过
④ 主关键词命中？（任一即可）
⑤ 二级关键词门槛（selective）
⑥ 概率 → 包含组 → 预算 → 插入
```

意思是：**关键词只是一道门**。写「怎么触发」要把 ④⑤⑥ 一起想，不然条目写了等于没写。

## 1. 关键词怎么匹配（matchKeys, :337-366）

| 分支 | 判定 | 行为 |
|:-----|:-----|:-----|
| 正则 | 键字符串是 `/pattern/flags` 形状（`parseRegexFromString`, :2821，正则 `/^\/([\w\W]+?)\/([gimsuy]*)$/`） | 当正则跑，**其余键设置全被覆盖** |
| 纯文本 | 其他一切 | `haystack.includes(key)`，两边按大小写设置转小写 |

- ⚠️ **修正旧记录**：「ST keys 一律当正则、含 `(` `[` `*` 要转义」不成立。卡侧转换函数塞的顶层 `use_regex: true` 引擎**不读**；判定只看字符串形状，不写成 `/…/` 就是纯文本 `includes()`。
- `caseSensitive` 默认 `world_info_case_sensitive = false`（:77）。条目级必须写 `extensions.case_sensitive`——顶层 `case_sensitive` 引擎不读（:5533）。
- `matchWholeWords` 默认 `world_info_match_whole_words = false`（:78）。⚠️ 中文 wiki 写「默认启用」，以源码为准。
  - 开时：多词短语 → `includes`；单词 → `(?:^|\W)(key)(?:$|\W)`（:356）
  - JS 的 `\w` 只有 `[A-Za-z0-9_]`，中文字符算 `\W` → **中文键开全词匹配照样命中**（「店长哥哥」能被「哥哥」命中）。「中文会误伤」的说法对这条规则不完全适用。
- 条目级覆盖同理：`extensions.match_whole_words`；不写就继承用户全局设置。

## 2. selective + secondary_keys（最容易踩的一处）

```js
const hasSecondaryKeywords = (
    entry.selective &&            // 不写/false → 二级键整段被忽略
    Array.isArray(entry.keysecondary) &&
    entry.keysecondary.length
);
```

spec 原文：`secondary_keys` — *ignored if selective == false*。

成立后，二级键是**强制门槛**（主键命中后还要再过一道），不是可选修饰：

| `extensions.selectiveLogic` | 名称 | 语义 |
|:---:|:---|:---|
| 0（默认） | AND_ANY | 主键 **且** 任一二级键 |
| 1 | NOT_ALL | 主键 **且** 至少一个二级键不出现 |
| 2 | NOT_ANY | 主键 **且** 无任何二级键 |
| 3 | AND_ALL | 主键 **且** 全部二级键 |

⚠️ 旧记录写的「1=AND ALL / 3=NOT ALL」是**反的**；源码枚举在 :33-38。

**实务推论：** 只想要「任意一个词就触发」，就**别写 secondary_keys**（或 `selective: false`）；只有真要「A 且 B」才开。

## 3. 扫描范围与深度

- `chat` 传入时**已反序（新→旧）**（`checkWorldInfo` 注释 :4590），`depthBuffer[0]` = 最新一条。扫描范围 = 最近 `depth` **条消息**。
- ⚠️ 飞书社区文档说按「一对（你+角色）」计数 —— 与源码不符，以「条」为准。
- 全局默认 `world_info_depth = 2`（:69）；条目级 `extensions.scan_depth` 覆盖；设 0 则完全不扫聊天（只剩常驻/递归条目）。
- 名称默认参与匹配（`world_info_include_names = true`, :74）→ 扫描区带「角色名: 」前缀。**关键词撞上角色名或用户名 = 每轮都触发**，这是「通用词当键」翻车的主因。

## 4. 位置 / 深度 / 顺序

`world_info_position`（:855-864）：

```
0 before_char | 1 after_char | 2 AN_top | 3 AN_bottom | 4 at_depth | 5 EM_top | 6 EM_bottom | 7 outlet
```

- 卡侧顶层 `position` 只认 `before_char` / `after_char`；**细位置（作者注记前后 / 示例消息前后 / @D / 输出口）必须写 `extensions.position`（数值）** —— `convertCharacterBook:5517` 优先读 `extensions.position`，没有才回落到顶层。
- `extensions.depth`（默认 4，`DEFAULT_DEPTH`）配合 `at_depth`。
- `insertion_order`：数值大 → 插得越靠后 → 对当前对话影响越强。

## 5. 预算

- 引擎用的是 `world_info_budget`（**百分比**，默认 25%）+ `world_info_budget_cap`（绝对 token，0=不生效）。
- ⚠️ **卡里 `character_book` 的 book 级 `scan_depth` / `token_budget` / `recursive_scanning` 在引擎中没有任何读取点**（全仓 grep 实证；转换时只随 `originalData` 留存）。写这些数字是**摆设**，真正生效的是用户侧全局设置。
- 预算耗尽时的保留顺序：常数 > 直接命中 > 递归命中；同级按 order 大者优先。

## 6. 递归（默认关闭）

`world_info_recursive = false`（:75）。条目级：`extensions.exclude_recursion`（不被别人激活）/ `prevent_recursion`（不激活别人）/ `delay_until_recursion`（只在递归轮激活）。全局最大递归步数默认 0 = 只受预算约束。

## 7. 其它可写字段（一律在 `entry.extensions`）

| 字段 | 默认 | 说明 |
|:-----|:-----|:-----|
| `probability` / `useProbability` | 100 / true | 触发概率 |
| `group` / `group_weight` / `group_override` / `use_group_scoring` | '' / 100 / false / false | 包含组（同组只留一条） |
| `sticky` / `cooldown` / `delay` | null | 定时效果（单位=消息条数，0=无效果） |
| `triggers` | [] | 按生成类型过滤（普通/继续/角色扮演/滑动/重生成/静默） |
| `outlet_name` | '' | 输出口，配合 `{{outlet::名称}}` |
| `match_character_description` / `match_character_personality` / `match_character_depth_prompt` / `match_scenario` / `match_persona_description` / `match_creator_notes` | false | 附加匹配来源：卡字段本身也能当触发源 |
| `vectorized` | false | 向量匹配（需扩展，且不可预测） |
| `ignore_budget` | false | 无视预算上限（慎用） |

## 8. 角色过滤器：卡内书做不到

条目级 `characterFilter: { isExclude, names, tags }` 确实存在（:1355），但 `convertCharacterBook` 从头到尾**没有读取它** →
**卡内嵌的 `character_book` 无法按角色过滤；只有独立世界书 `.json` 可以。**

这是「共用世界书走独立文件、卡用 `extensions.world` 指名字关联」的硬理由，不是风格偏好。

## 9. 设计世界书时的检查单

1. 触发词用**专有名词**（机构名 / 地名 / 术语名），别用「店长」「老师」「哥哥」这类通用词——名称参与扫描、跨卡也会撞。
2. 只有真要「A 且 B」才开 `selective` + `secondary_keys`；否则一律关。
3. 想控扫描深度/预算 → 写条目级 `extensions`，别指望 book 级字段。
4. 要按角色分流 → 只能放独立世界书，卡内书做不到。
5. 位置：卡侧顶层只能 before/after；要 @D / 作者注记 / 输出口，走 `extensions.position`。
6. **order 是全局排序键，跨书会打架**：卡内书与共用世界书同时加载时，预算截断按 order 降序保留——想把「角色关系」排在「世界背景」之前，就把共用书的 order 段位压在卡内书之下（例：卡内书 300-390，共用书就压到 100-179）。同 position 桶内才比强弱，但**预算优先级不看桶、只看 order**。

## 10. 求值全流程（源码顺序，伪代码）

```
chatForWI = coreChat.map(x => include_names ? `${x.name}: ${x.mes}` : x.mes).reverse()  # 新→旧
buffer       = WorldInfoBuffer(chatForWI)          # depthBuffer[i] = 第 i 近的一条
sortedEntries= getSortedEntries()                  # order 降序（sortFn = (a,b)=>b.order-a.order）
budget       = round(world_info_budget% × maxContext) || 1，再被 budget_cap 截断

每一轮扫描（初始 / 递归 / 最小激活数）：
  for entry in sortedEntries:                      # order 大的先处理
      disabled → skip；constant → 激活；sticky 生效中 → 激活；没 key → skip
      text = 最近 (entry.scan_depth ?? 全局 depth) 条消息
      主键 = key.find(k => matchKeys(text, k))；没命中 → skip
      selective && keysecondary → 过 selectiveLogic 闸，不过 → skip
  排序：sticky 优先，其余按 sortedEntries 顺序
  filterByInclusionGroups(...)                     # 同组只留一条（组权重随机 / group_override 取 order 最大 / use_group_scoring 按命中数）
  for entry in 上面顺序:
      概率检查（useProbability=false 或 probability=100 直接过；sticky 不重掷）
      content += entry.content + '\n'
      非 ignore_budget 且 (已累计 token) ≥ budget → 置 overflowed，之后非 ignore_budget 全丢

组装：allActivatedEntries 按 order 降序遍历 → 按 position 分桶 unshift
      → 桶内最终顺序 = order 升序（order 小的在前，大的紧贴字符定义、影响更强）
      桶序：before_char → after_char → EM_top/EM_bottom → AN_top/AN_bottom → at_depth(按 depth+role 合桶) → outlet(宏取用)
```

## 11. 模拟器 / CLI

`bin/tavern wb`（底层 `scripts/worldbook_tools.py`，复刻上面这套判定链）：

```bash
tavern wb ls    角色.json        # book 级参数 + 条目表（order / position / 常驻 / 二级键 / token）
tavern wb check --all            # 体检：二级键门槛 / 通用词主键 / 跨卡撞车 / 摆设字段 / 预算
tavern wb keys  --all            # 关键词矩阵（谁跟谁撞）
tavern wb sim   角色 "学姐，那批新装备做好了吗" --depth 2 --prev "上一条" --book 世界书.json
tavern wb new    清单.json --out my-world.json   # 从条目清单生成独立世界书
tavern wb selftest               # 机制判定自测（37 项，改代码后必跑）
tavern wb unfilter 卡.json --fix  # 去 selective 隐形门槛：二级键分流（并入主键／泛词剔除）+ 推版本
```

**两种书都能读**：`ls/check/keys` 自动识别是卡内 `character_book` 还是独立世界书（条目字段形状不同：卡侧 `keys`/`insertion_order`/字符串 position；WI 侧 `key`/`order`/数值 position）。`sim --book` 可把卡内书与独立书挂在一起跑，位置桶分列（`before_char` / `after_char`）。

`sim` 的价值：把「我以为会触发」变成「实际会不会」。实测例：

```
── 触发 1 条（含常驻）──
   ✅ [0] ord=300 after_char 某角色=最亲近的人  ← 常驻
── 主键命中但被二级键拦住 1 条 ──
   ⛔ [1] 某角色 主键 '学姐' 命中，但 AND_ANY 未满足（二级 <专有名词>，命中：无）
```

**模拟器的边界（别当保证）：** 概率是真随机；定时效果（sticky/cooldown/delay）依赖聊天状态；包含组按权重随机；宏只替换 `{{user}}`/`{{char}}`；Python 正则 ≈ 但 ≠ JS 正则；向量匹配不模拟。
