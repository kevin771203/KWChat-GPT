import threading
import time
from collections import OrderedDict
from flask import Flask, request, jsonify, render_template
import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel
from openai import OpenAI
import signal
import sys
import os

# 初始化 Flask 應用
app = Flask(__name__)
client = OpenAI(api_key='your api key')

# 定義 KeyChat 類別以驗證 API 回應結構
class KeyChat(BaseModel):
    response: str
    extracted_keywords: list[str]


class ChatBot:
    def __init__(self, role_name):  # 構造函數，初始化 ChatBot 類別
        self.role_name = role_name
        self.message_flow = []
        self.query = ''
        self.last_google_search_time = 0
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.cache = OrderedDict()

    def update_message_flow(self, role, content):
        self.message_flow.append({'role': role, 'content': content})
        del self.message_flow[:-12]

    # 獲取AI助手的回應
    def get_assistant_response(self):
        messages = [
            {
                'role': 'system',
                'content': (
                    f'你叫{self.role_name}，用繁體中文（zh-TW）回應。'
                    '語氣輕鬆幽默，富有創意，不主動詢問聊天話題。'
                    '遇具體話題時，萃取關鍵字供 Google 搜尋；否則回傳空列表。'
                ),
            },
        ]
        if self.cache:# 如果有緩存的內容
            count = 0
            for key in reversed(self.cache):
                messages.append({
                    'role': 'assistant',
                    'content': self.cache[key]# 添加ＡＩ助手的緩存訊息
                })
                count += 1
                if count == 2:
                    break
        elif self.query:
            messages.append({
                'role': 'assistant',
                'content': '這話題有點意思～讓我去 PTT 八卦一下，找到料再來跟你分享！',
            })

        messages.extend(self.message_flow)

        completion = client.beta.chat.completions.parse(
            model='gpt-4o-mini',
            messages=messages,
            response_format=KeyChat,
        )

        return completion.choices[0].message

    def fetch_html_soup(self, url, headers=None):# 獲取 HTML 網頁並解析
        try:
            html = requests.get(url, headers=headers, timeout=10)
            html.raise_for_status()
            html.encoding = 'UTF-8'
            return BeautifulSoup(html.text, 'lxml')
        except Exception as e:
            print(f'[Error fetching or parsing content from {url}] {e}')
            return None

    def extract_ptt_content(self, soup):# 從 PTT 網頁中提取內容
        extracted_content = []
        main_content = soup.find('div', id='main-content')# 獲取主內容區域

        if main_content:
            for child in main_content.children:
                try:
                    classes = child.get('class', [])
                except AttributeError:
                    extracted_content.append(child.strip())
                    continue

                if child.name == 'span' or 'article-metaline-right' in classes:# 過濾掉無關元素
                    continue

                if 'push' in classes:
                    if (
                        (user_id := child.find('span', class_='f3 hl push-userid'))
                        and (content := child.find('span', class_='f3 push-content'))
                    ):
                        extracted_content.append(f'{user_id.text}{content.text}')# 添加推文到提取內容

                elif 'article-metaline' in classes:
                    if (
                        (tag := child.find('span', class_='article-meta-tag'))
                        and (value := child.find('span', class_='article-meta-value'))
                    ):
                        extracted_content.append(f'{tag.text} {value.text}')

                else:
                    extracted_content.append(child.text.strip())

        return '\n'.join(extracted_content)

    def process_query(self):
        while not self.stop_event.is_set():
            if self.query and time.monotonic() - self.last_google_search_time > 3.0:
                search_url = f'https://www.google.com/search?q={self.query}'
                headers = {
                    'User-Agent': (
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                        '(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                    ),
                }

                if soup := self.fetch_html_soup(search_url, headers):
                    self.last_google_search_time = time.monotonic()
                    with self.lock:
                        self.query = ''

                    scraped = False
                    for result in soup.find_all('a', href=True):
                        url = result['href']
                        if 'https://www.ptt.cc/' in url:
                            if url in self.cache:
                                with self.lock:
                                    self.cache.move_to_end(url)

                            elif (
                                    (soup := self.fetch_html_soup(url))
                                    and (content := self.extract_ptt_content(soup))
                            ):
                                with self.lock:
                                    self.cache[url] = content
                                    if len(self.cache) > 12:
                                        self.cache.popitem(last=False)
                            print(f'搜尋到的連結有:{url}')
                            if scraped:
                                break
                            scraped = True

                else:
                    time.sleep(0.5)

            else:
                time.sleep(0.5)

    def start_keyword_thread(self):
        threading.Thread(target=self.process_query, daemon=True).start()
    def stop_keyword_thread(self):
        self.stop_event.set()
def signal_handler(sig, frame):
    print('Gracefully shutting down...')
    bot.stop_keyword_thread()
    sys.exit(0)


bot = ChatBot(role_name='BI教父-小K')
bot.start_keyword_thread()
signal.signal(signal.SIGINT, signal_handler)

@app.route('/')# 設定根路由
def index():
    if not os.path.exists('templates/index.html'):
        abort(404)
    return render_template('index.html')# 渲染 index.html 頁面


@app.route('/chat', methods=['POST'])  # 設定 /chat 路由，允許 POST 請求
def chat():
    user_message = request.json.get('message')  # 從請求的 JSON 數據中獲取用戶訊息
    bot.update_message_flow('user', user_message)  # 更新聊天歷史，添加用戶訊息

    try:
        assistant_response_data = bot.get_assistant_response()  # 獲取助手的回應數據
    except Exception as e:  # 如果獲取回應時發生錯誤
        return jsonify({'error': str(e)}), 500  # 返回錯誤信息和 500 錯誤代碼

    if refusal := assistant_response_data.refusal:  # 檢查助手是否拒絕提供回應
        return jsonify({'refusal': refusal}), 400  # 如果拒絕，返回拒絕信息和 400 錯誤代碼
    else:
        key_chat = assistant_response_data.parsed
        print(f'Bot: {key_chat.response}')
        bot.update_message_flow('assistant', key_chat.response)
        print(f'Extracted Keywords: {key_chat.extracted_keywords}')

        if key_chat.extracted_keywords:  # 如果助手提取了關鍵字
            # 過濾並清理關鍵字，去掉空白
            query = '+'.join(keyword.strip() for keyword in key_chat.extracted_keywords if keyword.strip()) + '+ptt'
            with bot.lock:  # 獲取鎖以保護關鍵字的更新
                bot.query = query  # 更新機器人的關鍵字列表

        return jsonify({'response': key_chat.response})  # 返回助手回應的 JSON 響應


if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=8000)# 啟動 Flask 應用

    except KeyboardInterrupt:
        signal_handler(signal.SIGINT, None)