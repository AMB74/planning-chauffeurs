import os
import json
import urllib.request
import urllib.parse
from datetime import datetime

# ── CONFIGURATION ──────────────────────────────────────────
AIRTABLE_TOKEN = os.environ["AIRTABLE_TOKEN"]
AIRTABLE_BASE  = os.environ["AIRTABLE_BASE"]
VIEW_NAME      = "5. Bagages stockés"
TABLES         = ["SEMAINE 1", "SEMAINE 2"]

JOURS_FR = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"]
MOIS_FR  = ["Janvier","Février","Mars","Avril","Mai","Juin",
             "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]


def get_text(fields, key):
    val = fields.get(key, "")
    if isinstance(val, list):
        return ", ".join(str(v) for v in val if v)
    return str(val) if val else ""


def format_client(val):
    if not val:
        return "–"
    if ' (' in val:
        parts = val.split(' (', 1)
        nom = parts[0].strip()
        tel = '(' + parts[1].strip()
        return f"{nom}\n{tel}"
    return val


def format_date_fr_court(date_str):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{JOURS_FR[d.weekday()][:3]} {d.day:02d}/{d.month:02d}"
    except Exception:
        return date_str or "–"


def fetch_records(table_name):
    records = []
    offset = None
    page = 0
    max_pages = 20

    while page < max_pages:
        url = (
            f"https://api.airtable.com/v0/{AIRTABLE_BASE}/"
            f"{urllib.parse.quote(table_name)}"
            f"?pageSize=100"
            f"&view={urllib.parse.quote(VIEW_NAME, safe='')}"
        )
        if offset:
            url += f"&offset={offset}"

        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {AIRTABLE_TOKEN}"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
        data = json.loads(body)
        if "error" in data:
            raise Exception(f"Erreur Airtable ({table_name}) : {data['error']}")

        batch = data.get("records", [])
        records.extend(batch)
        offset = data.get("offset")
        page += 1

        if not offset:
            break

    return records


def main():
    all_records = []
    for table in TABLES:
        print(f"Récupération de '{table}' — vue '{VIEW_NAME}'...")
        recs = fetch_records(table)
        print(f"  → {len(recs)} enregistrement(s)")
        if recs:
            print(f"  → Champs disponibles : {list(recs[0]['fields'].keys())}")
        for r in recs:
            r["_table"] = table
        all_records.extend(recs)

    # Filtrer les lignes où NBRE est vide / nul, et dédoublonner par DOSSIER_ID
    vus = set()
    lignes = []

    for rec in all_records:
        f = rec.get("fields", {})

        nbre = get_text(f, "NBRE").strip()
        if not nbre or nbre in ("0", "0.0"):
            continue

        dossier_id = get_text(f, "DOSSIER_ID")
        cle = dossier_id or rec.get("id")
        if cle in vus:
            continue
        vus.add(cle)

        date_debut = get_text(f, "DÉBUT")
        date_fin   = get_text(f, "FIN")

        lignes.append({
            "jour_semaine": get_text(f, "Jour de la semaine") or "–",
            "client":      format_client(get_text(f, "CLIENT /AEM")),
            "chauffeur":   get_text(f, "CHAUFFEUR") or "–",
            "nbre":        nbre,
            "depart":      get_text(f, "HÉBERGEMENT (from DEBUT)") or "–",
            "arrivee":     get_text(f, "HÉBERGEMENT (from FIN SEJOUR)") or "–",
            "date_debut":  date_debut,
            "date_fin":    date_fin,
            "date_debut_aff": format_date_fr_court(date_debut),
            "date_fin_aff":   format_date_fr_court(date_fin),
            "semaine":     rec.get("_table", ""),
        })

    # Tri par date de début de séjour
    def parse_date(date_str):
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except Exception:
            return datetime.max

    lignes.sort(key=lambda l: parse_date(l["date_debut"]))

    data = {
        "meta": {
            "genere_le": datetime.now().strftime("%d/%m/%Y %H:%M"),
        },
        "lignes": lignes,
    }

    with open("data_bagages.json", "w", encoding="utf-8") as fout:
        json.dump(data, fout, ensure_ascii=False, indent=2)

    print(f"data_bagages.json généré : {len(lignes)} ligne(s) de bagages stockés.")


if __name__ == "__main__":
    main()
