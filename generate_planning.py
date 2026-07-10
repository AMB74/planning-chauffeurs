import os
import json
import urllib.request
import urllib.parse
from datetime import datetime

# ── CONFIGURATION ──────────────────────────────────────────
AIRTABLE_TOKEN  = os.environ["AIRTABLE_TOKEN"]
AIRTABLE_BASE   = os.environ["AIRTABLE_BASE"]
TABLE_NAME      = "SEMAINE 1"
VIEW_NAME       = "🚫 Extraction Github 🚫"

# Liste fixe des chauffeurs
CHAUFFEURS = [
    {"prenom": "Aurore",    "nom": "VALANCE",      "vehicule": "TRAFIC",        "plaque": "GN-881-PK"},
    {"prenom": "Bertrand",  "nom": "AUMOITTE",     "vehicule": "TRAFIC",        "plaque": "FM-024-VV"},
    {"prenom": "Charlie",   "nom": "DESMONT",      "vehicule": "MASTER",        "plaque": "GQ-609-ZB"},
    {"prenom": "Damian",    "nom": "TESAURO",      "vehicule": "FIAT FULLBACK", "plaque": "EP-096-RC"},
    {"prenom": "Ivan",      "nom": "VILA",         "vehicule": "MASTER",        "plaque": "GT-479-YM"},
    {"prenom": "Jean-Marc", "nom": "LONNE-PEYRET", "vehicule": "TRAFIC",        "plaque": "HB-471-JL"},
    {"prenom": "Laurent",   "nom": "GOUGAIN",      "vehicule": "MOVANO",        "plaque": "GD-485-GB"},
    {"prenom": "Oscar",     "nom": "TESAURO",      "vehicule": "TRAFIC",        "plaque": "FN-020-TV"},
    {"prenom": "Serge",     "nom": "DECLERCK",     "vehicule": "TRAFIC",        "plaque": "HK-583-DV"},
    {"prenom": "Yan",       "nom": "ANDRE",        "vehicule": "TRAFIC",        "plaque": "Location"},
]

JOURS_FR = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"]
MOIS_FR  = ["Janvier","Février","Mars","Avril","Mai","Juin",
             "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]

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

def fetch_records():
    records = []
    offset = None
    page = 0
    max_pages = 20

    while page < max_pages:
        url = (
            f"https://api.airtable.com/v0/{AIRTABLE_BASE}/"
            f"{urllib.parse.quote(TABLE_NAME)}"
            f"?pageSize=100"
            f"&view={urllib.parse.quote(VIEW_NAME, safe='')}"
        )
        if offset:
            url += f"&offset={offset}"

        print(f"Page {page+1} — {len(records)} enregistrements récupérés jusqu'ici...")
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {AIRTABLE_TOKEN}"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
        data = json.loads(body)
        if "error" in data:
            raise Exception(f"Erreur Airtable : {data['error']}")

        batch = data.get("records", [])
        records.extend(batch)
        offset = data.get("offset")
        print(f"Offset reçu : {repr(offset)}")
        page += 1

        if not offset:
            break

    print(f"{len(records)} enregistrements récupérés au total ({page} page(s)).")
    return records

def format_date_fr(date_str):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{JOURS_FR[d.weekday()]} {d.day} {MOIS_FR[d.month-1]}".upper()
    except:
        return date_str

def main():
    print(f"Récupération de '{TABLE_NAME}' vue '{VIEW_NAME}'...")
    records = fetch_records()

    now = datetime.now()
    # Trouver le samedi de la semaine des données Airtable
    from datetime import timedelta
    dates_prestation = []
    for rec in records:
        d = rec.get("fields", {}).get("DATE PRESTATION", "")
        if d:
            try:
                dates_prestation.append(datetime.strptime(d, "%Y-%m-%d"))
            except:
                pass
    if dates_prestation:
        premiere_date = min(dates_prestation)
        days_until_saturday = (5 - premiere_date.weekday()) % 7
        saturday = premiere_date + timedelta(days=days_until_saturday)
    else:
        days_until_saturday = (5 - now.weekday()) % 7
        saturday = now + timedelta(days=days_until_saturday)
    date_affichee  = f"SEMAINE du SAMEDI {saturday.day} {MOIS_FR[saturday.month-1]}"
    numero_semaine = f"Semaine {now.isocalendar()[1]}"
    genere_le      = now.strftime("%d/%m/%Y")

    # Trier les enregistrements comme dans Airtable
    def sort_key(rec):
        f = rec.get("fields", {})
        date = get_text(f, "DATE PRESTATION") or "9999"
        num_dep = get_text(f, "NUM DÉPART (from DÉPART)") or "9999"
        num_arr = get_text(f, "NUM ARRIVÉE (from ARRIVÉE)") or "9999"
        try: num_dep = int(num_dep)
        except: num_dep = 9999
        try: num_arr = int(num_arr)
        except: num_arr = 9999
        return (date, num_dep, num_arr)

    records.sort(key=sort_key)

    from collections import OrderedDict
    dates = OrderedDict()

    for rec in records:
        f = rec.get("fields", {})
        if not dates:
            print(f"Champs disponibles : {list(f.keys())[:12]}")

        date_prestation = get_text(f, "DATE PRESTATION")
        if not date_prestation:
            date_prestation = "Sans date"

        massif = get_text(f, "MASSIFS_CALC")
        if not massif:
            massif = "Autres"

        # ── FILTRES identiques à Airtable ──

        # Inclusion : ARRIVÉE non vide OU TYPE PRODUIT contient "SANS TRANSPORT"
        arrivee = get_text(f, "ARRIVÉE")
        type_produit = get_text(f, "TYPE PRODUIT")
        if not arrivee and "SANS TRANSPORT" not in type_produit.upper():
            continue

        # Exclusion : CLIENT/AEM contient ANNULÉ ou ALTITUDE HAUTE MONTAGNE
        client = get_text(f, "CLIENT /AEM")
        if any(x in client.upper() for x in ("ANNULÉ", "ANNULE", "ALTITUDE HAUTE MONTAGNE")):
            continue

        # Exclusion : MASSIFS_CALC contient ARAVIS, VERCORS, DOLOMITES
        if any(x in massif.upper() for x in ("DOLOMITES", "ARAVIS", "VERCORS")):
            continue

        # Exclusion : SÉJOUR contient GTA 3, GTA 4, RANDOS ET TRAINS, AU COEUR DES FIZ, ANNULE, DOLOMITES
        sejour = get_text(f, "SÉJOUR")
        if any(x in sejour.upper() for x in ("GTA 3", "GTA 4", "RANDOS ET TRAINS", "AU COEUR DES FIZ", "ANNULE", "DOLOMITES")):
            continue

        ligne = {
            "pilote":    get_text(f, "PILOTE_NOM"),
            "renforts":  ", ".join(filter(None, [get_text(f, "RENFORTS_NOM"), get_text(f, "TAXIS (from TAXI)")])),
            "alerte":    get_text(f, "!!"),
            "type_transfert": get_text(f, "TYPE TRANSFERT") or "–",
            "type":      get_text(f, "TYPE") or "–",
            "transfert": get_text(f, "TRANSFERT") or "–",
            "details":   get_text(f, "DÉTAILS") or "–",
            "heure_rdv": get_text(f, "HEURE RDV"),
            "depart":    get_text(f, "HÉBERGEMENT (from DÉPART)") or "–",
            "client":    format_client(get_text(f, "CLIENT /AEM")),
            "nbre":      get_text(f, "Nombre ajusté") or "–",
            "arrivee":   get_text(f, "HÉBERGEMENT (from ARRIVÉE)") or "–",
            "stockes":   get_text(f, "NBRE") or "–",
        }

        if date_prestation not in dates:
            dates[date_prestation] = OrderedDict()
        if massif not in dates[date_prestation]:
            dates[date_prestation][massif] = []
        dates[date_prestation][massif].append(ligne)

    def sort_massif(m):
        # Trier par le numéro en début de nom (ex: "3. MONT-BLANC" → 3)
        # Les noms sans numéro vont à la fin
        import re
        match = re.match(r'^(\d+)[\.\s]', m)
        if match:
            return int(match.group(1))
        # Noms spéciaux sans numéro en premier
        if "RDV" in m.upper() or "TRANSFERT" in m.upper():
            return -2
        if "BAGAGES" in m.upper():
            return -1
        return 9999

    sections = []
    idx = 0
    for date_str in sorted(dates.keys()):
        massifs = dates[date_str]
        if date_str == "Sans date":
            titre = "Sans date"
            label = "–"
            date_complete = "SANS DATE"
        else:
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d")
                titre = f"{JOURS_FR[d.weekday()]} {d.day} {MOIS_FR[d.month-1]}".upper()
                label = d.strftime("%d/%m")
                date_complete = f"{JOURS_FR[d.weekday()]} {d.day} {MOIS_FR[d.month-1]} {d.year}".upper()
            except:
                titre = date_str
                label = date_str
                date_complete = date_str

        # Groupes de massifs dans l'ordre, avec toutes les lignes
        groupes = []
        for massif, lignes in sorted(massifs.items(), key=lambda x: sort_massif(x[0])):
            groupes.append({
                "massif": massif if massif != "Autres" else "",
                "lignes": lignes,
            })

        sections.append({
            "id":            f"s{idx}",
            "label":         label,
            "titre":         titre,
            "date_complete": date_complete,
            "groupes":       groupes,
        })
        idx += 1

    data = {
        "meta": {
            "semaine":        "SEMAINE 1",
            "date_affichee":  date_affichee.upper(),
            "numero_semaine": numero_semaine,
            "genere_le":      genere_le,
        },
        "chauffeurs": CHAUFFEURS,
        "sections":   sections,
    }

    with open("data.json", "w", encoding="utf-8") as fout:
        json.dump(data, fout, ensure_ascii=False, indent=2)

    print(f"data.json généré : {len(sections)} sections, {len(records)} lignes total.")

if __name__ == "__main__":
    main()
