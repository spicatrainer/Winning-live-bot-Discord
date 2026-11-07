import os
import sqlite3
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


# ========================================
# CONFIG
# ========================================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

DB_PATH = "winning_live.db"

if not TOKEN or not GUILD_ID:
    raise RuntimeError(
        "Missing DISCORD_TOKEN or GUILD_ID in .env"
    )

GUILD = discord.Object(id=int(GUILD_ID))

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ========================================
# DATABASE
# ========================================

def connect_db():
    return sqlite3.connect(DB_PATH)


def init_db():
    with connect_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS user_dances (
                user_id TEXT NOT NULL,
                slot INTEGER NOT NULL
                    CHECK(slot BETWEEN 1 AND 3),
                name TEXT NOT NULL,
                gif_url TEXT NOT NULL,

                PRIMARY KEY(user_id, slot)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS preset_dances (
                slot INTEGER PRIMARY KEY
                    CHECK(slot BETWEEN 1 AND 10),
                name TEXT NOT NULL,
                gif_url TEXT NOT NULL
            )
        """)


def save_dance(user_id, slot, name, url):
    with connect_db() as db:
        db.execute("""
            INSERT INTO user_dances
                (user_id, slot, name, gif_url)
            VALUES (?, ?, ?, ?)

            ON CONFLICT(user_id, slot)
            DO UPDATE SET
                name = excluded.name,
                gif_url = excluded.gif_url
        """, (str(user_id), slot, name, url))


def save_preset(slot, name, url):
    with connect_db() as db:
        db.execute("""
            INSERT INTO preset_dances
                (slot, name, gif_url)
            VALUES (?, ?, ?)

            ON CONFLICT(slot)
            DO UPDATE SET
                name = excluded.name,
                gif_url = excluded.gif_url
        """, (slot, name, url))


def get_dance(user_id, slot):
    with connect_db() as db:
        return db.execute("""
            SELECT slot, name, gif_url
            FROM user_dances
            WHERE user_id = ? AND slot = ?
        """, (str(user_id), slot)).fetchone()


def get_preset(slot):
    with connect_db() as db:
        return db.execute("""
            SELECT slot, name, gif_url
            FROM preset_dances
            WHERE slot = ?
        """, (slot,)).fetchone()


def get_all_dances(user_id):
    with connect_db() as db:
        return db.execute("""
            SELECT slot, name, gif_url
            FROM user_dances
            WHERE user_id = ?
            ORDER BY slot
        """, (str(user_id),)).fetchall()


def get_all_presets():
    with connect_db() as db:
        return db.execute("""
            SELECT slot, name, gif_url
            FROM preset_dances
            ORDER BY slot
        """).fetchall()


def valid_url(url):
    parsed = urlparse(url)

    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and not parsed.username
        and not parsed.password
        and len(url) <= 2000
    )


# ========================================
# PREVIEW UI
# ========================================

def make_preview_embed(record, is_preset):
    slot, name, url = record

    embed = discord.Embed(
        title=f"🎤 {name}",
        description=(
            f"{'Preset' if is_preset else 'Personal Dance'}"
            f" — Slot {slot}\n\n"
            "ตรวจสอบภาพก่อนขึ้นแสดง"
        ),
        color=discord.Color.purple()
    )

    embed.set_image(url=url)

    embed.set_footer(
        text="Perform = ส่งภาพ | Change = เปลี่ยนท่า"
    )

    return embed


class DanceSelect(discord.ui.Select):

    def __init__(self, parent, records):
        self.parent_view = parent

        options = [
            discord.SelectOption(
                label=f"{slot} — {name}"[:100],
                value=str(slot)
            )
            for slot, name, url in records
        ]

        super().__init__(
            placeholder="เลือกท่าเต้นที่ต้องการ",
            options=options,
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction):
        slot = int(self.values[0])

        if self.parent_view.is_preset:
            record = get_preset(slot)
        else:
            record = get_dance(
                self.parent_view.owner_id,
                slot
            )

        if record is None:
            await interaction.response.send_message(
                "ไม่พบ GIF นี้แล้ว",
                ephemeral=True
            )
            return

        self.parent_view.record = record
        self.parent_view.show_select = False
        self.parent_view.build_items()

        await interaction.response.edit_message(
            embed=make_preview_embed(
                record,
                self.parent_view.is_preset
            ),
            view=self.parent_view
        )


class PreviewView(discord.ui.View):

    def __init__(self, owner_id, record, is_preset):
        super().__init__(timeout=180)

        self.owner_id = owner_id
        self.record = record
        self.is_preset = is_preset
        self.show_select = False

        self.build_items()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Preview นี้เป็นของผู้เรียกคำสั่งเท่านั้น",
                ephemeral=True
            )
            return False

        return True

    def build_items(self):
        self.clear_items()

        perform = discord.ui.Button(
            label="Perform",
            emoji="🎤",
            style=discord.ButtonStyle.success,
            row=0
        )

        change = discord.ui.Button(
            label="Change",
            emoji="🔄",
            style=discord.ButtonStyle.primary,
            row=0
        )

        cancel = discord.ui.Button(
            label="Cancel",
            emoji="✖️",
            style=discord.ButtonStyle.secondary,
            row=0
        )

        perform.callback = self.perform_callback
        change.callback = self.change_callback
        cancel.callback = self.cancel_callback

        self.add_item(perform)
        self.add_item(change)
        self.add_item(cancel)

        if self.show_select:
            if self.is_preset:
                records = get_all_presets()
            else:
                records = get_all_dances(self.owner_id)

            if records:
                self.add_item(
                    DanceSelect(self, records)
                )

    async def perform_callback(self, interaction):
        slot, name, url = self.record

        # Acknowledge the button interaction first.
        await interaction.response.defer(ephemeral=True)

        try:
            # Recheck that the saved record still exists.
            if self.is_preset:
                latest = get_preset(slot)
            else:
                latest = get_dance(self.owner_id, slot)

            if latest is None:
                await interaction.followup.send(
                    "GIF นี้ถูกลบไปแล้ว",
                    ephemeral=True
                )
                return

            # Send GIF publicly in the RP channel.
            embed = discord.Embed(
                title=f"🎶 {latest[1]}",
                color=discord.Color.purple()
            )

            embed.set_image(url=latest[2])

            embed.set_footer(
                text=f"Performed by {interaction.user.display_name}"
            )

            await interaction.channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none()
            )

            for item in self.children:
                item.disabled = True

            await interaction.edit_original_response(
                content="✅ ส่งท่าเต้นลงห้อง RP แล้ว!",
                embed=None,
                view=self
            )

            self.stop()

        except discord.Forbidden:
            await interaction.followup.send(
                "บอทไม่มีสิทธิ์ส่งข้อความหรือ Embed ในห้องนี้",
                ephemeral=True
            )

        except discord.HTTPException:
            await interaction.followup.send(
                "Discord ไม่สามารถส่ง GIF ได้ กรุณาลองใหม่",
                ephemeral=True
            )

    async def change_callback(self, interaction):
        self.show_select = not self.show_select
        self.build_items()

        await interaction.response.edit_message(
            view=self
        )

    async def cancel_callback(self, interaction):
        self.stop()

        await interaction.response.edit_message(
            content="ยกเลิกการแสดงแล้ว",
            embed=None,
            view=None
        )


async def open_preview(interaction, record, is_preset):
    view = PreviewView(
        owner_id=interaction.user.id,
        record=record,
        is_preset=is_preset
    )

    await interaction.response.send_message(
        embed=make_preview_embed(record, is_preset),
        view=view,
        ephemeral=True
    )


# ========================================
# SLASH COMMAND GROUP
# ========================================

live = app_commands.Group(
    name="live",
    description="Winning Live performance commands"
)


# ----------------------------------------
# /live dance
# ----------------------------------------

@live.command(
    name="dance",
    description="แสดงท่าเต้นส่วนตัว"
)
@app_commands.describe(
    slot="หมายเลขท่าเต้นส่วนตัว 1-3"
)
async def dance(
    interaction: discord.Interaction,
    slot: app_commands.Range[int, 1, 3]
):
    record = get_dance(interaction.user.id, slot)

    if record is None:
        await interaction.response.send_message(
            "ยังไม่มี GIF ในช่องนี้\n"
            "ใช้ `/live set` เพื่อบันทึก "
            "หรือ `/live example` เพื่อใช้ท่ากลาง",
            ephemeral=True
        )
        return

    await open_preview(
        interaction,
        record,
        is_preset=False
    )


# ----------------------------------------
# /live example
# ----------------------------------------

@live.command(
    name="example",
    description="แสดงท่าเต้นส่วนกลาง"
)
@app_commands.describe(
    slot="หมายเลขท่าส่วนกลาง 1-10"
)
async def example(
    interaction: discord.Interaction,
    slot: app_commands.Range[int, 1, 10]
):
    record = get_preset(slot)

    if record is None:
        await interaction.response.send_message(
            "ยังไม่มี GIF ส่วนกลางในช่องนี้",
            ephemeral=True
        )
        return

    await open_preview(
        interaction,
        record,
        is_preset=True
    )


# ----------------------------------------
# /live set
# ----------------------------------------

@live.command(
    name="set",
    description="บันทึกท่าเต้นส่วนตัว"
)
@app_commands.describe(
    slot="ช่องบันทึก 1-3",
    name="ชื่อท่าเต้น",
    url="ลิงก์ GIF หรือภาพเคลื่อนไหว"
)
async def set_dance(
    interaction: discord.Interaction,
    slot: app_commands.Range[int, 1, 3],
    name: app_commands.Range[str, 1, 80],
    url: str
):
    if not valid_url(url):
        await interaction.response.send_message(
            "กรุณาใช้ HTTPS URL ที่ถูกต้อง",
            ephemeral=True
        )
        return

    save_dance(
        interaction.user.id,
        slot,
        name,
        url
    )

    await interaction.response.send_message(
        f"✅ บันทึก **{name}** ในช่อง {slot} แล้ว\n"
        f"เรียกใช้ด้วย `/live dance {slot}`",
        ephemeral=True
    )


# ----------------------------------------
# /live preset_set
# ----------------------------------------

@live.command(
    name="preset_set",
    description="Admin: บันทึก GIF ส่วนกลาง"
)
@app_commands.default_permissions(
    manage_guild=True
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
@app_commands.describe(
    slot="ช่องส่วนกลาง 1-10",
    name="ชื่อท่าเต้น",
    url="ลิงก์ GIF"
)
async def preset_set(
    interaction: discord.Interaction,
    slot: app_commands.Range[int, 1, 10],
    name: app_commands.Range[str, 1, 80],
    url: str
):
    if not valid_url(url):
        await interaction.response.send_message(
            "กรุณาใช้ HTTPS URL ที่ถูกต้อง",
            ephemeral=True
        )
        return

    save_preset(slot, name, url)

    await interaction.response.send_message(
        f"✅ บันทึก Preset {slot}: {name}",
        ephemeral=True
    )


# ----------------------------------------
# /live list
# ----------------------------------------

@live.command(
    name="list",
    description="ดูรายการท่าเต้นที่บันทึกไว้"
)
async def dance_list(interaction: discord.Interaction):
    personal = get_all_dances(interaction.user.id)
    presets = get_all_presets()

    embed = discord.Embed(
        title="🎶 Winning Live Library",
        color=discord.Color.purple()
    )

    personal_text = "\n".join(
        f"`{slot}` — {discord.utils.escape_markdown(name)}"
        for slot, name, url in personal
    ) or "ยังไม่มีท่าเต้นส่วนตัว"

    preset_text = "\n".join(
        f"`{slot}` — {discord.utils.escape_markdown(name)}"
        for slot, name, url in presets
    ) or "ยังไม่มีท่าเต้นส่วนกลาง"

    embed.add_field(
        name="Personal Dances (3)",
        value=personal_text,
        inline=False
    )

    embed.add_field(
        name="Global Presets (10)",
        value=preset_text,
        inline=False
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ========================================
# ERROR HANDLING
# ========================================

@bot.tree.error
async def on_app_command_error(interaction, error):
    if isinstance(
        error,
        app_commands.errors.MissingPermissions
    ):
        message = "คำสั่งนี้สำหรับ Admin เท่านั้น"
    else:
        print(f"Command error: {error!r}")
        message = "เกิดข้อผิดพลาดในการทำงานของบอท"

    if interaction.response.is_done():
        await interaction.followup.send(
            message,
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            message,
            ephemeral=True
        )


# ========================================
# STARTUP
# ========================================

class WinningLiveBot(commands.Bot):

    async def setup_hook(self):
        init_db()

        self.tree.add_command(
            live,
            guild=GUILD
        )

        synced = await self.tree.sync(guild=GUILD)

        print(f"Synced {len(synced)} command groups")


# Replace the initial bot instance with the configured subclass.
bot = WinningLiveBot(
    command_prefix="!",
    intents=intents
)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print("Winning Live Bot is ready!")


bot.run(TOKEN)