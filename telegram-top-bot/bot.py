import os
import logging
import socks
import asyncio
import traceback
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import Channel
from telethon.tl.custom import Button
from telethon.errors.rpcerrorlist import UsernameNotOccupiedError, ChatAdminRequiredError, MessageNotModifiedError

# --- 配置 ---
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.INFO)

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

# 固定搜索的消息数量
FIXED_SEARCH_LIMIT = 500000
# 分页设置
PAGE_SIZE = 10

# --- 状态管理 ---
user_sessions = {}

# --- 客户端设置 ---
PROXY_IP = os.getenv('PROXY_IP', '127.0.0.1')
PROXY_PORT = int(os.getenv('PROXY_PORT', 20170))
PROXY_ENABLED = os.getenv('PROXY_ENABLED', 'true').lower() == 'true'
proxy_config = (socks.SOCKS5, PROXY_IP, PROXY_PORT) if PROXY_ENABLED else None

# --- 客户端定义 ---
bot_client = TelegramClient('my_top_bot_session', API_ID, API_HASH, proxy=proxy_config)
user_client = TelegramClient('user_session_for_bot', API_ID, API_HASH, proxy=proxy_config)


# --- 辅助函数：格式化页面内容和按钮 (无需修改) ---
def format_page(chat_id):
    session = user_sessions.get(chat_id)
    if not session:
        return "会话已过期，请重新发起查询。", None

    sorted_list = session['sorted_list']
    current_page = session['current_page']
    display_name = session['display_name']
    link_prefix = session['link_prefix']
    total_found = len(sorted_list)
    total_pages = (total_found + PAGE_SIZE - 1) // PAGE_SIZE

    start_index = current_page * PAGE_SIZE
    end_index = start_index + PAGE_SIZE
    page_items = sorted_list[start_index:end_index]

    text = (
        f"**频道 `{display_name}` Reaction Top 榜单**\n"
        f"*(在扫描的 {session['limit']} 条消息中，共找到 {total_found} 条带 reaction 的消息)*\n\n"
        f"--- **第 {current_page + 1} / {total_pages} 页** ---\n\n"
    )

    if not page_items:
        text += "这一页没有内容了。"
        return text, []

    for i, (reactions, message) in enumerate(page_items):
        rank = start_index + i + 1
        message_link = f"https://t.me/{link_prefix}/{message.id}"
        preview = message.text[:30].replace('\n', ' ') + '...' if message.text else "[媒体消息]"
        text += f"**{rank}.** ❤️ **{reactions}** | [{preview}]({message_link})\n"

    buttons_row = []
    if current_page > 0:
        buttons_row.append(Button.inline("⬅️ 上一页", data=f"prev_{current_page}"))
    if end_index < total_found:
        buttons_row.append(Button.inline("下一页 ➡️", data=f"next_{current_page}"))

    return text, [buttons_row] if buttons_row else []


# --- 核心逻辑：处理频道请求 (新函数) ---
async def process_channel_request(event, user_input):
    """
    封装了查找、扫描和返回频道top榜单的核心逻辑。
    """
    limit = FIXED_SEARCH_LIMIT

    try:
        entity_to_find = int(user_input)
    except ValueError:
        entity_to_find = user_input.lstrip('@')

    processing_message = await event.reply(f"好的，正在查找 `{user_input}` 并准备搜索最近 **{limit}** 条消息...")

    try:
        target_channel = await user_client.get_entity(entity_to_find)

        if not isinstance(target_channel, Channel) or not (target_channel.broadcast or target_channel.megagroup):
            await processing_message.edit(f"❌ **错误：** `{user_input}` 似乎不是一个频道或超级群组。")
            return

        if target_channel.username:
            display_name = f"@{target_channel.username}"
            link_prefix = target_channel.username
        else:
            display_name = target_channel.title
            link_prefix = f"c/{target_channel.id}"

        total_messages = (await user_client.get_messages(target_channel, limit=1)).total
        messages_to_scan = min(limit, total_messages)

        await processing_message.edit(
            f"频道 **{display_name}** 约有 **{total_messages}** 条消息。\n正在扫描指定的 **{messages_to_scan}** 条，请耐心等待...")

        messages_with_reactions = []
        processed_count = 0
        BATCH_SIZE = 3000

        async for message in user_client.iter_messages(target_channel, limit=messages_to_scan):
            processed_count += 1
            if message.reactions:
                current_reactions = sum(r.count for r in message.reactions.results)
                if current_reactions > 0:
                    messages_with_reactions.append((current_reactions, message))

            if processed_count % BATCH_SIZE == 0 and processed_count < messages_to_scan:
                progress = (processed_count / messages_to_scan) * 100
                try:
                    await processing_message.edit(
                        f"扫描进度: **{progress:.1f}%** ({processed_count}/{messages_to_scan})")
                except MessageNotModifiedError:
                    pass
                await asyncio.sleep(0.5)

        await processing_message.delete()

        if not messages_with_reactions:
            await event.respond(f"在扫描的 **{messages_to_scan}** 条消息中，没有找到任何带有 reaction 的消息。")
            return

        sorted_list = sorted(messages_with_reactions, key=lambda item: item[0], reverse=True)

        user_sessions[event.chat_id] = {
            'sorted_list': sorted_list,
            'current_page': 0,
            'display_name': display_name,
            'link_prefix': link_prefix,
            'limit': messages_to_scan
        }

        text, buttons = format_page(event.chat_id)
        final_message = await event.respond(text, buttons=buttons, link_preview=False)
        user_sessions[event.chat_id]['message_id'] = final_message.id

    except (ValueError, UsernameNotOccupiedError):
        await processing_message.edit(
            f"❌ **错误：** 找不到名为 `{user_input}` 的频道、群组或用户。请检查拼写或ID是否正确。")
    except ChatAdminRequiredError:
        await processing_message.edit(
            f"❌ **错误：** 无法访问 `{display_name}` 的消息历史。这通常是一个私密频道，您的用户账户需要先加入才能访问。")
    except Exception as e:
        logging.error(f"处理过程中发生未知错误: {e}")
        logging.error(traceback.format_exc())
        await processing_message.edit(f"❌ **在扫描过程中出错了！**\n\n**错误详情:**\n`{e}`")


# --- 事件处理器：处理所有私聊消息 (核心修改) ---
@bot_client.on(events.NewMessage(func=lambda e: e.is_private))
async def message_handler(event):
    """
    处理所有私聊消息，并根据内容分发任务。
    """
    chat = await event.get_chat()
    raw_text = event.raw_text.strip()
    logging.info(f"收到来自 {chat.id} 的消息: {raw_text}")

    # 1. 处理 /start 命令
    if raw_text.lower() == '/start':
        await event.reply(
            "**欢迎使用 Reaction Top 榜单机器人！**\n\n"
            "请直接发送给我一个公开频道的用户名（如 `@telegram` 或 `telegram`）或 ID（如 `-100123456789`），我将为你查找该频道 Reaction 最高的帖子。\n\n"
            "您也可以使用 `/top @channel_name` 的格式。"
        )
        return

    # 2. 确定用户输入
    user_input = ""
    if raw_text.lower().startswith('/top '):
        parts = raw_text.split(maxsplit=1)
        if len(parts) > 1:
            user_input = parts[1]
    # 3. 如果不是命令，则将整个消息视为输入
    elif not raw_text.startswith('/'):
        user_input = raw_text

    # 如果确定了有效输入，则调用处理函数
    if user_input:
        await process_channel_request(event, user_input)
    # 否则，可以忽略该消息或发送帮助信息（当前选择忽略）


# --- 事件处理器：处理按钮点击（回调查询） (无需修改) ---
@bot_client.on(events.CallbackQuery())
async def button_click_handler(event):
    chat_id = event.chat_id
    session = user_sessions.get(chat_id)

    if not session:
        await event.answer("这个查询已经过期了，请重新发起。", alert=True)
        return

    if event.message_id != session.get('message_id'):
        await event.answer("这是一个旧的查询结果，请使用最新的那个。", alert=True)
        return

    data = event.data.decode('utf-8')
    action, page_str = data.split('_')
    page = int(page_str)

    if action == 'next':
        session['current_page'] += 1
    elif action == 'prev':
        session['current_page'] -= 1

    try:
        text, buttons = format_page(chat_id)
        await event.edit(text, buttons=buttons, link_preview=False)
    except MessageNotModifiedError:
        pass
    except Exception as e:
        logging.error(f"编辑消息时出错: {e}")
        await event.answer("无法更新页面，可能发生了错误。", alert=True)
    finally:
        await event.answer()


# --- 启动机器人 (无需修改) ---
async def main():
    """主函数，同时启动并运行两个客户端"""
    try:
        await user_client.start()
        user_info = await user_client.get_me()
        logging.info(f"用户客户端 @{user_info.username} 已成功登录，用于数据抓取。")

        await bot_client.start(bot_token=BOT_TOKEN)
        bot_info = await bot_client.get_me()
        logging.info(f"机器人 @{bot_info.username} 已成功启动并正在监听命令...")

        await bot_client.run_until_disconnected()

    except Exception as e:
        logging.critical(f"启动或运行过程中发生致命错误: {e}")
        logging.critical(traceback.format_exc())
    finally:
        if user_client.is_connected():
            await user_client.disconnect()
        if bot_client.is_connected():
            await bot_client.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
