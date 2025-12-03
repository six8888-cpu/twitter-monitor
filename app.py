#!/usr/bin/env python3
"""
Twitter 监控系统 - Web版
支持多用户监控，Telegram 推送
增强稳定性，支持长时间运行
"""

from flask import Flask, render_template, request, jsonify
import requests
import json
import threading
import time
from datetime import datetime
import os
import logging
import traceback

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('monitor.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 配置文件路径
CONFIG_FILE = "config.json"
STATE_FILE = "state.json"  # 保存推文状态，程序重启后不会重复发送

# 默认配置
DEFAULT_CONFIG = {
    "twitter_api_key": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "monitor_users": [],
    "check_interval": 60,
    "is_running": False
}

# 全局变量
config = {}
monitor_thread = None
last_tweets = {}  # 记录每个用户的最后推文ID
state_lock = threading.Lock()  # 线程锁，保证状态文件写入安全

def load_config():
    global config
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
        else:
            config = DEFAULT_CONFIG.copy()
            save_config()
    except Exception as e:
        logger.error(f"加载配置失败: {e}")
        config = DEFAULT_CONFIG.copy()

def save_config():
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"保存配置失败: {e}")

def load_state():
    """加载推文状态（程序重启后恢复）"""
    global last_tweets
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                last_tweets = json.load(f)
            logger.info(f"已恢复推文状态: {len(last_tweets)} 条记录")
    except Exception as e:
        logger.error(f"加载状态失败: {e}")
        last_tweets = {}

def save_state():
    """保存推文状态"""
    try:
        with state_lock:
            with open(STATE_FILE, "w") as f:
                json.dump(last_tweets, f, indent=2)
    except Exception as e:
        logger.error(f"保存状态失败: {e}")

def get_user_info(username, retry=3):
    """获取用户资料，带重试机制"""
    url = f"https://api.twitterapi.io/twitter/user/info?userName={username}"
    headers = {"X-API-Key": config["twitter_api_key"], "Accept": "application/json"}
    
    for i in range(retry):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            return response.json()
        except requests.exceptions.Timeout:
            logger.warning(f"获取用户信息超时 (尝试 {i+1}/{retry})")
            time.sleep(2)
        except requests.exceptions.RequestException as e:
            logger.warning(f"获取用户信息失败: {e} (尝试 {i+1}/{retry})")
            time.sleep(2)
        except Exception as e:
            logger.error(f"获取用户信息异常: {e}")
            return {"status": "error", "msg": str(e)}
    
    return {"status": "error", "msg": "请求超时"}

def get_user_tweets(username, retry=3):
    """获取用户最新推文，带重试机制"""
    url = f"https://api.twitterapi.io/twitter/user/last_tweets?userName={username}&includeReplies=true"
    headers = {"X-API-Key": config["twitter_api_key"], "Accept": "application/json"}
    
    for i in range(retry):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            return response.json()
        except requests.exceptions.Timeout:
            logger.warning(f"获取推文超时 (尝试 {i+1}/{retry})")
            time.sleep(2)
        except requests.exceptions.RequestException as e:
            logger.warning(f"获取推文失败: {e} (尝试 {i+1}/{retry})")
            time.sleep(2)
        except Exception as e:
            logger.error(f"获取推文异常: {e}")
            return {"status": "error", "msg": str(e)}
    
    return {"status": "error", "msg": "请求超时"}

def classify_tweets(tweets):
    """分类推文"""
    original = None
    reply = None
    retweet = None
    
    for tweet in tweets:
        if tweet.get("retweeted_tweet"):
            if not retweet:
                retweet = tweet
        elif tweet.get("isReply"):
            if not reply:
                reply = tweet
        else:
            if not original:
                original = tweet
        if original and reply and retweet:
            break
    
    return {"original": original, "reply": reply, "retweet": retweet}

def send_telegram(message, retry=3):
    """发送 Telegram 消息，带重试机制"""
    if not config.get("telegram_bot_token") or not config.get("telegram_chat_id"):
        return False
    
    url = f"https://api.telegram.org/bot{config['telegram_bot_token']}/sendMessage"
    data = {
        "chat_id": config["telegram_chat_id"],
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    for i in range(retry):
        try:
            response = requests.post(url, json=data, timeout=30)
            result = response.json()
            if result.get("ok"):
                return True
            else:
                logger.warning(f"Telegram 发送失败: {result.get('description')}")
                return False
        except requests.exceptions.Timeout:
            logger.warning(f"Telegram 发送超时 (尝试 {i+1}/{retry})")
            time.sleep(2)
        except Exception as e:
            logger.warning(f"Telegram 发送异常: {e} (尝试 {i+1}/{retry})")
            time.sleep(2)
    
    return False

def check_new_tweets(username):
    """检查新推文并发送通知"""
    global last_tweets
    
    logger.info(f"检查用户 @{username} 的推文...")
    
    # 获取用户信息（包含置顶推文ID）
    user_resp = get_user_info(username)
    user_name = username
    current_pinned = None
    
    if user_resp.get("status") == "success":
        user_data = user_resp.get("data", {})
        user_name = user_data.get("name", username)
        
        # 获取置顶推文ID
        pinned_ids = user_data.get("pinnedTweetIds", [])
        current_pinned = pinned_ids[0] if pinned_ids else None
        pinned_key = f"{username}_pinned"
        
        if pinned_key not in last_tweets:
            last_tweets[pinned_key] = current_pinned
            if current_pinned:
                logger.info(f"初始化 @{username} 的置顶推文ID: {current_pinned}")
            save_state()
        elif last_tweets[pinned_key] != current_pinned:
            old_pinned = last_tweets[pinned_key]
            last_tweets[pinned_key] = current_pinned
            save_state()
            
            if current_pinned:
                logger.info(f"📌 发现 @{username} 更换置顶推文: {old_pinned} -> {current_pinned}")
                message = f"""📌 <b>更换置顶推文</b>

<b>用户:</b> {user_name} (@{username})
<b>新置顶:</b> https://x.com/{username}/status/{current_pinned}"""
                result = send_telegram(message)
                logger.info(f"Telegram 发送{'成功' if result else '失败'}")
            else:
                logger.info(f"📌 @{username} 取消了置顶推文")
                message = f"""📌 <b>取消置顶推文</b>

<b>用户:</b> {user_name} (@{username})"""
                result = send_telegram(message)
                logger.info(f"Telegram 发送{'成功' if result else '失败'}")
    else:
        logger.warning(f"获取 @{username} 用户信息失败")
    
    # 获取推文列表
    tweets_resp = get_user_tweets(username)
    if tweets_resp.get("status") != "success":
        logger.warning(f"获取 @{username} 推文失败: {tweets_resp.get('msg', 'unknown error')}")
        return
    
    tweets = tweets_resp.get("data", {}).get("tweets", [])
    if not tweets:
        logger.info(f"@{username} 暂无推文")
        return
    
    logger.info(f"@{username} 获取到 {len(tweets)} 条推文")
    
    # 分类推文
    classified = classify_tweets(tweets)
    
    # 检查每种类型的新推文
    state_changed = False
    for tweet_type, tweet in classified.items():
        if not tweet:
            continue
        
        tweet_id = tweet.get("id")
        key = f"{username}_{tweet_type}"
        
        if key not in last_tweets:
            last_tweets[key] = tweet_id
            logger.info(f"初始化 @{username} 的 {tweet_type} 推文ID: {tweet_id}")
            state_changed = True
            continue
        
        if last_tweets[key] != tweet_id:
            # 跳过置顶推文（已单独处理）
            if tweet_id == current_pinned:
                logger.info(f"跳过置顶推文: {tweet_id}")
                last_tweets[key] = tweet_id
                state_changed = True
                continue
            
            # 有新推文！
            logger.info(f"🆕 发现 @{username} 新{tweet_type}推文: {tweet_id}")
            last_tweets[key] = tweet_id
            state_changed = True
            
            type_names = {"original": "原创", "reply": "回复", "retweet": "转发"}
            type_name = type_names.get(tweet_type, tweet_type)
            
            text = tweet.get("text", "")[:200]
            url = tweet.get("url", "")
            
            message = f"""🐦 <b>新{type_name}推文</b>

<b>用户:</b> {user_name} (@{username})
<b>内容:</b> {text}
<b>链接:</b> {url}
<b>时间:</b> {tweet.get('createdAt', '')}"""
            
            result = send_telegram(message)
            logger.info(f"Telegram 发送{'成功' if result else '失败'}")
    
    # 批量保存状态
    if state_changed:
        save_state()

def monitor_loop():
    """监控循环"""
    logger.info("=== 监控循环启动 ===")
    consecutive_errors = 0
    max_consecutive_errors = 10
    
    while config.get("is_running"):
        try:
            logger.info(f"--- 开始新一轮检查 (间隔: {config.get('check_interval', 60)}秒) ---")
            
            for user in config.get("monitor_users", []):
                if not config.get("is_running"):
                    break
                try:
                    check_new_tweets(user)
                    consecutive_errors = 0  # 成功后重置错误计数
                except Exception as e:
                    logger.error(f"监控 {user} 出错: {e}\n{traceback.format_exc()}")
                    consecutive_errors += 1
                time.sleep(1)
            
            # 连续错误过多，等待更长时间
            if consecutive_errors >= max_consecutive_errors:
                logger.warning(f"连续错误 {consecutive_errors} 次，等待 5 分钟后重试...")
                for _ in range(300):
                    if not config.get("is_running"):
                        break
                    time.sleep(1)
                consecutive_errors = 0
                continue
            
            # 等待下次检查
            interval = config.get("check_interval", 60)
            logger.info(f"等待 {interval} 秒后进行下一轮检查...")
            for _ in range(interval):
                if not config.get("is_running"):
                    break
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"监控循环异常: {e}\n{traceback.format_exc()}")
            time.sleep(10)
    
    logger.info("=== 监控循环停止 ===")

def start_monitor():
    """启动监控"""
    global monitor_thread
    
    # 检查线程是否已在运行
    if monitor_thread and monitor_thread.is_alive():
        logger.info("监控线程已在运行")
        return
    
    config["is_running"] = True
    save_config()
    
    logger.info("启动监控线程...")
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()

def stop_monitor():
    """停止监控"""
    config["is_running"] = False
    save_config()

# ============ 路由 ============

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify({
        "twitter_api_key": config.get("twitter_api_key", ""),
        "telegram_bot_token": config.get("telegram_bot_token", ""),
        "telegram_chat_id": config.get("telegram_chat_id", ""),
        "monitor_users": config.get("monitor_users", []),
        "check_interval": config.get("check_interval", 60),
        "is_running": config.get("is_running", False)
    })

@app.route("/api/config", methods=["POST"])
def update_config():
    data = request.json
    if "twitter_api_key" in data:
        config["twitter_api_key"] = data["twitter_api_key"]
    if "telegram_bot_token" in data:
        config["telegram_bot_token"] = data["telegram_bot_token"]
    if "telegram_chat_id" in data:
        config["telegram_chat_id"] = data["telegram_chat_id"]
    if "check_interval" in data:
        config["check_interval"] = int(data["check_interval"])
    save_config()
    return jsonify({"status": "success"})

@app.route("/api/users", methods=["GET"])
def get_users():
    return jsonify({"users": config.get("monitor_users", [])})

@app.route("/api/users", methods=["POST"])
def add_user():
    data = request.json
    username = data.get("username", "").strip().replace("@", "")
    if not username:
        return jsonify({"status": "error", "msg": "用户名不能为空"})
    
    if username in config.get("monitor_users", []):
        return jsonify({"status": "error", "msg": "用户已存在"})
    
    # 验证用户是否存在
    user_resp = get_user_info(username)
    if user_resp.get("status") != "success":
        return jsonify({"status": "error", "msg": f"用户不存在或API错误: {user_resp.get('msg', '')}"})
    
    config.setdefault("monitor_users", []).append(username)
    save_config()
    
    user_data = user_resp.get("data", {})
    return jsonify({
        "status": "success",
        "user": {
            "username": username,
            "name": user_data.get("name", ""),
            "followers": user_data.get("followers", 0),
            "avatar": user_data.get("profilePicture", "")
        }
    })

@app.route("/api/users/<username>", methods=["DELETE"])
def delete_user(username):
    if username in config.get("monitor_users", []):
        config["monitor_users"].remove(username)
        save_config()
        # 清除该用户的推文记录
        keys_to_remove = [k for k in last_tweets if k.startswith(f"{username}_")]
        for k in keys_to_remove:
            del last_tweets[k]
        save_state()
    return jsonify({"status": "success"})

@app.route("/api/user/<username>/tweets", methods=["GET"])
def get_tweets(username):
    """获取用户最新推文"""
    tweets_resp = get_user_tweets(username)
    if tweets_resp.get("status") != "success":
        return jsonify({"status": "error", "msg": tweets_resp.get("msg", "获取失败")})
    
    tweets = tweets_resp.get("data", {}).get("tweets", [])
    classified = classify_tweets(tweets)
    
    # 获取置顶推文
    user_resp = get_user_info(username)
    pinned_id = None
    if user_resp.get("status") == "success":
        pinned_ids = user_resp.get("data", {}).get("pinnedTweetIds", [])
        if pinned_ids:
            pinned_id = pinned_ids[0]
    
    return jsonify({
        "status": "success",
        "data": {
            "original": classified["original"],
            "reply": classified["reply"],
            "retweet": classified["retweet"],
            "pinned_id": pinned_id
        }
    })

@app.route("/api/monitor/start", methods=["POST"])
def api_start_monitor():
    start_monitor()
    return jsonify({"status": "success", "is_running": True})

@app.route("/api/monitor/stop", methods=["POST"])
def api_stop_monitor():
    stop_monitor()
    return jsonify({"status": "success", "is_running": False})

@app.route("/api/telegram/test", methods=["POST"])
def test_telegram():
    """测试 Telegram 发送"""
    result = send_telegram("🔔 测试消息\n\nTwitter 监控系统配置成功！")
    if result:
        return jsonify({"status": "success", "msg": "发送成功"})
    else:
        return jsonify({"status": "error", "msg": "发送失败，请检查配置"})

@app.route("/api/status", methods=["GET"])
def get_status():
    """获取系统状态"""
    return jsonify({
        "is_running": config.get("is_running", False),
        "monitor_thread_alive": monitor_thread.is_alive() if monitor_thread else False,
        "tracked_states": len(last_tweets),
        "monitor_users": len(config.get("monitor_users", []))
    })

if __name__ == "__main__":
    load_config()
    load_state()  # 加载之前保存的推文状态
    
    # 如果之前是运行状态，自动启动
    if config.get("is_running"):
        start_monitor()
    
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
