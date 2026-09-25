# 世界书机制（主档 · 一次求值怎么走）

> **依据 ST 1.19.0 源码逐行查证**，行号皆为 1.19.0。本档＝**常规路径与日常工具**。
> **特殊与边缘机制**（递归、包含组、outlet、向量化、角色过滤器、扫描状态机、时间效果、外部注入、版本复核）→ `12b-mechanics-advanced.md`。
> **条目怎么设计** → `13-entry-design.md`。
> 源码获取：`curl -sL -o st.tar.gz https://codeload.github.com/SillyTavern/SillyTavern/tar.gz/refs/tags/1.19.0`（`raw.githubusercontent.com` 在本机不通）。


## 0. 一次生成的调用链（加载 → 扫描 → 触发 → 预算 → 注入）

```js
// script.js:4624-4635（每轮生成都走这条链）
const chatForWI = coreChat.map(x => world_info_include_names ? `${x.name}: ${x.mes}` : x.mes).reverse();
const globalScanData = { personaDescription, characterDescription, characterPersonality,
                         characterDepthPrompt, scenario, creatorNotes, trigger };
const { worldInfoString, worldInfoBefore, worldInfoAfter, worldInfoExamples,
        worldInfoDepth, anBefore, anAfter, outletEntries } =
  await getWorldInfoPrompt(chatForWI, this_max_context, dryRun, globalScanData);
//   └ world-info.js:4590 getSortedEntries()  四来源 + 分组策略
//   └ world-info.js:4709 checkWorldInfo()    扫描 / 触发 / 预算
//        └ WorldInfoBuffer.get(entry)        扫描文本 = 聊天切片 + 按 match_* 拼卡字段
```

### ① 来源与分组（`getSortedEntries` :4590-4622）

四个来源：`globalLore`（独立书）· `characterLore`（卡内书 + charLore 关联）· `chatLore`（聊天绑定书）· `personaLore`（人设书）。

`world_info_character_strategy`（默认 **`character_first` = 1**）：

| 值 | 策略 | 结果 |
|:--|:--|:--|
| 0 | evenly | global + character 混在一起按 `sortFn` 排 |
| **1（默认）** | **character_first** | `[...characterLore.sort, ...globalLore.sort]` —— **卡内书整体先于独立书** |
| 2 | global_first | 反过来 |

最终拼接：`chatLore → personaLore →（上面那坨）`；`sortFn = (a, b) => b.order - a.order`（组内降序）。

⚠️ **推论（修正旧「检查单」里「靠 order 段位压共用书」的说法）**：预算优先级**先由来源分组决定**，`order` 只在同一组内比较。所以「卡内关系压过共用书世界」是**默认策略本身就保证**的；order 段位是双保险，不是唯一手段。

### ② 扫描源（`WorldInfoBuffer.get` :270-330）

```
扫描文本 = 最近 entry.scanDepth（?? 全局 world_info_depth，默认 2）条聊天，新→旧
          + entry.matchPersonaDescription    ? personaDescription
          + entry.matchCharacterDescription  ? characterDescription
          + entry.matchCharacterPersonality  ? characterPersonality
          + entry.matchCharacterDepthPrompt  ? characterDepthPrompt
          + entry.matchScenario              ? scenario
          + entry.matchCreatorNotes          ? creatorNotes
          + 注入缓冲 / 递归缓冲
```

- `scanDepth` 是**条目级**的：设 `0` → 完全不扫聊天，只能靠上面 6 类卡字段命中；设 `1` → 只看最新一条。
- ⚠️ **聊天文本每条都带 `名字: ` 前缀**（`world_info_include_names` 默认 true）→ 扫描深度内的**角色名/用户名每轮都在扫描区**。所以：以「**本体角色名**」为键 ≈ 隐性常驻（无触发意义）；以「**别的角色名**」为键才是「被提到才触发」。
- `MAX_SCAN_DEPTH = 1000`（:98）；条目级超出会截断并 warn。

### ③ 触发：内容级装饰器（:100 / :4652 / :4875）

条目 **content 开头**可以写装饰器，优先级高于键：

| 写法 | 效果 |
|:--|:--|
| `@@activate` | **强制激活**（不看键、不受 scanDepth 限制） |
| `@@dont_activate` | 强制不激活 |
| `@@@xxx` | 转义（三个 `@` 抵掉一个，避免被当装饰器） |

`KNOWN_DECORATORS = ['@@activate', '@@dont_activate']`。装饰器在 `parseDecorators` 里剥离，不会进 prompt。

### ④ 排序 / 预算 / ⑤ 注入

排序与预算见 §0、§8；注入端有**六条通道**（`getWorldInfoPrompt` :892-914）＋ `position` 七桶组装（:5200-5260，`at_depth` 按 `(depth, role)` 合桶、`outlet` 由 `{{outlet::名}}` 宏取用）。

## 1. 求值顺序（一次 checkWorldInfo 的判定链）

```
① constant（🔵常数）  → 直接激活，不看关键词（仍占预算）
② sticky 生效中        → 直接激活
③ key 为空             → 跳过
④ 主关键词命中？（任一即可）
⑤ 二级关键词门槛（selective）
⑥ 概率 → 包含组 → 预算 → 插入
```

意思是：**关键词只是一道门**。写「怎么触发」要把 ④⑤⑥ 一起想，不然条目写了等于没写。

## 2. 关键词怎么匹配（`matchKeys` :337 起）

| 分支 | 判定 | 行为 |
|:-----|:-----|:-----|
| 正则 | 键字符串是 `/pattern/flags` 形状（`parseRegexFromString`, :2821 → **:2901**，正则 `/^\/([\w\W]+?)\/([gimsuy]*)$/` 逐字未变） | 当正则跑，**其余键设置全被覆盖** |
| 纯文本 | 其他一切 | `haystack.includes(key)`，两边按大小写设置转小写 |

- ⚠️ **修正旧记录**：「ST keys 一律当正则、含 `(` `[` `*` 要转义」不成立。卡侧转换函数塞的顶层 `use_regex: true` 引擎**不读**；判定只看字符串形状，不写成 `/…/` 就是纯文本 `includes()`。
- `caseSensitive` 默认 `world_info_case_sensitive = false`（:77）。条目级必须写 `extensions.case_sensitive`——顶层 `case_sensitive` 引擎不读（1.19 读点在 :269，转换在 :5652）。
- `matchWholeWords` 默认 `world_info_match_whole_words = false`（:78）。⚠️ 中文 wiki 写「默认启用」，以源码为准。
  - 开时：多词短语 → `includes`；单词 → `(?:^|\W)(key)(?:$|\W)`（:356）
  - JS 的 `\w` 只有 `[A-Za-z0-9_]`，中文字符算 `\W` → **中文键开全词匹配照样命中**（「店长哥哥」能被「哥哥」命中）。「中文会误伤」的说法对这条规则不完全适用。
- 条目级覆盖同理：`extensions.match_whole_words`；不写就继承用户全局设置。

## 3. selective + secondary_keys（最容易踩的一处）

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

⚠️ 旧记录写的「1=AND ALL / 3=NOT ALL」是**反的**；源码枚举在 :33-37。

**实务推论：** 只想要「任意一个词就触发」，就**别写 secondary_keys**（或 `selective: false`）；只有真要「A 且 B」才开。

## 4. 扫描范围与深度（扫描文本的拼装见 §0）

- `chat` 传入时**已反序（新→旧）**（`checkWorldInfo` :4597 → :4709），`depthBuffer[0]` = 最新一条。扫描范围 = 最近 `depth` **条消息**。
- ⚠️ 飞书社区文档说按「一对（你+角色）」计数 —— 与源码不符，以「条」为准。
- 全局默认 `world_info_depth = 2`（:69）；条目级 `extensions.scan_depth` 覆盖；设 0 则完全不扫聊天（只剩常驻/递归条目）。
- 名称默认参与匹配（`world_info_include_names = true`, :74）→ 扫描区带「角色名: 」前缀。**关键词撞上角色名或用户名 = 每轮都触发**，这是「通用词当键」翻车的主因。

## 5. 位置 / 深度 / 顺序

`world_info_position`（:855，两个版本同）：

```
0 before_char | 1 after_char | 2 AN_top | 3 AN_bottom | 4 at_depth | 5 EM_top | 6 EM_bottom | 7 outlet
```

- 卡侧顶层 `position` 只认 `before_char` / `after_char`；**细位置（作者注记前后 / 示例消息前后 / @D / 输出口）必须写 `extensions.position`（数值）** —— `convertCharacterBook:5498 → :5617` 优先读 `extensions.position`（1.19 在 :5636），没有才回落到顶层。
- `extensions.depth`（默认 4，`DEFAULT_DEPTH`）配合 `at_depth`。
- `insertion_order`：数值大 → 插得越靠后 → 对当前对话影响越强。

## 6. 预算

- 引擎用的是 `world_info_budget`（**百分比**，默认 25%）+ `world_info_budget_cap`（绝对 token，0=不生效）。
- ⚠️ **卡里 `character_book` 的 book 级 `scan_depth` / `token_budget` / `recursive_scanning` 在引擎中没有任何读取点**（全仓 grep 实证；转换时只随 `originalData` 留存）。写这些数字是**摆设**，真正生效的是用户侧全局设置。
- 预算耗尽时的保留顺序：常数 > 直接命中 > 递归命中；同级按 order 大者优先。

### 预算截断的精确行为（:5017-5070）

- 遍历顺序＝ `newEntries`（sticky 优先 + sortedEntries 位次）。
- 累计 `textToScanTokens + newContent ≥ budget` → `token_budget_overflowed = true`，**其后所有非 `ignoreBudget` 的一律丢弃**。
- `ignoreBudget: true` 无视预算上限（慎用；溢出后仍会被尝试）。
- 概率：`useProbability && probability < 100` 才掷骰；**sticky 不重掷**。
- **递归轮的命中在初始轮之后处理** → 准确表述是「初始轮（含常数与直接命中）＞递归命中」，而不是三档排队。

## 7. 其它可写字段（一律在 `entry.extensions`）

| 字段 | 默认 | 说明 |
|:-----|:-----|:-----|
| `probability` / `useProbability` | 100 / true | 触发概率 |
| `group` / `group_weight` / `group_override` / `use_group_scoring` | '' / 100 / false / false | 包含组（同组只留一条） |
| `sticky` / `cooldown` / `delay` | null | 定时效果（单位=消息条数，0=无效果） |
| `triggers` | [] | 按生成类型过滤——取值 `normal` / `continue` / `impersonate` / `swipe` / `regenerate` / `quiet`（不在列表里＝该类型下不激活）；枚举见 `constants.js` 的 `GENERATION_TYPE_TRIGGERS` |
| `outlet_name` | '' | 输出口，配合 `{{outlet::名称}}` |
| `match_character_description` / `match_character_personality` / `match_character_depth_prompt` / `match_scenario` / `match_persona_description` / `match_creator_notes` | false | 附加匹配来源：卡字段本身也能当触发源 |
| `vectorized` | false | 向量匹配（需扩展，且不可预测） |
| `role` | `extension_prompt_roles.SYSTEM` | `at_depth` 注入时的消息角色；其余取值见 `public/script.js` 的 `extension_prompt_roles` 定义 |
| `ignore_budget` | false | 无视预算上限（慎用） |

## 8. 求值全流程（源码顺序，伪代码）

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

## 9. 模拟器 / CLI 与官方排序工具

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

### 官方「Apply Current Sorting」怎么用（1.19 起）

用途：**按当前列表顺序给全书重排 order**。

- 入口：世界书面板 → Apply Current Sorting
- 语义：列表**第一项**拿起始值；降序时逐项 −步长（下限 0），升序时逐项 +步长
- 默认：起始 `100`、步长 `1`、降序 → 首项 100、次项 99、……
- **只动当前这一本书**（不碰卡内 `character_book`）→ 与「卡内 300-390 / 共用书 100-179」的跨书段位**不冲突**
- ⚠️ 降序 + 起始值 < 条目数 → 底部条目被 clamp 到 0 造成碰撞；1.19 弹窗会实时警告，别忽略
- 步长 > 1 的用处：**留插入空档**（100/95/90…），以后想在中间插条目还有整数位可用
- 什么时候用：手写书、条目顺序靠拖拽整理完 → 一把赋号；脚本生成的书（`wb new`）order 已由脚本算好，**别再用它覆盖**
