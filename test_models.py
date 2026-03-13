import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.getenv('GOOGLE_API_KEY')
if not api_key:
    print("Kein API Key!")
    exit()

client = genai.Client(api_key=api_key)

print("Verfügbare Modelle:")
try:
    # List models that support generateContent
    for model in client.models.list():
        # Filter nach Modellen, die 'generateContent' unterstützen
        if 'generateContent' in (model.supported_actions or []):
            print(f"- {model.name} ({model.display_name})")
except Exception as e:
    print(f"Fehler beim Listen: {e}")
