import streamlit as st
from ddgs import DDGS
import trafilatura
import traceback
import httpx
import io
import pdfplumber
import docx

from notion_utils import notion_blocks_to_markdown, markdown_to_notion_blocks

# --- HTTPクライアントをセッション管理するためのユーティリティ ---
@st.cache_resource
def get_httpx_client():
    """再利用可能なhttpx.Clientインスタンスを取得する"""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0'}
    return httpx.Client(headers=headers, follow_redirects=True, timeout=15.0)

# --- ファイル処理 ---
def process_uploaded_files(uploaded_files):
    full_text = ""
    if not uploaded_files:
        return full_text
    for uploaded_file in uploaded_files:
        full_text += f"--- 参考資料: {uploaded_file.name} ---\n\n"
        try:
            if uploaded_file.name.lower().endswith('.pdf'):
                with pdfplumber.open(uploaded_file) as pdf:
                    full_text += "".join(page.extract_text() + "\n" for page in pdf.pages if page.extract_text())
            elif uploaded_file.name.lower().endswith('.docx'):
                document = docx.Document(uploaded_file)
                full_text += "".join(para.text + "\n" for para in document.paragraphs)
            elif uploaded_file.name.lower().endswith('.txt'):
                stringio = io.StringIO(uploaded_file.getvalue().decode("utf-8"))
                full_text += stringio.read() + "\n"
        except Exception as e:
            st.error(f"ファイル '{uploaded_file.name}' の読み込み中にエラーが発生しました: {e}")
        full_text += "\n\n"
    return full_text

# --- Webコンテンツ取得 ---
def get_content_from_single_url(url: str, status_placeholder):
    status_placeholder.info(f"単一URLから本文を抽出しています: {url}")
    client = get_httpx_client()
    try:
        response = client.get(url)
        response.raise_for_status() 
        extracted = trafilatura.extract(response.text, include_comments=False, include_tables=True)
        if extracted:
            return f"--- 参考URL: {url} ---\n\n{extracted}"
        else:
            st.warning(f"URLから本文を抽出できませんでした。: {url}")
            return None
    except Exception as e:
        st.error(f"URLの処理中にエラーが発生しました: {url}\n原因: {e}")
        return None

def generate_content_from_web(user_prompt: str, search_count: int, full_text_token_limit: int, status_placeholder, results_placeholder):
    status_placeholder.info("1/5: リクエストから検索キーワードを抽出中...")
    keyword_prompt = f"以下のリクエスト文から、Web検索に最適なキーワードを5つ以内でカンマ区切りで抜き出してください。\n\nリクエスト文：{user_prompt}\nキーワード："
    try:
        keyword_response = st.session_state.gemini_lite_model.generate_content(keyword_prompt)
        search_keywords = keyword_response.text.strip().replace("\n", "")
    except Exception:
        search_keywords = user_prompt
    
    if not search_keywords:
        search_keywords = user_prompt
        
    search_query = f"{search_keywords} -filetype:pdf"
    status_placeholder.info(f"2/5: 「{search_query}」でWeb検索を実行中...")
    
    try:
        search_results = list(DDGS().text(search_query, max_results=search_count))
    except Exception as e:
        st.error(f"Web検索中にエラーが発生しました: {e}")
        return None

    if not search_results:
        st.warning("Web検索で情報を取得できませんでした。")
        return None

    with results_placeholder.container():
        st.info(f"🤖 **検索クエリ:** `{search_query}`")
        with st.expander(f"参考にしたWebサイト ({len(search_results)}件)"):
            for result in search_results:
                st.markdown(f"- [{result.get('title')}]({result.get('href')})")
    
    status_placeholder.info("3/5: Webページから記事本文を抽出中...")
    client = get_httpx_client()
    extracted_articles = []
    for result in search_results:
        url = result.get('href')
        if not url: continue
        try:
            response = client.get(url)
            response.raise_for_status()
            extracted = trafilatura.extract(response.text, include_comments=False, include_tables=True)
            if extracted:
                extracted_articles.append({"url": url, "text": extracted})
        except Exception:
            continue
            
    if not extracted_articles:
        st.error("どのWebサイトからも記事本文を抽出できませんでした。")
        return None
    
    status_placeholder.info("4/5: 参考情報を構築中...")
    final_context, remaining_articles_text = "", ""
    current_tokens = 0
    
    for article in extracted_articles:
        article_text_with_header = f"--- 参考記事: {article['url']} ---\n{article['text']}\n\n"
        if (current_tokens + len(article_text_with_header) / 2) < full_text_token_limit:
            final_context += article_text_with_header
            current_tokens += len(article_text_with_header) / 2
        else:
            remaining_articles_text += article_text_with_header
    
    if remaining_articles_text:
        status_placeholder.info("トークン上限を超えたため、残りの記事を要約しています...")
        summarize_prompt = f"以下の記事群を、ユーザーリクエストに沿うように重要なポイントを一つにまとめてください。\n\nユーザーリクエスト: {user_prompt}\n\n--- 記事群 ---\n{remaining_articles_text}"
        try:
            summary_response = st.session_state.gemini_lite_model.generate_content(summarize_prompt)
            final_context += f"--- 複数の参考記事の要約 ---\n{summary_response.text}\n\n"
        except Exception as e:
            st.warning(f"要約処理中にエラーが発生しました: {e}")

    status_placeholder.info("5/5: AIによる最終的な記事生成を開始します...")
    return final_context

# --- AI処理とNotion連携 ---
def parse_gemini_output(text, fallback_prompt):
    """Geminiの出力をパースしてタイトルと本文を抽出する"""
    title = f"生成記事: {fallback_prompt[:20]}..."
    try:
        if "タイトル：" in text and "本文：" in text:
            title_part, content_part = text.split("本文：", 1)
            title = title_part.replace("タイトル：", "").strip()
            content = content_part.strip()
        else:
            content = text
    except Exception:
        content = text
    return title, content

def run_new_page_process(database_id, user_prompt, ai_persona, uploaded_files, source_url, search_count, full_text_token_limit, status_placeholder, results_placeholder):
    try:
        if uploaded_files:
            full_text_context = process_uploaded_files(uploaded_files)
        elif source_url:
            full_text_context = get_content_from_single_url(source_url, status_placeholder)
        else:
            full_text_context = generate_content_from_web(user_prompt, search_count, full_text_token_limit, status_placeholder, results_placeholder)
        
        if not full_text_context:
            st.error("参考情報が見つからなかったため、処理を中断しました。")
            status_placeholder.empty()
            return

        final_prompt = f"""
# 命令: {ai_persona}
与えられた「参考情報」と「リクエスト」に基づき、記事を作成してください。
出力は必ず「タイトル：～」「本文：～」の形式で、本文はMarkdown形式で記述してください。
# 参考情報
{full_text_context}
# リクエスト
{user_prompt}
# 出力形式 (厳守)
タイトル：(ここにタイトル)
本文：(ここにMarkdown本文)
"""
        response = st.session_state.gemini_model.generate_content(final_prompt)
        title, content = parse_gemini_output(response.text, user_prompt)
        
        with results_placeholder.container(border=True):
            st.markdown(f"### プレビュー: {title}")
            st.markdown(content)
        
        status_placeholder.info("Notionに新しいページを作成中...")
        all_blocks = markdown_to_notion_blocks(content)
        db_info = st.session_state.notion_client.databases.retrieve(database_id=database_id)
        title_prop_name = next((k for k, v in db_info['properties'].items() if v['type'] == 'title'), 'Name')
        
        created_page = st.session_state.notion_client.pages.create(
            parent={"database_id": database_id},
            properties={title_prop_name: {"title": [{"text": {"content": title}}]}},
            children=all_blocks[:100]
        )
        
        if len(all_blocks) > 100:
            for i in range(100, len(all_blocks), 100):
                st.session_state.notion_client.blocks.children.append(block_id=created_page['id'], children=all_blocks[i:i+100])
        
        st.balloons()
        status_placeholder.success(f"✅ 新規ページ「{title}」の作成が完了しました！")
    except Exception as e:
        st.error(f"❌ 新規ページ作成中にエラーが発生しました: {e}")
        st.code(traceback.format_exc())

def run_edit_page_process(page_id, user_prompt, ai_persona, uploaded_files, source_url, search_count, full_text_token_limit, status_placeholder, results_placeholder):
    try:
        status_placeholder.info("1/4: 既存コンテンツを読み込み中...")
        existing_blocks = st.session_state.notion_client.blocks.children.list(block_id=page_id).get('results', [])
        existing_markdown = notion_blocks_to_markdown(existing_blocks, st.session_state.notion_client)
        
        with results_placeholder.container(border=True):
            with st.expander("現在のページ内容（Markdown）"):
                st.markdown(existing_markdown or "（空のページです）")
        
        if uploaded_files:
            full_text_context = process_uploaded_files(uploaded_files)
        elif source_url:
            full_text_context = get_content_from_single_url(source_url, status_placeholder)
        else:
            full_text_context = generate_content_from_web(user_prompt, search_count, full_text_token_limit, status_placeholder, results_placeholder)
        
        if not full_text_context:
            st.error("参考情報が見つからなかったため、処理を中断しました。")
            status_placeholder.empty()
            return
        
        status_placeholder.info("3/4: AIによる追記コンテンツを生成中...")
        final_prompt = f"""
# 命令: {ai_persona}
「既存の記事」と「参考情報」を踏まえ、「追記リクエスト」に沿った**追記すべき新しい文章のみ**を生成してください。
# 既存の記事
{existing_markdown}
# 参考情報
{full_text_context}
# 追記リクエスト
{user_prompt}
# 出力形式 (厳守)
タイトル：(ここに新しいタイトル)
本文：(ここに**追記する**Markdown本文)
"""
        response = st.session_state.gemini_model.generate_content(final_prompt)
        title, content = parse_gemini_output(response.text, user_prompt)

        with results_placeholder.container(border=True):
            st.markdown(f"### プレビュー（追記部分）: {title}")
            st.markdown(content)
            
        status_placeholder.info("4/4: Notionページにコンテンツを追記中...")
        try:
            page_info = st.session_state.notion_client.pages.retrieve(page_id=page_id)
            db_id = page_info.get('parent', {}).get('database_id')
            if db_id:
                db_info = st.session_state.notion_client.databases.retrieve(database_id=db_id)
                title_prop_name = next((k for k, v in db_info['properties'].items() if v['type'] == 'title'), None)
                if title_prop_name:
                    st.session_state.notion_client.pages.update(page_id=page_id, properties={title_prop_name: {"title": [{"text": {"content": title}}]}})
        except Exception as e:
            st.warning(f"ページのタイトル更新に失敗しました: {e}")
            
        new_blocks = markdown_to_notion_blocks(content)
        if new_blocks:
            for i in range(0, len(new_blocks), 100):
                st.session_state.notion_client.blocks.children.append(block_id=page_id, children=new_blocks[i:i+100])
        
        st.balloons()
        status_placeholder.success(f"✅ ページ「{title}」への追記が完了しました！")
    except Exception as e:
        st.error(f"❌ ページ追記中にエラーが発生しました: {e}")
        st.code(traceback.format_exc())