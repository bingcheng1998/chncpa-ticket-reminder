import os
import logging
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import requests
from lxml import html
import schedule
import time
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import subprocess
import random
import configparser
from selenium import webdriver
from selenium.webdriver.common.by import By

# 从配置文件读取检查配置
config = configparser.ConfigParser()
config.read('config.ini')
browser = config['web']['platform'].lower()
SINGLE_PAGR_WAIT_SECONDS = 30

if browser == 'chrome':
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from webdriver_manager.chrome import ChromeDriverManager as WebDriverManager
elif browser == 'firefox':
    from selenium.webdriver.firefox.service import Service
    from selenium.webdriver.firefox.options import Options
    from webdriver_manager.firefox import GeckoDriverManager as WebDriverManager
else:
    raise ValueError("Unsupported platform", browser)

browdriver_binary_path = WebDriverManager().install()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_random_user_agent():
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.101 Safari/537.36',
        'Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Mobile/15E148 Safari/604.1'
    ]
    return random.choice(user_agents)

# 设置 Chrome 选项
browser_options = Options()
browser_options.add_argument("--headless")  # 无头模式，不打开浏览器界面
browser_options.add_argument("--disable-gpu")
browser_options.add_argument("--no-sandbox")
browser_options.add_argument(f"user-agent={get_random_user_agent()}")

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///subscriptions.sqlite'
app.secret_key = 'bingcheng_secret_key'
db = SQLAlchemy(app)

class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(200), nullable=False)
    interval = db.Column(db.Integer, default=1)
    alert_config = db.Column(db.String(200))
    email = db.Column(db.String(100), nullable=False)
    callback = db.Column(db.Text)
    image = db.Column(db.String(200))
    title = db.Column(db.String(200))
    venue = db.Column(db.String(100))
    price_range = db.Column(db.String(100))
    date_range = db.Column(db.String(100))
    status = db.Column(db.String(20), default='active')
    created_at = db.Column(db.DateTime, default=datetime.now())
    last_checked = db.Column(db.DateTime)  # 新增字段

@app.route('/')
def index():
    subscriptions = Subscription.query.all()
    check_configs = {section: config[section] for section in config.sections() if section.startswith('check-')}
    callback_code = config['callback']['code']
    return render_template('index.html', subscriptions=subscriptions, check_configs=check_configs, callback_code=callback_code)

@app.route('/add_subscription', methods=['POST'])
def add_subscription():
    url = request.form['url']
    interval = int(request.form.get('interval', 1))
    alert_config = request.form.getlist('alert_config')
    email = request.form['email']
    callback = request.form.get('callback', '')
    
    # 检查是否已经存在相同的 URL
    existing_subscription = Subscription.query.filter_by(url=url).first()
    if existing_subscription:
        flash('该 URL 已经存在，请先删除。', 'error')
        return redirect(url_for('index'))

    try:
        # 初始化 WebDriver
        service = Service(browdriver_binary_path)
        if browser == 'chrome':
            driver = webdriver.Chrome(service=service, options=browser_options)
        elif browser == 'firefox':
            driver = webdriver.Firefox(service=service, options=browser_options)
        else:
            raise ValueError("Unsupported platform", browser)

        driver.get(url)
        driver.implicitly_wait(SINGLE_PAGR_WAIT_SECONDS)
    
        image_element = driver.find_element(By.XPATH, '//*[@id="productImg"]/img')
        image = image_element.get_attribute('src')
        title = driver.find_element(By.XPATH, '//*[@id="productName"]').text
        venue = driver.find_element(By.XPATH, '//*[@id="venueName"]').text
        price_range = driver.find_element(By.XPATH, '//*[@id="productPrices"]').text
        date_range = driver.find_element(By.XPATH, '//*[@id="productTime"]').text
        if not image or not title:
            raise requests.RequestException("没有获取到演出信息")
    except requests.RequestException as e:
        flash(f'获取页面信息失败，请稍后重试。错误: {str(e)}', 'error')
        return redirect(url_for('index'))

    subscription = Subscription(
        url=url, interval=interval, alert_config=','.join(alert_config),
        email=email, callback=callback, image=image, title=title,
        venue=venue, price_range=price_range, date_range=date_range
    )
    db.session.add(subscription)
    db.session.commit()
    
    flash('订阅已添加。', 'success')
    
    def delayed_check():
        with app.app_context():
            check_subscriptions()

    # 等待 5 秒后触发一次查询
    threading.Timer(SINGLE_PAGR_WAIT_SECONDS, lambda: delayed_check()).start()

    return redirect(url_for('index'))

@app.route('/trigger_check', methods=['POST'])
def trigger_check():
    def delayed_check():
        with app.app_context():
            check_subscriptions()

    threading.Timer(0, delayed_check).start()
    
    return jsonify({'message': 'Check triggered successfully'}), 200

@app.route('/delete_subscription/<int:id>', methods=['POST'])
def delete_subscription(id):
    subscription = Subscription.query.get_or_404(id)
    db.session.delete(subscription)
    db.session.commit()
    return jsonify(success=True)

@app.route('/test_notification/<int:id>', methods=['POST'])
def test_notification(id):
    logger.info(f"============== test_notification ({id}) ==============")
    subscription = Subscription.query.get_or_404(id)
    send_notification(subscription, test=True)
    return jsonify(success=True)

def send_notification(subscription, test=False):
    subject = f"{'[测试] ' if test else '⭐️ '}开票提醒：{subscription.title}"
    body = f"""
    <html>
        <body>
            <p>{'这是一条测试通知。' if test else '您订阅的演出已经开票！'}</p>
            <p>演出信息：</p>
            <ul>
                <li>标题：{subscription.title}</li>
                <li>地点：{subscription.venue}</li>
                <li>价格：{subscription.price_range}</li>
                <li>日期：{subscription.date_range}</li>
            </ul>
            <a href="{subscription.url}" style="display:inline-block; padding:10px 20px; font-size:16px; color:white; background-color:blue; text-align:center; text-decoration:none; border-radius:5px;">立即购票</a>
            <p>
            <img src="{subscription.image}" alt="演出图片" style="width:300px; height:auto;">
        </body>
    </html>
    """

    # 获取SMTP配置
    smtp_server = config['SMTP']['server']
    smtp_port = config['SMTP'].getint('port')
    smtp_username = config['SMTP']['username']
    smtp_password = config['SMTP']['password']

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = config['SMTP']['email']
    msg['To'] = subscription.email
    
    msg.attach(MIMEText(body, 'html'))
    
    logger.info(f"Notification sent for subscription: {subscription.title}")

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.send_message(msg)

    if subscription.callback and len(subscription.callback) > 0:
        callback_code = subscription.callback \
            .replace('{{title}}', subscription.title) \
            .replace('{{venue}}', subscription.venue) \
            .replace('{{price_range}}', subscription.price_range) \
            .replace('{{date_range}}', subscription.date_range) \
            .replace('{{image}}', subscription.image) \
            .replace('{{url}}', subscription.url)
        try:
            subprocess.run(callback_code, shell=True)
            logger.info(f"运行自定义脚本已经触发：{callback_code}")
        except:
            logger.error(f"运行自定义脚本失败：{callback_code}")

def check_subscriptions():
    subscriptions = Subscription.query.filter_by(status='active').all()
    for i, subscription in enumerate(subscriptions):
        time.sleep(SINGLE_PAGR_WAIT_SECONDS * 1.5)
        logger.info(f"============== check_subscriptions ({i + 1}/{len(subscriptions)}) ==============")
        try:
            service = Service(browdriver_binary_path)
            if browser == 'chrome':
                driver = webdriver.Chrome(service=service, options=browser_options)
            elif browser == 'firefox':
                driver = webdriver.Firefox(service=service, options=browser_options)
            else:
                raise ValueError("Unsupported platform", browser)
            driver.get(subscription.url)
            driver.implicitly_wait(SINGLE_PAGR_WAIT_SECONDS)
            page_source = driver.page_source
            driver.quit()
        except Exception as e:
            print(f"Error fetching page: {e}")
            continue

        tree = html.fromstring(page_source)
        
        # 记录日志
        logger.info(f"Checked subscription: {subscription.title}")

        for section in config.sections():
            if not section.startswith('check-'):
                continue
            
            xpath = config[section]['xpath']
            keywords = config[section]['keywords']
            reverse = config[section].getboolean('reverse')
            elements = tree.xpath(xpath)
            # !reverse && elements && keywords in text => send notification
            # reverse && !elements => send notification
            # reverse && elements && keywords not in text => send notification
            # else not send notification
            send_flag = False
            if elements:
                text_content = elements[0].text_content()
                if (reverse and keywords not in text_content) or (not reverse and keywords in text_content):
                    send_flag = True
            if send_flag or (not elements and reverse):
                send_notification(subscription)
                subscription.status = 'notified'
                logger.info(f"Notification sent for subscription: {subscription.title}")
                break
        
        subscription.last_checked = datetime.now()
        db.session.commit()

def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    schedule.every(1).hours.do(check_subscriptions)
    threading.Thread(target=run_schedule, daemon=True).start()
    
    app.run(host='0.0.0.0', port=18882, debug=True)

