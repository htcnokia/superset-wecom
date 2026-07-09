#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Superset 翻译自动补全脚本（v2）
以英文 .po 为基准，对比目标语言中缺失的条目，调用 Google Translate 翻译补全。
支持：
  - 基于英文翻译任意目标语言
  - 用 OpenCC 从简体生成台湾正体（zh_TW）
  - 编译 .po → .mo（后端）和 .po → .json（前端 JED 格式）

用法：
  # 补全简体中文
  python translate_po.py --translations-dir /app/superset/translations --target-lang zh-CN --target-po zh

  # 从简体生成繁体
  python translate_po.py --translations-dir /app/superset/translations --generate-zh-tw

  # 补全日语
  python translate_po.py --translations-dir /app/superset/translations --target-lang ja --target-po ja
"""

import os
import re
import sys
import json
import time
import argparse
import subprocess
from pathlib import Path
from copy import deepcopy


# ==========================================
# 1. PO 文件解析与写入
# ==========================================

def parse_po_entries(filepath):
    """
    解析 .po 文件，返回 list of dict:
    {
        'msgid': str,
        'msgstr': str,
        'msgid_plural': str,
        'msgstr_plural': dict,
        'comments': list,       # 所有注释行（#, #: #. #）
        'is_fuzzy': bool,
        'is_header': bool,      # 第一个空 msgid 的 header 条目
        'raw_block': str,       # 原始文本块（用于保留格式）
    }
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    entries = []
    blocks = re.split(r'\n\n+', content.strip())

    for block in blocks:
        lines = block.strip().split('\n')
        if not lines:
            continue

        entry = {
            'msgid': '',
            'msgstr': '',
            'msgid_plural': '',
            'msgstr_plural': {},
            'comments': [],
            'is_fuzzy': False,
            'is_header': False,
            'raw_block': block,
        }

        current_field = None
        for line in lines:
            if line.startswith('#'):
                entry['comments'].append(line)
                if 'fuzzy' in line:
                    entry['is_fuzzy'] = True
            elif line.startswith('msgid_plural '):
                current_field = 'msgid_plural'
                entry['msgid_plural'] = _extract_quoted(line)
            elif line.startswith('msgid '):
                current_field = 'msgid'
                entry['msgid'] = _extract_quoted(line)
            elif line.startswith('msgstr['):
                current_field = 'msgstr_plural'
                m = re.match(r'msgstr\[(\d+)\]\s+"(.*)"', line)
                if m:
                    entry['msgstr_plural'][int(m.group(1))] = _unescape(m.group(2))
            elif line.startswith('msgstr '):
                current_field = 'msgstr'
                entry['msgstr'] = _extract_quoted(line)
            elif line.startswith('"') and line.endswith('"'):
                # continuation line
                val = _unescape(line[1:-1])
                if current_field == 'msgid':
                    entry['msgid'] += val
                elif current_field == 'msgstr':
                    entry['msgstr'] += val
                elif current_field == 'msgid_plural':
                    entry['msgid_plural'] += val
                elif current_field == 'msgstr_plural':
                    # find the last plural index
                    last_idx = max(entry['msgstr_plural'].keys()) if entry['msgstr_plural'] else 0
                    entry['msgstr_plural'][last_idx] = entry['msgstr_plural'].get(last_idx, '') + val

        if not entry['msgid'] and not entry['msgstr']:
            entry['is_header'] = True

        entries.append(entry)

    return entries


def _extract_quoted(line):
    """从 msgid/msgstr 行提取引号内内容并反转义"""
    m = re.match(r'(?:msgid|msgid_plural|msgstr)\s+"(.*)"', line)
    if m:
        return _unescape(m.group(1))
    return ''


def _unescape(s):
    """反转义 PO 字符串"""
    return s.replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n').replace('\\t', '\t')


def _escape(s):
    """转义字符串为 PO 格式"""
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\t', '\\t')


def build_po_content(entries):
    """将 entries 重新组装为 .po 文件内容"""
    blocks = []
    for entry in entries:
        lines = []

        # 注释
        for c in entry['comments']:
            # 去掉 fuzzy 标记（如果已经翻译了）
            if not entry['is_fuzzy'] and c.startswith('#,'):
                flags = [f.strip() for f in c[2:].split(',') if f.strip() != 'fuzzy']
                if flags:
                    lines.append('#, ' + ', '.join(flags))
                # else: skip the fuzzy line
            else:
                lines.append(c)

        # msgid
        if entry['msgid']:
            escaped = _escape(entry['msgid'])
            if '\n' in entry['msgid'] and len(entry['msgid']) > 70:
                lines.append('msgid ""')
                for part in escaped.split('\\n'):
                    if part:
                        lines.append(f'"{part}\\n"')
            else:
                lines.append(f'msgid "{escaped}"')
        else:
            lines.append('msgid ""')

        # msgid_plural
        if entry['msgid_plural']:
            lines.append(f'msgid_plural "{_escape(entry["msgid_plural"])}"')

        # msgstr
        if entry['msgstr_plural']:
            for idx in sorted(entry['msgstr_plural'].keys()):
                lines.append(f'msgstr[{idx}] "{_escape(entry["msgstr_plural"][idx])}"')
        else:
            lines.append(f'msgstr "{_escape(entry["msgstr"])}"')

        blocks.append('\n'.join(lines))

    return '\n\n'.join(blocks) + '\n'


# ==========================================
# 2. 翻译过滤逻辑
# ==========================================

def should_translate(msgid):
    """判断一个 msgid 是否需要翻译"""
    if not msgid or not msgid.strip():
        return False

    # 跳过纯占位符 / 纯符号
    if re.match(r'^[%\(\)\[\]{}s\d\s.,;:!?/\\@#$^&*+=<>|~`"\'+\-]+$', msgid):
        return False

    # 跳过含 %(xxx)s 变量的条目（翻译容易破坏占位符格式）
    if re.search(r'%\([^)]+\)[sdifFeEgGcrb%]', msgid):
        return False

    # 跳过含 {xxx} 格式变量的条目
    if re.search(r'\{[^}]+\}', msgid):
        return False

    # 跳过纯数字、纯标点、纯空白
    if re.match(r'^[\d\s\W]+$', msgid):
        return False

    # 跳过 HTML/XML 标签
    if re.match(r'^<[^>]+>$', msgid.strip()):
        return False

    # 跳过 CSS 类名、JSON key 等技术标识符
    if re.match(r'^[a-z_\-\.]+$', msgid):
        return False

    # 跳过纯大写缩写（如 API、SQL、CSV 等）
    if re.match(r'^[A-Z]{1,6}$', msgid):
        return False

    # 跳过文件路径
    if re.match(r'^[/\\]', msgid) or re.match(r'^[a-zA-Z]:\\', msgid):
        return False

    # 跳过 URL
    if re.match(r'^https?://', msgid):
        return False

    return True


# ==========================================
# 3. Google Translate
# ==========================================

def translate_batch(texts, source_lang='en', target_lang='zh-CN', batch_size=15):
    """批量翻译"""
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        print("ERROR: deep-translator not installed. Run: pip install deep-translator")
        sys.exit(1)

    translator = GoogleTranslator(source=source_lang, target=target_lang)
    results = {}
    total = len(texts)

    for i in range(0, total, batch_size):
        batch = texts[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} items)...")

        try:
            translated = translator.translate_batch(batch)
            for original, translation in zip(batch, translated):
                if translation:
                    results[original] = translation
                else:
                    results[original] = original
        except Exception as e:
            print(f"  Batch failed: {e}, falling back to single translation...")
            for text in batch:
                try:
                    result = translator.translate(text)
                    results[text] = result if result else text
                    time.sleep(0.5)
                except Exception as e2:
                    print(f"    Failed: {text[:50]}... ({e2})")
                    results[text] = text

        if i + batch_size < total:
            time.sleep(1.5)

    return results


# ==========================================
# 4. OpenCC 简繁转换
# ==========================================

def convert_s2t(source_po_path, output_po_path):
    """用 OpenCC 将简体 .po 转换为台湾正体"""
    import opencc
    converter = opencc.OpenCC('s2t')

    entries = parse_po_entries(source_po_path)

    for entry in entries:
        if entry['is_header']:
            # 修改头部语言标识
            for i, c in enumerate(entry['comments']):
                entry['comments'][i] = c.replace('Language: zh\\n', 'Language: zh_TW\\n')
                entry['comments'][i] = entry['comments'][i].replace('Language: zh_CN\\n', 'Language: zh_TW\\n')
            continue

        if entry['msgstr']:
            entry['msgstr'] = converter.convert(entry['msgstr'])

        for idx in entry['msgstr_plural']:
            if entry['msgstr_plural'][idx]:
                entry['msgstr_plural'][idx] = converter.convert(entry['msgstr_plural'][idx])

    content = build_po_content(entries)
    os.makedirs(os.path.dirname(output_po_path), exist_ok=True)
    with open(output_po_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"  ✓ Converted {source_po_path} → {output_po_path} (s2t)")


# ==========================================
# 5. 编译
# ==========================================

def compile_mo(translations_dir):
    print("\n[Compiling .po → .mo]")
    result = subprocess.run(
        ['pybabel', 'compile', '-d', translations_dir],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("  ✓ .mo compilation successful")
    else:
        print(f"  ⚠ .mo warnings (non-fatal): {result.stderr[:300]}")


def compile_json(translations_dir):
    print("\n[Compiling .po → .json (JED format)]")
    po_files = list(Path(translations_dir).rglob('messages.po'))

    for po_file in sorted(po_files):
        json_file = po_file.with_suffix('.json')
        if json_file.exists():
            json_file.unlink()

        result = subprocess.run(
            ['po2json', '--domain', 'superset', '--format', 'jed1.x', '--fuzzy',
             str(po_file), str(json_file)],
            capture_output=True, text=True
        )
        lang = po_file.parent.parent.name
        if result.returncode == 0:
            print(f"  ✓ {lang}/messages.json")
            subprocess.run(['prettier', '--write', str(json_file)],
                         capture_output=True, text=True)
        else:
            print(f"  ✗ {lang}: {result.stderr[:200]}")


# ==========================================
# 6. 主流程
# ==========================================

def main():
    parser = argparse.ArgumentParser(description='Superset translation auto-completion (v2)')
    parser.add_argument('--translations-dir', required=True)
    parser.add_argument('--source-po', default='en', help='Source .po directory name (default: en)')
    parser.add_argument('--target-po', default=None, help='Target .po directory name (e.g. zh, ja, ko)')
    parser.add_argument('--target-lang', default=None, help='Google Translate target language code (e.g. zh-CN, ja, ko)')
    parser.add_argument('--generate-zh-tw', action='store_true', help='Generate zh_TW from zh using OpenCC')
    parser.add_argument('--batch-size', type=int, default=15)
    parser.add_argument('--skip-translate', action='store_true', help='Skip translation, only compile')
    args = parser.parse_args()

    td = args.translations_dir
    en_po_path = os.path.join(td, args.source_po, 'LC_MESSAGES', 'messages.po')

    if not os.path.exists(en_po_path):
        print(f"ERROR: English source not found: {en_po_path}")
        sys.exit(1)

    # ---- Step 1: 解析英文基准 ----
    print(f"\n[1] Parsing English source: {en_po_path}")
    en_entries = parse_po_entries(en_po_path)
    en_map = {}
    for entry in en_entries:
        if entry['msgid'] and not entry['is_header']:
            en_map[entry['msgid']] = entry
    print(f"  Found {len(en_map)} translatable entries in English")

    # ---- Step 2: 翻译目标语言 ----
    if args.target_po and args.target_lang and not args.skip_translate:
        target_po_path = os.path.join(td, args.target_po, 'LC_MESSAGES', 'messages.po')

        if not os.path.exists(target_po_path):
            print(f"\n[2] Target .po not found, creating from English template: {target_po_path}")
            os.makedirs(os.path.dirname(target_po_path), exist_ok=True)
            # 复制英文作为模板
            import shutil
            shutil.copy2(en_po_path, target_po_path)
            # 清空所有 msgstr
            entries = parse_po_entries(target_po_path)
            for entry in entries:
                if not entry['is_header']:
                    entry['msgstr'] = ''
                    entry['msgstr_plural'] = {}
            with open(target_po_path, 'w', encoding='utf-8') as f:
                f.write(build_po_content(entries))

        print(f"\n[2] Comparing English vs {args.target_po}...")
        target_entries = parse_po_entries(target_po_path)
        target_map = {}
        for entry in target_entries:
            if entry['msgid'] and not entry['is_header']:
                target_map[entry['msgid']] = entry

        # 找出需要翻译的条目
        to_translate = []
        for msgid in en_map:
            if not should_translate(msgid):
                continue
            if msgid not in target_map:
                # 英文有但目标语言完全没有这个条目
                to_translate.append(msgid)
            elif not target_map[msgid]['msgstr'] or target_map[msgid]['msgstr'] == msgid:
                # 有条目但 msgstr 为空或与原文相同
                to_translate.append(msgid)

        print(f"  Need to translate: {len(to_translate)} entries")

        if to_translate:
            print(f"\n[3] Translating {len(to_translate)} entries (en → {args.target_lang})...")
            translations = translate_batch(
                to_translate,
                source_lang='en',
                target_lang=args.target_lang,
                batch_size=args.batch_size
            )

            updated = 0
            for msgid in to_translate:
                translated = translations.get(msgid, '')
                if translated and translated != msgid:
                    if msgid in target_map:
                        target_map[msgid]['msgstr'] = translated
                        target_map[msgid]['is_fuzzy'] = False
                    else:
                        # 新增条目
                        new_entry = {
                            'msgid': msgid,
                            'msgstr': translated,
                            'msgid_plural': en_map[msgid].get('msgid_plural', ''),
                            'msgstr_plural': {},
                            'comments': en_map[msgid].get('comments', []),
                            'is_fuzzy': False,
                            'is_header': False,
                            'raw_block': '',
                        }
                        target_entries.append(new_entry)
                        target_map[msgid] = new_entry
                    updated += 1

            print(f"  ✓ Translated {updated}/{len(to_translate)} entries")

            # 写回文件
            with open(target_po_path, 'w', encoding='utf-8') as f:
                f.write(build_po_content(target_entries))
            print(f"  ✓ Saved to {target_po_path}")
        else:
            print("  All entries already translated!")

    elif args.skip_translate:
        print("\n[2-3] Skipping translation (--skip-translate)")

    # ---- Step 4: 生成繁体中文 ----
    if args.generate_zh_tw:
        print(f"\n[4] Generating zh_TW from zh using OpenCC...")
        zh_po = os.path.join(td, 'zh', 'LC_MESSAGES', 'messages.po')
        zh_tw_po = os.path.join(td, 'zh_TW', 'LC_MESSAGES', 'messages.po')

        if not os.path.exists(zh_po):
            print(f"  ERROR: zh source not found: {zh_po}")
        else:
            convert_s2t(zh_po, zh_tw_po)
    else:
        print("\n[4] Skipping zh_TW generation (use --generate-zh-tw to enable)")

    # ---- Step 5 & 6: 编译 ----
    compile_mo(td)
    compile_json(td)

    print("\n✅ All done! Restart Superset to apply changes.")


if __name__ == '__main__':
    main()
