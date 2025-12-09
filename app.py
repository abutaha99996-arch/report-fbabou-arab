#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
أداة الإبلاغ الآمنة على فيسبوك - نسخة محترفة
تم تصميمها لتجنب الحظر ودعم الإبلاغ المتعدد
"""

import os
import json
import time
import random
import logging
import sqlite3
from datetime import datetime, timedelta
from threading import Thread, Lock
from queue import Queue

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify, session
from flask_session import Session
from user_agents import parse
import fake_useragent
from stem import Signal
from stem.control import Controller
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.proxy import Proxy, ProxyType

# =============== إعدادات النظام ===============
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
Session(app)

# إعدادات السجل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/reporter.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============== فئات النظام ===============

class ProxyManager:
    """مدير البروكسيات المتقدم"""
    
    def __init__(self, proxy_file='proxies/proxies.txt'):
        self.proxies = []
        self.current_index = 0
        self.lock = Lock()
        self.load_proxies(proxy_file)
        
        # قنوات TOR (اختياري)
        self.tor_enabled = False
        self.tor_port = 9050
        self.tor_control_port = 9051
        self.tor_password = "MyTorPassword"
    
    def load_proxies(self, proxy_file):
        """تحميل قائمة البروكسيات"""
        try:
            with open(proxy_file, 'r') as f:
                self.proxies = [line.strip() for line in f if line.strip()]
            logger.info(f"تم تحميل {len(self.proxies)} بروكسي")
        except FileNotFoundError:
            logger.warning("لم يتم العثور على ملف البروكسيات، سيتم استخدام قائمة افتراضية")
            self.proxies = self.get_default_proxies()
    
    def get_default_proxies(self):
        """الحصول على بروكسيات عامة (مجانية - تستخدم للاختبار فقط)"""
        return [
            "http://proxy1.example.com:8080",
            "http://proxy2.example.com:8080",
            "http://proxy3.example.com:8080",
        ]
    
    def get_random_proxy(self):
        """الحصول على بروكسي عشوائي"""
        with self.lock:
            if not self.proxies:
                return None
            proxy = random.choice(self.proxies)
            return {'http': proxy, 'https': proxy}
    
    def get_rotated_proxy(self):
        """الحصول على بروكسي مع التدوير"""
        with self.lock:
            if not self.proxies:
                return None
            proxy = self.proxies[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.proxies)
            return {'http': proxy, 'https': proxy}
    
    def renew_tor_ip(self):
        """تجديد عنوان IP لـ TOR"""
        if not self.tor_enabled:
            return False
        
        try:
            with Controller.from_port(port=self.tor_control_port) as controller:
                controller.authenticate(password=self.tor_password)
                controller.signal(Signal.NEWNYM)
                logger.info("تم تجديد عنوان TOR IP")
                return True
        except Exception as e:
            logger.error(f"فشل في تجديد TOR IP: {e}")
            return False
    
    def get_tor_proxy(self):
        """الحصول على إعدادات TOR بروكسي"""
        return {
            'http': f'socks5://127.0.0.1:{self.tor_port}',
            'https': f'socks5://127.0.0.1:{self.tor_port}'
        }

class FacebookReporter:
    """محور الإبلاغ على فيسبوك"""
    
    def __init__(self):
        self.proxy_manager = ProxyManager()
        self.user_agent = fake_useragent.UserAgent()
        self.report_queue = Queue()
        self.active_reports = 0
        self.max_concurrent = 3  # أقصى عدد للإبلاغات المتزامنة
        self.report_delay = random.uniform(10, 30)  # تأخير بين البلاغات
        
        # إعدادات فيسبوك
        self.facebook_urls = {
            'report_form': 'https://www.facebook.com/help/contact/209046679279097',
            'login': 'https://www.facebook.com/login',
            'safety_center': 'https://www.facebook.com/safety',
            'terrorism_report': 'https://www.facebook.com/help/contact/201914306524994'
        }
        
        # بدء العاملين
        self.start_workers()
    
    def start_workers(self):
        """بدء العاملين لمعالجة البلاغات"""
        for i in range(self.max_concurrent):
            worker = Thread(target=self.report_worker, args=(i,))
            worker.daemon = True
            worker.start()
    
    def report_worker(self, worker_id):
        """عامل معالجة البلاغات"""
        while True:
            try:
                report_data = self.report_queue.get()
                if report_data is None:
                    break
                
                self.process_report(report_data, worker_id)
                time.sleep(self.report_delay)
                self.report_queue.task_done()
                
            except Exception as e:
                logger.error(f"خطأ في العامل {worker_id}: {e}")
    
    def process_report(self, report_data, worker_id):
        """معالجة بلاغ واحد"""
        try:
            logger.info(f"العامل {worker_id}: معالجة بلاغ لـ {report_data['profile_url']}")
            
            # اختيار طريقة الإبلاغ
            method = report_data.get('method', 'selenium')
            
            if method == 'selenium':
                success = self.report_with_selenium(report_data)
            elif method == 'requests':
                success = self.report_with_requests(report_data)
            else:
                success = False
            
            # تحديث حالة البلاغ
            self.update_report_status(report_data['report_id'], 
                                    'completed' if success else 'failed',
                                    worker_id)
            
            # تغيير البروكسي بعد كل 10 بلاغات
            if report_data.get('report_number', 0) % 10 == 0:
                self.rotate_proxy(worker_id)
            
            return success
            
        except Exception as e:
            logger.error(f"فشل معالجة البلاغ: {e}")
            return False
    
    def rotate_proxy(self, worker_id):
        """تغيير البروكسي"""
        logger.info(f"العامل {worker_id}: تغيير البروكسي")
        
        if random.random() < 0.3:  # 30% فرصة لاستخدام TOR
            if self.proxy_manager.tor_enabled:
                self.proxy_manager.renew_tor_ip()
                time.sleep(5)  # انتظار تجديد IP
        else:
            # استخدام بروكسي جديد
            time.sleep(random.uniform(2, 5))
    
    def report_with_selenium(self, report_data):
        """الإبلاغ باستخدام Selenium (محاكاة المتصفح)"""
        driver = None
        try:
            # إعدادات Chrome
            chrome_options = Options()
            
            # إضافة User-Agent عشوائي
            chrome_options.add_argument(f'user-agent={self.user_agent.random}')
            
            # وضع التخفي (Headless)
            if report_data.get('headless', True):
                chrome_options.add_argument('--headless')
            
            # إعدادات أخرى
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-gpu')
            
            # إعداد البروكسي
            proxy = self.proxy_manager.get_rotated_proxy()
            if proxy:
                chrome_options.add_argument(f'--proxy-server={proxy["http"]}')
            
            # إنشاء المتصفح
            driver = webdriver.Chrome(options=chrome_options)
            driver.set_page_load_timeout(30)
            
            # زيارة صفحة الإبلاغ
            driver.get(self.facebook_urls['report_form'])
            time.sleep(random.uniform(3, 7))
            
            # ملء نموذج الإبلاغ
            self.fill_report_form(driver, report_data)
            
            # تقديم البلاغ
            self.submit_report(driver)
            
            # التحقق من النجاح
            success = self.verify_submission(driver)
            
            driver.quit()
            return success
            
        except Exception as e:
            logger.error(f"خطأ في Selenium: {e}")
            if driver:
                driver.quit()
            return False
    
    def fill_report_form(self, driver, report_data):
        """ملء نموذج الإبلاغ"""
        try:
            # البحث عن حقول النموذج (قد تتغير أسماء الحقول)
            wait = WebDriverWait(driver, 10)
            
            # رابط الحساب
            url_field = wait.until(
                EC.presence_of_element_located((By.NAME, "report_url"))
            )
            url_field.send_keys(report_data['profile_url'])
            time.sleep(random.uniform(1, 3))
            
            # نوع البلاغ
            report_type = wait.until(
                EC.presence_of_element_located((By.XPATH, "//input[@value='terrorism']"))
            )
            report_type.click()
            time.sleep(random.uniform(1, 2))
            
            # معلومات إضافية
            if report_data.get('additional_info'):
                info_field = driver.find_element(By.NAME, "additional_details")
                info_field.send_keys(report_data['additional_info'])
                time.sleep(random.uniform(1, 3))
            
            return True
            
        except Exception as e:
            logger.error(f"خطأ في ملء النموذج: {e}")
            return False
    
    def submit_report(self, driver):
        """تقديم البلاغ"""
        try:
            submit_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))
            )
            
            # محاكاة حركة الماوس قبل النقر
            action = webdriver.ActionChains(driver)
            action.move_to_element(submit_button).pause(random.uniform(0.5, 1.5)).click().perform()
            
            time.sleep(random.uniform(5, 10))
            return True
            
        except Exception as e:
            logger.error(f"خطأ في تقديم البلاغ: {e}")
            return False
    
    def report_with_requests(self, report_data):
        """الإبلاغ باستخدام Requests (أسرع لكن أقل موثوقية)"""
        try:
            # الحصول على جلسة
            session = requests.Session()
            
            # إعداد البروكسي
            proxy = self.proxy_manager.get_random_proxy()
            if proxy:
                session.proxies.update(proxy)
            
            # إعدادات الطلب
            headers = {
                'User-Agent': self.user_agent.random,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'ar,en-US;q=0.7,en;q=0.3',
                'Referer': 'https://www.facebook.com/',
                'Origin': 'https://www.facebook.com',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }
            
            # الحصول على رمز CSRF
            response = session.get(self.facebook_urls['report_form'], headers=headers)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # البحث عن حقول النموذج
            form_data = {
                'report_url': report_data['profile_url'],
                'report_type': 'terrorism',
                'additional_details': report_data.get('additional_info', ''),
                'fb_dtsg': self.extract_fb_dtsg(soup),
                'jazoest': self.extract_jazoest(soup),
            }
            
            # إرسال البلاغ
            response = session.post(
                self.facebook_urls['report_form'],
                data=form_data,
                headers=headers,
                timeout=30
            )
            
            return response.status_code == 200
            
        except Exception as e:
            logger.error(f"خطأ في Requests: {e}")
            return False
    
    def extract_fb_dtsg(self, soup):
        """استخراج رمز fb_dtsg من الصفحة"""
        try:
            fb_dtsg_input = soup.find('input', {'name': 'fb_dtsg'})
            return fb_dtsg_input['value'] if fb_dtsg_input else ''
        except:
            return ''
    
    def extract_jazoest(self, soup):
        """استخراج رمز jazoest من الصفحة"""
        try:
            jazoest_input = soup.find('input', {'name': 'jazoest'})
            return jazoest_input['value'] if jazoest_input else ''
        except:
            return ''
    
    def add_report(self, report_data):
        """إضافة بلاغ جديد للطابور"""
        report_id = f"REPORT_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{random.randint(1000, 9999)}"
        report_data['report_id'] = report_id
        report_data['timestamp'] = datetime.now().isoformat()
        report_data['status'] = 'queued'
        
        # إضافة للطابور
        self.report_queue.put(report_data)
        
        # حفظ في قاعدة البيانات
        self.save_to_database(report_data)
        
        return report_id
    
    def save_to_database(self, report_data):
        """حفظ البلاغ في قاعدة البيانات"""
        conn = sqlite3.connect('database/reports.db')
        cursor = conn.cursor()
        
        # إنشاء الجدول إذا لم يكن موجوداً
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id TEXT UNIQUE,
                profile_url TEXT,
                account_name TEXT,
                report_type TEXT,
                report_count INTEGER,
                priority TEXT,
                status TEXT,
                worker_id INTEGER,
                timestamp DATETIME,
                completed_at DATETIME,
                notes TEXT
            )
        ''')
        
        # إدخال البيانات
        cursor.execute('''
            INSERT INTO reports 
            (report_id, profile_url, account_name, report_type, report_count, 
             priority, status, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            report_data['report_id'],
            report_data['profile_url'],
            report_data.get('account_name', ''),
            ','.join(report_data.get('violation_types', [])),
            report_data.get('report_count', 1),
            report_data.get('priority', 'medium'),
            report_data['status'],
            report_data['timestamp']
        ))
        
        conn.commit()
        conn.close()
    
    def update_report_status(self, report_id, status, worker_id=None):
        """تحديث حالة البلاغ"""
        conn = sqlite3.connect('database/reports.db')
        cursor = conn.cursor()
        
        if status == 'completed':
            cursor.execute('''
                UPDATE reports 
                SET status = ?, worker_id = ?, completed_at = datetime('now')
                WHERE report_id = ?
            ''', (status, worker_id, report_id))
        else:
            cursor.execute('''
                UPDATE reports 
                SET status = ?, worker_id = ?
                WHERE report_id = ?
            ''', (status, worker_id, report_id))
        
        conn.commit()
        conn.close()
    
    def get_report_stats(self):
        """الحصول على إحصائيات البلاغات"""
        conn = sqlite3.connect('database/reports.db')
        cursor = conn.cursor()
        
        stats = {
            'total': 0,
            'completed': 0,
            'failed': 0,
            'queued': 0,
            'today': 0
        }
        
        try:
            # إجمالي البلاغات
            cursor.execute("SELECT COUNT(*) FROM reports")
            stats['total'] = cursor.fetchone()[0]
            
            # البلاغات المكتملة
            cursor.execute("SELECT COUNT(*) FROM reports WHERE status = 'completed'")
            stats['completed'] = cursor.fetchone()[0]
            
            # البلاغات الفاشلة
            cursor.execute("SELECT COUNT(*) FROM reports WHERE status = 'failed'")
            stats['failed'] = cursor.fetchone()[0]
            
            # البلاغات في الطابور
            cursor.execute("SELECT COUNT(*) FROM reports WHERE status = 'queued'")
            stats['queued'] = cursor.fetchone()[0]
            
            # بلاغات اليوم
            today = datetime.now().strftime('%Y-%m-%d')
            cursor.execute("SELECT COUNT(*) FROM reports WHERE date(timestamp) = ?", (today,))
            stats['today'] = cursor.fetchone()[0]
            
        except Exception as e:
            logger.error(f"خطأ في إحصائيات قاعدة البيانات: {e}")
        
        conn.close()
        return stats

# =============== واجهة Flask ===============

# تهيئة المدير
reporter = FacebookReporter()

@app.route('/')
def index():
    """الصفحة الرئيسية"""
    stats = reporter.get_report_stats()
    return render_template('index.html', stats=stats)

@app.route('/api/submit_report', methods=['POST'])
def submit_report():
    """تقديم بلاغ جديد"""
    try:
        data = request.json
        
        # التحقق من البيانات
        required_fields = ['profile_url']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'حقل {field} مطلوب'
                }), 400
        
        # إعداد بيانات البلاغ
        report_data = {
            'profile_url': data['profile_url'],
            'account_name': data.get('account_name', ''),
            'violation_types': data.get('violation_types', []),
            'report_count': min(int(data.get('report_count', 1)), 10),
            'priority': data.get('priority', 'medium'),
            'additional_info': data.get('additional_info', ''),
            'method': data.get('method', 'selenium'),
            'headless': data.get('headless', True)
        }
        
        # إضافة عدة بلاغات إذا كان العدد > 1
        report_ids = []
        for i in range(report_data['report_count']):
            report_data['report_number'] = i + 1
            report_id = reporter.add_report(report_data.copy())
            report_ids.append(report_id)
            time.sleep(0.5)  # فاصل بين إنشاء البلاغات
        
        return jsonify({
            'success': True,
            'message': f'تم إضافة {len(report_ids)} بلاغ للطابور',
            'report_ids': report_ids,
            'queue_size': reporter.report_queue.qsize()
        })
        
    except Exception as e:
        logger.error(f"خطأ في تقديم البلاغ: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/reports')
def get_reports():
    """الحصول على قائمة البلاغات"""
    try:
        conn = sqlite3.connect('database/reports.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT report_id, profile_url, account_name, report_type, 
                   report_count, priority, status, timestamp, completed_at
            FROM reports 
            ORDER BY timestamp DESC 
            LIMIT 100
        ''')
        
        reports = []
        for row in cursor.fetchall():
            reports.append({
                'report_id': row[0],
                'profile_url': row[1],
                'account_name': row[2],
                'report_type': row[3],
                'report_count': row[4],
                'priority': row[5],
                'status': row[6],
                'timestamp': row[7],
                'completed_at': row[8]
            })
        
        conn.close()
        
        return jsonify({
            'success': True,
            'reports': reports,
            'total': len(reports)
        })
        
    except Exception as e:
        logger.error(f"خطأ في جلب البلاغات: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/stats')
def get_stats():
    """الحصول على إحصائيات النظام"""
    stats = reporter.get_report_stats()
    return jsonify({
        'success': True,
        'stats': stats,
        'queue_size': reporter.report_queue.qsize(),
        'active_workers': reporter.max_concurrent
    })

@app.route('/dashboard')
def dashboard():
    """لوحة التحكم"""
    return render_template('dashboard.html')

@app.route('/api/proxy/rotate', methods=['POST'])
def rotate_proxy():
    """تغيير البروكسي يدوياً"""
    try:
        # تجديد TOR IP إذا كان مفعلاً
        if reporter.proxy_manager.tor_enabled:
            reporter.proxy_manager.renew_tor_ip()
        
        return jsonify({
            'success': True,
            'message': 'تم تغيير البروكسي بنجاح'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# =============== إعدادات TOR (اختياري) ===============

def setup_tor():
    """إعداد TOR (اختياري - يحتاج تثبيت TOR)"""
    try:
        # التحقق من وجود TOR
        response = requests.get('http://check.torproject.org', 
                              proxies={'http': 'socks5://127.0.0.1:9050'})
        if 'Congratulations' in response.text:
            logger.info("تم اكتشاف TOR بنجاح")
            return True
        else:
            logger.warning("TOR غير نشط")
            return False
    except:
        logger.warning("فشل في الاتصال بـ TOR")
        return False

# =============== التشغيل الرئيسي ===============

if __name__ == '__main__':
    # إنشاء المجلدات
    os.makedirs('logs', exist_ok=True)
    os.makedirs('database', exist_ok=True)
    os.makedirs('proxies', exist_ok=True)
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static/css', exist_ok=True)
    os.makedirs('static/js', exist_ok=True)
    os.makedirs('static/images', exist_ok=True)
    
    # التحقق من TOR
    tor_available = setup_tor()
    if tor_available:
        reporter.proxy_manager.tor_enabled = True
    
    logger.info("بدء تشغيل أداة الإبلاغ الآمنة...")
    logger.info(f"عدد العاملين: {reporter.max_concurrent}")
    logger.info(f"حجم طابور البلاغات: {reporter.report_queue.qsize()}")
    
    # تشغيل الخادم
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,
        threaded=True
    )
