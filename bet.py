#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
滚球水位实时监控系统 v7.5
- 新数据结构: markets/selections
- 支持更多盘口类型: RE, ROU, ROUO, ROUU, ROUHO, ROUHU等
- 直接使用 wtype/rtype/chose_team 下注
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support. ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium. common.exceptions import TimeoutException, NoSuchElementException
import requests
import urllib3
import xml.etree.ElementTree as ET
import time
import pickle
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
import re
import json
import os
import base64
from collections import defaultdict

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================== 配置 ==================
URL = "https://mos055.com/"
API_URL = "https://mos055.com/transform. php"
USERNAME = "LJJ123123"
PASSWORD = "zz66688899"
COOKIES_FILE = "mos055_cookies.pkl"
CONFIG_FILE = "bet_config.json"
XHR_DATA_FILE = "xhr_collected. json"
ANALYSIS_FILE = "xhr_analysis.json"
ROLLING_ODDS_FILE = "rolling_odds_full.json"

# 盘口类型映射
MARKET_NAMES = {
    'RE': '让球',
    'ROU':  '大/小',
    'ROUO': '大球',
    'ROUU': '小球',
    'ROUHO': '主队大',
    'ROUHU': '主队小',
    'ROUCO': '客队大',
    'ROUCU': '客队小',
    'RM': '独赢',
    'HRE': '半场让球',
    'HROU': '半场大/小',
    'HRM': '半场独赢',
    'RG': '下个进球',
    'RTS': '双方进球',
}

SCOPE_NAMES = {
    'FULL': '全场',
    'HALF': '半场',
    '1H': '上半场',
    '2H': '下半场',
}


# ================== 滚球数据解析器 (新结构) ==================
class RollingOddsParser:
    """
    解析滚球数据，输出 markets/selections 结构
    可直接用于下注
    """
    
    def __init__(self, xml_string: str):
        self.raw_text = xml_string
        self. root = None
        self.parse_errors = []
        self.is_valid = False
        self._try_parse(xml_string)
    
    def _try_parse(self, xml_string: str):
        """尝试解析XML"""
        if not xml_string or not isinstance(xml_string, str):
            self.parse_errors.append("空响应或非字符串")
            return
        
        if 'table id error' in xml_string. lower():
            self.parse_errors.append("table id error")
            return
        
        if xml_string.strip() == 'CheckEMNU':
            self. parse_errors.append("CheckEMNU")
            return
        
        if len(xml_string. strip()) < 50:
            self.parse_errors.append(f"响应过短: {xml_string[: 100]}")
            return
        
        try:
            xml_string = re.sub(r'<\?xml[^>]*\?>', '', xml_string)
            xml_string = xml_string.strip().lstrip('\ufeff')
            xml_string = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xml_string)
            
            if not xml_string: 
                self.parse_errors.append("预处理后为空")
                return
            
            self.root = ET.fromstring(xml_string)
            self.is_valid = True
            
        except ET.ParseError as e:
            self.parse_errors.append(f"XML解析错误: {str(e)}")
            try:
                wrapped = f"<root>{xml_string}</root>"
                self.root = ET. fromstring(wrapped)
                self.is_valid = True
                self.parse_errors. pop()
            except:
                pass
        except Exception as e:
            self.parse_errors.append(f"解析异常: {str(e)}")
    
    def _safe_get_text(self, element: ET.Element, tag: str, default: str = '') -> str:
        """安全获取元素文本"""
        try:
            elem = element.find(tag)
            if elem is not None and elem.text:
                return str(elem.text).strip()
        except:
            pass
        return default
    
    def _parse_odds(self, value: Any) -> float:
        """解析赔率值"""
        try:
            if value is None or value == '':
                return 0.0
            v = float(str(value).strip())
            return round(v / 100 if v > 50 else v, 3)
        except:
            return 0.0
    
    def _parse_time_display(self, retime: str) -> str:
        """解析时间显示"""
        if not retime:
            return ''
        if '^' in retime:
            parts = retime.split('^')
            period_map = {'1H': '上半场', '2H': '下半场', 'HT': '中场', 'FT': '完场'}
            period = period_map.get(parts[0], parts[0])
            time_val = parts[1] if len(parts) > 1 else ''
            return f"{period} {time_val}"
        return retime
    
    def parse_matches(self) -> List[Dict]:
        """解析所有比赛，返回新结构"""
        matches = []
        
        if not self.is_valid or self.root is None:
            return matches
        
        # 查找所有game节点
        for ec in self.root.findall('. //ec'):
            game = ec.find('game')
            if game is None:
                continue
            try:
                match = self._extract_match(game)
                if match: 
                    matches.append(match)
            except Exception as e: 
                self.parse_errors.append(f"提取比赛错误: {str(e)}")
        
        # 备用方法
        if not matches:
            for game in self.root.findall('. //game'):
                try: 
                    match = self._extract_match(game)
                    if match:
                        matches.append(match)
                except Exception as e:
                    self.parse_errors.append(f"提取比赛错误: {str(e)}")
        
        return matches
    
    def _extract_match(self, game: ET.Element) -> Optional[Dict]:
        """提取单场比赛数据"""
        gid = self._safe_get_text(game, 'GID') or game.get('id', '')
        team_h = self._safe_get_text(game, 'TEAM_H')
        team_c = self._safe_get_text(game, 'TEAM_C')
        
        if not team_h and not team_c:
            return None
        
        retime = self._safe_get_text(game, 'RETIMESET')
        
        match = {
            'meta': {
                'gid': gid,
                'league': self._safe_get_text(game, 'LEAGUE', '未知联赛'),
                'team_h': team_h,
                'team_c': team_c,
                'score_h':  self._safe_get_text(game, 'SCORE_H', '0'),
                'score_c': self._safe_get_text(game, 'SCORE_C', '0'),
                'retime': retime,
                'time_display': self._parse_time_display(retime),
                'datetime': self._safe_get_text(game, 'DATETIME'),
                'strong':  self._safe_get_text(game, 'STRONG'),
                'is_running': self._safe_get_text(game, 'RUNNING') == 'Y',
                'is_rb': self._safe_get_text(game, 'IS_RB') == 'Y',
                'has_live':  self._safe_get_text(game, 'GLIVE') == 'Y',
            },
            'markets': self._extract_markets(game)
        }
        
        return match
    
    def _extract_markets(self, game: ET.Element) -> List[Dict]:
        """提取所有盘口"""
        markets = []
        
        # ===== 全场让球 RE =====
        ratio_re = self._safe_get_text(game, 'RATIO_RE')
        reh = self._parse_odds(self._safe_get_text(game, 'IOR_REH'))
        rec = self._parse_odds(self._safe_get_text(game, 'IOR_REC'))
        if ratio_re or reh > 0 or rec > 0:
            selections = []
            if reh > 0:
                selections.append({
                    'direction': 'H',
                    'chose_team': 'H',
                    'wtype':  'RE',
                    'rtype': 'REH',
                    'ioratio':  reh
                })
            if rec > 0:
                selections.append({
                    'direction': 'C',
                    'chose_team': 'C',
                    'wtype':  'RE',
                    'rtype': 'REC',
                    'ioratio':  rec
                })
            markets. append({
                'scope': 'FULL',
                'market': 'RE',
                'wtype': 'RE',
                'name': '让球',
                'handicap': [ratio_re] if ratio_re else [],
                'selections': selections
            })
        
        # ===== 全场大小 ROU =====
        ratio_rouo = self._safe_get_text(game, 'RATIO_ROUO')
        ratio_rouu = self._safe_get_text(game, 'RATIO_ROUU')
        rouh = self._parse_odds(self._safe_get_text(game, 'IOR_ROUH'))
        rouc = self._parse_odds(self._safe_get_text(game, 'IOR_ROUC'))
        handicap_ou = ratio_rouo or ratio_rouu
        if handicap_ou or rouh > 0 or rouc > 0:
            selections = []
            if rouh > 0:
                selections.append({
                    'direction': 'O',
                    'chose_team': 'H',  # 大球用H
                    'chose_team_raw': 'O',
                    'wtype':  'ROU',
                    'rtype': 'ROUH',
                    'ioratio': rouh
                })
            if rouc > 0:
                selections.append({
                    'direction': 'U',
                    'chose_team': 'C',  # 小球用C
                    'chose_team_raw': 'U',
                    'wtype':  'ROU',
                    'rtype': 'ROUC',
                    'ioratio': rouc
                })
            markets.append({
                'scope': 'FULL',
                'market': 'ROU',
                'wtype': 'ROU',
                'name': '大/小',
                'handicap':  [handicap_ou] if handicap_ou else [],
                'selections':  selections
            })
        
        # ===== 全场独赢 RM =====
        rmh = self._parse_odds(self._safe_get_text(game, 'IOR_RMH'))
        rmn = self._parse_odds(self._safe_get_text(game, 'IOR_RMN'))
        rmc = self._parse_odds(self._safe_get_text(game, 'IOR_RMC'))
        if rmh > 0 or rmn > 0 or rmc > 0:
            selections = []
            if rmh > 0:
                selections.append({
                    'direction': 'H',
                    'chose_team': 'H',
                    'wtype': 'RM',
                    'rtype': 'RMH',
                    'ioratio': rmh
                })
            if rmn > 0:
                selections.append({
                    'direction': 'N',
                    'chose_team': 'N',
                    'wtype': 'RM',
                    'rtype': 'RMN',
                    'ioratio': rmn
                })
            if rmc > 0:
                selections.append({
                    'direction': 'C',
                    'chose_team': 'C',
                    'wtype':  'RM',
                    'rtype': 'RMC',
                    'ioratio': rmc
                })
            markets.append({
                'scope': 'FULL',
                'market': 'RM',
                'wtype': 'RM',
                'name': '独赢',
                'handicap': [],
                'selections': selections
            })
        
        # ===== 半场让球 HRE =====
        ratio_hre = self._safe_get_text(game, 'RATIO_HRE')
        hreh = self._parse_odds(self._safe_get_text(game, 'IOR_HREH'))
        hrec = self._parse_odds(self._safe_get_text(game, 'IOR_HREC'))
        if ratio_hre or hreh > 0 or hrec > 0:
            selections = []
            if hreh > 0:
                selections.append({
                    'direction': 'H',
                    'chose_team': 'H',
                    'wtype': 'HRE',
                    'rtype': 'HREH',
                    'ioratio': hreh
                })
            if hrec > 0:
                selections.append({
                    'direction': 'C',
                    'chose_team': 'C',
                    'wtype': 'HRE',
                    'rtype': 'HREC',
                    'ioratio':  hrec
                })
            markets.append({
                'scope':  'HALF',
                'market': 'HRE',
                'wtype': 'HRE',
                'name': '半场让球',
                'handicap': [ratio_hre] if ratio_hre else [],
                'selections': selections
            })
        
        # ===== 半场大小 HROU =====
        ratio_hrouo = self._safe_get_text(game, 'RATIO_HROUO')
        ratio_hrouu = self._safe_get_text(game, 'RATIO_HROUU')
        hrouh = self._parse_odds(self._safe_get_text(game, 'IOR_HROUH'))
        hrouc = self._parse_odds(self._safe_get_text(game, 'IOR_HROUC'))
        handicap_hou = ratio_hrouo or ratio_hrouu
        if handicap_hou or hrouh > 0 or hrouc > 0:
            selections = []
            if hrouh > 0:
                selections. append({
                    'direction':  'O',
                    'chose_team': 'H',
                    'chose_team_raw': 'O',
                    'wtype': 'HROU',
                    'rtype': 'HROUH',
                    'ioratio': hrouh
                })
            if hrouc > 0:
                selections.append({
                    'direction': 'U',
                    'chose_team': 'C',
                    'chose_team_raw': 'U',
                    'wtype': 'HROU',
                    'rtype': 'HROUC',
                    'ioratio': hrouc
                })
            markets.append({
                'scope': 'HALF',
                'market': 'HROU',
                'wtype': 'HROU',
                'name': '半场大/小',
                'handicap':  [handicap_hou] if handicap_hou else [],
                'selections': selections
            })
        
        # ===== 半场独赢 HRM =====
        hrmh = self._parse_odds(self._safe_get_text(game, 'IOR_HRMH'))
        hrmn = self._parse_odds(self._safe_get_text(game, 'IOR_HRMN'))
        hrmc = self._parse_odds(self._safe_get_text(game, 'IOR_HRMC'))
        if hrmh > 0 or hrmn > 0 or hrmc > 0:
            selections = []
            if hrmh > 0:
                selections.append({
                    'direction': 'H',
                    'chose_team': 'H',
                    'wtype': 'HRM',
                    'rtype': 'HRMH',
                    'ioratio': hrmh
                })
            if hrmn > 0:
                selections.append({
                    'direction': 'N',
                    'chose_team': 'N',
                    'wtype': 'HRM',
                    'rtype':  'HRMN',
                    'ioratio': hrmn
                })
            if hrmc > 0:
                selections.append({
                    'direction': 'C',
                    'chose_team': 'C',
                    'wtype': 'HRM',
                    'rtype': 'HRMC',
                    'ioratio': hrmc
                })
            markets.append({
                'scope': 'HALF',
                'market': 'HRM',
                'wtype':  'HRM',
                'name': '半场独赢',
                'handicap': [],
                'selections': selections
            })
        
        # ===== 下个进球 RG =====
        rgh = self._parse_odds(self._safe_get_text(game, 'IOR_RGH'))
        rgn = self._parse_odds(self._safe_get_text(game, 'IOR_RGN'))
        rgc = self._parse_odds(self._safe_get_text(game, 'IOR_RGC'))
        if rgh > 0 or rgn > 0 or rgc > 0:
            selections = []
            if rgh > 0:
                selections.append({
                    'direction': 'H',
                    'chose_team':  'H',
                    'wtype': 'RG',
                    'rtype': 'RGH',
                    'ioratio': rgh
                })
            if rgn > 0:
                selections.append({
                    'direction': 'N',
                    'chose_team': 'N',
                    'wtype': 'RG',
                    'rtype': 'RGN',
                    'ioratio':  rgn
                })
            if rgc > 0:
                selections.append({
                    'direction': 'C',
                    'chose_team': 'C',
                    'wtype': 'RG',
                    'rtype': 'RGC',
                    'ioratio': rgc
                })
            markets.append({
                'scope': 'FULL',
                'market': 'RG',
                'wtype': 'RG',
                'name': '下个进球',
                'handicap': [],
                'selections': selections
            })
        
        # ===== 双方球队进球 RTS =====
        rtsy = self._parse_odds(self._safe_get_text(game, 'IOR_RTSY'))
        rtsn = self._parse_odds(self._safe_get_text(game, 'IOR_RTSN'))
        if rtsy > 0 or rtsn > 0:
            selections = []
            if rtsy > 0:
                selections. append({
                    'direction':  'Y',
                    'chose_team': 'H',
                    'chose_team_raw': 'Y',
                    'wtype':  'RTS',
                    'rtype': 'RTSY',
                    'ioratio': rtsy
                })
            if rtsn > 0:
                selections.append({
                    'direction': 'N',
                    'chose_team': 'C',
                    'chose_team_raw': 'N',
                    'wtype': 'RTS',
                    'rtype': 'RTSN',
                    'ioratio': rtsn
                })
            markets.append({
                'scope': 'FULL',
                'market': 'RTS',
                'wtype': 'RTS',
                'name': '双方进球',
                'handicap':  [],
                'selections': selections
            })
        
        # ===== 主队大小 ROUH/ROUC (球队独立大小) =====
        # 主队大
        ratio_rouho = self._safe_get_text(game, 'RATIO_ROUHO')
        rouho_h = self._parse_odds(self._safe_get_text(game, 'IOR_ROUHOH'))
        rouho_c = self._parse_odds(self._safe_get_text(game, 'IOR_ROUHOC'))
        if ratio_rouho or rouho_h > 0 or rouho_c > 0:
            selections = []
            if rouho_h > 0:
                selections.append({
                    'direction': 'O',
                    'chose_team': 'H',
                    'wtype': 'ROUHO',
                    'rtype': 'ROUHOH',
                    'ioratio': rouho_h
                })
            if rouho_c > 0:
                selections.append({
                    'direction': 'U',
                    'chose_team': 'C',
                    'wtype':  'ROUHO',
                    'rtype': 'ROUHOC',
                    'ioratio': rouho_c
                })
            markets.append({
                'scope':  'FULL',
                'market': 'ROUHO',
                'wtype': 'ROUHO',
                'name': '主队大/小',
                'handicap':  [ratio_rouho] if ratio_rouho else [],
                'selections': selections
            })
        
        # 客队大小
        ratio_rouco = self._safe_get_text(game, 'RATIO_ROUCO')
        rouco_h = self._parse_odds(self._safe_get_text(game, 'IOR_ROUCOH'))
        rouco_c = self._parse_odds(self._safe_get_text(game, 'IOR_ROUCOC'))
        if ratio_rouco or rouco_h > 0 or rouco_c > 0:
            selections = []
            if rouco_h > 0:
                selections.append({
                    'direction': 'O',
                    'chose_team': 'H',
                    'wtype':  'ROUCO',
                    'rtype': 'ROUCOH',
                    'ioratio': rouco_h
                })
            if rouco_c > 0:
                selections.append({
                    'direction': 'U',
                    'chose_team': 'C',
                    'wtype': 'ROUCO',
                    'rtype': 'ROUCOC',
                    'ioratio': rouco_c
                })
            markets. append({
                'scope': 'FULL',
                'market': 'ROUCO',
                'wtype': 'ROUCO',
                'name':  '客队大/小',
                'handicap': [ratio_rouco] if ratio_rouco else [],
                'selections': selections
            })
        
        return markets
    
    def get_statistics(self, matches:  List[Dict]) -> Dict:
        """获取统计信息"""
        market_count = 0
        selection_count = 0
        
        for match in matches:
            for market in match.get('markets', []):
                market_count += 1
                selection_count += len(market.get('selections', []))
        
        return {
            'match_count': len(matches),
            'market_count': market_count,
            'selection_count': selection_count
        }


# ================== XHR数据分析器 ==================
class XHRAnalyzer:
    """XHR数据分析器"""
    
    def __init__(self):
        self.matches_history = {}
        self.odds_changes = defaultdict(list)
        self.score_changes = defaultdict(list)
        self.analysis_results = {
            "last_update": None,
            "total_matches_tracked": 0,
            "total_odds_changes": 0,
            "total_score_changes": 0,
            "matches":  {},
            "alerts": []
        }
        self.lock = threading.Lock()
        self._load_existing()
    
    def _load_existing(self):
        try:
            if os.path.exists(ANALYSIS_FILE):
                with open(ANALYSIS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.analysis_results = data.get('analysis_results', self.analysis_results)
                    self.matches_history = data. get('matches_history', {})
        except: 
            pass
    
    def save(self):
        try:
            with self.lock:
                data = {
                    "analysis_results": self.analysis_results,
                    "matches_history": self.matches_history
                }
                with open(ANALYSIS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except: 
            pass
    
    def analyze_response(self, matches: List[Dict], timestamp: str = None) -> Dict:
        """分析比赛数据"""
        if not timestamp:
            timestamp = datetime.now().isoformat()
        
        results = {
            "timestamp": timestamp,
            "matches": matches,
            "odds_changes": [],
            "score_changes": [],
            "alerts": []
        }
        
        try:
            for match in matches:
                meta = match.get('meta', {})
                gid = meta.get('gid', '')
                if not gid:
                    continue
                
                # 检查比分变化
                score_change = self._check_score_change(gid, meta, timestamp)
                if score_change: 
                    results["score_changes"].append(score_change)
                    results["alerts"].append({
                        "type": "score",
                        "time": timestamp,
                        "gid": gid,
                        "message": f"⚽ 进球!  {meta['team_h']} {score_change['old_score']} → {score_change['new_score']} {meta['team_c']}"
                    })
                
                # 检查赔率变化
                odds_changes = self._check_odds_changes(gid, match, timestamp)
                for change in odds_changes:
                    results["odds_changes"].append(change)
                    if abs(change['change']) >= 0.1:
                        results["alerts"].append({
                            "type": "odds",
                            "time": timestamp,
                            "gid":  gid,
                            "message": f"📊 赔率变化 {meta['team_h']}vs{meta['team_c']} {change['market']} {change['old']:. 2f}→{change['new']:.2f}"
                        })
                
                # 保存快照
                self._save_snapshot(gid, match, timestamp)
            
            # 更新分析结果
            with self.lock:
                self. analysis_results["last_update"] = timestamp
                self.analysis_results["total_matches_tracked"] = len(self.matches_history)
                self.analysis_results["total_odds_changes"] += len(results["odds_changes"])
                self.analysis_results["total_score_changes"] += len(results["score_changes"])
                self.analysis_results["alerts"] = (results["alerts"] + self.analysis_results. get("alerts", []))[:100]
                
                for match in matches:
                    meta = match.get('meta', {})
                    gid = meta.get('gid', '')
                    if gid: 
                        self.analysis_results["matches"][gid] = {
                            "league": meta.get('league', ''),
                            "team_h": meta.get('team_h', ''),
                            "team_c": meta.get('team_c', ''),
                            "score": f"{meta. get('score_h', '0')}-{meta.get('score_c', '0')}",
                            "time": meta.get('time_display', ''),
                            "market_count": len(match.get('markets', [])),
                            "is_running": meta.get('is_running', False),
                            "last_update": timestamp
                        }
            
            self.save()
            
        except Exception as e:
            print(f"分析错误:  {e}")
        
        return results
    
    def _check_score_change(self, gid: str, meta: Dict, timestamp: str) -> Optional[Dict]:
        history = self.matches_history.get(gid, [])
        if not history:
            return None
        
        last = history[-1]. get('meta', {})
        old_score = f"{last.get('score_h', '0')}-{last.get('score_c', '0')}"
        new_score = f"{meta.get('score_h', '0')}-{meta.get('score_c', '0')}"
        
        if old_score != new_score: 
            return {
                "gid": gid,
                "timestamp": timestamp,
                "old_score": old_score,
                "new_score": new_score
            }
        return None
    
    def _check_odds_changes(self, gid: str, match: Dict, timestamp: str) -> List[Dict]:
        changes = []
        history = self.matches_history.get(gid, [])
        if not history:
            return changes
        
        last_markets = {m['market']: m for m in history[-1].get('markets', [])}
        current_markets = {m['market']: m for m in match.get('markets', [])}
        
        for market_key, current in current_markets.items():
            if market_key not in last_markets:
                continue
            
            last = last_markets[market_key]
            last_sels = {s['rtype']: s for s in last.get('selections', [])}
            
            for sel in current.get('selections', []):
                rtype = sel['rtype']
                if rtype in last_sels:
                    old_val = last_sels[rtype].get('ioratio', 0)
                    new_val = sel. get('ioratio', 0)
                    if old_val > 0 and new_val > 0 and old_val != new_val:
                        changes. append({
                            "gid": gid,
                            "timestamp": timestamp,
                            "market": market_key,
                            "rtype": rtype,
                            "old":  old_val,
                            "new": new_val,
                            "change": round(new_val - old_val, 3)
                        })
        
        return changes
    
    def _save_snapshot(self, gid: str, match: Dict, timestamp: str):
        with self.lock:
            if gid not in self.matches_history:
                self.matches_history[gid] = []
            
            snapshot = {
                'snapshot_time': timestamp,
                'meta': match.get('meta', {}),
                'markets': match. get('markets', [])
            }
            
            self.matches_history[gid]. append(snapshot)
            if len(self.matches_history[gid]) > 500: 
                self.matches_history[gid] = self.matches_history[gid][-500:]
    
    def get_match_history(self, gid: str) -> List[Dict]:
        with self.lock:
            return self.matches_history.get(gid, []).copy()
    
    def get_statistics(self) -> Dict:
        with self.lock:
            return {
                "total_matches":  len(self.matches_history),
                "total_snapshots": sum(len(v) for v in self.matches_history.values()),
                "total_odds_changes": self.analysis_results. get("total_odds_changes", 0),
                "total_score_changes": self.analysis_results.get("total_score_changes", 0),
                "last_update": self.analysis_results.get("last_update"),
                "recent_alerts": self.analysis_results.get("alerts", [])[:10]
            }
    
    def get_all_alerts(self) -> List[Dict]:
        with self.lock:
            return self.analysis_results. get("alerts", []).copy()
    
    def clear(self):
        with self.lock:
            self.matches_history = {}
            self.analysis_results = {
                "last_update": None,
                "total_matches_tracked": 0,
                "total_odds_changes":  0,
                "total_score_changes": 0,
                "matches": {},
                "alerts": []
            }
            self.save()


# ================== XHR收集器 ==================
class XHRCollector:
    """XHR请求收集器"""
    
    def __init__(self, analyzer: XHRAnalyzer = None):
        self.filename = XHR_DATA_FILE
        self.is_collecting = False
        self.collect_thread = None
        self.driver = None
        self.lock = threading.Lock()
        self.analyzer = analyzer or XHRAnalyzer()
        self.log_callback = print
        
        self.har_data = {"log": {"entries": []}}
        self. pending_requests = {}
    
    def start_collecting(self, driver, log_callback=None):
        self.driver = driver
        self.is_collecting = True
        self. log_callback = log_callback or print
        
        try:
            self.driver.execute_cdp_cmd('Network.enable', {})
            self.log_callback("✓ 网络监控已启用")
        except Exception as e:
            self.log_callback(f"⚠ 启用网络监控:  {e}")
        
        self.collect_thread = threading.Thread(target=self._collect_loop, daemon=True)
        self.collect_thread.start()
        self.log_callback("✓ XHR数据收集已启动")
    
    def stop_collecting(self):
        self.is_collecting = False
        if self.collect_thread:
            self.collect_thread.join(timeout=2)
    
    def _collect_loop(self):
        while self.is_collecting and self.driver:
            try:
                logs = self.driver.get_log('performance')
                for entry in logs: 
                    try:
                        log_data = json.loads(entry['message'])
                        message = log_data.get('message', {})
                        method = message.get('method', '')
                        params = message.get('params', {})
                        
                        if method == 'Network.requestWillBeSent':
                            self._handle_request(params)
                        elif method == 'Network.responseReceived':
                            self._handle_response(params)
                        elif method == 'Network. loadingFinished':
                            self._handle_loading_finished(params)
                    except:
                        pass
                time.sleep(0.5)
            except:
                if self.is_collecting:
                    time. sleep(1)
    
    def _handle_request(self, params):
        request_id = params.get('requestId', '')
        request = params.get('request', {})
        url = request.get('url', '')
        
        if 'transform. php' not in url:
            return
        
        self.pending_requests[request_id] = {
            "startedDateTime": datetime.now().isoformat(),
            "request": {"url": url, "postData": request.get('postData', '')},
            "response": None
        }
    
    def _handle_response(self, params):
        request_id = params.get('requestId', '')
        if request_id in self.pending_requests:
            self.pending_requests[request_id]['response'] = {"status": params.get('response', {}).get('status', 0)}
    
    def _handle_loading_finished(self, params):
        request_id = params.get('requestId', '')
        if request_id not in self.pending_requests:
            return
        
        entry = self.pending_requests[request_id]
        if entry['response'] is None:
            del self.pending_requests[request_id]
            return
        
        body = ""
        try:
            result = self.driver.execute_cdp_cmd('Network.getResponseBody', {'requestId': request_id})
            body = result.get('body', '')
            if result.get('base64Encoded', False):
                try:
                    body = base64.b64decode(body).decode('utf-8')
                except:
                    pass
        except:
            pass
        
        del self.pending_requests[request_id]
        
        post_data = entry['request']. get('postData', '')
        if body and 'get_game_list' in post_data and len(body) > 100:
            try:
                parser = RollingOddsParser(body)
                if parser.is_valid: 
                    matches = parser.parse_matches()
                    stats = parser.get_statistics(matches)
                    analysis = self.analyzer.analyze_response(matches, entry['startedDateTime'])
                    
                    running = sum(1 for m in matches if m.get('meta', {}).get('is_running'))
                    self.log_callback(f"📊 解析:  {stats['match_count']}场({running}进行中) {stats['market_count']}盘口 {stats['selection_count']}选项")
                    
                    for alert in analysis['alerts'][:2]: 
                        self.log_callback(f"   {alert['message']}")
            except Exception as e:
                self.log_callback(f"   ⚠ 分析异常: {str(e)[:50]}")
    
    def get_statistics(self) -> Dict:
        with self.lock:
            return {
                "total_requests":  len(self.har_data['log']['entries']),
                "is_collecting": self.is_collecting
            }
    
    def clear(self):
        with self. lock:
            self.har_data['log']['entries'] = []


# ================== API类 ==================
class BettingAPI:
    """投注API类"""
    
    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://mos055.com/transform.php"
        self.cookies = {}
        self.uid = ""
        self.ver = None
        self.langx = "zh-cn"
        self.session.verify = False
        
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Requested-With': 'XMLHttpRequest',
            'Origin': 'https://mos055.com',
            'Referer': 'https://mos055.com/',
        })
    
    def build_ver(self) -> str:
        return f"{datetime.now().strftime('%Y-%m-%d')}-mtfix_133"
    
    def set_cookies(self, cookies_dict: Dict):
        self.cookies = cookies_dict
        self.session.cookies.update(cookies_dict)
        
        for key in cookies_dict. keys():
            match = re.search(r'_(\d{8})(?:_|$)', key)
            if match:
                self.uid = match.group(1)
                break
        
        if not self.uid:
            for key in cookies_dict.keys():
                match = re.search(r'(\d{8})', key)
                if match: 
                    self.uid = match.group(1)
                    break
        
        self.ver = self.build_ver()
    
    def set_uid(self, uid: str):
        if uid:
            match = re.search(r'(\d{8})', str(uid))
            if match: 
                self.uid = match. group(1)
            else:
                digits = re.sub(r'\D', '', str(uid))
                if len(digits) >= 8:
                    self. uid = digits[: 8]
    
    def set_ver(self, ver: str):
        if ver:
            ver = str(ver).strip()
            if re.match(r'\d{4}-\d{2}-\d{2}-mtfix', ver):
                self.ver = ver
            elif re.match(r'\d{4}-\d{2}-\d{2}', ver):
                self.ver = f"{ver}-mtfix_133"
            else:
                self. ver = self.build_ver()
    
    def get_rolling_matches(self) -> Dict:
        """获取滚球比赛列表 - 返回新结构"""
        try:
            if not self.ver:
                self.ver = self.build_ver()
            
            data = {
                'p': 'get_game_list',
                'uid':  self.uid,
                'langx': self.langx,
                'gtype': 'FT',
                'showtype': 'live',
                'rtype': 'rb',
                'ltype': '3',
                'sorttype': 'L',
                'specialClick': '',
                'is498': 'N',
                'ts': int(time.time() * 1000)
            }
            
            response = self.session.post(
                self.base_url,
                params={'ver': self.ver},
                data=data,
                timeout=30,
                verify=False
            )
            
            if response.status_code != 200:
                return {'success': False, 'error': f'HTTP {response.status_code}', 'matches': []}
            
            xml_text = response.text
            
            if 'table id error' in xml_text. lower():
                return {'success':  False, 'error': 'table id error', 'matches': [], 'hint': f'UID: {self.uid}, ver: {self.ver}'}
            
            if xml_text.strip() == 'CheckEMNU':
                return {'success': False, 'error': 'CheckEMNU', 'matches':  []}
            
            # 使用新解析器
            parser = RollingOddsParser(xml_text)
            
            if not parser.is_valid:
                return {'success': False, 'error': f'解析失败: {"; ".join(parser.parse_errors[: 3])}', 'matches': []}
            
            matches = parser.parse_matches()
            stats = parser.get_statistics(matches)
            
            return {
                'success': True,
                'matches': matches,
                'match_count': stats['match_count'],
                'market_count': stats['market_count'],
                'selection_count': stats['selection_count'],
                'running_count': sum(1 for m in matches if m.get('meta', {}).get('is_running')),
                'parse_errors': parser.parse_errors
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e), 'matches': []}
    
    def test_connection(self) -> Dict:
        try:
            if not self.ver:
                self.ver = self.build_ver()
            
            data = {
                'p': 'get_game_list',
                'uid': self.uid,
                'showtype': 'live',
                'rtype': 'rb',
                'gtype': 'FT',
                'ltype': '3',
                'langx': self.langx,
                'ts': int(time.time() * 1000)
            }
            
            response = self.session.post(self.base_url, params={'ver': self.ver}, data=data, timeout=10, verify=False)
            text = response.text
            
            return {
                'status_code': response.status_code,
                'response_length': len(text),
                'has_game_data': '<game' in text. lower() or '<GID>' in text,
                'has_error': 'table id error' in text.lower(),
                'is_check_menu': text.strip() == 'CheckEMNU',
            }
        except Exception as e:
            return {'error': str(e)}
    
    def try_different_vers(self) -> List[Dict]:
        results = []
        today = datetime.now()
        
        for days in range(7):
            date = today - timedelta(days=days)
            ver = f"{date.strftime('%Y-%m-%d')}-mtfix_133"
            
            try: 
                data = {
                    'p': 'get_game_list',
                    'uid': self.uid,
                    'showtype': 'live',
                    'rtype': 'rb',
                    'gtype':  'FT',
                    'ltype': '3',
                    'langx': self. langx,
                    'ts': int(time.time() * 1000)
                }
                
                response = self.session.post(self.base_url, params={'ver': ver}, data=data, timeout=10, verify=False)
                text = response.text
                success = '<game' in text.lower() or '<GID>' in text
                
                results.append({'ver': ver, 'success': success, 'length': len(text)})
                
                if success:
                    self.ver = ver
                    return results
                    
            except Exception as e:
                results.append({'ver': ver, 'success': False, 'error': str(e)})
        
        return results
    
    def place_bet(self, gid: str, wtype: str, rtype: str, chose_team: str, ioratio: float, gold: float) -> Dict:
        """下注 - 直接使用 selection 中的参数"""
        try:
            data = {
                'p':  'FT_bet',
                'golds': gold,
                'gid': gid,
                'gtype': 'FT',
                'wtype': wtype,
                'rtype': rtype,
                'chose_team': chose_team,
                'ioratio':  ioratio,
                'autoOdd': 'Y',
                'isRB': 'Y',
                'uid': self.uid,
                'langx': self.langx,
                'ts': int(time.time() * 1000)
            }
            
            response = self.session.post(self.base_url, params={'ver': self.ver}, data=data, timeout=15, verify=False)
            
            if response.status_code != 200:
                return {'success': False, 'error': f'HTTP {response.status_code}'}
            
            text = response.text
            if 'success' in text.lower():
                return {'success': True, 'message': '下注成功'}
            return {'success': False, 'error': text[: 100]}
            
        except Exception as e:
            return {'success': False, 'error': str(e)}


# ================== BettingBot类 ==================
class BettingBot:
    """投注机器人核心类"""
    
    def __init__(self):
        self.driver = None
        self.is_running = False
        self.is_logged_in = False
        self. wait = None
        self.auto_bet_enabled = False
        self.bet_amount = 2
        self.bet_history = []
        self.current_matches = []
        self.odds_threshold = 1.80
        
        self.analyzer = XHRAnalyzer()
        self.xhr_collector = XHRCollector(self.analyzer)
        self.api = BettingAPI()
    
    def setup_driver(self, headless:  bool = False):
        options = webdriver.ChromeOptions()
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        options.set_capability('goog:loggingPrefs', {'performance':  'ALL', 'browser': 'ALL'})
        
        if headless:
            options.add_argument("--headless=new")
        
        self. driver = webdriver.Chrome(options=options)
        self.wait = WebDriverWait(self. driver, 60)
        
        self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined});'
        })
    
    def handle_password_popup(self, log_callback):
        for _ in range(10):
            try:
                result = self.driver.execute_script("""
                    var els = document.querySelectorAll('div, button, span, a');
                    for (var e of els) {
                        var text = e.innerText. trim();
                        if ((text === '否' || text === '取消') && e.offsetWidth > 0) {
                            e.click();
                            return true;
                        }
                    }
                    return false;
                """)
                if result:
                    log_callback("  ✓ 关闭弹窗")
                    time.sleep(1)
                else:
                    break
            except:
                pass
            time.sleep(1)
    
    def extract_uid_from_page(self, log_callback) -> Optional[str]:
        log_callback("  从cookies提取UID...")
        try:
            for c in self.driver.get_cookies():
                match = re.search(r'_(\d{8})(?:_|$)', c['name'])
                if match:
                    log_callback(f"    ✓ 找到UID: {match.group(1)}")
                    return match. group(1)
        except:
            pass
        return None
    
    def login(self, username: str, password:  str, log_callback, manual_uid: str = None) -> bool:
        try:
            log_callback("访问登录页面...")
            self.driver.get(URL)
            time.sleep(8)
            
            self.driver.execute_script(f"""
                var inputs = document.querySelectorAll('input');
                for(var i of inputs) {{ if(i.type==='text' && i.offsetWidth>0) {{ i.value='{username}'; i.dispatchEvent(new Event('input', {{bubbles: true}})); break; }} }}
            """)
            
            self.driver.execute_script(f"""
                var inputs = document. querySelectorAll('input[type="password"]');
                for(var i of inputs) {{ if(i.offsetWidth>0) {{ i.value='{password}'; i.dispatchEvent(new Event('input', {{bubbles: true}})); break; }} }}
            """)
            log_callback(f"✓ 输入凭据: {username}")
            time.sleep(1)
            
            self.driver.execute_script("""
                var btn = document.getElementById('btn_login');
                if(btn) btn.click();
                else { var els = document.querySelectorAll('button, div, span'); for(var e of els) { if((e.innerText. trim()==='登录'||e.innerText.trim()==='登入') && e.offsetWidth>0) { e.click(); break; } } }
            """)
            log_callback("✓ 点击登录")
            time.sleep(10)
            
            self.handle_password_popup(log_callback)
            time.sleep(3)
            
            log_callback("\n提取Cookies...")
            cookies = self.driver.get_cookies()
            cookies_dict = {c['name']: c['value'] for c in cookies}
            log_callback(f"获取到 {len(cookies_dict)} 个cookies")
            
            for name, value in cookies_dict.items():
                if 'GameVer' in name or 'login' in name:
                    log_callback(f"  ★ {name}: {value[: 30]}...")
            
            self. api.set_cookies(cookies_dict)
            
            if manual_uid and manual_uid.strip():
                self.api.set_uid(manual_uid. strip())
                log_callback(f"✓ 使用手动UID: {self.api.uid}")
            
            if not self.api.uid:
                uid = self.extract_uid_from_page(log_callback)
                if uid:
                    self.api.set_uid(uid)
            
            self.api.ver = self.api.build_ver()
            log_callback(f"\n当前UID: {self.api.uid or '未设置'}")
            log_callback(f"当前ver: {self.api.ver}")
            
            try:
                with open(COOKIES_FILE, "wb") as f:
                    pickle.dump(cookies, f)
            except:
                pass
            
            log_callback("\n进入滚球页面...")
            self.driver. execute_script("""
                var els = document.querySelectorAll('*');
                for(var e of els) { if(e.textContent && e.textContent.trim()==='滚球' && e.offsetWidth>0) { e.click(); break; } }
            """)
            time.sleep(5)
            
            log_callback("\n🔴 启动XHR数据收集和分析...")
            self.xhr_collector.start_collecting(self.driver, log_callback)
            log_callback("✓ 使用 RollingOddsParser 解析 (markets/selections)")
            
            log_callback("\n测试API...")
            test = self.api.test_connection()
            
            if test. get('has_game_data'):
                log_callback("  ✓ API正常!")
            elif test.get('has_error'):
                log_callback("  ⚠ table id error - 尝试不同日期...")
                for r in self.api.try_different_vers():
                    if r.get('success'):
                        log_callback(f"  ✓ 找到有效ver: {r['ver']}")
                        break
            
            self.is_logged_in = True
            log_callback("\n✓ 登录完成!")
            return True
            
        except Exception as e: 
            log_callback(f"✗ 登录失败:  {e}")
            import traceback
            log_callback(traceback.format_exc())
            return False
    
    def get_all_odds_data(self) -> Dict:
        result = self.api.get_rolling_matches()
        if result['success']:
            self.current_matches = result['matches']
            self.analyzer.analyze_response(result['matches'])
        return result
    
    def auto_bet_check(self, log_callback):
        """自动下注检查 - 使用新结构"""
        if not self. auto_bet_enabled:
            return False
        
        for match in self.current_matches:
            meta = match.get('meta', {})
            gid = meta.get('gid', '')
            
            for market in match.get('markets', []):
                for sel in market.get('selections', []):
                    ioratio = sel.get('ioratio', 0)
                    
                    if ioratio >= self.odds_threshold and ioratio < 10:
                        bet_key = f"{gid}_{sel['rtype']}_{datetime.now().strftime('%Y%m%d%H')}"
                        if bet_key in self.bet_history:
                            continue
                        
                        log_callback(f"\n🎯 触发下注!  {meta. get('team_h', '')} vs {meta.get('team_c', '')}")
                        log_callback(f"   {market['name']} {sel['direction']} @ {ioratio}")
                        
                        result = self.api.place_bet(
                            gid=gid,
                            wtype=sel['wtype'],
                            rtype=sel['rtype'],
                            chose_team=sel['chose_team'],
                            ioratio=ioratio,
                            gold=self.bet_amount
                        )
                        
                        if result['success']:
                            self. bet_history.append(bet_key)
                            log_callback("   ✓ 下注成功!")
                        else:
                            log_callback(f"   ✗ 下注失败: {result. get('error', '')}")
                        
                        return result['success']
        return False
    
    def monitor_realtime(self, interval: float, log_callback, update_callback):
        log_callback(f"\n🚀 开始监控 | 间隔:{interval}s | 阈值:{self.odds_threshold}")
        log_callback(f"   UID:{self.api.uid} | ver:{self.api.ver}")
        
        while self.is_running:
            try:
                data = self.get_all_odds_data()
                
                if data['success']:
                    update_callback(data)
                    
                    analyzer_stats = self.analyzer.get_statistics()
                    running = data. get('running_count', 0)
                    
                    log_callback(f"[{datetime.now().strftime('%H:%M:%S')}] "
                               f"{data['match_count']}场({running}进行中) | "
                               f"{data['market_count']}盘口 | "
                               f"{data['selection_count']}选项 | "
                               f"追踪:{analyzer_stats['total_matches']}")
                    
                    if self.auto_bet_enabled:
                        self.auto_bet_check(log_callback)
                else:
                    log_callback(f"[{datetime.now().strftime('%H:%M:%S')}] ✗ {data.get('error', '')[:50]}")
                
                time.sleep(interval)
                
            except Exception as e: 
                log_callback(f"✗ 监控错误: {e}")
                time.sleep(interval)
        
        log_callback("监控已停止")
    
    def stop(self):
        self.is_running = False
        self. xhr_collector.stop_collecting()
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass

# ================== GUI类 ==================
class BettingBotGUI:
    """GUI界面 - 使用 markets/selections 结构"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("滚球水位实时监控系统 v7.5 (markets/selections)")
        self.root.geometry("1950x1020")
        self.root.configure(bg='#1a1a2e')
        
        self.bot = BettingBot()
        self.monitor_thread = None
        
        self.create_widgets()
        self.load_config()
        self.update_stats()
    
    def load_config(self):
        """加载配置"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.bot.odds_threshold = config.get('threshold', 1.80)
                    self.bot.bet_amount = config.get('bet_amount', 2)
                    self.threshold_entry.delete(0, tk.END)
                    self.threshold_entry.insert(0, str(self.bot.odds_threshold))
                    self.amount_entry.delete(0, tk.END)
                    self. amount_entry.insert(0, str(self.bot.bet_amount))
                    if config.get('uid'):
                        self.uid_entry.delete(0, tk.END)
                        self. uid_entry.insert(0, config['uid'])
        except: 
            pass
    
    def save_config(self):
        """保存配置"""
        try:
            config = {
                'threshold': self.bot.odds_threshold,
                'bet_amount': self. bot.bet_amount,
                'uid': self.uid_entry.get().strip(),
                'ver': self.bot.api.ver
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except:
            pass
    
    def create_widgets(self):
        """创建界面组件"""
        # ========== 标题 ==========
        title_frame = tk.Frame(self.root, bg='#1a1a2e')
        title_frame.pack(fill='x', padx=20, pady=10)
        
        tk.Label(title_frame, text="🎯 滚球水位实时监控系统 v7.5", bg='#1a1a2e', fg='#00ff88',
                font=('Microsoft YaHei UI', 22, 'bold')).pack()
        tk.Label(title_frame, text="新结构:  markets/selections | 直接支持下注 (wtype/rtype/chose_team) | 多盘口类型",
                bg='#1a1a2e', fg='#888', font=('Microsoft YaHei UI', 10)).pack()
        
        # ========== 主容器 ==========
        main_frame = tk.Frame(self. root, bg='#1a1a2e')
        main_frame.pack(fill='both', expand=True, padx=20, pady=10)
        
        # ========== 左侧面板 ==========
        left_frame = tk.Frame(main_frame, bg='#16213e', width=450)
        left_frame.pack(side='left', fill='y', padx=(0, 10))
        left_frame.pack_propagate(False)
        
        # ----- 登录区域 -----
        login_frame = tk.LabelFrame(left_frame, text="🔐 登录", bg='#16213e',
                                   fg='#00ff88', font=('Microsoft YaHei UI', 11, 'bold'), padx=10, pady=10)
        login_frame.pack(fill='x', padx=10, pady=(10, 5))
        
        tk.Label(login_frame, text="用户名:", bg='#16213e', fg='#fff',
                font=('Microsoft YaHei UI', 10)).grid(row=0, column=0, sticky='w', pady=3)
        self.username_entry = tk.Entry(login_frame, bg='#0f3460', fg='#fff',
                                      font=('Consolas', 10), insertbackground='#fff', relief='flat', width=28)
        self.username_entry.grid(row=0, column=1, pady=3, padx=(5, 0))
        self.username_entry.insert(0, USERNAME)
        
        tk.Label(login_frame, text="密码:", bg='#16213e', fg='#fff',
                font=('Microsoft YaHei UI', 10)).grid(row=1, column=0, sticky='w', pady=3)
        self.password_entry = tk.Entry(login_frame, show="*", bg='#0f3460', fg='#fff',
                                      font=('Consolas', 10), insertbackground='#fff', relief='flat', width=28)
        self.password_entry.grid(row=1, column=1, pady=3, padx=(5, 0))
        self.password_entry.insert(0, PASSWORD)
        
        tk.Label(login_frame, text="UID:", bg='#16213e', fg='#ffaa00',
                font=('Microsoft YaHei UI', 10)).grid(row=2, column=0, sticky='w', pady=3)
        self.uid_entry = tk.Entry(login_frame, bg='#0f3460', fg='#ffaa00',
                                 font=('Consolas', 11, 'bold'), insertbackground='#fff', relief='flat', width=28)
        self.uid_entry.grid(row=2, column=1, pady=3, padx=(5, 0))
        
        tk.Label(login_frame, text="ver:", bg='#16213e', fg='#00ccff',
                font=('Microsoft YaHei UI', 10)).grid(row=3, column=0, sticky='w', pady=3)
        self.ver_entry = tk.Entry(login_frame, bg='#0f3460', fg='#00ccff',
                                 font=('Consolas', 10), insertbackground='#fff', relief='flat', width=28)
        self.ver_entry. grid(row=3, column=1, pady=3, padx=(5, 0))
        self.ver_entry.insert(0, datetime.now().strftime('%Y-%m-%d') + '-mtfix_133')
        
        btn_row = tk.Frame(login_frame, bg='#16213e')
        btn_row.grid(row=4, column=0, columnspan=2, pady=(10, 0))
        
        self.login_btn = tk.Button(btn_row, text="登录", bg='#00ff88', fg='#000',
                                  font=('Microsoft YaHei UI', 10, 'bold'), relief='flat',
                                  command=self.login, cursor='hand2', padx=20, pady=3)
        self.login_btn.pack(side='left', padx=5)
        
        self. try_ver_btn = tk.Button(btn_row, text="尝试不同日期", bg='#ff9900', fg='#000',
                                    font=('Microsoft YaHei UI', 9), relief='flat',
                                    command=self.try_different_vers, cursor='hand2', padx=10, pady=3)
        self.try_ver_btn.pack(side='left', padx=5)
        
        # ----- 数据统计 -----
        stats_frame = tk.LabelFrame(left_frame, text="📊 数据统计", bg='#16213e',
                                   fg='#ff4444', font=('Microsoft YaHei UI', 11, 'bold'), padx=10, pady=10)
        stats_frame.pack(fill='x', padx=10, pady=5)
        
        self. stats_label1 = tk.Label(stats_frame, text="比赛:  0 | 盘口: 0 | 选项: 0", bg='#16213e', fg='#aaa',
                                    font=('Microsoft YaHei UI', 10))
        self.stats_label1.pack(anchor='w')
        
        self.stats_label2 = tk.Label(stats_frame, text="追踪: 0场 | 赔率变化: 0 | 比分变化: 0",
                                    bg='#16213e', fg='#888', font=('Microsoft YaHei UI', 9))
        self.stats_label2.pack(anchor='w')
        
        self.xhr_status_label = tk.Label(stats_frame, text="XHR:  ⚪ 未启动", bg='#16213e', fg='#666',
                                        font=('Microsoft YaHei UI', 8))
        self.xhr_status_label.pack(anchor='w')
        
        stats_btn_frame = tk.Frame(stats_frame, bg='#16213e')
        stats_btn_frame. pack(fill='x', pady=(5, 0))
        
        tk.Button(stats_btn_frame, text="⚠告警", bg='#cc3333', fg='#fff',
                 font=('Microsoft YaHei UI', 9), relief='flat',
                 command=self.view_alerts, cursor='hand2', padx=6).pack(side='left', padx=(0, 3))
        
        tk.Button(stats_btn_frame, text="📈分析", bg='#336699', fg='#fff',
                 font=('Microsoft YaHei UI', 9), relief='flat',
                 command=self.view_analysis, cursor='hand2', padx=6).pack(side='left', padx=(0, 3))
        
        tk.Button(stats_btn_frame, text="📜历史", bg='#669933', fg='#fff',
                 font=('Microsoft YaHei UI', 9), relief='flat',
                 command=self.view_match_history, cursor='hand2', padx=6).pack(side='left', padx=(0, 3))
        
        tk.Button(stats_btn_frame, text="🗑清空", bg='#993333', fg='#fff',
                 font=('Microsoft YaHei UI', 9), relief='flat',
                 command=self.clear_analysis, cursor='hand2', padx=6).pack(side='left')
        
        # ----- 实时告警 -----
        alert_frame = tk.LabelFrame(left_frame, text="⚠ 实时告警", bg='#16213e',
                                   fg='#ffaa00', font=('Microsoft YaHei UI', 10, 'bold'), padx=5, pady=5)
        alert_frame.pack(fill='x', padx=10, pady=5)
        
        self. alert_text = scrolledtext.ScrolledText(alert_frame, bg='#0f3460', fg='#ffaa00',
                                                   font=('Consolas', 9), relief='flat', height=3, wrap='word')
        self.alert_text.pack(fill='x')
        
        # ----- 日志区域 -----
        log_frame = tk.LabelFrame(left_frame, text="📋 日志", bg='#16213e',
                                 fg='#888', font=('Microsoft YaHei UI', 10, 'bold'), padx=5, pady=5)
        log_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, bg='#0f3460', fg='#00ff88',
                                                 font=('Consolas', 9), relief='flat', height=6, wrap='word')
        self.log_text.pack(fill='both', expand=True)
        
        # ----- 下注设置 -----
        self.bet_frame = tk.LabelFrame(left_frame, text="💰 下注设置", bg='#16213e',
                                      fg='#ff9900', font=('Microsoft YaHei UI', 11, 'bold'), padx=10, pady=10)
        
        tk.Label(self.bet_frame, text="金额:", bg='#16213e', fg='#fff',
                font=('Microsoft YaHei UI', 10)).grid(row=0, column=0, sticky='w', pady=3)
        self.amount_entry = tk.Entry(self.bet_frame, bg='#0f3460', fg='#00ff88',
                                    font=('Consolas', 12, 'bold'), insertbackground='#fff', relief='flat', width=6)
        self.amount_entry.grid(row=0, column=1, pady=3, padx=(5, 0))
        self.amount_entry.insert(0, "2")
        
        tk. Label(self.bet_frame, text="间隔:", bg='#16213e', fg='#fff',
                font=('Microsoft YaHei UI', 10)).grid(row=0, column=2, sticky='w', pady=3, padx=(10, 0))
        self.interval_entry = tk.Entry(self.bet_frame, bg='#0f3460', fg='#fff',
                                      font=('Consolas', 12), insertbackground='#fff', relief='flat', width=4)
        self.interval_entry.grid(row=0, column=3, pady=3, padx=(5, 0))
        self.interval_entry.insert(0, "3")
        tk.Label(self.bet_frame, text="秒", bg='#16213e', fg='#888',
                font=('Microsoft YaHei UI', 9)).grid(row=0, column=4, padx=2)
        
        tk.Label(self.bet_frame, text="阈值:", bg='#16213e', fg='#fff',
                font=('Microsoft YaHei UI', 10)).grid(row=1, column=0, sticky='w', pady=3)
        self.threshold_entry = tk.Entry(self.bet_frame, bg='#0f3460', fg='#ffaa00',
                                       font=('Consolas', 12, 'bold'), insertbackground='#fff', relief='flat', width=6)
        self.threshold_entry.grid(row=1, column=1, pady=3, padx=(5, 0))
        self.threshold_entry.insert(0, "1.80")
        
        self.auto_bet_var = tk.BooleanVar(value=False)
        self.auto_bet_check = tk.Checkbutton(self. bet_frame, text="⚡自动下注",
                                            variable=self.auto_bet_var, bg='#16213e', fg='#ff4444',
                                            selectcolor='#0f3460', font=('Microsoft YaHei UI', 10, 'bold'),
                                            command=self.toggle_auto_bet)
        self.auto_bet_check.grid(row=1, column=2, columnspan=3, pady=3, sticky='w', padx=(10, 0))
        
        # ----- 控制按钮 -----
        self.control_frame = tk.Frame(left_frame, bg='#16213e')
        
        self.start_btn = tk.Button(self.control_frame, text="🚀 开始监控", bg='#0088ff',
                                  fg='#fff', font=('Microsoft YaHei UI', 12, 'bold'), relief='flat',
                                  command=self.start_monitoring, cursor='hand2', pady=8)
        self.start_btn.pack(fill='x', pady=(0, 5))
        
        self.stop_btn = tk.Button(self.control_frame, text="⏹ 停止", bg='#ff4444',
                                 fg='#fff', font=('Microsoft YaHei UI', 12, 'bold'), relief='flat',
                                 command=self.stop_monitoring, cursor='hand2', pady=8, state='disabled')
        self.stop_btn.pack(fill='x', pady=(0, 5))
        
        btn_row2 = tk.Frame(self. control_frame, bg='#16213e')
        btn_row2.pack(fill='x')
        
        self.refresh_btn = tk.Button(btn_row2, text="🔄刷新", bg='#666', fg='#fff',
                                    font=('Microsoft YaHei UI', 10), relief='flat',
                                    command=self.refresh_data, cursor='hand2', pady=4)
        self.refresh_btn.pack(side='left', fill='x', expand=True, padx=(0, 2))
        
        self.diagnose_btn = tk.Button(btn_row2, text="🔬诊断", bg='#9933ff', fg='#fff',
                                     font=('Microsoft YaHei UI', 10), relief='flat',
                                     command=self.diagnose_api, cursor='hand2', pady=4)
        self.diagnose_btn.pack(side='left', fill='x', expand=True, padx=(2, 0))
        
        # ========== 右侧数据区域 ==========
        self.right_frame = tk.Frame(main_frame, bg='#16213e')
        self.right_frame.pack(side='right', fill='both', expand=True)
        
        # 标题栏
        header_frame = tk.Frame(self.right_frame, bg='#16213e')
        header_frame.pack(fill='x', pady=(0, 5))
        
        tk.Label(header_frame, text="📊 实时水位数据 (markets/selections)", bg='#16213e',
                font=('Microsoft YaHei UI', 14, 'bold'), fg='#00ff88').pack(side='left')
        
        self.uid_label = tk.Label(header_frame, text="UID:  未设置", bg='#16213e',
                                 font=('Microsoft YaHei UI', 10, 'bold'), fg='#ff4444')
        self.uid_label.pack(side='left', padx=10)
        
        self.ver_label = tk.Label(header_frame, text="ver: 未设置", bg='#16213e',
                                 font=('Microsoft YaHei UI', 10), fg='#00ccff')
        self.ver_label.pack(side='left', padx=10)
        
        self.update_label = tk.Label(header_frame, text="", bg='#16213e',
                                    font=('Microsoft YaHei UI', 10), fg='#ffaa00')
        self.update_label.pack(side='right', padx=10)
        
        # 提示
        self.hint_label = tk.Label(self.right_frame,
                                  text="请先登录\n\nv7. 5 新结构:\n\n📦 matches[] → meta + markets[]\n📊 markets[] → scope, market, wtype, handicap, selections[]\n🎯 selections[] → direction, chose_team, wtype, rtype, ioratio\n\n✓ 直接使用 selection 参数下注",
                                  bg='#16213e', fg='#888', font=('Microsoft YaHei UI', 11), justify='center')
        self.hint_label. pack(pady=60)
        
        self.odds_canvas = None
        self.odds_inner_frame = None
        
        # 状态栏
        status_frame = tk.Frame(self.root, bg='#0f3460', height=30)
        status_frame.pack(side='bottom', fill='x')
        
        self.status_label = tk.Label(status_frame, text="状态: 未登录", bg='#0f3460',
                                    fg='#888', font=('Microsoft YaHei UI', 10), anchor='w', padx=20)
        self.status_label.pack(side='left', fill='y')
        
        self.time_label = tk.Label(status_frame, text="", bg='#0f3460',
                                  fg='#00ff88', font=('Microsoft YaHei UI', 10), anchor='e', padx=20)
        self.time_label.pack(side='right', fill='y')
    
    def update_stats(self):
        """更新统计信息"""
        try:
            xhr_stats = self.bot.xhr_collector.get_statistics()
            analyzer_stats = self.bot.analyzer. get_statistics()
            
            if xhr_stats['is_collecting']:
                self.xhr_status_label.config(text="XHR: 🔴 收集中", fg='#ff4444')
            else:
                self.xhr_status_label.config(text="XHR: ⚪ 未启动", fg='#888')
            
            self.stats_label2.config(
                text=f"追踪: {analyzer_stats['total_matches']}场 | 赔率变化: {analyzer_stats['total_odds_changes']} | 比分变化: {analyzer_stats['total_score_changes']}"
            )
            
            recent_alerts = analyzer_stats.get('recent_alerts', [])[:5]
            if recent_alerts:
                alert_text = "\n".join([f"[{a. get('time', '')[-8:]}] {a.get('message', '')[:50]}" for a in recent_alerts])
                self.alert_text.delete('1.0', tk.END)
                self.alert_text. insert('1.0', alert_text)
        except: 
            pass
        
        self.root.after(2000, self.update_stats)
    
    def view_alerts(self):
        """查看告警"""
        alerts = self.bot.analyzer.get_all_alerts()
        
        win = tk.Toplevel(self. root)
        win.title("⚠ 告警记录")
        win.geometry("900x600")
        win.configure(bg='#1a1a2e')
        
        tk.Label(win, text=f"⚠ 告警记录 ({len(alerts)}条)", bg='#1a1a2e', fg='#ffaa00',
                font=('Microsoft YaHei UI', 14, 'bold')).pack(pady=10)
        
        text = scrolledtext.ScrolledText(win, bg='#0f3460', fg='#00ff88', font=('Consolas', 10), wrap='word')
        text.pack(fill='both', expand=True, padx=20, pady=10)
        
        for alert in alerts:
            text.insert('end', f"[{alert. get('time', '')[-19:]}] {alert.get('message', '')}\n")
        
        tk. Button(win, text="关闭", bg='#666', fg='#fff', command=win.destroy).pack(pady=10)
    
    def view_analysis(self):
        """查看分析数据"""
        stats = self.bot.analyzer.get_statistics()
        results = self.bot.analyzer.analysis_results
        
        win = tk.Toplevel(self. root)
        win.title("📈 分析数据")
        win.geometry("1100x750")
        win.configure(bg='#1a1a2e')
        
        tk.Label(win, text="📈 分析数据 (markets/selections结构)", bg='#1a1a2e', fg='#00ff88',
                font=('Microsoft YaHei UI', 14, 'bold')).pack(pady=10)
        
        text = scrolledtext.ScrolledText(win, bg='#0f3460', fg='#00ff88', font=('Consolas', 9), wrap='none')
        text.pack(fill='both', expand=True, padx=20, pady=10)
        
        try:
            display = {
                "statistics": stats,
                "matches": results. get('matches', {}),
                "recent_alerts": results.get('alerts', [])[:20]
            }
            text.insert('1.0', json.dumps(display, ensure_ascii=False, indent=2))
        except Exception as e:
            text.insert('1.0', f"加载失败: {e}")
        
        tk.Button(win, text="关闭", bg='#666', fg='#fff', command=win.destroy).pack(pady=10)
    
    def view_match_history(self):
        """查看比赛历史"""
        win = tk.Toplevel(self.root)
        win.title("📜 比赛历史")
        win.geometry("1200x800")
        win.configure(bg='#1a1a2e')
        
        tk.Label(win, text="📜 比赛历史 (含markets快照)", bg='#1a1a2e', fg='#00ff88',
                font=('Microsoft YaHei UI', 14, 'bold')).pack(pady=10)
        
        select_frame = tk.Frame(win, bg='#1a1a2e')
        select_frame.pack(fill='x', padx=20)
        
        tk.Label(select_frame, text="选择比赛:", bg='#1a1a2e', fg='#fff',
                font=('Microsoft YaHei UI', 10)).pack(side='left')
        
        matches = self.bot.analyzer.analysis_results.get('matches', {})
        match_list = [f"{gid}:  {info. get('team_h', '')} vs {info.get('team_c', '')} ({info.get('score', '')})"
                     for gid, info in matches.items()]
        
        combo = ttk.Combobox(select_frame, values=match_list, width=70)
        combo.pack(side='left', padx=10)
        
        history_text = scrolledtext.ScrolledText(win, bg='#0f3460', fg='#00ff88', font=('Consolas', 9), wrap='word')
        history_text.pack(fill='both', expand=True, padx=20, pady=10)
        
        def show_history():
            selection = combo.get()
            if not selection:
                return
            gid = selection.split(':')[0]
            history = self.bot.analyzer.get_match_history(gid)
            
            history_text.delete('1.0', tk.END)
            history_text.insert('end', f"比赛 {gid} 历史 ({len(history)}条快照)\n\n")
            
            for snap in history[-30:]:
                meta = snap.get('meta', {})
                history_text.insert('end', f"[{snap.get('snapshot_time', '')[-19:]}] ")
                history_text.insert('end', f"{meta.get('score_h', '0')}-{meta.get('score_c', '0')} {meta.get('time_display', '')}\n")
                
                for market in snap.get('markets', [])[:4]:
                    handicap = market.get('handicap', [''])[0] if market.get('handicap') else ''
                    sels = market.get('selections', [])
                    sel_str = ' | '.join([f"{s['direction']}:{s['ioratio']:. 2f}" for s in sels])
                    history_text. insert('end', f"  {market['name']} {handicap}:  {sel_str}\n")
                history_text.insert('end', "\n")
        
        tk. Button(select_frame, text="查看", bg='#336699', fg='#fff', command=show_history).pack(side='left', padx=5)
        tk.Button(win, text="关闭", bg='#666', fg='#fff', command=win.destroy).pack(pady=10)
    
    def clear_analysis(self):
        """清空分析数据"""
        if messagebox.askyesno("确认", "确定要清空所有分析数据吗？"):
            self.bot.analyzer.clear()
            self.bot.xhr_collector.clear()
            self.log("✓ 分析数据已清空")
    
    def try_different_vers(self):
        """尝试不同日期的ver"""
        def try_vers():
            self.log("\n尝试不同日期的ver...")
            manual_uid = self.uid_entry.get().strip()
            if manual_uid:
                self.bot.api. set_uid(manual_uid)
            if not self.bot.api.uid:
                self.log("✗ 请先输入UID")
                return
            
            for r in self.bot.api.try_different_vers():
                status = "✓" if r.get('success') else "✗"
                self.log(f"  {status} {r['ver']}")
                if r. get('success'):
                    self.root.after(0, lambda v=r['ver']: (
                        self.ver_entry.delete(0, tk.END),
                        self.ver_entry.insert(0, v),
                        self.ver_label.config(text=f"ver: {v}", fg='#00ff88')
                    ))
                    self.log(f"\n✓ 找到有效ver: {r['ver']}")
                    break
            else:
                self.log("\n✗ 所有日期都失败")
        
        threading.Thread(target=try_vers, daemon=True).start()
    
    def create_odds_display_area(self, parent):
        """创建水位显示区域"""
        if self.hint_label:
            self.hint_label.pack_forget()
        
        if self.odds_canvas:
            self.odds_canvas.master.destroy()
        
        canvas_frame = tk.Frame(parent, bg='#16213e')
        canvas_frame.pack(fill='both', expand=True)
        
        self.odds_canvas = tk.Canvas(canvas_frame, bg='#0f3460', highlightthickness=0)
        scrollbar_y = tk.Scrollbar(canvas_frame, orient='vertical', command=self.odds_canvas.yview)
        scrollbar_x = tk.Scrollbar(canvas_frame, orient='horizontal', command=self.odds_canvas.xview)
        
        self. odds_inner_frame = tk.Frame(self.odds_canvas, bg='#0f3460')
        
        self.odds_canvas.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        
        scrollbar_y.pack(side='right', fill='y')
        scrollbar_x.pack(side='bottom', fill='x')
        self.odds_canvas.pack(side='left', fill='both', expand=True)
        
        self.canvas_window = self.odds_canvas.create_window((0, 0), window=self.odds_inner_frame, anchor='nw')
        
        self.odds_inner_frame.bind('<Configure>', lambda e: self.odds_canvas. configure(scrollregion=self. odds_canvas.bbox('all')))
        self.odds_canvas.bind('<Configure>', lambda e: self.odds_canvas.itemconfig(self.canvas_window, width=e.width))
        self.odds_canvas.bind_all('<MouseWheel>', lambda e: self.odds_canvas.yview_scroll(int(-1*(e.delta/120)), 'units'))
    
    def update_odds_display(self, data):
        """更新水位显示 - 使用 markets/selections 结构"""
        def update():
            try:
                if not self.odds_inner_frame:
                    self.create_odds_display_area(self.right_frame)
                
                matches = data.get('matches', [])
                timestamp = datetime.now().strftime('%H:%M:%S')
                
                self.time_label.config(text=f"更新:  {timestamp}")
                self.update_label.config(text=f"🔄 {timestamp}", fg='#00ff88')
                
                # 更新统计
                self.stats_label1.config(
                    text=f"比赛: {data.get('match_count', 0)} | 盘口: {data.get('market_count', 0)} | 选项: {data.get('selection_count', 0)}"
                )
                
                uid = self.bot.api.uid
                ver = self.bot.api.ver
                self.uid_label.config(text=f"UID: {uid}" if uid else "UID: 未设置",
                                     fg='#00ff88' if uid else '#ff4444')
                self.ver_label.config(text=f"ver: {ver}" if ver else "ver: 未设置",
                                     fg='#00ff88' if ver and 'mtfix' in ver else '#ff4444')
                
                for widget in self.odds_inner_frame.winfo_children():
                    widget.destroy()
                
                if not matches:
                    error = data.get('error', '')
                    if error:
                        tk.Label(self.odds_inner_frame, text=f"❌ {error[: 100]}",
                                bg='#0f3460', fg='#ff4444', font=('Microsoft YaHei UI', 11), wraplength=800).pack(pady=10)
                        if data.get('hint'):
                            tk.Label(self.odds_inner_frame, text=f"💡 {data['hint']}",
                                    bg='#0f3460', fg='#ffaa00', font=('Microsoft YaHei UI', 10)).pack(pady=5)
                    else:
                        tk. Label(self.odds_inner_frame, text="暂无滚球比赛数据",
                                bg='#0f3460', fg='#888', font=('Microsoft YaHei UI', 11)).pack(pady=20)
                    return
                
                # 显示统计
                running = data.get('running_count', 0)
                tk.Label(self.odds_inner_frame,
                        text=f"共 {data['match_count']} 场比赛 ({running}进行中) | {data['market_count']} 盘口 | {data['selection_count']} 选项",
                        bg='#0f3460', fg='#00ff88', font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w', padx=10, pady=5)
                
                current_league = ''
                threshold = self.bot.odds_threshold
                
                # 定义要显示的盘口类型顺序
                display_markets = ['RE', 'ROU', 'RM', 'HRE', 'HROU', 'HRM', 'RG', 'RTS']
                
                for match in matches:
                    meta = match.get('meta', {})
                    markets = match.get('markets', [])
                    
                    league = meta.get('league', '未知联赛')
                    team_h = meta.get('team_h', '')
                    team_c = meta.get('team_c', '')
                    score_h = meta.get('score_h', '0')
                    score_c = meta.get('score_c', '0')
                    time_display = meta.get('time_display', '')
                    gid = meta.get('gid', '')
                    is_running = meta.get('is_running', False)
                    
                    if league and league != current_league:
                        league_frame = tk.Frame(self.odds_inner_frame, bg='#2d2d44')
                        league_frame. pack(fill='x', pady=(15, 5), padx=5)
                        tk.Label(league_frame, text=f"🏆 {league}", bg='#2d2d44', fg='#ffaa00',
                                font=('Microsoft YaHei UI', 12, 'bold'), pady=5).pack(anchor='w', padx=10)
                        current_league = league
                    
                    match_frame = tk.Frame(self.odds_inner_frame, bg='#1e1e32', bd=1, relief='solid')
                    match_frame. pack(fill='x', padx=5, pady=3)
                    
                    # 构建markets字典
                    markets_dict = {m['market']: m for m in markets}
                    
                    status_icon = "🔴" if is_running else "⚪"
                    
                    # 信息行
                    info_frame = tk.Frame(match_frame, bg='#1e1e32')
                    info_frame.pack(fill='x', pady=(5, 2), padx=5)
                    
                    tk.Label(info_frame, text=f"{status_icon} {time_display} [ID:{gid}] 📊{len(markets)}",
                            bg='#1e1e32', fg='#888', font=('Microsoft YaHei UI', 8), width=30, anchor='w').pack(side='left')
                    
                    # 盘口标题
                    for mk in display_markets:
                        market = markets_dict.get(mk, {})
                        handicap = market.get('handicap', [''])[0] if market.get('handicap') else ''
                        name = MARKET_NAMES.get(mk, mk)
                        header = f"{name}\n{handicap}" if handicap else name
                        tk.Label(info_frame, text=header, bg='#1e1e32', fg='#aaa',
                                font=('Microsoft YaHei UI', 8), width=10, anchor='center').pack(side='left', padx=1)
                    
                    # 主队行
                    team1_frame = tk.Frame(match_frame, bg='#1e1e32')
                    team1_frame.pack(fill='x', pady=2, padx=5)
                    
                    s_color = '#ff4444' if score_h. isdigit() and int(score_h) > 0 else '#fff'
                    tk.Label(team1_frame, text=score_h or '0', bg='#1e1e32', fg=s_color,
                            font=('Microsoft YaHei UI', 11, 'bold'), width=3).pack(side='left')
                    
                    strong_mark = " ⭐" if meta.get('strong') == 'H' else ""
                    t1_display = (team_h[: 18] + '. .' if len(team_h) > 20 else team_h) + strong_mark
                    tk.Label(team1_frame, text=t1_display, bg='#1e1e32', fg='#fff',
                            font=('Microsoft YaHei UI', 9), width=22, anchor='w').pack(side='left')
                    
                    for mk in display_markets:
                        cell = tk.Frame(team1_frame, bg='#1e1e32', width=80)
                        cell.pack(side='left', padx=1)
                        cell.pack_propagate(False)
                        
                        market = markets_dict.get(mk, {})
                        selections = market.get('selections', [])
                        
                        # 找主队/大球选项
                        home_sel = None
                        for sel in selections:
                            if sel.get('direction') in ['H', 'O', 'Y']:
                                home_sel = sel
                                break
                        
                        inner = tk.Frame(cell, bg='#1e1e32')
                        inner.pack(expand=True)
                        
                        if home_sel:
                            val = home_sel['ioratio']
                            color = '#ff4444' if val >= threshold else '#00ff88'
                            
                            # 可点击下注
                            btn = tk.Label(inner, text=f"{val:.2f}", bg='#1e1e32', fg=color,
                                          font=('Consolas', 10, 'bold'), cursor='hand2')
                            btn.pack()
                            btn.bind('<Button-1>', lambda e, g=gid, s=home_sel, m=meta: self.on_odds_click(g, s, m))
                        else:
                            tk.Label(inner, text="-", bg='#1e1e32', fg='#444', font=('Consolas', 10)).pack()
                    
                    # 和局行 (独赢盘口)
                    draw_markets = ['RM', 'HRM', 'RG']
                    has_draw = any(
                        any(s.get('direction') == 'N' for s in markets_dict.get(mk, {}).get('selections', []))
                        for mk in draw_markets
                    )
                    
                    if has_draw:
                        draw_frame = tk.Frame(match_frame, bg='#1e1e32')
                        draw_frame.pack(fill='x', pady=1, padx=5)
                        
                        tk.Label(draw_frame, text="", bg='#1e1e32', width=3).pack(side='left')
                        tk.Label(draw_frame, text="和局", bg='#1e1e32', fg='#aaa',
                                font=('Microsoft YaHei UI', 9), width=22, anchor='w').pack(side='left')
                        
                        for mk in display_markets:
                            cell = tk.Frame(draw_frame, bg='#1e1e32', width=80)
                            cell.pack(side='left', padx=1)
                            cell.pack_propagate(False)
                            
                            market = markets_dict.get(mk, {})
                            selections = market.get('selections', [])
                            
                            draw_sel = None
                            for sel in selections:
                                if sel.get('direction') == 'N':
                                    draw_sel = sel
                                    break
                            
                            inner = tk.Frame(cell, bg='#1e1e32')
                            inner.pack(expand=True)
                            
                            if draw_sel:
                                val = draw_sel['ioratio']
                                color = '#ff4444' if val >= threshold else '#00ccff'
                                btn = tk.Label(inner, text=f"{val:.2f}", bg='#1e1e32', fg=color,
                                              font=('Consolas', 10, 'bold'), cursor='hand2')
                                btn.pack()
                                btn. bind('<Button-1>', lambda e, g=gid, s=draw_sel, m=meta:  self.on_odds_click(g, s, m))
                            else:
                                tk. Label(inner, text="", bg='#1e1e32', font=('Consolas', 10)).pack()
                    
                    # 客队行
                    team2_frame = tk.Frame(match_frame, bg='#1e1e32')
                    team2_frame.pack(fill='x', pady=(0, 5), padx=5)
                    
                    s_color = '#ff4444' if score_c.isdigit() and int(score_c) > 0 else '#fff'
                    tk.Label(team2_frame, text=score_c or '0', bg='#1e1e32', fg=s_color,
                            font=('Microsoft YaHei UI', 11, 'bold'), width=3).pack(side='left')
                    
                    strong_mark = " ⭐" if meta.get('strong') == 'C' else ""
                    t2_display = (team_c[:18] + '..' if len(team_c) > 20 else team_c) + strong_mark
                    tk. Label(team2_frame, text=t2_display, bg='#1e1e32', fg='#fff',
                            font=('Microsoft YaHei UI', 9), width=22, anchor='w').pack(side='left')
                    
                    for mk in display_markets: 
                        cell = tk.Frame(team2_frame, bg='#1e1e32', width=80)
                        cell.pack(side='left', padx=1)
                        cell.pack_propagate(False)
                        
                        market = markets_dict.get(mk, {})
                        selections = market.get('selections', [])
                        
                        # 找客队/小球选项
                        away_sel = None
                        for sel in selections:
                            if sel.get('direction') in ['C', 'U', 'N'] and sel.get('direction') != 'N':
                                away_sel = sel
                                break
                            if sel.get('direction') == 'C':
                                away_sel = sel
                                break
                        
                        # 如果没找到C，找U
                        if not away_sel:
                            for sel in selections:
                                if sel.get('direction') == 'U':
                                    away_sel = sel
                                    break
                        
                        inner = tk. Frame(cell, bg='#1e1e32')
                        inner.pack(expand=True)
                        
                        if away_sel:
                            val = away_sel['ioratio']
                            color = '#ff4444' if val >= threshold else '#ffaa00'
                            btn = tk.Label(inner, text=f"{val:.2f}", bg='#1e1e32', fg=color,
                                          font=('Consolas', 10, 'bold'), cursor='hand2')
                            btn.pack()
                            btn. bind('<Button-1>', lambda e, g=gid, s=away_sel, m=meta:  self.on_odds_click(g, s, m))
                        else:
                            tk. Label(inner, text="-", bg='#1e1e32', fg='#444', font=('Consolas', 10)).pack()
                
                self.odds_inner_frame.update_idletasks()
                self. odds_canvas.configure(scrollregion=self.odds_canvas.bbox('all'))
                
            except Exception as e:
                print(f"显示错误: {e}")
                import traceback
                traceback.print_exc()
        
        self.root.after(0, update)
    
    def on_odds_click(self, gid:  str, selection: Dict, meta: Dict):
        """点击赔率下注"""
        team_h = meta.get('team_h', '')
        team_c = meta.get('team_c', '')
        ioratio = selection['ioratio']
        wtype = selection['wtype']
        rtype = selection['rtype']
        chose_team = selection['chose_team']
        direction = selection['direction']
        
        dir_name = {'H': '主队', 'C': '客队', 'N': '和局', 'O': '大', 'U': '小', 'Y': '是', 'N':  '否'}. get(direction, direction)
        market_name = MARKET_NAMES.get(wtype, wtype)
        
        msg = f"确认下注?\n\n{team_h} vs {team_c}\n{market_name} {dir_name}\n赔率:  {ioratio}\n金额: {self.bot.bet_amount}\n\nwtype: {wtype}\nrtype: {rtype}\nchose_team: {chose_team}"
        
        if messagebox.askyesno("确认下注", msg):
            def do_bet():
                self.log(f"🎯 下注: {team_h} vs {team_c} | {market_name} {dir_name} @ {ioratio}")
                result = self.bot.api.place_bet(
                    gid=gid,
                    wtype=wtype,
                    rtype=rtype,
                    chose_team=chose_team,
                    ioratio=ioratio,
                    gold=self.bot.bet_amount
                )
                if result['success']:
                    self.log("   ✓ 下注成功!")
                else:
                    self.log(f"   ✗ 下注失败: {result. get('error', '')}")
            
            threading.Thread(target=do_bet, daemon=True).start()
    
    def log(self, message):
        """写日志"""
        def update_log():
            ts = datetime.now().strftime('%H:%M:%S')
            self.log_text.insert('end', f"[{ts}] {message}\n")
            self.log_text.see('end')
            lines = int(self.log_text.index('end-1c').split('.')[0])
            if lines > 500:
                self.log_text.delete('1.0', '200.0')
        self.root.after(0, update_log)
    
    def toggle_auto_bet(self):
        """切换自动下注"""
        if self.auto_bet_var.get():
            if messagebox.askyesno("确认", f"启用自动下注?\n水位≥{self.threshold_entry.get()}时下注{self.amount_entry.get()}RMB"):
                self.bot.auto_bet_enabled = True
                self.bot.odds_threshold = float(self.threshold_entry.get())
                self.bot.bet_amount = float(self.amount_entry.get())
                self.save_config()
                self.log("⚡ 自动下注已启用!")
            else:
                self.auto_bet_var.set(False)
        else:
            self.bot.auto_bet_enabled = False
            self.log("自动下注已关闭")
    
    def login(self):
        """登录"""
        username = self.username_entry.get()
        password = self.password_entry.get()
        manual_uid = self.uid_entry.get().strip()
        
        if not username or not password:
            messagebox.showerror("错误", "请输入用户名和密码")
            return
        
        self.login_btn.config(state='disabled', text="登录中...")
        self.status_label.config(text="状态: 登录中.. .", fg='#ffaa00')
        
        def login_thread():
            try:
                self.bot.setup_driver(headless=False)
                success = self.bot.login(username, password, self.log, manual_uid)
                
                def update_ui():
                    if success: 
                        self.status_label. config(text="状态: 已登录", fg='#00ff88')
                        self.login_btn.config(text="✓ 已登录", state='disabled')
                        self.bet_frame.pack(fill='x', padx=10, pady=5)
                        self.control_frame.pack(fill='x', padx=10, pady=10)
                        
                        if self.bot.api.uid:
                            self.uid_entry.delete(0, tk.END)
                            self.uid_entry. insert(0, self.bot. api.uid)
                            self.uid_label.config(text=f"UID: {self.bot.api.uid}", fg='#00ff88')
                        
                        if self.bot.api.ver:
                            self.ver_entry.delete(0, tk.END)
                            self. ver_entry.insert(0, self.bot.api.ver)
                            self.ver_label.config(text=f"ver: {self.bot.api. ver}", fg='#00ff88')
                        
                        self.create_odds_display_area(self.right_frame)
                        self. save_config()
                        self. refresh_data()
                    else:
                        self.status_label.config(text="状态: 登录失败", fg='#ff4444')
                        self.login_btn. config(state='normal', text="登录")
                
                self.root.after(0, update_ui)
            except Exception as e:
                self.log(f"登录异常: {e}")
                self.root.after(0, lambda: self.login_btn.config(state='normal', text="登录"))
        
        threading.Thread(target=login_thread, daemon=True).start()
    
    def start_monitoring(self):
        """开始监控"""
        manual_uid = self.uid_entry.get().strip()
        manual_ver = self.ver_entry.get().strip()
        
        if manual_uid: 
            self.bot.api.set_uid(manual_uid)
        if manual_ver:
            self. bot.api.set_ver(manual_ver)
        
        if not self.bot.api.uid or len(self.bot.api.uid) < 6:
            messagebox.showwarning("警告", "请输入有效的UID!")
            return
        
        try:
            interval = float(self.interval_entry.get())
            self.bot.bet_amount = float(self.amount_entry.get())
            self.bot.odds_threshold = float(self.threshold_entry.get())
        except ValueError:
            messagebox. showerror("错误", "请输入有效数字")
            return
        
        self.bot.auto_bet_enabled = self.auto_bet_var.get()
        self.bot.is_running = True
        self.save_config()
        
        self.start_btn.config(state='disabled')
        self.stop_btn.config(state='normal')
        self.status_label.config(text="状态:  监控中", fg='#00ff88')
        
        self.monitor_thread = threading.Thread(
            target=self.bot.monitor_realtime,
            args=(interval, self.log, self.update_odds_display),
            daemon=True
        )
        self.monitor_thread.start()
    
    def stop_monitoring(self):
        """停止监控"""
        self.bot.is_running = False
        self.start_btn.config(state='normal')
        self.stop_btn.config(state='disabled')
        self.status_label.config(text="状态: 已停止", fg='#ffaa00')
        self.log("监控已停止")
    
    def refresh_data(self):
        """刷新数据"""
        manual_uid = self.uid_entry.get().strip()
        manual_ver = self.ver_entry.get().strip()
        
        if manual_uid:
            self.bot.api.set_uid(manual_uid)
        if manual_ver:
            self.bot.api.set_ver(manual_ver)
        
        def refresh():
            self.log("刷新数据...")
            self.log(f"UID: {self. bot.api.uid}, ver: {self.bot.api. ver}")
            self.root.after(0, lambda: self.update_label. config(text="🔄 刷新中.. .", fg='#ffaa00'))
            
            data = self.bot.get_all_odds_data()
            self.update_odds_display(data)
            
            if data['success']:
                running = data. get('running_count', 0)
                self. log(f"✓ {data['match_count']}场({running}进行中) | {data['market_count']}盘口 | {data['selection_count']}选项")
            else:
                self.log(f"❌ 失败: {data.get('error', '')[:60]}")
        
        threading.Thread(target=refresh, daemon=True).start()
    
    def diagnose_api(self):
        """API诊断"""
        def diagnose():
            self.log("\n" + "="*50)
            self.log("🔬 API诊断 v7.5 (markets/selections)")
            self.log("="*50)
            
            self.log(f"\n【UID】 {self.bot.api.uid or '未设置'}")
            self.log(f"【ver】 {self.bot.api.ver or '未设置'}")
            
            analyzer_stats = self.bot.analyzer.get_statistics()
            self.log(f"\n【数据分析】")
            self.log(f"  追踪比赛: {analyzer_stats['total_matches']}")
            self.log(f"  赔率变化: {analyzer_stats['total_odds_changes']}")
            
            self.log(f"\n【测试请求】")
            test = self.bot.api.test_connection()
            if test. get('error'):
                self.log(f"❌ 错误: {test['error'][: 60]}")
            else:
                self.log(f"状态码: {test['status_code']}")
                if test. get('has_game_data'):
                    self.log("✓ API正常!")
                elif test.get('has_error'):
                    self.log("⚠ table id error")
            
            self.log("\n" + "="*50)
        
        threading.Thread(target=diagnose, daemon=True).start()
    
    def on_closing(self):
        """关闭"""
        if messagebox.askokcancel("退出", "确定退出? "):
            self.save_config()
            self.bot.stop()
            self.root.destroy()


# ================== 主程序 ==================
if __name__ == "__main__":
    root = tk. Tk()
    app = BettingBotGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
