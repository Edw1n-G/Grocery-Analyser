import os
import json
import sqlite3
from dotenv import load_dotenv
from PIL import Image
from pillow_heif import register_heif_opener
from pydantic import BaseModel, Field
from google import genai

# Konfiguration
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
EINGANG_ORDNER = "input"

register_heif_opener() 

if not API_KEY:
    print("Fehler: GEMINI_API_KEY wurde in der .env Datei nicht gefunden!")
    exit()


client = genai.Client(api_key=API_KEY)

# --- Pydantic Schema (Das neue "Structured Output") ---
# Wir definieren hier als Python-Klasse, wie das JSON am Ende aussehen MUSS.
class Artikel(BaseModel):
    original_name: str = Field(description="Der exakte Text auf dem Bon")
    standard_name: str = Field(description="Generische Kategorie (z.B. statt 'Rewe Bio Äpfel 1kg' nur 'Äpfel')")
    menge: float = Field(description="Gekaufte Menge als Zahl (z.B. 1.0)")
    preis: float = Field(description="Preis als Zahl (z.B. 1.49)")

class Kassenbon(BaseModel):
    supermarkt: str = Field(description="Name des Supermarkts")
    datum: str = Field(description="Kaufdatum als YYYY-MM-DD")
    gesamtsumme: float = Field(description="Die Gesamtsumme des Bons")
    artikel: list[Artikel]

# --- Funktionen ---
def kassenbon_scannen(bild_pfad):
    print(f"\nAnalysiere Bild '{os.path.basename(bild_pfad)}'...")
    img = Image.open(bild_pfad)

    # --- DAS IST DER NEUE PROMPT ---
    prompt = """
    Lese diesen Kassenzettel aus und extrahiere die Daten.
    
    WICHTIGE REGELN:
    1. Fasse generische Kategorien zusammen (z.B. statt 'Rewe Bio Äpfel 1kg' -> 'Äpfel').
    2. RABATTE / PREISVORTEILE: Wenn auf dem Bon ein Rabatt (z.B. 'Preisvorteil', 'Aktion', 'Rabatt') steht, darf dieser NIEMALS als eigener Artikel gespeichert werden!
    3. RABATT-BERECHNUNG: Finde das Produkt, zu dem der Rabatt gehört (meistens die Zeile direkt darüber). Ziehe den Rabatt vom Gesamtpreis dieses Produkts ab und berechne den neuen reduzierten Einzelpreis.
    4. Falls ganz am ende jeder Zeile immer der gleiche Buchstabe oder Preis steht (z.B. 1,2,A,B) ignoriere diesen, da es nicht zum Einkauf gehört.
    5. Falls die Menge nicht explizit steht neben dem Produkt ist die Menge 1. Falls eine Mengenangabe einzeln steht (z.B. 6 x 0,99) dann gehört die Menge zum Produkt in der Zeile darunter.
    
    BEISPIEL FÜR DEINE LOGIK:
    Auf dem Bon steht:
    "Schokolade  2 x 0,99   1,98"
    "Preisvorteil          -0,20"
    
    Dein korrekter Output für dieses Produkt muss so aussehen:
    standard_name: "Schokolade"
    menge: 2.0
    preis: 0.89  (Berechnung: (1.98 - 0.20) / 2 = 0.89)
    """
    # -------------------------------
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[prompt, img],
        config={
            "response_mime_type": "application/json",
            "response_schema": Kassenbon,
            "temperature": 0.0 # Auf 0.0 setzen für maximale mathematische Präzision!
        }
    )
    
    return json.loads(response.text)

def in_datenbank_speichern(daten):
    conn = sqlite3.connect('database/database.db')
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT INTO einkaeufe (datum, supermarkt, gesamtsumme)
            VALUES (?, ?, ?)
        ''', (daten['datum'], daten['supermarkt'], daten['gesamtsumme']))
        
        einkauf_id = cursor.lastrowid
        
        for artikel in daten['artikel']:
            cursor.execute('''
                INSERT INTO artikel (einkauf_id, original_name, standard_name, menge, preis)
                VALUES (?, ?, ?, ?, ?)
            ''', (einkauf_id, artikel['original_name'], artikel['standard_name'], artikel['menge'], artikel['preis']))
            
        conn.commit()
        print(f"-> Erfolg! {len(daten['artikel'])} Artikel in die Datenbank geschrieben.")
        
    except sqlite3.Error as e:
        print("-> Datenbank-Fehler:", e)
    finally:
        conn.close()

def eingang_verarbeiten():
    if not os.path.exists(EINGANG_ORDNER):
        os.makedirs(EINGANG_ORDNER)
        print(f"Ordner '{EINGANG_ORDNER}' wurde erstellt. Bitte lege hier deine Fotos ab.")
        return

    erlaubte_endungen = ('.png', '.jpg', '.jpeg', '.heic', '.heif')
    dateien = [f for f in os.listdir(EINGANG_ORDNER) if f.lower().endswith(erlaubte_endungen)]

    if not dateien:
        print(f"Keine Bilder im Ordner '{EINGANG_ORDNER}' gefunden.")
        return

    print(f"=== {len(dateien)} Bild(er) gefunden. Starte Verarbeitung ===")

    for datei in dateien:
        bild_pfad = os.path.join(EINGANG_ORDNER, datei)
        
        try:
            bon_daten = kassenbon_scannen(bild_pfad)
            
            print("\nErkannte Daten:")
            print(json.dumps(bon_daten, indent=2, ensure_ascii=False))
            print("-" * 40)
            
            while True:
                antwort = input("Sind die Daten korrekt? (j = Speichern & Löschen / n = Überspringen): ").strip().lower()
                if antwort in ['j', 'n']:
                    break
                print("Bitte mit 'j' oder 'n' antworten.")

            if antwort == 'j':
                in_datenbank_speichern(bon_daten)
                os.remove(bild_pfad) 
                print(f"-> Datei '{datei}' wurde gelöscht.")
            else:
                print(f"-> Überspringe '{datei}'. Die Datei bleibt im Ordner.")
                
        except Exception as e:
            print(f"-> Ein Fehler ist bei '{datei}' aufgetreten: {e}")

if __name__ == "__main__":
    eingang_verarbeiten()