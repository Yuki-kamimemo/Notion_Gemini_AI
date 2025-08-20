import streamlit as st
from ddgs import DDGS
import trafilatura
import traceback
import httpx
import io
import pdfplumber
import docx

from notion_utils import notion_blocks_to_markdown, markdown_to_notion_blocks

# --- HTTPクライアントをセッション管理 ---
@st.cache_resource
def get_httpx_client():
    """再利用可能なhttpx.Clientインスタンスを取得"""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'}
    return httpx.Client(headers=headers, follow_redirects=True, timeout=15.0)

def process_uploaded_files(uploaded_files):
    full_text = ""
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
                full_text += io.StringIO(uploaded_file.getvalue().decode("utf-8")).read() + "\n"
        except Exception as e:
            st.error(f"ファイル '{uploaded_file.name}' の読み込み中にエラー: {e}")
        full_text += "\n\n"
    return full_text

def get_content_from_single_url(url: str, status_placeholder):
    status_placeholder.info(f"URLから本文を抽出中: {url}")
    client = get_httpx_client()
    try:
        response = client.get(url)
        response.raise_for_status() 
        extracted = trafilatura.extract(response.text, include_comments=False, include_tables=True)
        return f"--- 参考URL: {url} ---\n\n{extracted}" if extracted else None
    except httpx.HTTPStatusError as e:
        st.warning(f"URLへのアクセスに失敗（{e.response.status_code}エラー）: {url}")
        return None
    except Exception as e:
        st.error(f"URLの処理中に予期せぬエラー: {url}\n原因: {e}")
        return None

# ★★★ `time_limit` 引数を追加 ★★★
def generate_content_from_web(user_prompt: str, search_count: int, full_text_token_limit: int, time_limit: str, status_placeholder, results_placeholder):
    status_placeholder.info("1/5: キーワード抽出中...")
    keyword_prompt = f"リクエスト文：`{user_prompt}`\n\nこのリクエストに最も適したWeb検索キーワードを5つ以内で作成して検索してください。"
    try:
        keyword_response = st.session_state.gemini_lite_model.generate_content(keyword_prompt)
        search_keywords = keyword_response.text.strip().replace("\n", "") or user_prompt
    except Exception:
        search_keywords = user_prompt
        
    search_query = f"{search_keywords} -filetype:pdf"
    status_placeholder.info(f"2/5: 「{search_query}」でWeb検索中...")
    
    try:
        # ★★★ `timelimit` をDDGS検索に適用 ★★★
        search_results = list(DDGS().text(search_query, max_results=search_count, timelimit=time_limit))
    except Exception as e:
        st.error(f"Web検索中にエラーが発生しました: {e}")
        return None

    if not search_results:
        st.warning("Web検索で情報を取得できませんでした。")
        return None

    with results_placeholder.container():
        st.info(f"🤖 **検索クエリ:** `{search_query}` (期間: {time_limit or '指定なし'})")
        with st.expander(f"参考Webサイト ({len(search_results)}件)"):
            for r in search_results: st.markdown(f"- [{r.get('title')}]({r.get('href')})")
    
    status_placeholder.info("3/5: Webページから本文を抽出中...")
    client = get_httpx_client()
    extracted_articles = []
    for i, result in enumerate(search_results, 1):
        url = result.get('href')
        if not url: continue
        status_placeholder.info(f"3/5: Webページから本文を抽出中... ({i}/{len(search_results)})")
        try:
            response = client.get(url)
            response.raise_for_status() # 4xx, 5xxエラーをチェック
            extracted = trafilatura.extract(response.text, include_comments=False, include_tables=True)
            if extracted:
                extracted_articles.append({"url": url, "text": extracted})
            else:
                st.warning(f"本文の抽出に失敗: {url}")
        except httpx.HTTPStatusError as e:
            # ★★★ 404エラーなどをユーザーに分かりやすく通知 ★★★
            st.warning(f"URLへのアクセスに失敗 ({e.response.status_code}エラー): {url}")
        except Exception:
            st.warning(f"URLの処理中にタイムアウトまたはその他エラー: {url}")
            
    if not extracted_articles:
        st.error("どのWebサイトからも記事本文を抽出できませんでした。")
        return None
    
    status_placeholder.info("4/5: 参考情報を構築中...")
    final_context, remaining_articles_text = "", ""
    current_tokens = 0
    
    for article in extracted_articles:
        article_text = f"--- 参考記事: {article['url']} ---\n{article['text']}\n\n"
        # トークン数を概算（文字数 / 1.5）で計算
        article_tokens = len(article_text) / 1.5
        if (current_tokens + article_tokens) < full_text_token_limit:
            final_context += article_text
            current_tokens += article_tokens
        else:
            remaining_articles_text += article_text
    
    if remaining_articles_text:
        status_placeholder.info("トークン上限を超えたため、残りの記事を要約中...")
        summarize_prompt = f"ユーザーリクエスト「{user_prompt}」に沿うように、以下の記事群の要点を一つにまとめてください。\n\n--- 記事群 ---\n{remaining_articles_text}"
        try:
            summary_response = st.session_state.gemini_lite_model.generate_content(summarize_prompt)
            final_context += f"--- 複数の参考記事の要約 ---\n{summary_response.text}\n\n"
        except Exception as e:
            st.warning(f"要約処理中にエラーが発生: {e}")

    status_placeholder.info("5/5: AIによる最終記事の生成を開始...")
    return final_context

def parse_gemini_output(text, fallback_prompt):
    try:
        if "タイトル：" in text and "本文：" in text:
            title_part, content_part = text.split("本文：", 1)
            title = title_part.replace("タイトル：", "").strip()
            return title, content_part.strip()
    except Exception:
        pass
    return f"生成記事: {fallback_prompt[:20]}...", text

def _run_process(process_logic, **kwargs):
    try:
        process_logic(**kwargs)
    except Exception as e:
        kwargs['status_placeholder'].error(f"❌ 処理中に予期せぬエラーが発生しました: {e}")
        st.code(traceback.format_exc())

def _page_creation_logic(database_id, user_prompt, ai_persona, uploaded_files, source_url, search_count, full_text_token_limit, time_limit, status_placeholder, results_placeholder):
    context_generator = process_uploaded_files if uploaded_files else get_content_from_single_url if source_url else generate_content_from_web
    context_args = {'uploaded_files': uploaded_files} if uploaded_files else {'url': source_url, 'status_placeholder': status_placeholder} if source_url else {'user_prompt': user_prompt, 'search_count': search_count, 'full_text_token_limit': full_text_token_limit, 'time_limit': time_limit, 'status_placeholder': status_placeholder, 'results_placeholder': results_placeholder}
    
    full_text_context = context_generator(**context_args)
    if not full_text_context:
        st.error("参考情報が見つからなかったため、処理を中断しました。")
        status_placeholder.empty()
        return

    final_prompt = f"# 命令\n{ai_persona}として、「参考情報」と「リクエスト」に基づき記事を作成してください。\n出力は「タイトル：～」「本文：～」の形式で、本文はMarkdownで記述してください。\n\n# 参考情報\n{full_text_context}\n\n# リクエスト\n{user_prompt}"
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

def _page_edit_logic(page_id, **kwargs):
    status_placeholder = kwargs['status_placeholder']
    status_placeholder.info("1/4: 既存コンテンツを読み込み中...")
    existing_blocks = st.session_state.notion_client.blocks.children.list(block_id=page_id).get('results', [])
    existing_markdown = notion_blocks_to_markdown(existing_blocks, st.session_state.notion_client)
    
    with kwargs['results_placeholder'].container(border=True):
        with st.expander("現在のページ内容（Markdown）"):
            st.markdown(existing_markdown or "（空のページです）")
            
    # （...以降のロジックは新規作成とほぼ同じなので省略...）
    
def run_new_page_process(**kwargs):
    _run_process(_page_creation_logic, **kwargs)

def run_edit_page_process(**kwargs):
    _run_process(_page_edit_logic, **kwargs)