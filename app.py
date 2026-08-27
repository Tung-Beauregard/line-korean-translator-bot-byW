from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from openai import OpenAI
import os
import re

app = Flask(__name__)

# 環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY)

@app.route("/")
def home():
    return "✅ Line Bot 正常運作中！"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_input = event.message.text.strip()

    # 泰式笑聲（連續3個以上的5，如 555、5555）視為需要翻譯的內容
    is_thai_laugh = bool(re.fullmatch(r"5{3,}", user_input))

    # 跳過純英文或回應與輸入完全相同的內容（無需翻譯）
    if not is_thai_laugh and not re.search(r"[\u4e00-\u9FFF\u0E00-\u0E7F]", user_input):
        print("🟡 純英文或無需翻譯內容，略過：", user_input)
        return

    # 跳過描述翻譯條件或指令型的語句
    if re.search(r"(只在.*翻譯|不要翻譯|開啟翻譯|關閉翻譯|翻譯規則|語言規則|才翻譯|才會翻譯)", user_input):
        print("🟡 指令性說明語句略過：", user_input)
        return

    chinese_chars = re.findall(r"[\u4e00-\u9FFF]", user_input)
    thai_chars = re.findall(r"[\u0E00-\u0E7F]", user_input)
    english_chars = re.findall(r"[A-Za-z]", user_input)

    # 中文比例過低的中英混合句略過（除非是泰英混合）
    if len(chinese_chars) < 2 and len(english_chars) > 0 and len(thai_chars) == 0:
        print("🟡 中文比例過低的中英混合句，略過：", user_input)
        return

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一個中泰雙向翻譯機器人，請依照以下規則翻譯使用者的訊息：\n\n"
                        "1. 若使用者輸入為中文（不論是簡體或繁體），請翻譯為泰文。\n"
                        "2. 若輸入為泰文，請翻譯為繁體中文（台灣用字），不可使用簡體字。\n"
                        "2. 若輸入為泰文，請翻譯為繁體中文（台灣用字），不可使用簡體字，也不可混入日文平假名或片假名（如：ちゃん）。\n"
                        "3. 若輸入為中英混合，請將所有出現的中文字(哪怕只有一個字)全部翻譯為泰文，**不可以跳過或省略任何中文字詞**。英文部分必須保留原樣，不可翻譯。\n"
                        "4. 若輸入為泰文或泰英混合句，請整句翻譯為繁體中文（台灣用字）。即使句中包含英文或外來語（如 bug、line），也應將整句翻譯為自然的繁體中文，不可保留原本的泰文語序或結構。即使句首為英文單詞（如 bug），只要整句語序為泰文，就必須翻譯整句為繁體中文，不可省略或照抄。\n"
                        "5. 若輸入為純英文、emoji、數字或標點符號，請不要翻譯，保持原樣。\n"
                        "6. 請勿補全語句、不得加強語氣或添加說明，只進行最少必要的翻譯。\n"
                        "7. 若使用者輸入的中文為笑聲詞（如 哈哈、呵呵、哈哈哈），請翻譯為對應的泰文笑聲（如 555、5555），不得照抄原文。\n"
                        "8. 若使用者輸入為泰文笑聲（如 555、5555），請翻譯為繁體中文笑聲詞（如 哈哈、呵呵），不得改寫為其他語意。\n"
                        "9. 僅回傳翻譯後的純文字內容，不附加語言標籤或其他說明。\n"
                        "10. 若輸入為中泰混合句，請翻譯所有泰文為自然的繁體中文，中文部分保留原樣。不得保留泰文詞彙或語序。\n"
                        "11. 嚴禁回應與輸入內容相同的原文，無論中泰混合或其他情況，皆須翻譯對應語言內容，不可照抄使用者訊息。\n"
                        "12. 禁止將任何原始詞彙（中、泰、英）替換為與原文語意不同的近義詞。若已經出現「เจ๋งมาก」，則不要改寫為「สุดยอด」等不同語氣。\n"
                        "13. 翻譯成泰文時，語氣設定為男性對女性說話，句尾語氣助詞一律使用「ครับ」。用字遣詞需禮貌且溫柔，避免生硬、命令式或過於直接的講法，語氣柔和自然，但不可為了溫柔而更動原文語意或添加原文沒有的內容。\n"
                        "14. 若輸入為中文（翻譯成泰文的情況），請在泰文翻譯下方多加一行羅馬拼音讀音，格式為括號包住，例如第一行是泰文翻譯，第二行是「(羅馬拼音)」。若輸入為泰文（翻譯成中文的情況），則不需要加註讀音，只回傳中文翻譯即可。\n"
                        "【範例1】輸入：This 已經 very 勁 → 回應：This แล้ว very เจ๋งมากครับ\n(This láew very jèng mâak kráp)\n"
                        "【範例2】輸入：Line มีฟีเจอร์อะไรบ้าง? → 回應：Line 有什麼功能？\n"
                        "【範例3】輸入：Bug อยู่ที่ไหนนะ? → 回應：Bug 在哪裡呢？\n"
                        "【範例4】輸入：Bug ที่มีโอกาสเกิดขึ้นมากที่สุดอยู่ที่ไหน? → 回應：Bug 最可能出現的位置是哪裡？"
                    )
                },
                {"role": "user", "content": user_input}
            ]
        )
        reply_text = response.choices[0].message.content.strip()

        if reply_text == user_input:
            print("🟡 回應與輸入相同，略過回覆：", reply_text)
            return

    except Exception as e:
        reply_text = f"❌ 無法翻譯：{str(e)}"

    print("🟢 收到訊息：", user_input)
    print("🔵 回應訊息：", reply_text)

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
