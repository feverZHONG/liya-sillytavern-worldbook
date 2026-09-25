#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""worldbook_tools —— 世界书（character_book）机制引擎（SillyTavern 1.18.0 口径，1.19.0 复核一致）

用法:
  worldbook_tools.py ls    <卡.json...>                      世界书台账（book 级 + 条目级）
  worldbook_tools.py check <卡.json...> [--max-context 8192] 体检：触发陷阱 / 冲突 / 预算
  worldbook_tools.py keys  <卡.json...>                      关键词矩阵（跨卡撞车）
  worldbook_tools.py sim   <卡.json> <文本> [选项]            触发模拟：谁会被激活、为什么没激活

sim 选项:
  --depth N        扫描深度（默认 2，与酒馆全局默认一致）
  --char-name N    角色名（参与「名称: 内容」前缀，默认取卡内 name）
  --user-name N    用户名（默认「{{user}}」）
  --prev TEXT      上一条消息（可重复，按旧→新给；最后给的离当前最近）
  --no-names       关闭名称前缀（酒馆「包含名称」默认是开的）
  --max-context N  上下文上限（预算 = 25% × 该值）

口径来源: public/scripts/world-info.js（触发链 / matchKeys / selectiveLogic / 预算 / 组装）
           见 skills/sillytavern-worldbook/references/12-worldbook-mechanics.md
"""
import argparse
import json
import os
import re
import sys
import time

# ── 酒馆全局默认（world-info.js 模块级初值）──────────────────────────────
G = {
    "depth": 2,                # world_info_depth
    "case_sensitive": False,   # world_info_case_sensitive
    "match_whole_words": False,  # world_info_match_whole_words
    "include_names": True,     # world_info_include_names
    "recursive": False,
    "budget_pct": 25,          # world_info_budget（百分比）
    "budget_cap": 0,           # world_info_budget_cap（0=不生效）
    "max_context": 8192,
}
DEFAULT_DEPTH = 4   # entry.extensions.depth 缺省
DEFAULT_WEIGHT = 100

POSITION = {0: "before_char", 1: "after_char", 2: "AN_top", 3: "AN_bottom",
            4: "at_depth", 5: "EM_top", 6: "EM_bottom", 7: "outlet"}
LOGIC = {0: "AND_ANY", 1: "NOT_ALL", 2: "NOT_ANY", 3: "AND_ALL"}
# 通用词：撞上角色名 / 用户名 / 高频日常词就会常年误触发
GENERIC_KEYS = {"店长", "老师", "哥哥", "姐姐", "弟弟", "妹妹", "先生", "小姐", "大人",
                "我", "你", "他", "她", "它", "我们", "你们", "咖啡", "工作", "过去",
                "现在", "喜欢", "讨厌", "秘密", "朋友", "决定", "责任"}

_TOKEN_ENC = None
_TOKEN_TRIED = False


def token_count(text):
    """tiktoken cl100k_base（与酒馆 getTokenCountAsync 同口径）；缺库则中文加权估算。"""
    global _TOKEN_ENC, _TOKEN_TRIED
    if not _TOKEN_TRIED:
        _TOKEN_TRIED = True
        try:
            import tiktoken
            _TOKEN_ENC = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _TOKEN_ENC = None
    if _TOKEN_ENC is not None:
        return len(_TOKEN_ENC.encode(text or ""))
    cjk = sum(1 for ch in (text or "") if "\u4e00" <= ch <= "\u9fff")
    return int(cjk * 1.5 + (len(text or "") - cjk) * 0.3) + 1


# ── 读取 / 归一化（两种来源：卡内 character_book 与独立世界书 .json）────────
def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_book(path):
    """返回 (kind, book, card)。kind: 'card' / 'wi' / 'spec' / 'none'"""
    obj = load(path)
    if isinstance(obj.get("data"), dict):
        return "card", (obj["data"].get("character_book") or {}), obj
    if isinstance(obj.get("entries"), (dict, list)):
        ents = obj["entries"]
        first = (ents[list(ents)[0]] if isinstance(ents, dict) and ents
                 else (ents[0] if isinstance(ents, list) and ents else None))
        if isinstance(first, dict) and _is_card_entry(first):
            return "spec", obj, None          # 条目清单（给 wb new 用的），不是书
        return "wi", obj, None
    return "none", {}, obj


def _is_card_entry(entry):
    return "keys" in entry or "insertion_order" in entry or "secondary_keys" in entry


def raw_entries(book):
    """book.entries 可能是数组（卡侧）或 {uid: entry}（WI 侧）。"""
    ents = book.get("entries") or []
    if isinstance(ents, dict):
        return [ents[k] for k in sorted(ents, key=lambda x: str(x))]
    return list(ents)


def normalize(entry, index=0):
    """条目 → 统一判定形状（两种来源各自的字段名与默认值按各自规范对齐）。"""
    ext = entry.get("extensions") or {}
    if _is_card_entry(entry):                    # 卡侧（convertCharacterBook 口径）
        keys, sec = entry.get("keys") or [], entry.get("secondary_keys") or []
        order, enabled, uid = entry.get("insertion_order"), entry.get("enabled", True), entry.get("id", index)
        pos = ext.get("position")
        if pos is None:
            pos = 0 if entry.get("position") == "before_char" else 1
        cs, ww, sd = ext.get("case_sensitive"), ext.get("match_whole_words"), ext.get("scan_depth")
        logic, prob, use_prob, depth = (ext.get("selectiveLogic"), ext.get("probability"),
                                        ext.get("useProbability"), ext.get("depth"))
    else:                                        # 独立世界书（ST 保存形状，驼峰）
        keys, sec = entry.get("key") or [], entry.get("keysecondary") or []
        order, enabled, uid = entry.get("order"), not entry.get("disable", False), entry.get("uid", index)
        pos = entry.get("position")
        if isinstance(pos, str):
            pos = 0 if pos == "before_char" else 1
        if not isinstance(pos, int):
            pos = 1
        cs = entry.get("caseSensitive", ext.get("case_sensitive"))
        ww = entry.get("matchWholeWords", ext.get("match_whole_words"))
        sd = entry.get("scanDepth", ext.get("scan_depth"))
        logic = entry.get("selectiveLogic", ext.get("selectiveLogic"))
        prob = entry.get("probability", ext.get("probability"))
        use_prob = entry.get("useProbability", ext.get("useProbability"))
        depth = entry.get("depth", ext.get("depth"))
    return {
        "uid": uid,
        "key": list(keys),
        "keysecondary": list(sec),
        "comment": entry.get("comment") or "",
        "content": entry.get("content") or "",
        "constant": bool(entry.get("constant")),
        "selective": bool(entry.get("selective")),
        "order": order if isinstance(order, (int, float)) else None,
        "position": pos,
        "enabled": bool(enabled),
        "probability": prob if prob is not None else 100,
        "use_probability": bool(use_prob) if use_prob is not None else True,
        "depth": depth if depth is not None else DEFAULT_DEPTH,
        "logic": logic if logic is not None else 0,
        "scan_depth": sd,
        "case_sensitive": cs,
        "whole_words": ww,
        "group": entry.get("group") or ext.get("group") or "",
        "sticky": entry.get("sticky"), "cooldown": entry.get("cooldown"), "delay": entry.get("delay"),
        "raw": entry,
    }


def entries_of(path):
    kind, book, _ = load_book(path)
    return [normalize(e, i) for i, e in enumerate(raw_entries(book))]


# ── 触发判定（matchKeys / world-info.js:337-366）──────────────────────────
_REGEX_SHAPE = re.compile(r"^/([\s\S]+?)/([gimsuy]*)$")


def parse_regex_from_string(s):
    """字符串是不是 /pattern/flags 形状？是就返回编译后的正则（JS 语义的近似）。"""
    m = _REGEX_SHAPE.match(s)
    if not m:
        return None
    pattern, flags = m.group(1), m.group(2)
    if re.search(r"(^|[^\\])/", pattern):      # 未转义的分隔符 → 判定失败
        return None
    pattern = pattern.replace("\\/", "/")
    f = 0
    if "i" in flags:
        f |= re.I
    if "m" in flags:
        f |= re.M
    if "s" in flags:
        f |= re.S
    try:
        return re.compile(pattern, f)
    except re.error:
        return None


def match_keys(haystack, needle, case_sensitive=False, whole_words=False):
    needle = (needle or "").strip()
    if not needle:
        return False
    rx = parse_regex_from_string(needle)
    if rx is not None:
        return rx.search(haystack) is not None          # 正则分支：其余设置被覆盖
    hs = haystack if case_sensitive else haystack.lower()
    nd = needle if case_sensitive else needle.lower()
    if whole_words:
        words = re.split(r"\s+", nd)
        if len(words) > 1:
            return nd in hs
        # JS 的 \w 只等于 [A-Za-z0-9_]；Python 的 \w 连中日韩都算，必须手写边界
        return re.search(r"(?:^|[^A-Za-z0-9_])(" + re.escape(nd) + r")(?:$|[^A-Za-z0-9_])", hs) is not None
    return nd in hs


def scan_text(messages, depth, char_name=None, user_name=None, include_names=True):
    """messages: [(name, text)] 旧→新。反序后取最近 depth 条，按酒馆规则拼扫描区。"""
    depth = max(0, int(depth))
    rev = list(reversed(messages))[:depth]
    parts = []
    for name, text in rev:
        parts.append(f"{name}: {text}" if include_names else text)
    return "\x01" + "\n\x01".join(parts)


def subst(text, char_name="", user_name=""):
    """只做最常用的宏替换；其余 {{...}} 原样留着（模拟器不追求全宏）。"""
    if not text:
        return text
    return (text.replace("{{char}}", char_name or "{{char}}")
                .replace("{{user}}", user_name or "{{user}}")
                .replace("{{charIfNotGroup}}", char_name or "{{charIfNotGroup}}"))


def evaluate_entry(entry, text, char_name="", user_name=""):
    """返回 (verdict, detail)。verdict: constant / active / blocked-secondary / no-primary / no-keys / disabled"""
    if not entry["enabled"]:
        return "disabled", None
    if entry["constant"]:
        return "constant", None
    if not entry["key"]:
        return "no-keys", None
    cs = entry["case_sensitive"] if entry["case_sensitive"] is not None else G["case_sensitive"]
    whole = entry["whole_words"] if entry["whole_words"] is not None else G["match_whole_words"]
    hit = None
    for k in entry["key"]:
        sk = subst(k, char_name, user_name).strip()
        if sk and match_keys(text, sk, cs, whole):
            hit = k
            break
    if hit is None:
        return "no-primary", None
    if entry["selective"] and entry["keysecondary"]:
        matched = [k for k in entry["keysecondary"]
                   if match_keys(text, subst(k, char_name, user_name), cs, whole)]
        n, total = len(matched), len(entry["keysecondary"])
        logic = entry["logic"]
        if logic == 0:
            ok = n > 0
        elif logic == 1:
            ok = n < total
        elif logic == 2:
            ok = n == 0
        else:
            ok = n == total
        if not ok:
            return "blocked-secondary", {"hit": hit, "logic": LOGIC.get(logic, logic), "matched": matched}
        return "active", {"hit": hit, "secondary": matched}
    return "active", {"hit": hit, "secondary": []}


# ── 子命令 ───────────────────────────────────────────────────────────────
def cmd_ls(args):
    for p in args.cards:
        kind, book, card = load_book(p)
        data = (card or {}).get("data") or {}
        ents = entries_of(p)
        if kind == "spec":
            print(f"===== {os.path.basename(p)} · 条目清单（不是书；用 `wb new` 生成后再看）=====\n")
            continue
        label = data.get("name") or ("独立世界书" if kind == "wi" else "?")
        print(f"===== {os.path.basename(p)} · {label} [{kind}] =====")
        if not ents:
            print("  （没有条目）\n")
            continue
        if kind == "wi":
            print(f"  entries 键 {len(raw_entries(book))} 个"
                  f"{'（⚠️ 与 uid 数量不符）' if len(raw_entries(book)) != len({e['uid'] for e in ents}) else ''}")
        else:
            print(f"  book: name={book.get('name')!r}")
            for k in ("scan_depth", "token_budget", "recursive_scanning"):
                if k in book:
                    print(f"        {k}={book[k]}  ⚠️ 引擎不读（摆设）")
        print(f"  条目 {len(ents)}  常驻 {sum(1 for e in ents if e['constant'])}  "
              f"token 合计 {sum(token_count(e['content']) for e in ents)}")
        print(f"  {'uid':>4} {'ord':>5} {'pos':<12} {'常':<2} {'sel':<3} {'logic':<8} {'prob':>5}  备注 / 主键")
        for e in ents:
            sel = "是" if e["selective"] and e["keysecondary"] else "—"
            print(f"  {e['uid']:>4} {str(e['order']):>5} {POSITION.get(e['position'], e['position']):<12} "
                  f"{'🔵' if e['constant'] else '  '} {sel:<3} {LOGIC.get(e['logic'], e['logic']):<8} "
                  f"{e['probability']:>5}  {e['comment'][:22]} | {'/'.join(e['key'])}")
        print()
    return 0


def cmd_keys(args):
    index = {}
    for p in args.cards:
        if load_book(p)[0] == "spec":
            continue
        stem = os.path.splitext(os.path.basename(p))[0]
        for e in entries_of(p):
            for k in e["key"]:
                index.setdefault(k, []).append((stem, e["uid"], e["comment"][:16]))
    print(f"主键 {len(index)} 个（按跨卡出现数排序）\n")
    for k, uses in sorted(index.items(), key=lambda kv: (-len({u[0] for u in kv[1]}), -len(kv[1]))):
        cards = {u[0] for u in uses}
        flag = "⚠️ 跨卡" if len(cards) > 1 else ("⚠️ 通用词" if k in GENERIC_KEYS else "   ")
        print(f"  {flag} {k:<14} 出现在 {len(cards)} 张卡 / {len(uses)} 条："
              + "、".join(f"{c}#{u}" for c, u, _ in uses))
    return 0


def cmd_check(args):
    G["max_context"] = args.max_context
    budget = int(round(G["budget_pct"] * G["max_context"] / 100)) or 1
    if G["budget_cap"] > 0:
        budget = min(budget, G["budget_cap"])
    problems = 0
    rows = []
    key_owner = {}
    for p in args.cards:
        name = os.path.basename(p)
        kind, book, card = load_book(p)
        ents = entries_of(p)
        issues = []
        if kind == "spec":
            print(f"  {name:<18} （条目清单，不是书；`wb new` 生成后再体检）")
            continue
        if not ents:
            print(f"  {name:<18} （没有条目，跳过）")
            continue
        for e in ents:
            tag = f"#{e['uid']} {e['comment'][:14]}"
            if e["selective"] and e["keysecondary"]:
                issues.append(f"🟡 二级键门槛 {tag}：{LOGIC.get(e['logic'])}，"
                              f"要 [{'/'.join(e['key'][:3])}] 且 [{'/'.join(e['keysecondary'])}]")
            elif e["selective"] and not e["keysecondary"]:
                issues.append(f"⚪ 空 selective {tag}：开了开关但没二级键，可清")
            for k in e["key"]:
                if k in GENERIC_KEYS:
                    issues.append(f"🟠 通用词主键 {tag}：'{k}'（名称参与扫描 → 易常年触发）")
                elif len(k) == 1:
                    issues.append(f"🟠 单字主键 {tag}：'{k}'")
                if parse_regex_from_string(k) is not None:
                    issues.append(f"🟠 正则形状主键 {tag}：{k}（会被当正则跑）")
                key_owner.setdefault(k, set()).add(name)
            if e["order"] is None:
                issues.append(f"🔴 缺 insertion_order {tag} → 引擎拿到 undefined")
            top_pos = e["raw"].get("position")
            if kind == "card" and top_pos is not None and top_pos not in ("before_char", "after_char") and \
                    (e["raw"].get("extensions") or {}).get("position") is None:
                issues.append(f"🔴 顶层 position='{top_pos}' 无效 {tag}：卡侧只认 before/after_char，"
                              f"细位置要写 extensions.position")
            if e["scan_depth"] is not None:
                issues.append(f"⚪ 条目级 scan_depth={e['scan_depth']} {tag}（生效，确认是有意为之）")
            if "@@" in e["content"][:200]:
                issues.append(f"🟠 content 带 @@ 装饰器 {tag}")
        for k in ("scan_depth", "token_budget", "recursive_scanning"):
            if k in book:
                issues.append(f"🟡 book 级 {k}={book[k]} 是摆设（引擎无读取点）")
        consts = [e for e in ents if e["constant"]]
        if consts:
            issues.append(f"⚪ 常驻 {len(consts)} 条，占 {sum(token_count(e['content']) for e in consts)} token"
                          f"（常驻也吃预算）")
        mark = "❌" if any(i.startswith("🔴") for i in issues) else ("⚠️" if issues else "✅")
        tok = sum(token_count(e["content"]) for e in ents)
        print(f"  {mark} {name:<18} {len(ents)} 条 / {tok} token")
        for i in issues:
            print(f"        {i}")
        rows.append((name, len(ents), tok))
        problems += sum(1 for i in issues if i.startswith("🔴"))
    collide = {k: v for k, v in key_owner.items() if len(v) > 1}
    if collide:
        print(f"\n  ⚠️ 跨卡撞车主键 {len(collide)} 个：")
        for k, v in sorted(collide.items(), key=lambda kv: -len(kv[1]))[:15]:
            print(f"        {k:<12} → {'、'.join(sorted(v))}")
    print(f"\n  预算口径：{G['budget_pct']}% × {G['max_context']} = {budget} token（单卡）")
    print(f"  {'文件':<18} {'条目':>4} {'条目合计 token':>14}   占预算")
    for name, n, tok in rows:
        pct = f"{tok / budget * 100:.0f}%"
        warn = "  ⚠️ 全部触发会超预算（按 order 降序截断）" if tok > budget else ""
        print(f"  {name:<18} {n:>4} {tok:>14}   {pct}{warn}")
    print(f"  🔴 硬问题 {problems} 个")
    return 1 if problems else 0


def write_card(path, card):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(card, f, ensure_ascii=False, indent=2)


def classify_secondary(key):
    """二级键分流判据：通用词 / 单字 → 剔除；其余（专有名词、体征、梗）→ 并入主键。"""
    if key in GENERIC_KEYS or len(key) < 2:
        return "drop"
    return "merge"


def cmd_unfilter(args):
    """关掉 selective 隐形门槛：二级键分流（并入主键 / 剔除），selective=false。

    默认只报不改；--fix 才写回，并按项目惯例把 character_version +0.1、追加 creator_notes。
    """
    total = 0
    for p in args.cards:
        kind, book, card = load_book(p)
        if kind != "card":
            print(f"===== {os.path.basename(p)} —— 独立世界书，跳过（unfilter 只处理卡内书）\n")
            continue
        data = card.get("data") or {}
        ents = raw_entries(book)
        plans = []
        for raw in ents:
            sec = raw.get("secondary_keys") or []
            if not (raw.get("selective") and sec):
                continue
            keys = list(raw.get("keys") or [])
            merge = [k for k in sec if classify_secondary(k) == "merge" and k not in keys]
            drop = [k for k in sec if k not in merge]
            plans.append((raw, keys, merge, drop))
        print(f"===== {os.path.basename(p)} —— 待去门槛 {len(plans)} 条")
        for raw, keys, merge, drop in plans:
            print(f"  #{raw.get('id')} {raw.get('comment','')[:22]}")
            print(f"      主键 {keys}  →  {keys + merge}")
            if drop:
                print(f"      剔除：{'、'.join(drop)}")
        if not plans:
            print()
            continue
        if not args.fix:
            print()
            continue
        for raw, keys, merge, _ in plans:
            raw["keys"] = keys + merge
            raw.pop("secondary_keys", None)
            raw["selective"] = False
            if isinstance(raw.get("extensions"), dict):
                raw["extensions"].pop("selectiveLogic", None)
        if not args.no_bump:
            old = str(data.get("character_version") or "0.0")
            try:
                new = f"{float(old) + 0.1:.1f}"
            except ValueError:
                new = old
            data["character_version"] = new
            if "character_version" in card:
                card["character_version"] = new
            line = (f"{time.strftime('%m-%d')} 世界书触发修复：去掉 {len(plans)} 条的 selective 隐形门槛"
                    f"（二级键并入主键／通用词剔除）。")
            notes = data.get("creator_notes") or ""
            if line not in notes:
                data["creator_notes"] = notes + line
            print(f"      版本 {old} → {new}；creator_notes 追加修复说明")
        write_card(p, card)
        total += len(plans)
        print()
    if args.fix and total:
        print(f"🔧 共处理 {total} 条（写入完成）")
    elif total:
        print(f"（预览：{total} 条待处理；加 --fix 写回）")
    return 0


def cmd_selftest(args):
    """机制判定自测：每条规则对拍 world-info.js 的行为，改代码后跑一遍。"""
    passed, failed = 0, []

    def t(desc, got, want):
        nonlocal passed
        if got == want:
            passed += 1
        else:
            failed.append(f"{desc}: got={got!r} want={want!r}")

    def mk(key=None, sec=None, logic=0, **kw):
        e = {"keys": key or [], "secondary_keys": sec or [], "content": "x", "comment": "",
             "enabled": True, "insertion_order": 1, "id": 0,
             "selective": sec is not None,   # 有二级键才开开关，跟卡里一致
             "extensions": {"selectiveLogic": logic} if sec is not None else {}}
        e.update(kw)
        return normalize(e)

    # ① 正则判定：看字符串形状，不看开关（parseRegexFromString :2821）
    t("正则形状 /a/i", parse_regex_from_string("/a/i") is not None, True)
    t("非 '/…/' 不算正则", parse_regex_from_string("a/b") is None, True)
    t("未转义斜杠不算", parse_regex_from_string("/a/b/") is None, True)
    t("含括号的纯文本不是正则", parse_regex_from_string("咖啡(店") is None, True)

    # ② 文本匹配
    t("纯文本 includes", match_keys("今天去喝咖啡(店)", "咖啡(店"), True)
    t("默认大小写不敏感", match_keys("FOO bar", "foo"), True)
    t("caseSensitive 生效", match_keys("FOO bar", "foo", case_sensitive=True), False)
    t("正则分支 /foo/i", match_keys("FOO bar", "/foo/i"), True)

    # ③ 全词匹配（JS \w 只含 ASCII，中文算边界）
    t("全词命中", match_keys("long live the king", "king", whole_words=True), True)
    t("全词不命中", match_keys("it's not to my liking", "king", whole_words=True), False)
    t("全词-中文邻接仍命中", match_keys("店长哥哥，早安", "哥哥", whole_words=True), True)
    t("全词-多词短语", match_keys("say hello world now", "hello world", whole_words=True), True)

    # ④ selective 四逻辑（0=AND_ANY 1=NOT_ALL 2=NOT_ANY 3=AND_ALL）
    for logic, txt, want in ((0, "k s1", True), (0, "k s3", False),
                             (1, "k s1", True), (1, "k s1 s2", False),
                             (2, "k s1", False), (2, "k s3", True),
                             (3, "k s1 s2", True), (3, "k s1", False)):
        t(f"logic={logic} «{txt}»", evaluate_entry(mk(["k"], ["s1", "s2"], logic), txt)[0] == "active", want)

    # ⑤ 其余判定
    t("selective 关 → 二级键忽略", evaluate_entry(mk(["k"], ["s1"], 0, selective=False), "k")[0], "active")
    t("constant 无需关键词", evaluate_entry(mk(constant=True), "什么都没有")[0], "constant")
    t("没主键 → no-keys", evaluate_entry(mk(), "k")[0], "no-keys")
    t("禁用 → disabled", evaluate_entry(mk(["k"], enabled=False), "k")[0], "disabled")
    t("主键命中但二级不过 → blocked",
      evaluate_entry(mk(["k"], ["s1"], 0), "只有 k")[0], "blocked-secondary")

    # ⑥ 扫描区拼装：反序（新→旧）+ 取最近 depth 条 + 名称前缀
    msgs = [("用户", "第一条"), ("角色", "第二条"), ("用户", "第三条")]
    t("depth=2 只含最近两条", "第一条" not in scan_text(msgs, 2, "角色", "用户"), True)
    t("名称前缀默认带", "角色: 第二条" in scan_text(msgs, 3, "角色", "用户"), True)
    t("关掉名称前缀", ": " not in scan_text(msgs, 1, include_names=False), True)

    # ⑦ 独立世界书形状（驼峰字段）与卡侧形状解析等价
    wi = {"uid": 5, "key": ["示例键"], "keysecondary": [], "comment": "示例键", "content": "xx",
          "constant": False, "selective": False, "order": 290, "position": 0, "disable": False,
          "probability": 100, "useProbability": True, "scanDepth": 3,
          "caseSensitive": False, "matchWholeWords": False}
    n = normalize(wi)
    t("WI 侧 key", n["key"], ["示例键"])
    t("WI 侧 position=0 → before_char", n["position"], 0)
    t("WI 侧 order", n["order"], 290)
    t("WI 侧 scanDepth", n["scan_depth"], 3)
    t("WI 侧 disable=False → enabled", n["enabled"], True)
    t("WI 侧 disable=True → 禁用", normalize({**wi, "disable": True})["enabled"], False)
    card_e = {"id": 5, "keys": ["示例键"], "secondary_keys": [], "insertion_order": 290,
              "position": "before_char", "enabled": True, "comment": "示例键", "content": "xx"}
    t("卡侧/ WI 侧 解析一致",
      [normalize(card_e)[k] for k in ("key", "position", "order", "enabled")],
      [n[k] for k in ("key", "position", "order", "enabled")])
    t("entries 字典形状可枚举", len(raw_entries({"entries": {"0": wi, "1": wi}})), 2)
    t("生成模板字段齐备",
      all(k in WI_TEMPLATE for k in ("key", "keysecondary", "role", "sticky", "triggers", "position")), True)

    if failed:
        print(f"❌ 自测 {passed} 通过 / {len(failed)} 失败")
        for f in failed:
            print(f"   {f}")
        return 1
    print(f"✅ 自测全过（{passed} 项）——判定链与 world-info.js 规则一致")
    return 0


def _sim_one(path, label, msgs, char_name, user_name, include_names, depth, budget):
    """对一本书跑一遍触发模拟，返回本次注入 token。"""
    kind, book, card = load_book(path)
    ents = entries_of(path)
    print(f"───── {label}：{os.path.basename(path)} [{kind}] ─────")
    if not ents:
        print("   （没有条目）\n")
        return 0
    fired, blocked, miss = [], [], []
    for e in ents:
        d = e["scan_depth"] if e["scan_depth"] is not None else depth
        text = scan_text(msgs, d, char_name, user_name, include_names)
        verdict, detail = evaluate_entry(e, text, char_name, user_name)
        if verdict in ("constant", "active"):
            fired.append((e, verdict, detail))
        elif verdict == "blocked-secondary":
            blocked.append((e, detail))
        else:
            miss.append((e, verdict))
    print(f"── 触发 {len(fired)} 条" + ("（含常驻）" if any(v == 'constant' for _, v, _ in fired) else "") + " ──")
    for e, v, d in sorted(fired, key=lambda x: -(x[0]["order"] or 0)):
        how = "常驻" if v == "constant" else f"主键 '{d['hit']}'"
        if v == "active" and d.get("secondary"):
            how += f" + 二级 {d['secondary']}（{LOGIC.get(e['logic'])}）"
        print(f"   ✅ [{e['uid']:>3}] ord={str(e['order']):>4} {POSITION.get(e['position']):<11} "
              f"{e['comment'][:20]:<22} ← {how}")
    if blocked:
        print(f"\n── 主键命中但被二级键拦住 {len(blocked)} 条 ──")
        for e, d in blocked:
            print(f"   ⛔ [{e['uid']:>3}] {e['comment'][:20]:<22} 主键 '{d['hit']}' 命中，"
                  f"但 {d['logic']} 未满足（二级 {'/'.join(e['keysecondary'])}，命中 {d['matched'] or '无'}）")
    if miss:
        print(f"\n── 未触发 {len(miss)} 条 ──")
        for e, v in miss:
            why = {"no-primary": "主键没出现", "no-keys": "没写主键", "disabled": "已禁用"}.get(v, v)
            print(f"   ·  [{e['uid']:>3}] {e['comment'][:20]:<22} {why}")
    total = sum(token_count(e["content"]) for e, _, _ in fired)
    print("\n　注入顺序（弱→强，按 position 分组 / order 升序）：")
    bypos = {}
    for e, _, _ in fired:
        bypos.setdefault(e["position"], []).append(e)
    for pos in sorted(bypos, key=lambda x: (x not in (0, 1), x)):
        grp = sorted(bypos[pos], key=lambda x: x["order"] or 0)
        print(f"   {POSITION.get(pos):<12} " + " → ".join(f"#{e['uid']}" for e in grp))
    print(f"\n　本册注入 {total} token\n")
    return total


def cmd_sim(args):
    G["max_context"] = args.max_context
    kind, _book, card = load_book(args.card)
    data = (card or {}).get("data") or {}
    char_name = args.char_name or data.get("name") or "{{char}}"
    user_name = args.user_name or "{{user}}"
    include_names = not args.no_names
    msgs = [(user_name if i % 2 == 0 else char_name, t) for i, t in enumerate(list(args.prev or []) + [args.text])]
    budget = int(round(G["budget_pct"] * G["max_context"] / 100)) or 1
    if G["budget_cap"] > 0:
        budget = min(budget, G["budget_cap"])

    sources = [(args.card, "卡内书")] + [(b, "独立书") for b in (args.book or [])]
    print(f"扫描深度 {args.depth}{'' if include_names else ' / 无名称前缀'}  角色名 {char_name}  用户名 {user_name}")
    print("消息（旧→新）:")
    for n, t in msgs:
        print(f"   {n}: {t}")
    print()
    total = 0
    for path, label in sources:
        total += _sim_one(path, label, msgs, char_name, user_name, include_names, args.depth, budget)
    if len(sources) > 1:
        print(f"　合计注入 {total} token / 预算 {budget} token" + ("  ⚠️ 超预算" if total > budget else ""))
    else:
        print(f"　预算 {budget} token" + ("  ⚠️ 超预算" if total > budget else ""))
    return 0


# ── 生成独立世界书（从条目清单）──────────────────────────────────────────
WI_TEMPLATE = {
    "key": [], "keysecondary": [], "comment": "", "content": "", "constant": False,
    "vectorized": False, "selective": False, "selectiveLogic": 0, "addMemo": True,
    "order": 100, "position": 0, "disable": False, "ignoreBudget": False,
    "excludeRecursion": False, "preventRecursion": False, "delayUntilRecursion": False,
    "matchPersonaDescription": False, "matchCharacterDescription": False,
    "matchCharacterPersonality": False, "matchCharacterDepthPrompt": False,
    "matchScenario": False, "matchCreatorNotes": False, "probability": 100,
    "useProbability": True, "depth": DEFAULT_DEPTH, "outletName": "", "group": "",
    "groupOverride": False, "groupWeight": DEFAULT_WEIGHT, "scanDepth": None,
    "caseSensitive": None, "matchWholeWords": None, "useGroupScoring": None,
    "automationId": "", "role": 0, "sticky": None, "cooldown": None, "delay": None,
    "triggers": [],
}


def cmd_new(args):
    """从条目清单（JSON）生成独立世界书文件。"""
    spec = load(args.spec)
    items = spec.get("entries") or []
    out = {"entries": {}}
    for i, sp in enumerate(items):
        e = dict(WI_TEMPLATE)
        e["uid"] = i
        keys = list(sp.get("keys") or sp.get("key") or [])
        e["key"] = keys
        e["keysecondary"] = list(sp.get("secondary_keys") or sp.get("keysecondary") or [])
        e["comment"] = sp.get("comment") or (keys[0] if keys else f"entry{i}")
        e["content"] = sp.get("content") or ""
        e["order"] = sp.get("order", 100)
        pos = sp.get("position", "before_char")
        e["position"] = pos if isinstance(pos, int) else (0 if pos == "before_char" else 1)
        e["constant"] = bool(sp.get("constant", False))
        e["selective"] = bool(sp.get("selective", False))
        e["disable"] = bool(sp.get("disable", False))
        for k in ("depth", "scanDepth", "caseSensitive", "matchWholeWords", "group",
                  "probability", "useProbability", "role", "sticky", "cooldown", "delay"):
            if sp.get(k) is not None:
                e[k] = sp[k]
        cf = sp.get("character_filter")
        if cf:
            e["characterFilter"] = {"isExclude": bool(cf.get("is_exclude")),
                                    "names": list(cf.get("names") or []),
                                    "tags": list(cf.get("tags") or [])}
        out["entries"][str(i)] = e

    dst = args.out or (os.path.splitext(args.spec)[0] + ".json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    stem = os.path.splitext(os.path.basename(dst))[0]
    tok = sum(token_count(sp.get("content") or "") for sp in items)
    print(f"✅ 生成 {dst}")
    print(f"   条目 {len(items)}  常驻 {sum(1 for sp in items if sp.get('constant'))}  token 合计 {tok}")
    print(f"   16 张卡写：extensions.world = {stem!r}"
          f"{'  ⚠️ 含非 ASCII 字符，导入后可能变乱码、关联断链' if not stem.isascii() else ''}")
    bad = sorted({k for sp in items for k in (sp.get("keys") or sp.get("key") or [])
                  if k in GENERIC_KEYS or len(k) < 2})
    if bad:
        print(f"   ⚠️ 主键里混进通用词/单字：{'、'.join(bad)}")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="worldbook_tools", description="世界书机制引擎（ST 1.18.0 口径；1.19.0 复核判定链未变）")
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("ls", help="世界书台账"); p.add_argument("cards", nargs="+")
    p.set_defaults(func=cmd_ls)

    p = sp.add_parser("keys", help="关键词矩阵"); p.add_argument("cards", nargs="+")
    p.set_defaults(func=cmd_keys)

    p = sp.add_parser("check", help="触发体检")
    p.add_argument("cards", nargs="+")
    p.add_argument("--max-context", type=int, default=G["max_context"])
    p.set_defaults(func=cmd_check, all=False)

    p = sp.add_parser("sim", help="触发模拟")
    p.add_argument("card")
    p.add_argument("text")
    p.add_argument("--depth", type=int, default=G["depth"])
    p.add_argument("--char-name"); p.add_argument("--user-name")
    p.add_argument("--prev", action="append")
    p.add_argument("--no-names", action="store_true")
    p.add_argument("--book", action="append", help="再挂一本独立世界书一起模拟（可重复）")
    p.add_argument("--max-context", type=int, default=G["max_context"])
    p.set_defaults(func=cmd_sim)

    p = sp.add_parser("new", help="从条目清单（JSON）生成独立世界书")
    p.add_argument("spec")
    p.add_argument("--out")
    p.set_defaults(func=cmd_new)

    p = sp.add_parser("selftest", help="机制判定自测（对拍源码规则）")
    p.set_defaults(func=cmd_selftest)

    p = sp.add_parser("unfilter", help="去掉 selective 隐形门槛（二级键分流：并入主键／剔除）")
    p.add_argument("cards", nargs="+")
    p.add_argument("--fix", action="store_true", help="写回（默认只预览）")
    p.add_argument("--no-bump", action="store_true", help="不推版本、不加 creator_notes")
    p.set_defaults(func=cmd_unfilter)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
