import os
import logging
import socks
import asyncio
import traceback
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.custom import Button
from telethon.errors.rpcerrorlist import UsernameNotOccupiedError, ChatAdminRequiredError, MessageNotModifiedError

# --- 配置 ---
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.INFO)

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

# 固定搜索的消息数量
FIXED_SEARCH_LIMIT = 5000
# 分页设置
PAGE_SIZE = 10

# --- 状态管理 ---
user_sessions = {}

# --- 客户端设置 ---
PROXY_IP = os.getenv('PROXY_IP', '127.0.0.1')
PROXY_PORT = int(os.getenv('PROXY_PORT', 7890))
PROXY_ENABLED = os.getenv('PROXY_ENABLED', 'true').lower() == 'true'
proxy_config = (socks.SOCKS5, PROXY_IP, PROXY_PORT) if PROXY_ENABLED else None

# --- 核心修改：定义两个客户端 ---

# 1. 机器人客户端：用于和用户交互
bot_client = TelegramClient(
    'my_top_bot_session',  # Bot session file
    API_ID,
    API_HASH,
    proxy=proxy_config
)

# 2. 用户客户端：用于抓取频道数据
#    使用不同的 session 文件名以避免冲突
user_client = TelegramClient(
    'user_session_for_bot',  # User session file
    API_ID,
    API_HASH,
    proxy=proxy_config
)


# --- 辅助函数：格式化页面内容和按钮 (无需修改) ---
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
        return text, []

    for i, (reactions, message) in enumerate(page_items):
        rank = start_index + i + 1
        channel_clean = channel.lstrip('@')
        message_link = f"https://t.me/{channel_clean}/{message.id}"
        preview = message.text[:30].replace('\n', ' ') + '...' if message.text else "[媒体消息]"
        text += f"**{rank}.** ❤️ **{reactions}** | [{preview}]({message_link})\n"

    buttons_row = []
    if current_page > 0:
        buttons_row.append(Button.inline("⬅️ 上一页", data=f"prev_{current_page}"))
    if end_index < total_found:
        buttons_row.append(Button.inline("下一页 ➡️", data=f"next_{current_page}"))

    return text, [buttons_row] if buttons_row else []


# --- 事件处理器：处理 /top 命令 ---
# 注意：事件处理器要注册在 bot_client 上
@bot_client.on(events.NewMessage(pattern='/top'))
async def find_top_post_handler(event):
    chat = await event.get_chat()
    logging.info(f"收到来自 {chat.id} 的命令: {event.raw_text}")

    parts = event.raw_text.split()
    if len(parts) < 2 or not parts[1].startswith('@'):
        await event.reply("🤔 **命令格式不正确！**\n请使用: `/top @channel_username`")
        return

    channel_username = parts[1]
    limit = FIXED_SEARCH_LIMIT

    processing_message = await event.reply(f"好的，正在准备从 `{channel_username}` 频道中搜索最近 **{limit}** 条消息...")

    try:
        # --- 核心修改：使用 user_client 来执行需要用户权限的操作 ---
        target_channel = await user_client.get_entity(channel_username)

        total_messages = (await user_client.get_messages(target_channel, limit=1)).total
        messages_to_scan = min(limit, total_messages)

        await processing_message.edit(
            f"频道约有 **{total_messages}** 条消息。\n正在扫描指定的 **{messages_to_scan}** 条，请耐心等待...")

        messages_with_reactions = []
        processed_count = 0
        BATCH_SIZE = 100

        # 使用 user_client 来遍历消息
        async for message in user_client.iter_messages(target_channel, limit=messages_to_scan):
            processed_count += 1
            if message.reactions:
                current_reactions = sum(r.count for r in message.reactions.results)
                if current_reactions > 0:
                    messages_with_reactions.append((current_reactions, message))

            if processed_count % BATCH_SIZE == 0 and processed_count < messages_to_scan:
                progress = (processed_count / messages_to_scan) * 100
                try:
                    # 编辑消息仍然由 bot_client 执行
                    await processing_message.edit(
                        f"扫描进度: **{progress:.1f}%** ({processed_count}/{messages_to_scan})")
                except MessageNotModifiedError:
                    pass
                await asyncio.sleep(0.5)

        # --- 数据处理和发送部分由 bot_client 完成 ---
        await processing_message.delete()

        if not messages_with_reactions:
            await event.respond(f"在扫描的 **{messages_to_scan}** 条消息中，没有找到任何带有 reaction 的消息。")
            return

        sorted_list = sorted(messages_with_reactions, key=lambda item: item[0], reverse=True)

        user_sessions[chat.id] = {
            'sorted_list': sorted_list,
            'current_page': 0,
            'channel': channel_username,
            'limit': messages_to_scan
        }

        text, buttons = format_page(chat.id)
        final_message = await event.respond(text, buttons=buttons, link_preview=False)
        user_sessions[chat.id]['message_id'] = final_message.id

    except UsernameNotOccupiedError:
        await processing_message.edit(f"❌ **错误：** 找不到名为 `{channel_username}` 的频道或用户。请检查拼写。")
    except (ChatAdminRequiredError, ValueError) as e:
        logging.warning(f"访问频道 {channel_username} 失败: {e}")
        await processing_message.edit(
            f"❌ **错误：** 无法访问 `{channel_username}` 的消息历史。可能是私密频道，或者您的用户账户被限制访问。")
    except Exception as e:
        logging.error(f"处理过程中发生未知错误: {e}")
        logging.error(traceback.format_exc())
        await processing_message.edit(f"❌ **在扫描过程中出错了！**\n\n**错误详情:**\n`{e}`")


# --- 事件处理器：处理按钮点击（回调查询） ---
# 同样注册在 bot_client 上
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


# --- 启动机器人 ---
async def main():
    """主函数，同时启动并运行两个客户端"""
    try:
        # 分别启动两个客户端
        # 1. 启动用户客户端（它会在后台连接并准备就绪）
        await user_client.start()
        user_info = await user_client.get_me()
        logging.info(f"用户客户端 @{user_info.username} 已成功登录，用于数据抓取。")

        # 2. 启动机器人客户端并保持运行
        await bot_client.start(bot_token=BOT_TOKEN)
        bot_info = await bot_client.get_me()
        logging.info(f"机器人 @{bot_info.username} 已成功启动并正在监听命令...")

        await bot_client.run_until_disconnected()

    except Exception as e:
        logging.critical(f"启动或运行过程中发生致命错误: {e}")
        logging.critical(traceback.format_exc())
    finally:
        # 确保两个客户端都能断开连接
        if user_client.is_connected():
            await user_client.disconnect()
        if bot_client.is_connected():
            await bot_client.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
