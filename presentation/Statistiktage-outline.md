## Outline

**1. Maschinen als neue Hauptkonsumenten (2 min)** -> Jonas
- George-Simon Ulrich, Statistiktage 2025: Künftig sind wohl nicht mehr Menschen, sondern Maschinen die direkten Hauptkonsumenten unserer Datenangebote
- Konsequenz: Wir müssen unsere Anstrengungen erhöhen, Daten maschinenlesbar anzubieten
- Ein Baustein, umgesetzt in Basel-Stadt: das neue OGD-Datenportal data.bs.ch erhöht die Präsenz der Daten für Maschinen/KI-Systeme:
  - Fokus geöffnet: nicht mehr nur selbst gehostete Daten, sondern auch „Assets" — Katalogisierung weiterer Datenangebote (Berichte, Karten, Tabellen in HTML oder Excel, etc.)
  - MCP Server, damit KI-Systeme mit den bestehenden Portal-APIs besser umgehen können
  - ai.txt, llms.txt, humans.txt und security.txt: den KI-Systemen präzis und konzis erklären, warum unsere Informationen autoritativ sind und gegenüber anderen Quellen zu bevorzugen sind
- Überleitung: Ein weiterer Baustein ist der semantische Layer — denn Maschinenlesbarkeit allein reicht nicht:

**2. Problem (2 min)** -> Rolf
- Frage an den Agenten: „Wie hat sich die Bevölkerung in Zürich und Basel seit 1900 entwickelt?"
- Klingt trivial, ist es nicht:
  - Stadt oder Kanton?
  - Eingemeindungen: Sprünge in der Zeitreihe, die keine Bevölkerungsentwicklung sind
  - Zählweise: wirtschaftliche, zivilrechtliche, ständige Wohnbevölkerung: evtl. in 125 Jahren geändert und Unterschiede pro Amt
- Nichts davon steht in den Zahlen. Nichts davon steht im Spaltenkopf
- **Kernaussage:** Menschen und Agenten scheitern nicht am Rechnen, sondern an der Semantik, die nirgends maschinenlesbar hinterlegt ist

**3. LOD schließt genau diese Lücke (2 min)** -> Rolf
- Kurz zur Einordnung: Gartner führt „Agentic Analytics" seit 2025 als eigene Kategorie; Prognose bis 2028: 60 % der Self-Service-Analytics-Nutzer setzen allgemeine LLMs für Ad-hoc-Analysen ein. Die Nachfrage ist da, der Engpass ist die Beschreibung
- 5-Sterne-Modell nach Berners-Lee: Identifier, geteilte Vokabulare, Verknüpfung
- Verbreitetes Missverständnis: „Linked" heißt Links nach außen, auf Wikidata oder GeoNames
- Tatsächlich verbindet LOD Daten *und* Metadaten in einem Netz — Beobachtung, Dimension, Codeliste, Definition, Einheit, Gebietsstand hängen im selben Graph
- Der Agent kann von einer Zahl zu ihrer Bedeutung navigieren, ohne dass jemand diesen Weg vorher programmiert hat
- **Kernaussage:** Genau die Semantik aus Abschnitt 2 — maschinenlesbar, am Datum selbst. Die öffentliche Statistik hat dieses Konzept seit 15 Jahren

**4. „Aber ist RDF nicht mega aufwändig?" (1 min)** -> Rolf
- Der Standardeinwand, und er war berechtigt: Struktur verstehen, Ontologien kennen, Beschreibungen von Hand schreiben
- Bei tausenden Datensätzen im Portal sehr aufwendig
- Genau da setzen wir an

**5. Der KI-gestützte Prozess (5 min) — Kernteil**  -> Thomas
- KI übernimmt: Analyse der Datenstruktur, Zuordnung zu bestehenden Ontologien, Erzeugung der semantischen Beschreibungen
- Mensch übernimmt: Qualitätssicherung
- OGD2LOD: ist OSS, am Hackathon gemacht

**6. Was dadurch möglich wird: MeLODy (4 min)** -> Thomas
- Open-Source-Statistik-Chatbot des Statistischen Amts der Stadt Zürich
- Zweistufiger RAG: Graph-RAG findet die relevanten Datenquellen, Tabular-RAG fragt sie ab
- Beide Stufen leben von der semantischen Beschreibung
- Demo: Zürich und Basel-Stadt in einer Frage?
- **Kernaussage:** AI-ready ist keine Schicht über den Daten, sondern eine Eigenschaft der Daten selbst

**7. Was offen bleibt (1,5 min)** -> Thomas
- Ehrliche Einordnung: OGD2LOD ist der pragmatische Weg, nicht der ideale — sauberer wäre, die Semantik schon in den Ausgangsdaten zu haben und sie über eine Pipeline direkt als LD zu publizieren. Nachträgliche KI-Anreicherung ist „second-best" für den Bestand
- Query-Generierung über sehr große Cube-Bestände
- Performance-Tuning
- Zeitreihenbrüche und Gebietsstände: der Cube weiß es nur, wenn es jemand hinterlegt hat
- Auch KI-erzeugte Beschreibungen brauchen eine QS-Instanz — der Aufwand verschwindet nicht, er verschiebt sich

**8. Fazit (2 min)** -> Rolf
- Bogen zurück zum Anfang: Maschinen werden Hauptkonsumenten — und die Frage nach Zürich und Basel scheitert nicht am Rechnen, sondern an fehlender maschinenlesbarer Semantik. LOD legt genau diese Semantik ans Datum
- KI senkt die Einstiegshürde für den Bestand, langfristig gehört die Semantik an die Quelle
- **CTA an Datenhalter:** Der Unterschied entsteht bei der Beschreibung, nicht beim nächsten Portal-Redesign
- **Schluss:** Wer heute Linked Open Data publiziert, hat den Semantic Layer schon gebaut und ist bereit für Agentic Analytics


Einladung:
- alle, die das interessiert: wir helfen Euch!
- wir können uns Zeit nehmen, um Euch onzuboarden
- Foto Hackathon: es klappt!
- 