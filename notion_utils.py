import streamlit as st
import notion_client
import re

@st.cache_data(ttl=600)
def get_all_databases(_notion_client):
    """APIキーがアクセス可能なデータベースの一覧を取得します。"""
    try:
        response = _notion_client.search(filter={"value": "database", "property": "object"})
        databases = []
        for db in response.get('results', []):
            title_list = db.get('title', [])
            if title_list:
                title = title_list[0].get('plain_text', '（無題のデータベース）')
            else:
                title = '（無題のデータベース）'
            databases.append({'id': db['id'], 'title': title})
        return databases
    except Exception as e:
        st.error(f"Notionのデータベース検索中にAPIエラーが発生しました: {e}")
        return []

@st.cache_data(ttl=300)
def get_pages_in_database(_notion_client, db_id):
    """指定されたデータベース内のページ一覧を取得します。"""
    try:
        response = _notion_client.databases.query(database_id=db_id)
        pages = []
        for page in response.get('results', []):
            title_property = next((prop for prop in page['properties'].values() if prop['type'] == 'title'), None)
            if title_property:
                title = title_property.get('title', [{}])[0].get('plain_text', '無題のページ')
                pages.append({'id': page['id'], 'title': title})
        return pages
    except Exception:
        return []

def notion_blocks_to_markdown(blocks: list, _notion_client: notion_client.Client) -> str:
    """Notionブロックのリストをマークダウン文字列に変換します。"""
    # （この関数の内容は変更なし）
    def rich_text_to_markdown(rich_text: list) -> str:
        markdown_text = ""
        for rt in rich_text:
            content = rt.get('plain_text', '')
            annotations = rt.get('annotations', {})
            if annotations.get('bold'): content = f"**{content}**"
            if annotations.get('italic'): content = f"*{content}*"
            if annotations.get('strikethrough'): content = f"~{content}~"
            if annotations.get('code'): content = f"`{content}`"
            markdown_text += content
        return markdown_text

    markdown_lines = []
    for block in blocks:
        block_type = block['type']
        if block_type == 'paragraph':
            markdown_lines.append(rich_text_to_markdown(block['paragraph']['rich_text']))
        # ... 以下、この関数の残りの部分は元のまま ...
        elif block_type == 'heading_1':
            markdown_lines.append(f"# {rich_text_to_markdown(block['heading_1']['rich_text'])}")
        elif block_type == 'heading_2':
            markdown_lines.append(f"## {rich_text_to_markdown(block['heading_2']['rich_text'])}")
        elif block_type == 'heading_3':
            markdown_lines.append(f"### {rich_text_to_markdown(block['heading_3']['rich_text'])}")
        elif block_type == 'quote':
            markdown_lines.append(f"> {rich_text_to_markdown(block['quote']['rich_text'])}")
        elif block_type == 'bulleted_list_item':
            markdown_lines.append(f"- {rich_text_to_markdown(block['bulleted_list_item']['rich_text'])}")
        elif block_type == 'numbered_list_item':
            markdown_lines.append(f"1. {rich_text_to_markdown(block['numbered_list_item']['rich_text'])}")
        elif block_type == 'to_do':
            text = rich_text_to_markdown(block['to_do']['rich_text'])
            checked = block['to_do']['checked']
            markdown_lines.append(f"[{'x' if checked else ' '}] {text}")
        elif block_type == 'divider':
            markdown_lines.append("---")
        elif block_type == 'code':
            text = "".join(rt['plain_text'] for rt in block['code']['rich_text'])
            lang = block['code']['language']
            markdown_lines.append(f"```{lang}\n{text}\n```")
        elif block_type == 'table':
            try:
                rows_response = _notion_client.blocks.children.list(block_id=block['id'])
                rows = rows_response.get('results', [])
                if not rows: continue
                md_rows = []
                has_header = block.get('table', {}).get('has_column_header', False)
                for i, row in enumerate(rows):
                    if row['type'] != 'table_row': continue
                    cells = [rich_text_to_markdown(cell) for cell in row['table_row']['cells']]
                    md_rows.append(f"| {' | '.join(cells)} |")
                    if i == 0 and has_header:
                        num_columns = len(row.get('table_row', {}).get('cells', []))
                        md_rows.append(f"|{'|'.join(['---'] * num_columns)}|")
                markdown_lines.extend(md_rows)
            except Exception:
                markdown_lines.append("[テーブル変換エラー]")
        if markdown_lines and markdown_lines[-1] and not markdown_lines[-1].strip() == "---":
             markdown_lines.append("")
    return "\n".join(markdown_lines)


def markdown_to_notion_blocks(markdown_text: str) -> list:
    """マークダウン文字列をNotionブロックのリストに変換します。"""
    # （この関数の内容は変更なし）
    def parse_rich_text(text: str):
        pattern = r"(\*\*|__)(?P<bold>.+?)\1|(\*|_)(?P<italic>.+?)\1|(~)(?P<strikethrough>.+?)\1|(`)(?P<code>.+?)(`)"
        rich_text_objects = []
        last_index = 0
        for match in re.finditer(pattern, text):
            start, end = match.span()
            if start > last_index:
                rich_text_objects.append({"type": "text", "text": {"content": text[last_index:start]}})
            annotations = {}
            content = ""
            if match.group('bold') is not None:
                annotations['bold'] = True
                content = match.group('bold')
            elif match.group('italic') is not None:
                annotations['italic'] = True
                content = match.group('italic')
            elif match.group('strikethrough') is not None:
                annotations['strikethrough'] = True
                content = match.group('strikethrough')
            elif match.group('code') is not None:
                annotations['code'] = True
                content = match.group('code')
            if content:
                rich_text_objects.append({"type": "text", "text": {"content": content}, "annotations": annotations})
            last_index = end
        if last_index < len(text):
            rich_text_objects.append({"type": "text", "text": {"content": text[last_index:]}})
        return [obj for obj in rich_text_objects if obj["text"]["content"]]

    blocks = []
    lines = markdown_text.strip().split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        # テーブル
        if line.strip().startswith('|') and i + 1 < len(lines) and re.match(r'^\s*\|?.*-.*\|?\s*$', lines[i+1].strip()):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_lines.append(lines[i].strip())
                i += 1
            header_line = table_lines[0]
            row_lines = table_lines[2:]
            header_cells = [cell.strip() for cell in header_line.strip('|').split('|')]
            num_columns = len(header_cells)
            table_rows = [{"type": "table_row", "table_row": {"cells": [[{"type": "text", "text": {"content": cell}}] for cell in header_cells]}}]
            for row_line in row_lines:
                row_cells_content = [cell.strip() for cell in row_line.strip('|').split('|')]
                row_cells_content += [''] * (num_columns - len(row_cells_content))
                table_rows.append({"type": "table_row", "table_row": {"cells": [parse_rich_text(cell) for cell in row_cells_content[:num_columns]]}})
            if table_rows:
                blocks.append({"type": "table", "table": {"table_width": num_columns, "has_column_header": True, "has_row_header": False, "children": table_rows}})
            continue
        # 他のブロック
        stripped_line = line.strip()
        if stripped_line.startswith('# '):
            blocks.append({"type": "heading_1", "heading_1": {"rich_text": parse_rich_text(stripped_line[2:])}})
        elif stripped_line.startswith('## '):
            blocks.append({"type": "heading_2", "heading_2": {"rich_text": parse_rich_text(stripped_line[3:])}})
        # ... 以下、この関数の残りの部分は元のまま ...
        elif stripped_line.startswith('### '):
            blocks.append({"type": "heading_3", "heading_3": {"rich_text": parse_rich_text(stripped_line[4:])}})
        elif stripped_line.startswith('> '):
            blocks.append({"type": "quote", "quote": {"rich_text": parse_rich_text(stripped_line[2:])}})
        elif re.match(r'^[\*\-\+]\s', stripped_line):
            blocks.append({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": parse_rich_text(re.sub(r'^[\*\-\+]\s', '', stripped_line))}})
        elif re.match(r'^\d+\.\s', stripped_line):
            blocks.append({"type": "numbered_list_item", "numbered_list_item": {"rich_text": parse_rich_text(re.sub(r'^\d+\.\s', '', stripped_line))}})
        elif stripped_line.startswith('[ ] ') or stripped_line.startswith('[x] '):
            checked = stripped_line.startswith('[x]')
            blocks.append({"type": "to_do", "to_do": {"rich_text": parse_rich_text(stripped_line[4:]), "checked": checked}})
        elif stripped_line in ('---', '***', '___'):
            blocks.append({"type": "divider", "divider": {}})
        elif stripped_line.startswith('```'):
            lang = stripped_line[3:].strip()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip() == '```':
                code_lines.append(lines[i])
                i += 1
            blocks.append({"type": "code", "code": {"rich_text": [{"type": "text", "text": {"content": "\n".join(code_lines)}}], "language": lang if lang else "plain text"}})
        else:
            if line.strip():
                blocks.append({"type": "paragraph", "paragraph": {"rich_text": parse_rich_text(line)}})
        i += 1
    return blocks