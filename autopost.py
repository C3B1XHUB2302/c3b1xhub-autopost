# ============================================================
#  C3B1XHUB — Auto Post Manager
#  Features: Discord OAuth2, Admin Panel, Vercel Support
# ============================================================
from flask import Flask, render_template_string, request, redirect, flash, jsonify, session
import json, time, threading, os, requests, secrets
import psycopg2, psycopg2.extras
from functools import wraps
from datetime import datetime
from urllib.parse import urlencode

app = Flask(__name__)

@app.template_filter('datetimeformat')
def datetimeformat(value):
    return datetime.fromtimestamp(int(value)).strftime('%d %b %Y %H:%M')

# =================== ENVIRONMENT / CONFIG ===================
IS_VERCEL  = bool(os.environ.get('VERCEL'))
BASE_DIR   = '/tmp' if IS_VERCEL else '.'
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
DATABASE_URL = os.environ.get('DATABASE_URL', '')

app.secret_key = os.environ.get('SECRET_KEY', 'c3b1xhub-dev-change-this-in-production')

DISCORD_CLIENT_ID     = os.environ.get('DISCORD_CLIENT_ID', '')
DISCORD_CLIENT_SECRET = os.environ.get('DISCORD_CLIENT_SECRET', '')
DISCORD_REDIRECT_URI  = os.environ.get('DISCORD_REDIRECT_URI', 'http://localhost:5000/auth/discord/callback')
DISCORD_LOG_WEBHOOK   = os.environ.get('DISCORD_LOG_WEBHOOK', '')
ADMIN_IDS             = [x.strip() for x in os.environ.get('ADMIN_DISCORD_IDS', '').split(',') if x.strip()]

# =================== BOT CONFIG ===================
config = {"tokens": [], "current_token_index": -1}
config_loaded = False

def load_config():
    global config, config_loaded
    if config_loaded:
        return
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r') as f:
            try:
                loaded = json.load(f)
                if 'token' in loaded and 'channels' in loaded:
                    new_token = {
                        "name": "Default Bot Token",
                        "token": loaded.get("token", ""),
                        "use_webhook": loaded.get("use_webhook", False),
                        "webhook_url": loaded.get("webhook_url", ""),
                        "channels": loaded.get("channels", []),
                        "posting_active": False
                    }
                    config["tokens"].append(new_token)
                    config["current_token_index"] = 0
                    save_config()
                else:
                    config.update(loaded)
                    if not (0 <= config["current_token_index"] < len(config["tokens"])):
                        config["current_token_index"] = 0 if config["tokens"] else -1
            except json.JSONDecodeError:
                pass
    config_loaded = True

def save_config():
    os.makedirs(os.path.dirname(CONFIG_PATH) if os.path.dirname(CONFIG_PATH) else '.', exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=4)

def get_current_token_data():
    load_config()
    if 0 <= config["current_token_index"] < len(config["tokens"]):
        return config["tokens"][config["current_token_index"]]
    return None

def send_log(message, channel_id=None, success=True, webhook_url=None):
    if webhook_url:
        try:
            now = time.strftime("%d %B %Y  %I:%M:%S %p")
            embed = {
                "title": "<a:ms_discord:1129069176917610619> Auto Post Discord",
                "description": "> **Details Info**",
                "color": 65280 if success else 16711680,
                "fields": [
                    {"name": "Status Log",    "value": "> Success" if success else "> Failed"},
                    {"name": "Date Time",     "value": f"> {now}"},
                    {"name": "Channel Target","value": f"> <#{channel_id}>" if channel_id else "> Unknown"},
                    {"name": "Status Message","value": f"> {message}"}
                ],
                "footer": {"text": "Auto Post By C3B1XHUB."}
            }
            requests.post(webhook_url, json={"embeds": [embed]}, timeout=5)
        except Exception as e:
            print(f"[LOG ERROR] {e}")

posting_threads = {}

def post_to_channel(token_data, ch):
    while token_data.get("posting_active", False):
        try:
            url     = f"https://discord.com/api/v10/channels/{ch['id']}/messages"
            headers = {"Authorization": token_data["token"], "Content-Type": "application/json"}
            res     = requests.post(url, headers=headers, json={"content": ch["message"]})
            success = res.status_code in (200, 204)
            if token_data.get("use_webhook") and token_data.get("webhook_url"):
                send_log(f"Pesan ke <#{ch['id']}> {'berhasil' if success else 'gagal'} [{res.status_code}].",
                         ch['id'], success, token_data["webhook_url"])
        except Exception as e:
            if token_data.get("use_webhook") and token_data.get("webhook_url"):
                send_log(f"Error: {e}", ch['id'], False, token_data["webhook_url"])
        if not token_data.get("posting_active", False):
            break
        time.sleep(ch["interval"])

def auto_post(token_data):
    token_name = token_data['name']
    if token_name in posting_threads:
        del posting_threads[token_name]
    channel_threads = {}
    for ch in token_data["channels"]:
        t = threading.Thread(target=post_to_channel, args=(token_data, ch), daemon=True)
        channel_threads[ch['id']] = t
        t.start()
    posting_threads[token_name] = channel_threads

# ✅ GANTI TOTAL (Lines 161-203) dengan ini:

# =================== DATABASE ===================
def get_db():
    conn = psycopg2.connect(DATABASE_URL)

    class _CursorWrapper:
        def __init__(self, cur):
            self._c = cur
        def fetchone(self):  return self._c.fetchone()
        def fetchall(self):  return self._c.fetchall()
        def __getitem__(self, k): return self._c.fetchall()[k]

    class _ConnWrapper:
        def __init__(self, c):
            self._conn = c
        def execute(self, sql, params=None):
            cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params)
            return _CursorWrapper(cur)
        def commit(self):  self._conn.commit()
        def close(self):   self._conn.close()

    return _ConnWrapper(conn)

def init_db():
    if not DATABASE_URL:
        return
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            discord_id      TEXT PRIMARY KEY,
            username        TEXT,
            global_name     TEXT,
            avatar          TEXT,
            login_count     INTEGER DEFAULT 1,
            first_login     DOUBLE PRECISION,
            last_login      DOUBLE PRECISION,
            is_banned       INTEGER DEFAULT 0,
            ban_reason      TEXT DEFAULT '',
            premium_type    TEXT DEFAULT NULL,
            premium_expires DOUBLE PRECISION DEFAULT 0
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS premium_log (
            id          SERIAL PRIMARY KEY,
            discord_id  TEXT,
            username    TEXT,
            plan        TEXT,
            price       INTEGER,
            granted_by  TEXT,
            expires_at  DOUBLE PRECISION,
            created_at  DOUBLE PRECISION
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS activity_log (
            id         SERIAL PRIMARY KEY,
            discord_id TEXT,
            username   TEXT,
            action     TEXT,
            ip         TEXT,
            timestamp  DOUBLE PRECISION
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# =================== PREMIUM HELPERS ===================
PREMIUM_PLANS = {
    '1day':   {'label': '1 Hari',   'emoji': '⚡', 'price': 5000,   'duration': 86400,    'price_fmt': 'Rp 5.000'},
    '1week':  {'label': '1 Minggu', 'emoji': '🔥', 'price': 20000,  'duration': 604800,   'price_fmt': 'Rp 20.000'},
    '1month': {'label': '1 Bulan',  'emoji': '👑', 'price': 100000, 'duration': 2592000,  'price_fmt': 'Rp 100.000'},
}
FREE_TOKEN_LIMIT   = 1
FREE_CHANNEL_LIMIT = 5

def is_premium(discord_id):
    if not discord_id or discord_id == 'local_dev':
        return True  # local dev = bypass semua limit
    conn = get_db()
    row = conn.execute('SELECT premium_expires FROM users WHERE discord_id=%s', (discord_id,)).fetchone()
    conn.close()
    if not row:
        return False
    return bool(row['premium_expires'] and row['premium_expires'] > time.time())

def get_premium_info(discord_id):
    if not discord_id or discord_id == 'local_dev':
        return {'type': 'admin', 'label': 'Admin', 'expires': None, 'active': True}
    conn = get_db()
    row = conn.execute('SELECT premium_type, premium_expires FROM users WHERE discord_id=%s', (discord_id,)).fetchone()
    conn.close()
    if not row or not row['premium_expires'] or row['premium_expires'] <= time.time():
        return None
    plan = PREMIUM_PLANS.get(row['premium_type'], {})
    return {
        'type':    row['premium_type'],
        'label':   plan.get('label', row['premium_type']),
        'emoji':   plan.get('emoji', '⭐'),
        'expires': row['premium_expires'],
        'active':  True
    }

# =================== DISCORD WEBHOOK LOG ===================
def send_discord_log(action, user_data, ip='', extra_note=''):
    if not DISCORD_LOG_WEBHOOK:
        return
    color_map = {
        'register': 0x22c55e,
        'login':    0x3b82f6,
        'banned':   0xe8000d,
        'ban':      0xf59e0b,
        'unban':    0x22c55e,
    }
    label_map = {
        'register': '🆕 New User Registered',
        'login':    '🔑 User Login',
        'banned':   '🚫 Banned Login Attempt',
        'ban':      '🔨 Admin Banned User',
        'unban':    '✅ Admin Unbanned User',
    }
    did      = str(user_data.get('discord_id', ''))
    username = user_data.get('username', 'Unknown')
    avatar   = user_data.get('avatar')
    embed = {
        "title": label_map.get(action, action),
        "color": color_map.get(action, 0x888888),
        "fields": [
            {"name": "👤 Username",   "value": f"`{username}`",   "inline": True},
            {"name": "🆔 Discord ID", "value": f"`{did}`",        "inline": True},
            {"name": "🌐 IP",         "value": f"`{ip or 'N/A'}`","inline": True},
            {"name": "🕐 Time",       "value": f"`{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`", "inline": False},
        ],
        "footer": {"text": "C3B1XHUB — Auto Post Manager"},
        "timestamp": datetime.utcnow().isoformat() + 'Z'
    }
    if avatar:
        embed['thumbnail'] = {"url": f"https://cdn.discordapp.com/avatars/{did}/{avatar}.png?size=64"}
    if extra_note:
        embed['fields'].append({"name": "📝 Note", "value": extra_note, "inline": False})
    try:
        requests.post(DISCORD_LOG_WEBHOOK, json={"embeds": [embed]}, timeout=5)
    except:
        pass

# =================== AUTH DECORATORS ===================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Dev mode: no OAuth configured → auto-login as local dev
        if not DISCORD_CLIENT_ID:
            if 'user' not in session:
                session['user'] = {
                    'discord_id': 'local_dev',
                    'username': 'Local Developer',
                    'avatar': None,
                    'is_admin': True
                }
        if 'user' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect('/login')
        # Dev mode = always admin
        if not DISCORD_CLIENT_ID:
            return f(*args, **kwargs)
        if not ADMIN_IDS or session['user'].get('discord_id') not in ADMIN_IDS:
            flash('Admin access required.', 'danger')
            return redirect('/')
        return f(*args, **kwargs)
    return decorated

# =================== AUTH ROUTES ===================
@app.route('/login')
def login_page():
    if 'user' in session:
        return redirect('/')
    return render_template_string(login_template, client_id_set=bool(DISCORD_CLIENT_ID))

@app.route('/auth/discord')
def auth_discord():
    if not DISCORD_CLIENT_ID:
        flash('Discord OAuth not configured. Set DISCORD_CLIENT_ID env var.', 'danger')
        return redirect('/login')
    state = secrets.token_urlsafe(16)
    session['oauth_state'] = state
    params = {
        'client_id': DISCORD_CLIENT_ID,
        'redirect_uri': DISCORD_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'identify',
        'state': state
    }
    return redirect(f"https://discord.com/api/oauth2/authorize?{urlencode(params)}")

@app.route('/auth/discord/callback')
def auth_callback():
    if request.args.get('error'):
        flash('Authorization cancelled.', 'warning')
        return redirect('/login')

    code  = request.args.get('code')
    state = request.args.get('state')

    if not code or state != session.pop('oauth_state', None):
        flash('Invalid OAuth state. Please try again.', 'danger')
        return redirect('/login')

    token_res = requests.post(
        'https://discord.com/api/oauth2/token',
        data={
            'client_id': DISCORD_CLIENT_ID,
            'client_secret': DISCORD_CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': DISCORD_REDIRECT_URI
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=10
    )
    if not token_res.ok:
        flash('Discord authentication failed. Try again.', 'danger')
        return redirect('/login')

    access_token = token_res.json().get('access_token')
    user_res = requests.get(
        'https://discord.com/api/users/@me',
        headers={'Authorization': f'Bearer {access_token}'},
        timeout=10
    )
    if not user_res.ok:
        flash('Failed to retrieve Discord user info.', 'danger')
        return redirect('/login')

    d        = user_res.json()
    did      = d['id']
    username = d.get('global_name') or d.get('username', 'Unknown')
    avatar   = d.get('avatar')
    ip       = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()
    now      = time.time()

    conn     = get_db()
    existing = conn.execute('SELECT * FROM users WHERE discord_id=%s', (did,)).fetchone()

    if existing:
        if existing['is_banned']:
            conn.close()
            send_discord_log('banned', {'discord_id': did, 'username': username, 'avatar': avatar}, ip)
            flash('Your account has been suspended. Contact an admin.', 'danger')
            return redirect('/login')
        conn.execute(
            'UPDATE users SET username=%s, avatar=%s, last_login=%s, login_count=login_count+1 WHERE discord_id=%s',
            (username, avatar, now, did)
        )
        is_new = False
    else:
        conn.execute(
            'INSERT INTO users (discord_id, username, global_name, avatar, first_login, last_login) VALUES (%s,%s,%s,%s,%s,%s)',
            (did, username, d.get('username'), avatar, now, now)
        )
        is_new = True

    conn.execute(
        'INSERT INTO activity_log (discord_id, username, action, ip, timestamp) VALUES (%s,%s,%s,%s,%s)',
        (did, username, 'register' if is_new else 'login', ip, now)
    )
    conn.commit()
    conn.close()

    send_discord_log('register' if is_new else 'login',
                     {'discord_id': did, 'username': username, 'avatar': avatar}, ip)

    session['user'] = {
        'discord_id': did,
        'username': username,
        'avatar': avatar,
        'is_admin': did in ADMIN_IDS
    }
    flash(f"Welcome, {username}! 👋", 'success')
    return redirect('/')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# =================== ADMIN ROUTES ===================
@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    conn         = get_db()
    total_users  = conn.execute('SELECT COUNT(*) AS cnt FROM users').fetchone()['cnt']
    banned_users = conn.execute('SELECT COUNT(*) AS cnt FROM users WHERE is_banned=1').fetchone()['cnt']
    new_today    = conn.execute(
        "SELECT COUNT(*) AS cnt FROM activity_log WHERE timestamp>%s AND action='register'",
        (time.time() - 86400,)
    ).fetchone()['cnt']
    logins_today = conn.execute(
        'SELECT COUNT(*) AS cnt FROM activity_log WHERE timestamp>%s',
        (time.time() - 86400,)
    ).fetchone()['cnt']
    users = conn.execute('SELECT * FROM users ORDER BY last_login DESC').fetchall()
    logs  = conn.execute('SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT 60').fetchall()
    conn.close()
    load_config()
    return render_template_string(
        admin_template,
        total_users=total_users,
        banned_users=banned_users,
        new_today=new_today,
        logins_today=logins_today,
        users=users,
        logs=logs,
        config=config,
        ADMIN_IDS=ADMIN_IDS,
        datetime=datetime,
        now=time.time()
    )


@app.route('/admin/ban/<did>', methods=['POST'])
@login_required
@admin_required
def admin_ban(did):
    reason = request.form.get('reason', 'Violated terms of use')
    conn   = get_db()
    user   = conn.execute('SELECT * FROM users WHERE discord_id=?', (did,)).fetchone()
    conn.execute('UPDATE users SET is_banned=1, ban_reason=%s WHERE discord_id=%s', (reason, did))
    conn.commit()
    conn.close()
    if user:
        send_discord_log('ban', {'discord_id': did, 'username': user['username'], 'avatar': user['avatar']},
                         extra_note=f"Reason: {reason}")
    flash(f"User {did} has been banned.", 'success')
    return redirect('/admin')

@app.route('/admin/unban/<did>', methods=['POST'])
@login_required
@admin_required
def admin_unban(did):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE discord_id=%s', (did,)).fetchone()
    conn.execute("UPDATE users SET is_banned=0, ban_reason='' WHERE discord_id=%s", (did,))
    conn.commit()
    conn.close()
    if user:
        send_discord_log('unban', {'discord_id': did, 'username': user['username'], 'avatar': user['avatar']})
    flash(f"User {did} has been unbanned.", 'success')
    return redirect('/admin')

@app.route('/admin/delete/<did>', methods=['POST'])
@login_required
@admin_required
def admin_delete(did):
    conn = get_db()
    conn.execute('DELETE FROM users WHERE discord_id=%s', (did,))
    conn.execute('DELETE FROM activity_log WHERE discord_id=%s', (did,))
    conn.commit()
    conn.close()
    flash(f"User {did} has been deleted.", 'success')
    return redirect('/admin')

# =================== PREMIUM ADMIN ROUTES ===================
@app.route('/admin/set-premium/<did>', methods=['POST'])
@login_required
@admin_required
def admin_set_premium(did):
    plan = request.form.get('plan')
    if plan not in PREMIUM_PLANS:
        flash('Plan tidak valid.', 'danger')
        return redirect('/admin')
    p        = PREMIUM_PLANS[plan]
    expires  = time.time() + p['duration']
    admin_id = session.get('user', {}).get('discord_id', 'unknown')
    conn     = get_db()
    user_row = conn.execute('SELECT username FROM users WHERE discord_id=%s', (did,)).fetchone()
    uname    = user_row['username'] if user_row else 'Unknown'
    conn.execute('UPDATE users SET premium_type=%s, premium_expires=%s WHERE discord_id=%s', (plan, expires, did))
    conn.execute(
        'INSERT INTO premium_log (discord_id, username, plan, price, granted_by, expires_at, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',
        (did, uname, plan, p['price'], admin_id, expires, time.time())
    )
    conn.commit()
    conn.close()
    flash(f"✅ Premium <strong>{p['label']}</strong> ({p['price_fmt']}) berhasil diaktifkan untuk <strong>{uname}</strong>! Expires: {datetime.fromtimestamp(expires).strftime('%d %b %Y %H:%M')}", 'success')
    return redirect('/admin')

@app.route('/admin/remove-premium/<did>', methods=['POST'])
@login_required
@admin_required
def admin_remove_premium(did):
    conn     = get_db()
    user_row = conn.execute('SELECT username FROM users WHERE discord_id=%s', (did,)).fetchone()
    uname    = user_row['username'] if user_row else 'Unknown'
    conn.execute('UPDATE users SET premium_type=NULL, premium_expires=0 WHERE discord_id=%s', (did,))
    conn.commit()
    conn.close()
    flash(f"🗑️ Premium user <strong>{uname}</strong> telah dihapus.", 'warning')
    return redirect('/admin')

# =================== MAIN APP ROUTES ===================
@app.route("/", methods=["GET"])
@app.route("/index", methods=["GET"])
@login_required
def index():
    load_config()
    if not config["tokens"] or config["current_token_index"] == -1:
        return redirect("/add-new-token")
    current_token_data = get_current_token_data()
    user       = session.get('user', {})
    discord_id = user.get('discord_id', '')
    is_admin   = user.get('is_admin', False)
    prem_info  = get_premium_info(discord_id)
    user_is_premium = is_admin or is_premium(discord_id)
    token_limit   = None if user_is_premium else FREE_TOKEN_LIMIT
    channel_limit = None if user_is_premium else FREE_CHANNEL_LIMIT
    return render_template_string(
        html_template,
        config_json=json.dumps(current_token_data, indent=4),
        config=config, current_token_data=current_token_data,
        editing=False,
        prem_info=prem_info,
        user_is_premium=user_is_premium,
        token_limit=token_limit,
        channel_limit=channel_limit,
        premium_plans=PREMIUM_PLANS
    )

@app.route("/add-new-token", methods=["GET"])
@login_required
def add_new_token_page():
    load_config()
    return render_template_string(register_token_template,
                                  has_existing_tokens=len(config["tokens"]) > 0)

@app.route("/register-token", methods=["POST"])
@login_required
def register_token():
    global config
    user       = session.get('user', {})
    discord_id = user.get('discord_id', '')
    is_admin   = user.get('is_admin', False)

    # Cek limit token untuk user biasa (bukan admin & bukan premium)
    if not is_admin and not is_premium(discord_id):
        if len(config["tokens"]) >= FREE_TOKEN_LIMIT:
            flash(f"❌ Batas token tercapai! User biasa hanya bisa menambahkan {FREE_TOKEN_LIMIT} token. Upgrade ke <strong>Premium</strong> untuk unlimited token.", "danger")
            return redirect("/add-new-token")

    token_name  = request.form.get("token_name", "").strip()
    token_value = request.form.get("token", "").strip()
    if not token_name or not token_value:
        flash("Bot name and token are required.", "danger")
        return redirect("/add-new-token")
    if any(t['token'] == token_value for t in config["tokens"]):
        flash("Token already registered.", "warning")
        return redirect("/add-new-token")
    if any(t['name'] == token_name for t in config["tokens"]):
        token_name += f" ({len(config['tokens']) + 1})"
    new_token_data = {
        "name": token_name, "token": token_value,
        "use_webhook": False, "webhook_url": "",
        "channels": [], "posting_active": False
    }
    config["tokens"].append(new_token_data)
    config["current_token_index"] = len(config["tokens"]) - 1
    save_config()
    flash(f"Token '{token_name}' registered successfully!", "success")
    return redirect("/")

@app.route("/switch-token/<int:index>", methods=["GET"])
@login_required
def switch_token(index):
    global config
    load_config()
    if 0 <= index < len(config["tokens"]):
        config["current_token_index"] = index
        save_config()
        flash(f"Switched to: {config['tokens'][index]['name']}", "info")
    else:
        flash("Invalid token index.", "danger")
    return redirect("/")

@app.route("/save-config", methods=["POST"])
@login_required
def save():
    load_config()
    current_token_data = get_current_token_data()
    if not current_token_data:
        flash("Token not found.", "danger")
        return redirect("/add-new-token")
    if 'webhook_url' in request.form:
        current_token_data["webhook_url"]  = request.form.get("webhook_url", "").strip()
        current_token_data["use_webhook"]  = bool(request.form.get("use_webhook"))
        save_config()
        flash("Webhook settings saved!", "success")
        return redirect("/#webhook")
    if 'token' in request.form:
        token = request.form.get("token", "").strip()
        if token:
            if token != current_token_data["token"] and any(
                t['token'] == token and t['name'] != current_token_data['name']
                for t in config["tokens"]
            ):
                flash("Token already used by another bot.", "danger")
                return redirect("/#settings")
            current_token_data["token"] = token
            save_config()
            flash("Token saved!", "success")
        return redirect("/#settings")
    channel_id         = request.form.get("channel_id")
    message            = request.form.get("message")
    original_channel_id= request.form.get("original_channel_id")
    action             = request.form.get("action")
    if action != "remove" and (not channel_id or not message):
        flash("Channel ID and message are required.", "danger")
        return redirect("/#channels")
    try:
        hours   = int(request.form.get("hours", 0))
        minutes = int(request.form.get("minutes", 0))
        seconds = int(request.form.get("seconds", 0))
    except ValueError:
        hours = minutes = seconds = 0
    interval = hours * 3600 + minutes * 60 + seconds
    if action != "remove" and interval <= 0:
        flash("Interval must be at least 1 second.", "danger")
        return redirect("/#channels")
    if action == "add":
        user       = session.get('user', {})
        discord_id = user.get('discord_id', '')
        is_admin   = user.get('is_admin', False)

        # Cek limit channel untuk user biasa
        if not is_admin and not is_premium(discord_id):
            if len(current_token_data["channels"]) >= FREE_CHANNEL_LIMIT:
                flash(f"❌ Batas channel tercapai! User biasa hanya bisa menambahkan {FREE_CHANNEL_LIMIT} channel. Upgrade ke <strong>Premium</strong> untuk unlimited channel.", "danger")
                return redirect("/#channels")

        if any(ch['id'] == channel_id for ch in current_token_data["channels"]):
            flash(f"Channel {channel_id} already exists!", "danger")
            return redirect("/#channels")
        current_token_data["channels"].append({"id": channel_id, "message": message, "interval": interval})
        flash("Channel added!", "success")
    elif action == "edit":
        for ch in current_token_data["channels"]:
            if ch["id"] == original_channel_id:
                if channel_id != original_channel_id and any(
                    c['id'] == channel_id for c in current_token_data["channels"]
                ):
                    flash(f"Channel {channel_id} already exists!", "danger")
                    return redirect("/#channels")
                ch["id"] = channel_id; ch["message"] = message; ch["interval"] = interval
                flash("Channel updated!", "success")
                break
    elif action == "remove":
        before = len(current_token_data["channels"])
        current_token_data["channels"] = [
            ch for ch in current_token_data["channels"] if ch["id"] != channel_id
        ]
        flash("Channel removed!" if len(current_token_data["channels"]) < before else "Channel not found.", 
              "success" if len(current_token_data["channels"]) < before else "danger")
    save_config()
    return redirect("/#channels")

@app.route("/start", methods=["POST"])
@login_required
def start():
    load_config()
    current_token_data = get_current_token_data()
    if not current_token_data:
        flash("Token not found.", "danger")
        return redirect("/")
    if not current_token_data.get("posting_active", False):
        current_token_data["posting_active"] = True
        threading.Thread(target=auto_post, args=(current_token_data,), daemon=True).start()
        save_config()
        flash(f"Auto posting started for '{current_token_data['name']}'!", "success")
    return redirect("/")

@app.route("/stop", methods=["POST"])
@login_required
def stop():
    load_config()
    current_token_data = get_current_token_data()
    if not current_token_data:
        flash("Token not found.", "danger")
        return redirect("/")
    current_token_data["posting_active"] = False
    save_config()
    flash(f"Auto posting stopped for '{current_token_data['name']}'.", "info")
    return redirect("/")

@app.route("/test-webhook", methods=["POST"])
@login_required
def test_webhook():
    load_config()
    current_token_data = get_current_token_data()
    if not current_token_data or not current_token_data.get("use_webhook") or not current_token_data.get("webhook_url"):
        return jsonify(success=False, message="Webhook not configured"), 400
    send_log(f"Test webhook from '{current_token_data['name']}'.", success=True,
             webhook_url=current_token_data["webhook_url"])
    return jsonify(success=True)

@app.route("/edit-channel", methods=["GET"])
@login_required
def edit_channel():
    load_config()
    current_token_data = get_current_token_data()
    if not current_token_data:
        flash("Token not found.", "danger")
        return redirect("/")
    channel_id = request.args.get("channel_id")
    return render_template_string(html_template,
        config_json=json.dumps(current_token_data, indent=4),
        config=config, current_token_data=current_token_data,
        editing=True,
        original_channel_id=channel_id,
        channel_id=channel_id,
        channel_message=request.args.get("message", ""),
        hours=request.args.get("hours", 0),
        minutes=request.args.get("minutes", 0),
        seconds=request.args.get("seconds", 0)
    )

@app.route("/save-dark-mode", methods=["POST"])
@login_required
def save_dark_mode():
    return jsonify(success=True)

# ===========================================================
#  TEMPLATES
# ===========================================================

# ─── LOGIN ──────────────────────────────────────────────────
login_template = '''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>C3B1XHUB — Login</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Rajdhani:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/brands.min.css">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
:root{--red:#e8000d;--red-dark:#a30009;--red-glow:rgba(232,0,13,.5);--bg:#080808;--bg2:#111;--border:rgba(232,0,13,.22);--text:#eee;--muted:#777}
html,body{min-height:100vh;background:var(--bg);color:var(--text);font-family:'Rajdhani',sans-serif;overflow:hidden}
body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(232,0,13,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(232,0,13,.04) 1px,transparent 1px);background-size:55px 55px;animation:gridScroll 25s linear infinite;pointer-events:none;z-index:0}
@keyframes gridScroll{0%{background-position:0 0}100%{background-position:55px 55px}}
.radial{position:fixed;inset:0;background:radial-gradient(ellipse at 50% 60%,rgba(232,0,13,.08) 0%,transparent 65%);pointer-events:none;z-index:0}
.particles{position:fixed;inset:0;pointer-events:none;z-index:0}
.p{position:absolute;border-radius:50%;opacity:0;background:var(--red);animation:rise var(--d,8s) var(--dl,0s) infinite ease-in-out}
@keyframes rise{0%{opacity:0;transform:translateY(100vh) scale(0)}10%{opacity:.7}90%{opacity:.4}100%{opacity:0;transform:translateY(-80px) scale(2)}}
.wrap{position:relative;z-index:10;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:1rem}
.logo{text-align:center;margin-bottom:2.5rem}
.logo-ring{width:90px;height:90px;border-radius:50%;border:2px solid var(--red);background:rgba(232,0,13,.08);display:inline-flex;align-items:center;justify-content:center;font-size:2.2rem;color:var(--red);margin-bottom:1rem;animation:pulse 2.5s ease-in-out infinite;box-shadow:0 0 30px var(--red-glow)}
@keyframes pulse{0%,100%{box-shadow:0 0 20px var(--red-glow)}50%{box-shadow:0 0 55px var(--red-glow),0 0 90px rgba(232,0,13,.18)}}
.logo-name{font-family:'Orbitron',monospace;font-size:1.8rem;font-weight:900;color:var(--red);text-shadow:0 0 20px var(--red-glow);letter-spacing:3px}
.logo-sub{font-size:.75rem;color:var(--muted);letter-spacing:3px;text-transform:uppercase;margin-top:.3rem}
.card{background:linear-gradient(135deg,rgba(20,20,20,.97),rgba(12,12,12,.98));border:1px solid var(--border);border-radius:16px;padding:2.5rem;width:100%;max-width:420px;position:relative;overflow:hidden;box-shadow:0 0 80px rgba(232,0,13,.08),inset 0 1px 0 rgba(255,255,255,.04)}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,var(--red),transparent);animation:scan 3s ease-in-out infinite}
@keyframes scan{0%{transform:scaleX(0);opacity:0}50%{transform:scaleX(1);opacity:1}100%{transform:scaleX(0);opacity:0}}
.card-title{font-family:'Orbitron',monospace;font-size:1rem;font-weight:700;color:var(--text);margin-bottom:.5rem;display:flex;align-items:center;gap:.6rem}
.card-title i{color:var(--red)}
.card-sub{font-size:.85rem;color:var(--muted);margin-bottom:2rem;line-height:1.6}
.btn-discord{display:flex;align-items:center;justify-content:center;gap:.85rem;width:100%;padding:1rem;background:linear-gradient(135deg,#5865f2,#4752c4);border:none;border-radius:10px;color:#fff;font-family:'Orbitron',monospace;font-size:.85rem;font-weight:700;letter-spacing:1.5px;cursor:pointer;text-decoration:none;transition:all .3s ease;position:relative;overflow:hidden}
.btn-discord::before{content:'';position:absolute;top:0;left:-100%;width:100%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.12),transparent);transition:left .5s}
.btn-discord:hover::before{left:100%}
.btn-discord:hover{transform:translateY(-2px);box-shadow:0 8px 30px rgba(88,101,242,.5);color:#fff}
.btn-discord i{font-size:1.3rem}
.setup-box{background:rgba(232,0,13,.06);border:1px solid var(--border);border-radius:10px;padding:1.25rem;margin-top:1.5rem;font-size:.85rem;color:var(--muted);line-height:1.7}
.setup-box code{background:rgba(255,255,255,.07);padding:.1rem .4rem;border-radius:4px;color:#fff;font-size:.8rem}
.divider{display:flex;align-items:center;gap:.75rem;margin:1.5rem 0;color:var(--muted);font-size:.75rem;text-transform:uppercase;letter-spacing:1px}
.divider::before,.divider::after{content:'';flex:1;height:1px;background:var(--border)}
.alerts{position:fixed;top:1.25rem;right:1.25rem;z-index:9999;display:flex;flex-direction:column;gap:.6rem;max-width:360px}
.alert{background:var(--bg2);border:1px solid var(--border);border-radius:9px;padding:.8rem 1.1rem;display:flex;align-items:center;gap:.65rem;font-size:.88rem;animation:slideIn .35s ease}
@keyframes slideIn{from{opacity:0;transform:translateX(60px)}to{opacity:1;transform:translateX(0)}}
.alert-success{border-left:3px solid #22c55e}.alert-success i{color:#22c55e}
.alert-danger{border-left:3px solid var(--red)}.alert-danger i{color:var(--red)}
.alert-warning{border-left:3px solid #f59e0b}.alert-warning i{color:#f59e0b}
.alert-info{border-left:3px solid #3b82f6}.alert-info i{color:#3b82f6}
.alert-close{margin-left:auto;background:none;border:none;color:var(--muted);cursor:pointer;font-size:1rem}
.footer-note{margin-top:1.5rem;font-size:.75rem;color:var(--muted);text-align:center}
.prem-badge{display:inline-flex;align-items:center;gap:.4rem;padding:.25rem .7rem;border-radius:20px;font-size:.75rem;font-weight:700;letter-spacing:.5px}
.prem-badge.free{background:rgba(255,255,255,.08);color:#aaa;border:1px solid rgba(255,255,255,.12)}
.prem-badge.active{background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;border:none;box-shadow:0 0 12px rgba(245,158,11,.4)}
.prem-widget{background:#181818;border:1px solid rgba(245,158,11,.3);border-radius:14px;padding:1.2rem;margin-bottom:1rem}
.prem-widget.free{border-color:rgba(255,255,255,.1)}
.prem-plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.75rem;margin-top:1rem}
.plan-card{border-radius:12px;padding:1rem;text-align:center;border:1px solid rgba(255,255,255,.1);background:#111}
.plan-card .plan-emoji{font-size:1.6rem;display:block;margin-bottom:.4rem}
.plan-card .plan-name{font-weight:700;font-size:.95rem;color:#eee}
.plan-card .plan-price{color:#f59e0b;font-weight:700;font-size:1rem;margin:.3rem 0}
.plan-card .plan-dur{color:#777;font-size:.78rem}
.btn-buy{display:inline-block;margin-top:.6rem;padding:.35rem .9rem;background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;border-radius:8px;font-weight:700;font-size:.8rem;border:none;cursor:pointer;text-decoration:none;transition:all .2s}
.btn-buy:hover{filter:brightness(1.15);transform:translateY(-1px)}
.limit-bar{display:flex;align-items:center;justify-content:space-between;margin-bottom:.4rem;font-size:.82rem}
.limit-track{background:rgba(255,255,255,.08);border-radius:4px;height:6px;flex:1;margin:0 .6rem}
.limit-fill{height:100%;border-radius:4px;background:#e8000d;transition:width .4s}
.limit-fill.ok{background:#22c55e}
.limit-fill.warn{background:#f59e0b}
.prem-admin-select{background:#181818;color:#eee;border:1px solid rgba(255,255,255,.15);border-radius:8px;padding:.3rem .6rem;font-size:.8rem}
.btn-prem-grant{background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;border:none;border-radius:8px;padding:.3rem .8rem;font-size:.8rem;font-weight:700;cursor:pointer}
.btn-prem-grant:hover{filter:brightness(1.1)}
.btn-prem-remove{background:rgba(232,0,13,.2);color:#e8000d;border:1px solid rgba(232,0,13,.3);border-radius:8px;padding:.3rem .8rem;font-size:.8rem;cursor:pointer}
</style>
</head>
<body>
<div class="radial"></div>
<div class="particles" id="pts"></div>

{% with messages = get_flashed_messages(with_categories=true) %}
{% if messages %}
<div class="alerts">
{% for cat, msg in messages %}
<div class="alert alert-{{ cat }}">
  <i class="fas fa-{{ 'check-circle' if cat=='success' else 'exclamation-circle' if cat=='danger' else 'info-circle' }}"></i>
  <span>{{ msg }}</span>
  <button class="alert-close" onclick="this.parentElement.remove()">×</button>
</div>
{% endfor %}
</div>
{% endif %}
{% endwith %}

<div class="wrap">
  <div class="logo">
    <div class="logo-ring"><i class="fas fa-satellite-dish"></i></div>
    <div class="logo-name">C3B1XHUB</div>
    <div class="logo-sub">Auto Post Manager</div>
  </div>
  <div class="card">
    <div class="card-title"><i class="fas fa-door-open"></i> Authentication</div>
    <div class="card-sub">Sign in with your Discord account to access the dashboard and manage your auto-posting bots.</div>
    {% if client_id_set %}
    <a href="/auth/discord" class="btn-discord">
      <i class="fab fa-discord"></i> LOGIN WITH DISCORD
    </a>
    {% else %}
    <a href="#" class="btn-discord" style="opacity:.5;cursor:not-allowed">
      <i class="fab fa-discord"></i> LOGIN WITH DISCORD
    </a>
    <div class="setup-box">
      <strong style="color:var(--red)"><i class="fas fa-exclamation-triangle"></i> OAuth Not Configured</strong><br><br>
      Set these environment variables:<br>
      <code>DISCORD_CLIENT_ID</code><br>
      <code>DISCORD_CLIENT_SECRET</code><br>
      <code>DISCORD_REDIRECT_URI</code><br><br>
      Or run locally without env vars — you'll be auto-logged in as <strong>Local Developer</strong>.
    </div>
    {% endif %}
    <div class="footer-note">By logging in you agree to use this tool responsibly.</div>
  </div>
</div>
<script>
const pts = document.getElementById('pts');
for(let i=0;i<45;i++){
  const p=document.createElement('div');
  p.className='p';
  const sz=1+Math.random()*3;
  p.style.cssText=`left:${Math.random()*100}%;width:${sz}px;height:${sz}px;--d:${6+Math.random()*10}s;--dl:${Math.random()*9}s`;
  pts.appendChild(p);
}
setTimeout(()=>{document.querySelectorAll('.alert').forEach(a=>{a.style.transition='opacity .5s';a.style.opacity='0';setTimeout(()=>a.remove(),500)})},5000);
</script>
</body>
</html>
'''

# ─── REGISTER TOKEN ─────────────────────────────────────────
register_token_template = '''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>C3B1XHUB — Add Token</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Rajdhani:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/brands.min.css">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
:root{--red:#e8000d;--red-dark:#a30009;--red-glow:rgba(232,0,13,.5);--bg:#080808;--bg2:#111;--border:rgba(232,0,13,.22);--text:#eee;--muted:#777}
html,body{min-height:100vh;background:var(--bg);color:var(--text);font-family:'Rajdhani',sans-serif}
body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(232,0,13,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(232,0,13,.04) 1px,transparent 1px);background-size:55px 55px;animation:gridScroll 25s linear infinite;pointer-events:none;z-index:0}
@keyframes gridScroll{0%{background-position:0 0}100%{background-position:55px 55px}}
.wrap{position:relative;z-index:10;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1rem}
.card{background:linear-gradient(135deg,rgba(20,20,20,.97),rgba(12,12,12,.98));border:1px solid var(--border);border-radius:16px;padding:2.5rem;width:100%;max-width:440px;position:relative;overflow:hidden;box-shadow:0 0 60px rgba(232,0,13,.08)}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,var(--red),transparent);animation:scan 3s ease-in-out infinite}
@keyframes scan{0%{transform:scaleX(0);opacity:0}50%{transform:scaleX(1);opacity:1}100%{transform:scaleX(0);opacity:0}}
.logo{text-align:center;margin-bottom:2rem}
.logo-name{font-family:'Orbitron',monospace;font-size:1.4rem;font-weight:900;color:var(--red);text-shadow:0 0 15px var(--red-glow);letter-spacing:2px}
.logo-sub{font-size:.72rem;color:var(--muted);letter-spacing:2px;text-transform:uppercase;margin-top:.25rem}
.title{font-family:'Orbitron',monospace;font-size:.85rem;font-weight:700;color:var(--text);margin-bottom:1.75rem;display:flex;align-items:center;gap:.6rem;padding-bottom:.75rem;border-bottom:1px solid var(--border)}
.title i{color:var(--red)}
label{display:block;font-size:.72rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:.5rem}
input{width:100%;background:rgba(0,0,0,.4);border:1px solid rgba(255,255,255,.07);border-radius:8px;padding:.8rem 1rem;color:var(--text);font-family:'Rajdhani',sans-serif;font-size:1rem;transition:all .25s;outline:none;margin-bottom:1.25rem}
input:focus{border-color:var(--red);box-shadow:0 0 0 3px rgba(232,0,13,.12)}
input::placeholder{color:rgba(255,255,255,.18)}
.btn{display:flex;align-items:center;justify-content:center;gap:.6rem;width:100%;padding:.9rem;background:linear-gradient(135deg,var(--red),var(--red-dark));border:none;border-radius:9px;color:#fff;font-family:'Orbitron',monospace;font-size:.82rem;font-weight:700;letter-spacing:1.5px;cursor:pointer;transition:all .3s;position:relative;overflow:hidden;margin-top:.25rem}
.btn::before{content:'';position:absolute;top:0;left:-100%;width:100%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.1),transparent);transition:left .4s}
.btn:hover::before{left:100%}
.btn:hover{transform:translateY(-2px);box-shadow:0 8px 28px var(--red-glow)}
.back-link{display:block;text-align:center;margin-top:1.25rem;color:var(--muted);font-size:.88rem;text-decoration:none;transition:color .2s}
.back-link:hover{color:var(--red)}
.alerts{position:fixed;top:1.25rem;right:1.25rem;z-index:9999;display:flex;flex-direction:column;gap:.6rem;max-width:360px}
.alert{background:var(--bg2);border:1px solid var(--border);border-radius:9px;padding:.8rem 1.1rem;display:flex;align-items:center;gap:.65rem;font-size:.88rem;animation:slideIn .35s ease}
@keyframes slideIn{from{opacity:0;transform:translateX(60px)}to{opacity:1;transform:translateX(0)}}
.alert-success{border-left:3px solid #22c55e}.alert-success i{color:#22c55e}
.alert-danger{border-left:3px solid var(--red)}.alert-danger i{color:var(--red)}
.alert-warning{border-left:3px solid #f59e0b}.alert-warning i{color:#f59e0b}
.alert-close{margin-left:auto;background:none;border:none;color:var(--muted);cursor:pointer;font-size:1rem}
.prem-badge{display:inline-flex;align-items:center;gap:.4rem;padding:.25rem .7rem;border-radius:20px;font-size:.75rem;font-weight:700;letter-spacing:.5px}
.prem-badge.free{background:rgba(255,255,255,.08);color:#aaa;border:1px solid rgba(255,255,255,.12)}
.prem-badge.active{background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;border:none;box-shadow:0 0 12px rgba(245,158,11,.4)}
.prem-widget{background:#181818;border:1px solid rgba(245,158,11,.3);border-radius:14px;padding:1.2rem;margin-bottom:1rem}
.prem-widget.free{border-color:rgba(255,255,255,.1)}
.prem-plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.75rem;margin-top:1rem}
.plan-card{border-radius:12px;padding:1rem;text-align:center;border:1px solid rgba(255,255,255,.1);background:#111}
.plan-card .plan-emoji{font-size:1.6rem;display:block;margin-bottom:.4rem}
.plan-card .plan-name{font-weight:700;font-size:.95rem;color:#eee}
.plan-card .plan-price{color:#f59e0b;font-weight:700;font-size:1rem;margin:.3rem 0}
.plan-card .plan-dur{color:#777;font-size:.78rem}
.btn-buy{display:inline-block;margin-top:.6rem;padding:.35rem .9rem;background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;border-radius:8px;font-weight:700;font-size:.8rem;border:none;cursor:pointer;text-decoration:none;transition:all .2s}
.btn-buy:hover{filter:brightness(1.15);transform:translateY(-1px)}
.limit-bar{display:flex;align-items:center;justify-content:space-between;margin-bottom:.4rem;font-size:.82rem}
.limit-track{background:rgba(255,255,255,.08);border-radius:4px;height:6px;flex:1;margin:0 .6rem}
.limit-fill{height:100%;border-radius:4px;background:#e8000d;transition:width .4s}
.limit-fill.ok{background:#22c55e}
.limit-fill.warn{background:#f59e0b}
.prem-admin-select{background:#181818;color:#eee;border:1px solid rgba(255,255,255,.15);border-radius:8px;padding:.3rem .6rem;font-size:.8rem}
.btn-prem-grant{background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;border:none;border-radius:8px;padding:.3rem .8rem;font-size:.8rem;font-weight:700;cursor:pointer}
.btn-prem-grant:hover{filter:brightness(1.1)}
.btn-prem-remove{background:rgba(232,0,13,.2);color:#e8000d;border:1px solid rgba(232,0,13,.3);border-radius:8px;padding:.3rem .8rem;font-size:.8rem;cursor:pointer}
</style>
</head>
<body>
{% with messages = get_flashed_messages(with_categories=true) %}
{% if messages %}
<div class="alerts">
{% for cat, msg in messages %}
<div class="alert alert-{{ cat }}">
  <i class="fas fa-{{ 'check-circle' if cat=='success' else 'exclamation-circle' }}"></i>
  <span>{{ msg }}</span>
  <button class="alert-close" onclick="this.parentElement.remove()">×</button>
</div>
{% endfor %}
</div>
{% endif %}
{% endwith %}
<div class="wrap">
  <div class="card">
    <div class="logo">
      <div class="logo-name">C3B1XHUB</div>
      <div class="logo-sub">Auto Post Manager</div>
    </div>
    <div class="title"><i class="fas fa-plug"></i> Register Bot Token</div>
    <form method="post" action="/register-token">
      <label>Bot Name</label>
      <input type="text" name="token_name" placeholder="e.g. Main Posting Bot" required>
      <label>Discord Bot Token</label>
      <input type="password" name="token" placeholder="Enter your Discord bot token" required>
      <button type="submit" class="btn"><i class="fas fa-satellite-dish"></i> CONNECT TOKEN</button>
    </form>
    {% if has_existing_tokens %}
    <a href="/" class="back-link">← Back to Dashboard</a>
    {% endif %}
  </div>
</div>
<script>setTimeout(()=>{document.querySelectorAll('.alert').forEach(a=>{a.style.transition='opacity .5s';a.style.opacity='0';setTimeout(()=>a.remove(),500)})},5000)</script>
</body>
</html>
'''

# ─── ADMIN PANEL ────────────────────────────────────────────
admin_template = '''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>C3B1XHUB — Admin Panel</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Rajdhani:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/brands.min.css">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
:root{--red:#e8000d;--red-dark:#a30009;--red-glow:rgba(232,0,13,.45);--bg:#070707;--bg2:#101010;--bg3:#181818;--border:rgba(232,0,13,.18);--border2:rgba(255,255,255,.07);--text:#eee;--dim:#aaa;--muted:#666;--green:#22c55e;--blue:#3b82f6;--yellow:#f59e0b;--sw:260px}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);font-family:'Rajdhani',sans-serif;min-height:100vh;overflow-x:hidden}
body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(232,0,13,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(232,0,13,.025) 1px,transparent 1px);background-size:60px 60px;pointer-events:none;z-index:0}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:var(--bg2)}::-webkit-scrollbar-thumb{background:var(--red-dark);border-radius:2px}

/* SIDEBAR */
.sb{position:fixed;left:0;top:0;width:var(--sw);height:100vh;background:linear-gradient(180deg,#0c0c0c,#080808);border-right:1px solid var(--border);z-index:100;display:flex;flex-direction:column;overflow:hidden}
.sb::after{content:'';position:absolute;top:0;right:0;width:1px;height:100%;background:linear-gradient(180deg,transparent,var(--red),transparent);opacity:.35}
.sb-logo{padding:1.5rem;border-bottom:1px solid var(--border)}
.sb-brand{font-family:'Orbitron',monospace;font-size:1.1rem;font-weight:900;color:var(--red);text-shadow:0 0 12px var(--red-glow);letter-spacing:1.5px;display:flex;align-items:center;gap:.6rem}
.sb-tag{font-size:.65rem;color:var(--muted);letter-spacing:2px;text-transform:uppercase;margin-top:.2rem;padding-left:1.75rem}
.sb-section{padding:.75rem 1rem .2rem;font-size:.62rem;color:var(--muted);text-transform:uppercase;letter-spacing:2px;font-weight:700}
.sb-nav{flex:1;overflow-y:auto;padding:.25rem .6rem}
.sb-item{display:flex;align-items:center;gap:.7rem;padding:.65rem .8rem;border-radius:8px;color:var(--dim);text-decoration:none;transition:all .2s;margin-bottom:.2rem;border:1px solid transparent;font-size:.9rem;font-weight:600;cursor:pointer;background:none;width:100%}
.sb-item:hover{background:var(--bg3);color:var(--text);border-color:var(--border)}
.sb-item.active{background:rgba(232,0,13,.08);border-color:rgba(232,0,13,.4);color:var(--red)}
.sb-item i{width:20px;text-align:center;font-size:.85rem}
.sb-badge{margin-left:auto;background:var(--red);color:#fff;font-size:.65rem;font-weight:700;padding:.1rem .45rem;border-radius:10px;min-width:20px;text-align:center}

/* MAIN */
.main{margin-left:var(--sw);min-height:100vh;position:relative;z-index:1}
.topbar{position:sticky;top:0;z-index:90;background:rgba(7,7,7,.92);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);padding:0 1.5rem;height:58px;display:flex;align-items:center;justify-content:space-between}
.tb-title{font-family:'Orbitron',monospace;font-size:.82rem;font-weight:700;color:var(--text);letter-spacing:1px}
.tb-title span{color:var(--red)}
.tb-right{display:flex;align-items:center;gap:.75rem}
.user-chip{display:flex;align-items:center;gap:.5rem;padding:.3rem .75rem .3rem .35rem;border-radius:50px;background:var(--bg3);border:1px solid var(--border2);font-size:.82rem;color:var(--dim)}
.user-chip img{width:26px;height:26px;border-radius:50%}
.user-chip .ava-ph{width:26px;height:26px;border-radius:50%;background:var(--red);display:flex;align-items:center;justify-content:center;font-size:.7rem;color:#fff}
.btn-sm{display:inline-flex;align-items:center;gap:.4rem;padding:.35rem .8rem;border-radius:7px;font-family:'Rajdhani',sans-serif;font-size:.8rem;font-weight:700;cursor:pointer;border:none;transition:all .2s;text-decoration:none}
.btn-back{background:rgba(255,255,255,.05);border:1px solid var(--border2);color:var(--dim)}
.btn-back:hover{border-color:var(--red);color:var(--red)}
.content{padding:1.5rem}

/* STATS */
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-bottom:1.5rem}
@media(max-width:900px){.stats{grid-template-columns:repeat(2,1fr)}}
@media(max-width:500px){.stats{grid-template-columns:1fr}}
.stat-card{background:var(--bg2);border:1px solid var(--border2);border-radius:12px;padding:1.25rem;position:relative;overflow:hidden;transition:border-color .2s}
.stat-card:hover{border-color:var(--border)}
.stat-card::before{content:'';position:absolute;bottom:0;left:0;right:0;height:2px}
.stat-card.sc-red::before{background:var(--red)}
.stat-card.sc-green::before{background:var(--green)}
.stat-card.sc-blue::before{background:var(--blue)}
.stat-card.sc-yellow::before{background:var(--yellow)}
.stat-icon{width:40px;height:40px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:1.1rem;margin-bottom:.85rem}
.si-red{background:rgba(232,0,13,.12);color:var(--red)}
.si-green{background:rgba(34,197,94,.1);color:var(--green)}
.si-blue{background:rgba(59,130,246,.1);color:var(--blue)}
.si-yellow{background:rgba(245,158,11,.1);color:var(--yellow)}
.stat-num{font-family:'Orbitron',monospace;font-size:1.8rem;font-weight:900;color:var(--text);line-height:1}
.stat-label{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;margin-top:.35rem}

/* CARDS */
.card{background:var(--bg2);border:1px solid var(--border2);border-radius:12px;margin-bottom:1.25rem;overflow:hidden;transition:border-color .2s}
.card:hover{border-color:var(--border)}
.card-head{display:flex;align-items:center;justify-content:space-between;padding:.9rem 1.1rem;border-bottom:1px solid var(--border2);background:rgba(0,0,0,.2)}
.card-title{display:flex;align-items:center;gap:.55rem;font-family:'Orbitron',monospace;font-size:.72rem;font-weight:700;color:var(--text);letter-spacing:.8px}
.card-title i{color:var(--red)}
.card-body{padding:1.1rem}

/* TABLE */
.tbl{width:100%;border-collapse:collapse;font-size:.875rem}
.tbl th{padding:.65rem .9rem;text-align:left;font-size:.68rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);font-weight:700;border-bottom:1px solid var(--border2)}
.tbl td{padding:.7rem .9rem;border-bottom:1px solid rgba(255,255,255,.04);vertical-align:middle}
.tbl tr:hover td{background:rgba(255,255,255,.02)}
.tbl tr:last-child td{border-bottom:none}
.ava{width:32px;height:32px;border-radius:50%;object-fit:cover;border:2px solid var(--border)}
.ava-ph2{width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,var(--red),var(--red-dark));display:flex;align-items:center;justify-content:center;font-size:.8rem;color:#fff;font-weight:700}
.user-info{display:flex;align-items:center;gap:.65rem}
.user-name{font-weight:700;color:var(--text);font-size:.9rem}
.user-id{font-size:.72rem;color:var(--muted);font-family:monospace}
.badge{display:inline-flex;align-items:center;gap:.25rem;padding:.18rem .55rem;border-radius:20px;font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px}
.b-active{background:rgba(34,197,94,.1);color:var(--green);border:1px solid rgba(34,197,94,.25)}
.b-banned{background:rgba(232,0,13,.1);color:var(--red);border:1px solid rgba(232,0,13,.25)}
.b-admin{background:rgba(245,158,11,.1);color:var(--yellow);border:1px solid rgba(245,158,11,.25)}
.b-login{background:rgba(59,130,246,.1);color:var(--blue);border:1px solid rgba(59,130,246,.2)}
.b-reg{background:rgba(34,197,94,.1);color:var(--green);border:1px solid rgba(34,197,94,.2)}
.b-ban{background:rgba(232,0,13,.1);color:var(--red);border:1px solid rgba(232,0,13,.2)}
.actions-row{display:flex;align-items:center;gap:.4rem;flex-wrap:wrap}
.btn-act{display:inline-flex;align-items:center;gap:.3rem;padding:.3rem .65rem;border-radius:6px;font-family:'Rajdhani',sans-serif;font-size:.78rem;font-weight:700;cursor:pointer;border:none;transition:all .2s;text-decoration:none}
.ba-danger{background:rgba(232,0,13,.08);border:1px solid rgba(232,0,13,.2);color:var(--red)}
.ba-danger:hover{background:rgba(232,0,13,.18)}
.ba-success{background:rgba(34,197,94,.08);border:1px solid rgba(34,197,94,.2);color:var(--green)}
.ba-success:hover{background:rgba(34,197,94,.18)}
.ba-neutral{background:rgba(255,255,255,.05);border:1px solid var(--border2);color:var(--dim)}
.ba-neutral:hover{border-color:var(--red);color:var(--red)}
.ban-form{display:flex;gap:.4rem;align-items:center;flex-wrap:wrap}
.ban-input{background:rgba(0,0,0,.4);border:1px solid rgba(255,255,255,.07);border-radius:6px;padding:.28rem .6rem;color:var(--text);font-family:'Rajdhani',sans-serif;font-size:.8rem;width:130px;outline:none;transition:border-color .2s}
.ban-input:focus{border-color:var(--red)}
.ban-input::placeholder{color:rgba(255,255,255,.2)}

/* LOG */
.log-item{display:flex;align-items:center;gap:.75rem;padding:.6rem 0;border-bottom:1px solid rgba(255,255,255,.03)}
.log-item:last-child{border-bottom:none}
.log-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.ld-login{background:var(--blue)}
.ld-register{background:var(--green)}
.ld-banned{background:var(--red)}
.log-text{flex:1;font-size:.83rem;color:var(--dim)}
.log-text strong{color:var(--text)}
.log-time{font-size:.73rem;color:var(--muted);white-space:nowrap}
.log-ip{font-size:.72rem;color:var(--muted);font-family:monospace}

/* SEARCH */
.search-bar{display:flex;align-items:center;gap:.5rem;background:rgba(0,0,0,.3);border:1px solid var(--border2);border-radius:8px;padding:.45rem .9rem;margin-bottom:1rem}
.search-bar i{color:var(--muted);font-size:.85rem}
.search-bar input{background:none;border:none;color:var(--text);font-family:'Rajdhani',sans-serif;font-size:.9rem;outline:none;flex:1}
.search-bar input::placeholder{color:var(--muted)}
.empty{text-align:center;padding:2.5rem;color:var(--muted);font-size:.9rem}
.empty i{font-size:2rem;color:rgba(232,0,13,.2);display:block;margin-bottom:.5rem}

/* ALERTS */
.alerts{position:fixed;top:1.25rem;right:1.25rem;z-index:9999;display:flex;flex-direction:column;gap:.6rem;max-width:360px}
.alert{background:var(--bg2);border:1px solid var(--border2);border-radius:9px;padding:.8rem 1.1rem;display:flex;align-items:center;gap:.65rem;font-size:.88rem;animation:slideIn .35s ease}
@keyframes slideIn{from{opacity:0;transform:translateX(60px)}to{opacity:1;transform:translateX(0)}}
.alert-success{border-left:3px solid var(--green)}.alert-success i{color:var(--green)}
.alert-danger{border-left:3px solid var(--red)}.alert-danger i{color:var(--red)}
.alert-warning{border-left:3px solid var(--yellow)}.alert-warning i{color:var(--yellow)}
.alert-info{border-left:3px solid var(--blue)}.alert-info i{color:var(--blue)}
.alert-close{margin-left:auto;background:none;border:none;color:var(--muted);cursor:pointer;font-size:1rem}
.prem-badge{display:inline-flex;align-items:center;gap:.4rem;padding:.25rem .7rem;border-radius:20px;font-size:.75rem;font-weight:700;letter-spacing:.5px}
.prem-badge.free{background:rgba(255,255,255,.08);color:#aaa;border:1px solid rgba(255,255,255,.12)}
.prem-badge.active{background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;border:none;box-shadow:0 0 12px rgba(245,158,11,.4)}
.prem-widget{background:#181818;border:1px solid rgba(245,158,11,.3);border-radius:14px;padding:1.2rem;margin-bottom:1rem}
.prem-widget.free{border-color:rgba(255,255,255,.1)}
.prem-plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.75rem;margin-top:1rem}
.plan-card{border-radius:12px;padding:1rem;text-align:center;border:1px solid rgba(255,255,255,.1);background:#111}
.plan-card .plan-emoji{font-size:1.6rem;display:block;margin-bottom:.4rem}
.plan-card .plan-name{font-weight:700;font-size:.95rem;color:#eee}
.plan-card .plan-price{color:#f59e0b;font-weight:700;font-size:1rem;margin:.3rem 0}
.plan-card .plan-dur{color:#777;font-size:.78rem}
.btn-buy{display:inline-block;margin-top:.6rem;padding:.35rem .9rem;background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;border-radius:8px;font-weight:700;font-size:.8rem;border:none;cursor:pointer;text-decoration:none;transition:all .2s}
.btn-buy:hover{filter:brightness(1.15);transform:translateY(-1px)}
.limit-bar{display:flex;align-items:center;justify-content:space-between;margin-bottom:.4rem;font-size:.82rem}
.limit-track{background:rgba(255,255,255,.08);border-radius:4px;height:6px;flex:1;margin:0 .6rem}
.limit-fill{height:100%;border-radius:4px;background:#e8000d;transition:width .4s}
.limit-fill.ok{background:#22c55e}
.limit-fill.warn{background:#f59e0b}
.prem-admin-select{background:#181818;color:#eee;border:1px solid rgba(255,255,255,.15);border-radius:8px;padding:.3rem .6rem;font-size:.8rem}
.btn-prem-grant{background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;border:none;border-radius:8px;padding:.3rem .8rem;font-size:.8rem;font-weight:700;cursor:pointer}
.btn-prem-grant:hover{filter:brightness(1.1)}
.btn-prem-remove{background:rgba(232,0,13,.2);color:#e8000d;border:1px solid rgba(232,0,13,.3);border-radius:8px;padding:.3rem .8rem;font-size:.8rem;cursor:pointer}
</style>
</head>
<body>
{% with messages = get_flashed_messages(with_categories=true) %}
{% if messages %}
<div class="alerts">
{% for cat,msg in messages %}
<div class="alert alert-{{ cat }}">
  <i class="fas fa-{{ 'check-circle' if cat=='success' else 'times-circle' if cat=='danger' else 'info-circle' }}"></i>
  <span>{{ msg }}</span>
  <button class="alert-close" onclick="this.parentElement.remove()">×</button>
</div>
{% endfor %}
</div>
{% endif %}
{% endwith %}

<aside class="sb">
  <div class="sb-logo">
    <div class="sb-brand"><i class="fas fa-shield-halved"></i> ADMIN</div>
    <div class="sb-tag">C3B1XHUB Panel</div>
  </div>
  <div class="sb-section">Navigation</div>
  <div class="sb-nav">
    <button class="sb-item active" onclick="showPanel('dashboard')"><i class="fas fa-chart-line"></i> Dashboard</button>
    <button class="sb-item" onclick="showPanel('users')"><i class="fas fa-users"></i> Users <span class="sb-badge">{{ total_users }}</span></button>
    <button class="sb-item" onclick="showPanel('logs')"><i class="fas fa-list-alt"></i> Activity Logs</button>
    <div class="sb-section" style="margin-top:.5rem">System</div>
    <a href="/" class="sb-item"><i class="fas fa-robot"></i> Bot Manager</a>
    <a href="/logout" class="sb-item"><i class="fas fa-sign-out-alt"></i> Logout</a>
  </div>
</aside>

<main class="main">
  <div class="topbar">
    <div class="tb-title">Admin <span>Panel</span></div>
    <div class="tb-right">
      {% set u = session.get('user',{}) %}
      <div class="user-chip">
        {% if u.get('avatar') %}
        <img src="https://cdn.discordapp.com/avatars/{{ u.discord_id }}/{{ u.avatar }}.png?size=64" alt="">
        {% else %}
        <div class="ava-ph">{{ u.get('username','?')[:1].upper() }}</div>
        {% endif %}
        {{ u.get('username','Admin') }}
      </div>
    </div>
  </div>

  <div class="content">

    <!-- DASHBOARD -->
    <div id="panel-dashboard">
      <div class="stats">
        <div class="stat-card sc-blue">
          <div class="stat-icon si-blue"><i class="fas fa-users"></i></div>
          <div class="stat-num">{{ total_users }}</div>
          <div class="stat-label">Total Users</div>
        </div>
        <div class="stat-card sc-red">
          <div class="stat-icon si-red"><i class="fas fa-ban"></i></div>
          <div class="stat-num">{{ banned_users }}</div>
          <div class="stat-label">Banned Users</div>
        </div>
        <div class="stat-card sc-green">
          <div class="stat-icon si-green"><i class="fas fa-user-plus"></i></div>
          <div class="stat-num">{{ new_today }}</div>
          <div class="stat-label">New Today</div>
        </div>
        <div class="stat-card sc-yellow">
          <div class="stat-icon si-yellow"><i class="fas fa-right-to-bracket"></i></div>
          <div class="stat-num">{{ logins_today }}</div>
          <div class="stat-label">Logins Today</div>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="card-title"><i class="fas fa-clock-rotate-left"></i> Recent Activity</div>
        </div>
        <div class="card-body">
          {% if logs %}
          {% for log in logs[:15] %}
          <div class="log-item">
            <div class="log-dot ld-{{ log['action'] if log['action'] in ['login','register','banned'] else 'login' }}"></div>
            <div class="log-text">
              <strong>{{ log['username'] or 'Unknown' }}</strong>
              — {{ log['action'] }}
            </div>
            <div class="log-ip">{{ log['ip'] or '' }}</div>
            <div class="log-time">{{ datetime.fromtimestamp(log['timestamp']).strftime('%m/%d %H:%M') if log['timestamp'] else '' }}</div>
          </div>
          {% endfor %}
          {% else %}
          <div class="empty"><i class="fas fa-inbox"></i>No activity yet</div>
          {% endif %}
        </div>
      </div>
    </div>

    <!-- USERS -->
    <div id="panel-users" style="display:none">
      <div class="card">
        <div class="card-head">
          <div class="card-title"><i class="fas fa-users"></i> User Management</div>
          <span style="font-size:.78rem;color:var(--muted)">{{ total_users }} registered</span>
        </div>
        <div class="card-body">
          <div class="search-bar">
            <i class="fas fa-search"></i>
            <input type="text" id="userSearch" placeholder="Search by username or Discord ID..." oninput="filterUsers()">
          </div>
          {% if users %}
          <div style="overflow-x:auto">
          <table class="tbl" id="userTable">
            <thead>
              <tr>
                <th>User</th>
                <th>Discord ID</th>
                <th>Logins</th>
                <th>First Seen</th>
                <th>Last Seen</th>
                <th>Status</th>
                <th>Actions</th>
                <th>Premium</th>
              </tr>
            </thead>
            <tbody>
              {% for u in users %}
              <tr data-name="{{ u['username']|lower }}" data-id="{{ u['discord_id'] }}">
                <td>
                  <div class="user-info">
                    {% if u['avatar'] %}
                    <img class="ava" src="https://cdn.discordapp.com/avatars/{{ u['discord_id'] }}/{{ u['avatar'] }}.png?size=64" alt="">
                    {% else %}
                    <div class="ava-ph2">{{ u['username'][:1].upper() if u['username'] else '?' }}</div>
                    {% endif %}
                    <div>
                      <div class="user-name">{{ u['username'] or 'Unknown' }}</div>
                      {% if u['discord_id'] in ADMIN_IDS %}<span class="badge b-admin"><i class="fas fa-crown"></i> Admin</span>{% endif %}
                    </div>
                  </div>
                </td>
                <td><span style="font-family:monospace;font-size:.78rem;color:var(--muted)">{{ u['discord_id'] }}</span></td>
                <td><span class="badge b-login">{{ u['login_count'] }}</span></td>
                <td style="font-size:.78rem;color:var(--muted)">{{ datetime.fromtimestamp(u['first_login']).strftime('%Y-%m-%d') if u['first_login'] else 'N/A' }}</td>
                <td style="font-size:.78rem;color:var(--muted)">{{ datetime.fromtimestamp(u['last_login']).strftime('%Y-%m-%d %H:%M') if u['last_login'] else 'N/A' }}</td>
                <td>
                  {% if u['is_banned'] %}
                  <span class="badge b-banned"><i class="fas fa-ban"></i> Banned</span>
                  {% else %}
                  <span class="badge b-active"><i class="fas fa-circle"></i> Active</span>
                  {% endif %}
                </td>
                <td>
                  <div class="actions-row">
                    {% if u['is_banned'] %}
                    <form method="post" action="/admin/unban/{{ u['discord_id'] }}" style="display:inline">
                      <button type="submit" class="btn-act ba-success"><i class="fas fa-unlock"></i> Unban</button>
                    </form>
                    {% else %}
                    <form method="post" action="/admin/ban/{{ u['discord_id'] }}" style="display:inline">
                      <div class="ban-form">
                        <input class="ban-input" type="text" name="reason" placeholder="Ban reason...">
                        <button type="submit" class="btn-act ba-danger" onclick="return confirm('Ban {{ u['username'] }}?')"><i class="fas fa-ban"></i> Ban</button>
                      </div>
                    </form>
                    {% endif %}
                    <form method="post" action="/admin/delete/{{ u['discord_id'] }}" style="display:inline">
                      <button type="submit" class="btn-act ba-neutral" onclick="return confirm('Delete {{ u['username'] }} permanently?')"><i class="fas fa-trash"></i></button>
                    </form>
                  </div>
                </td>
                                <td>
                  {% if u['premium_expires'] and u['premium_expires'] > now %}
                    <div style="margin-bottom:.4rem">
                      <span style="color:#f59e0b;font-size:.8rem;font-weight:700">
                        👑 {{ u['premium_type'] }}
                      </span><br>
                      <span style="color:#aaa;font-size:.75rem">
                        Exp: {{ u['premium_expires']|int|datetimeformat }}
                      </span>
                    </div>
                    <form action="/admin/remove-premium/{{ u['discord_id'] }}" method="post" style="display:inline">
                      <button type="submit" class="btn-prem-remove" onclick="return confirm('Hapus premium user ini?')">
                        <i class="fas fa-times"></i> Hapus
                      </button>
                    </form>
                  {% else %}
                    <span style="color:#555;font-size:.8rem">— Free</span>
                  {% endif %}
                  <div style="display:flex;gap:.3rem;align-items:center;margin-top:.4rem;flex-wrap:wrap">
                    <form action="/admin/set-premium/{{ u['discord_id'] }}" method="post" style="display:contents">
                      <select name="plan" class="prem-admin-select">
                        <option value="1day">⚡ 1 Hari — Rp5rb</option>
                        <option value="1week">🔥 1 Minggu — Rp20rb</option>
                        <option value="1month">👑 1 Bulan — Rp100rb</option>
                      </select>
                      <button type="submit" class="btn-prem-grant">
                        <i class="fas fa-star"></i> Grant
                      </button>
                    </form>
                  </div>
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
          </div>
          {% else %}
          <div class="empty"><i class="fas fa-users"></i>No users registered yet</div>
          {% endif %}
        </div>
      </div>
    </div>

    <!-- LOGS -->
    <div id="panel-logs" style="display:none">
      <div class="card">
        <div class="card-head">
          <div class="card-title"><i class="fas fa-list-alt"></i> Full Activity Log</div>
        </div>
        <div class="card-body">
          {% if logs %}
          {% for log in logs %}
          <div class="log-item">
            <div class="log-dot ld-{{ log['action'] if log['action'] in ['login','register','banned'] else 'login' }}"></div>
            <div class="log-text">
              <strong>{{ log['username'] or 'Unknown' }}</strong>
              —
              {% if log['action'] == 'register' %}<span class="badge b-reg">New User</span>
              {% elif log['action'] == 'login' %}<span class="badge b-login">Login</span>
              {% elif log['action'] == 'banned' %}<span class="badge b-ban">Banned Attempt</span>
              {% else %}<span class="badge b-login">{{ log['action'] }}</span>{% endif %}
            </div>
            <div class="log-ip">{{ log['ip'] or '' }}</div>
            <div class="log-time">{{ datetime.fromtimestamp(log['timestamp']).strftime('%Y-%m-%d %H:%M:%S') if log['timestamp'] else '' }}</div>
          </div>
          {% endfor %}
          {% else %}
          <div class="empty"><i class="fas fa-inbox"></i>No logs yet</div>
          {% endif %}
        </div>
      </div>
    </div>

  </div>
</main>

<script>
function showPanel(name){
  ['dashboard','users','logs'].forEach(p=>{
    document.getElementById('panel-'+p).style.display = p===name?'block':'none';
  });
  document.querySelectorAll('.sb-item').forEach(b=>{
    b.classList.remove('active');
    if(b.textContent.toLowerCase().includes(name)) b.classList.add('active');
  });
}
function filterUsers(){
  const q = document.getElementById('userSearch').value.toLowerCase();
  document.querySelectorAll('#userTable tbody tr').forEach(r=>{
    const show = r.dataset.name.includes(q) || r.dataset.id.includes(q);
    r.style.display = show ? '' : 'none';
  });
}
setTimeout(()=>{document.querySelectorAll('.alert').forEach(a=>{a.style.transition='opacity .5s';a.style.opacity='0';setTimeout(()=>a.remove(),500)})},5000);
</script>
</body>
</html>
'''

# ─── MAIN APP ────────────────────────────────────────────────
html_template = '''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>C3B1XHUB — {{ current_token_data.name }}</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Rajdhani:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/brands.min.css">
<style>
*,*::before,*::after{margin:0;padding:0;box-sizing:border-box}
:root{
  --red:#e8000d;--red-dark:#a30009;--red-mid:#cc0011;
  --red-glow:rgba(232,0,13,.5);--red-sub:rgba(232,0,13,.08);
  --bg:#080808;--bg2:#101010;--bg3:#181818;--bg4:#1e1e1e;
  --border:rgba(232,0,13,.2);--border2:rgba(255,255,255,.07);
  --text:#eee;--dim:#aaa;--muted:#666;--sw:270px;
  --green:#22c55e;--yellow:#f59e0b;--blue:#3b82f6
}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);font-family:'Rajdhani',sans-serif;min-height:100vh;overflow-x:hidden}
body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(232,0,13,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(232,0,13,.03) 1px,transparent 1px);background-size:60px 60px;pointer-events:none;z-index:0;animation:bgG 30s linear infinite}
@keyframes bgG{0%{background-position:0 0}100%{background-position:60px 60px}}
::-webkit-scrollbar{width:4px;height:4px}::-webkit-scrollbar-track{background:var(--bg2)}::-webkit-scrollbar-thumb{background:var(--red-dark);border-radius:2px}

/* SIDEBAR */
.sb{position:fixed;left:0;top:0;width:var(--sw);height:100vh;background:linear-gradient(180deg,#0d0d0d,#0a0a0a);border-right:1px solid var(--border);z-index:1000;display:flex;flex-direction:column;transition:transform .35s cubic-bezier(.4,0,.2,1);overflow:hidden}
.sb::after{content:'';position:absolute;top:0;right:0;width:1px;height:100%;background:linear-gradient(180deg,transparent,var(--red),transparent);opacity:.35}
@media(max-width:900px){.sb{transform:translateX(-100%)}.sb-open .sb{transform:translateX(0)}}
.sb-logo{padding:1.4rem 1.4rem 1rem;border-bottom:1px solid var(--border)}
.sb-brand{font-family:'Orbitron',monospace;font-size:1.15rem;font-weight:900;color:var(--red);text-shadow:0 0 14px var(--red-glow);letter-spacing:1.5px;display:flex;align-items:center;gap:.65rem}
.sb-sub{font-size:.65rem;color:var(--muted);letter-spacing:2px;text-transform:uppercase;margin-top:.2rem;padding-left:1.8rem}
.bot-chip{margin:.9rem;padding:.8rem;background:var(--red-sub);border:1px solid var(--border);border-radius:10px;position:relative;overflow:hidden}
.bot-chip::before{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent,var(--red),transparent)}
.bot-chip.on{background:rgba(34,197,94,.06);border-color:rgba(34,197,94,.25)}
.bot-chip.on::before{background:linear-gradient(90deg,transparent,#22c55e,transparent)}
.bot-name{font-weight:700;font-size:.92rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stat-dot{display:inline-flex;align-items:center;gap:.35rem;font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.8px;margin-top:.25rem}
.stat-dot::before{content:'';width:6px;height:6px;border-radius:50%;display:inline-block}
.stat-dot.on{color:var(--green)}.stat-dot.on::before{background:var(--green);box-shadow:0 0 7px var(--green);animation:blink 1.5s ease-in-out infinite}
.stat-dot.off{color:var(--muted)}.stat-dot.off::before{background:var(--muted)}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}
.sb-sect{padding:.85rem 1.1rem .25rem;font-size:.62rem;color:var(--muted);text-transform:uppercase;letter-spacing:2px;font-weight:700}
.sb-scroll{flex:1;overflow-y:auto;padding:.2rem .65rem}
.tok-item{display:flex;align-items:center;gap:.7rem;padding:.65rem .8rem;border-radius:8px;color:var(--dim);text-decoration:none;transition:all .2s;margin-bottom:.2rem;border:1px solid transparent;position:relative;overflow:hidden}
.tok-item:hover{background:var(--bg3);color:var(--text);border-color:var(--border2)}
.tok-item.active{background:var(--red-sub);border-color:rgba(232,0,13,.45);color:var(--red)}
.tok-item.active::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--red);border-radius:0 3px 3px 0;box-shadow:0 0 8px var(--red-glow)}
.tok-ic{width:30px;height:30px;border-radius:8px;background:rgba(255,255,255,.04);display:flex;align-items:center;justify-content:center;font-size:.85rem;flex-shrink:0}
.tok-item.active .tok-ic{background:var(--red-sub);color:var(--red)}
.tok-nm{font-size:.88rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tok-meta{font-size:.7rem;color:var(--muted)}
.add-tok{display:flex;align-items:center;gap:.7rem;padding:.65rem .8rem;margin:.4rem 0;border-radius:8px;border:1px dashed var(--border);color:var(--muted);text-decoration:none;font-size:.84rem;font-weight:600;transition:all .2s}
.add-tok:hover{border-color:var(--red);color:var(--red);background:var(--red-sub)}
/* user footer */
.sb-user{padding:.85rem;margin:.5rem;border-radius:10px;background:var(--bg3);border:1px solid var(--border2);display:flex;align-items:center;gap:.6rem}
.sb-ava{width:34px;height:34px;border-radius:50%;object-fit:cover;border:2px solid var(--border)}
.sb-ava-ph{width:34px;height:34px;border-radius:50%;background:linear-gradient(135deg,var(--red),var(--red-dark));display:flex;align-items:center;justify-content:center;font-size:.85rem;color:#fff;font-weight:700}
.sb-uname{font-size:.85rem;font-weight:700;color:var(--text)}
.sb-logout{margin-left:auto;background:none;border:none;color:var(--muted);cursor:pointer;font-size:.9rem;padding:.25rem;border-radius:5px;transition:color .2s}
.sb-logout:hover{color:var(--red)}

/* MAIN */
.main{margin-left:var(--sw);min-height:100vh;position:relative;z-index:1;transition:margin-left .35s cubic-bezier(.4,0,.2,1)}
@media(max-width:900px){.main{margin-left:0}}
.topbar{position:sticky;top:0;z-index:900;background:rgba(8,8,8,.92);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);padding:0 1.5rem;height:60px;display:flex;align-items:center;justify-content:space-between}
.tb-left{display:flex;align-items:center;gap:.85rem}
.tb-right{display:flex;align-items:center;gap:.75rem}
.mob-btn{display:none;background:none;border:1px solid var(--border);border-radius:6px;color:var(--text);width:36px;height:36px;align-items:center;justify-content:center;cursor:pointer;font-size:1.1rem;transition:all .2s}
.mob-btn:hover{border-color:var(--red);color:var(--red)}
@media(max-width:900px){.mob-btn{display:flex}}
.page-title{font-family:'Orbitron',monospace;font-size:.82rem;font-weight:700;letter-spacing:.8px}
.page-title span{color:var(--red)}
.status-pill{display:flex;align-items:center;gap:.38rem;padding:.32rem .8rem;border-radius:50px;font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.8px}
.pill-on{background:rgba(34,197,94,.1);color:var(--green);border:1px solid rgba(34,197,94,.28)}
.pill-off{background:rgba(232,0,13,.09);color:#ff5555;border:1px solid rgba(232,0,13,.22)}
.pill-dot{width:6px;height:6px;border-radius:50%}
.pill-on .pill-dot{background:var(--green);animation:blink 1.5s ease-in-out infinite}
.pill-off .pill-dot{background:#ff5555}
.admin-link{display:flex;align-items:center;gap:.35rem;padding:.32rem .75rem;border-radius:6px;font-size:.78rem;font-weight:700;color:var(--yellow);background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.22);text-decoration:none;transition:all .2s}
.admin-link:hover{background:rgba(245,158,11,.15);color:var(--yellow)}

/* TABS */
.tabs{display:flex;padding:0 1.5rem;border-bottom:1px solid var(--border);background:var(--bg2);overflow-x:auto}
.tab-btn{display:flex;align-items:center;gap:.5rem;padding:.95rem 1.2rem;background:none;border:none;border-bottom:2px solid transparent;color:var(--muted);font-family:'Rajdhani',sans-serif;font-size:.88rem;font-weight:600;text-transform:uppercase;letter-spacing:.5px;cursor:pointer;transition:all .2s;white-space:nowrap}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:var(--red);border-bottom-color:var(--red)}
.tab-btn i{font-size:.82rem}

/* CONTENT */
.area{padding:1.75rem 1.5rem}
.tab-p{display:none;animation:fadeUp .3s ease}
.tab-p.active{display:block}
@keyframes fadeUp{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:translateY(0)}}
.card{background:var(--bg2);border:1px solid var(--border2);border-radius:12px;margin-bottom:1.4rem;overflow:hidden;transition:border-color .2s}
.card:hover{border-color:var(--border)}
.card-head{display:flex;align-items:center;justify-content:space-between;padding:.95rem 1.2rem;border-bottom:1px solid var(--border2);background:rgba(0,0,0,.2)}
.ch-title{display:flex;align-items:center;gap:.55rem;font-family:'Orbitron',monospace;font-size:.72rem;font-weight:700;letter-spacing:.8px}
.ch-title i{color:var(--red)}
.card-body{padding:1.2rem}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
@media(max-width:640px){.form-row{grid-template-columns:1fr}}
.fg{margin-bottom:1.2rem}
label{display:block;font-size:.7rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:.45rem}
input[type=text],input[type=number],input[type=password],input[type=url],textarea{width:100%;background:rgba(0,0,0,.35);border:1px solid rgba(255,255,255,.07);border-radius:8px;padding:.75rem 1rem;color:var(--text);font-family:'Rajdhani',sans-serif;font-size:.95rem;transition:all .25s;outline:none}
input:focus,textarea:focus{border-color:var(--red);box-shadow:0 0 0 3px rgba(232,0,13,.12);background:rgba(0,0,0,.5)}
input::placeholder,textarea::placeholder{color:rgba(255,255,255,.17)}
input[readonly]{opacity:.55}
textarea{resize:vertical;min-height:95px}
.hint{font-size:.73rem;color:var(--muted);margin-top:.35rem}
.hint.warn{color:#ff6b6b}
.int-row{display:grid;grid-template-columns:repeat(3,1fr);gap:.65rem}
@media(max-width:480px){.int-row{grid-template-columns:1fr}}
.inp-grp{position:relative}
.inp-grp input{padding-right:2.75rem}
.inp-tog{position:absolute;right:.75rem;top:50%;transform:translateY(-50%);background:none;border:none;color:var(--muted);cursor:pointer;font-size:.95rem;padding:.2rem;border-radius:4px;transition:color .2s}
.inp-tog:hover{color:var(--red)}
.btn-row{display:flex;gap:.5rem;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;gap:.45rem;padding:.6rem 1.2rem;border-radius:8px;font-family:'Rajdhani',sans-serif;font-size:.88rem;font-weight:700;letter-spacing:.3px;cursor:pointer;border:none;transition:all .25s;text-decoration:none}
.btn-red{background:linear-gradient(135deg,var(--red),var(--red-dark));color:#fff;position:relative;overflow:hidden}
.btn-red::before{content:'';position:absolute;top:0;left:-100%;width:100%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.1),transparent);transition:left .4s}
.btn-red:hover::before{left:100%}
.btn-red:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(232,0,13,.4);color:#fff}
.btn-ghost{background:rgba(255,255,255,.04);border:1px solid var(--border2);color:var(--dim)}
.btn-ghost:hover{border-color:var(--red);color:var(--red)}
.btn-del{background:rgba(232,0,13,.06);border:1px solid rgba(232,0,13,.18);color:#ff6b6b}
.btn-del:hover{background:rgba(232,0,13,.14)}
.btn-sm{padding:.38rem .8rem;font-size:.8rem}

/* CHANNELS */
.ch-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:.9rem}
.ch-card{background:rgba(0,0,0,.28);border:1px solid rgba(255,255,255,.05);border-radius:10px;padding:1.05rem;transition:all .25s;position:relative;overflow:hidden}
.ch-card::before{content:'';position:absolute;top:0;left:0;width:3px;height:100%;background:var(--red);opacity:.45;transition:opacity .2s}
.ch-card:hover{border-color:var(--border);transform:translateX(3px)}
.ch-card:hover::before{opacity:1}
.ch-top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:.65rem}
.ch-id{font-family:'Orbitron',monospace;font-size:.68rem;background:var(--red-sub);border:1px solid var(--border);color:var(--red);padding:.22rem .55rem;border-radius:5px;letter-spacing:.8px}
.ch-live{display:flex;align-items:center;gap:.28rem;font-size:.68rem;color:var(--green);font-weight:700;text-transform:uppercase;margin-top:.3rem}
.ch-live .dot{width:5px;height:5px;border-radius:50%;background:var(--green);animation:blink 1.5s ease-in-out infinite}
.ch-acts{display:flex;gap:.35rem}
.ch-int{display:flex;align-items:center;gap:.35rem;font-size:.76rem;color:var(--muted);margin-bottom:.6rem}
.ch-int i{color:var(--red);font-size:.68rem}
.ch-msg{background:rgba(0,0,0,.38);border:1px solid rgba(255,255,255,.04);border-radius:6px;padding:.7rem;font-size:.83rem;color:var(--dim);white-space:pre-wrap;word-break:break-word;max-height:75px;overflow:hidden;position:relative}
.ch-msg::after{content:'';position:absolute;bottom:0;left:0;right:0;height:18px;background:linear-gradient(transparent,rgba(0,0,0,.38))}
.empty{text-align:center;padding:3rem 1rem;color:var(--muted)}
.empty-ic{font-size:2.5rem;color:rgba(232,0,13,.18);margin-bottom:.85rem}
.badge{display:inline-flex;align-items:center;gap:.2rem;padding:.18rem .5rem;border-radius:20px;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.4px}
.b-red{background:var(--red-sub);color:var(--red);border:1px solid var(--border)}

/* TOGGLE SWITCH */
.switch-row{display:flex;align-items:center;justify-content:space-between;padding:.9rem 0;border-bottom:1px solid var(--border2)}
.sw-label{font-size:.88rem;font-weight:600}.sw-sub{font-size:.73rem;color:var(--muted);margin-top:.12rem}
.toggle{width:42px;height:22px;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.15);border-radius:50px;position:relative;cursor:pointer;transition:background .3s;flex-shrink:0}
.toggle::after{content:'';position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;transition:transform .3s}
.toggle.on{background:linear-gradient(135deg,var(--red),var(--red-dark));border-color:var(--red);box-shadow:0 0 10px rgba(232,0,13,.3)}
.toggle.on::after{transform:translateX(20px)}

/* SOCIAL */
.social-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:.75rem;margin-top:1.2rem}
.soc-btn{display:flex;align-items:center;gap:.75rem;padding:.9rem 1.1rem;border-radius:11px;color:#fff;text-decoration:none;font-weight:700;font-size:.9rem;transition:all .25s;position:relative;overflow:hidden}
.soc-btn::before{content:'';position:absolute;top:0;left:-100%;width:100%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.1),transparent);transition:left .45s}
.soc-btn:hover::before{left:100%}
.soc-btn:hover{transform:translateY(-3px);filter:brightness(1.1);color:#fff}
.soc-btn i{font-size:1.25rem;flex-shrink:0}
.soc-discord{background:linear-gradient(135deg,#5865f2,#4752c4)}
.soc-youtube{background:linear-gradient(135deg,var(--red),var(--red-dark))}
.soc-github{background:linear-gradient(135deg,#24292e,#111)}
.soc-support{background:linear-gradient(135deg,#e67e22,#d35400)}
.soc-guns{background:linear-gradient(135deg,#a855f7,#7c3aed)}

/* FAB */
.fab-wrap{position:fixed;bottom:2rem;right:2rem;z-index:800}
.fab{width:58px;height:58px;border-radius:50%;border:none;display:flex;align-items:center;justify-content:center;font-size:1.35rem;cursor:pointer;transition:all .3s;position:relative}
.fab::before{content:'';position:absolute;inset:-4px;border-radius:50%;border:2px solid currentColor;opacity:.22}
.fab-start{background:linear-gradient(135deg,#22c55e,#16a34a);color:#fff;box-shadow:0 4px 20px rgba(34,197,94,.4)}
.fab-start:hover{transform:translateY(-4px) scale(1.06);box-shadow:0 10px 30px rgba(34,197,94,.55)}
.fab-stop{background:linear-gradient(135deg,var(--red),var(--red-dark));color:#fff;box-shadow:0 4px 20px var(--red-glow)}
.fab-stop:hover{transform:translateY(-4px) scale(1.06);box-shadow:0 12px 32px rgba(232,0,13,.6)}

/* ALERTS */
.alerts{position:fixed;top:1.25rem;right:1.25rem;z-index:9999;display:flex;flex-direction:column;gap:.6rem;max-width:380px}
.alert{background:var(--bg2);border:1px solid var(--border2);border-radius:10px;padding:.82rem 1.1rem;display:flex;align-items:center;gap:.7rem;font-size:.88rem;animation:slideIn .35s ease;backdrop-filter:blur(8px)}
@keyframes slideIn{from{opacity:0;transform:translateX(60px)}to{opacity:1;transform:translateX(0)}}
.alert i{flex-shrink:0;font-size:.95rem}
.alert-success{border-left:3px solid var(--green)}.alert-success i{color:var(--green)}
.alert-danger{border-left:3px solid var(--red)}.alert-danger i{color:var(--red)}
.alert-warning{border-left:3px solid var(--yellow)}.alert-warning i{color:var(--yellow)}
.alert-info{border-left:3px solid var(--blue)}.alert-info i{color:var(--blue)}
.alert-close{margin-left:auto;background:none;border:none;color:var(--muted);cursor:pointer;font-size:1rem}
.overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:999}
@media(max-width:900px){.sb-open .overlay{display:block}}
hr{border:none;border-top:1px solid var(--border2);margin:1.25rem 0}
p{line-height:1.7;color:var(--dim);margin-bottom:.75rem}
.prem-badge{display:inline-flex;align-items:center;gap:.4rem;padding:.25rem .7rem;border-radius:20px;font-size:.75rem;font-weight:700;letter-spacing:.5px}
.prem-badge.free{background:rgba(255,255,255,.08);color:#aaa;border:1px solid rgba(255,255,255,.12)}
.prem-badge.active{background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;border:none;box-shadow:0 0 12px rgba(245,158,11,.4)}
.prem-widget{background:#181818;border:1px solid rgba(245,158,11,.3);border-radius:14px;padding:1.2rem;margin-bottom:1rem}
.prem-widget.free{border-color:rgba(255,255,255,.1)}
.prem-plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.75rem;margin-top:1rem}
.plan-card{border-radius:12px;padding:1rem;text-align:center;border:1px solid rgba(255,255,255,.1);background:#111}
.plan-card .plan-emoji{font-size:1.6rem;display:block;margin-bottom:.4rem}
.plan-card .plan-name{font-weight:700;font-size:.95rem;color:#eee}
.plan-card .plan-price{color:#f59e0b;font-weight:700;font-size:1rem;margin:.3rem 0}
.plan-card .plan-dur{color:#777;font-size:.78rem}
.btn-buy{display:inline-block;margin-top:.6rem;padding:.35rem .9rem;background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;border-radius:8px;font-weight:700;font-size:.8rem;border:none;cursor:pointer;text-decoration:none;transition:all .2s}
.btn-buy:hover{filter:brightness(1.15);transform:translateY(-1px)}
.limit-bar{display:flex;align-items:center;justify-content:space-between;margin-bottom:.4rem;font-size:.82rem}
.limit-track{background:rgba(255,255,255,.08);border-radius:4px;height:6px;flex:1;margin:0 .6rem}
.limit-fill{height:100%;border-radius:4px;background:#e8000d;transition:width .4s}
.limit-fill.ok{background:#22c55e}
.limit-fill.warn{background:#f59e0b}
.prem-admin-select{background:#181818;color:#eee;border:1px solid rgba(255,255,255,.15);border-radius:8px;padding:.3rem .6rem;font-size:.8rem}
.btn-prem-grant{background:linear-gradient(135deg,#f59e0b,#d97706);color:#000;border:none;border-radius:8px;padding:.3rem .8rem;font-size:.8rem;font-weight:700;cursor:pointer}
.btn-prem-grant:hover{filter:brightness(1.1)}
.btn-prem-remove{background:rgba(232,0,13,.2);color:#e8000d;border:1px solid rgba(232,0,13,.3);border-radius:8px;padding:.3rem .8rem;font-size:.8rem;cursor:pointer}
</style>
</head>
<body>
<div class="overlay" id="overlay" onclick="closeSb()"></div>

{% with messages = get_flashed_messages(with_categories=true) %}
{% if messages %}
<div class="alerts" id="alertBox">
{% for cat, msg in messages %}
<div class="alert alert-{{ cat }}">
  <i class="fas fa-{{ 'check-circle' if cat=='success' else 'times-circle' if cat=='danger' else 'info-circle' if cat=='info' else 'exclamation-triangle' }}"></i>
  <span>{{ msg }}</span>
  <button class="alert-close" onclick="this.parentElement.remove()">×</button>
</div>
{% endfor %}
</div>
{% endif %}
{% endwith %}

<!-- SIDEBAR -->
<aside class="sb" id="sb">
  <div class="sb-logo">
    <div class="sb-brand"><i class="fas fa-satellite-dish"></i> C3B1XHUB</div>
    <div class="sb-sub">Auto Post Manager</div>
  </div>

  <div class="bot-chip {{ 'on' if current_token_data.posting_active else '' }}">
    <div class="bot-name">{{ current_token_data.name }}</div>
    <div class="stat-dot {{ 'on' if current_token_data.posting_active else 'off' }}">
      {{ 'Running' if current_token_data.posting_active else 'Stopped' }}
    </div>
  </div>

  <div class="sb-sect">Switch Token</div>
  <div class="sb-scroll">
    {% for idx in range(config.tokens | length) %}
    {% set tok = config.tokens[idx] %}
    <a href="/switch-token/{{ idx }}" class="tok-item {{ 'active' if idx == config.current_token_index else '' }}">
      <div class="tok-ic"><i class="fas fa-robot"></i></div>
      <div style="flex:1;min-width:0">
        <div class="tok-nm">{{ tok.name }}</div>
        <div class="tok-meta">{{ tok.channels|length }} channels</div>
      </div>
    </a>
    {% endfor %}
    <a href="/add-new-token" class="add-tok" style="margin-top:.65rem">
      <i class="fas fa-plus-circle"></i> Add New Token
    </a>
  </div>

  <!-- User info at bottom -->
  {% set u = session.get('user', {}) %}
  <div class="sb-user">
    {% if u.get('avatar') %}
    <img class="sb-ava" src="https://cdn.discordapp.com/avatars/{{ u.discord_id }}/{{ u.avatar }}.png?size=64" alt="">
    {% else %}
    <div class="sb-ava-ph">{{ u.get('username', '?')[:1].upper() }}</div>
    {% endif %}
    <div style="min-width:0">
      <div class="sb-uname" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{{ u.get('username','User') }}</div>
    </div>
    <a href="/logout" class="sb-logout" title="Logout"><i class="fas fa-sign-out-alt"></i></a>
  </div>
</aside>

<!-- MAIN -->
<main class="main">
  <nav class="topbar">
    <div class="tb-left">
      <button class="mob-btn" onclick="toggleSb()"><i class="fas fa-bars"></i></button>
      <div class="page-title">Auto Post / <span>{{ current_token_data.name }}</span></div>
    </div>
    <div class="tb-right">
      {% if session.get('user', {}).get('is_admin') %}
      <a href="/admin" class="admin-link"><i class="fas fa-shield-halved"></i> Admin</a>
      {% endif %}
      <div class="status-pill {{ 'pill-on' if current_token_data.posting_active else 'pill-off' }}">
        <span class="pill-dot"></span>
        {{ 'Running' if current_token_data.posting_active else 'Stopped' }}
      </div>
    </div>
  </nav>

  <!-- TABS -->
  <div class="tabs" id="tabsBar">
    <button class="tab-btn active" data-tab="channels"><i class="fas fa-hashtag"></i> Channels</button>
    <button class="tab-btn" data-tab="settings"><i class="fas fa-cog"></i> Settings</button>
    <button class="tab-btn" data-tab="webhook"><i class="fas fa-link"></i> Webhook</button>
    <button class="tab-btn" data-tab="credit"><i class="fas fa-heart"></i> Credit</button>
  </div>

  <div class="area">

    <!-- ─── CHANNELS ─── -->
    <div class="tab-p active" id="tab-channels">
      <div class="card">
        <div class="card-head">
          <div class="ch-title">
            <i class="fas fa-{{ 'pen' if editing else 'plus-circle' }}"></i>
            {{ 'Edit Channel' if editing else 'Add New Channel' }}
          </div>
        </div>
        <div class="card-body">
          <form method="post" action="/save-config">
            <input type="hidden" name="action" value="{{ 'edit' if editing else 'add' }}">
            <input type="hidden" name="original_channel_id" value="{{ original_channel_id if editing else '' }}">
            <div class="form-row">
              <div class="fg">
                <label>Channel ID</label>
                <input type="text" name="channel_id" value="{{ channel_id or '' }}"
                       {{ 'readonly' if editing else '' }}
                       placeholder="e.g. 123456789012345678" required>
                <div class="hint">Discord channel target ID</div>
              </div>
              <div class="fg">
                <label>Posting Interval</label>
                <div class="int-row">
                  <div><label style="font-size:.62rem">Hours</label><input type="number" name="hours" value="{{ hours or 0 }}" min="0" required></div>
                  <div><label style="font-size:.62rem">Minutes</label><input type="number" name="minutes" value="{{ minutes or 0 }}" min="0" max="59" required></div>
                  <div><label style="font-size:.62rem">Seconds</label><input type="number" name="seconds" value="{{ seconds or 0 }}" min="0" max="59" required></div>
                </div>
              </div>
            </div>
            <div class="fg">
              <label>Message Content</label>
              <textarea name="message" rows="4" placeholder="Enter message... (Discord markdown supported)">{{ channel_message or '' }}</textarea>
            </div>
            <div class="btn-row">
              <button type="submit" class="btn btn-red">
                <i class="fas fa-{{ 'sync-alt' if editing else 'save' }}"></i>
                {{ 'Update Channel' if editing else 'Add Channel' }}
              </button>
              {% if editing %}<a href="/" class="btn btn-ghost">Cancel</a>{% endif %}
            </div>
          </form>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="ch-title"><i class="fas fa-list-check"></i> Active Channels</div>
          <span class="badge b-red">{{ current_token_data.channels|length }}</span>
        </div>
        <div class="card-body">
          {% if current_token_data.channels %}
          <div class="ch-grid">
            {% for ch in current_token_data.channels %}
            <div class="ch-card">
              <div class="ch-top">
                <div>
                  <div class="ch-id">#{{ ch.id }}</div>
                  {% if current_token_data.posting_active %}
                  <div class="ch-live"><span class="dot"></span> Live</div>
                  {% endif %}
                </div>
                <div class="ch-acts">
                  <a href="/edit-channel?channel_id={{ ch.id }}&message={{ ch.message|urlencode }}&hours={{ ch.interval//3600 }}&minutes={{ (ch.interval%3600)//60 }}&seconds={{ ch.interval%60 }}"
                     class="btn btn-ghost btn-sm"><i class="fas fa-pen"></i></a>
                  <form method="post" action="/save-config" onsubmit="return confirm('Remove channel?')" style="display:inline">
                    <input type="hidden" name="action" value="remove">
                    <input type="hidden" name="channel_id" value="{{ ch.id }}">
                    <button type="submit" class="btn btn-del btn-sm"><i class="fas fa-trash"></i></button>
                  </form>
                </div>
              </div>
              <div class="ch-int"><i class="fas fa-clock"></i> Every {{ ch.interval//3600 }}h {{ (ch.interval%3600)//60 }}m {{ ch.interval%60 }}s</div>
              <div class="ch-msg">{{ ch.message }}</div>
            </div>
            {% endfor %}
          </div>
          {% else %}
          <div class="empty">
            <div class="empty-ic"><i class="fas fa-hashtag"></i></div>
            <div style="font-family:'Orbitron',monospace;font-size:.88rem;color:var(--dim);margin-bottom:.4rem">No channels configured</div>
            <div>Add your first channel above to start auto-posting</div>
          </div>
          {% endif %}
        </div>
      </div>
    </div>

    <!-- ─── SETTINGS ─── -->
    <div class="tab-p" id="tab-settings">
      <div class="card">
        <div class="card-head"><div class="ch-title"><i class="fas fa-key"></i> Discord Bot Token</div></div>
        <div class="card-body">
          <form method="post" action="/save-config">
            <div class="fg">
              <label>Bot Token</label>
              <div class="inp-grp">
                <input type="password" name="token" id="tokenInput" value="{{ current_token_data.token }}" required>
                <button type="button" class="inp-tog" id="tokTog"><i class="fas fa-eye" id="tokEye"></i></button>
              </div>
              <div class="hint warn"><i class="fas fa-exclamation-triangle"></i> Keep your token private — never share it</div>
            </div>
            <button type="submit" class="btn btn-red"><i class="fas fa-save"></i> Save Token</button>
          </form>
        </div>
      </div>
    </div>

    <!-- ─── WEBHOOK ─── -->
    <div class="tab-p" id="tab-webhook">
      <div class="card">
        <div class="card-head"><div class="ch-title"><i class="fas fa-link"></i> Webhook Configuration</div></div>
        <div class="card-body">
          <form method="post" action="/save-config" id="webhookForm">
            <div class="switch-row">
              <div>
                <div class="sw-label">Enable Webhook Logging</div>
                <div class="sw-sub">Receive post status updates via Discord webhook</div>
              </div>
              <input type="checkbox" name="use_webhook" id="whCheck" {% if current_token_data.use_webhook %}checked{% endif %} style="display:none">
              <div class="toggle {{ 'on' if current_token_data.use_webhook else '' }}" id="whTog" onclick="toggleWh()"></div>
            </div>
            <div class="fg" style="margin-top:1.1rem">
              <label>Webhook URL</label>
              <input type="url" name="webhook_url" value="{{ current_token_data.webhook_url }}" placeholder="https://discord.com/api/webhooks/...">
            </div>
            <div class="btn-row">
              <button type="submit" class="btn btn-red"><i class="fas fa-save"></i> Save Webhook</button>
              <button type="button" class="btn btn-ghost" onclick="testWh()"><i class="fas fa-paper-plane"></i> Test Webhook</button>
            </div>
          </form>
        </div>
      </div>
    </div>

    <!-- ─── CREDIT ─── -->
    <div class="tab-p" id="tab-credit">
      <div style="background:linear-gradient(135deg,var(--red-sub),transparent);border:1px solid var(--border);border-radius:12px;padding:1.5rem;margin-bottom:1.4rem;position:relative;overflow:hidden">
        <div style="position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,var(--red),transparent)"></div>
        <div style="font-family:'Orbitron',monospace;font-size:1.15rem;color:var(--red);text-shadow:0 0 14px var(--red-glow);margin-bottom:.4rem">AUTO POST DISCORD</div>
        <p style="margin:0;font-size:.9rem">Multi-token Discord auto-poster built with Flask</p>
      </div>
      <div class="card">
        <div class="card-head"><div class="ch-title"><i class="fas fa-info-circle"></i> About This Tool</div></div>
        <div class="card-body">
          <p>This tool automates posting messages to Discord channels using multiple bot tokens. Manage multiple bots, each with their own channels, custom messages, and posting intervals.</p>
          <p>Built with Flask and crafted for simplicity. Supports Discord OAuth2 login and admin panel.</p>
          <hr>
          <!-- PREMIUM WIDGET -->
{% if user_is_premium %}
<div class="prem-widget" style="margin-bottom:1rem">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem">
    <div>
      <span class="prem-badge active">
        {{ prem_info.emoji if prem_info else '👑' }} PREMIUM {{ prem_info.label if prem_info else 'ADMIN' }}
      </span>
    </div>
    {% if prem_info and prem_info.expires %}
    <div style="font-size:.78rem;color:#aaa">
      <i class="fas fa-clock"></i> Expires {{ prem_info.expires | int | datetimeformat if prem_info.expires else '' }}
    </div>
    {% endif %}
  </div>
  <div style="margin-top:.6rem;font-size:.82rem;color:#aaa"><i class="fas fa-infinity"></i> Unlimited Token &nbsp;·&nbsp; <i class="fas fa-infinity"></i> Unlimited Channel</div>
</div>
{% else %}
<div class="prem-widget free" style="margin-bottom:1rem">
  <div style="display:flex;align-items:center;justify-content:space-between">
    <span class="prem-badge free">🆓 FREE USER</span>
    <span style="font-size:.78rem;color:#aaa">Token: {{ config.tokens|length }}/{{ token_limit }} · Channel: {{ current_token_data.channels|length }}/{{ channel_limit }}</span>
  </div>
  <!-- Limit bar Token -->
  <div style="margin-top:.7rem">
    <div class="limit-bar"><span style="color:#aaa">Token</span><div class="limit-track"><div class="limit-fill {% if config.tokens|length >= token_limit %}{% else %}ok{% endif %}" style="width:{{ [config.tokens|length * 100 // token_limit, 100]|min }}%"></div></div><span style="color:#aaa">{{ config.tokens|length }}/{{ token_limit }}</span></div>
    <div class="limit-bar"><span style="color:#aaa">Channel</span><div class="limit-track"><div class="limit-fill {% if current_token_data.channels|length >= channel_limit %}{% else %}{{ 'warn' if current_token_data.channels|length >= (channel_limit - 1) else 'ok' }}{% endif %}" style="width:{{ [current_token_data.channels|length * 100 // channel_limit, 100]|min }}%"></div></div><span style="color:#aaa">{{ current_token_data.channels|length }}/{{ channel_limit }}</span></div>
  </div>
  <div style="margin-top:.8rem;font-size:.82rem;color:#aaa;margin-bottom:.6rem">⬆️ Upgrade Premium untuk Unlimited Token & Channel</div>
  <div class="prem-plans">
    {% for key, plan in premium_plans.items() %}
    <div class="plan-card">
      <span class="plan-emoji">{{ plan.emoji }}</span>
      <div class="plan-name">{{ plan.label }}</div>
      <div class="plan-price">{{ plan.price_fmt }}</div>
      <div class="plan-dur">Unlimited semua fitur</div>
      <a href="https://saweria.co/BuronanBelang" target="_blank" class="btn-buy">Beli Sekarang</a>
    </div>
    {% endfor %}
  </div>
</div>
{% endif %}
          <div class="ch-title" style="margin-bottom:1rem"><i class="fas fa-code"></i> Developer &amp; Social</div>
          <p>Developed by <strong style="color:var(--red)">C3B1XHUB</strong></p>
          <div class="social-grid">
<a href="https://discord.com/invite/psdQaVEnHt" target="_blank" class="soc-btn soc-discord">
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" viewBox="0 0 127.14 96.36" style="flex-shrink:0"><path d="M107.7,8.07A105.15,105.15,0,0,0,81.47,0a72.06,72.06,0,0,0-3.36,6.83A97.68,97.68,0,0,0,49,6.83,72.37,72.37,0,0,0,45.64,0,105.89,105.89,0,0,0,19.39,8.09C2.79,32.65-1.71,56.6.54,80.21h0A105.73,105.73,0,0,0,32.71,96.36,77.7,77.7,0,0,0,39.6,85.25a68.42,68.42,0,0,1-10.85-5.18c.91-.66,1.8-1.34,2.66-2a75.57,75.57,0,0,0,64.32,0c.87.71,1.76,1.39,2.66,2a68.68,68.68,0,0,1-10.87,5.19,77,77,0,0,0,6.89,11.1A105.25,105.25,0,0,0,126.6,80.22h0C129.24,52.84,122.09,29.11,107.7,8.07ZM42.45,65.69C36.18,65.69,31,60,31,53s5-12.74,11.43-12.74S54,46,53.89,53,48.84,65.69,42.45,65.69Zm42.24,0C78.41,65.69,73.25,60,73.25,53s5-12.74,11.44-12.74S96.23,46,96.12,53,91.08,65.69,84.69,65.69Z"/></svg>
              Discord Community
            </a>
            <a href="https://guns.lol" target="_blank" class="soc-btn soc-guns">
              <i class="fas fa-globe"></i> guns.lol
            </a>
            <a href="https://github.com/LRiqlapa" target="_blank" class="soc-btn soc-github">
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" viewBox="0 0 16 16" style="flex-shrink:0"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.012 8.012 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
              GitHub Profile
            </a>
            <a href="https://saweria.co/BuronanBelang" target="_blank" class="soc-btn soc-support">
              <i class="fas fa-hand-holding-heart"></i> Support Project
            </a>
          </div>
        </div>
      </div>
    </div>

  </div><!-- end area -->
</main>

<!-- FAB -->
<div class="fab-wrap">
  {% if current_token_data.posting_active %}
  <form action="/stop" method="post"><button type="submit" class="fab fab-stop" title="Stop"><i class="fas fa-stop"></i></button></form>
  {% else %}
  <form action="/start" method="post"><button type="submit" class="fab fab-start" title="Start"><i class="fas fa-play"></i></button></form>
  {% endif %}
</div>

<script>
// TABS
document.querySelectorAll('.tab-btn').forEach(b=>{
  b.addEventListener('click',()=>{
    const t=b.dataset.tab;
    document.querySelectorAll('.tab-btn').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.tab-p').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
    document.getElementById('tab-'+t).classList.add('active');
  });
});
const h=location.hash.replace('#','');
if(h){const b=document.querySelector('[data-tab="'+h+'"]');if(b)b.click();}

// SIDEBAR
function toggleSb(){document.body.classList.toggle('sb-open')}
function closeSb(){document.body.classList.remove('sb-open')}

// TOKEN TOGGLE
document.getElementById('tokTog')?.addEventListener('click',function(){
  const i=document.getElementById('tokenInput'),e=document.getElementById('tokEye');
  if(i.type==='password'){i.type='text';e.classList.replace('fa-eye','fa-eye-slash')}
  else{i.type='password';e.classList.replace('fa-eye-slash','fa-eye')}
});

// WEBHOOK TOGGLE
function toggleWh(){
  const t=document.getElementById('whTog'),c=document.getElementById('whCheck');
  t.classList.toggle('on');c.checked=t.classList.contains('on');
}

// TEST WEBHOOK
function testWh(){
  fetch('/test-webhook',{method:'POST'})
    .then(r=>showToast(r.ok?'Webhook test sent!':'Failed to send test.',r.ok?'success':'danger'))
    .catch(()=>showToast('Error sending test.','danger'));
}

// TOAST
function showToast(msg,type='info'){
  const icons={success:'check-circle',danger:'times-circle',warning:'exclamation-triangle',info:'info-circle'};
  let box=document.querySelector('.alerts');
  if(!box){box=document.createElement('div');box.className='alerts';document.body.appendChild(box)}
  const d=document.createElement('div');
  d.className='alert alert-'+type;
  d.innerHTML=`<i class="fas fa-${icons[type]}"></i><span>${msg}</span><button class="alert-close" onclick="this.parentElement.remove()">×</button>`;
  box.prepend(d);
  setTimeout(()=>{d.style.transition='opacity .5s';d.style.opacity='0';setTimeout(()=>d.remove(),500)},4500);
}

// AUTO-DISMISS
setTimeout(()=>{document.querySelectorAll('.alert').forEach(a=>{a.style.transition='opacity .5s';a.style.opacity='0';setTimeout(()=>a.remove(),500)})},5500);
</script>
</body>
</html>
'''

# =================== INIT & RUN ===================
init_db()

if __name__ == "__main__":
    load_config()
    app.run(debug=True, host="0.0.0.0", port=5000)
