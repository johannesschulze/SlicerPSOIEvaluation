---
title: PSI Analysis Workflow
author: Johannes Schulze
date: 2026-06-16
lang: de
---

**Modul:** GeneralPSOIWorkflowModule  
**Zweck:** Evaluierung der dreidimensionalen Position patientenspezifischer Implantate (PSI) im Vergleich zur präoperativen Planung  
**Zielgruppe:** Klinisches Personal und Forscher im Bereich der PSI-Chirurgie

---

# Voraussetzungen

- 3D Slicer (Version 5.0 oder neuer)
- Die Erweiterung **SlicerPSOIEvaluation** muss über den Extension Manager oder manuell geladen sein
- Weitere benötigte Erweiterungen:
  - **ModelToModelDistance**
  - **SlicerIGT**
  - **SlicerRT**
  - **ModelRegistration** (im Extension Manager als „SlicerModelRegistration" gelistet)
- CT-Datensätze in HU-kalibrierten Einheiten (DICOM oder NIfTI): präoperatives CT + postoperatives CT bzw. C-Bogen-Aufnahme
- STL-Datei des geplanten PSI (obligatorisch)
- STL-Datei des geplanten Schädels / Mittelgesichts (optional; erforderlich wenn das PSI-STL in einem eigenen Koordinatensystem geliefert wird, das nicht mit dem des Planungs-CTs übereinstimmt)

---

# Überblick

Das Modul führt den Anwender in sechs aufeinanderfolgenden Schritten (Tabs) durch die vollständige Auswertung:

| Schritt | Aufgabe |
|---------|---------|
| **1. Prepare the Scene** | Volumen- und Modellauswahl, Szenenvorbereitung, grobe Vorausrichtung |
| **2. Register preop and postop CTs** | Manuelle Feinjustierung und automatische Intensitäts-basierte CT-Registrierung (BRAINS) |
| **2.a (Optional) Register plan to preop CT** | Registrierung der Planungs-STLs ins präoperative CT-Koordinatensystem |
| **3. Segment intraop PSI** | Segmentierung des PSI aus dem postoperativen CT |
| **4. Align PSIs** | Registrierung des Planungs-STL auf die postoperative PSI-Position |
| **5. Calculate Comparison** | Punkt-zu-Punkt-Abstandsanalyse |
| **6. Output Results** | Berechnung und Export aller Kenngrößen als CSV-Datei |

Die Schritte 2.a und Teile von Schritt 3 sind situationsabhängig optional.

---

# Schritt 0: Daten laden und Namensgebung

## Daten in 3D Slicer importieren

**STL-Dateien:** Per Drag & Drop in das Slicer-Fenster ziehen.

**DICOM-Datensätze:** Per Drag & Drop in Slicer ziehen und im anschließenden Dialog *Load directory into DICOM database* bestätigen. Anschließend die DICOM-Datenbank öffnen und den gewünschten Fall durch Doppelklick laden. Etwaige Warnungen können bestätigt werden.

## Namenskonvention

Für Übersichtlichkeit sollten die importierten Datensätze einheitlich benannt werden (Doppelklick auf den Namen in der Datenliste oder F2):

| Datensatz | Empfohlener Name |
|-----------|-----------------|
| STL-Datei des geplanten PSI | `PSI planned` |
| STL-Datei des Schädels / Mittelgesichts (falls vorhanden) | `Skull planned` |
| Präoperativer CT-Datensatz | `preop volume` |
| Postoperativer CT / C-Bogen-Datensatz | `postop volume` |

> **Hinweis:** Das Modul vergibt die Namen für Prä- und Postop-Volumen beim Klick auf *Prepare Scene* automatisch. Die Umbenennung ist dennoch empfehlenswert, um im Auswahlschritt die richtigen Datensätze leicht identifizieren zu können.

Das Modul öffnen: Entweder über STRG+F (*Module Finder*) nach „General PSOI" suchen oder im Modulmenü unter *PSOI Evaluation → General PSOI Analysis Workflow* navigieren.

![](Screenshots/00_startup_and_volume_selection.png)

---

# Schritt 1: Vorbereitung der Szene

## Eingaben auswählen

| Feld | Inhalt |
|------|--------|
| **Preop volume** | Präoperativer CT-Datensatz |
| **Postop volume** | Postoperativer CT / C-Bogen-Datensatz |
| **Skull Planned** | STL des geplanten Schädels (nur nötig wenn Planungs-STLs im Koordinatensystem des Planungs-CTs registriert werden müssen, z.B. bei ReOss) |

![](Screenshots/01_prepare_scene.png)

## Optional: Volumes zuschneiden

Wenn die CT-Datensätze größere Körperregionen umfassen als nötig, können sie vorab zugeschnitten werden, um Rechenzeit zu sparen:

1. **Crop volume** klicken → ein verstellbares ROI-Quader erscheint in der Szene.
2. Quader durch Ziehen der Handles an die gewünschten Grenzen anpassen.
3. **Apply crop** klicken → der Datensatz wird dauerhaft auf die gewählte Region beschnitten.

Für Präop- und Postop-Volumen stehen jeweils eigene Crop-Schaltflächen zur Verfügung.

## Optional: Midsagittal-Ausrichtung

Der Button **1a. Align to Midline (PCA)** berechnet anhand der kortikalen Knochenverteilung des präoperativen CT automatisch einen Yaw-Winkel, der die Midsagittalebene mit x = 0 ausrichtet. Der resultierende Transform wird als interaktiv bearbeitbare Transformation dargestellt, sodass das Ergebnis manuell feinkorrigiert werden kann.

> **Hinweis:** Dieser Schritt ist optional und vor allem dann hilfreich, wenn die Lagerung des Patienten stark geneigt ist.

## Szene vorbereiten

Klick auf **1. Prepare Scene** führt folgende Schritte aus:

- Umbenennung der Volumina zu `preop volume` / `postop volume`
- Anwendung der Midline-Transformation auf das präoperative CT (falls vorhanden, wird diese eingebrannt)
- Grobe Vorausrichtung des postoperativen Volumens auf das präoperative CT (Schwerpunkt-Ausrichtung)
- Aktivierung einer interaktiven Transformationsanzeige für die manuelle Feinjustierung

---

# Schritt 2: Registrierung der CT-Scans

## Manuelle Vorausrichtung

Im Rahmen der Szenenvorbereitung (Schritt 1) wurden die beiden Volumina bereits grob überlagert. In diesem Schritt erfolgt eine manuelle Feinjustierung, um die automatische Registrierung zu erleichtern.

In den axialen, koronaren und sagittalen Schichtansichten wird das postoperative Volumen mit 50 % Opazität über das präoperative geblendet. Mit den Transformations-Handles kann das postoperative Volumen verschoben und rotiert werden:

- **Grauer Kreis:** Rotation
- **Fadenkreuz in der Mitte:** Translation
- **ALT + Fadenkreuz verschieben:** Rotationszentrum versetzen

**Empfohlener Ausrichtungsworkflow:**

1. **Sagittale Ansicht (gelb):** Vertikale und sagittale Position einstellen. Orientierungspunkte: Stirnhöhle, Keilbeinhöhle, Sella turcica, Hartgaumen.
2. **Koronare Ansicht (grün):** Transversale Ausrichtung korrigieren. Geeigneter Orientierungspunkt: Canalis infraorbitalis der gesunden Seite (Rotationszentrum dort setzen, dann Rotation um die Sagittalachse korrigieren).
3. **Axiale Ansicht:** Jochbeinprominenz der gesunden Seite überlagern, Rotationszentrum setzen und Rotation feinanpassen. Nicht an der frakturierten Gegenseite orientieren.

> Anschließend in allen drei Ansichten durchscrollen um die Überlagerung zu überprüfen. Die Ausrichtung im Bereich von Mittelgesicht und Schädelbasis sollte möglichst genau sein; im Bereich der Halswirbelsäule sind Abweichungen durch unterschiedliche Lagerung möglich und unkritisch.

## Optional: Registrierungsmaske

Eine Segmentierungsmaske kann verwendet werden, um die Registrierung auf eine bestimmte anatomische Region einzugrenzen (z.B. nur die Orbita oder nur den Unterkiefer). Klick auf **2a. Registrierungsmaske zeichnen (optional)** öffnet den Segment Editor mit aktiviertem Scissors-Werkzeug. Das gezeichnete Segment wird als ROI für die BRAINS-Registrierung verwendet.

## Automatische Registrierung

Klick auf **2b. Register CT-Scans (BRAINS)** startet die rigide Intensitäts-basierte Registrierung. Je nach Rechenleistung dauert dieser Schritt 1–5 Minuten. Nach Abschluss wird der Normalized Cross-Correlation (NCC)-Wert der Registrierung in der Konsole ausgegeben.

> **Ergebnis prüfen:** Nach der Registrierung nochmals in allen drei Ansichten durchscrollen. Die Überlagerung im Bereich des Mittelgesichts und der Schädelbasis sollte nahezu perfekt sein.

---

# Schritt 2.a (Optional): Planungs-STL ins präoperative CT registrieren

Dieser Schritt ist **nur erforderlich**, wenn die STL-Dateien nicht im Koordinatensystem des präoperativen CT geliefert wurden (erkennbar daran, dass das Modell nach dem Import weit von den CT-Volumina entfernt liegt).

Die Registrierung erfolgt in drei Teilschritten:

## 1. Planungs-STLs vorausrichten

Klick auf **1. Recenter the planning STLs** zentriert das Schädel-STL grob auf das präoperative Volumen und aktiviert Transformations-Handles für die manuelle Feinjustierung. Ziel ist eine einigermaßen korrekte Ausgangsposition für die nachfolgende automatische Registrierung.

> Hier lohnt es sich, ein paar Minuten zu investieren, da die Qualität der manuellen Vorausrichtung die Registrierungsgenauigkeit direkt beeinflusst.

## 2. Präoperatives CT segmentieren

Klick auf **2. Segment the preop CT** erstellt eine Knochen-Segmentierung des präoperativen CT (Schwellenwert 200 HU, beschränkt auf den Bereich des Schädel-STL). Das Ergebnis wird als 3D-Modell in der Szene angezeigt.

> **Alternative:** Steht bereits ein Oberflächenmodell des Schädels aus einer anderen Quelle zur Verfügung (z.B. aus einer früheren Sitzung), kann dieses über das Dropdown-Feld *or select existing model* ausgewählt und der Segmentierungsschritt übersprungen werden. Anschließend **Register to selected model** klicken.

## 3. Planungs-STL registrieren

Klick auf **3. Register plan STLs to preop CT** führt eine ICP-Registrierung (Iterative Closest Point) des Schädel-STL auf das segmentierte präoperative Modell durch. Nach Abschluss wird der RMS-Wert (Root Mean Square) der Überlagerung in einem Dialogfenster angezeigt.

> **Qualitätskriterium:** Der RMS sollte < 0,5 mm liegen. In der 3D-Ansicht sollten STL (grün) und Segmentierung (grau) sich gleichmäßig abwechseln — ein einseitiger Farbwechsel deutet auf eine systematische Verschiebung hin.

> **Wichtig:** Etwaige Registrierungsfehler addieren sich zu den Fehlern der nachfolgenden Analyseschritte. Das Ergebnis dieses Schritts sorgfältig prüfen.

---

# Schritt 3: Segmentierung des intraoperativen PSI

## PSI-Modell auswählen

Im Feld **PSI Planned** das STL-Modell des geplanten PSI auswählen. Bei Fällen mit mehreren PSIs kann zu diesem Tab jederzeit zurückgekehrt und ein anderes Modell ausgewählt werden.

## Optional: Planungs-STL in das Präop-Koordinatensystem übertragen

Wenn Schritt 2.a durchgeführt wurde, muss das PSI-STL ebenfalls ins Präop-Koordinatensystem transformiert werden: Klick auf **3.a Align PSI to preop CT (optional)** übernimmt den in Schritt 2.a berechneten Transform auf das aktuell ausgewählte PSI-Modell.

Nach diesem Schritt sollte das PSI-STL in der 3D-Ansicht in der korrekten anatomischen Position sichtbar sein.

## PSI segmentieren

Klick auf **3.b Segment intraop PSI** führt folgende Schritte aus:

1. Das postoperative Volumen wird auf den Bereich des geplanten PSI zugeschnitten (Bounding Box des PSI-Modells mit 2 cm Puffer).
2. Das Programm wechselt in den *Segment Editor*.
3. Das Threshold-Werkzeug wird mit einem Startwert von 1750 HU vorbelegt und automatisch angewendet.

Im Segment Editor sind in der Regel zwei Nachbearbeitungen nötig:

1. **Schwellenwert anpassen:** Falls zu viel (z.B. Knochen mit eingeschlossen) oder zu wenig (PSI nicht vollständig erfasst) segmentiert wurde, den Mindestschwellenwert anpassen. Die Umrisse des PSI sollten klar abgegrenzt sein; die Binnenstruktur ist weniger kritisch.
2. **Überschüssige Strukturen entfernen:** Mit dem **Scissors**-Werkzeug (Scherensymbol) alle segmentierten Bereiche entfernen, die nicht zum PSI gehören (Schrauben, weitere Platten, Knochenfragmente).

![](Screenshots/03_segmentation.png)

Nach Abschluss zurück zum Modul **General PSOI Analysis Workflow** wechseln.

## Bestehende Segmentierung verwenden

Wurde das PSI bereits in einer früheren Sitzung segmentiert, kann die bestehende Segmentierung über das Dropdown-Feld *or use existing* ausgewählt und anschließend mit **Use selected** bestätigt werden.

---

# Schritt 4: PSIs überlagern

## Automatische Registrierung

Klick auf **4. Align PSIs** führt folgende Schritte aus:

1. Die ausgewählte Segmentierung wird in ein Oberflächenmodell konvertiert (nur das ausgewählte Segment wird exportiert).
2. Eine Kopie des Planungs-STL wird erstellt und mittels ICP-Algorithmus auf das postoperative Modell registriert.
3. Der Transform wird als interaktiv bearbeitbares Handle angezeigt.
4. Nach Abschluss wird der RMS-Wert in einem Dialogfenster ausgegeben.

> **Qualitätskriterium:** Der RMS sollte < 0,5 mm liegen. Falls die Ausrichtung nicht passt, kann über die eingeblendeten Transformations-Handles nachkorrigiert werden. Das PSI besitzt eine minimale Flexibilität — daher ist eine global gute Überlagerung anzustreben, auch wenn nicht alle Bereiche gleichermaßen perfekt passen.

![](Screenshots/04_align_psis.png)

## Manuelle Ausrichtung (ohne Segmentierung)

Als Alternative zur Segmentierung steht der Button **4b. Manual alignment (no segmentation)** zur Verfügung. Dabei wird:

- Eine Kopie des Planungs-STL in der Szene erstellt
- Ein zentrierter linearer Transform mit interaktiven Handles angelegt
- Keine automatische Registrierung durchgeführt

Diese Option eignet sich für Fälle, in denen das PSI im postoperativen CT nur schlecht sichtbar ist und eine manuelle Zuordnung erforderlich ist.

---

# Schritt 5: Abstandsanalyse

Klick auf **5. Calculate Comparison** führt eine Punkt-zu-Punkt-Abstandsanalyse zwischen dem Planungs-STL und der registrierten Postop-Position durch.

Das Ergebnis wird als farbkodiertes Abstandsmodell dargestellt, wobei die Abstände als vorzeichenbehaftete Werte (von Planung zu Postop) gespeichert werden. Die Farbskala reicht von kalt (geringer Abstand) bis warm (größerer Abstand).

![](Screenshots/05_distance_model.png)

---

# Schritt 6: Ergebnisausgabe

Klick auf **Output Results** berechnet alle verbleibenden Kenngrößen und schreibt die Ergebnisse in eine CSV-Datei im Verzeichnis der PSI-STL-Datei. Der Dateiname lautet `output_<Name des PSI-Modells>.csv`.

## Ausgabewerte

| Spaltenname | Bedeutung |
|-------------|-----------|
| `*_rms_plan_to_preop` | RMS-Abstand (mm) der Registrierung Planungs-STL → Präop-CT |
| `*_rms_plan_to_postop` | RMS-Abstand (mm) der Registrierung Planungs-STL → Postop-PSI |
| `*_dice_plan_intraop` | Dice-Koeffizient (Überlappungsmaß, 0–1) |
| `*_hausdorff_avg_planned_postop` | Durchschnittliche Hausdorff-Distanz (mm) |
| `*_hausdorff_max_planned_postop` | Maximale Hausdorff-Distanz (mm) |
| `*_rotation_x/y/z` | Rotationsfehler um x, y, z-Achse (Grad, Euler XYZ) |
| `*_distance` | Euklidischer Abstand zwischen den Bounding-Box-Zentren (mm) |
| `*_vector_x/y/z` | Verschiebungsvektor zwischen den Bounding-Box-Zentren (mm) |
| `*_m2m_rms` | RMS der vorzeichenbehafteten Punkt-zu-Punkt-Abstände (mm) |
| `registration_ncc` | Normalized Cross-Correlation der CT-Registrierung (−1 bis 1) |

> Die CSV-Datei kann direkt in MS Excel oder LibreOffice Calc importiert werden. Die Ergebniszeile sollte in die fallübergreifende Auswertungstabelle kopiert werden.

![](Screenshots/06_output_results.png)

---

# Tipps und Hinweise

- **Mehrere PSIs pro Fall:** Für jeden PSI kann zu Schritt 3 zurückgekehrt werden. Ein anderes Planungs-Modell auswählen und den Workflow ab dort wiederholen. Die Ergebnisse werden mit dem jeweiligen Modellnamen als Präfix gespeichert.
- **Koordinatensystem prüfen:** Liegt das importierte STL weit abseits der CT-Volumina, ist fast immer Schritt 2.a erforderlich.
- **Manuelle Feinjustierung nach Schritt 4:** Die Transformations-Handles nach *Align PSIs* können für manuelle Korrekturen genutzt werden. Wichtig: Die Korrektur muss abgeschlossen sein, bevor Schritt 5 (*Calculate Comparison*) ausgeführt wird, da dort der Transform eingebrannt wird.
- **Segmentierungs-Qualität:** Die Segmentierungsgenauigkeit in Schritt 3 hat einen erheblichen Einfluss auf das Registrierungsergebnis in Schritt 4. Lieber etwas Zeit in eine saubere Segmentierung investieren.
- **Registrierungsmaske (Schritt 2):** Besonders hilfreich, wenn präoperatives und postoperatives CT in verschiedenen Körperpositionen aufgenommen wurden (z.B. liegende vs. sitzende Position). Die Maske kann die Registrierung auf die relevante anatomische Region einschränken.
- **Szene speichern:** Nach abgeschlossener Auswertung die Slicer-Szene (.mrb) im Fallverzeichnis sichern.

---

# Fehlersuche

| Problem | Mögliche Ursache | Lösung |
|---------|-----------------|--------|
| PSI-STL liegt weit vom CT entfernt | STL in eigenem Koordinatensystem des Herstellers | Schritt 2.a durchführen |
| RMS nach Schritt 2.a > 0,5 mm | Manuelle Vorausrichtung unzureichend | „Recenter" erneut und Vorausrichtung verbessern, dann erneut registrieren |
| Threshold in Schritt 3 segmentiert zu viel Knochen | Untere HU-Grenze zu niedrig | Threshold-Untergrenze auf ≥ 1750 HU erhöhen oder manuell mit Scissors nachbearbeiten |
| Threshold erfasst PSI nicht vollständig | PSI aus niedrig-attenuierendem Material (Titan: ~2000 HU, PEEK: ~400 HU) | Schwellenwert entsprechend dem Material anpassen |
| Align PSIs konvergiert auf falsche Position | Planungs-STL noch nicht in Präop-Koordinatensystem überführt | Schritt 3.a (*Align PSI to preop CT*) vor Segmentierung ausführen |
| RMS nach Schritt 4 > 0,5 mm | PSI verformt oder Segmentierung ungenau | Ergebnis visuell prüfen; ggf. Segmentierung in Schritt 3 verbessern und wiederholen |
| Keine CSV-Ausgabe in Schritt 6 | PSI-STL-Datei hat keinen Dateipfad (z.B. direkt importiert ohne Speicherort) | PSI-Modell als STL-Datei auf der Festplatte speichern, bevor die Auswertung gestartet wird |
| Rotationswerte unerwartet groß | Transform aus Schritt 4 enthält noch eine Vortransformation | Sicherstellen dass der Transform vor Schritt 5 nicht manuell überschrieben wurde |
