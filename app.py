import os
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
import subprocess
import random
import configparser
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

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
chrome_options = Options()
chrome_options.add_argument("--headless")  # 无头模式，不打开浏览器界面
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument(f"user-agent={get_random_user_agent()}")

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

@app.route('/')
def index():
    subscriptions = Subscription.query.all()
    return render_template('index.html', subscriptions=subscriptions)

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
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)

        driver.get(url)
        driver.implicitly_wait(10)
    
        image_element = driver.find_element(By.XPATH, '//*[@id="productImg"]/img')
        image = image_element.get_attribute('src')
        title = driver.find_element(By.XPATH, '//*[@id="productName"]').text
        venue = driver.find_element(By.XPATH, '//*[@id="venueName"]').text
        price_range = driver.find_element(By.XPATH, '//*[@id="productPrices"]').text
        date_range = driver.find_element(By.XPATH, '//*[@id="productTime"]').text
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

    return redirect(url_for('index'))

@app.route('/delete_subscription/<int:id>', methods=['POST'])
def delete_subscription(id):
    subscription = Subscription.query.get_or_404(id)
    db.session.delete(subscription)
    db.session.commit()
    return jsonify(success=True)

@app.route('/test_notification/<int:id>', methods=['POST'])
def test_notification(id):
    subscription = Subscription.query.get_or_404(id)
    send_notification(subscription, test=True)
    return jsonify(success=True)

def send_notification(subscription, test=False):
    subject = f"{'[测试] ' if test else ''}开票提醒：{subscription.title}"
    body = f"""
    {'这是一条测试通知。' if test else '您订阅的演出即将开票！'}
    
    演出信息：
    标题：{subscription.title}
    地点：{subscription.venue}
    价格：{subscription.price_range}
    日期：{subscription.date_range}
    链接：{subscription.url}
    """

    # 读取配置文件
    config = configparser.ConfigParser()
    config.read('config.ini')

    # 获取SMTP配置
    smtp_server = config['SMTP']['server']
    smtp_port = config['SMTP'].getint('port')
    smtp_username = config['SMTP']['username']
    smtp_password = config['SMTP']['password']

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = config['SMTP']['email']
    msg['To'] = subscription.email

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.send_message(msg)

    if subscription.callback and not test:
        subprocess.run(subscription.callback, shell=True)

def check_subscriptions():
    subscriptions = Subscription.query.filter_by(status='active').all()
    for subscription in subscriptions:
        response = requests.get(subscription.url)
        tree = html.fromstring(response.content)
        
        alert_configs = subscription.alert_config.split(',')
        for config in alert_configs:
            xpath, text = config.split(':', 1)
            elements = tree.xpath(xpath)
            if elements and text in elements[0].text_content():
                send_notification(subscription)
                subscription.status = 'notified'
                db.session.commit()
                break

def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    schedule.every(1).hours.do(check_subscriptions)
    threading.Thread(target=run_schedule, daemon=True).start()
    
    app.run(debug=True)

