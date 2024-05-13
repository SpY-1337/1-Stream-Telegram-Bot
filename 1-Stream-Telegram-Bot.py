import json
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, JobQueue
import asyncio
import nest_asyncio

nest_asyncio.apply()

# Load configuration from JSON file
with open('config.json', 'r') as config_file:
    config = json.load(config_file)

telegram_token = config["telegram_token"]
telegram_chat_id = config["telegram_chat_id"]
base_url = config["base_url"]
credentials = config["credentials"]

# Construct URLs from base URL
login_url = f"{base_url}/login"
dashboard_url = f"{base_url}/api/dashboard-stats"
servers_url = f"{base_url}/api/servers/index?with_external_servers=1&with_gpu_processes=0"

# State to keep track of previous server statuses
prev_server_statuses = {}

# Function to retrieve data
def get_data():
    # Create a session object to maintain cookies across requests
    session = requests.Session()

    # Fetch the login page to get the CSRF token
    login_page = session.get(login_url)
    soup = BeautifulSoup(login_page.content, 'html.parser')
    csrf_token = soup.find('input', {'name': '_token'})['value']

    # Update credentials with the CSRF token
    credentials["_token"] = csrf_token

    # Submit the login form
    response = session.post(login_url, data=credentials)

    # Check if login was successful
    if response.ok and "dashboard" in response.url:
        print("Login successful!")

        # Now, access the dashboard stats page with the authenticated session
        dashboard_response = session.get(dashboard_url)
        servers_response = session.get(servers_url)

        dashboard_data = {}
        servers_data = []

        if dashboard_response.status_code == 200:
            try:
                dashboard_data = dashboard_response.json()
            except ValueError:
                print("Error: Received non-JSON response")
                print("Raw response:", dashboard_response.text)
        else:
            print(f"Error: Unable to retrieve dashboard stats, status code {dashboard_response.status_code}")

        if servers_response.status_code == 200:
            try:
                servers_data = servers_response.json()
            except ValueError:
                print("Error: Received non-JSON response")
                print("Raw response:", servers_response.text)
        else:
            print(f"Error: Unable to retrieve servers info, status code {servers_response.status_code}")

        return {"dashboard": dashboard_data, "servers": servers_data}
    else:
        print("Login failed.")
        print("Raw response:", response.text)
        return None

# Format data for Telegram
def format_dashboard_stats(dashboard_stats):
    return (
        f"**Dashboard Stats**\n"
        f"- Total Connections: {dashboard_stats['connections']['total']}\n"
        f"- Total Streams: {dashboard_stats['streams']['total']}\n"
        f"- Total Users: {dashboard_stats['users']['total']}\n"
        f"- License Status: {dashboard_stats['license']['status']}\n"
        f"- License Product Name: {dashboard_stats['license']['product_name']}\n"
    )

def format_servers_info(servers_info):
    formatted_servers = "\n**Servers Info**\n"
    for server in servers_info:
        formatted_servers += (
            f"\n**{server['name']}**\n"
            f"- IP: {server['ip']}\n"
            f"- Domain: {server['domain']}\n"
            f"- Status: {server['health_status']}\n"
            f"- Live Streams: {server['live_streams']}\n"
            f"- Online Streams: {server['online_streams']}\n"
            f"- Load (1/5/15): {server['load_avg_1']}/{server['load_avg_5']}/{server['load_avg_15']}\n"
            f"- Connected Clients: {server['connected_clients']}\n"
            f"- Version: {server['version']}\n"
        )
    return formatted_servers

async def send_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = get_data()

    if data:
        dashboard_stats = format_dashboard_stats(data["dashboard"])
        servers_info = format_servers_info(data["servers"])

        message = dashboard_stats + servers_info

        await context.bot.send_message(chat_id=update.effective_chat.id, text=message, parse_mode="Markdown")

async def notify_if_license_inactive(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = get_data()
    if data and data['dashboard']['license']['status'] != 'Active':
        await context.bot.send_message(
            chat_id=telegram_chat_id,
            text="⚠️ License status is not active! Please check immediately.",
            parse_mode="Markdown"
        )

async def notify_server_status(context: ContextTypes.DEFAULT_TYPE) -> None:
    global prev_server_statuses

    data = get_data()
    if data:
        servers = data["servers"]
        current_statuses = {}

        for server in servers:
            server_name = server['name']
            health_status = server['health_status']
            current_statuses[server_name] = health_status

            # Notify only if the status changes
            if server_name in prev_server_statuses and prev_server_statuses[server_name] != health_status:
                if health_status != "online":
                    await context.bot.send_message(
                        chat_id=telegram_chat_id,
                        text=f"⚠️ Server **{server_name}** is currently **{health_status}**.",
                        parse_mode="Markdown"
                    )
                elif health_status == "online":
                    await context.bot.send_message(
                        chat_id=telegram_chat_id,
                        text=f"✅ Server **{server_name}** is back **{health_status}**.",
                        parse_mode="Markdown"
                    )

        prev_server_statuses = current_statuses

async def poll_status(context: ContextTypes.DEFAULT_TYPE) -> None:
    await notify_if_license_inactive(context)
    await notify_server_status(context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Welcome to the Server Dashboard Bot! Type /status to get the latest stats.")

async def main():
    application = ApplicationBuilder().token(telegram_token).build()

    start_handler = CommandHandler("start", start)
    status_handler = CommandHandler("status", send_report)

    application.add_handler(start_handler)
    application.add_handler(status_handler)

    job_queue = application.job_queue or JobQueue()
    job_queue.set_application(application)

    # Poll status every 10 minutes (600 seconds)
    job_queue.run_repeating(poll_status, interval=600, first=10)

    await application.run_polling()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())