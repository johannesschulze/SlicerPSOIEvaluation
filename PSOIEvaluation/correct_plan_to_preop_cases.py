"""
correct_plan_to_preop_cases.py

Korrekturskript für PSOI-Fälle, bei denen "registration planned to preop"
(falscher Name) nicht auf das psiPlannedModel angewendet wurde.

Das Skript durchsucht ein Root-Verzeichnis nach Unterordnern. Ein Unterordner
wird als korrekturbedürftig erkannt, wenn darin die Datei
  "registration planned to preop.h5"
vorhanden ist. Dann wird die .mrml-Szenendatei geladen und folgendes gemacht:

  1. Transform umbenennen: "registration planned to preop" → "registration plan to preop"
  2. Diesen Transform auf psiPlannedModel anwenden und harden
  3. "registration {psiName} to postop" korrigieren:
       T_new = T_old × inv(T_planToPreop)
     (verschiebt die Rotationsreferenz in den korrekten Preop-Raum)
  4. calculatePSIModelToModelDistance neu berechnen
  5. printPSIResults neu berechnen und CSV speichern
  6. Szene speichern

Am Ende: CSV-Dateien aus ALLEN Fallordnern (output_*.csv) einsammeln,
Fall-ID (= Ordnername) als erste Spalte einfügen, in Gesamtdatei schreiben.

Ausführung in der Slicer Python Console:
    exec(open('/path/to/correct_plan_to_preop_cases.py').read())

Oder als Slicer-Skript (headless):
    Slicer --no-main-window --python-script /path/to/correct_plan_to_preop_cases.py
"""

import sys
import os
import csv
import glob
import vtk        # available in Slicer Python environment
import numpy as np

try:
    import slicer  # only available inside Slicer's Python runtime
except ImportError:
    raise RuntimeError(
        "Dieses Skript muss innerhalb der Slicer Python-Umgebung ausgeführt werden."
    )

# ============================================================
# KONFIGURATION – hier anpassen
# ============================================================

# Root-Verzeichnis, das die Fallordner als direkte Unterordner enthält
CASES_ROOT_DIR = "/home/johannes/Dokumente/Forschung/PSOI-Evaluation/Orbita-PSI-Evaluation/Daten/cases"

# Ausgabeordner für die zusammengeführte CSV
COMBINED_OUTPUT_DIR = os.path.expanduser("/home/johannes/Dokumente/Forschung/PSOI-Evaluation/Orbita-PSI-Evaluation/Daten/")

# Pfad zum PSOIEvaluation-Modulverzeichnis
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))

# Wenn True: nur anzeigen, was getan würde – keine Szene laden, nichts speichern
DRY_RUN = False

# ============================================================

OLD_TRANSFORM_FILENAME = "registration planned to preop.h5"
OLD_TRANSFORM_NAME     = "registration planned to preop"
CORRECT_TRANSFORM_NAME = "registration plan to preop"

_DRY = "[DRY RUN] "

# Node-Klasse → (StorageNode-Klasse, Dateiendung) für Nodes ohne gültigen Pfad
_NODE_STORAGE = {
    "vtkMRMLModelNode":           ("vtkMRMLModelStorageNode",        ".vtk"),
    "vtkMRMLLinearTransformNode": ("vtkMRMLLinearTransformStorageNode", ".h5"),
    "vtkMRMLTransformNode":       ("vtkMRMLTransformStorageNode",    ".h5"),
    "vtkMRMLSegmentationNode":    ("vtkMRMLSegmentationStorageNode", ".seg.nrrd"),
    "vtkMRMLMarkupsROINode":      ("vtkMRMLMarkupsJsonStorageNode",  ".mrk.json"),
}


def _is_valid_path(filepath):
    if not filepath:
        return False
    _, ext = os.path.splitext(filepath)
    return bool(ext) and " Copy" not in filepath


def _write_node(node, case_dir, override_filename=None):
    """
    Schreibt einen einzelnen Node.
    override_filename: absoluter Pfad, der im StorageNode gesetzt wird.
    Fehlt er und der vorhandene Pfad ist ungültig, wird ein Pfad aus
    case_dir + Nodename + passender Endung generiert.
    """
    if node is None:
        return
    sn = node.GetStorageNode()

    if override_filename:
        if sn is None:
            for nc, (snc, _) in _NODE_STORAGE.items():
                if node.IsA(nc):
                    sn = slicer.mrmlScene.AddNewNodeByClass(snc)
                    node.SetAndObserveStorageNodeID(sn.GetID())
                    break
        if sn:
            sn.SetFileName(override_filename)
    elif not _is_valid_path(sn.GetFileName() if sn else None):
        for nc, (snc, ext) in _NODE_STORAGE.items():
            if node.IsA(nc):
                if sn is None:
                    sn = slicer.mrmlScene.AddNewNodeByClass(snc)
                    node.SetAndObserveStorageNodeID(sn.GetID())
                safe = node.GetName().replace("/", "_").replace(" ", "_")
                sn.SetFileName(os.path.join(case_dir, safe + ext))
                break

    if sn is None or not _is_valid_path(sn.GetFileName()):
        print(f"  WARNUNG: kein gültiger Storage-Pfad für '{node.GetName()}'", file=sys.stderr)
        return
    try:
        sn.WriteData(node)
        print(f"  Gespeichert: {sn.GetFileName()}")
    except Exception as e:
        print(f"  WARNUNG: '{node.GetName()}' nicht gespeichert: {e}", file=sys.stderr)


# Bekannte Metrik-Suffixe aus printResults – identisch in allen Fällen,
# nur der PSI-Präfix davor variiert.
_KNOWN_METRICS = (
    "rms_plan_to_preop", "rms_plan_to_postop", "dice_plan_intraop",
    "hausdorff_avg_planned_postop", "hausdorff_max_planned_postop",
    "rotation_x", "rotation_y", "rotation_z",
    "distance", "vector_x", "vector_y", "vector_z", "m2m_rms",
)

def _strip_psi_prefix(col):
    """'PSI_planned_rotation_x' → 'rotation_x', 'case_id' → 'case_id'."""
    for metric in _KNOWN_METRICS:
        if col == metric or col.endswith("_" + metric):
            return metric
    return col


def find_cases_to_correct(root_dir):
    """
    Gibt eine Liste von (case_dir, mrml_path) für alle Unterordner zurück,
    in denen 'registration planned to preop.h5' vorhanden ist.
    """
    if not os.path.isdir(root_dir):
        print(f"FEHLER: Root-Verzeichnis nicht gefunden: {root_dir}", file=sys.stderr)
        return []

    cases = []
    n_already_done = 0
    for entry in sorted(os.scandir(root_dir), key=lambda e: e.name):
        if not entry.is_dir():
            continue

        old_h5 = os.path.join(entry.path, OLD_TRANSFORM_FILENAME)
        new_h5 = os.path.join(entry.path, CORRECT_TRANSFORM_NAME + ".h5")

        if not os.path.exists(old_h5):
            continue  # kein alter Transform → dieser Fall war nie betroffen

        # Bereits korrigiert wenn die korrekte .h5 neuer ist als die alte
        if os.path.exists(new_h5) and os.path.getmtime(new_h5) >= os.path.getmtime(old_h5):
            n_already_done += 1
            continue

        mrml_files = glob.glob(os.path.join(entry.path, "*.mrml"))
        if not mrml_files:
            print(f"WARNUNG: Marker gefunden, aber keine .mrml in {entry.path}", file=sys.stderr)
            continue
        if len(mrml_files) > 1:
            print(f"WARNUNG: Mehrere .mrml-Dateien in {entry.path} – nehme erste.", file=sys.stderr)
        cases.append((entry.path, mrml_files[0]))

    tag = _DRY if DRY_RUN else ""
    if n_already_done:
        print(f"{tag}{n_already_done} Fall/Fälle bereits korrigiert – übersprungen.")
    print(f"{tag}{len(cases)} korrekturbedürftige Fall/Fälle in '{root_dir}' gefunden.")
    return cases


def _setup_imports():
    if MODULE_DIR not in sys.path:
        sys.path.insert(0, MODULE_DIR)


def _get_matrix(transform_node):
    mat = vtk.vtkMatrix4x4()
    transform_node.GetMatrixTransformToParent(mat)
    arr = np.eye(4)
    for i in range(4):
        for j in range(4):
            arr[i, j] = mat.GetElement(i, j)
    return arr


def _set_matrix(transform_node, arr):
    mat = vtk.vtkMatrix4x4()
    for i in range(4):
        for j in range(4):
            mat.SetElement(i, j, float(arr[i, j]))
    transform_node.SetMatrixTransformToParent(mat)


def _find_node_or_none(name):
    try:
        return slicer.util.getNode(name)
    except Exception:
        return None


def _get_logic():
    if hasattr(slicer, 'modules') and hasattr(slicer.modules, 'OrbitaPSIWorkflowModule'):
        return slicer.modules.OrbitaPSIWorkflowModule.widgetRepresentation().self().logic
    _setup_imports()
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "OrbitaPSIWorkflowModule",
        os.path.join(MODULE_DIR, "OrbitaPSIWorkflowModule.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.OrbitaPSIWorkflowModuleLogic()


def correct_case(case_dir, mrml_path):
    """
    Einen Fall laden, korrigieren, speichern.
    Im Dry-Run-Modus wird nur geloggt, was getan würde.
    Gibt den erwarteten CSV-Pfad zurück (auch im Dry Run), oder None bei Fehler.
    """
    tag = _DRY if DRY_RUN else ""
    print(f"\n{'='*60}")
    print(f"{tag}Fall:  {case_dir}")
    print(f"{tag}Szene: {mrml_path}")
    print('='*60)

    if DRY_RUN:
        existing = glob.glob(os.path.join(case_dir, "output_*.csv"))
        csv_hint = existing[0] if existing else "(keine output_*.csv gefunden)"
        print("\n".join([
            f"{tag}Würde laden:     {mrml_path}",
            f"{tag}Würde umbenennen: '{OLD_TRANSFORM_NAME}' → '{CORRECT_TRANSFORM_NAME}'",
            f"{tag}Würde '{CORRECT_TRANSFORM_NAME}' auf psiPlannedModel anwenden und harden.",
            f"{tag}Würde 'registration <psiName> to postop' korrigieren.",
            f"{tag}Würde calculatePSIModelToModelDistance ausführen.",
            f"{tag}Würde printPSIResults ausführen und CSV speichern.",
            f"{tag}Würde Szene speichern: {mrml_path}",
            f"{tag}Erwartete CSV:    {csv_hint}",
        ]))
        return existing[0] if existing else None

    # ── Szene laden ──────────────────────────────────────────────────────────
    slicer.mrmlScene.Clear(False)
    slicer.util.loadScene(mrml_path)

    logic = _get_logic()
    pn = logic.getParameterNode()
    psiPlannedModel = pn.psiPlannedModel

    if psiPlannedModel is None:
        print("FEHLER: psiPlannedModel fehlt im ParameterNode.", file=sys.stderr)
        return None

    psiName = psiPlannedModel.GetName()
    print(f"PSI-Modell: {psiName}")

    # ── Schritt 1: Transform umbenennen ──────────────────────────────────────
    old_node = _find_node_or_none(OLD_TRANSFORM_NAME)
    if old_node is None:
        print(f"FEHLER: Datei '{OLD_TRANSFORM_FILENAME}' war vorhanden, aber Node "
              f"'{OLD_TRANSFORM_NAME}' nicht in der Szene.", file=sys.stderr)
        return None

    T_planToPreop = _get_matrix(old_node)
    old_node.SetName(CORRECT_TRANSFORM_NAME)
    plan_to_preop_node = old_node
    print(f"Transform umbenannt: '{OLD_TRANSFORM_NAME}' → '{CORRECT_TRANSFORM_NAME}'")

    # ── Schritt 2: psiPostopModel an Postop-Position fixieren ────────────────
    psiPostopModel = pn.psiPostopModel
    if psiPostopModel is None:
        print("FEHLER: psiPostopModel fehlt im ParameterNode.", file=sys.stderr)
        return None
    # Sicherstellen, dass das Modell geometrisch an der Postop-Position liegt,
    # bevor wir den Transform-Node auf T_new ändern.
    psiPostopModel.HardenTransform()

    # ── Schritt 3: registration plan to preop auf psiPlannedModel anwenden ───
    psiPlannedModel.SetAndObserveTransformNodeID(plan_to_preop_node.GetID())
    psiPlannedModel.HardenTransform()
    print(f"'{CORRECT_TRANSFORM_NAME}' auf '{psiName}' angewendet und gehärtet.")

    # ── Schritt 4: registration {psiName} to postop korrigieren ──────────────
    # Herleitung:
    #   Alt (fehlerhaft): p_postop ≈ T_old × p_plan
    #   Korrekt:          p_preop  = T_p2pr × p_plan
    #                     p_postop ≈ T_new × p_preop = T_new × T_p2pr × p_plan
    #   → T_new = T_old × inv(T_p2pr)   [Rechts-Komposition der Inversen]
    psi_to_postop_name = f"registration {psiName} to postop"
    psi_to_postop_node = _find_node_or_none(psi_to_postop_name)
    if psi_to_postop_node is None:
        print(f"WARNUNG: '{psi_to_postop_name}' nicht gefunden – "
              "Rotationswerte können nicht korrigiert werden.", file=sys.stderr)
    else:
        T_old = _get_matrix(psi_to_postop_node)
        T_new = T_old @ np.linalg.inv(T_planToPreop)
        _set_matrix(psi_to_postop_node, T_new)
        print(f"'{psi_to_postop_name}' korrigiert (T_new = T_old × inv(T_p2pr)).")

    # ── Schritt 5: Abstände neu berechnen ────────────────────────────────────
    logic.calculatePSIModelToModelDistance()
    print("calculatePSIModelToModelDistance abgeschlossen.")

    # ── Schritt 6: Ergebnisse ausgeben und CSV speichern ─────────────────────
    results = logic.printPSIResults()
    print("printPSIResults abgeschlossen. Ergebnisse:")
    for k, v in results.items():
        print(f"  {k[:35]:<35}: {float(v):.4f}")

    # ── Schritt 7: Geänderte Nodes und Szene speichern ───────────────────────
    print("Speichere geänderte Nodes:")

    # Umbenannten Transform unter neuem Dateinamen schreiben
    _write_node(plan_to_preop_node, case_dir,
                os.path.join(case_dir, CORRECT_TRANSFORM_NAME + ".h5"))

    # Korrigierten postop-Transform schreiben
    _write_node(psi_to_postop_node, case_dir)

    # PSI-Modell (neue Geometrie nach HardenTransform)
    _write_node(psiPlannedModel, case_dir)

    # Distance-Modell (neu berechnet, " Copy"-Pfad vermeiden)
    _write_node(pn.psiDistanceModel, case_dir,
                os.path.join(case_dir, f"{psiName} distance model planned postop.vtk"))

    # Nodes aus printPSIResults: Segmentierungen und OBBs
    # Namen: "{psiName} segmentation", "{postopName} segmentation",
    #        "{psiName} OBB",          "{postopName} OBB"
    postop_name = psiPostopModel.GetName()
    for node_name in [
        f"{psiName} segmentation",
        f"{postop_name} segmentation",
        f"{psiName} OBB",
        f"{postop_name} OBB",
    ]:
        _write_node(_find_node_or_none(node_name), case_dir)

    # MRML-Szenendatei aktualisieren
    slicer.util.saveScene(mrml_path)
    print(f"Szene gespeichert: {mrml_path}")

    prefix = psiName.replace(" ", "_")
    try:
        storage_path = psiPlannedModel.GetStorageNode().GetFileName()
        return os.path.join(os.path.dirname(storage_path), f"output_{prefix}.csv")
    except Exception as e:
        print(f"WARNUNG: CSV-Pfad konnte nicht ermittelt werden: {e}", file=sys.stderr)
        return None


def collect_all_results(root_dir, output_dir):
    """
    Alle output_*.csv aus direkten Unterordnern von root_dir einsammeln.
    Erste Spalte: case_id (= Name des Ordners, in dem die CSV liegt).
    """
    all_rows = []
    for entry in sorted(os.scandir(root_dir), key=lambda e: e.name):
        if not entry.is_dir():
            continue
        case_id = entry.name
        for csv_path in sorted(glob.glob(os.path.join(entry.path, "output_*.csv"))):
            with open(csv_path, 'r', newline='') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    normalized = {_strip_psi_prefix(k): v for k, v in row.items()}
                    all_rows.append({"case_id": case_id, **normalized})

    if not all_rows:
        print("Keine output_*.csv-Dateien im Root-Verzeichnis gefunden.", file=sys.stderr)
        return

    # Union aller Spaltennamen (Reihenfolge des ersten Auftretens beibehalten)
    seen_keys: dict = {}
    for row in all_rows:
        for key in row:
            seen_keys.setdefault(key, None)
    fieldnames = list(seen_keys.keys())

    tag = _DRY if DRY_RUN else ""
    os.makedirs(output_dir, exist_ok=True)
    combined_path = os.path.join(output_dir, "combined_output_corrected.csv")

    if DRY_RUN:
        print("\n".join([
            f"{tag}Würde {len(all_rows)} Zeile(n) aus {root_dir} nach {combined_path} schreiben.",
            f"{tag}Spalten: {fieldnames[:4]} ...",
        ]))
        return

    with open(combined_path, 'w', newline='') as f:
        # restval='' füllt fehlende Felder wenn Fälle unterschiedliche PSI-Namen haben
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval='')
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nGesamtergebnis gespeichert: {combined_path}")
    print(f"  {len(all_rows)} Zeile(n) insgesamt.")


# ── Argumente auswerten ──────────────────────────────────────────────────────
# Aufruf mit Einzelfall:
#   Slicer --no-main-window --python-script correct_plan_to_preop_cases.py -- /path/to/case/077
#   Slicer --no-main-window --python-script correct_plan_to_preop_cases.py -- --dry-run /path/to/case/077
# Aufruf im Batch-Modus:
#   Slicer --no-main-window --python-script correct_plan_to_preop_cases.py
#   Slicer --no-main-window --python-script correct_plan_to_preop_cases.py -- --dry-run
# In der Slicer Python Console (exec): kein Argument → Verhalten aus DRY_RUN-Variable.

_script_args = sys.argv[1:]
if "--dry-run" in _script_args:
    DRY_RUN = True
    _script_args = [a for a in _script_args if a != "--dry-run"]

_extra_args   = [a for a in _script_args if not a.startswith("-")]
single_case_dir = _extra_args[0] if _extra_args else None


# ── Logging: stdout/stderr auf Konsole UND Logfile spiegeln ─────────────────

class _Tee:
    """Schreibt gleichzeitig in den originalen Stream und eine Logdatei."""
    def __init__(self, original, logfile):
        self._orig = original
        self._log  = logfile

    def write(self, data):
        self._orig.write(data)
        self._log.write(data)

    def flush(self):
        self._orig.flush()
        self._log.flush()

    # Attribute des Original-Streams (z.B. encoding) durchreichen
    def __getattr__(self, name):
        return getattr(self._orig, name)


os.makedirs(COMBINED_OUTPUT_DIR, exist_ok=True)
_log_path = os.path.join(COMBINED_OUTPUT_DIR, "correction_log.txt")
_log_file = open(_log_path, "w", encoding="utf-8")

_orig_stdout = sys.stdout
_orig_stderr = sys.stderr
sys.stdout = _Tee(_orig_stdout, _log_file)
sys.stderr = _Tee(_orig_stderr, _log_file)


# ── Hauptablauf ──────────────────────────────────────────────────────────────

try:
    if DRY_RUN:
        print("*** DRY RUN – es werden keine Änderungen vorgenommen ***\n")

    if single_case_dir:
        # Einzelfall-Modus
        print(f"Einzelfall-Modus: {single_case_dir}")
        mrml_files = glob.glob(os.path.join(single_case_dir, "*.mrml"))
        if not mrml_files:
            print(f"FEHLER: Keine .mrml-Datei in '{single_case_dir}'.", file=sys.stderr)
        else:
            if len(mrml_files) > 1:
                print(f"WARNUNG: Mehrere .mrml-Dateien – nehme erste.", file=sys.stderr)
            if not os.path.exists(os.path.join(single_case_dir, OLD_TRANSFORM_FILENAME)):
                print(f"INFO: '{OLD_TRANSFORM_FILENAME}' nicht gefunden – Fall benötigt keine Korrektur.")
            else:
                try:
                    correct_case(single_case_dir, mrml_files[0])
                except Exception:
                    import traceback
                    print(f"\nFEHLER bei Fall '{single_case_dir}':", file=sys.stderr)
                    traceback.print_exc()
    else:
        # Batch-Modus: alle korrekturbedürftigen Fälle in CASES_ROOT_DIR
        cases = find_cases_to_correct(CASES_ROOT_DIR)
        for case_dir, mrml_path in cases:
            try:
                correct_case(case_dir, mrml_path)
            except Exception:
                import traceback
                print(f"\nFEHLER bei Fall '{case_dir}':", file=sys.stderr)
                traceback.print_exc()

    collect_all_results(CASES_ROOT_DIR, COMBINED_OUTPUT_DIR)
    print(f"\nLogfile gespeichert: {_log_path}")
    print("\nFertig.")

finally:
    sys.stdout = _orig_stdout
    sys.stderr = _orig_stderr
    _log_file.close()
