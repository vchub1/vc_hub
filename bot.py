"""
FULL MERGED BOT – VC Card Store + Ticket System (v2.3)
------------------------------------------------
- VC Store: PayPal checkout, auto‑dispense, live timer, expiry alerts.
- Ticket System: Orders (form with address, VCC) & General Support (form with help type).
- Auto‑creates missing categories.
- Orderer Online/Offline toggle.

"""

import discord
from discord import ui
from discord.ext import commands, tasks
import asyncio
import json
import os
import secrets
import requests
from datetime import datetime, timedelta, UTC
from flask import Flask, request
import threading
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# --------------------------
# Configuration
# --------------------------
TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", 0))
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID", 0))
SELLER_ROLE_ID = int(os.getenv("SELLER_ROLE_ID", 0))
SUPPORT_ROLE = int(os.getenv("SUPPORT_ROLE", 0))
STORE_CHANNEL_ID = int(os.getenv("STORE_CHANNEL_ID", 0))
PAYPAL_EMAIL = os.getenv("PAYPAL_EMAIL", "")
PORT = int(os.getenv("PORT", 5000))

TICKET_CHANNEL_ID = 1518426882595356773
VCPANEL_CHANNEL_ID = 1518420853757313155

ORDERER_FILE = "orderers.json"

if not TOKEN:
    print("❌ BOT_TOKEN not set.")
    exit(1)
if not all([GUILD_ID, ADMIN_CHANNEL_ID, SELLER_ROLE_ID, SUPPORT_ROLE, STORE_CHANNEL_ID, PAYPAL_EMAIL]):
    print("❌ Missing required environment variables.")
    exit(1)

# Files
VC_FILE = "vcs.json"
PENDING_FILE = "pending.json"
ACTIVE_FILE = "active.json"
TICKETS_FILE = "tickets.json"
CATEGORY_ORDERS_NAME = "Orders"
CATEGORY_GENERAL_NAME = "General"
INACTIVE_CLOSE_HOURS = 24

BASE_DIR = Path(__file__).parent.absolute()

# --------------------------
# Orderer Availability Helpers
# --------------------------
def load_orderers():
    if not os.path.exists(ORDERER_FILE):
        return {}
    with open(ORDERER_FILE, "r") as f:
        return json.load(f)

def save_orderers(data):
    with open(ORDERER_FILE, "w") as f:
        json.dump(data, f, indent=4)

def set_orderer_online(user_id: int, online: bool):
    data = load_orderers()
    data[str(user_id)] = online
    save_orderers(data)

def is_orderer_online(user_id: int) -> bool:
    data = load_orderers()
    return data.get(str(user_id), False)

# --------------------------
# VC File helpers
# --------------------------
def get_vc_file():
    return os.path.join(BASE_DIR, VC_FILE)

def get_pending_file():
    return os.path.join(BASE_DIR, PENDING_FILE)

def get_active_file():
    return os.path.join(BASE_DIR, ACTIVE_FILE)

def load_vc_pool():
    if not os.path.exists(get_vc_file()):
        save_vc_pool([])
        return []
    try:
        with open(get_vc_file(), "r") as f:
            data = json.load(f)
            cards = data.get("cards", [])
            cards = [c for c in cards if c.get("card") != "4111111111111111"]
            if len(cards) != data.get("cards", []):
                save_vc_pool(cards)
            return cards
    except:
        save_vc_pool([])
        return []

def save_vc_pool(cards):
    with open(get_vc_file(), "w") as f:
        json.dump({"cards": cards}, f, indent=4)

def load_pending():
    if not os.path.exists(get_pending_file()):
        return {}
    with open(get_pending_file(), "r") as f:
        return json.load(f)

def save_pending(data):
    with open(get_pending_file(), "w") as f:
        json.dump(data, f, indent=4)

def load_active():
    if not os.path.exists(get_active_file()):
        return {}
    with open(get_active_file(), "r") as f:
        return json.load(f)

def save_active(data):
    with open(get_active_file(), "w") as f:
        json.dump(data, f, indent=4)

# --------------------------
# Ticket file helpers
# --------------------------
def load_tickets():
    if not os.path.exists(TICKETS_FILE):
        return {"orders": {"open": [], "closed": []}, "general": {"open": [], "closed": []}}
    with open(TICKETS_FILE, "r") as f:
        return json.load(f)

def save_tickets(data):
    with open(TICKETS_FILE, "w") as f:
        json.dump(data, f, indent=4)

# --------------------------
# Discord Bot
# --------------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --------------------------
# VC System: Progress Embed
# --------------------------
def progress_embed(percent: int, title: str, description: str, color=discord.Color.blue()):
    filled = int(percent / 10)
    bar = "🟩" * filled + "⬜" * (10 - filled)
    embed = discord.Embed(title=title, description=f"{bar} **{percent}%**\n\n{description}", color=color)
    embed.set_footer(text="Your card will be delivered automatically.")
    return embed

async def edit_followup_embed(token: str, msg_id: int, embed: discord.Embed):
    url = f"https://discord.com/api/v10/webhooks/{bot.user.id}/{token}/messages/{msg_id}"
    payload = {"embeds": [embed.to_dict()]}
    try:
        resp = requests.patch(url, json=payload)
        if resp.status_code == 200:
            print("✅ Updated ephemeral followup")
        else:
            print(f"❌ Failed to update followup: {resp.status_code}")
    except Exception as e:
        print(f"❌ Error updating followup: {e}")

# --------------------------
# VC Dispense
# --------------------------
async def dispense_vc(user_id: int, token: str = None, msg_id: int = None):
    print(f"🔍 dispense_vc called")
    try:
        cards = load_vc_pool()
        if not cards:
            admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
            if admin_channel:
                await admin_channel.send("🚨 **No VCs left!**")
            return False

        vc_data = cards.pop(0)
        save_vc_pool(cards)
        expiry_time = datetime.now(UTC) + timedelta(hours=2)
        expiry_str = expiry_time.isoformat()

        user = await bot.fetch_user(user_id)
        if user:
            embed_card = discord.Embed(
                title="✨ Your Virtual Card",
                description=f"**Card:** `{vc_data['card']}`\n**Expiry:** `{vc_data['expiry']}`\n**CVV:** `{vc_data['cvv']}`",
                color=discord.Color.green()
            )
            embed_card.add_field(name="⏰ Time Remaining", value="2 hours (updates live)", inline=False)
            embed_card.set_footer(text="Terminated after 2 hours.")
            try:
                dm = await user.create_dm()
                await dm.send(embed=embed_card)
                await dm.send(embed=discord.Embed(title="✅ Thank You!", description="Your card is above.\nUse it within 2 hours.", color=discord.Color.gold()))
                print(f"✅ DM sent to user {user_id}")
            except:
                print(f"❌ Cannot DM user {user_id}")

        active = load_active()
        active[vc_data['card']] = {
            "user_id": user_id,
            "expires_at": expiry_str,
            "card_data": vc_data
        }
        save_active(active)

        if token and msg_id:
            embed = progress_embed(100, "✅ Card Delivered!", "Your Virtual Card has been sent to your DMs.", color=discord.Color.green())
            await edit_followup_embed(token, msg_id, embed)

        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            seller_role = admin_channel.guild.get_role(SELLER_ROLE_ID)
            role_mention = seller_role.mention if seller_role else "@here"
            embed_warn = discord.Embed(
                title="⚠️ VC DISPENSED – USE WITHIN 2 HOURS",
                description=f"Card: `{vc_data['card']}`\nUser: <@{user_id}>\nExpires at {expiry_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
                color=discord.Color.orange()
            )
            await admin_channel.send(content=f"{role_mention}", embed=embed_warn)
        return True
    except Exception as e:
        print(f"❌ Exception in dispense_vc: {e}")
        import traceback
        traceback.print_exc()
        return False

# --------------------------
# VC Management Panel
# --------------------------
class VCPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="➕ Add Cards", style=discord.ButtonStyle.success, emoji="➕", row=0)
    async def add_card(self, interaction: discord.Interaction, button: ui.Button):
        try:
            await interaction.response.send_modal(AddCardModal())
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to open modal: {e}", ephemeral=True)

    @ui.button(label="📋 View Cards", style=discord.ButtonStyle.primary, emoji="📋", row=0)
    async def view_cards(self, interaction: discord.Interaction, button: ui.Button):
        cards = load_vc_pool()
        if not cards:
            await interaction.response.send_message("📭 **No cards in stock.**", ephemeral=True)
            return
        total = len(cards)
        display = cards[:20]
        embed = discord.Embed(
            title=f"💳 VC Stock ({total} cards)",
            description="\n".join([f"`{i+1}. {c['card']} | Exp: {c['expiry']} | CVV: {c['cvv']}`" for i, c in enumerate(display)]),
            color=discord.Color.blue()
        )
        if total > 20:
            embed.set_footer(text=f"Showing 20 of {total} cards")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="🗑️ Remove Card", style=discord.ButtonStyle.danger, emoji="🗑️", row=0)
    async def remove_card(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(RemoveCardModal())

    @ui.button(label="🧹 Clear All Cards", style=discord.ButtonStyle.danger, emoji="🧹", row=1)
    async def clear_all(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(ClearConfirmModal())

class ClearConfirmModal(ui.Modal, title="🧹 Clear All Cards"):
    confirm = ui.TextInput(label="Type 'CONFIRM' to delete all cards", placeholder="CONFIRM", required=True)
    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.strip().upper() == "CONFIRM":
            save_vc_pool([])
            await interaction.response.send_message("🧹 **All cards cleared.**", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Cancelled.", ephemeral=True)

class AddCardModal(ui.Modal, title="➕ Add Virtual Card"):
    card_number = ui.TextInput(label="Card Number", placeholder="1234567890123456789", required=True, max_length=19)
    expiry = ui.TextInput(label="Expiry (MM/YY)", placeholder="11/30", required=True, max_length=5)
    cvv = ui.TextInput(label="CVV", placeholder="123", required=True, max_length=4)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            raw_card = self.card_number.value.strip()
            raw_expiry = self.expiry.value.strip()
            raw_cvv = self.cvv.value.strip()
            if not raw_card.isdigit() or not (12 <= len(raw_card) <= 19):
                await interaction.response.send_message("❌ Invalid card number.", ephemeral=True)
                return
            if not raw_expiry.replace('/', '').isdigit() or len(raw_expiry.replace('/', '')) != 4:
                await interaction.response.send_message("❌ Invalid expiry.", ephemeral=True)
                return
            if not raw_cvv.isdigit() or not (3 <= len(raw_cvv) <= 4):
                await interaction.response.send_message("❌ Invalid CVV.", ephemeral=True)
                return
            formatted_expiry = raw_expiry if '/' in raw_expiry else f"{raw_expiry[:2]}/{raw_expiry[2:]}"
            cards = load_vc_pool()
            cards.append({"card": raw_card, "expiry": formatted_expiry, "cvv": raw_cvv})
            save_vc_pool(cards)
            await interaction.response.send_message(f"✅ Card added: `{raw_card} | {formatted_expiry} | {raw_cvv}`", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

class RemoveCardModal(ui.Modal, title="🗑️ Remove Card"):
    index = ui.TextInput(label="Card Number (1‑based)", placeholder="Enter the card number to remove", required=True)
    async def on_submit(self, interaction: discord.Interaction):
        try:
            idx = int(self.index.value) - 1
            cards = load_vc_pool()
            if 0 <= idx < len(cards):
                removed = cards.pop(idx)
                save_vc_pool(cards)
                await interaction.response.send_message(f"✅ Removed card: `{removed['card']}`", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Invalid card number.", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid number.", ephemeral=True)

# --------------------------
# Orderer Toggle UI
# --------------------------
class OrdererToggleView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="🟢 Go Online", style=discord.ButtonStyle.success, emoji="🟢")
    async def go_online(self, interaction: discord.Interaction, button: ui.Button):
        if SELLER_ROLE_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("❌ You don't have the Orderer role.", ephemeral=True)
            return
        set_orderer_online(interaction.user.id, True)
        await interaction.response.send_message("✅ You are now **Online**.", ephemeral=True)

    @ui.button(label="🔴 Go Offline", style=discord.ButtonStyle.danger, emoji="🔴")
    async def go_offline(self, interaction: discord.Interaction, button: ui.Button):
        if SELLER_ROLE_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("❌ You don't have the Orderer role.", ephemeral=True)
            return
        set_orderer_online(interaction.user.id, False)
        await interaction.response.send_message("✅ You are now **Offline**.", ephemeral=True)

# --------------------------
# VC Store View
# --------------------------
class StoreView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="💳 Purchase Virtual Card", style=discord.ButtonStyle.primary, emoji="✨", row=0)
    async def buy(self, interaction: discord.Interaction, button: ui.Button):
        if not load_vc_pool():
            await interaction.response.send_message("❌ **No stock available.**", ephemeral=True)
            return
        embed = discord.Embed(
            title="📋 Terms & Conditions",
            description=(
                "By purchasing, you agree:\n"
                "1. Use within 2 hours\n"
                "2. No chargebacks\n"
                "3. One-time use only"
            ),
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, view=TermsView(), ephemeral=True)

    @ui.button(label="📖 How to Use", style=discord.ButtonStyle.secondary, emoji="📖", row=0)
    async def how_to_use(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(
            title="📖 How to Purchase",
            description=(
                "1. Click 'Purchase Virtual Card'\n"
                "2. Agree to Terms\n"
                "3. Enter your PayPal email\n"
                "4. Send £1.00 to the PayPal address\n"
                "5. Wait for auto‑delivery in DMs"
            ),
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class TermsView(ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @ui.button(label="✅ Agree", style=discord.ButtonStyle.success, emoji="✅")
    async def agree(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(BuyModal())

    @ui.button(label="❌ Disagree", style=discord.ButtonStyle.danger, emoji="❌")
    async def disagree(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("❌ You must agree to purchase.", ephemeral=True)

class BuyModal(ui.Modal, title="💳 Purchase VC"):
    email = ui.TextInput(label="Your PayPal Email", placeholder="The email you'll send from", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        purchase_id = f"{interaction.user.id}-{secrets.token_hex(4)}"
        pending = load_pending()
        pending[purchase_id] = {
            "user_id": interaction.user.id,
            "payer_email": self.email.value.strip().lower()
        }
        await interaction.response.defer(ephemeral=True, thinking=False)
        embed = progress_embed(
            20,
            "⏳ Step 1: Email Submitted",
            f"Send £1.00 to: `{PAYPAL_EMAIL}`\nOnce payment is detected, this will update."
        )
        followup = await interaction.followup.send(embed=embed, ephemeral=True)
        pending[purchase_id]["followup_msg_id"] = followup.id
        pending[purchase_id]["followup_token"] = interaction.token
        save_pending(pending)

# --------------------------
# Ticket System: Create Category if missing
# --------------------------
async def ensure_category(guild, name):
    category = discord.utils.get(guild.categories, name=name)
    if category:
        return category
    # Create it
    try:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
        # Add roles if needed (optional)
        return await guild.create_category(name, overwrites=overwrites)
    except discord.Forbidden:
        print(f"❌ Bot lacks permissions to create category '{name}'.")
        return None
    except Exception as e:
        print(f"❌ Failed to create category '{name}': {e}")
        return None

async def create_ticket_channel(guild, category_name, user, staff, ticket_type, order_data=None, attachment_url=None):
    category = await ensure_category(guild, category_name)
    if not category:
        return None

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    if staff:
        overwrites[staff] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    if ticket_type == "general":
        support_role = guild.get_role(SUPPORT_ROLE)
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    ticket_name = f"{ticket_type}-{user.name[:5]}-{secrets.token_hex(3)}"
    try:
        channel = await category.create_text_channel(name=ticket_name, overwrites=overwrites)
    except Exception as e:
        print(f"❌ Failed to create channel: {e}")
        return None

    embed = discord.Embed(
        title=f"📋 {ticket_type.title()} Ticket",
        description=f"**User:** {user.mention}\n**Staff:** {staff.mention if staff else 'None assigned yet'}",
        color=discord.Color.blue()
    )
    if order_data:
        embed.add_field(name="📦 Order Details", value=order_data, inline=False)
    if attachment_url:
        embed.set_image(url=attachment_url)
    embed.set_footer(text="Use buttons below to manage.")

    view = TicketView(channel.id, user.id)
    await channel.send(embed=embed, view=view)
    await channel.send(f"{user.mention} {staff.mention if staff else ''} – Welcome!")

    tickets = load_tickets()
    ticket_entry = {
        "channel_id": channel.id,
        "user_id": user.id,
        "staff_id": staff.id if staff else None,
        "created_at": datetime.now(UTC).isoformat(),
        "order_data": order_data,
        "last_activity": datetime.now(UTC).isoformat()
    }
    tickets[ticket_type]["open"].append(ticket_entry)
    save_tickets(tickets)
    return channel

# --------------------------
# TicketView (inside ticket)
# --------------------------
class TicketView(ui.View):
    def __init__(self, channel_id, user_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user_id = user_id

    @ui.button(label="✅ Order Completed", style=discord.ButtonStyle.success, emoji="✅")
    async def complete_order(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id and not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
            return
        await interaction.response.send_message("🔄 Closing ticket...")
        await asyncio.sleep(5)
        channel = interaction.channel
        tickets = load_tickets()
        ticket_type = None
        for ttype in ["orders", "general"]:
            for idx, ticket in enumerate(tickets[ttype]["open"]):
                if ticket["channel_id"] == channel.id:
                    closed = ticket.copy()
                    closed["closed_at"] = datetime.now(UTC).isoformat()
                    closed["closed_by"] = interaction.user.id
                    closed["reason"] = "Order Completed"
                    tickets[ttype]["open"].pop(idx)
                    tickets[ttype]["closed"].append(closed)
                    ticket_type = ttype
                    break
            if ticket_type:
                break
        save_tickets(tickets)
        await channel.set_permissions(interaction.guild.default_role, read_messages=False)
        await channel.send("🔒 Ticket closed (Order Completed).")

    @ui.button(label="❌ Close Ticket", style=discord.ButtonStyle.danger, emoji="❌")
    async def close_ticket(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("❌ Only staff can close.", ephemeral=True)
            return
        await interaction.response.send_message("🔄 Closing ticket...")
        await asyncio.sleep(2)
        channel = interaction.channel
        tickets = load_tickets()
        ticket_type = None
        for ttype in ["orders", "general"]:
            for idx, ticket in enumerate(tickets[ttype]["open"]):
                if ticket["channel_id"] == channel.id:
                    closed = ticket.copy()
                    closed["closed_at"] = datetime.now(UTC).isoformat()
                    closed["closed_by"] = interaction.user.id
                    closed["reason"] = "Closed by staff"
                    tickets[ttype]["open"].pop(idx)
                    tickets[ttype]["closed"].append(closed)
                    ticket_type = ttype
                    break
            if ticket_type:
                break
        save_tickets(tickets)
        await channel.set_permissions(interaction.guild.default_role, read_messages=False)
        await channel.send("🔒 Ticket closed by staff.")

# --------------------------
# Review View
# --------------------------
class ReviewView(ui.View):
    def __init__(self, user_id, order_data, staff_id, attachment_url=None):
        super().__init__(timeout=600)
        self.user_id = user_id
        self.order_data = order_data
        self.staff_id = staff_id
        self.attachment_url = attachment_url

    @ui.button(label="✅ Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild
        if SELLER_ROLE_ID not in [role.id for role in interaction.user.roles] and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ No permission.", ephemeral=True)
            return
        user = await bot.fetch_user(self.user_id)
        staff = interaction.user
        await interaction.response.send_message("✅ Approved! Creating ticket...", ephemeral=True)
        await interaction.message.edit(content="✅ Approved! Ticket created.", view=None)
        channel = await create_ticket_channel(
            guild, CATEGORY_ORDERS_NAME, user, staff, "orders", self.order_data, self.attachment_url
        )
        if channel:
            try:
                await user.send(embed=discord.Embed(title="✅ Order Approved", description=f"Ticket: {channel.mention}", color=discord.Color.green()))
            except:
                pass
            await interaction.channel.send(f"✅ Ticket created: {channel.mention}")

    @ui.button(label="❌ Reject", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: ui.Button):
        if SELLER_ROLE_ID not in [role.id for role in interaction.user.roles] and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ No permission.", ephemeral=True)
            return
        await interaction.response.send_message("❌ Rejected.", ephemeral=True)
        await interaction.message.edit(content="❌ Rejected by staff.", view=None)
        try:
            user = await bot.fetch_user(self.user_id)
            await user.send(embed=discord.Embed(title="❌ Order Rejected", description="Your order was rejected.", color=discord.Color.red()))
        except:
            pass

# --------------------------
# Order Modal (form)
# --------------------------
class OrderModal(ui.Modal, title="📦 Order Details"):
    store = ui.TextInput(label="Store Name", placeholder="e.g., Amazon", required=True, max_length=100)
    total = ui.TextInput(label="Order Total", placeholder="£50.00", required=True, max_length=20)
    address = ui.TextInput(label="Address Line 1", placeholder="123 Main St", required=True, max_length=200)
    postcode = ui.TextInput(label="Postcode", placeholder="AB12 3CD", required=True, max_length=10)
    vcc = ui.TextInput(label="VCC Details (if applicable)", placeholder="Card info or leave blank", required=False, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=False)
        if not hasattr(bot, "order_form_cache"):
            bot.order_form_cache = {}
        bot.order_form_cache[str(interaction.user.id)] = {
            "store": self.store.value,
            "total": self.total.value,
            "address": self.address.value,
            "postcode": self.postcode.value,
            "vcc": self.vcc.value or "Not provided",
            "step": "awaiting_screenshot"
        }
        embed = discord.Embed(
            title="📸 Please Attach Screenshot",
            description=(
                f"**Store:** {self.store.value}\n"
                f"**Total:** {self.total.value}\n"
                f"**Address:** {self.address.value}\n"
                f"**Postcode:** {self.postcode.value}\n"
                f"**VCC:** {self.vcc.value or 'None'}\n\n"
                "Reply to this DM with your screenshot (attach as image)."
            ),
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

# --------------------------
# Support Modal (form)
# --------------------------
class SupportModal(ui.Modal, title="💬 General Support"):
    help_type = ui.TextInput(label="Type of help", placeholder="e.g., Account issue, Payment, etc.", required=True, max_length=100)
    description = ui.TextInput(label="Describe your issue", placeholder="Please provide details...", required=True, style=discord.TextStyle.paragraph, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=False)
        order_data = f"**Type:** {self.help_type.value}\n**Description:** {self.description.value}"
        channel = await create_ticket_channel(
            guild, CATEGORY_GENERAL_NAME, interaction.user, None, "general", order_data
        )
        if channel:
            await interaction.followup.send(f"✅ Support ticket created: {channel.mention}", ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to create support ticket. Please contact an admin.", ephemeral=True)

# --------------------------
# Dropdown
# --------------------------
class TicketDropdown(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="📦 Order", value="orders", description="Open an order ticket"),
            discord.SelectOption(label="💬 General Support", value="general", description="Open a support ticket")
        ]
        super().__init__(placeholder="Select a ticket type...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "orders":
            # Check if an orderer is online
            guild = interaction.guild
            staff = get_available_orderer(guild)
            if not staff:
                await interaction.response.send_message("❌ No orderers online.", ephemeral=True)
                return
            # Open Order Modal
            await interaction.response.send_modal(OrderModal())
        else:  # general
            await interaction.response.send_modal(SupportModal())

def get_available_orderer(guild):
    orderers = load_orderers()
    for member in guild.members:
        if member.bot:
            continue
        if SELLER_ROLE_ID in [role.id for role in member.roles]:
            if is_orderer_online(member.id):
                return member
    return None

class TicketViewDropdown(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown())

# --------------------------
# DM Handler for Order Screenshots
# --------------------------
async def handle_order_screenshot(user_id: int, attachment_url: str):
    if not hasattr(bot, "order_form_cache"):
        return
    cache = bot.order_form_cache.get(str(user_id))
    if not cache or cache.get("step") != "awaiting_screenshot":
        return

    order_data = (
        f"**Store:** {cache['store']}\n"
        f"**Total:** {cache['total']}\n"
        f"**Address:** {cache['address']}\n"
        f"**Postcode:** {cache['postcode']}\n"
        f"**VCC:** {cache['vcc']}\n"
        f"**Screenshot:** [View]({attachment_url})"
    )
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    staff = get_available_orderer(guild)
    staff_id = staff.id if staff else None
    review_channel = guild.get_channel(ADMIN_CHANNEL_ID)
    if not review_channel:
        return

    embed = discord.Embed(title="📦 New Order Review", description=order_data, color=discord.Color.blue())
    embed.set_image(url=attachment_url)
    embed.set_footer(text="Orderer will be assigned.")
    view = ReviewView(user_id, order_data, staff_id, attachment_url)
    await review_channel.send(content=f"📢 <@&{SELLER_ROLE_ID}>" if staff_id else "📢 No orderer online", embed=embed, view=view)

    try:
        user = await bot.fetch_user(user_id)
        await user.send(embed=discord.Embed(title="✅ Order Sent for Review", description="You'll be notified once approved.", color=discord.Color.green()))
    except:
        pass

    del bot.order_form_cache[str(user_id)]

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if isinstance(message.channel, discord.DMChannel):
        if hasattr(bot, "order_form_cache"):
            user_id_str = str(message.author.id)
            if user_id_str in bot.order_form_cache and bot.order_form_cache[user_id_str].get("step") == "awaiting_screenshot":
                if message.attachments:
                    await handle_order_screenshot(message.author.id, message.attachments[0].url)
                    await message.channel.send("✅ Screenshot received! Order sent for review.")
                else:
                    await message.channel.send("❌ Please attach an image.")
                return
        await bot.process_commands(message)
        return
    await bot.process_commands(message)

# --------------------------
# Admin Commands
# --------------------------
@bot.command(name="add_orderer")
@commands.has_permissions(administrator=True)
async def add_orderer(ctx, member: discord.Member):
    role = ctx.guild.get_role(SELLER_ROLE_ID)
    if not role:
        await ctx.send("❌ Orderer role not found.")
        return
    await member.add_roles(role)
    await ctx.send(f"✅ Added {member.mention} to Orderer role.")

@bot.command(name="remove_orderer")
@commands.has_permissions(administrator=True)
async def remove_orderer(ctx, member: discord.Member):
    role = ctx.guild.get_role(SELLER_ROLE_ID)
    if not role:
        await ctx.send("❌ Orderer role not found.")
        return
    await member.remove_roles(role)
    await ctx.send(f"✅ Removed {member.mention} from Orderer role.")

@bot.command(name="ticket_stats")
@commands.has_permissions(administrator=True)
async def ticket_stats(ctx):
    tickets = load_tickets()
    embed = discord.Embed(
        title="📊 Ticket Statistics",
        description=f"**Orders:** {len(tickets['orders']['open'])} open, {len(tickets['orders']['closed'])} closed\n"
                    f"**General:** {len(tickets['general']['open'])} open, {len(tickets['general']['closed'])} closed",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)

@bot.command(name="close_ticket")
@commands.has_permissions(manage_channels=True)
async def close_ticket_cmd(ctx):
    channel = ctx.channel
    if not channel.category or channel.category.name not in [CATEGORY_ORDERS_NAME, CATEGORY_GENERAL_NAME]:
        await ctx.send("❌ Not a ticket channel.")
        return
    tickets = load_tickets()
    ticket_type = None
    for ttype in ["orders", "general"]:
        for idx, t in enumerate(tickets[ttype]["open"]):
            if t["channel_id"] == channel.id:
                closed = t.copy()
                closed["closed_at"] = datetime.now(UTC).isoformat()
                closed["closed_by"] = ctx.author.id
                closed["reason"] = "Closed by admin"
                tickets[ttype]["open"].pop(idx)
                tickets[ttype]["closed"].append(closed)
                ticket_type = ttype
                break
        if ticket_type:
            break
    save_tickets(tickets)
    await ctx.send("🔒 Closing ticket...")
    await asyncio.sleep(2)
    await channel.set_permissions(ctx.guild.default_role, read_messages=False)
    await channel.send("🔒 Ticket closed by admin.")

@bot.command(name="ticket_log")
@commands.has_permissions(manage_channels=True)
async def ticket_log(ctx):
    channel = ctx.channel
    if not channel.category or channel.category.name not in [CATEGORY_ORDERS_NAME, CATEGORY_GENERAL_NAME]:
        await ctx.send("❌ Not a ticket channel.")
        return
    await ctx.send("📜 Generating transcript...")
    messages = []
    async for msg in channel.history(limit=200, oldest_first=True):
        messages.append(f"{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')} - {msg.author.display_name}: {msg.content}")
        if msg.attachments:
            for att in msg.attachments:
                messages.append(f"  [Attachment: {att.url}]")
    transcript = "\n".join(messages)
    with open("transcript.txt", "w", encoding="utf-8") as f:
        f.write(transcript)
    await ctx.send(file=discord.File("transcript.txt"))
    os.remove("transcript.txt")

@bot.command(name="claim_ticket")
@commands.has_permissions(manage_channels=True)
async def claim_ticket(ctx):
    channel = ctx.channel
    if not channel.category or channel.category.name not in [CATEGORY_ORDERS_NAME, CATEGORY_GENERAL_NAME]:
        await ctx.send("❌ Not a ticket channel.")
        return
    tickets = load_tickets()
    for ttype in ["orders", "general"]:
        for t in tickets[ttype]["open"]:
            if t["channel_id"] == channel.id:
                t["staff_id"] = ctx.author.id
                save_tickets(tickets)
                await ctx.send(f"✅ {ctx.author.mention} claimed this ticket.")
                return
    await ctx.send("❌ Ticket not found.")

# --------------------------
# Purge/Nuke
# --------------------------
@bot.command(name="purge")
@commands.has_permissions(manage_messages=True)
async def purge(ctx, amount: int = None):
    if not amount or amount < 1 or amount > 1000:
        await ctx.send("❌ Provide a number 1-1000.", delete_after=5)
        return
    try:
        deleted = await ctx.channel.purge(limit=amount + 1)
        msg = await ctx.send(f"✅ Deleted {len(deleted)-1} messages.")
        await asyncio.sleep(3)
        await msg.delete()
    except:
        await ctx.send("❌ Failed to purge.", delete_after=5)

@bot.command(name="nuke")
@commands.has_permissions(manage_messages=True)
async def nuke(ctx):
    try:
        await ctx.send("⚠️ Nuking...", delete_after=3)
        deleted = await ctx.channel.purge(limit=10000)
        msg = await ctx.send(f"💥 Deleted {len(deleted)} messages.")
        await asyncio.sleep(5)
        await msg.delete()
    except:
        await ctx.send("❌ Failed to nuke.", delete_after=5)

# --------------------------
# Setup Commands
# --------------------------
@bot.command(name="setup_vcpanel")
@commands.has_permissions(administrator=True)
async def setup_vcpanel(ctx):
    embed = discord.Embed(title="💳 VC Management Panel", description="Manage your VC stock.", color=discord.Color.purple())
    await ctx.send(embed=embed, view=VCPanelView())
    await ctx.message.delete()

@bot.command(name="setup_orderer_panel")
@commands.has_permissions(administrator=True)
async def setup_orderer_panel(ctx):
    embed = discord.Embed(title="🛎️ Orderer Availability", description="Click to go online/offline.", color=discord.Color.blue())
    await ctx.send(embed=embed, view=OrdererToggleView())

# --------------------------
# Background Tasks
# --------------------------
async def expiry_watcher():
    await bot.wait_until_ready()
    while not bot.is_closed():
        active = load_active()
        expired = []
        for card, data in active.items():
            if datetime.now(UTC) >= datetime.fromisoformat(data["expires_at"]):
                admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
                if admin_channel:
                    try:
                        seller_role = admin_channel.guild.get_role(SELLER_ROLE_ID)
                        role_mention = seller_role.mention if seller_role else "@here"
                        await admin_channel.send(
                            f"⏰ {role_mention} **TERMINATE THIS VC NOW!**\nCard: `{card}`\nUser: <@{data['user_id']}>\nExpired at {data['expires_at']}"
                        )
                    except:
                        pass
                expired.append(card)
        if expired:
            for card in expired:
                del active[card]
            save_active(active)
        await asyncio.sleep(60)

async def timer_updater():
    await bot.wait_until_ready()
    while not bot.is_closed():
        active = load_active()
        for card, data in active.items():
            exp = datetime.fromisoformat(data["expires_at"])
            remaining = exp - datetime.now(UTC)
            if remaining.total_seconds() <= 0:
                continue
            # update DM timer not implemented for simplicity
        await asyncio.sleep(30)

@tasks.loop(hours=1)
async def auto_close_inactive():
    if INACTIVE_CLOSE_HOURS <= 0:
        return
    tickets = load_tickets()
    now = datetime.now(UTC)
    closed_any = False
    for ttype in ["orders", "general"]:
        for ticket in tickets[ttype]["open"]:
            last = datetime.fromisoformat(ticket["last_activity"])
            if (now - last).total_seconds() > INACTIVE_CLOSE_HOURS * 3600:
                closed = ticket.copy()
                closed["closed_at"] = now.isoformat()
                closed["closed_by"] = None
                closed["reason"] = "Auto-closed"
                tickets[ttype]["open"].remove(ticket)
                tickets[ttype]["closed"].append(closed)
                closed_any = True
                # Lock channel
                guild = bot.get_guild(GUILD_ID)
                if guild:
                    channel = guild.get_channel(ticket["channel_id"])
                    if channel:
                        try:
                            await channel.set_permissions(guild.default_role, read_messages=False)
                            await channel.send("🔒 Auto-closed due to inactivity.")
                        except:
                            pass
    if closed_any:
        save_tickets(tickets)

# --------------------------
# Flask IPN Server
# --------------------------
app_flask = Flask(__name__)

@app_flask.route("/test")
def test():
    return "✅ Flask is running!"

@app_flask.route("/ipn", methods=["POST"])
def ipn():
    data = request.form.to_dict()
    print("📥 IPN received")
    payment_status = data.get("payment_status")
    payer_email = data.get("payer_email", "").strip().lower()
    txn_id = data.get("txn_id")
    amount = data.get("mc_gross") or data.get("amount") or "0.00"

    verify_url = "https://www.paypal.com/cgi-bin/webscr"
    verify_data = data.copy()
    verify_data["cmd"] = "_notify-validate"
    try:
        resp = requests.post(verify_url, data=verify_data, timeout=10)
        if resp.text != "VERIFIED":
            return "Invalid", 400
    except:
        return "Error", 500

    if payment_status != "Completed":
        return f"OK - {payment_status}", 200

    if float(amount) != 1.00:
        # Handle wrong amount
        pending = load_pending()
        matched = None
        for pid, pd in pending.items():
            if pd.get("payer_email") == payer_email:
                matched = pd
                del pending[pid]
                break
        if matched:
            asyncio.run_coroutine_threadsafe(
                handle_wrong_amount(matched["user_id"], payer_email, txn_id, amount, matched.get("followup_token"), matched.get("followup_msg_id")),
                bot.loop
            )
            save_pending(pending)
        return f"OK - Wrong amount {amount}", 200

    # Normal flow
    pending = load_pending()
    matched = None
    for pid, pd in pending.items():
        if pd.get("payer_email") == payer_email:
            matched = pd
            break
    if matched:
        user_id = matched["user_id"]
        token = matched.get("followup_token")
        msg_id = matched.get("followup_msg_id")
        del pending[pid]
        save_pending(pending)
        # Update progress to 50%
        if token and msg_id:
            embed = progress_embed(50, "💰 Payment Received!", "Confirming payment...", color=discord.Color.blue())
            asyncio.run_coroutine_threadsafe(edit_followup_embed(token, msg_id, embed), bot.loop)
        asyncio.run_coroutine_threadsafe(dispense_vc(user_id, token, msg_id), bot.loop)
        return "OK", 200
    else:
        # unmatched
        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            embed = discord.Embed(title="⚠️ Unmatched Payment", description=f"**Email:** {payer_email}\n**Amount:** £{amount}", color=discord.Color.orange())
            asyncio.run_coroutine_threadsafe(admin_channel.send(content=f"📢 <@&{SELLER_ROLE_ID}>", embed=embed), bot.loop)
        return "OK - Unmatched", 200

async def handle_wrong_amount(user_id, payer_email, txn_id, amount, token, msg_id):
    # Notify user via DM
    try:
        user = await bot.fetch_user(user_id)
        if user:
            embed = discord.Embed(title="⚠️ Wrong Amount", description=f"You sent £{amount}, but £1.00 is required.\nPurchase cancelled.", color=discord.Color.red())
            await user.send(embed=embed)
    except:
        pass
    if token and msg_id:
        embed = discord.Embed(title="❌ Payment Failed", description=f"You sent £{amount} – £1.00 required.", color=discord.Color.red())
        await edit_followup_embed(token, msg_id, embed)
    # Admin alert
    admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if admin_channel:
        embed = discord.Embed(title="⚠️ Wrong Amount", description=f"Email: {payer_email}\nAmount: £{amount}\nUser: <@{user_id}>", color=discord.Color.red())
        await admin_channel.send(content=f"📢 <@&{SELLER_ROLE_ID}>", embed=embed)

def run_flask():
    app_flask.run(host="0.0.0.0", port=PORT)

# --------------------------
# Bot Events
# --------------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    print(f"   Guild ID: {GUILD_ID}")
    print(f"   Admin Channel: {ADMIN_CHANNEL_ID}")
    print(f"   Orderer Role: {SELLER_ROLE_ID}")
    print(f"   Support Role: {SUPPORT_ROLE}")
    print(f"   Store Channel: {STORE_CHANNEL_ID}")

    bot.loop.create_task(expiry_watcher())
    bot.loop.create_task(timer_updater())
    if INACTIVE_CLOSE_HOURS > 0:
        auto_close_inactive.start()
        print(f"🔄 Auto-close enabled: {INACTIVE_CLOSE_HOURS} hours.")

    # Post panels
    # VC Store
    channel = bot.get_channel(STORE_CHANNEL_ID)
    if channel:
        embed = discord.Embed(title="🛍️ Virtual Card Store", description="Click below to purchase a card for £1.", color=discord.Color.gold())
        try:
            await channel.send(embed=embed, view=StoreView())
            print("✅ VC Store posted")
        except Exception as e:
            print(f"❌ Error posting VC Store: {e}")

    # VC Management + Orderer toggle
    panel_channel = bot.get_channel(VCPANEL_CHANNEL_ID)
    if panel_channel:
        embed = discord.Embed(title="💳 VC Management", description="Manage VC stock.", color=discord.Color.purple())
        try:
            await panel_channel.send(embed=embed, view=VCPanelView())
            print("✅ VC Management posted")
        except Exception as e:
            print(f"❌ Error posting VC Management: {e}")

        embed = discord.Embed(title="🛎️ Orderer Availability", description="Click to go online/offline.", color=discord.Color.blue())
        try:
            await panel_channel.send(embed=embed, view=OrdererToggleView())
            print("✅ Orderer toggle posted")
        except Exception as e:
            print(f"❌ Error posting Orderer toggle: {e}")

    # Ticket dropdown
    ticket_channel = bot.get_channel(TICKET_CHANNEL_ID)
    if ticket_channel:
        embed = discord.Embed(title="🎫 Ticket System", description="Select option below to open a ticket.", color=discord.Color.gold())
        try:
            await ticket_channel.send(embed=embed, view=TicketViewDropdown())
            print("✅ Ticket dropdown posted")
        except Exception as e:
            print(f"❌ Error posting ticket dropdown: {e}")
    else:
        print(f"❌ Ticket channel {TICKET_CHANNEL_ID} not found.")

    print("✅ Bot is ready.")

# --------------------------
# Run
# --------------------------
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)
