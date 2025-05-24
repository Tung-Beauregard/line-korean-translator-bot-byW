from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI
import os

app = Flask(__name__)

# 環境變數設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY)

# 主頁測試
@app.route("/")
def home():
    return "✅ Line Bot 正常運作中！"

# LINE Webhook
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

# 核心翻譯邏輯
import re

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_input = event.message.text.strip()

    # 如果完全沒有中文或韓文，就不回覆
    if not re.search(r"[\u4e00-\u9FFFㄱ-ㅎ가-힣]", user_input):
        print("🟡 純英文或非目標語言訊息，略過回覆：", user_input)
        return  # 不回覆，靜默略過

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一個中韓雙向翻譯機器人，請依照以下規則翻譯使用者的訊息：\n\n"
                        "1. 若使用者輸入為「中文」（不論是繁體或簡體），請翻譯為韓文。\n"
                        "2. 若輸入為「韓文」，請翻譯為繁體中文（台灣用字）。\n"
                        "3. 若輸入為「中英混合」，請將中文翻譯為韓文，英文部分保留。\n"
                        "4. 若輸入為「韓英混合」，請將韓文翻譯為繁體中文，英文保留。\n"
                        "5. 若輸入純英文、emoji、數字或標點符號，不要翻譯，原樣保留。\n"
                        "6. 請勿改寫、勿補全，僅翻譯指定部分。\n"
                        "7. 只回傳翻譯後的文字，不附加說明或語言標籤。"
                    )
                },
                {"role": "user", "content": user_input}
            ]
        )
        reply_text = response.choices[0].message.content.strip()

    except Exception as e:
        reply_text = f"❌ 無法翻譯：{str(e)}"

    print("🟢 收到訊息：", user_input)
    print("🔵 回應訊息：", reply_text)

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )



# 正確使用 Render 指定的 PORT
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
