"""
FULL MERGED BOT – VC Card Store + Ticket System
------------------------------------------------
Features:
- VC Store: Purchase virtual cards via PayPal, auto-dispense, live timer, expiry alerts.
- Ticket System: Orders (with staff check, form-based collection, review channel) & General Support.
- Orderer Online/Offline Toggle UI.
- Admin commands for both systems.
- Auto‑posts all panels on startup.

Version: 2.2.0
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

# --------------------------
# Load environment variables
# --------------------------
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
    print("❌ BOT_TOKEN not set in environment variables.")
    exit(1)
if not GUILD_ID or not ADMIN_CHANNEL_ID or not SELLER_ROLE_ID or not SUPPORT_ROLE or not STORE_CHANNEL_ID or not PAYPAL_EMAIL:
    print("❌ One or more required environment variables missing.")
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
    file_path = get_vc_file()
    if not os.path.exists(file_path):
        save_vc_pool([])
        return []
    try:
        with open(file_path, "r") as f:
            data = json.load(f)
            cards = data.get("cards", [])
            original_len = len(cards)
            cards = [c for c in cards if c.get("card") != "4111111111111111"]
            if len(cards) != original_len:
                save_vc_pool(cards)
            return cards
    except json.JSONDecodeError:
        save_vc_pool([])
        return []

def save_vc_pool(cards):
    file_path = get_vc_file()
    with open(file_path, "w") as f:
        json.dump({"cards": cards}, f, indent=4)

def load_pending():
    file_path = get_pending_file()
    if not os.path.exists(file_path):
        return {}
    with open(file_path, "r") as f:
        return json.load(f)

def save_pending(data):
    file_path = get_pending_file()
    with open(file_path, "w") as f:
        json.dump(data, f, indent=4)

def load_active():
    file_path = get_active_file()
    if not os.path.exists(file_path):
        return {}
    with open(file_path, "r") as f:
        return json.load(f)

def save_active(data):
    file_path = get_active_file()
    with open(file_path, "w") as f:
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
# Discord Bot Setup
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
    embed = discord.Embed(
        title=title,
        description=f"{bar} **{percent}%**\n\n{description}",
        color=color
    )
    embed.set_footer(text="Your card will be delivered automatically.")
    return embed

async def edit_followup_embed(token: str, msg_id: int, embed: discord.Embed):
    url = f"https://discord.com/api/v10/webhooks/{bot.user.id}/{token}/messages/{msg_id}"
    payload = {"embeds": [embed.to_dict()]}
    try:
        resp = requests.patch(url, json=payload)
        if resp.status_code == 200:
            print("✅ Updated ephemeral followup with embed")
        else:
            print(f"❌ Failed to update followup: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"❌ Error updating followup: {e}")

# --------------------------
# VC System: Dispense
# --------------------------
async def dispense_vc(user_id: int, token: str = None, msg_id: int = None):
    print(f"🔍 dispense_vc called")
    try:
        cards = load_vc_pool()
        if not cards:
            admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
            if admin_channel:
                try:
                    await admin_channel.send("🚨 **No VCs left!**")
                except:
                    pass
            return False

        vc_data = cards.pop(0)
        save_vc_pool(cards)
        print(f"💳 Dispensing: {vc_data['card']}")

        expiry_time = datetime.now(UTC) + timedelta(hours=2)
        expiry_str = expiry_time.isoformat()

        user = await bot.fetch_user(user_id)
        dm_channel_id = None
        dm_message_id = None
        if user:
            embed_card = discord.Embed(
                title="✨ Your Virtual Card",
                description=f"**Card:** `{vc_data['card']}`\n**Expiry:** `{vc_data['expiry']}`\n**CVV:** `{vc_data['cvv']}`",
                color=discord.Color.green()
            )
            embed_card.add_field(name="⏰ Time Remaining", value="2 hours (updates live)", inline=False)
            embed_card.set_footer(text="This card will be terminated after 2 hours.")

            embed_confirm = discord.Embed(
                title="✅ Thank You for Your Order!",
                description="Your Virtual Card has been sent to your DMs above.\n\n**Please check your messages for the card details.**",
                color=discord.Color.gold()
            )
            embed_confirm.set_footer(text="You have 2 hours to use this card.")

            try:
                dm_channel = await user.create_dm()
                msg_card = await dm_channel.send(embed=embed_card)
                dm_channel_id = dm_channel.id
                dm_message_id = msg_card.id
                await dm_channel.send(embed=embed_confirm)
                print(f"✅ DM sent to user {user_id}")
            except discord.Forbidden:
                print(f"❌ Cannot DM user {user_id}")
            except Exception as e:
                print(f"❌ DM error: {e}")

        active = load_active()
        active[vc_data['card']] = {
            "user_id": user_id,
            "expires_at": expiry_str,
            "dm_channel_id": dm_channel_id,
            "dm_message_id": dm_message_id,
            "card_data": vc_data
        }
        save_active(active)

        if token and msg_id:
            embed = progress_embed(100, "✅ Card Delivered!", "Your Virtual Card has been sent to your DMs.", color=discord.Color.green())
            await edit_followup_embed(token, msg_id, embed)

        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            try:
                guild = admin_channel.guild
                seller_role = guild.get_role(SELLER_ROLE_ID)
                role_mention = seller_role.mention if seller_role else "@here"
                embed_warn = discord.Embed(
                    title="⚠️ VC DISPENSED – USE WITHIN 2 HOURS",
                    description=f"Card: `{vc_data['card']}`\nUser: <@{user_id}>\nExpires at {expiry_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    color=discord.Color.orange()
                )
                await admin_channel.send(content=f"{role_mention}", embed=embed_warn)
                print("✅ Admin alert sent")
            except discord.Forbidden:
                print(f"❌ No permission to send admin alert")
            except Exception as e:
                print(f"❌ Failed to send admin alert: {e}")

        return True
    except Exception as e:
        print(f"❌ Exception in dispense_vc: {e}")
        import traceback
        traceback.print_exc()
        return False

# --------------------------
# VC System: Wrong amount handler
# --------------------------
async def handle_wrong_amount(user_id: int, payer_email: str, txn_id: str, amount: str, token: str = None, msg_id: int = None):
    if token and msg_id:
        embed = discord.Embed(
            title="❌ Payment Failed – Wrong Amount",
            description=f"You sent **£{amount}** but the required amount is **£1.00**.\n\nYour purchase has been cancelled.",
            color=discord.Color.red()
        )
        await edit_followup_embed(token, msg_id, embed)

    try:
        user = await bot.fetch_user(user_id)
        if user:
            embed = discord.Embed(
                title="⚠️ Wrong Payment Amount",
                description=f"You sent **£{amount}** but the required amount is **£1.00**.\n\nYour purchase has been cancelled.",
                color=discord.Color.red()
            )
            await user.send(embed=embed)
            print(f"✅ Wrong amount DM sent to user {user_id}")
    except:
        pass

    admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if admin_channel:
        try:
            embed = discord.Embed(
                title="⚠️ Wrong Payment Amount Received",
                description=f"**Payer Email:** {payer_email}\n**Amount Received:** £{amount}\n**Expected:** £1.00\nUser: <@{user_id}>",
                color=discord.Color.red()
            )
            await admin_channel.send(content=f"📢 <@&{SELLER_ROLE_ID}>", embed=embed)
        except:
            pass

# --------------------------
# VC Management Panel
# --------------------------
class VCPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="➕ Add Cards", style=discord.ButtonStyle.success, emoji="➕", row=0)
    async def add_card(self, interaction: discord.Interaction, button: ui.Button):
        try:
            modal = AddCardModal()
            await interaction.response.send_modal(modal)
        except Exception as e:
            print(f"❌ Error sending Add Card modal: {e}")
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
        modal = RemoveCardModal()
        await interaction.response.send_modal(modal)

    @ui.button(label="🧹 Clear All Cards", style=discord.ButtonStyle.danger, emoji="🧹", row=1)
    async def clear_all(self, interaction: discord.Interaction, button: ui.Button):
        modal = ClearConfirmModal()
        await interaction.response.send_modal(modal)

class ClearConfirmModal(ui.Modal, title="🧹 Clear All Cards"):
    confirm = ui.TextInput(label="Type 'CONFIRM' to delete all cards", placeholder="CONFIRM", required=True)
    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.strip().upper() == "CONFIRM":
            save_vc_pool([])
            await interaction.response.send_message("🧹 **All cards have been cleared.**", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Clear cancelled – you must type 'CONFIRM'.", ephemeral=True)

class AddCardModal(ui.Modal, title="➕ Add Virtual Card"):
    card_number = ui.TextInput(label="Card Number (max 19 digits)", placeholder="1234567890123456789", required=True, max_length=19)
    expiry = ui.TextInput(label="Expiry Date (MM/YY)", placeholder="11/30", required=True, max_length=5)
    cvv = ui.TextInput(label="CVV (3-4 digits)", placeholder="123", required=True, max_length=4)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            raw_card = self.card_number.value.strip()
            raw_expiry = self.expiry.value.strip()
            raw_cvv = self.cvv.value.strip()

            if not raw_card.isdigit() or not (12 <= len(raw_card) <= 19):
                await interaction.response.send_message("❌ Invalid card – must be 12-19 digits.", ephemeral=True)
                return
            if not raw_expiry.replace('/', '').isdigit() or len(raw_expiry.replace('/', '')) != 4:
                await interaction.response.send_message("❌ Invalid expiry – use MM/YY.", ephemeral=True)
                return
            if not raw_cvv.isdigit() or not (3 <= len(raw_cvv) <= 4):
                await interaction.response.send_message("❌ Invalid CVV – must be 3-4 digits.", ephemeral=True)
                return

            formatted_card = raw_card
            formatted_expiry = raw_expiry if '/' in raw_expiry else f"{raw_expiry[:2]}/{raw_expiry[2:]}"
            formatted_cvv = raw_cvv

            cards = load_vc_pool()
            cards.append({"card": formatted_card, "expiry": formatted_expiry, "cvv": formatted_cvv})
            save_vc_pool(cards)
            await interaction.response.send_message(f"✅ Card added: `{formatted_card} | Exp: {formatted_expiry} | CVV: {formatted_cvv}`", ephemeral=True)
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
        await interaction.response.send_message("✅ You are now **Online** and will receive orders.", ephemeral=True)

    @ui.button(label="🔴 Go Offline", style=discord.ButtonStyle.danger, emoji="🔴")
    async def go_offline(self, interaction: discord.Interaction, button: ui.Button):
        if SELLER_ROLE_ID not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("❌ You don't have the Orderer role.", ephemeral=True)
            return
        set_orderer_online(interaction.user.id, False)
        await interaction.response.send_message("✅ You are now **Offline** and will not receive orders.", ephemeral=True)

# --------------------------
# VC Store View
# --------------------------
class StoreView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="💳 Purchase Virtual Card", style=discord.ButtonStyle.primary, emoji="✨", row=0)
    async def buy(self, interaction: discord.Interaction, button: ui.Button):
        cards = load_vc_pool()
        if not cards:
            await interaction.response.send_message("❌ **Error: No stock available.**", ephemeral=True)
            return
        embed = discord.Embed(
            title="📋 Terms & Conditions",
            description=(
                "By purchasing a Virtual Card, you agree to the following:\n\n"
                "**1.** You must use the Virtual Card within **2 hours** of receiving it.\n"
                "**2.** After 2 hours, the card will be terminated.\n"
                "**3.** **No chargebacks** – necessary action will be taken.\n"
                "**4.** This card is for **one-time use only**.\n\n"
                "Do you agree to these terms?"
            ),
            color=discord.Color.gold()
        )
        embed.set_footer(text="You must agree to proceed.")
        view = TermsView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @ui.button(label="📖 How to Use", style=discord.ButtonStyle.secondary, emoji="📖", row=0)
    async def how_to_use(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(
            title="📖 How to Purchase a Virtual Card",
            description=(
                "**1️⃣ Click 'Purchase Virtual Card'**\n"
                "**2️⃣ Agree to Terms & Conditions**\n"
                "**3️⃣ Enter your PayPal email** – must match the email you send from.\n"
                "**4️⃣ Send exactly £1.00** to the PayPal address shown.\n"
                "**5️⃣ Wait for confirmation** – your card will be sent to DMs.\n"
                "**6️⃣ Use the card within 2 hours** – it will be terminated after that."
            ),
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class TermsView(ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @ui.button(label="✅ Agree & Continue", style=discord.ButtonStyle.success, emoji="✅")
    async def agree(self, interaction: discord.Interaction, button: ui.Button):
        modal = BuyModal()
        await interaction.response.send_modal(modal)

    @ui.button(label="❌ Disagree", style=discord.ButtonStyle.danger, emoji="❌")
    async def disagree(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("❌ You must agree to the Terms & Conditions to purchase.", ephemeral=True)

class BuyModal(ui.Modal, title="💳 Purchase VC"):
    email = ui.TextInput(label="Your PayPal Email", placeholder="Enter the email you'll use to pay (must match)", required=True)

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
            f"Your email has been recorded.\n\n**Send £1.00 to:** `{PAYPAL_EMAIL}`\n"
            "Copy this email and paste it into PayPal.\n"
            "**DO NOT use PayPal.me – it doesn't work reliably.**\n\n"
            "Once payment is detected, this progress bar will update automatically."
        )
        followup_msg = await interaction.followup.send(embed=embed, ephemeral=True)
        pending[purchase_id]["followup_msg_id"] = followup_msg.id
        pending[purchase_id]["followup_token"] = interaction.token
        save_pending(pending)

# --------------------------
# Ticket System: Helpers
# --------------------------
def get_category(guild, name):
    for cat in guild.categories:
        if cat.name.lower() == name.lower():
            return cat
    return None

def get_available_orderer(guild):
    orderers = load_orderers()
    for member in guild.members:
        if member.bot:
            continue
        if SELLER_ROLE_ID in [role.id for role in member.roles]:
            if is_orderer_online(member.id):
                return member
    return None

async def create_ticket_channel(guild, category_name, user, staff, ticket_type, order_data=None, attachment_url=None):
    category = get_category(guild, category_name)
    if not category:
        print(f"❌ Category '{category_name}' not found. Please create it.")
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
    except discord.Forbidden:
        print(f"❌ Bot lacks permissions to create channel in category '{category_name}'.")
        return None
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
    embed.set_footer(text="Use the buttons below to manage this ticket.")

    view = TicketView(channel.id, user.id)
    await channel.send(embed=embed, view=view)
    await channel.send(f"{user.mention} {staff.mention if staff else ''} – Welcome to your ticket!")

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
# Ticket System: TicketView
# --------------------------
class TicketView(ui.View):
    def __init__(self, channel_id, user_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user_id = user_id

    @ui.button(label="✅ Order Completed", style=discord.ButtonStyle.success, emoji="✅")
    async def complete_order(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id and not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
            return

        await interaction.response.send_message("🔄 Closing ticket in 5 seconds...")
        await asyncio.sleep(5)

        channel = interaction.channel
        tickets = load_tickets()
        ticket_type = None
        for ttype in ["orders", "general"]:
            for idx, ticket in enumerate(tickets[ttype]["open"]):
                if ticket["channel_id"] == channel.id:
                    closed_ticket = ticket.copy()
                    closed_ticket["closed_at"] = datetime.now(UTC).isoformat()
                    closed_ticket["closed_by"] = interaction.user.id
                    closed_ticket["reason"] = "Order Completed"
                    tickets[ttype]["open"].pop(idx)
                    tickets[ttype]["closed"].append(closed_ticket)
                    ticket_type = ttype
                    break
            if ticket_type:
                break
        save_tickets(tickets)
        await channel.set_permissions(interaction.guild.default_role, read_messages=False)
        await channel.send("🔒 Ticket closed and archived (Order Completed).")

    @ui.button(label="❌ Close Ticket", style=discord.ButtonStyle.danger, emoji="❌")
    async def close_ticket(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("❌ Only staff can close tickets.", ephemeral=True)
            return

        await interaction.response.send_message("🔄 Closing ticket...")
        await asyncio.sleep(2)

        channel = interaction.channel
        tickets = load_tickets()
        ticket_type = None
        for ttype in ["orders", "general"]:
            for idx, ticket in enumerate(tickets[ttype]["open"]):
                if ticket["channel_id"] == channel.id:
                    closed_ticket = ticket.copy()
                    closed_ticket["closed_at"] = datetime.now(UTC).isoformat()
                    closed_ticket["closed_by"] = interaction.user.id
                    closed_ticket["reason"] = "Closed by staff"
                    tickets[ttype]["open"].pop(idx)
                    tickets[ttype]["closed"].append(closed_ticket)
                    ticket_type = ttype
                    break
            if ticket_type:
                break
        save_tickets(tickets)
        await channel.set_permissions(interaction.guild.default_role, read_messages=False)
        await channel.send("🔒 Ticket closed by staff.")

# --------------------------
# Ticket System: ReviewView
# --------------------------
class ReviewView(ui.View):
    def __init__(self, user_id, order_data, staff_id, attachment_url=None):
        super().__init__(timeout=600)
        self.user_id = user_id
        self.order_data = order_data
        self.staff_id = staff_id
        self.attachment_url = attachment_url

    @ui.button(label="✅ Approve Order", style=discord.ButtonStyle.success, emoji="✅")
    async def approve_order(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild
        orderer_role = guild.get_role(SELLER_ROLE_ID)
        if orderer_role not in interaction.user.roles and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ You don't have permission to approve orders.", ephemeral=True)
            return

        user = await bot.fetch_user(self.user_id)
        staff = interaction.user

        await interaction.response.send_message("✅ Order approved! Creating ticket...", ephemeral=True)
        await interaction.message.edit(content="✅ Order approved! Ticket created.", view=None)

        channel = await create_ticket_channel(
            guild,
            CATEGORY_ORDERS_NAME,
            user,
            staff,
            "orders",
            self.order_data,
            self.attachment_url
        )
        if channel:
            try:
                embed = discord.Embed(
                    title="✅ Order Approved",
                    description=f"Your order has been approved! Please check your ticket: {channel.mention}",
                    color=discord.Color.green()
                )
                await user.send(embed=embed)
            except:
                pass
            await interaction.channel.send(f"✅ Ticket created: {channel.mention} for {user.mention}")
        else:
            await interaction.channel.send(f"❌ Failed to create ticket for {user.mention}.")

    @ui.button(label="❌ Reject Order", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject_order(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild
        orderer_role = guild.get_role(SELLER_ROLE_ID)
        if orderer_role not in interaction.user.roles and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ You don't have permission to reject orders.", ephemeral=True)
            return

        await interaction.response.send_message("❌ Order rejected.", ephemeral=True)
        await interaction.message.edit(content="❌ Order rejected by staff.", view=None)

        try:
            user = await bot.fetch_user(self.user_id)
            embed = discord.Embed(
                title="❌ Order Rejected",
                description="Your order was rejected. Please review your details and try again.",
                color=discord.Color.red()
            )
            await user.send(embed=embed)
        except:
            pass

# --------------------------
# Order Modal (the form for orders)
# --------------------------
class OrderModal(ui.Modal, title="📦 Order Details"):
    store = ui.TextInput(label="Store Name", placeholder="e.g., Amazon, eBay, etc.", required=True, max_length=100)
    total = ui.TextInput(label="Order Total", placeholder="e.g., £50.00 or 50.00", required=True, max_length=20)
    details = ui.TextInput(label="Payment Details", placeholder="Enter payment details (VCC info, etc.)", required=True, style=discord.TextStyle.paragraph, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        # Defer so we can send a followup for screenshot
        await interaction.response.defer(ephemeral=True, thinking=False)

        # Store the modal data in a temporary cache
        if not hasattr(bot, "order_form_cache"):
            bot.order_form_cache = {}
        bot.order_form_cache[str(interaction.user.id)] = {
            "store": self.store.value,
            "total": self.total.value,
            "details": self.details.value,
            "step": "awaiting_screenshot"
        }

        # Ask user to attach screenshot
        embed = discord.Embed(
            title="📸 Please Attach Screenshot",
            description="Please reply to this message with your screenshot.\n\n"
                        "**Store:** " + self.store.value + "\n"
                        "**Total:** " + self.total.value + "\n\n"
                        "Reply with your screenshot (attach as image) and I'll send it to review.",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

# --------------------------
# Order Screenshot Handler (handles the attachment reply)
# --------------------------
async def handle_order_screenshot(user_id: int, attachment_url: str):
    if not hasattr(bot, "order_form_cache"):
        return None

    cache = bot.order_form_cache.get(str(user_id))
    if not cache or cache.get("step") != "awaiting_screenshot":
        return None

    # Build order data
    order_data = (
        f"**Store:** {cache['store']}\n"
        f"**Total:** {cache['total']}\n"
        f"**Details:** {cache['details']}\n"
        f"**Screenshot:** [View]({attachment_url})"
    )

    # Find available orderer
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return None

    staff = get_available_orderer(guild)
    staff_id = staff.id if staff else None

    # Send to review channel
    review_channel = guild.get_channel(ADMIN_CHANNEL_ID)
    if not review_channel:
        return None

    embed = discord.Embed(
        title="📦 New Order Review",
        description=order_data,
        color=discord.Color.blue()
    )
    embed.set_image(url=attachment_url)
    embed.set_footer(text="Orderer will be assigned automatically.")

    view = ReviewView(user_id, order_data, staff_id, attachment_url)
    staff_mention = f"<@&{SELLER_ROLE_ID}>" if staff_id else "No orderer online"
    await review_channel.send(content=f"📢 {staff_mention}", embed=embed, view=view)

    # Notify user
    user = await bot.fetch_user(user_id)
    if user:
        try:
            embed = discord.Embed(
                title="✅ Order Sent for Review",
                description=f"Your order has been sent to {staff.mention if staff else 'review'}.\nYou'll be notified once approved or rejected.",
                color=discord.Color.green()
            )
            await user.send(embed=embed)
        except:
            pass

    # Clear cache
    del bot.order_form_cache[str(user_id)]

    return True

# --------------------------
# DM Handler (for order screenshot)
# --------------------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Handle DM messages for order screenshots
    if isinstance(message.channel, discord.DMChannel):
        if not hasattr(bot, "order_form_cache"):
            bot.order_form_cache = {}

        user_id_str = str(message.author.id)
        if user_id_str in bot.order_form_cache and bot.order_form_cache[user_id_str].get("step") == "awaiting_screenshot":
            if message.attachments:
                attachment_url = message.attachments[0].url
                await handle_order_screenshot(message.author.id, attachment_url)
                await message.channel.send("✅ Screenshot received! Your order has been sent for review.")
            else:
                await message.channel.send("❌ Please attach an image/screenshot to this message.")
            return

        # Pass through to command processing
        await bot.process_commands(message)
        return

    # Guild messages – process commands
    await bot.process_commands(message)

# --------------------------
# Ticket System: Dropdown & Views
# --------------------------
class TicketDropdown(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="📦 Order", value="orders", description="Open an order ticket"),
            discord.SelectOption(label="💬 General Support", value="general", description="Open a general support ticket")
        ]
        super().__init__(placeholder="Select a ticket type...", options=options)

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if selected == "orders":
            guild = interaction.guild
            staff = get_available_orderer(guild)
            if not staff:
                await interaction.response.send_message(
                    "❌ No orderers are currently online. Please try again later.",
                    ephemeral=True
                )
                return

            # Open the Order Modal (form)
            modal = OrderModal()
            await interaction.response.send_modal(modal)

        else:  # general
            guild = interaction.guild
            await interaction.response.send_message("🔄 Creating your support ticket...", ephemeral=True)
            channel = await create_ticket_channel(
                guild,
                CATEGORY_GENERAL_NAME,
                interaction.user,
                None,
                "general",
                None
            )
            if channel:
                await interaction.followup.send(f"✅ Support ticket created: {channel.mention}", ephemeral=True)
            else:
                await interaction.followup.send("❌ Failed to create support ticket. Please contact an admin.", ephemeral=True)

class TicketViewDropdown(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown())

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
    orders_cat = get_category(ctx.guild, CATEGORY_ORDERS_NAME)
    general_cat = get_category(ctx.guild, CATEGORY_GENERAL_NAME)
    if not channel.category or channel.category.id not in [orders_cat.id if orders_cat else 0, general_cat.id if general_cat else 0]:
        await ctx.send("❌ This command can only be used in ticket channels.")
        return

    tickets = load_tickets()
    ticket_type = None
    for ttype in ["orders", "general"]:
        for idx, ticket in enumerate(tickets[ttype]["open"]):
            if ticket["channel_id"] == channel.id:
                closed_ticket = ticket.copy()
                closed_ticket["closed_at"] = datetime.now(UTC).isoformat()
                closed_ticket["closed_by"] = ctx.author.id
                closed_ticket["reason"] = "Closed by admin command"
                tickets[ttype]["open"].pop(idx)
                tickets[ttype]["closed"].append(closed_ticket)
                ticket_type = ttype
                break
        if ticket_type:
            break
    save_tickets(tickets)
    await ctx.send("🔒 Closing this ticket...")
    await asyncio.sleep(2)
    await channel.set_permissions(ctx.guild.default_role, read_messages=False)
    await channel.send("🔒 Ticket closed by admin.")

@bot.command(name="ticket_log")
@commands.has_permissions(manage_channels=True)
async def ticket_log(ctx):
    channel = ctx.channel
    orders_cat = get_category(ctx.guild, CATEGORY_ORDERS_NAME)
    general_cat = get_category(ctx.guild, CATEGORY_GENERAL_NAME)
    if not channel.category or channel.category.id not in [orders_cat.id if orders_cat else 0, general_cat.id if general_cat else 0]:
        await ctx.send("❌ This command can only be used in ticket channels.")
        return

    await ctx.send("📜 Generating transcript...")
    messages = []
    async for msg in channel.history(limit=200, oldest_first=True):
        messages.append(f"{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')} - {msg.author.display_name}: {msg.content}")
        if msg.attachments:
            for att in msg.attachments:
                messages.append(f"  [Attachment: {att.url}]")
    transcript = "\n".join(messages)
    if not transcript:
        transcript = "No messages found."
    with open("transcript.txt", "w", encoding="utf-8") as f:
        f.write(transcript)
    await ctx.send(file=discord.File("transcript.txt"))
    os.remove("transcript.txt")

@bot.command(name="claim_ticket")
@commands.has_permissions(manage_channels=True)
async def claim_ticket(ctx):
    channel = ctx.channel
    orders_cat = get_category(ctx.guild, CATEGORY_ORDERS_NAME)
    general_cat = get_category(ctx.guild, CATEGORY_GENERAL_NAME)
    if not channel.category or channel.category.id not in [orders_cat.id if orders_cat else 0, general_cat.id if general_cat else 0]:
        await ctx.send("❌ This command can only be used in ticket channels.")
        return

    tickets = load_tickets()
    for ttype in ["orders", "general"]:
        for ticket in tickets[ttype]["open"]:
            if ticket["channel_id"] == channel.id:
                ticket["staff_id"] = ctx.author.id
                save_tickets(tickets)
                await ctx.send(f"✅ {ctx.author.mention} has claimed this ticket.")
                return
    await ctx.send("❌ Could not find this ticket in the database.")

# --------------------------
# Purge/Nuke Commands
# --------------------------
@bot.command(name="purge")
@commands.has_permissions(manage_messages=True)
async def purge(ctx, amount: int = None):
    if amount is None:
        await ctx.send("❌ Please specify a number. Example: `!purge 50`", delete_after=5)
        return
    if amount < 1 or amount > 1000:
        await ctx.send("❌ Amount must be between 1 and 1000.", delete_after=5)
        return
    try:
        deleted = await ctx.channel.purge(limit=amount + 1)
        msg = await ctx.send(f"✅ Deleted {len(deleted) - 1} messages.")
        await asyncio.sleep(3)
        await msg.delete()
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to delete messages.", delete_after=5)

@bot.command(name="nuke")
@commands.has_permissions(manage_messages=True)
async def nuke(ctx):
    try:
        await ctx.send("⚠️ Nuking channel... this will delete ALL messages!", delete_after=3)
        deleted = await ctx.channel.purge(limit=10000)
        msg = await ctx.send(f"💥 Channel nuked! Deleted {len(deleted)} messages.")
        await asyncio.sleep(5)
        await msg.delete()
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to delete messages.", delete_after=5)

# --------------------------
# Setup Commands
# --------------------------
@bot.command(name="setup_vcpanel")
@commands.has_permissions(administrator=True)
async def setup_vcpanel(ctx):
    embed = discord.Embed(
        title="💳 VC Management Panel",
        description="Use the buttons below to manage your VC stock.",
        color=discord.Color.purple()
    )
    await ctx.send(embed=embed, view=VCPanelView())
    await ctx.message.delete()

@bot.command(name="setup_orderer_panel")
@commands.has_permissions(administrator=True)
async def setup_orderer_panel(ctx):
    embed = discord.Embed(
        title="🛎️ Orderer Availability",
        description="Click the buttons below to go online or offline.\nYou must have the Orderer role to use this.",
        color=discord.Color.blue()
    )
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
            exp = datetime.fromisoformat(data["expires_at"])
            if datetime.now(UTC) >= exp:
                admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
                if admin_channel:
                    try:
                        seller_role = admin_channel.guild.get_role(SELLER_ROLE_ID)
                        role_mention = seller_role.mention if seller_role else "@here"
                        await admin_channel.send(
                            f"⏰ {role_mention} **TERMINATE THIS VC NOW!**\n"
                            f"Card: `{card}`\nUser: <@{data['user_id']}>\nExpired at {exp.strftime('%Y-%m-%d %H:%M:%S UTC')}"
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
            dm_channel_id = data.get("dm_channel_id")
            dm_message_id = data.get("dm_message_id")
            if dm_channel_id and dm_message_id:
                try:
                    channel = bot.get_channel(dm_channel_id)
                    if channel:
                        msg = await channel.fetch_message(dm_message_id)
                        if msg.embeds:
                            embed = msg.embeds[0]
                            minutes, seconds = divmod(int(remaining.total_seconds()), 60)
                            hours, minutes = divmod(minutes, 60)
                            embed.set_field_at(0, name="⏰ Time Remaining", value=f"{hours}h {minutes}m {seconds}s", inline=False)
                            await msg.edit(embed=embed)
                except:
                    pass
        await asyncio.sleep(30)

@tasks.loop(hours=1)
async def auto_close_inactive():
    if INACTIVE_CLOSE_HOURS <= 0:
        return
    tickets = load_tickets()
    now = datetime.now(UTC)
    closed_any = False
    for ttype in ["orders", "general"]:
        to_close = []
        for idx, ticket in enumerate(tickets[ttype]["open"]):
            last_activity = datetime.fromisoformat(ticket["last_activity"])
            if (now - last_activity).total_seconds() > INACTIVE_CLOSE_HOURS * 3600:
                to_close.append(idx)
        for idx in reversed(to_close):
            ticket = tickets[ttype]["open"].pop(idx)
            closed_ticket = ticket.copy()
            closed_ticket["closed_at"] = now.isoformat()
            closed_ticket["closed_by"] = None
            closed_ticket["reason"] = "Auto-closed due to inactivity"
            tickets[ttype]["closed"].append(closed_ticket)
            closed_any = True
            guild = bot.get_guild(GUILD_ID)
            if guild:
                channel = guild.get_channel(ticket["channel_id"])
                if channel:
                    try:
                        await channel.set_permissions(guild.default_role, read_messages=False)
                        await channel.send("🔒 This ticket has been auto-closed due to inactivity.")
                    except:
                        pass
    if closed_any:
        save_tickets(tickets)

# --------------------------
# Flask IPN Server
# --------------------------
app_flask = Flask(__name__)

@app_flask.route("/test", methods=["GET"])
def test():
    return "✅ Flask server is running!", 200

@app_flask.route("/ipn", methods=["POST"])
def ipn():
    data = request.form.to_dict()
    print("📥 IPN received!")

    payment_status = data.get("payment_status")
    payer_email = data.get("payer_email", "").strip().lower()
    txn_id = data.get("txn_id")
    amount = data.get("mc_gross") or data.get("amount") or "0.00"
    print(f"🔑 Status: {payment_status}, Email: {payer_email}, TXID: {txn_id}, Amount: {amount}")

    verify_url = "https://www.paypal.com/cgi-bin/webscr"
    verify_data = data.copy()
    verify_data["cmd"] = "_notify-validate"
    try:
        resp = requests.post(verify_url, data=verify_data, timeout=10)
        print(f"✅ PayPal verification: {resp.text[:50]}...")
    except Exception as e:
        print(f"❌ Verification failed: {e}")
        return "Verification failed", 500

    if resp.text != "VERIFIED":
        print("❌ IPN verification failed")
        return "Verification failed", 400

    print("✅ IPN verified")
    if payment_status != "Completed":
        print(f"📌 Status: {payment_status} – not dispensing")
        return f"OK - {payment_status}", 200

    try:
        amt = float(amount)
    except:
        amt = 0.0

    if amt != 1.00:
        print(f"⚠️ Amount is {amount}, not £1.00")
        pending = load_pending()
        matched_user_id = None
        token = None
        msg_id = None
        for purchase_id, data in pending.items():
            if data.get("payer_email", "").strip().lower() == payer_email:
                matched_user_id = data.get("user_id")
                token = data.get("followup_token")
                msg_id = data.get("followup_msg_id")
                del pending[purchase_id]
                save_pending(pending)
                break
        if matched_user_id:
            asyncio.run_coroutine_threadsafe(
                handle_wrong_amount(matched_user_id, payer_email, txn_id, amount, token, msg_id),
                bot.loop
            )
        else:
            admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
            if admin_channel:
                try:
                    embed = discord.Embed(
                        title="⚠️ Wrong Payment Amount",
                        description=f"**Email:** {payer_email}\n**Amount:** £{amount}\n**TXID:** {txn_id}",
                        color=discord.Color.red()
                    )
                    asyncio.run_coroutine_threadsafe(
                        admin_channel.send(content=f"📢 <@&{SELLER_ROLE_ID}>", embed=embed),
                        bot.loop
                    )
                except:
                    pass
        return f"OK - Wrong amount {amount}", 200

    print("✅ Payment Completed & Amount verified")
    pending = load_pending()
    matched_purchase_id = None
    matched_user_id = None
    token = None
    msg_id = None

    for purchase_id, data in pending.items():
        if data.get("payer_email", "").strip().lower() == payer_email:
            matched_purchase_id = purchase_id
            matched_user_id = data.get("user_id")
            token = data.get("followup_token")
            msg_id = data.get("followup_msg_id")
            break

    if matched_purchase_id and matched_user_id:
        print(f"✅ Found matching email: {payer_email} -> User {matched_user_id}")
        del pending[matched_purchase_id]
        save_pending(pending)

        if token and msg_id:
            embed = progress_embed(50, "💰 Payment Received!", "Your payment has been confirmed.\nWe're now preparing your Virtual Card.", color=discord.Color.blue())
            asyncio.run_coroutine_threadsafe(edit_followup_embed(token, msg_id, embed), bot.loop)

        asyncio.run_coroutine_threadsafe(dispense_vc(matched_user_id, token, msg_id), bot.loop)
        return "OK", 200
    else:
        print(f"❌ No pending purchase found for email: {payer_email}")
        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            try:
                embed = discord.Embed(
                    title="⚠️ Unmatched Payment",
                    description=f"**Email:** {payer_email}\n**Amount:** £{amount}\n**TXID:** {txn_id}",
                    color=discord.Color.orange()
                )
                asyncio.run_coroutine_threadsafe(
                    admin_channel.send(content=f"📢 <@&{SELLER_ROLE_ID}>", embed=embed),
                    bot.loop
                )
            except:
                pass
        return "OK - Unmatched", 200

def run_flask():
    app_flask.run(host="0.0.0.0", port=PORT)

# --------------------------
# Bot Events
# --------------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    print(f"   Guild ID: {GUILD_ID}")
    print(f"   Admin/Review Channel: {ADMIN_CHANNEL_ID}")
    print(f"   Seller/Orderer Role: {SELLER_ROLE_ID}")
    print(f"   Support Role: {SUPPORT_ROLE}")
    print(f"   Store Channel: {STORE_CHANNEL_ID}")
    print(f"   Ticket Dropdown Channel: {TICKET_CHANNEL_ID}")
    print(f"   VC Management Panel Channel: {VCPANEL_CHANNEL_ID}")

    bot.loop.create_task(expiry_watcher())
    bot.loop.create_task(timer_updater())
    if INACTIVE_CLOSE_HOURS > 0:
        auto_close_inactive.start()
        print(f"🔄 Auto-close enabled: {INACTIVE_CLOSE_HOURS} hours.")

    # Post VC Store
    channel = bot.get_channel(STORE_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title="🛍️ Virtual Card Store",
            description="Click the button below to purchase a Virtual Card for **£1**.\n\nAfter payment, the card is delivered automatically.",
            color=discord.Color.gold()
        )
        embed.set_footer(text="Cards expire 2 hours after delivery.")
        try:
            await channel.send(embed=embed, view=StoreView())
            print(f"✅ VC Store posted in {channel.name}")
        except Exception as e:
            print(f"❌ Error posting VC Store: {e}")

    # Post VC Management Panel
    panel_channel = bot.get_channel(VCPANEL_CHANNEL_ID)
    if panel_channel:
        embed = discord.Embed(
            title="💳 VC Management Panel",
            description="Use the buttons below to manage your VC stock.",
            color=discord.Color.purple()
        )
        try:
            await panel_channel.send(embed=embed, view=VCPanelView())
            print(f"✅ VC Management Panel posted in {panel_channel.name}")
        except Exception as e:
            print(f"❌ Error posting VC Management Panel: {e}")

        # Orderer Toggle Panel
        embed = discord.Embed(
            title="🛎️ Orderer Availability",
            description="Click the buttons below to go online or offline.\nYou must have the Orderer role to use this.",
            color=discord.Color.blue()
        )
        try:
            await panel_channel.send(embed=embed, view=OrdererToggleView())
            print(f"✅ Orderer Toggle Panel posted in {panel_channel.name}")
        except Exception as e:
            print(f"❌ Error posting Orderer Toggle Panel: {e}")

    # Post Ticket Dropdown
    ticket_channel = bot.get_channel(TICKET_CHANNEL_ID)
    if ticket_channel:
        embed = discord.Embed(
            title="🎫 Ticket System",
            description="Select an option from the dropdown below to open a ticket.",
            color=discord.Color.gold()
        )
        try:
            await ticket_channel.send(embed=embed, view=TicketViewDropdown())
            print(f"✅ Ticket dropdown posted in {ticket_channel.name}")
        except Exception as e:
            print(f"❌ Error posting ticket dropdown: {e}")
    else:
        print(f"❌ Ticket channel {TICKET_CHANNEL_ID} not found.")

    print("✅ Bot is fully ready.")

# --------------------------
# Main
# --------------------------
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)
