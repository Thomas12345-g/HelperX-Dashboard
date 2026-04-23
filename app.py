from flask import Flask, render_template, redirect, url_for, session, request, jsonify
import requests
import os
import secrets
from functools import wraps
import threading
import time
import logging
import asyncio

# Logging konfigurieren
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))

# Discord OAuth2 Konfiguration
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "https://deine-app.onrender.com/callback")

# Bot-Import
from bot import bot, start_bot, send_message_sync, is_bot_ready, send_dm_sync

# ═══════════════════════════════════════════════════════════════════════════════
#  BOT AUTOMATISCH STARTEN (Für Render)
# ═══════════════════════════════════════════════════════════════════════════════
def start_bot_in_background():
    """Startet den Bot im Hintergrund (für Render)"""
    try:
        logger.info("🚀 Starte Discord-Bot im Hintergrund...")
        # Starte den Bot (nicht-blockierend)
        start_bot()
        
        # Warte kurz auf Bot-Start
        time.sleep(3)
        if is_bot_ready():
            logger.info("✅ Bot ist bereit!")
        else:
            logger.warning("⚠️  Bot startet noch...")
    except Exception as e:
        logger.error(f"❌ Bot-Fehler: {e}")

# Bot automatisch starten wenn die App läuft
# Wichtig: use_reloader=False in app.run() verhindert doppelten Start
if not os.environ.get('WERKZEUG_RUN_MAIN'):
    # Startet nur einmal, nicht beim Auto-Reload
    start_bot_in_background()

# ═══════════════════════════════════════════════════════════════════════════════
#  FLASK ROUTES (wie zuvor)
# ═══════════════════════════════════════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    if 'user_id' in session:
        return render_template('dashboard.html', 
                             username=session.get('username'),
                             avatar=session.get('avatar'),
                             user_id=session.get('user_id'),
                             is_admin=session.get('is_admin', False),
                             bot_ready=is_bot_ready())
    return render_template('login.html')

@app.route('/login')
def login():
    """Discord OAuth Login"""
    state = secrets.token_urlsafe(32)
    session['oauth_state'] = state
    
    params = {
        'client_id': DISCORD_CLIENT_ID,
        'redirect_uri': DISCORD_REDIRECT_URI,
        'response_type': 'code',
        'state': state,
        'scope': 'identify guilds'
    }
    
    auth_url = f"https://discord.com/api/oauth2/authorize?{'&'.join([f'{k}={v}' for k,v in params.items()])}"
    return redirect(auth_url)

@app.route('/callback')
def callback():
    """OAuth Callback"""
    if request.args.get('state') != session.get('oauth_state'):
        return "Invalid state", 400
    
    code = request.args.get('code')
    if not code:
        return "No code", 400
    
    data = {
        'client_id': DISCORD_CLIENT_ID,
        'client_secret': DISCORD_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': DISCORD_REDIRECT_URI
    }
    
    response = requests.post('https://discord.com/api/oauth2/token', 
                            data=data, 
                            headers={'Content-Type': 'application/x-www-form-urlencoded'})
    
    if response.status_code != 200:
        return "Token exchange failed", 400
    
    token_data = response.json()
    access_token = token_data.get('access_token')
    
    # Benutzerinfo abrufen
    user_response = requests.get('https://discord.com/api/users/@me',
                                headers={'Authorization': f'Bearer {access_token}'})
    
    if user_response.status_code != 200:
        return "Failed to get user", 400
    
    user_data = user_response.json()
    
    session['user_id'] = user_data['id']
    session['username'] = user_data['username']
    session['avatar'] = user_data.get('avatar')
    session['access_token'] = access_token
    
    admin_ids = os.getenv("ADMIN_USER_IDS", "").split(",")
    session['is_admin'] = session['user_id'] in admin_ids
    
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html',
                         username=session.get('username'),
                         avatar=session.get('avatar'),
                         user_id=session.get('user_id'),
                         is_admin=session.get('is_admin', False),
                         bot_ready=is_bot_ready())

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# API Endpunkte
@app.route('/api/bot/status', methods=['GET'])
@login_required
def api_bot_status():
    return jsonify({
        'bot_ready': is_bot_ready(),
        'bot_user': str(bot.user) if bot and bot.user else None,
        'guild_count': len(bot.guilds) if bot and bot.is_ready() else 0
    })

@app.route('/api/guilds', methods=['GET'])
@login_required
def api_get_guilds():
    if not is_bot_ready():
        return jsonify({'error': 'Bot not ready'}), 503
    
    guilds = []
    for guild in bot.guilds:
        guilds.append({
            'id': guild.id,
            'name': guild.name,
            'icon': str(guild.icon.url) if guild.icon else None,
            'member_count': guild.member_count,
            'text_channels': [{'id': ch.id, 'name': ch.name} 
                            for ch in guild.text_channels]
        })
    
    return jsonify(guilds)

@app.route('/api/send_message', methods=['POST'])
@login_required
def api_send_message():
    data = request.json
    channel_id = data.get('channel_id')
    message = data.get('message')
    
    if not channel_id or not message:
        return jsonify({'error': 'channel_id and message required'}), 400
    
    if not is_bot_ready():
        return jsonify({'error': 'Bot not ready'}), 503
    
    success = send_message_sync(int(channel_id), message)
    
    if success:
        return jsonify({'success': True, 'message': 'Message sent'})
    else:
        return jsonify({'success': False, 'error': 'Failed to send message'}), 500

@app.route('/api/send_dm', methods=['POST'])
@login_required
def api_send_dm():
    data = request.json
    user_id = data.get('user_id')
    message = data.get('message')
    
    if not user_id or not message:
        return jsonify({'error': 'user_id and message required'}), 400
    
    if not is_bot_ready():
        return jsonify({'error': 'Bot not ready'}), 503
    
    success = send_dm_sync(int(user_id), message)
    
    if success:
        return jsonify({'success': True, 'message': 'DM sent'})
    else:
        return jsonify({'success': False, 'error': 'Failed to send DM'}), 500

# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN - Für Render
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🌐 Starte Flask-Server auf Port {port}")
    
    # Für Render: use_reloader=False ist wichtig!
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,  # Render: Debug muss aus sein
        use_reloader=False  # Verhindert doppelten Bot-Start
    )
