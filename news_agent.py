import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import feedparser
from google import genai
from google.genai import types

# 1. SETUP & CONFIGURATION
api_key = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

USER_EMAIL = "sumit.kansal@gmail.com"
APP_PASSWORD = os.environ.get("APP_PASSWORD") 

TOPICS_AND_FEEDS = {
    "AI & Tech": "https://news.google.com/rss/search?q=Artificial+Intelligence",
    "Global Markets": "https://news.google.com/rss/search?q=Stock+Market+Economy",
    "Macroeconomics": "https://news.google.com/rss/search?q=Macroeconomics",
    "AI Power": "https://news.google.com/rss/search?q=AI+Power+energy",
    "AI memory HBM companies": "https://news.google.com/rss/search?q=AI+MEMORY+HBM",
}

MEMORY_FILE = "agent_memory.json"

def load_memory():
    if os.path.exists(MEMORY_FILE) and os.path.getsize(MEMORY_FILE) > 0:
        try:
            with open(MEMORY_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("Warning: agent_memory.json was corrupted. Resetting.")
    return {"evolution_notes": [], "preferred_style": "Concise, data-driven, highlighting institutional trends."}

def fetch_news():
    raw_content = ""
    for topic, url in TOPICS_AND_FEEDS.items():
        feed = feedparser.parse(url)
        raw_content += f"\n### TOPIC: {topic}\n"
        for entry in feed.entries[:5]:
            raw_content += f"Title: {entry.title}\nLink: {entry.link}\nSource: {entry.get('source', {}).get('title', 'Unknown')}\n\n"
    return raw_content

# 2. UPDATED FOR CLEAN HTML CODE GENERATION
def generate_summary(raw_news, formatting_instructions):
    system_instruction = (
        "You are an elite news intelligence agent. Your job is to output the summary as a clean HTML snippet. "
        "Format guidelines:\n"
        "1. Use <h3> for the main topic headers.\n"
        "2. Use an unordered list <ul> for items.\n"
        "3. Each list item <li> should start with a bolded theme keyword or core insight.\n"
        "4. Crucial: You MUST hyper-link the supporting source headlines by wrapping them in HTML anchor tags: <a href='URL'>Headline Text (Source)</a>. Hide raw URLs completely.\n"
        "5. Add an extra <br><br> at the end of each major <li> bullet point to introduce generous spacing and breathability.\n"
        f"Style baseline: {formatting_instructions}"
    )
    
    prompt = f"Synthesize and summarize the following news into clean HTML following the system rules:\n\n{raw_news}"
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.3,
        )
    )
    return response.text

# 3. SWITCHED TO HTML EMAIL DISPATCH
def send_email(html_content):
    if not APP_PASSWORD:
        print("Error: APP_PASSWORD environment variable is missing!")
        return

    msg = MIMEMultipart()
    msg['From'] = USER_EMAIL
    msg['To'] = USER_EMAIL
    msg['Subject'] = "Your Morning Intelligence Briefing"
    
    # 👇 CHANGE: Switched 'plain' text mode to 'html' mode
    msg.attach(MIMEText(html_content, 'html'))
    
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(USER_EMAIL, APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("Briefing sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

if __name__ == "__main__":
    memory = load_memory()
    print("Fetching free news sources...")
    news_data = fetch_news()
    
    print("Synthesizing summaries with Gemini...")
    summary_email = generate_summary(news_data, memory["preferred_style"])
    
    print("Dispatching morning brief...")
    send_email(summary_email)