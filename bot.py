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

# ----- Dispense VC -----
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

    active = load_active()
    active[vc] = {
        "user_id": user_id,
        "expires_at": expiry_str,
        "dm_channel_id": dm_channel_id,
        "dm_message_id": dm_message_id
    }
    save_active(active)

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

# ----- Send unmatched alert to admin channel (async) -----
async def send_unmatched_alert(payer_email: str, txn_id: str):
    admin_channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if admin_channel:
        embed = discord.Embed(
            title="⚠️ Unmatched Payment – Manual Review Needed",
            description=(
                f"**Payer Email:** {payer_email}\n"
                f"**Transaction ID:** {txn_id}\n"
                f"**Status:** Completed\n"
                f"**This payment didn't match any pending purchase.**\n\n"
                f"Please check your PayPal and manually dispense a card to this user if valid."
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
        purchase_id = f"{interaction.user.id}-{secrets.token_hex(4)}"
        pending = load_pending()
        pending[purchase_id] = {
            "user_id": interaction.user.id,
            "payer_email": self.email.value.strip().lower()
        }
        save_pending(pending)

        paypal_username = PAYPAL_EMAIL.split('@')[0]

        embed = discord.Embed(
            title="💳 Complete Your Payment",
            description=(
                f"**1.** Send **£1** to PayPal: `{PAYPAL_EMAIL}`\n"
                f"**2.** Click here: [PayPal.me/{paypal_username}](https://paypal.me/{paypal_username}/1)\n\n"
                f"**3.** That's it – the bot will detect your payment and deliver the card automatically."
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="Make sure you send from the email you just entered.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
    print(f"🔑 Payment Status: {payment_status}")
    print(f"🔑 Payer Email: {payer_email}")
    print(f"🔑 Transaction ID: {txn_id}")

    # Verify IPN
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
            print("✅ Payment Completed")

            # Try to match by email
            pending = load_pending()
            matched_purchase_id = None
            matched_user_id = None

            for purchase_id, data in pending.items():
                if data.get("payer_email", "").strip().lower() == payer_email:
                    matched_purchase_id = purchase_id
                    matched_user_id = data.get("user_id")
                    break

            if matched_purchase_id and matched_user_id:
                print(f"✅ Found matching email: {payer_email} -> User {matched_user_id}")
                del pending[matched_purchase_id]
                save_pending(pending)
                asyncio.run_coroutine_threadsafe(dispense_vc(matched_user_id), bot.loop)
                return "OK", 200
            else:
                print(f"❌ No pending purchase found for email: {payer_email}")
                print("📌 Sending manual approval request to admin channel...")
                # Schedule the async alert
                asyncio.run_coroutine_threadsafe(
                    send_unmatched_alert(payer_email, txn_id),
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
