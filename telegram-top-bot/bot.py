import os
import logging
import socks
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import Channel
from telethon.errors.rpcerrorlist import UsernameNotOccupiedError, ChatAdminRequiredError

# --- 配置 ---
logging.basicConfig(format='[%(levelname) 5s/%(asctime)s] %(name)s: %(message)s', level=logging.INFO)

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
BOT_USERNAME = os.getenv('BOT_USERNAME')

# 默认搜索消息数量
SEARCH_LIMIT_DEFAULT = 100
# 最大搜索数量限制
SEARCH_LIMIT_MAX = 500

# --- 机器人逻辑 ---
# --- 配置代理 ---
# 换成你自己的代理服务器地址和端口
proxy_ip = '127.0.0.1'
proxy_port = 7890 # 端口通常是整数，不需要引号

# 创建 TelegramClient 实例时，传入 proxy 参数
client = TelegramClient(
    'bot_session',
    API_ID,
    API_HASH,
    proxy=(socks.SOCKS5, proxy_ip, proxy_port)
)



@client.on(events.NewMessage(pattern='/top'))
async def find_top_post(event):
    """
    当用户发送 /top 命令时触发此函数
    命令格式: /top <@channel_username> [limit]
    例如: /top @durov
    或者: /top @durov 200
    """
    if event.is_channel:
        return

    chat = await event.get_chat()
    logging.info(f"收到来自 {chat.first_name} 的命令: {event.raw_text}")

    # --- 1. 解析和验证用户输入 ---
    parts = event.raw_text.split()

    if len(parts) < 2 or not parts[1].startswith('@'):
        await event.reply(
            "🤔 **命令格式不正确哦！**\n\n"
            "请使用以下格式：\n"
            "`/top @channel_username [数量]`\n\n"
            "**例如:**\n"
            "`/top @durov` (搜索最近100条)\n"
            "`/top @durov 200` (搜索最近200条)"
        )
        return

    channel_username = parts[1]
    limit = SEARCH_LIMIT_DEFAULT

    if len(parts) > 2 and parts[2].isdigit():
        limit = int(parts[2])
        if limit > SEARCH_LIMIT_MAX:
            limit = SEARCH_LIMIT_MAX
            await event.reply(f"😅 为了防止滥用，最大搜索数量已限制为 **{SEARCH_LIMIT_MAX}** 条。")

    # --- 2. 开始处理，并发送提示消息 ---
    try:
        processing_message = await event.reply(
            f"好的，正在从 `{channel_username}` 频道中搜索最近 **{limit}** 条消息，请稍候...")
    except Exception as e:
        logging.error(f"回复消息失败: {e}")
        return

    top_message = None
    max_reactions = -1

    # --- 3. 核心逻辑：获取频道信息并搜索 ---
    try:
        # 检查频道是否存在
        target_channel = await client.get_entity(channel_username)

        # 异步迭代频道历史消息
        async for message in client.iter_messages(target_channel, limit=limit):
            if message.reactions:
                current_reactions = sum(r.count for r in message.reactions.results)
                if current_reactions > max_reactions:
                    max_reactions = current_reactions
                    top_message = message

        # --- 4. 整理结果并回复 ---
        if top_message:
            message_link = f"https://t.me/{target_channel.username}/{top_message.id}"
            reply_text = (
                f"🎉 **查找完成！**\n\n"
                f"在频道 `{channel_username}` 的最近 **{limit}** 条消息中，最高 reaction 的消息是：\n\n"
                f"❤️ **总 Reaction 数**: {max_reactions}\n"
                f"🔗 **消息链接**: [点击这里跳转]({message_link})"
            )
            if top_message.text:
                reply_text += f"\n\n📝 **内容预览**:\n`{top_message.text[:150]}...`"

            await processing_message.edit(reply_text, link_preview=False)
        else:
            await processing_message.edit(
                f"在 `{channel_username}` 的最近 **{limit}** 条消息中，没有找到任何带有 reaction 的消息。")

    # --- 5. 强大的错误处理 ---
    except UsernameNotOccupiedError:
        await processing_message.edit(f"❌ **错误**: 找不到名为 `{channel_username}` 的频道或用户。请检查拼写是否正确。")
    except ChatAdminRequiredError:
        await processing_message.edit(f"❌ **错误**: 我需要是 `{channel_username}` 频道的管理员才能读取消息历史记录。")
    except (ValueError, TypeError):
        await processing_message.edit(
            f"❌ **错误**: `{channel_username}` 似乎不是一个公开频道或我无法访问它。请确保它是一个公开频道。")
    except Exception as e:
        logging.error(f"处理过程中发生未知错误: {e}")
        await processing_message.edit(f"出错了！\n错误信息: `{e}`")


# --- 启动机器人 ---
async def main():
    # 以用户身份登录 (首次运行会需要交互式输入)
    await client.start()
    logging.info("用户客户端已成功启动！")

    # 验证机器人是否正常工作
    me = await client.get_me()
    bot_info = await client.get_entity(BOT_USERNAME)
    logging.info(f"以 @{me.username} 的身份登录")
    logging.info(f"机器人 @{bot_info.username} 正在监听命令...")

    # 保持运行
    await client.run_until_disconnected()


if __name__ == '__main__':
    client.loop.run_until_complete(main())
