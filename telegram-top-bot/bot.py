import os
import logging
import socks
import asyncio
import traceback
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import Channel, ReactionEmoji
from telethon.tl.custom import Button
from telethon.errors.rpcerrorlist import UsernameNotOccupiedError, ChatAdminRequiredError, MessageNotModifiedError
from telethon.sessions import StringSession

# --- 配置 ---
logging.basicConfig(
    format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
PORXY_PORT = os.getenv('PORXY_PORT')

PHONE_NUMBER = os.getenv('PHONE_NUMBER')
USER_SESSION_STRING = os.getenv('USER_SESSION_STRING', '')

TOP_N = 50
FIXED_SEARCH_LIMIT = 500000
PAGE_SIZE = 10

# 并发配置
CONCURRENT_BATCH_SIZE = 100  # 每批消息数
MAX_CONCURRENT_PER_BATCH = 20  # 每批内部的最大并发数
PROGRESS_UPDATE_BATCH = 10  # 每处理多少批更新一次进度

# --- 状态管理 ---
user_sessions = {}
cancel_tasks = {}  # 用于存储取消标志

# --- 客户端设置 ---
PROXY_IP = os.getenv('PROXY_IP', '127.0.0.1')
PROXY_PORT = int(os.getenv('PROXY_PORT', PORXY_PORT))
PROXY_ENABLED = os.getenv('PROXY_ENABLED', 'true').lower() == 'true'
proxy_config = (socks.SOCKS5, PROXY_IP, PROXY_PORT) if PROXY_ENABLED else None

bot_client = TelegramClient('my_top_bot_session', API_ID, API_HASH, proxy=proxy_config)

if USER_SESSION_STRING:
    user_client = TelegramClient(StringSession(USER_SESSION_STRING), API_ID, API_HASH, proxy=proxy_config)
else:
    user_client = TelegramClient('user_session_for_bot', API_ID, API_HASH, proxy=proxy_config)


# --- 辅助函数：提取❤️ reaction 数量 ---
def get_heart_reaction_count(message):
    """只统计❤️表情的数量"""
    if not message.reactions:
        return 0

    heart_count = 0
    for reaction in message.reactions.results:
        # 检查是否为❤️表情
        if isinstance(reaction.reaction, ReactionEmoji) and reaction.reaction.emoticon == '❤':
            heart_count += reaction.count

    return heart_count


# --- 辅助函数：处理单条消息 ---
async def process_single_message(message):
    """处理单条消息，提取❤️ reaction"""
    heart_count = get_heart_reaction_count(message)
    if heart_count > 0:
        return {
            'count': heart_count,
            'id': message.id,
            'preview': message.text[:30].replace('\n', ' ') + '...' if message.text else "[媒体消息]"
        }
    return None


# --- 辅助函数：并发处理一批消息 ---
async def process_messages_batch_concurrent(messages_batch):
    """并发处理一批消息，使用信号量控制并发数"""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_PER_BATCH)

    async def process_with_limit(msg):
        async with semaphore:
            return await process_single_message(msg)

    # 创建所有任务
    tasks = [process_with_limit(msg) for msg in messages_batch]

    # 并发执行
    results = await asyncio.gather(*tasks)

    # 过滤掉 None 结果
    return [r for r in results if r is not None]


# --- 辅助函数：格式化页面内容和按钮 ---
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
        f"**频道 `{display_name}` ❤️ Reaction Top 榜单**\n"
        f"*(在扫描的 {session['limit']} 条消息中，排名前 {total_found} 条带❤️的消息)*\n\n"
        f"--- **第 {current_page + 1} / {total_pages} 页** ---\n\n"
    )

    if not page_items:
        text += "这一页没有内容了。"
        return text, []

    for i, item in enumerate(page_items):
        rank = start_index + i + 1
        message_link = f"https://t.me/{link_prefix}/{item['id']}"
        preview = item['preview']
        text += f"**{rank}.** ❤️ **{item['count']}** | [{preview}]({message_link})\n"

    buttons_row = []
    if current_page > 0:
        buttons_row.append(Button.inline("⬅️ 上一页", data=f"prev_{current_page}"))
    if end_index < total_found:
        buttons_row.append(Button.inline("下一页 ➡️", data=f"next_{current_page}"))

    return text, [buttons_row] if buttons_row else []


# --- 核心逻辑：处理频道请求 ---
async def process_channel_request(event, user_input):
    """封装了查找、扫描和返回频道top榜单的核心逻辑。"""
    limit = FIXED_SEARCH_LIMIT
    chat_id = event.chat_id

    # 设置取消标志
    cancel_tasks[chat_id] = False

    try:
        entity_to_find = int(user_input)
    except ValueError:
        entity_to_find = user_input.lstrip('@')

    processing_message = await event.reply(
        f"好的，正在查找 `{user_input}` 并准备搜索最近 **{limit}** 条消息...\n"
        f"只统计 ❤️ reaction"
    )

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
            f"频道 **{display_name}** 约有 **{total_messages}** 条消息。\n"
            f"正在扫描指定的 **{messages_to_scan}** 条，请耐心等待...\n"
            f"(只统计 ❤️ reaction，使用并发加速)"
        )

        # 并发处理
        all_results = []
        processed_count = 0
        current_batch = []
        batch_count = 0

        async for message in user_client.iter_messages(target_channel, limit=messages_to_scan):
            # 检查是否被取消
            if cancel_tasks.get(chat_id, False):
                await processing_message.edit("❌ 扫描已取消。")
                return

            current_batch.append(message)
            processed_count += 1

            # 当批次达到指定大小时，并发处理
            if len(current_batch) >= CONCURRENT_BATCH_SIZE:
                # 🔥 并发处理这一批消息
                batch_results = await process_messages_batch_concurrent(current_batch)
                all_results.extend(batch_results)
                current_batch = []
                batch_count += 1

                # 每处理若干批更新一次进度
                if batch_count % PROGRESS_UPDATE_BATCH == 0:
                    progress = (processed_count / messages_to_scan) * 100
                    try:
                        await processing_message.edit(
                            f"扫描进度: **{progress:.1f}%** ({processed_count}/{messages_to_scan})\n"
                            f"已发现 **{len(all_results)}** 条带❤️的消息"
                        )
                    except MessageNotModifiedError:
                        pass
                    # 短暂休眠，避免过于频繁的请求
                    await asyncio.sleep(0.3)

        # 处理剩余的消息
        if current_batch:
            batch_results = await process_messages_batch_concurrent(current_batch)
            all_results.extend(batch_results)

        await processing_message.delete()

        if not all_results:
            await event.respond(f"在扫描的 **{messages_to_scan}** 条消息中，没有找到任何带有❤️ reaction 的消息。")
            return

        # 按❤️数量排序
        sorted_list = sorted(all_results, key=lambda item: item['count'], reverse=True)[:TOP_N]

        user_sessions[chat_id] = {
            'sorted_list': sorted_list,
            'current_page': 0,
            'display_name': display_name,
            'link_prefix': link_prefix,
            'limit': messages_to_scan
        }

        text, buttons = format_page(chat_id)
        final_message = await event.respond(text, buttons=buttons, link_preview=False)
        user_sessions[chat_id]['message_id'] = final_message.id

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
    finally:
        # 清理取消标志
        cancel_tasks.pop(chat_id, None)


# --- 事件处理器：处理所有私聊消息 ---
@bot_client.on(events.NewMessage(func=lambda e: e.is_private))
async def message_handler(event):
    """处理所有私聊消息，并根据内容分发任务。"""
    chat = await event.get_chat()
    raw_text = event.raw_text.strip()
    logging.info(f"收到来自 {chat.id} 的消息: {raw_text}")

    if raw_text.lower() == '/start':
        await event.reply(
            "**欢迎使用 ❤️ Reaction Top 榜单机器人！**\n\n"
            "请直接发送给我一个公开频道的用户名（如 `@telegram` 或 `telegram`）或 ID（如 `-100123456789`），"
            "我将为你查找该频道 **❤️ reaction** 最高的帖子。\n\n"
            "您也可以使用 `/top @channel_name` 的格式。"
        )
        return

    user_input = ""
    if raw_text.lower().startswith('/top '):
        parts = raw_text.split(maxsplit=1)
        if len(parts) > 1:
            user_input = parts[1]
    elif not raw_text.startswith('/'):
        user_input = raw_text

    if user_input:
        await process_channel_request(event, user_input)


# --- 事件处理器：处理按钮点击 ---
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
        if not USER_SESSION_STRING:
            if not PHONE_NUMBER:
                logging.error("错误：未在 .env 中设置 PHONE_NUMBER，无法自动登录用户账户。")
                logging.error("请在 .env 中添加: PHONE_NUMBER=+8613800138000")
                return

            logging.info(f"首次登录，使用手机号: {PHONE_NUMBER}")
            await user_client.start(phone=PHONE_NUMBER)

            session_string = user_client.session.save()
            logging.info("=" * 60)
            logging.info("用户账户登录成功！请将以下 SESSION STRING 保存到 .env 文件中：")
            logging.info(f"USER_SESSION_STRING={session_string}")
            logging.info("=" * 60)
            logging.info("下次启动时将自动使用此 session，无需再次验证。")
        else:
            logging.info("使用已保存的 session string 登录...")
            await user_client.start()

        user_info = await user_client.get_me()
        logging.info(f"用户客户端 @{user_info.username or user_info.phone} 已成功登录，用于数据抓取。")

        await bot_client.start(bot_token=BOT_TOKEN)
        bot_info = await bot_client.get_me()
        logging.info(f"机器人 @{bot_info.username} 已成功启动并正在监听命令...")
        logging.info("Bot 正在后台运行，可以安全地关闭终端（如使用 screen/tmux/nohup）")

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