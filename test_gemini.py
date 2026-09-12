from dotenv import load_dotenv
from google import genai
from google.genai import types
import os

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

with open("IMG_0688.jpeg", "rb") as f:
    image_bytes = f.read()

prompt = """Analyse cette facture et réponds uniquement en JSON avec les champs suivants :
{
  "fournisseur": "",
  "date": "",
  "montant_total": "",
  "tps": "",
  "tvq": "",
  "montant_avant_taxes": "",
  "description": ""
}
Si un champ est introuvable, mets null."""

response = client.models.generate_content(
    model="gemini-3.6-flash",
    contents=[
        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
        prompt,
    ],
)

print(response.text)