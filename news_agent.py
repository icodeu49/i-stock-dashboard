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

# 2. FETCH NEWS & MAP ORIGINAL LINKS TO TRACKING IDs
def fetch_news():
    raw_content = ""
    link_map = {}
    link_counter = 1
    
    for topic, url in TOPICS_AND_FEEDS.items():
        feed = feedparser.parse(url)
        raw_content += f"\n### TOPIC: {topic}\n"
        for entry in feed.entries[:5]:
            # Safeguard long tracker URLs from LLM truncation
            link_id = f"LINK_ID_{link_counter}"
            link_map[link_id] = entry.link
            
            raw_content += f"Title: {entry.title}\nLinkID: {link_id}\nSource: {entry.get('source', {}).get('title', 'Unknown')}\n\n"
            link_counter += 1
            
    return raw_content, link_map

# 3. GENERATE SUMMARY (WITH LINK CODES INSTEAD OF BREAKABLE URL STRINGS)
def generate_summary(raw_news, formatting_instructions):
    system_instruction = (
        "You are an elite news intelligence agent. Your job is to output the summary as a clean HTML snippet. "
        "Do NOT wrap your entire response in markdown code blocks like ```html ... ```. Just output raw html tags directly.\n\n"
        "Format guidelines:\n"
        "1. Use <h3> for the main topic headers.\n"
        "2. Use an unordered list <ul> for items.\n"
        "3. Each list item <li> should start with a bolded theme keyword or core insight.\n"
        "4. Crucial for links: You MUST explicitly wrap your source headlines inside an HTML anchor tag using the exact LinkID provided, like this: "
        "   '<a href=\"LinkID\">Headline Text (Source)</a>'. Do not change or invent the LinkID string.\n"
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
    
    # Strip away markdown block text if present
    text = response.text.strip()
    if text.startswith("```html"):
        text = text[7:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

# 4. RESTORE UNTOUCHED URLS AND SEND HTML EMAIL
def send_email(html_content, link_map):
    if not APP_PASSWORD:
        print("Error: APP_PASSWORD environment variable is missing!")
        return

    # Dynamically inject pristine source links back into the HTML block safely
    for link_id, true_url in link_map.items():
        html_content = html_content.replace(link_id, true_url)

    msg = MIMEMultipart()
    msg['From'] = USER_EMAIL
    msg['To'] = USER_EMAIL
    msg['Subject'] = "Your Morning Intelligence Briefing"
    
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
    news_data, original_links = fetch_news()
    
    print("Synthesizing summaries with Gemini...")
    summary_email = generate_summary(news_data, memory["preferred_style"])
    
    print("Dispatching morning brief...")
    send_email(summary_email, original_links)