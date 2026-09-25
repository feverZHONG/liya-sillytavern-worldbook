#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""st-version-diff —— SillyTavern 两版本的世界书机制差异速查。

用途：上游发新版 → 一条命令看清「世界书机制动了没有」，替代手工 diff + 目视。
判据：判定链（匹配/selective/位置/预算/递归）只要「默认值常量、convertCharacterBook 字段集、
      关键函数与行号、world-info.js 实质 hunk」四项无实质差异，机制即未变。

用法：
    # 自动下载两个 tag（走 codeload；raw.githubusercontent 本机不通）
    python3 st-version-diff.py --tag-a 1.18.0 --tag-b 1.19.0

    # 用已解压的源码目录
    python3 st-version-diff.py --a /path/to/1.18.0 --b /path/to/1.19.0

    # 只看 world-info.js 的 hunk 明细
    python3 st-version-diff.py --tag-a 1.18.0 --tag-b 1.19.0 --hunks
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

FILES = [
    'public/scripts/world-info.js',
    'src/endpoints/characters.js',
    'src/endpoints/worldinfo.js',
]
CACHE = os.path.expanduser('~/.cache/st-src')


def fetch(tag):
    """下 codeload tar.gz 并解出需要的文件，返回源码根目录。"""
    root = os.path.join(CACHE, tag)
    marker = os.path.join(root, 'public/scripts/world-info.js')
    if os.path.exists(marker):
        return root
    os.makedirs(os.path.dirname(root), exist_ok=True)
    url = f'https://codeload.github.com/SillyTavern/SillyTavern/tar.gz/refs/tags/{tag}'
    tmp = tempfile.mkdtemp(prefix='stsrc-')
    tgz = os.path.join(tmp, 'st.tar.gz')
    print(f'  ↓ 下载 {tag} …', file=sys.stderr)
    subprocess.run(['curl', '-sL', '--max-time', '600', '-o', tgz, url], check=True)
    for f in FILES:
        subprocess.run(['tar', 'xzf', tgz, '-C', tmp, '--wildcards', f'*/{f}'], check=False)
    inner = [d for d in os.listdir(tmp) if d.startswith('SillyTavern-')]
    if not inner:
        sys.exit(f'解包失败：{tag}')
    src = os.path.join(tmp, inner[0])
    os.makedirs(root, exist_ok=True)
    subprocess.run(f'cp -r "{src}/." "{root}/"', shell=True, check=True)
    return root


def read(path):
    with open(path, encoding='utf-8', errors='replace') as fh:
        return fh.read()


def fn_body(text, name, span=8000):
    for pat in (f'export function {name}', f'export async function {name}', f'{name}(haystack'):
        i = text.find(pat)
        if i >= 0:
            seg = text[i:i + span]
            m = re.search(r'\n(?:export )?(?:async )?function ', seg[10:])
            return seg[:m.start() + 10] if m else seg
    return ''


def report(a_root, b_root, tag_a, tag_b, show_hunks):
    print(f'═══ {tag_a} → {tag_b} 世界书机制复核 ═══\n')
    verdict = []

    # 1) 文件行数
    print('【1】文件规模')
    for f in FILES:
        pa, pb = os.path.join(a_root, f), os.path.join(b_root, f)
        la = len(read(pa).split('\n')) if os.path.exists(pa) else -1
        lb = len(read(pb).split('\n')) if os.path.exists(pb) else -1
        print(f'    {f}:  {la} → {lb}  ({lb - la:+d})')
    print()

    wa, wb = read(os.path.join(a_root, FILES[0])), read(os.path.join(b_root, FILES[0]))

    # 2) 默认值常量
    print('【2】全局默认值常量（判定链的地基）')
    def consts(text):
        d = {}
        for m in re.finditer(r'world_info_(\w+) = ([^;]+);', text):
            k, v = m.group(1), m.group(2).strip()
            if not v.startswith(('Number', 'Boolean', 'String')):
                d[k] = v
        return d
    ca, cb = consts(wa), consts(wb)
    diff = {k for k in set(ca) | set(cb) if ca.get(k) != cb.get(k)}
    if diff:
        for k in sorted(diff):
            print(f'    ⚠️ {k}: {ca.get(k)} → {cb.get(k)}')
        verdict.append('默认值有变动')
    else:
        print(f'    ✅ 一致（{len(ca)} 项）')
    print()

    # 3) convertCharacterBook 字段集
    print('【3】convertCharacterBook 字段集（卡内书读什么）')
    def fields(text):
        seg = fn_body(text, 'convertCharacterBook')
        return set(re.findall(r'^\s{12}(\w+):', seg, re.M))
    fa, fb = fields(wa), fields(wb)
    print(f'    字段数 {len(fa)} → {len(fb)}')
    if fb - fa:
        print('    ⚠️ 新增字段：', sorted(fb - fa))
        verdict.append('卡内书字段有新增')
    if fa - fb:
        print('    ⚠️ 移除字段：', sorted(fa - fb))
        verdict.append('卡内书字段有移除')
    if fa == fb:
        print('    ✅ 逐字相同')
    print()

    # 4) 关键函数行号 + 函数体是否变
    print('【4】关键函数行号 / 函数体')
    names = ['parseRegexFromString', 'getSortedEntries', 'checkWorldInfo', 'convertCharacterBook']
    lines_a = wa.split('\n')
    lines_b = wb.split('\n')
    for n in names:
        ra = next((i for i, l in enumerate(lines_a, 1) if l.startswith(('export function ' + n,
                    'export async function ' + n))), -1)
        rb = next((i for i, l in enumerate(lines_b, 1) if l.startswith(('export function ' + n,
                    'export async function ' + n))), -1)
        same = '体同' if fn_body(wa, n) == fn_body(wb, n) else '⚠️ 体变'
        print(f'    {n}:  :{ra} → :{rb}   {same}')
        if same != '体同' and n in ('parseRegexFromString', 'convertCharacterBook'):
            verdict.append(f'{n} 函数体有变，需人工看')
    mk_a, mk_b = fn_body(wa, 'matchKeys'), fn_body(wb, 'matchKeys')
    print(f'    matchKeys: 体{"同 ✅" if mk_a == mk_b else "⚠️ 变"}')
    if mk_a != mk_b:
        verdict.append('matchKeys 有变（匹配逻辑高危）')
    print()

    # 5) 整文件 hunk
    print('【5】world-info.js 实质改动')
    d = subprocess.run(['diff', '-u', os.path.join(a_root, FILES[0]), os.path.join(b_root, FILES[0])],
                       capture_output=True, text=True).stdout
    hunks = re.findall(r'^@@.*$', d, re.M)
    add = sum(1 for l in d.split('\n') if l.startswith('+') and not l.startswith('+++'))
    rem = sum(1 for l in d.split('\n') if l.startswith('-') and not l.startswith('---'))
    print(f'    hunk {len(hunks)} 个 / +{add} −{rem} 行')
    for h in hunks:
        print(f'      {h}')
    if show_hunks:
        print()
        print(d)
    elif hunks:
        print('\n    （--hunks 看明细）')
    print()

    # 结论
    print('═══ 判定 ═══')
    if not verdict:
        print('✅ 机制层未发现实质改动（判定链/字段/默认值/函数体全一致）——条目设计与模拟器无需调整')
        print('   提醒：逐一过一遍 hunk 明细，确认改动都落在 UI/性能/链接同步这类外围（--hunks）')
    else:
        print('⚠️ 需要人工判断：' + '；'.join(verdict))
        print('   → 逐条对照 references/12-worldbook-mechanics.md，改完跑 bin/wb selftest')


def main():
    ap = argparse.ArgumentParser(description='SillyTavern 两版本世界书机制差异速查')
    ap.add_argument('--a', help='旧版本源码根目录')
    ap.add_argument('--b', help='新版本源码根目录')
    ap.add_argument('--tag-a', help='旧版本 tag（自动下载，如 1.18.0）')
    ap.add_argument('--tag-b', help='新版本 tag（自动下载，如 1.19.0）')
    ap.add_argument('--hunks', action='store_true', help='打印 diff 明细')
    args = ap.parse_args()

    if args.a and args.b:
        a_root, b_root, ta, tb = args.a, args.b, args.a, args.b
    elif args.tag_a and args.tag_b:
        a_root, b_root = fetch(args.tag_a), fetch(args.tag_b)
        ta, tb = args.tag_a, args.tag_b
    else:
        ap.error('给 --a/--b 或用 --tag-a/--tag-b')
        return
    report(a_root, b_root, ta, tb, args.hunks)


if __name__ == '__main__':
    main()
