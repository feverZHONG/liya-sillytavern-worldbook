# 世界书机制（附档 · 特殊与边缘）

> **依据 ST 1.19.0 源码逐行查证**，行号皆为 1.19.0。本档＝**非常规路径与版本维护**。
> 常规路径（调用链、判定链、匹配、扫描源、位置、预算、字段、工具）→ `12-worldbook-mechanics.md`。
> 一条命令复核上游新版：`python3 scripts/st-version-diff.py --tag-a 1.19.0 --tag-b <新版本>`。


## 0. 递归（默认关闭）

`world_info_recursive = false`（:75）。条目级：`extensions.exclude_recursion`（不被别人激活）/ `prevent_recursion`（不激活别人）/ `delay_until_recursion`（只在递归轮激活）。全局最大递归步数默认 0 = 只受预算约束。

## 1. 包含组 · outlet · 向量化

### 1.1 包含组（`extensions.group`）

- `group` 是**字符串**，可用**逗号分隔同时属于多个组**（:5392 `item.group.split(/,\s*/)`）。
- 同组只留一条，淘汰顺序（`filterByInclusionGroups` :5388 起）：
  1. 组内若有 sticky 生效中的条目 → 其余直接删（第 ② 步跳过）
  2. **组打分**（`filterGroupsByScoring`）：开了 `use_group_scoring` 按**命中键数**比；`group_override` 时取 order 最大者；否则按 `group_weight` **权重随机**
  3. 组内若已有激活条目（`allActivatedEntries`）→ 其余全删
- 用途：同类互斥条目（同一角色的几版口癖、同一事件的几种说法）——**只留一条**时用它，不是靠手写去重。

### 1.2 outlet（输出口）

- 条目 `position: outlet(7)` + `outlet_name` → 收集进 `outletEntries[名称]`，**本身不进 prompt**（:5246-5256）。
- 取用靠宏 `{{outlet::名称}}`（定义在 `macros.js:668` → `getOutletPrompt(key)`）。
- → **凡是被宏替换处理过的文本都能取到它**（卡 description／系统提示／作者注记／prompt 模板）。
  这是把「内容钉在指定位置」的正路，比用 position 猜位置更准。
- ⚠️ 前提：该 outlet 条目**必须先被激活**（靠键或常驻），否则宏取到空串。

### 1.3 vectorized（向量匹配）

- 主判定链**不处理** `vectorized`：`world-info.js` 里只有字段读写与 tri-state 选择器（:3277-3314）。
- 实际激活来自 **vectors 扩展**（`public/scripts/extensions/vectors/index.js`）——装了 Vector Storage + 建了向量数据，才按语义匹配激活。
- 结论：不装向量扩展就别开这个开关（等于把条目挂起来、不进任何判定）。

## 2. 角色过滤器：卡内书做不到，独立书可以

**求值链里确实读**（`checkWorldInfo` :4814-4836，2026-09-25 实证；1.18 版文档只写了「字段存在」，没说清它在哪儿生效）：

```js
// 按角色卡过滤
if (entry.characterFilter?.names?.length > 0) {
    const nameIncluded = entry.characterFilter.names.includes(getCharaFilename()); // avatar 文件名（去扩展名）
    const filtered = entry.characterFilter.isExclude ? nameIncluded : !nameIncluded;
    if (filtered) continue;        // isExclude=false（默认）→ 不在名单里的角色直接跳过
}
// 按标签过滤（getTagKeyForEntity(this_chid) + context.tagMap，逻辑同上）
if (entry.characterFilter?.tags?.length > 0) { … }
```

**卡内书拿不到这个字段**：`convertCharacterBook`（:5617）的 42 个字段里**没有 `characterFilter`** —— spec 的 `character_book.entries[]` 结构不含它，转换时自然丢弃。所以：

| 书类型 | 能否按角色/标签分流 | 原因 |
|:--|:--|:--|
| 独立世界书 `.json` | ✅ 能 | 条目原样进求值链，过滤器字段在 |
| 卡内嵌 `character_book` | ❌ 不能 | 转换时字段被丢，过滤器无从生效 |

⚠️ `names` 用的是 **avatar 文件名去扩展名**（`getCharaFilename` = `avatar.replace(/\.[^/.]+$/, '')`）——**改名即断**，与 `extensions.world` 同属脆弱约定，改名要全线同步。

**求值时机：在条目循环最前**——`disable`／生成类型 `triggers` 之后，`sticky`／`delay`／`@@activate`／`constant` 之前（1.19.0 :4809-4836 逐行）。
→ 推论：**常驻条目同样受限知情名单**，「限知情」不会被 constant 绕开。

⚠️ **群聊里按「当前发言成员」判，不是按「群」**（2026-09-25 实测，`group-chats.js`）：群聊生成是**逐成员轮转**——每个成员 `Generate()` 之前先 `setCharacterId(chId)`（`:1054`），整轮循环跑完才归 `undefined`（`:1080`）。
所以群聊里 `getCharaFilename()` **不是 null**，而是「这轮发言的那个人」→ names 过滤在群聊里**照常生效**：非知情成员的轮次里条目不进，知情成员的轮次里照常进。
**踩过的误判**：只看 `openGroupById` 里的 `setCharacterId(undefined)`（`:2034`）会得出「群聊里 names 过滤全灭」的错结论——**要连着生成循环一起读**才看得到 :1054 那一跳。

→ 这既是「共用世界书走独立文件 + `extensions.world` 关联」的硬理由，也让「**一本关系书按角色分流**」成为可能（架构对比见 `13-entry-design.md` §五）。

## 3. 扫描状态机 · 时间效果 · 外部注入

### 3.1 扫描状态机（`scan_state` :43-62，主循环 :5090-5145）

```
NONE 0 ／ INITIAL 1 ／ RECURSION 2 ／ MIN_ACTIVATIONS 3

while (scanState) {
  INITIAL        正常扫一遍
  → RECURSION    当 world_info_recursive 且有「可递归的命中条目」：
                 递归源 = successfulNewEntriesForRecursion（未被 preventRecursion / excludeRecursion 挡掉的）
                 把它们的 **content** 塞进 recurseBuffer → 下一轮扫的是「上一轮命中条目的正文」
  → MIN_ACTIVATIONS  当 world_info_min_activations > 0 且激活数不足：
                 buffer.advanceScan() 把扫描窗口往前推一条再扫（上限 min_activations_depth_max）
                 该轮之后再补一次 RECURSION（若还有递归缓冲）
  → RECURSION    当 delay_until_recursion 还有未处理的层级（currentRecursionDelayLevel 逐级推进）
}
```

- **递归是「内容 → 内容」的级联**，扫的不是聊天，是上一轮命中条目的正文——所以条目之间可以互相引出名
  （「08小队」条目正文提到「洛氏」→ 若开递归，能连锁激活洛氏条目）。
- `delay_until_recursion: N` = 只在第 N 层递归激活。
- 我们当前：`world_info_recursive=false`、`min_activations=0` → **只有 INITIAL 一轮**。

### 3.2 时间效果（sticky / cooldown / delay）

- 存 `chat_metadata.timedWorldInfo[type][key]`（**按聊天保存**），effect = `{ start, end, protected }`。
- 单位＝**消息条数**；`sticky` 生效中的条目不重新掷概率（`verifyProbability` 直接放行）。
- `sticky` 到期会自动挂上 `cooldown`（:518-526）；`checkTimedEffects()` 每个扫描周期跑一次。
- 用途：让某条设定「出现后持续 N 条消息」或「冷却 N 条后才允许再出现」。

### 3.3 外部注入进扫描（:4719-4726）

```js
for (const key of Object.keys(context.extensionPrompts)) {
    if (context.extensionPrompts[key]?.scan) {
        const prompt = await getExtensionPromptByName(key);
        if (prompt) buffer.addInject(prompt);
    }
}
```

- 扩展的 extension prompt 标 `scan: true` → 其内容进扫描缓冲 → **世界书可以靠扩展注入的内容触发**
  （内置扩展里未见标 scan 的调用点，这是留给扩展的接口）。
- 该内容走 `#injectBuffer`，在 `buffer.get()` 里拼在聊天切片与 match_* 字段之后。

## 4. 版本变更复核（1.18.0 → 1.19.0）

两个 tag 的 `world-info.js` 全文件 diff 只有 **6 个 hunk / 245 行**，其中实质改动 3 处，**没有一处落在判定链上**：

| # | 位置（1.19 行号） | 改动 | 对写书的影响 |
|:--|:--|:--|:--|
| 1 | `:2495` UI「Apply Current Sorting」 | 旧版只能填一个起始号、按列表降序每次减 1；新版给 **起始值 + 步长 + 升序/降序** 三个输入，带实时越界校验。顺带修了旧版 bug：`setWIOriginalDataValue(data, entry.order, 'order', …)` 把 order 当 uid 传 → 改 `entry.uid` | ✅ 官方批量赋 order 工具（用法见主档 §9）。**整书覆盖**，不是增量 |
| 2 | `:4203` `updateWorldInfoLinks` | 旧版重命名只同步 `world_info.charLore`（卡关联）；新版追加 **persona 的 lorebook 链接**（当前 persona + 其余 persona 的 `persona_descriptions[].lorebook`）与 **聊天元数据键** `chat_metadata[METADATA_KEY]` | 走 UI 重命名时链接跟得更全；**直接改文件名仍会断链**——ASCII 文件名纪律不变 |
| 3 | `:4991` 求值后的概率/预算排序 | `[...activatedNow].sort(… sortedEntries.indexOf(…))`（每轮 O(n²)）→ 先建 `sortedEntriesIndex = new Map(...)` 再 `.get()`，且只有 `size > 1` 才排序 | **纯性能优化，语义等价**（sticky 优先 + 原排序位次；`?? -1` 兜底与旧 `indexOf` 同为 −1） |

其余复核点：

- **`convertCharacterBook`（:5617）字段集 42 个，与 1.18 逐字相同**（脚本按 `^\s{12}(\w+):` 比对，集合差为空）→ 仍**不读** book 级 `scan_depth` / `token_budget` / `recursive_scanning`（全仓 grep 只命中类型定义 `src/types/spec-v2.d.ts:28-30` 与同名局部变量 `token_budget_overflowed`），仍**不读** `characterFilter`。
- 默认值常量 `:69-82` 逐字一致；`world_info_logic` `:33-37`、`world_info_position` `:855` 一致。
- `use_regex` / `useRegex` 在 1.19 的 `world-info.js` 里**零命中** → 「判定只看字符串形状」依旧成立。
- `src/endpoints/worldinfo.js` 两版本**零差异**；`src/endpoints/characters.js` 只有 3 处与世界书无关的改动（`getArrayBufferSlice` 工具化、Windows `cpSync` filter 绕过 Node 崩溃、import 调整）。
- 模拟器：复核后重跑 `wb selftest` —— **37 项全过**，Python 侧判定链无需改动。

> **复核工具（2026-09-25 沉淀）**：`python3 scripts/st-version-diff.py --tag-a 1.18.0 --tag-b 1.19.0`
> ——自动下载两版源码，一把列出「文件规模 / 默认值常量 / 卡内书字段集 / 关键函数体与行号 / hunk 清单」，并给出「机制是否变」的判定；`--hunks` 看 diff 明细。
> **下次上游发版先跑它**，判定「机制未变」再谈条目的事；`--a/--b` 可指已解压的源码目录。
