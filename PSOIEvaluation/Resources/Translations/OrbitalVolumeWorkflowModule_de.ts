<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="de_DE" sourcelanguage="en_US">
<context>
    <name>OrbitalVolumeWorkflowModule</name>

    <!-- ── .ui strings ─────────────────────────────────────────────── -->

    <message>
        <source>Parameter set:</source>
        <translation>Parametersatz:</translation>
    </message>
    <message>
        <source>Selects the parameter set for the current CT case. A new parameter set can be created with the &quot;+&quot; button.</source>
        <translation>Wählt den Parametersatz für den aktuellen CT-Fall. Ein neuer Parametersatz kann mit dem „+" angelegt werden.</translation>
    </message>
    <message>
        <source>&lt;h3&gt;Orbital Volume Workflow&lt;/h3&gt;&lt;p&gt;Step 1 creates an entry plane (mesh) from a closed curve along the orbital rim. Step 2 segments the intraorbital volume from the CT using Fast Marching.&lt;/p&gt;</source>
        <translation>&lt;h3&gt;Orbita-Volumen Workflow&lt;/h3&gt;&lt;p&gt;Schritt 1 erstellt eine Eingangsebene (Mesh) aus einer geschlossenen Kurve entlang des Orbitarandes. Schritt 2 segmentiert daraus das intraorbitale Volumen im CT mittels Fast Marching.&lt;/p&gt;</translation>
    </message>
    <message>
        <source>CT Volume (shared):</source>
        <translation>CT-Volumen (gemeinsam):</translation>
    </message>
    <message>
        <source>CT dataset (HU-calibrated), applies to both left and right orbit</source>
        <translation>CT-Datensatz (HU-kalibriert), gilt für linke und rechte Orbita</translation>
    </message>
    <message>
        <source>3D on</source>
        <translation>3D ein</translation>
    </message>
    <message>
        <source>3D off</source>
        <translation>3D aus</translation>
    </message>
    <message>
        <source>Show or hide volume rendering</source>
        <translation>Volume-Rendering ein- oder ausblenden</translation>
    </message>
    <message>
        <source>HU Shift:</source>
        <translation>HU-Verschiebung:</translation>
    </message>
    <message>
        <source>Shifts the transfer function of the volume rendering in HU units</source>
        <translation>Verschiebt die Übertragungsfunktion des Volume-Renderings in HU</translation>
    </message>
    <message>
        <source>Positive = brighter bones, Negative = emphasize soft tissue</source>
        <translation>Positiv = Knochen heller darstellen, Negativ = Weichgewebe hervorheben</translation>
    </message>
    <message>
        <source>Left Orbit</source>
        <translation>Linke Orbita</translation>
    </message>
    <message>
        <source>Right Orbit</source>
        <translation>Rechte Orbita</translation>
    </message>
    <message>
        <source>1. Create Orbital Entry Plane</source>
        <translation>1. Orbital-Eingangsebene erstellen</translation>
    </message>
    <message>
        <source>&lt;p&gt;Select a &lt;b&gt;Closed Curve&lt;/b&gt; along the orbital rim. The module triangulates the surface and creates a &lt;i&gt;ModelNode&lt;/i&gt; as the entry plane, which is automatically passed to step 2.&lt;/p&gt;</source>
        <translation>&lt;p&gt;Wählen Sie eine &lt;b&gt;Closed Curve&lt;/b&gt; entlang des Orbitarandes. Das Modul trianguliert die Fläche und legt einen &lt;i&gt;ModelNode&lt;/i&gt; als Eingangsebene an, der automatisch für Schritt 2 übernommen wird.&lt;/p&gt;</translation>
    </message>
    <message>
        <source>Closed Curve:</source>
        <translation>Geschlossene Kurve:</translation>
    </message>
    <message>
        <source>vtkMRMLMarkupsClosedCurveNode along the orbital rim</source>
        <translation>vtkMRMLMarkupsClosedCurveNode entlang des Orbitarandes</translation>
    </message>
    <message>
        <source>New Curve</source>
        <translation>Neue Kurve</translation>
    </message>
    <message>
        <source>Creates a new Closed Curve for the active side and starts placement mode</source>
        <translation>Erstellt eine neue Closed Curve für die aktive Seite und startet den Zeichen-Modus</translation>
    </message>
    <message>
        <source>Triangulation Method:</source>
        <translation>Triangulierungs-Methode:</translation>
    </message>
    <message>
        <source>auto: selects automatically based on concavity index; fan: fast for convex curves; delaunay: robust for concave curves</source>
        <translation>auto: wählt automatisch anhand des Konkavitäts-Index; fan: schnell für konvexe Kurven; delaunay: robust für konkave Kurven</translation>
    </message>
    <message>
        <source>Point Spacing (mm):</source>
        <translation>Punkt-Abstand (mm):</translation>
    </message>
    <message>
        <source>Distance between sampling points along the curve (mm). Smaller values → more points → more accurate surface</source>
        <translation>Abstand zwischen Abtastpunkten entlang der Kurve (mm). Kleinere Werte → mehr Punkte → genauere Fläche</translation>
    </message>
    <message>
        <source>Smoothing Iterations:</source>
        <translation>Glättungs-Iterationen:</translation>
    </message>
    <message>
        <source>Number of Windowed-Sinc smoothing iterations (0 = off)</source>
        <translation>Anzahl der Windowed-Sinc-Glättungs-Iterationen (0 = aus)</translation>
    </message>
    <message>
        <source>Create Orbital Plane</source>
        <translation>Orbital-Ebene erstellen</translation>
    </message>
    <message>
        <source>2. Segment Intraorbital Volume</source>
        <translation>2. Intraorbitales Volumen segmentieren</translation>
    </message>
    <message>
        <source>&lt;p&gt;Segments the intraorbital soft tissue using &lt;b&gt;Fast Marching&lt;/b&gt;. The algorithm propagates from an automatically chosen seed point and is bounded by the orbital cylinder (depth + radius).&lt;/p&gt;</source>
        <translation>&lt;p&gt;Segmentiert das intraorbitale Weichgewebe mittels &lt;b&gt;Fast Marching&lt;/b&gt;. Der Algorithmus breitet sich von einem automatisch gewählten Seed-Punkt aus und wird durch den Orbital-Zylinder (Tiefe + Radius) begrenzt.&lt;/p&gt;</translation>
    </message>
    <message>
        <source>Orbital Plane (Mesh):</source>
        <translation>Orbital-Ebene (Mesh):</translation>
    </message>
    <message>
        <source>OrbitalPlane mesh from step 1</source>
        <translation>OrbitalPlane-Mesh aus Schritt 1</translation>
    </message>
    <message>
        <source>&lt;b&gt;HU Window for Seed Search:&lt;/b&gt;</source>
        <translation>&lt;b&gt;HU-Fenster für Seed-Suche:&lt;/b&gt;</translation>
    </message>
    <message>
        <source>HU Minimum:</source>
        <translation>HU Minimum:</translation>
    </message>
    <message>
        <source>Lower HU threshold for automatic seed search (fat/soft tissue)</source>
        <translation>Untere HU-Grenze für die automatische Seed-Suche (Fett/Weichgewebe)</translation>
    </message>
    <message>
        <source>HU Maximum:</source>
        <translation>HU Maximum:</translation>
    </message>
    <message>
        <source>Upper HU threshold for automatic seed search (bone excluded above ~300)</source>
        <translation>Obere HU-Grenze für die automatische Seed-Suche (Knochen ab ~300 ausgeschlossen)</translation>
    </message>
    <message>
        <source>&lt;b&gt;Seed Point:&lt;/b&gt;</source>
        <translation>&lt;b&gt;Seed-Punkt:&lt;/b&gt;</translation>
    </message>
    <message>
        <source>Automatic Seed:</source>
        <translation>Automatischer Seed:</translation>
    </message>
    <message>
        <source>Automatically search for seed point within the HU window (recommended)</source>
        <translation>Seed-Punkt automatisch im HU-Fenster suchen (empfohlen)</translation>
    </message>
    <message>
        <source>Seed Offset (mm):</source>
        <translation>Seed-Offset (mm):</translation>
    </message>
    <message>
        <source>Manual distance of the seed point from the plane centroid in normal direction (only when auto seed is disabled)</source>
        <translation>Manueller Abstand des Seed-Punkts vom Ebenen-Centroid in Normalrichtung (nur bei deaktiviertem Auto-Seed)</translation>
    </message>
    <message>
        <source>&lt;b&gt;Orbital Cylinder:&lt;/b&gt;</source>
        <translation>&lt;b&gt;Orbital-Zylinder:&lt;/b&gt;</translation>
    </message>
    <message>
        <source>Orbital Depth (mm):</source>
        <translation>Orbitatiefe (mm):</translation>
    </message>
    <message>
        <source>Maximum depth of the orbital cylinder from the entry rim to the apex (mm)</source>
        <translation>Maximale Tiefe des Orbitazylinders vom Eingangsrand bis zum Apex (mm)</translation>
    </message>
    <message>
        <source>Radius Margin (mm):</source>
        <translation>Radius-Puffer (mm):</translation>
    </message>
    <message>
        <source>Margin beyond the estimated orbital rim radius (mm)</source>
        <translation>Puffer über den geschätzten Orbitarand-Radius hinaus (mm)</translation>
    </message>
    <message>
        <source>&lt;b&gt;Fast Marching Parameters:&lt;/b&gt;</source>
        <translation>&lt;b&gt;Fast Marching Parameter:&lt;/b&gt;</translation>
    </message>
    <message>
        <source>Stopping Value (mm):</source>
        <translation>Stopping-Wert (mm):</translation>
    </message>
    <message>
        <source>Stopping time of the Fast Marching algorithm in mm. Larger values → more volume. Rule of thumb: ~0.5 × orbital depth</source>
        <translation>Abbruchzeit des Fast-Marching-Algorithmus in mm. Größere Werte → mehr Volumen. Faustformel: ~0.5 × Orbitatiefe</translation>
    </message>
    <message>
        <source>Speed Sigma (HU):</source>
        <translation>Speed Sigma (HU):</translation>
    </message>
    <message>
        <source>Width of the Gaussian speed function in HU units. Smaller values → sharper tissue boundaries</source>
        <translation>Breite der Gauß-Speed-Funktion in HU-Einheiten. Kleinere Werte → schärfere Gewebegrenzen</translation>
    </message>
    <message>
        <source>Posterior Boost:</source>
        <translation>Posteriorer Boost:</translation>
    </message>
    <message>
        <source>Additional weighting of deep voxels (0 = off, &gt;0 = stronger posterior). Prevents anterior leakage</source>
        <translation>Zusätzliche Gewichtung tiefer Voxel (0 = aus, &gt;0 = stärker posterior). Verhindert anteriores Auslaufen</translation>
    </message>
    <message>
        <source>&lt;b&gt;Output:&lt;/b&gt;</source>
        <translation>&lt;b&gt;Ausgabe:&lt;/b&gt;</translation>
    </message>
    <message>
        <source>Show Seed Point:</source>
        <translation>Seed-Punkt anzeigen:</translation>
    </message>
    <message>
        <source>Display the seed point as a fiducial marker in the scene (for verification)</source>
        <translation>Seed-Punkt als Fiducial-Marker in der Szene anzeigen (zur Kontrolle)</translation>
    </message>
    <message>
        <source>Segment Volume</source>
        <translation>Volumen segmentieren</translation>
    </message>
    <message>
        <source>Edit in Segment Editor (Scissors)</source>
        <translation>Im Segment-Editor nachbearbeiten (Schere)</translation>
    </message>
    <message>
        <source>Opens the Segment Editor with the Scissors tool active for manual corrections. The volume is automatically recalculated after editing.</source>
        <translation>Öffnet den Segment-Editor mit aktiviertem Scissors-Werkzeug für manuelle Korrekturen. Das Volumen wird nach der Bearbeitung automatisch neu berechnet.</translation>
    </message>

    <!-- ── Python _() strings ───────────────────────────────────────── -->

    <message>
        <source>OrbitalVolumeWorkflowModule</source>
        <translation>OrbitalVolumeWorkflowModule</translation>
    </message>
    <message>
        <source>This module creates an entry plane for the orbit from a closed curve and subsequently segments the intraorbital volume using Fast Marching. The left and right orbits are managed separately.</source>
        <translation>Dieses Modul erstellt eine Eingangsebene für die Orbita aus einer geschlossenen Kurve und segmentiert anschließend das intraorbitale Volumen via Fast Marching. Linke und rechte Orbita werden getrennt verwaltet.</translation>
    </message>
    <message>
        <source>Developed by Johannes Schulze (Bundeswehrkrankenhaus Ulm) without external funding.</source>
        <translation>Entwickelt von Johannes Schulze (Bundeswehrkrankenhaus Ulm) ohne externe Förderung.</translation>
    </message>
    <message>
        <source>Error creating the orbital plane.</source>
        <translation>Fehler beim Erstellen der Orbital-Ebene.</translation>
    </message>
    <message>
        <source>Please select a closed curve (Closed Curve).</source>
        <translation>Bitte eine geschlossene Kurve (Closed Curve) auswählen.</translation>
    </message>
    <message>
        <source>&lt;b&gt;Created:&lt;/b&gt; {name}&lt;br&gt;Triangles: {tris} &amp;nbsp;|&amp;nbsp; Points: {pts}</source>
        <translation>&lt;b&gt;Erstellt:&lt;/b&gt; {name}&lt;br&gt;Dreiecke: {tris} &amp;nbsp;|&amp;nbsp; Punkte: {pts}</translation>
    </message>
    <message>
        <source>Error during volume segmentation.</source>
        <translation>Fehler bei der Volumensegmentierung.</translation>
    </message>
    <message>
        <source>Please select a CT volume.</source>
        <translation>Bitte ein CT-Volumen auswählen.</translation>
    </message>
    <message>
        <source>Please select an orbital plane (mesh).</source>
        <translation>Bitte eine Orbital-Ebene (Mesh) auswählen.</translation>
    </message>
    <message>
        <source>&lt;b&gt;Intraorbital volume: {vol:.2f} ml&lt;/b&gt;&lt;br&gt;Voxels: {vox}&lt;br&gt;Seed offset: {off:.1f} mm &amp;nbsp;|&amp;nbsp; HU at seed: {hu:.0f}</source>
        <translation>&lt;b&gt;Intraorbitales Volumen: {vol:.2f} ml&lt;/b&gt;&lt;br&gt;Voxel: {vox}&lt;br&gt;Seed-Offset: {off:.1f} mm &amp;nbsp;|&amp;nbsp; HU am Seed: {hu:.0f}</translation>
    </message>
    <message>
        <source>&lt;b&gt;Intraorbital volume: {vol:.2f} ml&lt;/b&gt; &amp;nbsp;&lt;i&gt;(after manual editing)&lt;/i&gt;&lt;br&gt;Voxels: {vox}</source>
        <translation>&lt;b&gt;Intraorbitales Volumen: {vol:.2f} ml&lt;/b&gt; &amp;nbsp;&lt;i&gt;(nach Nachbearbeitung)&lt;/i&gt;&lt;br&gt;Voxel: {vox}</translation>
    </message>
    <message>
        <source>No valid seed point found within the HU window [{hu_min}, {hu_max}] within {max_mm} mm. Adjust the HU window or seed offset.</source>
        <translation>Kein plausibler Seed-Punkt im HU-Fenster [{hu_min}, {hu_max}] innerhalb von {max_mm} mm gefunden. HU-Fenster oder Seed-Offset anpassen.</translation>
    </message>
    <message>
        <source>Segment has no binary labelmap representation.</source>
        <translation>Segment hat keine Binary-Labelmap-Repräsentation.</translation>
    </message>
    <message>
        <source>Unknown method: {method}</source>
        <translation>Unbekannte Methode: {method}</translation>
    </message>

</context>
</TS>
