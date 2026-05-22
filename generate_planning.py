import os
import json
import urllib.request
import urllib.parse
from datetime import datetime

# ── CONFIGURATION ──────────────────────────────────────────
AIRTABLE_TOKEN  = os.environ["AIRTABLE_TOKEN"]   # injecté par GitHub Actions
AIRTABLE_BASE   = os.environ["AIRTABLE_BASE"]    # ex: appXXXXXXXXXXXXXX
TABLE_NAME      = "SEMAINE 1"

# Champs à récupérer (noms exacts Airtable)
FIELDS = [
    "BAGAGES + TRANSFERT",
    "RENFORTS",
    "!!",
    "TYPE PRESTATION",
    "TRANSFERT",
    "DÉTAILS",
    "HEURE RDV",
    "DÉPART",
    "CLIENT /AEM",
    "Nombre ajusté",
    "ARRIVÉE",
    "PILOTE_NOM",
    "RENFORTS_NOM",
    "DEPART_NOM",
    "ARRIVEE_NOM",
]

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

# ── HELPERS ────────────────────────────────────────────────
def get_text(fields, key):
    """Extrait une valeur texte simple, gère les listes (lookup)."""
    val = fields.get(key, "")
    if isinstance(val, list):
        return ", ".join(str(v) for v in val if v)
    return str(val) if val else ""

def fetch_all_records():
    """Récupère tous les enregistrements Airtable (gère la pagination)."""
    records = []
    offset = None
    fields_param = "&".join(f"fields%5B%5D={urllib.parse.quote(f)}" for f in FIELDS)

    while True:
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{urllib.parse.quote(TABLE_NAME)}?{fields_param}"
        if offset:
            url += f"&offset={offset}"

        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {AIRTABLE_TOKEN}"}
        )
        print(f"URL appelée : https://api.airtable.com/v0/{AIRTABLE_BASE}/{urllib.parse.quote(TABLE_NAME)}")
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())

        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break

    return records

# ── MAIN ───────────────────────────────────────────────────
def main():
    print(f"Récupération de la table '{TABLE_NAME}'...")
    records = fetch_all_records()
    print(f"Premier enregistrement : {records[0]['fields'] if records else 'Aucun'}")
    print(f"{len(records)} enregistrements récupérés.")

    now = datetime.now()

    # Noms des jours/mois en français
    JOURS = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"]
    MOIS  = ["Janvier","Février","Mars","Avril","Mai","Juin",
             "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]

    date_affichee   = f"{JOURS[now.weekday()]} {now.day} {MOIS[now.month-1]}"
    numero_semaine  = f"Semaine {now.isocalendar()[1]}"
    genere_le       = now.strftime("%d/%m/%Y")

    # Construction des lignes
    lignes = []
    for rec in records:
        f = rec.get("fields", {})
        lignes.append({
            "pilote":    get_text(f, "PILOTE_NOM"),
            "renforts":  get_text(f, "RENFORTS_NOM"),
            "alerte":    get_text(f, "!!"),
            "type":      get_text(f, "TYPE PRESTATION") or "–",
            "transfert": get_text(f, "TRANSFERT") or "–",
            "details":   get_text(f, "DÉTAILS") or "–",
            "heure_rdv": get_text(f, "HEURE RDV"),
            "depart":    get_text(f, "DEPART_NOM") or get_text(f, "DÉPART") or "–",
            "client":    get_text(f, "CLIENT /AEM") or "–",
            "nbre":      get_text(f, "Nombre ajusté") or "–",
            "arrivee":   get_text(f, "ARRIVEE_NOM") or get_text(f, "ARRIVÉE") or "–",
        })

    # Structure finale
    data = {
        "meta": {
            "semaine":        "SEMAINE 1",
            "date_affichee":  date_affichee.upper(),
            "numero_semaine": numero_semaine,
            "genere_le":      genere_le,
        },
        "chauffeurs": CHAUFFEURS,
        "sections": [
            {
                "id":     "s1",
                "label":  "SEMAINE 1",
                "titre":  "Planning de la semaine",
                "lignes": lignes,
            }
        ]
    }

    # Écriture du fichier
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"data.json généré avec {len(lignes)} lignes.")

if __name__ == "__main__":
    main()
