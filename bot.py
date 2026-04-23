import discord
from discord.ext import commands
import os
import logging
import traceback
import shutil
import sys
import threading
import asyncio
from typing import Optional

# Cache löschen
if os.path.exists("./features/__pycache__"):
    shutil.rmtree("./features/__pycache__")

# Logging konfigurieren
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# ══════════════════════════════════════════════════════════════════════════════
#  MODUS
# ══════════════════════════════════════════════════════════════════════════════
BOT_MODE = "public"  # ← sofortiger Sync, kein Warten!

# ══════════════════════════════════════════════════════════════════════════════
#  SERVER-IDs
# ══════════════════════════════════════════════════════════════════════════════
GUILD_IDS = [
    1477774300508590332,
    1493276175429013735,
    # ← Weitere Server-IDs hier einfügen
]

# ══════════════════════════════════════════════════════════════════════════════
#  BOT-VERSION
# ══════════════════════════════════════════════════════════════════════════════
BOT_VERSION = "9.9"

# ══════════════════════════════════════════════════════════════════════════════
#  API-KEYS — Aus Umgebungsvariablen (kein Hardcoding mehr!)
# ══════════════════════════════════════════════════════════════════════════════
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Prüfen ob Token vorhanden ist
if not DISCORD_TOKEN:
    logging.error("❌ DISCORD_TOKEN nicht in Umgebungsvariablen gefunden!")
    raise ValueError("DISCORD_TOKEN environment variable is required")

# ══════════════════════════════════════════════════════════════════════════════
#  INTENTS
# ══════════════════════════════════════════════════════════════════════════════
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Globale Bot-Instanz (wird beim Import erstellt)
bot: Optional[commands.Bot] = None
_bot_started = False
_bot_thread: Optional[threading.Thread] = None


# ══════════════════════════════════════════════════════════════════════════════
#  BOT-KLASSE
# ══════════════════════════════════════════════════════════════════════════════
class MyBot(commands.Bot):
    async def setup_hook(self):
        logging.info("🔧 setup_hook gestartet...")
        logging.info(f"🌐 BOT_MODE: {BOT_MODE}")

        features_dir = "./features"
        if not os.path.exists(features_dir):
            logging.error(f"❌ Features-Verzeichnis '{features_dir}' nicht gefunden.")
            return

        for filename in sorted(os.listdir(features_dir)):
            if filename.endswith(".py"):
                ext = f"features.{filename[:-3]}"
                try:
                    await self.load_extension(ext)
                    logging.info(f"✅ Feature '{filename}' geladen.")
                except Exception:
                    logging.error(f"❌ Fehler beim Laden von '{filename}':")
                    traceback.print_exc()

        if BOT_MODE == "dev":
            for gid in GUILD_IDS:
                guild = discord.Object(id=gid)
                try:
                    synced = await self.tree.sync(guild=guild)
                    logging.info(
                        f"🔄 [DEV] Guild {gid}: {len(synced)} Command(s) gesynct: "
                        f"{[c.name for c in synced]}"
                    )
                except Exception:
                    logging.error(f"❌ Sync-Fehler für Guild {gid}:")
                    traceback.print_exc()

        elif BOT_MODE == "public":
            # ── 1) Globale Commands syngen ─────────────────────────────────
            try:
                synced = await self.tree.sync()
                logging.info(
                    f"🌍 [PUBLIC] Global gesynct: {len(synced)} Command(s): "
                    f"{[c.name for c in synced]}"
                )
            except Exception:
                logging.error("❌ Globaler Sync-Fehler:")
                traceback.print_exc()

            # ── 2) Guild-spezifische Commands syngen ─────────────────
            for gid in GUILD_IDS:
                guild = discord.Object(id=gid)
                try:
                    synced_guild = await self.tree.sync(guild=guild)
                    logging.info(
                        f"🔄 [PUBLIC] Guild {gid}: {len(synced_guild)} guild-spez. "
                        f"Command(s) gesynct: {[c.name for c in synced_guild]}"
                    )
                except Exception:
                    logging.error(f"❌ Guild-Sync-Fehler für {gid}:")
                    traceback.print_exc()

        else:
            logging.error(f"❌ Unbekannter BOT_MODE: '{BOT_MODE}'")


# ══════════════════════════════════════════════════════════════════════════════
#  BOT INITIALISIEREN (global verfügbar)
# ══════════════════════════════════════════════════════════════════════════════
def create_bot_instance() -> MyBot:
    """Erstellt und konfiguriert eine Bot-Instanz"""
    bot_instance = MyBot(command_prefix="!", intents=intents)
    
    @bot_instance.event
    async def on_ready():
        logging.info(f"✅ {bot_instance.user} ist online! (v{BOT_VERSION} | Modus: {BOT_MODE})")
        logging.info(f"📋 Registrierte Commands: {[c.name for c in bot_instance.tree.get_commands()]}")
        logging.info(f"🏠 Aktive Server: {len(bot_instance.guilds)}")

        post_changelog = getattr(bot_instance, "_helperx_post_changelog", None)
        if post_changelog:
            for gid in GUILD_IDS:
                guild = bot_instance.get_guild(gid)
                if guild:
                    try:
                        await post_changelog(guild)
                        logging.info(f"✅ Changelog gepostet (Guild {gid}).")
                    except Exception:
                        traceback.print_exc()
                else:
                    logging.warning(f"⚠️  Guild {gid} nicht gefunden – Changelog übersprungen.")
        else:
            logging.warning("⚠️  _helperx_post_changelog nicht registriert.")
    
    return bot_instance

# Bot-Instanz global erstellen (beim Import)
bot = create_bot_instance()


# ══════════════════════════════════════════════════════════════════════════════
#  HELPER-FUNKTIONEN FÜR EXTERNE ZUGRIFFE
# ══════════════════════════════════════════════════════════════════════════════

def is_bot_ready() -> bool:
    """Prüft ob der Bot bereit ist"""
    return bot.is_ready() if bot else False


def send_message_sync(channel_id: int, message: str) -> bool:
    """
    Synchroner Wrapper zum Senden von Nachrichten.
    Kann in Flask-Routen verwendet werden.
    
    Args:
        channel_id: Discord Channel ID
        message: Zu sendende Nachricht
    
    Returns:
        bool: Erfolg oder Fehlschlag
    """
    if not bot or not bot.is_ready():
        logging.error("Bot ist nicht bereit zum Senden von Nachrichten")
        return False
    
    channel = bot.get_channel(channel_id)
    if not channel:
        logging.error(f"Channel {channel_id} nicht gefunden")
        return False
    
    # Coroutine im Bot-Event-Loop ausführen
    future = asyncio.run_coroutine_threadsafe(
        channel.send(message),
        bot.loop
    )
    
    try:
        future.result(timeout=10)  # 10 Sekunden Timeout
        return True
    except Exception as e:
        logging.error(f"Fehler beim Senden der Nachricht: {e}")
        return False


def send_dm_sync(user_id: int, message: str) -> bool:
    """
    Synchroner Wrapper zum Senden von Direktnachrichten.
    
    Args:
        user_id: Discord User ID
        message: Zu sendende Nachricht
    
    Returns:
        bool: Erfolg oder Fehlschlag
    """
    if not bot or not bot.is_ready():
        logging.error("Bot ist nicht bereit zum Senden von Nachrichten")
        return False
    
    user = bot.get_user(user_id)
    if not user:
        logging.error(f"User {user_id} nicht gefunden")
        return False
    
    future = asyncio.run_coroutine_threadsafe(
        user.send(message),
        bot.loop
    )
    
    try:
        future.result(timeout=10)
        return True
    except Exception as e:
        logging.error(f"Fehler beim Senden der DM: {e}")
        return False


async def send_message_async(channel_id: int, message: str) -> bool:
    """
    Asynchrone Version zum Senden von Nachrichten.
    Für Verwendung in anderen async-Funktionen.
    """
    if not bot or not bot.is_ready():
        return False
    
    channel = bot.get_channel(channel_id)
    if not channel:
        return False
    
    try:
        await channel.send(message)
        return True
    except Exception as e:
        logging.error(f"Fehler beim async Senden: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  BOT-START (NICHT-BLOCKIEREND)
# ══════════════════════════════════════════════════════════════════════════════

def start_bot() -> bool:
    """
    Startet den Discord-Bot in einem separaten Thread.
    Diese Funktion blockiert nicht und kann parallel zu Flask verwendet werden.
    
    Returns:
        bool: True wenn Start erfolgreich initiiert, False wenn Bot bereits läuft
    """
    global _bot_started, _bot_thread
    
    if _bot_started:
        logging.warning("Bot wurde bereits gestartet")
        return False
    
    def run_bot():
        try:
            logging.info("🚀 Starte Discord-Bot im Hintergrund-Thread...")
            bot.run(DISCORD_TOKEN)
        except Exception as e:
            logging.error(f"❌ Bot-Fehler im Thread: {e}")
            traceback.print_exc()
    
    _bot_thread = threading.Thread(target=run_bot, daemon=True)
    _bot_thread.start()
    _bot_started = True
    
    logging.info("✅ Bot-Start im Hintergrund initiiert")
    return True


def stop_bot():
    """Stoppt den Bot (sofern möglich)"""
    global _bot_started
    
    if bot and bot.is_ready():
        asyncio.run_coroutine_threadsafe(bot.close(), bot.loop)
        _bot_started = False
        logging.info("🛑 Bot wird gestoppt...")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN (NUR BEI DIREKTER AUSFÜHRUNG)
# ══════════════════════════════════════════════════════════════════════════════
def main():
    """Wird nur ausgeführt wenn bot.py direkt gestartet wird"""
    logging.info("Bot wird direkt gestartet (nicht als Modul)")
    start_bot()
    
    # Hier blockieren und auf Thread warten
    if _bot_thread:
        _bot_thread.join()


if __name__ == "__main__":
    main()