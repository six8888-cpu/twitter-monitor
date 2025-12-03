# Twitter 监控系统

实时监控 Twitter 用户的推文，支持 Telegram 推送通知。

## 功能

- 📌 监控置顶推文变化
- 🐦 监控最新原创推文
- ↩️ 监控最新回复
- 🔁 监控最新转发
- 📬 Telegram 实时推送
- 🌐 Web 管理界面

## 安装

```bash
pip install flask requests
```

## 使用

1. 启动服务：
```bash
python app.py
```

2. 访问 `http://localhost:5000`

3. 配置：
   - Twitter API Key（从 [twitterapi.io](https://twitterapi.io) 获取）
   - Telegram Bot Token（从 @BotFather 获取）
   - Telegram Chat ID（从 @userinfobot 获取）

4. 添加要监控的用户，点击"启动监控"

## 配置说明

| 配置项 | 说明 |
|--------|------|
| twitter_api_key | TwitterAPI.io 的 API Key |
| telegram_bot_token | Telegram 机器人 Token |
| telegram_chat_id | 接收通知的 Chat ID |
| check_interval | 检查间隔（秒），默认 60 |

## 费用估算

使用 TwitterAPI.io：
- $0.15 / 1000 条推文
- 最低收费 $0.00015 / 请求
- 监控 2 用户，每分钟检查：约 $13/月

## License

MIT
