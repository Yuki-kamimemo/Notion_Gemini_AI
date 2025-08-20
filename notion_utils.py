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
            title = title_list[0].get('plain_text', '（無題のデータベース）') if title_list else '（無題のデータベース）'
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

def rich_text_to_markdown(rich_text: list) -> str:
    """NotionのリッチテキストをMarkdown文字列に変換します。"""
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

def notion_blocks_to_markdown(blocks: list, _notion_client: notion_client.Client) -> str:
    """Notionブロックのリストをマークダウン文字列に変換します。"""
    markdown_lines = []
    for block in blocks:
        block_type = block['type']
        content = ""
        
        if block_type == 'paragraph':
            content = rich_text_to_markdown(block['paragraph']['rich_text'])
        elif block_type == 'heading_1':
            content = f"# {rich_text_to_markdown(block['heading_1']['rich_text'])}"
        elif block_type == 'heading_2':
            content = f"## {rich_text_to_markdown(block['heading_2']['rich_text'])}"
        elif block_type == 'heading_3':
            content = f"### {rich_text_to_markdown(block['heading_3']['rich_text'])}"
        elif block_type == 'quote':
            content = f"> {rich_text_to_markdown(block['quote']['rich_text'])}"
        elif block_type == 'bulleted_list_item':
            content = f"- {rich_text_to_markdown(block['bulleted_list_item']['rich_text'])}"
        elif block_type == 'numbered_list_item':
            content = f"1. {rich_text_to_markdown(block['numbered_list_item']['rich_text'])}"
        elif block_type == 'to_do':
            text = rich_text_to_markdown(block['to_do']['rich_text'])
            checked = 'x' if block['to_do']['checked'] else ' '
            content = f"[{checked}] {text}"
        elif block_type == 'divider':
            content = "---"
        elif block_type == 'code':
            text = "".join(rt['plain_text'] for rt in block['code']['rich_text'])
            lang = block['code']['language']
            content = f"```{lang}\n{text}\n```"
        elif block_type == 'table':
            try:
                rows_response = _notion_client.blocks.children.list(block_id=block['id'])
                rows = rows_response.get('results', [])
                if rows:
                    md_rows = []
                    has_header = block.get('table', {}).get('has_column_header', False)
                    for i, row in enumerate(rows):
                        if row['type'] != 'table_row': continue
                        cells = [rich_text_to_markdown(cell) for cell in row['table_row']['cells']]
                        md_rows.append(f"| {' | '.join(cells)} |")
                        if i == 0 and has_header:
                            md_rows.append(f"|{'|'.join(['---'] * len(cells))}|")
                    content = "\n".join(md_rows)
            except Exception:
                content = "[テーブル変換エラー]"
        
        if content:
            markdown_lines.append(content)
            if block_type not in ['divider', 'table', 'code']:
                 markdown_lines.append("") # ブロック間に空行を追加
                 
    return "\n".join(markdown_lines)

def parse_rich_text(text: str) -> list:
    """インラインのMarkdown装飾をNotionのリッチテキストオブジェクトに変換します。"""
    pattern = r"(\*\*|__)(?P<bold>.+?)\1|(\*|_)(?P<italic>.+?)\1|(`)(?P<code>.+?)\2|(~)(?P<strikethrough>.+?)\4"
    parts = []
    last_index = 0
    for match in re.finditer(pattern, text):
        start, end = match.span()
        if start > last_index:
            parts.append({'type': 'text', 'text': {'content': text[last_index:start]}})
        
        group_dict = match.groupdict()
        content = next(v for v in group_dict.values() if v is not None)
        annotation_key = next(k for k, v in group_dict.items() if v is not None)
        
        parts.append({
            'type': 'text',
            'text': {'content': content},
            'annotations': {annotation_key: True}
        })
        last_index = end
    if last_index < len(text):
        parts.append({'type': 'text', 'text': {'content': text[last_index:]}})
    return [p for p in parts if p['text']['content']]

def markdown_to_notion_blocks(markdown_text: str) -> list:
    """マークダウン文字列をNotionブロックのリストに変換します。"""
    blocks = []
    lines = markdown_text.strip().split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped_line = line.strip()

        if not stripped_line:
            i += 1
            continue
        
        if stripped_line.startswith('```'):
            lang = stripped_line[3:].strip() or "plain text"
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip() == '```':
                code_lines.append(lines[i])
                i += 1
            blocks.append({"type": "code", "code": {"rich_text": [{"type": "text", "text": {"content": "\n".join(code_lines)}}], "language": lang}})
        elif stripped_line.startswith('# '):
            blocks.append({"type": "heading_1", "heading_1": {"rich_text": parse_rich_text(stripped_line[2:])}})
        elif stripped_line.startswith('## '):
            blocks.append({"type": "heading_2", "heading_2": {"rich_text": parse_rich_text(stripped_line[3:])}})
        elif stripped_line.startswith('### '):
            blocks.append({"type": "heading_3", "heading_3": {"rich_text": parse_rich_text(stripped_line[4:])}})
        elif stripped_line.startswith('> '):
            blocks.append({"type": "quote", "quote": {"rich_text": parse_rich_text(stripped_line[2:])}})
        elif re.match(r'^[\*\-\+]\s', stripped_line):
            blocks.append({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": parse_rich_text(re.sub(r'^[\*\-\+]\s', '', stripped_line))}})
        elif re.match(r'^\d+\.\s', stripped_line):
            blocks.append({"type": "numbered_list_item", "numbered_list_item": {"rich_text": parse_rich_text(re.sub(r'^\d+\.\s', '', stripped_line))}})
        elif stripped_line.startswith(('[ ] ', '[x] ')):
            checked = stripped_line.startswith('[x]')
            blocks.append({"type": "to_do", "to_do": {"rich_text": parse_rich_text(stripped_line[4:]), "checked": checked}})
        elif stripped_line in ('---', '***', '___'):
            blocks.append({"type": "divider", "divider": {}})
        else: # Paragraph as fallback
            blocks.append({"type": "paragraph", "paragraph": {"rich_text": parse_rich_text(line)}})
        
        i += 1
    return blocks