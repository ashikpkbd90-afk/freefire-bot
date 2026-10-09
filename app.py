import sys

# Ensure UTF-8 output and immediate line buffering on Windows consoles
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)

import discord
from discord.ext import commands, tasks
import os
import traceback
import threading
from flask import Flask
import aiohttp
import asyncio
import time
from dotenv import load_dotenv

# Initialize environment variables
load_dotenv()

# Flask Setup for 24/7 Keep-Alive
app = Flask(__name__)
bot_name = "Loading..."
flask_started = False

@app.route('/')
def home():
    """Health check endpoint for Render / UptimeRobot"""
    return f"Bot {bot_name} is operational (200 OK)", 200

@app.route('/health')
def health():
    return "OK", 200

def run_web_server():
    """Run production WSGI server (Waitress) or fallback to Flask with multi-threading"""
    port = int(os.environ.get("PORT", 10000))
    try:
        from waitress import serve
        print(f"Starting Waitress WSGI server on port {port}...")
        serve(app, host='0.0.0.0', port=port, threads=4)
    except Exception as e:
        print(f"Waitress fallback to Flask due to: {e}")
        app.run(host='0.0.0.0', port=port, threaded=True)

def start_background_server():
    global flask_started
    if not flask_started:
        flask_started = True
        t = threading.Thread(target=run_web_server, daemon=True)
        t.start()
        print("Web server started successfully in background.")

# Start the web server immediately at launch so Render port check passes instantly
start_background_server()

# Discord Bot Setup
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("Missing TOKEN in environment")

class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )
        self.session = None

    async def setup_hook(self):
        """Initialize bot components"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        
        # Load cogs
        try:
            await self.load_extension("cogs.infoCommands")
            print("Successfully loaded InfoCommands cog")
        except Exception as e:
            print(f"Failed to load cog: {e}")
            
        try:
            print("Syncing slash commands...")
            await self.tree.sync()
            print("Slash commands synced successfully")
        except Exception as e:
            print(f"Error syncing commands: {e}")
            
        if not self.update_status.is_running():
            self.update_status.start()

    async def on_ready(self):
        """When bot connects to Discord"""
        global bot_name
        bot_name = str(self.user)
        print(f"\nConnected as {bot_name}")
        print(f"Serving {len(self.guilds)} servers")

    @tasks.loop(minutes=5)
    async def update_status(self):
        """Update bot presence periodically"""
        try:
            activity = discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.guilds)} servers"
            )
            await self.change_presence(activity=activity)
        except Exception as e:
            print(f"Status update failed: {e}")

    @update_status.before_loop
    async def before_status_update(self):
        await self.wait_until_ready()

    async def close(self):
        """Cleanup on shutdown"""
        if self.session and not self.session.closed:
            await self.session.close()
        await super().close()

def run_bot_forever():
    """Runs the Discord bot with auto-reconnection and crash resilience"""
    while True:
        try:
            bot = Bot()
            bot.run(TOKEN, reconnect=True)
        except KeyboardInterrupt:
            print("Bot shutdown requested.")
            break
        except Exception as e:
            print(f"⚠️ Bot crashed/disconnected: {e}")
            traceback.print_exc()
            print("Auto-reconnecting bot in 5 seconds...")
            time.sleep(5)

if __name__ == "__main__":
    run_bot_forever()