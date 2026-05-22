import os
import json
import urllib.request
import urllib.parse
from datetime import datetime

# ── CONFIGURATION ──────────────────────────────────────────
AIRTABLE_TOKEN  = os.environ["AIRTABLE_TOKEN"]
AIRTABLE_BASE   = os.environ["AIRTABLE_BASE"]
TABLE_NAME      = "SEMAINE 1"

# Liste fixe des chauffeurs
CHAUFFEURS = [
    {"prenom": "Aurore",    "nom": "VALANCE",      "vehicule": "TRAFIC",        "plaque": "GN-881-PK"},
    {"prenom": "Bertrand",  "nom": "AUMOITTE",     "vehicule": "TRAFIC",        "plaque": "FM-024-VV"},
    {"prenom": "Charlie",   "nom": "DESMONT",      "vehicule": "MASTER",        "plaque": "GQ-609-ZB"},
    {"prenom": "Damian",    "nom": "TESAURO",      "vehicule": "FIAT FULLBACK", "plaque": "EP-096-RC"},
    {"prenom": "Ivan",      "nom": "VILA",         "vehicule": "MASTER",        "plaque": "GT-479-YM"},
    {"prenom": "Jean-Marc", "nom": "LONNE-PEYRET", "vehicule": "TRAFIC",        "plaque": "HB-471-JL"},
    {"prenom": "Laurent",   "nom": "GOUGAIN",      "vehicule": "MOVANO",        "plaque": "GD-485-GB"},
    {"prenom": "Oscar",     "nom": "TESAURO",      "vehicule": "LOCATION",      "plaque": None},
    {"prenom": "Serge",     "nom": "DECLERCK",     "vehicule": "TRAFIC",        "plaque": "HK-583-DV"},
]

def get_text(fields, key):
    val = fields.get(key, "")
    if isinstance(val, list):
        return ", ".join(str(v) for v in val if v)
    return str(val) if val else ""

def fetch_records():
    # Récupère max 100 enregistrements en une seule requête (sans pagination)
    url = (
        f"https://api.airtable.com/v0/{AIRTABLE_BASE}/"
        f"{urllib.parse.quote(TABLE_NAME)}"
        f"?maxRecords=200"
    )
    print(f"Appel Airtable : {url[:80]}...")
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {AIRTABLE_TOKEN}"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    data = json.loads(body)
    if "error" in data:
        raise Exception(f"Erreur Airtable : {data['error']}")
    records = data.get("records", [])
    print(f"{len(records)} enregistrements reçus.")
    return records

def main():
    print(f"Récupération de '{TABLE_NAME}'...")
    records = fetch_records()

    now = datetime.now()
    JOURS = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"]
    MOIS  = ["Janvier","Février","Mars","Avril","Mai","Juin",
             "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]

    date_affichee  = f"{JOURS[now.weekday()]} {now.day} {MOIS[now.month-1]}"
    numero_semaine = f"Semaine {now.isocalendar()[1]}"
    genere_le      = now.strftime("%d/%m/%Y")

    lignes = []
    for rec in records:
        f = rec.get("fields", {})
        # Debug : affiche le premier enregistrement
        if not lignes:
            print(f"Champs disponibles : {list(f.keys())[:10]}")
        lignes.append({
            "pilote":    get_text(f, "PILOTE_NOM"),
            "renforts":  get_text(f, "RENFORTS_NOM"),
            "alerte":    get_text(f, "!!"),
            "type":      get_text(f, "TYPE (from DÉPART) 2") or "–",
            "transfert": get_text(f, "TRANSFERT") or "–",
            "details":   get_text(f, "DÉTAILS") or "–",
            "heure_rdv": get_text(f, "HEURE RDV"),
            "depart":    get_text(f, "HÉBERGEMENT (from DÉPART)") or "–",
            "client":    get_text(f, "CLIENT /AEM") or "–",
            "nbre":      get_text(f, "Nombre ajusté") or "–",
            "arrivee":   get_text(f, "HÉBERGEMENT (from ARRIVÉE)") or "–",
            "stockes":   get_text(f, "Stockés  →  NBRE") or "–",
        })

    data = {
        "meta": {
            "semaine":        "SEMAINE 1",
            "date_affichee":  date_affichee.upper(),
            "numero_semaine": numero_semaine,
            "genere_le":      genere_le,
        },
        "chauffeurs": CHAUFFEURS,
        "sections": [{
            "id":     "s1",
            "label":  "SEMAINE 1",
            "titre":  "Planning de la semaine",
            "lignes": lignes,
        }]
    }

    with open("data.json", "w", encoding="utf-8") as fout:
        json.dump(data, fout, ensure_ascii=False, indent=2)

    print(f"data.json généré avec {len(lignes)} lignes.")

if __name__ == "__main__":
    main()
