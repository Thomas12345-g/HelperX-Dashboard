from flask import Flask, render_template, redirect, url_for, session, request, jsonify
import requests
import os
import secrets
from functools import wraps
from bot import bot, start_bot, send_message_sync, is_bot_ready, send_dm_sync
import logging

# Logging konfigurieren
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))

# Discord OAuth2 Konfiguration
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:5000/callback")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_TOKEN")

# Discord API Endpunkte
DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_AUTHORIZE_URL = f"{DISCORD_API_BASE}/oauth2/authorize"
DISCORD_TOKEN_URL = f"{DISCORD_API_BASE}/oauth2/token"
DISCORD_USER_URL = f"{DISCORD_API_BASE}/users/@me"
DISCORD_GUILDS_URL = f"{DISCORD_API_BASE}/users/@me/guilds"

# Bot beim Start initialisieren (wenn nicht schon geschehen)
if not is_bot_ready():
    start_bot()
    logging.info("Bot-Start initiiert...")

# Login erforderlich Decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Admin-Check Decorator (optional)
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin', False):
            return jsonify({"error": "Admin-Berechtigung erforderlich"}), 403
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    """Startseite mit Dashboard wenn eingeloggt"""
    if 'user_id' in session:
        return render_template('dashboard.html', 
                             username=session.get('username'),
                             avatar=session.get('avatar'),
                             bot_ready=is_bot_ready())
    return render_template('index.html')

@app.route('/login')
def login():
    """Discord OAuth Login starten"""
    # Generiere State für CSRF-Schutz
    state = secrets.token_urlsafe(32)
    session['oauth_state'] = state
    
    # Parameter für Discord OAuth
    params = {
        'client_id': DISCORD_CLIENT_ID,
        'redirect_uri': DISCORD_REDIRECT_URI,
        'response_type': 'code',
        'state': state,
        'scope': 'identify guilds',
        'prompt': 'none'
    }
    
    # Authorization URL erstellen
    auth_url = f"{DISCORD_AUTHORIZE_URL}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
    return redirect(auth_url)

@app.route('/callback')
def callback():
    """OAuth Callback von Discord"""
    # CSRF-Schutz prüfen
    if request.args.get('state') != session.get('oauth_state'):
        return "Invalid state parameter", 400
    
    # Authorization Code abrufen
    code = request.args.get('code')
    if not code:
        return "No code provided", 400
    
    # Token von Discord anfordern
    data = {
        'client_id': DISCORD_CLIENT_ID,
        'client_secret': DISCORD_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': DISCORD_REDIRECT_URI
    }
    
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    
    response = requests.post(DISCORD_TOKEN_URL, data=data, headers=headers)
    if response.status_code != 200:
        return f"Token exchange failed: {response.text}", 400
    
    token_data = response.json()
    access_token = token_data.get('access_token')
    
    # Benutzerinformationen abrufen
    user_headers = {'Authorization': f'Bearer {access_token}'}
    user_response = requests.get(DISCORD_USER_URL, headers=user_headers)
    
    if user_response.status_code != 200:
        return "Failed to get user info", 400
    
    user_data = user_response.json()
    
    # In Session speichern
    session['user_id'] = user_data['id']
    session['username'] = user_data['username']
    session['discriminator'] = user_data.get('discriminator', '0')
    session['avatar'] = user_data.get('avatar')
    session['access_token'] = access_token
    
    # Prüfen ob Benutzer Admin ist (hier nach eigenen Kriterien)
    # Z.B. bestimmte Discord IDs oder Server-Rollen
    admin_ids = os.getenv("ADMIN_USER_IDS", "").split(",")
    session['is_admin'] = session['user_id'] in admin_ids
    
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
@login_required
def dashboard():
    """Dashboard Hauptseite"""
    return render_template('dashboard.html',
                         username=session.get('username'),
                         avatar=session.get('avatar'),
                         user_id=session.get('user_id'),
                         is_admin=session.get('is_admin', False),
                         bot_ready=is_bot_ready())

@app.route('/logout')
def logout():
    """Logout und Session löschen"""
    session.clear()
    return redirect(url_for('index'))

# API Endpunkte für das Dashboard
@app.route('/api/bot/status', methods=['GET'])
@login_required
def api_bot_status():
    """Bot-Status API"""
    return jsonify({
        'bot_ready': is_bot_ready(),
        'bot_user': str(bot.user) if bot and bot.user else None,
        'guild_count': len(bot.guilds) if bot and bot.is_ready() else 0
    })

@app.route('/api/guilds', methods=['GET'])
@login_required
def api_get_guilds():
    """Liste der Server abrufen, auf denen der Bot ist"""
    if not is_bot_ready():
        return jsonify({'error': 'Bot not ready'}), 503
    
    guilds = []
    for guild in bot.guilds:
        guilds.append({
            'id': guild.id,
            'name': guild.name,
            'icon': str(guild.icon.url) if guild.icon else None,
            'member_count': guild.member_count,
            'channels': [{'id': ch.id, 'name': ch.name, 'type': str(ch.type)} 
                        for ch in guild.channels if hasattr(ch, 'send')]
        })
    
    return jsonify(guilds)

@app.route('/api/send_message', methods=['POST'])
@login_required
def api_send_message():
    """Nachricht in einen Channel senden"""
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
    """Direktnachricht an Benutzer senden"""
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

@app.route('/api/user_guilds', methods=['GET'])
@login_required
def api_user_guilds():
    """Server abrufen, auf denen der eingeloggte Benutzer ist"""
    access_token = session.get('access_token')
    if not access_token:
        return jsonify({'error': 'Not authenticated'}), 401
    
    headers = {'Authorization': f'Bearer {access_token}'}
    response = requests.get(DISCORD_GUILDS_URL, headers=headers)
    
    if response.status_code != 200:
        return jsonify({'error': 'Failed to fetch guilds'}), 500
    
    user_guilds = response.json()
    
    # Server filtern, auf denen der Benutzer Admin ist
    admin_guilds = [g for g in user_guilds if (g['permissions'] & 0x8) == 0x8]
    
    return jsonify({
        'all_guilds': user_guilds,
        'admin_guilds': admin_guilds
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)