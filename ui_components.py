import streamlit as st

# --- 定数定義 ---
PERSONA_OPTIONS = {
    "プロのライター": "あなたはプロのライターです。",
    "マーケティング担当者": "あなたは経験豊富なマーケティング担当者です。読者のエンゲージメントを高めることを意識してください。",
    "技術ドキュメントライター": "あなたは正確で分かりやすい文章を書く技術ドキュメントライターです。専門用語は適切に解説してください。",
    "フレンドリーな解説者": "あなたは複雑なトピックを、親しみやすくフレンドリーな口調で解説する専門家です。",
    "社内資料作成・利用ガイドの作成者": "あなたは、社内での利用を目的とした公式な資料やガイドを作成する担当者です。以下の点を重視してください：情報の正確性と客観性、専門用語の統一、後から他の人が追記・編集しやすいような構造的な記述。",
    "カスタム": "カスタム...",
}

PROMPT_TEMPLATES = {
    "記事作成": "{topic}について、読者の興味を引く魅力的な記事を作成してください。",
    "要約": "{topic}について、重要なポイントを箇条書きで分かりやすく要約してください。",
    "アイデア出し": "{topic}というテーマで、ユニークなアイデアを5つ提案してください。",
    "プレスリリース作成": "{topic}に関するプレスリリースを作成してください。背景、目的、主要な特徴、今後の展望を含めてください。",
    "利用ガイドの作成": "{topic}についての利用ガイドを作成してください。初心者でも理解できるよう、以下の構成で記述してください： 1. 概要と目的 2. 主な機能や特徴 3. 基本的な使い方（ステップバイステップ形式） 4. よくある質問（Q&A）",
    "カスタム": "カスタム...",
}

def render_main_ui(notion_client):
    """データベース選択とモード選択のUIを描画"""
    with st.spinner("データベースを読み込んでいます..."):
        from notion_utils import get_all_databases
        databases = get_all_databases(notion_client)

    if not databases:
        st.error("アクセス可能なNotionデータベースが見つかりませんでした。")
        st.stop()

    db_options = {db['id']: db['title'] for db in databases}
    selected_db_id = st.selectbox(
        "1. 操作するNotionデータベースを選択してください",
        options=db_options.keys(),
        format_func=lambda x: db_options[x]
    )
    mode = st.radio(
        "2. 実行する操作を選択してください",
        ("新しいページを作成する", "既存のページを編集・追記する"),
        horizontal=True
    )
    return selected_db_id, mode

def _render_common_form_elements(form_key_prefix=""):
    """新規作成と編集フォームで共通のUI要素を描画"""
    
    with st.expander("STEP 1: AIの役割（ペルソナ）を設定", expanded=True):
        selected_persona_key = st.selectbox(
            "AIのペルソナ:",
            options=PERSONA_OPTIONS.keys(),
            key=f"{form_key_prefix}_persona"
        )
        if selected_persona_key == "カスタム":
            ai_persona = st.text_input(
                "AIの具体的な役割を入力:",
                placeholder="例：小学生にもわかるように説明する科学の先生",
                key=f"{form_key_prefix}_custom_persona"
            )
        else:
            ai_persona = PERSONA_OPTIONS[selected_persona_key]

    with st.expander("STEP 2: 参考資料とWeb検索を設定", expanded=True):
        st.markdown("<small>※ ファイル > 単一URL > Web検索 の優先順位で情報源として利用します。</small>", unsafe_allow_html=True)
        uploaded_files = st.file_uploader(
            "参考ドキュメント (PDF/Word/Text):",
            type=['pdf', 'docx', 'txt'],
            accept_multiple_files=True,
            key=f"{form_key_prefix}_uploader"
        )
        source_url = st.text_input(
            "参考URL (上記ファイルがない場合):",
            placeholder="https://example.com/article",
            key=f"{form_key_prefix}_source_url"
        )
        
        col1, col2 = st.columns(2)
        with col1:
            search_count = st.slider(
                "Web検索数（件）:", min_value=1, max_value=15, value=5,
                help="参考資料がない場合にWeb検索する最大記事数。",
                key=f"{form_key_prefix}_slider"
            )
        with col2:
            full_text_token_limit = st.slider(
                "全文取得のトークン上限:", min_value=5000, max_value=150000, value=20000, step=5000,
                help="このトークン数までは記事の全文を使います。超えた分は要約されます。",
                key=f"{form_key_prefix}_slider_token"
            )

    with st.expander("STEP 3: AIへの指示内容を入力", expanded=True):
        selected_template_key = st.selectbox(
            "行いたい作業を選択してください:",
            options=PROMPT_TEMPLATES.keys(),
            key=f"{form_key_prefix}_template"
        )
        if selected_template_key == "カスタム":
            user_prompt = st.text_area(
                "AIへの具体的な指示を入力してください:",
                placeholder="例：{topic}について比較表を作成してください。",
                key=f"{form_key_prefix}_custom_prompt"
            )
            topic = ""
        else:
            user_prompt = PROMPT_TEMPLATES[selected_template_key]
            topic = st.text_area(
                "具体的なテーマやキーワードを入力してください:",
                placeholder="例：最新のAI技術",
                key=f"{form_key_prefix}_topic"
            )
            
    return {
        "ai_persona": ai_persona,
        "uploaded_files": uploaded_files,
        "source_url": source_url,
        "search_count": search_count,
        "full_text_token_limit": full_text_token_limit,
        "selected_template_key": selected_template_key,
        "user_prompt": user_prompt,
        "topic": topic
    }


def render_new_page_form():
    """新しいページを作成するためのフォームを描画"""
    st.subheader("新しいページを作成")
    with st.form("new_page_form"):
        common_data = _render_common_form_elements("new")
        submitted = st.form_submit_button("記事を生成する", type="primary")

    if submitted:
        final_prompt = (
            common_data["user_prompt"].format(topic=common_data["topic"])
            if common_data["selected_template_key"] != "カスタム"
            else common_data["user_prompt"]
        )
        if not final_prompt or (common_data["selected_template_key"] != "カスタム" and not common_data["topic"]):
            st.warning("作業内容とテーマの両方を入力してください。")
            return None
        if not common_data["ai_persona"]:
            st.warning("AIのペルソナを入力してください。")
            return None
            
        return {**common_data, "final_prompt": final_prompt}
    return None

def render_edit_page_form(pages):
    """既存のページを編集するためのフォームを描画"""
    st.subheader("既存のページを編集・追記")
    page_options = {p['id']: p['title'] for p in pages}
    selected_page_id = st.selectbox(
        "編集・追記したいページを選択してください",
        options=page_options.keys(),
        format_func=lambda x: page_options[x]
    )
    with st.form("edit_page_form"):
        common_data = _render_common_form_elements("edit")
        submitted = st.form_submit_button("編集・追記を実行する", type="primary")

    if submitted:
        final_prompt = (
            common_data["user_prompt"].format(topic=common_data["topic"])
            if common_data["selected_template_key"] != "カスタム"
            else common_data["user_prompt"]
        )

        if not final_prompt or (common_data["selected_template_key"] != "カスタム" and not common_data["topic"]):
            st.warning("作業内容とテーマの両方を入力してください。")
            return None
        if not common_data["ai_persona"]:
            st.warning("AIのペルソナを入力してください。")
            return None

        return {**common_data, "final_prompt": final_prompt, "selected_page_id": selected_page_id}
    return None