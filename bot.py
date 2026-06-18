import discord
from discord import ui
from discord.ext import commands
import asyncio
import json
import os
from datetime import datetime, timedelta, UTC
import secrets
import requests
from flask import Flask, request
import threading
from dotenv import load_dotenv
load_dotenv()

# ---------- CONFIG ----------
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))
SELLER_ROLE_ID = int(os.getenv("SELLER_ROLE_ID"))
STORE_CHANNEL_ID = int(os.getenv("STORE_CHANNEL_ID"))
PAYPAL_EMAIL = os.getenv("PAYPAL_EMAIL")
PORT = int(os.getenv("PORT", 5000))

VC_FILE = "vcs.json"
PENDING_FILE = "pending.json"
ACTIVE_FILE = "active.json"

# ----------------------------

def load_vc_pool():
    if not os.path.exists(VC_FILE):
        save_vc_pool([])
        return []
    try:
        with open(VC_FILE, "r") as f:
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
    with open(VC_FILE, "w") as f:
        json.dump({"cards": cards}, f, indent=4)

def load_pending():
    if not os.path.exists(PENDING_FILE): return {}
    with open(PENDING_FILE, "r") as f:
        return json.load(f)

def save_pending(data):
    with open(PENDING_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_active():
    if not os.path.exists(ACTIVE_FILE): return {}
    with open(ACTIVE_FILE, "r") as f:
        return json.load(f)

def save_active(data):
    with open(ACTIVE_FILE, "w") as f:
        json.dump(data, f, indent=4)

# ----- Discord Bot Setup -----
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ----- Helper to edit ephemeral followup -----
async def edit_followup(token: str, msg_id: int, content: str, embed=None):
    """Edit an ephemeral followup message using the interaction token."""
    url = f"https://discord.com/api/v10/webhooks/{bot.user.id}/{token}/messages/{msg_id}"
    payload = {"content": content}
    if embed:
        payload["embeds"] = [embed.to_dict()]
    try:
        resp = requests.patch(url, json=payload)
        if resp.status_code == 200:
            print("✅ Updated ephemeral followup")
        else:
            print(f"❌ Failed to update followup: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"❌ Error updating followup: {e}")

# ----- Purge/Nuke Commands -----
@bot.command(name="purge")
@commands.has_permissions(manage_messages=True)
async def purge(ctx, amount: int = None):
    if amount is None:
        await ctx.send("❌ Please specify a number. Example: `!purge 50`", delete_after=5)
        return
    if amount < 1:
        await ctx.send("❌ Amount must be at least 1.", delete_after=5)
        return
    if amount > 1000:
        await ctx.send("❌ Can't delete more than 1000 messages.", delete_after=5)
        return
    try:
        deleted = await ctx.channel.purge(limit=amount + 1)
        msg = await ctx.send(f"✅ Deleted {len(deleted) - 1} messages.")
        await asyncio.sleep(3)
        await msg.delete()
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to delete messages.", delete_after=5)
    except Exception as e:
        await ctx.send(f"❌ Error: {e}", delete_after=5)

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
    except Exception as e:
        await ctx.send(f"❌ Error: {e}", delete_after=5)

# ----- Validation Helpers -----
def clean_card_number(raw: str) -> str:
    return ''.join(filter(str.isdigit, raw))

def validate_card(card: str) -> bool:
    cleaned = clean_card_number(card)
    return cleaned.isdigit() and 12 <= len(cleaned) <= 19

def validate_expiry(expiry: str) -> bool:
    cleaned = expiry.strip().replace('/', '').replace('-', '').replace(' ', '')
    if len(cleaned) != 4:
        return False
    month = cleaned[:2]
    year = cleaned[2:]
    if not month.isdigit() or not year.isdigit():
        return False
    m = int(month)
    y = int(year)
    if m < 1 or m > 12:
        return False
    if y < 24 or y > 99:
        return False
    return True

def validate_cvv(cvv: str) -> bool:
    cleaned = cvv.strip()
    return cleaned.isdigit() and 3 <= len(cleaned) <= 4

def format_expiry(expiry: str) -> str:
    cleaned = expiry.strip().replace('/', '').replace('-', '').replace(' ', '')
    if len(cleaned) == 4:
        return f"{cleaned[:2]}/{cleaned[2:]}"
    return expiry

# ----- Clear All Confirmation Modal -----
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

# ----- Admin VC Management Panel -----
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

class AddCardModal(ui.Modal, title="➕ Add Virtual Card"):
    card_number = ui.TextInput(
        label="Card Number (max 19 digits)",
        placeholder="1234567890123456789",
        required=True,
        max_length=19
    )
    expiry = ui.TextInput(
        label="Expiry Date (MM/YY)",
        placeholder="11/30",
        required=True,
        max_length=5
    )
    cvv = ui.TextInput(
        label="CVV (3-4 digits)",
        placeholder="123",
        required=True,
        max_length=4
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            raw_card = self.card_number.value.strip()
            raw_expiry = self.expiry.value.strip()
            raw_cvv = self.cvv.value.strip()

            print(f"📝 Add Card: {raw_card} | {raw_expiry} | {raw_cvv}")

            cleaned_card = clean_card_number(raw_card)
            if not cleaned_card.isdigit() or not (12 <= len(cleaned_card) <= 19):
                await interaction.response.send_message(
                    f"❌ Invalid card – must be 12-19 digits. You entered: `{raw_card}`",
                    ephemeral=True
                )
                return

            if not validate_expiry(raw_expiry):
                await interaction.response.send_message(
                    f"❌ Invalid expiry – use MM/YY (e.g., 11/30). You entered: `{raw_expiry}`",
                    ephemeral=True
                )
                return

            if not validate_cvv(raw_cvv):
                await interaction.response.send_message(
                    f"❌ Invalid CVV – must be 3-4 digits. You entered: `{raw_cvv}`",
                    ephemeral=True
                )
                return

            formatted_card = cleaned_card
            formatted_expiry = format_expiry(raw_expiry)
            formatted_cvv = raw_cvv.strip()

            cards = load_vc_pool()
            cards.append({
                "card": formatted_card,
                "expiry": formatted_expiry,
                "cvv": formatted_cvv
            })
            save_vc_pool(cards)

            print(f"✅ Card added: {formatted_card} | {formatted_expiry} | {formatted_cvv}")

            await interaction.response.send_message(
                f"✅ Card added: `{formatted_card} | Exp: {formatted_expiry} | CVV: {formatted_cvv}`",
                ephemeral=True
            )

        except Exception as e:
            print(f"❌ AddCardModal error: {e}")
            try:
                await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
            except:
                pass

class RemoveCardModal(ui.Modal, title="🗑️ Remove Card"):
    index = ui.TextInput(
        label="Card Number (1‑based)",
        placeholder="Enter the card number to remove",
        required=True
    )

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

# ----- Setup VC Management Panel -----
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

# ----- Dispense VC (with followup edit) -----
async def dispense_vc(user_id: int, token: str = None, msg_id: int = None):
    cards = load_vc_pool()
    if not cards:
        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            await admin_channel.send("🚨 **No VCs left!** Use `!setup_vcpanel` to add more.")
        return False

    vc_data = cards.pop(0)
    save_vc_pool(cards)

    expiry_time = datetime.now(UTC) + timedelta(hours=2)
    expiry_str = expiry_time.isoformat()

    user = await bot.fetch_user(user_id)
    dm_channel_id = None
    dm_message_id = None
    if user:
        # Card details embed
        embed_card = discord.Embed(
            title="✨ Your Virtual Card",
            description=f"**Card:** `{vc_data['card']}`\n**Expiry:** `{vc_data['expiry']}`\n**CVV:** `{vc_data['cvv']}`",
            color=discord.Color.green()
        )
        embed_card.add_field(name="⏰ Time Remaining", value="2 hours (updates live)", inline=False)
        embed_card.set_footer(text="This card will be terminated after 2 hours.")

        # Thank you embed (DM)
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

    # Update the ephemeral followup message in the channel
    if token and msg_id:
        await edit_followup(
            token,
            msg_id,
            "✅ **Thank you for your order!**\nPlease check your DMs for your card details."
        )

    admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if admin_channel:
        guild = admin_channel.guild
        seller_role = guild.get_role(SELLER_ROLE_ID)
        role_mention = seller_role.mention if seller_role else "@here"
        embed_warn = discord.Embed(
            title="⚠️ VC DISPENSED – USE WITHIN 2 HOURS",
            description=f"Card: `{vc_data['card']}`\nExpiry: `{vc_data['expiry']}`\nUser: <@{user_id}>\nExpires at {expiry_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            color=discord.Color.orange()
        )
        await admin_channel.send(content=f"{role_mention}", embed=embed_warn)

    return True

# ----- Send unmatched alert -----
async def send_unmatched_alert(payer_email: str, txn_id: str, amount: str):
    admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if admin_channel:
        embed = discord.Embed(
            title="⚠️ Unmatched Payment – Manual Review",
            description=(
                f"**Payer Email:** {payer_email}\n"
                f"**Transaction ID:** {txn_id}\n"
                f"**Amount:** £{amount}\n"
                f"**Status:** Completed\n"
                f"This payment didn't match any pending purchase."
            ),
            color=discord.Color.orange()
        )
        await admin_channel.send(content=f"📢 <@&{SELLER_ROLE_ID}>", embed=embed)

# ----- Expiry watcher -----
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
                    seller_role = admin_channel.guild.get_role(SELLER_ROLE_ID)
                    role_mention = seller_role.mention if seller_role else "@here"
                    await admin_channel.send(
                        f"⏰ {role_mention} **TERMINATE THIS VC NOW!**\n"
                        f"Card: `{card}`\nUser: <@{data['user_id']}>\nExpired at {exp.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                    )
                expired.append(card)
        if expired:
            for card in expired:
                del active[card]
            save_active(active)
        await asyncio.sleep(60)

# ----- Timer updater -----
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

# ----- Purchase Flow (with T&C) -----
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
    email = ui.TextInput(
        label="Your PayPal Email",
        placeholder="The email you'll use to pay",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        purchase_id = f"{interaction.user.id}-{secrets.token_hex(4)}"
        pending = load_pending()
        pending[purchase_id] = {
            "user_id": interaction.user.id,
            "payer_email": self.email.value.strip().lower()
        }
        save_pending(pending)

        # Send the initial "processing" followup and store its ID and token
        await interaction.response.defer(ephemeral=True, thinking=False)  # Defer to send followup later
        followup_msg = await interaction.followup.send(
            content="⏳ **Processing your payment...**\nWe're waiting for PayPal to confirm your payment.",
            ephemeral=True
        )
        # Store the followup message ID and token in pending
        pending[purchase_id]["followup_msg_id"] = followup_msg.id
        pending[purchase_id]["followup_token"] = interaction.token
        save_pending(pending)

        paypal_username = PAYPAL_EMAIL.split('@')[0]

        # Now send the payment instructions in a separate followup or the same? The user already sees the processing message. But we also need to give them the payment link. We can edit the followup to include instructions, but we want it to update later to thank you. So we'll send a second message? Actually we can combine: initially send a message with instructions, then later edit it to thank you. But we can't edit the content if we want to keep instructions. So we can send the instructions as a separate ephemeral message, or include them in the initial and then edit to thank you. The user wants the message to change from "send £1" to "thank you". So we should initially send the payment instructions, then later edit to thank you.

        # We'll edit the followup to include instructions after defer.
        await interaction.edit_original_response(
            content=f"💳 **Complete Your Payment**\n\n"
                    f"**1.** Send **£1** to PayPal: `{PAYPAL_EMAIL}`\n"
                    f"**2.** Click here: [PayPal.me/{paypal_username}](https://paypal.me/{paypal_username}/1)\n\n"
                    f"**3.** After payment, this message will update automatically."
        )
        # But we already sent a followup, we need to edit that instead. Let's store the message ID and use edit_followup later. Actually we can use interaction.edit_original_response to edit the initial response (since we deferred). That's easier.

        # Let's redo: we defer, send an ephemeral message with the instructions (which will be the one we edit later). We don't need a separate followup.
        # We'll just use the original response.

        # But we already used await interaction.response.defer(ephemeral=True, thinking=False) and then sent the followup. We can edit the original response with interaction.edit_original_response, but that's for the original reply, not the followup. We can just send the initial message as the instructions and later edit it.

        # Let's simplify: Instead of deferring, we send a modal response that includes the instructions. But we need the message to be editable later. So we'll send an ephemeral message after defer.

        # I'll restructure: In on_submit, we defer, then send an ephemeral message with instructions, and store its ID and token. Then later we edit it.

        # The above code already does that: it sends a followup with the "processing" message, but we want the instructions. So we'll change the followup to include instructions.

        # Actually we can just send the instructions as the followup, and later edit it.

        # Let's remove the "processing" and just send the instructions directly, then later edit to thank you.

        # I'll rewrite the on_submit to send the instructions as the followup, and store its ID.

        # Since we already deferred, we can send the followup with instructions.

        await interaction.followup.edit(
            message_id=followup_msg.id,
            content=f"💳 **Complete Your Payment**\n\n"
                    f"**1.** Send **£1** to PayPal: `{PAYPAL_EMAIL}`\n"
                    f"**2.** Click here: [PayPal.me/{paypal_username}](https://paypal.me/{paypal_username}/1)\n\n"
                    f"**3.** After payment, this message will update automatically."
        )

        # But we already sent the "processing" message; we can edit it to instructions.
        # We stored the followup_msg.id and token, so we can edit later.

        # Actually, we can just send the instructions as the initial followup and not send a separate "processing" message.
        # Let's just send the instructions directly, and store the message ID for later editing.

        # We'll change the code: after defer, we send the instructions as a followup, store its ID and token.

        # But we already have the processing message sent. We can edit that to instructions now, and then later edit to thank you.

        await interaction.followup.edit(
            message_id=followup_msg.id,
            content=f"💳 **Complete Your Payment**\n\n"
                    f"**1.** Send **£1** to PayPal: `{PAYPAL_EMAIL}`\n"
                    f"**2.** Click here: [PayPal.me/{paypal_username}](https://paypal.me/{paypal_username}/1)\n\n"
                    f"**3.** After payment, this message will update automatically."
        )

        # Now store the token and msg_id in pending
        pending[purchase_id]["followup_msg_id"] = followup_msg.id
        pending[purchase_id]["followup_token"] = interaction.token
        save_pending(pending)

        # Done, the user sees the instructions, and we'll edit later.

# ----- Store Button -----
class StoreView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="💳 Purchase Virtual Card", style=discord.ButtonStyle.primary, emoji="✨", custom_id="buy")
    async def buy(self, interaction: discord.Interaction, button: ui.Button):
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

# ----- Flask IPN Server -----
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
            if float(amount) != 1.00:
                print(f"⚠️ Amount is {amount}, not £1.00 – ignoring.")
                return f"OK - Amount {amount}", 200

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
                asyncio.run_coroutine_threadsafe(dispense_vc(matched_user_id, token, msg_id), bot.loop)
                return "OK", 200
            else:
                print(f"❌ No pending purchase found for email: {payer_email}")
                asyncio.run_coroutine_threadsafe(
                    send_unmatched_alert(payer_email, txn_id, amount),
                    bot.loop
                )
                return "OK - Unmatched", 200
        else:
            print(f"📌 Status: {payment_status} – not dispensing")
            return f"OK - {payment_status}", 200
    else:
        print("❌ IPN verification failed")
        return "Verification failed", 400

def run_flask():
    app_flask.run(host="0.0.0.0", port=PORT)

# ----- Bot events -----
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    
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
            print("✅ Store message sent successfully!")
        except discord.Forbidden:
            print("❌ Missing permissions to send the store embed.")
        except Exception as e:
            print(f"❌ Unexpected error sending store: {e}")
    else:
        print(f"❌ Store channel {STORE_CHANNEL_ID} not found.")

    panel_channel = bot.get_channel(1517019905696858273)
    if panel_channel:
        embed = discord.Embed(
            title="💳 VC Management Panel",
            description="Use the buttons below to manage your VC stock.",
            color=discord.Color.purple()
        )
        try:
            await panel_channel.send(embed=embed, view=VCPanelView())
            print("✅ VC Management Panel sent successfully!")
        except discord.Forbidden:
            print("❌ Missing permissions to send the VC Management Panel.")
        except Exception as e:
            print(f"❌ Unexpected error sending VC Management Panel: {e}")
    else:
        print(f"❌ VC Management channel 1517019905696858273 not found.")

    bot.loop.create_task(expiry_watcher())
    bot.loop.create_task(timer_updater())

# ----- Main -----
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)
