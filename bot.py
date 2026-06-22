"""
FULL MERGED BOT – VC Card Store + Ticket System
------------------------------------------------
Features:
- VC Store: Purchase virtual cards via PayPal, auto-dispense, live timer, expiry alerts.
- Ticket System: Orders (with staff check, DM collection, review channel) & General Support.
- Admin commands for both systems.
- Auto‑posts VC store and ticket dropdown on startup.

Version: 2.0.0
"""

import discord
from discord import ui, app_commands
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
TOKEN = "MTUxNjk0NDI3NjY0MTAyNjIyOA.G5F-2T.FyCLi_HITdboi4jq0BckxycW4Pwy9tzBMRbRyk"  # Paste your actual token
GUILD_ID = int(os.getenv("GUILD_ID"))
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))   # Also used as review channel for orders
SELLER_ROLE_ID = int(os.getenv("SELLER_ROLE_ID"))       # Used as Orderer role
SUPPORT_ROLE = int(os.getenv("SUPPORT_ROLE"))           # Support role (you need to add this)
STORE_CHANNEL_ID = int(os.getenv("STORE_CHANNEL_ID"))   # Where VC store appears
PAYPAL_EMAIL = os.getenv("PAYPAL_EMAIL")
PORT = int(os.getenv("PORT", 5000))

# VC card files
VC_FILE = "vcs.json"
PENDING_FILE = "pending.json"
ACTIVE_FILE = "active.json"

# Ticket system files
TICKETS_FILE = "tickets.json"
CATEGORY_ORDERS_NAME = "Orders"
CATEGORY_GENERAL_NAME = "General"
TICKET_CHANNEL_ID = 1511704776046280826  # Lobby for ticket dropdown
INACTIVE_CLOSE_HOURS = 24

BASE_DIR = Path(__file__).parent.absolute()

# --------------------------
# Path helpers for VC files
# --------------------------
def get_vc_file():
    return os.path.join(BASE_DIR, VC_FILE)

def get_pending_file():
    return os.path.join(BASE_DIR, PENDING_FILE)

def get_active_file():
    return os.path.join(BASE_DIR, ACTIVE_FILE)

# --------------------------
# VC File helpers
# --------------------------
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
# VC System: Progress Embed helper
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
    print(f"🔍 dispense_vc called: user_id={user_id}, token={'present' if token else 'None'}, msg_id={msg_id}")
    try:
        cards = load_vc_pool()
        print(f"📊 Cards loaded: {len(cards)} cards in stock")
        if not cards:
            admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
            if admin_channel:
                try:
                    await admin_channel.send("🚨 **No VCs left!** Use `!setup_vcpanel` to add more.")
                except:
                    pass
            return False

        vc_data = cards.pop(0)
        save_vc_pool(cards)
        print(f"💳 Dispensing card: {vc_data['card']}")

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
                description="Your Virtual Card has been sent to your DMs above.\n\n"
                            "**Please check your messages for the card details.**",
                color=discord.Color.gold()
            )
            embed_confirm.set_footer(text="You have 2 hours to use this card.")

            try:
                dm_channel = await user.create_dm()
                msg_card = await dm_channel.send(embed=embed_card)
                dm_channel_id = dm_channel.id
                dm_message_id = msg_card.id
                await dm_channel.send(embed=embed_confirm)
                print(f"✅ Confirmation DM sent to user {user_id}")
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
            embed = progress_embed(
                100,
                "✅ Card Delivered!",
                "Your Virtual Card has been sent to your DMs.\n"
                "Please check your messages and use the card within 2 hours.",
                color=discord.Color.green()
            )
            await edit_followup_embed(token, msg_id, embed)

        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            try:
                guild = admin_channel.guild
                seller_role = guild.get_role(SELLER_ROLE_ID)
                role_mention = seller_role.mention if seller_role else "@here"
                embed_warn = discord.Embed(
                    title="⚠️ VC DISPENSED – USE WITHIN 2 HOURS",
                    description=f"Card: `{vc_data['card']}`\nExpiry: `{vc_data['expiry']}`\nUser: <@{user_id}>\nExpires at {expiry_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    color=discord.Color.orange()
                )
                await admin_channel.send(content=f"{role_mention}", embed=embed_warn)
                print("✅ Admin alert sent")
            except discord.Forbidden:
                print(f"❌ No permission to send admin alert in channel {ADMIN_CHANNEL_ID} – check bot permissions.")
            except Exception as e:
                print(f"❌ Failed to send admin alert: {e}")
        else:
            print(f"❌ Admin channel {ADMIN_CHANNEL_ID} not found.")

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
            description=f"You sent **£{amount}** but the required amount is **£1.00**.\n\n"
                        "Your purchase has been cancelled. Please try again with the correct amount.",
            color=discord.Color.red()
        )
        await edit_followup_embed(token, msg_id, embed)

    try:
        user = await bot.fetch_user(user_id)
        if user:
            embed = discord.Embed(
                title="⚠️ Wrong Payment Amount",
                description=(
                    f"You sent **£{amount}** but the required amount is **£1.00**.\n\n"
                    "Your purchase has been cancelled. Please send the correct amount (£1.00) to proceed."
                ),
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
                description=(
                    f"**Payer Email:** {payer_email}\n"
                    f"**Transaction ID:** {txn_id}\n"
                    f"**Amount Received:** £{amount}\n"
                    f"**Expected:** £1.00\n"
                    f"User: <@{user_id}>\n"
                    "Payment ignored and user notified."
                ),
                color=discord.Color.red()
            )
            await admin_channel.send(content=f"📢 <@&{SELLER_ROLE_ID}>", embed=embed)
        except:
            pass

# --------------------------
# VC System: VC Management Panel (Add/View/Remove Cards)
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
    confirm = ui.TextInput(
        label="Type 'CONFIRM' to delete all cards",
        placeholder="CONFIRM",
        required=True
    )
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

            # Basic validation
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
# VC System: Purchase flow (Store View)
# --------------------------
class StoreView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="💳 Purchase Virtual Card", style=discord.ButtonStyle.primary, emoji="✨", row=0)
    async def buy(self, interaction: discord.Interaction, button: ui.Button):
        cards = load_vc_pool()
        if not cards:
            await interaction.response.send_message(
                "❌ **Error: Unable to purchase** – there is no stock available at the moment. Please wait for restock.",
                ephemeral=True
            )
            return
        embed = discord.Embed(
            title="📋 Terms & Conditions",
            description=(
                "By purchasing a Virtual Card, you agree to the following:\n\n"
                "**1.** You must use the Virtual Card within **2 hours** of receiving it.\n"
                "**2.** After 2 hours, the card will be terminated and you will no longer be able to use it.\n"
                "**3.** **No chargebacks** – if a chargeback is issued, necessary action will be taken.\n"
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
                "**Step-by-Step Guide:**\n\n"
                "**1️⃣ Click the 'Purchase Virtual Card' button**\n"
                "**2️⃣ Read and agree to the Terms & Conditions**\n"
                "**3️⃣ Enter your PayPal email** – this must match the email you'll send from.\n"
                "**4️⃣ Send exactly £1.00** to the PayPal address shown.\n"
                "   – Copy the email and paste it into PayPal.\n"
                "   – **DO NOT use PayPal.me – it doesn't work reliably.**\n"
                "**5️⃣ Wait for automatic confirmation** – your card will be sent to DMs.\n"
                "**6️⃣ Use the card within 2 hours** – it will be terminated after that.\n\n"
                "⚠️ Double-check your email and amount before sending!"
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
            f"Your email has been recorded.\n\n"
            f"**Send £1.00 to:** `{PAYPAL_EMAIL}`\n"
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

def get_online_orderer(guild):
    for member in guild.members:
        if member.bot:
            continue
        for role in member.roles:
            if role.id == SELLER_ROLE_ID:  # Using SELLER_ROLE_ID as Orderer
                if member.status != discord.Status.offline:
                    return member
    return None

async def create_ticket_channel(guild, category_name, user, staff, ticket_type, order_data=None):
    category = get_category(guild, category_name)
    if not category:
        print(f"❌ Category '{category_name}' not found.")
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
    channel = await category.create_text_channel(name=ticket_name, overwrites=overwrites)

    embed = discord.Embed(
        title=f"📋 {ticket_type.title()} Ticket",
        description=f"**User:** {user.mention}\n**Staff:** {staff.mention if staff else 'None assigned yet'}",
        color=discord.Color.blue()
    )
    if order_data:
        embed.add_field(name="📦 Order Details", value=order_data, inline=False)
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
# Ticket System: TicketView (buttons inside tickets)
# --------------------------
class TicketView(ui.View):
    def __init__(self, channel_id, user_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user_id = user_id

    @ui.button(label="✅ Order Completed", style=discord.ButtonStyle.success, emoji="✅", custom_id="complete_order")
    async def complete_order(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.user_id and not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("❌ You don't have permission to close this ticket.", ephemeral=True)
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
        await channel.send("🔒 This ticket has been closed and archived (Order Completed).")

    @ui.button(label="❌ Close Ticket", style=discord.ButtonStyle.danger, emoji="❌", custom_id="close_ticket")
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
# Ticket System: ReviewView (Approve/Reject)
# --------------------------
class ReviewView(ui.View):
    def __init__(self, user_id, order_data, staff_id):
        super().__init__(timeout=600)
        self.user_id = user_id
        self.order_data = order_data
        self.staff_id = staff_id

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
            self.order_data
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
# Ticket System: Dropdown & Views
# --------------------------
class TicketDropdown(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="📦 Order", value="orders", description="Open an order ticket"),
            discord.SelectOption(label="💬 General Support", value="general", description="Open a general support ticket")
        ]
        super().__init__(placeholder="Select a ticket type...", options=options, custom_id="ticket_dropdown")

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if selected == "orders":
            guild = interaction.guild
            staff = get_online_orderer(guild)
            if not staff:
                await interaction.response.send_message("❌ No orderers are currently online. Please try again later.", ephemeral=True)
                return

            await interaction.response.send_message(
                f"✅ An orderer ({staff.mention}) is available! I've sent you a DM to collect order details.",
                ephemeral=True
            )

            try:
                await interaction.user.send(
                    f"📦 **Order Process Started**\n\n"
                    f"Your orderer is: {staff.mention}\n\n"
                    f"Please reply to this DM with the following details:\n"
                    f"1. **Payment details** (VCC info, store, order total)\n"
                    f"2. **A screenshot** (attach as image)\n\n"
                    f"Once received, your order will be reviewed and a ticket will be created."
                )
                if not hasattr(bot, "order_cache"):
                    bot.order_cache = {}
                bot.order_cache[str(interaction.user.id)] = {
                    "staff_id": staff.id,
                    "step": "awaiting_details"
                }
            except discord.Forbidden:
                await interaction.followup.send("❌ I couldn't DM you. Please enable DMs from server members.", ephemeral=True)

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

class TicketViewDropdown(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown())

# --------------------------
# Ticket System: DM Handler
# --------------------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if not isinstance(message.channel, discord.DMChannel):
        await bot.process_commands(message)
        return

    if not hasattr(bot, "order_cache"):
        bot.order_cache = {}

    user_id_str = str(message.author.id)
    if user_id_str not in bot.order_cache:
        return

    cache = bot.order_cache[user_id_str]
    if cache["step"] == "awaiting_details":
        if message.attachments:
            attachment_url = message.attachments[0].url
            order_data = f"**Order Details from {message.author.display_name}:**\n\n**Details:** {cache.get('details', 'Not provided')}\n**Screenshot:** {attachment_url}"
        else:
            cache["details"] = message.content
            bot.order_cache[user_id_str] = cache
            await message.channel.send("📝 Details recorded. Please send a **screenshot** to confirm (attach as image).")
            return

        staff_id = cache["staff_id"]
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            await message.channel.send("❌ Error: Guild not found.")
            return

        review_channel = guild.get_channel(ADMIN_CHANNEL_ID)  # Using ADMIN_CHANNEL_ID as review
        if not review_channel:
            await message.channel.send("❌ Error: Review channel not found.")
            return

        order_data_full = f"**User:** {message.author.mention}\n**Details:** {cache.get('details', 'Not provided')}\n**Screenshot:** [View]({attachment_url if message.attachments else 'None'})"

        embed = discord.Embed(
            title="📦 New Order Review",
            description=order_data_full,
            color=discord.Color.blue()
        )
        embed.set_footer(text="Orderer will be assigned automatically.")

        view = ReviewView(message.author.id, order_data_full, staff_id)
        await review_channel.send(content=f"📢 <@&{SELLER_ROLE_ID}>", embed=embed, view=view)

        await message.channel.send("✅ Your order has been sent for review! You'll be notified once approved or rejected.")
        del bot.order_cache[user_id_str]

# --------------------------
# Ticket System: Admin Commands
# --------------------------
@bot.command(name="add_orderer")
@commands.has_permissions(administrator=True)
async def add_orderer(ctx, member: discord.Member):
    role = ctx.guild.get_role(SELLER_ROLE_ID)
    if not role:
        await ctx.send("❌ Orderer role not found. Please create it first.")
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
# VC System: Purge/Nuke commands (keep from original)
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
# VC System: VC Management Panel setup command
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

# --------------------------
# Background Tasks: VC Expiry Watcher & Timer
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

# --------------------------
# Background Task: Auto-close inactive tickets
# --------------------------
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
# Flask IPN Server (for PayPal)
# --------------------------
app_flask = Flask(__name__)

@app_flask.route("/test", methods=["GET"])
def test():
    return "✅ Flask server is running!", 200

@app_flask.route("/ipn", methods=["POST"])
def ipn():
    data = request.form.to_dict()
    print("📥 IPN received!")
    print("📋 Full data:", data)

    payment_status = data.get("payment_status")
    payer_email = data.get("payer_email", "").strip().lower()
    txn_id = data.get("txn_id")
    amount = data.get("mc_gross") or data.get("amount") or "0.00"
    print(f"🔑 Payment Status: {payment_status}")
    print(f"🔑 Payer Email: {payer_email}")
    print(f"🔑 Transaction ID: {txn_id}")
    print(f"🔑 Amount: {amount}")

    verify_url = "https://www.paypal.com/cgi-bin/webscr"
    verify_data = data.copy()
    verify_data["cmd"] = "_notify-validate"
    try:
        resp = requests.post(verify_url, data=verify_data, timeout=10)
        print(f"✅ PayPal verification: {resp.text[:50]}...")
    except Exception as e:
        print(f"❌ Verification failed: {e}")
        return "Verification failed", 500

    if resp.text == "VERIFIED":
        print("✅ IPN verified")
        if payment_status == "Completed":
            try:
                amt = float(amount)
            except:
                amt = 0.0
            if amt != 1.00:
                print(f"⚠️ Amount is {amount}, not £1.00 – handling wrong amount.")
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
                    # fallback: send admin alert
                    admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
                    if admin_channel:
                        try:
                            embed = discord.Embed(
                                title="⚠️ Wrong Payment Amount (No Pending Match)",
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
                    embed = progress_embed(
                        50,
                        "💰 Payment Received!",
                        "Your payment has been confirmed.\nWe're now preparing your Virtual Card – please wait.",
                        color=discord.Color.blue()
                    )
                    asyncio.run_coroutine_threadsafe(
                        edit_followup_embed(token, msg_id, embed),
                        bot.loop
                    )

                asyncio.run_coroutine_threadsafe(dispense_vc(matched_user_id, token, msg_id), bot.loop)
                return "OK", 200
            else:
                print(f"❌ No pending purchase found for email: {payer_email}")
                admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
                if admin_channel:
                    try:
                        embed = discord.Embed(
                            title="⚠️ Unmatched Payment",
                            description=f"**Email:** {payer_email}\n**Amount:** £{amount}\n**TXID:** {txn_id}\nNo matching pending purchase.",
                            color=discord.Color.orange()
                        )
                        asyncio.run_coroutine_threadsafe(
                            admin_channel.send(content=f"📢 <@&{SELLER_ROLE_ID}>", embed=embed),
                            bot.loop
                        )
                    except:
                        pass
                return "OK - Unmatched", 200
        else:
            print(f"📌 Status: {payment_status} – not dispensing")
            return f"OK - {payment_status}", 200
    else:
        print("❌ IPN verification failed")
        return "Verification failed", 400

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

    # Start background tasks
    bot.loop.create_task(expiry_watcher())
    bot.loop.create_task(timer_updater())
    if INACTIVE_CLOSE_HOURS > 0:
        auto_close_inactive.start()
        print(f"🔄 Auto-close enabled: {INACTIVE_CLOSE_HOURS} hours inactivity.")

    # Post VC Store in STORE_CHANNEL_ID
    channel = bot.get_channel(STORE_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title="🛍️ Virtual Card Store",
            description="Click the button below to purchase a Virtual Card for **£1**.\n\n"
                        "After payment, the card is delivered automatically – no waiting.",
            color=discord.Color.gold()
        )
        embed.set_footer(text="Cards expire 2 hours after delivery.")
        try:
            await channel.send(embed=embed, view=StoreView())
            print(f"✅ VC Store posted in {channel.name}")
        except Exception as e:
            print(f"❌ Error posting VC Store: {e}")

    # Post VC Management Panel in ADMIN_CHANNEL_ID
    admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if admin_channel:
        embed = discord.Embed(
            title="💳 VC Management Panel",
            description="Use the buttons below to manage your VC stock.",
            color=discord.Color.purple()
        )
        try:
            await admin_channel.send(embed=embed, view=VCPanelView())
            print(f"✅ VC Management Panel posted in {admin_channel.name}")
        except Exception as e:
            print(f"❌ Error posting VC Management Panel: {e}")

    # Post Ticket Dropdown in TICKET_CHANNEL_ID
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

    print("✅ Bot is fully ready.")

# --------------------------
# Main runner
# --------------------------
if __name__ == "__main__":
    if not TOKEN:
        print("❌ BOT_TOKEN not set in environment variables.")
    else:
        # Start Flask in a separate thread for PayPal IPN
        threading.Thread(target=run_flask, daemon=True).start()
        bot.run(TOKEN)
