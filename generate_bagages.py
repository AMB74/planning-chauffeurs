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


def parse_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return None


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
        for r in recs:
            r["_table"] = table
        all_records.extend(recs)

    # Regrouper par DOSSIER_ID (un dossier peut avoir 1 ou 2 lignes,
    # une par semaine, avec un chauffeur différent au début et à la fin)
    groupes = {}

    for rec in all_records:
        f = rec.get("fields", {})

        nbre = get_text(f, "NBRE").strip()
        if not nbre or nbre in ("0", "0.0"):
            continue

        dossier_id = get_text(f, "DOSSIER_ID")
        cle = dossier_id or rec.get("id")
        groupes.setdefault(cle, []).append(rec)

    lignes = []
    for cle, recs in groupes.items():
        def get_date_prestation(r):
            return parse_date(get_text(r.get("fields", {}), "DATE PRESTATION"))

        def get_date_debut(r):
            return parse_date(get_text(r.get("fields", {}), "DÉBUT"))

        def get_date_fin(r):
            return parse_date(get_text(r.get("fields", {}), "FIN"))

        # Référence : DÉBUT et FIN du séjour (identiques sur toutes les lignes du dossier normalement)
        dates_debut = [d for d in (get_date_debut(r) for r in recs) if d]
        dates_fin   = [d for d in (get_date_fin(r) for r in recs) if d]
        ref_debut = min(dates_debut) if dates_debut else None
        ref_fin   = max(dates_fin) if dates_fin else None

        # Ligne de récupération (J1) : DATE PRESTATION == DÉBUT
        rec_debut = None
        for r in recs:
            if ref_debut and get_date_prestation(r) == ref_debut:
                rec_debut = r
                break

        # Ligne de dépose (dernier jour) : DATE PRESTATION == FIN
        rec_fin = None
        for r in recs:
            if ref_fin and get_date_prestation(r) == ref_fin:
                rec_fin = r
                break

        principal = rec_debut or rec_fin or recs[0]
        f = principal.get("fields", {})

        chauffeur_debut = get_text(rec_debut.get("fields", {}), "CHAUFFEUR") if rec_debut else "–"
        chauffeur_fin   = get_text(rec_fin.get("fields", {}), "CHAUFFEUR") if rec_fin else "–"
        chauffeur_debut = chauffeur_debut or "–"
        chauffeur_fin   = chauffeur_fin or "–"

        nbre = get_text(f, "NBRE").strip()
        date_debut = get_text(f, "DÉBUT")
        date_fin   = get_text(f, "FIN")

        # Hébergement d'arrivée : prendre la ligne du dernier jour si possible
        rec_pour_arrivee = rec_fin or principal
        arrivee = get_text(rec_pour_arrivee.get("fields", {}), "HÉBERGEMENT (from FIN SEJOUR)") or get_text(f, "HÉBERGEMENT (from FIN SEJOUR)") or "–"

        lignes.append({
            "jour_semaine":   get_text(f, "Jour de la semaine") or "–",
            "client":         format_client(get_text(f, "CLIENT /AEM")),
            "chauffeur_debut": chauffeur_debut,
            "chauffeur_fin":  chauffeur_fin,
            "nbre":           nbre,
            "depart":         get_text(f, "HÉBERGEMENT (from DEBUT)") or "–",
            "arrivee":        arrivee,
            "date_debut":     date_debut,
            "date_fin":       date_fin,
            "date_debut_aff": format_date_fr_court(date_debut),
            "date_fin_aff":   format_date_fr_court(date_fin),
        })

    # Tri par date de début de séjour
    def parse_date_sort(date_str):
        d = parse_date(date_str)
        return d or datetime.max

    lignes.sort(key=lambda l: parse_date_sort(l["date_debut"]))

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
