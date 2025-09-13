import os
import logging
import socks
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.custom import Button
from telethon.errors.rpcerrorlist import UsernameNotOccupiedError, ChatAdminRequiredError
from telethon.tl.functions.messages import GetHistoryRequest

# --- 配置 ---
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.INFO)

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
BOT_USERNAME = os.getenv('BOT_USERNAME')

# 默认搜索消息数量
SEARCH_LIMIT_DEFAULT = 200
# 最大搜索数量限制
SEARCH_LIMIT_MAX = 1000
# 分页设置
PAGE_SIZE = 10

# --- 状态管理 ---
user_sessions = {}

# --- 机器人逻辑 ---
proxy_ip = '127.0.0.1'
proxy_port = 7890

client = TelegramClient(
    'bot_session',
    API_ID,
    API_HASH,
    proxy=(socks.SOCKS5, proxy_ip, proxy_port)
)


# --- 辅助函数：格式化页面内容和按钮 ---
def format_page(chat_id):
    session = user_sessions.get(chat_id)
    if not session:
        return "会话已过期，请重新发起查询。", None

    sorted_list = session['sorted_list']
    current_page = session['current_page']
    channel = session['channel']
    total_found = len(sorted_list)
    total_pages = (total_found + PAGE_SIZE - 1) // PAGE_SIZE

    start_index = current_page * PAGE_SIZE
    end_index = start_index + PAGE_SIZE
    page_items = sorted_list[start_index:end_index]

    text = (
        f"**频道 `{channel}` Reaction Top 榜单**\n"
        f"*(在扫描的 {session['limit']} 条消息中，共找到 {total_found} 条带 reaction 的消息)*\n\n"
        f"--- **第 {current_page + 1} / {total_pages} 页** ---\n\n"
    )

    if not page_items:
        text += "这一页没有内容了。"
        return text, None

    for i, (reactions, message) in enumerate(page_items):
        rank = start_index + i + 1
        # --- 关键修正 ---
        # 使用 session 中可靠的 channel 用户名，而不是 message 对象中不稳定的那个
        message_link = f"https://t.me/{channel.lstrip('@')}/{message.id}"
        preview = message.text[:30].replace('\n', ' ') + '...' if message.text else "[媒体消息]"
        text += f"**{rank}.** ❤️ **{reactions}** | [{preview}]({message_link})\n"

    buttons_row = []
    if current_page > 0:
        buttons_row.append(Button.inline("⬅️ 上一页", data=f"prev_{current_page}"))
    if end_index < total_found:
        buttons_row.append(Button.inline("下一页 ➡️", data=f"next_{current_page}"))

    return text, buttons_row if buttons_row else None


# --- 事件处理器：处理 /top 命令 ---
@client.on(events.NewMessage(pattern='/top'))
async def find_top_post_handler(event):
    chat = await event.get_chat()
    logging.info(f"收到来自 {chat.first_name} 的命令: {event.raw_text}")

    parts = event.raw_text.split()
    if len(parts) < 2 or not parts[1].startswith('@'):
        await event.reply("🤔 **命令格式不正确！**\n请使用: `/top @channel_username [数量]`")
        return

    channel_username = parts[1]
    limit = SEARCH_LIMIT_DEFAULT
    if len(parts) > 2 and parts[2].isdigit():
        limit = int(parts[2])
        if limit > SEARCH_LIMIT_MAX:
            limit = SEARCH_LIMIT_MAX
            await event.reply(f"😅 为了防止滥用，最大搜索数量已限制为 **{SEARCH_LIMIT_MAX}** 条。")

    processing_message = await event.reply(f"好的，正在准备从 `{channel_username}` 频道中搜索消息...")

    try:
        target_channel = await client.get_entity(channel_username)

        history = await client(
            GetHistoryRequest(peer=target_channel, limit=1, offset_id=0, offset_date=None, add_offset=0, max_id=0,
                              min_id=0, hash=0))
        total_messages = history.messages[0].id if history.messages else 0
        messages_to_scan = min(limit, total_messages)

        await processing_message.edit(
            f"频道约有 **{total_messages}** 条消息。\n正在扫描指定的 **{messages_to_scan}** 条，请耐心等待...")

        messages_with_reactions = []
        processed_count = 0
        BATCH_SIZE = 200

        async for message in client.iter_messages(target_channel, limit=messages_to_scan):
            processed_count += 1
            if message.reactions:
                current_reactions = sum(r.count for r in message.reactions.results)
                messages_with_reactions.append((current_reactions, message))

            if processed_count % BATCH_SIZE == 0:
                progress = (processed_count / messages_to_scan) * 100
                await processing_message.edit(f"扫描进度: **{progress:.1f}%** ({processed_count}/{messages_to_scan})")
                await asyncio.sleep(1)

        if not messages_with_reactions:
            await processing_message.edit(f"在扫描的 **{messages_to_scan}** 条消息中，没有找到任何带有 reaction 的消息。")
            return

        sorted_list = sorted(messages_with_reactions, key=lambda item: item[0], reverse=True)

        user_sessions[chat.id] = {
            'sorted_list': sorted_list,
            'current_page': 0,
            'channel': channel_username,
            'limit': messages_to_scan
        }

        text, buttons = format_page(chat.id)
        await processing_message.edit(text, buttons=buttons, link_preview=False)

    except Exception as e:
        logging.error(f"处理过程中发生未知错误: {e}")
        await processing_message.edit(f"出错了！\n错误信息: `{e}`")


# --- 事件处理器：处理按钮点击（回调查询） ---
@client.on(events.CallbackQuery)
async def button_click_handler(event):
    chat_id = event.chat_id
    session = user_sessions.get(chat_id)

    if not session:
        await event.answer("这个查询已经过期了，请重新发起。", alert=True)
        return

    data = event.data.decode('utf-8')
    action, page_str = data.split('_')
    page = int(page_str)

    if action == 'next':
        session['current_page'] = page + 1
    elif action == 'prev':
        session['current_page'] = page - 1

    try:
        text, buttons = format_page(chat_id)
        await event.edit(text, buttons=buttons, link_preview=False)
    except Exception as e:
        logging.error(f"编辑消息时出错: {e}")
        await event.answer("无法更新页面，可能消息内容没有变化。", alert=True)

    await event.answer()


# --- 启动机器人 ---
async def main():
    await client.start()
    logging.info("用户客户端已成功启动！")
    me = await client.get_me()
    bot_info = await client.get_entity(BOT_USERNAME)
    logging.info(f"以 @{me.username} 的身份登录")
    logging.info(f"机器人 @{bot_info.username} 正在监听命令...")
    await client.run_until_disconnected()


if __name__ == '__main__':
    client.loop.run_until_complete(main())
