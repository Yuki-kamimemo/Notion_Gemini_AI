import streamlit_authenticator as stauth

# ここに、あなたのログイン用パスワードを設定してください
my_password = "Kamimemo10" 

# パスワードをハッシュ化します
hashed_password = stauth.Hasher().generate([my_password])

print("生成されたハッシュ値:")
print(hashed_password[0])