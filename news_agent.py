import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import feedparser
from google import genai
from google.genai import types

# 1. SETUP & CONFIGURATION
# Set your Gemini API key as an environment variable: export GEMINI_API_KEY="your_key"
client = genai.Client()

USER_EMAIL = "sumit.kansal@gmail.com"
APP_PASSWORD = "bczpembdiuivucbj" # Generated via Google Account App Passwords

TOPICS_AND_FEEDS = {
    "AI & Tech": "https://news.google.com/rss/search?q=Artificial+Intelligence",
    "Global Markets": "https://news.google.com/rss/search?q=Stock+Market+Economy",
    "Macroeconomics": "https://news.google.com/rss/search?q=Macroeconomics",
    "AI Power": "https://news.google.com/rss/search?q=AI+Power+energy",
    "AI memory HBM companies": "https://news.google.com/rss/search?q=AI+MEMORY+HBM",
}

MEMORY_FILE = "agent_memory.json"

# 2. LOAD LEARNINGS & FEEDBACK LOOP
def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    return {"evolution_notes": [], "preferred_style": "Concise, data-driven, highlighting institutional trends."}

def save_feedback(new_feedback):
    memory = load_memory()
    memory["evolution_notes"].append(new_feedback)
    # Use Gemini to distill all past feedback into a single, clean optimization instruction
    distill_prompt = f"Review these feedback logs: {memory['evolution_notes']}. Summarize them into a single paragraph of explicit instructions for how a news summarizing agent should adapt its style over time."
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=distill_prompt,
    )
    memory["preferred_style"] = response.text
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=4)

# 3. FETCH FREE NEWS RSS
def fetch_news():
    raw_content = ""
    for topic, url in TOPICS_AND_FEEDS.items():
        feed = feedparser.parse(url)
        raw_content += f"\n### TOPIC: {topic}\n"
        # Grab top 5 entries per topic
        for entry in feed.entries[:5]:
            raw_content += f"Title: {entry.title}\nLink: {entry.link}\nSource: {entry.get('source', {}).get('title', 'Unknown')}\n\n"
    return raw_content

# 4. GENERATE SUMMARY USING GEMINI
def generate_summary(raw_news, formatting_instructions):
    system_instruction = (
        "You are an elite news intelligence agent. Your job is to read raw headlines and sources, "
        "synthesize them into crisp, high-signal daily summaries, and preserve the exact links for deep dives. "
        f"Adhere strictly to the user's evolving style preferences: {formatting_instructions}"
    )
    
    prompt = f"Please synthesize and summarize the following raw news dump. Group by topic and include the URLs directly next to the summaries:\n\n{raw_news}"
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.3,
        )
    )
    return response.text

# 5. SEND THE EMAIL
def send_email(content):
    msg = MIMEMultipart()
    msg['From'] = USER_EMAIL
    msg['To'] = USER_EMAIL
    msg['Subject'] = "Your Morning Intelligence Briefing"
    
    msg.attach(MIMEText(content, 'plain'))
    
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(USER_EMAIL, APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("Briefing sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

# MAIN EXECUTION FLOW
if __name__ == "__main__":
    memory = load_memory()
    print("Fetching free news sources...")
    news_data = fetch_news()
    
    print("Synthesizing summaries with Gemini...")
    summary_email = generate_summary(news_data, memory["preferred_style"])
    
    print("Dispatching morning brief...")
    send_email(summary_email)
