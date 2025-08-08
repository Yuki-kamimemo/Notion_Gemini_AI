@echo off
rem --- 仮想環境を有効化し、Streamlitアプリケーションを起動するバッチファイル ---

rem コンソールの文字コードをUTF-8に設定し、文字化けを防ぎます 
chcp 65001 > nul

rem このバッチファイルがあるディレクトリに移動します 
cd /d "%~dp0"

echo --------------------------------------------------
echo  Notion AI アプリケーション 起動シーケンス
echo --------------------------------------------------
echo.

rem 仮想環境(.venv)が存在するかチェック
IF NOT EXIST .\.venv\ (
    echo [エラー] 仮想環境(.venv)が見つかりません。
    echo まず、このフォルダで以下のコマンドを実行して仮想環境を構築してください:
    echo python -m venv .venv
    echo.
    pause
    exit /b
)

rem 仮想環境を有効化
echo [1/2] 仮想環境を有効化しています...
CALL .\.venv\Scripts\activate.bat
echo 仮想環境が有効になりました。
echo.

rem Streamlitアプリケーションを起動
echo [2/2] Streamlitアプリケーションを起動します...
echo ブラウザが自動で開かない場合は、ターミナルに表示されるURLを開いてください。
echo.
streamlit run app.py [cite: 2]

echo.
echo アプリケーションサーバを停止しました。
pause