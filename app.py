import os
import random
import time
import re
from datetime import datetime, timedelta
import pandas as pd
import eng_to_ipa as ipa
import streamlit as st
import firebase_admin
from firebase_admin import credentials, db
import requests

# ==========================================
# 🚀 雲端資料庫 Firebase 連線初始化
# ==========================================
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        try:
            cert_dict = dict(st.secrets["firebase"])
            db_url = cert_dict.pop("databaseURL")
            cert_dict["private_key"] = cert_dict["private_key"].replace('\\n', '\n')
            
            cred = credentials.Certificate(cert_dict)
            firebase_admin.initialize_app(cred, {
                'databaseURL': db_url
            })
        except Exception as e:
            st.error(f"🚨 Firebase 初始化失敗！請檢查 Streamlit Secrets 設定是否正確。錯誤訊息：{e}")
            st.stop()
    return True

init_firebase()

# ==========================================
# 🧬 寶可夢全圖鑑動態生成 (1~649) 與階段設定
# ==========================================
# 階段對應最大 ID (分做6個階段)
STAGE_MAX_IDS = {1: 151, 2: 251, 3: 386, 4: 493, 5: 570, 6: 649} 
# 傳說/幻之寶可夢 ID (將作為 Boss)
LEGENDARY_IDS = {144,145,146,150,151, 243,244,245,249,250,251, 377,378,379,380,381,382,383,384,385,386, 
                 480,481,482,483,484,485,486,487,488,489,490,491,492,493,494, 638,639,640,641,642,643,644,645,646,647,648,649}

@st.cache_data(ttl=86400)
def get_pokemon_names():
    try:
        # 使用開源資料獲取繁體中文名稱快取
        return requests.get("https://raw.githubusercontent.com/sindresorhus/pokemon/main/data/zh-hant.json").json()
    except:
        return []

POKE_NAMES = get_pokemon_names()

def get_poke_name(p_id):
    if p_id - 1 < len(POKE_NAMES): return POKE_NAMES[p_id - 1]
    return f"未知名稱 #{p_id}"

def get_poke_url(p_id):
    return f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/versions/generation-v/black-white/animated/{p_id}.gif"

def spawn_enemy(unlocked_stage, is_boss=False):
    max_id = STAGE_MAX_IDS.get(unlocked_stage, 151)
    if is_boss:
        valid_ids = [i for i in LEGENDARY_IDS if i <= max_id]
        if not valid_ids: valid_ids = [150] # 備用防錯
    else:
        valid_ids = [i for i in range(1, max_id + 1) if i not in LEGENDARY_IDS]
    
    p_id = random.choice(valid_ids)
    return {"id": p_id, "name": get_poke_name(p_id), "url": get_poke_url(p_id)}

# --- 寶可夢角色與屬性設定 ---
EMOJI_LIST = ["🎮", "🧸", "🎲", "🧩", "🎯", "🪀", "🪁", "🚂", "🍔", "🍟", "🍕", "🍦", "🍩", "🍫", "🍬", "🍿", "🥤", "🧋", "👑", "🏆", "🥇", "⭐", "💰", "💎", "🐬", "🎬", "🚲", "⚽", "🏀", "🏊", "⛺", "🚀", "📖", "🖍️", "🎨", "🎒"]

CHARACTERS = {
    "電系 (皮丘)": {"stages": [(172, "皮丘"), (25, "皮卡丘"), (26, "雷丘")], "fx": "⚡", "snd_type": "square", "snd_freq": 1200, "snd_drop": 200, "snd_len": 0.15},
    "火系 (小火龍)": {"stages": [(4, "小火龍"), (5, "火恐龍"), (6, "噴火龍")], "fx": "🔥", "snd_type": "sawtooth", "snd_freq": 300, "snd_drop": 50, "snd_len": 0.4},
    "水系 (傑尼龜)": {"stages": [(7, "傑尼龜"), (8, "卡咪龜"), (9, "水箭龜")], "fx": "💦", "snd_type": "sine", "snd_freq": 600, "snd_drop": 100, "snd_len": 0.25},
    "草系 (妙蛙種子)": {"stages": [(1, "妙蛙種子"), (2, "妙蛙草"), (3, "妙蛙花")], "fx": "🍃", "snd_type": "triangle", "snd_freq": 900, "snd_drop": 400, "snd_len": 0.1}
}

HURT_SOUNDS = [
    {"type": "sawtooth", "f1": 150, "f2": 40, "len": 0.3},
    {"type": "square", "f1": 200, "f2": 80, "len": 0.2},
    {"type": "triangle", "f1": 100, "f2": 20, "len": 0.4}
]

# ==========================================
# ☁️ 核心 API (Firebase Realtime DB) 與預設數值
# ==========================================
DEFAULT_RATES = {
    "簡單": {"normal_exp": 5, "normal_gold": 10, "boss_exp": 10, "boss_gold": 50, "boss_medal": 1},
    "中等": {"normal_exp": 10, "normal_gold": 20, "boss_exp": 20, "boss_gold": 100, "boss_medal": 2},
    "困難": {"normal_exp": 15, "normal_gold": 30, "boss_exp": 30, "boss_gold": 150, "boss_medal": 3}
}
DEFAULT_GACHA = {
    "cost": 300,
    "prizes": [
        {"name": "特獎：榮耀大禮包 (3勳章)", "prob": 1, "type": "medal", "val": 3},
        {"name": "一獎：1枚榮耀勳章", "prob": 4, "type": "medal", "val": 1},
        {"name": "二獎：豪華道具包 (各x2)", "prob": 10, "type": "item", "val": 2},
        {"name": "三獎：金幣大暴發 (500G)", "prob": 20, "type": "gold", "val": 500},
        {"name": "四獎：實用道具包 (各x1)", "prob": 30, "type": "item", "val": 1},
        {"name": "五獎：安慰小紅包 (100G)", "prob": 35, "type": "gold", "val": 100}
    ]
}
DEFAULT_STORE = {"potion": 200, "shield": 250, "magnifier": 100, "scroll": 500}

# 🌟 確保這裡只有一個左括號和一個右括號配對，不要有多餘的 '}'
# 設定 GitHub Raw 的基礎路徑
GITHUB_BASE_URL = "https://raw.githubusercontent.com/garyleeplus-gy/EnglishGame/main/assets/items/"

BALL_IMAGES = {
    "特獎": f"{GITHUB_BASE_URL}beast-ball.png",
    "一獎": f"{GITHUB_BASE_URL}luxury-ball.png",
    "二獎": f"{GITHUB_BASE_URL}master-ball.png",
    "三獎": f"{GITHUB_BASE_URL}ultra-ball.png",
    "四獎": f"{GITHUB_BASE_URL}great-ball.png",
    "五獎": f"{GITHUB_BASE_URL}poke-ball.png"
}

@st.cache_data(ttl=30) 
def get_admin(): 
    cfg = db.reference("system/admin").get() or {}
    return {
        "admin_id": cfg.get("admin_id", "admin"), 
        "password": cfg.get("password", "1234"), 
        "unlocked_stage": cfg.get("unlocked_stage", 1), # 全域圖鑑開放階段
        "default_hero_limit": cfg.get("default_hero_limit", 3), 
        "default_bank_limit": cfg.get("default_bank_limit", 3),
        "default_reward_limit": cfg.get("default_reward_limit", 99),
        "default_play_time_min": cfg.get("default_play_time_min", 30),
        "game_rates": cfg.get("game_rates", DEFAULT_RATES),
        "store_prices": cfg.get("store_prices", DEFAULT_STORE),
        "gacha": cfg.get("gacha", DEFAULT_GACHA)
    }

def save_admin(d): 
    db.reference("system/admin").set(d)
    get_admin.clear()

@st.cache_data(ttl=30)
def get_parent_info(parent_id):
    return db.reference(f"parents/{parent_id}").get() or {}

def save_parent_info(parent_id, d): 
    db.reference(f"parents/{parent_id}").set(d)
    get_parent_info.clear()

def get_family_heroes(parent_id):
    heroes = db.reference("users").order_by_child("parent").equal_to(parent_id).get()
    return heroes if heroes else {}

def save_user_meta(u_key, d):
    db.reference(f"users/{u_key}").set(d)

@st.cache_data(ttl=300)
def get_shares(): return db.reference("shares").get() or {}
def save_shares(d): 
    db.reference("shares").set(d)
    get_shares.clear()

@st.cache_data(ttl=3600) 
def load_vocab_db(bank_key):
    data = db.reference(f"vocab_banks/{bank_key}").get()
    return data if data else []

def save_vocab_db(bank_key, df):
    records = df.fillna("").to_dict('records')
    db.reference(f"vocab_banks/{bank_key}").set(records)
    load_vocab_db.clear()

@st.cache_resource
def init_default_vocabs():
    if not db.reference("vocab_banks/國小/0").get(): save_vocab_db("國小", pd.DataFrame({"en": ["apple", "cat", "dog"], "zh": ["蘋果", "貓", "狗"], "hint": ["水果", "動物", "動物"]}))
    if not db.reference("vocab_banks/國中/0").get(): save_vocab_db("國中", pd.DataFrame({"en": ["environment", "develop"], "zh": ["環境", "發展"], "hint": ["大自然", "進步"]}))
    if not db.reference("vocab_banks/高中/0").get(): save_vocab_db("高中", pd.DataFrame({"en": ["environment", "develop"], "zh": ["環境", "發展"], "hint": ["大自然", "進步"]}))
    if not db.reference("vocab_banks/多益/0").get(): save_vocab_db("多益", pd.DataFrame({"en": ["implement", "revenue"], "zh": ["實施", "收入"], "hint": ["執行", "金錢"]}))
    return True

init_default_vocabs()

def load_user_data(u_key): 
    d = db.reference(f"user_data/{u_key}").get()
    if not d:
        d = {
            "exp": 0, "level": 1, "hero_hp": 3, "medals": 0, "combo": 0, "is_boss_fight": False, "boss_hp": 3, 
            "history": [], "total_questions": 0, "difficulty": "簡單", "trophies": [], "monster_dex": [], 
            "vocab_bank": "國小", "word_stats": {}, "gold": 0, "shield_active": False, "custom_avatar": None,
            "last_login_date": "", "login_streak": 0, "inventory": {"potion": 0, "shield": 0, "magnifier": 0, "scroll": 0},
            "reward_counts": {}, "play_date": "", "time_played_sec": 0, "extra_time_sec": 0
        }
    else:
        if "vocab_bank" not in d or d["vocab_bank"] == "家長自訂": d["vocab_bank"] = "custom_1"
        if "word_stats" not in d: d["word_stats"] = {}
        if "gold" not in d: d["gold"] = 0
        if "shield_active" not in d: d["shield_active"] = False
        if "last_login_date" not in d: d["last_login_date"] = ""
        if "login_streak" not in d: d["login_streak"] = 0
        if "inventory" not in d: d["inventory"] = {"potion": 0, "shield": 0, "magnifier": 0, "scroll": 0}
        if "scroll" not in d["inventory"]: d["inventory"]["scroll"] = 0
        if "history" not in d: d["history"] = []
        if "trophies" not in d: d["trophies"] = []
        if "monster_dex" not in d: d["monster_dex"] = []
        if "reward_counts" not in d: d["reward_counts"] = {}
        if "play_date" not in d: d["play_date"] = ""
        if "time_played_sec" not in d: d["time_played_sec"] = 0
        if "extra_time_sec" not in d: d["extra_time_sec"] = 0
        if "custom_avatar" not in d: d["custom_avatar"] = None
    return d

def save_user_data(u_key, d): db.reference(f"user_data/{u_key}").set(d)
def load_error_log(u_key): return db.reference(f"error_log/{u_key}").get() or []
def save_error_log(u_key, l): db.reference(f"error_log/{u_key}").set(l)

def delete_user(u_key):
    db.reference(f"user_data/{u_key}").delete()
    db.reference(f"error_log/{u_key}").delete()
    db.reference(f"users/{u_key}").delete()

def reset_user_data(u_key):
    d = load_user_data(u_key)
    d.update({
        "exp": 0, "level": 1, "hero_hp": 3, "medals": 0, "combo": 0, "is_boss_fight": False, "boss_hp": 3, 
        "history": [], "total_questions": 0, "trophies": [], "monster_dex": [], "custom_avatar": None,
        "word_stats": {}, "gold": 0, "shield_active": False, "login_streak": 0,
        "inventory": {"potion": 0, "shield": 0, "magnifier": 0, "scroll": 0}, "reward_counts": {},
        "time_played_sec": 0, "extra_time_sec": 0
    })
    save_user_data(u_key, d)
    save_error_log(u_key, [])

def get_max_hp(level): return min(10, 3 + (level // 5)) 
def get_title(level):
    if level < 3: return "🌱 新手訓練家"
    if level < 7: return "⚔️ 道館挑戰者"
    if level < 12: return "🌟 菁英訓練家"
    if level < 20: return "🔥 四天王候補"
    return "👑 寶可夢大師"

EBBINGHAUS_INTERVALS = [0, 60, 600, 86400, 86400*3, 86400*7, 86400*15]

def pick_next_question(v_list, err_log, total_q, word_stats):
    now = time.time()
    valid_err = [w for w in err_log if any(v['en'] == w for v in v_list)]
    if valid_err and (total_q >= 15 or random.random() < 0.3):
        w = random.choice(valid_err)
        for v in v_list:
            if v['en'] == w: return v
    due_words = []
    new_words = []
    for v in v_list:
        w = v['en']
        if w not in word_stats: new_words.append(v)
        elif word_stats[w].get("next_review", 0) <= now: due_words.append(v)
    if due_words: return random.choice(due_words)
    if new_words: return random.choice(new_words)
    return random.choice(v_list)

def generate_options(c_v, f_list):
    o = [c_v['zh']]
    w = [v['zh'] for v in f_list if v['zh'] != c_v['zh']]
    o.extend(random.sample(w, min(3, max(0, len(w)))))
    while len(o) < 4: o.append("錯誤選項")
    random.shuffle(o)
    return o

st.set_page_config(page_title="寶可夢英文挑戰", page_icon="⚡", layout="wide")

# 🎨 深度排版與佈局 CSS 優化 (支援 4 欄道具店)
st.markdown("""
<style>
#MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;}
.block-container { max-width: 900px; padding-top: 1rem; padding-bottom: 2rem; }

/* 大廳標題 */
.poke-title-box { background-color: #ffcb05; padding: 20px; border-radius: 15px; border: 5px solid #3c5aa6; text-align: center; margin-bottom: 25px; box-shadow: 0 6px 15px rgba(0,0,0,0.2); }
.poke-title { color: #3c5aa6; margin: 0; font-size: 3.2rem; font-weight: 900; letter-spacing: 2px; text-shadow: 2px 2px 0px #fff, -2px -2px 0px #fff, 2px -2px 0px #fff, -2px 2px 0px #fff; }
.poke-subtitle { color: #e74c3c; font-weight: bold; font-size: 1.2rem; margin-top: 10px; background: white; display: inline-block; padding: 5px 20px; border-radius: 20px; border: 2px solid #e74c3c;}

/* 🎛️ 絕美深色儀表板 */
.dash-board { display: flex; justify-content: space-between; background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); color: white; padding: 12px 15px; border-radius: 15px; margin-bottom: 15px; box-shadow: 0 6px 12px rgba(0,0,0,0.2); border: 2px solid #334155; align-items: center; }
.dash-item { text-align: center; flex: 1; border-right: 1px solid #334155; }
.dash-item:last-child { border-right: none; }
.dash-label { font-size: 0.75rem; color: #94a3b8; font-weight: bold; margin-bottom: 3px; letter-spacing: 1px;}
.dash-val { font-size: 1.2rem; font-weight: 900; }
.val-hp { color: #ef4444; } .val-gold { color: #facc15; } .val-medal { color: #38bdf8; } .val-lvl { color: #a78bfa; }

/* 🎯 戰鬥舞台 */
.arena-bg { position: relative; display: flex; justify-content: center; align-items: flex-end; padding: 30px 10px 20px 10px; border-radius: 15px; box-shadow: 0 8px 25px rgba(0,0,0,0.3); margin: 15px 0; min-height: 250px; overflow: hidden; gap: 20px;}
.hero-box, .monster-box { display: flex; flex-direction: column; align-items: center; justify-content: flex-end; width: 35%; max-width: 160px; z-index: 5; }
.vs-box { width: 15%; text-align: center; z-index: 5; align-self: center; }
.vs-text { color: #f1c40f; font-size: 3rem; font-style: italic; text-shadow: 2px 2px 0 #000; margin:0; }
.hp-badge { font-size: 1rem; margin-bottom: 10px; background: rgba(0,0,0,0.5); border-radius: 20px; padding: 3px 12px; display: inline-block; color: #fff; white-space: nowrap; border: 1px solid rgba(255,255,255,0.2); box-shadow: 0 2px 4px rgba(0,0,0,0.3);}
.hp-badge-enemy { color: #ff6b6b; }
.monster-name { color:white; font-weight:bold; margin-top:8px; text-shadow: 1px 1px 3px #000; font-size: 1.1rem; background: rgba(0,0,0,0.4); padding: 2px 10px; border-radius: 10px;}

/* 🛍️ 道具店4小格強制同行 */
div[data-testid="column"]:nth-child(1), div[data-testid="column"]:nth-child(2), div[data-testid="column"]:nth-child(3), div[data-testid="column"]:nth-child(4) { width: 25% !important; flex: 1 1 25% !important; min-width: 22% !important; }
div[data-testid="column"] button { height: 55px; padding: 0 !important; font-size: 0.85rem !important; border-radius: 12px; font-weight: bold; box-shadow: 0 2px 5px rgba(0,0,0,0.1); white-space: pre-line; }

/* 單字卡與圖鑑 */
.vocab-card { text-align:center; padding: 5%; background: #ffffff; border-radius: 12px; border: 3px solid #3498db; box-shadow: 0 4px 10px rgba(0,0,0,0.05); margin-bottom: 10px; }
.vocab-word { color:#2980b9; font-size: 3.5rem; margin: 5px 0; font-weight: 800; word-wrap: break-word;}
.vocab-hint-str { color:#34495e; font-size: 2.5rem; margin: 10px 0; font-weight: bold; letter-spacing: 5px; word-wrap: break-word;}
.dex-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(70px, 1fr)); gap: 12px; text-align: center; padding: 10px 0; }
.dex-item img { width: 100%; max-width: 60px; height: auto; transition: transform 0.2s; cursor: pointer; }
.dex-item img:hover { transform: scale(1.2); }
.dex-name { font-size: 0.75rem; color: #555; margin-top: 5px; font-weight: bold; }

@media screen and (max-width: 600px) {
    .poke-title { font-size: 2rem; }
    .poke-subtitle { font-size: 0.9rem; }
    .arena-bg { min-height: 180px; padding: 20px 5px; gap: 10px;}
    .hero-box, .monster-box { width: 40%; max-width: 120px;}
    .vs-text { font-size: 2rem; }
    .hp-badge { font-size: 0.75rem; padding: 2px 8px; margin-bottom: 5px;}
    .monster-name { font-size: 0.85rem; }
    .vocab-word { font-size: 2.5rem; }
    div[data-testid="column"] button { font-size: 0.75rem !important; height: 50px;}
}

@keyframes heroDash { 0% { transform: scaleX(-1) translateX(0px); } 30% { transform: scaleX(-1) translateX(-40px); } 100% { transform: scaleX(-1) translateX(0px); } }
@keyframes shakeHurt { 0% { transform: translateX(0); filter: brightness(1); } 20% { transform: translateX(-10px); filter: brightness(2.5) drop-shadow(0 0 25px red); } 40% { transform: translateX(10px); } 60% { transform: translateX(-10px); } 80% { transform: translateX(10px); } 100% { transform: translateX(0); filter: brightness(1); } }
@keyframes monsterDash { 0% { transform: translateX(0px); } 30% { transform: translateX(-40px); } 100% { transform: translateX(0px); } }
@keyframes heroHurt { 0% { transform: scaleX(-1) translateX(0); filter: brightness(1); } 20% { transform: scaleX(-1) translateX(-8px); filter: brightness(0.4) sepia(1) hue-rotate(-50deg) saturate(6); } 100% { transform: scaleX(-1) translateX(0); filter: brightness(1); } }
@keyframes heroDead { 0% { transform: scaleX(-1) rotate(0deg); filter: grayscale(0%); } 100% { transform: scaleX(-1) rotate(90deg) translateY(20px); filter: grayscale(100%); } }
@keyframes healFx { 0% { filter: brightness(1) drop-shadow(0 0 0px #2ecc71); } 50% { filter: brightness(1.5) drop-shadow(0 0 20px #2ecc71); } 100% { filter: brightness(1) drop-shadow(0 0 0px #2ecc71); } }
.m-fx { position: absolute; top: 40%; font-size: 60px; z-index: 10; }
</style>
""", unsafe_allow_html=True)

st.components.v1.html("""<script>
if (!window.parent.gameAudioCtx) {
    const AudioContext = window.parent.AudioContext || window.parent.webkitAudioContext;
    if (AudioContext) { window.parent.gameAudioCtx = new AudioContext(); }
}
const unlockAudio = function() { if (window.parent.gameAudioCtx && window.parent.gameAudioCtx.state === 'suspended') window.parent.gameAudioCtx.resume(); };
document.addEventListener('click', unlockAudio, true); document.addEventListener('touchstart', unlockAudio, true);
if(window.parent && window.parent.document) { window.parent.document.addEventListener('click', unlockAudio, true); window.parent.document.addEventListener('touchstart', unlockAudio, true); }
</script>""", height=0)

if 'page' not in st.session_state: st.session_state.page = 'login'
if 'play_auto_audio' not in st.session_state: st.session_state.play_auto_audio = True
if 'magnifier_active' not in st.session_state: st.session_state.magnifier_active = False
if 'spell_input' not in st.session_state: st.session_state.spell_input = ""
if 'level_up_flag' not in st.session_state: st.session_state.level_up_flag = False
if 'show_transform_modal' not in st.session_state: st.session_state.show_transform_modal = False

# ==================== 登入大廳 ====================
if st.session_state.page == 'login':
    st.markdown("""
    <div class="poke-title-box">
        <h1 class="poke-title">⚡ 寶可夢英文挑戰 ⚡</h1>
        <div class="poke-subtitle">打怪、進化、抓寶！成為英文大師！</div>
    </div>
    """, unsafe_allow_html=True)
    
    st.subheader("🎒 選擇您的家庭與訓練家")
    family_input = st.text_input("1️⃣ 請輸入您的家庭帳號", placeholder="輸入後按下 Enter 鍵確認...")
    
    if family_input:
        if family_input == "#ddmm":
            st.session_state.page = 'parent_login'
            st.rerun()
        elif family_input == "#GM":
            st.session_state.page = 'admin_login'
            st.rerun()
            
        parent_data = get_parent_info(family_input)
        if parent_data:
            family_heroes = get_family_heroes(family_input)
            if not family_heroes: st.warning("這個家庭還沒有建立訓練家帳號，請家長先登入控制台建立喔！")
            else:
                hero_display = {k: v["name"] for k, v in family_heroes.items()}
                sel_hero_key = st.selectbox("2️⃣ 選擇你的訓練家", list(hero_display.keys()), format_func=lambda x: hero_display[x])
                hero_pin = st.text_input("3️⃣ 輸入專屬密碼 (PIN)", type="password", placeholder="預設為 0000")
                
                if st.button("🚀 出發冒險！", type="primary", use_container_width=True):
                    if hero_pin == family_heroes[sel_hero_key].get("pin", "0000"):
                        init_data = load_user_data(sel_hero_key)
                        today_str = str(datetime.now().date())
                        last_date = init_data.get("last_login_date", "")
                        if last_date != today_str:
                            try:
                                delta = (datetime.strptime(today_str, "%Y-%m-%d") - datetime.strptime(last_date, "%Y-%m-%d")).days
                                if delta == 1: init_data["login_streak"] = init_data.get("login_streak", 0) + 1
                                else: init_data["login_streak"] = 1
                            except: init_data["login_streak"] = 1
                            
                            admin_cfg = get_admin()
                            rates = parent_data.get('game_rates', admin_cfg.get('game_rates', DEFAULT_RATES))
                            if "簡單" not in rates: rates = {"簡單": rates}
                            
                            base_gold = rates["簡單"].get('normal_gold', 10)
                            bonus_gold = min(base_gold * 5, init_data["login_streak"] * base_gold)
                            init_data["gold"] = init_data.get("gold", 0) + bonus_gold
                            init_data["last_login_date"] = today_str
                            save_user_data(sel_hero_key, init_data)
                            st.session_state.show_streak = f"🔥 連續冒險 {init_data['login_streak']} 天！獲得 {bonus_gold} 枚金幣！"
                        
                        st.session_state.current_user_key = sel_hero_key
                        st.session_state.current_parent = family_input
                        st.session_state.game_data = init_data
                        st.session_state.error_log = load_error_log(sel_hero_key)
                        
                        st.session_state.hero_name = family_heroes[sel_hero_key]["name"]
                        st.session_state.hero_char = family_heroes[sel_hero_key]['character']
                        st.session_state.play_auto_audio = True
                        st.session_state.spell_input = ""
                        st.session_state.page = 'game'; st.rerun()
                    else: st.error("❌ 密碼錯誤！請確認密碼是否正確。")
        else: st.error("找不到這個家庭帳號，請確認輸入是否正確。")

# ==================== 隱藏入口：家長登入與註冊 ====================
elif st.session_state.page == 'parent_login':
    st.markdown("<h1 style='text-align: center; color:#e67e22;'>👨‍👩‍👧 家庭控制台入口</h1><hr>", unsafe_allow_html=True)
    
    colA, colB = st.columns(2)
    with colA:
        st.subheader("家長登入")
        l_acc = st.text_input("家庭帳號", key="l_acc")
        l_pwd = st.text_input("密碼", type="password", key="l_pwd")
        if st.button("確認登入", use_container_width=True, type="primary"):
            p_data = get_parent_info(l_acc)
            if p_data and p_data.get("password") == l_pwd:
                st.session_state.current_parent = l_acc
                st.session_state.page = 'parent'; st.rerun()
            else: st.error("帳號或密碼錯誤！")
            
    with colB:
        st.subheader("註冊新家庭帳號")
        r_acc = st.text_input("設定帳號 (不可更改)", key="r_acc")
        r_pwd = st.text_input("設定密碼", type="password", key="r_pwd")
        if st.button("註冊", use_container_width=True):
            if not r_acc.strip() or not r_pwd.strip(): st.error("帳號密碼不能為空！")
            elif get_parent_info(r_acc): st.error("帳號已存在！")
            else:
                new_p = {
                    "password": r_pwd, "hero_limit": None, "bank_limit": None,
                    "rewards": [{"reward": "週末多玩 30 分鐘 Switch", "cost_medals": 1, "icon": "🎮", "limit": 99}],
                    "custom_banks": [{"id": "1", "name": "預設自建字庫"}]
                }
                save_parent_info(r_acc, new_p)
                st.success("註冊成功！請由左側登入。")
                
    st.markdown("---")
    if st.button("🚪 返回遊戲大廳", use_container_width=True):
        st.session_state.page = 'login'
        st.rerun()

# ==================== 隱藏入口：GM 登入 ====================
elif st.session_state.page == 'admin_login':
    st.markdown("<h1 style='text-align: center; color:#c0392b;'>👑 系統管理員驗證</h1><hr>", unsafe_allow_html=True)
    admin_db = get_admin()
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        gm_id = st.text_input("管理員帳號", key="gm_id")
        gm_pwd = st.text_input("輸入 GM 密碼", type="password", key="gm_pwd")
        if st.button("GM 登入", use_container_width=True, type="primary"):
            if gm_id == admin_db.get("admin_id", "admin") and gm_pwd == admin_db.get("password", "1234"): 
                st.session_state.page = 'admin'; st.rerun()
            else: st.error("帳號或密碼錯誤！")
        
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🚪 返回遊戲大廳", use_container_width=True):
            st.session_state.page = 'login'
            st.rerun()

# ==================== 遊戲主畫面 ====================
elif st.session_state.page == 'game':
    u_key = st.session_state.current_user_key
    u_data = st.session_state.game_data
    parent_id = st.session_state.current_parent
    
    p_info = get_parent_info(parent_id)
    admin_cfg = get_admin()
    unlocked_stage = admin_cfg.get("unlocked_stage", 1)

    now_ts = time.time()
    if 'last_db_sync' not in st.session_state:
        st.session_state.last_db_sync = now_ts
        
    if now_ts - st.session_state.last_db_sync > 5:
        latest_db_data = load_user_data(u_key)
        u_data['gold'] = latest_db_data.get('gold', u_data.get('gold', 0))
        u_data['medals'] = latest_db_data.get('medals', u_data.get('medals', 0))
        u_data['history'] = latest_db_data.get('history', [])
        u_data['reward_counts'] = latest_db_data.get('reward_counts', {})
        u_data['extra_time_sec'] = latest_db_data.get('extra_time_sec', u_data.get('extra_time_sec', 0))
        u_data['daily_play_time_min'] = latest_db_data.get('daily_play_time_min', u_data.get('daily_play_time_min', 30))
        u_data['level'] = latest_db_data.get('level', u_data.get('level', 1))
        st.session_state.last_db_sync = now_ts
        
    # --- ⏳ 當日遊玩時間與 7:00 AM 重置邏輯 ---
    tpe_now = datetime.utcnow() + timedelta(hours=8)
    logical_date = (tpe_now - timedelta(hours=7)).strftime("%Y-%m-%d")
    
    if u_data.get('play_date') != logical_date:
        u_data['play_date'] = logical_date
        u_data['time_played_sec'] = 0
        u_data['extra_time_sec'] = 0
        st.session_state.last_tick_time = time.time()
        
    now_ts = time.time()
    if 'last_tick_time' in st.session_state:
        elapsed = now_ts - st.session_state.last_tick_time
        if elapsed > 0:
            elapsed = min(elapsed, 900)
            u_data['time_played_sec'] = u_data.get('time_played_sec', 0) + elapsed
    st.session_state.last_tick_time = now_ts
    
    base_quota_min = u_data.get("daily_play_time_min", p_info.get("daily_play_time_min", admin_cfg.get("default_play_time_min", 30)))
    total_allowed_sec = (base_quota_min * 60) + u_data.get('extra_time_sec', 0)
    remaining_sec = max(0, total_allowed_sec - u_data['time_played_sec'])
    
    if remaining_sec <= 0:
        st.markdown("""
        <div style="background:#c0392b; padding:40px 20px; border-radius:15px; text-align:center; color:white; box-shadow: 0 10px 20px rgba(0,0,0,0.3); margin-top:30px;">
            <h1 style="font-size:3rem; margin-bottom:15px;">⏳ 時間到囉！</h1>
            <h3 style="font-size:1.5rem; line-height:1.6;">
                今天的遊戲額度已經用完囉！<br>請好好休息，保護眼睛！每天早上 7:00 會重新補滿時間。<br><br>
                <span style="color:#f1c40f;">💡 如果有需要，可以請家長到「家庭控制台」幫你解鎖加時喔！</span>
            </h3>
        </div>
        """, unsafe_allow_html=True)
        if st.button("🚪 點我返回登入大廳", use_container_width=True, type="primary"):
            st.session_state.page = 'login'
            st.rerun()
        st.stop()

    # --- ✨ 讀取所有已經收集的 ID 以備變身及圖鑑使用 ---
    def name_to_id(val):
        if isinstance(val, int): return val
        # 相容舊版 string name
        try:
            return POKE_NAMES.index(val) + 1
        except:
            return 132 # 百變怪

    dex_ids = list(set([name_to_id(x) for x in u_data.get('monster_dex', []) + u_data.get('trophies', [])]))
    dex_ids.sort()

    # --- 📜 變形卷軸彈窗 ---
    if st.session_state.show_transform_modal:
        st.markdown("<h2 style='text-align: center; color: #8e44ad;'>📜 變形卷軸：選擇你的新外觀！</h2>", unsafe_allow_html=True)
        
        if not dex_ids:
            st.warning("你還沒有在圖鑑中收集到任何寶可夢喔！快去打怪收集吧！")
            if st.button("❌ 取消返回"):
                st.session_state.show_transform_modal = False
                st.rerun()
        else:
            st.info("點擊下方你已收集到的寶可夢，即可變身！(消耗 1 張變形卷軸)")
            t_cols = st.columns(5)
            for i, pid in enumerate(dex_ids):
                with t_cols[i % 5]:
                    st.image(get_poke_url(pid))
                    if st.button(get_poke_name(pid), key=f"tf_btn_{pid}"):
                        u_data['custom_avatar'] = pid
                        if u_data['inventory'].get('scroll', 0) > 0:
                            u_data['inventory']['scroll'] -= 1
                        save_user_data(u_key, u_data)
                        st.session_state.show_transform_modal = False
                        st.success(f"✨ 成功變身為 {get_poke_name(pid)}！")
                        st.rerun()
            
            st.markdown("<hr>", unsafe_allow_html=True)
            t_cancel1, t_cancel2 = st.columns(2)
            if t_cancel1.button("❌ 取消並返回", use_container_width=True):
                st.session_state.show_transform_modal = False
                st.rerun()
            if u_data.get('custom_avatar'):
                if t_cancel2.button("🔄 恢復原本的主角外觀", use_container_width=True):
                    u_data['custom_avatar'] = None
                    save_user_data(u_key, u_data)
                    st.session_state.show_transform_modal = False
                    st.success("已恢復原始夥伴外觀！")
                    st.rerun()
        st.stop() # 停止渲染遊戲底層畫面

    # ==================== 🎁 全螢幕扭蛋巨球結果視窗 ====================
    if st.session_state.get('show_gacha_result', False):
        prize = st.session_state.gacha_result_prize
        b_color = "#bdc3c7"
        
        GITHUB_BASE_URL = "https://raw.githubusercontent.com/garyleeplus-gy/EnglishGame/main/assets/items/"
        HD_BALL_IMAGES = {
            "特獎": f"{GITHUB_BASE_URL}hd_beast-ball.png",
            "一獎": f"{GITHUB_BASE_URL}hd_luxury-ball.png",
            "二獎": f"{GITHUB_BASE_URL}hd_master-ball.png",
            "三獎": f"{GITHUB_BASE_URL}hd_ultra-ball.png",
            "四獎": f"{GITHUB_BASE_URL}hd_great-ball.png",
            "五獎": f"{GITHUB_BASE_URL}hd_poke-ball.png"
        }
        
        b_img = HD_BALL_IMAGES["五獎"]
        for k, v in HD_BALL_IMAGES.items():
            if k in prize['name']: 
                b_img = v
                if k == "特獎": b_color = "#f1c40f"
                elif k == "一獎": b_color = "#e74c3c"
                elif k == "二獎": b_color = "#9b59b6" 
                elif k == "三獎": b_color = "#f1c40f" 
                elif k == "四獎": b_color = "#3498db" 
                break
                
        st.markdown(f"""
        <style>
            [data-testid="stHeader"] {{ display: none !important; }}
            [data-testid="stAppViewContainer"] {{ background: rgba(40, 40, 40, 0.95) !important; overflow: hidden !important; }}
            .main, .main .block-container {{ padding: 0 !important; margin: 0 !important; height: 100vh !important; max-width: 100% !important; }}
            .main .block-container > div {{ position: fixed !important; top: 50% !important; left: 50% !important; transform: translate(-50%, -50%) !important; display: flex !important; flex-direction: column !important; align-items: center !important; justify-content: center !important; width: 100% !important; z-index: 9999 !important; }}
            .result-container {{ display: flex; flex-direction: column; align-items: center; justify-content: center; width: 100%; position: relative; animation: popIn 0.6s cubic-bezier(0.175, 0.885, 0.32, 1.275) both; }}
            .result-ball {{ width: 180px !important; height: 500px !important; min-width: 140px !important; min-height: 140px !important; object-fit: contain !important; image-rendering: auto !important; filter: drop-shadow(0 0 20px {b_color}) brightness(1.1) !important; margin-bottom: -250px !important; z-index: 50 !important; position: relative !important; animation: dropAndBounce 1s cubic-bezier(0.28, 0.84, 0.42, 1) forwards !important; }}
            .result-card {{ background: white !important; padding: 95px 20px 30px 20px !important; border-radius: 16px !important; text-align: center !important; box-shadow: 0 0 50px {b_color} !important; border: 4px solid {b_color} !important; width: 85% !important; max-width: 320px !important; z-index: 10 !important; position: relative !important; }}
            @keyframes dropAndBounce {{ 0% {{ transform: translateY(-300px) scale(0.5); opacity: 0; }} 50% {{ transform: translateY(0px) scale(1.1); opacity: 1; }} 70% {{ transform: translateY(-20px) scale(1); }} 85% {{ transform: translateY(0px) scale(1); }} 95% {{ transform: translateY(-8px) scale(1); }} 100% {{ transform: translateY(0px) scale(1.05); }} }}
            @keyframes popIn {{ 0% {{ transform: scale(0.8) translateY(50px); opacity: 0; }} 100% {{ transform: scale(1) translateY(0); opacity: 1; }} }}
            div[data-testid="stVerticalBlock"] > div:has(button) {{ display: flex !important; justify-content: center !important; width: 100% !important; margin-top: 25px !important; }}
            div[data-testid="stButton"] {{ width: 100% !important; max-width: 320px !important; display: flex !important; justify-content: center !important; margin: 0 !important; }}
            div[data-testid="stButton"] button {{ font-size: 1.1rem !important; font-weight: 900 !important; padding: 12px !important; background: linear-gradient(180deg, #ff6b6b 0%, #ff4757 100%) !important; color: white !important; border-radius: 12px !important; border: 2px solid white !important; box-shadow: 0 6px 15px rgba(255, 71, 87, 0.4) !important; width: 100% !important; transition: transform 0.2s, filter 0.2s !important; }}
            div[data-testid="stButton"] button:hover {{ filter: brightness(1.15) !important; transform: translateY(-2px) !important; }}
        </style>
        
        <div class="result-container">
            <img class="result-ball" src="{b_img}">
            <div class="result-card">
                <h1 style="color: #2c3e50; margin-top: 0; margin-bottom: 20px; font-size: 1.6rem; font-weight: 800; letter-spacing: 1px;">🎉 恭喜中獎 🎉</h1>
                <div style="background: #f4f4f4; border-radius: 12px; padding: 15px 10px; margin: 0;">
                    <h2 style="color: {b_color}; font-size: 1.35rem; margin: 0; font-weight: 900; text-shadow: 1px 1px 0px rgba(0,0,0,0.15), -1px -1px 0px rgba(255,255,255,0.8); line-height: 1.4;">
                        {prize['name'].replace(' (', '<br>(')}
                    </h2>
                </div>
            </div>
        </div>
        <script>
            setTimeout(() => {{
                let ctx = window.parent.gameAudioCtx;
                if(ctx) {{
                    if(ctx.state === 'suspended') ctx.resume();
                    let osc = ctx.createOscillator(); let gain = ctx.createGain();
                    osc.type = 'triangle'; osc.connect(gain); gain.connect(ctx.destination);
                    let now = ctx.currentTime;
                    osc.frequency.setValueAtTime(440, now); osc.frequency.setValueAtTime(554, now + 0.1);
                    osc.frequency.setValueAtTime(659, now + 0.2); osc.frequency.setValueAtTime(880, now + 0.3);
                    gain.gain.setValueAtTime(0, now); gain.gain.linearRampToValueAtTime(0.5, now+0.1);
                    gain.gain.exponentialRampToValueAtTime(0.01, now + 1.5);
                    osc.start(now); osc.stop(now + 1.5);
                }}
            }}, 500);
        </script>
        """, unsafe_allow_html=True)
        
        if st.button("🎁 點擊收下獎勵", type="primary"):
            prize = st.session_state.gacha_result_prize
            
            if prize['type'] == 'medal': u_data['medals'] += prize['val']
            elif prize['type'] == 'gold': u_data['gold'] += prize['val']
            elif prize['type'] == 'item':
                u_data['inventory']['potion'] += prize['val']
                u_data['inventory']['shield'] += prize['val']
                u_data['inventory']['magnifier'] += prize['val']
            
            if 'gacha_history' not in u_data: u_data['gacha_history'] = []
            u_data['gacha_history'].append(f"{datetime.now().strftime('%m-%d %H:%M')} 扭蛋獲得：{prize['name']}")
            u_data['gacha_history'] = u_data['gacha_history'][-10:]
            
            save_user_data(u_key, u_data)
            st.session_state.show_gacha_result = False
            st.rerun()
        st.stop()

    # --- 儀表板上方：當地時間與倒數計時 ---
    st.markdown(f"""
    <div style="display:flex; justify-content:space-between; align-items:center; background: linear-gradient(135deg, #1e293b, #0f172a); color:white; padding:12px 20px; border-radius:12px; margin-bottom:15px; border: 2px solid #38bdf8;">
        <div id="local-clock" style="font-size:1.2rem; font-weight:bold; color:#a78bfa;">⏰ 時間讀取中...</div>
        <div id="countdown-timer" style="font-size:1.3rem; font-weight:900; color:#facc15;">⏳ 剩餘時間: {int(remaining_sec//60)}分 {int(remaining_sec%60)}秒</div>
    </div>
    <script>
        if (window.parent.timerInterval) clearInterval(window.parent.timerInterval);
        let remain = {remaining_sec};
        window.parent.timerInterval = setInterval(() => {{
            const clockEl = window.parent.document.getElementById('local-clock');
            if(clockEl) clockEl.innerText = "⏰ 當地時間: " + new Date().toLocaleTimeString('zh-TW', {{ timeZone: 'Asia/Taipei', hour12: false }});
            if(remain > 0) {{
                remain -= 1;
                let m = Math.floor(remain / 60); let s = Math.floor(remain % 60);
                const timerEl = window.parent.document.getElementById('countdown-timer');
                if(timerEl) timerEl.innerText = "⏳ 剩餘時間: " + m + "分 " + s + "秒";
            }}
        }}, 1000);
    </script>
    """, unsafe_allow_html=True)

    exp_current = u_data.get('exp', 0) % 100
    st.markdown(f"""
    <div style="margin-bottom: 15px; padding: 0 5px;">
        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: #7f8c8d; font-weight: bold; margin-bottom: 5px;">
            <span>✨ 經驗值進度 (EXP)</span><span>{exp_current} / 100</span>
        </div>
        <div style="width: 100%; background-color: #e2e8f0; border-radius: 10px; height: 12px;">
            <div style="width: {exp_current}%; background: linear-gradient(90deg, #3498db, #2ecc71); height: 100%; border-radius: 10px;"></div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    raw_rates = p_info.get("game_rates", admin_cfg.get("game_rates", DEFAULT_RATES))
    if "簡單" not in raw_rates: raw_rates = {"簡單": raw_rates, "中等": {k:v*2 for k,v in raw_rates.items()}, "困難": {k:v*3 for k,v in raw_rates.items()}}
    diff = u_data.get('difficulty', "簡單")
    rates = raw_rates.get(diff, raw_rates["簡單"])
    
    # 🌟 修正：確保新舊價格能正確疊加 (預設 < GM < 家長)
    store_prices = DEFAULT_STORE.copy()
    if isinstance(admin_cfg.get("store_prices"), dict): store_prices.update(admin_cfg["store_prices"])
    if isinstance(p_info.get("store_prices"), dict): store_prices.update(p_info["store_prices"])
    
    gacha_cfg = p_info.get("gacha", admin_cfg.get("gacha", DEFAULT_GACHA))
    if 'show_streak' in st.session_state:
        st.toast(st.session_state.show_streak, icon="🔥")
        del st.session_state.show_streak
        
    hero_name = st.session_state.hero_name
    char_d = CHARACTERS[st.session_state.hero_char]  
    
    if u_data.get('custom_avatar'):
        hero_url = get_poke_url(u_data['custom_avatar'])
    else:
        stage_idx = 0 if u_data['level'] < 5 else (1 if u_data['level'] < 10 else 2)
        hero_img_id, hero_img_name = char_d["stages"][stage_idx]
        hero_url = get_poke_url(hero_img_id)

    bank_id = u_data.get('vocab_bank', '國小')
    bank_name = bank_id
    if bank_id.startswith("custom_"):
        cb_id = bank_id.split("_")[1]
        v_list = load_vocab_db(f"custom_{parent_id}_{cb_id}")
        # 👇 確保這裡只有賦值，沒有 st.warning
        if not v_list: v_list = [{"en": "apple", "zh": "蘋果", "hint": "預設單字"}]
        bank_name = next((b["name"] for b in p_info.get("custom_banks", []) if b["id"] == cb_id), "自訂字庫")
    else:
        v_list = load_vocab_db(bank_id)
        if not v_list: v_list = [{"en": "hero", "zh": "英雄", "hint": ""}]
    
    r_list = p_info.get("rewards", [])

    if 'current_monster' not in st.session_state: 
        st.session_state.current_monster = spawn_enemy(unlocked_stage, is_boss=u_data.get('is_boss_fight', False))
        
    if 'action_anim' not in st.session_state: st.session_state.action_anim = None
    if 'current_vocab' not in st.session_state: 
        st.session_state.current_vocab = pick_next_question(v_list, st.session_state.error_log, u_data['total_questions'], u_data['word_stats'])
        st.session_state.current_options = generate_options(st.session_state.current_vocab, v_list)
    if 'force_learning' not in st.session_state: st.session_state.force_learning = False
    
    c_w = st.session_state.current_vocab
    max_hp = get_max_hp(u_data['level'])

    def process_ans(s):
        st.session_state.play_auto_audio = True 
        st.session_state.magnifier_active = False 
        if "spell_input" in st.session_state: st.session_state.spell_input = "" 
        
        latest_db = load_user_data(u_key)
        u_data['gold'] = latest_db.get('gold', u_data.get('gold', 0))
        u_data['medals'] = latest_db.get('medals', u_data.get('medals', 0))
        u_data['history'] = latest_db.get('history', [])
        u_data['reward_counts'] = latest_db.get('reward_counts', {})
            
        u_data['total_questions'] += 1 
        word = c_w['en']
        
        if s.strip().lower() == c_w['zh'].strip().lower() or s.strip().lower() == word.strip().lower():
            stats = u_data['word_stats'].setdefault(word, {"level": 0, "next_review": 0, "mistakes": 0})
            stats["level"] = min(len(EBBINGHAUS_INTERVALS)-1, stats["level"] + 1)
            stats["next_review"] = time.time() + EBBINGHAUS_INTERVALS[stats["level"]]
            u_data['combo'] += 1
            if word in st.session_state.error_log and stats["level"] >= 4:
                st.session_state.error_log.remove(word)
                save_error_log(u_key, st.session_state.error_log)
                    
            if u_data.get('is_boss_fight', False):
                u_data['boss_hp'] -= 1
                u_data['exp'] += int(rates['boss_exp'])
                if u_data['boss_hp'] <= 0:
                    u_data['medals'] += int(rates['boss_medal'])
                    u_data['gold'] += int(rates['boss_gold'])
                    
                    e_id = st.session_state.current_monster['id']
                    if 'trophies' not in u_data: u_data['trophies'] = []
                    if e_id not in u_data['trophies']: u_data['trophies'].append(e_id)
                        
                    st.session_state.pending_boss_defeat = True
                    st.session_state.action_anim = 'boss_defeat'
                else: st.session_state.action_anim = 'attack'
            else:
                u_data['exp'] += int(rates['normal_exp']) 
                u_data['gold'] += int(rates['normal_gold'])
                
                e_id = st.session_state.current_monster['id']
                if 'monster_dex' not in u_data: u_data['monster_dex'] = []
                if e_id not in u_data['monster_dex']: u_data['monster_dex'].append(e_id)
                        
                st.session_state.action_anim = 'attack'
                if u_data['combo'] >= 10 and not u_data.get('is_boss_fight', False):
                    st.session_state.pending_boss_fight = True
            
            if (u_data['exp'] // 100) + 1 > u_data['level']:
                u_data['level'] = (u_data['exp'] // 100) + 1
                u_data['hero_hp'] = get_max_hp(u_data['level']) 
                st.session_state.level_up_flag = True
                
        else:
            stats = u_data['word_stats'].setdefault(word, {"level": 0, "next_review": 0, "mistakes": 0})
            stats["level"] = max(0, stats["level"] - 1)
            stats["mistakes"] = stats.get("mistakes", 0) + 1
            stats["next_review"] = time.time()
            u_data['combo'] = 0 
            if word not in st.session_state.error_log:
                st.session_state.error_log.append(word)
                save_error_log(u_key, st.session_state.error_log)
                
            if u_data.get('shield_active', False):
                u_data['shield_active'] = False
                st.session_state.action_anim = 'shield_block'
            else:
                u_data['hero_hp'] -= 1
                if u_data['hero_hp'] <= 0:
                    if u_data['level'] > 1:
                        u_data['level'] -= 1
                        st.session_state.level_dropped = True
                    else: st.session_state.level_dropped = False
                    u_data['exp'] = (u_data['level'] - 1) * 100
                    u_data['hero_hp'] = get_max_hp(u_data['level'])
                    if u_data.get('is_boss_fight', False): u_data['boss_hp'] = 3
                    st.session_state.action_anim = 'dead'
                else: st.session_state.action_anim = 'hurt'
        save_user_data(u_key, u_data)

    def text_input_submit():
        ans = st.session_state.spell_input
        if ans:
            st.session_state.spell_input = "" 
            process_ans(ans)

    # --- 🎛️ 絕美深色儀表板 ---
    hero_title = get_title(u_data['level'])
    st.markdown(f"""
    <div class="dash-board">
        <div class="dash-item">
            <div class="dash-label">{hero_title}</div>
            <div class="dash-val val-lvl">Lv.{u_data['level']}</div>
        </div>
        <div class="dash-item">
            <div class="dash-label">❤ 生命</div>
            <div class="dash-val val-hp">{u_data['hero_hp']}/{max_hp}</div>
        </div>
        <div class="dash-item">
            <div class="dash-label">💰 金幣</div>
            <div class="dash-val val-gold">{u_data['gold']}</div>
        </div>
        <div class="dash-item">
            <div class="dash-label">🎖️ 勳章</div>
            <div class="dash-val val-medal">{u_data['medals']}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # --- 🏪 道具商店 (變更為 4 欄並加入變形卷軸) ---
    st.markdown("<div style='font-size:0.8rem; font-weight:bold; color:#7f8c8d; margin-bottom:5px;'>🏪 道具店 (點擊花費金幣立即發動)</div>", unsafe_allow_html=True)
    c_btn1, c_btn2, c_btn3, c_btn4 = st.columns(4)
    
    inv_p = u_data['inventory'].get('potion', 0)
    btn1_lbl = f"🧪 藥水 ({inv_p})\n點擊發動" if inv_p > 0 else f"🧪 買藥水\n{store_prices['potion']}G"
    if c_btn1.button(btn1_lbl, use_container_width=True, disabled=u_data['hero_hp']>=max_hp):
        if inv_p > 0 or u_data['gold'] >= store_prices['potion']:
            if inv_p > 0: u_data['inventory']['potion'] -= 1
            else: u_data['gold'] -= store_prices['potion']
            u_data['hero_hp'] = min(max_hp, u_data['hero_hp'] + 1)
            st.session_state.action_anim = 'heal'
            save_user_data(u_key, u_data); st.rerun()
        else: st.error("金幣與庫存皆不足！")
            
    inv_s = u_data['inventory'].get('shield', 0)
    btn2_lbl = f"🛡️ 護盾 ({inv_s})\n點擊發動" if inv_s > 0 else f"🛡️ 買護盾\n{store_prices['shield']}G"
    if c_btn2.button(btn2_lbl, use_container_width=True, disabled=u_data.get('shield_active', False)):
        if inv_s > 0 or u_data['gold'] >= store_prices['shield']:
            if inv_s > 0: u_data['inventory']['shield'] -= 1
            else: u_data['gold'] -= store_prices['shield']
            u_data['shield_active'] = True
            save_user_data(u_key, u_data); st.rerun()
        else: st.error("金幣與庫存皆不足！")
            
    inv_m = u_data['inventory'].get('magnifier', 0)
    btn3_lbl = f"🔍 放大鏡 ({inv_m})\n點擊發動" if inv_m > 0 else f"🔍 買放大鏡\n{store_prices['magnifier']}G"
    if c_btn3.button(btn3_lbl, use_container_width=True, disabled=st.session_state.magnifier_active):
        if inv_m > 0 or u_data['gold'] >= store_prices['magnifier']:
            if inv_m > 0: u_data['inventory']['magnifier'] -= 1
            else: u_data['gold'] -= store_prices['magnifier']
            st.session_state.magnifier_active = True
            if diff == '簡單':
                correct_ans = c_w['zh']
                wrong_indices = [i for i, opt in enumerate(st.session_state.current_options) if opt != correct_ans and opt != "❌"]
                if len(wrong_indices) >= 2:
                    to_remove = random.sample(wrong_indices, 2)
                    for i in to_remove: st.session_state.current_options[i] = "❌"
            save_user_data(u_key, u_data); st.rerun()
        else: st.error("金幣與庫存皆不足！")

    scroll_cost = store_prices.get('scroll', 500)
    inv_scroll = u_data['inventory'].get('scroll', 0)
    btn4_lbl = f"📜 變形卷軸 ({inv_scroll})\n點擊發動" if inv_scroll > 0 else f"📜 變形卷軸\n{scroll_cost}G"
    if c_btn4.button(btn4_lbl, use_container_width=True):
        if inv_scroll > 0 or u_data['gold'] >= scroll_cost:
            if inv_scroll == 0: 
                u_data['gold'] -= scroll_cost
                u_data['inventory']['scroll'] += 1
                save_user_data(u_key, u_data)
            st.session_state.show_transform_modal = True
            st.rerun()
        else:
            st.error("金幣與庫存皆不足！")

# --- 🎰 幸運扭蛋機 ---
    with st.expander("🎰 幸運扭蛋機 (花費金幣抽大獎)", expanded=False):
        g_col1, g_col2 = st.columns([1, 1])
        with g_col1:
            gacha_html = f"""
            <html><head><style>
                body {{ margin: 0; padding: 0; font-family: sans-serif; background: transparent; overflow: hidden; display: flex; justify-content: center; align-items: center; }}
                .gacha-wrapper {{ display: flex; justify-content: center; align-items: center; padding: 25px 0 10px 0; width: 100%; }}
                .gacha-machine {{ background-color: #ff4757; border: 4px solid #2f3542; border-radius: 20px; padding: 20px 15px 10px; width: 280px; text-align: center; box-shadow: inset -5px -5px 0px rgba(0,0,0,0.1), 0 8px 0 #ff6b81, 0 15px 20px rgba(0,0,0,0.3); position: relative; margin: 0 auto; }}
                .gacha-glass {{ background-color: #f1f2f6; border: 4px solid #2f3542; border-radius: 15px; height: 160px; margin-bottom: 15px; position: relative; overflow: hidden; box-shadow: inset 0 0 20px rgba(0,0,0,0.1); }}
                .gacha-ball {{ position: absolute; width: 45px; height: 45px; background-size: contain; background-repeat: no-repeat; border-radius: 50%; box-shadow: 2px 2px 5px rgba(0,0,0,0.3); image-rendering: pixelated; }}
            </style></head><body>
            <div class="gacha-wrapper">
                <div class="gacha-machine">
                    <div style="background: #feca57; width: 100px; height: 30px; border: 4px solid #2f3542; border-radius: 20px 20px 0 0; position: absolute; top: -34px; left: 50%; transform: translateX(-50%);">
                        <div style="background: #ff6b6b; width: 14px; height: 14px; border-radius: 50%; margin: 4px auto; border: 2px solid #2f3542;"></div>
                    </div>
                    <div class="gacha-glass">
                        <div class="gacha-ball" style="background-image: url('{BALL_IMAGES["一獎"]}'); top: 10px; left:10px; transform: rotate(20deg);"></div>
                        <div class="gacha-ball" style="background-image: url('{BALL_IMAGES["三獎"]}'); top: 40px; left:50px; transform: rotate(-15deg);"></div>
                        <div class="gacha-ball" style="background-image: url('{BALL_IMAGES["四獎"]}'); top: 15px; left:110px; transform: rotate(45deg);"></div>
                        <div class="gacha-ball" style="background-image: url('{BALL_IMAGES["五獎"]}'); top: 70px; left:10px; transform: rotate(-30deg);"></div>
                        <div class="gacha-ball" style="background-image: url('{BALL_IMAGES["二獎"]}'); top: 90px; left:80px; transform: rotate(10deg);"></div>
                        <div class="gacha-ball" style="background-image: url('{BALL_IMAGES["特獎"]}'); top: 60px; left:140px; transform: rotate(60deg);"></div>
                        <div class="gacha-ball" style="background-image: url('{BALL_IMAGES["三獎"]}'); bottom: 5px; left:30px; transform: rotate(80deg);"></div>
                        <div class="gacha-ball" style="background-image: url('{BALL_IMAGES["五獎"]}'); bottom: 10px; left:110px; transform: rotate(-40deg);"></div>
                        <div style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: linear-gradient(135deg, rgba(255,255,255,0.6) 0%, rgba(255,255,255,0) 50%); pointer-events: none;"></div>
                    </div>
                    <div style="display: flex; justify-content: center; align-items: center; gap: 15px; margin-bottom: 5px;">
                        <div style="font-weight: bold; color: white; text-shadow: 1px 1px 0px #000; font-size: 1.1rem; text-align: left; line-height: 1.2;">點擊旋轉<br>({gacha_cfg['cost']}G) 👉</div>
                        <div id="gacha-knob" style="width: 70px; height: 70px; background: #1dd1a1; border: 4px solid #2f3542; border-radius: 50%; display: flex; align-items: center; justify-content: center; cursor: pointer; transition: transform 0.5s; box-shadow: 0 4px 0 #10ac84, inset 0 2px 5px rgba(255,255,255,0.5);">
                            <div style="width: 50px; height: 12px; background: #feca57; border: 2px solid #2f3542; transform: rotate(45deg); border-radius: 5px;"></div>
                        </div>
                    </div>
                </div>
            </div>
            <script>
                document.getElementById('gacha-knob').onclick = function() {{
                    this.style.transform = 'rotate(360deg)';
                    let ctx = window.parent.gameAudioCtx;
                    if(ctx) {{
                        if(ctx.state === 'suspended') ctx.resume();
                        let osc = ctx.createOscillator(); let gain = ctx.createGain();
                        osc.type = 'sine'; osc.connect(gain); gain.connect(ctx.destination);
                        let now = ctx.currentTime;
                        for(let i=0; i<10; i++){{osc.frequency.setValueAtTime(300 + Math.random()*200, now + i*0.05);}}
                        gain.gain.setValueAtTime(0.2, now); gain.gain.linearRampToValueAtTime(0, now + 0.5);
                        osc.start(now); osc.stop(now + 0.5);
                    }}
                    setTimeout(() => {{
                        const btns = window.parent.document.querySelectorAll('button');
                        for (let b of btns) {{ if(b.innerText.includes('🎮隱藏抽獎發動🎮')) {{ b.click(); break; }} }}
                        setTimeout(() => {{ document.getElementById('gacha-knob').style.transform = 'rotate(0deg)'; }}, 500);
                    }}, 600);
                }};
            </script>
            </body></html>
            """
            st.components.v1.html(gacha_html, height=360)
            
            if st.button("🎮隱藏抽獎發動🎮"):
                if u_data['gold'] >= gacha_cfg['cost']:
                    u_data['gold'] -= gacha_cfg['cost']
                    save_user_data(u_key, u_data)
                    prizes = gacha_cfg['prizes']
                    weights = [p['prob'] for p in prizes]
                    prize = random.choices(prizes, weights=weights)[0]
                    st.session_state.show_gacha_result = True
                    st.session_state.gacha_result_prize = prize
                    st.rerun()
                else: st.error("金幣不足！快去打怪賺錢吧！")

        with g_col2:
            st.markdown("<div style='font-size:1.1rem; font-weight:bold; color:#ff4757; margin-bottom:10px;'>📜 最新扭蛋紀錄</div>", unsafe_allow_html=True)
            if u_data.get('gacha_history'):
                for item in reversed(u_data['gacha_history']): st.caption(f"• {item}")
            else: st.caption("尚未有扭蛋紀錄。")

    # --- 🎁 家庭獎勵兌換系統 ---
    with st.expander("🎁 家庭獎勵兌換與紀錄 (花費勳章)", expanded=False):
        r_cols = st.columns(2)
        with r_cols[0]:
            st.markdown("**🏪 可兌換獎勵**")
            if not r_list: st.info("家長尚未設定獎勵")
            u_reward_counts = u_data.get("reward_counts", {})
            for r in r_list:
                r_name = r['reward']
                r_limit = r.get('limit', admin_cfg.get('default_reward_limit', 99))
                r_count = u_reward_counts.get(r_name, 0)
                r_left = r_limit - r_count
                
                btn_disabled = (u_data['medals'] < int(r['cost_medals'])) or (r_left <= 0)
                btn_lbl = f"{r['icon']} {r_name} (需 {r['cost_medals']} 勳章) - 剩餘 {r_left} 次" if r_left > 0 else f"❌ {r_name} (已達兌換上限)"
                
                if st.button(btn_lbl, use_container_width=True, key=f"ex_{r_name}", disabled=btn_disabled):
                    if u_data['medals'] >= int(r['cost_medals']) and r_left > 0:
                        u_data['medals'] -= int(r['cost_medals'])
                        u_reward_counts[r_name] = r_count + 1
                        u_data['reward_counts'] = u_reward_counts
                        u_data['history'].append(f"{datetime.now().strftime('%m-%d %H:%M')} 兌換 {r_name}")
                        save_user_data(u_key, u_data)
                        st.success(f"🎉 成功兌換 {r_name}！")
                        st.rerun()
        with r_cols[1]:
            st.markdown("**📜 我的兌換紀錄**")
            medal_history = [item for item in u_data.get('history', []) if "扭蛋獲得" not in item]
            if medal_history:
                for item in reversed(medal_history[-10:]): st.caption(f"• {item}")
            else: st.caption("尚未兌換任何獎勵。")
    
    # --- 冒險圖鑑 (修改為依賴 ID) ---
    with st.expander(f"📖 寶可夢圖鑑 (題庫: {bank_name} | 收集: {len(u_data.get('monster_dex', []))}/{STAGE_MAX_IDS[unlocked_stage]} | 傳說: {len(u_data.get('trophies', []))}/{len([i for i in LEGENDARY_IDS if i<=STAGE_MAX_IDS[unlocked_stage]])})"):
        d_tab1, d_tab2 = st.tabs(["🏆 傳說神獸", "👾 已收集寶可夢"])
        with d_tab1:
            trophy_ids = [name_to_id(x) for x in u_data.get('trophies', [])]
            if trophy_ids:
                html_dex = '<div class="dex-grid">'
                for pid in set(trophy_ids):
                    html_dex += f'<div class="dex-item"><img src="{get_poke_url(pid)}"><div class="dex-name">{get_poke_name(pid)}</div></div>'
                html_dex += '</div>'; st.markdown(html_dex, unsafe_allow_html=True)
            else: st.write("尚未收集到神獸。")
        with d_tab2:
            mon_ids = [name_to_id(x) for x in u_data.get('monster_dex', [])]
            if mon_ids:
                html_dex = '<div class="dex-grid">'
                for pid in set(mon_ids):
                    html_dex += f'<div class="dex-item"><img src="{get_poke_url(pid)}"><div class="dex-name">{get_poke_name(pid)}</div></div>'
                html_dex += '</div>'; st.markdown(html_dex, unsafe_allow_html=True)
            else: st.write("尚未收集到寶可夢。")

    # ==================== 🎯 戰鬥舞台與動畫邏輯 ====================
    scale_factor = 1 + min(u_data['medals'] * 0.1, 2.0)
    h_width = int(100 * scale_factor)

    anim = st.session_state.action_anim
    h_s = f"width: {h_width}%; max-width: 250px; transform: scaleX(-1); image-rendering: pixelated; transition: width 0.5s;"
    if u_data.get('shield_active', False): h_s += " filter: drop-shadow(0 0 10px #ffffff) drop-shadow(0 0 25px #ffd700) brightness(1.3) contrast(1.1);"
    
    m_s = "width: 100%; max-width: 180px; image-rendering: pixelated;"
    fx_html = ""
    audio_js = ""

    snd_lvlup = "let osc2 = ctx_lvl.createOscillator(); let gain2 = ctx_lvl.createGain(); osc2.type = 'square'; osc2.connect(gain2); gain2.connect(ctx_lvl.destination); let now2 = ctx_lvl.currentTime; osc2.frequency.setValueAtTime(330, now2); osc2.frequency.setValueAtTime(392, now2 + 0.1); osc2.frequency.setValueAtTime(523, now2 + 0.2); osc2.frequency.setValueAtTime(659, now2 + 0.3); osc2.frequency.setValueAtTime(784, now2 + 0.4); gain2.gain.setValueAtTime(0.2, now2); gain2.gain.linearRampToValueAtTime(0, now2 + 0.6); osc2.start(now2); osc2.stop(now2 + 0.6);"
    snd_boss_win = "let osc = ctx.createOscillator(); let gain = ctx.createGain(); osc.type = 'triangle'; osc.connect(gain); gain.connect(ctx.destination); let now = ctx.currentTime; osc.frequency.setValueAtTime(440, now); osc.frequency.setValueAtTime(440, now + 0.15); osc.frequency.setValueAtTime(440, now + 0.3); osc.frequency.setValueAtTime(587, now + 0.45); gain.gain.setValueAtTime(0.3, now); gain.gain.linearRampToValueAtTime(0, now + 1.0); osc.start(now); osc.stop(now + 1.0);"

    if anim == 'attack':
        h_s += " animation: heroDash 0.7s ease-in-out;"
        m_s += " animation: shakeHurt 0.7s ease-in-out 0.2s;"
        fx_html = f'<div class="m-fx">{char_d["fx"]}</div>'
        audio_js = f"<script>let ctx = window.parent.gameAudioCtx; if(ctx) {{ if(ctx.state === 'suspended') ctx.resume(); let osc = ctx.createOscillator(); let gain = ctx.createGain(); osc.type = '{char_d['snd_type']}'; osc.frequency.setValueAtTime({char_d['snd_freq']}, ctx.currentTime); osc.frequency.exponentialRampToValueAtTime({char_d['snd_drop']}, ctx.currentTime + {char_d['snd_len']}); gain.gain.setValueAtTime(0.8 * 0.25, ctx.currentTime); gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + {char_d['snd_len']}); osc.connect(gain); gain.connect(ctx.destination); osc.start(); osc.stop(ctx.currentTime + {char_d['snd_len']}); }}</script>"
    elif anim == 'hurt':
        m_s += " animation: monsterDash 0.7s ease-in-out;"
        h_s += " animation: heroHurt 0.7s ease-in-out 0.2s;"
        h_snd = random.choice(HURT_SOUNDS)
        audio_js = f"<script>let ctx = window.parent.gameAudioCtx; if(ctx) {{ if(ctx.state === 'suspended') ctx.resume(); let osc = ctx.createOscillator(); let gain = ctx.createGain(); osc.type = '{h_snd['type']}'; osc.frequency.setValueAtTime({h_snd['f1']}, ctx.currentTime); osc.frequency.exponentialRampToValueAtTime({h_snd['f2']}, ctx.currentTime + {h_snd['len']}); gain.gain.setValueAtTime(0.8 * 0.25, ctx.currentTime); gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + {h_snd['len']}); osc.connect(gain); gain.connect(ctx.destination); osc.start(); osc.stop(ctx.currentTime + {h_snd['len']}); }}</script>"
    elif anim == 'shield_block':
        m_s += " animation: monsterDash 0.7s ease-in-out;"
        h_s += " animation: heroDash 0.5s ease-in-out 0.2s;"
        audio_js = f"<script>let ctx = window.parent.gameAudioCtx; if(ctx) {{ if(ctx.state === 'suspended') ctx.resume(); let osc = ctx.createOscillator(); let gain = ctx.createGain(); osc.type = 'sine'; osc.frequency.setValueAtTime(800, ctx.currentTime); osc.frequency.linearRampToValueAtTime(1200, ctx.currentTime + 0.3); gain.gain.setValueAtTime(0.8 * 0.25, ctx.currentTime); gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3); osc.connect(gain); gain.connect(ctx.destination); osc.start(); osc.stop(ctx.currentTime + 0.3); }}</script>"
    elif anim == 'dead':
        h_s += " animation: heroDead 1s forwards;"
        audio_js = f"<script>let ctx = window.parent.gameAudioCtx; if(ctx) {{ if(ctx.state === 'suspended') ctx.resume(); let osc = ctx.createOscillator(); let gain = ctx.createGain(); osc.type = 'sawtooth'; osc.frequency.setValueAtTime(300, ctx.currentTime); osc.frequency.linearRampToValueAtTime(50, ctx.currentTime + 1.5); gain.gain.setValueAtTime(0.8 * 0.25, ctx.currentTime); gain.gain.linearRampToValueAtTime(0.01, ctx.currentTime + 1.5); osc.connect(gain); gain.connect(ctx.destination); osc.start(); osc.stop(ctx.currentTime + 1.5); }}</script>"
    elif anim == 'heal':
        h_s += " animation: healFx 1s ease-in-out;"
        audio_js = f"<script>let ctx = window.parent.gameAudioCtx; if(ctx) {{ if(ctx.state === 'suspended') ctx.resume(); let osc = ctx.createOscillator(); let gain = ctx.createGain(); osc.type = 'sine'; osc.frequency.setValueAtTime(400, ctx.currentTime); osc.frequency.exponentialRampToValueAtTime(800, ctx.currentTime + 0.5); gain.gain.setValueAtTime(0, ctx.currentTime); gain.gain.linearRampToValueAtTime(0.4, ctx.currentTime + 0.1); gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.5); osc.connect(gain); gain.connect(ctx.destination); osc.start(); osc.stop(ctx.currentTime + 0.5); }}</script>"
    elif anim == 'boss_defeat':
        audio_js = f"<script>let ctx = window.parent.gameAudioCtx; if(ctx) {{ if(ctx.state === 'suspended') ctx.resume(); {snd_boss_win} }}</script>"

    if st.session_state.get('level_up_flag'):
        audio_js += f"<script>let ctx_lvl = window.parent.gameAudioCtx; if(ctx_lvl) {{ if(ctx_lvl.state === 'suspended') ctx_lvl.resume(); {snd_lvlup} }}</script>"
        st.session_state.level_up_flag = False

    is_boss = u_data.get('is_boss_fight', False)
    if is_boss:
        e_n = st.session_state.current_monster['name']
        e_u = st.session_state.current_monster['url']
        e_hp = u_data.get('boss_hp', 3)
        m_hp = 3
        bg_s = "background: linear-gradient(135deg, #2b0b0f 0%, #4a0911 100%); border: 4px solid #ff4500;"
        m_s = m_s.replace("180px", "260px")
    else:
        enemy = st.session_state.current_monster
        e_n = enemy['name']
        e_u = enemy['url']
        e_hp = 1
        m_hp = 1
        bg_s = "background: linear-gradient(135deg, #2980b9 0%, #6dd5fa 100%); border: 4px solid #fff;"

    arena_html = (
        f'<div class="arena-bg" style="{bg_s}">'
        f'{fx_html}'
        f'<div class="hero-box">'
        f'<div class="hp-badge">{"❤️"*(u_data["hero_hp"])}{"🖤"*(max_hp-u_data["hero_hp"])}</div>'
        f'<img src="{hero_url}" style="{h_s}">'
        f'</div>'
        f'<div class="vs-box"><h1 class="vs-text">VS</h1></div>'
        f'<div class="monster-box">'
        f'<div class="hp-badge hp-badge-enemy">{"🩸"*e_hp}{"🖤"*(m_hp-e_hp)}</div>'
        f'<img src="{e_u}" style="{m_s}">'
        f'<div class="monster-name">{e_n}</div>'
        f'</div></div>'
    )
    
    st.markdown(arena_html, unsafe_allow_html=True)
    st.components.v1.html(audio_js if audio_js else " ", height=0)

   # ==================== ⚡ 答題區與動畫隱藏邏輯 ====================
    if anim:
        if anim == 'attack': st.success(f"💥 命中！獲得 {int(rates['normal_exp'])} EXP 與 {int(rates['normal_gold'])} G！")
        elif anim == 'heal': st.success("🧪 喝下生命藥水，生命值恢復了！")
        elif anim == 'shield_block': st.info("🛡️ 神聖護盾為你擋下了一次致命傷害！(但答錯了還是要進入記憶訓練喔！)")
        elif anim == 'hurt': st.error("🩸 遭受攻擊！連擊中斷！")
        elif anim == 'boss_defeat':
            st.balloons()
            st.success(f"🎊 擊敗傳說寶可夢！獲得 {int(rates['boss_exp'])} EXP、{int(rates['boss_gold'])} G 與 {int(rates['boss_medal'])} 枚勳章！")
        elif anim == 'dead': 
            if st.session_state.get('level_dropped', False): st.error("😭 夥伴寶可夢不支倒地... (等級下降 1 級，經驗值重置！)")
            else: st.error("😭 夥伴寶可夢不支倒地... (已經是最低等級 Lv.1 囉！)")
        
        st.info("⚔ 結算中，請稍候...")
        time.sleep(1.8)
        
        st.session_state.action_anim = None
        st.session_state.level_dropped = False 

        # === 🎯 動畫結束後的魔物同步切換邏輯 ===
        if st.session_state.get('pending_boss_defeat', False):
            u_data['is_boss_fight'] = False
            u_data['combo'] = 0 
            st.session_state.pending_boss_defeat = False
            st.session_state.current_monster = spawn_enemy(unlocked_stage, is_boss=False)
        elif st.session_state.get('pending_boss_fight', False):
            u_data['is_boss_fight'] = True
            u_data['boss_hp'] = 3
            st.session_state.current_monster = spawn_enemy(unlocked_stage, is_boss=True)
            st.session_state.pending_boss_fight = False
        elif anim == 'attack' and not u_data.get('is_boss_fight', False):
            st.session_state.current_monster = spawn_enemy(unlocked_stage, is_boss=False)
            
        save_user_data(u_key, u_data) 

        # --- 處理學習題目更新 ---
        if anim in ['hurt', 'dead', 'boss_defeat', 'shield_block']: 
            if anim != 'boss_defeat': st.session_state.force_learning = True
            else:
                st.session_state.current_vocab = pick_next_question(v_list, st.session_state.error_log, u_data['total_questions'], u_data['word_stats'])
                st.session_state.current_options = generate_options(st.session_state.current_vocab, v_list)
        else:
            st.session_state.current_vocab = pick_next_question(v_list, st.session_state.error_log, u_data['total_questions'], u_data['word_stats'])
            st.session_state.current_options = generate_options(st.session_state.current_vocab, v_list)
            st.session_state.play_auto_audio = True
            st.session_state.magnifier_active = False 
            
        st.rerun()

    else:
        opts = st.session_state.current_options
        clean_en = re.sub(r'[\(\[].*?[\)\]]', '', c_w['en']).strip()
        tts_word = clean_en.replace('"', '\\"').replace("'", "\\'")
        
        ipa_txt = ipa.convert(clean_en)
        ipa_d = f"[{ipa_txt}]" if ipa_txt and '*' not in ipa_txt else ""
        rev = '<span style="background: #e74c3c; color: white; padding: 2px 8px; border-radius: 10px; font-size: 14px; vertical-align: top;">⚠️ 復仇題</span>' if c_w['en'] in st.session_state.error_log else ''
        if st.session_state.force_learning:
            v_html = (
                f'<div class="vocab-card" style="background: #fff5f5; border-color: #e74c3c;">'
                f'<h3 style="margin:0; color:#c0392b; font-size: 1.2rem;">❌ 答錯了！請跟著唸 3 次正確答案！</h3>'
                f'<div class="vocab-word" style="color:#e74c3c;">{c_w["en"]} = {c_w["zh"]}</div>'
                f'<h3 style="color:#e67e22; margin:0 0 15px 0; font-family: monospace; font-size: 1.5rem;">{ipa_d}</h3>'
                f'</div>'
            )
            st.markdown(v_html, unsafe_allow_html=True)
            
            js_force = f"""
            <div style="text-align:center; margin-bottom: 20px;">
                <button id="tts-btn" onclick="window.playForce()" style="background-color: #e74c3c; color: white; border: none; padding: 15px 30px; font-size: 18px; border-radius: 8px; cursor: pointer; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 90%; max-width: 400px; font-weight: bold; animation: pulse 2s infinite;">
                    🔊 準備播放... (若無聲請手動點擊)
                </button>
            </div>
            <style>@keyframes pulse {{ 0% {{ transform: scale(1); }} 50% {{ transform: scale(1.02); }} 100% {{ transform: scale(1); }} }}</style>
            <script>
                setTimeout(() => {{
                    const btns = window.parent.document.querySelectorAll('button');
                    btns.forEach(b => {{ if(b.innerText.includes('繼續冒險')) {{ b.style.display = 'none'; window.parent.continueQuestBtn = b; }} }});
                }}, 100);
                let playCount = 0; let isSpeaking = false; let timeoutId = null;
                window.playForce = function() {{ if (isSpeaking) return; playCount = 0; clearTimeout(timeoutId); speakWord(); }};
                function speakWord() {{
                    let btn = document.getElementById('tts-btn'); if (!btn) return;
                    if (playCount >= 3) {{
                        btn.innerText = "✅ 已完成 3 次！請點下方按鈕繼續"; btn.style.backgroundColor = "#27ae60"; btn.style.animation = "none";
                        if(window.parent.continueQuestBtn) window.parent.continueQuestBtn.style.display = 'inline-flex'; return;
                    }}
                    if (window.speechSynthesis) window.speechSynthesis.cancel();
                    let msg = new SpeechSynthesisUtterance("{tts_word}"); msg.lang = 'en-US'; msg.rate = 0.85; msg.volume = 0.8;
                    let started = false; let ended = false;
                    msg.onstart = function() {{ started = true; isSpeaking = true; btn.innerText = "🔊 播放中，請跟著唸... (" + (playCount + 1) + "/3)"; btn.style.animation = "none"; }};
                    msg.onend = function() {{
                        if (ended) return; ended = true; isSpeaking = false; playCount++;
                        if (playCount < 3) {{ btn.innerText = "⏳ 停頓 1.5 秒... (" + playCount + "/3)"; timeoutId = setTimeout(speakWord, 1500); 
                        }} else speakWord(); 
                    }};
                    setTimeout(() => {{ if (!started && playCount === 0) {{ isSpeaking = false; btn.innerText = "👉 手機限制：請點我開始播放"; btn.style.animation = "pulse 1.5s infinite"; }} else if (started && !ended) msg.onend(); }}, 3500);
                    window.speechSynthesis.speak(msg);
                }}
                setTimeout(window.playForce, 500);
            </script>
            """
            st.components.v1.html(js_force, height=80)
            
            if st.button("💪 我記住了！繼續冒險！", use_container_width=True, type="primary"):
                st.session_state.force_learning = False
                st.session_state.play_auto_audio = True
                st.session_state.current_vocab = pick_next_question(v_list, st.session_state.error_log, u_data['total_questions'], u_data['word_stats'])
                st.session_state.current_options = generate_options(st.session_state.current_vocab, v_list)
                st.rerun()

        else:
            if u_data['total_questions'] >= 20 and st.session_state.error_log: st.warning("🔥 累積滿 20 題！進入強制錯題複習模式！")

            if bank_id.startswith("custom_") and len(v_list) == 1 and v_list[0]["en"] == "apple":
                st.warning("⚠️ 家長注意：這個自訂字庫目前是空的！請盡快前往「家庭控制台」新增單字！")
                
            v_html = f'<div class="vocab-card"><h3 style="margin:0; color:#7f8c8d; font-size: 1.2rem;">✨ 詠唱單字 ✨ {rev}</h3>'
            if diff == '簡單':
                v_html += f'<div class="vocab-word">{c_w["en"]}</div><h3 style="color:#e67e22; margin:0 0 15px 0; font-family: monospace; font-size: 1.5rem;">{ipa_d}</h3>'
                if c_w.get('hint'): v_html += f'<p style="color: #16a085; font-size: 1rem; margin: 0; background: #e8f8f5; padding: 8px; border-radius: 5px; font-weight: bold;">💡 提示：{c_w["hint"]}</p>'
            elif diff == '中等' or diff == '困難':
                word_en = c_w['en']
                w_len = len(word_en)
                
                # 先計算原本難度該給的基礎提示
                base_indices = []
                if diff == '中等':
                    if w_len <= 3: base_indices = [w_len // 2]
                    else: N = (w_len - 1) // 3 + 1; base_indices = [int(i * (w_len - 1) / (N - 1) + 0.5) for i in range(N)]
                
                # 如果有買放大鏡，則「額外」多開 2 個字母
                if st.session_state.magnifier_active:
                    available = [i for i in range(w_len) if i not in base_indices and word_en[i] not in [' ', '-']]
                    extra_reveal = min(2, len(available))
                    if extra_reveal > 0:
                        # 隨機挑選未解鎖的字母解鎖
                        base_indices.extend(random.sample(available, extra_reveal))
                        
                indices = sorted(base_indices)
                        
                hint_chars = [char if (i in indices or char in [' ', '-']) else '_' for i, char in enumerate(word_en)]
                hint_str = " ".join(hint_chars)
                v_html += f'<h1 style="color:#2980b9; font-size: 1.8rem; margin: 10px 0; font-weight: 800;">{c_w["zh"]}</h1>'
                if ipa_d: v_html += f'<h3 style="color:#e67e22; margin:0 0 5px 0; font-family: monospace; font-size: 1.2rem;">{ipa_d}</h3>'
                v_html += f'<div class="vocab-hint-str">{hint_str}</div><h3 style="color:#7f8c8d; margin:0 0 10px 0; font-size: 0.9rem;">請拼出對應的英文單字</h3>'
            
            v_html += '</div>'
            st.markdown(v_html, unsafe_allow_html=True)
            
            auto_script = "setTimeout(() => window.playNormal(false), 500);" if st.session_state.play_auto_audio else ""
            st.session_state.play_auto_audio = False 
            
            btn_html = f"""
            <div style="text-align:center; margin-bottom: 20px;">
                <button id="normal-tts-btn" onclick="window.playNormal(true)" style="background-color: #3498db; color: white; border: none; padding: 10px 25px; font-size: 16px; border-radius: 8px; cursor: pointer; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 80%; max-width: 300px;">
                    🔊 播放單字語音
                </button>
            </div>
            <script>
                const btn = document.getElementById('normal-tts-btn'); let isSpeaking = false; let forceUnlockTimeout = null;
                setTimeout(() => {{ const answerDiv = window.parent.document.getElementById('answer-zone'); if (answerDiv) {{ answerDiv.style.opacity = '0.3'; answerDiv.style.pointerEvents = 'none'; }} }}, 50);
                forceUnlockTimeout = setTimeout(() => {{ const answerDiv = window.parent.document.getElementById('answer-zone'); if (answerDiv) {{ answerDiv.style.opacity = '1'; answerDiv.style.pointerEvents = 'auto'; }} if (!isSpeaking) btn.innerText = "🔊 點擊聆聽單字"; }}, 2000);
                window.playNormal = function(isManual = false) {{ 
                    if (isSpeaking && !isManual) return;
                    if (window.speechSynthesis) window.speechSynthesis.cancel();
                    let msg = new SpeechSynthesisUtterance("{tts_word}"); msg.lang = 'en-US'; msg.rate = 0.9; msg.volume = 0.8; 
                    msg.onstart = function() {{ isSpeaking = true; btn.innerText = "🔊 播放中..."; }};
                    msg.onend = function() {{ isSpeaking = false; btn.innerText = "🔊 點擊重聽單字"; const answerDiv = window.parent.document.getElementById('answer-zone'); if (answerDiv) {{ answerDiv.style.opacity = '1'; answerDiv.style.pointerEvents = 'auto'; }} }};
                    msg.onerror = function() {{ isSpeaking = false; btn.innerText = "🔊 點擊重聽單字"; }};
                    window.speechSynthesis.speak(msg); 
                }};
                {auto_script}
            </script>
            """
            st.components.v1.html(btn_html, height=70)

            st.markdown('<div id="answer-zone" style="transition: opacity 0.5s;">', unsafe_allow_html=True)
            
            def handle_spell_submit():
                ans = st.session_state.get("spell_input", "")
                if ans.strip():
                    st.session_state.spell_input = "" 
                    process_ans(ans.strip())

            # 答題區 
            if diff == '簡單':
                cA, cB = st.columns(2)
                with cA:
                    if opts[0] != "❌": st.button(f"A. {opts[0]}", use_container_width=True, key="ans_a", on_click=process_ans, args=(opts[0],))
                    else: st.button("❌", disabled=True, use_container_width=True, key="ans_a_del")
                    if opts[2] != "❌": st.button(f"C. {opts[2]}", use_container_width=True, key="ans_c", on_click=process_ans, args=(opts[2],))
                    else: st.button("❌", disabled=True, use_container_width=True, key="ans_c_del")
                with cB:
                    if opts[1] != "❌": st.button(f"B. {opts[1]}", use_container_width=True, key="ans_b", on_click=process_ans, args=(opts[1],))
                    else: st.button("❌", disabled=True, use_container_width=True, key="ans_b_del")
                    if opts[3] != "❌": st.button(f"D. {opts[3]}", use_container_width=True, key="ans_d", on_click=process_ans, args=(opts[3],))
                    else: st.button("❌", disabled=True, use_container_width=True, key="ans_d_del")
            else:
                st.markdown("<hr style='border: 1px dashed #bdc3c7; margin: 15px 0;'>", unsafe_allow_html=True)
                st.text_input("✍️ 施展拼寫魔法 (輸入完請直接按鍵盤 Enter，或點擊下方按鈕)：", key="spell_input", autocomplete="off", on_change=handle_spell_submit)
                st.button("⚔️ 送出攻擊", type="primary", use_container_width=True, on_click=handle_spell_submit)
            
    # 放置返回大廳按鈕於最底部
    st.markdown("---")
    if st.button("🚪 離開戰鬥返回大廳", use_container_width=True): 
        st.session_state.page = 'login'
        st.rerun()

# ==================== 家庭控制台 ====================
elif st.session_state.page == 'parent':
    p_id = st.session_state.current_parent
    p_data = get_parent_info(p_id)
    admin_cfg = get_admin()
    
    if "custom_banks" not in p_data:
        p_data["custom_banks"] = [{"id": "1", "name": "預設自建字庫"}]
        save_parent_info(p_id, p_data)
        old_file = f"vocab_custom_{p_id}.csv"
        if os.path.exists(old_file): os.rename(old_file, f"vocab_custom_{p_id}_1.csv")

    st.markdown("<h1 style='text-align: center; color:#e67e22;'>👨‍👩‍👧 家庭專區</h1><hr>", unsafe_allow_html=True)
    c_top1, c_top2 = st.columns([1, 1])
    with c_top1:
        if st.button("⬅ 登出並返回大廳", use_container_width=True): st.session_state.page = 'login'; st.rerun()
    with c_top2:
        with st.expander("🔐 更改密碼"):
            o_pw = st.text_input("舊密碼", type="password")
            n_pw = st.text_input("新密碼", type="password")
            if st.button("確認修改"):
                if o_pw == p_data["password"] and n_pw.strip():
                    p_data["password"] = n_pw.strip()
                    save_parent_info(p_id, p_data)
                    st.success("✅ 成功！")
                else: st.error("錯誤")
    st.markdown("---")
    
    t1, t2, t6, t3, t4, t5 = st.tabs(["🎒 訓練家管理", "🏪 商店與獎勵", "⚙️ 遊戲數值設定", "📚 自建字庫", "🖼️ 目標圖鑑", "📝 官方糾錯回饋"])
    
    with t1:
        limit = p_data.get("hero_limit")
        if limit is None: limit = admin_cfg.get("default_hero_limit", 3)
        
        my_heroes = get_family_heroes(p_id)
        
        global_banks = ["國小", "國中", "高中", "多益"]
        custom_bank_options = [f"custom_{b['id']}" for b in p_data["custom_banks"]]
        all_banks = global_banks + custom_bank_options
        def format_bank(b):
            if b in global_banks: return b
            cb_id = b.split("_")[1]
            cb_name = next((cb["name"] for cb in p_data["custom_banks"] if cb["id"] == cb_id), "未知字庫")
            return f"📂 {cb_name}"

        st.info(f"🎒 目前已建立帳號：{len(my_heroes)} / {limit}")
        
        if len(my_heroes) < limit:
            with st.expander("➕ 建立新訓練家帳號", expanded=False):
                n_name = st.text_input("訓練家名稱 (小孩的名字或暱稱)")
                n_pin = st.text_input("設定登入密碼 (建議設定 4 位數字)", value="0000")
                n_char = st.selectbox("選擇夥伴寶可夢", ["火系 (小火龍)", "水系 (傑尼龜)", "草系 (妙蛙種子)", "電系 (皮丘)", "隨機"])
                n_bank = st.selectbox("選擇預設學習題庫", all_banks, format_func=format_bank)
                n_diff = st.selectbox("選擇初始難度", ["簡單", "中等", "困難"], index=0)
                if st.button("確認建立"):
                    # 加入正則表達式，過濾掉 Firebase 嚴禁的符號 . # $ [ ] /
                    safe_name = re.sub(r'[.#$\/[\]/]', '', n_name.strip())
                    
                    if not safe_name or not n_pin.strip(): 
                        st.error("名稱與密碼不可為空，且名稱不能僅包含特殊符號！")
                    else:
                        # 使用過濾後的安全字串來建立資料庫 Key
                        u_key = f"{p_id}_{safe_name}"
                        
                        if db.reference(f"users/{u_key}").get(): 
                            st.error("這個名稱已經存在於您的家庭中了！")
                        else:
                            c = random.choice(list(CHARACTERS.keys())) if n_char == "隨機" else n_char
                            # 顯示的名稱也改用 safe_name，避免後續讀取時發生非預期的錯誤
                            new_hero = {"name": safe_name, "parent": p_id, "character": c, "created_at": str(datetime.now().date()), "pin": n_pin.strip()}
                            save_user_meta(u_key, new_hero)
                            
                            init_data = load_user_data(u_key)
                            # ... (後續原始碼保持不變)
                            init_data["vocab_bank"] = n_bank
                            init_data["difficulty"] = n_diff
                            save_user_data(u_key, init_data)
                            st.success("✅ 建立成功！"); st.rerun()
        
        st.markdown("#### 訓練家列表")
        for u_key, u_info in my_heroes.items():
            d = load_user_data(u_key)
            e_log = load_error_log(u_key)
            with st.expander(f"👤 {u_info['name']} ({u_info['character']})"):
                
                # --- ⏳ 當日遊玩時間管理 ---
                st.markdown("#### ⏳ 當日遊玩時間管理")
                tpe_now = datetime.utcnow() + timedelta(hours=8)
                logic_date = (tpe_now - timedelta(hours=7)).strftime("%Y-%m-%d")
                
                time_played_sec = d.get('time_played_sec', 0) if d.get('play_date') == logic_date else 0
                extra_sec = d.get('extra_time_sec', 0) if d.get('play_date') == logic_date else 0
                base_quota_min = d.get("daily_play_time_min", p_data.get("daily_play_time_min", admin_cfg.get("default_play_time_min", 30)))
                total_sec = (base_quota_min * 60) + extra_sec
                rem_sec = max(0, total_sec - time_played_sec)
                
                st.info(f"📍 **基礎額度:** {base_quota_min} 分鐘 | **今日已解鎖加時:** {extra_sec//60} 分鐘\n\n"
                        f"🕹️ **今日已玩:** {int(time_played_sec//60)} 分 {int(time_played_sec%60)} 秒 | **⏳ 剩餘:** {int(rem_sec//60)} 分 {int(rem_sec%60)} 秒")
                
                st.markdown("**🎯 快速指派剩餘時間**")
                c_rem1, c_rem2, c_rem3 = st.columns([1, 1, 1.5])
                with c_rem1:
                    new_rem_m = st.number_input("剩餘(分)", min_value=0, value=int(rem_sec//60), key=f"nrm_{u_key}")
                with c_rem2:
                    new_rem_s = st.number_input("剩餘(秒)", min_value=0, max_value=59, value=int(rem_sec%60), key=f"nrs_{u_key}")
                with c_rem3:
                    st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
                    if st.button("💾 覆寫剩餘時間", key=f"btn_set_rem_{u_key}", use_container_width=True):
                        target_sec = (new_rem_m * 60) + new_rem_s
                        d['play_date'] = logic_date
                        d['extra_time_sec'] = target_sec + time_played_sec - (base_quota_min * 60)
                        if 'time_played_sec' not in d: d['time_played_sec'] = time_played_sec
                        save_user_data(u_key, d)
                        st.success(f"✅ 已將剩餘時間精準調整為 {new_rem_m} 分 {new_rem_s} 秒！")
                        st.rerun()

                st.markdown("**⚙️ 常規時間設定**")
                cT1, cT2 = st.columns(2)
                with cT1:
                    add_mins = st.number_input("解鎖增加時間 (分鐘)", min_value=1, value=10, key=f"add_t_{u_key}")
                    if st.button("➕ 解鎖加時", key=f"btn_add_t_{u_key}", use_container_width=True):
                        d['play_date'] = logic_date
                        overdrawn_sec = max(0, time_played_sec - total_sec)
                        d['extra_time_sec'] = extra_sec + (add_mins * 60) + overdrawn_sec
                        
                        if 'time_played_sec' not in d: d['time_played_sec'] = time_played_sec
                        save_user_data(u_key, d)
                        st.success(f"✅ 已成功為 {u_info['name']} 增加 {add_mins} 分鐘！")
                        st.rerun()
                with cT2:
                    child_base_min = st.number_input("專屬每日預設時間 (分鐘)", min_value=1, value=base_quota_min, key=f"base_t_{u_key}")
                    if st.button("💾 儲存專屬時間", key=f"btn_save_base_t_{u_key}", use_container_width=True):
                        d['daily_play_time_min'] = child_base_min
                        save_user_data(u_key, d)
                        st.success(f"✅ {u_info['name']} 專屬時間設定成功！")
                        st.rerun()

                st.markdown("---")
                st.markdown("#### 📊 數據調整")
                cA, cB, cC, cE = st.columns(4)
                n_lvl = cA.number_input("等級", min_value=1, value=d['level'], key=f"lvl_{u_key}")
                n_tq = cB.number_input("累積題數", min_value=0, value=d.get('total_questions', 0), key=f"tq_{u_key}")
                n_mdl = cC.number_input("勳章", min_value=0, value=d['medals'], key=f"mdl_{u_key}")
                new_diff = cE.selectbox("難度", ["簡單", "中等", "困難"], index=["簡單", "中等", "困難"].index(d.get("difficulty", "簡單")), key=f"diff_{u_key}")
                
                col_r2 = st.columns([1, 1, 1, 2])
                curr_bank = d.get("vocab_bank", "國小")
                if curr_bank == "家長自訂": curr_bank = "custom_1" 
                if curr_bank not in all_banks: curr_bank = "國小"
                
                new_bank = col_r2[0].selectbox("學習題庫", all_banks, index=all_banks.index(curr_bank), format_func=format_bank, key=f"bank_{u_key}")
                new_pin = col_r2[1].text_input("修改密碼 (PIN)", value=u_info.get("pin", "0000"), key=f"pin_{u_key}")
                n_gld = col_r2[2].number_input("金幣", min_value=0, value=d.get('gold', 0), key=f"gld_{u_key}")
                
                if new_pin != u_info.get("pin", "0000"):
                    u_info["pin"] = new_pin
                    save_user_meta(u_key, u_info)
                    st.success("密碼已更新！")
                
                if st.button("💾 儲存數據調整", key=f"save_data_{u_key}", type="primary"):
                    d['level'] = n_lvl
                    d['total_questions'] = n_tq
                    d['medals'] = n_mdl
                    d['difficulty'] = new_diff
                    d['vocab_bank'] = new_bank
                    d['gold'] = n_gld
                    save_user_data(u_key, d)
                    st.success("✅ 數據已成功更新！")
                    st.rerun()

                st.write("**🔥 復仇特訓中：**", ", ".join(e_log) if e_log else "目前無待復仇單字！")

                real_errors = [(w, s.get('mistakes', 0)) for w, s in d.get('word_stats', {}).items() if s.get('mistakes', 0) > 0]
                real_errors.sort(key=lambda x: x[1], reverse=True)

                error_display = ", ".join([f"{w} (錯{c}次)" for w, c in real_errors[:20]])
                st.write("**🔴 歷史高頻錯題 (由高到低)：**", error_display if error_display else "太棒了！無任何錯題紀錄！")
                
                # --- 📈 學習統整報告 ---
                st.markdown("#### 📈 學習統整報告")
                
                bank_id = d.get('vocab_bank', '國小')
                if bank_id == "家長自訂": bank_id = "custom_1"
                if bank_id.startswith("custom_"):
                    cb_id = bank_id.split("_")[1]
                    v_list = load_vocab_db(f"custom_{p_id}_{cb_id}")
                else:
                    v_list = load_vocab_db(bank_id)
                total_words = len(v_list) if v_list else 1
                
                word_stats = d.get('word_stats', {})
                learned_words = len(word_stats)
                mastered_words = sum(1 for stats in word_stats.values() if stats.get('level', 0) >= 3)
                error_count = len(e_log)
                
                prog_pct = min(100, int((learned_words / total_words) * 100))
                mast_pct = min(100, int((mastered_words / max(1, learned_words)) * 100)) if learned_words > 0 else 0
                
                mc1, mc2, mc3 = st.columns(3)
                mc1.metric("📚 題庫學習進度", f"{learned_words} / {total_words}", f"{prog_pct}% 完成度")
                mc2.metric("✨ 精熟度 (Lv3以上)", f"{mastered_words} 字", f"佔已學 {mast_pct}%")
                mc3.metric("⚠️ 待補強 (錯題數)", f"{error_count} 字", "錯題本累積" if error_count > 0 else "完美無瑕")
                
                if real_errors:
                    st.markdown("<br>", unsafe_allow_html=True)
                    df_errors = pd.DataFrame(real_errors, columns=["英文單字", "歷史總錯誤次數"])
                    csv_data = df_errors.to_csv(index=False).encode('utf-8-sig')
                    
                    st.download_button(
                        label="📥 一鍵匯出歷史錯題分析 (CSV 報表)",
                        data=csv_data,
                        file_name=f"{u_info['name']}_錯題分析報表_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv",
                        key=f"dl_csv_{u_key}",
                        use_container_width=True
                    )
                
                st.markdown("---")
                
                st.markdown("**🎁 兌換紀錄**")
                medal_history = [item for item in d.get('history', []) if "扭蛋獲得" not in item]
                if medal_history:
                    for i, item in enumerate(reversed(d['history'])):
                        if "扭蛋獲得" in item: continue
                        hA, hB, hC = st.columns([3, 1, 1])
                        hA.text(item)
                        
                        if hB.button("✅ 已兌現", key=f"ful_h_{u_key}_{i}"):
                            actual_idx = len(d['history']) - 1 - i
                            d['history'].pop(actual_idx)
                            save_user_data(u_key, d)
                            st.success("✅ 已標記為兌現並清除紀錄！")
                            st.rerun()
                            
                        if hC.button("🗑️ 退回", key=f"del_h_{u_key}_{i}"):
                            actual_idx = len(d['history']) - 1 - i
                            item_str = d['history'][actual_idx]
                            
                            r_name = item_str.split(" 兌換 ")[-1] if " 兌換 " in item_str else ""
                            refund_medal = 0
                            for r in p_data.get("rewards", []):
                                if r["reward"] == r_name:
                                    refund_medal = r["cost_medals"]
                                    break
                            
                            d['medals'] += refund_medal
                            if r_name in d.get("reward_counts", {}) and d["reward_counts"][r_name] > 0:
                                d["reward_counts"][r_name] -= 1
                                
                            d['history'].pop(actual_idx)
                            save_user_data(u_key, d)
                            st.success(f"🗑️ 已退回！歸還了 {refund_medal} 枚勳章。")
                            st.rerun()
                else: st.caption("無紀錄。")
                    
                b1, b2 = st.columns(2)
                with b1:
                    if st.button(f"🔄 清空訓練家學習資料", key=f"rs_{u_key}"): reset_user_data(u_key); st.rerun()
                with b2:
                    if st.button(f"🗑 永久刪除此帳號", key=f"dl_{u_key}"): delete_user(u_key); st.rerun()

    with t2:
        st.subheader("🛒 道具販售價格設定")
        # 🌟 修正：確保家長後台能正確繼承 GM 的預設值並補齊新道具
        prices = DEFAULT_STORE.copy()
        if isinstance(admin_cfg.get("store_prices"), dict): prices.update(admin_cfg["store_prices"])
        if isinstance(p_data.get("store_prices"), dict): prices.update(p_data["store_prices"])
        
        c1, c2, c3, c4 = st.columns(4)
        new_p = c1.number_input("🧪 藥水價格 (G)", min_value=1, value=prices.get("potion", 200))
        new_s = c2.number_input("🛡️ 護盾價格 (G)", min_value=1, value=prices.get("shield", 250))
        new_m = c3.number_input("🔍 放大鏡價格 (G)", min_value=1, value=prices.get("magnifier", 100))
        new_scroll = c4.number_input("📜 變形卷軸 (G)", min_value=1, value=prices.get("scroll", 500))
        if st.button("💾 儲存道具價格", type="primary"):
            p_data["store_prices"] = {"potion": new_p, "shield": new_s, "magnifier": new_m, "scroll": new_scroll}
            save_parent_info(p_id, p_data)
            st.success("✅ 道具物價已更新！")
            
        st.markdown("---")
        st.subheader("🎁 新增家庭專屬勳章獎勵")
        with st.form("add_r"):
            n_r = st.text_input("名稱")
            n_c = st.number_input("需要勳章", min_value=1, value=1)
            n_limit = st.number_input("兌換次數上限 (每位孩子)", min_value=1, value=admin_cfg.get("default_reward_limit", 99))
            n_i = st.selectbox("圖示", EMOJI_LIST)
            if st.form_submit_button("➕ 新增獎勵"):
                if n_r.strip():
                    if "rewards" not in p_data: p_data["rewards"] = []
                    p_data["rewards"].append({"reward": n_r, "cost_medals": n_c, "icon": n_i, "limit": n_limit})
                    save_parent_info(p_id, p_data); st.success("✅ 成功！"); st.rerun()
        
        st.subheader("目前可兌換清單")
        r_list = p_data.get("rewards", [])
        if not r_list: st.info("目前沒有設定任何獎勵。")
        for idx, r in enumerate(r_list):
            cA, cB = st.columns([4, 1])
            with cA: st.info(f"{r['icon']} {r['reward']} (需 {r['cost_medals']} 勳章 | 上限: {r.get('limit', admin_cfg.get('default_reward_limit', 99))}次)")
            with cB:
                if st.button("🗑️", key=f"d_r_{idx}"):
                    p_data["rewards"].pop(idx)
                    save_parent_info(p_id, p_data); st.rerun()

    with t6:
        st.subheader("⚙️ 家庭專屬遊戲參數 (覆寫系統預設)")
        gacha = p_data.get("gacha", admin_cfg.get("gacha", DEFAULT_GACHA))
        
        with st.expander("⏳ 家庭專屬每日遊玩時間預設值", expanded=True):
            p_play_time = st.number_input("每日預設遊玩時間 (分鐘)", min_value=1, value=p_data.get("daily_play_time_min", admin_cfg.get("default_play_time_min", 30)))
            if st.button("💾 儲存遊玩時間預設值"):
                p_data["daily_play_time_min"] = p_play_time
                save_parent_info(p_id, p_data); st.success("儲存成功！"); st.rerun()
                
        with st.expander("⚔️️ 戰鬥掉落率設定", expanded=True):
            raw_rates = p_data.get("game_rates", admin_cfg.get("game_rates", DEFAULT_RATES))
            if "簡單" not in raw_rates:
                raw_rates = {"簡單": raw_rates, "中等": {k:v*2 for k,v in raw_rates.items()}, "困難": {k:v*3 for k,v in raw_rates.items()}}
            
            diff_tabs = st.tabs(["🟢 簡單", "🟡 中等", "🔴 困難"])
            diff_keys = ["簡單", "中等", "困難"]
            new_rates = {}
            for i, d_key in enumerate(diff_keys):
                with diff_tabs[i]:
                    r1, r2 = st.columns(2)
                    cur = raw_rates[d_key]
                    new_rates[d_key] = {
                        "normal_exp": r1.number_input(f"一般怪 EXP ({d_key})", min_value=1, value=cur.get("normal_exp", 5), key=f"p_n_exp_{d_key}"),
                        "normal_gold": r2.number_input(f"一般怪 金幣 ({d_key})", min_value=1, value=cur.get("normal_gold", 10), key=f"p_n_gld_{d_key}"),
                        "boss_exp": r1.number_input(f"Boss EXP ({d_key})", min_value=1, value=cur.get("boss_exp", 10), key=f"p_b_exp_{d_key}"),
                        "boss_gold": r2.number_input(f"Boss 金幣 ({d_key})", min_value=1, value=cur.get("boss_gold", 50), key=f"p_b_gld_{d_key}"),
                        "boss_medal": r1.number_input(f"Boss 勳章 ({d_key})", min_value=0, value=cur.get("boss_medal", 1), key=f"p_b_mdl_{d_key}")
                    }
            if st.button("💾 儲存戰鬥掉落率"):
                p_data["game_rates"] = new_rates
                save_parent_info(p_id, p_data); st.success("儲存成功！"); st.rerun()

        with st.expander("🎰 幸運扭蛋機設定", expanded=True):
            g_cost = st.number_input("扭蛋單次花費 (G)", min_value=10, value=gacha.get("cost", 300))
            st.caption("設定各獎項內容與機率 (總和必須為 100%)")
            st.caption("您可以自由設定獎品名稱、發放的類型(勳章/金幣/道具包)、給予的數量，以及中獎機率。")
            
            current_prizes = gacha.get("prizes", DEFAULT_GACHA["prizes"])
            display_data = []
            ranks = ["特獎", "一獎", "二獎", "三獎", "四獎", "五獎"]
            type_map_ui = {"medal": "勳章", "gold": "金幣", "item": "道具包"}
            type_map_db = {"勳章": "medal", "金幣": "gold", "道具包": "item"}
            
            for i in range(6):
                p = current_prizes[i] if i < len(current_prizes) else DEFAULT_GACHA["prizes"][i]
                raw_name = p.get("name", "")
                clean_name = raw_name.split("：")[-1] if "：" in raw_name else raw_name
                
                display_data.append({
                    "rank": ranks[i],
                    "display_name": clean_name,
                    "reward_type": type_map_ui.get(p.get("type", "gold"), "金幣"),
                    "val": p.get("val", 100),
                    "prob": p.get("prob", 0)
                })
                
            display_df = pd.DataFrame(display_data)
            edited_gacha = st.data_editor(
                display_df,
                num_rows="fixed",
                column_config={
                    "rank": st.column_config.TextColumn("獎項級別", disabled=True),
                    "display_name": st.column_config.TextColumn("顯示名稱 (自由輸入)", required=True),
                    "reward_type": st.column_config.SelectboxColumn("獎勵類型", options=["勳章", "金幣", "道具包"], required=True),
                    "val": st.column_config.NumberColumn("數量", min_value=1, required=True),
                    "prob": st.column_config.NumberColumn("機率(%)", min_value=0, max_value=100, required=True)
                },
                hide_index=True,
                key="parent_gacha_editor"
            )
            
            if st.button("💾 儲存扭蛋機設定"):
                if round(edited_gacha['prob'].sum()) != 100:
                    st.error(f"❌ 機率總和必須等於 100%！目前為 {edited_gacha['prob'].sum()}%")
                else:
                    final_prizes = []
                    for _, row in edited_gacha.iterrows():
                        final_prizes.append({
                            "name": f"{row['rank']}：{row['display_name']}",
                            "prob": row["prob"],
                            "type": type_map_db[row["reward_type"]],
                            "val": row["val"]
                        })
                    p_data["gacha"] = {"cost": g_cost, "prizes": final_prizes}
                    save_parent_info(p_id, p_data); st.success("扭蛋機設定已儲存！"); st.rerun()

    with t3:
        bank_limit = p_data.get("bank_limit")
        if bank_limit is None: bank_limit = admin_cfg.get("default_bank_limit", 3)
        
        st.info(f"📂 目前已建立字庫：{len(p_data['custom_banks'])} / {bank_limit}")
        
        if len(p_data['custom_banks']) < bank_limit:
            with st.expander("➕ 新增自建字庫", expanded=False):
                n_bank_name = st.text_input("字庫名稱 (例如：康軒第三課)")
                if st.button("確認新增字庫"):
                    if n_bank_name.strip():
                        new_id = str(int(time.time()))
                        p_data['custom_banks'].append({"id": new_id, "name": n_bank_name.strip()})
                        save_parent_info(p_id, p_data)
                        st.success("✅ 字庫建立成功！"); st.rerun()
                    else: st.error("名稱不可為空")

        if p_data['custom_banks']:
            sel_bank_id = st.selectbox("選擇要管理/編輯的字庫", [b["id"] for b in p_data["custom_banks"]], format_func=lambda x: next(b["name"] for b in p_data["custom_banks"] if b["id"]==x))
            c_file = f"vocab_custom_{p_id}_{sel_bank_id}.csv"
            
            st.markdown("---")
            st.subheader("⚙️ 字庫設定")
            col_set1, col_set2 = st.columns(2)
            with col_set1:
                current_name = next(b["name"] for b in p_data["custom_banks"] if b["id"]==sel_bank_id)
                new_name = st.text_input("修改字庫名稱", value=current_name)
                if st.button("💾 儲存新名稱"):
                    if new_name.strip():
                        for b in p_data["custom_banks"]:
                            if b["id"] == sel_bank_id:
                                b["name"] = new_name.strip()
                        save_parent_info(p_id, p_data)
                        st.success("✅ 名稱已更新！")
                        st.rerun()
                    else: st.error("名稱不能為空！")
            with col_set2:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("🧹 一鍵清空此字庫內容", type="secondary"):
                    empty_df = pd.DataFrame(columns=["en", "zh", "hint"])
                    save_vocab_db(f"custom_{p_id}_{sel_bank_id}", empty_df)
                    st.success("✅ 字庫內容已清空！")
                    st.rerun()
            
            st.markdown("---")
            st.subheader("🤝 題庫分享與匯入 (Share & Import)")
            colA, colB = st.columns(2)
            with colA:
                st.markdown("**📤 分享目前選取的題庫**")
                shares = get_shares()
                my_code = None
                for k, v in shares.items():
                    if isinstance(v, dict) and v.get("p_id") == p_id and v.get("bank_id") == sel_bank_id: my_code = k
                    elif isinstance(v, str) and v == p_id and sel_bank_id == "1": my_code = k 
                
                if my_code:
                    st.success(f"專屬分享碼：**{my_code}**")
                    st.caption("將此代碼傳給其他家庭，即可匯入您的單字庫！")
                else:
                    if st.button("產生專屬分享碼"):
                        new_code = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=5))
                        shares[new_code] = {"p_id": p_id, "bank_id": sel_bank_id}
                        save_shares(shares); st.rerun()
            with colB:
                st.markdown("**📥 匯入他人題庫**")
                import_code = st.text_input("輸入分享碼 (將附加至目前選取的字庫)")
                if st.button("確認匯入"):
                    import_code = import_code.strip().upper()
                    shares = get_shares()
                    if import_code in shares:
                        share_data = shares[import_code]
                        if isinstance(share_data, str): source_p_id, source_bank_id = share_data, "1"
                        else: source_p_id, source_bank_id = share_data["p_id"], share_data["bank_id"]

                        src_data = load_vocab_db(f"custom_{source_p_id}_{source_bank_id}")
                        if src_data:
                            src_df = pd.DataFrame(src_data)
                            curr_data = load_vocab_db(f"custom_{p_id}_{sel_bank_id}")
                            if curr_data:
                                curr_df = pd.DataFrame(curr_data)
                                merged_df = pd.concat([curr_df, src_df]).drop_duplicates(subset=['en'], keep='last')
                            else: merged_df = src_df
                            save_vocab_db(f"custom_{p_id}_{sel_bank_id}", merged_df)
                            st.success("✅ 匯入成功！已將對方的單字加入您的題庫中。"); st.rerun()
                        else: st.error("對方的題庫目前是空的喔！")
                    else: st.error("❌ 無效的分享碼")

            st.markdown("---")
            v_data = load_vocab_db(f"custom_{p_id}_{sel_bank_id}")
            v_df = pd.DataFrame(v_data) if v_data else pd.DataFrame(columns=["en", "zh", "hint"])
            
            if st.button("🔄 修復：互換【中文】與【提示】欄位", key=f"swap_parent_{sel_bank_id}"):
                if not v_df.empty and 'zh' in v_df.columns and 'hint' in v_df.columns:
                    temp_zh = v_df['zh'].copy()
                    v_df['zh'] = v_df['hint']
                    v_df['hint'] = temp_zh
                    save_vocab_db(f"custom_{p_id}_{sel_bank_id}", v_df)
                    st.success("✅ 欄位互換成功！")
                    st.rerun()
                    
            edited_df = st.data_editor(v_df, num_rows="dynamic", use_container_width=True,
                                       column_order=["en", "zh", "hint"],
                                       column_config={
                                           "en": st.column_config.TextColumn("英文單字 (en)", required=True),
                                           "zh": st.column_config.TextColumn("正確中文/答案 (zh)", required=True),
                                           "hint": st.column_config.TextColumn("輔助提示/詞性 (hint)")
                                       })
            if st.button("💾 儲存自訂單字庫", type="primary"):
                if "sentence" in edited_df.columns: edited_df = edited_df.drop(columns=["sentence"])
                save_vocab_db(f"custom_{p_id}_{sel_bank_id}", edited_df)
                st.success("您的自訂題庫已更新成功！")
            
    with t4:
        st.subheader("🌟 夥伴寶可夢進化路線")
        st.info("孩子達到指定等級後，夥伴寶可夢就會自動進化！可以拿這個當作他們的目標。")
        for h_k, h_v in CHARACTERS.items():
            st.markdown(f"**{h_k} 家族**")
            h_cols = st.columns(min(len(h_v["stages"]), 5))
            for idx, (h_id, h_name) in enumerate(h_v["stages"]):
                lvl_req = 1 if idx==0 else (5 if idx==1 else 10)
                h_cols[idx].image(f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/versions/generation-v/black-white/animated/{h_id}.gif", caption=f"Lv.{lvl_req} {h_name}")
        
    with t5:
        st.subheader("📝 官方字庫糾錯回饋")
        st.info("若您發現官方字庫 (國小/國中/高中/多益) 中有翻譯不精準或錯誤的地方，請填寫此表單。審核通過後，該單字將會全球同步更新！")
        with st.form("feedback_form"):
            fb_bank = st.selectbox("回報目標字庫", ["國小", "國中", "高中", "多益"])
            fb_en = st.text_input("英文單字 (En)")
            fb_zh = st.text_input("正確中文 (Zh)")
            fb_hint = st.text_input("正確提示 (Hint)")
            if st.form_submit_button("送出審核"):
                if fb_en.strip() and fb_zh.strip():
                    fbs = db.reference("feedbacks").get() or []
                    fbs.append({
                        "id": str(int(time.time()*1000)), "p_id": p_id, "bank": fb_bank,
                        "en": fb_en.strip(), "zh": fb_zh.strip(), "hint": fb_hint.strip(),
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    })
                    db.reference("feedbacks").set(fbs)
                    st.success("✅ 回報已送出！非常感謝您的協助！")
                else: st.error("英文與中文欄位不可為空！")

# ==================== GM 控制台 ====================
elif st.session_state.page == 'admin':
    st.markdown("<h1 style='text-align: center; color:#c0392b;'>👑 系統最高管理員中心</h1><hr>", unsafe_allow_html=True)
    if st.button("⬅️ 登出並返回大廳"): st.session_state.page = 'login'; st.rerun()
    st.markdown("---")
    
    t1, t5, t2, t3, t4 = st.tabs(["👨‍👩‍👧 租戶管理", "⚙️ 全域數值設定", "📋 回饋審核", "📚 題庫增訂", "⚙ 系統設定"])
    admin_cfg = get_admin()
    
    with t1:
        p_db = db.reference("parents").get() or {}
        u_db = db.reference("users").get() or {}
        
        if not p_db: st.info("目前沒有任何家庭註冊。")
        for p_id, p_info in p_db.items():
            with st.expander(f"🏠 家庭帳號：{p_id}"):
                c1, c2, c3 = st.columns(3)
                new_pwd = c1.text_input("修改密碼", value=p_info['password'], key=f"apwd_{p_id}")
                
                h_limit_val = p_info.get('hero_limit')
                if h_limit_val is None: h_limit_val = admin_cfg.get("default_hero_limit", 3)
                
                b_limit_val = p_info.get('bank_limit')
                if b_limit_val is None: b_limit_val = admin_cfg.get("default_bank_limit", 3)
                    
                new_h_limit = c2.number_input("設定帳號上限", min_value=1, value=h_limit_val, key=f"hlim_{p_id}")
                new_b_limit = c3.number_input("設定字庫上限", min_value=1, value=b_limit_val, key=f"blim_{p_id}")
                
                if new_pwd != p_info['password'] or new_h_limit != p_info.get('hero_limit') or new_b_limit != p_info.get('bank_limit'):
                    p_info['password'] = new_pwd
                    p_info['hero_limit'] = new_h_limit
                    p_info['bank_limit'] = new_b_limit
                    save_parent_info(p_id, p_info); st.rerun()
                
                heroes = {k: v for k, v in u_db.items() if v.get("parent") == p_id}
                st.markdown(f"**旗下訓練家 ({len(heroes)})：**")
                for u_key, u_info in heroes.items():
                    d = load_user_data(u_key)
                    cols = st.columns([1, 1, 1, 1, 2])
                    cols[0].write(u_info['name'])
                    cols[1].write(f"Lv.{d['level']}")
                    cols[2].write(f"勳章: {d['medals']}")
                    cols[3].write(d['difficulty'])
                    if cols[4].button("🗑️ 刪除", key=f"gm_d_{u_key}"):
                        delete_user(u_key); st.rerun()
                        
                st.markdown("---")
                if st.button(f"🚨 刪除此家庭 (包含底下所有帳號)", key=f"gm_dp_{p_id}", type="primary"):
                    for u_key in heroes: delete_user(u_key)
                    db.reference(f"parents/{p_id}").delete()
                    get_parent_info.clear() 
                    st.rerun()

    with t5:
        st.subheader("⚙️ 系統全域遊戲參數預設值")
        # 🌟 修正：確保 GM 後台遇到舊資料時，也能自動補齊新增的卷軸欄位
        store = DEFAULT_STORE.copy()
        if isinstance(admin_cfg.get("store_prices"), dict): store.update(admin_cfg["store_prices"])
        
        gacha = admin_cfg.get("gacha", DEFAULT_GACHA)

        with st.expander("📖 圖鑑世代解鎖階段設定", expanded=True):
            st.info("透過下方開關控制全球玩家可以遭遇的寶可夢數量。階段越高，開放的世代與數量越多。")
            
            # 詳細說明 6 個階段的狀態
            stage_options = {
                1: "階段 1：初代經典 (No.1 ~ No.151) - 僅開放關都地區",
                2: "階段 2：金銀復古 (No.1 ~ No.251) - 新增城都地區",
                3: "階段 3：寶石冒險 (No.1 ~ No.386) - 新增豐緣地區",
                4: "階段 4：珍珠鑽石 (No.1 ~ No.493) - 新增神奧地區",
                5: "階段 5：黑白前期 (No.1 ~ No.570) - 新增合眾地區",
                6: "階段 6：全圖鑑解鎖 (No.1 ~ No.649) - 649 隻全數開放"
            }
            
            current_stage = admin_cfg.get("unlocked_stage", 1)
            
            # 使用互斥開關 (Radio) 來取代拉桿
            new_stage = st.radio(
                "請選擇要開放的圖鑑階段：",
                options=list(stage_options.keys()),
                format_func=lambda x: stage_options[x],
                index=current_stage - 1
            )
            
            if st.button("💾 儲存圖鑑階段"):
                admin_cfg["unlocked_stage"] = new_stage
                save_admin(admin_cfg)
                st.success(f"✅ 設定已更新！目前伺服器狀態為：{stage_options[new_stage]}")
                st.rerun()
        
        with st.expander("⏳ 系統遊玩時間預設值", expanded=True):
            new_play_time = st.number_input("全域預設每日遊玩時間 (分鐘)", min_value=1, value=admin_cfg.get("default_play_time_min", 30))
            if st.button("💾 儲存遊玩時間設定"):
                admin_cfg["default_play_time_min"] = new_play_time
                save_admin(admin_cfg); st.success("儲存成功！"); st.rerun()
                
        with st.expander("⚔ 戰鬥掉落率預設值", expanded=True):
            raw_rates = admin_cfg.get("game_rates", DEFAULT_RATES)
            if "簡單" not in raw_rates:
                raw_rates = {"簡單": raw_rates, "中等": {k:v*2 for k,v in raw_rates.items()}, "困難": {k:v*3 for k,v in raw_rates.items()}}
            
            diff_tabs = st.tabs(["🟢 簡單", "🟡 中等", "🔴 困難"])
            diff_keys = ["簡單", "中等", "困難"]
            new_rates = {}
            for i, d_key in enumerate(diff_keys):
                with diff_tabs[i]:
                    r1, r2 = st.columns(2)
                    cur = raw_rates[d_key]
                    new_rates[d_key] = {
                        "normal_exp": r1.number_input(f"一般怪 EXP ({d_key})", min_value=1, value=cur.get("normal_exp", 5), key=f"gm_n_exp_{d_key}"),
                        "normal_gold": r2.number_input(f"一般怪 金幣 ({d_key})", min_value=1, value=cur.get("normal_gold", 10), key=f"gm_n_gld_{d_key}"),
                        "boss_exp": r1.number_input(f"Boss EXP ({d_key})", min_value=1, value=cur.get("boss_exp", 10), key=f"gm_b_exp_{d_key}"),
                        "boss_gold": r2.number_input(f"Boss 金幣 ({d_key})", min_value=1, value=cur.get("boss_gold", 50), key=f"gm_b_gld_{d_key}"),
                        "boss_medal": r1.number_input(f"Boss 勳章 ({d_key})", min_value=0, value=cur.get("boss_medal", 1), key=f"gm_b_mdl_{d_key}")
                    }
            if st.button("💾 儲存全域戰鬥掉落率"):
                admin_cfg["game_rates"] = new_rates
                save_admin(admin_cfg); st.success("儲存成功！"); st.rerun()
                
        with st.expander("🏪 商店物價預設值", expanded=True):
            s1, s2, s3, s4 = st.columns(4)
            p_pot = s1.number_input("藥水價格", min_value=1, value=store.get("potion", 200))
            p_shi = s2.number_input("護盾價格", min_value=1, value=store.get("shield", 250))
            p_mag = s3.number_input("放大鏡價格", min_value=1, value=store.get("magnifier", 100))
            p_scr = s4.number_input("📜 變形卷軸", min_value=1, value=store.get("scroll", 500))
            if st.button("💾 儲存全域商店物價"):
                admin_cfg["store_prices"] = {"potion": p_pot, "shield": p_shi, "magnifier": p_mag, "scroll": p_scr}
                save_admin(admin_cfg); st.success("儲存成功！"); st.rerun()

        with st.expander("🎰 扭蛋機預設值", expanded=True):
            g_cost = st.number_input("扭蛋單次花費 (G)", min_value=10, value=gacha.get("cost", 300), key="gm_g_cost")
            st.caption("設定各獎項內容與機率 (總和必須為 100%)")
            st.caption("您可以自由設定獎品名稱、發放的類型(勳章/金幣/道具包)、給予的數量，以及中獎機率。")
            
            current_prizes = gacha.get("prizes", DEFAULT_GACHA["prizes"])
            display_data = []
            ranks = ["特獎", "一獎", "二獎", "三獎", "四獎", "五獎"]
            type_map_ui = {"medal": "勳章", "gold": "金幣", "item": "道具包"}
            type_map_db = {"勳章": "medal", "金幣": "gold", "道具包": "item"}
            
            for i in range(6):
                p = current_prizes[i] if i < len(current_prizes) else DEFAULT_GACHA["prizes"][i]
                raw_name = p.get("name", "")
                clean_name = raw_name.split("：")[-1] if "：" in raw_name else raw_name
                
                display_data.append({
                    "rank": ranks[i],
                    "display_name": clean_name,
                    "reward_type": type_map_ui.get(p.get("type", "gold"), "金幣"),
                    "val": p.get("val", 100),
                    "prob": p.get("prob", 0)
                })
                
            display_df = pd.DataFrame(display_data)
            edited_gacha = st.data_editor(
                display_df,
                num_rows="fixed",
                column_config={
                    "rank": st.column_config.TextColumn("獎項級別", disabled=True),
                    "display_name": st.column_config.TextColumn("顯示名稱 (自由輸入)", required=True),
                    "reward_type": st.column_config.SelectboxColumn("獎勵類型", options=["勳章", "金幣", "道具包"], required=True),
                    "val": st.column_config.NumberColumn("數量", min_value=1, required=True),
                    "prob": st.column_config.NumberColumn("機率(%)", min_value=0, max_value=100, required=True)
                },
                hide_index=True,
                key="gm_gacha_editor"
            )
            
            if st.button("💾 儲存全域扭蛋機設定"):
                if round(edited_gacha['prob'].sum()) != 100:
                    st.error(f"❌ 機率總和必須等於 100%！目前為 {edited_gacha['prob'].sum()}%")
                else:
                    final_prizes = []
                    for _, row in edited_gacha.iterrows():
                        final_prizes.append({
                            "name": f"{row['rank']}：{row['display_name']}",
                            "prob": row["prob"],
                            "type": type_map_db[row["reward_type"]],
                            "val": row["val"]
                        })
                    admin_cfg["gacha"] = {"cost": g_cost, "prizes": final_prizes}
                    save_admin(admin_cfg); st.success("扭蛋機設定已儲存！"); st.rerun()

    with t2:
        st.subheader("📋 官方字庫回饋審核")
        fbs = db.reference("feedbacks").get() or []
        if not fbs: st.info("目前沒有待審核的回饋單。")
        for fb in fbs:
            with st.container():
                cols = st.columns([1, 1, 1, 1, 1, 2])
                cols[0].write(f"**[{fb['bank']}]**")
                cols[1].write(fb['en'])
                cols[2].write(fb['zh'])
                cols[3].write(fb['hint'])
                cols[4].caption(f"By {fb['p_id']}")
                c_btn1, c_btn2 = cols[5].columns(2)
                
                if c_btn1.button("✅ 核准", key=f"fb_app_{fb['id']}"):
                    bank_key = fb['bank']
                    v_data = load_vocab_db(bank_key)
                    if v_data: df = pd.DataFrame(v_data)
                    else: df = pd.DataFrame(columns=["en", "zh", "hint"])
                    
                    if fb['en'] in df['en'].values:
                        idx = df.index[df['en'] == fb['en']].tolist()[0]
                        df.at[idx, 'zh'] = fb['zh']
                        df.at[idx, 'hint'] = fb['hint']
                    else:
                        new_row = pd.DataFrame([{"en": fb['en'], "zh": fb['zh'], "hint": fb['hint']}])
                        df = pd.concat([df, new_row], ignore_index=True)
                    save_vocab_db(bank_key, df)
                    
                    fbs = [f for f in fbs if f['id'] != fb['id']]
                    db.reference("feedbacks").set(fbs)
                    st.success("✅ 審核通過，官方字庫已更新！")
                    st.rerun()
                if c_btn2.button("❌ 拒絕", key=f"fb_rej_{fb['id']}"):
                    fbs = [f for f in fbs if f['id'] != fb['id']]
                    db.reference("feedbacks").set(fbs)
                    st.rerun()

    with t3:
        st.subheader("編輯全域單字庫")
        edit_bank = st.radio("選擇要編輯的題庫", ["國小", "國中", "高中", "多益"], horizontal=True)
        v_data = load_vocab_db(edit_bank)
        v_df = pd.DataFrame(v_data) if v_data else pd.DataFrame(columns=["en", "zh", "hint"])
        
        if st.button(f"🔄 修復：互換【{edit_bank}】的中文與提示欄位", key=f"swap_gm_{edit_bank}"):
            if not v_df.empty and 'zh' in v_df.columns and 'hint' in v_df.columns:
                temp_zh = v_df['zh'].copy()
                v_df['zh'] = v_df['hint']
                v_df['hint'] = temp_zh
                save_vocab_db(edit_bank, v_df)
                st.success("✅ 欄位互換成功！")
                st.rerun()
                
        edited_df = st.data_editor(v_df, num_rows="dynamic", use_container_width=True,
                                   column_order=["en", "zh", "hint"],
                                   column_config={
                                       "en": st.column_config.TextColumn("英文單字 (en)", required=True),
                                       "zh": st.column_config.TextColumn("正確中文/答案 (zh)", required=True),
                                       "hint": st.column_config.TextColumn("輔助提示/詞性 (hint)")
                                   })
        if st.button("💾 儲存題庫修改", type="primary"):
            if "sentence" in edited_df.columns: edited_df = edited_df.drop(columns=["sentence"])
            save_vocab_db(edit_bank, edited_df)
            st.success(f"【{edit_bank}】題庫更新成功！")
            
    with t4:
        st.subheader("系統安全設定")
        with st.form("admin_settings"):
            new_a_id = st.text_input("GM 帳號", value=admin_cfg.get("admin_id", "admin"))
            new_a_pwd = st.text_input("GM 密碼", value=admin_cfg.get("password", "1234"), type="password")
            new_h_limit = st.number_input("全域預設帳號上限", min_value=1, value=admin_cfg.get("default_hero_limit", 3))
            new_b_limit = st.number_input("全域預設字庫上限", min_value=1, value=admin_cfg.get("default_bank_limit", 3))
            new_r_limit = st.number_input("全域預設家庭獎勵兌換上限", min_value=1, value=admin_cfg.get("default_reward_limit", 99))
            if st.form_submit_button("儲存系統設定"):
                if new_a_id.strip() == "":
                    st.error("帳號不可為空！")
                else:
                    admin_cfg["admin_id"] = new_a_id.strip()
                    admin_cfg["password"] = new_a_pwd
                    admin_cfg["default_hero_limit"] = new_h_limit
                    admin_cfg["default_bank_limit"] = new_b_limit
                    admin_cfg["default_reward_limit"] = new_r_limit
                    save_admin(admin_cfg); st.success("系統設定已儲存！"); st.rerun()
