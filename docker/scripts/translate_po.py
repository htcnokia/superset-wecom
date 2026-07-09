#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Superset 翻译自动补全脚本
功能：
  1. 扫描 .po 文件中未翻译的条目，调用 Google Translate 免费接口翻译
  2. 基于简体中文用 OpenCC 生成台湾正体（zh_TW）
  3. 编译 .po → .mo（后端）和 .po → .json（前端 JED 格式）

用法：
  python translate_po.py --translations-dir /app/superset/translations
"""

import os
import re
import sys
import json
import time
import argparse
import subprocess
from pathlib import Path

# ==========================================
# 1. PO 文件解析与写入
# ==========================================

def parse_po_file(filepath):
    """解析 .po 文件，返回 (header, entries)
    entries 是 list of dict: {msgid, msgstr, msgctxt, comment, references, flags, is_fuzzy}
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    entries = []
    current = {
        'comment': [],
        'references': [],
        'flags': [],
        'msgctxt': '',
        'msgid': '',
        'msgid_plural': '',
        'msgstr': '',
        'msgstr_plural': {},
        'is_fuzzy': False,
        'raw_lines': []
    }

    lines = content.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.startswith('#,'):
            current['flags'].append(line[2:].strip())
            if 'fuzzy' in line:
                current['is_fuzzy'] = True
            current['raw_lines'].append(line)
        elif line.startswith('#:'):
            current['references'].append(line[2:].strip())
            current['raw_lines'].append(line)
        elif line.startswith('#.'):
            current['comment'].append(line[2:].strip())
            current['raw_lines'].append(line)
        elif line.startswith('#'):
            current['raw_lines'].append(line)
        elif line.startswith('msgctxt '):
            current['msgctxt'] = extract_string(line)
            current['raw_lines'].append(line)
        elif line.startswith('msgid_plural '):
            current['msgid_plural'] = extract_string(line)
            current['raw_lines'].append(line)
        elif line.startswith('msgid '):
            current['msgid'] = extract_string(line)
            current['raw_lines'].append(line)
        elif line.startswith('msgstr['):
            # plural form
            match = re.match(r'msgstr\[(\d+)\]\s+"(.*)"', line)
            if match:
                idx = int(match.group(1))
                val = match.group(2)
                current['msgstr_plural'][idx] = val
            current['raw_lines'].append(line)
        elif line.startswith('msgstr '):
            current['msgstr'] = extract_string(line)
            current['raw_lines'].append(line)
        elif line.startswith('"') and line.endswith('"'):
            # continuation line
            if current['raw_lines']:
                current['raw_lines'][-1] += '\n' + line
            # append to the last field
            cont = line[1:-1]
            if current['msgstr'] and not current['msgstr_plural']:
                current['msgstr'] += cont
            elif current['msgid'] and not current['msgstr'] and not current['msgstr_plural']:
                current['msgid'] += cont
            elif current['msgid_plural']:
                current['msgid_plural'] += cont
        elif line.strip() == '':
            # blank line = entry separator
            if current['msgid'] or current['msgstr'] or current['raw_lines']:
                entries.append(current)
            current = {
                'comment': [],
                'references': [],
                'flags': [],
                'msgctxt': '',
                'msgid': '',
                'msgid_plural': '',
                'msgstr': '',
                'msgstr_plural': {},
                'is_fuzzy': False,
                'raw_lines': []
            }
        else:
            current['raw_lines'].append(line)

        i += 1

    # last entry
    if current['msgid'] or current['msgstr'] or current['raw_lines']:
        entries.append(current)

    return entries


def extract_string(line):
    """从 msgid/msgstr 行中提取引号内的字符串"""
    match = re.match(r'(?:msgctxt|msgid|msgid_plural|msgstr)\s+"(.*)"', line)
    if match:
        return match.group(1)
    return ''


def is_untranslated(entry):
    """判断条目是否需要翻译"""
    if not entry['msgid']:
        return False
    
    msgid = entry['msgid']
    
    # 跳过纯占位符（如 %(name)s）
    if re.match(r'^[%\(\)\[\]{}s\d\s.,;:!?/\\@#$^&*+=<>|~`"\']+$', msgid):
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
    
    # 跳过 HTML/XML 标签（如 <br>、<div>）
    if re.match(r'^<[^>]+>$', msgid):
        return False
    
    # 跳过 CSS 类名、JSON key 等技术标识符（全小写+下划线/连字符）
    if re.match(r'^[a-z_\-]+$', msgid):
        return False
    
    # msgstr 为空 或 与 msgid 完全相同（未翻译）
    if not entry['msgstr']:
        return True
    if entry['msgstr'] == entry['msgid']:
        return True
    
    return False



def rebuild_po(entries, filepath):
    """将 entries 重新写回 .po 文件"""
    with open(filepath, 'w', encoding='utf-8') as f:
        for i, entry in enumerate(entries):
            for line in entry['raw_lines']:
                f.write(line + '\n')
            if i < len(entries) - 1:
                f.write('\n')


def update_msgstr_in_raw_lines(entry, new_msgstr):
    """更新 entry 的 raw_lines 中的 msgstr 值"""
    escaped = new_msgstr.replace('\\', '\\\\').replace('"', '\\"')
    new_lines = []
    in_msgstr = False
    msgstr_written = False

    for line in entry['raw_lines']:
        if line.startswith('msgstr '):
            new_lines.append(f'msgstr "{escaped}"')
            in_msgstr = True
            msgstr_written = True
        elif in_msgstr and line.startswith('"') and line.endswith('"'):
            # skip continuation lines (we put everything in one line)
            continue
        else:
            if in_msgstr and not line.startswith('"'):
                in_msgstr = False
            new_lines.append(line)

    if not msgstr_written:
        new_lines.append(f'msgstr "{escaped}"')

    # remove fuzzy flag
    cleaned = []
    for line in new_lines:
        if line.startswith('#,'):
            flags = [f.strip() for f in line[2:].split(',') if f.strip() != 'fuzzy']
            if flags:
                cleaned.append('#, ' + ', '.join(flags))
            # else: remove the line entirely
        else:
            cleaned.append(line)

    entry['raw_lines'] = cleaned
    entry['msgstr'] = new_msgstr
    entry['is_fuzzy'] = False


# ==========================================
# 2. Google Translate（免费接口）
# ==========================================

def translate_batch(texts, source_lang='en', target_lang='zh-CN', batch_size=20):
    """
    使用 deep-translator 的 GoogleTranslator 批量翻译。
    每次翻译 batch_size 条，避免触发限流。
    """
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
        print(f"  Translating batch {batch_num}/{total_batches} ({len(batch)} items)...")

        try:
            # deep-translator supports list input for batch translation
            translated = translator.translate_batch(batch)
            for original, translation in zip(batch, translated):
                results[original] = translation
        except Exception as e:
            print(f"  Batch translation failed: {e}")
            # fallback: translate one by one
            for text in batch:
                try:
                    result = translator.translate(text)
                    results[text] = result
                    time.sleep(0.3)
                except Exception as e2:
                    print(f"    Failed to translate: {text[:50]}... Error: {e2}")
                    results[text] = text  # keep original

        # rate limiting
        if i + batch_size < total:
            time.sleep(1)

    return results

import re

def validate_placeholders(original, translated):
    """确保翻译结果中包含原文的所有 %(xxx)s 占位符"""
    orig_placeholders = re.findall(r'%\([^)]+\)[sdifFeEgGcrb%]', original)
    trans_placeholders = re.findall(r'%\([^)]+\)[sdifFeEgGcrb%]', translated)
    
    if set(orig_placeholders) != set(trans_placeholders):
        # 占位符不匹配，尝试修复：把缺失的占位符追加到翻译末尾
        missing = set(orig_placeholders) - set(trans_placeholders)
        if missing:
            translated += ' ' + ' '.join(missing)
        else:
            # 翻译中多了占位符或格式不对，回退使用原文
            return original
    return translated

# ==========================================
# 3. OpenCC 简繁转换
# ==========================================

def convert_s2t(po_filepath, output_filepath):
    """用 OpenCC 将简体 .po 转换为台湾正体"""
    import opencc
    converter = opencc.OpenCC('s2t')

    with open(po_filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 只转换 msgstr 部分，不转换 msgid（msgid 是英文原文）
    entries = parse_po_file(po_filepath)

    for entry in entries:
        if entry['msgstr']:
            entry['msgstr'] = converter.convert(entry['msgstr'])
            update_msgstr_in_raw_lines(entry, entry['msgstr'])

    # 修改头部语言标识
    for entry in entries:
        for j, line in enumerate(entry['raw_lines']):
            if '"Language: zh\\n"' in line or '"Language: zh_CN\\n"' in line:
                entry['raw_lines'][j] = line.replace('zh\\n', 'zh_TW\\n').replace('zh_CN\\n', 'zh_TW\\n')
            if '"Language-Team: zh' in line:
                entry['raw_lines'][j] = re.sub(
                    r'Language-Team: zh[^\\]*',
                    'Language-Team: zh_TW',
                    line
                )

    rebuild_po(entries, output_filepath)
    print(f"  Converted {po_filepath} → {output_filepath} (s2t)")


# ==========================================
# 4. 编译 .po → .mo 和 .po → .json
# ==========================================

def compile_mo(translations_dir):
    """编译所有 .po 为 .mo"""
    print("\n[3/4] Compiling .po → .mo ...")
    result = subprocess.run(
        ['pybabel', 'compile', '-d', translations_dir],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("  .mo compilation successful")
    else:
        print(f"  .mo compilation warnings (non-fatal):\n{result.stderr[:500]}")


def compile_json(translations_dir):
    """编译所有 .po 为 .json（JED 格式）"""
    print("\n[4/4] Compiling .po → .json (JED format) ...")
    po_files = list(Path(translations_dir).rglob('messages.po'))

    for po_file in po_files:
        json_file = po_file.with_suffix('.json')
        # 删除旧的 json
        if json_file.exists():
            json_file.unlink()

        result = subprocess.run(
            ['po2json', '--domain', 'superset', '--format', 'jed1.x', '--fuzzy',
             str(po_file), str(json_file)],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"  ✓ {po_file.parent.name}/messages.json")
            # prettier 格式化（可选）
            subprocess.run(['prettier', '--write', str(json_file)],
                         capture_output=True, text=True)
        else:
            print(f"  ✗ {po_file.parent.name}: {result.stderr[:200]}")


# ==========================================
# 5. 主流程
# ==========================================

def main():
    parser = argparse.ArgumentParser(description='Superset translation auto-completion')
    parser.add_argument('--translations-dir', required=True, help='Path to translations directory')
    parser.add_argument('--source-lang', default='en', help='Source language (default: en)')
    parser.add_argument('--target-lang', default='zh-CN', help='Target language for Google Translate (default: zh-CN)')
    parser.add_argument('--target-po', default='zh', help='Target .po directory name (default: zh)')
    parser.add_argument('--generate-zh-tw', action='store_true', help='Generate zh_TW from zh using OpenCC')
    parser.add_argument('--batch-size', type=int, default=20, help='Translation batch size (default: 20)')
    parser.add_argument('--skip-translate', action='store_true', help='Skip translation, only compile')
    args = parser.parse_args()

    translations_dir = args.translations_dir
    target_po_dir = args.target_po
    target_po_path = os.path.join(translations_dir, target_po_dir, 'LC_MESSAGES', 'messages.po')

    if not os.path.exists(target_po_path):
        print(f"ERROR: {target_po_path} not found")
        sys.exit(1)

    # ---- Step 1: 翻译未翻译的条目 ----
    if not args.skip_translate:
        print(f"\n[1/4] Scanning {target_po_path} for untranslated entries...")
        entries = parse_po_file(target_po_path)

        untranslated = []
        untranslated_indices = []
        for i, entry in enumerate(entries):
            if is_untranslated(entry):
                untranslated.append(entry['msgid'])
                untranslated_indices.append(i)

        print(f"  Found {len(untranslated)} untranslated entries")

        if untranslated:
            print(f"\n[2/4] Translating {len(untranslated)} entries ({args.source_lang} → {args.target_lang})...")
            translations = translate_batch(
                untranslated,
                source_lang=args.source_lang,
                target_lang=args.target_lang,
                batch_size=args.batch_size
            )

            updated_count = 0
            for idx in untranslated_indices:
                entry = entries[idx]
                original = entry['msgid']
                translated = translations.get(original, '')

                if translated and translated != original:
                    translated = translated.replace('\\"', '"').replace('\\\\', '\\')
                    # 校验占位符
                    translated = validate_placeholders(entry['msgid'], translated)
                    update_msgstr_in_raw_lines(entry, translated)
                    updated_count += 1


            print(f"  Successfully translated {updated_count}/{len(untranslated)} entries")
            rebuild_po(entries, target_po_path)
            print(f"  Saved to {target_po_path}")
        else:
            print("  All entries already translated, skipping...")
    else:
        print("\n[1/4] Skipping translation (--skip-translate)")
        print("[2/4] Skipping translation (--skip-translate)")

    # ---- Step 2: 生成繁体中文 ----
    if args.generate_zh_tw:
        print(f"\n[2.5/4] Generating zh_TW from {target_po_dir} using OpenCC...")
        zh_tw_dir = os.path.join(translations_dir, 'zh_TW', 'LC_MESSAGES')
        os.makedirs(zh_tw_dir, exist_ok=True)
        zh_tw_po = os.path.join(zh_tw_dir, 'messages.po')
        convert_s2t(target_po_path, zh_tw_po)

    # ---- Step 3 & 4: 编译 ----
    compile_mo(translations_dir)
    compile_json(translations_dir)

    print("\n✅ All done! Restart Superset to apply changes.")


if __name__ == '__main__':
    main()
