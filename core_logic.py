import streamlit as st
from ddgs import DDGS
import trafilatura
import traceback
import httpx
import io
import pdfplumber
import docx

from notion_utils import notion_blocks_to_markdown, markdown_to_notion_blocks

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
    except Exception as e:
        st.error(f"URLの処理中にエラーが発生しました: {url}\n原因: {e}")
        return None

# --- generate_content_from_web 関数を大幅に修正 ---
def generate_content_from_web(user_prompt: str, search_count: int, full_text_token_limit: int, status_placeholder, results_placeholder):
    status_placeholder.info("1/5: リクエストからキーワードを抽出しています...")
    keyword_prompt = f"以下の「リクエスト文」から、Web検索に使うべき最も重要なキーワード（固有名詞など）を最大5つ、カンマ区切りで抜き出してください。\n\nリクエスト文：{user_prompt}\nキーワード："
    keyword_response = st.session_state.gemini_lite_model.generate_content(keyword_prompt)
    search_keywords = keyword_response.text.strip().replace("\n", "")
    if not search_keywords:
        search_keywords = user_prompt
    search_query = f"{search_keywords} -filetype:pdf"
    status_placeholder.info(f"2/5: 「{search_query}」でWeb検索を実行中...")
    search_results = list(DDGS().text(search_query, max_results=search_count))
    if not search_results:
        st.error("Web検索で情報を取得できませんでした。")
        return None, None
    with results_placeholder.container():
        st.info(f"🤖 **検索クエリ (PDF除外):** `{search_query}`")
        with st.expander(f"参考にしたWebサイト ({len(search_results)}件)"):
            for result in search_results:
                st.markdown(f"- [{result.get('title')}]({result.get('href')})")
    
    status_placeholder.info("3/5: Webページから記事本文を抽出しています...")
    HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0'}
    extracted_articles = []
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=15.0) as client:
        for i, result in enumerate(search_results):
            url = result.get('href')
            if url:
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
                    continue
    if not extracted_articles:
        st.error("どのWebサイトからも記事本文を抽出できませんでした。キーワードを変えて再度お試しください。")
        return None, None
    
    # --- ここからがハイブリッド戦略のロジック ---
    status_placeholder.info("4/5: トークン数を管理しながら参考情報を構築しています...")
    final_context = ""
    current_tokens = 0
    remaining_articles_text = ""

    for i, article in enumerate(extracted_articles):
        article_text_with_header = f"--- 参考記事 {i+1} ({article['url']}) ---\n{article['text']}\n\n"
        
        # 現在のトークンが上限内であれば、全文を追加
        if current_tokens < full_text_token_limit:
            final_context += article_text_with_header
            # トークン数を概算（文字数 / 2）で更新。正確ではないが速度優先。
            current_tokens += len(article_text_with_header) / 2 
        # 上限を超えたら、残りの記事は「要約対象」としてまとめる
        else:
            remaining_articles_text += article_text_with_header
    
    # 要約対象の記事が一つでもあれば、一度だけ要約APIを呼び出す
    if remaining_articles_text:
        status_placeholder.info("トークン上限を超えたため、残りの記事を要約しています...")
        summarize_prompt = f"以下の複数の記事群を、ユーザーのリクエストに沿うように重要なポイントを一つの文章にまとめてください。\n\nユーザーリクエスト: {user_prompt}\n\n--- 記事群 ---\n{remaining_articles_text}"
        try:
            summary_response = st.session_state.gemini_lite_model.generate_content(summarize_prompt)
            final_context += f"--- 複数の参考記事の要約 ---\n{summary_response.text}\n\n"
            st.info("残りの記事の要約が完了しました。")
        except Exception as e:
            st.warning(f"要約処理中にエラーが発生しました: {e}")
    # --- ハイブリッド戦略ここまで ---

    status_placeholder.info("5/5: Geminiによる最終的な記事生成を開始します...")
    return final_context

# core_logic.py の中

def parse_gemini_output(text, fallback_prompt):
    """
    Geminiの出力をパースしてタイトルと本文を抽出します。
    期待通りの形式でなくてもエラーにならないようにします。
    """
    # 最初に必ずデフォルト値を定義しておく
    title = f"生成記事: {fallback_prompt[:20]}..."
    content = ""

    # AIの応答に期待するキーワードが含まれているかチェック
    if "タイトル：" in text and "本文：" in text:
        try:
            # "本文：" を基準にテキストを分割
            title_part, content_part = text.split("本文：", 1)
            # タイトル部分から "タイトル：" を削除して整形
            title = title_part.replace("タイトル：", "").strip()
            # 本文部分を整形
            content = content_part.strip()
        except Exception:
            # 分割に失敗した場合は、応答全体を本文として扱う
            content = text
    else:
        # キーワードが含まれていない場合も、応答全体を本文として扱う
        content = text
    
    # titleとcontentが必ず定義された状態で値を返す
    return title, content

# ... これ以降の run_new_page_process などの関数は変更ありません ...
# --- run_new_page_process 関数を修正 ---
def run_new_page_process(database_id, user_prompt, ai_persona, uploaded_files, source_url, search_count, full_text_token_limit, status_placeholder, results_placeholder):
    try:
        full_text_context = ""
        if uploaded_files:
            status_placeholder.info("アップロードされたファイルを読み込んでいます...")
            full_text_context = process_uploaded_files(uploaded_files)
        elif source_url:
            full_text_context = get_content_from_single_url(source_url, status_placeholder)
        else:
            full_text_context = generate_content_from_web(user_prompt, search_count, full_text_token_limit, status_placeholder, results_placeholder)
        if not full_text_context:
            st.error("参考情報が見つからなかったため、処理を中断しました。")
            return
        # ... (これ以降のロジックは変更なし) ...
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
        text = response.text
        title, content = parse_gemini_output(text, user_prompt)
        with results_placeholder.container(border=True):
            st.markdown(f"### プレビュー: {title}")
            st.markdown(content)
            st.info("上記の内容でNotionに新しいページを作成します。")
        status_placeholder.info("Notionに新しいページを作成中...")
        all_blocks = markdown_to_notion_blocks(content)
        db_info = st.session_state.notion_client.databases.retrieve(database_id=database_id)
        title_prop_name = next((k for k, v in db_info['properties'].items() if v['type'] == 'title'), 'Name')
        parent_payload = {"database_id": database_id}
        properties_payload = {title_prop_name: {"title": [{"text": {"content": title}}]}}
        created_page = st.session_state.notion_client.pages.create(parent=parent_payload, properties=properties_payload, children=all_blocks[:100])
        if len(all_blocks) > 100:
            page_id = created_page['id']
            for i in range(100, len(all_blocks), 100):
                chunk = all_blocks[i:i+100]
                st.session_state.notion_client.blocks.children.append(block_id=page_id, children=chunk)
        st.balloons()
        status_placeholder.success(f"✅ 新規ページ「{title}」の作成が完了しました！")
    except Exception as e:
        st.error(f"❌ 新規ページ作成中にエラーが発生しました: {e}")
        st.code(traceback.format_exc())


# --- run_edit_page_process 関数を修正 ---
def run_edit_page_process(page_id, user_prompt, ai_persona, uploaded_files, source_url, search_count, full_text_token_limit, status_placeholder, results_placeholder):
    try:
        status_placeholder.info("1/4: Notionから既存のコンテンツを読み込んでいます...")
        existing_blocks_response = st.session_state.notion_client.blocks.children.list(block_id=page_id)
        existing_markdown = notion_blocks_to_markdown(existing_blocks_response.get('results', []), st.session_state.notion_client)
        with results_placeholder.container(border=True):
            with st.expander("現在のページ内容（Markdown）"):
                st.markdown(existing_markdown or "（このページは空です）")
        
        full_text_context = ""
        if uploaded_files:
            status_placeholder.info("2/4: アップロードされたファイルを読み込んでいます...")
            full_text_context = process_uploaded_files(uploaded_files)
        elif source_url:
            status_placeholder.info("2/4: 単一URLから情報を抽出しています...")
            full_text_context = get_content_from_single_url(source_url, status_placeholder)
        else:
            status_placeholder.info("2/4: Webからの情報収集を開始します...")
            full_text_context = generate_content_from_web(user_prompt, search_count, full_text_token_limit, status_placeholder, results_placeholder)
        
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
        # ... (これ以降のロジックは変更なし) ...
        response = st.session_state.gemini_model.generate_content(final_prompt)
        text = response.text
        title, content = parse_gemini_output(text, user_prompt)
        with results_placeholder.container(border=True):
            st.markdown(f"### プレビュー（追記部分）: {title}")
            st.markdown(content)
            st.info("上記の内容をNotionページの末尾に追記します。")
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