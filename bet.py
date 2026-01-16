import os
import json
import time
import random
import threading
import requests
from bs4 import BeautifulSoup
import pandas as pd
from tkinter import *
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import datetime, timedelta
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ProxyPool:
    """代理池管理类"""
    def __init__(self, pool_size=5):
        self.pool_size = pool_size
        self.proxy_queue = Queue()
        self.proxy_username = "19878843190"
        self.proxy_password = "HEGKp5ye"
        self.proxy_url = "http://121.5.174.75:9000/get_proxy"
        self.lock = threading.Lock()
        
    def get_proxy(self, max_retries=20):
        """获取单个代理"""
        for retry in range(1, max_retries + 1):
            try:
                response = requests.get(self.proxy_url, timeout=10)
                if response.status_code == 200:
                    proxy_raw = response.json()["data"]["raw"]
                    proxy_with_auth = f"{self.proxy_username}:{self.proxy_password}@{proxy_raw}"
                    proxy = {
                        'http': f'http://{proxy_with_auth}',
                        'https': f'http://{proxy_with_auth}'
                    }
                    logger.info(f'成功获取代理: {proxy_raw}')
                    return proxy
                else:
                    logger.info(f'获取代理失败,请求重试 {retry}/{max_retries}. Status code: {response.status_code}')
            except requests.RequestException as e:
                logger.info(f'获取代理失败,请求重试 {retry}/{max_retries}. Exception: {e}')
            
            if retry < max_retries:
                time.sleep(1)
        
        logger.warning(f'无法获取代理,已达到最大重试次数 ({max_retries})')
        return None
    
    def init_pool(self):
        """初始化代理池"""
        logger.info(f'开始初始化代理池,目标数量: {self.pool_size}')
        for i in range(self.pool_size):
            proxy = self.get_proxy()
            if proxy:
                self.proxy_queue.put(proxy)
                logger.info(f'代理池初始化进度: {i+1}/{self.pool_size}')
            else:
                logger.warning(f'代理池初始化失败: 无法获取足够的代理')
                break
        
        actual_size = self.proxy_queue.qsize()
        logger.info(f'代理池初始化完成,实际数量: {actual_size}')
        return actual_size > 0
    
    def get_proxy_from_pool(self):
        """从代理池获取代理"""
        with self.lock:
            if self.proxy_queue.empty():
                logger.warning('代理池为空,尝试获取新代理')
                proxy = self.get_proxy()
                return proxy
            else:
                return self.proxy_queue.get()
    
    def return_proxy_to_pool(self, proxy):
        """归还代理到代理池"""
        if proxy:
            with self.lock:
                if self.proxy_queue.qsize() < self.pool_size:
                    self.proxy_queue.put(proxy)
    
    def refresh_proxy(self):
        """刷新失效的代理"""
        new_proxy = self.get_proxy()
        return new_proxy



class OddsCrawlerGUI:
    def __init__(self, master):
        self.master = master
        master.title("500彩票网赔率爬虫(全公司无时间限制+多线程+代理池)")
        master.geometry("900x900")

        # 爬虫状态控制
        self.is_running = False
        self.is_paused = False
        self.current_id = 1369720
        self.retry_urls = []
        self.progress_file = "crawler_progress_no_limit.json"

        # 公司数据管理
        self.company_data_files = {}
        self.company_stats = {}
        
        # 线程池配置
        self.thread_pool_size = 3  # 默认3个线程
        self.executor = None
        
        # 代理池
        self.proxy_pool = None
        self.use_proxy = True  # 是否使用代理

        # 创建GUI组件
        self.create_widgets()

        # 加载进度
        self.load_progress()

    def create_widgets(self):
        # 顶部框架 - 目录选择
        top_frame = Frame(self.master)
        top_frame.pack(pady=10, fill=X)

        Label(top_frame, text="保存目录:").pack(side=LEFT, padx=5)
        self.dir_entry = Entry(top_frame, width=50)
        self.dir_entry.pack(side=LEFT, expand=True, fill=X)
        self.dir_entry.insert(0, os.getcwd())

        browse_btn = Button(top_frame, text="浏览...", command=self.browse_directory)
        browse_btn.pack(side=LEFT, padx=5)

        # 设置框架
        setting_frame = Frame(self.master)
        setting_frame.pack(pady=5, fill=X)

        # 起始ID设置
        id_frame = Frame(setting_frame)
        id_frame.pack(side=LEFT, padx=5)

        Label(id_frame, text="起始ID:").pack(side=LEFT)
        self.start_id_entry = Entry(id_frame, width=10)
        self.start_id_entry.pack(side=LEFT)
        self.start_id_entry.insert(0, "1369720")

        set_range_btn = Button(setting_frame, text="设置ID范围", command=self.set_id_range)
        set_range_btn.pack(side=LEFT, padx=5)
        
        # 线程池设置
        thread_frame = Frame(self.master)
        thread_frame.pack(pady=5, fill=X)
        
        Label(thread_frame, text="线程数:").pack(side=LEFT, padx=5)
        self.thread_spinbox = Spinbox(thread_frame, from_=1, to=10, width=5)
        self.thread_spinbox.delete(0, END)
        self.thread_spinbox.insert(0, "3")
        self.thread_spinbox.pack(side=LEFT, padx=5)
        
        # 代理设置
        Label(thread_frame, text="代理池大小:").pack(side=LEFT, padx=5)
        self.proxy_pool_spinbox = Spinbox(thread_frame, from_=1, to=20, width=5)
        self.proxy_pool_spinbox.delete(0, END)
        self.proxy_pool_spinbox.insert(0, "5")
        self.proxy_pool_spinbox.pack(side=LEFT, padx=5)
        
        self.use_proxy_var = IntVar(value=1)
        self.use_proxy_checkbox = Checkbutton(
            thread_frame, 
            text="使用代理", 
            variable=self.use_proxy_var
        )
        self.use_proxy_checkbox.pack(side=LEFT, padx=5)
        
        init_proxy_btn = Button(thread_frame, text="初始化代理池", command=self.init_proxy_pool)
        init_proxy_btn.pack(side=LEFT, padx=5)

        # 控制按钮框架
        control_frame = Frame(self.master)
        control_frame.pack(pady=10, fill=X)

        self.start_btn = Button(control_frame, text="开始爬取", command=self.start_crawling)
        self.start_btn.pack(side=LEFT, padx=5)

        self.pause_btn = Button(control_frame, text="暂停爬取", command=self.pause_crawling, state=DISABLED)
        self.pause_btn.pack(side=LEFT, padx=5)

        # 状态标签
        self.status_label = Label(control_frame, text="就绪", fg="green")
        self.status_label.pack(side=LEFT, padx=20)
        
        # 代理状态标签
        self.proxy_status_label = Label(control_frame, text="代理池: 未初始化", fg="gray")
        self.proxy_status_label.pack(side=LEFT, padx=10)

        # 底部框架 - 日志和统计
        bottom_frame = Frame(self.master)
        bottom_frame.pack(pady=10, fill=BOTH, expand=True)

        # 爬取统计
        stats_frame = Frame(bottom_frame)
        stats_frame.pack(fill=X, padx=5, pady=5)

        self.total_label = Label(stats_frame, text="总计: 0")
        self.total_label.pack(side=LEFT, padx=10)

        self.success_label = Label(stats_frame, text="成功: 0")
        self.success_label.pack(side=LEFT, padx=10)

        self.skip_label = Label(stats_frame, text="跳过: 0")
        self.skip_label.pack(side=LEFT, padx=10)

        self.error_label = Label(stats_frame, text="错误: 0")
        self.error_label.pack(side=LEFT, padx=10)

        # 公司数据统计
        self.company_count_label = Label(stats_frame, text="公司数量: 0")
        self.company_count_label.pack(side=LEFT, padx=10)

        # 重置统计按钮
        reset_stats_btn = Button(stats_frame, text="重置统计", command=self.reset_stats)
        reset_stats_btn.pack(side=RIGHT, padx=10)

        # 查看数据文件按钮
        view_files_btn = Button(stats_frame, text="查看数据文件", command=self.view_data_files)
        view_files_btn.pack(side=RIGHT, padx=10)

        # 日志标签
        Label(bottom_frame, text="爬取日志:").pack(anchor=NW, padx=5)

        # 使用ScrolledText替代普通Text
        self.log_text = scrolledtext.ScrolledText(bottom_frame, wrap=WORD, height=15)
        self.log_text.pack(fill=BOTH, expand=True, padx=5, pady=5)

        # 进度条
        self.progress_var = DoubleVar()
        self.progress_bar = ttk.Progressbar(
            bottom_frame, variable=self.progress_var, maximum=1369720 - 1
        )
        self.progress_bar.pack(fill=X, padx=5, pady=5)

        # 初始化统计
        self.reset_stats()

def browse_directory(self):
        directory = filedialog.askdirectory()
        if directory:
            self.dir_entry.delete(0, END)
            self.dir_entry.insert(0, directory)
            # 更新公司数据文件统计
            self.update_company_stats()

    def log_message(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_text.insert(END, f"[{timestamp}] {message}\n")
        self.log_text.see(END)
        self.master.update_idletasks()

    def reset_stats(self):
        """重置统计信息"""
        self.total_count = 0
        self.success_count = 0
        self.skip_count = 0
        self.error_count = 0
        self.update_stats_display()

    def update_stats_display(self):
        """更新统计信息显示"""
        self.total_label.config(text=f"总计: {self.total_count}")
        self.success_label.config(text=f"成功: {self.success_count}")
        self.skip_label.config(text=f"跳过: {self.skip_count}")
        self.error_label.config(text=f"错误: {self.error_count}")
        self.company_count_label.config(text=f"公司数量: {len(self.company_stats)}")

    def update_company_stats(self):
        """更新公司数据文件统计"""
        data_dir = self.dir_entry.get()
        if not os.path.exists(data_dir):
            self.company_count_label.config(text="公司数据文件: 0")
            return

        # 统计已存在的公司数据文件
        existing_files = 0
        for filename in os.listdir(data_dir):
            if filename.startswith("odds_data_") and filename.endswith(".xlsx"):
                existing_files += 1

        self.company_count_label.config(text=f"公司数据文件: {existing_files}")

    def view_data_files(self):
        """查看数据文件信息"""
        data_dir = self.dir_entry.get()
        if not os.path.exists(data_dir):
            messagebox.showinfo("信息", f"目录不存在: {data_dir}")
            return

        info_window = Toplevel(self.master)
        info_window.title("数据文件信息")
        info_window.geometry("600x400")

        # 创建文本框
        info_text = scrolledtext.ScrolledText(info_window, wrap=WORD)
        info_text.pack(fill=BOTH, expand=True, padx=10, pady=10)

        info = f"数据目录: {data_dir}\n\n"
        info += "=" * 50 + "\n"

        # 获取所有数据文件
        data_files = [f for f in os.listdir(data_dir) if f.startswith("odds_data_") and f.endswith(".xlsx")]

        for data_file in data_files:
            data_path = os.path.join(data_dir, data_file)
            try:
                # 读取Excel文件
                df = pd.read_excel(data_path)
                company_name = data_file.replace("odds_data_", "").replace(".xlsx", "")
                info += f"公司: {company_name}\n"
                info += f"文件: {data_file}\n"
                info += f"数据行数: {len(df)}\n"

                if not df.empty:
                    info += f"最新采集时间: {df['采集时间'].iloc[-1] if '采集时间' in df.columns else 'N/A'}\n"

                info += "-" * 30 + "\n"
            except Exception as e:
                info += f"文件: {data_file}\n"
                info += f"读取错误: {str(e)}\n"
                info += "-" * 30 + "\n"

        if not data_files:
            info += "尚未创建任何数据文件\n"

        info_text.insert(1.0, info)
        info_text.config(state=DISABLED)

    def load_progress(self):
        try:
            with open(os.path.join(self.dir_entry.get(), self.progress_file), "r") as f:
                progress = json.load(f)
                self.current_id = progress.get("current_id", 1369720)
                self.retry_urls = progress.get("retry_urls", [])

                # 更新输入框
                self.start_id_entry.delete(0, END)
                self.start_id_entry.insert(0, str(self.current_id))

                self.log_message(f"加载进度: 从ID {self.current_id} 继续")
                self.progress_var.set(1369720 - self.current_id)

        except FileNotFoundError:
            self.log_message("未找到进度文件,将从ID 1369720开始")
        except Exception as e:
            self.log_message(f"加载进度文件失败: {str(e)}")

    def save_progress(self):
        progress = {
            "current_id": self.current_id,
            "retry_urls": self.retry_urls
        }
        try:
            with open(os.path.join(self.dir_entry.get(), self.progress_file), "w") as f:
                json.dump(progress, f, indent=2)
        except Exception as e:
            self.log_message(f"保存进度文件失败: {str(e)}")

    def get_company_data_filename(self, company_name):
        """获取公司数据文件名"""
        # 移除可能存在的非法文件名字符
        safe_name = re.sub(r'[\\/*?:"<>|]', "_", company_name)
        return f"odds_data_{safe_name}.xlsx"

    def get_company_temp_filename(self, company_name):
        """获取公司临时文件名"""
        safe_name = re.sub(r'[\\/*?:"<>|]', "_", company_name)
        return f"temp_odds_data_{safe_name}.xlsx"

    def save_company_data(self, data, company_name):
        """保存单个公司的数据到独立的xlsx文件"""
        try:
            data_dir = self.dir_entry.get()

            # 确保目录存在
            if not os.path.exists(data_dir):
                os.makedirs(data_dir)

            # 获取文件名
            data_file = self.get_company_data_filename(company_name)
            temp_file = self.get_company_temp_filename(company_name)

            final_path = os.path.join(data_dir, data_file)
            temp_path = os.path.join(data_dir, temp_file)

            # 如果文件不存在,创建并写入表头
            if not os.path.exists(final_path):
                df = pd.DataFrame([data])
            else:
                # 读取现有数据
                existing_data = pd.read_excel(final_path)
                # 创建新数据DataFrame
                new_data = pd.DataFrame([data])
                # 合并数据
                df = pd.concat([existing_data, new_data], ignore_index=True)

            # 保存到临时文件
            df.to_excel(temp_path, index=False)

            # 替换原文件
            if os.path.exists(final_path):
                os.remove(final_path)
            os.rename(temp_path, final_path)

            # 更新公司统计
            if company_name not in self.company_stats:
                self.company_stats[company_name] = 0
                self.update_stats_display()
            self.company_stats[company_name] += 1

            return True

        except Exception as e:
            self.log_message(f"保存{company_name}数据失败: {str(e)}")
            # 清理临时文件
            temp_path = os.path.join(data_dir, self.get_company_temp_filename(company_name))
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
            return False

    def get_random_ua(self):
        """UA池"""
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; WOW64; Trident/7.0; rv:11.0) like Gecko",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36 Edg/90.0.818.62"
        ]
        return random.choice(user_agents)


def init_proxy_pool(self):
        """初始化代理池"""
        if self.is_running:
            messagebox.showwarning("警告", "爬虫运行中,无法初始化代理池")
            return
        
        pool_size = int(self.proxy_pool_spinbox.get())
        self.log_message(f"开始初始化代理池,大小: {pool_size}")
        self.proxy_status_label.config(text="代理池: 初始化中...", fg="orange")
        
        # 在新线程中初始化代理池
        def init_thread():
            self.proxy_pool = ProxyPool(pool_size=pool_size)
            success = self.proxy_pool.init_pool()
            
            if success:
                actual_size = self.proxy_pool.proxy_queue.qsize()
                self.log_message(f"代理池初始化成功,可用代理数: {actual_size}")
                self.proxy_status_label.config(
                    text=f"代理池: {actual_size}/{pool_size} 可用", 
                    fg="green"
                )
            else:
                self.log_message("代理池初始化失败")
                self.proxy_status_label.config(text="代理池: 初始化失败", fg="red")
        
        threading.Thread(target=init_thread, daemon=True).start()

    def crawl_page(self, page_id, use_proxy=True):
        """爬取单个页面,返回找到的所有公司数据"""
        url = f"https://odds.500.com/fenxi/ouzhi-{page_id}.shtml"

        headers = {
            "User-Agent": self.get_random_ua(),
            "Referer": "https://odds.500.com/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
        }

        # 获取代理
        proxy = None
        if use_proxy and self.proxy_pool:
            proxy = self.proxy_pool.get_proxy_from_pool()
            if not proxy:
                self.log_message(f"无法获取代理,页面 {page_id} 将不使用代理")

        try:
            # 随机延迟0.5-2秒(使用代理可以缩短延迟)
            delay = random.uniform(0.5, 2) if proxy else random.uniform(1, 3)
            time.sleep(delay)

            # 发送请求
            kwargs = {
                'headers': headers,
                'timeout': 15
            }
            if proxy:
                kwargs['proxies'] = proxy
            
            response = requests.get(url, **kwargs)
            response.encoding = 'gb2312'

            # 检查是否被反爬
            if "访问验证" in response.text or response.status_code != 200:
                self.log_message(f"可能触发反爬: {page_id} (状态码: {response.status_code})")
                
                # 如果使用了代理,尝试刷新代理
                if proxy and self.proxy_pool:
                    self.log_message(f"代理可能失效,尝试刷新代理")
                    new_proxy = self.proxy_pool.refresh_proxy()
                    if new_proxy:
                        self.proxy_pool.return_proxy_to_pool(new_proxy)
                
                return None

            # 归还代理
            if proxy and self.proxy_pool:
                self.proxy_pool.return_proxy_to_pool(proxy)

            soup = BeautifulSoup(response.text, 'html.parser')

            # 提取比赛时间
            game_time = "未知时间"
            game_time_element = soup.find('p', class_='game_time')
            if game_time_element:
                game_time_text = game_time_element.get_text().strip()
                time_match = re.search(r'(\d{4}-\d{2}-\d{2})', game_time_text)
                if time_match:
                    game_time = time_match.group(0)

            # 提取比赛基本信息
            hd_name_elements = soup.find_all('a', class_='hd_name')
            if len(hd_name_elements) >= 3:
                home_team = hd_name_elements[0].get_text().strip()
                league = hd_name_elements[1].get_text().strip()
                away_team = hd_name_elements[2].get_text().strip()
            else:
                home_team = "未知主队"
                league = "未知联赛"
                away_team = "未知客队"

            # 提取比分
            home_score = "0"
            away_score = "0"
            total_goals = "0"
            result = "未知"

            score_element = soup.find('p', class_='odds_hd_bf')
            if score_element:
                score_text = score_element.get_text().strip()
                score_match = re.search(r'(\d+):(\d+)', score_text)
                if score_match:
                    home_score = score_match.group(1)
                    away_score = score_match.group(2)
                    total_goals = str(int(home_score) + int(away_score))

                    if home_score > away_score:
                        result = "胜"
                    elif home_score < away_score:
                        result = "负"
                    else:
                        result = "平"

            # 半全场信息
            half_full_time = "未知"

            # 当前采集时间
            collect_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # 提取所有公司的赔率数据
            all_company_data = []
            rows = soup.find_all('tr', class_=['tr1', 'tr2'])

            for row in rows:
                company_td = row.find('td', class_='tb_plgs')
                if company_td:
                    company_name = company_td.get_text().strip()
                    if not company_name:
                        continue

                    company_data = self.extract_company_odds(row)
                    if company_data:
                        data = {
                            '日期': game_time,
                            '比赛编号': page_id,
                            '联赛': league,
                            '博彩公司': company_name,
                            '主队': home_team,
                            '比分': f"{home_score}-{away_score}",
                            '客队': away_team,
                            '总进球': total_goals,
                            '初_主队': company_data['initial_home'],
                            '初_和': company_data['initial_draw'],
                            '初_客队': company_data['initial_away'],
                            '终_主队': company_data['final_home'],
                            '终_和': company_data['final_draw'],
                            '终_客队': company_data['final_away'],
                            '胜负': result,
                            '半/全场': half_full_time,
                            '采集时间': collect_time
                        }
                        all_company_data.append((data, company_name))

            if all_company_data:
                return all_company_data
            else:
                return None

        except requests.exceptions.ProxyError as e:
            self.log_message(f"代理错误 {page_id}: {str(e)}")
            # 代理失效,不归还到池中
            if self.proxy_pool:
                new_proxy = self.proxy_pool.refresh_proxy()
                if new_proxy:
                    self.proxy_pool.return_proxy_to_pool(new_proxy)
            return None
            
        except requests.exceptions.Timeout as e:
            self.log_message(f"请求超时 {page_id}: {str(e)}")
            if proxy and self.proxy_pool:
                self.proxy_pool.return_proxy_to_pool(proxy)
            return None
            
        except Exception as e:
            self.log_message(f"爬取页面 {page_id} 出错: {str(e)}")
            if proxy and self.proxy_pool:
                self.proxy_pool.return_proxy_to_pool(proxy)
            return None

    def extract_company_odds(self, row):
        """从行中提取公司的赔率数据"""
        try:
            # 初盘赔率
            initial_odds = row.find('tr', class_='tr_bdb td_show_cp')
            if initial_odds:
                initial_odds_data = initial_odds.find_all('td')
                if len(initial_odds_data) >= 3:
                    initial_home = initial_odds_data[0].get_text().strip()
                    initial_draw = initial_odds_data[1].get_text().strip()
                    initial_away = initial_odds_data[2].get_text().strip()
                else:
                    initial_home = initial_draw = initial_away = "N/A"
            else:
                initial_home = initial_draw = initial_away = "N/A"

            # 终盘赔率
            final_odds = row.find_all('tr')[1]  # 第二行是终盘
            if final_odds:
                final_odds_data = final_odds.find_all('td')
                if len(final_odds_data) >= 3:
                    final_home = final_odds_data[0].get_text().strip()
                    final_draw = final_odds_data[1].get_text().strip()
                    final_away = final_odds_data[2].get_text().strip()
                else:
                    final_home = final_draw = final_away = "N/A"
            else:
                final_home = final_draw = final_away = "N/A"

            return {
                'initial_home': initial_home,
                'initial_draw': initial_draw,
                'initial_away': initial_away,
                'final_home': final_home,
                'final_draw': final_draw,
                'final_away': final_away
            }

        except Exception as e:
            self.log_message(f"提取赔率数据出错: {str(e)}")
            return None

    def set_id_range(self):
        """设置ID范围"""
        try:
            # 设置起始ID
            start_id = int(self.start_id_entry.get())
            if start_id < 1:
                messagebox.showerror("错误", "起始ID必须大于0")
                return

            self.current_id = start_id
            self.log_message(f"已设置起始ID: {start_id}")

        except ValueError:
            self.log_message("请输入有效的数字")


def start_crawling(self):
        """开始爬取"""
        if self.is_running:
            return

        # 检查是否使用代理
        use_proxy = self.use_proxy_var.get() == 1
        
        if use_proxy and not self.proxy_pool:
            response = messagebox.askyesno(
                "代理未初始化", 
                "代理池未初始化,是否先初始化代理池?\n\n选择'否'将不使用代理继续爬取"
            )
            if response:
                messagebox.showinfo("提示", "请先点击'初始化代理池'按钮")
                return
            else:
                self.use_proxy_var.set(0)
                use_proxy = False

        self.is_running = True
        self.is_paused = False
        self.start_btn.config(state=DISABLED)
        self.pause_btn.config(state=NORMAL)
        self.status_label.config(text="运行中", fg="green")

        # 重置统计
        self.reset_stats()
        
        # 获取线程数
        self.thread_pool_size = int(self.thread_spinbox.get())
        self.log_message(f"启动多线程爬虫,线程数: {self.thread_pool_size}")
        if use_proxy:
            self.log_message("已启用代理池")

        # 启动爬虫线程
        self.crawler_thread = threading.Thread(
            target=self.run_crawler_multithreaded, 
            daemon=True
        )
        self.crawler_thread.start()

    def pause_crawling(self):
        """暂停爬取"""
        if not self.is_running or self.is_paused:
            return

        self.is_paused = True
        self.pause_btn.config(state=DISABLED)
        self.status_label.config(text="已暂停", fg="orange")
        self.log_message("爬取已暂停")
        
        # 关闭线程池
        if self.executor:
            self.executor.shutdown(wait=False)
            self.executor = None

    def run_crawler_multithreaded(self):
        """多线程爬虫主逻辑"""
        self.log_message("开始多线程爬取...")
        
        use_proxy = self.use_proxy_var.get() == 1

        # 先处理需要重试的URL
        if self.retry_urls:
            self.log_message(f"开始处理 {len(self.retry_urls)} 个重试URL...")
            self.process_retry_urls(use_proxy)

        # 创建线程池
        self.executor = ThreadPoolExecutor(max_workers=self.thread_pool_size)
        
        # 批量提交任务
        batch_size = self.thread_pool_size * 5  # 每批次任务数
        
        while self.current_id >= 1 and self.is_running and not self.is_paused:
            futures = []
            batch_ids = []
            
            # 提交一批任务
            for _ in range(batch_size):
                if self.current_id < 1 or not self.is_running or self.is_paused:
                    break
                
                page_id = self.current_id
                batch_ids.append(page_id)
                
                # 提交任务到线程池
                future = self.executor.submit(
                    self.crawl_page_wrapper, 
                    page_id, 
                    use_proxy
                )
                futures.append(future)
                
                self.current_id -= 1
            
            # 等待这批任务完成
            for future in as_completed(futures):
                if not self.is_running or self.is_paused:
                    break
                
                try:
                    result = future.result(timeout=30)
                    if result:
                        page_id, data_list = result
                        if data_list:
                            # 保存每个公司的数据到各自的文件
                            saved_count = 0
                            for data, company_name in data_list:
                                if self.save_company_data(data, company_name):
                                    saved_count += 1
                            
                            if saved_count > 0:
                                self.log_message(
                                    f"✓ 页面 {page_id}: 保存 {saved_count} 个公司数据"
                                )
                        else:
                            self.log_message(f"○ 页面 {page_id}: 无数据")
                    
                except Exception as e:
                    self.log_message(f"任务执行出错: {str(e)}")
                
                # 更新进度
                self.progress_var.set(1369720 - self.current_id)
                self.master.update()
            
            # 保存进度
            self.save_progress()
            
            # 短暂暂停,防止UI卡死
            time.sleep(0.1)

        # 关闭线程池
        if self.executor:
            self.executor.shutdown(wait=True)
            self.executor = None

        if self.current_id < 1:
            self.log_message("=" * 50)
            self.log_message("所有页面爬取完成!")
            self.log_message(f"总计: {self.total_count}, 成功: {self.success_count}, "
                           f"跳过: {self.skip_count}, 错误: {self.error_count}")
            self.log_message("=" * 50)

        self.is_running = False
        self.start_btn.config(state=NORMAL)
        self.pause_btn.config(state=DISABLED)
        self.status_label.config(text="已完成", fg="blue")

    def crawl_page_wrapper(self, page_id, use_proxy):
        """爬取页面的包装方法,用于线程池"""
        # 更新统计
        self.total_count += 1
        self.update_stats_display()
        
        # 调用爬取方法
        data_list = self.crawl_page(page_id, use_proxy)
        
        if data_list:
            self.success_count += 1
            self.update_stats_display()
            return (page_id, data_list)
        else:
            # 判断是否需要重试
            url = f"https://odds.500.com/fenxi/ouzhi-{page_id}.shtml"
            if url not in self.retry_urls:
                self.retry_urls.append(url)
            
            self.skip_count += 1
            self.update_stats_display()
            return (page_id, None)

    def process_retry_urls(self, use_proxy):
        """处理重试URL列表"""
        if not self.retry_urls:
            return
        
        retry_count = len(self.retry_urls)
        self.log_message(f"开始重试 {retry_count} 个失败的URL...")
        
        retry_executor = ThreadPoolExecutor(max_workers=self.thread_pool_size)
        futures = []
        
        # 复制重试列表并清空原列表
        urls_to_retry = self.retry_urls.copy()
        self.retry_urls.clear()
        
        for url in urls_to_retry:
            if not self.is_running or self.is_paused:
                # 将未处理的URL放回重试列表
                self.retry_urls.extend([u for u in urls_to_retry if u not in [url]])
                break
            
            match = re.search(r'ouzhi-(\d+)', url)
            if match:
                page_id = int(match.group(1))
                future = retry_executor.submit(
                    self.crawl_page_wrapper, 
                    page_id, 
                    use_proxy
                )
                futures.append((future, page_id))
        
        # 等待重试任务完成
        for future, page_id in futures:
            if not self.is_running or self.is_paused:
                break
            
            try:
                result = future.result(timeout=30)
                if result:
                    _, data_list = result
                    if data_list:
                        saved_count = 0
                        for data, company_name in data_list:
                            if self.save_company_data(data, company_name):
                                saved_count += 1
                        
                        if saved_count > 0:
                            self.log_message(
                                f"✓ 重试成功 {page_id}: 保存 {saved_count} 个公司数据"
                            )
            except Exception as e:
                self.log_message(f"重试任务 {page_id} 出错: {str(e)}")
                # 重新加入重试列表
                url = f"https://odds.500.com/fenxi/ouzhi-{page_id}.shtml"
                if url not in self.retry_urls:
                    self.retry_urls.append(url)
        
        retry_executor.shutdown(wait=True)
        
        remaining = len(self.retry_urls)
        self.log_message(f"重试完成,剩余失败: {remaining} 个")
        self.save_progress()

# 主程序
if __name__ == "__main__":
    root = Tk()
    app = OddsCrawlerGUI(root)
    root.mainloop()
