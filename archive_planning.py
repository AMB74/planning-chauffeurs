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
import io
import re
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AIRTABLE_TABLE_NAME = "SEMAINE 1"
AIRTABLE_VIEW_NAME = "2. Attribution CHAUFFEURS"
DATE_FIELD = "DATE PRESTATION"
TAXI_FIELD = "TAXIS (from TAXI)"

# Colonnes de la vue "2. Attribution CHAUFFEURS" :
# (en-tete affiche dans Google Sheets, nom reel du champ dans Airtable)
# Les deux different pour les champs de type lookup/liaison, ou Airtable
# renvoie un ID/valeur brute sous un nom technique different du libelle
# affiche dans la vue.
COLUMNS = [
    ("FAIT", None),  # laisse volontairement vide : reserve au pointage comptable manuel
    ("BAGAGES", "PILOTE_NOM"),
    ("TRANSFERT", "RENFORTS_NOM"),
    ("TAXI", "TAXIS (from TAXI)"),
    ("TYPE", "TYPE"),
    ("DÉTAILS", "DÉTAILS"),
    ("HEURE RDV", "HEURE RDV"),
    ("TRANSFERT", "TRANSFERTS"),
    ("DATE PRESTATION", "DATE PRESTATION"),
    ("DÉPART", "HÉBERGEMENT (from DÉPART)"),
    ("CLIENT /AEM", "CLIENT /AEM"),
    ("NOMBRE", "NOMBRE"),
    ("Nombre ajusté", "Nombre ajusté"),
    ("ARRIVÉE", "HÉBERGEMENT (from ARRIVÉE)"),
    ("Libellé", "Libellé"),
    ("TYPE PRODUIT", "TYPE PRODUIT"),
    ("SÉJOUR", "SÉJOUR"),
]

HEADERS = [display for display, _ in COLUMNS]


# Colonne technique ajoutee uniquement dans le document RESAS TAXIS,
# pour eviter les doublons d'une semaine sur l'autre
TAXI_ID_COLUMN = "ID Airtable (interne)"
TAXI_TAB_TITLE = "Résas Taxis"

# Dossier Drive "ARCHIVES AMB"
ARCHIVES_FOLDER_ID = "1rZL34VUqtbTeTZlhut_9b_J3SkizV0pv"

# Site GitHub Pages a capturer en PDF chaque semaine
PLANNING_SITE_URL = "https://amb74.github.io/planning-chauffeurs/"

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


def build_row(record, extra_id_column=False):
    fields = record.get("fields", {})
    row = []
    for _display, airtable_field in COLUMNS:
        if airtable_field is None:
            row.append("")
        elif airtable_field == DATE_FIELD:
            row.append(format_date_fr(fields.get(airtable_field)))
        else:
            row.append(cell_to_str(fields.get(airtable_field)))
    if extra_id_column:
        row.append(record["id"])
    return row


def _extract_names(value):
    """Extrait un ou plusieurs noms depuis la valeur brute d'un champ
    (gere le cas ou plusieurs personnes sont listees dans la meme cellule,
    separees par une virgule, un '/', un '+' ou 'et').
    '0' est ignore : ca signifie 'pas besoin de chauffeur', pas un nom."""
    text = cell_to_str(value)
    if not text:
        return []
    parts = re.split(r",|/|\+|\bet\b", text)
    return [p.strip() for p in parts if p.strip() and p.strip() != "0"]


def group_records_by_driver(records):
    """Regroupe les lignes par chauffeur : le nom est pris directement
    dans PILOTE_NOM (BAGAGES) et/ou RENFORTS_NOM (TRANSFERT), sans liste
    fixe a maintenir a la main -> un nouveau chauffeur est detecte
    automatiquement des qu'il apparait dans Airtable."""
    groups = {}
    for r in records:
        fields = r.get("fields", {})
        names = set()
        names.update(_extract_names(fields.get("PILOTE_NOM")))
        names.update(_extract_names(fields.get("RENFORTS_NOM")))
        for name in names:
            groups.setdefault(name, []).append(r)
    return groups


def group_records_by_taxi(records):
    """Regroupe les lignes par taxi : la cle est la valeur brute du champ
    TAXI (TAXIS (from TAXI)), telle quelle."""
    groups = {}
    for r in records:
        val = cell_to_str(r.get("fields", {}).get(TAXI_FIELD)).strip()
        if val:
            groups.setdefault(val, []).append(r)
    return groups


def sum_nombre(records):
    total = 0.0
    for r in records:
        val = r.get("fields", {}).get("NOMBRE")
        try:
            total += float(val)
        except (TypeError, ValueError):
            continue
    return int(total) if total == int(total) else total


def compute_year_and_tab_title(records):
    parsed = [parse_airtable_date(r.get("fields", {}).get(DATE_FIELD)) for r in records]
    parsed = [d for d in parsed if d is not None]
    if not parsed:
        return None, None, None

    year = Counter(d.year for d in parsed).most_common(1)[0][0]
    min_date, max_date = min(parsed), max(parsed)
    tab_title = f"{min_date.strftime('%d/%m')} au {max_date.strftime('%d/%m')}"
    monday = min_date - timedelta(days=min_date.weekday())
    return year, tab_title, monday


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


class MissingFileError(Exception):
    """Leve quand un fichier (ex: PDF) attendu n'existe pas encore sur le Drive."""


def find_file(drive_service, name, parent_folder_id, mime_type, folder_path_hint):
    """Equivalent generique de find_spreadsheet, pour tout type de fichier
    (utilise ici pour le PDF cumulatif). Ne cree rien, cherche seulement."""
    query = f"mimeType = '{mime_type}' and '{parent_folder_id}' in parents and trashed = false"
    result = drive_service.files().list(q=query, fields="files(id, name)").execute()
    files = result.get("files", [])

    target = _normalize_name(name)
    for f in files:
        if _normalize_name(f["name"]) == target:
            return f["id"]

    found_names = ", ".join(f"'{f['name']}'" for f in files) or "(dossier vide)"
    raise MissingFileError(
        f"Le fichier '{name}' n'existe pas dans le dossier '{folder_path_hint}'.\n"
        f"Fichiers trouves dans ce dossier : {found_names}\n"
        f"Merci de creer manuellement un PDF (ex: imprimer une page blanche en PDF) "
        f"nomme exactement '{name}' et de le placer dans ce dossier, puis de relancer le workflow."
    )


def get_existing_tab_titles(sheets_service, spreadsheet_id):
    meta = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return {s["properties"]["title"] for s in meta.get("sheets", [])}


def ensure_tab_exists(sheets_service, spreadsheet_id, title):
    """Retourne True si l'onglet vient d'etre cree, False s'il existait deja."""
    existing = get_existing_tab_titles(sheets_service, spreadsheet_id)
    if title in existing:
        return False
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

    return True


def get_sheet_id(sheets_service, spreadsheet_id, title):
    meta = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == title:
            return s["properties"]["sheetId"]
    return None


def apply_airtable_style(sheets_service, spreadsheet_id, title, num_rows, num_cols):
    """Applique une mise en forme proche d'Airtable :
    en-tete grise en gras figee, bordures fines type grille, et fleches de
    filtre par colonne. N'ajuste PAS la largeur des colonnes (laissee libre
    a l'utilisateur). Sans effet si l'onglet est vide (aucune ligne).
    A appeler UNIQUEMENT lors de la creation d'un nouvel onglet : les
    reappels ecraseraient les ajustements manuels faits entretemps."""
    sheet_id = get_sheet_id(sheets_service, spreadsheet_id, title)
    if sheet_id is None or num_rows == 0 or num_cols == 0:
        return

    light_gray = {"red": 0.8, "green": 0.8, "blue": 0.8}
    header_bg = {"red": 0.94, "green": 0.95, "blue": 0.96}

    requests_batch = [
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": num_cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": header_bg,
                        "textFormat": {"bold": True, "fontSize": 10},
                        "verticalAlignment": "MIDDLE",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
            }
        },
        {
            "updateBorders": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": num_rows,
                    "startColumnIndex": 0,
                    "endColumnIndex": num_cols,
                },
                "top": {"style": "SOLID", "width": 1, "color": light_gray},
                "bottom": {"style": "SOLID", "width": 1, "color": light_gray},
                "left": {"style": "SOLID", "width": 1, "color": light_gray},
                "right": {"style": "SOLID", "width": 1, "color": light_gray},
                "innerHorizontal": {"style": "SOLID", "width": 1, "color": light_gray},
                "innerVertical": {"style": "SOLID", "width": 1, "color": light_gray},
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": num_rows,
                        "startColumnIndex": 0,
                        "endColumnIndex": num_cols,
                    }
                }
            }
        },
    ]
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests_batch}
    ).execute()


def style_header_only(sheets_service, spreadsheet_id, title, num_cols):
    """Version allegee de la mise en forme : fige et met en gras seulement
    la ligne d'en-tete (utilisee pour les onglets chauffeur/taxi, dont le
    contenu grossit chaque semaine sans limite connue a l'avance)."""
    sheet_id = get_sheet_id(sheets_service, spreadsheet_id, title)
    if sheet_id is None:
        return
    header_bg = {"red": 0.94, "green": 0.95, "blue": 0.96}
    requests_batch = [
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": num_cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": header_bg,
                        "textFormat": {"bold": True, "fontSize": 10},
                        "verticalAlignment": "MIDDLE",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
            }
        },
    ]
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests_batch}
    ).execute()


def append_grouped_block(sheets_service, spreadsheet_id, tab_title, header, week_label, data_rows, total_label):
    """Ajoute un bloc hebdomadaire a un onglet chauffeur/taxi :
    une ligne titre coloree ('Semaine du DD/MM'), les lignes de donnees,
    puis (si total_label est fourni) une ligne total coloree differemment.
    Le tout est fusionne (merge) sur toute la largeur du tableau."""
    num_cols = len(header)

    existing = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{tab_title}'"
    ).execute().get("values", [])
    existing_count = len(existing)

    block = []
    if not existing:
        block.append(header)

    title_row_index = existing_count if existing else 1
    title_row = [week_label] + [""] * (num_cols - 1)
    block.append(title_row)
    block.extend(data_rows)

    total_row_index = None
    if total_label is not None:
        total_row_index = title_row_index + 1 + len(data_rows)
        total_row = [total_label] + [""] * (num_cols - 1)
        block.append(total_row)

    sheets_service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_title}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": block},
    ).execute()

    sheet_id = get_sheet_id(sheets_service, spreadsheet_id, tab_title)
    if sheet_id is None:
        return

    title_bg = {"red": 0.80, "green": 0.88, "blue": 0.98}
    total_bg = {"red": 1.0, "green": 0.93, "blue": 0.78}

    def band_requests(row_index, bg_color):
        rng = {
            "sheetId": sheet_id,
            "startRowIndex": row_index,
            "endRowIndex": row_index + 1,
            "startColumnIndex": 0,
            "endColumnIndex": num_cols,
        }
        return [
            {"mergeCells": {"range": rng, "mergeType": "MERGE_ALL"}},
            {
                "repeatCell": {
                    "range": rng,
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": bg_color,
                            "textFormat": {"bold": True},
                            "horizontalAlignment": "CENTER",
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
                }
            },
        ]

    requests_batch = band_requests(title_row_index, title_bg)
    if total_row_index is not None:
        requests_batch += band_requests(total_row_index, total_bg)

    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests_batch}
    ).execute()



    """Efface le contenu de l'onglet puis ecrit les nouvelles lignes (utilise pour l'archive).

    N'applique aucune mise en forme : la mise en forme (largeurs de colonnes,
    retour a la ligne, alignement...) est laissee telle que l'utilisateur l'a
    configuree, et n'est appliquee automatiquement qu'a la toute premiere
    creation de l'onglet (voir archive_flow)."""
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


def append_taxi_rows(sheets_service, spreadsheet_id, title, header, new_rows, newly_created):
    existing_ids = get_existing_ids(sheets_service, spreadsheet_id, title)

    existing = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{title}'"
    ).execute().get("values", [])

    rows_to_append = [row for row in new_rows if row[-1] not in existing_ids]

    if not existing:
        rows_to_append = [header] + rows_to_append

    num_cols = len(header)

    if not rows_to_append:
        print(f"[{title}] Aucune nouvelle ligne taxi a ajouter.")
        if newly_created:
            apply_airtable_style(sheets_service, spreadsheet_id, title, num_rows=len(existing), num_cols=num_cols)
        return

    sheets_service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows_to_append},
    ).execute()
    print(f"[{title}] {len(rows_to_append)} ligne(s) ajoutee(s).")

    if newly_created:
        total_rows = len(existing) + len(rows_to_append)
        apply_airtable_style(sheets_service, spreadsheet_id, title, num_rows=total_rows, num_cols=num_cols)


# ---------------------------------------------------------------------------
# Flux principaux
# ---------------------------------------------------------------------------

def archive_flow(drive_service, sheets_service, spreadsheet_id, spreadsheet_name, tab_title, records):
    newly_created = ensure_tab_exists(sheets_service, spreadsheet_id, tab_title)

    rows = [HEADERS] + [build_row(r) for r in records]
    overwrite_tab(sheets_service, spreadsheet_id, tab_title, rows)
    print(f"[{spreadsheet_name} / {tab_title}] {len(records)} ligne(s) ecrite(s).")

    # La mise en forme n'est appliquee qu'a la creation de l'onglet, jamais
    # reappliquee ensuite, pour ne pas ecraser les ajustements manuels
    # (largeurs de colonnes, retour a la ligne, alignement...).
    if newly_created:
        num_cols = max((len(r) for r in rows), default=0)
        apply_airtable_style(sheets_service, spreadsheet_id, tab_title, num_rows=len(rows), num_cols=num_cols)


def taxi_flow(drive_service, sheets_service, spreadsheet_id, spreadsheet_name, records):
    taxi_records = [r for r in records if cell_to_str(r.get("fields", {}).get(TAXI_FIELD)).strip()]
    if not taxi_records:
        print("Aucune ligne TAXI cette semaine.")
        return

    newly_created = ensure_tab_exists(sheets_service, spreadsheet_id, TAXI_TAB_TITLE)

    header = HEADERS + [TAXI_ID_COLUMN]
    rows = [build_row(r, extra_id_column=True) for r in taxi_records]
    append_taxi_rows(sheets_service, spreadsheet_id, TAXI_TAB_TITLE, header, rows, newly_created)


def driver_flow(sheets_service, spreadsheet_id, records, week_label):
    """Un onglet par chauffeur, pour toute la saison. Chaque semaine ajoute
    un bloc : ligne titre coloree, lignes de la semaine, ligne total
    bagages (somme du champ NOMBRE) coloree."""
    groups = group_records_by_driver(records)
    for first_name in sorted(groups.keys()):
        group_records = groups[first_name]
        newly_created = ensure_tab_exists(sheets_service, spreadsheet_id, first_name)
        total = sum_nombre(group_records)
        total_label = f"Total bagages semaine : {total}"
        data_rows = [build_row(r) for r in group_records]
        append_grouped_block(sheets_service, spreadsheet_id, first_name, HEADERS, week_label, data_rows, total_label)
        if newly_created:
            style_header_only(sheets_service, spreadsheet_id, first_name, num_cols=len(HEADERS))
        print(f"[{first_name}] {len(group_records)} ligne(s) ajoutee(s) pour {week_label}.")


def taxi_group_flow(sheets_service, spreadsheet_id, records, week_label):
    """Un onglet par taxi, pour toute la saison (l'onglet general 'Résas
    Taxis' cree par taxi_flow reste le premier onglet du document)."""
    groups = group_records_by_taxi(records)
    for taxi_name in sorted(groups.keys()):
        group_records = groups[taxi_name]
        newly_created = ensure_tab_exists(sheets_service, spreadsheet_id, taxi_name)
        data_rows = [build_row(r) for r in group_records]
        append_grouped_block(sheets_service, spreadsheet_id, taxi_name, HEADERS, week_label, data_rows, total_label=None)
        if newly_created:
            style_header_only(sheets_service, spreadsheet_id, taxi_name, num_cols=len(HEADERS))
        print(f"[{taxi_name}] {len(group_records)} ligne(s) ajoutee(s) pour {week_label} (taxi).")


def export_and_archive_pdf(drive_service, year_folder_id, year, week_label):
    """Capture le site GitHub Pages en PDF et l'ajoute a la suite du PDF
    cumulatif de l'annee (une page par semaine).

    Le fichier PDF doit deja exister (cree manuellement une fois, comme les
    2 Google Sheets) : le compte de service ne peut pas creer de nouveau
    fichier binaire (PDF) sans quota de stockage propre. Il se contente
    donc de recuperer le contenu existant, d'ajouter la nouvelle page a la
    suite, et de reecrire le fichier (une modification de fichier existant
    ne consomme pas de quota, contrairement a une creation)."""
    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader, PdfWriter

    pdf_folder_name = f"{year} - PDF"
    pdf_folder_id = find_or_create_folder(drive_service, pdf_folder_name, year_folder_id)
    pdf_file_name = f"{year} - PLANNING PDF.pdf"
    folder_hint = f"ARCHIVES AMB/{year}/{pdf_folder_name}"

    try:
        file_id = find_file(drive_service, pdf_file_name, pdf_folder_id, "application/pdf", folder_hint)
    except MissingFileError as exc:
        print(str(exc))
        return

    # 1. Capturer la page actuelle du site en PDF
    local_new_pdf = "/tmp/new_week.pdf"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(PLANNING_SITE_URL, wait_until="networkidle")
        page.pdf(path=local_new_pdf, format="A4", print_background=True, landscape=True)
        browser.close()

    # 2. Recuperer le contenu existant et fusionner (ancien + nouvelle page)
    existing_bytes = drive_service.files().get_media(fileId=file_id).execute()

    writer = PdfWriter()
    try:
        existing_reader = PdfReader(io.BytesIO(existing_bytes))
        for existing_page in existing_reader.pages:
            writer.add_page(existing_page)
    except Exception:
        print(f"[{pdf_file_name}] Contenu existant illisible ou vide, on repart avec la nouvelle page seule.")

    new_reader = PdfReader(local_new_pdf)
    for new_page in new_reader.pages:
        writer.add_page(new_page)

    output_buffer = io.BytesIO()
    writer.write(output_buffer)
    output_buffer.seek(0)

    media = MediaIoBaseUpload(output_buffer, mimetype="application/pdf", resumable=False)
    drive_service.files().update(fileId=file_id, media_body=media).execute()
    print(f"[{pdf_file_name}] page ajoutee pour {week_label}.")


def main():
    today = date.today()
    if not is_in_active_window(today):
        print(f"{today} hors periode active (15 mai - 15 novembre). Arret.")
        return

    records = fetch_airtable_records()
    if not records:
        print("Aucun enregistrement recupere depuis Airtable. Arret.")
        return

    year, tab_title, monday = compute_year_and_tab_title(records)
    if year is None:
        print(f"Impossible de determiner l'annee ({DATE_FIELD} vide sur toutes les lignes). Arret.")
        sys.exit(1)

    week_label = f"Semaine du {monday.strftime('%d/%m')}"

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

    # Onglets par chauffeur (document PLANNINGS ARCHIVE) et par taxi
    # (document RESAS TAXIS), cumulatifs sur toute la saison.
    driver_flow(sheets_service, archive_id, records, week_label)
    taxi_group_flow(sheets_service, taxi_id, records, week_label)

    # PDF cumulatif de la saison (une page par semaine). Non bloquant :
    # si le fichier n'existe pas encore, on avertit mais on ne fait pas
    # echouer le workflow (les Sheets ont deja ete ecrits avec succes).
    export_and_archive_pdf(drive_service, year_folder_id, year, week_label)


if __name__ == "__main__":
    main()
