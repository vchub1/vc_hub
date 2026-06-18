import discord
from discord import ui
import asyncio
import json
import os
from datetime import datetime, timedelta
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

VC_FILE = "vcs.txt"
PENDING_FILE = "pending.json"
ACTIVE_FILE = "active.json"

# ----------------------------

# ----- File helpers -----
def load_vc_pool():
    if not os.path.exists(VC_FILE): return []
    with open(VC_FILE, "r") as f:
        return [line.strip() for line in f if line.strip()]

def save_vc_pool(pool):
    with open(VC_FILE, "w") as f:
        f.write("\n".join(pool))

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

# ----- Discord Bot -----
intents = discord.Intents.default()
intents.members = True
bot = discord.Client(intents=intents)

# ----- Dispense VC (called by IPN) -----
async def dispense_vc(user_id: int):
    pool = load_vc_pool()
    if not pool:
        admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            await admin_channel.send("🚨 **No VCs left!** Refill `vcs.txt`.")
        return False

    vc = pool.pop()
    save_vc_pool(pool)

    expiry_time = datetime.utcnow() + timedelta(hours=2)
    expiry_str = expiry_time.isoformat()

    # DM user with card + live timer
    user = await bot.fetch_user(user_id)
    dm_channel_id = None
    dm_message_id = None
    if user:
        embed = discord.Embed(
            title="✨ Your Virtual Card",
            description=f"```\n{vc}\n```",
            color=discord.Color.green()
        )
        embed.add_field(name="⏰ Time remaining", value="2 hours (updates live)", inline=False)
        embed.set_footer(text="This card will be terminated after 2 hours.")
        try:
            dm_channel = await user.create_dm()
            msg = await dm_channel.send(embed=embed)
            dm_channel_id = dm_channel.id
            dm_message_id = msg.id
        except:
            pass

    # Save active
    active = load_active()
    active[vc] = {
        "user_id": user_id,
        "expires_at": expiry_str,
        "dm_channel_id": dm_channel_id,
        "dm_message_id": dm_message_id
    }
    save_active(active)

    # Ping seller role with the card
    admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if admin_channel:
        guild = admin_channel.guild
        seller_role = guild.get_role(SELLER_ROLE_ID)
        role_mention = seller_role.mention if seller_role else "@here"
        embed_warn = discord.Embed(
            title="⚠️ VC DISPENSED – USE WITHIN 2 HOURS",
            description=f"Card: `{vc}`\nUser: <@{user_id}>\nExpires at {expiry_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            color=discord.Color.orange()
        )
        await admin_channel.send(content=f"{role_mention}", embed=embed_warn)

    return True

# ----- Expiry watcher -----
async def expiry_watcher():
    await bot.wait_until_ready()
    while not bot.is_closed():
        active = load_active()
        expired = []
        for vc, data in active.items():
            exp = datetime.fromisoformat(data["expires_at"])
            if datetime.utcnow() >= exp:
                admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
                if admin_channel:
                    seller_role = admin_channel.guild.get_role(SELLER_ROLE_ID)
                    role_mention = seller_role.mention if seller_role else "@here"
                    await admin_channel.send(
                        f"⏰ {role_mention} **TERMINATE THIS VC NOW!**\n"
                        f"Card: `{vc}`\nUser: <@{data['user_id']}>\nExpired at {exp.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                    )
                expired.append(vc)
        if expired:
            for vc in expired:
                del active[vc]
            save_active(active)
        await asyncio.sleep(60)

# ----- Live timer updater -----
async def timer_updater():
    await bot.wait_until_ready()
    while not bot.is_closed():
        active = load_active()
        for vc, data in active.items():
            exp = datetime.fromisoformat(data["expires_at"])
            remaining = exp - datetime.utcnow()
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
                            embed.set_field_at(0, name="⏰ Time remaining", value=f"{hours}h {minutes}m {seconds}s", inline=False)
                            await msg.edit(embed=embed)
                except:
                    pass
        await asyncio.sleep(30)

# ----- UI: Store button -----
class StoreView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="✨ Purchase Virtual Card", style=discord.ButtonStyle.primary, emoji="💳", custom_id="buy")
    async def buy(self, interaction: discord.Interaction, button: ui.Button):
        modal = BuyModal()
        await interaction.response.send_modal(modal)

class BuyModal(ui.Modal, title="💳 Purchase VC"):
    email = ui.TextInput(label="Your PayPal Email", placeholder="The email you'll use to pay", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        invoice_id = f"VC-{interaction.user.id}-{secrets.token_hex(6)}"
        pending = load_pending()
        pending[invoice_id] = str(interaction.user.id)
        save_pending(pending)

        # Extract the username from the PayPal email (for PayPal.me link)
        paypal_username = PAYPAL_EMAIL.split('@')[0]

        embed = discord.Embed(
            title="💳 Complete Your Payment",
            description=(
                f"**1.** Click the link below to pay **£1** via PayPal:\n"
                f"[Pay Now](https://paypal.me/{paypal_username}/1)\n\n"
                f"**2.** **IMPORTANT:** In the payment **note/message**, paste this code:\n"
                f"`{invoice_id}`\n\n"
                "**3.** After sending, the card will be automatically delivered to your DMs within 1 minute."
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="Your invoice code is unique – don't share it.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ----- Flask IPN Server (Auto-dispense) -----
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
    invoice_id = data.get("custom")
    txn_id = data.get("txn_id")
    payer_email = data.get("payer_email")
    print(f"🔑 Payment Status: {payment_status}")
    print(f"🔑 Invoice ID (custom): {invoice_id}")
    print(f"🔑 Transaction ID: {txn_id}")
    print(f"🔑 Payer Email: {payer_email}")

    # Verify IPN with PayPal
    verify_url = "https://www.paypal.com/cgi-bin/webscr"
    verify_data = data.copy()
    verify_data["cmd"] = "_notify-validate"
    try:
        resp = requests.post(verify_url, data=verify_data, timeout=10)
        print(f"✅ PayPal verification response: {resp.text[:100]}...")
    except Exception as e:
        print(f"❌ Verification request failed: {e}")
        return "Verification failed", 500

    if resp.text == "VERIFIED":
        print("✅ IPN verified successfully")
        if payment_status == "Completed":
            print("✅ Payment status: Completed")
            if invoice_id and invoice_id in load_pending():
                pending = load_pending()
                user_id_str = pending.pop(invoice_id)
                save_pending(pending)
                user_id = int(user_id_str)
                asyncio.run_coroutine_threadsafe(dispense_vc(user_id), bot.loop)
                return "OK", 200
            else:
                print(f"❌ Invoice ID '{invoice_id}' not found in pending")
                return "Invoice not found", 404
        else:
            print(f"📌 Payment status is '{payment_status}' – not dispensing")
            return f"OK - Status: {payment_status}", 200
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
            description=(
                "Click **Purchase Virtual Card** to buy a card for **£1**.\n"
                "After payment, the card is delivered automatically – no waiting."
            ),
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

    bot.loop.create_task(expiry_watcher())
    bot.loop.create_task(timer_updater())

# ----- TEST: Simple ping command -----
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.content == "!ping":
        await message.channel.send("Pong! Bot can send messages.")

# ----- Main -----
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)
