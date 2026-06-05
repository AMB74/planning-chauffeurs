import os
import json
import urllib.request
import urllib.parse
from datetime import datetime
from collections import OrderedDict

# ── CONFIGURATION ──────────────────────────────────────────
AIRTABLE_TOKEN = os.environ["AIRTABLE_TOKEN"]
AIRTABLE_BASE  = os.environ["AIRTABLE_BASE"]

TABLES = [
    {"table": "SEMAINE 1", "view": "2. Attribution CHAUFFEURS", "output": "data.json",  "meta_semaine": "SEMAINE 1"},
    {"table": "SEMAINE 2", "view": "2. Attribution CHAUFFEURS", "output": "data2.json", "meta_semaine": "SEMAINE 2"},
]

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

JOURS_FR = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"]
MOIS_FR  = ["Janvier","Février","Mars","Avril","Mai","Juin",
             "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]

ORDRE_MASSIFS = [
    "1. RDV & TRANSFERT",
    "0. BAGAGES",
    "CHABLAIS",
    "GTA 1",
    "MONT-BLANC",
    "GTA 2",
    "VANOISE",
    "BEAUFORTAIN",
    "ARAVIS / GLIERES",
    "GRAND PARADIS",
    "CHAMONIX - ZERMATT / CERVIN / VALAIS",
    "GRANDS COMBINS",
    "MONT-ROSE",
    "DOLOMITES",
    "VERCORS & DEVOLUY",
    "OBERLAND",
    "DENTS BLANCHES",
    "Autres",
]

def sort_massif(m):
    try:
        return ORDRE_MASSIFS.index(m)
    except ValueError:
        return len(ORDRE_MASSIFS)

def format_client(val):
    if not val:
        return "–"
    if ' (' in val:
        parts = val.split(' (', 1)
        nom = parts[0].strip()
        tel = '(' + parts[1].strip()
        return f"{nom}\n{tel}"
    return val

def get_text(fields, key):
    val = fields.get(key, "")
    if isinstance(val, list):
        return ", ".join(str(v) for v in val if v)
    return str(val) if val else ""

def fetch_records(table_name, view_name):
    params = urllib.parse.urlencode({
        "maxRecords": 200,
        "view": view_name,
    }, quote_via=urllib.parse.quote)
    url = (
        f"https://api.airtable.com/v0/{AIRTABLE_BASE}/"
        f"{urllib.parse.quote(table_name, safe='')}?{params}"
    )
    print(f"Appel Airtable : {url[:80]}...")
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {AIRTABLE_TOKEN}",
            "Accept": "application/json",
        }
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    data = json.loads(body)
    if "error" in data:
        raise Exception(f"Erreur Airtable : {data['error']}")
    records = data.get("records", [])
    print(f"{len(records)} enregistrements reçus.")
    return records

def generate_data(table_name, view_name, output_file, meta_semaine, now):
    print(f"\n── Génération {output_file} depuis '{table_name}' vue '{view_name}'...")
    records = fetch_records(table_name, view_name)

    date_affichee  = f"{JOURS_FR[now.weekday()]} {now.day} {MOIS_FR[now.month-1]}"
    numero_semaine = f"Semaine {now.isocalendar()[1]}"
    genere_le      = now.strftime("%d/%m/%Y")

    # Structure : { date_str: { massif: [lignes] } }
    dates = OrderedDict()

    for rec in records:
        f = rec.get("fields", {})

        date_prestation = get_text(f, "DATE PRESTATION")
        if not date_prestation:
            date_prestation = "Sans date"

        massif = get_text(f, "MASSIFS (from SÉJOUR)")
        if not massif or massif.startswith("rec"):
            massif = "Autres"

        ligne = {
            "pilote":    get_text(f, "PILOTE_NOM"),
            "renforts":  ", ".join(filter(None, [get_text(f, "RENFORTS_NOM"), get_text(f, "TAXIS (from TAXI)")])),
            "alerte":    get_text(f, "!!"),
            "type":      get_text(f, "TYPE (from ARRIVÉE) 2") or "–",
            "transfert": get_text(f, "TRANSFERT") or "–",
            "details":   get_text(f, "DÉTAILS") or "–",
            "heure_rdv": get_text(f, "HEURE RDV"),
            "depart":    get_text(f, "HÉBERGEMENT (from DÉPART)") or "–",
            "client":    format_client(get_text(f, "CLIENT /AEM")),
            "nbre":      get_text(f, "Nombre ajusté") or "–",
            "arrivee":   get_text(f, "HÉBERGEMENT (from ARRIVÉE)") or "–",
            "stockes":   get_text(f, "NBRE") or "–",
            "massif":    massif,
        }

        if date_prestation not in dates:
            dates[date_prestation] = OrderedDict()
        if massif not in dates[date_prestation]:
            dates[date_prestation][massif] = []
        dates[date_prestation][massif].append(ligne)

    # 1 section = 1 jour, avec sous-groupes massif à l'intérieur
    sections = []
    idx = 0
    for date_str in sorted(dates.keys()):
        massifs = dates[date_str]

        if date_str == "Sans date":
            titre = "Sans date"
            label = "–"
            lignes_groupees = []
            for massif, lignes in sorted(massifs.items(), key=lambda x: sort_massif(x[0])):
                lignes_groupees.append({"massif": massif, "lignes": lignes})
        else:
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d")
                jour = f"{JOURS_FR[d.weekday()]} {d.day} {MOIS_FR[d.month-1]}".upper()
                label = d.strftime("%d/%m")
            except:
                jour = date_str
                label = date_str
            titre = jour
            lignes_groupees = []
            for massif, lignes in sorted(massifs.items(), key=lambda x: sort_massif(x[0])):
                lignes_groupees.append({"massif": massif, "lignes": lignes})

        # Toutes les lignes à plat pour la compatibilité chauffeur
        toutes_lignes = []
        for groupe in lignes_groupees:
            toutes_lignes.extend(groupe["lignes"])

        sections.append({
            "id":              f"s{idx}",
            "label":           label,
            "titre":           titre,
            "groupes_massifs": lignes_groupees,
            "lignes":          toutes_lignes,
        })
        idx += 1

    data = {
        "meta": {
            "semaine":        meta_semaine,
            "date_affichee":  date_affichee.upper(),
            "numero_semaine": numero_semaine,
            "genere_le":      genere_le,
        },
        "chauffeurs": CHAUFFEURS,
        "sections":   sections,
    }

    with open(output_file, "w", encoding="utf-8") as fout:
        json.dump(data, fout, ensure_ascii=False, indent=2)

    print(f"{output_file} généré : {len(sections)} sections, {len(records)} lignes total.")

def main():
    now = datetime.now()
    for cfg in TABLES:
        generate_data(
            table_name   = cfg["table"],
            view_name    = cfg["view"],
            output_file  = cfg["output"],
            meta_semaine = cfg["meta_semaine"],
            now          = now,
        )

if __name__ == "__main__":
    main()
