"""
archive_planning.py

Sauvegarde hebdomadaire (vendredi 10h) de la vue Airtable "2. Attribution CHAUFFEURS"
(table SEMAINE 1) vers Google Sheets :

1. 20XX - PLANNINGS ARCHIVE
   -> un onglet par semaine, mise en page identique a la vue Airtable
   -> chemin Drive : ARCHIVES AMB / 20XX / 20XX - PLANNINGS ARCHIVE

2. 20XX - RESAS TAXIS
   -> un seul onglet cumulatif, ne contient que les lignes ou TAXI est rempli
   -> chemin Drive : ARCHIVES AMB / 20XX / 20XX - RESAS TAXIS

L'annee (20XX) est deduite du champ DATE PRESTATION des lignes recuperees.

Le script ne s'execute que si la date du jour est comprise entre le 15 mai
et le 15 novembre de l'annee en cours (sinon il s'arrete sans rien faire).
"""

import os
import json
import sys
import unicodedata
from collections import Counter
from datetime import date, datetime

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AIRTABLE_TABLE_NAME = "SEMAINE 1"
AIRTABLE_VIEW_NAME = "2. Attribution CHAUFFEURS"
DATE_FIELD = "DATE PRESTATION"
TAXI_FIELD = "TAXI"

# Ordre exact des colonnes de la vue "2. Attribution CHAUFFEURS"
FIELDS_ORDER = [
    "FAIT",
    "BAGAGES + TRANSFERT",
    "RENFORTS",
    "TAXI",
    "TYPE",
    "DÉTAILS",
    "HEURE RDV",
    "TRANSFERT",
    "DATE PRESTATION",
    "DÉPART",
    "CLIENT /AEM",
    "NOMBRE",
    "Nombre ajusté",
    "ARRIVÉE",
    "Libellé",
    "TYPE PRODUIT",
    "SÉJOUR",
]

# Colonne technique ajoutee uniquement dans le document RESAS TAXIS,
# pour eviter les doublons d'une semaine sur l'autre
TAXI_ID_COLUMN = "ID Airtable (interne)"
TAXI_TAB_TITLE = "Résas Taxis"

# Dossier Drive "ARCHIVES AMB"
ARCHIVES_FOLDER_ID = "1rZL34VUqtbTeTZlhut_9b_J3SkizV0pv"

MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


# ---------------------------------------------------------------------------
# Fenetre d'execution (15 mai -> 15 novembre)
# ---------------------------------------------------------------------------

def is_in_active_window(today: date) -> bool:
    start = date(today.year, 5, 15)
    end = date(today.year, 11, 15)
    return start <= today <= end


# ---------------------------------------------------------------------------
# Airtable
# ---------------------------------------------------------------------------

def fetch_airtable_records():
    base_id = os.environ["AIRTABLE_BASE"]
    token = os.environ["AIRTABLE_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://api.airtable.com/v0/{base_id}/{requests.utils.quote(AIRTABLE_TABLE_NAME)}"

    records = []
    offset = None
    while True:
        params = {"view": AIRTABLE_VIEW_NAME}
        if offset:
            params["offset"] = offset
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    return records


def cell_to_str(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def parse_airtable_date(value):
    """Airtable renvoie une date ISO ('2026-07-20' ou avec heure)."""
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def format_date_fr(value):
    d = parse_airtable_date(value)
    if not d:
        return cell_to_str(value)
    return d.strftime("%d/%m/%Y")


def format_day_month_fr(d: date) -> str:
    return f"{d.day} {MONTHS_FR[d.month - 1]}"


def build_row(record, extra_id_column=False):
    fields = record.get("fields", {})
    row = []
    for f in FIELDS_ORDER:
        if f == DATE_FIELD:
            row.append(format_date_fr(fields.get(f)))
        else:
            row.append(cell_to_str(fields.get(f)))
    if extra_id_column:
        row.append(record["id"])
    return row


def compute_year_and_tab_title(records):
    parsed = [parse_airtable_date(r.get("fields", {}).get(DATE_FIELD)) for r in records]
    parsed = [d for d in parsed if d is not None]
    if not parsed:
        return None, None

    year = Counter(d.year for d in parsed).most_common(1)[0][0]
    min_date, max_date = min(parsed), max(parsed)
    tab_title = f"Semaine du {format_day_month_fr(min_date)} au {format_day_month_fr(max_date)} {max_date.year}"
    return year, tab_title


# ---------------------------------------------------------------------------
# Google Drive / Sheets
# ---------------------------------------------------------------------------

def get_services():
    creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_KEY"])
    credentials = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    drive_service = build("drive", "v3", credentials=credentials)
    sheets_service = build("sheets", "v4", credentials=credentials)
    return drive_service, sheets_service


def find_or_create_folder(drive_service, name, parent_id):
    safe_name = name.replace("'", "\\'")
    query = (
        f"name = '{safe_name}' and mimeType = 'application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents and trashed = false"
    )
    result = drive_service.files().list(q=query, fields="files(id, name)").execute()
    files = result.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = drive_service.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def _normalize_name(name):
    """Normalise un nom de fichier pour une comparaison tolerante :
    ignore les differences de casse, d'espaces en trop, et les variantes
    de tirets (-, en dash, em dash) qui sont une source frequente de
    correspondances ratees (autocorrection clavier/mobile)."""
    n = unicodedata.normalize("NFKC", name)
    n = n.replace("\u2013", "-").replace("\u2014", "-")  # en dash, em dash -> -
    n = " ".join(n.split())  # espaces multiples -> un seul, trim
    return n.strip().lower()


class MissingSpreadsheetError(Exception):
    """Leve quand un document attendu n'existe pas encore sur le Drive."""


def find_spreadsheet(drive_service, name, parent_folder_id, folder_path_hint):
    """Cherche un spreadsheet existant. Ne le cree PAS.

    Avec un compte Google gratuit, le compte de service n'a aucun quota de
    stockage propre : toute tentative de creation de fichier (meme via un
    dossier partage) echoue avec 'storageQuotaExceeded'. Le document doit
    donc etre cree une fois manuellement par un humain (proprietaire du
    Drive), le compte de service ne fait ensuite que le modifier, ce qui ne
    consomme aucun quota.

    La comparaison de nom est tolerante (casse, espaces, type de tiret) pour
    eviter les faux negatifs lies a une autocorrection clavier.
    """
    query = (
        f"mimeType = 'application/vnd.google-apps.spreadsheet' "
        f"and '{parent_folder_id}' in parents and trashed = false"
    )
    result = drive_service.files().list(q=query, fields="files(id, name)").execute()
    files = result.get("files", [])

    target = _normalize_name(name)
    for f in files:
        if _normalize_name(f["name"]) == target:
            return f["id"]

    found_names = ", ".join(f"'{f['name']}'" for f in files) or "(dossier vide)"
    raise MissingSpreadsheetError(
        f"Le document '{name}' n'existe pas dans le dossier '{folder_path_hint}'.\n"
        f"Fichiers trouves dans ce dossier : {found_names}\n"
        f"Merci de creer manuellement un Google Sheet vierge nomme exactement "
        f"'{name}' et de le placer dans ce dossier, puis de relancer le workflow."
    )


def get_existing_tab_titles(sheets_service, spreadsheet_id):
    meta = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return {s["properties"]["title"] for s in meta.get("sheets", [])}


def ensure_tab_exists(sheets_service, spreadsheet_id, title):
    existing = get_existing_tab_titles(sheets_service, spreadsheet_id)
    if title in existing:
        return
    body = {"requests": [{"addSheet": {"properties": {"title": title}}}]}
    sheets_service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()

    # Par defaut Google Sheets cree un premier onglet "Sheet1" / "Feuille 1" vide
    # -> on le supprime seulement s'il reste vide et que ce n'est pas le seul onglet
    existing_after = get_existing_tab_titles(sheets_service, spreadsheet_id)
    default_names = {"Feuille 1", "Sheet1"}
    to_remove = default_names & existing_after
    if to_remove and len(existing_after) > 1:
        meta = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for s in meta.get("sheets", []):
            if s["properties"]["title"] in to_remove:
                req = {"requests": [{"deleteSheet": {"sheetId": s["properties"]["sheetId"]}}]}
                sheets_service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=req).execute()


def overwrite_tab(sheets_service, spreadsheet_id, title, rows):
    """Efface le contenu de l'onglet puis ecrit les nouvelles lignes (utilise pour l'archive)."""
    sheets_service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=f"'{title}'", body={}
    ).execute()
    sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()


def get_existing_ids(sheets_service, spreadsheet_id, title):
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{title}'"
    ).execute()
    values = result.get("values", [])
    if not values:
        return set()
    header = values[0]
    if TAXI_ID_COLUMN not in header:
        return set()
    id_index = header.index(TAXI_ID_COLUMN)
    ids = set()
    for row in values[1:]:
        if len(row) > id_index:
            ids.add(row[id_index])
    return ids


def append_taxi_rows(sheets_service, spreadsheet_id, title, header, new_rows):
    existing_ids = get_existing_ids(sheets_service, spreadsheet_id, title)

    existing = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{title}'"
    ).execute().get("values", [])

    rows_to_append = [row for row in new_rows if row[-1] not in existing_ids]

    if not existing:
        rows_to_append = [header] + rows_to_append

    if not rows_to_append:
        print(f"[{title}] Aucune nouvelle ligne taxi a ajouter.")
        return

    sheets_service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows_to_append},
    ).execute()
    print(f"[{title}] {len(rows_to_append)} ligne(s) ajoutee(s).")


# ---------------------------------------------------------------------------
# Flux principaux
# ---------------------------------------------------------------------------

def archive_flow(drive_service, sheets_service, spreadsheet_id, spreadsheet_name, tab_title, records):
    ensure_tab_exists(sheets_service, spreadsheet_id, tab_title)

    rows = [FIELDS_ORDER] + [build_row(r) for r in records]
    overwrite_tab(sheets_service, spreadsheet_id, tab_title, rows)
    print(f"[{spreadsheet_name} / {tab_title}] {len(records)} ligne(s) ecrite(s).")


def taxi_flow(drive_service, sheets_service, spreadsheet_id, spreadsheet_name, records):
    taxi_records = [r for r in records if cell_to_str(r.get("fields", {}).get(TAXI_FIELD)).strip()]
    if not taxi_records:
        print("Aucune ligne TAXI cette semaine.")
        return

    ensure_tab_exists(sheets_service, spreadsheet_id, TAXI_TAB_TITLE)

    header = FIELDS_ORDER + [TAXI_ID_COLUMN]
    rows = [build_row(r, extra_id_column=True) for r in taxi_records]
    append_taxi_rows(sheets_service, spreadsheet_id, TAXI_TAB_TITLE, header, rows)


def main():
    today = date.today()
    if not is_in_active_window(today):
        print(f"{today} hors periode active (15 mai - 15 novembre). Arret.")
        return

    records = fetch_airtable_records()
    if not records:
        print("Aucun enregistrement recupere depuis Airtable. Arret.")
        return

    year, tab_title = compute_year_and_tab_title(records)
    if year is None:
        print(f"Impossible de determiner l'annee ({DATE_FIELD} vide sur toutes les lignes). Arret.")
        sys.exit(1)

    drive_service, sheets_service = get_services()

    # Le dossier annee (ex: '2026') peut etre cree automatiquement : les
    # dossiers ne consomment aucun quota de stockage, contrairement aux
    # fichiers. Seuls les deux spreadsheets doivent deja exister.
    year_folder_id = find_or_create_folder(drive_service, str(year), ARCHIVES_FOLDER_ID)
    folder_hint = f"ARCHIVES AMB/{year}"

    archive_name = f"{year} - PLANNINGS ARCHIVE"
    taxi_name = f"{year} - RESAS TAXIS"

    # Verification prealable des deux documents avant toute ecriture, pour
    # ne pas se retrouver avec un seul des deux flux ecrit en cas de manque.
    try:
        archive_id = find_spreadsheet(drive_service, archive_name, year_folder_id, folder_hint)
        taxi_id = find_spreadsheet(drive_service, taxi_name, year_folder_id, folder_hint)
    except MissingSpreadsheetError as exc:
        print(str(exc))
        sys.exit(1)

    archive_flow(drive_service, sheets_service, archive_id, archive_name, tab_title, records)
    taxi_flow(drive_service, sheets_service, taxi_id, taxi_name, records)


if __name__ == "__main__":
    main()
