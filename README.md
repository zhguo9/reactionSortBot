# reactionSortBot
a bot sorting messages of channel by reaction's number

# how to use
## install python environment
```
conda env create -f environment.yml -n yourPythonEnvName
```
## add env file
add a .env file in the "telegram-top-bot" dir, the content should like this
```
API_ID=15000048  # 换成你的 API ID
API_HASH=1d022XXXXXXXXXXXXXXXX1601a4 # 换成你的 API Hash
BOT_TOKEN=83XXXXXX416:AAHXXXXXXQuT-C09PXXXXXXXXXCps # 换成你的 Bot Token
BOT_USERNAME=@sortByReaction_bot # 换成你的bot的username
PORXY_PORT=7890 # your porxy port #换成你的代理端口，ip在bot.py中定义
```
## create python environment
> please note the path !
```
conda env create -f environment.yml -n test_env
```
## run command
> please note the path !
```
python bot.py
```
input your phone number, and input the code you recived, then the bot should wrok !
