import streamlit as st
from ddgs import DDGS
import trafilatura
import traceback
import httpx
import io
import pdfplumber
import docx

from notion_utils import notion_blocks_to_markdown, markdown_to_notion_blocks

# --- File Processing ---
def process_uploaded_files(uploaded_files):
    # (この関数に変更はありません)
    full_text = ""
    if not uploaded_files:
        return full_text
    for uploaded_file in uploaded_files:
        full_text += f"--- 参考資料: {uploaded_file.name} ---\n\n"
        try:
            if uploaded_file.name.lower().endswith('.pdf'):
                with pdfplumber.open(uploaded_file) as pdf:
                    for page in pdf.pages:
                        text = page.extract_text()
                        if text:
                            full_text += text + "\n"
            elif uploaded_file.name.lower().endswith('.docx'):
                document = docx.Document(uploaded_file)
                for para in document.paragraphs:
                    full_text += para.text + "\n"
            elif uploaded_file.name.lower().endswith('.txt'):
                stringio = io.StringIO(uploaded_file.getvalue().decode("utf-8"))
                full_text += stringio.read() + "\n"
        except Exception as e:
            st.error(f"ファイル '{uploaded_file.name}' の読み込み中にエラーが発生しました: {e}")
        full_text += "\n\n"
    return full_text

# --- Web Content Extraction ---
def get_content_from_single_url(url: str, status_placeholder):
    # (この関数に変更はありません)
    status_placeholder.info(f"単一URLから本文を抽出しています: {url}")
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
    }
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15.0) as client:
            response = client.get(url)
            response.raise_for_status() 
            extracted = trafilatura.extract(response.text, include_comments=False, include_tables=True)
            if extracted:
                return f"--- 参考URL: {url} ---\n\n{extracted}"
            else:
                st.error(f"URLから本文を抽出できませんでした。コンテンツが記事形式でない可能性があります。: {url}")
                return None
    except httpx.HTTPStatusError as e:
        st.error(f"URLへのアクセスに失敗しました (ステータスコード: {e.response.status_code}): {url}")
        return None
    except Exception as e:
        st.error(f"URLの処理中に予期せぬエラーが発生しました: {url}\n原因: {e}")
        return None

def _extract_keywords_for_search(user_prompt: str) -> str:
    """ユーザーのプロンプトから検索キーワードを抽出する"""
    keyword_prompt = f"以下の「リクエスト文」から、Web検索に使うべき最も重要なキーワード（固有名詞など）を最大5つ、カンマ区切りで抜き出してください。\n\nリクエスト文：{user_prompt}\nキーワード："
    try:
        keyword_response = st.session_state.gemini_lite_model.generate_content(keyword_prompt)
        search_keywords = keyword_response.text.strip().replace("\n", "")
        return search_keywords if search_keywords else user_prompt
    except Exception:
        return user_prompt

def _search_web_and_display_results(search_query: str, search_count: int, results_placeholder):
    """Web検索を実行し、結果をUIに表示する"""
    search_results = list(DDGS().text(search_query, max_results=search_count))
    if not search_results:
        return []
    
    with results_placeholder.container():
        st.info(f"🤖 **検索クエリ (PDF除外):** `{search_query}`")
        with st.expander(f"参考にしたWebサイト ({len(search_results)}件)"):
            for result in search_results:
                st.markdown(f"- [{result.get('title')}]({result.get('href')})")
    return search_results

def _extract_articles_from_urls(search_results: list) -> list:
    """URLリストから記事本文を並列で抽出する"""
    HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0'}
    extracted_articles = []
    
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15.0) as client:
        for i, result in enumerate(search_results):
            url = result.get('href')
            if not url: continue
            try:
                response = client.get(url)
                response.raise_for_status()
                extracted = trafilatura.extract(response.text, include_comments=False, include_tables=True)
                if extracted:
                    extracted_articles.append({"url": url, "text": extracted})
                else:
                    st.warning(f"  - [{i+1}/{len(search_results)}] 本文抽出失敗: {url}")
            except Exception as e:
                st.warning(f"  - [{i+1}/{len(search_results)}] URL処理失敗: {url}\n  - 原因: {e}")
    return extracted_articles

def _build_context_with_token_management(extracted_articles: list, user_prompt: str, full_text_token_limit: int, status_placeholder) -> str:
    """トークン上限を考慮して参考情報コンテキストを構築する（ハイブリッド戦略）"""
    final_context = ""
    current_tokens = 0
    remaining_articles_text = ""

    for i, article in enumerate(extracted_articles):
        article_text_with_header = f"--- 参考記事 {i+1} ({article['url']}) ---\n{article['text']}\n\n"
        article_tokens = len(article_text_with_header) / 2 # 概算トークン数
        
        if current_tokens + article_tokens <= full_text_token_limit:
            final_context += article_text_with_header
            current_tokens += article_tokens
        else:
            remaining_articles_text += article_text_with_header
            
    if remaining_articles_text:
        status_placeholder.info("トークン上限を超えたため、残りの記事を要約しています...")
        summarize_prompt = f"以下の複数の記事群を、ユーザーのリクエストに沿うように重要なポイントを一つの文章にまとめてください。\n\nユーザーリクエスト: {user_prompt}\n\n--- 記事群 ---\n{remaining_articles_text}"
        try:
            summary_response = st.session_state.gemini_lite_model.generate_content(summarize_prompt)
            final_context += f"--- 複数の参考記事の要約 ---\n{summary_response.text}\n\n"
            st.info("残りの記事の要約が完了しました。")
        except Exception as e:
            st.warning(f"要約処理中にエラーが発生しました: {e}")
    
    return final_context

def generate_content_from_web(user_prompt: str, search_count: int, full_text_token_limit: int, status_placeholder, results_placeholder):
    """Web検索から参考情報を生成する一連のプロセス"""
    status_placeholder.info("1/5: リクエストからキーワードを抽出しています...")
    search_keywords = _extract_keywords_for_search(user_prompt)
    search_query = f"{search_keywords} -filetype:pdf"
    
    status_placeholder.info(f"2/5: 「{search_query}」でWeb検索を実行中...")
    search_results = _search_web_and_display_results(search_query, search_count, results_placeholder)
    if not search_results:
        st.error("Web検索で情報を取得できませんでした。")
        return None

    status_placeholder.info("3/5: Webページから記事本文を抽出しています...")
    extracted_articles = _extract_articles_from_urls(search_results)
    if not extracted_articles:
        st.error("どのWebサイトからも記事本文を抽出できませんでした。キーワードを変えて再度お試しください。")
        return None

    status_placeholder.info("4/5: トークン数を管理しながら参考情報を構築しています...")
    final_context = _build_context_with_token_management(extracted_articles, user_prompt, full_text_token_limit, status_placeholder)

    status_placeholder.info("5/5: Geminiによる最終的な記事生成を開始します...")
    return final_context

# --- Notion Page Generation ---
def parse_gemini_output(text, fallback_prompt):
    # (この関数に変更はありません)
    title = f"生成記事: {fallback_prompt[:20]}..."
    content = ""
    if "タイトル：" in text and "本文：" in text:
        try:
            title_part, content_part = text.split("本文：", 1)
            title = title_part.replace("タイトル：", "").strip()
            content = content_part.strip()
        except ValueError:
            content = text
    else:
        content = text
    if not title: # タイトルが空の場合のフォールバック
        title = f"生成記事: {fallback_prompt[:20]}..."
    return title, content

def _get_full_text_context(uploaded_files, source_url, user_prompt, search_count, full_text_token_limit, status_placeholder, results_placeholder):
    """利用可能な情報源からコンテキストを取得する"""
    if uploaded_files:
        status_placeholder.info("アップロードされたファイルを読み込んでいます...")
        return process_uploaded_files(uploaded_files)
    if source_url:
        return get_content_from_single_url(source_url, status_placeholder)
    return generate_content_from_web(user_prompt, search_count, full_text_token_limit, status_placeholder, results_placeholder)

def run_new_page_process(database_id, user_prompt, ai_persona, uploaded_files, source_url, search_count, full_text_token_limit, status_placeholder, results_placeholder):
    try:
        full_text_context = _get_full_text_context(uploaded_files, source_url, user_prompt, search_count, full_text_token_limit, status_placeholder, results_placeholder)
        if not full_text_context:
            st.error("参考情報が見つからなかったため、処理を中断しました。")
            return

        final_prompt = f'''
# 命令
{ai_persona} 与えられた「参考情報」と「リクエスト」に基づき、魅力的で分かりやすい記事を作成してください。
出力は必ず「タイトル：～」「本文：～」の形式で、本文はNotionで表示可能なMarkdown形式で記述してください。
見出し、箇条書き、**太字**などを活用し、構造化された文章を作成してください。

# 参考情報
{full_text_context}

# リクエスト
{user_prompt}

# 出力形式 (***必ず厳守***)
タイトル：(ここに記事のタイトルを記述)
本文：(ここに上記の書式ルールに従ったNotion記法のMarkdownで記事の本文を記述)
'''
        response = st.session_state.gemini_model.generate_content(final_prompt)
        title, content = parse_gemini_output(response.text, user_prompt)

        with results_placeholder.container(border=True):
            st.markdown(f"### プレビュー: {title}")
            st.markdown(content)
            st.info("上記の内容でNotionに新しいページを作成します。")

        status_placeholder.info("Notionに新しいページを作成中...")
        all_blocks = markdown_to_notion_blocks(content)
        
        # (これ以降のロジックは変更なし)
        db_info = st.session_state.notion_client.databases.retrieve(database_id=database_id)
        title_prop_name = next((k for k, v in db_info['properties'].items() if v['type'] == 'title'), 'Name')
        parent_payload = {"database_id": database_id}
        properties_payload = {title_prop_name: {"title": [{"text": {"content": title}}]}}
        
        created_page = st.session_state.notion_client.pages.create(parent=parent_payload, properties=properties_payload, children=all_blocks[:100])
        
        if len(all_blocks) > 100:
            for i in range(100, len(all_blocks), 100):
                st.session_state.notion_client.blocks.children.append(block_id=created_page['id'], children=all_blocks[i:i+100])

        st.balloons()
        status_placeholder.success(f"✅ 新規ページ「{title}」の作成が完了しました！ [{created_page['url']}]({created_page['url']})")

    except Exception as e:
        st.error(f"❌ 新規ページ作成中にエラーが発生しました: {e}")
        st.code(traceback.format_exc())

def run_edit_page_process(page_id, user_prompt, ai_persona, uploaded_files, source_url, search_count, full_text_token_limit, status_placeholder, results_placeholder):
    try:
        status_placeholder.info("1/4: Notionから既存のコンテンツを読み込んでいます...")
        existing_blocks_response = st.session_state.notion_client.blocks.children.list(block_id=page_id)
        existing_markdown = notion_blocks_to_markdown(existing_blocks_response.get('results', []), st.session_state.notion_client)
        
        with results_placeholder.container(border=True):
            with st.expander("現在のページ内容（Markdown）"):
                st.markdown(existing_markdown or "（このページは空です）")
        
        status_placeholder.info("2/4: 参考情報の収集を開始します...")
        full_text_context = _get_full_text_context(uploaded_files, source_url, user_prompt, search_count, full_text_token_limit, status_placeholder, results_placeholder)
        if not full_text_context:
            st.error("参考情報が見つからなかったため、処理を中断しました。")
            return
        
        status_placeholder.info("3/4: AIによる追記コンテンツの生成を開始します...")
        final_prompt = f'''
# 命令
{ai_persona} 以下の「既存の記事」と「参考情報」を踏まえ、ユーザーからの「追記リクエスト」に的確に答える形で、**追記すべき新しい文章のみ**を生成してください。
既存の記事の内容を繰り返す必要はありません。

# 既存の記事
{existing_markdown}

# 参考情報
{full_text_context}

# 追記リクエスト
{user_prompt}

# 出力形式 (***必ず厳守***)
タイトル：(ここに既存の記事タイトル、または新しいタイトルを記述)
本文：(ここに**追記すべき新しい文章**をMarkdown形式で記述)
'''
        response = st.session_state.gemini_model.generate_content(final_prompt)
        title, content = parse_gemini_output(response.text, user_prompt)

        with results_placeholder.container(border=True):
            st.markdown(f"### プレビュー（追記部分）: {title}")
            st.markdown(content)
            st.info("上記の内容をNotionページの末尾に追記します。")
            
        status_placeholder.info("4/4: Notionページにコンテンツを追記中...")
        
        # (これ以降のロジックは変更なし)
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
        page_url = f"https://www.notion.so/{page_id.replace('-', '')}"
        status_placeholder.success(f"✅ ページ「{title}」への追記が完了しました！ [{page_url}]({page_url})")
        
    except Exception as e:
        st.error(f"❌ ページ追記中にエラーが発生しました: {e}")
        st.code(traceback.format_exc())