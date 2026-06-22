"""
Ticket System Bot – Full Automated Support & Order Ticket Manager
-------------------------------------------------------------
Features:
- Dropdown menu for Orders or General Support tickets.
- Orders: checks online staff, collects order details + screenshot via DM, sends to review channel, creates ticket on approval.
- General: instantly creates a ticket.
- Ticket management: "Order Completed" and "Close Ticket" buttons.
- Admin commands: add/remove orderers, ticket stats, ticket log, archive, claim.
- Auto‑posts the dropdown in your specified channel on startup.

Version: 1.0.0
"""

import discord
from discord import ui, app_commands
from discord.ext import commands, tasks
import asyncio
import json
import os
import re
from datetime import datetime, timedelta, UTC
import secrets
from dotenv import load_dotenv

# --------------------------
# Load environment variables
# --------------------------
load_dotenv()

# --------------------------
# Configuration
# --------------------------
TOKEN = os.getenv("TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
REVIEW_CHANNEL = int(os.getenv("REVIEW_CHANNEL"))
ORDERER_ROLE = int(os.getenv("ORDERER_ROLE"))
SUPPORT_ROLE = int(os.getenv("SUPPORT_ROLE"))
TICKET_CHANNEL_ID = 1511704776046280826  # The lobby where dropdown appears

# Category names – the bot finds them by name
CATEGORY_ORDERS_NAME = "Orders"
CATEGORY_GENERAL_NAME = "General"

# Ticket data file
TICKETS_FILE = "tickets.json"

# Auto-close inactive tickets after 24 hours (set to 0 to disable)
INACTIVE_CLOSE_HOURS = 24

# --------------------------
# Database helper functions
# --------------------------
def load_tickets():
    """
    Load the tickets database from the JSON file.
    Returns a dict with orders and general tickets, each with open/closed lists.
    """
    if not os.path.exists(TICKETS_FILE):
        return {"orders": {"open": [], "closed": []}, "general": {"open": [], "closed": []}}
    with open(TICKETS_FILE, "r") as f:
        return json.load(f)

def save_tickets(data):
    """
    Save the tickets database to the JSON file.
    """
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
# Helper Functions
# --------------------------
def get_category(guild, name):
    """
    Find a category by name (case‑insensitive).
    Returns the category object or None.
    """
    for cat in guild.categories:
        if cat.name.lower() == name.lower():
            return cat
    return None

def get_online_orderer(guild):
    """
    Find a member with the Orderer role who is currently online (not offline).
    Returns the first online Orderer or None.
    """
    for member in guild.members:
        if member.bot:
            continue
        for role in member.roles:
            if role.id == ORDERER_ROLE:
                if member.status != discord.Status.offline:
                    return member
    return None

async def create_ticket_channel(guild, category_name, user, staff, ticket_type, order_data=None):
    """
    Create a new ticket channel in the specified category.
    Returns the channel object or None if the category doesn't exist.
    """
    category = get_category(guild, category_name)
    if not category:
        print(f"❌ Category '{category_name}' not found. Please create it.")
        return None

    # Permissions
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    if staff:
        overwrites[staff] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    # For general tickets, also give the Support role access
    if ticket_type == "general":
        support_role = guild.get_role(SUPPORT_ROLE)
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    # Create the channel
    ticket_name = f"{ticket_type}-{user.name[:5]}-{secrets.token_hex(3)}"
    channel = await category.create_text_channel(name=ticket_name, overwrites=overwrites)

    # Send the initial embed with ticket info
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

    # Store in database
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
# Ticket Management View (buttons inside tickets)
# --------------------------
class TicketView(ui.View):
    """
    View with buttons for ticket management: Order Completed & Close Ticket.
    """
    def __init__(self, channel_id, user_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user_id = user_id

    @ui.button(label="✅ Order Completed", style=discord.ButtonStyle.success, emoji="✅", custom_id="complete_order")
    async def complete_order(self, interaction: discord.Interaction, button: ui.Button):
        """
        Closes the ticket as 'Order Completed' – only the ticket owner or staff can use it.
        """
        # Permission check
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

        # Lock the channel
        await channel.set_permissions(interaction.guild.default_role, read_messages=False)
        await channel.send("🔒 This ticket has been closed and archived (Order Completed).")

    @ui.button(label="❌ Close Ticket", style=discord.ButtonStyle.danger, emoji="❌", custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: ui.Button):
        """
        Closes the ticket – staff only.
        """
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
# Review View (Approve/Reject orders)
# --------------------------
class ReviewView(ui.View):
    """
    View with Approve/Reject buttons for order reviews.
    """
    def __init__(self, user_id, order_data, staff_id):
        super().__init__(timeout=600)  # 10 minutes to respond
        self.user_id = user_id
        self.order_data = order_data
        self.staff_id = staff_id

    @ui.button(label="✅ Approve Order", style=discord.ButtonStyle.success, emoji="✅")
    async def approve_order(self, interaction: discord.Interaction, button: ui.Button):
        """
        Approve the order – creates a ticket and notifies the user.
        """
        guild = interaction.guild
        orderer_role = guild.get_role(ORDERER_ROLE)
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
            # DM the user
            try:
                embed = discord.Embed(
                    title="✅ Order Approved",
                    description=f"Your order has been approved! Please check your ticket: {channel.mention}",
                    color=discord.Color.green()
                )
                await user.send(embed=embed)
            except:
                pass

            # Confirmation in review channel
            await interaction.channel.send(f"✅ Ticket created: {channel.mention} for {user.mention}")

    @ui.button(label="❌ Reject Order", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject_order(self, interaction: discord.Interaction, button: ui.Button):
        """
        Reject the order – notifies the user.
        """
        guild = interaction.guild
        orderer_role = guild.get_role(ORDERER_ROLE)
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
# Dropdown Menu
# --------------------------
class TicketDropdown(ui.Select):
    """
    Dropdown for selecting ticket type: Orders or General Support.
    """
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
                await interaction.response.send_message(
                    "❌ No orderers are currently online. Please try again later.",
                    ephemeral=True
                )
                return

            await interaction.response.send_message(
                f"✅ An orderer ({staff.mention}) is available! I've sent you a DM to collect order details.",
                ephemeral=True
            )

            # Start DM flow
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

        else:  # general support
            guild = interaction.guild
            await interaction.response.send_message("🔄 Creating your support ticket...", ephemeral=True)

            channel = await create_ticket_channel(
                guild,
                CATEGORY_GENERAL_NAME,
                interaction.user,
                None,  # No specific staff assigned
                "general",
                None
            )

            if channel:
                await interaction.followup.send(f"✅ Support ticket created: {channel.mention}", ephemeral=True)

# --------------------------
# Dropdown View (holds the dropdown)
# --------------------------
class TicketViewDropdown(ui.View):
    """
    The view that holds the dropdown menu – stays forever.
    """
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown())

# --------------------------
# DM Handler (for order details)
# --------------------------
@bot.event
async def on_message(message):
    """
    Handle direct messages for order details.
    """
    if message.author.bot:
        return
    if not isinstance(message.channel, discord.DMChannel):
        # Pass through to command processing
        await bot.process_commands(message)
        return

    # Ignore if not in order cache
    if not hasattr(bot, "order_cache"):
        bot.order_cache = {}

    user_id_str = str(message.author.id)
    if user_id_str not in bot.order_cache:
        return

    cache = bot.order_cache[user_id_str]
    if cache["step"] == "awaiting_details":
        # If message has an attachment, we treat it as the screenshot
        if message.attachments:
            attachment_url = message.attachments[0].url
            order_data = f"**Order Details from {message.author.display_name}:**\n\n**Details:** {cache.get('details', 'Not provided')}\n**Screenshot:** {attachment_url}"
        else:
            # Store the text details and ask for screenshot
            cache["details"] = message.content
            bot.order_cache[user_id_str] = cache
            await message.channel.send("📝 Details recorded. Please send a **screenshot** to confirm (attach as image).")
            return

        # We have both details and screenshot – send to review channel
        staff_id = cache["staff_id"]
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            await message.channel.send("❌ Error: Guild not found.")
            return

        review_channel = guild.get_channel(REVIEW_CHANNEL)
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
        await review_channel.send(content=f"📢 <@&{ORDERER_ROLE}>", embed=embed, view=view)

        await message.channel.send("✅ Your order has been sent for review! You'll be notified once approved or rejected.")

        # Remove from cache
        del bot.order_cache[user_id_str]

# --------------------------
# Admin Commands
# --------------------------
@bot.command(name="add_orderer")
@commands.has_permissions(administrator=True)
async def add_orderer(ctx, member: discord.Member):
    """
    Add the Orderer role to a member.
    """
    role = ctx.guild.get_role(ORDERER_ROLE)
    if not role:
        await ctx.send("❌ Orderer role not found. Please create it first.")
        return
    await member.add_roles(role)
    await ctx.send(f"✅ Added {member.mention} to Orderer role.")

@bot.command(name="remove_orderer")
@commands.has_permissions(administrator=True)
async def remove_orderer(ctx, member: discord.Member):
    """
    Remove the Orderer role from a member.
    """
    role = ctx.guild.get_role(ORDERER_ROLE)
    if not role:
        await ctx.send("❌ Orderer role not found.")
        return
    await member.remove_roles(role)
    await ctx.send(f"✅ Removed {member.mention} from Orderer role.")

@bot.command(name="ticket_stats")
@commands.has_permissions(administrator=True)
async def ticket_stats(ctx):
    """
    Show ticket statistics (open/closed for each type).
    """
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
    """
    Force‑close the current ticket channel (staff only).
    """
    channel = ctx.channel
    # Check if the channel is in one of the ticket categories
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
    """
    Get a transcript of the current ticket (sends as a file).
    """
    channel = ctx.channel
    # Limit to ticket channels only
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

    # Send as a text file
    with open("transcript.txt", "w", encoding="utf-8") as f:
        f.write(transcript)
    await ctx.send(file=discord.File("transcript.txt"))
    os.remove("transcript.txt")

@bot.command(name="claim_ticket")
@commands.has_permissions(manage_channels=True)
async def claim_ticket(ctx):
    """
    Assign yourself as the staff member handling this ticket.
    """
    channel = ctx.channel
    orders_cat = get_category(ctx.guild, CATEGORY_ORDERS_NAME)
    general_cat = get_category(ctx.guild, CATEGORY_GENERAL_NAME)
    if not channel.category or channel.category.id not in [orders_cat.id if orders_cat else 0, general_cat.id if general_cat else 0]:
        await ctx.send("❌ This command can only be used in ticket channels.")
        return

    # Update the ticket entry
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
# Background Task: Auto‑close inactive tickets (optional)
# --------------------------
@tasks.loop(hours=1)
async def auto_close_inactive():
    """
    Automatically close tickets that have been inactive for more than INACTIVE_CLOSE_HOURS.
    """
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
            closed_ticket["reason"] = "Auto‑closed due to inactivity"
            tickets[ttype]["closed"].append(closed_ticket)
            closed_any = True
            # Optionally try to lock the channel
            guild = bot.get_guild(GUILD_ID)
            if guild:
                channel = guild.get_channel(ticket["channel_id"])
                if channel:
                    try:
                        await channel.set_permissions(guild.default_role, read_messages=False)
                        await channel.send("🔒 This ticket has been auto‑closed due to inactivity.")
                    except:
                        pass
    if closed_any:
        save_tickets(tickets)

# --------------------------
# Bot Events
# --------------------------
@bot.event
async def on_ready():
    """
    Called when the bot is ready. Posts the dropdown in the specified channel.
    """
    print(f"✅ Logged in as {bot.user}")
    print(f"   Guild ID: {GUILD_ID}")
    print(f"   Review Channel: {REVIEW_CHANNEL}")
    print(f"   Orderer Role: {ORDERER_ROLE}")
    print(f"   Support Role: {SUPPORT_ROLE}")

    # Start the auto-close loop
    if INACTIVE_CLOSE_HOURS > 0:
        auto_close_inactive.start()
        print(f"🔄 Auto-close enabled: {INACTIVE_CLOSE_HOURS} hours inactivity.")

    # Post the dropdown in the lobby channel
    channel = bot.get_channel(TICKET_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title="🎫 Ticket System",
            description="Select an option from the dropdown below to open a ticket.",
            color=discord.Color.gold()
        )
        try:
            await channel.send(embed=embed, view=TicketViewDropdown())
            print(f"✅ Ticket dropdown posted in {channel.name} ({channel.id})")
        except discord.Forbidden:
            print(f"❌ Missing permissions in {channel.name}")
        except Exception as e:
            print(f"❌ Error posting dropdown: {e}")
    else:
        print(f"❌ Channel {TICKET_CHANNEL_ID} not found.")

    # Notify that bot is fully loaded
    print("✅ Ticket system is ready.")

# --------------------------
# Run the bot
# --------------------------
if __name__ == "__main__":
    if not TOKEN:
        print("❌ TOKEN not set in .env")
    else:
        bot.run(TOKEN)
