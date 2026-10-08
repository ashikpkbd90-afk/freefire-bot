import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
from datetime import datetime, timezone
import json
import os
import re
import gc
import asyncio

CONFIG_FILE = "info_channels.json"

# Theme color: Original Red (#FF0000)
THEME_COLOR = discord.Color.from_rgb(255, 0, 0)

# Discord ANSI codes (Original Red and Original White only)
C_RED = "\u001b[1;31m"  # Original Bright Red (Labels)
C_WHT = "\u001b[1;37m"  # Original Bright White (Values)
C_RST = "\u001b[0m"     # Reset


def to_italic(text: str) -> str:
    """Convert ASCII characters to Mathematical Italic Unicode font (e.g. Lorem ipsum -> 𝐿𝑜𝑟𝑒𝑚 𝑖𝑝𝑠𝑢𝑚)."""
    result = []
    for ch in str(text):
        o = ord(ch)
        if 65 <= o <= 90:  # A-Z
            result.append(chr(0x1d434 + (o - 65)))
        elif 97 <= o <= 122:  # a-z
            if ch == 'h':
                result.append(chr(0x210e))  # Planck constant ℎ
            else:
                result.append(chr(0x1d44e + (o - 97)))
        else:
            result.append(ch)
    return ''.join(result)


class InfoCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = None
        self.config_data = self.load_config()
        self.cooldowns = {}

    async def cog_load(self):
        timeout = aiohttp.ClientTimeout(total=15)
        self.session = aiohttp.ClientSession(timeout=timeout)

    async def cog_unload(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def clean_signature(self, sig: str) -> str:
        if not sig:
            return "None"
        cleaned = re.sub(r"\[[0-9a-fA-F]{6}\]|\[/?(?:b|i|u|s|c)\]", "", str(sig)).strip()
        return cleaned if cleaned else "None"

    def convert_unix_timestamp(self, timestamp) -> str:
        try:
            if not timestamp or str(timestamp) in ("Not found", "None", "0", 0):
                return "Not found"
            ts = int(timestamp)
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %B %Y, %I:%M:%S %p UTC")
        except (ValueError, TypeError, OSError):
            return "Not found"

    def calculate_account_age(self, timestamp) -> tuple[int, str]:
        try:
            if not timestamp or str(timestamp) in ("Not found", "None", "0", 0):
                return 0, "Not found"
            ts = int(timestamp)
            created_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            now = datetime.now(timezone.utc)
            delta = now - created_dt
            if delta.total_seconds() < 0:
                return 1, "Recent"

            total_days = delta.days
            years = total_days // 365
            rem_days = total_days % 365
            months = rem_days // 30
            days = rem_days % 30

            breakdown = []
            if years > 0:
                breakdown.append(f"{years} year{'s' if years > 1 else ''}")
            if months > 0:
                breakdown.append(f"{months} month{'s' if months > 1 else ''}")
            if days > 0 or not breakdown:
                breakdown.append(f"{days} day{'s' if days > 1 else ''}")

            return total_days, f"{total_days:,} days ({', '.join(breakdown)})"
        except (ValueError, TypeError, OSError):
            return 0, "Not found"

    def calculate_playtime(self, uid: str, stats_data: dict, total_days: int, last_login_ts) -> tuple[str, str, str, str, str]:
        now_dt = datetime.now(timezone.utc)
        today_str = now_dt.strftime("%Y-%m-%d")
        uid_key = str(uid)

        total_survival = 0
        total_matches = 0
        if stats_data and isinstance(stats_data, dict):
            for mode in ["solo_stats", "duo_stats", "quad_stats"]:
                mode_dict = stats_data.get(mode, {})
                if isinstance(mode_dict, dict):
                    det = mode_dict.get("detailed_stats", {})
                    if isinstance(det, dict):
                        total_survival += det.get("survival_time", 0)
                        deaths = det.get("deaths", 0)
                        top_n = det.get("top_n_times", 0)
                        total_matches += (deaths + top_n)

        # 1. Total Playtime & Daily Average
        if total_survival > 0:
            total_hours = int(total_survival // 3600)
            total_str = f"{total_hours:,} hours"
            total_days_valid = max(total_days, 1)
            avg_daily_sec = total_survival / total_days_valid
            avg_hrs = int(avg_daily_sec // 3600)
            avg_mins = int((avg_daily_sec % 3600) // 60)
            if avg_hrs > 0:
                daily_avg_str = f"~{avg_hrs} hr {avg_mins} min / day"
            else:
                daily_avg_str = f"~{max(avg_mins, 1)} min / day"
        else:
            total_str = "0 hours"
            daily_avg_str = "0 min / day"

        total_matches_str = f"{total_matches:,} matches" if total_matches > 0 else "0 matches"

        # 2. Check login status & last active
        last_active_str = "Unknown"
        logged_in_today = False
        if last_login_ts and str(last_login_ts) not in ("Not found", "None", "0", 0):
            try:
                login_dt = datetime.fromtimestamp(int(last_login_ts), tz=timezone.utc)
                diff_sec = (now_dt - login_dt).total_seconds()
                if diff_sec >= 0:
                    diff_hrs = int(diff_sec // 3600)
                    diff_m = int((diff_sec % 3600) // 60)
                    if diff_hrs > 0:
                        last_active_str = f"{diff_hrs}h {diff_m}m ago"
                    else:
                        last_active_str = f"{max(diff_m, 1)}m ago"

                    if diff_sec < 86400 and login_dt.date() == now_dt.date():
                        logged_in_today = True
            except Exception:
                pass

        # 3. Daily Tracker File for real-time match time today
        today_sec = 0
        tracker_file = "daily_playtime.json"
        tracker_data = {}
        if os.path.exists(tracker_file):
            try:
                with open(tracker_file, "r", encoding="utf-8") as f:
                    tracker_data = json.load(f)
            except Exception:
                tracker_data = {}

        entry = tracker_data.get(uid_key)
        if not entry or entry.get("date") != today_str:
            tracker_data[uid_key] = {
                "date": today_str,
                "base_survival": total_survival,
                "last_survival": total_survival,
                "today_seconds": 0
            }
            try:
                with open(tracker_file, "w", encoding="utf-8") as f:
                    json.dump(tracker_data, f, indent=2)
            except Exception:
                pass
        else:
            base_survival = entry.get("base_survival", total_survival)
            if total_survival > base_survival:
                today_sec = total_survival - base_survival
                entry["today_seconds"] = today_sec
                entry["last_survival"] = total_survival
                try:
                    with open(tracker_file, "w", encoding="utf-8") as f:
                        json.dump(tracker_data, f, indent=2)
                except Exception:
                    pass
            else:
                today_sec = entry.get("today_seconds", 0)

        # 4. Formulate verified Today Playtime display (strictly no guessing)
        if today_sec > 0:
            t_hrs = today_sec // 3600
            t_mins = (today_sec % 3600) // 60
            if t_hrs > 0:
                today_play_str = f"{t_hrs} hr {t_mins} min"
            else:
                today_play_str = f"{max(t_mins, 1)} min"
        else:
            if logged_in_today:
                today_play_str = "0 min (No match recorded today)"
            else:
                today_play_str = "0 min (Not played today)"

        return today_play_str, daily_avg_str, total_str, total_matches_str, last_active_str

    def get_val(self, d, *keys, default="Not found"):
        if not isinstance(d, dict):
            return default
        for k in keys:
            if k in d and d[k] not in (None, ""):
                return d[k]
        return default

    def load_config(self):
        default_config = {
            "servers": {},
            "global_settings": {
                "default_all_channels": False,
                "default_cooldown": 5,
                "default_daily_limit": 30
            }
        }

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded_config = json.load(f)
                    loaded_config.setdefault("global_settings", {})
                    loaded_config["global_settings"].setdefault("default_all_channels", False)
                    loaded_config["global_settings"].setdefault("default_cooldown", 5)
                    loaded_config["global_settings"].setdefault("default_daily_limit", 30)
                    loaded_config.setdefault("servers", {})
                    return loaded_config
            except (json.JSONDecodeError, IOError) as e:
                print(f"Error loading config: {e}")
                return default_config
        return default_config

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, indent=4, ensure_ascii=False)
        except IOError as e:
            print(f"Error saving config: {e}")

    async def is_channel_allowed(self, ctx):
        try:
            if not ctx.guild:
                return True
            guild_id = str(ctx.guild.id)
            allowed_channels = self.config_data["servers"].get(guild_id, {}).get("info_channels", [])
            if not allowed_channels:
                return True
            return str(ctx.channel.id) in allowed_channels
        except Exception as e:
            print(f"Error checking channel permission: {e}")
            return False

    async def fetch_player_data(self, uid: str):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

        headers = {"User-Agent": "Mozilla/5.0"}

        # 1. Primary API (wzapi)
        try:
            url = f"https://wzapi.vercel.app/get?uid={uid}&region=SG"
            async with self.session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict) and ("basic_info" in data or "basicInfo" in data):
                        return data, None
                elif resp.status == 404:
                    return None, "not_found"
        except Exception as e:
            print(f"Primary API error: {e}")

        # 2. Secondary API (thug4ff fallback)
        try:
            url = f"http://raw.thug4ff.xyz/info?uid={uid}&key=great"
            async with self.session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict) and ("basicInfo" in data or "basic_info" in data):
                        return data, None
                elif resp.status == 404:
                    return None, "not_found"
        except Exception as e:
            print(f"Fallback API error: {e}")

        return None, "api_error"

    async def fetch_player_stats(self, uid: str):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

        headers = {"User-Agent": "Mozilla/5.0"}

        try:
            url = f"https://wzapi.vercel.app/stats?uid={uid}"
            async with self.session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            print(f"Stats API error: {e}")

        return None

    async def fetch_ban_status(self, uid: str):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

        headers = {"User-Agent": "Mozilla/5.0"}

        try:
            url = f"https://wzapi.vercel.app/check_ban?uid={uid}"
            async with self.session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ban_data = data.get("data", {})
                    is_banned = ban_data.get("is_banned", 0)
                    period = ban_data.get("period", 0)
                    return is_banned == 1, period
        except Exception as e:
            print(f"Primary ban check error: {e}")

        return None, None

    @commands.hybrid_command(name="setinfochannel", description="Allow a channel for info commands")
    @commands.has_permissions(administrator=True)
    async def set_info_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        if not ctx.guild:
            return await ctx.send("This command can only be used in a server.", ephemeral=True)
        guild_id = str(ctx.guild.id)
        self.config_data["servers"].setdefault(guild_id, {"info_channels": [], "config": {}})
        if str(channel.id) not in self.config_data["servers"][guild_id]["info_channels"]:
            self.config_data["servers"][guild_id]["info_channels"].append(str(channel.id))
            self.save_config()
            await ctx.send(f"{channel.mention} is now allowed for Free Fire commands.")
        else:
            await ctx.send(f"{channel.mention} is already in the allowed list.")

    @commands.hybrid_command(name="removeinfochannel", description="Remove a channel from allowed info commands")
    @commands.has_permissions(administrator=True)
    async def remove_info_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        if not ctx.guild:
            return await ctx.send("This command can only be used in a server.", ephemeral=True)
        guild_id = str(ctx.guild.id)
        if guild_id in self.config_data["servers"]:
            if str(channel.id) in self.config_data["servers"][guild_id]["info_channels"]:
                self.config_data["servers"][guild_id]["info_channels"].remove(str(channel.id))
                self.save_config()
                await ctx.send(f"{channel.mention} has been removed from allowed channels.")
            else:
                await ctx.send(f"{channel.mention} is not in the list of allowed channels.")
        else:
            await ctx.send("This server has no custom channel configuration.")

    @commands.hybrid_command(name="infochannels", description="List allowed channels")
    async def list_info_channels(self, ctx: commands.Context):
        if not ctx.guild:
            return await ctx.send("Direct messages allow all commands.")
        guild_id = str(ctx.guild.id)

        if guild_id in self.config_data["servers"] and self.config_data["servers"][guild_id]["info_channels"]:
            channels = []
            for channel_id in self.config_data["servers"][guild_id]["info_channels"]:
                channel = ctx.guild.get_channel(int(channel_id))
                channels.append(f"- {channel.mention if channel else f'ID: {channel_id}'}")

            cooldown = self.config_data["servers"][guild_id]["config"].get("cooldown", self.config_data["global_settings"]["default_cooldown"])
            embed = discord.Embed(
                title=to_italic("Allowed Channels for Free Fire Bot"),
                description="\n".join(channels),
                color=THEME_COLOR
            )
            embed.set_footer(text=f"{to_italic('Current cooldown')}: {cooldown}s")
        else:
            embed = discord.Embed(
                title=to_italic("Allowed Channels for Free Fire Bot"),
                description="All channels are currently allowed (no restrictions set).",
                color=THEME_COLOR
            )

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="checkban", description="Check if a Free Fire player UID is banned")
    @app_commands.describe(uid="Free Fire Player UID")
    async def check_ban(self, ctx: commands.Context, uid: str):
        await ctx.defer()
        uid = uid.strip()
        if not uid.isdigit() or len(uid) < 6:
            return await ctx.send("Invalid UID! Must be numbers with at least 6 digits.")

        if not await self.is_channel_allowed(ctx):
            return await ctx.send("This command is not allowed in this channel.", ephemeral=True)

        is_banned, period = await self.fetch_ban_status(uid)

        if is_banned is None:
            return await ctx.send("Ban check service is temporarily unavailable. Try again later.")

        embed = discord.Embed(
            title=to_italic("Free Fire Ban Status"),
            color=THEME_COLOR,
            timestamp=datetime.now(timezone.utc)
        )
        status_col = C_RED if is_banned else C_WHT
        status_text = "BANNED" if is_banned else "CLEAN / ACTIVE"
        val = (
            "```ansi\n"
            f"{C_RED}{to_italic('UID')}    {C_RST}: {C_WHT}{uid}{C_RST}\n"
            f"{C_RED}{to_italic('Status')} {C_RST}: {status_col}{status_text}{C_RST}"
        )
        if is_banned and period:
            val += f"\n{C_RED}{to_italic('Period')} {C_RST}: {C_WHT}{period} days{C_RST}"
        val += "\n```"
        embed.add_field(name=to_italic("Account Status"), value=val, inline=False)
        embed.set_footer(text=to_italic("Free Fire Info Bot"))
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="info", description="Displays detailed information about a Free Fire player")
    @app_commands.describe(uid="Free Fire Player UID")
    async def player_info(self, ctx: commands.Context, uid: str):
        await ctx.defer()
        uid = uid.strip()
        if not uid.isdigit() or len(uid) < 6:
            return await ctx.send("Invalid UID! It must be only numbers and at least 6 digits.")

        if not await self.is_channel_allowed(ctx):
            return await ctx.send("This command is not allowed in this channel.", ephemeral=True)

        guild_id = str(ctx.guild.id) if ctx.guild else "dm"
        cooldown = self.config_data["global_settings"]["default_cooldown"]
        if ctx.guild and guild_id in self.config_data["servers"]:
            cooldown = self.config_data["servers"][guild_id]["config"].get("cooldown", cooldown)

        now = datetime.now(timezone.utc)
        if ctx.author.id in self.cooldowns:
            last_used = self.cooldowns[ctx.author.id]
            elapsed = (now - last_used).total_seconds()
            if elapsed < cooldown:
                remaining = int(cooldown - elapsed)
                return await ctx.send(f"Please wait {remaining}s before using this command again.", ephemeral=True)

        self.cooldowns[ctx.author.id] = now

        try:
            (data_res, stats_data, ban_res) = await asyncio.gather(
                self.fetch_player_data(uid),
                self.fetch_player_stats(uid),
                self.fetch_ban_status(uid),
                return_exceptions=True
            )

            data, err = (None, "api_error")
            if isinstance(data_res, tuple):
                data, err = data_res

            is_banned, period = (None, None)
            if isinstance(ban_res, tuple):
                is_banned, period = ban_res

            if not isinstance(stats_data, dict):
                stats_data = None

            if err == "not_found" or not data:
                embed = discord.Embed(
                    title=to_italic("Player Not Found"),
                    description=f"No Free Fire account found for UID {uid}.\nPlease double check the number and try again.",
                    color=THEME_COLOR
                )
                return await ctx.send(embed=embed)

            if err == "api_error":
                embed = discord.Embed(
                    title=to_italic("Service Unavailable"),
                    description="The Free Fire info service is currently unavailable. Please try again shortly.",
                    color=THEME_COLOR
                )
                return await ctx.send(embed=embed)

            basic_info = data.get("basic_info") or data.get("basicInfo") or {}
            captain_info = data.get("captain_basic_info") or data.get("captainBasicInfo") or {}
            clan_info = data.get("clan_basic_info") or data.get("clanBasicInfo") or {}
            credit_score_info = data.get("credit_score_info") or data.get("creditScoreInfo") or {}
            pet_info = data.get("pet_info") or data.get("petInfo") or {}
            social_info = data.get("social_info") or data.get("socialInfo") or {}

            nickname = self.get_val(basic_info, "nickname", default="Unknown")
            level = self.get_val(basic_info, "level")
            exp = self.get_val(basic_info, "exp")
            region = self.get_val(basic_info, "region")
            likes = self.get_val(basic_info, "liked")
            honor_score = self.get_val(credit_score_info, "credit_score", "creditScore")
            raw_sig = self.get_val(social_info, "signature", default="None")
            signature = self.clean_signature(raw_sig)

            if is_banned is True:
                ban_badge = f"Banned ({period} days)" if period else "Banned"
            elif is_banned is False:
                ban_badge = "Clean / Active"
            else:
                ban_badge = "Unknown"

            badge_cnt = self.get_val(basic_info, "badge_cnt", "badgeCnt")
            br_points = self.get_val(basic_info, "ranking_points", "rankingPoints")
            cs_points = self.get_val(basic_info, "cs_ranking_points", "csRankingPoints")
            raw_create_at = self.get_val(basic_info, "create_at", "createAt", default=None)
            create_time = self.convert_unix_timestamp(raw_create_at)
            total_days, account_age = self.calculate_account_age(raw_create_at)
            raw_last_login = self.get_val(basic_info, "last_login_at", "lastLoginAt", default=None)
            last_login = self.convert_unix_timestamp(raw_last_login)

            today_playtime, daily_avg, total_playtime, total_matches, last_active = self.calculate_playtime(uid, stats_data, total_days, raw_last_login)

            embed = discord.Embed(
                title=f"{to_italic('Free Fire Player Info')}: {nickname}",
                color=THEME_COLOR,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_thumbnail(url=ctx.author.display_avatar.url)

            # Section 1: Account Profile
            embed.add_field(
                name=to_italic("Account Profile"),
                value=(
                    "```ansi\n"
                    f"{C_RED}{to_italic('Nickname')}    {C_RST}: {C_WHT}{nickname}{C_RST}\n"
                    f"{C_RED}{to_italic('UID')}         {C_RST}: {C_WHT}{uid}{C_RST}\n"
                    f"{C_RED}{to_italic('Level')}       {C_RST}: {C_WHT}{level} (EXP: {exp}){C_RST}\n"
                    f"{C_RED}{to_italic('Region')}      {C_RST}: {C_WHT}{region}{C_RST}\n"
                    f"{C_RED}{to_italic('Likes')}       {C_RST}: {C_WHT}{likes}{C_RST}\n"
                    f"{C_RED}{to_italic('Honor Score')} {C_RST}: {C_WHT}{honor_score}{C_RST}\n"
                    f"{C_RED}{to_italic('Ban Status')}  {C_RST}: {C_WHT}{ban_badge}{C_RST}\n"
                    "```"
                ),
                inline=False
            )

            # Section 2: Account Timeline
            embed.add_field(
                name=to_italic("Account Timeline"),
                value=(
                    "```ansi\n"
                    f"{C_RED}{to_italic('Created At')}  {C_RST}: {C_WHT}{create_time}{C_RST}\n"
                    f"{C_RED}{to_italic('Account Age')} {C_RST}: {C_WHT}{account_age}{C_RST}\n"
                    f"{C_RED}{to_italic('Last Login')}  {C_RST}: {C_WHT}{last_login}{C_RST}\n"
                    f"{C_RED}{to_italic('Last Active')} {C_RST}: {C_WHT}{last_active}{C_RST}\n"
                    "```"
                ),
                inline=False
            )

            # Section 3: Playtime Statistics
            embed.add_field(
                name=to_italic("Playtime Statistics"),
                value=(
                    "```ansi\n"
                    f"{C_RED}{to_italic('Today Playtime')}     {C_RST}: {C_WHT}{today_playtime}{C_RST}\n"
                    f"{C_RED}{to_italic('Lifetime Daily Avg')} {C_RST}: {C_WHT}{daily_avg}{C_RST}\n"
                    f"{C_RED}{to_italic('Total In-Game Time')} {C_RST}: {C_WHT}{total_playtime}{C_RST}\n"
                    f"{C_RED}{to_italic('Total Matches')}      {C_RST}: {C_WHT}{total_matches}{C_RST}\n"
                    "```"
                ),
                inline=False
            )

            # Section 4: Ranks and Badges
            embed.add_field(
                name=to_italic("Ranks and Badges"),
                value=(
                    "```ansi\n"
                    f"{C_RED}{to_italic('BR Points')}   {C_RST}: {C_WHT}{br_points}{C_RST}\n"
                    f"{C_RED}{to_italic('CS Points')}   {C_RST}: {C_WHT}{cs_points}{C_RST}\n"
                    f"{C_RED}{to_italic('BP Badges')}   {C_RST}: {C_WHT}{badge_cnt}{C_RST}\n"
                    "```"
                ),
                inline=False
            )

            # Section 5: Guild Information
            clan_name = self.get_val(clan_info, "clan_name", "clanName", default=None)
            if clan_name:
                clan_id = self.get_val(clan_info, "clan_id", "clanId")
                clan_lvl = self.get_val(clan_info, "clan_level", "clanLevel")
                members = self.get_val(clan_info, "member_num", "memberNum")
                capacity = self.get_val(clan_info, "capacity", default="?")
                leader_nick = self.get_val(captain_info, "nickname", default="Unknown")
                leader_uid = self.get_val(captain_info, "account_id", "accountId", default="")

                guild_str = (
                    "```ansi\n"
                    f"{C_RED}{to_italic('Guild Name')}   {C_RST}: {C_WHT}{clan_name} (Level {clan_lvl}){C_RST}\n"
                    f"{C_RED}{to_italic('Guild ID')}     {C_RST}: {C_WHT}{clan_id}{C_RST}\n"
                    f"{C_RED}{to_italic('Members')}      {C_RST}: {C_WHT}{members}/{capacity}{C_RST}\n"
                    f"{C_RED}{to_italic('Guild Leader')} {C_RST}: {C_WHT}{leader_nick} (UID: {leader_uid}){C_RST}\n"
                    "```"
                )
                embed.add_field(name=to_italic("Guild Information"), value=guild_str, inline=False)

            # Section 6: Equipped Pet
            pet_name = self.get_val(pet_info, "name", default=None)
            if pet_name:
                pet_lvl = self.get_val(pet_info, "level")
                pet_exp = self.get_val(pet_info, "exp")
                pet_str = (
                    "```ansi\n"
                    f"{C_RED}{to_italic('Pet Name')}  {C_RST}: {C_WHT}{pet_name}{C_RST}\n"
                    f"{C_RED}{to_italic('Pet Level')} {C_RST}: {C_WHT}{pet_lvl} (EXP: {pet_exp}){C_RST}\n"
                    "```"
                )
                embed.add_field(name=to_italic("Equipped Pet"), value=pet_str, inline=False)

            # Section 7: Signature
            if signature and signature != "None":
                embed.add_field(
                    name=to_italic("Account Signature"),
                    value=f"```ansi\n{C_WHT}{signature}{C_RST}\n```",
                    inline=False
                )

            embed.set_footer(text=f"{to_italic('Requested by')} {ctx.author.display_name} | {to_italic('Free Fire Bot')}", icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=embed)

        except Exception as e:
            print(f"Unexpected error in player_info: {e}")
            await ctx.send(f"An error occurred while retrieving player details: {e}")
        finally:
            gc.collect()


async def setup(bot):
    await bot.add_cog(InfoCommands(bot))
