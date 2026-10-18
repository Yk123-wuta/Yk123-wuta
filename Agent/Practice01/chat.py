import os
import sys
import configparser

from openai import OpenAI

config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")

config = configparser.ConfigParser()
if not config.read(config_path):
    sys.exit("错误：找不到配置文件 config.ini")

if not config.has_section("default"):
    sys.exit("错误：配置文件缺少 [default] section")

try:
    base_url = config.get("default", "base_url")
    model = config.get("default", "model")
    api_key = config.get("default", "api_key")
except configparser.NoOptionError as e:
    sys.exit(f"错误：配置文件缺少字段 {e.option}")

client = OpenAI(base_url=base_url, api_key=api_key)

print("你好，我是AI助手")

messages = []

while True:
    try:
        prompt = input("请输入提示词：")
    except (EOFError, KeyboardInterrupt):
        print("\n已退出。")
        break

    if not prompt.strip():
        continue

    print("---")

    messages.append({"role": "user", "content": prompt})

    try:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        full_reply = ""
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                print(content, end="", flush=True)
                full_reply += content
        print()
        messages.append({"role": "assistant", "content": full_reply})
    except Exception as e:
        print(f"错误：API 调用失败：{e}")
        messages.pop()
